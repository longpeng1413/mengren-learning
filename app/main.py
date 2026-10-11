"""猛人学习：在独立窗口里打开界面。界面直接调用 bridge.Api，不开网络端口。
python app/main.py [--debug]   （--debug 打开开发者工具，日志记得更详细）"""
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import config  # noqa: E402
from core.log import dump_threads, log, setup as setup_log  # noqa: E402

setup_log(debug='--debug' in sys.argv)

import webview  # noqa: E402
from webview.dom import DOMEventHandler  # noqa: E402

from bridge import Api  # noqa: E402

VERSION = '1.7.11'


_instance = None  # 保持互斥锁的句柄，进程退出时自动释放


def single_instance() -> bool:
    """同一份数据（%APPDATA%\\猛人学习）只允许开一个窗口。已经开着时切到那个窗口并返回 False。
    刚关掉又马上打开时，旧进程可能还没退完，最多等 5 秒。"""
    global _instance
    if os.name != 'nt' or '--selftest' in ' '.join(sys.argv):
        return True
    import ctypes
    import hashlib
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.WaitForSingleObject.restype = ctypes.c_uint32
    name = 'Local\\MengrenLearning-' + hashlib.md5(str(config.DATA_DIR).lower().encode('utf-8')).hexdigest()[:12]
    _instance = k32.CreateMutexW(None, False, name)
    if not _instance:
        return True
    if k32.WaitForSingleObject(_instance, 5000) in (0, 0x80):  # 拿到了（或上一个进程异常退出留下的）
        return True
    hwnd = u32.FindWindowW(None, config.APP_NAME)
    if hwnd:
        u32.ShowWindow(hwnd, 9)  # 还原
        u32.SetForegroundWindow(hwnd)
    else:
        u32.MessageBoxW(None, '猛人学习已经打开了。', config.APP_NAME, 0x40)
    return False


def log_data_files():
    """启动自检：记下这次实际读的数据文件夹和文件（大小、修改时间、媒体库条数），
    以后再出现「设置 / 媒体库变空」能从日志直接看出读的是哪份。不记内容，不记密钥。"""
    try:
        parts = []
        for f in (config.SETTINGS_FILE, config.LIBRARY_FILE, config.TEACHERS_FILE):
            if f.exists():
                st = f.stat()
                parts.append(f'{f.name} {st.st_size}B {time.strftime("%m-%d %H:%M:%S", time.localtime(st.st_mtime))}')
            else:
                parts.append(f'{f.name} 不存在')
        lib = config.load_library()
        log.info('数据文件夹：%s（APPDATA=%s）｜%s｜媒体库 %d 个文件夹、%d 个单独文件',
                 config.DATA_DIR, os.environ.get('APPDATA'), '；'.join(parts),
                 len(lib.get('folders') or []), len(lib.get('files') or []))
    except Exception as e:  # 自检本身不能影响启动
        log.warning('启动自检失败：%r', e)


def main():
    log.info('==== 猛人学习 %s 启动（%s）====', VERSION, 'exe' if getattr(sys, 'frozen', False) else '源码')
    from core import appdata
    selftest = '--selftest' in ' '.join(sys.argv)
    # 由其他打包应用（例如 Claude 桌面版）打开时，写设置会被 Windows 转存到那个应用的私有文件夹，
    # 和从桌面打开的不是同一份数据。这种情况通过资源管理器重新打开自己。
    if not selftest and appdata.is_virtualized() and appdata.relaunch_outside():
        return
    if not single_instance():
        log.info('已经有一个猛人学习开着，切到那个窗口后退出')
        return
    if not selftest and not appdata.is_virtualized():
        appdata.adopt_virtual()  # 以前被转存走的设置、媒体库搬回来
    from core import cache
    threading.Thread(target=cache.cleanup, daemon=True, name='cache-cleanup').start()
    api = Api()
    from core import portable
    api._bundled = portable.import_bundled(api)  # 解压即用：软件文件夹里附带的设置，第一次启动时自动导入
    try:
        s = config.load()
    except config.DataError as e:  # 设置文件读不出来：说明原因后退出，不带着空设置继续用（一保存就会冲掉真数据）
        log.error('启动时读不出设置：%s', e)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(e), config.APP_NAME, 0x10)
        return
    log_data_files()
    bg = '#f3f0ea' if s.get('theme') == 'light' else '#0e0e10'
    index = config.resource_dir() / 'web' / 'index.html'
    window = webview.create_window(config.APP_NAME, index.as_uri(), js_api=api, width=1440, height=900, min_size=(960, 600),
                                   background_color=bg, maximized=True, text_select=True)
    api._window = window
    api._version = VERSION

    def on_drop(e):
        for f in (e.get('dataTransfer') or {}).get('files') or []:
            full = f.get('pywebviewFullPath')
            if full:
                log.info('拖入文件：%s', full)
                window.evaluate_js(f'window.openPath && window.openPath({json.dumps(full)})')
                break

    def on_loaded():
        log.info('界面加载完成')
        window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)
        threading.Thread(target=watchdog, daemon=True, name='watchdog').start()
        selftest = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--selftest=')), None)
        if selftest:
            threading.Thread(target=run_selftest, args=(selftest,), daemon=True, name='selftest').start()

    def run_selftest(path):
        """开发自测：执行测试脚本，把结果写到旁边的 .result.json。"""
        time.sleep(1.5)
        code = Path(path).read_text(encoding='utf-8')
        base = str(Path(path).resolve().parents[1] / 'test-media' / 'ui') + '\\'
        window.evaluate_js(f'window.__TEST_BASE = {json.dumps(base)};')
        window.evaluate_js(f'window.__DONE = null; ({code}).then(r => window.__DONE = r, e => window.__DONE = ["ERROR " + e])')
        result = None
        for _ in range(330):
            time.sleep(1)
            try:
                raw = window.evaluate_js('window.__DONE ? JSON.stringify(window.__DONE) : ""')
            except Exception as e:
                raw = json.dumps(['ERROR ' + repr(e)])
            if raw:
                result = json.loads(raw)
                break
        Path(path).with_suffix('.result.json').write_text(json.dumps(result or ['TIMEOUT'], ensure_ascii=False, indent=1), encoding='utf-8')
        if '--selftest-close' in sys.argv:  # 连关闭流程一起测：走一遍和点右上角 × 一样的关闭流程
            on_closing()
            return
        if '--keep' not in sys.argv:
            os._exit(0)

    # 界面卡住时把各线程在做什么写进日志，方便查原因
    def watchdog():
        state = {'pending': None}
        while True:
            time.sleep(10)
            if closing['started']:
                return
            if state['pending'] and time.time() - state['pending'] > 20:
                dump_threads('界面超过 20 秒没有响应')
                state['pending'] = time.time() + 600  # 同一次卡顿只记一次
                continue
            if state['pending']:
                continue
            state['pending'] = time.time()

            def probe():
                try:
                    window.evaluate_js('1')
                finally:
                    state['pending'] = None
            threading.Thread(target=probe, daemon=True, name='probe').start()

    # 关闭：先取消这次关闭（关闭事件在界面线程里执行，不能在这里等界面），
    # 再在后台让界面保存，保存完（最多等 4 秒）才真正关窗。
    closing = {'started': False, 'done': False}

    def on_closing():
        if closing['done']:
            return True
        if closing['started']:
            return False
        closing['started'] = True
        log.info('收到关闭请求，先保存字幕和播放进度')

        def finish():
            try:
                window.evaluate_js('window.prepareClose ? window.prepareClose() : window.pywebview.api.call("quit", {})')
            except Exception:
                log.exception('通知界面保存时出错')
            if not api._quit.wait(4):
                log.warning('界面 4 秒内没有保存完，直接关闭')
            closing['done'] = True
            log.info('关闭窗口')
            try:
                window.destroy()
            except Exception:
                log.exception('关闭窗口出错')
            time.sleep(2)
            os._exit(0)  # 兜底：窗口关不掉时直接退出
        threading.Thread(target=finish, daemon=True, name='closing').start()
        return False

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    storage = config.CACHE_DIR / 'webview'  # 浏览器内核的缓存，也放进缓存文件夹
    storage.mkdir(parents=True, exist_ok=True)
    try:
        webview.start(gui='edgechromium', private_mode=False, storage_path=str(storage), debug='--debug' in sys.argv)
    except Exception:
        log.exception('窗口出错')
    log.info('==== 退出 ====')
    logging_shutdown()
    os._exit(0)


def logging_shutdown():
    import logging
    logging.shutdown()


if __name__ == '__main__':
    main()
