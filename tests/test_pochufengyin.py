"""pochufengyin.py：破除封印（点击）/钓鱼·开采石头（长按）共用闭环控制。

覆盖各推进方式的控制语义：
- 纯长按（锁定持握）：按住后进入目标区间也继续按住（假定游戏锁定
  游标），仅冲出右缘余量才松开回落、回到 L 以下再按住；结束/战斗
  打断必须抬起
- 长按运输 + 短段长按维持（hold_catch，钓鱼/开采石头）：长按推过带上缘
  →松手回落→回 C 后短段长按/松手围绕 C 保持到窗口结束，倒计时 ≤3s 不停手
- 点击：走 human_click 连点，绝不使用长按通道
- 冲刺连点（mash）：rapid_click_burst 按每击推进量冲进目标带后维持泵
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


def _install(monkeypatch, *, p_values, title_frames, zone_frames=None,
             target_x=908):
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
            return (target_x, 500)  # 目标带中心（默认 C≈8.49）
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


def test_click_mode_advances_to_zone_center(monkeypatch):
    """点击模式推进目标 = 目标带中心 C：填充在 L 附近（P=0.05，带
    [0.0,1.1]）不能一进带就停手——破除封印松手后填充会回落，停在左缘
    有掉出带下的风险，须点到 C≈0.5 附近（用户 2026-11-11）。"""
    clicks = []
    # C≈0.54：P=0.05/0.30/0.50 都 <C 继续点，P=0.59 ≥C 停手
    win = _install(monkeypatch, p_values=[0.05, 0.30, 0.50, 0.60],
                   title_frames=5, target_x=465)
    monkeypatch.setattr(pochufengyin, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(pochufengyin, "POCHU_TIER_NEAR", 0.0)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="客栈", label="破除封印")
    assert ok and "命中区间" in info and "P=0.59" in info
    assert len(clicks) == 3          # 到 C 附近即停手，P=0.59 那帧不点


def test_click_mode_zero_click_in_band_reports_hit(monkeypatch):
    """点击模式 0 次点击也要按实测 P 上报：填充一开始就在 C 以上
    （P=0.59，带 [0.0,1.1]）→ 全程停手，结束必须报「命中区间」+P 实测值，
    不能误报「P=未知/未确认命中」（2026-11-11 客栈实机日志教训）。"""
    clicks = []
    win = _install(monkeypatch, p_values=[0.60] * 6, title_frames=3,
                   target_x=465)
    monkeypatch.setattr(pochufengyin, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="客栈", label="破除封印")
    assert ok and "命中区间" in info
    assert "P=0.59" in info and "P=未知" not in info
    assert clicks == []


class _FakeClock:
    """伪时钟：每次取时推进 0.05s（一轮询约 0.25s），维持泵积分器可测。"""

    def __init__(self, t0=1000.0):
        self.t = t0

    def time(self):
        self.t += 0.05
        return self.t

    def sleep(self, s):
        self.t += s

    @staticmethod
    def strftime(fmt):
        import time as _t
        return _t.strftime(fmt)


def _install_mash(monkeypatch, *, per_click, decay=0.0, title_frames=14):
    """冲刺模式仿真：rapid_click_burst 每击把填充推进 per_click 刻度。

    decay>0 时每次测量（_fill）前先回退 decay 刻度，模拟真机
    「填充条自动回退」（用户 2026-11-10 实测：不补点即掉出目标带）。
    伪时钟让维持泵点击率积分器与速率伺服按轮询推进。
    返回 (win, state, clicks)；state={"p": 当前刻度, "clicks": 总击数}；
    clicks 记录 (x, y, times, 捕获轮次)。默认每击 0.1 刻度
    （2026-11-10 真机日志 10 击 P≈1.06 实测）。
    """
    win = _Win()
    state = {"p": 0.0, "clicks": 0}
    clicks = []

    def _burst(w, x, y, times=1, press=0.02):
        state["p"] = min(10.0, state["p"] + per_click * times)
        state["clicks"] += times
        clicks.append((x, y, times, win.n))

    def _match(bgr, icon, threshold=0.8, region=None, fallback=True):
        if icon == "title":
            return (700, 500) if win.n <= title_frames else None
        if icon in ("action", pochufengyin.POCHU_CLICK_ICON):
            return (950, 600)
        if icon == pochufengyin.POCHU_TARGET_ICON:
            return (908, 500)   # C≈8.49，L≈7.97 R≈9.01
        return None             # 「开始」按钮不出现（按钮已是流程态）

    def _fill(bgr):
        if decay:
            state["p"] = max(0.0, state["p"] - decay)
        return int(round(pochufengyin.POCHU_SCALE_X0
                         + state["p"] / 10.0 * pochufengyin.POCHU_SCALE_W))

    monkeypatch.setattr(pochufengyin, "MouseHold", _Hold)
    monkeypatch.setattr(pochufengyin, "rapid_click_burst", _burst)
    monkeypatch.setattr(pochufengyin, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("冲刺模式不应走 human_click")))
    monkeypatch.setattr(pochufengyin.game_map, "match_frame_np", _match)
    monkeypatch.setattr(pochufengyin, "pochu_fill_edge", _fill)
    monkeypatch.setattr(pochufengyin, "_pochu_countdown", lambda *a, **k: None)
    monkeypatch.setattr(pochufengyin, "_pochu_ocr_gated", lambda *a, **k: None)
    monkeypatch.setattr(pochufengyin, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(pochufengyin, "POCHU_TICK_PACE", 0.0)
    monkeypatch.setattr(pochufengyin, "POCHU_END_WINDOW", 0.0)
    monkeypatch.setattr(pochufengyin, "time", _FakeClock())
    return win, state, clicks


def _mash_band():
    """仿真目标带（含冲刺模式目标修正偏移）。"""
    off = pochufengyin.POCHU_MASH_ZONE_OFFSET
    lo = pochufengyin.pochu_tick(
        908 - pochufengyin.POCHU_ZONE_HALF) + off
    hi = pochufengyin.pochu_tick(
        908 + pochufengyin.POCHU_ZONE_HALF) + off
    return lo, hi


def test_mash_mode_bursts_to_target_zone(monkeypatch):
    """冲刺连点（蛟魔探秘钓鱼）：单击仅 0.1 刻度时，按在线估算的每击
    推进量连点冲进目标带（三档节奏 3 击/秒永远到不了目标带，用户实测）。"""
    win, state, clicks = _install_mash(monkeypatch, per_click=0.1)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="蛟魔探秘钓鱼", mash=True)
    assert ok and "命中区间" in info
    lo, hi = _mash_band()
    assert lo <= state["p"] <= hi
    assert clicks and clicks[0][:2] == (950, 600)
    # 接近段两次大冲刺 → 进入维持泵（按速率小步补点）
    counts = [c[2] for c in clicks]
    assert counts[0] >= 20 and counts[1] >= 10
    assert all(n in (1, 2) for n in counts[2:])
    assert len(clicks) > 5                           # 到带后仍在维持
    assert _Hold.events == []


def test_mash_mode_converges_when_click_advance_small(monkeypatch):
    """每击推进量更小（0.05）时，比例在线修正 + 单次冲刺上限 + 多轮
    补点仍能收敛进目标带（点击漏注册/推进慢场景）。"""
    win, state, clicks = _install_mash(monkeypatch, per_click=0.05,
                                       title_frames=18)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="蛟魔探秘钓鱼", mash=True)
    assert ok and "命中区间" in info
    lo, hi = _mash_band()
    assert lo <= state["p"] <= hi
    assert state["clicks"] > 110   # 需要比 0.1/击 更多的点击


def test_mash_mode_maintains_zone_against_decay(monkeypatch):
    """填充条自动回退（用户 2026-11-10 实测失败原因）：进入目标带后
    仍须持续补点维持；倒计时 ≤3s 也不停手（与破除封印不同），一直
    维持到窗口标题消失。"""
    win, state, clicks = _install_mash(monkeypatch, per_click=0.2,
                                       decay=0.15, title_frames=40)
    monkeypatch.setattr(pochufengyin, "_pochu_countdown", lambda *a, **k: 1)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="蛟魔探秘钓鱼", mash=True)
    assert ok and "命中区间" in info
    lo, hi = _mash_band()
    assert lo <= state["p"] <= hi
    # 倒计时 1s 仍维持补点：标题消失前（轮次 41）仍有点击
    assert max(c[3] for c in clicks) > 30
    # 全程维持住，不因回退掉出目标带
    assert state["p"] >= lo


def test_mash_stops_when_countdown_gone(monkeypatch):
    """倒计时曾读到、随后连续消失（结果画面）→ 冲刺模式停手，防在
    结算画面继续空点（2026-11-10 实机失败后空点 7s 的教训）。"""
    win, state, clicks = _install_mash(monkeypatch, per_click=0.2,
                                       title_frames=40)
    calls = {"n": 0}

    def _cnt(*a, **k):
        calls["n"] += 1
        return 2 if calls["n"] <= 10 else None

    monkeypatch.setattr(pochufengyin, "_pochu_countdown", _cnt)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="蛟魔探秘钓鱼", mash=True)
    assert ok
    # 结算后不再点击：最后一次点击远早于标题消失（轮次 41）
    assert clicks and max(c[3] for c in clicks) < 30


def test_click_mode_stops_at_countdown(monkeypatch):
    """对照：普通点击模式（破除封印）倒计时 ≤3s 仍按原规则停手。"""
    clicks = []
    win = _install(monkeypatch, p_values=[0.0, 0.0, 0.0], title_frames=6)
    monkeypatch.setattr(pochufengyin, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(pochufengyin, "_pochu_countdown", lambda *a, **k: 1)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="客栈", label="破除封印")
    assert ok and "倒计时" in info
    assert clicks == []   # 倒计时 1s：完全停手不点


def test_mash_ignored_in_hold_mode(monkeypatch):
    """mash 与 hold 同时传入时长按优先（冲刺连点仅点击模式生效）。"""
    _Hold.events = []
    win = _install(monkeypatch, p_values=[0.0, 8.5, 8.6], title_frames=5)
    monkeypatch.setattr(pochufengyin, "rapid_click_burst",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("长按模式不应走 rapid_click_burst")))
    ok, _info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action", mash=True)
    assert ok
    assert _Hold.events and _Hold.events[0][0] == "start"


# ---- 长按运输 + 短段长按维持（hold_catch；开采石头 2026-11-10、
# 钓鱼 2026-11-12 用户技巧）----
def _install_catch(monkeypatch, *, p_values, title_frames):
    """hold_catch 仿真：脚本化填充位置 + 伪时钟 + MouseHold 事件记录。

    目标带同 _install（C≈8.49、L≈7.97、R≈9.01）；CATCH_MIN_GAP 置 0
    便于脚本轮询驱动重运输/维持按住；维持段按住与松手都走 MouseHold
    （_Hold.events 记录，与运输段同一通道——不再用 rapid_click_burst，
    静默连点会丢 UP 把按钮卡在按下状态，2026-11-12 实机教训）。
    """
    win = _install(monkeypatch, p_values=p_values, title_frames=title_frames)
    monkeypatch.setattr(pochufengyin, "time", _FakeClock())
    monkeypatch.setattr(pochufengyin, "POCHU_CATCH_MIN_GAP", 0.0)
    return win


def test_hold_catch_transport_release_then_maintain(monkeypatch):
    """长按推过带上缘（R+0.2）松手 → 回落至 C 切换短段长按维持：
    P≤C-LO 按住、P≥C-HI 松手，围绕 C 振荡 → 命中区间。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch,
        p_values=[0.0, 9.5, 9.0, 8.3, 8.2, 8.45, 8.2, 8.45],
        title_frames=9)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="开采石头",
        hold=True, action_icon="action", hold_catch=True)
    assert ok and "命中区间" in info
    assert [e[0] for e in _Hold.events] == \
        ["start", "stop", "start", "stop", "start", "stop"]
    assert _Hold.events[0] == ("start", 2, 950, 600)
    assert _Hold.events[1] == ("stop", 3)      # P=9.5 ≥ R+0.2 松手等回落
    assert _Hold.events[2][0] == "start" and _Hold.events[2][1] >= 5
    # 维持按住只走 MouseHold，不再用连点脉冲
    assert _Hold.events[3] == ("stop", 7)


def test_hold_catch_repress_when_falls_below_band(monkeypatch):
    """维持段谷值跌破带下 → 放弃维持、重新长按运输（限次），再回落捕捉。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch,
        p_values=[0.0, 9.5, 8.3, 7.5, 7.5, 9.5, 8.3, 8.2, 8.45],
        title_frames=9)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="开采石头",
        hold=True, action_icon="action", hold_catch=True)
    assert ok and "命中区间" in info
    # 运输 → 松手 → 掉出带下 → 重新运输 → 松手 → 维持按住
    assert [e[0] for e in _Hold.events] == \
        ["start", "stop", "start", "stop", "start", "stop"]
    assert _Hold.events[2][1] == 6              # 第 2 次运输按住


def test_hold_catch_maintain_adapts_lo_after_below_band(monkeypatch):
    """掉出带下 → 维持按住阈值 LO 减小（在更高位置提前按住）：
    8.31 高于初始阈值 C-0.20=8.29，只有自适应后 C-0.17=8.32 才会按住。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch,
        p_values=[0.0, 9.5, 8.3, 7.5, 7.5, 9.5, 8.3, 8.31, 8.45],
        title_frames=9)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action", hold_catch=True)
    assert ok
    # r9 p=8.31 在自适应阈值（C-0.17）下按住
    assert ("start", 9, 950, 600) in _Hold.events


def test_hold_catch_maintain_releases_at_aim(monkeypatch):
    """维持段松手阈值 = C-HI（提前松手补偿动作滞后）：P=8.40 时松手，
    说明没有等到 R 才松（否则峰值会冲过带上缘）。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch,
        p_values=[0.0, 9.5, 8.3, 8.2, 8.40, 8.2, 8.45],
        title_frames=7)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action", hold_catch=True)
    assert ok
    # 维持按住（r5）后，在 r6 测得 8.40 ≥ C-0.10=8.39 即松手
    assert ("start", 5, 950, 600) in _Hold.events
    assert ("stop", 6) in _Hold.events


def test_hold_catch_ignores_countdown_stop(monkeypatch):
    """倒计时 ≤3s 不得停手（钓鱼 2026-11-12）：填充会回退，停手必然
    掉出目标带——回落途中倒计时已 ≤3s 仍须建立维持并保持到窗口结束
    （与冲刺模式同理）。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch,
        p_values=[0.0, 9.5, 8.3, 8.2, 8.45],
        title_frames=5)
    monkeypatch.setattr(pochufengyin, "_pochu_countdown", lambda *a, **k: 2)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="玄枢轶闻", label="钓鱼",
        hold=True, action_icon="action", hold_catch=True)
    assert ok and "命中区间" in info
    # 倒计时 2s 仍回落捕捉并维持按住（未按倒计时停手）
    assert ("start", 5, 950, 600) in _Hold.events


def test_hold_catch_releases_on_window_end(monkeypatch):
    """窗口在长按运输中断（标题消失）→ finally 必须抬手，不卡按下状态。"""
    _Hold.events = []
    win = _install_catch(
        monkeypatch, p_values=[0.0, 8.0, 8.2], title_frames=4)
    ok, info = pochufengyin.play_pochufengyin(
        win, "title", prefix="名望", label="开采石头",
        hold=True, action_icon="action", hold_catch=True)
    assert ok
    assert [e[0] for e in _Hold.events] == ["start", "stop"]
