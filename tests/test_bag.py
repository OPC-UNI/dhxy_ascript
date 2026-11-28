# -*- coding: utf-8 -*-
"""bag 摄妖香寻路期续用入口单测（门控逻辑，不触真实游戏与背包）。

验证点（use_sheyaoxiang_if_due 是替代定时续用线程的寻路期入口）：
- 开关关闭 / 未到期 / 非队长 / 战斗中 → 跳过，不执行使用流程
- 到期且条件满足 → 执行使用，成功才记录时间戳，失败不记录（下轮
  寻路重试）
"""
import pytest

from src.core import app_utils
from src.device import bag


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
    from src.device import battle as battle_mod
    from src.core import settings as core_settings
    monkeypatch.setattr(core_settings, "load_use_sheyaoxiang",
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
    import src.device.map as game_map
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


# ---- 任务储物格通用入口（各任务模块共用，2026-11-10）----
class _TaskBoxWin:
    def capture(self, force_activate=False):
        from PIL import Image
        import numpy as np
        return Image.fromarray(np.zeros((831, 1378, 3), dtype=np.uint8))


def _patch_task_box(monkeypatch, state):
    monkeypatch.setattr(bag, "is_bag_open", lambda w: True)
    monkeypatch.setattr(bag, "find_bag_box", lambda w: (500, 300))
    monkeypatch.setattr(bag.time, "sleep", lambda s: None)

    def fake_match(frame, icon, region=None):
        if state.get("opened"):
            return ((0.99, (1, 1)) if icon == bag.BOX_TASK_SELECTED_ICON
                    else (0.1, (0, 0)))
        return ((0.1, (0, 0)) if icon == bag.BOX_TASK_SELECTED_ICON
                else (0.99, (800, 400)))

    monkeypatch.setattr(bag.game_map, "best_match_np", fake_match)


def test_open_task_box_already_open_no_click(monkeypatch):
    """选中态分数更高 → 任务储物格已打开，不点击。"""
    _patch_task_box(monkeypatch, {"opened": True})
    monkeypatch.setattr(bag, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("已打开不应点击")))
    ok, info = bag.open_task_box(_TaskBoxWin(), prefix="名望")
    assert ok and "已在背包中打开" in info


def test_open_task_box_clicks_unselected_button(monkeypatch):
    """未选中态命中 → 点击任务储物格按钮，随后检测到选中态。"""
    state = {"opened": False}
    _patch_task_box(monkeypatch, state)
    clicks = []

    def fake_click(w, x, y, button="primary"):
        clicks.append((x, y, button))
        state["opened"] = True

    monkeypatch.setattr(bag, "human_click", fake_click)
    ok, info = bag.open_task_box(_TaskBoxWin(), prefix="名望")
    assert ok and "已在背包中打开" in info
    assert clicks == [(800, 400, "primary")]


# ---- 背包在场双锚点 / 遮挡时 Alt+E 关闭（2026-11-17 名望五庄封印实机）----

def test_bag_open_on_frame_storage_tab_fallback(monkeypatch):
    """bag_box 被追踪面板遮挡时，储物格按钮（选中/未选中态）兜底判定在场。"""
    monkeypatch.setattr(bag.game_map, "match_frame_np",
                        lambda frame, icon, **k: None)
    monkeypatch.setattr(bag.game_map, "best_match_multi_np",
                        lambda frame, icons, region=None: {
                            bag.BOX_TASK_SELECTED_ICON: (0.969, (370, 638)),
                            bag.BOX_TASK_ICON: (0.733, (375, 637))})
    assert bag.bag_open_on_frame(object()) is True


def test_bag_open_on_frame_closed_world(monkeypatch):
    """世界帧（面板关闭）两锚点都低分 → 判未打开（实测 0.542/0.651）。"""
    monkeypatch.setattr(bag.game_map, "match_frame_np",
                        lambda frame, icon, **k: None)
    monkeypatch.setattr(bag.game_map, "best_match_multi_np",
                        lambda frame, icons, region=None: {
                            bag.BOX_TASK_SELECTED_ICON: (0.542, (0, 0)),
                            bag.BOX_TASK_ICON: (0.651, (0, 0))})
    assert bag.bag_open_on_frame(object()) is False


def test_close_bag_hotkey_when_anchor_occluded(monkeypatch):
    """bag_box 未命中但面板在场（储物格按钮）→ 用 Alt+E 开关关闭。"""
    state = {"open": True, "pressed": 0}
    monkeypatch.setattr(bag, "_find_bag_box", lambda w: None)
    monkeypatch.setattr(bag, "is_bag_open", lambda w: state["open"])
    monkeypatch.setattr(bag.time, "sleep", lambda s: None)

    def press(win):
        state["pressed"] += 1
        state["open"] = False  # 开关关闭生效
        return True, "已静默投递 Alt+E 快捷键"

    monkeypatch.setattr(bag, "_press_bag_hotkey", press)
    ok, info = bag.close_bag(_TaskBoxWin())
    assert ok and "Alt+E" in info
    assert state == {"open": False, "pressed": 1}


def test_close_bag_closed_does_not_press_hotkey(monkeypatch):
    """面板确实不在场：不按 Alt+E（防把已关背包重新打开）。"""
    monkeypatch.setattr(bag, "_find_bag_box", lambda w: None)
    monkeypatch.setattr(bag, "is_bag_open", lambda w: False)
    monkeypatch.setattr(bag, "_press_bag_hotkey",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("已关闭不应按热键")))
    ok, info = bag.close_bag(_TaskBoxWin())
    assert ok and info == "背包未打开"


def test_find_item_in_bag_uses_catalog_threshold(monkeypatch):
    """数量角标模板按物品目录专用阈值匹配（珍兽丹 0.69，参考摄妖香）。"""
    seen = []

    def fake_match(win, icon, threshold=0.8):
        seen.append((icon, threshold))
        return []

    monkeypatch.setattr(bag, "_match_all_template", fake_match)
    bag.find_item_in_bag(object(), "item_zhenshoudan.png", threshold=0.8)
    bag.find_item_in_bag(object(), "item_tianwaifeishi.png", threshold=0.8)
    assert seen == [("item_zhenshoudan.png", 0.69),
                    ("item_tianwaifeishi.png", 0.8)]


# ---- 切储物格前复检面板在前台（2026-11-26 实机误点地图教训）----

def test_find_item_with_slots_aborts_when_bag_covered(monkeypatch):
    """背包被地图/面板盖住：切格前复检中止，绝不向未知窗口盲点。

    2026-11-26 修罗手动寻路：空闲摄妖香线程切储物格的点击落到同时
    打开的地图上，人物寻路去了 (176,7) 而不是任务坐标 (200,35)。
    """
    monkeypatch.setattr(bag, "find_item_in_bag", lambda *a, **k: [])
    monkeypatch.setattr(bag, "_match_all_template", lambda *a, **k: [])
    base = type("R", (), {"x": 446, "y": 579})()
    monkeypatch.setattr(bag, "find_box_one_close", lambda w: base)
    monkeypatch.setattr(bag, "is_slot_panel_open", lambda w: False)
    monkeypatch.setattr(bag.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(bag, "human_click", lambda *a, **k: clicks.append(a))
    item, desc = bag.find_item_with_slots(
        object(), "sheyaoxiang.png", "摄妖香", threshold=0.69)
    assert item is None
    assert "不在前台" in desc
    assert clicks == []  # 面板已不在前台，一次储物格点击都不能发


def test_slot_panel_on_frame_give_panel(monkeypatch):
    """给与面板在场（给与横幅命中、背包锚点全低分）也判「带储物格列面板在场」。

    2026-11-26 用户截图实测：give_title 1.000、bag_box 0.382、
    box_task 0.705/0.707——给与面板不是背包，旧复检按背包判会误中止切格。
    """
    def fake_match(frame, icon, **k):
        return (860, 350) if icon == bag.GIVE_TITLE_ICON else None

    monkeypatch.setattr(bag.game_map, "match_frame_np", fake_match)
    monkeypatch.setattr(bag.game_map, "best_match_multi_np",
                        lambda frame, icons, region=None: {
                            bag.BOX_TASK_SELECTED_ICON: (0.707, (0, 0)),
                            bag.BOX_TASK_ICON: (0.705, (0, 0))})
    assert bag.slot_panel_on_frame(object()) is True


def test_slot_panel_on_frame_world(monkeypatch):
    """世界帧（背包/给与都关闭）→ 不在场，切格必须中止。"""
    monkeypatch.setattr(bag.game_map, "match_frame_np",
                        lambda frame, icon, **k: None)
    monkeypatch.setattr(bag.game_map, "best_match_multi_np",
                        lambda frame, icons, region=None: {
                            bag.BOX_TASK_SELECTED_ICON: (0.542, (0, 0)),
                            bag.BOX_TASK_ICON: (0.651, (0, 0))})
    assert bag.slot_panel_on_frame(object()) is False


def test_find_item_with_slots_switches_in_give_panel(monkeypatch):
    """给与面板在场时正常切格：复检放行，4 个储物格逐个点击。"""
    monkeypatch.setattr(bag, "find_item_in_bag", lambda *a, **k: [])
    monkeypatch.setattr(bag, "_match_all_template", lambda *a, **k: [])
    base = type("R", (), {"x": 446, "y": 579})()
    monkeypatch.setattr(bag, "find_box_one_close", lambda w: base)
    monkeypatch.setattr(bag, "slot_panel_on_frame", lambda frame: True)
    monkeypatch.setattr(bag.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(bag, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))

    class _W:
        def capture(self, force_activate=False):
            import numpy as np
            from PIL import Image
            return Image.fromarray(np.zeros((831, 1378, 3), dtype=np.uint8))

    item, desc = bag.find_item_with_slots(_W(), "jiuzhuan.png", "九转还魂汤")
    assert item is None
    assert "切换 4 个储物格" in desc
    assert clicks == [(446, 579), (446, 614), (446, 649), (446, 684)]

