"""启动环境自检：记录 DPI/显示器/系统/会话与首帧截图统计，写入日志。

远程机器功能异常（截屏全黑）时，日志开头的自检段可直接定位差异来源：
显示缩放、混合 DPI 多显示器、远程会话、Windows 版本、运行形态（exe/
源码）等。自检全程只读（截图 force_activate=False，不点击不按键），
任何一步异常都静默降级，不影响任务启动。
"""
import ctypes

from .app_utils import log

# GetProcessDpiAwareness 返回值 → 文案
_AWARE_NAMES = {0: "unaware(未感知)", 1: "system(系统)", 2: "per-monitor(每显示器)"}
# 黑帧判定阈值：三通道均 <16 的像素占比超此值判黑帧
_BLACK_RATIO_THRESHOLD = 0.5


def _frozen_mode():
    """运行形态：Nuitka 冻结 exe / 源码（__compiled__ 仅冻结态注入）。"""
    try:
        import __main__
        return "冻结exe" if "__compiled__" in vars(__main__) else "源码"
    except Exception:
        return "未知"


def _process_dpi_awareness():
    """当前进程 DPI 感知模式（0=unaware 1=system 2=per-monitor；失败 None）。"""
    try:
        v = ctypes.c_int()
        ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(v))
        return v.value
    except Exception:
        return None


def _system_dpi():
    """系统 DPI（96=100% 缩放；失败 None）。"""
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return None


def _screen_size():
    """主屏物理分辨率（进程已 DPI 感知时返回物理像素；失败 None）。"""
    try:
        u = ctypes.windll.user32
        return (int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1)))
    except Exception:
        return None


def _monitors():
    """每台显示器的 (矩形, 有效 DPI)；枚举失败返回 None。"""
    import win32api
    infos = []
    for hmon, _hdc, _rect in win32api.EnumDisplayMonitors():
        dpi = None
        try:
            x, y = ctypes.c_uint(), ctypes.c_uint()
            # MDT_EFFECTIVE_DPI=0：该显示器对当前进程生效的 DPI
            if ctypes.windll.shcore.GetDpiForMonitor(
                    int(hmon), 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                dpi = int(x.value)
        except Exception:
            pass
        try:
            mon = win32api.GetMonitorInfo(int(hmon)).get("Monitor")
        except Exception:
            mon = None
        infos.append((mon, dpi))
    return infos


def _windows_version():
    """Windows 产品名 + 版本号（注册表；失败 None）。"""
    import winreg
    try:
        with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            name = winreg.QueryValueEx(k, "ProductName")[0]
            build = winreg.QueryValueEx(k, "CurrentBuildNumber")[0]
            try:
                disp = winreg.QueryValueEx(k, "DisplayVersion")[0]
            except OSError:
                disp = ""
            return f"{name}{(' ' + disp) if disp else ''} build {build}"
    except Exception:
        return None


def _remote_session():
    """是否远程桌面会话（SM_REMOTESESSION=4096；失败 None）。"""
    try:
        return bool(ctypes.windll.user32.GetSystemMetrics(4096))
    except Exception:
        return None


def frame_stats(img):
    """帧像素统计：尺寸/均值/黑帧比例（三通道均 <16 计黑像素）。

    供自检首帧判定与后续截屏回退链复用：模板匹配对恒定单色帧分数恒为
    0.00，OCR 恒为空——黑帧是「识别全失效」的总根源，须在入口拦截。
    """
    import numpy as np
    a = np.asarray(img, dtype=np.uint8)
    if a.ndim == 2:
        a = a[:, :, None]
    mean = float(a.mean())
    black = float((a < 16).all(axis=2).mean())
    return {"w": a.shape[1], "h": a.shape[0], "mean": mean, "black_ratio": black}


def log_environment(win):
    """任务启动自检：环境事实 + 首帧截图统计（只读；失败静默降级）。

    win 为已锁定的游戏主窗口（可为 None：仅记录环境事实，跳过窗口
    与首帧部分）。日志前缀 [自检]，便于远程机排障时一眼定位段落。
    """
    try:
        dpi = _system_dpi()
        aware = _process_dpi_awareness()
        scale = f" {int(round(dpi * 100 / 96))}%" if dpi else ""
        log(f"[自检] 运行形态: {_frozen_mode()} | "
            f"进程 DPI 感知: {_AWARE_NAMES.get(aware, aware)} | "
            f"系统 DPI: {dpi}{scale} | 物理屏幕: {_screen_size()} | "
            f"远程会话: {_remote_session()}", "SYS")
    except Exception as e:
        log(f"[自检] 基础环境读取失败: {e}", "WARN")
    try:
        for i, (mon, dpi) in enumerate(_monitors() or []):
            info = f"DPI={dpi} ({int(round(dpi * 100 / 96))}%)" if dpi else "DPI 读取失败"
            log(f"[自检] 显示器{i}: 矩形={mon} {info}", "SYS")
    except Exception as e:
        log(f"[自检] 显示器枚举失败: {e}", "WARN")
    try:
        ver = _windows_version()
        if ver:
            log(f"[自检] Windows: {ver}", "SYS")
    except Exception:
        pass
    try:
        if win is not None:
            import win32gui
            wdpi = int(ctypes.windll.user32.GetDpiForWindow(int(win.hwnd)))
            cl = win32gui.GetClientRect(int(win.hwnd))
            log(f"[自检] 游戏窗口: {win.rect.width}x{win.rect.height} "
                f"窗口DPI={wdpi} 客户端区={cl[2] - cl[0]}x{cl[3] - cl[1]}", "SYS")
    except Exception:
        pass
    try:
        if win is None:
            return
        img = win.capture(force_activate=False)
        if img is None:
            log("[自检] 首帧截图: 失败（返回 None，窗口 DC 或尺寸异常）", "WARN")
            return
        st = frame_stats(img)
        line = (f"[自检] 首帧截图: {st['w']}x{st['h']} mean={st['mean']:.1f} "
                f"黑帧比例={st['black_ratio'] * 100:.1f}%")
        if st["black_ratio"] > _BLACK_RATIO_THRESHOLD:
            log(line + " —— 黑帧！PrintWindow 未画出内容，后续识别将全部失效", "WARN")
            try:
                img.save("first_frame.png")
                log("[自检] 黑帧已存 first_frame.png 供人工排查", "WARN")
            except Exception:
                pass
        else:
            log(line, "SYS")
    except Exception as e:
        log(f"[自检] 首帧截图自检失败: {e}", "WARN")
