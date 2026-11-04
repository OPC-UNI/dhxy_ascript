# -*- coding: utf-8 -*-
"""tab_group 静默切 Tab 兜底单测（不触真实游戏与鼠标）。

验证点：
- _switch_next_by_click：Tab 条点击通道的下一序号计算（TCM_GETCURSEL+1
  取模）、可见子窗口句柄变化判定、失败重试同一序号（防竞态连切）、
  无控件/读数失败时返回 None
- _switch_next 路由：键盘不可用（静默+后台）走点击通道，可用走 Ctrl+Tab
- _pass_foreground_ok：静默模式免前台检查，非静默走 _ensure_foreground
"""
import pytest

from src import app_utils, tab_group

TAB_HWND = 0x777


class _FakeWin:
    def __init__(self, hwnd=0x123):
        self.hwnd = hwnd


def _list_tabs_script(script):
    """按调用次数脚本化的可见子窗口枚举（script[i] = 第 i+1 次调用的可见 hwnd）。

    返回 (list_tab_windows 替身, 调用计数 dict)。
    """
    state = {"calls": 0}

    def list_tabs(_win):
        i = min(state["calls"], len(script) - 1)
        state["calls"] += 1
        visible = script[i]
        return [{"hwnd": h, "visible": h == visible, "title": f"角色（ID：{h}）",
                 "id": f"id{h:x}"} for h in (0xA, 0xB, 0xC)]

    return list_tabs, state


@pytest.fixture(autouse=True)
def _no_switch_settle(monkeypatch):
    """切换等待归零：单测不睡 0.8s。"""
    monkeypatch.setattr(tab_group, "TAB_SWITCH_SETTLE", 0.0)


@pytest.fixture(autouse=True)
def _silent_mode_off():
    """每个测试结束复位静默开关（防污染其他测试）。"""
    yield
    app_utils.set_silent_mode(False)


def _mock_silent_click(monkeypatch, clicks, tab=None, count=3, cur=0,
                       names=None):
    """打桩 silent_click 的 Tab 条原语：clicks 记录点击序号。

    names 缺省 = 全部已登录（角色名，!= 游戏标题）。
    """
    from src import silent_click
    monkeypatch.setattr(silent_click, "tab_control_hwnd", lambda _w: tab)
    monkeypatch.setattr(silent_click, "tab_item_count", lambda _t: count)
    monkeypatch.setattr(silent_click, "tab_current_index", lambda _t: cur)
    names = names if names is not None else [f"角色{i}" for i in range(count)]
    monkeypatch.setattr(silent_click, "tab_items",
                        lambda _w, _c: [(n, (0, 0, 174, 23)) for n in names])

    def click(_w, index):
        clicks.append(index)
        return {"hwnd": tab, "index": index, "point": (97, 13),
                "msg_results": (0, 0)}

    monkeypatch.setattr(silent_click, "send_silent_tab_click", click)


# ---- _switch_next_by_click ----

def test_switch_next_by_click_success(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0)
    list_tabs, _ = _list_tabs_script([0xA, 0xB])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "idb"
    assert clicks == [1]  # 一次点击即生效，无重试


def test_switch_next_by_click_wraps_around(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=2)
    list_tabs, _ = _list_tabs_script([0xC, 0xA])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "ida"
    assert clicks == [0]  # 末 Tab 回绕到 0


def test_switch_next_by_click_retry_same_index_then_success(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0)
    list_tabs, _ = _list_tabs_script([0xA, 0xA, 0xB])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "idb"
    assert clicks == [1, 1]  # 重试同一序号：不重算（防竞态连切两个）


def test_switch_next_by_click_never_switches_returns_none(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0)
    list_tabs, _ = _list_tabs_script([0xA, 0xA, 0xA])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) is None
    assert clicks == [1, 1]


def test_switch_next_by_click_no_tab_control(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=None)
    assert tab_group._switch_next_by_click(_FakeWin()) is None
    assert clicks == []


def test_switch_next_by_click_bad_tab_read(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=0, cur=-1)
    assert tab_group._switch_next_by_click(_FakeWin()) is None
    assert clicks == []


def test_switch_next_by_click_skips_unlogged_tab(monkeypatch):
    clicks = []
    # Tab 1 未登录（UIA 名 = 游戏标题）：cur=0 直接点击已登录的 Tab 2
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0,
                       names=["角色0", tab_group.game.GAME_TITLE, "角色2"])
    list_tabs, _ = _list_tabs_script([0xA, 0xC])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "idc"
    assert clicks == [2]  # 跳过未登录 Tab 1，不投递点击


def test_switch_next_by_click_skips_wraps_to_logged(monkeypatch):
    clicks = []
    # cur=2，下一圈 Tab 0 未登录 → 绕回已登录的 Tab 1
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=2,
                       names=[tab_group.game.GAME_TITLE, "角色1", "角色2"])
    list_tabs, _ = _list_tabs_script([0xC, 0xB])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "idb"
    assert clicks == [1]


# ---- _next_logged_index ----

def test_next_logged_index_skip_and_wrap(monkeypatch):
    from src import silent_click
    g = tab_group.game.GAME_TITLE
    items = [(g, (0, 0, 1, 1)), ("角色1", (0, 0, 1, 1)), (g, (0, 0, 1, 1)),
             ("角色3", (0, 0, 1, 1))]
    monkeypatch.setattr(silent_click, "tab_items", lambda _w, _c: items)
    assert tab_group._next_logged_index(_FakeWin(), None, 0, 4) == 1
    assert tab_group._next_logged_index(_FakeWin(), None, 1, 4) == 3
    # 从 3 绕回 1（0 与 2 未登录）
    assert tab_group._next_logged_index(_FakeWin(), None, 3, 4) == 1


def test_next_logged_index_fallbacks(monkeypatch):
    from src import silent_click
    # UIA 不可用 → 回退 (cur+1)%count
    monkeypatch.setattr(silent_click, "tab_items", lambda _w, _c: None)
    assert tab_group._next_logged_index(_FakeWin(), None, 2, 5) == 3
    # 其余 Tab 全未登录 → None（无切换目标，调用方放弃本轮）
    g = tab_group.game.GAME_TITLE
    items = [("角色2", (0, 0, 1, 1)), (g, (0, 0, 1, 1)), (g, (0, 0, 1, 1))]
    monkeypatch.setattr(silent_click, "tab_items", lambda _w, _c: items)
    assert tab_group._next_logged_index(_FakeWin(), None, 0, 3) is None


def test_switch_next_by_click_no_other_logged_tab(monkeypatch):
    clicks = []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0,
                       names=["角色0", tab_group.game.GAME_TITLE,
                              tab_group.game.GAME_TITLE])
    assert tab_group._switch_next_by_click(_FakeWin()) is None
    assert clicks == []  # 无切换目标：不投递任何点击


# ---- _switch_next 路由 ----

def test_switch_next_routes_to_click_when_keyboard_blocked(monkeypatch):
    monkeypatch.setattr(app_utils, "keyboard_ok", lambda _w: False)
    monkeypatch.setattr(tab_group, "_switch_next_by_click", lambda _w: "X")

    def _no_keyboard(*_a, **_k):
        raise AssertionError("键盘通道不应被调用")
    monkeypatch.setattr(tab_group.Keyboard, "key_hot", _no_keyboard)
    assert tab_group._switch_next(_FakeWin()) == "X"


def test_switch_next_keyboard_path_when_ok(monkeypatch):
    monkeypatch.setattr(app_utils, "keyboard_ok", lambda _w: True)
    keys = []
    monkeypatch.setattr(tab_group.Keyboard, "key_hot",
                        lambda *a, **k: keys.append(a))
    monkeypatch.setattr(tab_group, "_switch_next_by_click",
                        lambda _w: pytest.fail("点击通道不应被调用"))
    list_tabs, _ = _list_tabs_script([0xA, 0xB])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next(_FakeWin()) == "idb"
    assert keys == [("ctrl", "tab")]


# ---- pass 前置门 ----

def test_pass_foreground_ok_silent_skips_foreground(monkeypatch):
    def _boom(_w):
        raise AssertionError("静默模式不应检查/激活前台")
    monkeypatch.setattr(tab_group, "_ensure_foreground", _boom)
    app_utils.set_silent_mode(True)
    assert tab_group._pass_foreground_ok(_FakeWin()) is True


def test_pass_foreground_ok_non_silent_uses_ensure(monkeypatch):
    monkeypatch.setattr(tab_group, "_ensure_foreground", lambda _w: False)
    app_utils.set_silent_mode(False)
    assert tab_group._pass_foreground_ok(_FakeWin()) is False
