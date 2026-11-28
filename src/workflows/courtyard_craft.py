# -*- coding: utf-8 -*-
"""庭院聚灵台打造物品流程（300环「急人所急」等需要灵宝物品时）。

用户 2026-11-27 实机流程：
1. 走到长安 (165,125)，点「管家」NPC 名 → 对话框；
2. 选「送我回自己的庭院」→ 回到高级庭院；
3. 打开小地图，走到 (112,47) 聚灵台旁；
4. 点聚灵台 → 选「我要打造物品」，同时识别对话框信息
   （聚灵台的灵气储存量：20000/20000、每小时回灵：150/600）；
5. 打造面板中金色虚线光圈标记要打造的物品（可能有多个，任选其一）→
   点击光圈物品（「所需灵气」0 → 具体值，如实机 3000）→ 点「打造」；
6. 二次确认「你确定要用3000点灵气打造属性13的忆梦符吗？」→ 点「确定」；
7. 下方「打造物品」进度标志与进度条出现，消失 = 打造完成。

2026-11-27 实机标定（可乐可口 36096466）：
- 管家模板 40x18：长安 (170,123) 帧命中 (618,300) 0.994，次优 0.426；
  点击后对话框四选项，目标「送我回自己的庭院」绿字 (497,443)；
- 聚灵台模板 78x30：庭院 (111,47) 帧命中 (745,253) 0.9999；
- 聚灵台对话框是自绘面板（无标准对话框边框），信息行与绿色选项同区：
  「聚灵台的灵气储存量：17615/20000」「每小时回灵：150/600」；
- 已打造过本任务所需物品时，点「我要打造物品」不打开面板，而是显示
  「你已经打造过需要的任务品了！」（2026-11-27 实机）——视为流程成功
  （物品已在背包/任务储物格），不再重复消耗灵气；
- 打造按钮 57x22、二次确认「确定」56x24 为用户提供的面板/确认框模板；
- 高级庭院小地图坐标导航首采 3 个悬停样本（(647,393)→(79,81) 等），
  点击 (112,47) 偏差 0，1.2s 到达 (111,47)；
- 打造面板（2026-11-27 实机，打造按钮 (803,565)）：6×5 物品格，需打造
  物品带金色虚线光圈（实机包围盒 (736,376)-(778,433)）；点击光圈后
  「所需灵气」0→3000；点「打造」弹确认「你确定要用3000点灵气打造属性
  13的忆梦符吗？」；确认后底部 (690,700) 出现「打造物品」进度标志与
  进度条，约 20~40s 后消失（打造完成）。

打造进度标志（用户 2026-11-27 提供 54x14「打造物品」黄字）：出现 =
正在打造，连续两帧消失 = 打造完成（见 _wait_progress_done）。
"""
import re
import time

import cv2
import numpy as np

from ..core import app_utils
from ..core.app_utils import human_click, log, norm_text
from ..device import map as game_map
from ..device import map_nav
from ..device import silent_click
from ..device import task_tracker
from . import claim_flow

TAG = "[庭院打造] "

# ---- 长安超级管家 ----
BUTLER_MAP = "长安城"
BUTLER_POS = (165, 125)          # 用户 2026-11-27 提供
BUTLER_ICON = "courtyard_craft_butler.png"   # 40x18「管家」名
# 搜索条带：实机 (170,123) 帧模板中心 (618,300)，向四周留足余量；
# 带区未命中才全帧兜底（限频，见 _match_icon）。
BUTLER_BAND = (450, 180, 850, 450)
BUTLER_THRESHOLD = 0.9           # 实机 0.994 / 次优 0.426
BUTLER_FIND_TIMEOUT = 12.0
BUTLER_RETRY_INTERVAL = 2.0
# 管家对话框选项（用户 2026-11-27 提供，精确子串）
BUTLER_OPTION = "送我回自己的庭院"
BUTLER_OPTION_NEEDLES = (norm_text(BUTLER_OPTION),)

# ---- 庭院聚灵台 ----
COURTYARD_MAP_KEYWORD = "庭院"    # 初级/中级/高级庭院通用
COURTYARD_POS = (112, 47)        # 用户 2026-11-27 提供（高级庭院）
COURTYARD_ARRIVE_TOLERANCE = 5   # 聚灵台旁到达容差（像素）
COURTYARD_ENTER_TIMEOUT = 30.0   # 点「送我回自己的庭院」后等场景切换
JULING_ICON = "courtyard_craft_juling.png"   # 78x30 橙色火焰
# 实机 (111,47) 帧模板中心 (745,253)；带区向四周留 ≥130px
JULING_BAND = (500, 120, 1000, 430)
JULING_THRESHOLD = 0.9           # 实机 0.9999
JULING_FIND_TIMEOUT = 10.0
JULING_OPTION = "我要打造物品"
JULING_OPTION_NEEDLES = (norm_text(JULING_OPTION),)
# 点「我要打造物品」但本任务已打造过时的系统浮字（2026-11-27 实机）
ALREADY_CRAFTED_NEEDLE = "已经打造过"
ALREADY_CRAFTED_REGION = (400, 380, 1000, 520)

# ---- 打造面板 / 二次确认 ----
CRAFT_BTN_ICON = "courtyard_craft_btn.png"       # 57x22「打造」
CRAFT_BTN_THRESHOLD = 0.9
CRAFT_BTN_BAND = (250, 120, 1150, 720)           # 面板位置未知时的宽条带
CRAFT_CONFIRM_ICON = "courtyard_craft_confirm.png"  # 56x24「确定」
CRAFT_CONFIRM_THRESHOLD = 0.9
# 实机确认框「确定」中心 (611,487)（用户 2026-11-27 截图模板 1.0000 命中）
CRAFT_CONFIRM_BAND = (450, 380, 800, 560)
# 确认框文案门控：模板命中后再 OCR 对话区，必须同时见到「打造」与「灵气」
# 才点确定（「确定」按钮是通用控件，防误点其它确认框）
CONFIRM_TEXT_NEEDLES = ("打造", "灵气")
CONFIRM_OCR_REGION = task_tracker.DIALOG_REGION

# ---- 打造面板物品格与光圈（2026-11-27 实机标定，按钮中心 (803,565)）----
# 面板 6×5 物品格：相对「打造」按钮中心的网格区域（左/上/右/下）。
# 实机网格约 (525,262)-(858,505)，按钮 (803,565)。
CRAFT_GRID_REL = (-280, -310, 60, -40)
# 「所需灵气」数值行（按钮左侧底部信息区；只取需求行，避开下一行
# 「现有灵气」，防未选中时把现有值误当需求值）。实机「所需灵气 3000」
# 行 y≈555-575。
CRAFT_COST_REGION_REL = (-260, -17, -20, 10)
# 光圈 = 金色虚线选中框（RGB 高红高绿低蓝）；按金色像素聚类定位，
# 不依赖具体物品图标（换任务物品也能用）。实机光圈像素 ~350-400、
# 包围盒 (736,376)-(778,433)（相对按钮中心 (-67,-189)-(-25,-132)）。
HALO_MIN_R = 170
HALO_MIN_G = 110
HALO_MAX_B = 110
HALO_MIN_RB = 90
HALO_MIN_AREA = 120              # 金色像素聚类最小面积
HALO_MERGE_DIST = 55             # 虚线断点合并距离（px）
CRAFT_SELECT_RETRIES = 2         # 光圈点击重试次数
CRAFT_SELECT_WAIT = 0.6          # 点击光圈后等选中渲染

# 打造进度标志（用户 2026-11-27 提供 54x14「打造物品」黄字）：点击确定
# 后出现 = 正在打造，连续两帧消失 = 打造完成。实机标志中心 (690,700)、
# 下方为进度条，20~40s 消失；搜索带覆盖标志与进度条（固定底部 UI）。
CRAFT_PROGRESS_ICON = "courtyard_craft_progress.png"
CRAFT_PROGRESS_BAND = (580, 640, 800, 750)
CRAFT_PROGRESS_THRESHOLD = 0.85
CRAFT_PROGRESS_APPEAR_TIMEOUT = 10.0
CRAFT_PROGRESS_GONE_TIMEOUT = 120.0
CRAFT_PROGRESS_POLL = 0.5

# ---- 等待/轮询节奏 ----
DIALOG_WAIT_TIMEOUT = 10.0       # NPC/聚灵台对话框选项出现
DIALOG_POLL_INTERVAL = 0.5
PANEL_WAIT_TIMEOUT = 12.0        # 「我要打造物品」→ 面板出现
PANEL_POLL_INTERVAL = 0.6
CONFIRM_WAIT_TIMEOUT = 10.0
CONFIRM_POLL_INTERVAL = 0.4
STAND_POLL_INTERVAL = 0.5
STAND_TIMEOUT = 4.0
FULL_CHECK_INTERVAL = 3.0        # 带区未命中→全帧兜底的限频（性能规则）
_LAST_FULL_CHECK = {}


def _match_icon(bgr, icon, band, threshold, key):
    """带区匹配 + 限频全帧兜底，返回 (score, (x,y) 或 None)。

    与 map.best_match_np 同算法（TM_CCOEFF_NORMED 灰度、返回匹配中心）。
    新增检测点必须配搜索条带：带区未命中才全帧兜底，且兜底按
    FULL_CHECK_INTERVAL 限频（性能规则 3）。
    """
    score, pt = game_map.best_match_np(bgr, icon, region=band)
    if score >= threshold:
        return score, pt
    now = time.time()
    if now - _LAST_FULL_CHECK.get(key, 0.0) >= FULL_CHECK_INTERVAL:
        _LAST_FULL_CHECK[key] = now
        fscore, fpt = game_map.best_match_np(bgr, icon)
        if fscore >= threshold:
            return fscore, fpt
        return max(score, fscore), None
    return score, None


def _log_spirit_info(items):
    """记录聚灵台对话框的灵气信息（用户 2026-11-27 要求识别）。

    实机文案：「聚灵台的灵气储存量：17615/20000」「每小时回灵：150/600」。
    识别失败只提示、不阻断流程（信息行是浮层文字，偶发 OCR 漏读）。
    """
    text = "".join(t for t, _r, s in (items or []) if s >= 0.5)
    storage = re.search(r"灵气储存量[：:]\s*(\d+)\s*/\s*(\d+)", text)
    regen = re.search(r"每小时回灵[：:]\s*(\d+)\s*/\s*(\d+)", text)
    if storage:
        log(f"{TAG}灵气储存量 {storage.group(1)}/{storage.group(2)}", "SYS")
    if regen:
        log(f"{TAG}每小时回灵 {regen.group(1)}/{regen.group(2)}", "SYS")
    if not storage and not regen:
        log(f"{TAG}聚灵台对话框未识别到灵气信息（忽略）", "WARN")


def _wait_green_option(win, needles, timeout=DIALOG_WAIT_TIMEOUT):
    """轮询对话框绿字选项，命中返回 (items, img_np)；超时 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(DIALOG_POLL_INTERVAL)
            continue
        items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if claim_flow.find_claim_option(items, img_np, needles) is not None:
            return items, img_np
        time.sleep(DIALOG_POLL_INTERVAL)
    return None


def _wait_map_keyword(win, keyword, timeout):
    """轮询 HUD 地名包含 keyword（如「庭院」），返回 (pos, img) 或 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(DIALOG_POLL_INTERVAL)
            continue
        pos = game_map.read_map_position(win, img=img)  # 同帧复用
        if pos is not None and keyword in pos[0]:
            return pos, img
        time.sleep(DIALOG_POLL_INTERVAL)
    return None


def _wait_stand(win, timeout=STAND_TIMEOUT):
    """世界点击前的站定判定：地图坐标连续两次读数相同。

    用户规则 13：点屏推进/世界点击必须先确认人物站定（行走中点击会
    取消寻路、人物停下或走去错误点）。坐标不可读按未站定处理，超时
    返回 False。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        pos = game_map.read_map_position(win)
        if pos is not None and pos == last:
            return True
        last = pos
        time.sleep(STAND_POLL_INTERVAL)
    return False


def _wait_panel_or_done(win):
    """点「我要打造物品」后等打造面板；已打造过则命中浮字。

    返回 ("panel", (x,y)) / ("already", None) / (None, None)。
    """
    deadline = time.time() + PANEL_WAIT_TIMEOUT
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is not None:
            bgr = game_map.bgr_frame(img)
            score, pt = _match_icon(bgr, CRAFT_BTN_ICON, CRAFT_BTN_BAND,
                                    CRAFT_BTN_THRESHOLD, "craft_btn")
            if pt is not None:
                log(f"{TAG}打造面板已打开（打造按钮 {score:.2f}）", "SYS")
                return "panel", pt
            items, _ = game_map.ocr_region(img, ALREADY_CRAFTED_REGION)
            txt = "".join(t for t, _r, s in items if s >= 0.5)
            if ALREADY_CRAFTED_NEEDLE in txt:
                return "already", None
        time.sleep(PANEL_POLL_INTERVAL)
    return None, None


def _cost_region(btn_pt):
    """「所需灵气」需求行区域（相对打造按钮中心）。"""
    l, t, r, b = CRAFT_COST_REGION_REL
    return (btn_pt[0] + l, btn_pt[1] + t,
            btn_pt[0] + r, btn_pt[1] + b)


def _read_craft_cost(img, btn_pt):
    """OCR 打造面板「所需灵气」数值；未选中返回 0/None。

    只 OCR 需求行（不含下一行「现有灵气」），防未选中时把现有值误当
    需求值。实机选中前 0、点击光圈后 3000。
    """
    if img is None:
        return None
    items, _ = game_map.ocr_region(img, _cost_region(btn_pt))
    text = "".join(t for t, _r, s in items if s >= 0.4)
    m = re.search(r"所需灵气[：:\s]*([0-9]+)", text)
    return int(m.group(1)) if m else None


def _find_craft_halo(frame_bgr, btn_pt):
    """面板物品格内金色光圈（虚线选中框）中心，返回 ((x,y), 面积) 或 None。

    用户 2026-11-27：光圈标记要打造的物品，可能有多个，任选其一。
    光圈是金色虚线框（RGB 高红高绿低蓝），按金色像素连通域 + 近邻合并
    定位——不依赖具体物品图标，换任务物品也能用。实机面板（按钮
    (803,565)）光圈包围盒 (736,376)-(778,433)，面积 ~350-400。
    """
    l = btn_pt[0] + CRAFT_GRID_REL[0]
    t = btn_pt[1] + CRAFT_GRID_REL[1]
    r = btn_pt[0] + CRAFT_GRID_REL[2]
    b = btn_pt[1] + CRAFT_GRID_REL[3]
    h, w = frame_bgr.shape[:2]
    l, t = max(0, int(l)), max(0, int(t))
    r, b = min(w, int(r)), min(h, int(b))
    if r - l < 20 or b - t < 20:
        return None
    roi = frame_bgr[t:b, l:r]
    blue = roi[:, :, 0].astype(int)
    green = roi[:, :, 1].astype(int)
    red = roi[:, :, 2].astype(int)
    mask = ((red > HALO_MIN_R) & (green > HALO_MIN_G)
            & (blue < HALO_MAX_B) & (red - blue > HALO_MIN_RB))
    mask = mask.astype(np.uint8)
    n, _labels, stats, _cents = cv2.connectedComponentsWithStats(mask, 8)
    boxes = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 6:
            continue
        boxes.append([int(x), int(y), int(x + bw), int(y + bh), int(area)])
    # 合并相近碎块（虚线框断点）
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, c = boxes[i], boxes[j]
                if (a[0] - HALO_MERGE_DIST <= c[2]
                        and c[0] - HALO_MERGE_DIST <= a[2]
                        and a[1] - HALO_MERGE_DIST <= c[3]
                        and c[1] - HALO_MERGE_DIST <= a[3]):
                    boxes[i] = [min(a[0], c[0]), min(a[1], c[1]),
                                max(a[2], c[2]), max(a[3], c[3]),
                                a[4] + c[4]]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    if not boxes:
        return None
    best = max(boxes, key=lambda bx: bx[4])
    if best[4] < HALO_MIN_AREA:
        return None
    cx = l + (best[0] + best[2]) // 2
    cy = t + (best[1] + best[3]) // 2
    return (cx, cy), best[4]


def _select_craft_item(win, frame_bgr, btn_pt):
    """在打造面板点击光圈物品并确认已选中（所需灵气 > 0）。

    已选中（所需灵气 > 0）直接返回；否则按金色光圈定位点击，OCR 复核
    「所需灵气」从 0 变为具体值后成功。用户 2026-11-27：光圈是「要打造
    的物品」，可能有多个，任选其一。
    """
    img = win.capture(force_activate=False)
    cost = _read_craft_cost(img, btn_pt)
    if cost:
        log(f"{TAG}打造面板已选中物品（所需灵气 {cost}）", "SYS")
        return True
    for _attempt in range(1, CRAFT_SELECT_RETRIES + 1):
        halo = _find_craft_halo(frame_bgr, btn_pt)
        if halo is None:
            log(f"{TAG}打造面板未找到光圈物品（金色像素不足）", "WARN")
            return False
        pt, area = halo
        human_click(win, pt[0], pt[1])
        log(f"{TAG}已点击光圈物品 @{pt}（金色像素 {area}）", "SYS")
        time.sleep(CRAFT_SELECT_WAIT)
        img = win.capture(force_activate=False)
        cost = _read_craft_cost(img, btn_pt)
        if cost:
            log(f"{TAG}物品已选中（所需灵气 {cost}）", "SYS")
            return True
        if img is not None:
            frame_bgr = game_map.bgr_frame(img)
    log(f"{TAG}光圈物品选中失败（所需灵气仍为 0/未识别）", "WARN")
    return False


def _wait_confirm_dialog(win):
    """等二次确认框：确定模板命中 + 对话区 OCR 含「打造」「灵气」。

    返回 (x, y, 对话框文案) 或 None。文案门控防「确定」通用按钮误点
    （模板 56x24 与其它确认框相似）。
    """
    deadline = time.time() + CONFIRM_WAIT_TIMEOUT
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(CONFIRM_POLL_INTERVAL)
            continue
        bgr = game_map.bgr_frame(img)
        score, pt = _match_icon(bgr, CRAFT_CONFIRM_ICON, CRAFT_CONFIRM_BAND,
                                CRAFT_CONFIRM_THRESHOLD, "craft_confirm")
        if pt is not None:
            items, _ = game_map.ocr_region(img, CONFIRM_OCR_REGION)
            text = "".join(t for t, _r, s in items if s >= 0.55)
            if all(n in text for n in CONFIRM_TEXT_NEEDLES):
                return pt[0], pt[1], text
            # 模板命中但文案不符：可能是其它确认框，不点，继续等
        time.sleep(CONFIRM_POLL_INTERVAL)
    return None


def _wait_progress_done(win):
    """等待打造进度标志消失（打造完成）。

    用户 2026-11-27 提供的进度标志为「打造物品」黄字（54x14）：出现 =
    正在打造，连续两帧消失 = 完成（防渲染瞬态误判）。点击确定后先等
    标志出现（最长 CRAFT_PROGRESS_APPEAR_TIMEOUT），再等到消失
    （最长 CRAFT_PROGRESS_GONE_TIMEOUT）。
    """
    deadline = time.time() + CRAFT_PROGRESS_APPEAR_TIMEOUT
    seen = False
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is not None:
            score, _pt = game_map.best_match_np(
                game_map.bgr_frame(img), CRAFT_PROGRESS_ICON,
                region=CRAFT_PROGRESS_BAND)
            if score >= CRAFT_PROGRESS_THRESHOLD:
                seen = True
                log(f"{TAG}打造进行中（进度标志匹配 {score:.2f}）", "SYS")
                break
        time.sleep(CRAFT_PROGRESS_POLL)
    if not seen:
        return False, "确认后未出现打造进度标志"
    gone = 0
    deadline = time.time() + CRAFT_PROGRESS_GONE_TIMEOUT
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            continue
        score, _pt = game_map.best_match_np(
            game_map.bgr_frame(img), CRAFT_PROGRESS_ICON,
            region=CRAFT_PROGRESS_BAND)
        if score < CRAFT_PROGRESS_THRESHOLD:
            gone += 1
            if gone >= 2:
                return True, "打造进度标志已消失（打造完成）"
        else:
            gone = 0
        time.sleep(CRAFT_PROGRESS_POLL)
    return False, "打造进度标志未消失"


def _panel_visible(win):
    """打造按钮模板是否仍在场（面板未关）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False
    score, _pt = game_map.best_match_np(
        game_map.bgr_frame(img), CRAFT_BTN_ICON, region=CRAFT_BTN_BAND)
    return score >= CRAFT_BTN_THRESHOLD


def _send_esc(win):
    """按一次 Esc（静默模式走窗口消息，2026-11-27 实测可关面板/对话框）。"""
    if app_utils.silent_mode_on():
        silent_click.send_silent_hotkey(win, (), "esc")
    else:
        from ascript.windows.hardware.keyboard import Keyboard
        Keyboard.key("esc")  # 物理模式：需游戏窗口在前台


def _close_panel_if_open(win):
    """打造面板仍在场时按 Esc 关闭，并等面板消失（防挡住后续点击）。"""
    if not _panel_visible(win):
        return False
    _send_esc(win)
    for _ in range(5):
        time.sleep(0.2)
        if not _panel_visible(win):
            break
    log(f"{TAG}已按 Esc 关闭打造面板", "INFO")
    return True


def _dialog_option_visible(win):
    """管家/聚灵台对话框是否仍在场（任一目标绿字选项可见）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
    return any(claim_flow.find_claim_option(items, img_np, needles)
               is not None
               for needles in (BUTLER_OPTION_NEEDLES, JULING_OPTION_NEEDLES))


def _close_residual_ui(win):
    """失败收尾：面板或管家/聚灵台对话框残留时按 Esc 关闭。

    残留对话框会挡住 follow 的任务链接点击，让整轮跟踪空转。
    """
    if _panel_visible(win) or _dialog_option_visible(win):
        _send_esc(win)
        log(f"{TAG}已按 Esc 关闭残留面板/对话框", "INFO")
        return True
    return False


def _open_butler_dialog(win):
    """在长安 (165,125) 找到「管家」名并点击，等对话框选项出现。"""
    ok, info = map_nav.navigate_to_map_coordinate(
        win, BUTLER_MAP, BUTLER_POS[0], BUTLER_POS[1],
        log_fn=lambda m: log(f"{TAG}[地图] {m}", "INFO"))
    if not ok:
        return False, f"导航到{BUTLER_MAP}管家失败：{info}"
    deadline = time.time() + BUTLER_FIND_TIMEOUT
    last_score = 0.0
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        # 世界点击前站定（规则 13：行走中点击会取消寻路/带走人物）
        if not _wait_stand(win):
            time.sleep(0.5)
            continue
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(BUTLER_RETRY_INTERVAL)
            continue
        bgr = game_map.bgr_frame(img)
        score, pt = _match_icon(bgr, BUTLER_ICON, BUTLER_BAND,
                                BUTLER_THRESHOLD, "butler")
        last_score = score
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"{TAG}已点击长安管家 @{pt}（匹配 {score:.2f}）", "SYS")
            frame = _wait_green_option(win, BUTLER_OPTION_NEEDLES)
            if frame is not None:
                return True, frame
            log(f"{TAG}管家对话框未出现目标选项，重试点击", "WARN")
        time.sleep(BUTLER_RETRY_INTERVAL)
    return False, f"未找到长安管家（最佳匹配 {last_score:.2f}）"


def _enter_courtyard(win):
    """点「送我回自己的庭院」并等待进入庭院场景。返回 (ok, info, pos)。"""
    ok, frame = _open_butler_dialog(win)
    if not ok:
        return False, frame, None
    items, img_np = frame
    if not claim_flow.click_claim_option(
            win, items, img_np, needles=BUTLER_OPTION_NEEDLES, prefix=TAG):
        return False, "点击管家选项失败", None
    log(f"{TAG}已点「{BUTLER_OPTION}」，等待进入庭院", "SYS")
    res = _wait_map_keyword(win, COURTYARD_MAP_KEYWORD,
                            COURTYARD_ENTER_TIMEOUT)
    if res is None:
        return False, f"{COURTYARD_ENTER_TIMEOUT:.0f}s 内未进入庭院", None
    pos, _img = res
    log(f"{TAG}已回到庭院 {pos[0]} [{pos[1]},{pos[2]}]", "SYS")
    return True, f"已进入 {pos[0]}", pos


def _move_to_juling(win, pos):
    """在庭院内走到聚灵台旁 (112,47)；已在附近则跳过。"""
    if abs(pos[1] - COURTYARD_POS[0]) <= COURTYARD_ARRIVE_TOLERANCE \
            and abs(pos[2] - COURTYARD_POS[1]) <= COURTYARD_ARRIVE_TOLERANCE:
        return True, "已在聚灵台旁"
    ok, info = map_nav.navigate_within_map(
        win, pos[0], COURTYARD_POS[0], COURTYARD_POS[1],
        log_fn=lambda m: log(f"{TAG}[地图] {m}", "INFO"))
    return ok, info


def _click_juling(win):
    """点击庭院聚灵台，等「我要打造物品」对话框。返回 (ok, frame)。"""
    deadline = time.time() + JULING_FIND_TIMEOUT
    last_score = 0.0
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        if not _wait_stand(win):
            time.sleep(0.5)
            continue
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        bgr = game_map.bgr_frame(img)
        score, pt = _match_icon(bgr, JULING_ICON, JULING_BAND,
                                JULING_THRESHOLD, "juling")
        last_score = score
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"{TAG}已点击聚灵台 @{pt}（匹配 {score:.2f}）", "SYS")
            frame = _wait_green_option(win, JULING_OPTION_NEEDLES)
            if frame is not None:
                return True, frame
            log(f"{TAG}聚灵台对话框未出现目标选项，重试点击", "WARN")
        time.sleep(0.6)
    return False, f"未找到聚灵台（最佳匹配 {last_score:.2f}）"


def craft_item(win, item_name="", prefix=TAG):
    """完整庭院打造流程：长安管家 → 庭院聚灵台 → 打造 → 确认 → 等完成。

    item_name 仅用于日志/确认框文案核对（面板光圈不按名称选，用户
    2026-11-27：可能有多个，任选其一）。
    返回 (ok, info)。失败时尽量用 Esc 关闭残留对话框/面板，避免挡住
    后续跟踪点击。
    """
    tag = prefix or TAG
    log(f"{tag}开始庭院打造{item_name and f'「{item_name}」' or ''}", "SYS")
    success = False  # 仅流程成功时置位（阶段 ok 会被后续步骤覆盖）
    try:
        pos = game_map.read_map_position(win)
        if pos is not None and COURTYARD_MAP_KEYWORD in pos[0]:
            log(f"{tag}已在{pos[0]}，直接前往聚灵台", "SYS")
        else:
            ok, info, pos = _enter_courtyard(win)
            if not ok:
                return False, info
        ok, info = _move_to_juling(win, pos)
        if not ok:
            return False, f"走到聚灵台失败：{info}"
        ok, frame = _click_juling(win)
        if not ok:
            return False, frame
        items, img_np = frame
        _log_spirit_info(items)
        if not claim_flow.click_claim_option(
                win, items, img_np, needles=JULING_OPTION_NEEDLES, prefix=tag):
            return False, f"点击「{JULING_OPTION}」失败"
        kind, pt = _wait_panel_or_done(win)
        if kind == "already":
            log(f"{tag}游戏提示已打造过所需任务品，跳过打造", "SYS")
            _close_residual_ui(win)  # 聚灵台对话框若仍在场则关闭
            success = True
            return True, "所需任务品已打造过（无需重复打造）"
        if kind != "panel" or pt is None:
            return False, f"{PANEL_WAIT_TIMEOUT:.0f}s 内未出现打造面板"
        img = win.capture(force_activate=False)
        if img is None or not _select_craft_item(
                win, game_map.bgr_frame(img), pt):
            return False, "打造面板未找到/未选中光圈物品"
        human_click(win, pt[0], pt[1])
        log(f"{tag}已点击「打造」@{pt}", "SYS")
        confirm = _wait_confirm_dialog(win)
        if confirm is None:
            return False, "未出现「确定要用灵气打造」二次确认框"
        cx, cy, ctext = confirm
        log(f"{tag}二次确认：{ctext[:40]}", "SYS")
        human_click(win, cx, cy)
        ok_prog, pinfo = _wait_progress_done(win)
        if not ok_prog:
            return False, pinfo
        _close_panel_if_open(win)  # 打造完成后面板可能仍开着，挡住后续点击
        success = True
        return True, pinfo
    finally:
        if not success:
            # 失败清理：面板/对话框残留会挡住 follow 的任务链接点击
            try:
                _close_residual_ui(win)
            except Exception:  # noqa: BLE001 —— 收尾不得掩盖原始失败
                pass
