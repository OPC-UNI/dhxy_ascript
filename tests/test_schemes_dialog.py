"""方案导入导出文件对话框（原生 comdlg32）单元测试。

对话框本身是系统模态窗口，测试用假 comdlg32 检查 OPENFILENAMEW 的
字段/过滤器/标志接线（打包版曾因 tkinter 缺失导致对话框弹不出来）。
"""
import ctypes

import pytest

from src.core import schemes


class _FakeFunc:
    """可赋 argtypes/restype 的可调用对象（模拟 ctypes 函数指针）。"""

    def __init__(self, impl):
        self._impl = impl
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self._impl(*args)


class _FakeComdlg:
    def __init__(self, save_ret=0, open_ret=0, ext_error=0,
                 on_save=None, on_open=None):
        self.GetSaveFileNameW = _FakeFunc(on_save or (lambda _ptr: save_ret))
        self.GetOpenFileNameW = _FakeFunc(on_open or (lambda _ptr: open_ret))
        self.CommDlgExtendedError = _FakeFunc(lambda: ext_error)


def _install(monkeypatch, fake):
    monkeypatch.setattr(ctypes, "WinDLL", lambda _name: fake)
    monkeypatch.setattr(schemes, "_dialog_owner", lambda: 0)


def _set_file(ofn, path):
    """模拟对话框写回选中路径（lpstrFile 指向调用方的 1024 缓冲）。"""
    buf = ctypes.create_unicode_buffer(path)
    ctypes.memmove(ofn.lpstrFile, ctypes.addressof(buf), ctypes.sizeof(buf))


_FILTER = "JSON 文件 (*.json)\0*.json\0所有文件 (*.*)\0*.*\0\0"


def _filter_items(ofn):
    return ctypes.wstring_at(ofn.lpstrFilter, len(_FILTER)).split("\0")


def test_save_dialog_fields_and_result(monkeypatch):
    seen = {}

    def on_save(ptr):
        ofn = ptr._obj
        seen["title"] = ctypes.wstring_at(ofn.lpstrTitle)
        seen["initial"] = ctypes.wstring_at(ofn.lpstrFile)
        seen["defext"] = ctypes.wstring_at(ofn.lpstrDefExt)
        seen["flags"] = ofn.Flags
        seen["filters"] = _filter_items(ofn)
        seen["size"] = ofn.lStructSize
        seen["nmax"] = ofn.nMaxFile
        seen["owner"] = ofn.hwndOwner
        _set_file(ofn, "D:/tmp/我的方案.json")
        return 1

    _install(monkeypatch, _FakeComdlg(on_save=on_save))
    path = schemes.pick_save_path("我的方案.json")

    assert path == "D:/tmp/我的方案.json"
    assert seen["title"] == "导出配置方案"
    assert seen["initial"] == "我的方案.json"
    assert seen["defext"] == "json"
    assert not seen["owner"]  # NULL owner → c_void_p 读回 None
    assert seen["nmax"] == 1024
    expected_size = 152 if ctypes.sizeof(ctypes.c_void_p) == 8 else 76
    assert seen["size"] == expected_size
    assert seen["flags"] & schemes._OFN_OVERWRITEPROMPT
    assert seen["flags"] & schemes._OFN_PATHMUSTEXIST
    assert not seen["flags"] & schemes._OFN_FILEMUSTEXIST
    assert seen["filters"][:5] == [
        "JSON 文件 (*.json)", "*.json", "所有文件 (*.*)", "*.*", ""]


def test_open_dialog_flags_and_owner(monkeypatch):
    seen = {}

    def on_open(ptr):
        ofn = ptr._obj
        seen["title"] = ctypes.wstring_at(ofn.lpstrTitle)
        seen["flags"] = ofn.Flags
        seen["defext"] = ofn.lpstrDefExt
        seen["owner"] = ofn.hwndOwner
        _set_file(ofn, "E:/导入/方案.json")
        return 1

    _install(monkeypatch, _FakeComdlg(on_open=on_open))
    path = schemes.pick_open_path(owner_hwnd=4321)

    assert path == "E:/导入/方案.json"
    assert seen["title"] == "导入配置方案"
    assert seen["owner"] == 4321
    assert seen["defext"] is None
    assert seen["flags"] & schemes._OFN_FILEMUSTEXIST
    assert not seen["flags"] & schemes._OFN_OVERWRITEPROMPT


def test_cancel_returns_empty(monkeypatch):
    _install(monkeypatch, _FakeComdlg(save_ret=0, open_ret=0, ext_error=0))
    assert schemes.pick_save_path("x.json") == ""
    assert schemes.pick_open_path() == ""


def test_dialog_error_raises(monkeypatch):
    _install(monkeypatch, _FakeComdlg(save_ret=0, ext_error=0x1001))
    with pytest.raises(OSError, match="0x1001"):
        schemes.pick_save_path("x.json")
