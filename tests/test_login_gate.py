"""已登录判定 + 顶号任务中止。

2026-11-22 用户规则：
1. 「其它玩家已经用这个账号登录，请立即退出游戏」属于未登录状态
   （标题仍带 ID，需画面兜底）。
2. 任务进程复用 follow/战斗线程已有的对话区 OCR 命中顶号 → 立即中止
   （方案 A，零额外截图/OCR）。
"""
import types

import numpy as np
import pytest

from src.core import app_utils
from src.device import dialog_common, game


class _Win:
    def __init__(self, title, hwnd=1):
        self.title = title
        self.hwnd = hwnd
        self.captures = 0

    def capture(self, force_activate=False):
        assert force_activate is False  # 轮询截图禁止抢前台
        self.captures += 1
        return object()


def _items(*texts, score=0.9):
    return [(t, [0, 0, 1, 1], score) for t in texts]


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    game.reset_kicked_cache()
    app_utils.reset_session_abort()
    monkeypatch.setattr(app_utils, "log", lambda *a, **k: None)
    yield
    game.reset_kicked_cache()
    app_utils.reset_session_abort()


def test_kicked_marker_matching():
    # 整句命中
    assert dialog_common.kicked_dialog_in_items(_items(
        "其它玩家已经用这个账号登录，请立即退出游戏"))
    # OCR 拆行：任一关键片段命中即算
    assert dialog_common.kicked_dialog_in_items(_items("其它玩家已经用这个账号登录"))
    assert dialog_common.kicked_dialog_in_items(_items("请立即退出游戏"))
    # 「账/帐」「录/陆」误读容忍
    assert dialog_common.kicked_dialog_in_items(_items("请用这个帐号登录"))
    assert dialog_common.kicked_dialog_in_items(_items("请用这个帐号登陆"))
    # 普通标准对话框不算
    assert not dialog_common.kicked_dialog_in_items(
        _items("少侠已经完成5次任务，请明天再来吧"))
    # 低置信度行不参与
    assert not dialog_common.kicked_dialog_in_items(_items(
        "其它玩家已经用这个账号登录", score=0.3))


def test_detect_skips_ocr_without_standard_dialog(monkeypatch):
    from src.device import dialog_common
    from src.device import map as game_map
    win = _Win("大话西游2经典版 - 一生所爱 - 甲（ID：111）")
    calls = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: 0.2)
    monkeypatch.setattr(game_map, "ocr_region",
                        lambda img, region: calls.append(region) or ([], None))
    assert game._detect_kicked_dialog(win) is False
    assert calls == []  # 模板未命中不 OCR


def test_detect_hits_on_dialog_text(monkeypatch):
    from src.device import dialog_common, task_tracker
    from src.device import map as game_map
    win = _Win("大话西游2经典版 - 一生所爱 - 甲（ID：111）")
    regions = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: 0.9)
    monkeypatch.setattr(game_map, "ocr_region", lambda img, region: (
        regions.append(region) or
        (_items("其它玩家已经用这个账号登录，请立即退出游戏"), None)))
    assert game._detect_kicked_dialog(win) is True
    assert regions == [task_tracker.DIALOG_REGION]


def test_detect_capture_failure_is_not_kicked(monkeypatch):
    """截图失败按未命中（继续按标题判定），不误报未登录。"""
    win = _Win("大话西游2经典版 - 一生所爱 - 甲（ID：111）")
    win.capture = lambda force_activate=False: None
    assert game._detect_kicked_dialog(win) is False


def test_window_logged_in_cached_and_fresh(monkeypatch):
    win = _Win("大话西游2经典版 - 一生所爱 - 甲（ID：111）")
    state = {"n": 0, "kicked": False}

    def _detect(w):
        state["n"] += 1
        return state["kicked"]

    monkeypatch.setattr(game, "_detect_kicked_dialog", _detect)
    assert game.window_logged_in(win) is True
    assert game.window_logged_in(win) is True   # 复检间隔内复用缓存
    assert state["n"] == 1
    state["kicked"] = True
    assert game.window_logged_in(win, fresh=True) is False  # 启动前强制复检
    assert state["n"] == 2
    assert game.window_logged_in(win) is False
    assert state["n"] == 2                       # 命中结果同样缓存


def test_unlogged_title_skips_screen_check(monkeypatch):
    win = _Win("大话西游2经典版")
    calls = []
    monkeypatch.setattr(game, "_detect_kicked_dialog",
                        lambda w: calls.append(w) or False)
    assert game.window_logged_in(win) is False
    assert calls == []


def test_list_main_window_infos_marks_kicked_window(monkeypatch):
    win = _Win("大话西游2经典版 $Revision: 1 - 一生所爱 - 甲（ID：111）")
    monkeypatch.setattr(game, "list_main_windows", lambda: [win])
    monkeypatch.setattr(game, "_detect_kicked_dialog", lambda w: True)
    infos = game.list_main_window_infos()
    assert infos[0]["role_id"] == "111"       # 角色信息保留（重登恢复）
    assert infos[0]["logged_in"] is False     # 被顶号 → 未登录


def test_abort_if_kicked_sets_session_abort_once(monkeypatch):
    """顶号 OCR 命中：置会话中止标志 + ERROR 仅一次 + 主线程抛出。"""
    logs = []
    monkeypatch.setattr(app_utils, "log",
                        lambda msg, level="INFO": logs.append((level, msg)))
    assert dialog_common.abort_if_kicked(
        _items("其它玩家已经用这个账号登录，请立即退出游戏"), where="[修罗]")
    assert "[修罗]" in app_utils.session_abort_reason()
    assert sum(1 for lv, _ in logs if lv == "ERROR") == 1
    # 重复命中（follow/战斗线程先后发现）不重复告警
    assert dialog_common.abort_if_kicked(_items("请立即退出游戏"))
    assert sum(1 for lv, _ in logs if lv == "ERROR") == 1
    with pytest.raises(app_utils.SessionAbort):
        app_utils.raise_if_aborted()


def test_no_kick_no_abort():
    assert dialog_common.abort_if_kicked(_items("少侠已经完成5次任务")) is False
    assert app_utils.session_abort_reason() is None
    app_utils.raise_if_aborted()  # 未中止：不抛


def test_follow_task_aborts_on_kicked_dialog(monkeypatch):
    """follow 外层双区域 OCR 命中顶号 → SessionAbort 立即结束跟踪。"""
    from src.workflows import follow

    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    win = types.SimpleNamespace(
        capture=lambda force_activate=False: frame)
    monkeypatch.setattr(follow, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(follow, "is_in_battle", lambda w: False)
    monkeypatch.setattr(follow.tab_group, "active", lambda: False)
    monkeypatch.setattr(
        follow.task_tracker, "ocr_follow",
        lambda img: (_items("其它玩家已经用这个账号登录，请立即退出游戏"),
                     np.zeros_like(frame)))

    with pytest.raises(app_utils.SessionAbort):
        follow.follow_task(win, "常规玩法", timeout=5, prefix="修罗")
    assert "[修罗]" in app_utils.session_abort_reason()


def test_battle_wuyi_check_aborts_on_kicked(monkeypatch):
    """战斗线程巫医检查复用对话区 OCR：命中顶号置标志（不抛，主线程收口）。"""
    from src.device import battle

    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    win = types.SimpleNamespace()
    monkeypatch.setattr(battle.dialog_common, "dialog_ocr_skip",
                        lambda frame, w=None: False)
    monkeypatch.setattr(battle.game_map, "ocr_regions",
                        lambda frame, regions: (_items("请立即退出游戏"), None))
    monkeypatch.setattr(battle, "_wuyi_prev_roi", None)

    assert battle._handle_wuyi_dialog(win, img=img) is False
    assert app_utils.session_abort_reason() is not None
