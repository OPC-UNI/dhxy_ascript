"""消息事件：日志行分类、去重窗口、ERROR 升级/预算。"""
from src.app.messages.events import (EventDeduper, classify_log_line,
                                     format_event)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_classify_lifecycle_lines():
    assert classify_log_line("[系统] 任务已启动")["key"] == "task_start"
    assert classify_log_line("[系统] 任务已停止")["key"] == "task_stop"
    assert classify_log_line("[系统] 任务已结束（自然退出）")["key"] == "task_end"
    crash = classify_log_line("[系统] 任务进程异常退出（exitcode=3）")
    assert crash["key"] == "task_crash"
    assert "3" in crash["detail"]
    window = classify_log_line("[系统] 检测到游戏窗口已关闭，任务自动终止")
    assert window["key"] == "task_stop"
    license_line = classify_log_line("[授权] 校验未通过：已过期，请续费")
    assert license_line["key"] == "license_block"
    assert "已过期" in license_line["detail"]


def test_classify_error_and_message_prefix():
    event = classify_log_line("10:20:30 [ERROR] 网络超时 3 次")
    assert event["key"] == "error"
    assert "网络超时" in event["detail"]
    assert classify_log_line("[消息] [ERROR] 自身日志") is None
    assert classify_log_line("普通一行") is None


def test_lifecycle_dedupe_window():
    clock = Clock()
    deduper = EventDeduper(clock=clock)
    first = classify_log_line("[系统] 检测到游戏窗口已关闭，任务自动终止")
    second = classify_log_line("[系统] 任务已停止")
    assert deduper.accept(first)
    assert deduper.accept(second) is None
    clock.advance(16)
    assert deduper.accept(second)


def test_crash_dedupe_by_exitcode():
    clock = Clock()
    deduper = EventDeduper(clock=clock)
    assert deduper.accept(classify_log_line("任务进程异常退出（exitcode=1）"))
    assert deduper.accept(classify_log_line("任务进程异常退出（exitcode=3）"))
    assert deduper.accept(classify_log_line("任务进程异常退出（exitcode=1）")) is None


def test_error_normalized_and_escalated():
    clock = Clock()
    deduper = EventDeduper(clock=clock)
    assert deduper.accept(classify_log_line("[ERROR] 错误 123"))
    assert deduper.accept(classify_log_line("[ERROR] 错误 456")) is None
    upgraded = None
    for _ in range(9):
        item = deduper.accept(classify_log_line("[ERROR] 错误 789"))
        if item is not None:
            upgraded = item
    assert upgraded is not None
    assert "重复 10 次" in upgraded["title"]


def test_error_window_and_budget():
    clock = Clock()
    deduper = EventDeduper(clock=clock)
    accepted = [deduper.accept(
        classify_log_line(f"[ERROR] 不同错误 {chr(ord('A') + i)}"))
        for i in range(7)]
    assert sum(1 for item in accepted if item) == 6
    assert deduper.suppressed == 1
    clock.advance(61)
    assert deduper.accept(classify_log_line("[ERROR] 新错误"))


def test_format_event():
    text = format_event({"title": "任务已启动", "detail": "x"}, identity="角色A")
    assert "【啄木鸟事件】任务已启动" in text
    assert "角色：角色A" in text
    assert "详情：x" in text
