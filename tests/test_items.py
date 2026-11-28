# -*- coding: utf-8 -*-
"""物品目录（device/items.py）核心完整性：模板真实存在、RGB、登记合法。"""
from pathlib import Path

from PIL import Image

from src.device import items


def test_item_catalog_entries_valid_and_templates_exist():
    root = Path(__file__).resolve().parent.parent / "res" / "img"
    assert "mihoutao" in items.ITEMS          # 用户 2026-11-26 新增
    assert "yimengfu" in items.ITEMS          # 用户 2026-11-27 新增（属性13）
    assert "pixiezhu" in items.ITEMS          # 用户 2026-11-27 新增（炼妖石·属性13）
    assert "lieyansha" in items.ITEMS
    # 用户 2026-11-27 新增（炼妖石·属性9，300环「9-12属性」区间给与）
    for key in ("canghaizhu", "lantianyu", "lingxijiao",
                "xiaohanding", "yimengfu9", "yunluozhang", "wudipao"):
        assert key in items.ITEMS
    # 用户 2026-11-27 新增（300环普通给与：天外飞石/内丹精华/千年寒铁/灵宝天威印）
    for key in ("tianwaifeishi", "neidan_jinghua", "qiannianhantie",
                "lingbaotianweiyin"):
        assert key in items.ITEMS
    assert "lingbaozhutianyin" in items.ITEMS  # 用户 2026-11-28 新增（灵宝诸天印）
    assert "sanjipeishihezi" in items.ITEMS    # 用户 2026-11-28 新增（三级配饰盒子）
    assert "zhenshoudan" in items.ITEMS        # 用户 2026-11-27 新增（含数量角标）
    # 用户 2026-11-27 新增（300环等级类别给与：1级或2级宝宝装备）
    for key in ("zhuangzi", "cubuyi", "muzheng"):
        assert key in items.ITEMS
    for key, entry in items.ITEMS.items():
        assert entry.key == key
        assert entry.name and entry.icon
        assert entry.storage in items.STORAGES
        path = root / entry.icon
        assert path.is_file(), f"{key} 模板缺失: {entry.icon}"
        assert Image.open(path).mode == "RGB", f"{key} 模板必须 RGB"


def test_item_threshold_resolves_by_icon():
    """数量角标模板（摄妖香/珍兽丹）登记专用阈值 0.69，按图标文件名可取。"""
    assert items.item("zhenshoudan").threshold == 0.69
    assert items.item("sheyaoxiang").threshold == 0.69
    assert items.threshold_for_icon("item_zhenshoudan.png") == 0.69
    # 未设置专用阈值的模板返回 0（调用方用默认 0.8/0.85）
    assert items.threshold_for_icon("item_tianwaifeishi.png") == 0.0
    assert items.threshold_for_icon("不存在的模板.png") == 0.0


def test_find_by_name_accepts_ocr_alias():
    """天外飞石的任务文案偶尔被 OCR 读成「天外石」，别名要能命中。"""
    assert items.find_by_name("快去找到一个天外飞石（7点雅誉）").key \
        == "tianwaifeishi"
    assert items.find_by_name("快去找到一个天外石（7点雅誉）").key \
        == "tianwaifeishi"
    assert items.find_by_name("内丹精华").key == "neidan_jinghua"
    assert items.find_by_name("千年寒铁").key == "qiannianhantie"
    # 灵宝天威印的任务文案被 OCR 实读成「灵宝天成印」，别名要能命中
    assert items.find_by_name("找到一个灵宝天威印").key == "lingbaotianweiyin"
    assert items.find_by_name("找到一个灵宝天成印").key == "lingbaotianweiyin"
    # 三级配饰盒子：任务文案写「三级不可培养配饰/佩饰盒子」，给与面板
    # 悬停提示名是「三级配饰盒子」，三种叫法都要命中同一条登记
    for alias in ("三级不可培养配饰", "三级不可培养佩饰",
                  "三级不可培养盒子", "三级佩饰盒子", "三级配饰盒子"):
        assert items.find_by_name(alias).key == "sanjipeishihezi"
    assert items.find_by_name("完全不存在的物品") is None


def test_find_by_category_gathers_any_candidates():
    """「属性13的炼妖石」类别型给与：按类别+属性值收集候选，任一可交。"""
    hits = items.find_by_category("炼妖石", 13)
    assert {e.key for e in hits} == {"pixiezhu", "lieyansha"}
    assert items.find_by_category("炼妖石", 15) == []
    assert items.find_by_category("", 13) == []


def test_find_by_category_range_includes_bounds():
    """「9-12属性的炼妖石」区间型给与：区间内候选，13 与区间外不参与。"""
    keys = {e.key for e in items.find_by_category_range("炼妖石", 9, 12)}
    assert keys == {"canghaizhu", "lantianyu", "lingxijiao",
                    "xiaohanding", "yimengfu9", "yunluozhang", "wudipao"}
    assert {e.key for e in items.find_by_category_range("炼妖石", 13, 13)} == {
        "pixiezhu", "lieyansha"}
    assert items.find_by_category_range("炼妖石", 10, 12) == []
    assert items.find_by_category_range("", 9, 12) == []


def test_baby_equipment_level_category_candidates():
    """「1级或2级宝宝装备」：三件登记模板等级均为 1（实机悬停复核）。"""
    hits = items.find_by_category_range("宝宝装备", 1, 2)
    assert [e.key for e in hits] == ["zhuangzi", "cubuyi", "muzheng"]
    assert all(e.attr_value == 1 for e in hits)
    assert items.find_by_category_range("宝宝装备", 3, 4) == []
