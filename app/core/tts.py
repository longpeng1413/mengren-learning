"""朗读（2026-10-21，用户要求课程重点、AI 对话能边听边看）。

界面切句、高亮、播放，这里只负责把一句话变成声音（mp3），在线的两种要由后台去取（网页里直接请求会被跨域挡住）：
- Edge 在线朗读：微软 Edge「大声朗读」的自然声音（晓晓、云希……），用开源组件 edge-tts（LGPLv3），要联网，不用账号；
- 百度朗读：百度翻译网页的朗读声音，免费、不用注册，要联网，**正在读的句子会发给百度翻译**。不是百度正式开放的服务，
  百度改了接口就会失效（和用户的 Obsidian 插件「听书」用的是同一个地址）；
- 系统声音（Windows 自带的慧慧、康康、瑶瑶）在界面里直接用浏览器的朗读，不经过这里，不联网。
声音只在内存里传给界面，不写硬盘，不用清理。
"""
import asyncio
import re
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import requests

ENGINES = ('edge', 'baidu', 'system')
EDGE_VOICES = [('zh-CN-XiaoxiaoNeural', '晓晓（女，温和）'), ('zh-CN-XiaoyiNeural', '晓伊（女，活泼）'),
               ('zh-CN-YunxiNeural', '云希（男，年轻）'), ('zh-CN-YunjianNeural', '云健（男，沉稳）'),
               ('zh-CN-YunyangNeural', '云扬（男，播音）')]
# Edge 偶尔一句要十几秒（2026-10-21 实测 12 句里 11 句 1.4～3 秒、1 句 11 秒，用户听到「停了几秒」）：
# 超过这么久还没回来就再发一次同样的请求，谁先回来用谁
HEDGE_AFTER = 4
MAX_CHARS = 400  # 界面按句子送来，一句很少超过 80 字；防止误送整篇
# 视频时间引用不读：[59:10]、[1:00:29]、[01:42]-[02:23]（界面已经跳过了显示成链接的时间，这里再兜一次底）
TIME_RE = re.compile(r'\[\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?\s*\](?:\s*[-–—~～至到]\s*\[\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d+)?\s*\])?')


class TtsError(Exception):
    pass


def clean(text):
    """要读的文字：去掉中括号里的视频时间、多余空白。"""
    t = TIME_RE.sub('', str(text or ''))
    return re.sub(r'\s+', ' ', t).strip()[:MAX_CHARS]


def _proxies(settings):
    p = (settings or {}).get('proxy') or ''
    return {'http': p, 'https': p} if p else None


def speak(engine, text, voice='', settings=None):
    """一句话 → mp3 字节。先直连，连不上而且设置里填了代理，再用代理试一次。"""
    t = clean(text)
    if not t:
        raise TtsError('没有要读的文字')
    if engine not in ('edge', 'baidu'):
        raise TtsError('系统声音由界面直接朗读')
    routes = [None] + ([_proxies(settings)['https']] if _proxies(settings) else [])
    last = None
    for proxy in routes:
        try:
            return _hedged(_edge, t, voice, proxy) if engine == 'edge' else _baidu(t, proxy)
        except Exception as e:  # noqa: BLE001  换代理再试，都不行才报错
            last = e
    name = 'Edge 在线朗读' if engine == 'edge' else '百度朗读'
    raise TtsError(f'{name}连不上（{type(last).__name__}：{str(last)[:120]}）')


def _hedged(fn, *args):
    """先发一次；HEDGE_AFTER 秒还没回来（或已经失败）就再发一次，用先成功的那个；30 秒都没有就报错。"""
    ex = ThreadPoolExecutor(2)
    try:
        futs = [ex.submit(fn, *args)]
        done, _ = wait(futs, timeout=HEDGE_AFTER)
        if done and not futs[0].exception():
            return futs[0].result()
        last = futs[0].exception() if done else None
        if done:
            futs = []
        futs.append(ex.submit(fn, *args))
        end = time.time() + 30
        while futs:
            done, _ = wait(futs, timeout=max(0.1, end - time.time()), return_when=FIRST_COMPLETED)
            if not done:
                raise TtsError('等了 30 秒还没有声音')
            for f in done:
                futs.remove(f)
                if not f.exception():
                    return f.result()
                last = f.exception()
        raise last
    finally:
        ex.shutdown(wait=False)


def _edge(text, voice, proxy):
    import edge_tts
    voice = voice if voice in dict(EDGE_VOICES) else EDGE_VOICES[0][0]

    async def run():
        out = bytearray()
        async for ch in edge_tts.Communicate(text, voice, proxy=proxy).stream():
            if ch.get('type') == 'audio':
                out += ch['data']
        return bytes(out)
    data = asyncio.run(asyncio.wait_for(run(), 30))
    if not data:
        raise TtsError('Edge 在线朗读没有返回声音')
    return data


def _baidu(text, proxy):
    lan = 'zh' if re.search(r'[㐀-鿿]', text) else 'en'
    url = f'https://fanyi.baidu.com/gettts?lan={lan}&text={urllib.parse.quote(text)}&spd=5&source=web'
    r = requests.get(url, timeout=15, proxies={'http': proxy, 'https': proxy} if proxy else None,
                     headers={'User-Agent': 'Mozilla/5.0'})
    if r.status_code != 200 or not re.search(r'audio|mpeg', r.headers.get('content-type', ''), re.I) or not r.content:
        raise TtsError(f'百度朗读返回的不是声音（HTTP {r.status_code}，可能是百度改了接口）')
    return r.content
