# -*- coding: utf-8 -*-
"""map_nav 多分区特殊地图（波月洞 A/B）单元测试。

波月洞同一坐标平面上有 A/B 两个互不相连的区域，靠洞口传送连接；单一边界
会被两区之间的空洞骗过去（A* 在空洞里绕，永远进不了 B 区）。本文件覆盖
分区判定、传送 BFS、跨区步行驱动与传送检测。
"""
import json
import types
from pathlib import Path

from src.device import map_nav


class _Win:
    """测试用窗口桩（_special_model_anchor 只读 rect.width/height）。"""

    def __init__(self, w, h):
        self.rect = types.SimpleNamespace(width=w, height=h)


_ZONE_A = {"name": "A", "boundary": [[0, 0], [10, 0], [10, 10], [0, 10]],
           "trace": [[1, 5], [9, 5]]}
_ZONE_B = {"name": "B", "boundary": [[20, 0], [30, 0], [30, 10], [20, 10]],
           "trace": [[21, 5], [29, 5]]}


def _zone_route():
    return {
        "zones": [_ZONE_A, _ZONE_B],
        "links": [
            # A→B 的 B 落点锚点样品正常：不需要重标定
            {"from_zone": "A", "from": [9, 5], "to_zone": "B",
             "land": [21, 5]},
            # B→A 的 A 落点只有转场期样品（波月洞实况）：必须重标定
            {"from_zone": "B", "from": [21, 5], "to_zone": "A",
             "land": [9, 5], "land_recalibrate": True},
        ],
        "trace": [[1, 5], [9, 5], [21, 5], [29, 5]],
        "boundary": [[0, 0], [10, 0], [10, 10], [0, 10]],
    }


def test_special_zone_path_bfs_both_directions():
    links = map_nav._special_zone_links(_zone_route())
    assert [l["to_zone"] for l in map_nav._special_zone_path(
        links, "A", "B")] == ["B"]
    assert [l["to_zone"] for l in map_nav._special_zone_path(
        links, "B", "A")] == ["A"]
    assert map_nav._special_zone_path(links, "A", "A") == []
    assert map_nav._special_zone_path(links, "A", "C") is None


def test_special_zone_at_and_nearest():
    zones = [_ZONE_A, _ZONE_B]
    assert map_nav._special_zone_name(
        map_nav._special_zone_at(zones, 5, 5)) == "A"
    assert map_nav._special_zone_name(
        map_nav._special_zone_at(zones, 25, 5)) == "B"
    assert map_nav._special_zone_at(zones, 15, 50) is None
    near = map_nav._special_zone_at(zones, 12, 5, allow_nearest=True)
    assert map_nav._special_zone_name(near) == "A"


def test_valid_special_route_accepts_zones_only():
    route = {"zones": [_ZONE_A, _ZONE_B]}
    assert map_nav._valid_special_route(route) is route
    assert map_nav._valid_special_route({"zones": [_ZONE_A]}) is None


def test_special_segment_inside_accepts_avoid_point_list():
    boundary = [[0, 0], [20, 0], [20, 20], [0, 20]]
    assert not map_nav._special_segment_inside(
        boundary, (1, 10), (19, 10), portal=[(10, 10)])
    assert not map_nav._special_segment_inside(
        boundary, (1, 10), (19, 10), portal=[(5, 10), (10, 10)])
    assert map_nav._special_segment_inside(
        boundary, (1, 2), (19, 2), portal=[(5, 10), (10, 10)])


def test_walk_special_zones_cross_zone_via_trigger(monkeypatch):
    """A→B：先走到 A 区触发点，传送后在 B 区按 B 边界走到目标。"""
    route = _zone_route()
    calls = []
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 3, 8))
    monkeypatch.setattr(map_nav, "_LAST_SPECIAL_VIEW", {})

    def fake_walk(win, target, x, y, emit, **kw):
        calls.append((x, y, kw.get("zone"), kw.get("avoid"),
                      kw.get("tolerance"), kw.get("recalibrate")))
        state = kw.get("zone_state")
        # 只在走 A 区触发点时传送到 B；B 区最终步行不发生传送
        if state is not None and (x, y) == (9, 5):
            state["teleported"] = True
            state["pos"] = ("洞", 21, 5)
            state["zone"] = _ZONE_B
        return True, "已到达"

    monkeypatch.setattr(map_nav, "_walk_special_coordinate", fake_walk)
    emits = []
    ok, info = map_nav._walk_special_zones(
        object(), "洞", 27, 8, emits.append)
    assert ok and info == "已到达"
    assert len(calls) == 2
    assert calls[0][0:2] == (9, 5)
    assert calls[0][2]["name"] == "A"
    assert calls[0][4] == map_nav.SPECIAL_TRIGGER_TOLERANCE
    assert calls[0][5] is False       # 普通起点：锚点模型可用
    assert calls[1][0:2] == (27, 8)
    assert calls[1][2]["name"] == "B"
    # B 落点锚点样品正常：不强制重标定（避免探针把人物带偏绕路）
    assert calls[1][5] is False
    # B 区内步行必须避让 B 区的回程触发点
    assert (21, 5) in [tuple(p) for p in calls[1][3]]
    assert any("需传送 1 次" in m for m in emits)


def test_walk_special_zones_flagged_landing_forces_recalibrate(monkeypatch):
    """B→A：A 落点附近无稳态锚点样品（land_recalibrate）→ 重标定相机。"""
    route = _zone_route()
    calls = []
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 25, 8))
    monkeypatch.setattr(map_nav, "_LAST_SPECIAL_VIEW", {})

    def fake_walk(win, target, x, y, emit, **kw):
        calls.append((x, y, kw.get("zone"), kw.get("recalibrate")))
        state = kw.get("zone_state")
        if state is not None and (x, y) == (21, 5):
            state["teleported"] = True
            state["pos"] = ("洞", 9, 5)
            state["zone"] = _ZONE_A
        return True, "已到达"

    monkeypatch.setattr(map_nav, "_walk_special_coordinate", fake_walk)
    ok, info = map_nav._walk_special_zones(
        object(), "洞", 8, 8, lambda m: None)
    assert ok and info == "已到达"
    assert calls[0][0:2] == (21, 5)
    assert calls[0][3] is False       # 普通起点：锚点模型可用
    assert calls[1][0:2] == (8, 8)
    assert calls[1][2]["name"] == "A"
    # A 落点被标记：目标区步行必须重标定相机
    assert calls[1][3] is True


def test_walk_special_zones_starting_near_flagged_landing_recalibrates(
        monkeypatch):
    """在 A 落点附近直接起步行（无传送）：同样按落点标记重标定。"""
    route = _zone_route()
    calls = []
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 10, 5))

    def fake_walk(win, target, x, y, emit, **kw):
        calls.append((x, y, kw.get("recalibrate")))
        return True, "已到达"

    monkeypatch.setattr(map_nav, "_walk_special_coordinate", fake_walk)
    ok, _info = map_nav._walk_special_zones(
        object(), "洞", 5, 5, lambda m: None)
    assert ok and calls == [(5, 5, True)]

    calls.clear()
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 22, 5))   # B 落点（未标记）
    ok, _info = map_nav._walk_special_zones(
        object(), "洞", 27, 5, lambda m: None)
    assert ok and calls == [(27, 5, False)]


def test_walk_special_zones_same_zone_walks_with_zone_boundary(monkeypatch):
    route = _zone_route()
    calls = []
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 3, 5))

    def fake_walk(win, target, x, y, emit, **kw):
        calls.append((x, y, kw.get("zone"), kw.get("avoid")))
        return True, "已到达"

    monkeypatch.setattr(map_nav, "_walk_special_coordinate", fake_walk)
    ok, info = map_nav._walk_special_zones(
        object(), "洞", 8, 8, lambda m: None)
    assert ok and len(calls) == 1
    assert calls[0][0:2] == (8, 8)
    assert calls[0][2]["name"] == "A"
    assert [tuple(p) for p in calls[0][3]] == [(9, 5)]


def test_walk_special_zones_no_link_returns_failure(monkeypatch):
    """目标在另一区但路网没有传送路线：明确失败，不按整图坐标乱走。"""
    route = {"zones": [_ZONE_A, _ZONE_B], "links": [],
             "trace": [[1, 5], [9, 5], [21, 5], [29, 5]]}
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("洞", 3, 5))
    emits = []
    ok, info = map_nav._walk_special_zones(
        object(), "洞", 27, 8, emits.append)
    assert not ok and "无传送路线" in info


def test_walk_special_coordinate_reports_zone_teleport(monkeypatch):
    """走到触发点途中就传送：步行器按分区变化提前返回，不再按 A 区走。"""
    map_nav._CANCEL.clear()
    route = _zone_route()
    zones = map_nav._special_zones(route)
    # 第三帧模拟 HUD 地名 OCR 错字「波月同」（2026-11-24 实机 B→A 失败场景）
    seq = [("波月洞", 5, 5), ("波月洞", 5, 5), ("波月同", 25, 5)]

    def read_pos(win):
        return seq.pop(0) if seq else ("波月同", 25, 5)

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos", read_pos)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, target, pos: None)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: (0.0, 0.0))
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    state = {}
    ok, info = map_nav._walk_special_coordinate(
        _W(), "波月洞", 9, 5, lambda m: None, zone=_ZONE_A,
        tolerance=map_nav.SPECIAL_TRIGGER_TOLERANCE,
        zones=zones, zone_state=state)
    assert ok and "已传送到" in info
    assert state.get("teleported")
    assert state["pos"] == ("波月同", 25, 5)
    assert map_nav._special_zone_name(state["zone"]) == "B"


def test_special_map_name_match_tolerates_one_ocr_error():
    """HUD 地名 1 字 OCR 错字仍按同图处理（波月同 → 波月洞）。"""
    assert map_nav._special_map_name_match("波月洞", "波月洞")
    assert map_nav._special_map_name_match("波月同", "波月洞")
    assert map_nav._special_map_name_match(" 波月洞 ", "波月洞")
    assert map_nav._special_map_name_match("波月", "波月洞")   # 漏字
    assert map_nav._special_map_name_match("波月洞洞", "波月洞")  # 多字
    assert not map_nav._special_map_name_match("月洞洞", "波月洞")
    assert not map_nav._special_map_name_match("平顶山", "波月洞")
    assert not map_nav._special_map_name_match("", "波月洞")


def test_special_wait_teleport_detects_zone_change_and_stabilizes(monkeypatch):
    zones = map_nav._special_zones(_zone_route())
    seq = [("洞", 9, 5), ("洞", 21, 5), ("洞", 21, 5)]
    monkeypatch.setattr(
        map_nav.game_map, "read_map_position",
        lambda win, img=None: seq.pop(0) if seq else ("洞", 21, 5))
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _W:
        def capture(self, force_activate=False):
            return object()

    emits = []
    pos, zone = map_nav._special_wait_teleport(
        _W(), "洞", "A", (9, 5), zones, emits.append, timeout=1.0)
    assert pos == ("洞", 21, 5)
    assert map_nav._special_zone_name(zone) == "B"
    assert any("传送完成" in m for m in emits)


def test_special_model_anchor_skips_out_of_window_samples(monkeypatch):
    """传送黑屏期相机跟踪跑飞写下的窗口外锚点必须丢弃。"""
    route = {"trace": [[0, 0], [1, 1]],
             "self_anchor": {"window": [100, 100],
                             "samples": [[10, 10, 50, 50],
                                         [11, 10, 500, 900]]}}
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    win = _Win(100, 100)
    ax, ay = map_nav._special_model_anchor(win, "洞", ("洞", 10, 10))
    assert (ax, ay) == (50.0, 50.0)


def test_builtin_wobue_route_declares_two_zones():
    """内置波月洞路网必须保持 A/B 两区 + 双向传送（数据回归）。"""
    path = (Path(__file__).resolve().parents[1]
            / "res" / "special_map_routes.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    route = data["maps"]["波月洞"]
    zones = map_nav._special_zones(route)
    assert [map_nav._special_zone_name(z) for z in zones] == ["A", "B"]
    links = map_nav._special_zone_links(route)
    assert {(l["from_zone"], l["to_zone"]) for l in links} == {
        ("A", "B"), ("B", "A")}
    assert map_nav._special_zone_name(
        map_nav._special_zone_at(zones, 62, 54)) == "A"
    assert map_nav._special_zone_name(
        map_nav._special_zone_at(zones, 23, 24)) == "B"


def test_walk_special_zones_replans_after_reverse_teleport(monkeypatch):
    """目标区步行时被洞口弹回原区：按新位置重新规划（有重试上限）。"""
    route = _zone_route()
    calls = []
    state = {"pos": ("洞", 5, 5), "reverse": True}
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_LAST_SPECIAL_VIEW", {})

    def fake_walk(win, target, x, y, emit, **kw):
        name = map_nav._special_zone_name(kw.get("zone"))
        calls.append((x, y, name))
        st = kw.get("zone_state")
        if (x, y) == (9, 5):          # A 区触发点 → B 区落点
            state["pos"] = ("洞", 19, 5)
            if st is not None:
                st["teleported"] = True
                st["pos"] = state["pos"]
                st["zone"] = _ZONE_B
        elif name == "B" and state.get("reverse"):   # 目标区步行被弹回 A
            state["reverse"] = False
            state["pos"] = ("洞", 8, 5)
            if st is not None:
                st["teleported"] = True
                st["pos"] = state["pos"]
                st["zone"] = _ZONE_A
        return True, "已到达"

    monkeypatch.setattr(map_nav, "_walk_special_coordinate", fake_walk)
    emits = []
    ok, info = map_nav._walk_special_zones(
        object(), "洞", 27, 8, emits.append)
    assert ok and info == "已到达"
    assert calls == [(9, 5, "A"), (27, 8, "B"),
                     (9, 5, "A"), (27, 8, "B")]
    assert any("重新规划" in m for m in emits)


def test_special_route_avoids_multiple_portals():
    """区内多个传送触发点：边界 A* 路线同时绕开全部避让点。"""
    boundary = [[0, 0], [20, 0], [20, 20], [0, 20]]
    route = {"boundary": boundary,
             "trace": [[1, 1], [19, 1], [19, 19], [1, 19]]}
    portals = [(6, 10), (14, 10)]
    wps = map_nav._special_route_waypoints(
        route, ("洞", 1, 10), 19, 10, portal=portals)
    assert len(wps) > 1
    r = map_nav.SPECIAL_PORTAL_AVOID_R
    prev = (1, 10)
    for wp in wps:
        for px, py in portals:
            assert (wp[0] - px) ** 2 + (wp[1] - py) ** 2 > r * r
        assert map_nav._special_segment_inside(
            boundary, prev, wp, portal=portals)
        prev = wp


def _recal_env(monkeypatch, model_raises=True, probe_raises=False,
               name_anchor=None):
    """传送后重标定测试环境：位置先停在起点、下一步直接到目标。"""
    map_nav._CANCEL.clear()
    seq = [("洞", 10, 10), ("洞", 10, 10), ("洞", 20, 10)]

    def read_pos(win):
        return seq.pop(0) if len(seq) > 1 else ("洞", 20, 10)

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "special_map_route", lambda name: None)
    monkeypatch.setattr(map_nav, "_special_read_pos", read_pos)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: name_anchor)
    if model_raises:
        def _no_model(*a, **k):
            raise AssertionError("recalibrate 不应调用描边锚点模型")
        monkeypatch.setattr(map_nav, "_special_model_anchor", _no_model)
    if probe_raises:
        def _no_probe(*a, **k):
            raise AssertionError("名字锚点成功时不应探针")
        monkeypatch.setattr(map_nav, "_special_probe", _no_probe)
    else:
        monkeypatch.setattr(map_nav, "_special_probe",
                            lambda *a, **k: (5.2, 19.8))
    monkeypatch.setattr(map_nav, "human_click",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    # 相机位移可测（0,0），避免走 mid-walk 的模型回退分支
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda a, b: (0.0, 0.0, 0.99))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    return _W


def test_walk_special_coordinate_recalibrate_prefers_name_anchor(monkeypatch):
    """传送后重标定：名字标签可用时不用描边锚点模型/探针。"""
    _W = _recal_env(monkeypatch, probe_raises=True,
                    name_anchor=(100.0, 200.0))
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "洞", 20, 10, emits.append, use_route=False,
        recalibrate=True)
    assert ok and "已到达" in info
    assert any("传送后名字锚点重标定" in m for m in emits)


def test_walk_special_coordinate_recalibrate_fails_over_to_probe(monkeypatch):
    """名字标签读不到 → 强制探针，仍不信任转场期锚点模型。"""
    _W = _recal_env(monkeypatch, name_anchor=None)
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "洞", 20, 10, emits.append, use_route=False,
        recalibrate=True)
    assert ok and "已到达" in info
    assert any("传送后探针重标定完成" in m for m in emits)
