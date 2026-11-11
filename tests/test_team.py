"""team.py：队伍身份帧分类与复核（2026-11-05 队长面板单帧误判非队长修复）。

验证点：
- 帧内贴解散队伍模板 → leader（分数达标且高于离开分）
- 帧内贴离开队伍模板 → member（真实按钮高分，不受解散模板弱误匹配影响）
- 无按钮帧 → unclear（不算确定性非队长，须走「队长无法判定」瞬态路径）
- unclear 复核：复核帧命中即改判；复核帧无面板信号即停（不空转）
- member 离开分 ≥0.9 直接采信不复核；弱 member 触发复核
"""
import time
from pathlib import Path

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


# ---- 自动归队（todo1：轮回司判定与 maybe_rejoin） ----

def test_is_in_lunhuisi():
    assert team.is_in_lunhuisi(("轮回司", 10, 10))
    assert not team.is_in_lunhuisi(None)
    assert not team.is_in_lunhuisi(("宝象国", 86, 86))
    assert not team.is_in_lunhuisi(("轮回司东", 1, 1))  # 地名须精确匹配


def test_at_lunhuisi_not_here_no_second_read(monkeypatch):
    """首读不在轮回司即返回：热路径免第二轮空转（常态就是不在）。"""
    reads = []
    monkeypatch.setattr(team.game_map, "read_map_position",
                        lambda win, img=None: reads.append(1) or ("宝象国", 86, 86))
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_frame()])
    ok, desc = team._at_lunhuisi(win)
    assert not ok and "不在轮回司" in desc
    assert len(reads) == 1


def test_at_lunhuisi_requires_two_confirms(monkeypatch):
    """首读命中、次读变卦 → 不在轮回司（防位置 OCR 单次误读）。"""
    reads = {"n": 0}

    def _pos(win, img=None):
        reads["n"] += 1
        return ("轮回司", 1, 1) if reads["n"] == 1 else ("宝象国", 86, 86)

    monkeypatch.setattr(team.game_map, "read_map_position", _pos)
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_frame()])
    ok, desc = team._at_lunhuisi(win)
    assert not ok and "不在轮回司" in desc
    assert reads["n"] == 2


def test_at_lunhuisi_confirmed(monkeypatch):
    monkeypatch.setattr(team.game_map, "read_map_position",
                        lambda win, img=None: ("轮回司", 1, 1))
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_frame()])
    ok, desc = team._at_lunhuisi(win)
    assert ok and "在轮回司" in desc


def test_maybe_rejoin_skips_when_not_lunhuisi(monkeypatch):
    """不在轮回司：不开面板，返回未处理（调用方按正常节奏轮询）。"""
    opened = []
    monkeypatch.setattr(team, "_at_lunhuisi", lambda win: (False, "不在轮回司"))
    monkeypatch.setattr(team, "open_team_panel",
                        lambda win: opened.append(1) or (True, ""))
    handled, desc = team.maybe_rejoin(None)
    assert not handled
    assert opened == []


def test_maybe_rejoin_leader_skip(monkeypatch):
    """队长身份（面板解散按钮高分）：无归队按钮，跳过并关闭面板。"""
    closed = []
    monkeypatch.setattr(team, "_at_lunhuisi", lambda win: (True, "在轮回司"))
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: closed.append(1) or (True, ""))
    win = _SeqWin([_panel_flag_frame("jiesanduiwu.png")])
    handled, desc = team.maybe_rejoin(win, prefix="[五倍]")
    assert handled and "队长" in desc
    assert closed == [1]


def test_maybe_rejoin_member_template_missing(monkeypatch):
    """队员但归队模板缺失：降级告警跳过，绝不误点击。"""
    clicked = []
    monkeypatch.setattr(team, "_at_lunhuisi", lambda win: (True, "在轮回司"))
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_tpl_path", lambda icon: Path("不存在_zudui_guidui.png"))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png")])
    handled, desc = team.maybe_rejoin(win)
    assert handled and "模板未提供" in desc
    assert clicked == []


def test_maybe_rejoin_click_success(monkeypatch):
    """队员 + 归队按钮命中：点击后位置离开轮回司即成功。"""
    clicked = []
    monkeypatch.setattr(team, "_at_lunhuisi", lambda win: (True, "在轮回司"))
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_tpl_path", lambda icon: Path(__file__))  # 存在
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8: (120, 300))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    monkeypatch.setattr(team.game_map, "read_map_position",
                        lambda win, img=None: ("宝象国", 86, 86))
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png")])
    handled, desc = team.maybe_rejoin(win)
    assert handled and desc.startswith("已归队")
    assert clicked == [(120, 300)]


# ---- 组号归队（rejoin_from_away：暂离不限于轮回司，2026-11-07 用户要求） ----


def test_rejoin_from_away_no_lunhuisi_gate(monkeypatch):
    """无轮回司前置：暂离队员在任何位置都点归队（maybe_rejoin 的
    _at_lunhuisi 门槛不适用）。点击后校验帧面板已关闭 = 成功。"""
    def _boom(win):
        raise AssertionError("rejoin_from_away 不得做轮回司判定")
    monkeypatch.setattr(team, "_at_lunhuisi", _boom)
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_tpl_path", lambda icon: Path(__file__))
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8: (120, 300))
    clicked = []
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png"), _frame()])
    handled, desc = team.rejoin_from_away(win, prefix="[组号]")
    assert handled and desc.startswith("已归队")
    assert clicked == [(120, 300)]


def test_rejoin_from_away_button_gone_success(monkeypatch):
    """点击归队后归队按钮从面板消失 = 成功（面板仍开）。"""
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_tpl_path", lambda icon: Path(__file__))
    monkeypatch.setattr(team, "human_click", lambda win, x, y: None)
    seq = iter([(120, 300), None])  # 首帧按钮命中；校验帧按钮消失
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8: next(seq))
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png"),
                   _panel_flag_frame()])  # 校验帧：面板开、无归队按钮
    handled, desc = team.rejoin_from_away(win, prefix="[组号]")
    assert handled and desc == "已归队（归队按钮已消失）"


def test_rejoin_from_away_button_persists_unconfirmed(monkeypatch):
    """点击后归队按钮持续在场：报告未确认（防假成功）。"""
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_tpl_path", lambda icon: Path(__file__))
    monkeypatch.setattr(team, "human_click", lambda win, x, y: None)
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8: (120, 300))
    t = {"t": 0}
    monkeypatch.setattr(team.time, "time", lambda: t["t"])
    monkeypatch.setattr(team.time, "sleep",
                        lambda s: t.__setitem__("t", t["t"] + s))
    win = _SeqWin([_panel_flag_frame("likaiduiwu.png"),
                   _panel_flag_frame("likaiduiwu.png")])  # 校验帧按钮仍在
    handled, desc = team.rejoin_from_away(win, prefix="[组号]")
    assert handled and "未确认" in desc


# ---- 自动创建队伍（修罗/五倍开关默认关：未组队点组队菜单「创建队伍」） ----

def _mock_create_env(monkeypatch, menu_in_team=False, tpl_exists=True,
                     find_pt=(120, 300), panel_results=None):
    """搭 ensure_team_created 的桩环境，返回记录器 dict。"""
    env = {"clicked": [], "menu_closed": 0, "panel_closed": 0}
    monkeypatch.setattr(team, "_ensure_menu_open",
                        lambda win: (True, _frame()))
    monkeypatch.setattr(team, "_has_duiwuxinxi",
                        lambda img: menu_in_team)
    monkeypatch.setattr(team, "_close_menu",
                        lambda win: env.__setitem__("menu_closed", env["menu_closed"] + 1)
                        or (True, "组队菜单已关闭"))
    monkeypatch.setattr(team, "_tpl_path",
                        lambda icon: Path(__file__) if tpl_exists
                        else Path("不存在_chuangjianduiwu.png"))
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8: find_pt)
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: env["clicked"].append((x, y)))
    monkeypatch.setattr(team, "_is_panel_open",
                        (lambda win: panel_results.pop(0))
                        if panel_results else (lambda win: False))
    monkeypatch.setattr(team, "close_team_panel",
                        lambda win: env.__setitem__("panel_closed", env["panel_closed"] + 1)
                        or (True, "队伍面板已关闭"))
    monkeypatch.setattr(team.time, "sleep", lambda s: None)
    return env


def test_ensure_team_created_skips_when_in_team(monkeypatch):
    """已在队伍中（菜单有「队伍信息」）：不点击创建，关闭菜单即返回。"""
    env = _mock_create_env(monkeypatch, menu_in_team=True)
    ok, desc = team.ensure_team_created(None)
    assert ok and "已在队伍中" in desc
    assert env["clicked"] == []
    assert env["menu_closed"] == 1
    assert env["panel_closed"] == 0


def test_ensure_team_created_click_and_verify(monkeypatch):
    """未组队：点击「创建队伍」→ 面板打开即创建成功并关闭面板。"""
    env = _mock_create_env(monkeypatch, panel_results=[False, True])
    ok, desc = team.ensure_team_created(None)
    assert ok and "创建成功" in desc
    assert env["clicked"] == [(120, 300)]
    assert env["panel_closed"] == 1


def test_ensure_team_created_template_missing(monkeypatch):
    """创建按钮模板缺失：报模板未提供，绝不误点击。"""
    env = _mock_create_env(monkeypatch, tpl_exists=False)
    ok, desc = team.ensure_team_created(None)
    assert not ok and "模板未提供" in desc
    assert env["clicked"] == []
    assert env["menu_closed"] == 1


def test_ensure_team_created_template_not_found(monkeypatch):
    """菜单中未定位到创建按钮：报失败关闭菜单，不点击。"""
    env = _mock_create_env(monkeypatch, find_pt=None)
    ok, desc = team.ensure_team_created(None)
    assert not ok and "未定位到" in desc
    assert env["clicked"] == []
    assert env["menu_closed"] == 1


def test_ensure_team_created_panel_never_opens(monkeypatch):
    """点击后队伍面板始终未打开：报失败并兜底关闭菜单。"""
    env = _mock_create_env(monkeypatch, panel_results=[False] * 9)
    ok, desc = team.ensure_team_created(None)
    assert not ok and "面板未打开" in desc
    assert env["clicked"] == [(120, 300)]
    assert env["menu_closed"] == 1


def test_ensure_team_created_menu_not_open(monkeypatch):
    """组队菜单打不开（瞬态）：报失败，不点击。"""
    env = _mock_create_env(monkeypatch)
    monkeypatch.setattr(team, "_ensure_menu_open", lambda win: (False, None))
    ok, desc = team.ensure_team_created(None)
    assert not ok and "菜单未打开" in desc
    assert env["clicked"] == []


# ---- 自动创建并入队伍面板打开（2026-11-11 组号逻辑优化） ----


def test_open_team_panel_create_if_missing(monkeypatch):
    """create_if_missing：未组队时同一次菜单打开内点「创建队伍」，
    成功后面板保持打开供调用方判身份/读成员（不再分开两次开菜单）。"""
    env = _mock_create_env(monkeypatch, panel_results=[False, True])
    ok, desc = team.open_team_panel(None, create_if_missing=True)
    assert ok and "创建成功" in desc
    assert env["clicked"] == [(120, 300)]
    assert env["menu_closed"] == 0    # 成功路径不额外关菜单
    assert env["panel_closed"] == 0   # 面板保持打开


def test_open_team_panel_create_if_missing_template_missing(monkeypatch):
    """create_if_missing 且模板缺失：报「未组队（模板未提供）」并关
    菜单——调用方按确定性未组队处理，绝不误点菜单其他项。"""
    env = _mock_create_env(monkeypatch, tpl_exists=False)
    ok, desc = team.open_team_panel(None, create_if_missing=True)
    assert not ok and "未组队" in desc and "模板未提供" in desc
    assert env["clicked"] == []
    assert env["menu_closed"] == 1


def test_open_team_panel_no_create_by_default(monkeypatch):
    """默认不创建：菜单无「队伍信息」直接关菜单报未组队（原行为）。"""
    env = _mock_create_env(monkeypatch)
    ok, desc = team.open_team_panel(None)
    assert not ok and "未组队" in desc
    assert env["clicked"] == []
    assert env["menu_closed"] == 1


def test_check_team_min_forwards_create_flag(monkeypatch):
    """check_team_min 把自动创建标记与日志前缀传到 open_team_panel。"""
    seen = {}
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "has_pending_apply", lambda img: False)

    def open_panel(win, create_if_missing=False, prefix="[组队]"):
        seen["create"] = create_if_missing
        seen["prefix"] = prefix
        return False, "组队菜单未打开"

    monkeypatch.setattr(team, "open_team_panel", open_panel)
    monkeypatch.setattr(team, "record_leader_status", lambda is_leader: None)
    win = _SeqWin([_frame()])
    team.check_team_min(win, create_if_missing=True,
                        prefix="[五倍][创建队伍]")
    assert seen == {"create": True, "prefix": "[五倍][创建队伍]"}


def test_chuangjianduiwu_target_frame_unique_hit():
    """创建队伍模板目标帧（贴入原模板）以 0.8 阈值唯一命中（用户
    2026-11-07 提供 113x22 模板；真图交叉验证待实机菜单帧校准）。"""
    frame = _paste_bgr(_bgr_frame(), "chuangjianduiwu.png", 600, 400)
    hits = team.game_map.match_frame_all(frame, team.CHUANGJIAN_DUIWU_ICON,
                                         team._TEMPLATE_THRESHOLD)
    assert len(hits) == 1
    # 命中中心 = 贴入中心（113x22 的一半偏移：113//2=56, 22//2=11）
    _score, x, y = hits[0]
    assert (x, y) == (656, 411)


def test_chuangjianduiwu_noise_frame_below_threshold():
    """纯噪声帧（非目标场景）创建队伍模板最佳分低于 0.8 阈值。"""
    score, _ = team.game_map.best_match_np(_bgr_frame(), team.CHUANGJIAN_DUIWU_ICON)
    assert score < team._TEMPLATE_THRESHOLD


def test_chuangjianduiwu_not_confused_as_team_buttons():
    """逆方向：创建队伍按钮不得被队伍信息/归队等既有模板命中
    （菜单判定与归队链路不受新模板影响）。"""
    frame = _paste_bgr(_bgr_frame(), "chuangjianduiwu.png", 600, 500)
    for icon in (team.DUIWU_XINXI_ICON, team.ZUDUI_GUIDUI_ICON,
                 team.ZUDUI_ITEM_ICON):
        score, _ = team.game_map.best_match_np(frame, icon)
        assert score < team._TEMPLATE_THRESHOLD, icon


# ---- 归队按钮模板校准回归（2026-11-06 实测：目标帧 1.000 /
#      非目标帧 离开/解散按钮 0.825 / 纯噪声 0.134） ----

def _bgr_frame(seed=20261106):
    import cv2
    r = np.random.default_rng(seed)
    return r.integers(0, 256, (831, 1378, 3), dtype=np.uint8)


def _paste_bgr(bgr, icon, x, y):
    """把 res/img 模板（转 BGR）贴入 BGR 帧。"""
    import cv2
    img = Image.open(R.img(icon)).convert("RGB")
    tpl = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    out = bgr.copy()
    th, tw = tpl.shape[:2]
    out[y:y + th, x:x + tw] = tpl
    return out


def test_guidui_threshold_at_least_0_9():
    """用户要求归队按钮匹配阈值 0.9+（通用 0.8 会把离开队伍按钮
    0.825 误判成归队按钮）。"""
    assert team.GUIDUI_THRESHOLD >= 0.9


def test_guidui_target_frame_unique_hit():
    """目标帧（含归队按钮）以 GUIDUI_THRESHOLD 唯一命中。"""
    frame = _paste_bgr(_bgr_frame(), "zudui_guidui.png", 600, 400)
    score, (x, y) = team.game_map.best_match_np(frame, team.ZUDUI_GUIDUI_ICON)
    assert score >= team.GUIDUI_THRESHOLD
    # 命中中心 = 贴入中心（90x19 的一半偏移）
    assert (x, y) == (645, 409)


def test_guidui_cross_buttons_below_threshold():
    """非目标帧（离开队伍/解散队伍按钮，同含「队伍」文案样式相近）
    全帧最佳分须低于归队阈值——0.9 恰好分开实测 0.825。"""
    frame = _paste_bgr(_bgr_frame(), "likaiduiwu.png", 600, 500)
    frame = _paste_bgr(frame, "jiesanduiwu.png", 900, 500)
    score, _ = team.game_map.best_match_np(frame, team.ZUDUI_GUIDUI_ICON)
    assert score < team.GUIDUI_THRESHOLD


def test_guidui_not_confused_as_leave_or_disband():
    """逆方向：归队按钮不得被离开/解散模板命中（队长判定双模板
    比分数不破坏）。"""
    frame = _paste_bgr(_bgr_frame(), "zudui_guidui.png", 600, 500)
    sl, _ = team.game_map.best_match_np(frame, team.LIKAI_DUIWU_ICON)
    sj, _ = team.game_map.best_match_np(frame, team.JIESAN_DUIWU_ICON)
    assert sl < team._TEMPLATE_THRESHOLD
    assert sj < team._TEMPLATE_THRESHOLD


# ---- todo2：暂离成员检测与请离（召回标记/请离队伍按钮） ----

def test_zhaohui_calibration():
    """召回标记模板校准回归（实测：目标 1.000 / 其他按钮帧 0.271 /
    噪声 0.216），阈值 0.9 干净分开。"""
    assert team.ZHAOHUI_THRESHOLD >= 0.9
    frame = _paste_bgr(_bgr_frame(), "duiwumianban_zhaohui.png", 600, 400)
    score, _ = team.game_map.best_match_np(frame, team.ZHAOHUI_ICON)
    assert score >= team.ZHAOHUI_THRESHOLD
    frame2 = _paste_bgr(_bgr_frame(), "zudui_guidui.png", 600, 400)
    frame2 = _paste_bgr(frame2, "duiwumian_qingliduiwu.png", 900, 400)
    frame2 = _paste_bgr(frame2, "likaiduiwu.png", 600, 550)
    frame2 = _paste_bgr(frame2, "jiesanduiwu.png", 900, 550)
    s, _ = team.game_map.best_match_np(frame2, team.ZHAOHUI_ICON)
    assert s < team.ZHAOHUI_THRESHOLD


def test_qingli_calibration():
    """请离按钮模板校准回归（实测：目标 1.000 / 归队+离开+解散按钮帧
    0.821 / 噪声 0.115；请离与回归队伍按钮样式相近，0.9 恰好分开）。"""
    assert team.QINGLI_THRESHOLD >= 0.9
    frame = _paste_bgr(_bgr_frame(), "duiwumian_qingliduiwu.png", 600, 400)
    score, _ = team.game_map.best_match_np(frame, team.ZUDUI_QINGLI_ICON)
    assert score >= team.QINGLI_THRESHOLD
    frame2 = _paste_bgr(_bgr_frame(), "zudui_guidui.png", 600, 400)
    frame2 = _paste_bgr(frame2, "likaiduiwu.png", 600, 550)
    frame2 = _paste_bgr(frame2, "jiesanduiwu.png", 900, 550)
    s, _ = team.game_map.best_match_np(frame2, team.ZUDUI_QINGLI_ICON)
    assert s < team.QINGLI_THRESHOLD
    # 逆方向：请离按钮不得被归队模板命中（实测 0.773）
    s2, _ = team.game_map.best_match_np(frame, "zudui_guidui.png")
    assert s2 < team.GUIDUI_THRESHOLD


def test_away_member_indices_finds_badge_and_name(monkeypatch):
    """面板帧第 2 区域贴召回标记 → 识别出第 2 名暂离并 OCR 出昵称
    （真实卡内布局：召回标记顶部→称谓→名字→等级，取等级上方最近行）。"""
    frame = _paste_tpl(_frame(), "duiwumianban.png", 500, 260)
    _score, banner = team._find_banner_rect(frame)
    l, t, _r, _b = team._member_regions(banner)[1]
    frame = _paste_tpl(frame, "duiwumianban_zhaohui.png", l + 10, t + 10)
    monkeypatch.setattr(team.game_map, "ocr_region", lambda img, region: (
        [("招回", (l + 60, t + 1, l + 100, t + 24), 0.70),    # 标记（OCR 读「招回」）
         ("女魔", (l + 100, t + 1, l + 115, t + 39), 0.90),   # 竖排种族（右缘）
         ("丹青梵音礼敦煌", (l + 1, t + 191, l + 110, t + 212), 0.90),  # 称谓（不取）
         ("碧桃娘子", (l + 20, t + 212, l + 88, t + 237), 0.90),       # 名字
         ("飞升159级", (l + 18, t + 239, l + 91, t + 260), 0.90)],     # 等级（锚点）
        None))
    away, reason = team._away_member_indices(frame)
    assert reason is None
    assert away == [(1, "碧桃娘子", "女魔")]


def test_member_name_picks_row_above_level():
    """_member_name：取等级行上方最近的一行（名字），跳过称谓/召回
    标记/竖排种族（用户 2026-11-07 实测布局）。"""
    items = [
        ("招回", (60, 1, 100, 24), 0.70),       # 暂离标记（排除）
        ("女魔", (100, 1, 115, 39), 0.90),      # 竖排种族（宽<高排除）
        ("丹青梵音礼敦煌", (1, 191, 110, 212), 0.90),  # 称谓（名字上方，不取）
        ("名字甲", (20, 212, 88, 237), 0.90),   # 名字（等级上方最近）
        ("飞升159级", (18, 239, 91, 260), 0.90),  # 等级行（锚点）
    ]
    assert team._member_name(items) == "名字甲"


def test_member_name_skips_title_when_name_missing():
    """名字行漏检只剩称谓：间距超 _MEMBER_NAME_LEVEL_GAP，返回 None
    （不把称谓误当名字）。"""
    items = [
        ("丹青梵音礼敦煌", (1, 191, 110, 212), 0.90),
        ("飞升159级", (18, 239, 91, 260), 0.90),
    ]
    assert team._member_name(items) is None


def test_member_name_none_when_no_level_anchor():
    items = [
        ("名字甲", (20, 212, 88, 237), 0.90),
        ("女魔", (100, 1, 115, 39), 0.90),
    ]
    assert team._member_name(items) is None


def test_away_member_indices_empty_panel():
    frame = _paste_tpl(_frame(), "duiwumianban.png", 500, 260)
    away, reason = team._away_member_indices(frame)
    assert reason is None
    assert away == []


def test_away_member_indices_no_banner():
    away, reason = team._away_member_indices(_frame())
    assert away is None
    assert "标志" in reason


def test_scan_away_members(monkeypatch):
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "open_team_panel", lambda win: (True, "队伍面板已打开"))
    closed = []
    monkeypatch.setattr(team, "close_team_panel", lambda win: closed.append(1) or (True, ""))
    monkeypatch.setattr(team, "_away_member_indices",
                        lambda img: ([(1, "甲", "女人"), (2, "乙", "男鬼")], None))
    win = _SeqWin([_frame()])
    ok, away, info = team.scan_away_members(win)
    assert ok and away == [(1, "甲", "女人"), (2, "乙", "男鬼")]
    assert "第 2 名「甲」(女人)" in info and "第 3 名「乙」(男鬼)" in info
    assert closed == [1]


def test_scan_away_members_name_none_fallback(monkeypatch):
    """昵称 OCR 未识别（None）时降级只显示第 X 名。"""
    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_away_member_indices",
                        lambda img: ([(2, None, None)], None))
    win = _SeqWin([_frame()])
    ok, away, info = team.scan_away_members(win)
    assert ok and away == [(2, None, None)]
    assert "第 3 名" in info and "「" not in info


def test_scan_away_members_none_away(monkeypatch):
    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_away_member_indices", lambda img: ([], None))
    win = _SeqWin([_frame()])
    ok, away, info = team.scan_away_members(win)
    assert ok and away == []
    assert "无暂离成员" in info


def test_kick_away_member_click_and_verify(monkeypatch):
    """请离链路：点选第 2 名暂离成员卡中心 → 请离队伍按钮出现即点 →
    暂离标记减少 → 成功（日志带昵称）。"""
    clicked = []
    calls = {"n": 0}

    def _away(img):
        calls["n"] += 1
        return ([(1, "甲", "女人")], None) if calls["n"] == 1 else ([], None)

    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_away_member_indices", _away)
    monkeypatch.setattr(team, "_find_banner_rect",
                        lambda img: (1.0, (100, 100, 400, 200)))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8:
                        (200, 300) if icon == team.ZUDUI_QINGLI_ICON else None)
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_frame()])
    ok, info = team.kick_away_member(win)
    assert ok and "已请离暂离成员「甲」(女人)" in info
    rect = team._member_regions((100, 100, 400, 200))[1]
    assert clicked == [((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2),
                       (200, 300)]


def test_kick_away_member_no_away(monkeypatch):
    clicked = []
    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_away_member_indices", lambda img: ([], None))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    win = _SeqWin([_frame()])
    ok, info = team.kick_away_member(win)
    assert not ok and "无暂离成员" in info
    assert clicked == []


def test_kick_away_member_never_kick_leader_self(monkeypatch):
    """召回标记只出现在第 1 区域（队长自己）：不点、不请离。"""
    clicked = []
    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_away_member_indices",
                        lambda img: ([(0, "队长", None)], None))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    win = _SeqWin([_frame()])
    ok, info = team.kick_away_member(win)
    assert not ok and "不能请离自己" in info
    assert clicked == []


# ---- todo2 任务流程：暂离计时账本与超时连踢 ----

def test_track_away_adds_and_removes(monkeypatch):
    team.reset_away_tracking()
    team._track_away([(1, "甲", "女人"), (2, "乙", "男人")])
    assert set(team._away_seen) == {("女人", "甲"), ("男人", "乙")}
    assert team._away_seen[("女人", "甲")] <= time.time()
    team._track_away([(2, "乙", "男人")])     # 甲归队：移出账本
    assert set(team._away_seen) == {("男人", "乙")}
    team._track_away([(3, None, None)])      # 无名字不记账；乙也不在名单 → 移除
    assert team._away_seen == {}
    team.reset_away_tracking()


def test_track_away_fuzzy_name_exact_race(monkeypatch):
    """名字模糊+种族精确（用户 2026-11-07 定稿）：「碧桃娘子 女人」与
    「碧桃浪子 男人」不同人（名字 0.75 相似但种族不同，不合并计时）；
    「碧桃娘子 女人」与「碧桃娘了 女人」同一人（OCR 噪声，沿用原计时）。"""
    now = time.time()
    team.reset_away_tracking()
    team._track_away([(2, "碧桃娘子", "女人")])
    t0 = team._away_seen[("女人", "碧桃娘子")]
    assert t0 >= now
    # 名字 OCR 噪声（娘了）同种族、持续暂离 → 沿用原账目与计时
    team._track_away([(2, "碧桃娘了", "女人")])
    assert team._away_seen == {("女人", "碧桃娘子"): t0}
    # 名字模糊（比 0.75）+ 种族不同 → 不同人：娘子账目移除、浪子新计
    team._track_away([(2, "碧桃浪子", "男人")])
    assert set(team._away_seen) == {("男人", "碧桃浪子")}
    # 归队：账本清空
    team._track_away([])
    assert team._away_seen == {}
    team.reset_away_tracking()


def test_overdue_away_back_to_front(monkeypatch):
    """多人超时按区域序号从后往前（序号降序）；未超时/无名字/第 1 区域排除。"""
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女人", "甲"): now - 400,
                            ("男人", "乙"): now - 400,
                            ("女魔", "丙"): now - 10})
    away = [(0, "队长", "男鬼"), (1, "丙", "女魔"), (2, None, None),
            (3, "甲", "女人"), (4, "乙", "男人")]
    assert team._overdue_away(away) == [(4, "乙", "男人"), (3, "甲", "女人")]
    team.reset_away_tracking()


def test_auto_kick_overdue_kicks_back_to_front(monkeypatch):
    """4、5 名都超时：先请离 5（序号 4）再请离 4（序号 3），面板不关，
    每次请离后重扫；请离成功者移出账本。"""
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女人", "甲"): now - 400,
                            ("男人", "乙"): now - 400})
    scans = {"n": 0}
    kicked = []

    def _away(img):
        scans["n"] += 1
        if scans["n"] == 1:
            return [(3, "甲", "女人"), (4, "乙", "男人")], None
        if scans["n"] == 2:
            return [(3, "甲", "女人")], None
        return [], None

    def _kick_one(win, idx, name, race, away_before):
        kicked.append((idx, name))
        return True, "ok"

    monkeypatch.setattr(team, "_away_member_indices", _away)
    monkeypatch.setattr(team, "_kick_one", _kick_one)
    n, info = team._auto_kick_overdue(_SeqWin([_frame()]))
    assert n == 2
    assert kicked == [(4, "乙"), (3, "甲")]
    assert "「乙」" in info and "「甲」" in info
    assert team._away_seen == {}
    team.reset_away_tracking()


def test_auto_kick_overdue_only_expired_kicked(monkeypatch):
    """4 超时、5 未超时：只请离 4（序号 3）。"""
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女人", "甲"): now - 400,
                            ("男人", "乙"): now - 10})
    scans = {"n": 0}
    kicked = []

    def _away(img):
        scans["n"] += 1
        if scans["n"] == 1:
            return [(3, "甲", "女人"), (4, "乙", "男人")], None
        return [(4, "乙", "男人")], None

    monkeypatch.setattr(team, "_away_member_indices", _away)
    monkeypatch.setattr(team, "_kick_one",
                        lambda win, idx, name, race, away_before:
                        (kicked.append((idx, name)) or True, "ok"))
    n, _info = team._auto_kick_overdue(_SeqWin([_frame()]))
    assert n == 1
    assert kicked == [(3, "甲")]
    team.reset_away_tracking()


def test_auto_kick_overdue_stops_on_kick_failure(monkeypatch):
    """请离失败即停（识别问题不盲目连踢），失败信息带出。"""
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女人", "甲"): now - 400,
                            ("男人", "乙"): now - 400})
    scans = {"n": 0}
    kicked = []

    def _away(img):
        scans["n"] += 1
        return [(3, "甲", "女人"), (4, "乙", "男人")], None

    monkeypatch.setattr(team, "_away_member_indices", _away)
    monkeypatch.setattr(team, "_kick_one",
                        lambda win, idx, name, race, away_before:
                        (kicked.append((idx, name)) or False, "未出现请离按钮"))
    n, info = team._auto_kick_overdue(_SeqWin([_frame()]))
    assert n == 0
    assert kicked == [(4, "乙")]
    assert "未出现请离按钮" in info
    team.reset_away_tracking()


def test_auto_kick_overdue_tracks_without_kick(monkeypatch):
    """无人超时：仅记账不请离，返回 (0, "")。"""
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女魔", "丙"): now - 10})

    def _boom(*_a):
        raise AssertionError("不应请离")

    monkeypatch.setattr(team, "_away_member_indices",
                        lambda img: ([(1, "丙", "女魔")], None))
    monkeypatch.setattr(team, "_kick_one", _boom)
    n, info = team._auto_kick_overdue(_SeqWin([_frame()]))
    assert n == 0 and info == ""
    team.reset_away_tracking()


def test_kick_one_name_verify_requires_name_gone_and_count_down(monkeypatch):
    """名字校验保守原则：名字消失且总数减少才成功（OCR 漏检/他人新暂离
    不误报假成功——假成功会移出账本重计 5 分钟）。"""
    monkeypatch.setattr(team, "_find_banner_rect",
                        lambda img: (1.0, (100, 100, 400, 200)))
    monkeypatch.setattr(team, "human_click", lambda win, x, y: None)
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8:
                        (200, 300) if icon == team.ZUDUI_QINGLI_ICON else None)
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([_frame()])

    def _run(after_away, before):
        monkeypatch.setattr(team, "_away_member_indices",
                            lambda img: (after_away, None))
        return team._kick_one(win, 1, "甲", "女人", before)

    # 名字消失 + 总数减少 → 成功
    assert _run([], [(1, "甲", "女人")])[0]
    # 名字 OCR 漏检但总数未减 → 失败（保守，不误报假成功）
    assert not _run([(1, None, None)], [(1, "甲", "女人")])[0]
    # 同身份仍在（区域平移）→ 失败
    assert not _run([(2, "甲", "女人")], [(1, "甲", "女人")])[0]
    # 同名不同种族补位、总数未减 → 失败（种族精确排除）
    assert not _run([(1, "甲", "男人")], [(1, "甲", "女人")])[0]
    # 名字消失 + 总数减少（他人平移补位，种族不同）→ 成功
    assert _run([(2, "乙", "男人")], [(1, "甲", "女人"), (3, "乙", "男人")])[0]


def test_check_team_min_allow_kick_flag(monkeypatch):
    """allow_kick=True 才执行超时请离（诊断检查不踢人）。"""
    from PIL import Image as _Img
    called = []

    def _noop_kick(win):
        called.append(1)
        return 0, ""

    frame = _paste_tpl(_frame(), "duiwumianban.png", 500, 260)
    frame = _paste_tpl(frame, "jiesanduiwu.png", 600, 520)
    monkeypatch.setattr(team, "_is_panel_open", lambda win: True)
    monkeypatch.setattr(team, "close_team_panel", lambda win: (True, ""))
    monkeypatch.setattr(team, "_analyze_panel_frame",
                        lambda win, img: (True, [("男人", "飞升159")], 1))
    monkeypatch.setattr(team, "_auto_kick_overdue", _noop_kick)
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = _SeqWin([frame])
    status, _info, _members = team.check_team_min(win, min_members=None)
    assert status == "ready" and called == []
    status, _info, _members = team.check_team_min(win, min_members=None,
                                                  allow_kick=True)
    assert status == "ready" and called == [1]


def test_auto_kick_overdue_excludes_matched_tabs(monkeypatch):
    """todo3 组号：匹配到队员 Tab 的暂离成员不参与超时请离（归队优先），
    未匹配的（路人）照常请离。"""
    import src.tab_group as tg
    now = time.time()
    team.reset_away_tracking()
    team._away_seen.update({("女人", "甲"): now - 400,
                            ("男人", "乙"): now - 400})
    scans = {"n": 0}
    kicked = []

    def _away(img):
        scans["n"] += 1
        if scans["n"] == 1:
            return [(3, "甲", "女人"), (4, "乙", "男人")], None
        return [(4, "乙", "男人")], None

    monkeypatch.setattr(team, "_away_member_indices", _away)
    monkeypatch.setattr(tg, "active", lambda: True)
    monkeypatch.setattr(tg, "member_rejoin_pass",
                        lambda win, names: (0, "队员归队未成功", {"甲"}))
    monkeypatch.setattr(team, "_kick_one",
                        lambda win, idx, name, race, away_before:
                        (kicked.append((idx, name)) or True, "ok"))
    n, _info = team._auto_kick_overdue(_SeqWin([_frame()]))
    assert n == 1
    assert kicked == [(4, "乙")]   # 甲是自家 Tab：归队 pass 处理，不请离
    team.reset_away_tracking()


# ---- 申请加入面板申请人行识别（_apply_rows）区域锚定（2026-11-07 10:25 教训） ----
# 「任务」场景文案被当申请人点击、误开领取对话框卡死流程。修复后扫描区
# 严格锚定面板标志模板匹配框：x ∈ [l, r]、申请人行仅取 y ∈ [b, b+300]，
# 超出区域文字一律视为误识别；表头校验条带 b-25..b 覆盖新模板（356x59）
# 内嵌表头行（名字/等级/族群/性别，模板内 y41-58）。

class _FakeFrame:
    size = (1378, 831)


def _apply_rows_with(monkeypatch, box, items):
    """替换 team.game_map.ocr_region 返回指定 items，记录实际请求区域。"""
    calls = []

    def stub(img, region):
        calls.append(region)
        return items, None

    monkeypatch.setattr(team.game_map, "ocr_region", stub)
    rows = team._apply_rows(_FakeFrame(), box)
    return calls, rows


def _apply_items_from_incident_screenshot():
    """2026-11-07 申请加入面板实测 OCR 行（模板框 [116,86,472,145]，1378x831 帧）。

    表头行内嵌模板底边（y127-147），申请人行在模板正下方（y158-178）；
    场景干扰：NPC 对话 x17-112（区域外）、面板按钮行 y445-473
    （b+300=445 之外）。
    """
    return [
        ("族群性别", (354, 128, 436, 147), 0.89),          # 表头（条带内）
        ("夜咏青灯@老北归", (177, 159, 271, 176), 0.99),    # 申请人名字
        ("3转120级", (302, 161, 358, 175), 1.0),           # 申请人等级
        ("魔", (371, 161, 386, 175), 0.97),                # 族群
        ("女", (413, 161, 426, 175), 1.0),                 # 性别
    ]


def test_apply_rows_region_anchored_to_template_box(monkeypatch):
    """扫描区域必须是模板框 x 全宽、底边向上 25 表头条带、向下 300。"""
    calls, _rows = _apply_rows_with(monkeypatch, [116, 86, 472, 145],
                                    _apply_items_from_incident_screenshot())
    assert calls == [(116, 120, 472, 445)]


def test_apply_rows_detects_applicant_below_template(monkeypatch):
    _, rows = _apply_rows_with(monkeypatch, [116, 86, 472, 145],
                               _apply_items_from_incident_screenshot())
    assert len(rows) == 1
    r = rows[0]
    assert "老北归" in r["name"]
    assert tuple(r["rect"]) == (177, 159, 271, 176)
    assert "3转120级" in r["id"]


def test_apply_rows_no_header_returns_empty(monkeypatch):
    """表头校验：区域内无表头关键字 → 面板未真正打开 → 空列表。"""
    items = [
        ("夜咏青灯@老北归", (177, 159, 271, 176), 0.99),
        ("3转120级", (302, 161, 358, 175), 1.0),
    ]
    _calls, rows = _apply_rows_with(monkeypatch, [116, 86, 472, 145], items)
    assert rows == []


def test_apply_rows_row_above_template_bottom_excluded(monkeypatch):
    """条带内非表头文字（表头 OCR 误读）不得进入行候选。"""
    items = [
        ("族群性别", (354, 128, 436, 147), 0.89),
        ("申请加入", (242, 95, 335, 126), 1.0),   # 标题误读进条带
        ("夜咏青灯@老北归", (177, 159, 271, 176), 0.99),
        ("3转120级", (302, 161, 358, 175), 1.0),
    ]
    _, rows = _apply_rows_with(monkeypatch, [116, 86, 472, 145], items)
    assert len(rows) == 1


def test_apply_rows_row_without_level_excluded(monkeypatch):
    """每行必须带等级：场景按钮文字（如「任务」）不是申请人。"""
    items = [
        ("族群性别", (354, 128, 436, 147), 0.89),
        ("任务", (177, 159, 271, 176), 0.99),   # 无等级行
    ]
    _, rows = _apply_rows_with(monkeypatch, [116, 86, 472, 145], items)
    assert rows == []


def test_apply_rows_region_clamped_to_frame(monkeypatch):
    """模板框超出帧边界时区域截断，不产生负坐标。"""
    calls, _rows = _apply_rows_with(monkeypatch, [-50, 100, 300, 159],
                                    _apply_items_from_incident_screenshot())
    assert calls == [(0, 134, 300, 459)]


# ---- 诊断-测试申请加入（verify_apply_panel，只读） ----

class _FrameWin:
    def capture(self, force_activate=False):
        return Image.fromarray(np.zeros((831, 1378, 3), dtype=np.uint8))


class _FakeMatch:
    def __init__(self, confidence, rect):
        self.confidence = confidence
        self.rect = rect


def test_verify_apply_panel_reports_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team.FindImages, "find_all_template",
                        lambda *a, **k: [_FakeMatch(0.95, [116, 86, 472, 145])])

    def _rows(img, box):
        calls.append(box)
        return [{"yc": 168, "name": "夜咏青灯@老北归",
                 "id": "夜咏青灯老北归3转120级", "rect": [177, 159, 271, 176]}]

    monkeypatch.setattr(team, "_apply_rows", _rows)
    ok, info = team.verify_apply_panel(_FrameWin())
    assert ok and "夜咏青灯" in info and "1 名" in info
    assert calls == [[116, 86, 472, 145]]


def test_verify_apply_panel_below_threshold_no_rows_scan(monkeypatch):
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team.FindImages, "find_all_template",
                        lambda *a, **k: [_FakeMatch(0.62, [116, 86, 472, 145])])

    def _boom(img, box):
        raise AssertionError("分数未达阈值不得进入行识别")

    monkeypatch.setattr(team, "_apply_rows", _boom)
    ok, info = team.verify_apply_panel(_FrameWin())
    assert not ok and "0.62" in info and "未达阈值" in info


def test_verify_apply_panel_no_applicant_rows(monkeypatch):
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team.FindImages, "find_all_template",
                        lambda *a, **k: [_FakeMatch(0.95, [116, 86, 472, 145])])
    monkeypatch.setattr(team, "_apply_rows", lambda img, box: [])
    ok, info = team.verify_apply_panel(_FrameWin())
    assert ok and "未识别出申请人行" in info


def test_verify_apply_panel_no_match(monkeypatch):
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team.FindImages, "find_all_template",
                        lambda *a, **k: [])
    ok, info = team.verify_apply_panel(_FrameWin())
    assert not ok and "请打开申请加入面板" in info


# ---- 补人清空与一次补满缺口（2026-11-07 用户结论：缺 N 补 N、补满/全失败清空） ----

class _FakeTime:
    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _appl(uid, yc=168):
    return {"yc": yc, "name": uid, "id": uid, "rect": [177, yc - 10, 227, yc + 10]}


def _run_fill_loop(monkeypatch, max_allow, rows_seq, clear_log, fail_stub):
    """跑 _fill_team_loop：面板恒开、允许按钮恒命中、失败提示走 fail_stub。"""
    monkeypatch.setattr(team, "time", _FakeTime())
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team, "_apply_panel_flag", lambda img: [116, 86, 472, 145])
    it = iter(rows_seq)
    monkeypatch.setattr(team, "_apply_rows", lambda img, box: next(it))
    monkeypatch.setattr(team.game_map, "find_template", lambda *a, **k: (330, 458))
    monkeypatch.setattr(team.game_map, "ocr_region", lambda *a, **k: ([], None))
    monkeypatch.setattr(team, "_dialog_text_present", fail_stub)
    monkeypatch.setattr(team, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(team, "_clear_apply_list",
                        lambda win, rows: clear_log.append(list(rows)) or "清空OK")
    win = _SeqWin([_frame()])
    ok, info = team._fill_team_loop(win, 5, max_allow)
    return ok, info, clear_log


def test_fill_loop_two_success_clears_remaining(monkeypatch):
    """缺 2 补 2：连续允许 2 人后清空剩余申请。"""
    rows_seq = [[_appl("甲"), _appl("乙"), _appl("丙")],   # 选甲
                [_appl("乙"), _appl("丙")],                # 甲成功离开
                [_appl("乙"), _appl("丙")],                # 选乙
                [_appl("丙")]]                             # 乙成功离开
    clear_log = []
    ok, info, _ = _run_fill_loop(monkeypatch, 2, rows_seq, clear_log,
                                 lambda items, txt: False)
    assert ok and "已允许 2 名申请人" in info and "清空OK" in info
    assert [r["id"] for r in clear_log[0]] == ["丙"]


def test_fill_loop_all_fail_clears_list(monkeypatch):
    """缺 1、3 人全失败：逐人换试后清空申请列表。"""
    rows_seq = [[_appl("甲"), _appl("乙"), _appl("丙")]] * 4
    clear_log = []
    ok, info, _ = _run_fill_loop(monkeypatch, 1, rows_seq, clear_log,
                                 lambda items, txt: True)
    assert not ok and "申请列表为空或全部失败" in info and "清空OK" in info
    assert [r["id"] for r in clear_log[0]] == ["甲", "乙", "丙"]


def test_fill_loop_one_success_one_fail_then_clear(monkeypatch):
    """缺 2、2 人申请：甲成功乙失败 → 乙为残留，清空后返回。"""
    rows_seq = [[_appl("甲"), _appl("乙")],   # 选甲（成功）
                [_appl("乙")],                # 甲成功校验
                [_appl("乙")],                # 选乙（失败）
                [_appl("乙")]]                # 无未失败可选 → 清空
    clear_log = []
    checks = {"n": 0}

    def fail_stub(items, txt):
        checks["n"] += 1
        return checks["n"] > 5   # 甲允许的 5 次窗口检查均无失败提示，乙的第 1 次即失败

    ok, info, _ = _run_fill_loop(monkeypatch, 2, rows_seq, clear_log, fail_stub)
    assert ok and "已允许 1 名申请人后无可选申请人" in info and "清空OK" in info
    assert [r["id"] for r in clear_log[0]] == ["乙"]


def test_clear_apply_list_empty_rows_no_click(monkeypatch):
    clicks = []
    monkeypatch.setattr(team.game_map, "find_template",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("列表空不应查模板")))
    monkeypatch.setattr(team, "human_click", lambda *a, **k: clicks.append(a))
    assert team._clear_apply_list(object(), []) == "无残留申请"
    assert clicks == []


def test_clear_apply_list_clicks_button(monkeypatch):
    clicks = []
    monkeypatch.setattr(team.game_map, "find_template", lambda *a, **k: (330, 458))
    monkeypatch.setattr(team, "human_click", lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(team.time, "sleep", lambda s: None)
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    assert team._clear_apply_list(object(), [_appl("甲")]) == "残留申请已清空"
    assert clicks == [(330, 458)]


def test_clear_apply_list_button_missing(monkeypatch):
    clicks = []
    monkeypatch.setattr(team.game_map, "find_template", lambda *a, **k: None)
    monkeypatch.setattr(team, "human_click", lambda *a, **k: clicks.append(a))
    info = team._clear_apply_list(object(), [_appl("甲")])
    assert "未找到清空按钮" in info and clicks == []


# ---- 启动清场（clear_pending_apply）与队长判定申请态兜底（2026-11-07 22:03 教训） ----

def _run_clear_pending(monkeypatch, frames, tpl_seq, qingkong_pt=(330, 458)):
    """跑 clear_pending_apply：模板匹配结果按 tpl_seq 逐次消费。"""
    calls = {"tpl": [], "click": [], "leader": [], "close": []}
    monkeypatch.setattr(team, "log", lambda *a, **k: None)
    monkeypatch.setattr(team.time, "sleep", lambda s: None)
    monkeypatch.setattr(team, "record_leader_status",
                        lambda is_leader: calls["leader"].append(is_leader))
    monkeypatch.setattr(team, "human_click",
                        lambda win, x, y: calls["click"].append((x, y)))
    monkeypatch.setattr(team, "_close_apply_panel",
                        lambda win: calls["close"].append(1) or (True, "已关闭"))
    monkeypatch.setattr(team.game_map, "find_template",
                        lambda *a, **k: qingkong_pt)
    seq = list(tpl_seq)

    def fake_find_in(img, icon, threshold=0.8, region=None):
        calls["tpl"].append(icon)
        return seq.pop(0)

    monkeypatch.setattr(team.game_map, "find_template_in", fake_find_in)
    win = _SeqWin(frames)
    hit, info = team.clear_pending_apply(win)
    return hit, info, calls


def test_clear_pending_apply_no_pending_state(monkeypatch):
    hit, info, calls = _run_clear_pending(monkeypatch, [_frame()], [None, None])
    assert not hit and info == "无申请待处理态"
    assert calls["click"] == [] and calls["close"] == []


def test_clear_pending_apply_item2_click_clear_close(monkeypatch):
    """item2 命中 → 点按钮开面板 → 点清空 → 关面板，且记录确定性队长。"""
    hit, info, calls = _run_clear_pending(
        monkeypatch, [_frame()],
        [None, (100, 100), None, None, (300, 300)])  # 面板检查×2 未开 → 第 3 帧开
    assert hit and info == "已清空申请列表并关闭面板"
    assert calls["click"] == [(100, 100), (330, 458)]
    assert calls["leader"] == [True]
    assert calls["close"] == [1]


def test_clear_pending_apply_panel_already_open(monkeypatch):
    """申请加入面板残留打开：不点组队按钮，直接清空+关闭。"""
    hit, info, calls = _run_clear_pending(monkeypatch, [_frame()], [(300, 300)])
    assert hit and info == "已清空申请列表并关闭面板"
    assert calls["click"] == [(330, 458)] and calls["close"] == [1]


def test_clear_pending_apply_panel_never_opens(monkeypatch):
    """点了 item2 但面板未打开：不点清空，报告未清空。"""
    hit, info, calls = _run_clear_pending(monkeypatch, [_frame()],
                                          [None, (100, 100)] + [None] * 6)
    assert hit and "面板未打开" in info
    assert calls["click"] == [(100, 100)] and calls["close"] == []


def test_clear_pending_apply_qingkong_missing(monkeypatch):
    """清空按钮未命中：仅关面板，不阻断。"""
    hit, info, calls = _run_clear_pending(monkeypatch, [_frame()],
                                          [None, (100, 100), (300, 300)],
                                          qingkong_pt=None)
    assert hit and "清空按钮未命中" in info
    assert calls["click"] == [(100, 100)] and calls["close"] == [1]


def test_classify_leader_pending_apply_is_leader(monkeypatch):
    """面板打不开但 item2 在场：确定性队长，不得按「队长无法判定」停任务。"""
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "open_team_panel",
                        lambda win, **kw: (False, "组队菜单未打开"))
    monkeypatch.setattr(team, "record_leader_status", lambda is_leader: None)
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8, region=None: (100, 100)
                        if icon == team.ZUDUI_ITEM2_ICON else None)
    kind, info = team.classify_leader_with_panel(_SeqWin([_frame()]))
    assert kind == "leader" and "申请待处理态" in info


def test_classify_leader_panel_fail_no_evidence_not_leader(monkeypatch):
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "open_team_panel",
                        lambda win, **kw: (False, "未组队（菜单中无「队伍信息」）"))
    monkeypatch.setattr(team, "record_leader_status", lambda is_leader: None)
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8, region=None: None)
    kind, info = team.classify_leader_with_panel(_SeqWin([_frame()]))
    assert kind == "not_leader" and "未组队" in info


def test_check_team_min_pending_apply_fallback_after_menu_fail(monkeypatch):
    """open_team_panel 因申请态打不开时重查 2 帧命中证据 → wait 确定性
    队长，不得返回「队长无法判定」（2026-11-07 日志 22:17 教训：item2
    动效抖动首帧漏检、菜单路径帧命中）。"""
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "open_team_panel",
                        lambda win, **kw: (False, "组队菜单未打开"))
    monkeypatch.setattr(team, "record_leader_status", lambda is_leader: None)
    calls = {"n": 0}

    def pending(img):
        calls["n"] += 1
        return calls["n"] >= 2  # 首帧漏检，兜底第 1 帧命中

    monkeypatch.setattr(team, "has_pending_apply", pending)
    win = _SeqWin([_frame()])
    status, info, _members = team.check_team_min(win, min_members=None)
    assert status == "wait" and "确定性队长" in info
    assert calls["n"] == 2  # 首帧漏检 + 兜底第 1 帧命中即返回


def test_check_team_min_menu_fail_no_pending_evidence(monkeypatch):
    """菜单打不开且 3 帧都无申请证据：保持「队长无法判定」瞬态语义。"""
    monkeypatch.setattr(team, "_is_panel_open", lambda win: False)
    monkeypatch.setattr(team, "open_team_panel",
                        lambda win, **kw: (False, "组队菜单未打开"))
    monkeypatch.setattr(team, "record_leader_status", lambda is_leader: None)
    monkeypatch.setattr(team, "has_pending_apply", lambda img: False)
    win = _SeqWin([_frame()])
    status, info, _members = team.check_team_min(win, min_members=None)
    assert status == "not_leader" and "队长无法判定" in info


# ---- 组队按钮搜索条带（2026-11-07 用户标定 900,737-1378,831） ----

def test_find_template_in_region_cropped():
    """条带内命中返回全图坐标；条带外不搜（None）。"""
    from src import map as game_map
    frame = _paste_tpl(_frame(), "zudui_item2.png", 1000, 750)
    pt = game_map.find_template_in(frame, team.ZUDUI_ITEM2_ICON,
                                   team.ZUDUI_ITEM2_THRESHOLD,
                                   region=team.ZUDUI_ITEM_REGION)
    assert pt is not None and pt[0] > 1000 and pt[1] > 750
    frame2 = _paste_tpl(_frame(), "zudui_item2.png", 200, 100)  # 带外
    assert game_map.find_template_in(frame2, team.ZUDUI_ITEM2_ICON,
                                     team.ZUDUI_ITEM2_THRESHOLD,
                                     region=team.ZUDUI_ITEM_REGION) is None


def test_has_pending_apply_uses_item_region(monkeypatch):
    """has_pending_apply 的 item2 查询限定右下角条带。"""
    calls = []
    monkeypatch.setattr(team.game_map, "find_template_in",
                        lambda img, icon, threshold=0.8, region=None:
                        calls.append(region) or None)
    monkeypatch.setattr(team, "_apply_panel_flag", lambda img: None)
    assert team.has_pending_apply(_frame()) is False
    assert calls == [team.ZUDUI_ITEM_REGION]


# ---- 暂离身份匹配易混字符归一化（2026-11-09） ----


def test_away_identity_match_confusable_names():
    """特殊字符名 OCR 抖动（〃→”、ㄨ→×、ˉ→一）不拆成两个身份。"""
    assert team._away_identity_match("”忆×嚣魂一", "男鬼",
                                     "〃忆ㄨ嚣魂ˉ", "男鬼")
    assert not team._away_identity_match("”忆×嚣魂一", "男鬼",
                                         "〃忆ㄨ嚣魂ˉ", "女人")
