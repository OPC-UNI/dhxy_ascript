"""task_start 顶层兜底测试（2026-10-31 任务线程裸崩、堆栈只留控制台的修复）。

验证点：
- UserAbort（用户人工接管鼠标）→ 记 WARN 后正常返回，不重抛（wrapper
  检测线程结束后执行 task_stop 善后，表现为干净中止）
- 其余未捕获异常 → 完整堆栈经 log 落盘（含日志文件双写）后重抛，
  控制台与文件均保留现场
- 仅战斗模式入口 _battle_only_loop：未登录不启动战斗线程直接结束；
  已登录只启动战斗监控线程（不跑任务侧操作），线程退出后任务收尾
"""
import threading
import time

import pytest

import main
from src import app_utils


def _patch_log(monkeypatch):
    """替换 main.log 收集调用（不落盘不打印）。"""
    calls = []
    monkeypatch.setattr(main, "log",
                        lambda msg, level="INFO": calls.append((msg, level)))
    return calls


def _patch_battle_only_env(monkeypatch, tab):
    """仅战斗路径依赖打桩：登录 Tab、悬浮层、战斗线程、驻留 sleep。"""
    entered = threading.Event()
    monkeypatch.setattr(main.game, "read_login_tab", lambda win: tab)
    monkeypatch.setattr(main.debug_overlay, "start", lambda win: None)
    monkeypatch.setattr(main.debug_overlay, "shutdown", lambda: None)
    monkeypatch.setattr(main.debug_overlay, "set_phase", lambda text: None)
    monkeypatch.setattr(main.debug_overlay, "set_name", lambda text: None)
    monkeypatch.setattr(main.battle, "watch_loop", lambda win: entered.set())
    # 驻留轮询 sleep 缩短（patch 的是 time 模块本身，勿动 threading 内部）
    monkeypatch.setattr(main.time, "sleep", lambda s: time.sleep(0.01))
    return entered


def test_task_start_user_abort_returns_cleanly(monkeypatch):
    calls = _patch_log(monkeypatch)

    def _abort():
        raise app_utils.UserAbort("检测到鼠标位于屏幕角落")

    monkeypatch.setattr(main, "_task_start_body", _abort)
    main.task_start()  # 不应抛出
    assert calls and calls[0][1] == "WARN"
    assert "屏幕角落" in calls[0][0]
    assert "任务中止" in calls[0][0]


def test_task_start_exception_logs_traceback_then_reraises(monkeypatch):
    calls = _patch_log(monkeypatch)

    def _boom():
        raise ValueError("boom-原因细节")

    monkeypatch.setattr(main, "_task_start_body", _boom)
    with pytest.raises(ValueError):
        main.task_start()
    assert calls and calls[0][1] == "ERROR"
    assert "Traceback" in calls[0][0]
    assert "boom-原因细节" in calls[0][0]


def test_battle_only_loop_not_logged_in_skips_battle(monkeypatch):
    calls = _patch_log(monkeypatch)
    entered = _patch_battle_only_env(monkeypatch, main.game.GAME_TITLE)
    main._battle_only_loop(object())  # 不应抛异常，直接返回
    assert not entered.is_set()      # 未登录：战斗线程不启动
    assert any("未登录" in msg for msg, _ in calls)


def test_battle_only_loop_runs_battle_until_thread_exits(monkeypatch):
    calls = _patch_log(monkeypatch)
    entered = _patch_battle_only_env(monkeypatch, "测试角色")
    main._battle_only_loop(object())  # 战斗线程 mock 立即退出，循环随之收尾
    assert entered.is_set()           # 已登录：战斗监控线程已启动
    assert any("仅战斗模式" in msg for msg, _ in calls)
    assert any("战斗监控线程已退出" in msg for msg, _ in calls)
