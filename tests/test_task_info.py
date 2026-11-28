# -*- coding: utf-8 -*-
"""任务追踪结构化解析 + 雅誉优先排序（2026-11-27 用户要求）。

覆盖：追踪面板 → 任务名称/所需物品结构化；天书卷号展开；雅誉高/低优先。
"""
from src.device import task_info


def test_parse_track_task_structured_give():
    """追踪内容结构化：任务名称 + 所需物品（千年魂石 40 / 天书 1或2 各 3）。"""
    items = [
        ("·急人所急[江湖历练任务]", (14, 232, 189, 251), 0.998),
        ("快去找到一个千年魂石(40点", (23, 252, 220, 271), 0.992),
        ("雅誉）或天书残卷一或二（3点", (22, 267, 222, 289), 0.950),
        ("雅誉)，并将其交给梅花仙", (22, 285, 198, 307), 0.958),
    ]
    task = task_info.parse_track_task(items)
    assert task.name == "急人所急"
    assert task.category == "江湖历练任务"
    assert [(o.name, o.honor) for o in task.offers] == [
        ("千年魂石", 40), ("天书残卷一", 3), ("天书残卷二", 3)]
    assert task.offers_text() == (
        "千年魂石(40雅誉)、天书残卷一(3雅誉)、天书残卷二(3雅誉)")
    data = task.to_dict()
    assert data["name"] == "急人所急"
    assert data["offers"] == [
        {"name": "千年魂石", "honor": 40},
        {"name": "天书残卷一", "honor": 3, "volumes": [1]},
        {"name": "天书残卷二", "honor": 3, "volumes": [2]}]
    # 非本玩法 / 空输入 → None（不误触发给与结构化）
    assert task_info.parse_track_task([]) is None
    other = [("·某某[常规玩法]", (14, 232, 189, 251), 0.99)]
    assert task_info.parse_track_task(other) is None


def test_order_give_offers_high_low():
    """高雅誉值优先 → 40 在前；低雅誉值优先 → 3 在前。"""
    offers = task_info.parse_give_offers(
        "找到一个千年魂石(40点雅誉)或天书残卷一或二(3点雅誉)")
    assert [o.name for o in task_info.order_give_offers(offers, True)] == [
        "千年魂石", "天书残卷一", "天书残卷二"]
    assert [o.name for o in task_info.order_give_offers(offers, False)] == [
        "天书残卷一", "天书残卷二", "千年魂石"]


def test_parse_give_offers_variants():
    """天书卷号展开/「获得N点雅誉」/区间属性文案都能结构化。"""
    assert [(o.name, o.honor) for o in task_info.parse_give_offers(
        "找到一个天书残卷三或十（3点雅誉），并将其交给高小姐")] == [
        ("天书残卷三", 3), ("天书残卷十", 3)]
    assert [(o.name, o.honor) for o in task_info.parse_give_offers(
        "找到属性13的炼妖石(获得5点雅誉)，并给予梅花仙")] == [
        ("属性13的炼妖石", 5)]
    assert task_info.parse_give_offers("无关文案") == []


def test_parse_volumes_shared():
    """卷号解析与任务侧共用同一份实现。"""
    assert task_info.parse_volumes("天书残卷三或十") == {3, 10}
    assert task_info.parse_volumes("天书残卷（三）或（十）") == {3, 10}
    # 2026-11-27 实机「天书残卷五、七、八或九」任意一卷可交（旧正则只出 {5}）
    assert task_info.parse_volumes(
        "快去找到一个天书残卷五、七、八或九（3点雅誉），并将其交给彩翼仙子"
    ) == {5, 7, 8, 9}
    # 行首绿字分隔符「、」被 OCR 丢掉（换行后读「八或九…」）也不能断：
    # 拼接成「五、七八或九」仍要解析出全部四卷
    assert task_info.parse_volumes(
        "快去找到一个天书残卷五、七八或九(3点雅誉)，并将其交给彩翼仙子"
    ) == {5, 7, 8, 9}
    # 括号雅誉值里的阿拉伯数字不得混入卷号
    assert task_info.parse_volumes("天书残卷五、七（3点雅誉）") == {5, 7}
    assert task_info.volume_of("天书残卷 第三卷") == 3
    assert task_info.volume_of("天书残卷") is None
