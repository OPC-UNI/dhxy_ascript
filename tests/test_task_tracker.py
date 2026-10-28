"""task_tracker.py：任务分组/查找/签名、绿字判定、面板差分门（TrackGate）。"""
import time

import numpy as np
import pytest
from PIL import Image

from src import task_tracker
from src import map as game_map

# ---- 任务分组与查找（合成 OCR items） ----

def _item(txt, x=10, y=240, score=0.95):
    return (txt, [x, y, x + 100, y + 30], score)


def test_group_tasks_and_find():
    items = [_item("五环[五环]"), _item("去找大唐境内(358, 21)惯骗[1/5]", y=280)]
    tasks = task_tracker.group_tasks(items)
    task = task_tracker.find_task(tasks, "五环")
    assert task is not None
    assert task["title"] == "五环[五环]"
    assert task["text"] == "去找大唐境内(358, 21)惯骗[1/5]"
    assert task_tracker.find_task(tasks, "不存在的任务") is None


def test_group_tasks_ignores_dialog_region_lines():
    """x≥PANEL_MAX_X 的行（对话框区）不参与任务内容采集。"""
    items = [_item("客栈[客栈任务]"), _item("对话选项", x=500)]
    tasks = task_tracker.group_tasks(items)
    assert tasks[0]["lines"] == []


def test_group_tasks_count_fragment_not_title():
    """计数碎片行「1/51]」不得误判为标题行（五环边界教训）。"""
    items = [_item("1/51]"), _item("去找大唐境内(223, 21)武痴[1/5]", y=280)]
    tasks = task_tracker.group_tasks(items)
    assert len(tasks) == 0 or tasks[0]["title"] != "1/51]"


def test_task_signature_stable_and_ordered():
    task = {"title": "五环[五环]",
            "lines": [("去找大唐境内(358, 21)惯骗[1/5]", None, None)],
            "text": ""}
    sig = task_tracker.task_signature(task)
    assert sig == task_tracker.norm_text("五环[五环]|去找大唐境内(358, 21)惯骗[1/5]")
    assert task_tracker.task_signature(task) == sig  # 稳定


# ---- 绿字判定 ----

def test_is_green_text():
    img = np.zeros((60, 200, 3), dtype=np.uint8)
    img[10:40, 20:180] = (0, 186, 0)  # 绿字块（G 通道显著）
    rect = [20, 10, 180, 40]
    assert task_tracker.is_green_text(img, rect)
    img[10:40, 20:180] = (186, 30, 30)  # 红块
    assert not task_tracker.is_green_text(img, rect)


# ---- 面板差分门（共享组件；语义契约，六处调用点共用） ----

FAKE = [("任务标题", [10, 220, 120, 250], 0.99)]


def _make_frame(seed):
    arr = np.zeros((831, 1378, 3), dtype=np.uint8)
    arr[200:400, 0:200] = seed
    return Image.fromarray(arr)


def test_gate_ocr_and_reuse(monkeypatch):
    calls = {"n": 0}

    def fake(img, region=None):
        calls["n"] += 1
        return list(FAKE), np.zeros_like(np.asarray(img))

    monkeypatch.setattr(game_map, "ocr_region", fake)
    gate = task_tracker.TrackGate()
    items = gate.ocr(_make_frame(10))
    assert calls["n"] == 1 and items == FAKE
    items2 = gate.ocr(_make_frame(10))
    assert calls["n"] == 1 and items2 is items  # 复用同一对象（extras 帧缓存键 id(items) 契约）


def test_gate_reopens_on_change_after_interval(monkeypatch):
    calls = {"n": 0}
    fake_now = [time.time()]

    def fake(img, region=None):
        calls["n"] += 1
        return list(FAKE), np.zeros_like(np.asarray(img))

    monkeypatch.setattr(game_map, "ocr_region", fake)
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    gate = task_tracker.TrackGate()
    gate.ocr(_make_frame(10))
    gate.ocr(_make_frame(10))
    assert calls["n"] == 1
    fake_now[0] += task_tracker.TRACK_GATE_MIN_INTERVAL + 0.5  # 越过节奏上限
    gate.ocr(_make_frame(20))  # 门区域变化 + 距上次 OCR 超 2s → 重新 OCR
    assert calls["n"] == 2


def test_gate_rate_cap_reuses_despite_change(monkeypatch):
    """节奏上限：2s 内像素变化也复用（世界像素/面板动画不击穿门）。"""
    calls = {"n": 0}
    fake_now = [time.time()]

    def fake(img, region=None):
        calls["n"] += 1
        return list(FAKE), np.zeros_like(np.asarray(img))

    monkeypatch.setattr(game_map, "ocr_region", fake)
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    gate = task_tracker.TrackGate()
    gate.ocr(_make_frame(10))
    fake_now[0] += 0.5  # 门区域变化（10→20），但距上次 OCR 仅 0.5s
    items2 = gate.ocr(_make_frame(20))
    assert calls["n"] == 1 and items2 == FAKE  # 节奏上限复用
    fake_now[0] += task_tracker.TRACK_GATE_MIN_INTERVAL + 0.5
    gate.ocr(_make_frame(20))  # 越过节奏上限且像素仍与上次 OCR 不同 → 新 OCR
    assert calls["n"] == 2


def test_gate_5s_fallback(monkeypatch):
    calls = {"n": 0}
    fake_now = [time.time() + 6.0]

    def fake(img, region=None):
        calls["n"] += 1
        return list(FAKE), np.zeros_like(np.asarray(img))

    monkeypatch.setattr(game_map, "ocr_region", fake)
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    try:
        gate = task_tracker.TrackGate()
        gate.ocr(_make_frame(10))   # 假时钟 t=+6：首调 OCR
        fake_now[0] += 6.0           # 假时钟前进 6s（越过节奏上限）
        gate.ocr(_make_frame(10))    # 门相同但距上次 OCR 6s > 5s → 强制 OCR
        assert calls["n"] == 2
    finally:
        monkeypatch.setattr(time, "time", time.time)


def test_gate_paste_track_fresh_pixels():
    gate = task_tracker.TrackGate()
    f = _make_frame(30)
    img_np = np.zeros_like(np.asarray(f))
    gate.paste_track(f, img_np)
    l, t, r, b = task_tracker.TRACK_REGION
    assert (img_np[t:b, l:r] == np.asarray(f)[t:b, l:r]).all()
    assert (img_np[b:, :] == 0).all() and (img_np[:t, :] == 0).all()
