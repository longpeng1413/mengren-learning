"""有道智云文本翻译（2026-11-11，截图编辑的「翻译」用；和猛人截图（D:\\mengren-desktop core/translate/youdao.py）同一个官方 v3 接口）。

设置在 providers.youdao：app_id = 应用ID（不是 APIKey），key = 应用密钥（和别的密钥一样，界面只显示后四位）。
有道在国内：默认直连；设置里勾了「走代理」才走代理。一次把几段一起发（批量接口 /v2/api），不支持批量时一段一段发。"""
import hashlib
import time
import uuid

import requests

ENDPOINT = 'https://openapi.youdao.com/api'
BATCH_ENDPOINT = 'https://openapi.youdao.com/v2/api'

ERRORS = {
    '101': '有道翻译请求缺少参数',
    '104': '这个有道应用不支持批量翻译',
    '108': '有道应用ID 不对：要填「应用ID」，不是 APIKey',
    '110': '这个有道应用还没开通「文本翻译」服务',
    '112': '有道翻译服务暂时不可用',
    '202': '有道签名校验失败：请检查应用密钥',
    '205': '有道应用的接入方式不对：创建应用时要选「API」',
    '401': '有道账户欠费了，请到有道智云充值',
    '411': '有道翻译访问太频繁，过一会儿再试',
}


class YoudaoError(ValueError):
    pass


class _NoBatch(YoudaoError):
    pass


def configured(settings) -> bool:
    p = (settings.get('providers') or {}).get('youdao') or {}
    return bool(p.get('app_id') and p.get('key'))


def _sign(app_id, secret, q, salt, curtime):
    q = q if len(q) <= 20 else f'{q[:10]}{len(q)}{q[-10:]}'
    return hashlib.sha256((app_id + q + salt + curtime + secret).encode('utf-8')).hexdigest()


def _post(settings, url, data):
    p = settings['providers']['youdao']
    proxies = {'http': settings['proxy'], 'https': settings['proxy']} if p.get('use_proxy') and settings.get('proxy') else None
    last = ''
    for attempt in range(3):
        try:
            # 没勾代理时连系统代理也不用（trust_env=False）：有道在国内，直连最快，走代理反而可能被拦
            with requests.Session() as sess:
                sess.trust_env = bool(proxies)
                r = sess.post(url, data=data, timeout=20, proxies=proxies)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = str(e)[:200]
            time.sleep(0.8 * (attempt + 1))
    raise YoudaoError('连接有道翻译失败：' + last)


def _check(body):
    code = str(body.get('errorCode', ''))
    if code != '0':
        if code in ('104', '110', '112'):
            raise _NoBatch(ERRORS.get(code, code))
        raise YoudaoError(ERRORS.get(code, f'有道翻译失败（错误码 {code}）'))


def translate_many(settings, texts, source='auto', target='zh-CHS'):
    """几段文字 → 中文（顺序对应）。"""
    p = settings['providers']['youdao']
    if not configured(settings):
        raise YoudaoError('请先在设置「有道翻译」里填应用ID 和应用密钥')
    texts = [t.strip() for t in texts]
    if not any(texts):
        return ['' for _ in texts]
    idx = [i for i, t in enumerate(texts) if t]
    qs = [texts[i] for i in idx]

    def fields(sign_q):
        salt, cur = uuid.uuid4().hex, str(int(time.time()))
        return [('from', source), ('to', target), ('appKey', p['app_id']), ('salt', salt), ('signType', 'v3'),
                ('curtime', cur), ('sign', _sign(p['app_id'], p['key'], sign_q, salt, cur))]

    out = ['' for _ in texts]
    try:
        body = _post(settings, BATCH_ENDPOINT, fields(''.join(qs)) + [('q', q) for q in qs])
        _check(body)
        res = body.get('translateResults') or []
        if len(res) != len(qs):
            raise _NoBatch('批量翻译返回不完整')
        for i, x in zip(idx, res):
            out[i] = str(x.get('translation') or '').strip()
        return out
    except _NoBatch:
        pass
    for k, (i, q) in enumerate(zip(idx, qs)):  # 不支持批量：一段一段翻
        if k:
            time.sleep(0.3)
        body = _post(settings, ENDPOINT, fields(q) + [('q', q)])
        _check(body)
        out[i] = '\n'.join(str(x) for x in body.get('translation') or []).strip()
    return out
