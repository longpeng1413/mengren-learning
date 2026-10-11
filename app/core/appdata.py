"""防止设置、媒体库被 Windows「转存」到别的应用的私有文件夹。

由打包应用（例如 Claude 桌面版）启动的程序，写 %APPDATA% 时会被 Windows 悄悄转存到
%LOCALAPPDATA%\\Packages\\<那个应用>\\LocalCache\\Roaming\\ 下面，而且只有那个应用启动的程序看得到。
结果是：从那里打开时一份数据，从桌面打开时另一份（媒体库空白、设置像是没了）。

对策：
1. 启动时探测自己是不是被转存了（往数据文件夹写个小文件，看它是不是出现在 Packages 下面）。
   是的话通过资源管理器重新打开自己（等同于双击桌面图标），本进程退出。
2. 正常启动时，如果 Packages 下面有转存的数据，比真正位置的新（或真正位置没有），先备份再搬回来，
   搬完把转存的文件夹改名「…（已搬回，可删除）」。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .log import log

ADOPTED = '（已搬回，可删除）'


def _packages():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Packages'


def virtual_dirs():
    """Packages 下面所有转存的「猛人学习」数据文件夹。"""
    base = _packages()
    if not base.is_dir():
        return []
    try:
        return [p for p in base.glob(f'*/LocalCache/Roaming/{config.APP_NAME}') if p.is_dir()]
    except OSError:
        return []


def is_virtualized() -> bool:
    """这个进程写 %APPDATA% 会不会被转存。"""
    if os.name != 'nt' or not _packages().is_dir():
        return False
    probe = config.DATA_DIR / f'.转存探测-{os.getpid()}'
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text('1', encoding='utf-8')
    except OSError:
        return False
    try:
        return any((d / probe.name).exists() for d in virtual_dirs())
    finally:
        probe.unlink(missing_ok=True)


def relaunch_outside() -> bool:
    """被转存时，通过资源管理器重新打开自己。成功交出去返回 True（本进程应退出）。
    20 秒内已经交过一次还是被转存，就不再重开（防止来回打开），照常运行。"""
    if not getattr(sys, 'frozen', False):
        log.warning('这个进程写设置会被转存到其他应用的私有文件夹（源码运行，不自动重开）')
        return False
    mark = config.LOG_DIR / '转存重开.txt'
    try:
        if mark.exists() and time.time() - mark.stat().st_mtime < 20:
            log.warning('重开后仍然被转存，照常运行')
            return False
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(time.strftime('%Y-%m-%d %H:%M:%S'), encoding='utf-8')
        subprocess.Popen(['explorer.exe', sys.executable], close_fds=True)
    except OSError:
        log.warning('通过资源管理器重开失败，照常运行', exc_info=True)
        return False
    log.info('这个进程是由其他打包应用启动的，写设置会被转存；已通过资源管理器重新打开，本进程退出')
    return True


def adopt_virtual() -> list:
    """把转存的数据搬回真正的数据文件夹，返回搬回的文件。
    这边从没真正用过（没有媒体库、也没有老师档案）而转存那边有：整份采用转存的；
    否则只搬比这边新的或这边没有的文件。被替换的文件先备份到 backups\\数据\\搬回前-时间\\。"""
    moved = []
    for vdir in virtual_dirs():
        stamp = time.strftime('%Y%m%d-%H%M%S')
        keep = config.DATA_DIR / 'backups' / '数据' / f'搬回前-{stamp}'
        used = lambda d: (d / 'library.json').exists() or (d / 'teachers.json').exists()  # noqa: E731
        take_all = used(vdir) and not used(config.DATA_DIR)
        for src in sorted(vdir.rglob('*')):
            if not src.is_file() or src.suffix == '.tmp' or src.name.startswith('.转存探测'):
                continue
            rel = src.relative_to(vdir)
            dst = config.DATA_DIR / rel
            try:
                if dst.exists():
                    if not take_all and dst.stat().st_mtime >= src.stat().st_mtime:
                        continue
                    (keep / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dst, keep / rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                moved.append(str(rel))
            except OSError:
                log.warning('搬回 %s 失败', rel, exc_info=True)
        target = vdir.with_name(config.APP_NAME + ADOPTED)
        if target.exists():
            target = vdir.with_name(f'{config.APP_NAME}{ADOPTED}-{stamp}')
        try:
            vdir.rename(target)
        except OSError:
            log.warning('转存的文件夹改名失败：%s', vdir, exc_info=True)
        log.info('从 %s 搬回 %d 个文件：%s', vdir, len(moved), '、'.join(moved[:20]))
    return moved
