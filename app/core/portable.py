"""解压即用：软件文件夹里放了「导出设置」得到的文件，而这台电脑上还没有猛人学习的设置时，第一次启动自动导入。
导入后把文件改名为「…（已导入，可删除）.json」，免得密钥一直留在软件文件夹里被忽略。"""
from pathlib import Path

from . import config
from .log import log

NAMES = ('猛人学习设置.json', '猛人字幕设置.json')


def import_bundled(api, folder=None):
    """返回导入结果（dict）；没有可导入的文件或这台电脑已经有设置时返回 None。"""
    if config.SETTINGS_FILE.exists():
        return None
    folder = Path(folder) if folder else config.app_dir()
    for name in NAMES:
        p = folder / name
        if not p.is_file():
            continue
        r = api.call('import_settings', {'path': str(p)})
        if not r.get('ok'):
            log.warning('自动导入随软件附带的设置失败：%s', r.get('error'))
            return None
        try:
            p.replace(p.with_name(p.stem + '（已导入，可删除）.json'))
        except OSError:
            pass
        log.info('第一次启动，自动导入了软件文件夹里的设置：%s', name)
        return r
    return None
