"""给与面板物品选择：模板定位候选格 → 悬停 OCR 物品名 → 按需求选物 → 给与。

300环给与类环（用户 2026-11-26 实机）：追踪「快去找到…并将其交给XXX」
→ 到达后对话框选「这是你被抢走的东西」→ 打开**给与面板**。面板里同名
道具图标几乎一样（实机天书残卷第三卷/第七卷），不能靠图标模板区分；
做法是：物品图标模板只用来定位候选格，逐个悬停读游戏自带物品名提示
（「天书残卷 第三卷」），按任务要求的卷号选真正需要的那一件，再点
「给与」。提示名 OCR 实测 1.00（2026-11-26 三帧）。

本模块不做任务编排，只提供：
- give_panel_open(bgr)：给与面板在场（bag.GIVE_TITLE_ICON）；
- find_candidates(win, icon)：当前储物格内图标模板的全部命中；
- find_candidates_multi(win, icons)：一帧批量定位多个候选模板；
- iter_slot_candidates(win, icons)：逐储物格产出候选（给与面板一次只
  显示一个储物格，属性不达标必须继续扫后续格）；
- hover_item_name(win, pt, keyword)：悬停候选格并 OCR 物品名提示；
- read_tooltip(win, pt, keyword)：悬停并读（名称行, 属性值）；
- read_tooltip_level(win, pt, keyword)：悬停并读（名称行, 装备等级）；
- pick_and_give(win, icon, accept, keyword, prefix)：选物 + 点给与；
- pick_and_give_attr(win, candidates, low, high, prefix)：类别/属性给与
  （名称之外再用提示框属性行复核区间，防同类不同属性物品递错）；
- pick_and_give_level(win, candidates, low, high, prefix)：等级类别给与
  （宝宝装备，提示框「等级：N级」行复核区间）；
- give_choice_panel_open(img_np)：双选一给与面板在场（面板说明行模板）；
- pick_choice_and_give(win, candidates, prefix)：双选一面板点选物品 →
  待给予框复核 → 点确定（2026-11-27 实机「千年魂石或天书残卷一或二」）。
"""
import re
import time

import cv2
import numpy as np

from ..core import app_utils
from ..core.app_utils import human_click, log
from ..device import bag
from ..device import items as item_catalog
from ..device import map as game_map
from ..device import silent_click

# 给与面板底部 NPC 行区域（与客栈 kezhan.GIVE_PANEL_NPC_REGION 同布局：
# 实机「NPC：苏巧儿」位于 (543,579)-(647,598)，右缘放宽到 740 兼容长名）。
GIVE_NPC_REGION = (520, 565, 740, 615)
_NPC_RE = re.compile(r"NPC[：:]\s*(.+)")


def npc_names_match(target, npc):
    """给与目标名（追踪文案）与面板「NPC：」名是否一致（容 1 字 OCR 误读）。

    追踪面板小字 OCR 会误读 NPC 名（2026-11-27 实机「彩翼仙子」读成
    「彩算仙子」），严格子串比对会把可给与判成不匹配、每 5s 拦截一次
    永不交付。等长且 ≥3 字时容 1 字差异（同地图名容错规则）；短名保持
    严格防给错。任一方缺失视为无可比对（返回 True，调用方跳过拦截）。
    """
    target = (target or "").strip()
    npc = (npc or "").strip()
    if not target or not npc:
        return True
    if target in npc or npc in target:
        return True
    if len(target) == len(npc) >= 3:
        return sum(a != b for a, b in zip(target, npc)) <= 1
    return False


def read_give_npc(win, img=None, region=None):
    """OCR 给与面板 NPC 行，返回对象名（识别失败返回空串/原文）。

    300环给与环（用户 2026-11-26）：任务文案「并将其交给黑熊精」给目标，
    给与面板底部显示「NPC：黑熊精」——点给与前核对两者一致，防止给错
    对象。OCR 只读面板底部小区域（~50ms，给与尝试 5s 节流）。region=None
    用旧给与面板的固定带；双选一面板的 NPC 行更低（实机「NPC：梅花仙」
    (518,614)-(626,638)），调用方传 GIVE_CHOICE_NPC_REGION。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return ""
    items, _ = game_map.ocr_region(img, region or GIVE_NPC_REGION)
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


def _icon_threshold(icon, default):
    """模板匹配阈值：物品目录登记的专用阈值优先（未登记用调用方默认）。

    未裁剪、带数量角标的模板（珍兽丹/摄妖香 0.69）数量字每次渲染可能
    不同，用给与默认 0.85 会漏检；误命中由悬停物品名 OCR 复核兜底。
    """
    catalog_threshold = item_catalog.threshold_for_icon(icon)
    return catalog_threshold if catalog_threshold > 0 else default


def _match_icons(bgr, icons, threshold=CANDIDATE_THRESHOLD):
    """一帧 BGR 上匹配多个候选图标模板（限定给与面板物品区）。"""
    out = {}
    for icon in icons:
        hits = game_map.match_frame_all(
            bgr, icon, threshold=_icon_threshold(icon, threshold),
            region=GIVE_PANEL_BAND)
        pts = [(int(x), int(y)) for _s, x, y in hits]
        pts.sort(key=lambda p: (p[1], p[0]))
        if pts:
            out[icon] = pts
    return out


def find_candidates(win, icon, threshold=CANDIDATE_THRESHOLD):
    """当前储物格内物品图标模板的全部命中中心（按 y,x 排序）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return []
    return _match_icons(game_map.bgr_frame(img), [icon], threshold).get(icon, [])


def find_candidates_multi(win, icons, threshold=CANDIDATE_THRESHOLD):
    """当前储物格内一帧批量定位多个候选图标模板。

    返回 {icon: [(x, y), ...]}，只含命中的图标，点按 (y,x) 排序。
    必须共用一帧：逐候选重新截帧会在上一个候选的提示框仍显示时截到
    被提示框遮挡的面板，后续模板漏检（2026-11-27 给与面板实测提示框
    盖住相邻候选格）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return {}
    return _match_icons(game_map.bgr_frame(img), icons, threshold)


# 扫描前把光标停到面板外（清掉上一次悬停残留的提示框遮挡）。给与面板
# 物品区右缘 ~875，取右侧世界空白处。
SCAN_PARK_POINT = (1050, 620)
# 切换储物格后的界面刷新等待（与 bag.find_item_with_slots 同节奏 0.1s，
# 留 50ms 余量）
SLOT_SWITCH_WAIT = 0.15
# 光标移开后等提示框消失（提示框可盖住面板左缘 give_title，导致切格
# 前台复检误判「面板丢失」）
TOOLTIP_HIDE_WAIT = 0.25
# 给与面板储物格最多 5 个（壹～伍，35px 步长；角色解锁数量不同，部分
# 只有 4 个，第 5 格点击落在面板空白处不改变状态）。不能沿用
# bag.SLOT_OFFSETS 的 4 格——武帝袍在最右侧第 5 格时永远找不到。
GIVE_SLOT_OFFSETS = (0, 35, 70, 105, 140)


def iter_slot_candidates(win, icons, threshold=CANDIDATE_THRESHOLD,
                         prefix=""):
    """逐储物格产出 (格子标签, {icon: [pt,...]})；面板丢失即停。

    给与面板一次只显示一个储物格，必须先扫当前格再按储物格按钮逐格
    切换。**不能在第一个图标命中的格子停下**——同款不同属性（+8/+9）
    模板会互相命中，属性复核失败后必须继续扫后续格子（2026-11-27 实机：
    第 3 格云罗帐+8 图标命中但属性 8 不达标，提前返回会漏掉后面格子里的
    +9 武帝袍）。未命中的格子也产出空 hits，调用方可据此统计扫描格数。
    每次切格前把光标移出面板、等提示框消失，再用当前帧复检面板在场
    （提示框盖住 give_title 会误判面板丢失）；面板丢失立即中止，绝不
    盲点（2026-11-26 摄妖香切格点击落到地图的教训）。
    """
    _hover(win, *SCAN_PARK_POINT)  # 清掉上一次悬停残留的提示框
    time.sleep(TOOLTIP_HIDE_WAIT)
    # 储物格按钮坐标必须在任何悬停之前从干净帧取：悬停提示框可能覆盖
    # 右侧储物格列（物品靠右时提示框向右翻转），切到一半再取会取不到
    base = bag.find_box_one_close(win)
    yield "当前储物格", find_candidates_multi(win, icons, threshold)
    if base is None:
        return
    for i, offset in enumerate(GIVE_SLOT_OFFSETS):
        _hover(win, *SCAN_PARK_POINT)
        time.sleep(TOOLTIP_HIDE_WAIT)
        if not bag.is_slot_panel_open(win):
            log(f"{prefix}给与面板不在前台，中止储物格切换", "WARN")
            return
        human_click(win, base.x, base.y + offset)
        time.sleep(SLOT_SWITCH_WAIT)
        yield (f"储物格 {i + 1}/{len(GIVE_SLOT_OFFSETS)}",
               find_candidates_multi(win, icons, threshold))


def tooltip_band(pt, size):
    """候选格 pt 对应的提示框 OCR 动态带（含左右/上下翻转余量）。"""
    x, y = int(pt[0]), int(pt[1])
    w, h = size
    return (max(0, x + TOOLTIP_DX0), max(0, y + TOOLTIP_DY0),
            min(w, x + TOOLTIP_DX1), min(h, y + TOOLTIP_DY1))


# 提示框属性行：「抗水 +9.0%」「抗封印 +9.0%」。百分号优先；OCR 偶发
# 丢百分号时用「抗X +N」形态兜底（炼妖石描述行不含「抗」，只有属性行有）
_ATTR_PCT_RE = re.compile(r"[+＋]\s*(\d+)\s*(?:\.\d+)?\s*[%％]")
_ATTR_KW_RE = re.compile(r"抗\S{0,4}\s*[+＋]\s*(\d+)")


def _tooltip_attr(items):
    """提示框 OCR 行 → 属性值（「抗水 +9.0%」→ 9），未读到 None。"""
    for txt, _r, score in items or []:
        if score < 0.55:
            continue
        m = _ATTR_PCT_RE.search(txt or "") or _ATTR_KW_RE.search(txt or "")
        if m:
            return int(m.group(1))
    return None


# 提示框等级行：「等级：1级」（2026-11-27 实机宝宝装备悬停 1.000）。
# 全角/半角冒号都兼容；只认「等级」前缀，描述行里的「N级」不参与。
_TOOLTIP_LEVEL_RE = re.compile(r"等级\s*[：:]\s*(\d+)\s*级")


def _tooltip_level(items):
    """提示框 OCR 行 → 装备等级（「等级：1级」→ 1），未读到 None。"""
    for txt, _r, score in items or []:
        if score < 0.55:
            continue
        m = _TOOLTIP_LEVEL_RE.search(txt or "")
        if m:
            return int(m.group(1))
    return None


def read_tooltip_full(win, pt, keyword, band=None):
    """悬停候选格 → OCR 提示框 → (名称行, 属性值或 None, 等级或 None)。

    名称行取包含 keyword 的最短行（标题行最短，描述行很长）；属性值与
    等级参考行分别供炼妖石属性给与/宝宝装备等级给与复核。band=None 时
    按候选格动态取带（提示框随物品位置移动/翻转）。
    """
    if not _hover(win, pt[0], pt[1]):
        return None, None, None
    time.sleep(TOOLTIP_WAIT)
    img = win.capture(force_activate=False)
    if img is None:
        return None, None, None
    if band is None:
        band = tooltip_band(pt, img.size)
    items, _ = game_map.ocr_region(img, band)
    cands = [t for t, r, s in items
             if s >= 0.55 and keyword in (t or "")]
    name = min(cands, key=len) if cands else None
    return name, _tooltip_attr(items), _tooltip_level(items)


def read_tooltip(win, pt, keyword, band=None):
    """悬停候选格 → (名称行, 属性值或 None)（炼妖石类别给与用）。"""
    name, attr, _level = read_tooltip_full(win, pt, keyword, band)
    return name, attr


def read_tooltip_level(win, pt, keyword, band=None):
    """悬停候选格 → (名称行, 装备等级或 None)（宝宝装备等级给与用）。"""
    name, _attr, level = read_tooltip_full(win, pt, keyword, band)
    return name, level


def hover_item_name(win, pt, keyword, band=None):
    """悬停 pt 并 OCR 物品名提示，返回包含 keyword 的行（无则 None）。"""
    return read_tooltip(win, pt, keyword, band)[0]


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
    tried, hit_pts = [], 0
    scanned = 0
    for _slot, hits in iter_slot_candidates(win, [icon], prefix=prefix):
        scanned += 1
        for pt in hits.get(icon, []):
            hit_pts += 1
            name = hover_item_name(win, pt, keyword)
            time.sleep(CANDIDATE_GAP)
            if not name:
                continue
            tried.append(name)
            if not accept(name):
                continue
            return _click_and_give(win, pt, name, prefix)
    if not tried:
        if hit_pts:
            # 图标已命中但提示名一行都没读到：通常是 keyword 与游戏提示名
            # 不一致（2026-11-28 实机：规范名「配饰盒子」漏一字读不到，
            # 与「图标没找到」是两种故障，分开报便于直接定位）
            return False, (f"候选图标命中 {hit_pts} 处但悬停未读到名称"
                           f"（名称关键词「{keyword}」，扫 {scanned} 个储物格）")
        return False, (f"给与面板未找到候选物品图标 {icon}"
                       f"（扫 {scanned} 个储物格）")
    return False, f"候选物品均不匹配（读到 {tried or '无'}）"


def pick_and_give_attr(win, candidates, low, high, prefix=""):
    """类别型给与（属性定值/区间）：批次定位 → 悬停读名+属性 → 达标才给与。

    candidates 为 [(icon, name), ...]（device.items.ItemDef 的 icon/name）。
    名称命中只说明图标像；属性以提示框属性行为准——同类不同属性的物品
    图标模板会互相命中（2026-11-27 实机：+8 沧海珠命中 +9 模板 0.925、
    +8 蓝田玉命中 0.98），只按名称会把不达标物品递出去（任务「9-12
    属性的炼妖石」，背包内同款 +8/+9 并存）。返回 (ok, 描述)。
    """
    icons = [icon for icon, _name in candidates]
    tried, hit_pts = [], 0
    scanned = 0
    for _slot, hits in iter_slot_candidates(win, icons, prefix=prefix):
        scanned += 1
        for icon, name in candidates:
            for pt in hits.get(icon, []):
                hit_pts += 1
                seen, attr = read_tooltip(win, pt, name)
                time.sleep(CANDIDATE_GAP)
                if not seen:
                    continue
                tried.append(f"{seen}+{attr}" if attr is not None
                             else f"{seen}(属性未识别)")
                if attr is None or not (low <= attr <= high):
                    continue
                return _click_and_give(win, pt, seen, prefix)
    req = f"{low}" if low == high else f"{low}-{high}"
    if not tried:
        if hit_pts:
            return False, (f"候选图标命中 {hit_pts} 处但悬停未读到名称"
                           f"（扫 {scanned} 个储物格）")
        return False, (f"给与面板未找到候选物品图标"
                       f"（{len(icons)} 个模板，扫 {scanned} 个储物格）")
    return False, (f"候选物品属性均不满足 {req}"
                   f"（扫 {scanned} 个储物格，读到 {tried or '无'}）")


def pick_and_give_level(win, candidates, low, high, prefix=""):
    """等级类别型给与（宝宝装备 1级或2级）：名称之外复核提示框等级行。

    candidates 为 [(icon, name), ...]。与 pick_and_give_attr 同链路，数值
    改读提示框「等级：N级」行——同类装备不同等级可能共用图标，只凭
    图标/名称会把不达标等级的装备递出去（2026-11-27 实机提示框
    「等级：1级」1.000）。未读到等级一律不递（宁可下轮重试）。返回
    (ok, 描述)。
    """
    icons = [icon for icon, _name in candidates]
    tried, hit_pts = [], 0
    scanned = 0
    for _slot, hits in iter_slot_candidates(win, icons, prefix=prefix):
        scanned += 1
        for icon, name in candidates:
            for pt in hits.get(icon, []):
                hit_pts += 1
                seen, level = read_tooltip_level(win, pt, name)
                time.sleep(CANDIDATE_GAP)
                if not seen:
                    continue
                tried.append(f"{seen} {level}级" if level is not None
                             else f"{seen}(等级未识别)")
                if level is None or not (low <= level <= high):
                    continue
                return _click_and_give(win, pt, seen, prefix)
    req = f"{low}" if low == high else f"{low}-{high}"
    if not tried:
        if hit_pts:
            return False, (f"候选图标命中 {hit_pts} 处但悬停未读到名称"
                           f"（扫 {scanned} 个储物格）")
        return False, (f"给与面板未找到候选物品图标"
                       f"（{len(icons)} 个模板，扫 {scanned} 个储物格）")
    return False, (f"候选物品等级均不满足 {req} 级"
                   f"（扫 {scanned} 个储物格，读到 {tried or '无'}）")


# ---- 双选一给与面板（2026-11-27 可乐可口实机）----
# 面板：「请从两种物品任选一种给予（雅誉不同）」+ 上半部两种需求物品与
# 雅誉（如「物品 天书残卷一或二 / 雅誉 3」「物品 千年魂石 / 雅誉 40」）+
# 中间「待给予物品」框 + 确定按钮 + 下方物品格（5 列 × 2 行，只列出本次
# 可交的物品，可能是多页）+ 底部 NPC 行（与旧给与面板相同）。
# 交互（2026-11-27 实机逐步验证）：点物品格里的图标 → 该格红框选中、
# 「待给予物品」框显示该物品 → 点「确定」才真正给与；不点确定不会给出去，
# 点已选中的物品不会取消选择（重试安全）。
# 与旧给与面板（储物格列 + 给与按钮 + 悬停读名）完全不同，不能复用旧
# 选物链路；物品格是游戏按任务过滤后的可交列表，图标模板只用于在格子里
# 认出目标物品（千年魂石 47x47 同帧 1.00；旧天书模板在格内 0.832，取
# 0.8 阈值兼容；两模板交叉匹配实测 ≤0.46，无互命中风险）。
GIVE_CHOICE_TITLE_ICON = "give_choice_title.png"      # 顶部说明行（固定文案）
GIVE_CHOICE_TITLE_BAND = (470, 235, 905, 320)
GIVE_CHOICE_CONFIRM_ICON = "give_choice_confirm.png"  # 「确定」按钮
GIVE_CHOICE_CONFIRM_BAND = (620, 440, 780, 510)
GIVE_CHOICE_GRID_BAND = (528, 486, 862, 578)          # 物品格
GIVE_CHOICE_PENDING_BAND = (655, 300, 735, 370)       # 待给予物品框
# 双选一面板的 NPC 行比旧面板低（实机「NPC：梅花仙」(518,614)-(626,638)，
# 旧带 (520,565,740,615) 只擦到上缘读不到）
GIVE_CHOICE_NPC_REGION = (500, 600, 780, 652)
GIVE_CHOICE_THRESHOLD = 0.80
GIVE_CHOICE_SELECT_WAIT = 0.5       # 点物品后等选中渲染
GIVE_CHOICE_CONFIRM_TIMEOUT = 3.0   # 点确定后等面板关闭
GIVE_CHOICE_CONFIRM_POLL = 0.3


def give_choice_panel_open(img_np):
    """双选一给与面板是否在场（顶部说明行模板；须传完整帧）。

    注意：follow 传给 extra_dialog_handler 的 img_np 是 OCR 掩码帧
    （只保留追踪区 + 对话区像素，其余全黑），而说明行顶部在对话区上缘
    之外会被切掉（2026-11-27 实机：完整帧 1.000 / 掩码帧 0.267），
    面板在场也检测不到。任务侧轮询判定请用 give_choice_panel_in_items
    （对话区 OCR 文本），本函数仅用于完整帧（如 pick_choice_and_give
    内部的自截帧轮询）。
    """
    bgr = _bgr(img_np)
    if bgr is None:
        return False
    return game_map.match_frame_np(
        bgr, GIVE_CHOICE_TITLE_ICON,
        region=GIVE_CHOICE_TITLE_BAND) is not None


def give_choice_panel_in_items(items):
    """双选一给与面板是否在场（对话区 OCR 文本判定，掩码帧可用）。

    面板说明行「请从两种物品任选一种给予（雅誉不同）」在对话区 OCR 里
    仍可读出（2026-11-27 实机 0.93，说明行顶部略出对话区上缘但文字
    基线在内）；OCR 漏读说明行时用面板独有的两行「雅誉」需求行兜底。
    rect[0] >= 400 只认居中面板/对话框区，排除左侧任务追踪文本。
    """
    n_yayu = 0
    for txt, rect, score in items or []:
        if score < 0.55 or rect[0] < 400:
            continue
        text = txt or ""
        if "任选一种" in text:
            return True
        if "雅誉" in text:
            n_yayu += 1
    return n_yayu >= 2


def pick_choice_and_give(win, candidates, prefix=""):
    """双选一面板：按候选顺序点选物品 → 复核待给予框 → 点确定。

    candidates 为 [(icon, name), ...]（按优先顺序；图标模板在物品格里
    定位）。只点确定前复核「待给予物品」框已显示同一图标，否则不点确定。
    返回 (ok, 描述)。
    """
    if not candidates:
        return False, "没有候选物品"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截帧失败（双选一给与面板物品格）"
    bgr = game_map.bgr_frame(img)
    # 按有效阈值分组批量匹配：match_frame_multi 一次只吃一个阈值，
    # 带数量角标的模板（珍兽丹 0.69）与默认 0.8 的模板分组各调一次，
    # 每组共用同一帧（灰度转换按组各做一次，阈值种类极少）。
    groups = {}
    for icon, _name in candidates:
        groups.setdefault(_icon_threshold(icon, GIVE_CHOICE_THRESHOLD),
                          []).append(icon)
    hits = {}
    for th, icons in groups.items():
        hits.update(game_map.match_frame_multi(
            bgr, icons, threshold=th,
            regions=[GIVE_CHOICE_GRID_BAND] * len(icons)))
    chosen = None
    tried = []
    for icon, name in candidates:
        pt = hits.get(icon)
        if pt is not None:
            chosen = (icon, name, pt)
            break
        tried.append(name)
    if chosen is None:
        return False, (f"物品格未找到候选物品图标"
                       f"（{len(icons)} 个模板，未命中 {tried}）")
    icon, name, pt = chosen
    human_click(win, pt[0], pt[1])
    time.sleep(GIVE_CHOICE_SELECT_WAIT)
    img2 = win.capture(force_activate=False)
    if img2 is None:
        return False, f"已点「{name}」但复核帧截取失败（未点确定）"
    bgr2 = game_map.bgr_frame(img2)
    pending = game_map.match_frame_np(
        bgr2, icon, threshold=_icon_threshold(icon, GIVE_CHOICE_THRESHOLD),
        region=GIVE_CHOICE_PENDING_BAND, fallback=False)
    if pending is None:
        return False, f"已点「{name}」但待给予框未显示该物品（未点确定）"
    btn = game_map.match_frame_np(
        bgr2, GIVE_CHOICE_CONFIRM_ICON, region=GIVE_CHOICE_CONFIRM_BAND,
        fallback=False)
    if btn is None:
        return False, f"已选「{name}」但未找到确定按钮（未点确定）"
    human_click(win, btn[0], btn[1])
    deadline = time.time() + GIVE_CHOICE_CONFIRM_TIMEOUT
    while time.time() < deadline:
        img3 = win.capture(force_activate=False)
        if img3 is None:
            time.sleep(GIVE_CHOICE_CONFIRM_POLL)
            continue
        if not give_choice_panel_open(img3):
            log(f"{prefix}已选择「{name}」并点击确定，面板已关闭", "SYS")
            return True, f"已给与「{name}」"
        time.sleep(GIVE_CHOICE_CONFIRM_POLL)
    return False, f"已点确定但面板未关闭（物品「{name}」可能未给出）"
