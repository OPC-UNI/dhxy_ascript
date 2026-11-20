"""OCR 引擎烟测（slow 标记：模型加载 ~1-2s，CI 默认排除）。

本地跑：python -m pytest -m slow
验证仓库内模型可加载、区域/2x 引擎参数构造正确（曾踩过 det_* 参数
漏传 det_model_path 的 KeyError 坑）；全帧引擎不参与预热，单独验证
首次 ocr_image 的懒创建路径。
"""
import numpy as np
import pytest

from src.device import map as game_map


@pytest.mark.slow
def test_ocr_engines_construct_and_infer():
    blank = np.zeros((64, 64, 3), dtype=np.uint8)
    # 区域快引擎
    items, _ = game_map.ocr_region(blank, (0, 0, 64, 64))
    assert isinstance(items, list)
    # 2x 放大快引擎（max/1600）
    items2, _ = game_map.ocr_zoom(blank)
    assert isinstance(items2, list)


@pytest.mark.slow
def test_full_frame_engine_lazy_construct():
    """全帧引擎不随 warmup 创建：首次 ocr_image 时懒创建并可用。"""
    game_map._engine = None
    items, _ = game_map.ocr_image(np.zeros((64, 64, 3), dtype=np.uint8))
    assert isinstance(items, list)
    assert game_map._engine is not None


@pytest.mark.slow
def test_ocr_detects_synthetic_text():
    """合成白底黑字图能被区域引擎检出文字。"""
    import cv2
    img = np.full((120, 320, 3), 255, dtype=np.uint8)
    cv2.putText(img, "test123", (20, 70), cv2.FONT_HERSHEY_SIMPLEX,
                1.5, (0, 0, 0), 3)
    items, _ = game_map.ocr_region(img, (0, 0, 320, 120))
    joined = "".join(t for t, _, _ in items)
    assert "test123" in joined.replace(" ", "")
