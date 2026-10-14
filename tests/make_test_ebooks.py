"""生成测试用的电子书和演示文稿（仓库里不放大文件）：test-media 下的
  测试电子书.epub、测试电子书.mobi、测试演示.pptx：用脚本直接写；
  测试文档.doc、测试演示.ppt：用本机的 WPS（或 Office）另存出来，没有装就跳过（测试里对应的检查也跳过）。
内容都是两章：「第一章 趋势的起点」「第二章 仓位的分配」。"""
import io
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / 'test-media'
CH1 = ['作者认为，趋势由三段组成：推进、回调、再推进。', '回调的极端位置称为级。']
CH2 = ['作者把仓位分为金字塔、矩形和漏斗三种模型。', '金字塔模型：第一次投入 40%，之后依次为 30%、20%、10%。']


def png():
    img = Image.new('RGB', (240, 120), 'white')
    ImageDraw.Draw(img).rectangle([20, 20, 220, 100], outline='black', width=4)
    b = io.BytesIO()
    img.save(b, 'PNG')
    return b.getvalue()


def epub(path):
    def page(title, paras, img=False):
        body = ''.join(f'<p>{x}</p>' for x in paras) + ('<p><img src="../images/fig1.png" alt="图1-1"/></p>' if img else '')
        return (f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
                f'<body><h1>{title}</h1>{body}</body></html>')
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf', '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试电子书</dc:title><dc:creator>测试作者</dc:creator></metadata>'
                   '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
                   '<item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="i1" href="images/fig1.png" media-type="image/png"/></manifest>'
                   '<spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>')
        z.writestr('OEBPS/nav.xhtml', '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                   '<body><nav epub:type="toc"><ol><li><a href="text/ch1.xhtml">第一章 趋势的起点</a></li>'
                   '<li><a href="text/ch2.xhtml">第二章 仓位的分配</a></li></ol></nav></body></html>')
        z.writestr('OEBPS/text/ch1.xhtml', page('第一章 趋势的起点', CH1, img=True))
        z.writestr('OEBPS/text/ch2.xhtml', page('第二章 仓位的分配', CH2))
        z.writestr('OEBPS/images/fig1.png', png())


def palmdoc_compress(data: bytes) -> bytes:
    """测试用的简单压缩：只用「原样字节」和「空格 + 字符」两种编码（解压程序要都认得）。"""
    out, i = bytearray(), 0
    while i < len(data):
        c = data[i]
        if c == 0x20 and i + 1 < len(data) and 0x40 <= data[i + 1] <= 0x7F:
            out.append(data[i + 1] ^ 0x80)
            i += 2
        elif c == 0 or 0x09 <= c <= 0x7F:
            out.append(c)
            i += 1
        else:
            run = data[i:i + 8]
            n = next((k for k, b in enumerate(run) if b == 0 or 0x09 <= b <= 0x7F), len(run)) or 1
            out.append(n)
            out += data[i:i + n]
            i += n
    return bytes(out)


def mobi(path):
    html = ('<html><head><guide></guide></head><body>'
            '<h2>第一章 趋势的起点</h2>' + ''.join(f'<p>{x}</p>' for x in CH1) + '<p><img recindex="00001"/></p><mbp:pagebreak/>'
            '<h2>第二章 仓位的分配</h2>' + ''.join(f'<p>{x}</p>' for x in CH2) + '</body></html>').encode('utf-8')
    chunks = [html[k:k + 4096] for k in range(0, len(html), 4096)]
    texts = [palmdoc_compress(c) for c in chunks]
    title = '测试电子书'.encode('utf-8')
    author = '测试作者'.encode('utf-8')
    exth_rec = struct.pack('>II', 100, 8 + len(author)) + author
    exth = b'EXTH' + struct.pack('>II', 12 + len(exth_rec), 1) + exth_rec
    hlen = 0xE8
    mobi_h = bytearray(hlen)
    mobi_h[0:4] = b'MOBI'
    struct.pack_into('>III', mobi_h, 4, hlen, 2, 65001)
    first_img = 1 + len(texts)
    name_off = 16 + hlen + len(exth)
    struct.pack_into('>II', mobi_h, 84 - 16, name_off, len(title))
    struct.pack_into('>I', mobi_h, 108 - 16, first_img)
    struct.pack_into('>I', mobi_h, 128 - 16, 0x40)
    struct.pack_into('>H', mobi_h, 0xF2 - 16, 0)
    r0 = struct.pack('>HHIHHHH', 2, 0, len(html), len(texts), 4096, 0, 0) + bytes(mobi_h) + exth + title + b'\0\0'
    records = [r0] + texts + [png()]
    n = len(records)
    head = bytearray(78)
    head[0:12] = b'test-mobi'.ljust(12, b'\0')
    head[60:68] = b'BOOKMOBI'
    struct.pack_into('>H', head, 76, n)
    offset = 78 + 8 * n + 2
    table = b''
    for k, r in enumerate(records):
        table += struct.pack('>II', offset, 2 * k)
        offset += len(r)
    Path(path).write_bytes(bytes(head) + table + b'\0\0' + b''.join(records))


def pptx(path):
    ns = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
          'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')

    def sp(text, ph=None):
        p = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else '<p:nvPr/>'
        paras = ''.join(f'<a:p><a:r><a:t>{t}</a:t></a:r></a:p>' for t in text)
        return f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{p}</p:nvSpPr><p:txBody>{paras}</p:txBody></p:sp>'

    def slide(title, body, pic=False):
        extra = ('<p:pic><p:blipFill><a:blip r:embed="rIdImg"/></p:blipFill></p:pic>' if pic else '')
        return f'<?xml version="1.0" encoding="UTF-8"?><p:sld {ns}><p:cSld><p:spTree>{sp([title], "title")}{sp(body)}{extra}</p:spTree></p:cSld></p:sld>'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr('ppt/presentation.xml', f'<?xml version="1.0"?><p:presentation {ns}><p:sldIdLst><p:sldId id="256" r:id="rId1"/>'
                   '<p:sldId id="257" r:id="rId2"/><p:sldId id="258" r:id="rId3"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/>'
                   '<Relationship Id="rId3" Target="slides/slide3.xml"/></Relationships>')
        z.writestr('ppt/slides/slide1.xml', slide('第一章 趋势的起点', CH1[:1], pic=True))
        z.writestr('ppt/slides/_rels/slide1.xml.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rIdImg" Target="../media/image1.png"/></Relationships>')
        z.writestr('ppt/media/image1.png', png())
        z.writestr('ppt/slides/slide2.xml', slide('三段结构', CH1[1:]))
        z.writestr('ppt/slides/slide3.xml', slide('第二章 仓位的分配', CH2))
        z.writestr('docProps/core.xml', '<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:title>测试演示</dc:title><dc:creator>测试作者</dc:creator></cp:coreProperties>')


# 用 WPS（或 Office）存出老格式：Word 97-2003（.doc，格式号 0）、PowerPoint 97-2003（.ppt，格式号 1）
PS_DOC = r'''
$ErrorActionPreference = 'Stop'
$app = $null
foreach ($id in 'KWPS.Application','Word.Application') { try { $app = New-Object -ComObject $id; break } catch {} }
if (-not $app) { exit 3 }
$app.Visible = $false
$doc = $app.Documents.Add()
$sel = $app.Selection
foreach ($blk in @(@('第一章 趋势的起点', @('作者认为，趋势由三段组成：推进、回调、再推进。', '回调的极端位置称为级。')),
                   @('第二章 仓位的分配', @('作者把仓位分为金字塔、矩形和漏斗三种模型。', '金字塔模型：第一次投入 40%，之后依次为 30%、20%、10%。')))) {
  $sel.TypeText($blk[0]); $sel.TypeParagraph()
  foreach ($t in $blk[1]) { $sel.TypeText($t); $sel.TypeParagraph() }
}
$doc.SaveAs2($args[0], 0)
$doc.Close(0); $app.Quit()
'''
PS_PPT = r'''
$ErrorActionPreference = 'Stop'
$app = $null
foreach ($id in 'KWPP.Application','PowerPoint.Application') { try { $app = New-Object -ComObject $id; break } catch {} }
if (-not $app) { exit 3 }
$pres = $app.Presentations.Add(0)
$k = 1
foreach ($blk in @(@('第一章 趋势的起点', '作者认为，趋势由三段组成：推进、回调、再推进。'), @('第二章 仓位的分配', '作者把仓位分为金字塔、矩形和漏斗三种模型。'))) {
  $s = $pres.Slides.Add($k, 2)
  $s.Shapes.Item(1).TextFrame.TextRange.Text = $blk[0]
  $s.Shapes.Item(2).TextFrame.TextRange.Text = $blk[1]
  $k++
}
$pres.SaveAs($args[0], 1)
$pres.Close(); $app.Quit()
'''


def office(script, dest):
    ps = OUT / f'_make_{dest.suffix[1:]}.ps1'
    ps.write_text(script, encoding='utf-8-sig')
    try:
        r = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ps), str(dest)],
                           capture_output=True, timeout=180)
        if r.returncode or not dest.exists():
            print(f'跳过 {dest.name}：没有能用的 WPS / Office（{r.returncode}）{r.stderr.decode("gbk", "replace")[-200:]}')
    except subprocess.TimeoutExpired:
        print(f'跳过 {dest.name}：WPS / Office 没有响应')
    finally:
        ps.unlink(missing_ok=True)


def main():
    OUT.mkdir(exist_ok=True)
    epub(OUT / '测试电子书.epub')
    mobi(OUT / '测试电子书.mobi')
    pptx(OUT / '测试演示.pptx')
    if sys.platform == 'win32':
        if not (OUT / '测试文档.doc').exists():
            office(PS_DOC, OUT / '测试文档.doc')
        if not (OUT / '测试演示.ppt').exists():
            office(PS_PPT, OUT / '测试演示.ppt')
    print('ok')


if __name__ == '__main__':
    main()
