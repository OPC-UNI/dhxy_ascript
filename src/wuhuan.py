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

from .app_utils import log, human_click, precise_click, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import bag  # 背包操作（储物格切换搜索）
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）
from .xuanshu import follow_task, _click_dialog_green_option  # 复用玄枢轶闻的任务跟踪/对话框逻辑
from .dialog_common import (_dialog_text_present,
                            _click_screen_center,
                            close_scene_panels,
                            green_option_center)  # 对话框共享组件

# 任务追踪面板中的任务标题
WUHUAN_TITLE = "五环"
# 五环子目标关键词：追踪链接行格式「去找{地图}(x, y){子目标}」（实测
# 「去找大唐境内(223, 21)武痴[1/5]」、「去找大唐边境(193, 144)惯骗[3/5]」），
# 链接行内容采集失败时按关键词兜底点击
WUHUAN_SUB_KEYWORDS = ["替身", "武痴", "惯骗","盗号贼", "食婴鬼"]
# 签名污染行判定（_wuhuan_text_key 用）：去标点后纯数字的行
# （计数碎片「1/51」→151、无标签倒计时「00:59:16」→005916）
_DIGITS_ONLY_RE = re.compile(r"^\d+$")
# 链接行标记：五环追踪链接行恒为「去找{地图}(x, y){子目标}」格式；
# 「去我」为「去找」的常见 OCR 误读
_LINK_MARK_RE = re.compile(r"去找|去我|前往")
# 链接行剥离项（签名归一用）：回合计数 [3/5]（碎片行拆分抖动，剥离后
# 签名稳定，真实计数变化由进度键/坐标变化检出）、坐标括号段（含右括号
# 丢失变体）、残留括号、括号丢失时的坐标数字（「1347情缘」，2026-10-31
# 实测区域 OCR 把 (13，47) 读成 1347 混入文案）
_LINK_STRIP_RE = re.compile(
    r"[\[［【]\s*\d+\s*/\s*\d+\s*[\]］】]?"
    r"|[\(（][^\)）]*[\)）]?"
    r"|[\(（\)）\[\]［］【】]"
    r"|\d{2,}")

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
YUNYOU_MAP_POS = (416, 368)                         # 小地图上云游大师位置的点击点
CLAIM_DIALOG_TEXT = "在我初入江湖时，有幸得到前辈大侠相助"  # 领取对话框文案
CLAIM_DIALOG_OPTION = "我可以帮助这些人"            # 领取对话框绿色选项
VITALITY_DIALOG_TEXT = "您的活力值在领取本次五环任务后将不足1800"  # 活力值不足对话框文案
VITALITY_DIALOG_OPTION = "那可不行，我一会再来。"    # 活力值不足时点击的选项（点击后结束任务）
VITALITY_LIMIT_INFO = "活力值不足（领取本次五环任务后将不足1800）"  # 结束状态描述
TASK_LINGQU_CHANGAN_ICON = "task_lingqu_changan.png"  # 长安云游大师的领取任务按钮（与洛阳城模板不同，实测 1.0）
# ---- 提交装备（给与）流程 ----
SUBMIT_DIALOG_TEXT = "我在这里练级，打了好久都打不过夜叉和猪怪。"  # 提交装备对话文案
SUBMIT_DIALOG_OPTION = "刚好我这里有，送你啦"              # 对话绿色选项
# 任务开场白对话（到达 NPC 自动弹出，无选项）：「李冰冰的替身…遇到了
# 麻烦，快去帮助他吧」——点屏幕中间推进（见 _handle_submit_dialog 开头）
INTRO_DIALOG_TEXT = "快去帮助他吧"
# 本轮跟踪是否出现过单次完成恭喜对话框「恭喜你完成了一次五环任务，
# 今日还能再完成一次…」——完成次数的计数信号（用户确认：完成提示以
# 对话框文案为准）。「任务从追踪中消失」只是面板现象，可能瞬时不可读
# 误判（实测 2026-10-13 08:46:36 面板空 2~3s 被误判完成计 1 次，
# 任务实际仍在），未见恭喜对话框的消失不计完成（run_loop 据此过滤）
_congrats_seen_this_follow = False
GIVE_ICON = "give_title.png"          # 给与面板标志（横幅「给与」静态标题区，32x114）
GIVE_BTN_ICON = "give_btn.png"                      # 给与按钮（28.png，OCR 读作「给子」）
XIANGLIAN_ICON = "fuzhuangdian_xianglian.png"       # 项链（与商店/给与面板行格式相同，实测给与面板匹配 1.0）
MISSING_EQUIPMENT_INFO = "缺少五环装备"              # 终止原因描述
# ---- 完成标记 ----
COMPLETE_DIALOG_TEXT = "恭喜你完成了今天五环任务"    # 今日五环全部完成的对话框文案
COMPLETE_INFO = "今日五环任务已完成"                 # 结束状态描述
# 单次完成的恭喜对话框（完成一次五环后出现「恭喜你完成了一次五环
# 任务，今日还能再完成一次…」，无绿色选项、无需处理，任务追踪中没有
# 五环任务时直接领取下一次即可）：文案与每日完成对话框相近，OCR 个别
# 字误读时模糊匹配可能混淆（实测 0.87 过阈值），须以「还能再完成」
# 作为区分特征排除
CONGRATS_MARK_TEXT = "还能再完成"                    # 区分特征：出现即非每日完成
# 每日两次上限对话框（领取时出现「互助友爱，铭记在心即可，今天你已经
# 完成过此任务两次，请明天再来！」，今日两次五环均已完成的结束信号）
DAILY_LIMIT_TEXT = "互助友爱，铭记在心即可，今天你已经完成过此任务两次，请明天再来！"
DAILY_LIMIT_INFO = "今日两次五环均已完成（请明天再来）"   # 结束状态描述
# 跟踪总超时（秒）：一轮五环含 5 个子目标与多场战斗，实测耗时约 9 分半，
# 默认 300s 会中途超时重启（日志出现「跟踪后任务信息无变化（超时）」）
WUHUAN_FOLLOW_TIMEOUT = 1800

# ---- 时序参数（秒） ----
CLOTH_SHOP_ARRIVE_TIMEOUT = 90  # 寻路到布店门口的超时
SHOP_ENTER_TIMEOUT = 15         # 进入服装店的超时（到达门口后）
CLAIM_ARRIVE_TIMEOUT = 90       # 寻路到云游大师附近的超时
CLAIM_BUTTON_TIMEOUT = 20       # 到达后等待领取任务按钮出现的超时
CLAIM_DIALOG_TIMEOUT = 15       # 领取对话框处理超时
CLAIM_VERIFY_TIMEOUT = 25       # 领取后任务出现在追踪面板的校验超时（任务渲染较慢，实测 15s 压线）
GIVE_PANEL_TIMEOUT = 10         # 等待给与面板出现的超时


class MissingEquipment(Exception):
    """给与面板中未找到五环装备（切换全部储物格后）。"""


class DailyComplete(Exception):
    """检测到今日五环任务全部完成的恭喜对话框。"""


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

    精确匹配避免误判相邻地图（如「长安城东」不是「长安」）；
    复用 game_map.is_at_position（地名由正则提取不含空白，原 strip 无实际作用）。
    """
    return game_map.is_at_position(pos, CHANGAN_CITY,
                                   target[0], target[1], MAP_POS_TOLERANCE)


def _wait_near(win, target, timeout, desc, interval):
    """等待走到长安目标坐标附近（战斗中跳过）。返回 (是否到达, 描述)。

    interval 为轮询间隔（到达判定非防抖点，1~2s 由调用方按场景传入）。
    """
    deadline = time.time() + timeout
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(interval)
            continue
        pos = game_map.read_map_position(win)
        if _near_pos(pos, target):
            return True, pos
        polls += 1
        if polls % 5 == 0:
            log(f"[五环] 前往{desc}中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(interval)
    return False, f"寻路到{desc}超时（未到达长安 {target[0]},{target[1]} 附近）"


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
    close_scene_panels(win, "五环")
    # 等待到达布店门口（长安 130,128 附近）后再进入，避免未到位就点击
    ok, info = _wait_near(win, CLOTH_SHOP_TARGET_POS, CLOTH_SHOP_ARRIVE_TIMEOUT,
                          "布店", 1.0)
    if not ok:
        return False, info
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
    precise_click(win, pt[0], pt[1], times=EQUIPMENT_COUNT, interval=0.4)  # 0.4s 间隔避免被识别为双击
    log(f"[五环] 已点击项链 {EQUIPMENT_COUNT} 次", "SYS")
    # 点购买完成
    pt = game_map.find_template(win, GOUMAI_ICON)
    if pt is None:
        return False, "未找到购买按钮"
    human_click(win, pt[0], pt[1])
    log("[五环] 已点击购买", "SYS")
    return True, f"已购买 {EQUIPMENT_COUNT} 件 2 级装备"


def _click_green_option_if(win, items, img_np, pred, needle=None):
    """点击满足谓词的对话框绿色选项（x≥PANEL_MAX_X）。

    限定对话框区域避免误点追踪面板的绿字链接；点击后移走鼠标
    （悬停变色会干扰后续绿字判定）。needle 为选项文案（规范化后）：
    行合并相邻文字时按比例取选项自身中心（防缓存毒化）。
    返回点击坐标 (cx, cy) 或 None。
    """
    for txt, rect, score in items:
        if score >= 0.55 and pred(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            opt_len = len(needle) if needle else 0
            cx, cy = green_option_center(img_np, rect, opt_len, len(norm_text(txt)))
            human_click(win, cx, cy)
            log(f"[五环] 已点击绿色选项「{txt}」", "SYS")
            move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
            return cx, cy
    return None


def _click_green_option(win, items, img_np, text):
    """点击指定文案的绿色选项（忽略标点差异的子串匹配）。

    OCR 对中文标点误读常见（如「，」读成「；」），去标点后按子串匹配。
    识别点击成功即记录选项固定位置（高性能模式缓存用，位置漂移自愈）。
    返回点击坐标 (cx, cy) 或 None。
    """
    needle = norm_text(text)
    pos = _click_green_option_if(win, items, img_np,
                                 lambda t: needle in norm_text(t), needle=needle)
    if pos is not None:
        app_utils.fixed_pos_set(f"option_{text}", win, pos[0], pos[1])
    return pos


def _daily_complete_open(win):
    """每日完成对话框是否打开（「恭喜你完成了今天五环任务」）。

    完成对话框可能在 follow 收尾后弹出——任务消失返回后无人再查，
    流程会继续领取（实测 2026-10-13 对话框已开但流程仍在反复尝试
    领取，世界地图被对话框遮挡打不开）。须排除单次恭喜对话框
    （含「还能再完成」特征即非每日完成）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
    return (_dialog_text_present(items, COMPLETE_DIALOG_TEXT)
            and not _dialog_text_present(items, CONGRATS_MARK_TEXT))


def _wait_completion_dialog(win, timeout=8.0):
    """任务消失后补查完成对话框（任务消失与对话框渲染存在竞态）。

    实测 2026-10-22：任务先从追踪面板消失（follow 随即返回），恭喜
    对话框稍后才渲染——两轮完成均在对话框出现前结束检查导致漏计数。
    follow 返回后补查一段窗口期，命中即判定本轮完成。
    返回 "daily"（今日全部完成）/ "single"（单次完成）/ None（未见）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.5)
            continue
        if is_in_battle_frame(img):
            time.sleep(1.5)  # 战斗画面遮挡对话框：跳过本帧
            continue
        items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if _dialog_text_present(items, COMPLETE_DIALOG_TEXT) \
                and not _dialog_text_present(items, CONGRATS_MARK_TEXT):
            return "daily"
        if _dialog_text_present(items, CONGRATS_MARK_TEXT):
            return "single"
        time.sleep(1.5)
    return None


def _handle_claim_blockers(win, items, img_np):
    """领取对话链中的结束条件对话框：今日两次已满/活力值不足。

    活力值不足点「那可不行，我一会再来。」后结束任务（选项点击即
    返回结束）；所有领取轮询路径（主循环/选项后窗口/兜底/校验）
    每轮询调用，避免高性能缓存直点后选项不可见时漏检。
    返回 (是否结束, 结束描述或 None)。
    """
    if _dialog_text_present(items, DAILY_LIMIT_TEXT):
        log(f"[五环] 检测到「{DAILY_LIMIT_TEXT}」", "SYS")
        return True, DAILY_LIMIT_INFO
    if _dialog_text_present(items, VITALITY_DIALOG_TEXT):
        if _click_green_option(win, items, img_np, VITALITY_DIALOG_OPTION):
            log("[五环] 活力值不足，已点击「那可不行，我一会再来。」", "SYS")
            return True, VITALITY_LIMIT_INFO
    return False, None


def _claim_task(win):
    """到云游大师领取五环任务。返回 (是否成功, 描述)。"""
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取"
    # 入口防御：上一轮遗留的限次/活力对话框未关闭时（如高性能直点后
    # 领取失败），会挡住后续小地图打开——先检测并处理（每轮一次
    # 对话框区域 OCR，约 100-170ms）
    img = win.capture(force_activate=False)
    if img is not None:
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        ended, info = _handle_claim_blockers(win, items, img_np)
        if ended:
            return False, info
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
    close_scene_panels(win, "五环")
    # 等待到达云游大师附近（长安 88,175 附近）后再点击领取按钮，避免未到位就点击
    ok, info = _wait_near(win, YUNYOU_TARGET_POS, CLAIM_ARRIVE_TIMEOUT,
                          "云游大师", 2.0)
    if not ok:
        return False, info
    log("[五环] 已到达云游大师附近", "SYS")
    # 等待领取任务按钮出现并点击（按钮随站位渲染位置变化，必须识图
    # 定位，不缓存）
    if not _wait_template(win, TASK_LINGQU_CHANGAN_ICON, CLAIM_BUTTON_TIMEOUT):
        return False, "到达后未出现领取任务按钮"
    pt = game_map.find_template(win, TASK_LINGQU_CHANGAN_ICON)
    if pt is None:
        return False, "领取任务按钮出现后又消失，未点击"
    human_click(win, pt[0], pt[1])
    log("[五环] 已点击领取任务", "SYS")
    # 高性能：领取选项坐标已缓存 → 固定等待渲染后直点（免首轮 OCR）；
    # 生效与否由下方任务出现轮询判定并兜底
    if app_utils.high_perf_on():
        pos = app_utils.fixed_pos_get(f"option_{CLAIM_DIALOG_OPTION}", win)
        if pos is not None:
            time.sleep(app_utils.HIGH_PERF_DIALOG_WAIT)
            human_click(win, pos[0], pos[1])
            log(f"[五环][高性能] 已按缓存坐标点击「{CLAIM_DIALOG_OPTION}」 ({pos[0]}, {pos[1]})", "SYS")
    # 对话框：活力值不足则点击「那可不行，我一会再来。」结束任务；
    # 正常则点击「我可以帮助这些人」完成领取。
    # 每轮询截一帧 + 面板差分门 + 对话框区域 OCR（任务出现与对话框
    # 判定同帧复用；面板门复用静态结论，任务进追踪必开）
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    claimed = False
    track_gate = task_tracker.TrackGate(min_interval=0.0)  # 面板差分门（四段循环共用；
        # 领取校验短窗口关键检测，不限频——同修罗 _claim_task（2026-10-28）
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.2)
            continue
        track_items = track_gate.ocr(img)
        dialog_items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        track_gate.paste_track(img, img_np)
        items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
        if task_tracker.has_task_in(items, img_np, WUHUAN_TITLE):
            claimed = True
            break
        ended, info = _handle_claim_blockers(win, items, img_np)
        if ended:
            return False, info
        if _click_green_option(win, items, img_np, CLAIM_DIALOG_OPTION):
            claimed = True
            # 活力值不足/今日两次已满提示在点击领取选项之后出现：重新截帧并轮询几秒。
            # 任务出现在追踪面板 = 领取成功，限次/活力对话框不会再出现 → 提前退出
            # （否则成功路径白白等满 4s 窗口，日志实测 ~5s 空档）
            vdeadline = time.time() + 4
            while time.time() < vdeadline:
                img = win.capture(force_activate=False)
                if img is None:
                    time.sleep(0.5)
                    continue
                track_items = track_gate.ocr(img)
                dialog_items, img_np = game_map.ocr_region(
                    img, task_tracker.DIALOG_REGION)
                track_gate.paste_track(img, img_np)
                items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
                if task_tracker.has_task_in(items, img_np, WUHUAN_TITLE):
                    break
                ended, info = _handle_claim_blockers(win, items, img_np)
                if ended:
                    return False, info
                time.sleep(0.8)
            break
        time.sleep(0.5)
    if not claimed:
        # 兜底：OCR 噪声导致指定文案未匹配时，点含「五环/帮助」的绿色选项
        deadline = time.time() + CLAIM_DIALOG_TIMEOUT
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.5)
                continue
            track_items = track_gate.ocr(img)
            dialog_items, img_np = game_map.ocr_region(
                img, task_tracker.DIALOG_REGION)
            track_gate.paste_track(img, img_np)
            items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
            if task_tracker.has_task_in(items, img_np, WUHUAN_TITLE):
                claimed = True
                break
            ended, info = _handle_claim_blockers(win, items, img_np)
            if ended:
                return False, info
            _click_green_option_if(win, items, img_np,
                                   lambda t: "五环" in t or "帮助" in t)
            time.sleep(0.5)
    if not claimed:
        return False, "领取对话框处理失败"
    # 校验任务出现在追踪面板；期间检测今日两次已满对话框
    # （点击领取选项后 NPC 回复「互助友爱…请明天再来」时任务不会出现）。
    # 任务渲染较慢，实测 15s 压线（日志 19:09），放宽到 25s
    deadline = time.time() + CLAIM_VERIFY_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        track_items = track_gate.ocr(img)
        dialog_items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        track_gate.paste_track(img, img_np)
        items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
        if task_tracker.has_task_in(items, img_np, WUHUAN_TITLE):
            break
        ended, info = _handle_claim_blockers(win, items, img_np)
        if ended:
            return False, info
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
    # 完成标记优先：每日五环全部完成出现恭喜对话框 → 抛异常结束任务循环。
    # 「恭喜你完成了一次五环任务（今日还能再完成一次）」是单次完成的
    # 恭喜对话框，与每日完成文案相近，OCR 误读时模糊匹配会混淆，
    # 含「还能再完成」特征即非每日完成，不得结束任务。
    # 单次对话框先判（特征词直接命中即可计数，不依赖与每日文案的
    # 模糊匹配——2026-10-22 实测两轮完成因该匹配未命中漏计数）
    if _dialog_text_present(items, CONGRATS_MARK_TEXT):
        # 单次完成恭喜对话框：本轮真实完成的计数信号——置位标记，
        # run_loop 据此计数（完成提示以对话框文案为准，用户 2026-10-13 确认）
        global _congrats_seen_this_follow
        _congrats_seen_this_follow = True
        log("[五环] 检测到单次完成恭喜对话框，本轮完成", "SYS")
        return True
    if _dialog_text_present(items, COMPLETE_DIALOG_TEXT):
        log(f"[五环] 检测到「{COMPLETE_DIALOG_TEXT}」，今日五环任务已完成", "SYS")
        raise DailyComplete()
    # 任务开场白对话（到达 NPC 自动弹出，无选项）：点屏幕中间推进一次
    # （多页对话由下一轮询自愈）。必须先于跟踪绿字处理——开场白与追踪
    # 面板变化常同窗口期出现，不处理会对着打开中的对话框点任务绿字
    # （实测 2026-10-13：点击「去找长安」绿字时开场白对话正打开）
    if _dialog_text_present(items, INTRO_DIALOG_TEXT):
        _click_screen_center(win, times=1, prefix="五环")
        return True
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
    """五环进度键：追踪面板中亮绿子目标关键词集合（去重、行序无关）。

    子目标推进时链接行文案变化（如「…武痴」→「…盗号贼」），新链接行
    亮绿、旧行变暗；只统计亮绿关键词行：暗绿条目（同一视觉行被 OCR
    拆出的重复行框、已完成子目标的残留行）不进键——日志 19:09:30-48
    键在「替身+」↔「替身|替身+」间震荡（暗绿替身条目出现/消失），
    亮绿集合恒为 {替身} 稳定；19:09:54 真实推进「惯骗+|替身」亮绿集
    {惯骗}≠{替身} 检出；19:09:57 旧行移除「惯骗|惯骗+」→「惯骗+」
    亮绿集同为 {惯骗}，不再误触发。
    旧行先于新行移除/渲染的过渡帧亮绿集短暂不变 → 延迟一拍检出
    （不误点击），文本签名兜底。
    """
    found = set()
    for txt, rect, score in items:
        if score >= 0.55 and rect[0] < task_tracker.PANEL_MAX_X:
            for kw in WUHUAN_SUB_KEYWORDS:
                if kw in txt and task_tracker.bright_green_ratio(img_np, rect) > 0:
                    found.add(kw)
                    break
    return "|".join(sorted(found))


def _merge_same_line(rows):
    """同行碎片合并：同一视觉行的碎片纵向高度重叠 ≥60%（行距约 18px 的
    相邻行达不到），返回 [(merged_rect, [(rect, txt), ...]), ...]。

    碎片再拆分也不改变合并结果——链接行「去找大雁塔二层(13，47)情缘」
    被区域 OCR 拆成「去找大雁塔二」+「情缘」或整段漏检都不影响定位。
    """
    rows.sort(key=lambda r: r[0][1])
    merged = []  # [(merged_rect, [(rect, txt), ...])]
    for rect, txt in rows:
        for mrect, mparts in merged:
            if min(rect[3], mrect[3]) - max(rect[1], mrect[1]) \
                    >= 0.6 * min(rect[3] - rect[1], mrect[3] - mrect[1]):
                mrect[0] = min(mrect[0], rect[0])
                mrect[2] = max(mrect[2], rect[2])
                mrect[1] = min(mrect[1], rect[1])
                mrect[3] = max(mrect[3], rect[3])
                mparts.append((rect, txt))
                break
        else:
            merged.append([list(rect), [(rect, txt)]])
    return merged


def _wuhuan_link_picker(items, img_np):
    """五环绿字精确定位回调（track_task 的 green_picker）。

    链接行「去找大雁塔二层(13，47)情缘」较长，区域 OCR 每次读取的拆分
    方式不同（实测拆成「去找大雁塔二」+「情缘」、坐标读成 1347 混入文案
    或整段漏检），行级选择在碎片间漂移——点中描述文字 (108,264) 或
    子目标行/剩余时间行 (63~69,285)（2026-10-31）。本回调把锚点下方
    面板行按同行合并，选最靠上且含链接标记（去找/去我/前往或地图坐标）
    的合并行点击：下划线检测在合并框内命中行尾链接下划线。
    无链接标记行时返回 PICK_FAIL：track_task 等待重试，绝不做行级兜底
    点击——子目标行同为亮绿带下划线、剩余时间行因 OCR 框重叠沾绿
    （实测 3.2%），颜色与下划线都无法区分。
    """
    anchor = None
    for txt, rect, score in items:
        if task_tracker._is_title_line(txt) and WUHUAN_TITLE in txt \
                and score >= 0.55 and rect[0] < task_tracker.PANEL_MAX_X:
            anchor = rect
            break
    if anchor is None:
        return task_tracker.PICK_FAIL
    rows = []
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if rect[1] - anchor[3] > 150:
            continue  # 锚点下方内容范围外（同 track_task 内容采集纵向限制）
        if rect[0] >= anchor[2] + 40 and rect[2] <= anchor[0] - 40:
            continue  # 与锚点无横向交叠（同内容采集几何过滤）
        rows.append((list(rect), txt))
    if not rows:
        return task_tracker.PICK_FAIL
    for mrect, members in _merge_same_line(rows):
        if any(_LINK_MARK_RE.search(txt)
               or task_tracker.MAP_COORD_RE.search(txt)
               for _rect, txt in members):
            return task_tracker.green_text_center(img_np, mrect)
    return task_tracker.PICK_FAIL


def _wuhuan_text_key(items, img_np):
    """五环文本签名键：标题 + 链接行归一化拼接（行序无关、噪声行过滤）。

    链接行「去找大雁塔二层(13，47)情缘」较长，区域 OCR 每次读取的拆分
    方式与坐标格式都不同（2026-10-31 实测交替出现「去找大雁塔二|47情缘」、
    「去找大雁塔二层1347情缘」、「层(13,47)情缘」等变体，签名随之翻转、
    每 2s 重复点击 22 轮）；同行碎片先按 x 合并再剥离坐标/计数后拼接，
    各变体归一后相似度 ≥0.8，不再触发误变化。
    非链接行不参与签名：替身子目标行（「24载的替身[1/5]」，OCR 误读
    多变、同为亮绿不可靠）、倒计时行（逐秒变化）、计数/纯数字碎片行，
    以及随人物走动出现/消失的场景玩家名（实测混入日行千里/老谭酸菜面
    等 11 行导致签名污染）——子目标推进由进度键（_wuhuan_progress_key）
    检测。链接行未检出时返回空串：follow_task 视作内容不可读，不点击
    也不作变化信号，等待下一轮复读。
    """
    task = task_tracker.find_task(task_tracker.group_tasks(items), WUHUAN_TITLE)
    if task is None:
        return ""
    rows = []
    for txt, rect, _score in task["lines"]:
        stripped = norm_text(txt)
        if not stripped:
            continue
        if _DIGITS_ONLY_RE.match(stripped):
            continue  # 计数碎片行/纯数字行（1/51→151、005916）
        if "剩余" in stripped:
            continue  # 倒计时行（含 OCR 变体「剩余时问」等），逐秒变化
        # 链接行标记用原文判定（norm_text 会吃掉坐标括号）；「去找」
        # 前缀与坐标括号互为兜底——前缀被误读时坐标行仍可入签名
        if not (_LINK_MARK_RE.search(txt)
                or task_tracker.MAP_COORD_RE.search(txt)):
            continue  # 替身行/场景玩家名等非链接行不参与签名
        rows.append((list(rect), stripped))
    if not rows:
        return ""
    parts = []
    for _mrect, mparts in _merge_same_line(rows):
        mparts.sort(key=lambda p: p[0][0])
        line = _LINK_STRIP_RE.sub("", "".join(t for _rect, t in mparts))
        if line:
            parts.append(line)
    if not parts:
        return ""
    return "|".join([norm_text(task["title"])] + sorted(set(parts)))


def run_loop(win, cycle_interval=3.0):
    """五环主循环：领取任务 → 跟踪到完成 → 回云游大师领取下一次。

    任务追踪中已有五环任务（续跑场景）时跳过购买装备与领取，直接跟踪。
    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取时出现活力值不足对话框（点击「那可不行，我一会再来。」后结束）
    3. 领取时出现「互助友爱，铭记在心即可…请明天再来」对话框（今日两次已满）
    4. 跟踪期间出现「恭喜你完成了今天五环任务」对话框（今日五环全部完成）
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

    done = app_utils.task_progress_done(win, WUHUAN_TITLE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[五环] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[五环] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次五环任务，达到次数上限 {max_rounds}"
    round_no = 0
    while True:
        round_no += 1
        if is_in_battle(win):
            # 战斗中任务面板不可读：静默等待战斗结束再判定（避免每 3s
            # 刷「跳过本轮领取」与面板 OCR，实测一场战斗 2.5 分钟刷 38 轮）
            time.sleep(3.0)
            continue
        log(f"[五环] ==== 第 {round_no} 轮 ====", "SYS")
        # 每日完成对话框检查（每轮开头）：见 _daily_complete_open——
        # 命中即今日两次五环均已完成，直接结束任务。对话框开着说明
        # 上一轮真实完成（对话框与任务消失的竞态漏计场景），含本轮
        # 计入并持久化（2026-10-22 实测漏计数修复）
        if _daily_complete_open(win):
            done += 1
            app_utils.task_progress_add(win, WUHUAN_TITLE, done)
            log(f"[五环] {COMPLETE_INFO}（含本轮共 {done} 次），结束任务", "SYS")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        # 任务追踪中已有「五环[五环]」任务：直接跟踪，跳过领取
        if task_tracker.has_task(win, WUHUAN_TITLE):
            log("[五环] 任务追踪中已有五环任务，直接跟踪", "SYS")
        else:
            ok, info = _claim_task(win)
            if not ok:
                if info == VITALITY_LIMIT_INFO:
                    log(f"[五环] {info}（已完成 {done} 次），结束任务", "SYS")
                    return f"活力值不足，结束五环任务（已完成 {done} 次）"
                if info == DAILY_LIMIT_INFO:
                    log(f"[五环] {info}（已完成 {done} 次），结束任务", "SYS")
                    return f"{DAILY_LIMIT_INFO}，结束五环任务（已完成 {done} 次）"
                log(f"[五环] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[五环] {info}", "SYS")
        # 提交装备步骤：由 follow_task 的附加对话处理回调接管
        # signature_key：链接行归一签名（碎片行翻转不再引起重复点击）
        # ignore_empty_key：到达 NPC 链接变暗的过渡帧不作变化信号
        # green_picker：碎片合并后按链接标记精确定位（替身行/剩余时间行
        # 不可点）；require_coord：标题/内容漏检时的关键词兜底也须含坐标
        _congrats_seen_this_follow = False  # 每轮跟踪前复位完成信号
        try:
            ok, info = follow_task(win, WUHUAN_TITLE, prefix="五环",
                                   timeout=WUHUAN_FOLLOW_TIMEOUT,
                                   extra_dialog_handler=_handle_submit_dialog,
                                   fallback_keywords=WUHUAN_SUB_KEYWORDS,
                                   progress_key=_wuhuan_progress_key,
                                   signature_key=_wuhuan_text_key,
                                   green_picker=_wuhuan_link_picker,
                                   ignore_empty_key=True,
                                   skip_texts={},
                                   require_coord=True)
        except MissingEquipment as e:
            log(f"[五环] {MISSING_EQUIPMENT_INFO}（{e}），结束任务", "SYS")
            return f"{MISSING_EQUIPMENT_INFO}，结束五环任务（已完成 {done} 次）"
        except DailyComplete:
            # 恭喜对话框出现在本轮完成时：计入本轮并持久化（对话框
            # 即完成证据；2026-10-22 前未持久化导致进度漏记）
            done += 1
            app_utils.task_progress_add(win, WUHUAN_TITLE, done)
            log(f"[五环] {COMPLETE_INFO}（含本轮共 {done} 次），结束任务", "SYS")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        completion_kind = None  # 补查到的完成对话框类型（daily/single/None）
        if not ok:
            if _congrats_seen_this_follow:
                # 完成恭喜对话框已出现：本轮真实完成，follow 收尾失败
                # 不影响计数（完成信号以对话框文案为准，用户确认）
                log(f"[五环] {info}（已出现完成恭喜对话框，照常计入）", "WARN")
            else:
                log(f"[五环] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
        elif not _congrats_seen_this_follow:
            # 任务从追踪中消失但跟踪期间未见完成恭喜对话框：任务消失
            # 与对话框渲染存在竞态（实测 2026-10-22 两轮完成均漏计数），
            # 补查对话框窗口期再决定——命中即计入；仍未命中才按面板
            # 瞬时不可读处理（下轮 has_task 重检，任务仍在则直接续跑）
            completion_kind = _wait_completion_dialog(win)
            if completion_kind is None:
                log(f"[五环] {info} 但未出现完成恭喜对话框，判为面板瞬时不可读，不计完成", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[五环] 补查检测到完成恭喜对话框（{'今日全部完成' if completion_kind == 'daily' else '单次完成'}），本轮计入", "SYS")
        done += 1
        app_utils.task_progress_add(win, WUHUAN_TITLE, done)
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        log(f"[五环] {info}（{done}/{total}）", "SYS")
        if completion_kind == "daily":
            # 补查发现今日五环全部完成：直接结束，不再领取下一轮
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次五环任务，达到次数上限 {max_rounds}"
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
