"""玄枢轶闻任务自动化。

流程：
1. 任务追踪中已有 [玄枢轶闻] 任务 → 循环跟踪任务直到完成
   （点击绿色带下划线文案，等待任务信息变化后继续点击；
   期间处理对话框绿色选项与锄遗聚宝小游戏）
2. 没有任务时，寻路到任务位置（洛阳城 93,125）并领取任务：
   - 位置识别失败一律当不在目标坐标（不按 Alt+C 重试），直接寻路
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

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard  # Alt+C 降落（封印机关破解）
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import log, human_click, precise_click, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import (is_in_battle, is_in_battle_frame,
                     watch_in_battle as battle_watch_in_battle)  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）

# 任务追踪面板中的任务标题
TASK_TITLE = "玄枢轶闻"

# ---- 寻路与领取任务（洛阳城 93,125） ----
LUOYANG_CITY = "洛阳城"
TASK_MAP_POS = (93, 125)            # 任务位置（洛阳城坐标；None 表示目标非坐标）
MAP_POS_TOLERANCE = 5               # 坐标判定容差（±5 像素）
MAP_CLICK_POS = (399, 435)          # 小地图上任务位置对应的点击点（窗口相对坐标）
TASK_LINGQU_ICON = "task_lingqu.png"           # 领取任务按钮模板
ARRIVE_TIMEOUT = 90                 # 等待寻路完成的超时（秒）
# 领取任务对话框中的绿色选项（两步）
DIALOG_OPTION_XUANSHU = "玄枢轶闻"
DIALOG_OPTION_LINGQU = "领取玄枢轶闻"
DIALOG_TIMEOUT = 10                 # 等待对话框出现的超时（秒）
# 任务过程中出现的对话框绿色选项（跟踪期间检测到则点击，按顺序）
DIALOG_OPTIONS = ["注入玄蕴试试看"]
# ---- 监天盘子任务（追踪文案「使用监天盘寻找石妖踪迹」出现即触发）----
JIANTIANPAN_TRACK_TEXT = "使用监天盘寻找石妖踪迹"  # 任务追踪触发文案
JIANTIANPAN_WORLD_ICON = "shijie_huoyungebi.png"  # 世界地图「火云戈壁」标签模板
JIANTIANPAN_MAP_CLICK_POS = (759, 435)            # 火云戈壁小地图点击点（寻路到目标附近）
JIANTIANPAN_TARGET_POS = (183, 81)                # 目标坐标（火云戈壁）
JIANTIANPAN_ARRIVE_TOLERANCE = 4                  # 到达容差（严格：仙药只在目标附近出现）
JIANTIANPAN_ARRIVE_TIMEOUT = 90                   # 寻路等待超时（秒）
JIANTIANPAN_ICON = "xuanshu_jiantianpan.png"      # 监天盘模板
JIANTIANPAN_CLICK_TIMEOUT = 10                    # 到达后等待监天盘出现的超时（秒）
TANYAO_ICON = "xuanshu_tanyao.png"                # 探妖按钮模板（使用监天盘后出现，
                                                  # 用户素材 21.png，实测匹配 1.0）
TANYAO_TIMEOUT = 10                               # 点击监天盘后等待探妖出现的超时（秒）
XIANYAO_ICON = "xuanshu_xianyao.png"              # 仙药模板（点击探妖后出现）
XIANYAO_TIMEOUT = 20                              # 等待仙药出现的超时（秒）
JIANTIANPAN_REDO_INTERVAL = 20                    # 流程失败后最短重做间隔（秒，防紧循环）
_jiantianpan_last_done = 0.0                      # 上次流程执行时间戳
# 战斗失败/平局对话框（共享常量，见 dialog_common）
# 每日次数已满文案与跳过/选项几何常量同样在 dialog_common
from .dialog_common import (DAILY_LIMIT_TEXT, DAILY_LIMIT_INFO,
                            DIALOG_LOSE_TEXTS, DIALOG_SKIP_CLICKS,
                            SCREEN_CENTER_POS,
                            OPTION_FIRST_OFFSET, OPTION_ROW_STEP, OPTION_X_OFFSET)
# 每日任务次数已满的对话文案（出现即结束任务循环）
# 需要点击屏幕中间跳过的对话框文案 → 点击次数（出现则连点屏幕中间推进
# 对话，同 kezhan.DIALOG_SKIP_TEXTS）；不同文案要推进的屏数不同，次数
# 按文案单独设置，默认先统一 4 次（DIALOG_SKIP_CLICKS），实测后逐条校准
DIALOG_SKIP_TEXTS = {
    "你是做嘛的？莫来惹老子！": 2,
    "多帮助新人，神仙都会感谢你的！": 4,
    "受害者所闻那水鬼之声出自善口技者之音，你幼时曾学过口技，又时常与人": 1,
    "第二起案子中，被打捞的尸体手脚被渔人结牢牢捆住，那渔人结并非常人所": 1,
    "少侠运气不错": 4,
    "翠花说的难道是这块石头？但是.": 4,
    "刚刚是你再说话？": 4,
    "准备好接受地理方面的考验了么": 4,
    "少侠准备好接受考验了么": 4,
    "少侠准备好接受常识类学问的考验了么": 4,
    "少侠可是来替小乔试装的？": 4,
    "看少侠的表情，旺小乔不满意？": 4,
    "少侠的脸色有点难看": 4,
    "留香阁的凤姑娘探听到了一些有关玄石的消息": 4,  # 领取后 NPC 追问（无选项，须屏幕中间推进）
    "看来要破解的便是此地的封印了。":1,
    "怎么没有反应": 5,
}
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
    "我倒数三个数后大家一起出拳":"出石头",
    "看打！":"看我怎么收拾你",
    "不知道这里有没有那条锦鲤鱼": "先钓一下再说！",
    "这就要少侠自己尝试了":"试试",
    "少侠习惯就好":"试试",
    "少侠想换什么款式":"试试",
    "略略略略略":"都别吵啦",
    "要么要钱，要么要命，你自己选吧":"看打",
    "少侠救我":"看我的",
    "嘿，又来一个上当的，今日又能提前完成修行了！":"竟是一只石妖，看打！"
}
# 选项无法被 OCR 识别（如单字选项「茶」）时，按序号点击：
# 选项为问题文案下方的纵向列表，第 idx 项（对话框文案 → 序号）
DIALOG_OPTION_INDEX_MAP = {
    "大唐人最喜欢喝什么": 3,  # 茶（咖啡、果汁之后的第 3 项）
    "太阳的别称是什么": 2,    # 金乌（铜乌/金乌/银乌三选项中的第 2 项；
                              # 实测区域 OCR 漏读「金乌」行致文案点击失败，
                              # 按序号兜底点击——2026-10-15）
}
# 序号点击的坐标参数（相对问题行 rect）：首项纵偏移、行间距、横向偏移
# （实测三选项文字中心：咖啡 +12、果汁 +30、茶 +48，间距 18px）
# ---- 封印机关破解（对话框「看来要破解的便是此地的封印了。」出现后） ----
JIGUAN_DIALOG_TEXT = "看来要破解的便是此地的封印了。"
JIGUAN_CLICK_ORDER = ("xuanshu_jiguanhuang.png",   # 点击顺序：黄→蓝→红（用户 2026-10-15 指定）
                      "xuanshu_jiguanlan.png",
                      "xuanshu_jiguanhong.png")
JIGUAN_CLICK_WAIT = 5        # 每次点击机关后等待（秒），随后检查任务追踪
JIGUAN_FIND_TIMEOUT = 3     # 单个机关模板查找超时（秒，找不到跳过继续下一个）
JIGUAN_THRESHOLD = 0.8       # 机关模板匹配阈值

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
# 跟踪轮询的 OCR 区域见 task_tracker（TRACK_REGION/DIALOG_REGION/OCR_REGIONS）
FOLLOW_TRACK_REGION = task_tracker.TRACK_REGION
FOLLOW_OCR_REGIONS = task_tracker.OCR_REGIONS


def _at_target(pos):
    """判断位置是否在任务目标附近（目标非坐标时恒 False）。"""
    if TASK_MAP_POS is None:
        return False
    return game_map.is_at_position(pos, LUOYANG_CITY,
                                   TASK_MAP_POS[0], TASK_MAP_POS[1],
                                   MAP_POS_TOLERANCE)


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


def _jiantianpan_active(items):
    """追踪面板是否出现监天盘子任务文案（跨行拼接后模糊匹配）。"""
    panel_txt = "".join(t for t, r, _ in items if r[0] < task_tracker.PANEL_MAX_X)
    return _fuzzy_contains(norm_text(panel_txt), JIANTIANPAN_TRACK_TEXT)


def _at_huoyun_target(pos):
    """是否已在火云戈壁目标附近（严格容差；地名抗 OCR 误读）。"""
    if pos is None:
        return False
    x, y = JIANTIANPAN_TARGET_POS
    if not (abs(pos[1] - x) <= JIANTIANPAN_ARRIVE_TOLERANCE
            and abs(pos[2] - y) <= JIANTIANPAN_ARRIVE_TOLERANCE):
        return False
    name = pos[0].replace("雲", "云").strip()
    if name == "火云戈壁":
        return True
    return len(name) >= 3 and difflib.SequenceMatcher(
        None, name, "火云戈壁").ratio() >= 0.7


def _wait_jiantianpan_arrive(win, timeout=JIANTIANPAN_ARRIVE_TIMEOUT):
    """等待走到火云戈壁目标附近（严格容差 + 连续两次同坐标确认停稳）。

    点击小地图后人物可能路过目标附近但不停下，必须停稳才算到达
    （仙药只在目标附近出现，附近要求严格）。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if _at_huoyun_target(pos) and pos == last:
            return True, f"已到达火云戈壁目标附近 {pos[0]}[{pos[1]},{pos[2]}]（严格容差）"
        last = pos
        time.sleep(2.0)
    return False, "前往火云戈壁目标附近超时（严格容差未达成）"


def _click_template_wait(win, icon, label, timeout):
    """轮询等待模板出现并点击。返回 (是否成功, 描述)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = game_map.find_template(win, icon)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[玄枢轶闻] 已点击「{label}」@({pt[0]},{pt[1]})", "SYS")
            return True, f"已点击「{label}」"
        time.sleep(0.5)
    return False, f"未找到「{label}」模板（{timeout}s 超时）"


def _handle_jiantianpan(win, items, img_np):
    """监天盘子任务（follow_task 附加处理回调）。

    追踪出现「使用监天盘寻找石妖踪迹」时执行：
    世界地图点火云戈壁 → 小地图点 (744,441) 寻路 → 严格等待到达
    火云戈壁 183,81 附近 → 点击监天盘 → 点击探妖（用户 2026-10-09
    提供模板）→ 等待仙药出现并点击。
    已在目标附近时跳过寻路直接点监天盘（失败重试幂等）。
    流程失败后 JIANTIANPAN_REDO_INTERVAL 内不重试（防紧循环）。
    返回是否已处理（执行过流程即 True，即使失败）。
    """
    global _jiantianpan_last_done
    if not _jiantianpan_active(items):
        return False
    if time.time() - _jiantianpan_last_done < JIANTIANPAN_REDO_INTERVAL:
        return False
    _jiantianpan_last_done = time.time()
    log("[玄枢轶闻] 检测到「使用监天盘寻找石妖踪迹」，执行监天盘流程", "SYS")
    if not _at_huoyun_target(game_map.read_map_position(win)):
        ok, info = game_map.open_mini_map_city(win, JIANTIANPAN_WORLD_ICON, "火云戈壁")
        if not ok:
            log(f"[玄枢轶闻] 监天盘流程寻路失败: {info}", "WARN")
            game_map.close_maps(win)
            return True
        human_click(win, JIANTIANPAN_MAP_CLICK_POS[0], JIANTIANPAN_MAP_CLICK_POS[1])
        log("[玄枢轶闻] 已点击火云戈壁小地图寻路点，自动寻路中", "SYS")
        time.sleep(1.0)  # 等待自动寻路启动
        game_map.close_maps(win)  # 关闭残留地图，避免遮挡监天盘
        ok, info = _wait_jiantianpan_arrive(win)
        if not ok:
            log(f"[玄枢轶闻] {info}", "WARN")
            return True
        log(f"[玄枢轶闻] {info}", "SYS")
    ok, info = _click_template_wait(win, JIANTIANPAN_ICON, "监天盘", JIANTIANPAN_CLICK_TIMEOUT)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
        return True
    # 使用监天盘后点「探妖」（同一右下角按钮区，用户 2026-10-09 确认顺序）
    ok, info = _click_template_wait(win, TANYAO_ICON, "探妖", TANYAO_TIMEOUT)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
        return True
    ok, info = _click_template_wait(win, XIANYAO_ICON, "仙药", XIANYAO_TIMEOUT)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
    else:
        log(f"[玄枢轶闻] {info}", "SYS")
    return True


from .dialog_common import (_click_green_option_once as _dc_click_green_option_once,
                            _click_dialog_option_by_index as _dc_click_dialog_option_by_index,
                            _click_screen_center as _dc_click_screen_center,
                            _fuzzy_contains, _dialog_text_present,
                            close_scene_panels)


def _click_green_option_once(win, items, img_np, text, prefix="玄枢轶闻"):
    """前缀包装（共享实现在 dialog_common；默认玄枢轶闻前缀）。"""
    return _dc_click_green_option_once(win, items, img_np, text, prefix)


def _click_dialog_option_by_index(win, items, dtxt, idx):
    """玄枢轶闻前缀包装（共享实现在 dialog_common）。"""
    return _dc_click_dialog_option_by_index(win, items, dtxt, idx, "玄枢轶闻")


def _option_present(items, text):
    """选项行是否可见（以 text 开头的行，限定对话框区域）。"""
    return any(t.startswith(text) and s >= 0.55
               and r[0] >= task_tracker.PANEL_MAX_X
               for t, r, s in items)


def _wait_option_visible(win, text, abort_text, timeout=4.0):
    """等对话框渲染出选项行（以 text 开头）；期间出现 abort_text 返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if abort_text and _dialog_text_present(items, abort_text):
            return False
        if _option_present(items, text):
            return True
        time.sleep(0.5)
    return False


def _option_gone(win, text, timeout=3.0):
    """点击后选项行是否消失（对话框已推进）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if not _option_present(items, text):
            return True
        time.sleep(0.5)
    return False


def _click_dialog_green_option(win, text, timeout=DIALOG_TIMEOUT, abort_text=None):
    """等待对话框出现，点击其中指定文案的绿色选项。

    等待期间若出现 abort_text 文案（如每日次数已满）则提前中止。
    高性能：选项坐标已缓存 → 先等选项渲染出现（点前确认）→ 按缓存
    坐标点击 → 点后确认选项消失（推进）；任一步不满足则清缓存回退
    OCR 识别路径（缓存中心被合并行毒化的实测教训；点后单端校验会把
    「点击已生效、对话框快速推进」误判为未生效）。返回 (是否成功, 描述)。
    """
    if app_utils.high_perf_on():
        pos = app_utils.fixed_pos_get(f"option_{text}", win)
        if pos is not None:
            if _wait_option_visible(win, text, abort_text):
                time.sleep(0.3)  # 渲染稳定
                human_click(win, pos[0], pos[1])
                log(f"[玄枢轶闻][高性能] 已按缓存坐标点击「{text}」 ({pos[0]}, {pos[1]})", "SYS")
                if _option_gone(win, text):
                    return True, f"已按缓存坐标点击对话框绿色选项「{text}」"
                app_utils.fixed_pos_clear(f"option_{text}")
                log(f"[玄枢轶闻][高性能] 缓存坐标点击「{text}」未生效，清缓存回退识别", "WARN")
            else:
                app_utils.fixed_pos_clear(f"option_{text}")
                log(f"[玄枢轶闻][高性能] 点击前选项「{text}」未出现"
                    f"（对话框未开或状态已变），清缓存回退识别", "WARN")
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if abort_text and _dialog_text_present(items, abort_text):
            return False, f"检测到「{abort_text}」"
        if _click_green_option_once(win, items, img_np, text):
            return True, f"已点击对话框绿色选项「{text}」"
        time.sleep(0.5)
    return False, f"未找到对话框绿色选项「{text}」"


def navigate_to_task(win):
    """寻路到玄枢轶闻任务位置（洛阳城 93,125）并领取任务。

    1. 场景前置：关闭已打开的小地图/世界地图/物品栏
    2. 识别当前位置（失败一律当不在目标坐标，不重试）
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
    close_scene_panels(win, "玄枢轶闻")

    # 识别失败一律当不在目标坐标处理：不按 Alt+C 重试（重试无收益
    # 且拖慢轮次），直接走世界地图路径，寻路完成后位置自然恢复
    pos = game_map.read_map_position(win)
    if pos is None:
        name = loc = "识别失败"
        log("[玄枢轶闻] 位置识别失败，按不在目标坐标处理", "WARN")
    else:
        name, x, y = pos
        loc = f"{name}[{x},{y}]"

    at_target = _at_target(pos)
    if at_target:
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

    if not at_target:
        human_click(win, MAP_CLICK_POS[0], MAP_CLICK_POS[1])
        log(f"[玄枢轶闻] 已点击小地图任务点，自动寻路中", "SYS")
        time.sleep(1.0)  # 等待自动寻路启动
        # 清理：关闭仍打开的小地图（点击任务点后仍打开）、世界地图（通常已自动关闭）与物品栏
        close_scene_panels(win, "玄枢轶闻")

    # 等待寻路完成
    ok, info = wait_arrive(win)
    if not ok:
        return False, info, None
    log(f"[玄枢轶闻] {info}", "SYS")

    # 领取任务（按钮随站位渲染位置变化，必须识图定位，不缓存）
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
    # 命中时把 OCR 结果带回，供 follow_task 复用，省一次截图+OCR
    deadline = time.time() + DIALOG_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            items, img_np = game_map.ocr_regions(img, (task_tracker.TRACK_REGION,))
            items2, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _dialog_text_present(items2, DAILY_LIMIT_TEXT):
                return False, DAILY_LIMIT_INFO, None
            if task_tracker.has_task_in(items, img_np, TASK_TITLE):
                # 任务刚出现时领取对话框还在切换/面板刚渲染，布局可能未稳定
                # （实测首帧坐标点击落空、未开始寻路）：等待约 1.5s 后重新
                # 截帧 OCR，把稳定帧带回 follow_task 保证首次点击落点准确
                time.sleep(1.5)
                img2 = win.capture(force_activate=False)
                if img2 is not None:
                    items3, img_np3 = game_map.ocr_regions(
                        img2, (task_tracker.TRACK_REGION,))
                    if task_tracker.has_task_in(items3, img_np3, TASK_TITLE):
                        return True, "已领取任务", (items3, img_np3)
                return True, "已领取任务", (items, img_np)  # 复检失败按首帧返回
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


def _current_task_sig(win, title):
    """当前任务签名（仅 OCR 追踪面板区域）；任务不存在返回 ""。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = game_map.ocr_region(img, task_tracker.TRACK_REGION)
    task = task_tracker.find_task(task_tracker.group_tasks(items), title)
    if task is None:
        return ""
    return task_tracker.task_signature(task)


def _task_done_or_changed(win, title, sig0):
    """任务追踪是否消失或签名变化（相对 sig0；sig0 None 表示读不到，
    此时不判变化）。"""
    sig = _current_task_sig(win, title)
    if sig is None:
        return False
    if sig == "":
        return True  # 任务从追踪中消失
    return sig0 is not None and sig0 != "" and not _signatures_similar(sig, sig0)


def _play_jiguan_puzzle(win, title):
    """封印机关破解（用户 2026-10-15 流程）：

    提示对话框点击一次关闭 → Alt+C 降落 → 按 黄→蓝→红 顺序识别并
    点击机关模板；每次点击后等待 5s，任务追踪消失或签名变化即停止
    （不再点剩余机关）。返回 (是否执行了流程, 描述)。
    """
    # 1. 点击一次关闭提示对话框（屏幕中间；该对话框无选项）
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[玄枢轶闻] 已点击关闭封印提示对话框", "SYS")
    time.sleep(0.5)
    # 2. Alt+C 降落（飞行状态下落地，机关才可见可点）
    Keyboard.key_hot("alt", "c")
    log("[玄枢轶闻] 已按 Alt+C 降落", "SYS")
    time.sleep(1.0)
    # 3. 按序点击机关（每次点击后 5s 检查任务追踪）
    sig0 = _current_task_sig(win, title)
    clicked = 0
    for icon in JIGUAN_CLICK_ORDER:
        deadline = time.time() + JIGUAN_FIND_TIMEOUT
        pt = None
        while time.time() < deadline:
            pt = game_map.find_template(win, icon, threshold=JIGUAN_THRESHOLD)
            if pt is not None:
                break
            time.sleep(0.5)
        if pt is None:
            log(f"[玄枢轶闻] 未找到机关 {icon}，跳过", "WARN")
            continue
        human_click(win, pt[0], pt[1])
        clicked += 1
        log(f"[玄枢轶闻] 已点击机关 {icon} @({pt[0]},{pt[1]})，等待 {JIGUAN_CLICK_WAIT}s", "SYS")
        time.sleep(JIGUAN_CLICK_WAIT)
        if _task_done_or_changed(win, title, sig0):
            return True, f"封印破解完成（点击 {clicked} 个机关后任务追踪变化）"
    return clicked > 0, f"封印破解流程结束（点击 {clicked} 个机关，任务追踪未变化）"


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
    close_scene_panels(win, "玄枢轶闻")

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
        # 兜底：关闭可能弹出的背包/地图面板（5 秒一次；quiet——未打开
        # 不点击的路径不打日志，避免小游戏期间每 5 秒刷 2 条 INFO）
        if now - last_panel_check >= 5.0:
            last_panel_check = now
            close_scene_panels(win, "玄枢轶闻", quiet=True)
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
        # 快速点击：杂气团块小且移动快，跳过拟人化弧线移动与落点抖动
        precise_click(win, tgt[0], tgt[1], move_duration=0.02)
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




def _frames_equal(a, b):
    """两帧像素完全一致（全分辨率逐像素比较，约 2-4ms）。

    仅全等才复用上一帧 OCR 结论；降采样比较会把微小变化抹平，
    导致缓慢浮现的对话框延迟检出，故不用。
    """
    if a is None or b is None or a.size != b.size:
        return False
    return bool((np.asarray(a) == np.asarray(b)).all())



def _confirm_task_gone(win, title, fallback_texts=()):
    """确认任务确实从追踪面板消失（连续轮询未见且无战斗遮挡）。

    OCR 偶发漏读（标题误读、面板瞬时遮挡）会误判任务消失——五环实测
    误判「任务消失」导致完成次数虚增、任务提前结束；战斗画面遮挡追踪
    面板时 OCR 必然读不到任务，战斗中不计入确认窗口（延长窗口等战斗
    结束后再观察）。返回 True=确认消失（任务完成）。

    fallback_texts：标题漏检防误判兜底——修罗实测标题行整个被区域
    OCR 漏检（彩色徽标前缀），领取后首次检查即误判「任务消失」，
    3s 假完成一轮、下一轮卡等 20s 领取按钮（任务激活时按钮不出现）。
    内容链接行「前往…消灭修罗」检测稳定，面板左区内出现任一兜底
    串即判定任务仍在（立刻中止确认）。

    战斗结束后的延迟优化（实测战斗画面已清 → 返程启动等 3~4s）：
    战斗线程的 2s 结束确认（防回合间隙误判）与任务消失确认原本串行，
    合计 4~6s。现在战斗画面已清但战斗线程确认未完成时先行收集证据
    （面板可读就 OCR；任务还在则立即中止，不影响正确性），最终判定
    仍以战斗线程确认为门——回合间隙的画面空档期间计数会被「战斗画面
    再出现」分支清零，不会累积成误判。
    """
    deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
    # 延长总上限：战斗/截窗失败不断延长窗口的硬顶（约为基本窗口 4 倍）。
    # 超过上限仍未决 → 返回 False 让调用方继续跟踪（由外层战斗检查
    # 等战斗结束后重新确认），避免长时间遮挡下误判完成
    hard_deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT * 4
    gone_polls = 0
    while time.time() < deadline and time.time() < hard_deadline:
        img = win.capture(force_activate=False)
        if img is None:
            # 截窗失败不计入确认窗口（与战斗帧同待遇，防瞬时失败误判）
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(TASK_GONE_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            # 战斗画面又出现（回合间隙结束/战斗未真结束）：此前计数作废，
            # 延长确认窗口。单帧匹配在战斗动画间隙会漏判（实测 2.5 分钟
            # 战斗中被误判任务消失完成），因此最终判定还须战斗线程确认
            gone_polls = 0
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(2.0)
            continue
        items, img_np = game_map.ocr_regions(img, (FOLLOW_TRACK_REGION,))
        if task_tracker.find_task(task_tracker.group_tasks(items), title) is not None:
            return False  # 任务重新出现：此前为漏读，继续跟踪
        if fallback_texts:
            # 标题行被区域 OCR 漏检时 find_task 找不到任务：用内容链接行
            # 兜底串判定任务仍在（修罗「前往…消灭修罗」检测稳定，
            # 面板左区+只在任务存在时出现），立刻中止确认防假完成
            for txt, rect, score in items:
                if score >= 0.55 and rect[0] < task_tracker.PANEL_MAX_X \
                        and any(ft in txt for ft in fallback_texts):
                    return False
        gone_polls += 1
        if battle_watch_in_battle():
            # 画面已清但战斗线程的 2s 结束确认未完成：证据已收集
            # （任务还在则上面已 return False），不最终判定，
            # 等战斗线程确认后的下一次轮询立即出结论
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(TASK_GONE_POLL_INTERVAL)
            continue
        if gone_polls >= TASK_GONE_CONFIRM_POLLS:
            return True
        time.sleep(TASK_GONE_POLL_INTERVAL)
    if time.time() >= hard_deadline:
        return False  # 延长耗尽仍无法确认：继续跟踪，稍后重新确认
    return True  # 确认窗口内始终未见任务：视为完成


def _confirm_guard_dialog(win, img, extra_dialog_handler):
    """确认轮询跳出前的对话框复核（负面条件检查）。

    确认轮询只 OCR 追踪面板（快速路径），但跳出后要点击任务绿字——
    确认窗口期可能弹出对话（如五环到达 NPC 的开场白，实测 2026-10-13
    跳出点击「去找长安」绿字时对话正打开）。补 OCR 对话框区域交给
    extras 处理：命中（对话被处理）返回 True，调用方回退正常轮询；
    无 extras 或未命中返回 False 放行。
    本守卫只作辅助检查：extras 抛出的业务异常（MissingEquipment/
    DailyComplete 等）一律吞掉放行——正常轮询路径仍会按设计处理，
    守卫里提前触发会把「给与面板关闭动画残留」误判成任务终止。
    """
    if extra_dialog_handler is None:
        return False
    try:
        items_d, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        return bool(extra_dialog_handler(win, items_d, np.asarray(img)))
    except Exception:
        return False


def follow_task(win, title=TASK_TITLE, timeout=FOLLOW_TIMEOUT, prefix="玄枢轶闻",
                extra_dialog_handler=None, green_order=None, progress_key=None,
                green_picker=None, fallback_keywords=None, first_items=None,
                signature_key=None, ignore_empty_key=False, skip_texts=None,
                pre_click_handler=None):
    """循环跟踪任务直到完成（prefix 供其他任务复用，如五环）。

    每轮：点击任务当前第一个绿色文案 → 等待任务信息变化（内容更新）
    → 进入下一轮；任务从追踪面板消失即完成。
    等待期间处理对话框（DIALOG_OPTION_MAP 指定绿字、skip_texts
    点屏幕中间跳过（None 用默认 DIALOG_SKIP_TEXTS，{} 禁用——
    五环等自有对话处理的调用方禁用，防误触；文案→点击次数字典，
    不同文案按屏数配置不同连点次数）、DIALOG_OPTIONS 已知绿字、
    战斗失败「你输了」点击任务绿字推进）与锄遗聚宝小游戏。
    战斗中跳过识别（任务追踪与坐标不可用），等待战斗结束。

    extra_dialog_handler：附加对话框处理回调 (win, items, img_np) → 是否已处理，
    供其他任务注入专属对话流程（如五环的提交装备）；回调中抛出的异常向上传播，
    供调用方做任务级终止。
    pre_click_handler：点击任务绿字前的检查回调 (win, items, img_np)，可抛异常
    终止跟踪（如五倍锁妖网任务在寻路开始前拦截，人物不起步直接替换领取）。
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
    signature_key：任务文本签名回调 (items, img_np) → str，替代默认的
    标题+首行签名参与相似度比较（五环链接行被 OCR 拆成链接/计数两个
    碎片行且输出顺序抖动，默认签名反复翻转导致重复点击；五环改用
    行序无关的排序拼接签名）。
    ignore_empty_key：进度键为空时不作为变化信号（五环到达 NPC 时
    链接变暗、亮绿关键词集合为空再回弹，属过渡帧抖动；客栈的「」
    是合法终态，默认不启用）。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    rounds = 0
    task_seen = False  # 本轮跟踪中任务是否曾被识别到：完成须满足
                       # 「无→有→无」——从未出现即确认消失是假完成
                       # （实测客栈 3s 一轮 0 次点击假完成，任务与对话框
                       # 都没执行；此时领取大概率失败，应报错回主循环重试）
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
            if _confirm_task_gone(win, title, fallback_keywords):
                if not task_seen:
                    # 诊断：转储追踪面板 OCR 行——宽松匹配（领取校验
                    # has_task）命中而分组匹配（find_task）漏任务时，
                    # 标题行形态即根因（五环实测死循环 2026-10-15）
                    try:
                        img2 = win.capture(force_activate=False)
                        if img2 is not None:
                            items2, _ = game_map.ocr_regions(
                                img2, (task_tracker.TRACK_REGION,))
                            dump = " | ".join(f"「{t[0]}」" for t in items2
                                              if t[2] >= 0.55)
                            log(f"[{prefix}] 追踪面板 OCR: {dump or '(无)'}", "WARN")
                    except Exception:
                        pass
                    return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
            continue  # 瞬时漏读：任务仍在，继续跟踪
        task_seen = True
        sig = _sig_of(items, img_np, task)
        if last_clicked_sig is not None and _signatures_similar(sig, last_clicked_sig) \
                and not progressed:
            # 任务信息未真实变化（OCR 噪声抖动）→ 不重复点击，继续等待
            # progressed=True 时（进度键已变化）允许继续点击下一个绿字。
            # 阻塞期仍须处理任务对话（如五环提交装备）：对话框出现时
            # 任务签名不变，不处理会一直卡到跟踪超时
            if extra_dialog_handler is not None:
                extra_dialog_handler(win, items, img_np)
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 点击前检查（可选回调，可抛异常终止跟踪）：在点任务绿字之前
        # 调用——需要替换的任务（如锁妖网）在寻路开始前拦截，人物
        # 不起步、原地进入替换领取（原流程先点链接开始寻路，内层轮询
        # 才检测到，白走一段再折返）
        if pre_click_handler is not None:
            pre_click_handler(win, items, img_np)
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
                if is_in_battle_frame(img):
                    time.sleep(2.0)  # 战斗画面遮挡面板：跳过本帧（免空转 OCR）
                    continue
                items, img_np = game_map.ocr_regions(img, (FOLLOW_TRACK_REGION,))
                task2 = task_tracker.find_task(task_tracker.group_tasks(items), title)
                if task2 is None:
                    if _confirm_task_gone(win, title, fallback_keywords):
                        if not task_seen:
                            return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                        return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
                    changed_once = False  # 面板瞬时不可读：回退正常轮询
                    continue
                sig2 = _sig_of(items, img_np, task2)
                text_changed = not _signatures_similar(sig2, sig)
                key = progress_key(items, img_np) if progress_key is not None else None
                key_changed = key is not None and not first_poll \
                    and key != baseline_key
                if ignore_empty_key and (not key or not baseline_key):
                    key_changed = False  # 空键为过渡帧，不作信号
                if changed_kind == "text":
                    if text_changed and _signatures_similar(sig2, changed_sig):
                        # 跳出前复核对话框：确认轮询只 OCR 追踪面板（快速
                        # 路径），而确认窗口期可能弹出对话（如五环到达 NPC
                        # 的开场白，实测 2026-10-13）——补 OCR 对话框区域
                        # 交给 extras 处理，命中则回退正常轮询，避免对着
                        # 打开中的对话框点任务绿字
                        if _confirm_guard_dialog(win, img, extra_dialog_handler):
                            changed_once = False
                            continue
                        log(f"[{prefix}] 变化确认: 任务签名「{sig}」→「{sig2}」", "INFO")
                        progressed = True
                        last_items = (items, img_np)
                        break
                    changed_once = False
                    continue
                if key_changed and key == changed_key:
                    if _confirm_guard_dialog(win, img, extra_dialog_handler):
                        changed_once = False
                        continue
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
            extras_handled = False
            if extra_dialog_handler is not None:
                extras_handled = extra_dialog_handler(win, items, img_np)
            if extras_handled:
                # extras 已处理（关闭对话框/点选项/点链接）：本帧是处理
                # 前的陈旧帧，签名变化与对话框判定均不可信，推迟到下一
                # 轮询（实测 2026-10-13 五环：开场白对话与面板变化同帧，
                # 若继续用陈旧帧跳出点击任务绿字会误点打开中的对话框）
                changed_once = False
                continue
            # 战斗中：跳过本轮识别（任务追踪与坐标不可用）；复用本帧
            if is_in_battle_frame(img):
                continue
            # 封印机关破解：提示对话框出现则完整处理（点击关闭 → 降落 →
            # 按序点机关，任务追踪变化即停）
            if _dialog_text_present(items, JIGUAN_DIALOG_TEXT):
                ok2, info2 = _play_jiguan_puzzle(win, title)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            # 锄遗聚宝优先：出现则完整处理（复用本帧模板匹配，不重复截窗）
            if game_map.find_template_in(img, CHUYIJUEBAO_ICON) is not None:
                ok2, info2 = _play_chuyijuebao(win)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            task2 = task_tracker.find_task(task_tracker.group_tasks(items), title)
            if task2 is None:
                if _confirm_task_gone(win, title, fallback_keywords):
                    if not task_seen:
                        return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                    return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
                changed_once = False  # 瞬时漏读：任务仍在，继续等待
                continue
            task_seen = True
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
                    if ignore_empty_key and (not key or not baseline_key):
                        key_changed = False  # 空键为过渡帧，不作信号
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
            # 文案匹配为模糊匹配（逐行与全文拼接，抗 OCR 个别字误读与换行拆分）。
            # extras 已处理过本帧（如点过开打选项/追踪链接）时跳过——
            # 防止回调点击后的陈旧帧继续命中内置表造成同帧双点（审计契约问题 1）
            if extras_handled:
                continue
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
                    log(f"[{prefix}] 检测到「你输了/平局」对话，继续点击任务绿字推进", "SYS")
                    handled = True
                else:
                    log(f"[{prefix}] 「你输了/平局」对话中任务绿字点击失败: {info3}", "WARN")
            if not handled:
                for dtxt, clicks in (DIALOG_SKIP_TEXTS.items()
                                     if skip_texts is None
                                     else skip_texts.items()):
                    if _dialog_text_present(items, dtxt):
                        # 命中即按文案对应次数连点推进（多屏对话无需枚举
                        # 后续文案，漏推进由下一轮询 OCR 自愈）；命中文案
                        # 打日志便于排查
                        log(f"[{prefix}] 跳过对话命中文案「{dtxt}」，连点 {clicks} 次", "SYS")
                        _dc_click_screen_center(win, times=clicks, prefix=prefix)
                        handled = True
                        break
            if not handled:
                for opt in DIALOG_OPTIONS:
                    if _click_green_option_once(win, items, img_np, opt, prefix=prefix):
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
        ok, info = follow_task(win, extra_dialog_handler=_handle_jiantianpan)
        return ("done", info) if ok else ("fail", info)

    # 没有任务 → 寻路到任务位置并领取任务
    ok, info, pre_items = navigate_to_task(win)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔；复用领取校验的 OCR 结果
    ok, info = follow_task(win, first_items=pre_items,
                           extra_dialog_handler=_handle_jiantianpan)
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
    done = app_utils.task_progress_done(win, TASK_TITLE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[玄枢轶闻] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[玄枢轶闻] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次玄枢轶闻任务，达到次数上限 {max_rounds}"
    round_no = 0
    while True:
        round_no += 1
        log(f"[玄枢轶闻] ==== 第 {round_no} 轮 ====", "SYS")
        status, info = run_cycle(win)
        if status == "done":
            done += 1
            app_utils.task_progress_add(win, TASK_TITLE, done)
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
