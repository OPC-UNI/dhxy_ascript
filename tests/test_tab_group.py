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


class _CapWin(_FakeWin):
    """带 capture 的假窗口（归队 pass 内帧级战斗判定用）。"""

    def __init__(self, frame=None):
        super().__init__()
        self.frame = frame

    def capture(self, force_activate=False):
        return self.frame


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


# ---- todo3 归队 pass ----

def test_tab_char_name_parses_title():
    # 实测格式（2026-11-07 用户日志）：... $Revision - 服务器 - 角色名（ID：账号）
    assert tab_group._tab_char_name(
        "[大话西游2_经典版] 大话西游2经典版 $Revision: 2057701"
        " - 佳期良年 - 碧桃娘子（ID：888888888）") == "碧桃娘子"
    # 旧格式兼容：角色名（ID：账号）
    assert tab_group._tab_char_name("碧桃娘子（ID：123456）") == "碧桃娘子"
    assert tab_group._tab_char_name("未登录默认标题") is None
    assert tab_group._tab_char_name("") is None


def test_name_similar_fuzzy():
    assert tab_group._name_similar("碧桃娘子", "碧桃娘子")
    assert tab_group._name_similar("碧桃娘子", "碧桃娘了")   # OCR 噪声容差
    # 娘子/浪子比 0.75 会命中——同组相似名双号时两个 Tab 都切过去，
    # 非轮回司的 Tab 由 maybe_rejoin 位置判定自然跳过，无害
    assert tab_group._name_similar("碧桃娘子", "碧桃浪子")
    assert not tab_group._name_similar("碧桃娘子", "张三")
    assert not tab_group._name_similar("", "碧桃娘子")
    assert not tab_group._name_similar("碧桃娘子", None)


def _make_rejoin_env(monkeypatch, children, switches):
    """归队 pass 测试环境：假激活状态 + 脚本化 Tab 枚举与切换。"""
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_leader_id", "id-leader")
    monkeypatch.setattr(tab_group, "_tab_count", 3)  # _for_each_member 循环上限
    monkeypatch.setattr(tab_group, "_try_enter_pass", lambda: True)
    monkeypatch.setattr(tab_group, "_exit_pass", lambda: None)
    monkeypatch.setattr(tab_group, "_pass_foreground_ok", lambda win: True)
    monkeypatch.setattr(tab_group, "_last_rejoin_pass_at", 0.0)
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda win: list(children))
    it = iter(switches)
    monkeypatch.setattr(tab_group, "_switch_next",
                        lambda win: next(it, None))
    returned = []
    monkeypatch.setattr(tab_group, "return_to_leader",
                        lambda win: returned.append(1))
    return returned


def test_rejoin_pass_matches_and_rejoins_member(monkeypatch):
    """暂离昵称匹配到队员 Tab → 切过去归队成功，返回匹配昵称集合。"""
    children = [
        {"id": "id-leader", "title": "队长号（ID：id-leader）", "visible": True},
        {"id": "id-m1", "title": "碧桃娘子（ID：id-m1）", "visible": False},
        {"id": "id-m2", "title": "张三（ID：id-m2）", "visible": False},
    ]
    returned = _make_rejoin_env(monkeypatch, children,
                                ["id-m2", "id-m1", "id-leader"])
    called = []

    def _maybe_rejoin(win, prefix="[组队]"):
        called.append(prefix)
        return True, "已归队（当前位置: 宝象国[86,86]）"

    import src.team as team_mod
    monkeypatch.setattr(team_mod, "maybe_rejoin", _maybe_rejoin)
    # 战斗判定必须走帧级 is_in_battle_frame（pass_active 期间 is_in_battle
    # 恒 True——误用把轮回司误报战斗跳过归队，2026-11-07 用户日志）
    from src import battle as battle_mod

    def _boom(win):
        raise AssertionError("pass 内不得调用 is_in_battle（pass_active 恒 True）")

    monkeypatch.setattr(battle_mod, "is_in_battle", _boom)
    monkeypatch.setattr(battle_mod, "is_in_battle_frame", lambda img: False)

    done, info, matched = tab_group.member_rejoin_pass(
        _CapWin(), ["碧桃娘子"])  # 帧级判定：capture 返回 None → 不判战斗
    assert done == 1 and "已归队 1 名队员" in info
    assert matched == {"碧桃娘子"}
    assert len(called) == 1       # 只有匹配的 m1 执行了归队
    # _for_each_member 结束切回一次 + finally 兜底再切回一次（幂等）
    assert returned == [1, 1]


def test_rejoin_pass_no_match_no_switch(monkeypatch):
    """暂离昵称无 Tab 匹配：不做任何切换，返回空集合。"""
    children = [
        {"id": "id-leader", "title": "队长号（ID：id-leader）", "visible": True},
        {"id": "id-m1", "title": "张三（ID：id-m1）", "visible": False},
    ]
    switched = []

    def _switch(_win):
        switched.append(1)
        return None

    _make_rejoin_env(monkeypatch, children, [])
    monkeypatch.setattr(tab_group, "_switch_next", _switch)
    done, info, matched = tab_group.member_rejoin_pass(
        _FakeWin(), ["碧桃娘子"])
    assert done == 0 and info == "" and matched == set()
    assert switched == []


def test_rejoin_pass_throttled(monkeypatch):
    """30s 节流：间隔内重复调用直接返回，不切 Tab。"""
    import time as _time
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_last_rejoin_pass_at",
                        _time.time() - 10)
    switched = []
    monkeypatch.setattr(tab_group, "_switch_next",
                        lambda win: switched.append(1) or None)
    done, info, matched = tab_group.member_rejoin_pass(
        _FakeWin(), ["碧桃娘子"])
    assert done == 0 and info == "" and matched == set()
    assert switched == []


# ---- 主进程诊断护栏：切回绑定角色 Tab ----

def _bound_tab_env(monkeypatch, bound_id="123456", logged_ids=("123456", "999999"),
                   cur_id="999999", switches=("123456",)):
    """ensure_bound_tab 测试环境（绑定账号 ID 为数字，同真实格式）。"""
    monkeypatch.setattr(tab_group.game, "bound_window_id",
                        lambda: {"id": f"（ID：{bound_id}）", "hwnd": 1,
                                 "title": f"角色（ID：{bound_id}）"})
    children = [{"id": i, "title": f"角色（ID：{i}）", "visible": i == cur_id}
                for i in logged_ids]
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda win: list(children))
    monkeypatch.setattr(tab_group, "_current_id", lambda win: cur_id)
    it = iter(switches)
    monkeypatch.setattr(tab_group, "_switch_next", lambda win: next(it, None))


def test_ensure_bound_tab_no_binding(monkeypatch):
    monkeypatch.setattr(tab_group.game, "bound_window_id", lambda: None)
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert ok and "未绑定" in info


def test_ensure_bound_tab_single_account(monkeypatch):
    """单号（已登录 Tab <2）：无切换目标，放行。"""
    _bound_tab_env(monkeypatch, logged_ids=("123456",))
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert ok and "单号" in info


def test_ensure_bound_tab_already_on_bound(monkeypatch):
    _bound_tab_env(monkeypatch, cur_id="123456")
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert ok and "已在绑定角色 Tab" in info


def test_ensure_bound_tab_switches_back(monkeypatch):
    """停在队员 Tab（999999）→ 循环切换命中绑定 Tab。"""
    switched = []

    def _switch(win):
        switched.append(1)
        return next(it)

    _bound_tab_env(monkeypatch, switches=("888888", "123456"))
    it = iter(("888888", "123456"))
    monkeypatch.setattr(tab_group, "_switch_next", _switch)
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert ok and "已切回绑定角色 Tab" in info
    assert len(switched) == 2


def test_ensure_bound_tab_switch_failure(monkeypatch):
    _bound_tab_env(monkeypatch, switches=(None,))
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert not ok and "切回绑定角色 Tab 失败" in info


def test_ensure_bound_tab_bound_not_logged(monkeypatch):
    """绑定角色 Tab 未登录：无法切回，按当前 Tab 执行（告警不阻断）。"""
    _bound_tab_env(monkeypatch, logged_ids=("999999", "888888"),
                   cur_id="999999")
    ok, info = tab_group.ensure_bound_tab(_FakeWin())
    assert ok and "未登录" in info


def test_rejoin_pass_force_bypasses_throttle(monkeypatch):
    """force=True（诊断按钮）：30s 节流不生效，手动连点不被静默吞掉。"""
    import time as _time
    monkeypatch.setattr(tab_group, "_active", False)
    monkeypatch.setattr(tab_group, "_last_rejoin_pass_at", _time.time())
    monkeypatch.setattr(tab_group, "_try_enter_pass", lambda: True)
    monkeypatch.setattr(tab_group, "_exit_pass", lambda: None)
    monkeypatch.setattr(tab_group, "_pass_foreground_ok", lambda win: True)
    children = [
        {"id": "id-leader", "title": "队长号（ID：id-leader）", "visible": True},
        {"id": "id-m1", "title": "碧桃娘子（ID：id-m1）", "visible": False},
    ]
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda win: list(children))
    monkeypatch.setattr(tab_group, "_tab_count", 2)
    monkeypatch.setattr(tab_group, "_switch_next",
                        lambda win: "id-m1")
    monkeypatch.setattr(tab_group, "return_to_leader", lambda win: None)
    # 非 force：节流返回空
    done, info, _matched = tab_group.member_rejoin_pass(
        _FakeWin(), ["碧桃娘子"])
    assert done == 0 and info == ""
    # force：绕过节流执行（进入匹配与遍历）
    done, info, matched = tab_group.member_rejoin_pass(
        _FakeWin(), ["碧桃娘子"], force=True)
    assert matched == {"碧桃娘子"}


def test_init_task_logs_member_tab_names(monkeypatch):
    """组号激活日志带队员 Tab 角色名（用户 2026-11-07 要求，人工核对
    队员对应关系）。"""
    children = [
        {"id": "241147824",
         "title": "[大话西游2_经典版] 大话西游2经典版 $Revision: 2057701"
                  " - 佳期良年 - 队长号（ID：241147824）",
         "visible": True},
        {"id": "888888888",
         "title": "[大话西游2_经典版] 大话西游2经典版 $Revision: 2057701"
                  " - 佳期良年 - 碧桃娘子（ID：888888888）",
         "visible": False},
        {"id": "999999999", "title": "标题异常无角色名", "visible": False},
    ]
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda win: list(children))
    logged = []
    monkeypatch.setattr(tab_group, "log",
                        lambda msg, level="INFO": logged.append(msg))
    tab_group.init_task(_FakeWin(), "修罗", True)
    msg = next(m for m in logged if "组号模式激活" in m)
    assert "队员 2 个" in msg
    assert "碧桃娘子" in msg
    assert "999999999" in msg  # 角色名解析失败回退账号 ID
    assert "241147824" in msg  # 队长 ID 保留
