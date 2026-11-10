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

from src import xiuluo

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
    monkeypatch.setattr(xiuluo, "_find_wuyi_option",
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

    monkeypatch.setattr(xiuluo, "_find_wuyi_option", _boom)
    ok, info = xiuluo._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, (650, 420)]
