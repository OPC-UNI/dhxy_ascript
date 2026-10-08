"""通用工具：日志输出、Windows toast 通知、拟人化鼠标操作。"""
import os
import pathlib
import random
import subprocess
import threading
import time

from ascript.windows.hardware.mouse import Mouse

# toast 使用的 AppId（Windows PowerShell 官方 AppId，无需注册）
_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"

# ---- 本地日志文件（开发调试用：日志落盘到工程目录 logs/，运行中/结束后
# 可直接读取文件排查问题，无需翻浮动窗口日志面板）----
_LOG_DIR = pathlib.Path(os.getcwd()) / "logs"
_LOG_FILE = None       # 当前日志文件路径（按日期滚动）
_LOG_FH = None         # 当前日志文件句柄（惰性打开，每行 flush）
_LOG_DATE = ""         # 句柄对应的日期（跨天滚动用）
_LOG_LOCK = threading.Lock()  # 战斗监控线程与任务主线程都会打日志


def _xml_escape(s: str) -> str:
    """转义 toast XML 中的特殊字符。"""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def toast(title: str, message: str):
    """弹出 Windows 右下角 toast 提示，不阻塞当前流程。"""
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$APP_ID = '{_APP_ID}'
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>{_xml_escape(title)}</text><text>{_xml_escape(message)}</text></binding></visual></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($APP_ID).Show($toast)
"""
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        print(f">>> [提示] toast 通知发送失败: {e}")


def _open_log_file():
    """惰性打开本地日志文件（logs/YYYY-MM-DD.log），跨天自动滚动。"""
    global _LOG_FILE, _LOG_FH, _LOG_DATE
    today = time.strftime("%Y-%m-%d")
    if _LOG_FH is not None and _LOG_DATE == today:
        return
    if _LOG_FH is not None:
        try:
            _LOG_FH.close()
        except Exception:
            pass
        _LOG_FH = None
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / f"{today}.log"
        _LOG_FH = open(path, "a", encoding="utf-8")
        _LOG_FILE = str(path)
        _LOG_DATE = today
    except Exception:
        _LOG_FH = None


def log_file_path():
    """返回本地日志文件路径（惰性创建文件与目录），供启动时打印。"""
    with _LOG_LOCK:
        _open_log_file()
        return _LOG_FILE


def log(msg, level="INFO"):
    """带时间戳和级别的日志输出：控制台（浮动窗口日志面板）+ 本地文件。

    本地文件为 logs/日期.log（进程工作目录下），每行即时落盘，
    运行中/结束后可直接读取文件排查问题。
    """
    line = f"{time.strftime('%H:%M:%S')} [{level}] {msg}"
    print(line)
    with _LOG_LOCK:
        try:
            _open_log_file()
            if _LOG_FH is not None:
                _LOG_FH.write(line + "\n")
                _LOG_FH.flush()  # 每行落盘，运行中即可直接读取
        except Exception:
            pass  # 文件日志失败不影响自动化流程


def human_move(target_x: int, target_y: int, duration: float = None):
    """直接移动到屏幕坐标（无轨迹），保持极短耗时。

    Args:
        target_x: 目标屏幕 X 坐标
        target_y: 目标屏幕 Y 坐标
        duration: 移动耗时（秒），None 时随机 40~90 毫秒
    """
    if duration is None:
        duration = random.uniform(0.04, 0.09)
    Mouse.mouse_move(target_x, target_y, move_duration=duration)


def human_click(win, x: int, y: int, button: str = "primary"):
    """拟人化点击：平滑弧线移动到窗口相对坐标并点击。

    落点带 ±2px 随机偏移，模拟人手的不精确性。

    Args:
        win: Window 对象（用于窗口相对坐标转屏幕坐标）
        x: 窗口相对 X 坐标
        y: 窗口相对 Y 坐标
        button: 鼠标按键，'primary' 左键 / 'secondary' 右键
    """
    sx = win.rect.left + x + random.randint(-2, 2)
    sy = win.rect.top + y + random.randint(-2, 2)
    human_move(sx, sy)
    Mouse.click(button=button)


def move_away(win):
    """点击对话框选项后把鼠标移走（窗口左下角中性区域）。

    鼠标停留在选项上会使选项进入悬停变色状态，干扰后续的绿字判定
    （选项变色后绿色占比下降，二次 OCR 会漏判/误判——用户实测）。
    """
    human_move(win.rect.left + 20, win.rect.top + win.rect.height - 20)
