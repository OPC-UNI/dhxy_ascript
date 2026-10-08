"""调试信息悬浮层：用 DrawWindow 在游戏窗口上绘制任务/配置/进度信息。

控制窗口「通用设置-调试信息」开关打开时，在游戏窗口顶部中央绘制几行
文本：任务模式、战斗配置、战斗场次、任务进度。

架构（DrawWindow 隔离到独立绘制进程）：
- ascript 的 DrawWindow 基于 ctypes 回调 + 消息循环线程，在本项目
  Python 3.13 环境下实测：长跑（真实窗口截图并发 ~140 秒）会触发
  未恢复的 access violation 直接杀死整个进程；正常退出时也段错误
  （实测 139）。因此 DrawWindow 只在独立子进程中使用：
  1. 任务进程（本模块主逻辑）：维护状态（set_* 写入）、读取开关、
     每周期把绘制指令写入状态文件，并监督绘制进程存活（崩了自动重启）
  2. 绘制进程（_helper_main）：循环读取状态文件，用 DrawWindow 绘制
     ——崩溃只影响悬浮层本身，任务进程不受影响
  3. 绘制进程永不正常退出：任务结束时父进程 terminate（不经过
     Python 退出流程，避开 DrawWindow 退出崩溃）；父进程意外死亡时
     绘制进程靠心跳文件超时 os._exit 自尽，不残留孤儿
- 开关由主进程控制窗口写入 KeyValue（config.json）；KeyValue 是进程内
  缓存，任务子进程读不到主进程的更新，因此本模块直接重读 JSON 文件，
  开关切换后约一个轮询周期（2 秒）生效
- 绘制指令经状态文件传递（任务/绘制进程共享工作目录，与 KeyValue
  同路径解析），含行文本与屏幕坐标（由任务进程按游戏窗口 rect 计算）

位置与颜色常量集中在文件头部，实机效果不满意时直接调整。
"""
import atexit
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

from .app_utils import log

# ---- 绘制参数（游戏窗口相对坐标） ----
OVERLAY_FONT = 18           # 字体大小（像素）
OVERLAY_LINE_HEIGHT = 26    # 行距（像素）
OVERLAY_TOP = 30            # 首行距窗口顶部（避开顶部 HUD）
OVERLAY_BLOCK_W = 360       # 文本块宽度（用于水平居中）
OVERLAY_COLOR = 0x88FF00    # BGR 亮绿（DrawWindow 用 BGR 格式）
OVERLAY_INTERVAL = 2.0      # 任务进程写状态/监督周期（秒）
OVERLAY_HELPER_INTERVAL = 1.0  # 绘制进程读取状态周期（秒）
OVERLAY_SWITCH_KEY = "debug_overlay"  # 控制窗口开关的 KeyValue 键
STATE_FILENAME = "debug_overlay_state.json"  # 任务进程 → 绘制进程的状态文件
_HELPER_STALE_TIMEOUT = 15  # 状态文件超过该秒数未更新视为父进程已死（绘制进程自尽）

# ---- 状态（各模块通过 set_* 写入，任务进程内存） ----
_lock = threading.Lock()
_task = "任务未开始"
_team = ""
_bars = ""
_name = ""
_config = ""
_battle = ""
_progress = ""

# ---- 绘制进程管理 ----
_helper_proc = None
_helper_lock = threading.Lock()
_stopped = False  # shutdown 后监督线程退出标志


def set_task(text):
    """设置任务模式行（main.py 任务启动时写入）。"""
    global _task
    with _lock:
        _task = text


def set_team(text):
    """设置队伍信息行（身份 + 人数/种族/等级，五倍模块启动时一次性写入）。"""
    global _team
    with _lock:
        _team = text


def set_bars(text):
    """设置血蓝摘要行（每场战斗结束后的最近一次检测结果）。"""
    global _bars
    with _lock:
        _bars = text


def set_name(text):
    """设置角色名行（main.py 启动时从登录 Tab 读取）。"""
    global _name
    with _lock:
        _name = text


def set_config(char, beast):
    """设置战斗配置行（人物/召唤兽战斗动作）。"""
    global _config
    with _lock:
        _config = f"人物={char} 召唤兽={beast}"


def set_battle(no):
    """设置战斗场次行（battle.watch_loop 每场战斗进入时更新）。"""
    global _battle
    with _lock:
        _battle = f"战斗: 第 {no} 场"


def set_progress(text):
    """设置任务进度行（各任务模块每轮完成时更新）。"""
    global _progress
    with _lock:
        _progress = text


def _config_dir():
    """配置目录：与 KeyValue._determine_path 一致（当前目录，无写权限回退用户目录）。"""
    root = pathlib.Path(os.getcwd())
    if not os.access(root, os.W_OK):
        root = pathlib.Path.home() / ".app_data"
    return root


def _state_path():
    return _config_dir() / STATE_FILENAME


def _read_switch():
    """读取调试信息开关：绕开 KeyValue 进程内缓存，直接重读 JSON 文件。"""
    try:
        data = json.loads((_config_dir() / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get(OVERLAY_SWITCH_KEY))


def _write_state(state):
    """原子写入状态文件（先写临时文件再替换，防绘制进程读到半截内容）。"""
    path = _state_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _remove_state_file():
    try:
        _state_path().unlink(missing_ok=True)
    except Exception:
        pass


def _read_state():
    """绘制进程读取状态文件；不存在或超时未更新返回 None（父进程已死）。"""
    path = _state_path()
    try:
        if not path.exists() or time.time() - path.stat().st_mtime > _HELPER_STALE_TIMEOUT:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _spawn_helper():
    """拉起绘制进程（subprocess.Popen 方式）；返回 Popen 对象。

    任务子进程由框架以 daemonic 进程创建，multiprocessing 禁止其再
    派生子进程（daemonic processes are not allowed to have children）；
    用 subprocess 直接启动解释器运行 _helper_main 绕开该限制。
    工作目录设为项目目录（状态文件路径与父进程一致），无控制台窗口。
    """
    root = str(pathlib.Path.cwd())
    code = ("import sys; sys.path.insert(0, {root!r}); "
            "from src.debug_overlay import _helper_main; _helper_main()").format(root=root)
    return subprocess.Popen([sys.executable, "-c", code], cwd=root,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def _helper_main():
    """绘制进程入口：循环读取状态文件并用 DrawWindow 绘制。

    独立进程运行（见模块 docstring）；只在本进程内使用 DrawWindow，
    永不正常退出：父进程 terminate 回收，或心跳超时 os._exit 自尽。
    异常退出原因写入退出文件（硬崩溃时无文件），便于排查。
    """
    exit_reason = None
    try:
        # 字体抗锯齿：DrawWindow 的 CreateFontW 用 DEFAULT_QUALITY（0），
        # 小字号文字发虚；在 ctypes DLL 单例层把绘制质量强制为
        # ANTIALIASED（4）——DrawWindow 内部取的 gdi32 即该单例，
        # 仅影响本进程的 GDI 字体创建
        import ctypes as _ct
        _orig_create_font = _ct.windll.gdi32.CreateFontW

        def _aa_font(*args):
            # CreateFontW 第 13 个参数（quality）强制为 4（ANTIALIASED）；
            # 参数顺序：height,width,esc,ori,weight,italic,underline,
            # strikeout,charset,outprec,clipprec,quality,pitch,face
            return _orig_create_font(*args[:12], 4, *args[13:])

        setattr(_ct.windll.gdi32, "CreateFontW", _aa_font)
        from ascript.windows.ui.draw_window import DrawWindow
        drawn = False
        last_sig = None
        while True:
            try:
                state = _read_state()
                if state is None:
                    exit_reason = "心跳超时（父进程已死）"
                    break
                if state.get("enabled"):
                    # 变更驱动重绘：内容未变时跳过 clear+draw。悬浮层是
                    # WS_EX_LAYERED 独立置顶窗口，游戏重绘不会擦除它，
                    # 无需周期重绘；按秒重绘使 DrawWindow 的 GDI 操作量
                    # 无谓放大 1~2 个数量级，实测硬崩溃（access violation，
                    # 无退出原因文件）频率与操作量正相关（任务重负载 OCR
                    # 期间 ~15s 一崩）。内容仅在 set_* 时变化，延迟不变
                    # （轮询周期仍为 1s）
                    sig = (state.get("color", OVERLAY_COLOR),
                           state.get("font", OVERLAY_FONT),
                           tuple(tuple(ln) for ln in state.get("lines", [])))
                    if sig != last_sig:
                        DrawWindow.clear()
                        for x, y, text in state.get("lines", []):
                            DrawWindow.draw_text(x, y, text,
                                                 color=state.get("color", OVERLAY_COLOR),
                                                 font_size=state.get("font", OVERLAY_FONT))
                        last_sig = sig
                        drawn = True
                elif drawn:
                    DrawWindow.clear()
                    drawn = False
                    last_sig = None
            except Exception as e:
                exit_reason = f"绘制循环异常: {e}"
                break
            time.sleep(OVERLAY_HELPER_INTERVAL)
    except Exception as e:
        exit_reason = f"启动异常: {e}"
    finally:
        try:
            if exit_reason is not None:
                (_config_dir() / "debug_overlay_helper_exit.txt").write_text(
                    exit_reason, encoding="utf-8")
        except Exception:
            pass
        os._exit(0)


def _build_frame(win):
    """按当前状态生成绘制帧：[(x, y, 文本), ...]。

    行合并：角色 + 任务 + 任务次数一行；队伍信息（身份 + 人数/种族/
    等级）一行；配置、战斗场次、血蓝各一行。
    """
    with _lock:
        head = _task
        if _name:
            head = f"{_name} | {head}"
        if _progress:
            head = f"{head} {_progress}"
        lines = [head]
        if _team:
            lines.append(_team)
        lines.append(f"配置: {_config}")
        if _battle:
            lines.append(_battle)
        if _bars:
            lines.append(f"血蓝: {_bars}")
    x = win.rect.left + (win.rect.width - OVERLAY_BLOCK_W) // 2
    y = win.rect.top + OVERLAY_TOP
    return [(x, y + i * OVERLAY_LINE_HEIGHT, ln) for i, ln in enumerate(lines)]


def _supervise(win):
    """任务进程监督线程：周期写状态文件（兼作心跳）并维护绘制进程存活。

    状态文件每周期都写（含未启用标记），绘制进程以文件更新时间为
    心跳，超时（>15 秒）判定父进程已死并自尽；绘制进程崩溃时自动
    重启（崩溃只丢悬浮层，任务不受影响）。
    """
    global _helper_proc
    restart_count = 0
    while not _stopped:
        try:
            enabled = _read_switch()
            _write_state({"enabled": enabled,
                          "lines": _build_frame(win) if enabled else [],
                          "color": OVERLAY_COLOR, "font": OVERLAY_FONT})
            with _helper_lock:
                proc = _helper_proc
            if enabled and (proc is None or proc.poll() is not None):
                if proc is not None:
                    # 崩溃重启日志降噪：每 3 次记一条（绘制进程崩溃是
                    # DrawWindow 已知不稳定，自动重启为设计行为，
                    # 逐次记录刷屏无信息量）
                    restart_count += 1
                    if restart_count % 3 == 1:
                        log(f"[调试] 悬浮层绘制进程已退出，自动重启（累计第 {restart_count} 次）", "WARN")
                with _helper_lock:
                    _helper_proc = _spawn_helper()
        except Exception as e:
            log(f"[调试] 悬浮层异常: {e}", "WARN")
        time.sleep(OVERLAY_INTERVAL)


def start(win):
    """启动悬浮层监督线程（开关状态实时生效，切换后约 2 秒内更新）。"""
    global _stopped
    _stopped = False
    log("[调试] 调试信息悬浮层线程已启动（开关实时生效）", "SYS")
    threading.Thread(target=_supervise, args=(win,), daemon=True,
                     name="debug-overlay").start()


def shutdown():
    """任务结束时终止绘制进程并清理状态文件（任务各结束路径显式调用）。

    用 terminate 回收绘制进程——不经过 Python 退出流程，避开 DrawWindow
    的退出段错误（实测）；绘制进程若已自尽则直接清理状态文件。
    置停止标志让监督线程退出，防 shutdown 后再次拉起绘制进程。
    幂等，可重复调用。
    """
    global _helper_proc, _stopped
    _stopped = True
    try:
        with _helper_lock:
            proc = _helper_proc
            _helper_proc = None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
    except Exception:
        pass
    _remove_state_file()


atexit.register(shutdown)
