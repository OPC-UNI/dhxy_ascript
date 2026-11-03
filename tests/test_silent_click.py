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
                              WM_MOUSEMOVE, WM_RBUTTONDOWN, WM_RBUTTONUP,
                              WM_CHAR, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN,
                              WM_SYSKEYUP)

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


# ---- 静默按键投递序列（键盘消息通道可行性验证） ----

def test_send_silent_hotkey_alt_c_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_hotkey(_FakeWin(0x123), ("alt",), "c")
    assert info["modifiers"] == ["alt"] and info["key"] == "c"
    # Alt 组合全走 WM_SYSKEY*：Alt 按下 → C 按下 → C 抬起 → Alt 抬起
    assert [m[1] for m in fake.msgs] == [WM_SYSKEYDOWN, WM_SYSKEYDOWN,
                                         WM_SYSKEYUP, WM_SYSKEYUP]
    assert [m[2] for m in fake.msgs] == [0x12, 0x43, 0x43, 0x12]
    assert fake.msgs[1][3] & (1 << 29)      # C 键消息带 Alt 上下文位
    assert fake.msgs[2][3] & (1 << 31)      # C 键抬起带转换位
    assert fake.msgs[3][3] & (1 << 31)      # Alt 抬起带转换位
    assert all(m[0] == 0x123 for m in fake.msgs)


def test_send_silent_hotkey_ctrl_tab_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_hotkey(_FakeWin(0x123), ("ctrl",), "tab")
    assert info["modifiers"] == ["ctrl"] and info["key"] == "tab"
    # Ctrl 组合走 WM_KEY*，主键消息无 Alt 上下文位
    assert [m[1] for m in fake.msgs] == [WM_KEYDOWN, WM_KEYDOWN, WM_KEYUP, WM_KEYUP]
    assert [m[2] for m in fake.msgs] == [0x11, 0x09, 0x09, 0x11]
    assert not fake.msgs[1][3] & (1 << 29)
    assert fake.msgs[2][3] & (1 << 31)  # 抬起转换位


def test_send_silent_hotkey_invalid(monkeypatch):
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    with pytest.raises(ValueError):
        silent_click.send_silent_hotkey(_FakeWin(0x123), ("shift",), "c")
    with pytest.raises(ValueError):
        silent_click.send_silent_hotkey(_FakeWin(0x123), ("alt",), "alt")


def test_send_and_report_key(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    logs = []
    ok, info = silent_click.send_and_report_key(_FakeWin(0x123), logs.append)
    assert ok
    assert "Alt+C" in info
    assert [m[1] for m in fake.msgs] == [WM_SYSKEYDOWN, WM_SYSKEYDOWN,
                                         WM_SYSKEYUP, WM_SYSKEYUP]
    assert any("已投递" in l and "Alt+C" in l for l in logs)
    assert any("请自行观察游戏效果" in l for l in logs)


def test_send_silent_hotkey_enter_plain(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_hotkey(_FakeWin(0x123), (), "enter")
    assert info["modifiers"] == [] and info["key"] == "enter"
    assert [m[1] for m in fake.msgs] == [WM_KEYDOWN, WM_KEYUP]
    assert [m[2] for m in fake.msgs] == [0x0D, 0x0D]
    assert not fake.msgs[0][3] & (1 << 29)  # 无 Alt 上下文位
    assert fake.msgs[1][3] & (1 << 31)      # 抬起转换位


def test_send_silent_hotkey_main_window_target(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    # 存在可见子窗口（click_target 会选 0x222），但 main_window=True 投主窗口
    monkeypatch.setattr(silent_click.game, "list_tab_windows",
                        lambda w: [{"hwnd": 0x222, "visible": True}])
    info = silent_click.send_silent_hotkey(_FakeWin(0x123), ("ctrl",), "tab",
                                           main_window=True)
    assert info["main_window"] is True
    assert all(m[0] == 0x123 for m in fake.msgs)


def test_send_silent_hotkey_with_char(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_hotkey(_FakeWin(0x123), (), "enter",
                                           char=0x0D)
    assert [m[1] for m in fake.msgs] == [WM_KEYDOWN, WM_CHAR, WM_KEYUP]
    assert [m[2] for m in fake.msgs] == [0x0D, 0x0D, 0x0D]
    assert fake.msgs[2][3] & (1 << 31)  # 抬起转换位


# ---- 静默粘贴（剪贴板打桩，不污染真实剪贴板） ----

def test_send_silent_paste_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    clip = []
    monkeypatch.setattr(silent_click, "set_clipboard_text", clip.append)
    info = silent_click.send_silent_paste(_FakeWin(0x123), "测试粘贴")
    assert clip == ["测试粘贴"]
    assert [(m[1], m[2], m[3]) for m in fake.msgs] == [(0x0302, 0, 0)]  # WM_PASTE


def test_send_and_report_paste(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    monkeypatch.setattr(silent_click, "set_clipboard_text", lambda t: None)
    logs = []
    ok, info = silent_click.send_and_report_paste(_FakeWin(0x123), logs.append)
    assert ok
    assert "测试粘贴" in info
    assert [m[1] for m in fake.msgs] == [0x0302]
    assert any("WM_PASTE" in l for l in logs)


def test_send_silent_unichar_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    # 返回值非零=该字符已消费（记录进 msg_results）
    def _send(hwnd, msg, wparam, lparam):
        fake.msgs.append((hwnd, msg, wparam, lparam))
        return 1
    monkeypatch.setattr(fake, "SendMessageW", _send)
    info = silent_click.send_silent_unichar(_FakeWin(0x123), "测")
    assert info["text"] == "测"
    assert [(m[1], m[2], m[3]) for m in fake.msgs] == [(0x0109, ord("测"), 0)]
    assert info["msg_results"] == (1,)


def test_dump_child_windows_no_hwnd():
    from src import game as game_mod
    assert game_mod.dump_child_windows(type("W", (), {"hwnd": 0})()) == ["窗口句柄无效"]


def test_send_silent_char_sequence(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_char(_FakeWin(0x123), "测")
    assert info["text"] == "测"
    assert [(m[1], m[2]) for m in fake.msgs] == [(WM_CHAR, ord("测"))]
    assert info["msg_results"] == (0,)


def test_send_silent_text_with_enter(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_text(_FakeWin(0x123), "测试", end_enter=True)
    assert info["text"] == "测试" and info["end_enter"] is True
    # 逐字符 WM_CHAR + 回车 WM_CHAR(0x0D)
    assert [(m[1], m[2]) for m in fake.msgs] == [
        (WM_CHAR, ord("测")), (WM_CHAR, ord("试")), (WM_CHAR, 0x0D)]
    assert info["msg_results"] == (0, 0, 0)


def test_send_silent_text_no_enter(monkeypatch):
    fake = _FakeUser32(client=(1366, 768))
    monkeypatch.setattr(silent_click, "user32", fake)
    monkeypatch.setattr(silent_click.game, "list_tab_windows", lambda w: [])
    info = silent_click.send_silent_text(_FakeWin(0x123), "1000")
    assert [(m[1], m[2]) for m in fake.msgs] == [
        (WM_CHAR, ord("1")), (WM_CHAR, ord("0")), (WM_CHAR, ord("0")), (WM_CHAR, ord("0"))]
