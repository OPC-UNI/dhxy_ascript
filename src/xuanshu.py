"""玄枢轶闻任务自动化。

流程：
1. 任务追踪中已有 [玄枢轶闻] 任务 → 循环跟踪任务直到完成
   （点击绿色带下划线文案，等待任务信息变化后继续点击；
   期间处理对话框绿色选项与锄遗聚宝小游戏）
2. 没有任务时，寻路到任务位置（洛阳城 93,125）并领取任务：
   - 位置识别失败时按 Alt+C 后重试一次（常见于人物未落地）
   - 已在任务位置 5px 内 → 跳过寻路
   - 在洛阳城其他位置 → 打开小地图点击任务点寻路
   - 不在洛阳城 → 世界地图 → 洛阳城 → 小地图 → 点击任务点寻路
   - 寻路完成判定：到达目标坐标附近；或坐标连续两次不变且出现领取按钮
     （距目标过近点击任务点无响应时的兜底）
   - 寻路前/后清理地图与物品栏，到达后点击领取任务按钮
   - 对话框 1 点击绿色选项「玄枢轶闻」，对话框 2 点击绿色选项「领取玄枢轶闻」
   - 校验任务追踪中出现玄枢轶闻任务
3. 任务完成后（追踪中消失）继续返回领取下一轮（run_loop 主循环）；
   达到设置的次数上限或领取时出现「请明天再来」文案（每日次数已满）时结束
"""
import difflib
import time

import cv2
import numpy as np

from .app_utils import log, human_click
from . import bag  # 物品栏开关
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle  # 战斗状态判定（统一在 battle.py 中实现）
from .control_window import load_task_counts  # 任务次数上限（通用设置）

# 任务追踪面板中的任务标题
TASK_TITLE = "玄枢轶闻"

# ---- 寻路与领取任务（洛阳城 93,125） ----
LUOYANG_CITY = "洛阳城"
TASK_MAP_POS = (93, 125)            # 任务位置（洛阳城坐标；None 表示目标非坐标）
MAP_POS_TOLERANCE = 5               # 坐标判定容差（±5 像素）
MAP_CLICK_POS = (399, 442)          # 小地图上任务位置对应的点击点（窗口相对坐标）
TASK_LINGQU_ICON = "task_lingqu.png"           # 领取任务按钮模板
ARRIVE_TIMEOUT = 90                 # 等待寻路完成的超时（秒）
# 领取任务对话框中的绿色选项（两步）
DIALOG_OPTION_XUANSHU = "玄枢轶闻"
DIALOG_OPTION_LINGQU = "领取玄枢轶闻"
DIALOG_TIMEOUT = 10                 # 等待对话框出现的超时（秒）
# 任务过程中出现的对话框绿色选项（跟踪期间检测到则点击，按顺序）
DIALOG_OPTIONS = ["注入玄蕴试试看"]
# 战斗失败对话框（出现则继续点击任务追踪的绿色文案推进任务）
DIALOG_LOSE_TEXT = "你输了"
# 每日任务次数已满的对话文案（出现即结束任务循环）
DAILY_LIMIT_TEXT = "少侠已经完成5次任务，请明天再来吧"
DAILY_LIMIT_INFO = "今日任务次数已满（请明天再来）"
# 需要点击屏幕中间跳过的对话框文案（出现则连点屏幕中间推进对话）
DIALOG_SKIP_TEXTS = ["你是做嘛的？莫来惹老子！"
                     ,"受害者所闻那水鬼之声出自善口技者之音，你幼时曾学过口技，又时常与人"
                     ,"第二起案子中，被打捞的尸体手脚被渔人结牢牢捆住，那渔人结并非常人所"
                     ,"少侠运气不错"
                     ,"翠花说的难道是这块石头？但是."
                     ,"刚刚是你再说话？"
                     ,"准备好接受地理方面的考验了么"
                     ,"少侠准备好接受考验了么"
                     ,"少侠准备好接受常识类学问的考验了么"
                     ]
DIALOG_SKIP_CLICKS = 4               # 跳过对话的点击次数
SCREEN_CENTER_POS = (689, 415)       # 屏幕中间（游戏窗口中心，点击推进对话）
# 特定对话框出现时点击的绿色选项（对话框文案 → 绿字选项）
DIALOG_OPTION_MAP = {
    "什么江中劫货？休要血口喷人，你有证据吗？": "指明口技之事",
    "哼，这天底下会口技的人多了去了，你怎么就能断定犯事的是我？": "指明绳结之事",
    "那、那又怎么样，这码头上做活的人那么多，你凭什么说一定是我？": "指明伤口一事",
    "少侠有什么事吗？": "玄枢轶闻",
    "太阳的别称是什么":"金乌",
    "浑天仪":"观测星星的位置",
    "大唐的都城":"长安",
    "大唐的丝绸":"丝绸之路",
    "大唐人最喜欢喝什么":"茶",
    "大唐人见面行礼":"拱手",
    "有人要偷我们的宝贝，拿下他！":"什么情况",
    "使劲晃动杯中的三颗骰子":"轻轻摇晃",
    "我倒数三个数后大家一起出拳":"出石头",
    "看打！":"看我怎么收拾你",
}
# 选项无法被 OCR 识别（如单字选项「茶」）时，按序号点击：
# 选项为问题文案下方的纵向列表，第 idx 项（对话框文案 → 序号）
DIALOG_OPTION_INDEX_MAP = {
    "大唐人最喜欢喝什么": 3,  # 茶（咖啡、果汁之后的第 3 项）
}
# 序号点击的坐标参数（相对问题行 rect）：首项纵偏移、行间距、横向偏移
# （实测三选项文字中心：咖啡 +12、果汁 +30、茶 +48，间距 18px）
OPTION_FIRST_OFFSET = 10
OPTION_ROW_STEP = 18
OPTION_X_OFFSET = 1
# 锄遗聚宝小游戏（点击「注入玄蕴试试看」后出现）
CHUYIJUEBAO_ICON = "chuyijuebao.png"              # 主图（消失即结束；实测匹配 0.91）"
CHUYIJUEBAO_START_ICON = "chuyijuebao_start.png"  # 开始按钮（实测匹配 0.92）
CHUYIJUEBAO_CHECK_INTERVAL = 0.1                 # 杂气检测间隔（秒）
CHUYIJUEBAO_TIMEOUT = 60                        # 小游戏总超时（秒）
# 红色杂气检测参数（游戏说明：点击红色杂气将其清除）
QI_RED_MIN = 120        # R 通道最低亮度
QI_RED_DOMINANCE = 30   # R 须高出 G/B 的差值
QI_REGION = (250, 115, 1150, 700)  # 世界区域（排除任务面板、顶部红色说明文字、底部 HUD）
QI_AREA_MIN = 250       # 团块最小面积（过滤红色文字等小噪点）
QI_AREA_MAX = 2500      # 团块最大面积
QI_CLICK_DEBOUNCE = 2.0  # 同一位置重复点击的最短间隔（秒）
# 循环跟踪任务参数
FOLLOW_TIMEOUT = 300                # 跟踪任务总超时（秒）
FOLLOW_POLL_INTERVAL = 2.0          # 任务信息变化轮询间隔（秒）


def _at_target(pos):
    """判断位置是否在任务目标附近（目标非坐标时恒 False）。"""
    if TASK_MAP_POS is None:
        return False
    return game_map.is_at_position(pos, LUOYANG_CITY,
                                   TASK_MAP_POS[0], TASK_MAP_POS[1],
                                   MAP_POS_TOLERANCE)


def _close_scene_panels(win):
    """关闭场景中可能打开的界面：小地图、世界地图、物品栏。"""
    game_map.close_maps(win)
    if bag.is_bag_open(win):
        ok, info = bag.close_bag(win)
        log(f"[玄枢轶闻] 物品栏{info}", "SYS" if ok else "WARN")


def wait_arrive(win, timeout=ARRIVE_TIMEOUT):
    """等待寻路完成。

    目标为坐标（TASK_MAP_POS 非空）：到达目标 5px 内即完成；
    通用兜底（含目标非坐标）：坐标连续两次不变且出现领取按钮即完成
    （距目标过近时点击任务点游戏可能不响应寻路，靠此兜底推进领取）；
    位置 20 秒无变化且无领取按钮时提前失败（避免长时间无响应）。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    last = None
    unchanged = 0
    polls = 0
    while time.time() < deadline:
        polls += 1
        if is_in_battle(win):
            # 战斗中：坐标显示不可用，跳过本轮识别（不计入稳定性）
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if TASK_MAP_POS is not None:
            if game_map.is_at_position(pos, LUOYANG_CITY,
                                       TASK_MAP_POS[0], TASK_MAP_POS[1],
                                       MAP_POS_TOLERANCE):
                return True, f"已到达目标附近 {LUOYANG_CITY}[{TASK_MAP_POS[0]},{TASK_MAP_POS[1]}]"
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 2 and game_map.find_template(win, TASK_LINGQU_ICON) is not None:
            return True, "位置稳定且出现领取任务按钮"
        if unchanged >= 10:
            return False, "位置 20 秒无变化且未出现领取按钮，寻路无响应"
        if polls % 5 == 0:
            log(f"[玄枢轶闻] 寻路等待中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"等待寻路完成超时（{timeout}s）"


def _click_green_option_once(win, items, img_np, text):
    """单帧检测并点击指定文案的绿色选项；点击成功返回 True。"""
    for txt, rect, score in items:
        if text in txt and score >= 0.6 and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[玄枢轶闻] 已点击对话框绿色选项「{txt}」@({cx},{cy})", "SYS")
            return True
    return False


def _click_dialog_option_by_index(win, items, dtxt, idx):
    """按序号点击问题下方的选项（选项无法 OCR 识别时使用）。

    定位问题文案所在行，选项在其下方纵向排列：
    第 idx 项的点击位置 = 问题行下方 10 + 18*(idx-1) 像素、问题行左侧 +1 像素
    （实测问答题三选项文字中心 咖啡 +12/果汁 +30/茶 +48，取略偏上避免点到行间）。
    """
    qrect = None
    for txt, rect, _ in items:
        if _fuzzy_contains(txt, dtxt):
            qrect = rect
            break
    if qrect is None:
        return False
    cx = qrect[0] + OPTION_X_OFFSET
    cy = qrect[3] + OPTION_FIRST_OFFSET + OPTION_ROW_STEP * (idx - 1)
    human_click(win, cx, cy)
    log(f"[玄枢轶闻] 已按序号点击第 {idx} 项选项 @({cx},{cy})", "SYS")
    return True


def _click_screen_center(win, times=DIALOG_SKIP_CLICKS):
    """点击屏幕中间若干次（推进 NPC 对话）。"""
    cx, cy = SCREEN_CENTER_POS
    for _ in range(times):
        human_click(win, cx, cy)
        time.sleep(0.5)  # 对话推进间隔
    log(f"[玄枢轶闻] 已点击屏幕中间 {times} 次（推进对话）", "SYS")


def _click_dialog_green_option(win, text, timeout=DIALOG_TIMEOUT, abort_text=None):
    """等待对话框出现，点击其中指定文案的绿色选项。

    等待期间若出现 abort_text 文案（如每日次数已满）则提前中止。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture()
        if img is None:
            time.sleep(1.0)
            continue
        items, img_np = game_map.ocr_image(img)
        if abort_text and _dialog_text_present(items, abort_text):
            return False, f"检测到「{abort_text}」"
        if _click_green_option_once(win, items, img_np, text):
            return True, f"已点击对话框绿色选项「{text}」"
        time.sleep(1.5)
    return False, f"未找到对话框绿色选项「{text}」"


def navigate_to_task(win):
    """寻路到玄枢轶闻任务位置（洛阳城 93,125）并领取任务。

    1. 场景前置：关闭已打开的小地图/世界地图/物品栏
    2. 识别当前位置（失败按 Alt+C 重试一次）
    3. 不在目标附近时寻路：在洛阳城 → 小地图点击任务点；不在 → 世界地图 → 洛阳城 → 小地图
    4. 等待寻路完成（到达目标附近；目标非坐标时位置稳定且出现领取按钮）
    5. 点击领取任务按钮 → 对话框 1 点「玄枢轶闻」→ 对话框 2 点「领取玄枢轶闻」
    6. 校验任务追踪中出现玄枢轶闻任务

    返回 (是否成功, 描述)。
    """
    # 战斗中：坐标与界面不可用，跳过本轮（由主循环稍后重试）
    if is_in_battle(win):
        return False, "当前战斗中，跳过本轮寻路"

    # 场景前置：清理残留地图（小地图在上层，先关）与物品栏
    _close_scene_panels(win)

    pos = game_map.read_position_with_recovery(win)
    if pos is None:
        return False, "无法识别当前地图位置"
    name, x, y = pos
    loc = f"{name}[{x},{y}]"

    if _at_target(pos):
        log(f"[玄枢轶闻] 已在任务位置附近（{loc}），跳过寻路", "SYS")
    elif name == LUOYANG_CITY:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
        log(f"[玄枢轶闻] 在洛阳城（{loc}），已打开小地图", "SYS")
    else:
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON, "洛阳城")
        if not ok:
            return False, info
        log(f"[玄枢轶闻] 不在洛阳城（{loc}），经世界地图前往洛阳城", "SYS")

    if not _at_target(pos):
        human_click(win, MAP_CLICK_POS[0], MAP_CLICK_POS[1])
        log(f"[玄枢轶闻] 已点击小地图任务点，自动寻路中", "SYS")
        time.sleep(1.0)  # 等待自动寻路启动
        # 清理：关闭仍打开的小地图（点击任务点后仍打开）、世界地图（通常已自动关闭）与物品栏
        _close_scene_panels(win)

    # 等待寻路完成
    ok, info = wait_arrive(win)
    if not ok:
        return False, info
    log(f"[玄枢轶闻] {info}", "SYS")

    # 领取任务
    pt = game_map.find_template(win, TASK_LINGQU_ICON)
    if pt is None:
        return False, "已到达但未找到领取任务按钮"
    human_click(win, pt[0], pt[1])
    log("[玄枢轶闻] 已点击领取任务", "SYS")

    # 对话框 1：点击绿色选项「玄枢轶闻」（每日次数已满时出现「请明天再来」文案）
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_XUANSHU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO) if DAILY_LIMIT_TEXT in info else (False, info)
    # 对话框 2：点击绿色选项「领取玄枢轶闻」
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_LINGQU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO) if DAILY_LIMIT_TEXT in info else (False, info)

    # 校验任务追踪中出现玄枢轶闻任务；期间检测每日次数已满文案
    # （满 5 次提示出现在点击「领取玄枢轶闻」之后）
    deadline = time.time() + DIALOG_TIMEOUT
    while time.time() < deadline:
        if task_tracker.has_task(win, TASK_TITLE):
            return True, "已领取任务"
        img = win.capture()
        if img is not None and _dialog_text_present(game_map.ocr_image(img)[0], DAILY_LIMIT_TEXT):
            return False, DAILY_LIMIT_INFO
        time.sleep(1.0)
    return False, "已点击领取玄枢轶闻，但任务追踪中未检测到任务"


def _find_red_qi_clusters(img_np):
    """检测世界区域内的红色杂气团块，返回 [(cx, cy), ...] 团块中心列表。

    基于游戏说明「点击红色杂气将其清除」：杂气为红色像素团块，
    用连通块分析过滤面积区间，对杂气动画与尺寸变化不敏感。
    """
    l, t, r, b = QI_REGION
    region = img_np[t:b, l:r]
    red = region[:, :, 0].astype(int)
    green = region[:, :, 1].astype(int)
    blue = region[:, :, 2].astype(int)
    mask = ((red > QI_RED_MIN) & (red > green + QI_RED_DOMINANCE)
            & (red > blue + QI_RED_DOMINANCE)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    centers = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if QI_AREA_MIN <= area <= QI_AREA_MAX:
            centers.append((l + x + w // 2, t + y + h // 2))
    return centers


def _play_chuyijuebao(win, timeout=CHUYIJUEBAO_TIMEOUT):
    """锄遗聚宝小游戏：点击开始（若开始按钮存在），循环点击消除红色杂气，
    主图消失即结束。

    开始后再次进入时无开始按钮（任务已在进行），直接进入消除循环。
    若背包或地图面板打开，先关闭（可能遮挡开始按钮与杂气）；
    循环中每 5 秒兜底检查一次。
    每 500ms 检测一次红色杂气团块（可能出现多个），逐个点击消除；
    同一位置 2 秒内不重复点击（避免误点静态红 UI 时刷屏）。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    # 场景前置：关闭可能遮挡的背包/小地图/世界地图
    _close_scene_panels(win)

    pt = game_map.find_template(win, CHUYIJUEBAO_START_ICON)
    if pt is not None:
        human_click(win, pt[0], pt[1])
        log("[玄枢轶闻] 已点击锄遗聚宝开始", "SYS")
        time.sleep(0.5)  # 等待开始动画

    clicked = 0
    last_click_at = {}
    iters = 0
    while time.time() < deadline:
        if game_map.find_template(win, CHUYIJUEBAO_ICON) is None:
            return True, f"锄遗聚宝结束（共消除 {clicked} 个）"
        iters += 1
        if iters % 10 == 0:
            # 每 5 秒兜底：关闭可能弹出的背包/地图面板
            _close_scene_panels(win)
        img = win.capture()
        if img is None:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        now = time.time()
        for cx, cy in _find_red_qi_clusters(np.asarray(img)):
            if now - last_click_at.get((cx, cy), 0) < QI_CLICK_DEBOUNCE:
                continue
            human_click(win, cx, cy)
            last_click_at[(cx, cy)] = now
            clicked += 1
            time.sleep(0.2)  # 点击后短暂等待，避免同一团块被重复命中
        time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
    return False, f"锄遗聚宝超时（{timeout}s，主图未消失）"


def _signatures_similar(a, b, ratio=0.8):
    """任务签名相似判定：OCR 抖动的细微差异视为同一任务状态。

    任务未真实变化时 OCR 可能产生 1~2 字符的抖动（如标点、单字误读），
    相似度达到 ratio 即视为相同，避免据此重复点击绿字。
    """
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def _fuzzy_contains(text, needle, threshold=0.85):
    """模糊子串匹配：text 中是否存在与 needle 相似度达标的窗口。

    对话框文案的 OCR 结果可能有个别字误读，精确匹配会漏掉；
    以长度 ±3 的滑动窗口做相似度比对（抗误读与多/少字）。
    """
    n = len(needle)
    if n == 0:
        return True
    if n > len(text):
        return difflib.SequenceMatcher(None, text, needle).ratio() >= threshold
    for size in (n - 3, n - 2, n - 1, n, n + 1, n + 2, n + 3):
        if size <= 0 or size > len(text):
            continue
        for i in range(0, len(text) - size + 1, max(1, size // 2)):
            if difflib.SequenceMatcher(None, text[i:i + size], needle).ratio() >= threshold:
                return True
    return False


def _dialog_text_present(items, dtxt):
    """对话框文案是否出现：逐行与全文拼接（无空格）做模糊匹配。"""
    joined = "".join(t[0] for t in items)
    return any(_fuzzy_contains(line, dtxt)
               for line in [t[0] for t in items] + [joined])


def follow_task(win, title=TASK_TITLE, timeout=FOLLOW_TIMEOUT, prefix="玄枢轶闻",
                extra_dialog_handler=None):
    """循环跟踪任务直到完成（prefix 供其他任务复用，如五环）。

    每轮：点击任务当前第一个绿色文案 → 等待任务信息变化（内容更新）
    → 进入下一轮；任务从追踪面板消失即完成。
    等待期间处理对话框（DIALOG_OPTION_MAP 指定绿字、DIALOG_SKIP_TEXTS
    点屏幕中间跳过、DIALOG_OPTIONS 已知绿字、战斗失败「你输了」点击任务绿字推进）
    与锄遗聚宝小游戏。
    战斗中跳过识别（任务追踪与坐标不可用），等待战斗结束。

    extra_dialog_handler：附加对话框处理回调 (win, items, img_np) → 是否已处理，
    供其他任务注入专属对话流程（如五环的提交装备）；回调中抛出的异常向上传播，
    供调用方做任务级终止。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    rounds = 0
    last_clicked_sig = None
    while time.time() < deadline:
        # 战斗中：任务追踪不可用（可能误判任务消失），等待战斗结束
        if is_in_battle(win):
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        task = task_tracker.get_task(win, title)
        if task is None:
            return True, f"任务已从任务追踪中消失（完成，共 {rounds} 轮）"
        sig = task_tracker.task_signature(task)
        if last_clicked_sig is not None and _signatures_similar(sig, last_clicked_sig):
            # 任务信息未真实变化（OCR 噪声抖动）→ 不重复点击，继续等待
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        ok, info = task_tracker.track_task(win, title)
        if not ok:
            return False, info
        last_clicked_sig = sig
        rounds += 1
        log(f"[{prefix}] 第 {rounds} 轮跟踪完成，等待任务信息变化", "SYS")

        # 等待任务信息变化或任务消失；期间处理对话框选项与锄遗聚宝小游戏
        changed_once = False
        while time.time() < deadline:
            time.sleep(FOLLOW_POLL_INTERVAL)
            img = win.capture()
            if img is None:
                continue
            items, img_np = game_map.ocr_image(img)
            # 附加对话框处理优先：战斗中也可能出现任务对话（如五环提交装备），
            # 此时任务追踪不可用，等战斗结束后再继续识别
            if extra_dialog_handler is not None:
                extra_dialog_handler(win, items, img_np)
            # 战斗中：跳过本轮识别（任务追踪与坐标不可用）
            if is_in_battle(win):
                continue
            # 锄遗聚宝优先：出现则完整处理（开始 + 消除 err 直到主图消失）
            if game_map.find_template(win, CHUYIJUEBAO_ICON) is not None:
                ok2, info2 = _play_chuyijuebao(win)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            task2 = task_tracker.find_task(task_tracker.group_tasks(items), title)
            if task2 is None:
                return True, f"任务已从任务追踪中消失（完成，共 {rounds} 轮）"
            if not _signatures_similar(task_tracker.task_signature(task2), sig):
                # 连续两次轮询确认签名确实变化（单次可能是 OCR 抖动，
                # 避免任务无变化时重复点击任务绿字）
                if changed_once:
                    break
                changed_once = True
                continue
            changed_once = False
            # 对话框处理：特定对话框指定绿字 → 点屏幕中间跳过 → 已知绿字选项
            # 文案匹配为模糊匹配（逐行与全文拼接，抗 OCR 个别字误读与换行拆分）
            handled = False
            for dtxt, opt in DIALOG_OPTION_MAP.items():
                if _dialog_text_present(items, dtxt):
                    if _click_green_option_once(win, items, img_np, opt):
                        handled = True
                        break
                    # 选项未被 OCR 识别（如单字选项）：按序号点击兜底
                    idx = DIALOG_OPTION_INDEX_MAP.get(dtxt)
                    if idx and _click_dialog_option_by_index(win, items, dtxt, idx):
                        handled = True
                        break
            if not handled and _dialog_text_present(items, DIALOG_LOSE_TEXT):
                # 战斗失败对话框：继续点击任务追踪的绿色文案推进任务
                ok3, info3 = task_tracker.track_task(win, title)
                if ok3:
                    log(f"[{prefix}] 检测到「你输了」对话，继续点击任务绿字推进", "SYS")
                    handled = True
                else:
                    log(f"[{prefix}] 「你输了」对话中任务绿字点击失败: {info3}", "WARN")
            if not handled:
                for dtxt in DIALOG_SKIP_TEXTS:
                    if _dialog_text_present(items, dtxt):
                        _click_screen_center(win)
                        handled = True
                        break
            if not handled:
                for opt in DIALOG_OPTIONS:
                    if _click_green_option_once(win, items, img_np, opt):
                        break
        else:
            return False, f"第 {rounds} 轮跟踪后任务信息无变化（超时）"
    return False, f"跟踪任务超时（{timeout}s）"


def run_cycle(win):
    """执行一轮玄枢轶闻检查：有任务则跟踪到完成，无任务则领取。

    领取成功后立即开始跟踪（不等轮次间隔，缩短领取后首次点击延迟）。

    Args:
        win: 游戏主窗口 Window 对象

    返回 (状态, 描述)；状态：done 完成一次任务 / daily_limit 每日次数已满 / fail 本轮失败
    """
    if is_in_battle(win):
        return "fail", "战斗中，跳过本轮（等待战斗结束）"

    if task_tracker.has_task(win, TASK_TITLE):
        ok, info = follow_task(win)
        return ("done", info) if ok else ("fail", info)

    # 没有任务 → 寻路到任务位置并领取任务
    ok, info = navigate_to_task(win)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔
    ok, info = follow_task(win)
    return ("done", info) if ok else ("fail", info)


def run_loop(win, cycle_interval=3.0):
    """玄枢轶闻主循环：跟踪完成的任务 → 回去继续领取 → 往复执行。

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取任务时出现「少侠已经完成5次任务，请明天再来吧」（每日次数已满）

    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(TASK_TITLE, 0)
    log(f"[玄枢轶闻] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    done = 0
    round_no = 0
    while True:
        round_no += 1
        log(f"[玄枢轶闻] ==== 第 {round_no} 轮 ====", "SYS")
        status, info = run_cycle(win)
        if status == "done":
            done += 1
            total = max_rounds if max_rounds > 0 else "∞"
            log(f"[玄枢轶闻] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[玄枢轶闻] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[玄枢轶闻] {info}", "WARN")
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
