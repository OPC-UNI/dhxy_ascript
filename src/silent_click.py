# -*- coding: utf-8 -*-
"""静默点击：不移动物理鼠标、不激活窗口，直接向游戏窗口投递鼠标消息。

用途：把部分原走物理鼠标的点击（human_click）切换为静默点击的最佳实践
通道——鼠标不用动、游戏窗口不用在前台，任务可与其他窗口并行。经典版
单窗口客户端曾实测不响应合成鼠标消息（2026-10-26，见 memory/
background-input-postmessage-analysis）；当前多标签客户端（xy2_tab_x64
系列）的渲染/输入面在 Tab 子窗口上，本通道按子窗口投递重新验证。
面板「诊断」页「静默点击」按钮做可行性验证（一次点击 + 前后帧对比）。

坐标空间：send_silent_click 的 x/y 与 human_click 同空间（win.rect
左上角为原点，即主窗口矩形相对坐标），迁移时直接替换调用即可；x/y
为 None 时点目标窗口客户区中心（游戏画面中心）。内部换算为点击目标
窗口（可见 Tab 子窗口，无则主窗口）的客户区坐标后投递
WM_MOUSEMOVE→WM_LBUTTONDOWN→WM_LBUTTONUP。投递全程持 app_utils
全局输入锁（_INPUT_LOCK），与物理鼠标点击序列互斥——与战斗线程/
任务线程共享输入通道时的交错防护同一把锁。

Tab 条切 Tab（静默+后台通道，2026-11-04 实机验证生效）：Tab 条是
标准 SysTabControl32 子窗口，UIA 读 Tab 项矩形（send_silent_tab_click，
DOWN+UP 最小序列）；矩形缓存为客户区坐标（读取时减去控件窗口原点），
窗口移动不失效（2026-11-10 拖动窗口后整轮切 Tab 失效的教训）。【禁区】
带指针的消息（TCM_GETITEMRECT 等）跨进程不封送、会令客户端崩溃（当日
教训）；TCM_SETCURSEL 不切视图；TCM 消息仅用 int-only（GETITEMCOUNT/
GETCURSEL）。
"""
import ctypes
import time
from ctypes import wintypes

from . import app_utils  # 全局输入锁：与物理鼠标点击互斥
from . import game  # 多标签客户端：点击目标优先可见的 Tab 子窗口

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002

# button 参数（human_click 同款命名）→ 消息常量映射
_BUTTON_MESSAGES = {
    "primary": (WM_LBUTTONDOWN, WM_LBUTTONUP, MK_LBUTTON),
    "secondary": (WM_RBUTTONDOWN, WM_RBUTTONUP, MK_RBUTTON),
}
_BUTTON_NAMES = {"primary": "左键", "secondary": "右键"}

# ---- 键盘消息投递（静默按键可行性验证：与点击同思路，游戏是否
# 消费键盘消息待实测——若游戏以 GetKeyState/原始输入读键则无效） ----
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_PASTE = 0x0302
WM_UNICHAR = 0x0109

CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0042

# 键表：名 → (虚拟键码, 扫描码)。扫描码为美式键盘标准布局，按需扩展。
_KEY_CODES = {
    "alt": (0x12, 0x38),
    "ctrl": (0x11, 0x1D),
    "tab": (0x09, 0x0F),
    "enter": (0x0D, 0x1C),
    "esc": (0x1B, 0x01),
    "e": (0x45, 0x12),
    "c": (0x43, 0x2E),
    "u": (0x55, 0x16),  # 飞行驭器高性能路径（Alt+U 直接打开面板）
    "v": (0x56, 0x2F),
    "5": (0x35, 0x06),
    "6": (0x36, 0x07),
}

# 帧差异判定：任一通道差 >32 判该像素变化
_CHANGED_PX = 32
# 点击后响应采样间隔（秒）：点击生效（走位/对话框开合）很快渲染出来
_RESPONSE_INTERVAL = 1.5
# 验证阈值：点击前后变化像素占比达 1% 判疑似生效（静态场景下生效
# 动作是大范围变化，未生效则画面几乎不动）
_RESPOND_THRESHOLD = 1.0


def _setup_prototypes():
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    ctypes.c_ssize_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p,
                                        wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    # 剪贴板（静默粘贴用）
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL


_setup_prototypes()


def win_class(hwnd):
    """窗口类名（日志用；失败返回空串）。"""
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def click_target(win):
    """静默点击的目标子窗口句柄：可见的登录 Tab 子窗口（Win32Window），无则主窗口。

    多标签客户端的渲染/输入面在 Tab 子窗口上，主窗口只承接框架区；
    点击消息投给子窗口才可能进入游戏输入管线。子窗口不存在（老式
    单窗口客户端）时回退主窗口句柄。
    """
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    if not hwnd:
        return None
    try:
        for tab in game.list_tab_windows(win):
            if tab.get("visible") and tab.get("hwnd"):
                return int(tab["hwnd"])
    except Exception:
        pass  # 子窗口枚举失败不阻塞（回退主窗口）
    return hwnd


def _resolve_point(win, x=None, y=None):
    """静默点击/长按的目标与坐标换算。

    x/y 为 human_click 同空间（win.rect 左上角为原点）；None 取目标
    窗口客户区中心。返回 (target, cx, cy, sx, sy, client_w, client_h)：
    cx/cy 为目标窗口客户区坐标，sx/sy 为屏幕坐标；窗口/客户区获取
    失败返回 None。
    """
    target = click_target(win)
    if not target:
        return None
    tr = wintypes.RECT()
    cl = wintypes.RECT()
    if not (user32.GetWindowRect(target, ctypes.byref(tr))
            and user32.GetClientRect(target, ctypes.byref(cl))
            and cl.right > 0 and cl.bottom > 0):
        return None
    if x is None or y is None:
        # 画面中心：目标窗口客户区中心
        cx, cy = cl.right // 2, cl.bottom // 2
        sx, sy = tr.left + cx, tr.top + cy
    else:
        # human_click 同空间：win.rect 相对坐标 → 屏幕 → 目标窗口客户区
        wr = getattr(win, "rect", None)
        base_x = wr.left if wr is not None else tr.left
        base_y = wr.top if wr is not None else tr.top
        sx, sy = base_x + int(x), base_y + int(y)
        cx, cy = sx - tr.left, sy - tr.top
    return target, cx, cy, sx, sy, cl.right, cl.bottom


def send_silent_click(win, x=None, y=None, button="primary"):
    """向游戏窗口投递一次静默点击（不移动鼠标、不激活窗口）。

    x/y 为点击坐标（human_click 同空间：win.rect 左上角为原点，
    即主窗口矩形相对坐标），None 时取目标窗口客户区中心（游戏画面
    中心）。button 与 human_click 同款命名：'primary' 左键 /
    'secondary' 右键。内部换算为目标窗口客户区坐标后投递，全程持
    全局输入锁与物理鼠标点击互斥；按下/抬起后追加一条 WM_MOUSEMOVE
    把游戏内部光标移到客户区左下角中性区（等效物理模式 move_away，
    防悬停高亮残留污染后续对话框绿字判定）。返回投递描述 dict
    （目标句柄/类名/点击点/各消息返回值）供日志打印；窗口或客户区
    获取失败返回 None。
    """
    msg = _BUTTON_MESSAGES.get(button)
    if msg is None:
        raise ValueError(f"不支持的按键类型: {button!r}（primary/secondary）")
    down, up, mk = msg
    resolved = _resolve_point(win, x, y)
    if resolved is None:
        return None
    target, cx, cy, sx, sy, cw, ch = resolved
    results = _post_mouse_click(target, cx, cy, down, up, mk, cw, ch)
    return {"hwnd": target, "class": win_class(target),
            "button": button,
            "client": (cw, ch),
            "point": (cx, cy),
            "screen_point": (sx, sy),
            "msg_results": results}


def send_silent_click_burst(win, x=None, y=None, times=1, press=0.02,
                            interval=0.0):
    """原地快速连点（冲刺连点小游戏）：一次定位，连点 times 次。

    与 send_silent_click 的区别：整批点击只投一次 WM_MOUSEMOVE，
    逐击之间无 3×50ms 填充延迟（SendMessage 同步投递，游戏逐条处理），
    供「每秒需连点十余次」的小游戏使用（蛟魔探秘钓鱼：单击仅推进
    ~0.1 刻度，三档节奏到不了目标带，2026-11-10 真机实测）。每击按压
    press 秒（默认 20ms，与 rapid_click 一致）。整批持一次全局输入锁，
    结束追加一次移开消息防悬停残留（同单点路径）。返回投递描述 dict；
    窗口/客户区获取失败返回 None。
    """
    resolved = _resolve_point(win, x, y)
    if resolved is None:
        return None
    target, cx, cy, sx, sy, cw, ch = resolved
    lp = (cy << 16) | (cx & 0xFFFF)
    away_lp = ((ch - 20) << 16) | 20
    n = max(1, int(times))
    with app_utils._INPUT_LOCK:
        user32.SendMessageW(target, WM_MOUSEMOVE, 0, lp)
        for _ in range(n):
            user32.SendMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lp)
            if press > 0:
                time.sleep(press)
            user32.SendMessageW(target, WM_LBUTTONUP, 0, lp)
            if interval > 0:
                time.sleep(interval)
        user32.SendMessageW(target, WM_MOUSEMOVE, 0, away_lp)
    return {"hwnd": target, "class": win_class(target), "button": "primary",
            "client": (cw, ch), "point": (cx, cy),
            "screen_point": (sx, sy), "times": n}


def begin_silent_hold(win, x=None, y=None, button="primary"):
    """投递长按按下序列（WM_MOUSEMOVE→WM_LBUTTONDOWN），保持按下状态。

    与 send_silent_click 不同，抬起由 end_silent_hold 延后投递——期间
    游戏内部按键状态保持按下（真实长按，钓鱼小游戏「长按」按钮）。
    本函数不持输入锁：调用方（app_utils.MouseHold）持锁跨越整个
    长按周期，防其他线程的点击/移出消息破坏按下状态。窗口/客户区
    获取失败返回 None。
    """
    msg = _BUTTON_MESSAGES.get(button)
    if msg is None:
        raise ValueError(f"不支持的按键类型: {button!r}（primary/secondary）")
    down, _up, mk = msg
    resolved = _resolve_point(win, x, y)
    if resolved is None:
        return None
    target, cx, cy, sx, sy, cw, ch = resolved
    lp = (cy << 16) | (cx & 0xFFFF)
    # WM_MOUSEMOVE 先投：部分客户端据此更新内部光标位置，缺它可能
    # 忽略后续按下（与 _post_mouse_click 同序）
    r_move = user32.SendMessageW(target, WM_MOUSEMOVE, 0, lp)
    time.sleep(0.05)
    r_down = user32.SendMessageW(target, down, mk, lp)
    return {"hwnd": target, "class": win_class(target),
            "button": button,
            "client": (cw, ch),
            "point": (cx, cy),
            "screen_point": (sx, sy),
            "lp": lp,
            "msg_results": (r_move, r_down)}


def end_silent_hold(info):
    """投递长按抬起序列（WM_LBUTTONUP→WM_MOUSEMOVE 移出中性区）。

    info 为 begin_silent_hold 的返回值（None 时无操作）；末尾移出
    与 send_silent_click 同理，防悬停高亮残留。返回消息返回值元组。
    """
    if not info:
        return ()
    msg = _BUTTON_MESSAGES.get(info.get("button", "primary"))
    if msg is None:
        return ()
    _down, up, _mk = msg
    target = info["hwnd"]
    lp = info["lp"]
    cw, ch = info["client"]
    away_lp = ((ch - 20) << 16) | 20
    r_up = user32.SendMessageW(target, up, 0, lp)
    time.sleep(0.05)
    r_away = user32.SendMessageW(target, WM_MOUSEMOVE, 0, away_lp)
    return (r_up, r_away)


def _post_mouse_click(target, cx, cy, down, up, mk, client_w, client_h):
    """向指定句柄投递一次鼠标点击序列（WM_MOUSEMOVE→按下→抬起→移开），
    持全局输入锁与物理鼠标点击互斥。cx/cy 为目标窗口客户区坐标。

    点击后虚拟光标移出：消息点击会把游戏内部光标位置留在点击点上，
    悬停高亮随之残留——对话框切换后新选项带悬停变色（黄色），干扰
    绿字判定（用户 2026-11-03 实测）。末尾投一条 WM_MOUSEMOVE 到客户区
    左下角中性区（与物理模式 move_away 等效目标）。返回四消息返回值。
    """
    lp = (cy << 16) | (cx & 0xFFFF)
    away_lp = ((client_h - 20) << 16) | 20
    with app_utils._INPUT_LOCK:
        # WM_MOUSEMOVE 先投：部分客户端据此更新内部光标位置，缺它可能
        # 忽略后续按下/抬起
        r_move = user32.SendMessageW(target, WM_MOUSEMOVE, 0, lp)
        time.sleep(0.05)
        r_down = user32.SendMessageW(target, down, mk, lp)
        time.sleep(0.05)
        r_up = user32.SendMessageW(target, up, 0, lp)
        time.sleep(0.05)
        r_away = user32.SendMessageW(target, WM_MOUSEMOVE, 0, away_lp)
    return (r_move, r_down, r_up, r_away)


def _frame(img):
    """PIL 帧转 RGB int16 数组（帧差统计用；int16 防相减溢出）。"""
    import numpy as np
    return np.asarray(img.convert("RGB"), dtype=np.int16)


def _diff_ratio(a, b):
    """两帧差异：变化像素占比（任一通道差 >_CHANGED_PX）与全通道均值差。"""
    import numpy as np
    d = np.abs(a - b)
    changed = float(np.mean(np.any(d > _CHANGED_PX, axis=2)) * 100)
    return changed, float(d.mean())


def _begin(win, log_fn, label):
    """解析点击/按键目标并写开头日志（主窗口/目标/当前前台）；
    返回 (main_hwnd, target)，失败返回 (None, None)。"""
    main_hwnd = int(getattr(win, "hwnd", 0) or 0)
    target = click_target(win)
    if not target:
        return None, None
    fg = user32.GetForegroundWindow()
    log_fn(f"==== 静默操作验证开始（{label}） ====")
    log_fn(f"游戏主窗口: hwnd={main_hwnd:#x} 类名={win_class(main_hwnd)} | "
           f"当前前台: {'游戏窗口' if fg in (main_hwnd, target) else '非游戏窗口（符合验证条件）'}")
    log_fn(f"投递目标: hwnd={target:#x} 类名={win_class(target)} "
           f"{'(Tab 子窗口)' if target != main_hwnd else '(主窗口)'}")
    return main_hwnd, target


def _log_delivery(log_fn, info_click, button, extra=""):
    """投递详情日志（extra 追加在点击点之后，如窗口坐标原文）。"""
    lr = "R" if button == "secondary" else "L"
    log_fn(f"已投递: WM_MOUSEMOVE→WM_{lr}BUTTONDOWN→WM_{lr}BUTTONUP | "
           f"客户区 {info_click['client'][0]}x{info_click['client'][1]} | "
           f"点击点 客户({info_click['point'][0]},{info_click['point'][1]}) "
           f"屏幕({info_click['screen_point'][0]},{info_click['screen_point'][1]}){extra} | "
           f"消息返回 {info_click['msg_results']}")


def _log_fg_after(log_fn, main_hwnd, target):
    """点击后前台状态佐证（静默点击不应把游戏置前）。"""
    fg_after = user32.GetForegroundWindow()
    log_fn(f"点击后前台: {'仍是游戏窗口' if fg_after in (main_hwnd, target)
            else f'未变（{win_class(fg_after)}，游戏未被置前）'}")


def send_and_report(win, log_fn, button="primary", x=None, y=None):
    """只投递一次静默点击并写日志（不做前后帧判定，结果由用户自行
    观察游戏效果——如静默回蓝：右键血蓝条位置）。

    x/y 为窗口坐标（human_click 同空间），None 时点画面中心。
    返回 (ok, info)：ok 为投递成功。
    """
    if button not in _BUTTON_MESSAGES:
        return False, f"不支持的按键类型: {button!r}（primary/secondary）"
    if x is not None:
        x = int(x)
    if y is not None:
        y = int(y)
    main_hwnd, target = _begin(win, log_fn, _BUTTON_NAMES[button])
    if not target:
        return False, "未找到可投递的游戏窗口"
    info_click = send_silent_click(win, x=x, y=y, button=button)
    if info_click is None:
        return False, "消息点击投递失败（获取目标窗口/客户区失败）"
    _log_delivery(log_fn, info_click, button,
                  extra=f" 窗口({x},{y})" if x is not None and y is not None else "")
    _log_fg_after(log_fn, main_hwnd, target)
    log_fn("==== 结束：已投递，请自行观察游戏效果 ====")
    if x is not None and y is not None:
        return True, f"已投递{_BUTTON_NAMES[button]}点击 窗口({x},{y})，请自行观察效果"
    return True, f"已投递{_BUTTON_NAMES[button]}点击（画面中心），请自行观察效果"


def _key_lparam(scan, down, alt=False):
    """键盘消息 lParam：位 16-23 扫描码、位 29 上下文（Alt 按下）、
    位 30 前态、位 31 抬起转换位、低 16 位重复计数 1。"""
    lp = ((scan & 0xFF) << 16) | 1
    if alt:
        lp |= 1 << 29
    if not down:
        lp |= (1 << 30) | (1 << 31)
    return lp


def send_silent_hotkey(win, modifiers, key, main_window=False, char=None):
    """向游戏窗口投递一次修饰键组合按键（如 Alt+C / Ctrl+Tab），
    不移动鼠标、不激活窗口。

    modifiers 仅支持 "alt"/"ctrl"（alt 组合走 WM_SYSKEY*，ctrl 组合
    走 WM_KEY*）；key 见 _KEY_CODES。序列：修饰键按下 → 主键按下 →
    （char 不为 None 时 WM_CHAR）→ 主键抬起 → 修饰键抬起（反向）。
    main_window=True 时投给主框架窗口而非 Tab 子窗口（客户端外壳
    功能如 Ctrl+Tab 切 Tab 可能在主窗口处理）。返回投递描述 dict；
    窗口获取失败返回 None。可行性待实测：游戏若以 GetKeyState/
    原始输入读键，消息投递无效（与旧客户端鼠标消息同类的风险）。
    """
    mods = tuple(modifiers or ())
    if any(m not in ("alt", "ctrl") for m in mods):
        raise ValueError(f"不支持的修饰键: {modifiers!r}（仅 alt/ctrl）")
    if key not in _KEY_CODES or key in ("alt", "ctrl"):
        raise ValueError(f"不支持的主键: {key!r}")
    target = (int(getattr(win, "hwnd", 0) or 0) if main_window
              else click_target(win))
    if not target:
        return None
    seq = []  # (消息, wParam, lParam)
    for m in mods:
        vk, sc = _KEY_CODES[m]
        seq.append((WM_SYSKEYDOWN if m == "alt" else WM_KEYDOWN,
                    vk, _key_lparam(sc, True)))
    vk, sc = _KEY_CODES[key]
    alt_combo = "alt" in mods
    seq.append((WM_SYSKEYDOWN if alt_combo else WM_KEYDOWN,
                vk, _key_lparam(sc, True, alt=alt_combo)))
    if char is not None:
        seq.append((WM_CHAR, int(char), _key_lparam(sc, True, alt=alt_combo)))
    seq.append((WM_SYSKEYUP if alt_combo else WM_KEYUP,
                vk, _key_lparam(sc, False, alt=alt_combo)))
    for m in reversed(mods):
        vk, sc = _KEY_CODES[m]
        seq.append((WM_SYSKEYUP if m == "alt" else WM_KEYUP,
                    vk, _key_lparam(sc, False)))
    results = []
    with app_utils._INPUT_LOCK:
        for msg, wparam, lparam in seq:
            results.append(user32.SendMessageW(target, msg, wparam, lparam))
            time.sleep(0.05)
    return {"hwnd": target, "class": win_class(target),
            "modifiers": list(mods), "key": key,
            "main_window": bool(main_window),
            "seq": [(hex(m), hex(w)) for m, w, _ in seq],
            "msg_results": tuple(results)}


def _hotkey_label(modifiers, key):
    """组合键显示文案（如「Alt+C」）。"""
    return "+".join([m.capitalize() for m in modifiers] + [key.upper()])


def send_and_report_key(win, log_fn, modifiers=("alt",), key="c",
                        main_window=False, char=None):
    """只投递一次静默按键并写日志（不做判定，结果用户自行观察——
    Alt+C 生效信号：人物降落/位置显示刷新）。返回 (ok, info)。"""
    label = f"按键 {_hotkey_label(modifiers, key)}"
    if main_window:
        label += "→主窗口"
    if char is not None:
        label += "+WM_CHAR"
    main_hwnd, target = _begin(win, log_fn, label)
    if not target:
        return False, "未找到可投递的游戏窗口"
    info_key = send_silent_hotkey(win, modifiers, key,
                                  main_window=main_window, char=char)
    if info_key is None:
        return False, "消息按键投递失败（获取目标窗口失败）"
    log_fn(f"已投递: {label} 消息序列 "
           f"{' → '.join(f'{m}({w})' for m, w in info_key['seq'])} | "
           f"消息返回 {info_key['msg_results']}")
    _log_fg_after(log_fn, main_hwnd, target)
    log_fn("==== 结束：已投递，请自行观察游戏效果 ====")
    return True, f"已投递{label}，请自行观察效果"


# ---- Tab 条静默切 Tab（SysTabControl32 标准控件） ----
# 多标签客户端（xy2_tab_x64）Tab 条是主窗口的直接子窗口、标准
# SysTabControl32 控件（2026-11-04 实机探测：主窗口客户区偏移 (16,30)、
# 尺寸 1362x23、每 Tab 174x23，Tab 名=角色名）。
# 切换通道（2026-11-04 实机验证生效）：UIA 读 Tab 项屏幕矩形
# （TabItemControl.BoundingRectangle，COM 封送跨进程安全）→ 换算
# Tab 条客户区坐标 → 投递 WM_LBUTTONDOWN+WM_LBUTTONUP 最小序列
# （无前置移动、无移出——Tab 条无中性区，移出会悬停到别的 Tab）。
# 【禁区】两条，均 2026-11-04 实测：
#   1) 不得投递带指针的消息（TCM_GETITEMRECT 等）：lParam 裸指针
#      跨进程不封送，游戏按我方虚拟地址在自身进程写内存，直接崩溃
#      退出（无 WER 记录，客户端内部处理）——当日首次「点击实验」
#      崩溃根因即此，点击消息本身从未送达
#   2) TCM_SETCURSEL 实测只动控件选中、游戏视图不切（客户端不响应
#      TCN_SELCHANGE），无切换价值
# TCM 消息仅用 int-only（GETITEMCOUNT/GETCURSEL）。
TCM_GETITEMCOUNT = 0x1304
TCM_GETCURSEL = 0x130B

# Tab 项缓存：main hwnd -> (count, 客户区尺寸, [(名字, 客户区矩形)×count])。
# 矩形在 UIA 读取时即换算成 Tab 条客户区坐标（减去控件窗口矩形原点）：
# UIA 给的是屏幕坐标，若直接缓存屏幕矩形，窗口一动缓存就过期——点击
# 按旧屏幕原点换算成负/越界客户区坐标，客户端不消费（2026-11-10 实测：
# 拖动游戏窗口后整轮静默切 Tab 全部「点击投递未被消费」）。客户区矩形
# 与窗口位置无关，移动不需要失效；控件客户区尺寸变化（窗口缩放/DPI
# 变化）才失效重读。Tab 条是固定 UI（会话内 Tab 数不变则布局不变，与
# 高性能缓存白名单同原则）；Tab 数变化同样失效。名字用于未登录过滤
# （未登录 Tab 名 = 游戏标题，已登录 = 角色名）。
_tab_rects_cache = {}


def tab_control_hwnd(win):
    """主窗口的 Tab 条控件句柄（SysTabControl32 直接子窗口；无则 None）。"""
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    if not hwnd:
        return None
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(h, _l):
        try:
            if user32.GetParent(h) != hwnd:
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(h, buf, 256)
            if buf.value == "SysTabControl32":
                found.append(int(h))
        except Exception:
            pass
        return True

    try:
        user32.EnumChildWindows(ctypes.c_void_p(hwnd), _cb, 0)
    except Exception:
        return None
    return found[0] if found else None


def tab_item_count(tab):
    """Tab 条 Tab 总数（TCM_GETITEMCOUNT；失败返回 0）。"""
    return int(user32.SendMessageW(tab, TCM_GETITEMCOUNT, 0, 0) or 0)


def tab_current_index(tab):
    """Tab 条当前选中序号（TCM_GETCURSEL；失败返回 -1）。"""
    return int(user32.SendMessageW(tab, TCM_GETCURSEL, 0, 0) or 0)


def _client_size(tab):
    """控件客户区尺寸 (宽, 高)；读取失败返回 None。"""
    cr = wintypes.RECT()
    if not user32.GetClientRect(tab, ctypes.byref(cr)):
        return None
    return (cr.right - cr.left, cr.bottom - cr.top)


def _item_center(rect):
    """Tab 项客户区矩形 → 中心点 (x, y)。"""
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def _uia_tab_items(win, count):
    """UIA 读 Tab 项 (名字, Tab 条客户区矩形) 列表（按 Tab 顺序，长度
    须 == count；失败 None）。

    UIA BoundingRectangle 是屏幕坐标，读取时立刻减去 Tab 条窗口矩形
    原点换算成客户区坐标——此后窗口怎么移动都不影响缓存值（2026-11-10
    拖动窗口后整轮切换失效的教训）。
    COM 封送跨进程安全（TCM_GETITEMRECT 裸指针不可用，2026-11-04
    崩溃教训）。线程内 COM 惰性初始化：调用方线程（任务/战斗线程）
    首次调用时 CoInitializeEx，实例随调用结束配对释放——与 main.py
    任务线程持有的常驻实例引用计数互补，重入安全。
    """
    import uiautomation as auto  # 惰性导入：仅切 Tab 路径需要
    try:
        tab = tab_control_hwnd(win)
        tr = wintypes.RECT()
        if not tab or not user32.GetWindowRect(tab, ctypes.byref(tr)):
            return None
        auto.UIAutomationInitializerInThread()
        root = auto.ControlFromHandle(int(getattr(win, "hwnd", 0) or 0))
        for c in root.GetChildren():
            if c.ControlTypeName == "TabControl":
                items = []
                for it in c.GetChildren():
                    br = it.BoundingRectangle
                    items.append((it.Name or "",
                                  (br.left - tr.left, br.top - tr.top,
                                   br.right - tr.left, br.bottom - tr.top)))
                return items if len(items) == count else None
        return None
    except Exception:
        return None


def tab_items(win, count):
    """Tab 项 (名字, Tab 条客户区矩形) 列表（带缓存；Tab 数变化、
    控件尺寸变化或无缓存时 UIA 重读）。

    缓存命中条件：Tab 数相同且 Tab 条客户区尺寸未变（窗口移动不影响
    客户区矩形，无需失效；缩放会改变 Tab 项布局，尺寸变化即重读）。
    长度必须与 count 一致才可用（UIA 读到的项数与 TCM 计数不一致
    视为失败——布局可能正在变化，宁可重试不点错 Tab）。名字供
    未登录过滤（未登录 Tab 名 = 游戏标题，见 tab_group）。
    """
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    tab = tab_control_hwnd(win)
    size = _client_size(tab) if tab else None
    cached = _tab_rects_cache.get(hwnd)
    if cached is not None and cached[0] == count and cached[1] == size:
        return cached[2]
    items = _uia_tab_items(win, count)
    if items and len(items) == count:
        _tab_rects_cache[hwnd] = (count, size, items)
        return items
    return None


def invalidate_tab_items(win):
    """丢弃 Tab 项缓存（切 Tab 反复未生效时由调用方触发 UIA 重读自愈）。"""
    try:
        _tab_rects_cache.pop(int(getattr(win, "hwnd", 0) or 0), None)
    except Exception:
        pass


def send_silent_tab_click(win, index):
    """向 Tab 条投递一次点击（第 index 个 Tab 的中心），等效真实点击
    切 Tab——无需前台、无需键盘、不移动物理鼠标。

    消息集最小化：仅 WM_LBUTTONDOWN→WM_LBUTTONUP（无前置移动、无
    移出——Tab 条无中性区，移出会悬停到别的 Tab 上）；2026-11-04
    实机验证：视图切换、客户端稳定。
    坐标：缓存里已是 Tab 条客户区矩形（见 _uia_tab_items），窗口移动
    不影响；中心落在客户区外 = 缓存与当前布局不符（缩放/DPI 变化），
    失效重读 UIA 一次，仍越界则放弃投递（防点错 Tab 切错账号）。
    序号越界/控件不存在/矩形获取失败返回 None；成功返回投递描述 dict。
    """
    tab = tab_control_hwnd(win)
    if not tab:
        return None
    count = tab_item_count(tab)
    if count <= 0 or index < 0 or index >= count:
        return None
    items = tab_items(win, count)
    if not items:
        return None
    cx, cy = _item_center(items[index][1])
    size = _client_size(tab)
    if size and not (0 <= cx < size[0] and 0 <= cy < size[1]):
        invalidate_tab_items(win)
        items = tab_items(win, count)
        if not items:
            return None
        cx, cy = _item_center(items[index][1])
        if not (0 <= cx < size[0] and 0 <= cy < size[1]):
            return None
    lp = (cy << 16) | (cx & 0xFFFF)
    with app_utils._INPUT_LOCK:
        r_down = user32.SendMessageW(tab, WM_LBUTTONDOWN, MK_LBUTTON, lp)
        time.sleep(0.08)
        r_up = user32.SendMessageW(tab, WM_LBUTTONUP, 0, lp)
    return {"hwnd": tab, "class": win_class(tab), "index": index,
            "point": (cx, cy), "msg_results": (r_down, r_up)}


def set_clipboard_text(text):
    """把文本写入系统剪贴板（CF_UNICODETEXT，ctypes 直调；
    失败抛异常由调用方兜底）。"""
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard 失败")
    try:
        user32.EmptyClipboard()
        data = str(text).encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("GlobalAlloc 失败")
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalUnlock(h)
            raise OSError("GlobalLock 失败")
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise OSError("SetClipboardData 失败")
    finally:
        user32.CloseClipboard()


def send_silent_paste(win, text):
    """静默粘贴：剪贴板写入 text → 向游戏窗口投递 WM_PASTE（标准
    EDIT 控件直接消费，比 Ctrl+V 键序更可行——游戏不消费
    WM_KEYDOWN/UP）。前提：游戏内输入框已打开且处于输入状态。
    返回投递描述 dict；失败返回 None。"""
    target = click_target(win)
    if not target:
        return None
    set_clipboard_text(text)
    with app_utils._INPUT_LOCK:
        r = user32.SendMessageW(target, WM_PASTE, 0, 0)
    return {"hwnd": target, "class": win_class(target),
            "text": str(text), "msg_results": (r,)}


def send_and_report_paste(win, log_fn, text="测试粘贴"):
    """只投递一次静默粘贴并写日志（不做判定，结果用户自行观察——
    游戏输入框出现「测试粘贴」即生效；前提：先手动点开游戏聊天
    输入框）。返回 (ok, info)。"""
    main_hwnd, target = _begin(win, log_fn, f"粘贴「{text}」")
    if not target:
        return False, "未找到可投递的游戏窗口"
    info_paste = send_silent_paste(win, text)
    if info_paste is None:
        return False, "消息粘贴投递失败（获取目标窗口失败）"
    log_fn(f"已投递: WM_PASTE（剪贴板已写入「{text}」）| "
           f"消息返回 {info_paste['msg_results']}")
    _log_fg_after(log_fn, main_hwnd, target)
    log_fn("==== 结束：已投递，请自行观察游戏输入框 ====")
    return True, f"已投递 WM_PASTE「{text}」，请自行观察输入框"


def send_silent_unichar(win, text):
    """向游戏窗口投递 WM_UNICHAR 字符序列（Unicode 字符注入）。

    标准 EDIT 控件逐字符插入并返回非零表示已消费；游戏自定义输入
    控件不保证。返回投递描述 dict（msg_results 为逐字符返回值，
    0 = 该字符未被消费）；窗口获取失败返回 None。
    """
    target = click_target(win)
    if not target:
        return None
    results = []
    with app_utils._INPUT_LOCK:
        for ch in str(text):
            results.append(int(user32.SendMessageW(target, WM_UNICHAR, ord(ch), 0)))
            time.sleep(0.05)
    return {"hwnd": target, "class": win_class(target),
            "text": str(text), "msg_results": tuple(results)}


def send_and_report_unichar(win, log_fn, text="测"):
    """只投递一次 WM_UNICHAR 字符序列并写日志（结果用户自行观察——
    游戏聊天输入框出现「测」即生效；前提：先手动点开聊天输入框）。
    返回 (ok, info)。"""
    label = f"WM_UNICHAR「{text}」"
    main_hwnd, target = _begin(win, log_fn, label)
    if not target:
        return False, "未找到可投递的游戏窗口"
    info_key = send_silent_unichar(win, text)
    if info_key is None:
        return False, "消息字符投递失败（获取目标窗口失败）"
    log_fn(f"已投递: {label} 逐字符消息返回 {info_key['msg_results']}"
           f"（非零=该字符已消费）")
    _log_fg_after(log_fn, main_hwnd, target)
    log_fn("==== 结束：已投递，请自行观察游戏输入框 ====")
    return True, f"已投递{label}，请自行观察输入框"


def send_silent_text(win, text, end_enter=False):
    """向游戏窗口投递文本字符序列（WM_CHAR 逐字符，wParam 为 UTF-16
    码元）。游戏自定义输入系统已实证消费 WM_CHAR（中文/数字/标点均
    直投，2026-11-03 用户实测「测」生效）。

    end_enter=True 时末尾追加 WM_CHAR(0x0D) 回车（发送/确认），
    追加前等待 0.3s 与物理路径输入落地时序一致。返回投递描述
    dict；窗口获取失败返回 None。
    """
    target = click_target(win)
    if not target:
        return None
    results = []
    with app_utils._INPUT_LOCK:
        for ch in str(text):
            results.append(int(user32.SendMessageW(target, WM_CHAR, ord(ch), 1)))
            time.sleep(0.05)
        if end_enter:
            time.sleep(0.3)  # 输入落地后回车（与物理路径同序）
            results.append(int(user32.SendMessageW(target, WM_CHAR, 0x0D, 1)))
    return {"hwnd": target, "class": win_class(target),
            "text": str(text), "end_enter": bool(end_enter),
            "msg_results": tuple(results)}


def send_silent_char(win, text):
    """向游戏窗口投递 WM_CHAR 字符序列（send_silent_text 别名，
    诊断按钮用；不追加回车）。"""
    return send_silent_text(win, text)


def send_and_report_char(win, log_fn, text="测"):
    """只投递一次 WM_CHAR 字符序列并写日志（结果用户自行观察——
    游戏聊天输入框出现「测」即生效；前提：先手动点开聊天输入框）。
    返回 (ok, info)。"""
    label = f"WM_CHAR「{text}」"
    main_hwnd, target = _begin(win, log_fn, label)
    if not target:
        return False, "未找到可投递的游戏窗口"
    info_key = send_silent_char(win, text)
    if info_key is None:
        return False, "消息字符投递失败（获取目标窗口失败）"
    log_fn(f"已投递: {label} 逐字符消息返回 {info_key['msg_results']}")
    _log_fg_after(log_fn, main_hwnd, target)
    log_fn("==== 结束：已投递，请自行观察游戏输入框 ====")
    return True, f"已投递{label}，请自行观察输入框"


def run_diag(win, log_fn, button="primary"):
    """静默点击可行性验证：截点击前帧 → 投递一次静默点击 → 截点击后帧对比。

    button：'primary' 左键 / 'secondary' 右键（右键点击游戏世界会弹
    操作菜单，同样是明确的生效信号）。逐行 log_fn(msg)（msg 无时间
    戳与级别，由调用方统一包装写入日志系统）。返回 (ok, info)：
    ok 为疑似生效，info 为一行结论文案。请同时直接观察游戏画面
    （左键看人物是否走向画面中心；右键看是否弹出操作菜单）做最终确认。
    """
    if button not in _BUTTON_MESSAGES:
        return False, f"不支持的按键类型: {button!r}（primary/secondary）"
    main_hwnd, target = _begin(win, log_fn, _BUTTON_NAMES[button])
    if not target:
        return False, "未找到可投递的游戏窗口"

    img0 = win.capture(force_activate=False)
    if img0 is None:
        return False, "截帧失败（窗口可能最小化或渲染异常）"

    info_click = send_silent_click(win, button=button)
    if info_click is None:
        return False, "消息点击投递失败（获取目标窗口/客户区失败）"
    _log_delivery(log_fn, info_click, button)
    _log_fg_after(log_fn, main_hwnd, target)

    time.sleep(_RESPONSE_INTERVAL)
    img1 = win.capture(force_activate=False)
    if img1 is None:
        return False, "点击后截帧失败"
    if img1.size != img0.size:
        img1 = img1.resize(img0.size)
    click_pct, click_mean = _diff_ratio(_frame(img0), _frame(img1))
    log_fn(f"点击前后画面差（间隔 {_RESPONSE_INTERVAL}s）: "
           f"变化像素 {click_pct:.2f}% 均值差 {click_mean:.1f}")

    if click_pct >= _RESPOND_THRESHOLD:
        verdict = f"点击后画面变化 {click_pct:.2f}%，疑似静默点击已生效"
        ok = True
    else:
        verdict = f"点击后画面几乎无变化（{click_pct:.2f}%），疑似未生效"
        ok = False
    log_fn(f"==== 验证结束: {verdict} ====")
    return ok, verdict
