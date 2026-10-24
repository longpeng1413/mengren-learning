"""老师工作台（2026-10-24，用户要求「AI 按老师画像，忠实地用老师的知识和我对话」）。

一位「老师」= Obsidian 里 `04、外部知识转化库/老师名/` 文件夹，里面有蒸馏好的：
- `…观点库….md`：逐节提取的观点表（编号 S01-05、出处 S01 05:19、类型），开头「来源」表把 S01… 对应到课程笔记；
- `…老师画像….md`：给 AI 的扮演规则和老师的信念、思路、比喻、风格（第一节就是规则：出处怎么标、超出课程怎么标）；
- `…课程蒸馏….md`（可选）：整体体系。
这些文件以 Obsidian 里的为准，软件每次读最新的，不另存副本（和术语表文件一样）。

对话时交给 AI 的资料：老师画像 + 课程蒸馏 + 观点库（少就全给，多就挑和问题相关的）+ 和问题相关的课程原文片段
（来自猛人学习的字幕文件，带时间），所以回答里的出处能在界面上点开、跳到那节课那一秒。
挑「相关」用的是本机的字词重合打分，不联网、不花钱。

对话记录存在 `%APPDATA%\\猛人学习\\mentors\\老师id\\chats\\`（用户的资料，不自动删；界面上能删单个对话或全部）；
用户点「收藏」的回答追加到老师文件夹里的 `对话精选.md`，写之前备份到 `backups\\对话精选`（留 30 份）。
"""
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import config, doc as docs

MENTOR_DIR = config.DATA_DIR / 'mentors'
FAVORITES = '对话精选.md'
HISTORY_TURNS = 12          # 带上最近几条对话
ATOMS_ALL_LIMIT = 40000     # 观点库不超过这么多字就整份交给 AI，超过就挑相关的
ATOMS_PICK = 40
WINDOWS_PICK = 10
WINDOW_SECONDS = 45         # 字幕按约 45 秒一段切成原文片段
SRC_RE = re.compile(r'^\|\s*(S\d{2,3})\s*\|\s*(.*?)\s*\|\s*(\[\[.+?\]\]|[^|\n]*?)\s*\|', re.M)  # 第三格是 [[笔记]]，没剪藏的课是说明文字
ROW_RE = re.compile(r'^\|\s*(S\d{2,3}-[A-Za-z0-9]+)\s*\|(.+)\|\s*$', re.M)
TIME_RE = re.compile(r'(S\d{2,3})\s+(\d{1,2}:\d{2}(?::\d{2})?)')


class MentorError(Exception):
    pass


# ------------------------------------------------------------------ 工具 ----
def _ts(t):
    t = int(t)
    return f'{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}' if t >= 3600 else f'{t // 60:02d}:{t % 60:02d}'


def parse_time(s):
    parts = [int(x) for x in str(s).split(':')]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _norm(p):
    return os.path.normcase(os.path.abspath(str(p or '')))


def _same(a, b):
    return _norm(a) == _norm(b)


def _read(path):
    return config.read_text_file(path) if path and Path(path).is_file() else ''


def _pick_file(folder, word):
    hits = sorted(p for p in Path(folder).glob('*.md') if word in p.name)
    return hits[0] if hits else None


def files_of(folder):
    """老师文件夹里的三份文件（找不到的是 None）。"""
    return {'persona': _pick_file(folder, '老师画像'), 'atoms': _pick_file(folder, '观点库'), 'distill': _pick_file(folder, '课程蒸馏')}


def parse_sources(md):
    """观点库开头的来源表 → [{sid, title, note}]（没剪藏的课 note 是空的）"""
    out = []
    for m in SRC_RE.finditer(md):
        link = re.match(r'\[\[(.+?)\]\]', m.group(3))
        out.append({'sid': m.group(1), 'title': m.group(2).strip(), 'note': link.group(1).split('|')[0].strip() if link else ''})
    return out


def parse_atoms(md):
    """观点库里的每一行：观点表（编号 | 观点 | 条件 | 出处 | 类型）和老师自述表（编号 | 内容 | 出处）。"""
    out = {}
    for m in ROW_RE.finditer(md):
        cells = [c.strip() for c in m.group(2).split('|')]
        aid = m.group(1)
        if len(cells) >= 4:
            text, cond, src, kind = cells[0], cells[1], cells[2], cells[3]
        elif len(cells) >= 2:
            text, cond, src, kind = cells[0], '', cells[1], '自述'
        else:
            continue
        tm = TIME_RE.search(src)
        out[aid] = {'id': aid, 'sid': aid.split('-')[0], 'text': text, 'cond': '' if cond in ('—', '-') else cond, 'src': src, 'kind': kind,
                    't': parse_time(tm.group(2)) if tm else None}
    return out


def _frontmatter_video(note_path):
    head = _read(note_path)[:3000]
    m = re.search(r'^视频文件:\s*"?(.+?)"?\s*$', head, re.M)
    return os.path.normpath(m.group(1)) if m else ''


def _find_notes(vault, names):
    """在 Obsidian 库里按文件名找课程笔记 → {名字: 路径}。整个库只翻一遍、找齐就停；
    先翻设置里的剪藏文件夹（课程笔记一般在那里）。以前每节课翻一遍整个库，导入 5 节课要 8 秒。"""
    want = {(n + '.md').lower(): n for n in names if n}
    found = {}
    if not vault or not os.path.isdir(vault) or not want:
        return found
    sub = (config.load().get('obsidian') or {}).get('folder') or ''
    first = os.path.join(vault, sub) if sub and os.path.isdir(os.path.join(vault, sub)) else ''
    for top in [first, vault] if first else [vault]:
        for root, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (top == vault and first and _same(os.path.join(root, d), first))]
            for f in files:
                n = want.get(f.lower())
                if n and n not in found:
                    found[n] = Path(root) / f
            if len(found) == len(want):
                return found
    return found


def _find_note(vault, name):
    return _find_notes(vault, [name]).get(name)


def _vault_of(folder):
    """老师文件夹所在的 Obsidian 库：设置里填了就用；没填就往上找有 .obsidian 的那一层。"""
    v = (config.load().get('obsidian') or {}).get('vault') or ''
    if v and os.path.normcase(os.path.abspath(folder)).startswith(os.path.normcase(os.path.abspath(v))):
        return v
    p = Path(folder).resolve()
    for q in [p, *p.parents]:
        if (q / '.obsidian').is_dir():
            return str(q)
    return v


# ----------------------------------------------------------------- 老师 ----
def _mdir(mid):
    if not re.fullmatch(r'[0-9a-f]{10}', str(mid or '')):
        raise MentorError('没有这位老师')
    return MENTOR_DIR / mid


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def list_mentors(chats=True):
    out = []
    if MENTOR_DIR.is_dir():
        for d in sorted(MENTOR_DIR.iterdir()):
            m = _load_json(d / 'mentor.json')
            if m:
                out.append(dict(m, chats=list_chats(m['id'])) if chats else m)
            for t in d.glob('**/*.tmp'):  # 写到一半退出留下的临时文件
                if time.time() - t.stat().st_mtime > 86400:
                    t.unlink(missing_ok=True)
    return sorted(out, key=lambda m: m.get('created', ''))


def update(mid, **kw):
    m = get(mid)
    m.update(kw)
    _save_json(_mdir(mid) / 'mentor.json', m)
    return m


def get(mid):
    m = _load_json(_mdir(mid) / 'mentor.json')
    if not m:
        raise MentorError('没有这位老师')
    return m


def add(folder):
    """导入一个已经蒸馏好的老师文件夹。要有观点库和老师画像；来源表里的课程笔记要能在 Obsidian 库里找到。"""
    folder = os.path.abspath(str(folder or ''))
    if not os.path.isdir(folder):
        raise MentorError('找不到这个文件夹')
    f = files_of(folder)
    if not f['atoms'] or not f['persona']:
        raise MentorError('这个文件夹里要有「观点库」和「老师画像」两份文件（文件名里带这几个字），先完成蒸馏再导入')
    for m in list_mentors(chats=False):
        if _same(m['folder'], folder):
            raise MentorError(f'「{m["name"]}」已经导入过了')
    m = {'id': uuid.uuid4().hex[:10], 'name': Path(folder).name, 'folder': folder, 'created': time.strftime('%Y-%m-%d %H:%M:%S')}
    m['sources'] = resolve_sources(folder)
    _save_json(_mdir(m['id']) / 'mentor.json', m)
    return m


def resolve_sources(folder):
    """来源表 S01… → 课程笔记 → 笔记属性里的「视频文件」（找不到的标出来）。
    在软件里蒸馏的老师（distill.json 里记着每节课的视频）：笔记找不到、或者还没剪藏，就用记下的视频。"""
    md = _read(files_of(folder)['atoms']) if Path(folder).is_dir() else ''
    srcs = parse_sources(md)
    notes = _find_notes(_vault_of(folder), [s['note'] for s in srcs])
    known = {}
    for m in list_mentors(chats=False):
        if _same(m['folder'], folder):
            known = {x['sid']: x['path'] for x in (_load_json(_mdir(m['id']) / 'distill.json') or {}).get('lessons') or []}
    out = []
    for s in srcs:
        note = notes.get(s['note'])
        video = _frontmatter_video(note) if note else ''
        if not (video and os.path.isfile(video)) and known.get(s['sid']):
            video = os.path.normpath(known[s['sid']])
        out.append(dict(s, notePath=str(note) if note else '', path=video, found=bool(video and os.path.isfile(video))))
    return out


def refresh(mid):
    m = get(mid)
    m['sources'] = resolve_sources(m['folder'])
    _save_json(_mdir(mid) / 'mentor.json', m)
    return m


def remove(mid):
    """删掉软件里这位老师和他的对话记录；Obsidian 里的文件不动。"""
    d = _mdir(mid)
    if d.is_dir():
        shutil.rmtree(d)


def overview(mid):
    """打开一位老师时界面要的：来源（出处跳视频用）、观点编号和出处（出处显示原文用）。"""
    m = get(mid)
    f = files_of(m['folder'])
    atoms = parse_atoms(_read(f['atoms']))
    return {'mentor': m, 'files': {k: str(v) if v else '' for k, v in f.items()},
            'atoms': {k: {'src': v['src'], 'text': v['text'], 't': v['t'], 'sid': v['sid']} for k, v in atoms.items()}}


# ------------------------------------------------------------ 找相关资料 ----
def _grams(text):
    t = re.sub(r'[\s\W_]+', '', str(text).lower())
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def _windows(m):
    """字幕切成约 45 秒一段的原文片段：[{sid, start, end, text}]"""
    out = []
    for s in m.get('sources') or []:
        if not s.get('path'):
            continue
        try:
            d = docs.load(s['path']) or {}
        except Exception:  # noqa: BLE001  某节课字幕读不出就跳过
            continue
        cur = None
        for seg in d.get('segments') or []:
            if cur is None:
                cur = {'sid': s['sid'], 'start': float(seg['start']), 'end': float(seg['end']), 'text': seg['text']}
            else:
                cur['end'] = float(seg['end'])
                cur['text'] += seg['text']
            if cur['end'] - cur['start'] >= WINDOW_SECONDS:
                out.append(cur)
                cur = None
        if cur:
            out.append(cur)
    return out


def rank(query, items, key, k):
    """按和问题的字词重合打分，取前 k 个（常见的字词权重低）。"""
    q = _grams(query)
    if not q or not items:
        return []
    grams = [_grams(key(x)) for x in items]
    n = len(items)
    df = {}
    for g in grams:
        for w in g & q:
            df[w] = df.get(w, 0) + 1
    scored = []
    for x, g in zip(items, grams):
        hit = g & q
        if hit:
            s = sum(math.log(1 + n / df[w]) for w in hit) / (1 + math.log(1 + len(g)))
            scored.append((s, x))
    scored.sort(key=lambda p: -p[0])
    return [x for _, x in scored[:k]]


def build_messages(m, history, question):
    """交给 AI 的资料和对话。返回 (messages, 用了哪些资料的说明)。"""
    f = files_of(m['folder'])
    persona, distill, atoms_md = _read(f['persona']), _read(f['distill']), _read(f['atoms'])
    if not persona or not atoms_md:
        raise MentorError('老师文件夹里的「老师画像」或「观点库」不见了，请检查 Obsidian 里的文件')
    atoms = list(parse_atoms(atoms_md).values())
    last_q = next((h['content'] for h in reversed(history or []) if h.get('role') == 'user'), '')
    query = question + ' ' + last_q
    total = sum(len(a['text']) + len(a['cond']) for a in atoms)
    picked = atoms if total <= ATOMS_ALL_LIMIT else rank(query, atoms, lambda a: a['text'] + a['cond'], ATOMS_PICK)
    wins = rank(query, _windows(m), lambda w: w['text'], WINDOWS_PICK)
    wins.sort(key=lambda w: (w['sid'], w['start']))
    names = '\n'.join(f'- {s["sid"]}：{s["title"]}' for s in m.get('sources') or [])
    atom_lines = '\n'.join(f'- [{a["id"]}]（{a["src"]}，{a["kind"]}）{a["text"]}' + (f'　条件：{a["cond"]}' if a['cond'] else '') for a in picked)
    win_lines = '\n'.join(f'- [{w["sid"]} {_ts(w["start"])}–{_ts(w["end"])}] {w["text"]}' for w in wins) or '（没有找到特别相关的原文）'
    system = (
        f'{persona}\n\n---\n# 课程（编号对应）\n{names}\n\n'
        + (f'---\n# 课程蒸馏（整体体系）\n{distill}\n\n' if distill else '')
        + f'---\n# 观点库（{"全部" if picked is atoms else "和这个问题相关的部分"}，编号可作出处）\n{atom_lines}\n\n'
        f'---\n# 和这个问题相关的课程原文（字幕片段）\n{win_lines}\n\n'
        '---\n# 这一次回答的要求\n'
        '1. 严格按上面「老师画像」第一节的规则回答，用老师的思路和说话方式。\n'
        '2. 出处只能写成 ［S02 19:54］（课程原文时间）或 ［S02-21］（观点库编号），而且只能引用上面给出的编号和时间，不许编造出处。\n'
        '3. 课程里没讲的，按规则标【AI 推测：……不是课程内容】，说清从哪几条推出来；推不出来就照实说课程没讲。\n'
        '4. 用户没要求时，不加 AI 自己的观点；用户要求时另起一段标【AI 自己的看法，不代表老师】。\n'
        '5. 用中文，先结论后理由，最后给能马上做的事；不给具体的买卖建议。'
    )
    messages = [{'role': 'system', 'content': system}]
    for h in (history or [])[-HISTORY_TURNS:]:
        if h.get('role') in ('user', 'assistant') and h.get('content'):
            messages.append({'role': h['role'], 'content': h['content']})
    messages.append({'role': 'user', 'content': question})
    used = {'atoms': len(picked), 'atoms_total': len(atoms), 'windows': len(wins), 'chars': len(system)}
    return messages, used


def quote(mid, sid, t):
    """出处那一秒前后的原话（界面上鼠标停在出处上时显示）。"""
    m = get(mid)
    s = next((x for x in m.get('sources') or [] if x['sid'] == sid), None)
    if not s or not s.get('path'):
        return {'title': s['title'] if s else sid, 'text': '', 'path': ''}
    d = docs.load(s['path']) or {}
    segs = d.get('segments') or []
    i = max((k for k, x in enumerate(segs) if float(x['start']) <= float(t) + 0.5), default=0)
    lines = [f'[{_ts(x["start"])}] {x["text"]}' for x in segs[i:i + 4]]
    return {'title': s['title'], 'text': '\n'.join(lines), 'path': s['path']}


# ----------------------------------------------------------------- 对话 ----
def _cpath(mid, cid):
    if not re.fullmatch(r'[0-9a-f]{12}', str(cid or '')):
        raise MentorError('没有这个对话')
    return _mdir(mid) / 'chats' / f'{cid}.json'


def list_chats(mid):
    d = _mdir(mid) / 'chats'
    out = []
    if d.is_dir():
        for p in d.glob('*.json'):
            c = _load_json(p)
            if c:
                out.append({'id': c['id'], 'title': c.get('title') or '新对话', 'updated': c.get('updated', ''), 'count': len(c.get('messages') or [])})
    return sorted(out, key=lambda c: c['updated'], reverse=True)


def load_chat(mid, cid):
    c = _load_json(_cpath(mid, cid))
    if not c:
        raise MentorError('这个对话不见了')
    return c


def new_chat(mid, title=''):
    get(mid)
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    c = {'id': uuid.uuid4().hex[:12], 'title': title or '新对话', 'created': now, 'updated': now, 'messages': []}
    _save_json(_cpath(mid, c['id']), c)
    return c


def append(mid, cid, *msgs):
    """往对话里加消息（读最新的再写，界面和后台不会互相覆盖）。第一句问题当标题。"""
    c = load_chat(mid, cid)
    for msg in msgs:
        c['messages'].append(dict(msg, time=msg.get('time') or time.strftime('%Y-%m-%d %H:%M:%S')))
    if c.get('title') in ('', '新对话'):
        q = next((x['content'] for x in c['messages'] if x['role'] == 'user'), '')
        c['title'] = re.sub(r'\s+', ' ', q).strip()[:30] or '新对话'
    c['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    _save_json(_cpath(mid, cid), c)
    return c


def rename_chat(mid, cid, title):
    c = load_chat(mid, cid)
    c['title'] = str(title or '').strip()[:60] or c['title']
    _save_json(_cpath(mid, cid), c)
    return c


def delete_chat(mid, cid):
    _cpath(mid, cid).unlink(missing_ok=True)


def delete_all_chats(mid):
    d = _mdir(mid) / 'chats'
    n = len(list(d.glob('*.json'))) if d.is_dir() else 0
    if d.is_dir():
        shutil.rmtree(d)
    return n


def search(mid, q):
    """在这位老师的所有对话里找（标题和内容）。"""
    q = str(q or '').strip().lower()
    out = []
    for c in list_chats(mid):
        if not q:
            out.append(c)
            continue
        full = load_chat(mid, c['id'])
        if q in c['title'].lower() or any(q in (x.get('content') or '').lower() for x in full['messages']):
            out.append(c)
    return out


# ------------------------------------------------------- 收藏到 Obsidian ----
def _backup(path):
    folder = config.DATA_DIR / 'backups' / '对话精选'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, folder / f'{Path(path).parent.name}-{time.strftime("%Y%m%d-%H%M%S")}.md')
    olds = sorted(folder.glob(f'{Path(path).parent.name}-*.md'))
    for old in olds[:-30]:
        old.unlink(missing_ok=True)


def _fav_block(c, i):
    msgs = c['messages']
    a = msgs[i]
    q = next((msgs[k]['content'] for k in range(i - 1, -1, -1) if msgs[k]['role'] == 'user'), '')
    quoted = '\n'.join('> ' + line if line.strip() else '>' for line in q.strip().splitlines())
    return (f'\n\n---\n\n## {re.sub(chr(10), " ", q.strip())[:40]}\n\n'
            f'<small>{a.get("time", "")[:16]} · {a.get("model", "")} · 对话「{c.get("title", "")}」</small>\n\n'
            f'**问：**\n\n{quoted}\n\n**答：**\n\n{a["content"].strip()}\n')


def _fav_write(m, blocks):
    """把几条问答追加到老师文件夹里的「对话精选.md」（写之前备份一次）。"""
    path = Path(m['folder']) / FAVORITES
    if path.is_file():
        _backup(path)
        old = _read(path).rstrip()
    else:
        atoms = files_of(m['folder'])['atoms']
        old = (f'---\n老师: {m["name"]}\n类型: 对话精选\ntags:\n  - 课程蒸馏\n---\n\n# {m["name"]}：对话精选\n\n'
               f'> 在猛人学习「老师工作台」里和{m["name"]}的老师画像对话时，收藏的回答。出处 ［S02 19:54］ 对应 [[{atoms.stem if atoms else "01、观点库（逐节提取）"}]] 里的课程编号。'
               f'回答是 AI 按老师画像写的，不等于老师原话，也不是你的结论。')
    path.write_text(old + ''.join(blocks), encoding='utf-8')
    return path


def save_favorite(mid, cid, index):
    """把第 index 条（AI 的回答）连同它前面的问题，追加到老师文件夹里的「对话精选.md」。"""
    m = get(mid)
    c = load_chat(mid, cid)
    msgs = c['messages']
    if not (0 <= int(index) < len(msgs)) or msgs[int(index)]['role'] != 'assistant':
        raise MentorError('只能收藏老师的回答')
    path = _fav_write(m, [_fav_block(c, int(index))])
    msgs[int(index)]['saved'] = time.strftime('%Y-%m-%d %H:%M:%S')
    _save_json(_cpath(mid, cid), c)
    return dict(favorites(mid), chat=c, count=1)


def save_all(mid, cid):
    """一键收藏整个对话：还没收藏的回答按顺序一次写进去，已经收藏的不重复。"""
    m = get(mid)
    c = load_chat(mid, cid)
    todo = [i for i, x in enumerate(c['messages']) if x['role'] == 'assistant' and not x.get('saved') and (x.get('content') or '').strip()]
    if not todo:
        return dict(favorites(mid), chat=c, count=0)
    _fav_write(m, [_fav_block(c, i) for i in todo])
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    for i in todo:
        c['messages'][i]['saved'] = now
    _save_json(_cpath(mid, cid), c)
    return dict(favorites(mid), chat=c, count=len(todo))


def favorites(mid):
    """对话精选在哪、有没有、在 Obsidian 里打开的链接（库外面的文件没有链接，就在资源管理器里显示）。"""
    m = get(mid)
    path = Path(m['folder']) / FAVORITES
    vault = _vault_of(m['folder'])
    uri = ''
    if vault and _norm(path).startswith(_norm(vault).rstrip('\\/') + os.sep):
        from urllib.parse import quote as q
        rel = os.path.relpath(path, vault).replace('\\', '/')
        uri = f'obsidian://open?vault={q(Path(vault).name)}&file={q(rel)}'
    return {'file': str(path), 'exists': path.is_file(), 'uri': uri}
