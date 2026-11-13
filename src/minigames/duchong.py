"""拍毒虫（药圃除害）小游戏的检测、阻塞循环与内部状态。

任务侧只保留触发/armed 与入口门控：
- 入口：kezhan `_handle_follow_extras`（面板按钮模板触发）
- 循环：`_play_duchong` / `_swat_round`（本模块实现）
完成态与重进退避状态归本模块，任务侧通过 `can_enter()` 读取；
import 期零副作用。
"""
import difflib
import time

import cv2
import numpy as np
from ascript.windows.window.Window import WindowCaptureFormat

from ..device import map as game_map
from ..device import task_tracker
from ..core.app_utils import human_click, log

BUG_START_ICON = "bug_start.png"             # 「开始」按钮（首次进入面板）
BUG_RESTART_ICON = "kezhan_bug_restart.png"  # 「重新开始」按钮（每局结束后面板）
BUG_BUTTON_REGION = (590, 520, 800, 595)     # 按钮搜索条带（实测按钮 (624,541)-(737,566)）
BUG_BUTTON_THRESHOLD = 0.72                  # 按钮模板阈值（实测命中 0.87~1.00）
DUCHONG_ICONS = (                            # 三种毒虫飞行精灵模板（实测裁剪）
    "kezhan_duchong_young1.png", "kezhan_duchong_young2.png",
    "kezhan_duchong_young3.png", "kezhan_duchong_young4.png",
    "kezhan_duchong_young5.png", "kezhan_duchong_young6.png",
    "kezhan_duchong_young7.png",
    "kezhan_duchong_adult1.png", "kezhan_duchong_adult2.png",
    "kezhan_duchong_adult3.png", "kezhan_duchong_adult4.png",
    "kezhan_duchong_adult5.png", "kezhan_duchong_adult6.png",
    "kezhan_duchong_color1.png", "kezhan_duchong_color2.png",
)
DUCHONG_THRESHOLD = 0.75               # 毒虫阈值（实测：真虫每段悬停内必有一帧 ≥0.81，
                                       # 逐帧命中率 57~76%，一局多轮询足够补足；蜻蜓/七星虫
                                       # ≤0.47、平台花丛误配 ≤0.69 全部拒掉）
BUG_REGION = (380, 190, 1020, 630)     # 拍虫搜索区（实测全部落点 + 挥拍/黄圈特效范围）
BUG_POLL_INTERVAL = 0.14               # 拍虫轮询间隔（秒，用户指定 140ms）
BUG_DIFF_THRESHOLD = 40                # 帧间差分阈值（场景静态，只有虫/特效在动）
BUG_CANDIDATE_PAD = 30                 # 差分候选框外扩（变化区 → 完整精灵搜索窗）
BUG_MIN_AREA = 120                     # 差分候选最小面积（像素）
BUG_MAX_DIM = 160                      # 差分候选最大边长（超过视为场景动画/UI）
BUG_CLICK_COOLDOWN = 0.8               # 两次点击最小间隔（秒；一只虫点一次，防重复拍）
BUG_ROUND_TIMEOUT = 40                 # 单局兜底超时（正常倒计时 30s）
BUG_TOTAL_TIMEOUT = 200                # 整个小游戏（可多局）总超时（秒）
BUG_PANEL_WAIT = 8                     # 每局结束后等待面板重现的时限（秒）
BUG_FIRST_PANEL_WAIT = 5               # 进入时等待开始按钮的时限（秒）
BUG_PANEL_CONFIRM = 2                  # 按钮连续命中次数判面板重现/消失（防抖）
BUG_IDLE_END = 6.0                     # 连续无真虫命中且无面板 → 判定回合结束（秒）
BUG_SIG_RATIO = 0.8                    # 任务签名相似阈值（同 follow_task，过滤 OCR 抖动）
DUCHONG_FINISHED_TTL = 300             # 判定完成后抑制重进的窗口（秒）
DUCHONG_RETRY_GAP = 10                 # 拍虫失败后的重进退避（秒，防连点）


def _bug_sig_similar(a, b, ratio=BUG_SIG_RATIO):
    """任务签名相似判定（同 follow_task 的 0.8 阈值，过滤 OCR 单字抖动）。"""
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def _bug_button_pt(bgr):
    """小游戏面板按钮中心（「重新开始」/「开始」），不在面板返回 None。

    条带内两模板同帧取最高分（一次灰度转换），任一达阈值即命中；条带
    由真实按钮位置标定（BUG_BUTTON_REGION），不做全帧兜底。
    """
    res = game_map.best_match_multi_np(
        bgr, (BUG_RESTART_ICON, BUG_START_ICON), region=BUG_BUTTON_REGION)
    best = None
    for _, (score, pt) in res.items():
        if score >= BUG_BUTTON_THRESHOLD and (best is None or score > best[0]):
            best = (score, pt)
    return None if best is None else best[1]


def _bug_candidates(prev_bgr, cur_bgr):
    """帧间差分候选框（整帧坐标），返回 [(x, y, w, h), ...]。

    小游戏场景静态（实测整局除虫/挥拍/黄圈外像素一致），移动的只有虫子
    和特效——差分即可低成本圈出候选，毒虫模板匹配只在小窗口内做
    （性能规则 3 的有限条带思路，避免每轮全帧×15 模板）。
    """
    l, t, r, b = BUG_REGION
    d = cv2.absdiff(cur_bgr[t:b, l:r], prev_bgr[t:b, l:r]).max(axis=2)
    mask = (d > BUG_DIFF_THRESHOLD).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for k in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[k][:5])
        if area < BUG_MIN_AREA or w > BUG_MAX_DIM or h > BUG_MAX_DIM:
            continue
        out.append((l + x, t + y, w, h))
    return out


def _match_duchong(cur_bgr, boxes):
    """候选框内匹配全部毒虫模板，返回全局最高 (score, (x, y), icon)。

    双模板比分数不适用于本场景（毒虫与蜻蜓/七星虫形态差异大，实测负样本
    最高分 0.47），任何一张毒虫模板命中即视为毒虫。
    """
    best = (0.0, None, None)
    l, t, r, b = BUG_REGION
    pad = BUG_CANDIDATE_PAD
    for (x, y, w, h) in boxes:
        x0, y0 = max(l, x - pad), max(t, y - pad)
        x1, y1 = min(r, x + w + pad), min(b, y + h + pad)
        crop = cur_bgr[y0:y1, x0:x1]
        if crop.size == 0:
            continue
        for icon, (score, pt) in game_map.best_match_multi_np(
                crop, DUCHONG_ICONS).items():
            if score > best[0]:
                best = (score, (x0 + pt[0], y0 + pt[1]), icon)
    return best


# ---- 拍毒虫闭环状态（原 kezhan 模块级状态）----
_duchong_finished = None
_duchong_next_try = 0.0     # 下次允许进入的时间戳（与完成态同属本模块）


def can_enter():
    """入口门控：退避期内不允许再次进入（任务侧每轮询读取）。"""
    return time.time() >= _duchong_next_try


def backoff(seconds=DUCHONG_RETRY_GAP):
    """设置重进退避（失败/已完成残留面板时调用）。"""
    global _duchong_next_try
    _duchong_next_try = time.time() + seconds


def _bug_task_state(win, task_title):
    """当前客栈任务签名（追踪面板区域 OCR）；任务不在面板返回 ""，
    截图/OCR 失败返回 None（调用方不得据此判定变化）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None
    img_np = np.asarray(img)
    try:
        items, _ = task_tracker.ocr_track(img)
    except Exception as e:  # OCR 异常不应打断拍虫流程
        log(f"[客栈] 拍毒虫任务追踪 OCR 失败: {e}", "WARN")
        return None
    task = task_tracker.find_task(
        task_tracker.group_tasks(items, task_tracker.panel_row_xmax(img_np)),
        task_title)
    if task is None:
        return ""
    return task_tracker.task_signature(task)

def _bug_task_advanced(state, sig0):
    """任务追踪消失或签名相对 sig0 变化 → 小游戏完成/任务已推进
    （2026-11-12 用户规则：连续重开直到任务追踪变化）。state 为
    _bug_task_state 的返回值：None（读不到）不判、"" 为任务已消失。"""
    if state is None:
        return False
    if not state:
        return True
    return bool(sig0) and not _bug_sig_similar(state, sig0)

def _bug_capture(win):
    """拍虫轮询截帧：BGR 数组（OpenCV 格式），force_activate=False 不抢焦点。"""
    return win.capture(force_activate=False,
                       format=WindowCaptureFormat.OPENCV)

def _swat_round(win, deadline, ignore_panel_until=0.0):
    """单局拍虫（阻塞到面板重现/长时间无虫/超时）。

    每次轮询固定顺序：截帧 → 面板按钮检查（连续 BUG_PANEL_CONFIRM 次
    命中判回合结束，ignore_panel_until 内跳过——点开始后的面板淡出帧
    会误命中）→ 帧间差分候选 → 毒虫模板匹配 → 命中点击（全局点击冷却
    BUG_CLICK_COOLDOWN，一只虫点一次）。返回 (状态, 本局点击数)：
    "panel"（面板重现）/ "idle"（长时间无真虫命中）/ "timeout"。
    """
    clicks = 0
    last_click = 0.0
    last_hit = time.time()
    panel_hits = 0
    prev = None
    while time.time() < deadline:
        t0 = time.time()
        img = _bug_capture(win)
        if img is None:
            time.sleep(BUG_POLL_INTERVAL)
            continue
        if time.time() > ignore_panel_until and _bug_button_pt(img) is not None:
            panel_hits += 1
            if panel_hits >= BUG_PANEL_CONFIRM:
                return "panel", clicks
            time.sleep(0.15)  # 隔帧复查（面板淡入淡出单帧不判）
            prev = None       # 面板帧不参与差分（画面整体变化会污染候选）
            continue
        panel_hits = 0
        if prev is not None and prev.shape == img.shape:
            boxes = _bug_candidates(prev, img)
            if boxes:
                score, pt, icon = _match_duchong(img, boxes)
                if pt is not None and score >= DUCHONG_THRESHOLD \
                        and time.time() - last_click >= BUG_CLICK_COOLDOWN:
                    human_click(win, pt[0], pt[1])
                    clicks += 1
                    last_click = time.time()
                    last_hit = last_click
                    log(f"[客栈] 拍中第 {clicks} 只毒虫 @({pt[0]},{pt[1]})"
                        f"（{icon} {score:.2f}）", "SYS")
        if time.time() - last_hit > BUG_IDLE_END:
            return "idle", clicks
        prev = img
        dt = BUG_POLL_INTERVAL - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)
    return "timeout", clicks

def play(win, task_title, prefix="客栈"):
    """拍毒虫小游戏完整流程（多局，阻塞执行）。

    2026-11-12 用户确认流程：点「开始」→ 30s 倒计时内只拍三种毒虫
    （蜻蜓/七星虫不点）→ 面板重现（按钮变「重新开始」）→ 任务追踪
    签名未变就继续点「重新开始」重玩，签名变化/任务消失即完成。
    结束条件：任务签名变化（含消失）/ 总超时 / 连续快速回面板
    （点击未进入小游戏，防连点）。返回 (是否成功, 描述)。
    """
    global _duchong_finished
    now = time.time()
    # 完成态只用于残留面板抑制；TTL 过期即无意义，主动清空防长期驻留
    if _duchong_finished is not None \
            and now - _duchong_finished[1] > DUCHONG_FINISHED_TTL:
        _duchong_finished = None
    if _duchong_finished is not None:
        sig = _bug_task_state(win, task_title)
        done_sig = _duchong_finished[0]
        if sig is not None and (
                (not sig and not done_sig)
                or (sig and done_sig and _bug_sig_similar(sig, done_sig))):
            # 已完成后的残留面板：不重开，退避期内不再进入（防日志刷屏）
            backoff()
            return True, "拍毒虫已完成（本轮不再重进）"
    deadline_all = time.time() + BUG_TOTAL_TIMEOUT
    sig0 = None          # 首次开拍前的任务签名基线
    rounds = 0
    clicks_total = 0
    quick_panels = 0     # 点击后 3s 内即回面板的连击计数
    panel_wait = BUG_FIRST_PANEL_WAIT
    while time.time() < deadline_all:
        # ---- 面板阶段：等开始/重新开始按钮出现 ----
        pt = None
        wait_until = time.time() + panel_wait
        while time.time() < wait_until:
            img = _bug_capture(win)
            if img is not None:
                pt = _bug_button_pt(img)
                if pt is not None:
                    break
            time.sleep(0.2)
        if pt is None:
            state = _bug_task_state(win, task_title)
            if rounds == 0:
                return False, "拍毒虫：未检测到开始按钮（未进入小游戏）"
            if _bug_task_advanced(state, sig0):
                _duchong_finished = (state, time.time())
                return True, (f"拍毒虫完成（任务已推进，共 {rounds} 局，"
                              f"拍中 {clicks_total} 只）")
            return True, (f"拍毒虫结束（未见面板，共 {rounds} 局，"
                          f"拍中 {clicks_total} 只）")
        if rounds > 0:
            # 不再盲目重开：先确认任务追踪是否已变化
            state = _bug_task_state(win, task_title)
            if _bug_task_advanced(state, sig0):
                _duchong_finished = (state, time.time())
                return True, (f"拍毒虫完成（任务已推进，共 {rounds} 局，"
                              f"拍中 {clicks_total} 只）")
        else:
            sig0 = _bug_task_state(win, task_title)
        # ---- 点击开始/重新开始 ----
        human_click(win, pt[0], pt[1])
        rounds += 1
        round_start = time.time()
        log(f"[{prefix}] 拍毒虫第 {rounds} 局开始（按钮 @({pt[0]},{pt[1]})）", "SYS")
        status, clicks = _swat_round(
            win, round_start + BUG_ROUND_TIMEOUT,
            ignore_panel_until=round_start + 1.0)
        clicks_total += clicks
        panel_wait = BUG_PANEL_WAIT
        if status == "panel" and clicks == 0 \
                and time.time() - round_start < 3.0:
            quick_panels += 1
            if quick_panels >= 3:
                return False, ("拍毒虫：点击开始后面板未消失"
                               "（点击未进入小游戏）")
            continue
        quick_panels = 0
        if status == "idle":
            # 长时间无真虫命中：可能面板已不出现（任务完成/自动关闭）
            state = _bug_task_state(win, task_title)
            if _bug_task_advanced(state, sig0):
                _duchong_finished = (state, time.time())
                return True, (f"拍毒虫完成（任务已推进，共 {rounds} 局，"
                              f"拍中 {clicks_total} 只）")
    return False, (f"拍毒虫总超时结束（{rounds} 局，"
                   f"拍中 {clicks_total} 只）")
