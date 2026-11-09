"""确定性打包脚本测试：同内容两次打包必须字节一致。"""
import hashlib
import importlib.util
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "make_zip", ROOT / "scripts" / "make_zip.py")
assert _SPEC and _SPEC.loader
make_zip = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_zip)


def test_make_zip_is_deterministic(tmp_path):
    source = tmp_path / "src"
    (source / "res").mkdir(parents=True)
    (source / "zhuomuniao.exe").write_bytes(b"exe")
    (source / "res" / "b.txt").write_bytes(b"bbb")
    (source / "res" / "a.txt").write_bytes(b"aaa")

    first = make_zip.make_zip(source, tmp_path / "a.zip")
    second = make_zip.make_zip(source, tmp_path / "b.zip")
    assert hashlib.sha256(first.read_bytes()).hexdigest() == \
        hashlib.sha256(second.read_bytes()).hexdigest()

    with zipfile.ZipFile(first) as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert names == ["res/a.txt", "res/b.txt", "zhuomuniao.exe"]
        for info in zf.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert zf.read("res/a.txt") == b"aaa"
