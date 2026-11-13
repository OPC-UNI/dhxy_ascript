"""任务进度汇总测试（从 core.settings 拆出的 device.progress）。"""
import time

from src.device import progress


def _today():
    return time.strftime("%Y-%m-%d")


def test_fresh_progress_reads_today_state(monkeypatch):
    monkeypatch.setattr(
        progress.user_data, "get_state",
        lambda *a, **k: {"date": _today(),
                         "modes": {"客栈": 3, "五倍": 2.0, "bad": "x"}})
    monkeypatch.setattr(progress.user_data, "is_migrated", lambda: True)
    assert progress._fresh_progress_modes() == {"客栈": 3, "五倍": 2}


def test_fresh_progress_ignores_other_day(monkeypatch):
    monkeypatch.setattr(
        progress.user_data, "get_state",
        lambda *a, **k: {"date": "2000-01-01", "modes": {"客栈": 3}})
    monkeypatch.setattr(progress.user_data, "is_migrated", lambda: True)
    assert progress._fresh_progress_modes() == {}


def test_fresh_progress_bad_state_returns_empty(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("state 损坏")
    monkeypatch.setattr(progress.user_data, "get_state", _raise)
    monkeypatch.setattr(progress.user_data, "is_migrated", lambda: True)
    assert progress._fresh_progress_modes() == {}


def test_fresh_progress_legacy_config_fallback(monkeypatch):
    """未迁移且 state 无数据：回退 config.json 的角色键（旧版兼容）。"""
    monkeypatch.setattr(progress.user_data, "get_state", lambda *a, **k: None)
    monkeypatch.setattr(progress.user_data, "is_migrated", lambda: False)
    monkeypatch.setattr(progress, "_legacy_progress_char_key", lambda: "ck")

    class _KV:
        @staticmethod
        def _initialize():
            pass

        @staticmethod
        def _load_data():
            return {progress._TASK_PROGRESS_KEY: {
                "ck": {"date": _today(), "modes": {"客栈": 2}}}}

    monkeypatch.setattr(progress, "KeyValue", _KV)
    assert progress._fresh_progress_modes() == {"客栈": 2}


def test_legacy_progress_char_key(monkeypatch):
    monkeypatch.setattr(progress.game, "find_main_window", lambda: object())
    monkeypatch.setattr(progress.app_utils, "char_key", lambda win: "ck")
    assert progress._legacy_progress_char_key() == "ck"

    monkeypatch.setattr(progress.game, "find_main_window", lambda: None)
    assert progress._legacy_progress_char_key() is None

    def _raise():
        raise RuntimeError("枚举失败")
    monkeypatch.setattr(progress.game, "find_main_window", _raise)
    assert progress._legacy_progress_char_key() is None


def test_task_progress_summary(monkeypatch):
    monkeypatch.setattr(progress, "load_selected_modes",
                        lambda: ["客栈", "五倍", "修罗"])
    monkeypatch.setattr(progress, "load_task_counts",
                        lambda: {"客栈": 20, "五倍": 0, "修罗": 90})
    monkeypatch.setattr(progress, "_fresh_progress_modes",
                        lambda: {"客栈": 25, "五倍": 3, "修罗": 10})
    result = progress.task_progress_summary()
    # 五倍次数上限 0（不限）不计入分母；客栈完成超出上限按上限计。
    assert result["total"] == 110
    assert result["done"] == 30
    assert result["percent"] == 27
    assert result["per_task"]["客栈"] == {
        "done": 20, "count": 20, "percent": 100}
    assert result["per_task"]["五倍"] == {
        "done": 3, "count": 0, "percent": 0}
    assert result["per_task"]["修罗"] == {
        "done": 10, "count": 90, "percent": 11}


def test_task_progress_summary_zero_total(monkeypatch):
    monkeypatch.setattr(progress, "load_selected_modes", lambda: ["五倍"])
    monkeypatch.setattr(progress, "load_task_counts", lambda: {"五倍": 0})
    monkeypatch.setattr(progress, "_fresh_progress_modes",
                        lambda: {"五倍": 5})
    result = progress.task_progress_summary()
    assert result == {
        "percent": 0, "done": 0, "total": 0,
        "per_task": {"五倍": {"done": 5, "count": 0, "percent": 0}},
    }
