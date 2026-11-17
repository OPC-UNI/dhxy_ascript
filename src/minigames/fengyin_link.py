"""封印按序开启（玄枢轶闻）的进度键与链接定位逻辑。

任务面板提示「先触南，再寻东，后破西」：三个可跟踪绿字
（机关·东/南/西）嵌在描述行内，按 南→东→西 依次点击，每点完一处
面板出现「第N处封印已开启」——进度信号。本模块只提供 follow_task 的
progress_key / green_picker 回调（纯判定，import 期零副作用）；回调在
非本任务阶段自门控不介入（progress_key 返回 None、picker 返回 None
回退 track_task 行级选择）。

原与封印机关破解同驻 minigames/jiguan.py，2026-11-17 按「一个任务/
小游戏一个模块」拆分；判定代码逐字节未变。
"""
import re

import numpy as np

from ..device import task_tracker

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
