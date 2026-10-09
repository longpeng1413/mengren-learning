"""用 ChatGPT 账号登录（Codex 的登录方式，PKCE），调用 ChatGPT 套餐里的 Codex 额度。
做法与乔木剪藏相同：浏览器里登录 → 回调到 http://localhost:1455/auth/callback?code=… → 换取令牌。
有的电脑上本机回环连接不稳定，所以也接受用户把浏览器地址栏里的回调网址粘贴回来。"""
import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from . import config

CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
AUTHORIZE = 'https://auth.openai.com/oauth/authorize'
TOKEN = 'https://auth.openai.com/oauth/token'
REDIRECT = 'http://localhost:1455/auth/callback'
SCOPES = 'openid profile email offline_access'
BASE = 'https://chatgpt.com/backend-api/codex'
VERSION = '0.159.0'
MARGIN = 180  # 提前 3 分钟刷新


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
    return {'http': s['proxy'], 'https': s['proxy']} if s['providers']['codex'].get('use_proxy') and s.get('proxy') else None


def _creds(tok: dict, base: dict) -> dict:
    idc = _claims(tok.get('id_token', '')) if tok.get('id_token') else {}
    acc = _claims(tok.get('access_token', ''))
    auth = idc.get('https://api.openai.com/auth', {}) or {}
    aauth = acc.get('https://api.openai.com/auth', {}) or {}
    life = float(tok.get('expires_in') or (acc.get('exp', time.time() + 3600) - time.time()))
    return {
        'access': tok['access_token'],
        'refresh': tok.get('refresh_token') or base.get('refresh', ''),
        'id_token': tok.get('id_token') or base.get('id_token', ''),
        'expires': time.time() + life,
        'account_id': auth.get('chatgpt_account_id') or aauth.get('chatgpt_account_id') or base.get('account_id', ''),
        'email': idc.get('email') or base.get('email', ''),
        'plan': auth.get('chatgpt_plan_type') or aauth.get('chatgpt_plan_type') or base.get('plan', ''),
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
        reason = d.get('error_description') or (err.get('message') if isinstance(err, dict) else err) or r.text[:160]
        raise AuthError(f'登录失败（{r.status_code}）：{reason}')
    return d


# ------------------------------------------------------------------ 登录流程 ----
class _Login:
    def __init__(self):
        self.verifier = _b64url(secrets.token_bytes(48))
        self.state = _b64url(secrets.token_bytes(24))
        challenge = _b64url(hashlib.sha256(self.verifier.encode()).digest())
        self.url = AUTHORIZE + '?' + urlencode({
            'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': REDIRECT, 'scope': SCOPES,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'id_token_add_organizations': 'true',
            'codex_cli_simplified_flow': 'true', 'state': self.state, 'originator': 'codex_cli_rs'})
        self.code = None
        self.error = None
        self.done = False
        self.server = None

    def accept(self, url: str) -> bool:
        q = parse_qs(urlparse(url).query)
        if q.get('error'):
            self.error = (q.get('error_description') or q['error'])[0]
            return True
        if q.get('code'):
            if q.get('state', [''])[0] != self.state:
                self.error = '登录结果与本次请求不符，请重新登录'
            else:
                self.code = q['code'][0]
            return True
        return False


_current = None
_lock = threading.Lock()


def start() -> str:
    """打开浏览器登录页，同时在 1455 端口等回调。返回登录网址。"""
    global _current
    with _lock:
        if _current and _current.server:
            try:
                _current.server.server_close()
            except OSError:
                pass
        login = _Login()
        _current = login

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            ok = login.accept(self.path)
            body = ('<meta charset="utf-8"><body style="font:16px sans-serif;background:#111;color:#eee;padding:40px">'
                    + ('登录完成，可以关掉这个页面，回到「猛人字幕」。' if ok and not login.error else '没有拿到登录结果，请回到「猛人字幕」重试。')
                    + '</body>').encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        login.server = HTTPServer(('127.0.0.1', 1455), Handler)
        login.server.timeout = 1

        def serve():
            deadline = time.time() + 600
            while time.time() < deadline and not (login.code or login.error) and _current is login:
                login.server.handle_request()
            try:
                login.server.server_close()
            except OSError:
                pass
        threading.Thread(target=serve, daemon=True).start()
    except OSError:
        login.server = None  # 端口被占用（比如 Codex 正在登录），只能靠粘贴回调网址
    webbrowser.open(login.url)
    return login.url


def finish(pasted_url: str = '') -> dict:
    """检查登录是否完成。pasted_url：用户从浏览器地址栏复制的回调网址。返回 {'status': 'waiting'|'done', ...}。"""
    login = _current
    if not login:
        raise AuthError('请先点击「登录」')
    if pasted_url and not login.accept(pasted_url.strip()):
        raise AuthError('这个网址里没有登录结果。请复制登录完成后浏览器地址栏里以 http://localhost:1455/auth/callback 开头的完整网址')
    if login.error:
        raise AuthError(login.error)
    if not login.code:
        return {'status': 'waiting'}
    if not login.done:
        tok = _post_token({'grant_type': 'authorization_code', 'code': login.code, 'redirect_uri': REDIRECT,
                           'client_id': CLIENT_ID, 'code_verifier': login.verifier})
        if not tok.get('refresh_token'):
            raise AuthError('OpenAI 没有返回登录凭证')
        config.set_oauth('codex', _creds(tok, {}))
        login.done = True
    o = config.load()['providers']['codex']['oauth']
    return {'status': 'done', 'email': o.get('email', ''), 'plan': o.get('plan', '')}


def logout():
    config.set_oauth('codex', {})


def fresh() -> dict:
    """拿到有效的访问令牌，快过期时自动刷新。"""
    with _lock:
        o = config.load()['providers']['codex']['oauth'] or {}
        if not o.get('refresh'):
            raise AuthError('还没有用 ChatGPT 账号登录，请到「设置 → AI 服务 → ChatGPT」登录')
        if o.get('access') and o.get('expires', 0) - MARGIN > time.time():
            return o
        tok = _post_token({'grant_type': 'refresh_token', 'client_id': CLIENT_ID, 'refresh_token': o['refresh'],
                           'scope': 'openid profile email'})
        o = _creds(tok, o)
        config.set_oauth('codex', o)
        return o


def headers(o: dict) -> dict:
    h = {'Authorization': 'Bearer ' + o['access'], 'originator': 'codex_cli_rs', 'version': VERSION,
         'OpenAI-Beta': 'responses=experimental'}
    if o.get('account_id'):
        h['chatgpt-account-id'] = o['account_id']
    return h


def models() -> list:
    o = fresh()
    r = requests.get(f'{BASE}/models?client_version={VERSION}', headers=headers(o), timeout=30, proxies=_proxies())
    if r.status_code >= 400:
        raise AuthError(f'获取 ChatGPT 模型列表失败（{r.status_code}）')
    d = r.json()
    entries = d.get('models') if isinstance(d.get('models'), list) else d.get('data') or []
    out = []
    for e in entries:
        mid = e.get('slug') or e.get('id')
        if mid and e.get('visibility', 'list') == 'list':
            out.append({'id': mid, 'name': e.get('display_name') or mid})
    return out
