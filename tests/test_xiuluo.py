"""xiuluo.py：巫医 NPC 识别窗口的遮挡对话框处理（2026-10-31 优化）。

验证点：
- NPC 模板可见时不 OCR 不点击，直接返回命中
- 模板被「阻止它为非作歹」对话框遮挡时点击屏幕中间取消（仅一次），
  取消后窗口顺延、模板恢复可见即返回
- 对话框持续存在也只点一次（防重复点击误点世界走位），超时取消
- 无对话框遮挡的普通漏检：只等待超时，不点击
"""
import numpy as np
import pytest
from PIL import Image

from src.tasks import xiuluo
from src.workflows import claim_flow

CENTER = xiuluo.SCREEN_CENTER_POS  # (689, 415)


@pytest.fixture(autouse=True)
def _reset_claim_walk_state(monkeypatch):
    """小地图走位/任务筛选状态跨用例隔离。"""
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", False)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", None)
    monkeypatch.setattr(xiuluo, "_claim_map_kept_open", False)
    monkeypatch.setattr(xiuluo, "_claim_arrive_dialog_frame", None)


class _FakeClock:
    """可控时钟：sleep 推进虚拟时间，time 返回虚拟时间。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _FakeWin:
    def capture(self, force_activate=False):
        return Image.new("RGB", (60, 60))


@pytest.fixture
def world(monkeypatch):
    """装配 _find_wuyi_npc 的全部外部依赖，返回观测钩子。"""
    clock = _FakeClock()
    clicks = []
    logs = []
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    monkeypatch.setattr(xiuluo, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))

    state = {"clock": clock, "clicks": clicks, "logs": logs,
             "ocr_calls": 0, "match_calls": 0}

    def fake_match(frame, icon, *a, **k):
        state["match_calls"] += 1
        return state["match_ret"].pop(0) if len(state["match_ret"]) > 1 \
            else state["match_ret"][-1]

    def fake_ocr(img, regions):
        state["ocr_calls"] += 1
        return state["ocr_ret"], None

    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", fake_match)
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions", fake_ocr)
    return state


def test_find_wuyi_npc_found_immediately(world):
    world["match_ret"] = [(320, 240)]
    world["ocr_ret"] = []
    pt, info = xiuluo._find_wuyi_npc(_FakeWin())
    assert pt == (320, 240)
    assert info == ""
    assert world["ocr_calls"] == 0
    assert world["clicks"] == []


def test_find_wuyi_npc_dialog_dismissed_then_found(world):
    # 首轮模板被对话框遮挡，OCR 命中「阻止它为非作歹」→ 点屏幕中间取消；
    # 次轮模板恢复可见 → 返回命中
    world["match_ret"] = [None, (320, 240)]
    world["ocr_ret"] = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    pt, info = xiuluo._find_wuyi_npc(_FakeWin())
    assert pt == (320, 240)
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    assert any("屏幕中间取消" in msg for msg, _ in world["logs"])


def test_find_wuyi_npc_dialog_persists_clicks_once(world):
    # 对话框点不掉/持续存在：只点一次，超时取消（防重复点击误点世界）
    world["match_ret"] = [None]
    world["ocr_ret"] = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    pt, info = xiuluo._find_wuyi_npc(_FakeWin())
    assert pt is None
    assert "未识别到巫医 NPC" in info
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    # 顺延 2s 后窗口才结束：虚拟时间应走过 3 + 2 = 5s
    assert world["clock"].t >= 5.0
    assert world["ocr_calls"] == 1  # 点过后不再 OCR


def test_find_wuyi_npc_no_dialog_timeout(world):
    world["match_ret"] = [None]
    world["ocr_ret"] = []
    pt, info = xiuluo._find_wuyi_npc(_FakeWin())
    assert pt is None
    assert "未识别到巫医 NPC" in info
    assert world["clicks"] == []
    assert world["clock"].t >= 3.0


def test_dismiss_wuyi_block_dialog_found(world):
    # 领取完成后的检查：对话框在场 → 点一次屏幕中间即返回，不空等窗口
    world["ocr_ret"] = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    assert xiuluo._dismiss_wuyi_block_dialog(_FakeWin()) is True
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    assert world["ocr_calls"] == 1
    assert world["clock"].t < 1.0
    assert any("屏幕中间取消" in msg for msg, _ in world["logs"])


def test_dismiss_wuyi_block_dialog_not_found(world):
    # 对话框未出现：等满 3s 窗口不点击，直接放行去巫医（到达窗口另有兜底）
    world["ocr_ret"] = []
    assert xiuluo._dismiss_wuyi_block_dialog(_FakeWin()) is False
    assert world["clicks"] == []
    assert world["clock"].t >= 3.0


def test_dismiss_wuyi_block_dialog_uses_claim_frame(world):
    # 领取确认帧已含对话框（2026-11-10 实测 3/3 轮）：直接点掉，
    # 不截图、不 OCR、不空等窗口
    items = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    assert xiuluo._dismiss_wuyi_block_dialog(_FakeWin(), items=items) is True
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    assert world["ocr_calls"] == 0
    assert world["clock"].t == 0.0


def test_dismiss_after_claim_uses_claim_frame(world):
    # 正常回合同款：确认帧命中即取消，免一次截图+模板门+对话区 OCR
    items = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    assert xiuluo._dismiss_block_dialog_after_claim(
        _FakeWin(), items=items) is True
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    assert world["ocr_calls"] == 0
    assert world["clock"].t == 0.0


def test_dismiss_after_claim_falls_back_to_window(world):
    # 确认帧不含（对话框晚到/未渲染）：回落窗口轮询，OCR 命中即取消
    world["ocr_ret"] = [("阻止它为非作歹", (400, 300, 500, 330), 0.9)]
    items = [("修罗任务", (0, 220, 200, 250), 0.9)]
    assert xiuluo._dismiss_block_dialog_after_claim(
        _FakeWin(), items=items) is True
    assert world["clicks"] == [(CENTER[0], CENTER[1])]
    assert world["ocr_calls"] == 1


def test_dismiss_after_claim_absent_times_out(world):
    # 窗口内始终未见：不点击，等满 2s 放行（follow 有剧情对话兜底）
    world["ocr_ret"] = []
    assert xiuluo._dismiss_block_dialog_after_claim(
        _FakeWin(), items=[]) is False
    assert world["clicks"] == []
    assert world["clock"].t >= 2.0
    assert any("未见阻止对话" in msg for msg, _ in world["logs"])


# ---- 巫医链超时路径：战斗线程已点击时不重复兜底点击（2026-11-04） ----

DIALOG_ITEMS = [("我是超级巫医，可以帮你医治召唤兽",
                 (400, 300, 900, 600), 0.96)]
NPC_PT = (678, 496)
OPTION_PT = (534, 408)


@pytest.fixture
def wuyi_chain(monkeypatch):
    """装配 _run_wuyi_chain 外部依赖：对话框恒在场、超时缩短为 1s。"""
    clock = _FakeClock()
    clock.t = 1000.0  # 真实时间戳语义（>0：区分「从未点击」的 last_wuyi_option_at=0）
    clicks = []
    logs = []
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    monkeypatch.setattr(xiuluo, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda win: ("灵兽村", *xiuluo.WUYI_SPOT))
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), None))
    monkeypatch.setattr(xiuluo, "_find_wuyi_npc",
                        lambda win: (NPC_PT, ""))
    monkeypatch.setattr(xiuluo, "find_wuyi_option",
                        lambda items, img_np: OPTION_PT)
    monkeypatch.setattr(xiuluo, "WUYI_HANDLE_TIMEOUT", 1.0)  # 单轮即超时
    monkeypatch.setattr(xiuluo.battle, "last_wuyi_option_at", 0)
    monkeypatch.setattr(xiuluo.battle, "last_wuyi_option_pos", None)
    monkeypatch.setattr(xiuluo.battle, "_wuyi_pos_by_key", {})  # 分键缓存隔离
    return {"clock": clock, "clicks": clicks, "logs": logs}


def test_wuyi_chain_click_detected_returns_immediately(wuyi_chain):
    """战斗线程点击后（时间戳晚于点 NPC）→ 随机 1~1.2s 即返回，
    不等对话框关闭（用户 2026-11-04：点击全部医治后立即寻路）。"""
    xiuluo.battle.last_wuyi_option_at = 1000.5  # 点 NPC 之后战斗线程点击
    ok, info = xiuluo._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "自动巫医完成" in info
    assert wuyi_chain["clicks"] == [NPC_PT]      # 仅点 NPC，无重复点击
    t = wuyi_chain["clock"].t
    assert 1001.0 <= t <= 1001.2                 # 随机 1~1.2s 等待后返回


def test_wuyi_chain_timeout_no_click_self_clicks(wuyi_chain):
    """超时时战斗线程未点击且对话框在场 → 兜底自行点击一次。"""
    xiuluo.battle.last_wuyi_option_at = 0
    ok, info = xiuluo._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, OPTION_PT]  # 点 NPC + 兜底点选项


def test_wuyi_chain_coord_nav_uses_map_coord(
        wuyi_chain, monkeypatch, coord_nav_enabled):
    """坐标化开关开启：巫医走位改走 map_nav 坐标点击，到达判据不变。"""
    from src.device import map_nav
    started = {}

    def _start(win, map_name, x, y, log_fn=None, on_map_open=None):
        started["args"] = (map_name, x, y)
        return True, "已点击", (610, 442)

    monkeypatch.setattr(map_nav, "start_navigation", _start)
    monkeypatch.setattr(xiuluo.game_map, "close_maps", lambda *a, **k: None)
    seq = [("灵兽村", 1, 2)]  # 首次判定不在巫医旁 → 走位

    def _pos(win):
        return seq.pop(0) if seq else ("灵兽村", *xiuluo.WUYI_SPOT)

    monkeypatch.setattr(xiuluo.game_map, "read_map_position", _pos)
    xiuluo.battle.last_wuyi_option_at = 1000.5
    ok, info = xiuluo._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert started["args"] == (xiuluo.LINGSHOUCUN, *xiuluo.WUYI_SPOT)


def test_claim_map_wrapper_passes_coord(monkeypatch):
    """修罗领取包装：坐标化坐标随 map_coord 传给共享实现。"""
    captured = {}
    win = _FakeWin()

    def fake(win, pos=None, **kwargs):
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(xiuluo.claim_flow, "open_claim_map", fake)
    assert xiuluo._open_claim_map(win, ("灵兽村", 111, 90)) == (True, "ok")
    assert captured["map_name"] == xiuluo.LINGSHOUCUN
    assert captured["map_pos"] == xiuluo.LINGSHOU_MAP_POS
    assert captured["map_coord"] == xiuluo.CLAIM_COORD


def test_wuyi_chain_timeout_self_click_uses_cached_pos(wuyi_chain, monkeypatch):
    """兜底自点优先用绿字校验过的本弹窗缓存位置，不重新识别。"""
    import numpy as np
    xiuluo.battle.last_wuyi_option_at = 0
    # 本弹窗（命中文案）已缓存点击位置——经 record 走分键缓存
    xiuluo.battle.record_wuyi_option_pos((650, 420), DIALOG_ITEMS[0][0])
    xiuluo.battle.last_wuyi_option_at = 0  # 清点击信号（仅测分键缓存直点）
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    img_np[410:430, 610:690] = (0, 186, 0)  # 缓存点周围亮绿
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), img_np))

    def _boom(*a, **k):
        raise AssertionError("有缓存不应再识别")

    monkeypatch.setattr(xiuluo, "find_wuyi_option", _boom)
    ok, info = xiuluo._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, (650, 420)]


# ---- 领取点到达判据地图名门控（2026-11-11 用户实机长安误判） ----
def test_in_claim_map_requires_lingshoucun():
    assert xiuluo._in_claim_map(("灵兽村", 111, 90))
    assert not xiuluo._in_claim_map(("长安", 367, 27))
    assert not xiuluo._in_claim_map(("长安城东", 111, 90))
    assert not xiuluo._in_claim_map(None)  # 位置读不出不采信按钮


def test_walk_to_claim_spot_button_in_changan_not_arrival(monkeypatch):
    """长安命中「任务」按钮模板（五环领取横幅同图）：不得判到达。"""
    win = _FakeWin()
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np",
                        lambda frame, icon, **k: (300, 300))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安", 367, 27))
    monkeypatch.setattr(xiuluo, "_open_claim_map",
                        lambda w, pos=None: (False, "地图导航中止"))
    ok, info = xiuluo._walk_to_claim_spot(win)
    assert ok is False and info == "地图导航中止"


def test_walk_to_claim_spot_button_in_lingshoucun_arrival(monkeypatch):
    win = _FakeWin()
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np",
                        lambda frame, icon, **k: (300, 300))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 165, 98))
    ok, info = xiuluo._walk_to_claim_spot(win)
    assert ok is True and "领取按钮可见" in info


def test_walk_to_claim_spot_keeps_map_when_auto_dialog(monkeypatch):
    """走位后领取对话框已自动弹出（模态）：跳过关地图，标记待选项点掉补关。"""
    win = _FakeWin()
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 165, 98))
    def fake_open(w, pos=None):
        xiuluo._claim_task_filter_on = True  # 开小地图时读到的筛选状态
        return True, "已点击"

    def fake_close(w):
        xiuluo._claim_map_kept_open = True
        return True

    monkeypatch.setattr(xiuluo, "_open_claim_map", fake_open)
    monkeypatch.setattr(xiuluo, "_close_claim_map_before_arrival", fake_close)
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info = xiuluo._walk_to_claim_spot(win)
    assert ok is True and "自动弹出" in info
    assert xiuluo._claim_map_kept_open is True


def test_close_claim_map_before_arrival_keeps_map_on_dialog(monkeypatch):
    """单次关地图失败且任务对话框已自动弹出：保留地图，不再做 3 次重试。"""
    win = _FakeWin()
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np",
                        lambda *a, **k: (1085, 217))
    monkeypatch.setattr(xiuluo.game_map, "is_mini_map_open", lambda w: True)
    monkeypatch.setattr(xiuluo, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: ([], None))
    closed = []
    monkeypatch.setattr(xiuluo.game_map, "close_maps",
                        lambda w, quiet=False: closed.append(1))
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    assert xiuluo._close_claim_map_before_arrival(win) is True
    assert xiuluo._claim_map_kept_open is True
    assert closed == []


def test_close_claim_map_before_arrival_retries_when_no_dialog(monkeypatch):
    """关地图失败但无自动对话框（非对话框原因）：回退标准 close_maps 重试。"""
    win = _FakeWin()
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np",
                        lambda *a, **k: (1085, 217))
    monkeypatch.setattr(xiuluo.game_map, "is_mini_map_open", lambda w: True)
    monkeypatch.setattr(xiuluo, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(xiuluo.game_map, "close_maps",
                        lambda w, quiet=False: closed.append(1))
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    assert xiuluo._close_claim_map_before_arrival(win) is False
    assert xiuluo._claim_map_kept_open is False
    assert closed == [1]


def test_walk_to_claim_spot_closes_map_when_filter_off(monkeypatch):
    """任务筛选未勾选：不查自动对话框，照常关地图走位（原流程不变）。"""
    win = _FakeWin()
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: None)
    # 决策帧：不在领取点（避免「位置近但入口未出现」宽限窗读模板）；
    # 走位到达循环：位置到领取点即返回
    positions = iter([("灵兽村", 165, 98)]
                     + [("灵兽村", *xiuluo.CLAIM_SPOT)] * 10)
    monkeypatch.setattr(
        xiuluo.game_map, "read_map_position",
        lambda w, img=None: next(positions,
                                 ("灵兽村", *xiuluo.CLAIM_SPOT)))
    def fake_open(w, pos=None):
        xiuluo._claim_task_filter_on = False  # 开小地图时读到未勾选
        return True, "已点击"

    monkeypatch.setattr(xiuluo, "_open_claim_map", fake_open)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    auto = []
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: auto.append(1) or ([], None))
    closed = []
    monkeypatch.setattr(xiuluo.game_map, "close_world_map",
                        lambda w, quiet=False: closed.append(1))
    monkeypatch.setattr(xiuluo.game_map, "close_maps",
                        lambda w, quiet=False: closed.append("maps"))
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, _info = xiuluo._walk_to_claim_spot(win)
    assert ok is True
    assert auto == [] and closed == [1]
    assert xiuluo._claim_map_kept_open is False


def test_task_state_panel_closed(monkeypatch):
    """面板不在场：按无任务返回（不阻塞领取），且不得 OCR 静态区域误采信。"""
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "panel_visible", lambda **k: False)

    def _boom(img):
        raise AssertionError("面板不在场不应 OCR 追踪区域")

    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", _boom)
    assert xiuluo._task_state(_FakeWin()) is False


def test_task_state_panel_visible_task_found(monkeypatch):
    monkeypatch.setattr(claim_flow, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "panel_visible", lambda **k: True)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track",
                        lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "has_task_in",
                        lambda items, img_np, title, fallback=(): True)
    assert xiuluo._task_state(_FakeWin()) is True


def test_claim_task_skips_when_task_reappeared(monkeypatch):
    """误判消失走了领取、任务又出现：跳过领取，交主循环续跑跟踪。

    2026-11-11 用户确认场景：任务真消失→走领取逻辑→过一会又出现，
    应继续寻路而不是空等领取入口。
    """
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "task_signature_in",
                        lambda items, title: "sig")
    called = []
    monkeypatch.setattr(
        xiuluo, "_click_entry_ensuring_dialog",
        lambda w, **k: called.append(1) or (False, "x", None, None, None))
    ok, info, frame = xiuluo._claim_task(_FakeWin())
    assert ok is False and "续跑跟踪" in info and frame is None
    assert called == []  # 不进入领取入口流程


def test_claim_task_reuses_arrival_dialog_frame(monkeypatch):
    """到达检查已确认的对话框帧直接复用点选项（免重截+重 OCR）；
    对话框门控复核不在场时不复用陈旧帧，回退短窗轮询。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", True)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    monkeypatch.setattr(xiuluo, "CLAIM_DIALOG_TIMEOUT", 0)
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    stash = (["到达帧选项"], None)
    monkeypatch.setattr(xiuluo, "_claim_arrive_dialog_frame", stash)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    called = []
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: called.append(1) or ([], None))
    used = []
    monkeypatch.setattr(xiuluo, "_click_claim_option",
                        lambda w, items, img_np: used.append((items, img_np))
                        or False)
    xiuluo._claim_task(_FakeWin())
    assert called == []  # 未重查对话框（复用到达检查帧）
    assert used == [(stash[0], stash[1])]
    assert xiuluo._claim_arrive_dialog_frame is None  # 用后即弃
    # 对话框门控不达标 → 不复用陈旧帧，回退短窗轮询
    monkeypatch.setattr(xiuluo, "_claim_arrive_dialog_frame", stash)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.3)
    called.clear()
    used.clear()
    xiuluo._claim_task(_FakeWin())
    assert called == [1]
    assert used and used[0][0] == []


def test_claim_task_auto_dialog_only_for_minimap_walk(monkeypatch):
    """小地图走位到达+任务勾选：到达自动弹出的对话框直接点选项，
    跳过领取入口点击（用户 2026-11-16）。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", True)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    seen = ([], None)
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda w, needles: seen)
    entry = []
    monkeypatch.setattr(
        xiuluo, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k) or (False, "x", None, None, None))
    options = []
    monkeypatch.setattr(xiuluo, "_click_claim_option",
                        lambda w, items, img_np: options.append(1) or False)
    monkeypatch.setattr(xiuluo, "CLAIM_DIALOG_TIMEOUT", 0)
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info, frame = xiuluo._claim_task(_FakeWin())
    assert not ok and "对话框处理失败" in info and frame is None
    assert entry == []  # 已自动弹出对话框：不点领取入口
    assert options == [1]  # 直接点自动对话框的领取选项


def test_claim_task_no_auto_dialog_check_when_standing(monkeypatch):
    """站着/返程到达：不检查自动对话框，正常走领取入口流程（原流程不变）。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", False)
    auto = []
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: auto.append(1) or ([], None))
    entry = []
    monkeypatch.setattr(
        xiuluo, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k)
        or (False, "入口失败", None, None, None))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info, frame = xiuluo._claim_task(_FakeWin())
    assert not ok and info == "入口失败" and frame is None
    assert auto == []  # 非走位到达：不查自动对话框
    assert entry and entry[0]["allow_auto_dialog"] is False


def test_claim_task_skips_auto_dialog_when_filter_off(monkeypatch):
    """小地图「任务」筛选未勾选：即使走位到达也不查自动对话框。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", True)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    auto = []
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: auto.append(1) or ([], None))
    entry = []
    monkeypatch.setattr(
        xiuluo, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k)
        or (False, "入口失败", None, None, None))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info, frame = xiuluo._claim_task(_FakeWin())
    assert not ok and info == "入口失败" and frame is None
    assert auto == []
    assert entry and entry[0]["allow_auto_dialog"] is False



# ---- 入口等待：轮询内检测自动弹出的任务对话框（2026-11-16 五倍
#      实机 18:32 空等入口窗口 8s 教训，修罗同款对齐） ----

def _entry_wait_env(monkeypatch, dialog_score=0.0):
    """装配 _wait_claim_entry：入口模板全不命中、无战斗、可控时钟。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: dialog_score)
    monkeypatch.setattr(
        xiuluo.game_map, "match_frame_multi",
        lambda frame, icons: {icon: None for icon in icons})
    state = {"ocr_calls": 0}

    def fake_ocr(img, region):
        state["ocr_calls"] += 1
        return [], None

    monkeypatch.setattr(xiuluo.game_map, "ocr_region", fake_ocr)
    return state


def test_wait_claim_entry_detects_auto_dialog_during_wait(monkeypatch):
    """入口等待期间自动弹出的任务对话框：轮询内命中即返回哨兵，
    调用方补查该帧直接处理选项，不再空等整个入口窗口。"""
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    _entry_wait_env(monkeypatch, dialog_score=1.0)
    monkeypatch.setattr(
        xiuluo, "_find_claim_option",
        lambda items, img_np, needles=None: (534, 399, "领取", False))
    assert xiuluo._wait_claim_entry(
        _FakeWin(), 10, allow_auto_dialog=True) == (None, "自动对话框")


def test_wait_claim_entry_skips_dialog_check_when_filter_off(monkeypatch):
    """任务筛选未勾选 / 未开启自动对话框检测：入口等待不查对话框
    （原流程零变化，不付对话区 OCR 成本）。"""
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    state = _entry_wait_env(monkeypatch, dialog_score=1.0)
    xiuluo._wait_claim_entry(_FakeWin(), 3, allow_auto_dialog=True)
    assert state["ocr_calls"] == 0


def test_auto_dialog_claim_mode_requires_walk_and_filter(monkeypatch):
    """自动对话框模式 = 小地图走位到达 + 任务筛选勾选/未知（None 也算）。

    该模式下首次领取前的队伍监控循环让位给领取流程（run_loop），
    直接点自动弹出的对话框选项（用户 2026-11-16 五倍实机同款）。"""
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", True)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    assert xiuluo._auto_dialog_claim_mode() is True
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", None)
    assert xiuluo._auto_dialog_claim_mode() is True
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    assert xiuluo._auto_dialog_claim_mode() is False
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", False)
    assert xiuluo._auto_dialog_claim_mode() is False


def test_arrival_claim_dialog_up_requires_mode_and_dialog(monkeypatch):
    """到达后检查自动对话框：只有「黄字走位模式 + 对话框真的在场」才跳过
    队伍监控；没点中黄字（对话框没弹）→ False，走正常队伍监控。"""
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", False)
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    called = []
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: called.append(1) or ([], None))
    assert xiuluo._arrival_claim_dialog_up(_FakeWin()) is False
    assert called == []  # 非走位模式：不检查对话框
    monkeypatch.setattr(xiuluo, "_claim_arrived_by_walk", True)
    assert xiuluo._arrival_claim_dialog_up(_FakeWin()) is True
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: None)
    assert xiuluo._arrival_claim_dialog_up(_FakeWin()) is False
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    monkeypatch.setattr(xiuluo.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: ([], None))
    assert xiuluo._arrival_claim_dialog_up(_FakeWin()) is False


# ---- 缺人喊话内容（2026-11-12：设置内容 + {种族} 占位符替换） ----

def test_build_shout_text_replaces_race_placeholder(monkeypatch):
    """{种族} 替换为必须种族（顿号连接）；无必须种族替换为「随意来人」。"""
    monkeypatch.setattr(xiuluo, "load_xiuluo_shout_text",
                        lambda: "百次修罗来人，效率队，来{种族}")
    monkeypatch.setattr(xiuluo, "load_xiuluo_settings",
                        lambda: {"min_members": 5,
                                 "races": ["男人", "男鬼"]})
    assert xiuluo._build_shout_text() == "百次修罗来人，效率队，来男人、男鬼"
    monkeypatch.setattr(xiuluo, "load_xiuluo_settings",
                        lambda: {"min_members": 5, "races": []})
    assert xiuluo._build_shout_text() == "百次修罗来人，效率队，来随意来人"


def test_build_shout_text_custom_template(monkeypatch):
    """自定义内容照常喊出；无占位符时原样输出。"""
    monkeypatch.setattr(xiuluo, "load_xiuluo_shout_text",
                        lambda: "百次修罗速度队，{种族}优先")
    monkeypatch.setattr(xiuluo, "load_xiuluo_settings",
                        lambda: {"min_members": 5, "races": ["女仙"]})
    assert xiuluo._build_shout_text() == "百次修罗速度队，女仙优先"
    assert xiuluo._build_shout_text("随缘来人") == "随缘来人"


# ---- 返程交接对齐五倍（2026-11-14：1 轮消失确认 + 返程前 has_task 复查） ----

def test_follow_to_done_uses_single_gone_poll(monkeypatch):
    """修罗消失确认只做 1 轮（对齐五倍非黄袍怪轮次，返程交接省 ~0.5s）。"""
    captured = {}

    def fake_follow(win, first_items=None, **kwargs):
        captured.update(kwargs)
        return True, "完成"

    monkeypatch.setattr(claim_flow, "follow_to_done", fake_follow)
    ok, info = xiuluo._follow_to_done(_FakeWin())
    assert ok is True and info == "完成"
    assert captured["task_gone_polls"] == 1


def test_task_still_tracked_before_return_true(monkeypatch):
    """返程前复查命中任务：单轮消失确认被误判，调用方须跳过返程。"""
    calls = []

    def fake_has_task(win, title, fallback, **kwargs):
        calls.append((title, fallback, kwargs))
        return True

    monkeypatch.setattr(xiuluo.task_tracker, "has_task", fake_has_task)
    assert xiuluo._task_still_tracked_before_return(_FakeWin()) is True
    assert calls == [(xiuluo.TASK_TITLE, xiuluo.TASK_FALLBACK_TEXTS,
                      {"fast_absent": True})]


def test_task_still_tracked_before_return_false(monkeypatch):
    """返程前复查确认任务消失：放行返程。"""
    monkeypatch.setattr(xiuluo.task_tracker, "has_task",
                        lambda win, title, fallback, **kwargs: False)
    assert xiuluo._task_still_tracked_before_return(_FakeWin()) is False


# ---- 取消任务（2026-11-14 用户指定：流程同领取，选项「我想取消任务」） ----

def test_cancel_task_skips_when_no_task(monkeypatch):
    """无追踪任务：不进入取消流程（入口不出现，防误点其他对话框选项）。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task",
                        lambda *a, **k: False)
    walked = []
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot",
                        lambda w: walked.append(1) or (True, ""))
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is False and "无需取消" in info
    assert walked == []


def test_cancel_task_happy_path(monkeypatch):
    """走位→点入口模板→点「我想取消任务」→确认弹窗点「是」→2 轮无任务确认。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    captured = {}

    def fake_entry(w, **kwargs):
        captured.update(kwargs)
        return True, "ok", "灵兽村使者", (300, 300), ([], None)

    monkeypatch.setattr(xiuluo, "_click_entry_ensuring_dialog", fake_entry)
    clicks = []
    monkeypatch.setattr(xiuluo, "_click_cancel_option",
                        lambda w, items, img_np: clicks.append(1) or True)
    confirmed = []
    monkeypatch.setattr(xiuluo, "_click_cancel_confirm",
                        lambda w, items, img_np: confirmed.append(1) or True)
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: (True, []))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is True and info == "已取消修罗任务"
    # 入口打开对话框按取消选项判定，且用独立缓存键（不与领取选项共键）
    assert captured["option_needles"] == xiuluo.CANCEL_OPTION_NEEDLES
    assert captured["option_cache_key"] == "cancel_option"
    assert captured["action"] == "取消任务"
    assert clicks == [1]
    assert confirmed == [1]  # 点「我想取消任务」后必须走二次确认
    assert xiuluo._cancel_confirmed_at is not None  # 6s 计时起点已记录


def test_cancel_task_fails_when_task_remains(monkeypatch):
    """点中选项且确认「是」但任务仍在面板：超时返回失败并提示残留文案。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    monkeypatch.setattr(xiuluo, "_click_entry_ensuring_dialog",
                        lambda w, **k: (True, "ok", "使者", (1, 1), ([], None)))
    monkeypatch.setattr(xiuluo, "_click_cancel_option",
                        lambda w, items, img_np: True)
    monkeypatch.setattr(xiuluo, "_click_cancel_confirm",
                        lambda w, items, img_np: True)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(
        xiuluo, "_task_absent_in_frame",
        lambda img: (False, [("前往瑶池消灭修罗", (20, 240, 300, 262), 0.9)]))
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is False
    assert "仍在追踪面板" in info and "消灭修罗" in info
    assert clock.t >= xiuluo.CANCEL_VERIFY_WAIT - 0.01


def test_cancel_task_confirm_dialog_timeout(monkeypatch):
    """确认弹窗始终不识别到「是」：返回失败（任务未取消），不进入消失校验。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    monkeypatch.setattr(xiuluo, "_click_entry_ensuring_dialog",
                        lambda w, **k: (True, "ok", "使者", (1, 1), ([], None)))
    monkeypatch.setattr(xiuluo, "_click_cancel_option",
                        lambda w, items, img_np: True)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)

    def _boom(img):
        raise AssertionError("未确认取消不应进入任务消失校验")

    monkeypatch.setattr(xiuluo, "_task_absent_in_frame", _boom)
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is False and "确认" in info
    assert clock.t >= xiuluo.CANCEL_CONFIRM_TIMEOUT - 0.01
    assert xiuluo._cancel_confirmed_at is None


def test_cancel_task_no_task_message_closes_and_succeeds(monkeypatch):
    """身上无任务：游戏提示「你身上都没有任务，不要来烦我。」→ 点屏幕
    中间关闭并按任务不存在返回（用户 2026-11-21），不判失败、不进
    消失校验、不计 6s 重领冷却。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    monkeypatch.setattr(xiuluo, "_click_entry_ensuring_dialog",
                        lambda w, **k: (True, "ok", "使者", (1, 1), ([], None)))
    monkeypatch.setattr(xiuluo, "_click_cancel_option",
                        lambda w, items, img_np: True)

    def _unexpected(*a, **k):
        raise AssertionError("无任务提示不应点确认「是」/进消失校验")

    monkeypatch.setattr(xiuluo, "_click_cancel_confirm", _unexpected)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame", _unexpected)
    monkeypatch.setattr(
        xiuluo.game_map, "ocr_regions",
        lambda img, regions: ([("你身上都没有任务，不要来烦我。",
                               (500, 330, 880, 352), 0.95)], None))
    dismissed = []
    monkeypatch.setattr(xiuluo, "_click_screen_center_safe",
                        lambda w, img=None: dismissed.append(1) or True)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is True and "无需取消" in info
    assert "你身上都没有任务" in info
    assert dismissed == [1]  # 点屏幕中间关闭提示对话框
    assert xiuluo._cancel_confirmed_at is None  # 未点「是」：不计重领冷却


def test_recover_battle_failure_no_task_message_is_completed(monkeypatch):
    """取消时游戏提示身上无任务（_cancel_task 返回无需取消）：按本轮
    完成处理，不等待 6s 重领冷却（用户 2026-11-21）。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(
        xiuluo, "_cancel_task",
        lambda w: (True, "无需取消：游戏提示「你身上都没有任务，"
                         "不要来烦我」（已关闭提示）"))
    monkeypatch.setattr(xiuluo, "_task_gone_last_round", False)
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", True)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "completed" and "按本轮完成" in info
    assert clock.t == 0  # 无取消动作：不等待重领间隔
    assert xiuluo._task_gone_last_round is True
    assert xiuluo._battle_recovery_pending is False


def test_manual_recover_no_task_message_reclaims_without_wait(monkeypatch):
    """手动寻路失败恢复：游戏提示身上无任务 → 直接按无任务重新领取，
    不等待 6s 重领间隔、不置待恢复标记（用户 2026-11-21）。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(
        xiuluo, "_cancel_task",
        lambda w, assume_task=False: (
            True,
            "无需取消：游戏提示「你身上都没有任务，不要来烦我」（已关闭提示）"))
    monkeypatch.setattr(xiuluo, "_team_checked", True)
    monkeypatch.setattr(xiuluo, "_task_gone_last_round", False)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._manual_recover(_FakeWin(), check_team=False)
    assert ok is True and "任务已不存在" in info
    assert clock.t == 0  # 无取消动作：不等待重领间隔
    assert xiuluo._team_checked is False  # 重新领取前强制队伍检查
    assert xiuluo._task_gone_last_round is True


# ---- 取消二次确认弹窗（2026-11-15 用户实机：你真的要取消任务？→「是」） ----

def _confirm_dialog_items():
    return [("你真的要取消任务？", [500, 300, 800, 322], 0.95),
            ("不是", [500, 350, 560, 372], 0.95),
            ("是", [500, 380, 530, 402], 0.95)]


def test_click_cancel_confirm_clicks_exact_yes(monkeypatch):
    """确认弹窗点「是」：整行等值匹配，点中的是「是」行中心。"""
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(xiuluo.task_tracker, "green_text_center",
                        lambda img, rect, prefer_underline=True: (
                            (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2))
    assert xiuluo._click_cancel_confirm(_FakeWin(), _confirm_dialog_items(), None)
    assert clicked == [(515, 391)]


def test_click_cancel_confirm_tolerates_number_prefix(monkeypatch):
    """选项带数字序号前缀（「1 是」）仍可点中；「2 不是」不参与。"""
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(xiuluo.task_tracker, "green_text_center",
                        lambda img, rect, prefer_underline=True: (
                            (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2))
    items = [("你真的要取消任务？", [500, 300, 800, 322], 0.95),
             ("2 不是", [500, 350, 560, 372], 0.95),
             ("1 是", [500, 380, 530, 402], 0.95)]
    assert xiuluo._click_cancel_confirm(_FakeWin(), items, None)
    assert clicked == [(515, 391)]


def test_click_cancel_confirm_ignores_no_option(monkeypatch):
    """只有「不是」时不点击：子串陷阱（「是」是「不是」的子串）。"""
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    items = [("你真的要取消任务？", [500, 300, 800, 322], 0.95),
             ("不是", [500, 350, 560, 372], 0.95)]
    assert not xiuluo._click_cancel_confirm(_FakeWin(), items, None)
    assert clicked == []


def test_click_cancel_confirm_requires_dialog_text(monkeypatch):
    """无确认弹窗文案（世界浮字「是」）时不点击。"""
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    assert not xiuluo._click_cancel_confirm(
        _FakeWin(), [("是", [500, 380, 530, 402], 0.95)], None)
    assert clicked == []


def test_click_cancel_confirm_zoom_maps_and_clicks(monkeypatch):
    """2x 兜底：对话区裁剪放大识别后 rect 映射回窗口坐标再点「是」。"""
    crop_sizes = []

    def fake_zoom(crop):
        crop_sizes.append(crop.size)
        return ([("你真的要取消任务？", [74, 224, 341, 260], 0.99),
                 ("是", [72, 262, 106, 303], 1.0),
                 ("不是", [69, 286, 142, 339], 1.0)], None)

    monkeypatch.setattr(xiuluo.game_map, "ocr_zoom", fake_zoom)
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    monkeypatch.setattr(xiuluo.task_tracker, "green_text_center",
                        lambda img, rect, prefer_underline=True: (
                            (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2))
    l, t, r, b = xiuluo.task_tracker.DIALOG_REGION
    img = Image.new("RGB", (r + 200, b + 200))
    assert xiuluo._click_cancel_confirm_zoom(_FakeWin(), img, None)
    assert crop_sizes == [((r - l) * 2, (b - t) * 2)]
    # 「是」裁剪坐标 [72,262,106,303] → 窗口坐标 [436,401,453,421] 的中心
    assert clicked == [(444, 411)]


def test_cancel_task_uses_zoom_fallback_for_short_options(monkeypatch):
    """区域引擎漏检短选项：问题行在场时走 2x 放大兜底确认。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    monkeypatch.setattr(xiuluo, "_click_entry_ensuring_dialog",
                        lambda w, **k: (True, "ok", "使者", (1, 1), ([], None)))
    monkeypatch.setattr(xiuluo, "_click_cancel_option",
                        lambda w, items, img_np: True)
    monkeypatch.setattr(xiuluo, "_click_cancel_confirm",
                        lambda w, items, img_np: False)  # 区域引擎漏检短选项
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: (
                            [("你真的要取消任务？", [435, 377, 572, 403], 1.0)],
                            None))
    zoom_calls = []
    monkeypatch.setattr(xiuluo, "_click_cancel_confirm_zoom",
                        lambda w, img, img_np: zoom_calls.append(1) or True)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: (True, []))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is True and info == "已取消修罗任务"
    assert zoom_calls == [1]


def test_click_cancel_confirm_requires_green_and_dialog_region(monkeypatch):
    """非绿字或追踪面板区（x<PANEL_MAX_X）的「是」不点击。"""
    clicked = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicked.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: False)
    assert not xiuluo._click_cancel_confirm(
        _FakeWin(), _confirm_dialog_items(), None)
    monkeypatch.setattr(xiuluo.task_tracker, "is_green_text",
                        lambda img, rect: True)
    items = [("你真的要取消任务？", [500, 300, 800, 322], 0.95),
             ("是", [100, 380, 130, 402], 0.95)]
    assert not xiuluo._click_cancel_confirm(_FakeWin(), items, None)
    assert clicked == []


def test_click_cancel_option_uses_cancel_needles(monkeypatch):
    """取消选项匹配串与缓存键独立于领取选项。"""
    captured = {}

    def fake(win, items, img_np, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(claim_flow, "click_claim_option", fake)
    assert xiuluo._click_cancel_option(_FakeWin(), [], None) is True
    assert captured["needles"] == xiuluo.CANCEL_OPTION_NEEDLES
    assert captured["cache_key"] == "cancel_option"


def test_verify_cancel_flow_hovers_without_cancelling(monkeypatch):
    """验证流程只定位取消选项、点屏幕中间关对话框，不真正取消。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda w: False)
    monkeypatch.setattr(xiuluo.task_tracker, "has_task", lambda *a, **k: True)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot", lambda w: (True, ""))
    monkeypatch.setattr(
        xiuluo, "_click_entry_ensuring_dialog",
        lambda w, **k: (True, "ok", "使者", (1, 1), ([], None)))
    monkeypatch.setattr(
        xiuluo, "_find_claim_option",
        lambda items, img_np, needles=None:
        (600, 310, "我想取消任务", False, True))
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info = xiuluo.verify_cancel_flow(_FakeWin(), silent=True)
    assert ok is True and "验证通过" in info
    assert clicks == [xiuluo.SCREEN_CENTER_POS]


# ---- 取消校验的单帧消失判定（fast_absent 语义） ----

def test_task_absent_in_frame_panel_missing(monkeypatch):
    """追踪面板缺席且无遮挡面板：按任务消失（免 OCR 内容区）。"""
    monkeypatch.setattr(xiuluo.task_tracker, "track_content_region",
                        lambda img_np: None)
    monkeypatch.setattr(xiuluo.task_tracker, "panel_occluded",
                        lambda img: False)

    def _boom(*a, **k):
        raise AssertionError("面板缺席不应 OCR")

    monkeypatch.setattr(xiuluo.game_map, "ocr_region", _boom)
    assert xiuluo._task_absent_in_frame(_FakeWin().capture()) == (True, None)


def test_task_absent_in_frame_occluded_not_confirmed(monkeypatch):
    """面板被背包/地图遮挡：不能当作任务消失（返回无法确认）。"""
    monkeypatch.setattr(xiuluo.task_tracker, "track_content_region",
                        lambda img_np: None)
    monkeypatch.setattr(xiuluo.task_tracker, "panel_occluded",
                        lambda img: True)
    assert xiuluo._task_absent_in_frame(_FakeWin().capture()) == (False, None)


def test_task_absent_in_frame_panel_visible_uses_content_ocr(monkeypatch):
    """面板在场：只 OCR 内容区，按 has_task_in 判定。"""
    monkeypatch.setattr(xiuluo.task_tracker, "track_content_region",
                        lambda img_np: (0, 230, 300, 500))
    ocr_calls = []
    monkeypatch.setattr(xiuluo.game_map, "ocr_region",
                        lambda img, region: ocr_calls.append(region) or ([], None))
    monkeypatch.setattr(xiuluo.task_tracker, "has_task_in",
                        lambda items, img_np, title, fallback=(): False)
    assert xiuluo._task_absent_in_frame(_FakeWin().capture()) == (True, [])
    assert ocr_calls == [(0, 230, 300, 500)]


# ---- 战斗失败判定（用户 2026-11-14：战后任务仍在 / 落轮回司） ----

def test_on_battle_end_dead_in_lunhuisi(monkeypatch):
    """战斗结束位置为轮回司：判定失败（死亡优先于任务消失确认）。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("轮回司", 38, 20))
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is True
    assert xiuluo._battle_failed_reason == "dead"


def test_on_battle_end_task_present_after_kanda(monkeypatch):
    """看打战斗结束任务仍在追踪（复核仍在）：判定失败。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安城东", 100, 120))
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    monkeypatch.setattr(
        xiuluo, "_task_absent_in_frame",
        lambda img: (False, [("前往长安城东消灭修罗",
                              (30, 240, 290, 262), 0.9)]))
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is True
    assert xiuluo._battle_failed_reason == "task"
    assert clock.t >= 0.5  # 战斗结束首帧后复核一次


def test_on_battle_end_task_lingering_row_not_failure(monkeypatch):
    """完成瞬间任务行仍在退出动画：复核已消失 → 不判失败。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安城东", 100, 120))
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    seq = [(False, [("前往长安城东消灭修罗", (30, 240, 290, 262), 0.9)]),
           (True, [])]
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: seq.pop(0))
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert xiuluo._battle_failed_reason is None


def test_on_battle_end_random_battle_task_present_not_failure(monkeypatch):
    """随机遭遇战结束任务仍在属正常，不按失败处理（看打门控）。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安城东", 100, 120))
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", False)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: (False, [("前往长安城东消灭修罗",
                                              (30, 240, 290, 262), 0.9)]))
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert xiuluo._battle_failed_reason is None


def test_on_battle_end_position_unreadable_retries(monkeypatch):
    """战斗结束首帧位置未渲染：补读 2 次（共 ~1s），读不出不判失败。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: calls.append(1) or None)
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame", lambda img: (True, None))
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert len(calls) == 3
    assert clock.t >= 1.0


def test_on_battle_end_win_task_gone_not_failure(monkeypatch):
    """战斗胜利任务消失（位置非轮回司）：正常完成，不判失败。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安城东", 100, 120))
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", None)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: (True, None))
    assert xiuluo._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert xiuluo._battle_failed_reason is None


# ---- 战斗失败恢复（取消重领 / 非队长归队） ----

def test_recover_battle_failure_non_leader_keep_alive(monkeypatch):
    """非队长：不取消/重领，转保持模式并自动归队。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(
        xiuluo, "_check_team_with_retry",
        lambda w, **k: ("not_leader", "非队长（队伍面板无解散按钮）"))
    kept = []
    monkeypatch.setattr(xiuluo, "_keep_alive",
                        lambda w, info: kept.append(info))
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "failed" and "保持模式" in info
    assert kept and "非队长" in kept[0]


def test_recover_battle_failure_canceled_then_reclaim(monkeypatch):
    """队长取消成功：等 6s 重新领取，并复位队伍检查标记。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(xiuluo, "_cancel_task",
                        lambda w: (True, "已取消修罗任务"))
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    monkeypatch.setattr(xiuluo, "_team_checked", True)
    monkeypatch.setattr(xiuluo, "_task_gone_last_round", False)
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", True)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "canceled" and "重新领取" in info
    assert clock.t >= xiuluo.CANCEL_RE_CLAIM_DELAY
    assert xiuluo._team_checked is False
    assert xiuluo._task_gone_last_round is True
    assert xiuluo._battle_recovery_pending is False  # 恢复完成：清除重试标记


def test_recover_battle_failure_reclaim_wait_from_confirm_click(monkeypatch):
    """6s 间隔从确认弹窗「是」起算：取消校验已耗 4s 时只补 2s。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(xiuluo, "_cancel_task",
                        lambda w: (True, "已取消修罗任务"))
    # 虚拟时钟从 0 起：确认点击时间戳 -4 → 已过 4s
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", -4.0)
    status, _info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "canceled"
    assert clock.t == pytest.approx(xiuluo.CANCEL_RE_CLAIM_DELAY - 4.0)
    assert xiuluo._cancel_confirmed_at is None  # 计时起点用后清空


def test_recover_battle_failure_completed_when_task_gone(monkeypatch):
    """战斗胜利后死亡（任务已消失）：无需取消，按本轮完成处理。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "time", _FakeClock())
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(
        xiuluo, "_cancel_task",
        lambda w: (False, "任务追踪中无修罗任务，无需取消"))
    monkeypatch.setattr(xiuluo, "_task_gone_last_round", False)
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", True)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "completed" and "按本轮完成" in info
    assert xiuluo._task_gone_last_round is True
    assert xiuluo._battle_recovery_pending is False


def test_recover_battle_failure_lunhuisi_uses_talisman(monkeypatch):
    """落轮回司：世界地图不可用，改走修罗回城符返程（2026-11-11 实测）。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "time", _FakeClock())
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("轮回司", 38, 20))
    calls = []
    monkeypatch.setattr(
        xiuluo, "_return_to_npc",
        lambda w: calls.append("talisman") or (True, "已返回灵兽村"))
    monkeypatch.setattr(
        xiuluo, "_walk_to_claim_spot",
        lambda w, **k: (_ for _ in ()).throw(
            AssertionError("轮回司不应走世界地图")))
    monkeypatch.setattr(xiuluo, "_cancel_task",
                        lambda w: (True, "已取消修罗任务"))
    monkeypatch.setattr(xiuluo, "_cancel_confirmed_at", None)
    monkeypatch.setattr(xiuluo, "_team_checked", True)
    status, _info = xiuluo._recover_battle_failure(_FakeWin(), False)
    assert status == "canceled" and calls == ["talisman"]


def test_recover_battle_failure_return_failed(monkeypatch):
    """回灵兽村多次失败：报告失败（主循环下轮重试）。"""
    clock = _FakeClock()
    monkeypatch.setattr(xiuluo, "time", clock)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("长安", 100, 100))
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot",
                        lambda w, **k: (False, "寻路超时"))
    monkeypatch.setattr(xiuluo, "_return_to_npc",
                        lambda w: (False, "背包打开失败"))
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", False)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), False)
    assert status == "failed" and "回灵兽村失败" in info
    assert clock.t >= 3 * 3.0  # 3 次重试间隔
    # 恢复未完成：下一轮先重试恢复，不得回走任务跟踪
    assert xiuluo._battle_recovery_pending is True


def test_recover_battle_failure_cancel_failed_sets_pending(monkeypatch):
    """取消任务失败：标记待恢复重试（下一轮不回走跟踪）。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "time", _FakeClock())
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda w, img=None: ("灵兽村", 117, 95))
    monkeypatch.setattr(xiuluo, "_cancel_task",
                        lambda w: (False, "取消对话框未识别到绿色选项「我想取消任务」"))
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", False)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "failed" and "取消任务失败" in info
    assert xiuluo._battle_recovery_pending is True


def test_recover_battle_failure_transient_identity_sets_pending(monkeypatch):
    """身份瞬态无法判定：标记待恢复重试，不贸然取消任务。"""
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_check_team_with_retry",
                        lambda w, **k: ("not_leader", "队长无法判定（面板按钮未命中）"))
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", False)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "failed" and "身份无法判定" in info
    assert xiuluo._battle_recovery_pending is True


def test_follow_to_done_wires_battle_end_handler(monkeypatch):
    """跟踪起点复位看打标记与失败原因，并注入战斗结束回调。"""
    captured = {}

    def fake_follow(win, first_items=None, **kwargs):
        captured.update(kwargs)
        return True, "完成"

    monkeypatch.setattr(claim_flow, "follow_to_done", fake_follow)
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", True)
    monkeypatch.setattr(xiuluo, "_battle_failed_reason", "dead")
    ok, _info = xiuluo._follow_to_done(_FakeWin())
    assert ok is True
    assert captured["battle_end_handler"] is xiuluo._on_battle_end
    assert xiuluo._fight_via_kanda is False
    assert xiuluo._battle_failed_reason is None


# ---- claim_flow 批次 2 包装接线与到达判据（2026-11-18）----

def test_claim_flow_open_map_wiring(monkeypatch):
    """包装必须把灵兽村参数与筛选状态回调原样传给共享实现。"""
    captured = {}
    win = _FakeWin()

    def fake(win, pos=None, **kwargs):
        captured["win"], captured["pos"] = win, pos
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(claim_flow, "open_claim_map", fake)
    assert xiuluo._open_claim_map(win, ("灵兽村", 1, 2)) == (True, "ok")
    assert captured["win"] is win
    assert captured["pos"] == ("灵兽村", 1, 2)
    assert captured["map_name"] == xiuluo.LINGSHOUCUN
    assert captured["map_pos"] == xiuluo.LINGSHOU_MAP_POS
    assert captured["prefix"] == "[修罗]"
    assert captured["on_filter_state"] is xiuluo._set_claim_task_filter


def test_claim_flow_set_filter_state_writes_global(monkeypatch):
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", True)
    xiuluo._set_claim_task_filter(False)
    assert xiuluo._claim_task_filter_on is False


def test_claim_flow_relocate_wiring(monkeypatch):
    captured = {}
    win = _FakeWin()

    def fake(win, **kwargs):
        captured["win"] = win
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(claim_flow, "relocate_to_claim_spot", fake)
    assert xiuluo._relocate_to_claim_spot(win) == (True, "ok")
    assert captured["win"] is win
    assert captured["map_name"] == xiuluo.LINGSHOUCUN
    assert captured["open_map_fn"] is xiuluo._open_claim_map
    assert captured["close_map_fn"] is xiuluo._close_claim_map_before_arrival
    assert captured["arrival_fn"] is xiuluo._relocate_arrival
    assert captured["timeout"] == xiuluo.CLAIM_ARRIVE_TIMEOUT


def test_relocate_arrival_button_requires_map_gate(monkeypatch):
    """长安命中领取按钮模板：不得判到达。"""
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: (300, 300))
    monkeypatch.setattr(xiuluo, "_near_claim_spot", lambda pos, tol: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.0)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert xiuluo._relocate_arrival(
        None, img, img, ("长安", 367, 27)) is None


def test_relocate_arrival_button_in_map(monkeypatch):
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: (300, 300))
    monkeypatch.setattr(xiuluo, "_near_claim_spot", lambda pos, tol: False)
    img = np.zeros((831, 1378, 3), np.uint8)
    desc = xiuluo._relocate_arrival(None, img, img, ("灵兽村", 60, 60))
    assert desc and "领取按钮可见" in desc


def test_relocate_arrival_dialog_checked_even_when_filter_off(monkeypatch):
    """小地图「任务」未勾选时到达等待也要检查对话框（2026-11-22 实机：
    上一轮领取滞留的确认/收尾对话框挡住领取入口，两轮到达各空耗 90s）。"""
    ocr = []
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", False)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_near_claim_spot", lambda pos, tol: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(xiuluo.game_map, "ocr_region",
                        lambda img, region: ocr.append(1) or ([], None))
    monkeypatch.setattr(xiuluo, "_handle_team_confirm", lambda *a: None)
    monkeypatch.setattr(xiuluo, "_handle_team_warning", lambda *a: False)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert xiuluo._relocate_arrival(
        None, img, img, ("灵兽村", 60, 60)) is None
    assert ocr == [1]


def test_relocate_arrival_filter_none_still_checks_dialog(monkeypatch):
    monkeypatch.setattr(xiuluo, "_claim_task_filter_on", None)
    monkeypatch.setattr(xiuluo.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_near_claim_spot", lambda pos, tol: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(xiuluo.game_map, "ocr_region",
                        lambda img, region: ([], None))
    monkeypatch.setattr(xiuluo, "_find_claim_option",
                        lambda items, img_np: (1, 2, "选项", False, True))
    img = np.zeros((831, 1378, 3), np.uint8)
    desc = xiuluo._relocate_arrival(None, img, img, ("灵兽村", 60, 60))
    assert desc and "自动弹出" in desc


# ---------------- 到达等待对话框处理（2026-11-22 12:17 实机） ----------------

class _Still:
    """站定判定桩：seconds 固定值。"""

    def __init__(self, sec):
        self.sec = sec

    def reset(self):
        pass

    def seconds(self, img):
        return self.sec


def _patch_arrival_dialog_env(monkeypatch, items, still_sec,
                              confirm=None, warning=False,
                              claim_found=False, green_row=False):
    clicks = []
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(xiuluo.game_map, "ocr_region",
                        lambda img, region: (items, None))
    monkeypatch.setattr(
        xiuluo, "_find_claim_option",
        lambda *a: (1, 2, "选项", False, True) if claim_found else None)
    monkeypatch.setattr(xiuluo, "_handle_team_confirm", lambda *a: confirm)
    monkeypatch.setattr(xiuluo, "_handle_team_warning", lambda *a: warning)
    monkeypatch.setattr(xiuluo, "green_option_row_present",
                        lambda img, its: green_row)
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda w: None)
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "_arrival_still", _Still(still_sec))
    return clicks


def test_arrival_dialog_signal_claim_option_is_arrival(monkeypatch):
    """对话框含领取绿色选项：直接按到达处理（与筛选状态无关）。"""
    _patch_arrival_dialog_env(monkeypatch, [], 5.0, claim_found=True)
    img = np.zeros((831, 1378, 3), np.uint8)
    arrived, stop = xiuluo._arrival_dialog_signal(
        None, img, ("灵兽村", 60, 60))
    assert stop is None and "领取对话框已自动弹出" in arrived


def test_arrival_dialog_signal_team_confirm_stops(monkeypatch):
    """队伍确认弹窗：已按设置点选项，提前结束到达等待（下轮重试）。"""
    clicks = _patch_arrival_dialog_env(monkeypatch, [], 5.0,
                                       confirm="declined")
    img = np.zeros((831, 1378, 3), np.uint8)
    arrived, stop = xiuluo._arrival_dialog_signal(
        None, img, ("灵兽村", 60, 60))
    assert arrived is None and "确认弹窗" in stop
    assert clicks == []  # 选项点击由确认处理器完成，不点屏


def test_arrival_dialog_signal_dismisses_no_option_dialog_when_still(
        monkeypatch):
    """站定 + 无选项对话框（上轮遗留收尾/提示）：点屏幕中间关闭。"""
    items = [("请速去四圣庄消灭修罗，阻止它为非作歹。", [450, 400, 900, 420], 0.9)]
    clicks = _patch_arrival_dialog_env(monkeypatch, items, 5.0)
    img = np.zeros((831, 1378, 3), np.uint8)
    arrived, stop = xiuluo._arrival_dialog_signal(
        None, img, ("灵兽村", 60, 60))
    assert arrived is None and stop is None
    assert clicks == [xiuluo.SCREEN_CENTER_POS]


def test_arrival_dialog_signal_no_click_while_moving(monkeypatch):
    """人物可能仍在寻路（位置区像素在变）：不点屏。"""
    items = [("提示文案", [450, 400, 900, 420], 0.9)]
    clicks = _patch_arrival_dialog_env(monkeypatch, items, 0.5)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert xiuluo._arrival_dialog_signal(
        None, img, ("灵兽村", 60, 60)) == (None, None)
    assert clicks == []


def test_arrival_dialog_signal_unknown_option_dialog_no_click(monkeypatch):
    """有绿字选项但不认识（如取消确认）：站定也不点屏（防误选）。"""
    items = [("你真的要取消任务？", [450, 400, 900, 420], 0.9)]
    clicks = _patch_arrival_dialog_env(monkeypatch, items, 5.0, green_row=True)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert xiuluo._arrival_dialog_signal(
        None, img, ("灵兽村", 60, 60)) == (None, None)
    assert clicks == []


def test_arrival_dialog_signal_ignores_dialog_away_from_spot(monkeypatch):
    """不在领取点附近（仍在路上）：只识别领取对话框，不点屏。"""
    items = [("提示文案", [450, 400, 900, 420], 0.9)]
    clicks = _patch_arrival_dialog_env(monkeypatch, items, 5.0)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert xiuluo._arrival_dialog_signal(
        None, img, ("长安", 1, 2)) == (None, None)
    assert clicks == []


# ---------------- 兼容无快捷寻路（手动寻路，用户 2026-11-20） ----------------

def test_parse_claim_destination_normal(monkeypatch):
    """领取对话框文案解析：全角括号/逗号 → (地图名, x, y)。"""
    logged = []
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": logged.append(m))
    items = [("去往洛阳城（191，110）消灭修罗", (500, 300, 800, 330), 0.95)]
    assert xiuluo._parse_claim_destination(None, items) == ("洛阳城", 191, 110)
    assert any("领取对话框文案" in m for m in logged)  # 全文写日志便于调整


def test_parse_claim_destination_layer(monkeypatch):
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": None)
    items = [("速去大雁塔四层(10, 20)消灭修罗", (500, 300, 800, 330), 0.95)]
    assert xiuluo._parse_claim_destination(None, items) == ("大雁塔四层", 10, 20)


def test_parse_claim_destination_ignores_panel_lines(monkeypatch):
    """只认对话框区域行（x≥PANEL_MAX_X）；追踪面板行里的坐标不算。"""
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": None)
    items = [("洛阳城（191,110）", (10, 230, 380, 250), 0.95)]
    assert xiuluo._parse_claim_destination(None, items) is None


def test_parse_claim_destination_no_coords(monkeypatch):
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": None)
    items = [("阻止它为非作歹", (500, 300, 800, 330), 0.95)]
    assert xiuluo._parse_claim_destination(None, items) is None


def test_task_markers_dedupes_and_sorts(monkeypatch):
    """多命中：重叠去重（24px 内视为同一标记），按 y/x 排序。"""
    seen = {}

    def _find(img, icon, threshold=0.8, region=None):
        seen["icon"] = icon
        return [(100, 200), (106, 204), (300, 100)]

    monkeypatch.setattr(xiuluo.game_map, "find_all_template_in", _find)
    assert xiuluo._task_markers(_FakeWin()) == [(300, 100), (100, 200)]
    assert seen["icon"] == "xiuluo_renwu_marker.png"


def test_task_marker_template_is_user_provided_sign():
    """手动寻路任务标记模板：用户 2026-11-21 提供的「任务」小牌（41x19 RGB）。"""
    from pathlib import Path
    from PIL import Image
    path = (Path(__file__).resolve().parent.parent / "res/img"
            / xiuluo.XIULUO_MARKER_ICON)
    assert path.is_file()
    im = Image.open(path)
    assert im.size == (41, 19)
    assert im.mode == "RGB"


def test_manual_nav_round_without_dest(monkeypatch):
    called = []
    monkeypatch.setattr(xiuluo.map_nav, "navigate_to_map_coordinate",
                        lambda *a, **k: called.append(1))
    ok, info = xiuluo._manual_nav_round(_FakeWin(), None)
    assert not ok and "未解析" in info
    assert called == []


def test_manual_nav_round_navigates_and_fights(monkeypatch):
    calls = []
    monkeypatch.setattr(
        xiuluo.map_nav, "navigate_to_map_coordinate",
        lambda win, name, x, y, log_fn=None, on_walk_start=None: (
            calls.append((name, x, y)) or (True, "到达")))
    monkeypatch.setattr(xiuluo, "_click_markers_and_fight",
                        lambda win, dest=None: (True, "战斗已结束"))
    ok, _info = xiuluo._manual_nav_round(_FakeWin(), ("洛阳城", 191, 110))
    assert ok and calls == [("洛阳城", 191, 110)]


def test_manual_nav_round_wires_bars_pass_to_walk_start(monkeypatch):
    """手动寻路回合：组号队员血蓝 pass 挂在 on_walk_start（行走期触发）。"""
    captured = {}

    def _fake_nav(win, name, x, y, log_fn=None, on_walk_start=None):
        captured["cb"] = on_walk_start
        return True, "到达"

    monkeypatch.setattr(
        xiuluo.map_nav, "navigate_to_map_coordinate", _fake_nav)
    monkeypatch.setattr(xiuluo, "_click_markers_and_fight",
                        lambda win, dest=None: (True, "战斗已结束"))
    bars = []
    monkeypatch.setattr(xiuluo, "_maybe_tab_bars_pass",
                        lambda win: bars.append(win))
    ok, _info = xiuluo._manual_nav_round(_FakeWin(), ("洛阳城", 191, 110))
    assert ok
    assert callable(captured.get("cb"))
    assert bars == []  # 回调只在位移启动后由 map_nav 调用
    captured["cb"]()
    assert len(bars) == 1


def test_click_markers_fight_flow(monkeypatch):
    """单个标记 → 看打对话 → 战斗开始 → 等战斗结束。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "_task_markers",
                        lambda win, img=None: [(300, 260)])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    monkeypatch.setattr(xiuluo, "_wait_fight_dialog",
                        lambda win, timeout: "dialog")
    monkeypatch.setattr(xiuluo, "_wait_battle_start",
                        lambda win, timeout: True)
    monkeypatch.setattr(xiuluo, "_wait_manual_battle_end",
                        lambda win: (True, "战斗已结束（手动寻路回合）"))
    ok, info = xiuluo._click_markers_and_fight(_FakeWin())
    assert ok and info.startswith("战斗已结束")
    assert clicks == [(300, 260)]


def test_click_markers_alt_fallback_fails(monkeypatch):
    """无标记 → Alt+A → 仍无 → 名字 OCR 也无 → 点屏幕中间释放，判失败。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(xiuluo, "_task_markers", lambda win, img=None: [])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    hotkeys = []
    monkeypatch.setattr(xiuluo.app_utils, "press_alt_hotkey",
                        lambda win, key, label, prefix="": (
                            hotkeys.append(key) or (True, "alt")))
    monkeypatch.setattr(xiuluo.world_target, "find_suffix_points",
                        lambda img, suffixes: [])
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    ok, info = xiuluo._click_markers_and_fight(_FakeWin())
    assert not ok and "Alt+A" in info and "OCR 名字兜底" in info
    assert hotkeys == ["a"]
    assert CENTER in clicks


def test_click_markers_ocr_name_fallback_fights(monkeypatch):
    """模板/Alt+A 都无标记 → OCR 世界区「xx修罗」名字 → 点击上方开打。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(xiuluo, "_task_markers", lambda win, img=None: [])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo.app_utils, "press_alt_hotkey",
                        lambda *a, **k: (True, "alt"))
    seen = {}

    def _suffix(img, suffixes):
        seen["suffixes"] = suffixes
        return [("狂风修罗", (300, 260))]

    monkeypatch.setattr(xiuluo.world_target, "find_suffix_points", _suffix)
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    monkeypatch.setattr(xiuluo, "_wait_fight_dialog",
                        lambda win, timeout: "dialog")
    monkeypatch.setattr(xiuluo, "_wait_battle_start",
                        lambda win, timeout: True)
    monkeypatch.setattr(xiuluo, "_wait_manual_battle_end",
                        lambda win: (True, "战斗已结束（手动寻路回合）"))
    ok, info = xiuluo._click_markers_and_fight(_FakeWin())
    assert ok and info.startswith("战斗已结束")
    assert seen["suffixes"] == ("修罗", "修羅")  # 目标名必为 xx修罗
    assert (300, 260) in clicks                   # 名字上方 40px 点击点
    assert CENTER not in clicks                   # 命中后不释放 Alt+A 点击


def test_click_markers_coord_screen_fallback(monkeypatch):
    """标记找不到 → 特殊地图先按任务坐标反查屏幕点（Alt+A 之前）。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(xiuluo, "_task_markers", lambda win, img=None: [])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    hotkeys = []
    monkeypatch.setattr(xiuluo.app_utils, "press_alt_hotkey",
                        lambda *a, **k: hotkeys.append(1) or (True, "alt"))
    seen = {}

    def _coord(win, name, x, y, emit=None):
        seen["args"] = (name, x, y)
        return (700, 400)

    monkeypatch.setattr(xiuluo.map_nav, "special_coord_screen_point", _coord)
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    monkeypatch.setattr(xiuluo, "_wait_fight_dialog",
                        lambda win, timeout: "dialog")
    monkeypatch.setattr(xiuluo, "_wait_battle_start",
                        lambda win, timeout: True)
    monkeypatch.setattr(xiuluo, "_wait_manual_battle_end",
                        lambda win: (True, "战斗已结束（手动寻路回合）"))
    ok, info = xiuluo._click_markers_and_fight(
        _FakeWin(), ("莲花洞", 40, 48))
    assert ok and info.startswith("战斗已结束")
    assert seen["args"] == ("莲花洞", 40, 48)
    assert clicks == [(700, 400)]  # 反查点点击
    assert hotkeys == []           # 反查命中，不按 Alt+A


def test_click_markers_ocr_skipped_when_dialog_present(monkeypatch):
    """名字 OCR 兜底前有对话框：不 OCR 世界区、不点名字（防点中对话文字）。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(xiuluo, "_task_markers", lambda win, img=None: [])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo.app_utils, "press_alt_hotkey",
                        lambda *a, **k: (True, "alt"))
    ocr_calls = []
    monkeypatch.setattr(xiuluo.world_target, "find_suffix_points",
                        lambda img, suffixes: ocr_calls.append(1) or [])
    clicks = []
    monkeypatch.setattr(xiuluo, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)
    ok, info = xiuluo._click_markers_and_fight(_FakeWin())
    assert not ok and "对话框在场" in info
    assert ocr_calls == []
    assert CENTER in clicks  # 释放 Alt+A


def test_cancel_task_assume_task_skips_tracker(monkeypatch):
    """无追踪取消：assume_task=True 不查面板；默认路径仍以面板为准。"""
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: False)
    tracker_calls = []
    monkeypatch.setattr(xiuluo.task_tracker, "has_task",
                        lambda *a, **k: tracker_calls.append(1) or False)
    monkeypatch.setattr(xiuluo, "_walk_to_claim_spot",
                        lambda win, just_returned=False: (False, "测试停止"))
    ok, info = xiuluo._cancel_task(_FakeWin(), assume_task=True)
    assert not ok and "测试停止" in info
    assert tracker_calls == []
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert not ok and "无需取消" in info
    assert tracker_calls == [1]


def test_click_markers_battle_skips_alt(monkeypatch):
    """进入战斗就不用 Alt+A：等战斗结束按原流程收尾（用户 2026-11-20）。"""
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", False)
    state = {"battle": True}
    monkeypatch.setattr(xiuluo, "is_in_battle", lambda win: state["battle"])
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    hotkeys = []
    monkeypatch.setattr(xiuluo.app_utils, "press_alt_hotkey",
                        lambda *a, **k: hotkeys.append(1) or (True, "alt"))
    markers_called = []
    monkeypatch.setattr(xiuluo, "_task_markers",
                        lambda win, img=None: markers_called.append(1) or [])
    monkeypatch.setattr(xiuluo, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(xiuluo, "move_away", lambda win: None)

    def _wait_end_manual(win):
        state["battle"] = False
        return True, "战斗已结束（手动寻路回合）"

    monkeypatch.setattr(xiuluo, "_wait_manual_battle_end", _wait_end_manual)
    ok, info = xiuluo._click_markers_and_fight(_FakeWin())
    assert ok and info.startswith("战斗已结束")
    assert hotkeys == []          # 不再使用 Alt+A
    assert markers_called == []   # 也不再找任务模板
    assert xiuluo._fight_via_kanda is True  # 战后按原流程返程


def test_force_manual_default_off():
    """强制手动寻路开关默认关闭（测试用，用户 2026-11-20）。"""
    from src.core import settings as core_settings
    assert core_settings.load_xiuluo_force_manual() is False


def test_parse_claim_destination_wait_retries(monkeypatch):
    """强制模式：校验帧没解析到目的地时短窗轮询现场 OCR。"""
    calls = []

    def _parse(win, items=None):
        calls.append(items)
        return None if len(calls) < 2 else ("洛阳城", 191, 110)

    monkeypatch.setattr(xiuluo, "_parse_claim_destination", _parse)
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    assert xiuluo._parse_claim_destination_wait(None) == ("洛阳城", 191, 110)
    assert len(calls) == 2


def test_parse_claim_destination_gates_dialog_template(monkeypatch):
    """现场 OCR 前有对话框模板门控：对话框不在场不跑区域 OCR。"""
    monkeypatch.setattr(xiuluo, "dialog_frame_score", lambda img: 0.1)

    def _boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("对话框不在场不应 OCR")

    monkeypatch.setattr(xiuluo.game_map, "ocr_region", _boom)
    assert xiuluo._parse_claim_destination(_FakeWin(), None) is None


def test_manual_nav_default_on():
    """兼容无快捷寻路默认开启（有追踪时行为不变）。"""
    from src.core import settings as core_settings
    assert core_settings.load_xiuluo_manual_nav() is True


def test_schemes_include_manual_nav_keys():
    """两个新开关进入配置方案表（快照/应用/导出都覆盖）。"""
    from src.core import schemes, settings as core_settings
    spec = schemes._settings_spec()
    assert spec[core_settings._XIULUO_MANUAL_NAV_KEY] is True
    assert spec[core_settings._XIULUO_FORCE_MANUAL_KEY] is False


def test_wait_fight_dialog_direct_battle_marks_kanda(monkeypatch):
    """点标记直接进战斗：必须置 _fight_via_kanda（否则战后不返程）。"""
    monkeypatch.setattr(xiuluo, "_fight_via_kanda", False)
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: True)
    res = xiuluo._wait_fight_dialog(_FakeWin(), 1.0)
    assert res == "fight"
    assert xiuluo._fight_via_kanda is True


def test_wait_manual_battle_end_lunhuisi_fails(monkeypatch):
    """战斗后落轮回司 = 战败（不能按本轮完成返程）。"""
    monkeypatch.setattr(xiuluo, "_wait_battle_end",
                        lambda win, timeout=60: (True, "战斗已结束"))
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda *a, **k: ("轮回司", 10, 20))
    monkeypatch.setattr(xiuluo.team, "is_in_lunhuisi",
                        lambda pos: pos is not None and pos[0] == "轮回司")
    ok, info = xiuluo._wait_manual_battle_end(_FakeWin())
    assert not ok and "轮回司" in info


def test_wait_manual_battle_end_task_still_tracked(monkeypatch):
    """战斗后任务仍在追踪 = 战斗失败（防随机遭遇按本轮完成）。"""
    monkeypatch.setattr(xiuluo, "_wait_battle_end",
                        lambda win, timeout=60: (True, "战斗已结束"))
    monkeypatch.setattr(xiuluo.time, "sleep", lambda s: None)
    monkeypatch.setattr(xiuluo.game_map, "read_map_position",
                        lambda *a, **k: ("龙窟一层", 10, 20))
    monkeypatch.setattr(xiuluo.team, "is_in_lunhuisi", lambda pos: False)
    monkeypatch.setattr(
        xiuluo, "_task_absent_in_frame",
        lambda img: (False, [("常规玩法", (10, 200, 200, 220), 0.9)]))
    ok, info = xiuluo._wait_manual_battle_end(_FakeWin())
    assert not ok and "仍在追踪" in info


def test_parse_claim_destination_real_text(monkeypatch):
    """用户提供的真机文案：请速去大雁塔四层(108, 37)处消灭修罗，阻止它为非作歹。"""
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": None)
    items = [("请速去大雁塔四层(108, 37)处消灭修罗，阻止它为非作歹。",
              (430, 300, 950, 330), 0.96)]
    assert xiuluo._parse_claim_destination(None, items) == ("大雁塔四层", 108, 37)


def test_parse_claim_destination_real_text_split_lines(monkeypatch):
    """真机文案被 OCR 拆成两行（换行在句尾）时仍能拼接解析。"""
    monkeypatch.setattr(xiuluo, "log", lambda m, l="INFO": None)
    items = [("请速去大雁塔四层(108, 37)处消灭修罗，阻止它为非作",
              (430, 300, 950, 318), 0.95),
             ("歹。", (430, 314, 470, 334), 0.92)]
    assert xiuluo._parse_claim_destination(None, items) == ("大雁塔四层", 108, 37)
