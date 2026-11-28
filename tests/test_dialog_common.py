"""dialog_common.py 与 app_utils.norm_text：对话文案匹配（原文不"纠正"）。"""
from src.device import dialog_common
from src.core.app_utils import norm_text


def _item(txt, x=500, y=400, score=0.95):
    return (txt, [x, y, x + 100, y + 30], score)


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
    # green 传常量（全部行视为绿字）或真实判定函数（按帧像素判绿，
    # 供行内高亮/整行选项的区分测试）
    check = green if callable(green) else (lambda img, rect: green)
    monkeypatch.setattr(dialog_common.task_tracker, "is_green_text", check)
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


def _paint_rect(frame, rect, color, count, start=0):
    """在 rect 内从第 start 个像素起涂 count 个 color（逐行铺，控制像素数）。"""
    x0, y0, x1, y1 = rect
    w = x1 - x0
    for i in range(start, start + count):
        y = y0 + i // w
        if y >= y1:
            break
        frame[y, x0 + i % w] = color


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
    """对话区有整行绿字选项（有待点的对话）不点屏。"""
    from src.device import task_tracker
    gate, clicks, frame = _dismiss_env(monkeypatch,
                                       green=task_tracker.is_green_text)
    # 整行绿字选项：纯绿色文字、无白色正文（实测选项行正文像素≈0）
    _paint_rect(frame, _STORY[0][1], (60, 220, 60), 600)
    for _ in range(3):
        assert gate.try_dismiss(None, _STORY, frame) is False
    assert clicks == []


def test_story_dismiss_inline_green_highlight_not_option(monkeypatch):
    """行内绿色高亮（物品名链接混排白色正文）不算选项，剧情对话照常点屏。

    2026-11-15 实机：魔王重聚牛魔王交付传音石台词「来，就是这块传音石，
    少侠拿好，用它能找到魔王护卫的位置……」整行含「传音石」「魔王护卫」
    两个绿字链接。旧判定「任一行有绿字即视为选项对话」令点屏兜底永不
    触发，mowangchongju 又因对话框在场拒绝使用传音石 → 任务卡死。
    """
    from src.device import task_tracker
    gate, clicks, frame = _dismiss_env(monkeypatch,
                                       green=task_tracker.is_green_text)
    # 高亮行：绿色链接字 + 大量白色正文同处一个 OCR 行（实测 绿 400 /
    # 正文 1368）；第二行为纯白叙述
    _paint_rect(frame, _STORY[0][1], (60, 220, 60), 200)
    _paint_rect(frame, _STORY[0][1], (255, 255, 255), 900, start=300)
    _paint_rect(frame, _STORY[1][1], (255, 255, 255), 900)
    assert _dismiss(gate, frame) is False
    assert _dismiss(gate, frame) is False
    assert _dismiss(gate, frame) is True
    assert clicks == [1]


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
    from src.core import app_utils
    from src.device import tab_group
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


# ---- 绿字选项行判定：行内绿色链接 vs 整行绿字选项（2026-11-17 实机）----

def _glyph_blocks(frame, rect, blocks, color):
    """在 rect 内按 (x0, x1) 列块画细字块（模拟稀疏字形像素）。"""
    for x0, x1 in blocks:
        frame[55:62, rect[0] + x0:rect[0] + x1] = color


def test_green_option_row_inline_map_link_not_option():
    """剧情行内绿色地图链接（「吹到了暖玉轩。」）不是选项行。

    2026-11-17 名望五庄封印实机：OCR 拆行后该行 3 白字 + 3 绿字，
    白/绿像素比 0.84（≤1.0）被旧判据当成选项行 → 点屏兜底永不触发、
    follow 空转 5 分钟超时。绿字列覆盖仅约一半，必须判为非选项。
    """
    import numpy as np
    frame = np.zeros((100, 200, 3), np.uint8)
    rect = [20, 50, 123, 72]
    _glyph_blocks(frame, rect, [(2, 9), (22, 29), (42, 49)],
                  (255, 255, 255))                 # 白字「吹到了」
    _glyph_blocks(frame, rect, [(62, 69), (82, 89), (98, 103)],
                  (0, 255, 0))                     # 绿字「暖玉轩」
    items = [("吹到了暖玉轩。", rect, 0.96)]
    assert dialog_common.green_option_row_present(frame, items) is False


def test_green_option_row_full_green_still_option():
    """整行绿字（无白色正文）仍是选项行——防本次修改放宽选项保护。"""
    import numpy as np
    frame = np.zeros((100, 200, 3), np.uint8)
    rect = [20, 50, 123, 72]
    for x0 in range(2, 100, 16):
        _glyph_blocks(frame, rect, [(x0, min(x0 + 8, 103))], (0, 255, 0))
    items = [("是武尊神遣我过来帮忙的。", rect, 0.95)]
    assert dialog_common.green_option_row_present(frame, items) is True


# ---- 寻路判定背景动画误判兜底：坐标 OCR 复核（2026-11-17 五庄观实机）----

def test_walking_false_positive_verified_by_position(monkeypatch):
    """像素判移动持续 3s 后，两次坐标 OCR 一致 → 按站定放行点屏。

    位置文字区是半透明标签条，背后世界动画让像素差分持续超阈值——
    2026-11-17 五庄观实机对话在场连续 3 分钟「寻路中」，任务空转到
    follow 超时（结束后自愈）。坐标 OCR 两次一致 = 人物实际站定。
    """
    import time as _time
    from src.device import dialog_common as dc
    d = dc.StoryDialogDismisser()
    monkeypatch.setattr(d._walk._move, "walking", lambda *a, **k: True)
    monkeypatch.setattr(dc.game_map, "read_map_position",
                        lambda win, img=None: ("五庄观", 7, 31))
    d._walk._moving_since = _time.time() - dc.STORY_MOVE_VERIFY_AFTER - 1
    d._walk._verify_at = 0.0
    d._walk._verify_pos = ("五庄观", 7, 31)
    assert d._walking(None) is False  # 坐标一致 → 站定放行


def test_walking_still_true_when_position_changes(monkeypatch):
    """坐标 OCR 与上次不同 → 仍按移动处理（真寻路不误放行）。"""
    import time as _time
    from src.device import dialog_common as dc
    d = dc.StoryDialogDismisser()
    monkeypatch.setattr(d._walk._move, "walking", lambda *a, **k: True)
    monkeypatch.setattr(dc.game_map, "read_map_position",
                        lambda win, img=None: ("五庄观", 8, 31))
    d._walk._moving_since = _time.time() - dc.STORY_MOVE_VERIFY_AFTER - 1
    d._walk._verify_at = 0.0
    d._walk._verify_pos = ("五庄观", 7, 31)
    assert d._walking(None) is True


def test_walking_verify_not_run_early(monkeypatch):
    """像素判移动未满 3s：不付坐标 OCR，直接按移动处理。"""
    import time as _time
    from src.device import dialog_common as dc
    d = dc.StoryDialogDismisser()
    monkeypatch.setattr(d._walk._move, "walking", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(dc.game_map, "read_map_position",
                        lambda win, img=None: called.append(1) or None)
    d._walk._moving_since = _time.time() - 1.0
    assert d._walking(None) is True
    assert called == []


# ---- 门控模板2：半透明 NPC 对话盒右下角（2026-11-18 采桃仙女实机） ----
# 标准模板在亮背景场景被半透明对话盒削弱到 0.766 < 0.79，对话区 OCR
# 被跳过、绿色选项永不点击 → 任务卡死。模板2（右下角）与原模板取高分补门。

def _pasted(tpl_name, seed=7, x=600, y=470):
    import cv2
    from ascript.windows.system import R as _R
    from tests.conftest import make_bgr_frame
    tpl = cv2.imread(str(_R.img(tpl_name)))
    frame = make_bgr_frame(seed=seed)
    h, w = tpl.shape[:2]
    frame[y:y + h, x:x + w] = tpl
    return frame


def test_dialog_frame_score_npc_corner_template_hits():
    """模板2 在条带内命中即放行（亮背景半透明对话盒场景，原模板 0.766）。"""
    frame = _pasted(dialog_common.DIALOG_FRAME_ICON2)
    score = dialog_common.dialog_frame_score(frame)
    assert score >= dialog_common.DIALOG_FRAME_THRESHOLD
    assert score > 0.99


def test_dialog_frame_score_original_template_unchanged():
    """原模板路径不受影响：模板1 命中照常返回模板1 分数。"""
    frame = _pasted(dialog_common.DIALOG_FRAME_ICON, seed=11)
    score = dialog_common.dialog_frame_score(frame)
    assert score >= dialog_common.DIALOG_FRAME_THRESHOLD
    assert score > 0.99


def test_dialog_frame_score_no_dialog_frame_stays_low():
    """无可匹配模板的噪声帧：两模板都不命中，门控保持关闭。"""
    from tests.conftest import make_bgr_frame
    score = dialog_common.dialog_frame_score(make_bgr_frame(seed=3))
    assert score < dialog_common.DIALOG_FRAME_THRESHOLD


# ---- 剧情对话快速翻页：纯像素绿字选项行判据（2026-11-27） ----

def _dialog_box_frame():
    """空白对话框文字区帧（DIALOG_BOX_REGION 内画内容）。"""
    import numpy as np
    frame = np.zeros((831, 1378, 3), np.uint8)
    l, t, r, b = dialog_common.DIALOG_BOX_REGION
    frame[t:b, l:r] = (40, 40, 40)  # 暗色对话框底
    return frame


def test_green_option_row_px_detects_full_green_line():
    """整行绿字（选项行）：判为选项行 → 快速翻页必须停。"""
    frame = _dialog_box_frame()
    l, t, _r, _b = dialog_common.DIALOG_BOX_REGION
    # 一行连续绿字：绿像素占满该行文字列，无白色正文
    frame[t + 60:t + 78, l + 15:l + 300] = (20, 210, 20)  # BGR 绿
    assert dialog_common.green_option_row_present_px(frame)
    # 下一行白字正文（与选项行间留空行）
    frame[t + 90:t + 108, l + 15:l + 400] = (230, 230, 230)
    assert dialog_common.green_option_row_present_px(frame)


def test_green_option_row_px_rejects_inline_green_link():
    """行内绿字链接（白正文 + 少量绿字）不算选项行 → 快速翻页放行。"""
    frame = _dialog_box_frame()
    l, t, _r, _b = dialog_common.DIALOG_BOX_REGION
    # 白字正文行中嵌一小段绿字（物品链接）
    frame[t + 60:t + 78, l + 15:l + 380] = (230, 230, 230)
    frame[t + 60:t + 78, l + 120:l + 200] = (20, 210, 20)
    assert not dialog_common.green_option_row_present_px(frame)
