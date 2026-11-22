"""AScript Windows 自动化应用入口程序。

架构：控制窗口（ControlWindow）是唯一界面；任务在独立 spawn 子进程中运行
（TaskManager 管理启停、升级强杀与日志回流）；主线程跑 webview GUI 消息循环，
Runtime.wait 驻留线程防止程序自行退出（托盘「彻底退出」→ Runtime.stop 清理退出）。

模块划分（main.py 之外的代码均在 src/ 下）：
- src/core/app_utils.py       通用工具（日志、拟人化鼠标操作）
- src/device/game.py            游戏窗口操作（窗口查找、登录 Tab 监听）
- src/device/bag.py             背包操作（打开背包、物品检测与点击）
- src/device/battle.py          战斗自动化（战斗监控与人物/召唤兽操作）
- src/tasks/wuhuan.py          五环任务自动化（购装备、领取、跟踪循环）
- src/tasks/kezhan.py          客栈任务自动化（洛阳客栈找掌柜、领取、跟踪循环）
- src/tasks/xiuluo.py          修罗任务自动化（灵兽村领取、跟踪、返程循环）
- src/tasks/wubei.py           五倍任务自动化（宝象国领取、队伍检查，战斗由战斗监控线程操作）
- src/tasks/zhongzu.py         种族比武任务自动化（长安领取、比武大会跟踪、助威令返程）
- src/app/task_manager.py    任务子进程管理（启停、升级强杀、日志回流）
- src/app/control_window.py  网页控制窗口（启动/停止/重置、任务多选与次数、总进度条、内嵌日志）
- src/core/schemes.py         配置方案管理（多套设置快照一键切换 + 导入导出）
- src/app/hotkey.py          全局老板键（默认 F11，只停不起/全部停止）
- src/app/window_registry.py 多窗口注册表（自动枚举/勾选持久化/选中目标注入）
"""
import multiprocessing  # freeze_support：冻结打包下 spawn 子进程（任务/悬浮层）用
import os
import sys
import threading
import time
import traceback

# ---- OpenBLAS 线程数限制（必须早于任何 numpy 导入）----
# numpy 自带的 OpenBLAS 默认按逻辑核数建线程池，导入时每线程提交
# ~32MB（实测 16 核 ≈ 499MB、32 线程 ≈ 1GB），本项目没有任何 BLAS
# 调用（无 np.dot/matmul/linalg/einsum），这些缓冲永远不会被访问，
# 却计入系统提交额度：主进程 + 任务子进程各一份，多开再翻倍。低配/
# 关闭页面文件/多开机器上可能挤占提交空间（内存申请失败或被迫扩
# 页面文件）。固定为 1 线程后每进程提交 499MB→17MB、线程 19→4，
# 结果与速度不受影响（BLAS 未被调用）。必须放在 import ascript
# 之前：AScript SDK 会间接导入 numpy（实测导入其 windows.system 前
# 提交 24MB、导入后 521MB）。spawn 任务子进程重导入本模块同样生效。
# 直接赋值而非 setdefault：本程序不调用 BLAS，必须保证用户全局环境
# 里别的工具设的线程数不会把这份提交开销带回来。
os.environ["OPENBLAS_NUM_THREADS"] = "1"

# ---- 打包环境路径引导 ----
# 打包后本文件位于 site-packages/dhxy2/main.py，src 子包随包安装为
# dhxy2/src，不在模块搜索路径上；将本文件所在目录插入 sys.path，使
# `import src` 在打包环境与源码环境（项目根目录）行为一致。
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(_HERE, "src")):
    sys.path.insert(0, _HERE)

# ---- 资源根目录引导（AScript SDK R 定位，必须早于任何 src/ascript 导入）----
# ascript >= 1.3.29 的 R.get_root() 非冻结分支优先读 __main__.__file__：
# Nuitka onefile 下该值指向 %TEMP% 解压目录，而 res/（面板 HTML、图片模板、
# OCR 模型）是随包放在 exe 旁的外部目录、不在 exe 内，结果全部指向缺失
# 路径——2026-11-18 v2.13.0 升级实测：控制面板白屏 ERR_FILE_NOT_FOUND，
# 模板/OCR 模型同时失效。SDK 提供 ASCRIPT_PROJECT_ROOT 显式指定工程根
# （R 首次调用即缓存 _BASE_DIR，src 模块导入期就会调用 R.res，故必须提前
# 设置；spawn 任务子进程重导入本模块时同样生效）。
if "__compiled__" in globals():
    _PROJECT_ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    _PROJECT_ROOT = _HERE
os.environ["ASCRIPT_PROJECT_ROOT"] = _PROJECT_ROOT

# ---- 自动更新助手入口（必须在导入 ascript/webview 等重依赖之前）----
# 更新助手是当前 exe 复制到 %TEMP% 的副本，以 --apply-update 启动：
# 只依赖标准库，快速进入「等主进程退出 → 覆盖 → 重启」流程。
# 放在 __main__ 守卫内：spawn 任务子进程重导入本模块（__mp_main__）时不执行。
if __name__ == '__main__' and "--apply-update" in sys.argv:
    from src.app.updater import apply_update_entry
    sys.exit(apply_update_entry(sys.argv))

# ---- 启动恢复：处理断电/强杀导致的中断更新（只在主进程执行）----
_UPDATE_RECOVERY = None
if __name__ == '__main__':
    try:
        from src.app import updater as _updater_boot
        from src import __version__ as _boot_version
        _UPDATE_RECOVERY = _updater_boot.recover_if_needed(_boot_version)
        if _UPDATE_RECOVERY:
            print("[更新] 启动恢复：%s" % (_UPDATE_RECOVERY,))
    except Exception:
        pass

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
from src.app.task_manager import TaskManager  # 任务子进程管理（启停/强杀/日志回流）
from src.app.control_window import ControlWindow  # 网页控制窗口（启动/停止/重置）
from src.device.binding import bound_short_label  # 绑定角色短文案
from src.core.settings import (BATTLE_ONLY_MODE,  # 设置读取（非 UI 层）
                               load_selected_modes,
                               load_task_counts,
                               load_use_sheyaoxiang,
                               load_debug_overlay,
                               load_battle_actions,
                               load_high_perf,
                               load_auto_refill,
                               load_clear_screen,
                               load_silent_mode,
                               load_battle_only_auto_rejoin)
from src.core import app_utils  # 通用工具（日志、高性能模式共享设施）
from src.core.app_utils import log, log_file_path  # 日志
# SDK 补丁：Window.capture 的 win32ui.error 逃逸兜底（详见 sdk_patches）。
# 必须在任务子进程启动前生效；spawn 子进程重导入本模块时同样执行。
from src.core.sdk_patches import install_window_capture_guard
install_window_capture_guard(log_fn=app_utils.log)
from src.device import game  # 游戏窗口操作
from src.core import user_data  # 角色数据目录（data/<角色ID>/）
from src.device import bag  # 背包操作
from src.device import battle  # 战斗自动化（战斗监控与人物/召唤兽操作）
from src.app.task_registry import run_task_mode  # 任务模式注册表（前置钩子 + 主循环）
from src.device import team  # 组队功能（仅战斗自动归队）
from src.device import status_bars  # 人物/召唤兽血蓝条检测
from src.core import debug_overlay  # 调试信息悬浮层（游戏窗口上绘制任务/配置/进度）
from src.device import clear_screen  # 启动清屏（Alt+6 屏蔽玩家 + Alt+5 屏蔽摊位）
from src.device import map as game_map  # 地图操作（OCR 引擎预热等）
from src.app import env_check  # 启动环境自检（DPI/屏幕/首帧统计，远程机排障）
from src.core.license_client import (LicenseClient,  # 雪松授权（设备绑定）
                                     LicenseError)
from src.app.license_config import (LICENSE_SERVER_URL, LICENSE_PRODUCT_NAME,
                                    LICENSE_PUBLIC_KEY_PEM)


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
            pystray.MenuItem("全部停止", lambda icon, item: control_win.stop_all()),
            pystray.MenuItem("彻底退出", lambda icon, item: Runtime.stop()),
        ]
        tooltip = f"啄木鸟 - {label}" if label else "啄木鸟"
        tray = pystray.Icon("AScriptTray", img, tooltip, pystray.Menu(*items))
        tray.run()
    except Exception:
        pass


# 空闲期兜底：任务持续运行但该时长内无「点击追踪寻路」（连续回城符/
# 地图点击赶路、流程卡在无寻路环节）时，寻路期续用没有触发机会——
# 到期后空闲期补用，防非寻路走路（回城/地图点击）无香遇敌。正常轮次
# 内点击追踪间隔远小于此值，不会与常规寻路期续用重叠。
_SHEYAOXIANG_IDLE_FALLBACK_MINUTES = 10


def _sheyaoxiang_idle_fallback(win):
    """摄妖香空闲期兜底续用线程（任务子进程内 daemon 线程，随进程强杀终止）。

    每 60 秒检查：到期且距上次点击追踪寻路 ≥ 10 分钟时走完整门控
    使用流程（bag.use_sheyaoxiang_if_due：开关/到期/非队长/战斗四重
    门，成功记时间）。与寻路期续用（workflows.follow._maybe_sheyaoxiang）双
    通道互补：寻路期覆盖常规轮次、空闲期兜底覆盖无寻路窗口。
    pass 期间跳过（队员 Tab 切换中，下分钟再查）。失败静默，
    下分钟重试。
    """
    from src.device import tab_group  # 延迟导入：pass_active 抑制
    while True:
        time.sleep(60)
        try:
            if tab_group.pass_active:
                continue  # pass 进行中（当前可能在队员 Tab）：下分钟再查
            if not tab_group.on_leader_screen(win):
                continue  # 用户手动切到队员 Tab：不能在队员身上用摄妖香
            if not app_utils.sheyaoxiang_idle_due(
                    win, _SHEYAOXIANG_IDLE_FALLBACK_MINUTES):
                continue
            ran, info = bag.use_sheyaoxiang_if_due(win)
            if ran:
                log(f"[摄妖香] 空闲期兜底续用: {info}", "SYS")
        except app_utils.UserAbort as e:
            log(f"[摄妖香] {e}，空闲期兜底线程退出", "WARN")
            break
        except Exception as e:
            log(f"[摄妖香] 空闲期兜底异常: {e}", "WARN")


def _battle_only_loop(win):
    """仅战斗模式：只启动战斗监控线程并驻留任务进程。

    与其它任务互斥（面板禁用 + 保存逻辑双重保证），不做任何任务侧
    操作：跳过清屏/摄妖香/队伍检查/任务循环/血蓝条检查，游戏界面
    零干预（组队跟战时队长侧操作不受干扰）。战斗线程按通用设置
    自动战斗/战后补血蓝/巫医对话框，持续运行至用户停止（TaskManager
    强杀进程，战斗线程随进程一并终止）；战斗线程自行退出（如
    UserAbort 人工接管）时任务自然结束。
    """
    debug_overlay.start(win)
    debug_overlay.set_phase("准备中")  # 看板阶段：启动检查期间显示
    tab = game.read_login_tab(win)
    if tab == game.GAME_TITLE:
        log("[检查] 当前未登录（Tab 显示为大话西游2经典版）", "WARN")
    elif tab:
        log(f"[检查] 当前登录角色: {tab}", "SYS")
        debug_overlay.set_name(tab)  # 悬浮层角色名
    else:
        log("[检查] Tab 读取为空（游戏加载中？）", "WARN")
    logged_in = bool(tab and tab != game.GAME_TITLE)
    if not logged_in:
        log("[检查] 未登录，无可执行任务，任务结束（登录后可重新启动）", "WARN")
        return
    log("[任务] 仅战斗模式：仅运行战斗监控线程"
        "（跳过清屏/摄妖香/队伍检查/任务循环），持续至手动停止", "SYS")
    # 当前任务模式 = 仅战斗：战斗线程的盒子判定（battle._handle_hezi 的
    # 「仅战斗-自动抢盒子」开关）按模式决策。task_start 已按 modes[0]
    # 置位，此处显式再置一次使本函数自包含（不依赖调用方前置）
    app_utils.set_current_task_mode(BATTLE_ONLY_MODE)
    # 自动归队（仅战斗设置开关，默认开；用户 2026-11-07 要求）：仅战斗
    # 不判定身份，死亡落轮回司即开队伍面板点「归队」——队长身份由
    # maybe_rejoin 自动跳过（队长面板无归队按钮）。轮询与五倍/修罗
    # 非队长保持循环同款：3s 位置轮询 + 归队尝试后 30s 放慢
    auto_rejoin = load_battle_only_auto_rejoin()
    log(f"[任务] 仅战斗自动归队: "
        f"{'开（死亡落轮回司自动点归队回队长）' if auto_rejoin else '关'}", "SYS")
    from src.device import tab_group  # 延迟导入：pass_active 抑制（与 162 行同款）
    battle_thread = threading.Thread(target=battle.watch_loop,
                                     args=(win,), daemon=True,
                                     name="battle-watch")
    battle_thread.start()
    next_rejoin_at = 0.0
    while battle_thread.is_alive():  # 驻留直至停止或战斗线程退出
        time.sleep(1)
        app_utils.raise_if_aborted()  # 顶号：战斗线程置标志后主线程退出
        if not auto_rejoin or tab_group.pass_active:
            continue
        now = time.time()
        if now < next_rejoin_at:
            continue
        next_rejoin_at = now + team.REJOIN_POLL_INTERVAL
        # 战斗判定只在轮询到期时执行（is_in_battle 是截窗+模板匹配）
        if battle.is_in_battle(win):
            continue
        try:
            handled, desc = team.maybe_rejoin(win, prefix="[仅战斗]")
            if handled:
                log(f"[仅战斗][归队] {desc}",
                    "SYS" if desc.startswith("已归队") else "WARN")
                next_rejoin_at = time.time() + team.REJOIN_RETRY_INTERVAL
        except Exception as e:
            log(f"[仅战斗][归队] 归队检查异常: {e}", "WARN")
    log("[任务] 战斗监控线程已退出，仅战斗任务结束", "WARN")


def _overall_progress_text(win, modes):
    """总进度文案（任务切换间隙更新悬浮层；任务运行中由各模块更新
    自己的进度行）。次数上限为 0（不限）的任务无法度量，不计入分母；
    已完成超出上限按上限计（上限修改后百分比不破百）。"""
    counts = load_task_counts()
    total = sum(counts.get(m, 0) for m in modes if counts.get(m, 0) > 0)
    done = sum(min(app_utils.task_progress_done(win, m), counts.get(m, 0))
               for m in modes if counts.get(m, 0) > 0)
    return f"总进度 {done}/{total}" if total else "总进度 -"


def _run_selected_tasks(win, modes):
    """按面板顺序执行选中的任务（注册表分发 + 悬浮层/进度更新）。

    单独成函数便于测试调用点：注册表映射正确但 main 实参颠倒/漏调用
    时，单测可直接捕获 (win, mode)。
    """
    for mode in modes:
        app_utils.raise_if_aborted()  # 顶号等会话中止（战斗线程置位）：不再启动下一任务
        app_utils.set_current_task_mode(mode)
        debug_overlay.set_task(mode)
        log(f"[任务] 开始执行: {mode}", "SYS")
        info = run_task_mode(win, mode)
        log(f"[任务] {mode} 循环结束：{info}", "SYS")
        # 任务切换间隙：悬浮层改显总进度（任务运行中由各模块
        # 更新自己的进度行），并稍候界面状态稳定
        debug_overlay.set_progress(_overall_progress_text(win, modes))
        time.sleep(2)


def task_start(role_id=None, hwnd=None, title=None):
    """业务逻辑入口函数（顶层函数，spawn 按引用 pickle）。

    由多任务管理器在独立子进程的 daemon 线程中调用；
    停止/超限时进程被强杀，本函数内所有循环与线程随进程一并终止。

    多窗口（2026-11-22）：接收目标角色/窗口句柄，先在本进程固化再
    进入业务体——此后 find_main_window / current_role_id / 角色设置 /
    日志文件全部锁定该窗口角色，不会回退或漂移到其他窗口。

    顶层兜底（2026-10-31 物理鼠标位于屏幕角落触发 fail-safe、任务
    线程裸崩且堆栈只留在控制台的教训）：
    - app_utils.UserAbort（用户人工接管鼠标）：记 WARN 后正常返回，
      wrapper 检测线程结束执行 task_stop 善后——干净中止而非异常退出；
    - 其余未捕获异常：完整堆栈先落盘（log 双写日志文件）再重抛，
      控制台与日志文件均保留现场。
    """
    if role_id or hwnd:
        user_data.set_process_role(role_id)
        game.set_process_target(role_id=role_id, hwnd=hwnd,
                                title=title or "", strict=True)
        try:
            log(f"[进程] 多窗口目标：角色 {role_id or '-'}，"
                f"句柄 {hwnd or '-'}，标题 {title or '-'}", "SYS")
        except Exception:
            pass
    try:
        _task_start_body()
    except app_utils.SessionAbort:
        # 顶号等会话级中止：abort_session 已打 ERROR（含原因），
        # 这里只需干净返回——wrapper 执行 task_stop 后进程自然退出
        pass
    except app_utils.UserAbort as e:
        log(f"[系统] {e}，任务中止（鼠标移出屏幕角落后可重新启动）", "WARN")
    except Exception:
        log("[系统] 任务线程未捕获异常，完整堆栈如下:\n"
            + traceback.format_exc(), "ERROR")
        raise


def _task_start_body():
    """task_start 逻辑体（收拢为函数以便顶层统一兜底，见 task_start）。"""
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
    # 静默操作（通用设置开关，默认开启）：全部点击走窗口消息通道、
    # 鼠标移动变空操作，游戏窗口可不前台；键盘/激活类操作按
    # app_utils.keyboard_ok 门控（游戏已在前台才执行，不再激活）
    app_utils.set_silent_mode(load_silent_mode())
    log(f"[配置] 静默操作: {'开（消息点击，不占用鼠标）' if app_utils.silent_mode_on() else '关（物理鼠标）'}", "SYS")
    # 当前任务模式：任务主循环切换任务时更新，战斗线程/寻路期摄妖香
    # 续用按当前模式决策（多任务顺序执行下替代旧的单一模式读取）
    app_utils.set_current_task_mode(modes[0] if modes else "")
    # 调试悬浮层初始状态：任务队列与战斗配置（战斗场次/进度由各模块实时更新）
    debug_overlay.set_task("、".join(modes) if modes else "未选择任务")
    debug_overlay.set_config(battle_actions["char"], battle_actions["beast"])

    # OCR 引擎预热：区域 + 2x 快引擎（首次调用加载 det/rec 模型，一次性
    # 约 1s），提前完成避免拖慢任务首轮识别。全帧引擎只服务兜底路径
    # （2026-11-13~20 日志实测 0 次触发），首次使用 ocr_image 时懒加载。
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
        # 仅战斗模式：与其它任务互斥，启动后只跑战斗监控线程——不做
        # 清屏/摄妖香/队伍检查等任何任务侧操作，游戏界面零干预
        if BATTLE_ONLY_MODE in modes:
            _battle_only_loop(main_win)
            debug_overlay.shutdown()
            return
        # 调试信息悬浮层：在游戏窗口上绘制任务/配置/进度（开关在控制窗口
        # 切换后实时生效；开关关闭时不创建绘制窗口，行为与未启用一致）
        debug_overlay.start(main_win)
        debug_overlay.set_phase("准备中")  # 看板阶段：启动检查/清屏期间显示

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
                # 由寻路期续用（点击任务追踪后）在战斗结束后补用
                log("[摄妖香] 启动时处于战斗中，跳过本次使用，由寻路期续用补用", "INFO")
            else:
                ok, info = bag.use_sheyaoxiang(main_win)
                if ok:
                    app_utils.sheyaoxiang_mark_used(main_win)
                    log(f"[摄妖香] {info}", "SYS")
                else:
                    log(f"[摄妖香] {info}", "WARN")
        # 3.5 摄妖香持续续用双通道：①寻路期——任务每轮点击任务追踪后
        #     （寻路期安全窗口，无弹框遮挡）到期自动补用，见
        #     workflows.follow._maybe_sheyaoxiang；②空闲期兜底——任务运行但
        #     10 分钟无寻路（连续回城符/地图赶路、流程卡顿）时到期补用。
        #     替代原任意时机的定时续用线程（任务领取/巫医弹框阶段
        #     背包交互被挡住、储物格切换无效，用户 2026-11-04）
        if logged_in and use_sheyaoxiang:
            threading.Thread(
                target=_sheyaoxiang_idle_fallback, args=(main_win,),
                daemon=True, name="sheyaoxiang-idle-fallback").start()

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
        #    任务前置（退队等）与循环逻辑由 app.task_registry.run_task_mode 分发；
        #    当前任务模式经 app_utils 共享，战斗线程/寻路期续用按当前模式决策。
        if logged_in and modes:
            _run_selected_tasks(main_win, modes)
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


if __name__ == '__main__':
    # 冻结打包（Nuitka/PyInstaller）下 spawn 子进程（任务进程/悬浮层绘制）
    # 会重新执行本二进制，须调用 freeze_support 正确处理（源码环境为空操作）
    multiprocessing.freeze_support()

    # 悬浮层绘制进程入口：冻结打包下子进程以「二进制 + 本参数」启动
    # （见 debug_overlay._spawn_helper，exe 不支持解释器 -c 方式），
    # 跳过授权门直接进入绘制循环
    if "--overlay-helper" in sys.argv:
        from src.core.debug_overlay import _helper_main
        _helper_main()
        sys.exit(0)

    # ---- 同一安装目录单实例锁（2026-11-22；不同目录允许并行） ----
    # 第二实例：触发「显示面板」事件后退出，不共享 config/data/logs/热键。
    from src.core import instance_lock
    _instance_lock = instance_lock.acquire()
    if _instance_lock is None:
        instance_lock.signal_show()
        log("[系统] 同一安装目录已有实例在运行，本次启动退出（已请求显示已有面板）", "WARN")
        sys.exit(0)

    # ---- 授权守门员：雪松 License Manager（设备绑定 + 在线校验）----
    # 本地许可证优先（RSA-PSS 验签 + 状态/有效期/硬件指纹），每次启动和
    # 点「启动」任务前在线心跳；未激活不退出主程序——激活页输入卡密即可，
    # 任务启动由 _license_gate 拦截（面板本身不含受保护功能）。
    debug_auth = os.environ.get("ASCRIPT_DEBUG_AUTH") == "1"  # 授权联调：强制未激活
    startup_logs = []  # 启动阶段授权日志（面板首屏加载后补投抽屉）
    # 资源根目录（res/ 所在目录）：包装路径解析异常时（如 SDK R 改用
    # __main__.__file__ 导致指向 %TEMP% 解压目录），日志一眼定位
    _root_line = f"[资源] 工程根目录: {_PROJECT_ROOT}"
    log(_root_line, "SYS")
    startup_logs.append(_root_line)
    if _UPDATE_RECOVERY:
        # 中断更新恢复结果（正常升级成功走 last_result，不在这里）
        _line = f"[更新] 启动恢复：{_UPDATE_RECOVERY}"
        log(_line, "SYS" if _UPDATE_RECOVERY.get("status") == "completed" else "WARN")
        startup_logs.append(_line)

    from src import __version__ as _app_version
    # 产品公钥内嵌在 license_config（编译进 exe），不读安装目录下的明文
    # pem——外置文件可被替换后自签许可证（2026-11-20 实测）。
    license_client = LicenseClient(
        LICENSE_SERVER_URL, LICENSE_PUBLIC_KEY_PEM,
        product_name=LICENSE_PRODUCT_NAME, app_version=_app_version)
    license_info = license_client.info()
    if debug_auth:
        line = "[授权] 调试模式：跳过在线校验，按未激活进入（可在激活页激活）"
        log(line, "WARN")
        startup_logs.append(line)
    else:
        try:
            license_info = license_client.check(online=True)
            line = f"[授权] 校验通过：{license_info['status']}"
            if license_info.get("expire"):
                line += f"，到期 {license_info['expire']}"
            if license_info.get("offline"):
                line += "（网络异常，使用本地许可证）"
            log(line, "SYS")
            startup_logs.append(line)
            days_left = license_info.get("days_left")
            if days_left is not None and 0 <= days_left <= 7:
                remind = f"[授权] 提醒：卡密将在 {days_left} 天后到期，请及时续费"
                log(remind, "SYS")
                startup_logs.append(remind)
        except LicenseError as e:
            license_info = license_client.info()
            deny_line = f"[授权] 未通过：{e}（请在激活页输入卡密激活）"
            log(deny_line, "WARN")
            startup_logs.append(deny_line)
    pass_line = (f"[授权] 当前状态：{license_info.get('status', '未知')}"
                 "，进入主程序")
    log(pass_line, "SYS")
    startup_logs.append(pass_line)

    # ---- 多窗口注册表：自动枚举 + 旧绑定迁移 + 选中目标注入 ----
    # 2026-11-22 起不再弹「选择游戏窗口」框：所有主窗口都进舰队看板，
    # 勾选/选中按角色持久化；旧 bound_window_id 的角色迁移为默认勾选+选中。
    from src.app import window_registry
    window_registry.ensure_initialized()

    # ---- 角色数据目录迁移 + 按角色切换方案（2026-11-11） ----
    from src.core import schemes
    migration = {"migrated": False, "role": user_data.DEFAULT_ROLE,
                 "last_active_role": None}
    try:
        migration = user_data.migrate_if_needed()
        if migration.get("migrated") or migration.get("settings_created"):
            # 刚迁移出角色设置：活动方案以角色 settings.json 为准
            schemes.bootstrap_current_role()
        else:
            schemes.ensure_role_schemes()
        role = migration.get("role") or user_data.current_role_id(refresh=True)
        schemes.activate_role(migration.get("last_active_role"), role)
        user_data.set_last_active_role(role)
        log(f"[数据] 角色数据目录: data/{role}"
            + ("（已迁移旧版数据）" if migration.get("migrated") else ""), "SYS")
        if migration.get("settings_created"):
            log(f"[数据] 已建立角色设置 data/{role}/settings.json", "SYS")
    except Exception as e:
        # 迁移只复制不移动，根目录旧文件仍在；失败时继续启动并按需
        # 重建当前角色默认数据（不因数据目录问题拒绝启动）。
        log(f"[数据] 角色数据初始化失败，按旧版根目录配置继续: {e}", "WARN")

    # ---- 旧版成就补导（迁移时复制失败/目的文件为空时的一次性修复） ----
    if not migration.get("migrated") and migration.get("stats_imported"):
        log(f"[数据] 已补导旧版成就到 "
            f"data/{migration.get('role')}/task_stats.json", "SYS")

    # ---- config.json 旧角色字段两阶段清理 ----
    # 迁移当次不动 config（回滚安全）；下一次启动备份后删除。
    if not migration.get("migrated"):
        try:
            pruned = user_data.prune_legacy_config(
                skip_settings=bool(migration.get("settings_created")))
            if pruned.get("pruned"):
                keys = pruned.get("keys")
                count = len(keys) if isinstance(keys, (list, tuple)) else 0
                log(f"[数据] 已清理 config.json 旧字段 {count} 项"
                    f"（备份 data/_legacy_config.json）", "SYS")
        except Exception as e:
            log(f"[数据] config.json 旧字段清理失败（下次启动重试）: {e}", "WARN")

    # ---- 根目录旧版 schemes.json / task_stats.json 归档 ----
    if not migration.get("migrated") and not migration.get("settings_created"):
        try:
            archived = user_data.archive_legacy_files()
            if archived.get("archived"):
                log("[数据] 已将旧版 schemes.json / task_stats.json "
                    "归档到 data/_legacy/", "SYS")
        except Exception as e:
            log(f"[数据] 旧版文件归档失败（下次启动重试）: {e}", "WARN")

    # ---- 装配主程序 ----
    # 必须放在 __main__ 内：Windows spawn 子进程会重新导入本模块但不执行此段
    # （解决打包后子进程重复启动问题）
    # 静默操作（通用设置）：主进程按配置置位（验证/诊断流程的点击通道；
    # 任务子进程在 task_start 内自行置位）
    app_utils.set_silent_mode(load_silent_mode())
    log(f"[配置] 静默操作: {'开（消息点击，不占用鼠标）' if app_utils.silent_mode_on() else '关（物理鼠标）'}", "SYS")
    tm = TaskManager(task_start, task_stop)

    def _license_gate():
        """启动任务前授权校验（按钮与老板键共用）。

        本地验签 + 在线心跳：未激活/过期/撤销/设备不匹配拦截启动；
        网络异常时本地许可证仍有效则放行（沿用本地授权，不误拦）。
        返回面板所需的结构化结果。
        """
        try:
            info = license_client.check(online=True)
            return {"ok": True, "msg": "", "info": info}
        except LicenseError as e:
            return {"ok": False, "msg": e.message,
                    "info": license_client.info()}

    control_win = ControlWindow(tm, license_info=license_info,
                                license_gate=_license_gate,
                                license_client=license_client)  # 构造内已接线 tm 的状态/日志回调
    if startup_logs:
        control_win.add_logs(startup_logs)  # 页面未就绪前暂存，首屏加载补投抽屉
    # ---- 清理已销毁的预置窗口（授权窗口/多开绑定选择弹窗）----
    # pywebview 全局窗口列表只增不减：show(block=True) 返回后窗口已销毁但仍
    # 留在列表，下一次 webview.start 会把 windows[0]（已销毁窗口）当主窗口
    # 重建、真正的主窗口被放到子线程创建——实测表现为主窗口启动后自动最小化
    # （2026-11-08 授权窗口路径首次走通即复现）。主窗口创建前清空即可恢复
    # 单窗口启动语义（选择弹窗路径同理）。
    webview.windows.clear()
    # 面板重设计后默认尺寸 720×560（侧边导航 + 运行看板，见 docs/ui-redesign.md）
    control_win.prepare(width=720, height=560)            # 创建唯一窗口（uid='master'）
    # 第二实例启动时唤起的监听（主实例隐藏到托盘也能拉回面板）
    instance_lock.start_show_listener(_instance_lock, control_win.show_window)

    # 自动检查更新：后台线程、启动 8 秒后执行；24 小时节流，
    # 不阻塞启动，也不抢占 OCR 预热。手动检查在系统页「关于」。
    control_win.schedule_update_check()

    # OCR 引擎预热（主进程，后台线程）：高级设置的验证/测试按钮在主进程
    # 跑 OCR，首次调用冷启动加载 det/rec 模型（本地模型实测约 1s；
    # 2026-10-30 曾因联网下载模型卡 30s）。预热范围 = 区域 + 2x 快引擎；
    # 全帧引擎只服务任务侧兜底路径，首次使用时懒加载。
    # 任务子进程另有 task_start 内预热；此处覆盖主进程。
    threading.Thread(target=game_map.warmup_ocr,
                     daemon=True, name="ocr-warmup").start()

    # 托盘：显示/隐藏控制窗口 + 彻底退出（Runtime.stop 触发退出清理）
    threading.Thread(target=_setup_tray,
                     args=(R.img("logo.ico"), control_win),
                     daemon=True, name="tray").start()

    # ---- 老板键：全局热键一键启动/停止任务（默认 F11，专用消息泵线程） ----
    # 状态/日志反馈复用 TaskManager 回调链（sync_status/add_logs 已跨线程
    # 工作），面板按钮与启动/停止状态自动同步
    from src.app import hotkey

    # 切换冷却（秒）：泵线程内回调执行期间按键会滞留队列（hotkey.py 已
    # 清队列），冷却再兜一层——3 秒内重复触发直接忽略，防连按/键盘粘连
    # 造成的「停完又启」（用户实测老板键无法停止的根因之一）
    _last_toggle_ts = [0.0]
    _TOGGLE_COOLDOWN = 3.0

    def _toggle_task():
        """老板键回调（消息泵线程）：只停不起——运行中→全部停止。

        多窗口（2026-11-22 用户确认）：老板键是 panic 停止，不能把一堆
        后台窗口误拉起来；启动一律从面板操作。
        """
        now = time.time()
        if now - _last_toggle_ts[0] < _TOGGLE_COOLDOWN:
            app_utils.log("[老板键] 冷却期内忽略本次触发", "SYS")
            return
        _last_toggle_ts[0] = now
        running = tm.is_running()
        starting = bool(getattr(control_win, "is_fleet_starting", lambda: False)())
        app_utils.log(
            f"[老板键] F11 触发（运行状态={running}，全部启动在途={starting}）", "SYS")
        if running or starting:
            # stop_all 先取消未完成的全量启动再杀 worker（防错峰线程继续拉起）
            result = control_win.stop_all()
            app_utils.log(f"[老板键] 停止结果={result}", "SYS")
        else:
            app_utils.log("[老板键] 已停止状态：老板键只停不起，请从面板启动", "SYS")

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

    # GUI 循环结束（窗口已销毁）后收尾：兜底清理消息服务与任务进程并退出
    control_win.stop_message_service()
    tm.stop()
    instance_lock.release(_instance_lock)
    Runtime.stop()
    print("[系统] 程序已正常退出。")
