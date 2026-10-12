"""AScript Windows 自动化应用入口程序。

架构：控制窗口（ControlWindow）是唯一界面；任务在独立 spawn 子进程中运行
（TaskManager 管理启停、升级强杀与日志回流）；主线程跑 webview GUI 消息循环，
Runtime.wait 驻留线程防止程序自行退出（托盘「彻底退出」→ Runtime.stop 清理退出）。

模块划分（main.py 之外的代码均在 src/ 下）：
- src/app_utils.py       通用工具（日志、toast）
- src/game.py            游戏窗口操作（窗口查找、登录 Tab 监听）
- src/bag.py             背包操作（打开背包、物品检测与点击）
- src/battle.py          战斗自动化（战斗监控与人物/召唤兽操作）
- src/wuhuan.py          五环任务自动化（购装备、领取、跟踪循环）
- src/kezhan.py          客栈任务自动化（洛阳客栈找掌柜、领取、跟踪循环）
- src/xiuluo.py          修罗任务自动化（灵兽村领取、跟踪、返程循环）
- src/wubei.py           五倍任务自动化（宝象国领取、队伍检查，战斗由战斗监控线程操作）
- src/task_manager.py    任务子进程管理（启停、升级强杀、日志回流）
- src/control_window.py  网页控制窗口（启动/停止/重置、任务模式选择、内嵌日志）
"""
import os
import sys
import threading
import time

# ---- 打包环境路径引导 ----
# 打包后本文件位于 site-packages/dhxy2/main.py，src 子包随包安装为
# dhxy2/src，不在模块搜索路径上；将本文件所在目录插入 sys.path，使
# `import src` 在打包环境与源码环境（项目根目录）行为一致。
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(_HERE, "src")):
    sys.path.insert(0, _HERE)
import pystray
from PIL import Image
import uiautomation as auto  # UIA 需要线程内 COM 初始化
import webview  # GUI 消息循环（窗口已在 ControlWindow.prepare 创建，这里直接 start）
from ascript.windows.system import R  # 程序上下文与资源路由
from ascript.windows.system import Runtime  # 生命周期驻留（防止程序自行退出）
from ascript.windows.ui import LicenseWindow  # 安全验证组件
from src.task_manager import TaskManager  # 任务子进程管理（启停/强杀/日志回流）
from src.control_window import (ControlWindow, load_task_mode,
                                load_use_sheyaoxiang,
                                load_debug_overlay,
                                load_battle_actions,
                                load_high_perf,
                                load_auto_refill,
                                load_clear_screen)  # 网页控制窗口（启动/停止/重置、配置持久化）
from src import app_utils  # 通用工具（日志、高性能模式共享设施）
from src.app_utils import log, toast, log_file_path  # 日志与系统通知
from src import game  # 游戏窗口操作
from src import bag  # 背包操作
from src import battle  # 战斗自动化（战斗监控与人物/召唤兽操作）
from src import xuanshu  # 玄枢轶闻任务自动化
from src import task_tracker  # 任务追踪面板操作（启动时判断是否已有跟踪中的任务）
from src import wuhuan  # 五环任务自动化
from src import kezhan  # 客栈任务自动化
from src import team  # 组队功能（任务前置退队）
from src import xiuluo  # 修罗任务自动化
from src import wubei  # 五倍任务自动化
from src import status_bars  # 人物/召唤兽血蓝条检测
from src import debug_overlay  # 调试信息悬浮层（游戏窗口上绘制任务/配置/进度）
from src import clear_screen  # 启动清屏（Alt+6 屏蔽玩家 + Alt+5 屏蔽摊位）
from src import map as game_map  # 地图操作（OCR 引擎预热等）


def _setup_tray(icon_path, control_win):
    """托盘菜单：显示/隐藏控制窗口 + 彻底退出（Runtime.stop 触发退出清理）。"""
    try:
        img = Image.open(icon_path)
        menu = pystray.Menu(
            pystray.MenuItem("显示/隐藏控制窗口",
                             lambda icon, item: control_win.toggle_visible(),
                             default=True),
            pystray.MenuItem("彻底退出", lambda icon, item: Runtime.stop()),
        )
        tray = pystray.Icon("AScriptTray", img, "啄木鸟", menu)
        tray.run()
    except Exception:
        pass


def task_start():
    """业务逻辑入口函数（顶层函数，spawn 按引用 pickle）。

    由 TaskManager 在独立子进程的 daemon 线程中调用；
    停止/超限时进程被强杀，本函数内所有循环与线程随进程一并终止。
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
    log(f"[配置] 战斗结束自动补血蓝: {'开' if load_auto_refill() else '关'}", "SYS")
    log(f"[配置] 启动清屏: {'开' if load_clear_screen() else '关'}", "SYS")
    # 高性能模式（通用设置开关）：置位共享设施，各任务模块据此启用
    # 固定位置缓存点击（主进程验证流程不受影响，恒为识别路径）
    app_utils.set_high_perf(load_high_perf())
    log(f"[配置] 高性能模式: {'开（固定位置缓存点击）' if app_utils.high_perf_on() else '关'}", "SYS")
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
        # 游戏未启动：结束任务——return 后 wrapper 执行 task_stop 善后、
        # 子进程退出，TaskManager 监控线程收口状态与日志，与各任务循环正常结束一致
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
            log("[检查] 未登录，跳过清屏/摄妖香/玄枢轶闻/血蓝条检查", "WARN")

        # 2.5 清屏（通用设置开关，默认开启；每次任务启动执行一次）：
        #     Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位，供后续任务干净执行
        if logged_in and load_clear_screen():
            ok, info = clear_screen.run_clear_screen(main_win)
            log(f"[清屏] {info}", "SYS" if ok else "WARN")

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
        #    任务前置：退出队伍（队长解散/队员离开），单人状态执行。
        #    已有跟踪中的任务时跳过退队直接续跑（退队只服务于领取流程，
        #    续跑无需检查队伍信息——用户确认）
        if logged_in and mode == "玄枢轶闻":
            if not task_tracker.has_task(main_win, xuanshu.TASK_TITLE):
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

        # 9. 五倍主循环（宝象国领取 → 寻路跟踪（绿字自动寻路/对话开打/
        #    战斗）→ 返程（五倍回城符回宝象国）→ 往复，达到次数上限时结束
        #    任务进程）；非队长时核心流程不执行，由 run_loop 内部保持
        #    任务运行，仅战斗线程自动操作
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


def task_stop():
    """任务停止时的善后处理回调（顶层函数，spawn 按引用 pickle）。

    由 TaskManager 的子进程 wrapper 在停止时调用（预算约 2.5 秒，
    超时由进程退出兜底）；优先显式回收调试悬浮层绘制子进程，
    强杀路径下其 15 秒心跳自灭兜底。
    """
    log("接收到停止信号，正在释放系统资源...", "SYS")
    debug_overlay.shutdown()  # 显式回收悬浮层绘制进程（幂等）
    # 模拟保存数据逻辑
    time.sleep(1)
    log("资源释放完毕，安全退出。", "SYS")


def _license_info(auth_win):
    """整理授权信息供面板展示（状态 + 到期时间）。

    字段来源：SDK 激活窗口同款数据——license_data.status（1=有效/2=过期）、
    license_data.expire（到期时间字符串）、app_info.is_free（免费版标记）。
    """
    data = auth_win.license_data or {}
    info = {"status": "未知", "expire": ""}
    if data.get("status") == 1:
        info["status"] = "已激活"
        info["expire"] = str(data.get("expire") or "")
    elif (auth_win.app_info or {}).get("is_free") == 1:
        info["status"] = "免费版"
    elif data.get("status") == 2:
        info["status"] = "已过期"
    return info


def _parse_expire(expire):
    """解析到期时间字符串，返回 datetime.date 或 None（格式未知时保守）。"""
    if not expire:
        return None
    import datetime
    text = str(expire).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _days_left(expire):
    """距到期剩余天数（到期时间无法解析时返回 None）。"""
    date = _parse_expire(expire)
    if date is None:
        return None
    import datetime
    return (date - datetime.date.today()).days


if __name__ == '__main__':
    # ---- 授权守门员：未激活不允许进入主程序 ----
    # 先静默在线校验（每次启动都需在线验证，achttp 闭源后端）：免费版直接
    # 放行；本地缓存激活码在线验证通过则不弹窗。校验失败（无缓存/无效/
    # 过期/网络异常）再弹激活窗口（阻塞），激活成功或关闭窗口后才继续。
    # 注意：授权窗口必须独占一次 webview.start()——主程序装配必须在此之后。
    debug_auth = os.environ.get("ASCRIPT_DEBUG_AUTH") == "1"  # 强制弹激活窗口（授权联调用）
    auth_win = LicenseWindow(app_id="23651")
    silent_ok = False
    deny_info = ""  # 静默被拒原因（用于弹窗前提示）
    if debug_auth:
        print("[授权] 调试模式：跳过静默校验，直接进入激活窗口")
    else:
        for attempt in range(3):  # 网络波动重试；明确拒绝（无效/过期）不重试
            try:
                auth_win.get_app_info()
                cached_key = auth_win.get_cached_key()
                if cached_key:
                    auth_win.activate_key(cached_key)
                result = auth_win.start_app()
                if result is True:
                    silent_ok = True
                    break
                deny_info = (result or {}).get("error", "授权未通过")
                break
            except Exception as e:
                deny_info = f"无法连接授权服务器：{e}"
                print(f"[授权] 在线校验异常（第 {attempt + 1}/3 次）: {e}")
                if attempt < 2:
                    time.sleep(2)
    if silent_ok:
        info = _license_info(auth_win)
        print(f"[授权] 校验通过：状态 {info['status']}"
              + (f"，到期 {info['expire']}" if info["expire"] else ""))
        days_left = _days_left(info["expire"])
        if days_left is not None and 0 <= days_left <= 7:
            print(f"[授权] 提醒：卡密将在 {days_left} 天后到期，请及时续费")
    else:
        toast("AScript 授权",
              f"未授权或授权已过期，请在弹出的窗口中输入卡密激活（{deny_info}）")
        # 阻塞式激活窗口：窗口内 start_app 校验通过会关闭窗口并置 _return_value=True
        auth_win.show(title="软件授权激活", width=400, height=500)
    if not auth_win._return_value:
        print("授权未通过，程序退出。")
        sys.exit(0)
    license_info = _license_info(auth_win)
    print(f"授权通过（{license_info['status']}），启动主程序...")

    # ---- 授权通过后装配主程序 ----
    # 必须放在 __main__ 内：Windows spawn 子进程会重新导入本模块但不执行此段
    # （解决打包后子进程重复启动问题）；同时保证授权窗口阶段 webview 窗口
    # 列表里只有授权窗口自己，其关闭即结束该次消息循环
    tm = TaskManager(task_start, task_stop)
    control_win = ControlWindow(tm, license_info=license_info)  # 构造内已接线 tm 的状态/日志回调
    control_win.prepare()            # 创建唯一窗口（uid='master'）

    # 托盘：显示/隐藏控制窗口 + 彻底退出（Runtime.stop 触发退出清理）
    threading.Thread(target=_setup_tray,
                     args=(R.img("logo.ico"), control_win),
                     daemon=True, name="tray").start()

    def _runtime_waiter():
        """Runtime 驻留线程：托盘「彻底退出」→ Runtime.stop() 后醒来执行清理。

        注意：Runtime 文档模式（主线程 Runtime.wait + 窗口 block=False）在本机
        pywebview 版本不可用——webview.start 要求调用线程名为 MainThread，
        且 winforms 会在调用线程注册 signal 处理器（非主线程必 ValueError）。
        故反向安排：主线程跑 GUI 循环（与授权窗口同一条已验证路径），
        Runtime.wait 放独立非守护线程（信号注册失败已被 Runtime 内部吞掉），
        承担驻留语义与退出触发。
        """
        Runtime.wait()
        # 清理顺序：先杀任务（日志还能进窗口），再销毁窗口结束 GUI 循环
        tm.stop()
        control_win.close_window()

    threading.Thread(target=_runtime_waiter, daemon=False,
                     name="runtime-waiter").start()

    # 主线程阻塞式 GUI 循环：窗口已在 prepare() 创建，这里直接 start
    # （不能用 WebWindow.show()——它内部会重复 create_window 出第二个窗口）
    webview.start(debug=False, gui='edgechromium')

    # GUI 循环结束（窗口已销毁）后收尾：兜底清理任务进程并退出
    tm.stop()
    Runtime.stop()
    print("[系统] 程序已正常退出。")
