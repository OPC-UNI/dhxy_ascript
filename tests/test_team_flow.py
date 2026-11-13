"""workflows.team_flow：组队流程编排的共享实现测试。"""
from src.device import silent_click
from src.workflows import team_flow


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
    assert calls["overlay"][0] == "ready"


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
