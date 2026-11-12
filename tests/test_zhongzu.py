"""zhongzu.py：种族比武任务（参考修罗）。

验证点：
- 任务对话五选一绿色选项匹配（进入战斗/开始战斗/开始采集/开始开采/
  开始训练），非绿字/追踪面板区域行不参与
- 领取 NPC 模板组：缺失模板跳过、同屏多个候选取最右侧
- 领取 NPC 点击门控：人物坐标不在领取点附近时不点击（NPC 可见不算到位）
- 到达领取点先 Alt+4 清屏再识别 NPC 模板（每次领取只按一次）
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


def test_find_claim_npc_picks_rightmost(monkeypatch):
    """领取 NPC 模板组：同屏两个候选时取最右侧（用户 2026-11-12 规则）。"""
    monkeypatch.setattr(zhongzu, "_claim_npc_icons",
                        lambda: ("a.png", "b.png"))
    hits = {"a.png": [(0.95, 400, 300), (0.91, 700, 305)],
            "b.png": [(0.88, 702, 302)]}
    calls = []

    def _fake_all(frame, icon, threshold=0.8):
        calls.append((icon, threshold))
        return hits[icon]

    monkeypatch.setattr(zhongzu.game_map, "match_frame_all", _fake_all)
    assert zhongzu._find_claim_npc_np(IMG) == (702, 302)
    # 模板组逐个匹配，统一走 CLAIM_NPC_THRESHOLD
    assert [c[0] for c in calls] == ["a.png", "b.png"]
    assert all(c[1] == zhongzu.CLAIM_NPC_THRESHOLD for c in calls)


def test_find_claim_npc_single_and_none(monkeypatch):
    """单候选正常返回；无候选返回 None。"""
    monkeypatch.setattr(zhongzu, "_claim_npc_icons", lambda: ("a.png",))
    monkeypatch.setattr(zhongzu.game_map, "match_frame_all",
                        lambda frame, icon, threshold=0.8: [(0.9, 500, 320)])
    assert zhongzu._find_claim_npc_np(IMG) == (500, 320)
    monkeypatch.setattr(zhongzu.game_map, "match_frame_all",
                        lambda frame, icon, threshold=0.8: [])
    assert zhongzu._find_claim_npc_np(IMG) is None


def test_claim_npc_icons_skips_missing(monkeypatch):
    """模板缺失时跳过且只告警一次（其余模板照常参与匹配）。"""
    logs = []
    monkeypatch.setattr(zhongzu, "_claim_npc_missing_logged", False)
    monkeypatch.setattr(zhongzu, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    monkeypatch.setattr(zhongzu, "_template_exists",
                        lambda icon: icon == "zhongzu_claim_npc.png")
    icons = zhongzu._claim_npc_icons()
    assert icons == ("zhongzu_claim_npc.png",)
    assert len(logs) == 1 and "缺失" in logs[0][0]
    # 再次调用复用缓存，不重复告警
    assert zhongzu._claim_npc_icons() == icons
    assert len(logs) == 1


def test_wait_claim_entry_reads_position_on_hit_frame(monkeypatch):
    """命中帧同时读地图位置（点击日志打印当前坐标，用户 2026-11-12）。"""
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    seen = {}
    here = (zhongzu.CHANGAN, *zhongzu.CLAIM_SPOT)  # 坐标到位，门控放行

    class _Win:
        def capture(self, force_activate=False):
            return frame

    monkeypatch.setattr(zhongzu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(zhongzu, "_claim_npc_candidates",
                        lambda frame: [(0.98, 818, 358)])

    def _read_pos(win, img=None):
        seen["img"] = img
        return here

    monkeypatch.setattr(zhongzu.game_map, "read_map_position", _read_pos)
    assert zhongzu._wait_claim_entry(_Win(), 1.0) == ((818, 358), here)
    assert seen["img"] is frame  # 同帧复用，不重复截窗


def test_clear_claim_screen_once_per_attempt(monkeypatch):
    """到达领取点后的 Alt+4 清屏每次领取只按一次（切换态防重复翻转）。"""
    calls = []
    monkeypatch.setattr(zhongzu, "_claim_clear_done", False)
    monkeypatch.setattr(zhongzu.app_utils, "press_alt4_clear",
                        lambda win, prefix="": calls.append(prefix))
    zhongzu._clear_claim_screen(None)
    zhongzu._clear_claim_screen(None)
    assert calls == ["[种族比武]"]
    # 新一轮领取复位后再按一次
    monkeypatch.setattr(zhongzu, "_claim_clear_done", False)
    zhongzu._clear_claim_screen(None)
    assert len(calls) == 2


def test_walk_to_claim_spot_clears_before_recognize(monkeypatch):
    """已在领取点：先 Alt+4 清屏，再识别领取 NPC 模板（用户 2026-11-12）。"""
    order = []
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    here = (zhongzu.CHANGAN, *zhongzu.CLAIM_SPOT)

    class _Win:
        def capture(self, force_activate=False):
            return frame

    monkeypatch.setattr(zhongzu, "_claim_configured", lambda: True)
    monkeypatch.setattr(zhongzu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(zhongzu.game_map, "read_map_position",
                        lambda win, img=None: here)
    monkeypatch.setattr(zhongzu, "_clear_claim_screen",
                        lambda win: order.append("clear"))
    monkeypatch.setattr(zhongzu, "_find_claim_npc_np",
                        lambda frame, verbose=False: order.append("match")
                        or (818, 358))
    ok, info = zhongzu._walk_to_claim_spot(_Win())
    assert ok and "领取 NPC 可见" in info
    assert order[:2] == ["clear", "match"]  # 清屏先于模板识别


def test_wait_claim_entry_gates_click_by_coordinate(monkeypatch):
    """坐标不在领取点附近时不得点击（用户 2026-11-12 坐标门控）。"""
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    logs = []
    far_x = zhongzu.CLAIM_SPOT[0] + zhongzu.CLAIM_NEAR_TOLERANCE + 5

    class _Win:
        def capture(self, force_activate=False):
            return frame

    monkeypatch.setattr(zhongzu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(zhongzu, "_claim_npc_candidates",
                        lambda frame: [(0.98, 818, 358)])
    monkeypatch.setattr(zhongzu.game_map, "read_map_position",
                        lambda win, img=None: (zhongzu.CHANGAN, far_x,
                                               zhongzu.CLAIM_SPOT[1]))
    monkeypatch.setattr(zhongzu, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    monkeypatch.setattr(zhongzu.time, "sleep", lambda s: None)

    assert zhongzu._wait_claim_entry(_Win(), 0.05) == (None, None)
    assert any("坐标不在领取点附近" in msg for msg, _ in logs)


def test_claim_configured_with_provided_assets():
    """用户 2026-11-12 提供的领取三件套已就位（NPC 模板组/坐标/小地图点）。"""
    # 领取点坐标/容差为用户实机标定资产，会持续微调，这里只校验已填写
    assert zhongzu.CLAIM_SPOT is not None
    assert zhongzu.CLAIM_MAP_POS is not None
    assert zhongzu.CLAIM_NPC_ICONS == (
        "zhongzu_claim_npc.png", "zhongzu_claim_npc2.png",
        "zhongzu_claim_npc3.png")
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
