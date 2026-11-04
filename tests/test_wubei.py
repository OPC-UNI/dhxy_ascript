"""wubei.py：黄袍怪多杀任务战斗结束后的追踪链接重点点击。

点击位置回归 2026-11-03：旧实现点 OCR 文本矩形中心——整行
「前往平顶山消灭黄袍怪」矩形中心 (99,261) 落在可点击链接「平顶山」
右缘外（同帧对比 follow 路径 green_text_center 点 (76,268)），偏 23px
点到非链接文字偶现点击无反应。修正后点下划线中心（链接专属标记）。
"""
import numpy as np

from src import wubei


def test_click_track_link_clicks_underline_not_rect_center(monkeypatch):
    """整行矩形中心在链接「平顶山」右缘外，须点下划线中心。

    像素形态按 2026-11-03 实测对比重建：行文字散碎笔画 y251-264
    （「平顶山」为亮绿链接 x48-99，其余白字不可点），下划线仅覆盖
    链接部分（x48-99, y271, 51px 实线）。
    """
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[251:264, 14:48:4] = (200, 200, 200)   # 「前往」白字（散碎笔画）
    img[251:264, 48:99:4] = (0, 255, 0)       # 「平顶山」亮绿链接文字
    img[251:264, 99:185:4] = (200, 200, 200)  # 「消灭黄袍怪」白字
    img[271, 48:99] = (0, 255, 0)             # 下划线只覆盖链接部分
    items = [
        ("前往平顶山消灭魔尊", [14, 228, 185, 248], 0.99),   # 无关键词：跳过
        ("黄袍怪(2/3)", [14, 230, 90, 246], 0.90),            # 有词但白字：跳过
        ("前往平顶山消灭黄袍怪", [14, 249, 185, 269], 0.99),  # 链接行
    ]
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    assert wubei._click_track_link(object(), items, img, "黄袍") is True
    # 下划线 51px 实线（x48-98）中心 x73、上方 2px；
    # 矩形中心 (99,259) 在链接右缘外，旧实现会点偏
    assert clicks == [(73, 269)]


def test_click_track_link_no_match_no_click(monkeypatch):
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    items = [("前往平顶山消灭魔尊", [14, 249, 185, 269], 0.99)]
    assert wubei._click_track_link(object(), items, img, "黄袍") is False
    assert clicks == []


# ---- 巫医链：轮询点击时间戳立即返回 / 超时兜底（2026-11-04 对齐修罗） ----

import pytest
from PIL import Image

DIALOG_ITEMS = [("我是超级巫医，可以帮你医治召唤兽",
                 (400, 300, 900, 600), 0.96)]
NPC_PT = (661, 187)
OPTION_PT = (534, 399)


class _FakeClock:
    """可控时钟：sleep 推进虚拟时间，time 返回虚拟时间。"""

    def __init__(self):
        self.t = 1000.0  # 真实时间戳语义（>0：区分 last_wuyi_option_at=0）

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _FakeWin:
    def capture(self, force_activate=False):
        return Image.new("RGB", (60, 60))


@pytest.fixture
def wuyi_chain(monkeypatch):
    """装配 wubei._run_wuyi_chain 外部依赖：NPC 立即可见、超时缩短为 1s。"""
    clock = _FakeClock()
    clicks = []
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "is_in_battle", lambda win: False)
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda win: (wubei.BAOXIANGGUO, *wubei.WUYI_SPOT))
    monkeypatch.setattr(wubei.game_map, "find_template",
                        lambda win, icon, *a, **k: NPC_PT)
    monkeypatch.setattr(wubei.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), None))
    monkeypatch.setattr(wubei, "_find_wuyi_option",
                        lambda items, img_np: OPTION_PT)
    monkeypatch.setattr(wubei, "WUYI_HANDLE_TIMEOUT", 1.0)  # 单轮即超时
    monkeypatch.setattr(wubei.battle, "last_wuyi_option_at", 0)
    monkeypatch.setattr(wubei.battle, "last_wuyi_option_pos", None)
    return {"clock": clock, "clicks": clicks}


def test_wuyi_chain_click_detected_returns_immediately(wuyi_chain):
    """战斗线程点击后（时间戳晚于点 NPC）→ 随机 1~1.2s 即返回，
    不等对话框关闭（点击即寻路）。"""
    wubei.battle.last_wuyi_option_at = 1000.5  # 点 NPC 之后战斗线程点击
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "自动巫医完成" in info
    assert wuyi_chain["clicks"] == [NPC_PT]
    t = wuyi_chain["clock"].t
    assert 1001.0 <= t <= 1001.2


def test_wuyi_chain_timeout_no_click_self_clicks(wuyi_chain):
    """超时时战斗线程未点击且对话框在场 → 兜底自行点击一次。"""
    wubei.battle.last_wuyi_option_at = 0
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, OPTION_PT]


def test_wuyi_chain_timeout_self_click_uses_cached_pos(wuyi_chain, monkeypatch):
    """兜底自点优先用绿字校验过的缓存位置，不重新识别。"""
    wubei.battle.last_wuyi_option_at = 0
    wubei.battle.last_wuyi_option_pos = (650, 420)  # 历史缓存（受伤弹窗）
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    img_np[410:430, 610:690] = (0, 186, 0)  # 缓存点周围亮绿
    monkeypatch.setattr(wubei.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), img_np))

    def _boom(*a, **k):
        raise AssertionError("有缓存不应再识别")

    monkeypatch.setattr(wubei, "_find_wuyi_option", _boom)
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, (650, 420)]
