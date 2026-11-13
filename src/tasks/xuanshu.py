"""玄枢轶闻任务自动化。

流程：
1. 任务追踪中已有 [玄枢轶闻] 任务 → 循环跟踪任务直到完成
   （点击绿色带下划线文案，等待任务信息变化后继续点击；
   期间处理对话框绿色选项与锄遗聚宝小游戏；钓鱼小游戏——对话
   「不知道这里有没有那条锦鲤鱼」→ 选项「先钓一下再说！」→ 窗口标题
   xuanshu_diaoyu.png 出现，闭环处理与客栈破除封印完全一致（共享
   pochufengyin 模块；仅标题模板与推进方式不同——钓鱼开始后按钮为
   「长按」，打法为长按运输 + 短段长按维持：长按推过目标带上缘→
   松手回落→回 C 后反复短按保持在 C 附近，破除封印为「点击」冲刺连点））
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
import time

import cv2
import numpy as np

from ..core.app_utils import log, human_click, norm_text
from ..core import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from ..device import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from ..minigames import pochufengyin as _pochu_minigame  # 破除封印/钓鱼共用小游戏（闭环实现）
from ..minigames import chuyijuebao as _chuyijuebao_minigame  # 锄遗聚宝阻塞循环
from ..minigames.jiguan import (JIGUAN_CLICK_ORDER, JIGUAN_DIALOG_TEXT,
                               JIGUAN_THRESHOLD, _jiguan_link_picker,
                               _jiguan_link_progress)  # 机关触发/链接
from ..minigames import jiguan as _jiguan_minigame  # 封印机关阻塞循环
from ..minigames.chuyijuebao import (CHUYIJUEBAO_ICON,
                                    CHUYIJUEBAO_TIMEOUT)  # 锄遗聚宝入口/超时
from ..device import task_tracker  # 任务追踪面板操作
from ..device import dialog_common  # 对话框共享组件（选项缓存点击）
from ..workflows.follow import _signatures_similar, follow_task
from ..device.battle import is_in_battle  # 战斗状态判定（统一在 battle.py 中实现）
from ..core import debug_overlay  # 调试信息悬浮层（任务进度更新）
from ..core.settings import load_task_counts  # 任务次数上限（通用设置）

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
# 领取链路的「会落到世界画面的点击」前置门（2026-11-13 用户实机教训：
# 已回到领取点附近却又走开到 [117,136]、下一轮才领到）。寻路未完全停稳
# 就点领取按钮/缓存选项时，点击会落到游戏世界画面：既重新指定目的地把
# 人物带走，又把刚渲染的领取对话框关掉，领取必失败。两道门：
#  ① 到达后先确认人物站定（位置文字区像素比对，零 OCR，机制与标定同
#     剧情点屏的 STORY_MOVE_*：45 对真实帧，站立帧差 ≤0.26/行走 ≥0.50）；
#  ② 缓存选项点击前**同一帧**必须命中对话框边框模板——世界画面里的同
#     文案文字（NPC 头顶浮标、任务变化浮字）同样以选项文案开头出现在
#     对话框区 OCR 里，只看文字会把「没开对话框」判成「选项已出现」；
#     点击前再复检一次，防 0.3s 渲染等待内对话框关闭。
CLAIM_STILL_POLL_INTERVAL = 0.3     # 站定轮询间隔（秒）
CLAIM_STILL_TIMEOUT = 6.0           # 站定等待上限（秒）；超时按本轮失败返回
# ---- 监天盘子任务（追踪文案「使用监天盘寻找石妖踪迹」出现即触发）----
JIANTIANPAN_TRACK_TEXT = "使用监天盘寻找石妖踪迹"  # 任务追踪触发文案
JIANTIANPAN_WORLD_ICON = "shijie_huoyungebi.png"  # 世界地图「火云戈壁」标签模板
JIANTIANPAN_MAP_CLICK_POS = (759, 435)            # 火云戈壁小地图点击点（寻路到目标附近）
JIANTIANPAN_TARGET_POS = (180, 86)                # 目标坐标（火云戈壁）
JIANTIANPAN_ARRIVE_TOLERANCE = 4                  # 到达容差（严格：仙药只在目标附近出现）
JIANTIANPAN_ARRIVE_TIMEOUT = 90                   # 寻路等待超时（秒）
JIANTIANPAN_ICON = "xuanshu_jiantianpan.png"      # 监天盘模板
JIANTIANPAN_THRESHOLD = 0.65                      # 监天盘模板匹配阈值（实测渲染分数偏低，
                                                  # 0.8 漏检——用户 2026-10-17 指定）
JIANTIANPAN_CLICK_TIMEOUT = 10                    # 到达后等待监天盘出现的超时（秒）
TANYAO_ICON = "xuanshu_tanyao.png"                # 探妖按钮模板（使用监天盘后出现，
                                                  # 用户素材 21.png，实测匹配 1.0）
TANYAO_TIMEOUT = 10                               # 点击监天盘后等待探妖出现的超时（秒）
XIANYAO_ICON = "xuanshu_xianyao.png"              # 仙药模板（点击探妖后出现）
XIANYAO_TIMEOUT = 20                              # 等待仙药出现的超时（秒）
JIANTIANPAN_REDO_INTERVAL = 20                    # 流程失败后最短重做间隔（秒，防紧循环）
_jiantianpan_last_done = 0.0                      # 上次流程执行时间戳
# 每日次数已满文案、遮挡对话框处理（其余对话常量已由 follow 引擎内置）
from ..device.dialog_common import (DAILY_LIMIT_TEXT, DAILY_LIMIT_INFO,
                            clear_blocking_dialog, MoveWatch)
# 每日任务次数已满的对话文案（出现即结束任务循环）
# 封印机关 / 锄遗聚宝的常量与纯检测逻辑见 minigames/jiguan.py、
# minigames/chuyijuebao.py（触发与阻塞循环仍在本任务模块）
# ---- 钓鱼小游戏（玄枢轶闻专属：对话框「不知道这里有没有那条锦鲤鱼」→
# 选项「先钓一下再说！」点击后出现）----
# 与客栈破除封印同一款进度条对准小游戏：闭环实现、开始按钮、目标高亮带、
# 进度条测量区域完全共用（pochufengyin 共享模块），仅窗口标题模板与
# 推进方式不同：钓鱼开始后按钮变为「长按」（xuanshu_diaoyu_changan.png），
# 破除封印为「点击」冲刺连点。**钓鱼虽为长按按钮，但进带不锁定**——
# 按住会一路冲过目标带（2026-11-12 日志：P 3.03→4.34 越过带上缘、
# 松手回落至带下判失败）。用户 2026-11-12 技巧：长按进入 C 附近即
# 改为反复短按、保持在 C 附近——复用开采石头同款 hold_catch（长按推过
# 带上缘→松手回落→回 C 后短段长按/松手 bang-bang 围绕 C 到窗口结束）。
DIAOYU_ENTRY_OPTION = "先钓一下再说！"          # 入口对话框选项（点击后小游戏窗口出现）
DIAOYU_TITLE_ICON = "xuanshu_diaoyu.png"        # 窗口标题「钓鱼」（消失=任务完成）
DIAOYU_HOLD_ICON = "xuanshu_diaoyu_changan.png"  # 「长按」按钮（开始后出现；按住推进进度）
DIAOYU_ARM_TIMEOUT = _pochu_minigame.POCHU_ARM_TIMEOUT      # 入口选项点击后检测窗口时限（秒）
DIAOYU_CHECK_INTERVAL = _pochu_minigame.POCHU_CHECK_INTERVAL  # 窗口检测限频（秒）
_diaoyu_armed_at = 0.0        # 钓鱼入口选项点击时间；>0 且未超时=检测开启
_diaoyu_last_check = 0.0      # 上次窗口检测时间（限频）


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


def _wait_claim_still(win, timeout=CLAIM_STILL_TIMEOUT):
    """到达领取点后等待人物站定。返回 (是否站定, 描述)。

    位置文字区像素比对（MoveWatch，零 OCR，~0.1ms/次）：寻路刚结束或
    仍在移动时点领取按钮，点击会落到游戏世界重新指定目的地——人物被
    带走、领取对话框随之关闭（2026-11-13 实机：回到领取点后又走开到
    [117,136]，下一轮才领到）。判据与剧情点屏推进同一套（位置区连续
    不变轮询数 STORY_MOVE_STATIC_POLLS）。

    超时返回 False，调用方按本轮失败处理——下一轮重读位置坐标后正常
    寻路/领取，自愈且不会把人物带走（比「边移动边点」安全）。
    """
    watch = MoveWatch()
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None and watch.walking(win, img) is False:
            return True, "人物已站定"
        time.sleep(CLAIM_STILL_POLL_INTERVAL)
    return False, "到达后人物仍未站定（寻路未停），本轮不点击领取以免被带走"


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


def _click_template_wait(win, icon, label, timeout, threshold=0.8):
    """轮询等待模板出现并点击。返回 (是否成功, 描述)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = game_map.find_template(win, icon, threshold=threshold)
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
    ok, info = _click_template_wait(win, JIANTIANPAN_ICON, "监天盘",
                                    JIANTIANPAN_CLICK_TIMEOUT,
                                    threshold=JIANTIANPAN_THRESHOLD)
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


from ..device.dialog_common import (_fuzzy_contains, _dialog_text_present,
                            close_scene_panels)

def _click_dialog_green_option(win, text, timeout=DIALOG_TIMEOUT, abort_text=None):
    """玄枢轶闻前缀包装（缓存点击/选项复检的共享实现在 dialog_common）。"""
    return dialog_common.click_dialog_green_option(
        win, text, timeout=timeout, abort_text=abort_text,
        prefix="玄枢轶闻")


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

    # 站定门（2026-11-13 实机）：寻路未完全停稳就点领取按钮，点击会落到
    # 游戏世界重新指定目的地——人物被带走、领取对话框关闭，本轮必失败
    ok, info = _wait_claim_still(win)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
        return False, info, None

    # 领取任务（按钮随站位渲染位置变化，必须识图定位，不缓存）
    pt = game_map.find_template(win, TASK_LINGQU_ICON)
    if pt is None:
        # 对话框遮挡兜底：到达时若有对话框（NPC 欢迎语/奇遇弹窗）会
        # 挡住领取按钮，点屏幕中间关闭后重试（2026-11-07 用户反馈）
        had, _ = clear_blocking_dialog(win, "玄枢轶闻")
        if had:
            pt = game_map.find_template(win, TASK_LINGQU_ICON)
            if pt is not None:
                log("[玄枢轶闻] 关闭遮挡对话框后已找到领取任务按钮", "SYS")
    if pt is None:
        return False, "已到达但未找到领取任务按钮", None
    human_click(win, pt[0], pt[1])
    # 记录按钮坐标：站位偏远时「任务」浮标仍可命中，点它会把人带走
    # （2026-11-13 排查用，正常应在人物附近）
    log(f"[玄枢轶闻] 已点击领取任务 @({pt[0]},{pt[1]})", "SYS")

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
    track_gate = task_tracker.TrackGate(min_interval=0.0)  # 面板差分门（领取校验短窗口
        # 关键检测，不限频——同修罗 _claim_task，防「未出现」结论被
        # 复用 2s 拖慢领取（2026-10-28）
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            items = track_gate.ocr(img)
            items2, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
            img_np = np.zeros_like(np.asarray(img))
            track_gate.paste_track(img, img_np)
            if _dialog_text_present(items2, DAILY_LIMIT_TEXT):
                return False, DAILY_LIMIT_INFO, None
            if task_tracker.has_task_in(items, img_np, TASK_TITLE):
                # 任务刚出现时领取对话框还在切换/面板刚渲染，布局可能未稳定
                # （实测首帧坐标点击落空、未开始寻路）：等待约 1.5s 后重新
                # 截帧 OCR，把稳定帧带回 follow_task 保证首次点击落点准确
                time.sleep(1.5)
                img2 = win.capture(force_activate=False)
                if img2 is not None:
                    items3, img_np3 = task_tracker.ocr_track(img2)
                    if task_tracker.has_task_in(items3, img_np3, TASK_TITLE):
                        return True, "已领取任务", (items3, img_np3)
                return True, "已领取任务", (items, img_np)  # 复检失败按首帧返回
        time.sleep(0.2)
    return False, "已点击领取玄枢轶闻，但任务追踪中未检测到任务", None






def _current_task_sig(win, title):
    """当前任务签名（仅 OCR 追踪面板区域）；任务不存在返回 ""。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = task_tracker.ocr_track(img)
    task = task_tracker.find_task(
        task_tracker.group_tasks(items,
                                 task_tracker.panel_row_xmax(np.asarray(img))),
        title)
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
    """封印机关破解：阻塞循环在 minigames/jiguan.py，任务侧保留薄包装。"""
    return _jiguan_minigame.play(win, title, _current_task_sig,
                                 _task_done_or_changed)



def verify_jiguan_templates(win):
    """高级设置-测试机关模板按钮：对当前画面逐一测试三个机关模板，
    打印全帧最佳匹配度与位置日志（只读不点击，供阈值/模板校准）。
    返回 (是否成功, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        log("[玄枢轶闻] 测试机关模板: 截图失败", "WARN")
        return False, "截图失败"
    for icon in JIGUAN_CLICK_ORDER:
        conf, (x, y) = game_map.best_match_in(img, icon)
        hit = conf >= JIGUAN_THRESHOLD
        log(f"[玄枢轶闻] 测试机关模板 {icon}: 全帧最佳匹配度 {conf:.2f}"
            f" @({x},{y}){'（≥阈值，任务会点击）' if hit else '（<阈值，任务不会点击）'}",
            "SYS" if hit else "WARN")
    return True, "三个模板已测试，结果见日志"


# ---- 封印按序开启任务（花果山「先触南，再寻东，后破西」，2026-10-19 实测） ----
# 三个可跟踪绿字（机关·东/南/西）嵌在描述行内、文案颜色恒不变，须按
# 南→东→西 点击；每点完一处面板出现「第N处封印已开启」——进度信号。
# green_order 不适用（关键词恒绿无法序进），用 progress_key（已开启
# 计数）+ green_picker（按计数选关键词、链接下划线实线段定位点击——
# OCR 文本比例映射会因行拆分/误读点错，实测 2026-10-25）实现；
# 回调在非本任务阶段自门控不介入（progress_key 返回 None、picker 返回
# None 回退 track_task 行级选择）。




def _play_chuyijuebao(win, timeout=CHUYIJUEBAO_TIMEOUT):
    """锄遗聚宝：阻塞循环在 minigames/chuyijuebao.py，任务侧保留薄包装。"""
    return _chuyijuebao_minigame.play(win, timeout=timeout)


def _arm_diaoyu_check(opt):
    """钓鱼入口选项被点击 → 开启小游戏窗口检测（armed 门控）。

    只有「先钓一下再说！」被实际点击后才检测 xuanshu_diaoyu.png 标题，
    其他任务阶段不跑这个模板匹配（无开销、无误配）。重复点击入口选项
    会刷新时限；窗口处理完成或超时后检测自动关闭。
    """
    global _diaoyu_armed_at
    if opt == DIAOYU_ENTRY_OPTION:
        _diaoyu_armed_at = time.time()
        log(f"[玄枢轶闻] 已点击钓鱼入口选项，开启小游戏窗口检测"
            f"（{DIAOYU_ARM_TIMEOUT}s 内有效）", "SYS")


def _diaoyu_window_check(win, img, prefix="玄枢轶闻"):
    """检测并执行钓鱼小游戏（armed 门控 + 限频），返回是否已处理。

    入口选项「先钓一下再说！」被点击后（_arm_diaoyu_check）才在
    POCHU_REGION 内匹配窗口标题 xuanshu_diaoyu.png（搜索条带/阈值与
    客栈破除封印完全一致）；窗口出现即阻塞执行完整闭环（共享实现
    pochufengyin.play_pochufengyin，长按运输 + 短段长按维持：hold=True +
    hold_catch=True，长按推过目标带上缘→松手回落→回 C 后反复短按保持在
    C 附近到窗口结束，2026-11-12 用户技巧），标题消失（结束）或战斗
    打断后返回。检测限频 DIAOYU_CHECK_INTERVAL（条带匹配 ~10ms）；
    未 armed/限频未到/未命中均返回 False，热路径零成本。
    """
    global _diaoyu_last_check, _diaoyu_armed_at
    if _diaoyu_armed_at <= 0 \
            or time.time() - _diaoyu_armed_at > DIAOYU_ARM_TIMEOUT:
        return False
    now = time.time()
    if now - _diaoyu_last_check < DIAOYU_CHECK_INTERVAL:
        return False
    _diaoyu_last_check = now
    bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
    score, wpt = game_map.best_match_np(bgr, DIAOYU_TITLE_ICON,
                                        region=_pochu_minigame.POCHU_REGION)
    if wpt is None or score < _pochu_minigame.POCHU_THRESHOLD:
        return False
    log(f"[{prefix}] 检测到钓鱼窗口 @({wpt[0]},{wpt[1]})"
        f"（匹配度 {score:.2f}）", "SYS")
    ok, info = _pochu_minigame.play_pochufengyin(
        win, DIAOYU_TITLE_ICON, prefix=prefix, label="钓鱼",
        hold=True, action_icon=DIAOYU_HOLD_ICON, hold_catch=True)
    if ok:
        # 小游戏完成 → 关闭检测（处理失败保持 armed，窗口仍在时
        # 下轮询重试）
        _diaoyu_armed_at = 0.0
    log(f"[{prefix}] {info}", "SYS" if ok else "WARN")
    return True


def _follow_wait_handler(win, items, img_np, img):
    """follow 等待期的玄枢专属小游戏处理（钓鱼 / 机关 / 锄遗聚宝）。

    顺序与原引擎一致：钓鱼窗口 → 机关提示对话 → 锄遗聚宝模板。
    返回 True 表示本帧已处理，follow 下一轮询继续。
    """
    if _diaoyu_window_check(win, img, TASK_TITLE):
        return True
    if _dialog_text_present(items, JIGUAN_DIALOG_TEXT):
        ok2, info2 = _play_jiguan_puzzle(win, TASK_TITLE)
        log(f"[{TASK_TITLE}] {info2}", "SYS" if ok2 else "WARN")
        return True
    if game_map.find_template_in(img, CHUYIJUEBAO_ICON) is not None:
        ok2, info2 = _play_chuyijuebao(win)
        log(f"[{TASK_TITLE}] {info2}", "SYS" if ok2 else "WARN")
        return True
    return False


def run_cycle(win):
    """执行一轮玄枢轶闻检查：有任务则跟踪到完成，无任务则领取。

    领取成功后立即开始跟踪（不等轮次间隔，缩短领取后首次点击延迟）。

    任务消失判定走全局规则（2026-11-12 用户确认）：任务追踪消失即
    任务结束——游戏在无追踪任务时把面板自动收起（锄遗聚宝完成后
    面板即消失），不再需要按任务传参（旧 task_gone_require_panel）。

    Args:
        win: 游戏主窗口 Window 对象

    返回 (状态, 描述)；状态：done 完成一次任务 / daily_limit 每日次数已满 / fail 本轮失败
    """
    if is_in_battle(win):
        return "fail", "战斗中，跳过本轮（等待战斗结束）"

    if task_tracker.has_task(win, TASK_TITLE):
        ok, info = follow_task(win, TASK_TITLE, prefix=TASK_TITLE,
                               extra_dialog_handler=_handle_jiantianpan,
                               progress_key=_jiguan_link_progress,
                               green_picker=_jiguan_link_picker,
                               stall_recheck=True,
                               wait_handler=_follow_wait_handler,
                               option_click_handler=_arm_diaoyu_check)
        return ("done", info) if ok else ("fail", info)

    # 没有任务 → 寻路到任务位置并领取任务
    debug_overlay.set_phase("前往领取点")  # 看板阶段
    ok, info, pre_items = navigate_to_task(win)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔；复用领取校验的 OCR 结果
    ok, info = follow_task(win, TASK_TITLE, prefix=TASK_TITLE,
                           first_items=pre_items,
                           extra_dialog_handler=_handle_jiantianpan,
                           progress_key=_jiguan_link_progress,
                           green_picker=_jiguan_link_picker,
                           stall_recheck=True,
                           wait_handler=_follow_wait_handler,
                           option_click_handler=_arm_diaoyu_check)
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
            debug_overlay.record_round()  # 统计页：完成轮次计数
            log(f"[玄枢轶闻] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[玄枢轶闻] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[玄枢轶闻] {info}", "WARN")
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
