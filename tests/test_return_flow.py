"""workflows.return_flow：返程流程编排的共享实现测试。"""
import numpy as np

from src.workflows import return_flow


class _Clock:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _Win:
    def capture(self, force_activate=False):
        return np.zeros((10, 10, 3), np.uint8)


def _patch_wait_env(monkeypatch, battle_frames=None, pos=None):
    """等待战斗结束测试环境：战斗帧序列（可调用或布尔）+ 坐标桩。"""
    clock = _Clock()
    monkeypatch.setattr(return_flow, "time", clock)
    if callable(battle_frames):
        monkeypatch.setattr(return_flow, "is_in_battle_frame", battle_frames)
    else:
        value = bool(battle_frames)
        monkeypatch.setattr(return_flow, "is_in_battle_frame",
                            lambda bgr: value)
    # 战斗线程共享状态默认未确认战斗中（各用例可覆盖）
    monkeypatch.setattr(return_flow, "watch_in_battle", lambda: False)
    reads = []
    monkeypatch.setattr(
        return_flow.game_map, "read_map_position",
        lambda win, img=None: reads.append(img) or pos)
    return clock, reads


def test_wait_battle_end_immediate(monkeypatch):
    _patch_wait_env(monkeypatch, battle_frames=False)
    assert return_flow.wait_battle_end(_Win()) == (True, "战斗已结束")


def test_wait_battle_end_timeout(monkeypatch):
    _patch_wait_env(monkeypatch, battle_frames=True)
    ok, info = return_flow.wait_battle_end(_Win(), timeout=3)
    assert ok is False
    assert info == "等待战斗结束超时（3s）"


def test_wait_battle_end_requires_consecutive_confirmation(monkeypatch):
    """连续 4 次非战斗才确认结束：回合演出的单帧间隙不得判结束。

    2026-11-22 12:28 实机：开打 10s 后单帧判「战斗已结束」，返程开背包
    在战斗中连续失败、任务线程空转。序列 3 非战斗 + 1 战斗 + 4 非战斗
    须在前 3 帧不返回，回到战斗后重新计数。
    """
    seq = iter([False, False, False, True, False, False, False, False])
    clock, _reads = _patch_wait_env(
        monkeypatch, battle_frames=lambda bgr: next(seq, True))
    ok, info = return_flow.wait_battle_end(_Win(), timeout=60)
    assert ok is True and info == "战斗已结束"
    # 3 帧非战斗时不能提前返回：耗时恰为 7 次轮询间隔（第 8 帧确认）
    assert clock.t >= 7 * return_flow.BATTLE_END_POLL


def test_wait_battle_end_single_gap_times_out(monkeypatch):
    """战斗帧间歇性返回非战斗帧（每 4 帧一次战斗）：始终不确认结束。"""
    calls = {"n": 0}

    def _battle(bgr):
        calls["n"] += 1
        return calls["n"] % 4 != 0  # 每 4 帧一个战斗帧，打断连续确认

    _patch_wait_env(monkeypatch, battle_frames=_battle)
    ok, _info = return_flow.wait_battle_end(_Win(), timeout=5)
    assert ok is False


def test_wait_battle_end_coordinate_signal(monkeypatch):
    """进战斗满 10s 后坐标可读连续 2 轮询即确认结束（≈1s）。

    坐标信号是战斗线程同款快速信号（场景已恢复=战斗实质结束），回合
    演出间隙坐标仍不可读，天然免疫模板沟壑误判；应早于模板 2s 窗口
    返回（实测战斗线程坐标信号从未误判，用户 2026-11-22 确认）。
    """
    calls = {"n": 0}

    def _battle(bgr):
        calls["n"] += 1
        return calls["n"] <= 21  # 前 21 轮询（约 10s）仍是战斗帧

    clock, reads = _patch_wait_env(
        monkeypatch, battle_frames=_battle, pos=("灵兽村", 1, 2))
    ok, _info = return_flow.wait_battle_end(_Win(), timeout=60)
    assert ok is True
    # 10s 门未开之前不读坐标（防开场过渡帧误判）
    assert reads and clock.t >= return_flow.BATTLE_END_POS_AFTER
    # 坐标路径 2 轮询确认：远早于模板路径的 4 轮询（2s）
    assert clock.t <= return_flow.BATTLE_END_POS_AFTER + 1.0


def test_wait_battle_end_coordinate_gated_before_10s(monkeypatch):
    """进场 10s 内即使坐标可读也不算结束（开场过渡帧保护）。"""
    calls = {"n": 0}

    def _battle(bgr):
        calls["n"] += 1
        return calls["n"] <= 20  # 10s 内全战斗帧

    clock, reads = _patch_wait_env(
        monkeypatch, battle_frames=_battle, pos=("灵兽村", 1, 2))
    ok, _info = return_flow.wait_battle_end(_Win(), timeout=60)
    assert ok is True
    assert reads  # 门开后读了坐标
    assert clock.t >= 10.0  # 10s 门之后才结束


def test_wait_battle_end_reuses_watch_state(monkeypatch):
    """战斗线程已确认战斗中：不重复截图/模板判定/坐标 OCR（2026-11-25）。

    线程确认期只等待；线程确认结束后仍走本函数自己的 4 轮询 2s 模板
    窗口复核（结束时序与原先一致）。
    """
    clock, reads = _patch_wait_env(monkeypatch, battle_frames=False)
    polls = {"n": 0}
    captured = {"n": 0}
    checked = {"n": 0}

    def _watch():
        polls["n"] += 1
        return polls["n"] <= 5      # 前 5 轮询（2.5s）确认战斗中

    def _frame(bgr):
        checked["n"] += 1
        return False

    monkeypatch.setattr(return_flow, "watch_in_battle", _watch)
    monkeypatch.setattr(return_flow, "is_in_battle_frame", _frame)
    win = _Win()

    def _capture(force_activate=False):
        captured["n"] += 1
        return np.zeros((10, 10, 3), np.uint8)

    win.capture = _capture
    ok, info = return_flow.wait_battle_end(win, timeout=60)
    assert ok is True and info == "战斗已结束"
    # 线程确认期零截图/零判定；确认结束后才走本函数的 4 轮询复核
    assert captured["n"] == 4
    assert checked["n"] == 4
    assert reads == []          # 全程零坐标 OCR（2.5s < 10s 坐标门）
    assert clock.t >= 5 * return_flow.BATTLE_END_POLL


def test_wait_battle_end_watch_confirmed_timeout(monkeypatch):
    """战斗线程一直确认战斗中：保持原超时语义，且不做帧级空转。"""
    _patch_wait_env(monkeypatch, battle_frames=True)
    checked = {"n": 0}

    def _frame(bgr):
        checked["n"] += 1
        return True

    monkeypatch.setattr(return_flow, "watch_in_battle", lambda: True)
    monkeypatch.setattr(return_flow, "is_in_battle_frame", _frame)
    ok, info = return_flow.wait_battle_end(_Win(), timeout=3)
    assert ok is False and info == "等待战斗结束超时（3s）"
    assert checked["n"] == 0


def test_wait_battle_end_ignores_stale_watch_after_session_abort(monkeypatch):
    """会话中止后战斗线程已退出、共享状态残留：回退帧级判定防死等。"""
    _patch_wait_env(monkeypatch, battle_frames=False)
    monkeypatch.setattr(return_flow, "watch_in_battle", lambda: True)
    monkeypatch.setattr(return_flow.app_utils, "session_abort_reason",
                        lambda: "顶号")
    assert return_flow.wait_battle_end(_Win(), timeout=60) == \
        (True, "战斗已结束")


def test_find_return_item_template_missing(monkeypatch):
    monkeypatch.setattr(return_flow, "_template_present",
                        lambda icon, te=None: False)
    assert return_flow.find_return_item(
        object(), "x.png", "道具") == (None, "道具模板未提供（待补充 x.png）")


def test_find_return_item_found(monkeypatch):
    monkeypatch.setattr(return_flow, "_template_present",
                        lambda icon, te=None: True)
    monkeypatch.setattr(return_flow.game_map, "match_frame_np",
                        lambda frame, icon: (5, 6))
    assert return_flow.find_return_item(
        _Win(), "x.png", "道具") == ((5, 6), "已在任务储物格中找到道具")


def test_use_return_item_bag_closed(monkeypatch):
    monkeypatch.setattr(return_flow, "find_return_item",
                        lambda *a, **k: ((1, 2), "found"))
    monkeypatch.setattr(return_flow.bag, "is_bag_open", lambda win: False)
    ok, info = return_flow.use_return_item(
        object(), "x.png", "道具", "长安", "[测试]", bag_closed_label="短名")
    assert ok is False
    assert info == "右键短名前背包已被关闭，未使用（下轮重试）"


def test_use_return_item_success(monkeypatch):
    clicks = []
    monkeypatch.setattr(return_flow, "find_return_item",
                        lambda *a, **k: ((1, 2), "found"))
    monkeypatch.setattr(return_flow.bag, "is_bag_open", lambda win: True)
    monkeypatch.setattr(return_flow, "human_click",
                        lambda win, x, y, button="left": clicks.append(
                            (x, y, button)))
    monkeypatch.setattr(return_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(return_flow, "log", lambda *a: None)
    ok, info = return_flow.use_return_item(
        object(), "x.png", "道具", "长安", "[测试]")
    assert ok is True and info == "已右键使用道具"
    assert clicks == [(1, 2, "secondary")]


def test_return_flow_order(monkeypatch):
    calls = []
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(return_flow, "use_return_item",
                        lambda *a, **k: calls.append("use") or (True, "used"))
    monkeypatch.setattr(return_flow, "close_return_panels",
                        lambda *a, **k: calls.append("close"))
    flow = return_flow.ReturnFlow(
        prefix="[测试]", item_icon="x.png", item_label="道具",
        target_label="长安", success_info="已返回长安",
        open_box=lambda win: calls.append("open") or (True, "ok"),
        read_position=False, post_sleep=0)
    assert flow.run(object()) == (True, "已返回长安")
    assert calls == ["open", "use", "close"]


def test_return_flow_open_box_retry(monkeypatch):
    calls = []
    monkeypatch.setattr(return_flow, "is_in_battle", lambda win: False)
    monkeypatch.setattr(return_flow, "use_return_item",
                        lambda *a, **k: (True, "used"))
    monkeypatch.setattr(return_flow, "close_return_panels", lambda *a, **k: None)
    monkeypatch.setattr(return_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(return_flow, "log", lambda *a: None)

    def _open(win):
        calls.append("open")
        return len(calls) >= 2, "fail" if len(calls) < 2 else "ok"

    flow = return_flow.ReturnFlow(
        prefix="[测试]", item_icon="x.png", item_label="道具",
        target_label="长安", success_info="已返回长安",
        open_box=_open, open_box_retries=2, read_position=False, post_sleep=0)
    assert flow.run(object()) == (True, "已返回长安")
    assert calls == ["open", "open"]
