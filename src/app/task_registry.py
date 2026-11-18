# -*- coding: utf-8 -*-
"""任务模式注册表：执行侧单点登记（模式 → 前置钩子 → 主循环）。

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

from ..core.app_utils import log
from ..device import task_tracker, team
from ..tasks import (kezhan, mingwang, wuhuan, wubei, xiuluo, xuanshu,
                     zhongzu)


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
    """

    mode: str
    run_loop: Callable
    pre_run: Optional[Callable] = None


TASKS = (
    TaskSpec("五环", wuhuan.run_loop),
    TaskSpec("客栈", kezhan.run_loop, _pre_kezhan),
    TaskSpec("修罗", xiuluo.run_loop),
    TaskSpec("五倍", wubei.run_loop),
    TaskSpec("种族比武", zhongzu.run_loop),
    TaskSpec("玄枢轶闻", xuanshu.run_loop, _pre_xuanshu),
    # 名望：第一阶段（种族判定 + 领取入口）；其余种族/流程待补充
    TaskSpec("名望", mingwang.run_loop),
)
TASK_MODES = tuple(spec.mode for spec in TASKS)
TASKS_BY_MODE = {spec.mode: spec for spec in TASKS}


def run_task_mode(win, mode):
    """执行单个任务模式（前置钩子 + 主循环），返回结束原因描述。

    未登记的模式（含「仅战斗」）与旧 main._run_task_mode 行为一致：
    WARN 后跳过并返回说明。
    """
    spec = TASKS_BY_MODE.get(mode)
    if spec is None:
        log(f"[任务] {mode} 暂无自动化实现，跳过", "WARN")
        return f"{mode} 暂无自动化实现，跳过"
    if spec.pre_run is not None:
        spec.pre_run(win)
    return spec.run_loop(win)
