"""启动清屏：Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位 + 收起游戏活动侧栏
（通用设置开关，默认开启）。

每个任务启动时执行一次（main.task_start 在登录检查后调用）。

活动侧栏（2026-11-04）：左边缘固定图标两态——展开态 game_activity_open
（16px 带边框盒）点击一次切换为收起态 game_activity_close（14px 窄条，
用户实机确认）。两态图标几乎同形（实测收起态画面 open 模板交叉得分
0.811），判定一律双模板比分数：达标（0.8）且更高者胜，禁止单模板
阈值判定（会把收起态误判成展开态点击、反而展开侧栏）。点击后轮询
收起态出现（2s 上限），未确认保守报失败。

实测标定（2026-10-12，一生所爱服 1378x831 窗口）：
- 两个热键的提示都是主窗口中央飘字（实测 x535-845 / y415-465，一到两行），
  出现有 0.2~1.2s 延迟（服务器确认），滞留可达数秒、连按时新旧飘字同屏
  ——分辨与消失时刻无关（用户确认），只按文案集合做差值。
- Alt+6 开启：「您选择了永久屏蔽非队伍成员、非队长的其他玩家，再次按alt+6
  可以取消屏蔽玩家。」；取消：「您取消了屏蔽玩家的状态，再次按alt+6可以设置
  屏蔽状态。」
- Alt+5 开启：「您选择了屏蔽摊位，再次按alt+5可以取消该…」；取消：
  「您取消了屏蔽摊位状态，再次按alt+5可以进入屏蔽状态。」

分辨规则（用户确认）：
- 每轮按键后与上一轮观察做差值，新出现的文案才是本次有效结果——旧飘字
  滞留时新旧文案会同屏；新出现「开启」→ 生效，新出现「取消」→ 再按一次。
- A+B 同屏时检查 B 是否命中：B 命中说明本次有效文案为 B（取消）→ 再按。
- 无新增且屏上只有开启文案 → 生效（同文案重发）；其余（未读到/只有取消/
  无法分辨）一律保守再按，不凭推断宣告成功，至多 MAX_PRESS 次。
- 热键经前台窗口送达（Keyboard.key_hot 即 pyautogui）：每次按键前校验游戏
  窗口仍在前台（任务执行期间用户切换窗口会抢占焦点，按键将失效甚至触发
  其他窗口的快捷键），非前台先重新激活，仍失败则终止本轮。
"""
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard

from .app_utils import human_click, log
from . import app_utils  # 静默操作门控（keyboard_ok）
from . import map as game_map

# 中央飘字 OCR 区域（实测标定 x535-845 / y415-465，四周留 ≥50px 余量）
POPUP_BAND = (470, 360, 920, 520)
# 每次按键最多尝试次数（超限保守判定失败，不阻塞任务启动）
MAX_PRESS = 4

# 游戏活动侧栏图标搜索区域（用户标定，窗口相对坐标；左边缘固定 UI，
# 实测图标中心 (14,161)、图标宽 14~16px 高 34px，区域余量横 3~34px、
# 纵 9~19px，固定 UI 无漂移足够）
ACTIVITY_REGION = (3, 135, 56, 197)
ACTIVITY_OPEN_ICON = "game_activity_open.png"
ACTIVITY_CLOSE_ICON = "game_activity_close.png"
ACTIVITY_THRESHOLD = 0.8
# 点击后确认收起的轮询上限（收起动画约 1s，留一倍余量）
ACTIVITY_CLOSE_TIMEOUT = 2.0


def _classify(text):
    """单条飘字文案 → 判定集合（空集 = 无相关文案）。

    锚词全部位于飘字第 1 行内（OCR 实测置信度 0.99~1.00），逐条文案
    匹配（不跨条拼接）：动词（选择了/取消了）区分开启与取消，主语词
    区分玩家与摊位、防止世界文案误判（「非队伍」几乎不出现在世界文案中）。
    """
    found = set()
    if "选择了" in text and ("非队伍" in text or "永久" in text):
        found.add("player_ok")      # Alt+6 开启
    if "取消了" in text and "玩家" in text:
        found.add("player_cancel")  # Alt+6 取消
    if "选择了" in text and "摊位" in text:
        found.add("stall_ok")       # Alt+5 开启
    if "取消了" in text and "摊位" in text:
        found.add("stall_cancel")   # Alt+5 取消
    return found


def _read_popup(win):
    """截一帧 OCR 飘字带，逐条文案分类后取并集（空集=无相关文案）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return set()
    items, _ = game_map.ocr_region(img, POPUP_BAND)
    found = set()
    for txt, _rect, _score in items:
        found |= _classify(str(txt))
    return found


def _read_popup_after_press(win):
    """按键后多帧读取并集：0.3/0.7/1.2s 三帧覆盖飘字 0.2~1.2s 的出现
    延迟窗口；并集即「按键后屏幕上出现过的文案」（不关心何时消失）。"""
    found = set()
    slept = 0.0
    for delay in (0.3, 0.4, 0.5):
        time.sleep(delay - slept)
        slept = delay
        found |= _read_popup(win)
    return found


def _game_foreground(win):
    """游戏窗口是否为前台窗口（热键经前台送达，非前台时按键无效甚至
    触发其他窗口的快捷键）。读取失败时返回 False 保守处理。"""
    try:
        import win32gui
        return win32gui.GetForegroundWindow() == win.hwnd
    except Exception:
        return False


def _press_hotkey(win, key):
    """按 Alt+key：静默操作走消息投递（游戏窗口无需前台），否则物理热键。
    返回是否已投递。"""
    if app_utils.silent_mode_on():
        from . import silent_click  # 延迟导入：避免模块级循环
        info = silent_click.send_silent_hotkey(win, ("alt",), key)
        return info is not None
    Keyboard.key_hot("alt", key)
    return True


def _ensure_hotkey_on(win, key, ok_sig, cancel_sig, label):
    """连按热键直到「开启」成为本次按键的有效结果。返回 (成功, 描述)。

    分辨规则（用户确认，见模块 docstring）：每轮与上一轮观察做差值，
    新出现的文案才是本次有效结果；A+B 同屏检查 B（命中=取消再按）。
    静默操作：按键走消息投递，免前台校验与激活。
    """
    last = _read_popup(win)  # 按键前基线：历史飘字可能仍滞留屏上
    for i in range(MAX_PRESS):
        if not app_utils.silent_mode_on():
            if not _game_foreground(win):
                win.activate()
                time.sleep(0.3)
                if not _game_foreground(win):
                    return False, f"游戏窗口未在前台，{label}清屏未执行"
        if not _press_hotkey(win, key):
            return False, f"{label}清屏按键投递失败"
        found = _read_popup_after_press(win)
        newly = found - last
        last = found
        if ok_sig in newly and cancel_sig not in newly:
            return True, f"{label}已屏蔽（第 {i + 1} 次按键生效）"
        if newly:
            # 新出现取消文案，或开启/取消同为新出现（A+B 同屏检查 B 命中）
            log(f"[清屏] {label}：本次按键为取消屏蔽，再按一次", "WARN")
            continue
        # 无新出现文案：屏上只有开启 → 生效（同文案重发）；其余保守再按
        if ok_sig in found and cancel_sig not in found:
            return True, f"{label}已屏蔽（同文案重发，按屏上判定）"
        log(f"[清屏] {label}：未检测到新文案"
            f"{'（屏上无提示飘字）' if not found else ''}，重按", "WARN")
    return False, f"{label}屏蔽未确认成功（已按 {MAX_PRESS} 次）"


def _capture_frame(win):
    """截一帧转 BGR numpy（模板匹配热路径；失败返回 None）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _activity_frame_state(frame):
    """活动栏状态判定：双模板比分数，达标（0.8）且更高者胜。

    返回 (状态, 位置)，状态 ∈ 'open'/'close'/None。两态图标几乎同形，
    收起态画面 open 模板交叉得分 0.811——单模板阈值判定会把收起态
    误判成展开态点击、反而展开侧栏，必须双模板比分数（实测校准）。
    """
    open_score, open_pos = game_map.best_match_np(frame, ACTIVITY_OPEN_ICON,
                                                  region=ACTIVITY_REGION)
    close_score, close_pos = game_map.best_match_np(frame, ACTIVITY_CLOSE_ICON,
                                                    region=ACTIVITY_REGION)
    if open_score >= ACTIVITY_THRESHOLD and open_score > close_score:
        return "open", open_pos
    if close_score >= ACTIVITY_THRESHOLD and close_score > open_score:
        return "close", close_pos
    return None, None


def _ensure_activity_closed(win):
    """收起游戏活动侧栏：区域内出现展开态图标则点击一次，轮询确认
    变成收起态。返回 (成功, 描述)。

    未找到图标视为无需操作（场景不可见或已收起）；点击后 2s 内未见
    收起态保守报失败，不凭推断宣告成功（与热键判定同原则）。
    """
    frame = _capture_frame(win)
    if frame is None:
        return False, "截帧失败，未检查活动侧栏"
    state, pos = _activity_frame_state(frame)
    if state == "close":
        return True, "游戏活动侧栏已收起"
    if state is None:
        return True, "未检测到游戏活动图标（场景不可见），跳过"
    # 双帧确认后再点击（防动画瞬态误检，同 close_map_if_open 模式）
    frame = _capture_frame(win)
    if frame is None:
        return False, "复检截帧失败，未执行活动侧栏点击"
    state, pos = _activity_frame_state(frame)
    if state == "close":
        return True, "游戏活动侧栏已收起"
    if state is None:
        return True, "复检时活动图标不可见，跳过"
    log(f"[清屏] 收起活动侧栏: 点击展开图标 @ ({pos[0]},{pos[1]})", "INFO")
    human_click(win, pos[0], pos[1])
    # 轮询收起状态出现（直接信号；固定时延只作超时上限）
    deadline = time.time() + ACTIVITY_CLOSE_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.4)
        frame = _capture_frame(win)
        if frame is None:
            continue
        state, _ = _activity_frame_state(frame)
        if state == "close":
            return True, "游戏活动侧栏已点击收起"
    return False, "点击后未确认活动侧栏收起（2s 内未见收起态）"


def run_clear_screen(win):
    """启动清屏：Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位 + 收起游戏活动
    侧栏。返回 (成功, 描述)。

    物理路径：执行前激活游戏窗口并校验前台——热键经前台窗口送达，
    游戏未置前时按键无效甚至触发其他窗口的快捷键，此时直接跳过。
    静默操作：按键走消息投递（无需前台），飘字 OCR 判定照常；
    活动侧栏点击走 human_click（静默模式内部切换消息投递）。
    """
    if not app_utils.silent_mode_on():
        try:
            win.activate()
            time.sleep(0.5)
            import win32gui
            if win32gui.GetForegroundWindow() != win.hwnd:
                return False, "游戏窗口未在前台（激活失败），跳过清屏"
        except Exception as e:
            log(f"[清屏] 前台检查异常: {e}", "WARN")
    ok6, info6 = _ensure_hotkey_on(win, "6", "player_ok", "player_cancel",
                                   "屏蔽玩家")
    ok5, info5 = _ensure_hotkey_on(win, "5", "stall_ok", "stall_cancel",
                                   "屏蔽摊位")
    ok_act, info_act = _ensure_activity_closed(win)
    return ok6 and ok5 and ok_act, f"{info6}；{info5}；{info_act}"
