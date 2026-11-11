"""截图编辑（2026-11-11 用户要求：把猛人截图（D:\\mengren-desktop，猛人桌面助手 F1）的图片编辑搬进来，代替以前的「AI 重画」）。

字幕栏、课程重点里插的截图都能「编辑」：裁剪、镜像、画笔、箭头、直线、圆形、方框、实心框、文字、橡皮、撤销 / 恢复、清除、
翻译（先认字，再把外文段落翻成中文盖在原处）、复制、保存。界面在 app.js 的 openShotEdit（网页画布，边画边显示）。

原截图从来不改：画的内容记成数据（marks），存在字幕文件里（sub_shots 每一张 / fig_edits['秒数']），以后还能接着改；
显示、打印、剪藏时由这里用 Pillow 按数据画到截图上（render），画好的图放在截图缓存（frames/，30 天没用自动删，随时能重画）。
**界面画布和这里的画法要一致**：改了一边（线宽、箭头、文字的行距和基线）要改另一边（app.js 的 seDrawItems）。

marks = {wh: [截图宽, 高], flip: 是否左右镜像, items: [...]}，坐标是镜像后整张截图的像素（裁剪在最后做，裁剪范围可以随时改）：
  {k: 'pen' | 'erase', w: 线宽, p: [[x, y], ...]}            画笔 / 橡皮（橡皮只擦画上去的东西，不擦截图）
  {k: 'arrow' | 'line' | 'ellipse' | 'rect' | 'fill', c: '#rrggbb', w: 线宽, p: [[x1, y1], [x2, y2]]}   fill = 实心框
  {k: 'text', c, s: 字号, x, y, t: '文字（可以多行）'}         x, y 是第一行的左上角；行距 s × TEXT_LH，按字体的 ascent 对基线
先画形状层（按顺序，橡皮在这一层擦），再把形状层叠到截图上，最后写文字（文字总在最上面，和猛人截图一样）。

翻译（translate_items）照猛人截图：认字（PP-OCRv6，core/ppocr.py，在单独的小进程里跑；出错退回 Windows 自带认字）→
按行、段落合并 → 不是中文的段落交给有道翻译（设置里填了时先用它，快）或「翻译」那一项 AI（shot_translate）→
每段返回两条标注：用原来的底色盖住原文的实心框 + 换好行的中文（可以拖动、改字、撤销）。"""
import ctypes
import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from statistics import median

TEXT_LH = 1.3          # 文字行距 = 字号 × 1.3（界面 SE_LH 一样）
MAX_ITEMS = 2000
MAX_POINTS = 20000
MAX_TEXT = 4000
KINDS = ('pen', 'erase', 'arrow', 'line', 'ellipse', 'rect', 'fill', 'text')
FONT_FILES = [r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\arial.ttf']
_HEX = re.compile(r'^#[0-9a-fA-F]{6}$')


class ShotEditError(ValueError):
    pass


# ------------------------------------------------------------------ 数据 ----
def _num(v, lo=-1e6, hi=1e6):
    v = float(v)
    if v != v:  # NaN
        raise ValueError
    return round(min(hi, max(lo, v)), 1)


def norm_marks(m):
    """界面交来的标注 → 检查过的字典；什么都没画、也没镜像时返回 None（= 没编辑过）。不认识的条目直接丢掉。"""
    if not isinstance(m, dict):
        return None
    try:
        W, H = (int(round(float(x))) for x in (m.get('wh') or [])[:2])
    except (TypeError, ValueError):
        return None
    if not (16 <= W <= 20000 and 16 <= H <= 20000):
        return None
    items, points = [], 0
    for it in (m.get('items') or [])[:MAX_ITEMS]:
        if not isinstance(it, dict) or it.get('k') not in KINDS:
            continue
        k = it['k']
        try:
            c = str(it.get('c') or '#ff3b30')
            c = c if _HEX.match(c) else '#ff3b30'
            if k == 'text':
                t = str(it.get('t') or '').replace('\r', '')[:MAX_TEXT]
                if not t.strip():
                    continue
                items.append({'k': k, 'c': c, 's': _num(it.get('s') or 24, 4, 1000), 'x': _num(it.get('x') or 0), 'y': _num(it.get('y') or 0), 't': t})
                continue
            p = [[_num(a), _num(b)] for a, b in (it.get('p') or [])[:MAX_POINTS]]
        except (TypeError, ValueError):
            continue
        if not p or (k not in ('pen', 'erase') and len(p) < 2):
            continue
        if k not in ('pen', 'erase'):
            p = [p[0], p[-1]]
        points += len(p)
        if points > MAX_POINTS * 5:
            break
        x = {'k': k, 'w': _num(it.get('w') or 3, 0.5, 500), 'p': p}
        if k != 'erase':
            x['c'] = c
        items.append(x)
    flip = bool(m.get('flip'))
    if not items and not flip:
        return None
    out = {'wh': [W, H], 'items': items}
    if flip:
        out['flip'] = True
    return out


def marks_key(crop, marks) -> str:
    """画好的图的缓存名：裁剪范围 + 标注的指纹。"""
    return hashlib.sha1(json.dumps([crop, marks], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:10]


# ------------------------------------------------------------------ 画图 ----
_fonts = {}


def font(size):
    from PIL import ImageFont
    size = max(4, int(round(size)))
    if size not in _fonts:
        for f in FONT_FILES:
            try:
                _fonts[size] = ImageFont.truetype(f, size)
                break
            except OSError:
                continue
        else:
            _fonts[size] = ImageFont.load_default()
    return _fonts[size]


def _rgba(c, a=255):
    c = c.lstrip('#')
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), a)


def _stroke(draw, pts, color, w):
    """圆头的线（画笔、橡皮、直线、箭头都用它）：一段段连起来，每个点补一个圆。"""
    r = w / 2
    if len(pts) == 1:
        (x, y), = pts
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
        return
    draw.line([tuple(p) for p in pts], fill=color, width=max(1, int(round(w))), joint='curve')
    for x, y in (pts[0], pts[-1]) if len(pts) == 2 else pts:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _arrow_head(a, b, w):
    """箭头两边的线（猛人截图的画法：长 max(线宽×4, 12)，张开 0.55 弧度）。"""
    import math
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx * dx + dy * dy < 16:
        return []
    ang, L = math.atan2(dy, dx), max(w * 4, 12)
    return [[b[0] - L * math.cos(ang - 0.55), b[1] - L * math.sin(ang - 0.55)], [b[0] - L * math.cos(ang + 0.55), b[1] - L * math.sin(ang + 0.55)]]


def _box(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def draw_marks(img, marks):
    """把标注画到图上（img 是镜像后的整张截图，大小不一样时按比例换算）。返回新的 RGB 图。"""
    from PIL import Image, ImageDraw
    img = img.convert('RGB')
    if marks.get('flip'):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    W0, H0 = marks['wh']
    sx, sy = img.width / W0, img.height / H0
    S = lambda p: [p[0] * sx, p[1] * sy]  # noqa: E731
    sw = (sx + sy) / 2
    layer = Image.new('RGBA', img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    texts = []
    for it in marks.get('items') or []:
        k = it['k']
        if k == 'text':
            texts.append(it)
            continue
        pts, w = [S(p) for p in it['p']], it['w'] * sw
        if k == 'erase':
            _stroke(d, pts, (0, 0, 0, 0), w)  # RGBA 图上画透明 = 擦掉（Pillow 直接改像素，不混合）
            continue
        col = _rgba(it['c'])
        if k == 'pen' or k == 'line':
            _stroke(d, pts, col, w)
        elif k == 'arrow':
            a, b = pts
            _stroke(d, [a, b], col, w)
            for h in _arrow_head(a, b, w):
                _stroke(d, [b, h], col, w)
        elif k in ('ellipse', 'rect'):  # 网页画布的线以边框为中线，Pillow 往里画：框往外扩半个线宽，两边才一样
            l, t, r, b = _box(*pts)
            h = w / 2
            box = (l - h, t - h, r + h, b + h)
            (d.ellipse if k == 'ellipse' else d.rectangle)(box, outline=col, width=max(1, int(round(w))))
        elif k == 'fill':
            d.rectangle(_box(*pts), fill=col)
    img = img.convert('RGBA')
    img.alpha_composite(layer)
    if texts:
        d = ImageDraw.Draw(img)
        for it in texts:
            f = font(it['s'] * sw)
            asc = f.getmetrics()[0]
            for n, line in enumerate(it['t'].split('\n')):
                d.text((it['x'] * sx, it['y'] * sy + n * it['s'] * TEXT_LH * sw + asc), line, font=f, fill=_rgba(it['c']), anchor='ls')
    return img.convert('RGB')


def render_image(src: Path, marks=None, crop=None):
    """整张截图 → 画上标注、裁剪后的图（PIL Image）。crop 是 media.norm_crop 过的比例。"""
    from PIL import Image
    with Image.open(src) as im:
        img = draw_marks(im, marks) if marks else im.convert('RGB')
    if crop:
        l, t, r, b = crop
        img = img.crop((int(round(l * img.width)), int(round(t * img.height)), int(round(r * img.width)), int(round(b * img.height))))
    return img


def render_file(src: Path, out: Path, marks=None, crop=None, quality=90):
    tmp = out.with_name(out.stem + '.part' + out.suffix)
    img = render_image(src, marks, crop)
    if out.suffix.lower() in ('.jpg', '.jpeg'):
        img.save(tmp, 'JPEG', quality=quality)
    else:
        img.save(tmp, 'PNG')
    tmp.replace(out)
    return out


# ------------------------------------------------------------ 复制、保存 ----
def copy_to_clipboard(img):
    """图片放进剪贴板（CF_DIB：微信、Word、Obsidian 都能粘贴）。"""
    import io
    import os
    if os.name != 'nt':
        raise ShotEditError('只有 Windows 能复制图片')
    buf = io.BytesIO()
    img.convert('RGB').save(buf, 'BMP')
    data = buf.getvalue()[14:]  # 去掉 BMP 文件头，剩下的就是 DIB
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    k32.GlobalFree.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    import time
    for _ in range(10):  # 别的程序正占着剪贴板时等一下再试
        if u32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise ShotEditError('剪贴板被别的程序占着，过一会儿再试')
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
        if not h:
            raise ShotEditError('内存不够，复制不了')
        p = k32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k32.GlobalUnlock(h)
        if not u32.SetClipboardData(8, h):  # CF_DIB；成功后这块内存归剪贴板管
            k32.GlobalFree(h)
            raise ShotEditError('复制到剪贴板失败')
    finally:
        u32.CloseClipboard()


# ------------------------------------------------------------- 认字、翻译 ----
_HAN = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
_KANA_HANGUL = re.compile(r'[\u3040-\u30ff\u1100-\u11ff\uac00-\ud7af]')
_FOREIGN_WORD = re.compile(r'[^\W\d_\u3040-\u30ff\u1100-\u11ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]+')
_LIST_MARKER = re.compile(r'^\s*([•·●▪◦■□➢►\-–—*]|\d{1,2}[.)、]|[a-zA-Z][.)])\s+')


def han_count(t):
    return len(_HAN.findall(t))


def is_foreign(text) -> bool:
    """这一段要不要翻译（猛人截图 source_language 的规则）：外文词（一个算 1.5 个汉字）占三成以上才翻；
    中文句子里夹几个英文产品名不算。"""
    han, syl = han_count(text), len(_KANA_HANGUL.findall(text))
    foreign = syl + 1.5 * len(_FOREIGN_WORD.findall(text))
    return not (foreign < 1.5 or foreign / (foreign + han) < 0.3)


def _is_cjk(ch):
    o = ord(ch)
    return 0x3000 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF


def _join(a, b):
    if not a:
        return b
    if not b:
        return a
    if a.endswith('-') and b[:1].islower():
        return a[:-1] + b
    if _is_cjk(a[-1]) or _is_cjk(b[0]):
        return a + b
    return f'{a} {b}'


def _union(a, b):
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _h(b):
    return b[3] - b[1]


def merge_rows(lines):
    """认字时被拆开的同一行接起来。lines: [{text, box: [左, 上, 右, 下]}]"""
    rows = []
    for ln in sorted(lines, key=lambda x: (x['box'][0], x['box'][1])):
        b, target = ln['box'], None
        for r in rows:
            h = min(_h(r['box']), _h(b))
            overlap = min(r['box'][3], b[3]) - max(r['box'][1], b[1])
            gap = b[0] - r['box'][2]
            if overlap >= 0.6 * h and -h <= gap <= 1.5 * h:
                target = r
                break
        if target is None:
            rows.append({'text': ln['text'].strip(), 'box': list(b)})
        else:
            target['text'] = _join(target['text'], ln['text'].strip())
            target['box'] = _union(target['box'], b)
    return rows


def _continues(above, below):
    a, b = above['box'], below['box']
    ha, hb = _h(a), _h(b)
    h = (ha + hb) / 2
    if max(ha, hb) > 1.5 * min(ha, hb):
        return False
    pitch = b[1] - a[1]
    if not 0.6 * h <= pitch <= 1.5 * h:
        return False
    left = abs(b[0] - a[0]) <= 1.5 * h
    centered = abs((b[0] + b[2]) / 2 - (a[0] + a[2]) / 2) <= 1.5 * h
    if not (left or centered):
        return False
    if left and not centered and (a[2] - a[0]) < 0.7 * (b[2] - b[0]) and a[2] + 3 * h < b[2]:
        return False  # 上一行比下一行短很多：上一段到这里结束了（小标题、一条的最后一行）
    return True


def group_blocks(lines):
    """行 → 段落（整段翻译才准，单独一行常常是半句话）。返回 [{text, box, lh, prefix}]，按从上到下的顺序。"""
    rows = merge_rows(lines)
    paras = []
    for row in sorted(rows, key=lambda r: (r['box'][1], r['box'][0])):
        target = None
        if not _LIST_MARKER.match(row['text']):
            cands = [p for p in paras if _continues(p[-1], row)]
            if cands:
                target = min(cands, key=lambda p: row['box'][1] - p[-1]['box'][1])
        if target is None:
            paras.append([row])
        else:
            target.append(row)
    out = []
    for p in paras:
        text, box = p[0]['text'], list(p[0]['box'])
        for r in p[1:]:
            text, box = _join(text, r['text']), _union(box, r['box'])
        prefix = ''
        m = _LIST_MARKER.match(text)
        if m and len(text) > m.end():
            prefix, text = m.group(1) + ' ', text[m.end():]
        out.append({'text': text.strip(), 'box': box, 'lh': round(median(_h(r['box']) for r in p)), 'prefix': prefix})
    out.sort(key=lambda b: (b['box'][1], b['box'][0]))
    for b in out:  # 左边差几个像素的段落对齐到同一条边
        b['box'][0] = min(o['box'][0] for o in out if abs(o['box'][0] - b['box'][0]) <= 4)
    return out


def ocr_lines(img):
    """认字：PIL 图 → [{text, box}]。先用 PP-OCRv6（core/ppocr.py，单独的小进程，准得多），出错时退回 Windows 自带认字。"""
    from . import ocr, ppocr
    from .log import log
    work = ocr.OCR_DIR / ('shot-' + uuid.uuid4().hex[:8])
    try:
        work.mkdir(parents=True)
        p = work / 'shot.png'
        img.convert('RGB').save(p)
        return [{'text': x['text'], 'box': x['box']} for x in ppocr.recognize(p)]
    except Exception as e:
        log.warning('PP-OCRv6 认字失败，改用 Windows 自带认字：%s', e)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return win_ocr_lines(img)


def win_ocr_lines(img):
    """Windows 自带认字（备用）。只装了中文认字时用中文的（也认得英文，只是常多空格、I / l 认错）；
    装了英文认字、而认出来的大多是英文时，换英文的再认一遍。"""
    from . import ocr
    langs = []
    try:
        langs = ocr._ps('', '', '', timeout=60).split()
    except Exception as e:
        raise ShotEditError(f'Windows 自带的文字识别用不了：{e}')
    zh = next((x for x in langs if x.lower().startswith('zh-hans')), None)
    en = next((x for x in langs if x.lower().startswith('en')), None)
    if not (zh or en):
        raise ShotEditError('这台电脑没有装文字识别：在 Windows「设置 → 时间和语言 → 语言和区域」里，给中文（简体）或英语装上「光学字符识别」，装好后重启猛人学习')
    work = ocr.OCR_DIR / ('shot-' + uuid.uuid4().hex[:8])
    try:
        (work / 'in').mkdir(parents=True)
        (work / 'out').mkdir()
        img.convert('RGB').save(work / 'in' / 'p0001.png')

        def run(lang):
            ocr._ps(str(work / 'in'), str(work / 'out'), lang, timeout=120)
            r = json.loads((work / 'out' / 'p0001.json').read_text(encoding='utf-8-sig'))
            res = []
            for ln in r.get('lines') or []:
                ws = ln.get('words') or []
                if isinstance(ws, dict):
                    ws = [ws]
                if not ws:
                    continue
                box = [min(w['x'] for w in ws), min(w['y'] for w in ws), max(w['x'] + w['w'] for w in ws), max(w['y'] + w['h'] for w in ws)]
                t = ocr.clean(ln.get('text') or '')
                if t:
                    res.append({'text': t, 'box': [round(x) for x in box]})
            return res

        res = run(zh or en)
        if zh and en:
            txt = ' '.join(x['text'] for x in res)
            if txt and len(re.findall(r'[A-Za-z]', txt)) > 2 * han_count(txt):
                res = run(en)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _hex(rgb):
    return '#%02x%02x%02x' % tuple(int(x) for x in rgb)


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def _contrast(a, b):
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def colors(arr, box):
    """原文那一块的底色（边上一圈的中位数）和字色（和底色差最多的那 5%，不够醒目就用黑 / 白）。"""
    import numpy as np
    l, t, r, b = box
    area = arr[t:b + 1, l:r + 1]
    if area.size == 0:
        return '#ffffff', '#000000'
    border = np.concatenate((area[0], area[-1], area[:, 0], area[:, -1]))
    bg = [int(v) for v in np.median(border, axis=0)]
    px = area.reshape(-1, 3).astype(np.int16)
    dist = np.abs(px - np.array(bg, dtype=np.int16)).sum(axis=1)
    strong = px[dist >= np.percentile(dist, 95)]
    if strong.size:
        fg = [int(v) for v in strong.mean(axis=0)]
        if _contrast(fg, bg) >= 3.0:
            return _hex(bg), _hex(fg)
    black, white = (0, 0, 0), (255, 255, 255)
    return _hex(bg), _hex(black if _contrast(black, bg) >= _contrast(white, bg) else white)


def wrap(text, size, width):
    """按宽度换行：中文哪里都能断，英文尽量在空格处断。返回行的列表。"""
    f = font(size)
    lines = []
    for para in text.split('\n'):
        cur = ''
        for tok in re.findall(r'[A-Za-z0-9’\'\-.,:;!?%/()]+\s*|\s+|.', para):
            if f.getlength(cur + tok) <= width or not cur:
                cur += tok
                while f.getlength(cur) > width and len(cur) > 1:  # 一个词比整行还宽：硬断
                    k = len(cur) - 1
                    while k > 1 and f.getlength(cur[:k]) > width:
                        k -= 1
                    lines.append(cur[:k])
                    cur = cur[k:]
            else:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
        lines.append(cur.rstrip())
    return [x for x in lines] or ['']


def _fitted(text, width, height, target):
    s = target
    while s > 9:
        if len(wrap(text, s, width)) * s * TEXT_LH <= height + s * 0.2:
            return s
        s -= 1
    return 9


def overlay_items(img, blocks, translations):
    """译文 → 标注：每段一个盖住原文的实心框 + 换好行的中文（照猛人截图 ImageTranslationRenderer：
    行高差不多的段落用同一个字号，放不下的那段才单独缩小；第一行对齐原文第一行的中间）。"""
    import numpy as np
    arr = np.asarray(img.convert('RGB'))
    W, H = img.size
    rows = []
    for b, tr in zip(blocks, translations):
        if not tr:
            continue
        l, t, r, bt = b['box']
        cover = [max(0, l - 2), max(0, t - 2), min(W - 1, r + 2), min(H - 1, bt + 2)]
        bg, fg = colors(arr, [max(0, l), max(0, t), min(W - 1, r), min(H - 1, bt)])
        lh = b['lh'] or (bt - t)
        margin = max(2, round(lh * 0.2))
        rows.append({'cover': cover, 'bg': bg, 'fg': fg, 'lh': lh, 'text': b['prefix'] + tr, 'tx': cover[0] + margin, 'tw': max(10, cover[2] - cover[0] - margin)})
    if not rows:
        return []
    groups, smallest = [], []  # 行高相差三成以内的算一组，用同一个字号
    for i in sorted(range(len(rows)), key=lambda i: rows[i]['lh']):
        if groups and rows[i]['lh'] <= smallest[-1] * 1.3:
            groups[-1].append(i)
        else:
            groups.append([i])
            smallest.append(rows[i]['lh'])
    for g in groups:
        target = max(9, round(median(rows[i]['lh'] for i in g) * 0.78))
        fit = {i: _fitted(rows[i]['text'], rows[i]['tw'], rows[i]['cover'][3] - rows[i]['cover'][1], target) for i in g}
        shared = round(median(fit.values()))
        for i, s in fit.items():
            rows[i]['s'] = min(shared, s)
    items = []
    for x in rows:
        s = x['s']
        lines = wrap(x['text'], s, x['tw'])
        h, sp = len(lines) * s * TEXT_LH, s * TEXT_LH
        top = x['cover'][1] + max(0.0, (x['lh'] + 4 - sp) / 2)
        top = min(top, max(float(x['cover'][1]), x['cover'][3] + 1 - h))
        items.append({'k': 'fill', 'c': x['bg'], 'w': 1, 'p': [x['cover'][:2], x['cover'][2:]]})
        items.append({'k': 'text', 'c': x['fg'], 's': s, 'x': x['tx'], 'y': round(top, 1), 't': '\n'.join(lines)})
    return items


SHOT_TRANSLATE_SYSTEM = '''你是翻译。下面是从一张截图（课件、网页、图表）里用文字识别认出来的几段外文，请翻译成简体中文。
- 文字是机器认出来的，可能有错字、单词中间多了空格、大写 I 和小写 l 认反等，请结合上下文理解原意后再翻译；
- 专有名词、人名、代码、公式、数字、单位照原样保留或用通行译法；
- 每段只翻译这一段，不要合并、不要补充解释；
- 只返回 JSON：{"译文": {"编号": "中文译文"}}'''


def translate_texts(settings, texts):
    """几段外文 → (中文列表, 用的哪个服务)。设置 shot_translate：auto 填了有道就先用有道（快，一般 1 秒内），出错再用 AI；
    youdao 只用有道；ai 只用「翻译」那一项的 AI。"""
    from . import youdao
    from .log import log
    how = settings.get('shot_translate') or 'auto'
    if how == 'youdao' or (how == 'auto' and youdao.configured(settings)):
        try:
            return youdao.translate_many(settings, texts), '有道翻译'
        except youdao.YoudaoError as e:
            if how == 'youdao':
                raise ShotEditError(str(e))
            log.warning('有道翻译失败，改用 AI：%s', e)
    return translate_ai(settings, texts)


def translate_ai(settings, texts):
    from . import llm
    from .config import PROVIDER_NAMES
    s, name = llm.for_task(settings, 'translate')
    payload = {str(i): t for i, t in enumerate(texts)}
    msgs = [{'role': 'system', 'content': SHOT_TRANSLATE_SYSTEM},
            {'role': 'user', 'content': '请翻译（JSON，键是编号）：\n' + json.dumps(payload, ensure_ascii=False)}]
    raw = llm.complete(s, name, msgs, json_mode=True)
    try:
        m = re.search(r'\{.*\}', raw or '', re.S)
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    tr = data.get('译文') if isinstance(data.get('译文'), dict) else data
    return [str(tr.get(str(i)) or '').strip() for i in range(len(texts))], PROVIDER_NAMES.get(name, name)


def translate_items(settings, src: Path, flip=False, crop=None, wh=None):
    """截图里的外文 → 盖在原处的中文标注（坐标换算成界面的 wh 大小）。只翻译裁剪范围里的字。"""
    from PIL import Image
    with Image.open(src) as im:
        img = im.convert('RGB')
    if flip:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    lines = ocr_lines(img)
    if crop:
        l, t, r, b = crop
        W, H = img.size
        lines = [x for x in lines if l * W <= (x['box'][0] + x['box'][2]) / 2 <= r * W and t * H <= (x['box'][1] + x['box'][3]) / 2 <= b * H]
    if not lines:
        raise ShotEditError('图里没认出文字，翻译不了')
    blocks = [b for b in group_blocks(lines) if b['text'] and is_foreign(b['text'])]
    if not blocks:
        raise ShotEditError('图里的文字已经是中文，不用翻译')
    trs, service = translate_texts(settings, [b['text'] for b in blocks])
    trs = [t if t and han_count(t) > han_count(b['text']) else '' for b, t in zip(blocks, trs)]  # 翻反了、没翻的不盖
    items = overlay_items(img, blocks, trs)
    if not items:
        raise ShotEditError('AI 没有返回译文，请重试')
    if wh and list(wh) != [img.width, img.height]:  # 界面的坐标按 wh 算（一般就是截图大小）
        kx, ky = float(wh[0]) / img.width, float(wh[1]) / img.height
        for it in items:
            if it['k'] == 'text':
                it['x'], it['y'], it['s'] = round(it['x'] * kx, 1), round(it['y'] * ky, 1), round(it['s'] * (kx + ky) / 2, 1)
            else:
                it['p'] = [[round(a * kx, 1), round(b * ky, 1)] for a, b in it['p']]
    return {'items': items, 'blocks': len([t for t in trs if t]), 'service': service}
