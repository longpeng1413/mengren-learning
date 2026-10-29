"""扫描书识别文字（OCR，2026-10-29 用户要求做进软件）：用 Windows 自带的中文识别（Windows.Media.Ocr，本机、免费、不联网），
把没有文字层（或者 WPS 识别得很差）的扫描 PDF 做成「可搜索 PDF」：页面外观不变，下面加一层看不见的文字，
软件的分章、提炼、朗读原文、AI 对话都用这层文字。

为什么不用 WPS：实测《K线动能理论》WPS 认得很差（「希望」→「秀望」、行序乱、9 页没字），超过 200 MB 还不能直接转；
Windows 自带的认得准、顺序对，300 页约 2 分钟。

几个坑（都踩过）：
- 每页要按实际显示「整页渲染」再识别：有的扫描书每页是几层图叠的，只取第一张图几乎是空白；
- 扫描件的页面坐标可能上下颠倒（原内容改了坐标系没还原），新加的文字会跟着颠倒，pdfium 读出来行序是反的：
  先按一种方向写，存盘再读回来检查每页的行序，反了的页换另一种方向重写；
- Windows 识别中文时字和字之间带空格，要去掉；
- 系统自带的 PowerShell 5.1 按系统编码读脚本：识别脚本里只能写英文。

临时文件：每批页面图片放在缓存的 ocr/ 下，识别完这一批就删；异常退出留下的由 cache.clean 清掉。"""
import ctypes
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from .config import CACHE_DIR
from .media import NO_WINDOW

DPI = 200
BATCH = 20
FONTS = [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\Deng.ttf', r'C:\Windows\Fonts\simkai.ttf', r'C:\Windows\Fonts\simfang.ttf']
CJK = r'[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]'
OCR_DIR = CACHE_DIR / 'ocr'
LANG = 'zh-Hans-CN'


class OcrError(Exception):
    pass


PS_SCRIPT = r'''param([string]$inDir, [string]$outDir, [string]$lang)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait() | Out-Null; $task.Result }
[void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
[void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
[void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
[void][Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
if (-not $inDir) { [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }; exit 0 }
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($lang))
if (-not $eng) { Write-Error "no recognizer for $lang"; exit 3 }
foreach ($f in Get-ChildItem $inDir -Filter *.png | Sort-Object Name) {
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($f.FullName)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  $lines = @()
  foreach ($ln in $res.Lines) {
    $words = @($ln.Words | ForEach-Object { @{ x = $_.BoundingRect.X; y = $_.BoundingRect.Y; w = $_.BoundingRect.Width; h = $_.BoundingRect.Height } })
    $lines += , @{ text = $ln.Text; words = $words }
  }
  $json = @{ w = $bmp.PixelWidth; h = $bmp.PixelHeight; lines = $lines } | ConvertTo-Json -Depth 6 -Compress
  [IO.File]::WriteAllText((Join-Path $outDir ($f.BaseName + '.json')), $json, (New-Object Text.UTF8Encoding($false)))
  $stream.Dispose()
}
'''


def _script():
    OCR_DIR.mkdir(parents=True, exist_ok=True)
    p = OCR_DIR / 'winocr.ps1'
    if not p.is_file() or p.read_text(encoding='ascii', errors='replace') != PS_SCRIPT:
        p.write_text(PS_SCRIPT, encoding='ascii')
    return p


def _ps(*args, timeout=600):
    if os.name != 'nt':
        raise OcrError('只有 Windows 能用自带的文字识别')
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(_script()), *args],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=NO_WINDOW)
    if r.returncode:
        raise OcrError('Windows 文字识别出错：' + (r.stderr or r.stdout).strip()[-300:])
    return r.stdout


_avail = None


def available():
    """这台电脑能不能认中文：返回 (能不能, 不能时的说明)。"""
    global _avail
    if _avail is None:
        try:
            langs = _ps('', '', '', timeout=60).split()
            _avail = (any(x.lower().startswith('zh-hans') for x in langs), '')
        except (OcrError, OSError, subprocess.TimeoutExpired) as e:
            _avail = (False, str(e))
    ok, why = _avail
    return ok, ('' if ok else (why or '这台电脑没有装中文文字识别：在 Windows「设置 → 时间和语言 → 语言和区域 → 中文（简体）→ 语言选项」里装上「光学字符识别」，装好后重启猛人学习'))


def recognize(images, work):
    """一批 PNG → {页码: {w, h, lines: [{text, words: [{x, y, w, h}]}]}}（测试时替换成假的）。"""
    out = work / 'out'
    out.mkdir(exist_ok=True)
    _ps(str(images), str(out), LANG)
    res = {}
    for f in out.glob('p*.json'):
        res[int(f.stem[1:])] = json.loads(f.read_text(encoding='utf-8-sig'))
        f.unlink()
    return res


def clean(t):
    t = re.sub(rf'(?<={CJK}) +(?={CJK})', '', t or '')  # 中文字和字之间的空格
    t = re.sub(rf'(?<={CJK}) +(?=[A-Za-z0-9])|(?<=[A-Za-z0-9]) +(?={CJK})', '', t)
    return t.strip()


def _font(pdf, c):
    for p in FONTS:
        if os.path.isfile(p):
            data = Path(p).read_bytes()
            buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
            f = c.FPDFText_LoadFont(pdf.raw, buf, len(data), c.FPDF_FONT_TRUETYPE, True)
            if f:
                return f, buf
    raise OcrError('找不到能写中文的字体（黑体 simhei.ttf 或等线 Deng.ttf）')


def _write(pdf, c, font, keep, n, d, flip):
    """第 n 页：去掉原来的文字（扫描书的旧 OCR 文字），按识别结果每行加一个看不见的文字对象。"""
    pg = pdf[n - 1]
    for k in range(c.FPDFPage_CountObjects(pg.raw) - 1, -1, -1):
        obj = c.FPDFPage_GetObject(pg.raw, k)
        if c.FPDFPageObj_GetType(obj) == c.FPDF_PAGEOBJ_TEXT:
            c.FPDFPage_RemoveObject(pg.raw, obj)
            c.FPDFPageObj_Destroy(obj)
    w_pt, h_pt = pg.get_size()
    k = w_pt / max(1, d['w'])
    for ln in d.get('lines') or []:
        words, text = ln.get('words') or [], clean(ln.get('text'))
        if not words or not text:
            continue
        x0, x1 = min(w['x'] for w in words), max(w['x'] + w['w'] for w in words)
        y0, y1 = min(w['y'] for w in words), max(w['y'] + w['h'] for w in words)
        size = max(4.0, (y1 - y0) * k)
        natural = size * sum(1.0 if re.match(CJK, ch) else 0.5 for ch in text)
        hs = (x1 - x0) * k / natural if natural else 1.0
        obj = c.FPDFPageObj_CreateTextObj(pdf.raw, font, ctypes.c_float(size))
        b = text.encode('utf-16-le') + b'\x00\x00'
        buf = ctypes.create_string_buffer(b, len(b))
        keep.append(buf)
        c.FPDFText_SetText(obj, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)))
        c.FPDFTextObj_SetTextRenderMode(obj, c.FPDF_TEXTRENDERMODE_INVISIBLE)
        c.FPDFPageObj_Transform(obj, hs, 0, 0, 1, x0 * k, (y0 * k) if flip else (h_pt - y1 * k + size * 0.12))
        c.FPDFPage_InsertObject(pg.raw, obj)
    c.FPDFPage_GenerateContent(pg.raw)
    pg.close()


def _build(src, dst, res, flips):
    import pypdfium2 as pdfium
    import pypdfium2.raw as c
    pdf = pdfium.PdfDocument(str(src))
    try:
        font, fbuf = _font(pdf, c)
        keep = [fbuf]
        for n in sorted(res):
            _write(pdf, c, font, keep, n, res[n], flips.get(n, True))
        pdf.save(str(dst))
    finally:
        pdf.close()


def _order_ok(path, res):
    """存盘后读回来：每页第一行在最后一行前面才算对。返回顺序反了的页。"""
    import pypdfium2 as pdfium
    bad = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for n, d in res.items():
            lines = [re.sub(r'\s', '', clean(ln.get('text'))) for ln in d.get('lines') or []]
            lines = [x for x in lines if len(x) >= 2]
            if len(lines) < 2:
                continue
            pg = pdf[n - 1]
            tp = pg.get_textpage()
            t = re.sub(r'\s', '', tp.get_text_range())
            tp.close()
            pg.close()
            a, b = t.find(lines[0][:8]), t.rfind(lines[-1][:8])
            if a >= 0 and b >= 0 and a > b:
                bad.append(n)
    finally:
        pdf.close()
    return bad


def make_searchable(src, dst, progress=lambda f, m='': None, cancel=None):
    """src（扫描 PDF）→ dst（可搜索 PDF）。返回 {pages, chars, empty: [几乎没字的页]}。"""
    import pypdfium2 as pdfium
    from . import book
    ok, why = available()
    if not ok:
        raise OcrError(why)
    work = OCR_DIR / uuid.uuid4().hex[:12]
    work.mkdir(parents=True, exist_ok=True)
    res = {}
    try:
        with book._pdf_lock:
            total = len(pdfium.PdfDocument(str(src)))
        t0 = time.time()
        for a in range(1, total + 1, BATCH):
            if cancel is not None and cancel.is_set():
                raise OcrError('已取消')
            b = min(total, a + BATCH - 1)
            progress((a - 1) / total * 0.9, f'正在识别文字：第 {a}-{b} 页 / 共 {total} 页')
            imgs = work / 'img'
            shutil.rmtree(imgs, ignore_errors=True)
            imgs.mkdir()
            with book._pdf_lock:
                pdf = pdfium.PdfDocument(str(src))
                try:
                    for n in range(a, b + 1):
                        pg = pdf[n - 1]
                        pg.render(scale=DPI / 72).to_pil().convert('RGB').save(imgs / f'p{n:04d}.png')
                        pg.close()
                finally:
                    pdf.close()
            res.update(recognize(imgs, work))
            shutil.rmtree(imgs, ignore_errors=True)  # 这一批的页面图用完就删
        progress(0.92, '正在写文字层…')
        with book._pdf_lock:
            _build(src, dst, res, {})
            bad = _order_ok(dst, res)
            if bad:  # 这些页的坐标是另一种方向：换个方向重写
                _build(src, dst, res, {n: n not in bad for n in res})
        chars = {n: sum(len(clean(ln.get('text'))) for ln in d.get('lines') or []) for n, d in res.items()}
        progress(1, f'识别完了，用时 {time.time() - t0:.0f} 秒')
        return {'pages': total, 'chars': sum(chars.values()), 'empty': [n for n in sorted(chars) if chars[n] < 20]}
    finally:
        shutil.rmtree(work, ignore_errors=True)
