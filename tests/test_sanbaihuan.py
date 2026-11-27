# -*- coding: utf-8 -*-
"""300环（江湖历练）任务核心路径单测。

只覆盖三件事：
1. 任务栏状态判定（有环数标记=续跑，无标记=领取）；
2. 按状态选择萧举人对话框选项；
3. 萧举人对话框无标准边框，立绘模板门控（低分帧不 OCR）。
"""
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
    assert sb.parse_give_target("无关文案") == ""


def test_parse_dig_target_map_and_coords():
    """挖宝：「到白骨山（237，26）藏宝处」→ (地图,x,y)，地图可变。"""
    assert sb.parse_dig_target("到白骨山（237，26）藏宝处") == ("白骨山", 237, 26)
    assert sb.parse_dig_target("到白骨山 (237, 26) 藏宝处") == ("白骨山", 237, 26)
    assert sb.parse_dig_target("到龙窟四层（12，34）藏宝处") == ("龙窟四层", 12, 34)
    assert sb.parse_dig_target("无关文案") is None
    from src.device import items as item_catalog
    assert item_catalog.item("wabaochutou").storage == item_catalog.TASK_BOX


def test_dig_tick_does_not_block_tracking_click(monkeypatch):
    """挖宝未到达时不得占用轮询：交回 follow 点追踪链接寻路。

    2026-11-27 实机：挖宝环出现后人物原地不动——旧实现非到达帧恒返回
    True，wait_handler 占住内层轮询，follow 看不到签名变化、不点追踪。
    """
    clock = _Clock()
    monkeypatch.setattr(sb, "time", clock)
    sb._DIG_STATE.update(key=None, used=False, used_at=0.0,
                         last_pos_at=0.0, last_try=0.0)
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

    # 到达藏宝处：右键锄头一次，本帧返回 True（帧已过期，不再交给 follow）
    pos["v"] = ("方寸后山", 39, 50)
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is True
    assert len(used) == 1

    # 用过之后不重复右键，交回 follow 等待任务推进/消失
    clock.sleep(sb.DIG_POS_POLL)
    assert sb._dig_tick(object(), items, object()) is False
    assert len(used) == 1


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
