"""AScript Windows 应用模板的入口程序。

演示如何使用 FloatScriptWindow 创建带有启动/停止控制的自动化脚本应用。
日志输出使用 FloatScriptWindow 自带的日志面板：print 会自动显示在
浮动窗口的日志区（点击窗口上的日志预览行即可展开）。
"""
import re
import subprocess
import time
import sys
from ascript.windows.system import R  # 程序上下文与资源路由
from ascript.windows.ui import LicenseWindow  # 安全验证组件
from ascript.windows.ui import FloatScriptWindow  # 脚本控制台组件
from ascript.windows.window import Window  # 窗口查找
from polyfill import patch_js_bridge  # pywebview 6 桥接兼容补丁

# 目标游戏窗口标题
GAME_TITLE = "大话西游2经典版"
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


# 初始化控制台：支持自启动 (auto_start) 与 多进程隔离 (use_process)
app = FloatScriptWindow(R.img("logo.ico"), title="我的程序", auto_start=True)

# pywebview 6 下修复悬浮窗页面桥接，日志才能正常显示（必须早于 app.show() 挂载）
patch_js_bridge(app)


def check_game_window():
    """检查「大话西游2经典版」游戏窗口是否存在。

    用进程名 xy2 过滤，避免标题恰好包含该文字的无关窗口（如编辑器）。
    """
    return Window.find_all(title_re=re.escape(GAME_TITLE), process_name="xy2")


@app.on('start')
def task_start():
    """业务逻辑入口函数。

    当用户点击启动按钮或触发自启动时，此函数将在独立的子进程中运行。
    在此函数中编写自动化任务的核心业务逻辑。
    """
    log("业务进程已建立，开始执行自动化任务...", "SYS")

    # ---- 启动后检查游戏窗口是否存在，并弹出一条提示信息 ----
    windows = check_game_window()
    if windows:
        msg = f"已检测到「{GAME_TITLE}」窗口，共 {len(windows)} 个"
        toast("AScript 窗口检查", msg)
        log(f"[检查] {msg}")
        for w in windows:
            log(f"[检查]   - {w.title} ({w.class_name}, {w.process_name})")
    else:
        msg = f"未检测到「{GAME_TITLE}」窗口，请确认游戏已启动"
        toast("AScript 窗口检查", msg)
        log(f"[检查] {msg}", "WARN")

    i = 0

    # 模拟耗时逻辑操作
    while True:
        time.sleep(0.5)
        i += 1


@app.on('stop')
def task_stop():
    """任务停止时的善后处理回调。

    当用户手动停止或任务结束时触发。可在此执行资源释放操作，
    如关闭文件句柄、断开数据库连接等。注意约有 3 秒的执行时限，
    超时后系统会强制回收进程资源。
    """
    log("接收到停止信号，正在释放系统资源...", "SYS")
    # 模拟保存数据逻辑
    time.sleep(1)
    log("资源释放完毕，安全退出。", "SYS")


if __name__ == '__main__':
    # 解决打包后子进程重复启动问题
    app.show()
