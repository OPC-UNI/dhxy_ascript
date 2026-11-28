# -*- coding: utf-8 -*-
"""300环 画符（灵心巧手）：窗口锚点、符纸几何、笔画提取方向与 wait_handler 接入。"""
import cv2
import numpy as np
from PIL import Image
from ascript.windows.system import R

from src.minigames import huafu
from src.tasks import sanbaihuan


def _blank_frame():
    return np.zeros((831, 1378, 3), np.uint8)


def test_locate_window_by_redraw_anchor():
    # 把不透明「重画」按钮模板贴到空白帧的带区内 → 命中
    tpl = Image.open(R.img(huafu.ANCHOR_ICON)).convert("RGB")
    frame = Image.fromarray(_blank_frame())
    cx, cy = 593, 624
    l = cx - tpl.width // 2
    t = cy - tpl.height // 2
    frame.paste(tpl, (l, t))
    assert huafu.is_ready(frame)
    assert huafu._locate(huafu._bgr(frame)) == (cx, cy)
    assert not huafu.is_ready(Image.fromarray(_blank_frame()))


def test_paper_geometry_roundtrip():
    anchor = (593, 624)
    assert huafu._paper_rect(anchor) == (528, 258, 679, 603)
    assert huafu._paper_rect(anchor, right=True) == (708, 258, 859, 603)


def test_stroke_paths_direction_from_thick_end():
    # 合成符纸：黄底 + 左侧粗、右侧细的黑色笔画 + 红色印章（应被排除）
    frame = _blank_frame()
    l, t, r, b = 528, 258, 679, 603
    frame[t:b, l:r] = (200, 180, 80)  # 黄纸（BGR）
    # 笔画：左端粗（14px 高）、右端细（4px 高）→ 起笔应在左端
    for i in range(80):
        x = l + 30 + i
        half = max(2, int(7 - i * 5 / 80))
        frame[t + 160 - half:t + 160 + half, x] = (10, 10, 10)
    # 红色印章（右侧上方）：不应被当成笔迹
    frame[t + 40:t + 80, l + 85:l + 125] = (40, 40, 180)
    paths = huafu._stroke_paths(Image.fromarray(frame), (l, t, r, b))
    assert len(paths) == 1
    pts, bbox_x = paths[0]
    # 起笔端在粗端（左侧）
    assert pts[0][0] < pts[-1][0]
    assert abs(pts[0][1] - (t + 160)) <= 6
    assert abs(pts[-1][0] - (l + 109)) <= 6


def test_dot_starts_at_sharp_entry_not_thick_belly():
    """点：左上尖锋入笔、右下顿笔成肚——尖端才是起笔（用户 2026-11-27）。"""
    frame = _blank_frame()
    l, t, r, b = 528, 258, 679, 603
    frame[t:b, l:r] = (200, 180, 80)  # 黄纸（BGR）
    # 泪滴形「点」：左上尖锋 (l+30, t+120) → 右下圆肚（半径约 11）
    poly = np.array([[l + 30, t + 120], [l + 40, t + 122], [l + 49, t + 127],
                     [l + 55, t + 134], [l + 54, t + 143], [l + 46, t + 146],
                     [l + 38, t + 141], [l + 32, t + 131]], np.int32)
    cv2.fillPoly(frame, [poly], (10, 10, 10))
    paths = huafu._stroke_paths(Image.fromarray(frame), (l, t, r, b))
    assert len(paths) == 1
    pts, _bx = paths[0]
    # 起笔在尖端（左上），而不是圆肚（右下）
    assert abs(pts[0][0] - (l + 31)) <= 6 and abs(pts[0][1] - (t + 121)) <= 6
    assert pts[-1][0] > pts[0][0] and pts[-1][1] > pts[0][1]


def test_uniform_width_stroke_starts_upper_left():
    """两端粗细相当：按左上端起笔（横左→右、竖上→下，方向确定不抖动）。"""
    frame = _blank_frame()
    l, t, r, b = 528, 258, 679, 603
    frame[t:b, l:r] = (200, 180, 80)
    # 等宽横向长条 80x8
    frame[t + 160:t + 168, l + 30:l + 110] = (10, 10, 10)
    paths = huafu._stroke_paths(Image.fromarray(frame), (l, t, r, b))
    assert len(paths) == 1
    pts, _bx = paths[0]
    assert abs(pts[0][0] - (l + 30)) <= 4, "等宽横画应从左端起笔"


def test_simplify_keeps_endpoints():
    pts = [(0, 0), (1, 0), (2, 0), (3, 0), (10, 0)]
    out = huafu._simplify(pts, step=3)
    assert out[0] == (0, 0) and out[-1] == (10, 0)
    assert len(out) < len(pts)


def test_wait_handler_runs_huafu(monkeypatch):
    calls = []
    monkeypatch.setattr(sanbaihuan.huafu_mini, "is_ready", lambda bgr: True)
    monkeypatch.setattr(sanbaihuan.huafu_mini, "play",
                        lambda win, prefix="": calls.append(prefix)
                        or (True, "ok", False))
    monkeypatch.setattr(sanbaihuan, "_txwz_tick", lambda *a: False)
    monkeypatch.setitem(sanbaihuan._HUAFU_STATE, "pending", False)
    frame = Image.fromarray(_blank_frame())
    assert sanbaihuan._wait_handler(None, [], None, frame)
    assert calls, "画符窗口在场时应调用 play"
    assert not sanbaihuan._HUAFU_STATE["pending"]  # 成功不进入重开等待


def test_close_window_clicks_x_and_confirms(monkeypatch):
    """画 2 次不通过：锚点 + X 合成帧 → 点 X 位置并按锚点确认关闭。"""
    tpl = Image.open(R.img(huafu.ANCHOR_ICON)).convert("RGB")
    x_tpl = Image.open(R.img(huafu.CLOSE_ICON)).convert("RGB")
    frame = Image.fromarray(_blank_frame())
    anchor = (593, 624)
    frame.paste(tpl, (anchor[0] - tpl.width // 2, anchor[1] - tpl.height // 2))
    xc = (anchor[0] + huafu.CLOSE_REL[0], anchor[1] + huafu.CLOSE_REL[1])
    frame.paste(x_tpl, (xc[0] - x_tpl.width // 2, xc[1] - x_tpl.height // 2))
    clicked = []

    class _Win:
        def capture(self, force_activate=False):
            return frame
    monkeypatch.setattr(huafu.app_utils, "human_click",
                        lambda win, x, y: clicked.append((x, y)))
    monkeypatch.setattr(huafu, "_wait_window_gone", lambda win, t: True)
    assert huafu._close_window(_Win())
    assert clicked, "命中 X 后必须点击"
    assert abs(clicked[0][0] - xc[0]) <= 2 and abs(clicked[0][1] - xc[1]) <= 2
    # 窗口不在场（空白帧）：按已关闭返回，不点击
    clicked.clear()

    class _Gone:
        def capture(self, force_activate=False):
            return Image.fromarray(_blank_frame())
    assert huafu._close_window(_Gone())
    assert not clicked


def test_wait_handler_restart_resumes_tracking(monkeypatch):
    """画符 2 次不通过返回待重走：关窗期间重走任务链接（退避后不重复点）。"""
    picks = iter([True, False, False, False])
    monkeypatch.setattr(sanbaihuan.huafu_mini, "is_ready",
                        lambda bgr: next(picks, False))
    monkeypatch.setattr(sanbaihuan.huafu_mini, "play",
                        lambda win, prefix="": (False, "已关闭制符窗口", True))
    monkeypatch.setattr(sanbaihuan, "_txwz_tick", lambda *a: False)
    monkeypatch.setattr(sanbaihuan.app_utils, "mark_track_walk", lambda: None)
    # 静默门（2026-11-28）：本用例只验证「等待期不占帧 + 退避重走」契约，
    # 寻路/对话判定由 test_sanbaihuan 的重走静默门用例单独覆盖
    monkeypatch.setattr(sanbaihuan._RESTART_WALK, "walking",
                        lambda win=None, img=None: False)
    monkeypatch.setitem(sanbaihuan._HUAFU_STATE, "pending", False)
    monkeypatch.setitem(sanbaihuan._HUAFU_STATE, "restarts", 0)
    tracks = []
    monkeypatch.setattr(sanbaihuan.task_tracker, "track_task",
                        lambda win, title, **kw: tracks.append(title) or (True, "ok"))
    frame = Image.fromarray(_blank_frame())
    assert sanbaihuan._wait_handler(None, [], None, frame)  # 检测窗口 → play
    assert sanbaihuan._HUAFU_STATE["pending"]
    # 等待期不得占住本帧（否则 follow 内置对话链点不了「交给我吧」）
    assert not sanbaihuan._wait_handler(None, [], None, frame)  # 下一轮询重走
    assert tracks == [sanbaihuan.TASK_TITLE]
    sanbaihuan._wait_handler(None, [], None, frame)         # 退避期不重复点击
    assert len(tracks) == 1


def test_locate_falls_back_to_full_frame_outside_band(monkeypatch):
    """窗口被拖到带区外：限频全帧兜底仍能定位（用户可拖动窗口）。"""
    tpl = Image.open(R.img(huafu.ANCHOR_ICON)).convert("RGB")
    frame = Image.fromarray(_blank_frame())
    cx, cy = 300, 300  # ANCHOR_BAND (447,185,930,705) 之外
    frame.paste(tpl, (cx - tpl.width // 2, cy - tpl.height // 2))
    monkeypatch.setattr(huafu, "_last_full_check", 0.0)
    assert huafu._locate(huafu._bgr(frame)) == (cx, cy)
