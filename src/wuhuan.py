"""五环任务自动化。

流程：
0. 任务追踪中已有「五环[五环]」任务（续跑）→ 跳过准备与领取，直接跟踪
1. 准备（任务开始一次）：打开背包检查是否已有项链，有则跳过购买；
   没有则确保在长安（不在则世界地图点击长安）
   → 长安小地图点击布店位置寻路，等待到达布店门口（长安 130,128 附近）
   → 进入服装店 → 点老板（牛记布店固定坐标 643,463）→ 对话框点「我想买点东西」
   → 匹配项链图标后鼠标移过去左键点击两次 → 点购买（购买 2 件 2 级装备）
2. 任务循环（次数按通用设置-任务次数）：
   - 到云游大师：长安小地图点击 (408,362) 寻路 → 等待到达长安 88,175 附近
     → 点击领取任务按钮 → 对话框点绿色选项「我可以帮助这些人」→ 任务出现在追踪面板
   - 点击五环任务追踪绿字 → 战斗（战斗线程自动处理）或提交装备
     （对话点「刚好我这里有，送你啦」→ 给与面板检索项链并点击选中
     → 点给与按钮完成；未找到则切换背包储物格再找，
     全部切换后仍未找到即终止任务）
     → 继续点击绿字 → 任务从追踪消失即完成一次
3. 完成后回到云游大师领取下一次；达到次数上限、领取时出现活力值不足
   对话框（点击「那可不行，我一会再来。」）或跟踪期间出现
   「恭喜你完成了今天五环任务」对话框（今日五环全部完成）即结束任务进程
"""
import re
import time

from ascript.windows.hardware.mouse import Mouse

from .app_utils import log, human_click, human_move
from . import bag  # 背包操作（储物格切换搜索）
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）
from .xuanshu import (follow_task, _click_dialog_green_option,
                      _dialog_text_present)  # 复用玄枢轶闻的任务跟踪/对话框逻辑

# 任务追踪面板中的任务标题
WUHUAN_TITLE = "五环"
# 五环子目标关键词：追踪链接行格式「去找{地图}(x, y){子目标}」（实测
# 「去找大唐境内(223, 21)武痴[1/5]」、「去找大唐边境(193, 144)惯骗[3/5]」），
# 链接行内容采集失败时按关键词兜底点击
WUHUAN_SUB_KEYWORDS = ["替身", "武痴", "惯骗","盗号贼", "食婴鬼"]

# ---- 长安与服装店（用户实测坐标/模板） ----
# 长安城世界地图图标见 map.py 的 WORLD_MAP_CHANGAN_ICON（城市图标统一在 map.py 维护）
CHANGAN_CITY = "长安"                              # 位置识别地名（模糊匹配）
CLOTH_SHOP_WALK_POS = (475, 443)                   # 长安小地图上布店位置的点击点（寻路到服装店门口）
CLOTH_SHOP_ENTER_POS = (750, 373)                  # 进入服装店的点击点
CLOTH_SHOP_TARGET_POS = (130, 128)                 # 布店门口地图坐标（长安 130,128）
YUNYOU_TARGET_POS = (88, 175)                      # 云游大师附近地图坐标（长安 88,175）
MAP_POS_TOLERANCE = 5                              # 到达判定容差（坐标 OCR 噪声）
SHOP_OWNER_POS = (643, 463)                         # 牛记布店老板坐标（进店后点击开始对话）
FUZHUANGDIAN_XIANGLIAN_ICON = "fuzhuangdian_xianglian.png"  # 服装店项链（21.png）
GOUMAI_ICON = "goumai.png"                          # 购买按钮（24.png）
BUY_DIALOG_OPTION = "我想买点东西"
EQUIPMENT_COUNT = 2                                 # 购买 2 件 2 级装备
YUNYOU_MAP_POS = (408, 362)                         # 小地图上云游大师位置的点击点
CLAIM_DIALOG_TEXT = "在我初入江湖时，有幸得到前辈大侠相助"  # 领取对话框文案
CLAIM_DIALOG_OPTION = "我可以帮助这些人"            # 领取对话框绿色选项
VITALITY_DIALOG_TEXT = "您的活力值在领取本次五环任务后将不足1800"  # 活力值不足对话框文案
VITALITY_DIALOG_OPTION = "那可不行，我一会再来。"    # 活力值不足时点击的选项（点击后结束任务）
VITALITY_LIMIT_INFO = "活力值不足（领取本次五环任务后将不足1800）"  # 结束状态描述
TASK_LINGQU_CHANGAN_ICON = "task_lingqu_changan.png"  # 长安云游大师的领取任务按钮（与洛阳城模板不同，实测 1.0）
# ---- 提交装备（给与）流程 ----
SUBMIT_DIALOG_TEXT = "我在这里练级，打了好久都打不过夜叉和猪怪。"  # 提交装备对话文案
SUBMIT_DIALOG_OPTION = "刚好我这里有，送你啦"              # 对话绿色选项
GIVE_ICON = "give.png"                              # 给与面板标志（27.png，32x213）
GIVE_BTN_ICON = "give_btn.png"                      # 给与按钮（28.png，OCR 读作「给子」）
XIANGLIAN_ICON = "fuzhuangdian_xianglian.png"       # 项链（与商店/给与面板行格式相同，实测给与面板匹配 1.0）
MISSING_EQUIPMENT_INFO = "缺少五环装备"              # 终止原因描述
# ---- 完成标记 ----
COMPLETE_DIALOG_TEXT = "恭喜你完成了今天五环任务"    # 今日五环全部完成的对话框文案
COMPLETE_INFO = "今日五环任务已完成"                 # 结束状态描述

# ---- 时序参数（秒） ----
CLOTH_SHOP_ARRIVE_TIMEOUT = 90  # 寻路到布店门口的超时
SHOP_ENTER_TIMEOUT = 15         # 进入服装店的超时（到达门口后）
CLAIM_ARRIVE_TIMEOUT = 90       # 寻路到云游大师附近的超时
CLAIM_BUTTON_TIMEOUT = 20       # 到达后等待领取任务按钮出现的超时
CLAIM_DIALOG_TIMEOUT = 15       # 领取对话框处理超时
GIVE_PANEL_TIMEOUT = 10         # 等待给与面板出现的超时


class MissingEquipment(Exception):
    """给与面板中未找到五环装备（切换全部储物格后）。"""


class DailyComplete(Exception):
    """检测到今日五环任务全部完成的恭喜对话框。"""


def _close_scene_panels(win):
    """关闭场景中可能打开的界面：小地图、世界地图、物品栏。"""
    game_map.close_maps(win)
    if bag.is_bag_open(win):
        ok, info = bag.close_bag(win)
        log(f"[五环] 物品栏{info}", "SYS" if ok else "WARN")


def _wait_template(win, icon, timeout):
    """等待模板出现（战斗中跳过等待）；返回是否出现。

    单帧判定：战斗状态与模板匹配共用一帧（原先每轮询截 2 次窗）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.5)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        if game_map.find_template_in(img, icon) is not None:
            return True
        time.sleep(1.5)
    return False


def _near_pos(pos, target):
    """判断位置是否在目标地图坐标附近（地名精确匹配，容差抗 OCR 噪声）。

    精确匹配避免误判相邻地图（如「长安城东」不是「长安」）。
    """
    if pos is None:
        return False
    return (pos[0].strip() == CHANGAN_CITY
            and abs(pos[1] - target[0]) <= MAP_POS_TOLERANCE
            and abs(pos[2] - target[1]) <= MAP_POS_TOLERANCE)


def _buy_equipments(win):
    """进入服装店购买 2 件 2 级装备（项链）。返回 (是否成功, 描述)。"""
    # 打开长安小地图点击布店位置寻路（走到服装店门口）
    if not game_map.is_in_map(win, CHANGAN_CITY):
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_CHANGAN_ICON, "长安")
        if not ok:
            return False, info
    if not game_map.is_mini_map_open(win):
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    human_click(win, CLOTH_SHOP_WALK_POS[0], CLOTH_SHOP_WALK_POS[1])
    log("[五环] 已点击小地图布店位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    _close_scene_panels(win)
    # 等待到达布店门口（长安 130,128 附近）后再进入，避免未到位就点击
    deadline = time.time() + CLOTH_SHOP_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(1.0)
            continue
        pos = game_map.read_map_position(win)
        if _near_pos(pos, CLOTH_SHOP_TARGET_POS):
            break
        polls += 1
        if polls % 5 == 0:
            log(f"[五环] 前往布店中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(1.0)
    else:
        return False, f"寻路到布店超时（未到达长安 {CLOTH_SHOP_TARGET_POS[0]},{CLOTH_SHOP_TARGET_POS[1]} 附近）"
    log("[五环] 已到达布店门口", "SYS")
    # 进入服装店：点入口 → 点老板坐标开始对话 → 对话框出现「我想买点东西」
    deadline = time.time() + SHOP_ENTER_TIMEOUT
    while time.time() < deadline:
        human_click(win, CLOTH_SHOP_ENTER_POS[0], CLOTH_SHOP_ENTER_POS[1])
        time.sleep(1.0)  # 等待进店
        human_click(win, SHOP_OWNER_POS[0], SHOP_OWNER_POS[1])
        log("[五环] 已点击服装店老板", "SYS")
        ok, info = _click_dialog_green_option(win, BUY_DIALOG_OPTION, timeout=4)
        if ok:
            break
    else:
        return False, "进入服装店后未出现购买对话框"
    time.sleep(0.3)
    # 匹配项链图标：鼠标移动过去后左键点击两次（购买 2 件）
    pt = game_map.find_template(win, FUZHUANGDIAN_XIANGLIAN_ICON)
    if pt is None:
        return False, "商店中未找到服装店项链"
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    for _ in range(EQUIPMENT_COUNT):
        Mouse.click(button="primary")
        time.sleep(0.4)  # 两次点击间隔，避免被识别为双击
    log(f"[五环] 已点击项链 {EQUIPMENT_COUNT} 次", "SYS")
    # 点购买完成
    pt = game_map.find_template(win, GOUMAI_ICON)
    if pt is None:
        return False, "未找到购买按钮"
    human_click(win, pt[0], pt[1])
    log("[五环] 已点击购买", "SYS")
    return True, f"已购买 {EQUIPMENT_COUNT} 件 2 级装备"


def _click_green_option(win, items, img_np, text):
    """点击指定文案的绿色选项（忽略标点差异的子串匹配），成功返回 True。

    OCR 对中文标点误读常见（如「，」读成「；」），去标点后按子串匹配。
    限定对话框区域（x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    """
    needle = re.sub(r"[^\w]", "", text)
    for txt, rect, score in items:
        if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[五环] 已点击绿色选项「{txt}」", "SYS")
            return True
    return False


def _click_wuhuan_option_once(win, items, img_np):
    """点击领取对话框中的绿色选项（含「五环」或「帮助」），成功返回 True。

    限定对话框区域（x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    """
    for txt, rect, score in items:
        if score >= 0.55 and ("五环" in txt or "帮助" in txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[五环] 已点击绿色选项「{txt}」", "SYS")
            return True
    return False


def _claim_task(win):
    """到云游大师领取五环任务。返回 (是否成功, 描述)。"""
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取"
    # 确保在长安：不在则世界地图点长安（打开长安小地图）
    if not game_map.is_in_map(win, CHANGAN_CITY):
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_CHANGAN_ICON, "长安")
        if not ok:
            return False, info
    # 打开长安小地图并点击云游大师位置寻路
    if not game_map.is_mini_map_open(win):
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    human_click(win, YUNYOU_MAP_POS[0], YUNYOU_MAP_POS[1])
    log("[五环] 已点击小地图云游大师位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    _close_scene_panels(win)
    # 等待到达云游大师附近（长安 88,175 附近）后再点击领取按钮，避免未到位就点击
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if _near_pos(pos, YUNYOU_TARGET_POS):
            break
        polls += 1
        if polls % 5 == 0:
            log(f"[五环] 前往云游大师中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    else:
        return False, f"寻路到云游大师超时（未到达长安 {YUNYOU_TARGET_POS[0]},{YUNYOU_TARGET_POS[1]} 附近）"
    log("[五环] 已到达云游大师附近", "SYS")
    # 等待领取任务按钮出现并点击
    if not _wait_template(win, TASK_LINGQU_CHANGAN_ICON, CLAIM_BUTTON_TIMEOUT):
        return False, "到达后未出现领取任务按钮"
    pt = game_map.find_template(win, TASK_LINGQU_CHANGAN_ICON)
    if pt is None:
        return False, "领取任务按钮出现后又消失，未点击"
    human_click(win, pt[0], pt[1])
    log("[五环] 已点击领取任务", "SYS")
    # 对话框：活力值不足则点击「那可不行，我一会再来。」结束任务；
    # 正常则点击「我可以帮助这些人」完成领取
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    claimed = False
    while time.time() < deadline:
        if task_tracker.has_task(win, WUHUAN_TITLE):
            claimed = True
            break
        img = win.capture()
        if img is None:
            time.sleep(0.2)
            continue
        items, img_np = game_map.ocr_image(img)
        if _click_green_option(win, items, img_np, CLAIM_DIALOG_OPTION):
            claimed = True
            # 活力值不足提示在点击领取选项之后出现：重新截帧并轮询几秒
            vdeadline = time.time() + 4
            while time.time() < vdeadline:
                img = win.capture()
                if img is None:
                    time.sleep(0.5)
                    continue
                items, img_np = game_map.ocr_image(img)
                if _dialog_text_present(items, VITALITY_DIALOG_TEXT):
                    if _click_green_option(win, items, img_np, VITALITY_DIALOG_OPTION):
                        log("[五环] 活力值不足，已点击「那可不行，我一会再来。」", "SYS")
                        return False, VITALITY_LIMIT_INFO
                time.sleep(0.8)
            break
        time.sleep(0.5)
    if not claimed:
        # 兜底：OCR 噪声导致指定文案未匹配时，点含「五环/帮助」的绿色选项
        deadline = time.time() + CLAIM_DIALOG_TIMEOUT
        while time.time() < deadline:
            if task_tracker.has_task(win, WUHUAN_TITLE):
                claimed = True
                break
            img = win.capture()
            if img is not None:
                items, img_np = game_map.ocr_image(img)
                _click_wuhuan_option_once(win, items, img_np)
            time.sleep(0.5)
    if not claimed:
        return False, "领取对话框处理失败"
    # 校验任务出现在追踪面板
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while not task_tracker.has_task(win, WUHUAN_TITLE) and time.time() < deadline:
        time.sleep(1.0)
    if not task_tracker.has_task(win, WUHUAN_TITLE):
        return False, "已点击领取选项，但任务追踪中未检测到任务"
    return True, "五环任务已领取"


def _prepare(win):
    """任务开始前准备：先检查背包是否已有项链，没有再去服装店购买。

    返回 (是否成功, 描述)。
    """
    # 打开背包检查项链（含储物格切换搜索）
    ok, info = bag.open_bag(win)
    if not ok:
        log(f"[五环] 打开背包失败: {info}，直接前往服装店购买", "WARN")
        return _buy_equipments(win)
    item, desc = bag.find_item_with_slots(win, XIANGLIAN_ICON, "项链")
    if item is not None:
        log(f"[五环] 背包中已有项链（{desc}，匹配度 {item.confidence:.2f}），跳过购买", "SYS")
        ok2, info2 = bag.close_bag(win)
        if not ok2:
            log(f"[五环] {info2}", "WARN")
        return True, "背包中已有装备，跳过购买"
    log(f"[五环] 背包中未找到项链（{desc}），前往服装店购买", "SYS")
    ok2, info2 = bag.close_bag(win)
    if not ok2:
        log(f"[五环] {info2}", "WARN")
    return _buy_equipments(win)


def _handle_submit_dialog(win, items, img_np):
    """处理五环提交装备流程（follow_task 的附加对话处理回调）。

    完成标记优先：对话「恭喜你完成了今天五环任务」出现 → 抛 DailyComplete
    结束任务循环（今日五环全部完成）。
    阶段一：对话「我在这里练级…」出现 → 点绿色选项「刚好我这里有，送你啦」；
    阶段二：给与面板打开 → 检索项链（含储物格切换）→ 点击选中 → 点给与按钮完成。
    给与面板打开时优先走阶段二（跨轮询续接）；
    全部储物格切换后仍未找到 → 抛 MissingEquipment 终止任务。
    返回是否已处理。
    """
    # 完成标记优先：每日五环全部完成出现恭喜对话框 → 抛异常结束任务循环
    if _dialog_text_present(items, COMPLETE_DIALOG_TEXT):
        log(f"[五环] 检测到「{COMPLETE_DIALOG_TEXT}」，今日五环任务已完成", "SYS")
        raise DailyComplete()
    # 阶段二优先：给与面板已打开（对话已处理过），继续完成给与
    if game_map.find_template(win, GIVE_ICON) is not None:
        return _do_give(win)
    # 阶段一：对话出现 → 点绿色选项
    if not _dialog_text_present(items, SUBMIT_DIALOG_TEXT):
        return False
    if not _click_green_option(win, items, img_np, SUBMIT_DIALOG_OPTION):
        return False  # 绿色选项尚未渲染，下次轮询再试
    log("[五环] 检测到提交装备对话，已点击「刚好我这里有，送你啦」", "SYS")
    # 等待给与面板出现
    if not _wait_template(win, GIVE_ICON, GIVE_PANEL_TIMEOUT):
        log("[五环] 给与面板未出现", "WARN")
        return True
    return _do_give(win)


def _do_give(win):
    """给与面板已打开：检索项链 → 点击选中 → 点击给与按钮完成给与。"""
    item, desc = bag.find_item_with_slots(win, XIANGLIAN_ICON, "项链")
    if item is None:
        log(f"[五环] {desc}", "WARN")
        raise MissingEquipment("给与面板中未找到项链（已切换全部储物格）")
    log(f"[五环] 找到项链（{desc}，匹配度 {item.confidence:.2f}），点击选中", "SYS")
    human_click(win, item.x, item.y)
    time.sleep(0.5)  # 等待选中
    pt = game_map.find_template(win, GIVE_BTN_ICON)
    if pt is None:
        log("[五环] 未找到给与按钮，等待下次轮询重试", "WARN")
        return True  # 面板仍在，下次轮询从阶段二继续
    human_click(win, pt[0], pt[1])
    log("[五环] 已点击给与按钮，装备给与完成", "SYS")
    return True


def _wuhuan_progress_key(items, img_np):
    """五环进度键：追踪面板中子目标关键词行及其亮绿状态。

    子目标推进时链接行文案变化（如「…武痴」→「…盗号贼」）；若文字
    签名检测不到（子目标行被判定为标题行、未并入任务内容），改用
    面板关键词行集合检测推进：行消失（关键词集合变化）或行色变暗
    （亮绿状态变化）均视为推进。
    """
    found = []
    for txt, rect, score in items:
        if score >= 0.55 and rect[0] < task_tracker.PANEL_MAX_X:
            for kw in WUHUAN_SUB_KEYWORDS:
                if kw in txt:
                    bright = task_tracker.bright_green_ratio(img_np, rect) > 0
                    found.append(f"{kw}{'+' if bright else ''}")
                    break
    return "|".join(sorted(set(found)))


def run_loop(win, cycle_interval=3.0):
    """五环主循环：领取任务 → 跟踪到完成 → 回云游大师领取下一次。

    任务追踪中已有五环任务（续跑场景）时跳过购买装备与领取，直接跟踪。
    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取时出现活力值不足对话框（点击「那可不行，我一会再来。」后结束）
    3. 跟踪期间出现「恭喜你完成了今天五环任务」对话框（今日五环全部完成）
    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(WUHUAN_TITLE, 0)
    log(f"[五环] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")

    # 续跑场景：任务已在追踪中则跳过购买装备（首次领取时再走完整流程）
    if not task_tracker.has_task(win, WUHUAN_TITLE):
        ok, info = _prepare(win)
        if not ok:
            return f"准备阶段失败：{info}"
        log(f"[五环] {info}", "SYS")

    done = 0
    round_no = 0
    while True:
        round_no += 1
        log(f"[五环] ==== 第 {round_no} 轮 ====", "SYS")
        # 任务追踪中已有「五环[五环]」任务：直接跟踪，跳过领取
        if task_tracker.has_task(win, WUHUAN_TITLE):
            log("[五环] 任务追踪中已有五环任务，直接跟踪", "SYS")
        else:
            ok, info = _claim_task(win)
            if not ok:
                if info == VITALITY_LIMIT_INFO:
                    log(f"[五环] {info}（已完成 {done} 次），结束任务", "SYS")
                    return f"活力值不足，结束五环任务（已完成 {done} 次）"
                log(f"[五环] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[五环] {info}", "SYS")
        # 提交装备步骤：由 follow_task 的附加对话处理回调接管
        try:
            ok, info = follow_task(win, WUHUAN_TITLE, prefix="五环",
                                   extra_dialog_handler=_handle_submit_dialog,
                                   fallback_keywords=WUHUAN_SUB_KEYWORDS,
                                   progress_key=_wuhuan_progress_key)
        except MissingEquipment as e:
            log(f"[五环] {MISSING_EQUIPMENT_INFO}（{e}），结束任务", "SYS")
            return f"{MISSING_EQUIPMENT_INFO}，结束五环任务（已完成 {done} 次）"
        except DailyComplete:
            # 恭喜对话框出现在本轮完成时：计入本轮
            log(f"[五环] {COMPLETE_INFO}（含本轮共 {done + 1} 次），结束任务", "SYS")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done + 1} 次）"
        if not ok:
            log(f"[五环] {info}", "WARN")
            time.sleep(cycle_interval)
            continue
        done += 1
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        log(f"[五环] {info}（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次五环任务，达到次数上限 {max_rounds}"
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
