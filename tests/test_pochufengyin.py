"""pochufengyin.py：破除封印（点击）/钓鱼（长按）共用闭环控制。

覆盖两种推进方式的控制语义：
- 长按：按住后进入目标区间也继续按住（游戏锁定游标，松手回退脱钩），
  仅冲出右缘余量才松开回落、回到 L 以下再按住；结束/战斗打断必须抬起
- 点击：走 human_click 连点，绝不使用长按通道
"""
import numpy as np

from src import pochufengyin


class _Hold:
    """MouseHold 替身：记录按下/抬起事件与当时的截图轮次（类级共享）。"""
    events = []

    def __init__(self, win, x, y, button="primary"):
        self.win = win
        self.x, self.y = x, y

    def start(self):
        type(self).events.append(("start", self.win.n, self.x, self.y))
        return True

    def stop(self):
        type(self).events.append(("stop", self.win.n))


class _Win:
    def __init__(self):
        self.n = 0

    def capture(self, force_activate=False):
        self.n += 1
        return np.zeros((831, 1378, 3), dtype=np.uint8)


def _install(monkeypatch, *, p_values, title_frames, zone_frames=None):
    """安装模板/测量替身：标题前 title_frames 帧可见，之后消失。

    p_values 按已开始后的轮询顺序消费（每轮一个刻度值，None=无填充）。
    zone_frames 不为 None 时，仅前 zone_frames 帧能匹配到目标带（模拟
    填充前缘遮住括号导致模板掉分）。
    """
    win = _Win()
    ps = iter(p_values)

    def _match(bgr, icon, threshold=0.8, region=None, fallback=True):
        if icon == "title":
            return (700, 500) if win.n <= title_frames else None
        if icon in ("action", pochufengyin.POCHU_CLICK_ICON):
            return (950, 600)  # 长按/点击按钮中心
        if icon == pochufengyin.POCHU_TARGET_ICON:
            if zone_frames is not None and win.n > zone_frames:
                return None
            return (908, 500)  # 目标带中心 C≈8.49
        return None            # 「开始」按钮不出现在测试帧

    def _fill(bgr):
        try:
            v = next(ps)
        except StopIteration:
            return None
        if v is None or v <= 0:
            return None
        return int(round(pochufengyin.POCHU_SCALE_X0
                         + v / 10.0 * pochufengyin.POCHU_SCALE_W))

    monkeypatch.setattr(pochufengyin, "MouseHold", _Hold)
    monkeypatch.setattr(pochufengyin.game_map, "match_frame_np", _match)
    monkeypatch.setattr(pochufengyin, "pochu_fill_edge", _fill)
    monkeypatch.setattr(pochufengyin, "_pochu_countdown", lambda *a, **k: None)
    monkeypatch.setattr(pochufengyin, "_pochu_ocr_gated", lambda *a, **k: None)
    monkeypatch.setattr(pochufengyin, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(pochufengyin, "POCHU_TICK_PACE", 0.0)
    monkeypatch.setattr(pochufengyin, "POCHU_HOLD_TICK_PACE", 0.0)
    monkeypatch.setattr(pochufengyin, "POCHU_END_WINDOW", 0.0)
    return win


def test_hold_mode_keeps_holding_after_entering_zone(monkeypatch):
    """长按模式：P 进入目标区间也不松手（游戏锁定游标，松手自动回退
    导致鱼脱钩），一直按住到窗口结束才由 finally 释放。"""
    _Hold.events = []
    win = _install(monkeypatch, p_values=[0.0, 2.0, 8.5, 8.6],
                   title_frames=5)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action")
    assert ok and "命中区间" in info
    # 只在轮次 2 按下一次，窗口结束（轮次 6）才释放，区间内不松手
    assert _Hold.events == [("start", 2, 950, 600), ("stop", 6)]


def test_hold_mode_uses_cached_zone_when_bracket_occluded(monkeypatch):
    """填充前缘遮住目标括号（2026-11-10 真机实测）→ 沿用缓存区间继续
    测量；否则目标带一掉匹配就无法继续决策（按住/释放时机失效）。"""
    _Hold.events = []
    win = _install(monkeypatch, p_values=[0.0, 5.0, 8.5, 8.6],
                   title_frames=5, zone_frames=2)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action")
    assert ok and "命中区间" in info
    assert _Hold.events == [("start", 2, 950, 600), ("stop", 6)]


def test_hold_mode_over_right_edge_releases_then_represses(monkeypatch):
    """长按模式冲出右缘余量（P>R+1 连续 2 轮）→ 松开让其回落；回到
    L 以下再次按住（不设永久终态，避免卡在过冲状态）。"""
    _Hold.events = []
    win = _install(monkeypatch, p_values=[0.0, 10.5, 10.5, 5.0, 5.0],
                   title_frames=6)
    ok, _info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action")
    assert ok
    assert [e[0] for e in _Hold.events] == ["start", "stop", "start", "stop"]
    assert _Hold.events[1] == ("stop", 4)   # 过冲确认后立即松开
    assert _Hold.events[2] == ("start", 5, 950, 600)  # 回落 <L 再按住


def test_hold_mode_releases_on_battle_interrupt(monkeypatch):
    """长按中途战斗打断：返回前必须抬起按键（防鼠标卡在按下状态）。"""
    _Hold.events = []
    win = _install(monkeypatch, p_values=[0.0, 0.0, 0.0], title_frames=99)
    monkeypatch.setattr(pochufengyin, "is_in_battle_frame",
                        lambda bgr: win.n >= 3)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action")
    assert not ok and "中断" in info
    assert [e[0] for e in _Hold.events] == ["start", "stop"]


def test_click_mode_uses_click_not_hold(monkeypatch):
    """点击模式（破除封印）：走 human_click 连点，绝不使用长按通道。"""
    _Hold.events = []
    clicks = []
    win = _install(monkeypatch, p_values=[0.0, 0.0], title_frames=4)
    monkeypatch.setattr(pochufengyin, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="客栈", label="破除封印")  # hold=False
    assert ok and "结束" in info
    assert clicks and clicks[0] == (950, 600)
    assert _Hold.events == []
