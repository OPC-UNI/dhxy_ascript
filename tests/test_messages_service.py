"""消息服务编排：启停/锁/事件扇出/热应用/验证/状态。"""
import time

from src.app.messages.channel_base import SendResult, VerifyReport, VerifyStep
from src.app.messages.lockfile import LockInfo
from src.app.messages.service import MessageService
from src.core.settings import MESSAGE_EVENT_KEYS


class FakeHost:
    def is_running(self):
        return False

    def runtime_status(self):
        return None

    def task_progress(self):
        return {}

    def identity(self):
        return "角色A"

    def capture(self):
        return {"ok": False, "reason": "no_window"}

    def log_tail(self, n):
        return {"ok": True, "lines": [], "path": ""}


class FakeLock:
    def __init__(self, acquired=True, pid=123, stale=False):
        self.info = LockInfo(acquired, pid=pid, stale_taken=stale,
                             reason="" if acquired else "alive")
        self.released = False
        self.acquires = 0

    def acquire(self):
        self.acquires += 1
        return self.info

    def release(self):
        self.released = True


class FakeAdapter:
    def __init__(self, config, log=None):
        self.config = config
        self.texts = []
        self.started = False
        self.stopped = False
        self.on_command = None
        self.fail_times = 0
        self.send_calls = 0
        self._state = {"state": "polling", "state_text": "轮询中",
                       "last_poll_ok": 0}

    def start(self, on_command, on_health):
        self.started = True
        self.on_command = on_command
        on_health(dict(self._state))
        return True

    def stop(self, timeout=2.0):
        self.stopped = True

    def send_text(self, chat_id, text):
        self.send_calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            return SendResult(False, "network: timeout", 0)
        self.texts.append((chat_id, text))
        return SendResult(True)

    def send_photo(self, chat_id, image, caption="", filename=""):
        return SendResult(True)

    def state(self):
        return dict(self._state)

    def verify(self, step_cb=None):
        step = VerifyStep("config", "检查配置", True, "ok")
        if step_cb:
            step_cb(step)
        now = time.time()
        return VerifyReport("telegram", True, [step], now, now)


def _config(enabled=True, events=None, token="123:abc"):
    return {
        "version": 1, "enabled": enabled, "notify_online": False,
        "events": events or {key: True for key in MESSAGE_EVENT_KEYS},
        "channels": {"telegram": {"enabled": True, "token": token,
                                  "chat_ids": ["42"], "proxy": "",
                                  "poll_timeout": 20,
                                  "photo_original": False}},
    }


def _service(tmp_path, config=None, lock=None):
    lock = lock or FakeLock()
    made = []

    def factory(name, cfg, log=None):
        adapter = FakeAdapter(cfg, log)
        made.append(adapter)
        return adapter

    service = MessageService(
        FakeHost(), config=config or _config(), lock_factory=lambda: lock,
        adapter_factory=factory, config_dir=tmp_path)
    return service, lock, made


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_disabled_does_not_take_lock(tmp_path):
    service, lock, made = _service(tmp_path, _config(enabled=False))
    state = service.start()
    assert state["status"] == "disabled"
    assert lock.acquires == 0
    assert made == []


def test_no_enabled_channel_does_not_take_lock(tmp_path):
    config = _config()
    config["channels"]["telegram"]["enabled"] = False
    service, lock, made = _service(tmp_path, config)
    state = service.start()
    assert state["status"] == "no_channels"
    assert lock.acquires == 0


def test_blocked_by_instance_lock(tmp_path):
    service, lock, made = _service(tmp_path, lock=FakeLock(False, pid=999))
    state = service.start()
    assert state["status"] == "blocked"
    assert state["lock"]["pid"] == 999
    assert made == []


def test_start_online_and_state(tmp_path):
    service, lock, made = _service(tmp_path)
    state = service.start()
    assert state["status"] == "online"
    assert state["running"] is True
    assert lock.acquires == 1
    assert len(made) == 1 and made[0].started
    assert state["channels"]["telegram"]["enabled"] is True
    assert state["channels"]["telegram"]["state"] == "polling"
    service.stop()
    assert lock.released is True
    assert made[0].stopped is True
    assert service.get_state()["channels"]["telegram"]["state"] == "stopped"


def test_event_send_retries_network_failure(tmp_path):
    service, _lock, made = _service(tmp_path)
    service.EVENT_RETRY_DELAYS = (0.01, 0.01)
    service.start()
    made[0].fail_times = 2
    service.on_logs(["[系统] 任务已启动"])
    assert _wait_until(lambda: made[0].texts)
    assert made[0].send_calls == 3
    assert "任务已启动" in made[0].texts[0][1]
    service.stop()


def test_events_fan_out_and_toggle(tmp_path):
    service, _lock, made = _service(tmp_path)
    service.start()
    service.on_logs(["[系统] 任务已启动"])
    assert _wait_until(lambda: made[0].texts)
    assert "任务已启动" in made[0].texts[0][1]

    events = {key: True for key in MESSAGE_EVENT_KEYS}
    events["task_start"] = False
    service2, _lock2, made2 = _service(
        tmp_path, _config(events=events), lock=FakeLock())
    service2.start()
    service2.on_logs(["[系统] 任务已启动"])
    time.sleep(0.3)
    assert made2[0].texts == []
    service.stop()
    service2.stop()


def test_stop_stops_event_ingest(tmp_path):
    service, _lock, made = _service(tmp_path)
    service.start()
    service.stop()
    service.on_logs(["[系统] 任务已启动"])
    time.sleep(0.3)
    assert made[0].texts == []


def test_apply_config_restarts_changed_channel(tmp_path):
    service, lock, made = _service(tmp_path)
    service.start()
    service.apply_config(_config(token="999:new"))
    assert len(made) == 2
    assert lock.acquires == 2
    service.stop()


def test_apply_config_events_only_does_not_restart(tmp_path):
    service, _lock, made = _service(tmp_path)
    service.start()
    events = {key: True for key in MESSAGE_EVENT_KEYS}
    events["task_end"] = False
    service.apply_config(_config(events=events))
    assert len(made) == 1
    service.stop()


def test_verify_async_callbacks(tmp_path):
    service, _lock, _made = _service(tmp_path)
    steps, done = [], []
    result = service.verify_async(
        "telegram", _config(), on_step=steps.append,
        on_done=done.append)
    assert result["status"] == "success"
    assert _wait_until(lambda: done)
    assert steps and steps[0].key == "config"
    assert done[0]["ok"] is True


def test_verify_async_single_flight(tmp_path):
    service, _lock, _made = _service(tmp_path)
    service._verify_state["running"] = True
    result = service.verify_async("telegram", _config())
    assert result["status"] == "error"


def test_recent_commands_limit(tmp_path):
    service, _lock, _made = _service(tmp_path)
    for index in range(35):
        service._record_command({"ts": float(index), "channel": "telegram",
                                 "chat_id": "123***789", "text": "/status",
                                 "ok": True, "error": ""})
    state = service.get_state()
    assert len(state["recent_commands"]) == 30


def test_queue_overflow_counts_dropped(tmp_path):
    service, _lock, _made = _service(tmp_path)
    for index in range(101):
        service._enqueue_raw({"key": "error", "title": str(index)})
    state = service.get_state()
    assert state["queue"]["dropped"] == 1
