"""文档和书籍的来源级整理（对应知识库《外部书籍与文档整理规范》）：
- 支持原生 PDF、OCR 过的扫描 PDF、Word（.docx）、TXT / Markdown；
- 文字层负责读正文，原页面负责看图表：有图、表、公式的页面截成图片一起交给能看图的 AI；
- 以书上印的页码（P42）作为回溯锚点；书上没有页码时写「PDF 第 X 页」；
- 一章一章整理：章节 MD 按规范的固定栏目；最后生成「00、全书总览」；
- 原书图示非必要不用，每章最多 2 张，按 AI 给的位置裁剪、压缩后放进笔记旁边的 image 文件夹。
整理结果存在原文件旁边的「文件名.猛人学习.json」里，剪藏时写成 Obsidian 笔记。"""
import ctypes
import json
import os
import re
import shutil
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .config import CACHE_DIR
from .media import cache_key

DOC_EXT = {'.pdf', '.docx', '.txt', '.md'}
LIB_EXT = {'.pdf', '.docx'}           # 媒体库里列出的文档（txt / md 用「打开」）
PART_PAGES = 18                       # 一次交给 AI 的最多页数
PART_CHARS = 36000                    # 一次交给 AI 的最多字数
PART_IMAGES = 10                      # 一次交给 AI 的最多页面图
PSEUDO_PAGE = 1800                    # Word / 文本按多少字分「页」
FIG_WORDS = re.compile(r'图\s*[\d一二三四五六七八九十]|表\s*[\d一二三四五六七八九十]|如图|如表|见图|见表|Figure|Fig\.|Table|公式|示意')
CHAPTER_RE = re.compile(r'^\s*(第[一二三四五六七八九十百零〇两\d]+[章篇部讲]|Chapter\s+\d+|CHAPTER\s+\d+)[\s:：、.．]*\S*')

_cache = {}
_lock = threading.Lock()


class BookError(Exception):
    pass


# ------------------------------------------------------------------ 读取 ----
def _clean(text):
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def _label_of(text):
    """页面最上或最下单独一行的数字，当作书上印的页码。"""
    lines = [x.strip() for x in text.split('\n') if x.strip()]
    for line in (lines[-1:] + lines[:1]) if lines else []:
        m = re.fullmatch(r'[-—–·\s]*(\d{1,4})[-—–·\s]*', line)
        if m:
            return int(m.group(1))
    return None


def _read_pdf(path):
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    pdf = pdfium.PdfDocument(path)
    pages, toc = [], []
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                tp = page.get_textpage()
                text = _clean(tp.get_text_range())
                tp.close()
                w, h = page.get_size()
                n_img = n_path = 0
                scan = False
                # 直接用底层函数数图片和线条：不生成页面对象的包装，免得它们在页面关闭后才被回收，导致程序崩溃
                for k in range(c.FPDFPage_CountObjects(page.raw)):
                    obj = c.FPDFPage_GetObject(page.raw, k)
                    kind = c.FPDFPageObj_GetType(obj)
                    if kind == c.FPDF_PAGEOBJ_IMAGE:
                        n_img += 1
                        l, b, r, t = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                        if c.FPDFPageObj_GetBounds(obj, l, b, r, t) and (r.value - l.value) * (t.value - b.value) > 0.6 * w * h:
                            scan = True
                    elif kind == c.FPDF_PAGEOBJ_PATH:
                        n_path += 1
                label = None
                try:
                    pl = pdf.get_page_label(i)
                    label = int(pl) if pl and str(pl).isdigit() else None
                except Exception:
                    label = None
                pages.append({'n': i + 1, 'text': text, 'label': label or _label_of(text), 'scan': scan,
                              'figure': bool(FIG_WORDS.search(text)) if scan else (n_img > 0 or n_path >= 3)})
            finally:
                page.close()
        try:
            for item in pdf.get_toc(max_depth=3):
                if item.page_index is not None and item.title:
                    toc.append({'level': item.level, 'title': item.title.strip(), 'n': item.page_index + 1})
        except Exception:
            toc = []
        try:
            meta = pdf.get_metadata_dict()
        except Exception:
            meta = {}
    finally:
        pdf.close()
    return {'kind': 'pdf', 'pages': pages, 'toc': toc, 'title': (meta.get('Title') or '').strip(), 'author': (meta.get('Author') or '').strip()}


def _pseudo_pages(blocks):
    """把 [(文字, 是否标题, 标题级别, [图片])] 按 PSEUDO_PAGE 字切成「页」，标题尽量放在页首。"""
    pages, toc, cur, imgs = [], [], [], []
    size = 0

    def flush():
        nonlocal cur, imgs, size
        if cur or imgs:
            text = '\n'.join(cur)
            pages.append({'n': len(pages) + 1, 'text': text, 'label': None, 'scan': False, 'figure': bool(imgs) or bool(FIG_WORDS.search(text)), 'images': imgs})
        cur, imgs, size = [], [], 0
    for text, heading, level, pics in blocks:
        if heading and level == 1 and cur:  # 每章从新的一「页」开始，章节才分得开
            flush()
        if heading:
            toc.append({'level': level, 'title': text, 'n': len(pages) + 1})
        cur.append(text)
        imgs += pics
        size += len(text)
        if size >= PSEUDO_PAGE:
            flush()
    flush()
    return pages, toc


W_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
A_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def _read_docx(path):
    out_dir = CACHE_DIR / 'pages' / cache_key(path)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read('word/document.xml'))
        rels = {}
        if 'word/_rels/document.xml.rels' in z.namelist():
            for r in ET.fromstring(z.read('word/_rels/document.xml.rels')):
                rels[r.get('Id')] = r.get('Target')
        core = {}
        if 'docProps/core.xml' in z.namelist():
            for el in ET.fromstring(z.read('docProps/core.xml')):
                core[el.tag.split('}')[-1]] = (el.text or '').strip()
        body = root.find(W_NS + 'body')
        blocks, k = [], 0
        for el in list(body) if body is not None else []:
            tag = el.tag.split('}')[-1]
            if tag == 'p':
                style = el.find(f'{W_NS}pPr/{W_NS}pStyle')
                sval = (style.get(W_NS + 'val') if style is not None else '') or ''
                m = re.search(r'(?:Heading|heading|标题)\s*(\d)', sval) or (re.fullmatch(r'(\d)', sval) if sval else None)
                level = int(m.group(1)) if m else 0
                text = ''.join(t.text or '' for t in el.iter(W_NS + 't')).strip()
                pics = []
                for blip in el.iter(A_NS + 'blip'):
                    target = rels.get(blip.get(R_NS + 'embed'))
                    if target:
                        name = 'word/' + target.lstrip('/') if not target.startswith('word/') else target
                        try:
                            data = z.read(name)
                        except KeyError:
                            continue
                        k += 1
                        dest = out_dir / f'docx_{k:03d}{Path(target).suffix.lower() or ".png"}'
                        dest.write_bytes(data)
                        pics.append(str(dest))
                if pics:
                    text = (text + ' ' if text else '') + ' '.join(f'［图片{n}］' for n in range(k - len(pics) + 1, k + 1))
                if text:
                    if not level and CHAPTER_RE.match(text) and len(text) < 40:
                        level = 1
                    blocks.append((text, bool(level), level, pics))
            elif tag == 'tbl':
                rows = []
                for tr in el.iter(W_NS + 'tr'):
                    cells = [''.join(t.text or '' for t in tc.iter(W_NS + 't')).strip() for tc in tr.iter(W_NS + 'tc')]
                    rows.append('| ' + ' | '.join(cells) + ' |')
                if rows:
                    rows.insert(1, '|' + ' --- |' * (rows[0].count('|') - 1))
                    blocks.append(('\n'.join(rows), False, 0, []))
    pages, toc = _pseudo_pages(blocks)
    return {'kind': 'docx', 'pages': pages, 'toc': toc, 'title': core.get('title', ''), 'author': core.get('creator', '')}


def _read_text(path):
    raw = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise BookError('读不出这个文件的文字，请用 UTF-8 编码保存后再试')
    blocks = []
    for para in re.split(r'\n\s*\n|\n(?=#)', text.replace('\r\n', '\n')):
        para = para.strip()
        if not para:
            continue
        m = re.match(r'^(#{1,3})\s+(.+)$', para.split('\n')[0])
        if m:
            blocks.append((m.group(2).strip(), True, len(m.group(1)), []))
            rest = '\n'.join(para.split('\n')[1:]).strip()
            if rest:
                blocks.append((rest, False, 0, []))
        elif CHAPTER_RE.match(para) and len(para.split('\n')[0]) < 40:
            first, _, rest = para.partition('\n')
            blocks.append((first.strip(), True, 1, []))
            if rest.strip():
                blocks.append((rest.strip(), False, 0, []))
        else:
            blocks.append((para, False, 0, []))
    pages, toc = _pseudo_pages(blocks)
    return {'kind': 'text', 'pages': pages, 'toc': toc, 'title': '', 'author': ''}


def read(path):
    """读取文档（按文件修改时间缓存）。返回 {kind, pages:[{n, text, label, scan, figure}], toc, title, author, shift}。"""
    path = os.path.abspath(path)
    try:
        key = (path.lower(), os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        raise BookError('找不到这个文件：' + path)
    with _lock:
        if key in _cache:
            return _cache[key]
    ext = Path(path).suffix.lower()
    try:
        if ext == '.pdf':
            info = _read_pdf(path)
        elif ext == '.docx':
            info = _read_docx(path)
        elif ext in ('.txt', '.md'):
            info = _read_text(path)
        else:
            raise BookError('不支持这种文档。支持 PDF、Word（.docx）、TXT 和 Markdown')
    except BookError:
        raise
    except Exception as e:
        raise BookError(f'读不出这个文档：{e}')
    info['shift'] = detect_shift(info['pages'])
    info['scan'] = sum(1 for p in info['pages'] if p['scan']) > len(info['pages']) / 2
    info['chars'] = sum(len(p['text']) for p in info['pages'])
    with _lock:
        _cache.clear()
        _cache[key] = info
    return info


def detect_shift(pages):
    """书上页码和 PDF 页序的差（PDF 第 n 页 = 书上第 n - shift 页）。至少两页对得上才算。"""
    votes = Counter(p['n'] - p['label'] for p in pages if p.get('label'))
    if not votes:
        return None
    shift, count = votes.most_common(1)[0]
    return shift if count >= 2 or (count >= 1 and len(pages) <= 3) else None


def page_ref(n, shift):
    """回溯锚点：书上有页码时写 P42，没有时写「PDF 第 X 页」。"""
    if shift is not None and n - shift >= 1:
        return f'P{n - shift}'
    return f'PDF 第 {n} 页'


def range_ref(a, b, shift):
    if shift is not None and a - shift >= 1:
        return f'P{a - shift}' + (f'-P{b - shift}' if b != a else '')
    return f'PDF 第 {a}' + (f'-{b}' if b != a else '') + ' 页'


# ------------------------------------------------------------------ 章节 ----
def detect_chapters(info):
    """章节：PDF 书签 / Word 标题 / 文本里的「第X章」；都没有时按页数分段。返回 [{title, start, end}]（PDF 页序，含首尾）。"""
    pages = info['pages']
    total = len(pages)
    if not total:
        return []
    starts = []
    toc = info.get('toc') or []
    if toc:
        top = min(x['level'] for x in toc)
        starts = [(x['title'], x['n']) for x in toc if x['level'] == top]
        if len(starts) < 2:  # 只有一个顶层（书名）时用下一级
            lv = sorted({x['level'] for x in toc})
            if len(lv) > 1:
                starts = [(x['title'], x['n']) for x in toc if x['level'] == lv[1]]
    if len(starts) < 2:
        starts = []
        for p in pages:
            for line in [x.strip() for x in p['text'].split('\n') if x.strip()][:3]:
                if CHAPTER_RE.match(line) and len(line) < 40 and '…' not in line and '...' not in line and not re.search(r'\d+\s*$', line):
                    starts.append((line, p['n']))
                    break
    seen, uniq = set(), []
    for title, n in sorted(starts, key=lambda x: x[1]):
        if n not in seen:
            seen.add(n)
            uniq.append((title, n))
    if not uniq:
        size = 30 if total > 40 else total
        return [{'title': (f'第 {a}–{min(total, a + size - 1)} 页' if total > size else '全文'), 'start': a, 'end': min(total, a + size - 1)}
                for a in range(1, total + 1, size)]
    out = []
    if uniq[0][1] > 1:
        front = range(1, uniq[0][1])
        if sum(len(pages[i - 1]['text']) for i in front) > 400:
            out.append({'title': '前言与目录', 'start': 1, 'end': uniq[0][1] - 1})
    for k, (title, n) in enumerate(uniq):
        end = uniq[k + 1][1] - 1 if k + 1 < len(uniq) else total
        out.append({'title': title, 'start': n, 'end': max(n, end)})
    return out


TOC_PROMPT = (
    '下面是一本书 / 一份文档前面几页的文字（每页前面标了 PDF 页序和书上页码）。请根据目录页或正文标题，列出全书的章（只要最上一级的章，不要节）。\n'
    '只输出 JSON：{"章": [{"标题": "第一章 ……", "书上页码": 1}]}。书上页码就是目录里写的页码；目录里没有页码时，写这一章开始的 PDF 页序，并加上 "PDF": true。看不出章节就输出 {"章": []}。'
)


def ai_chapters(settings, name, info):
    """让 AI 读目录页找章节。"""
    from . import llm
    shift = info.get('shift')
    head = []
    for p in info['pages'][:25]:
        head.append(f'【PDF 第 {p["n"]} 页｜{page_ref(p["n"], shift)}】\n{p["text"][:1500]}')
    data = llm._json_from(llm.complete(settings, name, [{'role': 'user', 'content': TOC_PROMPT + '\n\n' + '\n\n'.join(head)}], json_mode=True))
    items = data.get('章', []) if isinstance(data, dict) else []
    total = len(info['pages'])
    starts = []
    for x in items:
        try:
            title, num = str(x['标题']).strip(), int(x['书上页码'])
        except (KeyError, TypeError, ValueError):
            continue
        n = num if (x.get('PDF') or shift is None) else num + shift
        if title and 1 <= n <= total:
            starts.append((title, n))
    if not starts:
        return []
    info = dict(info, toc=[{'level': 1, 'title': t, 'n': n} for t, n in starts])
    return detect_chapters(info)


# ------------------------------------------------------------------ 页面图 ----
def pages_dir(path):
    return CACHE_DIR / 'pages' / cache_key(path)


def render_page(path, n, width=1100):
    """把 PDF 第 n 页渲染成 JPG（缓存）。"""
    out = pages_dir(path) / f'p{n:04d}_{width}.jpg'
    if out.is_file() and out.stat().st_size > 0:
        return out
    import pypdfium2 as pdfium
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(path)
    try:
        page = pdf[n - 1]
        w, _ = page.get_size()
        img = page.render(scale=max(0.5, width / max(1.0, w))).to_pil().convert('RGB')
        page.close()
    finally:
        pdf.close()
    tmp = out.with_name(out.stem + '.part.jpg')
    img.save(tmp, 'JPEG', quality=82)
    os.replace(tmp, out)
    return out


def page_image(path, info, n):
    """交给 AI 看的页面图：PDF 渲染；Word 用文档里的图片（第一张）。"""
    if info['kind'] == 'pdf':
        return [str(render_page(path, n))]
    return list(info['pages'][n - 1].get('images') or [])[:4]


def crop_figure(path, info, n, region, dest: Path, image_index=0):
    """按 AI 给的位置（页面宽高的比例：左, 上, 右, 下）裁出原书图示，缩到 960 宽压缩保存。"""
    from PIL import Image
    if info['kind'] == 'pdf':
        src = render_page(path, n, width=1600)
    else:
        imgs = info['pages'][n - 1].get('images') or []
        if not imgs:
            raise BookError('这一页没有图片')
        src = imgs[min(image_index, len(imgs) - 1)]
    img = Image.open(src).convert('RGB')
    W, H = img.size
    if region and len(region) == 4:
        x0, y0, x1, y1 = [max(0.0, min(1.0, float(v))) for v in region]
        if x1 - x0 > 0.05 and y1 - y0 > 0.05:
            m = 0.025
            img = img.crop((int(max(0, x0 - m) * W), int(max(0, y0 - m) * H), int(min(1, x1 + m) * W), int(min(1, y1 + m) * H)))
    if img.width > 960:
        img = img.resize((960, int(img.height * 960 / img.width)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, 'JPEG', quality=78, optimize=True)
    return dest


# ------------------------------------------------------------------ 整理 ----
DEFAULT_RULES = """整理要求（来源级整理，只回答「这一章作者实际讲了什么」）：
- 准确、完整、忠实、可回溯。允许压缩表达，不允许压缩知识；作者的定义、观点、论证、方法、规则、条件、案例、数字、公式、口诀都要保留。
- 不加入原书没有的知识，不补全作者没给出的规则，不评价作者观点，不用外部常识纠正作者，不把作者观点写成已验证的事实，不主动和别的理论统一术语。
- 文字层负责读正文，原页面图负责校正：书名、章节名、术语、数字、公式、图号表号以页面图为准；图表、流程图、结构图要读出节点、箭头、层级和对应关系，不能只抄图里的字；案例图和作者的说明放在一起写。
- 每段关键内容后面标回溯锚点：书上有页码的写（P42）或（P42-P45），没有页码的写（PDF 第 X 页）；锚点用下面每页开头标注的写法。
- 疑点三分法：来源有歧义且会改变知识含义的，列入「需要人工确认的位置」；本章本来没讲的，写「本章未提供 / 未明确」；作者明确给出但没有验证材料的观点、数字、因果判断，保留为作者观点，注明本阶段不验证。三者不能混。
- 文字层明显错（OCR 错字）而页面图能确认的，直接按页面图改，并记进「已确认的 OCR 修正」。
- 原书图示非必要不用，知识库要尽量小：只有文字确实说不清的结构图、流程图、关键表格、交易案例图才插，每章最多 2 张；能用文字写清的一律不插。
- 结构固定：先用 1–2 句话之后，按下面的二级标题依次写（没有实际内容的可选栏目整节省略，不要写「无」）：
  ## 一、本章主题
  ## 二、核心内容（按原书小节顺序，用 ### 1. 小节名（P1-P4） 分类，必要时 ####）
  ## 三、重要概念 / 定义
  ## 四、作者观点 / 论证
  ## 五、方法 / 规则 / 逻辑
  ## 六、案例 / 图表说明
  ## 七、完整知识框架
  ## 八、关键结论
  ## 九、原书图示（可选）
  ## 十、关键页码索引（表格：| 页码 | 内容 |）
  ## 十一、需要人工确认的位置（可选）
  ## 十二、已确认的 OCR 修正（可选）
  ## 十三、一句话概括
- 不要写一级标题（#），不要开场白和结束语。"""

FIGURE_RULE = (
    '插原书图示的写法：在需要的位置另起一行写 `![[原书图示 第N页 图号 图名 @左,上,右,下]]`，'
    'N 是下面标注里的 PDF 页序；@ 后面是这张图在页面上的位置，用页面宽高的比例（0 到 1）表示，比如 @0.08,0.30,0.92,0.75，尽量刚好框住图和图号；'
    '下一行写说明：*原书图示｜图 1-1 五行结构（P2）*。'
)


def _rules(settings):
    p = (settings.get('book_rules_file') or '').strip()
    if p and os.path.isfile(p):
        from .config import read_text_file
        try:
            return ('以下是用户知识库里正式生效的《外部书籍与文档整理规范》，以它为准（其中关于独立审计、放行、归档、Obsidian 目录的部分由用户另行处理，你只做整理）：\n\n'
                    + read_text_file(p)[:40000] + '\n\n' + DEFAULT_RULES)
        except (OSError, ValueError):
            pass
    return DEFAULT_RULES


def _book_head(meta, chapter, info, teacher):
    from . import llm
    lines = [f'书名：{meta.get("title") or ""}', f'作者：{meta.get("author") or "（未填写）"}',
             f'本次整理：{chapter["title"]}，{range_ref(chapter["start"], chapter["end"], info.get("shift"))}'
             + (f'（PDF 第 {chapter["start"]}-{chapter["end"]} 页）' if info['kind'] == 'pdf' else ''),
             '来源类型：' + {'pdf': '扫描 PDF（OCR 文字层 + 原页面图）' if info.get('scan') else 'PDF（文字层 + 原页面图）',
                         'docx': 'Word 文档', 'text': '文本文档'}[info['kind']]]
    tb = llm.teacher_block(teacher) if teacher else ''
    if tb:
        lines.append('作者 / 来源的术语说明（只用来识别术语、校正 OCR，不能当成本章讲过的内容）：\n' + tb)
    return '\n'.join(lines)


def _parts(info, chapter):
    pages = info['pages'][chapter['start'] - 1:chapter['end']]
    parts, cur, size = [], [], 0
    for p in pages:
        if cur and (len(cur) >= PART_PAGES or size + len(p['text']) > PART_CHARS):
            parts.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p['text'])
    if cur:
        parts.append(cur)
    return parts


def _content(path, info, pages, with_images):
    """一组页面：每页的文字（标好锚点），有图表的页附上页面图。"""
    shift = info.get('shift')
    flagged = [p for p in pages if p['figure'] or (info.get('scan') and len(p['text']) < 200)]
    flagged.sort(key=lambda p: (not FIG_WORDS.search(p['text']), p['n']))
    pick = {p['n'] for p in flagged[:PART_IMAGES]} if with_images else set()
    parts = []
    for p in pages:
        ref = page_ref(p['n'], shift)
        parts.append({'type': 'text', 'text': f'\n【PDF 第 {p["n"]} 页｜锚点写作（{ref}）】\n' + (p['text'] or '（这一页没有文字层）')})
        if p['n'] in pick:
            for img in page_image(path, info, p['n']):
                parts.append({'type': 'text', 'text': f'（PDF 第 {p["n"]} 页的页面图）'})
                parts.append({'type': 'image', 'path': img})
    return parts


def _figure_note(with_images):
    return FIGURE_RULE if with_images else '这次没有页面图（没有设置能看图的 AI）：不要插原书图示；图表只能按文字层整理，看不到的图形关系写「需视觉核验」。'


def organize_chapter(settings, name, path, info, chapter, meta, teacher=None, vision=True, progress=lambda f, m=None: None, cancel=None):
    """整理一章，返回章节 Markdown（不含属性和标题，由剪藏时加上）。"""
    from . import llm
    rules = _rules(settings)
    head = _book_head(meta, chapter, info, teacher)
    parts = _parts(info, chapter)
    system = rules + '\n\n' + _figure_note(vision)
    if len(parts) == 1:
        progress(0.1, f'正在整理「{chapter["title"]}」…')
        content = [{'type': 'text', 'text': head + '\n\n请整理这一章。以下是这一章的全部页面：'}] + _content(path, info, parts[0], vision)
        md = llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}])
    else:
        notes = []
        for k, pages in enumerate(parts, 1):
            if cancel is not None and cancel.is_set():
                raise BookError('已取消')
            progress(0.8 * (k - 1) / len(parts), f'正在整理「{chapter["title"]}」第 {k}/{len(parts)} 段…')
            content = [{'type': 'text', 'text': head + f'\n\n这一章比较长，分 {len(parts)} 段读。这是第 {k} 段（{range_ref(pages[0]["n"], pages[-1]["n"], info.get("shift"))}）。'
                        '请按原书顺序写出这一段的详细整理笔记：保留全部知识点、数字、图表关系和锚点，不要写栏目框架，后面会把各段合成一章。'
                        '需要插原书图示的地方照样写插图标记。'}] + _content(path, info, pages, vision)
            notes.append(llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}]))
        progress(0.85, f'正在合成「{chapter["title"]}」…')
        joined = '\n\n'.join(f'===== 第 {k} 段笔记 =====\n{n}' for k, n in enumerate(notes, 1))
        md = llm.complete(settings, name, [{'role': 'system', 'content': system}, {'role': 'user', 'content':
                          head + '\n\n下面是这一章分段读完后的整理笔记（已经带锚点和插图标记）。请把它们合成这一章的最终整理稿：按固定栏目写，'
                          '不能丢掉笔记里的任何知识点、数字和锚点，插图标记原样保留（每章最多 2 张）。\n\n' + joined}])
    return _strip_h1(md)


def _strip_h1(md):
    lines = (md or '').strip().split('\n')
    while lines and (not lines[0].strip() or re.match(r'^#\s', lines[0])):
        lines.pop(0)
    return '\n'.join(lines).strip()


_PREF = re.compile(r'P(\d{1,4})(?:\s*[-–—~至]\s*P?(\d{1,4}))?')
_PDFREF = re.compile(r'PDF\s*第\s*(\d{1,4})(?:\s*[-–—~至]\s*(\d{1,4}))?\s*页')


def cited_pages(md, shift):
    """整理稿里引用到的 PDF 页序。"""
    out = set()
    for m in _PREF.finditer(md or ''):
        if shift is None:
            continue
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b >= a and b - a < 200:
            out.update(range(a + shift, b + shift + 1))
    for m in _PDFREF.finditer(md or ''):
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if b >= a and b - a < 200:
            out.update(range(a, b + 1))
    return out


def uncovered_pages(info, chapter, md):
    cited = cited_pages(md, info.get('shift'))
    return [p for p in info['pages'][chapter['start'] - 1:chapter['end']] if len(p['text'].strip()) >= 150 and p['n'] not in cited]


BOOK_REVIEW = (
    '你是书籍整理稿的查漏员。下面是一章的整理稿，以及整理稿里一次都没有引用到的几页原文。'
    '请检查这些页里有没有整理稿漏掉的、有知识意义的内容（定义、观点、论证、方法、规则、条件、案例、数字、公式、图表说明）。'
    '已经写到的（哪怕换了说法、标在别的页码上）不算漏；目录、版权页、空白、重复内容不算漏。'
    '只写原书讲了的，不补充、不评价；每条带锚点（按原文每页开头标注的写法）。'
    '只输出 JSON：{"补充": [{"放在": "整理稿里已有的小标题原文", "内容": "- 一条 Markdown 要点（P42）"}]}；没有漏掉就输出 {"补充": []}。'
)


def review_chapter(settings, name, info, chapter, md, meta, teacher=None):
    """查漏：没被引用到的页让 AI 再看一遍，补进对应小节。返回 (新稿, 补了几条, 没引用的页数)。"""
    from . import llm
    miss = uncovered_pages(info, chapter, md)
    if not miss:
        return md, 0, 0
    shift = info.get('shift')
    pages_text = '\n\n'.join(f'【PDF 第 {p["n"]} 页｜锚点写作（{page_ref(p["n"], shift)}）】\n{p["text"]}' for p in miss[:15])
    raw = llm.complete(settings, name, [{'role': 'system', 'content': BOOK_REVIEW}, {'role': 'user', 'content':
                       _book_head(meta, chapter, info, teacher) + '\n\n===== 整理稿 =====\n' + md + '\n\n===== 没有被引用到的页 =====\n' + pages_text}],
                       json_mode=True)
    try:
        data = llm._json_from(raw)
    except ValueError:
        return md, 0, len(miss)
    adds = [a for a in (data.get('补充', []) if isinstance(data, dict) else []) if isinstance(a, dict)]
    return (llm.insert_additions(md, adds) if adds else md), len(adds), len(miss)


OVERVIEW_PROMPT = """请根据下面各章的整理稿，写这本书的「00、全书总览」。要求：
- 只依据这些整理稿，不加入外部知识，不评价作者观点；
- 只完成了部分章节时，必须写明已完成范围，不能把局部内容扩写成整本书的结论；
- 结构用二级标题依次写（没有内容的省略）：
  ## 一、书籍基本信息（书名、作者、来源类型、整理进度；没有的信息写「未提供」）
  ## 二、全书主旨
  ## 三、章节结构与整理进度（表格：| 章 | 页码 | 主题 | 状态 |）
  ## 四、作者整体框架
  ## 五、主要概念地图
  ## 六、关键结论索引（每条带页码锚点）
  ## 七、各章导航（用 [[文件名]] 链接，文件名见下面每章开头）
  ## 八、当前整理进度与来源边界
- 不要写一级标题（#），不要开场白和结束语。"""


def overview(settings, name, meta, chapters, files):
    from . import llm
    done = [(c, f) for c, f in zip(chapters, files) if (c.get('md') or '').strip()]
    if not done:
        raise BookError('还没有整理好的章节')
    body = '\n\n'.join(f'===== 文件名：{f}｜{c["title"]}（{c.get("range", "")}） =====\n{c["md"]}' for c, f in done)
    todo = [c['title'] for c in chapters if not (c.get('md') or '').strip()]
    head = (f'书名：{meta.get("title") or ""}\n作者：{meta.get("author") or "（未填写）"}\n'
            f'全书章节：{"、".join(c["title"] for c in chapters)}\n'
            + (f'还没有整理的章节：{"、".join(todo)}\n' if todo else '全部章节都已整理。\n'))
    return _strip_h1(llm.complete(settings, name, [{'role': 'user', 'content': OVERVIEW_PROMPT + '\n\n' + head + '\n' + body}]))


# ------------------------------------------------------------------ 剪藏 ----
_FIG = re.compile(r'!\[\[\s*原书图示\s*第\s*(\d{1,4})\s*页\s*([^@\]]*?)\s*(?:@\s*([\d.\s,，]+))?\s*\]\]')


def place_figures(md, path, info, note_dir: Path):
    """把整理稿里的 ![[原书图示 第N页 图1-1 名称 @区域]] 换成裁好的图片。"""
    if not md or not _FIG.search(md):
        return md

    def repl(m):
        n = int(m.group(1))
        label = re.sub(r'\s+', '-', (m.group(2) or '').strip()) or f'第{n}页'
        region = [x for x in re.split(r'[,，\s]+', (m.group(3) or '').strip()) if x]
        if not (1 <= n <= len(info['pages'])):
            return f'（原书图示：{label}，页码不对）'
        from .doc import safe_name
        name = safe_name(f'原书图示-{label}')[:80] + '.jpg'
        try:
            crop_figure(path, info, n, region if len(region) == 4 else None, note_dir / 'image' / name)
        except Exception:
            return f'（原书图示：{label}，裁图失败）'
        return f'![[image/{name}]]'
    return _FIG.sub(repl, md)


def _yaml(v):
    return '"' + str(v or '').replace('\\', '/').replace('"', "'") + '"'


def _old_status(p: Path):
    try:
        head = p.read_text(encoding='utf-8', errors='replace')[:3000]
    except OSError:
        return None
    m = re.search(r'(?m)^status:\s*"?(.*?)"?\s*$', head.split('\n---', 1)[0])
    return m.group(1).strip() if m and m.group(1).strip() else None


def chapter_file(k, title):
    from .doc import safe_name
    return f'{k:02d}、{safe_name(title)}'


def clip_book(doc, info, vault, folder, dir_pattern='《{title}》{author}'):
    """写进 Obsidian：《书名》作者/00、全书总览.md + 01、章节.md … + image/。返回 {dir, files, uri}。"""
    from .doc import safe_name
    if not vault or not Path(vault).is_dir():
        raise OSError('找不到 Obsidian 库文件夹，请到「设置 → Obsidian」检查库的位置')
    title = (doc.get('title') or Path(doc['media']).stem).strip('《》 ')
    author = (doc.get('author') or '').strip()
    dname = safe_name((dir_pattern or '《{title}》{author}').replace('{title}', title).replace('{author}', author).strip())
    base = Path(vault) / (folder or '') / dname
    base.mkdir(parents=True, exist_ok=True)
    shift = doc.get('shift') if doc.get('shift') is not None else info.get('shift')
    src_type = {'pdf': '扫描 PDF（OCR 文字层 + 原页面图）' if info.get('scan') else 'PDF（文字层 + 原页面图）', 'docx': 'Word 文档', 'text': '文本文档'}[info['kind']]
    written = []
    chapters = doc.get('chapters') or []
    for k, c in enumerate(chapters, 1):
        if not (c.get('md') or '').strip():
            continue
        fname = chapter_file(k, c['title'])
        path = base / (fname + '.md')
        rng = range_ref(c['start'], c['end'], shift)
        status = _old_status(path) or '待审计'
        body = place_figures(c['md'], doc['media'], info, base)
        lines = ['---', f'title: {_yaml(c["title"])}', 'type: 书籍章节整理', f'status: {_yaml(status)}', f'book: {_yaml(title)}',
                 f'author: {_yaml(author)}', f'chapter: {_yaml(c["title"])}', f'page_range: {_yaml(rng)}',
                 f'source_type: {_yaml(src_type)}', f'source: {_yaml(doc["media"])}', f'organized: {time.strftime("%Y-%m-%d %H:%M")}',
                 'tags:', '  - 外部原始资料', '  - 书籍', '  - 猛人学习', f'  - {safe_name(title).replace(" ", "_")}', '---', '',
                 f'# {c["title"]}', '',
                 f'> 来源：《{title}》' + (f'，{author}' if author else '') + '。', '>',
                 f'> 整理范围：{rng}' + (f'（PDF 第 {c["start"]}-{c["end"]} 页）' if info['kind'] == 'pdf' and shift is not None else '') + '。', '>',
                 '> 整理边界：只忠实说明本章如何表述，不使用外部常识纠正作者理论，不评价或验证作者观点。', '', '---', '', body.strip(), '']
        path.write_text('\n'.join(lines), encoding='utf-8')
        written.append(str(path))
    if (doc.get('overview') or '').strip():
        path = base / '00、全书总览.md'
        status = _old_status(path) or '待审计'
        done = [c['title'] for c in chapters if (c.get('md') or '').strip()]
        lines = ['---', f'title: {_yaml(title + " 全书总览")}', 'type: 书籍总览', f'status: {_yaml(status)}', f'book: {_yaml(title)}',
                 f'author: {_yaml(author)}', f'source_type: {_yaml(src_type)}', f'source: {_yaml(doc["media"])}',
                 f'completed_scope: {_yaml("、".join(done) if len(done) < len(chapters) else "全书")}',
                 'tags:', '  - 外部原始资料', '  - 书籍', '  - 猛人学习', f'  - {safe_name(title).replace(" ", "_")}', '---', '',
                 f'# 《{title}》全书总览', '', doc['overview'].strip(), '']
        path.write_text('\n'.join(lines), encoding='utf-8')
        written.insert(0, str(path))
    if not written:
        raise BookError('还没有整理好的章节')
    from urllib.parse import quote
    rel = Path(written[0]).relative_to(Path(vault)).as_posix()
    return {'dir': str(base), 'files': written, 'uri': f'obsidian://open?vault={quote(Path(vault).name)}&file={quote(rel)}',
            'time': time.strftime('%Y-%m-%d %H:%M:%S')}


def new_doc(path, info):
    stem = Path(path).stem
    m = re.match(r'^《(.+?)》\s*(.*)$', stem)
    return {
        'version': 1, 'kind': 'book', 'media': os.path.abspath(path),
        'title': info.get('title') or (m.group(1) if m else stem), 'author': info.get('author') or (m.group(2).strip() if m else ''),
        'segments': [], 'summary': '', 'chat': [], 'clip': None,
        'shift': info.get('shift'), 'chapters': [dict(c, md='') for c in detect_chapters(info)], 'overview': '',
        'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'updated': None,
    }
