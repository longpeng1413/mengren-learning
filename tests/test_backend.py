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
(TMP / 'appdata' / '猛人字幕').mkdir(parents=True)
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
check('保存字幕', s['ok'] and s['stored'].endswith('.猛人字幕.json'))
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
check('按老师分子文件夹', Path(c3['path']).parent.name == '大聪老师', c3)
check('笔记属性里有老师', 'teacher: "大聪老师"' in Path(c3['path']).read_text(encoding='utf-8'))

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
check('拒绝不是猛人字幕的文件', '不是猛人字幕导出的设置文件' in api.call('import_settings', {'path': str(TMP / 'bad.json')})['error'])

# 缓存和日志位置
check('缓存和日志在指定位置，文件夹名写着可随时删除', config.CACHE_DIR == TMP / 'store' / '缓存（可随时删除）' and config.LOG_DIR.name == '日志（可随时删除）')
r_ = api.call('set_storage', {'path': str(TMP / 'store2')})
check('更改缓存位置', r_['changed'] and config.load()['storage_dir'] == str(TMP / 'store2'))

print(f'\n全部通过：{passed} 项')
shutil.rmtree(TMP, ignore_errors=True)
