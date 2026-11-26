"""workflows.team_flow：组队流程编排的共享实现测试。"""
import pytest

from src.device import silent_click
from src.workflows import team_flow


@pytest.fixture(autouse=True)
def _not_in_battle(monkeypatch):
    """默认非战斗（战斗门单独用例覆盖）：_in_battle 不在单测里截窗。"""
    monkeypatch.setattr(team_flow, "_in_battle", lambda win: False)


def _flow(**overrides):
    kwargs = dict(prefix="[测试]", min_members=5,
                  auto_rejoin=lambda: False, shout_text=lambda: "")
    kwargs.update(overrides)
    return team_flow.TeamFlow(**kwargs)


def test_check_team_ready_passes_min_members_and_prefix(monkeypatch):
    calls = {}

    def _check(win, **kwargs):
        calls.update(kwargs)
        return "ready", "队伍已满", ["a", "b"]

    monkeypatch.setattr(team_flow.team, "check_team_min", _check)
    monkeypatch.setattr(team_flow.team, "update_overlay_team",
                        lambda *a: calls.setdefault("overlay", a))
    assert _flow().check_team_ready(object()) == ("ready", "队伍已满")
    assert calls["min_members"] == 5
    assert calls["prefix"] == "[测试][创建队伍]"
    assert calls["allow_kick"] is True
    assert calls["kick_overdue"] is True  # 未注入：保持原行为
    assert calls["kick_timeout"] is None  # 未注入：沿用 team 默认 5 分钟
    assert calls["overlay"][0] == "ready"


def test_check_team_with_retry_known_leader_unclear_keeps_leader(monkeypatch):
    """已确认队长 + 身份未决（菜单打不开）：不返回非队长，保持队长重试。

    用户 2026-11-14：瞬态两三次失败不能把队长降级；只有明确证据
    （菜单无队伍信息 / 面板无解散队伍）才判非队长。
    """
    calls = []
    monkeypatch.setattr(
        team_flow.TeamFlow, "check_team_ready",
        lambda self, win, create_if_missing=False, **kw:
        calls.append(1) or ("not_leader", "队长无法判定（组队菜单未打开）"))
    monkeypatch.setattr(team_flow.team, "known_leader", lambda: True)
    monkeypatch.setattr(team_flow, "log", lambda *a, **k: None)
    monkeypatch.setattr(team_flow, "time", type(
        "T", (), {"sleep": staticmethod(lambda s: None),
                  "time": staticmethod(lambda: 0.0)})())
    status, info = _flow().check_team_with_retry(object())
    assert status == "wait"
    assert "已确认队长" in info
    assert len(calls) == 3  # 仍先做满瞬态重试


def test_check_team_with_retry_unknown_identity_keeps_transient(monkeypatch):
    """未确认过队长：身份未决保持瞬态 not_leader（调用方继续重试）。"""
    monkeypatch.setattr(
        team_flow.TeamFlow, "check_team_ready",
        lambda self, win, create_if_missing=False, **kw:
        ("not_leader", "队长无法判定（组队菜单未打开）"))
    monkeypatch.setattr(team_flow.team, "known_leader", lambda: None)
    monkeypatch.setattr(team_flow, "log", lambda *a, **k: None)
    monkeypatch.setattr(team_flow, "time", type(
        "T", (), {"sleep": staticmethod(lambda s: None),
                  "time": staticmethod(lambda: 0.0)})())
    status, info = _flow().check_team_with_retry(object())
    assert status == "not_leader"
    assert "队长无法判定" in info


def test_check_team_with_retry_definitive_member_immediate(monkeypatch):
    """明确非队长（面板打开且无解散队伍）：即使已确认过队长也立即返回。"""
    calls = []
    monkeypatch.setattr(
        team_flow.TeamFlow, "check_team_ready",
        lambda self, win, create_if_missing=False, **kw:
        calls.append(1) or ("not_leader", "非队长（面板无「解散队伍」）"))
    monkeypatch.setattr(team_flow.team, "known_leader", lambda: True)
    monkeypatch.setattr(team_flow, "log", lambda *a, **k: None)
    status, info = _flow().check_team_with_retry(object())
    assert status == "not_leader"
    assert "非队长" in info
    assert len(calls) == 1  # 确定性状态不重试


def test_check_team_ready_defers_in_battle(monkeypatch):
    """战斗中不开组队菜单、不判身份（2026-11-26 队长误转保持模式教训）。"""
    checks = []
    monkeypatch.setattr(team_flow, "_in_battle", lambda win: True)
    monkeypatch.setattr(team_flow.team, "check_team_min",
                        lambda *a, **k: checks.append(1))
    status, info = _flow().check_team_ready(object())
    assert status == "not_leader"
    assert info == team_flow.BATTLE_DEFER_INFO
    # 顺延描述不得被当成确定性非队长（否则调用方转保持模式）
    assert not team_flow.team.is_definitive_not_leader(info)
    assert checks == []


def test_check_team_ready_invalidates_result_if_battle_starts(monkeypatch):
    """检查期间开打：确定性「未组队」结论作废，不写悬浮层。"""
    seq = iter([False, True])  # 检查前非战，检查后已开战
    monkeypatch.setattr(team_flow, "_in_battle", lambda win: next(seq))
    overlay = []
    monkeypatch.setattr(
        team_flow.team, "check_team_min",
        lambda *a, **k: ("not_leader", "未组队（菜单中无「队伍信息」）", []))
    monkeypatch.setattr(team_flow.team, "update_overlay_team",
                        lambda *a: overlay.append(a))
    status, info = _flow().check_team_ready(object())
    assert status == "not_leader"
    assert info == team_flow.BATTLE_DEFER_INFO
    assert overlay == []


def test_check_team_with_retry_returns_immediately_in_battle(monkeypatch):
    """战斗中直接顺延：不消耗重试、不调用检查。"""
    checks = []
    monkeypatch.setattr(team_flow, "_in_battle", lambda win: True)
    monkeypatch.setattr(
        team_flow.TeamFlow, "check_team_ready",
        lambda self, win, create_if_missing=False, **kw: checks.append(1))
    status, info = _flow().check_team_with_retry(object())
    assert status == "not_leader"
    assert info == team_flow.BATTLE_DEFER_INFO
    assert checks == []


def test_monitor_team_and_fill_waits_out_battle(monkeypatch):
    """监控循环战斗中不检查/不补人（只记一条），战斗结束立即恢复。"""
    seq = iter([True, True, False])
    monkeypatch.setattr(team_flow, "_in_battle", lambda win: False)
    monkeypatch.setattr(team_flow.battle, "watch_in_battle",
                        lambda: next(seq, False))
    monkeypatch.setattr(team_flow, "is_in_battle_frame", lambda img: False)
    sleeps = []
    monkeypatch.setattr(team_flow.time, "sleep", lambda s: sleeps.append(s))
    logs = []
    monkeypatch.setattr(team_flow, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    checks = []
    monkeypatch.setattr(
        team_flow.TeamFlow, "check_team_with_retry",
        lambda self, win, create_if_missing=False:
        checks.append(1) or ("ready", "队伍满足人数要求"))
    monkeypatch.setattr(team_flow.team, "has_pending_apply", lambda img: False)

    class _Win:
        def capture(self, force_activate=False):
            return object()

    _flow().monitor_team_and_fill(_Win())
    assert checks == [1]  # 战斗解除后才做队伍检查
    assert sleeps == [3.0, 3.0]  # 战斗中按识别重试节奏轮询
    battle_logs = [m for m, lv in logs if "顺延到战斗结束后" in m]
    assert len(battle_logs) == 1  # 只记一条，不刷屏


def test_check_team_ready_kick_switch_and_timeout(monkeypatch):
    """任务注入的请离开关/超时透传给 check_team_min（修罗设置）。

    请离开关只关闭「请离」，暂离处理块本身照常执行（组号队员归队
    pass 不能被一起关掉）。
    """
    calls = {}
    monkeypatch.setattr(team_flow.team, "check_team_min",
                        lambda win, **kw: calls.update(kw) or ("ready", "", []))
    monkeypatch.setattr(team_flow.team, "update_overlay_team", lambda *a: None)
    _flow(kick_overdue=lambda: False, kick_timeout=lambda: 120) \
        .check_team_ready(object())
    assert calls["allow_kick"] is True
    assert calls["kick_overdue"] is False
    assert calls["kick_timeout"] == 120


def test_check_team_ready_away_handling_passthrough(monkeypatch):
    """away_handling=False 透传 check_team_min（战斗失败恢复轮回司身份
    检查只读身份/人数，不替暂离队员归队——2026-11-26 用户实机）。"""
    calls = {}
    monkeypatch.setattr(team_flow.team, "check_team_min",
                        lambda win, **kw: calls.update(kw) or ("wait", "", []))
    monkeypatch.setattr(team_flow.team, "update_overlay_team", lambda *a: None)
    _flow().check_team_ready(object(), away_handling=False)
    assert calls["away_handling"] is False
    calls.clear()
    _flow().check_team_ready(object())
    assert calls["away_handling"] is True  # 默认行为不变


def test_check_team_ready_callable_min_members(monkeypatch):
    calls = {}
    monkeypatch.setattr(team_flow.team, "check_team_min",
                        lambda win, **kw: calls.update(kw) or ("wait", "", []))
    monkeypatch.setattr(team_flow.team, "update_overlay_team", lambda *a: None)
    _flow(min_members=lambda: 3).check_team_ready(object())
    assert calls["min_members"] == 3


def test_auto_create_team_once_uses_create_prefix(monkeypatch):
    calls = {}

    def _ensure(win, prefix):
        calls["prefix"] = prefix
        return True, "已创建"

    monkeypatch.setattr(team_flow.team, "ensure_team_created", _ensure)
    logs = []
    monkeypatch.setattr(team_flow, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    _flow().auto_create_team_once(object())
    assert calls["prefix"] == "[测试][创建队伍]"
    assert logs == [("[测试][创建队伍] 已创建", "SYS")]


def test_maybe_tab_bars_pass_swallows_exception(monkeypatch):
    monkeypatch.setattr(team_flow.tab_group, "active", lambda: True)

    def _raise(win):
        raise RuntimeError("pass 失败")

    monkeypatch.setattr(team_flow.tab_group, "member_bars_pass", _raise)
    logs = []
    monkeypatch.setattr(team_flow, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    _flow().maybe_tab_bars_pass(object())  # 不得抛出
    assert logs and logs[0][1] == "WARN"
    assert "组号队员血蓝 pass 异常" in logs[0][0]


def test_shout_recruit_silent_updates_rate_limit(monkeypatch):
    calls = {}
    monkeypatch.setattr(team_flow.app_utils, "silent_mode_on", lambda: True)
    monkeypatch.setattr(
        silent_click, "send_silent_text",
        lambda win, text, end_enter=False: calls.update(
            text=text, end_enter=end_enter) or 1)
    monkeypatch.setattr(team_flow.time, "time", lambda: 123.0)
    monkeypatch.setattr(team_flow, "log", lambda *a: None)
    flow = _flow(shout_text=lambda: "测试来人")
    flow.shout_recruit(object())
    assert calls == {"text": "测试来人", "end_enter": True}
    assert flow._shout_last_at == 123.0


def test_shout_recruit_silent_failure_keeps_retry(monkeypatch):
    monkeypatch.setattr(team_flow.app_utils, "silent_mode_on", lambda: True)
    monkeypatch.setattr(silent_click, "send_silent_text",
                        lambda *a, **k: None)
    monkeypatch.setattr(team_flow, "log", lambda *a: None)
    flow = _flow(shout_text=lambda: "测试来人")
    flow.shout_recruit(object())
    assert flow._shout_last_at == 0.0  # 投递失败不记时间戳，下轮重试
