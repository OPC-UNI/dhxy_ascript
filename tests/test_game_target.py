"""进程级目标窗口（多窗口）测试：strict worker 不回退其他窗口。"""
import pytest

from src.device import game


class _W:
    def __init__(self, hwnd, title):
        self.hwnd = hwnd
        self.title = title


def _a():
    return _W(1, "大话西游2经典版 $Revision: 1 - 一生所爱 - 甲（ID：111）")


def _b():
    return _W(2, "大话西游2经典版 $Revision: 1 - 一生所爱 - 乙（ID：222）")


@pytest.fixture(autouse=True)
def _clear_target():
    game.clear_process_target()
    yield
    game.clear_process_target()


def test_strict_target_never_falls_back(monkeypatch):
    monkeypatch.setattr(game, "list_main_windows", lambda: [_a(), _b()])
    game.set_process_target("111", 1, _a().title, strict=True)
    assert game.find_main_window().hwnd == 1

    # 目标窗口消失：strict 必须返回 None，不能操作到 222
    game.set_process_target("111", 99, _a().title, strict=True)
    assert game.find_main_window() is None
    assert game.find_bound_window() is None


def test_main_target_role_reacquires_reopened_window(monkeypatch):
    monkeypatch.setattr(game, "list_main_windows", lambda: [_a(), _b()])
    # 主进程选中 222，句柄失效但同角色窗口重开（新句柄）→ 按角色 ID 命中
    game.set_process_target("222", 99, "", strict=False)
    assert game.find_main_window().hwnd == 2


def test_main_target_does_not_switch_to_other_role(monkeypatch):
    monkeypatch.setattr(game, "list_main_windows", lambda: [_a()])
    game.set_process_target("222", 0, "", strict=False)
    assert game.find_main_window() is None


def test_synthetic_binding_from_target(monkeypatch):
    game.set_process_target("111", 1, _a().title, strict=True)
    raw = game.bound_window_id()
    assert raw["id"] == "（ID：111）"
    assert raw["hwnd"] == 1


def test_no_target_keeps_legacy_pick(monkeypatch):
    monkeypatch.setattr(game, "list_main_windows", lambda: [_a(), _b()])
    monkeypatch.setattr(game, "bound_window_id", lambda: None)
    assert game.find_main_window().hwnd == 1
