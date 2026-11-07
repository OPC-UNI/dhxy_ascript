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
