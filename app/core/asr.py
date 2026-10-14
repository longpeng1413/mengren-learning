"""字幕识别。

支持三家服务：
- OpenAI（whisper-1，返回句子级时间戳）；
- 硅基流动（OpenAI 兼容接口，只返回文字，按停顿切成短段后按句长分配时间）；
- 豆包（火山引擎），只留用户实测后选的三种（DOUBAO_MODELS），可以填几个账号，按 DOUBAO_ORDER 自动换：
  * 流式识别 2.0（bigmodel_nostream）：WebSocket 直接发送本地音频；
  * 录音文件识别 1.0 极速版：一次请求直接上传音频（base64），单次 ≤2 小时 / 100MB；
  * 录音文件识别 2.0 标准版：提交 + 查询。官方文档只写了传公网链接，实测直接发 base64（audio.data）也可以，
    所以不需要对象存储（TOS）。
"""
import base64
import gzip
import json
import random
import re
import struct
import tempfile
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from . import media
from .log import log
from .config import CACHE_DIR, PROVIDER_NAMES

RETRIES = 3
TIMEOUT = 600

# 用户 2026-10-14 用同一节已校对的 18 分钟课（带 61 个术语热词）实测后定的：只留这三种，按 DOUBAO_ORDER 的顺序用。
# 流式 2.0 的「峰」「级」认错最少（192 个里错 10，另外两种错 30 多）；录音文件 2.0 标准版和 1.0 极速版错法几乎一样，
# 极速版快得多，所以排在标准版前面。并发版每个账号只送 1 小时、闲时版要等很久、1.0 流式和标准版不如 2.0，都去掉了。
# 流式 2.0 不加路数提速（用户要质量第一；实测 8 路能快 3 倍多，但没看出质量有没有差别，不冒险）。
DOUBAO_MODELS = {
    'stream2': {'name': '流式识别 2.0（小时版）', 'short': '流式识别 2.0', 'resource': 'volc.seedasr.sauc.duration', 'kind': 'stream',
                'note': '识别最准（交易术语认错最少）。18 分钟的课约 3 分钟。'},
    'flash':   {'name': '录音文件识别 1.0 · 极速版', 'short': '极速版 1.0', 'resource': 'volc.bigasr.auc_turbo', 'kind': 'flash',
                'note': '几秒钟就好，准确度和录音文件识别 2.0 差不多。'},
    'std2':    {'name': '录音文件识别 2.0 · 标准版', 'short': '录音文件识别 2.0', 'resource': 'volc.seedasr.auc', 'kind': 'task',
                'submit': '/auc/bigmodel/submit', 'query': '/auc/bigmodel/query',
                'note': '18 分钟的课约 1 分半。'},
}
DOUBAO_ORDER = ('stream2', 'flash', 'std2')

class AsrError(Exception):
    pass


class Cancelled(AsrError):
    pass


class QuotaError(AsrError):
    """额度用完或欠费：换下一个账号 / 模型。"""


class AuthError(AsrError):
    """密钥不对，或这个账号没开通这种模型：换下一个，全都不行才报错。"""


def proxies_for(settings, name):
    p = settings['providers'][name]
    if p.get('use_proxy') and settings.get('proxy'):
        return {'http': settings['proxy'], 'https': settings['proxy']}
    return None


def check_ready(settings, name):
    p = settings['providers'][name]
    label = PROVIDER_NAMES[name]
    if name == 'doubao':
        if not doubao_accounts(p):
            raise AsrError(f'还没有填写{label}的 API Key，请到「设置」里填写')
    elif not p.get('key'):
        raise AsrError(f'还没有填写{label}的 API Key，请到「设置」里填写')


# ---------------------------------------------------------------- 文字整理 ----
NOISE = re.compile(r'<\|[^|>]*\|>|[\U0001F300-\U0001FAFF☀-➿️]')
SENTENCE = re.compile(r'[^。！？!?；;…]+[。！？!?；;…]*|[。！？!?；;…]+')
LONG_CUE = 46


def tidy(text):
    return re.sub(r'\s+', ' ', NOISE.sub('', str(text or ''))).strip()


def split_long(piece):
    if len(piece) <= LONG_CUE:
        return [piece]
    out, cur = [], ''
    for part in re.findall(r'[^，,、]+[，,、]?', piece):
        if cur and len(cur) + len(part) > LONG_CUE:
            out.append(cur.strip())
            cur = part
        else:
            cur += part
    if cur.strip():
        out.append(cur.strip())
    return out


def sentences(text):
    pieces = [x for p in SENTENCE.findall(text) if p.strip() for x in split_long(p.strip())]
    out = []
    for piece in pieces:
        out += [x.strip() for x in re.split(r'(?<=[a-z0-9\)][.])\s+(?=[A-Z"“])', piece) if x.strip()]
    merged = []
    for piece in out:
        if merged and len(piece) < 6:
            sep = ' ' if re.search(r'[A-Za-z0-9.!?"”)]$', merged[-1]) and re.match(r'[A-Za-z0-9"“(]', piece) else ''
            merged[-1] += sep + piece
        else:
            merged.append(piece)
    return merged


def cues_from(text, segments, start, end):
    """一段音频的字幕：服务给了分句时间就用，否则按句子长度把时间分下去。"""
    if segments:
        cues = []
        for seg in segments:
            try:
                a, b, t = float(seg['start']), float(seg['end']), tidy(seg.get('text'))
            except (KeyError, TypeError, ValueError):
                continue
            if t:
                cues.append({'start': round(start + a, 2), 'end': round(start + max(a, b), 2), 'text': t})
        if cues:
            return cues
    parts = sentences(text) if text else []
    if not parts:
        return []
    weights = [max(len(p), 1) for p in parts]
    total = float(sum(weights))
    cues, at = [], start
    for part, w in zip(parts, weights):
        span = (end - start) * w / total
        cues.append({'start': round(at, 2), 'end': round(at + span, 2), 'text': part})
        at += span
    return cues


def _retry_wait(attempt, retry_after=None):
    try:
        w = float(retry_after or 0)
    except ValueError:
        w = 0
    return min(30.0, w if w > 0 else (2 ** attempt) + random.random())


def _error_detail(resp):
    try:
        d = resp.json()
        e = d.get('error') if isinstance(d, dict) else None
        return str((e.get('message') if isinstance(e, dict) else e) or d.get('message') or d)[:200]
    except ValueError:
        return resp.text[:200]


# ------------------------------------------------- OpenAI 兼容（OpenAI、硅基流动）----
def openai_transcribe(settings, name, path: Path, language, cancel, prompt=''):
    p = settings['providers'][name]
    model = p.get('asr_model') or ('whisper-1' if name == 'openai' else 'Qwen/Qwen3-ASR-1.7B')
    want_segments = name == 'openai' and model == 'whisper-1'
    data = {'model': model, 'response_format': 'verbose_json' if want_segments else 'json'}
    if want_segments:
        data['timestamp_granularities[]'] = 'segment'
    if name == 'openai' and language in ('zh', 'en'):
        data['language'] = language
    if name == 'openai' and prompt:
        data['prompt'] = prompt
    url = p['base_url'].rstrip('/') + '/audio/transcriptions'
    last = None
    for attempt in range(RETRIES + 1):
        if cancel.is_set():
            raise Cancelled('已取消')
        try:
            with open(path, 'rb') as f:
                r = requests.post(url, headers={'Authorization': 'Bearer ' + p['key']}, data=data,
                                  files={'file': (path.name, f, 'audio/mpeg')}, timeout=TIMEOUT, proxies=proxies_for(settings, name))
            if r.status_code in (401, 403):
                raise AsrError(f'{PROVIDER_NAMES[name]}鉴权失败，请检查 API Key')
            if r.status_code == 402 or re.search(r'balance|quota|insufficient|余额|额度|欠费', r.text[:500], re.I) and r.status_code >= 400:
                raise AsrError(f'{PROVIDER_NAMES[name]}额度不足或欠费：' + _error_detail(r))
            if r.status_code in (429, 500, 502, 503, 504) and attempt < RETRIES:
                last = _error_detail(r)
                time.sleep(_retry_wait(attempt, r.headers.get('Retry-After')))
                continue
            if r.status_code >= 400:
                raise AsrError(f'{PROVIDER_NAMES[name]}识别失败（HTTP {r.status_code}）：' + _error_detail(r))
            d = r.json()
            segs = d.get('segments') if isinstance(d.get('segments'), list) else None
            return tidy(d.get('text')), segs
        except requests.RequestException as e:
            last = str(e)[:200]
            if attempt < RETRIES:
                time.sleep(_retry_wait(attempt))
                continue
    raise AsrError(f'连接{PROVIDER_NAMES[name]}失败：' + (last or '未知错误'))


# ------------------------------------------------------------------ 豆包 ----
DOUBAO_OK, DOUBAO_SILENT, DOUBAO_RUNNING, DOUBAO_QUEUED = '20000000', '20000003', '20000001', '20000002'


def doubao_headers(p, resource, request_id=None, sequence=True):
    h = {'X-Api-Resource-Id': resource, 'X-Api-Request-Id': request_id or str(uuid.uuid4())}
    if sequence:
        h['X-Api-Sequence'] = '-1'
    if p.get('key'):
        h['X-Api-Key'] = p['key']                       # 新版控制台
    else:
        h['X-Api-App-Key'] = p.get('app_id', '')        # 旧版控制台
        h['X-Api-Access-Key'] = p.get('access_token', '')
    return h


def doubao_request_body(p, language, audio, context=''):
    req = {'model_name': 'bigmodel', 'enable_itn': True, 'enable_punc': True, 'show_utterances': True}
    if context:
        req['corpus'] = {'context': context}
    if p.get('enable_ddc'):
        req['enable_ddc'] = True
    if language in ('zh', 'en'):
        audio = dict(audio, language='zh-CN' if language == 'zh' else 'en-US')
    return {'user': {'uid': 'mengren-zimu'}, 'audio': audio, 'request': req}


QUOTA_WORDS = r'quota|balance|insufficient|exhaust|arrear|overdue|额度|余额|欠费|用完|用尽|耗尽|不足'


def doubao_check(code, message):
    if code in ('', DOUBAO_OK):
        return
    if re.search(QUOTA_WORDS, message or '', re.I):
        raise QuotaError('豆包额度不足或账户欠费：' + message)
    if code.startswith('450000') and re.search(r'auth|key|token|permission|resource|unauthor|grant', message or '', re.I):
        raise AuthError('豆包鉴权失败：请检查 API Key，以及是否已在控制台开通所选模型（' + message[:80] + '）')
    raise AsrError(f'豆包识别失败（{code}）：{(message or "")[:160]}')


def doubao_denied(message, model):
    """HTTP 401 / 403：多半是密钥不对或没开通，但额度用完时也可能这样回，看说明文字分开。"""
    if re.search(QUOTA_WORDS, message or '', re.I):
        return QuotaError('豆包额度不足或账户欠费：' + (message or '')[:160])
    return AuthError(f'豆包鉴权失败：请检查 API Key，以及是否已开通「{DOUBAO_MODELS[model]["name"]}」（{(message or "")[:80]}）')


def doubao_accounts(p):
    """主账号 + 备用账号 → [{id, name, key, app_id, access_token}]，没填密钥的不算。"""
    out = []
    if p.get('key') or (p.get('app_id') and p.get('access_token')):
        out.append({'id': 'main', 'name': (p.get('name') or '').strip() or '账号 1',
                    'key': p.get('key', ''), 'app_id': p.get('app_id', ''), 'access_token': p.get('access_token', '')})
    for a in p.get('more_keys') or []:
        if isinstance(a, dict) and a.get('id') and a.get('key'):
            out.append({'id': a['id'], 'name': (a.get('name') or '').strip() or f'账号 {len(out) + 1}',
                        'key': a['key'], 'app_id': '', 'access_token': ''})
    return out


def doubao_plan(p):
    """要依次试的 (账号, 模型)：每种模型先把所有账号的用完，再换下一种（质量好的尽量多用）；跳过标记了用完的。"""
    spent = p.get('spent') or {}
    accounts = doubao_accounts(p)
    return [(a, m) for m in DOUBAO_ORDER for a in accounts if f"{a['id']}|{m}" not in spent]


def with_account(settings, account, model):
    """一份只换了豆包账号和模型的设置（浅拷贝，不改原来的）。"""
    out = dict(settings, providers=dict(settings['providers']))
    out['providers']['doubao'] = dict(settings['providers']['doubao'], model=model, key=account['key'],
                                      app_id=account['app_id'], access_token=account['access_token'])
    return out


def mark_spent(settings, account, model):
    """记下这个账号的这种模型用完了（设置文件，软件和命令行共用），以后跳过，直到用户在设置里点「重新启用」。"""
    from . import config
    key = f"{account['id']}|{model}"
    day = time.strftime('%Y-%m-%d')
    settings['providers']['doubao'].setdefault('spent', {})[key] = day  # 这次运行接下来的课也跳过
    try:
        spent = dict(config.load()['providers']['doubao'].get('spent') or {})
        spent[key] = day
        config.update({'providers': {'doubao': {'spent': spent}}})
    except Exception:
        log.warning('记录豆包额度用完失败', exc_info=True)


def doubao_utterances(data):
    result = data.get('result') if isinstance(data, dict) else None
    if isinstance(result, list):
        result = result[0] if result else None
    if not isinstance(result, dict):
        return '', None
    segs = [{'start': u['start_time'] / 1000.0, 'end': u['end_time'] / 1000.0, 'text': u.get('text')}
            for u in (result.get('utterances') or [])
            if isinstance(u, dict) and isinstance(u.get('start_time'), (int, float)) and isinstance(u.get('end_time'), (int, float))]
    return tidy(result.get('text')), segs or None


def doubao_flash(settings, path: Path, language, cancel, context=''):
    p = settings['providers']['doubao']
    body = doubao_request_body(p, language, {'data': base64.b64encode(path.read_bytes()).decode(), 'format': 'mp3'}, context)
    url = p['base_url'].rstrip('/') + '/auc/bigmodel/recognize/flash'
    last = None
    for attempt in range(RETRIES + 1):
        if cancel.is_set():
            raise Cancelled('已取消')
        try:
            r = requests.post(url, json=body, headers=doubao_headers(p, DOUBAO_MODELS['flash']['resource']), timeout=TIMEOUT,
                              proxies=proxies_for(settings, 'doubao'))
            code, msg = r.headers.get('X-Api-Status-Code', ''), r.headers.get('X-Api-Message', '')
            if r.status_code in (401, 403):
                raise doubao_denied(msg or _error_detail(r), 'flash')
            if code == DOUBAO_SILENT:
                return '', None
            if (code.startswith('55') or r.status_code in (429, 500, 502, 503, 504)) and attempt < RETRIES:
                last = f'{code} {msg}'
                time.sleep(_retry_wait(attempt))
                continue
            doubao_check(code, msg or _error_detail(r))
            if r.status_code >= 400:
                raise AsrError(f'豆包识别失败（HTTP {r.status_code}）：' + _error_detail(r))
            return doubao_utterances(r.json())
        except requests.RequestException as e:
            last = str(e)[:200]
            if attempt < RETRIES:
                time.sleep(_retry_wait(attempt))
                continue
    raise AsrError('连接豆包失败：' + (last or '未知错误'))


# 流式识别（bigmodel_nostream）的二进制协议：4 字节头 + 4 字节长度 + gzip 负载
def _frame(msg_type, flags, serial, payload: bytes) -> bytes:
    body = gzip.compress(payload)
    return bytes([0x11, (msg_type << 4) | flags, (serial << 4) | 0x1, 0x00]) + struct.pack('>I', len(body)) + body


def _parse_frame(raw: bytes):
    header_size = (raw[0] & 0x0F) * 4
    msg_type, flags = raw[1] >> 4, raw[1] & 0x0F
    compress = raw[2] & 0x0F
    pos = header_size
    if msg_type == 0x0F:
        code, size = struct.unpack('>II', raw[pos:pos + 8])
        msg = raw[pos + 8:pos + 8 + size]
        try:
            msg = gzip.decompress(msg) if compress == 1 else msg
        except OSError:
            pass
        return 'error', flags, {'code': str(code), 'message': msg.decode('utf-8', 'replace')}
    if flags & 0x01:
        pos += 4  # sequence
    size = struct.unpack('>I', raw[pos:pos + 4])[0]
    payload = raw[pos + 4:pos + 4 + size]
    if compress == 1:
        payload = gzip.decompress(payload)
    return 'response', flags, json.loads(payload.decode('utf-8') or '{}')


def doubao_stream(settings, wav: Path, start: float, end: float, language, cancel, context='', on_progress=None):
    """流式识别一段音频。on_progress(0~1)：这一段发送了多少。"""
    import websocket  # websocket-client
    p = settings['providers']['doubao']
    model = DOUBAO_MODELS[p['model']]
    url = p['base_url'].replace('https://', 'wss://').rstrip('/') + '/sauc/bigmodel_nostream'
    headers = doubao_headers(p, model['resource'], sequence=False)
    headers['X-Api-Connect-Id'] = headers.pop('X-Api-Request-Id')
    speed = max(1.0, float(p.get('stream_speed') or 8))
    packet_seconds = 0.2
    with wave.open(str(wav), 'rb') as w:
        rate = w.getframerate()
        w.setpos(int(start * rate))
        pcm = w.readframes(int((end - start) * rate))
    step = int(rate * packet_seconds) * 2
    body = doubao_request_body(p, language, {'format': 'pcm', 'codec': 'raw', 'rate': 16000, 'bits': 16, 'channel': 1}, context)
    last_err = None
    for attempt in range(RETRIES + 1):
        if cancel.is_set():
            raise Cancelled('已取消')
        try:
            ws = websocket.create_connection(url, header=[f'{k}: {v}' for k, v in headers.items()], timeout=60,
                                             **_ws_proxy(settings))
        except websocket.WebSocketBadStatusException as e:
            if e.status_code in (401, 403):
                raise doubao_denied(str(e), p['model'])
            last_err = str(e)[:200]
            time.sleep(_retry_wait(attempt))
            continue
        except (OSError, websocket.WebSocketException) as e:
            last_err = str(e)[:200]
            time.sleep(_retry_wait(attempt))
            continue
        try:
            latest = {}
            ws.send_binary(_frame(0x1, 0x0, 0x1, json.dumps(body).encode()))
            kind, _, data = _parse_frame(ws.recv())
            if kind == 'error':
                doubao_check(data['code'], data['message'])
            total = max(len(pcm), 1)

            # 发送和接收分开：以前每发一包都要等服务端回一包，实际只有一两倍速，长视频进度又不动，看起来像卡住了
            def send():
                try:
                    for i in range(0, total, step):
                        if cancel.is_set():
                            return
                        last = i + step >= len(pcm)
                        ws.send_binary(_frame(0x2, 0x2 if last else 0x0, 0x0, pcm[i:i + step]))
                        if on_progress:
                            on_progress(min(1.0, (i + step) / total))
                        if not last:
                            time.sleep(packet_seconds / speed)
                except (OSError, websocket.WebSocketException):
                    pass  # 连接断了，接收那边会报错并重试
            sender = threading.Thread(target=send, daemon=True, name='doubao-send')
            sender.start()
            while True:
                if cancel.is_set():
                    raise Cancelled('已取消')
                kind, flags, data = _parse_frame(ws.recv())
                if kind == 'error':
                    doubao_check(data['code'], data['message'])
                if data.get('result'):
                    latest = data
                if flags & 0x02:  # 最后一包的结果
                    break
            return doubao_utterances(latest)
        except (OSError, websocket.WebSocketException) as e:
            last_err = str(e)[:200]
            if attempt < RETRIES:
                time.sleep(_retry_wait(attempt))
                continue
        finally:
            try:
                ws.close()
            except Exception:
                pass
    raise AsrError('连接豆包流式识别失败：' + (last_err or '未知错误'))


def _ws_proxy(settings):
    if not (settings['providers']['doubao'].get('use_proxy') and settings.get('proxy')):
        return {}
    from urllib.parse import urlparse
    u = urlparse(settings['proxy'])
    return {'http_proxy_host': u.hostname, 'http_proxy_port': u.port or 80, 'proxy_type': 'http'}


def doubao_task(settings, mp3: Path, language, cancel, context=''):
    """录音文件识别 2.0 标准版：提交（直接带 base64 音频）→ 轮询结果。"""
    p = settings['providers']['doubao']
    model = DOUBAO_MODELS[p['model']]
    base = p['base_url'].rstrip('/')
    task_id = str(uuid.uuid4())
    body = doubao_request_body(p, language, {'data': base64.b64encode(mp3.read_bytes()).decode(), 'format': 'mp3'}, context)
    last = None
    for attempt in range(RETRIES + 1):
        if cancel.is_set():
            raise Cancelled('已取消')
        try:
            r = requests.post(base + model['submit'], json=body, headers=doubao_headers(p, model['resource'], task_id), timeout=TIMEOUT,
                              proxies=proxies_for(settings, 'doubao'))
        except requests.RequestException as e:
            last = str(e)[:200]
            time.sleep(_retry_wait(attempt))
            continue
        code, msg = r.headers.get('X-Api-Status-Code', ''), r.headers.get('X-Api-Message', '')
        if r.status_code in (401, 403):
            raise doubao_denied(msg or _error_detail(r), p['model'])
        if (code.startswith('55') or r.status_code in (429, 500, 502, 503, 504)) and attempt < RETRIES:
            last = f'{code} {msg}'
            time.sleep(_retry_wait(attempt))
            continue
        doubao_check(code, msg or r.text[:200])
        if r.status_code >= 400:
            raise AsrError(f'豆包提交任务失败（HTTP {r.status_code}）：' + _error_detail(r))
        break
    else:
        raise AsrError('连接豆包失败：' + (last or '未知错误'))
    started, wait, errors = time.time(), 3, 0
    while True:
        for _ in range(int(wait * 10)):
            if cancel.is_set():
                raise Cancelled('已取消')
            time.sleep(0.1)
        wait = min(10, wait * 1.5)
        try:
            r = requests.post(base + model['query'], json={}, headers=doubao_headers(p, model['resource'], task_id, sequence=False),
                              timeout=60, proxies=proxies_for(settings, 'doubao'))
        except requests.RequestException as e:
            errors += 1
            if errors > RETRIES:
                raise AsrError('查询豆包识别结果失败：' + str(e)[:200])
            continue
        code, msg = r.headers.get('X-Api-Status-Code', ''), r.headers.get('X-Api-Message', '')
        if code == DOUBAO_OK:
            return doubao_utterances(r.json())
        if code == DOUBAO_SILENT:
            return '', None
        if code not in (DOUBAO_RUNNING, DOUBAO_QUEUED):
            doubao_check(code, msg or r.text[:200])
        if time.time() - started > 3 * 3600:
            raise AsrError('豆包 3 小时还没识别完，请稍后重新生成字幕')


# --------------------------------------------------------------- 总流程 ----
def chunk_plan(name, settings):
    """(目标段长, 最长段长, 并发数)"""
    if name == 'openai':
        return 600, 900, 3
    if name == 'siliconflow':
        return 20, 40, 3
    kind = DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind']
    if kind == 'stream':
        return 300, 540, 2  # 长视频切成几段，两段同时发
    if kind == 'task':
        return 1200, 1800, 3  # 标准版：20 分钟左右一段（18 分钟的课一次发完，和实测一样）
    return 300, 540, 3  # 极速版


def asr_context(teacher, title, language):
    """术语表和老师背景 → 各服务能读的形式：(豆包 corpus.context, Whisper prompt, 直接替换表)。"""
    from .llm import parse_terms
    if not teacher:
        return '', '', []
    words, _hints, fixes = parse_terms(teacher.get('terms'))
    if words:  # 豆包「热词直传」
        doubao = json.dumps({'hotwords': [{'word': w} for w in words[:1000]]}, ensure_ascii=False)
    else:
        bg = '。'.join(x for x in (title, teacher.get('name'), teacher.get('about')) if x)[:700]
        doubao = json.dumps({'context_type': 'dialog_ctx', 'context_data': [{'text': bg}]}, ensure_ascii=False) if bg else ''
    lead = ('Glossary: ' if language == 'en' else '术语：') + ('、' if language != 'en' else ', ').join(words)
    prompt = (lead[:200] + ('. ' if language == 'en' else '。') + (title or '')) if words else (title or '')
    return doubao, prompt[-240:], fixes


class _Stop:
    """一次识别尝试自己的「停止」：用户取消也算停止；某一段失败时只停这一次，换账号或模型后还能接着识别。"""

    def __init__(self, outer):
        self.outer, self.own = outer, threading.Event()

    def is_set(self):
        return self.outer.is_set() or self.own.is_set()

    def set(self):
        self.own.set()


def recognise(media_path, duration, provider, language, settings, cancel, progress, on_cues, teacher=None, title=''):
    """生成整段字幕。progress(fraction, message, extra=None)；on_cues(list) 按顺序收到每段的字幕。
    豆包按 doubao_plan 依次试「账号 × 模型」：额度用完（记下来，以后跳过）或没开通就换下一个，从头重新识别，
    换之前 on_cues(None) 表示前面收到的作废。识别完 progress 的 extra = {'used': 用的哪个, 'notes': [换过的原因]}。"""
    check_ready(settings, provider)
    if provider != 'doubao':
        cues = _recognise_once(media_path, duration, provider, language, settings, cancel, progress, on_cues, teacher, title)
        progress(1.0, '识别完成')
        return cues
    plan = doubao_plan(settings['providers']['doubao'])
    if not plan:
        raise QuotaError('豆包额度不足或账户欠费：所有账号的三种识别模型都标记成用完了。'
                         '充值或买了资源包以后，到「设置 → 豆包语音」点「重新启用」')
    notes, quota = [], False
    for k, (account, model) in enumerate(plan):
        used = f"{account['name']} · {DOUBAO_MODELS[model]['short']}"
        got = []

        def cues_in(cues):
            got.append(1)
            on_cues(cues)
        try:
            cues = _recognise_once(media_path, duration, provider, language, with_account(settings, account, model),
                                   _Stop(cancel), progress, cues_in, teacher, title)
            progress(1.0, '识别完成', {'used': used, 'notes': notes})
            return cues
        except (QuotaError, AuthError) as e:
            if cancel.is_set():
                raise Cancelled('已取消')
            if isinstance(e, QuotaError):
                quota = True
                mark_spent(settings, account, model)
                notes.append(f'{used} 的额度用完了')
            else:
                notes.append(f'{used} 用不了（没开通这种模型，或密钥不对）')
            log.warning('豆包识别：%s，换下一个。%s', notes[-1], str(e)[:200])
            if got:
                on_cues(None)
            if k + 1 < len(plan):
                a2, m2 = plan[k + 1]
                progress(0.0, f"{notes[-1]}，换成 {a2['name']} · {DOUBAO_MODELS[m2]['short']} 重新识别…")
    if quota:
        raise QuotaError('豆包额度不足或账户欠费：' + '；'.join(notes) + '。充值或买了资源包以后，到「设置 → 豆包语音」点「重新启用」')
    raise AuthError('豆包鉴权失败：' + '；'.join(notes) + '。请检查 API Key，以及在火山引擎控制台「豆包语音 → 开通管理」里开通了这几种模型')


def _recognise_once(media_path, duration, provider, language, settings, cancel, progress, on_cues, teacher=None, title=''):
    """用设置里的这一个服务（豆包是这一个账号的这一种模型）识别一遍。"""
    label = PROVIDER_NAMES[provider]
    ctx_doubao, ctx_prompt, fixes = asr_context(teacher, title, language)
    if fixes:
        from .llm import apply_fixes
        emit = on_cues

        def on_cues(cues):
            for c in cues:
                c['text'] = apply_fixes(c['text'], fixes)
            emit(cues)
    progress(0.0, '正在提取音频…')
    wav = media.extract_wav(media_path, duration, lambda f: progress(0.08 * f, '正在提取音频…'), cancel)
    if cancel.is_set():
        raise Cancelled('已取消')
    kind = DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind'] if provider == 'doubao' else ''
    if provider == 'doubao':
        label = f"豆包（{DOUBAO_MODELS[settings['providers']['doubao']['model']]['short']}）"

    target, maximum, workers = chunk_plan(provider, settings)
    progress(0.09, '正在按停顿切分音频…' if duration > maximum else '正在连接识别服务…')
    mids = media.silences(wav) if duration > maximum else []
    chunks = media.plan_chunks(mids, duration, target, maximum)
    scratch = Path(tempfile.mkdtemp(dir=CACHE_DIR, prefix='chunks-'))
    is_stream = kind == 'stream'
    sent = {}  # 流式识别：每段发送了多少，用来显示进度
    shown = {'t': 0.0}
    progress(0.1, f'{label}识别中…')

    def stream_progress(i, f):
        sent[i] = f
        if time.time() - shown['t'] < 0.5:
            return
        shown['t'] = time.time()
        done = sum((b - a) * sent.get(k, 0) for k, (a, b) in enumerate(chunks)) / max(duration, 1)
        progress(0.1 + 0.85 * min(done, 1.0), f'{label}识别中… 已发送 {done * 100:.0f}%')

    def work(i):
        if cancel.is_set():
            raise Cancelled('已取消')
        a, b = chunks[i]
        if is_stream:
            return doubao_stream(settings, wav, a, b, language, cancel, ctx_doubao, lambda f: stream_progress(i, f))
        piece = media.encode_chunk(wav, a, b, scratch / f'c{i}')
        try:
            if kind == 'task':
                return doubao_task(settings, piece, language, cancel, ctx_doubao)
            if provider == 'doubao':
                return doubao_flash(settings, piece, language, cancel, ctx_doubao)
            return openai_transcribe(settings, provider, piece, language, cancel, ctx_prompt)
        finally:
            piece.unlink(missing_ok=True)

    made = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, i) for i in range(len(chunks))]
            for i, fut in enumerate(futures):
                try:
                    text, segs = fut.result()
                except BaseException:
                    cancel.set()
                    for f in futures:
                        f.cancel()
                    raise
                a, b = chunks[i]
                cues = cues_from(text, segs, a, b)
                made += cues
                if cues:
                    on_cues(cues)
                if not is_stream:  # 流式识别按发送进度显示
                    progress(0.1 + 0.9 * (i + 1) / len(chunks), f'{label}识别中… {i + 1}/{len(chunks)} 段')
    finally:
        for f in scratch.glob('*'):
            f.unlink(missing_ok=True)
        try:
            scratch.rmdir()
        except OSError:
            pass
    return made


def test_provider(settings, name):
    """用一段 1.5 秒的音频测试密钥和模型能不能用。豆包：每个账号的三种模型都试，能用的顺便取消「用完了」的标记。"""
    check_ready(settings, name)
    tmp = Path(tempfile.mkdtemp(dir=CACHE_DIR if CACHE_DIR.exists() else None))
    try:
        wav = tmp / 'tone.wav'
        media.run([media.tool('ffmpeg'), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5',
                   '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], timeout=60)
        cancel = threading.Event()
        if name != 'doubao':
            openai_transcribe(settings, name, media.encode_chunk(wav, 0, 1.5, tmp / 'c'), 'zh', cancel)
            return '连接正常'
        mp3 = media.encode_chunk(wav, 0, 1.5, tmp / 'c')
        lines, ok = [], []

        def one(account, model):
            s = with_account(settings, account, model)
            kind = DOUBAO_MODELS[model]['kind']
            try:
                if kind == 'stream':
                    doubao_stream(s, wav, 0, 1.5, 'zh', cancel)
                elif kind == 'task':
                    doubao_task(s, mp3, 'zh', cancel)
                else:
                    doubao_flash(s, mp3, 'zh', cancel)
                ok.append(f"{account['id']}|{model}")
                return '能用'
            except QuotaError:
                return '额度用完了'
            except AuthError:
                return '没开通或密钥不对'
            except AsrError as e:
                return '出错：' + str(e)[:60]
        for account in doubao_accounts(settings['providers']['doubao']):
            with ThreadPoolExecutor(max_workers=3) as pool:
                res = list(pool.map(lambda m: one(account, m), DOUBAO_ORDER))
            lines.append(account['name'] + '：' + '，'.join(f"{DOUBAO_MODELS[m]['short']} {r}" for m, r in zip(DOUBAO_ORDER, res)))
        spent = dict(settings['providers']['doubao'].get('spent') or {})
        back = [k for k in ok if k in spent]
        if back:
            from . import config
            config.update({'providers': {'doubao': {'spent': {k: v for k, v in spent.items() if k not in back}}}})
            lines.append(f'以前标记用完的 {len(back)} 项现在能用了，已重新启用')
        return '\n'.join(lines)
    finally:
        for f in tmp.glob('*'):
            f.unlink(missing_ok=True)
        tmp.rmdir()
