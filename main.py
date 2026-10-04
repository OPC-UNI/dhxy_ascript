"""AScript Windows 应用模板的入口程序。

演示如何使用 FloatScriptWindow 创建带有启动/停止控制的自动化脚本应用。
日志输出使用 FloatScriptWindow 自带的日志面板：print 会自动显示在
浮动窗口的日志区（点击窗口上的日志预览行即可展开）。

模块划分（main.py 之外的代码均在 src/ 下）：
- src/app_utils.py       通用工具（日志、toast）
- src/game.py            游戏窗口操作（窗口查找、登录 Tab 监听）
- src/bag.py             背包操作（打开背包、物品检测与点击）
- src/control_window.py  网页控制窗口（启动/重置/关闭、任务模式选择）
- src/polyfill.py        pywebview 6 桥接兼容补丁
"""
import multiprocessing
import os
import threading
import time
from typing import Optional
import pystray
from PIL import Image
import uiautomation as auto  # UIA 需要线程内 COM 初始化
from ascript.windows.system import R  # 程序上下文与资源路由
from ascript.windows.ui import LicenseWindow  # 安全验证组件
from ascript.windows.ui import FloatScriptWindow  # 脚本控制台组件
from src.polyfill import patch_js_bridge  # pywebview 6 桥接兼容补丁
from src.control_window import ControlWindow, load_task_mode  # 自定义网页控制窗口（开始/停止按钮）
from src.app_utils import log, toast, human_click  # 日志、系统通知与拟人化点击
from src import game  # 游戏窗口操作
from src import bag  # 背包操作


class AppWindow(FloatScriptWindow):
    """浮动控制面板：任务状态同步到网页控制窗口，托盘菜单扩展控制窗口显隐。

    覆写 on_toggle_click（面板播放键与控制窗口按钮都会经过它），
    任务启动/停止后仅同步控制窗口页面的状态显示，不改窗口显隐。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.control_win: Optional[ControlWindow] = None

    def on_toggle_click(self, status):
        result = super().on_toggle_click(status)
        if result.get("status") == "success" and self.control_win is not None:
            self.control_win.sync_status(bool(status))
        return result

    def _setup_tray(self):
        """托盘菜单：增加控制窗口的显示/隐藏项。"""
        try:
            img = Image.open(self.icon_path)
            menu = pystray.Menu(
                pystray.MenuItem("显示/隐藏悬浮面板", self.toggle_visible, default=True),
                pystray.MenuItem("显示/隐藏控制窗口", self._toggle_control_window),
                pystray.MenuItem("彻底退出", lambda: os._exit(0)),
            )
            tray = pystray.Icon("AScriptTray", img, self.ui_title, menu)
            tray.run()
        except Exception:
            pass

    def _toggle_control_window(self, icon=None, item=None):
        """托盘菜单回调：切换控制窗口显隐。"""
        if self.control_win is not None:
            self.control_win.toggle_visible()


# 初始化控制台：支持自启动 (auto_start) 与 多进程隔离 (use_process)
# auto_start=False：程序启动时不自动运行任务，由浮动面板播放键触发
app = AppWindow(R.img("logo.ico"), title="我的程序", auto_start=False)

# 仅主进程创建窗口资源（Windows spawn 子进程会重新导入本模块）
if multiprocessing.current_process().name == "MainProcess":
    # pywebview 6 下修复悬浮窗页面桥接，日志才能正常显示（必须早于 app.show() 挂载）
    patch_js_bridge(app)
    # 网页控制窗口：隐藏创建，任务启动后自动弹出；必须早于 app.show() 创建（单次 webview.start 显示全部窗口）
    control_win = ControlWindow(app)
    control_win.prepare()
    app.control_win = control_win

    # 面板关闭时一并销毁隐藏的控制窗口，避免程序残留后台
    # （窗口实例在 app.show() 内部才创建，故用线程轮询挂载关闭回调）
    def _on_panel_closed(*_args):
        try:
            if control_win.webview_instance is not None:
                control_win.webview_instance.destroy()
        except Exception:
            pass

    def _watch_panel_close():
        while app.webview_instance is None:
            time.sleep(0.05)
        app.webview_instance.events.closed += _on_panel_closed

    threading.Thread(target=_watch_panel_close, daemon=True).start()


@app.on('start')
def task_start():
    """业务逻辑入口函数。

    当用户点击启动按钮或触发自启动时，此函数将在独立的子进程中运行。
    在此函数中编写自动化任务的核心业务逻辑。
    """
    log("业务进程已建立，开始执行自动化任务...", "SYS")
    log(f"[模式] 当前任务模式: {load_task_mode()}", "SYS")

    # uiautomation 需要线程内 COM 初始化：任务运行在子进程的工作线程中，
    # 必须显式初始化；保持引用直至任务结束（析构时自动 CoUninitialize）
    _uia_init = auto.UIAutomationInitializerInThread()

    # ---- 启动后检查游戏窗口是否存在，并弹出一条提示信息 ----
    windows = game.check_game_window()
    if windows:
        msg = f"已检测到「{game.GAME_TITLE}」窗口，共 {len(windows)} 个"
        toast("AScript 窗口检查", msg)
        log(f"[检查] {msg}")
        for w in windows:
            r = w.rect
            tag = "主窗口" if w.class_name == game.MAIN_WINDOW_CLASS else "辅助窗口"
            log(f"[检查]   - [{tag}] 标题: {w.title}")
            log(f"[检查]     尺寸: {r.width}x{r.height}  位置: ({r.left}, {r.top})  "
                f"类名: {w.class_name}  进程: {w.process_name}")
        main = game.find_main_window()
        if main:
            log(f"[检查] 主窗口已锁定: {main.title}", "SYS")
        else:
            log("[检查] 未找到主窗口（游戏可能已最小化或未登录）", "WARN")
    else:
        msg = f"未检测到「{game.GAME_TITLE}」窗口，请确认游戏已启动"
        toast("AScript 窗口检查", msg)
        log(f"[检查] {msg}", "WARN")

    # ---- 监听登录用户 Tab 变化（每秒轮询一次） ----
    last_tab = None
    bag_opened = False   # 背包是否已点击打开（每次任务启动重新执行一次）
    bag_retry_at = 0.0   # 找背包失败后的下次重试时间
    while True:
        main_win = game.find_main_window()
        if main_win is None:
            # 游戏最小化或未启动：清空状态，避免恢复后误报"变化"
            if last_tab is not None:
                log("[监听] 游戏主窗口不可见，暂停监听", "WARN")
                last_tab = None
            time.sleep(1)
            continue

        tab = game.read_login_tab(main_win)
        if tab != last_tab:
            if last_tab is not None:
                log(f"[监听] Tab 变化: {last_tab!r} -> {tab!r}", "SYS")
            if tab == game.GAME_TITLE:
                log("[监听] 当前未登录（Tab 显示为大话西游2经典版）", "WARN")
            elif tab:
                log(f"[监听] 当前登录角色: {tab}", "SYS")
            else:
                log("[监听] Tab 读取为空（游戏加载中？）", "WARN")
            last_tab = tab

        # 登录状态下打开背包（点击+bag_box 验证，内置一次重试；成功一次后不再重复）
        if (tab and tab != game.GAME_TITLE and not bag_opened
                and time.time() >= bag_retry_at):
            ok, info = bag.open_bag(main_win)
            if ok:
                bag_opened = True
                log(f"[背包] 已打开（{info}）", "SYS")
                # 背包打开后检测摄妖香（未找到时自动切换储物格再找）
                item, desc = bag.find_sheyaoxiang_with_slots(main_win)
                if item:
                    log(f"[摄妖香] 背包中存在（{desc}，匹配度 {item.confidence:.2f}）", "SYS")
                    # 鼠标移动到物品位置并右键点击一次
                    human_click(main_win, item.x, item.y, button="secondary")
                    log(f"[摄妖香] 已右键点击 ({item.x}, {item.y})", "SYS")
                    # 使用完成后重新点击背包按钮关闭背包
                    time.sleep(0.2)  # 物品使用后短暂等待
                    ok2, info2 = bag.close_bag(main_win)
                    if ok2:
                        log(f"[背包] 已关闭（{info2}）", "SYS")
                    else:
                        log(f"[背包] {info2}", "WARN")
                else:
                    log(f"[摄妖香] {desc}", "WARN")
            else:
                log(f"[背包] {info}，10 秒后重试", "WARN")
                bag_retry_at = time.time() + 10

        time.sleep(1)


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
