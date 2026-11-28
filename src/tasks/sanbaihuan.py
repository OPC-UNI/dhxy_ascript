# -*- coding: utf-8 -*-
"""300环（江湖历练）任务：任务栏判定 → 洛阳萧举人领取/续跑 → 跟踪到消失。

用户 2026-11-26 实机规则：
1. 三态判定（用户 2026-11-26 实机截图）：
   - 运行中：任务追踪面板有「当前环名[江湖历练任务]」（实机
     「见多识广[江湖历练任务]」，内容绿字可点）→ 直接跟踪；
   - 暂停：追踪面板无任务；任务栏「日常活动」列表与无任务相同
     （分组 [1] + 仅「历练征程」），但详情有「目前环数：1/300」/
     「本周免费暂停次数」→ 去萧举人点续跑；
   - 无任务：列表同暂停，但详情没有环数/暂停 → 去萧举人点领取。
   任务追踪面板的「江湖历练任务」标题消失 = 本轮结束（300环跑完）。
2. 洛阳小地图：勾选底部「普通」筛选（显示 NPC 绿字）→ 点击萧举人
   （洛阳 434,142 附近）→ 点击后立即取消「普通」筛选（勾选时 NPC 名
   会吃掉其它点击，用户要求不能残留）→ 到达后自动弹出对话框。
3. 领取点「踏上周游之路，寻找百兽契约录失佚篇章」；续跑点
   「吃饱喝足，我要继续周游江湖，修复灵缘！」。
4. 关闭地图，等待任务出现在追踪面板后交给 follow_task 跟踪。

2026-11-26 实机探针（洛阳 434,135 到达帧）：
- 萧举人对话框没有标准对话框边框（dialog_frame 模板 0.59 不命中），
  用 NPC 立绘脸部模板判定在场（开 1.000 / 关 ≤0.571，阈值 0.8）；
- 对话框选项全部为同一亮绿色（0,208,17），选项可用性不能用颜色区分，
  领取/续跑必须由任务栏状态决定；
- 选项行 OCR 高度 24~28px（相邻选项框有重叠），共享
  claim_flow.find_claim_option 的绿字中心定位可用；
- 点击萧举人小地图点后地图保持打开，对话框叠加在地图窗口上；点完
  选项后再关闭地图。

小游戏类环（任务侧只做接入，实现见 minigames/）：
- 拼图（灵心巧手，4×4，2026-11-26）：sanbaihuan_jigsaw；
- 画符（灵心巧手，蝴蝶妹妹「制符」窗口，2026-11-27 实机验证）：
  minigames/huafu —— 左纸模板符文骨架提取 + 粗端起笔方向运笔，
  完成不响应则关窗重走追踪换符文（空闲帧才重走，2026-11-28）；
- 天下无贼（2026-11-27 实机）：minigames/tianxiawuzei —— 两种类型共用
  追踪文案与入口选项「毛贼！休想逃！」：竞技场型 → 48s 敲怪（背景差分
  + 颜色直方图分类）；战斗型 → 进战斗后战斗线程按注入的大型爆竹计划
  执行（右键爆竹/点怪/召唤兽防御，十字爆炸），失败后本模块重走追踪
  链接再挑战；
- 挖宝（2026-11-26）：到达藏宝处并站定后，背包-任务储物格右键挖宝锄头
  （降落不是必须的，用户 2026-11-27）；
- 答题（千古一题，2026-11-27）：诗句库（core/poem_bank.py）自动选句；
- 四锁四色钥匙开锁（妙趣横生变体，2026-11-27 实机）：minigames/
  sanbaihuan_jiesuo —— 阿合奇阿洪对话框「解开这奇锁！」→ 开锁面板
  （剩余 10 次）Mastermind 求解；次数不足点 X 关窗重开一次。
"""
import random
import re
import time

from PIL import Image

from ..core import app_utils
from ..core import debug_overlay
from ..core import poem_bank
from ..core import settings
from ..core.app_utils import log, norm_text
from ..device import items as item_catalog
from ..device import map as game_map
from ..device import map_nav
from ..device import task_info
from ..device import task_panel
from ..device import task_tracker
from ..device import battle as battle_lib
from ..device import zuoqi
from ..device.battle import is_in_battle
from ..device.dialog_common import (_click_green_option_once,
                                    _dialog_text_present,
                                    click_green_option_safe,
                                    DIALOG_FRAME_THRESHOLD,
                                    dialog_frame_score,
                                    WalkGuard)
from ..minigames import huafu as huafu_mini
from ..minigames import sanbaihuan_jiesuo as jiesuo_mini
from ..minigames import sanbaihuan_jigsaw as jigsaw
from ..minigames import tianxiawuzei as txwz
from ..workflows import claim_flow
from ..workflows import courtyard_craft
from ..workflows import give_flow
from ..workflows import item_use
from ..workflows.follow import DEFAULT_KNOWN_GREEN_OPTIONS, follow_task

MODE_NAME = "300环"
TAG = "[300环] "

# 任务追踪标题（用户 2026-11-26 实机截图）：追踪面板显示当前环的任务名
# + 方括号玩法名，如「见多识广[江湖历练任务]」；任务名每环变化，方括号
# 内的「江湖历练任务」恒定，因此用恒定段做标题匹配。
TASK_TITLE = "江湖历练任务"
# 日常活动页分组/子任务行关键字（任务栏活动树）
TASK_GROUP_KEYWORD = "江湖历练任务"
TASK_CHILD_KEYWORD = "历练征程"
# 分组行计数（如「江湖历练任务[1]」→ 1）：无任务时只有「历练征程」
# 一个子行（[1]）；已有任务时多出当前环任务子行（实机「江湖历练任务[2]」
# + 历练征程 + 见多识广），计数为 2。
_GROUP_COUNT_RE = re.compile(r"[\[［【]\s*(\d+)\s*[\]］】]")

# 洛阳萧举人（用户 2026-11-26 提供：洛阳 434,142；实机点击小地图点位
# 到达 434,135 后自动弹对话框）
CLAIM_MAP = "洛阳城"
CLAIM_NPC_POS = (434, 142)
# 萧举人对话框选项原文（用户 2026-11-26 提供，精确子串；claim_flow
# 匹配前做 norm_text 去标点）
CLAIM_OPTION_TEXT = "踏上周游之路，寻找百兽契约录失佚篇章"
CONTINUE_OPTION_TEXT = "吃饱喝足，我要继续周游江湖，修复灵缘！"
CLAIM_OPTION_NEEDLES = (norm_text(CLAIM_OPTION_TEXT),)
CONTINUE_OPTION_NEEDLES = (norm_text(CONTINUE_OPTION_TEXT),)

# 萧举人立绘脸部模板（60x70；2026-11-26 实机到达帧采集，开 1.00 /
# 关与其它场景 ≤0.571，取 0.8 分离）。对话框无标准边框，用立绘判定
# 在场后再 OCR 对话区。
NPC_FACE_ICON = "sanbaihuan_xiao_juren_face.png"
NPC_FACE_REGION = (430, 130, 720, 350)
NPC_FACE_THRESHOLD = 0.8
# 到达等待：从任意地图走到洛阳萧举人，跨地图 + 城内步行实测约 30~90s
DIALOG_WAIT_TIMEOUT = 150.0
DIALOG_POLL_INTERVAL = 0.8
# 选项点击后等待任务进入追踪面板（任务渲染实测 < 15s）
TRACK_VERIFY_TIMEOUT = 20.0
TRACK_VERIFY_POLL = 0.8
# 领取入口点击等待（到达后对话框弹出可能滞后于立绘渲染）
CLAIM_CLICK_TIMEOUT = 8.0
CLAIM_CLICK_POLL = 0.6

# 跟踪超时：单段 300环可能跨多张地图，与五环同量级（1800s）
FOLLOW_TIMEOUT = 1800


def _panel_rows(win, prefix=TAG):
    """打开任务栏 → 日常活动页 → 扫描「江湖历练任务」分组行。

    返回 (group_row, child_rows, 描述)。任务栏保持打开，由调用方关闭。
    group_row 为 None 表示列表里没有该分组（活动不可用或读取失败）。
    """
    row, info = task_panel.scan_list_for(
        win, TASK_GROUP_KEYWORD,
        tab=task_panel.TASK_PANEL_TAB_DAILY,
        anchors=task_panel.TASK_PANEL_DAILY_ANCHORS,
        prefix=prefix)
    if row is None:
        return None, [], info
    rows, _info = task_panel.read_list_rows(win)
    if rows is None:
        return row, [], info
    group = task_panel.find_row(rows, TASK_GROUP_KEYWORD,
                                require_group=True) or row
    children = task_panel.child_rows(rows, group)
    return group, children, f"已读取日常活动列表：{info}"


# 暂停状态判据（用户 2026-11-26 实机暂停截图）：列表与无任务时相同
# （分组 [1] + 仅「历练征程」），但右侧任务详情有「任务状态：目前环数
# 1/300、本周免费暂停次数 1/15」，任务目的里也写着「现在处于暂停状态，
# 快去找萧举人继续任务吧！」。无任务帧没有这些行。
PAUSED_DETAIL_NEEDLES = ("目前环数", "暂停")


def chain_state_in_rows(group_text, child_texts, detail_text=""):
    """任务栏行/详情文案 → "running" / "paused" / "none"。

    用户 2026-11-26 实机三态：
    - running（任务正在跑）：分组计数 [2] 且多出当前环任务子行
      （如「见多识广」），任务追踪面板显示「见多识广[江湖历练任务]」；
    - paused（任务暂停）：分组 [1] 且只有「历练征程」子行，但详情有
      「目前环数」/「暂停」；追踪面板无任务；
    - none（无任务）：分组 [1] 且只有「历练征程」子行，详情无环数/暂停。
    """
    if any(TASK_CHILD_KEYWORD not in (t or "") for t in child_texts):
        return "running"
    m = _GROUP_COUNT_RE.search(group_text or "")
    if m and int(m.group(1)) >= 2:
        return "running"
    if any(n in (detail_text or "") for n in PAUSED_DETAIL_NEEDLES):
        return "paused"
    return "none"


def probe_chain_state(win, prefix=TAG):
    """读取任务栏（列表 + 选中分组后的详情）判定 300环 状态。

    返回 ("running" | "paused" | "none" | None, 描述)：None 表示读取
    失败/不确定。选中「江湖历练任务」分组行后读详情，暂停状态靠详情里的
    「目前环数」/「暂停」区分于无任务（列表长相完全相同）。
    """
    ok, info = task_panel.open_task_panel(win, prefix)
    if not ok:
        return None, f"打开任务栏失败：{info}"
    group, children, info = _panel_rows(win, prefix)
    if group is None:
        task_panel.close_task_panel(win, prefix)
        return None, f"日常活动页未找到「{TASK_GROUP_KEYWORD}」：{info}"
    # 选中分组行，确保右侧详情显示的是 300环（面板可能记住别的选中行）
    task_panel.select_row(win, group, prefix)
    detail_items, _dinfo = task_panel.read_detail_lines(win)
    detail_text = "".join(t for t, _r, s in (detail_items or [])
                          if s >= task_panel.TASK_PANEL_MIN_OCR_SCORE)
    child_texts = [r["text"] for r in children]
    state = chain_state_in_rows(group["text"], child_texts, detail_text)
    log(f"{prefix}任务栏判定：分组「{group['text']}」"
        f"子行 {child_texts or '无'}，详情"
        f"{'含' if any(n in detail_text for n in PAUSED_DETAIL_NEEDLES) else '无'}"
        f"环数/暂停 → {state}", "SYS")
    task_panel.close_task_panel(win, prefix)
    return state, "任务栏状态读取完成"


def track_state(win, prefix=TAG):
    """单帧判定追踪面板：任务是否存在 + 是否有可点绿字行。

    返回 (has_task, has_green_link)；截图失败返回 None。用户 2026-11-26
    实机「已领取」帧：标题「见多识广[江湖历练任务]」非绿、任务内容
    「老贾见多识广，前往拜访一下…」为绿色可点行；有绿字 = 任务可直接
    跟踪；无绿字（暂停/待续跑）才需要回萧举人点续跑。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, img_np = task_tracker.ocr_track(img)
    if not task_tracker.has_task_in(items, img_np, TASK_TITLE):
        return (False, False)
    xmax = task_tracker.panel_row_xmax(img_np)
    for _txt, rect, score in items:
        if score >= 0.55 and rect[0] < xmax \
                and task_tracker.is_green_text(img_np, rect):
            return (True, True)
    return (True, False)


def _walk_to_xiao(win, prefix=TAG):
    """洛阳小地图 → 勾普通 → 点萧举人 → 取消普通。返回 (ok, 描述)。

    用户 2026-11-26 规则：勾选「普通」显示萧举人绿字后点击，点击完立即
    取消勾选（勾选状态会吃其它点击，不能残留）。坐标点击走
    map_nav 标定（悬停校验，等同点击小地图 NPC 点位）；keep_putong
    跳过开图后的统一取消，交给本函数在点击后取消。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过萧举人领取"

    def _on_map_open():
        ok, info = map_nav.set_minimap_putong_filter(
            win, True, log_fn=lambda m: log(f"{prefix}{m}", "INFO"))
        if not ok:
            log(f"{prefix}勾选「普通」筛选失败：{info}", "WARN")

    ok, info, _pt = map_nav.start_navigation(
        win, CLAIM_MAP, CLAIM_NPC_POS[0], CLAIM_NPC_POS[1],
        log_fn=lambda m: log(f"{prefix}[地图] {m}", "INFO"),
        on_map_open=_on_map_open, keep_putong=True)
    if not ok:
        return False, f"导航到{CLAIM_MAP}萧举人失败：{info}"
    # 点击萧举人后立即取消「普通」筛选（用户规则）：此时人物已开始
    # 自动寻路，地图保持打开，取消勾选不影响寻路
    ok2, info2 = map_nav.set_minimap_putong_filter(
        win, False, log_fn=lambda m: log(f"{prefix}{m}", "INFO"))
    if not ok2:
        log(f"{prefix}取消「普通」筛选失败：{info2}", "WARN")
    log(f"{prefix}已点击洛阳萧举人 {CLAIM_NPC_POS}，自动寻路中", "SYS")
    return True, f"已点击{CLAIM_MAP}萧举人 {CLAIM_NPC_POS}"


def _wait_npc_dialog(win, prefix=TAG, timeout=DIALOG_WAIT_TIMEOUT):
    """等待萧举人对话框自动弹出：立绘模板命中后 OCR 对话区。

    返回 (items, img_np) 或 None。对话框无标准边框（见模块注释），
    立绘模板是稳定在场信号；OCR 只在立绘命中后执行（行走期零 OCR）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(DIALOG_POLL_INTERVAL)
            continue
        bgr = game_map.bgr_frame(img)
        score, _pt = game_map.best_match_np(
            bgr, NPC_FACE_ICON, region=NPC_FACE_REGION)
        if score >= NPC_FACE_THRESHOLD:
            items, img_np = game_map.ocr_region(
                img, task_tracker.DIALOG_REGION)
            log(f"{prefix}萧举人对话框已出现（立绘匹配 {score:.2f}）", "SYS")
            return items, img_np
        time.sleep(DIALOG_POLL_INTERVAL)
    return None


def _wait_option_clickable(win, needles, prefix=TAG,
                           timeout=CLAIM_CLICK_TIMEOUT):
    """短轮询：对话框内目标绿色选项可点击（OCR 命中）即返回帧。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(CLAIM_CLICK_POLL)
            continue
        items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if claim_flow.find_claim_option(items, img_np, needles) is not None:
            return items, img_np
        time.sleep(CLAIM_CLICK_POLL)
    return None


def _click_dialog_option(win, needles, cache_key, prefix=TAG):
    """等待并点击对话框目标选项；成功后关闭地图。返回 (ok, 描述)。"""
    frame = _wait_npc_dialog(win, prefix)
    if frame is None:
        return False, f"{DIALOG_WAIT_TIMEOUT:.0f}s 内未出现萧举人对话框"
    items, img_np = frame
    if claim_flow.find_claim_option(items, img_np, needles) is None:
        frame = _wait_option_clickable(win, needles, prefix)
        if frame is None:
            return False, "萧举人对话中未找到目标选项"
        items, img_np = frame
    ok = claim_flow.click_claim_option(
        win, items, img_np, needles=needles, prefix=prefix,
        cache_key=cache_key)
    if not ok:
        return False, "点击萧举人对话选项失败"
    # 选项已点、任务开始：关闭残留地图（点击小地图点位后地图保持打开）
    close_ok, close_info = game_map.close_mini_map(win, quiet=True)
    if not close_ok:
        log(f"{prefix}关闭洛阳小地图失败（忽略）：{close_info}", "WARN")
    return True, "已点击萧举人对话选项"


def _wait_tracked(win, prefix=TAG, timeout=TRACK_VERIFY_TIMEOUT):
    """等待 300环任务出现在任务追踪面板。返回 (ok, 描述)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        if task_tracker.has_task(win, TASK_TITLE):
            return True, "任务已进入追踪面板"
        time.sleep(TRACK_VERIFY_POLL)
    return False, f"{timeout:.0f}s 内任务未出现在追踪面板"


def _track_sig_key(items, img_np):
    """300环文本签名：拜访类环取「环名 + 环名前的目标名」。

    2026-11-27 实机（见多识广环）：任务推进只改追踪首行行首的 NPC 名
    （女儿国国王→灵儿→仙女），「见多识广，前往拜访一下，增加江湖见闻」
    恒定——默认「标题+首行」签名 22 字里只差 2~5 字，相似度
    0.818（女儿国国王→灵儿）/0.909（灵儿→仙女）≥ follow 默认 0.8 阈值，
    被当 OCR 抖动过滤、不点新链接：20:42:41 任务已变「灵儿」，20:43:11
    才由 15s 寻路停滞看门狗补点，下一腿「仙女」再等 15s（用户实机
    反馈「任务追踪变化后没有立刻寻路」）。只比较目标名后：目标切换
    相似度 0.667 ≤ 阈值检出，单字误读 0.833 ≥ 阈值仍过滤（不重复点击）。
    环名不在首行的环（给与/挖宝/答题等，如「快去找到一个9-12属性的
    炼妖石…」）沿用默认标题+首行签名，行为不变。
    """
    task = task_tracker.find_task(
        task_tracker.group_tasks(items, task_tracker.panel_row_xmax(img_np)),
        TASK_TITLE)
    if task is None:
        return ""
    title = norm_text(task["title"])
    ring = title.replace(norm_text(TASK_TITLE), "")
    body = norm_text(task["lines"][0][0]) if task["lines"] else ""
    if ring and ring != title and ring in body:
        target = body.split(ring, 1)[0]
        if target:
            return norm_text(f"{ring}|{target}")
    return task_tracker.task_signature(task)


def _claim_or_continue(win, state, prefix=TAG):
    """去萧举人领取或续跑 300环，并校验任务出现在追踪面板。

    state="running"/"paused" 点续跑选项；"none" 点领取选项；None
    （任务栏读取失败）时按领取处理并告警——领取选项在两种状态都渲染，
    游戏会按实际状态给出提示，失败由调用方重试。
    """
    if state in ("running", "paused"):
        needles = CONTINUE_OPTION_NEEDLES
        cache_key = f"option_{CONTINUE_OPTION_TEXT}"
    else:
        if state is None:
            log(f"{prefix}任务栏状态不确定，按领取处理", "WARN")
        needles = CLAIM_OPTION_NEEDLES
        cache_key = f"option_{CLAIM_OPTION_TEXT}"
    ok, info = _walk_to_xiao(win, prefix)
    if not ok:
        return False, info
    ok, info = _click_dialog_option(win, needles, cache_key, prefix)
    if not ok:
        # 失败也尝试关地图/清理，避免残留地图挡住后续识别
        game_map.close_maps(win, quiet=True)
        return False, info
    return _wait_tracked(win, prefix)


def run_loop(win, cycle_interval=3.0):
    """300环主循环：判定 → 领取/续跑 → 跟踪到任务消失。

    完成判据（用户 2026-11-26）：任务追踪面板中的 300环任务消失。
    每完成一段计一次，次数设置（默认 1）达到即结束；次数 0 = 不限。
    """
    max_rounds = settings.load_task_counts().get(MODE_NAME, 0)
    log(f"{TAG}任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    done = app_utils.task_progress_done(win, MODE_NAME)
    if done:
        log(f"{TAG}恢复当天进度: 已完成 {done} 段", "SYS")
    if 0 < max_rounds <= done:
        log(f"{TAG}当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 段300环任务，达到次数上限 {max_rounds}"

    _DIG_STATE.update(key=None, used=False, used_at=0.0,
                      last_pos_at=0.0, last_try=0.0, last_pos=None,
                      stable=0, tries=0, escalated=False)  # 复位挖宝状态
    _TXWZ_STATE.update(ended_at=0.0, retry_at=0.0, retries=0)  # 复位天下无贼重试
    fail_streak = 0  # 连续领取/续跑失败次数（用户规则：3 次升级一条 ERROR）
    while True:
        if is_in_battle(win):
            time.sleep(cycle_interval)
            continue
        tracked = track_state(win)
        if tracked is not None and tracked[0]:
            # 用户 2026-11-26：有300环正在跑时任务追踪面板有任务；暂停态
            # 追踪面板无任务（列表三态见 chain_state_in_rows）
            log(f"{TAG}任务追踪中已有300环任务"
                f"（绿字{'可点' if tracked[1] else '未渲染'}），直接跟踪", "SYS")
        else:
            claim_state, info = probe_chain_state(win)
            log(f"{TAG}{info}", "SYS" if claim_state else "WARN")
            ok, info = _claim_or_continue(win, claim_state)
            if not ok:
                fail_streak += 1
                if fail_streak >= 3:
                    log(f"{TAG}领取/续跑连续失败 {fail_streak} 次：{info}",
                        "ERROR")
                else:
                    log(f"{TAG}{info}", "WARN")
                time.sleep(cycle_interval)
                continue
            fail_streak = 0
            log(f"{TAG}{info}", "SYS")
        debug_overlay.set_phase("跟踪任务")  # 看板阶段
        # 捕捉环战斗计划不跨 follow 轮次残留：本轮若仍是捕捉环，extras 首帧
        # 会重新注入（防上一轮超时/异常后计划残留到其它环的战斗）
        battle_lib.clear_battle_plan()
        _capture_reset()
        ok, info = follow_task(
            win, TASK_TITLE, timeout=FOLLOW_TIMEOUT, prefix=MODE_NAME,
            task_gone_polls=2,
            # 300环 NPC 对话框没有标准对话框边框：关闭模板门控，保证
            # 「一切就交给我吧」等任务选项能被 OCR 并点击（用户 2026-11-26
            # 实机对话框与萧举人同款无边框）
            dialog_tpl_gate=False,
            known_green_options=tuple(DEFAULT_KNOWN_GREEN_OPTIONS)
            + TASK_ACCEPT_OPTIONS,
            extra_dialog_handler=_extra_dialog_handler,
            wait_handler=_wait_handler,
            # 给与环（急人所急等）追踪文案含物品名+目标名两个绿字链接：
            # 物品名（无下划线）点了不寻路，必须点「交给」后的目标名
            # （2026-11-28 实机，见 _give_link_picker）
            green_picker=_give_link_picker,
            # 灵宝给与环（急人所急等）在点追踪链接前先去庭院聚灵台打造
            # 所需物品（用户 2026-11-27 规则；pre_click 保证人物不会先被
            # 自动寻路到给与 NPC，而是先打造再前往）
            pre_click_handler=_courtyard_craft_guard,
            # 签名只比「目标名」（见 _track_sig_key）：见多识广环推进只改
            # 行首 NPC 名，默认签名相似度 0.818~0.909 被当 OCR 抖动过滤，
            # 变化后不点新链接（2026-11-27 实机）
            signature_key=_track_sig_key,
            # 寻路停滞看门狗全轮次启用（stall_recheck_target=None）：2026-11-27
            # 实机见多识广环（狮驼岭→嬷嬷），自动寻路被剧情点屏打断后人物
            # 原地站立 [9,8]、追踪面板仍是绿色可点链接，follow 空等到 1800s
            # 超时；人工补点追踪链接（NPC 名下划线）数秒即到达并使任务推进。
            # 看门狗静止 ≥15s 且无对话框/战斗时自动补点，把恢复时间压到 ~15s
            # （五环 2026-11-19 同款教训）。
            stall_recheck=True,
            stall_recheck_target=None)
        if not ok:
            # 超时/卡住：任务可能仍在追踪中，回主循环继续跟踪；
            # 只有追踪面板消失（follow 成功）才计完成
            log(f"{TAG}跟踪未完成：{info}", "WARN")
            continue
        done += 1
        app_utils.task_progress_add(win, MODE_NAME, done)
        log(f"{TAG}本轮完成（任务已从追踪面板消失），累计 {done} 段", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 段300环任务，达到次数上限 {max_rounds}"
        log(f"{TAG}继续下一段（重新判定领取/续跑）", "SYS")


# ---- 给与类环（用户 2026-11-26）：天书残卷按卷号选物 ----
# 追踪文案「快去找到一个天书残卷三或十…并将其交给高小姐」：结尾是给与
# 目标，前面的天书残卷三/十是可选物品；给与面板里各卷图标几乎一样，
# 用图标模板只定位候选格，卷号靠悬停读物品名提示（「天书残卷 第三卷」）
# 区分（实机提示名 OCR 1.00）。
GIVE_ITEM_ICON = item_catalog.icon("tianshu")
GIVE_ITEM_KEYWORD = "天书残卷"
# 面板打开期间每 5s 最多尝试一次（失败/面板未关时不空烧悬停 OCR）
GIVE_RETRY_INTERVAL = 5.0
_LAST_GIVE_TRY = 0.0

def parse_required_volumes(text):
    """从任务文案解析要求的天书卷号集合（如「天书残卷三或十」→ {3,10}）。

    卷号正则已下沉 device/task_info（结构化解析共用同一份）。
    """
    return task_info.parse_volumes(text)


def volume_of(name):
    """物品名 → 卷号（「天书残卷第三卷」→ 3；无卷号 None）。"""
    return task_info.volume_of(name)


# 跨行换行时「交给」会落在行首（实机「…并将其」/「交给 彩翼仙子」，
# 2026-11-27 急人所急），逐行解析也要能取到行首「交给」后的名字
_GIVE_TARGET_RE = re.compile(
    r"(?:将其交给|给予|^\s*交给)\s*([^\s，,。；;：:]+)")


def parse_give_target(text):
    """从任务文案解析给与目标 NPC。

    实机文案：旧「…并将其交给黑熊精」、新「…并给予梅花仙」（2026-11-27
    炼妖石环）、换行断成行首的「交给 彩翼仙子」→「黑熊精」/「梅花仙」/
    「彩翼仙子」。
    """
    m = _GIVE_TARGET_RE.search(text or "")
    return m.group(1).strip(" 　。！!；;，,") if m else ""


def parse_give_target_from_items(items):
    """追踪面板逐行解析给与目标；行尾截断时返回空串。

    不能把整块 OCR 文本拼接后再解析：追踪区下方会混入世界玩家名浮标
    （实机「魔吒」「得过日过」），拼接后「并将其交给」会把浮名当成
    目标，误触发给与面板 NPC 不匹配拦截（2026-11-27 急人所急实机：
    追踪第三行被截断为「…并将其交给」，真实目标是「乌鸡国御林军」，
    拼接解析却得到「魔吒得过日过」）。逐行解析时截断行行尾没有名字
    → 空串 → 跳过交叉核对，以给与面板「NPC：」行为准（面板由任务
    对话打开，本身可信）。
    """
    for txt, rect, score in items or []:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        target = parse_give_target(txt)
        if target:
            return target
    return ""


# ---- 给与环追踪链接定位（2026-11-28 用户实机）----
# 追踪文案「快去找到一个属性13的炼妖石(5点雅誉)，并将其交给灵儿」：
# 物品名与目标 NPC 名都是亮绿文字，但只有目标名带下划线、是自动寻路
# 链接（物品名无下划线，点了不寻路）。换行把「灵儿」拆成行尾「灵」+
# 下一行「儿」，单字下划线段仅 15px，低于 has_underline 的 20px 实线
# 阈值：行级选择按亮绿占比选中物品行（实测 9.97% vs 8.67%），点击落在
# 物品链接上人物不起步，寻路停滞看门狗每 15s 补点同一位置，原地站了
# 2 分钟（2026-11-28 00:27:54~00:28:45 日志，用户反馈「没有开始寻路」）。
# green_picker 按「给与子句」定位目标链接：交给/给予之后的文字才是导航
# 链接，点子句所在行最右侧绿块（名字跨行只剩首字时点首字同样可导航，
# 用户确认「可以点灵 也可以点儿」）；子句行以关键词结尾（名字整段换行
# 到下一行）时点下一行。非给与环、找不到子句、目标行无亮绿时返回 None，
# 走原行级选择（其余环行为不变）。
_GIVE_LINK_CLAUSE_RE = re.compile(r"(?:将其交给|给予|给与|交给)")
_GIVE_LINK_MAX_DY = 150  # 追踪内容纵向范围（同 track_task 内容采集）


def _give_link_picker(items, img_np):
    """给与环追踪链接精确定位（follow_task 的 green_picker）。

    几何约束同 track_task 内容采集：面板左列、锚点下方 _GIVE_LINK_MAX_DY
    内、至下一标题行为止；左列外行（任务变化浮字/对话框行）不参与并
    抹黑像素（防绿色残留污染定位，2026-11-08 玄枢轶闻 / 2026-11-19
    五环同款教训）。目标行最右侧绿块由 green_text_center 定位（下划线
    ≥20px 时优先下划线；单字短下划线退回绿块底部）。
    """
    region = task_tracker.track_content_region(img_np)
    base = region[0] if region is not None else None
    anchor_hi = (base + task_tracker.ANCHOR_MAX_X
                 if base is not None else task_tracker.PANEL_MAX_X)
    anchor = None
    for txt, rect, score in items or []:
        if score >= 0.55 and rect[0] < anchor_hi \
                and task_tracker._is_title_line(txt) and TASK_TITLE in txt:
            anchor = rect
            break
    if anchor is None:
        return None
    if base is None:
        base = anchor[0]
    col_lo, col_hi = base - 10, base + task_tracker.ANCHOR_MAX_X
    rows = []
    dropped = []  # 左列外行（浮字提示/对话框行）：像素帧抹黑防绿色污染
    # 按纵向顺序采集：遇到下一个任务标题行即结束（增强 OCR 结果不保证
    # 有序，此处显式排序）
    for txt, rect, score in sorted(items or [], key=lambda it: it[1][1]):
        if score < 0.55:
            continue
        if not (col_lo <= rect[0] < col_hi):
            dropped.append(rect)
            continue
        if rect[1] < anchor[3] - 4:
            continue  # 锚点行及其上方（面板表头等）
        if rect[1] - anchor[3] > _GIVE_LINK_MAX_DY:
            continue
        if task_tracker._is_title_line(txt):
            break  # 下一个任务块开始：内容采集结束
        rows.append((list(rect), txt))
    if not rows:
        return None
    if dropped:
        img_np = img_np.copy()
        h, w = img_np.shape[:2]
        for l0, t0, r0, b0 in dropped:
            l0, t0 = max(0, l0), max(0, t0)
            r0, b0 = min(w, r0), min(h, b0)
            if r0 > l0 and b0 > t0:
                img_np[t0:b0, l0:r0] = 0
    # 同一视觉行的碎片按 x 合并，文本按阅读顺序拼接后定位给与子句
    lines = task_tracker.merge_same_line(rows)
    full = "".join(t for _mrect, members in lines for _r, t in members)
    m = _GIVE_LINK_CLAUSE_RE.search(full)
    if m is None:
        return None
    pos = m.end()
    line_i = None
    after = ""
    line_start = 0
    for i, (_mrect, members) in enumerate(lines):
        line_text = "".join(t for _r, t in members)
        if pos <= line_start + len(line_text):
            line_i = i
            after = full[pos:line_start + len(line_text)]
            break
        line_start += len(line_text)
    if line_i is None:
        return None
    if after.strip(" 　，,。；;：:！!、"):
        target_rect = lines[line_i][0]
    elif line_i + 1 < len(lines):
        target_rect = lines[line_i + 1][0]
    else:
        return None
    if task_tracker.bright_green_ratio(img_np, target_rect) <= 0:
        return None
    return task_tracker.green_text_center(img_np, target_rect)


# 追踪文案「快去找到一个XXXX（N点雅），并将其交给YY」：XXXX 是物品名
# （可带「一个」前缀与品质括号；天书残卷后接卷号），YY 是给与目标。
_GIVE_ITEM_RE = re.compile(r"找到\s*(?:一个)?\s*([^，,（(]+)")


def parse_required_item(text):
    """从任务文案解析给与物品名（去「一个」前缀/品质括号/标点）。

    例：「快去找到一个猕猴桃(4点雅)，并将其交给黑熊精」→「猕猴桃」；
        「…天书残卷三或十（3点雅誉）…」→「天书残卷三或十」。
    """
    m = _GIVE_ITEM_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip("。！!；;：: 、\"'“”")


def _task_text_from_items(items):
    """追踪面板（x<PANEL_MAX_X）的 OCR 文本拼接（任务名+目标行）。"""
    texts = [t for t, r, s in (items or [])
             if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X]
    return "".join(texts)


# ---- 庭院打造类给与（用户 2026-11-27）：可打造物品先去聚灵台 ----
# 实机两种文案：
# - 「急人所急…找到一个灵宝天威印(7点雅誉)，并将其交给莽汉」；
# - 「急人所急…找到一个属性13的忆梦符(9点雅誉)，并将其交给饲马小仙」
#   （2026-11-27 实机，物品目录 yimengfu 已标 craftable）。
# 触发：物品名以「灵宝」开头（灵宝类装备都在聚灵台打造，未登记物品也
# 兜底）或物品目录登记了 craftable（device/items.py 的 ItemDef）。
# 出发给与 NPC 前先走庭院打造流程（长安管家 → 庭院聚灵台 → 点光圈物品
# → 打造 → 确认 → 进度标志消失）；打造完成后 follow 再点追踪链接去给与。
# 触发放在 follow 的 pre_click_handler（点任务绿字之前）：同环中途任务
# 文案变化时也能先打造，而不是先把人物自动寻路到给与 NPC（2026-11-27
# 实机日志：追踪「灵宝天威印」后连续 7 轮只点链接空跑）。
COURTYARD_CRAFT_ITEM_PREFIX = "灵宝"
COURTYARD_CRAFT_ATTEMPTS = 2      # 单次触发内的重试次数
COURTYARD_CRAFT_RETRY_GAP = 3.0   # 两次尝试之间等待（秒）
# 同一物品+给与目标只打造一次；任务文案切走时复位（下一环同名物品
# 会重新打造——上一件已交给 NPC，游戏同样要求再打造）。
_CRAFT_TASK_STATE = {"key": None, "done": False, "fails": 0}


def _courtyard_craft_item_name(item, text):
    """给与物品名 → 打造判定用名（剥离「属性N的」前缀）。

    「属性13的忆梦符」→ 属性解析的类别「忆梦符」；其它写法原样返回。
    """
    _attr, cat = parse_required_attr_item(text)
    if cat and cat in (item or ""):
        return cat
    return item


def _is_courtyard_craftable(item, text):
    """物品是否可在聚灵台打造：灵宝前缀兜底 + 物品目录 craftable 标记。"""
    if (item or "").startswith(COURTYARD_CRAFT_ITEM_PREFIX):
        return True
    entry = item_catalog.find_by_name(_courtyard_craft_item_name(item, text))
    return bool(entry is not None and entry.craftable)


def _courtyard_craft_key(items):
    """追踪文本 → (任务键, 原始物品名, 给与目标)；非可打造给与返回 None。"""
    text = _task_text_from_items(items)
    item = parse_required_item(text)
    # 逐行解析优先（防世界浮名混入）；末行截断时用整段解析兜底——触发
    # 判定只需要确认「是给与环」，真正给与仍走逐行解析/NPC 面板核对。
    target = parse_give_target_from_items(items) or parse_give_target(text)
    if not target or not _is_courtyard_craftable(item, text):
        return None, "", ""
    # 键用剥离属性前缀的名称：OCR 偶尔读到/漏读「属性N的」时不会重复打造
    key = (norm_text(_courtyard_craft_item_name(item, text)),
           norm_text(target))
    return key, item, target


def _courtyard_craft_guard(win, items, img_np):
    """follow.pre_click_handler：灵宝给与环先到庭院聚灵台打造。

    非灵宝给与环直接复位状态返回（零开销）；同一任务键成功后不再重复
    打造（游戏侧也会提示「已经打造过需要的任务品了」）。失败重试
    COURTYARD_CRAFT_ATTEMPTS 次，连续 3 次失败升级 ERROR（用户规则）。
    """
    key, item, target = _courtyard_craft_key(items)
    if key is None:
        _CRAFT_TASK_STATE.update(key=None, done=False, fails=0)
        return
    if _CRAFT_TASK_STATE["key"] == key and _CRAFT_TASK_STATE["done"]:
        return
    log(f"{TAG}任务需要灵宝物品「{item}」（交给{target}），"
        f"先到庭院聚灵台打造", "SYS")
    ok, info = False, ""
    for attempt in range(1, COURTYARD_CRAFT_ATTEMPTS + 1):
        ok, info = courtyard_craft.craft_item(win, item, prefix=TAG)
        if ok:
            break
        if attempt < COURTYARD_CRAFT_ATTEMPTS:
            log(f"{TAG}庭院打造第 {attempt} 次失败：{info}，"
                f"{COURTYARD_CRAFT_RETRY_GAP:.0f}s 后重试", "WARN")
            time.sleep(COURTYARD_CRAFT_RETRY_GAP)
    if ok:
        _CRAFT_TASK_STATE.update(key=key, done=True, fails=0)
        log(f"{TAG}庭院打造完成：{info}", "SYS")
        return
    _CRAFT_TASK_STATE.update(key=key, done=False)
    _CRAFT_TASK_STATE["fails"] += 1
    level = "ERROR" if _CRAFT_TASK_STATE["fails"] >= 3 else "WARN"
    log(f"{TAG}庭院打造失败（连续 {_CRAFT_TASK_STATE['fails']} 次）：{info}",
        level)


# ---- 类别型给与：属性炼妖石（用户 2026-11-27）----
# 实机两种任务目的：
# - 定值「找到属性13的炼妖石(获得5点雅誉)，并给予梅花仙」；
# - 区间「快去找到一个9-12属性的炼妖石(2点雅誉)，并将其交给乌鸡国御林军」
#   （2026-11-27 可乐可口实机）。
# 文案只写类别，背包里任意一件属性达标（定值相等 / 落在区间内）的该类
# 物品都可交（用户说明「可以在很多个候选里选任意一个」）。候选来自物品
# 目录里登记了 category/attr_value 的物品，其图标模板自带「+N」角标；
# 但同款不同属性（+8/+9）图标模板会互相命中（实测 0.925/0.98），最终
# 属性由给与面板悬停提示框的属性行复核（give_flow.pick_and_give_attr）。
_ATTR_ITEM_RE = re.compile(r"属性\s*(\d+)\s*的\s*([^\s，,（(；;。]+)")
# 区间语序两种都支持：数字区间在「属性」前（9-12属性的炼妖石，实机）与
# 在「属性」后（属性9-12的炼妖石）
_ATTR_RANGE_RE = re.compile(
    r"(?:(\d+)\s*[-~～至到—–]\s*(\d+)\s*属性"
    r"|属性\s*(\d+)\s*[-~～至到—–]\s*(\d+))"
    r"\s*的?\s*([^\s，,（(；;。]+)")


def _normalize_attr_category(cat):
    """属性类给与的类别名归一：OCR 变体（炼女/炼妖右等）并到「炼妖石」。"""
    cat = (cat or "").strip()
    if cat and cat.startswith("炼") and len(cat) <= 3:
        return "炼妖石"
    return cat


def parse_required_attr_item(text):
    """「找到属性13的炼妖石」→ (13, "炼妖石")；非属性类文案 → (None, "")。"""
    m = _ATTR_ITEM_RE.search(text or "")
    if not m:
        return None, ""
    return int(m.group(1)), _normalize_attr_category(m.group(2))


def parse_required_attr_range(text):
    """「找到一个9-12属性的炼妖石」→ (9, 12, "炼妖石")；非区间文案 → None。"""
    m = _ATTR_RANGE_RE.search(text or "")
    if not m:
        return None
    lo = m.group(1) or m.group(3)
    hi = m.group(2) or m.group(4)
    return int(lo), int(hi), _normalize_attr_category(m.group(5))


# ---- 等级类别型给与：宝宝装备（用户 2026-11-27）----
# 追踪实机「·急人所急[江湖历练任务] 快去找到一个1级或2级宝宝装备
# (4点雅誉)，并将其交给村长」：文案只写类别与等级范围，背包里任意一件
# 等级达标的宝宝装备都可交（用户提供庄子/粗布衣/木筝三个模板，实机
# 悬停提示框均「等级：1级」）。等级由 give_flow.pick_and_give_level 读
# 提示框「等级：N级」行复核；候选来自 items.py 登记 category="宝宝装备"、
# attr_value=等级的物品。
_LEVEL_ITEM_RE = re.compile(
    r"(\d+)\s*"
    r"(?:级\s*(?:[-~～至到—–、,，或和及]\s*(\d+)\s*级)?"
    r"|[-~～至到—–]\s*(\d+)\s*级)"
    r"\s*的?\s*([^\s，,（(；;。]+)")


def _normalize_level_category(cat):
    """等级类给与的类别名归一（OCR「宝宝装各」→ 宝宝装备）。"""
    cat = (cat or "").strip()
    if "宝宝装" in cat:
        return "宝宝装备"
    return cat


def parse_required_level_item(text):
    """「找到一个1级或2级宝宝装备」→ (1, 2, "宝宝装备")；非等级类 → None。

    支持「1级或2级」「1-2级」「1级、2级」「1级」等写法；单级文案区间
    退化为 (N, N)。类别只作区间候选过滤，未登记类别自然无候选。
    """
    m = _LEVEL_ITEM_RE.search(text or "")
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2) or m.group(3) or lo)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi, _normalize_level_category(m.group(4))


# ---- 答题类环：千古一题（2026-11-27 用户实机）----
# 流程：追踪「千古一题[江湖历练任务]」→ 点追踪链接走到赵大 → 开场白
# 「…回答我的千古三题」+ 三个绿字选项（首项「这人到底靠谱不……也罢
# 横竖一试」开始答题）→ 三题诗句问答：一条白色诗句行（给出上一句）+
# 四个绿色诗句选项（下一句候选）→ 按诗句库（core/poem_bank.py，跨任务
# 复用）点正确的下一句；三题后赵大长对话（无选项）由剧情兜底点屏推进，
# 任务推进到下一环（挖宝）。未收录的诗句（含 OCR 严重误读）按用户规则
# 随机点一个绿色选项。
QUIZ_INTRO_TEXT = "回答我的千古三题"
QUIZ_START_OPTION = "这人到底靠谱不"
# 答题文本紧裁剪区（2026-11-27 实机标定）：生产 DIALOG_REGION 大裁剪
# 对 4 行选项的行检测会合并/漏检（同帧实测只检出 2 行），紧裁剪
# (420,360,760,500) 同帧 4 行全检出（含正确答案 0.997）——答题链路是
# 低频路径，多一次区域 OCR（~0.1-0.2s）换正确率。
QUIZ_OCR_REGION = (420, 360, 760, 500)
# 备用裁剪：主裁剪行检测偶发把相邻选项合并/漏检时换一档再试一次
# （2026-11-27 离线实测同一帧两档裁剪检出的行集合互补；仅在主裁剪
# 已见绿字选项迹象时启用，无选项的剧情长对话不付第二次 OCR）
QUIZ_OCR_FALLBACK = (425, 375, 640, 490)
# 问题/选项行左缘带上界：标准对话框文案列左缘实测 x=433~448；右侧
# 世界玩家名字浮标实测 x≥544，用带上界排除（防随机兜底点到玩家名）
QUIZ_TEXT_X_MAX = 520
QUIZ_OPTION_DX = 40          # 选项与问题行左缘的水平容差
QUIZ_OPTION_MAX_DY = 200     # 选项须在问题行下方该距离内
QUIZ_QUESTION_MIN_LEN = 3    # 诗句问题行长度范围（norm_text 后）
QUIZ_QUESTION_MAX_LEN = 12


def _quiz_question_row(qitems, img_np):
    """答题帧里定位诗句问题行（白字、绿字选项上方最近的短行）。"""
    green_tops = [r[1] for _t, r, _s in qitems
                  if task_tracker.is_green_text(img_np, r)]
    if not green_tops:
        return None
    top = min(green_tops)
    best = None
    for txt, rect, score in qitems:
        if score < 0.55 or task_tracker.is_green_text(img_np, rect):
            continue
        if not (task_tracker.PANEL_MAX_X <= rect[0] <= QUIZ_TEXT_X_MAX):
            continue
        n = norm_text(txt)
        if not (QUIZ_QUESTION_MIN_LEN <= len(n) <= QUIZ_QUESTION_MAX_LEN):
            continue
        # 须紧邻最上方绿字选项：覆盖 OCR 框重叠（实测问题框底 400、
        # 首选项框顶 400），同时排除对话框上方的 NPC 名/世界浮名
        if rect[3] > top + 8 or top - rect[3] > 40:
            continue
        if best is None or rect[3] > best[1][3]:
            best = (txt, rect)
    return best


def _quiz_option_rows(qitems, img_np, qrect):
    """问题行下方的绿字选项行（左缘对齐、限定在对话框文案列）。"""
    rows = []
    for txt, rect, score in qitems:
        if score < 0.55 or not task_tracker.is_green_text(img_np, rect):
            continue
        if not (task_tracker.PANEL_MAX_X <= rect[0] <= QUIZ_TEXT_X_MAX):
            continue
        if abs(rect[0] - qrect[0]) > QUIZ_OPTION_DX:
            continue
        if rect[1] < qrect[1] or rect[1] - qrect[3] > QUIZ_OPTION_MAX_DY:
            continue
        rows.append((txt, rect, score))
    return rows


def _quiz_option_band(qitems, img_np):
    """帧内是否有对话框文案列的绿字行（判断要不要换裁剪重试）。"""
    return any(it[2] >= 0.55 and task_tracker.is_green_text(img_np, it[1])
               and task_tracker.PANEL_MAX_X <= it[1][0] <= QUIZ_TEXT_X_MAX
               for it in qitems)


def _quiz_tick(win, items, img_np):
    """千古一题处理：开场点开始选项；诗句题点下一句；未收录随机点。"""
    # 环判定用「千古」宽松匹配（标题「千古一题」+ 目标行「千古谜题」都含，
    # OCR 误读一个字也不至于让整环卡死在答题对话上；其他环无此词）
    if "千古" not in _task_text_from_items(items):
        return False
    dlg = [it for it in (items or [])
           if it[2] >= 0.55 and it[1][0] >= task_tracker.PANEL_MAX_X]
    if not dlg:
        return False
    # 开场白 → 点「这人到底靠谱不……也罢横竖一试」开始答题
    if _dialog_text_present(dlg, QUIZ_INTRO_TEXT):
        ok = click_green_option_safe(win, items, img_np, QUIZ_START_OPTION,
                                     prefix=TAG)
        if ok:
            log(f"{TAG}答题（千古一题）：已点击开始答题选项", "SYS")
        return ok
    # 诗句题：标准对话框在场才做紧裁剪 OCR（行走/空场景零额外开销）
    if img_np is None \
            or dialog_frame_score(img_np) < DIALOG_FRAME_THRESHOLD:
        return False
    qitems, _ = game_map.ocr_region(img_np, QUIZ_OCR_REGION)
    qrow = _quiz_question_row(qitems, img_np)
    opts = _quiz_option_rows(qitems, img_np, qrow[1]) if qrow else []
    if len(opts) < 2:
        # 主裁剪行检测偶发合并/漏检：有所见（问题行或绿字选项）才换一档
        # 裁剪再试；无选项的剧情长对话（三题后的赵大长对话）不做第二次 OCR
        if qrow is None and not _quiz_option_band(qitems, img_np):
            return False
        qitems, _ = game_map.ocr_region(img_np, QUIZ_OCR_FALLBACK)
        qrow = _quiz_question_row(qitems, img_np)
        if qrow is None:
            return False
        opts = _quiz_option_rows(qitems, img_np, qrow[1])
    if len(opts) < 2:
        return False
    qtxt, qrect = qrow
    ans = poem_bank.answer_for(qtxt)
    if ans is not None:
        ok = click_green_option_safe(win, opts, img_np, ans, prefix=TAG)
        if ok:
            log(f"{TAG}答题（千古一题）：{qtxt} → {ans}", "SYS")
        return ok
    txt = random.choice(opts)[0]
    log(f"{TAG}答题（千古一题）：诗句「{qtxt}」未收录，随机点选项「{txt}」",
        "WARN")
    return _click_green_option_once(win, opts, img_np, txt, prefix=TAG)


# ---- 捕捉类环：贵人相助（用户 2026-11-27 实机）----
# 追踪「到五指山抓回出逃的召唤兽宝宝（猴宝宝、蟾宫兔宝宝或者赭炎宝宝）
# 并将其带回给主人」：点绿色链接自动寻路到目标地图（实机五指山 292,91
# 附近）→ 到达后降落 → 右键点地面走动直到暗雷遭遇 → 战斗由注入的捕捉
# 计划执行（battle.set_battle_plan：第 1 回合「捕捉」点选宝宝、召唤兽
# 「防御」，第 2 回合人物「逃跑」+ 召唤兽「防御」；捕捉一次必定成功）→
# 退出战斗后追踪变为「找到<NPC>交还召唤兽宝宝。」（实机女力士/张小虎），
# 交还由 follow 正常链路完成。
# 到达/降落/走位流程与名望「蛟魔复仇」暗雷链路同款（等寻路启动→目标
# 地图内位置稳定→降落；站定才点世界、右键点地面、走动中不点），按任务
# 模块隔离规则在本模块独立实现，不跨任务引用。
#
# 触发词只取追踪行行首稳定段（2026-11-27 实机教训）：追踪面板半透明，
# 行尾文字叠在移动/明亮背景上 OCR 会整段丢失——「到五指山抓回出逃的
# 召唤兽宝」实测被读成「到五指山抓回出逃的召唤」（19:12:03 补点链接
# 日志），旧触发词含行尾「兽」字，连续 3 轮缺失即误判环结束：战斗计划
# 被清、状态机复位且不再重新武装，人物到达五指山后既不降落也不走位
# （19:11:50 实机，就是用户报告的「没有下飞机」帧）。行首「抓回出逃」
# 在暗色面板左侧，稳定可读；目标地图仍由 CAPTURE_MAP_RE 独立解析。
CAPTURE_RING_KEYWORD = "抓回出逃"
CAPTURE_MAP_RE = re.compile(r"到([一-鿿]{2,8}?)抓回")
CAPTURE_BABY_NAMES = ("猴宝宝", "蟾宫兔宝宝", "赭炎宝宝")
# 触发词连续缺失轮询数 → 清除战斗计划（防战斗帧误清；同暗雷链路 5 轮。
# 空 OCR 帧不计缺失，见 _capture_tick；2.5s 窗口足以盖过加载/遮挡过渡帧）
CAPTURE_ABSENT_RESET = 5
CAPTURE_MOVE_DELTA = 5          # 寻路启动位移判定（像素）
CAPTURE_MOVE_WAIT = 12.0        # 未见位移视为已在目的地的超时（秒）
CAPTURE_ARRIVE_POLLS = 3        # 到达判定：位置连续稳定轮询数
CAPTURE_POS_TOLERANCE = 3       # 到达判定：坐标稳定容差（像素）
CAPTURE_POS_CHECK_INTERVAL = 1.0  # 位置读取限频（秒）
CAPTURE_LAND_RETRY_AFTER = 5.0  # 降落失败重试间隔（秒）
CAPTURE_WALK_CHECK_INTERVAL = 0.5  # 走位检查最小间隔（秒）
CAPTURE_WALK_MIN_INTERVAL = 2.0    # 两次走位点击最小间隔（秒）
# 站定判定（坐标直接信号，2026-11-27 用户实机反馈「走到目标点站住、
# 等了一下才点下一点」）：五指山位置标签背后是动态场景，WalkGuard 像素
# 差分易持续判「移动」，只能走 3s+1.2s 的坐标复核长路径（五庄观同款
# 教训）——每次走位后白站 ~3s（19:23:35/41/47 实测每 6s 一次走位）。
# 走位阶段改用地图坐标：走动时数字持续变化、站定时读数相同。轮询
# 0.5s，连续 CAPTURE_STAND_POLLS 次相同（3 次采样跨度 ≈1s，对齐
# WalkGuard 两次复核 1.2s 的标定）即判站定；坐标不可读时回退像素判定。
CAPTURE_STAND_POLLS = 2
# 走位点四点轮换（窗口坐标，同暗雷链路的安全世界区四角，避开追踪面板/
# 底部快捷栏/右侧血蓝）：对角穿越屏幕，单次走位距离最大
CAPTURE_WALK_POINTS = ((430, 660), (950, 230), (950, 660), (430, 230))

_CAP_STATE = {"armed": False, "phase": 0, "target": None, "missing": 0,
              "pos_at": 0.0, "land_at": 0.0, "walk_from": None,
              "move_seen": False, "walk_at": 0.0, "stable": 0,
              "last_pos": None, "walk_click_at": 0.0, "walk_idx": 0,
              "stand_pos": None, "stand_hits": 0, "first_walk": False}
_CAP_FRAME = {"key": None, "img": None, "pos": None}
_UNSET = object()
_CAP_WALK_GUARD = WalkGuard()


def parse_capture_map(text):
    """捕捉环文案 → 目标地图（「到五指山抓回出逃的召唤兽宝宝」→ 五指山）。"""
    m = CAPTURE_MAP_RE.search(text or "")
    return m.group(1) if m else ""


def capture_map_match(current, target):
    """当前位置地名与捕捉目标地图是否一致（等长 ≥3 字容 1 字 OCR 误读）。"""
    if not current or not target:
        return False
    if current == target:
        return True
    if len(current) == len(target) >= 3:
        return sum(a != b for a, b in zip(current, target)) <= 1
    return False


def _cap_shared_img(win, items):
    """extras 链共享帧（按 id(items) 缓存，每轮询只截一帧）。"""
    key = id(items)
    if _CAP_FRAME["key"] != key:
        _CAP_FRAME.update(key=key, img=win.capture(force_activate=False),
                          pos=_UNSET)
    return _CAP_FRAME["img"]


def _cap_pos(win, items):
    """共享帧上的地图位置读取（结果按帧缓存，None 也缓存）。"""
    img = _cap_shared_img(win, items)
    if _CAP_FRAME["pos"] is _UNSET:
        _CAP_FRAME["pos"] = (None if img is None
                             else game_map.read_map_position(win, img=img))
    return _CAP_FRAME["pos"]


def _capture_reset():
    """捕捉环状态复位（环结束/新环开始时调用；战斗计划由调用方清除）。"""
    _CAP_STATE.update(armed=False, phase=0, target=None, missing=0,
                      pos_at=0.0, land_at=0.0, walk_from=None,
                      move_seen=False, walk_at=0.0, stable=0,
                      last_pos=None, walk_click_at=0.0, walk_idx=0,
                      stand_pos=None, stand_hits=0, first_walk=False)
    _CAP_FRAME.update(key=None, img=None, pos=_UNSET)
    _CAP_WALK_GUARD.reset()


def _capture_step_arrival(win, items, now):
    """phase 0：等寻路启动 → 目标地图内位置稳定 → 降落（同暗雷链路）。"""
    st = _CAP_STATE
    pos = _cap_pos(win, items)
    if pos is None:
        return False  # 位置不可读（加载/飞行过渡帧）：下轮再查
    if not st["move_seen"]:
        if st["walk_from"] is None:
            st["walk_from"] = pos
            st["walk_at"] = now
        moved = (pos[0] != st["walk_from"][0]
                 or abs(pos[1] - st["walk_from"][1]) > CAPTURE_MOVE_DELTA
                 or abs(pos[2] - st["walk_from"][2]) > CAPTURE_MOVE_DELTA)
        if moved:
            st["move_seen"] = True
            log(f"{TAG}捕捉环：寻路已启动，等待到达{st['target']}后降落", "INFO")
        elif now - st["walk_at"] >= CAPTURE_MOVE_WAIT:
            st["move_seen"] = True
            # 12s 无位移 = 已连续站定：直接视为到达稳定（同暗雷链路）
            st["last_pos"] = pos
            st["stable"] = CAPTURE_ARRIVE_POLLS
            log(f"{TAG}捕捉环：未见位移（可能已在{st['target']}），按到达处理",
                "INFO")
        else:
            return False
    pos = _cap_pos(win, items)
    if pos is None:
        return False
    if not capture_map_match(pos[0], st["target"]):
        st["stable"] = 0  # 仍在其它地图寻路中：稳定计数清零
        st["last_pos"] = pos
        return False
    last = st["last_pos"]
    if (last is not None and pos[0] == last[0]
            and abs(pos[1] - last[1]) <= CAPTURE_POS_TOLERANCE
            and abs(pos[2] - last[2]) <= CAPTURE_POS_TOLERANCE):
        st["stable"] += 1
    else:
        st["stable"] = 0
    st["last_pos"] = pos
    if st["stable"] < CAPTURE_ARRIVE_POLLS:
        return False  # 到达但仍在移动：等停下
    if now - st["land_at"] < CAPTURE_LAND_RETRY_AFTER:
        return False  # 降落失败后的重试间隔未到
    st["land_at"] = now
    log(f"{TAG}捕捉环：已到达{st['target']}，执行降落", "SYS")
    ok, info = zuoqi.ensure_landed(win)
    _closed, cinfo = zuoqi.close_feixingyuqi_panel(win)  # 降落后面板保持打开
    log(f"{TAG}捕捉环：降落{'成功' if ok else '失败'}：{info}；{cinfo}",
        "SYS" if ok else "WARN")
    if not ok:
        return False  # 战斗中/面板瞬态失败：按间隔重试
    st["phase"] = 1
    # 降落完成即站定（用户 2026-11-27）：降落不会遇敌，第一次走位不需要
    # 等坐标复核（实测降落成功 21:40:01 → 首次右键 21:40:05 白等 4s）；
    # 后续走位仍按坐标站定判定，防走动中点击取消寻路。
    st["first_walk"] = True
    st["walk_click_at"] = 0.0
    st["stand_pos"] = None
    st["stand_hits"] = 0
    _CAP_WALK_GUARD.reset()  # 进入走位阶段：像素兜底基线同步清零
    return True


def _capture_stand_update(pos):
    """坐标站定判定（纯状态更新）：连续 CAPTURE_STAND_POLLS 次读数相同。

    返回 True=站定 / False=移动中 / None=坐标不可读（调用方回退像素判定）。
    走动时地图坐标数字持续变化，站定时两次读数完全相同——比位置标签像素
    差分更直接（半透明标签背后是动态场景，像素差分易持续超阈值）。
    """
    st = _CAP_STATE
    if pos is None:
        return None
    if st["stand_pos"] is not None and pos == st["stand_pos"]:
        st["stand_hits"] += 1
    else:
        st["stand_pos"] = pos
        st["stand_hits"] = 0
    return st["stand_hits"] >= CAPTURE_STAND_POLLS


def _capture_step_walk(win, items, now):
    """phase 1：已到达并降落，站定即右键点地面走动催发遭遇（同暗雷链路）。

    站定用地图坐标直接信号（见 CAPTURE_STAND_POLLS；坐标不可读时回退
    WalkGuard 像素判定），站定即点下一个走位点，人物接近持续走动。
    降落完成后的第一次走位不等站定（first_walk，用户 2026-11-27：降落
    不会遇敌，直接走）；后续走位仍须站定，走动中一律不点世界（规则 13：
    世界点击会打断自动寻路）；标准对话框在场不点（交给对话处理链）；
    两次点击间隔防起步前连点。
    """
    img = _cap_shared_img(win, items)
    if img is None:
        return False
    if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
        return False  # 标准对话框在场：交给对话处理链
    first_walk = _CAP_STATE.get("first_walk", False)
    _CAP_STATE["first_walk"] = False
    if first_walk:
        # 降落完成：人物已站立，且降落本身不会遇敌——不付坐标复核等待，
        # 直接点第一个走位点（用户 2026-11-27）
        stand = True
    else:
        stand = _capture_stand_update(_cap_pos(win, items))
        if stand is None:
            if _CAP_WALK_GUARD.walking(win, img=img) is not False:
                return False  # 坐标不可读：像素判定走动中/无法判断，不点世界
        elif not stand:
            return False  # 坐标仍在变化：走动中，不点世界
    if now - _CAP_STATE["walk_click_at"] < CAPTURE_WALK_MIN_INTERVAL:
        return False
    _CAP_STATE["walk_click_at"] = now
    _CAP_STATE.update(stand_pos=None, stand_hits=0)  # 点击后重新累计站定
    _CAP_WALK_GUARD.reset()  # 像素兜底基线同步重置
    x, y = CAPTURE_WALK_POINTS[_CAP_STATE["walk_idx"] % len(CAPTURE_WALK_POINTS)]
    _CAP_STATE["walk_idx"] += 1
    app_utils.human_click(win, x, y, button="secondary")
    log(f"{TAG}捕捉环：{'降落完成，' if first_walk else '站定未遇敌，'}"
        f"已右键点地面 ({x},{y}) 走动", "SYS")
    return True


def _capture_tick(win, items):
    """捕捉类环状态机（follow extras 通道）：到达→降落→走位遇敌 + 战斗计划注入。

    返回是否已处理（执行了降落/走位动作）——仅动作帧返回 True（follow
    跳过本帧识别），等待帧返回 False 交回正常链路（任务签名变化/对话）。
    战斗中跳过（战斗线程按注入的捕捉计划执行回合动作）；触发词连续
    CAPTURE_ABSENT_RESET 轮缺失（追踪文案变为交还阶段）才清除战斗计划，
    防战斗帧追踪面板不可见时误清。
    """
    if battle_lib.watch_in_battle():
        return False  # 战斗中：保持计划与状态（战斗线程执行捕捉流程）
    text = _task_text_from_items(items)
    if CAPTURE_RING_KEYWORD not in text:
        if not _CAP_STATE["armed"]:
            return False
        if not text:
            return False  # 空 OCR 帧（加载/过渡）：不计缺失
        _CAP_STATE["missing"] += 1
        if _CAP_STATE["missing"] < CAPTURE_ABSENT_RESET:
            return False
        battle_lib.clear_battle_plan()
        _capture_reset()
        log(f"{TAG}捕捉环结束（追踪文案已变化：{text[:24]}），"
            f"已清除捕捉战斗计划", "SYS")
        return False
    _CAP_STATE["missing"] = 0
    if not _CAP_STATE["armed"]:
        target = parse_capture_map(text)
        if not target:
            log(f"{TAG}捕捉环：未解析出目标地图（{text[:40]}）", "WARN")
            return False
        _capture_reset()
        _CAP_STATE["armed"] = True
        _CAP_STATE["target"] = target
        battle_lib.set_battle_plan({"kind": "capture",
                                    "target_names": CAPTURE_BABY_NAMES})
        log(f"{TAG}捕捉环：目标地图{target}，宝宝 "
            f"{'/'.join(CAPTURE_BABY_NAMES)} 任选其一；到达后降落并走位遇敌"
            f"（已注入捕捉战斗计划：捕捉→召唤兽防御→逃跑）", "SYS")
    now = time.time()
    if _CAP_STATE["phase"] == 1:
        if now - _CAP_STATE["pos_at"] < CAPTURE_WALK_CHECK_INTERVAL:
            return False
        _CAP_STATE["pos_at"] = now
        return _capture_step_walk(win, items, now)
    if now - _CAP_STATE["pos_at"] < CAPTURE_POS_CHECK_INTERVAL:
        return False  # 位置 OCR 限频（轮询热路径）
    _CAP_STATE["pos_at"] = now
    return _capture_step_arrival(win, items, now)


def _registered_entries_in_text(text):
    """任务文案中出现的已登记物品（按出现位置排序，同 key 去重）。

    名称与 aliases 都参与匹配（OCR 变体，如「天外石」= 天外飞石）。
    """
    text = text or ""
    hits, seen = [], set()
    for entry in item_catalog.ITEMS.values():
        names = [entry.name] + [a for a in entry.aliases if a]
        positions = [text.find(n) for n in names if n and n in text]
        if not positions or entry.key in seen:
            continue
        seen.add(entry.key)
        hits.append((min(positions), entry))
    hits.sort(key=lambda h: h[0])
    return [entry for _pos, entry in hits]


def _give_plan(text, phrase, entry):
    """给与候选计划：结构化 offers 按雅誉偏好排序；无结构化信息退回旧顺序。

    多选一文案（2026-11-27 实机）「找到一个千年魂石(40点雅誉)或天书残卷
    一或二(3点雅誉)」：任务接受任一件，按设置排序——高雅誉值优先先试
    千年魂石(40)，低雅誉值优先先试天书残卷(3)。返回 (attempts,
    tianshu_vols)：attempts=[(kind, name, entry)]，kind="registered"/
    "unknown"（未登记物品的 name 作全格悬停 keyword）；tianshu_vols 为
    文案里全部天书卷号（旧面板按卷号 accept）。
    """
    offers = task_info.parse_give_offers(text)
    if offers:
        ordered = task_info.order_give_offers(
            offers, high_first=settings.load_give_honor_high_first())
        attempts, seen, vols = [], set(), set()
        for offer in ordered:
            ent = item_catalog.find_by_name(offer.name)
            if ent is None:
                attempts.append(("unknown", offer.name, None))
                continue
            if ent.key == "tianshu":
                vols |= set(offer.volumes)
            if ent.key in seen:
                continue
            seen.add(ent.key)
            attempts.append(("registered", ent.name, ent))
        return attempts, vols
    attempts = []
    if phrase:
        # 已登记物品用目录规范名做给与面板关键词（任务文案可能是 OCR
        # 变体/别名，如「天外石」→ 天外飞石；用原文会匹配不上悬停名）
        name = entry.name if entry is not None else phrase
        attempts.append(("registered" if entry is not None else "unknown",
                         name, entry))
    for reg in _registered_entries_in_text(text):
        if entry is not None and reg.key == entry.key:
            continue
        if any(a[2] is not None and a[2].key == reg.key for a in attempts):
            continue
        attempts.append(("registered", reg.name, reg))
    return attempts, set()


def _choice_give_candidates(text):
    """双选一面板候选：按雅誉偏好排序的已登记物品 [(icon, name)]。"""
    offers = task_info.parse_give_offers(text)
    if not offers:
        return [(e.icon, e.name) for e in _registered_entries_in_text(text)]
    ordered = task_info.order_give_offers(
        offers, high_first=settings.load_give_honor_high_first())
    out, seen = [], set()
    for offer in ordered:
        ent = item_catalog.find_by_name(offer.name)
        if ent is None or ent.key in seen:
            continue
        seen.add(ent.key)
        out.append((ent.icon, ent.name))
    return out


_LAST_GIVE_STRUCT = {"sig": None}

# 追踪区 2x 放大重识别区域（给与文案漏检兜底）：实机追踪面板标题+内容
# 位于 y204~306，上下留余量；只裁到 y345 比整段 TRACK_REGION 便宜。
# 小块 2x 放大必须走全帧引擎（同修罗任务块重识别；CLAUDE.md 规则：
# 小块 2x 不得替换成 ocr_zoom 快引擎）。
GIVE_TEXT_ZOOM_REGION = (0, 195, 400, 345)


def _give_text_zoom(win):
    """截帧 → 追踪区 2x 放大重识别 → 拼接文本（失败返回空串）。

    2026-11-27 实机：行首绿字「、八或九」被区域 OCR 漏检，「天书残卷
    五、七、八或九」只读出「天书残卷五、七」，四卷里两卷白丢（用户
    报告「天书八 天书九怎么没了」）。2x 放大后重识别补读。
    """
    try:
        img = win.capture(force_activate=False)
    except Exception:
        return ""
    if img is None:
        return ""
    l, t, r, b = GIVE_TEXT_ZOOM_REGION
    crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                         Image.LANCZOS)
    try:
        items, _ = game_map.ocr_image(crop)
    except Exception:
        return ""
    mapped = sorted(
        ((txt, (rect[0] // 2 + l, rect[1] // 2 + t,
                rect[2] // 2 + l, rect[3] // 2 + t), s)
         for txt, rect, s in items),
        key=lambda x: (x[1][1], x[1][0]))
    return _task_text_from_items(mapped)


def _give_text_complete(text):
    """给与文案是否含完整要素（物品雅誉括号 + 给与目标）。"""
    return bool(text) and "点雅" in text and bool(parse_give_target(text))


def _give_text_best(win, text):
    """给与文案取更完整者：原帧文本 vs 追踪区 2x 重识别。

    只在文案缺要素或含天书残卷（卷号列表易丢字）时重识别，其余环零开销；
    两份都可用时取卷号更多者。
    """
    if _give_text_complete(text) and "天书残卷" not in text:
        return text
    alt = _give_text_zoom(win)
    if not alt or alt == text:
        return text
    if len(parse_required_volumes(alt)) > len(parse_required_volumes(text)) \
            or (not _give_text_complete(text)
                and _give_text_complete(alt)):
        log(f"{TAG}给与文案 2x 重识别补全：{alt[:60]}", "INFO")
        return alt
    return text


def _log_give_structure(items, text):
    """把追踪内容结构化后写一条日志（同一任务文案只写一次）。

    例：任务结构：名称「急人所急」所需物品 [千年魂石(40雅誉)、天书残卷
    一(3雅誉)、天书残卷二(3雅誉)]（高雅誉值优先）。物品列表用传入的
    text 解析（可能是 2x 重识别补全后的文案，items 里的原始行可能丢字）。
    """
    if not text or text == _LAST_GIVE_STRUCT["sig"]:
        return
    info = task_info.parse_track_task(items, category=TASK_TITLE)
    if info is None:
        return
    offers = tuple(task_info.parse_give_offers(text)) or info.offers
    if not offers:
        return
    _LAST_GIVE_STRUCT["sig"] = text
    mode = ("高雅誉值优先" if settings.load_give_honor_high_first()
            else "低雅誉值优先")
    log(f"{TAG}任务结构：名称「{info.name}」所需物品 "
        f"[{'、'.join(o.label() for o in offers)}]（{mode}）", "SYS")


def _choice_give_tick(win, items, img_np):
    """双选一给与面板（「请从两种物品任选一种给予」）：选物 + 确定。

    面板物品格只列本次可交物品，按「雅誉优先」设置排序点选（高雅誉值
    优先 = 千年魂石 40 先；低雅誉值优先 = 天书残卷 3 先，见
    settings.load_give_honor_high_first），选中后「待给予物品」框复核，
    再点确定。未点确定不会给出去，点已选中的物品不会取消选择
    （2026-11-27 实机验证），重试安全。
    """
    global _LAST_GIVE_TRY
    now = time.time()
    if now - _LAST_GIVE_TRY < GIVE_RETRY_INTERVAL:
        return True
    _LAST_GIVE_TRY = now
    text = _give_text_best(win, _task_text_from_items(items))
    _log_give_structure(items, text)
    target = parse_give_target_from_items(items)
    npc = give_flow.read_give_npc(
        win, region=give_flow.GIVE_CHOICE_NPC_REGION)
    if not give_flow.npc_names_match(target, npc):
        log(f"{TAG}给与面板 NPC 不匹配：任务目标「{target}」/ 面板「{npc}」，"
            f"暂不给与", "WARN")
        return True
    candidates = _choice_give_candidates(text)
    if not candidates:
        log(f"{TAG}双选一给与面板未解析到可交物品"
            f"（任务文案：{text[:40]}）", "WARN")
        return True
    ok, info = give_flow.pick_choice_and_give(win, candidates, prefix=TAG)
    log(f"{TAG}双选一给与：{'/'.join(name for _icon, name in candidates)}"
        f" → {info}", "SYS" if ok else "WARN")
    return True


def _extra_dialog_handler(win, items, img_np):
    """follow 注入：捕捉环到达/降落/走位 + 给与面板选物 + 答题环问答。

    捕捉类环（贵人相助）先走 _capture_tick（到达→降落→走位，战斗计划
    注入 battle 线程）。给与面板两种形态：旧面板（储物格列 + 给与按钮）
    走图标定位 → 悬停读名 → 给与，多选一文案按出现顺序逐件尝试；新
    「双选一」面板（「请从两种物品任选一种给予」，2026-11-27 实机）走
    _choice_give_tick 点选物品 → 确定。类别型文案（「找到属性13的炼妖
    石」/「找到一个9-12属性的炼妖石」）按目录登记的同类候选逐储物格
    批量定位，属性以提示框属性行复核后给与；等级类别型（「找到1级或
    2级宝宝装备」）同链路复核提示框「等级：N级」行。未登记物品走全格
    悬停兜底（慢路径，不常见）。给与面板不在场时交给答题处理（非对应
    环零开销）。
    """
    global _LAST_GIVE_TRY
    if _capture_tick(win, items):
        return True
    # 双选一面板用对话区 OCR 文本判定：follow 传入的 img_np 是 OCR 掩码帧
    # （只保留追踪区 + 对话区像素），面板说明行顶部在对话区上缘之外被切掉，
    # 模板检测在掩码帧上只有 0.267（2026-11-27 实机：面板在场空转 18s）
    if give_flow.give_choice_panel_in_items(items):
        return _choice_give_tick(win, items, img_np)
    if not give_flow.give_panel_open(img_np):
        return _quiz_tick(win, items, img_np)
    now = time.time()
    if now - _LAST_GIVE_TRY < GIVE_RETRY_INTERVAL:
        return True  # 面板已占用：节流窗口内不再重复 OCR/悬停
    _LAST_GIVE_TRY = now
    text = _give_text_best(win, _task_text_from_items(items))
    _log_give_structure(items, text)
    phrase = parse_required_item(text)
    target = parse_give_target_from_items(items)
    npc = give_flow.read_give_npc(win)
    if not give_flow.npc_names_match(target, npc):
        log(f"{TAG}给与面板 NPC 不匹配：任务目标「{target}」/ 面板「{npc}」，"
            f"暂不给与", "WARN")
        return True
    if target and npc:
        log(f"{TAG}给与面板 NPC 核对：任务「{target}」= 面板「{npc}」", "INFO")
    entry = item_catalog.find_by_name(phrase or text)
    attr_value, attr_category = parse_required_attr_item(text)
    attr_range = parse_required_attr_range(text)
    level_range = parse_required_level_item(text)
    level_category = ""
    if attr_range:
        attr_lo, attr_hi, attr_category = attr_range
        candidates = item_catalog.find_by_category_range(
            attr_category, attr_lo, attr_hi)
    elif attr_category:
        attr_lo = attr_hi = attr_value
        candidates = item_catalog.find_by_category(attr_category, attr_value)
    elif level_range:
        attr_lo, attr_hi, level_category = level_range
        candidates = item_catalog.find_by_category_range(
            level_category, attr_lo, attr_hi)
    else:
        attr_lo = attr_hi = None
        candidates = []
    if candidates:
        # 类别型给与（「属性13的炼妖石」定值 / 「9-12属性的炼妖石」区间，
        # 用户 2026-11-27 实机）：类别下已登记的候选任选其一；图标只定位
        # 候选格，属性以悬停提示框属性行复核（同款 +8/+9 图标互命中）。
        # 等级类别型（「1级或2级宝宝装备」）同链路，复核提示框等级行。
        req = f"{attr_lo}" if attr_lo == attr_hi else f"{attr_lo}-{attr_hi}"
        pairs = [(c.icon, c.name) for c in candidates]
        names = "、".join(c.name for c in candidates)
        if level_range:
            ok, info = give_flow.pick_and_give_level(
                win, pairs, attr_lo, attr_hi, prefix=TAG)
            log(f"{TAG}给与面板：等级类别「{level_category}」{req}级 候选 "
                f"{names} → {info}", "SYS" if ok else "WARN")
        else:
            ok, info = give_flow.pick_and_give_attr(
                win, pairs, attr_lo, attr_hi, prefix=TAG)
            log(f"{TAG}给与面板：类别「{attr_category}」属性{req} 候选 "
                f"{names} → {info}", "SYS" if ok else "WARN")
        return True
    if not phrase and entry is None:
        log(f"{TAG}给与面板已打开但未解析到物品名"
            f"（任务文案：{text[:40]}）", "WARN")
        return True  # 面板已占用：本帧不点追踪链接，下一轮重试
    # 多选一给与（2026-11-27 可乐可口实机）：「找到一个千年魂石(40点雅誉)
    # 或天书残卷一或二(3点雅誉)，并将其交给梅花仙」——任务接受任一件，
    # 按「雅誉优先」设置排序逐件尝试（高雅誉优先：千年魂石 40 → 天书 3；
    # 低雅誉优先反过来），失败再试下一件；未登记物品用全格悬停兜底。
    attempts, tianshu_vols = _give_plan(text, phrase, entry)
    for kind, name, ent in attempts:
        if kind == "registered" and ent.key == "tianshu":
            required = (tianshu_vols or parse_required_volumes(phrase)
                        or parse_required_volumes(text))
            if not required:
                log(f"{TAG}给与面板：天书文案未解析到卷号"
                    f"（{phrase!r}），跳过", "WARN")
                continue
            accept = lambda n, req=required: volume_of(n) in req
            detail = f"天书卷号 {sorted(required)}"
            ok, info = give_flow.pick_and_give(
                win, ent.icon, accept=accept, keyword=ent.name, prefix=TAG)
        elif kind == "registered":
            accept = lambda n, kw=name: kw in (n or "")
            detail = f"目录物品「{name}」（{ent.icon}）"
            ok, info = give_flow.pick_and_give(
                win, ent.icon, accept=accept, keyword=name, prefix=TAG)
        else:
            accept = lambda n, kw=name: kw in (n or "") or (n or "") in kw
            detail = f"未登记物品「{name}」（全格悬停兜底）"
            ok, info = give_flow.pick_and_give_any(
                win, accept=accept, keyword=name, prefix=TAG)
        log(f"{TAG}给与面板：{detail} → {info}", "SYS" if ok else "WARN")
        if ok:
            return True
    return True


# 300环 各环 NPC 对话框的接受/交付选项（实机逐个补）：
# - 灵心巧手（拼图）：宝石小贩修画 →「一切就交给我吧！」（OCR 常漏首字
#   「一」，补短 needle「交给我吧」）；
# - 给与类（用户 2026-11-26）：追踪「快去找到…并将其交给XXX」→ 到达
#   自动弹「少侠有什么事吗？」，选「这是你被抢走的东西」打开给与面板；
#   面板选物/给与由 _extra_dialog_handler 处理。
# - 战斗类（用户 2026-11-26）：追踪「赤焰妖正在长安城中喷火」→ 到达弹
#   「啊啊啊，头好疼，想喷火烧掉长安城，我们快忍不住了！」，选
#   「我来助你平静。」后进入战斗（战斗由战斗线程托管）。
TASK_ACCEPT_OPTIONS = ("一切就交给我吧", "交给我吧",
                       "这是你被抢走的东西",
                       "我来助你平静", "助你平静",
                       # 四锁四色钥匙开锁环（2026-11-27 实机）：阿合奇阿洪
                       # 对话框选项「解开这奇锁！」，点后弹开锁面板
                       "解开这奇锁",
                       # 天下无贼（2026-11-27 实机）：乌鸡国王后对话三选项
                       # 「毛贼！休想逃！(130分以上即可通关)」「找你有其他
                       # 事情」「天下无贼，只是一个梦。」，点第一个进小游戏
                       "拿来让我试试",
                       txwz.ENTRY_OPTION_TEXT)


# ---- 挖宝类环（用户 2026-11-26）----
# 追踪：「到白骨山（237，26）藏宝处」（地图/坐标可变）。点击追踪自动
# 寻路到藏宝处，到达后在背包-任务储物格右键「挖宝锄头」触发挖掘。
# 2026-11-27 实机教训（用户报告「到达藏宝处但一直兜底恢复寻路」）：
# - 「藏宝」是行尾独立绿字链接片段（与坐标之间留有空档），RapidOCR 常
#   只读出「到北俱芦洲（112,35）」而丢掉「藏宝」，旧正则要求「）藏宝处」
#   紧邻 → 永远匹配不到，挖宝环出现后一次都不挖、follow 反复补点链接。
#   判定改为标记词 + 坐标正则：「藏宝/挖宝」仅挖宝环出现（给与阶段文案
#   是「将挖出来的宝藏送给X」，宝藏≠藏宝，不含这两个词）。
# - 右键锄头必须在人物站定后（用户 2026-11-27：到达后降落不是必须的，
#   飞行状态照常可挖）：22:09 寻路途中穿过藏宝点瞬间右键，服务器回
#   「坐标点不对」——按位置显示计人物在点旁，但 4s 后服务器处理时人已
#   走开。位置连续 DIG_STABLE_POLLS 次读取一致才允许使用；见「坐标点
#   不对」解除已用抑制，站定后按 DIG_RETRY_INTERVAL 重试。
DIG_HOE_ICON = item_catalog.icon("wabaochutou")
DIG_ARRIVE_TOLERANCE = 8        # 藏宝处到达容差（寻路停在点旁，±8）
DIG_POS_POLL = 1.0              # 位置 OCR 节流（轮询 0.5s，位置读 ~0.1s）
DIG_STABLE_POLLS = 2            # 站定确认：连续 N 次位置读取一致才右键
DIG_RETRY_INTERVAL = 5.0        # 用锄头失败重试间隔
DIG_USED_RETRY = 15.0           # 用过锄头后任务仍无变化的重新尝试间隔
DIG_FAIL_TEXT = "坐标点不对"     # 服务器拒绝挖宝的对话框文案
DIG_FAIL_ALERT = 3              # 连续 N 次未推进升级一条 ERROR（用户规则）
_DIG_STATE = {"key": None, "used": False, "used_at": 0.0,
              "last_pos_at": 0.0, "last_try": 0.0, "last_pos": None,
              "stable": 0, "tries": 0, "escalated": False}

_DIG_TARGET_RE = re.compile(
    r"到\s*([^（(]+?)\s*[（(]\s*(\d+)\s*[，,]\s*(\d+)\s*[）)]")
_DIG_MARKERS = ("藏宝", "挖宝")


def parse_dig_target(text):
    """解析挖宝任务目标：「到白骨山（237，26）藏宝处」→ (地图, x, y)。

    不要求坐标后紧跟「藏宝处」：该处是独立绿字片段，OCR 常丢字
    （2026-11-27 实机，见上方注释）。
    """
    m = _DIG_TARGET_RE.search(text or "")
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2)), int(m.group(3))


def _is_dig_text(text):
    """追踪文本是否挖宝环（标记词见上方实机教训）。"""
    return any(m in text for m in _DIG_MARKERS)


def _dig_tick(win, items, img):
    """挖宝监控：追踪为挖宝环且到达目标坐标 → 站定 → 右键锄头。

    返回 True 仅在本帧真正执行了动作（右键锄头，帧已过期）时；
    其余帧一律返回 False，交回 follow 正常链路——前往藏宝处的导航
    由 follow 点任务追踪绿字驱动（用户 2026-11-26：藏宝处可以点击
    任务追踪到达目的地）。旧实现非到达帧恒返回 True，wait_handler
    占住内层轮询，follow 永远看不到任务签名变化、不点追踪链接，
    挖宝环出现后人物原地不动（2026-11-27 实机日志）。
    """
    text = _task_text_from_items(items)
    if not _is_dig_text(text):
        if _DIG_STATE.get("key") is not None:
            _DIG_STATE.update(key=None, used=False, last_pos=None,
                              stable=0, tries=0, escalated=False)
        return False
    target = parse_dig_target(text)
    if target is None:
        return False
    tmap, tx, ty = target
    key = (tmap, tx, ty)
    now = time.time()
    if _DIG_STATE.get("key") != key:
        _DIG_STATE.update(key=key, used=False, used_at=0.0,
                          last_pos_at=0.0, last_try=0.0, last_pos=None,
                          stable=0, tries=0, escalated=False)
        log(f"{TAG}挖宝任务：{tmap} ({tx},{ty}) 藏宝处", "SYS")
    # 服务器拒绝（人物未站定/不在藏宝点）：解除已用抑制，站定后重试
    if any(DIG_FAIL_TEXT in t for t, _r, _s in (items or [])):
        if _DIG_STATE.get("used"):
            _DIG_STATE.update(used=False, used_at=0.0)
        _DIG_STATE.update(last_pos=None, stable=0)
        return False
    if _DIG_STATE.get("used"):
        # 已用过锄头：等任务推进（挖到物品/换环）由 follow 正常检测。
        # 长时间无变化（右键可能落在动画/遮挡帧没生效）允许重试一次
        if now - _DIG_STATE.get("used_at", 0.0) < DIG_USED_RETRY:
            return False
        _DIG_STATE.update(used=False, last_try=now)
    if now - _DIG_STATE["last_pos_at"] < DIG_POS_POLL:
        return False
    _DIG_STATE["last_pos_at"] = now
    if img is None:
        return False
    pos = game_map.read_map_position(win, img=img)
    if pos is None or not map_nav.names_match(pos[0], tmap) \
            or abs(pos[1] - tx) > DIG_ARRIVE_TOLERANCE \
            or abs(pos[2] - ty) > DIG_ARRIVE_TOLERANCE:
        _DIG_STATE.update(last_pos=None, stable=0)
        return False
    # 站定确认：连续 DIG_STABLE_POLLS 次位置一致才右键——寻路穿过藏宝
    # 点瞬间右键会被服务器判「坐标点不对」（2026-11-27 实机）
    if _DIG_STATE.get("last_pos") == pos:
        _DIG_STATE["stable"] = _DIG_STATE.get("stable", 0) + 1
    else:
        _DIG_STATE["stable"] = 1
    _DIG_STATE["last_pos"] = pos
    if _DIG_STATE["stable"] < DIG_STABLE_POLLS:
        return False
    if now - _DIG_STATE["last_try"] < DIG_RETRY_INTERVAL:
        return False
    _DIG_STATE["last_try"] = now
    ok, info = item_use.use_task_box_item(
        win, DIG_HOE_ICON, "挖宝锄头", prefix="300环")
    log(f"{TAG}挖宝：{info}", "SYS" if ok else "WARN")
    if ok:
        tries = _DIG_STATE.get("tries", 0) + 1
        _DIG_STATE.update(used=True, used_at=time.time(), tries=tries)
        if tries >= DIG_FAIL_ALERT and not _DIG_STATE.get("escalated"):
            _DIG_STATE["escalated"] = True
            log(f"{TAG}挖宝连续 {tries} 次未推进任务（{tmap} {tx},{ty} "
                f"藏宝处），请人工确认人物是否站在藏宝点上", "ERROR")
    return True


# ---- 天下无贼类环（2026-11-27 实机，用户）：敲怪小游戏 + 失败重试 ----
# 流程：追踪「天下无贼[江湖历练任务]」（内容「乌鸡国王后频频遭贼骚扰，过去
# 查探一下。」）→ 点追踪绿字走到乌鸡国王后 → 对话框（无边框但标准框命中，
# 剧情页由点屏兜底推进）→ 选项「毛贼！休想逃！」（已加入
# TASK_ACCEPT_OPTIONS）→ 规则面板 9~10s → 浮空岛竞技场 48s（见
# minigames/tianxiawuzei.py）→ 达标推进下一环 / 未达标失败对话后任务
# 追踪不变 → 重走追踪链接再次挑战（本模块驱动重试）。
# 重试条件（全部满足）：追踪文本仍含「无贼/遭贼骚扰」+ 上次对局已结束且
# 间隔 >= TXWZ_RETRY_GAP + 无标准对话框（失败对话已关闭）+ 不在战斗。
# 连续重试 3 次仍不推进升级一条 ERROR（用户规则：周期失败别每轮 WARN）。
# 第二种类型（战斗型，2026-11-27 用户规则）：入口对话选项出现时给战斗
# 线程注入大型爆竹计划（txwz.run_firecracker_battle）；战斗中本函数让位，
# 战斗结束（watch_in_battle 翻转）后按上述同一重试链路重走追踪链接。
TXWZ_RETRY_GAP = 2.0        # 对局结束 → 允许重走的最小间隔（秒，等对话框渲染）
TXWZ_RETRY_INTERVAL = 4.0   # 重走追踪链接的重试退避（点击未生成对话时）
_TXWZ_STATE = {"ended_at": 0.0, "retry_at": 0.0, "retries": 0,
               "battle_seen": False}

# 画符关窗重开（用户 2026-11-27 规则）：同一制符窗口画 1 次不通过 →
# minigames/huafu.play 点窗口右上角 X 关闭（第三项返回待重走）→ 本模块
# 重走任务追踪链接重新开始，符文随机换一个。关窗后 NPC 还会弹「这个
# 骗子，竟敢溜！」对话（由 follow 对话链推进），对话链走完到空闲帧后
# 才点链接重走（静默门见 _track_restart_idle），直到新的制符窗口出现
# （is_ready 分支清 pending）。连续重走 3 次仍换不到可画符文升级一条
# ERROR（用户规则：周期失败别每轮 WARN）。
HUAFU_RESTART_INTERVAL = 10.0   # 重走追踪链接的重试退避（秒）
HUAFU_RESTART_TIMEOUT = 120.0   # 重开等待上限（秒）：超时仍未见到新制符窗口
                                # 即放弃重开（防 pending 跨环残留反复点链接）
_HUAFU_STATE = {"pending": False, "next_at": 0.0, "restarts": 0, "since": 0.0}

# 重走点击静默门（用户 2026-11-28 实机死循环教训）：关窗后的 NPC 台词与
# 重开对话由 follow 对话链推进，追踪链接点击若落在对话/寻路中途，游戏会
# 把整段任务对话从头重放（人物重新寻路到 NPC）——10s 间隔的重走永远打断
# 在对话中途，对话走不到选项、制符窗口再也回不来（2026-11-28 00:34~00:37
# 日志：重走第 1~19 次，对话框反复回到「雷鸟」第一页）。只在「无标准
# 对话框 + 人物站定」的空闲帧才允许重走点击。
_RESTART_WALK = WalkGuard()


def _track_restart_idle(win, img):
    """重走任务追踪链接前的静默门：对话框在场或人物移动中返回 False。

    对话框在场：NPC 台词/选项正由 follow 对话链推进，点追踪链接会把整段
    任务对话从头重放（2026-11-28 画符死循环教训）。人物移动中：上一次
    重走/任务对话的自动寻路还没走完，再点只会重复起步（同「点屏推进须
    站定」规则，2026-11-11）。无帧可查按未寻路放行（WalkGuard 返回
    None），保持旧行为防位置区不可读时重走饿死。
    """
    if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
        _RESTART_WALK.reset()  # 对话期间的像素不作寻路判定基线
        return False
    return _RESTART_WALK.walking(win, img) is not True


def _huafu_restart(win, img):
    """画符单次运笔不通过已关窗：重走任务追踪链接重新开始（换符文）。

    用户 2026-11-27 规则。关窗后 NPC 对话可能仍在场，任务链接点击可能
    被挡住/未生效——按 HUAFU_RESTART_INTERVAL 退避反复重走，直到新的
    制符窗口出现（_wait_handler 的 is_ready 分支清 pending）。

    2026-11-28 实机：重走点击必须先过 _track_restart_idle 静默门——对话
    推进中点链接会让整段任务对话从头重放，形成「对话框重头开始」死循环。
    """
    if not _track_restart_idle(win, img):
        return  # 对话/寻路进行中：本帧不点，交 follow 链推进后下轮再试
    _HUAFU_STATE["next_at"] = time.time() + HUAFU_RESTART_INTERVAL
    ok, info = task_tracker.track_task(win, TASK_TITLE)
    if ok:
        app_utils.mark_track_walk()
        _HUAFU_STATE["restarts"] += 1
        log(f"{TAG}画符：已重走任务链接重新开始"
            f"（第 {_HUAFU_STATE['restarts']} 次）",
            "ERROR" if _HUAFU_STATE["restarts"] >= 3 else "SYS")
    else:
        log(f"{TAG}画符：重走任务链接失败: {info}", "WARN")


# ---- 四锁四色钥匙开锁环（妙趣横生变体，2026-11-27 实机）----
# 入口：追踪「妙趣横生[江湖历练任务]」→ 点绿字到阿合奇阿洪 → 对话框选项
# 「解开这奇锁！」（已加入 TASK_ACCEPT_OPTIONS）→ 开锁面板（剩余次数
# 10，四色钥匙 Mastermind，实现见 minigames/sanbaihuan_jiesuo.py）。
# 次数保护（用户 2026-11-27 规则）：剩余次数不足时小游戏点面板右上角 X
# 关窗并返回 reopen（仅限一次），本模块按 HUAFU 同款机制重走任务追踪
# 链接重开面板（关窗后 NPC 对话/寻路可能仍在，follow 正常链路配合推进，
# 本分支周期重试直到面板重新出现）。
# 同一标题「妙趣横生」还会随机出别的玩法（转盘等），识环一律以面板
# 模板命中为准，不看标题。
JIESUO_RESTART_INTERVAL = 10.0   # 关窗后重走追踪链接的重试退避（秒）
JIESUO_RESTART_TIMEOUT = 120.0   # 重开等待上限（秒）：超时放弃重开
JIESUO_RESET_SECONDS = 300.0     # 面板消失超过此时长 → 允许下一实例再重开一次
                                 # （同一标题会随机换玩法，标题可能仍是妙趣横生）
_JIESUO_STATE = {"pending": False, "next_at": 0.0, "restarts": 0,
                 "since": 0.0, "reopened": False, "last_panel_at": 0.0}


def _jiesuo_tick(win, bgr, items, img):
    """开锁小游戏：面板在场阻塞求解；点 X 关窗后周期重走追踪链接重开。

    返回 True 仅在本帧真正执行了动作（面板求解或重走点击）时，其余帧
    False 交回 follow 正常链路——关窗重开后的 NPC 对话/寻路推进不能
    被本分支占帧（同 _dig_tick/_huafu_restart 教训）。

    重走点击与画符同款静默门（_track_restart_idle，2026-11-28 死循环
    教训）：对话推进/寻路中途点追踪链接会把整段任务对话从头重放。
    """
    if jiesuo_mini.is_ready(bgr):
        _JIESUO_STATE["pending"] = False  # 面板已（重新）打开
        _JIESUO_STATE["last_panel_at"] = time.time()
        status, info = jiesuo_mini.play(
            win, prefix=TAG,
            allow_reopen=not _JIESUO_STATE["reopened"])
        log(f"{TAG}开锁小游戏：{info}",
            "ERROR" if status == jiesuo_mini.STATUS_FAIL else "SYS")
        if status == jiesuo_mini.STATUS_REOPEN:
            _JIESUO_STATE.update(pending=True, next_at=0.0,
                                 since=time.time(), restarts=0,
                                 reopened=True)
            _RESTART_WALK.reset()
        return True
    if _JIESUO_STATE["pending"]:
        now = time.time()
        if now - _JIESUO_STATE["since"] >= JIESUO_RESTART_TIMEOUT:
            _JIESUO_STATE.update(pending=False, reopened=False,
                                 last_panel_at=0.0)
            log(f"{TAG}开锁：重开等待超时（{JIESUO_RESTART_TIMEOUT:.0f}s "
                f"未见开锁面板），放弃重开交回正常跟踪", "WARN")
            return False
        if now >= _JIESUO_STATE["next_at"]:
            if not _track_restart_idle(win, img):
                return False  # 对话/寻路进行中：下轮再试
            _JIESUO_STATE["next_at"] = now + JIESUO_RESTART_INTERVAL
            ok, info = task_tracker.track_task(win, TASK_TITLE)
            if ok:
                app_utils.mark_track_walk()
                _JIESUO_STATE["restarts"] += 1
                log(f"{TAG}开锁：已重走任务链接重开面板"
                    f"（第 {_JIESUO_STATE['restarts']} 次）",
                    "ERROR" if _JIESUO_STATE["restarts"] >= 3 else "SYS")
            else:
                log(f"{TAG}开锁：重走任务链接失败: {info}", "WARN")
        return False
    # 环已切走（追踪标题不再是「妙趣横生」）或面板已消失足够久：复位重开
    # 计数，下一实例可再重开一次（同标题会随机换玩法，标题也可能不变）
    stale = (_JIESUO_STATE["last_panel_at"]
             and time.time() - _JIESUO_STATE["last_panel_at"]
             >= JIESUO_RESET_SECONDS)
    if stale or "妙趣横生" not in _task_text_from_items(items):
        _JIESUO_STATE.update(pending=False, restarts=0, reopened=False,
                             last_panel_at=0.0)
    return False


def _txwz_ring(text):
    """追踪文本是否为天下无贼环（标题「天下无贼」或内容「遭贼骚扰」）。"""
    return "无贼" in (text or "") or "遭贼骚扰" in (text or "")


def _txwz_tick(win, items, img_np, img):
    """天下无贼处理：战斗型注入爆竹计划；竞技场型阻塞对局；失败重试。

    两种类型共用追踪文案与入口选项，进战斗后才能区分：入口对话选项
    「毛贼！休想逃！」出现时先注入大型爆竹战斗计划（若本局是战斗型，
    战斗线程进场即接管；竞技场型没有战斗，计划不会被使用）。战斗中
    让位给战斗线程；战斗结束后任务未推进则按原链路重走追踪链接。

    返回 True 仅在本帧真正执行了动作（对局或重走点击）时，其余帧一律
    False 交回 follow 正常链路——前往 NPC 的导航由 follow 点追踪绿字驱动，
    对话页与入口选项由 follow 对话链（点屏兜底 / known_green_options）处理。
    """
    text = _task_text_from_items(items)
    dlg_text = "".join(t for t, _r, s in (items or []) if s >= 0.55)
    # 战斗型：战斗线程已确认在战斗 → 爆竹计划接管，本模块不做竞技场/重试
    if battle_lib.watch_in_battle():
        _TXWZ_STATE["battle_seen"] = True
        return False
    if _TXWZ_STATE.get("battle_seen"):
        # 战斗刚结束（胜负未知）：任务追踪未推进 → 走失败重试链路；若任务
        # 已推进，下面 _txwz_ring 分支会复位状态并清除计划。
        _TXWZ_STATE["battle_seen"] = False
        if _txwz_ring(text):
            _TXWZ_STATE["ended_at"] = time.time()
            log(f"{TAG}天下无贼战斗结束，任务未推进，准备重走追踪链接", "SYS")
    if not _txwz_ring(text):
        plan = battle_lib.battle_plan()
        if plan and plan.get("kind") == "firecracker":
            battle_lib.clear_battle_plan()
            log(f"{TAG}天下无贼环结束，已清除大型爆竹战斗计划", "SYS")
        _TXWZ_STATE.update(ended_at=0.0, retry_at=0.0, retries=0,
                           battle_seen=False)
        return False
    # 战斗型入口：入口选项在场时注入爆竹计划（两种类型共用选项；竞技场
    # 型不会触发战斗，计划随即随环结束清除）。必须在作战前注入——战斗
    # 线程进场确认后立即读计划，进战斗再注入会晚一步。
    if norm_text(txwz.ENTRY_OPTION_TEXT) in norm_text(dlg_text):
        plan = battle_lib.battle_plan()
        if not plan or plan.get("kind") != "firecracker":
            battle_lib.set_battle_plan(
                {"kind": "firecracker",
                 "handler": txwz.run_firecracker_battle})
            log(f"{TAG}天下无贼：已注入大型爆竹战斗计划（战斗型入口）", "SYS")
    # 规则面板（拿起大锤…）或竞技场（分数/倒计时）：开始小游戏。
    # 「拿起大锤」是天下无贼独有文案；竞技场判据要求本环 + 顶部计分条。
    if "拿起大锤" in dlg_text \
            or (_txwz_ring(text) and "倒计时" in dlg_text and "分数" in dlg_text):
        ok, info = txwz.play(win, prefix=TAG)
        log(f"{TAG}天下无贼：{info}", "SYS" if ok else "WARN")
        if ok:
            _TXWZ_STATE["ended_at"] = time.time()
        return True
    # 失败重试：一局结束后任务未推进（追踪文本不变）→ 重走追踪链接再挑战
    now = time.time()
    if not _TXWZ_STATE["ended_at"] \
            or now - _TXWZ_STATE["ended_at"] < TXWZ_RETRY_GAP \
            or now < _TXWZ_STATE["retry_at"]:
        return False
    if img is None or dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
        return False  # 失败对话还没关：交给剧情兜底推进，关闭后再重走
    _TXWZ_STATE["retry_at"] = now + TXWZ_RETRY_INTERVAL
    ok, info = task_tracker.track_task(win, TASK_TITLE, pre_items=(items, img_np))
    if ok:
        app_utils.mark_track_walk()
        _TXWZ_STATE["retries"] += 1
        _TXWZ_STATE["ended_at"] = 0.0  # 待下一次对局结束再重置
        log(f"{TAG}天下无贼未通关，已重走任务链接再次挑战"
            f"（第 {_TXWZ_STATE['retries']} 次重试）",
            "ERROR" if _TXWZ_STATE["retries"] >= 3 else "SYS")
    else:
        log(f"{TAG}天下无贼重走任务链接失败: {info}", "WARN")
    return True


def _wait_handler(win, items, img_np, img):
    """follow 注入：开锁/天下无贼/画符/拼图小游戏；挖宝环到达后用锄头。"""
    if img is None:
        return False
    if _txwz_tick(win, items, img_np, img):
        return True
    bgr = game_map.bgr_frame(img)
    # 四锁四色钥匙开锁（妙趣横生变体，2026-11-27 实机）：面板在场即接管
    if _jiesuo_tick(win, bgr, items, img):
        return True
    # 画符（灵心巧手：蝴蝶妹妹「制符」窗口，2026-11-27 实机）
    if huafu_mini.is_ready(bgr):
        _HUAFU_STATE["pending"] = False  # 窗口已（重新）打开，重开达成
        log(f"{TAG}检测到画符小游戏（制符窗口），开始照描符文", "SYS")
        ok, info, restart = huafu_mini.play(win, prefix=TAG)
        log(f"{TAG}画符处理：{info}", "SYS" if ok else "WARN")
        if restart:
            _HUAFU_STATE["pending"] = True
            _HUAFU_STATE["next_at"] = 0.0  # 空闲帧立即重走任务追踪
            _HUAFU_STATE["since"] = time.time()
            _RESTART_WALK.reset()
        return True
    # 关窗重开等待期：周期重走任务追踪链接，直到制符窗口重新出现
    # （用户 2026-11-27 规则：画 1 次不通过关窗重开换符文）。**本分支
    # 不得 return True**：重开后要由 follow 内置对话链推进 NPC 对话、
    # 点「交给我吧」选项，wait_handler 占住本帧会让对话永不推进、
    # 制符窗口不再出现而卡到超时（同 _dig_tick 占帧教训）。
    if _HUAFU_STATE["pending"]:
        now = time.time()
        if now - _HUAFU_STATE["since"] >= HUAFU_RESTART_TIMEOUT:
            _HUAFU_STATE["pending"] = False
            log(f"{TAG}画符：重开等待超时（{HUAFU_RESTART_TIMEOUT:.0f}s 未见"
                f"制符窗口），放弃重开交回正常跟踪", "WARN")
            return False
        if now >= _HUAFU_STATE["next_at"]:
            _huafu_restart(win, img)
        return False
    if jigsaw.is_ready(bgr):
        log(f"{TAG}检测到拼图小游戏（准备好了），开始自动求解", "SYS")
        ok, info = jigsaw.play(win, prefix=TAG)
        log(f"{TAG}拼图处理：{info}", "SYS" if ok else "WARN")
        return True
    return _dig_tick(win, items, img)


def verify_claim_flow(win):
    """诊断：只走到萧举人并读取对话框选项（不点选项、不领取）。"""
    ok, info = _walk_to_xiao(win, TAG)
    if not ok:
        return False, info
    frame = _wait_npc_dialog(win, TAG)
    if frame is None:
        return False, "未出现萧举人对话框"
    items, _img_np = frame
    texts = [t for t, _r, _s in items]
    return True, f"萧举人对话框正常，文本行 {len(texts)} 条"
