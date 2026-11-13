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
                 idle=1e9, allow=None):
    import numpy as np
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: score)
    monkeypatch.setattr(dialog_common.task_tracker, "is_green_text",
                        lambda img, rect: green)
    monkeypatch.setattr(dialog_common, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    # 寻路保护：默认帧位置区像素不变（站定）+ 从未点过追踪寻路
    monkeypatch.setattr(dialog_common.app_utils, "track_walk_idle_seconds",
                        lambda: idle)
    gate = dialog_common.StoryDialogDismisser(known_texts=known,
                                              allow_dismiss=allow)
    return gate, clicks, np.zeros((831, 1378, 3), np.uint8)


def _dismiss(gate, frame, prefix=""):
    return gate.try_dismiss(None, _STORY, frame, prefix=prefix, img=frame)


def test_story_dismiss_static_polls_then_click(monkeypatch):
    """连续 2 轮询命中 + 人物站定（位置区像素连续 2 次不变）才点屏。

    2026-11-11 用户实机：剧情对话不挡路时人物照常自动寻路，点屏幕
    中间会取消寻路（人物停下不再走）——须先确认站定。
    """
    gate, clicks, frame = _dismiss_env(monkeypatch)
    assert _dismiss(gate, frame, "测试") is False
    assert _dismiss(gate, frame, "测试") is False
    assert _dismiss(gate, frame, "测试") is True
    assert clicks == [1]


def test_story_dismiss_skips_while_walking(monkeypatch):
    """位置区像素持续变化（寻路中）绝不点屏——防取消自动寻路。"""
    import numpy as np
    gate, clicks, _ = _dismiss_env(monkeypatch)
    monkeypatch.setattr(dialog_common, "STORY_MOVE_BACKOFF", 0.0)  # 每次都比对
    for i in range(10):
        frame = np.zeros((831, 1378, 3), np.uint8)
        frame[60:90, 45:222] = (i * 10) % 256  # 坐标文字区域逐次变化
        assert _dismiss(gate, frame) is False
    assert clicks == []


def test_story_dismiss_skips_after_track_walk(monkeypatch):
    """刚点过任务追踪：寻路起步窗口（位置文字刷新滞后）不点屏。"""
    gate, clicks, frame = _dismiss_env(
        monkeypatch, idle=dialog_common.STORY_MOVE_TRACK_GRACE - 0.5)
    for _ in range(10):
        assert _dismiss(gate, frame) is False
    assert clicks == []


def test_story_dismiss_no_frame_allows_click(monkeypatch):
    """无帧可查（截窗失败/无窗口）按未寻路放行：保持旧行为。"""
    gate, clicks, _ = _dismiss_env(monkeypatch)
    for _ in range(5):
        gate.try_dismiss(None, _STORY, None, img=None)
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


# ---- MoveWatch：人物移动直接信号（任务消失确认共用，2026-11-12） ----
def test_move_watch_static_then_standing(monkeypatch):
    """位置区像素不变：首帧建基线（按移动），连续不变后判站定。"""
    import numpy as np
    monkeypatch.setattr(dialog_common.app_utils,
                        "track_walk_idle_seconds", lambda: 1e9)
    frame = np.zeros((831, 1378, 3), np.uint8)
    watch = dialog_common.MoveWatch()
    assert watch.walking(None, frame) is True   # 首帧无基线：偏移动
    assert watch.walking(None, frame) is False  # 连续不变=站定


def test_move_watch_moving_detected(monkeypatch):
    """位置区像素变化 → 移动（阈值 0.3，站立 ≤0.26 / 行走 ≥0.50 标定）。"""
    import numpy as np
    monkeypatch.setattr(dialog_common.app_utils,
                        "track_walk_idle_seconds", lambda: 1e9)
    monkeypatch.setattr(dialog_common, "STORY_MOVE_BACKOFF", 0.0)
    watch = dialog_common.MoveWatch()
    frame = np.zeros((831, 1378, 3), np.uint8)
    assert watch.walking(None, frame) is True
    frame2 = np.zeros_like(frame)
    frame2[60:90, 45:222] = 200  # 地图位置文字区域变化（坐标刷新）
    assert watch.walking(None, frame2) is True


def test_move_watch_unknown_without_frame(monkeypatch):
    """无帧可查返回 None（调用方自定默认方向，本类不猜测）。"""
    monkeypatch.setattr(dialog_common.app_utils,
                        "track_walk_idle_seconds", lambda: 1e9)
    watch = dialog_common.MoveWatch()
    assert watch.walking(None, None) is None


# ---- PositionStillness：寻路停滞看门狗的逐轮静止计时（2026-11-13 徐卿卿腿） ----

def test_position_stillness_accumulates_and_move_resets():
    """位置区不变累计静止秒数；位置文字变化（移动）归零重计。"""
    import numpy as np
    watch = dialog_common.PositionStillness()
    frame = np.zeros((831, 1378, 3), np.uint8)
    assert watch.seconds(frame, now=100.0) == 0.0  # 首帧建基线
    assert watch.seconds(frame, now=101.5) == 1.5  # 连续静止：累计
    moved = frame.copy()
    moved[60:90, 45:222] = 200                     # 地图位置文字区域变化
    assert watch.seconds(moved, now=103.0) == 0.0  # 检测到移动：归零
    assert watch.seconds(moved, now=104.0) == 1.0  # 之后继续累计


def test_position_stillness_reset_and_no_frame():
    """reset()/无帧可查：归零并清基线（下帧从 0 重新累计）。"""
    import numpy as np
    watch = dialog_common.PositionStillness()
    frame = np.zeros((831, 1378, 3), np.uint8)
    watch.seconds(frame, now=1.0)
    assert watch.seconds(frame, now=2.0) == 1.0
    watch.reset()
    assert watch.seconds(frame, now=3.0) == 0.0
    assert watch.seconds(None, now=4.0) == 0.0     # 无帧：归零并清基线
    assert watch.seconds(frame, now=5.0) == 0.0


# ---- 对话区 OCR 门控：屏幕归属（2026-11-13 组号 pass/手动切 Tab） ----

def test_dialog_ocr_skip_off_leader_skips(monkeypatch):
    """当前画面不是队长 Tab（pass 进行中/用户手动切走）时按无对话框
    跳过：队员屏的对话框不是本任务的，门控放行会让队长侧 OCR/点击。"""
    from src import app_utils, tab_group
    monkeypatch.setattr(app_utils, "high_perf_on", lambda: True)
    monkeypatch.setattr(dialog_common, "dialog_frame_score",
                        lambda frame: 0.99)  # 模板命中也不放行
    monkeypatch.setattr(tab_group, "on_leader_screen", lambda win: False)
    assert dialog_common.dialog_ocr_skip(None, object()) is True
    # 队长屏：模板命中正常放行
    monkeypatch.setattr(tab_group, "on_leader_screen", lambda win: True)
    assert dialog_common.dialog_ocr_skip(None, object()) is False
    # 不传 win（非组号调用/单测兼容）：不检查归属
    assert dialog_common.dialog_ocr_skip(None) is False
