"""组队功能操作（库模块，供任务模块调用）。

识别方式（用户提供，2026-10-06，模板见 res/img）：
1. 是否已组队：点击组队按钮 zudui_item.png → 菜单中出现「队伍信息」
   duiwuxinxi.png 即已组队，否则未组队
2. 是否队长：队伍面板打开后双模板比分数——「解散队伍」jiesanduiwu.png
   分数达标且高于「离开队伍」likaiduiwu.png 才算队长（两按钮样式相近，
   单模板会把离开按钮误判为解散按钮，实测误匹配 0.807~0.834 ≥ 0.8 阈值）
3. 退出队伍：队长面板点「解散队伍」jiesanduiwu.png；队员面板点
   「离开队伍」likaiduiwu.png
4. 队伍面板已打开判定：duiwumianban.png（0.6）为主判据；标志匹配随成员数
   退化（5 人队实测 0.44~0.68 抖动）不达标时，以面板专属文案
   （队标/暂时离队/招募队友）OCR 兜底。不用关闭按钮判定——多个面板
   共用同款关闭按钮，不具区分性
5. 队伍成员分析：面板打开后 OCR (300,260)-(1066,620) 区域，种族文案限定
   男人/女人/男鬼/女鬼/男仙/女仙/男魔/女魔/男巫/女巫，
   等级文案格式 未转102 / 1转120 / 2转120 / 3转120 / 飞升120，
   按行配对（种族与等级对应）；成员最多 5 个、最少 1 个
6. 关闭队伍面板：面板内关闭按钮 zudui_close.png，鼠标移过去左键点击
   （ESC 实测关不掉队伍面板，勿依赖）
7. 关闭组队菜单：菜单打开时再点一次 zudui_item.png（开关式）即消失

实机自检记录（2026-10-06）：组队判定/面板打开/种族识别通过；
首次队长检查因上一轮菜单未关、再点组队按钮把菜单关闭而失败，
故所有入口统一走「菜单已开则复用」的 _ensure_menu_open 流程。

待接入（需使用方提供识别方式）：
- handle_join_requests：申请列表界面与同意/拒绝按钮定位
- apply_to_team：世界场景中「申请队伍」招募玩家的识别
"""
import os
import pathlib
import re
import time

import cv2
import numpy as np
from PIL import Image
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages

from .app_utils import log, human_click, human_move
from . import map as game_map  # 找图与 OCR 能力复用
from . import debug_overlay  # 调试信息悬浮层（队伍信息行更新）

# ---- 模板（res/img，用户截图提供） ----
ZUDUI_ITEM_ICON = "zudui_item.png"        # 组队按钮（点击弹出组队菜单）
DUIWU_XINXI_ICON = "duiwuxinxi.png"       # 菜单「队伍信息」（已组队时菜单中出现）
JIESAN_DUIWU_ICON = "jiesanduiwu.png"     # 队伍面板「解散队伍」按钮（仅队长可见）
DUIWU_MIANBAN_ICON = "duiwumianban.png"   # 队伍面板标志（面板已打开判定）
ZUDUI_CLOSE_ICON = "zudui_close.png"      # 队伍面板关闭按钮（鼠标移上去左键点击）
ZUDUI_SHENQINGJIARU_ICON = "zudui_shenqingjiaru.png"  # 申请加入面板标志（队伍人数不足时
                                          # 点组队按钮可能打开该面板；检测到直接点关闭，不处理）
LIKAI_DUIWU_ICON = "likaiduiwu.png"       # 队伍面板「离开队伍」按钮（队员可见）

# ---- 队伍面板内种族 OCR ----
# 面板成员种族文案区域（窗口相对）；种族文案范围见 RACE_WORDS，
# 每个成员对应一条种族文案，故成员数 = 识别到的种族数（最多 5，最少 1）
RACE_REGION = (300, 260, 1066, 620)
RACE_WORDS = ("男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫",
               "男龙", "女龙")

# 组队菜单区域与文案（OCR 判定菜单已打开：未组队菜单为
# 组队操作/创建队伍/附近队伍/组队平台，已组队菜单含队伍信息）
TEAM_MENU_REGION = (950, 640, 1150, 830)
TEAM_MENU_TEXTS = ("组队操作", "创建队伍", "附近队伍", "组队平台", "队伍信息")

# 第 1 列种族文字固定区域（窗口相对坐标，实测多张截图一致）：
# 该列文字在「选中态」为金色、灰度对比度低，OCR 稳定读不到；
# 用固定坐标裁剪 + 颜色掩码提取字形，与字形库做模板匹配识别
RACE_GLYPH_RECT = (455, 302, 478, 348)

# 取消第 1 个成员选中的点击位置（窗口相对坐标）：
# 面板打开时默认选中第 1 个成员，其种族渲染为选中态（金色）读不到；
# 点击其他成员的位置会把选中移走，第 1 个成员恢复普通渲染（用户实测：
# 空白处点击不能取消选中，点成员位置可以；第 2 列名字行坐标 552,505）
DESELECT_CLICK_POS = (552, 505)
DESELECT_SETTLE = 0.5  # 点击后等待选中状态更新
# 字形匹配阈值（同字体不同颜色，掩码归一化后同种族实测 0.86+，
# 不同种族 ≤0.31，取 0.80 留余量且不会误判）
_RACE_GLYPH_MATCH = 0.80

# 模板匹配与等待参数
_TEMPLATE_THRESHOLD = 0.8
# 面板标志模板匹配度随队伍成员数变化（单人队实测 ~0.99，两人队降至 0.65，
# 5 人队实测 0.436~0.683 抖动）；判定不依赖关闭按钮——多个面板共用同款
# 关闭按钮（申请加入面板即同款），不具区分性。低于阈值时 OCR 文案兜底
_PANEL_FLAG_THRESHOLD = 0.6
# 面板专属文案（队伍面板内按钮/标签，其他面板与场景不出现；OCR 兜底判据）
_PANEL_TEXTS = ("队标", "暂时离队", "招募队友")
_MENU_WAIT_TRIES = 3       # 菜单弹出等待（组队按钮点击后）
_MENU_WAIT_INTERVAL = 0.5
_PANEL_WAIT_TRIES = 8      # 面板打开等待（队伍信息点击后，面板为服务器数据，稍慢）
_PANEL_WAIT_INTERVAL = 0.5
_OCR_MIN_SCORE = 0.55

# 转生等级文案：未转102 / 1转120 / 2转120 / 3转120 / 飞升120
# （容 OCR 噪声：木转=未转、飞异/飞昇=飞升、数字 12O/1 20 等）
_LEVEL_RE = re.compile(r"(未转|木转|[123]转|飞[升异昇])\s*([0-9OoIlSs]{2,3})")
_LEVEL_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "S": "5", "s": "5",
                                  "I": "1", "l": "1"})


# ---- 内部流程 ----

def _is_menu_open(img):
    """判定组队菜单是否已打开（队伍信息模板优先，菜单文案 OCR 兜底）。

    申请加入面板打开时不算菜单（其区域内的「附近队伍」等文案会被
    OCR 兜底误判成菜单，先排除）。
    """
    if game_map.find_template_in(img, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        return False
    if game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD) is not None:
        return True
    items, _ = game_map.ocr_region(img, TEAM_MENU_REGION)
    return any(any(mt in txt for mt in TEAM_MENU_TEXTS) for txt, _, _ in items)


def _close_apply_panel(win):
    """关闭申请加入面板：点击 zudui_close（与队伍面板同款关闭按钮）。

    面板不处理（暂不处理申请流程），关闭后让外层重试。
    返回 (是否关闭, 描述)。
    """
    pt = game_map.find_template(win, ZUDUI_CLOSE_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        _try_close_menu()
        return False, "申请加入面板未找到关闭按钮（zudui_close），已退回 ESC"
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    time.sleep(0.15)  # 悬停让按钮进入可点击状态（与队伍面板关闭同款交互）
    human_click(win, pt[0], pt[1])
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if game_map.find_template(win, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is None:
            return True, "申请加入面板已关闭"
    return False, "申请加入面板关闭失败"


def _wait_menu(win):
    """等待组队菜单弹出，返回 (是否打开, 最新截图)。"""
    for _ in range(_MENU_WAIT_TRIES):
        time.sleep(_MENU_WAIT_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        if _is_menu_open(img):
            return True, img
    return False, None


def _ensure_menu_open(win):
    """确保组队菜单已打开：已打开则复用，未打开才点击组队按钮。

    菜单未关闭时再点组队按钮会把它关掉（实机踩坑），故必须先检查再决定
    是否点击。队伍人数不足时点击组队按钮可能打开申请加入面板：
    面板已开/被点开都只关闭不处理，本轮返回未打开交给外层重试。
    返回 (是否打开, 最新截图)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, None
    # 申请加入面板开着会挡住组队菜单：直接关闭，本轮不做其他处理
    if game_map.find_template_in(img, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        ok, info = _close_apply_panel(win)
        log(f"[组队] {info}", "SYS" if ok else "WARN")
        return False, None
    if _is_menu_open(img):
        return True, img
    pt = game_map.find_template(win, ZUDUI_ITEM_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, None
    human_click(win, pt[0], pt[1])
    opened, img2 = _wait_menu(win)
    if opened:
        return True, img2
    # 菜单未打开：可能点开了申请加入面板（队伍人数不足时出现），
    # 检测到则直接点击关闭，不处理该面板
    if game_map.find_template(win, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        ok, info = _close_apply_panel(win)
        log(f"[组队] {info}", "SYS" if ok else "WARN")
    return False, None


def _has_duiwuxinxi(img):
    """菜单中是否出现「队伍信息」（模板优先，OCR 兜底）。"""
    if game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD) is not None:
        return True
    items, _ = game_map.ocr_region(img, TEAM_MENU_REGION)
    return any("队伍信息" in txt for txt, _, _ in items)


def _is_panel_open(win):
    """队伍面板是否已打开（面板专属信号判定）。

    不依赖关闭按钮——多个面板共用同款关闭按钮（申请加入面板即同款），
    不具区分性。判定只看面板专属信号：
    1. duiwumianban 面板标志（随成员数退化，5 人队实测 0.436~0.683
       抖动，阈值 0.6 时灵时不灵）
    2. 不达标时以面板专属文案（队标/暂时离队/招募队友）OCR 兜底——
       这些文案只在队伍面板内出现，申请加入面板与场景界面均无
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if _best_match(img, DUIWU_MIANBAN_ICON)[0] >= _PANEL_FLAG_THRESHOLD:
        return True
    items, _ = game_map.ocr_region(img, RACE_REGION)
    return any(any(t in txt for t in _PANEL_TEXTS) for txt, _, _ in items)


def _wait_panel(win):
    """等待队伍面板打开（duiwumianban 模板）。"""
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if _is_panel_open(win):
            return True
    return False


def _try_close_menu():
    """尽力关闭组队菜单（ESC；对面板无效，仅菜单场景兜底用）。"""
    try:
        Keyboard.key("esc")
        time.sleep(0.3)
    except Exception as e:
        log(f"[组队] ESC 关闭菜单失败: {e}", "WARN")


def _parse_level(txt):
    """解析转生等级文案，返回 (转生段, 等级数字) 或 None。

    转生段为 未转/1转/2转/3转/飞升；容 OCR 噪声（木转→未转、
    飞异→飞升、数字 12O→120）。
    """
    m = _LEVEL_RE.search(txt)
    if not m:
        return None
    stage = m.group(1)
    if stage == "木转":
        stage = "未转"
    elif stage.startswith("飞"):
        stage = "飞升"
    digits = m.group(2).translate(_LEVEL_DIGIT_MAP)
    if not digits.isdigit():
        return None
    return stage, int(digits)


def _best_match(img, icon):
    """帧内模板匹配全局最高分与其中心坐标（maxcnt=1 快速模式）。

    与 find_template_in 同源（FindImages/TM_CCOEFF_NORMED），返回
    (confidence, (x, y))；无匹配时返回 (0.0, (0, 0))（调用方先以
    confidence ≥ 阈值 过滤，无匹配时坐标不会被使用）。
    """
    return game_map.best_match_in(img, icon)


def _is_leader_in_frame(img):
    """帧内队长判定：解散队伍分数达标且高于离开队伍分数（双模板比分数）。

    解散队伍模板与离开队伍按钮存在弱误匹配（实测 0.807 ≥ 0.8 阈值，
    而离开按钮真实匹配 0.985），单模板判定会把队员误判为队长；
    比分数即可区分两者，无需重裁模板。
    """
    jiesan_score, _ = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, _ = _best_match(img, LIKAI_DUIWU_ICON)
    return jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score


def _parse_member_ocr(img):
    """帧内解析队伍面板种族与等级行，按 x 分列排序（y 抖动不参与排序）。

    返回 (race_items, level_items)：race_items = [(种族, rect)]，
    level_items = [((转生段, 等级), rect)]。
    """
    items, _ = game_map.ocr_region(img, RACE_REGION)
    race_items = []
    level_items = []
    for txt, rect, score in items:
        if score < _OCR_MIN_SCORE:
            continue
        for w in RACE_WORDS:
            if w in txt:
                race_items.append((w, rect))
                break
        else:
            lv = _parse_level(txt)
            if lv is not None:
                level_items.append((lv, rect))
    race_items.sort(key=lambda it: it[1][0])
    level_items.sort(key=lambda it: it[1][0])
    return race_items, level_items


# ---- 种族字形库（第 1 列金色种族文字的识别） ----
# 第 1 列种族文字为金色（队长标记色？），灰度对比度低、OCR 读不到；
# 但 5 列种族文字位置固定（列中心 ≈465/608/753/894/1038）且字体相同。
# 方案：可读列的 OCR 结果自动采集 12 个种族的字形样本入库（随挂机
# 积累），第 1 列按固定坐标裁剪 + 金色掩码提取字形，与库做形状匹配
# 识别（颜色归一化后与颜色无关）。库在配置目录，不入 git。
_GLYPH_SIZE = (23, 46)  # 与 RACE_GLYPH_RECT 尺寸一致


def _glyph_path():
    """字形库文件路径（配置目录，随挂机自动积累）。"""
    return pathlib.Path(os.getcwd()) / "team_race_glyphs.npz"


def _load_glyphs():
    """加载字形库 {种族: 二值数组}；文件不存在/损坏返回空。"""
    path = _glyph_path()
    if not path.exists():
        return {}
    try:
        data = np.load(path)
        return {str(k): data[k].astype(np.uint8) for k in data.files}
    except Exception:
        return {}


def _save_glyphs(glyphs):
    """保存字形库（写临时文件后原子替换）。"""
    path = _glyph_path()
    tmp = path.with_suffix(".tmp")
    try:
        np.savez_compressed(tmp, **glyphs)
        os.replace(tmp, path)
    except Exception:
        pass


def _extract_glyph(np_img, rect, gold=False):
    """从矩形提取二值字形（归一化到 _GLYPH_SIZE），笔画过少返回 None。

    gold=True：第 1 列金色文字掩码（R/G 高、B 低）；
    gold=False：可读列亮像素掩码（max>150）。
    """
    l, t, r, b = [int(v) for v in rect]
    seg = np_img[t:b, l:r].astype(int)
    if seg.size == 0:
        return None
    if gold:
        mask = ((seg[:, :, 0] > 160) & (seg[:, :, 1] > 140) & (seg[:, :, 2] < 170)
                & (seg[:, :, 0] > seg[:, :, 2] + 40) & (seg[:, :, 1] > seg[:, :, 2] + 30))
    else:
        mask = seg.max(axis=2) > 150
    if mask.sum() < 30:
        return None
    small = Image.fromarray((mask * 255).astype(np.uint8)).resize(
        _GLYPH_SIZE, Image.NEAREST)
    return np.asarray(small) > 0


def _match_glyph(np_img, glyphs):
    """第 1 列金色字形与库匹配，返回 (种族, 分数) 或 (None, 0.0)。"""
    if not glyphs:
        return None, 0.0
    query = _extract_glyph(np_img, RACE_GLYPH_RECT, gold=True)
    if query is None:
        return None, 0.0
    qf = query.astype(np.float32)
    best, best_score = None, 0.0
    for race, g in glyphs.items():
        gf = g.astype(np.float32)
        if gf.shape != qf.shape:
            continue
        score = float(cv2.matchTemplate(qf, gf, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best, best_score = race, score
    return best, best_score


def _deselect_first_member(win):
    """点击第 2 个成员位置，取消第 1 个成员的默认选中。

    第 1 个成员处于选中态时其种族文字为金色、OCR 读不到；
    点击其他成员位置把选中移走后恢复普通渲染（用户实测）。
    """
    human_click(win, DESELECT_CLICK_POS[0], DESELECT_CLICK_POS[1])
    time.sleep(DESELECT_SETTLE)


def _harvest_glyphs(img_np, race_items, glyphs):
    """从可读种族列采集字形样本入库（已有种族的样本跳过）。

    img_np：面板帧（RGB numpy）；返回库是否变化（需要落盘）。
    """
    changed = False
    for race, rect in race_items:
        if race in glyphs or race == "?":
            continue
        # OCR 框外扩 2px 再采集，防笔画贴边被裁
        l, t, r, b = rect
        g = _extract_glyph(img_np, (l - 2, t - 2, r + 2, b + 2))
        if g is None:
            continue
        glyphs[race] = g.astype(np.uint8)
        changed = True
    return changed


def _count_members_frame(win, img):
    """帧内统计队伍人数：优先按等级行计数，无等级行时退回种族数。

    种族行第 1 列经常读不到（实测，原因待确认），导致种族数少于
    实际人数；等级行稳定（实测 5 人识别 5 条）。
    等级行缺失（面板数据尚未渲染完）时重截一次。
    返回 (是否成功, 人数, 种族列表)。
    """
    race_items, level_items = _parse_member_ocr(img)
    if not level_items:
        time.sleep(0.5)
        img2 = win.capture(force_activate=False)
        if img2 is not None:
            race_items, level_items = _parse_member_ocr(img2)
    count = len(level_items) or len(race_items)
    if count == 0:
        return False, 0, []
    return True, count, [r for r, _ in race_items]


def _close_menu(win):
    """关闭组队菜单：菜单打开时再点一次组队按钮（开关式）即关闭。

    用户实测：菜单打开状态下点击 zudui_item 会让子菜单消失。
    关闭后校验菜单消失，失败时退回 ESC。返回 (是否关闭, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None or not _is_menu_open(img):
        return True, "组队菜单本就未打开"
    pt = game_map.find_template_in(img, ZUDUI_ITEM_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        _try_close_menu()
        return False, "未定位到组队按钮，已退回 ESC"
    human_click(win, pt[0], pt[1])
    for _ in range(_MENU_WAIT_TRIES):
        time.sleep(_MENU_WAIT_INTERVAL)
        img2 = win.capture(force_activate=False)
        if img2 is None or not _is_menu_open(img2):
            return True, "组队菜单已关闭"
    _try_close_menu()
    return False, "组队菜单关闭失败"


# ---- 面板开关 ----

def is_team_panel_open(win):
    """检查队伍面板是否已打开（duiwumianban 模板出现）。返回 (是否打开, 描述)。"""
    if _is_panel_open(win):
        return True, "队伍面板已打开"
    return False, "队伍面板未打开"


def open_team_panel(win):
    """打开队伍面板：确保组队菜单打开 → 点击菜单「队伍信息」。

    仅已组队时菜单才有「队伍信息」，未组队时无法打开面板。
    面板已打开时直接复用——实机踩坑：面板开着时点「队伍信息」会把它
    切换关闭（按钮开关式），必须先检查面板状态。
    打开后保持面板开启，由调用方 close_team_panel 关闭。
    返回 (是否成功, 描述)。
    """
    if _is_panel_open(win):
        return True, "队伍面板已打开（复用）"
    opened, img = _ensure_menu_open(win)
    if not opened:
        return False, "组队菜单未打开"
    if not _has_duiwuxinxi(img):
        _close_menu(win)
        return False, "未组队（菜单中无「队伍信息」）"
    pt = game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        _close_menu(win)
        return False, "菜单中未定位到「队伍信息」"
    human_click(win, pt[0], pt[1])
    if not _wait_panel(win):
        _try_close_menu()
        return False, "队伍面板未打开"
    return True, "队伍面板已打开"


def close_team_panel(win):
    """关闭队伍面板：鼠标移到面板关闭按钮 zudui_close 上，左键点击。

    点击后校验 duiwumianban 消失。返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        return True, "队伍面板本就未打开"
    pt = game_map.find_template(win, ZUDUI_CLOSE_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "未找到面板关闭按钮（zudui_close）"
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    time.sleep(0.15)  # 悬停让按钮进入可点击状态
    human_click(win, pt[0], pt[1])
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if not _is_panel_open(win):
            return True, "队伍面板已关闭"
    return False, "队伍面板关闭失败（duiwumianban 仍可见）"


# ---- 组队状态检查 ----

# 最近一次队伍身份检查结果（供战斗线程等读取，如抢盒子的队长规则）
_known_leader = None  # True=队长 / False=非队长 / None=未知（未检查过）


def record_leader_status(is_leader):
    """记录最近一次队伍身份检查结果（见 _known_leader）。"""
    global _known_leader
    _known_leader = is_leader


def known_leader():
    """最近一次队伍身份检查是否队长；未检查过返回 None。"""
    return _known_leader


def is_in_team(win):
    """检查是否已组队：打开组队菜单，出现「队伍信息」即已组队。

    菜单已打开时直接复用（不重复点击组队按钮）。
    返回 (是否在队伍中, 描述)。
    """
    opened, img = _ensure_menu_open(win)
    if not opened:
        return False, "组队菜单未打开（无法判定）"
    in_team = _has_duiwuxinxi(img)
    _close_menu(win)  # 再点一次组队按钮关闭菜单（开关式），未组队同样生效
    if in_team:
        return True, "已在队伍中"
    return False, "未组队（菜单中无「队伍信息」）"


def is_team_leader(win):
    """检查自己是否是队长：队伍面板打开后双模板比分数判定。

    流程：面板未打开则先打开（组队菜单 → 队伍信息），
    帧内比较解散队伍/离开队伍两个模板的匹配分（见 _is_leader_in_frame，
    单模板会把离开按钮误判为解散按钮）。检查后关闭面板。
    返回 (是否队长, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    is_leader = _is_leader_in_frame(img) if img is not None else False
    close_team_panel(win)
    if is_leader:
        return True, "是队长（面板可见「解散队伍」）"
    return False, "不是队长（面板无「解散队伍」）"


# ---- 队伍种族分析 ----

def analyze_team_members(win):
    """分析队伍成员：种族与转生等级按成员列（x 最近）配对识别。

    前置条件：队伍面板已打开（duiwumianban 模板可见）。
    OCR 面板区域 (300,260)-(1066,620)：种族文案限定 RACE_WORDS
    （竖排文字，位于成员头像旁），等级文案按
    未转102/1转120/2转120/3转120/飞升120 格式解析（位于成员列表）。
    配对：种族与等级都按 x 分列排序；等级多于种族时缺列按第 1 列
    （实测第 1 列成员的种族文字 OCR 稳定读不到，原因待确认）处理，
    其余列按序后移对齐，缺列补「?」成员占位；种族多于等级时记 WARN。
    返回 (是否成功, [(种族, 等级文案或None), ...])；识别到 0 条视为失败。
    """
    if not _is_panel_open(win):
        return False, "队伍面板未打开"
    # 取消第 1 个成员的默认选中（选中态种族文字为金色、OCR 读不到）
    _deselect_first_member(win)
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    race_items, level_items = _parse_member_ocr(img)
    if not race_items and not level_items:
        return False, "种族区域内未识别到成员信息"
    # 有种族但无等级：面板数据可能尚未渲染完，等待后重截一次
    if not level_items:
        time.sleep(0.5)
        img2 = win.capture(force_activate=False)
        if img2 is not None:
            _, level_items = _parse_member_ocr(img2)
    # 字形库：可读列样本自动入库（新种族采集）；第 1 列金色文字形状匹配识别
    img_np = np.asarray(img)
    glyphs = _load_glyphs()
    if _harvest_glyphs(img_np, race_items, glyphs):
        _save_glyphs(glyphs)
    # 配对：等级多于种族时，缺列按第 1 列处理——实测第 1 列成员的种族
    # 文字为金色、OCR 稳定读不到；用固定坐标裁剪 + 字形库形状匹配识别
    # （库未积累到该种族时回退「?」），其余列按 x 序一一后移对齐
    # （race[i] ↔ level[i+gap]）。种族多于等级（数据异常）才告警。
    gap = len(level_items) - len(race_items)
    gold_race = None
    if gap > 0:
        matched, score = _match_glyph(img_np, glyphs)
        if matched is not None and score >= _RACE_GLYPH_MATCH:
            gold_race = matched
            log(f"[组队] 第 1 列金色种族字形匹配: {matched}（{score:.2f}）", "INFO")
    members = []
    for j in range(max(0, gap)):
        lv = level_items[j][0]
        race = gold_race if gold_race is not None else "?"
        members.append((race, f"{lv[0]}{lv[1]}"))
    for i, (race, _rect) in enumerate(race_items):
        lv_txt = None
        j = i + max(0, gap)
        if j < len(level_items):
            lv = level_items[j][0]
            lv_txt = f"{lv[0]}{lv[1]}"
        members.append((race, lv_txt))
    if len(race_items) > len(level_items):
        log(f"[组队] 种族({len(race_items)})多于等级({len(level_items)})，"
            f"部分等级缺失，结果可能错位", "WARN")
    return True, members


def analyze_team_races(win):
    """分析队伍内种族构成（见 analyze_team_members，仅取种族列表）。

    前置条件：队伍面板已打开。返回 (是否成功, 种族列表)。
    """
    ok, members = analyze_team_members(win)
    if not ok:
        return False, members
    return True, [m[0] for m in members]


def check_team_conditions(win, min_members=None, required_races=None):
    """检查队伍是否满足任务条件（如修罗任务的种族/人数要求）。

    流程：组队菜单 → 队伍信息 → 队伍面板 → 种族 OCR 统计 → 与条件比对，
    完成后关闭面板。
    min_members: 人数下限（含自己，按识别到的种族数计），None 不检查
    required_races: 必须包含的种族列表（每个都须在队伍中出现，如
    ["男鬼", "女魔"]），None 不检查
    返回 (是否满足, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    ok, races = analyze_team_races(win)
    close_team_panel(win)
    if not ok:
        return False, f"种族识别失败: {races}"
    problems = []
    if min_members is not None and len(races) < min_members:
        problems.append(f"人数不足（{len(races)}/{min_members}）")
    if required_races:
        missing = [r for r in required_races if r not in races]
        if missing:
            problems.append(f"缺少种族: {'、'.join(missing)}")
    if problems:
        log(f"[组队] 队伍不满足条件: {'；'.join(problems)}"
            f"（当前 {len(races)} 人: {'、'.join(races)}）", "WARN")
        return False, "队伍不满足条件：" + "；".join(problems)
    return True, f"队伍满足条件（{len(races)} 人: {'、'.join(races)}）"


def check_team_min(win, min_members=None):
    """领取前队伍检查（通用，修罗/五倍等复用）：一次打开队伍面板完成
    队长判定、人数检查与成员信息采集。

    面板打不开（未组队/申请加入面板等）与非队长均返回 not_leader。
    队长判定为双模板比分数（防解散/离开按钮互误匹配）；人数按等级行
    计数（种族行第 1 列读不到时也不漏）。
    min_members: 人数下限（含自己），None 不检查
    返回 (状态, 描述, 成员列表)；状态：ready 可领取 / wait 队长但人数
    不足 / not_leader 非队长或队长无法判定。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return "not_leader", f"队长无法判定（{info}）", []
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return "not_leader", "窗口截图失败", []
    if not _is_leader_in_frame(img):
        # 非队长同样在同一面板打开内采集成员信息（悬浮层展示用）
        ok2, members = analyze_team_members(win)
        close_team_panel(win)
        if not ok2:
            members = []
        record_leader_status(False)
        return "not_leader", "非队长（面板无「解散队伍」）", members
    record_leader_status(True)
    ok, count, _races = _count_members_frame(win, img)
    # 成员详情（同一面板打开内采集，不重复开关面板；内含取消第 1 列
    # 选中操作，种族识别结果以本次为准）
    ok2, members = analyze_team_members(win)
    close_team_panel(win)
    if not ok2:
        members = []
    if not ok:
        return "wait", "成员识别失败", members
    # 种族文案从成员分析结果取（_count_members_frame 用的是取消选中前
    # 的帧，选中态下第 1 列种族读不到会误报「未识别到种族」）
    race_txt = "、".join(r for r, _ in members if r and r != "?") or "未识别到种族"
    if min_members is not None and count < min_members:
        log(f"[组队] 队伍不满足条件: 人数不足（{count}/{min_members}）"
            f"（当前 {count} 人: {race_txt}）", "WARN")
        return "wait", f"队伍人数不足（{count}/{min_members}，当前: {race_txt}）", members
    return "ready", f"队伍满足人数要求（{count} 人: {race_txt}）", members


def update_overlay_team(status, info, members):
    """按队伍检查结果更新调试悬浮层「队伍」行（身份 + 人数/种族/等级）。

    修罗/五倍等任务模块每次 check_team_min 后调用；不为悬浮层单独
    开面板，检查的自然结果顺带展示。等级转生段省略为数字。
    """
    if status == "not_leader":
        leader_txt = ("非队长" if "非队长" in info
                      else "未组队" if "未组队" in info else "未知")
    else:
        leader_txt = "队长"
    parts = [f"队伍: {leader_txt}"]
    if members:
        items = []
        for race, lv in members:
            digits = re.sub(r"[^0-9]", "", lv or "")
            items.append(f"{race}{digits}")
        parts.append(f"{len(members)}人: {' '.join(items)}")
    debug_overlay.set_team(" | ".join(parts))


def check_xiuluo_team(win, min_members=None):
    """修罗领取前队伍检查（见 check_team_min，仅取状态与描述）。

    面板打不开（未组队/申请加入面板等）与非队长均返回 not_leader
    （修罗核心流程仅队长执行）。非队长不检查人数（修罗设置仅在队长生效）。
    min_members: 人数下限（含自己，按识别到的种族数计），None 不检查
    返回 (状态, 描述)；状态：ready 可领取 / wait 队长但人数不足
    / not_leader 非队长或队长无法判定。
    """
    status, info, _members = check_team_min(win, min_members)
    return status, info


def self_test(win):
    """实机自检：组队判定 → 面板内队长检查 → 成员分析（种族+等级）→ 关闭面板。

    最小界面流程：组队菜单开一次、队伍面板开一次（未组队时仅菜单）。
    所有检查在同一次面板打开内完成，避免重复开关面板。
    供实机验证使用（临时挂在某任务模式分支下），验证通过后移除调用即可。
    """
    log("[组队] ========== 实机自检开始 ==========", "SYS")
    # open_team_panel 未组队时报错（菜单无「队伍信息」），即组队判定结果
    ok, info = open_team_panel(win)
    log(f"[组队] 1.是否组队+打开面板: {ok} - {info}", "SYS")
    if ok:
        img = win.capture(force_activate=False)
        is_leader = _is_leader_in_frame(img) if img is not None else False
        log(f"[组队] 2.是否队长: {is_leader} - {'是队长' if is_leader else '不是队长'}"
            f"（双模板比分数识别）", "SYS")
        ok, members = analyze_team_members(win)
        if ok:
            detail = "、".join(f"{r} {lv or '等级未识别'}" for r, lv in members)
            log(f"[组队] 3.成员分析: True - {len(members)} 人: {detail}", "SYS")
        else:
            log(f"[组队] 3.成员分析: {ok} - {members}", "SYS")
        ok, info = close_team_panel(win)
        log(f"[组队] 4.关闭面板: {ok} - {info}", "SYS")
    log("[组队] ========== 实机自检结束 ==========", "SYS")
    return "实机自检完成"


# ---- 待接入识别方式的操作 ----

def leave_team(win):
    """离开队伍（队员身份）：已组队时打开队伍面板，点击「离开队伍」。

    流程：确保队伍面板打开（未组队时报错）→ 帧内双模板比分数，
    离开分数达标且高于解散分数才点击（防两按钮互误匹配）→ 关闭面板。
    面板中无离开按钮返回失败（通常表示是队长，应走 disband_team）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    likai_score, likai_pt = _best_match(img, LIKAI_DUIWU_ICON)
    jiesan_score, _ = _best_match(img, JIESAN_DUIWU_ICON)
    if not (likai_score >= _TEMPLATE_THRESHOLD and likai_score > jiesan_score):
        close_team_panel(win)
        return False, "面板中未找到「离开队伍」按钮（可能是队长）"
    human_click(win, likai_pt[0], likai_pt[1])
    log(f"[组队] 已点击离开队伍按钮 @ ({likai_pt[0]},{likai_pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 已离开队伍，面板可能已自动关闭；未关则补关
    return True, "已离开队伍"


def disband_team(win):
    """解散队伍（队长身份）：已组队时打开队伍面板，点击「解散队伍」。

    流程：确保队伍面板打开（未组队时报错）→ 帧内双模板比分数，
    解散分数达标且高于离开分数才点击（防两按钮互误匹配）→ 关闭面板。
    面板中无解散按钮返回失败（通常表示不是队长）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    jiesan_score, jiesan_pt = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, _ = _best_match(img, LIKAI_DUIWU_ICON)
    if not (jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score):
        close_team_panel(win)
        return False, "面板中未找到「解散队伍」按钮（可能不是队长）"
    human_click(win, jiesan_pt[0], jiesan_pt[1])
    log(f"[组队] 已点击解散队伍按钮 @ ({jiesan_pt[0]},{jiesan_pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
    return True, "已解散队伍"


def leave_or_disband_team(win):
    """按身份退出队伍：未组队不操作；队长解散；队员离开。

    单次面板流程（避免重复开关面板）：打开队伍面板 → 帧内双模板
    比分数（解散/离开按钮会互误匹配，取分数高且达标者）→ 点击。
    未组队时菜单无「队伍信息」，无法打开面板，直接返回成功不操作。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            if "未组队" in info:
                return True, f"未组队，无需操作：{info}"
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    jiesan_score, jiesan_pt = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, likai_pt = _best_match(img, LIKAI_DUIWU_ICON)
    if jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score:
        human_click(win, jiesan_pt[0], jiesan_pt[1])
        log(f"[组队] 已点击解散队伍按钮 @ ({jiesan_pt[0]},{jiesan_pt[1]})", "SYS")
        time.sleep(0.5)
        close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
        return True, "已解散队伍"
    if likai_score >= _TEMPLATE_THRESHOLD and likai_score > jiesan_score:
        human_click(win, likai_pt[0], likai_pt[1])
        log(f"[组队] 已点击离开队伍按钮 @ ({likai_pt[0]},{likai_pt[1]})", "SYS")
        time.sleep(0.5)
        close_team_panel(win)
        return True, "已离开队伍"
    close_team_panel(win)
    return False, "队伍面板中既无「解散队伍」也无「离开队伍」按钮"


def handle_join_requests(win, action="allow"):
    """处理入队申请：允许或拒绝。

    action: "allow" 允许 / "reject" 拒绝。
    TODO 待接入：申请列表界面识别与同意/拒绝按钮定位。
    返回 (是否成功, 描述)。
    """
    if action not in ("allow", "reject"):
        return False, f"未知处理方式「{action}」，可选: allow / reject"
    log(f"[组队] 处理入队申请({action})：申请列表界面识别待接入", "WARN")
    return False, "申请列表识别待接入"


def apply_to_team(win):
    """申请入队：点击「申请队伍」状态的玩家。

    机制：鼠标移到申请队伍（招募中）的玩家身上，左键点击即申请入队。
    TODO 待接入：世界场景中招募玩家的识别（名牌/旗帜标记）。
    返回 (是否成功, 描述)。
    """
    log("[组队] 申请入队：招募玩家识别待接入", "WARN")
    return False, "招募玩家识别待接入"
