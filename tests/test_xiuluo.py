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
