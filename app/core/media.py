"""ffmpeg 相关：读取媒体信息、转成可播放格式、抽音频、按停顿切段。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CACHE_DIR

NO_WINDOW = 0x08000000 if os.name == 'nt' else 0  # 打包后调用 ffmpeg 不弹黑框

AUDIO_EXT = {'.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.opus', '.wma', '.amr', '.ape'}
VIDEO_EXT = {'.mp4', '.m4v', '.mov', '.mkv', '.webm', '.avi', '.flv', '.ts', '.wmv', '.3gp', '.mpg', '.mpeg'}
MEDIA_EXT = AUDIO_EXT | VIDEO_EXT

# WebView2（Edge 内核）能直接放的容器和编码
PLAY_CONTAINERS = {'.mp4', '.m4v', '.mov', '.webm', '.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.opus'}
PLAY_VIDEO = {'h264', 'vp8', 'vp9', 'av1'}
PLAY_AUDIO = {'aac', 'mp3', 'opus', 'vorbis', 'flac', 'pcm_s16le', 'pcm_s24le', 'pcm_f32le', 'pcm_u8'}


class MediaError(Exception):
    pass


def tool(name: str) -> str:
    """优先用 exe 旁边的 ffmpeg，其次用系统 PATH 里的。"""
    here = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
    for cand in (here / f'{name}.exe', here / 'ffmpeg' / f'{name}.exe', here / 'tools' / f'{name}.exe'):
        if cand.is_file():
            return str(cand)
    found = shutil.which(name)
    if not found:
        raise MediaError(f'找不到 {name}，请先安装 ffmpeg（winget install Gyan.FFmpeg）')
    return found


def run(args, timeout=None, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                          timeout=timeout, creationflags=NO_WINDOW, **kw)


def probe(path: str) -> dict:
    r = run([tool('ffprobe'), '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', path], timeout=60)
    if r.returncode != 0:
        raise MediaError('无法读取这个文件：' + (r.stderr.strip().splitlines() or ['未知错误'])[-1][:200])
    data = json.loads(r.stdout or '{}')
    streams = data.get('streams') or []
    v = next((s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')), None)
    a = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    try:
        duration = float((data.get('format') or {}).get('duration') or 0)
    except ValueError:
        duration = 0.0
    if not a:
        raise MediaError('这个文件里没有声音，无法生成字幕')
    return {
        'duration': duration,
        'vcodec': v.get('codec_name') if v else None,
        'acodec': a.get('codec_name'),
        'width': v.get('width') if v else None,
        'height': v.get('height') if v else None,
        'has_video': bool(v),
    }


_tail_cache = {}


def tail_problem(path: str, seconds=30) -> str:
    """解码文件最后 30 秒，看结尾是否损坏（比如没下载完整）。没问题返回空字符串。"""
    try:
        key = (os.path.abspath(path).lower(), os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return ''
    if key in _tail_cache:
        return _tail_cache[key]
    msg = ''
    try:
        r = run([tool('ffmpeg'), '-v', 'error', '-sseof', f'-{seconds}', '-i', path, '-f', 'null', '-'], timeout=120)
        lines = [x for x in r.stderr.splitlines() if x.strip()]
        if r.returncode != 0 or any(re.search(r'partial file|Invalid NAL|corrupt|truncat|error while decoding|Invalid data', x, re.I) for x in lines):
            msg = '视频结尾解码出错（文件可能不完整）'
    except (MediaError, subprocess.TimeoutExpired, OSError):
        msg = ''
    _tail_cache[key] = msg
    return msg


def play_plan(path: str, info: dict) -> str:
    """direct：直接播放；remux：只换容器（很快）；transcode：重新编码（较慢）。"""
    ext = Path(path).suffix.lower()
    v_ok = (not info['has_video']) or info['vcodec'] in PLAY_VIDEO
    a_ok = info['acodec'] in PLAY_AUDIO
    if ext in PLAY_CONTAINERS and v_ok and a_ok:
        return 'direct'
    if v_ok and info['acodec'] in {'aac', 'mp3'} and (not info['has_video'] or info['vcodec'] == 'h264'):
        return 'remux'
    return 'transcode'


def cache_key(path: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f'{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}'.encode()).hexdigest()[:20]


def playable_path(path: str, info: dict) -> Path:
    ext = '.mp4' if info['has_video'] else '.m4a'
    return CACHE_DIR / 'play' / (cache_key(path) + ext)


def _ffmpeg_progress(args, duration, progress, cancel):
    """运行 ffmpeg 并通过 -progress 汇报进度。"""
    proc = subprocess.Popen(args + ['-progress', 'pipe:1', '-nostats'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace', creationflags=NO_WINDOW)
    err_tail = []

    def drain():
        for line in proc.stderr:
            err_tail.append(line)
            del err_tail[:-20]
    import threading
    threading.Thread(target=drain, daemon=True).start()
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            raise MediaError('已取消')
        m = re.match(r'out_time_(?:us|ms)=(\d+)', line)
        if m and duration:
            progress(min(1.0, int(m.group(1)) / 1e6 / duration))
    proc.wait()
    if proc.returncode != 0:
        raise MediaError('ffmpeg 处理失败：' + ''.join(err_tail[-3:]).strip()[:300])


def make_playable(path: str, info: dict, plan: str, progress=lambda f: None, cancel=None) -> Path:
    out = playable_path(path, info)
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.part' + out.suffix)
    base = [tool('ffmpeg'), '-y', '-hide_banner', '-loglevel', 'error', '-i', path, '-map', '0:v:0?', '-map', '0:a:0']
    if plan == 'remux':
        codec = ['-c', 'copy']
    elif info['has_video']:
        codec = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k']
    else:
        codec = ['-vn', '-c:a', 'aac', '-b:a', '160k']
    _ffmpeg_progress(base + codec + ['-movflags', '+faststart', str(tmp)], info['duration'], progress, cancel)
    os.replace(tmp, out)
    return out


def extract_wav(path: str, duration: float, progress=lambda f: None, cancel=None) -> Path:
    out = CACHE_DIR / 'audio' / (cache_key(path) + '.wav')
    if out.is_file() and out.stat().st_size > 1000:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + '.part.wav')
    _ffmpeg_progress([tool('ffmpeg'), '-y', '-hide_banner', '-loglevel', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
                      '-c:a', 'pcm_s16le', str(tmp)], duration, progress, cancel)
    os.replace(tmp, out)
    return out


# ---- 按停顿切段（思路与乔木剪藏本地助手相同）----
SILENCE_SECONDS = 0.35
MIN_CHUNK = 4.0


def silence_threshold(wav: Path) -> float:
    r = run([tool('ffmpeg'), '-hide_banner', '-nostats', '-i', str(wav), '-af', 'volumedetect', '-f', 'null', '-'], timeout=900)
    m = re.search(r'mean_volume:\s*(-?[0-9.]+) dB', r.stderr)
    return max(-45.0, min(-22.0, float(m.group(1)) - 13.0)) if m else -30.0


def silences(wav: Path) -> list:
    r = run([tool('ffmpeg'), '-hide_banner', '-nostats', '-i', str(wav), '-af',
             f'silencedetect=noise={silence_threshold(wav):.1f}dB:d={SILENCE_SECONDS}', '-f', 'null', '-'], timeout=900)
    starts = [float(x) for x in re.findall(r'silence_start:\s*(-?[0-9.]+)', r.stderr)]
    ends = [float(x) for x in re.findall(r'silence_end:\s*(-?[0-9.]+)', r.stderr)]
    return [(max(a, 0) + b) / 2 for a, b in zip(starts, ends)]


def plan_chunks(mids: list, total: float, target: float, maximum: float) -> list:
    """切成约 target 秒的段，切点落在停顿中间，单段不超过 maximum 秒。"""
    chunks, start = [], 0.0
    while total - start > maximum:
        cands = [m for m in mids if start + MIN_CHUNK <= m <= start + maximum]
        cut = min(cands, key=lambda m: abs(m - (start + target))) if cands else start + maximum
        chunks.append((round(start, 3), round(cut, 3)))
        start = cut
    if total - start >= 0.3 or not chunks:
        chunks.append((round(start, 3), round(total, 3)))
    return chunks


def encode_chunk(wav: Path, start: float, end: float, out: Path) -> Path:
    path = out.with_suffix('.mp3')
    r = run([tool('ffmpeg'), '-y', '-loglevel', 'error', '-ss', f'{start:.3f}', '-t', f'{end - start:.3f}', '-i', str(wav),
             '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libmp3lame', '-b:a', '48k', str(path)], timeout=300)
    if r.returncode != 0 or not path.is_file() or path.stat().st_size == 0:
        raise MediaError('切分音频失败：' + r.stderr.strip()[:200])
    return path
