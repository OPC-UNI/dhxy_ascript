"""组队功能操作（库模块，供任务模块调用）。

识别方式（用户提供，2026-10-06，模板见 res/img）：
1. 是否已组队：点击组队按钮 zudui_item.png → 菜单中出现「队伍信息」
   duiwuxinxi.png 即已组队，否则未组队
2. 是否队长：队伍面板打开后，面板中出现「解散队伍」jiesanduiwu.png 即队长
3. 退出队伍：队长面板点「解散队伍」jiesanduiwu.png；队员面板点
   「离开队伍」likaiduiwu.png
4. 队伍面板已打开判定：duiwumianban.png（阈值 0.6，匹配度随成员数变化）
   与 zudui_close.png（0.8）双条件印证
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
import re
import time

from ascript.windows.hardware.keyboard import Keyboard

from .app_utils import log, human_click, human_move
from . import map as game_map  # 找图与 OCR 能力复用

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
RACE_WORDS = ("男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫")

# 组队菜单区域与文案（OCR 判定菜单已打开：未组队菜单为
# 组队操作/创建队伍/附近队伍/组队平台，已组队菜单含队伍信息）
TEAM_MENU_REGION = (950, 640, 1150, 830)
TEAM_MENU_TEXTS = ("组队操作", "创建队伍", "附近队伍", "组队平台", "队伍信息")

# 模板匹配与等待参数
_TEMPLATE_THRESHOLD = 0.8
# 面板标志模板匹配度随队伍成员数变化（单人队实测 ~0.99，两人队降至 0.65），
# 面板打开判定放宽到 0.6 并与关闭按钮 zudui_close 互相印证（见 _is_panel_open）
_PANEL_FLAG_THRESHOLD = 0.6
_MENU_WAIT_TRIES = 3       # 菜单弹出等待（组队按钮点击后）
_MENU_WAIT_INTERVAL = 0.5
_PANEL_WAIT_TRIES = 6      # 面板打开等待（队伍信息点击后，面板为服务器数据，稍慢）
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
    """队伍面板是否已打开（双条件判定）。

    duiwumianban 面板标志的匹配度随成员数变化（两人队实测降至 0.65），
    故阈值放宽到 0.6；再与关闭按钮 zudui_close（实测 0.99+，面板专属）
    互相印证，避免放宽阈值引入误判。
    """
    if game_map.find_template(win, DUIWU_MIANBAN_ICON, _PANEL_FLAG_THRESHOLD) is None:
        return False
    return game_map.find_template(win, ZUDUI_CLOSE_ICON, _TEMPLATE_THRESHOLD) is not None


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
    """检查自己是否是队长：队伍面板打开后识别「解散队伍」按钮。

    流程：面板未打开则先打开（组队菜单 → 队伍信息），
    再在面板中查找 jiesanduiwu，出现即队长。检查后关闭面板。
    返回 (是否队长, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    is_leader = game_map.find_template(win, JIESAN_DUIWU_ICON, _TEMPLATE_THRESHOLD) is not None
    close_team_panel(win)
    if is_leader:
        return True, "是队长（面板可见「解散队伍」）"
    return False, "不是队长（面板无「解散队伍」）"


# ---- 队伍种族分析 ----

def analyze_team_members(win):
    """分析队伍成员：种族与转生等级配对识别。

    前置条件：队伍面板已打开（duiwumianban 模板可见）。
    OCR 面板区域 (300,260)-(1066,620)：种族文案限定 RACE_WORDS
    （竖排文字，位于选中成员头像旁），等级文案按
    未转102/1转120/2转120/3转120/飞升120 格式解析（位于成员列表）。
    配对：种族与等级分别按纵向顺序一一对应（面板布局实测两者不在同一行，
    但顺序一致）；数量不一致时按顺序配对并记 WARN。
    返回 (是否成功, [(种族, 等级文案或None), ...])；识别到 0 条视为失败。
    """
    if not _is_panel_open(win):
        return False, "队伍面板未打开"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    items, _ = game_map.ocr_region(img, RACE_REGION)
    race_items = []   # (种族词, rect)
    level_items = []  # ((转生段, 等级), rect)
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
    if not race_items:
        return False, "种族区域内未识别到种族文案"
    # 按位置排序：种族竖排文字（可能纵向堆叠或横向并列，按 上→下、左→右
    # 即成员顺序），等级列表（纵向堆叠，按 y）——两者顺序一致后一一配对
    race_items.sort(key=lambda it: (it[1][1], it[1][0]))
    level_items.sort(key=lambda it: it[1][1])
    # 有种族但无等级：面板数据可能尚未渲染完，等待后重截一次
    if not level_items:
        time.sleep(0.5)
        img = win.capture(force_activate=False)
        if img is not None:
            items, _ = game_map.ocr_region(img, RACE_REGION)
            for txt, rect, score in items:
                if score < _OCR_MIN_SCORE:
                    continue
                lv = _parse_level(txt)
                if lv is not None:
                    level_items.append((lv, rect))
            level_items.sort(key=lambda it: it[1][1])
    # 配对：种族与等级按纵向顺序一一对应
    members = []
    for i, (race, _) in enumerate(race_items):
        lv_txt = None
        if i < len(level_items):
            lv = level_items[i][0]
            lv_txt = f"{lv[0]}{lv[1]}"
        members.append((race, lv_txt))
    if len(level_items) != len(race_items):
        log(f"[组队] 种族({len(race_items)})与等级({len(level_items)})数量不一致，"
            f"按顺序配对，结果可能错位", "WARN")
    detail = "、".join(f"{r} {lv or '等级未识别'}" for r, lv in members)
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


def check_xiuluo_team(win, min_members=None):
    """修罗领取前队伍检查：一次打开队伍面板完成队长判定与人数检查。

    面板打不开（未组队/申请加入面板等）与非队长均返回 not_leader
    （修罗核心流程仅队长执行）。非队长不检查人数（修罗设置仅在队长生效）。
    min_members: 人数下限（含自己，按识别到的种族数计），None 不检查
    返回 (状态, 描述)；状态：ready 可领取 / wait 队长但人数不足
    / not_leader 非队长或队长无法判定。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return "not_leader", f"队长无法判定（{info}）"
    is_leader = game_map.find_template(win, JIESAN_DUIWU_ICON, _TEMPLATE_THRESHOLD) is not None
    if not is_leader:
        close_team_panel(win)
        return "not_leader", "非队长（面板无「解散队伍」）"
    ok, races = analyze_team_races(win)
    close_team_panel(win)
    if not ok:
        return "wait", f"种族识别失败: {races}"
    if min_members is not None and len(races) < min_members:
        log(f"[组队] 队伍不满足条件: 人数不足（{len(races)}/{min_members}）"
            f"（当前 {len(races)} 人: {'、'.join(races)}）", "WARN")
        return "wait", f"队伍人数不足（{len(races)}/{min_members}，当前: {'、'.join(races)}）"
    return "ready", f"队伍满足人数要求（{len(races)} 人: {'、'.join(races)}）"


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
        is_leader = game_map.find_template(win, JIESAN_DUIWU_ICON, _TEMPLATE_THRESHOLD) is not None
        log(f"[组队] 2.是否队长: {is_leader} - {'是队长' if is_leader else '不是队长'}"
            f"（面板内识别）", "SYS")
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

    流程：确保队伍面板打开（未组队时报错）→ 面板中查找 likaiduiwu
    并点击（点击即完成，无确认对话框）→ 关闭面板。
    面板中无离开按钮返回失败（通常表示是队长，应走 disband_team）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    pt = game_map.find_template(win, LIKAI_DUIWU_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        close_team_panel(win)
        return False, "面板中未找到「离开队伍」按钮（可能是队长）"
    human_click(win, pt[0], pt[1])
    log(f"[组队] 已点击离开队伍按钮 @ ({pt[0]},{pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 已离开队伍，面板可能已自动关闭；未关则补关
    return True, "已离开队伍"


def disband_team(win):
    """解散队伍（队长身份）：已组队时打开队伍面板，点击「解散队伍」。

    流程：确保队伍面板打开（未组队时报错）→ 面板中查找 jiesanduiwu
    并点击（点击即完成，无确认对话框）→ 关闭面板。
    面板中无解散按钮返回失败（通常表示不是队长）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    pt = game_map.find_template(win, JIESAN_DUIWU_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        close_team_panel(win)
        return False, "面板中未找到「解散队伍」按钮（可能不是队长）"
    human_click(win, pt[0], pt[1])
    log(f"[组队] 已点击解散队伍按钮 @ ({pt[0]},{pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
    return True, "已解散队伍"


def leave_or_disband_team(win):
    """按身份退出队伍：未组队不操作；队长解散；队员离开。

    单次面板流程（避免重复开关面板）：打开队伍面板 → 面板中同时
    查找「解散队伍」（队长）与「离开队伍」（队员），命中哪个点哪个。
    未组队时菜单无「队伍信息」，无法打开面板，直接返回成功不操作。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            if "未组队" in info:
                return True, f"未组队，无需操作：{info}"
            return False, info
    disband_pt = game_map.find_template(win, JIESAN_DUIWU_ICON, _TEMPLATE_THRESHOLD)
    if disband_pt is not None:
        human_click(win, disband_pt[0], disband_pt[1])
        log(f"[组队] 已点击解散队伍按钮 @ ({disband_pt[0]},{disband_pt[1]})", "SYS")
        time.sleep(0.5)
        close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
        return True, "已解散队伍"
    leave_pt = game_map.find_template(win, LIKAI_DUIWU_ICON, _TEMPLATE_THRESHOLD)
    if leave_pt is not None:
        human_click(win, leave_pt[0], leave_pt[1])
        log(f"[组队] 已点击离开队伍按钮 @ ({leave_pt[0]},{leave_pt[1]})", "SYS")
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
