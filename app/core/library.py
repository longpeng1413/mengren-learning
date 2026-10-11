"""媒体库：左侧列表里的文件夹和单个文件。只记路径，打开时读取磁盘上的最新情况。"""
import json
import os
import re
from pathlib import Path

from . import config, doc as docs
from .book import LIB_EXT
from .media import MEDIA_EXT

MAX_FILES = 3000


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def _status(path, positions):
    """一个文件在列表里要显示的状态：有没有字幕、剪藏过没有、看到哪里。"""
    item = {'path': path, 'name': Path(path).stem, 'ext': Path(path).suffix.lower()[1:]}
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
        item['reviewed'] = bool(d.get('reviewed'))
        item['teacher'] = d.get('teacher') or ''
        item['duration'] = d.get('duration') or 0
    pos = positions.get(os.path.abspath(path).lower())
    if pos:
        item['pos'] = pos.get('t', 0)
        item['duration'] = item.get('duration') or pos.get('d', 0)
    return item


def scan(folder):
    out = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted((d for d in dirs if not d.startswith(('.', '_'))), key=_natural)  # 「_关键帧缓存」这类辅助文件夹不列
        pdf_stems = {Path(f).stem.lower() for f in files if f.lower().endswith('.pdf')}
        for f in sorted(files, key=_natural):
            if f.lower().endswith('.docx') and Path(f).stem.lower() in pdf_stems:
                continue  # 和 PDF 同名的 Word 是它的正文参考，不单独列
            if Path(f).suffix.lower() in MEDIA_EXT or Path(f).suffix.lower() in LIB_EXT:
                out.append(os.path.join(root, f))
                if len(out) >= MAX_FILES:
                    return out
    return out


def view():
    """整个媒体库：[{folder, name, teacher, items:[…]}]，最后一组是单独添加的文件。"""
    lib = config.load_library()
    positions = config.get_positions()
    groups = []
    for f in lib['folders']:
        path = f.get('path')
        exists = bool(path) and os.path.isdir(path)
        items = []
        if exists:
            for p in scan(path):
                it = _status(p, positions)
                rel = os.path.relpath(os.path.dirname(p), path)
                it['sub'] = '' if rel == '.' else rel.replace('\\', '/')
                items.append(it)
        groups.append({'type': 'folder', 'path': path, 'name': f.get('name') or Path(path).name, 'teacher': f.get('teacher') or '',
                       'collapsed': bool(f.get('collapsed')), 'missing': not exists, 'items': items})
    files = [p for p in lib['files'] if os.path.isfile(p)]
    if files:
        groups.append({'type': 'files', 'path': '', 'name': '单个文件', 'teacher': '', 'collapsed': bool(lib.get('files_collapsed')),
                       'missing': False, 'items': [_status(p, positions) for p in files]})
    return groups


def add_folder(path):
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValueError('找不到这个文件夹：' + path)
    lib = config.load_library()
    if not any(os.path.normcase(f['path']) == os.path.normcase(path) for f in lib['folders']):
        lib['folders'].append({'path': path, 'name': Path(path).name, 'teacher': '', 'collapsed': False})
        config.save_library(lib)
    return len(scan(path))


def add_files(paths):
    lib = config.load_library()
    known = {os.path.normcase(p) for p in lib['files']}
    n = 0
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p) and (Path(p).suffix.lower() in MEDIA_EXT or Path(p).suffix.lower() in LIB_EXT) and os.path.normcase(p) not in known:
            lib['files'].append(p)
            known.add(os.path.normcase(p))
            n += 1
    config.save_library(lib)
    return n


def remove(kind, path):
    lib = config.load_library()
    if kind == 'folder':
        lib['folders'] = [f for f in lib['folders'] if os.path.normcase(f['path']) != os.path.normcase(path)]
    else:
        lib['files'] = [p for p in lib['files'] if os.path.normcase(p) != os.path.normcase(path)]
    config.save_library(lib)


def set_folder(path, **changes):
    lib = config.load_library()
    if path == '':  # 「单个文件」这一组
        if 'collapsed' in changes:
            lib['files_collapsed'] = bool(changes['collapsed'])
    for f in lib['folders']:
        if os.path.normcase(f['path']) == os.path.normcase(path):
            for k in ('teacher', 'collapsed', 'name'):
                if k in changes:
                    f[k] = changes[k]
    config.save_library(lib)


def folder_teacher(media_path):
    """视频所在的库文件夹绑定的老师（最长匹配）。"""
    best, tid = '', ''
    mp = os.path.normcase(os.path.abspath(media_path))
    for f in config.load_library()['folders']:
        fp = os.path.normcase(f['path']).rstrip('\\/') + os.sep
        if mp.startswith(fp) and len(fp) > len(best) and f.get('teacher'):
            best, tid = fp, f['teacher']
    return tid
