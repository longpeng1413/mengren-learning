"""关键帧和画面记录：把视频里有知识信息的画面（课件、板书、老师手绘、K 线标注）找出来，
让能看图的 AI 写成文字「画面记录」，供 AI 校对、课程重点和对话参考；重要的图可以插进剪藏笔记。

流程：
1. 候选：每 2 秒取一张很小的灰度图。按 15 秒分一段，每段挑一张「最像这段的背景」的
   （老师走来走去挡住屏幕的那几秒会被避开）；老师说「大家请看」之类的话时也挑一张；去掉几乎一样的。
2. 筛选：候选图缩小后每 8 张一批交给 AI，只留下有知识信息、而且是同一页里最完整的那张。
3. 画面记录：留下的图用原尺寸交给 AI，照录课件文字、板书，区分课件原有元素和老师现场写画的，
   记下画面上的字幕，并说明这张图值不值得插进笔记。
截图放在缓存里（「缓存/frames」），删了随时可以按时间重新截。"""
import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import CACHE_DIR
from .media import MediaError, NO_WINDOW, cache_key, run, tool

W, H = 64, 36
STEP = 2.0            # 每隔几秒取一张小图
WINDOW = 15.0         # 每段几秒挑一张候选
SAME = 2.5            # 两张小图平均差小于这个值（0–255）算重复
MASK_BOTTOM = 0.2     # 比较时不看最下面 20%（烧录字幕一直在变）
CUES = re.compile(r'大家请看|大家看一下|你们看|请看|来看一下|看一下这|看这[里个张幅]|如图|这张图|板书|画一下|写一下|我画')
SCREEN_BATCH = 8
DESCRIBE_BATCH = 3
WORKERS = 3


class Cancelled(Exception):
    pass


def frames_dir(path) -> Path:
    return CACHE_DIR / 'frames' / cache_key(path)


def _samples(path, duration, progress=None, cancel=None):
    """每 STEP 秒一张 64×36 灰度小图，返回 [(时间, bytes)]。"""
    args = [tool('ffmpeg'), '-v', 'error', '-i', path, '-an', '-vf', f'fps=1/{STEP},scale={W}:{H},format=gray',
            '-f', 'rawvideo', '-']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    out, size, k = [], W * H, 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Cancelled()
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            out.append((k * STEP, buf))
            k += 1
            if progress and duration and k % 15 == 0:
                progress(min(1.0, k * STEP / duration))
    finally:
        try:
            proc.stdout.close()
        except OSError:
            pass
        proc.wait()
    return out


_ROWS = int(H * (1 - MASK_BOTTOM))


def _diff(a: bytes, b: bytes) -> float:
    n = W * _ROWS
    return sum(abs(x - y) for x, y in zip(a[:n], b[:n])) / n


def _median(imgs):
    cols = zip(*imgs)
    return bytes(sorted(c)[len(c) // 2] for c in cols)


def candidate_times(samples, segments=None):
    """每 WINDOW 秒挑一张最接近这段「背景」的；加上老师说「请看」的时间；去重。返回 [(时间, 原因)]。"""
    if not samples:
        return []
    per = max(1, int(WINDOW / STEP))
    picked = []
    for i in range(0, len(samples), per):
        group = samples[i:i + per]
        bg = _median([g[1] for g in group])
        best = min(group, key=lambda g: _diff(g[1], bg))
        picked.append((best[0], best[1], '定时'))
    for seg in segments or []:
        if CUES.search(str(seg.get('text') or '')):
            t = float(seg.get('end') or seg.get('start') or 0) + 1.5
            i = min(len(samples) - 1, max(0, int(round(t / STEP))))
            picked.append((samples[i][0], samples[i][1], '老师说请看'))
    picked.sort(key=lambda p: p[0])
    out = []
    for t, img, why in picked:
        if out and (abs(t - out[-1][0]) < STEP * 2 or _diff(img, out[-1][1]) < SAME):
            if why == '老师说请看':
                out[-1] = (t, img, why)
            continue
        out.append((t, img, why))
    return [(round(t, 2), why) for t, _, why in out]


def frame_file(path, t, small=False) -> Path:
    return frames_dir(path) / (f'{int(round(float(t) * 1000)):09d}' + ('_s' if small else '') + '.jpg')


def grab(path, t, small=False) -> Path:
    """截一张图（已经截过就直接用）。small=True 时截 640 宽的小图，用来筛选。"""
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


SCREEN_PROMPT = (
    '下面是一节视频课程里按时间顺序截的几张画面（缩小过），每张前面标了编号和时间，后面附了这段时间的字幕。\n'
    '请挑出值得留下的画面：画面里有知识信息——课件 / PPT 上的文字和图、板书、图表、K 线、老师现场手写手画的内容或标注。\n'
    '规则：\n'
    '- 同一页课件或同一块板书，只留内容最完整、被人挡得最少的一张；如果后面的图比前面的写得更多、画得更多，留后面的；\n'
    '- 课程标题页、章节标题页要留（上面的课程名、章节名会在画面记录里照录）；\n'
    '- 只有老师在讲话、屏幕内容和别的图一样、片头片尾装饰、屏幕看不清的，不留；\n'
    '- 宁可少留，不要留重复的。\n'
    '只输出 JSON：{"保留": [{"图": 编号, "原因": "一句话，比如：新的一页课件 / 板书比前一张多了交易曲线"}]}；一张都不值得留就输出 {"保留": []}。'
)

DESCRIBE_PROMPT = (
    '下面是一节视频课程的几张关键画面（原尺寸），每张前面标了编号和时间，后面附了这段时间老师说的话（语音识别字幕，可能有错字）。\n'
    '请为每张写「画面记录」，给以后整理课程笔记的人当证据用：\n'
    '- 照录课件 / PPT 上的标题、文字、数字、表格；\n'
    '- 照录老师现场写的板书，描述老师手画的图形、箭头、圈、曲线、K 线标注和它们的位置关系；要分清哪些是课件原有的、哪些是老师现场写画的；\n'
    '- 画面底部如果有字幕，单独抄在「字幕」里；\n'
    '- 只写看得见的，不推测含义、不解释、不补充知识；看不清的字写「（看不清）」，拿不准的写「（疑似：…）」；\n'
    '- 「插图」：默认 false。只有文字确实说不清的（手绘曲线的形状、K 线上标注的具体位置、复杂的结构关系图）才写 true；课件文字、板书文字、列表、简单流程都能用文字写清楚，写 false。\n'
    '只输出 JSON：{"画面": [{"图": 编号, "记录": "……", "字幕": "画面上的字幕，没有就空", "插图": true 或 false}]}'
)


def _json(raw):
    raw = re.sub(r'^\s*```(?:json)?|```\s*$', '', (raw or '').strip()).strip()
    i, j = raw.find('{'), raw.rfind('}')
    return json.loads(raw[i:j + 1] if i >= 0 and j > i else raw)


def screen(settings, name, path, cands, segments, cancel=None, progress=lambda f, m=None: None, lo=0.0, hi=1.0):
    """让 AI 从候选里挑出有知识信息的画面。返回 [{t, why}]。"""
    from . import llm
    batches = [cands[i:i + SCREEN_BATCH] for i in range(0, len(cands), SCREEN_BATCH)]

    def work(batch):
        parts = [{'type': 'text', 'text': SCREEN_PROMPT}]
        for k, (t, _why) in enumerate(batch, 1):
            parts.append({'type': 'text', 'text': f'\n图{k}（{_fmt(t)}）'})
            parts.append({'type': 'image', 'path': str(grab(path, t, small=True))})
        parts.append({'type': 'text', 'text': '\n这段时间的字幕：\n' + _subs_between(segments, batch[0][0] - 10, batch[-1][0] + 10)})
        for _ in range(2):
            try:
                data = _json(llm.complete(settings, name, [{'role': 'user', 'content': parts}], json_mode=True))
                keep = data.get('保留', []) if isinstance(data, dict) else []
                out = []
                for x in keep:
                    k = int(x.get('图', 0)) if isinstance(x, dict) else 0
                    if 1 <= k <= len(batch):
                        out.append({'t': batch[k - 1][0], 'why': str(x.get('原因') or '').strip()[:80]})
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('画面筛选结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在挑选有内容的画面', lo, hi)
    seen, out = set(), []
    for r in results:
        for f in r or []:
            if f['t'] not in seen:
                seen.add(f['t'])
                out.append(f)
    return sorted(out, key=lambda f: f['t'])


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
                        out[k - 1].update(note=str(x.get('记录') or '').strip(), caption=str(x.get('字幕') or '').strip(),
                                          figure=bool(x.get('插图')))
                if on_part:
                    on_part(out)
                return out
            except (ValueError, TypeError, AttributeError):
                continue
        raise llm.LlmError('画面记录结果格式不对，请重试或换一个 AI 服务')
    results = _run_batches(batches, work, cancel, progress, 'AI 正在写画面记录', lo, hi)
    return [f for r in results for f in (r or [])]


def extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    """完整流程：候选 → AI 筛选 → 画面记录。name 为空时只截候选图（没有能看图的 AI）。返回 [{t, why, note, caption, figure}]。"""
    progress(0.0, '正在分析画面…')
    samples = _samples(path, duration, lambda f: progress(0.15 * f, '正在分析画面…'), cancel)
    if not samples:
        raise MediaError('读不出画面，这个文件可能没有视频')
    cands = candidate_times(samples, segments)
    for n, (t, _) in enumerate(cands, 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        grab(path, t, small=True)
        if n % 5 == 0:
            progress(0.15 + 0.15 * n / len(cands), f'正在截候选画面… {n}/{len(cands)}')
    if not name:
        return [{'t': t, 'why': why} for t, why in cands]
    chosen = screen(settings, name, path, cands, segments, cancel, progress, 0.3, 0.6)
    if not chosen:
        return []
    return describe(settings, name, path, chosen, segments, cancel, progress, 0.6, 1.0, on_part)


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
        line = f'[{_fmt(t)}]' + ('（可插图）' if f.get('figure') else '') + ' ' + (f.get('note') or '').replace('\n', ' ')
        if f.get('caption'):
            line += f'　画面字幕：「{f["caption"]}」'
        lines.append(line)
    return '\n'.join(lines)[:limit]
