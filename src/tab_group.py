"""组号专用（一窗口多 Tab 多账号）：队员自动挂战斗、巫医、补血补蓝。

机制（2026-10-31 实机验证，多标签客户端 xy2_tab_x64 系列）：
- 每个登录 Tab 对应主窗口一个 Win32Window 子窗口，标题含
  「角色名（ID：账号）」；当前 Tab 子窗口可见、其余隐藏——
  计数/登录判定/当前身份全部走句柄枚举（game.list_tab_windows），零 OCR
- 切换：Ctrl+Tab（Keyboard.key_hot 经前台窗口送达，切换前校验前台）；
  TCM_SETCURSEL 消息与 Alt+Tab 实测均无效（2026-10-31 探测）
- pass 期间 pass_active 置位：战斗线程与任务线程轮询见此标志跳过
  本轮处理（防误点队员屏幕/误判战斗结束）；pass 由单线程同步执行
- 护栏：Tab 条区域（窗口相对 y < 60）为鼠标禁入区，pass 点击经
  _guarded_click 过滤；切 Tab 只走 Ctrl+Tab，鼠标永不碰 Tab 条
- 激活条件（active()）：init_task 时开关开 + 已登录 Tab ≥2 + 队长
  ID 可解析，且当前任务模式仍为修罗/五倍——单号（已登录 Tab <2）
  或开关关闭时全部新代码路径不执行，行为与旧版完全一致
"""
import threading
import time

from PIL import Image
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import log, human_click, current_task_mode
from . import game  # 子窗口枚举（每个 Tab 一个 Win32Window）
from . import map as game_map  # 模板匹配快速接口
from . import status_bars  # 血蓝条检测回满（队员 pass 复用）
from . import task_tracker  # 对话框区域 OCR 与绿色选项定位
from .dialog_common import _dialog_text_present  # 巫医对话框文案模糊匹配

# ---- 时序与预算参数（秒） ----
TAB_SWITCH_SETTLE = 0.8    # Ctrl+Tab 后等待标题/可见性更新
TAB_CYCLE_GUARD = 10       # 切回队长/遍历队员的最大切换次数
NO_CLICK_ZONE_Y = 60       # Tab 条区域（窗口相对 y<60）：鼠标禁入区
AUTO_WAIT_FIRST = 5.0      # 首场战斗队员自动按钮等待上限（操作条轮询）
AUTO_WAIT_ROUTINE = 1.0    # 每 5 场例行队员自动按钮等待上限（多半已挂自动）
BARS_PASS_INTERVAL = 60.0  # 队员血蓝检查最小间隔（寻路中触发，节流）
WUYI_DIALOG_WAIT = 3.0     # 队员巫医对话框出现等待上限
WUYI_GONE_WAIT = 3.0       # 队员巫医对话框关闭确认上限
PASS_TOTAL_DEADLINE = 30.0  # 单次 pass 总时长上限（防异常拖垮主流程）

# ---- 状态（任务进程内共享） ----
_active = False        # 组号模式是否激活（init_task 置位）
_mode = ""             # 激活时的任务模式（active() 校验模式仍为修罗/五倍）
_leader_id = None      # 队长账号 ID（任务启动时可见 Tab 子窗口标题解析）
_tab_count = 0         # 窗口内 Tab 总数
_logged_count = 0      # 已登录 Tab 数
_members = 0           # 队员数（已登录 - 队长）
pass_active = False    # pass 进行中：战斗线程/任务线程轮询见此跳过处理
_last_bars_pass_at = 0.0  # 上次队员血蓝 pass 时间（节流）
_pass_lock = threading.Lock()  # pass 入口互斥：同一时刻至多一个 pass（双线程防撞车）


def _try_enter_pass():
    """pass 互斥入口：另一 pass 进行中则放弃本轮（返回 False）。

    战斗线程的自动 pass 与任务线程的巫医/血蓝 pass 理论上可能撞车
    （寻路中遇敌 → 战斗线程进场挂自动 pass，任务线程恰好在跑血蓝
    pass），双线程同时 Ctrl+Tab 会切乱 Tab——入口加锁，后到者放弃、
    等下一轮时机（每 5 场/60s 节流/下轮巫医）。
    """
    global pass_active
    with _pass_lock:
        if pass_active:
            return False
        pass_active = True
        return True


def _exit_pass():
    """pass 结束：清除抑制标志（与 _try_enter_pass 配对，finally 调用）。"""
    global pass_active
    with _pass_lock:
        pass_active = False


def init_task(win, mode, switch_on):
    """任务启动时初始化组号状态（开关开才枚举，一次）。

    激活条件：mode ∈ {修罗, 五倍} 且 switch_on 且已登录 Tab ≥2 且
    队长 ID 可解析（可见子窗口标题）。不满足时置不激活——单号
    （已登录 Tab <2）行为与旧版完全一致。枚举失败只告警不激活。
    """
    global _active, _mode, _leader_id, _tab_count, _logged_count, _members
    _active = False
    _mode = ""
    _leader_id = None
    _tab_count = 0
    _logged_count = 0
    _members = 0
    if mode not in ("修罗", "五倍") or not switch_on:
        return
    try:
        children = game.list_tab_windows(win)
    except Exception as e:
        log(f"[组号] 枚举子窗口失败: {e}", "WARN")
        return
    _tab_count = len(children)
    logged = [c for c in children if c["id"]]
    _logged_count = len(logged)
    if _logged_count < 2:
        log(f"[组号] 窗口内 {_tab_count} 个 Tab、已登录 {_logged_count} 个（<2），"
            f"组号模式不激活，按单号流程执行", "SYS")
        return
    visible = [c for c in children if c["visible"] and c["id"]]
    if not visible:
        log("[组号] 未识别到当前 Tab 身份（无可见已登录子窗口），组号模式不激活", "WARN")
        return
    _leader_id = visible[0]["id"]
    _members = _logged_count - 1
    _mode = mode
    _active = True
    log(f"[组号] 组号模式激活：{_tab_count} 个 Tab、已登录 {_logged_count} 个，"
        f"队长 ID {_leader_id}，队员 {_members} 个", "SYS")


def active():
    """组号模式是否激活（任务模式须仍为修罗/五倍）。

    多任务顺序执行时切到其他模式（如客栈）后不激活，防误切 Tab。
    """
    return _active and current_task_mode() == _mode


def ready():
    """组号基础条件是否具备（已登录 Tab ≥2 且队长 ID 可解析）。

    验证模式用：主进程没有任务模式（active() 需 current_task_mode
    匹配），验证时用 ready() + pass(force=True) 绕过模式校验。
    """
    return _leader_id is not None and _logged_count >= 2


def startup_leader_check(win):
    """组号模式启动检查：强制队长判定（即使「检查队伍」开关关闭）。

    战斗中先等战斗结束（队伍面板战斗中不可用，误报非队长会停任务）；
    确定性非队长（未组队/面板无解散按钮）立即返回；瞬态失败 3s 后
    重试一次。返回 (是否队长, 描述)。
    """
    from . import team  # 函数内导入：team 依赖 app_utils，避免循环依赖
    from .battle import is_in_battle  # 函数内导入，避免循环依赖
    deadline = time.time() + 90
    while is_in_battle(win) and time.time() < deadline:
        time.sleep(2.0)
    ok, info = team.is_team_leader(win)
    if ok:
        return True, info
    if team.is_definitive_not_leader(info):
        return False, info
    time.sleep(3)
    return team.is_team_leader(win)


# ---- 切换与身份 ----

def _current(win):
    """当前 Tab 的 (子窗口句柄, 账号 ID)：按可见子窗口判定，未登录 id=None。

    切 Tab 成败按句柄变化判定（未登录 Tab 无 ID，不能作判据）。
    """
    try:
        children = game.list_tab_windows(win)
    except Exception:
        return None, None
    for c in children:
        if c["visible"]:
            return c["hwnd"], c["id"]
    return None, None


def _current_id(win):
    """当前 Tab 的账号 ID（可见已登录子窗口标题解析），失败 None。"""
    return _current(win)[1]


def _ensure_foreground(win):
    """Ctrl+Tab 经前台窗口送达：激活游戏窗口并校验前台（同清屏链路）。"""
    try:
        win.activate()
        time.sleep(0.3)
        import win32gui
        return win32gui.GetForegroundWindow() == int(win.hwnd)
    except Exception as e:
        log(f"[组号] 前台检查异常: {e}", "WARN")
        return False


def _switch_next(win):
    """Ctrl+Tab 切到下一个 Tab。

    返回切换后当前 Tab 的账号 ID；空串 = 切换成功但该 Tab 未登录；
    None = 切换失败（按键未送达/前台丢失，重试一次后仍失败）。
    """
    before_hwnd, _before_id = _current(win)
    Keyboard.key_hot("ctrl", "tab")
    time.sleep(TAB_SWITCH_SETTLE)
    after_hwnd, after_id = _current(win)
    if after_hwnd is not None and after_hwnd != before_hwnd:
        return after_id if after_id is not None else ""
    if not _ensure_foreground(win):
        return None
    Keyboard.key_hot("ctrl", "tab")
    time.sleep(TAB_SWITCH_SETTLE)
    after_hwnd, after_id = _current(win)
    if after_hwnd is None or after_hwnd == before_hwnd:
        return None
    return after_id if after_id is not None else ""


def return_to_leader(win):
    """切回队长 Tab（Ctrl+Tab 循环直至当前 ID == 队长 ID）。

    只认 _leader_id（不查 active()——验证模式 force 走 pass 时
    _active 为 False，仍须切回队长）。
    """
    if _leader_id is None:
        return
    for _ in range(TAB_CYCLE_GUARD):
        if _current_id(win) == _leader_id:
            return
        if _switch_next(win) is None:
            log("[组号] 切回队长 Tab 失败（身份未确认）", "WARN")
            return
    log("[组号] 切回队长 Tab 超时（未在循环内命中队长）", "WARN")


def ensure_leader_tab(win):
    """确保当前在队长 Tab（pass 切回失败残留时的自愈护栏）。

    调用方（follow 循环/主循环每轮）在点击任何东西前先过此门：
    不在队长 Tab 就尝试切回，切不回返回 False、调用方本轮跳过——
    队长流程的点寻路/领任务/回程永远不会落在队员 Tab 上。
    未激活或无队长 ID 时返回 True（不干预单号流程）。
    """
    if not _active or _leader_id is None:
        return True
    if _current_id(win) == _leader_id:
        return True
    return_to_leader(win)
    return _current_id(win) == _leader_id


# ---- 护栏与队员遍历 ----

def _guarded_click(win, x, y, button="primary"):
    """pass 专用点击：Tab 条区域（y<60）为鼠标禁入区，落点进禁区跳过。

    防 OCR 抖动坐标漂移把点击落到 Tab 条上误切账号——切 Tab 只走
    Ctrl+Tab，鼠标永不碰 Tab 条。返回是否执行了点击。
    """
    if y < NO_CLICK_ZONE_Y:
        log(f"[组号] 点击落点 ({x},{y}) 进入 Tab 条禁入区，已跳过", "WARN")
        return False
    human_click(win, x, y, button=button)
    return True


def _for_each_member(win, handler, label, force=False):
    """遍历队员 Tab：Ctrl+Tab 切一整圈（_tab_count 次），逐个执行 handler。

    handler 签名 (win, tab_id)；未登录 Tab（切换成功但无 ID，
    cur == ""）跳过；绕回队长提前结束；handler 异常内部消化；
    整体受 PASS_TOTAL_DEADLINE 上限保护。结束切回队长。
    pass_active 由调用方置位/复位。
    force=True 供验证模式绕过 active() 检查（主进程无任务模式）。
    """
    if not _active and not force:
        return
    deadline = time.time() + PASS_TOTAL_DEADLINE
    for _ in range(_tab_count):
        if time.time() > deadline:
            log(f"[组号] {label} 超总时长上限，提前切回队长", "WARN")
            break
        cur = _switch_next(win)
        if cur is None:
            log(f"[组号] {label}：切 Tab 失败，中止", "WARN")
            break
        if cur == _leader_id:
            break  # 已绕回队长
        if cur == "":
            log(f"[组号] {label}：当前 Tab 未登录，跳过", "INFO")
            continue
        try:
            handler(win, cur)
        except Exception as e:
            log(f"[组号] {label}：队员 Tab（ID {cur}）处理异常: {e}", "WARN")
    return_to_leader(win)


# ---- 战斗自动 pass ----

def member_auto_pass(win, wait_auto=AUTO_WAIT_FIRST):
    """战斗自动 pass：队长点完自动后遍历队员 Tab 点击自动。

    触发时机跟随队长：首场挂自动后（wait_auto=5s，等队员操作条）、
    每 5 场例行确认后（wait_auto=1s，多半已挂自动跳过）。队员不在
    战斗中（单挑等）跳过；已挂自动时自动按钮不出现，轮询超时跳过。
    """
    global pass_active
    if not _active:
        return
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _ensure_foreground(win):
            log("[组号] 游戏窗口未在前台，跳过战斗自动 pass", "WARN")
            return
        _for_each_member(win, lambda w, cur: _auto_one(w, cur, wait_auto),
                         "战斗自动 pass")
    finally:
        _exit_pass()


def _auto_one(win, tab_id, wait_auto):
    """单个队员 Tab 的战斗自动：截一帧判战斗 → 等自动按钮出现即点。"""
    from . import battle  # 函数内导入：battle 顶层导入 tab_group，避免循环依赖
    deadline = time.time() + max(0.5, wait_auto)
    while time.time() < deadline:
        img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if img is None:
            time.sleep(0.5)
            continue
        if not battle.is_in_battle_frame(img):
            log(f"[组号] 队员 Tab（ID {tab_id}）不在战斗中，跳过自动", "INFO")
            return
        pt = game_map.match_frame_np(img, battle.AUTO_ICON,
                                     region=battle._bar_band(img), fallback=True)
        if pt is not None:
            if _guarded_click(win, pt[0], pt[1]):
                log(f"[组号] 队员 Tab（ID {tab_id}）已点击自动 @ ({pt[0]},{pt[1]})", "SYS")
            return
        time.sleep(0.5)
    log(f"[组号] 队员 Tab（ID {tab_id}）未识别到自动按钮（可能已挂自动），跳过", "INFO")


# ---- 巫医 pass ----

def member_wuyi_pass(win, force=False):
    """巫医 pass：队长点完全部医治后，逐个队员点击其自动弹出的巫医对话框。

    前置（用户确认）：队长点击巫医 NPC 后所有队员自动弹出对话框、
    不点不消失——队员不点 NPC、不检查血蓝（医治的是召唤兽忠诚度）、
    不重点任务追踪；对话框未弹出/选项未识别 → 超时跳过记日志。
    force=True 供验证模式绕过 active() 检查（主进程无任务模式）。
    """
    global pass_active
    if not _active and not force:
        return
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _ensure_foreground(win):
            log("[组号] 游戏窗口未在前台，跳过巫医 pass", "WARN")
            return
        _for_each_member(win, _wuyi_one, "巫医 pass", force=force)
    finally:
        _exit_pass()


def _wuyi_one(win, tab_id):
    """单个队员 Tab 的巫医对话框处理（出现→点全部医治和修复→消失）。"""
    from . import battle  # 函数内导入，避免循环依赖
    deadline = time.time() + WUYI_DIALOG_WAIT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if not any(_dialog_text_present(items, t) for t in battle.WUYI_DIALOG_TEXTS):
            time.sleep(0.5)
            continue
        pt = battle._find_wuyi_option(items, img_np)
        if pt is None:
            # 选项文字可能很小（实测字高仅 13px），对话框区 2x 放大重识别
            # （同 battle._handle_wuyi_dialog 兜底）
            l, t, r, b = task_tracker.DIALOG_REGION
            crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                                 Image.LANCZOS)
            items2, _ = game_map.ocr_zoom(crop)
            mapped = [(txt, [l + rect[0] // 2, t + rect[1] // 2,
                             l + rect[2] // 2, t + rect[3] // 2], score)
                      for txt, rect, score in items2]
            pt = battle._find_wuyi_option(mapped, img_np)
        if pt is not None:
            if _guarded_click(win, pt[0], pt[1]):
                log(f"[组号] 队员 Tab（ID {tab_id}）已点击「全部医治和修复」"
                    f" @ ({pt[0]},{pt[1]})", "SYS")
            _wait_dialog_gone(win, tab_id)
            return
        time.sleep(0.5)  # 对话框在但选项未识别：继续轮询
    log(f"[组号] 队员 Tab（ID {tab_id}）巫医对话框未出现/选项未识别，跳过", "WARN")


def _wait_dialog_gone(win, tab_id):
    """等待队员巫医对话框关闭（选项点击生效确认）。"""
    from . import battle  # 函数内导入，避免循环依赖
    deadline = time.time() + WUYI_GONE_WAIT
    while time.time() < deadline:
        time.sleep(0.5)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if not any(_dialog_text_present(items, t) for t in battle.WUYI_DIALOG_TEXTS):
            return
    log(f"[组号] 队员 Tab（ID {tab_id}）巫医对话框未确认关闭", "WARN")


# ---- 回血回蓝 pass ----

def member_bars_pass(win):
    """回血回蓝 pass：队长点完任务追踪后（寻路中）触发，60s 节流。

    逐个队员切过去检查血蓝条：战斗中跳过（战斗中不右键血蓝条）；
    低于阈值右键回满（复用 status_bars.check_all_bars，阈值沿用
    通用设置）。队长自己的战后补血蓝不受影响（战斗线程原逻辑）。
    """
    global pass_active, _last_bars_pass_at
    if not _active:
        return
    now = time.time()
    if now - _last_bars_pass_at < BARS_PASS_INTERVAL:
        return
    _last_bars_pass_at = now
    if not _try_enter_pass():
        return  # 另一 pass 进行中：放弃本轮，等下一轮时机
    try:
        if not _ensure_foreground(win):
            log("[组号] 游戏窗口未在前台，跳过回血回蓝 pass", "WARN")
            return
        _for_each_member(win, _bars_one, "回血回蓝 pass")
    finally:
        _exit_pass()


def _bars_one(win, tab_id):
    """单个队员 Tab 的血蓝检查（战斗中跳过）。"""
    from . import battle  # 函数内导入，避免循环依赖
    img = win.capture(force_activate=False)
    if img is not None and battle.is_in_battle_frame(img):
        log(f"[组号] 队员 Tab（ID {tab_id}）在战斗中，跳过血蓝检查", "INFO")
        return
    log(f"[组号] 检查队员 Tab（ID {tab_id}）血蓝", "INFO")
    status_bars.check_all_bars(win)
