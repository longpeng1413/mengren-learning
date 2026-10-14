"""各平台常见视频 / 音频格式的播放测试（2026-10-14）：生成 3 秒的样本 → 按 media.play_plan 处理（直接放 / 只换外壳 / 重新编码）
→ 在和软件一样的内置播放器（pywebview + Edge WebView2）里实际播放，检查有画面、有声音、能拖动。
会弹出一个小窗口，约 1～2 分钟。运行：.venv\\Scripts\\python tests\\test_playback.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _old in Path(tempfile.gettempdir()).glob('mengren-play-test-*'):
    shutil.rmtree(_old, ignore_errors=True)
TMP = Path(tempfile.mkdtemp(prefix='mengren-play-test-'))
os.environ['APPDATA'] = str(TMP / 'appdata')
os.environ['LOCALAPPDATA'] = str(TMP / 'local')
(TMP / 'appdata' / '猛人学习').mkdir(parents=True)
(TMP / 'appdata' / '猛人学习' / 'settings.json').write_text(json.dumps({'storage_dir': str(TMP / 'store')}), encoding='utf-8')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from core import media  # noqa: E402

SAMPLES = TMP / 'samples'
SAMPLES.mkdir()
V = ['-f', 'lavfi', '-i', 'testsrc2=size=320x240:rate=25:duration=3']
A = ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100:duration=3']
VC = {'h264': ['-c:v', 'libx264', '-pix_fmt', 'yuv420p'], 'hevc': ['-c:v', 'libx265', '-pix_fmt', 'yuv420p', '-tag:v', 'hvc1'],
      'av1': ['-c:v', 'libaom-av1', '-cpu-used', '8', '-crf', '40', '-b:v', '0'], 'vp9': ['-c:v', 'libvpx-vp9', '-deadline', 'realtime'],
      'vp8': ['-c:v', 'libvpx'], 'xvid': ['-c:v', 'libxvid'], 'mpeg4': ['-c:v', 'mpeg4'], 'wmv2': ['-c:v', 'wmv2'], 'flv1': ['-c:v', 'flv'],
      'mpeg2': ['-c:v', 'mpeg2video'], 'mpeg1': ['-c:v', 'mpeg1video'], 'h263': ['-c:v', 'h263', '-s', '352x288'],
      'mjpeg': ['-c:v', 'mjpeg', '-pix_fmt', 'yuvj420p'], 'prores': ['-c:v', 'prores_ks'], 'rv20': ['-c:v', 'rv20'], 'theora': ['-c:v', 'libtheora']}
AC = {'aac': ['-c:a', 'aac'], 'mp3': ['-c:a', 'libmp3lame'], 'opus': ['-c:a', 'libopus', '-ar', '48000'], 'vorbis': ['-c:a', 'libvorbis'],
      'flac': ['-c:a', 'flac'], 'ac3': ['-c:a', 'ac3'], 'eac3': ['-c:a', 'eac3'], 'pcm': ['-c:a', 'pcm_s16le'], 'wmav2': ['-c:a', 'wmav2'],
      'mp2': ['-c:a', 'mp2'], 'amr': ['-c:a', 'libopencore_amrnb', '-ar', '8000', '-ac', '1'], 'alac': ['-c:a', 'alac'],
      'real': ['-c:a', 'real_144', '-ar', '8000', '-ac', '1']}
# （文件名, ffmpeg 容器, 画面, 声音, 期望的处理方式, 常见来源）
CASES = [
    ('mp4_h264_aac.mp4', 'mp4', 'h264', 'aac', 'direct', '抖音、B站、网课、录屏'),
    ('mp4_hevc_aac.mp4', 'mp4', 'hevc', 'aac', 'transcode', 'B站高画质、抖音、视频号、苹果手机'),
    ('mp4_av1_aac.mp4', 'mp4', 'av1', 'aac', 'direct', 'B站 AV1'),
    ('mp4_av1_opus.mp4', 'mp4', 'av1', 'opus', 'direct', 'YouTube'),
    ('mp4_vp9_opus.mp4', 'mp4', 'vp9', 'opus', 'direct', 'YouTube'),
    ('mp4_h264_mp3.mp4', 'mp4', 'h264', 'mp3', 'direct', '下载器'),
    ('mp4_h264_opus.mp4', 'mp4', 'h264', 'opus', 'direct', '下载器'),
    ('mp4_h264_ac3.mp4', 'mp4', 'h264', 'ac3', 'remux', '电影资源（只转声音）'),
    ('mp4_h264_eac3.mp4', 'mp4', 'h264', 'eac3', 'remux', '流媒体资源（只转声音）'),
    ('mp4_mpeg4_aac.mp4', 'mp4', 'mpeg4', 'aac', 'transcode', '老格式'),
    ('mov_h264_aac.mov', 'mov', 'h264', 'aac', 'direct', '苹果设备'),
    ('mov_hevc_aac.mov', 'mov', 'hevc', 'aac', 'transcode', '苹果手机录像'),
    ('mov_prores_pcm.mov', 'mov', 'prores', 'pcm', 'transcode', '剪辑软件导出'),
    ('m4v_h264_aac.m4v', 'mp4', 'h264', 'aac', 'direct', 'iTunes'),
    ('f4v_h264_aac.f4v', 'mp4', 'h264', 'aac', 'direct', '老网页视频'),
    ('webm_vp9_opus.webm', 'webm', 'vp9', 'opus', 'direct', 'YouTube'),
    ('webm_vp8_vorbis.webm', 'webm', 'vp8', 'vorbis', 'direct', '老 WebM'),
    ('webm_av1_opus.webm', 'webm', 'av1', 'opus', 'direct', 'YouTube'),
    ('mkv_av1_opus.mkv', 'matroska', 'av1', 'opus', 'direct', 'YouTube（yt-dlp 默认）'),
    ('mkv_vp9_opus.mkv', 'matroska', 'vp9', 'opus', 'direct', 'YouTube'),
    ('mkv_h264_aac.mkv', 'matroska', 'h264', 'aac', 'direct', 'OBS 录屏、网盘资源'),
    ('mkv_hevc_aac.mkv', 'matroska', 'hevc', 'aac', 'transcode', '网盘高清资源'),
    ('mkv_h264_ac3.mkv', 'matroska', 'h264', 'ac3', 'remux', '电影资源（只转声音）'),
    ('mkv_h264_flac.mkv', 'matroska', 'h264', 'flac', 'direct', '网盘资源'),
    ('mkv_h264_opus.mkv', 'matroska', 'h264', 'opus', 'direct', 'OBS'),
    ('mkv_h264_mp3.mkv', 'matroska', 'h264', 'mp3', 'direct', '网盘资源'),
    ('flv_h264_aac.flv', 'flv', 'h264', 'aac', 'remux', 'B站老视频、直播录制、OBS'),
    ('flv_flv1_mp3.flv', 'flv', 'flv1', 'mp3', 'transcode', '很老的网页视频'),
    ('ts_h264_aac.ts', 'mpegts', 'h264', 'aac', 'remux', '小鹅通等网课（m3u8）、直播回放'),
    ('ts_hevc_aac.ts', 'mpegts', 'hevc', 'aac', 'transcode', '直播回放'),
    ('m2ts_h264_ac3.m2ts', 'mpegts', 'h264', 'ac3', 'remux', '摄像机'),
    ('mts_h264_ac3.mts', 'mpegts', 'h264', 'ac3', 'remux', '摄像机'),
    ('avi_xvid_mp3.avi', 'avi', 'xvid', 'mp3', 'transcode', '网盘老资源'),
    ('avi_h264_mp3.avi', 'avi', 'h264', 'mp3', 'remux', '网盘资源'),
    ('avi_mjpeg_pcm.avi', 'avi', 'mjpeg', 'pcm', 'transcode', '老相机'),
    ('wmv_wmv2_wmav2.wmv', 'asf', 'wmv2', 'wmav2', 'transcode', '老 Windows 视频'),
    ('asf_wmv2_wmav2.asf', 'asf', 'wmv2', 'wmav2', 'transcode', '老 Windows 视频'),
    ('3gp_h263_amr.3gp', '3gp', 'h263', 'amr', 'transcode', '老手机'),
    ('mpg_mpeg2_mp2.mpg', 'mpeg', 'mpeg2', 'mp2', 'transcode', 'DVD、电视录制'),
    ('mpeg_mpeg1_mp2.mpeg', 'mpeg', 'mpeg1', 'mp2', 'transcode', 'VCD'),
    ('vob_mpeg2_ac3.vob', 'vob', 'mpeg2', 'ac3', 'transcode', 'DVD'),
    ('rmvb_rv20_real.rmvb', 'rm', 'rv20', 'real', 'transcode', '网盘老资源（RMVB）'),
    ('ogv_theora_vorbis.ogv', 'ogg', 'theora', 'vorbis', 'transcode', '少见'),
    ('a_mp3.mp3', 'mp3', None, 'mp3', 'direct', '音频'),
    ('a_aac.m4a', 'ipod', None, 'aac', 'direct', '音频'),
    ('a_aac.m4b', 'ipod', None, 'aac', 'direct', '有声书'),
    ('a_adts.aac', 'adts', None, 'aac', 'direct', '音频'),
    ('a_wav.wav', 'wav', None, 'pcm', 'direct', '录音'),
    ('a_flac.flac', 'flac', None, 'flac', 'direct', '无损音频'),
    ('a_vorbis.ogg', 'ogg', None, 'vorbis', 'direct', '音频'),
    ('a_opus.opus', 'opus', None, 'opus', 'direct', '音频'),
    ('a_opus.mka', 'matroska', None, 'opus', 'direct', '音频'),
    ('a_wma.wma', 'asf', None, 'wmav2', 'remux', '老 Windows 音频'),
    ('a_amr.amr', 'amr', None, 'amr', 'remux', '手机录音'),
    ('a_ac3.ac3', 'ac3', None, 'ac3', 'remux', '音频'),
    ('a_alac.m4a', 'ipod', None, 'alac', 'remux', '苹果无损'),
]
encoders = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True).stdout
passed, skipped, failed = 0, [], []


def check(name, cond, detail=''):
    global passed
    if cond:
        passed += 1
        print('✓', name)
    else:
        failed.append(name)
        print('✗', name, detail)


# 1. 生成样本、按规则处理
todo = []
for name, fmt, vc, ac, want, src in CASES:
    enc_names = [x for x in (VC.get(vc, [None, None])[1] if vc else None, AC[ac][1]) if x]
    if any(f' {e} ' not in encoders for e in enc_names):
        skipped.append(name)
        continue
    out = SAMPLES / name
    cmd = ['ffmpeg', '-v', 'error', '-y'] + (V if vc else []) + A + (VC[vc] if vc else []) + AC[ac] + ['-shortest', '-f', fmt, str(out)]
    if subprocess.run(cmd, capture_output=True).returncode:
        skipped.append(name)
        continue
    assert name.split('.')[-1] and Path(name).suffix.lower() in media.MEDIA_EXT, name
    info = media.probe(str(out))
    plan = media.play_plan(str(out), info)
    check(f'{name}（{src}）：处理方式是「{ {"direct": "直接放", "remux": "只换外壳", "transcode": "重新编码"}[want] }」', plan == want, plan)
    t0 = time.time()
    play = out if plan == 'direct' else media.make_playable(str(out), info, plan)
    todo.append((name, vc, str(play), plan, round(time.time() - t0, 2)))

# 2. 在内置播放器里实际播放
import webview  # noqa: E402

html = TMP / 'probe.html'
html.write_text('''<html><body><video id="v" muted playsinline></video><script>
window.testOne = async function (src) {
  const v = document.getElementById('v'), r = {};
  const wait = ms => new Promise(f => setTimeout(f, ms));
  const ev = (name, ms) => new Promise(res => { let done = false; const h = () => { if (!done) { done = true; res(true); } };
    v.addEventListener(name, h, { once: true }); setTimeout(() => { if (!done) { done = true; res(false); } }, ms); });
  v.removeAttribute('src'); v.load();
  const meta = ev('loadedmetadata', 5000), err = ev('error', 5000);
  v.src = src;
  r.meta = await Promise.race([meta.then(x => x ? 'meta' : 'timeout'), err.then(x => x ? 'error' : 'timeout')]);
  if (r.meta !== 'meta') { r.err = v.error && v.error.code; return r; }
  r.w = v.videoWidth;
  try { await v.play(); } catch (e) { r.playErr = String(e); }
  await wait(1000);
  r.t1 = v.currentTime; r.aBytes = v.webkitAudioDecodedByteCount || 0;
  v.pause(); v.currentTime = 2.0; await wait(600);
  r.t2 = v.currentTime; r.rs = v.readyState; r.err = v.error && v.error.code;
  return r;
};</script></body></html>''', encoding='utf-8')
results = {}


def run(win):
    time.sleep(1.5)
    for name, vc, play, plan, secs in todo:
        win.evaluate_js(f'window.__r = ""; testOne({json.dumps(Path(play).as_uri())}).then(r => window.__r = JSON.stringify(r))')
        raw = ''
        for _ in range(100):
            time.sleep(0.1)
            raw = win.evaluate_js('window.__r || ""')
            if raw:
                break
        results[name] = json.loads(raw) if raw else {'meta': 'hang'}
    win.destroy()


webview.start(run, webview.create_window('播放测试', html.as_uri(), width=420, height=320))
for name, vc, play, plan, secs in todo:
    r = results.get(name, {})
    pic = vc is None or (r.get('w') or 0) > 0
    ok = (r.get('meta') == 'meta' and pic and (r.get('t1') or 0) > 0.4 and (r.get('aBytes') or 0) > 0
          and abs((r.get('t2') or 0) - 2.0) < 0.3 and (r.get('rs') or 0) >= 2 and not r.get('err'))
    check(f'{name}：内置播放器里有画面、有声音、能拖动（{ {"direct": "直接放", "remux": "换外壳", "transcode": "重新编码"}[plan] }'
          + (f'，处理用了 {secs} 秒' if plan != 'direct' else '') + '）', ok, r)

print(f'\n通过 {passed} 项' + (f'，失败 {len(failed)} 项' if failed else '') + (f'（这台电脑的 ffmpeg 生成不了 {len(skipped)} 个样本，跳过：{skipped}）' if skipped else ''))
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failed else 0)
