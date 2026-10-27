"""pytest 共享夹具。

关键前置：ascript 的 R 类按 sys.argv[0] 锚定工程根目录——测试进程的
argv[0] 是 pytest，必须显式指定根目录（须在任何 src.* 导入之前）。
"""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ascript.windows.system import R as _R  # noqa: E402

_R._BASE_DIR = ROOT


@pytest.fixture
def rng():
    return np.random.default_rng(20261027)


def make_bgr_frame(w=1378, h=831, seed=None):
    """合成 BGR 帧（尺寸同游戏窗口；噪声背景供模板匹配）。"""
    r = np.random.default_rng(seed if seed is not None else 12345)
    return r.integers(0, 256, (h, w, 3), dtype=np.uint8)


def paste_template(frame, template_bgr, x, y):
    """把 BGR 模板贴入帧的 (x, y) 左上角，返回新帧。"""
    out = frame.copy()
    th, tw = template_bgr.shape[:2]
    out[y:y + th, x:x + tw] = template_bgr
    return out
