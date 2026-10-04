"""pywebview 6 兼容补丁（polyfill）。

修复 FloatScriptWindow 悬浮窗在 pywebview 6 下页面初始化失败、日志不显示的问题：

- 框架 WebWindow 注入的桥接代码开头有 `if (window.callPython) return;`，而
  flot_control.html 页面自己声明了 callPython 函数，导致注入提前返回、
  onPythonReady 事件从未派发，页面的 get_init_data 初始化不执行，日志无法推送。
- pywebview 6 的 window.pywebview.api 只暴露 js_api 对象的公开方法
  （框架的 ApiBridge 仅有 call_internal），页面直调 get_init_data 等会失败。

修复方式：页面加载完成后重定义 callPython 走 call_internal 通道，
并手动派发 onPythonReady，使页面初始化正常执行。
该补丁在旧版 pywebview 下同样有效且无害（call_internal 始终存在）。
"""
import threading
import time

_PATCH_JS = (
    "(function () {"
    " window.callPython = async function (fnName, ...args) {"
    "  return await window.pywebview.api.call_internal(fnName, args);"
    " };"
    " window.dispatchEvent(new CustomEvent('onPythonReady'));"
    "})();"
)


def patch_js_bridge(app):
    """挂载桥接补丁。必须在 app.show() 之前调用，页面加载时自动生效。"""

    def _patch():
        # 等待 app.show() 内部创建出 webview 窗口实例
        while app.webview_instance is None:
            time.sleep(0.05)

        def _on_loaded(*_args):
            try:
                app.webview_instance.evaluate_js(_PATCH_JS)
            except Exception:
                pass

        app.webview_instance.events.loaded += _on_loaded
        if app.webview_instance.events.loaded.is_set():
            _on_loaded()

    threading.Thread(target=_patch, daemon=True).start()
