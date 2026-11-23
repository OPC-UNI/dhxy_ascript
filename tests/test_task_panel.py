# -*- coding: utf-8 -*-
"""任务栏（Alt+Q 任务面板）模块单测。

覆盖：
- 双标题条模板定位（位置/宽度动态，面板拖动后跟随）
- 分组展开/折叠图标双模板比分数
- OCR 行碎片合并、分组行/子任务行识别
- 任务目的段落截取
- 打开/关闭/展开/选中/读取全流程状态机（伪窗口 + 伪 OCR）
"""
import numpy as np
import cv2

from ascript.windows.system import R

from src.device import task_panel as tp


class _FakeClock:
    """伪时钟：sleep 推进时间，避免轮询循环真实等待。"""

    def __init__(self, t0=1000.0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s

    @staticmethod
    def strftime(fmt):
        import time as _t
        return _t.strftime(fmt)


def _tpl_bgr(name):
    img = cv2.imdecode(np.fromfile(R.img(name), dtype=np.uint8),
                       cv2.IMREAD_COLOR)
    assert img is not None, f"模板缺失/解码失败: {name}"
    return img


def _noise_frame(h=831, w=1378, seed=20261123):
    return np.random.default_rng(seed).integers(
        0, 256, (h, w, 3), dtype=np.uint8)


def _paste(frame, name, x, y):
    tpl = _tpl_bgr(name)
    th, tw = tpl.shape[:2]
    frame[y:y + th, x:x + tw] = tpl
    return frame


# 实测坐标（2026-11-23，长相思窗口 1378x831）
LIST_POS = (435, 295)
DETAIL_POS = (659, 295)


def _panel_frame(list_pos=LIST_POS, detail_pos=DETAIL_POS):
    frame = _noise_frame()
    _paste(frame, tp.TASK_PANEL_LIST_ICON, *list_pos)
    _paste(frame, tp.TASK_PANEL_DETAIL_ICON, *detail_pos)
    return frame


# ---- 面板定位 ----

def test_locate_panel_returns_regions_from_header_templates():
    panel = tp.locate_panel(_panel_frame())
    assert panel is not None
    assert panel["list_header"] == (435, 295, 655, 316)
    assert panel["detail_header"] == (659, 295, 950, 316)
    # 内容区：标题条底边下方 +1；列表右缘排除滚动条；高度固定 295
    assert panel["list_region"] == (436, 317, 641, 611)
    assert panel["detail_region"] == (660, 317, 947, 611)


def test_locate_panel_follows_panel_move():
    """面板可拖动：两栏位置整体移动后区域跟随（宽度不变）。"""
    panel = tp.locate_panel(_panel_frame((383, 346), (607, 346)))
    assert panel is not None
    assert panel["list_header"] == (383, 346, 603, 367)
    assert panel["detail_header"] == (607, 346, 898, 367)
    assert panel["list_region"][2] - panel["list_region"][0] == 205
    assert panel["detail_region"][2] - panel["detail_region"][0] == 287


def test_locate_panel_requires_both_headers():
    frame = _noise_frame()
    _paste(frame, tp.TASK_PANEL_LIST_ICON, *LIST_POS)  # 只有任务列表标题条
    assert tp.locate_panel(frame) is None
    assert tp.locate_panel(None) is None


# ---- 分组展开状态（双模板比分数） ----

def _icon_state_frame(icon, cx, cy):
    frame = _noise_frame()
    if icon:
        _paste(frame, icon, cx - 4, cy - 4)  # 模板 9x9，中心对齐
    return frame


def test_group_state_expanded():
    frame = _icon_state_frame(tp.TASK_PANEL_GROUP_EXPANDED_ICON, 446, 386)
    state, pt, scores = tp.group_expanded_state(frame, (437, 374, 541, 396))
    assert state == "expanded"
    assert pt == (446, 386)
    assert scores[0] >= tp.TASK_PANEL_ICON_THRESHOLD
    assert scores[0] >= scores[1] + tp.TASK_PANEL_ICON_MARGIN


def test_group_state_collapsed():
    frame = _icon_state_frame(tp.TASK_PANEL_GROUP_COLLAPSED_ICON, 446, 386)
    state, pt, scores = tp.group_expanded_state(frame, (437, 374, 541, 396))
    assert state == "collapsed"
    assert pt == (446, 386)
    assert scores[1] >= tp.TASK_PANEL_ICON_THRESHOLD
    assert scores[1] >= scores[0] + tp.TASK_PANEL_ICON_MARGIN


def test_group_state_unknown_without_icon():
    frame = _noise_frame()
    state, pt, _scores = tp.group_expanded_state(frame, (437, 374, 541, 396))
    assert state == "unknown" and pt is None


# ---- OCR 行合并与分组/子任务识别 ----

def _items(*rows):
    return [(txt, rect, 0.99) for txt, rect in rows]


EXPANDED_ITEMS = _items(
    ("任务列表", (445, 297, 516, 316)),
    ("追踪", (593, 296, 634, 316)),
    ("日遣灵遂愿[1]", (436, 314, 541, 337)),
    ("遣灵遂愿", (452, 335, 519, 358)),
    ("田常规玩法[3]", (437, 355, 541, 377)),
    ("日降魔任务[1]", (437, 374, 541, 396)),
    ("三藏逢魔", (451, 394, 520, 417)),
    ("[√]", (601, 396, 630, 415)),
    ("田门派试炼[1]", (437, 414, 541, 436)),
    ("田长安之围[2]", (437, 432, 541, 455)),
)
COLLAPSED_ITEMS = [it for it in EXPANDED_ITEMS if it[0] != "三藏逢魔"
                   and it[0] != "[√]"]


def test_visual_rows_merge_right_marker():
    rows = tp._visual_rows(EXPANDED_ITEMS)
    child = [r for r in rows if "三藏逢魔" in r["text"]]
    assert len(child) == 1
    # 同一行右侧 [√] 合并且不抢主文本框
    assert "[√]" in child[0]["text"]
    assert child[0]["rect"][0] == 451


def test_find_group_and_first_child_expanded():
    rows = tp._visual_rows(EXPANDED_ITEMS)
    group = tp.find_group_row(rows)
    assert group is not None and "降魔任务" in group["text"]
    child = tp.first_child_row(rows, group)
    assert child is not None and child["text"] == "三藏逢魔"
    assert child["rect"][0] == 451  # 点击任务名，不是右侧 [√]


def test_first_child_none_when_collapsed():
    rows = tp._visual_rows(COLLAPSED_ITEMS)
    group = tp.find_group_row(rows)
    assert group is not None and "降魔任务" in group["text"]
    assert tp.first_child_row(rows, group) is None


def test_find_group_missing():
    rows = tp._visual_rows(_items(("常规玩法[3]", (437, 355, 541, 377))))
    assert tp.find_group_row(rows) is None


# ---- 任务目的截取 ----

DETAIL_ITEMS = _items(
    ("任务详情", (779, 297, 850, 316)),
    ("分享", (915, 295, 950, 316)),
    ("任务目的：", (661, 317, 732, 336)),
    ("三藏黑松林迷路，误入黄袍怪老巢，性", (673, 334, 931, 354)),
    ("命危急。义士速赶至万寿山，消灭天眼", (661, 351, 931, 370)),
    ("魔尊(万寿山，37，11)，为营救三藏而战", (662, 368, 931, 388)),
    ("任务轮次：8/10（剩余56分29秒）", (661, 402, 887, 423)),
    ("今日已完成60次。", (660, 418, 779, 439)),
)


def test_objective_lines_stops_at_next_section():
    obj = tp.objective_lines(DETAIL_ITEMS)
    assert obj == [
        "三藏黑松林迷路，误入黄袍怪老巢，性",
        "命危急。义士速赶至万寿山，消灭天眼",
        "魔尊(万寿山，37，11)，为营救三藏而战",
    ]


def test_objective_lines_without_header_returns_all():
    items = _items(("第一行", (0, 0, 10, 10)), ("第二行", (0, 20, 10, 30)))
    assert tp.objective_lines(items) == ["第一行", "第二行"]


def test_objective_lines_skips_low_confidence_noise():
    """暗雷任务实机 OCR 在首行下方出现过 0.548 的单字碎片「叁」。"""
    items = _items(("任务目的：", (661, 317, 732, 336))) + [
        ("叁", (660, 330, 690, 350), 0.548),
        ("义士且去平顶山或火云戈壁，使用道具", (658, 330, 932, 355), 0.987),
        ("任务轮次：4/10", (661, 402, 887, 423), 0.994),
    ]
    assert tp.objective_lines(items) == ["义士且去平顶山或火云戈壁，使用道具"]


def test_objective_lines_stops_at_capture_status():
    items = _items(
        ("任务目的：", (661, 317, 732, 336)),
        ("与队友合力捕捉3只暗雷怪", (658, 346, 933, 374)),
        ("你当前捕捉妖魔：0/3", (660, 365, 880, 390)),
        ("全队当前捕捉妖魔：0/3", (660, 383, 900, 406)),
        ("任务轮次：4/10", (661, 402, 887, 423)),
    )
    assert tp.objective_lines(items) == ["与队友合力捕捉3只暗雷怪"]


# ---- 开关面板 / 全流程状态机 ----

def _fake_panel():
    return {"list_region": (0, 0, 100, 100),
            "detail_region": (200, 0, 300, 100),
            "list_header": (0, 0, 100, 10),
            "detail_header": (200, 0, 300, 10)}


def _fake_ocr_factory(panel, state):
    """伪 OCR：任务列表按调用次数返回折叠态→展开态；详情区返回样例。"""
    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            state["list_calls"] += 1
            if state["list_calls"] <= state["collapsed_until"]:
                return list(COLLAPSED_ITEMS), None
            return list(EXPANDED_ITEMS), None
        return list(DETAIL_ITEMS), None
    return fake_ocr


class _FakeImg:
    size = (1000, 1000)


def test_click_current_tab_clicks_ocr_center(monkeypatch):
    """找不到分组时的兜底：OCR 找「当前任务」标签并点击中心。"""
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr",
                        lambda _w: (_FakeImg(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    monkeypatch.setattr(
        game_map, "ocr_region",
        lambda _img, _region: ([("当前任务", (10, 10, 100, 30), 0.99)], None))
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    assert tp._click_current_tab(object(), panel) is True
    assert clicks == [(55, 20)]


def test_click_current_tab_missing_label(monkeypatch):
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr",
                        lambda _w: (_FakeImg(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    monkeypatch.setattr(game_map, "ocr_region",
                        lambda _img, _region: ([], None))
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    assert tp._click_current_tab(object(), panel) is False
    assert clicks == []


def test_ensure_group_expanded_switches_tab_when_missing(monkeypatch):
    """列表无降魔分组（停在其它标签页）→ 点「当前任务」后重读成功。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    only_other = [it for it in EXPANDED_ITEMS if "降魔" not in it[0]]
    state = {"list_calls": 0}

    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            state["list_calls"] += 1
            if state["list_calls"] == 1:
                return list(only_other), None
            return list(EXPANDED_ITEMS), None
        return list(DETAIL_ITEMS), None

    from src.device import map as game_map
    monkeypatch.setattr(game_map, "ocr_region", fake_ocr)
    tabs = []

    def fake_tab(_w, _panel, _prefix):
        tabs.append(1)
        return True

    monkeypatch.setattr(tp, "_click_current_tab", fake_tab)
    ok, info, group = tp.ensure_group_expanded(object())
    assert ok and tabs == [1]
    assert group is not None and "降魔任务" in group["text"]
    assert state["list_calls"] >= 2


def test_open_task_panel_uses_hotkey_when_closed(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    calls = {"locate": 0}

    def fake_locate(_b):
        # 初次检查未打开；Alt+Q 投递后的轮询命中
        calls["locate"] += 1
        return None if calls["locate"] == 1 else panel

    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", fake_locate)
    hotkeys = []
    monkeypatch.setattr(tp, "_press_toggle_hotkey",
                        lambda _w: hotkeys.append(1) or (True, "已投递"))
    ok, info = tp.open_task_panel(object())
    assert ok and hotkeys == [1]


def test_open_task_panel_skips_hotkey_when_open(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: _fake_panel())
    hotkeys = []
    monkeypatch.setattr(tp, "_press_toggle_hotkey",
                        lambda _w: hotkeys.append(1) or (True, "已投递"))
    ok, info = tp.open_task_panel(object())
    assert ok and info == "任务栏已打开" and hotkeys == []


def test_close_task_panel_clicks_close_button(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    calls = {"locate": 0}

    def fake_locate(_b):
        calls["locate"] += 1
        # 初始 + 关闭前各一次命中；点击后轮询判关闭
        return _fake_panel() if calls["locate"] <= 2 else None

    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", fake_locate)
    monkeypatch.setattr(tp, "_match_close_button",
                        lambda _b: (0.99, (944, 259)))
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    ok, info = tp.close_task_panel(object())
    assert ok and clicks == [(944, 259)] and "关闭按钮" in info


def test_close_task_panel_hotkey_fallback(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    calls = {"locate": 0}

    def fake_locate(_b):
        calls["locate"] += 1
        return _fake_panel() if calls["locate"] <= 2 else None

    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", fake_locate)
    monkeypatch.setattr(tp, "_match_close_button", lambda _b: None)
    keys = []
    monkeypatch.setattr(tp, "_press_toggle_hotkey",
                        lambda _w: keys.append(1) or (True, "已投递"))
    ok, info = tp.close_task_panel(object())
    assert ok and keys == [1] and "Alt+Q" in info


def test_read_first_group_task_expands_selects_and_reads(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    # 首次读列表为折叠态，点击图标后变为展开态
    state = {"list_calls": 0, "collapsed_until": 1}
    from src.device import map as game_map
    monkeypatch.setattr(game_map, "ocr_region",
                        _fake_ocr_factory(panel, state))
    monkeypatch.setattr(tp, "group_expanded_state",
                        lambda _b, _r: ("collapsed", (446, 386), (0.80, 0.97)))
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    result, info = tp.read_first_group_task(object())
    assert result is not None, info
    # 先点折叠图标，再点第一个子任务（三藏逢魔行中心）
    assert clicks[0] == (446, 386)
    assert clicks[1] == ((451 + 520) // 2, (394 + 417) // 2)
    assert result["task"] == "三藏逢魔"
    assert result["objective_text"].startswith("三藏黑松林迷路")
    assert "任务轮次" not in result["objective_text"]
    rec = result["recognition"]
    assert rec["kind"] == "kill"
    assert rec["map"] == "万寿山"
    assert rec["coords"] == [(37, 11)]
    assert rec["target"] == "天眼魔尊"


def test_read_first_group_task_fails_without_group(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    only_other = [it for it in EXPANDED_ITEMS if "降魔" not in it[0]]
    from src.device import map as game_map
    monkeypatch.setattr(game_map, "ocr_region",
                        lambda _img, _region: (only_other, None))
    result, info = tp.read_first_group_task(object())
    assert result is None and "未找到" in info
