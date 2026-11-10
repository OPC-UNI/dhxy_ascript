"""dialog_common.py 与 app_utils.norm_text：对话文案匹配（原文不"纠正"）。"""
from src import dialog_common
from src.app_utils import norm_text


def _item(txt, x=500, y=400, score=0.95):
    return (txt, [x, y, x + 100, y + 30], score)


def test_norm_text_strips_punct():
    assert norm_text("看打！，「」；：。") == "看打"


def test_dialog_text_present_fuzzy_tolerance():
    """文案检测为模糊匹配：抗 OCR 单字误读（设计行为）；无关文案不命中。

    逐字精确匹配（选项文案不"纠正"用字）是选项定位路径的契约
    （_find_fight_option 类 norm+精确子串），与本函数无关。
    """
    items = [_item("幸苦方丈，多亏了您")]
    assert dialog_common._dialog_text_present(items, "幸苦方丈")
    assert dialog_common._dialog_text_present(items, "辛苦方丈")  # 1 字误读容差
    assert not dialog_common._dialog_text_present(items, "道长请留步")


def test_dialog_text_present_joined_line():
    """跨行拆框拼接：文案被 OCR 拆成两行时，全文拼接可命中。"""
    items = [_item("各位少侠"), _item("请留步听我说")]
    assert dialog_common._dialog_text_present(items, "请留步听我说")


def test_norm_dialog_present_ignores_punct():
    items = [_item("我可要走了；你，留步！")]
    assert dialog_common._norm_dialog_present(items, "我可要走了你留步")


# ---- StoryDialogDismisser：无选项剧情对话点屏推进（2026-11-10 实测
#      剧情台词常驻屏幕命中对话门，对话区每 0.5s 全量 OCR 空烧） ----

_STORY = [_item("我被人骗了", 435, 395),
          _item("有人骗走了我的20万两银子", 556, 395)]


def _dismiss_env(monkeypatch, score=0.99, green=False, known=()):
    import numpy as np
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: score)
    monkeypatch.setattr(dialog_common.task_tracker, "is_green_text",
                        lambda img, rect: green)
    monkeypatch.setattr(dialog_common, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    gate = dialog_common.StoryDialogDismisser(known_texts=known)
    return gate, clicks, np.zeros((831, 1378, 3), np.uint8)


def test_story_dismiss_two_polls_then_click(monkeypatch):
    gate, clicks, frame = _dismiss_env(monkeypatch)
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is False
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is True
    assert clicks == [1]


def test_story_dismiss_requires_dialog_frame(monkeypatch):
    """无标准对话框边框（商店面板实测 0.653 < 0.79）不点——世界点击
    会误触走位。"""
    gate, clicks, frame = _dismiss_env(monkeypatch, score=0.65)
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_skips_green_option(monkeypatch):
    """对话区有绿字选项（有待点的对话）不点屏。"""
    gate, clicks, frame = _dismiss_env(monkeypatch, green=True)
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_skips_known_text(monkeypatch):
    """已知对话文案（选项未渲染时）只等待，不当剧情对话点屏。"""
    gate, clicks, frame = _dismiss_env(monkeypatch, known=("有人骗走了我的",))
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_allow_dismiss_veto(monkeypatch):
    """allow_dismiss 回调整体抑制（如五环给与面板预期窗口）。"""
    import numpy as np
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(dialog_common.task_tracker, "is_green_text",
                        lambda img, rect: False)
    monkeypatch.setattr(dialog_common, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    gate = dialog_common.StoryDialogDismisser(allow_dismiss=lambda: False)
    frame = np.zeros((831, 1378, 3), np.uint8)
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []
