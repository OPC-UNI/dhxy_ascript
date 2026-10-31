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
