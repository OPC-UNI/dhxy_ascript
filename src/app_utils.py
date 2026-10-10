"""通用工具：日志输出、Windows toast 通知、拟人化鼠标操作。"""
import os
import pathlib
import random
import re
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


def norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做精确/模糊匹配可大幅降低漏配（各任务模块共用，勿再本地复制）。
    """
    return re.sub(r"[^\w]", "", s)


# ---- 高性能模式共享设施（固定位置 UI 坐标缓存） ----
# 各任务模块共用：任务进程启动时由 main.task_start 按通用设置置位；
# 主进程（验证流程等）恒为 False，始终走识别路径。
_high_perf = False
_fixed_pos_cache = {}  # key -> (窗口宽, 窗口高, x, y)
# 高性能：入口点击后等待对话框渲染的固定延时（用户实测 500ms 即出，
# 免 OCR 轮询）
HIGH_PERF_DIALOG_WAIT = 0.5


def set_high_perf(on):
    """设置高性能开关（任务进程启动时调用一次）。"""
    global _high_perf
    _high_perf = bool(on)


def high_perf_on():
    """高性能开关是否开启（关闭时全部缓存设施退化为识别路径）。"""
    return _high_perf


def fixed_pos_get(key, win):
    """读取固定位置缓存坐标；窗口尺寸不匹配（缩放/换窗口）视为失效返回 None。"""
    entry = _fixed_pos_cache.get(key)
    if entry is None:
        return None
    w, h, x, y = entry
    return (x, y) if (w, h) == (win.rect.width, win.rect.height) else None


def fixed_pos_set(key, win, x, y):
    """记录固定位置坐标（识别路径成功时调用，位置漂移自愈）。"""
    _fixed_pos_cache[key] = (win.rect.width, win.rect.height, int(x), int(y))


def fixed_pos_clear(key):
    """清除固定位置缓存（缓存点击校验失败时调用，下轮回退识别路径）。"""
    _fixed_pos_cache.pop(key, None)


def high_perf_click(win, key, label, prefix=""):
    """高性能缓存点击：有缓存坐标即点击并返回 True（调用方自行校验
    效果并按需回退识别路径）；开关关闭或无缓存返回 False 不点击。"""
    if not _high_perf:
        return False
    pos = fixed_pos_get(key, win)
    if pos is None:
        return False
    human_click(win, pos[0], pos[1])
    log(f"{prefix}[高性能] 已按缓存坐标点击{label} ({pos[0]}, {pos[1]})", "SYS")
    return True
