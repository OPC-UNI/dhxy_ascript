"""消息服务单实例锁：原子获取/占用/stale 接管/释放归属。"""
import os
import time

from src.app.messages.lockfile import LOCK_FILENAME, InstanceLock


def _lock(tmp_path, alive=True, create=1000.0):
    return InstanceLock(tmp_path,
                        pid_alive=lambda pid: alive,
                        pid_create_time=lambda pid: create)


def test_acquire_and_block(tmp_path):
    first = _lock(tmp_path)
    info = first.acquire()
    assert info.acquired is True
    assert (tmp_path / LOCK_FILENAME).exists()
    second = _lock(tmp_path)
    blocked = second.acquire()
    assert blocked.acquired is False
    assert blocked.pid == os.getpid()
    assert blocked.reason == "alive"
    first.release()
    assert not (tmp_path / LOCK_FILENAME).exists()


def test_dead_pid_takes_over(tmp_path):
    first = _lock(tmp_path)
    first.acquire()
    second = _lock(tmp_path, alive=False)
    info = second.acquire()
    assert info.acquired is True
    assert info.stale_taken is True


def test_pid_reuse_takes_over(tmp_path):
    first = _lock(tmp_path, create=1000.0)
    first.acquire()
    second = _lock(tmp_path, create=2000.0)
    info = second.acquire()
    assert info.acquired is True
    assert info.stale_taken is True


def test_invalid_lock_grace_then_takeover(tmp_path):
    path = tmp_path / LOCK_FILENAME
    path.write_text("not json", encoding="utf-8")
    recent = _lock(tmp_path).acquire()
    assert recent.acquired is False
    assert recent.reason == "invalid_recent"
    old = time.time() - 40
    os.utime(path, (old, old))
    takeover = _lock(tmp_path).acquire()
    assert takeover.acquired is True
    assert takeover.stale_taken is True


def test_release_only_own_lock(tmp_path):
    first = _lock(tmp_path)
    first.acquire()
    other = _lock(tmp_path)
    other.release()
    assert (tmp_path / LOCK_FILENAME).exists()
    first.release()
    assert not (tmp_path / LOCK_FILENAME).exists()


def test_creates_directory(tmp_path):
    directory = tmp_path / "nested" / "dir"
    info = _lock(directory).acquire()
    assert info.acquired is True
    assert (directory / LOCK_FILENAME).exists()
