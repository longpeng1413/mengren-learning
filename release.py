"""打发布用的压缩包（放在 release\\ 里）：
    .venv\\Scripts\\python release.py                      # 用 dist\\猛人学习 打两个包
    .venv\\Scripts\\python release.py --src dist-next      # 用 dist-next 里刚打包好的
    .venv\\Scripts\\python release.py --personal 设置.json --out 桌面\\xx.zip   # 再打一个带个人设置的包（含密钥，别上传）

- mengren-learning-版本-windows.zip：只有软件（需要自己装 ffmpeg）；
- mengren-learning-版本-windows-with-ffmpeg.zip：附带 ffmpeg，解压就能用；
- 个人包：附带 ffmpeg 和导出的设置（软件第一次启动时自动导入）。
软件文件夹里的「缓存」「日志」不打进去。"""
import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP = ('缓存（可随时删除）', '日志（可随时删除）')

README = """猛人学习 {ver}

【怎么用】
1. 把整个「猛人学习」文件夹放到任意位置（不要放在 C:\\Program Files 这类需要管理员权限的地方）。
2. 双击「猛人学习.exe」。可以在桌面建一个快捷方式（右键 猛人学习.exe → 发送到 → 桌面快捷方式）。
3. 第一次使用：点右上角的「？」看使用说明；点齿轮打开「设置」填写 AI 服务（或用 ChatGPT 账号登录）。
{ffmpeg}{personal}
【需要】Windows 10 / 11（64 位）。Windows 10 打不开时，先安装微软的 WebView2 运行时：
https://developer.microsoft.com/microsoft-edge/webview2/
程序没有数字签名，第一次打开时 Windows 或安全软件可能会提示，选择「仍要运行」或加入信任即可。

【数据放在哪里】
设置、密钥、老师档案、媒体库：%APPDATA%\\猛人学习\\
字幕和整理结果：视频 / 文档旁边的「文件名.猛人学习.json」
缓存和日志：软件文件夹里的「缓存（可随时删除）」「日志（可随时删除）」

开源地址：https://github.com/longpeng1413/mengren-learning
"""

FFMPEG_NOTE = """
【ffmpeg】已经附带在「ffmpeg」文件夹里（用来读取和转换音视频），不用另外安装。
ffmpeg 是独立的开源软件，按 GPL v3 许可分发，许可证和源码地址见 ffmpeg 文件夹里的 LICENSE 和 README.txt。
"""

PERSONAL_NOTE = """
【个人设置】软件文件夹里的「猛人学习设置.json」包含你的 AI 密钥和 ChatGPT 登录。
第一次启动时会自动导入，导入后它会改名为「…（已导入，可删除）」，请删掉它。这个压缩包不要发给别人。
"""


def version():
    m = re.search(r"^VERSION = '([^']+)'", (ROOT / 'app' / 'main.py').read_text(encoding='utf-8'), re.M)
    return m.group(1)


def ffmpeg_files():
    """本机安装的 ffmpeg：ffmpeg.exe、ffprobe.exe，以及许可证和说明（GPL 要求一起分发）。"""
    exe = shutil.which('ffmpeg')
    if not exe:
        sys.exit('找不到 ffmpeg，请先安装：winget install Gyan.FFmpeg')
    bin_dir = Path(os.path.realpath(exe)).parent
    files = {'ffmpeg.exe': bin_dir / 'ffmpeg.exe', 'ffprobe.exe': bin_dir / 'ffprobe.exe'}
    for extra in ('LICENSE', 'README.txt'):
        p = bin_dir.parent / extra
        if p.is_file():
            files[extra] = p
    missing = [k for k in ('ffmpeg.exe', 'ffprobe.exe') if not files[k].is_file()]
    if missing:
        sys.exit('ffmpeg 文件不全：' + '、'.join(missing))
    return files


def make_zip(src: Path, dest: Path, ver, with_ffmpeg=False, settings: Path = None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix('.part')
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(src):
            if os.path.relpath(root, src).split(os.sep)[0] in SKIP:
                dirs[:] = []
                continue
            for f in files:
                full = Path(root) / f
                z.write(full, Path('猛人学习') / full.relative_to(src))
        if with_ffmpeg:
            for name, p in ffmpeg_files().items():
                z.write(p, Path('猛人学习') / 'ffmpeg' / name)
        if settings:
            z.write(settings, Path('猛人学习') / '猛人学习设置.json')
        z.writestr(str(Path('猛人学习') / '使用前请看.txt'),
                   README.format(ver=ver, ffmpeg=FFMPEG_NOTE if with_ffmpeg else
                                 '\n【ffmpeg】需要另外安装：在「终端」里运行 winget install Gyan.FFmpeg\n',
                                 personal=PERSONAL_NOTE if settings else '').replace('\n', '\r\n').encode('utf-8-sig'))
    os.replace(tmp, dest)
    print(f'{dest}  {dest.stat().st_size / 1024 / 1024:.1f} MB')
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default='dist')
    ap.add_argument('--personal', help='导出的设置文件（含密钥），打一个个人用的包')
    ap.add_argument('--out', help='个人包放在哪里')
    a = ap.parse_args()
    src = ROOT / a.src / '猛人学习'
    if not (src / '猛人学习.exe').is_file():
        sys.exit(f'找不到 {src}\\猛人学习.exe，请先打包（python build.py）')
    ver = version()
    if a.personal:
        out = Path(a.out) if a.out else ROOT / 'release' / f'猛人学习-{ver}-个人版（含密钥，勿外传）.zip'
        make_zip(src, out, ver, with_ffmpeg=True, settings=Path(a.personal))
        return
    rel = ROOT / 'release'
    for old in rel.glob('mengren-learning-*.zip'):
        old.unlink()
    make_zip(src, rel / f'mengren-learning-{ver}-windows.zip', ver)
    make_zip(src, rel / f'mengren-learning-{ver}-windows-with-ffmpeg.zip', ver, with_ffmpeg=True)


if __name__ == '__main__':
    main()
