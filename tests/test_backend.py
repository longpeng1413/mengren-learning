"""后台自测（不联网、不需要 API Key）。运行：.venv\\Scripts\\python tests\\test_backend.py
会把设置和缓存放到临时目录，不影响真实数据。"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix='mengren-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
ROOT = Path(__file__).resolve().parents[1]
if not (ROOT / 'test-media' / '旧格式.avi').exists():  # 测试视频不放进仓库，用 ffmpeg 生成
    import subprocess
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_media.py')], check=True)
(TMP / 'appdata' / '猛人字幕').mkdir(parents=True)  # 故意用改名前的位置，测试自动搬家
(TMP / 'appdata' / '猛人字幕' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
sys.path.insert(0, str(ROOT / 'app'))

from bridge import Api  # noqa: E402
from core import asr, config, doc as docs, jobs, media  # noqa: E402

MEDIA = TMP / 'media'
MEDIA.mkdir()
shutil.copy(ROOT / 'test-media' / '演示视频.mp4', MEDIA / '演示视频.mp4')
shutil.copy(ROOT / 'test-media' / '演示视频.mp4.猛人字幕.json', MEDIA / '演示视频.mp4.猛人字幕.json')
shutil.copy(ROOT / 'test-media' / '旧格式.avi', MEDIA / '旧格式.avi')
api = Api()
passed = 0


def check(name, cond, detail=''):
    global passed
    if not cond:
        print('✗', name, detail)
        raise SystemExit(1)
    passed += 1
    print('✓', name)


def wait(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = api.call('job', {'id': job_id})['job']
        if j['status'] != 'running':
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


# 改名：设置文件夹自动从 %APPDATA%\\猛人字幕 搬到 %APPDATA%\\猛人学习
check('改名后设置自动搬家', config.DATA_DIR.name == '猛人学习' and config.DATA_DIR.exists() and not (TMP / 'appdata' / '猛人字幕').exists()
      and config.load()['storage_dir'] == str(TMP / 'store'))

# 打开已有字幕的 mp4：直接播放，读回字幕
r = api.call('open', {'path': str(MEDIA / '演示视频.mp4')})
check('打开 mp4', r['ok'] and r['plan'] == 'direct' and r['src'].startswith('file:///'), r)
check('读回字幕', len(r['doc']['segments']) == 8 and r['doc']['lang'] == 'en')
check('初始进度为 0', r['position'] == 0)

# 保存修改 + 备份
d = r['doc']
d['segments'][0]['text'] = 'Edited line'
d['segments'].append({'start': 1.0, 'end': 2.0, 'text': '乱序插入', 'tr': ''})
s = api.call('save', {'path': r['path'], 'doc': d})
check('保存字幕（改名前的字幕文件存成新名字，旧文件去掉）', s['ok'] and s['stored'].endswith('.猛人学习.json') and not (MEDIA / '演示视频.mp4.猛人字幕.json').exists(), s)
again = api.call('open', {'path': r['path']})['doc']
check('保存后能读回修改', again['segments'][0]['text'] == 'Edited line')
check('保存时按时间排序', [x['start'] for x in again['segments']] == sorted(x['start'] for x in again['segments']))
check('留有上一版备份', Path(s['stored'] + '.bak').exists())

# 播放进度
api.call('position', {'path': r['path'], 't': 12.5})
check('记住播放进度', api.call('open', {'path': r['path']})['position'] == 12.5)

# avi（mpeg4 + mp3）需要转码
r2 = api.call('open', {'path': str(MEDIA / '旧格式.avi')})
check('avi 需要转换', r2['ok'] and r2['plan'] == 'transcode' and r2.get('convert_job'), r2)
j = wait(r2['convert_job'])
check('转换完成', j['status'] == 'done' and j['result']['src'].endswith('.mp4'), j)
r3 = api.call('open', {'path': str(MEDIA / '旧格式.avi')})
check('第二次打开直接用缓存', 'convert_job' not in r3 and r3['src'].endswith('.mp4'))

# 导出
out_txt = TMP / 'out.txt'
api.call('export', {'doc': again, 'dest': str(out_txt), 'format': 'txt', 'mode': 'both', 'timestamps': True})
txt = out_txt.read_text(encoding='utf-8-sig')
check('导出 TXT（双语+时间戳）', '[00:00] Edited line' in txt and '        欢迎来到交易心理学这一课' in txt and '今天我们聊聊' in txt, txt[:200])
out_srt = TMP / 'out.srt'
api.call('export', {'doc': again, 'dest': str(out_srt), 'format': 'srt', 'mode': 'tr'})
srt = out_srt.read_text(encoding='utf-8')
check('导出 SRT', srt.startswith('1\n00:00:00,500 --> ') and '今天我们聊聊' in srt, srt[:120])
plain = api.call('text', {'doc': again, 'format': 'txt', 'mode': 'src', 'timestamps': False})['text']
check('只要原文、不带时间戳', '[' not in plain and 'Edited line' in plain)

# 剪藏到 Obsidian（临时库）
vault = TMP / '库'
vault.mkdir()
api.call('settings', {'settings': {'obsidian': {'vault': str(vault), 'folder': '学习/猛人字幕'}}})
again['summary'] = '## 一句话概括\n测试总结 [00:05]'
c = api.call('clip', {'doc': again})
note = Path(c['path'])
check('剪藏生成笔记', c['ok'] and note.exists() and note.parent == vault / '学习' / '猛人字幕', c)
body = note.read_text(encoding='utf-8')
check('笔记：上半部分课程重点，下半部分完整字幕', body.index('## 课程重点') < body.index('## 完整字幕') and '[00:00] Edited line' in body and 'reviewed: false' in body)
check('obsidian 链接', c['uri'].startswith('obsidian://open?vault='))
again['clip'] = {'path': c['path']}
c2 = api.call('clip', {'doc': again})
check('再次剪藏更新同一篇笔记', c2['path'] == c['path'])

# 设置：密钥掩码与更新
api.call('settings', {'settings': {'providers': {'deepseek': {'key': 'sk-1234567890abcd'}}}})
pub = api.call('settings', {})['settings']
check('密钥只显示后四位', pub['providers']['deepseek']['key'] == '••••abcd' and pub['providers']['deepseek']['has_key'])
api.call('settings', {'settings': {'providers': {'deepseek': {'key': '••••abcd'}}}})
check('提交掩码不会覆盖密钥', config.load()['providers']['deepseek']['key'] == 'sk-1234567890abcd')
api.call('settings', {'settings': {'providers': {'deepseek': {'key': None}}}})
check('可以清除密钥', config.load()['providers']['deepseek']['key'] == '')
check('未填密钥时给出提示', '还没有填写' in api.call('transcribe', {'path': r['path'], 'lang': 'en', 'provider': 'openai'})['error'])

# 切段
mids = [10, 19.5, 31, 48, 61, 75]
ch = media.plan_chunks(mids, 80, 20, 40)
check('切段不超过最长长度', all(b - a <= 40 for a, b in ch) and ch[0][0] == 0 and ch[-1][1] == 80, ch)

# 豆包流式协议：编码、解码往返
frame = asr._frame(0x9, 0x1, 0x1, json.dumps({'result': {'text': '你好'}}).encode())
raw = frame[:4] + b'\x00\x00\x00\x01' + frame[4:]  # 服务端回包多 4 字节序号
kind, flags, data = asr._parse_frame(raw)
check('流式协议解析', kind == 'response' and data['result']['text'] == '你好')
text, segs = asr.doubao_utterances({'result': {'text': 'ab', 'utterances': [{'start_time': 1000, 'end_time': 2500, 'text': '你好。'}]}})
check('豆包分句时间', segs == [{'start': 1.0, 'end': 2.5, 'text': '你好。'}])
cues = asr.cues_from('第一句。第二句很长很长。', None, 10, 20)
check('无时间戳时按句长分配', len(cues) == 2 and cues[0]['start'] == 10 and abs(cues[-1]['end'] - 20) < 0.01)

# 抽音频 + 静音检测
wav = media.extract_wav(str(MEDIA / '演示视频.mp4'), 40)
check('抽出 16k 单声道音频', wav.exists() and wav.stat().st_size > 1_000_000)

# 老师与术语表
from core import codex_auth, library, llm  # noqa: E402
t = api.call('teacher_save', {'teacher': {'name': '大聪老师', 'terms': '底层逻辑\n峰级, 破级\n风级, 分 => 峰级\n托破旋 =>! 拓扑学',
                                          'about': '期货交易', 'notes': '峰：行情推进的方向', 'lang': 'zh'}})['teacher']
check('保存老师（含术语说明）', t['id'] and t['notes'] and len(api.call('settings', {})['teachers']) == 1)
words, hints, forced = llm.parse_terms(t['terms'])
check('解析术语表', words == ['底层逻辑', '峰级', '破级', '拓扑学'] and hints == [('风级', '峰级'), ('分', '峰级')] and forced == [('托破旋', '拓扑学')], (words, hints, forced))
check('只直接替换标了 =>! 的', llm.apply_fixes('部分讲到托破旋', forced) == '部分讲到拓扑学')
check('参考对照和术语说明写进 AI 提示', '分 → 峰级' in llm.teacher_block(t) and '峰：行情推进的方向' in llm.teacher_block(t))
check('课程重点不套固定模板', '不要套用固定栏目' in llm.DEFAULT_TEMPLATE and '不补充老师没讲的知识' in llm.DEFAULT_TEMPLATE)
ctx_d, ctx_p, fx = asr.asr_context(t, '交易底层逻辑', 'zh')
check('豆包热词', json.loads(ctx_d)['hotwords'][0]['word'] == '底层逻辑')
check('Whisper 提示词', ctx_p.startswith('术语：底层逻辑') and '交易底层逻辑' in ctx_p, ctx_p)
check('老师信息写进 AI 提示', '底层逻辑、峰级、破级、拓扑学' in llm.system_prompt({'title': 'x', 'segments': []}, t))

# 剪藏到老师子文件夹
again['teacher'] = t['id']
again['clip'] = None
c3 = api.call('clip', {'doc': again})
check('没有剪藏记录时，按视频路径找到已有笔记并更新', c3['path'] == c['path'], c3)
check('笔记属性里有老师', 'teacher: "大聪老师"' in Path(c3['path']).read_text(encoding='utf-8'))
shutil.rmtree(vault / '学习')
c3b = api.call('clip', {'doc': again})
check('按老师分子文件夹', Path(c3b['path']).parent.name == '大聪老师', c3b)

# ---------------------------------------------------------------- 1.4 ----
# 关联 Obsidian 术语表文件
gloss = TMP / '课程术语表.md'
gloss.write_text('''# 课程术语表 V1.1

# 三、峰

正确写法：

> **峰**

# 十、破级

- **标准写法**：破级

# 三十七、已知ASR常见错误映射

| ASR常见识别 | 正确术语 |
| --- | --- |
| 风、分 | 峰 |
| 破极、破局 | 破级 |
| 止行、部分“止盈” | 可能为“指形”，必须结合上下文 |
| 冲门 | 葱门（仅限上下文明确指向师门群体） |

--hotwords "大葱老师,峰级线"
''', encoding='utf-8')
extracted = llm.terms_from_markdown(gloss.read_text(encoding='utf-8'))
w2, h2, f2 = llm.parse_terms(extracted)
check('从术语表文档读出术语', w2[:4] == ['峰', '破级', '大葱老师', '峰级线'] and not f2, (w2, extracted))
check('读出识别错误对照（只作参考）', ('冲门', '葱门（仅限上下文明确指向师门群体）') in h2 and ('分', '峰') in h2 and ('止行', '指形（可能为“指形”，必须结合上下文）') in h2, h2)
check('识别错误的说明不进热词', '葱门' in w2 and all('仅限' not in x for x in w2), w2)
t2 = api.call('teacher_save', {'teacher': {'name': '咖米大葱老师', 'display': '大葱老师', 'terms': '底层逻辑', 'terms_file': str(gloss)}})['teacher']
rt = config.get_teacher(t2['id'])
check('关联文件：术语合并、整份文件当术语说明', '底层逻辑' in rt['terms'] and '冲门 => 葱门' in rt['terms'] and '已知ASR常见错误映射' in rt['notes'])
check('原始档案不被改动', config.get_teacher(t2['id'], resolve=False)['terms'] == '底层逻辑')
check('Obsidian 里改了术语表，软件用最新的', (gloss.write_text(gloss.read_text(encoding='utf-8') + '\n# 九、新术语\n\n正确写法：**新词**\n', encoding='utf-8') or True)
      and '新词' in config.get_teacher(t2['id'])['terms'])
pv = api.call('terms_preview', {'path': str(gloss)})
check('术语表文件预览', pv['ok'] and pv['words'] >= 5 and pv['hints'] >= 4, pv)
gloss.unlink()
check('文件不见了也能继续用档案里的术语', config.get_teacher(t2['id'])['terms'] == '底层逻辑' and config.get_teacher(t2['id']).get('terms_file_error'))

# 课程重点要求：老师自述、课程安排、不加自己的归纳
for key in ('老师自述', '课程安排', '整理者注', '不要写「这说明', '不要写「图示中」'):
    check('课程重点要求：' + key, key in llm.DEFAULT_TEMPLATE)

# 覆盖率和查漏插入
segs = [{'start': i * 10.0, 'end': i * 10 + 9.0, 'text': f'第{i}句讲了一些内容，有知识意义的话。', 'tr': ''} for i in range(30)]
summ = '一句话说明。\n\n## 一、开头\n\n- 要点一 [00:00] [00:30]\n\n## 二、结尾\n\n- 要点二 [04:10]'
ratio, gaps = llm.coverage(segs, summ)
check('覆盖率：找出没有出处的大段', 0 < ratio < 1 and gaps and gaps[0][0] == 70.0, (ratio, [(a, b) for a, b, _ in gaps]))
new = llm.insert_additions(summ, [{'放在': '一、开头', '内容': '- 老师自述：教过两万多学生 [01:20]'}, {'放在': '不存在的小节', '内容': '课程安排：线下课讲 [03:00]'}])
check('查漏：补到对应小节末尾', new.index('教过两万多学生') < new.index('## 二、结尾') and new.index('要点一') < new.index('教过两万多学生'), new)
check('查漏：找不到小节的放到最后', new.rstrip().endswith('- 课程安排：线下课讲 [03:00]') and '## 其他补充' in new, new)
orig_complete = llm.complete
llm.complete = lambda *a, **k: json.dumps({'补充': [{'放在': '二、结尾', '内容': '- 漏掉的举例 [02:00]'}]}, ensure_ascii=False)
out, n_add, b4, af = llm.review_summary({}, 'codex', {'title': 't', 'segments': segs}, None, summ)
check('查漏流程：补完覆盖率变高', n_add == 1 and af > b4 and '漏掉的举例 [02:00]' in out, (n_add, b4, af))

# AI 校对：存疑句
llm.complete = lambda *a, **k: json.dumps({'改动': {'1': '峰级的入门'}, '存疑': {'2': '疑似识别错误，可能是「讲论」'}}, ensure_ascii=False)
fx2, dbt = llm.correct_batch({}, 'codex', [(1, '风级的入门'), (2, '这是一个关于峰级的讲论文')], 't', None)
check('AI 校对：改动和存疑分开返回', fx2 == {1: '峰级的入门'} and dbt == {2: '疑似识别错误，可能是「讲论」'}, (fx2, dbt))
llm.complete = lambda *a, **k: json.dumps({'改动': {'1': '峰级的入门'}})
check('AI 校对：兼容没有存疑的回答', llm.correct_batch({}, 'codex', [(1, '风级的入门')], 't', None) == ({1: '峰级的入门'}, {}))
llm.complete = orig_complete

# 来源情况、标题层级、剪藏
bd = docs.source_boundary({'duration': 1478.0, 'segments': [{'start': 1460.68, 'end': 1464.0, 'text': '特别是在大葱老师线下课啊，你也可以'}]})
check('来源情况：字幕提前结束、最后一句没说完', '最后约 14 秒' in bd and '没有说完' in bd, bd)
check('来源情况：完整的不报', docs.source_boundary({'duration': 10, 'segments': [{'start': 0, 'end': 9.5, 'text': '好。'}]}) == '')
sh = docs._shift_headings('# 课程重点：交易底层逻辑\n\n概述\n\n## 一、人事钱\n\n### 1. 钱\n')
check('课程重点去掉总标题、标题降一级', sh.startswith('概述') and '### 一、人事钱' in sh and '#### 1. 钱' in sh, sh)
shutil.rmtree(vault / '学习')
d14 = dict(again, teacher=t2['id'], clip=None, summary='# 课程重点：演示\n\n## 一、开头\n要点 [00:05]',
           doubts=[{'t': again['segments'][1]['start'], 'reason': '疑似识别错误'}])
api.call('settings', {'settings': {'obsidian': {'vault': str(vault), 'folder': '学习/猛人字幕', 'teacher_subfolder': True, 'name_pattern': '{title}（猛人字幕）'}}})
c4 = api.call('clip', {'doc': d14})
n4 = Path(c4['path'])
b4_ = n4.read_text(encoding='utf-8')
check('笔记文件名格式、老师称呼', n4.name == '演示视频（猛人字幕）.md' and n4.parent.name == '大葱老师' and 'teacher: "大葱老师"' in b4_, c4)
check('笔记：标题层级、状态、存疑句', '\n### 一、开头' in b4_ and '# 课程重点：演示' not in b4_ and 'status: "待审计"' in b4_ and '〔存疑：疑似识别错误〕' in b4_, b4_[:600])
moved = vault / '别处' / '改了名.md'
moved.parent.mkdir()
n4.replace(moved)
moved.write_text(moved.read_text(encoding='utf-8').replace('status: "待审计"', 'status: "已确认-资料"'), encoding='utf-8')
d14['clip'] = {'path': str(n4)}
c5 = api.call('clip', {'doc': d14})
check('笔记被移动改名后，再剪藏仍更新原笔记', Path(c5['path']) == moved and not n4.exists(), c5)
check('再剪藏保留笔记里的 status', 'status: "已确认-资料"' in moved.read_text(encoding='utf-8'))
api.call('teacher_delete', {'id': t2['id']})

# ---------------------------------------------------------------- 1.5 画面 ----
from core import cache, frames  # noqa: E402
blank, page2 = bytes(frames.W * frames.H), bytes([200]) * (frames.W * frames.H)
samples = [(i * 2.0, blank if i < 10 else page2) for i in range(20)]
ct = frames.candidate_times(samples, [{'start': 30.0, 'end': 31.0, 'text': '来，大家请看这里'}])
check('候选画面：每段一张、去掉重复、老师说请看时补一张', [w for _, w in ct] == ['定时', '老师说请看'] and ct[1][0] >= 32, ct)
mp4 = str(MEDIA / '演示视频.mp4')
g = frames.grab(mp4, 3.0)
check('截图', g.exists() and g.stat().st_size > 1000 and frames.grab(mp4, 3.0) == g)
check('截小图', frames.grab(mp4, 3.0, small=True).name.endswith('_s.jpg'))
fu = api.call('frame_url', {'path': mp4, 't': 3.0})
check('画面图片地址', fu['ok'] and fu['url'].startswith('file:///'), fu)
api.call('settings', {'settings': {'vision': 'deepseek'}})
check('画面识别选了 DeepSeek 时提示它不能看图', '不能看图' in api.call('frames_extract', {'path': mp4, 'segments': [], 'duration': 40})['error'])
fj = api.call('frames_extract', {'path': mp4, 'segments': [], 'duration': 40, 'no_ai': True})
jj = wait(fj['job'])
check('没有能看图的 AI 时只截候选画面', jj['status'] == 'done' and jj['result']['frames'] and not jj['result']['ai'], jj)
shots = [{'t': 5.0, 'note': '课件标题：交易心理学', 'caption': '欢迎来到', 'figure': True}, {'t': 20.0, 'note': '老师手画曲线'}]
nt = frames.notes_text(shots)
check('画面记录写成文字', '[00:05]（可插图） 课件标题：交易心理学　画面字幕：「欢迎来到」' in nt and '[00:20] 老师手画曲线' in nt, nt)
check('画面记录按时间段取', '老师手画曲线' not in frames.notes_text(shots, 0, 10))
sp = llm.system_prompt({'title': 'x', 'segments': [], 'frames': shots}, None)
check('画面记录写进 AI 提示', '===== 画面记录 =====' in sp and '课件标题：交易心理学' in sp)
check('没有画面记录时不写', '画面记录' not in llm.system_prompt({'title': 'x', 'segments': []}, None))
check('课程重点要求：有画面时怎么写、怎么插图', '![[画面 mm:ss]]' in llm.DEFAULT_TEMPLATE and '画面显示' in llm.DEFAULT_TEMPLATE)
seen = {}
llm.complete = lambda s_, n_, msgs, **k: seen.setdefault('u', msgs[-1]['content']) and json.dumps({'改动': {}, '存疑': {}})
llm.correct_all({}, 'codex', [(0, '甲'), (1, '乙')], 't', None, threading.Event(), lambda *a: None, lambda p: None, {0: 4.0, 1: 6.0}, shots + [{'t': 300.0, 'note': '很远的画面'}])
check('AI 校对参考同一时间段的画面记录', '课件标题：交易心理学' in seen['u'] and '很远的画面' not in seen['u'], seen.get('u', '')[:300])
llm.complete = orig_complete
shutil.rmtree(vault / '学习', ignore_errors=True)
d15 = dict(again, clip=None, teacher='', frames=shots, summary='## 一、开头\n- 要点 [00:05]\n![[画面 00:05]]\n- 再一个 [00:20]')
c6 = api.call('clip', {'doc': d15})
b6 = Path(c6['path']).read_text(encoding='utf-8')
img = Path(c6['path']).parent / 'image' / '演示视频__00-05.jpg'
check('剪藏：插图复制到笔记旁边的 image 文件夹', '![[image/演示视频__00-05.jpg]]' in b6 and img.exists() and img.stat().st_size > 1000, b6[:800])
check('剪藏：笔记最后附画面记录', b6.index('## 完整字幕') < b6.index('## 画面记录') and '**[00:05]** 课件标题：交易心理学' in b6)
check('剪藏：插图压缩得比原截图小', img.stat().st_size < frames.grab(mp4, 5.0).stat().st_size, (img.stat().st_size, frames.grab(mp4, 5.0).stat().st_size))
api.call('settings', {'settings': {'obsidian': dict(config.load()['obsidian'], include_frames=False)}})
check('可以不附画面记录', '## 画面记录' not in Path(api.call('clip', {'doc': dict(d15, clip={'path': c6['path']})})['path']).read_text(encoding='utf-8'))
check('插图非必要不用：最多 3 张', '最多 3 张' in llm.DEFAULT_TEMPLATE and '默认 false' in frames.DESCRIBE_PROMPT)
(config.CACHE_DIR / 'frames' / 'old').mkdir(parents=True, exist_ok=True)
oldf = config.CACHE_DIR / 'frames' / 'old' / 'x.jpg'
oldf.write_bytes(b'0')
os.utime(oldf, (time.time() - 40 * 86400, time.time() - 40 * 86400))
cache.cleanup()
check('30 天没用的截图自动清掉', not oldf.exists() and g.exists())

# ---------------------------------------------------------------- 1.6 书籍 ----
from core import book  # noqa: E402
import zipfile  # noqa: E402
if not (ROOT / 'test-media' / '测试书籍.pdf').exists():
    import subprocess
    subprocess.run([sys.executable, str(ROOT / 'tests' / 'make_test_book.py')], check=True)
BOOKS = TMP / 'books'
BOOKS.mkdir()
pdfp = BOOKS / '《测试书籍》测试作者.pdf'
shutil.copy(ROOT / 'test-media' / '测试书籍.pdf', pdfp)
info = book.read(str(pdfp))
check('PDF：读出每页文字', len(info['pages']) == 8 and '三段结构' in info['pages'][3]['text'])
check('PDF：认出书上的页码（PDF 第 3 页 = P1）', info['shift'] == 2 and book.page_ref(3, 2) == 'P1' and book.page_ref(1, 2) == 'PDF 第 1 页')
check('PDF：认出有图的页', info['pages'][3]['figure'] and not info['pages'][4]['figure'])
chs = book.detect_chapters(info)
check('PDF：按「第X章」分章，目录页不算', [(c['title'], c['start'], c['end']) for c in chs] == [('第一章 趋势的起点', 3, 5), ('第二章 仓位的分配', 6, 8)], chs)
check('页码范围写法', book.range_ref(3, 5, 2) == 'P1-P3' and book.range_ref(3, 5, None) == 'PDF 第 3-5 页')
nd = book.new_doc(str(pdfp), info)
check('书名、作者从文件名「《书名》作者」读出', nd['title'] == '测试书籍' and nd['author'] == '测试作者' and len(nd['chapters']) == 2 and nd['segments'] == [])
pg = book.render_page(str(pdfp), 4)
check('渲染页面图', pg.exists() and pg.stat().st_size > 5000)
check('引用到的页', book.cited_pages('……（P1-P2）……（PDF 第 8 页）', 2) == {3, 4, 8})
check('没被引用的页', [p['n'] for p in book.uncovered_pages(info, chs[0], '- 定义（P1）')] == [] and
      [p['n'] for p in book.uncovered_pages(dict(info, pages=[dict(p, text=p['text'] * 5) for p in info['pages']]), chs[0], '- 定义（P1）')] == [4, 5])
# Word
docx = BOOKS / '讲义.docx'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
paras = ''.join(f'<w:p><w:pPr><w:pStyle w:val="{st}"/></w:pPr><w:r><w:t>{t}</w:t></w:r></w:p>' if st else f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>'
                for st, t in [('Heading1', '第一章 开始'), ('', '正文一' * 50), ('Heading1', '第二章 继续'), ('', '正文二' * 50)])
table = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>模型</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>比例</w:t></w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:p><w:r><w:t>金字塔</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>40%</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
with zipfile.ZipFile(docx, 'w') as z:
    z.writestr('word/document.xml', f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{W}"><w:body>{paras}{table}</w:body></w:document>')
    z.writestr('docProps/core.xml', '<?xml version="1.0"?><cp:coreProperties xmlns:cp="x" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>讲义标题</dc:title><dc:creator>某作者</dc:creator></cp:coreProperties>')
dinfo = book.read(str(docx))
dch = book.detect_chapters(dinfo)
check('Word：按标题分章、表格转成 Markdown', [c['title'] for c in dch] == ['第一章 开始', '第二章 继续'] and '| 金字塔 | 40% |' in dinfo['pages'][-1]['text'] and dinfo['title'] == '讲义标题', (dch, dinfo['pages'][-1]['text'][-60:]))
txt = BOOKS / '文章.md'
txt.write_text('# 第一部分\n\n' + '甲' * 300 + '\n\n# 第二部分\n\n' + '乙' * 300, encoding='utf-8')
check('文本：按 # 标题分章', [c['title'] for c in book.detect_chapters(book.read(str(txt)))] == ['第一部分', '第二部分'])
# 打开、整理（AI 用假的）、剪藏
ob = api.call('open', {'path': str(pdfp)})
check('打开 PDF 进入书籍模式', ob['ok'] and ob['kind'] == 'book' and ob['viewer'].startswith('file:///') and ob['book']['pages'] == 8 and len(ob['doc']['chapters']) == 2, ob.get('error'))
api.call('settings', {'settings': {'chat': 'openai', 'providers': {'openai': {'key': 'sk-test-1234567890'}}}})
calls = []


def fake_complete(s_, n_, msgs, **k):
    text = json.dumps([m['content'] if isinstance(m['content'], str) else [p.get('text', '[图]') for p in m['content']] for m in msgs], ensure_ascii=False)
    calls.append(text)
    if '查漏员' in text:
        return json.dumps({'补充': []})
    return '## 一、本章主题\n\n讲趋势。（P1）\n\n## 十三、一句话概括\n\n三段。（P1-P3）\n\n![[原书图示 第4页 图1-1 三段结构 @0.1,0.3,0.9,0.6]]\n*原书图示｜图 1-1 三段结构（P2）*'


llm.complete = fake_complete
r_ = api.call('book_organize', {'path': str(pdfp), 'doc': ob['doc'], 'indexes': [0]})
jb = wait(r_['job'])
check('整理一章', jb['status'] == 'done' and jb['items'][0]['md'].startswith('## 一、本章主题'), jb)
check('有图表的页附上页面图、锚点写法正确', '[图]' in calls[0] and '锚点写作（P2）' in calls[0] and '【PDF 第 4 页' in calls[0])
check('整理要求：固定栏目、疑点三分法、非必要不插图', '## 十三、一句话概括' in book.DEFAULT_RULES and '疑点三分法' in book.DEFAULT_RULES and '每章最多 2 张' in book.DEFAULT_RULES)
rules = TMP / '规范.md'
rules.write_text('# 外部书籍与文档整理规范 V9\n只回答原书讲了什么。', encoding='utf-8')
check('关联知识库里的书籍整理规范', '规范 V9' in book._rules({'book_rules_file': str(rules)}) and '规范 V9' not in book._rules({}))
llm.complete = orig_complete
bdoc = ob['doc']
bdoc['chapters'][0]['md'] = jb['items'][0]['md']
bdoc['overview'] = '## 一、书籍基本信息\n\n测试。'
api.call('save', {'path': str(pdfp), 'doc': bdoc})
check('书籍整理结果存进旁边的文件', json.loads(docs.sidecar(str(pdfp)).read_text(encoding='utf-8'))['chapters'][0]['md'].startswith('## 一'))
api.call('settings', {'settings': {'obsidian': dict(config.load()['obsidian'], vault=str(vault), book_folder='资料/书籍')}})
bc = api.call('clip', {'doc': bdoc}) if False else api.call('book_clip', {'doc': bdoc})
bdir = vault / '资料' / '书籍' / '《测试书籍》测试作者'
ch1 = bdir / '01、第一章 趋势的起点.md'
check('剪藏书籍：总览 + 章节文件', bc['ok'] and (bdir / '00、全书总览.md').exists() and ch1.exists() and not (bdir / '02、第二章 仓位的分配.md').exists(), bc)
cb = ch1.read_text(encoding='utf-8')
check('章节笔记：属性、来源说明、页码范围', 'type: 书籍章节整理' in cb and 'page_range: "P1-P3"' in cb and '整理范围：P1-P3（PDF 第 3-5 页）' in cb and 'status: "待审计"' in cb, cb[:500])
figp = bdir / 'image' / '原书图示-图1-1-三段结构.jpg'
check('原书图示按位置裁剪、压缩', '![[image/原书图示-图1-1-三段结构.jpg]]' in cb and figp.exists() and figp.stat().st_size < 60000, cb[-300:])
from PIL import Image  # noqa: E402
with Image.open(figp) as im:
    check('裁剪后的图比整页小', im.width <= 960 and im.height < im.width, im.size)
ch1.write_text(cb.replace('status: "待审计"', 'status: "已确认-资料"'), encoding='utf-8')
api.call('book_clip', {'doc': bdoc})
check('再次剪藏书籍保留 status', 'status: "已确认-资料"' in ch1.read_text(encoding='utf-8'))
(BOOKS / '_关键帧缓存').mkdir()
shutil.copy(pdfp, BOOKS / '_关键帧缓存' / '视觉资料.pdf')
lb = api.call('library_add_folder', {'path': str(BOOKS)})
names = [i['name'] for i in lb['groups'][-1]['items']]
check('媒体库列出 PDF 和 Word、不列「_」开头的辅助文件夹', '《测试书籍》测试作者' in names and '讲义' in names and '视觉资料' not in names and '文章' not in names, names)
bi = next(i for i in lb['groups'][-1]['items'] if i['name'] == '《测试书籍》测试作者')
check('媒体库显示整理进度', bi.get('book') == {'done': 1, 'total': 2}, bi)
api.call('library_remove', {'kind': 'folder', 'path': str(BOOKS)})

# 媒体库
lib = api.call('library_add_folder', {'path': str(MEDIA)})
check('添加文件夹', lib['added'] == 2 and len(lib['groups'][0]['items']) == 2, lib)
item = next(i for i in lib['groups'][0]['items'] if i['name'] == '演示视频')
check('媒体库显示字幕和进度', item['subs'] == 9 and item['pos'] == 12.5, item)
api.call('library_set', {'path': str(MEDIA), 'teacher': t['id']})
check('文件夹绑定老师', library.folder_teacher(str(MEDIA / '旧格式.avi')) == t['id'])
check('打开时自动用文件夹的老师', api.call('open', {'path': str(MEDIA / '旧格式.avi')})['doc']['teacher'] == t['id'])
api.call('library_add_files', {'paths': [str(MEDIA / '演示视频.mp4')]})
check('添加单个文件', api.call('library', {})['groups'][-1]['type'] == 'files')
check('移除文件夹', len(api.call('library_remove', {'kind': 'folder', 'path': str(MEDIA)})['groups']) == 1)
api.call('teacher_delete', {'id': t['id']})
check('删除老师', api.call('settings', {})['teachers'] == [])

# ChatGPT 登录（不真正登录）
login = codex_auth._Login()
check('ChatGPT 登录网址', 'code_challenge_method=S256' in login.url and 'client_id=app_' in login.url)
check('拒绝不匹配的回调', login.accept('http://localhost:1455/auth/callback?code=x&state=wrong') and login.error)
api.call('settings', {'settings': {'chat': 'codex', 'providers': {'codex': {'chat_model': 'x'}}}})
check('选了 ChatGPT 但没登录时提示', '还没有用 ChatGPT 账号登录' in api.call('chat', {'doc': {}})['error'])
api.call('settings', {'settings': {'translate': 'claude'}})
check('Claude 没填密钥时提示', '还没有填写Claude的 API Key' in api.call('translate', {'items': [[0, 'hi']]})['error'])

# 缓存清理
from core import cache  # noqa: E402
import os  # noqa: E402,F811
(config.CACHE_DIR / 'audio').mkdir(parents=True, exist_ok=True)
old_wav = config.CACHE_DIR / 'audio' / 'old.wav'
old_wav.write_bytes(b'0' * 1000)
os.utime(old_wav, (time.time() - 5 * 86400, time.time() - 5 * 86400))
(config.CACHE_DIR / 'chunks-x').mkdir(exist_ok=True)
cache.cleanup()
check('自动清理 3 天前的音频和残留片段', not old_wav.exists() and not (config.CACHE_DIR / 'chunks-x').exists())
check('清空缓存', api.call('cache_clear', {})['cache'] == 0)

# 设置导出 / 导入
api.call('settings', {'settings': {'providers': {'deepseek': {'key': 'sk-export-test-123456'}}, 'theme': 'purple'}})
api.call('teacher_save', {'teacher': {'name': '导出测试老师', 'terms': '峰级'}})
exp = api.call('export_settings', {'dest': str(TMP / 'exp.json'), 'keys': True})
data = json.loads(Path(exp['path']).read_text(encoding='utf-8'))
check('导出设置（含密钥和老师）', data['settings']['providers']['deepseek']['key'] == 'sk-export-test-123456' and data['teachers'][0]['name'] == '导出测试老师')
exp2 = api.call('export_settings', {'dest': str(TMP / 'exp2.json'), 'keys': False})
check('导出时可以不带密钥', json.loads(Path(exp2['path']).read_text(encoding='utf-8'))['settings']['providers']['deepseek']['key'] == '')
config.save(config._merge(config.DEFAULTS, {}))           # 模拟新电脑
for t_ in config.load_teachers():
    config.delete_teacher(t_['id'])
imp = api.call('import_settings', {'path': str(TMP / 'exp.json')})
s_ = config.load()
check('导入设置', imp['imported'] and s_['providers']['deepseek']['key'] == 'sk-export-test-123456' and s_['theme'] == 'purple' and config.load_teachers()[0]['name'] == '导出测试老师')
api.call('import_settings', {'path': str(TMP / 'exp2.json')})
check('导入不带密钥的文件时保留已有密钥', config.load()['providers']['deepseek']['key'] == 'sk-export-test-123456')
(TMP / 'bad.json').write_text('{"a": 1}', encoding='utf-8')
check('拒绝不是猛人学习的文件', '不是猛人学习（猛人字幕）导出的设置文件' in api.call('import_settings', {'path': str(TMP / 'bad.json')})['error'])

# 缓存和日志位置
check('缓存和日志在指定位置，文件夹名写着可随时删除', config.CACHE_DIR == TMP / 'store' / '缓存（可随时删除）' and config.LOG_DIR.name == '日志（可随时删除）')
r_ = api.call('set_storage', {'path': str(TMP / 'store2')})
check('更改缓存位置', r_['changed'] and config.load()['storage_dir'] == str(TMP / 'store2'))

print(f'\n全部通过：{passed} 项')
shutil.rmtree(TMP, ignore_errors=True)
