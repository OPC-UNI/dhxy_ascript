"""AScript Windows 应用模板的入口程序。

演示如何使用 FloatScriptWindow 创建带有启动/停止控制的自动化脚本应用。
日志输出使用 FloatScriptWindow 自带的日志面板：print 会自动显示在
浮动窗口的日志区（点击窗口上的日志预览行即可展开）。

模块划分（main.py 之外的代码均在 src/ 下）：
- src/app_utils.py       通用工具（日志、toast）
- src/game.py            游戏窗口操作（窗口查找、登录 Tab 监听）
- src/bag.py             背包操作（打开背包、物品检测与点击）
- src/battle.py          战斗自动化（战斗监控与人物/召唤兽操作）
- src/wuhuan.py          五环任务自动化（购装备、领取、跟踪循环）
- src/kezhan.py          客栈任务自动化（洛阳客栈找掌柜、领取、跟踪循环）
- src/xiuluo.py          修罗任务自动化（灵兽村领取、跟踪、返程循环）
- src/wubei.py           五倍任务自动化（战斗先行，其余流程后续增加）
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
from src.control_window import (ControlWindow, load_task_mode,
                                load_use_sheyaoxiang,
                                load_debug_overlay,
                                load_battle_actions)  # 自定义网页控制窗口（开始/停止按钮）
from src.app_utils import log, toast, log_file_path  # 日志与系统通知
from src import game  # 游戏窗口操作
from src import bag  # 背包操作
from src import battle  # 战斗自动化（战斗监控与人物/召唤兽操作）
from src import xuanshu  # 玄枢轶闻任务自动化
from src import wuhuan  # 五环任务自动化
from src import kezhan  # 客栈任务自动化
from src import team  # 组队功能（任务前置退队）
from src import xiuluo  # 修罗任务自动化
from src import wubei  # 五倍任务自动化
from src import status_bars  # 人物/召唤兽血蓝条检测
from src import debug_overlay  # 调试信息悬浮层（游戏窗口上绘制任务/配置/进度）
from src import map as game_map  # 地图操作（OCR 引擎预热等）


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
    log(f"[日志] 本地日志文件: {log_file_path()}", "SYS")
    mode = load_task_mode()
    use_sheyaoxiang = load_use_sheyaoxiang()
    battle_actions = load_battle_actions()
    log(f"[模式] 当前任务模式: {mode}", "SYS")
    log(f"[配置] 使用摄妖香: {'开' if use_sheyaoxiang else '关'}", "SYS")
    log(f"[配置] 人物战斗动作: {battle_actions['char']}，召唤兽战斗动作: {battle_actions['beast']}", "SYS")
    log(f"[配置] 调试信息悬浮: {'开' if load_debug_overlay() else '关'}", "SYS")
    # 调试悬浮层初始状态：任务模式与战斗配置（战斗场次/进度由各模块实时更新）
    debug_overlay.set_task(mode)
    debug_overlay.set_config(battle_actions["char"], battle_actions["beast"])

    # OCR 引擎预热：首次调用需加载 det/cls/rec 三个模型（一次性约 1s），
    # 提前完成避免拖慢任务首轮识别
    game_map.warmup_ocr()

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

    # ---- 启动检查（仅执行一次，不做循环检查） ----
    # 1. 等待游戏主窗口出现（最多 30 秒）
    main_win = None
    deadline = time.time() + 30
    while main_win is None and time.time() < deadline:
        main_win = game.find_main_window()
        if main_win is None:
            time.sleep(1)

    if main_win is None:
        # 游戏未启动：结束任务——return 触发框架停止流程（面板自动显示已停止、
        # 执行 on('stop') 善后回调释放系统资源），与各任务循环正常结束一致
        log("[检查] 30 秒内未检测到游戏主窗口，游戏未启动，任务结束", "WARN")
        debug_overlay.shutdown()
        return
    else:
        # 调试信息悬浮层：在游戏窗口上绘制任务/配置/进度（开关在控制窗口
        # 切换后实时生效；开关关闭时不创建绘制窗口，行为与未启用一致）
        debug_overlay.start(main_win)

        # 2. 登录状态检查（Tab 读取一次）
        tab = game.read_login_tab(main_win)
        if tab == game.GAME_TITLE:
            log("[检查] 当前未登录（Tab 显示为大话西游2经典版）", "WARN")
        elif tab:
            log(f"[检查] 当前登录角色: {tab}", "SYS")
            debug_overlay.set_name(tab)  # 悬浮层角色名
        else:
            log("[检查] Tab 读取为空（游戏加载中？）", "WARN")

        logged_in = bool(tab and tab != game.GAME_TITLE)
        if not logged_in:
            log("[检查] 未登录，跳过摄妖香/玄枢轶闻/血蓝条检查", "WARN")

        # 3. 摄妖香流程（开关开启且已登录时执行一次）
        if logged_in and use_sheyaoxiang:
            ok, info = bag.use_sheyaoxiang(main_win)
            if ok:
                log(f"[摄妖香] {info}", "SYS")
            else:
                log(f"[摄妖香] {info}", "WARN")

        # 4. 战斗监控线程（已登录时启动，任何模式下自动执行人物/召唤兽战斗操作；
        #    启动日志由 watch_loop 内部输出，此处不重复打）
        if logged_in:
            battle_thread = threading.Thread(target=battle.watch_loop,
                                             args=(main_win,),
                                             daemon=True, name="battle-watch")
            battle_thread.start()

        # 5. 玄枢轶闻主循环（该模式且已登录时执行：跟踪完成 → 继续领取 → 往复）
        #    循环正常结束（次数上限/每日次数已满）时任务进程退出，面板自动显示已停止
        #    任务前置：退出队伍（队长解散/队员离开），单人状态执行
        if logged_in and mode == "玄枢轶闻":
            ok, info = team.leave_or_disband_team(main_win)
            log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")
            info = xuanshu.run_loop(main_win)
            log(f"[玄枢轶闻] 任务循环结束：{info}", "SYS")
            debug_overlay.shutdown()
            return

        # 6. 五环主循环（购买装备 → 领取 → 跟踪 → 往复，达到次数上限结束任务进程）
        if logged_in and mode == "五环":
            info = wuhuan.run_loop(main_win)
            log(f"[五环] 任务循环结束：{info}", "SYS")
            debug_overlay.shutdown()
            return

        # 7. 客栈主循环（洛阳客栈找掌柜 → 领取 → 跟踪 → 往复，
        #    达到次数上限或找不到掌柜时结束任务进程）
        #    任务前置：退出队伍（队长解散/队员离开），单人状态执行
        if logged_in and mode == "客栈":
            ok, info = team.leave_or_disband_team(main_win)
            log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")
            info = kezhan.run_loop(main_win)
            log(f"[客栈] 任务循环结束：{info}", "SYS")
            debug_overlay.shutdown()
            return

        # 8. 修罗主循环（灵兽村领取 → 跟踪（寻路/对话开打/战斗）→ 返程 → 往复，
        #    达到次数上限时结束任务进程）；非队长时核心流程不执行，
        #    由 run_loop 内部保持任务运行，仅战斗线程自动操作
        if logged_in and mode == "修罗":
            info = xiuluo.run_loop(main_win)
            log(f"[修罗] 任务循环结束：{info}", "SYS")
            debug_overlay.shutdown()
            return

        # 9. 五倍主循环（当前仅战斗功能：保持任务运行，战斗由战斗监控
        #    线程自动操作；领取/寻路/返程流程后续增加）
        if logged_in and mode == "五倍":
            info = wubei.run_loop(main_win)
            log(f"[五倍] 任务循环结束：{info}", "SYS")
            debug_overlay.shutdown()
            return

        # 10. 血蓝条检测（已登录时执行一次）
        if logged_in:
            status_bars.check_all_bars(main_win)

    # ---- 保持任务运行，等待停止信号（后续各模式的持续逻辑在此扩展） ----
    while True:
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
