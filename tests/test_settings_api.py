"""app.settings_api：表驱动的设置保存方法。

锁四件事：
1. 映射目标真实存在于 core.settings；
2. 所有方法在 mixin/ControlWindow 上可解析且归属正确（callPython 名不变，
   且不得在 ControlWindow 类体重复定义形成两份实现）；
3. 纯转发：函数体是单条 `return settings_module.<同名>(...)`，53 个转发
   逐一验证参数原样到达 core.settings、返回值原样透传；
4. 「运行中拒绝修改」守卫经 ControlWindow 的解析路径生效。
"""
import ast
import inspect
import textwrap

import pytest

from src.app import settings_api
from src.app.control_window import ControlWindow
from src.core import settings

_SPECIAL = {"save_task_modes", "save_task_count"}
DELEGATE_NAMES = sorted(
    name for name in vars(settings_api.SettingsApiMixin)
    if name.startswith("save_") and name not in _SPECIAL)


def test_delegates_target_real_settings_functions():
    assert len(DELEGATE_NAMES) == 53
    for name in DELEGATE_NAMES:
        assert callable(getattr(settings, name)), name


def test_delegates_are_single_return_calls():
    """每个薄方法必须是单条 `return settings_module.<同名>(...)`。

    用 AST 而非子串匹配：docstring/注释里出现同名字符串不能蒙混过关。
    """
    for name in DELEGATE_NAMES:
        source = textwrap.dedent(inspect.getsource(
            vars(settings_api.SettingsApiMixin)[name]))
        body = ast.parse(source).body[0].body
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant):
            body = body[1:]  # 跳过 docstring
        assert len(body) == 1 and isinstance(body[0], ast.Return), name
        call = body[0].value
        assert isinstance(call, ast.Call), name
        func = call.func
        assert isinstance(func, ast.Attribute) and func.attr == name, name
        assert isinstance(func.value, ast.Name) and \
            func.value.id == "settings_module", name
        assert len(call.args) == len(inspect.signature(
            getattr(settings, name)).parameters), name
        assert not call.keywords, name


def test_all_delegates_defined_on_mixin_and_control_window():
    for name in DELEGATE_NAMES:
        assert callable(getattr(settings_api.SettingsApiMixin, name))
        assert getattr(ControlWindow, name).__module__ == "src.app.settings_api"
    # 守卫与消息编排方法均由 mixin 提供；ControlWindow 类体不得重复定义
    # （类体定义会遮蔽 mixin 版本，令守卫测试与实际运行路径脱节）。
    assert ControlWindow.save_task_modes.__module__ == "src.app.settings_api"
    assert ControlWindow.save_task_count.__module__ == "src.app.settings_api"
    assert ControlWindow.save_message_config.__module__ == "src.app.message_api"


@pytest.mark.parametrize("name", DELEGATE_NAMES)
def test_every_delegate_forwards_args_and_result(monkeypatch, name):
    """53 个转发逐一验证：参数原样到达 core.settings，返回值原样透传。"""
    sentinel = object()
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    params = list(inspect.signature(getattr(settings, name)).parameters)
    dummy = [object() for _ in params]
    monkeypatch.setattr(settings, name, fake)
    method = getattr(settings_api.SettingsApiMixin, name)
    assert method(settings_api.SettingsApiMixin(), *dummy) is sentinel
    assert calls == [(tuple(dummy), {})]


def test_guarded_saves_reject_while_running():
    host = settings_api.SettingsApiMixin()
    host._tm = type("Tm", (), {"is_running": lambda self: True})()
    # 经 ControlWindow 解析调用，确保测的是生产路径而不是被遮蔽的死副本
    for result in (ControlWindow.save_task_modes(host, ["五环"]),
                   ControlWindow.save_task_count(host, "五环", 3)):
        assert result == {"status": "error",
                          "msg": "任务运行中，请先停止任务再修改"}


def test_guarded_saves_forward_when_stopped(monkeypatch):
    host = settings_api.SettingsApiMixin()
    host._tm = type("Tm", (), {"is_running": lambda self: False})()
    monkeypatch.setattr(settings, "save_selected_modes",
                        lambda modes: {"modes": modes})
    monkeypatch.setattr(settings, "save_task_count",
                        lambda mode, value: {"mode": mode, "value": value})
    assert ControlWindow.save_task_modes(host, ["五环"]) == {"modes": ["五环"]}
    assert ControlWindow.save_task_count(host, "五环", 3) == \
        {"mode": "五环", "value": 3}
