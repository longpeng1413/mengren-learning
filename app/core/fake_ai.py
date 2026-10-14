"""界面自测用的假识别和假 AI（只在 main.py --selftest … --fake-ai 时装上，正常使用不会碰到）。
不联网、不花钱，固定返回，用来测界面上的整理按钮和一键整理走后台任务层的整条流程。"""
import json
import time

from . import asr, book, frames, llm


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


def stream(settings, name, messages, cancel=None, json_mode=False):
    for piece in ['这节课（假 AI）讲风险。\n\n', '## 一、风险\n\n', '- 截断亏损 [00:10]\n', '- 让利润奔跑 [00:15]\n']:
        if cancel is not None and cancel.is_set():
            return
        time.sleep(0.1)
        yield piece


def extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    progress(0.5, '正在分析画面…（假 AI）')
    return [{'t': 3.0, 'why': '老师说请看', 'note': '假 AI 挑的图', 'figure': True}]


def recheck_doubts(settings, name, path, items, frames_list, cancel=None, progress=lambda f, m=None: None, on_part=None):
    return [{'i': it['i'], 'text': it['text'] + '（画面确认）'} for it in items]


def install():
    asr.recognise = recognise
    llm.complete = complete
    llm.stream = stream
    frames.extract = extract
    frames.recheck_doubts = recheck_doubts
    book.organize_chapter = lambda s, n, path, info, c, meta, *a, **k: f'## 一、本章主题\n\n{c["title"]}（假 AI 整理）'
    book.review_chapter = lambda s, n, info, c, md, meta, teacher=None: (md, 0, 0)
    book.verify_doubts = lambda s, n, path, info, c, md, meta, teacher=None, vision=True: (md, 0)
    book.overview = lambda s, n, meta, chs, files: '## 全书总览\n\n假 AI 写的总览。'
