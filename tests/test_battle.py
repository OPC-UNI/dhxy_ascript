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
    battle._wuyi_pos_by_key.clear()  # 无缓存走识别路径
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


def test_wuyi_click_degrades_immediately(monkeypatch):
    """点击「全部医治和修复」成功 → 立即降级（无需再空检 4 次）。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_option_missing_warned = False
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    battle._wuyi_pos_by_key.clear()  # 无缓存走识别路径
    clicks = []
    monkeypatch.setattr(battle, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(battle, "move_away", lambda win: None)
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: (
                            [("我是超级巫医，可以帮你医治召唤兽",
                              [500, 300, 900, 340], 0.96)], None))
    # 选项定位打桩（真实定位另有 test_find_wuyi_option 覆盖）
    monkeypatch.setattr(battle, "_find_wuyi_option",
                        lambda items, img_np: (650, 420))
    assert battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(1)) is True
    assert clicks == [(650, 420)]           # 选项中心被点击
    assert battle._wuyi_degraded is True    # 点击成功即降级
    assert battle._wuyi_noise_streak == battle.WUYI_DEGRADE_STREAK


def _wuyi_img_np(green_band):
    """构造巫医 OCR 帧：对话框区外置黑、指定条带内放亮绿选项文字。"""
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    l, t, r, b = green_band
    img_np[t:b, l:r] = (0, 186, 0)  # RGB 亮绿（与任务绿字同款）
    return img_np


WUYI_TEXT_XIULUO = "我是超级巫医，可以帮你医治召唤兽"
WUYI_TEXT_WUBEI = "召唤兽和坐骑都是上天赐给我们的好朋友，要好好对待它们，会保佑你的。"


def test_wuyi_click_uses_cached_position(monkeypatch):
    """高性能直点：同弹窗缓存点条带内有绿字 → 直接点击，不重新识别选项。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_option_missing_warned = False
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    battle._wuyi_pos_by_key.clear()
    battle.record_wuyi_option_pos((534, 408), WUYI_TEXT_XIULUO)
    clicks = []
    monkeypatch.setattr(battle, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(battle, "move_away", lambda win: None)
    img_np = _wuyi_img_np((494, 398, 574, 418))  # 缓存点周围绿字
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: (
                            [(WUYI_TEXT_XIULUO, [500, 300, 900, 340], 0.96)],
                            img_np))

    def _boom(*a, **k):
        raise AssertionError("有缓存不应再识别选项")

    monkeypatch.setattr(battle, "_find_wuyi_option", _boom)
    assert battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(1)) is True
    assert clicks == [(534, 408)]


def test_wuyi_click_cross_dialog_cache_not_reused(monkeypatch):
    """不同弹窗（不同文案）缓存互不复用：五倍弹窗不得直点修罗缓存坐标
    （2026-11-05 14:01:35 五倍直点修罗 (534,410) 教训）。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_option_missing_warned = False
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    battle._wuyi_pos_by_key.clear()
    battle.record_wuyi_option_pos((534, 410), WUYI_TEXT_XIULUO)  # 修罗弹窗缓存
    clicks = []
    monkeypatch.setattr(battle, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(battle, "move_away", lambda win: None)
    img_np = _wuyi_img_np((610, 410, 690, 430))  # 五倍选项条带（另一位置）
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: (
                            [(WUYI_TEXT_WUBEI, [500, 300, 900, 340], 0.96)],
                            img_np))
    monkeypatch.setattr(battle, "_find_wuyi_option",
                        lambda items, np_img: (650, 420))
    assert battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(1)) is True
    assert clicks == [(650, 420)]               # 走识别流程，未复用修罗坐标
    assert battle.last_wuyi_option_pos == (650, 420)  # 全局最新坐标已刷新
    # 分键缓存：五倍键已建立、修罗键未被覆盖
    assert battle._wuyi_pos_by_key[battle._wuyi_cache_key(WUYI_TEXT_WUBEI)] \
        == (650, 420)
    assert battle._wuyi_pos_by_key[battle._wuyi_cache_key(WUYI_TEXT_XIULUO)] \
        == (534, 410)


def test_wuyi_cache_keyed_by_text_and_mode(monkeypatch):
    """缓存按（任务模式, 命中文案）分键：模式或文案不同均不得命中。"""
    battle._wuyi_pos_by_key.clear()
    monkeypatch.setattr(battle, "current_task_mode", lambda: "修罗")
    battle.record_wuyi_option_pos((534, 408), WUYI_TEXT_XIULUO)
    monkeypatch.setattr(battle, "current_task_mode", lambda: "五倍")
    assert battle._wuyi_cached_pt(
        _wuyi_img_np((494, 398, 574, 418)), WUYI_TEXT_XIULUO) is None  # 模式不同
    assert battle._wuyi_cached_pt(
        _wuyi_img_np((494, 398, 574, 418)), WUYI_TEXT_WUBEI) is None    # 文案不同
    battle.record_wuyi_option_pos((650, 420), WUYI_TEXT_WUBEI)
    assert battle._wuyi_cached_pt(
        _wuyi_img_np((610, 410, 690, 430)), WUYI_TEXT_WUBEI) == (650, 420)
    monkeypatch.setattr(battle, "current_task_mode", lambda: "修罗")
    assert battle._wuyi_cached_pt(
        _wuyi_img_np((494, 398, 574, 418)), WUYI_TEXT_XIULUO) == (534, 408)


def test_record_without_text_does_not_pollute_keyed_cache(monkeypatch):
    """无文案上下文的记录（旧调用方）只更新全局最新坐标，不进分键缓存。"""
    battle._wuyi_pos_by_key.clear()
    monkeypatch.setattr(battle, "current_task_mode", lambda: "修罗")
    battle.record_wuyi_option_pos((123, 456))
    assert battle.last_wuyi_option_pos == (123, 456)
    assert battle._wuyi_pos_by_key == {}


def test_wuyi_click_cache_green_check_still_guards(monkeypatch):
    """同弹窗缓存但条带无绿字（布局漂移/误场景）→ 走识别流程。"""
    battle._wuyi_prev_roi = None
    battle._wuyi_option_missing_warned = False
    battle._wuyi_degraded = False
    battle._wuyi_noise_streak = 0
    battle._wuyi_pos_by_key.clear()
    battle.record_wuyi_option_pos((534, 408), WUYI_TEXT_XIULUO)
    clicks = []
    monkeypatch.setattr(battle, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(battle, "move_away", lambda win: None)
    img_np = _wuyi_img_np((610, 410, 690, 430))  # 绿字不在缓存点条带
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda img, regions: (
                            [(WUYI_TEXT_XIULUO, [500, 300, 900, 340], 0.96)],
                            img_np))
    monkeypatch.setattr(battle, "_find_wuyi_option",
                        lambda items, np_img: (650, 420))
    assert battle._handle_wuyi_dialog(object(), img=_make_noisy_pil(1)) is True
    assert clicks == [(650, 420)]  # 绿字校验拦截缓存直点，识别结果被点击


def test_handle_hezi_reuses_frame(monkeypatch):
    """bgr 传入时复用轮询帧：开关全开且无盒子时不截窗。"""
    monkeypatch.setattr(battle, "current_task_mode", lambda: "修罗")
    monkeypatch.setattr(battle, "load_xiuluo_hezi", lambda: True)
    monkeypatch.setattr(battle.team, "known_leader", lambda: True)
    monkeypatch.setattr(battle.game_map, "match_frame_np", lambda *a, **k: None)
    assert battle._handle_hezi(object(), bgr=make_bgr_frame()) is False
