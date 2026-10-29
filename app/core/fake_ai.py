"""界面自测用的假识别和假 AI（只在 main.py --selftest … --fake-ai 时装上，正常使用不会碰到）。
不联网、不花钱，固定返回，用来测界面上的整理按钮和一键整理走后台任务层的整条流程。"""
import json
import time

from . import asr, book, distill, frames, llm


def _payload(messages):
    """校对 / 翻译时用户消息的最后一行是 {"编号": "原文"} 的 JSON。"""
    try:
        return json.loads(str(messages[-1]['content']).rsplit('\n', 1)[-1])
    except (ValueError, KeyError, IndexError):
        return {}


def recognise(path, duration, provider, language, settings, cancel, progress, on_cues, teacher=None, title=''):
    cues = []
    texts = (['Hello and welcome.', 'This lesson is about risk.', 'Cut losses short.', 'Let profits run.'] if language == 'en'
             else ['大家好，今天讲峰级。', '风级的入门很重要。', '这是一个关于峰级的讲论文。', '这就是三段结构。'])
    for k, t in enumerate(texts):
        if cancel.is_set():
            raise asr.Cancelled('已取消')
        time.sleep(0.15)
        c = {'start': k * 5.0, 'end': k * 5.0 + 4.0, 'text': t}
        cues.append(c)
        on_cues([c])
        progress((k + 1) / len(texts), f'识别中… {k + 1}/{len(texts)}')
    return cues


def complete(settings, name, messages, json_mode=False):
    system = str(messages[0].get('content')) if messages else ''
    if system == llm.CORRECT_SYSTEM:
        keys = list(_payload(messages))
        fixed = {keys[0]: '（AI 校对过）' + _payload(messages)[keys[0]]} if keys else {}
        doubts = {keys[1]: '疑似识别错误（假 AI）'} if len(keys) > 1 else {}
        return json.dumps({'改动': fixed, '存疑': doubts}, ensure_ascii=False)
    if system == llm.TRANSLATE_SYSTEM:
        return json.dumps({'译文': {k: '中文：' + v for k, v in _payload(messages).items()}}, ensure_ascii=False)
    if system == llm.REVIEW_SYSTEM:
        return json.dumps({'补充': [{'放在': '假 AI 的小节', '内容': '- 查漏补上的一条 [00:05]'}]}, ensure_ascii=False)
    if system == llm.TERMS_SYSTEM:
        return json.dumps({'新术语': [{'术语': '假AI新术语', '时间': '00:01', '原话': '假 AI'}], '识别错误': []}, ensure_ascii=False)
    return '{}'


def _distill_reply(system):
    if system == distill.EXTRACT_SYSTEM:
        return json.dumps({'说明': '假 AI 提取的一节课。', '观点': [
            {'内容': '风险要先算好（假 AI）。', '条件': '—', '出处': '00:02–00:05', '类型': '观点', '说得很满': False},
            {'内容': '「截断亏损」。', '条件': '未讲明', '出处': '00:10', '类型': '方法', '说得很满': True}],
            '自述': [{'内容': '我是假老师。', '出处': '00:01'}], '术语': [{'术语': '峰级', '意思': '假 AI 的解释', '出处': '00:03'}],
            '实践': [{'内容': '先算风险', '依据': [1]}], '存疑': ['00:08 假 AI 标的存疑']}, ensure_ascii=False)
    if system == distill.GROUP_SYSTEM:
        return '### 这几节的主线\n\n- 假 AI 小结（S01-01）'
    if system == distill.MERGE_SYSTEM:
        return '## 一、一句话\n\n假 AI 合并的课程蒸馏（S01-01、S01-02）。\n\n## 三、核心观点（合并后）\n\n1. **风险先算**（S01-01）'
    if system == distill.PERSONA_SYSTEM:
        return '## 二、他是谁\n\n- 假老师［S01-A1］\n\n## 八、他没讲、或明确不碰的\n\n- 止盈（未讲明）'
    from . import roundtable
    if system == roundtable.ROUND_SUMMARY_SYSTEM:
        return '### 共识\n\n- 都说风险要先算（假 AI 总结）\n\n### 分歧和原因\n\n- 没有'
    if system == roundtable.TOPIC_TEACHER_SYSTEM:
        return '### 他的主要观点\n\n- 风险先算（假 AI）'
    if system == roundtable.TOPIC_MERGE_SYSTEM:
        return '## 一、一句话\n\n假 AI 写的主题蒸馏。\n\n## 三、共识\n\n- 风险先算'
    return None


def stream(settings, name, messages, cancel=None, json_mode=False):
    reply = _distill_reply(str(messages[0].get('content')) if messages else '')
    if reply is None and messages and str(messages[0].get('content')).startswith('你是用户读书的助手'):
        reply = '书里说（假 AI）：趋势从三段开始（P2）。'  # 书的 AI 对话
    if reply is not None:
        time.sleep(0.2)
        yield reply
        return
    for piece in ['这节课（假 AI）讲风险。\n\n', '## 一、风险\n\n', '- 截断亏损 [00:10]\n', '- 让利润奔跑 [00:15]\n']:
        if cancel is not None and cancel.is_set():
            return
        time.sleep(0.3)  # 界面测试要来得及在写课程重点时切到别的课
        yield piece


def extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    progress(0.5, '正在分析画面…（假 AI）')
    return [{'t': 3.0, 'why': '老师说请看', 'note': '假 AI 挑的图', 'figure': True, 'unclear': True,
             'redraw': {'kind': 'mermaid', 'code': 'flowchart LR\n  A[破级] --> B[首峰]', 'note': '假 AI 重画的结构'}}]


def redraw(settings, name, path, frames_list, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0, on_part=None):
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="80"><rect width="200" height="80" fill="#fff"/><path d="M10 70 L60 20 L110 50 L190 10" stroke="#000" fill="none"/></svg>'
    return [dict(f, redraw={'kind': 'svg', 'code': svg, 'note': '假 AI 重画的走势'}) for f in frames_list]


def recheck_doubts(settings, name, path, items, frames_list, cancel=None, progress=lambda f, m=None: None, on_part=None):
    return [{'i': it['i'], 'text': it['text'] + '（画面确认）'} for it in items]


def install():
    asr.recognise = recognise
    llm.complete = complete
    llm.stream = stream
    frames.extract = extract
    frames.recheck_doubts = recheck_doubts
    frames.redraw = redraw
    book.organize_chapter = lambda s, n, path, info, c, meta, *a, **k: f'## 一、本章主题\n\n{c["title"]}（假 AI 整理）'
    book.review_chapter = lambda s, n, info, c, md, meta, teacher=None: (md, 0, 0)
    book.verify_doubts = lambda s, n, path, info, c, md, meta, teacher=None, vision=True: (md, 0)
    book.overview = lambda s, n, meta, chs, files, info=None: '## 全书总览\n\n假 AI 写的总览。'
