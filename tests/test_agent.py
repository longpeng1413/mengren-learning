"""Agent 自动整理（任务层 workflow.py、任务记录 tasks.py、命令行 cli.py）的自测。
不联网、不花钱：识别和 AI 都换成假的，检查步骤顺序、合并规则、断点续做、错误分类、保存冲突、命令行 JSON。
运行：.venv\\Scripts\\python tests\\test_agent.py"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

for _old in Path(tempfile.gettempdir()).glob('mengren-agent-test-*'):
    shutil.rmtree(_old, ignore_errors=True)
TMP = Path(tempfile.mkdtemp(prefix='mengren-agent-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'test-media' / '演示视频.mp4').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
if not (ROOT / 'test-media' / '测试书籍.pdf').exists():
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
(TMP / 'appdata' / '猛人学习').mkdir(parents=True)
(TMP / 'appdata' / '猛人学习' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
sys.path.insert(0, str(ROOT / 'app'))

import cli  # noqa: E402
from bridge import Api  # noqa: E402
from core import asr, book, config, doc as docs, frames, library, llm, tasks, workflow as wf  # noqa: E402

passed = 0


def check(name, cond, detail=''):
    global passed
    if not cond:
        print('✗', name, detail)
        raise SystemExit(1)
    passed += 1
    print('✓', name)


# ------------------------------------------------------------ 准备数据 ----
COURSE = TMP / '峰级分析'
(COURSE / '1、基础').mkdir(parents=True)
for n in ('1、第一课', '2、第二课', '3、第三课'):
    shutil.copy(ROOT / 'test-media' / '演示视频.mp4', COURSE / '1、基础' / f'{n}.mp4')
ENG = TMP / 'English Course'
ENG.mkdir()
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', ENG / 'Lesson 1.mp4')
BOOKS = TMP / '书'
BOOKS.mkdir()
shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', BOOKS / '《测试书籍》测试作者.pdf')
OUTSIDE = TMP / '不在库里.mp4'
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', OUTSIDE)

config.update({'providers': {'deepseek': {'key': 'sk-test-deepseek'}, 'openai': {'key': 'sk-test-openai'}, 'doubao': {'key': 'test-doubao'}},
               'chat': 'deepseek', 'correct': 'deepseek', 'translate': 'deepseek', 'vision': 'openai', 'asr_zh': 'doubao', 'asr_en': 'openai',
               'show_tr': False, 'auto_review': True})
teacher = config.save_teacher({'name': '峰级', 'terms': '峰级, 拐点, 三段结构', 'lang': 'zh'})
teacher_en = config.save_teacher({'name': 'English', 'terms': 'trend', 'lang': 'en'})
library.add_folder(str(COURSE))
library.set_folder(str(COURSE), teacher=teacher['id'])
library.add_folder(str(ENG))
library.set_folder(str(ENG), teacher=teacher_en['id'])
library.add_folder(str(BOOKS))
L1 = str(COURSE / '1、基础' / '1、第一课.mp4')
L2 = str(COURSE / '1、基础' / '2、第二课.mp4')
L3 = str(COURSE / '1、基础' / '3、第三课.mp4')
EN = str(ENG / 'Lesson 1.mp4')
BK = str(BOOKS / '《测试书籍》测试作者.pdf')

# ------------------------------------------------------------ 假的识别和 AI ----
calls = Counter()
FAIL = {}  # 步骤 → [还要失败几次, 异常]
seen = {}


def maybe_fail(stage):
    f = FAIL.get(stage)
    if f and f[0] > 0:
        f[0] -= 1
        raise f[1]


def fake_recognise(path, duration, provider, lang, s, cancel, progress, on_cues, pending=None, teacher=None, title=''):
    calls['asr'] += 1
    seen['asr_provider'] = provider
    maybe_fail('transcribe')
    progress(0.5, '识别中…')
    if lang == 'en':
        cues = [{'start': 0.0, 'end': 2.0, 'text': 'Hello everyone.'}, {'start': 2.0, 'end': 4.0, 'text': 'Today we talk about trend.'}]
    else:
        cues = [{'start': 0.0, 'end': 2.0, 'text': '大家好，今天讲峰级。'}, {'start': 2.0, 'end': 4.0, 'text': '风级的入门很重要。'},
                {'start': 4.0, 'end': 6.0, 'text': '这是一个关于峰级的讲论文。'}, {'start': 6.0, 'end': 9.0, 'text': '这就是三段结构。'}]
    on_cues(cues)
    return cues


def fake_complete(settings, name, messages, json_mode=False):
    sys_text = str(messages[0].get('content')) if messages else ''
    if sys_text == llm.CORRECT_SYSTEM:
        calls['correct'] += 1
        maybe_fail('correct')
        return json.dumps({'改动': {'1': '峰级的入门很重要。'}, '存疑': {'2': '疑似识别错误，可能是「讲论」'}}, ensure_ascii=False)
    if sys_text == llm.TRANSLATE_SYSTEM:
        calls['translate'] += 1
        payload = json.loads(messages[-1]['content'].split('\n')[-1])
        return json.dumps({'译文': {k: '中文：' + v for k, v in payload.items()}}, ensure_ascii=False)
    if sys_text == llm.REVIEW_SYSTEM:
        calls['review'] += 1
        maybe_fail('review')
        return json.dumps({'补充': [{'放在': '二、结构', '内容': '- 老师自述：讲了很多年 [00:06]'}]}, ensure_ascii=False)
    if sys_text == llm.TERMS_SYSTEM:
        calls['terms'] += 1
        return json.dumps({'新术语': [{'术语': '再推进', '时间': '00:06', '原话': '这就是三段结构'}, {'术语': '峰级', '时间': '00:00'}],
                           '识别错误': [{'错写': '风级', '正确': '峰级', '时间': '00:02', '原话': '风级的入门'}]}, ensure_ascii=False)
    raise AssertionError('没想到的 AI 调用：' + sys_text[:40])


def fake_stream(settings, name, messages, cancel=None, json_mode=False):
    calls['summary'] += 1
    maybe_fail('summary')
    for piece in ['这节课讲峰级。\n\n## 一、峰级\n\n- 峰级的入门很重要 [00:02]\n\n', '## 二、结构\n\n- 三段结构 [00:06]\n']:
        yield piece


def fake_extract(settings, name, path, duration, segments, cancel=None, progress=lambda f, m=None: None, on_part=None):
    calls['frames'] += 1
    maybe_fail('frames')
    return [{'t': 5.0, 'why': '老师说请看', 'note': '三段结构示意图', 'figure': True}]


def fake_recheck(settings, name, path, items, frames_list, cancel=None, progress=lambda f, m=None: None, on_part=None):
    calls['recheck'] += 1
    maybe_fail('recheck')
    seen['recheck_items'] = items
    return [{'i': it['i'], 'text': it['text'].replace('讲论文', '讲论')} for it in items]


asr.recognise = fake_recognise
llm.complete = fake_complete
llm.stream = fake_stream
frames.extract = fake_extract
frames.recheck_doubts = fake_recheck
wf.RETRY_WAIT = (0.05, 0.05)

# ------------------------------------------------------------ 查找 ----
items = wf.find(course='峰级')
check('按课程找：媒体库里的文件夹名', [x['path'] for x in items] == [L1, L2, L3], items)
check('按课程 + 第几个找', [x['path'] for x in wf.find(course='峰级', index=2)] == [L2])
check('按子文件夹名找课程', [x['path'] for x in wf.find(course='基础', index=3)] == [L3])
check('按关键词找', [x['path'] for x in wf.find('第三课')] == [L3])
vid = wf.video_id(L2)
check('video_id 稳定，能用它找回文件', vid == wf.video_id(L2.upper()) and wf.resolve(vid) == L2 and len(vid) == 12)
check('找到的结果带课程、编号、时长、状态', {'course_id', 'video_id', 'name', 'path', 'duration', 'state', 'index', 'kind'} <= set(items[0])
      and items[0]['state'] == '未处理' and items[0]['kind'] == 'video')
check('只找还没整理的', len(wf.find(todo=True)) == 5 and len(wf.find(todo=True, kind='book')) == 1)
check('按新增日期找', len(wf.find(since='today')) == 5 and wf.find(since='2999-01-01') == [])
try:
    wf.resolve(str(OUTSIDE))
    check('媒体库以外的文件不处理', False)
except wf.WorkError as e:
    check('媒体库以外的文件不处理', e.code == 'not_in_library' and not e.retryable)

# ------------------------------------------------------------ 加载 ----
inf = wf.info(L1)
check('加载：返回文件、时长、字幕状态，不改任何东西', inf['metadata']['duration'] > 0 and inf['state'] == {'exists': False}
      and not inf['processed'] and inf['teacher'] == teacher['id'] and docs.load(L1) is None, inf)

# ------------------------------------------------------------ 整条流程 ----
progress_seen = []
t1 = wf.process(L1, on_progress=lambda d: progress_seen.append(d.get('stage')))
st = {s['name']: s['status'] for s in t1['stages']}
check('一键整理：全部完成', t1['status'] == 'completed' and t1['error'] is None, t1)
check('步骤顺序和界面的一键整理一样', [s['name'] for s in t1['stages']] == ['transcribe', 'correct', 'frames', 'recheck', 'translate', 'summary', 'review', 'terms'])
check('中文视频不翻译，其余都做了', st == {'transcribe': 'completed', 'correct': 'completed', 'frames': 'completed', 'recheck': 'completed',
                                     'translate': 'skipped', 'summary': 'completed', 'review': 'completed', 'terms': 'completed'}, st)
check('进度会报告', 'correct' in progress_seen and 'summary' in progress_seen)
d = docs.load(L1)
check('字幕写进原来的字幕文件', len(d['segments']) == 4 and d['lang'] == 'zh' and d['provider'].startswith('doubao') and d['teacher'] == teacher['id'])
check('AI 校对：改了的句子记进 fixes（积累术语表用）', d['segments'][1]['text'] == '峰级的入门很重要。'
      and any(f['before'] == '风级的入门很重要。' and f['by'] == 'ai' for f in d['fixes']) and d['corrected'] is True)
check('AI 校对前存了一份历史版本（可以在软件里恢复）', d['versions'][0]['reason'] == 'AI 校对前' and d['versions'][0]['segments'][1]['text'] == '风级的入门很重要。')
check('画面复核：存疑句改正、去掉存疑标记', d['segments'][2]['text'] == '这是一个关于峰级的讲论。' and d['doubts'] == []
      and any(f['by'] == 'frames' for f in d['fixes']) and seen['recheck_items'][0]['before'])
check('找关键帧：插图存进 frames', [f['t'] for f in d['frames']] == [5.0] and d['frames'][0]['figure'])
check('课程重点 + 查漏补缺', '## 一、峰级' in d['summary'] and '老师自述：讲了很多年 [00:06]' in d['summary'])
check('新术语只放进「待勾选」，不写术语表', [x['term'] for x in d['terms_pending']['terms']] == ['再推进']
      and d['terms_pending']['errors'][0]['wrong'] == '风级' and '再推进' not in (config.get_teacher(teacher['id'])['terms']))
check('不剪藏、不替用户标「已校对」', not d.get('clip') and not d.get('reviewed'))
check('每一步记下完成时间', set(d['steps']) >= {'transcribe', 'correct', 'frames', 'recheck', 'summary', 'review', 'terms'})
rec = tasks.load(t1['task_id'])
check('任务记录存到磁盘：每步状态、时间、模型', rec['status'] == 'completed' and rec['stages'][1]['model'].startswith('DeepSeek')
      and rec['stages'][0]['started'] and rec['stages'][0]['finished'] and rec['video_id'] == wf.video_id(L1)
      and rec['options']['steps'] and rec['outputs']['subtitle_file'].endswith('.猛人学习.json'))
check('任务记录里没有密钥', 'sk-test' not in (tasks.TASKS_DIR / f'{t1["task_id"]}.json').read_text(encoding='utf-8'))
check('媒体库能看到整理结果', next(x for x in wf.find(course='峰级') if x['path'] == L1)['state'] == '已整理')

# ------------------------------------------------------------ 再运行 ----
before = calls.copy()
t1b = wf.process(L1)
check('已完成的课再运行：所有步骤跳过，不再调用识别和 AI', t1b['status'] == 'completed' and calls == before
      and all(s['status'] == 'skipped' and s['reason'] for s in t1b['stages']), [(s['name'], s['status'], s['reason']) for s in t1b['stages']])
t1c = wf.run_step(L1, 'summary', force=True)
check('force：指定的步骤重做', t1c['status'] == 'completed' and calls['summary'] == before['summary'] + 1 and calls['asr'] == before['asr'])

# ------------------------------------------------------------ 中途失败、接着做 ----
FAIL['summary'] = [99, llm.LlmError('连接DeepSeek失败：Read timed out')]
c0 = calls.copy()
t2 = wf.process(L2)
st2 = {s['name']: s for s in t2['stages']}
check('中途失败：停在出错的那一步', t2['status'] == 'failed' and t2['error']['stage'] == 'summary' and st2['summary']['status'] == 'failed'
      and st2['terms']['status'] == 'pending', t2['error'])
check('网络错误：可以重试，自动重试了 2 次', t2['error']['error_code'] == 'network' and t2['error']['retryable'] and st2['summary']['retries'] == 2
      and calls['summary'] - c0['summary'] == 3)
d2 = docs.load(L2)
check('失败前完成的步骤已经保存', d2['corrected'] and len(d2['segments']) == 4 and not d2.get('summary'))
FAIL.pop('summary')
c1 = calls.copy()
t2r = wf.resume(t2['task_id'])
st2r = {s['name']: s['status'] for s in t2r['stages']}
check('接着做：从失败的那一步继续，不重新识别、不重新校对', t2r['status'] == 'completed' and calls['asr'] == c1['asr']
      and calls['correct'] == c1['correct'] and calls['summary'] == c1['summary'] + 1
      and st2r['transcribe'] == 'skipped' and st2r['summary'] == 'completed', st2r)

FAIL['correct'] = [1, llm.LlmError('DeepSeek出错（HTTP 503）：busy')]
t3 = wf.process(L3, steps=['transcribe', 'correct'])
check('临时出错：自动重试后成功', t3['status'] == 'completed' and t3['stages'][1]['retries'] == 1 and docs.load(L3)['corrected'])
FAIL.pop('correct', None)

# AI 校对失败：什么都不写（不会出现「报错了，结果却出现了一半」）
seg_before = json.dumps(docs.load(L3)['segments'], ensure_ascii=False)
FAIL['correct'] = [99, llm.LlmError('校对结果格式不对，请重试或换一个 AI 服务')]
t4 = wf.run_step(L3, 'correct', force=True)
check('AI 校对失败：字幕一句都没改，也没多出历史版本', t4['status'] == 'failed' and t4['error']['error_code'] == 'bad_output'
      and json.dumps(docs.load(L3)['segments'], ensure_ascii=False) == seg_before and len(docs.load(L3).get('versions') or []) == 1)
FAIL.pop('correct')

# 不可重试的错误
config.update({'chat': 'claude'})  # Claude 没填 Key（设置里空的 Key 不会覆盖已有的，所以换一个没填的服务）
c2 = calls.copy()
t5 = wf.run_step(L3, 'summary')
check('缺 API Key：不重试，错误码 config', t5['status'] == 'failed' and t5['error']['error_code'] == 'config' and not t5['error']['retryable']
      and t5['stages'][0]['retries'] == 0 and calls['summary'] == c2['summary'])
config.update({'chat': 'deepseek'})
check('错误分类', tasks.classify(llm.LlmError('DeepSeek鉴权失败：bad key'))[:2] == ('auth', False)
      and tasks.classify(asr.AsrError('豆包额度不足或账户欠费：x'))[:2] == ('quota', False)
      and tasks.classify(asr.AsrError('连接豆包失败：timeout'))[:2] == ('network', True)
      and tasks.classify(__import__('core.media', fromlist=['x']).MediaError('无法读取这个文件：moov atom not found'))[:2] == ('media', False)
      and tasks.classify(FileNotFoundError('x'))[0] == 'not_found' and tasks.classify(KeyError('x'))[:2] == ('internal', False))

# 「软」步骤失败不影响后面
FAIL['review'] = [99, llm.LlmError('查漏结果格式不对，请重试或换一个 AI 服务')]
t6 = wf.process(L3)
check('查漏补缺失败：不影响后面的新术语，写进 warnings', t6['status'] == 'completed' and {s['name']: s['status'] for s in t6['stages']}['review'] == 'failed'
      and {s['name']: s['status'] for s in t6['stages']}['terms'] == 'completed' and t6['warnings'], t6['warnings'])
FAIL.pop('review')

# ------------------------------------------------------------ 换模型 ----
t7 = wf.process(EN, provider='siliconflow', steps=['transcribe'])
check('换识别服务：用指定的', t7['status'] == 'failed' and t7['error']['error_code'] == 'config')  # 硅基流动没填 Key
t7 = wf.process(EN, provider='openai', steps=['transcribe'])
check('换识别服务：英文用 OpenAI', t7['status'] == 'completed' and seen['asr_provider'] == 'openai' and docs.load(EN)['lang'] == 'en')
t8 = wf.process(EN, model='local', steps=['transcribe'], redo=['transcribe'])
check('本地模型：接口留着，明确报「还不支持」', t8['status'] == 'failed' and t8['error']['error_code'] == 'not_supported')
t9 = wf.process(EN)
de = docs.load(EN)
check('英文视频：自动翻译，中英同时显示', {s['name']: s['status'] for s in t9['stages']}['translate'] == 'completed'
      and all(s['tr'].startswith('中文：') for s in de['segments']) and config.load()['show_tr'] is True)

# ------------------------------------------------------------ 书 ----
book_calls = Counter()
book.organize_chapter = lambda s, n, path, info, c, meta, *a, **k: (book_calls.update(['org']), f'## 一、本章主题\n\n{c["title"]}（P1）')[1]
book.review_chapter = lambda s, n, info, c, md, meta, teacher=None: (md, 0, 0)
book.verify_doubts = lambda s, n, path, info, c, md, meta, teacher=None, vision=True: (md, 0)
book.overview = lambda s, n, meta, chs, files: (book_calls.update(['overview']), '## 全书总览\n\n两章。')[1]
tb = wf.process(BK)
db = docs.load(BK)
check('书：逐章整理 + 全书总览，不剪藏', tb['status'] == 'completed' and all(c['md'] for c in db['chapters']) and db['overview']
      and not db.get('clip') and book_calls['org'] == len(db['chapters']), tb)
tb2 = wf.process(BK)
check('书：再运行全部跳过', tb2['status'] == 'completed' and book_calls['org'] == len(db['chapters']) and book_calls['overview'] == 1)

# ------------------------------------------------------------ 保存冲突 ----
d = docs.load(L1)
rev = d['rev']
d2 = docs.load(L1)
d2['summary'] += '\n- Agent 后来加的'
docs.save(L1, d2, base_rev=rev)
d['summary'] = '界面里的旧版本'
r = docs.save(L1, d, base_rev=rev)
check('保存冲突：界面拿着旧版本，不覆盖 Agent 的结果', r.get('conflict') and 'Agent 后来加的' in docs.load(L1)['summary'])
api = Api()
r = api.call('save', {'path': L1, 'doc': d, 'base_rev': rev})
check('界面保存：返回 conflict 让界面问用户', r['ok'] and r.get('conflict'))
r = api.call('save', {'path': L1, 'doc': d, 'base_rev': rev, 'force': True})
check('用户选择覆盖：force 保存', r['ok'] and not r.get('conflict') and docs.load(L1)['summary'] == '界面里的旧版本' and r['rev'] == rev + 2)
r = api.call('save', {'path': L1, 'doc': d})
check('以前的调用方式（不带版本号）照样能保存', r['ok'] and not r.get('conflict'))

# ------------------------------------------------------------ 正在整理 ----
key = docs.media_key(L2)
tasks.claim(key, 'other-task')
t10 = wf.process(L2)
check('同一节课正在被别的任务整理：不重复处理', t10['status'] == 'failed' and t10['error']['error_code'] == 'busy')
r = api.call('open', {'path': L2})
check('界面打开正在整理的课：会提示', r['ok'] and r['busy'] and r['busy']['task_id'] == 'other-task')
tasks.release(key, 'other-task')
(tasks.BUSY_DIR / f'{key}.busy').write_text(json.dumps({'task_id': 'dead', 'pid': 999999, 'since': 'x'}), encoding='utf-8')
check('留下来的旧标记（程序已经不在了）不算', tasks.busy(key) is None and wf.process(L2)['status'] == 'completed')
r = api.call('open', {'path': L2})
check('整理完以后界面不再提示', r['ok'] and r['busy'] is None)

# 中途退出的任务
fake = {'task_id': '20000101-000000000-dead', 'status': 'running', 'pid': 999999, 'path': L2, 'stages': [{'name': 'summary', 'status': 'running'}]}
(tasks.TASKS_DIR / f'{fake["task_id"]}.json').write_text(json.dumps(fake), encoding='utf-8')
check('程序中途退出的任务：显示为失败（被打断），可以接着做', tasks.load(fake['task_id'])['status'] == 'failed'
      and tasks.load(fake['task_id'])['error']['error_code'] == 'interrupted')

# 取消
started = threading.Event()


def slow_stream(*a, **k):
    started.set()
    for _ in range(100):
        time.sleep(0.1)
        yield 'x'


llm.stream = slow_stream
holder = {}
th = threading.Thread(target=lambda: holder.setdefault('r', wf.run_step(L3, 'summary', force=True)))
th.start()
started.wait(5)
time.sleep(0.3)
tid = next(t['task_id'] for t in tasks.recent(5) if t['status'] == 'running')
check('取消：通知正在运行的任务', tasks.request_cancel(tid))
th.join(15)
check('取消：任务停下，状态 cancelled，课程重点没被改', holder['r']['status'] == 'cancelled' and docs.load(L3)['summary'] != 'x' * 100)
llm.stream = fake_stream


# ------------------------------------------------------------ 命令行 ----
def run_cli(*argv):
    buf, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, json.loads(buf.getvalue()) if '--json' in argv else buf.getvalue(), err.getvalue()


code, out, _ = run_cli('find', '--course', '峰级', '--index', '2', '--json')
check('命令行 find：JSON', code == 0 and out['success'] and out['count'] == 1 and out['items'][0]['path'] == L2)
code, out, _ = run_cli('info', wf.video_id(L1), '--json')
check('命令行 info：JSON', code == 0 and out['success'] and out['state']['segments'] == 4 and 'metadata' in out)
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', COURSE / '1、基础' / '4、第四课.mp4')
code, out, _ = run_cli('process', '--course', '峰级', '--todo', '--dry-run', '--json')
check('命令行 process --dry-run：只看不做', code == 0 and out['dry_run'] and [Path(x['path']).name for x in out['plan']] == ['4、第四课.mp4'])
c3 = calls.copy()
code, out, err = run_cli('process', '--course', '峰级', '--todo', '--json')
r0 = out['results'][0] if out.get('results') else {}
check('命令行 process：整理今天新增的、还没整理的', code == 0 and out['success'] and out['count'] == 1 and r0['success']
      and calls['asr'] == c3['asr'] + 1, out)
check('JSON 结构：success / task_id / stage / error_code / retryable / stages / state',
      {'success', 'task_id', 'video_id', 'stage', 'error_code', 'message', 'retryable', 'stages', 'state', 'warnings'} <= set(r0)
      and {'name', 'label', 'status', 'retries', 'model', 'result'} <= set(r0['stages'][0]))
check('进度写在 stderr（不混进 JSON）', '生成字幕' in err or '%' in err)
code, out, _ = run_cli('process', str(OUTSIDE), '--json')
check('命令行：失败时也是结构化 JSON', code == 1 and out['success'] is False and out['error_code'] == 'not_in_library' and out['retryable'] is False)
code, out, _ = run_cli('process', L1, '--force', '--json')
check('命令行：--force 必须写明重做哪几步（防止重复花钱）', code == 1 and out['error_code'] == 'not_supported')
code, out, _ = run_cli('run', 'nosuchstep', L1, '--json')
check('命令行：不认识的步骤', code == 1 and not out['success'] and 'nosuchstep' in json.dumps(out, ensure_ascii=False))
code, out, _ = run_cli('tasks', '--limit', '3', '--json')
check('命令行 tasks', code == 0 and len(out['tasks']) == 3 and out['tasks'][0]['task_id'])
code, out, _ = run_cli('task', out['tasks'][0]['task_id'], '--json')
check('命令行 task：完整记录', code == 0 and out['record']['stages'])
code, out, _ = run_cli('steps', '--json')
check('命令行 steps', code == 0 and [s['name'] for s in out['video']][:2] == ['transcribe', 'correct'])
code, text, _ = run_cli('find', '第一课')
check('命令行：不加 --json 输出中文', code == 0 and '第一课' in text and '已整理' in text)
check('转存检测：C 盘路径换成 \\\\localhost\\C$', cli.unc_of(r'C:\Users\x\AppData\Roaming') == r'\\localhost\C$\Users\x\AppData\Roaming')

# 真的起一个进程（不用假的 AI，只查找）
env = dict(os.environ)
r = subprocess.run([sys.executable, str(ROOT / 'app' / 'cli.py'), 'list', '--kind', 'book', '--json'], capture_output=True, env=env, timeout=120)
outp = json.loads(r.stdout.decode('utf-8'))
check('命令行进程：退出码 0、stdout 是 UTF-8 的 JSON', r.returncode == 0 and outp['count'] == 1 and outp['items'][0]['name'] == '《测试书籍》测试作者')
r = subprocess.run(['cmd', '/c', str(ROOT / 'mengren.cmd'), 'steps', '--json'], capture_output=True, env=env, timeout=120)
check('mengren.cmd 能直接调用', r.returncode == 0 and json.loads(r.stdout.decode('utf-8'))['success'], r.stderr.decode('utf-8', 'replace')[-300:])

# ------------------------------------------------------------ 收尾 ----
r = subprocess.run([sys.executable, '-m', 'pyflakes', str(ROOT / 'app' / 'cli.py'), str(ROOT / 'app' / 'core' / 'workflow.py'),
                    str(ROOT / 'app' / 'core' / 'tasks.py')], capture_output=True, text=True)
check('pyflakes：没有用了没定义的变量', r.returncode == 0, r.stdout)
print(f'\n全部通过：{passed} 项')
import logging  # noqa: E402
logging.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
