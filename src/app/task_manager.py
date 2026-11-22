"""多任务进程管理：每个游戏窗口一个 worker 子进程，日志经队列回流主进程。

多窗口（2026-11-22，见 docs/multi-window.md）：
- 一个 worker = 一个窗口/角色；各自独立进程，某个窗口崩溃/关闭只影响
  自己（进程级全局状态天然隔离：tab_group/pass/team/battle/OCR 引擎）。
- 目标角色/窗口句柄作为 spawn 参数传入，子进程固化后不再解析其他窗口。
- 停止按窗口独立：multiprocessing.Pipe 发停止信号 → join 3s →
  terminate → join 1s → kill（沿用原 TaskManager 时序）。
- 日志队列条目带角色标签，主进程按角色前缀转发面板/控制台。
- 跨实例同角色互斥：worker 持命名互斥体，另一实例同角色启动被拒。
- 窗口看护每个 worker 独立：句柄失效/窗口最小化都只停该窗口。

兼容旧单窗口路径：start() 无参时按当前绑定窗口解析目标；stop() 无参
= 停止全部；is_running() 无参 = 是否有任一窗口在跑。
"""
import multiprocessing
import queue
import re
import sys
import threading
import time

import win32gui

from src.core import role_lock, user_data  # 角色锁/角色解析
from src.device import binding, game  # 任务目标窗口解析/角色短文案


class _QueueWriter:
    """子进程内替换 sys.stdout/stderr，逐条写入 multiprocessing.Queue。

    多窗口下队列条目为 (role_id, message)，监听线程按角色分桶后加前缀。
    """

    def __init__(self, q, role_id=None):
        self.queue = q
        self.role_id = role_id

    def write(self, message):
        if message and message.strip():
            try:
                self.queue.put((self.role_id, message))
            except Exception:
                pass

    def flush(self):
        pass


def _invoke_start(start_func, role_id, hwnd, title):
    """调用业务入口：新入口接收 (role_id, hwnd, title)，旧无参函数兼容。"""
    try:
        import inspect
        params = inspect.signature(start_func).parameters
    except (TypeError, ValueError):
        params = {}
    if params:
        start_func(role_id, hwnd, title)
    else:
        start_func()


def _worker_wrapper(start_func, stop_func, msg_queue, stop_conn,
                    role_id, hwnd, title):
    """子进程入口（模块级函数，spawn 按引用 pickle）。

    role_id 非空时先取跨实例角色互斥体：失败说明另一安装实例正在跑
    同一角色，直接退出（父进程启动前已探测，这里是竞态兜底）。
    随后在 daemon 线程运行业务入口，本线程轮询停止管道；收到停止信号
    后以 2.5s 预算执行 stop_func，随后退出进程。
    """
    sys.stdout = _QueueWriter(msg_queue, role_id)
    sys.stderr = _QueueWriter(msg_queue, role_id)

    lock_handle = None
    if role_id:
        try:
            lock_handle = role_lock.acquire(str(role_id))
        except Exception:
            lock_handle = None
        if lock_handle is None:
            print(f"[系统] 角色 {role_id} 正在另一实例中运行，任务退出")
            try:
                stop_conn.close()
            except Exception:
                pass
            return

    worker_thread = threading.Thread(target=_invoke_start,
                                     args=(start_func, role_id, hwnd, title),
                                     daemon=True)
    worker_thread.start()

    def safe_cleanup():
        if stop_func:
            t = threading.Thread(target=stop_func, daemon=True)
            t.start()
            t.join(timeout=2.5)

    while True:
        try:
            if stop_conn.poll(0.1):
                stop_conn.recv()
                break
        except (EOFError, OSError):
            break
        if not worker_thread.is_alive():
            break

    safe_cleanup()
    try:
        stop_conn.close()
    except Exception:
        pass
    if lock_handle is not None:
        role_lock.release(lock_handle)


def _short_label(role_id, title):
    """角色短文案（服务器+角色名）；标题不可解析时退角色 ID。"""
    label = binding.short_label_from_title(title or "")
    return label or str(role_id or "")


class _WorkerSlot:
    """单个窗口 worker 的运行态。"""

    def __init__(self, key, role_id, hwnd, title, process, stop_conn):
        self.key = key
        self.role_id = role_id
        self.hwnd = int(hwnd or 0)
        self.title = title or ""
        self.process = process
        self.stop_conn = stop_conn
        self.stop_requested = False


class TaskManager:
    """主进程侧多窗口任务生命周期管理。"""

    MAX_BATCH = 50  # 单次 evaluate_js 推送的最大日志行数
    WINDOW_CHECK_INTERVAL = 1.0  # 句柄/最小化校验间隔（秒）
    WINDOW_FATAL_MISSES = 3      # 连续失效次数（≈3 秒）判定窗口已关闭
    MINIMIZED_FATAL_MISSES = 3   # 连续最小化次数（≈3 秒）判定需停该窗口

    def __init__(self, start_func, stop_func):
        self._start_func = start_func      # main.task_start（顶层函数，可 pickle）
        self._stop_func = stop_func        # main.task_stop
        self._on_status = None             # callable(bool) → 全局「是否有任务在跑」
        self._on_log = None                # callable(list[str]) → 面板日志（已带角色前缀）
        self._on_role_status = None        # callable(role_id, bool) → 行级状态
        self._on_role_log = None           # callable(role_id, list[str]) → 行级日志（未加前缀）
        self._workers = {}                 # key（角色 ID / _default）→ _WorkerSlot
        self._labels = {}                  # key → 角色短文案（日志前缀/行标题）
        self._lock = threading.RLock()
        # 队列在主线程创建（与旧版一致，规避 spawn 句柄继承风险）
        self.msg_queue = multiprocessing.Queue()
        self._listener = threading.Thread(target=self._queue_listener,
                                          daemon=True, name="task-log-listener")
        self._listener.start()

    def set_callbacks(self, on_status, on_log,
                      on_role_status=None, on_role_log=None):
        """接线控制窗口：全局回调（兼容旧版）+ 行级回调（多窗口）。"""
        self._on_status = on_status
        self._on_log = on_log
        self._on_role_status = on_role_status
        self._on_role_log = on_role_log

    # ---- 对外启停接口 ----

    def start(self, role_id=None, hwnd=None, title=None):
        """启动一个窗口的任务 worker。

        无参调用（旧单窗口路径）：解析当前绑定/回退窗口作为目标；
        解析不到窗口时传空目标，worker 保持旧的 30s 等待语义。
        返回 {"status": "success", "running": True, "role": ...} 或 error。
        """
        if role_id is None and hwnd is None:
            win = game.find_main_window()
            if win is None:
                # 多窗口：绝不 spawn 无目标 worker（子进程会回退到全局绑定，
                # 可能操作到其他窗口/旧角色）。UI 必须显式传角色+句柄。
                return {"status": "error",
                        "msg": "未找到游戏窗口（游戏未启动或选中窗口已关闭）"}
            hwnd = int(getattr(win, "hwnd", 0) or 0)
            title = getattr(win, "title", "") or ""
            role_id = (game.window_role_id(win)
                       or user_data.current_role_id(refresh=True))
        rid = str(role_id) if role_id else ""
        if rid and not re.fullmatch(r"\d+", rid):
            rid = ""  # _default/未登录：不启用角色目标与跨实例锁
        key = rid or "_default"
        with self._lock:
            slot = self._workers.get(key)
            if slot is not None and slot.process.is_alive():
                return {"status": "error", "msg": "该窗口任务已在运行"}
            if rid and role_lock.is_held(rid):
                return {"status": "error",
                        "msg": "该角色正在另一实例中运行"}
            try:
                # duplex=False: (recv_end, send_end)；子进程拿 recv，主进程拿 send
                child_conn, parent_conn = multiprocessing.Pipe(duplex=False)
            except Exception as e:
                return {"status": "error", "msg": f"创建进程通信失败: {e}"}
            proc = multiprocessing.Process(
                target=_worker_wrapper,
                args=(self._start_func, self._stop_func, self.msg_queue,
                      child_conn, rid or None, int(hwnd or 0) or None,
                      title or ""),
                daemon=True)
            try:
                proc.start()
            except Exception as e:
                for conn in (child_conn, parent_conn):
                    try:
                        conn.close()
                    except Exception:
                        pass
                return {"status": "error", "msg": f"启动任务进程失败: {e}"}
            try:
                child_conn.close()  # 子进程已持有自己的句柄副本
            except Exception:
                pass
            slot = _WorkerSlot(key, rid or None, hwnd, title, proc, parent_conn)
            self._workers[key] = slot
            self._labels[key] = _short_label(rid, title)
            threading.Thread(target=self._monitor_worker, args=(key, slot),
                             daemon=True, name=f"task-monitor-{key}").start()
            threading.Thread(target=self._window_watchdog, args=(key, slot),
                             daemon=True, name=f"window-watchdog-{key}").start()
        self._notify_status(key, True)
        self._emit_role(key, ["[系统] 任务已启动"])
        return {"status": "success", "running": True, "role": rid or None}

    def stop(self, role_id=None):
        """停止一个窗口或全部窗口的任务 worker（升级强杀）。"""
        rid = str(role_id) if role_id else None
        with self._lock:
            if rid:
                slots = [(k, s) for k, s in self._workers.items()
                         if k == rid or s.role_id == rid]
            else:
                slots = list(self._workers.items())
            for key, _slot in slots:
                self._workers.pop(key, None)
        for key, slot in slots:
            self._kill_worker(slot)
            self._notify_status(key, False)
            # 主动停止时槽位已先行摘除，monitor 不再收口——这里补一行
            self._emit_role(key, ["[系统] 任务已停止"])
        return {"status": "success", "running": self.is_running()}

    def is_running(self, role_id=None):
        """是否有任务在跑（指定 role_id 时只看该窗口）。"""
        with self._lock:
            if role_id:
                rid = str(role_id)
                slot = self._workers.get(rid)
                return slot is not None and slot.process.is_alive()
            return any(s.process.is_alive() for s in self._workers.values())

    def running_roles(self):
        """当前在跑的角色键列表（UI 行状态用）。"""
        with self._lock:
            return [k for k, s in self._workers.items() if s.process.is_alive()]

    def running_targets(self):
        """当前在跑的 worker 目标（角色/句柄/标题/短文案）。

        组号 pass 期间主窗口标题会显示队员角色；UI 用这里的槽位角色
        覆盖标题解析，保证舰队行始终是启动时的队长角色。
        """
        with self._lock:
            return [{"key": k, "role": s.role_id, "hwnd": s.hwnd,
                     "title": s.title,
                     "label": self._labels.get(k) or str(k)}
                    for k, s in self._workers.items() if s.process.is_alive()]

    def label_for(self, key):
        """角色短文案（日志前缀/行标题；_default 或未知返回空串）。"""
        return self._labels.get(key) or (
            "" if key in ("_default", None) else str(key))

    # ---- 内部机制 ----

    def _kill_worker(self, slot):
        """升级强杀（沿用旧 TaskManager 时序）。"""
        t0 = time.time()
        slot.stop_requested = True
        if slot.process.is_alive():
            try:
                slot.stop_conn.send("stop")
            except (BrokenPipeError, OSError):
                pass
            self._emit_role(slot.key, ["[系统] 已发送停止信号，等待子进程退出..."])
            slot.process.join(timeout=3.0)

        if slot.process.is_alive():
            self._emit_role(slot.key,
                            [f"[系统] 子进程 {time.time() - t0:.1f}s 未退出，升级 terminate"])
            slot.process.terminate()
            slot.process.join(timeout=1.0)

        if slot.process.is_alive():
            self._emit_role(slot.key, ["[系统] terminate 后仍存活，升级 kill"])
            slot.process.kill()
            slot.process.join()
        try:
            slot.stop_conn.close()
        except Exception:
            pass

    def _monitor_worker(self, key, slot):
        """子进程结束监听：自然结束/崩溃/被强杀都收口状态与日志。"""
        slot.process.join()
        with self._lock:
            is_current = self._workers.get(key) is slot
            if is_current:
                self._workers.pop(key, None)
        if not is_current:
            return
        self._notify_status(key, False)
        code = slot.process.exitcode
        if slot.stop_requested:
            self._emit_role(key, ["[系统] 任务已停止"])
        elif code == 0:
            self._emit_role(key, ["[系统] 任务已结束（自然退出）"])
        else:
            self._emit_role(key, [f"[系统] 任务进程异常退出（exitcode={code}）"])
        try:
            slot.stop_conn.close()
        except Exception:
            pass

    def _resolve_task_window(self):
        """旧单窗口路径：解析任务目标窗口句柄。"""
        win = game.find_main_window()
        hwnd = getattr(win, "hwnd", 0) if win is not None else 0
        return int(hwnd or 0) or None

    def _window_watchdog(self, key, slot):
        """看护单个 worker 的目标窗口：关闭/最小化只停该窗口。

        显式目标（多窗口）直接校验传入句柄；旧单窗口路径首次解析后
        沿用旧行为（句柄失效即终止，不再自动改绑）。
        最小化按用户 2026-11-22 确认：多窗口要求窗口非最小化，连续
        ≈3 秒最小化即停该窗口并提示。
        """
        target = slot.hwnd or 0
        misses = 0
        minimized = 0
        while slot.process.is_alive():
            time.sleep(self.WINDOW_CHECK_INTERVAL)
            if not target:
                target = self._resolve_task_window()
                misses = 0
                continue
            if not win32gui.IsWindow(target):
                misses += 1
                if misses < self.WINDOW_FATAL_MISSES:
                    continue
                if not slot.process.is_alive():
                    return
                with self._lock:
                    current = self._workers.get(key) is slot
                if not current:
                    return
                self._emit_role(key, ["[系统] 检测到游戏窗口已关闭，任务自动终止"])
                self.stop(slot.role_id or key)
                return
            misses = 0
            if win32gui.IsIconic(target):
                minimized += 1
                if minimized < self.MINIMIZED_FATAL_MISSES:
                    continue
                if not slot.process.is_alive():
                    return
                with self._lock:
                    current = self._workers.get(key) is slot
                if not current:
                    return
                self._emit_role(key, ["[系统] 检测到游戏窗口已最小化，"
                                      "任务自动终止（请保持窗口非最小化）"])
                self.stop(slot.role_id or key)
                return
            minimized = 0

    def _queue_listener(self):
        """日志回流：批式取出（≤MAX_BATCH/批）→ 按角色分桶 → 前缀转发。"""
        while True:
            try:
                first = self.msg_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < self.MAX_BATCH:
                try:
                    batch.append(self.msg_queue.get_nowait())
                except queue.Empty:
                    break
            grouped = {}
            order = []
            for item in batch:
                if isinstance(item, tuple) and len(item) == 2:
                    rid, line = item
                else:
                    rid, line = None, item
                if rid not in grouped:
                    grouped[rid] = []
                    order.append(rid)
                grouped[rid].append(str(line).rstrip("\n"))
            for rid in order:
                self._emit_role(rid or "_default", grouped[rid])

    def _emit_role(self, key, lines):
        """按角色转发日志：控制台/面板统一加角色前缀。"""
        lines = [ln for ln in lines if ln and str(ln).strip()]
        if not lines:
            return
        label = self._labels.get(key) or ("" if key in ("_default", None) else str(key))
        print_lines = [f"[{label}] {ln}" if label else ln for ln in lines]
        for line in print_lines:
            print(line)
        if self._on_role_log is not None:
            try:
                self._on_role_log(key, lines)
            except Exception:
                pass
        if self._on_log is not None:
            try:
                self._on_log(print_lines)
            except Exception:
                pass  # 窗口销毁期间调用失败忽略

    def _notify_status(self, key, running):
        if self._on_role_status is not None:
            try:
                self._on_role_status(key, running)
            except Exception:
                pass
        if self._on_status is not None:
            try:
                self._on_status(self.is_running())
            except Exception:
                pass

    def _emit(self, lines):
        """主进程自身消息：控制台 + 全局日志回调（不加角色前缀）。"""
        for line in lines:
            print(line)
        if self._on_log is not None:
            try:
                self._on_log(lines)
            except Exception:
                pass
