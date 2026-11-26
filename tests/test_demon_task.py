# -*- coding: utf-8 -*-
"""降魔任务识别单测（纯文本解析，不触真实游戏）。

样例取自用户 2026-11-23 实机截图的任务栏 OCR（任务详情原文）：
- 三藏逢魔（四圣庄 / 万寿山两种变体）：击杀类
- 殿前献艺（暗雷/锁妖网）：需重新领取
- 宝象迷情（显形镜 A/B）：显形镜类
- 智斗黄袍（黄袍怪多轮）：黄袍怪类
"""
from src.device import demon_task as dt


# 实机 OCR 文本（按行拼接；task_panel.objective_lines 的输出）
KILL_SISHENG = (
    "三藏黑松林迷路，误入黄袍怪老巢，性"
    "命危急。义士速赶至四圣庄，消灭神通"
    "魔尊(四圣庄，163，105)，为营救三藏而"
    "战！"
)
KILL_WANSHOU = (
    "三藏黑松林迷路，误入黄袍怪老巢，性"
    "命危急。义士速赶至万寿山，消灭天眼"
    "魔尊(万寿山，37，11)，为营救三藏而战"
)
XIANXING = (
    "前往平顶山的(187，27)，(269，28)几处"
    "使用显形镜查探。"
)
ANLEI = (
    "义士且去平顶山或火云戈壁，使用道具"
    "栏的锁妖网，与队友合力捕捉3只暗雷怪"
    "(呆头小虫、粗放大鬼、西域哑马)。【"
    "吃/不吃摄妖香均可触发任务暗雷，战斗"
    "胜利才算捕捉成功哦】。"
)
HUANGPAO = (
    "前往平顶山（302，22）消灭愤怒的黄袍"
    "怪。"
    "智斗黄袍为限时通关任务，一旦切入战"
    "斗，请在7分钟内完成尽可能多轮次的挑"
    "战（最多5轮）。"
)
# 奎星归位（奎木狼）：目标名紧邻括号地图坐标、无击杀动词（用户
# 2026-11-25 实机截图，任务栏 OCR 原文）
KUIXING_GUIWEI = (
    "奎木狼(火云戈壁，134，12)终于要出动了，"
    "与他一决雌雄吧！"
)
# 修罗任务（用户 2026-11-26 实机截图）：任务栏「常规玩法」分组下的
# 单杀任务；任务目的为层级图坐标文案
XIULUO_FENGCHAO = "前往凤巢六层(84,55)处消灭修罗。"
XIULUO_DAYANTA = "请速去大雁塔四层(108, 37)处消灭修罗，阻止它为非作歹。"


def test_kill_task_sishengzhuang():
    rec = dt.recognize("三藏逢魔", KILL_SISHENG)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["reclaim"] is False
    assert rec["map"] == "四圣庄"
    assert rec["coords"] == [(163, 105)]
    assert rec["coord_a"] == (163, 105) and rec["coord_b"] is None
    assert rec["target"] == "神通魔尊"
    assert rec["max_rounds"] == 1  # 击杀类每轮 1 场战斗
    assert rec["item"] is None and rec["item_icon"] is None


def test_kill_task_wanshoushan():
    rec = dt.recognize("三藏逢魔", KILL_WANSHOU)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["map"] == "万寿山"
    assert rec["coords"] == [(37, 11)]
    assert rec["target"] == "天眼魔尊"


def test_xianxing_task_two_coords():
    rec = dt.recognize("宝象迷情", XIANXING)
    assert rec["kind"] == dt.KIND_XIANXING
    assert rec["reclaim"] is False
    assert rec["map"] == "平顶山"
    assert rec["coords"] == [(187, 27), (269, 28)]
    assert rec["coord_a"] == (187, 27)
    assert rec["coord_b"] == (269, 28)
    assert rec["item"] == "显形镜"
    assert rec["item_icon"] == "wubei_xianxingjing.png"
    assert rec["item_location"] == "task_box"


def test_xianxing_ocr_variant_dou():
    """OCR 把「显」读成「豆」：取「形镜」子串仍识别。"""
    text = "前往平顶山的(187,27)，(269,28)几处使用豆形镜查探。"
    rec = dt.recognize("宝象迷情", text)
    assert rec["kind"] == dt.KIND_XIANXING
    assert rec["coords"] == [(187, 27), (269, 28)]


def test_xianxing_single_coord():
    text = "前往平顶山的(187,27)使用显形镜查探。"
    rec = dt.recognize("宝象迷情", text)
    assert rec["kind"] == dt.KIND_XIANXING
    assert rec["coord_a"] == (187, 27) and rec["coord_b"] is None


def test_huangpao_task():
    rec = dt.recognize("智斗黄袍", HUANGPAO)
    assert rec["kind"] == dt.KIND_HUANGPAO
    assert rec["reclaim"] is False
    assert rec["map"] == "平顶山"
    assert rec["coords"] == [(302, 22)]
    assert rec["coord_a"] == (302, 22) and rec["coord_b"] is None
    assert rec["target"] == "愤怒的黄袍怪"
    assert rec["max_rounds"] == 5  # 文案「最多5轮」，日志实测 5 场
    assert "黄袍怪多轮任务" in dt.describe(rec)
    assert "轮次=5" in dt.describe(rec)


def test_huangpao_rounds_parsed_from_text():
    """黄袍轮次数优先取任务文案「最多N轮」，文案缺失默认 5。"""
    rec = dt.recognize("智斗黄袍",
                       "前往平顶山(1,2)消灭黄袍怪，最多3轮。")
    assert rec["max_rounds"] == 3
    rec2 = dt.recognize("智斗黄袍", "前往平顶山(1,2)消灭黄袍怪。")
    assert rec2["max_rounds"] == dt.HUANGPAO_DEFAULT_ROUNDS == 5


def test_kill_task_kuixing_guiwei_kuimulang():
    """奎星归位：目标名紧邻括号地图坐标、无击杀动词，杀一次（2026-11-25）。"""
    rec = dt.recognize("奎星归位", KUIXING_GUIWEI)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["reclaim"] is False
    assert rec["map"] == "火云戈壁"
    assert rec["coords"] == [(134, 12)]
    assert rec["coord_a"] == (134, 12) and rec["coord_b"] is None
    assert rec["target"] == "奎木狼"
    assert rec["max_rounds"] == 1  # 用户确认：同样是杀一次
    assert "击杀任务" in dt.describe(rec)


def test_kill_kuimulang_ocr_variants():
    """OCR 变体：地图/坐标间无逗号、缺右括号、名字前带叙述与动词。"""
    for text in (
        "奎木狼(火云戈壁134,12)终于要出动了，与他一决雌雄吧！",
        "奎木狼(火云戈壁，134，12终于要出动了，与他一决雌雄吧",
        "义士速赶至火云戈壁消灭奎木狼(火云戈壁，134，12)！",
    ):
        rec = dt.recognize("奎星归位", text)
        assert rec["kind"] == dt.KIND_KILL
        assert rec["map"] == "火云戈壁"
        assert rec["coords"] == [(134, 12)]
        assert rec["target"] == "奎木狼"


def test_xiuluo_task_kill_once_layer_map():
    """修罗任务：单杀（轮次 1），层级图地名整名保留（不归一成「凤巢」）。"""
    rec = dt.recognize("修罗任务", XIULUO_FENGCHAO)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["reclaim"] is False
    assert rec["map"] == "凤巢六层"
    assert rec["coords"] == [(84, 55)]
    assert rec["coord_a"] == (84, 55) and rec["coord_b"] is None
    assert rec["target"] == "修罗"
    assert rec["max_rounds"] == 1  # 用户确认：修罗都是只杀一次
    assert "击杀任务" in dt.describe(rec)
    assert "轮次=1" in dt.describe(rec)


def test_xiuluo_claim_dialog_text_keeps_layer():
    """领取对话框文案（请速去…）：坐标前最近地名层号不丢。"""
    rec = dt.recognize("修罗任务", XIULUO_DAYANTA)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["map"] == "大雁塔四层"
    assert rec["coords"] == [(108, 37)]
    assert rec["target"] == "修罗"
    assert rec["max_rounds"] == 1


def test_xiuluo_task_name_fallback_without_target_text():
    """任务目的丢失击杀句时按任务名「修罗任务」兜底判击杀（单杀）。"""
    rec = dt.recognize("修罗任务", "前往凤巢六层(84,55)处。")
    assert rec["kind"] == dt.KIND_KILL
    assert rec["map"] == "凤巢六层"
    assert rec["coords"] == [(84, 55)]
    assert rec["target"] is None
    assert rec["max_rounds"] == 1


def test_xiuluo_hint_text_does_not_change_result():
    """任务提示段（每日共享次数/混沌之气）不参与识别：结果不受影响。"""
    text = XIULUO_FENGCHAO + (
        "任务提示：1.小鬼/天庭/鬼王/修罗每日共享任"
        "务次数，一共为90次；2.每日任务次数小于90次时，击败修罗消耗"
        "1点任务次数并消耗2点混沌之气；3.前往灵兽村找巫医净化混沌之气，"
        "净化成功则获得正常修罗奖励。")
    rec = dt.recognize("修罗任务", text)
    assert rec["kind"] == dt.KIND_KILL
    assert rec["map"] == "凤巢六层"
    assert rec["coords"] == [(84, 55)]
    assert rec["target"] == "修罗"
    assert rec["max_rounds"] == 1


def test_name_before_coord_needs_known_kill_task():
    """未知任务名：同样句式不误判击杀，地图/坐标仍照常返回给调用方。"""
    rec = dt.recognize("未知任务", KUIXING_GUIWEI)
    assert rec["kind"] == dt.KIND_UNKNOWN
    assert rec["map"] == "火云戈壁" and rec["coords"] == [(134, 12)]
    assert rec["target"] is None


def test_sancang_not_misjudged_as_huangpao():
    """三藏逢魔文案含「黄袍怪老巢」是叙述，不得误判为黄袍怪多轮任务。"""
    for text in (KILL_SISHENG, KILL_WANSHOU):
        rec = dt.recognize("三藏逢魔", text)
        assert rec["kind"] == dt.KIND_KILL
        assert rec["target"] in ("神通魔尊", "天眼魔尊")


def test_reclaim_anlei():
    rec = dt.recognize("殿前献艺", ANLEI)
    assert rec["kind"] == dt.KIND_RECLAIM
    assert rec["reclaim"] is True
    assert "暗雷" in rec["reclaim_reason"]
    assert rec["coords"] == []


def test_reclaim_by_task_name_fallback():
    """目标文案 OCR 失败时按已知任务名「殿前献艺」兜底判重领。"""
    rec = dt.recognize("殿前献艺", "任务目的无法识别")
    assert rec["kind"] == dt.KIND_RECLAIM
    assert rec["reclaim"] is True


def test_unknown_task_keeps_coords():
    text = "前往某地的(12,34)做点什么。"
    rec = dt.recognize("未知任务", text)
    assert rec["kind"] == dt.KIND_UNKNOWN
    assert rec["reclaim"] is False
    assert rec["map"] == "某地"
    assert rec["coords"] == [(12, 34)]


def test_coord_ocr_variants():
    # 半角括号/逗号 + 缺右括号 + 地图形近字（平项山 → 平顶山）
    text = "前往平项山的(187,27),(269,28几处使用显形镜查探"
    rec = dt.recognize("宝象迷情", text)
    assert rec["kind"] == dt.KIND_XIANXING
    assert rec["map"] == "平顶山"
    assert rec["coords"] == [(187, 27), (269, 28)]


def test_describe_lines():
    kill = dt.recognize("三藏逢魔", KILL_SISHENG)
    s = dt.describe(kill)
    assert "击杀任务" in s and "四圣庄" in s and "神通魔尊" in s
    xj = dt.recognize("宝象迷情", XIANXING)
    s = dt.describe(xj)
    assert "显形镜任务" in s and "A=(187, 27)" in s and "B=(269, 28)" in s
    rec = dt.recognize("殿前献艺", ANLEI)
    assert "需重新领取" in dt.describe(rec)
    assert "未识别" in dt.describe(dt.recognize("未知", "随便什么"))
