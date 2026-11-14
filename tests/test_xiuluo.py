"""xiuluo.py：巫医 NPC 识别窗口的遮挡对话框处理（2026-10-31 优化）。

验证点：
- NPC 模板可见时不 OCR 不点击，直接返回命中
- 模板被「阻止它为非作歹」对话框遮挡时点击屏幕中间取消（仅一次），
  取消后窗口顺延、模板恢复可见即返回
- 对话框持续存在也只点一次（防重复点击误点世界走位），超时取消
- 无对话框遮挡的普通漏检：只等待超时，不点击
"""
import pytest
from PIL import Image

from src.tasks import xiuluo
from src.workflows import claim_flow

CENTER = xiuluo.SCREEN_CENTER_POS  # (689, 415)


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
    """走位→点入口模板→点「我想取消任务」→连续 2 轮无任务确认成功。"""
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
    monkeypatch.setattr(xiuluo, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xiuluo, "_task_absent_in_frame",
                        lambda img: (True, []))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is True and info == "已取消修罗任务"
    # 入口打开对话框按取消选项判定，且用独立缓存键（不与领取选项共键）
    assert captured["option_needles"] == xiuluo.CANCEL_OPTION_NEEDLES
    assert captured["option_cache_key"] == "cancel_option"
    assert captured["action"] == "取消任务"
    assert clicks == [1]


def test_cancel_task_fails_when_task_remains(monkeypatch):
    """点中选项但任务仍在面板：超时返回失败并提示残留文案。"""
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
    monkeypatch.setattr(
        xiuluo, "_task_absent_in_frame",
        lambda img: (False, [("前往瑶池消灭修罗", (20, 240, 300, 262), 0.9)]))
    monkeypatch.setattr(xiuluo.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(xiuluo, "log", lambda *a, **k: None)
    ok, info = xiuluo._cancel_task(_FakeWin())
    assert ok is False
    assert "仍在追踪面板" in info and "消灭修罗" in info
    assert clock.t >= xiuluo.CANCEL_VERIFY_WAIT - 0.01


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
        lambda items, img_np, needles=None: (600, 310, "我想取消任务", False))
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
    monkeypatch.setattr(xiuluo, "_team_checked", True)
    monkeypatch.setattr(xiuluo, "_task_gone_last_round", False)
    monkeypatch.setattr(xiuluo, "_battle_recovery_pending", True)
    status, info = xiuluo._recover_battle_failure(_FakeWin(), True)
    assert status == "canceled" and "重新领取" in info
    assert clock.t >= xiuluo.CANCEL_RE_CLAIM_DELAY
    assert xiuluo._team_checked is False
    assert xiuluo._task_gone_last_round is True
    assert xiuluo._battle_recovery_pending is False  # 恢复完成：清除重试标记


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
