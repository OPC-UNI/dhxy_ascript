"""任务统计（task_stats.json）累积/持久化逻辑测试（统计页后端）。

验证点：
- record_round 计入会话/当日/累计/当前模式；启动期多模式串不计模式
- _stats_tick 按增量计战斗场次与耗时并落盘；session 快照正确
- daily 按模式拆分（统计页「今日」视图数据源）
- 文件缺失/损坏→空结构；clear 删除文件；daily 90 天修剪
"""
import time
from datetime import datetime, timedelta

import pytest

from src import debug_overlay as do


@pytest.fixture
def stats_env(monkeypatch, tmp_path):
    """隔离：统计文件指向临时目录 + 重置会话统计全局（不得碰真实
    工程目录的 task_stats.json）。"""
    path = tmp_path / "task_stats.json"
    monkeypatch.setattr(do, "_stats_path", lambda: path)
    monkeypatch.setattr(do, "_stats_data", None)
    monkeypatch.setattr(do, "_session_rounds", 0)
    monkeypatch.setattr(do, "_session_battles", 0)
    monkeypatch.setattr(do, "_session_seconds", 0.0)
    monkeypatch.setattr(do, "_battle_no", 0)
    monkeypatch.setattr(do, "_last_battle_seen", 0)
    monkeypatch.setattr(do, "_last_tick", None)
    monkeypatch.setattr(do, "_session_started_at", 1000.0)
    monkeypatch.setattr(do, "_task", "修罗")
    yield path


def test_record_round_counts_total_daily_mode(stats_env):
    do.record_round()
    do.record_round()
    do._stats_tick(1002.0)  # 落盘
    data = do._stats_load()
    assert data["total"]["rounds"] == 2
    assert data["total"]["modes"]["修罗"]["rounds"] == 2
    day = data["daily"][do._today_str()]
    assert day["rounds"] == 2
    assert day["modes"]["修罗"]["rounds"] == 2
    assert data["session"]["rounds"] == 2
    assert data["session"]["mode"] == "修罗"


def test_record_round_no_mode_attribution_on_multi_mode_string(stats_env, monkeypatch):
    """启动期 set_task 为「修罗、五倍」多模式串：只计总数不计模式。"""
    monkeypatch.setattr(do, "_task", "修罗、五倍")
    do.record_round()
    data = do._stats_ensure()
    assert data["total"]["rounds"] == 1
    assert data["total"]["modes"] == {}
    day = data["daily"][do._today_str()]
    assert day["rounds"] == 1
    assert day["modes"] == {}


def test_stats_tick_accumulates_battles_and_seconds(stats_env, monkeypatch):
    monkeypatch.setattr(do, "_last_tick", 1000.0)
    monkeypatch.setattr(do, "_battle_no", 5)
    do._stats_tick(1002.0)
    data = do._stats_load()
    assert data["total"]["seconds"] == pytest.approx(2.0)
    assert data["total"]["battles"] == 5
    assert data["total"]["modes"]["修罗"]["battles"] == 5
    assert data["total"]["modes"]["修罗"]["seconds"] == pytest.approx(2.0)
    assert data["session"]["battles"] == 5
    assert data["session"]["seconds"] == pytest.approx(2.0)
    # 第二次 tick：战斗序号未变不计新战斗，耗时继续累计
    do._stats_tick(1004.0)
    data = do._stats_load()
    assert data["total"]["battles"] == 5
    assert data["total"]["seconds"] == pytest.approx(4.0)


def test_first_tick_counts_zero_elapsed(stats_env):
    """首个 tick（_last_tick 为 None）不计耗时、战斗序号为 0 不虚增。"""
    do._stats_tick(1002.0)
    data = do._stats_load()
    assert data["total"]["seconds"] == 0
    assert data["total"]["battles"] == 0


def test_read_task_stats_daily_window_7_days(stats_env):
    """最近 7 天窗口 + 今日包含；session 快照透传。"""
    data = do._stats_ensure()
    for i in range(10):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        data["daily"][d] = {"rounds": 1, "battles": 1, "seconds": 1, "modes": {}}
    data["session"] = {"rounds": 3, "battles": 2, "seconds": 9, "mode": "修罗"}
    do._stats_write(data)
    got = do.read_task_stats()
    assert len(got["daily"]) == 7
    assert do._today_str() in got["daily"]
    assert got["session"]["rounds"] == 3
    assert got["total"]["rounds"] == 0


def test_missing_or_corrupt_file_returns_empty(stats_env):
    got = do.read_task_stats()
    assert got["total"]["rounds"] == 0
    assert got["daily"] == {}
    assert got["session"] is None
    stats_env.write_text("{not json", encoding="utf-8")
    assert do._stats_load() == do._empty_stats()


def test_clear_task_stats_removes_file(stats_env):
    do.record_round()
    do._stats_tick(time.time())
    assert stats_env.exists()
    assert do.clear_task_stats()["status"] == "success"
    assert not stats_env.exists()


def test_daily_trim_keeps_90_days(stats_env):
    data = do._stats_ensure()
    for i in range(120):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        data["daily"][d] = {"rounds": 0, "battles": 0, "seconds": 0, "modes": {}}
    do._stats_write(data)
    reloaded = do._stats_load()
    assert len(reloaded["daily"]) <= do.STATS_KEEP_DAYS
