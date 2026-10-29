"""媒体库：左侧列表（2026-10-14 改成树形，按实际层级显示）。只记路径和用户的设置，显示时读磁盘上的最新情况。

library.json（%APPDATA%\\猛人学习）：
- folders：加进来的文件夹（最上层）[{path, name, teacher, collapsed, added}]；
- files：单独加进来的文件 [路径]；
- order：最上层的顺序 [路径]（文件夹和单个文件混在一起，默认按加入的先后，用户可以拖动）；
- dirs：下层文件夹的设置 {小写路径: {teacher, collapsed, summary, rule}}（绑定的老师、是否收起、写不写课程重点、整理规则）；
- orders：用户在某个文件夹里拖出来的顺序 {小写路径: [文件名或文件夹名…]}。

排列规则（用户 2026-10-14 定的）：
- 最上层按加入媒体库的先后；
- 文件夹里同一层：带数字序号的（1、 01. 第3讲 三、…）按序号排在前面，没有序号的按下载（创建）时间排在后面；
- 没有序号的可以拖到任意位置，软件记住；带序号的之间永远按序号（拖乱了界面会提示改名）；
- 每一层文件夹都能绑定老师，没绑定的沿用最近一层上级的（folder_teacher）。
- 每一层文件夹都能设「不写课程重点」（summary: 'off'；子文件夹可以设回 'on'；空着沿用上层，最上层默认写），
  灵修类这种逐字稿课程只识别、校对，直接蒸馏（2026-10-28 用户要求按文件夹选，不按老师；folder_no_summary）。
- 每一层文件夹都能记一套整理规则 rule {steps, lang, doubao_model, doubao_account}：「整个文件夹排进后台整理」时定的，
  下层沿用；以后右键单节课「排进后台整理」也按它（folder_rule）。
识别的文件：音视频（media.MEDIA_EXT）和文档（book.LIB_EXT）；字幕、txt、软件自己的 json 等不列。
「_」或「.」开头的文件夹（例如「_关键帧缓存」）不列。"""
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import config, doc as docs
from .book import LIB_EXT
from .log import log
from .media import AUDIO_EXT, MEDIA_EXT

MAX_FILES = 3000
SKIP_EXT = {'.json', '.bak', '.tmp', '.part', '.ini', '.db', '.lnk', '.srt', '.ass', '.vtt', '.lrc', '.txt', '.md'}  # 不提示「跳过了」的
CN_DIGIT = {c: i for i, c in enumerate('零一二三四五六七八九')} | {'〇': 0}


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _inside(child_key, parent_key):
    return child_key.startswith(parent_key.rstrip('\\/') + os.sep)


def _cn_int(s):
    """中文数字（到几百）→ 整数。"""
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s.replace('两', '二'):
        if ch in CN_DIGIT:
            cur = CN_DIGIT[ch]
        elif ch == '十':
            total += (cur or 1) * 10
            cur = 0
        elif ch == '百':
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def number_key(name):
    """名字开头的序号 → 元组（「1、」(1,)，「1.2 」(1, 2)，「第三讲」(3,)）；没有序号返回 None。"""
    s = str(name or '').strip()
    m = re.match(r'^[\[【(（]?\s*(?:第\s*)?(\d+(?:[.．_\-]\d+)*)', s)
    if m:
        return tuple(int(x) for x in re.split(r'[.．_\-]', m.group(1)) if x)
    m = re.match(r'^[\[【(（]?\s*(?:第\s*)?([零〇一二三四五六七八九十百两]+)\s*[、.．章讲节课期集篇部】\])）\s]', s)
    if m:
        return (_cn_int(m.group(1)),)
    return None


def _default_key(e):
    nk = number_key(e['name'])
    return (0, nk, _natural(e['name'])) if nk else (1, e.get('ctime', 0), _natural(e['name']))


def arrange(entries, custom=None):
    """同一层的排列：带序号的按序号在前，没序号的按创建时间在后；有用户拖过的顺序就按它，
    但带序号的之间永远按序号（占着的位置不变，里面的东西按序号重新放）。entries：[{name, ctime, …}]。"""
    base = sorted(entries, key=_default_key)
    if custom:
        pos = {n: i for i, n in enumerate(custom)}
        known = sorted([e for e in base if e['name'] in pos], key=lambda e: pos[e['name']])
        base = known + [e for e in base if e['name'] not in pos]  # 新出现的放在后面
    slots = [i for i, e in enumerate(base) if number_key(e['name'])]
    nums = sorted((base[i] for i in slots), key=_default_key)
    for i, e in zip(slots, nums):
        base[i] = e
    return base


# ------------------------------------------------------------------ 读写 ----
def load():
    lib = config.load_library()
    lib.setdefault('dirs', {})
    lib.setdefault('orders', {})
    known = [f['path'] for f in lib['folders'] if f.get('path')] + list(lib['files'])
    order = [p for p in lib.get('order') or [] if any(_key(p) == _key(k) for k in known)]
    for p in known:  # 以前的版本没有 order：按文件夹在前、单个文件在后的原顺序
        if not any(_key(p) == _key(o) for o in order):
            order.append(p)
    lib['order'] = order
    return lib


def save(lib):
    config.save_library(lib)


def _status(path, positions, env=None):
    """一个文件在列表里要显示的状态：有没有字幕、剪藏过没有、看到哪里。
    给了 env（_scan_env）时还算出 pending：按一键整理默认的步骤还要做哪几步（用已经读出来的字幕文件，不多读文件）。"""
    item = {'path': path, 'name': Path(path).stem, 'ext': Path(path).suffix.lower()[1:],
            'kind': 'book' if Path(path).suffix.lower() in LIB_EXT else 'media'}  # 左栏「书籍 / 课程」分开列（2026-10-29）
    side = docs.stored_file(path)
    d = None
    if side is not None:
        try:
            d = json.loads(side.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            d = None
    if d and d.get('kind') == 'book':
        chs = d.get('chapters') or []
        item['book'] = {'done': sum(1 for c in chs if (c.get('md') or '').strip()), 'total': len(chs)}
        item['clipped'] = bool(d.get('clip'))
        item['teacher'] = d.get('teacher') or ''
    elif d:
        item['subs'] = len(d.get('segments') or [])
        item['summary'] = bool((d.get('summary') or '').strip())
        item['clipped'] = bool(d.get('clip'))
        item['stale'] = docs.clip_stale(d)
        item['summary_changes'] = docs.summary_changes(d)  # 生成课程重点后字幕改了几句
        item['reviewed'] = bool(d.get('reviewed'))
        item['doubts'] = len(d.get('doubts') or [])  # 还没点「已确认」的存疑句，媒体库标出来好人工校对
        item['teacher'] = d.get('teacher') or ''
        item['duration'] = d.get('duration') or 0
    if env is not None:
        from . import workflow  # workflow 也用 library，放这里免得互相引用
        item['no_summary'] = _nearest(env['summary'], path) == 'off'
        item['pending'] = workflow.pending_in_scan(path, d, env['facts'], _nearest(env['binds'], path), item['no_summary'])
    pos = positions.get(os.path.abspath(path).lower())
    if pos:
        item['pos'] = pos.get('t', 0)
        item['duration'] = item.get('duration') or pos.get('d', 0)
    return item


def _wanted(name):
    return Path(name).suffix.lower() in MEDIA_EXT or Path(name).suffix.lower() in LIB_EXT


def _listdir(path, stats=None):
    """一个文件夹里要列的子文件夹和文件（不递归）。stats 给了就顺便统计跳过的文件。"""
    try:
        entries = list(os.scandir(path))
    except OSError:
        return [], []
    pdf_stems = {Path(e.name).stem.lower() for e in entries if e.name.lower().endswith('.pdf')}
    dirs, files = [], []
    for e in entries:
        try:
            if e.is_dir():
                if not e.name.startswith(('.', '_')):  # 「_关键帧缓存」这类辅助文件夹不列
                    dirs.append(e)
                continue
            ctime = e.stat().st_ctime
        except OSError:
            continue
        ext = Path(e.name).suffix.lower()
        if ext == '.docx' and Path(e.name).stem.lower() in pdf_stems:
            continue  # 和 PDF 同名的 Word 是它的正文参考，不单独列
        if _wanted(e.name):
            files.append({'name': e.name, 'path': e.path, 'ctime': ctime})
        elif stats is not None and ext not in SKIP_EXT and not e.name.lower().endswith(docs.SUFFIX):
            stats['skipped'][ext or '（没有扩展名）'] = stats['skipped'].get(ext or '（没有扩展名）', 0) + 1
    return dirs, files


def _ctime(path):
    try:
        return os.stat(path).st_ctime
    except OSError:
        return 0


def _tree(path, lib, positions, budget, top=False, env=None, inherit=''):
    """一个文件夹的树：{type, path, name, top, teacher, collapsed, summary, no_summary, missing, count, num, children}。
    summary 是这一层自己的设置（'on' / 'off' / 空），no_summary 是算上上层之后的结果；inherit 是上层的结果。没有任何可列文件的子文件夹不显示。"""
    k = _key(path)
    conf = _conf(lib, k)
    own = conf.get('summary') or ''
    node = {'type': 'folder', 'path': path, 'name': Path(path).name or path, 'top': top, 'teacher': conf.get('teacher') or '',
            'collapsed': bool(conf.get('collapsed')), 'summary': own, 'no_summary': (own or inherit) == 'off',
            'missing': not os.path.isdir(path), 'count': 0, 'children': []}
    if node['missing']:
        return node
    dirs, files = _listdir(path)
    entries = []
    for d in dirs:
        if budget[0] <= 0:
            break
        child = _tree(d.path, lib, positions, budget, env=env, inherit=own or inherit)
        if child['count']:
            entries.append(dict(child, ctime=_ctime(d.path), fname=d.name))
    for f in files:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        entries.append(dict(_status(f['path'], positions, env), type='file', ctime=f['ctime'], fname=f['name']))
    ordered = arrange([dict(e, name=e['fname'], label=e['name']) for e in entries], lib['orders'].get(k))
    node['children'] = [dict(e, name=e['label'], num=bool(number_key(e['fname']))) for e in ordered]
    for e in node['children']:
        e.pop('label', None)
    node['count'] = sum(c['count'] if c['type'] == 'folder' else 1 for c in node['children'])
    return node


def view():
    """整个媒体库（树）：最上层是加进来的文件夹和单个文件，按 order 排。"""
    lib = load()
    positions = config.get_positions()
    budget = [MAX_FILES]
    env = _scan_env(lib)
    by_key = {_key(f['path']): f for f in lib['folders']}
    files = {_key(p): p for p in lib['files']}
    out = []
    for p in lib['order']:
        k = _key(p)
        if k in by_key:
            f = by_key[k]
            node = _tree(f['path'], lib, positions, budget, top=True, env=env, inherit=_nearest(env['summary'], f['path']) if env else '')
            node.update(name=f.get('name') or node['name'], teacher=f.get('teacher') or '', collapsed=bool(f.get('collapsed')))
            out.append(node)
        elif k in files and os.path.isfile(files[k]):
            out.append(dict(_status(files[k], positions, env), type='file', top=True, fname=Path(files[k]).name, num=False))
    return out


def flat_groups():
    """旧格式（给 Agent 的 workflow 用）：每个最上层文件夹一组，里面的文件按显示顺序摊平，sub 是所在的子文件夹；单个文件放最后一组。"""
    groups, singles = [], []
    for node in view():
        if node['type'] == 'file':
            singles.append(node)
            continue
        items = []

        def walk(n, rel):
            for c in n['children']:
                if c['type'] == 'folder':
                    walk(c, (rel + '/' if rel else '') + c['name'])
                else:
                    items.append(dict(c, sub=rel))
        walk(node, '')
        groups.append({'type': 'folder', 'path': node['path'], 'name': node['name'], 'teacher': node['teacher'],
                       'collapsed': node['collapsed'], 'missing': node['missing'], 'items': items})
    if singles:
        groups.append({'type': 'files', 'path': '', 'name': '单个文件', 'teacher': '', 'collapsed': False, 'missing': False, 'items': singles})
    return groups


def scan(folder):
    """文件夹（含子文件夹）里所有要列的文件，按显示顺序。"""
    lib = load()
    node = _tree(os.path.abspath(folder), lib, {}, [MAX_FILES], top=True)
    out = []

    def walk(n):
        for c in n['children']:
            walk(c) if c['type'] == 'folder' else out.append(c['path'])
    walk(node)
    return out


def scan_stats(folder):
    """加进来的东西有多少：视频、音频、文档各几个，跳过了哪些格式。"""
    stats = {'video': 0, 'audio': 0, 'doc': 0, 'skipped': {}}
    todo = [folder]
    while todo:
        d = todo.pop()
        dirs, files = _listdir(d, stats)
        todo += [x.path for x in dirs]
        for f in files:
            ext = Path(f['name']).suffix.lower()
            stats['doc' if ext in LIB_EXT else ('audio' if ext in AUDIO_EXT else 'video')] += 1
    return stats


# ------------------------------------------------------------------ 加入 / 移除 ----
def add_folder(path, merge=None):
    """加入一个文件夹。返回 {status: added | exists | inside | need_merge, …}。
    need_merge：媒体库里已经有它下面的文件夹或文件，要问用户：合并（merge=True，原来的并进来，设置带过来）还是重复加入（merge=False）。"""
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError('找不到这个文件夹：' + path)
    lib = load()
    k = _key(path)
    for f in lib['folders']:
        fk = _key(f['path'])
        if fk == k:
            return {'status': 'exists', 'path': f['path']}
        if _inside(k, fk):
            return {'status': 'inside', 'path': path, 'parent': f['path'], 'parent_name': f.get('name') or Path(f['path']).name}
    children = [f for f in lib['folders'] if _inside(_key(f['path']), k)]
    singles = [p for p in lib['files'] if _inside(_key(p), k)]
    if (children or singles) and merge is None:
        return {'status': 'need_merge', 'path': path, 'children': [f.get('name') or Path(f['path']).name for f in children], 'files': len(singles)}
    if merge:
        for f in children:
            conf = lib['dirs'].setdefault(_key(f['path']), {})
            if f.get('teacher'):
                conf['teacher'] = f['teacher']
            for name in ('summary', 'rule'):
                if f.get(name):
                    conf[name] = f[name]
            conf['collapsed'] = bool(f.get('collapsed'))
        gone = {_key(f['path']) for f in children} | {_key(p) for p in singles}
        lib['folders'] = [f for f in lib['folders'] if _key(f['path']) not in gone]
        lib['files'] = [p for p in lib['files'] if _key(p) not in gone]
        lib['order'] = [p for p in lib['order'] if _key(p) not in gone]
    lib['folders'].append({'path': path, 'name': Path(path).name, 'teacher': '', 'collapsed': False, 'added': time.time()})
    lib['order'].append(path)
    save(lib)
    return dict(status='added', path=path, merged=bool(merge and (children or singles)), **scan_stats(path))


def add_files(paths):
    """加入单个文件。已经在某个加进来的文件夹里的、已经加过的跳过。"""
    lib = load()
    known = {_key(p) for p in lib['files']}
    folders = [_key(f['path']) for f in lib['folders']]
    n = inside = 0
    for p in paths:
        p = os.path.abspath(p)
        k = _key(p)
        if not (os.path.isfile(p) and _wanted(p)) or k in known:
            continue
        if any(_inside(k, fk) for fk in folders):
            inside += 1
            continue
        lib['files'].append(p)
        lib['order'].append(p)
        known.add(k)
        n += 1
    save(lib)
    return {'added': n, 'inside': inside}


def remove(kind, path):
    lib = load()
    k = _key(path)
    if kind == 'folder':
        lib['folders'] = [f for f in lib['folders'] if _key(f['path']) != k]
        covered = [_key(f['path']) for f in lib['folders']]
        if not any(_inside(k, c) for c in covered):  # 这个文件夹里的设置、顺序没人用了，一起清掉
            for store in (lib['dirs'], lib['orders']):
                for x in [x for x in store if x == k or _inside(x, k)]:
                    store.pop(x, None)
    else:
        lib['files'] = [p for p in lib['files'] if _key(p) != k]
    lib['order'] = [p for p in lib['order'] if _key(p) != k]
    save(lib)


def set_folder(path, **changes):
    """改一个文件夹的设置：teacher、collapsed、name（只有最上层）、no_summary（True / False：这里的课写不写课程重点）、rule（整理规则）。"""
    lib = load()
    if path == '':  # 以前的「单个文件」一组
        save(lib)
        return
    k = _key(path)
    if 'no_summary' in changes:  # 和上层一样就不单独记（以后改上层，这里跟着变）
        want = 'off' if changes.pop('no_summary') else 'on'
        changes['summary'] = '' if want == (_nearest(_summary_binds(lib), path) or 'on') else want
    top = next((f for f in lib['folders'] if _key(f['path']) == k), None)
    if top is not None:
        for name in ('teacher', 'collapsed', 'name', 'summary', 'rule'):
            if name in changes:
                top[name] = changes[name]
    else:
        conf = lib['dirs'].setdefault(k, {})
        for name in ('teacher', 'collapsed', 'summary', 'rule'):
            if name in changes:
                conf[name] = changes[name]
        for name in ('summary', 'rule'):
            if not conf.get(name):
                conf.pop(name, None)
        if not any(conf.get(x) for x in ('teacher', 'collapsed', 'summary', 'rule')):
            lib['dirs'].pop(k, None)
    save(lib)


def reorder(parent, names):
    """用户拖动后的顺序。parent 为空：最上层（names 是路径）；否则是那个文件夹里的名字。"""
    lib = load()
    if not parent:
        keys = {_key(p): p for p in lib['order']}
        new = [keys[_key(n)] for n in names if _key(n) in keys]
        lib['order'] = new + [p for p in lib['order'] if p not in new]
    else:
        lib['orders'][_key(parent)] = [str(n) for n in names]
    save(lib)


def _scan_env(lib):
    """列整个媒体库时只准备一次的东西：设置、老师档案、各层文件夹绑定的老师。"""
    from . import workflow
    try:
        facts = workflow.scan_facts()
    except Exception:  # noqa: BLE001  读设置出错时不显示「未整理完」，列表照样能用
        log.exception('媒体库：准备「还差哪几步」的判断出错')
        return None
    return {'facts': facts, 'binds': _teacher_binds(lib), 'summary': _summary_binds(lib)}


def _conf(lib, k):
    """一层文件夹的设置：最上层的在 folders 里，下层的在 dirs 里。"""
    return next((f for f in lib['folders'] if _key(f['path']) == k), None) or lib['dirs'].get(k, {})


def _binds(lib, name):
    binds = {_key(f['path']): f.get(name) for f in lib['folders'] if f.get(name)}
    binds.update({k: v.get(name) for k, v in lib['dirs'].items() if v.get(name)})
    return binds


def _teacher_binds(lib):
    return _binds(lib, 'teacher')


def _summary_binds(lib):
    return _binds(lib, 'summary')


def folder_teacher(media_path):
    """视频所在文件夹绑定的老师：自己没绑定就沿用最近一层上级的（加进来的文件夹和下层文件夹都算）。"""
    return _nearest(_teacher_binds(load()), media_path)


def folder_no_summary(media_path):
    """视频所在文件夹（或最近一层设过的上级）设了「不写课程重点」。"""
    return _nearest(_summary_binds(load()), media_path) == 'off'


def folder_rule(media_path):
    """视频所在文件夹（或最近一层设过的上级）的整理规则，没有就是 None。"""
    return _nearest(_binds(load(), 'rule'), media_path) or None


def folder_settings(folder):
    """一个文件夹自己的设置加上沿用上层的：{teacher, no_summary, rule}（「整个文件夹排进后台整理」对话框用）。"""
    lib = load()
    conf = _conf(lib, _key(folder))
    at = lambda name: conf.get(name) or _nearest(_binds(lib, name), folder)  # noqa: E731
    return {'teacher': at('teacher') or '', 'no_summary': at('summary') == 'off', 'rule': at('rule') or None}


def _nearest(binds, media_path):
    """从文件（文件夹）所在的上一层往上找，最近一层设过的值；都没设返回空。"""
    d = os.path.dirname(_key(media_path))
    while True:
        if binds.get(d):
            return binds[d]
        parent = os.path.dirname(d)
        if parent == d:
            return ''
        d = parent


# ------------------------------------------------------------------ 改名 ----
BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def rename(path, new_name):
    """在软件里改文件或文件夹的名字：电脑上的文件、旁边的字幕文件、PDF 同名的 Word、标题、播放进度、
    媒体库里的设置和顺序一起改。Obsidian 里已经剪藏的笔记不改名（用户 2026-10-14 决定：重新剪藏也保留原文件名）。
    文件只改名字部分，扩展名不变。返回新路径。"""
    from . import tasks
    path = os.path.abspath(path)
    is_dir = os.path.isdir(path)
    if not is_dir and not os.path.isfile(path):
        raise ValueError('找不到这个文件：' + path)
    name = str(new_name or '').strip().rstrip('. ')
    if not name or name in ('.', '..') or BAD_NAME.search(name) or len(name) > 200:
        raise ValueError('名字不能为空，也不能包含 \\ / : * ? " < > | 这些符号')
    if not is_dir:
        ext = Path(path).suffix
        if name.lower().endswith(ext.lower()):
            name = name[:-len(ext)].rstrip()
        name += ext
    new = os.path.join(os.path.dirname(path), name)
    if new == path:
        return path
    if os.path.exists(new) and _key(new) != _key(path):
        raise ValueError('这个位置已经有同名的' + ('文件夹' if is_dir else '文件') + '：' + name)
    busy = tasks.busy_under(path) if is_dir else tasks.busy(docs.media_key(path))
    if busy:
        raise ValueError('后台正在整理' + ('这个文件夹里的课' if is_dir else '这节课') + '，整理完再改名')
    companion = None
    if not is_dir and path.lower().endswith('.pdf'):
        from .book import companion_docx
        companion = companion_docx(path)
    try:
        os.rename(path, new)
    except OSError as e:
        raise ValueError(f'改名失败：这个{"文件夹里有文件" if is_dir else "文件"}正被占用（可能正在播放，或被别的软件打开），关掉后再试（{e}）')
    if is_dir:
        for root, _, files in os.walk(new):
            for f in files:
                if _wanted(f):
                    nf = os.path.join(root, f)
                    _moved_file(path + nf[len(new):], nf, sidecars_moved=True)
    else:
        _moved_file(path, new)
        if companion and Path(companion).stem.lower() == Path(path).stem.lower():
            try:
                os.rename(companion, str(Path(new).with_suffix('.docx')))
            except OSError:
                pass
    _moved_library(path, new, is_dir)
    config.move_paths(path, new)
    return new


def _moved_file(old, new, sidecars_moved=False):
    """一个文件换了路径：字幕文件跟着换名字，里面记的路径、标题跟着改；以前的路径记下来（剪藏时用它找到原来的笔记）。"""
    if not sidecars_moved:
        for a, b in ((docs.sidecar(old), docs.sidecar(new)), (docs.old_sidecar(old), docs.old_sidecar(new))):
            for x, y in ((a, b), (a.with_name(a.name + '.bak'), b.with_name(b.name + '.bak'))):
                if x.exists() and not y.exists():
                    try:
                        os.replace(x, y)
                    except OSError:
                        pass
    fo, fn = docs.fallback(old), docs.fallback(new)  # 写不进视频旁边时存在 %APPDATA% 里的
    if fo.exists() and not fn.exists():
        try:
            shutil.move(str(fo), str(fn))
        except OSError:
            pass
    d = docs.load(new)
    if not d:
        return
    d.pop('_stored', None)
    hist = [x for x in d.get('media_history') or [] if _key(x) != _key(new)]
    if _key(old) not in {_key(x) for x in hist}:
        hist.append(old)
    d['media_history'] = hist[-10:]
    d['media'] = new
    so, sn = Path(old).stem, Path(new).stem
    if d.get('kind') == 'book':
        mo, mn = re.match(r'^《(.+?)》\s*(.*)$', so), re.match(r'^《(.+?)》\s*(.*)$', sn)
        if (d.get('title') or '') in (so, mo.group(1) if mo else so):
            d['title'] = mn.group(1) if mn else sn
        if mo and mn and (d.get('author') or '') == mo.group(2).strip():
            d['author'] = mn.group(2).strip()
    elif (d.get('title') or '') == so:
        d['title'] = sn
    docs.save(new, d)


def _moved_library(old, new, is_dir):
    """媒体库里记的路径、下层文件夹的设置和拖好的顺序跟着改。"""
    lib = load()
    ok, ol = _key(old), old

    def fix(p):
        k = _key(p)
        if k == ok:
            return new
        if is_dir and _inside(k, ok):
            return new + os.path.abspath(p)[len(ol):]
        return p
    for f in lib['folders']:
        np_ = fix(f['path'])
        if np_ != f['path']:
            if (f.get('name') or '') == Path(f['path']).name:
                f['name'] = Path(np_).name
            f['path'] = np_
    lib['files'] = [fix(p) for p in lib['files']]
    lib['order'] = [fix(p) for p in lib['order']]
    for store in (lib['dirs'], lib['orders']):
        for k in list(store):
            if is_dir and (k == ok or _inside(k, ok)):
                store[_key(new) + k[len(ok):]] = store.pop(k)
    parent = _key(os.path.dirname(old))
    if parent in lib['orders']:
        lib['orders'][parent] = [Path(new).name if n == Path(old).name else n for n in lib['orders'][parent]]
    save(lib)
