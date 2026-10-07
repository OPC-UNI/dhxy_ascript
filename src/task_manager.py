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
            return {"status": "success", "running": False}
        finally:
            with self._lock:
                self._busy = False

    def is_running(self):
        """当前是否有任务子进程存活。"""
        with self._lock:
            worker = self.active_worker
        return worker is not None and worker.is_alive()

    # ---- 内部机制 ----

    def _kill_worker(self, worker):
        """升级强杀（复制 flot_window._force_stop_worker 时序）。"""
        if worker.is_alive():
            self.stop_event.set()
            worker.join(timeout=3.0)

        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=1.0)

        if worker.is_alive():
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
