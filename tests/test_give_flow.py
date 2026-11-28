# -*- coding: utf-8 -*-
"""给与面板类别型选物：提示框属性复核 + 逐储物格扫描（同款 +8/+9 不得递错）。"""
from src.workflows import give_flow


def test_tooltip_attr_parsing():
    """属性行「抗水 +9.0%」→ 9；百分号丢失时用「抗X +N」兜底。"""
    assert give_flow._tooltip_attr(
        [("抗水 +9.0%", (0, 0, 0, 0), 0.97)]) == 9
    assert give_flow._tooltip_attr(
        [("抗封印 1 +9.0%", (0, 0, 0, 0), 0.91)]) == 9
    assert give_flow._tooltip_attr(
        [("抗雷+8.0", (0, 0, 0, 0), 0.90)]) == 8
    assert give_flow._tooltip_attr(
        [("蓝田玉", (0, 0, 0, 0), 1.0)]) is None


def test_tooltip_level_parsing():
    """宝宝装备提示框「等级：1级」→ 1（全/半角冒号），无等级行 → None。"""
    assert give_flow._tooltip_level(
        [("等级：1级", (0, 0, 0, 0), 0.999)]) == 1
    assert give_flow._tooltip_level(
        [("等级: 12 级", (0, 0, 0, 0), 0.90)]) == 12
    assert give_flow._tooltip_level(
        [("宝宝的乐器，普通的木筝", (0, 0, 0, 0), 0.96)]) is None
    # 描述行里的「N级」没有「等级」前缀，不得当等级行
    assert give_flow._tooltip_level(
        [("1级宝宝装备", (0, 0, 0, 0), 0.99)]) is None


def test_match_icons_uses_catalog_threshold_per_icon(monkeypatch):
    """带数量角标的珍兽丹模板用目录阈值 0.69，其余模板仍用给与默认 0.85。"""
    seen = {}

    def fake_match(bgr, icon, threshold=0.8, region=None):
        seen[icon] = threshold
        return []

    monkeypatch.setattr(give_flow.game_map, "match_frame_all", fake_match)
    give_flow._match_icons(object(), ["item_zhenshoudan.png",
                                      "item_tianwaifeishi.png"])
    assert seen["item_zhenshoudan.png"] == 0.69
    assert seen["item_tianwaifeishi.png"] == give_flow.CANDIDATE_THRESHOLD


def test_pick_and_give_attr_skips_out_of_range(monkeypatch):
    """同款 +8/+9 都命中模板：只递属性落在 9-12 区间的那一件。"""
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("当前储物格", {"item_lantianyu.png": [(600, 320), (600, 365)]}),
        ]))
    reads = iter([("蓝田玉", 8), ("蓝田玉", 9)])
    monkeypatch.setattr(give_flow, "read_tooltip",
                        lambda win, pt, kw: next(reads))
    given = []

    def _click(win, pt, name, prefix):
        given.append((pt, name))
        return True, f"已给与「{name}」"

    monkeypatch.setattr(give_flow, "_click_and_give", _click)
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    ok, info = give_flow.pick_and_give_attr(
        object(), [("item_lantianyu.png", "蓝田玉")], 9, 12)
    assert ok and given == [((600, 365), "蓝田玉")]
    assert "蓝田玉" in info


def test_pick_and_give_attr_continues_after_bad_slot(monkeypatch):
    """第 1 格只有 +8（图标命中但属性不达标）→ 必须继续扫后续储物格。

    2026-11-27 实机教训：第 3 格云罗帐+8 图标命中即返回，漏掉后面
    储物格里的 +9 武帝袍，任务卡在给与面板。
    """
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("储物格 1/5", {"item_yunluozhang.png": [(600, 320)]}),
            ("储物格 2/5", {}),
            ("储物格 3/5", {"item_wudipao.png": [(603, 365)]}),
        ]))
    reads = {600: ("云罗帐", 8), 603: ("武帝袍", 9)}
    monkeypatch.setattr(give_flow, "read_tooltip",
                        lambda win, pt, kw: reads[pt[0]])
    given = []
    monkeypatch.setattr(give_flow, "_click_and_give",
                        lambda win, pt, name, prefix:
                        given.append((pt, name))
                        or (True, f"已给与「{name}」"))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    ok, info = give_flow.pick_and_give_attr(
        object(),
        [("item_yunluozhang.png", "云罗帐"), ("item_wudipao.png", "武帝袍")],
        9, 12)
    assert ok and given == [((603, 365), "武帝袍")]
    assert "武帝袍" in info


def test_pick_and_give_attr_requires_attr_line(monkeypatch):
    """属性行未识别时不递（宁可下轮重试，不递错属性）。"""
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("当前储物格", {"item_canghaizhu.png": [(600, 320)]}),
        ]))
    monkeypatch.setattr(give_flow, "read_tooltip",
                        lambda win, pt, kw: ("沧海珠", None))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    called = []
    monkeypatch.setattr(give_flow, "_click_and_give",
                        lambda *a, **k: called.append(1) or (True, ""))
    ok, info = give_flow.pick_and_give_attr(
        object(), [("item_canghaizhu.png", "沧海珠")], 9, 12)
    assert not ok and called == []
    assert "属性均不满足 9-12" in info


def test_pick_and_give_level_skips_other_and_unknown_levels(monkeypatch):
    """宝宝装备等级给与：只递等级落在 1-2 的；3级/等级未识别一律跳过。"""
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("当前储物格", {"item_zhuangzi.png": [(600, 320), (600, 365)],
                          "item_cubuyi.png": [(650, 320)]}),
        ]))
    reads = iter([("庄子", 3), ("庄子", None), ("粗布衣", 1)])
    monkeypatch.setattr(give_flow, "read_tooltip_level",
                        lambda win, pt, kw: next(reads))
    given = []
    monkeypatch.setattr(give_flow, "_click_and_give",
                        lambda win, pt, name, prefix:
                        given.append((pt, name)) or (True, f"已给与「{name}」"))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    ok, info = give_flow.pick_and_give_level(
        object(), [("item_zhuangzi.png", "庄子"),
                   ("item_cubuyi.png", "粗布衣")], 1, 2)
    assert ok and given == [((650, 320), "粗布衣")]
    assert "粗布衣" in info


def test_pick_and_give_level_reports_miss(monkeypatch):
    """全部候选等级不达标 → 不点给与并报等级区间。"""
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("当前储物格", {"item_muzheng.png": [(600, 320)]}),
        ]))
    monkeypatch.setattr(give_flow, "read_tooltip_level",
                        lambda win, pt, kw: ("木筝", 3))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    called = []
    monkeypatch.setattr(give_flow, "_click_and_give",
                        lambda *a, **k: called.append(1) or (True, ""))
    ok, info = give_flow.pick_and_give_level(
        object(), [("item_muzheng.png", "木筝")], 1, 2)
    assert not ok and called == []
    assert "等级均不满足 1-2 级" in info


def test_pick_and_give_reports_hover_name_miss_separately(monkeypatch):
    """图标命中但悬停名读不到 → 报「命中未读到名称」，不误报「未找到图标」。

    2026-11-28 实机：规范名用「配饰盒子」而游戏提示名是「佩饰盒子」，
    图标 0.9985 明明命中，日志却报「未找到候选物品图标」，误导排查。
    """
    monkeypatch.setattr(
        give_flow, "iter_slot_candidates",
        lambda win, icons, prefix="": iter([
            ("当前储物格", {"item_sanjipeishihezi.png": [(557, 479)]}),
        ]))
    monkeypatch.setattr(give_flow, "hover_item_name",
                        lambda win, pt, kw: None)
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    called = []
    monkeypatch.setattr(give_flow, "_click_and_give",
                        lambda *a, **k: called.append(1) or (True, ""))
    ok, info = give_flow.pick_and_give(
        object(), "item_sanjipeishihezi.png",
        accept=lambda n: True, keyword="三级配饰盒子")
    assert not ok and called == []
    assert "命中 1 处但悬停未读到名称" in info
    assert "三级配饰盒子" in info


# ---- 储物格切换（给与面板一次只显示一个储物格） ----

class _BoxBase:
    x, y = 850, 300


def test_iter_slot_candidates_yields_all_slots(monkeypatch):
    """当前格 + 5 个储物格逐一产出；格内无候选产出空 hits。"""
    scans = iter([{}, {},
                  {"item_wudipao.png": [(600, 320)]}, {}, {}, {}])
    monkeypatch.setattr(give_flow, "find_candidates_multi",
                        lambda win, icons, threshold=0.85: next(scans))
    monkeypatch.setattr(give_flow, "_hover", lambda *a, **k: True)
    monkeypatch.setattr(give_flow.bag, "find_box_one_close",
                        lambda win: _BoxBase())
    monkeypatch.setattr(give_flow.bag, "is_slot_panel_open", lambda win: True)
    clicks = []
    monkeypatch.setattr(give_flow, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    slots = list(give_flow.iter_slot_candidates(object(), ["x"]))
    assert len(slots) == 6  # 当前格 + 5 格
    assert clicks == [(850, 300 + off) for off in give_flow.GIVE_SLOT_OFFSETS]
    assert slots[2][1] == {"item_wudipao.png": [(600, 320)]}


def test_iter_slot_candidates_aborts_when_panel_gone(monkeypatch):
    """切格前面板丢失（被地图/其它面板遮挡）→ 立即中止，绝不盲点。"""
    monkeypatch.setattr(give_flow, "find_candidates_multi",
                        lambda *a, **k: {})
    monkeypatch.setattr(give_flow, "_hover", lambda *a, **k: True)
    monkeypatch.setattr(give_flow.bag, "find_box_one_close",
                        lambda win: _BoxBase())
    monkeypatch.setattr(give_flow.bag, "is_slot_panel_open", lambda win: False)
    clicks = []
    monkeypatch.setattr(give_flow, "human_click",
                        lambda *a, **k: clicks.append(1))
    slots = list(give_flow.iter_slot_candidates(object(), ["x"]))
    assert len(slots) == 1 and clicks == []  # 只有当前格，未盲点


# ---- 双选一给与面板（2026-11-27 实机「千年魂石或天书残卷一或二」） ----

def test_pick_choice_and_give_selects_then_confirms(monkeypatch):
    """点物品格图标 → 待给予框复核 → 点确定；面板关闭判成功。"""
    class _Win:
        def capture(self, force_activate=False):
            return object()

    clicks = []
    monkeypatch.setattr(give_flow, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(give_flow.game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(give_flow.game_map, "match_frame_multi",
                        lambda bgr, icons, threshold, regions:
                        {"item_qiannianhuns.png": (613, 516)})

    def _match(bgr, icon, threshold=0.8, region=None, fallback=True):
        if region == give_flow.GIVE_CHOICE_PENDING_BAND:
            return (692, 337)
        if region == give_flow.GIVE_CHOICE_CONFIRM_BAND:
            return (698, 476)
        return None

    monkeypatch.setattr(give_flow.game_map, "match_frame_np", _match)
    monkeypatch.setattr(give_flow, "give_choice_panel_open", lambda img: False)
    ok, info = give_flow.pick_choice_and_give(
        _Win(), [("item_qiannianhuns.png", "千年魂石")])
    assert ok and clicks == [(613, 516), (698, 476)]
    assert "千年魂石" in info


def test_pick_choice_and_give_does_not_confirm_without_selection(monkeypatch):
    """点选后待给予框没显示目标物品 → 不点确定（不给错/空给）。"""
    class _Win:
        def capture(self, force_activate=False):
            return object()

    clicks = []
    monkeypatch.setattr(give_flow, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(give_flow.time, "sleep", lambda s: None)
    monkeypatch.setattr(give_flow.game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(give_flow.game_map, "match_frame_multi",
                        lambda *a, **k: {"item_qiannianhuns.png": (613, 516)})
    monkeypatch.setattr(give_flow.game_map, "match_frame_np",
                        lambda *a, **k: None)
    ok, info = give_flow.pick_choice_and_give(
        _Win(), [("item_qiannianhuns.png", "千年魂石")])
    assert not ok and clicks == [(613, 516)]
    assert "未点确定" in info


def test_choice_panel_detected_from_dialog_items():
    """双选一面板：follow 的 OCR 掩码帧上模板失效，用对话区文本判定。"""
    items = [
        ("请从两种物品任选一种给予（雅誉不同）", (527, 270, 828, 284), 0.93),
        ("雅誉", (527, 407, 570, 431), 0.99),
        ("雅誉", (698, 408, 740, 431), 0.99),
    ]
    assert give_flow.give_choice_panel_in_items(items)
    # 说明行 OCR 漏读：两行「雅誉」需求行兜底
    assert give_flow.give_choice_panel_in_items([
        ("雅誉", (527, 407, 570, 431), 0.99),
        ("雅誉", (698, 408, 740, 431), 0.99)])
    # 左侧任务追踪文本里的「雅誉」不算（rect[0] < 400）
    assert not give_flow.give_choice_panel_in_items([
        ("找到一个千年魂石(40点雅誉)或天书残卷一或二(3点雅誉)",
         (21, 284, 199, 306), 0.97)])
    assert not give_flow.give_choice_panel_in_items([])


def test_npc_names_match_tolerates_one_char_ocr_error():
    """追踪面板 NPC 名 1 字 OCR 误读不得拦截给与（2026-11-27 实机）。

    「彩翼仙子」读成「彩算仙子」；短名/差异大仍严格拦截防给错。
    """
    assert give_flow.npc_names_match("彩算仙子", "彩翼仙子") is True
    assert give_flow.npc_names_match("彩翼仙子", "彩翼仙子") is True
    assert give_flow.npc_names_match("彩翼", "彩翼仙子") is True   # 截断
    assert give_flow.npc_names_match("黑熊精", "梅花仙") is False
    assert give_flow.npc_names_match("阿旺", "阿王") is False     # 短名严格
    assert give_flow.npc_names_match("", "彩翼仙子") is True      # 无可比对
    assert give_flow.npc_names_match("彩翼仙子", "") is True
