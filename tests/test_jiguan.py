"""minigames/jiguan.py：封印机关破解的解除结果信号与换模板逻辑。

用户 2026-11-17 修正：点击任意一个机关模板后只看结果对话框——
出现「换一处再试试」才换下一个模板；出现「此处已解除封印/解除封印
成功」即解除成功；两者都没有也按成功处理（旧实现固定等 5s 查任务
签名，签名被 OCR 抖动成乱码时会误判完成、跳过后续机关）。
"""
import numpy as np

from src.minigames import jiguan


class _Win:
    def capture(self, force_activate=False):
        return np.zeros((831, 1378, 3), dtype=np.uint8)


def _install(monkeypatch, results, found=None):
    """安装替身：results 按点击次数消费（"fail"/"success"/None）。

    found：模板 icon → 坐标（缺省三个模板都命中）。
    """
    clicks = []
    tried = []
    dismissed = []
    outcomes = iter(results)
    found = found if found is not None else {
        icon: (800 + i * 10, 400) for i, icon in enumerate(jiguan.JIGUAN_CLICK_ORDER)
    }

    def _find(win, icon, threshold=0.8, region=None):
        if not tried or tried[-1] != icon:
            tried.append(icon)  # 查找超时内会重复轮询，记录尝试顺序即可
        return found.get(icon)

    def _result(win, timeout=jiguan.JIGUAN_RESULT_WAIT):
        return next(outcomes)

    monkeypatch.setattr(jiguan, "log", lambda *a, **k: None)
    monkeypatch.setattr(jiguan, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(jiguan.game_map, "find_template", _find)
    monkeypatch.setattr(jiguan, "_jiguan_result_dialog", _result)
    monkeypatch.setattr(jiguan, "clear_blocking_dialog",
                        lambda win, prefix="", max_clicks=3:
                        dismissed.append(prefix) or (True, True))
    monkeypatch.setattr(jiguan.app_utils, "silent_mode_on", lambda: False)
    monkeypatch.setattr(jiguan.app_utils, "keyboard_ok", lambda win: False)
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    monkeypatch.setattr(jiguan, "JIGUAN_FIND_TIMEOUT", 0.01)
    return clicks, tried, dismissed


def test_play_fail_then_success_dialog_stops(monkeypatch):
    """第一个机关提示换一处 → 换第二个并出现成功对话框 → 不再点第三个。"""
    clicks, tried, dismissed = _install(monkeypatch, ["fail", "success"])
    ok, info = jiguan.play(_Win(), prefix="玄枢轶闻")
    assert ok and "出现解除成功提示" in info
    assert tried == list(jiguan.JIGUAN_CLICK_ORDER[:2])  # 红不再查找/点击
    assert len(clicks) == 3  # 1 次关提示对话 + 2 次点机关
    assert len(dismissed) == 1  # 失败对话框被关闭后才换下一个


def test_play_fail_then_timeout_treated_as_success(monkeypatch):
    """失败后换下一个；第二个既无失败也无成功对话框（超时）按成功处理。"""
    clicks, tried, _ = _install(monkeypatch, ["fail", None])
    ok, info = jiguan.play(_Win())
    assert ok and "未出现换一处提示" in info
    assert tried == list(jiguan.JIGUAN_CLICK_ORDER[:2])
    assert len(clicks) == 3


def test_play_all_fail_reports_end(monkeypatch):
    """三个模板都提示换一处 → 逐个尝试后结束，不再重复。"""
    clicks, tried, dismissed = _install(monkeypatch, ["fail", "fail", "fail"])
    ok, info = jiguan.play(_Win())
    assert ok and "均提示换一处" in info
    assert tried == list(jiguan.JIGUAN_CLICK_ORDER)
    assert len(clicks) == 4  # 1 次关提示对话 + 3 次点机关
    assert len(dismissed) == 3


def test_play_no_template_found(monkeypatch):
    """三个模板都找不到 → 未执行任何机关点击。"""
    clicks, tried, _ = _install(monkeypatch, [], found={})
    ok, info = jiguan.play(_Win())
    assert not ok and "尝试 0 个机关" in info
    assert clicks == [jiguan.SCREEN_CENTER_POS]  # 只关了提示对话，无机关点击
    assert tried == list(jiguan.JIGUAN_CLICK_ORDER)


def test_result_dialog_detects_fail_text(monkeypatch):
    """结果对话框：OCR 到「换一处再试试」→ "fail"。"""
    monkeypatch.setattr(jiguan, "dialog_frame_score", lambda img: 1.0)
    monkeypatch.setattr(jiguan.game_map, "ocr_region",
                        lambda img, region: ([("换一处再试试", [0, 0, 1, 1], 0.99)], None))
    assert jiguan._jiguan_result_dialog(_Win()) == "fail"


def test_result_dialog_detects_success_text(monkeypatch):
    """结果对话框：OCR 到「此处已解除封印」→ "success"。"""
    monkeypatch.setattr(jiguan, "dialog_frame_score", lambda img: 1.0)
    monkeypatch.setattr(jiguan.game_map, "ocr_region",
                        lambda img, region: ([("此处已解除封印", [0, 0, 1, 1], 0.99)], None))
    assert jiguan._jiguan_result_dialog(_Win()) == "success"


def test_result_dialog_timeout_without_text(monkeypatch):
    """无结果对话框时按超时上限返回 None（调用方按成功处理）。"""
    monkeypatch.setattr(jiguan, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    assert jiguan._jiguan_result_dialog(_Win(), timeout=0.01) is None
