"""team.py：队伍面板成员区域扫描（2026-11-05 重写：固定 5 区域）。

验证点：
- 成员区域几何：标志模板 rect 锚定（右缘+15/顶缘+15，124x265，间隔 20）
- 空位模板命中即截断：首空位之后不再解析，人数=首空位序号
- 空位模板判定有人但 OCR 读不到 / 空位后仍有内容 → 可疑，重截再扫
- 模板缺失时 OCR 内容兜底判空（无种族且无等级）
- 第 1 区域金色选中态：OCR 读不到种族时字形库兜底
- 种族匹配限竖排文字框（高≥宽），横向名字/帮派行不误配
"""
import numpy as np
import pytest
from PIL import Image

from src import team

REGIONS = team._member_regions((326, 305, 358, 450))
EXPECTED_REGIONS = [
    (373, 320, 497, 585),
    (517, 320, 641, 585),
    (661, 320, 785, 585),
    (805, 320, 929, 585),
    (949, 320, 1073, 585),
]


def _dummy_img():
    return Image.new("RGB", (1378, 831))


def _np_dummy():
    return np.zeros((831, 1378, 3), dtype=np.uint8)


# ---- 几何 ----

def test_member_regions_geometry():
    assert team._member_regions((326, 305, 358, 450)) == EXPECTED_REGIONS
    assert len(team._member_regions((0, 0, 32, 145))) == team.MEMBER_REGION_COUNT
    first = team._member_regions((0, 0, 32, 145))[0]
    assert first == (47, 15, 171, 280)  # 右缘+15 / 顶缘+15 / 124x265


# ---- 区域扫描逻辑（OCR 与空位模板 monkeypatch，纯逻辑验证） ----

def _member(race, lv):
    return lambda img_np, img, rect, idx, glyphs: (race, lv, False)


def test_scan_stops_at_first_empty_template(monkeypatch):
    monkeypatch.setattr(team, "_region_is_empty",
                        lambda img, rect: rect[0] >= EXPECTED_REGIONS[2][0])
    monkeypatch.setattr(team, "_parse_member_region", _member("男人", "飞升188"))
    members, count, suspicious, changed = team._scan_member_regions(
        _np_dummy(), _dummy_img(), REGIONS, {})
    assert count == 2
    assert len(members) == 2
    assert not suspicious
    assert not changed


def test_scan_suspicious_content_after_empty_template(monkeypatch):
    # 空位之后又有成员：数据渲染中，须重截
    monkeypatch.setattr(team, "_region_is_empty",
                        lambda img, rect: rect[0] == EXPECTED_REGIONS[1][0])
    monkeypatch.setattr(team, "_parse_member_region", _member("男人", "飞升188"))
    members, count, suspicious, _c = team._scan_member_regions(
        _np_dummy(), _dummy_img(), REGIONS, {})
    assert suspicious
    assert count == 1  # 已解析人数（首空位序号）


def test_scan_template_says_member_but_ocr_empty(monkeypatch):
    # 空位模板确认有人但 OCR 读不到任何信息：可疑（渲染中）
    monkeypatch.setattr(team, "_region_is_empty", lambda img, rect: False)
    monkeypatch.setattr(team, "_parse_member_region",
                        lambda img_np, img, rect, idx, glyphs: (None, None, False))
    members, count, suspicious, _c = team._scan_member_regions(
        _np_dummy(), _dummy_img(), REGIONS, {})
    assert suspicious
    assert count == 0


def test_scan_ocr_fallback_empty_stops_and_probes(monkeypatch):
    # 模板缺失（None）：区域 1/2 有成员、区域 3 OCR 空 → 抽查区域 4 也空 → 2 人
    calls = []
    def parse(img_np, img, rect, idx, glyphs):
        calls.append(rect[0])
        if rect[0] < EXPECTED_REGIONS[2][0]:
            return ("男人", "飞升188", False)
        return (None, None, False)

    monkeypatch.setattr(team, "_region_is_empty", lambda img, rect: None)
    monkeypatch.setattr(team, "_parse_member_region", parse)
    members, count, suspicious, _c = team._scan_member_regions(
        _np_dummy(), _dummy_img(), REGIONS, {})
    assert count == 2
    assert len(members) == 2
    assert not suspicious
    # 解析了区域 1、2、3（判空）与抽查区域 4
    assert calls == [r[0] for r in EXPECTED_REGIONS[:4]]


def test_scan_ocr_fallback_suspicious_content_after_ocr_empty(monkeypatch):
    def parse(img_np, img, rect, idx, glyphs):
        if rect[0] == EXPECTED_REGIONS[1][0]:
            return (None, None, False)      # 区域 2 OCR 漏检（判空）
        return ("男人", "飞升188", False)   # 区域 1/3 有成员
    monkeypatch.setattr(team, "_region_is_empty", lambda img, rect: None)
    monkeypatch.setattr(team, "_parse_member_region", parse)
    members, count, suspicious, _c = team._scan_member_regions(
        _np_dummy(), _dummy_img(), REGIONS, {})
    assert suspicious  # 空位后抽查出内容 → 重截
    assert count == 1


# ---- 单区域解析 ----

def _fake_items(rows):
    """构造 ocr_region 返回值 [(txt, (l,t,r,b), score), ...]。"""
    return [(txt, rect, score) for txt, rect, score in rows], None


def test_parse_member_region_race_aspect_filter(monkeypatch):
    rows = [
        ("2016烟雨斜阳PK之星", (370, 509, 514, 531), 1.0),   # 帮派名（宽行）
        ("男人帮", (400, 520, 480, 540), 1.0),               # 横向含种族词
        ("男仙", (489, 321, 509, 360), 1.0),                 # 竖排种族框
        ("飞升188级", (408, 556, 478, 579), 1.0),
    ]
    monkeypatch.setattr(team.game_map, "ocr_region",
                        lambda img, region: _fake_items(rows))
    monkeypatch.setattr(team, "_harvest_glyphs",
                        lambda img_np, race_items, glyphs: False)
    race, lv, _c = team._parse_member_region(
        _np_dummy(), _dummy_img(), (373, 320, 497, 585), 1, {})
    assert race == "男仙"
    assert lv == "飞升188"


def test_parse_member_region_first_region_gold_glyph_fallback(monkeypatch):
    # 区域 1 OCR 读不到种族（金色选中态）→ 字形库兜底
    monkeypatch.setattr(team.game_map, "ocr_region",
                        lambda img, region: _fake_items(
                            [("飞升188级", (408, 556, 478, 579), 1.0)]))
    monkeypatch.setattr(team, "_match_glyph",
                        lambda img_np, glyphs, rect: ("男仙", 0.92))
    race, lv, _c = team._parse_member_region(
        _np_dummy(), _dummy_img(), (373, 320, 497, 585), 0, {"男仙": np.ones((23, 46), np.uint8)})
    assert race == "男仙"
    assert lv == "飞升188"


def test_parse_member_region_glyph_rect_anchored_to_region(monkeypatch):
    seen = []
    monkeypatch.setattr(team.game_map, "ocr_region",
                        lambda img, region: _fake_items([]))
    monkeypatch.setattr(team, "_match_glyph",
                        lambda img_np, glyphs, rect: seen.append(rect) or (None, 0.0))
    team._parse_member_region(_np_dummy(), _dummy_img(), (373, 320, 497, 585), 0, {})
    l, t, r, b = seen[0]
    assert (l, t, r, b) == (373 + 114, 320 + 0, 373 + 138, 320 + 41)


# ---- 面板整体扫描 ----

def test_scan_panel_img_no_banner(monkeypatch):
    monkeypatch.setattr(team, "_find_banner_rect", lambda img: (0.0, None))
    members, count, ok = team._scan_panel_img(_dummy_img())
    assert not ok
    assert members == [] and count == 0


def test_analyze_panel_frame_retry_on_suspicious(monkeypatch):
    frames = [
        ([("男人", "飞升188")], 1, False),   # 可疑（渲染中）
        ([("男人", "飞升188"), ("男魔", "飞升169")], 2, True),
    ]
    monkeypatch.setattr(team, "_scan_panel_img", lambda img: frames.pop(0))
    monkeypatch.setattr(team.time, "sleep", lambda _s: None)
    win = type("W", (), {"capture": lambda self, force_activate=False: _dummy_img()})()
    ok, members, count = team._analyze_panel_frame(win, _dummy_img())
    assert ok
    assert count == 2 and len(members) == 2


def test_scan_panel_img_does_not_write_glyphs_when_no_change(monkeypatch, tmp_path):
    saved = []
    monkeypatch.setattr(team, "_find_banner_rect",
                        lambda img: (1.0, (326, 305, 358, 450)))
    monkeypatch.setattr(team, "_load_glyphs", lambda: {})
    monkeypatch.setattr(team, "_save_glyphs", lambda glyphs: saved.append(glyphs))
    monkeypatch.setattr(team, "_scan_member_regions",
                        lambda img_np, img, regions, glyphs: ([], 0, False, False))
    members, count, ok = team._scan_panel_img(_dummy_img())
    assert ok and members == [] and count == 0
    assert saved == []
