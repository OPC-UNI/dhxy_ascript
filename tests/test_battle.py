"""battle.py：技能坐标解析、文案规范化、战斗帧判定、巫医选项定位。"""
import cv2
import numpy as np
from PIL import Image
from ascript.windows.system import R

from src import battle

from .conftest import make_bgr_frame, paste_template


def _make_noisy_pil(seed, w=1378, h=831):
    """合成 PIL 帧（随机噪声，与真实截帧同尺寸，差分必超门限）。"""
    r = np.random.default_rng(seed)
    return Image.fromarray(r.integers(0, 256, (h, w, 3), dtype=np.uint8))


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


# ---- 巫医空闲 OCR 降级（2026-11-04：站立场景差分门恒击穿，空检降级限频） ----

def test_wuyi_ocr_gate_not_degraded_passes(monkeypatch):
    monkeypatch.setattr(battle, "_wuyi_degraded", False)
    assert battle._wuyi_ocr_gate(100.0, idle=True)


def test_wuyi_ocr_gate_window_not_throttled(monkeypatch):
    """窗口期检查（idle=False）不受降级限频。"""
    monkeypatch.setattr(battle, "_wuyi_degraded", True)
    monkeypatch.setattr(battle, "_wuyi_last_ocr", 100.0)
    assert battle._wuyi_ocr_gate(100.5, idle=False)


def test_wuyi_ocr_gate_degraded_idle_throttled(monkeypatch):
    """降级态空闲检查：兜底间隔内跳过，达间隔放行。"""
    monkeypatch.setattr(battle, "_wuyi_degraded", True)
    monkeypatch.setattr(battle, "_wuyi_last_ocr", 100.0)
    assert not battle._wuyi_ocr_gate(100.0 + battle.WUYI_DEGRADE_RECHECK - 0.1,
                                     idle=True)
    assert battle._wuyi_ocr_gate(100.0 + battle.WUYI_DEGRADE_RECHECK, idle=True)


def test_wuyi_noise_streak_degrades_and_recovers(monkeypatch):
    """空闲连续空检 → 降级（兜底间隔内跳过 OCR）→ 命中文案恢复。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    battle._wuyi_last_ocr = 0.0
    battle._wuyi_option_missing_warned = False
    clock = [100.0]
    monkeypatch.setattr(battle.time, "time", lambda: clock[0])
    ocr_n = [0]
    items_slot = [[]]
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: (ocr_n.__setitem__(0, ocr_n[0] + 1)
                                              or (list(items_slot[0]), None)))
    # 命中后选项定位与点击打桩：只验证降级恢复，不实际点击
    monkeypatch.setattr(battle, "_find_wuyi_option", lambda items, img_np: None)
    seed = [0]

    def check():
        seed[0] += 1
        return battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(seed[0]),
                                          idle=True)

    for _ in range(battle.WUYI_DEGRADE_STREAK):  # 连续空检喂满降级
        assert check() is False
    assert ocr_n[0] == battle.WUYI_DEGRADE_STREAK
    assert battle._wuyi_degraded is True
    clock[0] += 0.5
    assert check() is False                  # 降级态兜底间隔内：跳过 OCR
    assert ocr_n[0] == battle.WUYI_DEGRADE_STREAK
    clock[0] += battle.WUYI_DEGRADE_RECHECK  # 达兜底间隔：重查一次
    assert check() is False
    assert ocr_n[0] == battle.WUYI_DEGRADE_STREAK + 1
    items_slot[0] = [("我是超级巫医，可以帮你医治召唤兽",
                      [500, 400, 800, 440], 0.96)]
    clock[0] += battle.WUYI_DEGRADE_RECHECK  # 降级态：达兜底间隔才 OCR
    assert check() is False                  # 命中文案：恢复快节奏
    assert battle._wuyi_degraded is False
    assert battle._wuyi_noise_streak == 0


def test_handle_wuyi_dialog_reuses_frame(monkeypatch):
    """img 传入时复用轮询帧：不二次截窗（win 无 capture 属性即证）。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_option_missing_warned = False
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    assert battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(1)) is False


def test_handle_hezi_reuses_frame(monkeypatch):
    """bgr 传入时复用轮询帧：开关全开且无盒子时不截窗。"""
    monkeypatch.setattr(battle, "current_task_mode", lambda: "修罗")
    monkeypatch.setattr(battle, "load_xiuluo_hezi", lambda: True)
    monkeypatch.setattr(battle.team, "known_leader", lambda: True)
    monkeypatch.setattr(battle.game_map, "match_frame_np", lambda *a, **k: None)
    assert battle._handle_hezi(object(), bgr=make_bgr_frame()) is False
