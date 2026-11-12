"""进度条对准小游戏（破除封印 / 钓鱼）闭环控制。

同一款小游戏窗口被多个任务复用：客栈「破除封印」（灵兔封印）、名望
「蛟魔探秘钓鱼」与玄枢「钓鱼」共用完全相同的窗口布局、开始按钮、目标
高亮带与进度条区域，仅标题模板与推进方式不同——破除封印/蛟魔探秘为
「点击」（冲刺连点推进，mash=True），钓鱼为「长按」（按住持续推进，
长按按钮模板 xuanshu_diaoyu_changan.png）。
长按模式细分两种：钓鱼为**锁定持握**（hold=True，进带后游戏锁定，
须按住到窗口结束）；开采石头为**定时释放**（hold=True +
hold_catch=True，长按推过带上缘→松手回落→回 C 后点按维持）。
实现与标定同源（2026-11-09 用户完整描述 + 7.png 实测），调用方只传
标题模板、按钮模板、推进方式与日志前缀/名称。

流程：
弹窗出现 → 识别目标高亮区间 [L,R,C]（target 模板定位，目标带 ≈1 刻度宽）
→ 点「开始」（按钮同位置变为流程按钮）→ 每轮询一帧测量填充右边缘 P
（HSV 金掩码 + 从左连通段）→ 推进（点击模式按 C 三档连点到目标带中心
C 附近才停手——破除封印松手后填充会回落，停在左缘有掉出带下的风险 /
长按模式按住不放 / 冲刺连点模式按在线估算的每击推进量连点并持续维持）。
点击模式 C≤P≤R 停手保持、P>R 过头永久停手；长按模式进入区间后继续按住
（游戏锁定游标，松手会回退脱钩），仅 P>R 时松开让其回落；冲刺模式不按
倒计时停手（填充条自动回退，须维持到窗口结束），P 低于维持点即补点。
按钮变「保持」或倒计时 ≤3s（冲刺模式除外）完全停手等结算 → 标题消失
即任务结束。
全部测量在 POCHU_REGION/进度条带内，坐标映射 1~10 刻度（x 435~992，
7.png 实测刻度线间距 55.7px 吻合）。
"""
import re
import time

import cv2
import numpy as np

from .app_utils import log, human_click, rapid_click_burst, MouseHold
from . import map as game_map
from .battle import is_in_battle_frame

# ---- 窗口按钮与模板（区域范围与客栈破除封印完全一致）----
POCHU_START_ICON = "kezhan_pochufengyin_kaishi.png"  # 「开始」按钮（点击后同位置变为流程按钮）
POCHU_CLICK_ICON = "kezhan_pochufengyin_dianji.png"  # 点击模式「点击」按钮（过程中会变外观，坐标不变仍须点）
POCHU_TARGET_ICON = "kezhan_pochufengyin_target.png"  # 目标高亮区间标记（模板定位 C）
POCHU_REGION = (314, 458, 1058, 645)   # 弹窗模板搜索条带（用户提供，覆盖小游戏窗口区）
POCHU_THRESHOLD = 0.8                  # 模板匹配阈值（标题 0.999/点击 0.998/目标 0.985 实测）
# ---- 进度条测量（7.png 实测标定）----
POCHU_SCALE_X0 = 435                   # 1 刻度窗口 x（进度条左端）
POCHU_SCALE_X1 = 992                   # 10 刻度窗口 x（进度条右端）
POCHU_SCALE_W = POCHU_SCALE_X1 - POCHU_SCALE_X0  # 一满刻度宽（557px，刻度线间距实测 55.7）
POCHU_BAR_REGION = (430, 528, 1000, 562)  # 进度条搜索带（目标带/填充测量用）
POCHU_BAR_Y = (536, 554)               # 进度条本体 y 带（填充行；刻度线在其上方 511~527）
POCHU_FILL_MIN_COL = 2                 # 列内填充像素下限（18px 高带内实测 ~10px/列）
POCHU_FILL_GAP = 8                     # 连续缺填充列数即视为填充段结束
POCHU_ZONE_HALF = 29                   # 目标带半宽（模板 58px≈1 刻度；实测括号 879~937 吻合）
# 填充金掩码（HSV，实测）：填充体 H21 S133 V176、前缘亮标 H26 S96 V202；
# 目标带括号为白金色 H36 S61 V215（H>32 排除）、暗内部 V≤125（V 门排除）
POCHU_FILL_H = (15, 32)
POCHU_FILL_S = 95
POCHU_FILL_V = 120
# ---- 倒计时与按钮文字（OCR，差分门 + 限频）----
POCHU_CNT_REGION = (600, 545, 770, 598)   # 「施法倒计时：N秒」区域（7.png 实测）
POCHU_BTN_REGION = (915, 565, 1010, 635)  # 按钮文字区域（点击/保持/长按）
POCHU_CNT_STOP = 3                     # 倒计时 ≤3 秒完全停手
POCHU_OCR_REFRESH = 2.0                # 倒计时/按钮 OCR 强制重检间隔（差分门兜底）
POCHU_STALL_CLICKS = 4                 # 点击模式：P 连续不动点击数 → 告警按钮坐标
POCHU_HOLD_STALL = 2.0                 # 长按模式：按住 P 不动达该时长（秒）→ 告警一次
POCHU_HOLD_OVER_MARGIN = 1.0           # 长按模式「冲过右缘」判定余量（刻度）：
                                       # 游标锁定在区间右缘时测量噪声可轻微过 R，
                                       # 余量外（R+1）才松开回落，防误松手
POCHU_STATE_LOG_INTERVAL = 1.0         # 状态日志限频（秒）
# 点击节奏三档（用户 spec）：C<4 近目标易过头（400~500ms，每点等位置更新）；
# 4≤C≤7 中距离（250~300ms）；C>7 远距离时间紧（150~200ms 快速推进）
POCHU_TIER_NEAR = 0.45
POCHU_TIER_MID = 0.275
POCHU_TIER_FAR = 0.175
POCHU_TICK_PACE = 0.15                 # 点击模式测量节拍（每轮询一帧）
POCHU_HOLD_TICK_PACE = 0.08            # 长按模式测量节拍（填充推进快，需更密
                                       # 采样以在进入区间时及时松手防过头）
# ---- 冲刺连点模式（mash=True，蛟魔探秘钓鱼 2026-11-10 真机实测）----
# 同款「点击」窗口的通用打法；客栈破除封印 2026-11-12 实机日志确认
# 同病：13 击/7s 只推到 P≈1.4，10s 倒计时内三档节奏永远到不了目标带。
# 该小游戏两个真机特性（用户 2026-11-10 反馈 + 日志实测）：
# 1) 单击推进小（慢点 10 击 P≈1.06），三档节奏 ~3 击/秒 到不了目标带；
# 2) 填充条会**自动回退**（停手即从带内掉回 L 以下，倒计时 3s 停手
#    必失败），且连点后填充条有追帧延迟（14 连点瞬间仍测到 0.05、
#    约 1s 后才升到 3.23）——按滞后值补点会过量。
# 冲刺模式用 rapid_click_burst 按「每击推进量」在线估算冲进带内，
# 每次冲刺后等填充条停稳（不再上升）才决策，停稳后维持在 L 附近：
# P 低于维持点即补点，一直点着维持到窗口标题消失（不按倒计时停手）。
POCHU_MASH_RATIO0 = 0.25          # 初始每击推进量估计（刻度；偏大保守防过头）
POCHU_MASH_RATIO_SAFETY = 1.15    # 估算保守系数：按每击多推进 15% 计算，宁可少点
POCHU_MASH_RATIO_MIN = 0.01       # 每击推进量有效区间（异常观测丢弃）
POCHU_MASH_RATIO_MAX = 1.0
POCHU_MASH_AIM = 0.45             # 冲刺目标点 = L + 0.45（目标带内、右侧留余量防过头）
POCHU_MASH_DEADBAND = 0.08        # 冲刺死区：P ≥ 目标点-死区 不冲刺（防微抖连点）
POCHU_MASH_RISE_EPS = 0.04        # 冲刺后每轮上升超过该值 = 追帧未停稳，不决策
POCHU_MASH_STABLE_POLLS = 2       # 连续「不再上升」轮数确认停稳（防动画中间帧误判）
# 维持泵（2026-11-10 第三轮实机标定：停在 L 下方 ~0.2 格进不了带）。
# 实测填充条位置与「持续点击率」正相关（~4.5 击/秒稳定在 3.8 格，
# 3 击/秒只到 1~2 格），停手约 1s 即快速回退（3.99→2.84）。维持段改
# **点击率伺服**：保持一个持续点击率，按位置误差每 POCHU_MASH_RATE_EVAL
# 秒调整速率，逼近右缘完全停泵防追帧滞后冲过。
POCHU_MASH_ZONE_OFFSET = 0.32     # 目标带修正（用户 2026-11-10 试调：识别值偏小 +0.32；
                                  # 仅 mash 生效——蛟魔探秘/破除封印同一填充图形
                                  # 同一测量，长按标定不受影响）
POCHU_MASH_HOLD_ENTER = 0.60      # 进入维持泵：P ≥ L-0.60 即切换（实测停在 L-0.2，门槛须更宽）
POCHU_MASH_TOP_MARGIN = 0.30      # 维持上限：P ≥ R-0.30 停泵（防追帧滞后冲过右缘）
POCHU_MASH_RATE0 = 5.5            # 维持泵初始点击率（击/秒；实测 4.5/s 稳定在 L-0.2）
POCHU_MASH_RATE_MIN = 2.0
POCHU_MASH_RATE_MAX = 14.0
POCHU_MASH_RATE_EVAL = 0.8        # 速率自适应评估间隔（秒）
POCHU_MASH_RATE_GAIN = 1.2        # 每 1 刻度误差调整的击/秒（实测斜率 ~0.84 格/击/秒）
POCHU_MASH_ERR_DEADBAND = 0.12    # 位置误差死区（刻度）
POCHU_MASH_CNT_GONE_POLLS = 3     # 倒计时连续消失轮数 → 已结算，停手
POCHU_MASH_CREDIT_MAX = 3.0       # 补点积分上限（防暂停后爆发）
POCHU_MASH_HOLD_MAX = 6           # 单轮点击上限
POCHU_MASH_MAX_BURST = 90         # 单次冲刺点击上限（限制测量间隔）
POCHU_MASH_MIN_CLICKS = 5         # 判定「点击未推进」所需最少点击数
POCHU_MASH_PRESS = 0.02           # 冲刺点击按压时长（rapid_click 默认）
POCHU_MASH_PRESS_MAX = 0.05       # 连续未推进时加压上限（防漏注册）
POCHU_ARM_TIMEOUT = 120                # 入口选项点击后检测窗口的最长时限（秒）
POCHU_CHECK_INTERVAL = 1.0             # extras 链窗口检测限频（秒，条带匹配 ~10ms）
POCHU_END_WINDOW = 2.0                 # 标题连续消失确认窗口（秒；结束确认防抖，不得缩短）
POCHU_TIMEOUT = 180                    # 小游戏总超时（秒）
# ---- 长按运输 + 点按维持模式（hold_catch；开采石头 2026-11-10）----
# 该小游戏与钓鱼不同：长按推进极快（真机实测 ~2.4 刻度/秒），目标带
# 仅 ~1 刻度宽且进带不锁定；松手后填充整体回落（日志：P 4.17→7.36
# 释放后回退到带下判失败）。用户 2026-11-10 技巧：**先长按把填充推过
# 目标带上缘，松手让它回落，回到 C（带中心）后改为「点按」维持**——
# 点按复用冲刺模式的维持泵（点击率伺服：填充位置与点击率正相关、停手
# 即回退），把填充保持在 [L,R] 内直到窗口结束；掉出带下则重新长按
# 运输（限次）。不清空既有长按（钓鱼锁定）与点击/冲刺模式语义。
POCHU_CATCH_ABOVE = 0.20        # 长按越过 R 该值后松手（回落起点保证在带上）
POCHU_CATCH_REPRESS_MARGIN = 0.40  # 维持泵仍落出 L 下方该值 → 重新长按运输
POCHU_CATCH_MIN_GAP = 0.25      # 松手后重新按住的最小间隔（秒）
POCHU_CATCH_MAX_PRESS = 4       # 最多长按运输次数


def pochu_tick(x):
    """窗口 x → 1~10 刻度（0=进度条左端 435，10=右端 992）。"""
    return (x - POCHU_SCALE_X0) / POCHU_SCALE_W * 10.0


def pochu_fill_edge(bgr):
    """进度条金色填充右边缘窗口 x（HSV 金掩码 + 从左起连通段）。

    填充体/前缘实测 HSV 见 POCHU_FILL_H/S/V；目标带括号（H>32）与暗
    内部（V≤125）被掩码排除，连通段从条左端起算不受其干扰。无填充
    （未开始/掩码漏检）返回 None（调用方按刻度 0 处理）。
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    l, _t, r, _b = POCHU_BAR_REGION
    fill = (h_ch >= POCHU_FILL_H[0]) & (h_ch <= POCHU_FILL_H[1]) \
        & (s_ch > POCHU_FILL_S) & (v_ch > POCHU_FILL_V)
    colcnt = fill[POCHU_BAR_Y[0]:POCHU_BAR_Y[1], l:r].sum(axis=0)
    have = colcnt >= POCHU_FILL_MIN_COL
    idx = np.flatnonzero(have)
    if idx.size == 0:
        return None
    start = int(idx[0])
    pend = start
    gap = 0
    for i in range(start, have.size):
        if have[i]:
            gap = 0
            pend = i
        else:
            gap += 1
            if gap >= POCHU_FILL_GAP:
                break
    return l + pend


def _pochu_zone(bgr):
    """目标高亮区间：target 模板定位中心 C，L/R=C±POCHU_ZONE_HALF
    （目标带 ≈1 刻度宽，7.png 实测括号 879~937 与模板 ±29 吻合）。
    返回 (L, R, C) 窗口 x 或 (None, None, None)。"""
    c = game_map.match_frame_np(bgr, POCHU_TARGET_ICON,
                                threshold=POCHU_THRESHOLD,
                                region=POCHU_BAR_REGION, fallback=False)
    if c is None:
        return None, None, None
    return c[0] - POCHU_ZONE_HALF, c[0] + POCHU_ZONE_HALF, c[0]


_POCHU_CNT_RE = re.compile(r"(\d{1,2})\s*秒")


def _pochu_ocr_gated(img_pil, region, cache, force=False):
    """区域 OCR 差分门：区域像素无变化（且非强制）直接返回缓存文本
    （性能规则 5：OCR 前置像素差分预过滤）；变化才 OCR。识别失败
    返回 None（未知状态，调用方不动作）。"""
    crop = np.asarray(img_pil.crop(region))
    if not force and cache["crop"] is not None \
            and np.array_equal(cache["crop"], crop):
        return cache["text"]
    cache["crop"] = crop.copy()
    items, _ = game_map.ocr_regions(img_pil, (region,))
    txt = "".join(t for t, _r, s in items if s >= 0.55)
    cache["text"] = txt or None
    return cache["text"]


def _pochu_countdown(img_pil, cache, force=False):
    """倒计时秒数（差分门 OCR「施法倒计时：N秒」）；解析失败返回 None。"""
    txt = _pochu_ocr_gated(img_pil, POCHU_CNT_REGION, cache, force=force)
    if not txt:
        return None
    m = _POCHU_CNT_RE.search(txt)
    return int(m.group(1)) if m else None


def play_pochufengyin(win, title_icon, prefix="客栈", label="破除封印",
                      hold=False, action_icon=None, mash=False,
                      hold_catch=False):
    """进度条对准小游戏闭环控制（阻塞执行到标题消失）。返回 (是否完成, 描述)。

    流程（用户 2026-11-09 完整描述 + 7.png 实测标定）：
    1. 弹窗出现（记录时间）→ 识别目标高亮区间 [L,R,C]
    2. 点「开始」→ 按钮同位置变为流程按钮（点击模式「点击」/长按模式
       「长按」，过程中会变其他外观，坐标不变仍须操作；变「保持」则
       完全停手）
    3. 闭环（每 POCHU_TICK_PACE 一帧一判定，同帧复用性能规则 1）：
       测量填充右边缘 P（HSV 金掩码+连通段）与目标带 [L,R,C]（模板）
       → 推进：点击模式按 C 三档节奏连点到 C 附近停手（松手后填充会
       回落，只推到 L 有掉出带下的风险，用户 2026-11-11）、长按模式
       按住不放。点击模式：C≤P≤R 停手保持、P>R 连续 2 轮确认过头后
       永久停手。
       长按模式：进入区间后继续按住（游戏锁定游标，松手会回退脱钩），
       仅 P>R 连续 2 轮时松开让其回落、回到 L 以下自动再按住。
       按钮变「保持」或倒计时 ≤3s 完全停手等结算（长按期间跳过 OCR，
       以标题消失为准）
    4. 标题连续消失 POCHU_END_WINDOW 秒即任务结束（输出最终 P 与是否命中）
    5. 战斗打断立即退出；异常：目标带检测失败沿用缓存区间继续决策、
       长按模式区间左侧按住无进度 POCHU_HOLD_STALL 秒告警并释放重按
       （down 消息漏消费自愈；区间内不动视为游戏锁定，不释放）

    title_icon：窗口标题模板（各任务不同，如 kezhan_pochufengyin.png /
    xuanshu_diaoyu.png）；prefix/label：日志前缀与小游戏名（同款窗口
    不同任务日志可区分）。hold=True 为长按模式（钓鱼：开始后按钮为
    action_icon=xuanshu_diaoyu_changan.png，按住才推进；游标进入目标
    区间后游戏会锁定，须保持按住到窗口结束，松手会回退脱钩）；
    hold=False 为点击模式。mash=True 为冲刺连点模式（仅 hold=False
    生效；蛟魔探秘钓鱼与客栈破除封印——同一「点击」窗口）：该游戏单次
    点击推进小、三档拟人节奏到不了目标带（真机实测 7s 内只到 P≈1.4~2），
    且填充条有追帧延迟、停手后会**自动回退**。改为 rapid_click_burst
    按「每击推进量」在线估算连点冲进带内（目标带按
    POCHU_MASH_ZONE_OFFSET 修正，
    2026-11-10 用户试调识别值偏小 +0.32），每次冲刺后等填充条停稳
    再决策（防按滞后值过量）；进带后切换「维持泵」点击率伺服——保持一个持续点击率
    （填充条位置与点击率正相关，停手约 1s 即快速回退），按位置误差
    每 POCHU_MASH_RATE_EVAL 秒调节速率，逼近右缘完全停泵；不按
    倒计时停手、维持到窗口标题消失。
    hold_catch=True 为「长按运输 + 点按维持」模式（开采石头，须配合
    hold=True；2026-11-10 用户技巧）：该游戏长按推进 ~2.4 刻度/秒、
    目标带仅 ~1 刻度宽且进带不锁定，钓鱼式「按住到窗口结束」必然冲过
    目标带、松手回退判失败。改为：长按把填充推过带上缘（P≥R+0.2）→
    松手让填充整体回落 → 回到 C（带中心）即切换维持泵，用点按把填充
    保持在 [L,R] 内直到窗口结束；掉出带下重新长按运输（限次）。见
    POCHU_CATCH_* 常量。
    """
    def _pace(tick_start):
        """补齐测量节拍（截图/匹配/点击耗时计入间隔，不累积漂移）。"""
        pace = POCHU_HOLD_TICK_PACE if hold else POCHU_TICK_PACE
        rem = pace - (time.time() - tick_start)
        if rem > 0:
            time.sleep(rem)

    mash = bool(mash) and not hold   # 冲刺连点仅点击模式可用
    hold_catch = bool(hold_catch) and hold  # 长按运输+点按维持仅长按模式可用
    act_icon = action_icon or POCHU_CLICK_ICON
    act_name = "长按" if hold else "点击"
    deadline = time.time() + POCHU_TIMEOUT
    started = False
    btn_pt = None                 # 「开始/流程按钮」位置（同位置，命中刷新、未命中缓存续按）
    last_seen = time.time()       # 标题最近命中时间（消失确认窗口起点）
    last_click = 0.0
    last_p = None                 # 点击模式最近一次点击前测量的 P（停顿告警用）
    last_meas_p = None            # 每轮最新测量 P（结束描述/命中判定用）
    stall_clicks = 0
    over_cnt = 0                  # P>R 连续轮数（过头确认防抖）
    terminal = None               # 完全停手原因（按钮保持/倒计时/过头）
    cnt_cache = {"crop": None, "text": None}
    btn_cache = {"crop": None, "text": None}
    last_ocr = 0.0
    last_log = 0.0
    last_zone_warn = 0.0          # 目标带暂未识别（沿用缓存区间）告警限频
    last_cnt = None               # 最近一次读到的倒计时（结束描述用）
    clicks = 0                    # 推进动作次数（点击次数/长按按下次数）
    zone = None                   # 最近一次 (L, R, C) 刻度（缓存，遮挡时沿用）
    holder = None                 # 长按模式当前按住的鼠标（None=未按住）
    hold_p0 = None                # 本次按下时的 P（长按不动告警）
    hold_t0 = 0.0
    hold_stall_logged = False
    mash_ratio = None             # 冲刺模式：在线估算每击推进量（刻度/击）
    mash_press = POCHU_MASH_PRESS  # 冲刺点击按压时长（漏注册时加压）
    mash_mark = None              # 冲刺模式：(clicks, p) 稳定锚点
    mash_stall = 0                # 连续「点击未推进」冲刺次数
    mash_waiting = False          # 冲刺后等待填充条追帧停稳（停稳前不决策）
    mash_prev_p = 0.0             # 上轮 P（判定填充条是否仍在上升）
    mash_stable_cnt = 0           # 连续「不再上升」轮数（停稳确认）
    cnt_none_polls = 0            # 倒计时消失连续轮数（结算画面判定，mash 停手用）
    mash_hold = False             # 已接近目标带 → 维持泵模式（禁止长空窗）
    mash_rate = POCHU_MASH_RATE0  # 维持泵点击率（击/秒，伺服调节）
    mash_credit = 0.0             # 点击率积分器（不足 1 击的余量）
    mash_rate_t = 0.0             # 上次速率自适应时间
    mash_prev_t = 0.0             # 上轮时间（积分器用时基）
    press_cnt = 0                 # 长按运输模式：按住次数（限次防失控）
    last_release = 0.0            # 长按运输模式：上次松手时间（重点间隔）
    catch_pump = False            # 长按运输模式：已切点按维持泵
    log(f"[{prefix}] {label}弹窗出现 {time.strftime('%H:%M:%S')}，进入闭环控制", "SYS")

    def _release_hold():
        """释放长按（窗口结束/战斗打断/异常统一出口；未按住无操作）。"""
        nonlocal holder
        if holder is not None:
            holder.stop()
            holder = None

    def _maintain_pump(now, p, bgr, L, R):
        """维持泵：点按把填充保持在目标带内（mash 维持段 / hold_catch 共用）。

        点击率伺服（蛟魔探秘钓鱼 2026-11-10 实机标定）：填充位置与持续
        点击率正相关、停手即回退，按位置误差每 POCHU_MASH_RATE_EVAL 秒
        调整速率，逼近右缘（R-余量）完全停泵；倒计时消失多轮=已结算，
        停手防结果画面空点。状态均为 play_pochufengyin 的局部变量。
        """
        nonlocal mash_waiting, mash_credit, mash_prev_t, mash_rate, \
            mash_rate_t, clicks, last_click, btn_pt
        mash_waiting = False
        aim = (L + R) / 2.0
        top = R - POCHU_MASH_TOP_MARGIN
        dt = max(0.0, now - mash_prev_t)
        mash_prev_t = now
        n = 0
        if p >= top or cnt_none_polls >= POCHU_MASH_CNT_GONE_POLLS:
            # 逼近右缘：完全停泵（追帧滞后仍可能继续上升）；
            # 倒计时消失多轮 = 已结算，停手防结果画面空点
            mash_credit = 0.0
        else:
            mash_credit = min(mash_credit + mash_rate * dt,
                              POCHU_MASH_CREDIT_MAX)
            n = int(mash_credit)
            if n:
                mash_credit -= n
                n = min(n, POCHU_MASH_HOLD_MAX)
        # 点击率伺服：按位置误差调整速率（误差 1 刻度 ≈ 1.2 击/秒）
        if now - mash_rate_t >= POCHU_MASH_RATE_EVAL:
            mash_rate_t = now
            err = aim - p
            if abs(err) > POCHU_MASH_ERR_DEADBAND:
                mash_rate = max(
                    POCHU_MASH_RATE_MIN,
                    min(POCHU_MASH_RATE_MAX,
                        mash_rate + POCHU_MASH_RATE_GAIN * err))
        if n > 0:
            dpt = game_map.match_frame_np(
                bgr, act_icon, threshold=POCHU_THRESHOLD,
                region=POCHU_REGION, fallback=False)
            if dpt is not None:
                btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
            if btn_pt is not None:
                rapid_click_burst(win, btn_pt[0], btn_pt[1], n,
                                  press=mash_press)
                clicks += n
                last_click = now

    try:
        while time.time() < deadline:
            t0 = time.time()
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.1)
                continue
            bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
            if is_in_battle_frame(bgr):
                return False, f"战斗开始，{label}中断"
            title = game_map.match_frame_np(bgr, title_icon,
                                            threshold=POCHU_THRESHOLD,
                                            region=POCHU_REGION, fallback=False)
            if title is None:
                # 标题消失 = 小游戏结束。连续消失满 POCHU_END_WINDOW
                # 秒才确认结束（结束确认 2 秒窗口防单帧误判，不得缩短）
                if time.time() - last_seen >= POCHU_END_WINDOW:
                    ok = terminal is not None and "保持" in terminal
                    if zone is not None and last_meas_p is not None:
                        # 点击模式保留 ±0.3 容差（按下的量化跳变）；
                        # 长按模式连续测量，按精确区间判定命中
                        tol = 0.0 if hold else 0.3
                        ok = ok or zone[0] - tol <= last_meas_p <= zone[1] + tol
                    state = (f"P={last_meas_p:.2f}" if last_meas_p is not None
                             else "P=未知")
                    if zone is not None:
                        state += f" 区间[{zone[0]:.1f},{zone[1]:.1f}]"
                    if last_cnt is not None:
                        state += f" 倒计时={last_cnt}s"
                    return True, (f"{label}结束（{state}，{act_name} {clicks} 次，"
                                  f"{'命中区间' if ok else '未确认命中'}）")
                _pace(t0)
                continue
            last_seen = time.time()
            now = time.time()
            if not started:
                # 「开始」出现则点击；若按钮已是流程按钮态（手动已开/续接
                # 场景），直接进入闭环不点
                kpt = game_map.match_frame_np(bgr, POCHU_START_ICON,
                                              threshold=POCHU_THRESHOLD,
                                              region=POCHU_REGION, fallback=False)
                dpt = game_map.match_frame_np(bgr, act_icon,
                                              threshold=POCHU_THRESHOLD,
                                              region=POCHU_REGION, fallback=False)
                if kpt is not None:
                    human_click(win, kpt[0], kpt[1])
                    btn_pt = kpt
                    started = True
                    log(f"[{prefix}] {label}：已点击「开始」@({kpt[0]},{kpt[1]})", "SYS")
                elif dpt is not None:
                    btn_pt = dpt
                    started = True
                    log(f"[{prefix}] {label}：按钮已是「{act_name}」态 "
                        f"@({dpt[0]},{dpt[1]})，直接进入闭环", "SYS")
                _pace(t0)
                continue
            # ---- 已开始：闭环决策（同帧测量）----
            if hold and holder is not None:
                # 长按期间跳过区域 OCR：单次 100~200ms 阻塞会拖慢填充
                # 位置采样（实测填充 ~3.7 刻度/秒）；游标控制优先，倒计时/
                # 保持文字由松开后的轮询与标题消失兜底（长按中不显示
                # 倒计时，避免日志里显示按下前的陈旧值）
                cnt = btn = None
                last_cnt = None
            else:
                force_ocr = now - last_ocr >= POCHU_OCR_REFRESH
                if force_ocr:
                    last_ocr = now
                cnt = _pochu_countdown(img, cnt_cache, force=force_ocr)
                if cnt is not None:
                    last_cnt = cnt
                    cnt_none_polls = 0
                elif mash and last_cnt is not None:
                    # 倒计时曾读到、随后连续消失 = 小游戏已结算（结果画面），
                    # 冲刺模式据此停手，防在结算画面继续空点
                    cnt_none_polls += 1
                btn = _pochu_ocr_gated(img, POCHU_BTN_REGION, btn_cache,
                                       force=force_ocr)
            lx, rx, cx = _pochu_zone(bgr)
            if cx is None:
                if zone is None:
                    # 从未识别到目标带：0.5s 重试（无区间无法决策）
                    if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                        last_log = now
                        log(f"[{prefix}] {label}：目标带未识别，0.5s 后重试", "WARN")
                    _pace(t0)
                    continue
                # 填充前缘接近/遮住目标括号时模板匹配会掉到阈值下
                # （2026-11-10 实测：长按推进至带前必现）——沿用上次
                # 识别到的区间继续决策，否则永远等不到释放时机
                L, R, C = zone
                if now - last_zone_warn >= POCHU_STATE_LOG_INTERVAL:
                    last_zone_warn = now
                    log(f"[{prefix}] {label}：目标带暂未识别（可能被填充前缘"
                        f"遮挡），沿用上次区间 L={L:.1f} R={R:.1f} C={C:.1f}",
                        "WARN")
            else:
                L, R, C = pochu_tick(lx), pochu_tick(rx), pochu_tick(cx)
                if mash and POCHU_MASH_ZONE_OFFSET:
                    # 冲刺模式目标带修正：识别值整体偏小（用户实测试调）
                    L += POCHU_MASH_ZONE_OFFSET
                    R += POCHU_MASH_ZONE_OFFSET
                    C += POCHU_MASH_ZONE_OFFSET
                if zone is None or abs(zone[2] - C) > 0.15:
                    zone = (L, R, C)
                    log(f"[{prefix}] {label}：目标带 L={L:.1f} R={R:.1f} C={C:.1f}"
                        f"（已{act_name} {clicks} 次）", "SYS")
            px = pochu_fill_edge(bgr)
            p = 0.0 if px is None else pochu_tick(px)
            # 每轮记录最新测量 P：结束描述/命中判定用。点击模式 last_p 只在
            # 点击时更新，0 次点击（填充一开始就在带内）时是 None——不能拿它
            # 做结束判定，否则误报「P=未知/未确认命中」（2026-11-11 客栈实测）
            last_meas_p = p
            # 完全停手判定（优先；terminal 一旦建立不再恢复）
            if terminal is None:
                if btn and "保持" in btn:
                    terminal = f"按钮变为「{btn}」"
                elif not mash and cnt is not None and cnt <= POCHU_CNT_STOP:
                    # 冲刺模式不禁点：该游戏填充条会持续回退，提前停手
                    # 等于让游标掉出目标带（用户 2026-11-10 实测教训），
                    # 维持到窗口标题消失为止
                    terminal = f"倒计时 {cnt}s≤{POCHU_CNT_STOP}s"
                elif p > R + (POCHU_HOLD_OVER_MARGIN if hold else 0.0):
                    over_cnt += 1
                    if over_cnt >= 2 and not hold and not mash:
                        # 点击模式：过头永久停手。长按模式不设永久终态
                        # （松开回落、回到 L 以下再次按住）；冲刺模式也不设
                        # 永久终态——维持泵在 P≥R-余量 已自动停泵，追帧
                        # 滞后造成的瞬时越界应等回落，停死会丢失维持
                        terminal = f"过头 P={p:.2f}>R={R:.2f}"
                else:
                    over_cnt = 0
                if terminal is not None:
                    log(f"[{prefix}] {label}：{terminal}，完全停手等待结算", "SYS")
            if hold and hold_catch:
                # 长按运输 + 点按维持（开采石头，用户 2026-11-10 技巧）：
                # 1) 运输：长按把填充推过目标带上缘（P≥R+POCHU_CATCH_ABOVE）
                # 2) 松手：填充整体回落（该游戏松手即回退）
                # 3) 捕捉：回落到 C（带中心）切换维持泵，点按保持在 [L,R]
                # 4) 兜底：维持泵仍落出带下 → 重新长按运输（限次）
                if catch_pump:
                    _maintain_pump(now, p, bgr, L, R)
                    if p < L - POCHU_CATCH_REPRESS_MARGIN \
                            and press_cnt < POCHU_CATCH_MAX_PRESS \
                            and now - last_release >= POCHU_CATCH_MIN_GAP \
                            and holder is None:
                        catch_pump = False
                        log(f"[{prefix}] {label}：P={p:.2f} 掉出带下"
                            f"（L-{POCHU_CATCH_REPRESS_MARGIN:.2f}），"
                            f"重新长按运输", "WARN")
                elif holder is not None:
                    if p >= R + POCHU_CATCH_ABOVE:
                        _release_hold()
                        last_release = now
                        log(f"[{prefix}] {label}：P={p:.2f} 已越过目标带上缘"
                            f"（R+{POCHU_CATCH_ABOVE:.2f}），松手等回落"
                            f"至 C={C:.2f} 后点按维持", "SYS")
                    elif not hold_stall_logged and hold_p0 is not None \
                            and now - hold_t0 >= POCHU_HOLD_STALL \
                            and abs(p - hold_p0) < 0.05 and p < L - 0.2:
                        # 按住无进度（长按通道可能未生效）：释放后重按自愈
                        hold_stall_logged = True
                        log(f"[{prefix}] {label}：已长按 {POCHU_HOLD_STALL:.0f}s"
                            f" P 未移动（P={p:.2f}），释放重按并检查长按通道",
                            "WARN")
                        _release_hold()
                        last_release = now
                elif terminal is None:
                    if p < L - POCHU_CATCH_REPRESS_MARGIN:
                        # 尚未开始/回落途中已低于带下：长按运输
                        if press_cnt < POCHU_CATCH_MAX_PRESS \
                                and now - last_release >= POCHU_CATCH_MIN_GAP:
                            dpt = game_map.match_frame_np(
                                bgr, act_icon, threshold=POCHU_THRESHOLD,
                                region=POCHU_REGION, fallback=False)
                            if dpt is not None:
                                btn_pt = dpt  # 命中刷新按钮位置（防漂移）
                            # 按下后按钮外观会变，模板不命中时仍按缓存坐标按
                            if btn_pt is not None:
                                h = MouseHold(win, btn_pt[0], btn_pt[1])
                                if h.start():
                                    holder = h
                                    press_cnt += 1
                                    clicks += 1
                                    hold_p0 = p
                                    hold_t0 = now
                                    hold_stall_logged = False
                                    log(f"[{prefix}] {label}：按住「{act_name}」"
                                        f"@({btn_pt[0]},{btn_pt[1]}) P={p:.2f}，"
                                        f"第 {press_cnt}/{POCHU_CATCH_MAX_PRESS}"
                                        f" 次长按运输", "SYS")
                                elif now - last_log >= POCHU_STATE_LOG_INTERVAL:
                                    last_log = now
                                    log(f"[{prefix}] {label}：长按按下失败"
                                        f"（窗口坐标不可用），下轮重试", "WARN")
                    elif p <= C:
                        # 回落到带中心 C：切入点按维持泵
                        catch_pump = True
                        mash_credit = 0.0
                        mash_rate_t = now
                        mash_prev_t = now
                        log(f"[{prefix}] {label}：已回落至 C={C:.2f}"
                            f"（区间[{L:.1f},{R:.1f}]），切换点按维持",
                            "SYS")
            elif hold:
                # 长按模式（钓鱼，2026-11-10 用户实测）：按住不放，游戏
                # 会把游标推进到目标区间并锁定；松手即自动回退、鱼脱钩。
                # 因此进入区间后必须保持按住直到窗口结束，不做 P≥L 释放。
                # 唯一例外：P>R 连续 2 轮（未锁定、冲过右缘）——松开让其
                # 回落，回落到 L 以下会再次按住（终态不永久锁死）。
                if holder is not None and over_cnt >= 2:
                    _release_hold()
                    over_cnt = 0
                    log(f"[{prefix}] {label}：P={p:.2f}>R+余量"
                        f"={R + POCHU_HOLD_OVER_MARGIN:.2f} 冲过目标带，"
                        f"释放长按让其回落（回到 L 以下再按住）", "WARN")
                if terminal is None and p < L and holder is None:
                    dpt = game_map.match_frame_np(bgr, act_icon,
                                                  threshold=POCHU_THRESHOLD,
                                                  region=POCHU_REGION,
                                                  fallback=False)
                    if dpt is not None:
                        btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                    # 按下后按钮外观会变（用户 2026-11-10 确认），模板
                    # 不命中时仍按缓存坐标按下/续按
                    if btn_pt is not None:
                        h = MouseHold(win, btn_pt[0], btn_pt[1])
                        if h.start():
                            holder = h
                            clicks += 1
                            hold_p0 = p
                            hold_t0 = now
                            hold_stall_logged = False
                            log(f"[{prefix}] {label}：按住「{act_name}」"
                                f"@({btn_pt[0]},{btn_pt[1]}) P={p:.2f}<L={L:.1f}，"
                                f"持续推进", "SYS")
                        elif now - last_log >= POCHU_STATE_LOG_INTERVAL:
                            last_log = now
                            log(f"[{prefix}] {label}：长按按下失败（窗口坐标"
                                f"不可用），下轮重试", "WARN")
                elif holder is not None and not hold_stall_logged \
                        and hold_p0 is not None \
                        and now - hold_t0 >= POCHU_HOLD_STALL \
                        and abs(p - hold_p0) < 0.05 and p < L - 0.2:
                    # 仍在区间左侧却按住无进度：坐标/静默长按通道可能未
                    # 生效 → 释放后下一轮重按一次（down 消息漏消费自愈）。
                    # p 已进区间时不动：那是游戏把游标锁定在目标带的正常
                    # 现象，不能按「无进度」处理（否则松手回退脱钩）。
                    hold_stall_logged = True
                    log(f"[{prefix}] {label}：已长按 {POCHU_HOLD_STALL:.0f}s P 未移动"
                        f"（P={p:.2f}），释放重按并检查按钮坐标/长按通道", "WARN")
                    _release_hold()
            elif terminal is None and mash:
                # 冲刺连点（蛟魔探秘钓鱼）两段式：
                # 1) 接近段（P < L-0.45）：按在线估算的每击推进量连点冲刺，
                #    每次冲刺后等填充条追帧停稳再决策（防按滞后值过量）
                # 2) 维持段：「维持泵」点击率伺服——保持一个持续点击率
                #    （实测位置与点击率正相关，停手 ~1s 即回退），按位置
                #    误差每 0.8s 调节速率，逼近右缘完全停泵；一直维持到
                #    窗口标题消失（不按倒计时停手）
                if p >= L - POCHU_MASH_HOLD_ENTER and not mash_hold:
                    mash_hold = True
                    mash_credit = 0.0
                    mash_rate_t = now
                    mash_prev_t = now
                if mash_hold:
                    _maintain_pump(now, p, bgr, L, R)
                if not mash_hold and mash_waiting:
                    if p > mash_prev_p + POCHU_MASH_RISE_EPS:
                        # 填充条仍在上升（追帧/点击队列处理中）：不决策，
                        # 按滞后值补点会过量冲刺
                        mash_prev_p = p
                        mash_stable_cnt = 0
                    else:
                        mash_stable_cnt += 1
                    if mash_waiting and mash_stable_cnt >= POCHU_MASH_STABLE_POLLS:
                        # 连续多轮不再上升 = 停稳：用停稳值更新每击推进量
                        mash_stable_cnt = 0
                        if mash_mark is not None:
                            dc = clicks - mash_mark[0]
                            dp = p - mash_mark[1]
                            if dc >= 1 and dp > 0.03:
                                obs = dp / dc
                                if POCHU_MASH_RATIO_MIN <= obs \
                                        <= POCHU_MASH_RATIO_MAX:
                                    mash_ratio = obs if mash_ratio is None \
                                        else mash_ratio * 0.4 + obs * 0.6
                                mash_stall = 0
                            elif dc >= POCHU_MASH_MIN_CLICKS and dp <= 0.02:
                                mash_stall += 1
                                if mash_stall == 2:
                                    mash_press = min(POCHU_MASH_PRESS_MAX,
                                                     mash_press + 0.015)
                                    log(f"[{prefix}] {label}：连点 {dc} 次"
                                        f" P 未推进（P={p:.2f}），按压时长增至"
                                        f" {mash_press:.3f}s（检查点击漏注册）",
                                        "WARN")
                        mash_mark = (clicks, p)
                        mash_waiting = False
                if not mash_hold and not mash_waiting:
                    ratio = mash_ratio or POCHU_MASH_RATIO0
                    goal = L + POCHU_MASH_AIM
                    n = 0
                    if p < goal - POCHU_MASH_DEADBAND:
                        n = int((goal - p) / (ratio * POCHU_MASH_RATIO_SAFETY))
                        n = max(1, min(n, POCHU_MASH_MAX_BURST))
                    if n > 0:
                        dpt = game_map.match_frame_np(
                            bgr, act_icon, threshold=POCHU_THRESHOLD,
                            region=POCHU_REGION, fallback=False)
                        if dpt is not None:
                            btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                        if btn_pt is not None:
                            # 整批一次定位/一把锁，逐击按下抬起（静默通道
                            # ~20ms/击；逐击 rapid_click 静默 ~150ms/击，
                            # 倒计时内推不到目标带）
                            mash_mark = (clicks, p)
                            mash_prev_p = p
                            rapid_click_burst(win, btn_pt[0], btn_pt[1], n,
                                              press=mash_press)
                            clicks += n
                            last_click = now
                            mash_waiting = True
            elif terminal is None and p < C:
                # 点击决策：推进到目标带中心 C 才停手（节奏按 C 三档）。
                # 不能只推到 L 就停：破除封印松手后填充会回落，停在左缘
                # 有一点回落就掉出带下（用户 2026-11-11：P=0.05 虽 >0 但
                # 有风险），落在 C 附近上下各留 ~0.5 余量
                if C < 4.0:
                    interval = POCHU_TIER_NEAR
                elif C <= 7.0:
                    interval = POCHU_TIER_MID
                else:
                    interval = POCHU_TIER_FAR
                if now - last_click >= interval:
                    dpt = game_map.match_frame_np(bgr, act_icon,
                                                  threshold=POCHU_THRESHOLD,
                                                  region=POCHU_REGION,
                                                  fallback=False)
                    if dpt is not None:
                        btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                    # 按钮在过程中会变成其他外观但坐标不变（用户 2026-11-09
                    # 确认），模板不命中时仍按缓存坐标续点
                    if btn_pt is not None:
                        human_click(win, btn_pt[0], btn_pt[1])
                        clicks += 1
                        last_click = now
                        # P 不动告警：点击没生效检查按钮坐标
                        if last_p is not None and abs(p - last_p) < 0.05:
                            stall_clicks += 1
                            if stall_clicks == POCHU_STALL_CLICKS:
                                log(f"[{prefix}] {label}：已点击 {POCHU_STALL_CLICKS} 次"
                                    f" P 未移动（P={p:.2f}），检查按钮坐标/点击是否生效",
                                    "WARN")
                        else:
                            stall_clicks = 0
                        last_p = p
            if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                last_log = now
                if terminal is not None:
                    st = terminal
                elif p < (L if hold or mash else C):
                    # 点击模式推进目标是带中心 C（防松手回落掉出带下），
                    # 长按/冲刺模式仍以 L 为推进边界
                    st = f"{act_name}推进中 P={p:.2f}"
                else:
                    st = f"保持区间内 P={p:.2f}"
                extra = f" 倒计时={last_cnt}s" if last_cnt is not None else ""
                log(f"[{prefix}] {label}：{st} 区间[{L:.1f},{R:.1f}] C={C:.1f}"
                    f" {act_name}{clicks}次{extra}", "INFO")
            _pace(t0)
    finally:
        # 标题消失/战斗打断/异常退出统一抬起按键，防鼠标卡在按下状态
        _release_hold()
    return False, f"{label}超时（{POCHU_TIMEOUT}s，{act_name} {clicks} 次，P={last_meas_p}）"
