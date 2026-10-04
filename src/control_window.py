"""自定义网页控制窗口：页面提供启动/重置/关闭三个功能控制自动化任务。

ControlWindow 基于 WebWindow（pywebview），页面为 res/ui/control_window.html。
必须在 app.show()（webview.start）之前调用 prepare() 创建窗口，
与浮动控制面板共用同一次 GUI 事件循环。

功能约定：
- 启动：对当前选中的任务模式开始执行任务
- 重置：停止当前任务，并把模式恢复默认（五环）
- 关闭：仅隐藏控制窗口，程序继续后台运行（托盘菜单可再唤起）

注意：页面不要自行声明 callPython 函数，使用框架注入的桥接
（window.callPython → call_internal 通道），否则注入会被跳过。
"""
import webview
from pathlib import Path
from ascript.windows.system import R
from ascript.windows.system.key_value import KeyValue
from ascript.windows.ui import WebWindow

# ---- 任务模式（UI 单选项，KeyValue 持久化，记住上次选择） ----
TASK_MODES = ["五环", "客栈", "修罗", "五倍", "玄枢轶闻", "名望"]
_DEFAULT_MODE = TASK_MODES[0]  # 五环
_MODE_KEY = "task_mode"
# 是否使用摄妖香（UI 开关，KeyValue 持久化）
_USE_SHEYAOXIANG_KEY = "use_sheyaoxiang"


def load_task_mode():
    """读取上次选择的任务模式（首次默认五环）。"""
    mode = KeyValue.get(_MODE_KEY, _DEFAULT_MODE)
    return mode if mode in TASK_MODES else _DEFAULT_MODE


def save_task_mode(mode):
    """保存任务模式选择，返回操作结果。"""
    if mode not in TASK_MODES:
        return {"status": "error", "msg": "INVALID_MODE"}
    KeyValue.save(_MODE_KEY, mode)
    return {"status": "success"}


def load_use_sheyaoxiang():
    """读取是否使用摄妖香开关（首次默认开启）。"""
    return bool(KeyValue.get(_USE_SHEYAOXIANG_KEY, True))


def save_use_sheyaoxiang(on):
    """保存是否使用摄妖香开关，返回操作结果。"""
    KeyValue.save(_USE_SHEYAOXIANG_KEY, bool(on))
    return {"status": "success"}


class _ApiBridge:
    """JS 与 Python 之间的 API 桥接，与 WebWindow 内部实现保持一致。"""

    def __init__(self, funcs):
        self._funcs = funcs

    def call_internal(self, func_name, args):
        f = self._funcs.get(func_name)
        if not f:
            return None
        try:
            return f(*args)
        except Exception as e:
            print(f"Error calling {func_name}: {e}")
            return None


class ControlWindow(WebWindow):
    """独立网页控制窗口。

    开始/停止按钮通过桥接调用浮动面板（FloatScriptWindow）的启停逻辑，
    任务进程的创建、日志转发等仍由浮动面板统一管理。
    """

    def __init__(self, app, title="AScript 控制面板"):
        super().__init__(R.ui("control_window.html"))
        self._app = app
        self._title = title
        self._visible = True  # 启动时直接可见
        self.expose(self.get_init_data)
        self.expose(self.save_task_mode)
        self.expose(self.save_use_sheyaoxiang)
        self.expose(self.start_task)
        self.expose(self.reset_task)
        self.expose(self.close_window)

    def get_init_data(self):
        """页面初始化数据：当前选中的任务模式与摄妖香开关状态。"""
        return {"mode": load_task_mode(), "use_sheyaoxiang": load_use_sheyaoxiang()}

    def save_task_mode(self, mode):
        """单选项变更：保存任务模式。"""
        return save_task_mode(mode)

    def save_use_sheyaoxiang(self, on):
        """摄妖香开关变更：保存开关状态。"""
        return save_use_sheyaoxiang(on)

    def start_task(self):
        """启动按钮：对当前选中的模式开始执行任务。"""
        return self._app.on_toggle_click(True)

    def reset_task(self):
        """重置按钮：停止当前任务，并把模式恢复默认（五环）。"""
        result = self._app.on_toggle_click(False)
        if result.get("status") != "success":
            return result
        save_task_mode(_DEFAULT_MODE)
        return {"status": "success", "mode": load_task_mode()}

    def close_window(self):
        """关闭按钮：仅隐藏控制窗口，程序继续后台运行。"""
        if self.webview_instance and self._visible:
            try:
                self.webview_instance.hide()
                self._visible = False
            except Exception:
                pass
        return {"status": "success"}

    def toggle_visible(self):
        """切换控制窗口显示/隐藏（供托盘菜单调用）。"""
        if not self.webview_instance:
            return
        try:
            if self._visible:
                self.webview_instance.hide()
                self._visible = False
            else:
                self.webview_instance.show()
                self._visible = True
        except Exception:
            pass

    def sync_status(self, running):
        """任务状态同步到页面（仅更新状态与按钮，不改窗口显隐）。"""
        if self.webview_instance:
            try:
                state = "true" if running else "false"
                self.webview_instance.evaluate_js(
                    f"if(window.syncStatus){{window.syncStatus({state});}}")
            except Exception:
                pass

    def prepare(self, width=360, height=360):
        """创建窗口但不启动事件循环（webview.start 由 app.show() 统一执行）。"""
        file_url = Path(self.abs_path).as_uri()
        win = webview.create_window(
            title=self._title,
            url=file_url,
            js_api=_ApiBridge(self._exposed_functions),
            width=width,
            height=height,
        )
        if win is None:
            return
        self.webview_instance = win

        def on_loaded(*_args):
            self._inject("control window loaded")

        win.events.loaded += on_loaded
