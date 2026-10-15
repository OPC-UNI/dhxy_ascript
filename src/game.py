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
    """当前绑定的窗口标识（未绑定返回 None）。

    格式（2026-10-15 起）：{"id": 标题中的「（ID：xxx）」片段,
    "hwnd": 窗口句柄, "title": 绑定时的完整标题}——绑定语义为
    「这个窗口的这个角色」：句柄区分同账号多窗口，ID 跨重启延续
    匹配。旧版纯 ID 字符串兼容读取（按 ID 匹配）。
    """
    return KeyValue.get(_BOUND_WINDOW_KEY, None)


def save_bound_window(win):
    """保存窗口绑定：句柄 + 账号 ID（用户 2026-10-15 确认的绑定
    语义）。无 ID 时（未登录窗口）ID 字段退完整标题。"""
    title = win.title
    m = _ID_IN_TITLE_RE.search(title)
    bid = f"（ID：{m.group(1)}）" if m else title
    data = {"id": bid,
            "hwnd": int(getattr(win, "hwnd", 0) or 0),
            "title": title}
    # 保存前重读合并：任务子进程会写入运行时状态（摄妖香时间戳/
    # 任务进度等），KeyValue 整文件覆写会抹掉（同 control_window._kv_save）
    KeyValue._initialize()
    KeyValue._load_data()
    KeyValue.save(_BOUND_WINDOW_KEY, data)
    return {"status": "success"}


def clear_bound_window():
    """解除窗口绑定（下次启动多开时重新弹框选择）。"""
    KeyValue._initialize()
    KeyValue._load_data()  # 同 save_bound_window：防覆写子进程运行时状态
    KeyValue.remove(_BOUND_WINDOW_KEY)
    return {"status": "success", "msg": "已解除窗口绑定，下次启动重新选择"}


def find_bound_window():
    """返回绑定的窗口（未绑定或已不存在时返回 None）。

    匹配顺序：句柄精确命中（会话内句柄稳定、同账号多窗口时唯一
    区分）→ 账号 ID 命中标题（重启后句柄失效的延续匹配）。旧版
    纯 ID 字符串绑定按 ID 匹配。
    """
    raw = bound_window_id()
    if not raw:
        return None
    wins = list_main_windows()
    bid = raw.get("id") if isinstance(raw, dict) else raw
    bh = raw.get("hwnd") if isinstance(raw, dict) else None
    if bh:
        for w in wins:
            if int(getattr(w, "hwnd", 0) or 0) == bh:
                return w
    if bid:
        for w in wins:
            if bid in w.title:
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
    """读取当前登录角色名（未登录时返回"大话西游2经典版"，失败返回 None）。

    优先解析窗口标题中的角色段（标题格式：... - 服务器 - 角色名（ID：账号），
    切 Tab 换角色后角色段同步变化，多 Tab 下标题角色 == 选中 Tab）——直接读
    TabItemControl 会返回 UIA 树序第一个 Tab，多 Tab 时可能不是当前角色。
    标题无角色段（未登录/加载中）时回退读 TabItemControl 首个文本。
    """
    try:
        parts = (getattr(win, "title", "") or "").split(" - ")
        if len(parts) >= 3 and "（ID：" in parts[-1]:
            return parts[-1].split("（ID：")[0].strip()
    except Exception:
        pass
    try:
        item = Selector(win).type("TabItemControl").find()
        return item.name if item else None
    except Exception:
        return None
