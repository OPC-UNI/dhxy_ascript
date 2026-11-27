# -*- coding: utf-8 -*-
"""300环拼图核心路径单测：目标格排列 → 交换序列。

labels 取自 2026-11-26 长安东实机帧（参考图匹配 + 按钮遮挡补全后的
真实结果），该序列的 12 次交换已在实机把拼图复原并通过「完成」。
"""
from src.minigames import sanbaihuan_jigsaw as jigsaw


def test_swap_plan_real_puzzle_labels():
    labels = [12, 11, 5, 9, 0, 6, 7, 15, 2, 13, 10, 1, 4, 14, 8, 3]
    swaps = jigsaw.swap_plan(labels)
    assert len(swaps) == 12
    # 按交换序列执行后，每格块的目标格 == 自身（拼图复原）
    cur = list(labels)
    for a, b in swaps:
        cur[a], cur[b] = cur[b], cur[a]
    assert cur == list(range(16))
    assert jigsaw.swap_plan(list(range(16))) == []


def test_swap_plan_rejects_invalid_labels():
    assert jigsaw.swap_plan(None) is None
    assert jigsaw.swap_plan([0, 1, None]) is None
    assert jigsaw.swap_plan([0, 0, 2]) is None
