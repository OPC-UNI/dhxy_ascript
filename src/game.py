"""游戏窗口相关操作：窗口查找、登录 Tab 读取。"""
import re

from ascript.windows.window import Window
from ascript.windows.window.Selector import Selector

# 目标游戏窗口标题
GAME_TITLE = "大话西游2经典版"
# 游戏主窗口类名（聊天窗口等辅助窗口类名不同，如 xychildwindow）
MAIN_WINDOW_CLASS = "DHXYJYMainFrame"


def check_game_window():
    """检查「大话西游2经典版」游戏窗口是否存在。

    用进程名 xy2 过滤，避免标题恰好包含该文字的无关窗口（如编辑器）。
    返回所有可见的相关窗口（含聊天窗口等辅助窗口）。
    """
    return Window.find_all(title_re=re.escape(GAME_TITLE), process_name="xy2")


def find_main_window():
    """查找游戏主窗口。

    游戏存在多个同进程可见窗口（如聊天窗口 xychildwindow），
    用类名 MAIN_WINDOW_CLASS 精确定位主窗口。游戏最小化时返回 None。
    """
    return Window.find(title_re=re.escape(GAME_TITLE), process_name="xy2",
                       class_name=MAIN_WINDOW_CLASS)


def read_login_tab(win):
    """读取游戏主窗口的 TabItemControl 文本。

    该 Tab 对应登录用户：未登录时显示"大话西游2经典版"，登录后显示角色名。
    读取失败返回 None。
    """
    try:
        item = Selector(win).type("TabItemControl").find()
        return item.name if item else None
    except Exception:
        return None
