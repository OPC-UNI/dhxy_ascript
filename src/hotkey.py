"""全局老板键：一键启动/停止任务（默认 F11，config.json 可配）。

RegisterHotKey 需要消息泵：主线程被 webview GUI 消息循环占用，热键
注册在专用守护线程里（hwnd=NULL 时 WM_HOTKEY 投递到注册线程的消息
队列，由 GetMessageW 泵接收）。热键为系统级全局注册，任意前台窗口
下都生效，按键不会再传给游戏（WM_HOTKEY 被消费）。

配置：config.json 键 boss_hotkey，取值 "f1"~"f12"（默认 "f11"，
用户选定），"none" 关闭老板键。F11 不在任务使用的按键集合内
（Alt+C/Alt+5/Alt+6/Alt+E）。
"""
import ctypes
import ctypes.wintypes  # 显式导入：import ctypes 不自动绑定 wintypes 子模块
import threading

from ascript.windows.system.key_value import KeyValue

_BOSS_HOTKEY_KEY = "boss_hotkey"
_DEFAULT = "f11"
# F1~F12 虚拟键码（0x70~0x7B）
_VK_BY_NAME = {f"f{i}": 0x70 + i - 1 for i in range(1, 13)}
_MOD_NOREPEAT = 0x4000  # 按住不重复触发（防长按连点启停）
WM_HOTKEY = 0x0312
_HOTKEY_ID = 1


def load_hotkey():
    """读取老板键配置：默认 f11；"none" 关闭；非法值回默认。"""
    raw = str(KeyValue.get(_BOSS_HOTKEY_KEY, _DEFAULT) or "").strip().lower()
    if raw == "none":
        return None
    return raw if raw in _VK_BY_NAME else _DEFAULT


def register(callback):
    """注册全局热键（专用消息泵线程）。

    返回 (是否已注册, 热键名)；多实例运行时后启动者注册失败（同键被
    前实例占用）——打印提示后静默降级，不影响正常使用。
    """
    name = load_hotkey()
    if name is None:
        print("[老板键] 已关闭（config.json boss_hotkey=none）")
        return False, name
    vk = _VK_BY_NAME[name]

    def pump():
        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, _HOTKEY_ID, _MOD_NOREPEAT, vk):
            print(f"[老板键] 注册失败：{name.upper()} 可能被其他程序占用")
            return
        print(f"[老板键] 已注册 {name.upper()}：一键启动/停止任务")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    callback()
                except Exception as e:
                    print(f"[老板键] 回调异常: {e}")
                # 回调期间（尤其停止任务 join 数秒）到达的按键会滞留在队列，
                # 全部清掉——否则停止完成后旧按键再触发一次 toggle 又把任务
                # 拉起来（用户实测「无法停止」的根因）
                while user32.PeekMessageW(ctypes.byref(msg), None,
                                          WM_HOTKEY, WM_HOTKEY, 1):
                    pass
        user32.UnregisterHotKey(None, _HOTKEY_ID)

    threading.Thread(target=pump, daemon=True, name="boss-hotkey").start()
    return True, name
