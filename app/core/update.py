"""软件内更新（2026-11-03，用户要求照猛人快传 v1.8.0 的办法做，不另建下载站）：
问 GitHub 最新正式版 → 下载更新包（不带 ffmpeg，约 30 MB）→ 核对 SHA-256 → 解压检查 →
PowerShell 脚本等软件保存退出后换上新文件、自动重新打开。

- 只认 releases/latest（正式版），标签 vX.Y.Z，按数字比，只有更高才算新版本。
- 附件名不能改，改了以前装的版本就找不到更新包：
    mengren-learning-版本-windows-update.zip（更新包，release.py 打）
    mengren-learning-版本-windows-with-ffmpeg.zip（完整包；没有更新包时也能用，里面的 ffmpeg 不换）
    SHA256SUMS-版本.txt（校验值清单）
  校验值：GitHub 附件自带的 digest 和清单至少有一份，两份都有时必须一致；下载时边写边算，对不上就删掉。
- 网络：先按系统代理（requests 会读 Windows 的代理设置，Clash 开着时就走它），连不上、又在「网络代理」里填了地址时再走那个。
- 替换（PS_SCRIPT）：整块换掉更新包里有的东西（猛人学习.exe、_internal 文件夹），旧的先挪进备份；
  软件文件夹里别的（缓存、日志、ffmpeg、用户放的文件）不动。中途出错全部还原。
  换完打开新版本，新版本启动时（startup）写 launched.txt；新版本还没写就退出、退出码不是 0（打不开）时，脚本退回旧版本再打开。
- **PowerShell 5.1 不能用「不分配控制台」的方式启动**（猛人快传实测：什么都不执行就退出），
  所以由一个短暂的 PowerShell 用 Start-Process 拉起替换脚本，脚本先写 apply_started.txt，软件看到它才退出。
- 脚本只写英文（5.1 按系统编码读没有 BOM 的脚本），中文路径从参数传。
- 临时文件都在软件文件夹的 WORK_NAME 里（和软件同一个磁盘，挪文件夹很快）；每次启动时（startup）清掉，
  包括下载到一半的、异常退出留下的。只在软件是打包好的「猛人学习.exe」、文件夹能写时才能自动更新。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

from . import config
from .log import log

REPO = 'longpeng1413/mengren-learning'
PREFIX = 'mengren-learning'
EXE = config.APP_NAME + '.exe'
WORK_NAME = '更新临时文件（可随时删除）'
LATEST_API = f'https://api.github.com/repos/{REPO}/releases/latest'
RELEASES = f'https://github.com/{REPO}/releases'
KEEP = {'ffmpeg', config.CACHE_NAME, config.LOG_NAME, WORK_NAME}  # 更新包里就算有也不换（ffmpeg 很大、平时不用换）

_last = None  # 上次更新的结果（startup 读出来，界面显示一次）
_lock = threading.Lock()


class UpdateError(ValueError):
    """更新不成功的原因（直接给界面看）。"""


def work_dir() -> Path:
    return config.app_dir() / WORK_NAME


def ver_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or '')[:4])


def test_version():
    """更新测试版：python main.py --update-test-version=1.0.0，假装自己是旧版本，用来实测整套更新（正式用不到）。"""
    return next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--update-test-version=')), None)


# ------------------------------------------------------------------ 检查 ----
def _get(url, proxy=None, **kw):
    import requests
    last = None
    for px in [None] + ([{'http': proxy, 'https': proxy}] if proxy else []):
        try:
            r = requests.get(url, timeout=kw.pop('timeout', (8, 30)), proxies=px, **kw)
            if r.status_code == 200:
                return r
            last = f'HTTP {r.status_code}'
            r.close()
        except requests.RequestException as e:
            last = e
    raise ConnectionError(last)


def _headers(current):
    return {'Accept': 'application/vnd.github+json', 'User-Agent': f'{PREFIX}/{current or "dev"}'}


def fetch_latest(current, proxy=None) -> dict:
    """问 GitHub 最新正式版。返回界面要的信息，asset 是要下载的更新包（没有就是 None）。"""
    try:
        data = _get(LATEST_API, proxy, headers=_headers(current), timeout=(8, 20)).json()
    except ConnectionError as e:
        log.info('检查更新失败：%s', e)
        raise ConnectionError('连不上 GitHub，检查不了更新。可以稍后再试，或者在「设置 → 网络代理」里填上代理地址再试')
    tag = str(data.get('tag_name') or '')
    latest = tag.lstrip('vV')
    newer = bool(re.fullmatch(r'[vV]?\d+(\.\d+){1,3}', tag)) and ver_tuple(latest) > ver_tuple(current)
    return {'current': current, 'latest': latest, 'newer': newer, 'url': data.get('html_url') or RELEASES + '/latest',
            'notes': str(data.get('body') or '')[:6000], 'date': str(data.get('published_at') or '')[:10],
            'asset': pick_asset(data, latest) if newer else None, 'sums': _sums_asset(data, latest)}


def pick_asset(release, version):
    """优先更新包，没有就用完整包。"""
    assets = {a.get('name'): a for a in release.get('assets') or []}
    for name in (f'{PREFIX}-{version}-windows-update.zip', f'{PREFIX}-{version}-windows-with-ffmpeg.zip'):
        a = assets.get(name)
        if a and a.get('browser_download_url'):
            digest = str(a.get('digest') or '').lower()
            return {'name': name, 'url': a['browser_download_url'], 'size': int(a.get('size') or 0),
                    'digest': digest[7:] if digest.startswith('sha256:') and len(digest) == 71 else None}
    return None


def _sums_asset(release, version):
    for a in release.get('assets') or []:
        if a.get('name') == f'SHA256SUMS-{version}.txt':
            return a.get('browser_download_url')
    return None


def parse_sums(text) -> dict:
    """「校验值  文件名」一行一个；文件名前的 * 不算。"""
    out = {}
    for line in str(text or '').splitlines():
        m = re.match(r'^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$', line)
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def expected_sha(digest, from_sums):
    if digest and from_sums and digest != from_sums:
        raise UpdateError('发布页上的两份校验值不一致，已停止更新，请去下载页手动下载')
    if not (digest or from_sums):
        raise UpdateError('发布页上没有更新包的校验值，已停止更新，请去下载页手动下载')
    return digest or from_sums


def can_update():
    """能不能自动更新：(能, 不能的原因)。"""
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        return False, '从源码运行时不能自动更新'
    exe = Path(sys.executable)
    if exe.name.lower() != EXE.lower():
        return False, f'程序文件不叫「{EXE}」，不能自动更新，请去下载页手动下载'
    probe = config.app_dir() / f'.update-probe-{os.getpid()}'
    try:
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
    except OSError:
        return False, '软件所在的文件夹不能写入（例如放在 C:\\Program Files 里），不能自动更新，请去下载页手动下载'
    return True, ''


# ------------------------------------------------------------ 下载、解压 ----
def prepare(info, job, proxy=None):
    """下载 → 核对 → 解压 → 检查。返回替换要用的 plan。job 用来报进度、看有没有点「停止」。"""
    asset = info.get('asset')
    if not asset:
        raise UpdateError('发布页上没有找到更新包，请去下载页手动下载')
    ok, why = can_update()
    if not ok:
        raise UpdateError(why)
    work = work_dir()
    work.mkdir(parents=True, exist_ok=True)
    current = info.get('current')
    from_sums = None
    if info.get('sums'):
        try:
            from_sums = parse_sums(_get(info['sums'], proxy, headers={'User-Agent': f'{PREFIX}/{current}'}).text).get(asset['name'])
        except ConnectionError as e:
            log.info('取校验值清单失败：%s', e)
    sha = expected_sha(asset.get('digest'), from_sums)
    zpath = work / asset['name']
    part = zpath.with_name(zpath.name + '.part')
    try:
        _download(asset, part, sha, job, proxy, current)
        os.replace(part, zpath)
    finally:
        part.unlink(missing_ok=True)
    job.report(0.96, '正在解压新版本…')
    staging = work / ('staging-' + info['latest'])
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(zpath) as z:
            z.extractall(staging)
    except (zipfile.BadZipFile, OSError) as e:
        raise UpdateError(f'解压更新包失败（{e}），请去下载页手动下载')
    finally:
        zpath.unlink(missing_ok=True)
    root = find_root(staging)
    for name in KEEP | {config.APP_NAME + '设置.json'}:
        p = root / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    return {'version': info['latest'], 'staging': str(root), 'target': str(config.app_dir())}


def _download(asset, part, sha, job, proxy, current):
    import requests
    h = hashlib.sha256()
    last = None
    for px in [None] + ([{'http': proxy, 'https': proxy}] if proxy else []):
        h = hashlib.sha256()
        got = 0
        try:
            with requests.get(asset['url'], stream=True, timeout=(10, 60), proxies=px,
                              headers={'User-Agent': f'{PREFIX}/{current}'}) as r:
                if r.status_code != 200:
                    last = f'HTTP {r.status_code}'
                    continue
                if not r.url.startswith('https://'):
                    raise UpdateError('下载地址不是 https，已停止更新')
                total = int(r.headers.get('Content-Length') or asset.get('size') or 0)
                with open(part, 'wb') as f:
                    for chunk in r.iter_content(256 * 1024):
                        if job.cancel.is_set():
                            raise UpdateError('已停止下载')
                        f.write(chunk)
                        h.update(chunk)
                        got += len(chunk)
                        if total:
                            job.report(got / total * 0.95, f'正在下载新版本 {got / 1048576:.1f} / {total / 1048576:.1f} MB')
            break
        except requests.RequestException as e:
            last = e
    else:
        log.info('下载更新包失败：%s', last)
        raise UpdateError('下载更新包失败（连不上 GitHub 或中途断了）。可以稍后再试，或者在「设置 → 网络代理」里填上代理地址再试')
    if h.hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise UpdateError('下载的更新包校验不对（可能下载时坏了），已经删掉，请再试一次')


def find_root(staging: Path) -> Path:
    """压缩包里可能直接是软件文件，也可能多一层「猛人学习」文件夹。"""
    for d in [staging] + [p for p in staging.iterdir() if p.is_dir()]:
        if (d / EXE).is_file() and (d / '_internal').is_dir():
            return d
    raise UpdateError(f'更新包内容不完整（没有 {EXE} 或 _internal），已停止更新')


# ------------------------------------------------------------------ 替换 ----
PS_SCRIPT = r'''
param(
  [Parameter(Mandatory = $true)][string]$Staging,
  [Parameter(Mandatory = $true)][string]$Target,
  [Parameter(Mandatory = $true)][string]$ExeName,
  [Parameter(Mandatory = $true)][string]$Work,
  [string]$Version = '',
  [int]$ProcessId = 0,
  [int]$Retries = 40,
  [int]$LaunchWait = 120,
  [switch]$NoRestart
)
# Replaces the app with the extracted update. Keep this file ASCII-only:
# Windows PowerShell 5.1 reads scripts without a BOM in the system code page.
$ErrorActionPreference = 'Stop'
$started = Join-Path $Work 'apply_started.txt'
$resultFile = Join-Path $Work 'update_result.json'
$launched = Join-Path $Work 'launched.txt'
$backup = Join-Path $Work 'backup'

function Write-Result([bool]$Ok, [string]$Code, [string]$Message) {
  $payload = @{ ok = $Ok; version = $Version; code = $Code; message = $Message; time = (Get-Date).ToString('o') } | ConvertTo-Json -Compress
  [System.IO.File]::WriteAllText($resultFile, $payload, (New-Object System.Text.UTF8Encoding($false)))
}

function Retry([scriptblock]$Action) {
  for ($i = 1; $i -le $Retries; $i++) {
    try { & $Action; return } catch { if ($i -ge $Retries) { throw }; Start-Sleep -Milliseconds 500 }
  }
}

$names = New-Object System.Collections.Generic.List[string]
$moved = New-Object System.Collections.Generic.List[string]
$created = New-Object System.Collections.Generic.List[string]

function Restore-Old {
  foreach ($n in $created) {
    $d = Join-Path $Target $n
    if (Test-Path -LiteralPath $d) { Retry { Remove-Item -LiteralPath $d -Recurse -Force } }
  }
  foreach ($n in $moved) {
    Retry { Move-Item -LiteralPath (Join-Path $backup $n) -Destination (Join-Path $Target $n) -Force }
  }
}

try { [System.IO.File]::WriteAllText($started, "$PID") } catch {}

$ok = $false
try {
  if ($ProcessId -gt 0) {
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $p -and -not $p.WaitForExit(60000)) { throw 'The running app did not exit within 60 seconds.' }
    Start-Sleep -Milliseconds 800
  }
  if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
  New-Item -ItemType Directory -Path $backup -Force | Out-Null
  if (Test-Path -LiteralPath $launched) { Remove-Item -LiteralPath $launched -Force }
  $items = @(Get-ChildItem -LiteralPath $Staging -Force)
  if ($items.Count -eq 0) { throw 'The extracted update is empty.' }
  if (-not (Test-Path -LiteralPath (Join-Path $Staging $ExeName))) { throw 'The update does not contain the program.' }
  try {
    foreach ($item in $items) {
      $n = $item.Name
      $dest = Join-Path $Target $n
      if (Test-Path -LiteralPath $dest) {
        Retry { Move-Item -LiteralPath $dest -Destination (Join-Path $backup $n) -Force }
        $moved.Add($n)
      }
      $created.Add($n)
      Retry {
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        Copy-Item -LiteralPath $item.FullName -Destination $dest -Recurse -Force
      }
    }
  } catch {
    $failure = $_.Exception.Message
    Restore-Old
    throw "Copy failed and the previous version was restored: $failure"
  }
  Write-Result $true '' ''
  $ok = $true
} catch {
  try { Write-Result $false 'apply_failed' $_.Exception.Message } catch {}
}

if (-not $NoRestart) {
  $exe = Join-Path $Target $ExeName
  if ($ok) {
    $p = Start-Process -FilePath $exe -WorkingDirectory $Target -PassThru
    $null = $p.Handle  # without this, ExitCode stays empty after the process ends
    $deadline = (Get-Date).AddSeconds($LaunchWait)
    $broken = $false
    while ((Get-Date) -lt $deadline) {
      if (Test-Path -LiteralPath $launched) { break }
      if ($p.HasExited) {
        if ($p.ExitCode -ne 0) { $broken = $true }
        break
      }
      Start-Sleep -Milliseconds 500
    }
    if ($broken) {
      try {
        Restore-Old
        Write-Result $false 'new_version_failed' ("The new version exited with code " + $p.ExitCode)
      } catch {
        try { Write-Result $false 'restore_failed' $_.Exception.Message } catch {}
      }
      if (Test-Path -LiteralPath $exe) { Start-Process -FilePath $exe -WorkingDirectory $Target }
    }
  } elseif (Test-Path -LiteralPath $exe) {
    Start-Process -FilePath $exe -WorkingDirectory $Target
  }
}
try { Remove-Item -LiteralPath $started -Force } catch {}
'''


def _ps_args(plan, pid, restart=True, launch_wait=120):
    work = Path(plan.get('work') or work_dir())
    script = work / 'apply_update.ps1'
    args = ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script),
            '-Staging', plan['staging'], '-Target', plan['target'], '-ExeName', plan.get('exe') or EXE,
            '-Work', str(work), '-Version', plan.get('version') or '',
            '-ProcessId', str(pid), '-LaunchWait', str(launch_wait)]
    return script, args + ([] if restart else ['-NoRestart'])


def launch(plan, pid=None, restart=True, wait=15, launch_wait=120):
    """拉起替换脚本，等它报「已启动」。返回后调用方马上退出软件（脚本会等这个进程结束才开始换）。"""
    work = Path(plan.get('work') or work_dir())
    script, args = _ps_args(plan, pid if pid is not None else os.getpid(), restart, launch_wait)
    script.write_text(PS_SCRIPT.replace('\r\n', '\n').replace('\n', '\r\n'), encoding='ascii')
    started = work / 'apply_started.txt'
    for f in (started, work / 'update_result.json'):
        f.unlink(missing_ok=True)
    line = subprocess.list2cmdline(args).replace("'", "''")
    cwd = str(work).replace("'", "''")
    relay = ("Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden "
             f"-WorkingDirectory '{cwd}' -ArgumentList '{line}'")
    r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', relay],
                       capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if r.returncode != 0:
        log.warning('启动替换脚本失败：%s', (r.stderr or b'').decode('gbk', 'replace')[:500])
        raise UpdateError('更新程序没能启动，软件不会关闭；请去下载页手动下载')
    deadline = time.time() + wait
    while not started.exists():
        if time.time() > deadline:
            raise UpdateError('更新程序没能启动（可能被安全软件拦住了），软件不会关闭；请去下载页手动下载')
        time.sleep(0.2)
    log.info('替换脚本已启动，软件马上退出，换成 %s', plan.get('version'))


# ------------------------------------------------------------------ 启动 ----
RESULT_TEXT = {
    'apply_failed': '更新没成功，还是原来的版本',
    'new_version_failed': '新版本没能打开，已经自动退回原来的版本',
    'restore_failed': '更新出了问题，没能完全退回原来的版本，请去下载页重新下载完整的压缩包',
}


def startup():
    """软件启动时最先调用（main.py）：读上次更新的结果（界面稍后显示一次）、
    告诉还在等的替换脚本「新版本打开了」，再在后台清掉更新临时文件（包括下载到一半、异常退出留下的）。"""
    global _last
    work = work_dir()
    if not work.exists():
        return
    try:
        r = json.loads((work / 'update_result.json').read_text(encoding='utf-8-sig'))
        if isinstance(r, dict):
            _last = r
            log.info('上次更新的结果：%s', r)
    except (OSError, ValueError):
        pass
    try:
        (work / 'launched.txt').write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        pass
    threading.Thread(target=_clean_later, args=(work,), daemon=True, name='update-clean').start()


def _alive(pid):
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    except Exception:
        return False


def _clean_later(work: Path, wait=180):
    """替换脚本还在运行（看到 launched.txt 后几秒内就结束）时先等它，再整个删掉。"""
    started = work / 'apply_started.txt'
    end = time.time() + wait
    while started.exists() and time.time() < end:
        try:
            pid = int(started.read_text(encoding='utf-8', errors='ignore').strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if not pid or not _alive(pid):
            break
        time.sleep(1)
    with _lock:
        shutil.rmtree(work, ignore_errors=True)


def last_result():
    """上次更新的结果，只给一次。"""
    global _last
    r, _last = _last, None
    if not r:
        return None
    ok = bool(r.get('ok'))
    return {'ok': ok, 'version': r.get('version') or '',
            'text': '' if ok else RESULT_TEXT.get(r.get('code'), RESULT_TEXT['apply_failed']),
            'detail': '' if ok else str(r.get('message') or '')[:300]}
