"""封印机关破解小游戏的纯判定与链接定位逻辑。

触发（对话文案 `JIGUAN_DIALOG_TEXT`）与阻塞点击循环
（`_play_jiguan_puzzle`）仍在 xuanshu 任务侧；本模块只做
「第 N 处封印已开启」计数、阶段判定与按序链接定位，
import 期零副作用。
"""
import re
import time

import numpy as np
from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..device import map as game_map
from ..device import task_tracker
from ..core.app_utils import human_click, log
from ..device.dialog_common import SCREEN_CENTER_POS

# ---- 封印机关破解（对话触发 + 按序点击）----
JIGUAN_DIALOG_TEXT = "看来要破解的便是此地的封印了。"
JIGUAN_CLICK_ORDER = ("xuanshu_jiguanhuang.png",   # 点击顺序：黄→蓝→红（用户 2026-10-15 指定）
                      "xuanshu_jiguanlan.png",
                      "xuanshu_jiguanhong.png")
JIGUAN_CLICK_WAIT = 5        # 每次点击机关后等待（秒），随后检查任务追踪
JIGUAN_FIND_TIMEOUT = 8     # 单个机关模板查找超时（秒，找不到跳过继续下一个；
                            # 8s 覆盖角色短暂遮挡——遮挡帧低于阈值移，
                            # 不点击，等遮挡移开后高分局命中；也吸收 Alt+C 降落动画）
JIGUAN_THRESHOLD = 0.6       # 机关模板匹配阈值（2026-10-19 测试按钮实测：黄 0.94、
                             # 红 0.88~1.0、蓝 0.62~0.68，角色遮挡时蓝掉到 0.54
                             # 且最佳位置漂移为误匹配。取 0.6：蓝机关正常分可通过，
                             # 遮挡帧 0.54 不点击——遮挡是瞬时的，靠加长查找窗口
                             # 等其移开；失败时打全帧最佳匹配度日志供再校准）

JIGUAN_LINK_ORDER = ("南", "东", "西")   # 点击顺序（面板提示原文：先触南，再寻东，后破西）
# 链接行绿字簇序（按 x 左→右）→ 关键词：面板固定渲染「机关·东、
# 机关·南、机关·西」（实测簇 x 24-88 / 106-170 / 188-220，2026-10-25）
JIGUAN_LINK_CLUSTER = {"东": 0, "南": 1, "西": 2}
# 进度信号用词兼容「已开启」/「已解除」两种变体（实测 2026-10-25 截图
# OCR 为「已开启」，用户确认亦有「已解除」文案的轮次变体）
JIGUAN_PROGRESS_RE = re.compile(r"第\s*([一二三四五1-5])\s*处\s*封印\s*已\s*(?:开启|解除)")
_JIGUAN_ORD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}


def _jiguan_link_count(items):
    """面板「第N处封印已开启」的最大序数（无则 None）。"""
    best = None
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        for m in JIGUAN_PROGRESS_RE.finditer(txt):
            n = _JIGUAN_ORD.get(m.group(1))
            if n is None and m.group(1).isdigit():
                n = int(m.group(1))
            if n is not None and (best is None or n > best):
                best = n
    return best

def _jiguan_link_stage(items, img_np):
    """是否处于封印按序开启阶段：面板存在含「机关」的绿字行
    （描述行绿字占比实测 8~9%，提示行无绿字）。"""
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if "机关" in txt and task_tracker.is_green_text(img_np, rect):
            return True
    return False

def _jiguan_link_progress(items, img_np):
    """封印按序开启进度键：已开启计数；阶段内未开为 0（与点击前
    基线一致，点后 1/2/3 变化即推进），阶段外 None（交由默认
    文本签名驱动）。"""
    n = _jiguan_link_count(items)
    if n is not None:
        return str(n)
    return "0" if _jiguan_link_stage(items, img_np) else None

def _jiguan_link_picker(items, img_np):
    """按已开启计数选下一个关键词（南→东→西）并定位点击。

    布局随进度变化（每点开一处任务描述即改写，2026-10-26 用户确认）：
    - 0 处已开（描述「根据桃小淘所言，前往花果…」）：链接行折两行——
      上行「…机关·东、机关」+ 下行「·南、机关·西三处封印。」，南的标签
      「机关·南」被折行拆成两段（上行行尾「机关」+ 下行行首「·南」），
      共 4 段实线下划线（实测 y287：东 113-172、南A 188-217；y304：
      南B 23-55、西 73-137）。此时只能点南：取最底行最左段（南B）。
    - 1/2 处已开（描述「第一/二处封印已开启，再去开启…」）：链接行仍
      折两行但主行含 3 段——上行「机关·东、机关·南、机关」+ 下行「·西
      …」尾段（实测 y288：东 24-88、南 106-170、西A 188-220；y305：
      西B 24-56 单段尾行）。点法：找 3 段主行按 x 序映射（东/南/西），
      不能按最底行取（会取到 1 段的西B 尾行）。

    扫描取含「机关」行的纵向邻域（-10/+30px：下行底 307 vs 上行底
    289，差 18px，OCR 漏读下行时也能扫到其下划线；碎片行可能不含
    机关关键词却承载链接）。点击取下划线上方 2px（行底可点区规则）。
    阶段内链接无法解析时返回 PICK_FAIL（track_task 拒绝行级兜底点击
    并重试——行级兜底按最长实线段点会点错机关：2026-10-26 实测点南
    落到西、顺序破坏任务卡死）。非封印阶段返回 None（回退行级选择，
    如领取后的寻路链接）。计数用尽（≥3）返回 None。
    """
    n = _jiguan_link_count(items)
    if n is None:
        if not _jiguan_link_stage(items, img_np):
            return None  # 非封印阶段不介入（防其他任务绿字行含南/东/西误选）
        n = 0
    if n >= len(JIGUAN_LINK_ORDER):
        return None
    y_top = y_bot = None
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if "机关" in txt:
            y_top = rect[1] if y_top is None else min(y_top, rect[1])
            y_bot = rect[3] if y_bot is None else max(y_bot, rect[3])
    if y_top is None:
        return None
    y_top = max(0, y_top - 10)
    y_bot = min(img_np.shape[0], y_bot + 30)
    region = img_np[y_top:y_bot, 0:task_tracker.PANEL_MAX_X].astype(int)
    mask = task_tracker.green_mask(region, task_tracker.GREEN_MIN_G)
    # 收集所有含实线段（≥20px 连续亮绿列）的行（自底向上）：下划线可跨
    # 1~2 行，字形笔画连排实测 ≤13px 不构成实线段
    run_rows = []
    for rr in range(mask.shape[0] - 1, -1, -1):
        cols = np.where(mask[rr])[0]
        if cols.size < 20:
            continue
        segs = []
        start = prev = cols[0]
        for c in cols[1:]:
            if c != prev + 1:
                segs.append((int(start), int(prev)))
                start = c
            prev = c
        segs.append((int(start), int(prev)))
        solid = sorted(s for s in segs if s[1] - s[0] + 1 >= 20)
        if solid:
            run_rows.append((y_top + rr, solid))
    if not run_rows:
        return task_tracker.PICK_FAIL  # 阶段内实线段全无：拒绝行级兜底
    # 主链接行优先：含 3 段实线段的行（1/2 处已开布局：主行「机关·东、
    # 机关·南、机关」+ 下行「·西…」尾段——实测 y288 东 24-88 / 南
    # 106-170 / 西A 188-220，y305 西B 24-56 单段尾行）。不能按最底行
    # 取（会取到 1 段的西B 尾行），须在全部行中找 3 段主行
    for y0, runs0 in run_rows:
        if len(runs0) == 3:
            # 按 x 序映射（东/南/西）
            ci = JIGUAN_LINK_CLUSTER[JIGUAN_LINK_ORDER[n]]
            cx = (runs0[ci][0] + runs0[ci][1]) // 2
            return cx, y0 - 2  # 下划线上方 2px
    if n == 0 and len(run_rows) == 2 and len(run_rows[0][1]) == 2 \
            and len(run_rows[1][1]) == 2:
        # 折行布局（初始态）：上下两行各 2 段，南的可点部分是下行
        # 最左段（「·南」）。要求两行俱全——上行孤存（下行被遮挡/
        # 渲染中）时其左段是东，不能点
        y0, runs0 = run_rows[0]
        cx = (runs0[0][0] + runs0[0][1]) // 2
        return cx, y0 - 2
    return task_tracker.PICK_FAIL

def play(win, title, current_task_sig, task_done_or_changed,
         prefix="玄枢轶闻"):
    """封印机关破解（用户 2026-10-15 流程）：

    提示对话框点击一次关闭 → Alt+C 降落 → 按 黄→蓝→红 顺序识别并
    点击机关模板；每次点击后等待 5s，任务追踪消失或签名变化即停止
    （不再点剩余机关）。返回 (是否执行了流程, 描述)。
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
    # 3. 按序点击机关（每次点击后 5s 检查任务追踪）
    sig0 = current_task_sig(win, title)
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
        log(f"[{prefix}] 已点击机关 {icon} @({pt[0]},{pt[1]})，等待 {JIGUAN_CLICK_WAIT}s", "SYS")
        time.sleep(JIGUAN_CLICK_WAIT)
        if task_done_or_changed(win, title, sig0):
            return True, f"封印破解完成（点击 {clicked} 个机关后任务追踪变化）"
    return clicked > 0, f"封印破解流程结束（点击 {clicked} 个机关，任务追踪未变化）"
