"""日志末尾读取：行数/半行截断/编码容错。"""
import pytest

from src.app.messages.log_tail import read_tail_lines


def test_tail_last_lines(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("\n".join(f"line{i}" for i in range(10)),
                    encoding="utf-8")
    assert read_tail_lines(path, 3) == ["line7", "line8", "line9"]
    assert len(read_tail_lines(path, 0)) == 10


def test_drops_partial_first_line(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("X" * 1000 + "\na\nb\n", encoding="utf-8")
    lines = read_tail_lines(path, 10, max_bytes=10)
    assert lines == ["a", "b"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_tail_lines(tmp_path / "missing.log", 5)


def test_invalid_utf8_replaced(tmp_path):
    path = tmp_path / "a.log"
    path.write_bytes(b"ok\n\xff\xfe\n")
    lines = read_tail_lines(path, 10)
    assert len(lines) == 2
    assert lines[0] == "ok"
