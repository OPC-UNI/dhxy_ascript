"""软件自动更新：清单校验、分片下载、覆盖升级与失败回滚。

运行形态
--------
- 主进程：`check_for_update()` 检查 → `prepare_update()` 下载/解压 →
  `launch_apply_helper()` 拉起更新助手。
- 更新助手：主进程把当前 exe 复制到 ``%TEMP%``，用副本以
  ``--apply-update <pending.json>`` 启动。助手运行在临时目录，不锁定安装
  目录的 exe；等主进程退出后备份、替换、重启，失败自动回滚。

约束
----
- 本模块只依赖标准库：``main.py`` 在导入 ascript/webview/uiautomation 等
  重依赖之前执行 ``--apply-update`` 分支，保证助手快速进入。
- 只覆盖 ``PART_TARGETS`` 白名单内的文件（exe/res/pylibs）；config.json、
  schemes.json、task_stats.json、logs/ 等用户数据一律不动。
- 安装目录 = 冻结 exe 所在目录；源码运行取仓库根，仅用于本地联调。
"""
import ctypes
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from ctypes import wintypes

from . import update_config

IS_WINDOWS = os.name == "nt"

UPDATE_DIR_NAME = "update"
PENDING_NAME = "pending.json"
INSTALLED_NAME = "installed.json"
LAST_RESULT_NAME = "last_result.json"
JOURNAL_NAME = "journal.json"
LOG_NAME = "update.log"

SCHEMA = 1
# 分片名 → 允许覆盖的安装目录顶层路径。清单里的 targets 仅作展示，
# 客户端以本表为准，恶意清单无法越界改 config.json 等文件。
PART_TARGETS = {
    "app": ("zhuomuniao.exe", "res"),
    "pylibs": ("pylibs",),
}
ALLOWED_TARGETS = frozenset(
    target for targets in PART_TARGETS.values() for target in targets
)

MAX_MANIFEST_BYTES = 256 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024  # 1 GB 解压上限（防 zip bomb）
DOWNLOAD_CHUNK = 64 * 1024
UA = "Zhuomuniao-Updater/1.0 (Windows)"

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

DETACHED_PROCESS = 0x00000008
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
WAIT_TIMEOUT = 0x00000102
SYNCHRONIZE = 0x00100000


class UpdateError(Exception):
    """可预期的更新错误（展示给用户 / 写入日志）。"""


# ==================== 版本 ====================

def parse_version(raw):
    """把 ``vX.Y.Z`` / ``X.Y.Z`` 解析为三元组；非法返回 None。"""
    m = _VERSION_RE.match(str(raw or "").strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def compare_versions(left, right):
    """比较版本：left > right 返回 1，相等 0，小于 -1。"""
    lv = parse_version(left)
    rv = parse_version(right)
    if lv is None or rv is None:
        raise UpdateError("版本号格式不正确：%r / %r" % (left, right))
    return (lv > rv) - (lv < rv)


def is_newer(remote, current):
    return compare_versions(remote, current) > 0


def is_frozen():
    """Nuitka 冻结态判据（Nuitka 不设置 sys.frozen）。

    主判据是模块自身的 ``__compiled__``（Nuitka 注入每个编译模块）；
    再兜底检查 ``__main__``，与 src/env_check.py 的判据保持一致。
    """
    if "__compiled__" in globals():
        return True
    main = sys.modules.get("__main__")
    return main is not None and "__compiled__" in vars(main)


def install_root():
    """安装目录：冻结态取 exe 所在目录，源码态取仓库根。"""
    if is_frozen():
        return pathlib.Path(sys.executable).resolve().parent
    return pathlib.Path(__file__).resolve().parents[1]


# ==================== URL / HTTP ====================

def _validate_download_url(url):
    """只允许 HTTPS；本地联调允许 http://127.0.0.1 / localhost。"""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return
    raise UpdateError("更新地址必须是 HTTPS：%s" % (url,))


def _cache_bust(url):
    sep = "&" if "?" in url else "?"
    return "%s%sts=%d" % (url, sep, int(time.time()))


def resolve_feed_url(feed_url=None):
    """清单地址优先级：环境变量 > config 覆盖 > 编译期默认。"""
    env = os.environ.get("ZMN_UPDATE_FEED", "").strip()
    if env:
        return env
    override = str(feed_url or "").strip()
    if override:
        return override
    return update_config.UPDATE_FEED_URL


def fetch_manifest(feed_url=None, timeout=None):
    """拉取 update.json 原始 JSON（不校验字段）。"""
    url = resolve_feed_url(feed_url)
    _validate_download_url(url)
    req = urllib.request.Request(
        _cache_bust(url),
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(
                req, timeout=timeout or update_config.CHECK_TIMEOUT) as resp:
            raw = resp.read(MAX_MANIFEST_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise UpdateError("检查更新失败：HTTP %s" % e.code)
    except urllib.error.URLError as e:
        raise UpdateError("检查更新失败：%s" % (e.reason,))
    except OSError as e:
        raise UpdateError("检查更新失败：%s" % e)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("更新清单过大")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError("更新清单不是合法 JSON")


# ==================== 清单校验 ====================

def _validate_manifest(data, current_version=None):
    """校验并规整清单；非法抛 UpdateError。"""
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式错误")
    if data.get("schema") != SCHEMA:
        raise UpdateError("不支持的更新清单版本：%r" % (data.get("schema"),))
    app = data.get("app")
    if app not in (None, update_config.APP_NAME):
        raise UpdateError("更新清单应用标识不匹配：%r" % (app,))
    channel = str(data.get("channel") or update_config.CHANNEL)
    if channel != update_config.CHANNEL:
        raise UpdateError("更新渠道不匹配：%r" % (channel,))

    raw_version = str(data.get("version") or "")
    parsed_version = parse_version(raw_version)
    if parsed_version is None:
        raise UpdateError("更新清单版本号格式错误：%r" % (raw_version,))
    version = "%d.%d.%d" % parsed_version

    raw_min = str(data.get("min_supported_version") or "0.0.0")
    parsed_min = parse_version(raw_min)
    if parsed_min is None:
        raise UpdateError("min_supported_version 格式错误：%r" % (raw_min,))
    min_supported = "%d.%d.%d" % parsed_min

    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise UpdateError("更新清单缺少 parts")
    parts = []
    seen = set()
    for raw in raw_parts:
        if not isinstance(raw, dict):
            raise UpdateError("part 格式错误")
        name = raw.get("name")
        if name not in PART_TARGETS:
            raise UpdateError("未知更新分片：%r" % (name,))
        if name in seen:
            raise UpdateError("更新分片重复：%r" % (name,))
        seen.add(name)
        url = str(raw.get("url") or "")
        _validate_download_url(url)
        mirrors = raw.get("mirror_urls") or []
        if not isinstance(mirrors, list):
            raise UpdateError("mirror_urls 格式错误")
        mirrors = [str(u) for u in mirrors]
        for mirror in mirrors:
            _validate_download_url(mirror)
        sha256 = str(raw.get("sha256") or "").lower()
        if not _SHA256_RE.match(sha256):
            raise UpdateError("分片 sha256 格式错误：%s" % (name,))
        size = raw.get("size")
        if type(size) is not int or size <= 0:
            raise UpdateError("分片 size 格式错误：%s" % (name,))
        parts.append({
            "name": name,
            "url": url,
            "mirror_urls": mirrors,
            "sha256": sha256,
            "size": size,
        })

    release_url = str(data.get("release_url") or "")
    if release_url:
        _validate_download_url(release_url)

    mandatory = bool(data.get("mandatory"))
    if current_version and compare_versions(current_version, min_supported) < 0:
        mandatory = True

    notes = str(data.get("notes") or "")
    if len(notes) > 65536:
        notes = notes[:65536]

    return {
        "schema": SCHEMA,
        "app": update_config.APP_NAME,
        "channel": channel,
        "version": version,
        "min_supported_version": min_supported,
        "published_at": str(data.get("published_at") or ""),
        "release_url": release_url,
        "notes": notes,
        "mandatory": mandatory,
        "parts": parts,
        "total_size": sum(part["size"] for part in parts),
    }


def validate_manifest(data, current_version=None):
    """公开的清单校验入口（CI 脚本与客户端共用）。"""
    return _validate_manifest(data, current_version=current_version)


def check_for_update(current_version, feed_url=None):
    """检查更新；返回 ``{"status": available|up_to_date|error, ...}``。"""
    current = str(current_version or "")
    if parse_version(current) is None:
        return {"status": "error", "current": current,
                "error": "当前版本号格式错误：%r" % (current,)}
    try:
        raw = fetch_manifest(feed_url)
        manifest = _validate_manifest(raw, current_version=current)
    except Exception as e:
        return {"status": "error", "current": current, "error": str(e)}
    if compare_versions(manifest["version"], current) > 0:
        return {"status": "available", "current": current, "manifest": manifest}
    return {"status": "up_to_date", "current": current, "manifest": manifest}


# ==================== 下载 / 解压 ====================

def _emit(progress_cb, stage, done, total, part=None):
    if progress_cb is None:
        return
    percent = int(done * 100 / total) if total else 0
    try:
        progress_cb({
            "stage": stage,
            "done": done,
            "total": total,
            "percent": max(0, min(100, percent)),
            "part": part or "",
        })
    except Exception:
        pass


def _download_one(url, dest, timeout, on_progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    hasher = hashlib.sha256()
    size = 0
    last_report = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, \
                open(dest, "wb") as out:
            while True:
                chunk = resp.read(DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
                # 每 1MB 回调一次，避免 64KB 级 evaluate_js 刷屏
                if on_progress is not None and size - last_report >= 1024 * 1024:
                    last_report = size
                    on_progress(size)
    except Exception:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    if on_progress is not None:
        on_progress(size)
    return size, hasher.hexdigest()


def _download_part(part, dest, progress_cb, offset, total):
    """下载单个分片，失败换镜像并重试一轮。"""
    urls = [part["url"]] + list(part.get("mirror_urls") or [])
    last_error = None
    for attempt in range(2):
        for url in urls:
            try:
                _emit(progress_cb, "downloading", offset, total, part["name"])
                size, sha256 = _download_one(
                    url, dest, update_config.DOWNLOAD_TIMEOUT,
                    on_progress=lambda done: _emit(
                        progress_cb, "downloading", offset + done,
                        total, part["name"]))
                if size != part["size"] or sha256 != part["sha256"]:
                    raise UpdateError(
                        "分片校验失败（%s）：大小 %d/%d，sha256 %s/%s"
                        % (part["name"], size, part["size"],
                           sha256[:12], part["sha256"][:12]))
                return
            except Exception as e:
                last_error = e
                try:
                    if os.path.exists(dest):
                        os.unlink(dest)
                except OSError:
                    pass
        if attempt == 0:
            time.sleep(0.5)
    raise UpdateError("下载 %s 失败：%s" % (part["name"], last_error))


def _safe_extract(zip_path, dest_dir):
    """解压到 dest_dir，拒绝绝对路径 / .. / 符号链接 / 超大解压。"""
    dest_root = pathlib.Path(dest_dir).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            # 下载阶段已校验 sha256，无需 testzip 再整包解压一遍；
            # 条目损坏会在下面 extract 时暴露。
            total = 0
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                if (name.startswith("/") or re.match(r"^[A-Za-z]:", name)
                        or "\x00" in name or ".." in name.split("/")):
                    raise UpdateError("更新包包含非法路径：%s" % name)
                # 符号链接（Unix mode 高 16 位）一律拒绝
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise UpdateError("更新包包含符号链接：%s" % name)
                total += info.file_size
                if total > MAX_EXTRACTED_BYTES:
                    raise UpdateError("更新包解压后过大")
                target = (dest_root / name).resolve()
                if not target.is_relative_to(dest_root):
                    raise UpdateError("更新包路径越界：%s" % name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, DOWNLOAD_CHUNK)
    except zipfile.BadZipFile:
        raise UpdateError("更新包不是有效的 zip")


def _sha8(sha256):
    return sha256[:8]


def _load_installed(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {}
    parts = data.get("parts")
    return parts if isinstance(parts, dict) else {}


def _save_installed(update_dir, version, parts):
    mapping = {}
    for part in parts:
        name = part.get("name")
        sha = part.get("sha256")
        if name in PART_TARGETS and isinstance(sha, str):
            mapping[name] = sha
    _write_json(pathlib.Path(update_dir) / INSTALLED_NAME, {
        "schema": SCHEMA,
        "version": version,
        "parts": mapping,
        "updated_at": time.time(),
    })


def prepare_update(manifest, install_dir, current_version, cwd=None,
                   progress_cb=None):
    """下载缺失分片、解压到 staging，写 pending.json 并返回 pending 数据。

    只下载 sha256 与 installed.json 记录不同的分片；pylibs 没变时不会重复
    下载。调用方随后停止任务并 ``launch_apply_helper()``。
    """
    install_dir = pathlib.Path(install_dir)
    if not install_dir.is_dir():
        raise UpdateError("安装目录不存在：%s" % install_dir)
    update_dir = install_dir / UPDATE_DIR_NAME
    try:
        update_dir.mkdir(parents=True, exist_ok=True)
        download_dir = update_dir / "download"
        download_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise UpdateError(
            "安装目录不可写，无法创建更新目录：%s（%s）" % (update_dir, e))
    # 清理上次失败/取消留下的下载文件（zip 或 .part），避免长期占用磁盘
    for old in download_dir.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    version = manifest["version"]
    staging = update_dir / ("staging-" + version)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    installed = _load_installed(update_dir / INSTALLED_NAME)
    changed = [
        part for part in manifest["parts"]
        if installed.get(part["name"]) != part["sha256"]
    ]
    total = sum(part["size"] for part in changed)

    if total > 0:
        free = shutil.disk_usage(str(install_dir)).free
        # 下载 zip + 解压 staging + 旧文件备份（备份的目录用 rename，不占额外
        # 空间；按 zip 大小 2.2 倍 + 100MB 余量估算足够）。
        required = int(total * 2.2) + 100 * 1024 * 1024
        if free < required:
            raise UpdateError(
                "磁盘空间不足：需要约 %.0f MB，可用 %.0f MB"
                % (required / 1024 / 1024, free / 1024 / 1024))

    done = 0
    for part in changed:
        zip_path = download_dir / (
            "%s-%s.zip" % (part["name"], _sha8(part["sha256"])))
        _download_part(part, str(zip_path), progress_cb, done, total)
        done += part["size"]
        _emit(progress_cb, "extracting", done, total, part["name"])
        _safe_extract(zip_path, staging)
        try:
            zip_path.unlink()
        except OSError:
            pass

    for part in changed:
        for target in PART_TARGETS[part["name"]]:
            if not (staging / target).exists():
                raise UpdateError("更新包缺少 %s（分片 %s）"
                                  % (target, part["name"]))
    for child in staging.iterdir():
        if child.name not in ALLOWED_TARGETS:
            raise UpdateError("更新包包含白名单外路径：%s" % child.name)

    pending = {
        "schema": SCHEMA,
        "from_version": current_version,
        "to_version": version,
        "install_dir": str(install_dir.resolve()),
        "cwd": str(pathlib.Path(cwd or os.getcwd()).resolve()),
        "staging": str(staging.resolve()),
        "apply_parts": [part["name"] for part in changed],
        "parts": [
            {"name": part["name"], "sha256": part["sha256"]}
            for part in manifest["parts"]
        ],
        "main_pid": os.getpid(),
        "log": str((update_dir / LOG_NAME).resolve()),
        "created_at": time.time(),
    }
    pending_path = update_dir / PENDING_NAME
    _write_json(pending_path, pending)
    pending["pending_path"] = str(pending_path)
    _emit(progress_cb, "ready", total, total)
    return pending


# ==================== 应用更新（helper） ====================

def _write_json(path, data):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(str(tmp), str(path))


def _append_log(log_path, message):
    try:
        log_path = pathlib.Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


def _move(src, dst):
    """同卷 rename 优先；跨卷回退 shutil.move。"""
    src = pathlib.Path(src)
    dst = pathlib.Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(str(src), str(dst))
        return
    except OSError:
        pass
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    shutil.move(str(src), str(dst))


def _remove_path(path):
    path = pathlib.Path(path)
    try:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    except OSError:
        pass


# ---- Windows 进程枚举（只依赖 ctypes） ----

class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


_K32 = None


def _kernel32():
    global _K32
    if _K32 is not None:
        return _K32
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.Process32FirstW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k.Process32NextW.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    k.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    _K32 = k
    return k


def _process_image_path(kernel32, pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return None


def _iter_process_paths():
    """生成 (pid, image_path)；无法读取路径的进程跳过。"""
    if not IS_WINDOWS:
        return
    kernel32 = _kernel32()
    TH32CS_SNAPPROCESS = 0x00000002
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            if pid > 4:
                path = _process_image_path(kernel32, pid)
                if path:
                    yield pid, path
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)


def _pid_alive(pid):
    if not IS_WINDOWS:
        return False
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _wait_install_processes(install_dir, timeout):
    """等待安装目录下的所有进程退出；返回是否在超时前清空。"""
    root = str(pathlib.Path(install_dir).resolve()).lower().rstrip("\\/")
    deadline = time.time() + timeout
    while True:
        remaining = []
        for pid, path in _iter_process_paths():
            if pid == os.getpid():
                continue
            try:
                image = str(pathlib.Path(path).resolve()).lower()
            except OSError:
                continue
            if image == root or image.startswith(root + os.sep):
                remaining.append(pid)
        if not remaining:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1.0)


def _start_app(install_dir, cwd):
    exe = pathlib.Path(install_dir) / "zhuomuniao.exe"
    if not exe.is_file():
        raise UpdateError("安装目录缺少 zhuomuniao.exe")
    start_cwd = pathlib.Path(cwd or install_dir)
    if not start_cwd.is_dir():
        start_cwd = pathlib.Path(install_dir)
    return subprocess.Popen(
        [str(exe)], cwd=str(start_cwd), close_fds=True)


def _message_box(text, title="啄木鸟更新"):
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, str(text), str(title), 0x10)
    except Exception:
        pass


def _backup_targets(install_dir, backup_dir, apply_parts):
    """备份待替换目标。

    exe 用硬链接（替代后旧内容仍由 backup 持有，且不会出现「无 exe」窗口）；
    目录直接 rename 到 backup（同卷，瞬时完成）。
    """
    install_dir = pathlib.Path(install_dir)
    backup_dir = pathlib.Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in apply_parts:
        for target in PART_TARGETS[name]:
            src = install_dir / target
            if not src.exists():
                continue
            dst = backup_dir / target
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                try:
                    os.link(str(src), str(dst))
                except OSError:
                    shutil.copy2(str(src), str(dst))
            else:
                _move(src, dst)


def _apply_targets(staging, install_dir, apply_parts):
    """把 staging 中的白名单目标移入安装目录；返回已应用目标列表。"""
    staging = pathlib.Path(staging)
    install_dir = pathlib.Path(install_dir)
    applied = []
    for name in apply_parts:
        for target in PART_TARGETS[name]:
            src = staging / target
            if not src.exists():
                raise UpdateError("staging 缺少 %s" % target)
            dst = install_dir / target
            if src.is_dir():
                # 目录不能原子替换：旧目录已 rename 到 backup，这里直接换入。
                if dst.exists():
                    _remove_path(dst)
                os.replace(str(src), str(dst))
            else:
                # 文件（exe）用 os.replace 原子覆盖，不出现「无 exe」窗口。
                os.replace(str(src), str(dst))
            applied.append(target)
    return applied


def _rollback(install_dir, backup_dir):
    """把 backup 中的所有目标恢复回安装目录（尽力而为）。"""
    install_dir = pathlib.Path(install_dir)
    backup_dir = pathlib.Path(backup_dir)
    if not backup_dir.is_dir():
        return
    for child in list(backup_dir.iterdir()):
        target = install_dir / child.name
        _remove_path(target)
        _move(child, target)
    try:
        backup_dir.rmdir()
    except OSError:
        pass


def _verify_install(install_dir, apply_parts):
    install_dir = pathlib.Path(install_dir)
    exe = install_dir / "zhuomuniao.exe"
    if not exe.is_file() or exe.stat().st_size <= 0:
        raise UpdateError("安装校验失败：zhuomuniao.exe 缺失")
    for name in apply_parts:
        for target in PART_TARGETS[name]:
            if not (install_dir / target).exists():
                raise UpdateError("安装校验失败：%s 缺失" % target)


def _cleanup_after_success(update_dir):
    update_dir = pathlib.Path(update_dir)
    for child in update_dir.glob("staging-*"):
        _remove_path(child)
    _remove_path(update_dir / "download")
    for name in (PENDING_NAME, JOURNAL_NAME):
        try:
            (update_dir / name).unlink()
        except OSError:
            pass
    # 保留最近 KEEP_BACKUPS 份备份（刚升级前的版本用于手动回滚）。
    backup_root = update_dir / "backup"
    if backup_root.is_dir():
        backups = sorted(
            [p for p in backup_root.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[update_config.KEEP_BACKUPS:]:
            _remove_path(old)


def _apply_update(pending, log):
    install_dir = pathlib.Path(pending["install_dir"])
    staging = pathlib.Path(pending["staging"])
    cwd = pending.get("cwd") or str(install_dir)
    update_dir = install_dir / UPDATE_DIR_NAME
    from_version = pending.get("from_version")
    to_version = pending.get("to_version")
    apply_parts = pending.get("apply_parts") or []
    log_path = pathlib.Path(pending.get("log") or (update_dir / LOG_NAME))

    log("开始更新：%s -> %s，分片=%s" %
        (from_version, to_version, apply_parts))
    if not install_dir.is_dir():
        raise UpdateError("安装目录不存在：%s" % install_dir)
    if apply_parts and not staging.is_dir():
        raise UpdateError("staging 不存在：%s" % staging)

    if not _wait_install_processes(install_dir, timeout=60):
        log("等待进程退出超时：安装目录下仍有进程")
        # 此时尚未改动任何文件，不自动再拉一个实例（可能有用户实例在跑）；
        # 提示用户关闭所有窗口后重新打开即可。
        _message_box("检测到仍有程序实例在运行，更新未执行（文件未改动）。\n"
                     "请关闭所有窗口后重新打开程序再试。")
        return 1

    backup_dir = update_dir / "backup" / (
        "%s-%d" % (from_version, int(time.time())))
    journal = {
        "schema": SCHEMA,
        "phase": "backup",
        "from_version": from_version,
        "to_version": to_version,
        "install_dir": str(install_dir),
        "backup": str(backup_dir),
        "apply_parts": apply_parts,
        "parts": pending.get("parts") or [],
        "main_pid": pending.get("main_pid"),
        "started_at": time.time(),
    }
    _write_json(update_dir / JOURNAL_NAME, journal)

    try:
        _backup_targets(install_dir, backup_dir, apply_parts)
        journal["phase"] = "apply"
        _write_json(update_dir / JOURNAL_NAME, journal)
        _apply_targets(staging, install_dir, apply_parts)
        journal["phase"] = "verify"
        _write_json(update_dir / JOURNAL_NAME, journal)
        _verify_install(install_dir, apply_parts)

        # 先写「成功」结果再启动：新版本加载面板时即可看到成功提示，
        # 不必等 20 秒健康检查结束。若健康检查失败，下面会覆盖为 rolled_back。
        _write_json(update_dir / LAST_RESULT_NAME, {
            "status": "success",
            "from": from_version,
            "to": to_version,
            "ts": time.time(),
        })
        journal["phase"] = "restart"
        _write_json(update_dir / JOURNAL_NAME, journal)
        proc = _start_app(install_dir, cwd)

        # 健康检查：新 exe 20 秒内以非 0 退出视为启动失败。
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            code = None
        if code not in (None, 0):
            raise UpdateError("新版本启动后立即退出（exit=%s）" % code)

        # 文件已替换且新版本已启动：后续元数据/清理失败不再回滚
        # （此时应用正在运行，回滚会破坏它）。installed.json 写入失败时
        # 保留 journal；下次启动 recover_if_needed 只清理，不影响使用
        # （下次更新按旧 installed 哈希重新下载，安全但多下一点）。
        try:
            _save_installed(update_dir, to_version, pending.get("parts") or [])
            journal["phase"] = "done"
            _write_json(update_dir / JOURNAL_NAME, journal)
            try:
                (update_dir / JOURNAL_NAME).unlink()
            except OSError:
                pass
            _cleanup_after_success(update_dir)
        except Exception:
            log("更新已完成，但收尾失败：\n" + traceback.format_exc())
        log("更新成功：%s -> %s" % (from_version, to_version))
        return 0
    except Exception as e:
        log(traceback.format_exc())
        # 新版本若已拉起子进程，先等它们退出，避免回滚时 DLL/exe 被占用
        try:
            _wait_install_processes(install_dir, timeout=15)
        except Exception:
            pass
        rolled_back = False
        try:
            _rollback(install_dir, backup_dir)
            rolled_back = True
            log("已回滚到 %s" % from_version)
        except Exception:
            log("回滚失败：\n" + traceback.format_exc())
        if rolled_back:
            # 回滚完成：清 journal，避免旧版本下次启动再走一次恢复
            try:
                (update_dir / JOURNAL_NAME).unlink()
            except OSError:
                pass
        _write_json(update_dir / LAST_RESULT_NAME, {
            "status": "rolled_back",
            "from": from_version,
            "to": to_version,
            "error": str(e),
            "ts": time.time(),
        })
        _restart_safe(install_dir, cwd, log)
        _message_box("更新失败，已回滚到 %s。\n%s\n详情：%s"
                     % (from_version, e, log_path))
        return 1


def _restart_safe(install_dir, cwd, log):
    try:
        _start_app(install_dir, cwd)
    except Exception:
        log("重启旧版本失败：\n" + traceback.format_exc())


def apply_update_entry(argv):
    """``--apply-update <pending.json>`` 助手入口；返回进程退出码。"""
    try:
        index = argv.index("--apply-update")
        pending_path = pathlib.Path(argv[index + 1])
    except (ValueError, IndexError):
        return 2
    log_path = pending_path.parent / LOG_NAME
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception as e:
        _append_log(log_path, "读取 pending.json 失败：%s" % e)
        _message_box("更新失败：无法读取更新任务。\n%s" % e)
        return 1

    def log(message):
        _append_log(log_path, message)

    try:
        return _apply_update(pending, log)
    except Exception as e:
        log(traceback.format_exc())
        _message_box("更新失败：%s\n详情：%s" % (e, log_path))
        return 1


def launch_apply_helper(pending_path):
    """把当前 exe 复制到 %TEMP% 并以 ``--apply-update`` 启动。

    不能直接启动安装目录的 exe：该进程会锁住目标 exe，导致无法替换。
    """
    if not is_frozen():
        raise UpdateError("源码运行不支持自动更新")
    exe = pathlib.Path(sys.executable)
    if not exe.is_file():
        raise UpdateError("无法定位当前 exe：%s" % exe)
    helper_dir = pathlib.Path(tempfile.mkdtemp(prefix="zhuomuniao-updater-"))
    helper = helper_dir / exe.name
    try:
        shutil.copy2(str(exe), str(helper))
    except Exception:
        shutil.rmtree(str(helper_dir), ignore_errors=True)
        raise
    # 关键：不能把 Nuitka onefile 的父进程提取目录变量传给副本。
    # 否则副本可能复用主进程的 %TEMP% 提取目录，主进程退出后目录被删，
    # 助手会在替换中途崩溃。移除后副本会自行解压到独立临时目录。
    env = os.environ.copy()
    env.pop("NUITKA_ONEFILE_PARENT", None)
    subprocess.Popen(
        [str(helper), "--apply-update", str(pending_path)],
        cwd=str(helper_dir),
        close_fds=True,
        creationflags=DETACHED_PROCESS,
        env=env,
    )
    return str(helper_dir)


# ==================== 启动恢复 / 结果回读 ====================

def _restore_backup(install_dir, backup_dir, skip_exe=True):
    """启动恢复：把 backup 中的目标搬回安装目录。

    ``skip_exe=True`` 用于「旧版本正在运行」的场景——运行中的 exe 无法替换，
    此时安装目录的 exe 已经就是旧版本，只恢复 res/pylibs 即可。
    """
    install_dir = pathlib.Path(install_dir)
    backup_dir = pathlib.Path(backup_dir)
    if not backup_dir.is_dir():
        return False
    for child in list(backup_dir.iterdir()):
        if skip_exe and child.name == "zhuomuniao.exe":
            continue
        target = install_dir / child.name
        _remove_path(target)
        _move(child, target)
    return True


def recover_if_needed(current_version, install_dir=None):
    """启动时处理中断的更新 journal（幂等，尽力而为）。

    - 当前版本 == to_version：更新实际已成功，补写 installed 记录并清 journal。
    - 当前版本 == from_version：旧版本正在运行，恢复 backup 中的 res/pylibs
      （exe 正在使用不替换），清 journal。
    - 其它：保留 journal 供排查。
    """
    root = pathlib.Path(install_dir) if install_dir else install_root()
    update_dir = root / UPDATE_DIR_NAME
    journal_path = update_dir / JOURNAL_NAME
    if not journal_path.exists():
        return None
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except Exception:
        _remove_path(journal_path)
        return None
    main_pid = journal.get("main_pid")
    if main_pid and _pid_alive(main_pid):
        return {"status": "in_progress"}
    to_version = journal.get("to_version")
    from_version = journal.get("from_version")
    if journal.get("phase") == "done" or current_version == to_version:
        # 新版本已正常运行：清 staging/download，保留最近备份。
        # 这里不补写 installed.json——写入只由 helper 在健康检查通过后做；
        # 缺失时下次更新按旧哈希重新下载，安全。
        try:
            _cleanup_after_success(update_dir)
        except Exception:
            pass
        _remove_path(journal_path)
        return {"status": "completed", "version": to_version}
    if current_version == from_version:
        backup = journal.get("backup")
        restored = False
        try:
            if backup:
                restored = _restore_backup(root, backup, skip_exe=True)
        except Exception:
            _append_log(update_dir / LOG_NAME,
                        "启动恢复失败：\n" + traceback.format_exc())
            return {"status": "restore_failed"}
        _remove_path(journal_path)
        return {"status": "rolled_back", "version": from_version,
                "restored": restored}
    return {"status": "unknown"}


def read_last_result(install_dir=None):
    root = pathlib.Path(install_dir) if install_dir else install_root()
    path = root / UPDATE_DIR_NAME / LAST_RESULT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clear_last_result(install_dir=None):
    root = pathlib.Path(install_dir) if install_dir else install_root()
    _remove_path(root / UPDATE_DIR_NAME / LAST_RESULT_NAME)
