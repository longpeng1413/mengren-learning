"""右下角托盘图标：点右上角 × 时（设置里「关闭窗口时收到右下角」打开）窗口收进这里，软件继续运行。
单击图标打开窗口；右键菜单「打开猛人学习」「退出」。"""
import threading

from core import config
from core.log import log

TITLE = '猛人学习'
MENU_OPEN = '打开猛人学习'
MENU_QUIT = '退出'


class Tray:
    def __init__(self, on_open, on_quit):
        self.on_open, self.on_quit = on_open, on_quit
        self.icon = None
        self.tipped = False  # 第一次收起时提示一下软件还在运行

    def start(self):
        try:
            import pystray
            from PIL import Image
            image = Image.open(config.resource_dir() / 'web' / 'icon.png')
            menu = pystray.Menu(pystray.MenuItem(MENU_OPEN, lambda: self.on_open(), default=True),
                                pystray.MenuItem(MENU_QUIT, lambda: self.on_quit()))
            self.icon = pystray.Icon('mengren-learning', image, TITLE, menu)
            threading.Thread(target=self.icon.run, daemon=True, name='tray').start()
            return True
        except Exception:
            log.exception('托盘图标启动失败，点 × 会直接退出')
            self.icon = None
            return False

    def tip(self):
        if self.icon and not self.tipped:
            self.tipped = True
            try:
                self.icon.notify('猛人学习还在右下角运行。单击这个图标打开，右键可以退出。\n不想这样可以在「设置 → 关于与数据」里关掉。', TITLE)
            except Exception:
                log.warning('托盘提示失败', exc_info=True)

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
