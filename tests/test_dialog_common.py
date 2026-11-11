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


def _dismiss_env(monkeypatch, score=0.99, green=False, known=(),
                 pos=("洛阳城", 100, 100), idle=1e9, allow=None):
    import numpy as np
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: score)
    monkeypatch.setattr(dialog_common.task_tracker, "is_green_text",
                        lambda img, rect: green)
    monkeypatch.setattr(dialog_common, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    # 寻路保护：默认坐标静止（连续不变 → 站定）+ 从未点过追踪寻路
    monkeypatch.setattr(dialog_common.game_map, "read_map_position",
                        lambda win, img=None: pos)
    monkeypatch.setattr(dialog_common.app_utils, "track_walk_idle_seconds",
                        lambda: idle)
    gate = dialog_common.StoryDialogDismisser(known_texts=known,
                                              allow_dismiss=allow)
    return gate, clicks, np.zeros((831, 1378, 3), np.uint8)


def test_story_dismiss_static_polls_then_click(monkeypatch):
    """连续 2 轮询命中 + 人物站定（坐标连续 3 次不变）才点屏。

    2026-11-11 用户实机：剧情对话不挡路时人物照常自动寻路，点屏幕
    中间会取消寻路（人物停下不再走）——须先确认站定。
    """
    gate, clicks, frame = _dismiss_env(monkeypatch)
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is False
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is False
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is False
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is False
    assert gate.try_dismiss(None, _STORY, frame, prefix="测试") is True
    assert clicks == [1]


def test_story_dismiss_skips_while_walking(monkeypatch):
    """人物坐标持续变化（寻路中）绝不点屏——防取消自动寻路。"""
    gate, clicks, frame = _dismiss_env(monkeypatch)
    seq = iter([("洛阳城", 100 + i, 100) for i in range(20)])
    monkeypatch.setattr(dialog_common.game_map, "read_map_position",
                        lambda win, img=None: next(seq))
    for _ in range(10):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_skips_after_track_walk(monkeypatch):
    """刚点过任务追踪：寻路起步窗口（坐标显示刷新滞后）不点屏。"""
    gate, clicks, frame = _dismiss_env(
        monkeypatch, idle=dialog_common.STORY_MOVE_TRACK_GRACE - 0.5)
    for _ in range(10):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_pos_unreadable_allows_click(monkeypatch):
    """坐标不可读按未寻路放行（无坐标场景剧情对话仍可推进，旧行为）。"""
    gate, clicks, frame = _dismiss_env(monkeypatch)
    monkeypatch.setattr(dialog_common.game_map, "read_map_position",
                        lambda win, img=None: None)
    for _ in range(5):
        gate.try_dismiss(None, _STORY, frame)
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
    gate, clicks, frame = _dismiss_env(monkeypatch, allow=lambda: False)
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


# ---- 连点推进的对话框在场复检（2026-11-11：对话提前结束后的多余
#      点击会落到世界画面、取消自动寻路） ----


class _FakeWin:
    def capture(self, force_activate=False):
        return "frame"


def test_click_screen_center_stops_when_dialog_closes(monkeypatch):
    """连点 >1 次：首检确认对话框在场后逐次复检，对话框消失即停。"""
    clicks = []
    scores = iter([0.99, 0.99, 0.65])  # 首检、第 1 次复检、第 2 次复检
    monkeypatch.setattr(dialog_common, "dialog_frame_score",
                        lambda img: next(scores))
    monkeypatch.setattr(dialog_common, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(dialog_common, "move_away", lambda win: None)
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    monkeypatch.setattr(dialog_common.time, "sleep", lambda s: None)
    dialog_common._click_screen_center(_FakeWin(), times=4, prefix="测试")
    assert len(clicks) == 2


def test_click_screen_center_nonstandard_dialog_keeps_fixed_clicks(monkeypatch):
    """非标准对话框（模板不在场）保持旧的固定连点行为。"""
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: 0.6)
    monkeypatch.setattr(dialog_common, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(dialog_common, "move_away", lambda win: None)
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    monkeypatch.setattr(dialog_common.time, "sleep", lambda s: None)
    dialog_common._click_screen_center(_FakeWin(), times=4, prefix="测试")
    assert len(clicks) == 4


def test_click_screen_center_single_click_no_capture(monkeypatch):
    """单次点击不做模板复检（无多余点击，零额外截窗开销）。"""
    calls = []

    class _Win:
        def capture(self, force_activate=False):
            calls.append(force_activate)
            return "frame"

    monkeypatch.setattr(dialog_common, "dialog_frame_score",
                        lambda img: (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(dialog_common, "human_click", lambda win, x, y: None)
    monkeypatch.setattr(dialog_common, "move_away", lambda win: None)
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    monkeypatch.setattr(dialog_common.time, "sleep", lambda s: None)
    dialog_common._click_screen_center(_Win(), times=1, prefix="测试")
    assert calls == []
