"""媒体库树形显示、排列、拖动排序、合并、改名，以及电子书 / 老版 Office / 演示文稿的读取（2026-10-14）。不联网。
运行：.venv\\Scripts\\python tests\\test_library.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _old in Path(tempfile.gettempdir()).glob('mengren-lib-test-*'):
    shutil.rmtree(_old, ignore_errors=True)
TMP = Path(tempfile.mkdtemp(prefix='mengren-lib-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
ROOT = Path(__file__).resolve().parents[1]
TM = ROOT / 'test-media'
if not (TM / '演示视频.mp4').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
if not (TM / '测试书籍.pdf').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
if not (TM / '测试电子书.epub').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_ebooks.py')], check=True)
(TMP / 'appdata' / '猛人学习').mkdir(parents=True)
(TMP / 'appdata' / '猛人学习' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
sys.path.insert(0, str(ROOT / 'app'))

from bridge import Api  # noqa: E402
from core import book, config, doc as docs, library, tasks  # noqa: E402

api = Api()
passed = skipped = 0


def check(name, cond, detail=''):
    global passed
    if not cond:
        print('✗', name, detail)
        raise SystemExit(1)
    passed += 1
    print('✓', name)


# ------------------------------------------------------------ 新格式 ----
for fname, kind in (('测试电子书.epub', 'epub'), ('测试电子书.mobi', 'mobi'), ('测试演示.pptx', 'pptx'), ('测试文档.doc', 'doc'), ('测试演示.ppt', 'ppt')):
    if not (TM / fname).exists():
        print('（跳过：没有', fname, '，这台电脑没有 WPS / Office 生成它）')
        skipped += 1
        continue
    info = book.read(str(TM / fname))
    chs = [c['title'] for c in book.detect_chapters(info)]
    check(f'读取 {kind}：分出两章、正文读对', info['kind'] == kind and chs == ['第一章 趋势的起点', '第二章 仓位的分配']
          and '金字塔' in ''.join(p['text'] for p in info['pages']), (info['kind'], chs))
    if kind in ('epub', 'mobi', 'pptx'):
        check(f'读取 {kind}：书名作者、图片抽出来', info['title'] in ('测试电子书', '测试演示') and info['author'] == '测试作者'
              and any(p.get('images') for p in info['pages']) and all(Path(x).exists() for p in info['pages'] for x in p.get('images') or []))
check('新格式都在媒体库里列出、txt 不列', {'.epub', '.mobi', '.azw3', '.doc', '.ppt', '.pptx'} <= book.LIB_EXT and '.txt' not in book.LIB_EXT)
check('模板自带的作者、标题不用（之后用文件名）', book.ebook.clean_meta({'title': '空白演示', 'author': 'Administrator'}) == {'title': '', 'author': ''})
try:
    bad = TMP / '坏的.mobi'
    bad.write_bytes(b'0' * 100)
    book.read(str(bad))
    check('坏的电子书：说明原因', False)
except book.BookError as e:
    check('坏的电子书：说明原因', 'MOBI' in str(e) or '坏' in str(e), str(e))

# ------------------------------------------------------------ 准备一个多层的课程文件夹 ----
VID = TM / '演示视频.mp4'
C = TMP / '课程'
(C / '1、第一讲').mkdir(parents=True)
for n in ('2、b', '10、c', '1、a'):
    shutil.copy(VID, C / '1、第一讲' / f'{n}.mp4')
    time.sleep(0.05)
shutil.copy(VID, C / '第三讲.mp4')
time.sleep(0.05)
(C / '资料').mkdir()
shutil.copy(TM / '测试书籍.pdf', C / '资料' / '讲义.pdf')
shutil.copy(TM / '测试电子书.epub', C / '资料' / '补充.epub')
(C / '资料' / '说明.txt').write_text('说明', encoding='utf-8')
(C / '资料' / 'x.srt').write_text('1', encoding='utf-8')
(C / '资料' / '附件.zip').write_bytes(b'0')
time.sleep(0.05)
shutil.copy(VID, C / '预告.mp4')
(C / '_关键帧缓存').mkdir()
shutil.copy(VID, C / '_关键帧缓存' / 'v.mp4')
(C / '空文件夹').mkdir()

check('序号：1、 01. 第三讲 三、 1.2 都认得；没序号的认不出', library.number_key('1、a') == (1,) and library.number_key('01. x') == (1,)
      and library.number_key('第三讲') == (3,) and library.number_key('三、总结') == (3,) and library.number_key('1.2 小节') == (1, 2)
      and library.number_key('预告') is None and library.number_key('《峰级分析》10.08') is None and library.number_key('第20期 新手') == (20,))

# 先单独加子文件夹「资料」，绑定老师
t1 = config.save_teacher({'name': '资料老师'})
t2 = config.save_teacher({'name': '课程老师'})
r = api.call('library_add_folder', {'path': str(C / '资料')})
check('加文件夹：告诉界面加了几个、跳过了哪些格式（txt、字幕不算）', r['status'] == 'added' and r['doc'] == 2 and r['skipped'] == {'.zip': 1}, r)
api.call('library_set', {'path': str(C / '资料'), 'teacher': t1['id']})
shutil.copy(VID, TMP / '单独.mp4')
api.call('library_add_files', {'paths': [str(TMP / '单独.mp4'), str(C / '预告.mp4')]})

# 再加上层「课程」：先问
r = api.call('library_add_folder', {'path': str(C)})
check('再加上层文件夹：先问要不要合并', r['status'] == 'need_merge' and r['children'] == ['资料'] and r['files'] == 1 and r['groups'] is None, r)
r = api.call('library_add_folder', {'path': str(C), 'merge': True})
tree = r['groups']
check('合并：原来单独加的子文件夹和文件并进来，最上层只剩「单独.mp4」和「课程」（按加入先后）',
      [n['name'] for n in tree] == ['单独', '课程'] and r['merged'], [n['name'] for n in tree])
course = tree[1]
check('按实际层级显示：带序号的按序号在前，没序号的按下载时间在后；「_」开头的、空文件夹不列',
      [c['name'] for c in course['children']] == ['1、第一讲', '第三讲', '资料', '预告'], [c['name'] for c in course['children']])
lesson = course['children'][0]
check('子文件夹里按序号排（10 排在 2 后面）', [c['name'] for c in lesson['children']] == ['1、a', '2、b', '10、c'] and lesson['count'] == 3)
check('文件夹显示里面一共几个', course['count'] == 3 + 1 + 2 + 1)
ziliao = course['children'][2]
check('合并后原来的设置带过来（绑定的老师）', ziliao['teacher'] == t1['id'] and not ziliao['top'])
check('老师：自己绑定的用自己的', library.folder_teacher(str(C / '资料' / '讲义.pdf')) == t1['id'])
check('老师：没绑定的沿用上一层，上一层也没有就是无', library.folder_teacher(str(C / '1、第一讲' / '1、a.mp4')) == '')
api.call('library_set', {'path': str(C), 'teacher': t2['id']})
check('老师：上层绑定后，没单独绑定的子文件夹沿用上层', library.folder_teacher(str(C / '1、第一讲' / '1、a.mp4')) == t2['id']
      and library.folder_teacher(str(C / '资料' / '讲义.pdf')) == t1['id'])
r = api.call('library_add_folder', {'path': str(C / '1、第一讲')})
check('上层已经在了，再加子文件夹：提示已经在里面了，不重复加', r['status'] == 'inside' and r['parent_name'] == '课程')
check('同一个文件夹再加一次：提示已经有了', api.call('library_add_folder', {'path': str(C)})['status'] == 'exists')

# 拖动排序
r = api.call('library_reorder', {'parent': str(C), 'names': ['预告.mp4', '1、第一讲', '资料', '第三讲.mp4']})
check('拖动：没序号的可以放到任意位置，带序号的之间还是按序号', [c['name'] for c in r['groups'][1]['children']] == ['预告', '1、第一讲', '资料', '第三讲'],
      [c['name'] for c in r['groups'][1]['children']])
r = api.call('library_reorder', {'parent': str(C), 'names': ['第三讲.mp4', '预告.mp4', '1、第一讲', '资料']})
check('拖动：带序号的拖乱了不算（保持序号顺序，界面会提示改名）', [c['name'] for c in r['groups'][1]['children']] == ['1、第一讲', '预告', '第三讲', '资料'],
      [c['name'] for c in r['groups'][1]['children']])
r = api.call('library_reorder', {'parent': '', 'names': [str(C), str(TMP / '单独.mp4')]})
check('拖动：最上层随意排', [n['name'] for n in r['groups']] == ['课程', '单独'])
api.call('library_set', {'path': str(C / '1、第一讲'), 'collapsed': True})
check('下层文件夹可以收起，记住', next(c for c in library.view()[0]['children'] if c['name'] == '1、第一讲')['collapsed'])

# 不合并：重复加入
D = TMP / '另一门课'
(D / '子').mkdir(parents=True)
shutil.copy(VID, D / '子' / '视频.mp4')
api.call('library_add_folder', {'path': str(D / '子')})
r = api.call('library_add_folder', {'path': str(D), 'merge': False})
check('不合并：上层照样加入，子文件夹出现两次', [n['name'] for n in r['groups']][-2:] == ['子', '另一门课'] and r['groups'][-1]['children'][0]['name'] == '子')
api.call('library_remove', {'kind': 'folder', 'path': str(D)})
api.call('library_remove', {'kind': 'folder', 'path': str(D / '子')})

# Agent 看到的还是原来的格式
from core import workflow  # noqa: E402
items = workflow.find(course='课程')
check('Agent 查找：按显示顺序摊平，带子文件夹名', [x['name'] for x in items][:3] == ['1、a', '2、b', '10、c'] and items[0]['sub'] == '1、第一讲', [x['name'] for x in items])

# ------------------------------------------------------------ 改名 ----
f = C / '1、第一讲' / '2、b.mp4'
d = docs.new(str(f), {'duration': 40})
d.update(segments=[{'start': 0, 'end': 1, 'text': '你好', 'tr': ''}], summary='重点')
docs.save(str(f), d)
config.set_position(str(f), 12.0, 40)
config.add_recent(str(f), '2、b')
r = api.call('rename', {'path': str(f), 'name': '2、改过的名字'})
nf = C / '1、第一讲' / '2、改过的名字.mp4'
nd = docs.load(str(nf))
check('改文件名：电脑上的文件、字幕文件一起改，扩展名不变', r['ok'] and r['path'] == str(nf) and nf.exists() and not f.exists()
      and docs.sidecar(str(nf)).exists() and not docs.sidecar(str(f)).exists(), r)
check('改文件名：标题、记的路径跟着改，旧路径记下来（剪藏时找原来的笔记）', nd['title'] == '2、改过的名字' and nd['media'] == str(nf)
      and str(f) in nd['media_history'] and nd['summary'] == '重点')
check('改文件名：播放进度、最近打开跟着改', config.get_position(str(nf)) == 12.0 and any(x['path'] == str(nf) for x in config.load()['recent']))
r = api.call('rename', {'path': str(nf), 'name': '1、a'})
check('改名：同名已经有了，不改并说明', not r['ok'] and '同名' in r['error'])
r = api.call('rename', {'path': str(nf), 'name': 'a/b'})
check('改名：不能用的符号，不改并说明', not r['ok'] and '不能' in r['error'])
tasks.claim(docs.media_key(str(nf)), 'x-task')
(tasks.TASKS_DIR).mkdir(parents=True, exist_ok=True)
(tasks.TASKS_DIR / 'x-task.json').write_text(json.dumps({'task_id': 'x-task', 'status': 'running', 'pid': os.getpid(), 'path': str(nf)}), encoding='utf-8')
r = api.call('rename', {'path': str(nf), 'name': '2、又改'})
check('改名：后台正在整理这节课，不改', not r['ok'] and '整理' in r['error'])
r = api.call('rename', {'path': str(C / '1、第一讲'), 'name': '01、第一讲'})
check('改文件夹：里面有课正在整理，不改', not r['ok'] and '整理' in r['error'])
tasks.release(docs.media_key(str(nf)), 'x-task')

# 改文件夹名
api.call('library_reorder', {'parent': str(C / '1、第一讲'), 'names': ['1、a.mp4', '2、改过的名字.mp4', '10、c.mp4']})
api.call('library_set', {'path': str(C / '1、第一讲'), 'teacher': t1['id']})
r = api.call('rename', {'path': str(C / '1、第一讲'), 'name': '01、第一讲'})
L = C / '01、第一讲'
nd = docs.load(str(L / '2、改过的名字.mp4'))
check('改文件夹名：电脑上的文件夹改了，里面的字幕文件记的路径跟着改', r['ok'] and L.is_dir() and nd['media'] == str(L / '2、改过的名字.mp4')
      and str(nf) in nd['media_history'])
check('改文件夹名：绑定的老师、收起状态跟着走', library.folder_teacher(str(L / '1、a.mp4')) == t1['id']
      and next(c for c in library.view()[0]['children'] if c['name'] == '01、第一讲')['collapsed'])
check('改文件夹名：播放进度跟着走', config.get_position(str(L / '2、改过的名字.mp4')) == 12.0)
r = api.call('rename', {'path': str(C), 'name': '课程（改名）'})
C2 = TMP / '课程（改名）'
check('改最上层文件夹名：媒体库里的名字、路径跟着改', r['ok'] and r['groups'][0]['name'] == '课程（改名）' and r['groups'][0]['path'] == str(C2)
      and library.folder_teacher(str(C2 / '01、第一讲' / '1、a.mp4')) == t1['id'] and library.folder_teacher(str(C2 / '第三讲.mp4')) == t2['id'])

# 剪藏过的课改名后再剪藏：保留 Obsidian 里原来的文件名
vault = TMP / '库'
(vault / '猛人学习').mkdir(parents=True)
v = C2 / '第三讲.mp4'
dv = docs.new(str(v), {'duration': 40})
dv.update(segments=[{'start': 0, 'end': 1, 'text': '你好。', 'tr': ''}], summary='重点')
c1 = docs.clip(dv, str(vault), '猛人学习')
dv['clip'] = {'path': c1['path'], 'img_base': c1['img_base']}
docs.save(str(v), dv)
moved = vault / '我的分类' / Path(c1['path']).name  # 用户在 Obsidian 里把笔记挪了地方
moved.parent.mkdir()
os.replace(c1['path'], moved)
api.call('rename', {'path': str(v), 'name': '第三讲（新名字）'})
dv2 = docs.load(str(C2 / '第三讲（新名字）.mp4'))
c2 = docs.clip(dv2, str(vault), '猛人学习')
check('改名后再剪藏：找到挪过地方的原笔记，文件名不变', c2['path'] == str(moved) and not (vault / '猛人学习' / '第三讲（新名字）.md').exists(), c2['path'])
check('改名后再剪藏：插图沿用原来的名字（不多出一套新图片）', c2['img_base'] == '第三讲')

# PDF 改名：同名的 Word 参考一起改
shutil.copy(TM / '测试书籍.pdf', C2 / '资料' / '书.pdf')
(C2 / '资料' / '书.docx').write_bytes(b'PK')
api.call('rename', {'path': str(C2 / '资料' / '书.pdf'), 'name': '新书'})
check('PDF 改名：同名的 Word 参考一起改', (C2 / '资料' / '新书.pdf').exists() and (C2 / '资料' / '新书.docx').exists() and not (C2 / '资料' / '书.docx').exists())

# 移除最上层文件夹：里面的设置和顺序一起清掉（不留垃圾）
api.call('library_remove', {'kind': 'folder', 'path': str(C2)})
lib = library.load()
check('移除文件夹：它下面的老师绑定、拖好的顺序一起清掉', not lib['dirs'] and not lib['orders'], (lib['dirs'], lib['orders']))

r = subprocess.run([sys.executable, '-m', 'pyflakes', str(ROOT / 'app' / 'core' / 'library.py'), str(ROOT / 'app' / 'core' / 'ebook.py')], capture_output=True, text=True)
check('pyflakes', r.returncode == 0, r.stdout)
print(f'\n全部通过：{passed} 项' + (f'（跳过 {skipped} 项）' if skipped else ''))
import logging  # noqa: E402
logging.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
