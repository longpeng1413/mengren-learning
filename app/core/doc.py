"""字幕文件：保存在视频旁边（「视频名.猛人字幕.json」），写不进去时放到 %APPDATA%\\猛人字幕\\library。
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

SUFFIX = '.猛人字幕.json'
VERSION = 1


def sidecar(media_path: str) -> Path:
    p = Path(media_path)
    return p.with_name(p.name + SUFFIX)


def fallback(media_path: str) -> Path:
    h = hashlib.sha1(os.path.abspath(media_path).lower().encode('utf-8')).hexdigest()[:20]
    return DATA_DIR / 'library' / f'{h}.json'


def load(media_path: str):
    for path in (sidecar(media_path), fallback(media_path)):
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
        _write(sidecar(media_path), data)
        where = sidecar(media_path)
    except OSError:
        _write(fallback(media_path), data)
        where = fallback(media_path)
    return {'updated': data['updated'], 'stored': str(where)}


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


def to_markdown(doc, teacher=None, series=''):
    """蒸馏用的笔记：上半部分课程重点，下半部分完整带时间戳的字幕（审核校验用的原始数据）。"""
    media = doc.get('media', '')
    uri = Path(media).as_uri() if media else ''
    try:
        lesson_date = time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(media))) if media else ''
    except OSError:
        lesson_date = ''
    tname = (teacher or {}).get('name', '')
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
        f'glossary: {_yaml(tname if teacher and teacher.get("terms") else "")}',
        f'corrected: {"true" if doc.get("corrected") else "false"}',
        f'reviewed: {"true" if doc.get("reviewed") else "false"}',
        f'segments: {len(segs)}',
        f'clipped: {time.strftime("%Y-%m-%d %H:%M")}',
        'tags:',
        '  - 猛人字幕',
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
    lines += ['## 课程重点', '', (doc.get('summary') or '').strip() or '（还没有生成课程重点）', '']
    lines += ['## 完整字幕', '', '> 以下是带时间戳的完整字幕，作为审核和校验的原始数据。'
              + ('已人工校对。' if doc.get('reviewed') else '尚未人工校对。'), '']
    for s in segs:
        lines.append(f'[{fmt(s["start"])}] {s.get("text", "")}'.rstrip())
        if s.get('tr'):
            lines.append(f'　　{s["tr"]}')
    return '\n'.join(lines).rstrip() + '\n'


def clip(doc, vault: str, folder: str, teacher=None, series='', teacher_subfolder=True):
    if not vault or not Path(vault).is_dir():
        raise OSError('找不到 Obsidian 库文件夹，请到「设置 → Obsidian」检查库的位置')
    target_dir = Path(vault) / (folder or '')
    if teacher and teacher_subfolder:
        target_dir = target_dir / safe_name(teacher.get('folder') or teacher.get('name') or '')
    target_dir.mkdir(parents=True, exist_ok=True)
    previous = (doc.get('clip') or {}).get('path')
    if previous and Path(previous).parent == target_dir and Path(previous).exists():
        path = Path(previous)  # 同一个视频再次剪藏时更新原来的笔记
    else:
        path = target_dir / (safe_name(doc.get('title') or '字幕') + '.md')
        n = 2
        while path.exists():
            path = target_dir / f'{safe_name(doc.get("title") or "字幕")} ({n}).md'
            n += 1
    path.write_text(to_markdown(doc, teacher, series), encoding='utf-8')
    vault_name = Path(vault).name
    rel = path.relative_to(Path(vault)).as_posix()
    from urllib.parse import quote
    return {'path': str(path), 'uri': f'obsidian://open?vault={quote(vault_name)}&file={quote(rel)}',
            'time': time.strftime('%Y-%m-%d %H:%M:%S')}
