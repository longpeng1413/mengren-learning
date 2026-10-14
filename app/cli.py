"""猛人学习命令行：给 Claude Code、Codex 等 AI Agent 调用，也可以自己在终端里用。

    mengren list      [--course 课程] [--todo] [--since today] [--kind video|book]
    mengren find      [关键词] [--course 课程] [--index 12] [--todo] [--since today]
    mengren info      <文件路径或 video_id>
    mengren process   <文件路径或 video_id …> | --course 课程 [--index 12] [--todo] [--since today]
                      [--steps a,b] [--skip a,b] [--force] [--lang zh|en] [--provider doubao|openai|siliconflow] [--dry-run]
    mengren run       <步骤> <文件路径或 video_id> [--force]
    mengren steps
    mengren tasks     [--limit 20]
    mengren task      <task_id>
    mengren resume    <task_id>
    mengren cancel    <task_id>

所有命令加 --json 输出机器能读的结果（进度写在 stderr）。process 做的事等于在软件里点「一键整理」
（生成字幕 → AI 校对 → 找关键帧 → 画面复核存疑句 → 翻译（只有英文）→ 课程重点 → 查漏 → 新术语），不剪藏。
说明文档：docs/命令行.md
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_DATA = 0, 1, 2, 3


def unc_of(path):
    r"""C:\Users\x\AppData\Roaming → \\localhost\C$\Users\x\AppData\Roaming"""
    p = str(path)
    if len(p) >= 2 and p[1] == ':':
        return '\\\\localhost\\' + p[0].upper() + '$' + p[2:]
    return p


def use_real_appdata():
    r"""被 Claude 桌面版这类打包应用启动时，写 %APPDATA% 会被 Windows 转存到那个应用的私有文件夹
    （见 core/appdata.py，2026-10 出过「媒体库空白」）。命令行必须读写软件界面看到的那份真实数据，
    所以发现被转存时，改用 \\localhost\C$\… 访问真实位置（走文件共享，不经过转存）。
    返回：None（没被转存）/ 改用的路径 / 'unreachable'（被转存而且访问不到真实位置）。"""
    if os.name != 'nt':
        return None
    appdata = os.environ.get('APPDATA') or ''
    packages = Path(os.environ.get('LOCALAPPDATA') or '') / 'Packages'
    if not appdata or appdata.startswith('\\\\') or not packages.is_dir():
        return None
    d = Path(appdata) / '猛人学习'
    probe = d / f'.转存探测-cli-{os.getpid()}'
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe.write_text('1', encoding='utf-8')
    except OSError:
        return None
    try:
        virtual = any((x / probe.name).exists() for x in packages.glob('*/LocalCache/Roaming/猛人学习'))
    except OSError:
        virtual = False
    finally:
        probe.unlink(missing_ok=True)
    if not virtual:
        return None
    real = unc_of(appdata)
    if not Path(real).is_dir():
        return 'unreachable'
    os.environ['APPDATA'] = real
    return real


def _console_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass


def _setup_log():
    import logging
    from logging.handlers import RotatingFileHandler
    from core.config import LOG_DIR
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(LOG_DIR / 'agent.log', maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    except OSError:
        return
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)-5s [%(threadName)s] %(message)s'))
    root = logging.getLogger()
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    for noisy in ('urllib3', 'httpx', 'httpcore', 'anthropic', 'websocket'):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------- 输出 ----
class Out:
    def __init__(self, as_json, quiet):
        self.as_json = as_json
        self.quiet = quiet
        self._last = (0.0, '')

    def result(self, obj, text=None):
        if self.as_json:
            sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1) + '\n')
        else:
            sys.stdout.write((text if text is not None else json.dumps(obj, ensure_ascii=False, indent=1)) + '\n')
        sys.stdout.flush()

    def note(self, text):
        if not self.quiet:
            sys.stderr.write(text + '\n')
            sys.stderr.flush()

    def progress(self, data):
        if self.quiet:
            return
        st = data.get('stage') or ''
        msg = data.get('message') or ''
        line = f'  [{time.strftime("%H:%M:%S")}] {msg}  {int((data.get("progress") or 0) * 100)}%'
        now = time.time()
        if (st, msg) != self._last[1:] or now - self._last[0] > 5:
            self._last = (now, st, msg)
            self.note(line)


def compact(task):
    from core.workflow import brief  # 命令行和界面用同一份（导入要放在转存检测之后）
    return brief(task)


STATUS_CN = {'completed': '完成', 'failed': '失败', 'cancelled': '已取消', 'skipped': '跳过', 'pending': '没轮到', 'running': '进行中'}


def task_text(c):
    lines = [f'{"✓" if c["success"] else "✗"} {c["title"] or c["path"]}  [{STATUS_CN.get(c["status"], c["status"])}]'
             + (f'  任务 {c["task_id"]}' if c.get('task_id') else '')]
    for s in c['stages']:
        extra = s.get('reason') or s.get('error') or (json.dumps(s['result'], ensure_ascii=False) if s.get('result') else '')
        lines.append(f'    {s["label"]}：{STATUS_CN.get(s["status"], s["status"])}' + (f'（{extra}）' if extra else '')
                     + (f'  重试 {s["retries"]} 次' if s.get('retries') else ''))
    if c.get('error_code') and not c['success']:
        lines.append(f'    原因：{c["message"]}（{c["error_code"]}，{"可以重试" if c.get("retryable") else "需要先处理"}）')
    for w in c.get('warnings') or []:
        lines.append('    注意：' + w)
    return '\n'.join(lines)


def item_text(x):
    m, s = divmod(int(x.get('duration') or 0), 60)
    return f'{x["video_id"]}  {x["course"]}{" / " + x["sub"] if x["sub"] else ""}  #{x["index"]}  {x["name"]}  [{x["state"]}]' + (f'  {m}:{s:02d}' if m or s else '')


# ---------------------------------------------------------------- 命令 ----
def _split(v):
    return [x.strip() for x in str(v or '').replace('，', ',').split(',') if x.strip()]


def cmd_list(a, out, wf):
    items = wf.find(query=getattr(a, 'query', None), course=a.course, index=getattr(a, 'index', None), todo=a.todo, since=a.since, kind=a.kind)
    out.result({'success': True, 'count': len(items), 'items': items},
               '\n'.join(item_text(x) for x in items) + f'\n共 {len(items)} 个' if items else '没有找到')
    return EXIT_OK


def cmd_info(a, out, wf):
    r = wf.info(a.target)
    r = dict(success=True, **r)
    out.result(r)
    return EXIT_OK


def _targets(a, wf):
    if a.targets:
        return [wf.resolve(t) for t in a.targets]
    if not (a.course or a.query or a.todo or a.since):
        raise wf.WorkError('not_found', '没有指定要整理的视频：给出文件路径 / video_id，或用 --course、--todo、--since 选择')
    items = wf.find(query=a.query, course=a.course, index=a.index, todo=a.todo, since=a.since, kind=a.kind)
    if a.limit:
        items = items[:a.limit]
    return [x['path'] for x in items]


def _run_tasks(paths, out, wf, make):
    """一节一节地整理。Ctrl+C：取消正在做的这节，后面的不做了。"""
    results, stop = [], threading.Event()
    for n, p in enumerate(paths, 1):
        if stop.is_set():
            break
        out.note(f'[{n}/{len(paths)}] {Path(p).name}')
        cancel = threading.Event()
        box = {}
        th = threading.Thread(target=lambda: box.setdefault('r', make(p, cancel)), daemon=True, name='agent-task')
        th.start()
        try:
            while th.is_alive():
                th.join(0.5)
        except KeyboardInterrupt:
            out.note('正在取消…')
            cancel.set()
            stop.set()
            th.join()
        c = compact(box.get('r') or {'status': 'failed', 'path': p, 'error': {'error_code': 'internal', 'message': '任务没有返回结果', 'retryable': False}})
        results.append(c)
        out.note(task_text(c))
    ok = all(c['success'] for c in results)
    out.result({'success': ok and bool(results), 'count': len(results), 'succeeded': sum(1 for c in results if c['success']),
                'results': results},
               f'完成 {sum(1 for c in results if c["success"])}/{len(results)} 个')
    return EXIT_OK if ok and results else EXIT_FAILED


def cmd_process(a, out, wf):
    steps, skip = _split(a.steps), _split(a.skip)
    if a.force and not steps:
        raise wf.WorkError('not_supported', '--force 要和 --steps 一起用，写明重做哪几步（例如 --steps summary,review --force），免得把字幕也重新识别、重复花钱')
    paths = _targets(a, wf)
    if not paths:
        out.result({'success': True, 'count': 0, 'results': [], 'message': '没有需要整理的'}, '没有需要整理的')
        return EXIT_OK
    if a.dry_run:
        plan = [{'path': p, 'video_id': wf.video_id(p), 'kind': wf.kind_of(p), 'steps': wf.stages_for(p, steps or None, skip or None),
                 'state': wf.summarize(p)} for p in paths]
        out.result({'success': True, 'count': len(plan), 'dry_run': True, 'plan': plan},
                   '\n'.join(f'{x["video_id"]}  {Path(x["path"]).name}：{"、".join(wf.LABELS[s] for s in x["steps"])}' for x in plan)
                   + f'\n共 {len(plan)} 个（只是预览，没有执行；做过的步骤执行时会自动跳过）')
        return EXIT_OK

    def make(p, cancel):
        return wf.process(p, steps=steps or None, skip=skip or None, redo=steps if a.force else (), lang=a.lang, provider=a.provider,
                          model=a.model, on_progress=out.progress, cancel=cancel)
    return _run_tasks(paths, out, wf, make)


def cmd_run(a, out, wf):
    path = wf.resolve(a.target)

    def make(p, cancel):
        return wf.run_step(p, a.step, force=a.force, lang=a.lang, provider=a.provider, model=a.model, on_progress=out.progress, cancel=cancel)
    return _run_tasks([path], out, wf, make)


def cmd_steps(a, out, wf):
    data = {'success': True, 'video': [{'name': s, 'label': wf.LABELS[s], 'soft': s in wf.SOFT} for s in wf.VIDEO_STAGES],
            'book': [{'name': s, 'label': wf.LABELS[s]} for s in wf.BOOK_STAGES]}
    out.result(data, '视频：' + ' → '.join(f'{s}（{wf.LABELS[s]}）' for s in wf.VIDEO_STAGES)
               + '\n书籍：' + ' → '.join(f'{s}（{wf.LABELS[s]}）' for s in wf.BOOK_STAGES))
    return EXIT_OK


def cmd_tasks(a, out, wf):
    from core import tasks
    rows = [{k: t.get(k) for k in ('task_id', 'status', 'kind', 'title', 'path', 'stage', 'created', 'finished', 'seconds', 'error')}
            for t in tasks.recent(a.limit)]
    out.result({'success': True, 'count': len(rows), 'tasks': rows},
               '\n'.join(f'{r["task_id"]}  {STATUS_CN.get(r["status"], r["status"])}  {r["title"]}  {r["created"]}' for r in rows) or '还没有任务记录')
    return EXIT_OK


def cmd_task(a, out, wf):
    from core import tasks
    d = tasks.load(a.task_id)
    if not d:
        raise wf.WorkError('not_found', '没有这个任务：' + a.task_id)
    c = compact(d)
    out.result(dict(c, record=d), task_text(c))
    return EXIT_OK


def cmd_resume(a, out, wf):
    from core import tasks
    d = tasks.load(a.task_id)
    if not d:
        raise wf.WorkError('not_found', '没有这个任务：' + a.task_id)
    return _run_tasks([d['path']], out, wf, lambda p, cancel: wf.resume(a.task_id, on_progress=out.progress, cancel=cancel))


def cmd_cancel(a, out, wf):
    from core import tasks
    ok = tasks.request_cancel(a.task_id)
    out.result({'success': ok, 'task_id': a.task_id, 'message': '已通知任务停止（会在当前这一小步做完后停下）' if ok else '这个任务没有在运行'})
    return EXIT_OK if ok else EXIT_FAILED


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--json', action='store_true', help='输出 JSON（给程序读）')
    common.add_argument('--quiet', action='store_true', help='不显示进度')
    sel = argparse.ArgumentParser(add_help=False)
    sel.add_argument('--course', help='课程：媒体库里的文件夹名或子文件夹名（包含即可）')
    sel.add_argument('--index', type=int, help='这个课程里的第几个（从 1 开始，和软件左边列表顺序一样）')
    sel.add_argument('--todo', action='store_true', help='只要还没整理的（没有课程重点 / 书还有章节没整理）')
    sel.add_argument('--since', help='只要这天以后新增的：today、yesterday 或 2026-10-14')
    sel.add_argument('--kind', choices=['video', 'book'], help='只要视频（含音频）或只要书')
    run_opts = argparse.ArgumentParser(add_help=False)
    run_opts.add_argument('--lang', choices=['zh', 'en'], help='视频语言（默认按字幕文件、老师档案、上次的选择）')
    run_opts.add_argument('--provider', choices=['doubao', 'openai', 'siliconflow'], help='识别服务（默认用软件设置里的）')
    run_opts.add_argument('--model', choices=['cloud', 'local'], default='cloud', help='识别方式：cloud 云端（local 本地模型还没接入）')

    p = argparse.ArgumentParser(prog='mengren', description='猛人学习命令行（给 AI Agent 调用）')
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('list', parents=[common, sel], help='列出媒体库里的视频和书')
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser('find', parents=[common, sel], help='查找视频')
    s.add_argument('query', nargs='?', help='关键词（文件名或路径里包含即可），也可以是 video_id')
    s.set_defaults(fn=cmd_list)
    s = sub.add_parser('info', parents=[common], help='一个视频的详细情况（不改任何东西）')
    s.add_argument('target', help='文件路径或 video_id')
    s.set_defaults(fn=cmd_info)
    s = sub.add_parser('process', parents=[common, sel, run_opts], help='整理（等于在软件里点「一键整理」，不剪藏）')
    s.add_argument('targets', nargs='*', help='文件路径或 video_id（可以多个）；不写就用 --course / --todo / --since 选择')
    s.add_argument('--query', help='再按关键词筛选')
    s.add_argument('--steps', help='只做这几步，逗号分隔（mengren steps 查看全部）')
    s.add_argument('--skip', help='跳过这几步，逗号分隔')
    s.add_argument('--force', action='store_true', help='--steps 里的步骤做过也重做')
    s.add_argument('--limit', type=int, help='最多整理几个')
    s.add_argument('--dry-run', action='store_true', help='只看会整理哪些、做哪几步，不执行')
    s.set_defaults(fn=cmd_process)
    s = sub.add_parser('run', parents=[common, run_opts], help='只做一步')
    s.add_argument('step', help='步骤名（mengren steps 查看）')
    s.add_argument('target', help='文件路径或 video_id')
    s.add_argument('--force', action='store_true', help='做过也重做')
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser('steps', parents=[common], help='列出所有步骤')
    s.set_defaults(fn=cmd_steps)
    s = sub.add_parser('tasks', parents=[common], help='最近的任务')
    s.add_argument('--limit', type=int, default=20)
    s.set_defaults(fn=cmd_tasks)
    s = sub.add_parser('task', parents=[common], help='一个任务的详细记录')
    s.add_argument('task_id')
    s.set_defaults(fn=cmd_task)
    s = sub.add_parser('resume', parents=[common], help='接着做没做完的任务（做完的步骤自动跳过）')
    s.add_argument('task_id')
    s.set_defaults(fn=cmd_resume)
    s = sub.add_parser('cancel', parents=[common], help='让正在运行的任务停下')
    s.add_argument('task_id')
    s.set_defaults(fn=cmd_cancel)
    return p


def main(argv=None):
    _console_utf8()
    a = parser().parse_args(argv)
    out = Out(a.json, a.quiet)
    redirected = use_real_appdata()
    if redirected == 'unreachable':
        out.result({'success': False, 'error_code': 'data', 'retryable': False,
                    'message': '这个命令是从 Claude 桌面版这类应用里启动的，Windows 把它的设置读写转到了别的文件夹，'
                               '而且访问不到真正的设置（\\\\localhost\\C$）。请在普通的终端（PowerShell / Windows Terminal）里运行'})
        return EXIT_DATA
    _setup_log()
    from core import workflow as wf
    from core.log import log
    if redirected:
        log.info('命令行被转存，改用真实数据文件夹：%s', redirected)
    log.info('命令行：%s', ' '.join(sys.argv[1:] if argv is None else argv))
    if a.cmd in ('process', 'run', 'resume'):  # 和软件启动时一样清理缓存（音频 3 天、截图 30 天没用的），命令行的缓存不会越积越多
        from core import cache
        try:
            cache.cleanup()
        except Exception:  # noqa: BLE001  清理失败不影响整理
            log.exception('清理缓存出错')
    try:
        return a.fn(a, out, wf)
    except wf.WorkError as e:
        out.result({'success': False, 'error_code': e.code, 'retryable': e.retryable, 'message': str(e)}, f'✗ {e}')
        return EXIT_FAILED
    except Exception as e:  # noqa: BLE001
        from core import tasks
        err = tasks.error_dict(e)
        log.exception('命令行出错')
        out.result(dict(success=False, **err), f'✗ {err["message"]}')
        return EXIT_FAILED


if __name__ == '__main__':
    sys.exit(main())
