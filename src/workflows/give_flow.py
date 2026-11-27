"""给与面板物品选择：模板定位候选格 → 悬停 OCR 物品名 → 按需求选物 → 给与。

300环给与类环（用户 2026-11-26 实机）：追踪「快去找到…并将其交给XXX」
→ 到达后对话框选「这是你被抢走的东西」→ 打开**给与面板**。面板里同名
道具图标几乎一样（实机天书残卷第三卷/第七卷），不能靠图标模板区分；
做法是：物品图标模板只用来定位候选格，逐个悬停读游戏自带物品名提示
（「天书残卷 第三卷」），按任务要求的卷号选真正需要的那一件，再点
「给与」。提示名 OCR 实测 1.00（2026-11-26 三帧）。

本模块不做任务编排，只提供：
- give_panel_open(bgr)：给与面板在场（bag.GIVE_TITLE_ICON）；
- find_candidates(win, icon)：面板内物品图标模板的全部命中；
- hover_item_name(win, pt, keyword)：悬停候选格并 OCR 物品名提示；
- pick_and_give(win, icon, accept, keyword, prefix)：选物 + 点给与。
"""
import re
import time

import cv2
import numpy as np

from ..core import app_utils
from ..core.app_utils import human_click, log
from ..device import bag
from ..device import map as game_map
from ..device import silent_click

# 给与面板底部 NPC 行区域（与客栈 kezhan.GIVE_PANEL_NPC_REGION 同布局：
# 实机「NPC：苏巧儿」位于 (543,579)-(647,598)，右缘放宽到 740 兼容长名）。
GIVE_NPC_REGION = (520, 565, 740, 615)
_NPC_RE = re.compile(r"NPC[：:]\s*(.+)")


def read_give_npc(win, img=None):
    """OCR 给与面板 NPC 行，返回对象名（识别失败返回空串/原文）。

    300环给与环（用户 2026-11-26）：任务文案「并将其交给黑熊精」给目标，
    给与面板底部显示「NPC：黑熊精」——点给与前核对两者一致，防止给错
    对象。OCR 只读面板底部小区域（~50ms，给与尝试 5s 节流）。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return ""
    items, _ = game_map.ocr_region(img, GIVE_NPC_REGION)
    txt = "".join(t for t, _r, s in items if s >= 0.55)
    m = _NPC_RE.search(txt)
    name = m.group(1) if m else txt
    return name.strip(" 　，,。；;:：")


# 给与面板物品格搜索区（实机 2026-11-26：面板物品区约 (530,300)-(820,500)）
GIVE_PANEL_BAND = (500, 285, 875, 515)
# 悬停物品名提示 OCR 带：以候选格为中心的动态窗口。提示框在物品左侧/
# 上方，靠近屏幕边缘时会向右/向下翻转；固定带只覆盖实机底部行的位置，
# 物品在其他行（尤其顶部行）提示框上移会漏读（2026-11-27 用户提问）。
TOOLTIP_DX0, TOOLTIP_DX1 = -430, 270
TOOLTIP_DY0, TOOLTIP_DY1 = -270, 130
# 候选物品图标模板阈值（同图标多卷共用；名称由提示 OCR 区分）
CANDIDATE_THRESHOLD = 0.85
TOOLTIP_WAIT = 0.75      # 悬停后等提示框渲染
CANDIDATE_GAP = 0.55     # 逐候选悬停间隔
GIVE_BUTTON_TIMEOUT = 2.0


def _bgr(img_np):
    """RGB numpy 帧 → BGR（模板匹配用；OCR 路径保持 PIL/RGB）。"""
    if img_np is None:
        return None
    return cv2.cvtColor(np.asarray(img_np)[:, :, :3], cv2.COLOR_RGB2BGR)


def give_panel_open(img_np):
    """给与面板是否打开（img_np 为 follow 传入的 RGB 帧）。"""
    bgr = _bgr(img_np)
    if bgr is None:
        return False
    return game_map.match_frame_np(bgr, bag.GIVE_TITLE_ICON) is not None


def _hover(win, x, y):
    """窗口相对坐标悬停（静默模式走消息，物理模式移动鼠标）。"""
    if app_utils.silent_mode_on():
        return silent_click.send_silent_move(win, x, y) is not None
    try:
        rect = getattr(win, "rect", None)
        left = int(getattr(rect, "left", 0) or 0)
        top = int(getattr(rect, "top", 0) or 0)
        app_utils.human_move(left + int(x), top + int(y))
        return True
    except Exception:  # noqa: BLE001 —— 悬停失败按未命中处理
        return False


def find_candidates(win, icon, threshold=CANDIDATE_THRESHOLD):
    """面板内物品图标模板的全部命中中心（按 y,x 排序）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return []
    bgr = game_map.bgr_frame(img)
    hits = game_map.match_frame_all(bgr, icon, threshold=threshold,
                                    region=GIVE_PANEL_BAND)
    pts = [(int(x), int(y)) for _s, x, y in hits]
    pts.sort(key=lambda p: (p[1], p[0]))
    return pts


def tooltip_band(pt, size):
    """候选格 pt 对应的提示框 OCR 动态带（含左右/上下翻转余量）。"""
    x, y = int(pt[0]), int(pt[1])
    w, h = size
    return (max(0, x + TOOLTIP_DX0), max(0, y + TOOLTIP_DY0),
            min(w, x + TOOLTIP_DX1), min(h, y + TOOLTIP_DY1))


def hover_item_name(win, pt, keyword, band=None):
    """悬停 pt 并 OCR 物品名提示，返回包含 keyword 的行（无则 None）。

    band=None 时按候选格动态取带（提示框随物品位置移动/翻转）。
    """
    if not _hover(win, pt[0], pt[1]):
        return None
    time.sleep(TOOLTIP_WAIT)
    img = win.capture(force_activate=False)
    if img is None:
        return None
    if band is None:
        band = tooltip_band(pt, img.size)
    items, _ = game_map.ocr_region(img, band)
    cands = [t for t, r, s in items
             if s >= 0.55 and keyword in (t or "")]
    if not cands:
        return None
    # 提示框标题行最短（描述行很长但不含 keyword 的卷号形态也可能含
    # keyword）：取包含 keyword 且最短的行作为名称行
    return min(cands, key=len)


# 给与面板「给与」按钮模板（与五环/客栈共用同一美术，res/img/give_btn.png）
GIVE_BTN_ICON = "give_btn.png"


def _give_button(win):
    """给与按钮（GIVE_BTN_ICON）定位，返回中心点或 None。"""
    deadline = time.time() + GIVE_BUTTON_TIMEOUT
    while time.time() < deadline:
        pt = game_map.find_template(win, GIVE_BTN_ICON)
        if pt is not None:
            return pt
        time.sleep(0.3)
    return None


# 未登记物品的全格悬停兜底：给与面板 6x4 物品格中心（实机 2026-11-26）
GIVE_GRID_X = (558, 603, 648, 693, 738, 783)
GIVE_GRID_Y = (320, 365, 410, 455)


def _grid_points():
    return [(x, y) for y in GIVE_GRID_Y for x in GIVE_GRID_X]


def _click_and_give(win, pt, name, prefix=""):
    """选中候选格并点给与按钮，返回 (ok, 描述)。"""
    human_click(win, pt[0], pt[1])
    time.sleep(0.5)  # 选中渲染
    btn = _give_button(win)
    if btn is None:
        return False, f"已选「{name}」但未找到给与按钮"
    human_click(win, btn[0], btn[1])
    log(f"{prefix}已选择「{name}」并点击给与 @({pt[0]},{pt[1]})", "SYS")
    return True, f"已给与「{name}」"


def pick_and_give_any(win, accept, keyword, prefix=""):
    """未知图标物品：逐格悬停读名称，命中条件后选物+给与（慢路径）。"""
    tried = []
    for pt in _grid_points():
        name = hover_item_name(win, pt, keyword)
        time.sleep(CANDIDATE_GAP)
        if not name:
            continue
        tried.append(name)
        if accept(name):
            return _click_and_give(win, pt, name, prefix)
    return False, f"全格悬停未找到匹配物品（读到 {tried or '无'}）"


def pick_and_give(win, icon, accept, keyword, prefix=""):
    """在给与面板里选目标物品并点给与。返回 (ok, 描述)。

    accept(name) → bool：名称行（含 keyword）是否命中任务要求；
    icon：候选物品图标模板（只用于定位候选格，名称由提示 OCR 决定）。
    """
    pts = find_candidates(win, icon)
    if not pts:
        return False, f"给与面板未找到候选物品图标 {icon}"
    tried = []
    for pt in pts:
        name = hover_item_name(win, pt, keyword)
        time.sleep(CANDIDATE_GAP)
        if not name:
            continue
        tried.append(name)
        if not accept(name):
            continue
        return _click_and_give(win, pt, name, prefix)
    return False, f"候选物品均不匹配（读到 {tried or '无'}）"
