"""生成测试用的书（仓库里不放大文件）：test-media/测试书籍.pdf
- 2 页前言（没有页码）+ 6 页正文，正文页脚印着书上的页码 1–6（PDF 第 3–8 页）；
- 两章：「第一章 趋势的起点」（P1–P3）、「第二章 仓位的分配」（P4–P6）；
- 第 2 页正文有一张示意图（几个方块和文字「图1-1 三段结构」）。
用 Windows 自带的黑体（simhei.ttf）写中文。"""
import ctypes
import sys
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as c

OUT = Path(__file__).resolve().parents[1] / 'test-media' / '测试书籍.pdf'
FONT = Path(r'C:\Windows\Fonts\simhei.ttf')

PAGES = [
    (None, ['前言', '', '这是一本用来测试猛人字幕书籍整理功能的小书。', '它只有两章，每章三页。']),
    (None, ['目录', '', '第一章 趋势的起点 ………… 1', '第二章 仓位的分配 ………… 4']),
    (1, ['第一章 趋势的起点', '', '作者认为，趋势由三段组成：推进、回调、再推进。', '推进是价格沿主要方向运行的阶段。',
         '回调是价格逆着主要方向的修正，回调的极端位置称为级。']),
    (2, ['第一节 三段结构', '', '图1-1 三段结构', '', '', '', '', '', '', '如图1-1所示，三段结构由左到右依次排列。',
         '作者强调：只有出现再推进，趋势才算延续。']),
    (3, ['第二节 本章小结', '', '本章给出三个定义：推进、回调、级。', '作者没有给出回调幅度的具体数字。']),
    (4, ['第二章 仓位的分配', '', '作者把仓位分为金字塔、矩形和漏斗三种模型。', '金字塔模型：第一次投入 40%，之后依次为 30%、20%、10%。']),
    (5, ['第一节 矩形与漏斗', '', '矩形模型每次投入相同比例，例如每次 20%。', '漏斗模型先少后多：10%、20%、30%。']),
    (6, ['第二节 本章小结', '', '作者认为没有绝对最好的仓位模型，', '仓位取决于每个人的财务状况和认知。']),
]


def wide(s):
    data = (s + '\0').encode('utf-16-le')
    buf = (ctypes.c_ushort * (len(data) // 2)).from_buffer_copy(data)
    return ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), buf


def main():
    if not FONT.exists():
        sys.exit('找不到黑体字体 ' + str(FONT))
    OUT.parent.mkdir(exist_ok=True)
    pdf = pdfium.PdfDocument.new()
    font_data = FONT.read_bytes()
    fbuf = (ctypes.c_uint8 * len(font_data)).from_buffer_copy(font_data)
    font = c.FPDFText_LoadFont(pdf.raw, fbuf, len(font_data), c.FPDF_FONT_TRUETYPE, 1)
    keep = []
    for label, lines in PAGES:
        page = pdf.new_page(420, 595)
        y = 540
        for k, line in enumerate(lines):
            if line:
                size = 18 if k == 0 else 12
                obj = c.FPDFPageObj_CreateTextObj(pdf.raw, font, size)
                ptr, buf = wide(line)
                keep.append(buf)
                c.FPDFText_SetText(obj, ptr)
                c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 40, y)
                c.FPDFPage_InsertObject(page.raw, obj)
            y -= 28
        if label == 2:  # 示意图：三个方块
            for i, h in enumerate((60, 30, 80)):
                rect = c.FPDFPageObj_CreateNewRect(60 + i * 110, 300, 80, h)
                c.FPDFPageObj_SetFillColor(rect, 60, 120, 200, 255)
                c.FPDFPath_SetDrawMode(rect, c.FPDF_FILLMODE_ALTERNATE, 0)
                c.FPDFPage_InsertObject(page.raw, rect)
        if label:
            obj = c.FPDFPageObj_CreateTextObj(pdf.raw, font, 10)
            ptr, buf = wide(str(label))
            keep.append(buf)
            c.FPDFText_SetText(obj, ptr)
            c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 205, 30)
            c.FPDFPage_InsertObject(page.raw, obj)
        c.FPDFPage_GenerateContent(page.raw)
    pdf.save(str(OUT))
    print('生成', OUT)


if __name__ == '__main__':
    main()
