"""AI：翻译、术语校对、课程重点和对话。
支持的服务：
- OpenAI 兼容接口：OpenAI、DeepSeek、豆包大模型（火山方舟）、硅基流动；
- ChatGPT 账号登录（Codex 接口）；
- Claude（Anthropic 官方 SDK）；
- Gemini（Google 的 REST 接口）。"""
import difflib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import codex_auth
from .config import PROVIDER_NAMES

OPENAI_COMPAT = ('openai', 'deepseek', 'ark', 'siliconflow')


class LlmError(Exception):
    pass


def _proxies(settings, name):
    p = settings['providers'][name]
    return {'http': settings['proxy'], 'https': settings['proxy']} if p.get('use_proxy') and settings.get('proxy') else None


def check(settings, name):
    if name not in PROVIDER_NAMES or name == 'doubao':
        raise LlmError('未知的 AI 服务：' + str(name))
    p = settings['providers'][name]
    label = PROVIDER_NAMES[name]
    if name == 'codex':
        if not (p.get('oauth') or {}).get('refresh'):
            raise LlmError('还没有用 ChatGPT 账号登录，请到「设置 → AI 服务 → ChatGPT」登录')
    elif not p.get('key'):
        raise LlmError(f'还没有填写{label}的 API Key，请到「设置 → AI 服务」填写')
    if not p.get('chat_model'):
        raise LlmError(f'还没有选择{label}的模型，请到「设置 → AI 服务」选择')


def _raise_for(r, name):
    if r.status_code < 400:
        return
    try:
        d = r.json()
        e = d.get('error') if isinstance(d, dict) else None
        msg = (e.get('message') if isinstance(e, dict) else e) or d.get('message') or d.get('detail') or r.text
    except ValueError:
        msg = r.text
    msg = str(msg)[:240]
    label = PROVIDER_NAMES[name]
    if r.status_code in (401, 403):
        raise LlmError(f'{label}鉴权失败：{msg}')
    if r.status_code == 402 or re.search(r'balance|quota|insufficient|余额|额度|usage limit', msg, re.I):
        raise LlmError(f'{label}额度不足：{msg}')
    if r.status_code == 404:
        raise LlmError(f'{label}找不到这个模型，请在设置里换一个：{msg}')
    raise LlmError(f'{label}出错（HTTP {r.status_code}）：{msg}')


def _split_system(messages):
    system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
    rest = [m for m in messages if m['role'] in ('user', 'assistant')]
    return system, rest


# 消息内容可以是文字，也可以是 [{'type': 'text', 'text': …}, {'type': 'image', 'path': 图片文件}] 这样的图文混排
VISION_NOTE = {'deepseek': 'DeepSeek 不能看图'}


def _parts(content):
    return [{'type': 'text', 'text': content}] if isinstance(content, str) else list(content or [])


def _image_b64(part):
    import base64
    from pathlib import Path
    data = Path(part['path']).read_bytes()
    return base64.b64encode(data).decode('ascii'), part.get('mime') or ('image/png' if str(part['path']).lower().endswith('.png') else 'image/jpeg')


def _to_openai(content):
    if isinstance(content, str):
        return content
    out = []
    for p in _parts(content):
        if p['type'] == 'image':
            b64, mime = _image_b64(p)
            out.append({'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}})
        else:
            out.append({'type': 'text', 'text': p['text']})
    return out


def _to_codex(role, content):
    out = []
    for p in _parts(content):
        if p['type'] == 'image':
            b64, mime = _image_b64(p)
            out.append({'type': 'input_image', 'image_url': f'data:{mime};base64,{b64}'})
        else:
            out.append({'type': 'output_text' if role == 'assistant' else 'input_text', 'text': p['text']})
    return out


def _to_claude(content):
    if isinstance(content, str):
        return content
    out = []
    for p in _parts(content):
        if p['type'] == 'image':
            b64, mime = _image_b64(p)
            out.append({'type': 'image', 'source': {'type': 'base64', 'media_type': mime, 'data': b64}})
        else:
            out.append({'type': 'text', 'text': p['text']})
    return out


def _to_gemini(content):
    out = []
    for p in _parts(content):
        if p['type'] == 'image':
            b64, mime = _image_b64(p)
            out.append({'inline_data': {'mime_type': mime, 'data': b64}})
        else:
            out.append({'text': p['text']})
    return out


def _sse_lines(r):
    r.encoding = 'utf-8'
    for line in r.iter_lines(decode_unicode=True):
        if line and line.startswith('data:'):
            data = line[5:].strip()
            if data and data != '[DONE]':
                try:
                    yield json.loads(data)
                except ValueError:
                    continue


def _post_stream(url, name, settings, **kw):
    """带重试的流式请求：只在还没收到内容时重试。"""
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, stream=True, timeout=(20, 300), proxies=_proxies(settings, name), **kw)
        except requests.RequestException as e:
            last = str(e)[:200]
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            last = f'HTTP {r.status_code}'
            r.close()
            time.sleep(2 ** attempt + 1)
            continue
        _raise_for(r, name)
        return r
    raise LlmError(f'连接{PROVIDER_NAMES[name]}失败：{last}')


def stream(settings, name, messages, cancel=None, json_mode=False):
    """逐段产出回答文字。messages 可以包含 role=system 的消息。"""
    check(settings, name)
    p = settings['providers'][name]
    model = p['chat_model']

    if name in OPENAI_COMPAT:
        body = {'model': model, 'messages': [dict(m, content=_to_openai(m['content'])) for m in messages], 'stream': True}
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        with _post_stream(p['base_url'].rstrip('/') + '/chat/completions', name, settings, json=body,
                          headers={'Authorization': 'Bearer ' + p['key']}) as r:
            for d in _sse_lines(r):
                if cancel is not None and cancel.is_set():
                    return
                try:
                    piece = (d['choices'][0].get('delta') or {}).get('content')
                except (KeyError, IndexError):
                    continue
                if piece:
                    yield piece
        return

    if name == 'codex':
        try:
            o = codex_auth.fresh()
        except codex_auth.AuthError as e:
            raise LlmError(str(e))
        system, rest = _split_system(messages)
        body = {'model': model, 'instructions': system or 'You are a helpful assistant.', 'store': False, 'stream': True,
                'input': [{'type': 'message', 'role': m['role'], 'content': _to_codex(m['role'], m['content'])} for m in rest]}
        headers = dict(codex_auth.headers(o), **{'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
        with _post_stream(codex_auth.BASE + '/responses', name, settings, json=body, headers=headers) as r:
            for d in _sse_lines(r):
                if cancel is not None and cancel.is_set():
                    return
                t = d.get('type')
                if t == 'response.output_text.delta' and isinstance(d.get('delta'), str):
                    yield d['delta']
                elif t in ('response.failed', 'error'):
                    err = (d.get('response') or {}).get('error') or d.get('error') or {}
                    raise LlmError('ChatGPT 出错：' + str(err.get('message') if isinstance(err, dict) else err)[:240])
        return

    if name == 'claude':
        import anthropic
        from anthropic import DefaultHttpxClient
        system, rest = _split_system(messages)
        px = _proxies(settings, name)
        client = anthropic.Anthropic(api_key=p['key'], max_retries=2,
                                     http_client=DefaultHttpxClient(proxy=px['https']) if px else None)
        kwargs = dict(model=model, max_tokens=32000, messages=[dict(m, content=_to_claude(m['content'])) for m in rest])
        if system:
            kwargs['system'] = system
        try:
            if model in ('claude-opus-5-5', 'claude-opus-5', 'claude-fable-5-1', 'claude-sonnet-5-5'):
                ctx = client.beta.messages.stream(betas=['server-side-fallback-2026-07-01'], fallbacks='default', **kwargs)
            else:
                ctx = client.messages.stream(**kwargs)
            with ctx as s:
                for text in s.text_stream:
                    if cancel is not None and cancel.is_set():
                        return
                    yield text
                final = s.get_final_message()
                if final.stop_reason == 'refusal':
                    yield '\n\n（Claude 拒绝回答这个请求）'
        except anthropic.AuthenticationError:
            raise LlmError('Claude 鉴权失败，请检查 API Key')
        except anthropic.PermissionDeniedError as e:
            raise LlmError('Claude 拒绝访问：' + str(e.message)[:200])
        except anthropic.NotFoundError:
            raise LlmError('Claude 找不到这个模型，请在设置里换一个')
        except anthropic.RateLimitError:
            raise LlmError('Claude 请求太频繁或额度用完，请稍后再试')
        except anthropic.APIStatusError as e:
            raise LlmError(f'Claude 出错（{e.status_code}）：{str(e.message)[:200]}')
        except anthropic.APIConnectionError:
            raise LlmError('连接 Claude 失败，请检查网络和代理设置')
        return

    if name == 'gemini':
        system, rest = _split_system(messages)
        body = {'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': _to_gemini(m['content'])} for m in rest]}
        if system:
            body['systemInstruction'] = {'parts': [{'text': system}]}
        if json_mode:
            body['generationConfig'] = {'responseMimeType': 'application/json'}
        url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse'
        with _post_stream(url, name, settings, json=body, headers={'x-goog-api-key': p['key']}) as r:
            for d in _sse_lines(r):
                if cancel is not None and cancel.is_set():
                    return
                for c in d.get('candidates') or []:
                    for part in (c.get('content') or {}).get('parts') or []:
                        if part.get('text') and not part.get('thought'):
                            yield part['text']
        return
    raise LlmError('未知的 AI 服务：' + name)


def complete(settings, name, messages, json_mode=False):
    return ''.join(stream(settings, name, messages, json_mode=json_mode))


def list_models(settings, name):
    """服务商提供的模型列表，用来在设置里选择。"""
    p = settings['providers'][name]
    if name == 'codex':
        try:
            return codex_auth.models()
        except codex_auth.AuthError as e:
            raise LlmError(str(e))
    if not p.get('key'):
        raise LlmError(f'请先填写{PROVIDER_NAMES[name]}的 API Key 并保存')
    px = _proxies(settings, name)
    try:
        if name == 'claude':
            import anthropic
            from anthropic import DefaultHttpxClient
            client = anthropic.Anthropic(api_key=p['key'], http_client=DefaultHttpxClient(proxy=px['https']) if px else None)
            return [{'id': m.id, 'name': m.display_name} for m in client.models.list(limit=100)]
        if name == 'gemini':
            r = requests.get('https://generativelanguage.googleapis.com/v1beta/models?pageSize=200',
                             headers={'x-goog-api-key': p['key']}, timeout=30, proxies=px)
            _raise_for(r, name)
            return [{'id': m['name'].split('/', 1)[-1], 'name': m.get('displayName') or m['name']}
                    for m in r.json().get('models', []) if 'generateContent' in (m.get('supportedGenerationMethods') or [])]
        url = p['base_url'].rstrip('/') + '/models' + ('?type=text&sub_type=chat' if name == 'siliconflow' else '')
        r = requests.get(url, headers={'Authorization': 'Bearer ' + p['key']}, timeout=30, proxies=px)
        if name == 'ark' and r.status_code == 404:
            raise LlmError('火山方舟不提供模型列表，请在方舟控制台复制模型 ID（如 doubao-seed-…）或推理接入点 ID（ep-…）')
        _raise_for(r, name)
        items = r.json().get('data') or []
        return sorted(({'id': m['id'], 'name': m['id']} for m in items if m.get('id')), key=lambda m: m['id'])
    except requests.RequestException as e:
        raise LlmError(f'连接{PROVIDER_NAMES[name]}失败：{str(e)[:200]}')
    except LlmError:
        raise
    except Exception as e:  # SDK 的错误
        raise LlmError(f'获取{PROVIDER_NAMES[name]}模型列表失败：{str(e)[:200]}')


# ------------------------------------------------------------------ 字幕文本 ----
def fmt(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def transcript_text(doc, limit=200000):
    lines = []
    for seg in doc.get('segments', []):
        line = f"[{fmt(seg['start'])}] {seg.get('text', '')}"
        if seg.get('tr'):
            line += f"（{seg['tr']}）"
        lines.append(line)
    return '\n'.join(lines)[:limit]


def parse_terms(text):
    """术语表，每行一条：
    - 「术语」或「术语1, 术语2」：专用术语；
    - 「错写1, 错写2 => 正确」：常见识别错误，交给 AI 结合上下文判断，不直接替换；
    - 「错写 =>! 正确」：确定不会误伤的，识别完直接替换。
    返回 (术语列表, [(错写, 正确)] 参考对照, [(错写, 正确)] 直接替换)。"""
    words, hints, forced = [], [], []
    for raw in re.split(r'[\r\n]+', text or ''):
        line = raw.strip().strip('|').strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue
        note = ''
        cm = re.search(r'\s#\s*', line)  # 行尾「 # 说明」：给 AI 看的补充说明
        if cm:
            line, note = line[:cm.start()].strip(), line[cm.end():].strip()
        m = re.match(r'^(.+?)\s*(?:=>|->|→|＝>)\s*(!|！)?\s*(.+)$', line)
        if m:
            wrongs = [w.strip() for w in re.split(r'[,，、/|]', m.group(1)) if w.strip()]
            right = m.group(3).strip()
            if m.group(2):
                forced.extend((w, right) for w in wrongs if w != right)
            else:
                shown = right + ((note if note.startswith(('（', '(')) else f'（{note}）') if note else '')
                hints.extend((w, shown) for w in wrongs if w != right)
            words.append(right)
            continue
        words += [w.strip() for w in re.split(r'[,，、;；]', line) if w.strip()]
    seen, out = set(), []
    for w in words:
        if w not in seen and len(w) <= 30:
            seen.add(w)
            out.append(w)
    return out, hints, forced


def terms_from_markdown(text):
    """从 Obsidian 里的术语表文档（比如《课程术语表 V1.1》）读出软件能用的术语表写法：
    - 小节里写了「正确写法 / 标准写法」的，小节标题或加粗的写法就是术语；
    - 「--hotwords "a,b,c"」里的热词；
    - 表头含「ASR / 误识 / 识别」的表格：左列是常见识别错误，右列是正确写法（只作 AI 参考，不直接替换）。
    返回软件术语表格式的多行文字。"""
    text = text or ''
    words, hints = [], []
    # 1. 小节
    parts = re.split(r'(?m)^(#{1,4})\s+(.+?)\s*$', text)
    for k in range(1, len(parts) - 2, 3):
        title, body = parts[k + 1], parts[k + 2]
        m = re.search(r'(?:正确|标准)写法\**\s*[：:]\s*(?:\n\s*)*(?:>\s*)?\**([^*\n（(]+)', body)
        if not m:
            continue
        w = m.group(1).strip().strip('“”"')
        if not w or len(w) > 20:
            w = re.sub(r'^[一二三四五六七八九十百零〇\d]+[、.．]\s*', '', title).strip()
        if w and len(w) <= 20:
            words.append(w)
    # 2. 热词
    for m in re.finditer(r'--hotwords\s+["“]([^"”]+)["”]', text):
        words += [w.strip() for w in re.split(r'[,，]', m.group(1)) if w.strip()]
    # 3. 识别错误对照表
    lines = text.split('\n')
    i = 0
    while i < len(lines):
        head = lines[i].strip()
        first = head.strip('|').split('|')[0].strip() if head.startswith('|') else ''
        if first in ('术语', '新术语') and i + 1 < len(lines) and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                w = lines[i].strip().strip('|').split('|')[0].strip().strip('*“”"')
                if w and len(w) <= 20:
                    words.append(w)
                i += 1
            continue
        if head.startswith('|') and re.search(r'ASR|误识|识别|错误', head) and i + 1 < len(lines) and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i + 1]):
            i += 2
            while i < len(lines) and lines[i].strip().startswith('|'):
                cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                if len(cells) >= 2 and cells[0] and cells[1]:
                    wrongs = [w.strip() for w in re.split(r'[、,，/]', cells[0]) if w.strip() and '部分' not in w and '等' != w]
                    wrongs = [re.sub(r'等$', '', w) for w in wrongs]
                    right = cells[1]
                    clean = re.split(r'[（(，,]', right)[0].strip().strip('“”"')
                    note = right[len(clean):].strip() if right.startswith(clean) else ''
                    if '可能' in right or '结合上下文' in right:
                        q = re.search(r'[“"]([^”"]+)[”"]', right)
                        clean, note = (q.group(1) if q else ''), right
                    if clean and len(clean) <= 20:
                        hints.append((wrongs, clean, note))
                i += 1
            continue
        i += 1
    seen, out = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            out.append(w)
    rows = [', '.join(out[j:j + 12]) for j in range(0, len(out), 12)]
    for wrongs, right, note in hints:
        if wrongs:
            rows.append(f'{", ".join(wrongs)} => {right}' + (f'　# {note}' if note else ''))
    return '\n'.join(rows)


def teacher_block(teacher, with_notes=True):
    if not teacher:
        return ''
    words, hints, forced = parse_terms(teacher.get('terms'))
    # 档案名可能是老师，也可能是几位老师共用的课程体系（例如「峰级」），不能当成老师的名字
    parts = [f"术语表：{teacher.get('name', '')}"]
    if teacher.get('about'):
        parts.append('老师和课程背景：' + teacher['about'])
    if words:
        parts.append('专用术语表（必须用这些写法）：' + '、'.join(words))
    if hints or forced:
        parts.append('常见识别错误（只作参考，必须结合上下文判断，不能看到相似读音就替换）：'
                     + '；'.join(f'{a} → {b}' for a, b in hints + forced))
    if with_notes and teacher.get('notes'):
        parts.append('术语说明 / 参考资料（用来理解课程用语，不要把这里的内容当成老师在这节课里讲过的话）：\n'
                     + teacher['notes'][:NOTES_LIMIT])
    return '\n'.join(parts)


NOTES_LIMIT = 20000


def frames_block(doc, a=None, b=None, limit=30000):
    from .frames import notes_text
    text = notes_text(doc.get('frames'), a, b, limit)
    if not text:
        return ''
    return ('===== 画面（可插图）=====\n'
            '（AI 挑出的、文字不好说清需要看图的画面，每张一句话说明图上画的是什么。标了「可插图」的可以插进笔记。）\n' + text + '\n===== 画面结束 =====')


def system_prompt(doc, teacher=None):
    title = doc.get('title') or '视频'
    tb = teacher_block(teacher)
    fb = frames_block(doc)
    return (
        '你是用户的学习助手，帮助用户学习一节视频课程。请用中文回答。\n'
        f'课程标题：{title}\n' + (tb + '\n' if tb else '') +
        '下面是带时间戳的完整字幕' + ('和画面记录' if fb else '') + '。回答时尽量引用相关时间点，写成 [mm:ss] 或 [h:mm:ss] 的格式，方便用户点击跳转。\n'
        '只根据字幕' + ('和画面记录' if fb else '') + '回答；字幕可能有个别识别错字，请结合上下文和术语表理解；字幕里没有的信息要说明是你自己的补充。\n\n'
        + (fb + '\n\n' if fb else '') +
        '===== 字幕开始 =====\n' + transcript_text(doc) + '\n===== 字幕结束 ====='
    )


DEFAULT_TEMPLATE = """请整理这节课的「课程重点」。它会放在 Markdown 笔记的上半部分（下半部分是完整字幕），以后用于主题知识库和老师 AI 分身的蒸馏，所以必须忠实、准确、完整、可追溯。

整理方式：
- 按老师这节课实际讲解的知识体系和逻辑框架来组织，不要套用固定栏目。先看清老师是怎么搭这节课的（比如先讲概念、再讲规则、再举例，或者围绕几个问题层层推进），就用这个结构来写；标题和层级由内容决定。
- 为了把逻辑关系讲清楚，可以调整讲解顺序、把分散在不同时间讲的同一件事合并到一起；合并时每一处的原意分别保留、分别标时间，不能拼成老师没说过的新判断。
- 只写老师讲过的内容。不补充老师没讲的知识，不替老师推理结论，不评价对错，不加自己的建议。
- 宁可多写，不要漏写。凡是有知识意义的内容都要有着落：概念、定义、规则、条件、方法、举例、类比、列举的具体事物、数字、对错误做法的纠正、反复强调的话。
- 老师自述一律保留，不评价、不核实、不删除，每条前面写「老师自述：」。包括：个人经历、从业和研究年限、教过多少学生、出书和出版社、「原创」之类的声明、对自己水平的评价、对弟子或师门的称呼。老师对市场、对别人的看法不是自述，不加这个标签。
- 课程安排也按老师自述处理，同样保留、写「老师自述：」：哪些内容老师说放到后面的课、线下课或专栏里讲，这节课为后面哪些内容做了铺垫。
- 老师给出的定义、口诀、规则、条件、数字和阈值要照录；能体现老师观点和表达习惯的关键原话，用引号原样保留，引号里的断句按意思断清楚。
- 不要写「这说明……」「核心是……」「本质上……」「换句话说……」「老师真正想表达的是……」这类你自己的归纳。确实需要说明来源情况时（比如原话前后不一致、话没说完），单独写成「整理者注：……」，整理者注里不能补充知识。
- 画面：老师指着图讲的地方，写成「老师结合走势举例」这类说法，不要写「图示中」「画面显示」；画面说明只用来决定在哪里插图，不要把它写成老师讲的内容。只有能看出老师口述和课件不一致时，才两边分别保留，写成「老师口述：……」「课件写的是：……」，再加一行「整理者注：口述与课件不一致」，不替老师判断哪个对；没有不一致时不要写「老师口述：」。
- 插图：非必要不插图，笔记要尽量小。只有文字确实说不清的（手绘曲线的形状、K 线上标注的具体位置、复杂的结构关系图）才插——在对应要点后面另起一行写 `![[画面 mm:ss]]`，时间必须是「画面（可插图）」里列出的；整节课最多 3 张，没有必要就一张都不插。课件文字、板书文字、列表、简单流程，一律用文字写清楚，不插图。
- 每个要点后面标注出处时间，写成 [mm:ss] 或 [h:mm:ss]；合并了多处内容时，把几个时间都标上。
- 术语一律用术语表里的写法。字幕可能有识别错字，按上下文理解；拿不准的地方标注「（存疑）」。老师提到但没讲明的（没给定义、没说条件、话没说完、说以后再讲），在那句后面标「（未讲明）」，只标这一次，不要再另写「本节未讲明」；不补写、不推测。
- 格式：不要写总标题，直接用一两句话说明这节课讲了什么，然后用「##」开始各部分，下面可以用「###」；不要开场白和结束语。"""


def summary_messages(doc, teacher, template, boundary=''):
    ask = (template or DEFAULT_TEMPLATE).strip()
    if boundary:
        ask += '\n\n来源情况（软件检查得到）：' + boundary + '\n请在最后用一行「整理者注：」如实说明，不要补写缺失或没说完的内容。'
    return [{'role': 'system', 'content': system_prompt(doc, teacher)},
            {'role': 'user', 'content': ask}]


# ------------------------------------------------------------------ 新术语 ----
TERMS_SYSTEM = (
    '你在帮用户逐课积累一份课程专用术语表，用作语音识别的热词和 AI 校对的参考。从这节课里找两类东西：\n'
    '1. 新术语：老师在这门课里使用的专有名词——理论和概念的名称、口诀、形态和工具的名字、老师自创的说法、对师门的称呼等。'
    '不要大众通用词（趋势、价格、止损、均线这类，除非老师给了特殊的叫法），不要普通口语，不要术语表里已经有的。\n'
    '2. 识别错误：只从「校对改动」里找——语音识别把术语识别成了同音、近音的别的字（例如把「峰级」识别成「风机」）。'
    '不要普通错别字、标点、语气词、增删字；正确写法应当是术语表里已有的术语或这次的新术语；错写写成字幕里实际出现的样子，可以比改动的字多带一两个字，让它成为完整的词。\n'
    '只记录名称，不写定义。拿不准的不要列。每条写出处时间 mm:ss 和老师原话片段（40 字以内，照抄字幕）。\n'
    '只输出 JSON 对象：{"新术语": [{"术语": "", "时间": "mm:ss", "原话": ""}], "识别错误": [{"错写": "", "正确": "", "时间": "mm:ss", "原话": ""}]}；'
    '没有就输出空列表。'
)


def fix_spans(fixes, limit=300):
    """字幕改动（AI 校对、画面复核、手动修改）→ 每处换掉的几个字：[{t, wrong, right, before, after, by}]。
    只看「替换」：拆分、合并、删字、加字不算识别错误。"""
    out = []
    for f in fixes or []:
        a, b = str(f.get('before') or ''), str(f.get('after') or '')
        if not a or not b or a == b:
            continue
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            wrong, right = a[i1:i2].strip(), b[j1:j2].strip()
            if op == 'replace' and wrong and right and len(wrong) <= 8 and len(right) <= 8 and re.search(r'\w', right):
                out.append({'t': float(f.get('t') or 0), 'wrong': wrong, 'right': right, 'before': a, 'after': b, 'by': f.get('by') or 'ai'})
    return out[:limit]


def terms_messages(doc, teacher):
    now = {str(s.get('text') or '') for s in doc.get('segments') or []}
    spans = fix_spans([f for f in doc.get('fixes') or [] if f.get('after') in now])  # 撤销掉的改动不算
    by = {'manual': '人工改', 'frames': '看画面改', 'ai': 'AI 改'}
    changes = '\n'.join(f'[{fmt(x["t"])}] 「{x["wrong"]}」→「{x["right"]}」（{by.get(x["by"], "AI 改")}）　原句：{x["before"][:80]}　改后：{x["after"][:80]}'
                         for x in spans) or '（这节课没有校对改动）'
    return [{'role': 'system', 'content': TERMS_SYSTEM},
            {'role': 'user', 'content': f'课程标题：{doc.get("title") or ""}\n' + teacher_block(teacher, with_notes=False) +
             '\n\n===== 校对改动（原识别 → 改正后）=====\n' + changes +
             '\n\n===== 完整字幕 =====\n' + transcript_text(doc)[:80000]}]


def _secs(v):
    m = re.match(r'^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?$', str(v or '').strip())
    if not m:
        return 0.0
    a, b, c = m.groups()
    return float(int(a) * 3600 + int(b) * 60 + int(c)) if c is not None else float(int(a) * 60 + int(b))


def known_terms(teacher):
    """术语表里已有的：(术语集合, 已有的错写集合)。"""
    words, hints, forced = parse_terms((teacher or {}).get('terms'))
    known = set(words)
    wrongs = set()
    for w, right in hints + forced:
        wrongs.add(w)
        known.add(re.split(r'[（(]', right)[0].strip())
    return known, wrongs


def suggest_terms(settings, name, doc, teacher):
    """返回 {'terms': [{term, t, quote}], 'errors': [{wrong, right, t, quote}]}，已去掉术语表里有的。"""
    data = _json_from(complete(settings, name, terms_messages(doc, teacher), json_mode=True)) or {}
    known, wrongs = known_terms(teacher)
    terms, errors, seen = [], [], set()
    for x in data.get('新术语') or []:
        w = str((x or {}).get('术语') or '').strip().strip('「」“”"')
        if w and len(w) <= 20 and w not in known and w not in seen:
            seen.add(w)
            terms.append({'term': w, 't': _secs(x.get('时间')), 'quote': str(x.get('原话') or '').strip()[:60]})
    pairs = set()
    for x in data.get('识别错误') or []:
        a = str((x or {}).get('错写') or '').strip().strip('「」“”"')
        b = str((x or {}).get('正确') or '').strip().strip('「」“”"')
        if a and b and a != b and len(a) <= 20 and len(b) <= 20 and a not in wrongs and (a, b) not in pairs:
            pairs.add((a, b))
            errors.append({'wrong': a, 'right': b, 't': _secs(x.get('时间')), 'quote': str(x.get('原话') or '').strip()[:60]})
    return {'terms': terms, 'errors': errors}


# ------------------------------------------------------------------ 查漏补缺 ----
_TS = re.compile(r'\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?')


def cited_times(text):
    """课程重点里标注的所有出处时间（秒）。"""
    out = []
    for m in _TS.finditer(text or ''):
        a, b, c = m.groups()
        out.append(int(a) * 3600 + int(b) * 60 + int(c) if c is not None else int(a) * 60 + int(b))
    return out


def coverage(segments, summary, before=5.0, after=30.0):
    """按课程重点里的出处时间，估算字幕有多少被引用到。
    每个出处时间算覆盖它前 5 秒到后 30 秒；返回 (覆盖比例, 没有被引用的大段 [(开始, 结束, 文字)])。"""
    times = sorted(cited_times(summary))
    segs = [s for s in segments or [] if str(s.get('text') or '').strip()]
    if not segs:
        return 1.0, []
    import bisect

    def covered(s):
        i = bisect.bisect_left(times, s['start'] - after)
        return i < len(times) and times[i] <= s['end'] + before
    total = sum(len(s['text']) for s in segs) or 1
    hit = sum(len(s['text']) for s in segs if covered(s))
    gaps, run = [], []
    for s in segs + [None]:
        if s is not None and not covered(s):
            run.append(s)
            continue
        if run:
            text = ''.join(x['text'] for x in run)
            if run[-1]['end'] - run[0]['start'] >= 20 and len(text) >= 40:
                gaps.append((run[0]['start'], run[-1]['end'], text))
            run = []
    return hit / total, gaps


REVIEW_SYSTEM = (
    '你是课程笔记的查漏员。对照完整字幕，检查「课程重点」有没有漏掉老师讲过的、有知识意义的内容。'
    '要找的包括：概念、定义、规则、条件、方法、举例、类比、列举的具体事物、数字；老师自述（经历、年限、学生数量、出版、原创说法、对自己的评价、对弟子或师门的称呼）；'
    '课程安排（放到后面的课、线下课或专栏讲的内容）和对后面内容的铺垫。'
    '已经写到的（哪怕换了说法）不算漏。口头语、寒暄、重复、没说完又重说的话不算漏。'
    '只写老师讲过的，不补充、不评价；老师自述和课程安排每条前面写「老师自述：」；老师没讲明的标「（未讲明）」，不补写；'
    '口述和课件不一致时两边分别写出，再加「整理者注：口述与课件不一致」；每条标出处时间 [mm:ss]；术语用术语表的写法。'
    '只输出 JSON 对象：{"补充": [{"放在": "课程重点里已有的小标题原文（找不到合适的就写空字符串）", "内容": "- 一条 Markdown 要点 [mm:ss]"}, ...]}；没有漏掉的就输出 {"补充": []}。'
)


def review_messages(doc, teacher, summary, gaps):
    hint = ''
    if gaps:
        hint = ('\n\n软件发现下面这些时间段在课程重点里没有任何出处，请重点检查（不一定都有知识内容）：\n'
                + '\n'.join(f'- [{fmt(a)}–{fmt(b)}] {t[:300]}' for a, b, t in gaps[:30]))
    return [{'role': 'system', 'content': REVIEW_SYSTEM},
            {'role': 'user', 'content': f'课程标题：{doc.get("title") or ""}\n' + teacher_block(teacher) +
             ('\n\n' + frames_block(doc) if doc.get('frames') else '') +
             '\n\n===== 完整字幕 =====\n' + transcript_text(doc) +
             '\n\n===== 课程重点 =====\n' + (summary or '') + hint}]


def _heading_title(line):
    m = re.match(r'^(#{1,6})\s+(.*?)\s*$', line)
    return (len(m.group(1)), m.group(2)) if m else (None, None)


def _norm_title(t):
    return re.sub(r'[\s*_`#：:，,。.、“”"\'（）()\[\]【】]', '', t or '')


def insert_additions(summary, additions):
    """把查漏补上的要点插到对应小标题那一节的末尾；找不到小标题的放到最后「其他补充」。"""
    lines = (summary or '').rstrip().split('\n')
    leftovers = []
    for add in additions:
        text = str(add.get('内容') or add.get('content') or '').strip()
        if not text:
            continue
        if not text.lstrip().startswith(('-', '*', '+', '>', '|')) and not re.match(r'^\s*\d+[.)]', text):
            text = '- ' + text
        target = _norm_title(str(add.get('放在') or add.get('section') or ''))
        idx = None
        if target:
            for i, line in enumerate(lines):
                level, title = _heading_title(line)
                if level and (_norm_title(title) == target or (len(target) >= 4 and target in _norm_title(title))):
                    end = len(lines)
                    for j in range(i + 1, len(lines)):
                        lv, _ = _heading_title(lines[j])
                        if lv and lv <= level:
                            end = j
                            break
                    while end > i + 1 and not lines[end - 1].strip():
                        end -= 1
                    idx = end
                    break
        if idx is None:
            leftovers.append(text)
        else:
            lines[idx:idx] = text.split('\n')
    if leftovers:
        lines += ['', '## 其他补充', ''] + [x for t in leftovers for x in t.split('\n')]
    return '\n'.join(lines).strip()


def review_summary(settings, name, doc, teacher, summary):
    """查漏：返回 (补充后的课程重点, 补了几条, 补之前的覆盖率, 补之后的覆盖率)。"""
    before, gaps = coverage(doc.get('segments'), summary)
    raw = complete(settings, name, review_messages(doc, teacher, summary, gaps), json_mode=True)
    try:
        data = _json_from(raw)
    except ValueError:
        raise LlmError('查漏结果格式不对，请重试或换一个 AI 服务')
    adds = data.get('补充', data.get('additions', [])) if isinstance(data, dict) else []
    adds = [a for a in adds if isinstance(a, dict)]
    out = insert_additions(summary, adds) if adds else summary
    after, _ = coverage(doc.get('segments'), out)
    return out, len(adds), before, after


def _json_from(raw):
    raw = re.sub(r'^\s*```(?:json)?|```\s*$', '', raw.strip()).strip()
    i, j = raw.find('{'), raw.rfind('}')
    return json.loads(raw[i:j + 1] if i >= 0 and j > i else raw)


def _batches(items, size, workers, work, cancel, progress, on_part, label):
    batches = [items[i:i + size] for i in range(0, len(items), size)]
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, b) for b in batches]
        for f in futures:
            part = f.result()
            if cancel.is_set():
                return
            on_part(part)
            done += 1
            progress(done / len(batches), f'{label}… {done}/{len(batches)} 批')


# ------------------------------------------------------------------ 翻译 ----
TRANSLATE_SYSTEM = (
    '你是专业的字幕翻译。把英文字幕翻译成自然、准确的简体中文；专有名词用常见译法，术语表里有的按术语表；'
    '术语第一次出现时可在括号里保留英文。每条单独翻译，不合并、不拆分、不漏条。'
    '只输出 JSON 对象，格式为 {"译文": {"编号": "中文", ...}}，编号与输入一致。'
)


def translate_batch(settings, name, items, title, teacher, context_before=''):
    payload = {str(i): t for i, t in items}
    tb = teacher_block(teacher)
    user = (f'课程标题：{title}\n' + (tb + '\n' if tb else '') +
            (f'前文（仅供参考，不要翻译）：{context_before}\n' if context_before else '') +
            '请翻译下面的字幕（JSON，键是编号）：\n' + json.dumps(payload, ensure_ascii=False))
    msgs = [{'role': 'system', 'content': TRANSLATE_SYSTEM}, {'role': 'user', 'content': user}]
    for _ in range(3):
        try:
            data = _json_from(complete(settings, name, msgs, json_mode=True))
            data = data.get('译文', data) if isinstance(data, dict) else {}
            out = {int(k): str(v).strip() for k, v in data.items() if str(k).isdigit() and str(v).strip()}
            if len(out) >= max(1, int(len(items) * 0.8)):
                return out
        except (ValueError, AttributeError):
            pass
    raise LlmError('翻译结果格式不对，请重试或换一个 AI 服务')


def translate_all(settings, name, segments, title, teacher, cancel, progress, on_part):
    def work(b):
        if cancel.is_set():
            return {}
        first = b[0][0]
        before = ' '.join(t for i, t in segments if first - 3 <= i < first)
        return translate_batch(settings, name, b, title, teacher, before)
    _batches(segments, 30, 3, work, cancel, progress, on_part, '翻译中')


# ------------------------------------------------------------------ 术语校对 ----
CORRECT_SYSTEM = (
    '你是语音识别字幕的校对员。只修正语音识别造成的问题：错别字、同音或近音错词、术语和人名写错、数字写错、明显缺失或错误的标点。'
    '术语表里的词一律用术语表的写法；「常见识别错误」是读音相近、容易识别错的对照，要结合上下文判断确实是识别错了才改，'
    '比如「部分」里的「分」不能因为对照表里有「分 → 峰」就改。'
    '不要改写句子、不要润色、不要删减口语、不要合并或拆分字幕。拿不准的不改。'
    '拿不准、但读起来明显不通或疑似识别错误的句子（比如不成句、像是听错了的词），不要改，列进「存疑」，写明原因和你猜的可能写法，方便人工回听。'
    '只输出 JSON 对象：{"改动": {"编号": "改正后的整句", ...}, "存疑": {"编号": "原因", ...}}，只列出确实改了的句子和确实存疑的句子；都没有就输出 {"改动": {}, "存疑": {}}。'
)


def correct_batch(settings, name, items, title, teacher, before='', after='', visual=''):
    """返回 (改动 {编号: 新句子}, 存疑 {编号: 原因})。visual：同一时间段的画面记录。"""
    payload = {str(i): t for i, t in items}
    user = (f'课程标题：{title}\n' + teacher_block(teacher) + '\n' +
            (f'同一时间段的画面（一句话说明，仅供参考）：\n{visual}\n' if visual else '') +
            (f'前文（仅供参考，不要输出）：{before}\n' if before else '') +
            (f'后文（仅供参考，不要输出）：{after}\n' if after else '') +
            '请校对下面的字幕（JSON，键是编号）：\n' + json.dumps(payload, ensure_ascii=False))
    msgs = [{'role': 'system', 'content': CORRECT_SYSTEM}, {'role': 'user', 'content': user}]  # noqa: E501
    for _ in range(3):
        try:
            data = _json_from(complete(settings, name, msgs, json_mode=True))
            if not isinstance(data, dict):
                continue
            changes = data.get('改动', {} if '存疑' in data else data)
            doubts = data.get('存疑', {})
            src = dict(items)
            fixed = {int(k): str(v).strip() for k, v in (changes if isinstance(changes, dict) else {}).items()
                     if str(k).isdigit() and int(k) in src and str(v).strip() and str(v).strip() != src[int(k)]}
            flagged = {int(k): str(v).strip()[:200] for k, v in (doubts if isinstance(doubts, dict) else {}).items()
                       if str(k).isdigit() and int(k) in src and str(v).strip()}
            return fixed, flagged
        except (ValueError, AttributeError):
            pass
    raise LlmError('校对结果格式不对，请重试或换一个 AI 服务')


def apply_fixes(text, fixes):
    """术语表里标了「=>!」的对照，识别完直接替换。"""
    for wrong, right in fixes:
        if wrong and wrong != right:
            text = text.replace(wrong, right)
    return text


def correct_all(settings, name, segments, title, teacher, cancel, progress, on_part, times=None, frames=None):
    texts = dict(segments)
    times = times or {}
    from .frames import notes_text

    def work(b):
        if cancel.is_set():
            return {}, {}
        first, last = b[0][0], b[-1][0]
        before = ' '.join(texts[i] for i in range(first - 3, first) if i in texts)
        after = ' '.join(texts[i] for i in range(last + 1, last + 3) if i in texts)
        visual = ''
        if frames and first in times and last in times:
            visual = notes_text(frames, times[first] - 20, times[last] + 20, 6000)
        return correct_batch(settings, name, b, title, teacher, before, after, visual)
    _batches(segments, 40, 3, work, cancel, progress, on_part, 'AI 校对中')
