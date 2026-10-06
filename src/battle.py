"""战斗自动化：进入战斗后按配置自动执行人物/召唤兽操作。

战斗状态检测（模板匹配多信号判定，不用 OCR，防止非战斗画面误报）：
- 操作条按钮（法术/防御/自动）：战斗专属 UI，误报率最低（非战斗实测 ≤0.39，
  真实按钮 1.0，阈值 0.85）
- round_banner.png：回合横幅「回合」文字区（第X回合任意回合均匹配，
  非战斗实测 ≤0.45，真实横幅 0.99，阈值 0.8），覆盖操作条隐藏的回合间隙
- battle_first.png 即「第1回合」横幅（用于判断是否第 1 回合）

战斗状态判定统一在本模块实现，其他模块（玄枢轶闻等）复用 is_in_battle。

流程：
1. 战斗开始（回合横幅可见）→ 等待操作条出现
2. 若同时存在「取消自动」「恢复自动」按钮（上局遗留的自动战斗），
   先点击取消自动，恢复手动控制
3. 判断当前回合：
   - 第 1 回合（battle_first 匹配）：按配置执行人物操作、召唤兽操作
     （先人物后召唤兽）
   - 非第 1 回合：直接点击自动按钮，剩余回合交由游戏自动战斗
4. 战斗中不检查血蓝条；战斗结束（回合横幅消失）后再检查，低于阈值右键回满

人物/召唤兽操作（第 1 回合）：
- 攻击：直接点击攻击按钮位置
- 防御：识别 fangyu 模板并点击
- 技能C-R：识别 fashu 模板点击打开技能选择面板 → 点击对应技能
  → 鼠标移动到攻击按钮位置再点击一次
- 无 / 捕捉 / 逃跑：不操作（捕捉与逃跑暂无按钮模板，仅日志提示），
  等待倒计时自动结束本阶段

技能网格规律（用户实测 5 个锚点）：面板 3 列 × 5 行，
「技能C-R」= 第 C 列、第 R 行：
    x = 512 + 150 * (C - 1)   （锚点 512 / 662 / 815）
    y = 300 + 30 * (R - 1)    （锚点 300 / 420）
召唤兽技能坐标用户尚未提供，先留 BEAST_SKILL_POS 占位。
"""
import re
import time

from .app_utils import log, human_click, human_move
from . import map as game_map
from . import status_bars  # 血蓝条检测回满（战斗结束后调用）
from .control_window import load_battle_actions

# ---- 战斗 UI 坐标与模板（窗口相对坐标，用户实测） ----
ATTACK_POS = (366, 415)        # 攻击按钮位置；技能选择后也在此位置再点一次
SKILL_GRID_ORIGIN = (512, 300)  # 人物技能网格原点（技能1-1 位置）
SKILL_GRID_STEP = (150, 30)     # 列/行间隔（3 列 × 5 行）
FASHU_ICON = "fashu.png"       # 法术按钮模板（用户提供 15.png）
FANGYU_ICON = "fangyu.png"     # 防御按钮模板（用户提供 16.png）
AUTO_ICON = "auto.png"         # 自动按钮模板（用户提供 17.png）
AUTO_CANCEL_ICON = "auto_cancel.png"    # 取消自动按钮模板（用户提供 18.png）
AUTO_RESUME_ICON = "auto_resume.png"    # 恢复自动按钮模板（用户提供 19.png）
BATTLE_FIRST_ICON = "battle_first.png"  # 「第1回合」横幅模板（第 1 回合才匹配）
ROUND_BANNER_ICON = "round_banner.png"  # 回合横幅「回合」文字区（任意回合均匹配）
ROUND_BANNER_THRESHOLD = 0.8   # 横幅文字匹配阈值（非战斗画面实测最高 0.45，真实横幅 0.99）
BAR_BUTTON_THRESHOLD = 0.85    # 操作条按钮匹配阈值（非战斗画面实测最高 0.39，真实按钮 1.0）

# 召唤兽技能坐标（用户稍后提供）：技能1~技能5 在技能面板中的窗口相对坐标
BEAST_SKILL_POS = {
    "技能1": (588, 300),
    "技能2": (588, 330),
    "技能3": (588, 360),
    "技能4": (588, 390),
    "技能5": (588, 420),
}

# ---- 时序参数（秒） ----
WATCH_POLL_INTERVAL = 1.0   # 战斗监控轮询间隔
BAR_POLL_INTERVAL = 0.5     # 操作条出现/消失轮询间隔
ROUND_BAR_TIMEOUT = 60      # 等待新回合操作条出现（含回合动画时间）
PHASE_SWITCH_DELAY = 2.0    # 点击操作后操作条关闭、召唤兽条出现
BEAST_BAR_TIMEOUT = 10      # 等待召唤兽操作条出现
IDLE_COUNTDOWN_WAIT = 40    # 无操作时等待游戏倒计时结束（游戏约 30 秒）
BAR_GONE_TIMEOUT = 10       # 操作后等待操作条关闭
SKILL_PANEL_DELAY = 1.0     # 点击法术后等待技能选择面板打开
SKILL_CONFIRM_DELAY = 0.5   # 点击技能后等待面板收起
AUTO_CANCEL_SETTLE = 1.5    # 点击取消自动后等待操作条恢复
BATTLE_END_POLL = 2.0       # 等待战斗结束的轮询间隔

_SKILL_RE = re.compile(r"^技能(\d)-(\d)$")

# 暂未支持的操作（无按钮模板/坐标），仅日志提示
_UNSUPPORTED_ACTIONS = ("捕捉", "逃跑")


def is_in_battle(win):
    """判断是否处于战斗中（多信号判定，防止非战斗画面误报）。

    操作条按钮（法术/防御/自动）是战斗专属 UI，出现即战斗操作阶段，
    误报率最低；回合横幅「回合」文字覆盖操作条隐藏的回合间隙。
    单次截图多模板匹配（旧实现每个模板各自截窗，4 次整窗截图实测
    233ms，单帧共用 129ms），模板、阈值与短路顺序不变。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    return is_in_battle_frame(img)


def is_in_battle_frame(img):
    """在已捕获帧上判断是否战斗中（模板、阈值与顺序同 is_in_battle）。

    供轮询循环复用本帧已抓的截图（如 follow_task 内层），避免重复截窗。
    """
    for icon in (FASHU_ICON, FANGYU_ICON, AUTO_ICON):
        if game_map.find_template_in(img, icon, threshold=BAR_BUTTON_THRESHOLD) is not None:
            return True
    return game_map.find_template_in(img, ROUND_BANNER_ICON,
                                     threshold=ROUND_BANNER_THRESHOLD) is not None


def is_round_1(win):
    """判断当前是否第 1 回合（battle_first 模板即「第1回合」横幅）。"""
    return game_map.find_template(win, BATTLE_FIRST_ICON) is not None


def char_skill_pos(action):
    """解析「技能C-R」为人物技能网格坐标；不合法返回 None。"""
    m = _SKILL_RE.match(action)
    if not m:
        return None
    col, row = int(m.group(1)), int(m.group(2))
    if not (1 <= col <= 3 and 1 <= row <= 5):
        return None
    return (SKILL_GRID_ORIGIN[0] + SKILL_GRID_STEP[0] * (col - 1),
            SKILL_GRID_ORIGIN[1] + SKILL_GRID_STEP[1] * (row - 1))


def _park_mouse(win):
    """将鼠标移回窗口中间，避免悬停在操作条上遮挡召唤兽操作/高亮按钮。"""
    human_move(win.rect.left + win.rect.width // 2,
               win.rect.top + win.rect.height // 2)


def _wait_icon(win, icon, timeout, want_found=True):
    """轮询模板出现（want_found=True）或消失（False），战斗结束提前返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_in_battle(win):
            return False
        if (game_map.find_template(win, icon) is not None) == want_found:
            return True
        time.sleep(BAR_POLL_INTERVAL)
    return False


def _wait_battle_end(win):
    """等待战斗真正结束（回合横幅消失）。"""
    while is_in_battle(win):
        time.sleep(BATTLE_END_POLL)


def _click_template(win, icon):
    """查找模板并点击其位置，返回是否成功。"""
    pt = game_map.find_template(win, icon)
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    return True


def _cancel_auto(win):
    """规则 3：同时存在「取消自动」「恢复自动」按钮（上局遗留自动）时，
    先点击取消自动恢复手动控制。"""
    cancel_pt = game_map.find_template(win, AUTO_CANCEL_ICON)
    resume_pt = game_map.find_template(win, AUTO_RESUME_ICON)
    if cancel_pt is not None and resume_pt is not None:
        human_click(win, cancel_pt[0], cancel_pt[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡操作条
        log("[战斗] 检测到遗留自动战斗，已点击取消自动", "SYS")
        time.sleep(AUTO_CANCEL_SETTLE)  # 等待操作条恢复手动状态


def _do_action(win, action, label):
    """执行一条战斗操作，返回是否发出了操作指令。

    未发出指令（无/暂不支持/识别失败）时操作条会停留到倒计时结束，
    调用方需等待该阶段自行结束。
    """
    if action == "无":
        return False
    if action in _UNSUPPORTED_ACTIONS:
        log(f"[战斗] {label}：{action}暂不支持（无按钮模板），本阶段不操作", "WARN")
        return False

    # 攻击：直接点击攻击按钮
    if action == "攻击":
        human_click(win, ATTACK_POS[0], ATTACK_POS[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
        log(f"[战斗] {label}：攻击", "INFO")
        return True

    # 防御：识别防御按钮点击
    if action == "防御":
        pt = game_map.find_template(win, FANGYU_ICON)
        if pt is None:
            log(f"[战斗] {label}：未识别到防御按钮，本阶段不操作", "WARN")
            return False
        human_click(win, pt[0], pt[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
        log(f"[战斗] {label}：防御", "INFO")
        return True

    # 技能：识别法术按钮 → 打开技能面板 → 点击技能 → 攻击按钮位置再点一次
    if label == "人物":
        pos = char_skill_pos(action)
    else:
        pos = BEAST_SKILL_POS.get(action)
        if pos is None:
            log(f"[战斗] {label}：{action} 坐标未配置，本阶段不操作", "WARN")
            return False
    if pos is None:
        log(f"[战斗] {label}：无法解析 {action}，本阶段不操作", "WARN")
        return False
    pt = game_map.find_template(win, FASHU_ICON)
    if pt is None:
        log(f"[战斗] {label}：未识别到法术按钮，本阶段不操作", "WARN")
        return False
    human_click(win, pt[0], pt[1])
    time.sleep(SKILL_PANEL_DELAY)  # 等待技能选择面板打开
    human_click(win, pos[0], pos[1])
    time.sleep(SKILL_CONFIRM_DELAY)  # 等待面板收起
    human_click(win, ATTACK_POS[0], ATTACK_POS[1])
    _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
    log(f"[战斗] {label}：施放 {action}", "INFO")
    return True


def handle_battle(win):
    """处理一场战斗：第 1 回合按配置操作，之后点击自动；战斗结束后回血蓝。"""
    actions = load_battle_actions()
    log(f"[战斗] 战斗开始：人物={actions['char']}，召唤兽={actions['beast']}", "SYS")
    rounds = 0
    round_1 = True
    while is_in_battle(win):
        # 等待操作条出现（新回合）
        if not _wait_icon(win, FASHU_ICON, ROUND_BAR_TIMEOUT, want_found=True):
            break
        time.sleep(0.5)  # 操作条刚出现，稍作稳定再操作
        rounds += 1
        if rounds == 1:
            # 首次操作条：先取消遗留自动（规则 3），再判断当前回合（规则 2）
            _cancel_auto(win)
            round_1 = is_round_1(win)
            log(f"[战斗] 当前为第 1 回合" if round_1 else "[战斗] 当前非第 1 回合", "INFO")
        # 非第 1 回合：点击自动按钮，剩余回合交由游戏自动战斗
        if not (rounds == 1 and round_1):
            if _click_template(win, AUTO_ICON):
                _park_mouse(win)  # 鼠标移开，避免遮挡操作条
                log("[战斗] 已点击自动，剩余回合由游戏自动战斗", "SYS")
                break
            log("[战斗] 未识别到自动按钮，本回合按配置手动操作", "WARN")
        # ---- 第 1 回合（或自动不可用）：按配置执行人物/召唤兽操作 ----
        log(f"[战斗] 第 {rounds} 回合：人物操作", "INFO")
        acted = _do_action(win, actions["char"], "人物")
        if acted:
            time.sleep(PHASE_SWITCH_DELAY)  # 操作条关闭，召唤兽条出现
        else:
            _wait_icon(win, FASHU_ICON, IDLE_COUNTDOWN_WAIT, want_found=False)
        if not is_in_battle(win):
            break
        # ---- 召唤兽操作阶段 ----
        _wait_icon(win, FASHU_ICON, BEAST_BAR_TIMEOUT, want_found=True)
        log(f"[战斗] 第 {rounds} 回合：召唤兽操作", "INFO")
        acted = _do_action(win, actions["beast"], "召唤兽")
        _wait_icon(win, FASHU_ICON,
                   BAR_GONE_TIMEOUT if acted else IDLE_COUNTDOWN_WAIT,
                   want_found=False)
    # 规则 1：战斗中不检查血蓝条；战斗真正结束（回合横幅消失）后再检查回满
    _wait_battle_end(win)
    status_bars.check_all_bars(win)
    log(f"[战斗] 战斗结束（共 {rounds} 回合）", "SYS")


def watch_loop(win):
    """战斗监控线程入口：轮询战斗状态，进入战斗则处理整场战斗。"""
    log("[战斗] 战斗监控已启动", "SYS")
    while True:
        try:
            if is_in_battle(win):
                handle_battle(win)
        except Exception as e:
            log(f"[战斗] 监控异常: {e}", "WARN")
        time.sleep(WATCH_POLL_INTERVAL)
