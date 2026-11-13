"""软件自动更新核心逻辑测试（纯本地，不启动 GUI / 不碰游戏窗口）。

覆盖：版本比较、清单校验、分片选择、下载校验、安全解压、覆盖/回滚、
中断 journal 恢复。真实 exe 替换/重启的健康检查在实机验收中覆盖。
"""
import functools
import hashlib
import http.server
import json
import pathlib
import subprocess
import threading
import zipfile

import pytest

from src.app import updater


# ---------------- 工具 ----------------

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def web_root(tmp_path):
    """本地 HTTP 服务：http://127.0.0.1:<port>/ 指向 tmp 目录。"""
    root = tmp_path / "www"
    root.mkdir()
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, "http://127.0.0.1:%d" % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data)}


def _manifest(base, app_info, pylibs_info, version="2.10.0",
              min_supported="0.0.0", extra=None):
    data = {
        "schema": 1,
        "app": "zhuomuniao",
        "channel": "stable",
        "version": version,
        "min_supported_version": min_supported,
        "notes": "- 测试更新",
        "parts": [
            {"name": "app", "url": base + "/app.zip", **app_info},
            {"name": "pylibs", "url": base + "/pylibs.zip", **pylibs_info},
        ],
    }
    if extra:
        data.update(extra)
    return data


def _make_install(tmp_path, exe=b"old-exe", res=b"old-res"):
    install = tmp_path / "install"
    install.mkdir()
    (install / "zhuomuniao.exe").write_bytes(exe)
    (install / "res").mkdir()
    (install / "res" / "old.txt").write_bytes(res)
    (install / "pylibs").mkdir()
    (install / "pylibs" / "lib.bin").write_bytes(b"old-lib")
    (install / "config.json").write_text('{"keep": true}', encoding="utf-8")
    (install / "logs").mkdir()
    (install / "logs" / "run.log").write_text("keep", encoding="utf-8")
    return install


# ---------------- 版本 ----------------

def test_version_compare():
    assert updater.parse_version("v2.10.0") == (2, 10, 0)
    assert updater.parse_version("2.9.6") == (2, 9, 6)
    assert updater.parse_version("2.10") is None
    assert updater.compare_versions("2.10.0", "2.9.9") == 1
    assert updater.compare_versions("2.9.6", "2.9.6") == 0
    assert updater.compare_versions("2.0.0", "2.0.1") == -1
    assert updater.is_newer("v2.10.0", "2.9.6")


# ---------------- 清单校验 ----------------

def test_manifest_valid():
    m = updater.validate_manifest(_manifest(
        "https://example.com/x",
        {"sha256": "a" * 64, "size": 10},
        {"sha256": "b" * 64, "size": 20}))
    assert m["version"] == "2.10.0"
    assert m["total_size"] == 30
    assert m["mandatory"] is False


def test_manifest_validation_errors():
    base = _manifest(
        "https://example.com/x",
        {"sha256": "a" * 64, "size": 10},
        {"sha256": "b" * 64, "size": 20})
    cases = [
        {**base, "schema": 2},
        {**base, "channel": "beta"},
        {**base, "version": "2.10"},
        {**base, "parts": []},
        {**base, "parts": [{"name": "config", "url": "https://e/x",
                            "sha256": "a" * 64, "size": 1}]},
        {**base, "parts": [{"name": "app", "url": "http://example.com/x",
                            "sha256": "a" * 64, "size": 1}]},
        {**base, "parts": [{"name": "app", "url": "https://e/x",
                            "sha256": "xyz", "size": 1}]},
        {**base, "parts": [{"name": "app", "url": "https://e/x",
                            "sha256": "a" * 64, "size": True}]},
    ]
    for data in cases:
        with pytest.raises(updater.UpdateError):
            updater.validate_manifest(data)


def test_manifest_mandatory_by_min_version():
    data = _manifest(
        "https://example.com/x",
        {"sha256": "a" * 64, "size": 10},
        {"sha256": "b" * 64, "size": 20},
        min_supported="2.0.0")
    m = updater.validate_manifest(data, current_version="1.9.0")
    assert m["mandatory"] is True


# ---------------- 安全解压 ----------------

def test_safe_extract_rejects_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", b"x")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_absolute(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/tmp/evil.txt", b"x")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(archive, tmp_path / "out")


def test_safe_extract_rejects_symlink(tmp_path):
    archive = tmp_path / "bad.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, b"target")
    with pytest.raises(updater.UpdateError):
        updater._safe_extract(archive, tmp_path / "out")


def test_safe_extract_ok(tmp_path):
    archive = tmp_path / "ok.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("zhuomuniao.exe", b"exe")
        zf.writestr("res/ui.txt", b"ui")
    updater._safe_extract(archive, tmp_path / "out")
    assert (tmp_path / "out" / "zhuomuniao.exe").read_bytes() == b"exe"
    assert (tmp_path / "out" / "res" / "ui.txt").read_bytes() == b"ui"


# ---------------- 检查更新 ----------------

def test_check_for_update_via_http(tmp_path, web_root):
    root, base = web_root
    app_info = _zip(root / "app.zip", {"zhuomuniao.exe": b"exe"})
    pylibs_info = _zip(root / "pylibs.zip", {"pylibs/lib.bin": b"lib"})
    data = _manifest(base, app_info, pylibs_info)
    (root / "update.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")

    result = updater.check_for_update(
        "2.9.6", feed_url=base + "/update.json")
    assert result["status"] == "available"
    assert result["manifest"]["version"] == "2.10.0"

    result = updater.check_for_update(
        "2.10.0", feed_url=base + "/update.json")
    assert result["status"] == "up_to_date"


def test_check_for_update_http_error(web_root):
    _, base = web_root
    result = updater.check_for_update(
        "2.9.6", feed_url=base + "/missing.json")
    assert result["status"] == "error"
    assert result["error"]


# ---------------- 下载 / 分片选择 ----------------

def test_prepare_update_skips_unchanged_pylibs(tmp_path, web_root):
    root, base = web_root
    app_info = _zip(root / "app.zip", {
        "zhuomuniao.exe": b"new-exe",
        "res/new.txt": b"new-res",
    })
    pylibs_info = _zip(root / "pylibs.zip", {"pylibs/lib.bin": b"new-lib"})
    manifest = updater.validate_manifest(
        _manifest(base, app_info, pylibs_info))

    install = _make_install(tmp_path)
    # 上次安装记录里 pylibs 哈希与本次相同 → 跳过 pylibs 下载
    updater._save_installed(
        install / "update", "2.9.6",
        [{"name": "pylibs", "sha256": pylibs_info["sha256"]}])

    stages = []
    pending = updater.prepare_update(
        manifest, install, "2.9.6", cwd=str(install),
        progress_cb=lambda p: stages.append(p["stage"]))

    assert pending["apply_parts"] == ["app"]
    staging = pathlib.Path(pending["staging"])
    assert (staging / "zhuomuniao.exe").read_bytes() == b"new-exe"
    assert (staging / "res" / "new.txt").read_bytes() == b"new-res"
    assert not (staging / "pylibs").exists()
    assert "downloading" in stages and "ready" in stages
    # pending.json 必须可被 helper 读取
    data = json.loads((install / "update" / "pending.json").read_text("utf-8"))
    assert data["apply_parts"] == ["app"]
    assert data["install_dir"] == str(install.resolve())
    # 用户数据未动
    assert (install / "config.json").read_text("utf-8") == '{"keep": true}'


def test_prepare_update_hash_mismatch(tmp_path, web_root):
    root, base = web_root
    app_info = _zip(root / "app.zip", {"zhuomuniao.exe": b"new-exe"})
    pylibs_info = _zip(root / "pylibs.zip", {"pylibs/lib.bin": b"new-lib"})
    bad = dict(app_info)
    bad["sha256"] = "0" * 64
    manifest = updater.validate_manifest(_manifest(base, bad, pylibs_info))
    install = _make_install(tmp_path)
    with pytest.raises(updater.UpdateError):
        updater.prepare_update(manifest, install, "2.9.6")
    assert not (install / "update" / "staging-2.10.0" / "zhuomuniao.exe").exists()


# ---------------- 覆盖 / 回滚 ----------------

def test_backup_apply_rollback_preserves_user_data(tmp_path):
    install = _make_install(tmp_path)
    backup = tmp_path / "backup"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "zhuomuniao.exe").write_bytes(b"new-exe")
    (staging / "res").mkdir()
    (staging / "res" / "new.txt").write_bytes(b"new-res")

    updater._backup_targets(install, backup, ["app"])
    # exe 用硬链接备份：旧 exe 仍在原位，res 已移走
    assert (install / "zhuomuniao.exe").read_bytes() == b"old-exe"
    assert not (install / "res").exists()
    assert (backup / "res" / "old.txt").exists()

    updater._apply_targets(staging, install, ["app"])
    assert (install / "zhuomuniao.exe").read_bytes() == b"new-exe"
    assert (install / "res" / "new.txt").exists()
    assert (install / "config.json").read_text("utf-8") == '{"keep": true}'
    assert (install / "logs" / "run.log").read_text("utf-8") == "keep"
    updater._verify_install(install, ["app"])

    updater._rollback(install, backup)
    assert (install / "zhuomuniao.exe").read_bytes() == b"old-exe"
    assert (install / "res" / "old.txt").exists()
    assert not (install / "res" / "new.txt").exists()
    assert (install / "config.json").read_text("utf-8") == '{"keep": true}'


# ---------------- helper 全流程（桩掉进程等待/启动） ----------------

class _AliveProc:
    """模拟新版本启动后一直存活（健康检查通过）。"""
    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("zhuomuniao.exe", timeout)


class _CrashProc:
    """模拟新版本启动后立即以非 0 退出（健康检查失败 → 回滚）。"""
    def wait(self, timeout=None):
        return 1


def _pending(install, staging):
    update_dir = install / "update"
    return {
        "schema": 1,
        "from_version": "2.9.6",
        "to_version": "2.10.0",
        "install_dir": str(install),
        "cwd": str(install),
        "staging": str(staging),
        "apply_parts": ["app"],
        "parts": [{"name": "app", "sha256": "a" * 64},
                  {"name": "pylibs", "sha256": "b" * 64}],
        "main_pid": None,
        "log": str(update_dir / "update.log"),
    }


def _staging(tmp_path):
    staging = tmp_path / "staging-2.10.0"
    staging.mkdir()
    (staging / "zhuomuniao.exe").write_bytes(b"new-exe")
    (staging / "res").mkdir()
    (staging / "res" / "new.txt").write_bytes(b"new-res")
    return staging


def test_apply_update_success_flow(tmp_path, monkeypatch):
    install = _make_install(tmp_path)
    staging = _staging(tmp_path)
    pending = _pending(install, staging)
    monkeypatch.setattr(updater, "_wait_install_processes",
                        lambda *a, **k: True)
    monkeypatch.setattr(updater, "_start_app", lambda *a, **k: _AliveProc())
    monkeypatch.setattr(updater, "_message_box", lambda *a, **k: None)
    logs = []
    code = updater._apply_update(pending, logs.append)

    assert code == 0
    assert (install / "zhuomuniao.exe").read_bytes() == b"new-exe"
    assert (install / "res" / "new.txt").exists()
    # pylibs 未变更：不参与本次 apply
    assert (install / "pylibs" / "lib.bin").read_bytes() == b"old-lib"
    assert (install / "config.json").read_text("utf-8") == '{"keep": true}'
    assert (install / "logs" / "run.log").read_text("utf-8") == "keep"
    installed = json.loads(
        (install / "update" / "installed.json").read_text("utf-8"))
    assert installed["version"] == "2.10.0"
    assert installed["parts"]["app"] == "a" * 64
    assert updater.read_last_result(install_dir=install)["status"] == "success"
    assert not (install / "update" / "journal.json").exists()
    assert not (install / "update" / "staging-2.10.0").exists()
    assert any("更新成功" in line for line in logs)


def test_apply_update_crash_rolls_back(tmp_path, monkeypatch):
    install = _make_install(tmp_path)
    staging = _staging(tmp_path)
    pending = _pending(install, staging)
    monkeypatch.setattr(updater, "_wait_install_processes",
                        lambda *a, **k: True)
    monkeypatch.setattr(updater, "_start_app", lambda *a, **k: _CrashProc())
    monkeypatch.setattr(updater, "_message_box", lambda *a, **k: None)
    logs = []
    code = updater._apply_update(pending, logs.append)

    assert code == 1
    assert (install / "zhuomuniao.exe").read_bytes() == b"old-exe"
    assert (install / "res" / "old.txt").exists()
    assert not (install / "res" / "new.txt").exists()
    assert (install / "pylibs" / "lib.bin").read_bytes() == b"old-lib"
    assert (install / "config.json").read_text("utf-8") == '{"keep": true}'
    result = updater.read_last_result(install_dir=install)
    assert result["status"] == "rolled_back"
    assert not (install / "update" / "installed.json").exists()
    assert not (install / "update" / "journal.json").exists()
    assert any("已回滚" in line for line in logs)


# ---------------- 启动恢复 ----------------

def test_recover_if_needed_restores_dirs(tmp_path):
    install = _make_install(tmp_path)
    backup = install / "update" / "backup" / "2.9.6-1"
    updater._backup_targets(install, backup, ["app"])
    # 模拟新版本已把 exe 换上、res 尚未换入时中断。
    # 必须用 os.replace（换新 inode），不能在原 exe 上 write_bytes——
    # 后者会截断硬链接备份指向的同一份旧内容。
    new_exe = tmp_path / "new.exe"
    new_exe.write_bytes(b"new-exe")
    new_exe.replace(install / "zhuomuniao.exe")
    updater._write_json(install / "update" / "journal.json", {
        "phase": "apply",
        "from_version": "2.9.6",
        "to_version": "2.10.0",
        "install_dir": str(install),
        "backup": str(backup),
        "parts": [],
        "main_pid": None,
    })

    result = updater.recover_if_needed("2.9.6", install_dir=install)

    assert result["status"] == "rolled_back"
    assert (install / "res" / "old.txt").exists()
    assert not (install / "update" / "journal.json").exists()
    # 运行中的 exe 不替换（旧版本本来就在跑）
    assert (install / "zhuomuniao.exe").read_bytes() == b"new-exe"


def test_recover_marks_completed_when_new_version_running(tmp_path):
    install = _make_install(tmp_path)
    updater._write_json(install / "update" / "journal.json", {
        "phase": "restart",
        "from_version": "2.9.6",
        "to_version": "2.10.0",
        "install_dir": str(install),
        "backup": str(install / "update" / "backup" / "x"),
        "parts": [{"name": "app", "sha256": "a" * 64}],
        "main_pid": None,
    })
    result = updater.recover_if_needed("2.10.0", install_dir=install)
    assert result["status"] == "completed"
    assert not (install / "update" / "journal.json").exists()


# ---------------- last_result ----------------

def test_last_result_roundtrip(tmp_path):
    install = _make_install(tmp_path)
    updater._write_json(install / "update" / "last_result.json",
                        {"status": "success", "from": "2.9.6",
                         "to": "2.10.0"})
    data = updater.read_last_result(install_dir=install)
    assert data["status"] == "success"
    updater.clear_last_result(install_dir=install)
    assert updater.read_last_result(install_dir=install) is None


# ---------------- install_root ----------------

def test_install_root_source_mode_is_repo_root():
    """源码态取仓库根：迁移到 src/app/ 后不能变成 src/。"""
    if updater.is_frozen():
        pytest.skip("冻结态安装目录由 exe 位置决定")
    assert updater.install_root() == pathlib.Path(__file__).resolve().parents[1]
