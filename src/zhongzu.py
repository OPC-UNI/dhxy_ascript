"""种族比武任务自动化（参考修罗实现）。

流程：
0. 任务追踪中已有「比武大会」任务（续跑）→ 跳过领取，直接跟踪
1. 领任务（长安；领取 NPC/坐标/小地图点见下方常量）：
   1.0 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
   1.1 队长先走到长安领取点附近：
       已在领取点（±15）→ 跳过寻路；
       不在长安 → 世界地图点击长安图标 → 小地图点击领取点寻路；
       在长安但不在领取点附近 → 直接打开小地图点击领取点寻路
       寻路等待到达领取点（±5）
   1.2 到达后进入队伍监控循环（种族比武设置，仅队长生效）：
       固定 5 人，缺人从申请加入面板允许申请人补齐 → 人满才领取；
       自动创建队伍并入首次队伍面板打开（未组队时直接创建）；
       队长被转移则转 1.0 非队长保持模式
   1.3 点击领取 NPC → 对话框点绿色选项
       「我们也可以为服务器的比武大会贡献力量，放心吧。」
       → 校验任务出现在追踪面板
       领取人数不足（用户 2026-11-12 实机）：点击选项后任务不出现
       （无独立弹窗）→ 复查队伍人数，不足则进入监控补人循环
2. 寻路：点击任务追踪绿字自动寻路（复用 xuanshu.follow_task）
3. 任务对话：寻路到达后出现对话面板 → 命中绿色选项
   「进入战斗 / 开始战斗 / 开始采集 / 开始开采 / 开始训练」任一即点击；
   之后由战斗线程 / follow_task 等待任务从追踪面板消失（=本轮完成）
4. 战斗：战斗由 battle.watch_loop 线程自动操作；follow_task 战斗中跳过识别
5. 返程：仅本轮由上述任务选项启动时才返程——打开背包任务储物格 →
   右键「助威令」回长安 → 关闭世界地图/小地图/背包
   助威令缺失时跳过返程并告警，下一轮走地图回长安
6. 循环往复；达到次数上限（默认 10、上限 100）时结束任务进程

巫医能力保留但暂不实现（用户 2026-11-12 确认）：设置卡保留自动巫医
开关与间隔，任务侧首次轮到巫医时提示「巫医暂无实现，跳过」后静默
跳过，链路调用点保留 TODO；战斗线程现有的巫医对话框自动处理天然生效。
"""
import re
import time
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.system import R  # 资源路由（模板路径）

from .app_utils import (human_click, human_move, precise_click, log,
                        move_away, norm_text)
from . import app_utils
from . import task_tracker
from . import map as game_map
from . import team
from . import debug_overlay
from . import bag
from . import battle
from .battle import is_in_battle, is_in_battle_frame
from . import repair
from .control_window import (load_task_counts, load_zhongzu_check_team,
                             load_zhongzu_auto_create_team,
                             load_zhongzu_auto_rejoin,
                             load_zhongzu_auto_wuyi,
                             load_zhongzu_wuyi_interval,
                             load_zhongzu_auto_repair,
                             load_zhongzu_repair_interval,
                             load_zhongzu_tab_group)
from .xuanshu import follow_task  # 任务跟踪（绿字点击寻路/战斗跳过/附加对话处理）
from . import tab_group  # 组号模式（一窗口多 Tab）：初始化/队员 pass/抑制标志
from .dialog_common import (SCREEN_CENTER_POS,
                            green_option_center)  # 对话框共享组件

# 任务模式与追踪面板标题（用户 2026-11-12 确认：追踪中显示「比武大会」）
TASK_MODE = "种族比武"        # 控制面板任务模式名（任务次数配置键）
TASK_TITLE = "比武大会"       # 追踪面板标题匹配串
TASK_FALLBACK_TEXTS = ("比武大会",)  # has_task 兜底匹配串（用户 2026-11-12
                                 # 确认内容行含「比武」即可；兜底取
                                 # 「比武大会」而非裸「比武」——防匹配到
                                 # 客栈任务追踪行「阿旺朝洛阳城比武场方向跑」）

# ---- 领取任务（长安；用户 2026-11-12 提供领取 NPC 模板与坐标） ----
CHANGAN = "长安"                              # 位置识别地名（精确匹配）
CLAIM_NPC_ICON = "zhongzu_claim_npc.png"      # 领取 NPC 模板（用户提供 29x55）
CLAIM_SPOT = (286, 159)                       # 领取点地图坐标（长安 286,159）
CLAIM_MAP_POS = (737, 387)                    # 小地图领取点点击点（用户提供）
CLAIM_NEAR_TOLERANCE = 15                     # 已在领取点附近判定容差
CLAIM_NEAR_ENTRY_GRACE = 3.0                  # 位置近但 NPC 未出现的渲染短窗口（秒）
WALK_ARRIVE_TOLERANCE = 5                     # 寻路到达判定容差（更严格）
CLAIM_ENTRY_FIRST_WAIT = 4.5                  # 首轮等待入口出现的短窗口（秒）
CLAIM_ARRIVE_TIMEOUT = 90                     # 寻路到领取点的超时
CLAIM_BUTTON_TIMEOUT = 20                     # 重试时等待领取入口出现的超时
CLAIM_DIALOG_TIMEOUT = 15                     # 领取对话框处理超时
CLAIM_DIALOG_APPEAR_TIMEOUT = 4               # 点击入口后等待对话框出现的短窗口
CLAIM_VERIFY_WAIT = 5                         # 领取后任务进入追踪的正常轮询时长（秒）
# 领取对话框绿色选项（游戏内原文，用户 2026-11-12 提供）
CLAIM_DIALOG_OPTION = "我们也可以为服务器的比武大会贡献力量，放心吧。"
CLAIM_OPTION_TAIL = "贡献力量，放心吧。"      # 换行渲染时的尾部匹配串
CLAIM_OPTION_SHORT = "贡献力量"               # 最后兜底
CLAIM_UNCONFIGURED_INFO = (
    "种族比武领取三件套未就绪（NPC 模板缺失/坐标或小地图点为空），任务结束")

# ---- 领取前队伍检查（队伍任务，固定 5 人） ----
TEAM_MIN_MEMBERS = 5            # 领取与人数监控的固定人数（用户 2026-11-12 确认）
TEAM_CHECK_RETRY_INTERVAL = 10  # 缺人且无申请人可补时的重查间隔（秒）
TEAM_RECOG_RETRY_INTERVAL = 3   # 成员识别失败（OCR 噪声）时的重试间隔（秒）

# ---- 任务对话（寻路到达后出现；用户 2026-11-12 确认五选一） ----
TASK_DIALOG_OPTIONS = ("进入战斗", "开始战斗", "开始采集", "开始开采", "开始训练")
_WAIT_POLL_INTERVAL = 0.3      # 领取/对话轮询间隔（非防抖判定点）

# ---- 战斗 ----
BATTLE_END_POLL = 2.0          # 等待战斗结束的轮询间隔

# ---- 返程（背包任务储物格 → 助威令右键 → 回长安） ----
ZHUWEILING_ICON = "zhongzu_zhuweiling.png"   # 助威令模板（用户提供 47x47）
ZHUWEILING_LABEL = "助威令"
RETURN_ITEM_TIMEOUT = 15       # 等待助威令在任务储物格中出现的超时
RETURN_POLL_INTERVAL = 0.5     # 返程轮询间隔

# ---- 自动巫医（能力保留，链路待实现；用户 2026-11-12 确认） ----
# TODO(巫医链路标定)：以下三件套待用户提供后接入（参考修罗同款字段）
# WUYI_NPC_ICON = "zhongzu_wuyi.png"   # 巫医 NPC 模板（待提供）
# WUYI_SPOT = None                     # 巫医 NPC 地图坐标（待标定）
# WUYI_MAP_POS = None                  # 小地图上巫医位置的点击点（待标定）

# ---- 模块内状态 ----
_started_via_option = False    # 本轮是否由任务选项启动（决定战后是否返程）
_team_checked = False          # 首次领取后置位：后续轮次跳过队伍检查
_task_gone_last_round = False  # 上一轮任务消失已确认（跳过 has_task 检查）
_wuyi_notice_logged = False    # 自动巫医「未实现」提示每次任务启动只记一次


_TEMPLATE_EXISTS_CACHE = {}  # 模板存在性缓存（任务进程运行期不新增素材；
                             # 缓存避免每轮多次调用 R.img 重复打印缺失警告）


def _template_exists(icon):
    """模板文件是否存在（R.img 对缺失模板会打印警告，GBK 控制台下
    可能抛 UnicodeEncodeError，吞掉按缺失处理）。"""
    if icon in _TEMPLATE_EXISTS_CACHE:
        return _TEMPLATE_EXISTS_CACHE[icon]
    try:
        exists = Path(R.img(icon)).exists()
    except Exception:
        exists = False
    _TEMPLATE_EXISTS_CACHE[icon] = exists
    return exists


def _claim_configured():
    """领取三件套是否已标定（坐标/小地图点/模板均需就绪）。"""
    return (CLAIM_SPOT is not None and CLAIM_MAP_POS is not None
            and _template_exists(CLAIM_NPC_ICON))


def _near_claim_spot(pos, tolerance):
    """判断位置是否在长安领取点附近（地名精确匹配，容差抗 OCR 噪声）。"""
    if CLAIM_SPOT is None:
        return False
    return game_map.is_at_position(pos, CHANGAN,
                                  CLAIM_SPOT[0], CLAIM_SPOT[1], tolerance)


def _in_claim_map(pos):
    """位置是否在领取地图长安（领取 NPC 模板跨图误命中门控）。

    位置读取失败按不在处理——无法确认在长安时不得采信 NPC 模板信号。
    """
    return pos is not None and pos[0].strip() == CHANGAN


def _task_state(win):
    """一次截帧判定续跑状态：追踪面板是否有种族比武任务。

    面板不在场（标题条模板未命中）时返回 False——ocr_track 会回退
    静态区域，世界浮字/对话框文字可能误命中任务文案。面板门控只
    作用于「任务存在」判定，不阻断领取：面板缺席/战斗帧/截图失败
    均按无任务放行（走位/跟踪自行处理）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if is_in_battle_frame(img):
        return False
    if not task_tracker.panel_visible(img=img):
        return False
    items, img_np = task_tracker.ocr_track(img)
    return task_tracker.has_task_in(items, img_np, TASK_TITLE,
                                    TASK_FALLBACK_TEXTS)


# ---- 队伍检查（固定 5 人，与修罗同款骨架） ----

def _check_team_ready(win, create_if_missing=False):
    """领取前队伍检查（一次面板开关，固定 5 人）。

    create_if_missing：自动创建队伍并入本次菜单打开（见
    team.check_team_min）。返回 (状态, 描述)；状态 ready / wait /
    not_leader（见 team.check_team_min）。
    """
    status, info, members = team.check_team_min(
        win, min_members=TEAM_MIN_MEMBERS, allow_kick=True,
        create_if_missing=create_if_missing, prefix="[种族比武][创建队伍]")
    team.update_overlay_team(status, info, members)
    return status, info


def _check_team_with_retry(win, tries=3, create_if_missing=False):
    """队伍检查带瞬态失败重试（与修罗同款）。

    截窗失败/面板打不开可能是瞬时问题，一次失败即转非队长保持模式
    会把队长永久挂起。瞬态类失败短间隔重试，连续 N 次才按 not_leader
    处理；明确的非队长判定与未组队（确定性状态）不重试。
    """
    last = ("not_leader", "队伍检查未执行")
    for i in range(tries):
        status, info = _check_team_ready(win,
                                         create_if_missing=create_if_missing)
        last = (status, info)
        if status != "not_leader":
            return status, info
        # 未组队是确定性状态（没组队就没有队长）——提示带「队长无法
        # 判定」前缀，必须先于瞬态判定短路返回
        if "未组队" in info:
            return status, info
        if "队长无法判定" not in info and "截图失败" not in info:
            return status, info  # 明确非队长（面板打开且无解散按钮）
        if i < tries - 1:
            log(f"[种族比武] {info}，瞬态失败重试（{i + 1}/{tries - 1}）", "WARN")
            time.sleep(2.0)
    return last


def _monitor_team_and_fill(win, create_if_missing=False):
    """队长缺人监控循环（领取前共用）。

    每轮先看申请人证据（申请面板已开，或组队按钮为 zudui_item2
    申请待处理态）：有则先补人（按最近一次人数检查的缺口一次补满，
    补满或全失败后清空残留申请列表，立即重查），无则查人数——缺人
    按间隔重查、人满返回（调用方随即领取）；非队长转保持模式（不返回）。
    本任务不喊话（用户 2026-11-12 确认）。
    """
    shortfall = None  # 最近一次人数检查的缺口（缺 N 补 N）；None=未知按 1 人补
    while True:
        img = win.capture(force_activate=False)
        if img is not None and team.has_pending_apply(img):
            ok_fill, fill_info = team.fill_team(
                win, target=TEAM_MIN_MEMBERS, max_allow=shortfall or 1)
            log(f"[种族比武] 补人: {fill_info}", "SYS" if ok_fill else "INFO")
            if ok_fill:
                continue  # 已补人，立即重查
        status, info = _check_team_with_retry(
            win, create_if_missing=create_if_missing)
        create_if_missing = False  # 自动创建只在首次检查内执行
        if status == "not_leader":
            if team.is_definitive_not_leader(info):
                _keep_alive(win, info)  # 无限循环，用户手动停止
            interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                        else TEAM_CHECK_RETRY_INTERVAL)
            log(f"[种族比武] {info}（瞬态失败，{interval}s 后重查）", "WARN")
            time.sleep(interval)
            continue
        if status == "ready":
            return
        # 人数不足信息里解析缺口（成员识别失败/申请待处理态无人数信息
        # 时保留旧值）：缺 2 人一次补 2 人，不是每轮只补 1 人
        m = re.search(r"队伍人数不足（(\d+)/(\d+)", info)
        if m:
            shortfall = max(0, int(m.group(2)) - int(m.group(1)))
        # 等待状态下复查申请人证据：check_team_min 的帧可能比本循环
        # 首帧更晚拍到申请待处理态（申请人恰在两次截帧间出现），
        # 有则立即补人不空等重查间隔
        img = win.capture(force_activate=False)
        if img is not None and team.has_pending_apply(img):
            ok_fill, fill_info = team.fill_team(
                win, target=TEAM_MIN_MEMBERS, max_allow=shortfall or 1)
            log(f"[种族比武] 补人: {fill_info}", "SYS" if ok_fill else "INFO")
            if ok_fill:
                continue
        interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                    else TEAM_CHECK_RETRY_INTERVAL)
        log(f"[种族比武] {info}，暂无申请人可补，{interval}s 后重查", "WARN")
        time.sleep(interval)


def _keep_alive(win, info):
    """非队长：核心流程不执行，保持任务运行（战斗线程自动操作）。

    自动归队（设置开关默认开，仅非队长生效——本保持模式即非队长）：
    战斗死亡落轮回司后自动打开队伍面板点「归队」传送回队长。
    """
    log(f"[种族比武] {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
    battle.wuyi_fast_idle = True
    auto_rejoin = load_zhongzu_auto_rejoin()
    log(f"[种族比武] 自动归队: "
        f"{'开（死亡落轮回司自动点归队回队长）' if auto_rejoin else '关'}", "SYS")
    next_rejoin_at = 0.0
    while True:
        time.sleep(1)
        if not auto_rejoin or tab_group.pass_active:
            continue
        now = time.time()
        if now < next_rejoin_at:
            continue
        next_rejoin_at = now + team.REJOIN_POLL_INTERVAL
        # 战斗判定只在轮询到期时执行（is_in_battle 是截窗+模板匹配，
        # 每秒跑会空烧）；战斗中死亡送回轮回司必在战斗结束后
        if is_in_battle(win):
            continue
        try:
            handled, desc = team.maybe_rejoin(win, prefix="[种族比武][归队]")
            if handled:
                log(f"[种族比武][归队] {desc}",
                    "SYS" if desc.startswith("已归队") else "WARN")
                next_rejoin_at = time.time() + team.REJOIN_RETRY_INTERVAL
        except Exception as e:
            log(f"[种族比武][归队] 归队检查异常: {e}", "WARN")


def _auto_create_team_once(win):
    """执行一次自动创建队伍（开关已确认开启）。"""
    try:
        ok, info = team.ensure_team_created(win, prefix="[种族比武][创建队伍]")
    except Exception as e:
        ok, info = False, f"创建队伍异常: {e}"
    log(f"[种族比武][创建队伍] {info}", "SYS" if ok else "WARN")


# ---- 领取点导航（长安） ----

def _click_claim_map_pos(win):
    """点击小地图领取点；坐标超出窗口时夹取到窗口内并告警。

    防御性夹取：坐标笔误/窗口尺寸变化时避免点到游戏窗口外
    （可能命中其他窗口）。正常窗口内坐标原样点击。
    """
    x, y = CLAIM_MAP_POS
    try:
        w, h = win.rect.width, win.rect.height
    except Exception:
        w = h = None
    if w and h and not (0 <= x < w and 0 <= y < h):
        cx, cy = max(0, min(w - 1, x)), max(0, min(h - 1, y))
        log(f"[种族比武] 小地图点击点 ({x},{y}) 超出窗口 {w}x{h}，"
            f"已夹取为 ({cx},{cy})，请确认坐标", "WARN")
        x, y = cx, cy
    human_click(win, x, y)


def _open_claim_map(win, pos=None):
    """地图感知导航到长安领取点（世界地图/小地图自动选择）。

    位置读为长安 → 直接打开小地图；其他地图或位置不可读 → 世界地图
    点击长安图标。随后点击小地图领取点坐标寻路。
    pos：调用方刚读到的位置（同帧复用，免重截+重 OCR）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    if is_in_battle_frame(img):
        return False, "战斗中，跳过地图导航"
    if pos is None:
        pos = game_map.read_map_position(win, img=img)
    if pos is not None and pos[0].strip() == CHANGAN:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    else:
        if pos is not None:
            log(f"[种族比武] 不在长安（{pos[0].strip()}[{pos[1]},{pos[2]}]），"
                f"经世界地图前往长安", "SYS")
        ok, info = game_map.open_mini_map_city(
            win, game_map.WORLD_MAP_CHANGAN_ICON, CHANGAN)
        if not ok:
            return False, info
    _click_claim_map_pos(win)
    return True, "已点击长安小地图领取点位置"


def _walk_to_claim_spot(win):
    """寻路到长安领取点附近（与队伍人数无关，队长身份下执行）。

    已在领取点附近（±15）且领取 NPC 可见 → 跳过寻路；位置近但 NPC
    未出现给 3s 渲染短窗口，仍无则小地图重走。位置读取失败按不在
    附近处理，直接走地图寻路。
    到达判定：领取 NPC 可见（须地图名门控，防跨图误命中）或坐标
    ±5 严格容差。
    返回 (是否成功, 描述)。
    """
    if not _claim_configured():
        return False, CLAIM_UNCONFIGURED_INFO
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    npc_visible = game_map.match_frame_np(frame, CLAIM_NPC_ICON) is not None
    pos = game_map.read_map_position(win, img=img)
    if npc_visible and _in_claim_map(pos):
        return True, "已在领取点附近（领取 NPC 可见）"
    if _near_claim_spot(pos, CLAIM_NEAR_TOLERANCE):
        # 位置近但入口不可见：可能是位置误读或超出可点击范围，
        # 给入口一个渲染短窗口，仍无则小地图重走
        deadline = time.time() + CLAIM_NEAR_ENTRY_GRACE
        while time.time() < deadline:
            if game_map.find_template(win, CLAIM_NPC_ICON) is not None:
                return True, "已在领取点附近（领取 NPC 可见）"
            time.sleep(0.5)
        log("[种族比武] 位置近但领取 NPC 未出现，改走小地图重走领取点", "WARN")
    ok, info = _open_claim_map(win, pos)  # 决策帧已读位置，同帧复用
    if not ok:
        return False, info
    log("[种族比武] 已点击长安小地图领取点位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    game_map.close_maps(win)
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pos = game_map.read_map_position(win, img=img)  # 同帧复用
        if _in_claim_map(pos) \
                and game_map.match_frame_np(frame, CLAIM_NPC_ICON) is not None:
            log("[种族比武] 已到达领取点附近（领取 NPC 可见）", "SYS")
            return True, "已到达领取点附近"
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            log("[种族比武] 已到达领取点附近", "SYS")
            return True, "已到达领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[种族比武] 前往领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, (f"寻路到领取点超时（未到达长安 "
                   f"{CLAIM_SPOT[0]},{CLAIM_SPOT[1]} 附近）")


def _relocate_to_claim_spot(win):
    """小地图点击领取点坐标重新寻路（领取校验兜底）。"""
    if not _claim_configured():
        return False, CLAIM_UNCONFIGURED_INFO
    if is_in_battle(win):
        return False, "战斗中，跳过重走"
    ok, info = _open_claim_map(win)
    if not ok:
        return False, info
    log("[种族比武] 已点击长安小地图领取点位置，重新寻路（领取校验兜底）", "SYS")
    time.sleep(1.0)
    game_map.close_maps(win)
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pos = game_map.read_map_position(win, img=img)
        if _in_claim_map(pos) \
                and game_map.match_frame_np(frame, CLAIM_NPC_ICON) is not None:
            return True, "已重走到领取点附近（领取 NPC 可见）"
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            return True, "已重走到领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[种族比武] 重走领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, "重走领取点超时"


def _wait_claim_entry(win, timeout):
    """等待领取 NPC 出现，返回坐标或 None（战斗中跳过等待）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.5)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, CLAIM_NPC_ICON)
        if pt is not None:
            return pt
        time.sleep(1.5)
    return None


def _find_claim_option(items, img_np):
    """在 OCR 结果中查找领取对话框绿色选项，返回 (cx, cy, txt, 是否兜底)。

    选项可能换行渲染：按优先级完整文案 → 尾部「贡献力量，放心吧。」
    → 短兜底「贡献力量」。匹配前去标点；限定对话框区域
    （x≥PANEL_MAX_X），避免误点追踪面板绿字链接。
    """
    needles = (norm_text(CLAIM_DIALOG_OPTION),
               norm_text(CLAIM_OPTION_TAIL),
               norm_text(CLAIM_OPTION_SHORT))
    for idx, needle in enumerate(needles):
        for txt, rect, score in items:
            ntxt = norm_text(txt)
            if score >= 0.55 and needle and needle in ntxt \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                cx, cy = green_option_center(img_np, rect,
                                             len(needle), len(ntxt))
                return cx, cy, txt, idx == len(needles) - 1
    return None


def _click_claim_option(win, items, img_np):
    """点击领取对话框中的绿色选项，成功返回 True。"""
    found = _find_claim_option(items, img_np)
    if found is None:
        return False
    cx, cy, txt, fallback = found
    human_click(win, cx, cy)
    suffix = "（兜底匹配）" if fallback else ""
    log(f"[种族比武] 已点击绿色选项「{txt}」{suffix}", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _click_entry_ensuring_dialog(win):
    """点击领取 NPC 并确保对话框出现；未出现判定坐标不对，小地图重定位
    后重试一次。返回 (是否成功, 描述, 对话框帧)；成功时对话框已打开
    （选项可见），帧供领取流程同帧点选项。
    """
    dialog_seen = False
    appear_timeout = CLAIM_DIALOG_APPEAR_TIMEOUT
    seen_frame = None
    for attempt in (1, 2):
        wait_timeout = (CLAIM_ENTRY_FIRST_WAIT if attempt == 1
                        else CLAIM_BUTTON_TIMEOUT)
        pt = _wait_claim_entry(win, wait_timeout)
        if pt is None:
            if attempt == 1:
                log(f"[种族比武] 等待领取 NPC {wait_timeout}s 未出现，"
                    f"小地图重走领取点后重试", "WARN")
                ok, info = _relocate_to_claim_spot(win)
                if not ok:
                    log(f"[种族比武] {info}", "WARN")
                    return False, "到达后未出现领取 NPC", None
                continue
            return False, "到达后未出现领取 NPC（重走领取点后仍无）", None
        # 瞬时点击（取消拟人化移动）：走动中镜头随人物移动，拟人化
        # 移动期间点击位置已过期
        precise_click(win, pt[0], pt[1], move_duration=0)
        log(f"[种族比武] 已点击领取 NPC ({pt[0]}, {pt[1]})", "SYS")
        deadline = time.time() + appear_timeout
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(_WAIT_POLL_INTERVAL)
                continue
            items, img_np = game_map.ocr_regions(
                img, (task_tracker.DIALOG_REGION,))
            if _find_claim_option(items, img_np) is not None:
                dialog_seen = True
                seen_frame = (items, img_np)
                break
            time.sleep(_WAIT_POLL_INTERVAL)
        if dialog_seen:
            break
        if attempt == 1:
            log("[种族比武] 点击后未出现对话框（坐标不对），打开小地图重定位", "WARN")
            ok, info = _open_claim_map(win)
            if not ok:
                return False, info, None
            log("[种族比武] 已点击长安小地图领取点位置，重新寻路中", "SYS")
            time.sleep(1.0)
            game_map.close_maps(win)
            deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
            while time.time() < deadline:
                img = win.capture(force_activate=False)
                if img is None:
                    time.sleep(2.0)
                    continue
                if is_in_battle_frame(img):
                    time.sleep(2.0)
                    continue
                frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
                pos = game_map.read_map_position(win, img=img)
                if _in_claim_map(pos) and game_map.match_frame_np(
                        frame, CLAIM_NPC_ICON) is not None:
                    break
                time.sleep(2.0)
            appear_timeout = CLAIM_DIALOG_TIMEOUT
    if not dialog_seen:
        return False, "点击领取 NPC 后对话框始终未出现（小地图重定位后仍无）", None
    return True, "已打开领取对话框", seen_frame


def _claim_task(win, allow_fill=False):
    """在领取点附近领取种族比武任务。

    点击领取 NPC → 对话框点绿色选项
    「我们也可以为服务器的比武大会贡献力量，放心吧。」
    → 校验任务出现在追踪面板。
    返回 (是否成功, 描述, 校验帧)：校验帧供 follow_task 首轮复用。

    领取人数不足（用户 2026-11-12 实机确认）：点击领取选项后**没有
    独立弹窗**，表现为任务追踪中不出现任务。校验失败时复查队伍：
    人数不足且 allow_fill（检查队伍开）→ 进入监控补人循环，人满后
    下轮重试领取；人数满足 → 重走领取点后重试；非队长 → 保持模式。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取", None
    # 领取前面板签名：旧任务遗留面板时须等新任务渲染（签名变化）才确认
    pre_sig = None
    img0 = win.capture(force_activate=False)
    if img0 is not None:
        items0, _ = task_tracker.ocr_track(img0)
        pre_sig = task_tracker.task_signature_in(items0, TASK_TITLE)
    if pre_sig is not None:
        return False, "任务追踪中仍有种族比武任务，跳过领取（续跑跟踪）", None
    ok, info, seen = _click_entry_ensuring_dialog(win)
    if not ok:
        return False, info, None
    claimed = False
    if seen is not None:
        items, img_np = seen
        claimed = _click_claim_option(win, items, img_np)
    # 选项未在同一帧点中（罕见：选项渲染中）→ 短窗轮询兜底。
    # has_task（追踪区 OCR ~0.9s）限频到每 4 轮——对话框出现前任务
    # 不可能已进追踪
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    polls = 0
    while not claimed and time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(_WAIT_POLL_INTERVAL)
            continue
        items, img_np = game_map.ocr_regions(
            img, (task_tracker.DIALOG_REGION,))
        claimed = _click_claim_option(win, items, img_np)
        polls += 1
        if not claimed and polls % 4 == 0 and task_tracker.has_task(
                win, TASK_TITLE, TASK_FALLBACK_TEXTS):
            claimed = True
        time.sleep(_WAIT_POLL_INTERVAL)
    if not claimed:
        return False, "领取对话框处理失败", None
    # 校验任务出现在追踪面板（正常轮询 CLAIM_VERIFY_WAIT 秒）
    deadline = time.time() + CLAIM_VERIFY_WAIT
    found_frame = None
    track_gate = task_tracker.TrackGate(min_interval=0.0)  # 领取校验短窗口
        # 关键检测，不限频（见 CLAUDE.md 性能规则 12）
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        items = track_gate.ocr(img)
        img_np = np.zeros_like(np.asarray(img))
        track_gate.paste_track(img, img_np)  # 像素判定用当前帧，不陈旧
        if task_tracker.has_new_task_in(items, img_np, TASK_TITLE,
                                        TASK_FALLBACK_TEXTS, pre_sig):
            found_frame = (items, img_np)
            break
        time.sleep(0.5)
    if found_frame is not None:
        return True, "种族比武任务已领取", found_frame
    # 点击选项后任务未出现：人数不足是已知原因（无独立弹窗，用户
    # 2026-11-12 实机）。复查队伍——不足则进入监控补人循环，人满
    # 后下轮重试领取；人数满足才走重走领取点兜底
    if allow_fill:
        status, team_info = _check_team_ready(win)
        if status == "not_leader":
            if team.is_definitive_not_leader(team_info):
                _keep_alive(win, team_info)  # 无限循环，用户手动停止
            return False, (f"领取后任务未出现，队伍检查异常：{team_info}，"
                           f"下轮重试"), None
        if status == "wait":
            log(f"[种族比武] 领取后任务未出现（{team_info}），"
                f"进入监控补人循环", "WARN")
            _monitor_team_and_fill(win)  # 人满才返回
            return False, "队伍人数不足，已补人，下轮重试领取", None
        log("[种族比武] 领取后任务未出现，队伍人数已满足，重走领取点后重试",
            "WARN")
    # 未检索到：小地图重走领取点坐标后再次检索（重走可能触发任务渲染）
    ok2, info2 = _relocate_to_claim_spot(win)
    if not ok2:
        log(f"[种族比武] {info2}", "WARN")
    elif task_tracker.has_new_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS,
                                   pre_sig):
        log("[种族比武] 重走领取点后新任务已出现在追踪面板", "SYS")
        return True, "种族比武任务已领取", None
    try:
        titles = [t["title"] for t in task_tracker.read_tasks(win)]
        log(f"[种族比武] 验证失败，追踪面板当前标题: "
            f"{' / '.join(titles) if titles else '(无标题行)'}", "WARN")
    except Exception:
        pass
    return False, "已点击领取选项，但任务追踪中未检测到任务（重走领取点后仍无）", None


def verify_claim_flow(win):
    """验证种族比武领取链路（诊断页按钮，不改变任务状态）。

    与任务领取流程一致、时序无额外延时：
    走位到长安领取点 → 点击领取 NPC 打开对话框 → 定位绿色领取选项
    （仅移动鼠标定位，不点击——点击即领取）→ 点屏幕中间关闭对话框。
    不领取任务、不检查队伍（单人也可验证）；失败时返回原因。
    """
    tag = "[种族比武][验证]"
    if is_in_battle(win):
        return False, "战斗中，无法验证领取"
    ok, info = _walk_to_claim_spot(win)
    if not ok:
        return False, f"走位失败: {info}"
    ok, info, seen = _click_entry_ensuring_dialog(win)
    if not ok:
        return False, info
    if seen is not None:
        items, img_np = seen
    else:
        img = win.capture(force_activate=False)
        if img is None:
            return False, "截图失败"
        items, img_np = game_map.ocr_regions(
            img, (task_tracker.DIALOG_REGION,))
    found = _find_claim_option(items, img_np)
    if found is None:
        return False, "领取对话框已出现，但未识别到绿色领取选项"
    cx, cy, txt, fallback = found
    human_move(win.rect.left + cx, win.rect.top + cy)  # 仅定位，不点击
    suffix = "（兜底匹配）" if fallback else ""
    log(f"{tag} 已定位领取选项「{txt}」@({cx},{cy}){suffix}，仅定位不点击",
        "SYS")
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log(f"{tag} 已点击屏幕中间关闭领取对话框（未领取任务）", "SYS")
    return True, f"领取链路正常（选项「{txt}」已定位，未领取任务）"


# ---- 任务对话（五选一） ----

def _find_task_option(items, img_np):
    """在 OCR 结果中查找任务对话的绿色选项。

    命中「进入战斗 / 开始战斗 / 开始采集 / 开始开采 / 开始训练」
    任一即返回 (cx, cy, txt, 命中的选项)；限定对话框区域
    （x≥PANEL_MAX_X）且须为绿字，避免误点追踪面板链接。
    """
    for option in TASK_DIALOG_OPTIONS:
        needle = norm_text(option)
        for txt, rect, score in items:
            ntxt = norm_text(txt)
            if score >= 0.55 and needle in ntxt \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                cx, cy = green_option_center(img_np, rect,
                                             len(needle), len(ntxt))
                return cx, cy, txt, option
    return None


def _handle_task_dialog(win, items, img_np):
    """寻路到达后的任务对话：命中五个选项任一即点击。

    点击成功即标记本轮由任务选项启动（战后据此决定是否返程）。
    返回是否已处理。
    """
    global _started_via_option
    found = _find_task_option(items, img_np)
    if found is None:
        return False
    cx, cy, txt, option = found
    human_click(win, cx, cy)
    log(f"[种族比武] 已点击「{txt}」（{option}）", "SYS")
    _started_via_option = True
    move_away(win)
    return True


def _maybe_tab_bars_pass(win):
    """组号模式：点完任务追踪（寻路中）检查队员血蓝（tab_group 内节流）。"""
    try:
        if tab_group.active():
            tab_group.member_bars_pass(win)
    except Exception as e:
        log(f"[种族比武] 组号队员血蓝 pass 异常: {e}", "WARN")


def _follow_to_done(win, first_items=None):
    """寻路 + 任务对话 + 战斗：跟踪任务直到本轮完成。

    复用 xuanshu.follow_task：绿字点击自动寻路、任务对话走附加对话
    处理（五选一）、战斗由战斗监控线程操作且跟踪中自动跳过；
    任务从追踪面板消失即完成。
    """
    return follow_task(win, TASK_TITLE, prefix="种族比武",
                       extra_dialog_handler=_handle_task_dialog,
                       fallback_keywords=TASK_FALLBACK_TEXTS,
                       first_items=first_items,
                       post_track_handler=_maybe_tab_bars_pass,
                       chuyijuebao_check=False,
                       battle_extras_tpl_gate=True)


# ---- 返程（任务储物格 → 助威令） ----

def _wait_battle_end(win, timeout=60):
    """等待当前战斗结束（战斗操作由 battle.watch_loop 线程自动完成）。"""
    deadline = time.time() + timeout
    while True:
        if tab_group.pass_active:
            time.sleep(0.3)
            continue
        if not is_in_battle(win):
            return True, "战斗已结束"
        if time.time() > deadline:
            return False, f"等待战斗结束超时（{timeout}s）"
        time.sleep(BATTLE_END_POLL)


def _find_return_item(win):
    """在已打开的任务储物格中查找助威令，返回 (pt 或 None, 描述)。

    模板尚未提供时直接失败并说明（不进入等待循环）。
    """
    if not _template_exists(ZHUWEILING_ICON):
        return None, (f"{ZHUWEILING_LABEL}模板未提供（待补充 "
                      f"{ZHUWEILING_ICON}）")
    deadline = time.time() + RETURN_ITEM_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, ZHUWEILING_ICON)
        if pt is not None:
            return pt, f"已在任务储物格中找到{ZHUWEILING_LABEL}"
        time.sleep(RETURN_POLL_INTERVAL)
    return None, f"任务储物格中未找到{ZHUWEILING_LABEL}"


def _use_return_item(win):
    """识别助威令并右键使用，返回长安。返回 (是否成功, 描述)。"""
    pt, info = _find_return_item(win)
    if pt is None:
        return False, info
    # 保险：右键前确认背包仍打开
    if not bag.is_bag_open(win):
        return False, "右键助威令前背包已被关闭，未使用（下轮重试）"
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"[种族比武] 已右键使用{ZHUWEILING_LABEL} ({pt[0]}, {pt[1]})，"
        f"返回长安", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, f"已右键使用{ZHUWEILING_LABEL}"


def _close_return_panels(win):
    """返程收尾：关闭世界地图、小地图、背包（尽力而为，失败仅记日志）。

    先截一帧批量检测三项是否全未打开：全未打开直接返回，省去逐项
    带截图的关闭检测；地图未打开时跳过 close_maps 的双帧确认。
    """
    img = win.capture(force_activate=False)
    if img is None:
        game_map.close_maps(win)
        ok, info = bag.close_bag(win)
        log(f"[种族比武] {info}", "SYS" if ok else "WARN")
        return
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    found = game_map.match_frame_multi(
        frame, (game_map.WORLD_MAP_OPEN_ICON,
                game_map.MINI_MAP_OPEN_ICON,
                bag.BAG_BOX_ICON))
    maps_open = found[game_map.WORLD_MAP_OPEN_ICON] is not None \
        or found[game_map.MINI_MAP_OPEN_ICON] is not None
    bag_open = found[bag.BAG_BOX_ICON] is not None
    if not maps_open and not bag_open:
        log("[种族比武] 世界地图/小地图/背包均未打开，无需关闭", "INFO")
        return
    if maps_open:
        game_map.close_maps(win)
    if bag_open:
        ok, info = bag.close_bag(win)
        log(f"[种族比武] {info}", "SYS" if ok else "WARN")


def _return_to_npc(win):
    """返程：任务完成后用助威令回长安，准备下一轮领取。

    1. 战斗结束后打开背包并确保任务储物格已打开（bag.open_task_box）
    2. 识别助威令 → 右键使用，返回长安
    3. 关闭世界地图、小地图、背包
    助威令缺失/模板未提供：告警并跳过返程，下一轮领取流程会走
    世界地图寻路回长安兜底（用户 2026-11-12 确认）。
    """
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 模板缺失快速失败：不打开背包空等（素材标定前每轮都会走这里）
    if not _template_exists(ZHUWEILING_ICON):
        return False, (f"{ZHUWEILING_LABEL}模板未提供（待补充 "
                       f"{ZHUWEILING_ICON}）")
    # 背包整段流程持背包锁：与其他背包流程（摄妖香等）互斥
    with app_utils._BAG_LOCK:
        ok, info = bag.open_task_box(win, prefix="种族比武")
        if not ok:
            return False, info
        ok, info = _use_return_item(win)
        if not ok:
            _close_return_panels(win)
            return False, info
        _close_return_panels(win)
    time.sleep(1.0)  # 传送加载短等待（下一轮领取会自行处理加载慢场景）
    return True, f"已使用{ZHUWEILING_LABEL}返回长安"


# ---- 自动巫医（能力保留，链路暂未实现） ----

def _auto_wuyi(win):
    """自动巫医占位：链路未实现（待标定巫医 NPC 位置/模板）。

    用户 2026-11-12 确认：设置卡保留开关与间隔，每次任务启动首次
    轮到巫医时提示「巫医暂无实现，跳过」后静默跳过；后续轮次不再
    打日志。战斗线程现有的巫医对话框自动处理天然生效，不需要任务侧移植。
    """
    global _wuyi_notice_logged
    if not _wuyi_notice_logged:
        _wuyi_notice_logged = True
        return False, "巫医暂无实现，跳过"
    # TODO(巫医链路)：标定巫医 NPC 后，在此复用「走到 NPC → 点击 →
    # 战斗线程点「全部医治和修复」」链路（参考修罗 _run_wuyi_chain）。
    return False, ""


# ---- 主循环 ----

def run_loop(win, cycle_interval=1.0):
    """种族比武主循环：领任务 → 寻路 → 任务对话 → 战斗 → 返程 → 循环。

    领取逻辑（与修罗同款，固定 5 人）：
    1. 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
    2. 不在领取点时先预检队伍（身份+人数，一次面板开合）；人数满足
       → 走位后直接领取；缺人 → 走位后进入监控循环正常轮询
    3. 首次领取前到达后持续监控队伍人数：满 5 人才领取，不满足
       10 秒重查；自动创建队伍并入首次检查
    4. 队伍检查仅首次领取前执行一次（_team_checked），后续轮次直接
       走位领取；领取失败时补查队伍状态
    领取三件套未就绪（素材缺失/坐标为空）时：追踪中已有任务 →
    正常续跑；否则直接安全结束并提示（不空转、不乱点）。
    结束条件：完成任务次数达到设置的次数上限即返回结束原因。
    """
    global _started_via_option, _team_checked, _task_gone_last_round, \
        _wuyi_notice_logged
    _started_via_option = False
    _team_checked = False
    _task_gone_last_round = False
    _wuyi_notice_logged = False

    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[种族比武] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}",
        "SYS")
    check_team = load_zhongzu_check_team()
    auto_wuyi = load_zhongzu_auto_wuyi()
    wuyi_interval = max(1, load_zhongzu_wuyi_interval())
    log(f"[种族比武] 自动巫医: {'开' if auto_wuyi else '关'}"
        + (f"（每 {wuyi_interval} 轮，链路待实现）" if auto_wuyi else ""),
        "SYS")
    auto_repair = load_zhongzu_auto_repair()
    repair_interval = max(1, load_zhongzu_repair_interval())
    log(f"[种族比武] 自动修理装备: {'开' if auto_repair else '关'}"
        + (f"（每 {repair_interval} 场战斗）" if auto_repair else ""), "SYS")
    log(f"[种族比武] 检查队伍: "
        f"{'开（领取前检查队伍，固定 5 人）' if check_team else '关（跳过队伍检查，直接领取）'}",
        "SYS")
    auto_create = load_zhongzu_auto_create_team()
    log(f"[种族比武] 自动创建队伍: "
        f"{'开（无队伍时任务开始自动创建）' if auto_create else '关'}", "SYS")
    auto_rejoin = load_zhongzu_auto_rejoin()
    log(f"[种族比武] 自动归队: "
        f"{'开（非队长死亡落轮回司自动点归队）' if auto_rejoin else '关'}", "SYS")
    # 申请待处理态清场（启动时组队按钮为申请态则菜单打不开，先清场）
    pending_apply_clear = True
    if not is_in_battle(win):
        pending_apply_clear = False
        hit, apply_clear_info = team.clear_pending_apply(win)
        if hit:
            log(f"[种族比武] 启动清场: {apply_clear_info}", "SYS")
    else:
        log("[种族比武] 战斗中，申请待处理态清场顺延至战斗结束", "INFO")
    pending_create = auto_create
    create_defer_logged = False
    if pending_create and not check_team:
        if not is_in_battle(win):
            pending_create = False
            _auto_create_team_once(win)
        else:
            create_defer_logged = True
            log("[种族比武] 战斗中，自动创建队伍顺延至战斗结束", "INFO")

    done = app_utils.task_progress_done(win, TASK_MODE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[种族比武] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[种族比武] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次种族比武任务，达到次数上限 {max_rounds}"

    # 领取点未标定：追踪中已有任务可续跑；否则安全结束（用户确认）。
    # 置于组号初始化之前——未标定时不产生组号激活/降级等副作用
    if not _claim_configured() and not _task_state(win):
        log(f"[种族比武] {CLAIM_UNCONFIGURED_INFO}", "ERROR")
        log("[种族比武] 请确认 res/img/zhongzu_claim_npc.png 存在，"
            "且 CLAIM_SPOT/CLAIM_MAP_POS 已填写后重新启动", "WARN")
        return CLAIM_UNCONFIGURED_INFO

    # 组号专用（高级设置开关，默认开启）：与修罗完全一致——要求
    # 「检查队伍」必须开启，队长 Tab 带队，队员按账号配置挂战斗/巫医/补血蓝
    tab_group.init_task(win, TASK_MODE, load_zhongzu_tab_group())
    group_leader_confirmed = False
    group_team_count = None
    if tab_group.active():
        if not check_team:
            log("[种族比武] 组号模式要求开启「检查队伍」开关（种族比武设置），"
                "停止任务，请开启后重新启动", "ERROR")
            return "组号模式：请先在种族比武设置开启「检查队伍」开关后重新启动"
        ok_leader, leader_info, panel_names = tab_group.startup_check(
            win, create_if_missing=pending_create)
        pending_create = False
        if not ok_leader:
            if "未组队" in leader_info:
                tab_group.deactivate_group(reason=leader_info)
                log(f"[种族比武] {leader_info}，组号降级为普通非队长模式继续运行",
                    "WARN")
            else:
                log(f"[种族比武] {leader_info}，停止任务（组号模式要求队长带队，"
                    f"请在队长 Tab 下启动）", "ERROR")
                return f"组号模式：{leader_info}，请在队长 Tab 下启动任务"
        else:
            missing, member_info = tab_group.startup_member_check(win,
                                                                  panel_names)
            if missing:
                names = "、".join(f"{m['name']}({m['id']})" for m in missing)
                log(f"[种族比武] 组号队员 {names} 未设置战斗动作，停止任务"
                    f"（请在「组号」设置面板设置后重新启动）", "ERROR")
                return (f"组号模式：请在「组号」设置面板为 {names} "
                        f"设置战斗动作后重新启动")
            log(f"[种族比武] 组号启动检查: {member_info}", "SYS")
            team.record_leader_status(True)
            group_leader_confirmed = True
            group_team_count = tab_group.startup_team_count(panel_names)
            if group_team_count is not None:
                log(f"[种族比武] 组号启动检查：队伍面板可读成员 "
                    f"{group_team_count} 人（满 {TEAM_MIN_MEMBERS} 人免首次领取前"
                    f"队伍检查）", "INFO")
            acts = tab_group.leader_battle_actions()
            log(f"[种族比武] 组号队长战斗动作: 人物 {acts['char']}，"
                f"召唤兽 {acts['beast']}"
                + ("（本账号组号设置）" if tab_group.load_group_actions().get(
                    tab_group.leader_id()) else "（通用战斗设置）"), "SYS")

    round_no = 0
    while True:
        round_no += 1
        log(f"[种族比武] ==== 第 {round_no} 轮 ====", "SYS")
        if tab_group.active() and not tab_group.ensure_leader_tab(win):
            time.sleep(cycle_interval)
            continue
        if pending_apply_clear and not is_in_battle(win):
            pending_apply_clear = False
            hit, apply_clear_info = team.clear_pending_apply(win)
            if hit:
                log(f"[种族比武] 启动清场: {apply_clear_info}", "SYS")
        if pending_create and not check_team and not is_in_battle(win):
            pending_create = False
            _auto_create_team_once(win)
        elif pending_create and not check_team and not create_defer_logged:
            create_defer_logged = True
            log("[种族比武] 战斗中，自动创建队伍顺延至战斗结束", "INFO")

        claim_items = None      # 领取校验帧（续跑路径无）
        repair_round = False    # 本轮是否执行了自动修理
        if _task_gone_last_round:
            task_present = False
        else:
            task_present = _task_state(win)
        if task_present:
            log("[种族比武] 任务追踪中已有种族比武任务，直接跟踪", "SYS")
            team.record_leader_status(True)
            _team_checked = True
        else:
            if _task_gone_last_round:
                _task_gone_last_round = False
                log("[种族比武] 上一轮已确认任务完成，跳过任务检查，直接领取",
                    "INFO")
            if not _claim_configured():
                log(f"[种族比武] {CLAIM_UNCONFIGURED_INFO}", "ERROR")
                return CLAIM_UNCONFIGURED_INFO
            pre_status = None
            if check_team and not _team_checked:
                if group_leader_confirmed:
                    if group_team_count is not None \
                            and group_team_count >= TEAM_MIN_MEMBERS:
                        pre_status = "ready"
                        log(f"[种族比武] 组号启动检查已确认队伍 "
                            f"{group_team_count}/{TEAM_MIN_MEMBERS} 人，"
                            f"跳过首次领取前队伍检查", "SYS")
                    elif group_team_count is not None:
                        log(f"[种族比武] 组号启动检查读到 {group_team_count}/"
                            f"{TEAM_MIN_MEMBERS} 人，走位后进入监控循环补人", "SYS")
                    else:
                        log("[种族比武] 组号启动检查已确认队长（人数未读到），"
                            "走位后进入监控循环检查", "SYS")
                else:
                    # 非队长判定：已在领取点附近时跳过（返程落点就在
                    # 领取点，监控循环马上会检查）；不在则先判身份，
                    # 非队长不白走寻路。瞬态失败原地重查、不走位。
                    img0 = win.capture(force_activate=False)
                    pos0 = game_map.read_map_position(win, img=img0) \
                        if img0 is not None else None
                    frame0 = cv2.cvtColor(np.asarray(img0), cv2.COLOR_RGB2BGR) \
                        if img0 is not None else None
                    at_spot = _near_claim_spot(pos0, CLAIM_NEAR_TOLERANCE) \
                        or (frame0 is not None
                            and (pos0 is None or pos0[0].strip() == CHANGAN)
                            and game_map.match_frame_np(
                                frame0, CLAIM_NPC_ICON) is not None)
                    if not at_spot:
                        while True:
                            status, info = _check_team_with_retry(
                                win, create_if_missing=pending_create)
                            pending_create = False
                            if status == "not_leader" \
                                    and team.is_definitive_not_leader(info):
                                _keep_alive(win, info)
                            if status != "not_leader":
                                pre_status = status
                                break
                            interval = (TEAM_RECOG_RETRY_INTERVAL
                                        if "识别失败" in info
                                        else TEAM_CHECK_RETRY_INTERVAL)
                            log(f"[种族比武] {info}（瞬态失败，"
                                f"{interval}s 后原地重查）", "WARN")
                            time.sleep(interval)
            elif pending_create and not is_in_battle(win):
                pending_create = False
                _auto_create_team_once(win)
            # 1.1 队长：队伍是否满都先走到领取点附近
            debug_overlay.set_phase("前往领取点")
            ok, info = _walk_to_claim_spot(win)
            if not ok:
                log(f"[种族比武] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            if not check_team:
                team.record_leader_status(True)
            if check_team and not _team_checked:
                if pre_status == "ready":
                    log("[种族比武] 走位前队伍检查已通过（人数满足），直接领取",
                        "SYS")
                else:
                    if pre_status == "wait":
                        time.sleep(TEAM_CHECK_RETRY_INTERVAL)
                    debug_overlay.set_phase("检查队伍")
                    log("[种族比武] 首次领取前进入队伍监控循环（缺人自动补）",
                        "SYS")
                    _monitor_team_and_fill(win,
                                           create_if_missing=pending_create)
                    pending_create = False
                    log("[种族比武] 队伍人数满足要求，开始领取", "SYS")
            # 1.3 领取任务
            debug_overlay.set_phase("领取任务")
            ok, info, claim_items = _claim_task(win, allow_fill=check_team)
            if not ok:
                log(f"[种族比武] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[种族比武] {info}", "SYS")
            team.record_leader_status(True)
            _team_checked = True
            _started_via_option = False  # 新一轮开始：重置任务选项标记
            # 自动巫医（链路待实现）：每 N 轮领取后轮到即提示一次后跳过
            wuyi_round = auto_wuyi and round_no % wuyi_interval == 0
            if wuyi_round:
                debug_overlay.set_phase("自动巫医")
                ok2, info2 = _auto_wuyi(win)
                if info2:  # 占位链路每次启动只提示一次（见 _auto_wuyi）
                    log(f"[种族比武][巫医] {info2}", "SYS" if ok2 else "WARN")
            # 自动修理装备（默认开、每 30 场战斗一次）：巫医之后
            repair_round = auto_repair and repair.is_due(
                win, TASK_MODE, repair_interval)
            if repair_round:
                debug_overlay.set_phase("自动修理")
                ok3, info3 = repair.auto_repair(win, TASK_MODE,
                                                prefix="种族比武")
                log(f"[种族比武][修理] {info3}", "SYS" if ok3 else "WARN")
        # 2~4. 寻路 / 任务对话 / 战斗：由任务跟踪统一驱动
        #     修理行程后旧帧已过期——重新截帧识别
        ok, info = _follow_to_done(
            win, first_items=None if repair_round else claim_items)
        if not ok:
            log(f"[种族比武] {info}", "WARN")
            time.sleep(cycle_interval)
            continue
        _task_gone_last_round = True  # 任务消失已确认：下一轮跳过 has_task
        # 5. 返程：仅「由任务选项启动」的轮次结束后才返程
        if _started_via_option:
            debug_overlay.set_phase("返程")
            ok, info = _return_to_npc(win)
            if not ok:
                # 任务已完成：返程失败不吞计数（用户 2026-11-12 确认），
                # 下一轮领取流程会走世界地图回长安兜底
                log(f"[种族比武] {info}（跳过返程，下一轮走地图回长安）", "WARN")
        done += 1
        app_utils.task_progress_add(win, TASK_MODE, done)
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        debug_overlay.record_round()  # 统计页：完成轮次计数
        log(f"[种族比武] 本轮种族比武任务完成（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次种族比武任务，达到次数上限 {max_rounds}"
