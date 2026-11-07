"""飞行驭器（座骑）面板开合与飞行状态操作（库模块，供任务模块调用）。

识别方式（用户 2026-11-07 提供，模板见 res/img）：
1. 座骑按钮 zuoqi.png（33x33）点击弹出菜单（与组队按钮 zudui 同款
   pop menu 交互，开关式：再点一次关闭菜单）
2. 菜单「飞行驭器」zuoqi_feixingyuqi.png（115x21）点击打开飞行驭器
   面板
3. 面板出现 = feixingyuqi_panel.png（30x213 竖排「飞行」标题条）命中
4. 关闭 = 面板关闭按钮 feixingyuqi_panel_close.png（24x21 X 样式）点击；
   与队伍面板关闭按钮 zudui_close 同款（实测互匹配 0.984）——关闭前
   必须先校验飞行驭器面板打开，防止点到其他面板的同款关闭按钮
5. 高性能模式：Alt+U 直接打开面板（等效 1+2，免识别点击；静默操作
   走消息投递，物理路径热键只进前台窗口）
6. 飞行状态（面板内，2026-11-07）：出现「飞行」feixingyuqi_feixing
   （55x21）= 未飞行，点击起飞；出现「降落」feixingyuqi_jiangluo
   （54x20）= 飞行中，点击降落。两按钮同款样式，判定走双模板比分数
   （各自达标且更高者胜，禁止单模板阈值判定）；起飞/降落后按钮互
   换，面板保持打开（校验以按钮互换为准）

约束：飞行驭器只能非战斗中使用（open/ensure_* 入口判战斗，战斗中拒绝）。

实机验证（2026-11-07，1378x831 帧）：面板开时 zuoqi 0.998、
feixingyuqi_panel 1.000、feixingyuqi_panel_close 1.000（zudui_close
在同位置 0.984）；飞行中帧 feixingyuqi_jiangluo 1.000、
feixingyuqi_feixing 0.610（非目标帧低于阈值）；面板关时
zuoqi_feixingyuqi 0.360。合成帧校准：feixing/jiangluo 互匹配
0.548/0.549、噪声 ≤0.152，0.8 阈值干净分开。
"""
import time

from ascript.windows.hardware.keyboard import Keyboard

from .app_utils import log, human_click
from . import app_utils  # 高性能开关/静默开关（high_perf_on/silent_mode_on/keyboard_ok）
from . import map as game_map  # 找图能力复用
from .battle import is_in_battle  # 战斗状态判定（仅非战斗可用）

# ---- 模板（res/img，用户 2026-11-07 提供） ----
ZUOQI_ICON = "zuoqi.png"                        # 座骑按钮（33x33，点击弹出菜单）
ZUOQI_FEIYINGYUQI_ICON = "zuoqi_feixingyuqi.png"  # 弹出菜单「飞行驭器」项（115x21）
FEIYINGYUQI_PANEL_ICON = "feixingyuqi_panel.png"  # 飞行驭器面板标志（30x213 竖排标题条）
FEIYINGYUQI_PANEL_CLOSE_ICON = "feixingyuqi_panel_close.png"  # 面板关闭按钮（24x21 X）
FEIYINGYUQI_FEIXING_ICON = "feixingyuqi_feixing.png"    # 面板「飞行」按钮（55x21，
                                                        # 在场=未飞行，点击起飞）
FEIYINGYUQI_JIANGLUO_ICON = "feixingyuqi_jiangluo.png"  # 面板「降落」按钮（54x20，
                                                        # 在场=飞行中，点击降落）

# ---- 等待参数（与 team.py 同款节奏） ----
_TEMPLATE_THRESHOLD = 0.8
_MENU_WAIT_TRIES = 3       # 座骑按钮点击后菜单弹出等待
_MENU_WAIT_INTERVAL = 0.5
_PANEL_WAIT_TRIES = 8      # 面板打开/关闭等待（面板为服务器数据，稍慢）
_PANEL_WAIT_INTERVAL = 0.5
_FLIGHT_WAIT_TRIES = 8     # 起飞/降落后按钮互换等待
_FLIGHT_WAIT_INTERVAL = 0.5


# ---- 内部流程 ----

def _panel_open_in(img):
    """帧内判定飞行驭器面板是否已打开（feixingyuqi_panel 模板）。"""
    if img is None:
        return False
    return game_map.find_template_in(img, FEIYINGYUQI_PANEL_ICON,
                                     _TEMPLATE_THRESHOLD) is not None


def is_feixingyuqi_panel_open(win):
    """飞行驭器面板是否已打开（截帧判定）。"""
    return _panel_open_in(win.capture(force_activate=False))


def _wait_panel(win):
    """等待飞行驭器面板出现（feixingyuqi_panel 模板）。"""
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if is_feixingyuqi_panel_open(win):
            return True
    return False


def _menu_open_in(img):
    """帧内判定座骑弹出菜单是否已打开（菜单项「飞行驭器」可见）。"""
    if img is None:
        return False
    return game_map.find_template_in(img, ZUOQI_FEIYINGYUQI_ICON,
                                     _TEMPLATE_THRESHOLD) is not None


def _press_alt_u(win):
    """按 Alt+U 打开飞行驭器面板：静默操作走消息投递（游戏窗口无需
    前台），否则物理热键（热键只进前台窗口）。返回是否已投递。

    静默投递异常（如按键表缺键 ValueError）按未投递返回，不向外抛——
    任务流程不得因按键通道问题崩溃（2026-11-07 静默 Alt+U 缺键
    ValueError 打崩五环进店前降落检查的教训）。"""
    try:
        if app_utils.silent_mode_on():
            from . import silent_click  # 延迟导入：避免模块级循环
            return silent_click.send_silent_hotkey(win, ("alt",), "u") is not None
        if not app_utils.keyboard_ok(win):
            return False
        Keyboard.key_hot("alt", "u")
        return True
    except Exception as e:
        log(f"[飞行驭器] Alt+U 按键投递异常: {e}", "WARN")
        return False


def _close_menu_if_open(win):
    """菜单仍打开时再点一次座骑按钮关闭（开关式，同组队菜单经验）。
    返回 (是否已关闭, 描述)。"""
    img = win.capture(force_activate=False)
    if img is None or not _menu_open_in(img):
        return True, "菜单本就未打开"
    pt = game_map.find_template_in(img, ZUOQI_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "菜单仍打开且未定位到座骑按钮"
    human_click(win, pt[0], pt[1])
    return True, "座骑菜单已关闭"


# ---- 面板开关 ----

def open_feixingyuqi_panel(win):
    """打开飞行驭器面板：仅非战斗可用（战斗中拒绝，飞行驭器约束）。

    高性能：Alt+U 直接打开（等效 座骑按钮 → 菜单「飞行驭器」两步，
    免识别点击）；识别路径：zuoqi 座骑按钮点击 → 弹出菜单（与组队
    按钮同款交互）→ zuoqi_feixingyuqi 菜单项点击 → 面板出现校验。
    面板已打开时直接复用（开关式交互，重复点击会关闭——与组队菜单
    同款经验，必须先检查面板状态）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中不可使用飞行驭器"
    if is_feixingyuqi_panel_open(win):
        return True, "飞行驭器面板已打开（复用）"
    if app_utils.high_perf_on():
        if not _press_alt_u(win):
            return False, "Alt+U 按键未投递（游戏不在前台或静默通道失败）"
        log("[飞行驭器] 高性能模式：已按 Alt+U，等待面板打开", "SYS")
        if not _wait_panel(win):
            return False, "按 Alt+U 后飞行驭器面板未出现"
        return True, "飞行驭器面板已打开（Alt+U）"
    # 识别路径：点击座骑按钮弹出菜单
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    pt = game_map.find_template_in(img, ZUOQI_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "未找到座骑按钮（zuoqi）"
    human_click(win, pt[0], pt[1])
    log(f"[飞行驭器] 已点击座骑按钮 @ ({pt[0]},{pt[1]})，等待菜单弹出", "SYS")
    menu_img = None
    for _ in range(_MENU_WAIT_TRIES):
        time.sleep(_MENU_WAIT_INTERVAL)
        menu_img = win.capture(force_activate=False)
        if menu_img is not None and _menu_open_in(menu_img):
            break
    if menu_img is None or not _menu_open_in(menu_img):
        return False, "点击座骑按钮后未出现菜单（zuoqi_feixingyuqi）"
    pt2 = game_map.find_template_in(menu_img, ZUOQI_FEIYINGYUQI_ICON,
                                    _TEMPLATE_THRESHOLD)
    if pt2 is None:
        _close_menu_if_open(win)
        return False, "菜单中未定位到「飞行驭器」项（zuoqi_feixingyuqi）"
    human_click(win, pt2[0], pt2[1])
    log(f"[飞行驭器] 已点击菜单「飞行驭器」 @ ({pt2[0]},{pt2[1]})，等待面板打开", "SYS")
    if not _wait_panel(win):
        # 面板未出现：菜单可能仍残留，关闭防遮挡后续流程（开关式，
        # 与组队菜单同款经验）
        _close_menu_if_open(win)
        return False, "点击「飞行驭器」后飞行驭器面板未出现"
    return True, "飞行驭器面板已打开"


def close_feixingyuqi_panel(win):
    """关闭飞行驭器面板：点击面板关闭按钮 feixingyuqi_panel_close，
    校验面板消失。

    关闭按钮与队伍面板关闭按钮同款（实测互匹配 0.984），必须先校验
    飞行驭器面板打开再点击，防止点到其他面板的同款关闭按钮。点击后
    校验面板消失（feixingyuqi_panel 不再命中）；仍可见报失败不盲点。
    返回 (是否成功, 描述)。
    """
    if not is_feixingyuqi_panel_open(win):
        return True, "飞行驭器面板本就未打开"
    pt = game_map.find_template(win, FEIYINGYUQI_PANEL_CLOSE_ICON,
                                _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "未找到面板关闭按钮（feixingyuqi_panel_close）"
    human_click(win, pt[0], pt[1])
    log(f"[飞行驭器] 已点击面板关闭按钮 @ ({pt[0]},{pt[1]})", "SYS")
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if not is_feixingyuqi_panel_open(win):
            return True, "飞行驭器面板已关闭"
    return False, "飞行驭器面板关闭失败（面板仍可见）"


# ---- 飞行状态识别与起飞/降落（面板内，2026-11-07） ----

def _classify_flight_frame(img):
    """帧内飞行状态三分类（双模板比分数，同帧完成两模板匹配）。

    飞行/降落两按钮同款样式会互误匹配（合成帧实测互匹配 0.548/0.549，
    实机帧对方按钮 0.610），必须比分数区分（队伍解散/离开按钮教训）。
    返回 (状态, (飞行分, 飞行坐标), (降落分, 降落坐标))：
    - flying 飞行中：降落按钮达标且更高
    - ground 未飞行：飞行按钮达标且更高
    - unclear 两按钮均不达标（面板未打开/数据渲染中/帧异常）
    """
    fs, fp = game_map.best_match_in(img, FEIYINGYUQI_FEIXING_ICON)
    js, jp = game_map.best_match_in(img, FEIYINGYUQI_JIANGLUO_ICON)
    if js >= _TEMPLATE_THRESHOLD and js > fs:
        return "flying", (fs, fp), (js, jp)
    if fs >= _TEMPLATE_THRESHOLD and fs > js:
        return "ground", (fs, fp), (js, jp)
    return "unclear", (fs, fp), (js, jp)


def _classify_current(win, retries=1):
    """截帧分类当前飞行状态；unclear 时按 0.5s 间隔重截复核
    （面板数据渲染中，单帧抖动不算确定性状态）。返回同
    _classify_flight_frame 的三元组。"""
    img = win.capture(force_activate=False)
    if img is None:
        return "unclear", (0.0, (0, 0)), (0.0, (0, 0))
    state, f, j = _classify_flight_frame(img)
    for _ in range(retries):
        if state != "unclear":
            break
        time.sleep(0.5)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        state, f, j = _classify_flight_frame(img)
    return state, f, j


def flight_state(win):
    """识别当前是否在飞行中（面板内双模板比分数，一帧完成）。

    返回 (状态, 描述)：flying 飞行中（降落按钮在场）/ ground 未飞行
    （飞行按钮在场）/ unclear 面板未打开或两按钮均未命中。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return "unclear", "窗口截图失败"
    state, f, j = _classify_flight_frame(img)
    if state == "flying":
        return "flying", f"飞行中（降落按钮在场，降落分 {j[0]:.2f}）"
    if state == "ground":
        return "ground", f"未飞行（飞行按钮在场，飞行分 {f[0]:.2f}）"
    return "unclear", (f"面板内飞行/降落按钮均未命中"
                       f"（飞行分 {f[0]:.2f}、降落分 {j[0]:.2f}）")


def ensure_flying(win):
    """确保飞行中：面板打开 → 未飞行点击「飞行」按钮起飞 → 校验转飞行中。

    已在飞行中直接返回成功；面板未打开先打开（识别路径/高性能 Alt+U
    复用，仅非战斗可用）；按钮未命中按面板渲染中复核一帧，仍不明报
    失败不点击（绝不盲点面板空白处）。起飞后校验按钮互换（降落按钮
    出现），面板保持打开（关闭交调用方）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中不可使用飞行驭器"
    ok, info = open_feixingyuqi_panel(win)
    if not ok:
        return False, info
    state, f, j = _classify_current(win)
    if state == "flying":
        return True, "已在飞行中（无需起飞）"
    if state != "ground":
        return False, (f"面板内未定位到飞行按钮（飞行分 {f[0]:.2f}、"
                       f"降落分 {j[0]:.2f}）")
    human_click(win, f[1][0], f[1][1])
    log(f"[飞行驭器] 已点击飞行按钮 @ ({f[1][0]},{f[1][1]})，等待起飞", "SYS")
    for _ in range(_FLIGHT_WAIT_TRIES):
        time.sleep(_FLIGHT_WAIT_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        state2, _f2, j2 = _classify_flight_frame(img)
        if state2 == "flying":
            return True, f"已起飞（降落按钮出现，降落分 {j2[0]:.2f}）"
    return False, "点击飞行按钮后未确认起飞（降落按钮未出现）"


def ensure_landed(win):
    """确保降落：面板打开 → 飞行中点击「降落」按钮 → 校验转未飞行。

    与 ensure_flying 镜像（见其说明）；降落后校验按钮互换（飞行按钮
    出现），面板保持打开。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中不可使用飞行驭器"
    ok, info = open_feixingyuqi_panel(win)
    if not ok:
        return False, info
    state, f, j = _classify_current(win)
    if state == "ground":
        return True, "已降落（无需降落）"
    if state != "flying":
        return False, (f"面板内未定位到降落按钮（飞行分 {f[0]:.2f}、"
                       f"降落分 {j[0]:.2f}）")
    human_click(win, j[1][0], j[1][1])
    log(f"[飞行驭器] 已点击降落按钮 @ ({j[1][0]},{j[1][1]})，等待降落", "SYS")
    for _ in range(_FLIGHT_WAIT_TRIES):
        time.sleep(_FLIGHT_WAIT_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        state2, f2, _j2 = _classify_flight_frame(img)
        if state2 == "ground":
            return True, f"已降落（飞行按钮出现，飞行分 {f2[0]:.2f}）"
    return False, "点击降落按钮后未确认降落（飞行按钮未出现）"


def self_test(win):
    """实机自检：打开面板 → 校验 → 关闭面板（供实机验证/诊断使用）。

    仅非战斗可用；主进程无高性能开关（恒识别路径）。返回描述。
    """
    log("[飞行驭器] ========== 实机自检开始 ==========", "SYS")
    ok, info = open_feixingyuqi_panel(win)
    log(f"[飞行驭器] 1.打开面板: {ok} - {info}", "SYS")
    if not ok:
        log("[飞行驭器] ========== 实机自检结束 ==========", "SYS")
        return f"自检失败: {info}"
    ok, info = close_feixingyuqi_panel(win)
    log(f"[飞行驭器] 2.关闭面板: {ok} - {info}", "SYS")
    log("[飞行驭器] ========== 实机自检结束 ==========", "SYS")
    return f"自检完成: {info}"
