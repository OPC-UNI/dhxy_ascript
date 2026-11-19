"""xinghun（星魂归位·四象禁地）小游戏单元测试。

锁住（2026-11-17 用户实机全流程）：
- 触发条件：任务名 + 「星宿石」同时在场（NPC 入口阶段零操作）
- green_picker：场景阶段只点「四象禁地」（不点星宿石链接）
- 场景标签识别：只认四字整行「X之星魂」，对话「此乃X之星魂」不算
- 找星魂：点标签 → 走位结束 → 点石柱（标签上方 68px / 遮挡回退）
- 石柱重试上限与跳过、无星魂可见时回中心/游走搜索
- 星宿石链接点击 → 「星魂归位！」→ 回中心 → 下一轮
- 任务文案连续缺失复位
截图/OCR/点击全部桩替换（不依赖真机画面）。
"""
import time

import numpy as np
import pytest

from src.minigames import xinghun as xh

# 四象禁地阶段追踪面板 OCR 行（2026-11-17 实机）
TRACKER_ITEMS = [
    ("[日常]星魂归位[山河名望]", [14, 232, 204, 252], 0.97),
    ("在四象禁地（160，50）附近", [21, 250, 220, 273], 0.99),
    ("找到星魂，点击星魂移动，对", [21, 267, 221, 290], 1.0),
    ("应带到箕之星宿石、房之星宿", [20, 283, 221, 308], 0.998),
    ("石、心之星宿石、角之星宿石", [23, 304, 220, 324], 1.0),
    ("氐之星宿石、尾之星宿石处", [36, 320, 221, 340], 0.96),
]
ENTRY_ITEMS = [
    ("·[日常]星魂归位[山河名望]", [14, 232, 204, 252], 0.97),
    ("看看天庭的星官角木蛟遇到什", [21, 250, 220, 272], 0.99),
    ("么麻烦了。", [22, 268, 90, 289], 0.99),
]
_FRAME = np.zeros((831, 1378, 3), np.uint8)


def _frame_fn(win, items):
    return _FRAME, _FRAME


@pytest.fixture(autouse=True)
def _reset_state():
    xh.reset()
    yield
    xh.reset()


# ---- 触发条件 / green_picker ----

def test_scene_active_requires_both_keywords():
    """四象禁地阶段（任务名+星宿石）命中；NPC 入口阶段不命中。"""
    assert xh.scene_active(TRACKER_ITEMS)
    assert not xh.scene_active(ENTRY_ITEMS)
    assert not xh.scene_active(
        [("[日常]星魂归位[山河名望]", [14, 232, 204, 252], 0.97)])


def test_green_picker_returns_center_link_only_in_scene(monkeypatch):
    """场景阶段返回「四象禁地」链接点；入口阶段返回 None（走引擎默认）。"""
    seen = {}

    def fake_underline(img, rect, x):
        seen["txt"] = rect
        return (65, 261)

    monkeypatch.setattr(xh.task_tracker, "underline_point", fake_underline)
    assert xh.green_picker(TRACKER_ITEMS, _FRAME) == (65, 261)
    assert seen["txt"] == [21, 250, 220, 273]  # 「在四象禁地（160，50）附近」行
    assert xh.green_picker(ENTRY_ITEMS, _FRAME) is None


def test_link_point_keyword_targets_right_link(monkeypatch):
    """一行两个星宿石链接：按关键词落在目标链接（尾在行右段）。"""
    seen = {}

    def fake_underline(img, rect, x):
        seen["x"] = x
        return (int(x), 330)

    monkeypatch.setattr(xh.task_tracker, "underline_point", fake_underline)
    pt = xh._link_point(TRACKER_ITEMS, _FRAME, xh._stone_keywords("尾"))
    assert pt is not None
    assert 120 < seen["x"] < 210   # 「氐之星宿石、尾之星宿石处」行右段
    assert pt[0] == int(seen["x"])


def test_stone_keywords_aliases():
    """星宿石链接关键词：4 字「X之星宿」+ 3 字「X之星」折行兜底 + 别名。"""
    assert xh._stone_keywords("尾") == ("尾之星宿", "尾之星",
                                        "尼之星宿", "尼之星")
    assert xh._stone_keywords("氐") == ("氐之星宿", "氐之星",
                                        "氏之星宿", "氏之星")
    assert "页之星宿" in xh._stone_keywords("箕")


def test_dialog_soul_element_beats_label(monkeypatch):
    """「此乃X之星魂。」对话解析元素，冲突时覆盖标签 OCR 结果。"""
    items = TRACKER_ITEMS + SOUL_DIALOG_ITEMS
    assert xh._dialog_soul_element(items) == "尾"
    assert xh._dialog_soul_element(TRACKER_ITEMS) is None


# ---- 场景标签识别 ----

def test_soul_labels_exact_line_only(monkeypatch):
    """只认四字整行「X之星魂」：对话「此乃X之星魂」不算；形近字归一。"""
    monkeypatch.setattr(xh.game_map, "ocr_region", lambda img, region: (
        [("尾之星魂", [640, 420, 710, 445], 0.99),
         ("此乃尾之星魂", [500, 380, 700, 400], 0.99),
         ("氐之星魂。", [300, 400, 380, 430], 0.98),
         ("尼之星魂", [860, 200, 930, 225], 0.97)], None))
    cands = xh._soul_labels(object(), [], _frame_fn, time.time(), force=True)
    assert [c[0] for c in cands] == ["尾", "氐", "尾"]


def test_soul_labels_left_edge_fallback(monkeypatch):
    """贴左缘标签主条带漏读时补扫左带检出。

    2026-11-19 实机截图：屏幕左下「箕之星魂」rect 左缘 x=389 < 主条带
    左界 410，主条带 OCR 读成「2星魂」（实测 0.899）→ 四字整行匹配
    失败，模块连续 3 轮「视野内无星魂」游走约 60s。左带按原尺寸裁剪
    识别为「箕之星魂」（实测 0.991）。
    """
    calls = []

    def _ocr(img, region):
        calls.append(region)
        if region == xh.XH_SCAN_REGION:
            return [("2星魂", [410, 725, 458, 751], 0.899)], None
        return [("箕之星魂", [389, 725, 459, 750], 0.991)], None

    monkeypatch.setattr(xh.game_map, "ocr_region", _ocr)
    cands = xh._soul_labels(object(), [], _frame_fn, time.time(), force=True)
    assert cands == [("箕", [389, 725, 459, 750])]
    assert calls == [xh.XH_SCAN_REGION, xh.XH_SCAN_LEFT_REGION]


def test_soul_labels_main_hit_skips_left_band(monkeypatch):
    """主条带已命中星魂时不付左带补扫（正常帧零额外 OCR）。"""
    calls = []

    def _ocr(img, region):
        calls.append(region)
        return [("尾之星魂", [640, 420, 710, 445], 0.99)], None

    monkeypatch.setattr(xh.game_map, "ocr_region", _ocr)
    cands = xh._soul_labels(object(), [], _frame_fn, time.time(), force=True)
    assert [c[0] for c in cands] == ["尾"]
    assert calls == [xh.XH_SCAN_REGION]


def test_pillar_point_offset_and_fallback():
    """石柱点 = 标签框顶上方 68px；标签被遮挡时回退人物上方固定点。"""
    assert xh._pillar_point([("尾", [640, 420, 710, 445])], "尾") == (675, 352)
    assert xh._pillar_point([], "尾") == xh.XH_SELF_PILLAR_POINT


# ---- 找星魂阶段 ----

def _win():
    return object()


def test_search_clicks_soul_label(monkeypatch):
    """视野内有星魂标签 → 点标签中心自动寻路，记录走位等待。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels",
                        lambda *a, **k: [("尾", [640, 420, 710, 445])])
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [(675, 432)]
    assert xh._target == "尾" and xh._walk_at > 0


def test_walk_end_clicks_pillar(monkeypatch):
    """走位结束（位置连续稳定 2 轮）→ 点星魂石柱。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels",
                        lambda *a, **k: [("尾", [640, 420, 710, 445])])
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 160, 50))
    xh._target = "尾"
    xh._walk_at = time.time() - 1.0
    xh._walk_pos = ("四象禁地", 160, 50)
    xh._walk_stable = 0
    # 第一轮：位置稳定 1 轮，未达 2 轮 → 不点
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is False
    assert clicks == []
    # 第二轮：稳定 2 轮 → 点石柱（标签框顶 420-68=352）
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [(675, 352)]
    assert xh._pillar_tries == 1


def test_walk_timeout_clicks_pillar(monkeypatch):
    """走位超时（位置一直变化）→ 仍点石柱（不该无限等）。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels", lambda *a, **k: [])
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 1, 1))
    xh._target = "尾"
    xh._walk_at = time.time() - xh.XH_WALK_TIMEOUT - 1
    xh._walk_pos = ("四象禁地", 9, 9)
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [xh.XH_SELF_PILLAR_POINT]


def test_pillar_retry_limit_skips_soul(monkeypatch):
    """点石柱连续 3 次无对话 → 该星魂本轮跳过（防死循环）。"""
    monkeypatch.setattr(xh, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(xh, "_soul_labels", lambda *a, **k: [])
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    xh._target = "尾"
    xh._pillar_tries = xh.XH_PILLAR_RETRY_MAX
    xh._pillar_at = time.time() - xh.XH_PILLAR_RETRY_INTERVAL - 1
    xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn)
    assert "尾" in xh._bad
    assert xh._target is None


def test_no_souls_walks_to_center_then_search_points(monkeypatch):
    """视野内无星魂：先点「四象禁地」回中心，站定后再游走扩大视野。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels", lambda *a, **k: [])
    monkeypatch.setattr(xh, "_link_point", lambda *a, **k: (65, 261))
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 160, 50))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [(65, 261)]
    xh._search_at = 0.0
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks[-1] == xh.XH_SEARCH_OFFSETS[0]


def test_search_offsets_wait_for_standstill(monkeypatch):
    """游走点须站定才点：人物还在走（位置变化）时不改道。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels", lambda *a, **k: [])
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 130, 70))
    xh._search_idx = 1
    xh._search_pos = ("四象禁地", 100, 50)
    xh._search_still = 0
    xh._search_at = 0.0
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is False
    assert clicks == []


# ---- 星宿石 / 归位 / 回中心 ----

SOUL_DIALOG_ITEMS = [
    ("此乃尾之星魂。", [435, 380, 539, 402], 0.99),
    ("催动法力，让星魂跟随自己移动。", [436, 401, 657, 421], 1.0),
    ("我就摸摸星宿石而已。", [434, 416, 584, 440], 1.0),
]
ARRIVE_DIALOG_ITEMS = [
    ("感觉差不多到对应的星位了，是否让星魂归位？", [437, 381, 751, 400], 1.0),
    ("星魂归位！", [436, 401, 508, 421], 0.998),
    ("好像还没到对应的星位，再走走。", [434, 416, 657, 439], 1.0),
]


def test_soul_dialog_starts_following(monkeypatch):
    """「此乃X之星魂」对话点「催动法力」→ 进入带魂阶段。"""
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: True)
    xh._target = "尾"
    items = TRACKER_ITEMS + SOUL_DIALOG_ITEMS
    assert xh.handle(_win(), items, _FRAME, frame_fn=_frame_fn) is True
    assert xh._phase == 1
    assert xh._link_tries == 0 and xh._link_at == 0.0


def test_soul_dialog_present_waits_if_click_missed(monkeypatch):
    """星魂对话在场但选项没点中（低置信度）→ 等待，不做世界点击。"""
    clicks = []
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    xh._target = "尾"
    items = TRACKER_ITEMS + SOUL_DIALOG_ITEMS
    assert xh.handle(_win(), items, _FRAME, frame_fn=_frame_fn) is False
    assert clicks == [] and xh._phase == 0


def test_stone_phase_clicks_stone_link(monkeypatch):
    """带魂阶段：点「X之星宿石」追踪链接。"""
    clicks = []
    xh._phase = 1
    xh._target = "尾"
    xh._phase_at = time.time() - xh.XH_PHASE_DELAY - 1
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 100, 50))
    monkeypatch.setattr(xh, "_link_point", lambda items, img, kws: (150, 332))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [(150, 332)]
    assert xh._link_tries == 1


def test_stone_phase_no_reclick_while_walking(monkeypatch):
    """寻路中（位置仍在变化）不重复点链接。"""
    clicks = []
    xh._phase = 1
    xh._target = "尾"
    xh._phase_at = time.time() - 5
    xh._link_tries = 1
    xh._link_at = time.time() - xh.XH_LINK_CLICK_INTERVAL - 1
    xh._link_pos = ("四象禁地", 100, 50)
    xh._link_still = 0
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 120, 60))
    monkeypatch.setattr(xh, "_link_point", lambda *a, **k: (150, 332))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is False
    assert clicks == []


def test_stone_phase_enhanced_ocr_fallback(monkeypatch):
    """末行漏检 → 普通链接查找为空 → 增强 OCR 重识别补点星宿石。

    2026-11-19 实机 160s 卡死：跟随后追踪末行「、氏之星宿石、尾之星宿石处」
    压在亮背景下被区域 OCR 整行漏检（实拍截图复现），模块找不到「尾之星宿」
    链接、静默等到 follow 超时。增强 OCR（3x 对比度）可读回该行
    （实测「尾之星宿石处」0.99），命中即点。
    """
    clicks, enh_calls = [], []
    xh._phase = 1
    xh._target = "尾"
    xh._phase_at = 0.0
    xh._link_at = 0.0
    xh._link_tries = 0
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 100, 50))
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh.task_tracker, "ocr_track_enhanced",
                        lambda img: enh_calls.append(img) or TRACKER_ITEMS)
    items = TRACKER_ITEMS[:-1]  # 实拍帧：末行整行漏检
    assert xh.handle(_win(), items, _FRAME, frame_fn=_frame_fn) is True
    assert enh_calls, "普通查找为空时应触发增强 OCR 兜底"
    assert clicks, "增强 OCR 命中末行后必须点击星宿石链接"
    assert xh._link_tries == 1


def test_stone_phase_no_enhanced_when_link_found(monkeypatch):
    """普通帧读出链接时不付增强 OCR（正常路径零额外开销）。"""
    clicks, enh_calls = [], []
    xh._phase = 1
    xh._target = "尾"
    xh._phase_at = 0.0
    xh._link_at = 0.0
    xh._link_tries = 0
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: False)
    monkeypatch.setattr(xh, "_pos", lambda *a, **k: ("四象禁地", 100, 50))
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh.task_tracker, "ocr_track_enhanced",
                        lambda img: enh_calls.append(img) or [])
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks and not enh_calls


def test_arrival_dialog_confirms_return(monkeypatch):
    """到达星宿石点「星魂归位！」→ 计数 +1、进入回中心阶段。"""
    monkeypatch.setattr(xh, "_click_green_option_once", lambda *a, **k: True)
    xh._phase = 1
    xh._target = "尾"
    items = TRACKER_ITEMS + ARRIVE_DIALOG_ITEMS
    assert xh.handle(_win(), items, _FRAME, frame_fn=_frame_fn) is True
    assert xh._phase == 2 and xh._done == 1


def test_return_phase_clicks_center_link_then_back(monkeypatch):
    """回中心阶段：点「四象禁地」→ 等待 → 回找星魂阶段。"""
    clicks = []
    xh._phase = 2
    xh._phase_at = time.time() - xh.XH_PHASE_DELAY - 1
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_link_point", lambda *a, **k: (65, 261))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == [(65, 261)]
    assert xh._link_tries == 1
    xh._link_at = time.time() - xh.XH_RETURN_WAIT - 1
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is False
    assert xh._phase == 0 and xh._link_tries == 0


# ---- 复位 ----

def test_absent_task_resets_idle_state(monkeypatch):
    """空闲态任务文案连续缺失 → 累计状态复位（防跨任务残留）。"""
    xh._done = 3
    xh._bad = {"尾"}
    other = [("别的任务[山河名望]", [14, 232, 204, 252], 0.97)]
    for _ in range(xh.XH_ABSENT_RESET):
        assert xh.handle(_win(), other, _FRAME, frame_fn=_frame_fn) is False
    assert xh._done == 0 and xh._bad == set()


def test_mid_phase_kept_on_empty_items(monkeypatch):
    """mid-phase（带魂）收到无追踪面板的 items（战斗 extras 通道）不得复位。

    战斗中 extras 只拿到对话区 items，若按「任务关键词缺失」复位会丢掉
    带魂/已归位状态，战后重复拿魂卡死（同五庄封印 _seal_missing 教训）。
    """
    xh._phase = 1
    xh._target = "尾"
    for _ in range(xh.XH_ABSENT_RESET * 2):
        assert xh.handle(_win(), [], _FRAME, frame_fn=_frame_fn) is False
    assert xh._phase == 1 and xh._target == "尾"


def test_entry_phase_no_action(monkeypatch):
    """NPC 入口阶段（无「星宿石」）零操作，交给引擎跟踪与对话表。"""
    monkeypatch.setattr(xh, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("入口阶段不应点击")))
    assert xh.handle(_win(), ENTRY_ITEMS, _FRAME, frame_fn=_frame_fn) is False


# ---- 入场阻挡对话（2026-11-17 用户补充：刚进四象禁地有对话影响找星魂）----

class _DismissStub:
    def __init__(self, ret):
        self.ret = ret

    def try_dismiss(self, *a, **k):
        return self.ret


def test_entry_dialog_blocks_world_clicks(monkeypatch):
    """入场剧情对话在场：先推进；推进器被守卫拒绝时也不点世界。"""
    clicks = []
    monkeypatch.setattr(xh, "human_click", lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xh, "_soul_labels",
                        lambda *a, **k: [("尾", [640, 420, 710, 445])])
    monkeypatch.setattr(xh, "dialog_frame_score", lambda bgr: 0.99)
    # 推进器拒绝（站定/渲染守卫未过）→ 本帧等待，不点星魂标签
    monkeypatch.setattr(xh, "_scene_dismisser", lambda: _DismissStub(False))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is False
    assert clicks == []
    # 推进器点击成功（关闭入场对话）→ 本帧已处理
    monkeypatch.setattr(xh, "_scene_dismisser", lambda: _DismissStub(True))
    assert xh.handle(_win(), TRACKER_ITEMS, _FRAME, frame_fn=_frame_fn) is True
    assert clicks == []
