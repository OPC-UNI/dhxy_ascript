# -*- coding: utf-8 -*-
"""四锁四色钥匙开锁（300环「妙趣横生」解锁环）小游戏：Mastermind 求解。

2026-11-27 实机（可乐可口 36096466，宝象国·阿合奇阿洪）完整流程：
- 入口：任务追踪「妙趣横生[江湖历练任务]」，追踪文案「听闻阿合奇阿洪
  获得了藏有上古秘籍的宝匣，过去打听下。」→ 点追踪绿字走到 NPC →
  对话框（阿合奇阿洪）两个选项「解开这奇锁！」「待我仔细斟酌一下。」
  → 点「解开这奇锁！」弹出开锁面板（任务侧把选项加入
  TASK_ACCEPT_OPTIONS，本模块由 wait_handler 在面板出现时调用）。
- 面板：标题「剩余开锁次数：10」；上方 4 个锁槽；右侧四把钥匙（金/红/
  银/绿竖排）；锁槽右侧撤销箭头（删最后一把）；「开锁」按钮；「开锁
  记录」行 1/2/3…（自上而下追加，行高 61px，首行圆点中心 y=452）；
  每行显示本次 4 把钥匙 + 两个计数徽章：绿点=全对（颜色和位置都对）、
  橙点=半对（只有颜色对、位置不对）。底部图例原文：「全对：颜色和位置
  都正确 半对：只有颜色正确，位置不正确」。
- 交互：点右侧钥匙→依次填入下一个空槽（可重复选同一把）；撤销箭头
  删最后一把；凑满 4 把点「开锁」→ 次数 -1，记录行追加。
- 求解：标准 Mastermind（4 色 × 4 位、可重复、反馈=全对/半对），
  候选空间 4^4=256，按信息量最优（最小化最坏分支）逐轮猜；实机一局
  4 次提交解开（金红银绿 1/2 → 金金红红 0/3 → 金金金银 1/2 → 银红金金
  命中），面板关闭并给奖励、任务推进下一环。
- 次数保护（用户 2026-11-27 规则）：可提交次数有限；快到上限时点右上
  角 X 关闭再重开可重置一次（仅限一次）。本模块在剩余次数不足时点 X
  关窗，返回 STATUS_REOPEN，由任务侧重走任务追踪链接重开面板；重开
  后按面板上现存的记录/次数重新求解（重开是否换题未实测，一律以
  面板现况为准）。
- 异常恢复：面板带着已有记录重新进入（任务重启/中途接管）时，按行
  序号 + 钥匙颜色 + 反馈数字重建历史；记录缺行（列表滚动）则不完整，
  历史清零按剩余次数重解。

标定数据（2026-11-27 实机截帧，窗口 1378×831 窗口相对坐标）：
- 开锁按钮模板「sanbaihuan_jiesuo_btn.png」88×40：面板在场 0.949~1.000，
  非面板帧 ≤0.514，阈值 0.8；
- 面板像素静态（同状态两帧槽位 ROI 差分 0.000）；空槽→入钥匙的槽位
  ROI（64×64，灰度差 >30 的像素占比）实测 20.1%~41.6%，空槽 0%，
  以 8% 判「已填入」；
- 钥匙圆盘颜色（记录行与锁槽同款配色）：金 HSV≈H19-22/S69-149；红
  H7-14/S56-135；银 S≤25（低饱和）；绿 H69-74/S70-87。G-B/R-B 比值
  金≈0.62-0.75、红≈0.24-0.62（取中位数 + 0.55 阈值）与绿（G>R 且
  G>B）、银（无饱和色块）分离，实机 5 行记录 + 2 帧锁槽 100% 命中；
- 计数徽章数字（草书体）：绿点圆心 x=711、橙点 x=748，圆点右侧
  20×22px 数字框 3x LANCZOS 放大后区域 OCR 稳定 1.00（1x/2x 常空）；
  绿点可能被识别成前导 0（「1」→「01」、「0」→「00」），解析取末位；
- 剩余次数标题条 (500,252)-(720,300) 1x OCR 实测 1.00；
- 记录行序号（大字）2x OCR：行 1 的 1x 偶发空，2x 稳定。

import 期零副作用；面板内交互全部阻塞在本模块 play() 内，任务侧只做
触发与关窗重开的状态机（见 tasks/sanbaihuan.py `_jiesuo_tick`）。
"""
import itertools
import re
import time

import numpy as np
from PIL import Image

from ..core.app_utils import human_click, human_scroll, log
from ..device import map as game_map

# 颜色编号（选钥匙顺序/点击坐标一一对应）
GOLD, RED, SILVER, GREEN = 0, 1, 2, 3
COLOR_NAMES = ("金", "红", "银", "绿")
SLOTS = 4
ALL_CODES = tuple(itertools.product(range(4), repeat=SLOTS))  # 4^4 = 256

STATUS_DONE = "done"      # 面板关闭（解开/被外部关闭），任务侧交回 follow
STATUS_REOPEN = "reopen"  # 本轮已点 X 关窗，任务侧重走追踪链接重开
STATUS_FAIL = "fail"      # 无计可施（已尽量关窗），任务侧按失败记录

# ---- 面板判据（见模块注释标定）----
PANEL_ICON = "sanbaihuan_jiesuo_btn.png"   # 「开锁」按钮（面板在场判据）
PANEL_BAND = (830, 588, 940, 650)          # 按钮搜索条带（模板中心 884,618）
PANEL_THRESHOLD = 0.8
PANEL_GONE_CONFIRM = 2                     # 连续未命中次数判面板消失

# ---- 面板几何（窗口相对坐标，2026-11-27 实机标定）----
TITLE_REGION = (500, 252, 720, 300)        # 「剩余开锁次数：N」
MAX_ATTEMPTS = 10                          # 默认次数（标题 OCR 失败时兜底）
SLOT_CENTERS = ((467, 339), (543, 339), (619, 339), (695, 339))
SLOT_ROI_HALF = 32                         # 槽位空/满差分 ROI 半宽
SLOT_DIFF_THRESHOLD = 30                   # 灰度差阈值（空槽同状态实测 0）
SLOT_CHANGED_PCT = 8.0                     # 差分像素占比 ≥8% 判已填入（实测满槽 ≥20.1）
SLOT_COLOR_PATCH = 11                      # 槽内钥匙圆盘取色块偏移
UNDO_POINT = (774, 338)                    # 撤销箭头（删最后一把）
UNDO_CLICKS = 6                            # 进入时清空残留选择（≤4 把，多删无害）
KEY_POINTS = ((883, 316), (883, 392), (883, 466), (883, 541))  # 金 红 银 绿
KEY_FILL_TIMEOUT = 0.9                     # 单把钥匙落位等待
KEY_FILL_RETRY = 2                         # 单击未落位的重试次数
SUBMIT_SETTLE = 0.2                        # 开锁点击后开始等新记录行
CLOSE_POINT = (961, 240)                   # 面板右上角 X（关窗重开）

# ---- 开锁记录（行自上而下追加）----
ROW_KEY_X = (498, 555, 613, 671)           # 每行 4 把钥匙圆盘中心 x
ROW_REGION = (695, 425, 790, 645)          # 徽章圆点搜索区（右边界避开图例）
DOT_GROUP_GAP = 6                          # 圆点像素按 y 聚类间隙
DOT_MIN_PIXELS = 10                        # 单个圆点最少像素
DOT_PAIR_TOL = 5                           # 绿/橙圆点同一行的 y 容差
BADGE_DIGIT_REGIONS = ((716, 736), (754, 774))  # 绿/橙数字框 x 范围
BADGE_DIGIT_DY = 11                        # 数字框半高
BADGE_DIGIT_SCALE = 3                      # 3x LANCZOS（实机 1x/2x 常空）
ROW_NO_REGION_X = (443, 488)               # 行序号框 x 范围
ROW_NO_DY = 20
ROW_NO_SCALE = 2                           # 行序号 2x OCR（1x 行 1 偶发空）

# ---- 节奏 ----
CLICK_GAP = 0.25                           # 选钥匙点击间隔
UNDO_GAP = 0.2
FILL_POLL = 0.12                           # 槽位落位轮询
NEW_ROW_TIMEOUT = 6.0                      # 开锁后等待新记录行
NEW_ROW_POLL = 0.2
FEEDBACK_RETRY = 4                         # 新行数字读失败重试次数
FEEDBACK_RETRY_GAP = 0.25
REOPEN_WAIT = 3.0                          # 点 X 等面板关闭
SUBMIT_RETRY = 2                           # 开锁未生成新记录行的重试次数
RECORD_SCROLL_POINT = (610, 520)           # 记录列表中心（滚动兜底取点）
RECORD_SCROLL_LINES = -3                   # 向下滚 3 行（human_scroll 负数向下）
SCROLL_AFTER_DECREMENT = 1.2               # 次数已减但新行不可见 → 滚动兜底


# ---------------- Mastermind 求解（纯函数，单测覆盖） ----------------

def feedback(guess, secret):
    """Mastermind 反馈：(全对, 半对)。

    全对=颜色和位置都对；半对=颜色对但位置不对（同一颜色按两边出现
    次数取小，先扣全对）。与面板图例一致（2026-11-27 实机验证：
    金红银绿 vs 真解 银红金金 → 1/2；金金红红 → 0/3；金金金银 → 1/2）。
    """
    exact = 0
    gcount = [0, 0, 0, 0]
    scount = [0, 0, 0, 0]
    for g, s in zip(guess, secret):
        if g == s:
            exact += 1
        else:
            gcount[g] += 1
            scount[s] += 1
    return exact, sum(min(gcount[i], scount[i]) for i in range(4))


def filter_codes(cands, guess, exact, coloronly):
    """按一次反馈过滤候选（保留与反馈一致的代码）。"""
    want = (exact, coloronly)
    return [c for c in cands if feedback(guess, c) == want]


def choose_guess(cands):
    """信息量最优猜测：最小化最坏反馈分支，平手取分支平方和小、候选内优先。

    候选空间仅 256，直接枚举全部猜测（含非候选猜测——纯探测猜可能在
    最坏分支上更优）。返回 4 元组代码。
    """
    cands = list(cands)
    if not cands:
        return (0, 0, 0, 0)
    if len(cands) == 1:
        return tuple(cands[0])
    cand_set = set(cands)
    best_key = None
    best_guess = None
    for g in ALL_CODES:
        buckets = {}
        for c in cands:
            fb = feedback(g, c)
            buckets[fb] = buckets.get(fb, 0) + 1
        worst = max(buckets.values())
        sq = sum(v * v for v in buckets.values())
        key = (worst, sq, 0 if g in cand_set else 1)
        if best_key is None or key < best_key:
            best_key = key
            best_guess = g
            if worst == 1 and sq == len(cands):
                break  # 已可一猜分辨全部候选，理论最优
    return best_guess


def needed_guesses(n):
    """候选数 → 保守估计还需的提交次数（用于次数保护/关窗重开）。"""
    if n <= 1:
        return 1
    if n <= 2:
        return 2
    if n <= 6:
        return 3
    if n <= 24:
        return 4
    return 5


# ---------------- 图像识别（面板内单帧复用） ----------------

def _bgr(frame):
    return game_map.bgr_frame(frame)


def _capture(win):
    """窗口截图（不抢焦点）；失败重试一次。"""
    for _ in range(2):
        img = win.capture(force_activate=False)
        if img is not None:
            return img
        time.sleep(0.15)
    return None


def _panel_pt(bgr):
    """面板「开锁」按钮中心；不在面板返回 None（条带匹配，不回退全帧）。"""
    score, pt = game_map.best_match_np(bgr, PANEL_ICON, region=PANEL_BAND)
    return pt if score >= PANEL_THRESHOLD else None


def is_ready(frame_bgr):
    """单帧判定面板是否在场（wait_handler 每轮询触发）。"""
    try:
        return _panel_pt(frame_bgr) is not None
    except Exception:
        return False


def _parse_badge_text(text):
    """徽章数字解析：绿点可能被 OCR 成前导 0（「1」→「01」）。"""
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    if len(digits) == 1:
        return int(digits)
    if len(digits) == 2 and digits[0] == "0":
        return int(digits[1])
    return None


def _ocr_crop(frame, region, scale=1, min_score=0.5):
    """裁剪 → 可选放大 → 区域 OCR，返回拼接文本。"""
    crop = frame.crop(region)
    if scale > 1:
        crop = crop.resize((crop.width * scale, crop.height * scale),
                           Image.LANCZOS)
    items, _ = game_map.ocr_regions(crop, [(0, 0, crop.width, crop.height)])
    return "".join(t for t, _r, s in items if s >= min_score)


def _read_attempts(frame):
    """剩余开锁次数（标题 OCR）；失败返回 None。"""
    text = _ocr_crop(frame, TITLE_REGION, scale=1)
    m = re.search(r"剩余开锁次数[：:]?\s*(\d+)", text or "")
    return int(m.group(1)) if m else None


def _dot_rows(frame):
    """记录行徽章圆点中心 y 列表（升序，绿/橙成对才算一行）。"""
    rgb = np.asarray(frame.convert("RGB")).astype(np.int16)
    l, t, r, b = ROW_REGION
    sub = rgb[t:b, l:r]
    red, green, blue = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    green_mask = (green > 120) & (green - red > 40) & (green - blue > 40)
    orange_mask = ((red > 180) & (green > 120) & (green < 220)
                   & (blue < 120) & (red - blue > 100))
    ys = np.sort(np.nonzero(green_mask)[0])
    if len(ys) == 0:
        return []
    groups = [[int(ys[0])]]
    for y in ys[1:]:
        if int(y) - groups[-1][-1] <= DOT_GROUP_GAP:
            groups[-1].append(int(y))
        else:
            groups.append([int(y)])
    orange_ys = np.nonzero(orange_mask)[0]
    rows = []
    for g in groups:
        if len(g) < DOT_MIN_PIXELS:
            continue
        rel = int(round(float(np.mean(g))))
        if len(orange_ys) == 0 or not np.any(
                np.abs(orange_ys - rel) <= DOT_PAIR_TOL):
            continue
        rows.append(rel + t)
    return rows


def _read_digit(frame, x0, x1, cy):
    text = _ocr_crop(frame, (x0, cy - BADGE_DIGIT_DY, x1, cy + BADGE_DIGIT_DY),
                     scale=BADGE_DIGIT_SCALE)
    return _parse_badge_text(text)


def _read_badges(frame, cy):
    """一行两个徽章 → (全对, 半对)；任一读不出返回 None。"""
    exact = _read_digit(frame, BADGE_DIGIT_REGIONS[0][0],
                        BADGE_DIGIT_REGIONS[0][1], cy)
    coloronly = _read_digit(frame, BADGE_DIGIT_REGIONS[1][0],
                            BADGE_DIGIT_REGIONS[1][1], cy)
    if exact is None or coloronly is None:
        return None
    if exact + coloronly > SLOTS:
        return None
    return exact, coloronly


def _read_row_no(frame, cy):
    text = _ocr_crop(frame, (ROW_NO_REGION_X[0], cy - ROW_NO_DY,
                             ROW_NO_REGION_X[1], cy + ROW_NO_DY),
                     scale=ROW_NO_SCALE)
    digits = "".join(ch for ch in (text or "") if ch.isdigit())
    return int(digits) if digits else None


def classify_key_color(rgb, cx, cy):
    """记录行/锁槽内钥匙圆盘颜色 → 颜色编号。

    实机标定（见模块注释）：先取四角色块，绿色优先（G 最高）、无饱和
    色块=银，其余用 (G-B)/(R-B) 中位数分离金/红（0.55 阈值）。
    """
    patches = []
    p = SLOT_COLOR_PATCH
    for dx, dy in ((p, p), (-p, -p), (p, -p), (-p, p)):
        block = rgb[cy + dy - 3:cy + dy + 4, cx + dx - 3:cx + dx + 4]
        m = block.reshape(-1, 3).mean(axis=0)
        patches.append((m, float(m.max() - m.min())))
    colored = [(m, s) for m, s in patches if s >= 30.0]
    if not colored:
        return SILVER
    for m, _s in colored:
        if m[1] > m[0] and m[1] > m[2]:
            return GREEN
    ratios = sorted((m[1] - m[2]) / max(1.0, m[0] - m[2]) for m, _s in colored)
    return RED if ratios[len(ratios) // 2] < 0.55 else GOLD


def _row_colors(frame, cy):
    rgb = np.asarray(frame.convert("RGB")).astype(np.float32)
    return tuple(classify_key_color(rgb, x, cy) for x in ROW_KEY_X)


def _decode_history(frame, rows):
    """按面板现存记录重建历史 [(guess, exact, coloronly), ...]。

    要求行序号从 1 连续（记录列表滚动/缺行时无法完整重建，返回 None，
    调用方以剩余次数从头求解）。钥匙颜色/徽章任一读不出同样返回 None。
    """
    hist = []
    for cy in rows:
        no = _read_row_no(frame, cy)
        fb = _read_badges(frame, cy)
        if no is None or fb is None:
            return None
        hist.append((no, _row_colors(frame, cy), fb))
    hist.sort(key=lambda h: h[0])
    if [h[0] for h in hist] != list(range(1, len(hist) + 1)):
        return None
    return [(h[1], h[2][0], h[2][1]) for h in hist]


# ---------------- 面板交互 ----------------

def _slot_gray(frame):
    return np.asarray(frame.convert("L")).astype(np.int16)


def _slot_roi(gray, center):
    h = SLOT_ROI_HALF
    return gray[center[1] - h:center[1] + h,
                center[0] - h:center[0] + h]


def _slot_refs(frame):
    """空槽参考（面板像素静态，进入时一次采集，全程复用）。"""
    gray = _slot_gray(frame)
    return [_slot_roi(gray, c).copy() for c in SLOT_CENTERS]


def _changed_pct(gray, ref, center):
    cur = _slot_roi(gray, center)
    if cur.shape != ref.shape:
        return 0.0
    d = np.abs(cur - ref)
    return 100.0 * float((d > SLOT_DIFF_THRESHOLD).mean())


def _wait_slot_filled(win, idx, refs, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(FILL_POLL)
        frame = _capture(win)
        if frame is None:
            continue
        gray = _slot_gray(frame)
        if _changed_pct(gray, refs[idx], SLOT_CENTERS[idx]) >= SLOT_CHANGED_PCT:
            return True
    return False


def _clear_slots(win):
    """撤销箭头连点清空选择槽；面板中途消失返回 False。"""
    for _ in range(UNDO_CLICKS):
        frame = _capture(win)
        if frame is None:
            continue
        if _panel_pt(_bgr(frame)) is None:
            return False
        human_click(win, UNDO_POINT[0], UNDO_POINT[1])
        time.sleep(UNDO_GAP)
    return True


def _fill_guess(win, guess, refs):
    """按猜测依次点四把钥匙；每把确认落位，最后校验四槽颜色。"""
    for i, color in enumerate(guess):
        placed = False
        for _ in range(KEY_FILL_RETRY + 1):
            human_click(win, KEY_POINTS[color][0], KEY_POINTS[color][1])
            time.sleep(CLICK_GAP)
            if _wait_slot_filled(win, i, refs, KEY_FILL_TIMEOUT):
                placed = True
                break
        if not placed:
            return False
    time.sleep(0.2)  # 最后一把钥匙的落位动画/余量
    frame = _capture(win)
    if frame is None:
        return False
    rgb = np.asarray(frame.convert("RGB")).astype(np.float32)
    actual = tuple(classify_key_color(rgb, *SLOT_CENTERS[i])
                   for i in range(SLOTS))
    return actual == tuple(guess)


def _wait_new_row(win, prev_row_no, before_attempts):
    """等待记录行序号超过 prev_row_no。

    返回 (frame, 行中心 y, 行序号)；面板关闭返回字符串 "gone"；超时 None。
    记录列表若满屏不自动滚动，新行会看不见——检测到剩余次数已减
    （开锁确实执行）且 1.2s 内仍无新行时，在记录区向下滚 3 行兜底一次。
    """
    deadline = time.time() + NEW_ROW_TIMEOUT
    gone = 0
    scrolled = False
    dec_at = None
    while time.time() < deadline:
        time.sleep(NEW_ROW_POLL)
        frame = _capture(win)
        if frame is None:
            continue
        if _panel_pt(_bgr(frame)) is None:
            gone += 1
            if gone >= PANEL_GONE_CONFIRM:
                return "gone"
            continue
        gone = 0
        for cy in reversed(_dot_rows(frame)):
            no = _read_row_no(frame, cy)
            if no is not None and no > prev_row_no:
                return frame, cy, no
        if not scrolled:
            if dec_at is None:
                now_attempts = _read_attempts(frame)
                if now_attempts is not None and before_attempts is not None \
                        and now_attempts < before_attempts:
                    dec_at = time.time()
            elif time.time() - dec_at >= SCROLL_AFTER_DECREMENT:
                human_scroll(win, RECORD_SCROLL_POINT[0],
                             RECORD_SCROLL_POINT[1], RECORD_SCROLL_LINES)
                scrolled = True
    return None


def _read_feedback(win, frame, cy):
    """读新记录行反馈；数字可能滞后渲染，有限重试。返回 (frame, fb|None)。"""
    for _ in range(FEEDBACK_RETRY):
        fb = _read_badges(frame, cy)
        if fb is not None:
            return frame, fb
        time.sleep(FEEDBACK_RETRY_GAP)
        frame = _capture(win) or frame
    return frame, None


def _close_panel(win, prefix, why, want_reopen=True):
    """点面板右上角 X 关窗。

    want_reopen=True（本实例尚未重开过）返回 STATUS_REOPEN 交任务侧重走
    追踪链接；False 返回 STATUS_FAIL——已重开过还返回 reopen 会让任务侧
    反复重走（次数未重置时死循环）。
    """
    frame = _capture(win)
    if frame is None or _panel_pt(_bgr(frame)) is None:
        return STATUS_FAIL, f"{why}；面板已不在场"
    human_click(win, CLOSE_POINT[0], CLOSE_POINT[1])
    deadline = time.time() + REOPEN_WAIT
    misses = 0
    while time.time() < deadline:
        time.sleep(0.2)
        frame = _capture(win)
        if frame is None:
            continue
        if _panel_pt(_bgr(frame)) is None:
            misses += 1
            if misses >= PANEL_GONE_CONFIRM:
                break
        else:
            misses = 0
    if misses < PANEL_GONE_CONFIRM:
        log(f"[{prefix}] 开锁：点 X 后面板未关闭", "WARN")
        return STATUS_FAIL, f"{why}；点 X 关窗未生效"
    if not want_reopen:
        return STATUS_FAIL, f"{why}，已关闭面板（本局重开次数已用完）"
    return STATUS_REOPEN, f"{why}，已关闭面板（待任务侧重开一次）"


# ---------------- 主流程 ----------------

def play(win, prefix="300环", allow_reopen=True):
    """面板内完整求解（阻塞）。返回 (状态, 描述)，状态见 STATUS_*。

    流程：读取现存记录/剩余次数 → 清空选择槽并采集空槽参考 → 循环
    「选最优猜测 → 逐把点钥匙（逐把确认落位）→ 开锁 → 等新记录行 →
    读反馈 → 过滤候选」直到命中或次数不足。次数不足且未被禁用重开时
    点 X 关窗返回 STATUS_REOPEN，由任务侧重走追踪链接重开面板。
    """
    try:
        return _play(win, prefix, allow_reopen)
    except Exception as exc:  # 小游戏异常不得打断任务主循环
        log(f"[{prefix}] 开锁小游戏异常：{exc!r}", "ERROR")
        return STATUS_FAIL, f"开锁小游戏异常：{exc!r}"


def _play(win, prefix, allow_reopen):
    frame = _capture(win)
    if frame is None:
        return STATUS_FAIL, "开锁：截图失败"
    if _panel_pt(_bgr(frame)) is None:
        return STATUS_DONE, "开锁：面板不在场"
    rows = _dot_rows(frame)
    attempts = _read_attempts(frame)
    history = _decode_history(frame, rows) if rows else []
    if history is None:
        # 记录不完整（列表滚动/行号或反馈识别失败）：无法安全重建历史，
        # 按剩余次数硬解会把旧记录误当新提交反馈，直接关窗重开重置。
        log(f"[{prefix}] 开锁：面板已有 {len(rows)} 条记录但无法完整重建，"
            f"关闭面板重开", "WARN")
        return _close_panel(win, prefix, "记录无法完整重建",
                            want_reopen=allow_reopen)
    last_row_no = history[-1][0] if history else 0
    if attempts is None:
        attempts = max(0, MAX_ATTEMPTS - len(history))
    if history:
        log(f"[{prefix}] 开锁：重建 {len(history)} 条历史记录，"
            f"剩余 {attempts} 次", "SYS")
    cands = list(ALL_CODES)
    for guess, ex, co in history:
        cands = filter_codes(cands, guess, ex, co)
    if not cands:
        log(f"[{prefix}] 开锁：历史记录相互矛盾（识别误读），忽略历史重解",
            "WARN")
        cands = list(ALL_CODES)
    log(f"[{prefix}] 开锁：剩余 {attempts} 次，候选 {len(cands)} 种", "SYS")
    if not _clear_slots(win):
        return STATUS_DONE, "开锁：面板已关闭（清空选择时）"
    frame = _capture(win)
    if frame is None:
        return STATUS_FAIL, "开锁：截图失败"
    refs = _slot_refs(frame)
    while True:
        if attempts <= 0:
            return _close_panel(win, prefix, "开锁次数已用尽",
                                want_reopen=allow_reopen)
        if allow_reopen and needed_guesses(len(cands)) > attempts:
            return _close_panel(
                win, prefix,
                f"剩余次数 {attempts} 不足以解 {len(cands)} 种候选")
        guess = choose_guess(cands)
        log(f"[{prefix}] 开锁：提交 "
            f"{''.join(COLOR_NAMES[c] for c in guess)}"
            f"（候选 {len(cands)}，剩余 {attempts}）", "SYS")
        filled = False
        for _ in range(SUBMIT_RETRY + 1):
            if _fill_guess(win, guess, refs):
                filled = True
                break
            log(f"[{prefix}] 开锁：选钥匙未按预期落位，清空重选", "WARN")
            if not _clear_slots(win):
                return STATUS_DONE, "开锁：面板已关闭（重选时）"
        if not filled:
            return _close_panel(win, prefix, "选钥匙反复未落位",
                                want_reopen=allow_reopen)
        frame = _capture(win)
        if frame is None:
            return STATUS_FAIL, "开锁：提交前截图失败"
        pt = _panel_pt(_bgr(frame))
        if pt is None:
            return STATUS_DONE, "开锁：提交前面板关闭"
        before = attempts
        human_click(win, pt[0], pt[1])
        time.sleep(SUBMIT_SETTLE)
        got = _wait_new_row(win, last_row_no, before)
        if got == "gone":
            return STATUS_DONE, "开锁：提交后面板关闭（按成功处理，待任务核对）"
        if got is None:
            now_frame = _capture(win)
            now_attempts = _read_attempts(now_frame) \
                if now_frame is not None else None
            if now_attempts is not None and now_attempts < before:
                # 次数已扣说明提交成功，只是新行没显示（列表未自动滚动/
                # 识别失败）——不能重复提交再烧一次次数
                return _close_panel(win, prefix,
                                    "开锁已扣次数但新记录行不可见",
                                    want_reopen=allow_reopen)
            log(f"[{prefix}] 开锁：开锁后未出现新记录行（本次未消耗次数）",
                "WARN")
            if not _clear_slots(win):
                return STATUS_DONE, "开锁：面板已关闭（提交未生效后）"
            continue
        frame, cy, row_no = got
        frame, fb = _read_feedback(win, frame, cy)
        if fb is None:
            return _close_panel(win, prefix, "记录行反馈数字识别失败",
                                want_reopen=allow_reopen)
        # 提交内容以选钥匙时的逐槽颜色复核为准（刚验证过）；记录行颜色
        # 只做交叉核对，不一致按识别漂移告警、不改写历史
        actual = tuple(guess)
        row_colors = _row_colors(frame, cy)
        if row_colors != actual:
            log(f"[{prefix}] 开锁：记录行钥匙颜色 {row_colors} 与提交 "
                f"{actual} 不一致（按提交为准）", "WARN")
        exact, coloronly = fb
        log(f"[{prefix}] 开锁：第 {row_no} 次 "
            f"{''.join(COLOR_NAMES[c] for c in actual)} → "
            f"全对 {exact} 半对 {coloronly}", "SYS")
        last_row_no = row_no
        if exact == SLOTS:
            return STATUS_DONE, f"开锁成功（第 {row_no} 次提交命中）"
        attempts = _read_attempts(frame)
        if attempts is None:
            attempts = max(0, before - 1)
        history.append((actual, exact, coloronly))
        new_cands = filter_codes(cands, actual, exact, coloronly)
        if not new_cands:
            log(f"[{prefix}] 开锁：反馈与候选矛盾（识别误读），"
                f"忽略历史按剩余次数重解", "WARN")
            new_cands = list(ALL_CODES)
        cands = new_cands
