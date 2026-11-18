# -*- coding: utf-8 -*-
"""模板图片格式约束：res/img 下的模板必须是 RGB/RGBA。

背景（2026-11-18 打包版实机崩溃）：SDK 1.3.29+ 的
`AsCv.find_all_template` 对模板走 `to_numpy(im_search, preserve_alpha=True)`
→ `cv2.imdecode(..., IMREAD_UNCHANGED)`。灰度模式（mode L）PNG 会被解码成
2 维单通道数组，随后 `cv2.cvtColor(im_search, COLOR_BGR2GRAY)` 抛
`cv2.error: (-15:Bad number of channels) ... 'scn' is 1`，任务线程直接崩。
旧 SDK（如 1.3.4）用 IMREAD_COLOR 强制转 3 通道，所以同一张图在旧 SDK
源码环境正常、升级 SDK 的打包版才炸——靠实机才能发现，必须有静态测试兜住。

这里直接按 SDK 的解码方式（IMREAD_UNCHANGED）断言通道数，而不是只查
PIL mode：将来出现灰度+alpha、16 位灰度等变体也能拦住。
"""
from pathlib import Path

import cv2
import numpy as np

RES_IMG = Path(__file__).resolve().parent.parent / "res" / "img"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


def test_res_img_templates_decode_to_3_or_4_channels():
    bad = []
    for path in sorted(RES_IMG.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        data = np.fromfile(str(path), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            bad.append(f"{path.relative_to(RES_IMG)}: 解码失败")
        elif img.ndim != 3 or img.shape[2] not in (3, 4):
            bad.append(f"{path.relative_to(RES_IMG)}: shape={img.shape}")
    assert not bad, (
        "模板必须是 RGB/RGBA（灰度模板在 SDK 1.3.29+ 的 "
        "IMREAD_UNCHANGED 路径下导致 cvtColor 崩溃）: " + "; ".join(bad)
    )
