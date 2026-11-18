"""领取/走位共享流程。

收敛逐行同构或仅配置不同的部分：任务状态判定、领取选项查找/点击、
领取点地图门控、校验等待与探针、地图感知导航/重走领取点、follow 收尾
参数封装。入口门控（`_wait_claim_entry`）、走位到达模型
（`_walk_to_claim_spot`）与 `_claim_task` 的任务专属分支仍留在 tasks/。
"""
import time

import cv2
import numpy as np

from ..core import app_utils
from ..core.app_utils import human_click, log, move_away, norm_text
from ..device import map as game_map
from ..device import task_tracker
from ..device.battle import is_in_battle, is_in_battle_frame
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD,
                                    dialog_frame_score,
                                    green_option_center)
from .follow import follow_task

_UNSET = object()

# 领取校验轮询间隔：五倍/修罗统一 0.8s（2026-11-13 用户指定；原五倍 1.0）。
# 0.8s 下 OCR 占空比 ~50%、CPU 占用低，检测粒度由任务块 2x 放大重识别补偿。
CLAIM_VERIFY_POLL_INTERVAL = 0.8


def task_state(win, title, fallbacks):
    """一次截帧判定续跑状态：追踪面板是否有目标任务。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if is_in_battle_frame(img):
        return False
    if not task_tracker.panel_visible(img=img):
        return False
    items, img_np = task_tracker.ocr_track(img)
    return task_tracker.has_task_in(items, img_np, title, fallbacks)


def probe_task_block(win, title, fallbacks, region_fn):
    """小区域探针：新截帧 + 任务块 OCR，返回 (是否渲染, 探针耗时)。"""
    t0 = time.time()
    img = win.capture(force_activate=False)
    if img is None:
        return False, time.time() - t0
    img_np = np.asarray(img)
    items, _ = game_map.ocr_region(img, region_fn(img_np))
    hit = task_tracker.has_task_in(items, img_np, title, fallbacks)
    return hit, time.time() - t0


def claim_verify_wait(win, pre_sig, t_opt, polls, *, prefix, interval,
                      probe_fn):
    """校验轮询等待：探针命中即跳过等待，未命中补足 interval。"""
    if pre_sig is not None:
        time.sleep(interval)
        return
    t0 = time.time()
    hit, probe_dt = probe_fn(win)
    log(f"{prefix}[领取计时] 任务块探针"
        f"{'命中（立即校验）' if hit else '未命中'}"
        f" @选项点击后 {time.time() - t_opt:.2f}s"
        f"（第 {polls} 轮校验后，探针耗时 {probe_dt:.2f}s）", "INFO")
    if hit:
        return
    time.sleep(max(0.0, interval - (time.time() - t0)))


def find_claim_option(items, img_np, needles):
    """查找领取对话框绿色选项，返回 (cx, cy, txt, 是否兜底) 或 None。

    needles 已规范化（去标点），按顺序匹配；命中最后一个视为兜底。
    """
    for idx, needle in enumerate(needles):
        for txt, rect, score in items:
            ntxt = norm_text(txt)
            if score >= 0.55 and needle and needle in ntxt \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                cx, cy = green_option_center(
                    img_np, rect, len(needle), len(ntxt))
                return cx, cy, txt, idx == len(needles) - 1
    return None


# 到达领取点后等待「自动弹出任务对话框」的短窗口（用户 2026-11-16）：
# 小地图勾选「任务」筛选时，点任务目标点走位到附近可能自动触发领取
# 对话框，此时领取入口按钮/NPC 可能被对话框遮挡——先查对话框直接点选项。
AUTO_DIALOG_GRACE = 1.5
AUTO_DIALOG_POLL = 0.5


def wait_auto_claim_dialog(win, needles, timeout=AUTO_DIALOG_GRACE,
                           interval=AUTO_DIALOG_POLL):
    """短窗口检查任务对话框是否已自动弹出；命中返回 (items, img_np)。

    2026-11-16 用户提示：修罗/五倍走小地图点任务目标点时，若小地图底部
    「任务」筛选已勾选，到达目标点后游戏可能自动弹出领取对话框。命中
    绿色选项时直接复用该帧点选项，跳过领取入口（按钮/NPC）点击——入口
    可能已被对话框遮挡或已无必要。未命中返回 None，调用方按原入口流程
    继续；timeout=0 时只查一帧（点入口前/入口未出现时的即时判定）。
    """
    deadline = time.time() + timeout
    while True:
        img = win.capture(force_activate=False)
        if img is not None:
            # 对话框模板门控（~9ms）：无标准对话框边框直接跳过对话区
            # OCR（空场景 OCR ~150-600ms，用户 2026-11-16 要求加门控）。
            # 标准对话框（领取等）模板分实测 ≥0.98；未命中不解锁 OCR。
            if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
                items, img_np = game_map.ocr_region(
                    img, task_tracker.DIALOG_REGION)
                if find_claim_option(items, img_np, needles) is not None:
                    return items, img_np
        if time.time() >= deadline:
            return None
        time.sleep(interval)


def click_claim_option(win, items, img_np, *, needles, prefix,
                       cache_key=None):
    """点击领取选项；成功后刷新高性能模式缓存（cache_key 为 None 不缓存）。"""
    found = find_claim_option(items, img_np, needles)
    if found is None:
        return False
    cx, cy, txt, fallback = found
    human_click(win, cx, cy)
    if cache_key is not None:
        app_utils.fixed_pos_set(cache_key, win, cx, cy)
    suffix = "（兜底匹配）" if fallback else ""
    log(f"{prefix} 已点击绿色选项「{txt}」{suffix}", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def in_claim_map(pos, map_name):
    """位置是否在领取地图（领取入口模板跨图误命中门控）。"""
    return pos is not None and pos[0].strip() == map_name


def near_claim_spot(pos, map_name, spot, tolerance):
    """位置是否在领取点附近；spot 未标定时一律 False。"""
    if spot is None:
        return False
    return game_map.is_at_position(
        pos, map_name, spot[0], spot[1], tolerance)


def open_claim_map(win, pos=None, *, map_name, world_icon, map_pos, prefix,
                   on_filter_state=None, click_fn=None):
    """地图感知导航到领取地图的小地图领取点（世界地图/小地图自动选择）。

    位置读为 map_name → 直接打开小地图；其他地图或位置不可读 → 世界
    地图点击地图图标。随后点击小地图领取点坐标寻路。

    pos：调用方刚读到的位置（同帧复用，省一次位置 OCR；战斗判定仍需
    本函数截一帧）；None 时自行截帧读取。

    on_filter_state：读取小地图「任务」筛选状态后回调 True/False/None
    （五倍/修罗据此决定到达时是否检查自动弹出的领取对话框）；不传则
    不读取（种族比武无自动对话框路径）。

    click_fn：小地图点击实现（如种族比武的坐标夹取）；None 时直接按
    map_pos 绝对坐标点击。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    if is_in_battle_frame(img):
        return False, "战斗中，跳过地图导航"
    if pos is None:
        pos = game_map.read_map_position(win, img=img)
    if pos is not None and pos[0].strip() == map_name:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    else:
        if pos is not None:
            log(f"{prefix} 不在{map_name}"
                f"（{pos[0].strip()}[{pos[1]},{pos[2]}]），"
                f"经世界地图前往{map_name}", "SYS")
        ok, info = game_map.open_mini_map_city(win, world_icon, map_name)
        if not ok:
            return False, info
    if on_filter_state is not None:
        # 记录小地图「任务」筛选状态（用户 2026-11-16）：仅勾选时小地图
        # 走位到达可能自动弹领取对话框；未勾选则不做自动对话框检查。
        # 模板匹配勾选框（跨场景通用，~10ms），地图窗口此刻仍打开。
        state = game_map.minimap_task_filter_state(win)
        on_filter_state(state)
        if state is True:
            log(f"{prefix} 小地图「任务」筛选已勾选"
                f"（到达可能自动弹领取对话框）", "INFO")
        elif state is False:
            log(f"{prefix} 小地图「任务」筛选未勾选"
                f"（跳过自动对话框检查）", "INFO")
        else:
            log(f"{prefix} 小地图「任务」筛选状态未知（仍做门控检查）",
                "INFO")
    # 小地图窗口位置固定（用户 2026-11-16 规则：即使被拖走，下次打开也
    # 会恢复到默认位置）→ 直接点绝对坐标，不做锚点相对换算（关闭按钮
    # 所在面板宽度随状态变化，锚点不是稳定的内容参照，实机 19:04 实测
    # 锚点从 (1030,217) 漂到 (1011,229)，相对点击会整体偏移）。
    if click_fn is not None:
        # 自定义点击实现自行负责点击日志（如种族比武坐标夹取会打印实际
        # 点击点）；共享层不再重复打印未夹取的原始坐标，避免两份真相。
        click_fn(win)
    else:
        human_click(win, map_pos[0], map_pos[1])
        log(f"{prefix} 已点击{map_name}小地图领取点位置 {map_pos}", "SYS")
    return True, f"已点击{map_name}小地图领取点位置"


# 重走领取点：点击后等待自动寻路启动，再按 2s 轮询等待到达信号。
RELOCATE_START_WAIT = 1.0
RELOCATE_POLL_INTERVAL = 2.0


def relocate_to_claim_spot(win, *, prefix, map_name, open_map_fn, close_map_fn,
                           arrival_fn, timeout):
    """小地图点击领取点坐标重新寻路（领取校验兜底）。

    共享循环骨架：战斗中跳过 → 地图感知导航 → 关地图（close_map_fn
    返回 True 表示领取对话框已自动弹出、地图被模态保留，直接按到达
    处理）→ 轮询等待到达。到达判据由任务注入：
    arrival_fn(win, img, frame, pos) -> 描述或 None，命中即按到达返回。

    每轮询只截一帧：img 为 PIL 帧（对话框判定用）、frame 为 BGR 帧
    （模板匹配用）、pos 为同帧地图位置（OCR 同帧复用）。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过重走"
    ok, info = open_map_fn(win)
    if not ok:
        return False, info
    log(f"{prefix} 已点击{map_name}小地图领取点位置，"
        f"重新寻路（领取校验兜底）", "SYS")
    time.sleep(RELOCATE_START_WAIT)
    # 模态领取对话框会拦住关地图点击，命中则保留地图直接按到达处理
    # （选项点掉后由任务侧 _claim_task 补关）。
    if close_map_fn(win):
        return True, "已重走到领取点附近（领取对话框已自动弹出）"
    deadline = time.time() + timeout
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RELOCATE_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            time.sleep(RELOCATE_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pos = game_map.read_map_position(win, img=img)  # 同帧复用
        desc = arrival_fn(win, img, frame, pos)
        if desc:
            return True, desc
        polls += 1
        if polls % 5 == 0:
            log(f"{prefix} 重走领取点中... 当前位置: {pos or '识别失败'}",
                "INFO")
        time.sleep(RELOCATE_POLL_INTERVAL)
    return False, "重走领取点超时"


def follow_to_done(win, first_items=None, *, title, prefix,
                   extra_dialog_handler, fallback_keywords,
                   skip_texts=_UNSET, story_known_texts=_UNSET,
                   pre_click_handler=_UNSET, post_track_handler=_UNSET,
                   task_gone_polls=_UNSET, battle_extras_tpl_gate=True,
                   recovery=None, recovery_window=30.0,
                   battle_end_handler=_UNSET):
    """寻路 + 对话开打 + 战斗：跟踪任务直到本轮完成。

    可选 recovery(win) -> (ok, info)：仅在首次跟踪（first_items 非空）、
    失败原因为「任务内容中未找到绿色文案」且仍在 recovery_window 内时
    调用一次，用于修罗的领取点补偿重试。
    battle_end_handler：透传 follow_task 的战斗结束回调（修罗战斗失败
    判定用），默认不传、其它任务零影响。
    """
    def _kwargs(include_first):
        kwargs = dict(prefix=prefix,
                      extra_dialog_handler=extra_dialog_handler,
                      fallback_keywords=fallback_keywords,
                      battle_extras_tpl_gate=battle_extras_tpl_gate)
        if include_first:
            kwargs["first_items"] = first_items
        if skip_texts is not _UNSET:
            kwargs["skip_texts"] = skip_texts
        if story_known_texts is not _UNSET:
            kwargs["story_known_texts"] = story_known_texts
        if pre_click_handler is not _UNSET:
            kwargs["pre_click_handler"] = pre_click_handler
        if post_track_handler is not _UNSET:
            kwargs["post_track_handler"] = post_track_handler
        if task_gone_polls is not _UNSET:
            kwargs["task_gone_polls"] = task_gone_polls
        if battle_end_handler is not _UNSET:
            kwargs["battle_end_handler"] = battle_end_handler
        return kwargs

    t0 = time.time()
    ok, info = follow_task(win, title, **_kwargs(True))
    if not ok and first_items is not None and recovery is not None \
            and "任务内容中未找到绿色文案" in info \
            and time.time() - t0 < recovery_window:
        log(f"[{prefix}] {info}，走到领取点补偿重试跟踪（一次）", "WARN")
        ok2, info2 = recovery(win)
        if not ok2:
            return False, f"{info}；走到领取点失败: {info2}"
        ok, info = follow_task(win, title, **_kwargs(False))
    return ok, info
