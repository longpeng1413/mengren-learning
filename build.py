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
    '--collect-all', 'edge_tts',  # 朗读（Edge 在线声音），LGPLv3，许可证随包附上
    '--collect-all', 'pypdfium2', '--collect-all', 'pypdfium2_raw', '--collect-all', 'pypdfium2_cfg',
    '--exclude-module', 'numpy', '--exclude-module', 'tkinter',
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
print(f'\n打包完成：{DIST}\\猛人学习\\猛人学习.exe' if code == 0 else '\n打包失败（如果提示「拒绝访问」，请先关闭正在运行的猛人学习）')
sys.exit(code)
