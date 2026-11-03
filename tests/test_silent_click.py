"""静默点击模块单测（纯函数/投递序列，不触真实游戏与鼠标）。

验证点：
- _diff_ratio 帧差统计：相同帧 0、阈值过滤（≤32 不计变化）、占比/均值正确
- click_target 目标选择：优先可见 Tab 子窗口；无子窗口回退主窗口；无句柄返回 None
- send_silent_click 投递序列：MOVEMOVE→LBUTTONDOWN→LBUTTONUP、中心坐标、
  lParam 打包、wParam 按键位、消息全部投给目标句柄（不移动鼠标/不激活窗口）
- 坐标空间：x/y 与 human_click 同空间（win.rect 相对），经屏幕换算为
  目标窗口客户区坐标
"""
import ctypes
from ctypes import wintypes

import numpy as np
import pytest

from src import silent_click
from src.silent_click import (MK_LBUTTON, MK_RBUTTON, WM_LBUTTONDOWN, WM_LBUTTONUP,
                              WM_MOUSEMOVE, WM_RBUTTONDOWN, WM_RBUTTONUP)

_RECT_PTR = ctypes.POINTER(wintypes.RECT)


class _FakeWin:
    """click_target/send_silent_click 所需的最小窗口替身（hwnd + rect）。"""

    def __init__(self, hwnd, rect=(10, 5, 1388, 836)):
        self.hwnd = hwnd
        self.rect = type("Rect", (), {"left": rect[0], "top": rect[1]})()


class _FakeWinNoRect:
    """无 rect 属性的窗口替身（回退用目标窗口矩形做坐标基准）。"""

    def __init__(self, hwnd):
        self.hwnd = hwnd


class _FakeUser32:
    """可编程 user32 替身：记录投递消息序列，客户区/窗口矩形可配置。"""

    def __init__(self, client=(1366, 768), winrect=(100, 50, 1466, 818)):
        self.client = client
        self.winrect = winrect
        self.msgs = []

    def GetClientRect(self, hwnd, rect_ptr):
        rect = ctypes.cast(rect_ptr, _RECT_PTR).contents
        rect.right, rect.bottom = self.client
        return True

    def GetWindowRect(self, hwnd, rect_ptr):
        rect = ctypes.cast(rect_ptr, _RECT_PTR).contents
        rect.left, rect.top, rect.right, rect.bottom = self.winrect
        return True

    def SendMessageW(self, hwnd, msg, wparam, lparam):
        self.msgs.append((hwnd, msg, wparam, lparam))
        return 0

    def GetForegroundWindow(self):
        return 0

    def GetClassNameW(self, hwnd, buf, n):
        buf.value = "Win32Window"
        return len(buf.value)


# ---- 帧差统计 ----

def test_diff_ratio_identical_frames():
    a = np.zeros((10, 10, 3), dtype=np.int16)
    pct, mean = silent_click._diff_ratio(a, a)
    assert pct == 0.0 and mean == 0.0


def test_diff_ratio_threshold_filters_small_changes():
    # 全帧差 16（≤32 阈值）：不算变化像素，均值差仍为 16
    a = np.zeros((10, 10, 3), dtype=np.int16)
    b = a + 16
    pct, mean = silent_click._diff_ratio(a, b)
    assert pct == 0.0
    assert abs(mean - 16.0) < 1e-6


def test_diff_ratio_half_changed():
    a = np.zeros((10, 10, 3), dtype=np.int16)
    b = a.copy()
    b[:5] = 100
    pct, mean = silent_click._diff_ratio(a, b)
    assert pct == 50.0
    assert abs(mean - 50.0) < 1e-6


# ---- 点击目标选择 ----

def test_click_target_prefers_visible_tab(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows",
                        lambda w: [{"hwnd": 0x111, "visible": False},
                                   {"hwnd": 0x222, "visible": True}])
    assert silent_click.click_target(_FakeWin(0x123)) == 0x222


def test_click_target_falls_back_to_main_hwnd(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    assert silent_click.click_target(_FakeWin(0x123)) == 0x123


def test_click_target_no_hwnd(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    assert silent_click.click_target(_FakeWin(0)) is None


def test_click_target_enum_failure_falls_back(monkeypatch):
    def boom(_w):
        raise RuntimeError("EnumChildWindows 失败")
    monkeypatch.setattr(silent_click.game, "list_tab_windows", boom)
    assert silent_click.click_target(_FakeWin(0x123)) == 0x123


# ---- 消息投递序列 ----

def test_send_silent_click_center_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_click(_FakeWin(0x123))
    # 未给坐标：目标窗口客户区中心；屏幕点 = 目标窗口矩形左上 + 中心
    assert info["point"] == (683, 384)
    assert info["screen_point"] == (100 + 683, 50 + 384)
    assert info["client"] == (1366, 768)
    # 序列：移到点击点 → 按下 → 抬起 → 移到左下角中性区（防悬停高亮残留）
    assert [m[1] for m in fake.msgs] == [WM_MOUSEMOVE, WM_LBUTTONDOWN,
                                         WM_LBUTTONUP, WM_MOUSEMOVE]
    assert [m[2] for m in fake.msgs] == [0, MK_LBUTTON, 0, 0]
    assert [m[3] for m in fake.msgs] == [(384 << 16) | 683] * 3 + [((768 - 20) << 16) | 20]
    assert all(m[0] == 0x123 for m in fake.msgs)


def test_send_silent_click_custom_point(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    # x/y 与 human_click 同空间（win.rect 相对）：win.rect=(10,5)、
    # 目标窗口 rect=(100,50)→客户区坐标 = (110-100, 205-50) = (10,155)
    info = silent_click.send_silent_click(_FakeWin(0x123), x=100, y=200)
    assert info["point"] == (10, 155)
    assert info["screen_point"] == (110, 205)
    assert fake.msgs[0][3] == (155 << 16) | 10


def test_send_silent_click_right_button_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_click(_FakeWin(0x123), button="secondary")
    assert info["button"] == "secondary"
    assert [m[1] for m in fake.msgs] == [WM_MOUSEMOVE, WM_RBUTTONDOWN,
                                         WM_RBUTTONUP, WM_MOUSEMOVE]
    assert [m[2] for m in fake.msgs] == [0, MK_RBUTTON, 0, 0]
    assert [m[3] for m in fake.msgs] == [(384 << 16) | 683] * 3 + [((768 - 20) << 16) | 20]


def test_send_silent_click_invalid_button(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    with pytest.raises(ValueError):
        silent_click.send_silent_click(_FakeWin(0x123), button="middle")


# ---- 纯投递流程（静默回蓝路径：只投递+日志，不做帧差判定） ----

def test_send_and_report_custom_point(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    logs = []
    ok, info = silent_click.send_and_report(_FakeWin(0x123), logs.append,
                                            button="secondary", x=1316, y=105)
    assert ok
    assert "右键" in info and "(1316,105)" in info
    assert [m[1] for m in fake.msgs] == [WM_MOUSEMOVE, WM_RBUTTONDOWN,
                                         WM_RBUTTONUP, WM_MOUSEMOVE]
    # win.rect=(10,5)→屏幕(1326,110)；目标窗口 rect=(100,50)→客户区(1226,60)
    assert fake.msgs[0][3] == (60 << 16) | 1226
    assert fake.msgs[3][3] == ((768 - 20) << 16) | 20
    assert any("已投递" in l for l in logs)
    assert any("窗口(1316,105)" in l for l in logs)
    assert any("请自行观察游戏效果" in l for l in logs)


def test_send_and_report_no_window(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    logs = []
    ok, info = silent_click.send_and_report(_FakeWin(0), logs.append,
                                            button="primary")
    assert not ok
    assert "未找到可投递的游戏窗口" in info


def test_send_silent_click_custom_point_no_rect_fallback(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    # 无 rect 的窗口替身：回退用目标窗口矩形做基准（客户区=屏幕偏移 0）
    info = silent_click.send_silent_click(_FakeWinNoRect(0x123), x=100, y=200)
    assert info["point"] == (100, 200)
    assert info["screen_point"] == (200, 250)
