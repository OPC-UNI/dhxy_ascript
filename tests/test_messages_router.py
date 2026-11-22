"""消息路由：解析/白名单/限频/查询与控制指令。"""
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
        self.scheme_list = {"active": "默认", "names": ["默认", "方案A"]}
        self.switch_result = {"status": "success", "active": "方案A"}
        self.switch_calls = []
        self.start_result = {"status": "success", "running": True}
        self.stop_result = {"status": "success", "running": False}
        self.start_calls = 0
        self.stop_calls = 0
        # 多窗口 all：逐窗口行数据 + 全部启停
        self.any_running = True
        self.fleet_starting = False
        self.fleet_rows = [
            {"role_id": "111", "label": "角色A", "live": True, "enabled": True,
             "running": True, "can_start": False, "selected": True,
             "seq": 0, "hwnd": 111,
             "status": {"task": "修罗", "phase": "战斗中 · 第 3 场"},
             "progress": {"percent": 51, "done": 51, "total": 100}},
            {"role_id": "222", "label": "角色B", "live": True, "enabled": True,
             "running": False, "can_start": True, "selected": False,
             "seq": 1, "hwnd": 222, "status": None,
             "progress": {"percent": 83, "done": 10, "total": 12}},
        ]
        self.start_all_result = {"status": "success", "running": True,
                                 "starting": 1}
        self.stop_all_result = {"status": "success", "running": False}
        self.start_all_calls = 0
        self.stop_all_calls = 0
        self.select_calls = []
        self.disband_result = {"ok": True, "message": "已解散队伍"}
        self.shout_result = {"ok": True, "message": "已喊话：百次五倍来人，效率队"}
        self.disband_calls = 0
        self.shout_calls = []

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

    def list_schemes(self):
        return dict(self.scheme_list)

    def switch_scheme(self, name):
        self.switch_calls.append(name)
        return dict(self.switch_result)

    def start_task(self):
        self.start_calls += 1
        self.running = bool(self.start_result.get("running"))
        return dict(self.start_result)

    def stop_task(self):
        self.stop_calls += 1
        self.running = bool(self.stop_result.get("running"))
        return dict(self.stop_result)

    def start_all(self):
        self.start_all_calls += 1
        return dict(self.start_all_result)

    def stop_all(self):
        self.stop_all_calls += 1
        return dict(self.stop_all_result)

    def is_any_running(self):
        return self.any_running

    def is_fleet_starting(self):
        return self.fleet_starting

    def fleet_state(self):
        return {"rows": [dict(r) for r in self.fleet_rows]}

    def select_window(self, role_id, hwnd=None):
        self.select_calls.append((role_id, hwnd))
        return {"status": "success", "selected": role_id,
                "selected_hwnd": hwnd or 0}

    def disband_team(self):
        self.disband_calls += 1
        return dict(self.disband_result)

    def shout(self, text=""):
        self.shout_calls.append(text)
        return dict(self.shout_result)


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
    assert parse_command("方案") == ("/schemes", "")
    assert parse_command("方案列表") == ("/schemes", "")
    assert parse_command("获取方案列表") == ("/schemes", "")
    assert parse_command("切换方案 方案A") == ("/switch", "方案A")
    assert parse_command("/switch 方案A") == ("/switch", "方案A")
    assert parse_command("开始") == ("/start", "")
    assert parse_command("启动任务") == ("/start", "")
    assert parse_command("/start") == ("/start", "")
    assert parse_command("停止") == ("/stop", "")
    assert parse_command("/stop") == ("/stop", "")
    assert parse_command("解散队伍") == ("/disband", "")
    assert parse_command("解散") == ("/disband", "")
    assert parse_command("/disband") == ("/disband", "")
    assert parse_command("喊话") == ("/shout", "")
    assert parse_command("喊话 百次五倍来人") == ("/shout", "百次五倍来人")
    assert parse_command("/shout 来人") == ("/shout", "来人")
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


def test_schemes_list_marks_active():
    host = FakeHost()
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/schemes"), ["42"])
    text = adapter.texts[-1][1]
    assert "配置方案 2 个，当前：默认" in text
    assert "· 默认（当前）" in text
    assert "· 方案A" in text
    assert "/switch" in text
    assert records[-1]["command"] == "/schemes"
    assert records[-1]["ok"] is True


def test_switch_scheme_success():
    host = FakeHost()
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("切换方案 方案A"), ["42"])
    assert host.switch_calls == ["方案A"]
    assert "✔ 已切换方案：「方案A」" in adapter.texts[-1][1]
    assert adapter.texts[-1][1].startswith("角色：角色A\n")
    assert records[-1]["command"] == "/switch"


def test_switch_scheme_rejects_unknown_and_missing_name():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/switch"), ["42"])
    assert "用法" in adapter.texts[-1][1]
    router.dispatch(adapter, _incoming("/switch 不存在的方案"), ["42"])
    assert "方案不存在" in adapter.texts[-1][1]
    assert "默认、方案A" in adapter.texts[-1][1]
    assert host.switch_calls == []


def test_switch_scheme_already_active_and_guard_error():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/switch 默认"), ["42"])
    assert "已是当前方案" in adapter.texts[-1][1]
    assert host.switch_calls == []
    host.switch_result = {"status": "error",
                          "msg": "任务运行中，请先停止任务再操作"}
    router.dispatch(adapter, _incoming("/switch 方案A"), ["42"])
    assert "✘ 切换失败" in adapter.texts[-1][1]
    assert "任务运行中" in adapter.texts[-1][1]
    assert host.switch_calls == ["方案A"]


def test_start_task_when_running_skips_start():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/start"), ["42"])
    assert "任务已在运行中" in adapter.texts[-1][1]
    assert host.start_calls == 0


def test_start_refused_without_game_window():
    host = FakeHost()
    host.running = False
    host.game_present = False
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/start"), ["42"])
    assert "未检测到游戏窗口" in adapter.texts[-1][1]
    assert host.start_calls == 0


def test_start_task_success_and_failure():
    host = FakeHost()
    host.running = False
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("开始"), ["42"])
    assert host.start_calls == 1
    assert "✔ 任务已启动" in adapter.texts[-1][1]
    assert records[-1]["ok"] is True
    host.running = False
    host.start_result = {"status": "error",
                         "msg": "授权校验未通过，任务未启动：已过期"}
    router.dispatch(adapter, _incoming("/start"), ["42"])
    assert "✘ 启动失败" in adapter.texts[-1][1]
    assert "授权校验未通过" in adapter.texts[-1][1]


def test_start_stop_busy_message():
    host = FakeHost()
    host.running = False
    host.start_result = {"status": "error", "msg": "BUSY"}
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/start"), ["42"])
    assert "任务正在启停中" in adapter.texts[-1][1]
    host.running = True
    host.stop_result = {"status": "error", "msg": "BUSY"}
    router.dispatch(adapter, _incoming("/stop"), ["42"])
    assert "任务正在启停中" in adapter.texts[-1][1]


def test_stop_task_not_running_and_success():
    host = FakeHost()
    host.running = False
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("停止"), ["42"])
    assert "任务当前未运行" in adapter.texts[-1][1]
    assert host.stop_calls == 0
    host.running = True
    router.dispatch(adapter, _incoming("/stop"), ["42"])
    assert host.stop_calls == 1
    assert "✔ 任务已停止" in adapter.texts[-1][1]


def test_start_all_submits_all_windows():
    """ /start all：走全部启动入口，不走单窗口 start_task。"""
    host = FakeHost()
    host.running = False
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/start all"), ["42"])
    assert host.start_all_calls == 1
    assert host.start_calls == 0
    assert "已提交启动" in adapter.texts[-1][1]
    assert adapter.texts[-1][1].startswith("✔")  # all 回复不带单角色前缀
    assert records[-1]["ok"] is True


def test_start_all_chinese_alias_and_no_candidates():
    host = FakeHost()
    host.running = False
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("启动 全部"), ["42"])
    assert host.start_all_calls == 1
    host.fleet_rows = [dict(r, can_start=False) for r in host.fleet_rows]
    router.dispatch(adapter, _incoming("/start all"), ["42"])
    assert host.start_all_calls == 1  # 没有可启动窗口时不调用
    assert "没有可启动的窗口" in adapter.texts[-1][1]


def test_stop_all_stops_everything_without_role_prefix():
    host = FakeHost()
    host.any_running = True
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/stop all"), ["42"])
    assert host.stop_all_calls == 1
    assert host.stop_calls == 0
    assert "已停止全部窗口任务" in adapter.texts[-1][1]
    assert adapter.texts[-1][1].startswith("✔")


def test_stop_all_cancels_pending_start_and_skips_when_idle():
    host = FakeHost()
    host.any_running = False
    host.fleet_starting = True
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("停止 全部"), ["42"])
    assert host.stop_all_calls == 1  # 全部启动在途：stop_all 负责取消
    host.any_running = False
    host.fleet_starting = False
    router.dispatch(adapter, _incoming("/stop all"), ["42"])
    assert host.stop_all_calls == 1
    assert "任务当前未运行" in adapter.texts[-1][1]


def test_status_all_lists_each_window():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/status all"), ["42"])
    text = adapter.texts[-1][1]
    assert text.startswith("多窗口状态：2 个窗口 / 1 个运行中")
    assert "· 角色A：运行中 · 修罗 · 战斗中 · 第 3 场" in text
    assert "· 角色B：未运行（今日 10/12）" in text


def test_task_all_lists_each_window_progress():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/task 全部"), ["42"])
    text = adapter.texts[-1][1]
    assert text.startswith("多窗口进度：2 个窗口")
    assert "· 角色A：51%（51/100）" in text
    assert "· 角色B：83%（10/12）" in text


def test_window_list_and_switch_by_index():
    """/window 列表（标记当前）与按序号切换；后续指令作用于新窗口。"""
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/window"), ["42"])
    text = adapter.texts[-1][1]
    assert text.startswith("多窗口（2 个 / 1 个运行中）")
    assert "● 1. 角色A（ID 111 · 当前 · 运行中 · 修罗 · 战斗中 · 第 3 场）" in text
    assert "○ 2. 角色B（ID 222 · 未运行）" in text
    router.dispatch(adapter, _incoming("/window 2"), ["42"])
    assert host.select_calls == [("222", 222)]
    assert "已切换到「角色B」" in adapter.texts[-1][1]


def test_window_switch_by_name_id_and_already_selected():
    host = FakeHost()
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("切换窗口 角色B"), ["42"])
    assert host.select_calls == [("222", 222)]
    router.dispatch(adapter, _incoming("/window 222"), ["42"])
    assert host.select_calls == [("222", 222), ("222", 222)]
    router.dispatch(adapter, _incoming("/window 1"), ["42"])
    assert "已是当前窗口" in adapter.texts[-1][1]
    assert len(host.select_calls) == 2


def test_window_switch_rejects_closed_and_unknown():
    host = FakeHost()
    host.fleet_rows[1]["live"] = False
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/window 2"), ["42"])
    assert "窗口已关闭，无法切换" in adapter.texts[-1][1]
    assert host.select_calls == []
    router.dispatch(adapter, _incoming("/window 不存在"), ["42"])
    assert "未找到该窗口" in adapter.texts[-1][1]


def test_disband_team_success_and_failure():
    host = FakeHost()
    host.running = False
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("解散队伍"), ["42"])
    assert host.disband_calls == 1
    assert "✔ 已解散队伍" in adapter.texts[-1][1]
    assert adapter.texts[-1][1].startswith("角色：角色A\n")
    assert records[-1]["command"] == "/disband"
    host.disband_result = {"ok": False,
                           "message": "面板中未找到「解散队伍」按钮（可能不是队长）"}
    router.dispatch(adapter, _incoming("/disband"), ["42"])
    assert "✘" in adapter.texts[-1][1]
    assert "可能不是队长" in adapter.texts[-1][1]


def test_disband_refused_while_running():
    host = FakeHost()  # running=True
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/disband"), ["42"])
    assert host.disband_calls == 0
    assert "任务运行中" in adapter.texts[-1][1]
    assert "/stop" in adapter.texts[-1][1]


def test_shout_with_text_and_without():
    host = FakeHost()
    host.running = False
    router, records = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("喊话 百次五倍来人"), ["42"])
    assert host.shout_calls == ["百次五倍来人"]
    assert "✔ 已喊话" in adapter.texts[-1][1]
    assert records[-1]["command"] == "/shout"
    router.dispatch(adapter, _incoming("/shout"), ["42"])
    assert host.shout_calls[-1] == ""  # 空内容由 host 取所选任务的喊话设置


def test_shout_allowed_while_running_and_failure():
    """任务运行中也可喊话（host 侧用跨进程输入互斥排队），失败如实回复。"""
    host = FakeHost()  # running=True
    router, _ = _router(host)
    adapter = FakeAdapter()
    router.dispatch(adapter, _incoming("/shout 来人"), ["42"])
    assert host.shout_calls == ["来人"]
    assert "✔ 已喊话" in adapter.texts[-1][1]
    host.shout_result = {
        "ok": False,
        "message": "未选中五倍/修罗/种族比武任务，没有可用的喊话文案；"
                   "请带上内容：/shout 喊话内容"}
    router.dispatch(adapter, _incoming("喊话"), ["42"])
    assert "✘" in adapter.texts[-1][1]
    assert "喊话内容" in adapter.texts[-1][1]
