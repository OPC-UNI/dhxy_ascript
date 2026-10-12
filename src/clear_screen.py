"""启动清屏：Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位（通用设置开关，默认开启）。

每个任务启动时执行一次（main.task_start 在登录检查后调用）。

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

from ascript.windows.hardware.keyboard import Keyboard

from .app_utils import log
from . import map as game_map

# 中央飘字 OCR 区域（实测标定 x535-845 / y415-465，四周留 ≥50px 余量）
POPUP_BAND = (470, 360, 920, 520)
# 每次按键最多尝试次数（超限保守判定失败，不阻塞任务启动）
MAX_PRESS = 4


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


def _ensure_hotkey_on(win, key, ok_sig, cancel_sig, label):
    """连按热键直到「开启」成为本次按键的有效结果。返回 (成功, 描述)。

    分辨规则（用户确认，见模块 docstring）：每轮与上一轮观察做差值，
    新出现的文案才是本次有效结果；A+B 同屏检查 B（命中=取消再按）。
    """
    last = _read_popup(win)  # 按键前基线：历史飘字可能仍滞留屏上
    for i in range(MAX_PRESS):
        if not _game_foreground(win):
            win.activate()
            time.sleep(0.3)
            if not _game_foreground(win):
                return False, f"游戏窗口未在前台，{label}清屏未执行"
        Keyboard.key_hot("alt", key)
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


def run_clear_screen(win):
    """启动清屏：Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位。返回 (成功, 描述)。

    执行前激活游戏窗口并校验前台——热键经前台窗口送达，游戏未置前时
    按键无效甚至触发其他窗口的快捷键，此时直接跳过并告警。
    """
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
    return ok6 and ok5, f"{info6}；{info5}"
