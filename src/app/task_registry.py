# -*- coding: utf-8 -*-
"""任务模式注册表：执行侧单点登记（模式 → 次数上限检查 → 前置钩子 → 主循环）。

main.task_start 按面板顺序遍历模式时只调用 `run_task_mode`。新增任务
需同步以下位置（一致性均由 tests/test_task_registry.py /
test_control_window.py 锁定）：

1. 本文件 `TASKS` 加一条 `TaskSpec`；
2. `core.settings.TASK_MODES` 与 `TASK_COUNT_DEFAULTS`（次数默认/上限；
   缺失时按不限处理，会无限跑）；
3. `res/ui/control_window.html` 的任务卡与前端 `const TASK_MODES`
   （卡片仍为手写 HTML，本次不改动态渲染）；
4. `core.debug_overlay._STATS_MODES`（悬浮层/统计页的模式归属）。

「仅战斗」是特殊模式（不走任务主循环，由 `main._battle_only_loop`
处理），不在此表内。

位于 app 层（组合根）而非 tasks/：任务模块之间禁止互引（CLAUDE.md
「任务模块隔离」），注册表需要 import 全部任务模块，放到 tasks/ 会触发
tests/test_import_rules.py 的任务互引检查。
"""
from dataclasses import dataclass
from typing import Callable, Optional

from ..core import app_utils
from ..core.app_utils import log
from ..core.settings import load_task_counts
from ..device import dialog_common, task_tracker, team
from ..tasks import (kezhan, mingwang, sanbaihuan, wuhuan, wubei, xiuluo,
                     xuanshu, zhongzu)


def _pre_xuanshu(win):
    """玄枢轶闻前置：退出队伍（单人执行）。

    已有跟踪中的任务时跳过退队直接续跑（退队只服务于领取流程，续跑
    无需检查队伍信息——用户确认）。
    """
    if not task_tracker.has_task(win, xuanshu.TASK_TITLE):
        ok, info = team.leave_or_disband_team(win)
        log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")


def _pre_kezhan(win):
    """客栈前置：退出队伍（单人状态执行）。"""
    ok, info = team.leave_or_disband_team(win)
    log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")


@dataclass(frozen=True)
class TaskSpec:
    """一个任务模式的执行描述。

    mode：面板模式名（与 core.settings.TASK_MODES 一致）。
    run_loop：主循环入口 `(win) -> str`（返回结束原因描述）。
    pre_run：可选前置钩子 `(win) -> None`（退队等），在主循环前执行。
    cleanup：可选任务结束自清理钩子 `(win) -> bool`（残留奇遇弹窗、
        名望面板等无标准边框界面）；返回 True = 本轮检测到并已处理。
        标准对话框/地图/物品栏由 dialog_common.clean_task_leftovers 统一
        清理，钩子只补任务专属残留（2026-11-28 用户需求）。
    """

    mode: str
    run_loop: Callable
    pre_run: Optional[Callable] = None
    cleanup: Optional[Callable] = None


TASKS = (
    TaskSpec("五环", wuhuan.run_loop),
    # 客栈：奇遇弹窗无标准边框，任务结束帧可能残留并遮挡下一任务领取按钮
    TaskSpec("客栈", kezhan.run_loop, _pre_kezhan, kezhan.cleanup_leftovers),
    TaskSpec("修罗", xiuluo.run_loop),
    TaskSpec("五倍", wubei.run_loop),
    TaskSpec("种族比武", zhongzu.run_loop),
    TaskSpec("玄枢轶闻", xuanshu.run_loop, _pre_xuanshu),
    # 名望：第一阶段（种族判定 + 领取入口）；其余种族/流程待补充
    # 结束须关闭山河名望面板（残留面板遮挡后续任务追踪/对话框区）
    TaskSpec("名望", mingwang.run_loop, None, mingwang.cleanup_leftovers),
    TaskSpec("300环", sanbaihuan.run_loop),
)
TASK_MODES = tuple(spec.mode for spec in TASKS)
TASKS_BY_MODE = {spec.mode: spec for spec in TASKS}


def _daily_count_reached(win, mode):
    """当天完成次数已达上限 → 返回跳过说明；否则 None（零游戏操作）。

    次数设置 + 当天进度均为本地持久化读数（与各任务 run_loop 开头同一
    数据源），在退队等前置动作之前判断：客栈已满 20 次时不必先退队
    （用户 2026-11-24：前置退队白耗 ~3s）。次数 0 = 不限；读取异常按
    未达上限处理，不阻断任务执行。
    """
    try:
        max_rounds = load_task_counts().get(mode, 0)
        if max_rounds <= 0:
            return None
        done = app_utils.task_progress_done(win, mode)
    except Exception:  # noqa: BLE001 —— 仅优化判断，读取失败照常执行
        return None
    if done < max_rounds:
        return None
    return f"当天已完成 {done}/{max_rounds} 次，无需再执行"


def run_task_cleanup(win, spec):
    """任务正常结束后的自清理（2026-11-28 用户需求）。

    任务完成次数达到上限直接返回时，可能把对话框/奇遇弹窗/打开的面板
    留给下一任务（实报：10 次客栈后接修罗，残留界面挡住灵兽村领取按钮）。
    这里调 dialog_common.clean_task_leftovers：任务专属钩子（spec.cleanup）
    + 标准对话框 + 地图/物品栏。异常/会话中止路径不调用（用户接管或
    顶号后不得再点游戏）；清理本身的异常只记 WARN，不影响任务队列继续。
    """
    if app_utils.session_abort_reason():
        log(f"[任务] {spec.mode} 会话已中止，跳过自清理", "WARN")
        return False
    extras = []
    if spec.cleanup is not None:
        extras.append((spec.mode, spec.cleanup))
    try:
        ok, info = dialog_common.clean_task_leftovers(
            win, prefix=spec.mode, extra_cleaners=extras)
    except Exception as e:  # noqa: BLE001 —— 卫生动作失败不得阻断任务队列
        log(f"[任务] {spec.mode} 自清理异常: {e}", "WARN")
        return False
    log(f"[任务] {spec.mode} 自清理：{info}", "SYS" if ok else "WARN")
    return ok


def run_task_mode(win, mode):
    """执行单个任务模式（次数上限检查 → 前置钩子 → 主循环 → 自清理）。

    当天次数已达上限时直接返回跳过说明，不做前置动作（退队等）——
    与各任务 run_loop 开头的上限检查结论一致，只是提前到前置之前。
    主循环正常返回后执行任务自清理（run_task_cleanup）；主循环抛异常
    （含 UserAbort/SessionAbort）时不清场，由 task_start 顶层收口。
    未登记的模式（含「仅战斗」）与旧 main._run_task_mode 行为一致：
    WARN 后跳过并返回说明。
    """
    spec = TASKS_BY_MODE.get(mode)
    if spec is None:
        log(f"[任务] {mode} 暂无自动化实现，跳过", "WARN")
        return f"{mode} 暂无自动化实现，跳过"
    reason = _daily_count_reached(win, mode)
    if reason is not None:
        log(f"[任务] {mode} {reason}", "SYS")
        return f"{mode} {reason}"
    if spec.pre_run is not None:
        spec.pre_run(win)
    info = spec.run_loop(win)
    run_task_cleanup(win, spec)
    return info
