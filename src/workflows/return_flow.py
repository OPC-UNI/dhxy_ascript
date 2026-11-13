"""返程流程编排（五倍 / 修罗 / 种族比武共用）。

任务差异全部由构造参数注入：回程道具模板/名称、目标地图名、开箱重试
次数、传送后位置识别策略，以及任务侧已有的开箱/模板检查函数。返程
机制（背包/地图/点击）仍在 device 层；本模块只负责流程顺序与日志。
"""
import time
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.system import R

from ..core import app_utils
from ..core.app_utils import human_click, log
from ..device import bag
from ..device import map as game_map
from ..device import tab_group
from ..device.battle import is_in_battle

# 任务储物格按钮双模板（选中/未选中态）与搜索参数
BOX_TASK_ICON = "box_task.png"
BOX_TASK_SELECTED_ICON = "box_task_selected.png"
TASK_BOX_THRESHOLD = 0.8
TASK_BOX_TIMEOUT = 10
TASK_BOX_OFFSET = (351, 305)
TASK_BOX_BAND_MARGIN = 55
# 返程道具等待与轮询
RETURN_ITEM_TIMEOUT = 15
RETURN_POLL_INTERVAL = 0.5
# 等待战斗结束
BATTLE_END_POLL = 2.0
# 传送后位置识别窗口（实测加载超 3s 时 3s 时限会失败）
POSITION_WAIT = 5


def wait_battle_end(win, timeout=60):
    """等待当前战斗结束（战斗操作由 battle.watch_loop 线程完成）。"""
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


def open_task_box(win, prefix):
    """打开背包并确保任务储物格已打开。返回 (是否成功, 描述)。

    双模板比分数判定（选中态/未选中态互相弱匹配）；以 bag_box 为锚点
    限定搜索条带，带区全未命中才全帧兜底。每轮询只截一帧。
    """
    if not bag.is_bag_open(win):
        ok, info = bag.open_bag(win)
        if not ok:
            return False, f"打开背包失败: {info}"
    else:
        log(f"{prefix} 背包已打开，跳过开包", "INFO")
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
        sel_score, _ = game_map.best_match_np(
            frame, BOX_TASK_SELECTED_ICON, region=region)
        task_score, task_pt = game_map.best_match_np(
            frame, BOX_TASK_ICON, region=region)
        if sel_score < TASK_BOX_THRESHOLD and task_score < TASK_BOX_THRESHOLD \
                and region is not None:
            # 带区全未命中：全帧兜底（仅此分支触发）
            sel_score, _ = game_map.best_match_np(
                frame, BOX_TASK_SELECTED_ICON)
            task_score, task_pt = game_map.best_match_np(
                frame, BOX_TASK_ICON)
        if sel_score >= TASK_BOX_THRESHOLD and sel_score > task_score:
            return True, "任务储物格已在背包中打开"
        if task_score >= TASK_BOX_THRESHOLD:
            if not clicked:
                human_click(win, task_pt[0], task_pt[1])
                log(f"{prefix} 已点击任务储物格按钮，等待打开...", "SYS")
                clicked = True
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        time.sleep(RETURN_POLL_INTERVAL)
    if clicked:
        return False, "已点击任务储物格按钮，但未检测到选中态（打开失败）"
    return False, "背包中未找到任务储物格按钮"


def _template_present(icon, template_exists=None):
    if template_exists is not None:
        return template_exists(icon)
    try:
        return Path(R.img(icon)).exists()
    except Exception:
        # R.img 对不存在的模板会打印警告，GBK 控制台下可能抛异常
        return False


def find_return_item(win, icon, label, template_exists=None):
    """在已打开的任务储物格中查找返程道具，返回 (pt 或 None, 描述)。"""
    if not _template_present(icon, template_exists):
        return None, f"{label}模板未提供（待补充 {icon}）"
    deadline = time.time() + RETURN_ITEM_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, icon)
        if pt is not None:
            return pt, f"已在任务储物格中找到{label}"
        time.sleep(RETURN_POLL_INTERVAL)
    return None, f"任务储物格中未找到{label}"


def use_return_item(win, icon, label, target, prefix,
                    bag_closed_label=None, template_exists=None):
    """识别返程道具并右键使用，返回 (是否成功, 描述)。"""
    pt, info = find_return_item(
        win, icon, label, template_exists=template_exists)
    if pt is None:
        return False, info
    # 保险：右键前确认背包仍打开（持背包锁下不可达，防未来遗漏锁）
    if not bag.is_bag_open(win):
        return False, (f"右键{bag_closed_label or label}前背包已被关闭，"
                       f"未使用（下轮重试）")
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"{prefix} 已右键使用{label} ({pt[0]}, {pt[1]})，返回{target}", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, f"已右键使用{label}"


def close_return_panels(win, prefix, optimized=False, failure_detail=True):
    """返程收尾：关闭世界地图、小地图、背包（尽力而为，失败仅记日志）。

    optimized=False：任一打开才走完整关闭流程（五倍旧行为）。
    optimized=True：地图未打开时跳过 close_maps 的双帧确认（修罗/种族比武）。
    """
    img = win.capture(force_activate=False)
    if optimized:
        if img is None:
            game_map.close_maps(win)
            ok, info = bag.close_bag(win)
            _log_close(ok, info, prefix, failure_detail)
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
            log(f"{prefix} 世界地图/小地图/背包均未打开，无需关闭", "INFO")
            return
        if maps_open:
            game_map.close_maps(win)
        if bag_open:
            ok, info = bag.close_bag(win)
            _log_close(ok, info, prefix, failure_detail)
        return
    if img is not None:
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        found = game_map.match_frame_multi(
            frame, (game_map.WORLD_MAP_OPEN_ICON,
                    game_map.MINI_MAP_OPEN_ICON,
                    bag.BAG_BOX_ICON))
        if not any(found.values()):
            log(f"{prefix} 世界地图/小地图/背包均未打开，无需关闭", "INFO")
            return
    game_map.close_maps(win)
    ok, info = bag.close_bag(win)
    _log_close(ok, info, prefix, failure_detail)


def _log_close(ok, info, prefix, failure_detail):
    if ok:
        log(f"{prefix} {info}", "SYS")
    elif failure_detail:
        log(f"{prefix} 背包关闭失败: {info}", "WARN")
    else:
        log(f"{prefix} {info}", "WARN")


class ReturnFlow:
    """一个任务模式的返程流程实例。"""

    def __init__(self, *, prefix, item_icon, item_label, target_label,
                 success_info, open_box, open_box_retries=1,
                 open_box_retry_delay=2.0, read_position=True,
                 pos_poll_interval=RETURN_POLL_INTERVAL, post_sleep=0.0,
                 preflight=None, template_exists=None,
                 close_optimized=True, close_failure_detail=True,
                 bag_closed_label=None):
        self.prefix = prefix
        self.item_icon = item_icon
        self.item_label = item_label
        self.target_label = target_label
        self.success_info = success_info
        self.open_box = open_box
        self.open_box_retries = open_box_retries
        self.open_box_retry_delay = open_box_retry_delay
        self.read_position = read_position
        self.pos_poll_interval = pos_poll_interval
        self.post_sleep = post_sleep
        self.preflight = preflight
        self.template_exists = template_exists
        self.close_optimized = close_optimized
        self.close_failure_detail = close_failure_detail
        self.bag_closed_label = bag_closed_label

    def run(self, win):
        """执行返程，返回 (是否成功, 描述)。"""
        # 前置：确保战斗已结束（意外遭遇战会遮挡背包按钮）
        if is_in_battle(win):
            ok, info = wait_battle_end(win, timeout=60)
            if not ok:
                return False, info
        if self.preflight is not None:
            err = self.preflight()
            if err:
                return False, err
        with app_utils._BAG_LOCK:
            ok = False
            info = ""
            for attempt in range(self.open_box_retries):
                ok, info = self.open_box(win)
                if ok:
                    break
                if attempt < self.open_box_retries - 1:
                    log(f"{self.prefix} 背包打开失败（第 {attempt + 1} 次）："
                        f"{info}，等待结算过渡后重试", "WARN")
                    time.sleep(self.open_box_retry_delay)
            if not ok:
                return False, info
            ok, info = use_return_item(
                win, self.item_icon, self.item_label, self.target_label,
                self.prefix, bag_closed_label=self.bag_closed_label,
                template_exists=self.template_exists)
            if not ok:
                # 道具未使用：也关闭背包与地图保持界面干净；下一轮领取
                # 会走世界地图寻路兜底
                close_return_panels(
                    win, self.prefix, optimized=self.close_optimized,
                    failure_detail=self.close_failure_detail)
                return False, info
            close_return_panels(
                win, self.prefix, optimized=self.close_optimized,
                failure_detail=self.close_failure_detail)
        if self.read_position:
            deadline = time.time() + POSITION_WAIT
            pos = None
            while time.time() < deadline:
                pos = game_map.read_map_position(win)
                if pos is not None:
                    break
                time.sleep(self.pos_poll_interval)
            if pos is not None:
                log(f"{self.prefix} 返程后位置: "
                    f"{pos[0]} {pos[1]},{pos[2]}", "SYS")
            else:
                log(f"{self.prefix} 返程后位置识别失败（不影响本轮返程）",
                    "INFO")
        else:
            time.sleep(self.post_sleep)
        return True, self.success_info
