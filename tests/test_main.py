"""task_start 顶层兜底测试（2026-10-31 任务线程裸崩、堆栈只留控制台的修复）。

验证点：
- UserAbort（用户人工接管鼠标）→ 记 WARN 后正常返回，不重抛（wrapper
  检测线程结束后执行 task_stop 善后，表现为干净中止）
- 其余未捕获异常 → 完整堆栈经 log 落盘（含日志文件双写）后重抛，
  控制台与文件均保留现场
"""
import pytest

import main
from src import app_utils


def _patch_log(monkeypatch):
    """替换 main.log 收集调用（不落盘不打印）。"""
    calls = []
    monkeypatch.setattr(main, "log",
                        lambda msg, level="INFO": calls.append((msg, level)))
    return calls


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
