"""用 ffmpeg 生成测试用的音视频（仓库里不放大文件）：
- test-media/演示视频.mp4：40 秒，H.264 + AAC，能直接播放；
- test-media/旧格式.avi：20 秒，MPEG-4 + MP3，需要转换才能播放。
字幕 test-media/演示视频.mp4.猛人字幕.json 已经在仓库里。"""
import shutil
import subprocess
import sys
from pathlib import Path

MEDIA = Path(__file__).resolve().parents[1] / 'test-media'


def main():
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        sys.exit('找不到 ffmpeg，请先安装：winget install Gyan.FFmpeg')
    MEDIA.mkdir(exist_ok=True)
    jobs = [
        ('演示视频.mp4', ['-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=25:duration=40', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=40',
                     '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest']),
        ('旧格式.avi', ['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=25:duration=20', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=20',
                     '-c:v', 'mpeg4', '-c:a', 'mp3', '-shortest']),
    ]
    for name, args in jobs:
        out = MEDIA / name
        if out.exists():
            continue
        subprocess.run([ffmpeg, '-y', '-loglevel', 'error', *args, str(out)], check=True)
        print('生成', out)


if __name__ == '__main__':
    main()
