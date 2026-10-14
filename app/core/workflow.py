"""任务层：Agent（命令行 cli.py，以后 MCP）调用猛人学习的入口。

做的事和界面上的「一键整理」一样，步骤和顺序是用户定的：
    生成字幕（已有就跳过）→ AI 校对 → 找关键帧（挑插图）→ 用画面复核存疑句 → 翻译（只有英文才翻）
    → 课程重点 → 查漏补缺 → 新术语（只放进「待勾选」，不写术语表）
**不剪藏**：用户在软件里人工校对、打开「已校对」后自己剪藏。书籍：分章 → 逐章整理（含查漏、回页面核对）→ 全书总览，同样不剪藏。

每一步的做法：读磁盘上最新的字幕文件 → 调核心模块（asr / llm / frames / book，和界面用的是同一批函数）
→ 再读一次（这期间用户可能在界面里改过）→ 按和界面相同的规则合并 → 保存。
一步做到一半失败，这一步什么都不写（不会出现「报错了，结果却出现了一半」）。
已经做完的步骤再运行时跳过（看字幕文件里的结果和 steps 记录），除非 force。

过渡期注意：界面那边同样的合并规则还在 app/web/app.js（startTranscribe、startCorrect、findFrames、recheckDoubts、
doSummary、findTerms、bookOrganize…），改规则要两边一起改；之后会把界面也改成调用这里。
"""
import os
import re
import time
from pathlib import Path

from . import asr, book, config, doc as docs, frames, library, llm, media, tasks
from .book import LIB_EXT
from .log import log
from .media import AUDIO_EXT
from .tasks import COMPLETED, FAILED, CANCELLED, SKIPPED, WorkError

VIDEO_STAGES = ['transcribe', 'correct', 'frames', 'recheck', 'translate', 'summary', 'review', 'terms']
BOOK_STAGES = ['chapters', 'organize', 'overview']
LABELS = {'transcribe': '生成字幕', 'correct': 'AI 校对', 'frames': '找关键帧', 'recheck': '用画面复核存疑句',
          'translate': '翻译', 'summary': '课程重点', 'review': '查漏补缺', 'terms': '新术语',
          'chapters': '分章', 'organize': '逐章整理', 'overview': '全书总览'}
SOFT = {'recheck', 'review'}  # 失败了不影响后面的步骤（界面的一键整理也是这样）
RETRIES = 2                   # 网络、接口临时出错时，这一步自动重做几次
RETRY_WAIT = (5, 20)
VERSIONS_KEEP = 5


# ------------------------------------------------------------------ 编号 ----
def video_id(path):
    return docs.media_key(path)[:12]


def course_id(folder):
    return docs.media_key(folder)[:12] if folder else ''


def kind_of(path):
    ext = Path(path).suffix.lower()
    return 'book' if ext in LIB_EXT else ('audio' if ext in AUDIO_EXT else 'video')


# ------------------------------------------------------------- 找视频 ----
def _state(it, kind):
    if kind == 'book':
        b = it.get('book') or {}
        if it.get('clipped'):
            return '已剪藏'
        if b.get('total') and b.get('done') == b.get('total'):
            return '已整理'
        return f'整理了 {b.get("done", 0)}/{b.get("total", 0)} 章' if b.get('done') else '未处理'
    if it.get('clipped'):
        return '已剪藏'
    if it.get('reviewed'):
        return '已校对'
    if it.get('summary'):
        return '已整理'
    return '有字幕' if it.get('subs') else '未处理'


def _todo(it, kind):
    if kind == 'book':
        b = it.get('book') or {}
        return not b.get('total') or b.get('done', 0) < b.get('total', 0)
    return not it.get('summary')


def library_items():
    """媒体库里所有的视频、音频和书（和软件左边列表一样的顺序）。index 是在这个课程文件夹里的第几个（从 1 开始）。"""
    out = []
    for g in library.view():
        for n, it in enumerate(g.get('items') or [], 1):
            kind = kind_of(it['path'])
            out.append({
                'video_id': video_id(it['path']), 'course_id': course_id(g.get('path')), 'course': g.get('name') or '',
                'course_path': g.get('path') or '', 'sub': it.get('sub') or '', 'index': n, 'name': it.get('name') or '',
                'path': it['path'], 'kind': kind, 'duration': round(float(it.get('duration') or 0), 1),
                'state': _state(it, kind), 'todo': _todo(it, kind), 'subtitles': it.get('subs', 0),
                'summary': bool(it.get('summary')), 'reviewed': bool(it.get('reviewed')), 'clipped': bool(it.get('clipped')),
                'teacher': it.get('teacher') or '',
            })
    return out


def _since(value):
    """'today' / 'yesterday' / 'YYYY-MM-DD' → 时间戳"""
    v = str(value).strip().lower()
    day = time.strftime('%Y-%m-%d')
    if v in ('today', '今天'):
        v = day
    elif v in ('yesterday', '昨天'):
        v = time.strftime('%Y-%m-%d', time.localtime(time.time() - 86400))
    try:
        return time.mktime(time.strptime(v, '%Y-%m-%d'))
    except ValueError:
        raise WorkError('not_supported', f'看不懂这个日期：{value}（写 today、yesterday 或 2026-10-14）')


def find(query=None, course=None, index=None, todo=False, since=None, kind=None):
    """按课程（文件夹名或子文件夹名，包含即可）、关键词（文件名或路径）、第几个、是否还没整理、是否某天以后新增 查找。"""
    items = library_items()
    if course:
        c = str(course).lower()
        items = [x for x in items if c == x['course_id'] or c in x['course'].lower() or c in x['sub'].lower()]
    if query:
        q = str(query).lower()
        items = [x for x in items if q == x['video_id'] or q in x['name'].lower() or q in x['path'].lower()]
    if kind:
        items = [x for x in items if x['kind'] == kind or (kind == 'video' and x['kind'] == 'audio')]
    if index:
        index = int(index)
        if course and len({x['course_id'] for x in items}) == 1 and not any(x['sub'] for x in items):
            items = [x for x in items if x['index'] == index]  # 整个课程文件夹里的第几个
        else:
            items = items[index - 1:index] if 0 < index <= len(items) else []  # 筛选后的第几个
    if since:
        t0 = _since(since)
        items = [x for x in items if _ctime(x['path']) >= t0]
    if todo:
        items = [x for x in items if x['todo']]
    return items


def _ctime(path):
    try:
        st = os.stat(path)
        return max(st.st_ctime, st.st_mtime)  # Windows 上 st_ctime 是文件创建（下载）时间
    except OSError:
        return 0


def allowed(path):
    """Agent 只处理媒体库里的文件（库里的文件夹及子文件夹、单独添加的文件）。"""
    lib = config.load_library()
    p = os.path.normcase(os.path.abspath(path))
    for f in lib['folders']:
        base = os.path.normcase(os.path.abspath(f.get('path') or '')).rstrip('\\/') + os.sep
        if f.get('path') and p.startswith(base):
            return True
    return any(os.path.normcase(os.path.abspath(x)) == p for x in lib['files'])


def resolve(target):
    """文件路径或 video_id → 媒体库里的文件路径。"""
    t = str(target or '').strip().strip('"')
    if not t:
        raise WorkError('not_found', '没有指定视频')
    if os.path.isfile(t):
        path = os.path.abspath(t)
        if not allowed(path):
            raise WorkError('not_in_library', f'这个文件不在猛人学习的媒体库里：{path}。请先在软件左边的媒体库添加它所在的文件夹')
        return path
    hits = [x for x in library_items() if x['video_id'] == t.lower()]
    if hits:
        return hits[0]['path']
    if os.path.sep in t or '/' in t:
        raise WorkError('not_found', '找不到这个文件：' + t)
    raise WorkError('not_found', f'媒体库里没有编号为 {t} 的视频')


# --------------------------------------------------------------- 读和存 ----
def _load(path):
    """读字幕文件（没有就新建），补上界面打开时会补的东西（时长、文件夹绑定的老师）。返回 (doc, rev)。"""
    if kind_of(path) == 'book':
        info = book.read(path)
        d = docs.load(path)
        if not d or d.get('kind') != 'book':
            d = book.new_doc(path, info)
        if not d.get('teacher'):
            d['teacher'] = library.folder_teacher(path)
        return d, int(d.get('rev') or 0)
    d = docs.load(path)
    if d is None:
        d = docs.new(path, media.probe(path))
    d.setdefault('segments', [])
    if not d.get('teacher'):
        d['teacher'] = library.folder_teacher(path)
    return d, int(d.get('rev') or 0)


def _save(path, doc, rev):
    """保存。这期间界面又改过（rev 变了）就重读再合并一次。"""
    r = docs.save(path, doc, base_rev=rev)
    if r.get('conflict'):
        raise _Retry()
    return r


class _Retry(Exception):
    """保存时发现字幕文件刚被别的程序改过：重读最新的再合并。"""


def _apply(path, change, tries=4):
    """读最新的字幕文件 → change(doc) 修改 → 保存；保存冲突就重来。change 返回的值原样返回。"""
    for _ in range(tries):
        d, rev = _load(path)
        out = change(d)
        try:
            _save(path, d, rev)
            return out
        except _Retry:
            time.sleep(0.3)
    raise WorkError('busy', '字幕文件一直在被别的程序修改，保存不进去，请稍后再试')


def _steps(d):
    if not isinstance(d.get('steps'), dict):
        d['steps'] = {}
    return d['steps']


def _seg_at(d, start, text=None):
    """按开始时间找回同一句（期间用户可能在界面里增删过句子）；给了 text 时要求原文也没变。"""
    for i, s in enumerate(d.get('segments') or []):
        if abs(float(s.get('start', 0)) - float(start)) < 0.02 and (text is None or s.get('text') == text):
            return i, s
    return None, None


def record_fix(d, start, before, after, by):
    """记下这句字幕从什么改成了什么（和界面 app.js 的 recordFix 相同），用来积累术语表。"""
    if not before or before == after:
        return
    lst = d.setdefault('fixes', [])
    old = next((f for f in lst if abs(float(f.get('t', 0)) - start) < 0.05 and f.get('after') == before), None)
    if old:
        old['after'] = after
        if by == 'manual':
            old['by'] = 'manual'
    else:
        lst.append({'t': start, 'before': before, 'after': after, 'by': by})
    if len(lst) > 1500:
        del lst[:len(lst) - 1500]


def push_version(d, reason):
    """改字幕之前存一份历史版本（最多 5 份，和界面的「历史版本」是同一份），不满意可以在软件里恢复。"""
    segs = d.get('segments') or []
    if not segs:
        return
    v = {'time': time.strftime('%Y/%m/%d %H:%M:%S'), 'reason': reason, 'provider': d.get('provider') or '',
         'corrected': bool(d.get('corrected')), 'count': len(segs), 'segments': docs.clean_segments(segs)}
    d['versions'] = [v] + list(d.get('versions') or [])[:VERSIONS_KEEP - 1]


def doubt_of(d, seg):
    return next((x for x in d.get('doubts') or [] if abs(float(x.get('t', 0)) - float(seg.get('start', 0))) < 0.02), None)


def vision_provider(s, need=True):
    """「画面识别」用哪个 AI（要能看图）。need=False 时没设好返回空字符串。界面的 bridge._vision 也用这个。"""
    name = s.get('vision') or ''
    if name and name in config.CHAT_PROVIDERS:
        try:
            if name in llm.VISION_NOTE:
                raise llm.LlmError(llm.VISION_NOTE[name] + '，请到「设置 → 任务用哪个 AI」给「画面识别」换一个能看图的服务')
            llm.check(s, name)
            return name
        except llm.LlmError:
            if need:
                raise
    if need:
        raise llm.LlmError('还没有设置「画面识别」用哪个 AI，请到「设置 → 任务用哪个 AI」选择')
    return ''


def _model_of(s, name):
    p = s['providers'].get(name) or {}
    return f'{config.PROVIDER_NAMES.get(name, name)} · {p.get("chat_model") or ""}'.rstrip(' ·')


# ------------------------------------------------------------- 各个步骤 ----
class _Skip(Exception):
    """这一步不需要做，原因写在 message 里。"""


class Ctx:
    def __init__(self, path, task, opts):
        self.path = path
        self.task = task
        self.opts = opts
        self.force = bool(opts.get('force'))
        self.settings = config.load()
        self.cancel = task.cancel
        self.lo, self.hi = 0.0, 1.0

    def doc(self):
        return _load(self.path)[0]

    def teacher(self, d):
        return config.get_teacher(d.get('teacher'))

    def progress(self, f=None, m=None, extra=None):
        self.task.progress(self.lo + (self.hi - self.lo) * float(f or 0) if f is not None else None, m)

    def check_cancel(self):
        if self.cancel.is_set():
            raise WorkError('cancelled', '已取消')


def _lang(ctx, d):
    t = ctx.teacher(d) or {}
    lang = ctx.opts.get('lang') or d.get('lang') or t.get('lang') or ctx.settings.get('last_lang') or 'zh'
    return lang if lang in ('zh', 'en') else 'zh'


def st_transcribe(ctx):
    d = ctx.doc()
    if (ctx.opts.get('model') or 'cloud') == 'local':
        raise WorkError('not_supported', '本地模型识别还没有接入，现在只能用云端识别（model=cloud）')
    if d.get('segments') and not ctx.force:
        raise _Skip('已经有字幕（要重新识别：--steps transcribe --force）')
    s = ctx.settings
    lang = _lang(ctx, d)
    provider = ctx.opts.get('provider') or (s['asr_en'] if lang == 'en' else s['asr_zh'])
    if provider not in ('openai', 'siliconflow', 'doubao'):
        raise WorkError('config', '未知的识别服务：' + str(provider))
    pending = d.get('pending_task') if provider == 'doubao' and isinstance(d.get('pending_task'), dict) else None
    if pending and pending.get('model'):
        s['providers']['doubao']['model'] = pending['model']  # 接着取上次提交的豆包标准版 / 闲时版任务
    asr.check_ready(s, provider)
    info = media.probe(ctx.path)
    teacher = ctx.teacher(d)
    model = s['providers']['doubao']['model'] if provider == 'doubao' else s['providers'][provider].get('asr_model')
    ctx.task.stage('transcribe').update(model=f'{provider} · {model}', params={'lang': lang, 'provider': provider, 'resume': bool(pending)})
    cues = []

    def on_progress(f, m=None, extra=None):
        ctx.progress(f, m)
        if extra and extra.get('task_id') and (not pending or pending.get('task_id') != extra['task_id']):
            # 豆包标准版 / 闲时版：任务一提交就记下来，程序中途退出，下次运行接着取结果（不用重新上传、重新付费）
            def keep(doc):
                doc['pending_task'] = dict(extra, lang=lang, provider=provider)
            _apply(ctx.path, keep)

    asr.recognise(ctx.path, info['duration'], provider, lang, s, ctx.cancel, on_progress, cues.extend, pending, teacher,
                  d.get('title') or Path(ctx.path).stem)
    ctx.check_cancel()

    def apply(doc):
        if doc.get('segments'):
            push_version(doc, '重新识别前')
        doc['segments'] = [{'start': c['start'], 'end': c['end'], 'text': c['text'], 'tr': ''} for c in cues]
        doc['lang'] = lang
        doc['provider'] = f'{provider}' + (f' · {model}' if model else '')
        doc['corrected'] = False
        doc['reviewed'] = False
        doc['duration'] = info['duration'] or doc.get('duration', 0)
        doc.pop('pending_task', None)
        doc['steps'] = {'transcribe': tasks.now()}  # 字幕换了，后面的步骤都要重做
    _apply(ctx.path, apply)
    return {'segments': len(cues), 'lang': lang}


def _need_segments(d):
    if not d.get('segments'):
        raise WorkError('no_subtitles', '没有字幕，先要生成字幕')


def st_correct(ctx):
    d = ctx.doc()
    _need_segments(d)
    teacher = ctx.teacher(d)
    if not teacher:
        raise _Skip('这节课没有指定老师（术语表），和一键整理一样不做 AI 校对')
    if d.get('corrected') and not ctx.force:
        raise _Skip('已经校对过')
    s = ctx.settings
    name = s['correct']
    llm.check(s, name)
    ctx.task.stage('correct').update(model=_model_of(s, name))
    segs = d['segments']
    items = [(i, x['text']) for i, x in enumerate(segs) if str(x.get('text') or '').strip()]
    sent = {i: (segs[i]['start'], segs[i]['text']) for i, _ in items}
    times = {i: segs[i]['start'] for i, _ in items}
    shots = [f for f in d.get('frames') or [] if f.get('note')]
    fixed, flagged = {}, {}

    def on_part(part):
        a, b = part
        fixed.update(a)
        flagged.update(b)
    llm.correct_all(s, name, items, d.get('title') or '', teacher, ctx.cancel, ctx.progress, on_part, times, shots)
    ctx.check_cancel()

    def apply(doc):
        push_version(doc, 'AI 校对前')
        n = 0
        for i, new in fixed.items():
            start, before = sent[i]
            _, seg = _seg_at(doc, start, before)
            if seg is None:
                continue  # 这句在校对期间被用户改过，以用户的为准
            record_fix(doc, start, before, new, 'ai')
            seg['text'] = new
            n += 1
        doubts = []
        for i, reason in flagged.items():
            start, before = sent[i]
            _, seg = _seg_at(doc, start)
            if seg is not None:
                doubts.append({'t': seg['start'], 'reason': reason, 'text': seg['text']})
        doc['doubts'] = doubts
        doc['corrected'] = True
        _steps(doc).pop('recheck', None)
        _steps(doc)['correct'] = tasks.now()
        return n, len(doubts)
    n, nd = _apply(ctx.path, apply)
    return {'fixed': n, 'doubts': nd}


def st_frames(ctx):
    d = ctx.doc()
    _need_segments(d)
    info = media.probe(ctx.path)
    if not info.get('has_video'):
        raise _Skip('音频文件，没有画面')
    s = ctx.settings
    name = vision_provider(s, need=False)
    if not name:
        raise _Skip('还没有设置能看图的「画面识别」AI，和一键整理一样跳过')
    if (d.get('frames') or 'frames' in _steps(d)) and not ctx.force:
        raise _Skip('已经挑过插图')
    ctx.task.stage('frames').update(model=_model_of(s, name))
    try:
        got = frames.extract(s, name, ctx.path, float(d.get('duration') or info['duration'] or 0), d['segments'], ctx.cancel, ctx.progress)
    except frames.Cancelled:
        raise WorkError('cancelled', '已取消')
    ctx.check_cancel()

    def apply(doc):
        manual = [f for f in doc.get('frames') or [] if f.get('manual')]  # 手动截取的保留，自动挑的替换
        doc['frames'] = sorted(manual + list(got), key=lambda f: float(f.get('t', 0)))
        _steps(doc)['frames'] = tasks.now()
    _apply(ctx.path, apply)
    return {'figures': len(got)}


def st_recheck(ctx):
    d = ctx.doc()
    _need_segments(d)
    info = media.probe(ctx.path)
    if not info.get('has_video'):
        raise _Skip('音频文件，没有画面')
    s = ctx.settings
    name = vision_provider(s, need=False)
    if not name:
        raise _Skip('还没有设置能看图的「画面识别」AI，和一键整理一样跳过')
    if 'recheck' in _steps(d) and not ctx.force:
        raise _Skip('已经复核过')
    segs = d['segments']
    idx = [i for i, x in enumerate(segs) if doubt_of(d, x)]
    if not idx:
        _apply(ctx.path, lambda doc: _steps(doc).__setitem__('recheck', tasks.now()))
        raise _Skip('没有存疑句')
    ctx.task.stage('recheck').update(model=_model_of(s, name))
    items = [{'i': i, 'start': segs[i]['start'], 'end': segs[i]['end'], 'text': segs[i]['text'], 'reason': doubt_of(d, segs[i]).get('reason', ''),
              'before': ' '.join(x['text'] for x in segs[max(0, i - 2):i]), 'after': ' '.join(x['text'] for x in segs[i + 1:i + 3])}
             for i in idx][:60]
    sent = {it['i']: (it['start'], it['text']) for it in items}
    shots = [f for f in d.get('frames') or [] if f.get('note')]
    try:
        res = frames.recheck_doubts(s, name, ctx.path, items, shots, ctx.cancel, ctx.progress)
    except frames.Cancelled:
        raise WorkError('cancelled', '已取消')
    ctx.check_cancel()

    def apply(doc):
        fixed = confirmed = 0
        for x in res:
            start, before = sent.get(x['i'], (None, None))
            if start is None:
                continue
            _, seg = _seg_at(doc, start, before)
            if seg is None:
                continue
            dd = doubt_of(doc, seg)
            if 'text' in x:
                if x['text'] != seg['text']:
                    record_fix(doc, start, seg['text'], x['text'], 'frames')
                    seg['text'] = x['text']
                    fixed += 1
                if dd:
                    doc['doubts'] = [y for y in doc['doubts'] if y is not dd]
                confirmed += 1
            elif dd and x.get('doubt'):
                dd['reason'] = x['doubt']
        _steps(doc)['recheck'] = tasks.now()
        return fixed, confirmed, sum(1 for y in doc.get('segments') or [] if doubt_of(doc, y))
    fixed, confirmed, left = _apply(ctx.path, apply)
    return {'checked': len(items), 'confirmed': confirmed, 'fixed': fixed, 'left': left}


def st_translate(ctx):
    d = ctx.doc()
    _need_segments(d)
    if (d.get('lang') or _lang(ctx, d)) != 'en':
        raise _Skip('中文字幕，不用翻译')
    segs = d['segments']
    todo = [(i, x['text']) for i, x in enumerate(segs) if x['text'].strip() and (ctx.force or not str(x.get('tr') or '').strip())]
    if not todo:
        raise _Skip('已经都有译文')
    s = ctx.settings
    name = s['translate']
    llm.check(s, name)
    ctx.task.stage('translate').update(model=_model_of(s, name))
    sent = {i: (segs[i]['start'], segs[i]['text']) for i, _ in todo}
    got = {}
    llm.translate_all(s, name, todo, d.get('title') or '', ctx.teacher(d), ctx.cancel, ctx.progress, got.update)
    ctx.check_cancel()
    if len(got) < len(todo) * 0.8:
        raise WorkError('bad_output', f'只翻译出 {len(got)}/{len(todo)} 句', True)

    def apply(doc):
        n = 0
        for i, zh in got.items():
            if i not in sent:
                continue
            _, seg = _seg_at(doc, *sent[i])
            if seg is not None:
                seg['tr'] = zh
                n += 1
        return n
    n = _apply(ctx.path, apply)
    config.update({'show_tr': True})  # 和界面一样：翻译完打开「显示译文」，软件里中英同时显示
    return {'translated': n}


def st_summary(ctx):
    d = ctx.doc()
    _need_segments(d)
    if (d.get('summary') or '').strip() and not ctx.force:
        raise _Skip('已经有课程重点')
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    ctx.task.stage('summary').update(model=_model_of(s, name))
    teacher = ctx.teacher(d)
    template = (teacher or {}).get('template') or s.get('summary_template') or llm.DEFAULT_TEMPLATE
    boundary = docs.source_boundary(d, media.tail_problem(ctx.path))
    messages = llm.summary_messages(d, teacher, template, boundary)
    ctx.progress(0.05, 'AI 正在整理课程重点…')
    parts = []
    for piece in llm.stream(s, name, messages, ctx.cancel):
        parts.append(piece)
        if len(parts) % 40 == 0:
            ctx.progress(min(0.9, 0.05 + len(''.join(parts)) / 20000), 'AI 正在整理课程重点…')
    ctx.check_cancel()
    text = ''.join(parts).strip()
    if not text:
        raise WorkError('bad_output', 'AI 没有写出课程重点', True)

    def apply(doc):
        doc['summary'] = text
        _steps(doc).pop('review', None)
        _steps(doc)['summary'] = tasks.now()
    _apply(ctx.path, apply)
    return {'chars': len(text)}


def st_review(ctx):
    d = ctx.doc()
    summary = (d.get('summary') or '').strip()
    if not summary:
        raise _Skip('还没有课程重点')
    if not ctx.settings.get('auto_review', True):
        raise _Skip('设置里关掉了「自动查漏补缺」')
    if 'review' in _steps(d) and not ctx.force:
        raise _Skip('已经查漏过')
    if 'summary' not in _steps(d) and not ctx.force:
        raise _Skip('课程重点是以前在软件里生成的（当时已经自动查漏）')
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    ctx.task.stage('review').update(model=_model_of(s, name))
    ctx.progress(0.05, 'AI 正在对照字幕查漏…')
    out, added, before, after = llm.review_summary(s, name, d, ctx.teacher(d), summary)
    ctx.check_cancel()

    def apply(doc):
        if (doc.get('summary') or '').strip() == summary:  # 期间用户改过课程重点就不动
            if added:
                doc['summary'] = out
            _steps(doc)['review'] = tasks.now()
            return True
        return False
    applied = _apply(ctx.path, apply)
    return {'added': added if applied else 0, 'coverage_before': round(before, 3), 'coverage_after': round(after, 3)}


def st_terms(ctx):
    d = ctx.doc()
    _need_segments(d)
    teacher = ctx.teacher(d)
    if not teacher:
        raise _Skip('这节课没有指定老师（术语表），和一键整理一样不找新术语')
    st = _steps(d)
    if not ctx.force and ('terms' in st or d.get('terms_pending') or (not st and (d.get('summary') or '').strip())):
        raise _Skip('已经找过新术语' + ('（等你在软件里勾选）' if d.get('terms_pending') else ''))
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    ctx.task.stage('terms').update(model=_model_of(s, name))
    ctx.progress(0.05, 'AI 正在找这节课的新术语…')
    res = llm.suggest_terms(s, name, d, teacher)
    ctx.check_cancel()

    def apply(doc):
        doc['terms_pending'] = res if (res['terms'] or res['errors']) else None  # 只放进「待勾选」，用户在软件里确认后才写术语表
        _steps(doc)['terms'] = tasks.now()
    _apply(ctx.path, apply)
    return {'terms': len(res['terms']), 'errors': len(res['errors'])}


# ------------------------------------------------------------------ 书籍 ----
def _paged(chs):
    return bool(chs) and all(re.match(r'^第 \d+–\d+ 页$|^全文$', c.get('title') or '') for c in chs)


def st_chapters(ctx):
    d = ctx.doc()
    info = book.read(ctx.path)
    chs = d.get('chapters') or []
    if any((c.get('md') or '').strip() for c in chs):
        raise _Skip('已经有整理好的章节，保持现在的分章')
    if chs and not (_paged(chs) and not info.get('toc')):
        raise _Skip(f'用软件认出的 {len(chs)} 章')
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    ctx.task.stage('chapters').update(model=_model_of(s, name))
    ctx.progress(0.1, 'AI 正在读目录…')  # 没认出章节时，等于在软件里点「AI 读目录分章」
    if 'shift' in d:
        info = dict(info, shift=d['shift'])
    got = book.ai_chapters(s, name, info)
    if not got:
        raise _Skip('AI 没有从目录里认出章节，按页数分段整理')

    def apply(doc):
        if any((c.get('md') or '').strip() for c in doc.get('chapters') or []):
            return 0
        doc['chapters'] = [dict(c, md='') for c in got]
        return len(got)
    return {'chapters': _apply(ctx.path, apply)}


def st_organize(ctx):
    d = ctx.doc()
    chs = d.get('chapters') or []
    todo = [i for i, c in enumerate(chs) if ctx.force or not (c.get('md') or '').strip()]
    if not chs:
        raise WorkError('no_subtitles', '这本书没有分出章节')
    if not todo:
        raise _Skip('每一章都整理过了')
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    vision = name not in llm.VISION_NOTE
    ctx.task.stage('organize').update(model=_model_of(s, name), params={'chapters': len(todo), 'vision': vision})
    info = book.read(ctx.path)
    if 'shift' in d:
        info = dict(info, shift=d['shift'])
    teacher = ctx.teacher(d)
    done = adds = 0
    for k, i in enumerate(todo):
        ctx.check_cancel()
        d = ctx.doc()  # 每章都用最新的（用户可能在软件里改了章名、页码）
        c = d['chapters'][i]
        lo, hi = k / len(todo), (k + 1) / len(todo)

        def prog(f, m=None):
            ctx.progress(lo + (hi - lo) * float(f or 0), (m or '') + (f'（第 {k + 1}/{len(todo)} 章）' if len(todo) > 1 else ''))
        md = book.organize_chapter(s, name, ctx.path, info, c, d, teacher, vision, prog, ctx.cancel, d['chapters'])
        prog(0.92, f'正在查漏「{c["title"]}」…')
        md, added, missing = book.review_chapter(s, name, info, c, md, d, teacher)
        prog(0.96, f'正在回页面核对「{c["title"]}」里需要确认的地方…')
        md, verified = book.verify_doubts(s, name, ctx.path, info, c, md, d, teacher, vision)
        ctx.check_cancel()
        key = (c.get('title'), c.get('start'), c.get('end'))

        def apply(doc):
            cc = (doc.get('chapters') or [])[i] if i < len(doc.get('chapters') or []) else None
            if not cc or (cc.get('title'), cc.get('start'), cc.get('end')) != key:
                return False  # 这期间分章被改了，这一章不写
            cc['md'] = md
            cc['updated'] = time.strftime('%Y/%m/%d %H:%M:%S')
            cc['note'] = (f'查漏：{missing} 页没被引用，补了 {added} 条' if missing else '每一页都有引用') + (f'；回页面核对确认了 {verified} 处' if verified else '')
            return True
        if _apply(ctx.path, apply):  # 每章整理完就保存：中途失败，下次从没整理的那章接着做
            done += 1
            adds += added
    return {'chapters': done, 'added': adds}


def st_overview(ctx):
    d = ctx.doc()
    chs = d.get('chapters') or []
    if not any((c.get('md') or '').strip() for c in chs):
        raise WorkError('no_subtitles', '还没有整理好的章节')
    if not all((c.get('md') or '').strip() for c in chs):
        raise WorkError('no_subtitles', '还有章节没整理完，整理完才写全书总览', True)
    if (d.get('overview') or '').strip() and not ctx.force:
        raise _Skip('已经有全书总览')
    s = ctx.settings
    name = s['chat']
    llm.check(s, name)
    ctx.task.stage('overview').update(model=_model_of(s, name))
    ctx.progress(0.1, 'AI 正在写全书总览…')
    files = book.chapter_files(chs)
    info = book.read(ctx.path)
    shift = d.get('shift', info.get('shift'))
    for c in chs:
        c['range'] = book.range_ref(c['start'], c['end'], shift)
    text = book.overview(s, name, d, chs, files)

    def apply(doc):
        doc['overview'] = text
    _apply(ctx.path, apply)
    return {'chars': len(text or '')}


RUNNERS = {'transcribe': st_transcribe, 'correct': st_correct, 'frames': st_frames, 'recheck': st_recheck,
           'translate': st_translate, 'summary': st_summary, 'review': st_review, 'terms': st_terms,
           'chapters': st_chapters, 'organize': st_organize, 'overview': st_overview}


# ------------------------------------------------------------- 整条流程 ----
def stages_for(path, steps=None, skip=None):
    """这个文件要走哪些步骤（按固定顺序）。steps 只做这几步，skip 跳过这几步。"""
    allv = BOOK_STAGES if kind_of(path) == 'book' else VIDEO_STAGES
    wanted = [x for x in allv if (not steps or x in steps) and not (skip and x in skip)]
    bad = [x for x in (list(steps or []) + list(skip or [])) if x not in allv]
    if bad:
        raise WorkError('not_supported', f'不认识的步骤：{"、".join(bad)}。{"书籍" if kind_of(path) == "book" else "视频"}可以用：{"、".join(allv)}')
    return wanted


def process(target, steps=None, skip=None, redo=(), lang=None, provider=None, model='cloud',
            on_progress=None, cancel=None, caller='cli'):
    """整理一节课（或一本书）。返回任务记录（dict）。不抛出异常：失败写在记录里。
    redo：这几步已经做过也重做（其余做过的照样跳过）。"""
    try:
        path = resolve(target)
    except WorkError as e:
        return _failed_lookup(target, e)
    kind = 'book' if kind_of(path) == 'book' else 'video'
    try:
        stages = stages_for(path, steps, skip)
    except WorkError as e:
        return _failed_lookup(target, e)
    forced = tuple(x for x in (redo or ()) if x in stages)
    opts = {'steps': stages, 'redo': list(forced), 'lang': lang, 'provider': provider,
            'model': model or 'cloud', 'caller': caller}
    task = tasks.Task(kind, path, video_id(path), Path(path).stem, dict(opts), stages, LABELS)
    if cancel is not None:
        task.cancel = cancel
    key = docs.media_key(path)
    try:
        tasks.claim(key, task.id)
    except WorkError as e:
        task.begin()
        task.finish(FAILED, tasks.error_dict(e))
        return task.data
    task.begin()
    task.data['outputs']['subtitle_file'] = str(docs.sidecar(path))
    if on_progress:
        orig = task.progress

        def report(fraction=None, message=None):
            orig(fraction, message)
            on_progress(task.data)
        task.progress = report
    status, err = COMPLETED, None
    try:
        for n, name in enumerate(stages):
            if task.cancel.is_set():
                status, err = CANCELLED, {'error_code': 'cancelled', 'retryable': False, 'message': '已取消'}
                break
            ctx = Ctx(path, task, dict(opts, force=name in forced))
            ctx.lo, ctx.hi = n / len(stages), (n + 1) / len(stages)
            ok, e = _run_stage(ctx, name)
            if ok:
                continue
            if e['error_code'] == 'cancelled':
                status, err = CANCELLED, e
                break
            if name in SOFT:
                task.data['warnings'].append(f'{LABELS[name]}没有完成：{e["message"]}（不影响后面的步骤）')
                continue
            status, err = FAILED, dict(e, stage=name)
            break
        for st in task.data['stages']:  # 没轮到的步骤
            if st['status'] == 'pending':
                st['status'] = SKIPPED if status == COMPLETED else 'pending'
    except Exception as e:  # 兜底：程序本身出错
        log.exception('Agent 任务 %s 出错', task.id)
        status, err = FAILED, tasks.error_dict(e)
    finally:
        tasks.release(key, task.id)
    task.data['outputs']['state'] = summarize(path)
    task.finish(status, err)
    return task.data


def _run_stage(ctx, name):
    """跑一步；网络、接口临时出错自动重做（最多 RETRIES 次）。返回 (成功或跳过, 错误)。"""
    task = ctx.task
    st = task.start_stage(name)
    tries = 0
    while True:
        try:
            result = RUNNERS[name](ctx)
            task.end_stage(name, COMPLETED, result=result)
            return True, None
        except _Skip as e:
            task.end_stage(name, SKIPPED, reason=str(e))
            return True, None
        except Exception as e:  # noqa: BLE001
            err = tasks.error_dict(e)
            if ctx.cancel.is_set() or err['error_code'] == 'cancelled':
                err = {'error_code': 'cancelled', 'retryable': False, 'message': '已取消'}
                task.end_stage(name, CANCELLED, error=err)
                return False, err
            if err['error_code'] == 'internal':
                log.exception('Agent 任务 %s：「%s」出错', task.id, LABELS[name])
            if err['retryable'] and tries < RETRIES:
                wait = RETRY_WAIT[min(tries, len(RETRY_WAIT) - 1)]
                tries += 1
                st['retries'] = tries
                task.progress(None, f'{LABELS[name]}出错（{err["message"][:80]}），{wait} 秒后第 {tries} 次重试…')
                log.warning('Agent 任务 %s：「%s」出错，%d 秒后重试：%s', task.id, LABELS[name], wait, err['message'])
                if ctx.cancel.wait(wait):
                    continue
                ctx.settings = config.load()
                continue
            task.end_stage(name, FAILED, error=err)
            return False, err


def _failed_lookup(target, e):
    err = tasks.error_dict(e)
    return {'task_id': None, 'path': str(target), 'video_id': None, 'status': FAILED, 'error': err, 'stages': [], 'warnings': []}


def run_step(target, step, force=False, **kw):
    """只做一步（例如只校对）。force 时这一步已经做过也重做。"""
    return process(target, steps=[step], redo=(step,) if force else (), **kw)


def resume(task_id, on_progress=None, cancel=None):
    """接着做一个没做完的任务：已经完成的步骤会自动跳过（判断依据在字幕文件里），不会重复花钱。"""
    d = tasks.load(task_id)
    if not d:
        return {'task_id': task_id, 'status': FAILED, 'error': {'error_code': 'not_found', 'retryable': False, 'message': '没有这个任务'},
                'stages': [], 'warnings': []}
    o = d.get('options') or {}
    target = d.get('path')
    try:
        target = resolve(target)
    except WorkError:
        pass
    return process(target, steps=o.get('steps'), lang=o.get('lang'), provider=o.get('provider'),
                   model=o.get('model') or 'cloud', on_progress=on_progress, cancel=cancel, caller='resume')


# ------------------------------------------------------------------ 状态 ----
def summarize(path):
    """这节课现在的整理情况（给 Agent 看，也写进任务记录）。"""
    try:
        d = docs.load(path)
    except Exception:  # noqa: BLE001
        d = None
    if not d:
        return {'exists': False}
    if d.get('kind') == 'book':
        chs = d.get('chapters') or []
        return {'exists': True, 'kind': 'book', 'chapters': len(chs), 'organized': sum(1 for c in chs if (c.get('md') or '').strip()),
                'overview': bool((d.get('overview') or '').strip()), 'clipped': bool(d.get('clip'))}
    segs = d.get('segments') or []
    st = d.get('steps') if isinstance(d.get('steps'), dict) else {}
    tp = d.get('terms_pending') or {}
    return {
        'exists': True, 'kind': 'video', 'segments': len(segs), 'lang': d.get('lang'), 'provider': d.get('provider'),
        'corrected': bool(d.get('corrected')), 'doubts': sum(1 for x in segs if doubt_of(d, x)),
        'figures': sum(1 for f in d.get('frames') or [] if f.get('figure')),
        'translated': sum(1 for x in segs if str(x.get('tr') or '').strip()),
        'summary_chars': len((d.get('summary') or '').strip()), 'reviewed_by_ai': 'review' in st,
        'terms_pending': {'terms': len(tp.get('terms') or []), 'errors': len(tp.get('errors') or [])} if tp else None,
        'human_reviewed': bool(d.get('reviewed')), 'clipped': bool(d.get('clip')), 'steps': st, 'updated': d.get('updated'),
    }


def info(target):
    """一节课的详细情况（相当于在软件里打开它，但不播放、不改任何东西）。"""
    path = resolve(target)
    out = {'video_id': video_id(path), 'path': path, 'title': Path(path).stem, 'kind': kind_of(path),
           'subtitle_file': str(docs.sidecar(path)), 'state': summarize(path)}
    busy = tasks.busy(docs.media_key(path))
    out['busy'] = busy
    if kind_of(path) == 'book':
        b = book.read(path)
        out['metadata'] = {'pages': len(b['pages']), 'kind': b['kind'], 'chars': b['chars'], 'toc': len(b.get('toc') or [])}
    else:
        m = media.probe(path)
        out['metadata'] = m
        tail = media.tail_problem(path)
        if tail:
            out['warning'] = tail
    d = docs.load(path) or {}
    out['teacher'] = d.get('teacher') or library.folder_teacher(path)
    out['processed'] = bool(out['state'].get('summary_chars') or out['state'].get('overview'))
    out['next_steps'] = [s for s in stages_for(path)]
    out['recent_tasks'] = [{k: t.get(k) for k in ('task_id', 'status', 'created', 'finished')} for t in tasks.recent(5, path)]
    return out
