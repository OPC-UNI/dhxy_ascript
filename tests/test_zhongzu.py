"""zhongzu.py：种族比武任务（参考修罗）。

验证点：
- 任务对话五选一绿色选项匹配（进入战斗/开始战斗/开始采集/开始开采/
  开始训练），非绿字/追踪面板区域行不参与
- 领取对话框选项匹配：完整文案 → 尾部 → 短兜底
- 领取三件套未标定时 run_loop 安全结束（不空转、不乱点）
- 新增种族比武设置键默认值与方案快照一致
"""
import numpy as np

from src import control_window, schemes, task_tracker, zhongzu

IMG = np.zeros((200, 200, 3), dtype=np.uint8)


def _patch_green(monkeypatch, green=True):
    monkeypatch.setattr(task_tracker, "is_green_text",
                        lambda img, rect: green)
    monkeypatch.setattr(zhongzu, "green_option_center",
                        lambda img, rect, nlen, tlen:
                        (rect[0] + 10, rect[1] + 10))


def _dialog_items(text, x=500):
    return [(text, [x, 100, x + 180, 130], 0.9)]


def test_find_task_option_each_of_five(monkeypatch):
    """五个任务选项任一命中即可（用户 2026-11-12 确认）。"""
    _patch_green(monkeypatch)
    for option in ("进入战斗", "开始战斗", "开始采集", "开始开采", "开始训练"):
        found = zhongzu._find_task_option(_dialog_items(f"请选择：{option}"), IMG)
        assert found is not None
        assert found[3] == option
        assert found[0] == 510 and found[1] == 110


def test_find_task_option_ignores_non_green_and_tracker(monkeypatch):
    """非绿字与追踪面板区域（x<PANEL_MAX_X）行不参与匹配。"""
    _patch_green(monkeypatch, green=False)
    assert zhongzu._find_task_option(_dialog_items("开始战斗"), IMG) is None
    _patch_green(monkeypatch, green=True)
    assert zhongzu._find_task_option(
        _dialog_items("开始战斗", x=100), IMG) is None
    assert zhongzu._find_task_option(_dialog_items("无关文案"), IMG) is None


def test_find_claim_option_full_tail_and_short(monkeypatch):
    """领取选项：完整文案 → 尾部 → 短兜底，短兜底标记 fallback。"""
    _patch_green(monkeypatch)
    full = zhongzu.CLAIM_DIALOG_OPTION
    found = zhongzu._find_claim_option(_dialog_items(full), IMG)
    assert found is not None and found[3] is False
    found = zhongzu._find_claim_option(
        _dialog_items(zhongzu.CLAIM_OPTION_TAIL), IMG)
    assert found is not None and found[3] is False
    found = zhongzu._find_claim_option(
        _dialog_items(zhongzu.CLAIM_OPTION_SHORT), IMG)
    assert found is not None and found[3] is True


def test_claim_unconfigured_safe_exit(monkeypatch):
    """领取三件套未标定（素材缺失/坐标为空）：run_loop 直接安全结束，
    不执行队伍检查/寻路。"""
    logs = []
    called = {"team": 0}

    monkeypatch.setattr(zhongzu, "_claim_configured", lambda: False)
    monkeypatch.setattr(zhongzu, "load_task_counts", lambda: {"种族比武": 0})
    monkeypatch.setattr(zhongzu, "load_zhongzu_check_team", lambda: True)
    monkeypatch.setattr(zhongzu, "load_zhongzu_auto_create_team", lambda: False)
    monkeypatch.setattr(zhongzu, "load_zhongzu_auto_rejoin", lambda: True)
    monkeypatch.setattr(zhongzu, "load_zhongzu_auto_wuyi", lambda: False)
    monkeypatch.setattr(zhongzu, "load_zhongzu_wuyi_interval", lambda: 5)
    monkeypatch.setattr(zhongzu, "load_zhongzu_auto_repair", lambda: False)
    monkeypatch.setattr(zhongzu, "load_zhongzu_repair_interval", lambda: 30)
    monkeypatch.setattr(zhongzu, "load_zhongzu_tab_group", lambda: False)
    monkeypatch.setattr(zhongzu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(zhongzu.team, "clear_pending_apply",
                        lambda win: (False, ""))
    monkeypatch.setattr(zhongzu.team, "check_team_min",
                        lambda *a, **k: called.__setitem__("team",
                                                           called["team"] + 1))
    monkeypatch.setattr(zhongzu.app_utils, "task_progress_done",
                        lambda win, mode: 0)
    monkeypatch.setattr(zhongzu, "_task_state", lambda win: False)
    monkeypatch.setattr(zhongzu, "_walk_to_claim_spot",
                        lambda win: called.__setitem__("team",
                                                       called["team"] + 1))
    monkeypatch.setattr(zhongzu, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))

    class _Win:
        pass

    out = zhongzu.run_loop(_Win())
    assert out == zhongzu.CLAIM_UNCONFIGURED_INFO
    assert called["team"] == 0  # 未就绪不执行队伍检查/寻路
    assert any("未就绪" in msg for msg, _ in logs)


def test_verify_claim_flow_hover_only(monkeypatch):
    """领取测试：定位领取选项只悬停不点击，最后点屏幕中间关闭。"""
    events = {"moves": [], "clicks": []}
    monkeypatch.setattr(zhongzu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(zhongzu, "_walk_to_claim_spot",
                        lambda win: (True, "已到达领取点附近"))
    monkeypatch.setattr(
        zhongzu, "_click_entry_ensuring_dialog",
        lambda win: (True, "已打开领取对话框", ([], IMG)))
    monkeypatch.setattr(
        zhongzu, "_find_claim_option",
        lambda items, img_np: (520, 300, zhongzu.CLAIM_DIALOG_OPTION, False))
    monkeypatch.setattr(zhongzu, "human_move",
                        lambda sx, sy: events["moves"].append((sx, sy)))
    monkeypatch.setattr(zhongzu, "human_click",
                        lambda win, x, y, **k: events["clicks"].append((x, y)))
    monkeypatch.setattr(zhongzu, "log", lambda *a, **k: None)

    class _Rect:
        left, top = 100, 50

    class _Win:
        rect = _Rect()

    ok, info = zhongzu.verify_claim_flow(_Win())
    assert ok and "未领取任务" in info
    # 选项坐标换算为屏幕坐标后仅悬停；点击只有屏幕中间一次
    assert events["moves"] == [(620, 350)]
    assert events["clicks"] == [zhongzu.SCREEN_CENTER_POS]


def test_click_claim_map_pos_clamps_out_of_window(monkeypatch):
    """小地图点击点超出窗口时夹取到窗口内（防御点到游戏窗口外）。"""
    clicks = []
    monkeypatch.setattr(zhongzu, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(zhongzu, "log", lambda *a, **k: None)

    class _Rect:
        width, height = 1378, 831

    class _Win:
        rect = _Rect()

    monkeypatch.setattr(zhongzu, "CLAIM_MAP_POS", (200, 1000))
    zhongzu._click_claim_map_pos(_Win())
    assert clicks[-1] == (200, 830)
    monkeypatch.setattr(zhongzu, "CLAIM_MAP_POS", (300, 400))
    zhongzu._click_claim_map_pos(_Win())
    assert clicks[-1] == (300, 400)


def test_claim_configured_with_provided_assets():
    """用户 2026-11-12 提供的领取三件套已就位（NPC 模板/坐标/小地图点）。"""
    assert zhongzu.CLAIM_SPOT == (286, 159)
    assert zhongzu.CLAIM_MAP_POS == (737, 387)
    assert zhongzu._claim_configured() is True


def _patch_role_defaults(monkeypatch):
    """load_* 首次默认：role_settings.get 一律返回默认值。"""
    monkeypatch.setattr(control_window.role_settings, "get",
                        lambda key, default=None, role=None: default)


def test_zhongzu_settings_defaults(monkeypatch):
    """种族比武设置默认值与方案快照一致（默认全开，巫医 5 轮/修理 30 场）。"""
    _patch_role_defaults(monkeypatch)
    assert control_window.load_zhongzu_check_team() is True
    assert control_window.load_zhongzu_auto_create_team() is True
    assert control_window.load_zhongzu_auto_rejoin() is True
    assert control_window.load_zhongzu_auto_wuyi() is True
    assert control_window.load_zhongzu_wuyi_interval() == 5
    assert control_window.load_zhongzu_auto_repair() is True
    assert control_window.load_zhongzu_repair_interval() == 30
    assert control_window.load_zhongzu_tab_group() is True
    spec = schemes._settings_spec()
    assert spec[control_window._ZHONGZU_CHECK_TEAM_KEY] is True
    assert spec[control_window._ZHONGZU_AUTO_CREATE_TEAM_KEY] is True
    assert spec[control_window._ZHONGZU_AUTO_REJOIN_KEY] is True
    assert spec[control_window._ZHONGZU_AUTO_WUYI_KEY] is True
    assert spec[control_window._ZHONGZU_WUYI_INTERVAL_KEY] == 5
    assert spec[control_window._ZHONGZU_AUTO_REPAIR_KEY] is True
    assert spec[control_window._ZHONGZU_REPAIR_INTERVAL_KEY] == 30
    assert spec[control_window._ZHONGZU_TAB_GROUP_KEY] is True


def test_zhongzu_count_default_and_max():
    """次数默认 10、上限 100（用户 2026-11-12 确认）。"""
    assert control_window.TASK_COUNT_DEFAULTS["种族比武"] == 10
    assert control_window.TASK_COUNT_MAX["种族比武"] == 100
    assert control_window._clamp_count("种族比武", 1000) == 100
    assert control_window._clamp_count("种族比武", 0) == 0
