# -*- coding: utf-8 -*-
"""配置方案面板 API（自 control_window.py 抽出的 mixin）。

方案列表/切换/保存/另存/删除/导入导出，全部委托 core.schemes；文件
对话框在后台线程执行、结果经 JS 回调回推页面。ControlWindow 继承本
mixin，callPython 名称与行为不变（纯搬迁）。
"""
import json
import threading

from ..core import schemes, user_data
from . import window_registry
from .host_contract import ControlHost


class SchemeApiMixin(ControlHost):
    def get_schemes(self):
        """方案列表与当前活动方案（页面下拉/管理区渲染）。"""
        return schemes.list_schemes()

    def _scheme_guard(self):
        """方案操作前置检查：仅拒绝选中窗口自身任务运行中的操作。

        多窗口（2026-11-22）：方案按角色隔离，A 在跑不影响编辑 B 的方案；
        A 运行中时 A 的方案只读。
        """
        role = window_registry.selected_role() or user_data.current_role_id()
        if self._tm.is_running(role):
            return {"status": "error", "msg": "该窗口任务运行中，请先停止该窗口任务再操作"}
        return None

    def switch_scheme(self, name):
        """一键切换方案：整体替换当前设置（切换前自动保存当前改动）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.switch_scheme(name)

    def scheme_resave(self, name):
        """保存：当前设置快照写回活动方案（输入名即方案名，改了名即重命名）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.resave_scheme(name)

    def scheme_copy(self, name):
        """另存新方案：当前设置另存为新方案（重名自动加序号）。"""
        guard = self._scheme_guard()
        return guard if guard else schemes.save_scheme_copy(name)

    def scheme_delete(self):
        """删除当前活动方案（页面二次点击确认；删除后回落「默认」）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        name = schemes.active_name()
        if not name:
            return {"status": "error", "msg": "当前无活动方案"}
        return schemes.delete_scheme(name)

    def scheme_export(self):
        """导出当前活动方案：后台线程弹另存为对话框，结果经
        onSchemeExportResult 回推页面（同验证流程模式，不阻塞 webview）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        if self._scheme_dialog_busy:
            return {"status": "error", "msg": "文件对话框已打开，请先完成当前操作"}
        name = schemes.active_name()
        if not name:
            return {"status": "error", "msg": "当前无活动方案"}
        self._scheme_dialog_busy = True
        threading.Thread(target=self._run_scheme_export, args=(name,),
                         daemon=True, name="scheme-export").start()
        return {"status": "success", "msg": "请在弹出的窗口选择导出位置"}

    def _run_scheme_export(self, name):
        """导出线程体：文件对话框 → 写文件 → 结果回推页面。"""
        try:
            path = schemes.pick_save_path(f"{name}.json")
            if not path:
                ok, info = False, "已取消导出"
            else:
                schemes.export_scheme_to(path, name)
                ok, info = True, f"已导出方案「{name}」"
        except Exception as e:
            ok, info = False, f"导出失败: {e}"
        self._push_js("if(window.onSchemeExportResult){window.onSchemeExportResult("
                      f"{'true' if ok else 'false'}, {json.dumps(info)});}}")
        self._scheme_dialog_busy = False

    def scheme_import(self):
        """导入方案：后台线程弹打开对话框；同名冲突经 onSchemeImportPending
        回推页面确认（覆盖/另存为/取消），无冲突直接入列（不激活）。"""
        guard = self._scheme_guard()
        if guard:
            return guard
        if self._scheme_dialog_busy:
            return {"status": "error", "msg": "文件对话框已打开，请先完成当前操作"}
        self._scheme_dialog_busy = True
        threading.Thread(target=self._run_scheme_import,
                         daemon=True, name="scheme-import").start()
        return {"status": "success", "msg": "请选择要导入的方案文件"}

    def _run_scheme_import(self):
        """导入线程体：对话框 → 解析校验 → 无冲突直接入列 / 冲突挂起等确认。"""
        try:
            path = schemes.pick_open_path()
            if not path:
                self._finish_scheme_import(False, "已取消导入")
                return
            name, settings = schemes.read_import_file(path)
            if schemes.name_exists(name):
                self._pending_import = {"name": name, "settings": settings}
                self._push_js("if(window.onSchemeImportPending)"
                              f"{{window.onSchemeImportPending({json.dumps(name)});}}")
                self._scheme_dialog_busy = False
                return
            result = schemes.add_scheme(name, settings)
            if result.get("status") != "success":
                self._finish_scheme_import(False, result.get("msg", "导入失败"))
                return
            self._finish_scheme_import(True, f"已导入方案「{name}」（未激活，可在顶部下拉切换）")
        except Exception as e:
            self._finish_scheme_import(False, f"导入失败: {e}")

    def _finish_scheme_import(self, ok, info):
        """导入结束回推页面（附最新方案列表供下拉重渲染）。"""
        self._pending_import = None
        self._push_js("if(window.onSchemeImportResult){window.onSchemeImportResult("
                      f"{'true' if ok else 'false'}, {json.dumps(info)}, "
                      f"{json.dumps(schemes.list_schemes())});}}")
        self._scheme_dialog_busy = False

    def scheme_import_confirm(self, action):
        """导入同名冲突确认（页面确认行按钮）：overwrite/rename/cancel。"""
        pending = self._pending_import
        self._pending_import = None
        if not pending:
            self._scheme_dialog_busy = False
            return {"status": "error", "msg": "无待确认的导入"}
        name, settings = pending["name"], pending["settings"]
        try:
            if action == "cancel":
                self._scheme_dialog_busy = False
                return {"status": "success", "msg": "已取消导入",
                        "schemes": schemes.list_schemes()}
            if action == "overwrite":
                result = schemes.replace_scheme(name, settings)
                msg = f"已覆盖方案「{name}」"
            elif action == "rename":
                new_name = schemes.unique_name(name)
                result = schemes.add_scheme(new_name, settings)
                msg = f"已导入为「{new_name}」（未激活，可在顶部下拉切换）"
            else:
                return {"status": "error", "msg": "未知操作",
                        "schemes": schemes.list_schemes()}
        finally:
            self._scheme_dialog_busy = False
        if result.get("status") != "success":
            return {**result, "schemes": schemes.list_schemes()}
        return {"status": "success", "msg": msg,
                "schemes": schemes.list_schemes()}
