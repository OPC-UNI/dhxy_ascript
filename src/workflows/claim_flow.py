"""领取/走位共享流程（批次 1：低风险部分）。

只收敛逐行同构或仅配置不同的部分：任务状态判定、领取选项查找/点击、
领取点地图门控、校验等待与探针、follow 收尾参数封装。入口门控
（`_wait_claim_entry`）、走位到达模型（`_walk_to_claim_spot`）与
`_claim_task` 的任务专属分支仍留在 tasks/。
"""
import time

import numpy as np

from ..core import app_utils
from ..core.app_utils import human_click, log, move_away, norm_text
from ..device import map as game_map
from ..device import task_tracker
from ..device.battle import is_in_battle_frame
from ..device.dialog_common import green_option_center
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
