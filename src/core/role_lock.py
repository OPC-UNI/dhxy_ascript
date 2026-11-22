"""跨实例同角色互斥（会话级命名互斥体）。

多窗口允许同一安装目录单实例、不同安装目录并行（2026-11-22 用户
确认）。两个实例会枚举到同一批游戏窗口：worker 启动时按角色 ID 获取
`Local\\ZhuomuniaoRole_<角色ID>` 命名互斥体，先启动者持有；另一实例
的同一角色启动被拒绝（主进程启动前用 is_held 探测，子进程 acquire
兜底竞态）。

约束：只有 worker 子进程 acquire/release（同一线程成对调用）；主进程
只用 is_held 只读探测。互斥体随进程退出由系统自动释放（强杀安全）。
"""
import ctypes
from ctypes import wintypes

_MUTEX_PREFIX = "Local\\ZhuomuniaoRole_"
_SYNCHRONIZE = 0x00100000
_MUTEX_MODIFY_STATE = 0x0001
_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_k32.OpenMutexW.restype = wintypes.HANDLE
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_k32.ReleaseMutex.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CloseHandle.restype = wintypes.BOOL


def _name(role_id):
    return f"{_MUTEX_PREFIX}{role_id}"


def acquire(role_id):
    """尝试获取角色互斥体（非阻塞）。

    成功返回句柄（调用方线程持有，直到 release）；被其他实例/进程
    持有时返回 None。
    """
    if not role_id:
        return None
    try:
        handle = _k32.CreateMutexW(None, False, _name(role_id))
        if not handle:
            return None
        result = _k32.WaitForSingleObject(handle, 0)
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            return handle
        _k32.CloseHandle(handle)
        return None
    except Exception:
        return None


def release(handle):
    """释放角色互斥体（acquire 的同一线程调用）。"""
    if not handle:
        return
    try:
        _k32.ReleaseMutex(handle)
    except Exception:
        pass
    try:
        _k32.CloseHandle(handle)
    except Exception:
        pass


def is_held(role_id):
    """角色互斥体是否被持有（主进程只读探测；不获取所有权）。"""
    if not role_id:
        return False
    handle = None
    try:
        handle = _k32.OpenMutexW(_SYNCHRONIZE | _MUTEX_MODIFY_STATE,
                                 False, _name(role_id))
        if not handle:
            return False
        result = _k32.WaitForSingleObject(handle, 0)
        if result in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
            # 当前线程拿到了所有权：没有其他持有者，立即归还
            _k32.ReleaseMutex(handle)
            return False
        return result == _WAIT_TIMEOUT
    except Exception:
        return False
    finally:
        if handle:
            _k32.CloseHandle(handle)
