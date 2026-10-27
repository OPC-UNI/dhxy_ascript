"""性能护栏（热路径回归测试）。

CI 共享机器计时噪声大：断言用「相对关系 + 极宽松的绝对上限」，只拦
数量级回归（如条带匹配退化到全帧、差分门比较退化成拷贝），不做毫秒级
精确断言（精确基准在实机用 .diag/measure_costs.py 测，见 docs/performance.md）。
"""
import time

import cv2
import numpy as np
from ascript.windows.system import R

from src import map as game_map
from src import xuanshu
from src.dialog_common import _dialog_text_present

from .conftest import make_bgr_frame, paste_template


def _tpl(icon):
    return cv2.imread(R.img(icon), cv2.IMREAD_COLOR)


def _timed(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def test_band_match_faster_than_full_frame():
    """条带匹配必须显著快于全帧匹配（实测 5-18 倍，此处只要求快）。"""
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    band = (0, 600, 1378, 831)  # 含模板的搜索条带
    t_band = min(_timed(lambda: game_map.match_frame_np(
        frame, "fashu.png", region=band, fallback=False)) for _ in range(5))
    t_full = min(_timed(lambda: game_map.match_frame_np(
        frame, "fashu.png", fallback=False)) for _ in range(5))
    assert t_band <= t_full * 1.5  # 条带应 ≤ 全帧（给 CI 噪声留 1.5x 余量）


def test_gate_crop_equality_budget():
    """差分门比较（200x200x3 全等判定）上限：实测 ~1-2ms，上限 100ms。"""
    crop = np.random.default_rng(1).integers(0, 256, (200, 200, 3), dtype=np.uint8)
    prev = crop.copy()
    t = min(_timed(lambda: (crop == prev).all()) for _ in range(10))
    assert t < 0.1


def test_dialog_option_map_scan_budget():
    """对话文案全表扫描上限：实测 0.7-1.5ms，上限 200ms（含 OCR 噪声行）。"""
    items = [(f"测试文案第{i}行", [400, 300 + i * 30, 700, 330 + i * 30], 0.9)
             for i in range(8)]
    t = min(_timed(lambda: [
        _dialog_text_present(items, dtxt) for dtxt in xuanshu.DIALOG_OPTION_MAP
    ]) for _ in range(10))
    assert t < 0.2


def test_frames_equal_budget():
    """整帧全等比较（1378x831）上限：实测 2-4ms，上限 100ms。"""
    a = np.random.default_rng(2).integers(0, 256, (831, 1378, 3), dtype=np.uint8)
    b = a.copy()
    t = min(_timed(lambda: (a == b).all()) for _ in range(10))
    assert t < 0.1


def test_template_cache_hit_budget():
    """模板缓存命中路径（磁盘解码一次后）单次匹配上限。"""
    frame = make_bgr_frame()
    game_map._load_template("fashu.png")  # 预热缓存
    t = min(_timed(lambda: game_map.match_frame_np(
        frame, "fashu.png", region=(0, 0, 400, 400), fallback=False))
        for _ in range(5))
    assert t < 0.2  # 条带匹配 + 灰度转换（实测 ~10ms，上限留 20x 余量）
