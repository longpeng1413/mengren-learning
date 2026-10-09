"""后台任务（识别、翻译、格式转换），界面轮询进度。"""
import threading
import time
import uuid

from .log import log


class Job:
    def __init__(self, kind):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = 'running'
        self.progress = 0.0
        self.message = ''
        self.error = None
        self.items = []      # 陆续产出的结果（字幕段、译文）
        self.result = None
        self.extra = None
        self.cancel = threading.Event()
        self.lock = threading.Lock()

    def report(self, fraction, message=None, extra=None):
        with self.lock:
            self.progress = max(self.progress, min(1.0, float(fraction)))
            if message:
                self.message = message
            if extra is not None:
                self.extra = extra

    def push(self, items):
        with self.lock:
            self.items.extend(items)

    def snapshot(self, since=0):
        with self.lock:
            return {'id': self.id, 'kind': self.kind, 'status': self.status, 'progress': round(self.progress, 4),
                    'message': self.message, 'error': self.error, 'items': self.items[since:], 'total': len(self.items),
                    'result': self.result if self.status == 'done' else None, 'extra': self.extra}


_jobs = {}
_lock = threading.Lock()


def start(kind, fn):
    job = Job(kind)
    with _lock:
        _jobs[job.id] = job

    def run():
        t0 = time.time()
        log.info('任务开始：%s %s', kind, job.id)
        try:
            result = fn(job)
            with job.lock:
                if job.cancel.is_set():
                    job.status = 'cancelled'
                else:
                    job.result = result
                    job.progress = 1.0
                    job.status = 'done'
        except Exception as e:  # 把错误原样告诉界面
            with job.lock:
                if job.cancel.is_set():
                    job.status = 'cancelled'
                else:
                    job.status = 'error'
                    job.error = str(e) or e.__class__.__name__
            if job.status == 'error':
                log.exception('任务出错：%s %s', kind, job.id)
        log.info('任务结束：%s %s %s，用时 %.1f 秒', kind, job.id, job.status, time.time() - t0)
    threading.Thread(target=run, daemon=True, name=f'job-{kind}').start()
    return job


def get(job_id):
    with _lock:
        return _jobs.get(job_id)
