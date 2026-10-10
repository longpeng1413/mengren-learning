"""逐课积累术语表：把用户勾选确认的新术语和识别错误加进老师的术语表。

老师档案关联了 Obsidian 里的术语表文件（terms_file）时，写进文件末尾的「逐课补充（猛人学习）」一节：
    ### 新术语        | 术语 | 首次出现 | 老师原话 |
    ### 识别错误      | ASR常见识别 | 正确术语 | 出处 |
llm.terms_from_markdown 认得这两种表，下次识别和校对就会用上。文件其他部分一个字都不动；写之前先备份。
没有关联文件时，加在老师档案手写的术语后面（「术语」和「错写 => 正确」）。"""
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


def merge_markdown(text, terms, errors, lesson=''):
    """把新术语和识别错误写进术语表文档（返回新文字, 加了几条术语, 加了几条识别错误）。"""
    nl = '\r\n' if '\r\n' in text else '\n'
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
        if w and w not in have:
            have.add(w)
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
        if not wrong or not right or wrong == right:
            continue
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
