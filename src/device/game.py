"""游戏窗口相关操作：窗口查找、登录 Tab 读取、多开窗口绑定。"""
import ctypes
import re
from ctypes import wintypes

from ascript.windows.window import Window
from ascript.windows.window.Selector import Selector
from ascript.windows.system.key_value import KeyValue
from ..core import app_utils  # KeyValue 合并读写（防覆写子进程运行时状态）

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
    app_utils.kv_save_merged(_BOUND_WINDOW_KEY, data)
    return {"status": "success"}


def clear_bound_window():
    """解除窗口绑定（下次启动多开时重新弹框选择）。"""
    app_utils.kv_remove_merged(_BOUND_WINDOW_KEY)  # 同 save_bound_window：防覆写子进程运行时状态
    return {"status": "success", "msg": "已解除窗口绑定，下次启动重新选择"}


def _bound_match(raw, wins):
    """在窗口列表里匹配绑定记录（调用方持 wins，避免重复枚举）。

    匹配顺序：句柄精确命中（会话内句柄稳定、同账号多窗口时唯一
    区分）→ 账号 ID 命中标题（重启后句柄失效的延续匹配）。旧版
    纯 ID 字符串绑定按 ID 匹配。
    """
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


def find_bound_window():
    """返回绑定的窗口（未绑定或已不存在时返回 None）。"""
    raw = bound_window_id()
    if not raw:
        return None
    return _bound_match(raw, list_main_windows())


def _pick_main_window(wins, raw=None):
    """从窗口列表选实际操作窗口（find_main_window 的纯函数部分）。

    多个主窗口并存时（多开客户端）：优先绑定窗口；绑定缺失时优先
    已登录窗口（标题含 $Revision），否则取第一个。
    """
    if raw:
        bound = _bound_match(raw, wins)
        if bound is not None:
            return bound
    for w in wins:
        if "$Revision" in w.title:
            return w
    return wins[0] if wins else None


def find_main_window():
    """查找游戏主窗口。

    游戏存在多个同进程可见窗口（如聊天窗口 xychildwindow），
    用类名 MAIN_WINDOW_CLASS 精确定位主窗口；无可见主窗口时返回
    None。多个主窗口并存时（多开客户端）：优先返回绑定的窗口；
    绑定窗口已不存在时回退到已登录窗口（标题含 $Revision），否则
    取第一个。
    """
    return _pick_main_window(list_main_windows(), bound_window_id())


def ensure_binding_follows_live_window():
    """绑定窗口已关闭时，把绑定记录对齐到实际回退操作的游戏窗口。

    find_main_window 在绑定窗口消失后动态回退到其他游戏窗口（脚本
    实际就操作它），但绑定记录若不改写，按角色隔离的数据目录
    （user_data._probe_role_id 绑定优先）会停在旧角色——A 退出、打开
    B 后面板方案/设置/成就仍显示 A（2026-11-13 用户反馈）。控制窗口
    轮询本函数完成改绑，改绑后角色解析随新窗口。

    未登录窗口（标题无账号 ID）不改绑：save_bound_window 会退用完整
    标题作 ID，那是任意游戏窗口标题的公共子串，多开时会误匹配。
    返回 (win, rebound)：win 为当前操作窗口（可能 None），rebound 表示
    本次是否写入了新绑定。
    """
    raw = bound_window_id()
    wins = list_main_windows()
    if not raw:
        return _pick_main_window(wins), False  # 无绑定：交给启动/任务启动绑定
    bound = _bound_match(raw, wins)
    if bound is not None:
        return bound, False
    win = _pick_main_window(wins)
    if win is None or not _ID_IN_TITLE_RE.search(win.title or ""):
        return win, False
    save_bound_window(win)
    return win, True


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


# ---- 一窗口多 Tab（组号模式）子窗口枚举 ----
# 多标签客户端（xy2_tab_x64 系列）每个登录 Tab 对应主窗口的一个
# Win32Window 子窗口，标题「[大话西游2_经典版] 大话西游2经典版
# $Revision: xxx - 服务器 - 角色名（ID：账号）」；当前选中 Tab 的
# 子窗口可见、其余隐藏（客户端维护）——计数、登录判定、当前身份
# 全部走句柄枚举（2026-10-31 实机探测验证），零 OCR。
_WM_GETTEXT = 0x000D


def _child_window_text(hwnd):
    """跨进程读取子窗口标题（GetWindowTextW 优先，WM_GETTEXT 兜底）。

    本程序与游戏同完整性级别（均为管理员），WM_GETTEXT 不受 UIPI 拦截。
    """
    buf = ctypes.create_unicode_buffer(512)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 512)
    if buf.value:
        return buf.value
    ctypes.windll.user32.SendMessageW(hwnd, _WM_GETTEXT, 512, buf)
    return buf.value


def dump_child_windows(win):
    """枚举主窗口下所有可见子窗口（hwnd/类名/标题/矩形），返回行列表。

    诊断用（只读）：定位聊天输入框等子控件——静默文本输入通道的
    排查依据（WM_PASTE 投游戏视图无效时，真实输入控件可能是某个
    标准 EDIT 子窗口，需按类名/矩形找出来直投）。
    """
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    if not hwnd:
        return ["窗口句柄无效"]
    rows = []
    _EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                   ctypes.c_void_p)

    def _cb(h, _l):
        try:
            if not ctypes.windll.user32.IsWindowVisible(h):
                return True
            cls = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(h, cls, 256)
            r = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r)):
                return True
            rows.append(f"hwnd={int(h):#x} 类名={cls.value} "
                        f"矩形=({r.left},{r.top},{r.right},{r.bottom})")
        except Exception:
            pass
        return True

    try:
        ctypes.windll.user32.EnumChildWindows(ctypes.c_void_p(hwnd),
                                              _EnumProc(_cb), 0)
    except Exception:
        return rows or ["枚举失败"]
    return rows or ["无可见子窗口"]


def list_tab_windows(win):
    """枚举主窗口的登录 Tab 子窗口（每个 Tab 一个 Win32Window）。

    返回 [{"hwnd", "visible", "title", "id"}]，按子窗口枚举顺序；
    id 为标题中的「ID：账号」（未登录 Tab 无）。异常返回 []。
    """
    hwnd = getattr(win, "hwnd", 0)
    if not hwnd:
        return []
    results = []
    _EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                                   ctypes.c_void_p)

    def _cb(h, _l):
        try:
            cls = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(h, cls, 256)
            if cls.value != "Win32Window":
                return True
            title = _child_window_text(h)
            m = _ID_IN_TITLE_RE.search(title)
            results.append({"hwnd": int(h), "title": title,
                            "visible": bool(ctypes.windll.user32.IsWindowVisible(h)),
                            "id": m.group(1) if m else None})
        except Exception:
            pass
        return True

    try:
        ctypes.windll.user32.EnumChildWindows(
            ctypes.c_void_p(int(hwnd)), _EnumProc(_cb), 0)
    except Exception:
        return []
    return results
