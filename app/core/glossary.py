"""逐课积累术语表：把用户勾选确认的新术语和识别错误加进老师的术语表。

老师档案关联了 Obsidian 里的术语表文件（terms_file）时，写进文件末尾的「逐课补充（猛人学习）」一节：
    ### 新术语        | 术语 | 首次出现 | 老师原话 |
    ### 识别错误      | ASR常见识别 | 正确术语 | 出处 |
llm.terms_from_markdown 认得这两种表，下次识别和校对就会用上。文件其他部分一个字都不动；写之前先备份。
没有关联文件时，加在老师档案手写的术语后面（「术语」和「错写 => 正确」）。

新老师的第一节课（2026-10-28，用户要求）：还没有老师和术语表时也能找新术语，勾选后填老师名字，`create` 建老师档案、
在 Obsidian `02、规则体系/02、课程专用规则/老师名课程术语表.md` 建一份术语表（结构照峰级那份：怎么用 / 术语 / 识别错误对照 /
逐课补充）、关联上、把这节课所在的文件夹绑定这位老师，再把勾选的写进「逐课补充」。以后这个文件夹里的课照常逐课积累。"""
import os
import re
import shutil
import time
from pathlib import Path

from . import config

SECTION = '逐课补充（猛人学习）'
INTRO = '> 由「猛人学习」整理课程时找出、经人工勾选确认后加入。只记录正确名称和常见识别错误，不补写定义。'
TERM_HEAD = ['| 术语 | 首次出现 | 老师原话 |', '| --- | --- | --- |']
ERR_HEAD = ['| ASR常见识别 | 正确术语 | 出处 |', '| --- | --- | --- |']
CN = '零一二三四五六七八九'
KB_DIR = ('02、规则体系', '02、课程专用规则')   # 新老师的术语表建在知识库的这里（和峰级术语表放在一起）


def template(name):
    """新老师术语表的初始内容（「逐课补充」由 merge_markdown 第一次写入时加在最后，成为第四节）。"""
    tag = re.sub(r'[\s#,，、/\\]+', '', name) or '课程术语表'
    return (f'---\ntitle: {name}课程术语表\ntype: 课程专用术语表\nstatus: 使用中\nscope: {name}的课程\ntags:\n  - 课程术语表\n  - {tag}\n---\n\n'
            f'# {name}课程术语表\n\n'
            f'> 由猛人学习在整理{name}的第一节课时建立（{time.strftime("%Y-%m-%d")}）。只适用于{name}的课程，其他老师不默认加载。\n\n'
            '---\n\n# 一、怎么用\n\n'
            '- **猛人学习每次读取本文件最新内容**：「二、术语」和「逐课补充」里的新术语作为识别热词；「三、识别错误对照」和「逐课补充」里的识别错误作为 AI 校对参考；整份文件作为 AI 理解课程用语的术语说明。\n'
            '- **新术语**：每节课整理完，猛人学习列出候选 → 作者勾选 → 软件写入末尾「逐课补充（猛人学习）」，写入前自动备份。未经勾选的不写；只记名称，不补写定义。\n'
            '- **作者可以自己整理**：把逐课补充里确认过的术语挪到「二、术语」并写上含义；在「三」里加识别错误对照。\n'
            '- **识别错误只作参考**：AI 校对时结合上下文判断，不机械替换；只有「识别完直接替换」表里的才直接替换。\n\n'
            '---\n\n# 二、术语\n\n| 术语 | 含义 |\n| --- | --- |\n\n'
            '---\n\n# 三、识别错误对照\n\n| ASR常见识别 | 正确术语 | 说明 |\n| --- | --- | --- |\n\n'
            '### 识别完直接替换\n\n只放确定不会误伤的长词。单字、短词（例如「分 → 峰」会把「部分」改成「部峰」）不能放这里，放上面的对照表。\n\n'
            '| 直接替换的错写 | 正确术语 |\n| --- | --- |\n')


def bind_choices(media_path):
    """这节课往上、到加进媒体库的那个文件夹为止，可以绑定老师的各层文件夹（近的在前）。"""
    from . import library
    lib = library.load()
    key = lambda p: os.path.normcase(os.path.abspath(p)).rstrip('\\/')
    me = key(media_path)
    tops = [f['path'] for f in lib['folders'] if me.startswith(key(f['path']) + os.sep)]
    if not tops:
        return []
    top = max(tops, key=len)
    out, d = [], os.path.dirname(os.path.abspath(media_path))
    while True:
        out.append({'path': d, 'name': Path(d).name, 'top': key(d) == key(top)})
        parent = os.path.dirname(d)
        if key(d) == key(top) or parent == d:
            return out
        d = parent


def plan(media_path):
    """新建术语表之前给界面看的：术语表会建在哪里、已有的老师名字、可以绑定的文件夹。"""
    vault = (config.load().get('obsidian') or {}).get('vault') or ''
    folder = Path(vault, *KB_DIR) if vault and os.path.isdir(vault) else None
    return {'folder': str(folder) if folder else '', 'teachers': [t['name'] for t in config.load_teachers()],
            'binds': bind_choices(media_path)}


def create(name, terms, errors, lesson='', bind=''):
    """新老师的第一节课：建老师档案和 Obsidian 术语表（已有同名老师就用他的），绑定文件夹，把勾选的写进去。
    返回 add 的结果，加上 teacher（老师id）、created（是不是新建的老师）、file_created（是不是新建的术语表文件）。"""
    name = re.sub(r'\s+', ' ', str(name or '')).strip()
    if not name:
        raise ValueError('请填老师的名字')
    t = next((x for x in config.load_teachers() if x['name'] == name), None)
    file_created, new_teacher = False, t is None
    if new_teacher:
        file = ''
        vault = (config.load().get('obsidian') or {}).get('vault') or ''
        if vault and os.path.isdir(vault):
            folder = Path(vault, *KB_DIR)
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f'{re.sub(r"[\\/:*?<>|\"]", "", name)}课程术语表.md'
            if not path.exists():  # 同名文件已经有了就直接关联，不覆盖
                path.write_bytes(template(name).encode('utf-8'))
                file_created = True
            file = str(path)
        t = config.save_teacher({'name': name, 'terms_file': file})
    if bind:
        from . import library
        library.set_folder(bind, teacher=t['id'])
    r = add(t['id'], terms, errors, lesson)
    return dict(r, teacher=t['id'], name=t['name'], created=new_teacher, file_created=file_created, bound=bind)


def _cn(n):
    if n < 10:
        return CN[n]
    if n < 20:
        return '十' + (CN[n % 10] if n % 10 else '')
    if n < 100:
        return CN[n // 10] + '十' + (CN[n % 10] if n % 10 else '')
    return str(n)


def _num(s):
    """一、二十三 这类中文数字 → 整数，认不出返回 0。"""
    if not s or any(c not in CN + '十' for c in s):
        return 0
    if '十' not in s:
        return CN.index(s) if len(s) == 1 else 0
    a, _, b = s.partition('十')
    return (CN.index(a) if a else 1) * 10 + (CN.index(b) if b else 0)


def _cell(s):
    return re.sub(r'\s+', ' ', str(s or '')).replace('|', '／').strip()


def _fmt(t):
    t = int(float(t or 0))
    return f'{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}' if t >= 3600 else f'{t // 60:02d}:{t % 60:02d}'


def _where(lesson, t):
    return f'《{_cell(lesson)}》[{_fmt(t)}]' if lesson else f'[{_fmt(t)}]'


def _table_end(lines, head_idx):
    """表头所在行 → 表格最后一行之后的位置。"""
    j = head_idx + 2
    while j < len(lines) and lines[j].strip().startswith('|'):
        j += 1
    return j


def _section_range(lines):
    """本节的 (开始行, 结束行)；没有返回 None。结束在下一个同级或更高级标题之前。"""
    for i, ln in enumerate(lines):
        m = re.match(r'^(#{1,3})\s+(?:[一二三四五六七八九十]+、)?' + re.escape(SECTION) + r'\s*$', ln.strip())
        if m:
            level = len(m.group(1))
            j = i + 1
            while j < len(lines):
                h = re.match(r'^(#{1,6})\s', lines[j])
                if h and len(h.group(1)) <= level:
                    break
                j += 1
            return i, j
    return None


def _new_section(lines):
    nums = [_num(m.group(1)) for m in (re.match(r'^#\s+([一二三四五六七八九十]+)、', ln) for ln in lines) if m]
    title = (_cn(max(nums) + 1) + '、' if nums and max(nums) else '') + SECTION
    while lines and not lines[-1].strip():
        lines.pop()
    lines += ['', '---', '', f'# {title}', '', INTRO, '', '### 新术语', '', *TERM_HEAD, '', '### 识别错误', '', *ERR_HEAD, '']


def _norm(w):
    """比较术语是否相同时：去掉空格、引号、加粗符号。"""
    return re.sub(r'[\s　*“”"「」]', '', str(w or ''))


def _file_known(text):
    """整份术语表文件里已有的：(术语集合, (错写, 正确) 集合)，都是 _norm 过的。
    以前只看「逐课补充」那两张表，作者自己整理在「二、术语」「识别错误对照」里的会被重复加进逐课补充。"""
    from .llm import parse_terms, terms_from_markdown
    words, hints, forced = parse_terms(terms_from_markdown(text))
    pairs = {(_norm(a), _norm(re.split(r'[（(]', b)[0])) for a, b in hints + forced}
    return {_norm(w) for w in words}, pairs


def merge_markdown(text, terms, errors, lesson=''):
    """把新术语和识别错误写进术语表文档（返回新文字, 加了几条术语, 加了几条识别错误）。
    整份文件里已经有的（任何一张表）都不再加。"""
    nl = '\r\n' if '\r\n' in text else '\n'
    known_all, pairs_all = _file_known(text)
    lines = text.replace('\r\n', '\n').split('\n')
    if not _section_range(lines):
        _new_section(lines)
    a, b = _section_range(lines)

    def find_head(first):
        for k in range(a, b):
            if lines[k].strip().startswith('|') and lines[k].strip().strip('|').split('|')[0].strip() == first \
                    and k + 1 < len(lines) and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[k + 1]):
                return k
        return None

    def ensure(first, title, head):
        nonlocal b
        k = find_head(first)
        if k is None:  # 用户把表删了：在本节末尾重建
            while b > a + 1 and not lines[b - 1].strip():
                b -= 1
            block = ['', f'### {title}', '', *head, '']
            lines[b:b] = block
            b += len(block)
            k = find_head(first)
        return k

    # 新术语
    added_t = 0
    k = ensure('术语', '新术语', TERM_HEAD)
    have = {ln.strip().strip('|').split('|')[0].strip() for ln in lines[k + 2:_table_end(lines, k)]}
    rows = []
    for x in terms:
        w = _cell(x.get('term'))
        if w and w not in have and _norm(w) not in known_all:
            have.add(w)
            known_all.add(_norm(w))
            rows.append(f'| {w} | {_where(lesson, x.get("t"))} | {_cell(x.get("quote"))} |')
    end = _table_end(lines, k)
    lines[end:end] = rows
    added_t = len(rows)
    b += len(rows)

    # 识别错误：同一个正确写法已有一行时，把错写并进那一行
    added_e = 0
    k = ensure('ASR常见识别', '识别错误', ERR_HEAD)
    for x in errors:
        wrong, right = _cell(x.get('wrong')), _cell(x.get('right'))
        if not wrong or not right or wrong == right or (_norm(wrong), _norm(right)) in pairs_all:
            continue
        pairs_all.add((_norm(wrong), _norm(right)))
        end = _table_end(lines, k)
        hit = None
        for r in range(k + 2, end):
            cells = [c.strip() for c in lines[r].strip().strip('|').split('|')]
            if len(cells) >= 2 and cells[1] == right:
                hit = (r, cells)
                break
        if hit:
            r, cells = hit
            olds = [w.strip() for w in re.split(r'[、,，/]', cells[0]) if w.strip()]
            if wrong in olds:
                continue
            cells[0] = '、'.join(olds + [wrong])
            if len(cells) >= 3 and lesson and f'《{_cell(lesson)}》' not in cells[2]:
                cells[2] = (cells[2] + '；' if cells[2] else '') + _where(lesson, x.get('t'))
            lines[r] = '| ' + ' | '.join(cells) + ' |'
        else:
            lines[end:end] = [f'| {wrong} | {right} | {_where(lesson, x.get("t"))} |']
            b += 1
        added_e += 1
    return nl.join(lines), added_t, added_e


def filter_pending(teacher_id, terms, errors):
    """打开「新术语」对话框前再筛一遍：候选是整理那节课时找的，之后（例如几节课一起在后台整理、一节节确认）
    术语表里可能已经加了同样的，不再让用户勾第二遍。返回 {terms, errors, removed}。"""
    from .llm import known_terms
    known, wrongs = known_terms(config.get_teacher(teacher_id) or {})
    known, wrongs = {_norm(w) for w in known}, {_norm(w) for w in wrongs}
    out_t, out_e, seen = [], [], set()
    for x in terms or []:
        k = _norm(x.get('term'))
        if k and k not in known and k not in seen:
            seen.add(k)
            out_t.append(x)
    pairs = set()
    for x in errors or []:
        a, b = _norm(x.get('wrong')), _norm(x.get('right'))
        if a and b and a not in wrongs and (a, b) not in pairs:
            pairs.add((a, b))
            out_e.append(x)
    return {'terms': out_t, 'errors': out_e, 'removed': len(terms or []) + len(errors or []) - len(out_t) - len(out_e)}


def _table_kind(first):
    """表头第一格 → 'term' / 'error' / 'forced' / None（和 llm.terms_from_markdown 认表的规则一样）。"""
    if first in ('术语', '新术语'):
        return 'term'
    if '直接替换' in first:
        return 'forced'
    if re.search(r'ASR|误识|识别|错误', first):
        return 'error'
    return None


def check_file(path):
    """「查看」术语表文件：软件读到的术语、识别错误，以及重复的条目在第几行（只报告，不改文件）。"""
    from .llm import parse_terms, terms_from_markdown
    text = config.read_text_file(path)
    words, hints, forced = parse_terms(terms_from_markdown(text))
    lines = text.replace('\r\n', '\n').split('\n')
    sec = sub = ''
    term_at, err_at = {}, {}
    kind = None
    for i, ln in enumerate(lines, 1):
        s = ln.strip()
        if ln.startswith('# '):
            sec, sub, kind = ln[2:].strip(), '', None
            continue
        if ln.startswith('### '):
            sub, kind = ln[4:].strip(), None
            continue
        if not s.startswith('|'):
            kind = None
            continue
        if re.match(r'^\|[\s:|-]+\|$', s):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if i < len(lines) and re.match(r'^\s*\|[\s:|-]+\|\s*$', lines[i]):  # 下一行是分隔线：这是表头
            kind = _table_kind(cells[0])
            continue
        where = f'第 {i} 行（{sec}{" / " + sub if sub else ""}）'
        if kind == 'term' and cells[0]:
            term_at.setdefault(_norm(cells[0]), [cells[0], []])[1].append(where)
        elif kind in ('error', 'forced') and len(cells) >= 2 and cells[1]:
            for w in re.split(r'[、,，/]', cells[0]):
                if w.strip():
                    key = (_norm(w), _norm(cells[1]))
                    err_at.setdefault(key, [w.strip(), cells[1], []])[2].append(where + ('（直接替换）' if kind == 'forced' else ''))
    return {'words': words, 'hints': [list(h) for h in hints], 'forced': [list(f) for f in forced], 'chars': len(text),
            'dup_terms': [{'term': v[0], 'where': v[1]} for v in term_at.values() if len(v[1]) > 1],
            'dup_errors': [{'wrong': v[0], 'right': v[1], 'where': v[2]} for v in err_at.values() if len(v[2]) > 1],
            'uri': obsidian_uri(path)}


def obsidian_uri(path):
    """在 Obsidian 里打开这个文件的链接（不在设置的 Obsidian 库里就是空的，界面改成在资源管理器里显示）。"""
    from urllib.parse import quote
    vault = (config.load().get('obsidian') or {}).get('vault') or ''
    p, v = os.path.normcase(os.path.abspath(str(path))), os.path.normcase(os.path.abspath(vault)) if vault else ''
    if not v or not p.startswith(v.rstrip('\\/') + os.sep):
        return ''
    rel = os.path.relpath(path, vault).replace('\\', '/')
    return f'obsidian://open?vault={quote(Path(vault).name)}&file={quote(rel)}'


def _backup(path: Path):
    folder = config.DATA_DIR / 'backups' / '术语表'
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, folder / f'{path.stem}-{time.strftime("%Y%m%d-%H%M%S")}{path.suffix}')
    olds = sorted(folder.glob(f'{path.stem}-*{path.suffix}'))
    for old in olds[:-30]:  # 每份术语表留最近 30 个备份
        old.unlink(missing_ok=True)


def add(teacher_id, terms, errors, lesson=''):
    """返回 {'file': 写进的文件（没有关联文件时为空）, 'terms': 加了几条, 'errors': 加了几条}。"""
    t = config.get_teacher(teacher_id, resolve=False)
    if not t:
        raise ValueError('先在顶栏选择老师，才知道加进哪份术语表')
    terms = [x for x in terms or [] if str(x.get('term') or '').strip()]
    errors = [x for x in errors or [] if str(x.get('wrong') or '').strip() and str(x.get('right') or '').strip()]
    path = t.get('terms_file')
    if path:
        path = Path(path)
        if not path.is_file():
            raise ValueError('找不到关联的术语表文件：' + str(path))
        text = config.read_text_file(path)
        new, nt, ne = merge_markdown(text, terms, errors, lesson)
        if nt or ne:
            _backup(path)
            tmp = path.with_name(path.name + '.tmp')
            tmp.write_bytes(new.encode('utf-8'))
            os.replace(tmp, path)
        return {'file': str(path), 'terms': nt, 'errors': ne}
    # 没有关联文件：加进老师档案
    from .llm import known_terms
    known, wrongs = known_terms(t)
    rows = []
    new_words = [x['term'].strip() for x in terms if x['term'].strip() not in known]
    if new_words:
        rows.append(', '.join(dict.fromkeys(new_words)))
    pairs = [(x['wrong'].strip(), x['right'].strip()) for x in errors if x['wrong'].strip() not in wrongs]
    rows += [f'{a} => {b}' for a, b in dict.fromkeys(pairs)]
    if rows:
        config.save_teacher(dict(t, terms=(t.get('terms') or '').rstrip() + '\n' + '\n'.join(rows)))
    return {'file': '', 'terms': len(dict.fromkeys(new_words)), 'errors': len(dict.fromkeys(pairs))}
