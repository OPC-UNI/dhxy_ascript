"""battle.py：技能坐标解析、文案规范化、战斗帧判定、巫医选项定位。"""
import cv2
import numpy as np
from ascript.windows.system import R

from src import battle

from .conftest import make_bgr_frame, paste_template


def _tpl(icon):
    return cv2.imread(R.img(icon), cv2.IMREAD_COLOR)


# ---- 纯逻辑 ----

def test_char_skill_pos():
    assert battle.char_skill_pos("技能1-1") == battle.SKILL_GRID_ORIGIN
    assert battle.char_skill_pos("技能3-5") == (
        battle.SKILL_GRID_ORIGIN[0] + battle.SKILL_GRID_STEP[0] * 2,
        battle.SKILL_GRID_ORIGIN[1] + battle.SKILL_GRID_STEP[1] * 4)
    assert battle.char_skill_pos("技能0-1") is None
    assert battle.char_skill_pos("技能1-9") is None
    assert battle.char_skill_pos("自动") is None


def test_norm_text():
    assert battle._norm_text("看打！，「」") == "看打"
    assert battle._norm_text("全部医治和修复") == "全部医治和修复"


# ---- 战斗帧判定（真实模板 + 条带语义） ----

def test_is_in_battle_frame_bar_hit(monkeypatch):
    """操作条按钮出现在底部条带内 → 判为战斗。"""
    monkeypatch.setattr(battle, "_last_full_check", 0.0)
    tpl = _tpl("fashu.png")
    frame = make_bgr_frame()
    l, t, r, b = battle._bar_band(frame)
    frame = paste_template(frame, tpl, (l + r) // 2, (t + b) // 2)
    assert battle.is_in_battle_frame(frame)


def test_is_in_battle_frame_plain(monkeypatch):
    monkeypatch.setattr(battle, "_last_full_check", 0.0)
    assert not battle.is_in_battle_frame(make_bgr_frame())


def test_is_in_battle_frame_banner_hit(monkeypatch):
    """回合横幅命中 → 判为战斗（操作条隐藏的回合间隙）。"""
    monkeypatch.setattr(battle, "_last_full_check", 0.0)
    tpl = _tpl("round_banner.png")
    frame = make_bgr_frame()
    l, t, r, b = battle.BANNER_BAND
    frame = paste_template(frame, tpl, (l + r) // 2, (t + b) // 2)
    assert battle.is_in_battle_frame(frame)


# ---- 巫医选项定位 ----

def test_find_wuyi_option():
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    # 选项绿字块（x≥PANEL_MAX_X 的对话框区域）
    img_np[400:440, 500:800] = (0, 186, 0)
    items = [("全部医治和修复", [500, 400, 800, 440], 0.96)]
    cx, cy = battle._find_wuyi_option(items, img_np)
    assert 500 <= cx <= 800 and 400 <= cy <= 440


def test_find_wuyi_option_rejects_panel_side():
    """追踪面板侧（x<PANEL_MAX_X）的同文案行不得被点击。"""
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    img_np[400:440, 10:300] = (0, 186, 0)
    items = [("全部医治和修复", [10, 400, 300, 440], 0.96)]
    assert battle._find_wuyi_option(items, img_np) is None


# ---- 巫医检查节奏 ----

def test_wuyi_check_interval_post_battle_window():
    """战后高发窗口内 → 2s 高频。"""
    now = 100.0
    assert battle._wuyi_check_interval(now, now - 5.0, 0.0) \
        == battle.WUYI_CHECK_INTERVAL


def test_wuyi_check_interval_expected_window():
    """主流程点 NPC 后预期窗口内 → 2s 高频（战后窗口已过）。"""
    now = 100.0
    assert battle._wuyi_check_interval(now, now - 20.0, now - 5.0) \
        == battle.WUYI_CHECK_INTERVAL


def test_wuyi_check_interval_idle():
    """两窗口之外（含预期窗口过期）→ 10s 兜底。"""
    now = 100.0
    assert battle._wuyi_check_interval(now, now - 20.0, 0.0) \
        == battle.WUYI_IDLE_CHECK_INTERVAL
    assert battle._wuyi_check_interval(now, now - 20.0, now - 16.0) \
        == battle.WUYI_IDLE_CHECK_INTERVAL


def test_wuyi_check_interval_fast_idle():
    """非队长保持模式（wuyi_fast_idle）→ 空闲节奏提升到 2s。"""
    old = battle.wuyi_fast_idle
    battle.wuyi_fast_idle = True
    try:
        now = 100.0
        assert battle._wuyi_check_interval(now, now - 20.0, 0.0) \
            == battle.WUYI_CHECK_INTERVAL
    finally:
        battle.wuyi_fast_idle = old
