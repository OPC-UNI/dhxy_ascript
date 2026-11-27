# -*- coding: utf-8 -*-
"""300环 拼图小游戏（4x4）：目标图模板匹配 + 交换环分解。

用户 2026-11-26 实机规则：
- 拼图固定 4x4，图案可以是任意地图；
- 点「准备好了」之前显示**完整目标地图**（参考图）；
- 开始后 16 块打乱；点一块出现蓝框选中 → 点任意另一块交换；
- 完成后点「完成」→ 二次确认「你现在的完成度是100%，确定完成吗？」→「确定」；
- 超时该环失败（对话框「大侠，就差一点了，再试试吧！」），重新点追踪
  寻路即可重开，不需重新接任务。

实现（性能：全程模板匹配，无 OCR）：
1. 「准备好了」按钮模板定位参考态 → 按「完成」按钮锚点换算 4x4 边框；
2. 截参考图（中心按钮区域记为遮挡）→ 点开始 → 等参考按钮消失；
3. 逐块取中央 60% 子图对参考图做 TM_CCOEFF_NORMED → 目标格；
   被按钮遮挡的格（参考图里没有内容的格）用可见带匹配补全；
4. labels[i]=该格当前块的目标格，selection-sort 生成交换序列并点击；
5. 模板定位「完成」→ 点；模板定位「确定」→ 点；复核完成。

所有模板 2026-11-26 实机帧采集（res/img/sanbaihuan_jigsaw_*）。
"""
import time

import cv2
import numpy as np

from ..core.app_utils import human_click, log
from ..device import map as game_map

TAG = "[300环拼图] "

# ---- 模板与搜索带（窗口 1378x831 相对坐标）----
READY_ICON = "sanbaihuan_jigsaw_ready.png"      # 「准备好了」165x48
DONE_ICON = "sanbaihuan_jigsaw_done.png"        # 「完成」78x36
GIVEUP_ICON = "sanbaihuan_jigsaw_giveup.png"    # 「放弃」78x36
CONFIRM_ICON = "sanbaihuan_jigsaw_confirm.png"  # 二次确认「确定」60x35
READY_BAND = (520, 405, 810, 520)
DONE_BAND = (850, 480, 1010, 565)
GIVEUP_BAND = (850, 540, 1010, 620)
CONFIRM_BAND = (520, 440, 720, 540)
# 实机判别（2026-11-26）：准备好了 1.000 / 关闭态 0.331；
# 完成 1.000 / 参考态 0.877（参考态按钮灰显）；确定 1.000 / 其它 ≤0.59
READY_THRESHOLD = 0.9
BUTTON_THRESHOLD = 0.9

# ---- 4x4 网格：Sobel 边缘积分 + 五线等差拟合（随地图/面板自适应）----
# 拼图面板随地图长宽比变化（长安东/女儿村/天宫实测位置/高度不同），
# 固定锚点偏移会切错格。以「准备好了」按钮中心为参照取 ROI，按列/行
# 的 Sobel 边缘强度找格线，拟合 5 条等间距线（允许个别线漏检）。
GRID_ROI_LEFT, GRID_ROI_RIGHT = 330, 200    # ROI = 按钮中心左/右
GRID_ROI_TOP, GRID_ROI_BOTTOM = 230, 180    # ROI = 按钮中心上/下
GRID_MIN_W, GRID_MAX_W = 300, 520
GRID_MIN_H, GRID_MAX_H = 220, 400
GRID_GAP_MIN, GRID_GAP_MAX = 50, 150
GRID_PEAK_SIGMA = 0.8           # 峰值阈值 = mean + 0.8*std（实测）
GRID_FIT_TOL = 6                # 候选峰与拟合线的匹配容差（px）
GRID_INSET = 5                  # 参考图/取块内缩，避开羊皮纸边框
TILE_INSET = 10                 # 拼图块取内容内缩（撕纸边）
TEMPLATE_RATIO = 0.2            # 每块取中央 60% 子图做匹配
HIGH_SCORE = 0.9                # 高置信匹配阈值（实机 1.000）
LOW_SCORE = 0.35                # 低于此分视为该格被参考图按钮遮挡

SWAP_CLICK_GAP = 0.45           # 交换两次点击间隔
SWAP_SETTLE = 0.55              # 交换后等待渲染
READY_WAIT = 2.0                # 点开始后等参考按钮消失
SOLVE_DEADLINE = 540.0          # 单局最长处理（游戏限时 10 分钟）
VERIFY_PASSES = 3               # 交换后复核/补交换轮数


def _bgr_frame(img):
    return cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)


def _match(frame_bgr, icon, band, threshold):
    """模板匹配（限定搜索带），返回 (score, 中心点) 或 None。

    注意：map.best_match_np 已返回**匹配中心**（2026-11-26 实机教训：
    先前又加了一次模板尺寸/2，准备好了点偏 76px、完成点偏 39px，
    导致点空后误走完成/二次确认失败）。
    """
    score, pt = game_map.best_match_np(frame_bgr, icon, region=band)
    if score < threshold:
        return None
    return score, (int(pt[0]), int(pt[1]))


def is_ready(frame_bgr):
    """参考态（显示完整地图 + 「准备好了」按钮）。"""
    return _match(frame_bgr, READY_ICON, READY_BAND,
                  READY_THRESHOLD) is not None


def _edge_peaks(values, offset):
    """一维边缘强度峰（Sobel 列/行均值的局部极大，合并相邻峰）。"""
    th = float(values.mean() + values.std() * GRID_PEAK_SIGMA)
    raw = []
    for i in range(1, len(values) - 1):
        if values[i] >= th and values[i] >= values[i - 1]                 and values[i] >= values[i + 1]:
            raw.append((i, float(values[i])))
    merged = []
    for pos, sc in raw:
        if merged and pos - merged[-1][0] <= 5:
            if sc > merged[-1][1]:
                merged[-1] = (pos, sc)
        else:
            merged.append((pos, sc))
    return [(offset + pos, sc) for pos, sc in merged]


def _fit_five_lines(peaks, lo, hi):
    """从候选峰拟合 5 条等间距网格线（允许个别线漏检）。

    枚举相邻两峰作为等差线对（gap 50~150），尝试两种相位（a 作为第
    k 条线），5 条线必须落在 ROI 内；按「命中峰数多 → 拟合误差小 →
    居中」择优。实测 2026-11-26 长安东/女儿村五线均一次命中。
    """
    best = None
    for i in range(len(peaks)):
        for j in range(len(peaks)):
            if i == j:
                continue
            a, b = peaks[i][0], peaks[j][0]
            if b < a:
                a, b = b, a
            gap = b - a
            if not (GRID_GAP_MIN <= gap <= GRID_GAP_MAX):
                continue
            for k in range(4):  # a 作为第 k 条线
                lines = [a + (t - k) * gap for t in range(5)]
                if lines[0] < lo + 2 or lines[-1] > hi - 2:
                    continue
                matches = sum(
                    1 for L in lines
                    if any(abs(p[0] - L) <= GRID_FIT_TOL for p in peaks))
                if matches < 3:
                    continue
                err = sum(
                    min(abs(p[0] - L) for L in lines)
                    for p in peaks
                    if min(abs(p[0] - L) for L in lines) <= GRID_FIT_TOL)
                center = sum(lines) / 5.0
                score = (-matches, err, abs(center - (lo + hi) / 2.0))
                if best is None or score < best[0]:
                    best = (score, lines)
    if best is None:
        return None
    return [int(round(v)) for v in best[1]]


def detect_grid_lines(frame_bgr, button_center):
    """动态检测拼图 4x4 网格线，返回 (xs, ys) 或 None。

    button_center：「准备好了」按钮中心（ROI 参照）。按列/行 Sobel
    边缘强度积分找格线（格线贯穿整幅地图，边缘积分远高于内容纹理），
    再拟合 5 条等间距线；面板位置/地图长宽比变化时自适应。
    """
    bx, by = button_center
    h, w = frame_bgr.shape[:2]
    x0 = max(0, bx - GRID_ROI_LEFT)
    x1 = min(w, bx + GRID_ROI_RIGHT)
    y0 = max(0, by - GRID_ROI_TOP)
    y1 = min(h, by + GRID_ROI_BOTTOM)
    if x1 - x0 < 200 or y1 - y0 < 150:
        return None
    gray = cv2.cvtColor(frame_bgr[y0:y1, x0:x1],
                        cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)).mean(axis=1)
    xs = _fit_five_lines(_edge_peaks(gx, x0), x0, x1)
    ys = _fit_five_lines(_edge_peaks(gy, y0), y0, y1)
    if xs is None or ys is None:
        return None
    if not (GRID_MIN_W <= xs[-1] - xs[0] <= GRID_MAX_W
            and GRID_MIN_H <= ys[-1] - ys[0] <= GRID_MAX_H):
        return None
    return xs, ys


def _cell_rect(xs, ys, r, c, inset):
    return (xs[c] + inset, ys[r] + inset, xs[c + 1] - inset, ys[r + 1] - inset)


def _cell_index(r, c, n=4):
    return r * n + c


def _tile_targets(ref_bgr, frame_bgr, xs, ys, button_rect):
    """逐块匹配参考图，返回 labels[i]=该格块的目标格索引（或 None）。

    ref_bgr：参考态帧（完整目标地图，含「准备好了」按钮遮挡）；
    frame_bgr：拼图开始后的帧；button_rect：参考帧里按钮的绝对矩形。
    高置信匹配唯一占格；被按钮遮挡的格用可见带补全。
    """
    n = 4
    map_rect = (xs[0] + 3, ys[0] + 3, xs[-1] - 3, ys[-1] - 3)
    ref_area = ref_bgr[map_rect[1]:map_rect[3], map_rect[0]:map_rect[2]]
    if ref_area.size == 0:
        return None
    ref_h, ref_w = ref_area.shape[:2]
    labels = [None] * (n * n)
    scores = [0.0] * (n * n)
    used = set()
    high = []
    for r in range(n):
        for c in range(n):
            i = _cell_index(r, c)
            x0, y0, x1, y1 = _cell_rect(xs, ys, r, c, TILE_INSET)
            tile = frame_bgr[y0:y1, x0:x1]
            if tile.size == 0:
                continue
            th, tw = tile.shape[:2]
            mx, my = int(tw * TEMPLATE_RATIO), int(th * TEMPLATE_RATIO)
            tpl = tile[my:th - my, mx:tw - mx]
            if tpl.size == 0 or tpl.shape[0] > ref_h or tpl.shape[1] > ref_w:
                continue
            res = cv2.matchTemplate(ref_area, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            cx = maxloc[0] + tpl.shape[1] / 2
            cy = maxloc[1] + tpl.shape[0] / 2
            gx = int(cx / (ref_w / n))
            gy = int(cy / (ref_h / n))
            gx = max(0, min(n - 1, gx))
            gy = max(0, min(n - 1, gy))
            tcell = _cell_index(gy, gx)
            if maxv >= HIGH_SCORE:
                high.append((maxv, i, tcell))
            else:
                scores[i] = float(maxv)
    # 高置信优先，按分数降序占格（同目标格取最高分）
    for score, i, tcell in sorted(high, reverse=True):
        if labels[i] is not None or tcell in used:
            continue
        labels[i] = tcell
        scores[i] = score
        used.add(tcell)
    # 被按钮遮挡的格：用格内不被按钮覆盖的最大矩形带补全
    covered = _covered_cells(xs, ys, button_rect)
    left_cells = [i for i in range(n * n) if labels[i] is None]
    left_targets = [i for i in range(n * n) if i not in used]
    if not left_cells and not left_targets:
        return labels
    if len(left_cells) != len(left_targets):
        # 数量对不上：只用高置信结果（调用方会判定失败/重试）
        return labels
    for cell in left_cells:
        r, c = divmod(cell, n)
        # 块自身的格原点（frame 坐标）——块内像素按相对偏移取；
        # 遮挡发生在**目标格**（参考图按钮），所以按目标格的可见带取
        tile_ox, tile_oy = xs[c], ys[r]
        best = (-1.0, None)
        for tgt in left_targets:
            if tgt not in covered:
                continue
            tr, tc = divmod(tgt, n)
            band = _cell_visible_band(xs, ys, tr, tc, button_rect)
            if band is None:
                continue
            bx0, by0, bx1, by1 = band
            cell_ref = ref_bgr[by0:by1, bx0:bx1]
            # 可见带在目标格内的相对偏移 → 拼图块内同一位置
            ox0 = bx0 - xs[tc]
            oy0 = by0 - ys[tr]
            ox1 = bx1 - xs[tc]
            oy1 = by1 - ys[tr]
            tile_band = frame_bgr[tile_oy + oy0:tile_oy + oy1,
                                  tile_ox + ox0:tile_ox + ox1]
            if tile_band.size == 0:
                continue
            if tile_band.shape[:2] != cell_ref.shape[:2]:
                # 各格宽高因卷轴透视略有差异：带缩放到参考带尺寸再比
                tile_band = cv2.resize(
                    tile_band, (cell_ref.shape[1], cell_ref.shape[0]))
            res = cv2.matchTemplate(cell_ref, tile_band,
                                    cv2.TM_CCOEFF_NORMED)
            sc = float(res.max())
            if sc > best[0]:
                best = (sc, tgt)
        if best[1] is not None and best[0] >= LOW_SCORE:
            labels[cell] = best[1]
            scores[cell] = best[0]
            left_targets.remove(best[1])
    return labels


def _covered_cells(xs, ys, button_rect):
    """参考图按钮覆盖到的格（绝对矩形与格相交面积 > 格面积 15%）。"""
    cells = []
    if button_rect is None:
        return cells
    bl, bt, br, bb = button_rect
    for r in range(4):
        for c in range(4):
            l, t, rr, brow = xs[c], ys[r], xs[c + 1], ys[r + 1]
            iw = max(0, min(br, rr) - max(bl, l))
            ih = max(0, min(bb, brow) - max(bt, t))
            if iw * ih > 0.15 * (rr - l) * (brow - t):
                cells.append(_cell_index(r, c))
    return cells


BAND_INSET = 3  # 可见带只避让按钮/边框，不做块级 10px 内缩


def _cell_visible_band(xs, ys, r, c, button_rect):
    """格内不被参考图按钮覆盖的最大横向带（上带/下带取大者）。"""
    l, t, rr, bb = xs[c], ys[r], xs[c + 1], ys[r + 1]
    bl, bt, br, bbot = button_rect
    # 只在按钮与该格水平相交时才有遮挡
    if br <= l or bl >= rr:
        return (l + BAND_INSET, t + BAND_INSET,
                rr - BAND_INSET, bb - BAND_INSET)
    top_band = (l + BAND_INSET, t + BAND_INSET,
                rr - BAND_INSET, bt - BAND_INSET)
    bottom_band = (l + BAND_INSET, bbot + BAND_INSET,
                   rr - BAND_INSET, bb - BAND_INSET)
    top_h = max(0, top_band[3] - top_band[1])
    bottom_h = max(0, bottom_band[3] - bottom_band[1])
    band = top_band if top_h >= bottom_h else bottom_band
    if band[3] - band[1] < 15:
        return None
    return band


def swap_plan(labels):
    """labels[i]=第 i 格当前块的目标格 → 交换序列 [(a,b), ...]。

    选择排序：交换两格后它们的内容互换，labels 同步互换。
    非排列（None/重复）返回 None。
    """
    if labels is None or any(v is None for v in labels):
        return None
    if sorted(labels) != list(range(len(labels))):
        return None
    cur = list(labels)
    swaps = []
    for i in range(len(cur)):
        if cur[i] != i:
            j = cur.index(i)
            cur[i], cur[j] = cur[j], cur[i]
            swaps.append((i, j))
    return swaps


def _cell_center(xs, ys, idx):
    r, c = divmod(idx, 4)
    return ((xs[c] + xs[c + 1]) // 2, (ys[r] + ys[r + 1]) // 2)


def _apply_swaps(win, xs, ys, swaps):
    for a, b in swaps:
        ax, ay = _cell_center(xs, ys, a)
        bx, by = _cell_center(xs, ys, b)
        human_click(win, ax, ay)
        time.sleep(SWAP_CLICK_GAP)
        human_click(win, bx, by)
        time.sleep(SWAP_SETTLE)


def _capture(win):
    img = win.capture(force_activate=False)
    if img is None:
        return None, None
    return img, _bgr_frame(img)


def _click_button(win, icon, band, threshold, prefix=TAG):
    """模板定位按钮并点击；返回 (ok, 描述)。"""
    _img, bgr = _capture(win)
    if bgr is None:
        return False, "截图失败"
    hit = _match(bgr, icon, band, threshold)
    if hit is None:
        return False, f"未找到按钮模板 {icon}"
    _score, (cx, cy) = hit
    human_click(win, cx, cy)
    log(f"{prefix}已点击按钮 {icon} @({cx},{cy})", "SYS")
    return True, f"已点击 {icon}"


def _finish(win, prefix=TAG):
    """点「完成」→ 等二次确认 → 点「确定」。"""
    if not _click_button(win, DONE_ICON, DONE_BAND, BUTTON_THRESHOLD, prefix)[0]:
        return False, "未找到「完成」按钮"
    deadline = time.time() + 4.0
    while time.time() < deadline:
        time.sleep(0.3)
        _img, bgr = _capture(win)
        if bgr is None:
            continue
        if _match(bgr, CONFIRM_ICON, CONFIRM_BAND,
                  BUTTON_THRESHOLD) is not None:
            ok, info = _click_button(win, CONFIRM_ICON, CONFIRM_BAND,
                                     BUTTON_THRESHOLD, prefix)
            if ok:
                return True, "拼图完成并已二次确认"
    return False, "点完成未出现二次确认"


def _give_up(win, prefix=TAG):
    """失败收尾：点放弃并尝试二次确认（best effort）。"""
    if _click_button(win, GIVEUP_ICON, GIVEUP_BAND, BUTTON_THRESHOLD,
                     prefix)[0]:
        time.sleep(0.6)
        _img, bgr = _capture(win)
        if bgr is not None and _match(
                bgr, CONFIRM_ICON, CONFIRM_BAND,
                BUTTON_THRESHOLD) is not None:
            _click_button(win, CONFIRM_ICON, CONFIRM_BAND,
                          BUTTON_THRESHOLD, prefix)


def play(win, prefix=TAG, deadline=SOLVE_DEADLINE):
    """求解一局拼图。返回 (是否完成, 描述)。

    调用前应刚检测到「准备好了」参考态；本函数负责：截参考图 → 点开始
    → 匹配 → 交换 → 完成/二次确认。失败会 best-effort 点放弃。
    """
    t0 = time.time()
    img, bgr = _capture(win)
    if bgr is None:
        return False, "截图失败"
    hit = _match(bgr, READY_ICON, READY_BAND, READY_THRESHOLD)
    if hit is None:
        return False, "未检测到拼图参考态（准备好了）"
    _score, ready_center = hit
    log(f"{prefix}拼图参考态：准备好了 @({ready_center[0]},"
        f"{ready_center[1]})", "SYS")
    # 点「准备好了」：必须确认参考按钮消失才算真正开始；未消失说明点击
    # 落空/面板移动，重新定位后重试（2026-11-26 实机：面板整体偏移
    # +82,+24 时点空，随后把完整参考图当已拼好、误点了「完成」）
    started = False
    for click_attempt in (1, 2, 3):
        log(f"{prefix}点击「准备好了」@({ready_center[0]},"
            f"{ready_center[1]})（第{click_attempt}次）", "SYS")
        human_click(win, ready_center[0], ready_center[1])
        deadline_ready = time.time() + READY_WAIT
        while time.time() < deadline_ready:
            time.sleep(0.4)
            _img, bgr2 = _capture(win)
            if bgr2 is not None and not is_ready(bgr2):
                started = True
                break
        if started:
            break
        _img, bgr2 = _capture(win)
        if bgr2 is None:
            return False, "点击准备好了后截图失败"
        hit = _match(bgr2, READY_ICON, READY_BAND, READY_THRESHOLD)
        if hit is None:
            started = True  # 模板读不到且参考态检测失败：按已开始处理
            break
        ready_center = hit[1]
    if not started:
        return False, "三次点击准备好了仍未开始（仍在参考态）"
    # 网格动态检测：拼图面板随地图长宽比变化，不能用固定锚点偏移
    _img, bgr3 = _capture(win)
    if bgr3 is None:
        return False, "开始拼图后截图失败"
    grid = detect_grid_lines(bgr3, ready_center)
    if grid is None:
        _give_up(win, prefix)
        return False, "拼图网格检测失败（面板位置/地图异常）"
    xs, ys = grid
    log(f"{prefix}拼图网格：x {xs} y {ys}", "SYS")
    # 参考图的「准备好了」按钮水平居中于地图：遮挡带取中间两列 +
    # 按钮中心 y 上下 22px（模板只是按钮局部，不能直接用模板矩形）
    button_rect = (xs[1], ready_center[1] - 22,
                   xs[3], ready_center[1] + 22)
    # 最多 VERIFY_PASSES 轮交换 + 每轮后复核；最后一轮交换后再复核一次
    for attempt in range(VERIFY_PASSES + 1):
        if time.time() - t0 > deadline:
            _give_up(win, prefix)
            return False, "拼图处理超时"
        _img, cur = _capture(win)
        if cur is None:
            return False, "拼图过程截图失败"
        labels = _tile_targets(bgr, cur, xs, ys, button_rect)
        if labels is None:
            _give_up(win, prefix)
            return False, "拼图匹配失败（参考图/网格异常）"
        missing = [i for i, v in enumerate(labels) if v is None]
        if missing:
            _give_up(win, prefix)
            return False, f"拼图匹配不完整（缺 {len(missing)} 块）"
        swaps = swap_plan(labels)
        if swaps is None:
            _give_up(win, prefix)
            return False, "拼图排列无效（匹配目标格异常）"
        if not swaps:
            break
        if attempt >= VERIFY_PASSES:
            _give_up(win, prefix)
            return False, "拼图交换后仍未复原"
        log(f"{prefix}第{attempt + 1}轮交换：{len(swaps)} 次", "SYS")
        _apply_swaps(win, xs, ys, swaps)
        time.sleep(0.4)
    # 最终复核：当前帧不得还是参考态（否则「无需交换」是参考图造成的
    # 假象，不能点完成）
    _img, final = _capture(win)
    if final is None:
        return False, "完成前截图失败"
    if is_ready(final):
        return False, "求解结束仍在参考态（拼图未真正开始）"
    return _finish(win, prefix)
