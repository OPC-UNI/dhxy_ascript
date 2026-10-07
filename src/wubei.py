"""五倍任务自动化。

当前阶段：仅战斗功能（先增加战斗功能，其余流程后续逐步填充）。
战斗由 battle.watch_loop 线程自动处理——任务启动即运行、任何模式下
生效。

队伍检查（参考修罗模块 xiuluo.py 的流程）：
- 首次检查一次面板打开即取全量信息：身份（双模板比分数）+ 人数
  （等级行计数）+ 成员种族/等级，并更新调试悬浮层「队伍」行
- 非队长：核心流程不执行，任务保持运行仅战斗线程自动操作
- 队长：人数不满时每 10 秒轮询查看队伍面板（顺带更新悬浮层队伍
  信息）；人数满足后继续周期复查（领取流程接入后改为领取）

待实现（后续逐步填充，流程参考修罗模块 xiuluo.py）：
- 领取任务（NPC 与领取点待用户提供实测坐标/模板）
- 寻路跟踪（复用 xuanshu.follow_task）
- 返程路径
"""
import time

from .app_utils import log
from .battle import is_in_battle  # 战斗状态判定（统一在 battle.py 中实现）
from . import team  # 队伍面板身份/人数检查与悬浮层队伍信息更新
from .control_window import load_wubei_min  # 五倍设置：最少人数

# 队长人数不足时的重查间隔（秒）
TEAM_CHECK_RETRY_INTERVAL = 10


def run_loop(win, cycle_interval=3.0):
    """五倍主循环：队伍检查 → 非队长保持运行 / 队长人数不满轮询。

    战斗由战斗监控线程自动操作；领取/寻路/返程流程待实现。
    用户手动停止时任务进程被回收，无需自行退出。
    """
    min_members = load_wubei_min()
    log(f"[五倍] 战斗功能已就绪（最少人数 {min_members}），其余流程待实现", "SYS")
    last_info = None
    while True:
        try:
            if is_in_battle(win):
                time.sleep(cycle_interval)
                continue
            status, info, members = team.check_team_min(win, min_members)
        except Exception as e:
            log(f"[五倍] 队伍检查异常: {e}", "WARN")
            time.sleep(TEAM_CHECK_RETRY_INTERVAL)
            continue
        if status == "not_leader":
            team.update_overlay_team(status, info, members)
            if info != last_info:
                log(f"[五倍] {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
                last_info = info
            # 非队长：不再查看队伍面板，保持运行即可
            while True:
                time.sleep(1)
        # 队长：人数不满轮询（顺带更新悬浮层队伍信息）
        team.update_overlay_team(status, info, members)
        if info != last_info:
            log(f"[五倍] {info}" + ("（持续监控队伍人数）" if status == "wait" else ""),
                "SYS" if status == "ready" else "WARN")
            last_info = info
        time.sleep(TEAM_CHECK_RETRY_INTERVAL)
