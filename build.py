"""打包成 exe：生成 dist-next\\猛人学习\\猛人学习.exe，再双击「更新到新版.bat」换进 dist。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 默认打包到 dist-next，再用「更新到新版.bat」换进 dist（会保留缓存和日志）
DIST = next((a.split('=', 1)[1] for a in sys.argv[1:] if a.startswith('--dist=')), 'dist-next')
args = [
    sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed',
    '--name', '猛人学习',
    '--icon', str(ROOT / 'app' / 'assets' / 'icon.ico'),
    '--paths', str(ROOT / 'app'),
    '--add-data', f'{ROOT / "app" / "web"};web',
    '--hidden-import', 'websocket',
    '--hidden-import', 'pystray._win32',
    '--collect-all', 'pypdfium2', '--collect-all', 'pypdfium2_raw', '--collect-all', 'pypdfium2_cfg',
    '--exclude-module', 'numpy', '--exclude-module', 'tkinter',
    '--distpath', str(ROOT / DIST), '--workpath', str(ROOT / 'build'), '--specpath', str(ROOT / 'build'),
    str(ROOT / 'app' / 'main.py'),
]
code = subprocess.call(args, cwd=ROOT)
print(f'\n打包完成：{DIST}\\猛人学习\\猛人学习.exe' if code == 0 else '\n打包失败（如果提示「拒绝访问」，请先关闭正在运行的猛人学习）')
sys.exit(code)
