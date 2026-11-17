"""日志文件末尾读取（/log 指令用，不整文件加载）。"""
import os
from pathlib import Path


def read_tail_lines(path, max_lines=20, max_bytes=262144):
    """返回文件最后 max_lines 行。

    max_bytes 只从末尾读取，避免大日志整文件读入；从中间截断时丢弃
    首个半行；UTF-8 损坏字节替换不抛异常。
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - max(0, int(max_bytes)))
        f.seek(start)
        raw = f.read()
    if start > 0:
        idx = raw.find(b"\n")
        raw = raw[idx + 1:] if idx >= 0 else b""
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines()
             if line.strip()]
    limit = max(0, int(max_lines))
    return lines[-limit:] if limit else lines
