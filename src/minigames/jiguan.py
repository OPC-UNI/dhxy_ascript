"""封印机关破解小游戏的阻塞点击循环。

触发（对话文案 `JIGUAN_DIALOG_TEXT`）仍在 xuanshu 任务侧；本模块提供
play（提示对话框关闭 → Alt+C 降落 → 找任意一个机关模板点击 → 按结果
对话框决定：换一处再试试=换下一个模板；解除封印成功=结束；超时无结果
也按成功处理），import 期零副作用。封印按序开启（面板链接按序点击）的
进度键与链接定位逻辑见 minigames/fengyin_link.py（2026-11-17 拆分，
代码逐字节未变）。
"""
import time

from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..device import map as game_map
from ..device import task_tracker
from ..core.app_utils import human_click, log
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD, SCREEN_CENTER_POS,
                                    _dialog_text_present,
                                    clear_blocking_dialog,
                                    dialog_frame_score)

# ---- 封印机关破解（对话触发 + 失败换模板）----
JIGUAN_DIALOG_TEXT = "看来要破解的便是此地的封印了。"
JIGUAN_CLICK_ORDER = ("xuanshu_jiguanhuang.png",   # 尝试顺序：黄→蓝→红（用户 2026-10-15 指定；
                      "xuanshu_jiguanlan.png",     # 2026-11-17 起失败才试下一个，成功即停）
                      "xuanshu_jiguanhong.png")
# 点击机关后的解除结果信号（用户 2026-11-17 机制修正）：
# - 失败对话框「换一处再试试」= 本机关解除失败，换下一个模板；
# - 成功对话框「此处已解除封印」/「解除封印成功」= 解除成功，立即停止；
# - 两者都没出现 = 按解除成功处理（旧实现固定等 5s 看任务签名，签名被
#   OCR 抖动成「20虹」「不死」等乱码时会提前停止，剩余机关不再点，任务卡住）。
JIGUAN_FAIL_TEXT = "换一处"     # 失败对话框原文「换一处再试试」；短键抗 OCR 漏字
JIGUAN_SUCCESS_TEXT = "解除封印"  # 成功对话框二者共有的短键（此处已解除封印/解除封印成功）
JIGUAN_RESULT_WAIT = 10       # 等待结果对话框的上限（秒）。点击后失败对话框要
                              # 几秒才出现（2026-11-17 用户确认），5s 偏紧；固定
                              # 时延只作超时上限，任一方命中即返回（性能规则 7）
JIGUAN_DIALOG_POLL = 0.5      # 等待对话框的轮询间隔（秒）
JIGUAN_FIND_TIMEOUT = 8     # 单个机关模板查找超时（秒，找不到跳过继续下一个；
                            # 8s 覆盖角色短暂遮挡——遮挡帧低于阈值移，
                            # 不点击，等遮挡移开后高分局命中；也吸收 Alt+C 降落动画）
JIGUAN_THRESHOLD = 0.6       # 机关模板匹配阈值（2026-10-19 测试按钮实测：黄 0.94、
                             # 红 0.88~1.0、蓝 0.62~0.68，角色遮挡时蓝掉到 0.54
                             # 且最佳位置漂移为误匹配。取 0.6：蓝机关正常分可通过，
                             # 遮挡帧 0.54 不点击——遮挡是瞬时的，靠加长查找窗口
                             # 等其移开；失败时打全帧最佳匹配度日志供再校准）

def _jiguan_result_dialog(win, timeout=JIGUAN_RESULT_WAIT):
    """等待点击机关后的解除结果对话框（直接信号）。

    标准对话框模板先门控（条带匹配 ~9ms/帧，无对话框时不 OCR），
    命中后才对对话区做区域 OCR 判文案。返回：
    - "fail"：出现「换一处再试试」，应换下一个模板；
    - "success"：出现「此处已解除封印」/「解除封印成功」，解除成功；
    - None：超时未出现任何结果对话框（按成功处理，调用方记录）。

    timeout 固定时延只作超时上限（性能规则 7）：结果对话框要几秒才
    出现（用户 2026-11-17 确认），任一方命中即返回，不空等；
    超时未命中说明本次点击没触发失败提示，按解除成功处理。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None \
                and dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
            items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
            if _dialog_text_present(items, JIGUAN_FAIL_TEXT):
                return "fail"
            if _dialog_text_present(items, JIGUAN_SUCCESS_TEXT):
                return "success"
        time.sleep(JIGUAN_DIALOG_POLL)
    return None

def play(win, prefix="玄枢轶闻"):
    """封印机关破解（用户 2026-10-15 流程，2026-11-17 修正点击判定）：

    提示对话框点击一次关闭 → Alt+C 降落 → 按 黄→蓝→红 顺序找任意
    一个机关模板并点击 → 轮询等待解除结果对话框：
    - 「换一处再试试」：当前机关解除失败，关闭对话框后换下一个模板再点；
    - 「此处已解除封印」/「解除封印成功」：解除成功，立即停止；
    - 超时无任何结果对话框：同样按解除成功处理。
    成功对话框留在屏幕上，由 follow 的通用对话处理推进/关闭。
    返回 (是否执行了流程, 描述)。
    """
    # 1. 点击一次关闭提示对话框（屏幕中间；该对话框无选项）
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log(f"[{prefix}] 已点击关闭封印提示对话框", "SYS")
    time.sleep(0.5)
    # 2. Alt+C 降落（飞行状态下落地，机关才可见可点）；
    #    静默操作：走消息投递（无需前台，2026-11-03 实测有效）
    if app_utils.silent_mode_on():
        from ..device import silent_click  # 延迟导入：避免模块级循环
        silent_click.send_silent_hotkey(win, ("alt",), "c")
        log(f"[{prefix}] 已静默 Alt+C 降落", "SYS")
        time.sleep(1.0)
    elif app_utils.keyboard_ok(win):
        Keyboard.key_hot("alt", "c")
        log(f"[{prefix}] 已按 Alt+C 降落", "SYS")
        time.sleep(1.0)
    else:
        log(f"[{prefix}] 游戏不在前台：跳过 Alt+C 降落", "WARN")
    # 3. 逐个尝试机关：点击后只看「换一处再试试」失败对话框决定是否
    #    换下一个（不再按固定间隔查任务签名——签名 OCR 抖动会误判完成）
    clicked = 0
    for icon in JIGUAN_CLICK_ORDER:
        deadline = time.time() + JIGUAN_FIND_TIMEOUT
        pt = None
        while time.time() < deadline:
            pt = game_map.find_template(win, icon, threshold=JIGUAN_THRESHOLD)
            if pt is not None:
                break
            time.sleep(0.5)
        if pt is None:
            # 诊断：记录全帧最佳匹配度与位置，区分「分数差一点」与
            # 「模板/场景不符」（后者分数会远低于阈值，须换模板）
            conf, (bx, by) = (0.0, (0, 0))
            img = win.capture(force_activate=False)
            if img is not None:
                conf, (bx, by) = game_map.best_match_in(img, icon)
            log(f"[{prefix}] 未找到机关 {icon}，跳过"
                f"（全帧最佳匹配度 {conf:.2f} @({bx},{by})）", "WARN")
            continue
        human_click(win, pt[0], pt[1])
        clicked += 1
        log(f"[{prefix}] 已点击机关 {icon} @({pt[0]},{pt[1]})，等待解除结果", "SYS")
        result = _jiguan_result_dialog(win)
        if result == "fail":
            log(f"[{prefix}] 点击机关 {icon} 后出现「换一处再试试」，改点下一个机关", "SYS")
            clear_blocking_dialog(win, prefix=prefix, max_clicks=5)
            continue
        if result == "success":
            log(f"[{prefix}] 点击机关 {icon} 后出现解除成功对话框，封印破解完成", "SYS")
            return True, f"封印破解完成（点击 {clicked} 个机关，出现解除成功提示）"
        log(f"[{prefix}] 点击机关 {icon} 后 {JIGUAN_RESULT_WAIT}s 内未出现"
            f"解除结果对话框，按解除成功处理", "SYS")
        return True, f"封印破解完成（点击 {clicked} 个机关，未出现换一处提示）"
    return clicked > 0, f"封印破解流程结束（尝试 {clicked} 个机关，均提示换一处）"
