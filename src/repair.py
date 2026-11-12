"""自动修理装备（洛阳城）：每日战斗场次计数、修理行程链路、组号队员 pass 调度。

用户规则（2026-11-12 定稿）：
- 修罗/五倍各自独立开关 + 间隔（场战斗），默认开、30 场；
- 每轮领取任务成功后判断「今日累计战斗场次 ≥ 间隔」→ 前往洛阳城修理；
- 修理行程：世界地图 → 洛阳城 → 洛阳小地图点击 NPC 坐标 → 等待到达
  （位置 ±容差 / NPC 模板可见 / 已见洛阳城后位置缺失均算到达，超时 90s，
  途中进战斗等待并顺延截止）→ 识别修理 NPC 模板 → 点击 → 战斗线程在
  预期窗口内点对话框选项（见 battle._handle_repair_dialog，文案→选项
  映射见 REPAIR_DIALOGS）；
- 单号：点完选项等 3s 后重新点任务追踪寻路（由调用方紧接着调用
  follow_task，其首轮即点链接）；组号：队长点完后逐个切队员 Tab 点
  各自弹窗选项，切回队长后再等 3s；
- 失败（模板未识别/未弹窗/选项未点到/超时）本次取消、记日志、重置
  计数、本轮继续任务（用户选择 A：一次性尝试，下次再等满间隔）。

计数口径：战斗线程每确认进入一场战斗 +1（battle.watch_loop 调用
record_battle），按角色持久化到 state.json，按自然日 00:00 清零；
修罗/五倍各自独立计数。开关关闭期间计数照常累计、只是不触发修理。

资料（用户 2026-11-12 提供）：
- 模板 res/img/luoyang_xiuli.png（36x18，洛阳城修理 NPC 名牌）；
- 洛阳小地图点击坐标 (783, 458)；到达判定坐标 洛阳城 [321,110]（±5）；
- 两套对话框（互斥，不会同时出现）：
  有装备：「…小隐隐于野，中隐隐于市，大隐隐于朝。阁下想修理装备吗？」
          → 绿色选项「确认修理」；
  无装备：「你身上的装备完好无损，无需修理」→ 绿色选项「算了，还是看看」。
  长文案按白龙马模式拆多个片段匹配（OCR 换行/截断时仍能命中），
  任一命中即按该弹窗的选项处理；两套都按一次「完成」处理。
"""
import difflib
import time

import cv2
import numpy as np

from .app_utils import human_click, human_move, log, move_away
from . import app_utils
from . import user_data
from . import task_tracker
from . import dialog_common
from . import map as game_map
from . import battle  # 修理弹窗预期窗口 / 点击信号（battle 内延迟导入本模块，无顶层环）
from . import tab_group  # 组号模式：修理 pass

# ---- 洛阳城修理点（用户 2026-11-12 实测提供） ----
LUOYANG = "洛阳城"                        # 位置识别地名（精确匹配）
WORLD_MAP_LUOYANG_ICON = game_map.WORLD_MAP_LUOYANG_ICON  # 世界地图洛阳城图标（客栈在用，实测 0.991）
REPAIR_NPC_ICON = "luoyang_xiuli.png"     # 修理 NPC 屏幕模板（36x18 名牌）
REPAIR_NPC_THRESHOLD = 0.8                # 模板匹配阈值（真机校准资产，改动前真图交叉验证）
REPAIR_MAP_POS = (783, 458)               # 洛阳小地图上点修理 NPC 的点击点（窗口相对坐标）
REPAIR_SPOT = (321, 110)                  # 到达判定坐标（洛阳城 [321,110]，容差 ±5）
# 两套弹窗（互斥）：文案长，按白龙马模式拆多个片段——OCR 换行/截断时
# 任一稳定片段命中即可，多个片段映射到同一个绿色选项。
REPAIR_DIALOGS = (
    # 有装备需要修理 → 确认修理
    ("小隐隐于野", "确认修理"),
    ("中隐隐于市", "确认修理"),
    ("大隐隐于朝", "确认修理"),
    ("阁下想修理装备吗", "确认修理"),
    # 无装备需要修理 → 算了，还是看看（点完关闭对话框，同样算完成）
    ("你身上的装备完好无损", "算了，还是看看"),
    ("装备完好无损", "算了，还是看看"),
    ("无需修理", "算了，还是看看"),
)

# ---- 行程参数 ----
REPAIR_NEAR_TOLERANCE = 5          # 到达判定容差（对齐巫医：严格 ±5）
REPAIR_ARRIVE_TIMEOUT = 90         # 跨图寻路到达超时（秒）；途中战斗等待并顺延
REPAIR_NPC_TIMEOUT = 3             # 到达后识别修理 NPC 模板窗口（秒，对齐巫医）
REPAIR_HANDLE_TIMEOUT = 12         # 点击 NPC 后等待战斗线程点选项的超时（秒，对齐巫医）
REPAIR_RESUME_WAIT = 3.0           # 点完选项后等 3s 再重新点任务追踪寻路（用户指定）

# ---- 战斗场次计数（按角色持久化、自然日清零、按任务模式独立） ----
_REPAIR_COUNT_KEY = "repair_battles"  # {"date": "YYYY-MM-DD", "modes": {mode: n}}
REPAIR_TASK_MODES = ("修罗", "五倍")


def _today():
    return time.strftime("%Y-%m-%d")


def _role(win):
    """窗口对应角色目录（与 task_progress 同源：窗口标题 ID 片段）。"""
    return user_data.role_id_from_char_key(app_utils.char_key(win))


def _read_entry(win):
    try:
        return user_data.get_state(_REPAIR_COUNT_KEY, _role(win), None)
    except Exception:
        return None


def battles_today(win, mode):
    """该角色今日该任务的累计战斗场次（跨重启持久、跨自然日清零）。"""
    entry = _read_entry(win)
    if not isinstance(entry, dict) or entry.get("date") != _today():
        return 0
    try:
        return int(entry.get("modes", {}).get(mode, 0) or 0)
    except (TypeError, ValueError):
        return 0


def record_battle(win):
    """战斗线程确认进入一场战斗时调用：当前任务模式今日场次 +1。

    任意任务模式都会写入对应键（修理开关关闭期间也累计，只是不触发
    修理）；非修罗/五倍模式不计数（本功能仅覆盖两个任务）。
    """
    mode = app_utils.current_task_mode()
    if mode not in REPAIR_TASK_MODES:
        return
    today = _today()

    def _merge(state):
        entry = state.get(_REPAIR_COUNT_KEY)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "modes": {}}
        modes = entry.setdefault("modes", {})
        try:
            modes[mode] = int(modes.get(mode, 0) or 0) + 1
        except (TypeError, ValueError):
            modes[mode] = 1
        state[_REPAIR_COUNT_KEY] = entry
        return state

    try:
        user_data.update_state(_merge, _role(win))
    except Exception as e:  # 计数失败不影响任务（最坏本次不触发修理）
        log(f"[修理] 战斗场次持久化失败: {e}", "WARN")


def mark_attempted(win, mode):
    """尝试过修理后重置今日计数（用户选择 A：成功/失败都重置）。"""
    today = _today()

    def _merge(state):
        entry = state.get(_REPAIR_COUNT_KEY)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "modes": {}}
        entry.setdefault("modes", {})[mode] = 0
        state[_REPAIR_COUNT_KEY] = entry
        return state

    try:
        user_data.update_state(_merge, _role(win))
    except Exception as e:
        log(f"[修理] 计数重置失败: {e}", "WARN")


def is_due(win, mode, interval):
    """今日累计场次是否达到修理间隔。"""
    try:
        interval = max(1, int(interval))
    except (TypeError, ValueError):
        interval = 30
    return battles_today(win, mode) >= interval


# ---- 对话框规格 ----
_DIALOG_MATCH_THRESHOLD = 0.85  # 文案相似度阈值（与 dialog_common._fuzzy_contains 默认一致）


def _dialog_score(items, text):
    """文案相似度分数（0~1）：逐行与拼接全文取最高，窗口 ±3 字（同 _fuzzy_contains）。"""
    n = len(text)
    if n == 0:
        return 0.0
    lines = [t[0] for t in items]
    lines.append("".join(lines))
    best = 0.0
    for line in lines:
        if not line:
            continue
        if n > len(line):
            best = max(best, difflib.SequenceMatcher(None, line, text).ratio())
            continue
        for size in (n - 3, n - 2, n - 1, n, n + 1, n + 2, n + 3):
            if size <= 0 or size > len(line):
                continue
            # 步长 1（不是 _fuzzy_contains 的 size//2）：修理文案按片段
            # 匹配，片段短（4~10 字），跳采样会漏掉唯一精确窗口导致漏检；
            # 片段间存在包含关系（「装备完好无损」/「你身上的装备完好
            # 无损」），精确窗口必须能拿满分才能取高分者胜
            for i in range(0, len(line) - size + 1):
                window = line[i:i + size]
                sm = difflib.SequenceMatcher(None, window, text)
                if sm.real_quick_ratio() < best or sm.quick_ratio() < best:
                    continue
                best = max(best, sm.ratio())
    return best


def match_dialog(items):
    """在 OCR 行中匹配修理对话框文案，返回 (命中片段, 对应绿色选项) 或 None。

    两套弹窗互斥、不会同时出现；同一弹窗按白龙马模式拆多个文案片段
    （长文案 OCR 换行/截断时仍能命中），所有片段按相似度取最高分——
    片段间存在包含关系（「装备完好无损」是「你身上的装备完好无损」的
    子串），必须让精确窗口拿满分并由最高分者胜，不能「首个命中即返回」
    （否则表序靠前的弱匹配片段会点错弹窗的选项）。同分按表顺序。
    """
    best = None
    best_score = 0.0
    for text, option in REPAIR_DIALOGS:
        if not text or not option:
            continue
        score = _dialog_score(items, text)
        if score >= _DIALOG_MATCH_THRESHOLD and score > best_score:
            best_score = score
            best = (text, option)
    return best


def _configured():
    """修理资料是否齐全（模板文件/小地图坐标/到达坐标/对话框映射）。

    模板文件存在性一并校验（Image Template Rule：不得对着不存在的模板
    路径写点击逻辑；缺图/缺资料时按未配置处理，不进行走位与点击）。
    2026-11-12 用户资料已齐全（见模块文档）；本门保留用于资料误删、
    打包漏带资源等场景的自检（失败只记日志，不误走位/误点击）。
    """
    return (bool(REPAIR_NPC_ICON)
            and game_map.template_size(REPAIR_NPC_ICON) is not None
            and REPAIR_MAP_POS is not None
            and REPAIR_SPOT is not None and bool(REPAIR_DIALOGS)
            and all(t and o for t, o in REPAIR_DIALOGS))


# ---- 行程链路 ----

def _near_repair_spot(pos):
    if REPAIR_SPOT is None:
        return False
    return game_map.is_at_position(pos, LUOYANG, REPAIR_SPOT[0], REPAIR_SPOT[1],
                                   REPAIR_NEAR_TOLERANCE)


def _wait_arrive(win, prefix):
    """等待跨图寻路到达修理点。

    到达判据（同巫医经验）：位置 ±容差 / NPC 模板可见 / 已读到洛阳城后
    位置变为缺失（实测站在 NPC 旁位置显示会消失）。位置缺失只在「此前
    已确认为洛阳城」时当作到达——跨图途中地图切换也会短暂读不到位置，
    不能直接当到达。途中进战斗：等待战斗结束并顺延截止时间（不按失败）。
    """
    deadline = time.time() + REPAIR_ARRIVE_TIMEOUT
    seen_luoyang = False
    while time.time() < deadline:
        if tab_group.pass_active:
            # 组号 pass 进行中：屏幕可能是队员 Tab，不识别/不点击；
            # pass 是外部等待，不计入到达超时（与战斗等待同语义）
            t0 = time.time()
            time.sleep(0.5)
            deadline += time.time() - t0
            continue
        img = win.capture(force_activate=False)  # 每轮询只截一帧（性能规则 1）
        if img is None:
            time.sleep(0.5)
            continue
        if battle.is_in_battle_frame(img):
            t0 = time.time()
            time.sleep(2.0)
            deadline += time.time() - t0  # 战斗是外部等待，不计入到达超时
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        if game_map.match_frame_np(frame, REPAIR_NPC_ICON,
                                   threshold=REPAIR_NPC_THRESHOLD) is not None:
            return True, ""
        pos = game_map.read_map_position(win, img=img)
        if _near_repair_spot(pos):
            return True, ""
        if pos is not None and pos[0].strip() == LUOYANG:
            seen_luoyang = True
        elif pos is None and seen_luoyang:
            return True, ""  # 站在 NPC 旁位置显示缺失（巫医同款到达兜底）
        time.sleep(2.0)
    return False, f"走到洛阳城修理坐标附近超时（{REPAIR_ARRIVE_TIMEOUT}s，本次取消）"


def _find_repair_npc(win, prefix):
    """到达后 3s 窗口识别修理 NPC 模板；遮挡对话框沿用巫医兜底（点屏关闭）。"""
    pt = None
    deadline = time.time() + REPAIR_NPC_TIMEOUT
    dismissed = False
    while time.time() < deadline:
        if tab_group.pass_active:
            t0 = time.time()
            time.sleep(0.5)
            deadline += time.time() - t0
            continue
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if battle.is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, REPAIR_NPC_ICON,
                                     threshold=REPAIR_NPC_THRESHOLD)
        if pt is not None:
            break
        if not dismissed \
                and dialog_common.dialog_frame_score(img) >= dialog_common.DIALOG_FRAME_THRESHOLD:
            # 其他对话框遮挡 NPC 模板：点屏幕中间关闭一次，窗口顺延 2s
            dialog_common._click_screen_center(win, times=1, prefix=f"[{prefix}][修理] ")
            dismissed = True
            deadline += 2.0
            log(f"[{prefix}][修理] 修理 NPC 被对话框遮挡，已点屏幕中间关闭", "WARN")
        time.sleep(0.5)
    if pt is None:
        return None, f"到达后 {REPAIR_NPC_TIMEOUT}s 内未识别到修理 NPC（本次取消）"
    return pt, ""


def _ocr_dialog_spec(win, img=None):
    """对对话框区域做一次 OCR 并匹配修理弹窗规格。返回 (spec, items, img_np, img)。"""
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return None, None, None, None
    items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    return match_dialog(items), items, img_np, img


def _wait_battle_thread(win, prefix, npc_click_ts):
    """真实流程：轮询战斗线程的选项点击信号（零截图零 OCR）。

    超时后任务侧兜底：OCR 对话框 → 匹配文案 → 定位对应绿色选项 → 自行
    点击（最多 2 次，1s 间隔，与巫医兜底一致）。超时未见对话框/选项
    未识别判失败（本次取消）。
    """
    deadline = time.time() + REPAIR_HANDLE_TIMEOUT
    while time.time() < deadline:
        if battle.last_repair_option_at >= npc_click_ts:
            return True, "自动修理完成"
        time.sleep(0.5)
    # 超时边界复查：战斗线程可能在等待期最后一刻完成点击（预期窗口 15s
    # > 本等待 12s），不复查会重复点击、多余点击落到世界取消寻路
    if battle.last_repair_option_at >= npc_click_ts:
        return True, "自动修理完成"
    if tab_group.pass_active:
        return False, "组号 pass 进行中，跳过选项兜底（本次取消）"
    spec = None
    pt = None
    for _ in range(2):
        spec, items, img_np, img = _ocr_dialog_spec(win)
        if spec is not None:
            text, option = spec
            if battle.last_repair_option_at >= npc_click_ts:
                return True, "自动修理完成"  # 兜底 OCR 期间战斗线程已点
            pt = battle._find_repair_option(items, img_np, option, img=img)
            if pt is not None:
                break
        time.sleep(1.0)
    if pt is None:
        if spec is not None:
            return False, "修理对话框出现，但未识别到选项（本次取消）"
        return False, "点击修理 NPC 后未出现修理对话框（本次取消）"
    text, option = spec
    human_click(win, pt[0], pt[1])
    log(f"[{prefix}][修理] 等待超时，自行点击「{option}」 @ ({pt[0]}, {pt[1]})"
        f"（弹窗: {text[:8]}…）", "WARN")
    battle.record_repair_option_pos(pt, option)
    move_away(win)
    return True, "修理对话框超时未关闭，已自行点击选项"


def _wait_option_self(win, prefix):
    """验证模式：主进程没有战斗线程，自行 OCR 点选项并等对话框关闭。"""
    deadline = time.time() + REPAIR_HANDLE_TIMEOUT
    appeared = False
    clicked = False
    last_text = None
    while time.time() < deadline:
        spec, items, img_np, img = _ocr_dialog_spec(win)
        if spec is not None:
            appeared = True
            text, option = spec
            last_text = text
            if not clicked:
                pt = battle._find_repair_option(items, img_np, option, img=img)
                if pt is not None:
                    human_click(win, pt[0], pt[1])
                    battle.record_repair_option_pos(pt, option)
                    move_away(win)
                    clicked = True
                    log(f"[{prefix}][验证] 已点击「{option}」"
                        f"（弹窗: {text[:8]}…）", "SYS")
            time.sleep(1.0)
            continue
        if appeared:
            return True, f"自动修理完成（弹窗: {last_text[:10]}…）"
        time.sleep(0.5)
    if appeared and clicked:
        return True, f"修理对话框超时未关闭（已点击选项，弹窗: {last_text[:10]}…）"
    if appeared:
        return False, "修理对话框出现，但未识别到选项"
    return False, "点击修理 NPC 后未出现修理对话框"


def _run_repair_chain(win, prefix="修理", hover_before_click=False,
                      click_option_self=False):
    """修理主链路（自动流程与验证共用）：前往洛阳城修理 NPC 并点选项。

    返回 (是否成功, 描述)。失败不阻塞任务：调用方继续本轮任务。
    """
    if not _configured():
        return False, "修理资料未配置（模板/坐标/对话框文案待提供）"
    if tab_group.pass_active:
        # 组号 pass 进行中：屏幕可能是队员 Tab，误点会操作错账号
        return False, "组号 pass 进行中，跳过自动修理"
    if battle.is_in_battle(win):
        return False, "战斗中，跳过自动修理"
    # 1. 位置检查：已在洛阳城修理点附近 → 直接识别 NPC；否则走地图
    pos = game_map.read_map_position(win)
    need_walk = not _near_repair_spot(pos)
    if need_walk:
        if pos is not None and pos[0].strip() == LUOYANG:
            # 同图：直接打开当前地图小地图（免世界地图中转）
            ok, info = game_map.open_mini_map(win)
        else:
            # 跨图（含位置读不到）：世界地图 → 洛阳城 → 洛阳小地图
            ok, info = game_map.open_mini_map_city(
                win, WORLD_MAP_LUOYANG_ICON, LUOYANG)
        if not ok:
            return False, info
        human_click(win, REPAIR_MAP_POS[0], REPAIR_MAP_POS[1])
        log(f"[{prefix}][修理] 已点击洛阳小地图修理位置，自动寻路中", "SYS")
        time.sleep(1.0)
        game_map.close_maps(win)
        ok, info = _wait_arrive(win, prefix)
        if not ok:
            return False, info
    # 2. 到达后 3s 窗口识别 NPC 模板；遮挡对话框点屏兜底
    pt, npc_info = _find_repair_npc(win, prefix)
    if pt is None:
        return False, npc_info
    # 点击前确认没有组号 pass 在进行（否则屏幕是队员 Tab）
    pass_deadline = time.time() + 15.0
    while tab_group.pass_active and time.time() < pass_deadline:
        time.sleep(0.5)
    if tab_group.pass_active:
        return False, "组号 pass 进行中，跳过自动修理"
    if hover_before_click:
        human_move(win.rect.left + pt[0], win.rect.top + pt[1])
        log(f"[{prefix}][验证] 已移动鼠标到修理 NPC ({pt[0]}, {pt[1]})，"
            f"已定位到（验证时序与真实流程一致）", "SYS")
    human_click(win, pt[0], pt[1])
    npc_click_ts = time.time()
    # 通知战斗线程：修理对话框即将出现，切入 2s 高频检查（预期窗口）
    battle.repair_expected_at = npc_click_ts
    log(f"[{prefix}][修理] 已点击修理 NPC ({pt[0]}, {pt[1]})，等待对话框处理", "SYS")
    if click_option_self:
        return _wait_option_self(win, prefix)
    return _wait_battle_thread(win, prefix, npc_click_ts)


def auto_repair(win, mode, prefix="修理"):
    """自动修理（任务流程）：一次性尝试 + 组号队员 pass + 3s 后恢复寻路。

    计数在尝试前重置（用户选择 A：成功/失败都只尝试一次，下次再等
    满间隔）。组号模式：队长点完选项后逐个切队员 Tab 点其弹窗选项，
    切回队长后再等 3s（调用方紧接着进入 follow_task 即重新点寻路）。
    """
    count = battles_today(win, mode)
    mark_attempted(win, mode)
    log(f"[{prefix}][修理] 今日累计 {count} 场战斗达到间隔，前往洛阳城修理", "SYS")
    ok, info = _run_repair_chain(win, prefix=prefix)
    if ok:
        if tab_group.active():
            tab_group.member_repair_pass(win)
        time.sleep(REPAIR_RESUME_WAIT)
    return ok, info


def verify_repair_flow(win, prefix="修理"):
    """验证自动修理链路（主进程调用，不改变任务状态、不动计数）。

    主进程没有战斗线程：验证自行点击选项并等对话框关闭（与任务进程
    战斗线程点击行为等效）。
    """
    return _run_repair_chain(win, prefix=prefix, hover_before_click=True,
                             click_option_self=True)


def verify_tab_repair_flow(win, mode, prefix="修理"):
    """验证组号修理：队长链路 + 队员 Tab 修理 pass。

    前置：窗口内 ≥2 个已登录 Tab（开关状态不校验，force 绕过模式检查）。
    """
    ok, info = _run_repair_chain(win, prefix=prefix, hover_before_click=True,
                                 click_option_self=True)
    if not ok:
        return False, info
    tab_group.init_task(win, mode, True)
    if not tab_group.ready():
        return False, f"{info}；组号模式未激活（需窗口内 ≥2 个已登录 Tab），队员 pass 未执行"
    tab_group.member_repair_pass(win, force=True)
    return True, f"{info}；队员修理 pass 已执行"
