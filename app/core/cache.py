"""缓存清理，防止越用越大。
- 抽出来的音频（audio/*.wav，1 小时视频约 115MB）：只在识别时用，保留 3 天；
- 转换后的视频（play/*.mp4）：按最近使用时间保留，总量超过 5GB 时先删最久没用的；
- 识别时切出来的临时片段（chunks-*）：软件异常退出时可能留下，启动时清掉；
- 崩溃日志超过 1MB 时只保留后半部分。
日志本身由 RotatingFileHandler 控制：app.log 最多 4 个文件 × 2MB。"""
import os
import shutil
import time
from pathlib import Path

from .config import CACHE_DIR, LOG_DIR, OLD_CACHE_DIR, OLD_LOG_DIR, OLD_WEBVIEW_DIR
from .log import log

AUDIO_DAYS = 3
PLAY_LIMIT = 5 * 1024 ** 3
FRAME_DAYS = 30


def _size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def info() -> dict:
    return {'cache': _size(CACHE_DIR) if CACHE_DIR.exists() else 0, 'logs': _size(LOG_DIR) if LOG_DIR.exists() else 0}


def remove_old_locations() -> int:
    """旧版本把缓存、日志和浏览器数据放在 C 盘，换了位置以后清掉。"""
    freed = 0
    for old, new in ((OLD_CACHE_DIR, CACHE_DIR), (OLD_LOG_DIR, LOG_DIR), (OLD_WEBVIEW_DIR, CACHE_DIR / 'webview')):
        try:
            if old.exists() and old.resolve() != new.resolve():
                freed += _size(old)
                shutil.rmtree(old, ignore_errors=True)
                if old == OLD_CACHE_DIR and not any(old.parent.iterdir()):
                    old.parent.rmdir()
        except OSError:
            pass
    if freed:
        log.info('清掉旧位置的缓存和日志，释放 %.1f MB', freed / 1024 ** 2)
    return freed


def cleanup(clear_all=False) -> int:
    """返回释放的字节数。"""
    freed = 0 if clear_all else remove_old_locations()
    now = time.time()
    if not CACHE_DIR.exists():
        return 0
    for d in CACHE_DIR.glob('chunks-*'):
        freed += _size(d)
        shutil.rmtree(d, ignore_errors=True)
    for f in (CACHE_DIR / 'audio').glob('*'):
        try:
            if clear_all or now - f.stat().st_mtime > AUDIO_DAYS * 86400 or '.part' in f.name:
                freed += f.stat().st_size
                f.unlink()
        except OSError:
            pass
    plays = []
    for f in (CACHE_DIR / 'play').glob('*'):
        try:
            st = f.stat()
            if clear_all or '.part' in f.name:
                freed += st.st_size
                f.unlink()
            else:
                plays.append((st.st_mtime, st.st_size, f))
        except OSError:
            pass
    total = sum(s for _, s, _ in plays)
    for _, size, f in sorted(plays):  # 最久没用的先删
        if total <= PLAY_LIMIT:
            break
        try:
            f.unlink()
            total -= size
            freed += size
        except OSError:
            pass
    for d in (CACHE_DIR / 'frames').glob('*'):  # 关键帧截图：30 天没用过的删掉，需要时会按时间重新截
        try:
            newest = max((f.stat().st_mtime for f in d.iterdir()), default=d.stat().st_mtime)
            if clear_all or now - newest > FRAME_DAYS * 86400:
                freed += _size(d)
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    crash = LOG_DIR / 'crash.log'
    try:
        if crash.exists() and crash.stat().st_size > 1024 ** 2:
            data = crash.read_bytes()[-512 * 1024:]
            crash.write_bytes(data)
    except OSError:
        pass
    if freed:
        log.info('清理缓存，释放 %.1f MB', freed / 1024 ** 2)
    return freed


def touch(path) -> None:
    """记一下最近用过（转换后的视频按最近使用时间保留）。"""
    try:
        os.utime(path, None)
    except OSError:
        pass
