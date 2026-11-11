"""截图认字：PP-OCRv6 small（RapidOCR 3.10，Apache-2.0；2026-11-11 用户让实测后定的）。

实测（同一天，12 张真实课程画面：吴恩达英文课件、峰级 K 线图、人间落叶的课件）：
- 英文课件：Windows 自带认字（中文识别器）认成「frorn overusec*」「This is 0 nuanced」、整行乱码；PP-OCRv6 几乎全对；
- 中文课件：Windows「常看常新」→「堂看堂新」、「敞口」→「敞囗」；PP-OCRv6 都对；
- K 线图：Windows 基本认不出，PP-OCRv6 能读出数字；
- 速度：PP-OCRv6 一张 1～1.8 秒，Windows 约 0.6 秒。

**在单独的进程里认字**（`猛人学习.exe --ocr-worker 图片 结果.json`，源码运行时是 `python app/main.py --ocr-worker …`）：
RapidOCR 用 pip 装的 onnxruntime，本机识别（sherpa-onnx）带着另一份 onnxruntime.dll，放进同一个进程里谁先加载谁赢，
版本对不上会出错甚至崩溃。认完进程就退出，不占内存。出错时 shotedit 退回 Windows 自带认字。

模型（检测 10 MB + 识别 21 MB + 方向 0.6 MB）在 RapidOCR 自己的 models 文件夹：开发环境第一次用时自动下载，
build.py 打包前先下好（ensure_models），跟着软件一起发，用户那边不用下载。临时图片放在缓存 ocr/ 下，用完就删。"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _engine():
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangDet, LangRec, ModelType, OCRVersion
    return RapidOCR(params={'Det.ocr_version': OCRVersion.PPOCRV6, 'Det.model_type': ModelType.SMALL, 'Det.lang_type': LangDet.CH,
                            'Rec.ocr_version': OCRVersion.PPOCRV6, 'Rec.model_type': ModelType.SMALL, 'Rec.lang_type': LangRec.CH,
                            'Global.log_level': 'error'})


def ensure_models():
    """打包前调用：模型不在就下载（RapidOCR 建引擎时自己下），返回模型文件夹。"""
    _engine()
    import rapidocr
    return Path(rapidocr.__file__).parent / 'models'


def worker(src, dst):
    """认字小进程：图片 → {lines: [{text, box: [左, 上, 右, 下], score}]} 或 {error}。"""
    for name in ('stdout', 'stderr'):  # 打包后的窗口程序没有控制台：认字库要写日志时别出错
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    try:
        t = time.time()
        out = _engine()(str(src))
        lines = []
        for txt, box, score in zip(out.txts or (), out.boxes if out.boxes is not None else (), out.scores or ()):
            txt = str(txt).strip()
            if not txt:
                continue
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
            lines.append({'text': txt, 'box': [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))], 'score': round(float(score), 3)})
        res = {'lines': lines, 'sec': round(time.time() - t, 2)}
    except Exception as e:  # noqa: BLE001  出错写进结果，交给调用方退回 Windows 认字
        res = {'error': f'{type(e).__name__}: {e}'[:500]}
    Path(dst).write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
    return 0 if 'lines' in res else 1


def _cmd(src, dst):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--ocr-worker', str(src), str(dst)]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / 'main.py'), '--ocr-worker', str(src), str(dst)]


def recognize(img_path: Path, timeout=120):
    """开一个认字小进程认这张图 → [{text, box}]。失败抛 RuntimeError（调用方退回 Windows 认字）。"""
    from .media import NO_WINDOW
    dst = img_path.with_suffix('.json')
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    r = subprocess.run(_cmd(img_path, dst), capture_output=True, timeout=timeout, creationflags=NO_WINDOW, env=env)
    if not dst.is_file():
        raise RuntimeError('认字进程没有结果：' + (r.stderr or b'').decode('utf-8', 'replace').strip()[-300:])
    res = json.loads(dst.read_text(encoding='utf-8'))
    dst.unlink(missing_ok=True)
    if 'error' in res:
        raise RuntimeError(res['error'])
    return res['lines']
