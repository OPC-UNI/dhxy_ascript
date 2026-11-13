"""workflows.claim_flow：批次 1 领取共享流程测试。"""
import numpy as np

from src.workflows import claim_flow


class _Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _Win:
    def capture(self, force_activate=False):
        return np.zeros((60, 60, 3), np.uint8)


def test_task_state_battle_returns_false(monkeypatch):
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: True)
    assert claim_flow.task_state(_Win(), "任务", ()) is False


def test_task_state_panel_closed_returns_false(monkeypatch):
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(claim_flow.task_tracker, "panel_visible",
                        lambda **k: False)
    assert claim_flow.task_state(_Win(), "任务", ()) is False


def test_task_state_panel_visible_uses_task_in(monkeypatch):
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(claim_flow.task_tracker, "panel_visible",
                        lambda **k: True)
    monkeypatch.setattr(claim_flow.task_tracker, "ocr_track",
                        lambda img: ([], None))
    monkeypatch.setattr(claim_flow.task_tracker, "has_task_in",
                        lambda items, img_np, title, fallback=(): True)
    assert claim_flow.task_state(_Win(), "任务", ("兜底",)) is True


def test_probe_task_block_uses_region_fn(monkeypatch):
    monkeypatch.setattr(claim_flow.game_map, "ocr_region",
                        lambda img, region: ([], None))
    monkeypatch.setattr(claim_flow.task_tracker, "has_task_in",
                        lambda *a: True)
    called = {}

    def _region(img_np):
        called["region"] = True
        return (0, 0, 10, 10)

    hit, _dt = claim_flow.probe_task_block(
        _Win(), "任务", (), _region)
    assert hit is True and called["region"] is True


def test_claim_verify_wait_probe_hit_skips_sleep(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "log", lambda *a: None)
    calls = []

    def _probe(win):
        calls.append(1)
        return True, 0.1

    claim_flow.claim_verify_wait(
        object(), None, 0.0, 1, prefix="[测试]", interval=0.8,
        probe_fn=_probe)
    assert calls == [1]
    assert clock.t == 0.0


def test_claim_verify_wait_miss_fills_interval(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "log", lambda *a: None)
    claim_flow.claim_verify_wait(
        object(), None, 0.0, 1, prefix="[测试]", interval=0.8,
        probe_fn=lambda win: (False, 0.1))
    assert clock.t >= 0.7


def test_claim_verify_wait_stale_task_skips_probe(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    claim_flow.claim_verify_wait(
        object(), "旧签名", 0.0, 1, prefix="[测试]", interval=0.8,
        probe_fn=lambda win: (_ for _ in ()).throw(AssertionError("不应探针")))
    assert clock.t == 0.8


def test_find_claim_option_full_and_fallback(monkeypatch):
    monkeypatch.setattr(claim_flow.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(claim_flow, "green_option_center",
                        lambda img, rect, nlen, ntxt: (520, 300))
    needles = (claim_flow.norm_text("完整文案"),
               claim_flow.norm_text("尾部"),
               claim_flow.norm_text("兜底"))
    full = [("完整文案", [500, 300, 700, 320], 0.9)]
    assert claim_flow.find_claim_option(full, None, needles) == (
        520, 300, "完整文案", False)
    short = [("兜底", [500, 300, 700, 320], 0.9)]
    assert claim_flow.find_claim_option(short, None, needles) == (
        520, 300, "兜底", True)


def test_find_claim_option_ignores_tracker_and_low_score(monkeypatch):
    monkeypatch.setattr(claim_flow.task_tracker, "is_green_text",
                        lambda img, rect: True)
    needles = (claim_flow.norm_text("完整文案"),)
    assert claim_flow.find_claim_option(
        [("完整文案", [100, 300, 300, 320], 0.9)], None, needles) is None
    assert claim_flow.find_claim_option(
        [("完整文案", [500, 300, 700, 320], 0.3)], None, needles) is None


def test_click_claim_option_caches_position(monkeypatch):
    calls = {}
    monkeypatch.setattr(claim_flow, "find_claim_option",
                        lambda *a: (10, 20, "文案", False))
    monkeypatch.setattr(claim_flow, "human_click",
                        lambda win, x, y: calls.setdefault("click", (x, y)))
    monkeypatch.setattr(claim_flow.app_utils, "fixed_pos_set",
                        lambda key, win, x, y: calls.setdefault(
                            "cache", (key, x, y)))
    monkeypatch.setattr(claim_flow, "move_away",
                        lambda win: calls.setdefault("move", True))
    monkeypatch.setattr(claim_flow, "log", lambda *a: None)
    assert claim_flow.click_claim_option(
        object(), [], None, needles=("x",), prefix="[测试]",
        cache_key="option_x") is True
    assert calls["click"] == (10, 20)
    assert calls["cache"] == ("option_x", 10, 20)
    assert calls["move"] is True


def test_in_claim_map_and_near_claim_spot():
    assert claim_flow.in_claim_map(("宝象国", 1, 2), "宝象国") is True
    assert claim_flow.in_claim_map(("长安", 1, 2), "宝象国") is False
    assert claim_flow.in_claim_map(None, "宝象国") is False
    assert claim_flow.near_claim_spot(
        ("宝象国", 86, 86), "宝象国", (86, 86), 15) is True
    assert claim_flow.near_claim_spot(
        ("宝象国", 120, 86), "宝象国", (86, 86), 15) is False
    assert claim_flow.near_claim_spot(
        ("宝象国", 86, 86), "宝象国", None, 15) is False


def test_follow_to_done_passes_config(monkeypatch):
    calls = []

    def _follow(win, title, **kwargs):
        calls.append((title, kwargs))
        return True, "done"

    monkeypatch.setattr(claim_flow, "follow_task", _follow)
    out = claim_flow.follow_to_done(
        object(), first_items=([], None), title="任务", prefix="五倍",
        extra_dialog_handler="handler", fallback_keywords=("兜底",),
        skip_texts={}, story_known_texts=("剧情",),
        pre_click_handler="pre", post_track_handler="post",
        task_gone_polls="polls")
    assert out == (True, "done")
    title, kwargs = calls[0]
    assert title == "任务"
    assert kwargs["prefix"] == "五倍"
    assert kwargs["skip_texts"] == {}
    assert kwargs["story_known_texts"] == ("剧情",)
    assert kwargs["pre_click_handler"] == "pre"
    assert kwargs["task_gone_polls"] == "polls"
    assert kwargs["first_items"] is not None


def test_follow_to_done_omits_unset_options(monkeypatch):
    calls = []
    monkeypatch.setattr(
        claim_flow, "follow_task",
        lambda win, title, **kw: calls.append(kw) or (True, "done"))
    claim_flow.follow_to_done(
        object(), title="任务", prefix="种族比武",
        extra_dialog_handler="handler", fallback_keywords=(),
        post_track_handler="post")
    for key in ("skip_texts", "story_known_texts", "pre_click_handler",
                "task_gone_polls"):
        assert key not in calls[0]


def test_follow_to_done_recovery_retries_without_first_items(monkeypatch):
    calls = []
    results = [(False, "任务内容中未找到绿色文案"), (True, "done")]

    def _follow(win, title, **kwargs):
        calls.append(kwargs)
        return results.pop(0)

    monkeypatch.setattr(claim_flow, "follow_task", _follow)
    monkeypatch.setattr(claim_flow, "log", lambda *a: None)
    recovered = []

    def _recovery(win):
        recovered.append(1)
        return True, "walked"

    out = claim_flow.follow_to_done(
        object(), first_items=([], None), title="任务", prefix="修罗",
        extra_dialog_handler="handler", fallback_keywords=(),
        recovery=_recovery, recovery_window=30)
    assert out == (True, "done")
    assert recovered == [1]
    assert "first_items" in calls[0]
    assert "first_items" not in calls[1]
