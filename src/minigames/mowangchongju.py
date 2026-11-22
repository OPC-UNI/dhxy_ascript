"""魔王重聚「传音石寻人」小游戏：使用传音石 → 按提示找图/按方向收敛 →
近身触发魔王护卫战斗（2026-11-15 实机全流程标定）。

流程（用户 2026-11-11 规则 + 2026-11-15 实机打通修正）：
1. 任务追踪「使用传音石找到魔王和魔王护卫的位置，并在他附近使用传音石」
   时使用传音石（任务储物格道具；使用实现由任务侧注入 `use_item`，
   避免复制任务道具使用逻辑）→ 屏幕中央提示框（实测文字约
   y≈405-470、持续约 2 秒）：
   - 不在目标地图：「传音石有所感应，魔王应该不在附近，而在大唐边境地图」
   - 目标地图内按方向+距离逐级收敛（实机四档）：
     「传音石微微颤抖，魔王应该在西北很远的地方。」
     「传音石朝着西北方向剧烈震动，应该不远了。」
     「传音石振动得越发激烈，魔王应该就在东方不远处。」
     「传音石几乎崩裂开来，魔王就在附近偏西北方向。」
   - 目标地图内使用时**世界地图会自动打开**；其它地图不打开。
2. 跨图：世界地图点目标地图标签（`game_map.WORLD_MAP_LABELS` 模板）
   打开详情图 → 点普通地形点自动走过去。**必须避开飞行旗图标**
   （任务中不能飞，2026-11-15 用户实机：点到飞行旗提示「不能飞」且
   不移动；普通位置可自动寻路）。
3. 方向引导：当前地图直接点左上位置文字区打开小地图（open_mini_map，
   2026-11-18 用户规则：不经世界地图），以玩家位置红点为原点，按方向
   词 + 距离级别点目标点自动寻路；重复「使用 → 按提示走」直到提示
   「几乎崩裂/就在附近」——近身继续使用即触发 2 个「魔王护卫」战斗
   （战斗由 follow_task/battle 线程托管，本模块不做战斗操作）。
4. 战后追踪变为「已经通过了魔王护卫的考验，请将传音石交给魔王三大王」
   ——该阶段由任务跟踪引擎通用绿字点击 + 任务侧 DIALOG_OPTION_MAP
   （「那老牛派你过来，想说啥？」→「这是牛魔王遣我送来的传音石。」）
   处理，**本模块不参与**（触发词不再命中，道具也已在战斗中用掉）。

与调用方（mingwang 任务侧）的接口：`handle(win, items, img_np,
frame_fn, use_item, prefix)`——extras 链每轮询调用一次；`frame_fn`
提供共享帧（(win, items) → (RGB np, BGR np)，复用同轮询已截帧），
`use_item` 为任务储物格使用传音石的实现（含对话框门与背包锁）。
import 期零副作用；不 import tasks/*。
"""
import re
import time

import cv2
import numpy as np

from ..core.app_utils import human_click, log, norm_text
from ..device import map as game_map
from ..device.map import bgr_frame as _bgr_frame
from ..device import task_tracker  # 追踪面板左区边界（签名只取面板列）
from ..device.battle import is_in_battle_frame  # 用道具后进战斗的判定
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD,
                                    dialog_frame_score)  # 对话框在场门

# 追踪触发词：引导阶段文案「使用传音石找到魔王和魔王护卫的位置，
# 并在他附近使用传音石」——必须排除战后交付文案（「请将传音石交给
# 魔王三大王」也含「传音石」，但交付由引擎/对话表处理，不再用道具）。
MOWANG_TASK_KEYWORDS = ("找到魔王", "使用传音", "在他附近")
MOWANG_ABSENT_RESET = 5        # 触发词连续缺失轮询数 → 状态复位
MOWANG_USE_RETRY_AFTER = 8.0   # 两次使用之间的最小间隔（秒；提示未读出时的
                               # 重试节奏，正常按到达节奏更快）
MOWANG_USE_MAX = 40            # 单任务使用次数安全上限（实测 7 次收敛）
MOWANG_DIALOG_WAIT_LOG = 10.0  # 对话框在场等待日志限频（秒）：处理器每轮询
                               # 都被调用，仅零操作等待（对话由 follow 引擎
                               # 推进），静默会让「卡在对话」无从排查
                               # （2026-11-15 实机：点屏兜底误判导致空转，
                               # 日志完全无迹可寻）
# 中央提示框（2026-11-15 实机标定：文字两行、x≈455-945、y≈405-470；
# 区域 OCR 约 0.1-0.2s，最多轮询 MOWANG_HINT_TIMEOUT 秒）
MOWANG_HINT_REGION = (450, 400, 960, 480)
MOWANG_HINT_POLL = 0.25        # 提示框 OCR 轮询间隔（秒）
MOWANG_HINT_TIMEOUT = 2.5      # 使用后等待提示出现的时限（秒）
MOWANG_MAP_HINT_RE = re.compile(r"而在(.{2,8}?)(?:地图|地圖)")
# 方向词（两字优先：东南/东北/西南/西北 先于单字）
MOWANG_DIR_WORDS = ("东南", "东北", "西南", "西北", "东", "南", "西", "北")
# 距离级别（提示词 → 步长级别；「几乎崩裂」= 到身边触发战斗的最后阶段）
MOWANG_LEVEL_WORDS = (
    ("几乎崩裂", "adjacent"), ("就在附近", "adjacent"),
    ("不远处", "near"), ("不远了", "mid"), ("很远", "far"),
)
# 详情图点击步长（地图像素；大唐边境 2026-11-15 两点标定 ≈0.665 像素/格：
# (620,430)→(234,150)、(480,340)→(141,210)；两轴 0.664/0.667）
MOWANG_STEP_PX = {"far": 70, "mid": 46, "near": 26, "adjacent": 14}
MOWANG_DIR_VEC = {  # 地图像素方向：东=+x、北=-y（上=北，实测）
    "东": (1, 0), "西": (-1, 0), "北": (0, -1), "南": (0, 1),
    "东北": (1, -1), "东南": (1, 1), "西北": (-1, -1), "西南": (-1, 1),
}
# 世界地图详情图图区（窗口相对）与点击点内缩（防点到边框/标题）
MOWANG_MAP_REGION = (290, 240, 1080, 680)
MOWANG_MAP_INSET = 30
# 地图关闭一律走 game_map.close_maps（模板定位关闭钮 + 双帧确认 + 1s
# 动画等待），禁止写死关闭坐标——2026-11-18 实机教训：原先按 2026-11-15
# 标定的固定坐标点击（详情 (1106,219)、世界 (1048,238)），小地图面板
# 尺寸/位置随目标地图变化（同日狮驼岭帧实测面板右缘 ~1005、关闭钮
# (996,214)，固定点已在面板外；历史日志同模板命中也在
# (1030..1111, 202..227) 漂移），
# 固定坐标落到面板外即点到游戏世界，把刚用落点点亮的自动寻路打断
# （用户实机观察：「点了一下地图右上，实际点到地图外边，寻路被打断」）。
# 地图保持打开会让追踪面板 OCR 读到窗口左缘竖排「世界地图」文字，
# follow 引擎误判任务变化并额外点击绿字（2026-11-15 实机 16:05:30
# 日志教训）——地图操作完成后必须关闭。
# 跨图落点候选（详情图普通地形点，按序尝试；避开飞行旗/驿站图标，
# 以「位移启动」为成功信号）
MOWANG_ENTRY_POINTS = ((640, 450), (640, 380), (560, 500), (720, 500),
                       (600, 430))
MOWANG_WALK_START_TIMEOUT = 6.0  # 点击后等待位移启动的时限（秒）
MOWANG_WALK_ARRIVE_POLLS = 3     # 位置连续稳定轮询数判定到达（同封印/镐）
MOWANG_WALK_POS_TOLERANCE = 3    # 坐标稳定容差（像素）
# 判定寻路已启动的位移阈值（格）。必须为 1（任意坐标变化）：
# 2026-11-22 狮驼岭实机——详情图缩放随地图变化（大唐边境≈1.0 px/格、
# 狮驼岭≈5.2 px/格、火云戈壁≈2.9 px/格，实测），adjacent 档 14px
# 在狮驼岭只有 ≈2.7 格，且点完 0.5s 内即到点；旧阈值「>2 才启动」
# 把整次位移（2 格）判成未启动 → 关图重试死循环（08:33-08:37 连续
# 20+ 轮「按提示寻路未启动」，人物实际每轮只挪 2 格）。任意位置
# 变化即算起步，与 map_nav._wait_movement「pos != before」同源。
MOWANG_WALK_MOVE_DELTA = 1
MOWANG_WALK_STALL_TIMEOUT = 12.0 # 位置连续不变多久判寻路停滞（秒）
MOWANG_WALK_MAX = 150.0          # 单次寻路总时限（秒；跨图长途兜底）

# ---- 状态 ----
_use_at = 0.0          # 上次使用时间（最小间隔）
_uses = 0              # 已使用次数（安全上限）
_missing = 0           # 触发词连续缺失轮询数（≥MOWANG_ABSENT_RESET 复位）
_hint_misses = 0       # 连续未读出提示次数（日志/告警）
_dialog_log_at = 0.0   # 对话框在场等待日志上次时间（限频）
_walk = None           # 行走状态（dict，见 _walk_click/_walk_poll）
# 共享帧缓存（frame_fn 未注入时的兜底；按 items 对象 id 缓存）
_FRAME_UNSET = object()
_frame_key = None
_frame_rgb = None
_frame_bgr = None
_frame_pos = _FRAME_UNSET


def reset():
    """状态复位（新任务领取成功/跨任务防残留）。"""
    global _use_at, _uses, _missing, _hint_misses, _walk
    global _dialog_log_at, _frame_key, _frame_rgb, _frame_bgr, _frame_pos
    _use_at = 0.0
    _uses = 0
    _missing = 0
    _hint_misses = 0
    _dialog_log_at = 0.0
    _walk = None
    _frame_key = None
    _frame_rgb = _frame_bgr = None
    _frame_pos = _FRAME_UNSET


def _sig(items):
    """追踪面板文案签名（只取面板左列，排除对话框/浮字；抗 OCR 拆行）。"""
    return "".join(norm_text(t) for t, r, s in items
                   if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X)


def _trigger(sig):
    """追踪面板文案是否触发传音石引导（使用传音石找到魔王...）。"""
    return any(kw in sig for kw in MOWANG_TASK_KEYWORDS)


def _frame(win, items, frame_fn):
    """共享帧（按 id(items) 缓存），返回 (RGB, BGR)。

    frame_fn 由调用方注入（复用同一轮询的截帧，性能规则 1）；未注入时
    本模块自行截帧缓存（测试/独立运行用）。
    """
    global _frame_key, _frame_rgb, _frame_bgr, _frame_pos
    if frame_fn is not None:
        return frame_fn(win, items)
    key = id(items)
    if _frame_key != key:
        img = win.capture(force_activate=False)
        _frame_key = key
        _frame_pos = _FRAME_UNSET
        if img is None:
            _frame_rgb = _frame_bgr = None
        else:
            _frame_rgb = np.asarray(img)[:, :, :3]
            _frame_bgr = cv2.cvtColor(_frame_rgb, cv2.COLOR_RGB2BGR)
    return _frame_rgb, _frame_bgr


def _pos(win, items, frame_fn):
    """当前轮询共享帧上的位置读取（结果按帧缓存，None 也缓存）。"""
    global _frame_pos
    if frame_fn is not None:
        rgb, _ = frame_fn(win, items)
        return None if rgb is None else game_map.read_map_position(
            win, img=rgb)
    rgb, _ = _frame(win, items, frame_fn)
    if _frame_pos is not _FRAME_UNSET:
        return _frame_pos
    _frame_pos = (None if rgb is None
                  else game_map.read_map_position(win, img=rgb))
    return _frame_pos


def _parse_hint(text):
    """解析传音石提示文案，返回 (kind, data)。

    kind：'map'（data=目标地图名或 None）/ 'dir'（data=(方向, 级别)）/
    (None, None)（提示在场但未识别）。
    """
    t = norm_text(text)
    m = MOWANG_MAP_HINT_RE.search(t)
    if m:
        return "map", game_map.resolve_world_map_name(m.group(1))
    level = None
    for word, lv in MOWANG_LEVEL_WORDS:
        if word in t:
            level = lv
            break
    direction = next((w for w in MOWANG_DIR_WORDS if w in t), None)
    if direction is not None or level is not None:
        return "dir", (direction, level or "mid")
    return None, None


def _read_hint(win):
    """使用传音石后读取中央提示框，返回 (kind, data)。

    轮询 MOWANG_HINT_REGION 直到读到含「传音」的提示行或超时；战斗中
    提示框不出现（直接进战斗）——命中战斗帧立即返回 ('battle', None)。
    返回 (None, None) 表示未读到提示（用后被吞/漏帧）。
    """
    deadline = time.time() + MOWANG_HINT_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            if is_in_battle_frame(_bgr_frame(img)):
                return "battle", None
            items, _ = game_map.ocr_region(img, MOWANG_HINT_REGION)
            text = "".join(t for t, _r, s in items if s >= 0.5)
            if "传音" in text and len(text) >= 6:
                kind, data = _parse_hint(text)
                if kind is not None:
                    return kind, data
        time.sleep(MOWANG_HINT_POLL)
    return None, None


def _close_map(win):
    """关闭世界地图/详情地图（best effort）。

    走 game_map.close_maps：模板定位真实关闭钮（面板尺寸/位置随地图
    与拖动变化）+ 双帧确认 + 1s 动画等待（面板已自动关闭或正在渐隐时
    不点击）。详情面板在世界上层时按「小地图 → 世界地图」顺序关闭。
    禁止改回固定坐标——2026-11-18 实机：固定坐标落到面板外点到游戏
    世界，打断刚启动的地图寻路（见上方地图关闭注释）。
    """
    game_map.close_maps(win)


def _find_dot(img):
    """详情图上定位玩家位置红点（红色圆点标记），返回中心坐标或 None。

    2026-11-15 实机：红点约 8×9px、亮红（RGB≈205,0,32），中心偏暗，
    在两张不同地图详情图上形态一致。颜色 + 连通块尺寸过滤（红色地物
    常远大于/小于标记），取尺寸 4~16px、面积 12~160 的最大红色块。
    """
    rgb = np.asarray(img)[:, :, :3].astype(np.int16)
    l, t, r, b = MOWANG_MAP_REGION
    sub = rgb[t:min(b, rgb.shape[0]), l:min(r, rgb.shape[1])]
    if sub.size == 0:
        return None
    red, green, blue = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    mask = ((red > 150) & (green < 110) & (blue < 110)
            & (red - green > 70) & (red - blue > 60))
    if int(mask.sum()) < 12:
        return None
    num, _labels, stats, cents = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), 8)
    best = None
    for i in range(1, num):
        w, h, area = int(stats[i, 2]), int(stats[i, 3]), int(stats[i, 4])
        if not (4 <= w <= 16 and 4 <= h <= 16 and 12 <= area <= 160):
            continue
        if best is None or area > best[0]:
            best = (area, cents[i])
    if best is None:
        return None
    return int(l + best[1][0]), int(t + best[1][1])


def _walk_click(win, x, y):
    """点击详情图目标点并等待位移启动；启动则登记行走状态并返回 True。

    任务中不能飞（2026-11-15 用户实机）：点击飞行旗图标会提示
    「不能飞」且人物不动——位移启动检查同时用于识别这种无效点击。
    位移判定用「任意坐标变化」（≥MOWANG_WALK_MOVE_DELTA=1 格）：
    详情图缩放随地图变化，adjacent 档在狮驼岭只有 ≈2.7 格且到点极快
    （2026-11-22 实测），阈值放宽会把整次短位移判成未启动。
    """
    global _walk
    start = game_map.read_map_position(win)
    human_click(win, x, y)
    deadline = time.time() + MOWANG_WALK_START_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.5)
        cur = game_map.read_map_position(win)
        if cur is None:
            continue
        if start is None:
            start = cur  # 起点识别失败：以首个可读位置为基准
            continue
        moved = (cur[0] != start[0]
                 or abs(cur[1] - start[1]) >= MOWANG_WALK_MOVE_DELTA
                 or abs(cur[2] - start[2]) >= MOWANG_WALK_MOVE_DELTA)
        if moved:
            now = time.time()
            _walk = {"at": now, "last_move_at": now,
                     "from": start, "last": cur, "stable": 0}
            return True
    return False


def _walk_poll(win, items, frame_fn, prefix):
    """行走状态轮询：到达/停滞/超时返回 True（可继续用传音石）。

    到达 = 位置连续 MOWANG_WALK_ARRIVE_POLLS 次稳定（容差 3px，同
    封印/灵力镐）；停滞 = 位置 MOWANG_WALK_STALL_TIMEOUT 秒无变化；
    超时 = MOWANG_WALK_MAX 总时限（跨图长途兜底）。未结束返回 False。
    """
    global _walk
    w = _walk
    if w is None:
        return True
    pos = _pos(win, items, frame_fn)
    now = time.time()
    if pos is None:
        if now - w["at"] > MOWANG_WALK_MAX:
            log(f"{prefix}传音石：寻路超时且位置不可读，重新取提示", "WARN")
            _walk = None
            return True
        return False
    last = w["last"]
    if (last is not None and pos[0] == last[0]
            and abs(pos[1] - last[1]) <= MOWANG_WALK_POS_TOLERANCE
            and abs(pos[2] - last[2]) <= MOWANG_WALK_POS_TOLERANCE):
        w["stable"] += 1
    else:
        w["stable"] = 0
        w["last_move_at"] = now
    w["last"] = pos
    if w["stable"] >= MOWANG_WALK_ARRIVE_POLLS:
        log(f"{prefix}传音石：寻路到达 {pos[0]}[{pos[1]},{pos[2]}]", "INFO")
        _walk = None
        return True
    if now - w["last_move_at"] >= MOWANG_WALK_STALL_TIMEOUT:
        log(f"{prefix}传音石：寻路停滞"
            f"（{pos[0]}[{pos[1]},{pos[2]}] 已 "
            f"{MOWANG_WALK_STALL_TIMEOUT:.0f}s 未移动），重新取提示", "WARN")
        _walk = None
        return True
    if now - w["at"] > MOWANG_WALK_MAX:
        log(f"{prefix}传音石：单次寻路超时（{MOWANG_WALK_MAX:.0f}s），"
            f"重新取提示", "WARN")
        _walk = None
        return True
    return False


def _walk_step(win, direction, level, prefix):
    """当前地图详情图按方向/距离点目标点自动寻路。返回是否启动寻路。

    2026-11-18 用户规则：方向提示只在目标地图内出现，直接打开当前地图
    的小地图即可（点左上位置文字区，open_mini_map），不必先开世界地图
    再点标签——旧路径每轮多两次地图操作，世界地图的关闭点击还可能落到
    面板外打断寻路。
    """
    if game_map.is_world_map_open(win):
        # 使用传音石后世界地图可能自动打开：先关掉，位置区点击才落在 HUD
        game_map.close_world_map(win, quiet=True)
    if not game_map.is_mini_map_open(win):
        ok, info = game_map.open_mini_map(win)
        if not ok:
            log(f"{prefix}传音石：打开当前地图小地图失败（{info}）", "WARN")
            return False
    img = win.capture(force_activate=False)
    dot = _find_dot(img) if img is not None else None
    if dot is None:
        # 红点未识别：退回图区中心（方向步长小，误差由提示反馈收敛）
        dot = ((MOWANG_MAP_REGION[0] + MOWANG_MAP_REGION[2]) // 2,
               (MOWANG_MAP_REGION[1] + MOWANG_MAP_REGION[3]) // 2)
    ox, oy = MOWANG_DIR_VEC[direction]
    step = MOWANG_STEP_PX[level]
    l, t, r, b = MOWANG_MAP_REGION
    x = max(l + MOWANG_MAP_INSET,
            min(r - MOWANG_MAP_INSET, dot[0] + ox * step))
    y = max(t + MOWANG_MAP_INSET,
            min(b - MOWANG_MAP_INSET, dot[1] + oy * step))
    ok = _walk_click(win, x, y)
    if not ok:
        # 半步兜底：起点贴边/目标点撞图标（飞行旗等）时再试一次
        half = max(6, step // 2)
        x2 = max(l + MOWANG_MAP_INSET,
                 min(r - MOWANG_MAP_INSET, dot[0] + ox * half))
        y2 = max(t + MOWANG_MAP_INSET,
                 min(b - MOWANG_MAP_INSET, dot[1] + oy * half))
        ok = _walk_click(win, x2, y2)
    _close_map(win)
    return ok


def _go_map(win, map_name, prefix):
    """世界地图导航到 map_name：点标签 → 详情图点落点候选 → 关地图。

    打开世界地图 → 点标签 → 等详情图出现的整段复用
    `game_map.open_mini_map_by_name`（标签模板统一走 WORLD_MAP_LABELS，
    2026-11-18 用户规则；本模块不再自行截图匹配标签）。落点候选按序
    尝试（避开飞行旗/驿站图标），以「位移启动」为成功信号；未收录
    标签模板或全部候选失败返回 False（稍后重试）。
    """
    if map_name not in game_map.WORLD_MAP_LABELS:
        log(f"{prefix}传音石：地图「{map_name}」未收录标签模板"
            f"（见 game_map.WORLD_MAP_LABELS），无法自动导航", "WARN")
        return False
    ok, info = game_map.open_mini_map_by_name(win, map_name, prefix=prefix)
    if not ok:
        log(f"{prefix}传音石：前往「{map_name}」详情图失败（{info}），"
            f"稍后重试", "WARN")
        _close_map(win)
        return False
    ok = False
    for x, y in MOWANG_ENTRY_POINTS:
        if _walk_click(win, x, y):
            log(f"{prefix}传音石：详情图落点 @({x},{y}) 已启动寻路", "INFO")
            ok = True
            break
    _close_map(win)
    return ok


def handle(win, items, img_np, frame_fn=None, use_item=None,
           prefix="[名望] "):
    """魔王重聚·传音石引导状态机（follow extras 通道，每轮询一次）。

    frame_fn：(win, items) → (RGB np|None, BGR np|None)，共享帧回调
    （复用同轮询截帧）；None 时本模块自行截帧缓存。
    use_item：(win) → (ok, info)，任务储物格使用传音石（含对话框门/
    背包锁），由任务侧注入。未注入时返回 False（不做任何操作）。
    触发：追踪文案含「找到魔王/使用传音/在他附近」（引导阶段）→ 使用
    传音石 → 读中央提示 → 跨图（世界地图点标签 + 点普通地形点自动
    寻路）或按方向/距离在详情图点目标点寻路；重复直到近身——继续使用
    即触发「魔王护卫」战斗（战斗由 follow_task/battle 线程托管）。
    战后交付阶段（「请将传音石交给魔王三大王」）不命中触发词，由
    follow 引擎绿字点击 + 任务对话表处理，本函数零操作。
    返回是否已处理。
    """
    global _missing, _use_at, _uses, _hint_misses, _walk
    global _dialog_log_at
    sig = _sig(items)
    if not _trigger(sig):
        _missing += 1
        if _missing >= MOWANG_ABSENT_RESET:
            reset()
        return False
    _missing = 0
    if use_item is None:
        return False
    if _walk is not None:
        if not _walk_poll(win, items, frame_fn, prefix):
            return False  # 行走中：等到达/停滞
        # 到达/停滞 → 继续本轮使用传音石
    now = time.time()
    if now - _use_at < MOWANG_USE_RETRY_AFTER:
        return False  # 使用最小间隔未到（首次 _use_at=0 立即执行）
    _rgb, bgr = _frame(win, items, frame_fn)
    if bgr is not None and dialog_frame_score(bgr) >= DIALOG_FRAME_THRESHOLD:
        # 对话框在场：交给引擎对话处理，稍后再用道具（限频日志，防静默卡死）
        if now - _dialog_log_at >= MOWANG_DIALOG_WAIT_LOG:
            _dialog_log_at = now
            log(f"{prefix}传音石：对话框在场，等对话推进后再使用", "INFO")
        return False
    if _uses >= MOWANG_USE_MAX:
        if _uses == MOWANG_USE_MAX:
            _uses += 1
            log(f"{prefix}传音石：使用次数达上限 {MOWANG_USE_MAX}，"
                f"停止本次引导（等待任务超时/人工处理）", "WARN")
        return False
    ok, info = use_item(win)
    _use_at = time.time()
    if not ok:
        log(f"{prefix}传音石：{info}，稍后重试", "WARN")
        return False
    _uses += 1
    kind, data = _read_hint(win)
    if kind == "battle":
        log(f"{prefix}传音石：魔王护卫现身，进入战斗（战斗线程托管）", "SYS")
        return True
    if kind == "map":
        if data is None:
            _hint_misses += 1
            log(f"{prefix}传音石：提示目标地图未收录标签模板"
                f"（见 game_map.WORLD_MAP_LABELS），无法自动导航", "WARN")
            _close_map(win)
            return True
        _hint_misses = 0
        log(f"{prefix}传音石：魔王应在{data}，经世界地图前往", "SYS")
        if not _go_map(win, data, prefix):
            log(f"{prefix}传音石：前往{data}未启动寻路，稍后重试", "WARN")
        return True
    if kind == "dir":
        direction, level = data
        _hint_misses = 0
        if direction is None:
            log(f"{prefix}传音石：魔王就在附近，继续使用传音石", "SYS")
            _close_map(win)
            return True
        log(f"{prefix}传音石：魔王在{direction}方（{level}），按提示寻路",
            "SYS")
        if not _walk_step(win, direction, level, prefix):
            log(f"{prefix}传音石：按提示寻路未启动，稍后重试", "WARN")
        return True
    _hint_misses += 1
    log(f"{prefix}传音石：未读到提示（连续 {_hint_misses} 次），稍后重试",
        "WARN")
    _close_map(win)
    return True
