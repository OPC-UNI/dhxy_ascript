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
