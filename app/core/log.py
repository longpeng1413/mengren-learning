"""日志：软件所在文件夹\\日志（可随时删除）\\app.log（最多 4 个文件，每个 2MB）。
打包后的窗口程序没有控制台（sys.stdout/stderr 是 None），这里把它们也接到日志里，
否则 print 或打印错误堆栈时会再出错。程序崩溃时的堆栈写在 crash.log。"""
import faulthandler
import logging
import sys
import threading
from logging.handlers import RotatingFileHandler

from .config import LOG_DIR
log = logging.getLogger('mengren')
_crash_file = None


class _ToLog:
    def __init__(self, level):
        self.level = level
        self._buf = ''

    def write(self, text):
        self._buf += str(text)
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            if line.strip():
                log.log(self.level, line.rstrip())
        return len(text)

    def flush(self):
        if self._buf.strip():
            log.log(self.level, self._buf.rstrip())
        self._buf = ''

    def isatty(self):
        return False


def setup(debug=False):
    global _crash_file
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_DIR / 'app.log', maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for noisy in ('urllib3', 'httpx', 'httpcore', 'anthropic', 'websocket', 'pywebview'):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if sys.stdout is None or getattr(sys, 'frozen', False):
        sys.stdout = _ToLog(logging.INFO)
    if sys.stderr is None or getattr(sys, 'frozen', False):
        sys.stderr = _ToLog(logging.ERROR)
    try:
        _crash_file = open(LOG_DIR / 'crash.log', 'a', encoding='utf-8')
        faulthandler.enable(_crash_file, all_threads=True)
    except OSError:
        pass

    def excepthook(exc_type, exc, tb):
        log.critical('未处理的错误', exc_info=(exc_type, exc, tb))
    sys.excepthook = excepthook
    threading.excepthook = lambda a: log.critical('线程 %s 出错', a.thread.name if a.thread else '?',
                                                  exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    return log


def dump_threads(reason=''):
    """把所有线程当前在做什么写进日志，用来查卡死。"""
    import traceback
    frames = sys._current_frames()
    lines = [f'==== 线程快照 {reason} ====']
    for t in threading.enumerate():
        f = frames.get(t.ident)
        if f:
            lines.append(f'--- {t.name}')
            lines.extend(x.rstrip() for x in traceback.format_stack(f))
    log.warning('\n'.join(lines))
