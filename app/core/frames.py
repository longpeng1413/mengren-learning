"""画面：截图、给截图写一句说明、AI 重画看不清的画面（都是用户手动点的）。

2026-11-10 起没有「自动找插图」（以前：每 2 秒取小图、挑候选、AI 筛选、写说明、去重，一键整理会做）和
「用画面复核存疑句」：用户觉得插图自己听课时手动插更好、不受数量限制；AI 看图改字幕效果不好；这两步还占了一节课大半的
ChatGPT 额度。以前挑好的插图（字幕文件里的 frames）照常显示、照常剪藏，课程重点里的 ![[画面 mm:ss]] 不受影响。

- 截图放在缓存里（「缓存/frames」），删了随时可以按时间重新截（grab）。
- 写说明（describe）：画面页「截取当前画面」后、或点「写说明」时，交给「看图」那项 AI 写一句话。
- AI辅助理解图（redraw，2026-10-15，用户定的规则见知识库视频规范第八节）：画面看不清时，AI 依据老师的讲解和画面里能看清的部分重画：
  关系图用 Mermaid 代码，形状图用 SVG。只画老师讲过、画过的，拿不准就不画（保留原截图）。结果记在画面的
  redraw = {kind: 'mermaid'|'svg', code, note}，剪藏时代替截图（doc.place_figures）。"""
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import CACHE_DIR
from .media import MediaError, cache_key, crop_vf, norm_crop, run, tool

DESCRIBE_BATCH = 3
WORKERS = 3


class Cancelled(Exception):
    pass


def frames_dir(path) -> Path:
    return CACHE_DIR / 'frames' / cache_key(path)


def frame_file(path, t, small=False) -> Path:
    return frames_dir(path) / (f'{int(round(float(t) * 1000)):09d}' + ('_s' if small else '') + '.jpg')


def grab(path, t, small=False, crop=None) -> Path:
    """截一张图（已经截过就直接用）。small=True 时截 640 宽的小图，用来筛选。
    crop：用户在「编辑截图」里框的范围（media.norm_crop），从整张截图裁出来另存一张（同一个缓存文件夹，一起按 30 天清理）。"""
    crop = norm_crop(crop) if crop else None
    if crop:
        src = grab(path, t)
        out = src.with_name(src.stem + '_c' + hashlib.sha1(json.dumps(crop).encode()).hexdigest()[:8] + '.jpg')
        if out.is_file() and out.stat().st_size > 0:
            return out
        tmp = out.with_name(out.stem + '.part.jpg')
        r = run([tool('ffmpeg'), '-y', '-v', 'error', '-i', str(src), '-vf', crop_vf(crop), '-q:v', '3', str(tmp)], timeout=60)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise MediaError('裁剪截图失败：' + (r.stderr.strip()[:200] or '未知错误'))
        os.replace(tmp, out)
        return out
    out = frame_file(path, t, small)
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.part.jpg')
    vf = ['-vf', 'scale=640:-2'] if small else []
    r = run([tool('ffmpeg'), '-y', '-v', 'error', '-ss', f'{float(t):.3f}', '-i', path, '-frames:v', '1', *vf, '-q:v', '3', str(tmp)],
            timeout=60)
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MediaError('截图失败：' + (r.stderr.strip()[:200] or '未知错误'))
    os.replace(tmp, out)
    return out


def _fmt(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


def _subs_between(segments, a, b, limit=1500):
    lines = [f'[{_fmt(s["start"])}] {s["text"]}' for s in segments or [] if a - 1 <= float(s['start']) <= b + 1 and s.get('text')]
    return '\n'.join(lines)[:limit]


def _run_batches(batches, work, cancel, progress, label, lo, hi):
    """并发处理几批，按顺序收集结果。"""
    results = [None] * len(batches)
    done = [0]
    lock = threading.Lock()

    def one(i):
        if cancel is not None and cancel.is_set():
            return
        results[i] = work(batches[i])
        with lock:
            done[0] += 1
            progress(lo + (hi - lo) * done[0] / len(batches), f'{label}… {done[0]}/{len(batches)} 批')
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, range(len(batches))))
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    return results


DESCRIBE_PROMPT = (
    '下面是一节视频课程的几张画面（原尺寸），每张前面标了编号和时间，后面附了这段时间老师说的话（语音识别字幕，可能有错字）。\n'
    '课程笔记只插必要的图，帮助理解文字说不清的地方。请对每张：\n'
    '- 「插图」：只有文字确实说不清的（手绘曲线的形状、K 线上标注的具体位置、复杂的结构关系图）才写 true；纯文字课件、列表、简单流程写 false；\n'
    '- 「说明」：一句话说清图上画的是什么，40 字以内，方便放到笔记对应的位置（例如：老师手画的下跌趋势，标出峰和级的位置）；只写看得见的，不解释含义、不补充知识，不要照抄课件上的文字；\n'
    '- 「看不清」：只有「插图」为 true、但画面本身看不清（模糊、被挡住、太小、反光），直接截图放进笔记也看不明白时写 true，否则写 false。\n'
    '只输出 JSON：{"画面": [{"图": 编号, "说明": "……", "插图": true 或 false, "看不清": true 或 false}]}'
)

REDRAW_PROMPT = (
    '下面是一节视频课程里的一张画面（原尺寸），以及这张画面前后老师说的话（语音识别字幕，可能有错字）。'
    '这张画面对理解课程很重要，但画面本身看不清（模糊、被挡住或太小），直接截图放进笔记看不明白。\n'
    '请依据老师的讲解和画面里能看清的部分，重画一张清楚的示意图放进课程笔记，笔记里会标注为「AI辅助理解图（非原画面）」。规则：\n'
    '- 只画老师讲过、画过的内容：可以重画画面里已有的结构，把明确的顺序、层级、位置画清楚；'
    '不得添加老师没讲的规则、条件、因果关系，不把推测画成确定结论；图上的字用老师的说法。\n'
    '- 结构、流程、层级、分类这类关系图，用 Mermaid 代码（flowchart）；走势形状、K 线示意、曲线、位置标注这类形状图，'
    '用 SVG 代码（宽不超过 800，白色背景，线条清楚，文字用中文；不要引用外部图片、字体和链接，不要写脚本）。\n'
    '- 拿不准画面上是什么、或者老师没讲清楚的，不要画，写「画」为 false 和原因（笔记里会继续用原截图）。\n'
    '只输出 JSON：{"画": true, "类型": "mermaid" 或 "svg", "代码": "……", "说明": "一句话说明画的是什么"}；'
    '不画时输出 {"画": false, "原因": "……"}'
)


def _json(raw):
    raw = re.sub(r'^\s*```(?:json)?|```\s*$', '', (raw or '').strip()).strip()
    i, j = raw.find('{'), raw.rfind('}')
    return json.loads(raw[i:j + 1] if i >= 0 and j > i else raw)


def describe(settings, name, path, frames, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0, on_part=None):
    """为每张画面写画面记录。frames: [{t, …}]，返回同样的列表，加上 note、caption、figure。"""
    from . import llm
    batches = [frames[i:i + DESCRIBE_BATCH] for i in range(0, len(frames), DESCRIBE_BATCH)]

    def work(batch):
        parts = [{'type': 'text', 'text': DESCRIBE_PROMPT}]
        for k, f in enumerate(batch, 1):
            parts.append({'type': 'text', 'text': f'\n图{k}（{_fmt(f["t"])}）'})
            parts.append({'type': 'image', 'path': str(grab(path, f['t']))})
            parts.append({'type': 'text', 'text': '这段时间的字幕：\n' + _subs_between(segments, f['t'] - 25, f['t'] + 15, 800)})
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
                items = data.get('画面', []) if isinstance(data, dict) else []
                out = [dict(f) for f in batch]
                for x in items:
                    k = int(x.get('图', 0)) if isinstance(x, dict) else 0
                    if 1 <= k <= len(batch):
                        out[k - 1].update(note=str(x.get('说明') or x.get('记录') or '').strip()[:120], caption='',
                                          figure=bool(x.get('插图')) or bool(out[k - 1].get('manual')))  # 手动截的是用户挑的，默认可插
                        out[k - 1]['unclear'] = bool(x.get('看不清')) and out[k - 1]['figure']
                if on_part:
                    on_part(out)
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('插图说明结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在判断哪些图需要插', lo, hi)
    return [f for r in results for f in (r or [])]


def clean_mermaid(code):
    """AI 给的 Mermaid 代码：去掉 ``` 围栏，只认常见的图类型；不像的返回空。"""
    s = re.sub(r'^\s*```(?:mermaid)?\s*|\s*```\s*$', '', str(code or '').strip()).strip()
    if not s or '```' in s or len(s) > 6000:
        return ''
    head = s.split('\n', 1)[0].strip().lower()
    kinds = ('flowchart', 'graph', 'sequencediagram', 'classdiagram', 'statediagram', 'mindmap', 'timeline', 'quadrantchart', 'block-beta')
    return s if head.startswith(kinds) else ''


def _one_redraw(x):
    """AI 的回答 → {kind, code, note}；不画或代码不对返回 None。"""
    from .doc import sanitize_svg
    if not isinstance(x, dict) or not x.get('画'):
        return None
    kind = str(x.get('类型') or '').strip().lower()
    code = clean_mermaid(x.get('代码')) if kind == 'mermaid' else sanitize_svg(x.get('代码')) if kind == 'svg' else ''
    return {'kind': kind, 'code': code, 'note': str(x.get('说明') or '').strip()[:80]} if code else None


def redraw(settings, name, path, frames, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0, on_part=None):
    """画面看不清的，让 AI 依据讲解和画面重画（AI辅助理解图）。返回同样的列表：画了的加上 redraw，
    AI 说不画（拿不准）的不加，剪藏时继续用原截图。"""
    from . import llm

    def work(batch):
        f = batch[0]
        parts = [{'type': 'text', 'text': REDRAW_PROMPT},
                 {'type': 'text', 'text': f'\n画面（{_fmt(f["t"])}）' + (f'，AI 之前写的说明：{f["note"]}' if f.get('note') else '')},
                 {'type': 'image', 'path': str(grab(path, f['t']))},
                 {'type': 'text', 'text': '这张画面前后老师说的话：\n' + _subs_between(segments, f['t'] - 40, f['t'] + 40, 2000)}]
        out = dict(f)
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
            except (ValueError, TypeError, AttributeError):
                continue
            got = _one_redraw(data)
            if got:
                out['redraw'] = got
            break
        if on_part:
            on_part([out])
        return [out]
    results = _run_batches([[f] for f in frames], work, cancel, progress, 'AI 正在重画看不清的画面', lo, hi)
    return [f for r in results for f in (r or [])]


def nearest(frames, t, within=3.0):
    best = min(frames or [], key=lambda f: abs(float(f.get('t', 0)) - t), default=None)
    return best if best is not None and abs(float(best.get('t', 0)) - t) <= within else None


def notes_text(frames, a=None, b=None, limit=20000):
    """画面记录写成文字，给 AI 参考。a/b 限定时间范围。"""
    lines = []
    for f in sorted(frames or [], key=lambda f: float(f.get('t', 0))):
        t = float(f.get('t', 0))
        if (a is not None and t < a) or (b is not None and t > b) or not (f.get('note') or f.get('caption')):
            continue
        tag = '（可插图，原画面看不清，已有 AI辅助理解图）' if f.get('figure') and f.get('redraw') else '（可插图）' if f.get('figure') else ''
        line = f'[{_fmt(t)}]' + tag + ' ' + (f.get('note') or '').replace('\n', ' ')
        if f.get('caption'):
            line += f'　画面字幕：「{f["caption"]}」'
        lines.append(line)
    return '\n'.join(lines)[:limit]
