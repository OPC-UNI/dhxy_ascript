# -*- coding: utf-8 -*-
"""物品目录（device/items.py）核心完整性：模板真实存在、RGB、登记合法。"""
from pathlib import Path

from PIL import Image

from src.device import items


def test_item_catalog_entries_valid_and_templates_exist():
    root = Path(__file__).resolve().parent.parent / "res" / "img"
    assert "mihoutao" in items.ITEMS          # 用户 2026-11-26 新增
    assert "yimengfu" in items.ITEMS          # 用户 2026-11-27 新增（属性13）
    for key, entry in items.ITEMS.items():
        assert entry.key == key
        assert entry.name and entry.icon
        assert entry.storage in items.STORAGES
        path = root / entry.icon
        assert path.is_file(), f"{key} 模板缺失: {entry.icon}"
        assert Image.open(path).mode == "RGB", f"{key} 模板必须 RGB"
