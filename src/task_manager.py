"""任务进程管理：spawn 子进程运行任务，日志经队列回流主进程，停止时逐级强杀。

接管原 FloatScriptWindow 的 worker 管理模式（见 ascript flot_window.py）：
- 任务在 daemon spawn 子进程运行——业务代码无协作式停止标志（battle 监控、
  五倍/修罗 keep-alive 等均为无限循环），杀进程是唯一可靠的全线程清理方式
- 子进程 stdout/stderr → QueueWriter → multiprocessing.Queue → 主进程监听线程
- 停止升级：stop_event → join 3s → terminate → join 1s → kill → join
- 监听线程不回写日志文件：文件由子进程 app_utils.log 写入，避免重复落盘
"""
import multiprocessing
import queue
import sys
import threading
import time

import win32gui

from src import game  # 任务目标窗口解析（find_main_window，与任务子进程同接缝）


class _QueueWriter:
    """子进程内替换 sys.stdout/stderr，逐条写入 multiprocessing.Queue。"""

    def __init__(self, q):
        self.queue = q

    def write(self, message):
        if message and message.strip():
            self.queue.put(message)

    def flush(self):
        pass


def _worker_wrapper(start_func, stop_func, msg_queue, stop_event):
    """子进程入口（必须为模块级函数，spawn 按引用 pickle）。

    复制 flot_window._worker_wrapper：start_func 在 daemon 线程运行，
    本函数轮询 stop_event（0.1s），收到停止信号后以 2.5s 预算执行 stop_func，
    随后退出进程（daemon 工作线程随解释器退出一并终止）。
    """
    sys.stdout = _QueueWriter(msg_queue)
    sys.stderr = _QueueWriter(msg_queue)

    worker_thread = threading.Thread(target=start_func, daemon=True)
    worker_thread.start()

    def safe_cleanup():
        if stop_func:
            t = threading.Thread(target=stop_func, daemon=True)
            t.start()
            t.join(timeout=2.5)

    while True:
        if stop_event.is_set():
            safe_cleanup()
            break

        if not worker_thread.is_alive():
            safe_cleanup()
            break

        time.sleep(0.1)


class TaskManager:
    """主进程侧任务生命周期管理：启停互斥、升级强杀、自然结束收口。"""

    MAX_BATCH = 50  # 单次 evaluate_js 推送的最大日志行数

    def __init__(self, start_func, stop_func):
        self._start_func = start_func      # main.task_start（顶层函数，可 pickle）
        self._stop_func = stop_func        # main.task_stop
        self._on_status = None             # callable(bool)  → control_win.sync_status
        self._on_log = None                # callable(list[str]) → control_win.add_logs
        self.stop_event = multiprocessing.Event()
        # 在主线程立即触底建 SemLock：Windows 下信号量在非主线程首次
        # 使用会走命名信号量路径，spawn 子进程重建句柄存在继承风险
        # （表现为子进程收不到停止信号，停止退化到 3s join 超时强杀）。
        # 老板键热键线程可能先于主线程调用 start/stop，此处预建规避。
        self.stop_event.clear()
        self.msg_queue = multiprocessing.Queue()
        self.active_worker = None
        self._lock = threading.Lock()      # 保护 active_worker/_busy/_stop_requested
        self._busy = False                 # 启停互斥（防连点/停止期间再启动）
        self._stop_requested = False
        self._listener = threading.Thread(target=self._queue_listener,
                                          daemon=True, name="task-log-listener")
        self._listener.start()

    def set_callbacks(self, on_status, on_log):
        """接线控制窗口（ControlWindow 构造时调用）。"""
        self._on_status = on_status
        self._on_log = on_log

    # ---- 对外启停接口（供控制窗口桥接调用） ----

    def start(self):
        """启动任务子进程。返回 {"status": "success", "running": True} 或 BUSY。"""
        with self._lock:
            if self._busy:
                return {"status": "error", "msg": "BUSY"}
            self._busy = True
        try:
            with self._lock:
                worker = self.active_worker
            if worker is not None and worker.is_alive():
                self._kill_worker(worker)  # 防御：状态不同步时的残留进程
            self.stop_event.clear()
            self._stop_requested = False
            worker = multiprocessing.Process(
                target=_worker_wrapper,
                args=(self._start_func, self._stop_func,
                      self.msg_queue, self.stop_event),
                daemon=True)
            worker.start()
            with self._lock:
                self.active_worker = worker
            threading.Thread(target=self._monitor_worker, args=(worker,),
                             daemon=True, name="task-monitor").start()
            threading.Thread(target=self._window_watchdog, args=(worker,),
                             daemon=True, name="window-watchdog").start()
            self._notify_status(True)
            self._emit(["[系统] 任务已启动"])
            return {"status": "success", "running": True}
        finally:
            with self._lock:
                self._busy = False

    def stop(self):
        """停止任务子进程（升级强杀）。返回 {"status": "success", "running": False} 或 BUSY。"""
        with self._lock:
            if self._busy:
                return {"status": "error", "msg": "BUSY"}
            self._busy = True
        try:
            with self._lock:
                worker = self.active_worker
                self.active_worker = None
                self._stop_requested = worker is not None
            if worker is not None:
                self._kill_worker(worker)
            self.stop_event.clear()
            self._notify_status(False)  # 立即同步面板状态（不等 monitor 收口）
            return {"status": "success", "running": False}
        finally:
            with self._lock:
                self._busy = False

    def is_running(self):
        """当前是否有任务子进程存活。"""
        with self._lock:
            worker = self.active_worker
        return worker is not None and worker.is_alive()

    # ---- 任务目标窗口存活看护 ----
    # 任务子进程锁定启动时的主窗口后不再换窗；窗口被关闭（句柄失效）
    # 后其所有截图失败（GetClientRect 1400 无限刷屏）且不可恢复，
    # 由本线程在任务期间校验句柄并自动终止任务（最小化不影响：
    # 句柄仍有效，恢复前台后任务可继续轮询）。
    WINDOW_CHECK_INTERVAL = 1.0  # 句柄校验间隔（秒）
    WINDOW_FATAL_MISSES = 3      # 连续失效次数（≈3 秒）判定窗口已关闭

    def _resolve_task_window(self):
        """解析任务目标窗口句柄（与任务子进程同接缝：find_main_window 绑定优先）。"""
        win = game.find_main_window()
        hwnd = getattr(win, "hwnd", 0) if win is not None else 0
        return int(hwnd or 0) or None

    def _window_watchdog(self, worker):
        """看护任务目标窗口：句柄连续失效即判定窗口已关闭，终止任务。

        启动时窗口尚未出现（target None）则跟随任务子进程的 30s 等待期
        每秒重解析，不提前判死；句柄失效后若又被系统复用（IsWindow 转真）
        计数自动归零自愈。终止前确认 worker 仍是当前轮任务，防止旧看护
        误杀快速重启后的新一轮任务。
        """
        target = None
        misses = 0
        while worker.is_alive():
            time.sleep(self.WINDOW_CHECK_INTERVAL)
            if target is None:
                target = self._resolve_task_window()
                misses = 0
                continue
            if win32gui.IsWindow(target):
                misses = 0
                continue
            misses += 1
            if misses < self.WINDOW_FATAL_MISSES:
                continue
            if not worker.is_alive():
                return  # 任务已自然结束，无需终止
            with self._lock:
                current = self.active_worker is worker
            if not current:
                return  # 新一轮任务已接管，旧看护静默退出
            self._emit(["[系统] 检测到游戏窗口已关闭，任务自动终止"])
            self.stop()
            return

    # ---- 内部机制 ----

    def _kill_worker(self, worker):
        """升级强杀（复制 flot_window._force_stop_worker 时序）。

        分级日志：每阶升级都落一行——老板键停止异常时可直接看出子进程
        是否收到停止信号（优雅退出无升级行；收不到会依次出现 3s 超时/
        terminate/kill 行）。
        """
        t0 = time.time()
        if worker.is_alive():
            self.stop_event.set()
            self._emit(["[系统] 已发送停止信号，等待子进程退出..."])
            worker.join(timeout=3.0)

        if worker.is_alive():
            self._emit([f"[系统] 子进程 {time.time() - t0:.1f}s 未退出，升级 terminate"])
            worker.terminate()
            worker.join(timeout=1.0)

        if worker.is_alive():
            self._emit(["[系统] terminate 后仍存活，升级 kill"])
            worker.kill()
            worker.join()

    def _monitor_worker(self, worker):
        """子进程结束监听：自然结束/崩溃/被强杀都收口状态与日志。

        is_current 检查防止旧一轮的 monitor 覆盖新一轮任务的状态。
        """
        worker.join()
        with self._lock:
            is_current = self.active_worker is worker
            if is_current:
                self.active_worker = None
            was_stop = self._stop_requested
        if not is_current:
            return
        self._notify_status(False)
        code = worker.exitcode
        if was_stop:
            self._emit(["[系统] 任务已停止"])
        elif code == 0:
            self._emit(["[系统] 任务已结束（自然退出）"])
        else:
            self._emit([f"[系统] 任务进程异常退出（exitcode={code}）"])

    def _queue_listener(self):
        """日志回流：批式取出（≤MAX_BATCH/批）→ 控制台 print + 窗口 addLogs。"""
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
            for line in batch:
                print(line)
            if self._on_log is not None:
                try:
                    self._on_log(batch)
                except Exception:
                    pass  # 窗口销毁期间调用失败忽略

    def _notify_status(self, running):
        if self._on_status is not None:
            try:
                self._on_status(running)
            except Exception:
                pass

    def _emit(self, lines):
        """主进程自身消息：控制台 + 窗口日志区。"""
        for line in lines:
            print(line)
        if self._on_log is not None:
            try:
                self._on_log(lines)
            except Exception:
                pass
