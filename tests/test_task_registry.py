"""app.task_registry：任务模式注册表（执行侧单点登记）。

锁定三件事：
1. 注册表模式集合与顺序与 core.settings.TASK_MODES 一致（「仅战斗」除外，
   它是 main._battle_only_loop 的特殊模式，不走任务主循环）。
2. 每个 TaskSpec 的 run_loop 可调用、模式名唯一。
3. run_task_mode 的前置钩子顺序、未知模式跳过行为与旧 main._run_task_mode
   一致。
"""
import re
from pathlib import Path

from src.app import task_registry as registry
from src.core import debug_overlay, settings
from src.tasks import (kezhan, mingwang, sanbaihuan, wuhuan, wubei, xiuluo,
                       xuanshu, zhongzu)


def test_registry_modes_match_settings_order():
    expected = [m for m in settings.TASK_MODES
                if m != settings.BATTLE_ONLY_MODE]
    assert list(registry.TASK_MODES) == expected
    assert len(set(registry.TASK_MODES)) == len(registry.TASK_MODES)


def test_registry_every_spec_has_callable_run_loop():
    for spec in registry.TASKS:
        assert spec.mode
        assert callable(spec.run_loop)
        assert registry.TASKS_BY_MODE[spec.mode] is spec
        if spec.pre_run is not None:
            assert callable(spec.pre_run)
    assert set(registry.TASKS_BY_MODE) == set(registry.TASK_MODES)
    assert len(registry.TASKS_BY_MODE) == len(registry.TASKS)


def test_registry_real_mapping_is_pinned():
    """模式 → 真实主循环/前置钩子逐项锁定（配错任务也必须测试失败）。"""
    expected = {
        "五环": wuhuan.run_loop,
        "客栈": kezhan.run_loop,
        "修罗": xiuluo.run_loop,
        "五倍": wubei.run_loop,
        "种族比武": zhongzu.run_loop,
        "玄枢轶闻": xuanshu.run_loop,
        "名望": mingwang.run_loop,
        "300环": sanbaihuan.run_loop,
    }
    for mode, run_loop in expected.items():
        assert registry.TASKS_BY_MODE[mode].run_loop is run_loop
    assert registry.TASKS_BY_MODE["客栈"].pre_run is registry._pre_kezhan
    assert registry.TASKS_BY_MODE["玄枢轶闻"].pre_run is registry._pre_xuanshu
    for mode in ("五环", "修罗", "五倍", "种族比武", "名望", "300环"):
        assert registry.TASKS_BY_MODE[mode].pre_run is None


def test_frontend_and_stats_mode_tables_match_settings():
    """新增模式的同步点：前端 const TASK_MODES 与 debug_overlay 统计表。"""
    assert debug_overlay._STATS_MODES == tuple(settings.TASK_MODES)
    html = (Path(__file__).resolve().parent.parent
            / "res" / "ui" / "control_window.html")
    text = html.read_text(encoding="utf-8")
    match = re.search(r"const TASK_MODES = \[([^\]]*)\]", text)
    assert match, "页面缺少 const TASK_MODES 定义"
    modes = re.findall(r"'([^']+)'", match.group(1))
    assert modes == list(settings.TASK_MODES)


def test_run_task_mode_pre_hook_then_loop(monkeypatch):
    calls = []
    spec = registry.TaskSpec(
        "测试",
        lambda win: calls.append(("loop", win)) or "done",
        lambda win: calls.append(("pre", win)))
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"测试": spec})
    win = object()
    assert registry.run_task_mode(win, "测试") == "done"
    assert calls == [("pre", win), ("loop", win)]


def test_run_task_mode_without_pre_hook(monkeypatch):
    calls = []
    spec = registry.TaskSpec(
        "测试", lambda win: calls.append(win) or "done")
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"测试": spec})
    win = object()
    assert registry.run_task_mode(win, "测试") == "done"
    assert calls == [win]


def test_run_task_mode_unknown_logs_and_skips(monkeypatch):
    logs = []
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {})
    monkeypatch.setattr(registry, "log",
                        lambda msg, level: logs.append((msg, level)))
    assert registry.run_task_mode(object(), "仅战斗") \
        == "仅战斗 暂无自动化实现，跳过"
    assert logs == [("[任务] 仅战斗 暂无自动化实现，跳过", "WARN")]


def test_run_task_mode_skips_all_when_daily_limit_reached(monkeypatch):
    """当天次数已达上限 → 前置退队与主循环都不执行，直接返回跳过说明。

    用户 2026-11-24：客栈已满 20 次时仍先做前置退队（21:47:49→:52 白耗
    约 3s）；上限判断是本地持久化读数，应放在前置动作之前。
    """
    calls = []
    spec = registry.TaskSpec(
        "客栈",
        lambda win: calls.append("loop") or "done",
        lambda win: calls.append("pre"))
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"客栈": spec})
    monkeypatch.setattr(registry, "load_task_counts", lambda: {"客栈": 20})
    monkeypatch.setattr(registry.app_utils, "task_progress_done",
                        lambda win, mode: 20)
    logs = []
    monkeypatch.setattr(registry, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    info = registry.run_task_mode(object(), "客栈")
    assert calls == []                       # 退队/主循环都不执行
    assert "20/20" in info and "无需再执行" in info
    assert logs == [("[任务] 客栈 当天已完成 20/20 次，无需再执行", "SYS")]


def test_run_task_mode_runs_below_daily_limit(monkeypatch):
    """未达上限 → 正常顺序：前置钩子 → 主循环。"""
    calls = []
    spec = registry.TaskSpec(
        "客栈",
        lambda win: calls.append("loop") or "done",
        lambda win: calls.append("pre"))
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"客栈": spec})
    monkeypatch.setattr(registry, "load_task_counts", lambda: {"客栈": 20})
    monkeypatch.setattr(registry.app_utils, "task_progress_done",
                        lambda win, mode: 19)
    assert registry.run_task_mode(object(), "客栈") == "done"
    assert calls == ["pre", "loop"]


def test_run_task_mode_unlimited_skips_progress_read(monkeypatch):
    """次数 0 = 不限：不读进度（零额外依赖），直接执行。"""
    calls = []
    spec = registry.TaskSpec("测试", lambda win: calls.append("loop") or "done")
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"测试": spec})
    monkeypatch.setattr(registry, "load_task_counts", lambda: {})
    monkeypatch.setattr(
        registry.app_utils, "task_progress_done",
        lambda win, mode: (_ for _ in ()).throw(
            AssertionError("不限次数时不应读取进度")))
    assert registry.run_task_mode(object(), "测试") == "done"
    assert calls == ["loop"]


def test_run_task_mode_progress_read_failure_runs_task(monkeypatch):
    """进度/设置读取异常 → 按未达上限处理，不阻断任务执行。"""
    calls = []
    spec = registry.TaskSpec("测试", lambda win: calls.append("loop") or "done")
    monkeypatch.setattr(registry, "TASKS_BY_MODE", {"测试": spec})
    monkeypatch.setattr(registry, "load_task_counts",
                        lambda: (_ for _ in ()).throw(RuntimeError("坏")))
    assert registry.run_task_mode(object(), "测试") == "done"
    assert calls == ["loop"]


def test_xuanshu_pre_hook_skips_leave_when_task_tracked(monkeypatch):
    titles = []

    def _has_task(win, title):
        titles.append(title)
        return True

    monkeypatch.setattr(registry.task_tracker, "has_task", _has_task)
    monkeypatch.setattr(
        registry.team, "leave_or_disband_team",
        lambda win: (_ for _ in ()).throw(AssertionError("不应退队")))
    registry._pre_xuanshu(object())
    assert titles == [xuanshu.TASK_TITLE]


def test_xuanshu_pre_hook_leaves_when_no_task(monkeypatch):
    calls = []
    titles = []
    monkeypatch.setattr(registry.task_tracker, "has_task",
                        lambda win, title:
                        titles.append(title) or False)
    monkeypatch.setattr(registry.team, "leave_or_disband_team",
                        lambda win: calls.append(win) or (True, "已退队"))
    monkeypatch.setattr(registry, "log", lambda *a: None)
    win = object()
    registry._pre_xuanshu(win)
    assert calls == [win]
    assert titles == [xuanshu.TASK_TITLE]


def test_kezhan_pre_hook_always_leaves(monkeypatch):
    calls = []
    logs = []
    monkeypatch.setattr(registry.team, "leave_or_disband_team",
                        lambda win: calls.append(win) or (False, "未组队"))
    monkeypatch.setattr(registry, "log",
                        lambda msg, level: logs.append(level))
    registry._pre_kezhan(object())
    assert len(calls) == 1
    assert logs == ["WARN"]  # 未组队也记录（与旧版一致）


def test_main_uses_registry_dispatch():
    """main 只保留注册表入口，旧的 if/elif 分发已删除。"""
    import main

    assert main.run_task_mode is registry.run_task_mode
    assert not hasattr(main, "_run_task_mode")


def test_run_selected_tasks_dispatches_in_order(monkeypatch):
    """main 真实调用点：按面板顺序传 (win, mode)，并更新悬浮层/进度。"""
    import main

    calls = []
    sleeps = []

    class _Clock:
        @staticmethod
        def sleep(seconds):
            sleeps.append(seconds)

    monkeypatch.setattr(main, "run_task_mode",
                        lambda win, mode: calls.append(("run", win, mode))
                        or f"{mode} 完成")
    monkeypatch.setattr(main.app_utils, "set_current_task_mode",
                        lambda mode: calls.append(("current", mode)))
    monkeypatch.setattr(main.debug_overlay, "set_task",
                        lambda mode: calls.append(("task", mode)))
    monkeypatch.setattr(main.debug_overlay, "set_progress",
                        lambda text: calls.append(("progress", text)))
    monkeypatch.setattr(main, "log",
                        lambda msg, level="INFO":
                        calls.append(("log", msg)))
    monkeypatch.setattr(main, "_overall_progress_text",
                        lambda win, modes: "总进度 0/1")
    monkeypatch.setattr(main, "time", _Clock)
    win = object()
    main._run_selected_tasks(win, ["五倍", "修罗"])
    assert [c for c in calls if c[0] == "run"] == [
        ("run", win, "五倍"), ("run", win, "修罗")]
    assert [c for c in calls if c[0] == "current"] == [
        ("current", "五倍"), ("current", "修罗")]
    assert sleeps == [2, 2]
    run_idxs = [i for i, c in enumerate(calls) if c[0] == "run"]
    assert "开始执行" in calls[run_idxs[0] - 1][1]
    assert "循环结束" in calls[run_idxs[0] + 1][1]
    assert calls[run_idxs[0] + 2][0] == "progress"


# ---- 五环领取冷却在队列层顺延（2026-11-23 用户需求） ----

def _patch_queue_env(monkeypatch, calls, state):
    """调度测试公共打桩：run_task_mode 记录调用、冷却剩余由 state 控制。"""
    import main

    class _Clock:
        @staticmethod
        def sleep(seconds):
            calls.append(("sleep", seconds))

    monkeypatch.setattr(main, "time", _Clock)
    monkeypatch.setattr(main.app_utils, "raise_if_aborted", lambda: None)
    monkeypatch.setattr(main.app_utils, "set_current_task_mode",
                        lambda mode: None)
    monkeypatch.setattr(main.debug_overlay, "set_task", lambda mode: None)
    monkeypatch.setattr(main.debug_overlay, "set_progress", lambda text: None)
    monkeypatch.setattr(main, "_overall_progress_text",
                        lambda win, modes: "总进度")
    monkeypatch.setattr(main, "log",
                        lambda msg, level="INFO": calls.append(("log", msg)))
    monkeypatch.setattr(main.wuhuan_task, "cooldown_remaining",
                        lambda win: state["rem"])


def _runs(monkeypatch, state, modes, run_impl):
    import main
    calls = []
    _patch_queue_env(monkeypatch, calls, state)

    def _run(win, mode):
        calls.append(("run", mode))
        return run_impl(mode, calls)

    monkeypatch.setattr(main, "run_task_mode", _run)
    main._run_selected_tasks(object(), modes)
    return [c[1] for c in calls if c[0] == "run"]


def test_wuhuan_deferred_to_end_when_cooling(monkeypatch):
    """冷却未结束且还有其他任务：五环排到队尾先做其他任务。"""
    state = {"rem": 300.0}
    runs = _runs(monkeypatch, state, ["五环", "客栈", "修罗"],
                 lambda mode, calls: f"{mode} 完成")
    assert runs == ["客栈", "修罗", "五环"]


def test_wuhuan_returns_to_front_after_cooldown(monkeypatch):
    """其他任务完成后冷却已结束：五环插回队首优先执行。"""
    state = {"rem": 300.0}

    def _impl(mode, calls):
        if mode == "客栈":
            state["rem"] = 0.0  # 客栈跑完时冷却恰好结束
        return f"{mode} 完成"

    runs = _runs(monkeypatch, state, ["五环", "客栈", "修罗"], _impl)
    assert runs == ["客栈", "五环", "修罗"]


def test_wuhuan_cooldown_info_requeued_after_other_tasks(monkeypatch):
    """五环领取时才发现冷却（返回 COOLDOWN_INFO）：重新排队，先做其他
    任务，五环最后执行（run_loop 会在云游大师处等待）。"""
    from src.tasks import wuhuan as wuhuan_task
    state = {"rem": 0.0}

    def _impl(mode, calls):
        if mode == "五环" and sum(1 for m, _ in calls if m == "run") == 1:
            state["rem"] = 300.0
            return f"{wuhuan_task.COOLDOWN_INFO}（距可领取还差 5分0秒）"
        return f"{mode} 完成"

    runs = _runs(monkeypatch, state, ["五环", "客栈"], _impl)
    assert runs == ["五环", "客栈", "五环"]


def test_wuhuan_skip_info_not_requeued(monkeypatch):
    """冷却超过等待上限的跳过哨兵不触发重新排队（本次运行结束五环）。"""
    from src.tasks import wuhuan as wuhuan_task
    state = {"rem": wuhuan_task.COOLDOWN_MAX_WAIT + 60}
    runs = _runs(monkeypatch, state, ["五环"],
                 lambda mode, calls: wuhuan_task.COOLDOWN_SKIP_INFO)
    assert runs == ["五环"]


def test_wuhuan_alone_runs_directly_with_cooldown(monkeypatch):
    """五环是唯一任务：直接执行（run_loop 内部等待冷却）。"""
    state = {"rem": 300.0}
    runs = _runs(monkeypatch, state, ["五环"],
                 lambda mode, calls: f"{mode} 完成")
    assert runs == ["五环"]
