"""后台整理队列（2026-10-17，用户要求「一键整理时能切到别的课继续校对」）。

界面上点「一键整理」（或在媒体库右键「排进后台整理」）后交给这里，一节一节地整理；用户可以照常打开别的课听课、校对。
整理规则还是 workflow.process（和命令行同一套）：每一步直接写进那节课的字幕文件，「正在整理」标记和保存冲突的提醒都是现成的。
一次只整理一节：不同时打满 AI 和豆包（额度、并发都有限）。

只在内存里：软件退出就没了。已经做完的步骤都保存了，没做完的下次一键整理或命令行 --todo 会接着做。

「整个文件夹排进后台整理」（2026-10-28，几百节课）一次排进很多条，它们有同一个 batch：
其中一节因为额度用完（error_code quota）没做完时，同一批剩下排着的全部停下，免得一节接一节地失败。
"""
import threading
import time
import uuid
from pathlib import Path

from . import workflow
from .log import log

KEEP_FINISHED = 20  # 做完的最多留几条给界面看

_lock = threading.Lock()
_items = []
_wake = threading.Event()
_thread = None


def _key(path):
    return str(path).lower()


def add(path, steps=None, redo=(), lang=None, provider=None, options=None, batch=None):
    """排进队列。steps=None：按一键整理默认的步骤，只做还没做完的（和命令行一样）。返回这一条。"""
    with _lock:
        if queued(path, locked=True):
            raise ValueError('这节课已经在后台整理了')
        it = {'id': uuid.uuid4().hex[:10], 'path': str(path), 'title': Path(path).stem, 'steps': list(steps) if steps else None,
              'redo': list(redo or ()), 'lang': lang, 'provider': provider, 'options': dict(options or {}), 'batch': batch,
              'status': 'waiting', 'progress': 0.0, 'message': '排队中', 'task': None, 'error': '',
              'added': time.time(), 'finished': None, 'cancel': threading.Event()}
        _items.append(it)
        _trim()
    _ensure_worker()
    _wake.set()
    return _public(it)


def queued(path, locked=False):
    """这节课已经排着或正在整理"""
    if not locked:
        with _lock:
            return queued(path, True)
    return any(_key(x['path']) == _key(path) and x['status'] in ('waiting', 'running') for x in _items)


def _trim():
    done = [x for x in _items if x['status'] not in ('waiting', 'running')]
    for x in done[:max(0, len(done) - KEEP_FINISHED)]:
        _items.remove(x)


def _ensure_worker():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True, name='bg-organize')
            _thread.start()


def _loop():
    while True:
        with _lock:
            it = next((x for x in _items if x['status'] == 'waiting'), None)
            if it:
                it['status'], it['message'] = 'running', '开始整理…'
        if not it:
            _wake.wait(5)
            _wake.clear()
            continue
        _run(it)


def _run(it):
    def report(d):
        it['progress'] = float(d.get('progress') or 0)
        it['message'] = d.get('message') or it['message']
    try:
        t = workflow.process(it['path'], steps=it['steps'], redo=it['redo'], lang=it['lang'], provider=it['provider'],
                             caller='gui', check_library=False, cancel=it['cancel'], on_progress=report, **it['options'])
        b = workflow.brief(t)
        it['task'] = b
        it['status'] = {'completed': 'done', 'cancelled': 'cancelled'}.get(t.get('status'), 'failed')
        if it['status'] == 'failed':
            it['error'] = (b.get('message') or '没有完成') + (f'（{b["stage"]}）' if b.get('stage') else '')
            if b.get('error_code') == 'quota' and it.get('batch'):
                it['batch_stopped'] = _stop_batch(it['batch'])
    except Exception as e:  # noqa: BLE001  兜底：一节出错不影响后面排着的
        log.exception('后台整理 %s 出错', it['path'])
        it['status'], it['error'] = 'failed', str(e)
    it['progress'], it['finished'] = 1.0, time.time()
    it['message'] = {'done': '整理完了', 'cancelled': '已停止'}.get(it['status'], '没有完成')


def _stop_batch(batch):
    """额度用完：同一批剩下排着的都不整理了。返回停了几条。"""
    n = 0
    with _lock:
        for x in _items:
            if x.get('batch') == batch and x['status'] == 'waiting':
                x['status'], x['message'], x['finished'] = 'cancelled', '额度用完，这一批停下了', time.time()
                n += 1
    if n:
        log.info('后台整理：额度用完，同一批剩下的 %s 节停下了', n)
    return n


def _public(it):
    return {k: v for k, v in it.items() if k != 'cancel'}


def items():
    """给界面看的队列：排队中、正在整理、最近做完的。"""
    with _lock:
        return [_public(x) for x in _items]


def cancel(item_id=None):
    """停下一条（排队中的直接去掉，正在整理的做完手上这一步就停，已经做完的步骤保留）；不给 id 就全部停。返回停了几条。"""
    n = 0
    with _lock:
        for x in _items:
            if (item_id is None or x['id'] == item_id) and x['status'] in ('waiting', 'running'):
                if x['status'] == 'waiting':
                    x['status'], x['message'], x['finished'] = 'cancelled', '已取消', time.time()
                x['cancel'].set()
                n += 1
    return n


def clear_finished():
    with _lock:
        _items[:] = [x for x in _items if x['status'] in ('waiting', 'running')]


def busy():
    with _lock:
        return any(x['status'] in ('waiting', 'running') for x in _items)
