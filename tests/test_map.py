"""map.py 纯逻辑与模板匹配快速接口测试（合成帧 + 仓库内真实模板）。"""
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.system import R

from src.device import map as game_map

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


def test_best_match_multi_np_two_templates():
    """同帧多模板取分（一次灰度转换）：命中模板返回中心，未命中低分。"""
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    res = game_map.best_match_multi_np(frame, ("fashu.png", "fangyu.png"))
    score, (cx, cy) = res["fashu.png"]
    assert score >= 0.9
    assert (cx, cy) == (300 + tpl.shape[1] // 2, 600 + tpl.shape[0] // 2)
    assert res["fangyu.png"][0] < 0.9


def test_best_match_multi_np_region_and_missing():
    """条带限定（不回退全帧）与模板缺失（0 分）。"""
    tpl = _tpl("fashu.png")
    frame = paste_template(make_bgr_frame(), tpl, 300, 600)
    res = game_map.best_match_multi_np(frame, ("fashu.png",),
                                       region=(0, 0, 200, 200))
    assert res["fashu.png"][0] < 0.9
    res = game_map.best_match_multi_np(frame, ("no_such_icon.png",))
    assert res["no_such_icon.png"] == (0.0, (0, 0))


# ---- 模板缓存 ----

def test_load_template_cache_identity():
    a = game_map._load_template("fashu.png")
    b = game_map._load_template("fashu.png")
    assert a is not None and a is b  # 第二次命中缓存（同一对象）


# ---- 世界地图标签全表（按地图名导航用）----

def test_world_map_labels_templates_exist():
    """WORLD_MAP_LABELS 全部模板文件存在（世界地图标签素材齐备）。"""
    root = Path(__file__).resolve().parents[1] / "res" / "img"
    missing = [icon for icon in game_map.WORLD_MAP_LABELS.values()
               if not (root / icon).exists()]
    assert missing == []
    assert len(game_map.WORLD_MAP_LABELS) >= 50


def test_resolve_world_map_name():
    """地图名解析：精确 → 繁体/形近归一 → 相似度兜底；未收录返回 None。"""
    assert game_map.resolve_world_map_name("大唐边境") == "大唐边境"
    assert game_map.resolve_world_map_name("龍宫") == "龙宫"
    assert game_map.resolve_world_map_name("蓬菜") == "蓬莱"
    assert game_map.resolve_world_map_name("长安东") == "长安东"
    assert game_map.resolve_world_map_name("不存在的地") is None
    assert game_map.resolve_world_map_name("") is None


# ---- 关闭地图：确认等待（动画瞬态不点击，2026-11-15 玄枢轶闻跨地图传送） ----
# 跨地图传送时小地图随传送自动关闭，关闭动画期间仍能双帧匹配到渐隐帧；
# 此时点击关闭按钮会落到游戏世界取消寻路、把人物带走（玄枢轶闻实测：
# 点了关闭的 5 个跨地图轮全失败、没点的 2 轮全成功）→ 关小地图对齐
# 世界地图，带 1s 确认等待，复检消失即不点击。

class _Clock:
    """可控时钟：sleep 推进虚拟时间，time 返回虚拟时间。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _FrameWin:
    """capture 返回固定合成帧的窗口桩（close_map_if_open 只取帧给匹配）。"""

    def __init__(self):
        self.frame = np.zeros((20, 20, 3), dtype=np.uint8)

    def capture(self, force_activate=False):
        return self.frame


def _patch_close_env(monkeypatch, hits):
    """装配 close_map_if_open 依赖：match_frame_np 按序返回 hits（None=未命中）。"""
    monkeypatch.setattr(game_map, "time", _Clock())
    seq = list(hits)

    def fake_match(frame, icon, region=None, fallback=True):
        return seq.pop(0) if seq else None

    monkeypatch.setattr(game_map, "match_frame_np", fake_match)
    monkeypatch.setattr(game_map, "log", lambda *a, **k: None)
    clicks = []

    def fake_click(win, x, y, button="primary"):
        clicks.append((x, y, button))

    monkeypatch.setattr(game_map, "human_click", fake_click)
    return clicks


def test_close_mini_map_transient_no_click(monkeypatch):
    """双帧命中但确认等待后复检消失（传送/关闭动画瞬态）→ 不点击。"""
    clicks = _patch_close_env(monkeypatch, [(1111, 227), (1111, 227), None])
    ok, info = game_map.close_mini_map(_FrameWin())
    assert ok is True and "自动关闭" in info
    assert clicks == []  # 没有点到世界


def test_close_mini_map_still_open_clicks(monkeypatch):
    """确认等待后仍打开（真开着）→ 点击模板位置，复检消失即关闭。"""
    clicks = _patch_close_env(monkeypatch,
                              [(1111, 227), (1111, 227), (1111, 227), None])
    ok, info = game_map.close_mini_map(_FrameWin())
    assert ok is True and "关闭" in info
    assert clicks == [(1111, 227, "primary")]


# ---- 地图打开失败诊断帧（2026-11-16 用户要求：失败瞬间截帧） ----

class _DumpWin:
    """capture 返回固定 PIL 帧（诊断帧落盘用）。"""

    def __init__(self):
        from PIL import Image
        self.img = Image.fromarray(np.zeros((831, 1378, 3), dtype=np.uint8))

    def capture(self, force_activate=False):
        return self.img


def test_dump_failure_frame_writes_and_rate_limits(tmp_path, monkeypatch):
    """失败现场帧落盘；限频窗口内不重复落盘（防反复失败刷盘）。"""
    win = _DumpWin()
    monkeypatch.setattr(game_map, "_FAIL_DUMP_DIR", tmp_path)
    monkeypatch.setattr(game_map, "_last_fail_dump_at", 0.0)
    monkeypatch.setattr(game_map, "log", lambda *a, **k: None)
    game_map._dump_failure_frame(win, "city_open")
    assert len(list(tmp_path.glob("diag_map_*_city_open.png"))) == 1
    game_map._dump_failure_frame(win, "city_open")  # 限频内
    assert len(list(tmp_path.glob("diag_map_*.png"))) == 1


def test_open_mini_map_city_dumps_frame_on_failure(monkeypatch):
    """城市图标点击后小地图未打开 → 失败现场落盘（2026-11-16 排查依据）。

    当天 10:18 修理链路：世界地图在场、洛阳城标签位置与缓存一致，但
    点击城市图标两次（间隔 3s）都没进详情图，仅凭日志无法还原现场。
    """
    calls = []
    monkeypatch.setattr(game_map, "time", _Clock())
    monkeypatch.setattr(game_map, "open_world_map",
                        lambda w: (True, "世界地图已打开"))
    monkeypatch.setattr(game_map.app_utils, "high_perf_click",
                        lambda *a, **k: True)
    monkeypatch.setattr(game_map, "is_mini_map_open", lambda w: False)
    monkeypatch.setattr(game_map, "find_template", lambda w, icon: (667, 627))
    monkeypatch.setattr(game_map, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(game_map.app_utils, "fixed_pos_set",
                        lambda *a, **k: None)
    monkeypatch.setattr(game_map, "log", lambda *a, **k: None)
    monkeypatch.setattr(game_map, "_dump_failure_frame",
                        lambda w, tag: calls.append(tag))
    ok, info = game_map.open_mini_map_city(
        _FrameWin(), "shijie_luoyang.png", "洛阳城")
    assert ok is False and "小地图未打开" in info
    assert calls == ["city_open"]
