"""Agent 任务记录：每节课每次自动整理一条，存在 %APPDATA%\\猛人学习\\tasks\\<task_id>.json。

记下每一步的状态、开始和结束时间、错误、重试次数、用的 AI 和模型，失败了能看出卡在哪、能接着做。
（界面自己的后台任务仍用 jobs.py，只在内存里。）

还负责：
- 错误分类：网络、接口临时错误可以自动重试；缺密钥、文件不存在、视频损坏这类不重试；
- 「正在整理」标记：同一节课同时只让一个 Agent 处理，界面打开这节课时能提示；
- 取消：另一个命令写一个 <task_id>.cancel 文件，正在跑的任务看到就停。
"""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .config import DATA_DIR
from .log import log

TASKS_DIR = DATA_DIR / 'tasks'
BUSY_DIR = DATA_DIR / 'locks'
KEEP = 300  # 最多留多少条任务记录

# 任务和每一步的状态。skipped：这一步不需要做（已经做过、中文不用翻译、没有画面……），原因写在 reason
PENDING, RUNNING, COMPLETED, FAILED, CANCELLED, SKIPPED = 'pending', 'running', 'completed', 'failed', 'cancelled', 'skipped'


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


# ------------------------------------------------------------------ 错误 ----
class WorkError(Exception):
    """任务层自己发现的问题，带错误码。"""

    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


ERROR_CODES = {
    'cancelled': '已取消',
    'not_found': '找不到这个视频或文件',
    'not_in_library': '不在媒体库里',
    'busy': '这节课正在被另一个任务整理',
    'config': '设置不完整（缺 API Key、没选模型、没登录等）',
    'auth': '鉴权失败（API Key 不对或没开通）',
    'quota': '额度不足或请求太频繁',
    'model': '找不到所选的模型',
    'network': '网络或服务暂时出错（可以重试）',
    'bad_output': 'AI 返回的格式不对（可以重试）',
    'service': '服务返回错误',
    'media': '视频读不出或已损坏',
    'data': '字幕文件或设置文件读不出',
    'not_supported': '还不支持',
    'no_subtitles': '没有字幕，后面的步骤做不了',
    'internal': '程序出错（详情在日志里）',
}

_RULES = [  # (错误码, 能否重试, 错误信息里的关键词)
    ('config', False, r'还没有|请先填写|未知的 AI 服务|未知的识别服务|不能看图|对象存储|存储桶|Access Key|找不到 ff(mpeg|probe)|请先安装 ffmpeg'),
    ('auth', False, r'鉴权失败|拒绝访问'),
    ('quota', False, r'额度|欠费|太频繁'),
    ('model', False, r'找不到这个模型'),
    ('bad_output', True, r'格式不对'),
    ('network', True, r'连接.*失败|超时|timed? ?out|HTTP 5\d\d|（5\d\d）|Connection|网络'),
    ('not_found', False, r'找不到这个文件|文件不见了|不见了'),
]


def classify(e):
    """→ (错误码, 能否重试, 给人看的说明)"""
    msg = str(e) or e.__class__.__name__
    if isinstance(e, WorkError):
        return e.code, e.retryable, msg
    name = e.__class__.__name__
    if name == 'Cancelled' or msg in ('已取消', 'Cancelled'):
        return 'cancelled', False, '已取消'
    if isinstance(e, FileNotFoundError):
        return 'not_found', False, msg
    if name == 'DataError':
        return 'data', False, msg
    for code, retry, pat in _RULES:
        if re.search(pat, msg, re.I):
            return code, retry, msg
    if name == 'MediaError':
        return 'media', False, msg
    try:
        import requests
        if isinstance(e, requests.RequestException):
            return 'network', True, msg
    except ImportError:
        pass
    if isinstance(e, (TimeoutError, ConnectionError)):
        return 'network', True, msg
    if name in ('AsrError', 'LlmError', 'BookError', 'AuthError'):
        return 'service', False, msg
    return 'internal', False, msg


def error_dict(e):
    code, retry, msg = classify(e)
    return {'error_code': code, 'retryable': retry, 'message': msg}


# ---------------------------------------------------------- 进程还在不在 ----
def pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _write(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    for i in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    os.replace(tmp, path)


def _read(path: Path):
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ 任务 ----
class Task:
    def __init__(self, kind, path, video_id, title, options, stages, labels):
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.data = {
            'task_id': time.strftime('%Y%m%d-%H%M%S') + f'{int(time.time() * 1000) % 1000:03d}-' + uuid.uuid4().hex[:4],  # 按时间排序
            'kind': kind, 'path': path, 'video_id': video_id, 'title': title,
            'status': PENDING, 'stage': None, 'progress': 0.0, 'message': '',
            'created': now(), 'started': None, 'finished': None, 'seconds': None,
            'caller': options.pop('caller', 'cli'), 'options': options, 'pid': os.getpid(),
            'error': None, 'warnings': [],
            'stages': [{'name': s, 'label': labels.get(s, s), 'status': PENDING, 'started': None, 'finished': None,
                        'retries': 0, 'error_code': None, 'retryable': None, 'error': None, 'reason': None,
                        'model': None, 'result': None} for s in stages],
            'outputs': {},
        }
        self._last_write = 0.0
        self._watch = None

    @property
    def id(self):
        return self.data['task_id']

    def file(self):
        return TASKS_DIR / f'{self.id}.json'

    def save(self, force=True):
        with self.lock:
            if not force and time.time() - self._last_write < 1.0:
                return
            self._last_write = time.time()
            _write(self.file(), self.data)

    def stage(self, name):
        return next(s for s in self.data['stages'] if s['name'] == name)

    def begin(self):
        self.data.update(status=RUNNING, started=now())
        self.save()
        _prune()
        self._watch = threading.Thread(target=self._watch_cancel, daemon=True, name='task-cancel')
        self._watch.start()

    def _watch_cancel(self):
        flag = TASKS_DIR / f'{self.id}.cancel'
        while not self.cancel.is_set() and self.data['status'] == RUNNING:
            if flag.exists():
                self.cancel.set()
                break
            time.sleep(1.0)

    def progress(self, fraction=None, message=None):
        if fraction is not None:
            self.data['progress'] = round(max(0.0, min(1.0, float(fraction))), 4)
        if message:
            self.data['message'] = str(message)[:200]
        self.save(force=False)

    def start_stage(self, name, model=None, params=None):
        st = self.stage(name)
        st.update(status=RUNNING, started=now(), finished=None, error=None, error_code=None, retryable=None, reason=None)
        if model:
            st['model'] = model
        if params:
            st['params'] = params
        self.data['stage'] = name
        self.data['message'] = st['label'] + '…'
        log.info('Agent 任务 %s：开始「%s」%s', self.id, st['label'], f'（{model}）' if model else '')
        self.save()
        return st

    def end_stage(self, name, status, result=None, reason=None, error=None):
        st = self.stage(name)
        st.update(status=status, finished=now())
        if result is not None:
            st['result'] = result
        if reason:
            st['reason'] = reason
        if error:
            st.update(error=error['message'], error_code=error['error_code'], retryable=error['retryable'])
        log.info('Agent 任务 %s：「%s」%s%s', self.id, st['label'], status,
                 f'：{reason or (error or {}).get("message", "")}' if (reason or error) else '')
        self.save()

    def finish(self, status, error=None):
        t0 = time.mktime(time.strptime(self.data['started'] or self.data['created'], '%Y-%m-%d %H:%M:%S'))
        self.data.update(status=status, finished=now(), seconds=round(time.time() - t0, 1), error=error)
        if status == COMPLETED:
            self.data['progress'] = 1.0
        self.data['message'] = {COMPLETED: '完成', FAILED: '失败', CANCELLED: '已取消'}.get(status, status)
        self.save()
        (TASKS_DIR / f'{self.id}.cancel').unlink(missing_ok=True)
        log.info('Agent 任务 %s 结束：%s，用时 %s 秒', self.id, status, self.data['seconds'])


def load(task_id):
    """读一条任务记录。记录里还是 running、但跑它的程序已经不在了（被关掉、断电），改成 failed。"""
    d = _read(TASKS_DIR / f'{Path(str(task_id)).name}.json')
    if not d:
        return None
    if d.get('status') in (RUNNING, PENDING) and not pid_alive(d.get('pid')):
        d['status'] = FAILED
        d['error'] = {'error_code': 'interrupted', 'retryable': True, 'message': '任务没跑完程序就退出了（被关掉或出错），可以接着运行'}
        for st in d.get('stages') or []:
            if st.get('status') == RUNNING:
                st.update(status=FAILED, error_code='interrupted', retryable=True, error='中途被打断')
    return d


def recent(limit=20, path=None):
    """最近的任务记录（新的在前）。"""
    if not TASKS_DIR.is_dir():
        return []
    out = []
    for f in sorted(TASKS_DIR.glob('*.json'), reverse=True):
        d = load(f.stem)
        if d and (path is None or os.path.normcase(d.get('path') or '') == os.path.normcase(path)):
            out.append(d)
            if len(out) >= limit:
                break
    return out


def request_cancel(task_id):
    d = load(task_id)
    if not d:
        return False
    if d.get('status') == RUNNING:
        TASKS_DIR.mkdir(parents=True, exist_ok=True)
        (TASKS_DIR / f'{d["task_id"]}.cancel').write_text(now(), encoding='utf-8')
        return True
    return False


def _prune():
    try:
        files = sorted(TASKS_DIR.glob('*.json'))
        for f in files[:-KEEP]:
            f.unlink(missing_ok=True)
    except OSError:
        pass


# ------------------------------------------------------- 「正在整理」标记 ----
def _busy_file(key):
    return BUSY_DIR / f'{key}.busy'


def busy(key):
    """这节课是不是正在被某个 Agent 任务整理：是的话返回 {task_id, pid, since}。"""
    d = _read(_busy_file(key))
    if d and pid_alive(d.get('pid')):
        return d
    return None


def busy_under(folder):
    """这个文件夹（含子文件夹）里有没有课正在被整理：有就返回其中一个的标记。"""
    base = os.path.normcase(os.path.abspath(folder)).rstrip('\\/') + os.sep
    for f in BUSY_DIR.glob('*.busy') if BUSY_DIR.is_dir() else []:
        d = busy(f.stem)
        t = load(d.get('task_id')) if d else None
        if t and os.path.normcase(os.path.abspath(t.get('path') or '')).startswith(base):
            return d
    return None


def claim(key, task_id):
    """占住这节课。已经有别的任务在整理就报错。"""
    BUSY_DIR.mkdir(parents=True, exist_ok=True)
    f = _busy_file(key)
    for _ in range(2):
        try:
            fd = os.open(f, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            other = busy(key)
            if other and other.get('task_id') != task_id:
                raise WorkError('busy', f'这节课正在被另一个任务整理（{other.get("task_id")}，{other.get("since")} 开始）')
            f.unlink(missing_ok=True)  # 留下来的旧标记（那个程序已经不在了）
            continue
        with os.fdopen(fd, 'w', encoding='utf-8') as fp:
            json.dump({'task_id': task_id, 'pid': os.getpid(), 'since': now()}, fp, ensure_ascii=False)
        return
    raise WorkError('busy', '这节课正在被另一个任务整理')


def release(key, task_id):
    d = _read(_busy_file(key))
    if d is None or d.get('task_id') == task_id:
        _busy_file(key).unlink(missing_ok=True)
