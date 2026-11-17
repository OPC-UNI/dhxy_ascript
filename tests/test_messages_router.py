"""消息路由：解析/白名单/限频/只读指令格式化。"""
from src.app.messages.channel_base import InboundCommand, SendResult
from src.app.messages.router import MessageRouter, parse_command


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeHost:
    def __init__(self):
        self.running = True
        self.status = {"task": "五环", "progress": "1/2", "phase": "领取任务",
                       "battle": "第 1 场", "team": "队长 · 5 人",
                       "bars": "血 90% 蓝 80%"}
        self.progress = {"percent": 50, "done": 5, "total": 10,
                         "per_task": {"五环": {"done": 1, "count": 2},
                                      "修罗": {"done": 4, "count": 0}}}
        self.capture_result = {"ok": True, "image": b"png", "w": 100,
                               "h": 50, "black": False}
        self.log_result = {"ok": True, "lines": ["a", "b"],
                           "path": "logs/x.log"}
        self.log_n = None
        self.capture_calls = 0
        self.identity_text = "角色A"
        self.game_present = True
        self.exit_result = {"ok": True, "message": "游戏客户端已关闭"}
        self.exit_calls = []

    def is_running(self):
        return self.running

    def runtime_status(self):
        return self.status

    def task_progress(self):
        return self.progress

    def identity(self):
        return self.identity_text

    def capture(self):
        self.capture_calls += 1
        return self.capture_result

    def log_tail(self, n):
        self.log_n = n
        return self.log_result

    def game_window_present(self):
        return self.game_present

    def exit_game(self, progress=None):
        self.exit_calls.append(progress)
        if progress is not None:
            progress("正在停止任务…")
        return dict(self.exit_result)


class FakeAdapter:
    def __init__(self):
        self.texts = []
        self.photos = []
        self.fail = False

    def send_text(self, chat_id, text):
        if self.fail:
            return SendResult(False, "send failed")
        self.texts.append((chat_id, text))
        return SendResult(True)

    def send_photo(self, chat_id, image, caption="", filename=""):
        if self.fail:
            return SendResult(False, "photo failed")
        self.photos.append((chat_id, image, caption))
        return SendResult(True)


def _incoming(text, chat_id="42"):
    return InboundCommand(channel="telegram", chat_id=chat_id, text=text)


def _router(host=None, clock=None):
    records = []
    router = MessageRouter(host or FakeHost(),
                           clock=clock or Clock(),
                           record=records.append)
    return router, records


def test_parse_commands_and_aliases():
    assert parse_command("/status") == ("/status", "")
    assert parse_command("/status@MyBot") == ("/status", "")
    assert parse_command("/LOG") == ("/log", "")
    assert parse_command(" 状态 ") == ("/status", "")
    assert parse_command("运行状态") == ("/status", "")
    assert parse_command("日志 30") == ("/log", "30")
    assert parse_command("/log 30") == ("/log", "30")
    assert parse_command("退出游戏") == ("/exitgame", "")
    assert parse_command("/exitgame confirm") == ("/exitgame", "confirm")
    assert parse_command("/foo") == ("unknown", "/foo")
    assert parse_command("你好") is None
    assert parse_command("") is None


def test_help_status_task_log():
    router, records = _router()
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/help"), ["42"])
    router.dispatch(adapter, _incoming("/status"), ["42"])
    router.dispatch(adapter, _incoming("/task"), ["42"])
    router.dispatch(adapter, _incoming("日志 30"), ["42"])
    for _, text in adapter.texts:
        assert text.startswith("角色：角色A\n")
    assert "啄木鸟消息服务" in adapter.texts[0][1]
    assert "任务状态：运行中" in adapter.texts[1][1]
    assert "当前任务：五环 1/2" in adapter.texts[1][1]
    assert adapter.texts[1][1].count("角色：") == 1
    assert "今日总进度：50%（5/10）" in adapter.texts[2][1]
    assert "修罗：4（不限）" in adapter.texts[2][1]
    assert "最近 2 行日志" in adapter.texts[3][1]
    assert records[-1]["command"] == "/log"
    assert records[-1]["ok"] is True


def test_exit_game_requires_confirmation():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/exitgame"), ["42"])
    assert "confirm" in adapter.texts[-1][1]
    assert host.exit_calls == []
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert len(host.exit_calls) == 1
    assert "正在停止任务" in adapter.texts[-2][1]
    assert "游戏客户端已关闭" in adapter.texts[-1][1]
    assert adapter.texts[-1][1].startswith("角色：角色A\n✔")


def test_exit_game_without_game_skips_confirmation():
    """游戏未启动：/exitgame 直接回复，不建立待确认、不执行退出。"""
    host = FakeHost()
    host.game_present = False
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/exitgame"), ["42"])
    assert host.exit_calls == []
    assert "游戏未启动" in adapter.texts[-1][1]
    assert "confirm" not in adapter.texts[-1][1]
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert host.exit_calls == []
    assert "没有待确认" in adapter.texts[-1][1]


def test_exit_game_confirm_without_preview():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert host.exit_calls == []
    assert "没有待确认" in adapter.texts[-1][1]


def test_exit_game_confirmation_expires_and_cancels():
    clock = Clock()
    host = FakeHost()
    router, _ = _router(host, clock=clock)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("退出游戏"), ["42"])
    clock.advance(121)
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert host.exit_calls == []
    router.dispatch(adapter, _incoming("/exitgame"), ["42"])
    router.dispatch(adapter, _incoming("/status"), ["42"])
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert host.exit_calls == []


def test_exit_game_failure_reported():
    host = FakeHost()
    host.exit_result = {"ok": False, "message": "队伍处理失败：面板未打开"}
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/exitgame"), ["42"])
    router.dispatch(adapter, _incoming("/exitgame confirm"), ["42"])
    assert "✘" in adapter.texts[-1][1]
    assert "队伍处理失败" in adapter.texts[-1][1]


def test_reply_without_identity():
    host = FakeHost()
    host.identity_text = ""
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/task"), ["42"])
    assert not adapter.texts[-1][1].startswith("角色：")


def test_log_argument_clamped():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/log 999"), ["42"])
    assert host.log_n == 100
    router.dispatch(adapter, _incoming("/log abc"), ["42"])
    assert host.log_n == 20


def test_whitelist_rejected_silently():
    router, records = _router()
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/status", chat_id="999"), ["42"])
    assert adapter.texts == []
    assert records[-1]["rejected"] is True
    assert records[-1]["error"] == "chat_id 未授权"


def test_rate_limit_and_notice():
    clock = Clock()
    router, records = _router(clock=clock)
    adapter = FakeAdapter()
    for _ in range(5):
        router.dispatch(adapter, _incoming("/status"), ["42"])
    assert len(adapter.texts) == 5
    router.dispatch(adapter, _incoming("/status"), ["42"])
    assert records[-1]["error"] == "限频"
    assert "操作太频繁" in adapter.texts[-1][1]
    router.dispatch(adapter, _incoming("/status"), ["42"])
    assert records[-1]["error"] == "限频"
    clock.advance(4.0)
    router.dispatch(adapter, _incoming("/status"), ["42"])
    assert records[-1]["error"] == ""


def test_global_rate_limit():
    router, records = _router()
    adapter = FakeAdapter()
    chats = ["1", "2", "3", "4", "5"]
    for index in range(20):
        router.dispatch(adapter, _incoming("/status", chats[index % 5]),
                        chats)
        assert records[-1]["error"] == ""
    router.dispatch(adapter, _incoming("/status", "1"), chats)
    assert records[-1]["error"] == "限频"


def test_capture_and_cooldown():
    clock = Clock()
    host = FakeHost()
    router, records = _router(host, clock=clock)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/capture"), ["42"])
    assert len(adapter.photos) == 1
    assert adapter.photos[0][2].startswith("角色：角色A\n游戏窗口截图")
    assert "100x50" in adapter.photos[0][2]
    assert records[-1]["ok"] is True
    router.dispatch(adapter, _incoming("/capture"), ["42"])
    assert len(adapter.photos) == 1
    assert records[-1]["error"] == "截图冷却"
    assert any("冷却" in text for _, text in adapter.texts)
    clock.advance(3.1)
    router.dispatch(adapter, _incoming("截图"), ["42"])
    assert len(adapter.photos) == 2


def test_capture_failure_messages():
    clock = Clock()
    host = FakeHost()
    host.capture_result = {"ok": False, "reason": "minimized"}
    router, _ = _router(host, clock=clock)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/capture"), ["42"])
    assert "最小化" in adapter.texts[-1][1]
    clock.advance(3.1)
    host.capture_result = {"ok": False, "reason": "no_window"}
    router.dispatch(adapter, _incoming("/capture"), ["42"])
    assert "未找到游戏窗口" in adapter.texts[-1][1]


def test_unknown_and_plain_text():
    router, records = _router()
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/foo"), ["42"])
    assert "未知指令" in adapter.texts[-1][1]
    before = len(records)
    router.dispatch(adapter, _incoming("随便聊聊"), ["42"])
    assert len(records) == before


def test_reply_to_origin_chat():
    router, _ = _router()
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/status", chat_id="42"), ["42", "43"])
    assert adapter.texts[-1][0] == "42"
