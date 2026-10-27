"""xuanshu.py：任务签名相似判定（follow 轮询防抖核心）。"""
from src import xuanshu


def test_signatures_similar_identical():
    assert xuanshu._signatures_similar("常规玩法|前往龙窟五层消灭修罗",
                                       "常规玩法|前往龙窟五层消灭修罗")


def test_signatures_similar_ocr_jitter():
    """1~2 字符 OCR 抖动（标点/单字误读）视为同一状态。"""
    a = "常规玩法|前往龙窟五层消灭修罗"
    b = "常规玩法|前往龙窟五层消灭修罹"  # 单字误读
    assert xuanshu._signatures_similar(a, b)


def test_signatures_similar_different():
    """目标文案整体不同（仅共享标题前缀）→ 视为任务状态变化。"""
    a = "常规玩法|前往龙窟五层消灭修罗"
    b = "常规玩法|任务已完成返回领取奖励"
    assert not xuanshu._signatures_similar(a, b)
