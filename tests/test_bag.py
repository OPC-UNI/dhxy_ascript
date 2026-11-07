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


# ---- 背包按钮搜索条带（2026-11-07 用户标定右下角托盘区） ----

def test_match_template_region_offsets_coords(monkeypatch):
    """区域匹配返回全图坐标：带内命中偏移正确、带外不搜。"""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(20261107)
    frame = Image.fromarray(rng.integers(0, 256, (831, 1378, 3),
                                         dtype=np.uint8))
    import src.map as game_map
    frame.paste(Image.open(__import__("ascript.windows.system",
                                      fromlist=["R"]).R.img(bag.BAG_ICON)),
                (1000, 750))

    class _W:
        def capture(self, force_activate=False):
            return frame

    r = bag._match_template(_W(), bag.BAG_ICON,
                            region=game_map.TRAY_BTN_REGION)
    assert r is not None
    assert r.x >= 1000 and r.y >= 750
    assert r.confidence >= 0.8
    # 带外不搜：同帧左侧再贴一张，条带匹配不得命中左侧
    frame2 = frame.copy()
    frame2.paste(Image.open(__import__("ascript.windows.system",
                                       fromlist=["R"]).R.img(bag.BAG_ICON)),
                 (200, 100))

    class _W2:
        def capture(self, force_activate=False):
            return frame2

    r2 = bag._match_template(_W2(), bag.BAG_ICON,
                             region=game_map.TRAY_BTN_REGION)
    assert r2.x >= 1000 and r2.y >= 750  # 仍只命中带内，不命中带外
