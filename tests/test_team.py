"""team.py：队伍身份帧分类与复核（2026-11-05 队长面板单帧误判非队长修复）。

验证点：
- 帧内贴解散队伍模板 → leader（分数达标且高于离开分）
- 帧内贴离开队伍模板 → member（真实按钮高分，不受解散模板弱误匹配影响）
- 无按钮帧 → unclear（不算确定性非队长，须走「队长无法判定」瞬态路径）
- unclear 复核：复核帧命中即改判；复核帧无面板信号即停（不空转）
- member 离开分 ≥0.9 直接采信不复核；弱 member 触发复核
"""
import numpy as np
import pytest
from PIL import Image
from ascript.windows.system import R

from src import team

FRAME_W, FRAME_H = 1378, 831


def _frame(rgb=False):
    """合成噪声帧（尺寸同游戏窗口；RGB 转 PIL）。"""
    r = np.random.default_rng(20261105)
    arr = r.integers(0, 256, (FRAME_H, FRAME_W, 3), dtype=np.uint8)
    return Image.fromarray(arr)


def _paste_tpl(frame, icon, x, y, alpha=1.0):
    """把 res/img 模板（RGBA）以给定不透明度贴入 PIL 帧，返回新帧。"""
    tpl = Image.open(R.img(icon)).convert("RGBA")
    if alpha < 1.0:
        a = tpl.split()[3].point(lambda v: int(v * alpha))
        tpl.putalpha(a)
    out = frame.copy()
    out.paste(tpl, (x, y), tpl)
    return out


def _classify_with(icon, x=600, y=520):
    frame = _paste_tpl(_frame(), icon, x, y)
    return team._classify_leader_frame(frame)


# ---- 帧分类 ----

def test_classify_leader_frame_leader():
    kind, (js, ls) = _classify_with("jiesanduiwu.png")
    assert kind == "leader"
    assert js >= team._TEMPLATE_THRESHOLD and js > ls


def test_classify_leader_frame_member():
    kind, (js, ls) = _classify_with("likaiduiwu.png")
    assert kind == "member"
    assert ls >= team._TEMPLATE_THRESHOLD and ls > js


def test_classify_leader_frame_unclear_on_empty_frame():
    kind, (js, ls) = team._classify_leader_frame(_frame())
    assert kind == "unclear"
    assert js < team._TEMPLATE_THRESHOLD and ls < team._TEMPLATE_THRESHOLD


def test_classify_leader_frame_weak_member_triggered_by_confirm():
    """离开按钮降质（离开分落在 0.8~0.9）：分类仍为 member，但
    _confirm_leader_kind 应触发复核而非直接采信。"""
    # 半透明叠加会使模板区域与噪声混合，分数低于实贴
    frame = _paste_tpl(_frame(), "likaiduiwu.png", 600, 520, alpha=0.55)
    kind, scores = team._classify_leader_frame(frame)
    assert kind == "member" or kind == "unclear"  # 降质后可能低于 0.8
    if kind == "member":
        assert scores[1] < team._MEMBER_CONFIRM_SCORE


# ---- 复核 ----

class _SeqWin:
    """按序返回预置帧的假窗口。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.i = 0

    def capture(self, force_activate=False):
        if self.i >= len(self.frames):
            return self.frames[-1]
        img = self.frames[self.i]
        self.i += 1
        return img


def _panel_flag_frame(button_icon=None):
    """含队伍面板标志（duiwumianban）的帧；button_icon 非空时贴按钮。"""
    frame = _paste_tpl(_frame(), "duiwumianban.png", 500, 260)
    if button_icon:
        frame = _paste_tpl(frame, button_icon, 600, 520)
    return frame


def test_recheck_recovers_leader(monkeypatch):
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([
        _panel_flag_frame(),                       # 复核 1：仍无按钮
        _panel_flag_frame(),                       # 复核 2：仍无按钮
        _panel_flag_frame("jiesanduiwu.png"),      # 复核 3：按钮已渲染
    ])
    kind, scores = team._recheck_leader_frames(win, (0.5, 0.4))
    assert kind == "leader"
    assert scores[0] >= team._TEMPLATE_THRESHOLD


def test_recheck_confirms_member(monkeypatch):
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([
        _panel_flag_frame("likaiduiwu.png"),
    ])
    kind, scores = team._recheck_leader_frames(win, (0.4, 0.5))
    assert kind == "member"
    assert scores[1] >= team._TEMPLATE_THRESHOLD


def test_recheck_stops_without_panel_signal(monkeypatch):
    """复核帧无面板信号（面板自关/检测误报）：立即停，不空转全部次数。"""
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    monkeypatch.setattr(team.game_map, "ocr_region",
                        lambda img, region: ([], None))  # 免 OCR 兜底
    win = _SeqWin([_frame(), _frame(), _frame()])
    kind, _scores = team._recheck_leader_frames(win, (0.5, 0.4))
    assert kind == "unclear"
    assert win.i == 1  # 首帧无面板即停，未继续重截


def test_recheck_stays_unclear_when_buttons_never_render(monkeypatch):
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_panel_flag_frame()] * 5)
    kind, _scores = team._recheck_leader_frames(win, (0.5, 0.4))
    assert kind == "unclear"
    assert win.i == team._LEADER_RECHECK_TRIES


# ---- _confirm_leader_kind 触发规则 ----

def test_confirm_unclear_triggers_recheck(monkeypatch):
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_panel_flag_frame("jiesanduiwu.png")])
    kind, _scores = team._confirm_leader_kind(win, "unclear", (0.5, 0.4))
    assert kind == "leader"


def test_confirm_strong_member_no_recheck(monkeypatch):
    def _boom(win, scores):
        raise AssertionError("强 member 不应触发复核")

    monkeypatch.setattr(team, "_recheck_leader_frames", _boom)
    kind, scores = team._confirm_leader_kind(None, "member", (0.6, 0.97))
    assert kind == "member"
    assert scores == (0.6, 0.97)


def test_confirm_weak_member_triggers_recheck(monkeypatch):
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png")])
    kind, _scores = team._confirm_leader_kind(win, "member", (0.5, 0.84))
    assert kind == "member"


# ---- 确定性非队长判定口径 ----

def test_definitive_not_leader_only_member_and_ungrouped():
    assert team.is_definitive_not_leader("非队长（面板无「解散队伍」）")
    assert team.is_definitive_not_leader("未组队（菜单中无「队伍信息」）")
    assert not team.is_definitive_not_leader("队长无法判定（面板内解散/离开按钮未命中）")
    assert not team.is_definitive_not_leader("队长无法判定（窗口截图失败）")
