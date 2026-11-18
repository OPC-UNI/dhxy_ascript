# -*- coding: utf-8 -*-
"""AScript SDK 运行时补丁（集中在此，main.py 导入期应用）。

window_capture_guard
    ascript 1.3.31 的 ``Window.capture()`` 异常白名单只有
    ``(OSError, ValueError, win32gui.error)``，不含 ``win32ui.error``。
    而 ``_capture_client_printwindow`` 在 ``CreateCompatibleDC`` 失败后，
    finally 里 ``mfc_dc.DeleteDC()`` 再抛 ``win32ui.error``——原始异常被
    替换并直接穿透 ``capture()``。截屏在轮询热路径上 0.2~0.5s 一次，
    一次瞬态 GDI 失败就会杀死任务线程（2026-11-18 11:27 修罗返程实机：
    CreateCompatibleDC failed → DeleteDC failed → 任务线程未捕获异常）。

    补丁按 SDK 既有的失败语义把 ``win32ui.error`` 转成 None：所有热循环
    调用方本来就有「None → 等下一轮询重试」分支，瞬态失败可自愈。
"""
import time

# 安装状态与日志节流（故障持续时避免每个轮询都刷 WARN）
_guard_state = {"installed": False, "last_log": 0.0}
_LOG_INTERVAL = 10.0


def install_window_capture_guard(window_cls=None, log_fn=None):
    """给 Window.capture 包一层 ``win32ui.error -> None`` 的兜底。

    Args:
        window_cls: 目标 Window 类；None 时使用 ascript 的 Window。
        log_fn: 可选日志函数 ``(msg, level)``；失败日志按 10s 节流。
    Returns:
        bool: 是否已安装（重复调用幂等，返回 True）。
    """
    if _guard_state["installed"]:
        return True
    if window_cls is None:
        from ascript.windows.window import Window as window_cls
    try:
        import win32ui
    except ImportError:  # 非 Windows / 精简环境：无补丁可打
        return False

    original = window_cls.capture

    def capture_guard(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except win32ui.error as exc:
            now = time.time()
            if (log_fn is not None
                    and now - _guard_state["last_log"] >= _LOG_INTERVAL):
                _guard_state["last_log"] = now
                detail = str(exc)
                context = getattr(exc, "__context__", None)
                if context is not None:  # 保留被 finally 抛错替换的原始失败
                    detail = "%s（原始异常：%s）" % (detail, context)
                try:
                    log_fn("[截屏] Window.capture 失败，按 None 交由调用方重试：%s"
                           % detail, "WARN")
                except Exception:  # 日志失败不得影响补丁语义
                    pass
            return None

    capture_guard.__name__ = "capture"
    capture_guard.__doc__ = getattr(original, "__doc__", None)
    window_cls.capture = capture_guard
    _guard_state["installed"] = True
    return True


def reset_guard_state_for_tests():
    """测试用：恢复未安装状态（模块级状态会跨用例保留）。"""
    _guard_state["installed"] = False
    _guard_state["last_log"] = 0.0
