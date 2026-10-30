"""AScript Windows 自动化应用入口程序。

架构：控制窗口（ControlWindow）是唯一界面；任务在独立 spawn 子进程中运行
（TaskManager 管理启停、升级强杀与日志回流）；主线程跑 webview GUI 消息循环，
Runtime.wait 驻留线程防止程序自行退出（托盘「彻底退出」→ Runtime.stop 清理退出）。

模块划分（main.py 之外的代码均在 src/ 下）：
- src/app_utils.py       通用工具（日志、拟人化鼠标操作）
- src/game.py            游戏窗口操作（窗口查找、登录 Tab 监听）
- src/bag.py             背包操作（打开背包、物品检测与点击）
- src/battle.py          战斗自动化（战斗监控与人物/召唤兽操作）
- src/wuhuan.py          五环任务自动化（购装备、领取、跟踪循环）
- src/kezhan.py          客栈任务自动化（洛阳客栈找掌柜、领取、跟踪循环）
- src/xiuluo.py          修罗任务自动化（灵兽村领取、跟踪、返程循环）
- src/wubei.py           五倍任务自动化（宝象国领取、队伍检查，战斗由战斗监控线程操作）
- src/task_manager.py    任务子进程管理（启停、升级强杀、日志回流）
- src/control_window.py  网页控制窗口（启动/停止/重置、任务多选与次数、总进度条、内嵌日志）
- src/schemes.py         配置方案管理（多套设置快照一键切换 + 导入导出）
- src/hotkey.py          全局老板键（默认 F8 一键启动/停止任务）
- src/select_window.py   多开窗口绑定选择弹窗（启动时多开且无有效绑定时弹出）
"""
import multiprocessing  # freeze_support：冻结打包下 spawn 子进程（任务/悬浮层）用
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
# ---- 外挂重型依赖引导（pylibs）----
# numpy/onnxruntime C 编译量巨大，打包时用 --nofollow-import-to 排除出 exe，
# 以 exe 同目录 pylibs/ 随包分发；冻结模式下插入 sys.path 供运行时导入。
# Nuitka 不设置 sys.frozen，注入的 __compiled__ 全局是冻结态的唯一可靠标志。
if "__compiled__" in globals():
    _pylibs = os.path.join(os.path.dirname(sys.executable), "pylibs")
    if os.path.isdir(_pylibs):
        sys.path.insert(0, _pylibs)
import pystray
from PIL import Image
import uiautomation as auto  # UIA 需要线程内 COM 初始化
import webview  # GUI 消息循环（窗口已在 ControlWindow.prepare 创建，这里直接 start）
from ascript.windows.system import R  # 程序上下文与资源路由
from ascript.windows.system import Runtime  # 生命周期驻留（防止程序自行退出）
from ascript.windows.ui import LicenseWindow  # 安全验证组件
from src.task_manager import TaskManager  # 任务子进程管理（启停/强杀/日志回流）
from src.control_window import (ControlWindow, bound_short_label,
                                load_selected_modes,
                                load_task_counts,
                                load_use_sheyaoxiang,
                                load_debug_overlay,
                                load_battle_actions,
                                load_high_perf,
                                load_auto_refill,
                                load_clear_screen)  # 网页控制窗口（启动/停止/重置、配置持久化）
from src import app_utils  # 通用工具（日志、高性能模式共享设施）
from src.app_utils import log, log_file_path  # 日志
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
from src import env_check  # 启动环境自检（DPI/屏幕/首帧统计，远程机排障）


def _setup_tray(icon_path, control_win):
    """托盘菜单：显示/隐藏控制窗口 + 彻底退出（Runtime.stop 触发退出清理）。

    首行展示当前绑定角色（服务器+角色名，禁用静态项仅作展示）；
    悬停提示同步附上角色信息。文案与窗口标题共用 bound_short_label。
    """
    try:
        img = Image.open(icon_path)
        label = bound_short_label()  # 绑定角色短文案（与窗口标题同源）
        items = []
        if label:
            items.append(pystray.MenuItem(f"角色：{label}", None, enabled=False))
        items += [
            pystray.MenuItem("显示/隐藏控制窗口",
                             lambda icon, item: control_win.toggle_visible(),
                             default=True),
            pystray.MenuItem("彻底退出", lambda icon, item: Runtime.stop()),
        ]
        tooltip = f"啄木鸟 - {label}" if label else "啄木鸟"
        tray = pystray.Icon("AScriptTray", img, tooltip, pystray.Menu(*items))
        tray.run()
    except Exception:
        pass


def _ensure_window_binding():
    """多开时弹框选择要绑定的游戏窗口（单窗口/已有有效绑定时跳过）。

    授权通过后、控制窗口创建前调用（webview 消息循环须独占一次，
    与控制窗口分属两次 start——同授权窗口路径）。绑定保存到
    KeyValue（bound_window_id = 句柄+账号 ID：句柄区分同账号多
    窗口，ID 跨重启延续匹配），find_main_window 优先返回绑定窗口
    （面板/任务子进程/悬浮层共用同一查找接缝）；绑定窗口不存在时
    下次启动自动重新弹出。
    """
    from src.select_window import SelectWindow  # 弹框按需导入
    wins = game.list_main_windows()
    if len(wins) <= 1:
        return  # 单窗口无需选择
    if game.find_bound_window() is not None:
        return  # 已有绑定且窗口存在
    log("[绑定] 检测到多个游戏窗口，弹出选择窗口", "SYS")
    sel = SelectWindow(wins)
    idx = sel.show(title="选择游戏窗口", block=True,
                   width=560, height=max(220, 120 + 56 * len(wins)))
    if idx is not None:
        game.save_bound_window(wins[idx])
        log(f"[绑定] 已绑定游戏窗口: {wins[idx].title}", "SYS")
    else:
        log("[绑定] 未选择窗口，本次按自动选择运行", "WARN")


def _sheyaoxiang_watchdog(win):
    """摄妖香定时续用线程（任务子进程内 daemon 线程，随进程强杀终止）。

    每分钟检查一次到期状态；到期时执行完整使用流程（与任务/战斗
    线程的点击经全局输入锁互斥），成功后记录时间。失败不记录时间，
    下一分钟自动重试直到成功。战斗中跳过——物品栏战斗中打不开
    （实测 2026-10-18 第 71 场战斗期间三次点击背包按钮均未打开面板，
    战斗 UI 覆盖按钮区）；跳过后续用重查间隔缩短为 10 秒，战斗链
    结束后尽快补用，缩短无摄妖香走路的遇敌窗口。
    修罗/五倍模式下非队长不续用（跟队无需自用，用户 2026-10-21）。
    有效期 1 小时，按 57 分钟续用提前 3 分钟覆盖，避免到期空挡遇敌
    （日志文案仍按「1 小时」表述）。使用流程内部还有多处战斗检查
    （bag.use_sheyaoxiang，watch 状态 + 单帧判定）：入口检查落在
    战斗开场动画期会漏检，流程内检查兜底（实测 2026-10-19 第 15 场
    战斗内整段流程执行完、摄妖香未生效却标记成功形成空挡遇敌）。
    """
    skip_logged = False  # 非队长跳过仅提示一次，防每分钟刷屏
    while True:
        time.sleep(60)
        try:
            if not app_utils.sheyaoxiang_due(win):
                skip_logged = False
                continue
            if app_utils.sheyaoxiang_skip_nonleader():  # 缺省取当前任务模式
                if not skip_logged:
                    log("[摄妖香] 定时续用: 非队长无需使用，跳过", "INFO")
                    skip_logged = True
                continue
            skip_logged = False
            if battle.watch_in_battle() or battle.is_in_battle(win):
                log("[摄妖香] 定时续用: 战斗中跳过，10 秒后重查", "INFO")
                time.sleep(10)
                continue
            # use_sheyaoxiang 内部整段持 _BAG_LOCK（与其他背包流程互斥），
            # 单次点击仍走 _INPUT_LOCK——不能在外层再包输入锁，否则与
            # 返程流程（先 _BAG_LOCK 后点击取 _INPUT_LOCK）锁序相反死锁
            ok, info = bag.use_sheyaoxiang(win)
            if ok:
                app_utils.sheyaoxiang_mark_used(win)
            log(f"[摄妖香] 定时续用: {info}", "SYS" if ok else "WARN")
        except Exception as e:
            log(f"[摄妖香] 定时续用异常: {e}", "WARN")


def _run_task_mode(win, mode):
    """执行单个任务模式的主循环（原 task_start 内按模式分支的收拢）。

    返回该模式循环的结束原因描述；「名望」为面板占位模式，暂无自动化
    实现，跳过并记日志（多任务场景下不阻塞后续任务）。
    """
    if mode == "玄枢轶闻":
        # 任务前置：退出队伍（队长解散/队员离开），单人状态执行。
        # 已有跟踪中的任务时跳过退队直接续跑（退队只服务于领取流程，
        # 续跑无需检查队伍信息——用户确认）
        if not task_tracker.has_task(win, xuanshu.TASK_TITLE):
            ok, info = team.leave_or_disband_team(win)
            log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")
        return xuanshu.run_loop(win)
    if mode == "五环":
        return wuhuan.run_loop(win)
    if mode == "客栈":
        # 任务前置：退出队伍（队长解散/队员离开），单人状态执行
        ok, info = team.leave_or_disband_team(win)
        log(f"[组队] 任务前置退队：{info}", "SYS" if ok else "WARN")
        return kezhan.run_loop(win)
    if mode == "修罗":
        return xiuluo.run_loop(win)
    if mode == "五倍":
        return wubei.run_loop(win)
    # 名望等其余模式：暂无自动化实现
    log(f"[任务] {mode} 暂无自动化实现，跳过", "WARN")
    return f"{mode} 暂无自动化实现，跳过"


def _overall_progress_text(win, modes):
    """总进度文案（任务切换间隙更新悬浮层；任务运行中由各模块更新
    自己的进度行）。次数上限为 0（不限）的任务无法度量，不计入分母；
    已完成超出上限按上限计（上限修改后百分比不破百）。"""
    counts = load_task_counts()
    total = sum(counts.get(m, 0) for m in modes if counts.get(m, 0) > 0)
    done = sum(min(app_utils.task_progress_done(win, m), counts.get(m, 0))
               for m in modes if counts.get(m, 0) > 0)
    return f"总进度 {done}/{total}" if total else "总进度 -"


def task_start():
    """业务逻辑入口函数（顶层函数，spawn 按引用 pickle）。

    由 TaskManager 在独立子进程的 daemon 线程中调用；
    停止/超限时进程被强杀，本函数内所有循环与线程随进程一并终止。
    """
    log("业务进程已建立，开始执行自动化任务...", "SYS")
    log(f"[日志] 本地日志文件: {log_file_path()}", "SYS")
    modes = load_selected_modes()  # 选中任务列表（按面板勾选，固定顺序执行）
    use_sheyaoxiang = load_use_sheyaoxiang()
    battle_actions = load_battle_actions()
    log(f"[模式] 选中任务: {'、'.join(modes) if modes else '无'}"
        + (f"（依次执行）" if modes else "（请先在面板勾选任务）"), "SYS")
    log(f"[配置] 使用摄妖香: {'开' if use_sheyaoxiang else '关'}", "SYS")
    log(f"[配置] 人物战斗动作: {battle_actions['char']}，召唤兽战斗动作: {battle_actions['beast']}", "SYS")
    log(f"[配置] 调试信息悬浮: {'开' if load_debug_overlay() else '关'}", "SYS")
    log(f"[配置] 战斗结束自动补血蓝: {'开' if load_auto_refill() else '关'}", "SYS")
    log(f"[配置] 启动清屏: {'开' if load_clear_screen() else '关'}", "SYS")
    # 高性能模式（通用设置开关）：置位共享设施，各任务模块据此启用
    # 固定位置缓存点击（主进程验证流程不受影响，恒为识别路径）
    app_utils.set_high_perf(load_high_perf())
    log(f"[配置] 高性能模式: {'开（固定位置缓存点击）' if app_utils.high_perf_on() else '关'}", "SYS")
    # 当前任务模式：任务主循环切换任务时更新，战斗线程/摄妖香线程
    # 按当前模式决策（多任务顺序执行下替代旧的单一模式读取）
    app_utils.set_current_task_mode(modes[0] if modes else "")
    # 调试悬浮层初始状态：任务队列与战斗配置（战斗场次/进度由各模块实时更新）
    debug_overlay.set_task("、".join(modes) if modes else "未选择任务")
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
            # 启动环境自检（只读）：DPI 缩放/显示器/系统版本/首帧黑帧
            # 统计——远程机功能异常时日志开头可直接定位差异（黑帧=截屏层
            # 失败，后续模板/OCR 全部失效），失败静默不影响启动
            env_check.log_environment(main)
        else:
            log("[检查] 未找到主窗口（游戏可能已最小化或未登录）", "WARN")
    else:
        msg = f"未检测到「{game.GAME_TITLE}」窗口，请确认游戏已启动"
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

        # 2.5 清屏（通用设置开关，默认开启）：
        #     Alt+6 屏蔽非队伍玩家 + Alt+5 屏蔽摊位，供后续任务干净执行。
        #     窗口句柄与角色均未变化时跳过（屏蔽状态在游戏侧持续，
        #     重复按键无收益且有误触风险；换窗口/换角色自动重新清屏）
        if logged_in and load_clear_screen():
            if app_utils.clear_screen_skip(main_win):
                log("[清屏] 窗口与角色未变化，跳过启动清屏", "INFO")
            else:
                ok, info = clear_screen.run_clear_screen(main_win)
                log(f"[清屏] {info}", "SYS" if ok else "WARN")
                if ok:
                    app_utils.clear_screen_mark(main_win)

        # 3. 摄妖香流程（开关开启且已登录；一次使用有效 1 小时，
        #    按 57 分钟续用提前覆盖，避免到期空挡遇敌）：
        #    上次使用距今不足 57 分钟才跳过（角色维度持久化，
        #    重启后不重复使用；日志文案仍按「1 小时」表述）
        if logged_in and use_sheyaoxiang:
            if not app_utils.sheyaoxiang_due(main_win):
                log("[摄妖香] 距上次使用不足 1 小时，跳过", "INFO")
            elif app_utils.sheyaoxiang_skip_nonleader():  # 缺省取当前任务模式
                log("[摄妖香] 启动检查: 非队长无需使用，跳过", "INFO")
            elif battle.watch_in_battle() or battle.is_in_battle(main_win):
                # 战斗中跳过：物品栏战斗中打不开（实测 2026-10-18），
                # 由定时续用线程在战斗结束后补用
                log("[摄妖香] 启动时处于战斗中，跳过本次使用，由定时续用补用", "INFO")
            else:
                ok, info = bag.use_sheyaoxiang(main_win)
                if ok:
                    app_utils.sheyaoxiang_mark_used(main_win)
                    log(f"[摄妖香] {info}", "SYS")
                else:
                    log(f"[摄妖香] {info}", "WARN")
        # 3.5 摄妖香定时续用：任务持续运行时每小时检查一次，到期自动
        #     补用（战斗中跳过——物品栏战斗中打不开，实测 2026-10-18
        #     第 71 场战斗期间三次点击均未打开背包；
        #     全局输入锁与任务/战斗线程互斥，不打断当前点击）
        if logged_in and use_sheyaoxiang:
            threading.Thread(
                target=_sheyaoxiang_watchdog, args=(main_win,),
                daemon=True, name="sheyaoxiang-watchdog").start()

        # 4. 战斗监控线程（已登录时启动，任何模式下自动执行人物/召唤兽战斗操作；
        #    启动日志由 watch_loop 内部输出，此处不重复打）
        if logged_in:
            battle_thread = threading.Thread(target=battle.watch_loop,
                                             args=(main_win,),
                                             daemon=True, name="battle-watch")
            battle_thread.start()

        # 5. 选中任务顺序执行：每个任务的主循环正常结束（次数上限/
        #    每日次数已满/活力不足等）后进入下一个任务，全部结束后
        #    任务进程退出，面板自动显示已结束（无需逐个任务重启）。
        #    任务前置（退队/清屏等）与循环逻辑由 _run_task_mode 按模式分发；
        #    当前任务模式经 app_utils 共享，战斗线程/摄妖香线程按当前模式决策。
        if logged_in and modes:
            for mode in modes:
                app_utils.set_current_task_mode(mode)
                debug_overlay.set_task(mode)
                log(f"[任务] 开始执行: {mode}", "SYS")
                info = _run_task_mode(main_win, mode)
                log(f"[任务] {mode} 循环结束：{info}", "SYS")
                # 任务切换间隙：悬浮层改显总进度（任务运行中由各模块
                # 更新自己的进度行），并稍候界面状态稳定
                debug_overlay.set_progress(_overall_progress_text(main_win, modes))
                time.sleep(2)
        elif not logged_in:
            log("[检查] 未登录，无可执行任务，任务结束（登录后可重新启动）", "WARN")
        else:
            log("[检查] 未勾选任何任务，任务结束（请在面板勾选任务后启动）", "WARN")

        # 6. 血蓝条检测（已登录时执行一次）
        if logged_in:
            status_bars.check_all_bars(main_win)

    # ---- 全部选中任务执行完毕：结束任务进程（面板自动显示已结束） ----
    debug_overlay.shutdown()


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
    # 冻结打包（Nuitka/PyInstaller）下 spawn 子进程（任务进程/悬浮层绘制）
    # 会重新执行本二进制，须调用 freeze_support 正确处理（源码环境为空操作）
    multiprocessing.freeze_support()

    # 悬浮层绘制进程入口：冻结打包下子进程以「二进制 + 本参数」启动
    # （见 debug_overlay._spawn_helper，exe 不支持解释器 -c 方式），
    # 跳过授权门直接进入绘制循环
    if "--overlay-helper" in sys.argv:
        from src.debug_overlay import _helper_main
        _helper_main()
        sys.exit(0)

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
        log(f"[授权] 未授权或授权已过期，请在弹出的窗口中输入卡密激活（{deny_info}）",
            "WARN")
        # 阻塞式激活窗口：窗口内 start_app 校验通过会关闭窗口并置 _return_value=True
        auth_win.show(title="软件授权激活", width=400, height=500)
    if not auth_win._return_value:
        print("授权未通过，程序退出。")
        sys.exit(0)
    license_info = _license_info(auth_win)
    print(f"授权通过（{license_info['status']}），启动主程序...")

    # ---- 多开窗口绑定选择（弹框；须在控制窗口创建前独占一次消息循环） ----
    _ensure_window_binding()

    # ---- 配置方案首次迁移：现有 config.json 设置落为「默认」方案 ----
    # schemes.json 已存在时空操作（读取路径同样有重建兜底）
    from src import schemes
    schemes.migrate_if_needed()

    # ---- 授权通过后装配主程序 ----
    # 必须放在 __main__ 内：Windows spawn 子进程会重新导入本模块但不执行此段
    # （解决打包后子进程重复启动问题）；同时保证授权窗口阶段 webview 窗口
    # 列表里只有授权窗口自己，其关闭即结束该次消息循环
    tm = TaskManager(task_start, task_stop)
    control_win = ControlWindow(tm, license_info=license_info)  # 构造内已接线 tm 的状态/日志回调
    control_win.prepare()            # 创建唯一窗口（uid='master'）

    # OCR 引擎预热（主进程，后台线程）：高级设置的验证/测试按钮在主进程
    # 跑 OCR，首次调用冷启动加载 det/cls/rec 模型实测阻塞约 30s
    # （2026-10-30 白龙马模板验证：点候选后第一次 OCR 卡 30s 才检测到
    # 对话框）。任务子进程另有 task_start 内预热；此处覆盖主进程。
    threading.Thread(target=game_map.warmup_ocr,
                     daemon=True, name="ocr-warmup").start()

    # 托盘：显示/隐藏控制窗口 + 彻底退出（Runtime.stop 触发退出清理）
    threading.Thread(target=_setup_tray,
                     args=(R.img("logo.ico"), control_win),
                     daemon=True, name="tray").start()

    # ---- 老板键：全局热键一键启动/停止任务（默认 F11，专用消息泵线程） ----
    # 状态/日志反馈复用 TaskManager 回调链（sync_status/add_logs 已跨线程
    # 工作），面板按钮与启动/停止状态自动同步
    from src import hotkey

    # 切换冷却（秒）：泵线程内回调执行期间按键会滞留队列（hotkey.py 已
    # 清队列），冷却再兜一层——3 秒内重复触发直接忽略，防连按/键盘粘连
    # 造成的「停完又启」（用户实测老板键无法停止的根因之一）
    _last_toggle_ts = [0.0]
    _TOGGLE_COOLDOWN = 3.0

    def _toggle_task():
        """老板键回调（消息泵线程）：运行中→停止，已停止→启动。"""
        now = time.time()
        if now - _last_toggle_ts[0] < _TOGGLE_COOLDOWN:
            app_utils.log("[老板键] 冷却期内忽略本次触发", "SYS")
            return
        _last_toggle_ts[0] = now
        running = tm.is_running()
        app_utils.log(f"[老板键] F11 触发（当前运行状态={running}）", "SYS")
        if running:
            result = tm.stop()
            app_utils.log(f"[老板键] 停止结果={result}", "SYS")
        else:
            result = tm.start()
            app_utils.log(f"[老板键] 启动结果={result}", "SYS")

    hotkey.register(_toggle_task)

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
