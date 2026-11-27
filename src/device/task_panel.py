"""任务栏（Alt+Q 任务面板）操作：开关面板、展开分组、选中任务、读取任务目的。

五倍任务在任务栏中的分组名为「降魔任务」（同一玩法不同叫法，用户
2026-11-23 明确）；修罗任务位于「常规玩法」分组下，子任务行文案
「修罗任务」（同组还可能有山河名望等其他子任务，须按任务名选中）。
识别结果见 demon_task。任务详情底部的「任务提示」段是玩法说明，
读取时整段截掉、不参与识别也不返回（用户 2026-11-26）。

面板结构（2026-11-23 实机标定，长相思窗口 1378x831）：
- 任务栏是游戏内独立面板，可用鼠标拖动，面板大小固定；左「任务列表」与
  右「任务详情」两栏的尺寸固定。
- 顶部为标签页（当前任务 / 日常活动 / 周常副本 / 其他任务）与右上角关闭 X。
- 左栏任务列表是树形：分组行如「降魔任务[1]」，行首有展开/折叠图标；
  子任务行缩进显示（如「三藏逢魔」）。
- 右栏任务详情首段为「任务目的：」，其后是任务轮次 / 今日已完成等段落。
- 分组图标：展开=两格（日形）、折叠=四格（田形）。9x9 图标本体模板
  双模板比分数实测目标态 0.97~1.00 / 另一态 0.80~0.87，分离良好。

定位不写死：两段标题条模板包含该栏完整宽度（220x21 / 291x21），模板命中
即得到栏的位置与宽度（用户 2026-11-23 指定）；行区域高度为面板固定值
（标题条底边向下 295px 为面板内容底边，实测 y316→611），面板被拖动后
同样成立。关闭按钮模板为右上角黑色圆形 X（34x34，全帧唯一，次高分 0.53）。

限制：分组行必须位于任务列表可视区内（本模块不做列表滚动）；任务详情
超出面板可视高度时只读可见部分（不做详情栏滚动）。识别出的任务类型与
目标见 demon_task（击杀 / 显形镜 / 需重新领取）。
"""
import re
import time

import cv2
import numpy as np

from ..core.app_utils import log, human_click, human_scroll
from . import demon_task
from . import map as game_map

TAG = "[任务栏] "

# 栏标题条模板（含栏内标题与右端文字，整条宽度=该栏宽度）
TASK_PANEL_LIST_ICON = "task_panel_list.png"        # 「任务列表…追踪」220x21
TASK_PANEL_DETAIL_ICON = "task_panel_detail.png"    # 「任务详情…分享」291x21
# 右上角关闭按钮（黑色圆形 X）
TASK_PANEL_CLOSE_ICON = "task_panel_close.png"      # 34x34
# 分组展开/折叠图标（同一位置两种状态，9x9，仅含图标本体不含行背景）
TASK_PANEL_GROUP_EXPANDED_ICON = "task_panel_group_expanded.png"
TASK_PANEL_GROUP_COLLAPSED_ICON = "task_panel_group_collapsed.png"

# 标题条 / 关闭按钮匹配阈值（实测目标帧 1.00，关闭按钮次高分 0.53）
TASK_PANEL_MATCH_THRESHOLD = 0.9
# 分组图标双模板比分数：实测目标态 0.97~1.00、另一态 0.80~0.87；取
# 0.90 + 领先 0.05 分离。模板只裁图标本体（9x9）：行被选中时行背景变灰，
# 含背景的 12x12 模板分数会掉到 0.87（实测 2026-11-23），9x9 仅图标
# 像素不受背景影响（选中折叠态 0.968）
TASK_PANEL_ICON_THRESHOLD = 0.9
TASK_PANEL_ICON_MARGIN = 0.05
# 标题条底边 → 面板内容底边的距离（固定面板尺寸，实测 316→611）
TASK_PANEL_BODY_HEIGHT = 295
# 列表右缘留白（排除滚动条；滚动条在栏右缘内侧约 10px）
TASK_PANEL_LIST_RIGHT_PAD = 14
# 开关面板 / 展开分组 / 选中任务后的轮询参数
TASK_PANEL_POLL_INTERVAL = 0.4
TASK_PANEL_TOGGLE_TIMEOUT = 4.0
TASK_PANEL_EXPAND_TIMEOUT = 2.5
TASK_PANEL_DETAIL_TIMEOUT = 2.0
# 同一视觉行的纵向合并容差（行距约 19px，同行碎片中心差 <2px）
TASK_PANEL_ROW_MERGE_DY = 8
# 子任务行与上一行（分组行/前一条子任务行）的最大中心距（行距约
# 19px，留一组噪声余量）
TASK_PANEL_CHILD_MAX_GAP = 40
# 子任务行右侧选中标记：详情栏正在显示的任务行右缘有勾选/追踪标记。
# 实机只读探针 2026-11-24 抓到 OCR 读作「[~]」（三藏逢魔行，面板打开
# 即已选中、详情栏已有内容）；「[√]」为既有测试样例的同类形态，一并
# 兼容。已选中时跳过点击直接读详情（用户 2026-11-24）；OCR 漏读按
# 未选中处理（照常点击，只少一次优化、不会读错任务）。
TASK_PANEL_SELECTED_MARKERS = ("~", "√", "✓", "☑")
# 目标分组关键词（「降魔任务」分组；标题栏任务名还会显示为
# 「三藏逢魔[降魔任务]」，此处只匹配任务列表里的分组行）
TASK_PANEL_DEMON_KEYWORD = "降魔"
# 修罗任务（用户 2026-11-26 实机截图）：位于「常规玩法」分组下（分组行
# 如「常规玩法[2]」），子任务行文案「修罗任务」。同组还可能有「山河
# 名望」等其他子任务，必须按任务名选中而不是取第一个子任务。
TASK_PANEL_XIULUO_GROUP = "常规玩法"
TASK_PANEL_XIULUO_TASK = "修罗任务"
# 任务详情中的「任务提示」段落：玩法说明（每日共享次数/混沌之气等），
# 不参与任务识别，也不需要返回（用户 2026-11-26）——读取结果整段截掉。
TASK_PANEL_HINT_HEAD = "任务提示"
# 任务详情中「任务目的」段落的结束标志（后续段落标题）
TASK_PANEL_SECTION_HEADS = (
    "任务轮次", "任务状态", "今日已完成", "当前状态", "任务奖励",
    "任务积分", "遣灵积分", "剩余时间", "你当前", "全队当前",
    TASK_PANEL_HINT_HEAD,
)
# 任务详情 OCR 行最低置信度（低于此值视为噪声：实测暗雷任务首行下方
# 出现过 0.548 的单字碎片「叁」）
TASK_PANEL_MIN_OCR_SCORE = 0.55
# 「当前任务」标签页：任务栏会记住上次标签（可能是日常活动/其他任务），
# 列表里找不到降魔分组时先切回当前任务页再重读。标签行在两块标题条
# 上方，纵向取标题条顶 -60 ~ -2px。
TASK_PANEL_TAB_TEXT = "当前任务"
TASK_PANEL_TAB_TOP_PAD = 60
# 顶部四个标签页（顺序固定；2026-11-26 实机 1378x831）
TASK_PANEL_TABS = ("当前任务", "日常活动", "周常副本", "其他任务")
TASK_PANEL_TAB_DAILY = "日常活动"
# 切页成功判据：日常活动页列表始终包含这些行之一（实机 2026-11-26：
# 常规玩法[6] 分组及其子项五环任务/客栈任务等、雁塔除妖、江湖历练任务、
# 街坊玩法；列表可滚动，任意一行出现即认为在正确的标签页）
TASK_PANEL_DAILY_ANCHORS = ("常规玩法", "五环任务", "客栈任务", "雁塔除妖",
                            "江湖历练任务", "街坊玩法")
# 列表滚动（human_scroll 在列表中心，正数向上、负数向下）：每次 3 行，
# 最多双向各 8 次（日常活动全列表 ~20 行，实测 2~3 次即可见底）
TASK_PANEL_SCROLL_LINES = 3
TASK_PANEL_MAX_SCROLLS = 8
TASK_PANEL_SCROLL_WAIT = 0.35

_GROUP_COUNT_RE = re.compile(r"[\[［【]\s*(\d+)\s*[\]］】]")


def _bgr(img):
    """PIL 帧 → BGR 数组（模板匹配用；OCR 路径保持 PIL/RGB 原帧）。"""
    return cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)


def _capture_bgr(win):
    """截一帧（不抢前台）并转 BGR，返回 (PIL 帧, BGR 帧) 或 (None, None)。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None, None
    return img, _bgr(img)


def _rect_from_center(center, size):
    w, h = size
    cx, cy = int(center[0]), int(center[1])
    left = cx - w // 2
    top = cy - h // 2
    return (left, top, left + w, top + h)


def _clip_region(region, width, height):
    l, t, r, b = (int(v) for v in region)
    return (max(0, l), max(0, t), min(width, r), min(height, b))


def locate_panel(img_bgr):
    """定位任务栏两栏，返回区域字典或 None。

    两块标题条模板均命中（阈值 0.9）才视为面板在场；位置由模板匹配中心
    给出，栏宽由模板宽度给出；内容区 = 标题条底边向下 295px。面板拖动后
    区域随模板移动，无写死坐标。
    """
    if img_bgr is None or not getattr(img_bgr, "size", 0):
        return None
    if img_bgr.ndim != 3 or img_bgr.shape[2] != 3:
        return None
    scores = game_map.best_match_multi_np(
        img_bgr, (TASK_PANEL_LIST_ICON, TASK_PANEL_DETAIL_ICON))
    l_score, l_pt = scores[TASK_PANEL_LIST_ICON]
    d_score, d_pt = scores[TASK_PANEL_DETAIL_ICON]
    if l_score < TASK_PANEL_MATCH_THRESHOLD \
            or d_score < TASK_PANEL_MATCH_THRESHOLD:
        return None
    l_size = game_map.template_size(TASK_PANEL_LIST_ICON)
    d_size = game_map.template_size(TASK_PANEL_DETAIL_ICON)
    if not l_size or not d_size:
        return None
    list_rect = _rect_from_center(l_pt, l_size)
    detail_rect = _rect_from_center(d_pt, d_size)
    h, w = img_bgr.shape[:2]
    list_region = _clip_region(
        (list_rect[0] + 1, list_rect[3] + 1,
         list_rect[2] - TASK_PANEL_LIST_RIGHT_PAD,
         list_rect[3] + TASK_PANEL_BODY_HEIGHT), w, h)
    detail_region = _clip_region(
        (detail_rect[0] + 1, detail_rect[3] + 1,
         detail_rect[2] - 3,
         detail_rect[3] + TASK_PANEL_BODY_HEIGHT), w, h)
    return {
        "list_header": list_rect,
        "detail_header": detail_rect,
        "list_region": list_region,
        "detail_region": detail_region,
        "scores": (l_score, d_score),
    }


def is_task_panel_open(win):
    """任务栏当前是否打开（双标题条模板判定）。"""
    _img, bgr = _capture_bgr(win)
    return bgr is not None and locate_panel(bgr) is not None


def _press_toggle_hotkey(win):
    """Alt+Q 任务栏开关（toggle）：静默模式走消息投递，否则前台热键。"""
    from ..core import app_utils
    if app_utils.silent_mode_on():
        from . import silent_click
        info = silent_click.send_silent_hotkey(win, ("alt",), "q")
        if info is None:
            return False, "静默 Alt+Q 投递失败（窗口不存在）"
        return True, "已静默投递 Alt+Q 任务栏开关"
    try:
        import win32gui
        from ascript.windows.hardware.keyboard import Keyboard
        if win32gui.GetForegroundWindow() != win.hwnd:
            win.activate()
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != win.hwnd:
                return False, "游戏窗口未在前台，Alt+Q 未发送"
        Keyboard.key_hot("alt", "q")
        return True, "已按 Alt+Q 任务栏开关"
    except Exception as e:
        return False, f"Alt+Q 发送异常: {e}"


def open_task_panel(win, prefix=TAG):
    """确保任务栏已打开：已打开直接返回；否则 Alt+Q 打开并轮询标题条。

    Alt+Q 是开关：首次投递后若检测超时（可能实际已打开但慢渲染），第二次
    投递会先关闭再打开，流程自愈（同背包 Alt+E 重试语义）。返回 (是否成功, 描述)。
    """
    _img, bgr = _capture_bgr(win)
    if bgr is not None and locate_panel(bgr) is not None:
        return True, "任务栏已打开"
    for attempt in (1, 2):
        sent, desc = _press_toggle_hotkey(win)
        if not sent:
            return False, desc
        log(f"{prefix}{desc}，等待任务栏打开...", "SYS")
        deadline = time.time() + TASK_PANEL_TOGGLE_TIMEOUT
        while time.time() < deadline:
            _img, bgr = _capture_bgr(win)
            if bgr is not None and locate_panel(bgr) is not None:
                return True, f"第{attempt}次 Alt+Q 打开成功"
            time.sleep(TASK_PANEL_POLL_INTERVAL)
        log(f"{prefix}第{attempt}次 Alt+Q 后未检测到任务栏，重试...", "WARN")
    return False, "两次 Alt+Q 后仍未打开任务栏"


def _match_close_button(bgr):
    """全帧匹配关闭按钮，返回 (score, (x, y)) 或 None。"""
    score, pt = game_map.best_match_np(bgr, TASK_PANEL_CLOSE_ICON)
    if score < TASK_PANEL_MATCH_THRESHOLD:
        return None
    return score, pt


def close_task_panel(win, prefix=TAG):
    """关闭任务栏：优先点击右上角关闭按钮，失败回退 Alt+Q 开关。

    点击后轮询双标题条消失（关闭动画很快）；按钮模板缺失时（面板不在常规
    位置/被遮挡）回退 Alt+Q。返回 (是否成功, 描述)。
    """
    _img, bgr = _capture_bgr(win)
    if bgr is None:
        return False, "窗口截图失败"
    if locate_panel(bgr) is None:
        return True, "任务栏未打开"
    for attempt in (1, 2):
        _img, bgr = _capture_bgr(win)
        if bgr is None:
            continue
        if locate_panel(bgr) is None:
            return True, "任务栏已关闭"
        close = _match_close_button(bgr)
        if close is None:
            log(f"{prefix}未找到关闭按钮模板，改用 Alt+Q 关闭", "WARN")
            break
        score, pt = close
        human_click(win, pt[0], pt[1])
        deadline = time.time() + TASK_PANEL_TOGGLE_TIMEOUT
        while time.time() < deadline:
            _img, bgr = _capture_bgr(win)
            if bgr is not None and locate_panel(bgr) is None:
                return True, f"第{attempt}次点击关闭按钮成功（匹配度 {score:.2f}）"
            time.sleep(TASK_PANEL_POLL_INTERVAL)
        log(f"{prefix}第{attempt}次点击关闭按钮后任务栏仍打开，重试...", "WARN")
    sent, desc = _press_toggle_hotkey(win)
    if not sent:
        return False, desc
    log(f"{prefix}关闭按钮未生效，{desc}，等待任务栏关闭...", "WARN")
    deadline = time.time() + TASK_PANEL_TOGGLE_TIMEOUT
    while time.time() < deadline:
        _img, bgr = _capture_bgr(win)
        if bgr is not None and locate_panel(bgr) is None:
            return True, "Alt+Q 关闭成功"
        time.sleep(TASK_PANEL_POLL_INTERVAL)
    return False, "点击关闭按钮与 Alt+Q 后任务栏仍未关闭"


def _group_count(text):
    """分组行的数量（如「降魔任务[1]」→ 1）；非分组行返回 None。"""
    m = _GROUP_COUNT_RE.search(text or "")
    return int(m.group(1)) if m else None


def _is_group_line(text):
    """分组行判定：短行且含「[数字]」计数（子任务行无计数）。"""
    return len((text or "").strip()) <= 24 and _group_count(text) is not None


def _visual_rows(items):
    """把 OCR 行碎片按纵向中心合并为视觉行。

    同一行的任务名与右侧状态标记（如 [√]）会被 OCR 拆成多个框；合并后
    row["items"] 按 x 排序、row["text"] 为同行拼接文本，row["rect"] 为
    最左（主文本）框——点击目标取主文本框，不会点到右侧标记。
    """
    rows = []
    for txt, rect, score in sorted(items, key=lambda it: (it[1][1], it[1][0])):
        cy = (rect[1] + rect[3]) // 2
        row = None
        for r in rows:
            if abs(r["cy"] - cy) <= TASK_PANEL_ROW_MERGE_DY:
                row = r
                break
        if row is None:
            rows.append({"cy": cy, "items": [(txt, rect, score)]})
        else:
            row["items"].append((txt, rect, score))
    for row in rows:
        row["items"].sort(key=lambda it: it[1][0])
        row["text"] = "".join(it[0] for it in row["items"])
        row["rect"] = row["items"][0][1]
    rows.sort(key=lambda r: r["cy"])
    return rows


def find_group_row(rows, keyword=TASK_PANEL_DEMON_KEYWORD):
    """在视觉行中找目标分组行（优先带计数的分组行，其次裸关键词）。"""
    groups = [r for r in rows if keyword and keyword in r["text"]
              and _is_group_line(r["text"])]
    if not groups:
        groups = [r for r in rows if keyword and keyword in r["text"]]
    return groups[0] if groups else None


def child_rows(rows, group_row):
    """分组行下的子任务行列表（至下一分组行或行距过大为止）。

    行距按相邻视觉行判定（行距约 19px，TASK_PANEL_CHILD_MAX_GAP=40
    留一组噪声余量）：分组下可能有多条子任务（「常规玩法」下实测有
    修罗任务 / 山河名望），全部纳入供按任务名挑选。
    """
    try:
        idx = rows.index(group_row)
    except ValueError:
        return []
    out = []
    prev_cy = group_row["cy"]
    for row in rows[idx + 1:]:
        if row["cy"] - prev_cy > TASK_PANEL_CHILD_MAX_GAP:
            break
        if _is_group_line(row["text"]):
            break
        out.append(row)
        prev_cy = row["cy"]
    return out


def first_child_row(rows, group_row, name_keyword=None):
    """分组行的第一个子任务行；下一行为另一分组/距离过远时返回 None。

    name_keyword：按任务名挑选子任务行（如「修罗任务」）。分组下有多条
    子任务时不指定关键字取第一个（五倍原行为）。
    """
    children = child_rows(rows, group_row)
    if name_keyword:
        children = [r for r in children if name_keyword in r["text"]]
    if not children:
        return None
    row = children[0]
    txt, rect, score = row["items"][0]  # 最左块=任务名，排除右侧 [√] 标记
    if len(txt.strip()) < 2:
        return None
    return {"text": txt, "rect": rect, "row": row, "score": score}


def row_selected(row):
    """视觉行是否带右侧选中标记（详情栏正在显示该任务）。

    只看任务名块右侧的 OCR 块（同一视觉行内 x 在任务名右缘之后）：
    游戏在已选中任务的行右缘显示勾选/追踪标记，OCR 实测读作「[~]」
    （2026-11-24 只读探针）。标记 OCR 漏读返回 False → 调用方照常
    点击选中（与旧行为一致，只会少一次优化、不会读错任务）。
    """
    items = row.get("items") or []
    if len(items) < 2:
        return False  # 只有任务名块：无右侧标记
    name_rect = items[0][1]
    for txt, rect, _score in items[1:]:
        if rect[0] < name_rect[2]:
            continue  # 不在任务名右侧（行内其它碎片）
        if any(m in (txt or "") for m in TASK_PANEL_SELECTED_MARKERS):
            return True
    return False


def group_expanded_state(img_bgr, group_rect):
    """分组展开状态：返回 (state, 图标中心, (展开分, 折叠分))。

    state ∈ {"expanded", "collapsed", "unknown"}。图标位于分组行行首
    （OCR 框左缘 -26~+26、纵向中心 ±10 内），双模板同帧比分数：目标态
    达标且领先另一态 0.05 才判定，避免单模板阈值误判（性能规则：相似
    模板互匹配一律双模板比分数）。
    """
    l, t, r, b = group_rect
    cy = (t + b) // 2
    h, w = img_bgr.shape[:2]
    roi = (max(0, l - 26), max(0, cy - 10),
           min(w, l + 26), min(h, cy + 10))
    if roi[2] <= roi[0] or roi[3] <= roi[1]:
        return "unknown", None, (0.0, 0.0)
    scores = game_map.best_match_multi_np(
        img_bgr, (TASK_PANEL_GROUP_EXPANDED_ICON,
                  TASK_PANEL_GROUP_COLLAPSED_ICON), region=roi)
    exp_score, exp_pt = scores[TASK_PANEL_GROUP_EXPANDED_ICON]
    col_score, col_pt = scores[TASK_PANEL_GROUP_COLLAPSED_ICON]
    if exp_score >= TASK_PANEL_ICON_THRESHOLD \
            and exp_score >= col_score + TASK_PANEL_ICON_MARGIN:
        return "expanded", exp_pt, (exp_score, col_score)
    if col_score >= TASK_PANEL_ICON_THRESHOLD \
            and col_score >= exp_score + TASK_PANEL_ICON_MARGIN:
        return "collapsed", col_pt, (exp_score, col_score)
    return "unknown", None, (exp_score, col_score)


def _read_list(win):
    """截一帧并 OCR 任务列表，返回 (rows, BGR 帧, PIL 帧, 面板字典)。"""
    img, bgr = _capture_bgr(win)
    if img is None:
        return None, None, None, None
    panel = locate_panel(bgr)
    if panel is None:
        return None, bgr, img, None
    items, _ = game_map.ocr_region(img, panel["list_region"])
    return _visual_rows(items), bgr, img, panel


def _tab_region(img, panel):
    """标签行 OCR 区域：两块标题条上方（纵向 标题条顶 -60 ~ -2px）。

    面板可拖动，区域随标题条模板定位；标签行横向覆盖两栏宽度。
    """
    size = getattr(img, "size", None)
    if not size:
        return None
    l, t, _r, _b = panel["list_header"]
    right = panel["detail_header"][2]
    region = (max(0, l), max(0, t - TASK_PANEL_TAB_TOP_PAD),
              min(size[0], right), max(1, t - 2))
    if region[2] <= region[0] or region[3] <= region[1]:
        return None
    return region


def _click_tab_text(win, img, panel, tab_text, prefix=TAG):
    """OCR 标签行并点击 tab_text 标签页中心。返回是否点击。

    标签文字唯一性：同一标签被 OCR 拆成多个框时取精确匹配优先、其次
    最高分框；完全读不到返回 False（不盲点其它标签）。
    """
    region = _tab_region(img, panel)
    if region is None:
        return False
    items, _ = game_map.ocr_region(img, region)
    cands = [it for it in items
             if tab_text in (it[0] or "")
             and it[2] >= TASK_PANEL_MIN_OCR_SCORE]
    if not cands:
        log(f"{prefix}未读到「{tab_text}」标签，无法切换标签页", "WARN")
        return False
    exact = [it for it in cands if (it[0] or "").strip() == tab_text]
    hit = max(exact or cands, key=lambda it: it[2])
    _txt, rect, score = hit
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    human_click(win, cx, cy)
    log(f"{prefix}已点击「{tab_text}」标签页 ({cx},{cy})"
        f"（匹配度 {score:.2f}），等待列表刷新", "SYS")
    return True


def _click_current_tab(win, panel, prefix=TAG):
    """点击任务栏「当前任务」标签页（仅在列表找不到目标分组时兜底）。

    任务栏会记住上次停留的标签页（日常活动/周常副本/其他任务），此时
    当前任务列表不可见。标签行位于两块标题条上方，区域随面板定位；
    OCR 找到「当前任务」文本后点击其中心，列表随标签切换重新渲染。
    返回是否点击。
    """
    img, bgr = _capture_bgr(win)
    if img is None:
        return False
    fresh = locate_panel(bgr) or panel
    if fresh is None:
        return False
    return _click_tab_text(win, img, fresh, TASK_PANEL_TAB_TEXT, prefix)


def _rows_text(rows):
    """列表视觉行文本按序拼接（滚动前后是否变化的比较键）。"""
    return "|".join(r["text"] for r in rows)


def _read_list_retry(win, attempts=2):
    """读一次列表；面板/截图瞬时失败时短重试。返回 (rows, panel)。"""
    for i in range(attempts):
        rows, bgr, _img, panel = _read_list(win)
        if rows is not None and panel is not None:
            return rows, panel
        if i + 1 < attempts:
            time.sleep(TASK_PANEL_POLL_INTERVAL)
    return None, None


def click_tab(win, tab_text, prefix=TAG):
    """点击任务栏指定标签页（不等待列表渲染）。返回 (是否成功, 描述)。"""
    img, bgr = _capture_bgr(win)
    if img is None:
        return False, "窗口截图失败"
    panel = locate_panel(bgr)
    if panel is None:
        return False, "未定位到任务栏面板"
    ok = _click_tab_text(win, img, panel, tab_text, prefix)
    return ok, ("已点击标签页" if ok else f"未找到「{tab_text}」标签")


def switch_to_tab(win, tab_text, anchors=(), prefix=TAG,
                  timeout=TASK_PANEL_DETAIL_TIMEOUT):
    """切换标签页并等待列表出现锚点行（确认切页成功）。

    anchors 为空时只点击一次、不做内容校验（返回点击结果）。日常活动
    页传 TASK_PANEL_DAILY_ANCHORS：列表可滚动，任一锚点行出现即确认。
    """
    for attempt in (1, 2):
        ok, info = click_tab(win, tab_text, prefix)
        if not ok:
            return False, info
        if not anchors:
            return True, f"已点击「{tab_text}」"
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(TASK_PANEL_POLL_INTERVAL)
            rows, _panel = _read_list_retry(win, attempts=1)
            if rows is None:
                continue
            if any(a in r["text"] for r in rows for a in anchors):
                return True, f"已切换到「{tab_text}」"
        log(f"{prefix}第{attempt}次点击「{tab_text}」后未出现预期列表，重试",
            "WARN")
    return False, f"两次点击「{tab_text}」后列表仍未切换"


def select_row(win, row, prefix=TAG):
    """点击任务行主文本（选中该行并刷新右侧任务详情）。

    row 为 _visual_rows 结构；点击点取主文本框中心（不含行首展开图标、
    不含右侧追踪标记）。返回 (是否点击, 描述)。
    """
    rect = (row or {}).get("rect")
    if not rect:
        return False, "任务行缺少文本框"
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    human_click(win, cx, cy)
    log(f"{prefix}已选中任务行「{row.get('text', '')}」({cx},{cy})，"
        f"等待详情刷新", "SYS")
    time.sleep(TASK_PANEL_POLL_INTERVAL)
    return True, "已选中任务行"


def read_list_rows(win):
    """读取任务列表视觉行。返回 (rows, 描述)；失败 rows 为 None。"""
    rows, panel = _read_list_retry(win)
    if rows is None:
        return None, "未定位到任务栏面板"
    return rows, "ok"


def find_row(rows, keyword, require_group=False):
    """在视觉行中查找包含 keyword 的行（可选仅分组行）。"""
    if not keyword:
        return None
    for row in rows:
        if keyword not in row["text"]:
            continue
        if require_group and not _is_group_line(row["text"]):
            continue
        return row
    return None


def scroll_list(win, lines, prefix=TAG):
    """在任务列表中心滚动滚轮（lines 正数向上、负数向下）。"""
    img, bgr = _capture_bgr(win)
    if img is None:
        return False, "窗口截图失败"
    panel = locate_panel(bgr)
    if panel is None:
        return False, "未定位到任务栏面板"
    l, t, r, b = panel["list_region"]
    cx, cy = (l + r) // 2, (t + b) // 2
    ok = human_scroll(win, cx, cy, int(lines))
    if not ok:
        return False, f"列表滚动投递失败 @({cx},{cy})"
    time.sleep(TASK_PANEL_SCROLL_WAIT)
    return True, f"列表滚动 {lines:+d} 行"


def scan_list_for(win, keyword, *, tab=None, anchors=(), prefix=TAG,
                  scroll=True, require_group=False,
                  max_scrolls=TASK_PANEL_MAX_SCROLLS):
    """在任务列表中查找 keyword 行（可切标签、双向滚动全列表）。

    返回 (命中行或 None, 描述)。命中行沿用 _visual_rows 结构
    （text/items/rect/cy）。滚动方向：先向上到顶，再向下扫到底；
    每个位置读取一次列表并检查。列表滚动后内容不变即判定到达边界。
    scroll=False 时只看当前可见区。
    """
    img, bgr = _capture_bgr(win)
    if img is None:
        return None, "窗口截图失败"
    panel = locate_panel(bgr)
    if panel is None:
        return None, "未定位到任务栏面板"
    if tab:
        ok, info = switch_to_tab(win, tab, anchors, prefix)
        if not ok:
            return None, info
    rows, panel = _read_list_retry(win)
    if rows is None:
        return None, "任务列表读取失败"
    hit = find_row(rows, keyword, require_group=require_group)
    if hit is not None:
        return hit, f"当前可见区已找到「{keyword}」"
    if not scroll:
        return None, f"当前可见区未找到「{keyword}」"
    # 向上扫到顶（human_scroll 正数向上）
    last = _rows_text(rows)
    for i in range(max_scrolls):
        ok, _info = scroll_list(win, TASK_PANEL_SCROLL_LINES, prefix)
        if not ok:
            return None, _info
        rows, panel = _read_list_retry(win)
        if rows is None:
            continue
        hit = find_row(rows, keyword, require_group=require_group)
        if hit is not None:
            return hit, f"向上滚动 {i + 1} 次后找到「{keyword}」"
        cur = _rows_text(rows)
        if cur == last:
            break
        last = cur
    # 向下扫到底
    last = _rows_text(rows or [])
    for i in range(max_scrolls):
        ok, _info = scroll_list(win, -TASK_PANEL_SCROLL_LINES, prefix)
        if not ok:
            return None, _info
        rows, panel = _read_list_retry(win)
        if rows is None:
            continue
        hit = find_row(rows, keyword, require_group=require_group)
        if hit is not None:
            return hit, f"向下滚动 {i + 1} 次后找到「{keyword}」"
        cur = _rows_text(rows)
        if cur == last:
            break
        last = cur
    return None, f"全列表扫描后未找到「{keyword}」"


def ensure_group_expanded(win, keyword=TASK_PANEL_DEMON_KEYWORD, prefix=TAG):
    """确保任务列表中的目标分组处于展开状态。

    判定顺序：读到子任务行 → 已展开；否则看分组图标（展开/折叠模板比
    分数）；折叠则点击图标并轮询子任务行出现。图标状态未知时按折叠处理
    点击一次；若实际是展开态会被收起，下一轮状态变为折叠后再点开（有限
    3 次，流程自愈，不会无限连点）。
    返回 (是否成功, 描述, 分组行或 None)。
    """
    last_info = "未知"
    for attempt in range(3):
        rows, bgr, _img, panel = _read_list(win)
        if rows is None:
            if panel is None and bgr is None:
                return False, "窗口截图失败", None
            return False, "未定位到任务栏面板", None
        group = find_group_row(rows, keyword)
        if group is None:
            # 任务栏可能停在其它标签页（上次停留记忆）：切回「当前任务」
            # 后重读一次；仍找不到才判定无分组
            if attempt == 0 and _click_current_tab(win, panel, prefix):
                time.sleep(TASK_PANEL_POLL_INTERVAL)
                continue
            return False, f"任务列表中未找到「{keyword}」分组", None
        child = first_child_row(rows, group)
        if child is not None:
            return True, f"「{group['text']}」已展开", group
        count = _group_count(group["text"])
        state, pt, scores = group_expanded_state(bgr, group["rect"])
        log(f"{prefix}「{group['text']}」状态={state}"
            f"（展开 {scores[0]:.2f} / 折叠 {scores[1]:.2f}）", "INFO")
        if state == "expanded":
            if count == 0:
                return True, f"「{group['text']}」已展开（分组下无任务）", group
            last_info = f"「{group['text']}」图标已展开但未读到子任务行"
            log(f"{prefix}{last_info}，重试", "WARN")
            time.sleep(TASK_PANEL_POLL_INTERVAL)
            continue
        click_pt = pt
        if click_pt is None:
            # 图标状态未知：分组图标恒在行首，按 OCR 框左缘推算中心
            l, t, r, b = group["rect"]
            click_pt = (l + 8, (t + b) // 2)
        human_click(win, click_pt[0], click_pt[1])
        log(f"{prefix}已点击「{group['text']}」"
            f"{'折叠' if state == 'collapsed' else '未知态'}图标"
            f" ({click_pt[0]},{click_pt[1]})", "SYS")
        deadline = time.time() + TASK_PANEL_EXPAND_TIMEOUT
        while time.time() < deadline:
            time.sleep(TASK_PANEL_POLL_INTERVAL)
            rows2, bgr2, _img2, panel2 = _read_list(win)
            if rows2 is None or bgr2 is None or panel2 is None:
                continue
            group2 = find_group_row(rows2, keyword)
            if group2 is None:
                continue
            if first_child_row(rows2, group2) is not None:
                return True, f"「{group2['text']}」展开成功", group2
        last_info = f"「{group['text']}」点击后未出现子任务行"
        log(f"{prefix}{last_info}，重试", "WARN")
    return False, f"三次尝试后仍未展开：{last_info}", None


def select_first_child(win, keyword=TASK_PANEL_DEMON_KEYWORD, prefix=TAG,
                       task_keyword=None):
    """在已展开的分组下选中子任务，返回 (子任务信息, 描述)。

    子任务信息：{'text': 任务名, 'rect': OCR 框, 'row': 视觉行}；失败返回
    (None, 描述)。点击任务名字框中心（行内选择，不会点到右侧 [√] 标记）；
    子任务行右侧已有选中标记（详情栏正在显示它）时跳过点击直接返回
    （用户 2026-11-24：已展开且已选中的情况不再重复点击）。
    task_keyword：按任务名选中（如「修罗任务」），None 取第一个子任务。
    """
    rows, bgr, _img, panel = _read_list(win)
    if rows is None or bgr is None or panel is None:
        return None, "未定位到任务栏面板"
    group = find_group_row(rows, keyword)
    if group is None:
        return None, f"任务列表中未找到「{keyword}」分组"
    child = first_child_row(rows, group, task_keyword)
    if child is None:
        return None, (f"「{group['text']}」分组下未找到子任务"
                      + (f"「{task_keyword}」" if task_keyword else ""))
    if row_selected(child["row"]):
        log(f"{prefix}子任务「{child['text']}」已选中，跳过点击", "SYS")
        return child, f"「{child['text']}」已选中"
    l, t, r, b = child["rect"]
    cx, cy = (l + r) // 2, (t + b) // 2
    human_click(win, cx, cy)
    log(f"{prefix}已选中子任务「{child['text']}」 ({cx},{cy})", "SYS")
    return child, f"已选中「{child['text']}」"


def read_detail_lines(win):
    """OCR 任务详情栏，返回 ([(txt, rect, score), ...], 描述)。

    区域由「任务详情」标题条模板动态定位（栏宽=模板宽），从标题条下沿到
    面板内容底边；面板拖动后仍适用。
    """
    img, bgr = _capture_bgr(win)
    if img is None:
        return None, "窗口截图失败"
    panel = locate_panel(bgr)
    if panel is None:
        return None, "未定位到任务栏面板"
    items, _ = game_map.ocr_region(img, panel["detail_region"])
    return items, "ok"


def objective_lines(items):
    """从任务详情 OCR 行中提取「任务目的」段落文本行。

    找到含「任务目的」的行作为起点（不含标题本身），向下收集到下一段
    标题（任务轮次 / 今日已完成 / 任务状态等）为止。段落标题未读出时
    返回标题之后的全部行（字段完整优先，调用方可结合 detail 判断）。
    """
    lines = [txt for txt, _rect, score in items
             if score >= TASK_PANEL_MIN_OCR_SCORE]
    start = None
    for i, txt in enumerate(lines):
        if "任务目的" in txt:
            start = i
            break
    if start is None:
        return lines
    out = []
    for txt in lines[start + 1:]:
        s = txt.strip()
        if not s:
            continue
        if any(s.startswith(h) or h in s[:8]
               for h in TASK_PANEL_SECTION_HEADS):
            break
        out.append(s)
    return out


def trim_hint_lines(items):
    """截掉「任务提示」段落及其后续行（用户 2026-11-26）。

    任务提示段是玩法说明（每日共享次数、混沌之气等），不参与任务识别，
    也不需要识别返回——读取结果从段标题起整段丢弃。段标题按前 8 字
    包含判定（容忍 OCR 冒号/前导噪声）；未读到时原样返回。
    """
    out = []
    for it in items:
        if TASK_PANEL_HINT_HEAD in (it[0] or "")[:8]:
            break
        out.append(it)
    return out


def _wait_detail(win, prefix=TAG):
    """等待任务详情渲染出内容（点击子任务后），返回 OCR 行。"""
    deadline = time.time() + TASK_PANEL_DETAIL_TIMEOUT
    items = []
    while time.time() < deadline:
        items, info = read_detail_lines(win)
        if items:
            return items
        if info != "ok":
            log(f"{prefix}读取任务详情失败: {info}", "WARN")
            return []
        time.sleep(TASK_PANEL_POLL_INTERVAL)
    return items


def read_first_group_task(win, keyword=TASK_PANEL_DEMON_KEYWORD, prefix=TAG,
                          task_keyword=None):
    """完整流程：打开任务栏 → 展开目标分组 → 选中子任务 → 读取任务目的。

    步骤：
    1. Alt+Q 确保任务栏打开（已打开不重复按，避免开关误关）；
    2. 在任务列表定位目标分组（默认「降魔任务」，修罗传「常规玩法」），
       可折叠时点击行首图标展开；
    3. 选中该分组下的子任务（默认第一个；task_keyword 指定任务名，如
       「修罗任务」——同一分组下可能还有山河名望等其他子任务）。行右缘
       已有 √ 选中标记则跳过点击；
    4. OCR 任务详情栏，识别「任务目的」段落；「任务提示」段整段截掉
       （玩法说明，不参与识别也不返回，用户 2026-11-26）；
    5. demon_task.recognize 解析任务类型与目标（击杀/显形镜/需重领），
       日志只打识别摘要一行（用户 2026-11-24：任务详情逐行/任务目的
       原文不再刷屏，识别结果已含地图/坐标/目标/轮次）。

    返回 dict：{'group', 'task', 'detail', 'objective', 'objective_text',
    'recognition'}；任一步失败返回 (None, 原因)。成功返回 (dict, 描述)。
    """
    ok, info = open_task_panel(win, prefix)
    if not ok:
        return None, f"打开任务栏失败: {info}"
    ok, info, _group = ensure_group_expanded(win, keyword, prefix)
    if not ok:
        return None, f"展开分组失败: {info}"
    child, info = select_first_child(win, keyword, prefix,
                                     task_keyword=task_keyword)
    if child is None:
        return None, f"选中子任务失败: {info}"
    detail = _wait_detail(win, prefix)
    if not detail:
        return None, "任务详情为空（未读取到任何文本）"
    detail = trim_hint_lines(detail)
    objective = objective_lines(detail)
    obj_text = "".join(objective)
    recognition = demon_task.recognize(child["text"], obj_text)
    # 日志只保留识别摘要（用户 2026-11-24）：任务详情逐行与任务目的原文
    # 不刷屏，识别结果已含地图/坐标/目标/轮次
    log(f"{prefix}任务识别：{demon_task.describe(recognition)}", "SYS")
    return {
        "group": _group["text"] if _group else "",
        "task": child["text"],
        "detail": detail,
        "objective": objective,
        "objective_text": obj_text,
        "recognition": recognition,
    }, f"已读取「{child['text']}」任务目的"
