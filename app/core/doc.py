"""字幕文件：保存在视频旁边（「视频名.猛人学习.json」），写不进去时放到 %APPDATA%\\猛人学习\\library。
1.6 以前叫「视频名.猛人字幕.json」：照样能读，下次保存时改成新名字。
另外负责导出 TXT / SRT，以及剪藏到 Obsidian。"""
import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from .config import DATA_DIR
from .llm import fmt

SUFFIX = '.猛人学习.json'
OLD_SUFFIX = '.猛人字幕.json'
VERSION = 1


def sidecar(media_path: str) -> Path:
    p = Path(media_path)
    return p.with_name(p.name + SUFFIX)


def old_sidecar(media_path: str) -> Path:
    p = Path(media_path)
    return p.with_name(p.name + OLD_SUFFIX)


def stored_file(media_path: str):
    """这个文件现在的字幕 / 整理结果存在哪里（新名字、旧名字、%APPDATA% 里的备用位置），都没有返回 None。"""
    for p in (sidecar(media_path), old_sidecar(media_path), fallback(media_path)):
        if p.exists():
            return p
    return None


def fallback(media_path: str) -> Path:
    h = hashlib.sha1(os.path.abspath(media_path).lower().encode('utf-8')).hexdigest()[:20]
    return DATA_DIR / 'library' / f'{h}.json'


def load(media_path: str):
    for path in (sidecar(media_path), old_sidecar(media_path), fallback(media_path)):
        try:
            d = json.loads(path.read_text(encoding='utf-8'))
            if isinstance(d, dict) and isinstance(d.get('segments'), list):
                d['_stored'] = str(path)
                return d
        except (OSError, ValueError):
            continue
    return None


def new(media_path: str, info: dict) -> dict:
    return {
        'version': VERSION,
        'media': os.path.abspath(media_path),
        'title': Path(media_path).stem,
        'duration': info.get('duration', 0),
        'lang': None,
        'provider': None,
        'segments': [],
        'summary': '',
        'chat': [],
        'clip': None,
        'created': time.strftime('%Y-%m-%d %H:%M:%S'),
        'updated': None,
    }


def clean_segments(segments):
    out = []
    for s in segments or []:
        try:
            start, end = float(s.get('start', 0)), float(s.get('end', 0))
        except (TypeError, ValueError):
            continue
        out.append({'start': round(max(0.0, start), 2), 'end': round(max(start, end), 2),
                    'text': str(s.get('text') or '').strip(), 'tr': str(s.get('tr') or '').strip()})
    out.sort(key=lambda s: s['start'])
    return out


def note_hash(doc) -> str:
    """会写进剪藏笔记的内容（标题、老师、课程重点、字幕、存疑句、是否已校对）的指纹。
    剪藏时记在 clip.hash 里；之后内容变了，媒体库就显示「待更新剪藏」。"""
    segs = [[s['start'], s['text'], s['tr']] for s in clean_segments(doc.get('segments'))]
    doubts = sorted([round(float(d.get('t') or 0), 2), str(d.get('reason') or '')] for d in doc.get('doubts') or [] if isinstance(d, dict))
    data = [doc.get('title') or '', doc.get('teacher') or '', (doc.get('summary') or '').strip(), segs, doubts, bool(doc.get('reviewed'))]
    return hashlib.sha1(json.dumps(data, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]


def clip_stale(doc) -> bool:
    """剪藏后又改过要写进笔记的内容。1.7.2 以前剪藏的没有指纹，不判断。"""
    clip = doc.get('clip') if isinstance(doc.get('clip'), dict) else {}
    return bool(clip.get('hash')) and doc.get('kind') != 'book' and clip['hash'] != note_hash(doc)


def _write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # 留一份上一次保存的版本，以防万一
        try:
            shutil.copy2(path, path.with_name(path.name + '.bak'))
        except OSError:
            pass
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, path)


def save(media_path: str, doc: dict) -> dict:
    data = {k: v for k, v in doc.items() if not k.startswith('_')}
    data['version'] = VERSION
    data['media'] = os.path.abspath(media_path)
    data['segments'] = clean_segments(data.get('segments'))
    data['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    try:
        old, new = old_sidecar(media_path), sidecar(media_path)
        if old.exists() and not new.exists():  # 改名前的字幕文件：先改成新名字，保存时照样留一份上一版
            os.replace(old, new)
        _write(new, data)
        where = new
        for p in (old, old.with_name(old.name + '.bak')):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        _write(fallback(media_path), data)
        where = fallback(media_path)
    return {'updated': data['updated'], 'stored': str(where), 'stale': clip_stale(data)}


# ------------------------------------------------------------------ 导出 ----
def to_txt(doc, mode='both', timestamps=True):
    lines = [doc.get('title') or '', '']
    for s in doc.get('segments', []):
        stamp = f'[{fmt(s["start"])}] ' if timestamps else ''
        if mode == 'tr':
            body = s.get('tr') or s.get('text', '')
        elif mode == 'src':
            body = s.get('text', '')
        else:
            body = s.get('text', '') + (f'\n{" " * len(stamp)}{s["tr"]}' if s.get('tr') else '')
        lines.append(stamp + body)
        if mode == 'both' and s.get('tr'):
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _srt_time(sec):
    ms = int(round(sec * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def to_srt(doc, mode='both'):
    out = []
    for i, s in enumerate(doc.get('segments', []), 1):
        if mode == 'tr':
            body = s.get('tr') or s.get('text', '')
        elif mode == 'src':
            body = s.get('text', '')
        else:
            body = s.get('text', '') + (('\n' + s['tr']) if s.get('tr') else '')
        out.append(f'{i}\n{_srt_time(s["start"])} --> {_srt_time(max(s["end"], s["start"] + 0.5))}\n{body}\n')
    return '\n'.join(out)


def export(doc, dest: str, fmt_name='txt', mode='both', timestamps=True):
    text = to_srt(doc, mode) if fmt_name == 'srt' else to_txt(doc, mode, timestamps)
    Path(dest).write_text(text, encoding='utf-8-sig' if fmt_name == 'txt' else 'utf-8')
    return dest


# --------------------------------------------------------------- Obsidian ----
def safe_name(name):
    name = re.sub(r'[\\/:*?"<>|#^\[\]]', ' ', name).strip().rstrip('.')
    return re.sub(r'\s+', ' ', name)[:120] or '未命名'


def _yaml(v):
    return '"' + str(v or '').replace('\\', '/').replace('"', "'") + '"'


_END_PUNCT = tuple('。！？!?.…”"」』）)～~')


def source_boundary(doc, tail_problem=''):
    """来源情况：字幕在视频结束前很久就没了、最后一句没说完、视频结尾损坏。没有问题返回空字符串。"""
    segs = [s for s in doc.get('segments') or [] if str(s.get('text') or '').strip()]
    if not segs:
        return ''
    notes = []
    last = segs[-1]
    dur = float(doc.get('duration') or 0)
    if dur and dur - last['end'] > 8:
        notes.append(f'字幕在 {fmt(last["end"])} 结束，视频时长 {fmt(dur)}，最后约 {int(dur - last["end"])} 秒没有识别到讲话')
    text = last['text'].strip()
    if not text.endswith(_END_PUNCT):
        notes.append(f'最后一句没有说完（[{fmt(last["start"])}]「{text[-30:]}」）')
    if tail_problem:
        notes.append(tail_problem)
    return '；'.join(notes)


def _shift_headings(summary):
    """课程重点放在笔记的「## 课程重点」下面：去掉 AI 写的总标题，其余标题降到 ### 起。"""
    lines = (summary or '').strip().split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r'^#{1,6}\s+.*(课程重点|重点|笔记)', lines[0]) and len(lines[0]) < 60:
        lines.pop(0)
    levels = [len(m.group(1)) for m in (re.match(r'^(#{1,6})\s', x) for x in lines) if m]
    if levels:
        shift = 3 - min(levels)
        if shift:
            lines = [re.sub(r'^(#{1,6})(?=\s)', lambda m: '#' * max(1, min(6, len(m.group(1)) + shift)), x) for x in lines]
    return '\n'.join(lines).strip()


def doubt_map(doc):
    """AI 校对时标出的存疑句：{开始时间（保留两位小数）: 原因}。"""
    out = {}
    for d in doc.get('doubts') or []:
        try:
            out[round(float(d.get('t')), 2)] = str(d.get('reason') or '').strip()
        except (TypeError, ValueError):
            continue
    return out


def to_markdown(doc, teacher=None, series='', status='待审计', boundary=''):
    """蒸馏用的笔记：上半部分课程重点（含必要的插图），下半部分完整带时间戳的字幕（审核校验用的原始数据）。
    画面记录不写进笔记（用户 2026-10 要求：笔记只要课程重点和完整字幕）。"""
    media = doc.get('media', '')
    uri = Path(media).as_uri() if media else ''
    try:
        lesson_date = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(media))) if media else ''
    except OSError:
        lesson_date = ''
    tname = (teacher or {}).get('display') or (teacher or {}).get('name', '')
    segs = doc.get('segments', [])
    lines = [
        '---',
        f'title: {_yaml(doc.get("title"))}',
        f'teacher: {_yaml(tname)}',
        f'series: {_yaml(series)}',
        f'lesson_date: {lesson_date}',
        f'source: {_yaml(media)}',
        f'duration: "{fmt(doc.get("duration") or 0)}"',
        f'language: {doc.get("lang") or ""}',
        f'asr: {_yaml(doc.get("provider"))}',
        f'glossary: {_yaml((teacher or {}).get("name", "") if teacher and teacher.get("terms") else "")}',
        f'corrected: {"true" if doc.get("corrected") else "false"}',
        f'reviewed: {"true" if doc.get("reviewed") else "false"}',
        f'status: {_yaml(status)}',
        f'segments: {len(segs)}',
        f'clipped: {time.strftime("%Y-%m-%d %H:%M")}',
        'tags:',
        '  - 猛人学习',
    ]
    if tname:
        lines.append(f'  - {safe_name(tname).replace(" ", "_")}')
    lines += ['---', '', f'# {doc.get("title") or ""}', '']
    meta = [f'老师：{tname}'] if tname else []
    if series:
        meta.append(f'系列：{series}')
    meta.append(f'时长：{fmt(doc.get("duration") or 0)}')
    if uri:
        meta.append(f'视频：[{Path(media).name}]({uri.replace(" ", "%20")})')
    lines += ['> ' + ' ｜ '.join(meta), '']
    lines += ['## 课程重点', '', _shift_headings(doc.get('summary')) or '（还没有生成课程重点）', '']
    if boundary and '整理者注' not in (doc.get('summary') or ''):  # 课程重点里已经写了就不重复
        lines += [f'> 来源情况（软件检查）：{boundary}。整理时没有补写缺失或没说完的内容。', '']
    doubts = doubt_map(doc)
    lines += ['## 完整字幕', '', '> 以下是带时间戳的完整字幕，作为审核和校验的原始数据。'
              + ('已人工校对。' if doc.get('reviewed') else '尚未人工校对。')
              + ('〔存疑：…〕是 AI 校对时标出、需要回听确认的句子。' if doubts else ''), '']
    for s in segs:
        why = doubts.get(round(float(s['start']), 2))
        lines.append((f'[{fmt(s["start"])}] {s.get("text", "")}' + (f' 〔存疑：{why}〕' if why else '')).rstrip())
        if s.get('tr'):
            lines.append(f'　　{s["tr"]}')
    return '\n'.join(lines).rstrip() + '\n'


def _same_media(a, b):
    norm = lambda p: os.path.normcase(os.path.abspath(str(p).replace('/', os.sep))) if p else ''
    return bool(a) and norm(a) == norm(b)


def find_note(vault: Path, media: str, prefer: Path = None):
    """找已经剪藏过的笔记（用户可能在 Obsidian 里移动或改了名）：按笔记属性里的 source 找。"""
    roots = [p for p in (prefer, vault) if p and p.is_dir()]
    seen = set()
    for root in roots:
        for p in root.rglob('*.md'):
            if p in seen or '.obsidian' in p.parts or '.trash' in p.parts:
                continue
            seen.add(p)
            try:
                with open(p, 'r', encoding='utf-8', errors='replace') as f:
                    head = f.read(1500)
            except OSError:
                continue
            if not head.startswith('---') or ('猛人学习' not in head and '猛人字幕' not in head):
                continue
            m = re.search(r'(?m)^source:\s*"?(.*?)"?\s*$', head)
            if m and _same_media(m.group(1), media):
                return p
    return None


def _old_status(path: Path):
    try:
        head = path.read_text(encoding='utf-8', errors='replace')[:3000]
    except OSError:
        return None
    m = re.search(r'(?m)^status:\s*"?(.*?)"?\s*$', head.split('\n---', 1)[0])
    return m.group(1).strip() if m and m.group(1).strip() else None


def note_fingerprint(text: str) -> str:
    """笔记文件的指纹，用来判断剪藏后有没有在 Obsidian 里改过。属性里的 status 是用户自己改的，重新剪藏会保留，不算。"""
    text = (text or '').replace('\r\n', '\n')
    head, sep, body = text.partition('\n---\n') if text.startswith('---\n') else ('', '', text)
    head = re.sub(r'(?m)^status:.*$', '', head)
    return hashlib.sha1((head + sep + body).encode('utf-8')).hexdigest()[:16]


def note_changed(path: Path, clip) -> bool:
    """上次剪藏后，笔记在 Obsidian 里被改过。
    有指纹（1.7.3 起）就比指纹；以前剪藏的没有指纹，看文件修改时间是不是比剪藏时间晚。"""
    clip = clip if isinstance(clip, dict) else {}
    try:
        if not path.is_file():
            return False
        if clip.get('file_hash'):
            return note_fingerprint(path.read_text(encoding='utf-8', errors='replace')) != clip['file_hash']
        t = time.mktime(time.strptime(str(clip.get('time') or ''), '%Y-%m-%d %H:%M:%S'))
    except (OSError, ValueError, OverflowError):
        return False
    return path.stat().st_mtime > t + 10


def backup_note(path: Path) -> Path:
    """覆盖在 Obsidian 里改过的笔记前，先备份到软件的数据文件夹（不放进知识库，免得多出一篇重复笔记）。"""
    folder = DATA_DIR / 'backups' / '笔记'
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f'{path.stem}-{time.strftime("%Y%m%d-%H%M%S")}{path.suffix}'
    shutil.copy2(path, dest)
    return dest


def note_name(pattern, doc, teacher=None, series=''):
    name = (pattern or '{title}')
    for k, v in (('{title}', doc.get('title') or '字幕'), ('{teacher}', (teacher or {}).get('display') or (teacher or {}).get('name', '')),
                 ('{series}', series or '')):
        name = name.replace(k, v)
    return safe_name(name)


_FIG = re.compile(r'!\[\[\s*画面\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]\]')


def _parse_ts(s):
    parts = [int(x) for x in s.split(':')]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _small_copy(src: Path, dest: Path):
    """插进笔记的图缩到 960 宽、压缩一些，让知识库尽量小；压缩失败就原样复制。"""
    try:
        from .media import run, tool
        tmp = dest.with_name(dest.stem + '.part.jpg')
        r = run([tool('ffmpeg'), '-y', '-v', 'error', '-i', str(src), '-vf', "scale='min(960,iw)':-2", '-q:v', '6', str(tmp)], timeout=60)
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            os.replace(tmp, dest)
            return
        tmp.unlink(missing_ok=True)
    except Exception:
        pass
    shutil.copy2(src, dest)


def place_figures(summary, frames_list, note_dir: Path, title, shot):
    """把课程重点里的 ![[画面 mm:ss]] 换成真正的图片：截图复制到笔记旁边的 image 文件夹。"""
    if not summary or not _FIG.search(summary):
        return summary

    def repl(m):
        t = _parse_ts(m.group(1))
        f = None
        for x in frames_list or []:
            if abs(float(x.get('t', 0)) - t) <= 3 and (f is None or abs(float(x['t']) - t) < abs(float(f['t']) - t)):
                f = x
        real_t = float(f['t']) if f else float(t)
        if shot is None:
            return f'（画面 {m.group(1)}）'
        try:
            src = Path(shot(real_t))
        except Exception:
            return f'（画面 {m.group(1)}，截图失败）'
        name = f'{safe_name(title)}__{fmt(real_t).replace(":", "-")}.jpg'
        dest = note_dir / 'image' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        _small_copy(src, dest)
        return f'![[image/{name}]]'
    return _FIG.sub(repl, summary)


def clip(doc, vault: str, folder: str, teacher=None, series='', teacher_subfolder=True, name_pattern='{title}', boundary='', shot=None,
         force=False):
    """剪藏到 Obsidian。笔记在上次剪藏后被改过、又没有 force 时，不写，返回 {'conflict': True, …} 让用户确认；
    确认后（force）先备份旧笔记再覆盖。"""
    if not vault or not Path(vault).is_dir():
        raise OSError('找不到 Obsidian 库文件夹，请到「设置 → Obsidian」检查库的位置')
    target_dir = Path(vault) / (folder or '')
    if teacher and teacher_subfolder:
        target_dir = target_dir / safe_name(teacher.get('folder') or teacher.get('display') or teacher.get('name') or '')
    previous = (doc.get('clip') or {}).get('path')
    path = None
    if previous and Path(previous).is_file():
        path = Path(previous)  # 同一个视频再次剪藏时更新原来的笔记
    elif doc.get('media'):
        path = find_note(Path(vault), doc['media'], prefer=target_dir if target_dir.is_dir() else Path(vault) / (folder or ''))
    if path is None:
        target_dir.mkdir(parents=True, exist_ok=True)
        base = note_name(name_pattern, doc, teacher, series)
        path = target_dir / (base + '.md')
        n = 2
        while path.exists():
            path = target_dir / f'{base} ({n}).md'
            n += 1
    backup = None
    if path.exists() and note_changed(path, doc.get('clip')):
        if not force:
            return {'conflict': True, 'path': str(path),
                    'modified': time.strftime('%Y-%m-%d %H:%M', time.localtime(path.stat().st_mtime))}
        backup = backup_note(path)
    status = (_old_status(path) if path.exists() else None) or '待审计'
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(doc, summary=place_figures(doc.get('summary') or '', doc.get('frames'), path.parent, doc.get('title') or '字幕', shot))
    text = to_markdown(out, teacher, series, status, boundary)
    path.write_text(text, encoding='utf-8')
    vault_name = Path(vault).name
    rel = path.relative_to(Path(vault)).as_posix()
    from urllib.parse import quote
    return {'path': str(path), 'uri': f'obsidian://open?vault={quote(vault_name)}&file={quote(rel)}',
            'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'hash': note_hash(doc), 'file_hash': note_fingerprint(text),
            'backup': str(backup) if backup else ''}
