"""app_utils 输入哨兵测试（2026-10-31 fail-safe 致任务线程裸崩的修复）。

验证点：
- Mouse 调用抛 pydirectinput.FailSafeException → 转 UserAbort（用户中断哨兵）
- 非 fail-safe 异常原样穿透，不被误转
- 消息兜底：后端替换/异常被包装后仍按 "fail-safe" 文案识别
- UserAbort 继承 BaseException：任务循环的 except Exception 兜底不吞它
"""
import pytest
from pydirectinput import FailSafeException

from src import app_utils


class _FakeMouse:
    """可编程 Mouse 替身（monkeypatch 到 app_utils.Mouse）。"""

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def mouse_move(self, x, y, move_duration=None):
        self.calls.append(("mouse_move", x, y))
        if self.exc is not None:
            raise self.exc

    def click(self, button="primary"):
        self.calls.append(("click", button))
        if self.exc is not None:
            raise self.exc


class _FakeWin:
    """human_click 坐标换算所需的最小窗口替身。"""

    def __init__(self, left=678, top=68):
        self.rect = type("Rect", (), {"left": left, "top": top})()


@pytest.fixture
def fake_mouse(monkeypatch):
    fake = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake)
    return fake


def test_human_move_failsafe_converts_to_user_abort(fake_mouse):
    fake_mouse.exc = FailSafeException("PyDirectInput fail-safe triggered")
    with pytest.raises(app_utils.UserAbort) as ei:
        app_utils.human_move(100, 200)
    assert "人工接管" in str(ei.value)


def test_human_move_failsafe_message_fallback(fake_mouse):
    # 类型信号丢失（后端替换/包装）时按消息兜底识别
    fake_mouse.exc = RuntimeError("PyAutoGUI fail-safe triggered from mouse")
    with pytest.raises(app_utils.UserAbort):
        app_utils.human_move(100, 200)


def test_human_move_other_exception_passthrough(fake_mouse):
    fake_mouse.exc = RuntimeError("unrelated")
    with pytest.raises(RuntimeError):
        app_utils.human_move(100, 200)


def test_human_move_normal_path(fake_mouse):
    app_utils.human_move(100, 200, duration=0.05)
    assert fake_mouse.calls == [("mouse_move", 100, 200)]


def test_human_click_failsafe_converts(fake_mouse):
    fake_mouse.exc = FailSafeException("fail-safe")
    with pytest.raises(app_utils.UserAbort):
        app_utils.human_click(_FakeWin(), 300, 400)


def test_precise_click_failsafe_converts(fake_mouse):
    fake_mouse.exc = FailSafeException("fail-safe")
    with pytest.raises(app_utils.UserAbort):
        app_utils.precise_click(_FakeWin(), 300, 400)


def test_user_abort_is_base_exception_not_swallowed():
    # 继承 BaseException 是设计契约：except Exception 兜底不得吞用户中断
    assert issubclass(app_utils.UserAbort, BaseException)
    assert not issubclass(app_utils.UserAbort, Exception)
    with pytest.raises(app_utils.UserAbort):
        try:
            raise app_utils.UserAbort("人工接管")
        except Exception:  # noqa: BLE001 —— 本测试专门验证不被该分支吞掉
            pytest.fail("except Exception 不应吞掉 UserAbort")


# ---- 静默点击模式（2026-11-03 静默修罗领取/巫医：点击走消息通道） ----

def test_silent_clicks_context_restores_flag():
    assert app_utils._SILENT_CLICK is False
    with app_utils.silent_clicks():
        assert app_utils._SILENT_CLICK is True
    assert app_utils._SILENT_CLICK is False


def test_human_click_silent_mode_delegates(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr("src.silent_click.send_silent_click",
                        lambda w, x=None, y=None, button="primary":
                        calls.append((w, x, y, button)))
    fake_mouse = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake_mouse)
    win = _FakeWin()
    app_utils.human_click(win, 100, 200)
    app_utils.human_click(win, 50, 60, button="secondary")
    assert calls == [(win, 100, 200, "primary"), (win, 50, 60, "secondary")]
    assert fake_mouse.calls == []  # 物理鼠标零操作


def test_precise_click_silent_mode_repeats(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr("src.silent_click.send_silent_click",
                        lambda w, x=None, y=None, button="primary": calls.append((x, y)))
    monkeypatch.setattr(app_utils, "Mouse", _FakeMouse())
    app_utils.precise_click(_FakeWin(), 10, 20, times=2, interval=0)
    assert calls == [(10, 20), (10, 20)]


def test_human_move_and_move_away_silent_mode_noop(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    fake_mouse = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake_mouse)
    app_utils.human_move(123, 456)
    app_utils.move_away(_FakeWin())
    assert fake_mouse.calls == []


def test_keyboard_ok_not_silent_always_true(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", False)
    assert app_utils.keyboard_ok(None) is True


def test_keyboard_ok_silent_no_win_false(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    assert app_utils.keyboard_ok(None) is False


def test_set_silent_mode_toggle():
    app_utils.set_silent_mode(True)
    assert app_utils.silent_mode_on() is True
    app_utils.set_silent_mode(False)
    assert app_utils.silent_mode_on() is False
