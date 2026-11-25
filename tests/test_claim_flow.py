"""workflows.claim_flow：领取共享流程测试（批次 1 + 批次 2）。"""
import numpy as np
import pytest

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
    monkeypatch.setattr(claim_flow.task_tracker, "track_content_region",
                        lambda img_np: (0, 0, 10, 10))
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


def test_probe_task_block_skips_ocr_when_panel_absent(monkeypatch):
    """追踪面板标题条模板缺席：任务不可能渲染，免任务块 OCR（~0.24s）。"""
    monkeypatch.setattr(claim_flow.task_tracker, "track_content_region",
                        lambda img_np: None)
    called = []
    monkeypatch.setattr(claim_flow.game_map, "ocr_region",
                        lambda img, region: called.append(1) or ([], None))
    monkeypatch.setattr(claim_flow.task_tracker, "has_task_in",
                        lambda *a: called.append("has_task_in") or True)
    hit, _dt = claim_flow.probe_task_block(
        _Win(), "任务", (), lambda img_np: (0, 0, 10, 10))
    assert hit is False and called == []


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
                        lambda img, rect, nlen, ntxt, target=None: (520, 300))
    needles = (claim_flow.norm_text("完整文案"),
               claim_flow.norm_text("尾部"),
               claim_flow.norm_text("兜底"))
    full = [("完整文案", [500, 300, 700, 320], 0.9)]
    # 单行框 + 非兜底词 → 可缓存
    assert claim_flow.find_claim_option(full, None, needles) == (
        520, 300, "完整文案", False, True)
    short = [("兜底", [500, 300, 700, 320], 0.9)]
    # 命中最后兜底词：坐标不可靠，不可写高性能缓存
    assert claim_flow.find_claim_option(short, None, needles) == (
        520, 300, "兜底", True, False)


def test_find_claim_option_merged_row_not_cacheable(monkeypatch):
    """OCR 把上下两个选项合并成高框：中心按比例估算，不可写缓存。

    2026-11-22 实机：修罗领取对话框「闲来无事，要我帮忙吗」与
    「我想取消任务」相邻行被合并后，补点把取消行的点写进领取缓存，
    此后每轮领取都点成取消。单行框实测 19~23px，合并框 ≥38px。
    """
    monkeypatch.setattr(claim_flow.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(claim_flow, "green_option_center",
                        lambda img, rect, nlen, ntxt, target=None: (520, 461))
    needles = (claim_flow.norm_text("闲来无事，要我帮忙吗"),
               claim_flow.norm_text("帮忙"))
    merged = [("闲来无事，要我帮忙吗", [436, 440, 590, 479], 0.9)]
    found = claim_flow.find_claim_option(merged, None, needles)
    assert found[:4] == (520, 461, "闲来无事，要我帮忙吗", False)
    assert found[4] is False  # 合并高框：只点不学


def test_find_claim_option_center_follows_needle_position(monkeypatch):
    """选项行被 OCR 与相邻文字合并时，点击中心按 needle 实际位置取。"""
    seen = {}

    def _center(img, rect, nlen, ntxt, target=None):
        seen["target"] = target
        return 600, 450

    monkeypatch.setattr(claim_flow.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(claim_flow, "green_option_center", _center)
    needles = (claim_flow.norm_text("要我帮忙吗"),)
    items = [("闲来无事，要我帮忙吗", [436, 440, 590, 461], 0.9)]
    assert claim_flow.find_claim_option(items, None, needles)[:2] == (600, 450)
    # 「要我帮忙吗」在整行中从第 4 字开始：中心 = 4 + 5/2 = 6.5
    assert seen["target"] == 6.5


def test_find_claim_option_ignores_tracker_and_low_score(monkeypatch):
    monkeypatch.setattr(claim_flow.task_tracker, "is_green_text",
                        lambda img, rect: True)
    needles = (claim_flow.norm_text("完整文案"),)
    assert claim_flow.find_claim_option(
        [("完整文案", [100, 300, 300, 320], 0.9)], None, needles) is None
    assert claim_flow.find_claim_option(
        [("完整文案", [500, 300, 700, 320], 0.3)], None, needles) is None


def _patch_click_env(monkeypatch, found):
    calls = {}
    monkeypatch.setattr(claim_flow, "find_claim_option",
                        lambda *a: found)
    monkeypatch.setattr(claim_flow, "human_click",
                        lambda win, x, y: calls.setdefault("click", (x, y)))
    monkeypatch.setattr(claim_flow.app_utils, "fixed_pos_set",
                        lambda key, win, x, y: calls.setdefault(
                            "cache", (key, x, y)))
    monkeypatch.setattr(claim_flow, "move_away",
                        lambda win: calls.setdefault("move", True))
    monkeypatch.setattr(claim_flow, "log", lambda *a: None)
    return calls


def test_click_claim_option_caches_position(monkeypatch):
    calls = _patch_click_env(monkeypatch, (10, 20, "文案", False, True))
    assert claim_flow.click_claim_option(
        object(), [], None, needles=("x",), prefix="[测试]",
        cache_key="option_x") is True
    assert calls["click"] == (10, 20)
    assert calls["cache"] == ("option_x", 10, 20)
    assert calls["move"] is True


def test_click_claim_option_learn_false_skips_cache(monkeypatch):
    """识别补点路径（learn=False）只点不学：过渡帧的中心不可靠。"""
    calls = _patch_click_env(monkeypatch, (10, 20, "文案", False, True))
    assert claim_flow.click_claim_option(
        object(), [], None, needles=("x",), prefix="[测试]",
        cache_key="option_x", learn=False) is True
    assert calls["click"] == (10, 20)
    assert "cache" not in calls


def test_click_claim_option_not_cacheable_skips_cache(monkeypatch):
    """合并框/兜底词命中的坐标不可靠：点击但不写缓存。"""
    calls = _patch_click_env(monkeypatch, (10, 20, "文案", False, False))
    assert claim_flow.click_claim_option(
        object(), [], None, needles=("x",), prefix="[测试]",
        cache_key="option_x") is True
    assert calls["click"] == (10, 20)
    assert "cache" not in calls


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


# ---- 小地图走位到达自动弹出的任务对话框（2026-11-16 用户提示）----

def test_wait_auto_claim_dialog_returns_frame_on_option(monkeypatch):
    """对话框门控命中 + 对话区命中领取绿色选项 → 返回该帧（供同帧点击）。"""
    calls = []
    monkeypatch.setattr(claim_flow, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(
        claim_flow.game_map, "ocr_region",
        lambda img, region: calls.append(region)
        or ([("闲来无事，要我帮忙吗", [450, 400, 700, 420], 0.95)], None))
    monkeypatch.setattr(claim_flow, "find_claim_option",
                        lambda items, img_np, needles: (1, 2, "txt", False))
    frame = claim_flow.wait_auto_claim_dialog(_Win(), ("x",), timeout=0.0)
    assert frame is not None and frame[0]
    assert calls == [claim_flow.task_tracker.DIALOG_REGION]


def test_wait_auto_claim_dialog_none_without_option(monkeypatch):
    """对话框门控通过但无领取选项 → 短窗口结束返回 None。"""
    monkeypatch.setattr(claim_flow, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(claim_flow.game_map, "ocr_region",
                        lambda img, region: ([], None))
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    assert claim_flow.wait_auto_claim_dialog(
        _Win(), ("x",), timeout=1.0, interval=0.5) is None
    assert clock.t >= 1.0


def test_wait_auto_claim_dialog_gate_skips_ocr(monkeypatch):
    """对话框模板门控未命中 → 不 OCR 对话区（空场景 OCR ~150-600ms）。"""
    ocr = []
    monkeypatch.setattr(claim_flow, "dialog_frame_score", lambda img: 0.1)
    monkeypatch.setattr(claim_flow.game_map, "ocr_region",
                        lambda img, region: ocr.append(1) or ([], None))
    assert claim_flow.wait_auto_claim_dialog(
        _Win(), ("x",), timeout=0.0) is None
    assert ocr == []


# ---- 地图感知导航 / 重走领取点（批次 2，2026-11-18）----

class _FrameWin:
    def __init__(self, frame=None):
        self.captures = 0
        self.frame = frame

    def capture(self, force_activate=False):
        self.captures += 1
        if self.frame is not None:
            return self.frame
        return np.zeros((60, 60, 3), np.uint8)


class _SeqWin:
    """按序列返回帧（None 表示截图失败），用尽后返回零帧。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.captures = 0

    def capture(self, force_activate=False):
        self.captures += 1
        if self.frames:
            return self.frames.pop(0)
        return np.zeros((60, 60, 3), np.uint8)


def _patch_open_env(monkeypatch, pos=("宝象国", 1, 2), battle=False,
                    mini=(True, "ok"), city=(True, "ok"),
                    filter_state=True):
    calls = {"mini": [], "city": [], "click": [], "filter": [],
             "read_pos": []}
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: battle)
    monkeypatch.setattr(
        claim_flow.game_map, "read_map_position",
        lambda win, img=None: calls["read_pos"].append(img) or pos)
    monkeypatch.setattr(claim_flow.game_map, "open_mini_map",
                        lambda win: calls["mini"].append(1) or mini)
    monkeypatch.setattr(
        claim_flow.game_map, "open_mini_map_by_name",
        lambda win, map_name: calls["city"].append(map_name) or city)
    monkeypatch.setattr(
        claim_flow.game_map, "minimap_task_filter_state",
        lambda win: calls["filter"].append(1) or filter_state)
    monkeypatch.setattr(claim_flow, "human_click",
                        lambda win, x, y: calls["click"].append((x, y)))
    monkeypatch.setattr(
        claim_flow.game_map, "uncheck_minimap_task_filter",
        lambda win: (True, "「任务」筛选未勾选"))
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    return calls


def test_open_claim_map_in_map_opens_minimap_and_clicks(monkeypatch):
    calls = _patch_open_env(monkeypatch)
    states = []
    win = _FrameWin()
    ok, info = claim_flow.open_claim_map(
        win, None, map_name="宝象国", map_pos=(517, 488),
        prefix="[测试]", on_filter_state=states.append)
    assert ok is True and "宝象国" in info
    assert calls["mini"] == [1] and calls["city"] == []
    assert states == [True] and calls["filter"] == [1]
    assert calls["click"] == [(517, 488)]
    assert win.captures == 1  # 决策帧只截一次
    assert len(calls["read_pos"]) == 1  # pos=None 时自行读一次位置


def test_open_claim_map_unchecks_task_filter(monkeypatch):
    """开图后取消小地图「任务」筛选（仅名望需要勾选；用户 2026-11-21）。"""
    _patch_open_env(monkeypatch)
    checked = []
    monkeypatch.setattr(
        claim_flow.game_map, "uncheck_minimap_task_filter",
        lambda win: checked.append(1) or (True, "未勾选"))
    ok, _info = claim_flow.open_claim_map(
        _FrameWin(), ("宝象国", 1, 2), map_name="宝象国",
        map_pos=(517, 488), prefix="[测试]")
    assert ok is True and checked == [1]


def test_open_claim_map_other_map_goes_through_world_map(monkeypatch):
    calls = _patch_open_env(monkeypatch, pos=("灵兽村", 111, 90))
    ok, _info = claim_flow.open_claim_map(
        _FrameWin(), None, map_name="宝象国", map_pos=(517, 488),
        prefix="[测试]")
    assert ok is True
    assert calls["mini"] == []
    assert calls["city"] == ["宝象国"]
    assert calls["click"] == [(517, 488)]


def test_open_claim_map_battle_returns_false(monkeypatch):
    calls = _patch_open_env(monkeypatch, battle=True)
    win = _FrameWin()
    ok, info = claim_flow.open_claim_map(
        win, None, map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]")
    assert ok is False and "战斗中" in info
    assert calls["mini"] == [] and calls["city"] == []
    assert calls["filter"] == [] and calls["click"] == []
    assert win.captures == 1


def test_open_claim_map_capture_none_returns_false(monkeypatch):
    calls = _patch_open_env(monkeypatch)
    ok, info = claim_flow.open_claim_map(
        _SeqWin([None]), None, map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]")
    assert ok is False and info == "截图失败"
    assert calls["mini"] == [] and calls["city"] == []
    assert calls["filter"] == [] and calls["click"] == []


def test_open_claim_map_minimap_open_failure(monkeypatch):
    calls = _patch_open_env(monkeypatch, mini=(False, "点击后小地图未打开"))
    ok, info = claim_flow.open_claim_map(
        _FrameWin(), ("宝象国", 1, 2), map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]")
    assert ok is False and info == "点击后小地图未打开"
    assert calls["filter"] == [] and calls["click"] == []


def test_open_claim_map_city_open_failure(monkeypatch):
    calls = _patch_open_env(monkeypatch, pos=("灵兽村", 1, 2),
                            city=(False, "世界地图中未找到宝象国"))
    ok, info = claim_flow.open_claim_map(
        _FrameWin(), None, map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]")
    assert ok is False and "未找到宝象国" in info
    assert calls["filter"] == [] and calls["click"] == []


@pytest.mark.parametrize("state", [True, False, None])
def test_open_claim_map_filter_state_callback(monkeypatch, state):
    calls = _patch_open_env(monkeypatch, filter_state=state)
    states = []
    ok, _info = claim_flow.open_claim_map(
        _FrameWin(), ("宝象国", 1, 2), map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]", on_filter_state=states.append)
    assert ok is True
    assert states == [state] and calls["filter"] == [1]


def test_open_claim_map_custom_click_fn_and_no_filter_read(monkeypatch):
    """种族比武路径：自定义点击（坐标夹取），不读取任务筛选状态。"""
    calls = _patch_open_env(monkeypatch)
    clicked = []
    win = _FrameWin()
    ok, _info = claim_flow.open_claim_map(
        win, ("长安", 1, 2), map_name="长安", map_pos=(737, 387),
        prefix="[测试]", click_fn=lambda w: clicked.append(w))
    assert ok is True and clicked
    assert calls["click"] == []  # 未走默认 human_click
    assert calls["filter"] == []  # 未读筛选状态（省一次截图+标记匹配）
    assert calls["read_pos"] == []  # pos 已给：省一次位置 OCR
    assert win.captures == 1


def test_open_claim_map_coord_nav_uses_start_navigation(
        monkeypatch, coord_nav_enabled):
    """坐标化开关开启且给出 map_coord：走 map_nav 坐标点击（不开旧地图）。

    trusted（2026-11-25）：固定领取点有标定直点，免悬停校验。
    """
    calls = _patch_open_env(monkeypatch)
    from src.device import map_nav
    started = {}

    def _start(win, map_name, x, y, log_fn=None, on_map_open=None,
               trusted=False):
        started["args"] = (map_name, x, y)
        started["trusted"] = trusted
        if on_map_open is not None:
            on_map_open()  # 模拟「地图已打开、点击前」的筛选读取时机
        return True, "已点击目标点 (627,396)，开始寻路", (627, 396)

    monkeypatch.setattr(map_nav, "start_navigation", _start)
    states = []
    win = _FrameWin()
    ok, info = claim_flow.open_claim_map(
        win, None, map_name="灵兽村", map_pos=(627, 396),
        prefix="[测试]", on_filter_state=states.append,
        map_coord=(120, 88))
    assert ok is True and "坐标" in info
    assert started["args"] == ("灵兽村", 120, 88)
    assert started["trusted"] is True
    assert states == [True] and calls["filter"] == [1]
    assert calls["mini"] == [] and calls["city"] == []  # 不走旧开图
    assert calls["click"] == []  # 不走固定像素点击


def test_open_claim_map_coord_nav_failure_falls_back_to_fixed_pixel(
        monkeypatch, coord_nav_enabled):
    """坐标直点失败（无标定/越界）→ 回退固定像素点击，不再整轮卡死。

    2026-11-25 手动寻路实机：悬停不可用时坐标寻路 32 次×36s 空转；
    固定像素是该地图窗口的生产标定值，差一点不影响领取点到达判据。
    """
    calls = _patch_open_env(monkeypatch)
    from src.device import map_nav
    monkeypatch.setattr(
        map_nav, "start_navigation",
        lambda *a, **k: (False, "灵兽村 无地图标定（trusted 直点不可用）", None))
    win = _FrameWin()
    ok, info = claim_flow.open_claim_map(
        win, None, map_name="宝象国", map_pos=(517, 488),
        prefix="[测试]", map_coord=(120, 88))
    assert ok is True and "宝象国" in info
    # 回退路径重新截帧 + 重读位置（旧帧/旧位置已在开合图后过期）
    assert win.captures == 2
    assert len(calls["read_pos"]) == 1
    assert calls["mini"] == [1] and calls["click"] == [(517, 488)]


def test_open_claim_map_pos_reused_skips_position_read(monkeypatch):
    calls = _patch_open_env(monkeypatch)
    ok, _info = claim_flow.open_claim_map(
        _FrameWin(), ("宝象国", 86, 86), map_name="宝象国",
        map_pos=(1, 2), prefix="[测试]")
    assert ok is True
    assert calls["read_pos"] == []  # 同帧复用：不再读位置


def test_relocate_returns_on_arrival(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    read_imgs = []
    monkeypatch.setattr(
        claim_flow.game_map, "read_map_position",
        lambda win, img=None: read_imgs.append(img) or ("宝象国", 94, 84))
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    seen = []

    def _arrival(win, img, frame, pos):
        seen.append((img, frame, pos))
        return "已重走到领取点附近"

    # RGB (10,20,30) 帧：转 BGR 后应为 (30,20,10)
    frame_rgb = np.full((60, 60, 3), (10, 20, 30), np.uint8)
    win = _FrameWin(frame_rgb)
    ok, info = claim_flow.relocate_to_claim_spot(
        win, prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: (True, "已点击"),
        close_map_fn=lambda w: False, arrival_fn=_arrival, timeout=30)
    assert ok is True and info == "已重走到领取点附近"
    assert seen and seen[0][2] == ("宝象国", 94, 84)
    # 同帧复用：位置 OCR 与到达判据拿到的是同一帧
    assert read_imgs[0] is seen[0][0]
    # PIL(RGB) 帧给对话框判定；BGR 帧给模板匹配（绿字判定依赖 RGB）
    assert seen[0][0][0, 0].tolist() == [10, 20, 30]
    assert seen[0][1][0, 0].tolist() == [30, 20, 10]
    assert win.captures == 1  # 每轮询一帧


def test_relocate_skips_capture_failure_and_battle_frames(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: False)
    battle_frame = np.full((60, 60, 3), 255, np.uint8)
    normal = np.zeros((60, 60, 3), np.uint8)
    monkeypatch.setattr(claim_flow, "is_in_battle_frame",
                        lambda img: img is not None and int(img[0, 0, 0]) == 255)
    read_imgs = []
    monkeypatch.setattr(
        claim_flow.game_map, "read_map_position",
        lambda win, img=None: read_imgs.append(img) or None)
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    seen = []

    def _arrival(win, img, frame, pos):
        seen.append(img)
        return "已重走到领取点附近"

    win = _SeqWin([None, battle_frame, normal])
    ok, info = claim_flow.relocate_to_claim_spot(
        win, prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: (True, "已点击"),
        close_map_fn=lambda w: False, arrival_fn=_arrival, timeout=30)
    assert ok is True and info == "已重走到领取点附近"
    assert win.captures == 3  # 失败帧/战斗帧各跳过一次
    assert seen == [normal]  # 跳过帧不进入到达判据
    assert read_imgs == [normal]  # 跳过帧不读位置


def test_relocate_kept_map_short_circuits_poll(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    opened, closed = [], []
    win = _FrameWin()
    ok, info = claim_flow.relocate_to_claim_spot(
        win, prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: opened.append(1) or (True, "已点击"),
        close_map_fn=lambda w: closed.append(1) or True,
        arrival_fn=lambda *a: None, timeout=30)
    assert ok is True and "自动弹出" in info
    assert opened == [1] and closed == [1]
    assert win.captures == 0


def test_relocate_battle_and_open_fail(monkeypatch):
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: True)
    ok, info = claim_flow.relocate_to_claim_spot(
        _FrameWin(), prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: (True, "x"), close_map_fn=lambda w: False,
        arrival_fn=lambda *a: None, timeout=1)
    assert ok is False and "战斗中" in info
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: False)
    ok, info = claim_flow.relocate_to_claim_spot(
        _FrameWin(), prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: (False, "截图失败"), close_map_fn=lambda w: False,
        arrival_fn=lambda *a: None, timeout=1)
    assert ok is False and info == "截图失败"


def test_relocate_times_out(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(claim_flow, "time", clock)
    monkeypatch.setattr(claim_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    read_imgs = []
    monkeypatch.setattr(
        claim_flow.game_map, "read_map_position",
        lambda win, img=None: read_imgs.append(img) or None)
    monkeypatch.setattr(claim_flow, "log", lambda *a, **k: None)
    opened, closed = [], []
    win = _FrameWin()
    ok, info = claim_flow.relocate_to_claim_spot(
        win, prefix="[测试]", map_name="宝象国",
        open_map_fn=lambda w: opened.append(1) or (True, "已点击"),
        close_map_fn=lambda w: closed.append(1) or False,
        arrival_fn=lambda *a: None, timeout=5)
    assert ok is False and info == "重走领取点超时"
    assert opened == [1] and closed == [1]
    assert win.captures == 3  # 1.0s 启动等待 + 2s 轮询，5s 内 3 帧
    assert len(read_imgs) == 3 and all(img is not None for img in read_imgs)
