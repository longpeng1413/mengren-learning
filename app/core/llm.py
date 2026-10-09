"""AI：翻译、术语校对、课程重点和对话。
支持的服务：
- OpenAI 兼容接口：OpenAI、DeepSeek、豆包大模型（火山方舟）、硅基流动；
- ChatGPT 账号登录（Codex 接口）；
- Claude（Anthropic 官方 SDK）；
- Gemini（Google 的 REST 接口）。"""
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
        body = {'model': model, 'messages': messages, 'stream': True}
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
                'input': [{'type': 'message', 'role': m['role'],
                           'content': [{'type': 'output_text' if m['role'] == 'assistant' else 'input_text', 'text': m['content']}]}
                          for m in rest]}
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
        kwargs = dict(model=model, max_tokens=32000, messages=rest)
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
        body = {'contents': [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]} for m in rest]}
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
        m = re.match(r'^(.+?)\s*(?:=>|->|→|＝>)\s*(!|！)?\s*(.+)$', line)
        if m:
            wrongs = [w.strip() for w in re.split(r'[,，、/|]', m.group(1)) if w.strip()]
            right = m.group(3).strip()
            (forced if m.group(2) else hints).extend((w, right) for w in wrongs if w != right)
            words.append(right)
            continue
        words += [w.strip() for w in re.split(r'[,，、;；]', line) if w.strip()]
    seen, out = set(), []
    for w in words:
        if w not in seen and len(w) <= 30:
            seen.add(w)
            out.append(w)
    return out, hints, forced


def teacher_block(teacher, with_notes=True):
    if not teacher:
        return ''
    words, hints, forced = parse_terms(teacher.get('terms'))
    parts = [f"老师：{teacher.get('name', '')}"]
    if teacher.get('about'):
        parts.append('老师和课程背景：' + teacher['about'])
    if words:
        parts.append('专用术语表（必须用这些写法）：' + '、'.join(words))
    if hints or forced:
        parts.append('常见识别错误（只作参考，必须结合上下文判断，不能看到相似读音就替换）：'
                     + '；'.join(f'{a} → {b}' for a, b in hints + forced))
    if with_notes and teacher.get('notes'):
        parts.append('术语说明 / 参考资料（用来理解课程用语，不要把这里的内容当成老师在这节课里讲过的话）：\n'
                     + teacher['notes'][:12000])
    return '\n'.join(parts)


def system_prompt(doc, teacher=None):
    title = doc.get('title') or '视频'
    tb = teacher_block(teacher)
    return (
        '你是用户的学习助手，帮助用户学习一节视频课程。请用中文回答。\n'
        f'课程标题：{title}\n' + (tb + '\n' if tb else '') +
        '下面是带时间戳的完整字幕。回答时尽量引用相关时间点，写成 [mm:ss] 或 [h:mm:ss] 的格式，方便用户点击跳转。\n'
        '只根据字幕内容回答；字幕可能有个别识别错字，请结合上下文和术语表理解；字幕里没有的信息要说明是你自己的补充。\n\n'
        '===== 字幕开始 =====\n' + transcript_text(doc) + '\n===== 字幕结束 ====='
    )


DEFAULT_TEMPLATE = """请整理这节课的「课程重点」。它会放在 Markdown 笔记的上半部分（下半部分是完整字幕），以后用于主题知识库和老师 AI 分身的蒸馏，所以必须忠实、准确、可追溯。

整理方式：
- 按老师这节课实际讲解的知识体系和逻辑框架来组织，不要套用固定栏目。先看清老师是怎么搭这节课的（比如先讲概念、再讲规则、再举例，或者围绕几个问题层层推进），就用这个结构来写；标题和层级由内容决定。
- 为了把逻辑关系讲清楚，可以调整讲解顺序、把分散在不同时间讲的同一件事合并到一起。
- 只写老师讲过的内容。不补充老师没讲的知识，不替老师推理结论，不评价对错，不加自己的建议。
- 老师给出的定义、口诀、规则、条件、数字和阈值要照录；能体现老师观点和表达习惯的关键原话，用引号原样保留。
- 每个要点后面标注出处时间，写成 [mm:ss] 或 [h:mm:ss]；合并了多处内容时，把几个时间都标上。
- 术语一律用术语表里的写法。字幕可能有识别错字，按上下文理解；拿不准的地方标注「（存疑）」，老师没讲清楚的标注「（未讲明）」，不要自己补全。
- 直接从第一个标题开始输出，不要开场白和结束语。最前面用一两句话说明这节课讲了什么。"""


def summary_messages(doc, teacher, template):
    return [{'role': 'system', 'content': system_prompt(doc, teacher)},
            {'role': 'user', 'content': (template or DEFAULT_TEMPLATE).strip()}]


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
    '只输出 JSON 对象：{"改动": {"编号": "改正后的整句", ...}}，只列出确实改了的句子；没有改动就输出 {"改动": {}}。'
)


def correct_batch(settings, name, items, title, teacher, before='', after=''):
    payload = {str(i): t for i, t in items}
    user = (f'课程标题：{title}\n' + teacher_block(teacher) + '\n' +
            (f'前文（仅供参考，不要输出）：{before}\n' if before else '') +
            (f'后文（仅供参考，不要输出）：{after}\n' if after else '') +
            '请校对下面的字幕（JSON，键是编号）：\n' + json.dumps(payload, ensure_ascii=False))
    msgs = [{'role': 'system', 'content': CORRECT_SYSTEM}, {'role': 'user', 'content': user}]  # noqa: E501
    for _ in range(3):
        try:
            data = _json_from(complete(settings, name, msgs, json_mode=True))
            data = data.get('改动', data) if isinstance(data, dict) else {}
            src = dict(items)
            return {int(k): str(v).strip() for k, v in data.items()
                    if str(k).isdigit() and int(k) in src and str(v).strip() and str(v).strip() != src[int(k)]}
        except (ValueError, AttributeError):
            pass
    raise LlmError('校对结果格式不对，请重试或换一个 AI 服务')


def apply_fixes(text, fixes):
    """术语表里标了「=>!」的对照，识别完直接替换。"""
    for wrong, right in fixes:
        if wrong and wrong != right:
            text = text.replace(wrong, right)
    return text


def correct_all(settings, name, segments, title, teacher, cancel, progress, on_part):
    texts = dict(segments)

    def work(b):
        if cancel.is_set():
            return {}
        first, last = b[0][0], b[-1][0]
        before = ' '.join(texts[i] for i in range(first - 3, first) if i in texts)
        after = ' '.join(texts[i] for i in range(last + 1, last + 3) if i in texts)
        return correct_batch(settings, name, b, title, teacher, before, after)
    _batches(segments, 40, 3, work, cancel, progress, on_part, 'AI 校对中')
