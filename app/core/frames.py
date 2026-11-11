"""画面：截图（插入截图、剪藏用），以及把以前留下的插图记录写成文字给 AI 参考。

2026-11 起课程里没有任何「看图」的 AI 了（用户定；书籍整理把书页图交给 AI 是另一回事，见 book.py）：
- 2026-11-10 去掉「自动找插图」（以前：每 2 秒取小图、挑候选、AI 筛选、写说明、去重，一键整理会做）和「用画面复核存疑句」：
  插图自己听课时手动插更好、不受数量限制；AI 看图改字幕效果不好；这两步还占了一节课大半的 ChatGPT 额度。
- 2026-11-11 去掉 AI 区的「画面」页（截取当前画面、补写说明、AI 重画）和设置里的「看图」：插图都用字幕栏 / 课程重点的「插入截图」，
  AI 重画一次都没用过。
以前留下的插图（字幕文件里的 frames，包括 AI辅助理解图 redraw）照常显示、照常剪藏，课程重点里的 ![[画面 mm:ss]] 不受影响
（显示在 app.js 的 redrawHTML，剪藏在 doc.place_figures）。

截图放在缓存里（「缓存/frames」），删了随时可以按时间重新截（grab），30 天没用的由 cache 清掉。"""
import hashlib
import json
import os
from pathlib import Path

from .config import CACHE_DIR
from .media import MediaError, cache_key, crop_vf, norm_crop, run, tool


def frames_dir(path) -> Path:
    return CACHE_DIR / 'frames' / cache_key(path)


def frame_file(path, t, small=False) -> Path:
    return frames_dir(path) / (f'{int(round(float(t) * 1000)):09d}' + ('_s' if small else '') + '.jpg')


def grab(path, t, small=False, crop=None) -> Path:
    """截一张图（已经截过就直接用）。small=True 时截 640 宽的小图。
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


def notes_text(frames, a=None, b=None, limit=20000):
    """以前留下的插图记录写成文字，给 AI 校对、课程重点参考。a/b 限定时间范围。"""
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
