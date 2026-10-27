"""map.py 纯逻辑与模板匹配快速接口测试（合成帧 + 仓库内真实模板）。"""
import cv2
import numpy as np
from ascript.windows.system import R

from src import map as game_map

from .conftest import make_bgr_frame, paste_template


def _tpl(icon):
    return cv2.imread(R.img(icon), cv2.IMREAD_COLOR)


# ---- 文案解析 ----

def test_clean_map_pos_normal():
    assert game_map._clean_map_pos("灵兽村[120,83]") == "灵兽村[120,83]"


def test_clean_map_pos_fullwidth_and_noise():
    out = game_map._clean_map_pos("㝱灵兽村［120，83］")
    assert out == "灵兽村[120,83]"


def test_clean_map_pos_garbage():
    assert game_map._clean_map_pos("！！，、") in ("", "，,")


# ---- 引擎输出转换 ----

class _FakeRes:
    def __init__(self, boxes, txts, scores):
        self.boxes = boxes
        self.txts = txts
        self.scores = scores


def test_engine_items_format():
    res = _FakeRes(boxes=np.array([[[10, 20], [50, 20], [50, 40], [10, 40]]]),
                   txts=["测试"], scores=[0.99])
    items = game_map._engine_items(res)
    assert items == [("测试", [10, 20, 50, 40], 0.99)]


# ---- 模板快速接口（真实模板） ----

def test_match_frame_np_finds_center():
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    h, w = tpl.shape[:2]
    got = game_map.match_frame_np(frame, "fashu.png")
    assert got == (300 + w // 2, 600 + h // 2)  # 返回匹配中心（中心约定）


def test_match_frame_np_not_found():
    frame = make_bgr_frame()
    assert game_map.match_frame_np(frame, "fashu.png") is None


def test_match_frame_np_region_restricts():
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    # 条带不含模板 → 未命中且不兜底（fallback=False）
    got = game_map.match_frame_np(frame, "fashu.png",
                                  region=(0, 0, 200, 200), fallback=False)
    assert got is None
    # 同条带 + 兜底 → 全帧命中
    got = game_map.match_frame_np(frame, "fashu.png", region=(0, 0, 200, 200))
    assert got is not None


def test_match_frame_multi():
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    found = game_map.match_frame_multi(frame, ("fashu.png", "fangyu.png"))
    assert found["fashu.png"] is not None
    assert found["fangyu.png"] is None


def test_match_frame_all_multi_instance():
    tpl = _tpl("auto.png")
    frame = paste_template(make_bgr_frame(), tpl, 200, 500)
    frame = paste_template(frame, tpl, 900, 650)
    pts = game_map.match_frame_all(frame, "auto.png")
    assert len(pts) == 2


def test_best_match_np_double_template():
    """双模板比分数：目标模板分数更高时返回其坐标（阈值语义见 battle）。"""
    tpl = _tpl("battle_first.png")
    frame = paste_template(make_bgr_frame(), tpl, 50, 100)
    score, (cx, cy) = game_map.best_match_np(frame, "battle_first.png")
    assert score >= 0.9
    assert (cx, cy) == (50 + tpl.shape[1] // 2, 100 + tpl.shape[0] // 2)


# ---- 模板缓存 ----

def test_load_template_cache_identity():
    a = game_map._load_template("fashu.png")
    b = game_map._load_template("fashu.png")
    assert a is not None and a is b  # 第二次命中缓存（同一对象）
