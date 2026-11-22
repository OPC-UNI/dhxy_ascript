"""同一安装目录单实例锁 + 二次启动唤起已有面板。

多窗口（2026-11-22 用户确认）：允许不同安装目录各开一个实例，但同一
安装目录不允许跑第二个（共享 config/data/logs/热键会互相踩）。锁的
键 = 安装目录路径的 SHA-256 前 16 位，互斥体/事件都在会话命名空间：

- `acquire()`：主实例取得互斥体；第二实例返回 None。
- `signal_show()`：第二实例在退出前触发 `Local\\ZhuomuniaoShow_<key>`
  事件，主实例监听线程把控制窗口显示出来（从托盘恢复）。
- `--overlay-helper` / spawn 子进程不调用本模块（main 的入口分支在前）。

互斥体随进程退出由系统自动释放（强杀安全）；事件对象由主实例创建，
第二实例只 OpenEvent（不存在时临时 CreateEvent 也能触发）。
"""
import ctypes
import hashlib
import os
import sys
import threading
from pathlib import Path
from ctypes import wintypes

_MUTEX_PREFIX = "Local\\ZhuomuniaoInstance_"
_EVENT_PREFIX = "Local\\ZhuomuniaoShow_"
_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_EVENT_MODIFY_STATE = 0x0002

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                              wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.SetEvent.argtypes = [wintypes.HANDLE]
_k32.SetEvent.restype = wintypes.BOOL
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


def install_root():
    """安装目录：冻结态 = exe 所在目录；源码态 = 工程根目录。"""
    if "__compiled__" in globals():
        return str(Path(sys.executable).resolve().parent)
    env = os.environ.get("ASCRIPT_PROJECT_ROOT")
    if env:
        return str(Path(env).resolve())
    return str(Path(__file__).resolve().parents[2])


def _key(root=None):
    path = str(root if root is not None else install_root()).lower()
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def lock_name(root=None):
    return _MUTEX_PREFIX + _key(root)


def event_name(root=None):
    return _EVENT_PREFIX + _key(root)


class InstanceLock:
    """主实例持有的锁句柄（mutex + show event）。"""

    def __init__(self, mutex_handle, event_handle):
        self.mutex_handle = mutex_handle
        self.event_handle = event_handle


def acquire():
    """尝试成为本安装目录的主实例；已有实例返回 None。

    CreateMutex 失败时 fail-open（返回无 mutex 的锁），避免系统 API
    异常导致程序完全无法启动；此时事件仍用于二次启动唤起。
    """
    key = _key()
    mutex = _k32.CreateMutexW(None, False, _MUTEX_PREFIX + key)
    if not mutex:
        event = _k32.CreateEventW(None, False, False, _EVENT_PREFIX + key)
        return InstanceLock(0, event or 0)
    result = _k32.WaitForSingleObject(mutex, 0)
    if result not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        _k32.CloseHandle(mutex)
        return None
    event = _k32.CreateEventW(None, False, False, _EVENT_PREFIX + key)
    return InstanceLock(mutex, event or 0)


def signal_show():
    """二次启动：触发主实例的「显示面板」事件。"""
    try:
        handle = _k32.OpenEventW(_EVENT_MODIFY_STATE, False, event_name())
        if not handle:
            handle = _k32.CreateEventW(None, False, False, event_name())
        if handle:
            _k32.SetEvent(handle)
            _k32.CloseHandle(handle)
    except Exception:
        pass


def start_show_listener(lock, callback):
    """主实例：监听 show 事件，收到后执行 callback（显示控制窗口）。"""
    handle = getattr(lock, "event_handle", 0)
    if not handle:
        return None

    def _loop():
        while True:
            try:
                result = _k32.WaitForSingleObject(handle, 500)
                if result == _WAIT_OBJECT_0:
                    try:
                        callback()
                    except Exception:
                        pass
            except Exception:
                return

    thread = threading.Thread(target=_loop, daemon=True,
                              name="instance-show-listener")
    thread.start()
    return thread


def release(lock):
    """释放锁（正常退出兜底；进程退出系统也会自动释放）。"""
    if not lock:
        return
    for handle in (getattr(lock, "event_handle", 0),
                   getattr(lock, "mutex_handle", 0)):
        if handle:
            try:
                _k32.CloseHandle(handle)
            except Exception:
                pass
