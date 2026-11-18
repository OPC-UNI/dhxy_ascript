"""mowangchongju（魔王重聚·传音石寻人）小游戏单元测试。

锁住：提示文案解析（地图提示/方向四档）、红点识别、行走到达/停滞、
引导动作分发（跨图/按方向/战斗）、交付阶段不触发、使用上限与状态复位。
截图/OCR/点击/道具使用全部桩替换（不依赖真机画面）。
"""
import time

import numpy as np
import pytest
from PIL import Image

from src.minigames import mowangchongju as mw

GUIDE_SIG = "山河名望 使用传音石找到魔王和魔王护卫的位置并在他附近使用传音石"
DELIVER_SIG = ("山河名望 已经通过了魔王护卫的考验请将传音石交给魔王三大王")


@pytest.fixture(autouse=True)
def _reset_state():
    mw.reset()


def _items():
    return [("使用传音石找到魔王和魔王护卫", [10, 250, 230, 270], 0.95)]


def test_trigger_keywords_guide_only():
    """引导文案命中；战后交付文案不命中（交付由引擎/对话表处理）。"""
    assert mw._trigger(GUIDE_SIG)
    assert mw._trigger("山河名望 使用传音石找到魔王和魔王护")
    assert not mw._trigger(DELIVER_SIG)
    assert not mw._trigger("火藏之位 木藏之位")
    assert not mw._trigger("")


def test_parse_hint_map():
    """不在目标地图 → 解析地图名（2026-11-15 实机文案，含繁体归一）。"""
    assert mw._parse_hint(
        "传音石有所感应，魔王应该不在附近，而在大唐边境地图") == (
            "map", "大唐边境")
    assert mw._parse_hint(
        "传音石有所感应，魔王应该不在附近，而在長安城地图") == (
            "map", "长安城")


def test_parse_hint_direction_levels():
    """目标地图内 → 方向 + 距离级别逐级收敛（四档实机文案）。"""
    assert mw._parse_hint(
        "传音石微微颤抖，魔王应该在西北很远的地方。") == (
            "dir", ("西北", "far"))
    assert mw._parse_hint(
        "传音石朝着西北方向剧烈震动，应该不远了。") == (
            "dir", ("西北", "mid"))
    assert mw._parse_hint(
        "传音石振动得越发激烈，魔王应该就在东方不远处。") == (
            "dir", ("东", "near"))
    assert mw._parse_hint(
        "传音石几乎崩裂开来，魔王就在附近偏西北方向。") == (
            "dir", ("西北", "adjacent"))
    # 无方向词但「就在附近」：方向 None，继续用传音石触发战斗
    assert mw._parse_hint(
        "传音石几乎崩裂开来，魔王就在附近。") == (
            "dir", (None, "adjacent"))


def test_parse_hint_unknown():
    assert mw._parse_hint("无关文案") == (None, None)
    assert mw._parse_hint("") == (None, None)


def _patch_actions(monkeypatch, hint=(None, None)):
    """桩：提示、跨图/寻路动作、关地图、道具使用。"""
    st = {"uses": 0, "ok": True, "hint": hint, "go": [], "walk": [],
          "closed": 0}

    def _use(win):
        st["uses"] += 1
        return st["ok"], "已使用传音石"

    def _close(win):
        st["closed"] += 1
        return True

    # 帧桩：无帧（对话框门不命中）
    monkeypatch.setattr(mw, "_frame", lambda win, items, fn: (None, None))
    monkeypatch.setattr(mw, "_read_hint", lambda win: st["hint"])
    monkeypatch.setattr(mw, "_close_map", _close)
    monkeypatch.setattr(mw, "_go_map",
                        lambda win, name, prefix:
                        st["go"].append(name) or True)
    monkeypatch.setattr(mw, "_walk_step",
                        lambda win, direction, level, prefix:
                        st["walk"].append((direction, level)) or True)
    return st, _use


def test_use_then_map_navigation(monkeypatch):
    """提示「而在<地图>地图」→ 世界地图导航到目标地图。"""
    st, use = _patch_actions(monkeypatch, ("map", "大唐边境"))
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert st["uses"] == 1
    assert st["go"] == ["大唐边境"]


def test_use_then_direction_walk(monkeypatch):
    """提示方向+距离 → 按方向/级别寻路。"""
    st, use = _patch_actions(monkeypatch, ("dir", ("西北", "far")))
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert st["walk"] == [("西北", "far")]
    assert mw._hint_misses == 0


def test_battle_hint_returns_true(monkeypatch):
    """近身使用触发魔王护卫战斗 → 已处理（战斗线程托管），不再用道具。"""
    st, use = _patch_actions(monkeypatch, ("battle", None))
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert st["uses"] == 1


def test_hint_missed_retries(monkeypatch):
    """提示未读到 → 计数 + 间隔后重试，地图已关闭。"""
    st, use = _patch_actions(monkeypatch)
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert mw._hint_misses == 1
    assert st["closed"] >= 1          # 世界地图可能被自动打开，须关闭
    mw._use_at = time.time() - mw.MOWANG_USE_RETRY_AFTER
    mw.handle(object(), _items(), None, use_item=use)
    assert mw._hint_misses == 2


def test_deliver_text_no_action(monkeypatch):
    """战后交付文案 → 不触发引导（引擎绿字点击 + 对话表处理）。"""
    st, use = _patch_actions(monkeypatch)
    items = [("，请将传音石交给魔王三大王", [10, 270, 230, 290], 0.95)]
    assert mw.handle(object(), items, None, use_item=use) is False
    assert st["uses"] == 0


def test_use_interval(monkeypatch):
    """使用最小间隔内不重复使用。"""
    st, use = _patch_actions(monkeypatch)
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert st["uses"] == 1
    assert mw.handle(object(), _items(), None, use_item=use) is False
    assert st["uses"] == 1
    mw._use_at = time.time() - mw.MOWANG_USE_RETRY_AFTER
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert st["uses"] == 2


def test_use_failure_not_counted(monkeypatch):
    """使用失败（背包/对话框瞬态）→ 不消耗次数、按间隔重试。"""
    st, use = _patch_actions(monkeypatch)
    st["ok"] = False
    assert mw.handle(object(), _items(), None, use_item=use) is False
    assert st["uses"] == 1 and mw._uses == 0
    st["ok"] = True
    mw._use_at = time.time() - mw.MOWANG_USE_RETRY_AFTER
    assert mw.handle(object(), _items(), None, use_item=use) is True
    assert mw._uses == 1


def test_use_cap(monkeypatch):
    """使用次数达上限 → 停止使用（安全阀）。"""
    st, use = _patch_actions(monkeypatch)
    mw._uses = mw.MOWANG_USE_MAX
    mw._use_at = 0.0
    assert mw.handle(object(), _items(), None, use_item=use) is False
    assert st["uses"] == 0


def test_reset_when_trigger_gone(monkeypatch):
    """触发词连续缺失 → 状态复位。"""
    _, use = _patch_actions(monkeypatch)
    mw.handle(object(), _items(), None, use_item=use)   # 置使用时间
    assert mw._use_at > 0
    items = [("菜市江湖 从农田仓库农场中收获", [10, 300, 200, 320], 0.95)]
    for _ in range(mw.MOWANG_ABSENT_RESET):
        mw.handle(object(), items, None, use_item=use)
    assert mw._use_at == 0.0
    assert mw._missing == 0


def test_no_use_item_callback(monkeypatch):
    """未注入道具使用回调 → 不操作（防御性返回 False）。"""
    _patch_actions(monkeypatch)
    assert mw.handle(object(), _items(), None) is False


def test_dialog_present_defers_use(monkeypatch):
    """标准对话框在场 → 暂不使用传音石（交给 follow 引擎推进对话）。

    2026-11-15 实机：牛魔王交付传音石对话常驻时处理器每轮询都被调用，
    只做零操作等待；须有一行限频日志，否则「卡在对话」无从排查。
    """
    st, use = _patch_actions(monkeypatch)
    logs = []
    monkeypatch.setattr(mw, "_frame", lambda win, items, fn: (None, "bgr"))
    monkeypatch.setattr(mw, "dialog_frame_score",
                        lambda frame: mw.DIALOG_FRAME_THRESHOLD + 0.1)
    monkeypatch.setattr(mw, "log", lambda msg, level="INFO": logs.append(msg))
    assert mw.handle(object(), _items(), None, use_item=use) is False
    assert st["uses"] == 0
    assert any("对话框在场" in line for line in logs)


def test_walk_poll_arrival(monkeypatch):
    """行走轮询：位置连续 3 次稳定 → 到达，清除行走状态。"""
    mw._walk = {"at": time.time(), "last_move_at": time.time(),
                "from": ("大唐边境", 100, 200),
                "last": ("大唐边境", 105, 202), "stable": 0}
    monkeypatch.setattr(mw, "_pos",
                        lambda win, items, fn: ("大唐边境", 105, 202))
    assert mw._walk_poll(object(), [], None, "[测] ") is False  # stable 1
    assert mw._walk_poll(object(), [], None, "[测] ") is False  # stable 2
    assert mw._walk_poll(object(), [], None, "[测] ") is True   # 到达
    assert mw._walk is None


def test_walk_poll_stall(monkeypatch):
    """位置长时间不变（超 stall 窗口）→ 结束行走，重新取提示。"""
    now = time.time()
    mw._walk = {"at": now,
                "last_move_at": now - mw.MOWANG_WALK_STALL_TIMEOUT - 1,
                "from": ("大唐边境", 100, 200),
                "last": ("大唐边境", 100, 200), "stable": 0}
    monkeypatch.setattr(mw, "_pos",
                        lambda win, items, fn: ("大唐边境", 100, 200))
    assert mw._walk_poll(object(), [], None, "[测] ") is True
    assert mw._walk is None


def test_walk_in_progress_blocks_use(monkeypatch):
    """行走中不再使用道具（等到达/停滞）。"""
    st, use = _patch_actions(monkeypatch)
    mw._walk = {"at": time.time(), "last_move_at": time.time(),
                "from": ("大唐边境", 100, 200),
                "last": ("大唐边境", 105, 202), "stable": 0}
    monkeypatch.setattr(mw, "_pos",
                        lambda win, items, fn: ("大唐边境", 105, 202))
    assert mw.handle(object(), _items(), None, use_item=use) is False
    assert st["uses"] == 0


def test_close_map_delegates_to_close_maps(monkeypatch):
    """关地图走 game_map.close_maps（模板定位关闭钮），不再用固定坐标。

    2026-11-18 实机：固定坐标 (1106,219) 在狮驼岭帧落到面板外（面板
    右缘 ~1005、关闭钮 (996,214)），点到游戏世界把刚启动的寻路打断。
    """
    calls = []
    monkeypatch.setattr(mw.game_map, "close_maps",
                        lambda win, quiet=False: calls.append(win))
    mw._close_map(object())
    assert len(calls) == 1


def _patch_go_map(monkeypatch, detail_open):
    """桩：世界地图 → 标签 → 详情图整段走共享 open_mini_map_by_name，
    在场与否可控（跨图导航不再自写截图匹配）。"""
    st = {"walk": [], "closed": 0}
    monkeypatch.setattr(
        mw.game_map, "open_mini_map_by_name",
        lambda win, name, prefix="[地图] ":
        (detail_open,
         "本图小地图已打开" if detail_open
         else f"点击{name}后小地图未打开"))
    monkeypatch.setattr(mw, "_walk_click",
                        lambda win, x, y:
                        st["walk"].append((x, y)) or True)
    monkeypatch.setattr(mw, "_close_map",
                        lambda win: st.__setitem__("closed",
                                                   st["closed"] + 1))
    return st


def test_go_map_requires_detail_panel(monkeypatch):
    """标签点击后详情图未打开 → 不点落点（防点到世界地图错位置）。"""
    st = _patch_go_map(monkeypatch, detail_open=False)
    logs = []
    monkeypatch.setattr(mw, "log", lambda msg, level="INFO": logs.append(msg))
    assert mw._go_map(object(), "狮驼岭", "[测] ") is False
    assert st["walk"] == []
    assert st["closed"] == 1
    assert any("详情图失败" in line for line in logs)


def test_go_map_clicks_entry_when_detail_open(monkeypatch):
    """详情图在场 → 点落点候选，首个启动寻路即停。"""
    st = _patch_go_map(monkeypatch, detail_open=True)
    assert mw._go_map(object(), "狮驼岭", "[测] ") is True
    assert st["walk"] == [mw.MOWANG_ENTRY_POINTS[0]]
    assert st["closed"] == 1


class _FakeWin:
    """桩窗口：capture 返回占位帧（_find_dot 已被替换）。"""
    def capture(self, force_activate=True):
        return object()


def test_walk_step_opens_mini_map_directly(monkeypatch):
    """方向步进直接开当前地图小地图，不经世界地图+标签（2026-11-18 用户规则）。

    旧路径每轮先开世界地图、再点当前地图标签、再关世界地图；关闭点击
    还可能落到面板外打断寻路。地图残留时先关，再点位置区开小地图。
    """
    calls = {"world": 0, "mini": 0, "close_world": 0,
             "click": [], "closed": 0}
    monkeypatch.setattr(mw.game_map, "is_world_map_open", lambda win: True)
    monkeypatch.setattr(
        mw.game_map, "close_world_map",
        lambda win, quiet=False: calls.__setitem__(
            "close_world", calls["close_world"] + 1))
    monkeypatch.setattr(
        mw.game_map, "open_world_map",
        lambda win: calls.__setitem__("world", calls["world"] + 1)
        or (True, "世界地图已打开"))
    monkeypatch.setattr(mw.game_map, "is_mini_map_open", lambda win: False)
    monkeypatch.setattr(
        mw.game_map, "open_mini_map",
        lambda win: calls.__setitem__("mini", calls["mini"] + 1)
        or (True, "小地图已打开"))
    monkeypatch.setattr(mw, "_find_dot", lambda img: (600, 400))
    monkeypatch.setattr(mw, "_walk_click",
                        lambda win, x, y: calls["click"].append((x, y)) or True)
    monkeypatch.setattr(
        mw, "_close_map",
        lambda win: calls.__setitem__("closed", calls["closed"] + 1))
    assert mw._walk_step(_FakeWin(), "东", "mid", "[测] ") is True
    assert calls["close_world"] == 1          # 世界地图残留先关
    assert calls["world"] == 0                 # 不再打开世界地图
    assert calls["mini"] == 1                  # 直接开当前地图小地图
    assert calls["click"] == [
        (600 + mw.MOWANG_STEP_PX["mid"], 400)]  # 红点为原点按方向步进
    assert calls["closed"] == 1


def test_find_dot_color():
    """详情图红点定位：亮红圆点命中；无红点返回 None。"""
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    frame[258:267, 417:426] = (205, 0, 32)     # 红点（RGB）
    dot = mw._find_dot(Image.fromarray(frame))
    assert dot is not None and abs(dot[0] - 421) <= 2 and abs(dot[1] - 262) <= 2
    blank = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert mw._find_dot(Image.fromarray(blank)) is None
