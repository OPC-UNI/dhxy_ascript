# -*- coding: utf-8 -*-
"""300环（江湖历练）任务核心路径单测。

只覆盖三件事：
1. 任务栏状态判定（有环数标记=续跑，无标记=领取）；
2. 按状态选择萧举人对话框选项；
3. 萧举人对话框无标准边框，立绘模板门控（低分帧不 OCR）。
"""
import numpy as np

from src.tasks import sanbaihuan as sb


class _Clock:
    def __init__(self, t0=1000.0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _Win:
    def capture(self, force_activate=False):
        return object()


# ---- 日常活动页状态判定 ----

def test_chain_state_classification():
    # 用户 2026-11-26 实机「运行中」帧：分组计数 [2]，子行多出当前环
    # 任务「见多识广」，追踪面板有「见多识广[江湖历练任务]」
    assert sb.chain_state_in_rows(
        "江湖历练任务[2]", ["历练征程", "见多识广[/]"]) == "running"
    # 子行 OCR 漏掉当前环任务名时，分组计数 ≥2 同样判运行中
    assert sb.chain_state_in_rows(
        "江湖历练任务[2]", ["历练征程"]) == "running"
    # 实机「暂停」帧：列表同无任务（[1] + 历练征程），但详情有目前环数
    assert sb.chain_state_in_rows(
        "江湖历练任务[1]", ["历练征程"],
        "任务状态：目前环数：1/300本周免费暂停次数：1/15") == "paused"
    # 实机「无任务」帧：列表同暂停，但详情没有环数/暂停
    assert sb.chain_state_in_rows(
        "江湖历练任务[1]", ["历练征程"],
        "任务目的：为安抚因灵兽遭难而躁动难安的召唤兽们") == "none"
    assert sb.chain_state_in_rows("江湖历练任务[1]", []) == "none"


# ---- 领取/续跑选项选择 ----

def test_claim_or_continue_option_selection(monkeypatch):
    calls = {}

    def _click(win, needles, cache_key, prefix):
        calls["needles"] = needles
        return True, "已点选项"

    monkeypatch.setattr(sb, "_walk_to_xiao", lambda w, p: (True, "到达"))
    monkeypatch.setattr(sb, "_click_dialog_option", _click)
    monkeypatch.setattr(sb, "_wait_tracked", lambda w, p: (True, "已追踪"))

    ok, _info = sb._claim_or_continue(object(), "running")
    assert ok and calls["needles"] == sb.CONTINUE_OPTION_NEEDLES

    ok, _info = sb._claim_or_continue(object(), "paused")
    assert ok and calls["needles"] == sb.CONTINUE_OPTION_NEEDLES

    ok, _info = sb._claim_or_continue(object(), "none")
    assert ok and calls["needles"] == sb.CLAIM_OPTION_NEEDLES

    # 任务栏读取失败：按领取处理（领取选项两种状态都渲染）
    ok, _info = sb._claim_or_continue(object(), None)
    assert ok and calls["needles"] == sb.CLAIM_OPTION_NEEDLES


# ---- 萧举人对话框：立绘模板门控 ----

def test_wait_npc_dialog_ignores_frames_below_threshold(monkeypatch):
    monkeypatch.setattr(sb, "time", _Clock())
    items = [("踏上周游之路，寻找百兽契约录失佚篇章",
              (500, 430, 900, 450), 0.99)]
    scores = [0.3, 0.4, 0.95]
    state = {"n": 0}

    def _best(_bgr, _icon, region=None):
        i = min(state["n"], len(scores) - 1)
        state["n"] += 1
        return scores[i], (500, 200)

    monkeypatch.setattr(sb.game_map, "bgr_frame", lambda _img: object())
    monkeypatch.setattr(sb.game_map, "best_match_np", _best)
    monkeypatch.setattr(sb.game_map, "ocr_region",
                        lambda _img, _region: (list(items), None))
    from src.core import app_utils
    monkeypatch.setattr(app_utils, "raise_if_aborted", lambda: None)

    frame = sb._wait_npc_dialog(_Win(), timeout=10)
    assert frame is not None and frame[0] == items
    # 前三帧：0.3/0.4 不触发 OCR，第三帧 0.95 命中
    assert state["n"] == 3


# ---- 给与类（天书残卷按卷号选物） ----

def test_required_volumes_from_task_text():
    """任务文案「天书残卷三或十」→ {3,10}；物品名「第三卷」→ 3。"""
    assert sb.parse_required_volumes(
        "快去找到一个天书残卷三或十（3点雅誉），并将其交给高小姐") == {3, 10}
    assert sb.parse_required_volumes("天书残卷（三）或（十）") == {3, 10}
    # 2026-11-27 实机：四卷任选（「、」分隔），任意一卷可交
    assert sb.parse_required_volumes(
        "快去找到一个天书残卷五、七、八或九（3点雅誉），并将其交给彩翼仙子"
    ) == {5, 7, 8, 9}
    assert sb.parse_required_volumes("无关文案") == set()
    assert sb.volume_of("天书残卷第三卷") == 3
    assert sb.volume_of("天书残卷第七卷") == 7
    assert sb.volume_of("天书残卷") is None


def test_parse_required_item_kiwi_and_tianshu():
    """给与文案解析：猕猴桃（普通物品）/ 天书残卷（多卷号）。"""
    assert sb.parse_required_item(
        "·急人所急[江湖历练任务]快去找到一个猕猴桃(4点雅)，并将其交给黑熊精"
    ) == "猕猴桃"
    assert sb.parse_required_item(
        "快去找到一个天书残卷三或十（3点雅誉），并将其交给高小姐"
    ) == "天书残卷三或十"
    assert sb.parse_required_item("无关文案") == ""
    from src.device import items as item_catalog
    assert item_catalog.find_by_name("猕猴桃").key == "mihoutao"
    assert item_catalog.find_by_name("天书残卷三或十").key == "tianshu"


def test_parse_give_target_npc():
    """给与目标 NPC：「…并将其交给黑熊精」→ 黑熊精。"""
    assert sb.parse_give_target(
        "·急人所急[江湖历练任务]快去找到一个猕猴桃(4点雅)，并将其交给黑熊精"
    ) == "黑熊精"
    assert sb.parse_give_target("……并将其交给高小姐。") == "高小姐"
    # 换行断行：「…并将其」/「交给 彩翼仙子」——行首「交给」也要解析出名字，
    # 否则 NPC 交叉核对被跳过（2026-11-27 急人所急实机）
    assert sb.parse_give_target("交给 彩翼仙子") == "彩翼仙子"
    assert sb.parse_give_target("无关文案") == ""


# ---- 给与类（类别型：属性13的炼妖石，2026-11-27 实机）----

def test_parse_attr_category_give_text():
    """实机任务目的「属性13的炼妖石…并给予梅花仙」→ 类别候选可交。"""
    text = ("急人所急江湖历练任务找到属性13的炼妖石(获得5点雅誉)，"
            "并给予梅花仙")
    assert sb.parse_required_item(text) == "属性13的炼妖石"
    assert sb.parse_required_attr_item(text) == (13, "炼妖石")
    assert sb.parse_give_target(text) == "梅花仙"
    # 非属性类文案不触发类别路径
    assert sb.parse_required_attr_item(
        "快去找到一个猕猴桃(4点雅)，并将其交给黑熊精") == (None, "")
    from src.device import items as item_catalog
    assert item_catalog.find_by_name("属性13的炼妖石") is None
    assert {e.key for e in item_catalog.find_by_category("炼妖石", 13)} == {
        "pixiezhu", "lieyansha"}


def test_give_panel_category_candidates_any(monkeypatch):
    """给与面板：定值类别型把候选模板批量交给 pick_and_give_attr（13）。"""
    text = ("急人所急江湖历练任务找到属性13的炼妖石(获得5点雅誉)，"
            "并给予梅花仙")
    items = [(text, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "梅花仙")
    calls = {}

    def _pick(win, pairs, low, high, prefix):
        calls["pairs"] = pairs
        calls["range"] = (low, high)
        return True, "已给与「辟邪珠」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give_attr", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls["range"] == (13, 13)
    assert calls["pairs"] == [("item_pixiezhu.png", "辟邪珠"),
                              ("item_lieyansha.png", "烈焰砂")]


# ---- 给与类（区间型：9-12属性的炼妖石，2026-11-27 可乐可口实机）----

def test_parse_attr_range_give_text():
    """实机「9-12属性的炼妖石…并将其交给」→ 区间解析与类别归一。"""
    text = ("急人所急江湖历练任务快去找到一个9-12属性的炼妖石(2点雅誉），"
            "并将其交给")
    assert sb.parse_required_item(text) == "9-12属性的炼妖石"
    assert sb.parse_required_attr_range(text) == (9, 12, "炼妖石")
    assert sb.parse_required_attr_item(text) == (None, "")
    # 位序变体 + OCR 变体（「炼女」→ 炼妖石）
    assert sb.parse_required_attr_range("找到属性9-12的炼女") == (9, 12, "炼妖石")
    assert sb.parse_required_attr_range("找到属性9-12的炼妖石") == (
        9, 12, "炼妖石")
    assert sb.parse_required_attr_range("找到属性13的炼妖石") is None
    assert sb.parse_required_attr_range("无关文案") is None


def test_give_target_from_items_ignores_world_labels():
    """追踪行尾截断时不得拼接世界浮名成假目标；完整行正常解析。"""
    truncated = [
        ("·急人所急[江湖历练任务]", (15, 231, 190, 252), 0.999),
        ("快去找到一个9-12属性的炼", (21, 249, 222, 272), 0.999),
        ("妖石(2点雅誉），并将其交给", (21, 266, 223, 290), 0.938),
        ("魔吒", (7, 412, 31, 455), 0.719),      # 追踪区下方世界浮名
        ("得过日过", (227, 542, 295, 556), 0.963),
    ]
    assert sb.parse_give_target_from_items(truncated) == ""
    full = [("妖石(2点雅誉），并将其交给乌鸡国御林军",
             (21, 266, 300, 290), 0.99)]
    assert sb.parse_give_target_from_items(full) == "乌鸡国御林军"


def test_give_panel_range_candidates_any(monkeypatch):
    """区间型给与：按 9-12 收集候选并交给 pick_and_give_attr 复核属性。"""
    text = ("急人所急江湖历练任务快去找到一个9-12属性的炼妖石(2点雅誉），"
            "并将其交给乌鸡国御林军")
    items = [(text, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc",
                        lambda win: "乌鸡国御林军")
    calls = {}

    def _pick(win, pairs, low, high, prefix):
        calls["pairs"] = pairs
        calls["range"] = (low, high)
        return True, "已给与「蓝田玉」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give_attr", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls["range"] == (9, 12)
    assert [p[1] for p in calls["pairs"]] == [
        "沧海珠", "蓝田玉", "灵犀角", "霄汉鼎", "忆梦符", "云罗帐", "武帝袍"]


# ---- 给与类（等级类别型：1级或2级宝宝装备，2026-11-27 实机）----

def test_parse_level_item_give_text():
    """实机追踪「找到1级或2级宝宝装备…交给村长」→ 等级区间 + 类别。"""
    text = ("急人所急江湖历练任务快去找到一个1级或2级宝宝装备(4点雅誉)，"
            "并将其交给村长")
    assert sb.parse_required_item(text) == "1级或2级宝宝装备"
    assert sb.parse_required_level_item(text) == (1, 2, "宝宝装备")
    assert sb.parse_give_target(text) == "村长"
    # 单级 / 连字符 / 顿号写法；类别 OCR 变体「宝宝装各」归一
    assert sb.parse_required_level_item("找到1级宝宝装备") == (1, 1, "宝宝装备")
    assert sb.parse_required_level_item("找到1-2级宝宝装备") == (
        1, 2, "宝宝装备")
    assert sb.parse_required_level_item("找到2级、1级宝宝装各") == (
        1, 2, "宝宝装备")
    # 属性类文案不带「级」，不得误触发等级路径
    assert sb.parse_required_level_item("找到属性13的炼妖石") is None
    assert sb.parse_required_level_item("找到一个猕猴桃(4点雅)") is None


def test_give_panel_level_candidates_any(monkeypatch):
    """给与面板：宝宝装备按 1-2 级收集候选并交给 pick_and_give_level。"""
    text = ("急人所急江湖历练任务快去找到一个1级或2级宝宝装备(4点雅誉)，"
            "并将其交给村长")
    items = [(text, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "村长")
    calls = {}

    def _pick(win, pairs, low, high, prefix):
        calls["pairs"] = pairs
        calls["range"] = (low, high)
        return True, "已给与「庄子」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give_level", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls["range"] == (1, 2)
    assert calls["pairs"] == [("item_zhuangzi.png", "庄子"),
                              ("item_cubuyi.png", "粗布衣"),
                              ("item_muzheng.png", "木筝")]


# ---- 给与类（多选一：千年魂石或天书残卷一或二，2026-11-27 实机）----

_GIVE_TEXT = ("急人所急江湖历练任务快去找到一个千年魂石(40点雅誉)"
              "或天书残卷一或二(3点雅誉)，并将其交给梅花仙")


def test_multi_choice_give_prefers_high_honor(monkeypatch):
    """高雅誉值优先：先试千年魂石（40 雅誉），成功即停。

    旧实现只解析第一件且千年魂石未登记图标，背包里没有魂石时永远给不
    出去；现在魂石有 47x47 模板，按图标精确定位。
    """
    assert sb.parse_required_item(_GIVE_TEXT) == "千年魂石"
    assert sb.parse_required_volumes(_GIVE_TEXT) == {1, 2}
    assert sb.parse_give_target(_GIVE_TEXT) == "梅花仙"
    items = [(_GIVE_TEXT, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: True)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "梅花仙")
    calls = []

    def _pick(win, icon, accept, keyword, prefix):
        calls.append((icon, keyword, accept("千年魂石")))
        return True, "已给与「千年魂石」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls == [("item_qiannianhuns.png", "千年魂石", True)]


def test_multi_choice_give_falls_back_to_tianshu_volumes(monkeypatch):
    """魂石没找到 → 依次试天书残卷，按卷号 1/2 accept；成功即停。"""
    items = [(_GIVE_TEXT, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: True)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "梅花仙")
    calls = []

    def _pick(win, icon, accept, keyword, prefix):
        if icon == "item_qiannianhuns.png":
            calls.append(("stone", accept("千年魂石")))
            return False, "未找到候选物品图标"
        calls.append(("tianshu", accept("天书残卷第二卷"),
                      accept("天书残卷第三卷")))
        return True, "已给与「天书残卷 第二卷」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls == [("stone", True), ("tianshu", True, False)]


def test_multi_choice_give_low_honor_tries_tianshu_first(monkeypatch):
    """低雅誉值优先：天书残卷(3) 先于千年魂石(40)。"""
    items = [(_GIVE_TEXT, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: False)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "梅花仙")
    calls = []

    def _pick(win, icon, accept, keyword, prefix):
        calls.append(icon)
        return True, "已给与"

    monkeypatch.setattr(sb.give_flow, "pick_and_give", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls == ["sanbaihuan_give_tianshu.png"]


def test_choice_give_panel_candidates_follow_honor_setting(monkeypatch):
    """双选一面板候选顺序随设置：高→魂石先；低→天书先。"""
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: True)
    assert sb._choice_give_candidates(_GIVE_TEXT) == [
        ("item_qiannianhuns.png", "千年魂石"),
        ("sanbaihuan_give_tianshu.png", "天书残卷")]
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: False)
    assert sb._choice_give_candidates(_GIVE_TEXT) == [
        ("sanbaihuan_give_tianshu.png", "天书残卷"),
        ("item_qiannianhuns.png", "千年魂石")]


def test_choice_give_panel_hands_candidates_to_picker(monkeypatch):
    """双选一面板处理链：候选交给 pick_choice_and_give。"""
    items = [(_GIVE_TEXT, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: True)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc",
                        lambda win, **k: "梅花仙")
    calls = {}

    def _pick(win, candidates, prefix):
        calls["candidates"] = candidates
        return True, "已给与「千年魂石」"

    monkeypatch.setattr(sb.give_flow, "pick_choice_and_give", _pick)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls["candidates"] == [
        ("item_qiannianhuns.png", "千年魂石"),
        ("sanbaihuan_give_tianshu.png", "天书残卷")]


def test_unregistered_phrase_still_uses_full_grid_hover(monkeypatch):
    """未登记物品（目录无图标）仍走全格悬停兜底。"""
    text = ("急人所急江湖历练任务快去找到一个玄铁令(5点雅誉)，"
            "并将其交给梅花仙")
    items = [(text, (10, 260, 300, 280), 0.99)]
    monkeypatch.setattr(sb, "_LAST_GIVE_TRY", 0.0)
    monkeypatch.setattr(sb.settings, "load_give_honor_high_first",
                        lambda: True)
    monkeypatch.setattr(sb.give_flow, "give_choice_panel_in_items",
                        lambda items: False)
    monkeypatch.setattr(sb.give_flow, "give_panel_open", lambda img: True)
    monkeypatch.setattr(sb.give_flow, "read_give_npc", lambda win: "梅花仙")
    calls = {}

    def _any(win, accept, keyword, prefix):
        calls["keyword"] = keyword
        calls["accept"] = accept("玄铁令")
        return True, "已给与「玄铁令」"

    monkeypatch.setattr(sb.give_flow, "pick_and_give_any", _any)
    assert sb._extra_dialog_handler(object(), items, object()) is True
    assert calls == {"keyword": "玄铁令", "accept": True}


# ---- 捕捉类环（贵人相助，2026-11-27 实机）----

def test_give_text_best_uses_zoom_when_truncated(monkeypatch):
    """追踪文本丢字时用追踪区 2x 重识别补全（2026-11-27 实机截图）。

    实机截图区域 OCR 只读出「快去找到一个天」；2x 重识别读出「天书残卷
    五、七 / 八或九(3点雅誉) / 交给彩翼仙子」，卷号 {5,7,8,9} 全部补回。
    """
    base = "任务追踪急人所急[江湖历练任务]快去找到一个天"
    zoom = ("任务追踪·急人所急[江湖历练任务]快去找到一个天书残卷五、七"
            "八或九(3点雅誉)，并将其交给彩翼仙子")
    calls = []
    monkeypatch.setattr(sb, "_give_text_zoom",
                        lambda win: (calls.append(1), zoom)[1])
    assert sb._give_text_best(object(), base) == zoom
    assert len(calls) == 1

    # 完整且非天书文案：不重识别（零额外 OCR）
    kiwi = ("任务追踪急人所急[江湖历练任务]快去找到一个猕猴桃(4点雅誉)，"
            "并将其交给黑熊精")
    assert sb._give_text_best(object(), kiwi) == kiwi
    assert len(calls) == 1

    # 天书文案即使看着完整也复核一次：区域 OCR 会整段丢行首绿字
    tianshu = ("任务追踪急人所急[江湖历练任务]快去找到一个天书残卷五"
               "(3点雅誉)，并将其交给彩翼仙子")
    assert sb._give_text_best(object(), tianshu) == zoom
    assert len(calls) == 2


def test_give_plan_uses_canonical_name_for_ocr_alias():
    """已登记物品的 OCR 别名（天外石）→ 给与面板关键词用规范名天外飞石。"""
    from src.device import items
    ent = items.find_by_name("天外石")
    assert ent is not None and ent.key == "tianwaifeishi"
    attempts, _vols = sb._give_plan(
        "快去找到一个天外石（7点雅誉），并将其交给老贾", "天外石", ent)
    assert attempts and attempts[0][0] == "registered"
    assert attempts[0][1] == "天外飞石"
    regs = sb._registered_entries_in_text("快去找到一个天外石（7点雅誉）")
    assert any(e.key == "tianwaifeishi" for e in regs)


def test_give_plan_level3_accessory_box_alternative_wording():
    """「三级不可培养配饰（或三级佩饰盒子）」两种叫法同一件物品。

    2026-11-28 实机：文案写「配饰/佩饰盒子」，给与面板悬停提示名是
    「三级佩饰盒子」（实机提示框 OCR 1.00）。两种写法都必须命中目录并按
    悬停提示名给与，不得落进未登记物品的全格悬停兜底——规范名错一个字
    （配/佩）悬停读名即为空，实机报「未找到候选物品图标」。
    """
    from src.device import items
    text = ("任务追踪急人所急[江湖历练任务]快去找到一个三级不可培养配"
            "饰（或三级佩饰盒子）（2点雅誉)，并将其交给刘家婿")
    phrase = sb.parse_required_item(text)
    entry = items.find_by_name(phrase or text)
    assert entry is not None and entry.key == "sanjipeishihezi"
    attempts, _vols = sb._give_plan(text, phrase, entry)
    assert attempts and attempts[0][0] == "registered"
    assert attempts[0][1] == "三级佩饰盒子"
    # 无「（或…）」写法、直写两种名字的文案同样命中
    for body in ("找到一个三级不可培养配饰(2点雅誉)，并将其交给刘家婶",
                 "找到一个三级不可培养盒子(2点雅誉)，并将其交给刘家婶"):
        ent = items.find_by_name(sb.parse_required_item(body) or body)
        assert ent is not None and ent.key == "sanjipeishihezi"


def test_parse_capture_map_and_match():
    """「到五指山抓回出逃的召唤兽宝宝」→ 目标地图解析/位置匹配。"""
    text = ("贵人相助江湖历练任务到五指山抓回出逃的召唤兽宝宝"
            "（猴宝宝、蟾宫兔宝宝或者赭炎宝宝）并将其带回给主人")
    assert sb.parse_capture_map(text) == "五指山"
    assert sb.parse_capture_map("找到乌鸡国国王交还召唤兽宝宝。") == ""
    assert sb.capture_map_match("五指山", "五指山")
    assert sb.capture_map_match("五指由", "五指山")    # 3 字名容 1 字 OCR 误读
    assert not sb.capture_map_match("五指山", "大唐边境")
    assert not sb.capture_map_match("", "五指山")


def test_capture_tick_arms_and_clears_battle_plan(monkeypatch):
    """捕捉环：追踪出现即注入战斗计划；转为交还阶段 N 轮后清除并复位。"""
    plans = {"plan": None, "cleared": 0}

    def _set(p):
        plans["plan"] = dict(p)

    def _clear():
        plans["plan"] = None
        plans["cleared"] += 1

    monkeypatch.setattr(sb.battle_lib, "watch_in_battle", lambda: False)
    monkeypatch.setattr(sb.battle_lib, "set_battle_plan", _set)
    monkeypatch.setattr(sb.battle_lib, "clear_battle_plan", _clear)

    class _Win:
        def capture(self, force_activate=False):
            return None  # 无帧：状态机等待帧不占用 follow

    sb._capture_reset()
    # 2026-11-27 实机 19:12:03 补点链接帧：追踪行尾「兽宝」被半透明面板
    # 背景吃掉（整行读成「…召唤」）——触发词取行首稳定段，仍须命中
    ring = [("到五指山抓回出逃的召唤", (10, 260, 300, 300), 0.99)]
    assert sb._capture_tick(_Win(), ring) is False
    assert plans["plan"]["kind"] == "capture"
    assert plans["plan"]["target_names"] == sb.CAPTURE_BABY_NAMES
    assert sb._CAP_STATE["armed"] and sb._CAP_STATE["target"] == "五指山"

    turn_in = [("找到乌鸡国国王交还召唤兽宝宝。", (10, 260, 300, 300), 0.99)]
    for _ in range(sb.CAPTURE_ABSENT_RESET - 1):
        sb._capture_tick(_Win(), turn_in)
    assert plans["plan"] is not None        # 未达缺失轮数：计划保持（防战斗帧误清）
    sb._capture_tick(_Win(), turn_in)
    assert plans["plan"] is None and not sb._CAP_STATE["armed"]


def test_capture_stand_uses_coordinate_signal(monkeypatch):
    """走位站定走地图坐标直接信号（2026-11-27 用户实机节奏反馈）。

    五指山位置标签背后是动态场景，WalkGuard 像素差分会持续判「移动」并
    落到 3s+1.2s 坐标复核长路径——人物每次走位后白站 ~3s（实测 6s/次）。
    改用坐标：走动时数字变化、站定时连续相同读数即点下一点。
    """
    sb._capture_reset()
    sb._CAP_STATE.update(phase=1)
    monkeypatch.setattr(sb, "_cap_shared_img", lambda w, i: object())
    monkeypatch.setattr(sb, "dialog_frame_score", lambda img: 0.0)
    clicks = []
    monkeypatch.setattr(sb.app_utils, "human_click",
                        lambda w, x, y, button="left": clicks.append((x, y)))
    pos = {"v": ("五指山", 100, 100)}
    monkeypatch.setattr(sb, "_cap_pos", lambda w, i: pos["v"])
    guard_calls = []
    monkeypatch.setattr(sb._CAP_WALK_GUARD, "walking",
                        lambda *a, **k: guard_calls.append(1) or False)

    items = [("到五指山抓回出逃的召唤", (10, 260, 300, 300), 0.99)]
    now = 1000.0
    assert sb._capture_step_walk(object(), items, now) is False  # 建基线
    pos["v"] = ("五指山", 104, 100)  # 走动中：坐标变化
    assert sb._capture_step_walk(object(), items, now) is False
    assert sb._capture_step_walk(object(), items, now) is False  # 相同 1/2
    assert sb._capture_step_walk(object(), items, now) is True   # 站定即走位
    assert clicks == [sb.CAPTURE_WALK_POINTS[0]]
    assert guard_calls == []  # 坐标可读：不付像素判定/复核长路径
    # 点击后站定计数清零，重新按坐标累计
    assert sb._CAP_STATE["stand_pos"] is None
    assert sb._CAP_STATE["stand_hits"] == 0


def test_capture_stand_falls_back_to_pixel_when_pos_unreadable(monkeypatch):
    """坐标不可读（加载/遮挡）：回退 WalkGuard 像素判定，未知不点世界。"""
    sb._capture_reset()
    sb._CAP_STATE.update(phase=1)
    monkeypatch.setattr(sb, "_cap_shared_img", lambda w, i: object())
    monkeypatch.setattr(sb, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(sb, "_cap_pos", lambda w, i: None)
    clicks = []
    monkeypatch.setattr(sb.app_utils, "human_click",
                        lambda w, x, y, button="left": clicks.append((x, y)))
    walking = {"v": True}
    monkeypatch.setattr(sb._CAP_WALK_GUARD, "walking",
                        lambda *a, **k: walking["v"])
    items = [("到五指山抓回出逃的召唤", (10, 260, 300, 300), 0.99)]
    assert sb._capture_step_walk(object(), items, 1000.0) is False  # 仍判移动
    walking["v"] = None
    assert sb._capture_step_walk(object(), items, 1000.0) is False  # 无法判断
    walking["v"] = False
    assert sb._capture_step_walk(object(), items, 1000.0) is True   # 像素站定
    assert clicks == [sb.CAPTURE_WALK_POINTS[0]]


def test_parse_dig_target_map_and_coords():
    """挖宝：「到白骨山（237，26）藏宝处」→ (地图,x,y)，地图可变。"""
    assert sb.parse_dig_target("到白骨山（237，26）藏宝处") == ("白骨山", 237, 26)
    assert sb.parse_dig_target("到白骨山 (237, 26) 藏宝处") == ("白骨山", 237, 26)
    assert sb.parse_dig_target("到龙窟四层（12，34）藏宝处") == ("龙窟四层", 12, 34)
    assert sb.parse_dig_target("无关文案") is None
    from src.device import items as item_catalog
    assert item_catalog.item("wabaochutou").storage == item_catalog.TASK_BOX


def test_dig_text_ocr_line_wrap():
    """「藏宝」绿字片段行尾丢字仍要认出挖宝环（2026-11-27 实机）。

    实机帧 OCR：「到北俱芦洲（112,35）」+「处使用挖宝锄头挖出宝藏（」，
    「藏宝」被丢掉；给与阶段「将挖出来的宝藏送给X」不得误判。
    """
    wrapped = ("任务追踪意外之喜[江湖历练任务]到北俱芦洲（112,35）"
               "处使用挖宝锄头挖出宝藏（挖宝锄头在任务道具栏内！")
    assert sb._is_dig_text(wrapped) is True
    assert sb.parse_dig_target(wrapped) == ("北俱芦洲", 112, 35)
    turnin = "任务追踪意外之喜[江湖历练任务]将挖出来的宝藏送给小龙王。"
    assert sb._is_dig_text(turnin) is False
    assert sb._is_dig_text("见多识广[江湖历练任务]去长安城找到程咬金") is False


def test_dig_tick_does_not_block_tracking_click(monkeypatch):
    """挖宝未到达时不得占用轮询：交回 follow 点追踪链接寻路。

    2026-11-27 实机：挖宝环出现后人物原地不动——旧实现非到达帧恒返回
    True，wait_handler 占住内层轮询，follow 看不到签名变化、不点追踪。
    到达后还须连续 DIG_STABLE_POLLS 次位置一致（站定）才右键：寻路穿过
    藏宝点瞬间右键会被服务器判「坐标点不对」（同日 22:09 实机；到达后
    降落不是必须的，用户 2026-11-27）。
    """
    clock = _Clock()
    monkeypatch.setattr(sb, "time", clock)
    sb._DIG_STATE.update(key=None, used=False, used_at=0.0,
                         last_pos_at=0.0, last_try=0.0, last_pos=None,
                         stable=0, tries=0, escalated=False)
    items = [("到方寸后山（39，50）藏宝处", (60, 300, 330, 330), 0.95)]
    used = []

    def _use(*_a, **_k):
        used.append(1)
        return True, "已使用挖宝锄头"

    monkeypatch.setattr(sb.item_use, "use_task_box_item", _use)
    pos = {"v": ("长安城", 100, 100)}
    monkeypatch.setattr(sb.game_map, "read_map_position",
                        lambda w, img=None: pos["v"])

    # 未到达：False（follow 继续正常跟踪、点追踪链接），不碰任务储物格
    assert sb._dig_tick(object(), items, object()) is False
    assert used == []

    # 首次读到到达：站定计数 1 < DIG_STABLE_POLLS，不右键
    pos["v"] = ("方寸后山", 39, 50)
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is False
    assert used == []

    # 位置连续一致（站定）：右键锄头一次，本帧返回 True（帧已过期）
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is True
    assert len(used) == 1

    # 用过之后不重复右键，交回 follow 等待任务推进/消失
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is False
    assert len(used) == 1


def test_dig_tick_rejected_dig_retries(monkeypatch):
    """服务器回「坐标点不对」：解除已用抑制，站定后重新右键。"""
    clock = _Clock()
    monkeypatch.setattr(sb, "time", clock)
    sb._DIG_STATE.update(key=None, used=False, used_at=0.0,
                         last_pos_at=0.0, last_try=0.0, last_pos=None,
                         stable=0, tries=0, escalated=False)
    items = [("到北俱芦洲（112,35）处使用挖宝锄头挖出宝藏（",
              (20, 247, 222, 290), 0.95)]
    used = []
    monkeypatch.setattr(
        sb.item_use, "use_task_box_item",
        lambda *_a, **_k: (used.append(1), (True, "已使用"))[1])
    monkeypatch.setattr(sb.game_map, "read_map_position",
                        lambda w, img=None: ("北俱芦洲", 112, 35))

    # 站定 2 次 → 右键一次（未站定时不右键）
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is False  # 站定 1
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is True   # 右键
    assert len(used) == 1

    # 拒绝对话框入场：used 解除；对话框在帧上时不右键
    fail_items = items + [("坐标点不对", (500, 380, 560, 400), 0.99)]
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), fail_items, object()) is False
    assert sb._DIG_STATE["used"] is False

    # 对话框消失、站定恢复后重试（DIG_RETRY_INTERVAL 后）
    clock.sleep(sb.DIG_RETRY_INTERVAL)
    assert sb._dig_tick(object(), items, object()) is False  # 站定计数 1
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is True
    assert len(used) == 2


# ---- 答题类（千古一题） ----

def _quiz_qitems(question):
    """合成答题帧 OCR：问题行 + 4 个选项 + 世界浮名（不参与）。"""
    return [
        (question, [436, 382, 546, 403], 0.99),
        ("都尉朝天跃马归", [434, 400, 544, 421], 1.0),
        ("青云之交不可攀", [434, 417, 544, 438], 1.0),
        ("银鞍紫轻照云日", [435, 433, 544, 454], 0.9),
        ("扶摇直上九万里", [434, 450, 544, 472], 0.997),
        ("幽冥地鬼", [620, 500, 700, 520], 1.0),
    ]


def _patch_quiz(monkeypatch, qitems):
    calls = {"safe": [], "once": []}
    option_txts = {t for t, _r, _s in qitems if t.startswith(("都尉", "青云",
                                                              "银鞍", "扶摇"))}
    monkeypatch.setattr(sb, "dialog_frame_score", lambda _img: 0.99)
    monkeypatch.setattr(sb.game_map, "ocr_region",
                        lambda _img, _region: (qitems, None))
    monkeypatch.setattr(sb.task_tracker, "is_green_text",
                        lambda _img, _rect: any(
                            t in option_txts and _rect == r
                            for t, r, _s in qitems))
    monkeypatch.setattr(sb, "click_green_option_safe",
                        lambda _w, _its, _img, text, prefix="":
                        calls["safe"].append(text) or True)
    monkeypatch.setattr(sb, "_click_green_option_once",
                        lambda _w, _its, _img, text, prefix="":
                        calls["once"].append(text) or True)
    return calls


def test_quiz_tick_answers_known_poem(monkeypatch):
    """白字问上一句 + 绿字选项 → 点内置的下一句；世界浮名不参与。"""
    calls = _patch_quiz(monkeypatch, _quiz_qitems("大鹏一日同风起"))
    items = [("·千古一题[江湖历练任务]", [9, 230, 191, 252], 0.997)] \
        + _quiz_qitems("大鹏一日同风起")
    assert sb._quiz_tick(None, items, object()) is True
    assert calls == {"safe": ["扶摇直上九万里"], "once": []}


def test_quiz_tick_starts_intro_and_random_fallback(monkeypatch):
    """开场白点开始选项；诗句未收录时随机点一个绿色选项。"""
    calls = _patch_quiz(monkeypatch, _quiz_qitems("甲乙丙丁戊己庚"))
    intro = [
        ("·千古一题[江湖历练任务]", [9, 230, 191, 252], 0.997),
        ("呵，原来是来寻求答案的，那也是知道的规矩了！来吧，回答我的千古三题",
         [435, 381, 940, 401], 0.999),
        ("这人到底靠谱不……也罢横竖一试", [435, 417, 664, 437], 0.973),
        ("找你有其他事情", [435, 435, 544, 456], 0.999),
    ]
    assert sb._quiz_tick(None, intro, object()) is True
    assert calls["safe"] == ["这人到底靠谱不"]

    monkeypatch.setattr(sb.random, "choice", lambda seq: seq[1])
    items = [("·千古一题[江湖历练任务]", [9, 230, 191, 252], 0.997)] \
        + _quiz_qitems("甲乙丙丁戊己庚")
    assert sb._quiz_tick(None, items, object()) is True
    assert calls["once"] == ["青云之交不可攀"]


# ---- 追踪签名（见多识广环目标切换，2026-11-27 实机） ----

def _track_items(title, first_line):
    return [(title, (30, 250, 210, 274), 0.99),
            (first_line, (39, 268, 330, 290), 0.99)]


def test_visit_ring_signature_detects_target_change():
    """见多识广环推进只改行首 NPC 名：签名须检出（默认标题+首行签名
    相似度 0.818/0.909 被当 OCR 抖动过滤，实机每次多等 15s 看门狗）；
    单字误读仍按未变化过滤（不重复点链接）；非拜访环沿用默认签名。"""
    from src.workflows.follow import _signatures_similar

    def sig(line):
        return sb._track_sig_key(
            _track_items("见多识广[江湖历练任务]", line), None)

    a = sig("女儿国国王见多识广，前往拜")
    b = sig("灵儿见多识广，前往拜访一下")
    c = sig("仙女见多识广，前往拜访一下")
    assert a and b and c
    assert not _signatures_similar(a, b)     # 女儿国国王 → 灵儿
    assert not _signatures_similar(b, c)     # 灵儿 → 仙女
    assert _signatures_similar(b, sig("灵几见多识广，前往拜访一下"))

    # 环名不在首行的环：默认「标题+首行」签名（保留行尾目标 NPC）
    give = sb._track_sig_key(_track_items(
        "急人所急[江湖历练任务]",
        "快去找到一个9-12属性的炼妖石(2点雅誉），并将其交给蟹将"), None)
    assert "蟹将" in give


# ---- 庭院打造类给与（灵宝物品，用户 2026-11-27） ----

def _give_track_items(first, second):
    return [("急人所急[江湖历练任务]", (30, 231, 189, 253), 0.994),
            (first, (30, 252, 220, 270), 0.918),
            (second, (30, 268, 199, 289), 0.966)]


def test_courtyard_craft_key_craftable_give():
    """可打造给与环解析出（物品, 目标）；非可打造/非给与不触发。"""
    # 灵宝前缀：未登记灵宝物品也去打造
    key, item, target = sb._courtyard_craft_key(_give_track_items(
        "快去找到一个灵宝天威印(7", "点雅誉），并将其交给莽汉"))
    assert item == "灵宝天威印" and target == "莽汉"
    assert key == (sb.norm_text("灵宝天威印"), sb.norm_text("莽汉"))
    # 属性前缀的可打造物品（2026-11-27 实机忆梦符）：键剥离「属性13的」
    key2, item2, target2 = sb._courtyard_craft_key(_give_track_items(
        "快去找到一个属性13的忆梦符(9", "点雅誉），并将其交给饲马小仙"))
    assert item2 == "属性13的忆梦符" and target2 == "饲马小仙"
    assert key2 == (sb.norm_text("忆梦符"), sb.norm_text("饲马小仙"))
    # 非可打造物品（天书残卷）不触发
    assert sb._courtyard_craft_key(_give_track_items(
        "快去找到一个天书残卷三(3", "点雅誉），并将其交给高小姐"))[0] is None
    # 类别型属性给与（炼妖石）不在目录登记 craftable，不触发
    assert sb._courtyard_craft_key(_give_track_items(
        "快去找到一个属性13的炼妖石(2", "点雅誉），并将其交给梅花仙"))[0] is None
    # 灵宝物品但没有给与目标（非给与环）不触发
    assert sb._courtyard_craft_key(_give_track_items(
        "去长安看看灵宝天威印", "这宝贝真不错"))[0] is None


def test_courtyard_craft_guard_once_per_key(monkeypatch):
    """同一物品+目标只打造一次；目标变化或文案切走再回来会重新打造。"""
    calls = []
    monkeypatch.setattr(sb.courtyard_craft, "craft_item",
                        lambda win, item, prefix="":
                        calls.append(item) or (True, "已打造"))
    sb._CRAFT_TASK_STATE.update(key=None, done=False, fails=0)
    items = _give_track_items("快去找到一个灵宝天威印(7",
                              "点雅誉），并将其交给莽汉")
    sb._courtyard_craft_guard(None, items, None)
    sb._courtyard_craft_guard(None, items, None)
    assert calls == ["灵宝天威印"]
    # 给与目标变化：上一件已交给前一个 NPC，需要再打造
    sb._courtyard_craft_guard(None, _give_track_items(
        "快去找到一个灵宝天威印(7", "点雅誉），并将其交给老猴精"), None)
    assert calls == ["灵宝天威印", "灵宝天威印"]
    # 文案切到非打造环：状态复位；再回到同一键重新打造
    sb._courtyard_craft_guard(None, _give_track_items(
        "去洛阳城找萧举人", "继续周游江湖"), None)
    assert sb._CRAFT_TASK_STATE["key"] is None
    sb._courtyard_craft_guard(None, items, None)
    assert calls == ["灵宝天威印"] * 3


def test_courtyard_craft_guard_retries_then_escalates(monkeypatch):
    """打造失败按次数重试；连续失败累计 fails（3 次升级 ERROR 由日志层）。"""
    calls = []
    monkeypatch.setattr(sb.courtyard_craft, "craft_item",
                        lambda win, item, prefix="":
                        calls.append(item) or (False, "面板未出现"))
    monkeypatch.setattr(sb.time, "sleep", lambda _s: None)
    sb._CRAFT_TASK_STATE.update(key=None, done=False, fails=0)
    items = _give_track_items("快去找到一个灵宝天威印(7",
                              "点雅誉），并将其交给莽汉")
    sb._courtyard_craft_guard(None, items, None)
    assert len(calls) == sb.COURTYARD_CRAFT_ATTEMPTS
    assert sb._CRAFT_TASK_STATE["fails"] == 1
    sb._courtyard_craft_guard(None, items, None)
    assert sb._CRAFT_TASK_STATE["fails"] == 2


def test_capture_first_walk_after_landing_is_immediate(monkeypatch):
    """降落完成即站定：首次走位不等坐标复核，直接右键走位。

    用户 2026-11-27 实机：降落成功 21:40:01 → 首次右键 21:40:05，白等
    4s；降落本身不会遇敌，人物落地即站立，直接点走位点即可。后续走位
    仍按坐标站定判定（走动中不点世界）。
    """
    sb._capture_reset()
    sb._CAP_STATE.update(phase=1, first_walk=True)
    monkeypatch.setattr(sb, "_cap_shared_img", lambda w, i: object())
    monkeypatch.setattr(sb, "dialog_frame_score", lambda img: 0.0)
    clicks = []
    monkeypatch.setattr(sb.app_utils, "human_click",
                        lambda w, x, y, button="left": clicks.append((x, y)))
    stand_calls = []
    monkeypatch.setattr(sb, "_capture_stand_update",
                        lambda pos: stand_calls.append(pos) or False)
    items = [("到五指山抓回出逃的召唤", (10, 260, 300, 300), 0.99)]
    assert sb._capture_step_walk(object(), items, 1000.0) is True
    assert clicks == [sb.CAPTURE_WALK_POINTS[0]]
    assert stand_calls == []          # 首次走位不付坐标站定等待
    assert not sb._CAP_STATE["first_walk"]
    # 第二次走位回到常规站定判定（移动中 → 不点）
    monkeypatch.setattr(sb, "_cap_pos", lambda w, i: ("五指山", 104, 100))
    assert sb._capture_step_walk(object(), items, 1002.5) is False
    assert len(clicks) == 1


# ---- 给与环追踪链接定位（_give_link_picker，2026-11-28 实机）----
# 实机帧型：物品名与目标名都是亮绿文字，物品名无下划线（点了不寻路）；
# 「灵儿」换行拆成行尾「灵」+ 下一行「儿」，单字下划线 15px 低于
# has_underline 的 20px 阈值，行级选择按亮绿占比点中物品链接，人物
# 原地站了 2 分钟（2026-11-28 00:27:54~00:28:45 日志）。

def _give_picker_frame():
    """物品行亮绿无下划线 + 给与行行首「石」/行尾「灵」（15px 下划线）。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[254:268, 122:218:4] = (0, 255, 0)   # 物品行「属性13的炼妖」笔画
    img[272:285, 25:38:4] = (0, 255, 0)     # 给与行行首「石」（物品名续行）
    img[271:285, 205:218:4] = (0, 255, 0)   # 给与行行尾「灵」
    img[287, 204:219] = (0, 255, 0)         # 「灵」下划线（15px，短于 20px）
    return img


def _give_picker_items():
    return [
        ("急人所急[江湖历练任务]", [13, 231, 190, 252], 0.99),
        ("快去找到一个属性13的炼妖", [22, 251, 219, 271], 1.0),
        ("石(5点雅誉)，并将其交给灵", [36, 266, 222, 290], 0.98),
    ]


def test_give_link_picker_clicks_target_not_item_link():
    """给与环点「交给」后的目标名（灵儿），不点物品名链接。

    修复前行级选择点物品行中点 (210,265)（亮绿占比 9.97% > 给与行
    8.67%），点击不触发寻路；修复后落点在目标名「灵」上 (211,285)。
    """
    assert sb._give_link_picker(_give_picker_items(), _give_picker_frame()) \
        == (211, 285)


def test_give_link_picker_target_wrapped_to_next_row():
    """子句行以「交给」结尾（目标名整段换到下一行）：点下一行目标名，
    不点子句行行首的物品名碎片「石」。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[254:268, 122:200:4] = (0, 255, 0)   # 物品行笔画
    img[272:285, 25:38:4] = (0, 255, 0)     # 子句行行首「石」
    img[293:306, 28:116:4] = (0, 255, 0)    # 下一行「彩翼仙子」
    img[308, 28:117] = (0, 255, 0)          # 目标名整行下划线
    items = [
        ("急人所急[江湖历练任务]", [13, 231, 190, 252], 0.99),
        ("快去找到一个猕猴桃(4点雅", [22, 251, 219, 271], 1.0),
        ("誉)，并将其交给", [36, 266, 222, 290], 0.98),
        ("彩翼仙子", [24, 291, 120, 310], 0.99),
    ]
    assert sb._give_link_picker(items, img) == (72, 306)


def test_give_link_picker_none_for_non_give_ring():
    """非给与环（挖宝送达）无给与子句：返回 None，走原行级选择。"""
    items = [
        ("意外之喜[江湖历练任务]", [13, 231, 190, 252], 0.99),
        ("将挖出来的宝藏送给青龙长老", [20, 251, 219, 271], 0.99),
    ]
    assert sb._give_link_picker(items, _give_picker_frame()) is None


def test_give_link_picker_ignores_float_row_outside_column():
    """面板左列外的浮字行（同文案、亮绿连续块）不参与子句定位与点击
    （2026-11-08 玄枢轶闻 / 2026-11-19 五环浮字劫持同款教训）。"""
    img = _give_picker_frame()
    img[252:269, 202:330] = (0, 255, 0)     # 浮字绿色块
    items = _give_picker_items() + [
        ("快去找到一个神秘人并将其交给浮名", [202, 250, 330, 274], 0.98),
    ]
    assert sb._give_link_picker(items, img) == (211, 285)


# ---- 画符/开锁关窗重走：对话或寻路中途不得点追踪链接 ----
# 2026-11-28 实机死循环：关窗后的 NPC 台词链由 follow 推进，10s 间隔的
# 重走点击落在对话中途 → 整段任务对话从头重放（重走第 1~19 次、对话框
# 反复回到「雷鸟」第一页）。重走必须先过 _track_restart_idle 静默门。

def _restart_env(monkeypatch, score, walking):
    calls = []
    monkeypatch.setattr(sb, "dialog_frame_score", lambda img: score)
    monkeypatch.setattr(sb._RESTART_WALK, "walking",
                        lambda win=None, img=None: walking)
    monkeypatch.setattr(sb.task_tracker, "track_task",
                        lambda *a, **k: (calls.append(1), (True, ""))[1])
    monkeypatch.setattr(sb.app_utils, "mark_track_walk", lambda: None)
    return calls


def test_huafu_restart_skips_while_dialog_open(monkeypatch):
    """对话框在场：台词/选项正由 follow 推进，点链接会重放整段对话。"""
    calls = _restart_env(monkeypatch, sb.DIALOG_FRAME_THRESHOLD + 0.1, False)
    sb._huafu_restart(object(), object())
    assert calls == []


def test_huafu_restart_skips_while_walking(monkeypatch):
    """人物移动中（上一次重走寻路未到站）：再点只会重复起步。"""
    calls = _restart_env(monkeypatch, 0.1, True)
    sb._huafu_restart(object(), object())
    assert calls == []


def test_huafu_restart_clicks_when_idle(monkeypatch):
    """无对话框且人物站定：允许重走并进入 10s 退避。"""
    clock = _Clock()
    monkeypatch.setattr(sb, "time", clock)
    calls = _restart_env(monkeypatch, 0.1, False)
    sb._huafu_restart(object(), object())
    assert calls == [1]
    assert sb._HUAFU_STATE["next_at"] == clock.t + sb.HUAFU_RESTART_INTERVAL


def test_jiesuo_restart_uses_idle_gate(monkeypatch):
    """开锁关窗重开同款静默门：对话/寻路中途不点追踪链接。"""
    calls = _restart_env(monkeypatch, sb.DIALOG_FRAME_THRESHOLD + 0.1, False)
    monkeypatch.setattr(sb.jiesuo_mini, "is_ready", lambda bgr: False)
    sb._JIESUO_STATE.update(pending=True, next_at=0.0, since=1000.0,
                            restarts=0, reopened=True, last_panel_at=0.0)
    clock = _Clock()
    monkeypatch.setattr(sb, "time", clock)
    assert sb._jiesuo_tick(object(), object(), [], object()) is False
    assert calls == []
    # 对话结束回到空闲帧：放行重走
    monkeypatch.setattr(sb, "dialog_frame_score", lambda img: 0.1)
    clock.sleep(sb.JIESUO_RESTART_INTERVAL)
    assert sb._jiesuo_tick(object(), object(), [], object()) is False
    assert calls == [1]
