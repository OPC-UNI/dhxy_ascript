# -*- coding: utf-8 -*-
"""map_nav（地图坐标导航）单元测试。

覆盖：层级图名称解析/下拉全表、HUD 地名匹配、搜索条锚点几何、
悬停浮层坐标解析、像素↔坐标线性变换求解（含单轴散布不足的方形格
补全与异常样本拒绝）、到达判定。

实机机制（2026-11-20）：地图窗口悬停读坐标 → 线性标定 → 悬停校验
后点击地图点寻路；比例/方向/残差门槛见 map_nav 模块常量注释。
"""
import json
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.device import map_nav


class _Win:
    """测试用窗口桩（affine_for 只读 rect.width/height）。"""

    def __init__(self, w, h):
        self.rect = types.SimpleNamespace(width=w, height=h)


@pytest.fixture(autouse=True)
def _isolate_affine_caches(monkeypatch, tmp_path):
    """标定缓存隔离：持久化文件指向 tmp 目录，不读写真实角色数据。"""
    monkeypatch.setattr(
        map_nav, "_persisted_affines_path",
        lambda: tmp_path / "map_nav_affines.json")
    map_nav.reset_affine_cache()
    map_nav.reset_layer_tile_cache()
    yield
    map_nav.reset_affine_cache()
    map_nav.reset_layer_tile_cache()


# ---------------- 层级图与下拉 ----------------

def test_parse_layer_target():
    assert map_nav.parse_navigate_target("龙窟四层") == ("龙窟", "龙窟四层")
    assert map_nav.parse_navigate_target("凤巢七层") == ("凤巢", "凤巢七层")


def test_parse_normal_target():
    assert map_nav.parse_navigate_target("洛阳城") == ("洛阳城", None)
    assert map_nav.parse_navigate_target("大雁塔四层") == ("大雁塔", "大雁塔四层")
    # 未收录的层名按普通图处理，由 resolve 阶段报错
    assert map_nav.parse_navigate_target("龙窟八层") == ("龙窟八层", None)
    assert map_nav.parse_navigate_target("") == ("", None)


def test_layer_map_names():
    names = map_nav.layer_map_names()
    assert len(names) == 20           # 龙窟/凤巢各 7 + 大雁塔 6
    assert names[0] == "龙窟一层" and names[6] == "龙窟七层"
    assert "凤巢一层" in names and "凤巢七层" in names
    assert "大雁塔一层" in names and "大雁塔六层" in names


def test_all_navigate_maps_excludes_unsupported_includes_layers():
    maps = map_nav.all_navigate_maps()
    # 海底迷宫/地狱迷宫也是层级图但用户决定先不处理（TODO）
    for name in ("海底迷宫", "地狱迷宫"):
        assert name not in maps
        assert f"{name}一层" not in maps
    assert "大雁塔四层" in maps        # 大雁塔走寻路面板特殊入口
    assert "洛阳城" in maps and "长安城" in maps
    assert "龙窟四层" in maps and "凤巢七层" in maps
    # 层级图基名不能作为导航目标（点标签只会进层块页，2026-11-20 审查）
    for name in ("龙窟", "凤巢", "大雁塔"):
        assert name not in maps
    assert len(maps) == len(set(maps))   # 无重复
    # 70 项常规/层名内置标定 + 特殊地图（世界地图无标签，2026-11-23）
    assert len(maps) == 70 + len(map_nav.SPECIAL_MAP_ENTRIES)
    for name in map_nav.SPECIAL_MAP_ENTRIES:
        assert name in maps


# ---------------- HUD 地名匹配与到达 ----------------

def test_names_match_alias_and_exact():
    assert map_nav.names_match("长安", "长安城")      # 别名归一
    assert map_nav.names_match("长安城东", "长安东")  # HUD 地名 ≠ 世界标签
    assert not map_nav.names_match("长安城东", "长安城")
    assert map_nav.names_match("洛阳城", "洛阳城")
    assert not map_nav.names_match("灵兽村", "洛阳城")
    assert not map_nav.names_match(None, "洛阳城")
    assert not map_nav.names_match("洛阳城", "")


def test_arrived_tolerance_and_layer_exact():
    assert map_nav._arrived(("洛阳城", 195, 105), "洛阳城", None, 191, 110)
    assert not map_nav._arrived(("洛阳城", 210, 105), "洛阳城", None, 191, 110)
    # 层级图用层名精确匹配（resolve 会把层名模糊归一成基础图）
    assert map_nav._arrived(("龙窟四层", 10, 20), "龙窟", "龙窟四层", 10, 20)
    assert not map_nav._arrived(("龙窟一层", 10, 20), "龙窟", "龙窟四层", 10, 20)
    assert not map_nav._arrived(None, "洛阳城", None, 191, 110)


# ---------------- 搜索条锚点几何 ----------------

def test_bar_points_offsets_match_calibration():
    """采样基准点按 2026-11-20 双窗口实测（高级搜索左缘为锚点）。"""
    assert map_nav._bar_points(227, 465) == {"y": 227, "x": 377}
    assert map_nav._bar_points(277, 667) == {"y": 277, "x": 579}


def _patch_no_template(monkeypatch):
    """让模板路径不命中（OCR 兜底分支测试用）。"""
    monkeypatch.setattr(map_nav.game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(map_nav.game_map, "best_match_np",
                        lambda *a, **k: (0.0, (0, 0)))


def test_locate_search_bar_from_ocr(monkeypatch):
    """两段 OCR 锚点 → 基准点（X/Y 占位符并入前段不影响）。"""
    items = [("查询NPC名字X", (257, 218, 397, 238), 0.99),
             ("高级搜索", (465, 217, 532, 238), 1.0),
             ("长安", (60, 70, 90, 90), 0.99)]

    monkeypatch.setattr(map_nav.game_map, "ocr_regions",
                        lambda img, regions: (items, None))
    _patch_no_template(monkeypatch)
    bar = map_nav.locate_search_bar(None, img=object())
    assert bar is not None
    assert bar["x"] == 377
    assert bar["y"] in (226, 227)


def test_locate_search_bar_missing_anchor(monkeypatch):
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda img, regions: ([("高级搜索", (465, 217, 532, 238), 1.0)], None))
    _patch_no_template(monkeypatch)
    assert map_nav.locate_search_bar(None, img=object()) is None


def test_locate_search_bar_by_template():
    """模板命中优先：贴到合成帧上应精确还原采样基准点（偏移 -121,0）。"""
    from PIL import Image
    import numpy as np
    root = Path(__file__).resolve().parent.parent
    tpl = Image.open(root / "res" / "img" /
                     map_nav.SEARCH_BAR_ADV_ICON).convert("RGB")
    rng = np.random.default_rng(7)
    bg = rng.integers(0, 60, size=(831, 1378, 3), dtype=np.uint8)
    frame = Image.fromarray(bg, "RGB")
    frame.paste(tpl, (500, 210))
    bar = map_nav.locate_search_bar(None, img=frame)
    assert bar == {"x": 500 + tpl.width // 2 - 121,
                   "y": 210 + tpl.height // 2}


def test_locate_search_bar_no_ocr_fast_path(monkeypatch):
    """allow_ocr=False（轮询热路径）：模板未命中直接 None，不跑 OCR。"""
    monkeypatch.setattr(map_nav.game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(map_nav.game_map, "best_match_np",
                        lambda *a, **k: (0.1, (0, 0)))

    def _boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("allow_ocr=False 不应调用 OCR")

    monkeypatch.setattr(map_nav.game_map, "ocr_regions", _boom)
    assert map_nav.locate_search_bar(None, img=object(),
                                     allow_ocr=False) is None


# ---------------- 悬停浮层坐标解析 ----------------

def test_parse_tooltip_text():
    assert map_nav.parse_tooltip_text("271,146") == (271, 146)
    assert map_nav.parse_tooltip_text("41, 46") == (41, 46)
    assert map_nav.parse_tooltip_text("x 157，112") == (157, 112)  # 全角逗号
    assert map_nav.parse_tooltip_text("怪物等级：70") is None      # 无逗号数字对
    assert map_nav.parse_tooltip_text("") is None
    assert map_nav.parse_tooltip_text(None) is None


# ---------------- 线性变换求解 ----------------

def _samples(ax, ay, sx, sy, points):
    """按模型生成样本：points=[(px,py), ...] → [(px,py,gx,gy), ...]。"""
    out = []
    for px, py in points:
        gx = round((px - ax) / sx)
        gy = round((py - ay) / sy)
        out.append((px, py, gx, gy))
    return out


def test_solve_affine_full_axes():
    samples = _samples(300, 600, 1.5, -1.5, [(450, 450), (600, 300)])
    affine = map_nav.solve_affine(samples)
    assert affine is not None
    assert affine["sx"] == pytest.approx(1.5, abs=0.02)
    assert affine["sy"] == pytest.approx(-1.5, abs=0.02)
    assert affine["ax"] == pytest.approx(300, abs=3)
    assert affine["ay"] == pytest.approx(600, abs=3)


def test_solve_affine_single_axis_fallback():
    """两样本 y 相同 → sy 由 sx 按方形格补全（层内小图 4.2 px/格）。"""
    # 模型 ay 需与补全结果一致：sy=-sx=-4.0，ay = py1 - sy*gy1
    gy1 = 100
    ay = 450 + 4.0 * gy1
    samples = [(450, 450, 100, gy1), (650, 450, 150, gy1)]
    affine = map_nav.solve_affine(samples)
    assert affine is not None
    assert affine["sx"] == pytest.approx(4.0, abs=0.02)
    assert affine["sy"] == pytest.approx(-4.0, abs=0.02)
    assert affine["ay"] == pytest.approx(ay, abs=0.1)


def test_solve_affine_rejects_bad_scale_ratio():
    """两轴比例不一致（非方形格/异常窗口）→ 拒绝。"""
    samples = [(450, 450, 100, 100), (600, 150, 200, 200)]  # sx=1.5, sy=-3
    assert map_nav.solve_affine(samples) is None


def test_solve_affine_rejects_wrong_direction():
    samples = [(450, 450, 100, 100), (300, 600, 200, 200)]  # sx<0
    assert map_nav.solve_affine(samples) is None


def test_solve_affine_rejects_small_spread():
    samples = [(450, 450, 100, 100), (455, 455, 103, 103)]
    assert map_nav.solve_affine(samples) is None


def test_solve_affine_ignores_outlier_sample():
    """单个离群样本不再让整张图失败（窗口边缘坏采样，2026-11-20 教训）。"""
    samples = _samples(300, 600, 1.5, -1.5, [(450, 450), (600, 300)])
    samples.append((700, 500, 210, 60))  # 明显偏离模型
    aff = map_nav.solve_affine(samples)
    assert aff is not None
    assert aff["sx"] == pytest.approx(1.5, abs=0.02)
    assert aff["sy"] == pytest.approx(-1.5, abs=0.02)
    assert aff["ax"] == pytest.approx(300, abs=3)


def test_solve_affine_real_lingshou_edge_sample():
    """灵兽村真机样本：右缘采样点读到坏 y（(250,1)），内点仍可解。"""
    samples = [(465, 327, 65, 112), (645, 327, 126, 112),
               (825, 327, 188, 112), (1005, 327, 250, 1)]
    aff = map_nav.solve_affine(samples)
    assert aff is not None
    assert abs(aff["sx"] - 2.93) < 0.06
    assert abs(aff["sy"] + 2.93) < 0.06
    assert abs(aff["ax"] - 274) < 6


def test_solve_affine_rejects_insufficient_inliers():
    """互相矛盾的两组样本（内点 < n-1）→ 拒绝，防拼出错误变换。"""
    samples = [(450, 450, 100, 100), (600, 300, 200, 200),
               (450, 300, 300, 300), (600, 450, 400, 400)]
    assert map_nav.solve_affine(samples) is None


def test_solve_affine_requires_two_samples():
    assert map_nav.solve_affine([]) is None
    assert map_nav.solve_affine([(450, 450, 100, 100)]) is None


# ---------------- 层块页定位 ----------------

# 2026-11-20 凤巢层块页真机 OCR 原始输出（「凤巢一层」被拆成「凤巢」+「层」，
# 「凤巢二层」整段被读成「凤巢」——直接子串匹配失败的现场）
FENGCHAO_OCR = [
    ("凤巢", (647, 235, 692, 256), 0.973),
    ("凤巢三层", (841, 235, 919, 255), 0.996),
    ("凤巢", (457, 236, 504, 254), 0.976),
    ("层", (510, 237, 530, 254), 1.000),
    ("方寸后", (309, 358, 372, 385), 1.000),
    ("凤巢四层", (456, 372, 532, 395), 0.996),
    ("凤巢五层", (648, 373, 723, 394), 0.974),
    ("凤巢六层", (842, 374, 919, 395), 0.926),
    ("方", (331, 383, 380, 412), 0.998),
    ("长", (345, 413, 372, 454), 0.967),
    ("长寿", (320, 465, 374, 490), 1.000),
    ("凤巢七层", (455, 512, 531, 533), 0.982),
    ("普陀山", (305, 561, 366, 585), 0.999),
]


def _patch_layer_ocr(monkeypatch, items):
    monkeypatch.setattr(map_nav.game_map, "ocr_regions",
                        lambda img, regions: (items, None))


def test_merge_ocr_lines_joins_fragments():
    lines = map_nav._merge_ocr_lines(FENGCHAO_OCR)
    texts = [t for t, _cx, _cy, _rect in lines]
    assert "凤巢层" in texts          # 「凤巢」+「层」合并
    assert "凤巢三层" in texts
    assert "方寸后" in texts


def test_locate_layer_tile_direct_match(monkeypatch):
    _patch_layer_ocr(monkeypatch, FENGCHAO_OCR)
    pt = map_nav.locate_layer_tile(object(), "凤巢", "凤巢四层")
    assert pt is not None
    assert abs(pt[0] - 494) <= 2 and abs(pt[1] - 383) <= 2


def test_locate_layer_tile_grid_fallback_for_missing_char(monkeypatch):
    """「凤巢一层」字样被 OCR 吞字 → 用其它层标签拟合 3 列网格推断位置。"""
    _patch_layer_ocr(monkeypatch, FENGCHAO_OCR)
    pt = map_nav.locate_layer_tile(object(), "凤巢", "凤巢一层")
    assert pt is not None
    assert abs(pt[0] - 493) <= 4 and abs(pt[1] - 245) <= 3


def test_locate_layer_tile_single_anchor_default_step(monkeypatch):
    """只有一个可识别层标签时用默认列距/行距外推。"""
    _patch_layer_ocr(monkeypatch, [
        ("凤巢三层", (841, 235, 919, 255), 0.996),
    ])
    pt = map_nav.locate_layer_tile(object(), "凤巢", "凤巢一层")
    assert pt is not None
    assert abs(pt[0] - (880 - 2 * map_nav.LAYER_GRID_DX_DEFAULT)) <= 4
    assert abs(pt[1] - 245) <= 4


def test_locate_layer_tile_not_layer_page(monkeypatch):
    _patch_layer_ocr(monkeypatch, [("长安", (60, 70, 90, 90), 0.99)])
    assert map_nav.locate_layer_tile(object(), "凤巢", "凤巢一层") is None


# 2026-11-20 龙窟层块页真机 OCR：同排标签 y 中心 245/246 抖动 1px——
# 旧合并按 (cy,x) 排序会把「三层」排到「二层」前并负间距合砖（点砖缝）
LONGKU_OCR = [
    ("龙窟二层", (647, 234, 726, 258), 1.0),
    ("龙窟一层", (455, 235, 531, 256), 1.0),
    ("龙窟三层", (841, 235, 918, 256), 1.0),
    ("方寸后", (309, 358, 372, 385), 1.0),
    ("龙窟五层", (648, 374, 724, 395), 1.0),
    ("龙窟六层", (841, 374, 918, 395), 1.0),
    ("龙窟四层", (456, 375, 531, 395), 1.0),
    ("方", (333, 384, 377, 410), 1.0),
    ("长", (347, 416, 371, 453), 0.97),
    ("长寿", (321, 465, 374, 490), 1.0),
    ("龙窟七层", (454, 514, 531, 535), 1.0),
    ("普陀山", (305, 561, 366, 585), 1.0),
]


def test_locate_layer_tile_longku_columns(monkeypatch):
    """龙窟 7 层标题定位：同排 y 抖动时不得跨砖合并。"""
    _patch_layer_ocr(monkeypatch, LONGKU_OCR)
    expected = {
        "龙窟一层": (493, 245), "龙窟二层": (686, 246),
        "龙窟三层": (879, 245), "龙窟四层": (493, 385),
        "龙窟五层": (686, 384), "龙窟六层": (879, 384),
        "龙窟七层": (492, 524),
    }
    for layer, (cx, cy) in expected.items():
        pt = map_nav.locate_layer_tile(object(), "龙窟", layer)
        assert pt is not None, layer
        assert abs(pt[0] - cx) <= 2 and abs(pt[1] - cy) <= 3, (layer, pt)


# ---------------- 层块页坐标缓存（2026-11-25） ----------------

def _layer_page_win(frame):
    class _W:
        rect = types.SimpleNamespace(width=1378, height=831)

        def capture(self, force_activate=False):
            return frame

    return _W()


def _layer_page_frame(seed):
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 255, (831, 1378, 3), dtype=np.uint8))


def test_layer_tile_cache_learns_then_matches_without_ocr(monkeypatch):
    """首次 OCR 定位后缓存标题小块；下次模板匹配 + 小区域文本确认，
    不再整页 OCR（省 ~0.5-0.9s/次）。"""
    frame = _layer_page_frame(7)
    win = _layer_page_win(frame)
    ocr = []
    monkeypatch.setattr(map_nav, "locate_layer_tile",
                        lambda img, base, layer: ocr.append(1) or (200, 150))
    monkeypatch.setattr(map_nav, "_rect_text",
                        lambda *a, **k: "凤巢七层")
    assert map_nav._wait_layer_tile(
        win, "凤巢", "凤巢七层", 1.0) == (200, 150)
    assert len(ocr) == 1  # 首趟走整页 OCR 并学习
    monkeypatch.setattr(map_nav, "locate_layer_tile",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("缓存命中不应整页 OCR")))
    assert map_nav._wait_layer_tile(
        win, "凤巢", "凤巢七层", 1.0) == (200, 150)


def test_layer_tile_cache_text_mismatch_falls_back_to_ocr(monkeypatch):
    """模板命中但小区域读回别的层名（防误配点错层块）→ 回退整页 OCR。"""
    frame = _layer_page_frame(7)
    win = _layer_page_win(frame)
    ocr = []
    texts = iter(["别的层名", "凤巢七层"])
    monkeypatch.setattr(map_nav, "_rect_text", lambda *a, **k: next(texts, ""))
    monkeypatch.setattr(map_nav, "locate_layer_tile",
                        lambda img, base, layer: ocr.append(1) or (200, 150))
    assert map_nav._wait_layer_tile(win, "凤巢", "凤巢七层", 1.0) == (200, 150)
    assert map_nav._wait_layer_tile(win, "凤巢", "凤巢七层", 1.0) == (200, 150)
    assert len(ocr) == 2  # 第二次文本确认失败 → OCR 兜底


def test_layer_tile_cache_miss_on_changed_page(monkeypatch):
    """层块页内容变化（动画/布局漂移）模板不命中 → 回退整页 OCR。"""
    win = _layer_page_win(_layer_page_frame(7))
    ocr = []
    monkeypatch.setattr(map_nav, "_rect_text", lambda *a, **k: "凤巢七层")
    monkeypatch.setattr(map_nav, "locate_layer_tile",
                        lambda img, base, layer: ocr.append(1) or (200, 150))
    assert map_nav._wait_layer_tile(win, "凤巢", "凤巢七层", 1.0) == (200, 150)
    other = _layer_page_win(_layer_page_frame(11))
    monkeypatch.setattr(map_nav, "locate_layer_tile",
                        lambda img, base, layer: ocr.append(1) or (300, 200))
    assert map_nav._wait_layer_tile(
        other, "凤巢", "凤巢七层", 1.0) == (300, 200)
    assert len(ocr) == 2


# ---------------- 内置标定表与会话缓存 ----------------

def test_affine_table_lookup_and_validation(monkeypatch, tmp_path):
    p = tmp_path / "map_nav_affines.json"
    p.write_text(json.dumps({
        "layout": [1378, 831],
        "maps": {
            "洛阳城": {"sx": 1.667, "sy": -1.667,
                       "ax": 249.0, "ay": 643.7},
            "坏图": {"sx": -1.0, "sy": 1.0, "ax": 0.0, "ay": 0.0},
        },
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(map_nav, "_affine_table_path", lambda: p)
    map_nav.reset_affine_cache()
    aff = map_nav.affine_for(_Win(1378, 831), "洛阳城")
    assert aff is not None and abs(aff["sx"] - 1.667) < 1e-9
    assert map_nav.affine_for(_Win(1378, 831), "坏图") is None
    # 布局（窗口尺寸）不匹配 → 不用表
    assert map_nav.affine_for(_Win(1280, 720), "洛阳城") is None
    map_nav.reset_affine_cache()


def test_affine_table_missing_file(monkeypatch, tmp_path):
    """标定表文件不存在：stat 失败分支必须返回 None（不抛异常）。"""
    monkeypatch.setattr(
        map_nav, "_affine_table_path",
        lambda: tmp_path / "no_such_affines.json")
    map_nav.reset_affine_cache()
    assert map_nav.affine_for(_Win(1378, 831), "洛阳城") is None
    map_nav.reset_affine_cache()


def test_session_affine_cache_roundtrip():
    map_nav.reset_affine_cache()
    win = _Win(1378, 831)
    assert map_nav.affine_for(win, "测试图") is None
    map_nav.remember_affine(win, "测试图",
                            {"sx": 2.0, "sy": -2.0, "ax": 10.0, "ay": 20.0})
    aff = map_nav.affine_for(win, "测试图")
    assert aff is not None and aff["ax"] == 10.0
    # 会话缓存按窗口尺寸隔离
    assert map_nav.affine_for(_Win(1280, 720), "测试图") is None
    map_nav.reset_affine_cache()


# ---------------- 标定修正的角色持久化（2026-11-22 用户需求） ----------------

def test_affine_persisted_across_restart(tmp_path):
    """修正/采样解写角色数据：进程重启（内存清空）后仍直接命中。"""
    win = _Win(1378, 831)
    aff = {"sx": 1.9459, "sy": -1.954, "ax": 339.0, "ay": 653.1}
    map_nav.remember_affine(win, "宝象国", aff)
    path = tmp_path / "map_nav_affines.json"
    assert path.exists()
    # 模拟进程重启：只清内存缓存，磁盘文件保留
    map_nav.reset_affine_cache()
    got = map_nav.affine_for(win, "宝象国")
    assert got is not None
    assert abs(got["ax"] - 339.0) < 0.01 and abs(got["ay"] - 653.1) < 0.01
    # 游戏窗口尺寸不同（布局不匹配）不得套用
    assert map_nav.affine_for(_Win(1280, 720), "宝象国") is None


def test_persisted_affine_overrides_stale_builtin(monkeypatch, tmp_path):
    """持久化修正值优先于内置表（窗口漂移后内置原点已过期）。"""
    builtin = tmp_path / "builtin.json"
    builtin.write_text(json.dumps({
        "layout": [1378, 831],
        "maps": {"洛阳城": {"sx": 1.667, "sy": -1.667,
                           "ax": 249.0, "ay": 643.7}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(map_nav, "_affine_table_path", lambda: builtin)
    map_nav.reset_affine_cache()
    assert map_nav.affine_for(_Win(1378, 831), "洛阳城")["ax"] == 249.0
    map_nav.remember_affine(_Win(1378, 831), "洛阳城",
                            {"sx": 1.667, "sy": -1.667,
                             "ax": 258.0, "ay": 641.0})
    map_nav.reset_affine_cache()  # 模拟重启
    got = map_nav.affine_for(_Win(1378, 831), "洛阳城")
    assert got is not None and abs(got["ax"] - 258.0) < 0.01


def test_persisted_invalid_entry_falls_back_to_builtin(monkeypatch, tmp_path):
    """持久化条目损坏/方向错误：忽略并回退内置表（不能带坏方向去点击）。"""
    builtin = tmp_path / "builtin.json"
    builtin.write_text(json.dumps({
        "layout": [1378, 831],
        "maps": {"洛阳城": {"sx": 1.667, "sy": -1.667,
                           "ax": 249.0, "ay": 643.7}},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(map_nav, "_affine_table_path", lambda: builtin)
    path = tmp_path / "map_nav_affines.json"
    path.write_text(json.dumps({
        "version": 1,
        "layouts": {"1378x831": {"洛阳城": {
            "sx": -1.0, "sy": 1.0, "ax": 0.0, "ay": 0.0}}},
    }, ensure_ascii=False), encoding="utf-8")
    map_nav.reset_affine_cache()
    got = map_nav.affine_for(_Win(1378, 831), "洛阳城")
    assert got is not None and abs(got["sx"] - 1.667) < 1e-9


# ---------------- 大雁塔寻路面板：目的地链接 ----------------

def _link_frame():
    """合成帧：黄色链接行 + 两行绿色步骤（RGB）。"""
    img = np.zeros((120, 200, 3), dtype=np.uint8)
    img[10:30, 10:150] = (255, 255, 0)     # 黄色链接
    img[50:70, 10:150] = (0, 255, 0)       # 绿色行
    img[90:110, 10:190] = (0, 255, 0)      # 绿色行（更靠下）
    return img


def _stub_rect_text(monkeypatch, fn):
    """桩掉黄带文本校验（单测不跑 OCR 引擎）。fn(rect) -> 文本。"""
    monkeypatch.setattr(map_nav, "_rect_text",
                        lambda img, rect, *a, **k: fn(tuple(rect)))


def test_find_destination_link_prefers_yellow_band(monkeypatch):
    """目的地文本在行内为黄色即链接，点击点为目的地子串中心。"""
    _stub_rect_text(monkeypatch, lambda r: "大雁塔四层")
    lines = [("到大雁塔四层", 80, 20, (10, 10, 150, 30)),
             ("到长安", 80, 60, (10, 50, 150, 70)),
             ("走到大雁塔四层(83,56)处通过传送点传送", 100, 100,
              (10, 90, 190, 110))]
    pt = map_nav.find_destination_link(_link_frame(), lines, "大雁塔四层",
                                       region=(0, 0, 200, 120))
    # 「到」字非黄：「大雁塔四层」子串 33..150
    assert abs(pt[0] - 91) <= 1 and pt[1] == 20


def test_yellow_text_bands():
    bands = map_nav._yellow_text_bands(_link_frame(), (0, 0, 200, 120))
    assert len(bands) == 1
    (cx, cy), rect = bands[0]
    assert abs(cx - 80) <= 1 and cy == 20
    assert rect == (10, 10, 150, 30)


def test_find_destination_link_fallback_lowest_when_no_yellow():
    """无黄色命中时退独立链接行文本（绿色链接），点目的地子串中心。"""
    lines = [("到大雁塔四层", 80, 60, (10, 50, 150, 70)),
             ("到大雁塔四层", 100, 100, (10, 90, 190, 110))]
    assert map_nav.find_destination_link(_link_frame(), lines, "大雁塔四层",
                                         region=(0, 40, 200, 120)) == (115, 100)


def test_find_destination_link_ignores_step_lines():
    lines = [("走到大雁塔四层(83,56)处通过传送点传送", 100, 100,
              (10, 90, 190, 110))]
    assert map_nav.find_destination_link(_link_frame(), lines, "大雁塔四层",
                                         region=(0, 40, 200, 120)) is None


def test_find_destination_link_ignores_route_header():
    """表头「到大雁塔六层共有1条路径：」不是链接（真机误点教训）。"""
    lines = [("到大雁塔六层共有1条路径：", 674, 20, (10, 10, 150, 30))]
    assert map_nav.find_destination_link(_link_frame(), lines, "大雁塔六层",
                                         region=(0, 40, 200, 120)) is None


def test_find_destination_link_wrapped_line_not_starting_with_dao(monkeypatch):
    """真机案例：链接行换行拼接成「送到大雁塔六层」——按黄色带点击。"""
    _stub_rect_text(monkeypatch, lambda r: "大雁塔六层")
    lines = [("送到大雁塔六层", 574, 20, (10, 10, 150, 30)),
             ("走到大雁塔六层(109,23)处点击引路小妖传送到大雁塔六层", 100, 100,
              (10, 90, 190, 110))]
    pt = map_nav.find_destination_link(_link_frame(), lines, "大雁塔六层",
                                       region=(0, 0, 200, 120))
    # 「送到」非黄：「大雁塔六层」子串 50..150
    assert abs(pt[0] - 100) <= 1 and pt[1] == 20


# 真机 OCR（2026-11-20 21:11，人在 大雁塔二层 打开寻路面板，窄区域）：
# 从字段显示层名后「大雁塔二层」和「到」被识别成一行——只认行首「到」
# 会漏掉锚点，报「寻路面板未出现」。
XUNLU_TOP_OCR_LAYER2 = [
    ("从", (514, 351, 534, 368), 0.999),
    ("大雁塔二层到", (534, 349, 679, 371), 1.000),
    ("搜索", (804, 350, 851, 373), 0.999),
]


def test_dao_rect_from_line_variants():
    # 单独一行
    assert map_nav._dao_rect_from_line("到", (660, 350, 684, 372)) == \
        (660, 350, 684, 372)
    # 「到」粘在行尾（从字段有层名，真机 [534,349,679,371]）
    assert map_nav._dao_rect_from_line("大雁塔二层到", (534, 349, 679, 371)) == \
        (655, 349, 679, 371)
    # 「从…到」整行粘连（宽区域真机输出）
    assert map_nav._dao_rect_from_line("从大雁塔二层到",
                                       (512, 348, 678, 373)) == \
        (654, 348, 678, 373)
    # 「到」粘在行中（到字段已有输入文字，真机 [535,350,789,371]）
    assert map_nav._dao_rect_from_line(
        "大雁塔二层到大雁塔一层", (535, 350, 789, 371)) == \
        (650, 350, 673, 371)
    # 「到」粘在行首
    assert map_nav._dao_rect_from_line("到长安", (10, 10, 60, 30)) == \
        (10, 10, 27, 30)
    # 表头/步骤行不是锚点
    assert map_nav._dao_rect_from_line(
        "从长安到大雁塔二层共有1条路径：", (519, 402, 750, 419)) is None
    assert map_nav._dao_rect_from_line(
        "走到大雁塔一层(128,82)处通过传送点传送到大雁塔二层",
        (500, 460, 870, 500)) is None


def test_find_xunlu_anchors_by_template():
    """面板「搜索」按钮模板命中后按固定偏移推锚点（不依赖 OCR 文字）。"""
    from PIL import Image
    root = Path(__file__).resolve().parent.parent
    tpl = Image.open(root / "res" / "img" /
                     map_nav.XUNLU_SEARCH_ICON).convert("RGB")
    rng = np.random.default_rng(11)
    bg = rng.integers(0, 60, size=(831, 1378, 3), dtype=np.uint8)
    frame = Image.fromarray(bg, "RGB")
    left, top = 795, 342
    frame.paste(tpl, (left, top))
    cx, cy = left + tpl.width // 2, top + tpl.height // 2
    dao, search, frm = map_nav._find_xunlu_anchors(frame)
    assert dao == (cx - 179, cy - 11, cx - 154, cy + 9)
    assert search == (cx - 24, cy - 11, cx + 22, cy + 12)
    assert frm == (cx - 315, cy - 10, cx - 296, cy + 7)
    assert search[0] <= cx <= search[2] and search[1] <= cy <= search[3]
    assert map_nav._xunlu_list_region(dao, search, frm)[0] == frm[0] - 5


def test_close_xunlu_panel_clicks_measured_x_center(monkeypatch):
    """关闭点落在实测 X 圆钮上（中心 (866,335)；旧公式 847 点在钮外）。"""
    from PIL import Image
    root = Path(__file__).resolve().parent.parent
    tpl = Image.open(root / "res" / "img" /
                     map_nav.XUNLU_SEARCH_ICON).convert("RGB")
    rng = np.random.default_rng(3)
    bg = rng.integers(0, 60, size=(831, 1378, 3), dtype=np.uint8)
    frame = Image.fromarray(bg, "RGB")
    frame.paste(tpl, (795, 342))  # 模板中心 (829,361)，与真机一致

    class _W:
        def capture(self, force_activate=False):
            return frame

    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    assert map_nav._close_xunlu_panel_if_open(_W()) is True
    x, y = clicks[0]
    assert abs(x - 866) <= 6 and abs(y - 335) <= 4


def test_find_xunlu_anchors_no_ocr_fast_path(monkeypatch):
    """allow_ocr=False（等面板热路径）：模板未命中直接 None，不跑 OCR。"""
    monkeypatch.setattr(map_nav.game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(map_nav.game_map, "best_match_np",
                        lambda *a, **k: (0.1, (0, 0)))

    def _boom(*a, **k):  # pragma: no cover - 被调用即失败
        raise AssertionError("allow_ocr=False 不应调用 OCR")

    monkeypatch.setattr(map_nav.game_map, "ocr_regions", _boom)
    assert map_nav._find_xunlu_anchors(object(), allow_ocr=False) == \
        (None, None, None)


def test_find_xunlu_anchors_merged_dao(monkeypatch):
    """从字段有层名时整行识别成「大雁塔二层到」，仍应找到 到 锚点。"""
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda img, regions: (XUNLU_TOP_OCR_LAYER2, None))
    dao, search, frm = map_nav._find_xunlu_anchors(np.zeros((831, 1378, 3),
                                                            dtype=np.uint8))
    assert dao == (655, 349, 679, 371)
    assert search == (804, 350, 851, 373)
    assert frm == (514, 351, 534, 368)


def test_find_xunlu_anchors_merged_from_and_dao_same_line(monkeypatch):
    """宽区域输出把「从大雁塔二层到」和「搜索」各识别为一行。"""
    items = [("从大雁塔二层到", (512, 348, 678, 373), 0.997),
             ("搜索", (804, 350, 854, 373), 0.999)]
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda img, regions: (items, None))
    dao, search, frm = map_nav._find_xunlu_anchors(np.zeros((831, 1378, 3),
                                                            dtype=np.uint8))
    assert dao is not None and search is not None
    assert frm == (512, 348, 678, 373)


def test_find_xunlu_anchors_excludes_advanced_search(monkeypatch):
    """地图窗口的「高级搜索」不能当面板搜索按钮（会盲点地图）。"""
    items = [("大雁塔二层到", (534, 349, 679, 371), 1.000),
             ("高级搜索", (700, 420, 780, 445), 0.99)]
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda img, regions: (items, None))
    _dao, search, _frm = map_nav._find_xunlu_anchors(
        np.zeros((831, 1378, 3), dtype=np.uint8))
    assert search is None


def test_prewalk_skips_same_map(monkeypatch):
    """同图导航不预走（直接点目标即可）。"""
    opened = []
    monkeypatch.setattr(map_nav, "_open_mini_map_quick",
                        lambda win: opened.append(1) or True)
    emits = []
    assert map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "灵兽村", None, emits.append) == 0.0
    assert opened == [] and emits == []


def test_prewalk_skips_non_xiuluo_task(monkeypatch):
    """预走路只认修罗/五倍任务：五环等其他任务从灵兽村出发不预走。"""
    opened = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "五环")
    monkeypatch.setattr(map_nav, "_open_mini_map_quick",
                        lambda win: opened.append(1) or True)
    assert map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "长安城", None, lambda m: None) == 0.0
    assert opened == []


def test_prewalk_clicks_configured_point(monkeypatch):
    """灵兽村→外地：按标定把坐标 (14,13) 换算成小地图点，再快速关图。"""
    calls = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "修罗")
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, target: {"sx": 2.9268, "sy": -2.9268,
                                             "ax": 275.3, "ay": 654.8})
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: True)
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: calls.append(("click", x, y)))
    monkeypatch.setattr(map_nav.game_map, "close_map_if_open",
                        lambda *a, **k: calls.append(("close", k)))
    emits = []
    dt = map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "大雁塔", "大雁塔一层", emits.append)
    assert dt >= 0.0
    # 原固定像素 (317,616) 的坐标反算值 → 换算回来同一像素（±1px）
    assert ("click", 316, 616) in calls
    assert any(c[0] == "close" for c in calls)
    close_kw = [c[1] for c in calls if c[0] == "close"][0]
    # 已知打开：免双帧复核 + 0.15s 快速复检（2026-11-25）
    assert close_kw.get("known_open") is True
    assert close_kw.get("poll_step") == 0.15
    assert any("预走路" in m for m in emits)


def test_prewalk_wubei_baoxiangguo_clicks_point(monkeypatch):
    """五倍任务（用户 2026-11-24）：宝象国 (75,135) 按标定换算成小地图点。

    预走路按任务模式识别（PREWALK_TASK_MODES 含五倍），导航内自动触发。
    """
    calls = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "五倍")
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, target: {"sx": 1.9459, "sy": -1.954,
                                             "ax": 329.5, "ay": 655.3})
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: True)
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: calls.append(("click", x, y)))
    monkeypatch.setattr(map_nav.game_map, "close_map_if_open",
                        lambda *a, **k: calls.append(("close", k)))
    emits = []
    dt = map_nav._prewalk_if_configured(
        object(), ("宝象国", 86, 86), "平顶山", None, emits.append)
    assert dt >= 0.0
    px = int(329.5 + 1.9459 * 75)   # 宝象国 (75,135) → 小地图点
    py = int(655.3 - 1.954 * 135)
    assert ("click", px, py) in calls
    assert any(c[0] == "close" for c in calls)
    assert any("预走路" in m for m in emits)


def test_prewalk_suppressed_skips(monkeypatch):
    """巫医轮次整轮抑制（用户 2026-11-24）：置位后不预走，复位恢复。"""
    opened = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "五倍")
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, target: {"sx": 1.9459, "sy": -1.954,
                                             "ax": 329.5, "ay": 655.3})
    monkeypatch.setattr(map_nav, "_open_mini_map_quick",
                        lambda win: opened.append(1) or True)
    monkeypatch.setattr(map_nav, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(map_nav.game_map, "close_map_if_open",
                        lambda *a, **k: None)
    map_nav.set_prewalk_suppressed(True)
    try:
        assert map_nav._prewalk_if_configured(
            object(), ("宝象国", 86, 86), "平顶山", None,
            lambda m: None) == 0.0
    finally:
        map_nav.set_prewalk_suppressed(False)
    assert opened == []
    # 复位后同场景照常预走（会尝试打开小地图并点击）
    assert map_nav._prewalk_if_configured(
        object(), ("宝象国", 86, 86), "平顶山", None,
        lambda m: None) >= 0.0
    assert opened == [1]


def test_prewalk_skips_when_minimap_unavailable(monkeypatch):
    """小地图打不开时跳过预走（不点任何东西）。"""
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "修罗")
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, target: {"sx": 2.9268, "sy": -2.9268,
                                             "ax": 275.3, "ay": 654.8})
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: False)
    clicked = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda *a: clicked.append(1))
    assert map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "大雁塔", "大雁塔一层",
        lambda m: None) == 0.0
    assert clicked == []


def test_open_mini_map_quick_retries_once(monkeypatch):
    """短轮询打开小地图：首点未开则重点一次，仍失败快速放弃。"""
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))

    seq = iter([False, False, True])
    monkeypatch.setattr(map_nav.game_map, "is_mini_map_open",
                        lambda win: next(seq, True))
    assert map_nav._open_mini_map_quick(object(), timeout=0.01) is True
    assert len(clicks) == 1

    clicks.clear()
    monkeypatch.setattr(map_nav.game_map, "is_mini_map_open",
                        lambda win: False)
    assert map_nav._open_mini_map_quick(object(), timeout=0.01) is False
    assert len(clicks) == 2  # 重试一次后放弃


def test_navigate_timing_summary(monkeypatch):
    """导航日志/结果带分段耗时（开图/定位/标定点击/行走/总计）。"""
    monkeypatch.setattr(map_nav, "parse_navigate_target",
                        lambda t: ("洛阳城", None))
    monkeypatch.setattr(map_nav.game_map, "resolve_world_map_name",
                        lambda n: n)
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("长安", 1, 2))
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a: (True, "地图已打开"))
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win: {"x": 100, "y": 200})
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, key: {"sx": 3.0, "sy": -3.0,
                                          "ax": 10.0, "ay": 600.0})
    monkeypatch.setattr(map_nav, "_aim_and_click",
                        lambda *a, **k: (True, (500, 400), ""))
    monkeypatch.setattr(map_nav, "_wait_movement", lambda *a, **k: True)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: (True, "到达 洛阳城 (10,20)"))
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: False)
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=emits.append)
    assert ok
    assert "耗时：" in info and "开图" in info and "总计" in info
    assert any(m.startswith("目标地图已打开") for m in emits)
    assert any(m.startswith("搜索条锚点") for m in emits)
    assert any(m.startswith("点击地图点") for m in emits)
    assert any(m.startswith("位移启动") for m in emits)
    assert any(m.startswith("耗时: ") and "行走到达" in m for m in emits)


def test_navigate_timing_layer_entry(monkeypatch):
    """层块点击触发的步行单独计「进层等待」，不混进开图。

    真机 2026-11-20 21:38 龙窟一层：点层块后人物从灵兽村步行 20.5s
    进层，旧统计把这 20.5s 全算进「开图 23.0s」。
    """
    import time

    monkeypatch.setattr(map_nav.game_map, "resolve_world_map_name",
                        lambda n: n)
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("灵兽村", 1, 2))
    # 灵兽村会触发预走路：测试里让小地图打不开直接跳过（不碰真实窗口）
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: False)

    def _open(win, base, layer, pos, emit, phases):
        time.sleep(0.05)
        phases["layer_entry"] = 0.05
        return True, "层内地图已打开"

    monkeypatch.setattr(map_nav, "_open_target_map", _open)
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win: {"x": 100, "y": 200})
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, key: {"sx": 3.0, "sy": -3.0,
                                          "ax": 10.0, "ay": 600.0})
    monkeypatch.setattr(map_nav, "_aim_and_click",
                        lambda *a, **k: (True, (500, 400), ""))
    monkeypatch.setattr(map_nav, "_wait_movement", lambda *a, **k: True)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: (True, "到达 龙窟一层 (10,20)"))
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: False)
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "龙窟一层", 10, 20, log_fn=emits.append)
    assert ok
    assert "进层等待" in info
    opened = [m for m in emits if m.startswith("目标地图已打开")][0]
    assert "进层等待" in opened and "开图" in opened
    summary = [m for m in emits if m.startswith("耗时: ")][0]
    assert "进层等待" in summary


def _patch_nav_happy(monkeypatch):
    """把导航后半程全部打桩为成功（标定/点击/到达）。"""
    monkeypatch.setattr(map_nav.game_map, "resolve_world_map_name",
                        lambda n: n)
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("长安", 1, 2))
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win: {"x": 100, "y": 200})
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, key: {"sx": 3.0, "sy": -3.0,
                                          "ax": 10.0, "ay": 600.0})
    monkeypatch.setattr(map_nav, "_aim_and_click",
                        lambda *a, **k: (True, (500, 400), ""))
    monkeypatch.setattr(map_nav, "_wait_movement", lambda *a, **k: True)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: (True, "到达 洛阳城 (10,20)"))
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: False)


def test_navigation_cancel_returns_interrupted(monkeypatch):
    """request_cancel 后在下个检查点退出：返回「已中断寻路」并清理。"""
    _patch_nav_happy(monkeypatch)
    cleaned = []
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: cleaned.append("maps"))
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: cleaned.append("panel") or False)

    def _open(win, base, layer, pos, emit, phases=None):
        map_nav.request_cancel()  # 开图期间用户点了「中断寻路」
        return True, "地图已打开"

    monkeypatch.setattr(map_nav, "_open_target_map", _open)
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=emits.append)
    assert not ok and info == "已中断寻路"
    assert "maps" in cleaned and "panel" in cleaned
    assert any("中断" in m for m in emits)


def test_navigation_cancel_cleared_on_next_run(monkeypatch):
    """上次中断的标记不得污染下一次导航（开始即清除）。"""
    map_nav.request_cancel()
    _patch_nav_happy(monkeypatch)
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=lambda m: None)
    assert ok


def test_navigate_timing_on_failure(monkeypatch):
    """失败路径也输出总耗时（便于定位卡在哪一步）。"""
    monkeypatch.setattr(map_nav, "parse_navigate_target",
                        lambda t: ("洛阳城", None))
    monkeypatch.setattr(map_nav.game_map, "resolve_world_map_name",
                        lambda n: n)
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("长安", 1, 2))
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a: (True, "地图已打开"))
    monkeypatch.setattr(map_nav, "locate_search_bar", lambda win: None)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: False)
    emits = []
    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=emits.append)
    assert not ok
    assert any(m.startswith("耗时: 地图窗口未出现") for m in emits)


def test_find_destination_link_rejects_wide_step_underline():
    """长路线首屏：整行黄色下划线（宽 300+，所在行不含目的地）不是链接。

    2026-11-20 21:47 灵兽村→大雁塔一层真机：列表首屏只有这种黄带
    （522,487,838,502），旧逻辑「取最靠下黄带」误点导致
    「点链接后目的地地图窗口未出现」，且不再滚动。
    """
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[60:75, 22:340] = (255, 255, 0)  # 宽 318 的整行下划线
    lines = [("走到冰窟(32,2)处通过传送点传送到北俱芦", 200, 67,
              (20, 55, 340, 80))]
    assert map_nav.find_destination_link(img, lines, "大雁塔一层",
                                         region=(0, 0, 420, 140)) is None


def test_find_destination_link_dest_text_yellow_only(monkeypatch):
    """用户规则 2026-11-20：可点文案与目的地文本相同且为黄色。

    步骤行末尾的黄色目的地名可点；同名白字（无黄色）不是链接。
    """
    _stub_rect_text(monkeypatch, lambda r: "大雁塔一层")
    lines = [("2)处再通过传送点传送到大雁塔一层", 200, 69, (20, 55, 380, 85))]
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[62:76, 300:373] = (255, 255, 0)  # 行尾「大雁塔一层」黄字
    pt = map_nav.find_destination_link(img, lines, "大雁塔一层",
                                       region=(0, 0, 420, 140))
    assert pt is not None and pt[0] > 300
    blank = np.zeros((140, 420, 3), dtype=np.uint8)
    assert map_nav.find_destination_link(blank, lines, "大雁塔一层",
                                         region=(0, 0, 420, 140)) is None


def test_find_destination_link_shape_fallback_without_ocr(monkeypatch):
    """OCR 读不出黄带文本时：短黄带贴近列表左缘按独立链接行接受。"""
    _stub_rect_text(monkeypatch, lambda r: "")   # 模拟识别失败
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[62:76, 33:106] = (255, 255, 0)  # 73x14，左缘 +33
    steps = [("走到北俱芦洲处", 200, 69, (20, 55, 340, 80))]
    pt = map_nav.find_destination_link(img, steps, "大雁塔六层",
                                       region=(0, 0, 420, 140))
    assert pt is not None and abs(pt[0] - 70) <= 1


def test_find_destination_link_rejects_other_layer_band(monkeypatch):
    """回归 2026-11-20 23:39：目的地行还没滚进可视区时，最靠下的黄带
    是别的层的链接（真机 灵兽村→大雁塔四层 点成「大雁塔三层」开错图）。"""
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[30:44, 33:106] = (255, 255, 0)   # 「大雁塔二层」
    img[60:74, 33:106] = (255, 255, 0)   # 「大雁塔三层」（更靠下）
    lines = [("到大雁塔二层", 66, 37, (20, 28, 120, 48)),
             ("到大雁塔三层", 66, 67, (20, 58, 120, 78))]
    checked = []

    def fake_text(img_, rect, *a, **k):
        checked.append(tuple(rect))
        return "大雁塔三层" if rect[1] >= 55 else "大雁塔二层"

    monkeypatch.setattr(map_nav, "_rect_text", fake_text)
    assert map_nav.find_destination_link(img, lines, "大雁塔四层",
                                         region=(0, 0, 420, 140)) is None
    assert checked  # 确实做了文本校验，而不是直接点最靠下黄带


def test_find_destination_link_clicks_verified_destination_band(monkeypatch):
    """滚出目的地行后：读回目的地的黄带照常可点。"""
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[60:74, 33:106] = (255, 255, 0)
    lines = [("到大雁塔四层", 66, 67, (20, 58, 120, 78))]
    _stub_rect_text(monkeypatch, lambda r: "大雁塔四层")
    pt = map_nav.find_destination_link(img, lines, "大雁塔四层",
                                       region=(0, 0, 420, 140))
    assert pt is not None and 55 <= pt[1] <= 80 and pt[0] >= 60


def test_find_destination_link_rejects_estimate_on_other_layer(monkeypatch):
    """OCR 行串色时，目的地子串估算位置读回别的层文本 → 不点。"""
    img = np.zeros((140, 420, 3), dtype=np.uint8)
    img[62:76, 300:373] = (255, 255, 0)   # 估算位置上是别的层黄字
    lines = [("走到大雁塔二层(83，56)处通过传送点传送到大雁塔四层", 200, 69,
              (20, 55, 380, 85))]
    _stub_rect_text(monkeypatch,
                    lambda r: "大雁塔二层83，56处通过传送点传送到")
    assert map_nav.find_destination_link(img, lines, "大雁塔四层",
                                         region=(0, 0, 420, 140)) is None


def test_read_map_window_title_picks_vertical_title(monkeypatch):
    """地图窗口标题：只认竖排（h≥2w）且匹配层名正则的文本。"""
    items = [("纵身百尺掷生死，散尽余霞明光现。忽见须弥悬日月",
              (937, 247, 956, 530), 0.920),          # 竖排诗句
             ("大雁塔四层", (972, 258, 1005, 416), 1.000),   # 竖排标题
             ("大雁塔三层", (471, 545, 574, 567), 1.000),    # 横排入口标签
             ("该场景怪物等级：30", (797, 630, 924, 648), 0.977)]
    monkeypatch.setattr(map_nav.game_map, "ocr_regions",
                        lambda img, regions: (items, None))
    assert map_nav._read_map_window_title(object()) == "大雁塔四层"
    monkeypatch.setattr(map_nav.game_map, "ocr_regions",
                        lambda img, regions: ([items[0], items[2], items[3]],
                                              None))
    assert map_nav._read_map_window_title(object()) == ""


def test_open_xunlu_destination_fails_on_wrong_map_title(monkeypatch):
    """链接点错层（开出三层地图）必须失败并关图，不能带着错图点坐标。"""
    dao, search, frm = (650, 350, 675, 370), (805, 350, 851, 373), \
                       (514, 351, 533, 368)
    clicks = []
    monkeypatch.setattr(map_nav.game_map, "open_world_map",
                        lambda win: (True, "ok"))
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda win: False)
    monkeypatch.setattr(map_nav, "_find_xunlu_anchors",
                        lambda img, allow_ocr=True: (dao, search, frm))
    monkeypatch.setattr(map_nav, "_type_text", lambda win, text: (True, ""))
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.game_map, "ocr_regions",
                        lambda img, regions: (
                            [("寻路", (364, 615, 402, 639), 0.999)], None))
    monkeypatch.setattr(map_nav, "find_destination_link",
                        lambda img, lines, dest, region=None: (574, 512))
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win, img=None, allow_ocr=True: {"x": 499,
                                                               "y": 217})
    monkeypatch.setattr(map_nav, "_read_map_window_title",
                        lambda img: "大雁塔三层")
    closed = []
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda win, quiet=False: closed.append(True))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _Win:
        rect = type("R", (), {"width": 1378, "height": 831})()

        def capture(self, force_activate=False):
            return object()

    emits = []
    ok, info = map_nav._open_xunlu_destination(_Win(), "大雁塔四层",
                                               emits.append)
    assert not ok
    assert "大雁塔三层" in info and "大雁塔四层" in info
    assert closed            # 错图已关
    assert (574, 512) in clicks  # 链接点过（随后才校验标题）


def test_open_xunlu_destination_stops_scrolling_at_bottom(monkeypatch):
    """滚到底（列表 OCR 文本不再变化）就提前失败，不再空转 12 次。"""
    dao, search, frm = (650, 350, 675, 370), (805, 350, 851, 373), \
                       (514, 351, 533, 368)
    monkeypatch.setattr(map_nav.game_map, "open_world_map",
                        lambda win: (True, "ok"))
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda win: False)
    monkeypatch.setattr(map_nav, "_find_xunlu_anchors",
                        lambda img, allow_ocr=True: (dao, search, frm))
    monkeypatch.setattr(map_nav, "_type_text", lambda win, text: (True, ""))
    monkeypatch.setattr(map_nav, "human_click", lambda win, x, y: None)

    def fake_ocr(img, regions):
        if regions and regions[0] in (map_nav.XUNLU_BTN_REGION,
                                      map_nav.XUNLU_BTN_WIDE_REGION):
            return [("寻路", (364, 615, 402, 639), 0.999)], None
        return [("走到大雁塔三层(14，39)处通过传送点传送",
                 (520, 511, 840, 533), 0.98)], None

    monkeypatch.setattr(map_nav.game_map, "ocr_regions", fake_ocr)
    monkeypatch.setattr(map_nav, "find_destination_link", lambda *a, **k: None)
    scrolls = []
    monkeypatch.setattr(map_nav.app_utils, "human_scroll",
                        lambda win, x, y, n: scrolls.append(n))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _Win:
        rect = type("R", (), {"width": 1378, "height": 831})()

        def capture(self, force_activate=False):
            return object()

    emits = []
    ok, info = map_nav._open_xunlu_destination(_Win(), "大雁塔四层",
                                               emits.append)
    assert not ok and "未找到" in info
    assert scrolls == [-map_nav.XUNLU_WHEEL_NOTCHES]   # 只滚一次就判到底


def test_xunlu_list_region_derived_from_anchors():
    dao = (640, 346, 685, 377)
    search = (798, 346, 862, 378)
    frm = (510, 346, 588, 374)
    assert map_nav._xunlu_list_region(dao, search, frm) == (505, 390, 870, 575)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_aim_and_click_corrects_stale_affine_direction(monkeypatch):
    """标定过期时的偏差修正方向必须收敛（px -= ex*sx）。

    回归 2026-11-20 审查发现的符号反了：旧实现 px += ex*sx 会让偏差
    每轮翻倍，3 次用尽报「不收敛」。
    """
    true = {"sx": 1.5, "sy": -1.5, "ax": 300.0, "ay": 600.0}
    stale = {"sx": 1.5, "sy": -1.5, "ax": 310.0, "ay": 600.0}

    def fake_hover(win, px, py):
        gx = int(round((px - true["ax"]) / true["sx"]))
        gy = int(round((py - true["ay"]) / true["sy"]))
        return (gx, gy)

    monkeypatch.setattr(map_nav, "_hover_coord", fake_hover)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    ok, pt, err = map_nav._aim_and_click(
        object(), stale, None, 100, 100, lambda m: None)
    assert ok, err
    assert clicks and clicks[0] == pt
    # 真实目标像素 = (300+1.5*100, 600-1.5*100) = (450, 450)
    assert abs(pt[0] - 450) <= 2 and abs(pt[1] - 450) <= 2


def test_aim_and_click_caches_correction(monkeypatch):
    """偏差修正并点击成功 → 回传修正后的标定（会话缓存，下次一次命中）。"""
    aff = {"sx": 2.0, "sy": -2.0, "ax": 100.0, "ay": 500.0}
    seq = iter([(54, 99), (50, 100)])  # 首次读回偏 (+4,-1)，修正后命中
    monkeypatch.setattr(map_nav, "_hover_coord",
                        lambda win, px, py: next(seq))
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    got = {}
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, None, 50, 100, lambda m: None,
        on_affine_corrected=lambda a: got.update(a))
    assert ok, err
    assert pt == (192, 298) and clicks == [(192, 298)]
    # 修正后原点：ax=100-4*2=92，ay=500-(-1)*(-2)=498
    assert got["ax"] == 92.0 and got["ay"] == 498.0


def test_aim_and_click_recovers_blocked_point_with_offset(monkeypatch):
    """内置标定点悬停无浮层：容差内小偏移找回地图内容，不回退完整采样。"""
    map_nav._CANCEL.clear()
    aff = {"sx": 2.0, "sy": -2.0, "ax": 100.0, "ay": 500.0}

    def fake_hover(win, px, py):
        if (int(px), int(py)) == (200, 300):
            return None  # 标定原点落在图标/边缘
        return (50, 100)

    monkeypatch.setattr(map_nav, "_hover_coord", fake_hover)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    got = {}
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, None, 50, 100, lambda m: None,
        on_affine_corrected=lambda a: got.update(a))
    assert ok, err
    # 容差半径 = AIM_TOLERANCE×2px = 4px，首个试探点 (+4,0)
    assert pt == (204, 300) and clicks == [(204, 300)]
    assert got == {}  # 偏移恢复无偏差修正，不写缓存


def test_aim_and_click_blocked_point_all_offsets_fail(monkeypatch):
    """容差内邻居都无浮层 → 返回失败，交由调用方完整采样。"""
    map_nav._CANCEL.clear()
    aff = {"sx": 2.0, "sy": -2.0, "ax": 100.0, "ay": 500.0}
    calls = []
    monkeypatch.setattr(
        map_nav, "_hover_coord",
        lambda w, px, py: calls.append((px, py)) or None)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click", lambda *a: clicks.append(1))
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, None, 50, 100, lambda m: None)
    assert ok is False and "无浮层" in err and clicks == []
    assert len(calls) == 1 + len(map_nav._aim_probe_offsets(2.0, -2.0))


def test_aim_and_click_probe_rejects_neighbor_out_of_tolerance(monkeypatch):
    """邻居读回超差（标定可能过期）→ 不点击、不回退错误，交由完整采样。"""
    map_nav._CANCEL.clear()
    aff = {"sx": 2.0, "sy": -2.0, "ax": 100.0, "ay": 500.0}
    monkeypatch.setattr(map_nav, "_hover_coord",
                        lambda w, px, py: None if (px, py) == (200, 300)
                        else (60, 100))  # 偏 10 格，远超容差
    clicks = []
    monkeypatch.setattr(map_nav, "human_click", lambda *a: clicks.append(1))
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, None, 50, 100, lambda m: None)
    assert ok is False and "无浮层" in err and clicks == []


def test_aim_and_click_probe_recovers_real_pantaoyuan_case(monkeypatch):
    """2026-11-23 蟠桃园 (27,112) 实机：目标像素被地图内容盖住。

    标定 (sx 2.0/-2.0, ax 489.0, ay 653.7) 时目标像素 (543,429)；
    (542..544,429) 与 (543,430) 悬停无浮层，±14px 的读回偏 7 格——
    旧实现偏差修正搬回原像素，3 次后回退完整采样。容差内探测应命中
    邻居（-4,0），且点击点与目标差 ≤2 格。
    """
    map_nav._CANCEL.clear()
    aff = {"sx": 2.0, "sy": -2.0, "ax": 489.0, "ay": 653.7}
    blocked = {(542, 429), (543, 429), (544, 429), (543, 430)}

    def fake_hover(win, px, py):
        if (int(px), int(py)) in blocked:
            return None
        return (int(round((px - 489.0) / 2.0)),
                int(round((653.7 - py) / 2.0)))

    monkeypatch.setattr(map_nav, "_hover_coord", fake_hover)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, None, 27, 112, lambda m: None)
    assert ok, err
    assert clicks == [pt]
    assert abs(pt[0] - 543) <= 4 and abs(pt[1] - 429) <= 4


def test_aim_and_click_probe_after_sampling(monkeypatch):
    """完整采样后目标像素仍被盖住：邻居探测优先于向锚点收缩。

    2026-11-23 08:40 实机：采样后瞄准 (543,429) 无浮层 → 收缩到
    (611,378) 读回偏 34/26 格 → 修正搬回 (543,430) 再次无浮层 →
    3 次用尽判失败（白付 14s 采样）。邻居探测应直接命中。
    """
    map_nav._CANCEL.clear()
    aff = {"sx": 2.0, "sy": -2.0, "ax": 489.0, "ay": 653.7}

    def fake_hover(win, px, py):
        if (int(px), int(py)) in {(542, 429), (543, 429), (544, 429)}:
            return None
        return (int(round((px - 489.0) / 2.0)),
                int(round((653.7 - py) / 2.0)))

    monkeypatch.setattr(map_nav, "_hover_coord", fake_hover)
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    ok, pt, err = map_nav._aim_and_click(
        object(), aff, (679, 327), 27, 112, lambda m: None)
    assert ok, err
    assert clicks == [pt]
    assert abs(pt[0] - 543) <= 4 and abs(pt[1] - 429) <= 4


def test_aim_and_click_no_correction_no_cache(monkeypatch):
    """首次悬停即命中：不回调缓存。"""
    aff = {"sx": 2.0, "sy": -2.0, "ax": 100.0, "ay": 500.0}
    monkeypatch.setattr(map_nav, "_hover_coord", lambda *a: (50, 100))
    monkeypatch.setattr(map_nav, "human_click", lambda *a: None)
    called = []
    ok, pt, _err = map_nav._aim_and_click(
        object(), aff, None, 50, 100, lambda m: None,
        on_affine_corrected=lambda a: called.append(a))
    assert ok and pt == (200, 300) and called == []


def test_start_impl_flags_dayan_pagoda_xunlu_entry(monkeypatch):
    """大雁塔跨层标记 xunlu_entry（面板被地图窗口遮住，需行走后补关）。"""
    _patch_start_env(monkeypatch)
    ok, _info, ctx = map_nav._start_impl(
        object(), "大雁塔四层", 105, 36, lambda m: None)
    assert ok and ctx["xunlu_entry"] is True

    # 已在目标层：直接开小地图，不涉及寻路面板
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: ("大雁塔四层", 1, 2))
    ok, _info, ctx = map_nav._start_impl(
        object(), "大雁塔四层", 105, 36, lambda m: None)
    assert ok and ctx["xunlu_entry"] is False

    # 其他层级图（龙窟）走层块页，不涉及寻路面板
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("灵兽村", 1, 2))
    monkeypatch.setattr(map_nav, "_prewalk_if_configured",
                        lambda *a, **k: 0.0)
    ok, _info, ctx = map_nav._start_impl(
        object(), "龙窟一层", 10, 20, lambda m: None)
    assert ok and ctx["xunlu_entry"] is False


def test_start_impl_pos_hint_skips_position_read(monkeypatch):
    """pos_hint：开图前免截帧+位置 OCR（只认地名参与路线判断）。"""
    _patch_start_env(monkeypatch)
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: (_ for _ in ()).throw(
                            AssertionError("传入 hint 不应再读位置")))
    seen = {}

    def _prewalk(win, pos, base, layer, emit):
        seen["pos"] = pos
        return 0.0

    monkeypatch.setattr(map_nav, "_prewalk_if_configured", _prewalk)
    emits = []
    ok, _info, _ctx = map_nav._start_impl(
        object(), "凤巢七层", 10, 20, emits.append,
        pos_hint=("灵兽村", 120, 88))
    assert ok and seen["pos"] == ("灵兽村", 120, 88)
    assert any("hint" in m for m in emits)


def test_start_impl_without_hint_reads_position(monkeypatch):
    """未传 hint：照常读位置（hint 只是可选优化）。"""
    _patch_start_env(monkeypatch)
    read = []
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: read.append(1) or ("长安", 1, 2))
    ok, _info, _ctx = map_nav._start_impl(
        object(), "凤巢七层", 10, 20, lambda m: None)
    assert ok and read == [1]


def test_start_impl_trusted_clicks_computed_point_without_hover(monkeypatch):
    """trusted：有标定直接点（免悬停校验/采样）——固定领取点专用。"""
    _patch_start_env(monkeypatch)
    monkeypatch.setattr(map_nav, "_aim_and_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("trusted 不应悬停校验")))
    monkeypatch.setattr(map_nav, "_collect_hover_samples",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("trusted 不应完整采样")))
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    ok, _info, ctx = map_nav._start_impl(
        object(), "洛阳城", 10, 20, lambda m: None, trusted=True)
    assert ok and ctx["point"] == (40, 540)
    assert clicks == [(40, 540)]


def test_start_impl_trusted_without_affine_fails_fast(monkeypatch):
    """trusted 无标定：立即失败（调用方回退固定像素），不跑悬停采样。"""
    _patch_start_env(monkeypatch)
    monkeypatch.setattr(map_nav, "affine_for", lambda win, key: None)
    monkeypatch.setattr(map_nav, "_collect_hover_samples",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("trusted 不应完整采样")))
    emits = []
    ok, info, ctx = map_nav._start_impl(
        object(), "洛阳城", 10, 20, emits.append, trusted=True)
    assert not ok and ctx is None and "无地图标定" in info
    assert any("trusted 直点无标定" in m for m in emits)


def test_start_impl_trusted_rejects_out_of_window(monkeypatch):
    """trusted 换算点越界（标定异常）：不点，返回失败由调用方回退。"""
    _patch_start_env(monkeypatch)
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, key: {"sx": 1.0, "sy": -1.0,
                                          "ax": 2000.0, "ay": 2000.0})
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    ok, info, ctx = map_nav._start_impl(
        object(), "洛阳城", 10, 20, lambda m: None, trusted=True)
    assert not ok and ctx is None and "超出窗口" in info
    assert clicks == []


def test_navigate_closes_xunlu_panel_after_walk_start(monkeypatch):
    """大雁塔：位移启动后先关地图再补关寻路面板，然后才等到达。

    2026-11-21 实机：开图阶段目的地地图窗口遮住面板，X 点不到，
    面板会一直留到行走/战斗中（用户截图确认）。
    """
    _patch_nav_happy(monkeypatch)
    order = []
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "目的地地图已打开"))
    monkeypatch.setattr(map_nav, "_wait_movement",
                        lambda *a, **k: order.append("move") or True)
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: order.append("maps"))
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: order.append("panel") or True)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: order.append("arrive") or (True, "到达"))
    emits = []
    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "大雁塔四层", 105, 36, log_fn=emits.append)
    assert ok
    assert (order.index("move") < order.index("maps")
            < order.index("panel") < order.index("arrive"))
    assert any("已关闭残留「寻路」面板" in m for m in emits)


def test_navigate_normal_map_closes_early(monkeypatch):
    """普通地图也在位移启动后立即关图（与行走并行，不占到达后收尾）。

    2026-11-25 性能：到达等待只读 HUD 位置、不依赖地图窗口；原实现
    到达后才 close_maps（含 1s 关闭确认），每轮白等 ~1.5s。收尾的
    _cleanup 仍幂等调用一次（地图已关时双帧检查快速返回）。
    """
    _patch_nav_happy(monkeypatch)
    order = []
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    monkeypatch.setattr(map_nav, "_wait_movement",
                        lambda *a, **k: order.append("move") or True)
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: order.append("maps"))
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: order.append("panel") or False)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: order.append("arrive") or (True, "到达"))
    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=lambda m: None)
    assert ok
    assert order.index("move") < order.index("maps") < order.index("arrive")
    assert order[-2:] == ["maps", "panel"]  # 收尾幂等再清理一次


def test_start_impl_caches_corrected_affine(monkeypatch):
    """_start_impl 把修正后的标定写入会话缓存（remember_affine）。"""
    _patch_start_env(monkeypatch)

    def _aim(win, aff, anchor, x, y, emit, on_affine_corrected=None):
        if on_affine_corrected is not None:
            on_affine_corrected(dict(aff, ax=1.0))
        return True, (500, 400), ""

    monkeypatch.setattr(map_nav, "_aim_and_click", _aim)
    saved = []
    monkeypatch.setattr(
        map_nav, "remember_affine",
        lambda win, key, aff: saved.append((key, aff["ax"])))
    ok, _info, _pt = map_nav.start_navigation(
        object(), "洛阳城", 10, 20, log_fn=lambda m: None)
    assert ok and saved == [("洛阳城", 1.0)]


# ---------------- 到达等待：停滞补寻路 + 生产日志降噪 ----------------

def _arrival_win():
    class _AW:
        def capture(self, force_activate=False):
            return object()
    return _AW()


def test_wait_arrival_restart_hint_when_stalled(monkeypatch):
    """位置 5s 不变 → 返回补寻路提示（由上层重新开图再点一次）。"""
    map_nav._CANCEL.clear()
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: ("灵兽村", 10, 10))
    monkeypatch.setattr(map_nav, "ARRIVE_RESTART_STALL", 0.0)
    monkeypatch.setattr(map_nav, "ARRIVE_STALL_TIMEOUT", 99.0)
    monkeypatch.setattr(map_nav, "ARRIVE_POLL_INTERVAL", 0.0)
    ok, info = map_nav._wait_arrival(
        _arrival_win(), "大雁塔四层", "大雁塔四层", 108, 37,
        lambda m: None, allow_restart=True)
    assert not ok and info == map_nav.ARRIVE_RESTART_TEXT


def test_wait_arrival_stall_failure_without_restart(monkeypatch):
    """补过一次后（allow_restart=False）仍停滞 → 按不可达失败。"""
    map_nav._CANCEL.clear()
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: ("灵兽村", 10, 10))
    monkeypatch.setattr(map_nav, "ARRIVE_STALL_TIMEOUT", 0.0)
    monkeypatch.setattr(map_nav, "ARRIVE_POLL_INTERVAL", 0.0)
    ok, info = map_nav._wait_arrival(
        _arrival_win(), "大雁塔四层", "大雁塔四层", 108, 37,
        lambda m: None, allow_restart=False)
    assert not ok and "未变化" in info and "最后位置" in info


def test_wait_arrival_skips_frame_check_when_watch_confirms_battle(monkeypatch):
    """战斗线程已确认战斗中：跳过截图 + 条带模板判定（2026-11-25）。

    只冻结停滞计时并顺延超时；线程未确认（本次等待首次轮询）后才做
    帧级战斗判定。
    """
    map_nav._CANCEL.clear()
    polls = {"watch": 0, "cap": 0, "frame": 0}

    def _watch():
        polls["watch"] += 1
        return polls["watch"] <= 3

    class _W:
        def capture(self, force_activate=False):
            polls["cap"] += 1
            return object()

    monkeypatch.setattr(map_nav, "watch_in_battle", _watch)
    monkeypatch.setattr(
        map_nav, "is_in_battle_frame",
        lambda img: polls.__setitem__("frame", polls["frame"] + 1) or False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: ("大雁塔四层", 108, 37))
    monkeypatch.setattr(map_nav, "ARRIVE_POLL_INTERVAL", 0.0)
    ok, _info = map_nav._wait_arrival(
        _W(), "大雁塔四层", "大雁塔四层", 108, 37, lambda m: None)
    assert ok
    assert polls["cap"] == 1    # 线程确认期零截图，随后一帧即到达
    assert polls["frame"] == 1  # 线程确认期零帧级战斗判定


def test_navigate_retries_once_after_stall(monkeypatch):
    """停滞提示 → 自动重新导航一次；补过仍失败不无限重试。"""
    calls = []
    hints = []

    def fake_impl(win, target, x, y, emit, verbose=False, allow_restart=False,
                  on_walk_start=None, pos_hint=None):
        calls.append(allow_restart)
        hints.append(pos_hint)
        if len(calls) == 1:
            return False, map_nav.ARRIVE_RESTART_TEXT
        return True, "到达 大雁塔四层 (105,36)"

    monkeypatch.setattr(map_nav, "_navigate_impl", fake_impl)
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "大雁塔四层", 108, 37, log_fn=emits.append,
        pos_hint=("灵兽村", 120, 88))
    assert ok and "到达" in info
    assert calls == [True, False]
    # 停滞重启时人物已移动：hint 过期，第二趟必须重新读位置
    assert hints == [("灵兽村", 120, 88), None]
    assert any("重新开图补一次" in m for m in emits)


def test_navigate_no_retry_when_second_stall(monkeypatch):
    calls = []

    def fake_impl(win, target, x, y, emit, verbose=False, allow_restart=False,
                  on_walk_start=None, pos_hint=None):
        calls.append(allow_restart)
        return False, map_nav.ARRIVE_RESTART_TEXT

    monkeypatch.setattr(map_nav, "_navigate_impl", fake_impl)
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "大雁塔四层", 108, 37, log_fn=lambda m: None)
    assert not ok and info == map_nav.ARRIVE_RESTART_TEXT
    assert calls == [True, False]   # 只补一次


def test_navigate_on_walk_start_hook_before_arrival(monkeypatch):
    """位移启动后、到达等待前调用 on_walk_start（行走期插队回调）。"""
    _patch_nav_happy(monkeypatch)
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    order = []

    def _arrive(*a, **k):
        order.append("arrive")
        return True, "到达 洛阳城 (10,20)"

    monkeypatch.setattr(map_nav, "_wait_arrival", _arrive)
    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=lambda m: None,
        on_walk_start=lambda: order.append("hook"))
    assert ok
    assert order == ["hook", "arrive"]


def test_navigate_on_walk_start_exception_ignored(monkeypatch):
    """回调异常只记日志，不拖垮导航。"""
    _patch_nav_happy(monkeypatch)
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    emits = []

    def _boom():
        raise RuntimeError("boom")

    ok, _info = map_nav.navigate_to_map_coordinate(
        object(), "洛阳城", 10, 20, log_fn=emits.append, on_walk_start=_boom)
    assert ok
    assert any("行走起步回调异常" in m for m in emits)


def _patch_start_env(monkeypatch, aim=None):
    """打桩 start_navigation 前半段（开图/搜索条/标定/点击）。"""
    monkeypatch.setattr(map_nav.game_map, "resolve_world_map_name",
                        lambda n: n)
    monkeypatch.setattr(map_nav, "_read_pos", lambda win: ("长安", 1, 2))
    monkeypatch.setattr(map_nav, "_prewalk_if_configured",
                        lambda *a, **k: 0.0)
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win: {"x": 100, "y": 200})
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, key: {"sx": 3.0, "sy": -3.0,
                                          "ax": 10.0, "ay": 600.0})
    monkeypatch.setattr(map_nav, "_aim_and_click",
                        aim or (lambda *a, **k: (True, (500, 400), "")))
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)
    monkeypatch.setattr(map_nav, "_close_xunlu_panel_if_open",
                        lambda *a: None)


def test_start_navigation_clicks_without_waiting_arrival(monkeypatch):
    """只开图/标定/点击：不等位移/到达、不在成功路径关地图。"""
    _patch_start_env(monkeypatch)
    called = []
    monkeypatch.setattr(map_nav, "_wait_movement",
                        lambda *a, **k: called.append("move") or True)
    monkeypatch.setattr(map_nav, "_wait_arrival",
                        lambda *a, **k: called.append("arrival") or (True, ""))
    closed = []
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: closed.append(1))
    ok, info, pt = map_nav.start_navigation(
        object(), "洛阳城", 321, 110, log_fn=lambda m: None)
    assert ok and pt == (500, 400)
    assert "已点击目标点" in info
    assert called == []  # 既不等位移也不等到达
    assert closed == []  # 成功时地图保持打开，交任务侧关闭


def test_start_navigation_on_map_open_before_click(monkeypatch):
    """on_map_open 在地图打开后、点击前调用；回调异常不影响寻路。"""
    order = []

    def _aim(*a, **k):
        order.append("click")
        return True, (500, 400), ""

    _patch_start_env(monkeypatch, aim=_aim)
    ok, _info, _pt = map_nav.start_navigation(
        object(), "洛阳城", 321, 110, log_fn=lambda m: None,
        on_map_open=lambda: order.append("map_open"))
    assert ok and order == ["map_open", "click"]

    order.clear()

    def _boom():
        raise RuntimeError("boom")

    ok, _info, _pt = map_nav.start_navigation(
        object(), "洛阳城", 321, 110, log_fn=lambda m: None,
        on_map_open=_boom)
    assert ok and order == ["click"]  # 回调异常被吞，点击照常


def test_start_navigation_failure_cleans_maps(monkeypatch):
    """开图失败：清理残留地图（寻路面板不会自动关闭）。"""
    _patch_start_env(monkeypatch)
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (False, "地图打不开"))
    closed = []
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: closed.append(1))
    ok, info, pt = map_nav.start_navigation(
        object(), "洛阳城", 321, 110, log_fn=lambda m: None)
    assert ok is False and info == "地图打不开" and pt is None
    assert closed == [1]


def test_prewalk_point_is_map_coordinate(monkeypatch):
    """预走路点坐标化：灵兽村 (14,13) 按标定换算成小地图点击像素。

    原固定像素 (317,616) 的标定反算即 (14,13)；换算后点击点应回到
    同一像素（灵兽村内置标定 sx 2.9268/ax 275.3 下为 (316.3,616.8)）。
    """
    clicks = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "修罗")
    monkeypatch.setattr(map_nav, "affine_for",
                        lambda win, target: {"sx": 2.0, "sy": -2.0,
                                             "ax": 275.0, "ay": 655.0})
    monkeypatch.setattr(map_nav, "names_match", lambda a, b: False)
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: True)
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.game_map, "close_map_if_open",
                        lambda *a, **k: None)
    dt = map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "长安城", None, lambda m: None)
    assert dt > 0
    assert map_nav.PREWALK_POINTS["灵兽村"] == (14, 13)
    assert clicks == [(275 + 2 * 14, 655 - 2 * 13)]


def test_prewalk_skips_without_affine(monkeypatch):
    """无标定（窗口尺寸/布局不匹配）时不按旧像素硬点，直接跳过预走。"""
    clicks = []
    monkeypatch.setattr(map_nav.app_utils, "current_task_mode",
                        lambda: "修罗")
    monkeypatch.setattr(map_nav, "affine_for", lambda win, target: None)
    monkeypatch.setattr(map_nav, "names_match", lambda a, b: False)
    monkeypatch.setattr(map_nav, "_open_mini_map_quick", lambda win: True)
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    dt = map_nav._prewalk_if_configured(
        object(), ("灵兽村", 1, 2), "长安城", None, lambda m: None)
    assert dt == 0
    assert clicks == []


def test_wait_map_name_logs_only_name_changes(monkeypatch):
    """进层等待只在地名变化时输出位置（凤巢逐层步行不刷屏）。"""
    map_nav._CANCEL.clear()
    seq = [("凤巢二层", 59, 48), ("凤巢二层", 66, 52), ("凤巢三层", 26, 22),
           ("凤巢三层", 21, 18), ("凤巢四层", 106, 49),
           ("凤巢四层", 110, 53), ("凤巢五层", 30, 24)]

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(
        map_nav.game_map, "read_map_position",
        lambda win, img=None: seq.pop(0) if seq else ("凤巢五层", 30, 24))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    assert map_nav._wait_map_name(_W(), "凤巢五层", 10, emits.append) is True
    pos_lines = [m for m in emits if m.startswith("位置")]
    assert pos_lines == ["位置: ('凤巢二层', 59, 48)",
                         "位置: ('凤巢三层', 26, 22)",
                         "位置: ('凤巢四层', 106, 49)",
                         "位置: ('凤巢五层', 30, 24)"]


def test_wait_map_name_skips_frame_check_when_watch_confirms_battle(
        monkeypatch):
    """战斗线程已确认战斗中：跳过截图 + 战斗模板判定（2026-11-25）。"""
    map_nav._CANCEL.clear()
    polls = {"watch": 0, "cap": 0, "frame": 0}

    def _watch():
        polls["watch"] += 1
        return polls["watch"] <= 3

    class _W:
        def capture(self, force_activate=False):
            polls["cap"] += 1
            return object()

    monkeypatch.setattr(map_nav, "watch_in_battle", _watch)
    monkeypatch.setattr(
        map_nav, "is_in_battle_frame",
        lambda img: polls.__setitem__("frame", polls["frame"] + 1) or False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: ("凤巢五层", 30, 24))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    assert map_nav._wait_map_name(_W(), "凤巢五层", 10, emits.append) is True
    assert polls["cap"] == 1    # 线程确认期零截图，随后一帧即命中
    assert polls["frame"] == 1  # 线程确认期零帧级战斗判定


def test_wait_arrival_position_log_quiet_without_verbose(monkeypatch):
    """生产默认只在地名变化时输出位置；诊断 verbose=True 逐次输出。"""
    map_nav._CANCEL.clear()
    seq = [("灵兽村", 10, 10), ("灵兽村", 11, 10), ("灵兽村", 12, 11),
           ("冰窟", 5, 5), ("冰窟", 6, 6)]

    def fake_pos(win, img=None):
        return seq.pop(0) if seq else ("大雁塔四层", 108, 37)

    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position", fake_pos)
    monkeypatch.setattr(map_nav, "ARRIVE_POLL_INTERVAL", 0.0)
    emits = []
    ok, _info = map_nav._wait_arrival(
        _arrival_win(), "大雁塔四层", "大雁塔四层", 108, 37, emits.append)
    assert ok
    pos_lines = [m for m in emits if m.startswith("位置")]
    assert pos_lines == ["位置: ('灵兽村', 10, 10)",
                         "位置: ('冰窟', 5, 5)",
                         "位置: ('大雁塔四层', 108, 37)"]

    map_nav._CANCEL.clear()
    seq = [("灵兽村", 10, 10), ("灵兽村", 11, 10), ("灵兽村", 12, 11)]
    emits2 = []
    ok, _info = map_nav._wait_arrival(
        _arrival_win(), "大雁塔四层", "大雁塔四层", 108, 37,
        emits2.append, verbose=True)
    assert ok
    assert len([m for m in emits2 if m.startswith("位置")]) == 4


def test_start_navigation_unchecks_task_filter(monkeypatch):
    """开图后、点击前取消小地图「任务」筛选（仅名望需要勾选）。"""
    _patch_start_env(monkeypatch)
    calls = []
    monkeypatch.setattr(
        map_nav.game_map, "uncheck_minimap_task_filter",
        lambda win: calls.append(1) or (True, "「任务」筛选未勾选"))
    ok, _info, _pt = map_nav.start_navigation(
        object(), "洛阳城", 321, 110, log_fn=lambda m: None)
    assert ok and calls == [1]


# ---------------- 特殊地图（莲花洞等，世界地图无标签） ----------------

def test_special_map_entry_table():
    """莲花洞入口定义：父地图平顶山 (102,42)，场景地图黄字锚点同名。"""
    entry = map_nav.special_map_entry("莲花洞")
    assert entry == {"parent": "平顶山", "entry": (102, 42),
                     "npc_anchor": "莲花洞", "portal": (84, 9)}
    assert map_nav.special_map_entry(" 莲花洞 ") is entry
    assert map_nav.special_map_entry("洛阳城") is None
    assert map_nav.special_map_entry("") is None


def test_wait_arrival_accepts_special_enter_name(monkeypatch):
    """特殊地图经父地图入口自动进场：HUD 地名变为特殊地图名即到达。"""
    map_nav._CANCEL.clear()
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: ("莲花洞", 83, 10))
    emits = []
    ok, info = map_nav._wait_arrival(
        _arrival_win(), "平顶山", None, 102, 42, emits.append,
        enter_name="莲花洞")
    assert ok and "莲花洞" in info
    # 未传 enter_name 时同帧不按特殊地图到达（普通导航行为不变）
    map_nav._CANCEL.clear()
    monkeypatch.setattr(map_nav, "ARRIVE_STALL_TIMEOUT", 0.0)
    ok2, info2 = map_nav._wait_arrival(
        _arrival_win(), "平顶山", None, 102, 42, lambda m: None)
    assert not ok2 and "未变化" in info2


def _fake_img(color, size=(40, 40)):
    from PIL import Image
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, :, :] = color
    return Image.fromarray(arr, "RGB")


def _fake_filter_boxes(states):
    """按调用序返回筛选行五框（第 3 个 =「普通」状态由 states 给出）。"""
    calls = {"n": 0}

    def fake(_img):
        i = min(calls["n"], len(states) - 1)
        calls["n"] += 1
        checked = states[i]
        return [(False, 275, 657), (False, 343, 657),
                (checked, 411, 657), (False, 479, 657), (False, 547, 657)]

    return fake


def test_putong_filter_box_selects_third_box(monkeypatch):
    """「普通」= 筛选行第 3 个框（全部/传送/普通/任务/商业）。"""
    monkeypatch.setattr(
        map_nav.game_map, "minimap_filter_boxes",
        lambda img: [(False, 275, 657), (False, 343, 657), (True, 411, 657),
                     (False, 479, 657), (False, 547, 657)])
    assert map_nav._putong_filter_box(object()) == (True, 411, 657)
    monkeypatch.setattr(map_nav.game_map, "minimap_filter_boxes",
                        lambda img: None)
    assert map_nav._putong_filter_box(object()) is None


def _npc_test_image(anchor=(100, 100, 150, 120)):
    """构造带绿字 NPC 行的帧（锚点下方绿色像素行）。"""
    from PIL import Image
    arr = np.zeros((300, 500, 3), dtype=np.uint8)
    arr[:, :, :] = (60, 60, 60)
    arr[124:134, 110:150] = (0, 255, 0)   # 金角大王绿字行（合成）
    return Image.fromarray(arr, "RGB")


def test_find_special_entry_npc_uses_anchor_and_green_row(monkeypatch):
    """锚点黄字正下方顶行绿字中点 = 入口 NPC 点击点（金角/银角均可）。"""
    img = _npc_test_image()
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda im, regions: ([("莲花洞", (100, 100, 150, 120), 0.99)], None))

    class _W:
        def capture(self, force_activate=False):
            return img

    pt = map_nav._find_special_entry_npc(
        _W(), "莲花洞", {"x": 230, "y": 60})
    # band: l=75, top=117；绿行 x=110..149、y=124..133 → (129, 130)
    assert pt == (129, 130)


def test_find_special_entry_npc_missing_anchor(monkeypatch):
    monkeypatch.setattr(
        map_nav.game_map, "ocr_regions",
        lambda im, regions: ([("平顶山", (100, 100, 150, 120), 0.99)], None))

    class _W:
        def capture(self, force_activate=False):
            return _npc_test_image()

    assert map_nav._find_special_entry_npc(
        _W(), "莲花洞", {"x": 230, "y": 60}) is None


def test_ensure_minimap_putong_filter_clicks_unchecked(monkeypatch):
    """「普通」未勾选时点筛选行模板定位的第 3 个框一次；已勾选不点。"""
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.game_map, "minimap_filter_boxes",
                        _fake_filter_boxes([False, True]))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _W:
        def capture(self, force_activate=False):
            return object()

    ok, info = map_nav._ensure_minimap_putong_filter(
        _W(), lambda m: None)
    assert ok and info == "已勾选「普通」筛选"
    assert clicks == [(411, 657)]   # 模板定位的第 3 个框中心

    clicks.clear()
    monkeypatch.setattr(map_nav.game_map, "minimap_filter_boxes",
                        _fake_filter_boxes([True]))
    ok2, _info2 = map_nav._ensure_minimap_putong_filter(
        _W(), lambda m: None)
    assert ok2 and clicks == []


def test_navigate_special_already_inside(monkeypatch):
    """已在特殊地图：直接洞内步行，不再开图/寻路。"""
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: ("莲花洞", 20, 21))
    calls = []
    monkeypatch.setattr(map_nav, "_navigate_impl",
                        lambda *a, **k: calls.append(1) or (True, ""))
    walked = []
    monkeypatch.setattr(
        map_nav, "_walk_special_coordinate",
        lambda win, target, x, y, emit, **k:
        walked.append((target, x, y)) or (True, "已到达莲花洞 (75,21)"))
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "莲花洞", 75, 21, log_fn=emits.append)
    assert ok and "已到达莲花洞" in info and calls == []
    assert walked == [("莲花洞", 75, 21)]
    assert any("直接步行到坐标" in m for m in emits)


def test_navigate_special_enters_via_parent_entry(monkeypatch):
    """未进入时经父地图入口坐标导航；进场（HUD 改名）即成功。"""
    pos_seq = [("宝象国", 110, 102), ("莲花洞", 83, 10)]
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: pos_seq.pop(0) if pos_seq else
                        ("莲花洞", 83, 10))
    calls = []

    def fake_impl(win, target, x, y, emit, verbose=False,
                  allow_restart=False, on_walk_start=None, enter_name=None):
        calls.append((target, x, y, enter_name))
        return True, "已进入莲花洞 (83,10)"

    monkeypatch.setattr(map_nav, "_navigate_impl", fake_impl)
    monkeypatch.setattr(map_nav, "_enter_special_by_npc",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("不应走 NPC 兜底")))
    monkeypatch.setattr(map_nav, "_walk_special_coordinate",
                        lambda *a, **k: (True, "已到达莲花洞 (74,20)"))
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "莲花洞", 75, 21, log_fn=emits.append)
    assert ok and "已到达莲花洞" in info
    assert calls == [("平顶山", 102, 42, "莲花洞")]
    assert any("经平顶山入口" in m for m in emits)


def test_navigate_special_falls_back_to_npc_entry(monkeypatch):
    """入口坐标到达但未自动进门 → 场景地图 NPC 入口兜底。"""
    pos_seq = [("宝象国", 110, 102), ("平顶山", 102, 42)]
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: pos_seq.pop(0) if pos_seq else
                        ("平顶山", 102, 42))
    monkeypatch.setattr(map_nav, "_navigate_impl",
                        lambda *a, **k: (True, "到达 平顶山 (102,42)"))
    monkeypatch.setattr(map_nav, "_wait_special_enter",
                        lambda *a, **k: False)
    monkeypatch.setattr(map_nav, "_enter_special_by_npc",
                        lambda win, target, entry, emit:
                        (True, f"已通过 NPC 入口进入{target}"))
    monkeypatch.setattr(map_nav, "_walk_special_coordinate",
                        lambda *a, **k: (True, "已到达莲花洞 (75,21)"))
    emits = []
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "莲花洞", 75, 21, log_fn=emits.append)
    assert ok and "已到达莲花洞" in info
    assert any("改走 NPC 入口" in m for m in emits)


def test_navigate_special_both_entries_fail(monkeypatch):
    pos_seq = [("宝象国", 110, 102), ("平顶山", 102, 42)]
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: pos_seq.pop(0) if pos_seq else
                        ("平顶山", 102, 42))
    monkeypatch.setattr(map_nav, "_navigate_impl",
                        lambda *a, **k: (False, "开图失败"))
    monkeypatch.setattr(map_nav, "_enter_special_by_npc",
                        lambda *a, **k: (False, "未找到入口 NPC"))
    ok, info = map_nav.navigate_to_map_coordinate(
        object(), "莲花洞", 75, 21, log_fn=lambda m: None)
    assert not ok and "莲花洞 进入失败" in info and "未找到入口 NPC" in info


def _patch_npc_entry_env(monkeypatch, clicks, emits, start_results):
    """打桩 NPC 入口兜底的各环节（开图/筛选/定位/点击/起步判定）。"""
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: ("平顶山", 94, 35))
    monkeypatch.setattr(map_nav, "_open_target_map",
                        lambda *a, **k: (True, "地图已打开"))
    monkeypatch.setattr(map_nav, "locate_search_bar",
                        lambda win: {"x": 406, "y": 217})
    monkeypatch.setattr(map_nav, "_ensure_minimap_putong_filter",
                        lambda *a, **k: (True, "已勾选"))
    monkeypatch.setattr(map_nav, "_find_special_entry_npc",
                        lambda *a, **k: (514, 547))
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    it = iter(start_results)
    monkeypatch.setattr(map_nav, "_wait_special_start",
                        lambda *a, **k: next(it, start_results[-1]))
    monkeypatch.setattr(map_nav, "_wait_special_enter",
                        lambda *a, **k: True)
    monkeypatch.setattr(map_nav.game_map, "close_maps",
                        lambda *a, **k: None)


def test_enter_special_by_npc_retries_click_when_not_started(monkeypatch):
    """首次点击只选中 NPC 未起步（地图仍开）→ 补点一次后起步。"""
    clicks, emits = [], []
    _patch_npc_entry_env(monkeypatch, clicks, emits, [False, True])
    monkeypatch.setattr(map_nav.game_map, "is_mini_map_open",
                        lambda win: True)
    monkeypatch.setattr(map_nav.game_map, "is_world_map_open",
                        lambda win: False)
    ok, info = map_nav._enter_special_by_npc(
        object(), "莲花洞", map_nav.special_map_entry("莲花洞"),
        emits.append)
    assert ok and "NPC 入口" in info
    assert clicks == [(514, 547), (514, 547)]
    assert any("补点一次" in m for m in emits)


def test_enter_special_by_npc_no_retry_when_map_closed(monkeypatch):
    """点击后地图已关闭（过图中）→ 不补点（补点会点到世界画面）。"""
    clicks, emits = [], []
    _patch_npc_entry_env(monkeypatch, clicks, emits, [False])
    monkeypatch.setattr(map_nav.game_map, "is_mini_map_open",
                        lambda win: False)
    monkeypatch.setattr(map_nav.game_map, "is_world_map_open",
                        lambda win: False)
    ok, info = map_nav._enter_special_by_npc(
        object(), "莲花洞", map_nav.special_map_entry("莲花洞"),
        emits.append)
    assert ok and clicks == [(514, 547)]


def test_enter_special_by_npc_fails_when_never_starts(monkeypatch):
    """地图始终开着且人物不动 → 补点到上限后失败（不无限连点）。"""
    clicks, emits = [], []
    _patch_npc_entry_env(monkeypatch, clicks, emits, [False])
    monkeypatch.setattr(map_nav.game_map, "is_mini_map_open",
                        lambda win: True)
    monkeypatch.setattr(map_nav.game_map, "is_world_map_open",
                        lambda win: False)
    ok, info = map_nav._enter_special_by_npc(
        object(), "莲花洞", map_nav.special_map_entry("莲花洞"),
        emits.append)
    assert not ok and "未起步" in info
    assert len(clicks) == map_nav.SPECIAL_CLICK_ATTEMPTS


# ---------------- 洞内按坐标步行（无小地图/不用角色名） ----------------

def test_special_settle_pos_returns_final_position(monkeypatch):
    """到达后等停稳：返回最终 HUD 位置（半路位置会被后续站位替代）。"""
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("火云洞", 74, 22))
    assert map_nav._special_settle_pos(
        None, ("火云洞", 74, 22)) == ("火云洞", 74, 22)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("火云洞", 72, 20))
    assert map_nav._special_settle_pos(
        None, ("火云洞", 74, 22)) == ("火云洞", 72, 20)


def test_walk_special_coordinate_endpoint_bias_correction(monkeypatch):
    """无首步校准：终点停稳误差超容差 → 按最终误差修正偏置补一步到位。"""
    map_nav._CANCEL.clear()
    B_true = (0.0, 24.7)
    delta = (4.0, 2.0)          # 模型映射与真实映射的偏差（不靠首步校准）
    B_model = (B_true[0] + delta[0], B_true[1] + delta[1])
    state = {"pos": ("莲花洞", 0, 10)}
    clicks = []

    def fake_click(win, px, py, button="primary"):
        assert button == "secondary"
        clicks.append((px, py))
        state["pos"] = (
            "莲花洞",
            int(round(map_nav.SPECIAL_WALK_SX * px + B_true[0])),
            int(round(map_nav.SPECIAL_WALK_SY * py + B_true[1])))

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "human_click", fake_click)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, target, pos: (
                            (pos[1] - B_model[0]) / map_nav.SPECIAL_WALK_SX,
                            (pos[2] - B_model[1]) / map_nav.SPECIAL_WALK_SY))
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: B_model)
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda win, before, emit:
                        (state["pos"], state["pos"] != before))
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 20, 20, emits.append, use_route=False)
    assert ok and "误差" in info
    assert abs(state["pos"][1] - 20) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert abs(state["pos"][2] - 20) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert any("修正偏置" in m for m in emits)


def test_walk_special_coordinate_converges_with_right_click(monkeypatch):
    """右键小步 + 每步用到达坐标重解 B：无角色名锚点也能收敛到 ±3 格。"""
    map_nav._CANCEL.clear()
    true_b = (12.0, 40.0)   # world = M * screen + B
    state = {"pos": ("莲花洞", 30, 30), "clicks": []}

    def fake_click(win, px, py, button="primary"):
        assert button == "secondary"       # 用户 2026-11-23：洞内小步用右键
        state["clicks"].append((px, py))
        wx = map_nav.SPECIAL_WALK_SX * px + true_b[0]
        wy = map_nav.SPECIAL_WALK_SY * py + true_b[1]
        state["pos"] = ("莲花洞", int(round(wx)), int(round(wy)))

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "human_click", fake_click)
    # 主循环先尝试用同一帧读位置（桩帧不是图像 → 返回 None 走重试函数）
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda win, before, emit:
                        (state["pos"], state["pos"] != before))
    # 相机位移跟踪：假世界固定 B（无相机滚动）
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: true_b)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 75, 21, emits.append, use_route=False)
    assert ok and "误差" in info
    assert abs(state["pos"][1] - 75) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert abs(state["pos"][2] - 21) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert state["clicks"]
    assert any("洞内步行到达" in m for m in emits)


def test_walk_special_coordinate_probe_all_blocked(monkeypatch):
    """探针无位移 → 明确失败（不空转/不无限连点）。"""
    map_nav._CANCEL.clear()

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("莲花洞", 30, 30))
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: None)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 75, 21, lambda m: None, use_route=False)
    assert not ok and "探针" in info


def test_name_hit_fuzzy():
    """名字标签 OCR 容忍 1 字误识别；短名字只认全等。"""
    assert map_nav._name_hit("20「可乐可口」", "可乐可口")
    assert map_nav._name_hit("20「同乐可口」", "可乐可口")   # 实测误识别
    assert not map_nav._name_hit("独孤剑客", "可乐可口")
    assert map_nav._name_hit("可乐可口", "可乐可口")
    assert not map_nav._name_hit("口乐", "可乐")            # 2 字名字不模糊
    assert map_nav._name_hit("「可乐」", "可乐")
    assert not map_nav._name_hit("", "可乐可口")


def test_name_label_anchor_zoom_crop_mapping(monkeypatch):
    """2x 裁剪 OCR 命中名字标签：rect 映射回窗口坐标（中心/上沿-3）。"""
    from PIL import Image
    img = Image.new("RGB", (1378, 831), (0, 0, 0))
    calls = {}

    def fake_zoom(crop):
        calls["size"] = crop.size
        # crop 是 2x 放大图：rect 坐标是放大图坐标
        return [("20「同乐可口」", [80, 40, 280, 70], 0.9)], None

    monkeypatch.setattr(map_nav.game_map, "ocr_zoom", fake_zoom)
    best = map_nav._name_label_anchor(img, "可乐可口",
                                      box=(100, 200, 500, 400))
    assert best == (0.9, 190, 217)
    assert calls["size"] == ((500 - 100) * 2, (400 - 200) * 2)

    # 置信度不足 → None
    monkeypatch.setattr(
        map_nav.game_map, "ocr_zoom",
        lambda crop: ([("20「可乐可口」", [0, 0, 40, 20], 0.3)], None))
    assert map_nav._name_label_anchor(img, "可乐可口",
                                      box=(100, 200, 500, 400)) is None


def test_special_model_anchor_knn_and_window_guard(monkeypatch):
    """描边锚点模型：K 近邻加权；窗口尺寸不符/覆盖太远返回 None。"""
    route = {
        "trace": [[10, 10], [20, 20]],
        "self_anchor": {
            "window": [1378, 831],
            "samples": [[10, 10, 700, 400], [12, 10, 704, 402],
                        [30, 30, 800, 500]],
        },
    }
    monkeypatch.setattr(map_nav, "special_map_route",
                        lambda name: route if name == "莲花洞" else None)

    class _Rect:
        width, height = 1378, 831

    class _W:
        rect = _Rect()

    ax, ay = map_nav._special_model_anchor(_W(), "莲花洞",
                                          ("莲花洞", 11, 10))
    assert abs(ax - 702) <= 3 and abs(ay - 401) <= 3
    # 附近没有样品（>12 格）→ None，退回名字锚点/探针
    assert map_nav._special_model_anchor(
        _W(), "莲花洞", ("莲花洞", 90, 50)) is None

    class _Rect2:
        width, height = 1280, 720

    class _W2:
        rect = _Rect2()

    # 窗口尺寸与采集不一致 → None（锚点是绝对屏幕像素）
    assert map_nav._special_model_anchor(
        _W2(), "莲花洞", ("莲花洞", 11, 10)) is None
    # 无该图/无锚点模型 → None
    assert map_nav._special_model_anchor(
        _W(), "平顶山", ("平顶山", 11, 10)) is None


def test_special_coord_screen_point(monkeypatch):
    """特殊地图坐标反查：锚点 + (目标-当前)/(SX,SY)，点击点上移 25px。"""
    monkeypatch.setattr(map_nav, "special_map_entry",
                        lambda name: {"name": name}
                        if name == "莲花洞" else None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("莲花洞", 38, 50))
    monkeypatch.setattr(map_nav, "_special_map_name_match",
                        lambda hud, target: hud == target)
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, name, pos: (712.0, 411.0))
    emit = []
    pt = map_nav.special_coord_screen_point(
        _Win(1378, 831), "莲花洞", 40, 48, emit=emit.append)
    # x: 712 + 2/0.048 = 753.7 → 754；y: 411 - 2/0.049 - 25 = 426.8 → 427
    assert pt == (754, 427)
    assert emit and "坐标反查" in emit[0]
    # map_name=None：按当前 HUD 地名自动匹配特殊地图（诊断路径）
    assert map_nav.special_coord_screen_point(
        _Win(1378, 831), None, 40, 48) == (754, 427)
    # 非特殊地图 / 锚点缺失 / 位置不在该图 / 目标反查点出窗 → None
    assert map_nav.special_coord_screen_point(
        _Win(1378, 831), "平顶山", 40, 48) is None
    assert map_nav.special_coord_screen_point(
        _Win(1378, 831), "莲花洞", 120, 48) is None  # x 超窗
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("波月洞", 38, 50))
    assert map_nav.special_coord_screen_point(
        _Win(1378, 831), "莲花洞", 40, 48) is None
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("莲花洞", 38, 50))
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, name, pos: None)
    assert map_nav.special_coord_screen_point(
        _Win(1378, 831), "莲花洞", 40, 48) is None


def test_walk_special_coordinate_uses_anchor_model_first(monkeypatch):
    """有描边锚点模型时直接标定 B，不调用名字 OCR / 探针。"""
    map_nav._CANCEL.clear()
    true_b = (12.0, 40.0)
    state = {"pos": ("莲花洞", 30, 30), "clicks": []}
    calls = {"ocr": 0, "probe": 0}

    def fake_click(win, px, py, button="primary"):
        assert button == "secondary"
        state["clicks"].append((px, py))
        wx = map_nav.SPECIAL_WALK_SX * px + true_b[0]
        wy = map_nav.SPECIAL_WALK_SY * py + true_b[1]
        state["pos"] = ("莲花洞", int(round(wx)), int(round(wy)))

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "human_click", fake_click)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda win, before, emit:
                        (state["pos"], state["pos"] != before))
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, target, pos: (
                            (pos[1] - true_b[0]) / map_nav.SPECIAL_WALK_SX,
                            (pos[2] - true_b[1]) / map_nav.SPECIAL_WALK_SY))
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: calls.__setitem__(
                            "ocr", calls["ocr"] + 1) or (700.0, 400.0))
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline:
                        calls.__setitem__(
                            "probe", calls["probe"] + 1) or true_b)
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 75, 21, emits.append, use_route=False)
    assert ok and "误差" in info
    assert abs(state["pos"][1] - 75) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert abs(state["pos"][2] - 21) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert calls == {"ocr": 0, "probe": 0}
    assert any("描边锚点模型" in m for m in emits)


def test_walk_special_coordinate_stops_within_tolerance(monkeypatch):
    """已在 ±3 格内：不点击，直接成功（用户 2026-11-23 确认容差）。"""
    map_nav._CANCEL.clear()
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda *a, **k: clicks.append(1))
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: ("莲花洞", 74, 22))
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda *a, **k: (("莲花洞", 74, 22), False))
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _W:
        def capture(self, force_activate=False):
            return object()

    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 75, 21, lambda m: None, use_route=False)
    assert ok and "已到达莲花洞" in info and clicks == []


def test_set_minimap_putong_filter_unchecks_when_checked(monkeypatch):
    """入口坐标寻路前取消「普通」（防止入口点附近 NPC 名吃掉点击）。"""
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.game_map, "minimap_filter_boxes",
                        _fake_filter_boxes([True, False]))
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _W:
        def capture(self, force_activate=False):
            return object()

    ok, info = map_nav._set_minimap_putong_filter(
        _W(), False, lambda m: None)
    assert ok and "已取消" in info
    assert clicks == [(411, 657)]   # 模板定位的第 3 个框中心


def test_set_minimap_putong_filter_no_row_no_click(monkeypatch):
    """筛选行未识别时绝不点击：旧偏移 (32,452) 落在筛选行下方的地图内容
    上，点击会打开世界地图盖住小地图，后续悬停标定全部失效（2026-11-24
    客栈跨图导航必现）。"""
    clicks = []
    monkeypatch.setattr(map_nav, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(map_nav.game_map, "minimap_filter_boxes",
                        lambda img: None)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)

    class _W:
        def capture(self, force_activate=False):
            return object()

    ok, info = map_nav._set_minimap_putong_filter(
        _W(), False, lambda m: None)
    assert not ok and "未识别" in info and clicks == []


def test_start_impl_unchecks_putong_for_world_maps(monkeypatch):
    """世界地图导航开图统一取消「普通」（用户 2026-11-24）：特殊地图 NPC
    入口兜底勾选后残留的状态，由任何普通导航开图时统一清除；层级图
    （龙窟/凤巢/大雁塔层）页面无该筛选行，不处理。"""
    _patch_start_env(monkeypatch)
    calls = []
    monkeypatch.setattr(
        map_nav, "_set_minimap_putong_filter",
        lambda win, want, emit: calls.append(want) or (True, "已取消"))
    ok, _info, _ctx = map_nav._start_impl(
        object(), "平顶山", 102, 42, lambda m: None)
    assert ok and calls == [False]

    calls.clear()
    monkeypatch.setattr(map_nav, "_read_pos",
                        lambda win: ("大雁塔四层", 1, 2))
    ok, _info, _ctx = map_nav._start_impl(
        object(), "大雁塔四层", 105, 36, lambda m: None)
    assert ok and calls == []


def test_special_frame_shift_measures_camera_scroll():
    """相位相关测相机位移：合成随机纹理平移 (20,15) 应读回同值。"""
    from PIL import Image
    rng = np.random.default_rng(20261123)
    arr = (rng.random((831, 1378)) * 255).astype(np.uint8)
    arr = np.stack([arr, arr, arr], axis=2)
    moved = np.roll(arr, shift=(15, 20), axis=(0, 1))
    s = map_nav._special_frame_shift(Image.fromarray(arr, "RGB"),
                                     Image.fromarray(moved, "RGB"))
    assert s is not None
    dx, dy, resp = s
    assert abs(dx - 20) < 1.5 and abs(dy - 15) < 1.5
    assert resp >= map_nav.SPECIAL_WALK_SHIFT_MIN_RESP


def test_special_apply_shift_updates_b():
    """B_new = B - M*shift（屏幕向上 = 世界 y 增大，符号不得写反）。"""
    B = (10.0, 20.0)
    B2 = map_nav._special_apply_shift(B, (100.0, -50.0))
    assert B2 == (10.0 - map_nav.SPECIAL_WALK_SX * 100.0,
                  20.0 - map_nav.SPECIAL_WALK_SY * -50.0)
    assert map_nav._special_apply_shift(B, None) == B
    assert map_nav._special_apply_shift(None, (1, 2)) is None


# ---------------- 特殊地图探图路网 ----------------

_ROUTE = {
    "center": [57, 26],
    "arms": {
        "1,-1": [[57, 26], [65, 18], [74, 9]],
        "-1,1": [[57, 26], [50, 33], [41, 41], [32, 49], [23, 57]],
        "-1,-1": [[57, 26], [49, 18], [40, 9]],
        "1,1": [[57, 26], [63, 31], [72, 39], [81, 47], [85, 55]],
    },
}


def test_special_route_waypoints_same_arm():
    """同臂：沿臂节点走到目标最近节点，最后追加目标坐标。"""
    wps = map_nav._special_route_waypoints(
        _ROUTE, ("莲花洞", 60, 24), 30, 56)
    assert wps[0] == (57, 26)
    assert wps[-1] == (30, 56)
    assert (50, 33) in wps and (41, 41) in wps and (23, 57) in wps


def test_special_route_waypoints_cross_arm_via_center():
    """跨臂：当前臂回中心，再沿目标臂走（不穿越空洞）。"""
    wps = map_nav._special_route_waypoints(
        _ROUTE, ("莲花洞", 70, 10), 30, 56)
    assert (74, 9) in wps and (65, 18) in wps
    assert (57, 26) in wps
    assert (23, 57) in wps
    assert wps[-1] == (30, 56)


def test_special_route_waypoints_off_route_returns_none():
    """目标离最近臂节点太远（不在十字臂上）→ 不按路网走。"""
    assert map_nav._special_route_waypoints(
        _ROUTE, ("莲花洞", 57, 26), 0, 0) is None


def test_special_map_route_role_data_priority(monkeypatch):
    """角色数据路网优先于内置表；中心/臂缺失视为无效。"""
    monkeypatch.setattr(
        map_nav, "_load_builtin_routes",
        lambda: {"maps": {"莲花洞": {"center": [1, 2],
                                     "arms": {"1,1": [[1, 2]]}}}})
    monkeypatch.setattr(
        map_nav, "_ROUTE_TABLE", None)
    from src.core import user_data
    monkeypatch.setattr(
        user_data, "read_special_map_routes",
        lambda role=None, path=None: {"maps": {"莲花洞": {
            "center": [57, 26], "arms": {"-1,1": [[57, 26]]}}}})
    route = map_nav.special_map_route("莲花洞")
    assert route["center"] == [57, 26]
    monkeypatch.setattr(user_data, "read_special_map_routes",
                        lambda role=None, path=None: {})
    route2 = map_nav.special_map_route("莲花洞")
    assert route2["center"] == [1, 2]
    assert map_nav.special_map_route("不存在") is None


def test_special_trace_boundary_inside_direct():
    """描边边界内：直接到目标（不再沿描边绕一圈）。"""
    route = {"boundary": [[0, 0], [10, 0], [10, 10], [0, 10]]}
    wps = map_nav._special_route_waypoints(
        route, ("莲花洞", 2, 2), 5, 5)
    assert wps == [(5, 5)]
    # 边界上（容差内）也算可走
    assert map_nav._special_route_waypoints(
        route, ("莲花洞", 2, 2), 10, 5) == [(10, 5)]


def test_special_trace_boundary_outside_clamped():
    """描边外：落到边界最近可走点（避免点进空洞）。"""
    route = {"boundary": [[0, 0], [10, 0], [10, 10], [0, 10]]}
    assert map_nav._special_route_waypoints(
        route, ("莲花洞", 2, 2), 15, 5) == [(10, 5)]
    wps = map_nav._special_route_waypoints(
        route, ("莲花洞", 2, 2), 15, 15)
    assert wps == [(10, 10)]


def test_special_trace_boundary_nonconvex_void():
    """非凸十字边界：臂内可走、两臂之间的空洞判为外部。"""
    plus = [[10, 0], [20, 0], [20, 10], [30, 10], [30, 20], [20, 20],
            [20, 30], [10, 30], [10, 20], [0, 20], [0, 10], [10, 10]]
    route = {"boundary": plus}
    # 上方臂内
    assert map_nav._special_route_waypoints(
        route, ("莲花洞", 15, 25), 15, 25) == [(15, 25)]
    # 右上空洞角（两臂之间）
    wps = map_nav._special_route_waypoints(
        route, ("莲花洞", 15, 25), 25, 25)
    assert wps != [(25, 25)]
    x, y = wps[0]
    assert x <= 20 or y <= 20


def test_special_trace_segment_crosses_void_uses_boundary_grid_route():
    """两端都在描边内、直线穿两臂间空洞 → 边界内栅格 A* 绕行。"""
    boundary = [[0, 0], [15, 0], [15, 10], [12, 10], [12, 3],
                [3, 3], [3, 10], [0, 10]]
    route = {"boundary": boundary,
             "trace": [[1, 8], [1, 1], [13, 1], [13, 8]]}
    assert map_nav._special_boundary_contains(boundary, 1, 8)
    assert map_nav._special_boundary_contains(boundary, 13, 8)
    # U 形两臂之间是空洞：直线 (1,8)→(13,8) 不全程在边界内
    assert not map_nav._special_segment_inside(boundary, (1, 8), (13, 8))
    wps = map_nav._special_route_waypoints(
        route, ("莲花洞", 1, 8), 13, 8)
    assert len(wps) > 1                      # 不再直线穿空洞
    for wx, wy in wps:                       # 途经点全部在描边内
        assert map_nav._special_boundary_contains(boundary, wx, wy)
    assert min(wy for _wx, wy in wps) <= 3   # 下到 U 形底部绕上去
    assert wps[-1] == (13, 8)
    # 同侧短线仍然直接走
    assert map_nav._special_route_waypoints(
        route, ("莲花洞", 1, 8), 1, 6) == [(1, 6)]


def test_special_segment_portal_avoidance():
    """传送光圈：两端在外时线段不得穿过；端点落在光圈内放行。"""
    boundary = [[0, 0], [20, 0], [20, 20], [0, 20]]
    portal = (10, 10)
    assert not map_nav._special_segment_inside(
        boundary, (1, 1), (19, 19), portal=portal)
    assert map_nav._special_segment_inside(
        boundary, (1, 1), (19, 1), portal=portal)      # 不经过光圈
    assert map_nav._special_segment_inside(
        boundary, (10, 10), (19, 19), portal=portal)   # 从光圈里走出
    assert map_nav._special_segment_inside(
        boundary, (1, 1), (10, 10), portal=portal)     # 目标就是光圈


def test_special_route_avoids_portal():
    """直线穿光圈 → 改走绕开光圈的边界栅格 A* 路线。"""
    boundary = [[0, 0], [20, 0], [20, 20], [0, 20]]
    route = {"boundary": boundary,
             "trace": [[1, 1], [19, 1], [19, 19], [1, 19]]}
    portal = (10, 10)
    wps = map_nav._special_route_waypoints(
        route, ("莲花洞", 1, 1), 19, 19, portal=portal)
    assert len(wps) > 1
    r = map_nav.SPECIAL_PORTAL_AVOID_R
    prev = (1, 1)
    for wp in wps:
        assert (wp[0] - 10) ** 2 + (wp[1] - 10) ** 2 > r * r
        assert map_nav._special_segment_inside(
            boundary, prev, wp, portal=portal)
        prev = wp


def test_walk_special_coordinate_direct_stuck_rescues_to_trace(monkeypatch):
    """直线导航长时间无进展（原地小幅抖动）→ 自动改走描边路网绕行。"""
    map_nav._CANCEL.clear()
    monkeypatch.setattr(map_nav, "SPECIAL_WALK_DIRECT_STUCK_TIME", -1.0)
    B_true = (0.0, 24.7)
    state = {"pos": ("莲花洞", 1, 1)}
    route = {"boundary": [[0, 0], [20, 0], [20, 20], [0, 20]],
             "trace": [[1, 1], [19, 1], [19, 19], [1, 19]]}

    def fake_click(win, px, py, button="primary"):
        wx = map_nav.SPECIAL_WALK_SX * px + B_true[0]
        wy = map_nav.SPECIAL_WALK_SY * py + B_true[1]
        # 只有沿描边路网（下边 y≈1 / 右边 x≈19）的点击才移动；
        # 沿直线对角的点击不动（模拟被局部障碍卡住、原地抖动）
        if abs(wy - 1) <= 2 or abs(wx - 19) <= 2:
            state["pos"] = ("莲花洞", int(round(wx)), int(round(wy)))

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "human_click", fake_click)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: B_true)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "_special_model_anchor",
                        lambda win, target, pos: None)
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda win, before, emit: (state["pos"], False))
    monkeypatch.setattr(map_nav, "special_map_route", lambda name: route)
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 19, 19, emits.append)
    assert ok and "误差" in info
    assert any("改走描边路网绕行" in m for m in emits)
    assert abs(state["pos"][1] - 19) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert abs(state["pos"][2] - 19) <= map_nav.SPECIAL_WALK_TOLERANCE


def test_special_route_validation_accepts_trace_only():
    """只有人工描边轨迹（无中心/臂）也是有效路网。"""
    assert map_nav._valid_special_route(
        {"trace": [[0, 0], [1, 1]]}) is not None
    assert map_nav._valid_special_route({"trace": [[0, 0]]}) is None


def test_walk_special_coordinate_visible_nav_skips_unreachable_waypoint(monkeypatch):
    """可见性导航：更远的途经点在屏幕内就直接点它，跳过中间不可达的点。"""
    map_nav._CANCEL.clear()
    B_true = (0.0, 24.7)
    state = {"pos": ("莲花洞", 0, 10)}
    clicks = []

    def fake_click(win, px, py, button="primary"):
        clicks.append((px, py))
        wx = map_nav.SPECIAL_WALK_SX * px + B_true[0]
        wy = map_nav.SPECIAL_WALK_SY * py + B_true[1]
        # 只有点到终目标 (10,10) 附近才移动；中间点 (5,10) 不可直达
        if abs(wx - 10) <= 0.8 and abs(wy - 10) <= 0.8:
            state["pos"] = ("莲花洞", int(round(wx)), int(round(wy)))

    class _W:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "human_click", fake_click)
    monkeypatch.setattr(map_nav.game_map, "read_map_position",
                        lambda win, img=None: None)
    monkeypatch.setattr(map_nav, "_special_read_pos",
                        lambda win: state["pos"])
    monkeypatch.setattr(map_nav, "_special_probe",
                        lambda win, target, emit, deadline: B_true)
    monkeypatch.setattr(map_nav, "_special_self_anchor",
                        lambda win, emit: None)
    monkeypatch.setattr(map_nav, "_special_frame_shift",
                        lambda img0, img1: None)
    monkeypatch.setattr(map_nav, "_special_wait_step",
                        lambda win, before, emit:
                        (state["pos"], state["pos"] != before))
    monkeypatch.setattr(map_nav, "special_map_route",
                        lambda name: {"center": [0, 10],
                                      "arms": {"1,0": [[0, 10], [5, 10],
                                                       [10, 10]]}})
    monkeypatch.setattr(map_nav, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(map_nav.time, "sleep", lambda s: None)
    emits = []
    # verbose=True：本例断言逐步点击明细（「途经点 (10,10)」），生产默认不输出
    ok, info = map_nav._walk_special_coordinate(
        _W(), "莲花洞", 10, 10, emits.append, verbose=True)
    assert ok and "误差" in info
    assert abs(state["pos"][1] - 10) <= map_nav.SPECIAL_WALK_TOLERANCE
    assert abs(state["pos"][2] - 10) <= map_nav.SPECIAL_WALK_TOLERANCE
    # 首点直接选最远可见的 (10,10)（无首步校准，屏幕 ≈208,300），跳过 (5,10)
    assert clicks and abs(clicks[0][0] - 208) <= 6 and abs(clicks[0][1] - 300) <= 6
    assert any("途经点 (10,10)" in m for m in emits)
