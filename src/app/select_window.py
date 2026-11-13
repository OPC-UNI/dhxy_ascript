"""多开游戏窗口绑定选择弹窗。

主进程启动时检测到多个游戏主窗口且无有效绑定时弹出（授权门通过后、
控制窗口创建前——webview 消息循环须独占一次，与控制窗口分属两次
start，同授权窗口路径）。用户选择后保存绑定并关闭，show() 阻塞返回
所选窗口标题；取消/直接关窗返回 None（回退自动选择）。
"""
from ascript.windows.system import R
from ascript.windows.ui import WebWindow


class SelectWindow(WebWindow):
    """列出主窗口供用户选择绑定；选择后保存并关闭。"""

    def __init__(self, wins):
        super().__init__(R.ui("select_window.html"))
        self._wins = wins
        self.expose(self.get_windows)
        self.expose(self.choose)
        self.expose(self.cancel)

    def get_windows(self):
        """返回主窗口列表（标题 + 尺寸），供页面渲染。"""
        return [{"title": w.title,
                 "size": f"{w.rect.width}×{w.rect.height}"} for w in self._wins]

    def choose(self, index):
        """用户选择：记录所选下标并关闭窗口（show 阻塞返回该下标，
        由调用方取窗口对象保存绑定——保存需要句柄+标题）。"""
        try:
            self._wins[int(index)]
        except (IndexError, ValueError, TypeError):
            return {"status": "error", "msg": "INVALID_INDEX"}
        self._return_value = int(index)
        self.close()
        return {"status": "success"}

    def cancel(self):
        """取消选择：关闭窗口（保持自动选择）。"""
        self._return_value = None
        self.close()
        return {"status": "success"}
