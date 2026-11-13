"""workflows.return_flow：返程流程编排的共享实现测试。"""
import numpy as np

from src.workflows import return_flow


class _Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _Win:
    def capture(self, force_activate=False):
        return np.zeros((10, 10, 3), np.uint8)


def test_wait_battle_end_immediate(monkeypatch):
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: False)
    assert return_flow.wait_battle_end(object()) == (True, "战斗已结束")


def test_wait_battle_end_timeout(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(return_flow, "time", clock)
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: True)
    ok, info = return_flow.wait_battle_end(object(), timeout=3)
    assert ok is False
    assert info == "等待战斗结束超时（3s）"


def test_find_return_item_template_missing(monkeypatch):
    monkeypatch.setattr(return_flow, "_template_present",
                        lambda icon, te=None: False)
    assert return_flow.find_return_item(
        object(), "x.png", "道具") == (None, "道具模板未提供（待补充 x.png）")


def test_find_return_item_found(monkeypatch):
    monkeypatch.setattr(return_flow, "_template_present",
                        lambda icon, te=None: True)
    monkeypatch.setattr(return_flow.game_map, "match_frame_np",
                        lambda frame, icon: (5, 6))
    assert return_flow.find_return_item(
        _Win(), "x.png", "道具") == ((5, 6), "已在任务储物格中找到道具")


def test_use_return_item_bag_closed(monkeypatch):
    monkeypatch.setattr(return_flow, "find_return_item",
                        lambda *a, **k: ((1, 2), "found"))
    monkeypatch.setattr(return_flow.bag, "is_bag_open", lambda win: False)
    ok, info = return_flow.use_return_item(
        object(), "x.png", "道具", "长安", "[测试]", bag_closed_label="短名")
    assert ok is False
    assert info == "右键短名前背包已被关闭，未使用（下轮重试）"


def test_use_return_item_success(monkeypatch):
    clicks = []
    monkeypatch.setattr(return_flow, "find_return_item",
                        lambda *a, **k: ((1, 2), "found"))
    monkeypatch.setattr(return_flow.bag, "is_bag_open", lambda win: True)
    monkeypatch.setattr(return_flow, "human_click",
                        lambda win, x, y, button="left": clicks.append(
                            (x, y, button)))
    monkeypatch.setattr(return_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(return_flow, "log", lambda *a: None)
    ok, info = return_flow.use_return_item(
        object(), "x.png", "道具", "长安", "[测试]")
    assert ok is True and info == "已右键使用道具"
    assert clicks == [(1, 2, "secondary")]


def test_return_flow_order(monkeypatch):
    calls = []
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(return_flow, "use_return_item",
                        lambda *a, **k: calls.append("use") or (True, "used"))
    monkeypatch.setattr(return_flow, "close_return_panels",
                        lambda *a, **k: calls.append("close"))
    flow = return_flow.ReturnFlow(
        prefix="[测试]", item_icon="x.png", item_label="道具",
        target_label="长安", success_info="已返回长安",
        open_box=lambda win: calls.append("open") or (True, "ok"),
        read_position=False, post_sleep=0)
    assert flow.run(object()) == (True, "已返回长安")
    assert calls == ["open", "use", "close"]


def test_return_flow_open_box_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(return_flow, "use_return_item",
                        lambda *a, **k: (True, "used"))
    monkeypatch.setattr(return_flow, "close_return_panels", lambda *a, **k: None)
    monkeypatch.setattr(return_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(return_flow, "log", lambda *a: None)

    def _open(win):
        calls.append("open")
        return len(calls) >= 2, "fail" if len(calls) < 2 else "ok"

    flow = return_flow.ReturnFlow(
        prefix="[测试]", item_icon="x.png", item_label="道具",
        target_label="长安", success_info="已返回长安",
        open_box=_open, open_box_retries=2, read_position=False, post_sleep=0)
    assert flow.run(object()) == (True, "已返回长安")
    assert calls == ["open", "open"]
