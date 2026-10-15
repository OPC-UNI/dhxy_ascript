"""游戏窗口相关操作：窗口查找、登录 Tab 读取、多开窗口绑定。"""
import re

from ascript.windows.window import Window
from ascript.windows.window.Selector import Selector
from ascript.windows.system.key_value import KeyValue

# 目标游戏窗口标题
GAME_TITLE = "大话西游2经典版"
# 游戏主窗口类名（聊天窗口等辅助窗口类名不同，如 xychildwindow）
MAIN_WINDOW_CLASS = "DHXYJYMainFrame"
# 多开绑定的窗口标识（标题中的账号 ID 片段，如「（ID：214369642）」；
# 稳定于 $Revision 版本号变化，跨游戏更新仍可匹配）
_BOUND_WINDOW_KEY = "bound_window_id"
_ID_IN_TITLE_RE = re.compile(r"ID：(\d+)")


def check_game_window():
    """检查「大话西游2经典版」游戏窗口是否存在。

    用进程名 xy2 过滤，避免标题恰好包含该文字的无关窗口（如编辑器）。
    返回所有可见的相关窗口（含聊天窗口等辅助窗口）。
    """
    return Window.find_all(title_re=re.escape(GAME_TITLE), process_name="xy2")


def list_main_windows():
    """所有游戏主窗口（多开时每个客户端一个；聊天等辅助窗口已过滤）。"""
    return Window.find_all(title_re=re.escape(GAME_TITLE), process_name="xy2",
                           class_name=MAIN_WINDOW_CLASS)


def bound_window_id():
    """当前绑定的窗口标识（未绑定返回 None）。"""
    return KeyValue.get(_BOUND_WINDOW_KEY, None)


def save_bound_window(title):
    """保存窗口绑定：优先提取标题中的账号 ID（稳定标识），无 ID 时存
    完整标题（未登录窗口的兜底，更新版本后可能失效需重选）。"""
    m = _ID_IN_TITLE_RE.search(title)
    # 保存前重读合并：任务子进程会写入运行时状态（摄妖香时间戳/
    # 任务进度等），KeyValue 整文件覆写会抹掉（同 control_window._kv_save）
    KeyValue._initialize()
    KeyValue._load_data()
    KeyValue.save(_BOUND_WINDOW_KEY, f"（ID：{m.group(1)}）" if m else title)
    return {"status": "success"}


def clear_bound_window():
    """解除窗口绑定（下次启动多开时重新弹框选择）。"""
    KeyValue._initialize()
    KeyValue._load_data()  # 同 save_bound_window：防覆写子进程运行时状态
    KeyValue.remove(_BOUND_WINDOW_KEY)
    return {"status": "success", "msg": "已解除窗口绑定，下次启动重新选择"}


def find_bound_window():
    """返回绑定的窗口（未绑定或已不存在时返回 None）。"""
    bound = bound_window_id()
    if not bound:
        return None
    for w in list_main_windows():
        if bound in w.title:
            return w
    return None


def find_main_window():
    """查找游戏主窗口。

    游戏存在多个同进程可见窗口（如聊天窗口 xychildwindow），
    用类名 MAIN_WINDOW_CLASS 精确定位主窗口。游戏最小化时返回 None。
    多个主窗口并存时（多开客户端）：优先返回绑定的窗口；无绑定时
    优先已登录窗口（标题含 $Revision），否则取第一个。
    """
    wins = list_main_windows()
    if not wins:
        return None
    bound = find_bound_window()
    if bound is not None:
        return bound
    for w in wins:
        if "$Revision" in w.title:
            return w
    return wins[0]


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
