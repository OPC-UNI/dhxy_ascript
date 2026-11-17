"""消息服务单实例锁（同一配置目录只允许一个实例启用）。

Telegram 同一 token 只允许一个 getUpdates 消费者；两个实例同时轮询会
409 冲突并随机抢消息。锁文件用 O_EXCL 原子创建，PID 存活 + 进程创建
时间双重校验，崩溃残留可 stale 接管；释放只删自己的锁。
"""
import ctypes
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOCK_FILENAME = "message_service.lock"
LOCK_SCHEMA = 1
INVALID_LOCK_GRACE = 30.0  # 空/损坏锁在 30 秒内视为启动竞争
PID_REUSE_TOLERANCE = 2.0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5

_K32 = None


def _kernel32():
    global _K32
    if _K32 is None:
        _K32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _K32


def _pid_alive(pid):
    """PID 是否存活；权限不足保守视为存活（避免误抢）。"""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        handle = _kernel32().OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ctypes.get_last_error() == ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not _kernel32().GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == STILL_ACTIVE
        finally:
            _kernel32().CloseHandle(handle)
    except Exception:
        return True


def _pid_create_time(pid):
    """进程创建时间（epoch 秒）；查不到返回 None。"""
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        handle = _kernel32().OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            created = ctypes.c_ulonglong()
            exited = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            if not _kernel32().GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            files = created.value
            return files / 1e7 - 11644473600.0
        finally:
            _kernel32().CloseHandle(handle)
    except Exception:
        return None


@dataclass
class LockInfo:
    acquired: bool
    pid: int = 0
    stale_taken: bool = False
    reason: str = ""
    path: str = ""


class InstanceLock:
    def __init__(self, directory, clock=time.time, pid=None,
                 pid_alive=None, pid_create_time=None):
        self.path = Path(directory) / LOCK_FILENAME
        self._clock = clock
        self._pid = int(pid or os.getpid())
        self._pid_alive = pid_alive or _pid_alive
        self._pid_create_time = pid_create_time or _pid_create_time
        self._token = uuid.uuid4().hex
        self.info = LockInfo(False, path=str(self.path))

    def acquire(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return LockInfo(False, reason=f"error: {e}", path=str(self.path))
        stale_taken = False
        for _ in range(3):
            try:
                fd = os.open(str(self.path),
                             os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                decision = self._stale_decision()
                if decision != "stale":
                    info = self._read() or {}
                    return LockInfo(False, pid=int(info.get("pid") or 0),
                                    reason=decision,
                                    path=str(self.path))
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    return LockInfo(False, reason="race", path=str(self.path))
                stale_taken = True
                continue
            except OSError as e:
                return LockInfo(False, reason=f"error: {e}",
                                path=str(self.path))
            payload = {
                "schema": LOCK_SCHEMA,
                "pid": self._pid,
                "create_time": self._pid_create_time(self._pid) or 0,
                "token": self._token,
                "started_at": self._clock(),
            }
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
            except Exception:
                try:
                    self.path.unlink()
                except OSError:
                    pass
                return LockInfo(False, reason="error: write failed",
                                path=str(self.path))
            self.info = LockInfo(True, pid=self._pid,
                                 stale_taken=stale_taken,
                                 path=str(self.path))
            return self.info
        return LockInfo(False, reason="race", path=str(self.path))

    def release(self):
        """只删除本实例（pid+token 都匹配）的锁。"""
        info = self._read()
        if info and int(info.get("pid") or 0) == self._pid \
                and info.get("token") == self._token:
            try:
                self.path.unlink()
            except OSError:
                pass
        self.info = LockInfo(False, path=str(self.path))

    def _read(self):
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _stale_decision(self):
        """'stale'（可接管）| 'alive' | 'invalid_recent' | 'race'。"""
        info = self._read()
        if info is None:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return "race"
            return "stale" if age >= INVALID_LOCK_GRACE else "invalid_recent"
        try:
            pid = int(info.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            try:
                age = time.time() - self.path.stat().st_mtime
            except OSError:
                return "race"
            return "stale" if age >= INVALID_LOCK_GRACE else "invalid_recent"
        if not self._pid_alive(pid):
            return "stale"
        recorded = info.get("create_time")
        if recorded:
            actual = self._pid_create_time(pid)
            try:
                if actual is not None and \
                        abs(float(actual) - float(recorded)) > PID_REUSE_TOLERANCE:
                    return "stale"  # PID 复用：原进程已退出
            except (TypeError, ValueError):
                pass
        return "alive"
