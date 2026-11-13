"""小游戏触发时机回归：对话/面板触发门控与 extras 链顺序。

这些测试不依赖真机画面，锁住「未触发不执行」「触发后才执行」「多触发
同时命中时的优先级」三类行为；小游戏实现搬到 minigames/ 后仍必须通过。
"""
import time

from src.minigames import duchong
from src.tasks import kezhan, mingwang, xuanshu


# ---- 客栈：拍毒虫优先于已 armed 的音律 ----
def test_kezhan_duchong_priority_over_armed_yinlv(monkeypatch):
    import numpy as np

    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(duchong, "_duchong_next_try", 0.0)
    monkeypatch.setattr(kezhan, "_yinlv_armed_at", time.time())
    monkeypatch.setattr(kezhan, "_yinlv_last_check", 0.0)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan.game_map, "best_match_np",
                        lambda *a, **k: (0.99, (677, 262)))
    monkeypatch.setattr(kezhan.task_tracker, "find_task", lambda *a: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)

    calls = []
    monkeypatch.setattr(kezhan, "_play_duchong",
                        lambda win: calls.append("duchong") or (True, "ok"))
    monkeypatch.setattr(kezhan, "_play_yinlv",
                        lambda win, banner_pt=None: calls.append("yinlv")
                        or (kezhan.YINLV_DONE, "ok"))

    assert kezhan._handle_follow_extras(object(), [], None) is True
    assert calls == ["duchong"]  # 面板按钮在场时不再进入音律检测


# ---- 客栈：音律未 armed 时不执行 ----
def test_kezhan_yinlv_not_armed_noop(monkeypatch):
    import numpy as np

    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: None)
    monkeypatch.setattr(duchong, "_duchong_next_try", 0.0)
    monkeypatch.setattr(kezhan, "_yinlv_armed_at", 0.0)
    monkeypatch.setattr(kezhan, "_pochu_armed_at", 0.0)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan.task_tracker, "find_task", lambda *a: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    # 后续分支依赖真实窗口/OCR：本测试只关心音律未 armed 不执行
    monkeypatch.setattr(kezhan, "_handle_give_panel", lambda *a, **k: False)
    monkeypatch.setattr(kezhan, "_handle_dialog_options", lambda *a, **k: False)

    calls = []
    monkeypatch.setattr(kezhan, "_play_yinlv",
                        lambda win, banner_pt=None: calls.append("yinlv")
                        or (kezhan.YINLV_DONE, "ok"))
    kezhan._handle_follow_extras(object(), [], None)
    assert calls == []


# ---- 客栈：只有入口选项才 arm 音律 ----
def test_kezhan_yinlv_arm_only_for_entry_option(monkeypatch):
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_yinlv_armed_at", 0.0)
    kezhan._arm_minigames("其他选项")
    assert kezhan._yinlv_armed_at == 0.0
    kezhan._arm_minigames(kezhan.YINLV_ENTRY_OPTION)
    assert kezhan._yinlv_armed_at > 0


# ---- 玄枢：钓鱼优先于机关；机关只在提示对话出现时触发 ----
def test_xuanshu_wait_handler_diaoyu_before_jiguan(monkeypatch):
    calls = []
    monkeypatch.setattr(xuanshu, "_diaoyu_window_check",
                        lambda win, img, prefix="": calls.append("diaoyu")
                        or True)
    monkeypatch.setattr(xuanshu, "_play_jiguan_puzzle",
                        lambda win, title: calls.append("jiguan")
                        or (True, "ok"))
    monkeypatch.setattr(xuanshu, "log", lambda *a, **k: None)

    items = [(xuanshu.JIGUAN_DIALOG_TEXT, [500, 300, 700, 330], 0.99)]
    assert xuanshu._follow_wait_handler(object(), items, None, None) is True
    assert calls == ["diaoyu"]

    monkeypatch.setattr(xuanshu, "_diaoyu_window_check", lambda *a, **k: False)
    assert xuanshu._follow_wait_handler(object(), items, None, None) is True
    assert calls == ["diaoyu", "jiguan"]


def test_xuanshu_jiguan_requires_dialog_text(monkeypatch):
    calls = []
    monkeypatch.setattr(xuanshu, "_diaoyu_window_check", lambda *a, **k: False)
    monkeypatch.setattr(xuanshu, "_play_jiguan_puzzle",
                        lambda *a, **k: calls.append("jiguan") or (True, "ok"))
    monkeypatch.setattr(xuanshu.game_map, "find_template_in",
                        lambda *a, **k: None)  # 锄遗聚宝不在场
    assert xuanshu._follow_wait_handler(object(), [], None, None) is False
    assert calls == []  # 没有提示对话，不进入机关流程


# ---- 名望：沙海弹窗检测未 armed 时零截图/零执行 ----
def test_mingwang_shahai_play_not_armed_noop(monkeypatch):
    calls = []

    class _Win:
        def capture(self, **kwargs):
            calls.append("capture")
            return object()

    monkeypatch.setattr(mingwang, "_shahai_game_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "play_shahai_miyong",
                        lambda *a, **k: calls.append("play") or (True, "ok"))
    assert mingwang._maybe_play_shahai(_Win()) is False
    assert calls == []
