"""消息服务编排：实例锁、通道生命周期、事件队列、状态快照、验证。

所有线程均为 daemon；网络异常只更新状态/日志，不向 UI 或任务冒泡。
本模块不 import ControlWindow：能力通过 host 注入（is_running/
runtime_status/task_progress/identity/capture/log_tail/exit_game/
list_schemes/switch_scheme/start_task/stop_task/disband_team/shout）。
"""
import collections
import queue
import threading
import time

from ...core import settings as core_settings
from ...core import user_data
from . import events as message_events
from . import registry
from .lockfile import InstanceLock

STATUS_TEXT = {
    "disabled": "未启用",
    "no_channels": "没有可用通道",
    "blocked": "实例锁被占用",
    "online": "运行中",
    "stopped": "已停止",
    "error": "异常",
}


def _mask_chat(chat_id):
    text = str(chat_id or "")
    return text if len(text) <= 6 else text[:3] + "***" + text[-3:]


class MessageService:
    # 事件推送失败重试：网络/TLS 抖动常见（实测 SSL handshake timeout 会
    # 丢任务停止事件），事件比指令更值得重试；仅网络/5xx 重试，4xx 不重试。
    EVENT_SEND_RETRIES = 3
    EVENT_RETRY_DELAYS = (2.0, 5.0)

    def __init__(self, host, config=None, log=None, clock=time.time,
                 lock_factory=None, adapter_factory=None, config_dir=None):
        self.host = host
        self._log = log or (lambda msg, level="INFO": None)
        self._clock = clock
        self._config = config if isinstance(config, dict) \
            else core_settings.load_message_config()
        self._adapter_factory = adapter_factory or registry.build_adapter
        self._config_dir = config_dir
        self._lock_factory = lock_factory
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._started = False
        self._status = "disabled"
        self._started_at = 0.0
        self._instance_lock = None
        self._lock_info = None
        self._adapters = {}
        self._adapter_health = {}
        self._router = None
        self._recent = collections.deque(maxlen=30)
        self._last_command_ts = 0.0
        self._last_rejected_ts = 0.0
        self._last_poll_ok = 0.0
        self._event_queue = queue.Queue(maxsize=100)
        self._event_thread = None
        self._dropped = 0
        self._deduper = message_events.EventDeduper(clock=clock)
        self._verify_lock = threading.Lock()
        self._verify_state = {"channel": None, "running": False, "report": None}
        self._counter_lock = threading.Lock()
        self._warn_times = {}

    # ---- 生命周期 ----

    def start(self):
        with self._lock:
            if self._started:
                return self.get_state()
            cfg = registry.effective_config(self._config)
            if not cfg.get("enabled"):
                self._status = "disabled"
                return self.get_state()
            names = [name for name in registry.enabled_channel_names(cfg)
                     if self._channel_ready(name, cfg["channels"].get(name))]
            if not names:
                self._status = "no_channels"
                self._log("消息服务已启用，但没有配置完整的可用通道", "WARN")
                return self.get_state()
            lock = self._make_lock()
            info = lock.acquire()
            self._lock_info = info
            if not info.acquired:
                self._instance_lock = None
                self._status = "blocked"
                self._log(
                    "另一个实例正在使用消息服务（PID %s），本实例未启动。"
                    "同一配置目录只允许一个实例；多开请为每个实例使用独立 bot "
                    "或独立配置目录。" % (info.pid or "?"), "WARN")
                return self.get_state()
            self._instance_lock = lock
            self._stop.clear()
            self._started = True
            self._started_at = self._clock()
            self._deduper.reset()
            self._event_thread = threading.Thread(
                target=self._event_worker, daemon=True, name="message-events")
            self._event_thread.start()
            from .router import MessageRouter
            self._router = MessageRouter(
                self.host, log=self._log, clock=self._clock,
                record=self._record_command)
            started = 0
            for name in names:
                channel_cfg = cfg["channels"].get(name) or {}
                try:
                    adapter = self._adapter_factory(
                        name, channel_cfg, self._log)
                except Exception as e:
                    self._log(f"消息通道 {name} 初始化失败：{e}", "WARN")
                    continue
                self._adapters[name] = adapter
                self._adapter_health[name] = self._safe_state(adapter)
                try:
                    ok = adapter.start(
                        on_command=lambda cmd, _name=name, _adapter=adapter:
                            self._on_command(_name, _adapter, cmd),
                        on_health=lambda state, _name=name:
                            self._on_health(_name, state))
                except Exception as e:
                    self._log(f"消息通道 {name} 启动失败：{e}", "WARN")
                    ok = False
                if ok:
                    started += 1
                else:
                    self._adapters.pop(name, None)
            if started == 0:
                self._stop.set()
                thread = self._event_thread
                self._event_thread = None
                self._adapters.clear()
                self._router = None
                self._started = False
                self._status = "no_channels"
                if thread is not None and thread.is_alive():
                    thread.join(timeout=1.0)
                try:
                    lock.release()
                except Exception:
                    pass
                self._instance_lock = None
                self._log("消息服务没有成功启动任何通道", "WARN")
                return self.get_state()
            self._status = "online"
            if info.stale_taken:
                self._log("检测到旧实例锁（原进程已退出），已接管消息服务。",
                          "SYS")
            self._log("消息服务已启动（%d 个通道）" % started, "SYS")
            if cfg.get("notify_online"):
                self._enqueue_raw({
                    "key": "service_online", "title": "消息服务已上线",
                    "detail": "可发送 /help 查看可用指令",
                    "dedupe_key": "service_online", "norm": ""})
            return self.get_state()

    def stop(self, timeout=1.5):
        with self._lock:
            if not self._started and self._instance_lock is None:
                return
            self._started = False
            self._stop.set()
            adapters = list(self._adapters.items())
            self._adapters.clear()
            for name in list(self._adapter_health):
                self._adapter_health[name] = {"state": "stopped",
                                              "state_text": "已停止"}
            self._router = None
            event_thread = self._event_thread
            self._event_thread = None
            instance_lock = self._instance_lock
            self._instance_lock = None
        deadline = time.time() + max(0.1, float(timeout))
        for _name, adapter in adapters:
            try:
                adapter.stop(timeout=max(0.1, deadline - time.time()))
            except Exception:
                pass
        if event_thread is not None and event_thread.is_alive():
            try:
                event_thread.join(timeout=max(0.1, deadline - time.time()))
            except Exception:
                pass
        if instance_lock is not None:
            try:
                instance_lock.release()
            except Exception:
                pass
        with self._lock:
            self._status = "stopped"

    def restart(self):
        self.stop()
        return self.start()

    def restart_with_config(self, config=None):
        """按指定配置重启（UI「重试启动」/锁 stale 接管用）。"""
        if isinstance(config, dict):
            with self._lock:
                self._config = config
        self.stop()
        return self.start()

    def apply_config(self, config=None):
        """热应用配置：通道/启用状态变化才重启；事件开关变化不重启。"""
        new = config if isinstance(config, dict) \
            else core_settings.load_message_config()
        with self._lock:
            old = self._config
            self._config = new
            started = self._started
        changed = (bool(old.get("enabled")) != bool(new.get("enabled"))
                   or (old.get("channels") or {}) != (new.get("channels") or {}))
        if changed:
            if started or self._instance_lock is not None:
                self.stop()
            if new.get("enabled"):
                self.start()
            else:
                with self._lock:
                    self._status = "disabled"
        elif new.get("enabled") and not started:
            self.start()
        return self.get_state()

    # ---- 指令 / 日志旁路 ----

    def _on_command(self, name, adapter, command):
        with self._lock:
            if not self._started:
                return
            router = self._router
            channel_cfg = registry.channel_config(self._config, name)
        if router is None:
            return
        chat_ids = [str(item) for item in channel_cfg.get("chat_ids") or []]
        try:
            router.dispatch(
                adapter, command, chat_ids,
                capture_cooldown=core_settings.MESSAGE_CAPTURE_COOLDOWN)
        except Exception as e:
            self._log(f"消息指令处理异常：{type(e).__name__}: {e}", "WARN")

    def on_logs(self, lines):
        """日志旁路入口（可在 TaskManager 监听线程调用）：只分类+入队。"""
        if not lines:
            return
        with self._lock:
            if not self._started:
                return
            event_cfg = dict(self._config.get("events") or {})
        for line in lines:
            try:
                event = message_events.classify_log_line(line)
            except Exception:
                continue
            if not event:
                continue
            if not event_cfg.get(event.get("key"), True):
                continue
            try:
                accepted = self._deduper.accept(event)
            except Exception:
                continue
            if accepted:
                self._enqueue_raw(accepted)

    def on_log_line(self, line):
        self.on_logs([line])

    def _enqueue_raw(self, event):
        try:
            self._event_queue.put_nowait(event)
        except queue.Full:
            with self._counter_lock:
                self._dropped += 1

    def _record_command(self, record):
        with self._lock:
            self._recent.appendleft(dict(record))
            ts = record.get("ts") or self._clock()
            if record.get("rejected"):
                self._last_rejected_ts = ts
            else:
                self._last_command_ts = ts

    def _on_health(self, name, state):
        with self._lock:
            if name not in self._adapters or not self._started:
                return
            self._adapter_health[name] = dict(state or {})
            try:
                poll_ok = float((state or {}).get("last_poll_ok") or 0)
            except (TypeError, ValueError):
                poll_ok = 0.0
            if poll_ok > self._last_poll_ok:
                self._last_poll_ok = poll_ok

    # ---- 事件推送 ----

    def _event_worker(self):
        while not self._stop.is_set() or not self._event_queue.empty():
            try:
                event = self._event_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._send_event(event)
            except Exception as e:
                self._log(f"事件推送异常：{type(e).__name__}: {e}", "WARN")

    def _send_event(self, event):
        text = message_events.format_event(event, identity=self._identity())
        targets = self._event_targets()
        title = event.get("title") or event.get("key") or "事件"
        for name, chat_ids in targets.items():
            with self._lock:
                adapter = self._adapters.get(name)
            if adapter is None:
                continue
            for chat_id in chat_ids:
                self._send_event_to(name, adapter, chat_id, title, text, event)

    def _send_event_to(self, name, adapter, chat_id, title, text, event):
        """单条事件发送：网络/5xx 重试 2 次；失败日志带事件标题与脱敏 chat。"""
        error = ""
        for attempt in range(max(1, int(self.EVENT_SEND_RETRIES))):
            with self._lock:
                if not self._started or self._adapters.get(name) is not adapter:
                    return
            try:
                result = adapter.send_text(chat_id, text)
                error = "" if result.ok else (result.error or "发送失败")
            except Exception as e:
                result = None
                error = f"{type(e).__name__}: {e}"
            if result is not None and result.ok:
                if event.get("key") != "error":
                    self._log("[消息] 事件已推送：%s → %s"
                              % (title, _mask_chat(chat_id)), "INFO")
                return
            retryable = result is None or result.code == 0 or result.code >= 500
            if retryable and attempt < len(self.EVENT_RETRY_DELAYS):
                if self._stop.wait(self.EVENT_RETRY_DELAYS[attempt]):
                    return
                continue
            self._log_throttled(
                (name, str(chat_id), event.get("key"), error),
                "[消息] 事件推送失败：%s → %s：%s"
                % (title, _mask_chat(chat_id), error or "未知错误"))
            return

    def _event_targets(self):
        cfg = registry.effective_config(self._config)
        targets = {}
        with self._lock:
            adapter_names = set(self._adapters)
        for name, item in cfg["channels"].items():
            if name not in adapter_names or not isinstance(item, dict):
                continue
            if not item.get("enabled"):
                continue
            chat_ids = [str(x) for x in item.get("chat_ids") or []]
            if chat_ids:
                targets[name] = chat_ids
        return targets

    def _identity(self):
        try:
            return self.host.identity() or ""
        except Exception:
            return ""

    # ---- 验证 ----

    def verify_async(self, channel, config_override=None, on_step=None,
                     on_done=None):
        channel = str(channel or "telegram")
        with self._verify_lock:
            if self._verify_state.get("running"):
                return {"status": "error", "msg": "验证进行中，请稍候"}
            self._verify_state = {"channel": channel, "running": True,
                                  "report": None}
        try:
            cfg = registry.channel_config(
                config_override or self._config, channel)
        except Exception as e:
            with self._verify_lock:
                self._verify_state = {"channel": channel, "running": False,
                                      "report": None}
            return {"status": "error", "msg": str(e)}

        def _run():
            report_dict = None
            try:
                adapter = self._adapter_factory(channel, cfg, self._log)
                report = adapter.verify(step_cb=on_step)
                report_dict = report.to_dict() \
                    if hasattr(report, "to_dict") else report
            except Exception as e:
                self._log(f"验证通道 {channel} 异常：{type(e).__name__}: {e}",
                          "WARN")
                report_dict = {"channel": channel, "ok": False,
                               "steps": [], "duration": 0}
            with self._verify_lock:
                self._verify_state = {"channel": channel, "running": False,
                                      "report": report_dict}
            if on_done is not None:
                try:
                    on_done(report_dict)
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True,
                         name="message-verify").start()
        return {"status": "success", "msg": "验证已开始", "channel": channel}

    # ---- 状态 ----

    def get_state(self):
        with self._lock:
            cfg = registry.effective_config(self._config)
            channels = {}
            for name, item in cfg["channels"].items():
                enabled = bool(item.get("enabled")) \
                    if isinstance(item, dict) else False
                health = dict(self._adapter_health.get(name) or {})
                adapter = self._adapters.get(name)
                if adapter is not None:
                    health.update(self._safe_state(adapter))
                channels[name] = {"enabled": enabled, **health}
            lock_info = self._lock_info
            status = self._status
            running = self._started
            enabled = bool(cfg.get("enabled"))
            recent = list(self._recent)
            verify = dict(self._verify_state)
            deduper_suppressed = getattr(self._deduper, "suppressed", 0)
            with self._counter_lock:
                dropped = self._dropped
            return {
                "enabled": enabled,
                "running": running,
                "status": status,
                "status_text": STATUS_TEXT.get(status, status),
                "started_at": self._started_at,
                "last_poll_ok": self._last_poll_ok,
                "last_command_ts": self._last_command_ts,
                "last_rejected_ts": self._last_rejected_ts,
                "lock": {
                    "held": self._instance_lock is not None,
                    "owner": "self" if self._instance_lock is not None else "",
                    "pid": getattr(lock_info, "pid", 0) if lock_info else 0,
                    "stale_taken": bool(getattr(lock_info, "stale_taken", False)),
                    "reason": getattr(lock_info, "reason", "") if lock_info else "",
                },
                "channels": channels,
                "recent_commands": recent,
                "queue": {"size": self._event_queue.qsize(),
                          "dropped": dropped,
                          "error_suppressed": deduper_suppressed},
                "verify": verify,
            }

    # ---- 内部 ----

    @staticmethod
    def _channel_ready(name, cfg):
        if name == "telegram":
            return bool(str((cfg or {}).get("token") or "").strip()
                        and (cfg or {}).get("chat_ids"))
        return True

    def _make_lock(self):
        if self._lock_factory is not None:
            return self._lock_factory()
        directory = self._config_dir
        if directory is None:
            directory = user_data.config_dir()
        return InstanceLock(directory, clock=self._clock)

    @staticmethod
    def _safe_state(adapter):
        try:
            state = adapter.state()
            return dict(state) if isinstance(state, dict) else {}
        except Exception:
            return {}

    def _log_throttled(self, key, message, level="WARN", window=300.0):
        now = self._clock()
        with self._counter_lock:
            last = self._warn_times.get(key, 0.0)
            if now - last < window:
                return
            self._warn_times[key] = now
        self._log(message, level)
