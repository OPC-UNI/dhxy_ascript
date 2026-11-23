"""任务栏（Alt+Q 任务面板）操作：开关面板、展开分组、选中任务、读取任务目的。

五倍任务在任务栏中的分组名为「降魔任务」（同一玩法不同叫法，用户
2026-11-23 明确）；识别结果见 demon_task。

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

from ..core.app_utils import log, human_click
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
# 子任务行与分组行的最大中心距（行距约 19px，留一组噪声余量）
TASK_PANEL_CHILD_MAX_GAP = 40
# 目标分组关键词（「降魔任务」分组；标题栏任务名还会显示为
# 「三藏逢魔[降魔任务]」，此处只匹配任务列表里的分组行）
TASK_PANEL_DEMON_KEYWORD = "降魔"
# 任务详情中「任务目的」段落的结束标志（后续段落标题）
TASK_PANEL_SECTION_HEADS = (
    "任务轮次", "任务状态", "今日已完成", "当前状态", "任务奖励",
    "任务积分", "遣灵积分", "剩余时间", "你当前", "全队当前",
)
# 任务详情 OCR 行最低置信度（低于此值视为噪声：实测暗雷任务首行下方
# 出现过 0.548 的单字碎片「叁」）
TASK_PANEL_MIN_OCR_SCORE = 0.55
# 「当前任务」标签页：任务栏会记住上次标签（可能是日常活动/其他任务），
# 列表里找不到降魔分组时先切回当前任务页再重读。标签行在两块标题条
# 上方，纵向取标题条顶 -60 ~ -2px。
TASK_PANEL_TAB_TEXT = "当前任务"
TASK_PANEL_TAB_TOP_PAD = 60

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


def first_child_row(rows, group_row):
    """分组行的第一个子任务行；下一行为另一分组/距离过远时返回 None。"""
    try:
        idx = rows.index(group_row)
    except ValueError:
        return None
    if idx + 1 >= len(rows):
        return None
    nxt = rows[idx + 1]
    if nxt["cy"] - group_row["cy"] > TASK_PANEL_CHILD_MAX_GAP:
        return None
    if _is_group_line(nxt["text"]):
        return None
    txt, rect, score = nxt["items"][0]  # 最左块=任务名，排除右侧 [√] 标记
    if len(txt.strip()) < 2:
        return None
    return {"text": txt, "rect": rect, "row": nxt, "score": score}


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
    size = getattr(img, "size", None)
    if not size:
        return False
    fresh = locate_panel(bgr) or panel
    if fresh is None:
        return False
    l, t, _r, _b = fresh["list_header"]
    right = fresh["detail_header"][2]
    region = (max(0, l), max(0, t - TASK_PANEL_TAB_TOP_PAD),
              min(size[0], right), max(1, t - 2))
    if region[2] <= region[0] or region[3] <= region[1]:
        return False
    items, _ = game_map.ocr_region(img, region)
    hit = next((it for it in items
                if TASK_PANEL_TAB_TEXT in it[0]
                and it[2] >= TASK_PANEL_MIN_OCR_SCORE), None)
    if hit is None:
        log(f"{prefix}未读到「{TASK_PANEL_TAB_TEXT}」标签，无法切换标签页",
            "WARN")
        return False
    _txt, rect, _score = hit
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    human_click(win, cx, cy)
    log(f"{prefix}已点击「{TASK_PANEL_TAB_TEXT}」标签页 ({cx},{cy})，"
        f"等待列表刷新", "SYS")
    return True


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


def select_first_child(win, keyword=TASK_PANEL_DEMON_KEYWORD, prefix=TAG):
    """在已展开的分组下点击第一个子任务，返回 (子任务信息, 描述)。

    子任务信息：{'text': 任务名, 'rect': OCR 框, 'row': 视觉行}；失败返回
    (None, 描述)。点击任务名字框中心（行内选择，不会点到右侧 [√] 标记）。
    """
    rows, bgr, _img, panel = _read_list(win)
    if rows is None or bgr is None or panel is None:
        return None, "未定位到任务栏面板"
    group = find_group_row(rows, keyword)
    if group is None:
        return None, f"任务列表中未找到「{keyword}」分组"
    child = first_child_row(rows, group)
    if child is None:
        return None, f"「{group['text']}」分组下未找到子任务"
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


def read_first_group_task(win, keyword=TASK_PANEL_DEMON_KEYWORD, prefix=TAG):
    """完整流程：打开任务栏 → 展开目标分组 → 选中第一个子任务 → 读取任务目的。

    步骤：
    1. Alt+Q 确保任务栏打开（已打开不重复按，避免开关误关）；
    2. 在任务列表定位「降魔任务」分组（可折叠），必要时点击行首图标展开；
    3. 点击该分组下的第一个子任务；
    4. OCR 任务详情栏，识别「任务目的」段落并逐行打印（同时写日志）；
    5. demon_task.recognize 解析任务类型与目标（击杀/显形镜/需重领），
       识别摘要同步写日志。

    返回 dict：{'group', 'task', 'detail', 'objective', 'objective_text',
    'recognition'}；任一步失败返回 (None, 原因)。成功返回 (dict, 描述)。
    """
    ok, info = open_task_panel(win, prefix)
    if not ok:
        return None, f"打开任务栏失败: {info}"
    ok, info, _group = ensure_group_expanded(win, keyword, prefix)
    if not ok:
        return None, f"展开分组失败: {info}"
    child, info = select_first_child(win, keyword, prefix)
    if child is None:
        return None, f"选中子任务失败: {info}"
    detail = _wait_detail(win, prefix)
    if not detail:
        return None, "任务详情为空（未读取到任何文本）"
    objective = objective_lines(detail)
    obj_text = "".join(objective)
    recognition = demon_task.recognize(child["text"], obj_text)
    log(f"{prefix}任务详情（{len(detail)} 行）：", "SYS")
    for txt, _rect, score in detail:
        if score >= TASK_PANEL_MIN_OCR_SCORE:
            log(f"{prefix}  {txt}", "SYS")
    log(f"{prefix}任务目的：{obj_text}", "SYS")
    log(f"{prefix}任务识别：{demon_task.describe(recognition)}", "SYS")
    return {
        "group": _group["text"] if _group else "",
        "task": child["text"],
        "detail": detail,
        "objective": objective,
        "objective_text": obj_text,
        "recognition": recognition,
    }, f"已读取「{child['text']}」任务目的"
