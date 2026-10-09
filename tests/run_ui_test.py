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
SCRIPT = ROOT / 'tests' / 'ui_selftest.js'
RESULT = SCRIPT.with_suffix('.result.json')


def main():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
    for d in (TD, UI):
        shutil.rmtree(d, ignore_errors=True)
    RESULT.unlink(missing_ok=True)
    (TD / 'appdata' / '猛人字幕').mkdir(parents=True)
    (TD / 'local').mkdir()
    (TD / 'appdata' / '猛人字幕' / 'settings.json').write_text(
        json.dumps({'storage_dir': str(TD / 'store')}, ensure_ascii=False), encoding='utf-8')
    UI.mkdir(parents=True)
    for name in ('演示视频.mp4', '演示视频.mp4.猛人字幕.json', '旧格式.avi'):
        shutil.copy(ROOT / 'test-media' / name, UI / name)

    env = dict(os.environ, APPDATA=str(TD / 'appdata'), LOCALAPPDATA=str(TD / 'local'), PYTHONIOENCODING='utf-8')
    if '--exe' in sys.argv:
        cmd = [str(ROOT / 'dist-next' / '猛人字幕' / '猛人字幕.exe')]
    else:
        cmd = [sys.executable, str(ROOT / 'app' / 'main.py')]
    subprocess.run(cmd + [f'--selftest={SCRIPT}', '--selftest-close'], env=env, timeout=400)

    results = json.loads(RESULT.read_text(encoding='utf-8')) if RESULT.exists() else ['没有结果']
    bad = [r for r in results if not r.startswith('PASS')]
    for r in bad:
        print(r)
    print(f'通过 {len(results) - len(bad)} / {len(results)}')
    shutil.rmtree(UI, ignore_errors=True)
    RESULT.unlink(missing_ok=True)
    if not bad:
        shutil.rmtree(TD, ignore_errors=True)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
