"""在真实窗口里跑界面自测（会弹出一个窗口，约 1–2 分钟后自动关闭）。
用法：
    .venv\\Scripts\\python tests\\run_ui_test.py            # 用源码
    .venv\\Scripts\\python tests\\run_ui_test.py --exe      # 用 dist-next 里打包好的 exe
设置、缓存和日志都放在项目里的 .testdata，不影响正式使用的数据。"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TD = ROOT / '.testdata'
UI = ROOT / 'test-media' / 'ui'
UI_AI = ROOT / 'test-media' / 'ui-ai'
UI_LIB = ROOT / 'test-media' / 'ui-lib'  # 测媒体库树形、排序、改名用的多层文件夹  # 测整理按钮用的另一份视频（不在媒体库里，不影响别的检查）
SCRIPT = ROOT / 'tests' / 'ui_selftest.js'
RESULT = SCRIPT.with_suffix('.result.json')


def main():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
    if not (ROOT / 'test-media' / '测试书籍.pdf').exists():
        subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
    for d in (TD, UI, UI_AI, UI_LIB):
        shutil.rmtree(d, ignore_errors=True)
    RESULT.unlink(missing_ok=True)
    (TD / 'appdata' / '猛人学习').mkdir(parents=True)
    (TD / 'local').mkdir()
    (TD / 'appdata' / '猛人学习' / 'settings.json').write_text(
        json.dumps({'storage_dir': str(TD / 'store')}, ensure_ascii=False), encoding='utf-8')
    UI.mkdir(parents=True)
    UI_AI.mkdir(parents=True)
    if not (ROOT / 'test-media' / '测试电子书.epub').exists():
        subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_ebooks.py')], check=True)
    (UI_LIB / '1、第一讲').mkdir(parents=True)
    (UI_LIB / '资料').mkdir()
    for name in ('1、第一讲/2、乙.avi', '1、第一讲/1、甲.avi', '预告.avi'):
        shutil.copy(ROOT / 'test-media' / '旧格式.avi', UI_LIB / name)
    shutil.copy(ROOT / 'test-media' / '测试电子书.epub', UI_LIB / '资料' / '补充.epub')
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', UI_AI / 'AI整理测试.mp4')
    shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', UI_AI / '《AI整理测试书》.pdf')
    for name in ('演示视频.mp4', '演示视频.mp4.猛人字幕.json', '旧格式.avi', '测试书籍.pdf'):
        shutil.copy(ROOT / 'test-media' / name, UI / name)

    env = dict(os.environ, APPDATA=str(TD / 'appdata'), LOCALAPPDATA=str(TD / 'local'), PYTHONIOENCODING='utf-8')
    if '--exe' in sys.argv:
        cmd = [str(ROOT / 'dist-next' / '猛人学习' / '猛人学习.exe')]
    else:
        cmd = [sys.executable, str(ROOT / 'app' / 'main.py')]
    rc = subprocess.run(cmd + [f'--selftest={SCRIPT}', '--selftest-close', '--fake-ai'], env=env, timeout=400).returncode
    if rc:
        print('程序退出码', rc, hex(rc & 0xffffffff))

    results = json.loads(RESULT.read_text(encoding='utf-8')) if RESULT.exists() else ['没有结果']
    bad = [r for r in results if not r.startswith('PASS')]
    for r in bad:
        print(r)
    print(f'通过 {len(results) - len(bad)} / {len(results)}')
    shutil.rmtree(UI, ignore_errors=True)
    shutil.rmtree(UI_AI, ignore_errors=True)
    shutil.rmtree(UI_LIB, ignore_errors=True)
    RESULT.unlink(missing_ok=True)
    if not bad:
        shutil.rmtree(TD, ignore_errors=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
