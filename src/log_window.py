"""独立日志窗口：吸附在主窗口右侧，仅在调试信息开关开启时显示。

- 日志仅由 Python 侧 evaluate_js 推送（页面不声明 callPython，无需桥接）
- 窗口隐藏期间日志进内存缓冲（deque 上限 500 行），显示后补推
- 用户点 X 关闭日志窗口 = 隐藏并回调关闭调试信息开关（主窗口同步页面勾选）
- 真正退出走 close_window()（_real_close 放行，避免退出死锁）
"""
import json
import threading
from collections import deque
from pathlib import Path

import webview
from ascript.windows.system import R
from ascript.windows.ui import WebWindow


class LogWindow(WebWindow):
    """吸附在主窗口右侧的日志窗口（WebWindow 子类，页面 res/ui/log_window.html）。"""

    def __init__(self):
        super().__init__(R.ui("log_window.html"))
        self._buffer = deque(maxlen=500)   # 页面加载前/隐藏期间的日志缓冲
        self._loaded = False
        self._theme = "light"
        self._real_close = threading.Event()  # 置位后 X/销毁真正放行（退出路径）
        self._debug_off_callback = None

    def set_debug_off_callback(self, cb):
        """接线：日志窗口被 X 关闭时通知主窗口关闭调试信息开关。"""
        self._debug_off_callback = cb

    def add_logs(self, lines):
        """批量日志：缓冲 + 页面已加载则实时推送。"""
        self._buffer.extend(lines)
        if self._loaded and self.webview_instance:
            self._push(list(lines))

    def _push(self, lines):
        payload = json.dumps(list(lines), ensure_ascii=False)  # 合法 JS 数组字面量
        try:
            self.webview_instance.evaluate_js(
                f"if(window.addLogs){{window.addLogs({payload});}}")
        except Exception:
            pass  # 窗口销毁期间调用失败忽略

    def _flush(self):
        """页面加载完成后补推缓冲日志。"""
        if self._buffer:
            buffered = list(self._buffer)
            self._buffer.clear()
            self._push(buffered)

    def apply_theme(self, theme):
        """主题联动（主面板切换浅色/深色时同步）。"""
        self._theme = theme if theme in ("dark", "light") else "light"
        if self.webview_instance:
            try:
                state = "true" if self._theme == "light" else "false"
                self.webview_instance.evaluate_js(
                    f"document.body.classList.toggle('light', {state});")
            except Exception:
                pass

    def set_visible(self, on):
        """显示/隐藏（吸附定位由主窗口 _dock_log 负责）。"""
        if not self.webview_instance:
            return
        try:
            if on:
                self.webview_instance.show()
            else:
                self.webview_instance.hide()
        except Exception:
            pass

    def _on_loaded(self, *_args):
        """页面加载完成：标记就绪、补推缓冲日志、应用当前主题。"""
        self._loaded = True
        self._flush()
        self.apply_theme(self._theme)

    def _on_closing(self, *_args):
        """用户点 X：取消关闭并隐藏，同时回调关闭调试信息开关。

        返回 False → pywebview closing 事件置 Cancel（与主窗口同机制）；
        _real_close 置位时放行真正退出。
        """
        if self._real_close.is_set():
            return True
        self.set_visible(False)
        if self._debug_off_callback is not None:
            try:
                self._debug_off_callback()
            except Exception:
                pass
        return False

    def close_window(self):
        """真正退出：放行关闭并销毁窗口。"""
        self._real_close.set()
        if self.webview_instance:
            try:
                self.webview_instance.destroy()
            except Exception:
                pass

    def prepare(self, width=260, height=500):
        """创建窗口（初始隐藏；webview.start 由主窗口 show 统一执行）。"""
        file_url = Path(self.abs_path).as_uri()
        win = webview.create_window(
            title="啄木鸟 - 日志",
            url=file_url,
            width=width,
            height=height,
            hidden=True,
        )
        if win is None:
            return
        self.webview_instance = win
        win.events.loaded += self._on_loaded
        win.events.closing += self._on_closing
