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


# ---- 绿字点击定位（green_text_center） ----

def test_green_text_center_tail_bottom_not_leaked_row():
    """框底漏入邻行绿字时点击不得被推落：行尾块列范围内取底部。

    客栈擂台 2026-10-28 实测失败帧：链接行框底 274、行尾「天」字底
    267，下方邻行「竺武士」顶笔画（y271-274, x26-35）漏入偏高的
    OCR 框，旧公式取整框最底绿色行得 (211,272)，落在「天」下方
    5px 空处点击失败（任务空转 ~2 分钟）。修正后取行尾块（天 +
    其 15px 下划线）列范围内底部上方 2px 得 (211,268)——与各成功
    点击（下划线路径 270-2）同高。
    """
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 204:219] = (0, 255, 0)    # 行尾「天」字（亮绿）
    img[270, 204:219] = (0, 255, 0)        # 天 字下划线（15px，短于 20px 检测阈值）
    img[271:275, 26:36] = (0, 255, 0)      # 邻行顶笔画漏入框内
    rect = [25, 250, 220, 275]
    cx, cy = task_tracker.green_text_center(img, rect)
    assert (cx, cy) == (211, 268)


def test_green_text_center_no_leak_stays_on_glyph():
    """无邻行漏入时（框底贴字底）点击落在行尾字本体上。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 204:219] = (0, 255, 0)    # 行尾「天」字（亮绿）
    rect = [25, 250, 220, 268]
    cx, cy = task_tracker.green_text_center(img, rect)
    assert (cx, cy) == (211, 265)


def test_green_text_center_underline_beyond_fragment_rect():
    """坐标碎片框不含行尾下划线时，右探 +60 仍点在下划线上。

    2026-10-31 五环实测：链接行被 OCR 拆碎，「(13，47)」碎片框
    x150-184，下划线在「情缘」文字下 x189-219、框右 5px 外——旧窗口
    （+4px）检不到即退化点碎片文字中点，点不中链接。
    """
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 155:180:4] = (0, 255, 0)  # 「(13，47)」文字（散碎笔画）
    img[270, 189:220] = (0, 255, 0)        # 行尾下划线（31px 实线）
    rect = [150, 249, 184, 276]
    cx, cy = task_tracker.green_text_center(img, rect)
    assert (cx, cy) == (204, 268)
    assert task_tracker.has_underline(img, rect)


def test_track_task_require_coord_refuses_subgoal_and_time_rows(monkeypatch):
    """require_coord：坐标行未检出时拒绝行级兜底点击（替身行/剩余时间
    行同为绿字也不点），直接失败等外层复读（2026-10-31 点错教训）。

    像素形态按 2026-10-31 实测截图：替身行亮绿+下划线、剩余时间行因
    OCR 框重叠沾到替身下划线绿色（3.2%）。
    """
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 25:145] = (0, 255, 0)     # 替身行文字（亮绿）
    img[287, 24:115] = (0, 255, 0)         # 替身行下划线
    items = [("五环[五环]", [14, 233, 100, 253], 0.99),
             ("24载的替身[1/5]", [19, 262, 139, 293], 0.90),
             ("剩余时间：00:58:03", [22, 286, 164, 306], 0.90)]
    clicks = []

    class FakeWin:
        def capture(self, force_activate=False):
            from PIL import Image
            return Image.fromarray(img)

    monkeypatch.setattr(task_tracker, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(task_tracker.game_map, "ocr_regions",
                        lambda im, regions: (items, img))
    ok, info = task_tracker.track_task(FakeWin(), "五环", retries=0,
                                       fallback_keywords=["替身", "武痴"],
                                       pre_items=(items, img),
                                       require_coord=True)
    assert not ok and clicks == []  # 未点任何行
    # 对比：不要求坐标时旧路径会点子目标行（回归对照，锁定行为差异）
    clicks.clear()
    ok, info = task_tracker.track_task(FakeWin(), "五环", retries=0,
                                       fallback_keywords=["替身", "武痴"],
                                       pre_items=(items, img))
    assert ok and clicks  # 旧路径仍可兜底点击（其他任务不受影响）


def test_track_task_require_coord_clicks_coord_line_underline(monkeypatch):
    """require_coord：坐标行检出时正常点击其下划线。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    # 文字用散碎笔画像（实线整块会被下划线检测误抓——真实文字笔画
    # 连排 ≤13px，下划线检测按 ≥20px 实线区分）
    img[255:268, 25:145:8] = (0, 255, 0)   # 替身行文字（亮绿笔画）
    img[287, 24:115] = (0, 255, 0)         # 替身行下划线
    img[255:268, 60:215:8] = (0, 255, 0)   # 链接行文字（亮绿笔画）
    img[270, 189:220] = (0, 255, 0)        # 链接行下划线
    items = [("五环[五环]", [14, 233, 100, 253], 0.99),
             ("去找大雁塔二层(13，47)情缘", [20, 249, 224, 276], 0.96),
             ("24载的替身[1/5]", [19, 262, 139, 293], 0.90)]
    clicks = []

    class FakeWin:
        def capture(self, force_activate=False):
            from PIL import Image
            return Image.fromarray(img)

    monkeypatch.setattr(task_tracker, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    ok, info = task_tracker.track_task(FakeWin(), "五环", retries=0,
                                       fallback_keywords=["替身", "武痴"],
                                       pre_items=(items, img),
                                       require_coord=True)
    assert ok and clicks == [(204, 268)]  # 链接下划线上方 2px


def test_keyword_fallback_require_coord_filters_subgoal_line():
    """关键词兜底 + require_coord：子目标行（含关键词但无坐标）不候选。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 25:145:4] = (0, 255, 0)   # 替身行文字（亮绿，框内）
    img[305:318, 60:215:4] = (0, 255, 0)   # 武痴链接行文字（亮绿，框内）
    items = [("24载的替身[1/5]", [19, 262, 139, 293], 0.90),
             ("去找大唐境内(223,21)武痴[1/5]", [20, 300, 260, 327], 0.95)]
    fb = task_tracker._keyword_fallback(items, img, ["替身", "武痴"], None,
                                        require_coord=True)
    assert fb is not None and "武痴" in fb[0]  # 只中链接行
    fb = task_tracker._keyword_fallback(items, img, ["替身", "武痴"], None)
    assert fb is not None and "替身" in fb[0]  # 不要求坐标时仍按首行命中


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


def test_gate_uncapped_claim_mode(monkeypatch):
    """领取校验用门（min_interval=0）：像素变化立即重开，检测不延迟。"""
    calls = {"n": 0}
    fake_now = [time.time()]

    def fake(img, region=None):
        calls["n"] += 1
        return list(FAKE), np.zeros_like(np.asarray(img))

    monkeypatch.setattr(game_map, "ocr_region", fake)
    monkeypatch.setattr(time, "time", lambda: fake_now[0])
    gate = task_tracker.TrackGate(min_interval=0.0)
    gate.ocr(_make_frame(10))
    fake_now[0] += 0.5
    gate.ocr(_make_frame(20))  # 不限频：像素变化 → 立即新 OCR
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
