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

from src.core import app_utils
from src.device import tab_group

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


# 模块级组号状态（init_task/pass 直接改写全局变量，不受 monkeypatch 管理）
_GROUP_STATE_ATTRS = ("_active", "_mode", "_leader_id", "_tab_count",
                      "_logged_count", "_members", "_team_member_ids",
                      "_acted_ids", "_no_members_logged", "pass_active",
                      "_last_bars_pass_at", "_last_rejoin_pass_at",
                      "_off_leader_log_at", "_switch_fail_streak",
                      "_switch_fail_alerted")


@pytest.fixture(autouse=True)
def _restore_group_state(monkeypatch):
    """用例结束后恢复组号模块状态：init_task 会置 _active/_leader_id，
    泄漏到其他测试文件会改变 on_leader_screen 等判定。"""
    for name in _GROUP_STATE_ATTRS:
        monkeypatch.setattr(tab_group, name, getattr(tab_group, name))


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
    from src.device import silent_click
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
    clicks, invalidates = [], []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0)
    from src.device import silent_click
    monkeypatch.setattr(silent_click, "invalidate_tab_items",
                        lambda _w: invalidates.append(True))
    list_tabs, _ = _list_tabs_script([0xA, 0xA, 0xB])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) == "idb"
    assert clicks == [1, 1]  # 重试同一序号：不重算（防竞态连切两个）
    assert invalidates == [True]  # 重试前丢弃矩形缓存重读 UIA


def test_switch_next_by_click_never_switches_returns_none(monkeypatch):
    clicks, invalidates = [], []
    _mock_silent_click(monkeypatch, clicks, tab=TAB_HWND, count=3, cur=0)
    from src.device import silent_click
    monkeypatch.setattr(silent_click, "invalidate_tab_items",
                        lambda _w: invalidates.append(True))
    list_tabs, _ = _list_tabs_script([0xA, 0xA, 0xA])
    monkeypatch.setattr(tab_group.game, "list_tab_windows", list_tabs)
    assert tab_group._switch_next_by_click(_FakeWin()) is None
    assert clicks == [1, 1, 1]  # 最多尝试 TAB_SWITCH_TRIES 次
    assert invalidates == [True, True]


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
    from src.device import silent_click
    g = tab_group.game.GAME_TITLE
    items = [(g, (0, 0, 1, 1)), ("角色1", (0, 0, 1, 1)), (g, (0, 0, 1, 1)),
             ("角色3", (0, 0, 1, 1))]
    monkeypatch.setattr(silent_click, "tab_items", lambda _w, _c: items)
    assert tab_group._next_logged_index(_FakeWin(), None, 0, 4) == 1
    assert tab_group._next_logged_index(_FakeWin(), None, 1, 4) == 3
    # 从 3 绕回 1（0 与 2 未登录）
    assert tab_group._next_logged_index(_FakeWin(), None, 3, 4) == 1


def test_next_logged_index_fallbacks(monkeypatch):
    from src.device import silent_click
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


@pytest.fixture(autouse=True)
def _reset_switch_fail_streak():
    """每个用例从干净失败链开始（模块级状态，用例内会累加）。"""
    tab_group._switch_fail_streak = 0
    tab_group._switch_fail_alerted = False


def _fail_switch_env(monkeypatch, results):
    """切 Tab 失败告警测试环境：静默点击通道返回 results 序列，记录日志。"""
    logs = []
    monkeypatch.setattr(tab_group, "log",
                        lambda msg, level="INFO": logs.append((level, msg)))
    monkeypatch.setattr(app_utils, "keyboard_ok", lambda _w: False)
    it = iter(results)
    monkeypatch.setattr(tab_group, "_switch_next_by_click", lambda _w: next(it))
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda _w: [{"id": "286958560", "visible": True,
                                     "hwnd": 0xA, "title": "队长（ID：286958560）"}])
    monkeypatch.setattr(tab_group, "_tab_count", 2)
    return logs


def test_switch_fail_escalates_to_error_once(monkeypatch):
    """连续 3 次切 Tab 失败升级一条 ERROR 显眼告警（第 1、2 次不报）；
    同一失败链只报一次，第 4 次失败不再刷 ERROR。"""
    logs = _fail_switch_env(monkeypatch, [None, None, None, None])
    for _ in range(2):
        assert tab_group._switch_next(_FakeWin()) is None
    assert not [m for lv, m in logs if lv == "ERROR"]
    assert tab_group._switch_next(_FakeWin()) is None
    errs = [m for lv, m in logs if lv == "ERROR"]
    assert len(errs) == 1
    assert "连续 3 次切 Tab 失败" in errs[0]
    assert "启动时窗口内 2 个 Tab，当前已登录 1 个" in errs[0]
    assert "独立游戏窗口" in errs[0] and "拖回" in errs[0]  # 恢复动作
    assert tab_group._switch_next(_FakeWin()) is None
    assert len([m for lv, m in logs if lv == "ERROR"]) == 1


def test_switch_fail_streak_resets_on_success(monkeypatch):
    """成功一次即清零：失败-失败-成功-失败×3 → 成功之后重新升级告警。"""
    logs = _fail_switch_env(monkeypatch, [None, None, "idB",
                                          None, None, None, None])
    for _ in range(3):
        tab_group._switch_next(_FakeWin())
    assert not [m for lv, m in logs if lv == "ERROR"]
    for _ in range(4):
        tab_group._switch_next(_FakeWin())
    assert len([m for lv, m in logs if lv == "ERROR"]) == 1


def test_fail_detail_level_downgrades(monkeypatch):
    """失败明细级别：失败链前两条 WARN，之后降为 INFO（不再每轮一条 WARN）。"""
    monkeypatch.setattr(tab_group, "_switch_fail_streak", 0)
    assert tab_group._fail_log_level() == "WARN"
    monkeypatch.setattr(tab_group, "_switch_fail_streak", 1)
    assert tab_group._fail_log_level() == "WARN"
    monkeypatch.setattr(tab_group, "_switch_fail_streak", 2)
    assert tab_group._fail_log_level() == "INFO"


def test_for_each_member_fail_summary_downgraded(monkeypatch):
    """pass 失败汇总行同样降级：首次 WARN、失败链上 INFO。"""
    logs = []
    monkeypatch.setattr(tab_group, "log",
                        lambda msg, level="INFO": logs.append((level, msg)))
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_tab_count", 2)
    monkeypatch.setattr(tab_group, "return_to_leader", lambda win: None)
    monkeypatch.setattr(tab_group, "_switch_next", lambda win: None)
    tab_group._for_each_member(_FakeWin(), lambda w, t: None, "回血回蓝 pass")
    monkeypatch.setattr(tab_group, "_switch_fail_streak", 2)
    tab_group._for_each_member(_FakeWin(), lambda w, t: None, "回血回蓝 pass")
    levels = [lv for lv, m in logs if "切 Tab 失败，中止" in m]
    assert levels == ["WARN", "INFO"]


def test_init_task_resets_switch_fail_streak(monkeypatch):
    """新任务重新计失败链：上个任务残留的失败链不带到下个任务。"""
    monkeypatch.setattr(tab_group, "_switch_fail_streak", 5)
    monkeypatch.setattr(tab_group, "_switch_fail_alerted", True)
    monkeypatch.setattr(tab_group.game, "list_tab_windows", lambda win: [])
    tab_group.init_task(_FakeWin(), "修罗", False)  # 开关关：走重置后提前返回
    assert tab_group._switch_fail_streak == 0
    assert tab_group._switch_fail_alerted is False


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
    # 非暂离 Tab 面板无归队按钮，rejoin_from_away 只报告不点击，无害
    assert tab_group._name_similar("碧桃娘子", "碧桃浪子")
    assert not tab_group._name_similar("碧桃娘子", "张三")
    assert not tab_group._name_similar("", "碧桃娘子")
    assert not tab_group._name_similar("碧桃娘子", None)
    # 易混字符归一化（2026-11-09 教训：〃忆ㄨ嚣魂ˉ 被面板 OCR 稳定
    # 误读成 ”忆×嚣魂一，原始模糊比 0.5 < 0.7 漏配，组号少识别 1 人）
    assert tab_group._name_similar("〃忆ㄨ嚣魂ˉ", "”忆×嚣魂一")
    assert tab_group._name_similar("道影”", '道影"')
    assert not tab_group._name_similar("〃忆ㄨ嚣魂ˉ", "义战天下メ帝")
    # 归一化只合并同形家族，不降低普通名字区分度
    assert not tab_group._name_similar("天一", "天1")


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
        return True, "已归队（归队按钮已消失）"

    import src.device.team as team_mod
    monkeypatch.setattr(team_mod, "rejoin_from_away", _maybe_rejoin)
    # 战斗判定必须走帧级 is_in_battle_frame（pass_active 期间 is_in_battle
    # 恒 True——误用把轮回司误报战斗跳过归队，2026-11-07 用户日志）
    from src.device import battle as battle_mod

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
    # 归队 handler 内帧级战斗判定会调 win.capture：必须用 _CapWin
    # （_FakeWin 无 capture → AttributeError 被 _for_each_member
    # 吞掉记 WARN，2026-11-06 日志噪音教训）
    import src.device.team as team_mod
    monkeypatch.setattr(team_mod, "rejoin_from_away",
                        lambda win, prefix="[组队]": (True, "已归队（模拟）"))
    # 非 force：节流返回空
    done, info, _matched = tab_group.member_rejoin_pass(
        _FakeWin(), ["碧桃娘子"])
    assert done == 0 and info == ""
    # force：绕过节流执行（进入匹配与遍历，handler 走通无异常）
    done, info, matched = tab_group.member_rejoin_pass(
        _CapWin(), ["碧桃娘子"], force=True)
    assert matched == {"碧桃娘子"}
    assert done == 2  # _switch_next 恒返回 id-m1，_tab_count=2 两轮都命中


def test_init_task_logs_member_tab_names(monkeypatch):
    """init 日志只陈述事实：在哪个 Tab 启动 + 其余已登录 Tab 角色名
    （用户 2026-11-07 要求，人工核对对应关系）；队长与组号结论待
    队伍检查确认，init 不得提前下结论（用户纠正）。"""
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
    msg = next(m for m in logged if "本任务在 Tab" in m)
    assert "队长号" in msg  # 启动所在 Tab 角色名
    assert "241147824" in msg  # 启动 Tab ID
    assert "已登录 3 个" in msg
    assert "碧桃娘子" in msg  # 其余已登录 Tab 角色名
    assert "999999999" in msg  # 角色名解析失败回退账号 ID
    assert "队长与组号结论待队伍检查确认" in msg  # 不下结论的提示


# ---- 组号成员战斗动作（2026-11-07：按账号设置/启动检查/队长解析） ----


class _ScanWin(_FakeWin):
    """带 capture 的假窗口（scan_group_members 开面板后截帧）。"""

    def capture(self, force_activate=False):
        return object()  # 面板帧内容不参与（panel_member_names 已打桩）


def _scan_env(monkeypatch, children, panel_names, saved=None,
              open_ok=True, open_info="队伍面板已打开"):
    """scan_group_members 打桩：Tab 枚举 + 队伍面板昵称 + 已存动作。

    panel_names=None 模拟面板昵称读不到（宽松路径）。
    """
    monkeypatch.setattr(tab_group.game, "list_tab_windows",
                        lambda win: list(children))
    import src.device.team as team_mod
    monkeypatch.setattr(team_mod, "is_team_panel_open",
                        lambda win: (False, "队伍面板未打开"))
    monkeypatch.setattr(team_mod, "open_team_panel",
                        lambda win: (open_ok, open_info))
    monkeypatch.setattr(team_mod, "close_team_panel",
                        lambda win: (True, "队伍面板已关闭"))
    monkeypatch.setattr(team_mod, "panel_member_names",
                        lambda img: panel_names)
    monkeypatch.setattr(tab_group, "load_group_actions",
                        lambda: saved or {})


def _scan_children():
    """3 个 Tab：队长可见 + 2 个队员（其一在队伍、其一不在）。"""
    return [
        {"id": "idL", "title": "队长甲（ID：idL）", "visible": True},
        {"id": "idM1", "title": "碧桃娘子（ID：idM1）", "visible": False},
        {"id": "idM2", "title": "陌生人（ID：idM2）", "visible": False},
    ]


def test_scan_group_members_matching(monkeypatch):
    """在队伍中的队员 Tab 按标题角色名与面板昵称模糊匹配确认；
    面板中未匹配到的 Tab 标记不在队伍。"""
    _scan_env(monkeypatch, _scan_children(),
              [(0, "队长甲"), (1, "碧桃娘子"), (2, "玩家乙")],
              saved={"idM1": {"char": "攻击", "beast": "防御"}})
    ok, info, data = tab_group.scan_group_members(_ScanWin())
    assert ok and data is not None
    assert data["leader"]["id"] == "idL"
    assert data["leader"]["name"] == "队长甲"
    m1 = next(m for m in data["members"] if m["id"] == "idM1")
    assert m1["in_team"] is True
    assert m1["char"] == "攻击" and m1["beast"] == "防御"
    m2 = next(m for m in data["members"] if m["id"] == "idM2")
    assert m2["in_team"] is False
    assert m2["char"] is None
    assert "队长甲" in info and "碧桃娘子" in info
    assert set(data["options"].keys()) == {"char", "beast"}


def test_scan_group_members_lenient_no_panel(monkeypatch):
    """队伍面板打不开（未组队等）：全员按未确认处理，不报失败——
    宽松兜底（用户确认：不拦截启动，未确认 Tab 只点自动）。"""
    _scan_env(monkeypatch, _scan_children(), None, open_ok=False,
              open_info="组队菜单未打开")
    ok, _info, data = tab_group.scan_group_members(_ScanWin())
    assert ok and data is not None
    assert all(m["in_team"] is False for m in data["members"])


def test_scan_group_members_lenient_names_none(monkeypatch):
    """面板打开但昵称未识别（panel_member_names=None）：同样宽松。"""
    _scan_env(monkeypatch, _scan_children(), None)
    ok, _info, data = tab_group.scan_group_members(_ScanWin())
    assert ok and data is not None
    assert all(m["in_team"] is False for m in data["members"])


def test_leader_battle_actions_resolution(monkeypatch):
    """队长动作解析：队长账号有组号设置则用之；无设置/单号模式回退
    通用战斗设置（队长换人自动生效，2026-11-07 需求）。"""
    global_actions = {"char": "技能3-5", "beast": "攻击"}
    group_actions = {"idB": {"char": "防御", "beast": "无"}}
    monkeypatch.setattr(tab_group, "load_group_actions",
                        lambda: group_actions)
    monkeypatch.setattr(tab_group, "load_battle_actions",
                        lambda: global_actions)
    monkeypatch.setattr(tab_group, "_leader_id", "idB")
    assert tab_group.leader_battle_actions() == {"char": "防御", "beast": "无"}
    monkeypatch.setattr(tab_group, "_leader_id", "idA")  # 无组号设置
    assert tab_group.leader_battle_actions() == global_actions
    monkeypatch.setattr(tab_group, "_leader_id", None)  # 单号模式
    assert tab_group.leader_battle_actions() == global_actions


def test_startup_member_check_gating(monkeypatch):
    """启动成员检查：确认在队伍中的队员未设置 → 返回缺失名单（停止
    任务）；已确认者写入 _team_member_ids。"""
    def _scan_ret(members):
        return (True, "ok", {"leader": {"id": "idL", "name": "队长甲",
                                        "char": None, "beast": None},
                             "members": members})

    members = [
        {"id": "idM1", "name": "碧桃娘子", "in_team": True,
         "char": "攻击", "beast": "防御"},
        {"id": "idM2", "name": "陌生人", "in_team": True,
         "char": None, "beast": None},
        {"id": "idM3", "name": "局外人", "in_team": False,
         "char": None, "beast": None},
    ]
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: _scan_ret(members))
    missing, info = tab_group.startup_member_check(_ScanWin())
    assert [m["id"] for m in missing] == ["idM2"]
    assert "陌生人" in info
    assert tab_group._team_member_ids == {"idM1", "idM2"}
    # 全部设置 → 不拦截
    members[1] = {"id": "idM2", "name": "陌生人", "in_team": True,
                  "char": "技能1-1", "beast": "攻击"}
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: _scan_ret(members))
    missing, info = tab_group.startup_member_check(_ScanWin())
    assert missing is None and "全部设置" in info
    assert tab_group._team_member_ids == {"idM1", "idM2"}


def test_startup_member_check_lenient(monkeypatch):
    """无确认成员（面板昵称未识别）：宽松放行，_team_member_ids 清空。"""
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: (True, "成员未确认",
                                     {"leader": {"id": "idL", "name": "队长甲",
                                                 "char": None, "beast": None},
                                      "members": [
                                          {"id": "idM1", "name": "队员乙",
                                           "in_team": False,
                                           "char": None, "beast": None}]}))
    missing, info = tab_group.startup_member_check(_ScanWin())
    assert missing is None
    assert "宽松" in info
    assert tab_group._team_member_ids == set()


def test_is_noop_actions():
    assert tab_group._is_noop_actions({"char": "无", "beast": "无"}) is True
    assert tab_group._is_noop_actions({"char": "无", "beast": "攻击"}) is False
    assert tab_group._is_noop_actions({"char": "攻击", "beast": "无"}) is False


# ---- 启动一次开面板 + 组号识别状态 + 动作只执行一次（2026-11-07） ----


def test_startup_check_single_panel_open(monkeypatch):
    """启动检查一次开面板完成队长判定+昵称读取（旧链路开 3 次面板的
    回归测试）；判定一次即队长，不重开。"""
    import src.device.team as team_mod
    import src.device.battle as battle_mod
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda win: False)
    monkeypatch.setattr(team_mod, "clear_pending_apply",
                        lambda win: (False, "无申请待处理态"))
    opens = []

    def classify(win, **kw):
        opens.append("classify")
        return "leader", "是队长（面板可见「解散队伍」）"
    monkeypatch.setattr(team_mod, "classify_leader_with_panel", classify)
    monkeypatch.setattr(team_mod, "panel_member_names",
                        lambda img: [(0, "队长甲"), (1, "碧桃娘子")])
    monkeypatch.setattr(team_mod, "close_team_panel",
                        lambda win: (True, "队伍面板已关闭"))
    ok, info, names = tab_group.startup_check(_ScanWin())
    assert ok and "是队长" in info
    assert names[1][1] == "碧桃娘子"
    assert opens == ["classify"]


def test_startup_check_forwards_create_flag(monkeypatch):
    """自动创建并入启动检查：create_if_missing/prefix 原样传给队长判定
    （2026-11-11 组号逻辑优化：不再先单独开菜单查是否组队）。"""
    import src.device.team as team_mod
    import src.device.battle as battle_mod
    seen = {}
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda win: False)
    monkeypatch.setattr(team_mod, "clear_pending_apply",
                        lambda win: (False, "无申请待处理态"))

    def classify(win, **kw):
        seen.update(kw)
        return "leader", "是队长"

    monkeypatch.setattr(team_mod, "classify_leader_with_panel", classify)
    monkeypatch.setattr(team_mod, "panel_member_names",
                        lambda img: [(0, "队长甲")])
    monkeypatch.setattr(team_mod, "close_team_panel",
                        lambda win: (True, ""))
    tab_group.startup_check(_ScanWin(), create_if_missing=True,
                            prefix="[五倍][创建队伍]")
    assert seen == {"create_if_missing": True,
                    "prefix": "[五倍][创建队伍]"}


def test_startup_team_count_lower_bound():
    """启动检查面板昵称人数下限：None 区域（空位/漏读）不计；面板未
    读到（None）返回 None 交正常检查。"""
    assert tab_group.startup_team_count(None) is None
    assert tab_group.startup_team_count(
        [(0, "队长甲"), (1, None), (2, "玩家乙")]) == 2
    assert tab_group.startup_team_count([(0, None)] * 5) == 0


def test_startup_check_unclear_rechecks_without_reopen(monkeypatch):
    """分类不明 3s 后同面板重判一次（classify 复用已开面板，不重新
    开面板）。"""
    import src.device.team as team_mod
    import src.device.battle as battle_mod
    import time as time_mod
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda win: False)
    monkeypatch.setattr(team_mod, "clear_pending_apply",
                        lambda win: (False, "无申请待处理态"))
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)
    calls = {"n": 0}

    def classify(win, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            return "leader", "是队长（面板可见「解散队伍」）"
        return "unclear", "队长无法判定（面板内解散/离开按钮未命中）"
    monkeypatch.setattr(team_mod, "classify_leader_with_panel", classify)
    monkeypatch.setattr(team_mod, "panel_member_names", lambda img: None)
    monkeypatch.setattr(team_mod, "close_team_panel",
                        lambda win: (True, ""))
    ok, _info, names = tab_group.startup_check(_ScanWin())
    assert ok and calls["n"] == 2
    assert names is None


def test_startup_check_clears_pending_apply_first(monkeypatch):
    """启动检查先清申请待处理态再判队长（2026-11-07 22:03 教训：
    item2 命中即确定性队长，不清空则菜单打不开、判定必失败停任务）。"""
    import src.device.team as team_mod
    import src.device.battle as battle_mod
    order = []
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda win: False)
    monkeypatch.setattr(team_mod, "clear_pending_apply",
                        lambda win: order.append("clear") or (True, "已清空申请列表并关闭面板"))
    monkeypatch.setattr(team_mod, "classify_leader_with_panel",
                        lambda win, **kw: order.append("classify") or ("leader", "是队长"))
    monkeypatch.setattr(team_mod, "panel_member_names", lambda img: [(0, "队长甲")])
    monkeypatch.setattr(team_mod, "close_team_panel", lambda win: (True, ""))
    ok, _info, _names = tab_group.startup_check(_ScanWin())
    assert ok and order == ["clear", "classify"]


def test_startup_member_check_writes_last_scan(monkeypatch):
    """启动检查写队伍检查快照（组号页打开自动展示）：只含在队伍中的
    队员，非组号成员不进快照。"""
    writes = {}
    monkeypatch.setattr(tab_group.app_utils, "role_state_set",
                        lambda key, value: writes.__setitem__(key, value) or True)
    data = {"panel_read": True,
            "leader": {"id": "idL", "name": "队长甲",
                       "char": None, "beast": None},
            "members": [
                {"id": "idM1", "name": "碧桃娘子", "in_team": True,
                 "char": "攻击", "beast": "防御"},
                {"id": "idM2", "name": "路人", "in_team": False,
                 "char": None, "beast": None}]}
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: (True, "ok", data))
    tab_group.startup_member_check(_ScanWin())
    snap = writes.get("group_last_scan")
    assert snap is not None
    assert snap["leader"]["id"] == "idL"
    assert snap["members"] == [{"id": "idM1", "name": "碧桃娘子"}]


def test_startup_member_check_writes_group_detected(monkeypatch):
    """面板昵称可读时同步写组号识别状态：确认到队员=组号 True、
    读不到不写（宽松保留上次状态）。"""
    writes = {}
    monkeypatch.setattr(tab_group.app_utils, "role_state_set",
                        lambda key, value: writes.__setitem__(key, value) or True)
    data = {"panel_read": True,
            "leader": {"id": "idL", "name": "队长甲",
                       "char": None, "beast": None},
            "members": [
                {"id": "idM1", "name": "碧桃娘子", "in_team": True,
                 "char": "攻击", "beast": "防御"}]}
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: (True, "ok", data))
    tab_group.startup_member_check(_ScanWin())
    assert writes.get("group_detected") is True
    # 面板昵称读不到：不写（保留上次状态）
    writes.clear()
    data2 = {"panel_read": False,
             "leader": {"id": "idL", "name": "队长甲",
                        "char": None, "beast": None},
             "members": [
                 {"id": "idM1", "name": "队员乙", "in_team": False,
                  "char": None, "beast": None}]}
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: (True, "ok", data2))
    tab_group.startup_member_check(_ScanWin())
    assert "group_detected" not in writes


def test_member_one_acted_member_just_auto(monkeypatch):
    """已执行过配置动作的队员（_acted_ids）pass 再触发时直接点自动，
    不取消自动重做动作（用户 2026-11-07 状态机第 2 种情况）。"""
    monkeypatch.setattr(tab_group, "_team_member_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_acted_ids", {"idM1"})
    calls = []
    monkeypatch.setattr(tab_group, "_act_one",
                        lambda w, t, a, w2: calls.append("act") or True)
    monkeypatch.setattr(tab_group, "_auto_one",
                        lambda w, t, w2: calls.append("auto"))
    monkeypatch.setattr(tab_group, "load_group_actions",
                        lambda: {"idM1": {"char": "攻击", "beast": "防御"}})
    tab_group._member_one(_FakeWin(), "idM1", 1.0, act_configured=True)
    assert calls == ["auto"]


def test_member_one_acts_and_records(monkeypatch):
    """未执行过的队员走配置动作路径并记入 _acted_ids（下次触发点自动）。"""
    monkeypatch.setattr(tab_group, "_team_member_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_acted_ids", set())
    calls = []
    monkeypatch.setattr(tab_group, "_act_one",
                        lambda w, t, a, w2: calls.append("act") or True)
    monkeypatch.setattr(tab_group, "_auto_one",
                        lambda w, t, w2: calls.append("auto"))
    monkeypatch.setattr(tab_group, "load_group_actions",
                        lambda: {"idM1": {"char": "攻击", "beast": "防御"}})
    tab_group._member_one(_FakeWin(), "idM1", 1.0, act_configured=True)
    assert calls == ["act"]
    assert "idM1" in tab_group._acted_ids


def test_member_one_unconfirmed_member_just_auto(monkeypatch):
    """启动检查未确认在队伍的 Tab：即使有已存动作也只点自动
    （其账号可能不在队伍，取消自动执行动作会干扰它自己的场景）。"""
    monkeypatch.setattr(tab_group, "_team_member_ids", set())
    monkeypatch.setattr(tab_group, "_acted_ids", set())
    calls = []
    monkeypatch.setattr(tab_group, "_act_one",
                        lambda w, t, a, w2: calls.append("act") or True)
    monkeypatch.setattr(tab_group, "_auto_one",
                        lambda w, t, w2: calls.append("auto"))
    monkeypatch.setattr(tab_group, "load_group_actions",
                        lambda: {"idM1": {"char": "攻击", "beast": "防御"}})
    tab_group._member_one(_FakeWin(), "idM1", 1.0, act_configured=True)
    assert calls == ["auto"]


def test_deactivate_group_hides_and_disables(monkeypatch):
    """组号降级（启动未组队）：本次运行关闭组号模式（pass 不再激活）、
    清空成员状态，并写组号识别状态 False（侧边栏入口隐藏）。"""
    writes = {}
    monkeypatch.setattr(tab_group.app_utils, "role_state_set",
                        lambda key, value: writes.__setitem__(key, value) or True)
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_mode", "修罗")
    monkeypatch.setattr(tab_group, "_team_member_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_acted_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_leader_id", "idL")
    tab_group.deactivate_group(reason="未组队（菜单中无「队伍信息」）")
    assert tab_group._active is False
    assert tab_group._mode == ""
    assert tab_group._team_member_ids == set()
    assert tab_group._acted_ids == set()
    assert writes.get("group_detected") is False
    # 队长 ID 保留：队长动作解析仍可用（该账号有组号设置继续用它的）
    assert tab_group._leader_id == "idL"


# ---- 只操作确认组员（2026-11-07 基本原则：比对后才是组号） ----


def test_for_each_member_only_members_skips_nonteam(monkeypatch):
    """only_members=True：只处理确认在队伍中的 Tab，其余 Tab 切过去
    但 handler 不执行（可能在别的队伍/干别的事，不碰）。"""
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_team_member_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_leader_id", "idL")
    monkeypatch.setattr(tab_group, "_tab_count", 3)
    seq = iter(["idM2", "idM1", "idL"])
    monkeypatch.setattr(tab_group, "_switch_next", lambda win: next(seq))
    monkeypatch.setattr(tab_group, "return_to_leader", lambda win: None)
    calls = []
    tab_group._for_each_member(_FakeWin(),
                               lambda w, t: calls.append(t),
                               "测试 pass", only_members=True)
    assert calls == ["idM1"]


def test_for_each_member_force_visits_all(monkeypatch):
    """force=True（验证模式，无队伍检查结果）：维持全部遍历旧语义。"""
    monkeypatch.setattr(tab_group, "_team_member_ids", {"idM1"})
    monkeypatch.setattr(tab_group, "_leader_id", "idL")
    monkeypatch.setattr(tab_group, "_tab_count", 3)
    seq = iter(["idM2", "idM1", "idL"])
    monkeypatch.setattr(tab_group, "_switch_next", lambda win: next(seq))
    monkeypatch.setattr(tab_group, "return_to_leader", lambda win: None)
    calls = []
    tab_group._for_each_member(_FakeWin(),
                               lambda w, t: calls.append(t),
                               "测试 pass", force=True, only_members=True)
    assert calls == ["idM2", "idM1"]


def test_member_auto_pass_skips_without_confirmed_members(monkeypatch):
    """未确认到在队伍中的队员 Tab：跳过整个 pass（不切 Tab、不点任何
    Tab），告警每任务一次。"""
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_team_member_ids", set())
    monkeypatch.setattr(tab_group, "_no_members_logged", False)
    entered = []
    monkeypatch.setattr(tab_group, "_try_enter_pass",
                        lambda: entered.append(1) or True)
    monkeypatch.setattr(tab_group, "_pass_foreground_ok", lambda win: True)
    tab_group.member_auto_pass(_FakeWin())
    assert entered == []  # 未进入 pass
    assert tab_group._no_members_logged is True
    # 再次调用不再重复告警
    tab_group.member_auto_pass(_FakeWin())
    assert entered == []


def test_startup_check_merges_name_reread(monkeypatch):
    """面板昵称部分区域 OCR 漏检：同面板重截一帧重读合并，真实队员
    不因单帧漏检被误判「不在队伍」。"""
    import src.device.team as team_mod
    import src.device.battle as battle_mod
    import time as time_mod
    monkeypatch.setattr(battle_mod, "is_in_battle", lambda win: False)
    monkeypatch.setattr(team_mod, "clear_pending_apply",
                        lambda win: (False, "无申请待处理态"))
    monkeypatch.setattr(time_mod, "sleep", lambda s: None)
    monkeypatch.setattr(team_mod, "classify_leader_with_panel",
                        lambda win, **kw: ("leader", "是队长（面板可见「解散队伍」）"))
    reads = []

    def read(img):
        if len(reads) == 0:
            reads.append(1)
            return [(0, "队长甲"), (1, None), (2, "玩家乙")]  # 第 2 区域漏检
        reads.append(2)
        return [(0, "队长甲"), (1, "碧桃娘子"), (2, "玩家乙")]
    monkeypatch.setattr(team_mod, "panel_member_names", read)
    monkeypatch.setattr(team_mod, "close_team_panel",
                        lambda win: (True, ""))
    ok, _info, names = tab_group.startup_check(_ScanWin())
    assert ok
    assert [n for _i, n in names] == ["队长甲", "碧桃娘子", "玩家乙"]
    assert reads == [1, 2]  # 重读了一次


def test_startup_member_check_no_match_distinct_message(monkeypatch):
    """面板昵称读到了但队员 Tab 均未匹配（启动时队伍仅队长 1 人）：
    文案必须区别于「面板昵称未识别」，且写组号识别状态 False
    （2026-11-07 日志 20:38 误报教训）。"""
    writes = {}
    monkeypatch.setattr(tab_group.app_utils, "role_state_set",
                        lambda key, value: writes.__setitem__(key, value) or True)
    data = {"panel_read": True,
            "leader": {"id": "idL", "name": "队长甲",
                       "char": None, "beast": None},
            "members": [
                {"id": "idM1", "name": "队员乙", "in_team": False,
                 "char": None, "beast": None}]}
    monkeypatch.setattr(tab_group, "scan_group_members",
                        lambda win, pn=None: (True, "ok", data))
    missing, info = tab_group.startup_member_check(_ScanWin())
    assert missing is None
    assert "队伍中无本窗口队员" in info
    assert "面板昵称未识别" not in info
    assert writes.get("group_detected") is False
    assert tab_group._team_member_ids == set()


# ---- 队长屏归属判定（2026-11-13 手动切队员 Tab 误判任务消失修复） ----

class _TitleWin:
    def __init__(self, title):
        self.title = title


def _group_active(monkeypatch, leader="286958560", mode="修罗", pass_on=False):
    """激活组号模式（active() 需 _mode 与当前任务模式一致）。"""
    monkeypatch.setattr(tab_group, "_active", True)
    monkeypatch.setattr(tab_group, "_mode", mode)
    monkeypatch.setattr(tab_group, "_leader_id", leader)
    monkeypatch.setattr(tab_group, "pass_active", pass_on)
    monkeypatch.setattr(app_utils, "_current_task_mode", mode)


def test_on_leader_screen_inactive_always_true(monkeypatch):
    """组号未激活（单号/非组号任务）：不做归属检查，行为不变。"""
    monkeypatch.setattr(tab_group, "_active", False)
    assert tab_group.on_leader_screen(
        _TitleWin("服务器 - 角色（ID：214369642）")) is True


def test_on_leader_screen_detects_manual_tab_switch(monkeypatch):
    """组号激活：标题是队长账号 → True；变成队员账号（pass/手动切 Tab）
    → False；标题无 ID（未登录/加载中）保守按非队长屏。"""
    _group_active(monkeypatch)
    assert tab_group.on_leader_screen(
        _TitleWin("服务器 - 队长（ID：286958560）")) is True
    assert tab_group.on_leader_screen(
        _TitleWin("服务器 - 队员（ID：214369642）")) is False
    assert tab_group.on_leader_screen(_TitleWin("大话西游2经典版")) is False


def test_on_leader_screen_pass_active_false(monkeypatch):
    """pass 进行中：屏幕是队员 Tab，一律 False（同一护栏）。"""
    _group_active(monkeypatch, pass_on=True)
    assert tab_group.on_leader_screen(
        _TitleWin("服务器 - 队长（ID：286958560）")) is False


def test_on_leader_screen_other_mode_no_guard(monkeypatch):
    """组号会话里切到非组号任务模式（五环等）：不套用队长屏判定——
    这些模式跟随当前画面执行、不管理 Tab，_leader_id 是上一个组号模式
    的旧值，套用会把正常画面误判成非队长屏。"""
    _group_active(monkeypatch, mode="修罗")
    monkeypatch.setattr(app_utils, "_current_task_mode", "五环")
    assert tab_group.on_leader_screen(
        _TitleWin("服务器 - 队员（ID：214369642）")) is True
