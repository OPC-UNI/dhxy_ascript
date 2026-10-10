"""五倍任务自动化。

流程（与修罗基本一致，差异点待用户提供）：
0. 任务追踪中已有五倍任务（续跑）→ 跳过领取，直接跟踪
1. 领任务（已实现）：宝象国领取点
   1.0 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
   1.1 队长先走到领取点附近（队伍是否满都先走位）：
       已在领取点 86,86 附近（±15）→ 跳过寻路；
       不在宝象国 → 世界地图点击宝象国图标 → 小地图点击 (514,483) 寻路；
       在宝象国但不在领取点附近 → 直接打开小地图点击 (514,483) 寻路
       寻路等待到达 86,86（±5，更严格：走动中识图点击容易点不到）
   1.2 到达后持续监控队伍人数（通用设置-五倍设置，仅队长生效）：
       满足最少人数才领取；不满足 10 秒后重查，人留在领取点；
       队长被转移则转 1.0 非队长模式
   1.3 识图 wubei_lingqu.png（领取任务按钮）/ wubei_xiangmoshiwei.png
       （降魔侍卫 NPC）点击 → 对话框点绿色选项「除魔卫国，义不容辞」
       → 校验任务出现在追踪面板
2. 寻路：点击任务追踪绿字自动寻路（复用 xuanshu.follow_task）
3. 对话开打：寻路到达后出现对话面板 → 点击绿色选项进入战斗
   （选项文案随场景变化，FIGHT_DIALOG_OPTIONS 逐一匹配；点击成功
   标记本轮战斗由对话开打进入）
4. 战斗：战斗由 battle.watch_loop 线程自动操作；follow_task 战斗中自动跳过识别
5. 返程（已实现，逻辑与修罗一致）：战斗结束后用五倍回城符回宝象国
   （仅对话开打进入的战斗才返程，其他方式进入的战斗不自动返程）：
   打开背包 → 打开任务储物格（box_task/box_task_selected 双模板比分数
   判定）→ 右键五倍回城符 → 关闭世界地图/小地图/背包（回城符模板
   wubei_huichengfu.png 待用户提供，缺失时返程失败走世界地图寻路兜底）
6. 循环往复；达到次数上限时结束任务进程

战斗由 battle.watch_loop 线程自动处理——任务启动即运行、任何模式下生效。

对话框选项实测（2026-10-08）：选项与故事文本、拒绝选项同框渲染，
「除魔卫国，义不容辞」单行完整文案首轮命中（0.89，绿色像素判定把
非绿色故事文本排除）；匹配仍按 完整文案 → 尾部「义不容辞」→「不容辞」
兜底 三级尝试以防换行渲染。对话框点屏幕中间不关闭，须点选项退出——
验证模式点拒绝选项「事态严重，我等退散」关闭且不领取（实测绿色）。
追踪面板标题实测为「xxxx[除魔任务]」（OCR 常读作「降魔任务」，
见 TASK_TITLE 注释：用两种读法的公共尾部「魔任务」匹配）。
"""
import re
import time
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard  # Alt+C 飞行（领取验证兜底）
from ascript.windows.system import R  # 资源路由（模板路径）

from .app_utils import human_click, human_move, log, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import task_tracker  # 任务追踪面板操作
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import team  # 组队检查（领取前队伍人数检查，仅队长生效）
from . import bag  # 背包操作（打开/关闭背包、任务储物格）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from .control_window import (load_task_counts,  # 任务次数上限
                             load_wubei_min,  # 五倍设置：最少人数
                             load_wubei_check_team)  # 五倍设置：检查队伍开关
from .dialog_common import (SCREEN_CENTER_POS, green_option_center,
                            _dialog_text_present,
                            _click_screen_center)  # 对话框共享组件（屏幕中间点击/选项中心/文案检测）
from .xuanshu import follow_task  # 任务跟踪（绿字点击寻路/战斗跳过/附加对话处理）

# 任务模式与追踪面板标题（用户 2026-10-08 提供：追踪面板标题为
# 「xxxx[除魔任务]」——前缀是彩色徽标（如「降魔」），OCR 时有时无
# （修罗实测徽标丢失读成「任务【常规玩法］」），用徽标之外的稳定
# 公共子串作匹配串，领取校验与跟踪锚点通用。
# 实测 OCR 读到的分类文案为「降魔任务」（截图 4/6 均读「降」，
# 与早期校准的「除魔任务」不一致——OCR 对该字稳定误读或游戏原文
# 变化），匹配串取两种读法的公共尾部「魔任务」兼容两者
TASK_MODE = "五倍"
TASK_TITLE = "魔任务"
TASK_FALLBACK_TEXTS = ()  # 兜底匹配串待用户提供任务内容实测后补充

# ---- 领取任务（宝象国，用户实测） ----
BAOXIANGGUO = "宝象国"                              # 位置识别地名（精确匹配）
WORLD_MAP_BAOXIANGGUO_ICON = "shijie_baoxiangguo.png"  # 世界地图宝象国图标（用户素材 17.png）
LINGQU_ICON = "wubei_lingqu.png"                   # 五倍领取任务按钮（用户素材 18.png）
XIANGMOSHIWEI_ICON = "wubei_xiangmoshiwei.png"     # 降魔侍卫 NPC（用户素材 19.png，
                                                   # 待机动画致分数波动，轮询重试覆盖）
CLAIM_SPOT = (86, 86)                              # 领取点地图坐标（用户实测 86,86 附近）
CLAIM_NEAR_TOLERANCE = 15                          # 已在领取点附近判定容差（用户步 1.1）
WALK_ARRIVE_TOLERANCE = 5                          # 寻路到达判定容差（用户步 1.2，更严格：
                                                   # 走动中识图点击容易点不到）
BAOXIANG_MAP_POS = (514, 483)                      # 宝象国小地图上领取点位置的点击点（窗口相对坐标，用户实测）
CLAIM_DIALOG_OPTION = "除魔卫国，义不容辞"           # 领取对话框绿色选项（游戏内原文，实测用字）
CLAIM_OPTION_TAIL = "义不容辞"                      # 选项换行渲染时的尾部匹配串
DECLINE_DIALOG_OPTION = "事态严重，我等退散"          # 领取对话框拒绝选项（实测绿色）：
                                                   # 该对话框点屏幕中间不关闭，验证模式
                                                   # 点拒绝选项关闭（不领取）
CLAIM_ARRIVE_TIMEOUT = 90                          # 寻路到领取点的超时
CLAIM_BUTTON_TIMEOUT = 20                          # 到达后等待领取入口出现的超时
CLAIM_DIALOG_TIMEOUT = 15                          # 领取对话框处理超时
CLAIM_DIALOG_APPEAR_TIMEOUT = 4                    # 点击领取入口后等待对话框出现的短窗口
                                                   # （正常 ~1.5s 即出）：超时未见选项判定
                                                   # 坐标不对，走小地图重定位兜底
CLAIM_VERIFY_WAIT = 5                              # 领取后任务进入追踪的正常轮询时长（秒）：
                                                   # 超时未检索到 → 按一次 Alt+C 飞行后仅再检索一次，
                                                   # 仍无即判定失败（用户确认：多次重试无效）

# ---- 对话开打（寻路到达后出现对话面板）----
# 绿色选项文案随场景变化（用户实测五倍不是「看打！」，可能是
# 「证明实力」「消灭它」）；全部枚举逐一匹配，命中即点
FIGHT_DIALOG_OPTIONS = ["看打！", "证明实力", "消灭它","奎星归位"]

# ---- 显形镜查探（宝象迷情[降魔任务]；追踪链接「前往火云壁、火云
#      戈壁处使用显形镜查探」换行渲染，A=火云壁 B=火云戈壁） ----
XIANXINGJING_DIALOG_TEXT = "一阵光芒闪过，白龙马出现在眼前，竟开口说起话来。"
XIANXINGJING_TASK_ICON = "wubei_bailongma_task.png"  # 白龙马任务按钮（用户提供素材）
# 流程（用户实测）：白龙马对话框出现 → 对话框文字遮挡任务按钮模板，
# 先连点屏幕中间两次关闭文字 → 再识别点击模板开始对话；后续开打
# 选项（证明实力/消灭它）由 FIGHT_DIALOG_OPTIONS 处理
XIANXINGJING_TEMPLATE_WINDOW = 6.0  # 关闭对话框后找模板的窗口（秒）
XIANXINGJING_LINK_KEYWORD = "镜"  # 追踪链接关键词（识别显形镜任务阶段）。
# 用单字「镜」而非「显形镜」：OCR 对「显」稳定误读（实测读成「豆形镜」），
# 三字全匹配会漏检导致状态机全程不启动；「镜」在五倍其他任务的追踪
# 文案中不出现（消灭魔尊/奎木狼/营救三藏等），误报风险可忽略
XIANXINGJING_GRACE = 6.0              # 用镜后等白龙马对话框的宽限（秒）
XIANXINGJING_ARRIVE_POLLS = 3         # 位置连续稳定轮询数判定到达（每次 ~1.7s）
XIANXINGJING_USE_MAX = 2              # 最多用镜次数（A、B 两处）
# A→B 回退状态机（A 失败后追踪签名不变，follow 不会重点链接——
# 本状态机按到达/用镜/宽限推进；关键词连续消失或新领取时重置。
# 注意：不能按签名文本变化复位——OCR 噪声（「前」读成「剪」、「显」
# 读成「豆」）让签名每轮询抖动，复位会把位置稳定计数清零，
# 到达判定永远凑不满 3 次）
_xj_phase = 0           # 0=等到达 1=已用镜等对话框
_xj_stable = 0          # 位置连续稳定轮询数
_xj_last_pos = None     # 上次位置
_xj_use_count = 0       # 已用镜次数
_xj_grace_start = 0.0   # 宽限窗口起点
_xj_warned = False      # 两处均失败仅告警一次
_xj_missing = 0         # 关键词连续消失轮询数（≥2 视为阶段结束）
_xj_need_move = False   # 点寻路链接后须位置先变化才允许再次用镜
_xj_walk_from = None    # 寻路链接点击时的位置（验证移动起点）
_xj_dialog_clicked_at = 0.0  # 白龙马对话框屏幕中间连点时间（冷却防连点）
_xj_dismissed_at = 0.0       # 对话框关闭时间（此后窗口内找模板点击）


def _reset_xianxingjing_state():
    """显形镜状态机复位（任务领取成功后调用，防跨任务残留）。"""
    global _xj_phase, _xj_stable, _xj_last_pos, _xj_use_count, \
        _xj_grace_start, _xj_warned, _xj_missing, _xj_need_move, _xj_walk_from, \
        _xj_dialog_clicked_at, _xj_dismissed_at
    _xj_phase = 0
    _xj_stable = 0
    _xj_last_pos = None
    _xj_use_count = 0
    _xj_grace_start = 0.0
    _xj_warned = False
    _xj_missing = 0
    _xj_need_move = False
    _xj_walk_from = None
    _xj_dialog_clicked_at = 0.0
    _xj_dismissed_at = 0.0

# ---- 领取前队伍检查 ----
TEAM_CHECK_RETRY_INTERVAL = 10  # 队长人数不足时的重试间隔（秒）
TEAM_RECOG_RETRY_INTERVAL = 3   # 成员识别失败（OCR 噪声）时的重试间隔（秒）：
                                # 识别失败≠人数不足，等满 10s 纯属空转

# ---- 返程（用户流程：背包任务储物格 → 五倍回城符右键 → 回宝象国；
#      逻辑与修罗完全一致，仅回城符模板不同——待用户提供） ----
BOX_TASK_ICON = "box_task.png"                    # 背包任务储物格按钮（未选中态，点击打开）
BOX_TASK_SELECTED_ICON = "box_task_selected.png"  # 任务储物格已打开（选中态，无需再点击）
WUBEI_RETURN_ICON = "wubei_huichengfu.png"        # 五倍回城符模板（TODO: 待用户提供）
TASK_BOX_THRESHOLD = 0.8         # 储物格按钮双模板判定阈值（与其他模板一致）
TASK_BOX_TIMEOUT = 10            # 等待任务储物格打开的超时（点击后渲染/切换）
RETURN_TALISMAN_TIMEOUT = 15     # 等待回城符在任务储物格中出现的超时
RETURN_POLL_INTERVAL = 0.5       # 返程轮询间隔（打开动画约 0.4s，实测点击后 1s 内
                                 # 出现选中态；非防抖判定点，无需更长窗口）
# 任务储物格按钮相对背包面板标志（bag_box 中心）的偏移与搜索条带：
# 面板可拖动但内部布局固定，偏移恒定（修罗模块两位置实测
# +349/+305 与 +353/+305，取均值）；条带留 55px 余量，带区全未命中
# 才全帧兜底（限频：仅带区未命中时触发）
TASK_BOX_OFFSET = (351, 305)
TASK_BOX_BAND_MARGIN = 55

_team_checked = False  # 队伍检查已通过（首次领取成功后置位）：
                       # 后续轮次跳过检查——与修罗一致，每轮开面板检查
                       # 耗时 5~7s，只需第一次领取前检查一次
_fight_via_kanda = False        # 本轮战斗是否由「看打！」进入（决定战后是否返程）
_task_gone_last_round = False   # 上一轮任务消失已确认（follow_task 完成判据）：
                                # 下一轮开始必然无任务，跳过 has_task 检查
                                # （区域 OCR ~0.9s）；仅进程首轮/上轮跟踪失败
                                # 未确认时才检查（续跑场景）

# ---- 战斗（战斗操作由 battle.watch_loop 线程负责） ----
BATTLE_END_POLL = 2.0            # 等待战斗结束的轮询间隔


def _wait_battle_end(win, timeout=60):
    """战斗：等待当前战斗结束（战斗操作由 battle.watch_loop 线程自动完成）。

    轮询 is_in_battle 直到战斗画面消失；超时返回失败防止卡死（验证流程用）。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    while is_in_battle(win):
        if time.time() > deadline:
            return False, f"等待战斗结束超时（{timeout}s）"
        time.sleep(BATTLE_END_POLL)
    return True, "战斗已结束"


def _near_claim_spot(pos, tolerance):
    """判断位置是否在宝象国领取点附近（地名精确匹配，容差抗 OCR 噪声）。"""
    return game_map.is_at_position(pos, BAOXIANGGUO,
                                   CLAIM_SPOT[0], CLAIM_SPOT[1], tolerance)


def _wait_claim_entry(win, timeout):
    """等待领取入口出现（按钮或降魔侍卫），返回 (pt, 来源) 或 (None, None)。

    战斗中跳过等待；每轮询只截一帧，双模板批量匹配共用一帧。
    降魔侍卫模板受待机动画影响分数波动（实测 0.91~0.95 与低相位交替），
    1.5s 轮询重复采样覆盖波动相位。
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
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        found = game_map.match_frame_multi(frame, (LINGQU_ICON, XIANGMOSHIWEI_ICON))
        for icon, label in ((LINGQU_ICON, "领取任务按钮"),
                            (XIANGMOSHIWEI_ICON, "降魔侍卫")):
            if found[icon] is not None:
                return found[icon], label
        time.sleep(1.5)
    return None, None


def _find_claim_option(items, img_np):
    """在 OCR 结果中查找领取对话框的绿色选项，返回 (cx, cy, txt, 是否兜底) 或 None。

    选项可能换行渲染：按优先级依次尝试 完整文案 → 尾部「义不容辞」→
    「不容辞」兜底。OCR 对中文标点误读常见，匹配前统一去标点。
    限定对话框区域（x≥PANEL_MAX_X），避免误点追踪面板的绿字链接；
    故事文本里的近义词（如「为国为家」）由绿色像素判定过滤。
    点击与验证（悬停）共用本函数。
    """
    needles = (re.sub(r"[^\w]", "", CLAIM_DIALOG_OPTION),  # 完整文案（单行渲染时）
               norm_text(CLAIM_OPTION_TAIL),             # 尾部（换行渲染时命中）
               norm_text("不容辞"))                      # 最后兜底
    for needle in needles:
        for txt, rect, score in items:
            if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                # 合并行按比例取选项自身中心（见 dialog_common.green_option_center）
                ntxt = re.sub(r"[^\w]", "", txt)
                cx, cy = green_option_center(img_np, rect, len(needle), len(ntxt))
                return cx, cy, txt, needle == norm_text("不容辞")
    return None


def _click_claim_option(win, items, img_np):
    """点击领取对话框中的绿色选项「除魔卫国，义不容辞」，成功返回 True。

    识别点击成功即记录选项坐标（高性能模式缓存用；位置漂移时由
    本路径自然刷新）。
    """
    found = _find_claim_option(items, img_np)
    if found is None:
        return False
    cx, cy, txt, fallback = found
    human_click(win, cx, cy)
    app_utils.fixed_pos_set(f"option_{CLAIM_DIALOG_OPTION}", win, cx, cy)
    suffix = "（兜底匹配）" if fallback else ""
    log(f"[五倍] 已点击绿色选项「{txt}」{suffix}", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _find_decline_option(items, img_np):
    """在 OCR 结果中查找领取对话框的拒绝选项「事态严重，我等退散」。

    实测该对话框点屏幕中间不关闭（与修罗领取对话框不同），验证模式
    用拒绝选项关闭且不领取。返回 (cx, cy, txt) 或 None。
    """
    needles = (re.sub(r"[^\w]", "", DECLINE_DIALOG_OPTION),  # 完整文案（单行渲染时）
               norm_text("退散"))                            # 尾部兜底
    for needle in needles:
        for txt, rect, score in items:
            if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                cx, cy = task_tracker.green_text_center(img_np, rect,
                                                        prefer_underline=False)
                return cx, cy, txt
    return None


def _close_dialog_without_claim(win, items, img_np):
    """验证模式关闭领取对话框且不领取：优先点拒绝选项「事态严重，我等
    退散」（实测屏幕中间点击关不掉），拒绝选项未识别时兜底点屏幕中间。
    """
    found = _find_decline_option(items, img_np)
    if found is not None:
        cx, cy, txt = found
        human_click(win, cx, cy)
        log(f"[五倍][验证] 已点击拒绝选项「{txt}」关闭对话框（未领取）", "SYS")
        return
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[五倍][验证] 已点击屏幕中间关闭对话框（未领取）", "SYS")


def _walk_to_claim_spot(win):
    """寻路到宝象国领取点附近（与队伍人数无关，队长身份下执行）。

    用户流程：已在领取点 86,86 附近（±15）→ 跳过寻路；
    不在宝象国 → 世界地图点击宝象国图标 → 小地图点击 (514,483) 寻路；
    在宝象国但不在领取点附近 → 直接打开小地图点击 (514,483) 寻路。
    等待到达 86,86（±5 严格容差：走动中识图点击容易点不到）。

    到达判定优先用直接信号（领取任务按钮可见 = 已到领取点）——
    位置 OCR 坐标不可靠时按钮判据兜底（修罗实测教训：区域快引擎
    把 111,90 误读为 11,90，玩家站在领取点却被判定未到达）。
    位置读取失败也按不在附近处理，直接走地图寻路（用户 2026-10-09
    确认）：加载/渲染等待由 open_mini_map_city 的轮询吸收，原 8s
    等待 + Alt+C 恢复实测整段空转（且 Alt+C 是位置显示开关，
    加载期误按会关掉显示——此处不再按）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    # 直接信号：领取按钮可见 = 已在领取点附近（免位置 OCR，规避坐标
    # 误读）。注意到达判据只认按钮——NPC 模板受动画影响有相位波动，
    # 且远距离也可能命中，不能作到达判据（修罗使者教训）
    if game_map.find_template(win, LINGQU_ICON) is not None:
        return True, "已在领取点附近（领取按钮可见）"
    # 位置读取：失败按不在附近处理，直接走地图寻路（不等待不 Alt+C）
    pos = game_map.read_map_position(win)
    if _near_claim_spot(pos, CLAIM_NEAR_TOLERANCE):
        return True, "已在领取点附近"
    if pos is None or pos[0].strip() != BAOXIANGGUO:
        # 不在宝象国：世界地图点击宝象国图标，打开宝象国小地图
        ok, info = game_map.open_mini_map_city(win, WORLD_MAP_BAOXIANGGUO_ICON,
                                               BAOXIANGGUO)
        if not ok:
            return False, info
    else:
        # 在宝象国但不在领取点附近：直接打开小地图
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    human_click(win, BAOXIANG_MAP_POS[0], BAOXIANG_MAP_POS[1])
    log("[五倍] 已点击宝象国小地图领取点位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    game_map.close_maps(win)
    # 等待到达：按钮可见优先（直接信号），坐标 ±5 作为次级判据
    # （坐标误读时按钮判据兜底）。每轮询只截一帧：战斗判定与按钮
    # 匹配共用（is_in_battle_frame + match_frame_np）；位置 OCR 为
    # 次级判据限频每 3 轮询一次（按钮判据覆盖绝大多数到达）
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    _walk_pos = None
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        if game_map.match_frame_np(frame, LINGQU_ICON, fallback=False) is not None:
            log("[五倍] 已到达领取点附近（领取按钮可见）", "SYS")
            return True, "已到达领取点附近"
        polls += 1
        if polls % 3 == 1:
            _walk_pos = game_map.read_map_position(win)
            if _near_claim_spot(_walk_pos, WALK_ARRIVE_TOLERANCE):
                log("[五倍] 已到达领取点附近", "SYS")
                return True, "已到达领取点附近"
        if polls % 5 == 0:
            log(f"[五倍] 前往领取点中... 当前位置: {_walk_pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"寻路到领取点超时（未到达宝象国 {CLAIM_SPOT[0]},{CLAIM_SPOT[1]} 附近）"


def _relocate_to_claim_spot(win):
    """小地图点击领取点坐标重新寻路（领取校验兜底，修罗同款）。

    领取已点但任务追踪未出现时调用：重走一次领取点坐标可能触发任务
    渲染。与 _walk_to_claim_spot 的区别：无条件走小地图（不判断位置，
    通常在领取点原地重走，几秒即回）。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过重走"
    ok, info = game_map.open_mini_map(win)
    if not ok:
        return False, info
    human_click(win, BAOXIANG_MAP_POS[0], BAOXIANG_MAP_POS[1])
    log("[五倍] 已点击宝象国小地图领取点位置，重新寻路（领取校验兜底）", "SYS")
    time.sleep(1.0)
    game_map.close_maps(win)
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        if game_map.find_template(win, LINGQU_ICON) is not None:
            return True, "已重走到领取点附近（领取按钮可见）"
        pos = game_map.read_map_position(win)
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            return True, "已重走到领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[五倍] 重走领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, "重走领取点超时"


def _click_entry_ensuring_dialog(win, log_tag="[五倍]", hover_before_click=False):
    """点击领取入口（按钮或降魔侍卫）并确保对话框出现；未出现判定坐标不对：
    打开小地图点击领取点坐标重定位后重试一次（修罗同款兜底路径）。

    hover_before_click：点击前先移动鼠标定位入口（无延时，验证时序与真实流程一致）。
    返回 (是否成功, 描述, 来源, 坐标, 对话框帧)；成功时对话框已打开
    （选项可见），对话框帧 (items, img_np) 供领取流程同帧点选项——
    免重截+重 OCR。
    """
    dialog_seen = False
    appear_timeout = CLAIM_DIALOG_APPEAR_TIMEOUT
    src2 = pt2 = None
    seen_frame = None
    for attempt in (1, 2):
        # 入口坐标直接用等待循环最近一帧的匹配结果点击：等待刚命中后
        # 再补一次识别（旧双检）会因 NPC 动画/镜头未稳出现「出现后又
        # 消失」假失败（修罗实测到达秒级报错未点击）；点不中由对话框
        # 出现判定兜底（小地图重定位重试）
        pt2, src2 = _wait_claim_entry(win, CLAIM_BUTTON_TIMEOUT)
        if pt2 is None:
            return False, "到达后未出现领取任务按钮/降魔侍卫", None, None, None
        if hover_before_click:
            human_move(win.rect.left + pt2[0], win.rect.top + pt2[1])
            log(f"{log_tag} 已移动鼠标到{src2} ({pt2[0]}, {pt2[1]})，"
                f"已定位到（验证时序与真实流程一致）", "SYS")
        human_click(win, pt2[0], pt2[1])
        log(f"{log_tag} 已点击{src2}（领取）", "SYS")
        # 高性能：领取选项坐标已缓存 → 跳过对话框 OCR 轮询，固定等待
        # 渲染后由 _claim_task 直接按坐标点选项（点不中由「任务进追踪」
        # 校验循环识别补点兜底，无 seen 帧返回）
        if app_utils.high_perf_on() \
                and app_utils.fixed_pos_get(f"option_{CLAIM_DIALOG_OPTION}", win) is not None:
            time.sleep(app_utils.HIGH_PERF_DIALOG_WAIT)
            dialog_seen = True
            break
        # 等待对话框出现：轮询选项（正常 ~1.5s 即出）；首次尝试短窗口，
        # 重定位后的第二次尝试用完整窗口
        deadline = time.time() + appear_timeout
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.3)
                continue
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _find_claim_option(items, img_np) is not None:
                dialog_seen = True
                seen_frame = (items, img_np)
                break
            time.sleep(0.3)
        if dialog_seen:
            break
        if attempt == 1:
            # 对话框未出现：坐标不对 → 打开小地图点击领取点坐标重定位
            log(f"{log_tag} 点击后未出现对话框（坐标不对），打开小地图重定位", "WARN")
            ok, info = game_map.open_mini_map(win)
            if not ok:
                return False, info, None, None, None
            human_click(win, BAOXIANG_MAP_POS[0], BAOXIANG_MAP_POS[1])
            log(f"{log_tag} 已点击宝象国小地图领取点位置，重新寻路中", "SYS")
            time.sleep(1.0)
            game_map.close_maps(win)
            # 等待重定位到达（只认领取按钮——按钮才代表进入可点击范围，
            # NPC 模板动画波动且远距离也可能命中，不能作到达判据）
            deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
            while time.time() < deadline:
                if is_in_battle(win):
                    time.sleep(2.0)
                    continue
                if game_map.find_template(win, LINGQU_ICON) is not None:
                    break
                time.sleep(2.0)
            appear_timeout = CLAIM_DIALOG_TIMEOUT
    if not dialog_seen:
        return False, "点击领取入口后对话框始终未出现（小地图重定位后仍无）", src2, pt2, None
    return True, f"已点击{src2}", src2, pt2, seen_frame


def _claim_task(win):
    """在领取点附近领取五倍任务（前提：已走到领取点附近）。

    识图 wubei_lingqu.png / wubei_xiangmoshiwei.png 点击 → 对话框点绿色
    选项「除魔卫国，义不容辞」→ 校验任务出现在追踪面板。

    返回 (是否成功, 描述, 校验帧)：校验帧为任务进追踪的确认帧
    （items, img_np），供 follow_task 首轮复用免重复截图+OCR；
    Alt+C 兜底路径无帧返回 None。

    领取时序优化（与修罗同款，实测一轮入口点击→选项点击 4s、选项点击
    →已领取判定 4s、判定→追踪点击 2s）：
    1. 选项点击直接复用「对话框已出现」的同一帧（免重截+重 OCR）
    2. 校验循环内确认任务出现即返回，不再重复一次 has_task 全量检查
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取", None
    # 识图领取入口（按钮或降魔侍卫）并点击；点击后对话框未出现
    # （短窗口内选项不可见）判定坐标不对：打开小地图点击领取点坐标
    # 重定位后重试一次（修罗同款兜底路径）
    ok, info, _src2, _pt2, seen = _click_entry_ensuring_dialog(win)
    if not ok:
        return False, info, None
    # 对话框：点绿色选项「除魔卫国，义不容辞」完成领取——直接用
    # 「对话框已出现」的同一帧点击（帧上已确认选项可见，点同一帧
    # 与 0.3s 后重截重 OCR 再点等价）；未点中再走短窗轮询兜底。
    # 完整文案与「不容辞」兜底在同一帧先后尝试（OCR 把选项拆成
    # 两行时完整串永远匹配不上——修罗实测教训）
    used_cached = False
    if seen is None:
        # 高性能：入口点击后未 OCR（缓存直点路径）→ 按缓存坐标点
        # 选项，生效与否由下方「任务进追踪」校验循环判定并补点兜底
        used_cached = app_utils.high_perf_click(win, f"option_{CLAIM_DIALOG_OPTION}",
                                                "领取选项", prefix="[五倍] ")
        claimed = used_cached
    else:
        items, img_np = seen
        claimed = _click_claim_option(win, items, img_np)
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while not claimed and time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        claimed = _click_claim_option(win, items, img_np)
        time.sleep(0.3)
    if not claimed:
        return False, "领取对话框处理失败", None
    # 校验任务出现在追踪面板：正常轮询 CLAIM_VERIFY_WAIT 秒；任务
    # 出现即确认（不再重复一次 has_task 全量检查）；缓存点击未生效
    # 时选项仍在，识别补点（并刷新缓存）。未出现则按一次 Alt+C
    # （飞行/刷新界面状态）后仅再检索一次——修罗实测多次重试无效
    deadline = time.time() + CLAIM_VERIFY_WAIT
    found_frame = None
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        items, img_np = game_map.ocr_regions(img, task_tracker.OCR_REGIONS)
        if task_tracker.has_task_in(items, img_np, TASK_TITLE, TASK_FALLBACK_TEXTS):
            found_frame = (items, img_np)
            break
        if used_cached and _find_claim_option(items, img_np) is not None:
            _click_claim_option(win, items, img_np)  # 识别补点并刷新缓存
        time.sleep(1.0)
    if found_frame is not None:
        return True, "五倍任务已领取", found_frame
    # 未检索到：按 Alt+C 飞行后再次检索（仅一次，多次重试无效）
    Keyboard.key_hot("alt", "c")
    log("[五倍] 任务追踪未检索到任务，按 Alt+C 飞行后再次检索", "WARN")
    time.sleep(1.0)
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        log("[五倍] Alt+C 飞行后任务已出现在追踪面板", "SYS")
        return True, "五倍任务已领取", None
    # Alt+C 后仍无：小地图重走领取点坐标后再次检索（修罗同款，用户
    # 2026-10-09 确认：重走可能触发任务渲染，不直接判失败）
    ok2, info2 = _relocate_to_claim_spot(win)
    if not ok2:
        log(f"[五倍] {info2}", "WARN")
    elif task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        log("[五倍] 重走领取点后任务已出现在追踪面板", "SYS")
        return True, "五倍任务已领取", None
    # 重走后仍无：不再重试，直接失败（下一轮 has_task 检查兜底，
    # 任务若随后渲染出现会以续跑方式被拾起）
    try:
        titles = [t["title"] for t in task_tracker.read_tasks(win)]
        log(f"[五倍] 验证失败，追踪面板当前标题: "
            f"{' / '.join(titles) if titles else '(无标题行)'}", "WARN")
    except Exception:
        pass
    return False, "已点击领取选项，但任务追踪中未检测到任务（Alt+C 飞行后仍无）", None


def _click_xianxingjing_use(win, items, img_np):
    """点击任务追踪面板中的显形镜使用文案（用户确认的用镜方式）。

    优先不含「前往」的行（使用链接，如「用显形镜查探。」），再优先
    绿色、较短的行。点击点为文本矩形中心：实测该行文字非绿色、只有
    底部绿色下划线，绿字中心会点到下划线上（点文字才生效，用户手动
    实测）。无显形镜文案返回 False。
    """
    cands = [(t, r, "前往" not in t, task_tracker.is_green_text(img_np, r))
             for t, r, s in items
             if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X
             and XIANXINGJING_LINK_KEYWORD in t]
    if not cands:
        return False
    cands.sort(key=lambda c: (not c[2], not c[3], len(c[0])))
    txt, rect, _, _ = cands[0]
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    human_click(win, cx, cy)
    log(f"[五倍] 显形镜：已点击追踪文案「{txt}」用镜 @({cx},{cy})", "SYS")
    move_away(win)
    return True


def _last_green_cluster_center(img_np, rect):
    """行内最右侧绿色文字簇的中心（寻路行的 B 位置链接）。

    实测寻路行「前往[A]、[B]处使用」含两个绿色链接（x 60-108 与
    126-174），整行中心落在两链接间隙——A 失败后应点最右簇（B）。
    无绿色簇时回退行矩形中心。
    """
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]].astype(int)
    if region.size == 0:
        return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    mask = task_tracker.green_mask(region, 120)
    n, _, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w >= 4 and area >= 10 and (best is None or x > best[0]):
            best = (x, y, w, h)
    if best is None:
        return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    x, y, w, h = best
    return rect[0] + x + w // 2, rect[1] + y + h // 2


def _click_xianxingjing_walk(win, items, img_np):
    """点击任务追踪面板中的寻路链接（前往…处），用于 A 失败后去 B。

    优先含「前往」的绿字行；点击其最右侧绿色簇（B 链接）中心。
    """
    cands = [(t, r) for t, r, s in items
             if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X
             and task_tracker.is_green_text(img_np, r)]
    if not cands:
        return False
    for txt, rect in cands:
        if "前往" in txt:
            cx, cy = _last_green_cluster_center(img_np, rect)
            human_click(win, cx, cy)
            log(f"[五倍] 显形镜：已点击寻路链接「{txt}」前往下一处 @({cx},{cy})", "SYS")
            move_away(win)
            return True
    return False


def _handle_xianxingjing(win, items, img_np):
    """显形镜查探：白龙马对话框出现时先连点屏幕中间两次关闭遮挡
    文字（对话框文字挡住任务按钮模板），随后窗口内识别点击模板
    开始对话；开打选项（证明实力/消灭它）由 _handle_fight_dialog
    处理。对话框持续存在时按 3 秒冷却重试。返回是否已处理。
    """
    global _xj_dialog_clicked_at, _xj_dismissed_at
    if _dialog_text_present(items, XIANXINGJING_DIALOG_TEXT):
        now = time.time()
        if now - _xj_dialog_clicked_at >= 3.0:
            _xj_dialog_clicked_at = now
            _click_screen_center(win, times=2, prefix="五倍")
            _xj_dismissed_at = now  # 关闭后开始窗口内找模板
            log("[五倍] 显形镜：白龙马对话框已出现，已连点屏幕中间两次关闭", "SYS")
        return True
    # 对话框文字已消失：刚关闭过的窗口内识别点击任务按钮模板
    if time.time() - _xj_dismissed_at < XIANXINGJING_TEMPLATE_WINDOW:
        pt = game_map.find_template(win, XIANXINGJING_TASK_ICON)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[五倍] 显形镜：已点击白龙马任务按钮 @ ({pt[0]},{pt[1]})，开始对话", "SYS")
            move_away(win)
            _xj_dismissed_at = 0.0  # 已点击：关闭找模板窗口
            return True
        return False  # 模板暂未出现：不阻塞其他处理，下轮询继续找
    return False


def _handle_xianxingjing_track(win, items, img_np):
    """显形镜查探 A→B 回退（用户实测 A 处用镜失败后追踪签名不变）：
    到达（位置连续稳定）→ 点链接用镜 → 宽限窗口等白龙马对话框 →
    未出现则点链接去下一处（B）再用镜；两处均失败告警一次。
    对话框出现时由 _handle_xianxingjing（调用链上游）处理。
    返回是否已处理（点击了链接）。
    """
    global _xj_phase, _xj_stable, _xj_last_pos, _xj_use_count, \
        _xj_grace_start, _xj_warned, _xj_missing, _xj_need_move, _xj_walk_from
    sig = "".join(norm_text(t) for t, r, s in items
                  if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X)
    if XIANXINGJING_LINK_KEYWORD not in sig:
        # 非显形镜任务阶段：连续 2 轮询消失才复位状态（单轮询 OCR
        # 漏读不误复位）
        _xj_missing += 1
        if _xj_missing >= 2:
            _reset_xianxingjing_state()
        return False
    _xj_missing = 0
    now = time.time()
    # phase 1：已用镜，等白龙马对话框（出现时上游处理器负责，本机
    # 冻结宽限窗口；模板未匹配时对话框持续存在，不能误点链接）。
    # 位置无关，跳过位置读取（区域 OCR ~150ms/轮询）
    if _xj_phase == 1:
        if _dialog_text_present(items, XIANXINGJING_DIALOG_TEXT):
            _xj_grace_start = now
            return False
        if now - _xj_grace_start < XIANXINGJING_GRACE:
            return False
        # 宽限已过未出对话框：本处失败 → 点寻路链接去下一处（位置会变，
        # 回等到达）；点链接时读一次位置作移动验证起点（仅此一次）
        pos = game_map.read_map_position(win)
        if _click_xianxingjing_walk(win, items, img_np):
            log("[五倍] 显形镜：本处未触发对话框，已点寻路链接前往下一处", "WARN")
            _xj_phase = 0
            _xj_stable = 0
            _xj_need_move = True    # 须位置变化（寻路启动）后才允许再次用镜
            _xj_walk_from = pos if pos is not None else _xj_last_pos
            return True
        log("[五倍] 显形镜：追踪面板未找到寻路链接，下轮询重试", "WARN")
        return False
    # phase 0：等到达（需要位置）
    pos = game_map.read_map_position(win)
    if pos is None:
        return False
    if _xj_last_pos is not None and abs(pos[1] - _xj_last_pos[1]) <= 3 \
            and abs(pos[2] - _xj_last_pos[2]) <= 3:
        _xj_stable += 1
    else:
        _xj_stable = 1
    _xj_last_pos = pos
    # 点寻路链接后须位置先变化（寻路启动）才允许再次用镜：链接
    # 点击未生效时位置不动，直接再次用镜会白耗 A/B 配额
    if _xj_need_move:
        if _xj_walk_from is not None and (
                abs(pos[1] - _xj_walk_from[1]) > 5
                or abs(pos[2] - _xj_walk_from[2]) > 5):
            _xj_need_move = False
            _xj_stable = 0  # 移动开始：重新累计到达稳定
        else:
            return False
    if _xj_stable < XIANXINGJING_ARRIVE_POLLS:
        return False  # 还在寻路
    if _xj_use_count >= XIANXINGJING_USE_MAX:
        if not _xj_warned:
            _xj_warned = True
            log("[五倍] 显形镜：A、B 两处均未触发白龙马对话框，请人工检查", "WARN")
        return False
    # 已到达（位置连续稳定）：点追踪面板「显形镜」文案用镜
    log(f"[五倍] 显形镜：已到达（位置稳定），点击追踪文案用镜"
        f"（第 {_xj_use_count + 1}/{XIANXINGJING_USE_MAX} 处）", "SYS")
    if _click_xianxingjing_use(win, items, img_np):
        _xj_use_count += 1
        _xj_phase = 1
        _xj_grace_start = now
        _xj_stable = 0
        return True
    log("[五倍] 显形镜：追踪面板未找到显形镜文案，下轮询重试", "WARN")
    return False


def _handle_fight_dialog(win, items, img_np):
    """对话开打：跟踪期间出现对话 → 点击绿色选项进入战斗。

    选项文案随场景变化（FIGHT_DIALOG_OPTIONS 逐一匹配，实测五倍
    可能是「证明实力」「消灭它」而非修罗的「看打！」）。点击成功
    即标记本轮战斗由对话开打进入（战后据此决定是否返程）。
    OCR 对标点误读常见，去标点后按子串匹配；限定对话框区域
    （x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    返回是否已处理。
    """
    global _fight_via_kanda
    for option in FIGHT_DIALOG_OPTIONS:
        needle = norm_text(option)
        for txt, rect, score in items:
            if score >= 0.55 and needle in norm_text(txt) \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                # 合并行按比例取选项自身中心（见 dialog_common.green_option_center）
                cx, cy = green_option_center(img_np, rect, len(needle),
                                             len(norm_text(txt)))
                human_click(win, cx, cy)
                log(f"[五倍] 已点击开打选项「{txt}」进入战斗", "SYS")
                _fight_via_kanda = True
                return True
    return False


def _handle_follow_extras(win, items, img_np):
    """跟随期间的附加对话处理：白龙马对话框（点任务按钮）优先，
    其次显形镜 A→B 回退（用镜/换点），最后对话开打选项。"""
    if _handle_xianxingjing(win, items, img_np):
        return True
    if _handle_xianxingjing_track(win, items, img_np):
        return True
    return _handle_fight_dialog(win, items, img_np)


def _follow_to_done(win, first_items=None):
    """寻路 + 对话开打 + 战斗：跟踪任务直到本轮完成。

    复用 xuanshu.follow_task：绿字点击自动寻路、对话开打走附加对话处理
    （点击「看打！」进入战斗）、战斗由战斗监控线程操作且跟踪中自动跳过。
    first_items：领取校验刚确认任务出现的 (items, img_np) 帧，首轮
    跳过截图+OCR 立即点击追踪绿字。
    返回 (是否成功, 描述)。
    """
    ok, info = follow_task(win, TASK_TITLE, prefix="五倍",
                           extra_dialog_handler=_handle_follow_extras,
                           fallback_keywords=TASK_FALLBACK_TEXTS,
                           first_items=first_items)
    return ok, info


def _open_task_box(win):
    """打开背包并确保任务储物格已打开。返回 (是否成功, 描述)。

    用户流程：打开背包 → 检查 box_task_selected（选中态）：存在说明
    任务储物格已在背包中打开，无需点击；否则点击 box_task 打开。
    两模板是同一按钮的两种状态、互相弱匹配，双模板比分数判定：
    各自分数达标且更高者胜（防止未选中按钮被选中态模板误判成已打开，
    以及已打开时误点未选中态反而把储物格关上）。
    每轮询只截一帧，双模板批量检测共用一帧；以 bag_box 为锚点的
    搜索条带限定匹配范围（带区全未命中才全帧兜底）。
    """
    if not bag.is_bag_open(win):
        ok, info = bag.open_bag(win)
        if not ok:
            return False, f"打开背包失败: {info}"
    else:
        log("[五倍] 背包已打开，跳过开包", "INFO")
    # 以 bag_box（面板标志）为锚点计算任务储物格按钮的搜索条带
    # （面板可拖动，内部元素相对偏移恒定；未找到锚点则全帧匹配）
    region = None
    anchor = bag.find_bag_box(win)
    if anchor is not None:
        bx, by = anchor
        region = (bx + TASK_BOX_OFFSET[0] - TASK_BOX_BAND_MARGIN,
                  by + TASK_BOX_OFFSET[1] - TASK_BOX_BAND_MARGIN,
                  bx + TASK_BOX_OFFSET[0] + TASK_BOX_BAND_MARGIN,
                  by + TASK_BOX_OFFSET[1] + TASK_BOX_BAND_MARGIN)
    deadline = time.time() + TASK_BOX_TIMEOUT
    clicked = False
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        sel_score, _ = game_map.best_match_np(frame, BOX_TASK_SELECTED_ICON,
                                              region=region)
        task_score, task_pt = game_map.best_match_np(frame, BOX_TASK_ICON,
                                                     region=region)
        if sel_score < TASK_BOX_THRESHOLD and task_score < TASK_BOX_THRESHOLD \
                and region is not None:
            # 带区全未命中：全帧兜底（限频——仅带区未命中时触发）
            sel_score, _ = game_map.best_match_np(frame, BOX_TASK_SELECTED_ICON)
            task_score, task_pt = game_map.best_match_np(frame, BOX_TASK_ICON)
        if sel_score >= TASK_BOX_THRESHOLD and sel_score > task_score:
            return True, "任务储物格已在背包中打开"
        if task_score >= TASK_BOX_THRESHOLD:
            if not clicked:
                human_click(win, task_pt[0], task_pt[1])
                log("[五倍] 已点击任务储物格按钮，等待打开...", "SYS")
                clicked = True
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        time.sleep(RETURN_POLL_INTERVAL)
    if clicked:
        return False, "已点击任务储物格按钮，但未检测到选中态（打开失败）"
    return False, "背包中未找到任务储物格按钮"


def _find_return_talisman(win):
    """在已打开的任务储物格中查找五倍回城符，返回 (pt 或 None, 描述)。

    只识别不点击（右键使用由 _use_return_talisman 执行；验证流程
    复用本函数仅移动鼠标悬停）。模板尚未提供时直接失败并说明
    （不进入等待循环）。
    """
    try:
        has_tpl = Path(R.img(WUBEI_RETURN_ICON)).exists()
    except Exception:
        # R.img 对不存在的模板会打印 ⚠️ 警告，GBK 控制台下 print 抛
        # UnicodeEncodeError；吞掉按模板缺失处理
        has_tpl = False
    if not has_tpl:
        return None, f"五倍回城符模板未提供（待补充 {WUBEI_RETURN_ICON}）"
    deadline = time.time() + RETURN_TALISMAN_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, WUBEI_RETURN_ICON)
        if pt is not None:
            return pt, "已在任务储物格中找到五倍回城符"
        time.sleep(RETURN_POLL_INTERVAL)
    return None, "任务储物格中未找到五倍回城符"


def _use_return_talisman(win):
    """在已打开的任务储物格中识别五倍回城符并右键使用，返回宝象国。

    返回 (是否成功, 描述)。未找到时下一轮领取流程会走世界地图
    寻路回宝象国兜底。
    """
    pt, info = _find_return_talisman(win)
    if pt is None:
        return False, info
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"[五倍] 已右键使用五倍回城符 ({pt[0]}, {pt[1]})，返回宝象国", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, "已右键使用五倍回城符"


def _close_return_panels(win):
    """返程收尾：关闭世界地图、小地图、背包（尽力而为，失败仅记日志）。

    先截一帧批量检测三项是否全未打开（常见场景：回城符使用后界面已
    自行关闭）：全未打开直接返回，1 次截图替代 4~6 次带截图的逐项检测
    （实测逐项 ~110ms/两项 + 关包检测 ~140ms）；任一打开才走既有关闭流程。
    """
    img = win.capture(force_activate=False)
    if img is not None:
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        found = game_map.match_frame_multi(
            frame, (game_map.WORLD_MAP_OPEN_ICON,
                    game_map.MINI_MAP_OPEN_ICON,
                    bag.BAG_BOX_ICON))
        if not any(found.values()):
            log("[五倍] 世界地图/小地图/背包均未打开，无需关闭", "INFO")
            return
    game_map.close_maps(win)
    ok, info = bag.close_bag(win)
    if ok:
        log(f"[五倍] {info}", "SYS")
    else:
        log(f"[五倍] 背包关闭失败: {info}", "WARN")


def _return_to_npc(win):
    """返程：任务完成后用五倍回城符回宝象国，准备下一轮领取。返回 (是否成功, 描述)。

    用户流程（与修罗一致，仅回城符模板不同）：
    1. 战斗结束后打开背包：检查 box_task_selected（选中态），存在说明
       任务储物格已在背包中打开；否则点击 box_task 打开
    2. 识别五倍回城符 → 右键使用，返回到宝象国
    3. 关闭世界地图、小地图、背包

    待寻路跟踪流程接入后由主循环调用（修罗在「看打！」进入的战斗
    结束后返程）。
    """
    # 前置：确保战斗已结束（意外遭遇战会遮挡背包按钮）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 1. 打开背包并确保任务储物格已打开。战斗结束后的结算过渡期游戏
    #    忽略输入（实测战斗结束 1s 后点背包两次尝试均失败，而验证流程
    #    静止场景正常）——重试带间隔直到打开或超时，骑过结算动画
    ok = False
    info = ""
    for attempt in range(3):
        ok, info = _open_task_box(win)
        if ok:
            break
        if attempt < 2:
            log(f"[五倍] 背包打开失败（第 {attempt + 1} 次）：{info}，"
                f"等待结算过渡后重试", "WARN")
            time.sleep(2.0)
    if not ok:
        return False, info
    # 2. 识别五倍回城符并右键使用（返回宝象国）
    ok, info = _use_return_talisman(win)
    if not ok:
        # 回城符未使用：也关闭背包与地图保持界面干净；下一轮领取
        # 会走世界地图寻路回宝象国兜底
        _close_return_panels(win)
        return False, info
    # 3. 关闭世界地图、小地图、背包（清理界面）
    _close_return_panels(win)
    # 传送后位置识别（短时等待加载完成，仅供日志与下一轮领取判断；
    # 5s 上限为实测校准值——实测加载超 3s 时 3s 时限识别失败，
    # 且失败后下一轮领取会误走世界地图寻路兜底）
    deadline = time.time() + 5
    pos = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if pos is not None:
            break
        time.sleep(1.0)
    if pos is not None:
        log(f"[五倍] 返程后位置: {pos[0]} {pos[1]},{pos[2]}", "SYS")
    else:
        log("[五倍] 返程后位置识别失败（不影响本轮返程）", "INFO")
    return True, "已返回宝象国"


def verify_return_flow(win):
    """验证五倍回程流程（高级设置-验证按钮，主进程调用）。

    与实际返程 _return_to_npc 完全一致，唯一区别是不右键使用回城符：
    仅将鼠标移动到回城符上（不点击，无延时——时序与真实返程一致），
    然后关闭世界地图/小地图/背包。返回 (是否成功, 描述)。
    """
    # 前置：确保战斗已结束（与实际返程一致）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 1. 打开背包并确保任务储物格已打开（与实际返程一致）
    ok, info = _open_task_box(win)
    if not ok:
        return False, info
    # 2. 识别五倍回城符：仅移动鼠标定位，不点击
    pt, info = _find_return_talisman(win)
    if pt is None:
        _close_return_panels(win)
        return False, info
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    log(f"[五倍][验证] 已移动鼠标到五倍回城符 ({pt[0]}, {pt[1]})，"
        f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
    # 3. 关闭世界地图、小地图、背包
    _close_return_panels(win)
    return True, f"验证通过：回城符位于 ({pt[0]}, {pt[1]})"


def _check_team_ready(win):
    """领取前队伍检查（五倍设置仅在队长时生效，一次面板开关）。

    队长时按通用设置-五倍设置的最少人数检查。
    检查结果顺带更新调试悬浮层「队伍」行（身份 + 成员信息）。
    返回 (状态, 描述)；状态见 team.check_team_min：
    ready 可领取 / wait 人数不足 / not_leader 非队长或队长无法判定。
    """
    min_members = load_wubei_min()
    status, info, members = team.check_team_min(win, min_members=min_members)
    team.update_overlay_team(status, info, members)
    return status, info


def _keep_alive(info):
    """非队长：核心流程不执行，保持任务运行（战斗线程自动操作）。

    用户手动停止时任务进程被回收，无需自行退出。
    """
    log(f"[五倍] {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
    while True:
        time.sleep(1)


def verify_claim_flow(win):
    """验证五倍领取流程（高级设置-验证按钮，主进程调用）。

    与领取流程一致，但在「点击绿色选项完成领取」前停止：走位 → 领取
    入口识别 → 点击入口打开对话框（领取发生在点选项之后，开对话框
    不改变任务状态）→ 识别绿色选项（完整/尾部/兜底）→ 仅移动鼠标定位
    （不点击）→ 点拒绝选项关闭对话框。
    返回 (是否成功, 描述)。
    """
    # 前置：确保战斗已结束（与实际领取一致）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 任务已在追踪时游戏不给重领（领取按钮不出现，修罗实测 20s 空等）→ 明确失败
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        return False, "任务追踪中已有五倍任务，无法验证领取（请先完成当前任务）"
    # 1. 走位到领取点（与实际领取一致）
    ok, info = _walk_to_claim_spot(win)
    if not ok:
        return False, info
    # 2. 领取入口：鼠标定位 → 点击打开对话框；点击后对话框未出现则
    #    小地图重定位后重试（与领取流程共用同一兜底助手）
    ok, info, src2, pt2, _frame = _click_entry_ensuring_dialog(
        win, log_tag="[五倍][验证]", hover_before_click=True)
    if not ok:
        return False, info
    # 3. 识别绿色选项：仅移动鼠标悬停，不点击（点击即领取）
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        found = _find_claim_option(items, img_np)
        if found is not None:
            cx, cy, txt, _ = found
            human_move(win.rect.left + cx, win.rect.top + cy)
            log(f"[五倍][验证] 已移动鼠标到绿色选项「{txt}」({cx}, {cy})，"
                f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
            # 4. 关闭对话框（不领取）：该对话框点屏幕中间不关闭，点拒绝
            #    选项「事态严重，我等退散」关闭（实测；拒绝未识别时兜底
            #    点屏幕中间）
            _close_dialog_without_claim(win, items, img_np)
            return True, f"验证通过：{src2} ({pt2[0]}, {pt2[1]})，选项「{txt}」({cx}, {cy})"
        time.sleep(0.3)
    # 对话框未出现或选项未识别：点屏幕中间清理后失败
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    return False, "对话框选项未识别（未领取，已尝试关闭对话框）"


def run_loop(win, cycle_interval=1.0):
    """五倍主循环：领任务 → 寻路 → 对话开打 → 战斗 → 返程 → 循环领取。

    领取逻辑（用户确认，参考修罗）：
    1. 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
    2. 队长先走到领取点附近（队伍是否满都先走位）
    3. 首次领取前到达后持续监控队伍人数：满足最少人数才领取，
       不满足 10 秒重查（人留在领取点，只开队伍面板检查）
    4. 队伍检查仅首次领取前执行一次（_team_checked）：首次领取成功后
       后续轮次直接走位领取，不再开面板（每轮省 5~7s）；领取失败时
       补查一次队伍状态，队长被转移则转非队长保持模式
    5. 任务追踪中已有五倍任务（续跑）→ 跳过领取，直接跟踪

    结束条件：完成任务次数达到设置的次数上限（通用设置-任务次数）。
    返回结束原因描述。
    """
    global _fight_via_kanda, _team_checked, _task_gone_last_round
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[五倍] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    min_members = load_wubei_min()
    log(f"[五倍] 领取点宝象国 {CLAIM_SPOT[0]},{CLAIM_SPOT[1]}，最少人数 {min_members}", "SYS")
    # 检查队伍开关（通用设置-五倍设置，默认关闭，对齐修罗）：关闭时
    # 领取前跳过全部队伍检查（非队长判定/人数监控），直接走位领取；
    # 寻路/领取成功即记录队长身份（抢盒子的队长规则用）
    check_team = load_wubei_check_team()
    log(f"[五倍] 检查队伍: {'开（领取前检查队伍）' if check_team else '关（跳过队伍检查，直接领取）'}", "SYS")

    done = 0
    round_no = 0
    while True:
        try:
            round_no += 1
            log(f"[五倍] ==== 第 {round_no} 轮 ====", "SYS")
            if is_in_battle(win):
                time.sleep(cycle_interval)
                continue
            # 1. 领任务：任务追踪中已有五倍任务（续跑）→ 跳过领取，直接跟踪。
            #    上一轮已确认任务完成（follow_task 以任务消失为完成判据）时，
            #    本轮开始必然无任务——has_task（区域 OCR ~0.9s）只在可能
            #    续跑时检查（进程首轮或上轮跟踪失败未确认完成）
            claim_items = None  # 领取校验帧（续跑路径无，跟踪首轮自行截图+OCR）
            if not _task_gone_last_round and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
                log("[五倍] 任务追踪中已有五倍任务，直接跟踪", "SYS")
            else:
                if _task_gone_last_round:
                    _task_gone_last_round = False
                    log("[五倍] 上一轮已确认任务完成，跳过任务检查，直接领取", "INFO")
                # 首次领取前队伍检查（检查队伍开关 + _team_checked 双门控；
                # 关闭开关时完全跳过，对齐修罗）：已在领取点附近时跳过
                # 非队长判定（返程落点就在领取点，监控循环马上会检查——
                # 先查再走位会让面板连续开合两次，一次开合实测 5~7s）；
                # 不在领取点则先判定身份，非队长不白走寻路
                if check_team and not _team_checked:
                    at_spot = (game_map.find_template(win, LINGQU_ICON) is not None
                               or _near_claim_spot(game_map.read_map_position(win),
                                                   CLAIM_NEAR_TOLERANCE))
                    if not at_spot:
                        status, info = _check_team_ready(win)
                        if status == "not_leader":
                            _keep_alive(info)
                # 队长：队伍是否满都先走到领取点附近
                ok, info = _walk_to_claim_spot(win)
                if not ok:
                    log(f"[五倍] {info}", "WARN")
                    time.sleep(cycle_interval)
                    continue
                if not check_team:
                    # 关闭队伍检查：不开面板判定身份，任务自行寻路即视为
                    # 队长（抢盒子的队长规则依赖此记录，见 battle._handle_hezi）
                    team.record_leader_status(True)
                if check_team and not _team_checked:
                    # 首次领取：到达后持续监控队伍人数（人留在领取点）：
                    # 满足最少人数才领取；队长被转移则转非队长保持模式；
                    # 识别失败是 OCR 噪声，短间隔重查（不等满 10 秒）
                    while True:
                        status, info = _check_team_ready(win)
                        if status == "not_leader":
                            _keep_alive(info)
                        if status == "ready":
                            log(f"[五倍] {info}", "SYS")
                            break
                        interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                                    else TEAM_CHECK_RETRY_INTERVAL)
                        log(f"[五倍] {info}，暂不领取（持续监控队伍人数）", "WARN")
                        time.sleep(interval)
                # 领取任务（校验帧随返回值带出，跟踪首轮复用免重复 OCR）
                ok, info, claim_items = _claim_task(win)
                if not ok:
                    if check_team and _team_checked:
                        # 已通过队伍检查仍领取失败（如队长被转移、入口未出现）：
                        # 补一次队伍检查恢复状态感知，非队长转保持模式
                        status, info2 = _check_team_ready(win)
                        if status == "not_leader":
                            _keep_alive(info2)
                    log(f"[五倍] {info}", "WARN")
                    time.sleep(cycle_interval)
                    continue
                log(f"[五倍] {info}", "SYS")
                team.record_leader_status(True)  # 领取成功必为队长（关闭检查队伍时抢盒子队长规则靠此记录）
                _team_checked = True  # 首次领取成功：后续轮次跳过队伍检查
                _reset_xianxingjing_state()  # 新任务：显形镜状态机复位
                _fight_via_kanda = False  # 新一轮开始：重置「看打！」进入战斗标记
            # 2~4. 寻路 / 对话开打 / 战斗：由任务跟踪统一驱动
            #     （寻路点击绿字自动进行，对话开打走附加对话处理，
            #      战斗由战斗监控线程自动操作、跟踪中自动跳过）；
            #     领取校验帧传入后首轮跳过截图+OCR 立即点击绿字
            ok, info = _follow_to_done(win, first_items=claim_items)
            if not ok:
                log(f"[五倍] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            # 5. 返程：战斗结束且任务追踪中确实没有任务才返程（用户确认：
            #    黄袍怪任务要杀多次，两场战斗之间追踪可能瞬时为空，follow
            #    的消失确认会误判完成——返程前复查一次，任务仍在则不返程
            #    并恢复跟踪）
            if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
                _task_gone_last_round = False  # 恢复跟踪：下一轮 has_task 续跑
                log("[五倍] 返程前复查任务仍在追踪（多杀任务），跳过返程继续跟踪", "WARN")
                time.sleep(cycle_interval)
                continue
            _task_gone_last_round = True  # 任务消失已确认：下一轮跳过 has_task 检查
            # 仅「看打！」/对话开打进入的战斗结束后才返程（背包任务
            # 储物格右键五倍回城符回宝象国），其他方式进入的战斗不返程
            if _fight_via_kanda:
                ok, info = _return_to_npc(win)
                if not ok:
                    log(f"[五倍] {info}", "WARN")
                    time.sleep(cycle_interval)
                    continue
            done += 1
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            log(f"[五倍] 本轮五倍任务完成（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次五倍任务，达到次数上限 {max_rounds}"
            # 成功路径不加轮次间隔：返程流程结尾已有 5s 位置识别等待，
            # 游戏状态已稳定，下一轮直接读位置领取即可（间隔仅保留在
            # 失败重试路径，防快速失败模式紧循环空转）
        except Exception as e:
            log(f"[五倍] 主循环异常: {e}", "WARN")
            time.sleep(TEAM_CHECK_RETRY_INTERVAL)
