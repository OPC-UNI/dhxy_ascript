# -*- coding: utf-8 -*-
"""bag 摄妖香寻路期续用入口单测（门控逻辑，不触真实游戏与背包）。

验证点（use_sheyaoxiang_if_due 是替代定时续用线程的寻路期入口）：
- 开关关闭 / 未到期 / 非队长 / 战斗中 → 跳过，不执行使用流程
- 到期且条件满足 → 执行使用，成功才记录时间戳，失败不记录（下轮
  寻路重试）
"""
import pytest

from src import app_utils, bag


class _FakeWin:
    def __init__(self, hwnd=0x123):
        self.hwnd = hwnd


@pytest.fixture(autouse=True)
def _cleanup(monkeypatch):
    yield
    app_utils.set_current_task_mode("")
    app_utils.set_silent_mode(False)


def _mock_gates(monkeypatch, switch_on=True, due=True, skip_nonleader=False,
                in_battle=False):
    """打桩开关/到期/队长/战斗四个门，返回 use 调用记录。"""
    from src import battle as battle_mod
    from src import control_window
    monkeypatch.setattr(control_window, "load_use_sheyaoxiang",
                        lambda: switch_on)
    monkeypatch.setattr(app_utils, "sheyaoxiang_due", lambda _w: due)
    monkeypatch.setattr(app_utils, "sheyaoxiang_skip_nonleader",
                        lambda *a, **k: skip_nonleader)
    monkeypatch.setattr(battle_mod, "watch_in_battle", lambda: in_battle)
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda _w: in_battle)
    calls = []
    monkeypatch.setattr(bag, "use_sheyaoxiang",
                        lambda _w: calls.append(_w) or (True, "使用完成"))
    marks = []
    monkeypatch.setattr(app_utils, "sheyaoxiang_mark_used", marks.append)
    return calls, marks


def test_switch_off_skips(monkeypatch):
    calls, _ = _mock_gates(monkeypatch, switch_on=False)
    ran, info = bag.use_sheyaoxiang_if_due(_FakeWin())
    assert not ran and "开关" in info
    assert calls == []


def test_not_due_skips(monkeypatch):
    calls, _ = _mock_gates(monkeypatch, due=False)
    ran, info = bag.use_sheyaoxiang_if_due(_FakeWin())
    assert not ran and "不足 1 小时" in info
    assert calls == []


def test_nonleader_skips(monkeypatch):
    calls, _ = _mock_gates(monkeypatch, skip_nonleader=True)
    ran, info = bag.use_sheyaoxiang_if_due(_FakeWin())
    assert not ran and "非队长" in info
    assert calls == []


def test_in_battle_skips(monkeypatch):
    calls, _ = _mock_gates(monkeypatch, in_battle=True)
    ran, info = bag.use_sheyaoxiang_if_due(_FakeWin())
    assert not ran and "战斗" in info
    assert calls == []


def test_due_uses_and_marks(monkeypatch):
    calls, marks = _mock_gates(monkeypatch)
    win = _FakeWin()
    ran, info = bag.use_sheyaoxiang_if_due(win)
    assert ran and info == "使用完成"
    assert calls == [win]
    assert marks == [win]  # 成功才记录时间戳


def test_due_use_fail_no_mark(monkeypatch):
    calls, marks = _mock_gates(monkeypatch)
    monkeypatch.setattr(bag, "use_sheyaoxiang",
                        lambda _w: calls.append(_w) or (False, "未找到摄妖香"))
    win = _FakeWin()
    ran, info = bag.use_sheyaoxiang_if_due(win)
    assert not ran and "未找到" in info
    assert calls == [win]
    assert marks == []  # 失败不记录，下轮寻路重试
