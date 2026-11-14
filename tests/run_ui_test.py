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
import socket
from pathlib import Path

from ui_native_input import drive_input

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
    # 老师工作台用的测试老师：一个小 Obsidian 库，观点库来源表指向课程笔记，笔记属性里的视频文件是上面的演示视频
    kb = UI / 'kb'
    (kb / '.obsidian').mkdir(parents=True)
    (kb / '03').mkdir()
    (kb / '03' / '演示课.md').write_text(f'---\n视频文件: "{(UI / "演示视频.mp4").as_posix()}"\n---\n\n# 演示课\n', encoding='utf-8')
    teacher = kb / '04、外部知识转化库' / '测试老师'
    teacher.mkdir(parents=True)
    (teacher / '01、观点库（逐节提取）.md').write_text(
        '| 编号 | 课程 | 笔记 |\n| --- | --- | --- |\n| S01 | 演示课 | [[演示课]] |\n\n'
        '| 编号 | 观点 | 条件 / 边界 | 出处 | 类型 |\n| --- | --- | --- | --- | --- |\n| S01-01 | 风险要先算好。 | — | S01 00:02 | 观点 |\n', encoding='utf-8')
    (teacher / '03、老师画像（对话用）.md').write_text('# 测试老师画像\n', encoding='utf-8')

    env = dict(os.environ, APPDATA=str(TD / 'appdata'), LOCALAPPDATA=str(TD / 'local'), PYTHONIOENCODING='utf-8')
    # 这台电脑上有猛人快传下好的本机识别模型时借来用（只读），界面自测里点「测试本机识别」真识别一次，
    # 打包版能确认运行库（onnxruntime.dll）加载的是自带的那份（Windows 11 系统文件夹里有一个旧的）
    kc = Path(os.environ.get('APPDATA', '')) / 'com.personal.lantransfer' / 'lan_transfer' / 'asr_model' / 'sensevoice-int8-2024-07-17'
    if (kc / 'model.int8.onnx').is_file():
        env['MENGREN_ASR_MODEL_DIR'] = str(kc)
    if '--exe' in sys.argv:
        cmd = [str(ROOT / 'dist-next' / '猛人学习' / '猛人学习.exe')]
    else:
        cmd = [sys.executable, str(ROOT / 'app' / 'main.py')]
    # Only the isolated test window exposes a debugging port; production is unchanged.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env['WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS'] = f'--remote-debugging-port={port}'
    process = subprocess.Popen(cmd + [f'--selftest={SCRIPT}', '--selftest-close', '--fake-ai'], env=env)
    drive_input(port, process)
    rc = process.wait(timeout=400)
    if rc:
        print('程序退出码', rc, hex(rc & 0xffffffff))

    results = json.loads(RESULT.read_text(encoding='utf-8')) if RESULT.exists() else ['没有结果']
    evidence = ROOT / 'build' / 'ui-regression'
    evidence.mkdir(parents=True, exist_ok=True)
    label = 'packaged' if '--exe' in sys.argv else 'source'
    (evidence / f'{label}-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    log_file = TD / 'store' / '日志（可随时删除）' / 'app.log'
    if log_file.exists():
        trace = [line for line in log_file.read_text(encoding='utf-8').splitlines() if 'SCROLL ' in line]
        (evidence / f'{label}-scroll.log').write_text('\n'.join(trace), encoding='utf-8')
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
