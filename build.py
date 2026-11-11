"""打包成 exe：生成 dist-next\\猛人学习\\猛人学习.exe，再双击「更新到新版.bat」换进 dist。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 默认打包到 dist-next，再用「更新到新版.bat」换进 dist（会保留缓存和日志）
DIST = next((a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--dist=')), 'dist-next')
# 截图认字（2026-11-11，PP-OCRv6，core/ppocr.py）：模型先下好（在 RapidOCR 自己的 models 文件夹），跟着软件一起打包，用户不用下载
subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, "app"); from core import ppocr; print("认字模型：", ppocr.ensure_models())'],
               cwd=ROOT, check=True)
args = [
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
    '--name', '猛人学习',
    '--icon', str(ROOT / 'app' / 'assets' / 'icon.ico'),
    '--paths', str(ROOT / 'app'),
    '--add-data', f'{ROOT / "app" / "web"};web',
    '--hidden-import', 'websocket',
    '--hidden-import', 'pystray._win32',
    '--collect-all', 'edge_tts',  # 朗读（Edge 在线声音），LGPLv3，许可证随包附上
    '--collect-all', 'pypdfium2', '--collect-all', 'pypdfium2_raw', '--collect-all', 'pypdfium2_cfg',
    # 本机识别（2026-11-10）：sherpa-onnx 连同它自带的 onnxruntime.dll 等运行库一起打包；它要用 numpy，所以不再排除 numpy
    '--collect-all', 'sherpa_onnx',
    # 截图认字（2026-11-11）：RapidOCR（连同 PP-OCRv6 模型和配置文件）+ onnxruntime + OpenCV。
    # 它的 onnxruntime 和上面 sherpa-onnx 的不是同一份，只在单独的认字小进程里用（main.py --ocr-worker），不会撞
    '--collect-all', 'rapidocr', '--collect-all', 'onnxruntime',
    '--exclude-module', 'tkinter',
    '--distpath', str(ROOT / DIST), '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build'),
    str(ROOT / 'app' / 'main.py'),
]
code = subprocess.call(args, cwd=ROOT)
# exe 旁边放「猛人学习.exe.config」：解压出来的文件带「来自网络」标记时，.NET 照样加载软件自带的组件
# （不然窗口建不出来、双击没反应，2026-11-09 朋友的电脑；见 app/core/unblock.py）。软件内更新会把它一起换过去。
EXE_CONFIG = ('<?xml version="1.0" encoding="utf-8"?>\r\n<configuration>\r\n  <runtime>\r\n'
              '    <loadFromRemoteSources enabled="true"/>\r\n  </runtime>\r\n</configuration>\r\n')
if code == 0:
    (ROOT / DIST / '猛人学习' / '猛人学习.exe.config').write_text(EXE_CONFIG, encoding='utf-8', newline='')
    # OpenCV 读视频用的组件（30 MB）：截图认字只读图片，用不上
    for f in (ROOT / DIST / '猛人学习' / '_internal' / 'cv2').glob('opencv_videoio_ffmpeg*.dll'):
        f.unlink()
print(f'\n打包完成：{DIST}\\猛人学习\\猛人学习.exe' if code == 0 else '\n打包失败（如果提示「拒绝访问」，请先关闭正在运行的猛人学习）')
sys.exit(code)
