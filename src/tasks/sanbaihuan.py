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
"""
import re
import time

from ..core import app_utils
from ..core import debug_overlay
from ..core import settings
from ..core.app_utils import log, norm_text
from ..device import items as item_catalog
from ..device import map as game_map
from ..device import map_nav
from ..device import task_panel
from ..device import task_tracker
from ..device.battle import is_in_battle
from ..minigames import sanbaihuan_jigsaw as jigsaw
from ..workflows import claim_flow
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

    _DIG_STATE.update(key=None, used=False,
                      last_pos_at=0.0, last_try=0.0)  # 新一局复位挖宝状态
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
            wait_handler=_wait_handler)
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

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_NUM = r"([一二三四五六七八九十])"
_VOLUME_RE = re.compile(
    r"天书残卷\s*第?\s*[（(]?\s*" + _NUM + r"\s*[)）]?\s*卷?"
    r"(?:\s*或\s*第?\s*[（(]?\s*" + _NUM + r"\s*[)）]?\s*卷?)?")


def parse_required_volumes(text):
    """从任务文案解析要求的天书卷号集合（如「天书残卷三或十」→ {3,10}）。"""
    m = _VOLUME_RE.search(text or "")
    if not m:
        return set()
    return {_CN_NUM[g] for g in m.groups() if g}


def volume_of(name):
    """物品名 → 卷号（「天书残卷第三卷」→ 3；无卷号 None）。"""
    m = _VOLUME_RE.search(name or "")
    return _CN_NUM[m.group(1)] if m else None


_GIVE_TARGET_RE = re.compile(r"将其交给\s*([^\s，,。；;：:]+)")


def parse_give_target(text):
    """从任务文案解析给与目标 NPC（「…并将其交给黑熊精」→「黑熊精」）。"""
    m = _GIVE_TARGET_RE.search(text or "")
    return m.group(1).strip(" 　。！!；;，,") if m else ""


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


def _extra_dialog_handler(win, items, img_np):
    """follow 注入：给与面板打开时按任务要求选物并给与。

    物品定位：任务文案解析物品名 → 物品目录（device/items.py）取图标
    模板定位候选格 → 悬停读物品名确认（同名多卷/近似图标靠提示名区分）。
    未登记物品走全格悬停兜底（慢路径，不常见）。
    """
    global _LAST_GIVE_TRY
    if not give_flow.give_panel_open(img_np):
        return False
    now = time.time()
    if now - _LAST_GIVE_TRY < GIVE_RETRY_INTERVAL:
        return True  # 面板已占用：节流窗口内不再重复 OCR/悬停
    _LAST_GIVE_TRY = now
    text = _task_text_from_items(items)
    phrase = parse_required_item(text)
    target = parse_give_target(text)
    npc = give_flow.read_give_npc(win)
    if target and npc and target not in npc and npc not in target:
        log(f"{TAG}给与面板 NPC 不匹配：任务目标「{target}」/ 面板「{npc}」，"
            f"暂不给与", "WARN")
        return True
    if target and npc:
        log(f"{TAG}给与面板 NPC 核对：任务「{target}」= 面板「{npc}」", "INFO")
    entry = item_catalog.find_by_name(phrase or text)
    if not phrase and entry is None:
        log(f"{TAG}给与面板已打开但未解析到物品名"
            f"（任务文案：{text[:40]}）", "WARN")
        return True  # 面板已占用：本帧不点追踪链接，下一轮重试
    if entry is not None and entry.key == "tianshu":
        required = parse_required_volumes(phrase)
        if not required:
            log(f"{TAG}给与面板：天书文案未解析到卷号"
                f"（{phrase!r}）", "WARN")
            return True
        accept = lambda name: volume_of(name) in required
        keyword = entry.name
        detail = f"天书卷号 {sorted(required)}"
    elif entry is not None:
        accept = lambda name: entry.name in (name or "")
        keyword = entry.name
        detail = f"目录物品「{entry.name}」（{entry.icon}）"
    else:
        accept = lambda name: phrase in (name or "")             or (name or "") in phrase
        keyword = phrase
        detail = f"未登记物品「{phrase}」（全格悬停兜底）"
    if entry is not None:
        ok, info = give_flow.pick_and_give(
            win, entry.icon, accept=accept, keyword=keyword, prefix=TAG)
    else:
        ok, info = give_flow.pick_and_give_any(
            win, accept=accept, keyword=keyword, prefix=TAG)
    log(f"{TAG}给与面板：{detail} → {info}", "SYS" if ok else "WARN")
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
                       "我来助你平静", "助你平静")


# ---- 挖宝类环（用户 2026-11-26）----
# 追踪：「到白骨山（237，26）藏宝处」（地图/坐标可变）。点击追踪自动
# 寻路到藏宝处，到达后在背包-任务储物格右键「挖宝锄头」触发挖掘。
DIG_HOE_ICON = item_catalog.icon("wabaochutou")
DIG_ARRIVE_TOLERANCE = 8        # 藏宝处到达容差（寻路停在点旁，±8）
DIG_POS_POLL = 1.0              # 位置 OCR 节流（轮询 0.5s，位置读 ~0.1s）
DIG_RETRY_INTERVAL = 5.0        # 用锄头失败重试间隔
_DIG_STATE = {"key": None, "used": False,
              "last_pos_at": 0.0, "last_try": 0.0}

_DIG_TARGET_RE = re.compile(
    r"到\s*([^（(]+?)\s*[（(]\s*(\d+)\s*[，,]\s*(\d+)\s*[）)]\s*藏宝处")


def parse_dig_target(text):
    """解析挖宝任务目标：「到白骨山（237，26）藏宝处」→ (地图, x, y)。"""
    m = _DIG_TARGET_RE.search(text or "")
    if not m:
        return None
    return m.group(1).strip(), int(m.group(2)), int(m.group(3))


def _dig_tick(win, items, img):
    """挖宝监控：追踪含藏宝处且到达目标坐标 → 右键挖宝锄头。

    返回 True 表示本帧由挖宝链路占用（继续轮询），False 表示不是
    挖宝任务（走普通跟踪/其它处理器）。
    """
    text = _task_text_from_items(items)
    if "藏宝处" not in text:
        if _DIG_STATE.get("key") is not None:
            _DIG_STATE.update(key=None, used=False)
        return False
    target = parse_dig_target(text)
    if target is None:
        return False
    tmap, tx, ty = target
    key = (tmap, tx, ty)
    if _DIG_STATE.get("key") != key:
        _DIG_STATE.update(key=key, used=False,
                          last_pos_at=0.0, last_try=0.0)
        log(f"{TAG}挖宝任务：{tmap} ({tx},{ty}) 藏宝处", "SYS")
    if _DIG_STATE.get("used"):
        return True
    now = time.time()
    if now - _DIG_STATE["last_pos_at"] < DIG_POS_POLL:
        return True
    _DIG_STATE["last_pos_at"] = now
    if img is None:
        return True
    pos = game_map.read_map_position(win, img=img)
    if pos is None or not map_nav.names_match(pos[0], tmap)             or abs(pos[1] - tx) > DIG_ARRIVE_TOLERANCE             or abs(pos[2] - ty) > DIG_ARRIVE_TOLERANCE:
        return True
    if now - _DIG_STATE["last_try"] < DIG_RETRY_INTERVAL:
        return True
    _DIG_STATE["last_try"] = now
    ok, info = item_use.use_task_box_item(
        win, DIG_HOE_ICON, "挖宝锄头", prefix="300环")
    log(f"{TAG}挖宝：{info}", "SYS" if ok else "WARN")
    if ok:
        _DIG_STATE["used"] = True
    return True


def _wait_handler(win, items, img_np, img):
    """follow 注入：拼图参考态整局求解；挖宝环到达后自动用锄头。"""
    if img is None:
        return False
    bgr = game_map.bgr_frame(img)
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
