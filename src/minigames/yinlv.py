"""音律（编钟演奏）小游戏的纯检测逻辑与常量。

触发/armed、阻塞追亮点循环、点击与状态重置仍在 kezhan 任务侧：
- 入口：`_arm_yinlv_check` / `_handle_follow_extras`
- 循环：`_play_yinlv`
本模块只做编钟定位、光晕判定与条带推导，import 期零副作用。
"""
import time

import cv2
import numpy as np

from ..device import map as game_map
from ..core.app_utils import human_click, log
from ..device.battle import is_in_battle_frame

YINLV_WINDOW_ICON = "kezhan_yinlv.png"       # 小游戏窗口横幅（出现=窗口已开，消失=结束）
YINLV_START_ICON = "kezhan_kaishiyanzou.png"  # 「开始演奏」按钮
YINLV_BELLS = ("kezhan_bianzhong1_ring.png",   # 五个编钟模板（点开始前记住位置）
               "kezhan_bianzhong2_ring.png",
               "kezhan_bianzhong3_ring.png",
               "kezhan_bianzhong4_ring.png",
               "kezhan_bianzhong5_ring.png")
YINLV_THRESHOLD = 0.75              # 模板匹配阈值（待实测校准，同毒虫/掌柜初值）
YINLV_ARM_TIMEOUT = 120             # 入口选项点击后检测窗口的最长时限（秒）；超时自动
                                    # 关闭检测（任务未进入小游戏等异常场景兜底）
YINLV_CHECK_INTERVAL = 3.0          # extras 链窗口横幅检测限频（秒；热路径防全帧匹配空烧，
                                    # 性能规则 3——带区待真实截图标定后收紧）
YINLV_WINDOW_REGION = None          # 窗口横幅搜索条带 (l,t,r,b)；TODO 真实截图标定
YINLV_LOCATE_TIMEOUT = 15           # 点开始前定位按钮+全部编钟的超时（秒）
# 动态追亮点（2026-11-13 实测标定）：每轮轮询截一帧，在每钟模板中心
# ROI 内数金色光晕像素（HSV 金掩码），最多的钟且达阈值即当前高亮钟，
# 立即点击该钟。实测真机（指导音律）：亮钟 327-742 像素、带内噪声
# ≤19、相邻钟无串扰（ROI 以模板中心 ±45×±40 裁切，钟间距约 95px）。
YINLV_GLOW_POLL = 0.1               # 追亮点轮询周期（秒；截图+HSV+5 ROI，性能规则 1/10）
YINLV_GLOW_THRESHOLD = 80           # 高亮判定金色像素阈值（单钟实测 ≥327，留余量）
YINLV_GLOW_COOLDOWN = 0.5           # 同钟点击防抖（高亮残影/回放帧防连点；音符间隔实测 ≥1s）
YINLV_BELL_ROI_HALF = (45, 40)      # 高亮检测 ROI 半宽/半高（相对编钟模板中心）
YINLV_END_TIMEOUT = 120             # 小游戏总超时（秒）
YINLV_END_MISSES = 5                # 窗口横幅连续未命中轮询数即判结束（0.1s×5=0.5s 防抖）
YINLV_BAND_MARGIN = 60              # 窗口横幅搜索条带相对检测点的余量（px）
# _play_yinlv 返回状态（二态）：横幅消失 = 完成（不再做失败判定——
# 2026-11-13 用户规则，动态追亮点保证正确性）；仅定位超时（窗口仍在）
# 返回 retry 保持 armed 下轮重试
YINLV_DONE = "done"                 # 正常结束（横幅消失）
YINLV_RETRY = "retry"               # 开始按钮/编钟定位超时：保持 armed 下轮重试


def _yinlv_locate_bells(bgr):
    """同一帧批量定位五个编钟，返回 {模板名: (x, y) 中心}；未全部命中
    返回 None（下轮重试，不缓存——小游戏窗口内位置固定，但仅流程内
    轮询，有界低频，无需高性能缓存白名单）。"""
    res = game_map.match_frame_multi(bgr, YINLV_BELLS, threshold=YINLV_THRESHOLD)
    if any(pt is None for pt in res.values()):
        return None
    return res


def _yinlv_window_band(banner_pt):
    """以入口检测到的横幅中心推导窗口检查条带。

    小游戏窗口在演奏期间固定不动（横幅检测点 2026-11-09/11-12 两次
    实测均为 (677,262)，匹配度 1.00），条带只需覆盖检测点邻域，
    免每帧全帧匹配（性能规则 3）。模板缺失/无检测点时退回
    YINLV_WINDOW_REGION（当前为 None = 全帧）。条带未命中时
    match_frame_np 仍会全帧兜底（窗口被拖动等布局漂移保险）。
    """
    size = game_map.template_size(YINLV_WINDOW_ICON)
    if banner_pt is None or size is None:
        return YINLV_WINDOW_REGION
    w, h = size
    m = YINLV_BAND_MARGIN
    return (banner_pt[0] - w // 2 - m, banner_pt[1] - h // 2 - m,
            banner_pt[0] + w // 2 + m, banner_pt[1] + h // 2 + m)


def _yinlv_bell_rois(bells):
    """五个编钟的金色高亮检测 ROI（相对模板中心 ±YINLV_BELL_ROI_HALF）。

    与 YINLV_BELLS 顺序一致；ROI 只在钟身附近，避免相邻钟光晕串扰
    （实测钟间距约 95px，ROI 宽 90px）。
    """
    hw, hh = YINLV_BELL_ROI_HALF
    rois = []
    for icon in YINLV_BELLS:
        cx, cy = bells[icon]
        rois.append((cx - hw, cy - hh, cx + hw, cy + hh))
    return rois


def _yinlv_glow_index(bgr, rois):
    """当前高亮编钟下标（金色光晕像素最多且达阈值的钟）；无高亮返回 None。

    金色光晕判定用 HSV 金掩码（H 15-40 / S>120 / V>150），逐钟 ROI 计数。
    实测（2026-11-13 真机）：亮钟 327-742 像素、带内噪声 ≤19。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 0] > 15) & (hsv[:, :, 0] < 40)
            & (hsv[:, :, 1] > 120) & (hsv[:, :, 2] > 150))
    best_i, best_n = None, 0
    for i, (l, t, r, b) in enumerate(rois):
        n = int(mask[t:b, l:r].sum())
        if n > best_n:
            best_i, best_n = i, n
    return best_i if best_n >= YINLV_GLOW_THRESHOLD else None


def play(win, banner_pt=None, prefix="客栈"):
    """音律小游戏完整流程（阻塞执行到窗口横幅消失）。

    返回 (状态, 描述)，状态为 YINLV_DONE / YINLV_RETRY。

    动态追亮点实现（2026-11-13 用户规则 + 实机验证）：点「开始演奏」
    后游戏按随机顺序逐个点亮编钟（金色光晕，同一时刻只有一个钟亮，
    上方另飘白色音符字），点亮哪一个就敲哪一个——旧的固定 15 音序列
    只适用于辨八音（阿拉普）的固定曲谱，凤眠艺·指导音律同款窗口但
    曲目随机，固定序列点不中。流程：
    1. 同帧定位「开始演奏」按钮与五个编钟（编钟位置在点开始前记住；
       点开始后倒计时期间画面会变化，不能点后再定位）
    2. 点开始 → 按 YINLV_GLOW_POLL 轮询截图：先确认窗口横幅仍在
       （消失立即停点，2026-11-12 用户规则），再检测金色高亮编钟 →
       立即点击该钟（同钟 YINLV_GLOW_COOLDOWN 防抖）
    3. 窗口横幅连续 YINLV_END_MISSES 次未命中即结束 = 完成（2026-11-13
       用户规则：动态追亮点保证正确性，不再按敲击数判失败重新点击）
    banner_pt：extras 检测到的横幅中心，用于推导窗口检查条带。
    """
    band = _yinlv_window_band(banner_pt)
    # 阶段一：定位开始按钮与全部编钟（小游戏窗口内，帧间位置稳定）
    deadline = time.time() + YINLV_LOCATE_TIMEOUT
    start_pt = None
    bells = None
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
        if is_in_battle_frame(bgr):
            time.sleep(1.0)
            continue
        start_pt = game_map.match_frame_np(bgr, YINLV_START_ICON,
                                           threshold=YINLV_THRESHOLD)
        bells = _yinlv_locate_bells(bgr) if start_pt is not None else None
        if start_pt is not None and bells is not None:
            break
        time.sleep(0.3)
    if start_pt is None or bells is None:
        return YINLV_RETRY, "音律小游戏：定位开始按钮/编钟超时（模板未命中，待校准阈值）"
    log(f"[{prefix}] 音律小游戏：已定位 5 个编钟，点击开始演奏 "
        f"@({start_pt[0]},{start_pt[1]})，动态追亮点（{YINLV_GLOW_POLL}s 轮询）", "SYS")
    human_click(win, start_pt[0], start_pt[1])
    # 阶段二：动态追亮点。每轮先确认横幅仍在（消失立即停点——剩余点击
    # 会落进世界画面），再检测高亮钟并立即点击；同钟 0.5s 防抖。
    rois = _yinlv_bell_rois(bells)
    deadline = time.time() + YINLV_END_TIMEOUT
    clicked = 0
    misses = 0
    ended = False
    last_click = {}
    t_next = time.time()
    while time.time() < deadline:
        now = time.time()
        if now < t_next:
            time.sleep(t_next - now)
        t_next = time.time() + YINLV_GLOW_POLL
        img = win.capture(force_activate=False)
        if img is None:
            continue  # 截图失败不算消失（宁可少点一次也不误停）
        bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
        if game_map.match_frame_np(bgr, YINLV_WINDOW_ICON,
                                   threshold=YINLV_THRESHOLD,
                                   region=band) is None:
            misses += 1
            if misses >= YINLV_END_MISSES:
                ended = True
                break
            continue
        misses = 0
        idx = _yinlv_glow_index(bgr, rois)
        if idx is None:
            continue
        now = time.time()
        if now - last_click.get(idx, -1e9) < YINLV_GLOW_COOLDOWN:
            continue  # 高亮残影 / 回放帧：同钟防抖
        icon = YINLV_BELLS[idx]
        pt = bells[icon]
        human_click(win, pt[0], pt[1])
        last_click[idx] = now
        clicked += 1
    log(f"[{prefix}] 音律小游戏：已敲击编钟 {clicked} 次", "SYS")
    if not ended:
        return YINLV_DONE, f"音律小游戏超时结束（{YINLV_END_TIMEOUT}s）"
    return YINLV_DONE, "音律小游戏结束（窗口横幅消失）"
