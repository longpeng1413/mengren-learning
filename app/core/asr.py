"""字幕识别。

支持三家服务：
- OpenAI（whisper-1，返回句子级时间戳）；
- 硅基流动（OpenAI 兼容接口，只返回文字，按停顿切成短段后按句长分配时间）；
- 豆包（火山引擎），按官方文档分三类接口：
  * 录音文件识别·极速版：一次请求直接上传音频（base64），单次 ≤2 小时 / 100MB；
  * 流式识别 1.0 / 2.0（bigmodel_nostream）：WebSocket 直接发送本地音频，可用 2.0 模型；
  * 录音文件识别 1.0 / 2.0 标准版、闲时版：只接受公网音频链接，需要先上传到火山引擎对象存储（TOS）。
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
from .config import CACHE_DIR, PROVIDER_NAMES

RETRIES = 3
TIMEOUT = 600

DOUBAO_MODELS = {
    'flash':    {'name': '录音文件识别 · 极速版（推荐）', 'resource': 'volc.bigasr.auc_turbo', 'kind': 'flash',
                 'note': '直接上传音频，返回快，带逐句时间戳。'},
    'stream2':  {'name': '流式识别 2.0 · 小时版', 'resource': 'volc.seedasr.sauc.duration', 'kind': 'stream',
                 'note': '豆包 2.0 模型，直接发送本地音频，按时长计费。'},
    'stream2c': {'name': '流式识别 2.0 · 并发版', 'resource': 'volc.seedasr.sauc.concurrent', 'kind': 'stream',
                 'note': '豆包 2.0 模型，按并发路数计费。'},
    'stream1':  {'name': '流式识别 1.0 · 小时版', 'resource': 'volc.bigasr.sauc.duration', 'kind': 'stream', 'note': ''},
    'stream1c': {'name': '流式识别 1.0 · 并发版', 'resource': 'volc.bigasr.sauc.concurrent', 'kind': 'stream', 'note': ''},
    'std2':     {'name': '录音文件识别 2.0 · 标准版', 'resource': 'volc.seedasr.auc', 'kind': 'task',
                 'submit': '/auc/bigmodel/submit', 'query': '/auc/bigmodel/query',
                 'note': '豆包 2.0 模型。需要先在设置里填写火山引擎对象存储（TOS）。'},
    'std1':     {'name': '录音文件识别 1.0 · 标准版', 'resource': 'volc.bigasr.auc', 'kind': 'task',
                 'submit': '/auc/bigmodel/submit', 'query': '/auc/bigmodel/query',
                 'note': '需要先在设置里填写火山引擎对象存储（TOS）。'},
    'idle':     {'name': '录音文件识别 · 闲时版（最便宜，可能要等很久）', 'resource': 'volc.bigasr.auc_idle', 'kind': 'task',
                 'submit': '/auc/bigmodel/idle/submit', 'query': '/auc/bigmodel/idle/query',
                 'note': '空闲时才处理，官方承诺 24 小时内出结果。需要对象存储（TOS）。'},
}


class AsrError(Exception):
    pass


class Cancelled(AsrError):
    pass


def proxies_for(settings, name):
    p = settings['providers'][name]
    if p.get('use_proxy') and settings.get('proxy'):
        return {'http': settings['proxy'], 'https': settings['proxy']}
    return None


def check_ready(settings, name):
    p = settings['providers'][name]
    label = PROVIDER_NAMES[name]
    if name == 'doubao':
        if not (p.get('key') or (p.get('app_id') and p.get('access_token'))):
            raise AsrError(f'还没有填写{label}的 API Key，请到「设置」里填写')
        if DOUBAO_MODELS.get(p.get('model'), {}).get('kind') == 'task' and not all(p.get(k) for k in ('tos_ak', 'tos_sk', 'tos_bucket', 'tos_endpoint')):
            raise AsrError('豆包标准版和闲时版需要先把音频传到火山引擎对象存储，请到「设置」里填写 TOS 的 AK/SK 和存储桶')
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


def doubao_check(code, message):
    if code in ('', DOUBAO_OK):
        return
    if re.search(r'quota|balance|insufficient|额度|余额|欠费', message or '', re.I):
        raise AsrError('豆包额度不足或账户欠费：' + message)
    if code.startswith('450000') and re.search(r'auth|key|token|permission|resource|unauthor|grant', message or '', re.I):
        raise AsrError('豆包鉴权失败：请检查 API Key，以及是否已在控制台开通所选模型（' + message[:80] + '）')
    raise AsrError(f'豆包识别失败（{code}）：{(message or "")[:160]}')


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
                raise AsrError('豆包鉴权失败：请检查 API Key，以及是否已开通「录音文件识别极速版」')
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


def doubao_stream(settings, wav: Path, start: float, end: float, language, cancel, context=''):
    import websocket  # websocket-client
    p = settings['providers']['doubao']
    model = DOUBAO_MODELS[p['model']]
    url = p['base_url'].replace('https://', 'wss://').rstrip('/') + '/sauc/bigmodel_nostream'
    headers = doubao_headers(p, model['resource'], sequence=False)
    headers['X-Api-Connect-Id'] = headers.pop('X-Api-Request-Id')
    speed = max(1.0, float(p.get('stream_speed') or 4))
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
                raise AsrError('豆包鉴权失败：请检查 API Key，以及是否已开通所选的流式识别模型')
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
            for i in range(0, max(len(pcm), 1), step):
                if cancel.is_set():
                    raise Cancelled('已取消')
                last = i + step >= len(pcm)
                ws.send_binary(_frame(0x2, 0x2 if last else 0x0, 0x0, pcm[i:i + step]))
                kind, flags, data = _parse_frame(ws.recv())
                if kind == 'error':
                    doubao_check(data['code'], data['message'])
                if data.get('result'):
                    latest = data
                if not last:
                    time.sleep(packet_seconds / speed)
            # 发完最后一包后，等服务端返回最终结果
            deadline = time.time() + 120
            while time.time() < deadline:
                if flags & 0x02:
                    break
                kind, flags, data = _parse_frame(ws.recv())
                if kind == 'error':
                    doubao_check(data['code'], data['message'])
                if data.get('result'):
                    latest = data
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


def _tos_client(p):
    import tos
    return tos.TosClientV2(p['tos_ak'], p['tos_sk'], p['tos_endpoint'], p['tos_region'])


def doubao_task(settings, mp3: Path, language, cancel, progress, pending=None, context=''):
    """标准版/闲时版：传到 TOS → 生成临时链接 → 提交 → 轮询。pending 用于闲时版重开软件后继续查询。"""
    p = settings['providers']['doubao']
    model = DOUBAO_MODELS[p['model']]
    base = p['base_url'].rstrip('/')
    client = _tos_client(p)
    if pending:
        task_id, key = pending['task_id'], pending['object']
    else:
        key = f'mengren-zimu/{uuid.uuid4().hex}.mp3'
        progress(0.05, '正在上传音频到对象存储…')
        try:
            client.put_object_from_file(p['tos_bucket'], key, str(mp3))
            signed = client.pre_signed_url('GET', p['tos_bucket'], key, expires=86400 * 2).signed_url
        except Exception as e:
            raise AsrError('上传到火山引擎对象存储失败，请检查 TOS 设置：' + str(e)[:200])
        task_id = str(uuid.uuid4())
        body = doubao_request_body(p, language, {'url': signed, 'format': 'mp3'}, context)
        r = requests.post(base + model['submit'], json=body, headers=doubao_headers(p, model['resource'], task_id), timeout=60,
                          proxies=proxies_for(settings, 'doubao'))
        doubao_check(r.headers.get('X-Api-Status-Code', ''), r.headers.get('X-Api-Message', '') or r.text[:200])
        if r.status_code >= 400:
            raise AsrError(f'豆包提交任务失败（HTTP {r.status_code}）：' + _error_detail(r))
    info = {'task_id': task_id, 'object': key, 'model': p['model'], 'submitted': time.time()}
    progress(0.15, '已提交，等待豆包识别…', info)
    started, wait = time.time(), 3
    try:
        while True:
            if cancel.is_set():
                raise Cancelled('已取消')
            r = requests.post(base + model['query'], json={}, headers=doubao_headers(p, model['resource'], task_id, sequence=False),
                              timeout=60, proxies=proxies_for(settings, 'doubao'))
            code, msg = r.headers.get('X-Api-Status-Code', ''), r.headers.get('X-Api-Message', '')
            if code == DOUBAO_OK:
                return doubao_utterances(r.json())
            if code == DOUBAO_SILENT:
                return '', None
            if code not in (DOUBAO_RUNNING, DOUBAO_QUEUED):
                doubao_check(code, msg or r.text[:200])
            mins = int((time.time() - started) / 60)
            progress(0.2, ('排队中' if code == DOUBAO_QUEUED else '识别中') + f'…已等待 {mins} 分钟', info)
            for _ in range(int(wait * 10)):
                if cancel.is_set():
                    raise Cancelled('已取消')
                time.sleep(0.1)
            wait = min(60 if p['model'] == 'idle' else 10, wait * 1.5)
    finally:
        if not cancel.is_set():
            try:
                client.delete_object(p['tos_bucket'], key)
            except Exception:
                pass


# --------------------------------------------------------------- 总流程 ----
def chunk_plan(name, settings):
    """(目标段长, 最长段长, 并发数)"""
    if name == 'openai':
        return 600, 900, 3
    if name == 'siliconflow':
        return 20, 40, 3
    kind = DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind']
    if kind == 'stream':
        return 600, 900, 2
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


def recognise(media_path, duration, provider, language, settings, cancel, progress, on_cues, pending=None, teacher=None, title=''):
    """生成整段字幕。progress(fraction, message, extra=None)；on_cues(list) 按顺序收到每段的字幕。"""
    check_ready(settings, provider)
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

    if provider == 'doubao' and DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind'] == 'task':
        mp3 = media.encode_chunk(wav, 0, duration, CACHE_DIR / 'audio' / (wav.stem + '-full'))
        try:
            text, segs = doubao_task(settings, mp3, language, cancel, lambda f, m, extra=None: progress(0.1 + 0.85 * f, m, extra), pending, ctx_doubao)
        finally:
            mp3.unlink(missing_ok=True)
        cues = cues_from(text, segs, 0.0, duration)
        on_cues(cues)
        progress(1.0, '识别完成')
        return cues

    target, maximum, workers = chunk_plan(provider, settings)
    progress(0.09, '正在按停顿切分音频…')
    mids = media.silences(wav) if duration > maximum else []
    chunks = media.plan_chunks(mids, duration, target, maximum)
    scratch = Path(tempfile.mkdtemp(dir=CACHE_DIR, prefix='chunks-'))
    is_stream = provider == 'doubao' and DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind'] == 'stream'

    def work(i):
        if cancel.is_set():
            raise Cancelled('已取消')
        a, b = chunks[i]
        if is_stream:
            return doubao_stream(settings, wav, a, b, language, cancel, ctx_doubao)
        piece = media.encode_chunk(wav, a, b, scratch / f'c{i}')
        try:
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
                progress(0.1 + 0.9 * (i + 1) / len(chunks), f'{label}识别中… {i + 1}/{len(chunks)} 段')
    finally:
        for f in scratch.glob('*'):
            f.unlink(missing_ok=True)
        try:
            scratch.rmdir()
        except OSError:
            pass
    progress(1.0, '识别完成')
    return made


def test_provider(settings, name):
    """用一段 1 秒的静音测试密钥和模型能不能用。"""
    check_ready(settings, name)
    tmp = Path(tempfile.mkdtemp(dir=CACHE_DIR if CACHE_DIR.exists() else None))
    try:
        wav = tmp / 'tone.wav'
        media.run([media.tool('ffmpeg'), '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5',
                   '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)], timeout=60)
        cancel = threading.Event()
        if name == 'doubao':
            kind = DOUBAO_MODELS[settings['providers']['doubao']['model']]['kind']
            if kind == 'stream':
                doubao_stream(settings, wav, 0, 1.5, 'zh', cancel)
            elif kind == 'task':
                _tos_client(settings['providers']['doubao']).head_bucket(settings['providers']['doubao']['tos_bucket'])
                return '对象存储连接正常。标准版/闲时版的识别要在真正生成字幕时才能验证。'
            else:
                doubao_flash(settings, media.encode_chunk(wav, 0, 1.5, tmp / 'c'), 'zh', cancel)
        else:
            openai_transcribe(settings, name, media.encode_chunk(wav, 0, 1.5, tmp / 'c'), 'zh', cancel)
        return '连接正常'
    finally:
        for f in tmp.glob('*'):
            f.unlink(missing_ok=True)
        tmp.rmdir()
