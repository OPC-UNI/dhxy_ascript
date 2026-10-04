"""通用工具：日志输出、Windows toast 通知、拟人化鼠标操作。"""
import random
import subprocess
import time

from ascript.windows.hardware.mouse import Mouse

# toast 使用的 AppId（Windows PowerShell 官方 AppId，无需注册）
_APP_ID = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe"


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


def log(msg, level="INFO"):
    """带时间戳和级别的日志输出，进入控制台与浮动窗口的日志面板。"""
    print(f"{time.strftime('%H:%M:%S')} [{level}] {msg}")


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
