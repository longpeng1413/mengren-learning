"""官方「用 ChatGPT 登录」（Sign in with ChatGPT，开源应用直连；2026-11-10 起推荐，旧的 codex_auth 留作备用）。

做法照 OpenAI 官方说明（developers.openai.com/siwc/token-sharing-open-source）和猛人输入法
（D:\\猛人输入法开发\\crates\\qingjian-predict\\src\\subscription\\）：
- 第一次登录：client_id=dynamic_agent_client 动态注册，agent_name_hint=猛人学习，ext_agent_host_id=这台电脑的固定编号；
  回调里拿到 OpenAI 发给「猛人学习」的应用编号（oaiapp_…），以后换令牌、续期、再登录都用它；
- 回调地址只能是 http://127.0.0.1:端口/auth/callback（不能写 localhost），同一次登录前后要一致；
- 令牌里必须有 chatgpt.tokens.use.direct（使用会员额度）；
- 推理发到 https://api.openai.com/v1/responses（不能发到 ChatGPT 的 backend-api），store=false、stream=true；
- 访问令牌 1 小时，续期令牌 30 天、每次续期换新的：同一份登录同时只能一个程序续期（界面和命令行可能同时在跑），
  所以续期在跨进程的文件锁里做，锁里先读最新的设置文件。
2026-11-09 实测：登录、模型列表、调用、思考强度、返回用量、注销都正常。Plus 账号的 5 小时额度和 Codex 是同一份。"""
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import config

AUTHORIZE = 'https://auth.openai.com/api/accounts/authorize'
TOKEN = 'https://auth.openai.com/api/accounts/oauth/token'
REVOKE = 'https://auth.openai.com/api/accounts/oauth/revoke'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'
USE_SCOPE = 'chatgpt.tokens.use.direct'
APP_NAME = '猛人学习'
MARGIN = 300  # 提前 5 分钟续期


class AuthError(Exception):
    pass


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip('=')


def _claims(jwt: str) -> dict:
    try:
        part = jwt.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (IndexError, ValueError, AttributeError):
        return {}


def _proxies():
    s = config.load()
    return {'http': s['proxy'], 'https': s['proxy']} if s['providers']['chatgpt'].get('use_proxy') and s.get('proxy') else None


def host_id() -> str:
    """这台电脑的固定编号（官方要求同一台电脑每次登录都用同一个）。第一次用时生成并存进设置。"""
    p = config.load()['providers']['chatgpt']
    if p.get('host_id'):
        return p['host_id']
    hid = 'urn:uuid:' + str(uuid.uuid4())
    config.update({'providers': {'chatgpt': {'host_id': hid}}})
    return hid


@contextmanager
def _refresh_lock():
    """跨进程的续期锁（界面、命令行同时用时排队）。"""
    d = config.DATA_DIR / 'locks'
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / 'chatgpt-login.lock', 'a+b')
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            locked = True
        yield
    finally:
        if locked:
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def _creds(tok: dict, base: dict, client_id: str) -> dict:
    scopes = str(tok.get('scope') or '').split()
    if USE_SCOPE not in scopes:
        raise AuthError('这次登录没有拿到「使用 ChatGPT 会员额度」的权限（可能是这个账号或套餐不支持），请换一个账号或用旧登录方式')
    idc = _claims(tok.get('id_token', '')) if tok.get('id_token') else {}
    auth = idc.get('https://api.openai.com/auth', {}) or {}
    return {
        'client_id': client_id,
        'access': tok['access_token'],
        'refresh': tok.get('refresh_token') or base.get('refresh', ''),
        'id_token': tok.get('id_token') or base.get('id_token', ''),
        'expires': time.time() + float(tok.get('expires_in') or 3600),
        'scope': ' '.join(scopes),
        'email': idc.get('email') or base.get('email', ''),
        'plan': auth.get('chatgpt_plan_type') or base.get('plan', ''),
    }


def _post_token(data: dict) -> dict:
    try:
        r = requests.post(TOKEN, data=data, headers={'Accept': 'application/json'}, timeout=60, proxies=_proxies())
    except requests.RequestException as e:
        raise AuthError('连接 OpenAI 登录服务失败：' + str(e)[:160])
    try:
        d = r.json()
    except ValueError:
        d = {}
    if r.status_code >= 400 or not d.get('access_token'):
        err = d.get('error')
        code = err.get('code') if isinstance(err, dict) else err
        reason = d.get('error_description') or (err.get('message') if isinstance(err, dict) else err) or r.text[:160]
        raise AuthError(f'登录失败（{r.status_code}）：{reason}' + (f'［{code}］' if code and code not in str(reason) else ''))
    return d


# ------------------------------------------------------------------ 登录流程 ----
class _Login:
    def __init__(self, port: int, client_id: str = ''):
        self.verifier = _b64url(secrets.token_bytes(48))
        self.state = _b64url(secrets.token_bytes(24))
        self.nonce = _b64url(secrets.token_bytes(24))
        self.redirect = f'http://127.0.0.1:{port}/auth/callback'
        challenge = _b64url(hashlib.sha256(self.verifier.encode()).digest())
        q = {'client_id': client_id or 'dynamic_agent_client', 'ext_agent_host_id': host_id(), 'response_type': 'code',
             'redirect_uri': self.redirect, 'scope': SCOPES, 'resource': RESOURCE, 'state': self.state, 'nonce': self.nonce,
             'code_challenge_method': 'S256', 'code_challenge': challenge}
        if not client_id:
            q['agent_name_hint'] = APP_NAME  # 只在第一次注册时带
        self.url = AUTHORIZE + '?' + urlencode(q)
        self.code = self.client_id = self.error = None
        self.done = False
        self.server = None

    def accept(self, url: str) -> bool:
        q = parse_qs(urlparse(url).query)
        if q.get('error'):
            e = q['error'][0]
            self.error = '你在授权页取消了授权' if e == 'access_denied' else (q.get('error_description') or q['error'])[0]
            return True
        if q.get('code'):
            if q.get('state', [''])[0] != self.state:
                self.error = '登录结果与本次请求不符，请重新登录'
            elif not q.get('client_id', [''])[0].startswith('oaiapp_'):
                self.error = '登录结果里没有应用编号，请重新登录'
            else:
                self.code, self.client_id = q['code'][0], q['client_id'][0]
            return True
        return False


_current = None
_lock = threading.Lock()


def _free_port() -> int:
    import socket
    for port in (1455, 0):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    raise AuthError('找不到可用的本机端口，请稍后重试')


def start() -> str:
    """打开浏览器授权页，同时在本机端口等回调。返回授权网址。"""
    global _current
    with _lock:
        if _current and _current.server:
            try:
                _current.server.server_close()
            except OSError:
                pass
        old = config.load()['providers']['chatgpt'].get('oauth') or {}
        login = _Login(_free_port(), old.get('client_id', ''))
        _current = login

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if not self.path.startswith('/auth/callback'):
                self.send_response(404)
                self.end_headers()
                return
            ok = login.accept(self.path)
            body = ('<meta charset="utf-8"><body style="font:16px sans-serif;background:#111;color:#eee;padding:40px">'
                    + ('授权完成，可以关掉这个页面，回到「猛人学习」。' if ok and not login.error else '没有拿到授权结果，请回到「猛人学习」重试。')
                    + '</body>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    port = int(urlparse(login.redirect).port)
    login.server = HTTPServer(('127.0.0.1', port), Handler)
    login.server.timeout = 1

    def serve():
        deadline = time.time() + 600
        while time.time() < deadline and not (login.code or login.error) and _current is login:
            login.server.handle_request()
        try:
            login.server.server_close()
        except OSError:
            pass
    threading.Thread(target=serve, daemon=True, name='chatgpt-login').start()
    webbrowser.open(login.url)
    return login.url


def finish(pasted_url: str = '') -> dict:
    """检查授权是否完成。pasted_url：用户从浏览器地址栏复制的回调网址。返回 {'status': 'waiting'|'done', ...}。"""
    login = _current
    if not login:
        raise AuthError('请先点击「登录」')
    if pasted_url and not login.accept(pasted_url.strip()):
        raise AuthError('这个网址里没有授权结果。请复制授权完成后浏览器地址栏里以 http://127.0.0.1 开头的完整网址')
    if login.error:
        raise AuthError(login.error)
    if not login.code:
        return {'status': 'waiting'}
    if not login.done:
        tok = _post_token({'grant_type': 'authorization_code', 'client_id': login.client_id, 'code': login.code,
                           'code_verifier': login.verifier, 'redirect_uri': login.redirect, 'resource': RESOURCE})
        if not tok.get('refresh_token'):
            raise AuthError('OpenAI 没有返回登录凭证')
        with _refresh_lock():
            config.set_oauth('chatgpt', _creds(tok, {}, login.client_id))
        login.done = True
    o = config.load()['providers']['chatgpt']['oauth']
    return {'status': 'done', 'email': o.get('email', ''), 'plan': o.get('plan', '')}


def logout():
    """退出登录：先在 OpenAI 那边注销续期令牌（让它彻底作废），再清掉本机的。注销失败也清掉本机的。"""
    o = config.load()['providers']['chatgpt'].get('oauth') or {}
    if o.get('refresh') and o.get('client_id'):
        try:
            requests.post(REVOKE, data={'token': o['refresh'], 'token_type_hint': 'refresh_token', 'client_id': o['client_id']},
                          timeout=20, proxies=_proxies())
        except requests.RequestException:
            pass
    with _refresh_lock():
        config.set_oauth('chatgpt', {})


def fresh() -> dict:
    """拿到有效的访问令牌，快过期时续期（跨进程排队，锁里先读最新的设置文件）。"""
    o = config.load()['providers']['chatgpt']['oauth'] or {}
    if not o.get('refresh'):
        raise AuthError('还没有用 ChatGPT 官方登录，请到「设置 → AI 服务 → ChatGPT 账号」登录')
    if o.get('access') and o.get('expires', 0) - MARGIN > time.time():
        return o
    with _lock, _refresh_lock():
        o = config.load()['providers']['chatgpt']['oauth'] or {}  # 别的程序可能刚续过
        if not o.get('refresh'):
            raise AuthError('还没有用 ChatGPT 官方登录，请到「设置 → AI 服务 → ChatGPT 账号」登录')
        if o.get('access') and o.get('expires', 0) - MARGIN > time.time():
            return o
        try:
            tok = _post_token({'grant_type': 'refresh_token', 'client_id': o['client_id'], 'refresh_token': o['refresh'],
                               'resource': RESOURCE})
        except AuthError as e:
            if any(w in str(e) for w in ('invalid_grant', 'refresh_token_reused', 'refresh_token_expired', '（400）', '（401）')):
                config.set_oauth('chatgpt', dict(o, access='', refresh=''))  # 这份登录已经作废，留着 client_id 方便重新登录
                raise AuthError('ChatGPT 登录已经失效，请到「设置 → AI 服务 → ChatGPT 账号」重新登录')
            raise
        o = _creds(tok, o, o['client_id'])
        config.set_oauth('chatgpt', o)
        return o


def headers(o: dict) -> dict:
    return {'Authorization': 'Bearer ' + o['access']}


def models() -> list:
    o = fresh()
    try:
        r = requests.get(RESOURCE + '/models', headers=headers(o), timeout=30, proxies=_proxies())
    except requests.RequestException as e:
        raise AuthError('获取 ChatGPT 模型列表失败：' + str(e)[:160])
    if r.status_code >= 400:
        raise AuthError(f'获取 ChatGPT 模型列表失败（{r.status_code}）')
    d = r.json()
    entries = d.get('data') if isinstance(d.get('data'), list) else d.get('models') or []
    return [{'id': e.get('id') or e.get('slug'), 'name': e.get('display_name') or e.get('id') or e.get('slug')}
            for e in entries if (e.get('id') or e.get('slug')) and e.get('visibility', 'list') == 'list']
