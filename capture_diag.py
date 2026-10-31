# -*- coding: utf-8 -*-
"""capture_diag.py —— 大话西游2经典版 截屏通道诊断脚本（纯标准库，无第三方依赖）

用途：在异常机上定位「自检首帧黑屏」的根因。对比四条截屏通道：
  A. PrintWindow(flags=3 = PW_CLIENTONLY | PW_RENDERFULLCONTENT) —— 产品现状路径
  B. PrintWindow(flags=0)                                    —— WM_PRINT 路径
  C. 窗口区域 BitBlt（GetWindowDC + BitBlt，屏幕拷贝语义）
  D. 全屏 BitBlt（GetDC(NULL) + BitBlt，验证屏幕本身是否有画面）
并在激活游戏窗口后复测 A/C，同时记录环境事实（进程提权/前台窗口/DWM/
MPO/兼容模式/显卡/显示器/系统版本）。

运行（异常机上，任意 Python 3.7+，无需 pip 安装任何包）：
    python capture_diag.py

产出（脚本同目录）：
    capture_diag_YYYYmmdd_HHMMSS.log   文本日志（utf-8，请发回这个文件）
    capture_diag_1_printwin3.bmp       通道 A 结果（预期与 App 一样黑）
    capture_diag_2_printwin0.bmp       通道 B 结果
    capture_diag_3_winblt.bmp          通道 C 结果
    capture_diag_4_screenblt.bmp       通道 D 结果
    capture_diag_5_printwin3_after_activate.bmp  激活游戏窗口后复测 A
    capture_diag_6_winblt_after_activate.bmp     激活游戏窗口后复测 C

说明：控制台若为 GBK 编码，中文输出会乱码，属正常；以 .log 文件内容为准。
本脚本只读屏幕与系统状态，全程不点击不按键，仅尝试激活游戏窗口一次（复测用）。
"""
import ctypes
import struct
import sys
import time
import winreg
from ctypes import wintypes
from datetime import datetime

# ---------------------------------------------------------------- Win32 绑定

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2
GWL_STYLE = -16
GWL_EXSTYLE = -20
TOKEN_QUERY = 0x0008
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_ELEVATION = 20
SM_REMOTESESSION = 4096
# 与 App 自检同阈值：三通道均 <16 的像素占比 >50% 判黑帧
BLACK_THRESHOLD = 50


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", ctypes.c_wchar * 32),
                ("DeviceString", ctypes.c_wchar * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", ctypes.c_wchar * 128), ("DeviceKey", ctypes.c_wchar * 128)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32)]


def _setup_prototypes():
    u, g, k, a, d = user32, gdi32, kernel32, advapi32, dwmapi

    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, ctypes.c_int]
    u.GetClassNameW.restype = ctypes.c_int
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsIconic.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.GetClientRect.restype = wintypes.BOOL
    u.GetWindowDC.argtypes = [wintypes.HWND]
    u.GetWindowDC.restype = wintypes.HDC
    u.GetDC.argtypes = [wintypes.HWND]
    u.GetDC.restype = wintypes.HDC
    u.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    u.ReleaseDC.restype = ctypes.c_int
    u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.c_uint]
    u.PrintWindow.restype = wintypes.BOOL
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetSystemMetrics.argtypes = [ctypes.c_int]
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetDpiForSystem.argtypes = []
    u.GetDpiForSystem.restype = ctypes.c_uint
    u.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                      ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    u.EnumDisplayDevicesW.restype = wintypes.BOOL
    u.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    u.GetMonitorInfoW.restype = wintypes.BOOL

    g.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g.CreateCompatibleDC.restype = wintypes.HDC
    g.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g.SelectObject.restype = wintypes.HGDIOBJ
    g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g.DeleteObject.restype = wintypes.BOOL
    g.DeleteDC.argtypes = [wintypes.HDC]
    g.DeleteDC.restype = wintypes.BOOL
    g.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
    g.BitBlt.restype = wintypes.BOOL
    g.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint,
                            ctypes.c_uint, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), ctypes.c_uint]
    g.GetDIBits.restype = ctypes.c_int

    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.GetCurrentProcess.argtypes = []
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                             wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.restype = wintypes.BOOL

    a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.HANDLE)]
    a.OpenProcessToken.restype = wintypes.BOOL
    a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                      wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    a.GetTokenInformation.restype = wintypes.BOOL

    d.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
    d.DwmIsCompositionEnabled.restype = ctypes.c_int

    try:
        get_lp = getattr(u, "GetWindowLongPtrW")
    except AttributeError:
        get_lp = u.GetWindowLongW
    get_lp.argtypes = [wintypes.HWND, ctypes.c_int]
    get_lp.restype = ctypes.c_ssize_t
    return get_lp


_get_window_long = _setup_prototypes()

_WinEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                      ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

# ---------------------------------------------------------------- 日志

_log_fh = None
_run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")


def log(msg):
    line = f"{datetime.now().strftime('%H:%M:%S')} [诊断] {msg}"
    print(line)
    if _log_fh is not None:
        _log_fh.write(line + "\n")
        _log_fh.flush()


# ---------------------------------------------------------------- 环境事实

def win_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def windows_version():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = winreg.QueryValueEx(k, "ProductName")[0]
            build = winreg.QueryValueEx(k, "CurrentBuildNumber")[0]
            try:
                disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
            except OSError:
                disp = ""
            return f"{name}{(' ' + disp) if disp else ''} build {build}"
    except OSError:
        return "读取失败"


def process_is_elevated(proc_handle):
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(proc_handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        val = wintypes.DWORD()
        ret = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION, ctypes.byref(val),
                                            ctypes.sizeof(val), ctypes.byref(ret)):
            return None
        return bool(val.value)
    finally:
        kernel32.CloseHandle(token)


def process_path(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if not kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        kernel32.CloseHandle(h)


def appcompat_layers(exe_path):
    """注册表里为该 exe 设置的兼容模式层（以兼容模式运行等）。"""
    hits = []
    roots = ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM"))
    views = ((0, ""), (winreg.KEY_WOW64_64KEY, "64"), (winreg.KEY_WOW64_32KEY, "32"))
    for base, base_name in roots:
        for view, tag in views:
            try:
                with winreg.OpenKey(
                        base,
                        r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers",
                        0, winreg.KEY_READ | view) as k:
                    i = 0
                    while True:
                        try:
                            vn, vv, _ = winreg.EnumValue(k, i)
                        except OSError:
                            break
                        if vn.lower() == (exe_path or "").lower():
                            hits.append(f"{base_name}/Layers{('/' + tag) if tag else ''}: {vv}")
                        i += 1
            except OSError:
                pass
    return hits or ["无（未设置兼容模式）"]


def overlay_test_mode():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\Dwm",
                            0, winreg.KEY_READ) as k:
            return str(winreg.QueryValueEx(k, "OverlayTestMode")[0])
    except OSError:
        return "未设置（系统默认，MPO 按驱动规则启用）"


def display_adapters():
    res = []
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        if dd.StateFlags & 0x1:  # DISPLAY_DEVICE_ACTIVE
            res.append(f"{dd.DeviceString} | {dd.DeviceID}")
        i += 1
    return res or ["枚举失败"]


def list_monitors():
    out = []

    @_MonitorEnumProc
    def cb(hmon, _hdc, _rect, _lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            out.append(f"矩形=({r.left},{r.top},{r.right},{r.bottom}) "
                       f"主屏={'是' if info.dwFlags & 0x1 else '否'} 设备={info.szDevice}")
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    return out or ["枚举失败"]


def find_game_windows():
    out = []

    @_WinEnumProc
    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = win_class(hwnd)
        title = win_text(hwnd)
        if cls == "DHXYJYMainFrame" or "大话西游2经典版" in title:
            out.append((hwnd, cls, title))
        return True

    user32.EnumWindows(cb, 0)
    return out


# ---------------------------------------------------------------- 截屏通道

def _frame_stats(data, w, h):
    n = w * h
    s_r = s_g = s_b = 0
    black = 0
    bright = 0
    for i in range(0, len(data), 4):
        b = data[i]
        g = data[i + 1]
        r = data[i + 2]
        s_b += b
        s_g += g
        s_r += r
        if b < 16 and g < 16 and r < 16:
            black += 1
        elif r >= 200 and g >= 200 and b >= 200:
            bright += 1
    return {"w": w, "h": h,
            "mean": (s_r + s_g + s_b) / (3 * n) if n else 0.0,
            "black": black / n * 100 if n else 0.0,
            "bright": bright / n * 100 if n else 0.0}


def _get_bits(mem, bmp, w, h):
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = h  # 正数：自下而上（BMP 文件同序，可直接落盘）
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    return buf.raw, got


def _save_bmp(stem, data, w, h):
    path = f"capture_diag_{_run_ts}_{stem}.bmp"
    head = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(data), 0, 0, 54)
    bih = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, len(data), 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(head)
        f.write(bih)
        f.write(data)
    return path


def _log_stats(label, stats, path):
    if stats is None:
        log(f"[{label}] 像素统计失败")
        return
    log(f"[{label}] 像素: {stats['w']}x{stats['h']} mean={stats['mean']:.1f} "
        f"黑帧比例={stats['black']:.1f}% 亮区比例={stats['bright']:.1f}% → {path}")


def capture_printwindow(hwnd, flags, label, stem):
    cl = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cl))
    w, h = cl.right, cl.bottom
    if w <= 0 or h <= 0:
        log(f"[{label}] 客户端区无效 {w}x{h}，跳过")
        return None
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        log(f"[{label}] GetWindowDC 失败 err={ctypes.get_last_error()}")
        return None
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        t0 = time.perf_counter()
        ok = user32.PrintWindow(hwnd, mem, flags)
        dt = (time.perf_counter() - t0) * 1000
        err = ctypes.get_last_error() if not ok else 0
        data, got = _get_bits(mem, bmp, w, h)
        stats = _frame_stats(data, w, h)
        path = _save_bmp(stem, data, w, h)
        log(f"[{label}] PrintWindow(flags={flags}) 返回={'TRUE' if ok else 'FALSE'} "
            f"err={err} 耗时={dt:.1f}ms GetDIBits={got}")
        _log_stats(label, stats, path)
        return stats
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)


def _capture_bitblt(hdc, w, h, label, stem, release_hdc, release_hwnd):
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        t0 = time.perf_counter()
        ok = gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, SRCCOPY | CAPTUREBLT)
        dt = (time.perf_counter() - t0) * 1000
        err = ctypes.get_last_error() if not ok else 0
        data, got = _get_bits(mem, bmp, w, h)
        stats = _frame_stats(data, w, h)
        path = _save_bmp(stem, data, w, h)
        log(f"[{label}] BitBlt 返回={'TRUE' if ok else 'FALSE'} err={err} "
            f"耗时={dt:.1f}ms GetDIBits={got}")
        _log_stats(label, stats, path)
        return stats
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        if release_hdc:
            user32.ReleaseDC(release_hwnd, hdc)


def capture_window_bitblt(hwnd, label, stem):
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    w, h = wr.right - wr.left, wr.bottom - wr.top
    hdc = user32.GetWindowDC(hwnd)
    if not hdc:
        log(f"[{label}] GetWindowDC 失败 err={ctypes.get_last_error()}")
        return None
    return _capture_bitblt(hdc, w, h, label, stem, release_hdc=True, release_hwnd=hwnd)


def capture_screen_bitblt(label, stem):
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    hdc = user32.GetDC(None)
    if not hdc:
        log(f"[{label}] GetDC(NULL) 失败 err={ctypes.get_last_error()}")
        return None
    return _capture_bitblt(hdc, w, h, label, stem, release_hdc=True, release_hwnd=None)


def describe_foreground():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "无前台窗口（句柄为空）"
    return f"hwnd={hwnd:#x} 类名={win_class(hwnd)!r} 标题={win_text(hwnd)!r}"


# ---------------------------------------------------------------- 主流程

def main():
    global _log_fh
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    log_path = f"capture_diag_{_run_ts}.log"
    _log_fh = open(log_path, "w", encoding="utf-8")

    log(f"==== 截屏通道诊断 v1 ==== 日志文件: {log_path}")
    log(f"Python {sys.version.split()[0]} ({ctypes.sizeof(ctypes.c_void_p) * 8} 位)")
    log(f"Windows: {windows_version()}")
    log(f"远程会话: {bool(user32.GetSystemMetrics(SM_REMOTESESSION))} | "
        f"系统 DPI: {user32.GetDpiForSystem()} | 屏幕: "
        f"{user32.GetSystemMetrics(0)}x{user32.GetSystemMetrics(1)}")
    for m in list_monitors():
        log(f"显示器: {m}")
    for a in display_adapters():
        log(f"显卡: {a}")
    dwm_on = wintypes.BOOL()
    dwmapi.DwmIsCompositionEnabled(ctypes.byref(dwm_on))
    log(f"DWM 合成: {'开启' if dwm_on.value else '关闭'} | MPO OverlayTestMode: {overlay_test_mode()}")

    # 本进程提权状态
    self_elev = process_is_elevated(kernel32.GetCurrentProcess())
    log(f"本诊断脚本进程: 提权={'是' if self_elev else '否' if self_elev is not None else '未知'}")

    # 游戏窗口
    wins = find_game_windows()
    if not wins:
        log("未找到游戏窗口（类名 DHXYJYMainFrame 或标题含「大话西游2经典版」），"
            "请先启动游戏后重跑")
        log("==== 诊断结束（未找到窗口） ====")
        return
    log(f"找到候选窗口 {len(wins)} 个:")
    for hwnd, cls, title in wins:
        log(f"  - hwnd={hwnd:#x} 类名={cls!r} 标题={title!r}")
    hwnd = wins[0][0]  # 类名匹配优先，EnumWindows 已按条件收集
    cls = win_class(hwnd)
    title = win_text(hwnd)
    if cls != "DHXYJYMainFrame" and len(wins) > 1:
        for w2 in wins:
            if w2[1] == "DHXYJYMainFrame":
                hwnd, cls, title = w2
                break

    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cl = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cl))
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    iconic = bool(user32.IsIconic(hwnd))
    style = _get_window_long(hwnd, GWL_STYLE)
    exstyle = _get_window_long(hwnd, GWL_EXSTYLE)
    log(f"目标窗口: hwnd={hwnd:#x} 类名={cls!r} 标题={title!r}")
    log(f"  窗口矩形=({wr.left},{wr.top},{wr.right},{wr.bottom}) "
        f"客户端={cl.right}x{cl.bottom} 最小化={'是' if iconic else '否'} "
        f"style={style:#x} exstyle={exstyle:#x} PID={pid.value}")

    exe_path = process_path(pid.value)
    game_elev = None
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if hproc:
        game_elev = process_is_elevated(hproc)
        kernel32.CloseHandle(hproc)
    log(f"游戏进程: 路径={exe_path or '读取失败'} 提权="
        f"{'是' if game_elev else '否' if game_elev is not None else '未知'}")
    if exe_path:
        for layer in appcompat_layers(exe_path):
            log(f"兼容模式: {layer}")
    log(f"当前前台窗口: {describe_foreground()}")

    # ---- 四通道 ----
    log("---- 截屏通道对比 ----")
    if iconic:
        log("注意：游戏窗口当前最小化，此时各通道黑帧属预期，请恢复窗口后重跑")
    time.sleep(0.3)
    a = capture_printwindow(hwnd, PW_CLIENTONLY | PW_RENDERFULLCONTENT, "A 现状路径", "1_printwin3")
    time.sleep(0.3)
    b = capture_printwindow(hwnd, 0, "B WM_PRINT 路径", "2_printwin0")
    time.sleep(0.3)
    c = capture_window_bitblt(hwnd, "C 窗口 BitBlt", "3_winblt")
    time.sleep(0.3)
    d = capture_screen_bitblt("D 全屏 BitBlt", "4_screenblt")

    # ---- 激活复测 ----
    log("---- 激活游戏窗口后复测 ----")
    setfg = bool(user32.SetForegroundWindow(hwnd))
    time.sleep(0.8)
    fg_after = user32.GetForegroundWindow()
    log(f"SetForegroundWindow 返回={'TRUE' if setfg else 'FALSE'} | "
        f"激活后前台: {'游戏窗口' if fg_after == hwnd else describe_foreground()}")
    after_a = capture_printwindow(hwnd, PW_CLIENTONLY | PW_RENDERFULLCONTENT,
                                  "A2 激活后现状路径", "5_printwin3_after_activate")
    time.sleep(0.3)
    after_c = capture_window_bitblt(hwnd, "C2 激活后窗口 BitBlt", "6_winblt_after_activate")

    # ---- 判定 ----
    log("==== 判定提示 ====")
    pa = a["black"] if a else None
    pb = b["black"] if b else None
    pc = c["black"] if c else None
    pd = d["black"] if d else None
    p2a = after_a["black"] if after_a else None
    p2c = after_c["black"] if after_c else None
    if iconic:
        log("- 游戏窗口最小化，上述黑帧不构成结论，请恢复窗口后重跑")
    if pa is not None and pa < BLACK_THRESHOLD:
        log("- 通道 A（产品同款路径）本次有画面：未复现黑帧。"
            "请确认测试时游戏窗口状态与出问题时一致（窗口化、未最小化）后重跑")
    else:
        if pc is not None and pc < BLACK_THRESHOLD:
            log("- 屏幕窗口区域有画面，但 PrintWindow 拿不到 → 根因在游戏呈现路径"
                "（DWM 未合成该窗口内容/覆盖平面），修复方向：截屏回退链 BitBlt/DXGI")
        elif pd is not None and pd < BLACK_THRESHOLD:
            log("- 全屏有画面但游戏窗口区域是黑的 → 游戏窗口本身没渲染，"
                "或窗口区域被全黑覆盖")
        elif pa is not None:
            log("- 屏幕与窗口区域均无画面 → 显示器/会话问题（关闭、睡眠、锁屏），"
                "与截屏方式无关")
    if pb is not None and pb < BLACK_THRESHOLD and (pa is None or pa >= BLACK_THRESHOLD):
        log("- PrintWindow(flags=0) 路径有画面：游戏响应 WM_PRINT，可作优先兜底通道")
    if game_elev and self_elev is False:
        log("- 游戏进程已提权而诊断脚本未提权（UIPI）：可解释 App 日志中"
            "「游戏窗口未在前台/激活失败」；不影响 PW_RENDERFULLCONTENT 通道本身")
    if not setfg or fg_after != hwnd:
        log("- 本机无法把游戏窗口置前（SetForegroundWindow 失败/未生效），"
            "与 App 日志「未在前台」一致：提权差异或前台锁")
    else:
        log(f"- 激活正常；激活后复测 A 黑帧比例={p2a if p2a is not None else 'N/A'}% "
            f"C={p2c if p2c is not None else 'N/A'}%")
    log("==== 诊断结束：请把本 .log 文件（及同目录 BMP）发回分析 ====")


if __name__ == "__main__":
    main()
