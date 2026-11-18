# -*- coding: utf-8 -*-
"""sdk_patches：Window.capture 的 win32ui.error 逃逸兜底。

背景：ascript 1.3.31 的 capture() 只捕获 OSError/ValueError/win32gui.error；
_capture_client_printwindow 在 CreateCompatibleDC 失败后，finally 的
DeleteDC 再抛 win32ui.error，异常穿透 capture() 打死任务线程。
补丁把 win32ui.error 转成 None（调用方按 SDK 既有语义重试）。
"""
import win32ui

from src.core import sdk_patches


def _make_window_class(exc=None, ret="frame"):
    """每个用例新类，避免补丁在模块级类上叠加。"""
    class _Win:
        def __init__(self):
            self.calls = 0

        def capture(self, *args, **kwargs):
            self.calls += 1
            if exc is not None:
                raise exc
            return ret

    return _Win


def setup_function(_fn=None):
    sdk_patches.reset_guard_state_for_tests()


def teardown_function(_fn=None):
    sdk_patches.reset_guard_state_for_tests()


def test_win32ui_error_converts_to_none_and_logs():
    logs = []
    cls = _make_window_class(exc=win32ui.error("CreateCompatibleDC failed"))
    assert sdk_patches.install_window_capture_guard(
        cls, lambda msg, level: logs.append((msg, level)))
    assert cls().capture(force_activate=False) is None
    assert len(logs) == 1
    assert logs[0][1] == "WARN" and "按 None" in logs[0][0]


def test_log_keeps_original_exception_context():
    # 复现 SDK 现场：CreateCompatibleDC 失败后 finally 的 DeleteDC
    # 再次抛错，日志需要带上被替换掉的原始异常
    class _Win:
        def capture(self):
            try:
                raise win32ui.error("CreateCompatibleDC failed")
            except win32ui.error:
                raise win32ui.error("DeleteDC failed")

    logs = []
    sdk_patches.install_window_capture_guard(
        _Win, lambda msg, level: logs.append(msg))
    assert _Win().capture() is None
    assert "DeleteDC failed" in logs[0]
    assert "CreateCompatibleDC failed" in logs[0]


def test_failure_log_is_throttled():
    logs = []
    cls = _make_window_class(exc=win32ui.error("DeleteDC failed"))
    sdk_patches.install_window_capture_guard(
        cls, lambda msg, level: logs.append((msg, level)))
    win = cls()
    assert win.capture() is None
    assert win.capture() is None  # 10s 节流窗口内不再刷日志
    assert len(logs) == 1


def test_non_win32ui_exception_propagates():
    cls = _make_window_class(exc=RuntimeError("boom"))
    sdk_patches.install_window_capture_guard(cls)
    try:
        cls().capture()
    except RuntimeError:
        pass
    else:
        raise AssertionError("非 win32ui.error 不应被吞")


def test_normal_capture_passthrough():
    cls = _make_window_class(ret="frame")
    sdk_patches.install_window_capture_guard(cls)
    win = cls()
    assert win.capture(force_activate=False) == "frame"
    assert win.calls == 1


def test_install_is_idempotent():
    cls = _make_window_class()
    assert sdk_patches.install_window_capture_guard(cls) is True
    assert sdk_patches.install_window_capture_guard(cls) is True
    # 只包装一层：正常调用仍返回原值，不会因重复安装递归
    assert cls().capture() == "frame"
