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
import os
import time
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np
from ascript.windows.hardware.mouse import Mouse
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import log, human_click
from . import bag  # 物品栏开关
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
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
# 战斗失败/平局对话框（出现任一文案则继续点击任务追踪的绿色文案推进任务）
DIALOG_LOSE_TEXTS = ["你输了", "平局"]
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
                     ,"少侠可是来替小乔试装的？"
                     ,"看少侠的表情，旺小乔不满意？"
                     ,"少侠的脸色有点难看"
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
    "不知道这里有没有那条锦鲤鱼": "先钓一下再说！",
    "这就要少侠自己尝试了":"试试",
    "少侠习惯就好":"试试",
    "少侠想换什么款式":"试试",
    "略略略略略":"都别吵啦",
    "要么要钱，要么要命，你自己选吧":"看打"
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
CHUYIJUEBAO_CHECK_INTERVAL = 0.2                 # 杂气检测间隔（秒）
CHUYIJUEBAO_TIMEOUT = 60                        # 小游戏总超时（秒）
CHUYIJUEBAO_DEBUG = False                       # 调试开关：True 时不点击鼠标，
                                                # 只标注检测结果截图供人工核对
CHUYIJUEBAO_DEBUG_DIR = "qi_debug"              # 标注截图输出目录（相对程序工作目录）
# 红色杂气检测参数（游戏说明：点击红色杂气将其清除）
QI_RED_MIN = 120        # R 通道最低亮度
QI_RED_DOMINANCE = 30   # R 须高出 G/B 的差值
QI_REGION = (250, 115, 1150, 700)  # 世界区域（排除任务面板、顶部红色说明文字、底部 HUD）
QI_AREA_MIN = 250       # 团块最小面积（过滤红色文字等小噪点）
QI_AREA_MAX = 2500      # 团块最大面积
QI_CLICK_DEBOUNCE = 2.0  # 同一位置重复点击的最短间隔（秒）
# 循环跟踪任务参数
FOLLOW_TIMEOUT = 300                # 跟踪任务总超时（秒）
FOLLOW_POLL_INTERVAL = 0.5          # 任务信息变化轮询间隔（秒）；配合帧复用，
                                    # 静止帧轮询几乎零成本，动画帧周期由 OCR 主导
CONFIRM_POLL_INTERVAL = 0.2         # 变化确认轮询间隔（秒）；OCR 本身约 1.2s，
                                    # 确认轮询只需极短间隔留出渲染稳定余量
# 任务消失确认（过滤 OCR 偶发漏读/战斗遮挡导致的误判完成——五环实测
# 误判「任务消失」导致完成次数虚增、任务提前结束）：任务从追踪面板
# 消失后连续若干轮询未见、且期间无战斗遮挡面板，才判定任务完成。
# 战斗中不计入确认窗口（战斗画面遮挡追踪面板，OCR 必然读不到任务）。
TASK_GONE_CONFIRM_TIMEOUT = 6.0    # 确认窗口总时长（秒）
TASK_GONE_CONFIRM_POLLS = 2        # 连续未见的轮询次数（面板区域 OCR 约 0.9s）
TASK_GONE_POLL_INTERVAL = 1.0      # 确认轮询间隔（秒）
# 空闲重试：任务信息长时间无变化时重试点击任务绿字。点击可能在战斗
# 切换/寻路启动等过渡期被游戏吞掉，此时任务状态不变化、不会触发下一次
# 点击，任务会一直卡到跟踪超时；限频重试替代旧逻辑的高频噪声式重复
# 点击（旧逻辑因 OCR 行序抖动每 ~3s 点击一次）
IDLE_RECLICK_INTERVAL = 15.0
# 跟踪轮询的 OCR 区域见 task_tracker（TRACK_REGION/DIALOG_REGION/OCR_REGIONS）
FOLLOW_TRACK_REGION = task_tracker.TRACK_REGION
FOLLOW_DIALOG_REGION = task_tracker.DIALOG_REGION
FOLLOW_OCR_REGIONS = task_tracker.OCR_REGIONS


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
    """单帧检测并点击指定文案的绿色选项；点击成功返回 True。

    限定对话框区域（x≥PANEL_MAX_X）：任务追踪面板的绿字链接可能包含
    相同文案（如链接「去找小胖丫帮旺小乔试试服装」含「试试」），
    面板行 y 序在前会被先遍历到，不限区域会误点面板链接。
    """
    for txt, rect, score in items:
        if text in txt and score >= 0.55 and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
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
        time.sleep(0.3)  # 对话推进间隔（漏推进由下一轮询 OCR 自愈）
    log(f"[玄枢轶闻] 已点击屏幕中间 {times} 次（推进对话）", "SYS")


def _click_dialog_green_option(win, text, timeout=DIALOG_TIMEOUT, abort_text=None):
    """等待对话框出现，点击其中指定文案的绿色选项。

    等待期间若出现 abort_text 文案（如每日次数已满）则提前中止。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        items, img_np = game_map.ocr_regions(img, task_tracker.OCR_REGIONS)
        if abort_text and _dialog_text_present(items, abort_text):
            return False, f"检测到「{abort_text}」"
        if _click_green_option_once(win, items, img_np, text):
            return True, f"已点击对话框绿色选项「{text}」"
        time.sleep(0.5)
    return False, f"未找到对话框绿色选项「{text}」"


def navigate_to_task(win):
    """寻路到玄枢轶闻任务位置（洛阳城 93,125）并领取任务。

    1. 场景前置：关闭已打开的小地图/世界地图/物品栏
    2. 识别当前位置（失败按 Alt+C 重试一次）
    3. 不在目标附近时寻路：在洛阳城 → 小地图点击任务点；不在 → 世界地图 → 洛阳城 → 小地图
    4. 等待寻路完成（到达目标附近；目标非坐标时位置稳定且出现领取按钮）
    5. 点击领取任务按钮 → 对话框 1 点「玄枢轶闻」→ 对话框 2 点「领取玄枢轶闻」
    6. 校验任务追踪中出现玄枢轶闻任务

    返回 (是否成功, 描述, 校验命中时的 (items, img_np) 或 None)——成功时
    附带领取校验最后轮询的 OCR 结果，供 follow_task 复用省一次截图+OCR。
    """
    # 战斗中：坐标与界面不可用，跳过本轮（由主循环稍后重试）
    if is_in_battle(win):
        return False, "当前战斗中，跳过本轮寻路", None

    # 场景前置：清理残留地图（小地图在上层，先关）与物品栏
    _close_scene_panels(win)

    pos = game_map.read_position_with_recovery(win)
    if pos is None:
        return False, "无法识别当前地图位置", None
    name, x, y = pos
    loc = f"{name}[{x},{y}]"

    if _at_target(pos):
        log(f"[玄枢轶闻] 已在任务位置附近（{loc}），跳过寻路", "SYS")
    elif name == LUOYANG_CITY:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info, None
        log(f"[玄枢轶闻] 在洛阳城（{loc}），已打开小地图", "SYS")
    else:
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON, "洛阳城")
        if not ok:
            return False, info, None
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
        return False, info, None
    log(f"[玄枢轶闻] {info}", "SYS")

    # 领取任务
    pt = game_map.find_template(win, TASK_LINGQU_ICON)
    if pt is None:
        return False, "已到达但未找到领取任务按钮", None
    human_click(win, pt[0], pt[1])
    log("[玄枢轶闻] 已点击领取任务", "SYS")

    # 对话框 1：点击绿色选项「玄枢轶闻」（每日次数已满时出现「请明天再来」文案）
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_XUANSHU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO, None) if DAILY_LIMIT_TEXT in info \
            else (False, info, None)
    # 对话框 2：点击绿色选项「领取玄枢轶闻」
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_LINGQU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO, None) if DAILY_LIMIT_TEXT in info \
            else (False, info, None)

    # 校验任务追踪中出现玄枢轶闻任务；期间检测每日次数已满文案
    # （满 5 次提示出现在点击「领取玄枢轶闻」之后）。
    # 面板优先：只 OCR 追踪面板区域（约 0.9s）等任务出现；任务未出现时
    # 再补查对话框区域检测每日次数已满文案（旧逻辑每轮两次全窗 OCR 约 5.4s）。
    # 命中时把 OCR 结果带回，供 follow_task 复用，省一次截图+OCR
    deadline = time.time() + DIALOG_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            items, img_np = game_map.ocr_regions(img, (task_tracker.TRACK_REGION,))
            if task_tracker.has_task_in(items, img_np, TASK_TITLE):
                return True, "已领取任务", (items, img_np)
            items2, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _dialog_text_present(items2, DAILY_LIMIT_TEXT):
                return False, DAILY_LIMIT_INFO, None
        time.sleep(0.2)
    return False, "已点击领取玄枢轶闻，但任务追踪中未检测到任务", None


def _find_red_qi_clusters(region_np):
    """检测杂气区域（QI_REGION 裁剪，RGB）内的红色杂气团块。

    返回 [(cx, cy, x, y, w, h, area), ...]，坐标为窗口相对全图坐标。
    基于游戏说明「点击红色杂气将其清除」：杂气为红色像素团块，
    用连通块分析过滤面积区间，对杂气动画与尺寸变化不敏感。
    cx/cy 为团块中心，x/y/w/h 为团块包围盒，area 为团块掩码面积（像素数）。
    """
    red = region_np[:, :, 0].astype(int)
    green = region_np[:, :, 1].astype(int)
    blue = region_np[:, :, 2].astype(int)
    mask = ((red > QI_RED_MIN) & (red > green + QI_RED_DOMINANCE)
            & (red > blue + QI_RED_DOMINANCE)).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clusters = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if QI_AREA_MIN <= area <= QI_AREA_MAX:
            clusters.append((QI_REGION[0] + x + w // 2, QI_REGION[1] + y + h // 2,
                             QI_REGION[0] + x, QI_REGION[1] + y, w, h, int(area)))
    return clusters


def _mark_qi_debug(img_np, clusters, path):
    """把检测到的杂气团块画框标注后保存（调试用，img_np 为全窗图）。"""
    out = img_np.copy()
    for cx, cy, x, y, w, h, _area in clusters:
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.circle(out, (cx, cy), 3, (0, 0, 255), -1)
    cv2.imwrite(path, out)


def _capture_qi_region(win):
    """快速抓取杂气检测区域，返回 RGB numpy 数组（区域裁剪）或 None。

    win.capture 整窗 NUMPY 后裁剪：实测约 34ms，PrintWindow 抗窗口遮挡
    （ImageGrab 区域 BitBlt 约 49ms 且遮挡时拍到遮挡物，已弃用）。
    """
    l, t, r, b = QI_REGION
    full = win.capture(force_activate=False, format=WindowCaptureFormat.NUMPY)
    if full is None:
        return None
    return full[t:b, l:r]


def _play_chuyijuebao(win, timeout=CHUYIJUEBAO_TIMEOUT):
    """锄遗聚宝小游戏：点击开始（若开始按钮存在），循环点击消除红色杂气，
    主图消失即结束。

    开始后再次进入时无开始按钮（任务已在进行），直接进入消除循环。
    若背包或地图面板打开，先关闭（可能遮挡开始按钮与杂气）；
    循环中每 5 秒兜底检查一次。
    每帧只点一个杂气团块：点击后立即重新截帧定位——杂气持续漂移，
    批量点击会点到已过期的位置（实测杂气多时无法完成）；
    同一位置 2 秒内不重复点击（容 ±20px 漂移，避免误点静态红 UI 刷屏）。
    杂气点击用快速移动（跳过拟人化弧线，团块小且移动快）。
    结束判定（主图模板匹配需独立截图）每 1 秒摊销一次。
    CHUYIJUEBAO_DEBUG=True 时：不点击鼠标，仅标注检测结果并保存截图
    （每秒一张到 CHUYIJUEBAO_DEBUG_DIR），供人工核对识别准确性。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    # 场景前置：关闭可能遮挡的背包/小地图/世界地图
    _close_scene_panels(win)

    if not CHUYIJUEBAO_DEBUG:
        pt = game_map.find_template(win, CHUYIJUEBAO_START_ICON)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log("[玄枢轶闻] 已点击锄遗聚宝开始", "SYS")
            time.sleep(0.5)  # 等待开始动画

    clicked = 0
    last_click_at = {}
    last_icon_check = 0.0
    last_panel_check = 0.0
    last_debug_save = 0.0
    while time.time() < deadline:
        now = time.time()
        # 结束判定：主图消失即结束（模板匹配需独立截图，1 秒摊销一次）
        if now - last_icon_check >= 1.0:
            last_icon_check = now
            if game_map.find_template(win, CHUYIJUEBAO_ICON) is None:
                return True, f"锄遗聚宝结束（共消除 {clicked} 个）"
        # 兜底：关闭可能弹出的背包/地图面板（5 秒一次）
        if now - last_panel_check >= 5.0:
            last_panel_check = now
            _close_scene_panels(win)
        if CHUYIJUEBAO_DEBUG:
            # 调试模式：不点击鼠标，标注检测框并保存截图（每秒一张）
            if now - last_debug_save >= 1.0:
                last_debug_save = now
                full = win.capture(force_activate=False,
                                   format=WindowCaptureFormat.NUMPY)
                if full is not None:
                    l, t, r, b = QI_REGION
                    clusters = _find_red_qi_clusters(full[t:b, l:r])
                    os.makedirs(CHUYIJUEBAO_DEBUG_DIR, exist_ok=True)
                    path = os.path.join(CHUYIJUEBAO_DEBUG_DIR,
                                        f"qi_{time.strftime('%H%M%S')}.png")
                    _mark_qi_debug(full, clusters, path)
                    log(f"[玄枢轶闻] 调试: 检测到 {len(clusters)} 个杂气，已标注保存 {path}", "SYS")
                    log(f"[玄枢轶闻] 调试: 团块 "
                        f"{[(c[0], c[1], c[6]) for c in clusters]}（中心, 面积）", "INFO")
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        # 快速区域截图：屏幕 BitBlt 只抓杂气区域（比 PrintWindow 全窗
        # 快数倍），杂气移动快，点击时效优先
        region = _capture_qi_region(win)
        if region is None:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        clusters = _find_red_qi_clusters(region)
        if not clusters:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        # 每帧只点一个团块：跳过近期点过的位置（容漂移），点击后立即
        # 重新截帧，保证每次点击都基于最新画面
        tgt = None
        for c in clusters:
            if any(now - t < QI_CLICK_DEBOUNCE
                   and abs(c[0] - px) <= 20 and abs(c[1] - py) <= 20
                   for (px, py), t in last_click_at.items()):
                continue
            tgt = c
            break
        if tgt is None:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        # 快速点击：杂气团块小且移动快，跳过拟人化弧线移动
        Mouse.mouse_move(win.rect.left + tgt[0], win.rect.top + tgt[1],
                         move_duration=0.02)
        Mouse.click(button="primary")
        last_click_at[(tgt[0], tgt[1])] = now
        if len(last_click_at) > 60:
            last_click_at = {k: t for k, t in last_click_at.items()
                             if now - t < QI_CLICK_DEBOUNCE}
        clicked += 1
    return False, f"锄遗聚宝超时（{timeout}s，主图未消失）"


def _signatures_similar(a, b, ratio=0.8):
    """任务签名相似判定：OCR 抖动的细微差异视为同一任务状态。

    任务未真实变化时 OCR 可能产生 1~2 字符的抖动（如标点、单字误读），
    相似度达到 ratio 即视为相同，避免据此重复点击绿字。
    """
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


@lru_cache(maxsize=None)
def _char_counts(s):
    """字符串字符多重集（needle 静态、同一行跨轮询重复，缓存供上界预过滤）。"""
    return Counter(s)


def _fuzzy_contains(text, needle, threshold=0.85):
    """模糊子串匹配：text 中是否存在与 needle 相似度达标的窗口。

    对话框文案的 OCR 结果可能有个别字误读，精确匹配会漏掉；
    以长度 ±3 的滑动窗口做相似度比对（抗误读与多/少字）。
    预过滤均为 difflib 保证的上界（多重集重合度 / real_quick_ratio /
    quick_ratio），低于阈值才跳过，不改命中语义（实测 15 万随机对
    与旧实现 0 不一致）。
    """
    n = len(needle)
    if n == 0:
        return True
    # 多重集上界：窗口 ratio = 2*M/(size+n) ≤ 2*O/(min_size+n)，
    # M 为匹配字符数、O 为整行与 needle 的字符重合度；上界低于阈值整行跳过
    min_size = max(1, n - 3) if n <= len(text) else len(text)
    overlap = sum((_char_counts(text) & _char_counts(needle)).values())
    if 2 * overlap / (n + min_size) < threshold:
        return False
    if n > len(text):
        sm = difflib.SequenceMatcher(None, text, needle)
        return sm.real_quick_ratio() >= threshold and sm.ratio() >= threshold
    for size in (n - 3, n - 2, n - 1, n, n + 1, n + 2, n + 3):
        if size <= 0 or size > len(text):
            continue
        for i in range(0, len(text) - size + 1, max(1, size // 2)):
            window = text[i:i + size]
            sm = difflib.SequenceMatcher(None, window, needle)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            if sm.ratio() >= threshold:
                return True
    return False


def _frames_equal(a, b):
    """两帧像素完全一致（全分辨率逐像素比较，约 2-4ms）。

    仅全等才复用上一帧 OCR 结论；降采样比较会把微小变化抹平，
    导致缓慢浮现的对话框延迟检出，故不用。
    """
    if a is None or b is None or a.size != b.size:
        return False
    return bool((np.asarray(a) == np.asarray(b)).all())


def _dialog_text_present(items, dtxt):
    """对话框文案是否出现：逐行与全文拼接（无空格）做模糊匹配。"""
    joined = "".join(t[0] for t in items)
    return any(_fuzzy_contains(line, dtxt)
               for line in [t[0] for t in items] + [joined])


def _confirm_task_gone(win, title):
    """确认任务确实从追踪面板消失（连续轮询未见且无战斗遮挡）。

    OCR 偶发漏读（标题误读、面板瞬时遮挡）会误判任务消失——五环实测
    误判「任务消失」导致完成次数虚增、任务提前结束；战斗画面遮挡追踪
    面板时 OCR 必然读不到任务，战斗中不计入确认窗口（延长窗口等战斗
    结束后再观察）。返回 True=确认消失（任务完成）。
    """
    deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
    gone_polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(TASK_GONE_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            # 战斗画面遮挡追踪面板：本帧不作判定，延长确认窗口
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(2.0)
            continue
        items, img_np = game_map.ocr_regions(img, (FOLLOW_TRACK_REGION,))
        if task_tracker.find_task(task_tracker.group_tasks(items), title) is not None:
            return False  # 任务重新出现：此前为漏读，继续跟踪
        gone_polls += 1
        if gone_polls >= TASK_GONE_CONFIRM_POLLS:
            return True
        time.sleep(TASK_GONE_POLL_INTERVAL)
    return True  # 确认窗口内始终未见任务：视为完成


def follow_task(win, title=TASK_TITLE, timeout=FOLLOW_TIMEOUT, prefix="玄枢轶闻",
                extra_dialog_handler=None, green_order=None, progress_key=None,
                green_picker=None, fallback_keywords=None, first_items=None,
                idle_reclick_interval=None, signature_key=None):
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
    green_order：多个可点击绿字须按序点击时的顺序关键词列表
    （如 青龙→白虎→朱雀→玄武），传给 track_task；None 时点第一个绿字。
    progress_key：进度判定回调 (items, img_np) → str。任务推进可能只改变
    绿字颜色而文字不变（如四象封印完成关键词变红），文字签名检测不到；
    该回调计算进度键，键变化即视为任务推进（点下一个绿字）。
    None 时仅按文字签名判定。基线在点击前捕获（点击后可能立刻完成，
    首个轮询才建基线会漏检）。
    green_picker：精确点击回调 (items, img_np) → (x, y) 或 None，
    透传给 track_task（同一行内多个绿字须按序点击时使用）。
    fallback_keywords：锚点内容采集失败时的兜底关键词列表（如五环子目标
    「替身/武痴/盗号贼/食婴鬼」），透传给 track_task 在追踪面板区域内
    直接按关键词匹配绿字行点击。
    first_items：调用方刚 OCR 的 (items, img_np)（如领取校验刚确认任务
    出现），首轮跳过截图+OCR，省约 1.2s。
    idle_reclick_interval：任务信息长时间无变化时重试点击任务绿字的
    限频间隔（秒）；None 不重试（默认——玄枢轶闻/客栈/修罗的跟踪节奏
    正常无需重试，五环实测点击可能被过渡期吞掉故启用）。
    signature_key：任务文本签名回调 (items, img_np) → str，替代默认的
    标题+首行签名参与相似度比较（五环链接行被 OCR 拆成链接/计数两个
    碎片行且输出顺序抖动，默认签名反复翻转导致重复点击；五环改用
    行序无关的排序拼接签名）。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    rounds = 0
    last_clicked_sig = None
    progressed = False
    last_items = None

    def _sig_of(items, img_np, task):
        """当前帧的任务文本签名（signature_key 优先，默认标题+首行）。"""
        if signature_key is not None:
            return signature_key(items, img_np)
        return task_tracker.task_signature(task)
    while time.time() < deadline:
        # 战斗中：任务追踪不可用（可能误判任务消失），等待战斗结束
        if is_in_battle(win):
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 复用最近一次 OCR 结果（内层轮询确认推进时或领取校验命中时），
        # 省一次截图+OCR；该帧同样透传给 track_task 与进度键基线，
        # 点击路径上一共省掉约 3.5s 的重复 OCR
        if last_items is not None:
            items, img_np = last_items
            last_items = None
        elif first_items is not None:
            items, img_np = first_items
            first_items = None
        else:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(FOLLOW_POLL_INTERVAL)
                continue
            items, img_np = game_map.ocr_regions(img, FOLLOW_OCR_REGIONS)
        task = task_tracker.find_task(task_tracker.group_tasks(items), title)
        if task is None:
            if _confirm_task_gone(win, title):
                return True, f"任务已从任务追踪中消失（完成，共 {rounds} 轮）"
            continue  # 瞬时漏读：任务仍在，继续跟踪
        sig = _sig_of(items, img_np, task)
        if last_clicked_sig is not None and _signatures_similar(sig, last_clicked_sig) \
                and not progressed:
            # 任务信息未真实变化（OCR 噪声抖动）→ 不重复点击，继续等待
            # progressed=True 时（进度键已变化）允许继续点击下一个绿字
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 进度键基线：直接用当前帧（点击前捕获；点击后目标可能立刻完成
        # 变色，首个轮询才建基线会漏检该变化导致卡住）
        pre_key = progress_key(items, img_np) if progress_key is not None else None
        ok, info = task_tracker.track_task(win, title, green_order=green_order,
                                           green_picker=green_picker,
                                           fallback_keywords=fallback_keywords,
                                           pre_items=(items, img_np))
        if not ok:
            return False, info
        last_clicked_sig = sig
        rounds += 1
        progressed = False
        log(f"[{prefix}] 第 {rounds} 轮跟踪完成，等待任务信息变化", "SYS")

        # 等待任务信息变化或任务消失；期间处理对话框选项与锄遗聚宝小游戏
        changed_once = False
        changed_kind = None    # 首次变化来自哪个信号："text"/"key"——
                               # 确认轮询须验证同一信号（旧逻辑文本/进度键
                               # 各自单次抖动可组合骗过确认，导致重复点击）
        changed_sig = None     # 首次变化时的文本签名：确认轮询的新签名
                               # 须与其相似（过滤 A→B→A 的 OCR 抖动）
        changed_key = None     # 首次变化时的进度键：确认轮询的键须与其
                               # 相等（过滤单轮询键抖动）
        baseline_key = pre_key
        first_poll = pre_key is None
        last_poll_img = None
        last_poll_items = None
        last_click_at = time.time()  # 最近一次点击任务绿字时间（空闲重试限频）
        while time.time() < deadline:
            time.sleep(CONFIRM_POLL_INTERVAL if changed_once else FOLLOW_POLL_INTERVAL)
            img = win.capture(force_activate=False)
            if img is None:
                continue
            if changed_once:
                # 确认轮询：只复查追踪面板区域的任务签名/进度键（区域更小
                # OCR 更快），跳过对话框处理与战斗判定，最快确认变化。
                # 验证首次变化时的同一信号且新值稳定：文本变化须新签名与
                # 首次变化时相似；进度键变化须与首次变化时相等。单一信号
                # 的单次 OCR 抖动被过滤，两个信号各抖一次也无法组合骗过
                items, img_np = game_map.ocr_regions(img, (FOLLOW_TRACK_REGION,))
                task2 = task_tracker.find_task(task_tracker.group_tasks(items), title)
                if task2 is None:
                    if _confirm_task_gone(win, title):
                        return True, f"任务已从任务追踪中消失（完成，共 {rounds} 轮）"
                    changed_once = False  # 面板瞬时不可读：回退正常轮询
                    continue
                sig2 = _sig_of(items, img_np, task2)
                text_changed = not _signatures_similar(sig2, sig)
                key = progress_key(items, img_np) if progress_key is not None else None
                key_changed = key is not None and not first_poll \
                    and key != baseline_key
                if changed_kind == "text":
                    if text_changed and _signatures_similar(sig2, changed_sig):
                        log(f"[{prefix}] 变化确认: 任务签名「{sig}」→「{sig2}」", "INFO")
                        progressed = True
                        last_items = (items, img_np)
                        break
                    changed_once = False
                    continue
                if key_changed and key == changed_key:
                    log(f"[{prefix}] 变化确认: 进度键「{baseline_key}」→「{key}」", "INFO")
                    progressed = True
                    last_items = (items, img_np)
                    break
                changed_once = False  # 变化未持续（OCR 抖动），回退正常轮询
                continue
            # 帧复用：与上一轮询帧完全相同时复用其 OCR 结果（RapidOCR
            # 同输入输出确定，逐字等价；静止帧省一次双区域 OCR ~1.2s）
            if last_poll_items is not None and _frames_equal(img, last_poll_img):
                items, img_np = last_poll_items
            else:
                items, img_np = game_map.ocr_regions(img, FOLLOW_OCR_REGIONS)
                last_poll_items = (items, img_np)
                last_poll_img = img
            # 附加对话框处理优先：战斗中也可能出现任务对话（如五环提交装备），
            # 此时任务追踪不可用，等战斗结束后再继续识别
            if extra_dialog_handler is not None:
                extra_dialog_handler(win, items, img_np)
            # 战斗中：跳过本轮识别（任务追踪与坐标不可用）；复用本帧
            if is_in_battle_frame(img):
                continue
            # 锄遗聚宝优先：出现则完整处理（复用本帧模板匹配，不重复截窗）
            if game_map.find_template_in(img, CHUYIJUEBAO_ICON) is not None:
                ok2, info2 = _play_chuyijuebao(win)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            task2 = task_tracker.find_task(task_tracker.group_tasks(items), title)
            if task2 is None:
                if _confirm_task_gone(win, title):
                    return True, f"任务已从任务追踪中消失（完成，共 {rounds} 轮）"
                changed_once = False  # 瞬时漏读：任务仍在，继续等待
                continue
            sig2 = _sig_of(items, img_np, task2)
            text_changed = not _signatures_similar(sig2, sig)
            key = None
            key_changed = False
            if progress_key is not None:
                # 进度键：点击后首轮建立基线，之后与基线比较（文字不变
                # 仅颜色变化的任务推进，如封印完成绿字变红）
                key = progress_key(items, img_np)
                if first_poll:
                    baseline_key = key
                    first_poll = False
                else:
                    key_changed = key != baseline_key
            if text_changed or key_changed:
                # 连续两次轮询确认同一信号确实变化（单次可能是 OCR 抖动，
                # 避免任务无变化时重复点击任务绿字）
                if changed_once:
                    progressed = True
                    last_items = (items, img_np)
                    break
                changed_once = True
                # 进度键为精确匹配，双信号同时变化时优先信任
                changed_kind = "key" if key_changed else "text"
                changed_sig = sig2
                changed_key = key
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
            if not handled and any(_dialog_text_present(items, t)
                                   for t in DIALOG_LOSE_TEXTS):
                # 战斗失败/平局对话框：继续点击任务追踪的绿色文案推进任务
                # （复用本轮 OCR 结果，省一次截图+OCR）
                ok3, info3 = task_tracker.track_task(win, title, green_order=green_order,
                                                     green_picker=green_picker,
                                                     fallback_keywords=fallback_keywords,
                                                     pre_items=(items, img_np))
                if ok3:
                    last_click_at = time.time()  # 空闲重试限频基准
                    log(f"[{prefix}] 检测到「你输了/平局」对话，继续点击任务绿字推进", "SYS")
                    handled = True
                else:
                    log(f"[{prefix}] 「你输了/平局」对话中任务绿字点击失败: {info3}", "WARN")
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
            # 空闲重试（idle_reclick_interval 启用时）：任务信息长时间
            # 无变化时重试点击任务绿字（复用本轮 OCR 帧）。点击可能在
            # 战斗切换/寻路启动等过渡期被游戏吞掉，此时任务状态不变化、
            # 不会触发下一次点击，任务会一直卡到跟踪超时；限频重试替代
            # 旧逻辑的高频噪声式重复点击
            if idle_reclick_interval is not None \
                    and time.time() - last_click_at >= idle_reclick_interval:
                ok3, info3 = task_tracker.track_task(win, title, green_order=green_order,
                                                     green_picker=green_picker,
                                                     fallback_keywords=fallback_keywords,
                                                     pre_items=(items, img_np))
                if ok3:
                    last_click_at = time.time()
                    log(f"[{prefix}] 任务信息 {idle_reclick_interval:.0f}s 无变化，"
                        f"重试点击任务绿字", "SYS")
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
    ok, info, pre_items = navigate_to_task(win)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔；复用领取校验的 OCR 结果
    ok, info = follow_task(win, first_items=pre_items)
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
            debug_overlay.set_progress(f"{done}/{total}")
            log(f"[玄枢轶闻] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[玄枢轶闻] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[玄枢轶闻] {info}", "WARN")
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
