"""退出游戏流程：队伍处理/多 Tab 切换/客户端关闭（全部桩，不碰真实窗口）。"""
from PIL import Image

from src.device import exit_game


class FakeWin:
    def __init__(self, tabs=("1",)):
        self.tabs = list(tabs)
        self.captures = 0

    def capture(self, force_activate=False):
        self.captures += 1
        return Image.new("RGB", (20, 20), "white")


class WindowSeq:
    def __init__(self, values):
        self.values = list(values)
        self.index = 0

    def __call__(self):
        if self.index < len(self.values):
            value = self.values[self.index]
            self.index += 1
            return value
        return self.values[-1] if self.values else None


def _patch_tabs(monkeypatch, state):
    monkeypatch.setattr(exit_game.game, "list_tab_windows",
                        lambda win: [{"id": tid} for tid in state["tabs"]])
    monkeypatch.setattr(exit_game.tab_group, "current_tab_id",
                        lambda win: state["cur"])

    def switch(_win):
        tabs = state["tabs"]
        if state["cur"] not in tabs or len(tabs) < 2:
            return None
        idx = tabs.index(state["cur"])
        state["cur"] = tabs[(idx + 1) % len(tabs)]
        return state["cur"]

    monkeypatch.setattr(exit_game.tab_group, "switch_next_logged", switch)


def test_no_logged_tabs(monkeypatch):
    _patch_tabs(monkeypatch, {"tabs": [], "cur": None})
    ok, info = exit_game.leave_teams_for_exit(FakeWin([]))
    assert ok is True
    assert "没有已登录" in info


def test_not_in_team(monkeypatch):
    _patch_tabs(monkeypatch, {"tabs": ["1"], "cur": "1"})
    monkeypatch.setattr(exit_game.team, "leave_or_disband_team",
                        lambda win: (True, "未组队，无需操作"))
    ok, info = exit_game.leave_teams_for_exit(FakeWin(["1"]))
    assert ok is True
    assert "未组队" in info


def test_member_leaves_then_leader_disbands(monkeypatch):
    state = {"tabs": ["1", "2"], "cur": "1"}
    _patch_tabs(monkeypatch, state)
    results = [(True, "已离开队伍"), (True, "已解散队伍")]
    calls = []

    def leave(_win):
        calls.append(state["cur"])
        return results.pop(0)

    monkeypatch.setattr(exit_game.team, "leave_or_disband_team", leave)
    ok, info = exit_game.leave_teams_for_exit(FakeWin(["1", "2"]))
    assert ok is True
    assert "已解散队伍" in info
    assert calls == ["1", "2"]


def test_leader_first_disbands_immediately(monkeypatch):
    state = {"tabs": ["1", "2", "3"], "cur": "1"}
    _patch_tabs(monkeypatch, state)
    calls = []

    def leave(_win):
        calls.append(state["cur"])
        return True, "已解散队伍"

    monkeypatch.setattr(exit_game.team, "leave_or_disband_team", leave)
    ok, info = exit_game.leave_teams_for_exit(FakeWin(["1", "2", "3"]))
    assert ok is True
    assert "已解散队伍" in info
    assert calls == ["1"]  # 队长解散后不再遍历剩余 Tab


def test_team_error_aborts(monkeypatch):
    _patch_tabs(monkeypatch, {"tabs": ["1"], "cur": "1"})
    monkeypatch.setattr(exit_game.team, "leave_or_disband_team",
                        lambda win: (False, "面板未打开"))
    ok, info = exit_game.leave_teams_for_exit(FakeWin(["1"]))
    assert ok is False
    assert "队伍处理失败" in info


def test_left_without_finding_leader(monkeypatch):
    state = {"tabs": ["1", "2"], "cur": "1"}
    _patch_tabs(monkeypatch, state)
    monkeypatch.setattr(exit_game.team, "leave_or_disband_team",
                        lambda win: (True, "已离开队伍"))
    ok, info = exit_game.leave_teams_for_exit(FakeWin(["1", "2"]))
    assert ok is True
    assert "未找到队长" in info


def test_exit_client_window_already_gone(monkeypatch):
    monkeypatch.setattr(exit_game.game, "find_main_window", lambda: None)
    ok, info = exit_game.exit_client(None)
    assert ok is True
    assert "已关闭" in info


def test_exit_client_no_logged_tabs(monkeypatch):
    win = FakeWin([])
    monkeypatch.setattr(exit_game.game, "find_main_window", lambda: win)
    monkeypatch.setattr(exit_game.game, "list_tab_windows", lambda w: [])
    ok, info = exit_game.exit_client(win)
    assert ok is True
    assert "登录界面" in info


def test_exit_client_single_tab_closes(monkeypatch):
    win = FakeWin(["1"])
    monkeypatch.setattr(exit_game, "_open_and_click_exit",
                        lambda w: (True, "已点击退出游戏"))
    monkeypatch.setattr(exit_game.game, "find_main_window",
                        WindowSeq([win, None]))
    monkeypatch.setattr(exit_game.game, "list_tab_windows",
                        lambda w: [{"id": tid} for tid in w.tabs])
    monkeypatch.setattr(exit_game.time, "sleep", lambda s: None)
    ok, info = exit_game.exit_client(win)
    assert ok is True
    assert "已关闭" in info


def test_exit_client_multiple_tabs(monkeypatch):
    win1 = FakeWin(["1", "2"])
    win2 = FakeWin(["2"])
    calls = []
    monkeypatch.setattr(exit_game, "_open_and_click_exit",
                        lambda w: (calls.append(w), (True, "ok"))[1])
    monkeypatch.setattr(exit_game.game, "find_main_window",
                        WindowSeq([win1, win2, win2, None]))
    monkeypatch.setattr(exit_game.game, "list_tab_windows",
                        lambda w: [{"id": tid} for tid in w.tabs])
    monkeypatch.setattr(exit_game.time, "sleep", lambda s: None)
    ok, info = exit_game.exit_client(win1)
    assert ok is True
    assert len(calls) == 2


def test_exit_client_three_tabs_shrink(monkeypatch):
    """队长 Tab 先退出后剩两个 Tab：每次重新枚举并继续退出剩余 Tab。"""
    win1 = FakeWin(["1", "2", "3"])
    win2 = FakeWin(["2", "3"])
    win3 = FakeWin(["3"])
    calls = []
    monkeypatch.setattr(exit_game, "_open_and_click_exit",
                        lambda w: (calls.append(w), (True, "ok"))[1])
    monkeypatch.setattr(
        exit_game.game, "find_main_window",
        WindowSeq([win1, win2, win2, win3, win3, None]))
    monkeypatch.setattr(exit_game.game, "list_tab_windows",
                        lambda w: [{"id": tid} for tid in w.tabs])
    monkeypatch.setattr(exit_game.time, "sleep", lambda s: None)
    ok, info = exit_game.exit_client(win1)
    assert ok is True
    assert len(calls) == 3


def test_exit_client_click_failure(monkeypatch):
    win = FakeWin(["1"])
    monkeypatch.setattr(exit_game, "_open_and_click_exit",
                        lambda w: (False, "系统菜单未打开"))
    monkeypatch.setattr(exit_game.game, "find_main_window", lambda: win)
    monkeypatch.setattr(exit_game.game, "list_tab_windows",
                        lambda w: [{"id": tid} for tid in w.tabs])
    ok, info = exit_game.exit_client(win)
    assert ok is False
    assert "系统菜单未打开" in info


def test_open_and_click_exit_sequence(monkeypatch):
    win = FakeWin(["1"])
    clicks = []
    monkeypatch.setattr(exit_game.time, "sleep", lambda s: None)
    monkeypatch.setattr(exit_game.silent_click, "send_silent_click",
                        lambda w, x, y: clicks.append((x, y)) or {})

    def match(_img, icon, region=None):
        if icon == exit_game.SYSTEM_ICON:
            return (1357, 808)
        return (860, 683)

    monkeypatch.setattr(exit_game.game_map, "find_template_in", match)
    monkeypatch.setattr(
        exit_game.game_map, "ocr_region",
        lambda img, region: ([("退出游戏", (1279, 763, 1348, 784), 1.0)], None))
    ok, info = exit_game._open_and_click_exit(win)
    assert ok is True
    assert clicks == [(1357, 808), (1313, 773), (860, 683)]
