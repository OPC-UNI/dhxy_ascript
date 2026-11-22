# -*- coding: utf-8 -*-
"""Mixin 宿主契约（仅类型检查用，运行期是 object）。

ControlWindow 组合多个 API mixin（diagnostics/settings_api/message_api/
scheme_api）；mixin 方法通过 self 访问宿主提供的锁、任务管理器、
webview 与推送方法。Pylance 看不到「跨 mixin 的实例属性」，会报
reportAttributeAccessIssue；这里集中声明宿主契约，mixin 继承本类后
静态检查即可解析。

运行期 ``ControlHost = object``：不产生任何属性/方法，不改变 MRO 或行为。
新增 mixin 宿主依赖时在此补一行，不要在 mixin 里散落 type: ignore。
"""
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    class ControlHost:
        # ControlWindow.__init__ 赋值的实例属性
        _tm: Any                    # TaskManager
        _message_service: Any       # MessageService
        _capture_lock: Any
        _game_op_lock: Any
        _verifying: bool
        _map_nav_running: bool     # 诊断-地图导航线程进行中（中断/启动门控）
        _scheme_dialog_busy: bool
        _pending_import: Any
        _start_lock: Any           # 启动动作锁（顶栏按钮/老板键）
        _license_gate: Any         # 启动前在线授权校验闭包
        _license_info: Any
        _fleet_lock: Any           # 多窗口启动动作锁（按需创建）
        _fleet_starting: bool      # 全部启动后台线程进行中
        webview_instance: Any

        # 宿主提供的方法
        def _push_js(self, js: str) -> None: ...

        def add_logs(self, lines: list) -> None: ...

        def _set_license_info(self, info: Any) -> None: ...

        def refresh_window_title(self) -> None: ...
else:
    ControlHost = object
