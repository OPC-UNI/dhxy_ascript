"""修罗任务自动化。

流程（与五环基本一致）：
0. 任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪
1. 领任务（已实现）：灵兽村领取点
   1.0 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
   1.1 队长先走到领取点附近（队伍是否满都先走位）：
       已在领取点 110,90 附近（±15）→ 跳过寻路；
       不在灵兽村 → 世界地图点击灵兽村图标 → 小地图点击 (603,392) 寻路；
       在灵兽村但不在领取点附近 → 直接打开小地图点击 (603,392) 寻路
       寻路等待到达 110,90（±5，更严格：走动中识图点击容易点不到）
   1.2 到达后持续监控队伍人数（通用设置-修罗设置，仅队长生效）：
       满足最少人数才领取（本步不检查种族）；不满足 10 秒后重查，
       人留在领取点；队长被转移则转 1.0 非队长模式
   1.3 识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
       → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
       （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框，本轮领取失败
   1.4 领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）
2. 寻路：点击任务追踪绿字自动寻路（复用 xuanshu.follow_task）
3. 对话开打：寻路到达后出现对话面板 → 点击绿色选项「看打！」进入战斗
   （点击成功标记本轮战斗由「看打！」进入）
4. 战斗：战斗由 battle.watch_loop 线程自动操作；follow_task 战斗中自动跳过识别
5. 返程：战斗结束后用修罗回城符回灵兽村（仅「看打！」进入的战斗才返程，
   其他方式进入的战斗不自动返程）：打开背包 → 打开任务储物格
   （box_task/box_task_selected 双模板比分数判定）→ 右键修罗回城符
   → 关闭世界地图/小地图/背包
6. 循环往复；达到次数上限时结束任务进程

TODO（待用户逐步确认后填充）：
- 任务标题（追踪面板匹配串；注意不可与地图位置文案如「修罗古城」混淆，
  参考客栈任务「客栈任务」vs「洛阳客栈」的教训，需实测确认）
- 修罗回城符模板（res/img/xiuluo_huichengfu.png，用户后续提供；
  未提供时返程步骤返回失败，下一轮领取走世界地图寻路兜底）
"""
import re
import time
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.system import R  # 资源路由（模板路径）

from .app_utils import human_click, human_move, log
from . import task_tracker  # 任务追踪面板操作
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import team  # 组队检查（领取前队伍人数检查，仅队长生效）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from . import bag  # 背包操作（打开/关闭背包、任务储物格）
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from .control_window import load_task_counts, load_xiuluo_settings  # 任务次数上限/修罗设置
from .xuanshu import (follow_task,  # 任务跟踪（绿字点击寻路/战斗跳过/附加对话处理）
                      _dialog_text_present)  # 对话框文案检测（复用玄枢轶闻）

# 任务模式与追踪面板标题（TODO: 匹配串待实测确认）
TASK_MODE = "修罗"       # 控制面板任务模式名（任务次数配置键）
TASK_TITLE = "修罗"      # 追踪面板标题匹配串（TODO: 实测确认，勿与修罗古城等文案混淆）

# ---- 领取任务（灵兽村，用户实测） ----
LINGSHOUCUN = "灵兽村"                        # 位置识别地名（精确匹配）
WORLD_MAP_LINGSHOUCUN_ICON = "shijie_lingshoucun.png"  # 世界地图灵兽村图标（14.png 实测素材）
LINGQU_ICON = "liuluo_lingqu.png"             # 修罗领取任务按钮（15.png 实测素材）
CLAIM_SPOT = (110, 90)                        # 领取点地图坐标（用户实测 111,90 附近）
CLAIM_NEAR_TOLERANCE = 15                     # 已在领取点附近判定容差（用户步 1.1）
WALK_ARRIVE_TOLERANCE = 5                     # 寻路到达判定容差（用户步 1.2，更严格：
                                              # 走动中识图点击容易点不到）
LINGSHOU_MAP_POS = (603, 392)                 # 灵兽村小地图上领取点位置的点击点（窗口相对坐标，用户实测）
CLAIM_DIALOG_OPTION = "闲来无事，要我帮忙吗"    # 领取对话框绿色选项（游戏内原文，实测用字）
CLAIM_COUNT_TEXT = "今日已完成次数"            # 领取后对话框中的完成次数文案（辅助记录完成次数）
TEAM_WARNING_TEXT = "修罗的战斗力异常强大，千万要找齐伙伴再一同消灭。"  # 队伍数量小于 3 的提示（实测文案）
SCREEN_CENTER_POS = (689, 415)                # 屏幕中间（游戏窗口中心，同 kezhan/xuanshu，点击关闭/推进对话框）
CLAIM_ARRIVE_TIMEOUT = 90                     # 寻路到领取点的超时
CLAIM_BUTTON_TIMEOUT = 20                     # 到达后等待领取任务按钮出现的超时
CLAIM_DIALOG_TIMEOUT = 15                     # 领取对话框处理超时
CLAIM_COUNT_TIMEOUT = 5                       # 等待完成次数对话框出现的超时

# ---- 领取前队伍检查 ----
TEAM_CHECK_RETRY_INTERVAL = 10  # 队伍人数不足时的重试间隔（秒）

# ---- 对话开打（寻路到达后出现对话面板） ----
FIGHT_DIALOG_OPTION = "看打！"  # 对话面板绿色选项（点击进入战斗，游戏内原文）
_fight_via_kanda = False        # 本轮战斗是否由「看打！」进入（决定战后是否返程）

# ---- 战斗（战斗操作由 battle.watch_loop 线程负责） ----
BATTLE_END_POLL = 2.0            # 等待战斗结束的轮询间隔
BATTLE_END_TIMEOUT = 600         # 等待战斗结束的超时兜底

# ---- 返程（用户流程：背包任务储物格 → 修罗回城符右键 → 回灵兽村） ----
BOX_TASK_ICON = "box_task.png"                    # 背包任务储物格按钮（未选中态，点击打开）
BOX_TASK_SELECTED_ICON = "box_task_selected.png"  # 任务储物格已打开（选中态，无需再点击）
XIULUO_RETURN_ICON = "xiuluo_huichengfu.png"      # 修罗回城符模板（TODO: 待用户提供）
TASK_BOX_THRESHOLD = 0.8         # 储物格按钮双模板判定阈值（与其他模板一致）
TASK_BOX_TIMEOUT = 10            # 等待任务储物格打开的超时（点击后渲染/切换）
RETURN_TALISMAN_TIMEOUT = 15     # 等待回城符在任务储物格中出现的超时
RETURN_POLL_INTERVAL = 0.5       # 返程轮询间隔（打开动画约 0.4s，实测点击后 1s 内
                                 # 出现选中态；非防抖判定点，无需更长窗口）
VERIFY_HOVER_SECONDS = 2.0       # 验证模式：鼠标悬停在回城符上等待关闭背包的秒数
# 任务储物格按钮相对背包面板标志（bag_box 中心）的偏移与搜索条带：
# 面板可拖动但内部布局固定，偏移恒定（面板左缘/中部两位置实测
# +349/+305 与 +353/+305，取均值）；条带留 55px 余量，带区全未命中
# 才全帧兜底（限频：仅带区未命中时触发）
TASK_BOX_OFFSET = (351, 305)
TASK_BOX_BAND_MARGIN = 55

# 骨架阶段占位标记：流程函数未实现时返回该描述，主循环据此退出避免空转；
# 实现逐个填充后该分支自然不再触发
STUB_INFO = "流程待实现"

# 领取后对话框「今日已完成次数：N」的完成次数解析（OCR 可能把冒号误读，兼容）
_CLAIM_COUNT_RE = re.compile(re.escape(CLAIM_COUNT_TEXT) + r"\s*[:：]?\s*(\d+)")


def _near_claim_spot(pos, tolerance):
    """判断位置是否在灵兽村领取点附近（地名精确匹配，容差抗 OCR 噪声）。"""
    if pos is None:
        return False
    return (pos[0].strip() == LINGSHOUCUN
            and abs(pos[1] - CLAIM_SPOT[0]) <= tolerance
            and abs(pos[2] - CLAIM_SPOT[1]) <= tolerance)


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


def _click_claim_option(win, items, img_np):
    """点击领取对话框中的绿色选项「闲来无事，要我帮忙吗」，成功返回 True。

    OCR 对中文标点误读常见（如「，」读成「；」），去标点后按子串匹配。
    限定对话框区域（x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    """
    needle = re.sub(r"[^\w]", "", CLAIM_DIALOG_OPTION)
    for txt, rect, score in items:
        if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[修罗] 已点击绿色选项「{txt}」", "SYS")
            return True
    return False


def _norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做模糊匹配可大幅降低漏配。
    """
    return re.sub(r"[^\w]", "", s)


def _handle_team_warning(win, items, img_np):
    """对话框出现「修罗的战斗力异常强大…」：队伍数量小于 3，
    点击屏幕中间一次关闭对话框。返回是否已处理。
    """
    norm_items = [(_norm_text(t), r, s) for t, r, s in items]
    if not _dialog_text_present(norm_items, _norm_text(TEAM_WARNING_TEXT)):
        return False
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[修罗] 队伍数量小于 3，已点击屏幕中间关闭提示对话框", "WARN")
    return True


def _click_claim_option_fallback(win, items, img_np):
    """领取选项 OCR 噪声兜底：点击含「帮忙」的绿色选项，成功返回 True。

    限定对话框区域（x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    """
    for txt, rect, score in items:
        if score >= 0.55 and "帮忙" in txt \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[修罗] 已点击绿色选项「{txt}」（兜底匹配）", "SYS")
            return True
    return False


def _read_claim_count(win, timeout=CLAIM_COUNT_TIMEOUT):
    """领取后对话框「今日已完成次数：N」中解析已完成次数；失败返回 None。

    全部 OCR 行拼接后正则提取（行拆分/冒号误读均兼容）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _ = game_map.ocr_image(img)
        raw = re.sub(r"\s", "", "".join(t for t, _, s in items if s >= 0.55))
        m = _CLAIM_COUNT_RE.search(raw)
        if m:
            return int(m.group(1))
        time.sleep(0.8)
    return None


def _walk_to_claim_spot(win):
    """寻路到灵兽村领取点附近（与队伍人数无关，队长身份下执行）。

    用户流程：已在领取点 110,90 附近（±15）→ 跳过寻路；
    不在灵兽村 → 世界地图点击灵兽村图标 → 小地图点击 (603,392) 寻路；
    在灵兽村但不在领取点附近 → 直接打开小地图点击 (603,392) 寻路。
    等待到达 110,90（±5 严格容差：走动中识图点击容易点不到）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    pos = game_map.read_position_with_recovery(win)
    if _near_claim_spot(pos, CLAIM_NEAR_TOLERANCE):
        return True, "已在领取点附近"
    if pos is None or pos[0].strip() != LINGSHOUCUN:
        # 不在灵兽村：世界地图点击灵兽村图标，打开灵兽村小地图
        ok, info = game_map.open_mini_map_city(win, WORLD_MAP_LINGSHOUCUN_ICON,
                                               LINGSHOUCUN)
        if not ok:
            return False, info
    else:
        # 在灵兽村但不在领取点附近：直接打开小地图
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    human_click(win, LINGSHOU_MAP_POS[0], LINGSHOU_MAP_POS[1])
    log("[修罗] 已点击灵兽村小地图领取点位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    game_map.close_maps(win)
    # 等待到达 110,90 附近（±5 严格容差：走动中识图点击容易点不到）
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            log("[修罗] 已到达领取点附近", "SYS")
            return True, "已到达领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[修罗] 前往领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"寻路到领取点超时（未到达灵兽村 {CLAIM_SPOT[0]},{CLAIM_SPOT[1]} 附近）"


def _claim_task(win):
    """在领取点附近领取修罗任务（前提：已走到领取点附近）。返回 (是否成功, 描述)。

    识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
    → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
    （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框，本轮领取失败。
    领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取"
    # 识图领取任务按钮并点击
    if not _wait_template(win, LINGQU_ICON, CLAIM_BUTTON_TIMEOUT):
        return False, "到达后未出现领取任务按钮"
    pt = game_map.find_template(win, LINGQU_ICON)
    if pt is None:
        return False, "领取任务按钮出现后又消失，未点击"
    human_click(win, pt[0], pt[1])
    log("[修罗] 已点击领取任务", "SYS")
    # 对话框：点绿色选项「闲来无事，要我帮忙吗」完成领取
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    claimed = False
    while time.time() < deadline:
        if task_tracker.has_task(win, TASK_TITLE):
            claimed = True
            break
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, img_np = game_map.ocr_image(img)
        if _handle_team_warning(win, items, img_np):
            return False, "队伍数量小于 3，无法领取（已关闭提示对话框）"
        if _click_claim_option(win, items, img_np):
            claimed = True
            break
        time.sleep(0.5)
    if not claimed:
        # 兜底：OCR 噪声导致指定文案未匹配时，点含「帮忙」的绿色选项
        deadline = time.time() + CLAIM_DIALOG_TIMEOUT
        while time.time() < deadline:
            if task_tracker.has_task(win, TASK_TITLE):
                claimed = True
                break
            img = win.capture(force_activate=False)
            if img is not None:
                items, img_np = game_map.ocr_image(img)
                if _handle_team_warning(win, items, img_np):
                    return False, "队伍数量小于 3，无法领取（已关闭提示对话框）"
                _click_claim_option_fallback(win, items, img_np)
            time.sleep(0.5)
    if not claimed:
        return False, "领取对话框处理失败"
    # 1.5 领取后对话框「今日已完成次数：N」：解析已完成次数（辅助完成次数记录）
    count = _read_claim_count(win)
    if count is not None:
        log(f"[修罗] {CLAIM_COUNT_TEXT}：{count}", "SYS")
    # 校验任务出现在追踪面板
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while not task_tracker.has_task(win, TASK_TITLE) and time.time() < deadline:
        time.sleep(1.0)
    if not task_tracker.has_task(win, TASK_TITLE):
        return False, "已点击领取选项，但任务追踪中未检测到任务"
    tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
    return True, f"修罗任务已领取{tail}"


def _check_team_ready(win):
    """领取前队伍检查（修罗设置仅在队长时生效，一次面板开关）。

    队长时按通用设置-修罗设置的最少人数检查（本步不检查种族）。
    检查结果顺带更新调试悬浮层「队伍」行（身份 + 成员信息）。
    返回 (状态, 描述)；状态见 team.check_xiuluo_team：
    ready 可领取 / wait 人数不足 / not_leader 非队长或队长无法判定。
    """
    settings = load_xiuluo_settings()
    status, info, members = team.check_team_min(win,
                                                min_members=settings["min_members"])
    team.update_overlay_team(status, info, members)
    return status, info


def _handle_fight_dialog(win, items, img_np):
    """对话开打：跟踪期间出现对话 → 点击绿色选项「看打！」进入战斗。

    点击成功即标记本轮战斗由「看打！」进入（战后据此决定是否返程）。
    OCR 对标点误读常见，去标点后按子串匹配；限定对话框区域
    （x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    返回是否已处理。
    """
    global _fight_via_kanda
    needle = _norm_text(FIGHT_DIALOG_OPTION)
    for txt, rect, score in items:
        if score >= 0.55 and needle in _norm_text(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[修罗] 已点击「看打！」进入战斗", "SYS")
            _fight_via_kanda = True
            return True
    return False


def _follow_to_done(win):
    """寻路 + 对话开打 + 战斗：跟踪任务直到本轮完成。

    复用 xuanshu.follow_task：绿字点击自动寻路、对话开打走附加对话处理
    （点击「看打！」进入战斗）、战斗由战斗监控线程操作且跟踪中自动跳过。
    返回 (是否成功, 描述)。
    """
    ok, info = follow_task(win, TASK_TITLE, prefix="修罗",
                           extra_dialog_handler=_handle_fight_dialog)
    return ok, info


def _wait_battle_end(win, timeout=BATTLE_END_TIMEOUT):
    """战斗：等待当前战斗结束（战斗操作由 battle.watch_loop 线程自动完成）。

    轮询 is_in_battle 直到战斗画面消失；超时返回失败防止卡死。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    while is_in_battle(win):
        if time.time() > deadline:
            return False, f"等待战斗结束超时（{timeout}s）"
        time.sleep(BATTLE_END_POLL)
    return True, "战斗已结束"


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
        log("[修罗] 背包已打开，跳过开包", "INFO")
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
                log("[修罗] 已点击任务储物格按钮，等待打开...", "SYS")
                clicked = True
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        time.sleep(RETURN_POLL_INTERVAL)
    if clicked:
        return False, "已点击任务储物格按钮，但未检测到选中态（打开失败）"
    return False, "背包中未找到任务储物格按钮"


def _find_return_talisman(win):
    """在已打开的任务储物格中查找修罗回城符，返回 (pt 或 None, 描述)。

    只识别不点击（右键使用由 _use_return_talisman 执行；验证流程
    复用本函数仅移动鼠标悬停）。模板尚未提供时直接失败并说明
    （不进入等待循环）。
    """
    try:
        has_tpl = Path(R.img(XIULUO_RETURN_ICON)).exists()
    except Exception:
        # R.img 对不存在的模板会打印 ⚠️ 警告，GBK 控制台下 print 抛
        # UnicodeEncodeError；吞掉按模板缺失处理
        has_tpl = False
    if not has_tpl:
        return None, f"修罗回城符模板未提供（待补充 {XIULUO_RETURN_ICON}）"
    deadline = time.time() + RETURN_TALISMAN_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, XIULUO_RETURN_ICON)
        if pt is not None:
            return pt, "已在任务储物格中找到修罗回城符"
        time.sleep(RETURN_POLL_INTERVAL)
    return None, "任务储物格中未找到修罗回城符"


def _use_return_talisman(win):
    """在已打开的任务储物格中识别修罗回城符并右键使用，返回灵兽村。

    返回 (是否成功, 描述)。未找到时下一轮领取流程会走世界地图
    寻路回灵兽村兜底。
    """
    pt, info = _find_return_talisman(win)
    if pt is None:
        return False, info
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"[修罗] 已右键使用修罗回城符 ({pt[0]}, {pt[1]})，返回灵兽村", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, "已右键使用修罗回城符"


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
            log("[修罗] 世界地图/小地图/背包均未打开，无需关闭", "INFO")
            return
    game_map.close_maps(win)
    ok, info = bag.close_bag(win)
    if ok:
        log(f"[修罗] {info}", "SYS")
    else:
        log(f"[修罗] 背包关闭失败: {info}", "WARN")


def _return_to_npc(win):
    """返程：任务完成后用修罗回城符回灵兽村，准备下一轮领取。返回 (是否成功, 描述)。

    用户流程：
    1. 战斗结束后打开背包：检查 box_task_selected（选中态），存在说明
       任务储物格已在背包中打开；否则点击 box_task 打开
    2. 识别修罗回城符 → 右键使用，返回到灵兽村
    3. 关闭世界地图、小地图、背包
    """
    # 前置：确保战斗已结束（意外遭遇战会遮挡背包按钮）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 1. 打开背包并确保任务储物格已打开
    ok, info = _open_task_box(win)
    if not ok:
        return False, info
    # 2. 识别修罗回城符并右键使用（返回灵兽村）
    ok, info = _use_return_talisman(win)
    if not ok:
        # 回城符未使用：也关闭背包与地图保持界面干净；下一轮领取
        # 会走世界地图寻路回灵兽村兜底
        _close_return_panels(win)
        return False, info
    # 3. 关闭世界地图、小地图、背包（清理界面）
    _close_return_panels(win)
    # 传送后位置识别（短时等待加载完成，仅供日志与下一轮领取判断）
    deadline = time.time() + 3
    pos = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if pos is not None:
            break
        time.sleep(1.0)
    if pos is not None:
        log(f"[修罗] 返程后位置: {pos[0]} {pos[1]},{pos[2]}", "SYS")
    else:
        log("[修罗] 返程后位置识别失败（不影响本轮返程）", "INFO")
    return True, "已返回灵兽村"


def verify_return_flow(win):
    """验证修罗回程流程（高级设置-验证按钮，主进程调用）。

    与实际返程 _return_to_npc 完全一致，唯一区别是不右键使用回城符：
    仅将鼠标移动到回城符上悬停 VERIFY_HOVER_SECONDS 秒供人工确认，
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
    # 2. 识别修罗回城符：仅移动鼠标悬停，不点击
    pt, info = _find_return_talisman(win)
    if pt is None:
        _close_return_panels(win)
        return False, info
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    log(f"[修罗][验证] 已移动鼠标到修罗回城符 ({pt[0]}, {pt[1]})，"
        f"悬停 {VERIFY_HOVER_SECONDS:.0f} 秒（不点击）...", "SYS")
    time.sleep(VERIFY_HOVER_SECONDS)
    # 3. 关闭世界地图、小地图、背包
    _close_return_panels(win)
    return True, f"验证通过：回城符位于 ({pt[0]}, {pt[1]})"


def _keep_alive(info):
    """非队长：核心流程不执行，保持任务运行（战斗线程自动操作）。

    用户手动停止时任务进程被回收，无需自行退出。
    """
    log(f"[修罗] {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
    while True:
        time.sleep(1)


def run_loop(win, cycle_interval=3.0):
    """修罗主循环：领任务 → 寻路 → 对话开打 → 战斗 → 返程 → 循环领取。

    领取逻辑（用户确认）：
    1. 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
    2. 队长先走到领取点附近（队伍是否满都先走位）
    3. 到达后持续监控队伍人数：满足最少人数才领取，不满足 10 秒重查
    （人留在领取点，只开队伍面板检查）

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取时出现每日次数已满（TODO: 具体文案待确认）
    返回结束原因描述。

    骨架阶段：流程函数返回 STUB_INFO（未实现）时直接结束任务，避免空转。
    """
    global _fight_via_kanda
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[修罗] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")

    done = 0
    round_no = 0
    while True:
        round_no += 1
        log(f"[修罗] ==== 第 {round_no} 轮 ====", "SYS")
        # 1. 领任务：任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪
        if task_tracker.has_task(win, TASK_TITLE):
            log("[修罗] 任务追踪中已有修罗任务，直接跟踪", "SYS")
        else:
            # 1.0 非队长判定（一次面板开关）：非队长 → 核心流程不执行
            status, info = _check_team_ready(win)
            if status == "not_leader":
                _keep_alive(info)
            # 1.1 队长：队伍是否满都先走到领取点附近
            ok, info = _walk_to_claim_spot(win)
            if not ok:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            # 1.2 到达后持续监控队伍人数（人留在领取点，10 秒重查）：
            #     满足最少人数才领取；队长被转移则转非队长保持模式
            while True:
                status, info = _check_team_ready(win)
                if status == "not_leader":
                    _keep_alive(info)
                if status == "ready":
                    log(f"[修罗] {info}", "SYS")
                    break
                log(f"[修罗] {info}，暂不领取（持续监控队伍人数）", "WARN")
                time.sleep(TEAM_CHECK_RETRY_INTERVAL)
            # 1.3 领取任务
            ok, info = _claim_task(win)
            if not ok:
                if info == STUB_INFO:
                    return f"修罗任务流程尚未实现（{info}），结束任务"
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[修罗] {info}", "SYS")
            _fight_via_kanda = False  # 新一轮开始：重置「看打！」进入战斗标记
        # 2~4. 寻路 / 对话开打 / 战斗：由任务跟踪统一驱动
        #     （寻路点击绿字自动进行，对话开打走附加对话处理，
        #      战斗由战斗监控线程自动操作、跟踪中自动跳过）
        ok, info = _follow_to_done(win)
        if not ok:
            if info == STUB_INFO:
                return f"修罗任务流程尚未实现（{info}），结束任务"
            log(f"[修罗] {info}", "WARN")
            time.sleep(cycle_interval)
            continue
        # 5. 返程：仅「看打！」进入的战斗结束后才返程（背包任务储物格
        #    右键修罗回城符回灵兽村），其他方式进入的战斗不自动返程
        if _fight_via_kanda:
            ok, info = _return_to_npc(win)
            if not ok:
                if info == STUB_INFO:
                    return f"修罗任务流程尚未实现（{info}），结束任务"
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
        done += 1
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        log(f"[修罗] 本轮修罗任务完成（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
