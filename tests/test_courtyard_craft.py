# -*- coding: utf-8 -*-
"""庭院聚灵台打造流程核心路径单测。

覆盖：
1. 游戏提示「已经打造过需要的任务品」→ 按成功返回（不再重复消耗灵气）；
2. 面板 → 打造 → 二次确认 → 进度条消失的完整动作序列；
3. 带区匹配未命中才全帧兜底，且兜底限频（性能规则）。
"""
import types

import cv2
import numpy as np

from src.workflows import courtyard_craft as cc


class _Win:
    def __init__(self):
        self.rect = types.SimpleNamespace(width=1378, height=831)

    def capture(self, force_activate=False):
        return object()


def _patch_courtyard_entry(monkeypatch):
    """已在庭院 + 聚灵台对话框已命中的公共桩。"""
    monkeypatch.setattr(cc.game_map, "read_map_position",
                        lambda win: ("高级庭院", 111, 47))
    monkeypatch.setattr(cc, "_move_to_juling", lambda win, pos: (True, "已在聚灵台旁"))
    monkeypatch.setattr(cc, "_click_juling",
                        lambda win: (True, (["聚灵台的灵气储存量：17615/20000"],
                                            None)))
    monkeypatch.setattr(cc, "_log_spirit_info", lambda items: None)
    monkeypatch.setattr(cc.claim_flow, "click_claim_option",
                        lambda *a, **k: True)
    monkeypatch.setattr(cc, "_close_residual_ui", lambda win: False)


def test_craft_item_already_crafted_is_success(monkeypatch):
    """点「我要打造物品」后游戏提示已打造过 → 成功退出、不再确认/等待。"""
    _patch_courtyard_entry(monkeypatch)
    monkeypatch.setattr(cc, "_wait_panel_or_done",
                        lambda win: ("already", None))
    called = []
    monkeypatch.setattr(cc, "_wait_confirm_dialog",
                        lambda win: called.append("confirm"))
    ok, info = cc.craft_item(_Win(), "灵宝天威印", prefix="[300环] ")
    assert ok and "已打造过" in info
    assert called == []


def test_craft_item_panel_confirm_progress(monkeypatch):
    """面板 → 选物 → 打造 → 确认 → 进度条消失 → 关面板。"""
    _patch_courtyard_entry(monkeypatch)
    clicks = []
    monkeypatch.setattr(cc, "_wait_panel_or_done",
                        lambda win: ("panel", (700, 500)))
    monkeypatch.setattr(cc.game_map, "bgr_frame", lambda img: None)
    monkeypatch.setattr(cc, "_select_craft_item", lambda *a: True)
    monkeypatch.setattr(cc, "_wait_confirm_dialog",
                        lambda win: (611, 487,
                                     "你确定要用2400点灵气打造<灵宝天威印>吗？"))
    monkeypatch.setattr(cc, "_wait_progress_done",
                        lambda win: (True, "打造进度条已消失"))
    monkeypatch.setattr(cc, "_close_panel_if_open",
                        lambda win: True)
    monkeypatch.setattr(cc, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    ok, info = cc.craft_item(_Win(), "灵宝天威印")
    assert ok and info == "打造进度条已消失"
    assert clicks == [(700, 500), (611, 487)]


def test_craft_item_no_panel_fails_and_closes_panel(monkeypatch):
    """面板未出现 → 失败返回，并尝试关闭残留面板（失败收尾）。"""
    _patch_courtyard_entry(monkeypatch)
    monkeypatch.setattr(cc, "_wait_panel_or_done", lambda win: (None, None))
    closed = []
    monkeypatch.setattr(cc, "_close_residual_ui",
                        lambda win: closed.append(1) or False)
    ok, info = cc.craft_item(_Win(), "灵宝天威印")
    assert not ok and "未出现打造面板" in info
    assert closed == [1]


def test_match_icon_band_then_limited_full_fallback(monkeypatch):
    """条带未命中 → 全帧兜底；限频窗口内不再全帧（性能规则 3）。"""
    calls = []

    def _best(bgr, icon, region=None):
        calls.append(region)
        if region is not None:
            return 0.5, (0, 0)
        return 0.95, (700, 300)

    monkeypatch.setattr(cc.game_map, "best_match_np", _best)
    cc._LAST_FULL_CHECK.clear()
    score, pt = cc._match_icon(None, "x.png", (0, 0, 10, 10), 0.9, "t")
    assert pt == (700, 300) and score == 0.95
    score2, pt2 = cc._match_icon(None, "x.png", (0, 0, 10, 10), 0.9, "t")
    assert pt2 is None
    assert len(calls) == 3      # 带区、全帧兜底、带区（兜底被限频）


def test_wait_progress_done_requires_two_absent_frames(monkeypatch):
    """进度标志：出现后连续两帧未命中才算完成（单帧抖动不误判）。"""
    scores = iter([0.95, 0.95, 0.2, 0.2])
    monkeypatch.setattr(cc.game_map, "best_match_np",
                        lambda bgr, icon, region=None:
                        (next(scores), (0, 0)))
    monkeypatch.setattr(cc.game_map, "bgr_frame", lambda img: None)
    monkeypatch.setattr(cc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cc.app_utils, "raise_if_aborted", lambda: None)
    ok, info = cc._wait_progress_done(_Win())
    assert ok and "已消失" in info


def test_find_craft_halo_locates_gold_selection_box():
    """光圈 = 金色虚线选中框：按金色像素聚类定位其中心（换物品也能用）。"""
    frame = np.zeros((300, 400, 3), np.uint8)
    btn = (300, 250)  # 网格带 = 按钮中心 + (-280,-310,60,-40) → 覆盖 (20,0)-(360,210)
    cv2.rectangle(frame, (180, 130), (220, 170), (0, 200, 255), 2)  # BGR 金色
    halo = cc._find_craft_halo(frame, btn)
    assert halo is not None
    (cx, cy), area = halo
    assert abs(cx - 200) <= 3 and abs(cy - 150) <= 3
    assert area >= cc.HALO_MIN_AREA
    # 无金色像素 → 未命中
    assert cc._find_craft_halo(np.zeros((300, 400, 3), np.uint8), btn) is None
