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
    # 实机只读探针 2026-11-24：选中行右缘标记 OCR 读作「[~]」
    ("[~]", (601, 396, 630, 415)),
    ("田门派试炼[1]", (437, 414, 541, 436)),
    ("田长安之围[2]", (437, 432, 541, 455)),
)
COLLAPSED_ITEMS = [it for it in EXPANDED_ITEMS if it[0] != "三藏逢魔"
                   and it[0] != "[~]"]


def test_visual_rows_merge_right_marker():
    rows = tp._visual_rows(EXPANDED_ITEMS)
    child = [r for r in rows if "三藏逢魔" in r["text"]]
    assert len(child) == 1
    # 同一行右侧 [~] 合并且不抢主文本框
    assert "[~]" in child[0]["text"]
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


def test_read_first_group_task_expands_and_skips_selected_child(monkeypatch):
    """已展开且第一个子任务已选中（右侧 [√]）→ 只点展开图标，不重复点子任务。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    # 首次读列表为折叠态，点击图标后变为展开态（实机样例带 [√] 选中标记）
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
    # 只点折叠图标；子任务行已选中（[√]）→ 跳过点击（用户 2026-11-24）
    assert clicks == [(446, 386)]
    assert result["task"] == "三藏逢魔"
    assert result["objective_text"].startswith("三藏黑松林迷路")
    assert "任务轮次" not in result["objective_text"]
    rec = result["recognition"]
    assert rec["kind"] == "kill"
    assert rec["map"] == "万寿山"
    assert rec["coords"] == [(37, 11)]
    assert rec["target"] == "天眼魔尊"


NO_MARKER_ITEMS = [it for it in EXPANDED_ITEMS if it[0] != "[~]"]


def test_read_first_group_task_clicks_unselected_child(monkeypatch):
    """子任务行无选中标记（[~] 缺失）→ 照常点击选中（旧行为兜底）。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map

    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            return list(NO_MARKER_ITEMS), None
        return list(DETAIL_ITEMS), None

    monkeypatch.setattr(game_map, "ocr_region", fake_ocr)
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    result, info = tp.read_first_group_task(object())
    assert result is not None, info
    # 无标记：点击第一个子任务（三藏逢魔行中心）
    assert clicks == [((451 + 520) // 2, (394 + 417) // 2)]
    assert result["task"] == "三藏逢魔"


def test_row_selected_marker_detection():
    """右侧选中标记判定：标记块须在任务名右侧；名称块本身不参与。"""
    rows = tp._visual_rows(EXPANDED_ITEMS)
    child = [r for r in rows if "三藏逢魔" in r["text"]][0]
    assert tp.row_selected(child) is True
    rows = tp._visual_rows(NO_MARKER_ITEMS)
    child = [r for r in rows if "三藏逢魔" in r["text"]][0]
    assert tp.row_selected(child) is False
    # 同一标记的另一种 OCR 形态「[√]」同样识别（兼容旧样例）
    alt = [it for it in EXPANDED_ITEMS
           if it[0] != "[~]"] + [("[√]", (601, 396, 630, 415), 0.99)]
    rows = tp._visual_rows(alt)
    child = [r for r in rows if "三藏逢魔" in r["text"]][0]
    assert tp.row_selected(child) is True
    # 任务名本身含标记字符不算（只看名称块右侧的 OCR 块）
    rows = tp._visual_rows(_items(("三藏逢魔~", (451, 394, 520, 417))))
    assert tp.row_selected(rows[0]) is False


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


# ---- 修罗任务（用户 2026-11-26 实机截图）：常规玩法分组 + 按任务名选中 ----

# 任务列表实机形态：常规玩法[2] 下有「山河名望」（第一个）与「修罗任务」
# 两条子任务，必须先按任务名挑中修罗任务，不能取第一个子任务
XIULUO_LIST_ITEMS = _items(
    ("任务列表", (445, 297, 516, 316)),
    ("追踪", (593, 296, 634, 316)),
    ("天常规玩法[2]", (437, 355, 541, 377)),
    ("山河名望", (451, 374, 520, 397)),
    ("修罗任务", (451, 393, 520, 416)),
    ("田门派试炼[1]", (437, 414, 541, 436)),
)

# 任务详情实机形态：任务目的 → 任务状态 → … → 任务提示（玩法说明）
XIULUO_DETAIL_ITEMS = _items(
    ("任务详情", (779, 297, 850, 316)),
    ("分享", (915, 295, 950, 316)),
    ("任务目的：", (661, 317, 732, 336)),
    ("前往凤巢六层(84,55)处消灭修罗。", (661, 334, 931, 354)),
    ("任务状态：", (661, 402, 732, 423)),
    ("当前环数：10/10", (660, 418, 779, 439)),
    ("今日已完成次数：7", (660, 436, 800, 457)),
    ("当前拥有混沌之气：357", (660, 454, 840, 475)),
    ("任务提示：", (660, 472, 732, 493)),
    ("1.小鬼/天庭/鬼王/修罗每日共享任务", (660, 490, 931, 511)),
    ("次数，一共为90次；", (660, 508, 800, 529)),
)


def test_child_rows_all_and_named_child():
    """常规玩法分组下两条子任务：默认取第一个，按任务名选中修罗任务。"""
    rows = tp._visual_rows(XIULUO_LIST_ITEMS)
    group = tp.find_group_row(rows, tp.TASK_PANEL_XIULUO_GROUP)
    assert group is not None and "常规玩法" in group["text"]
    children = tp.child_rows(rows, group)
    assert [r["text"] for r in children] == ["山河名望", "修罗任务"]
    first = tp.first_child_row(rows, group)
    assert first is not None and first["text"] == "山河名望"
    named = tp.first_child_row(rows, group, tp.TASK_PANEL_XIULUO_TASK)
    assert named is not None and named["text"] == "修罗任务"


def test_objective_lines_stops_at_task_hint():
    """「任务状态」段头漏读时，任务提示段仍能截断任务目的。"""
    items = _items(
        ("任务目的：", (661, 317, 732, 336)),
        ("前往凤巢六层(84,55)处消灭修罗。", (661, 334, 931, 354)),
        ("任务提示：", (660, 352, 732, 373)),
        ("1.小鬼/天庭/鬼王/修罗每日共享任务", (660, 370, 931, 391)),
    )
    assert tp.objective_lines(items) == ["前往凤巢六层(84,55)处消灭修罗。"]


def test_trim_hint_lines_drops_hint_section():
    """任务提示段（玩法说明）整段不返回；其上方的任务状态行保留。"""
    out = tp.trim_hint_lines(XIULUO_DETAIL_ITEMS)
    texts = [it[0] for it in out]
    assert "任务状态：" in texts and "当前拥有混沌之气：357" in texts
    assert "任务提示：" not in texts
    assert not any("每日共享" in t or "一共为90次" in t for t in texts)


def test_read_first_group_task_xiuluo_named_child(monkeypatch):
    """修罗任务：按任务名选中 → 读任务目的 → 识别为单杀 → 任务提示不返回。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (object(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map

    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            return list(XIULUO_LIST_ITEMS), None
        return list(XIULUO_DETAIL_ITEMS), None

    monkeypatch.setattr(game_map, "ocr_region", fake_ocr)
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    result, info = tp.read_first_group_task(
        object(), keyword=tp.TASK_PANEL_XIULUO_GROUP,
        task_keyword=tp.TASK_PANEL_XIULUO_TASK)
    assert result is not None, info
    # 只点选「修罗任务」行（山河名望是第一个子任务，不得被选中）
    assert clicks == [((451 + 520) // 2, (393 + 416) // 2)]
    assert result["task"] == "修罗任务"
    assert result["objective_text"] == "前往凤巢六层(84,55)处消灭修罗。"
    assert all("任务提示" not in line[0] for line in result["detail"])
    rec = result["recognition"]
    assert rec["kind"] == "kill"
    assert rec["map"] == "凤巢六层"
    assert rec["coords"] == [(84, 55)]
    assert rec["target"] == "修罗"
    assert rec["max_rounds"] == 1


# ---- 标签页切换 / 列表滚动 / 关键字扫描（300环 日常活动页） ----

TAB_ITEMS = [
    ("当前任务", (456, 273, 534, 295), 1.0),
    ("日常活动", (559, 270, 636, 291), 1.0),
    ("周常副本", (659, 273, 738, 294), 1.0),
    ("其他任务", (759, 271, 841, 296), 1.0),
]

DAILY_ROWS = _items(
    ("常规玩法[6]", (436, 317, 541, 339)),
    ("五环任务", (452, 334, 519, 355)),
    ("客栈任务", (452, 355, 519, 376)),
    ("雁塔除妖[1]", (436, 457, 541, 479)),
    ("江湖历练任务[1]", (436, 527, 572, 551)),
    ("历练征程", (452, 549, 519, 570)),
    ("街坊玩法[4]", (436, 587, 541, 609)),
)

NO_TARGET_ROWS = [it for it in DAILY_ROWS if "江湖历练" not in it[0]
                  and "历练征程" not in it[0]]


class _Img:
    size = (1378, 831)


def _fake_tab_ocr(panel, list_rows, tabs=TAB_ITEMS):
    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            return list(list_rows), None
        return list(tabs), None
    return fake_ocr


def test_click_tab_clicks_daily_tab_and_ignores_missing(monkeypatch):
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (_Img(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    monkeypatch.setattr(game_map, "ocr_region", _fake_tab_ocr(panel, []))
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    ok, info = tp.click_tab(object(), tp.TASK_PANEL_TAB_DAILY)
    assert ok and "已点击" in info
    assert clicks == [(597, 280)]
    # 标签读不到时不盲点其它标签（返回失败、零点击）
    monkeypatch.setattr(game_map, "ocr_region",
                        lambda _img, _region: (list(TAB_ITEMS[:1]), None))
    ok, _info = tp.click_tab(object(), tp.TASK_PANEL_TAB_DAILY)
    assert not ok and clicks == [(597, 280)]


def test_switch_to_tab_waits_for_daily_anchor(monkeypatch):
    """切到日常活动并等锚点行出现（首轮列表未刷新，第二轮出现）。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (_Img(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    state = {"n": 0}

    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            state["n"] += 1
            return (list(NO_TARGET_ROWS) if state["n"] == 1
                    else list(DAILY_ROWS)), None
        return list(TAB_ITEMS), None

    monkeypatch.setattr(game_map, "ocr_region", fake_ocr)
    clicks = []
    monkeypatch.setattr(tp, "human_click",
                        lambda _w, x, y: clicks.append((x, y)))
    ok, info = tp.switch_to_tab(
        object(), tp.TASK_PANEL_TAB_DAILY, tp.TASK_PANEL_DAILY_ANCHORS)
    assert ok and "已切换到" in info
    assert clicks == [(597, 280)]


def test_scan_list_for_scrolls_down_and_finds_row(monkeypatch):
    """目标不在当前可见区：向上到顶后向下滚动，找到「江湖历练任务」。"""
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (_Img(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    top_rows = [it for it in DAILY_ROWS if it[0] in
                ("常规玩法[6]", "五环任务", "客栈任务", "雁塔除妖[1]")]
    bottom_rows = [it for it in DAILY_ROWS if it[0] not in
                   ("常规玩法[6]", "五环任务", "客栈任务")]
    state = {"list_calls": 0, "scrolls": []}

    def fake_ocr(_img, region):
        if tuple(region) == tuple(panel["list_region"]):
            state["list_calls"] += 1
            if state["list_calls"] <= 3:
                return list(top_rows), None      # 初始/向上到顶（尚无目标）
            return list(bottom_rows), None       # 向下后目标出现
        return list(TAB_ITEMS), None

    monkeypatch.setattr(game_map, "ocr_region", fake_ocr)
    monkeypatch.setattr(tp, "human_click", lambda _w, x, y: None)

    def fake_scroll(win, lines, prefix=""):
        state["scrolls"].append(lines)
        return True, f"列表滚动 {lines:+d} 行"

    monkeypatch.setattr(tp, "scroll_list", fake_scroll)
    row, info = tp.scan_list_for(object(), "江湖历练任务",
                                 tab=tp.TASK_PANEL_TAB_DAILY,
                                 anchors=tp.TASK_PANEL_DAILY_ANCHORS)
    assert row is not None, info
    assert "江湖历练任务" in row["text"]
    # 先向上到顶（一次内容不变即停），再向下找到
    assert state["scrolls"][0] == tp.TASK_PANEL_SCROLL_LINES
    assert tp.TASK_PANEL_SCROLL_LINES in state["scrolls"]
    assert -tp.TASK_PANEL_SCROLL_LINES in state["scrolls"]


def test_scan_list_for_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(tp, "time", _FakeClock())
    panel = _fake_panel()
    monkeypatch.setattr(tp, "_capture_bgr", lambda _w: (_Img(), object()))
    monkeypatch.setattr(tp, "locate_panel", lambda _b: panel)
    from src.device import map as game_map
    rows_only = [it for it in DAILY_ROWS if "江湖历练" not in it[0]
                 and "历练征程" not in it[0]]
    monkeypatch.setattr(game_map, "ocr_region",
                        lambda _img, _region: (list(rows_only), None))
    monkeypatch.setattr(tp, "human_click", lambda _w, x, y: None)
    monkeypatch.setattr(tp, "scroll_list",
                        lambda win, lines, prefix="": (True, "滚动"))
    row, info = tp.scan_list_for(object(), "江湖历练任务",
                                 tab=tp.TASK_PANEL_TAB_DAILY,
                                 anchors=tp.TASK_PANEL_DAILY_ANCHORS)
    assert row is None and "未找到" in info



