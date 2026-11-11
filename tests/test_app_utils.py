"""app_utils 输入哨兵测试（2026-10-31 fail-safe 致任务线程裸崩的修复）。

验证点：
- Mouse 调用抛 pydirectinput.FailSafeException → 转 UserAbort（用户中断哨兵）
- 非 fail-safe 异常原样穿透，不被误转
- 消息兜底：后端替换/异常被包装后仍按 "fail-safe" 文案识别
- UserAbort 继承 BaseException：任务循环的 except Exception 兜底不吞它
"""
import pytest
from pydirectinput import FailSafeException

from src import app_utils


class _FakeMouse:
    """可编程 Mouse 替身（monkeypatch 到 app_utils.Mouse）。"""

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = []

    def mouse_move(self, x, y, move_duration=None):
        self.calls.append(("mouse_move", x, y))
        if self.exc is not None:
            raise self.exc

    def click(self, button="primary"):
        self.calls.append(("click", button))
        if self.exc is not None:
            raise self.exc

    def mouse_down(self, button="primary"):
        self.calls.append(("mouse_down", button))
        if self.exc is not None:
            raise self.exc

    def mouse_up(self, button="primary"):
        self.calls.append(("mouse_up", button))
        if self.exc is not None:
            raise self.exc


class _FakeWin:
    """human_click 坐标换算所需的最小窗口替身。"""

    def __init__(self, left=678, top=68):
        self.rect = type("Rect", (), {"left": left, "top": top})()


@pytest.fixture
def fake_mouse(monkeypatch):
    fake = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake)
    return fake


def test_human_move_failsafe_converts_to_user_abort(fake_mouse):
    fake_mouse.exc = FailSafeException("PyDirectInput fail-safe triggered")
    with pytest.raises(app_utils.UserAbort) as ei:
        app_utils.human_move(100, 200)
    assert "人工接管" in str(ei.value)


def test_human_move_failsafe_message_fallback(fake_mouse):
    # 类型信号丢失（后端替换/包装）时按消息兜底识别
    fake_mouse.exc = RuntimeError("PyAutoGUI fail-safe triggered from mouse")
    with pytest.raises(app_utils.UserAbort):
        app_utils.human_move(100, 200)


def test_human_move_other_exception_passthrough(fake_mouse):
    fake_mouse.exc = RuntimeError("unrelated")
    with pytest.raises(RuntimeError):
        app_utils.human_move(100, 200)


def test_human_move_normal_path(fake_mouse):
    app_utils.human_move(100, 200, duration=0.05)
    assert fake_mouse.calls == [("mouse_move", 100, 200)]


def test_human_click_failsafe_converts(fake_mouse):
    fake_mouse.exc = FailSafeException("fail-safe")
    with pytest.raises(app_utils.UserAbort):
        app_utils.human_click(_FakeWin(), 300, 400)


def test_precise_click_failsafe_converts(fake_mouse):
    fake_mouse.exc = FailSafeException("fail-safe")
    with pytest.raises(app_utils.UserAbort):
        app_utils.precise_click(_FakeWin(), 300, 400)


def test_user_abort_is_base_exception_not_swallowed():
    # 继承 BaseException 是设计契约：except Exception 兜底不得吞用户中断
    assert issubclass(app_utils.UserAbort, BaseException)
    assert not issubclass(app_utils.UserAbort, Exception)
    with pytest.raises(app_utils.UserAbort):
        try:
            raise app_utils.UserAbort("人工接管")
        except Exception:  # noqa: BLE001 —— 本测试专门验证不被该分支吞掉
            pytest.fail("except Exception 不应吞掉 UserAbort")


# ---- 静默点击模式（2026-11-03 静默修罗领取/巫医：点击走消息通道） ----

def test_silent_clicks_context_restores_flag():
    assert app_utils._SILENT_CLICK is False
    with app_utils.silent_clicks():
        assert app_utils._SILENT_CLICK is True
    assert app_utils._SILENT_CLICK is False


def test_human_click_silent_mode_delegates(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr("src.silent_click.send_silent_click",
                        lambda w, x=None, y=None, button="primary":
                        calls.append((w, x, y, button)))
    fake_mouse = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake_mouse)
    win = _FakeWin()
    app_utils.human_click(win, 100, 200)
    app_utils.human_click(win, 50, 60, button="secondary")
    assert calls == [(win, 100, 200, "primary"), (win, 50, 60, "secondary")]
    assert fake_mouse.calls == []  # 物理鼠标零操作


def test_precise_click_silent_mode_repeats(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr("src.silent_click.send_silent_click",
                        lambda w, x=None, y=None, button="primary": calls.append((x, y)))
    monkeypatch.setattr(app_utils, "Mouse", _FakeMouse())
    app_utils.precise_click(_FakeWin(), 10, 20, times=2, interval=0)
    assert calls == [(10, 20), (10, 20)]


def test_rapid_click_burst_physical_single_move(fake_mouse, monkeypatch):
    """冲刺连点（物理通道）：整批只移动一次，逐击按下/抬起。"""
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", False)
    app_utils.rapid_click_burst(_FakeWin(), 30, 40, times=3, press=0)
    assert fake_mouse.calls == [
        ("mouse_move", 708, 108),
        ("mouse_down", "primary"), ("mouse_up", "primary"),
        ("mouse_down", "primary"), ("mouse_up", "primary"),
        ("mouse_down", "primary"), ("mouse_up", "primary"),
    ]


def test_rapid_click_burst_silent_delegates(monkeypatch):
    """冲刺连点（静默通道）：整批一次投递，不逐击走 send_silent_click
    （后者每击 3×50ms 填充延迟，倒计时内推不到目标带）。"""
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr(
        "src.silent_click.send_silent_click_burst",
        lambda w, x=None, y=None, times=1, press=0.02, interval=0.0:
            calls.append((x, y, times, press)))
    monkeypatch.setattr("src.silent_click.send_silent_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("不应逐击投递")))
    app_utils.rapid_click_burst(_FakeWin(), 10, 20, times=5, press=0.03)
    assert calls == [(10, 20, 5, 0.03)]


def test_human_move_and_move_away_silent_mode_noop(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    fake_mouse = _FakeMouse()
    monkeypatch.setattr(app_utils, "Mouse", fake_mouse)
    app_utils.human_move(123, 456)
    app_utils.move_away(_FakeWin())
    assert fake_mouse.calls == []


# ---- 长按（钓鱼小游戏「长按」按钮：按住保持、松开抬起） ----


def test_mouse_hold_physical_press_release(monkeypatch, fake_mouse):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", False)
    hold = app_utils.MouseHold(_FakeWin(), 100, 200)
    assert hold.start() is True
    assert hold.active
    assert app_utils._INPUT_LOCK._is_owned()  # 长按期间持输入锁防交错
    hold.stop()
    assert not hold.active
    assert not app_utils._INPUT_LOCK._is_owned()
    assert fake_mouse.calls == [("mouse_move", 778, 268),
                                ("mouse_down", "primary"),
                                ("mouse_up", "primary")]


def test_mouse_hold_silent_channel(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    calls = []
    monkeypatch.setattr("src.silent_click.begin_silent_hold",
                        lambda w, x=None, y=None, button="primary":
                        calls.append(("begin", x, y, button))
                        or {"button": button})
    monkeypatch.setattr("src.silent_click.end_silent_hold",
                        lambda info: calls.append(("end", info["button"])))
    hold = app_utils.MouseHold(_FakeWin(), 10, 20)
    assert hold.start() is True
    assert hold.active
    assert app_utils._INPUT_LOCK._is_owned()
    hold.stop()
    assert not hold.active
    assert not app_utils._INPUT_LOCK._is_owned()
    assert calls == [("begin", 10, 20, "primary"), ("end", "primary")]


def test_mouse_hold_start_failure_releases_lock(monkeypatch):
    """静默长按按下失败（窗口坐标不可用）：不保持状态且不泄漏输入锁。"""
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    monkeypatch.setattr("src.silent_click.begin_silent_hold",
                        lambda *a, **k: None)
    hold = app_utils.MouseHold(_FakeWin(), 10, 20)
    assert hold.start() is False
    assert not hold.active
    assert not app_utils._INPUT_LOCK._is_owned()


def test_mouse_hold_stop_error_still_releases_lock(monkeypatch):
    """抬起投递异常：finally 仍释放输入锁（防后续点击全部饿死）。"""
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    monkeypatch.setattr("src.silent_click.begin_silent_hold",
                        lambda *a, **k: {"button": "primary"})

    def _boom(info):
        raise RuntimeError("抬起投递失败")

    monkeypatch.setattr("src.silent_click.end_silent_hold", _boom)
    hold = app_utils.MouseHold(_FakeWin(), 10, 20)
    assert hold.start() is True
    with pytest.raises(RuntimeError):
        hold.stop()
    assert not hold.active
    assert not app_utils._INPUT_LOCK._is_owned()


def test_mouse_hold_physical_start_failsafe_releases_lock(monkeypatch,
                                                         fake_mouse):
    """物理长按按下遇 fail-safe：转 UserAbort 且不泄漏输入锁。"""
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", False)
    fake_mouse.exc = FailSafeException("fail-safe")
    hold = app_utils.MouseHold(_FakeWin(), 10, 20)
    with pytest.raises(app_utils.UserAbort):
        hold.start()
    assert not hold.active
    assert not app_utils._INPUT_LOCK._is_owned()


def test_keyboard_ok_not_silent_always_true(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", False)
    assert app_utils.keyboard_ok(None) is True


def test_keyboard_ok_silent_no_win_false(monkeypatch):
    monkeypatch.setattr(app_utils, "_SILENT_CLICK", True)
    assert app_utils.keyboard_ok(None) is False


def test_set_silent_mode_toggle():
    app_utils.set_silent_mode(True)
    assert app_utils.silent_mode_on() is True
    app_utils.set_silent_mode(False)
    assert app_utils.silent_mode_on() is False


# ---- 寻路信号（摄妖香续用双通道的时钟） ----

def test_track_walk_signal(monkeypatch):
    monkeypatch.setattr(app_utils, "_last_track_walk", 0.0)  # 复位：防用例间状态泄漏
    fake_time = {"t": 1000.0}
    monkeypatch.setattr(app_utils.time, "time", lambda: fake_time["t"])
    assert app_utils.track_walk_idle_seconds() > 1e8  # 从未寻路：视为长期空闲
    app_utils.mark_track_walk()
    fake_time["t"] = 1600.0
    assert app_utils.track_walk_idle_seconds() == 600.0


def test_sheyaoxiang_idle_due(monkeypatch):
    fake_time = {"t": 1000.0}
    monkeypatch.setattr(app_utils.time, "time", lambda: fake_time["t"])
    monkeypatch.setattr(app_utils, "sheyaoxiang_due", lambda _w: True)
    app_utils.mark_track_walk()
    fake_time["t"] = 1000.0 + 9 * 60  # 空闲 9 分钟：未到 10 分钟阈值
    assert not app_utils.sheyaoxiang_idle_due(_win(), 10)
    fake_time["t"] = 1000.0 + 10 * 60  # 恰满 10 分钟：命中
    assert app_utils.sheyaoxiang_idle_due(_win(), 10)
    monkeypatch.setattr(app_utils, "sheyaoxiang_due", lambda _w: False)
    assert not app_utils.sheyaoxiang_idle_due(_win(), 10)  # 未到期不命中


def _win():
    return type("W", (), {"hwnd": 1})()


# ---- kv_read_fresh：主进程读任务进程运行时写入（2026-11-07） ----


def test_kv_read_fresh_reads_disk_not_snapshot(monkeypatch):
    """kv_read_fresh 锁内重读磁盘文件取键值；KeyValue.get 读本进程
    旧快照读不到对侧进程 kv_save_merged 的写入（组号入口不展示的
    根因回归测试）。"""
    from ascript.windows.system.key_value import KeyValue

    class _FakeLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    disk = {"group_detected": True}
    monkeypatch.setattr(KeyValue, "_initialize", lambda: None)
    monkeypatch.setattr(KeyValue, "_lock", _FakeLock())
    monkeypatch.setattr(KeyValue, "_load_data", lambda: dict(disk))
    monkeypatch.setattr(KeyValue, "get", lambda key, default=None: default)  # 旧快照恒无
    assert app_utils.kv_read_fresh("group_detected", False) is True
    assert app_utils.kv_read_fresh("missing", "dft") == "dft"
    # 文件读取异常回退 default
    monkeypatch.setattr(KeyValue, "_load_data",
                        lambda: (_ for _ in ()).throw(OSError("disk")))
    assert app_utils.kv_read_fresh("group_detected", False) is False


# ---- norm_name_for_cmp：角色名易混字符归一化（2026-11-09） ----


def test_norm_name_for_cmp_confusables():
    """〃/ㄨ/ˉ 家族在游戏字体下同形、OCR 稳定误读，两侧归一后同类。"""
    assert app_utils.norm_name_for_cmp("〃忆ㄨ嚣魂ˉ") == "q忆x嚣魂i"
    assert app_utils.norm_name_for_cmp("”忆×嚣魂一") == "q忆x嚣魂i"
    assert app_utils.norm_name_for_cmp("碧桃娘子") == "碧桃娘子"
    assert app_utils.norm_name_for_cmp("") == ""
    assert app_utils.norm_name_for_cmp(None) == ""


# ---- 角色维度状态按角色隔离（data/<角色>/state.json，2026-11-11） ----


class _RoleWin:
    def __init__(self, title):
        self.title = title


def _patch_role_data(monkeypatch, tmp_path):
    from ascript.windows.system.key_value import KeyValue
    from src import user_data
    monkeypatch.setattr(user_data, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(user_data, "_probe_role_id", lambda: "1001")
    monkeypatch.setattr(user_data, "_ROLE_CACHE", {"id": None, "ts": 0.0})
    monkeypatch.setattr(user_data, "is_migrated", lambda: False)
    monkeypatch.setattr(KeyValue, "get", lambda key, default=None: default)
    return user_data


def test_char_state_isolated_by_role(monkeypatch, tmp_path):
    _patch_role_data(monkeypatch, tmp_path)
    w1 = _RoleWin("服务器 - 角色甲（ID：1001）")
    w2 = _RoleWin("服务器 - 角色乙（ID：2002）")
    app_utils.char_state_set("mingwang_race", w1, {"date": "d", "race": "魔族"})
    assert app_utils.char_state_get("mingwang_race", w1)["race"] == "魔族"
    assert app_utils.char_state_get("mingwang_race", w2) is None
    assert (tmp_path / "data" / "1001" / "state.json").exists()
    assert not (tmp_path / "data" / "2002" / "state.json").exists()


def test_task_progress_isolated_by_role(monkeypatch, tmp_path):
    user_data = _patch_role_data(monkeypatch, tmp_path)
    w1 = _RoleWin("服务器 - 角色甲（ID：1001）")
    w2 = _RoleWin("服务器 - 角色乙（ID：2002）")
    app_utils.task_progress_add(w1, "修罗", 3)
    assert app_utils.task_progress_done(w1, "修罗") == 3
    assert app_utils.task_progress_done(w2, "修罗") == 0
    entry = user_data.get_state("task_progress", "1001")
    assert entry["modes"] == {"修罗": 3}


def test_kv_remove_many_merged(monkeypatch, tmp_path):
    import json
    import threading
    from ascript.windows.system.key_value import KeyValue
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"a": 1, "b": 2, "c": 3}), encoding="utf-8")
    monkeypatch.setattr(KeyValue, "_initialize", lambda: None)
    monkeypatch.setattr(KeyValue, "_file_path", path)
    monkeypatch.setattr(KeyValue, "_lock", threading.RLock())
    monkeypatch.setattr(KeyValue, "_load_data",
                        lambda: json.loads(path.read_text(encoding="utf-8")))
    removed = app_utils.kv_remove_many_merged(["a", "c", "missing"])
    assert removed == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"b": 2}
    assert KeyValue._data == {"b": 2}
