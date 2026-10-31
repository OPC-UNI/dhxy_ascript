"""xuanshu.py：任务签名相似判定（follow 轮询防抖核心）与
任务消失弹窗守卫（2026-10-31 客栈奇遇弹窗遗留教训）。"""
from PIL import Image

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


class _GuardWin:
    def __init__(self, img=Image.new("RGB", (60, 60))):
        self.img = img

    def capture(self, force_activate=False):
        return self.img


def test_task_gone_dialog_guard_no_handler():
    assert xuanshu._task_gone_dialog_guard(_GuardWin(), None) is False


def test_task_gone_dialog_guard_battle_frame_skips(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: True)
    ocr_calls = []
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda *a, **k: ocr_calls.append(1) or ([], None))
    assert xuanshu._task_gone_dialog_guard(_GuardWin(), lambda *a: True) \
        is False
    assert ocr_calls == []


def test_task_gone_dialog_guard_handled(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([("我知道了", (650, 500, 720, 530), 1.0)], None))
    assert xuanshu._task_gone_dialog_guard(
        _GuardWin(), lambda win, items, img_np: True) is True


def test_task_gone_dialog_guard_not_handled(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([], None))
    assert xuanshu._task_gone_dialog_guard(
        _GuardWin(), lambda win, items, img_np: False) is False


def test_task_gone_dialog_guard_extras_raise_swallowed(monkeypatch):
    # 业务异常吞掉放行（与 _confirm_guard_dialog 同约定）
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([("x", (400, 300, 500, 320), 1.0)], None))

    def _raise(*a, **k):
        raise RuntimeError("business")

    assert xuanshu._task_gone_dialog_guard(_GuardWin(), _raise) is False
