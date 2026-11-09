"""解压出来的软件文件带「来自网络」标记时打不开（2026-11-09，朋友的电脑：双击没反应，两秒后进程自己没了）。

原因：用 Windows 自带的解压（或保留标记的解压软件）解开从网上下载的压缩包，每个文件都带上
Zone.Identifier（ZoneId=3，「来自 Internet」）。.NET Framework 不肯加载带这个标记的
_internal\\pythonnet\\runtime\\Python.Runtime.dll（「尝试从一个网络位置加载程序集……」），
pywebview 建不出窗口，以前只在日志里写一句「窗口出错」就退出，用户什么都看不到。

三层保护：
1. 打包时在 exe 旁边放「软件名.exe.config」（build.py 的 EXE_CONFIG），打开 loadFromRemoteSources，
   带标记也照样加载（实测单靠它就能打开）；
2. 启动时 clear_app_marks() 去掉软件文件夹里的标记（只在打包版、而且 Python.Runtime.dll 带标记时才翻一遍，
   平时只多看一个文件）；有的电脑删不掉这个标记（朋友那台 Unblock-File、Remove-Item 都说成功、其实还在），
   就把标记清空——清空也等于没有标记；
3. 真打不开时 show_fail() 弹窗说清原因和日志在哪（explain() 按报错认几种常见原因）。"""
import os
import sys
from pathlib import Path

from . import config
from .log import log

STREAM = ':Zone.Identifier'
PROBE = Path('_internal') / 'pythonnet' / 'runtime' / 'Python.Runtime.dll'
SKIP_DIRS = {config.CACHE_NAME, config.LOG_NAME, '更新临时文件（可随时删除）'}  # 缓存可能有几万个文件，不用翻


def marked(path) -> bool:
    """这个文件带「来自网络」标记（而且标记里有内容）。"""
    try:
        return os.path.getsize(str(path) + STREAM) > 0
    except OSError:
        return False


def clear(path) -> bool:
    """去掉一个文件的标记：先删，删不掉就清空。返回最后是不是没有标记了。"""
    s = str(path) + STREAM
    try:
        os.remove(s)
    except OSError:
        pass
    if marked(path):
        try:
            with open(s, 'w'):
                pass
        except OSError:
            pass
    return not marked(path)


def clear_app_marks(app=None, force=False) -> dict:
    """去掉软件文件夹里所有文件的标记。返回 {'checked': 看了几个, 'cleared': 去掉几个, 'failed': 去不掉的几个}。
    不是打包版（从源码运行）不管；Python.Runtime.dll 没带标记时只看这一个文件就返回（force 时照样全翻）。"""
    res = {'checked': 0, 'cleared': 0, 'failed': 0}
    if app is None:
        if not getattr(sys, 'frozen', False) or os.name != 'nt':
            return res
        app = config.app_dir()
    app = Path(app)
    if not force and not marked(app / PROBE) and not marked(app / (config.APP_NAME + '.exe')):
        return res
    for root, dirs, files in os.walk(app):
        if Path(root) == app:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            res['checked'] += 1
            if marked(p):
                if clear(p):
                    res['cleared'] += 1
                else:
                    res['failed'] += 1
    log.info('软件文件带「来自网络」标记：看了 %d 个文件，去掉 %d 个，去不掉 %d 个', res['checked'], res['cleared'], res['failed'])
    return res


def explain(exc) -> str:
    """按报错说清打不开的原因和怎么办（给弹窗用，不写术语）。"""
    text = f'{type(exc).__name__}: {exc}'
    low = text.lower()
    if 'python.runtime' in low or 'loadfromremotesources' in low:
        return ('Windows 拦住了软件自带的组件：解压出来的文件带着「来自网络」的标记，Windows 不让用。\n\n'
                f'解决办法：把整个「{config.APP_NAME}」文件夹删掉；在下载的压缩包上点右键 →「属性」→ 勾选下面的「解除锁定」→「确定」，'
                '然后重新解压，再打开。')
    if 'webview2' in low or 'edgechromium' in low or 'edge chromium' in low:
        return ('这台电脑缺少微软的 WebView2 运行时（软件靠它显示界面）。\n\n'
                '解决办法：到 https://developer.microsoft.com/microsoft-edge/webview2/ 下载安装「常青版」，装好后再打开。')
    return '打开窗口时出错：' + text[:300]


def show_fail(exc):
    """启动失败时弹窗（以前只写日志，用户看到的是「双击没反应」）。点「是」打开日志文件夹。"""
    if os.name != 'nt' or '--selftest' in ' '.join(sys.argv):
        return
    try:
        import ctypes
        log_file = config.LOG_DIR / 'app.log'
        msg = (f'{config.APP_NAME}没能打开。\n\n{explain(exc)}\n\n'
               f'详细记录在：\n{log_file}\n反馈问题时把这个文件一起发过去。\n\n要打开这个文件夹吗？')
        if ctypes.windll.user32.MessageBoxW(None, msg, config.APP_NAME, 0x4 | 0x10) == 6:  # 是 / 否 + 错误图标；6 = 是
            os.startfile(config.LOG_DIR)
    except Exception:
        log.warning('启动失败的提示框没弹出来', exc_info=True)
