"""五环任务自动化。

流程：
0. 任务追踪中已有「五环[五环]」任务（续跑）→ 跳过准备与领取，直接跟踪
1. 准备（任务开始一次）：打开背包检查是否已有项链，有则跳过购买；
   没有则确保在长安（不在则世界地图点击长安）
   → 长安小地图点击布店位置寻路，等待到达布店门口（长安 130,128 附近）
   → 到达门口先确保降落（飞行中无法点击进店：开飞行驭器面板识别飞行
     状态、飞行中自动点降落，检查失败不阻断）→ 进入服装店 →
     点老板（牛记布店固定坐标 643,463）→ 对话框点「我想买点东西」
   → 轮询等待商店面板打开（项链图标出现）→ 匹配项链图标后鼠标移过去
     左键点击两次 → 点购买（购买 2 件 2 级装备）
2. 任务循环（次数按通用设置-任务次数）：
   - 到云游大师：长安小地图点击 (408,362) 寻路 → 等待到达长安 88,175 附近
     → 点击领取任务按钮 → 对话框点绿色选项「我可以帮助这些人」→ 任务出现在追踪面板
   - 点击五环任务追踪绿字 → 战斗（战斗线程自动处理）或提交装备
     （对话点「刚好我这里有，送你啦」→ 给与面板检索项链并点击选中
     → 点给与按钮完成；未找到则切换背包储物格再找，
     全部切换后仍未找到即终止任务）
     → 继续点击绿字 → 任务从追踪消失即完成一次
3. 完成后回到云游大师领取下一次；达到次数上限、领取时出现活力值不足
   对话框（按五环设置分流：开关开启点「不差那点小钱，快给我任务吧。」
   花钱领取继续任务；关闭点「那可不行，我一会再来。」结束任务）或跟踪
   期间出现「恭喜你完成了今天五环任务」对话框（今日五环全部完成）即结束任务进程
"""
import re
import time

from ..core.app_utils import log, human_click, precise_click, move_away, norm_text
from ..core import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from ..device import bag  # 背包操作（储物格切换搜索）
from ..device import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from ..device import task_tracker  # 任务追踪面板操作
from ..device.battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from ..core import debug_overlay  # 调试信息悬浮层（任务进度更新）
from ..core.settings import (load_task_counts,                # 任务次数上限（通用设置）
                             load_wuhuan_vitality_continue)   # 活力不足继续任务开关（五环设置）
from ..workflows.follow import (follow_task,
                               DEFAULT_DIALOG_OPTION_MAP as DIALOG_OPTION_MAP)  # 共享跟踪引擎/默认对话表
from ..device.dialog_common import click_dialog_green_option as _click_dialog_green_option  # 选项缓存点击
from ..device import zuoqi  # 飞行驭器（进服装店前降落检查：飞行中无法点击进店）
from ..device.dialog_common import (_dialog_text_present,
                            _click_screen_center,
                            close_scene_panels,
                            green_option_center,
                            StoryDialogDismisser)  # 对话框共享组件

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
VITALITY_DIALOG_OPTION = "那可不行，我一会再来。"    # 活力值不足时点击的选项（开关关闭：点击后结束任务）
VITALITY_CONTINUE_OPTION = "不差那点小钱，快给我任务吧。"  # 开关开启时点击的选项（点击后领取成功继续任务）
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
# ---- 无选项剧情对话点屏推进（2026-11-10 CPU 高峰实测） ----
# 五环途中的 NPC 剧情台词（如「我被人骗了…20万两银子…」）是标准对话框、
# 无绿字选项；wuhuan 的 follow 传 skip_texts={} 禁用了通用点屏推进（防
# 误触提交对话的绿字选项），此类对话无人处理 → 常驻屏幕，对话区模板门
# 每轮命中、每 0.5s 全量区域 OCR（一次 100-170ms，占任务线程 20-35%
# wall，与修罗 NPC 收尾对话 2026-11-09 同款）。兜底点屏幕中间推进/关闭
# （共享实现 dialog_common.StoryDialogDismisser）。follow 侧的通用兜底
# 传 story_dialog_dismiss=False 关闭、改用本实例：只有这里能挂「给与
# 面板预期窗口内不点」的任务专属抑制。
_give_expected_until = 0.0   # 给与面板预期截止（此窗口内不做剧情对话点屏，
                             # 防把手持给与流程的 NPC 对话误推进关闭）
_story_dismisser = StoryDialogDismisser(
    known_texts=DIALOG_OPTION_MAP,  # 已知选项对话：选项未渲染时只等待
    allow_dismiss=lambda: time.time() >= _give_expected_until)
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
# 签名变化判定相似阈值（follow_task 参数）：同图链接变化（「去找长安城东
# 李冰冰的替身」→「去找长安城东食婴鬼手下的手下」，地图同为长安城东）
# 归一签名相似度实测 0.846，默认 0.8 阈值将其过滤为 OCR 噪声而不点击、
# 任务卡住须人工点链接恢复（2026-11-01 两轮实测各卡 2.5/7.4 分钟）；
# 0.9 可检出（单字误读相似度 ≥0.923 仍被过滤，两字以上误读由确认轮询
# 收敛、误点击只是重复点同一链接无害）
WUHUAN_SIG_RATIO = 0.9

# ---- 时序参数（秒） ----
CLOTH_SHOP_ARRIVE_TIMEOUT = 90  # 寻路到布店门口的超时
# 进入服装店的超时（到达门口后）。每轮迭代含 点入口1s + 对话框选项最长4s
# + 商店面板最长 SHOP_OPEN_TIMEOUT，单次重试最坏约 11s——15s 只够一轮，
# 面板渲染慢未命中时来不及重试（2026-11-10 误判教训），放宽到 30s
SHOP_ENTER_TIMEOUT = 30
# 点「我想买点东西」后等待商店面板渲染打开的轮询超时：实测点击到面板
# 完整渲染有数百毫秒到数秒延迟，固定 0.3s sleep 后单次匹配会在面板渲染
# 前判定「未找到服装店项链」并使整个准备阶段失败（2026-11-10 实测：
# 失败后同一状态截图项链模板匹配 0.9999、购买按钮 1.0000，纯时序竞态）
SHOP_OPEN_TIMEOUT = 6.0
CLAIM_ARRIVE_TIMEOUT = 90       # 寻路到云游大师附近的超时
CLAIM_BUTTON_TIMEOUT = 20       # 到达后等待领取任务按钮出现的超时
CLAIM_DIALOG_TIMEOUT = 15       # 领取对话框处理超时
CLAIM_VERIFY_TIMEOUT = 25       # 领取后任务出现在追踪面板的校验超时（任务渲染较慢，实测 15s 压线）
GIVE_PANEL_TIMEOUT = 10         # 等待给与面板出现的超时


class MissingEquipment(Exception):
    """给与面板中未找到五环装备（切换全部储物格后）。"""


class DailyComplete(Exception):
    """检测到今日五环任务全部完成的恭喜对话框。"""


class SingleComplete(Exception):
    """检测到单次完成恭喜对话框：本轮完成，立即结束本轮跟踪。

    对话框即完成证据（用户确认：完成提示以对话框文案为准），
    无需等任务从追踪面板消失——实测任务消失延迟约 100 秒，
    期间 follow 空转且对话框关闭后补查落空漏计数（2026-11-03）。
    """


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


def _wait_shop_open(win, timeout=SHOP_OPEN_TIMEOUT):
    """轮询等待服装店商品面板出现（项链图标可见即视为已打开）。

    点「我想买点东西」到面板渲染完成有数百毫秒到数秒延迟，固定短
    sleep 后单次模板匹配会误判「未找到项链」（性能规则 7：固定 sleep
    等状态切换 → 改为轮询目标状态的直接信号）。timeout=0 时只查当前
    帧一次（复用已确认场景的快速断言）。返回 (是否打开, 项链坐标或 None)。
    """
    deadline = time.time() + timeout
    while True:
        pt = game_map.find_template(win, FUZHUANGDIAN_XIANGLIAN_ICON)
        if pt is not None:
            return True, pt
        if time.time() >= deadline:
            return False, None
        time.sleep(0.4)


def _enter_cloth_shop(win, tag="[五环]"):
    """走到布店门口并进入服装店、点老板打开商店对话框。

    与购买流程的进店段一致：小地图点布店位置寻路 → 等待到达布店门口
    → 降落检查 → 点入口 → 点老板 → 对话框点「我想买点东西」→ 轮询
    确认商店面板打开（项链图标可见）才返回成功——选项点击成功不代表
    面板已渲染，未打开则继续重试进店（2026-11-10 时序竞态教训）。
    诊断页「测试进入布店」复用本链路（tag 加验证标记）。
    """
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
    log(f"{tag} 已点击小地图布店位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    close_scene_panels(win, "五环")
    # 等待到达布店门口（长安 130,128 附近）后再进入，避免未到位就点击
    ok, info = _wait_near(win, CLOTH_SHOP_TARGET_POS, CLOTH_SHOP_ARRIVE_TIMEOUT,
                          "布店", 1.0)
    if not ok:
        return False, info
    log(f"{tag} 已到达布店门口", "SYS")
    # 飞行中无法点击进入服装店（空中进店点击无效，用户 2026-11-07 确认）：
    # 进店前先确保降落——开飞行驭器面板识别飞行状态（降落按钮在场=飞行中，
    # 点降落；未飞行空操作），再关面板防遮挡进店点击。仅在任务开始买装备
    # 时执行一次（_buy_equipments 每任务一次），面板开合开销可接受。
    # 降落检查失败不阻断：真飞行中进店点击会失败、由 SHOP_ENTER_TIMEOUT
    # 兜底后整轮重试（下轮再查降落），瞬态检查失败不影响正常进店
    ok, info = zuoqi.ensure_landed(win)
    log(f"{tag}[飞行驭器] 降落检查: {info}", "INFO" if ok else "WARN")
    zuoqi.close_feixingyuqi_panel(win)
    # 进入服装店：点入口 → 点老板坐标开始对话 → 对话框点「我想买点东西」
    # → 轮询确认商店面板打开。面板已开（上次流程遗留/渲染慢于本检查）时
    # 直接成功返回——入口/老板坐标都落在商店面板内部，已开面板继续盲点
    # 会把点击打进面板商品格
    deadline = time.time() + SHOP_ENTER_TIMEOUT
    while time.time() < deadline:
        ok, _pt = _wait_shop_open(win, timeout=0.0)
        if ok:
            log(f"{tag} 商店面板已打开", "SYS")
            return True, "已进入服装店，商店对话框已打开"
        human_click(win, CLOTH_SHOP_ENTER_POS[0], CLOTH_SHOP_ENTER_POS[1])
        time.sleep(1.0)  # 等待进店
        human_click(win, SHOP_OWNER_POS[0], SHOP_OWNER_POS[1])
        log(f"{tag} 已点击服装店老板", "SYS")
        ok, info = _click_dialog_green_option(win, BUY_DIALOG_OPTION, timeout=4,
                                             prefix="五环")
        if not ok:
            continue
        # 选项点击成功 ≠ 面板已渲染：轮询等项链图标出现再判定进店成功
        ok, _pt = _wait_shop_open(win)
        if ok:
            return True, "已进入服装店，商店对话框已打开"
        log(f"{tag} 已点击「{BUY_DIALOG_OPTION}」但商店面板 {SHOP_OPEN_TIMEOUT:.0f}s 内未出现，重试进店", "WARN")
    return False, "进入服装店后未出现购买对话框"


def _buy_equipments(win):
    """进入服装店购买 2 件 2 级装备（项链）。返回 (是否成功, 描述)。"""
    ok, info = _enter_cloth_shop(win)
    if not ok:
        return False, info
    # 进店成功时面板已确认打开（_enter_cloth_shop 内轮询确认）；此处再取
    # 一次项链位置直接用于点击（单帧快速断言，非单次判定的成败依据）
    ok, pt = _wait_shop_open(win, timeout=0.0)
    if not ok:
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


def verify_cloth_shop_flow(win):
    """诊断页-测试进入布店按钮：走真实进店链路，商店对话框打开即成功。

    与任务购买流程的进店段完全一致（走位 → 降落检查 → 点入口 →
    点老板 → 点「我想买点东西」），到商店对话框打开即停止——不购买
    任何装备。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，无法测试进入布店（请先结束战斗）"
    ok, info = _enter_cloth_shop(win, tag="[五环][验证]")
    if not ok:
        return False, info
    # 商店对话框打开信号：项链图标在场（进店链路已轮询确认，此处取位置
    # 仅用于验证日志）
    ok, pt = _wait_shop_open(win, timeout=0.0)
    if not ok:
        return False, "商店对话框中未找到服装店项链（未真正进入商店）"
    log(f"[五环][验证] 商店对话框已打开，项链图标 ({pt[0]}, {pt[1]})（未购买）", "SYS")
    return True, f"已进入布店（商店对话框已打开，未购买；项链图标 ({pt[0]}, {pt[1]})）"


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

    活力值不足按五环设置分流：开关开启点「不差那点小钱，快给我
    任务吧。」花钱领取继续任务（选项点击即领取成功，任务进入追踪
    面板，由调用方轮询检出）；关闭则点「那可不行，我一会再来。」
    后结束任务。所有领取轮询路径（主循环/选项后窗口/兜底/校验）
    每轮询调用，避免高性能缓存直点后选项不可见时漏检。
    返回 (是否结束, 结束描述或 None, 是否已点继续选项)。
    """
    if _dialog_text_present(items, DAILY_LIMIT_TEXT):
        log(f"[五环] 检测到「{DAILY_LIMIT_TEXT}」", "SYS")
        return True, DAILY_LIMIT_INFO, False
    if _dialog_text_present(items, VITALITY_DIALOG_TEXT):
        if load_wuhuan_vitality_continue():
            if _click_green_option(win, items, img_np, VITALITY_CONTINUE_OPTION):
                log("[五环] 活力值不足，已点击「不差那点小钱，快给我任务吧。」继续任务", "SYS")
                return False, None, True
            return False, None, False  # 选项未渲染/OCR 未读出，下次轮询重试
        if _click_green_option(win, items, img_np, VITALITY_DIALOG_OPTION):
            log("[五环] 活力值不足，已点击「那可不行，我一会再来。」", "SYS")
            return True, VITALITY_LIMIT_INFO, False
    return False, None, False


def _wait_tracked(win, timeout):
    """轮询等待任务出现在追踪面板（入口防御点继续选项后确认领取成功）。

    入口防御仅在遗留对话框场景触发，频率可接受（has_task 面板在场时
    区域 OCR 约 0.13~0.19s；fast_absent：标题条模板未命中且无遮挡时
    直接返回 False，面板未出现阶段每次轮询实测 ~0.12s）；战斗中跳过
    （战斗画面遮挡面板）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        if task_tracker.has_task(win, WUHUAN_TITLE, fast_absent=True):
            return True
        time.sleep(1.0)
    return False


def _walk_to_yunyou_claim_btn(win, tag="[五环]"):
    """走到云游大师并点击领取任务按钮（打开领取对话框）。

    与领取流程的走位段一致：小地图点云游大师位置寻路 → 等待到达
    （长安 88,175 附近）→ 等待领取任务按钮出现并点击（按钮随站位
    渲染位置变化，必须识图定位，不缓存）。返回 (是否成功, 描述)；
    成功时对话框由调用方轮询处理。诊断页「测试领取五环」复用本链路
    （tag 加验证标记）。
    """
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
    log(f"{tag} 已点击小地图云游大师位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    close_scene_panels(win, "五环")
    # 等待到达云游大师附近（长安 88,175 附近）后再点击领取按钮，避免未到位就点击
    ok, info = _wait_near(win, YUNYOU_TARGET_POS, CLAIM_ARRIVE_TIMEOUT,
                          "云游大师", 2.0)
    if not ok:
        return False, info
    log(f"{tag} 已到达云游大师附近", "SYS")
    # 等待领取任务按钮出现并点击
    if not _wait_template(win, TASK_LINGQU_CHANGAN_ICON, CLAIM_BUTTON_TIMEOUT):
        return False, "到达后未出现领取任务按钮"
    pt = game_map.find_template(win, TASK_LINGQU_CHANGAN_ICON)
    if pt is None:
        return False, "领取任务按钮出现后又消失，未点击"
    human_click(win, pt[0], pt[1])
    log(f"{tag} 已点击领取任务", "SYS")
    return True, f"已点击领取任务按钮 ({pt[0]}, {pt[1]})"


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
        ended, info, continued = _handle_claim_blockers(win, items, img_np)
        if ended:
            return False, info
        if continued:
            # 已点「不差那点小钱…」：选项点击即领取成功，任务进入
            # 追踪面板（渲染较慢，实测 15s 压线）。轮询确认后直接
            # 返回，不再走寻路重领——任务已在追踪时重领会误触
            # NPC 其它对话
            if _wait_tracked(win, CLAIM_VERIFY_TIMEOUT):
                return True, "活力值不足已点继续领取，五环任务已领取"
    # 走位到云游大师并点击领取任务按钮（打开领取对话框）
    ok, info = _walk_to_yunyou_claim_btn(win)
    if not ok:
        return False, info
    # 高性能：领取选项坐标已缓存 → 固定等待渲染后直点（免首轮 OCR）；
    # 生效与否由下方任务出现轮询判定并兜底
    if app_utils.high_perf_on():
        pos = app_utils.fixed_pos_get(f"option_{CLAIM_DIALOG_OPTION}", win)
        if pos is not None:
            time.sleep(app_utils.HIGH_PERF_DIALOG_WAIT)
            human_click(win, pos[0], pos[1])
            log(f"[五环][高性能] 已按缓存坐标点击「{CLAIM_DIALOG_OPTION}」 ({pos[0]}, {pos[1]})", "SYS")
    # 对话框：活力值不足按设置分流（点继续/点结束，见 _handle_claim_blockers）；
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
        ended, info, _continued = _handle_claim_blockers(win, items, img_np)
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
                ended, info, _continued = _handle_claim_blockers(win, items, img_np)
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
            ended, info, _continued = _handle_claim_blockers(win, items, img_np)
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
        ended, info, _continued = _handle_claim_blockers(win, items, img_np)
        if ended:
            return False, info
        time.sleep(1.0)
    if not task_tracker.has_task(win, WUHUAN_TITLE):
        return False, "已点击领取选项，但任务追踪中未检测到任务"
    return True, "五环任务已领取"


def verify_claim_flow(win):
    """诊断页-测试领取五环按钮：走到云游大师点领取任务按钮，对话面板
    出现即成功——不点任何选项、不真正领取。

    与领取流程一致到点击领取任务按钮为止；领取对话框/活力值不足/
    今日两次已满任一对话框出现均为面板出现证据。对话框停留不关闭
    （点屏幕中间是否会关领取对话框未实测，任何关闭尝试都可能误触
    绿色选项完成领取），由用户手动关闭。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，无法测试领取（请先结束战斗）"
    if task_tracker.has_task(win, WUHUAN_TITLE):
        return False, "任务追踪中已有五环任务，无法测试领取（请先完成当前任务）"
    ok, info = _walk_to_yunyou_claim_btn(win, tag="[五环][验证]")
    if not ok:
        return False, info
    # 轮询对话面板出现：任一领取链对话框均为面板出现证据（每轮询
    # 截一帧 + 对话框区域 OCR，复用领取超时参数）
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if _dialog_text_present(items, CLAIM_DIALOG_TEXT):
            log("[五环][验证] 领取对话框已出现（未领取）", "SYS")
            return True, "领取对话框已出现（未领取，请手动关闭对话框）"
        if _dialog_text_present(items, VITALITY_DIALOG_TEXT):
            log("[五环][验证] 出现活力值不足对话框", "SYS")
            return True, "出现活力值不足对话框（未领取，请手动关闭对话框）"
        if _dialog_text_present(items, DAILY_LIMIT_TEXT):
            log("[五环][验证] 出现今日两次已满对话框", "SYS")
            return True, "出现今日两次已满对话框（未领取，请手动关闭对话框）"
        time.sleep(0.5)
    return False, "点击领取任务按钮后未出现对话面板"


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


def _dismiss_story_dialog(win, items, img_np):
    """无绿字选项的剧情对话点屏推进（守卫见 StoryDialogDismisser）。

    本实例额外带「给与面板预期窗口内不点」抑制（allow_dismiss），
    见 _give_expected_until。返回是否已点击。
    """
    return _story_dismisser.try_dismiss(win, items, img_np, prefix="五环")


def _handle_submit_dialog(win, items, img_np):
    """处理五环提交装备流程（follow_task 的附加对话处理回调）。

    完成标记优先：对话「恭喜你完成了今天五环任务」出现 → 抛 DailyComplete
    结束任务循环（今日五环全部完成）。
    阶段一：对话「我在这里练级…」出现 → 点绿色选项「刚好我这里有，送你啦」；
    阶段二：给与面板打开 → 检索项链（含储物格切换）→ 点击选中 → 点给与按钮完成。
    给与面板打开时优先走阶段二（跨轮询续接）；
    全部储物格切换后仍未找到 → 抛 MissingEquipment 终止任务。
    兜底：无绿字选项的标准对话框（NPC 剧情台词）点屏幕中间推进/关闭
    （见 _dismiss_story_dialog）——不处理会常驻屏幕持续触发对话区 OCR。
    返回是否已处理。
    """
    # 完成标记优先：每日五环全部完成出现恭喜对话框 → 抛异常结束任务循环。
    # 「恭喜你完成了一次五环任务（今日还能再完成一次）」是单次完成的
    # 恭喜对话框，与每日完成文案相近，OCR 误读时模糊匹配会混淆，
    # 含「还能再完成」特征即非每日完成，不得结束任务。
    # 单次对话框先判（特征词直接命中即可计数，不依赖与每日文案的
    # 模糊匹配——2026-10-22 实测两轮完成因该匹配未命中漏计数）
    if _dialog_text_present(items, CONGRATS_MARK_TEXT):
        # 单次完成恭喜对话框：本轮真实完成的计数信号——置位标记并抛
        # SingleComplete 立即结束本轮 follow（对话框即完成证据，无需等
        # 任务从追踪面板消失；run_loop 捕获后直接计入——2026-11-03 实测
        # 任务消失延迟约 100 秒，期间 follow 空转且对话框关闭后补查漏计数）
        global _congrats_seen_this_follow
        if not _congrats_seen_this_follow:
            _congrats_seen_this_follow = True
            log("[五环] 检测到单次完成恭喜对话框，本轮完成", "SYS")
        raise SingleComplete()
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
    if _dialog_text_present(items, SUBMIT_DIALOG_TEXT):
        if not _click_green_option(win, items, img_np, SUBMIT_DIALOG_OPTION):
            return False  # 绿色选项尚未渲染，下次轮询再试
        log("[五环] 检测到提交装备对话，已点击「刚好我这里有，送你啦」", "SYS")
        global _give_expected_until
        _give_expected_until = time.time() + GIVE_PANEL_TIMEOUT + 5
        # 等待给与面板出现
        if not _wait_template(win, GIVE_ICON, GIVE_PANEL_TIMEOUT):
            log("[五环] 给与面板未出现", "WARN")
            return True
        return _do_give(win)
    # 兜底：无绿字选项的剧情对话（NPC 台词）→ 点屏幕中间推进/关闭。
    # 不处理会常驻屏幕，对话区模板门每轮命中、每 0.5s 全量 OCR
    return _dismiss_story_dialog(win, items, img_np)


def _do_give(win):
    """给与面板已打开：检索项链 → 点击选中 → 点击给与按钮完成给与。"""
    global _give_expected_until
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
    _give_expected_until = 0.0  # 给与完成：剧情对话点屏推进恢复正常
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
    xmax = task_tracker.panel_row_xmax(img_np)
    for txt, rect, score in items:
        if task_tracker._is_title_line(txt) and WUHUAN_TITLE in txt \
                and score >= 0.55 and rect[0] < xmax:
            anchor = rect
            break
    if anchor is None:
        return task_tracker.PICK_FAIL
    rows = []
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= xmax:
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
    task = task_tracker.find_task(
        task_tracker.group_tasks(items, task_tracker.panel_row_xmax(img_np)),
        WUHUAN_TITLE)
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


def _follow_wuhuan(win):
    """五环 follow_task 统一参数调用（领取/跟踪主链路，见参数注释）。

    signature_key：链接行归一签名（碎片行翻转不再引起重复点击）
    ignore_empty_key：到达 NPC 链接变暗的过渡帧不作变化信号
    green_picker：碎片合并后按链接标记精确定位（替身行/剩余时间行
    不可点）；require_coord：标题/内容漏检时的关键词兜底也须含坐标
    battle_resume_reclick：战斗（遭遇战/战败）打断寻路后任务签名不变时，
    战斗结束且人物站定即补点任务链接恢复寻路（重新进入战斗）——
    2026-11-14 用户规则：正常胜利推进由签名变化点新链接；战败/遭遇战
    打断后签名不变，需补点链接重新寻路（见 follow 参数说明）
    """
    return follow_task(win, WUHUAN_TITLE, prefix="五环",
                       timeout=WUHUAN_FOLLOW_TIMEOUT,
                       extra_dialog_handler=_handle_submit_dialog,
                       fallback_keywords=WUHUAN_SUB_KEYWORDS,
                       progress_key=_wuhuan_progress_key,
                       signature_key=_wuhuan_text_key,
                       green_picker=_wuhuan_link_picker,
                       ignore_empty_key=True,
                       skip_texts={},
                       require_coord=True,
                       signature_ratio=WUHUAN_SIG_RATIO,
                       battle_resume_reclick=True,
                       story_dialog_dismiss=False)


def run_loop(win, cycle_interval=3.0):
    """五环主循环：领取任务 → 跟踪到完成 → 回云游大师领取下一次。

    任务追踪中已有五环任务（续跑场景）时跳过购买装备与领取，直接跟踪。
    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取时出现活力值不足对话框（开关关闭点「那可不行，我一会再来。」
       后结束；开启点「不差那点小钱，快给我任务吧。」花钱领取继续任务）
    3. 领取时出现「互助友爱，铭记在心即可…请明天再来」对话框（今日两次已满）
    4. 跟踪期间出现「恭喜你完成了今天五环任务」对话框（今日五环全部完成）
    返回结束原因描述。
    """
    # _congrats_seen_this_follow 为模块级全局（_handle_submit_dialog 置位、
    # 本函数复位与读取）：无 global 声明时本函数内的赋值会遮蔽为局部变量，
    # 「检测到恭喜对话框 → 照常计入」快速路径永不生效（2026-11-03 实测
    # 计数退化为仅靠 8s 补查窗口、对话框关闭后漏计数——本轮完成未计入）
    global _congrats_seen_this_follow
    max_rounds = load_task_counts().get(WUHUAN_TITLE, 0)
    log(f"[五环] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")

    # 当天进度上限检查必须在准备阶段之前：已完成达上限时直接返回，
    # 不得先去布店买装备/走位（2026-11-03 实测 2/2 已达上限仍跑去
    # 前往布店，被误认为「又去做五环」）
    done = app_utils.task_progress_done(win, WUHUAN_TITLE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[五环] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[五环] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次五环任务，达到次数上限 {max_rounds}"

    # 续跑场景：任务已在追踪中则跳过购买装备（首次领取时再走完整流程）
    if not task_tracker.has_task(win, WUHUAN_TITLE):
        debug_overlay.set_phase("准备阶段")  # 看板阶段
        ok, info = _prepare(win)
        if not ok:
            return f"准备阶段失败：{info}"
        log(f"[五环] {info}", "SYS")
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
            # 点屏幕中间关闭今日全部完成对话框（防遮挡后续队列任务，
            # 用户 2026-11-03），计入并结束
            _click_screen_center(win, times=1, prefix="五环")
            done += 1
            app_utils.task_progress_add(win, WUHUAN_TITLE, done)
            log(f"[五环] {COMPLETE_INFO}（含本轮共 {done} 次），结束任务", "SYS")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        # 任务追踪中已有「五环[五环]」任务：直接跟踪，跳过领取
        if task_tracker.has_task(win, WUHUAN_TITLE):
            log("[五环] 任务追踪中已有五环任务，直接跟踪", "SYS")
        else:
            debug_overlay.set_phase("领取任务")  # 看板阶段
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
        # 提交装备步骤：由 follow_task 的附加对话处理回调接管（参数见
        # _follow_wuhuan）；剧情对话点屏走 _handle_submit_dialog 内实例
        # （带给与窗口抑制）
        _congrats_seen_this_follow = False  # 每轮跟踪前复位完成信号
        try:
            ok, info = _follow_wuhuan(win)
        except MissingEquipment as e:
            log(f"[五环] {MISSING_EQUIPMENT_INFO}（{e}），结束任务", "SYS")
            return f"{MISSING_EQUIPMENT_INFO}，结束五环任务（已完成 {done} 次）"
        except DailyComplete:
            # 恭喜对话框出现在本轮完成时：点屏幕中间关闭（防遮挡后续
            # 队列任务，用户 2026-11-03），计入本轮并持久化（对话框
            # 即完成证据；2026-10-22 前未持久化导致进度漏记）
            _click_screen_center(win, times=1, prefix="五环")
            done += 1
            app_utils.task_progress_add(win, WUHUAN_TITLE, done)
            log(f"[五环] {COMPLETE_INFO}（含本轮共 {done} 次），结束任务", "SYS")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        except SingleComplete:
            # 单次完成恭喜对话框出现：点屏幕中间一次关闭（防遮挡后续
            # 领取，用户 2026-11-03），本轮完成直接计入——跳过补查
            # （对话框可能已关闭，补查会落空漏计数；2026-11-03 教训）
            ok = True
            info = "检测到单次完成恭喜对话框，本轮完成"
            _click_screen_center(win, times=1, prefix="五环")
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
            if completion_kind == "single":
                # 单次恭喜对话框仍开着：点屏幕中间关闭，防遮挡下一轮领取
                _click_screen_center(win, times=1, prefix="五环")
        done += 1
        app_utils.task_progress_add(win, WUHUAN_TITLE, done)
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        debug_overlay.record_round()  # 统计页：完成轮次计数
        log(f"[五环] {info}（{done}/{total}）", "SYS")
        if completion_kind == "daily":
            # 补查发现今日五环全部完成：点屏幕中间关闭对话框（防遮挡
            # 后续队列任务），直接结束不再领取下一轮
            _click_screen_center(win, times=1, prefix="五环")
            return f"{COMPLETE_INFO}，结束五环任务（已完成 {done} 次）"
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次五环任务，达到次数上限 {max_rounds}"
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
