"""电子书、老版 Word、演示文稿的读取（2026-10-14，按微信读书能导入的格式补齐，TXT 除外）：
- EPUB：按书脊（spine）顺序读每个 XHTML，目录（nav / ncx）里的条目当章名，图片抽出来；
- MOBI / AZW3 / AZW（Kindle）：PalmDB 文件，文字记录用 PalmDOC 压缩或不压缩；有 DRM 加密、或用 HUFF/CDIC 压缩的读不了，明确提示；
- DOC（Word 97-2003）：用 olefile 读 WordDocument 流，按 piece table 取正文；没有标题层级，分章靠「第X章」；
- PPTX / PPT：一张幻灯片一「页」，幻灯片标题写在最前面；PPT 用 olefile 读 PowerPoint Document 流里的文字记录。
文档类的统一交给 book._pseudo_pages 切成「页」（PDF 以外的「页」= 段落块），演示文稿直接一张一页。
图片存到缓存 pages\\<文件指纹>\\ 里（book.pages_dir），整理时有图的页把图片交给能看图的 AI。"""
import io
import posixpath
import re
import struct
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


class EbookError(Exception):
    pass


IMG_OK = {'.jpg', '.jpeg', '.png'}


def save_image(data: bytes, dest_dir: Path, name: str):
    """存一张图片；GIF / WEBP / BMP 转成 PNG（AI 不一定认），SVG 和读不出的跳过。返回路径或 None。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(name).suffix.lower()
    if data[:3] == b'\xff\xd8\xff':
        ext = '.jpg'
    elif data[:8] == b'\x89PNG\r\n\x1a\n':
        ext = '.png'
    if ext in IMG_OK:
        p = dest_dir / (Path(name).stem + ext)
        p.write_bytes(data)
        return str(p)
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        p = dest_dir / (Path(name).stem + '.png')
        img.convert('RGBA' if img.mode in ('P', 'RGBA', 'LA') else 'RGB').save(p, 'PNG')
        return str(p)
    except Exception:  # noqa: BLE001  图片读不出不影响正文
        return None


# ------------------------------------------------------------- HTML → 块 ----
class _Blocks(HTMLParser):
    """把（X）HTML 拆成 [(文字, 是否标题, 级别, [图片])]：h1-h3 当标题，p / div / li / br 等分段，img 交给 on_img 取图片路径。"""
    BLOCK = {'p', 'div', 'li', 'blockquote', 'section', 'article', 'tr', 'table', 'ul', 'ol', 'pre', 'dd', 'dt', 'figure', 'figcaption', 'body'}

    def __init__(self, on_img=None, heading_shift=0):
        super().__init__(convert_charrefs=True)
        self.on_img = on_img
        self.shift = heading_shift
        self.blocks, self.buf, self.pics = [], [], []
        self.level = 0
        self.skip = 0

    def flush(self):
        text = re.sub(r'[ \t　\xa0]+', ' ', ''.join(self.buf)).strip()
        text = re.sub(r'\s*\n\s*', '\n', text)
        if text or self.pics:
            if self.pics:
                text = (text + ' ' if text else '') + '［图片］'
            self.blocks.append((text, bool(self.level), min(3, self.level + self.shift) if self.level else 0, self.pics))
        self.buf, self.pics = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'head', 'title'):
            self.skip += 1
            return
        if re.fullmatch(r'h[1-6]', tag):
            self.flush()
            self.level = min(3, int(tag[1]))
        elif tag in self.BLOCK:
            self.flush()
        elif tag == 'br':
            self.buf.append('\n')
        elif tag in ('img', 'image') and self.on_img:
            a = dict(attrs)
            src = a.get('src') or a.get('xlink:href') or a.get('href') or a.get('recindex') or ''
            if a.get('recindex'):
                src = 'recindex:' + a['recindex']
            p = self.on_img(src) if src else None
            if p:
                self.pics.append(p)
        elif tag in ('td', 'th'):
            self.buf.append(' | ')

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'head', 'title'):
            self.skip = max(0, self.skip - 1)
            return
        if re.fullmatch(r'h[1-6]', tag):
            self.flush()
            self.level = 0
        elif tag in self.BLOCK:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data.replace('\n', ' '))

    def result(self):
        self.flush()
        return self.blocks


def html_blocks(html: str, on_img=None, heading_shift=0):
    p = _Blocks(on_img, heading_shift)
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001  个别页面标签不规范：已经解析出来的照样用
        pass
    return p.result()


def _decode(data: bytes):
    m = re.search(rb'<\?xml[^>]*encoding=["\']([\w-]+)', data[:200]) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', data[:2000], re.I)
    for enc in ([m.group(1).decode('ascii', 'ignore')] if m else []) + ['utf-8', 'gb18030']:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', 'replace')


# ------------------------------------------------------------------ EPUB ----
def _local(tag):
    return tag.rsplit('}', 1)[-1]


def read_epub(path, img_dir: Path):
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise EbookError('这个 EPUB 文件坏了，读不出来')
    with z:
        names = set(z.namelist())
        if 'META-INF/encryption.xml' in names and b'EncryptedData' in z.read('META-INF/encryption.xml') \
                and b'font' not in z.read('META-INF/encryption.xml').lower():
            raise EbookError('这本电子书有 DRM 加密，读不出来（从书店买的加密书都这样），请换一个没有加密的版本')
        try:
            cont = ET.fromstring(z.read('META-INF/container.xml'))
            opf_path = next(el.get('full-path') for el in cont.iter() if _local(el.tag) == 'rootfile')
        except (KeyError, StopIteration, ET.ParseError):
            raise EbookError('这个 EPUB 文件不完整（找不到 content.opf）')
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(z.read(opf_path))
        meta = {'title': '', 'author': ''}
        manifest, spine, toc_id, nav_href = {}, [], None, None
        for el in opf.iter():
            t = _local(el.tag)
            if t == 'title' and not meta['title']:
                meta['title'] = (el.text or '').strip()
            elif t == 'creator' and not meta['author']:
                meta['author'] = (el.text or '').strip()
            elif t == 'item':
                href = posixpath.normpath(posixpath.join(base, unquote(el.get('href') or '')))
                manifest[el.get('id')] = (href, el.get('media-type') or '')
                if 'nav' in (el.get('properties') or '').split():
                    nav_href = href
            elif t == 'spine':
                toc_id = el.get('toc')
            elif t == 'itemref' and el.get('linear') != 'no':
                spine.append(el.get('idref'))
        titles = _epub_toc(z, manifest.get(toc_id, (None,))[0], nav_href)
        blocks, k = [], [0]
        for idref in spine:
            href = manifest.get(idref, (None,))[0]
            if not href or href not in names:
                continue
            folder = posixpath.dirname(href)

            def on_img(src, folder=folder):
                name = posixpath.normpath(posixpath.join(folder, unquote(src.split('#')[0])))
                if name not in names:
                    return None
                k[0] += 1
                return save_image(z.read(name), img_dir, f'epub_{k[0]:04d}{Path(name).suffix}')
            title = titles.get(href)
            part = html_blocks(_decode(z.read(href)), on_img, heading_shift=1 if title else 0)
            if title:
                if part and part[0][1] and part[0][0].replace(' ', '') == title.replace(' ', ''):
                    part = part[1:]  # 正文第一行就是章名：不重复
                part.insert(0, (title, True, 1, []))
            blocks += part
    return blocks, meta


def _epub_toc(z, ncx_href, nav_href):
    """目录：{章节文件: 章名}（每个文件取目录里第一条）。"""
    out = {}
    try:
        if nav_href:
            root = ET.fromstring(z.read(nav_href))
            folder = posixpath.dirname(nav_href)
            for nav in root.iter():
                if _local(nav.tag) == 'nav' and 'toc' in (nav.get('{http://www.idpf.org/2007/ops}type') or nav.get('epub:type') or 'toc'):
                    for a in nav.iter():
                        if _local(a.tag) == 'a' and a.get('href'):
                            f = posixpath.normpath(posixpath.join(folder, unquote(a.get('href').split('#')[0])))
                            out.setdefault(f, ''.join(a.itertext()).strip())
                    break
        if not out and ncx_href:
            root = ET.fromstring(z.read(ncx_href))
            folder = posixpath.dirname(ncx_href)
            for np_ in root.iter():
                if _local(np_.tag) == 'navPoint':
                    label = next((''.join(t.itertext()).strip() for t in np_.iter() if _local(t.tag) == 'text'), '')
                    content = next((t.get('src') for t in np_.iter() if _local(t.tag) == 'content'), None)
                    if content and label:
                        f = posixpath.normpath(posixpath.join(folder, unquote(content.split('#')[0])))
                        out.setdefault(f, label)
    except (KeyError, ET.ParseError):
        pass
    return {k: v for k, v in out.items() if v}


# ----------------------------------------------------------- MOBI / AZW3 ----
def palmdoc_decompress(data: bytes) -> bytes:
    """PalmDOC（LZ77 变体）解压。"""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        i += 1
        if 1 <= c <= 8:
            out += data[i:i + c]
            i += c
        elif c <= 0x7F:
            out.append(c)
        elif c >= 0xC0:
            out += b' '
            out.append(c ^ 0x80)
        elif i < n:
            pair = (c << 8) | data[i]
            i += 1
            dist, length = (pair >> 3) & 0x7FF, (pair & 7) + 3
            if dist and dist <= len(out):
                for _ in range(length):
                    out.append(out[-dist])
    return bytes(out)


def _trailing(data: bytes, flags: int) -> bytes:
    """去掉文字记录末尾的附加数据（MOBI 头里的 extra data flags 说明有哪些）。"""
    for bit in range(1, 16):
        if flags & (1 << bit) and data:
            num = 0
            for b in data[-4:]:
                if b & 0x80:
                    num = 0
                num = (num << 7) | (b & 0x7F)
            data = data[:-num] if 0 < num <= len(data) else data
    if flags & 1 and data:
        data = data[:-((data[-1] & 3) + 1)]
    return data


def read_mobi(path, img_dir: Path):
    raw = Path(path).read_bytes()
    if len(raw) < 86 or raw[60:68] not in (b'BOOKMOBI', b'TEXtREAd'):
        raise EbookError('读不出这本 Kindle 电子书（不是 MOBI / AZW3 格式，或者文件坏了）')
    count = struct.unpack('>H', raw[76:78])[0]
    offs = [struct.unpack('>I', raw[78 + 8 * k:82 + 8 * k])[0] for k in range(count)] + [len(raw)]

    def rec(k):
        return raw[offs[k]:offs[k + 1]]
    r0 = rec(0)
    compression, _, _text_len, n_text, _rec_size, encryption = struct.unpack('>HHIHHH', r0[:14])
    if encryption:
        raise EbookError('这本 Kindle 电子书有 DRM 加密，读不出来（从亚马逊买的书都这样），请换一个没有加密的版本')
    if compression == 17480:
        raise EbookError('这本 Kindle 电子书用的是 HUFF/CDIC 压缩，软件还读不了，可以先用 Calibre 转成 EPUB 再导入')
    meta = {'title': '', 'author': ''}
    enc, flags, first_img = 'cp1252', 0, None
    if r0[16:20] == b'MOBI':
        hlen = struct.unpack('>I', r0[20:24])[0]
        enc = 'utf-8' if struct.unpack('>I', r0[28:32])[0] == 65001 else 'cp1252'
        name_off, name_len = struct.unpack('>II', r0[84:92])
        meta['title'] = r0[name_off:name_off + name_len].decode(enc, 'replace').strip()
        first_img = struct.unpack('>I', r0[108:112])[0]
        if hlen >= 0xE4:
            flags = struct.unpack('>H', r0[0xF2:0xF4])[0]
        exth_flag = struct.unpack('>I', r0[128:132])[0]
        if exth_flag & 0x40 and r0[16 + hlen:20 + hlen] == b'EXTH':
            p = 16 + hlen + 12
            for _ in range(struct.unpack('>I', r0[16 + hlen + 8:16 + hlen + 12])[0]):
                t, ln = struct.unpack('>II', r0[p:p + 8])
                val = r0[p + 8:p + ln].decode(enc, 'replace').strip()
                if t == 100 and not meta['author']:
                    meta['author'] = val
                elif t == 503 and val:
                    meta['title'] = val
                p += ln
    text = bytearray()
    for k in range(1, min(n_text, count - 1) + 1):
        data = _trailing(rec(k), flags)
        text += palmdoc_decompress(data) if compression == 2 else data
    html = bytes(text).decode(enc, 'replace')
    k = [0]

    def on_img(src):
        m = re.search(r'(?:recindex:|kindle:embed:)0*([0-9A-Va-v]+)', src)
        if not m or first_img in (None, 0xFFFFFFFF):
            return None
        idx = int(m.group(1), 10 if src.startswith('recindex') else 32)
        r = first_img + idx - 1
        if not 0 <= r < count:
            return None
        k[0] += 1
        return save_image(rec(r), img_dir, f'mobi_{k[0]:04d}')
    # 老格式的分页标记、Kindle 的 aid 属性不影响正文；<mbp:pagebreak/> 当分段
    html = re.sub(r'<mbp:pagebreak\s*/?>', '<p></p>', html, flags=re.I)
    blocks = html_blocks(html, on_img)
    if not any(b[1] for b in blocks):  # 没有 h 标签：用「第X章」认章节
        from .book import CHAPTER_RE
        blocks = [(t, True, 1, p) if (CHAPTER_RE.match(t) and len(t) < 40) else (t, h, lv, p) for t, h, lv, p in blocks]
    return blocks, meta


# ----------------------------------------------------------- DOC（97-2003） ----
def read_doc(path):
    import olefile
    if not olefile.isOleFile(path):
        raise EbookError('这个 .doc 文件读不出来（可能其实是别的格式，或者文件坏了）。可以用 WPS / Word 另存为 .docx 再导入')
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists('WordDocument'):
            raise EbookError('这个 .doc 文件里没有 Word 正文')
        wd = ole.openstream('WordDocument').read()
        ident, = struct.unpack('<H', wd[0:2])
        if ident != 0xA5EC:
            raise EbookError('这是很老的 Word（6.0 / 95）文档，读不出来，可以用 WPS / Word 另存为 .docx 再导入')
        fl, = struct.unpack('<H', wd[0x0A:0x0C])
        if fl & 0x0100:
            raise EbookError('这个 Word 文档设了打开密码，读不出来')
        table = '1Table' if fl & 0x0200 else '0Table'
        if not ole.exists(table):
            raise EbookError('这个 .doc 文件不完整（缺少表格流）')
        tbl = ole.openstream(table).read()
        ccp_text, = struct.unpack('<I', wd[0x4C:0x50])
        fc_clx, lcb_clx = struct.unpack('<II', wd[0x1A2:0x1AA])
        clx = tbl[fc_clx:fc_clx + lcb_clx]
        i = 0
        while i < len(clx) and clx[i] == 1:  # Prc：格式信息，跳过
            i += 3 + struct.unpack('<H', clx[i + 1:i + 3])[0]
        if i >= len(clx) or clx[i] != 2:
            raise EbookError('读不出这个 Word 文档的正文结构，可以用 WPS / Word 另存为 .docx 再导入')
        lcb, = struct.unpack('<I', clx[i + 1:i + 5])
        plc = clx[i + 5:i + 5 + lcb]
        n = (lcb - 4) // 12
        cps = struct.unpack(f'<{n + 1}I', plc[:4 * (n + 1)])
        parts = []
        for k in range(n):
            if cps[k] >= ccp_text:
                break
            pcd = plc[4 * (n + 1) + 8 * k:4 * (n + 1) + 8 * k + 8]
            fc, = struct.unpack('<I', pcd[2:6])
            length = min(cps[k + 1], ccp_text) - cps[k]
            if fc & 0x40000000:
                start = (fc & ~0x40000000) // 2
                parts.append(wd[start:start + length].decode('cp1252', 'replace'))
            else:
                parts.append(wd[fc:fc + 2 * length].decode('utf-16-le', 'replace'))
        text = ''.join(parts)
        meta = _ole_meta(ole)
    finally:
        ole.close()
    # 域代码（\x13 代码 \x14 结果 \x15）只留结果；表格单元、换行、分页符号转成普通分隔
    text = re.sub(r'\x13[^\x13\x14\x15]*\x14', '', text)
    text = re.sub(r'\x13[^\x13\x14\x15]*\x15', '', text)
    text = text.translate({0x14: None, 0x15: None, 0x01: None, 0x08: None, 0x07: ' | ', 0x0B: '\n', 0x0C: '\r', 0x1E: '-', 0x1F: None})
    from .book import CHAPTER_RE
    blocks = []
    for para in text.split('\r'):
        para = para.strip().strip('|').strip()
        if not para:
            continue
        head = bool(CHAPTER_RE.match(para)) and len(para) < 40
        blocks.append((para, head, 1 if head else 0, []))
    return blocks, meta


def _ole_meta(ole):
    """老版 Office 文件属性里的标题、作者。按文件声明的代码页解码；读出来不像正常文字（WPS 有时写得不标准）就不用。"""
    meta = {'title': '', 'author': ''}
    try:
        m = ole.get_metadata()
        cp = getattr(m, 'codepage', None) or 936
        for key in ('title', 'author'):
            v = getattr(m, key, None)
            if isinstance(v, bytes):
                try:
                    v = v.decode('utf-16-le' if cp == 1200 else f'cp{cp}')
                except (UnicodeDecodeError, LookupError):
                    v = v.decode('gb18030', 'replace')
            v = str(v or '').split('\x00')[0].strip()
            if v and len(v) <= 100 and not re.search(r'[\x00-\x08\x0e-\x1f\ufffd]', v):
                meta[key] = v
        meta = clean_meta(meta)
    except Exception:  # noqa: BLE001  属性读不出不影响正文
        pass
    return meta


GENERIC = re.compile(r'^(administrator|admin|user|owner|微软用户|microsoft( office)?( user)?|windows 用户|unknown|未知|作者|'
                     r'空白演示|空白文档|演示文稿\d*|文档\d*|powerpoint 演示文稿|presentation\d*|document\d*|无标题.*|untitled.*)$', re.I)


def clean_meta(meta):
    """去掉软件模板自带的「Administrator」「空白演示」这类没意义的作者、标题（之后用文件名）。"""
    return {k: ('' if GENERIC.match((v or '').strip()) else (v or '').strip()) for k, v in meta.items()}


# ------------------------------------------------------------------ PPTX ----
P_NS = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
A_NS = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
R_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REL_NS = '{http://schemas.openxmlformats.org/package/2006/relationships}'


def _rels(z, path):
    rp = posixpath.join(posixpath.dirname(path), '_rels', posixpath.basename(path) + '.rels')
    out = {}
    if rp in z.namelist():
        for r in ET.fromstring(z.read(rp)):
            out[r.get('Id')] = posixpath.normpath(posixpath.join(posixpath.dirname(path), r.get('Target') or ''))
    return out


def read_pptx(path, img_dir: Path):
    """返回 ([(标题, 正文, [图片])] 每张幻灯片一项, meta)。"""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise EbookError('这个 PPTX 文件坏了，读不出来')
    with z:
        pres = 'ppt/presentation.xml'
        rels = _rels(z, pres)
        order = [rels.get(s.get(R_NS + 'id')) for s in ET.fromstring(z.read(pres)).iter(P_NS + 'sldId')]
        meta = {'title': '', 'author': ''}
        if 'docProps/core.xml' in z.namelist():
            for el in ET.fromstring(z.read('docProps/core.xml')):
                if _local(el.tag) == 'title':
                    meta['title'] = (el.text or '').strip()
                elif _local(el.tag) == 'creator':
                    meta['author'] = (el.text or '').strip()
        slides, k = [], 0
        for sp in order:
            if not sp or sp not in z.namelist():
                continue
            root = ET.fromstring(z.read(sp))
            srels = _rels(z, sp)
            title, body, pics = '', [], []
            for shape in root.iter(P_NS + 'sp'):
                ph = shape.find(f'{P_NS}nvSpPr/{P_NS}nvPr/{P_NS}ph')
                paras = [''.join(t.text or '' for t in p.iter(A_NS + 't')).strip() for p in shape.iter(A_NS + 'p')]
                paras = [x for x in paras if x]
                if ph is not None and ph.get('type') in ('title', 'ctrTitle') and not title:
                    title = ' '.join(paras)
                else:
                    body += paras
            for tbl in root.iter(A_NS + 'tbl'):
                for tr in tbl.iter(A_NS + 'tr'):
                    cells = [''.join(t.text or '' for t in tc.iter(A_NS + 't')).strip() for tc in tr.iter(A_NS + 'tc')]
                    body.append('| ' + ' | '.join(cells) + ' |')
            for blip in root.iter(A_NS + 'blip'):
                target = srels.get(blip.get(R_NS + 'embed'))
                if target and target in z.namelist():
                    k += 1
                    p = save_image(z.read(target), img_dir, f'pptx_{k:04d}{Path(target).suffix}')
                    if p:
                        pics.append(p)
            slides.append((title, '\n'.join(body), pics))
    return slides, clean_meta(meta)


# ------------------------------------------------------------ PPT（97-2003） ----
def read_ppt(path):
    """返回 ([(标题, 正文, [])] 每张幻灯片一项, meta)。只读文字（图片在老格式里不好取，跳过）。"""
    import olefile
    if not olefile.isOleFile(path):
        raise EbookError('这个 .ppt 文件读不出来（可能其实是别的格式，或者文件坏了）。可以用 WPS / PowerPoint 另存为 .pptx 再导入')
    ole = olefile.OleFileIO(path)
    try:
        if not ole.exists('PowerPoint Document'):
            raise EbookError('这个 .ppt 文件里没有幻灯片内容')
        data = ole.openstream('PowerPoint Document').read()
        meta = _ole_meta(ole)
    finally:
        ole.close()
    # 幻灯片文字有两处：大纲（SlideListWithText，标题和正文占位符）和每张幻灯片里的文本框（Slide 容器）
    outline, boxes = [], []
    TITLE_TYPES = {0, 6}  # TextHeaderAtom：0 标题、6 居中标题

    def walk(start, end, ctx):
        i = start
        while i + 8 <= end:
            ver_inst, typ, ln = struct.unpack('<HHI', data[i:i + 8])
            body_s, body_e = i + 8, min(end, i + 8 + ln)
            inst = ver_inst >> 4
            if (ver_inst & 0xF) == 0xF:  # 容器
                if typ == 0x0FF0 and inst == 0:  # SlideListWithText（幻灯片）
                    walk(body_s, body_e, {'where': 'outline'})
                elif typ == 0x03EE:  # Slide
                    boxes.append([])
                    walk(body_s, body_e, {'where': 'slide'})
                elif typ in (0x03F0, 0x03F8, 0x0FF0, 0x0F0D):  # 备注、母版、备注 / 母版的大纲：不要
                    pass
                else:
                    walk(body_s, body_e, ctx)
            elif ctx.get('where') == 'outline':
                if typ == 0x03F3:  # SlidePersistAtom：下一张幻灯片开始
                    outline.append({'title': '', 'body': []})
                elif typ == 0x0F9F and outline:  # TextHeaderAtom
                    ctx['ttype'], = struct.unpack('<I', data[body_s:body_s + 4])
                elif typ in (0x0FA0, 0x0FA8) and outline:
                    t = _ppt_text(data[body_s:body_e], typ)
                    if t:
                        if ctx.get('ttype') in TITLE_TYPES and not outline[-1]['title']:
                            outline[-1]['title'] = t
                        else:
                            outline[-1]['body'].append(t)
            elif ctx.get('where') == 'slide' and typ in (0x0FA0, 0x0FA8, 0x0FBA) and boxes:
                t = _ppt_text(data[body_s:body_e], 0x0FA0 if typ == 0x0FBA else typ)
                if t:
                    boxes[-1].append(t)
            i = body_e
    walk(0, len(data), {})
    slides = []
    for k in range(max(len(outline), len(boxes))):
        o = outline[k] if k < len(outline) else {'title': '', 'body': []}
        seen = set([o['title']] + o['body'])
        extra = [t for t in (boxes[k] if k < len(boxes) else []) if t not in seen]
        texts = o['body'] + extra
        title = o['title']
        if not title and texts:  # 没认出标题占位符：第一行就是标题
            title, texts = texts[0].split('\n')[0], ['\n'.join(texts[0].split('\n')[1:])] + texts[1:]
        texts = [t for t in texts if t.strip()]
        if title or texts:
            slides.append((title, '\n'.join(texts), []))
    return slides, meta


def _ppt_text(raw: bytes, typ):
    t = raw.decode('utf-16-le', 'replace') if typ == 0x0FA0 else raw.decode('cp1252', 'replace')
    t = t.replace('\r', '\n').replace('\x0b', '\n').strip()
    return '' if t in ('*', '') else t
