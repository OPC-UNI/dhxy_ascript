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
"""
import ctypes
import time
from ctypes import wintypes

from . import app_utils  # 全局输入锁：与物理鼠标点击互斥
from . import game  # 多标签客户端：点击目标优先可见的 Tab 子窗口

user32 = ctypes.WinDLL("user32", use_last_error=True)

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
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int


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
    lp = (cy << 16) | (cx & 0xFFFF)
    # 点击后虚拟光标移出：消息点击会把游戏内部光标位置留在点击点上，
    # 悬停高亮随之残留——对话框切换后新选项带悬停变色（黄色），干扰
    # 绿字判定（用户 2026-11-03 实测）。投一条 WM_MOUSEMOVE 到客户区
    # 左下角中性区（与物理模式 move_away 等效目标）。
    away_lp = ((cl.bottom - 20) << 16) | 20
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
    return {"hwnd": target, "class": win_class(target),
            "button": button,
            "client": (cl.right, cl.bottom),
            "point": (cx, cy),
            "screen_point": (sx, sy),
            "msg_results": (r_move, r_down, r_up, r_away)}


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


def _begin(win, log_fn, button):
    """解析点击目标并写开头日志（主窗口/点击目标/当前前台）；
    返回 (main_hwnd, target)，失败返回 (None, None)。"""
    main_hwnd = int(getattr(win, "hwnd", 0) or 0)
    target = click_target(win)
    if not target:
        return None, None
    fg = user32.GetForegroundWindow()
    log_fn(f"==== 静默点击验证开始（{_BUTTON_NAMES[button]}） ====")
    log_fn(f"游戏主窗口: hwnd={main_hwnd:#x} 类名={win_class(main_hwnd)} | "
           f"当前前台: {'游戏窗口' if fg in (main_hwnd, target) else '非游戏窗口（符合验证条件）'}")
    log_fn(f"点击目标: hwnd={target:#x} 类名={win_class(target)} "
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
    main_hwnd, target = _begin(win, log_fn, button)
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
    main_hwnd, target = _begin(win, log_fn, button)
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
