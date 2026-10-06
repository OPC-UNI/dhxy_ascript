"""战斗自动化：按场次调度战斗操作与自动战斗，处理巫医对话框。

战斗状态检测（模板匹配多信号判定，不用 OCR，防止非战斗画面误报）：
- 操作条按钮（法术/防御/自动）：战斗专属 UI，误报率最低（非战斗实测 ≤0.39，
  真实按钮 1.0，阈值 0.85）
- round_banner.png：回合横幅「回合」文字区（第X回合任意回合均匹配，
  非战斗实测 ≤0.45，真实横幅 0.99，阈值 0.8），覆盖操作条隐藏的回合间隙
- battle_first.png 即「第1回合」横幅（用于判断是否第 1 回合）

战斗状态判定统一在本模块实现，其他模块（玄枢轶闻等）复用 is_in_battle。

场次调度（不在战斗 → 进入战斗 → 战斗结束算一场战斗，回合变化不算；
进入与结束均连续两次轮询确认，防战斗开场抖动与回合间隙误判）：
1. 第 1 场战斗：按配置执行人物/召唤兽操作（第 1 回合），操作完成后
   点击自动按钮，剩余回合交由游戏自动战斗
2. 第 6/11/16... 场（每 5 场）：进入战斗后点击一次自动按钮——自动战斗
   跨场持续，例行确认；已处于自动战斗时（按钮为取消自动）跳过不点
3. 其余场次：不操作，交由游戏自动战斗
4. 每场战斗结束后检查血蓝条回满（战斗中不检查）
5. 巫医对话框：文案含「我是超级巫医，可以帮你医治召唤兽」时点击绿色
   选项「全部医治和修复」；战后立即检查，非战斗时周期性检查

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
from . import status_bars  # 血蓝条检测回满（每场战斗结束后调用）
from . import task_tracker  # 对话框区域与绿色选项定位（巫医对话框）
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

# ---- 场次调度（进入战斗 = 一场战斗开始，回合变化不算） ----
AUTO_REASSERT_EVERY = 5      # 每 5 场战斗进入战斗后点击一次自动按钮（第 1 场操作完成后点击）
AUTO_ASSERT_TIMEOUT = 35     # 例行确认自动时等待自动按钮出现的超时（覆盖一整回合操作条）
BATTLE_CONFIRM_POLLS = 2     # 战斗进入/结束的连续轮询确认次数（防开场抖动与回合间隙误判）

# ---- 巫医对话框（召唤兽医治） ----
WUYI_DIALOG_TEXT = "我是超级巫医，可以帮你医治召唤兽"  # 对话框文案（游戏内原文，出现则处理）
WUYI_OPTION_TEXT = "全部医治和修复"                    # 对话框绿色选项（游戏内原文，精确子串匹配）
WUYI_CHECK_INTERVAL = 5.0    # 非战斗时巫医对话框检查间隔（秒）
WUYI_RETRY_COOLDOWN = 15.0   # 点击选项后的冷却期（对话框未关闭时防重复点击）

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


def _norm_text(s):
    """去标点/空白（保留中文与字母数字），选项文案精确子串匹配前规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做子串匹配可大幅降低漏配。
    """
    return re.sub(r"[^\w]", "", s)


def _wait_icon(win, icon, timeout, want_found=True):
    """轮询模板出现（want_found=True）或消失（False），战斗结束提前返回 False。

    战斗结束判定连续两次轮询不在战斗（防回合间隙误判，回合变化不算战斗）。
    """
    deadline = time.time() + timeout
    out_polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            out_polls = 0
        else:
            out_polls += 1
            if out_polls >= BATTLE_CONFIRM_POLLS:
                return False
        if (game_map.find_template(win, icon) is not None) == want_found:
            return True
        time.sleep(BAR_POLL_INTERVAL)
    return False


def _battle_ended(win):
    """战斗结束判定：连续两次轮询不在战斗（防回合间隙误判，回合变化不算）。"""
    if is_in_battle(win):
        return False
    time.sleep(BAR_POLL_INTERVAL)
    return not is_in_battle(win)


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
    """处理首场战斗：第 1 回合按配置操作，操作完成后点击自动。

    后续场次由 watch_loop 按场次调度（每 5 场例行点击一次自动，
    其余场次交由游戏自动战斗），不再进入本函数；战斗结束的确认、
    血蓝条检查与巫医对话框处理也统一在 watch_loop 中完成。
    """
    actions = load_battle_actions()
    log(f"[战斗] 战斗开始：人物={actions['char']}，召唤兽={actions['beast']}", "SYS")
    rounds = 0
    round_1 = True
    while not _battle_ended(win):
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
        if _battle_ended(win):
            break
        # ---- 召唤兽操作阶段 ----
        _wait_icon(win, FASHU_ICON, BEAST_BAR_TIMEOUT, want_found=True)
        log(f"[战斗] 第 {rounds} 回合：召唤兽操作", "INFO")
        acted = _do_action(win, actions["beast"], "召唤兽")
        _wait_icon(win, FASHU_ICON,
                   BAR_GONE_TIMEOUT if acted else IDLE_COUNTDOWN_WAIT,
                   want_found=False)


def _click_auto_once(win):
    """进入战斗后点击一次自动按钮（每 5 场例行确认，自动战斗跨场持续）。

    已处于自动战斗时操作条显示「取消自动」而非「自动」，自动按钮
    不会出现，轮询超时即跳过、不打扰；自动被关掉（如次数耗尽）时
    点一次重新开启。操作条可能正处回合间隙，最多等待一整回合的
    操作条周期；战斗结束（连续两轮确认）提前返回。
    """
    deadline = time.time() + AUTO_ASSERT_TIMEOUT
    out_polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            out_polls = 0
        else:
            out_polls += 1
            if out_polls >= BATTLE_CONFIRM_POLLS:
                return
        if _click_template(win, AUTO_ICON):
            _park_mouse(win)  # 鼠标移开，避免遮挡操作条
            log("[战斗] 已点击自动按钮（每 5 场例行确认）", "SYS")
            return
        time.sleep(BAR_POLL_INTERVAL)
    log("[战斗] 未识别到自动按钮（可能已处于自动战斗），本场不点击", "INFO")


# 巫医对话框选项未识别告警去重标记（对话框消失后复位，防轮询刷屏）
_wuyi_option_missing_warned = False


def _handle_wuyi_dialog(win):
    """巫医对话框：文案含「我是超级巫医…」时点击绿色选项「全部医治和修复」。

    文案用模糊匹配（抗 OCR 误读）；选项去标点后精确子串匹配（游戏内原文，
    记忆规则：选项匹配不"纠正"用字）；限定对话框区域（x≥PANEL_MAX_X）
    且为绿字行，避免误点追踪面板的绿字链接。返回是否已点击。
    """
    global _wuyi_option_missing_warned
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    from .xuanshu import _dialog_text_present  # 函数内导入，避免循环依赖
    if not _dialog_text_present(items, WUYI_DIALOG_TEXT):
        _wuyi_option_missing_warned = False
        return False
    needle = _norm_text(WUYI_OPTION_TEXT)
    for txt, rect, score in items:
        if score >= 0.55 and needle in _norm_text(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect,
                                                    prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[战斗] 巫医对话框：已点击「{txt}」", "SYS")
            _wuyi_option_missing_warned = False
            return True
    if not _wuyi_option_missing_warned:
        log("[战斗] 巫医对话框出现，但未识别到「全部医治和修复」选项", "WARN")
        _wuyi_option_missing_warned = True
    return False


def watch_loop(win):
    """战斗监控线程入口：轮询战斗状态，按场次调度战斗操作。

    场次规则（不在战斗 → 进入战斗 → 战斗结束算一场战斗，回合变化不算；
    进入与结束均连续两次轮询确认，防战斗开场抖动与回合间隙误判）：
    - 第 1 场：handle_battle 按配置操作，操作完成后点击自动
    - 第 6/11/16... 场（每 5 场）：进入战斗后点击一次自动按钮
    - 其余场次：不操作，交由游戏自动战斗（自动战斗跨场持续生效）
    每场战斗结束后检查血蓝条回满，并立即检查巫医对话框；
    非战斗时周期性检查巫医对话框（召唤兽受伤弹出的医治对话框）。
    """
    log("[战斗] 战斗监控已启动", "SYS")
    battle_no = 0          # 本场次计数（第 1 场按配置操作）
    in_battle = False      # 已确认的战斗状态
    in_polls = 0           # 连续在战斗的轮询数（进入确认）
    out_polls = 0          # 连续不在战斗的轮询数（结束确认）
    last_wuyi_check = 0.0  # 上次巫医对话框检查时间
    wuyi_click_at = 0.0    # 上次点击巫医选项时间（冷却）
    while True:
        try:
            if is_in_battle(win):
                out_polls = 0
                if not in_battle:
                    in_polls += 1
                    if in_polls >= BATTLE_CONFIRM_POLLS:  # 连续两轮确认进入
                        in_battle = True
                        battle_no += 1
                        log(f"[战斗] 第 {battle_no} 场战斗开始", "SYS")
                        if battle_no == 1:
                            handle_battle(win)  # 首场：按配置操作后点击自动
                        elif battle_no % AUTO_REASSERT_EVERY == 1:
                            _click_auto_once(win)  # 每 5 场：点一次自动
                        # 其余场次：不操作，等待游戏自动战斗结束
            else:
                in_polls = 0
                if in_battle:
                    out_polls += 1
                    if out_polls >= BATTLE_CONFIRM_POLLS:  # 连续两轮确认结束
                        in_battle = False
                        log(f"[战斗] 第 {battle_no} 场战斗结束", "SYS")
                        status_bars.check_all_bars(win)
                        last_wuyi_check = 0.0  # 战后立即检查巫医对话框
                now = time.time()
                if now - last_wuyi_check >= WUYI_CHECK_INTERVAL:
                    last_wuyi_check = now
                    if now - wuyi_click_at >= WUYI_RETRY_COOLDOWN \
                            and _handle_wuyi_dialog(win):
                        wuyi_click_at = now
        except Exception as e:
            log(f"[战斗] 监控异常: {e}", "WARN")
        time.sleep(WATCH_POLL_INTERVAL)
