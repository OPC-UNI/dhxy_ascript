#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 / 校验自动更新清单 update.json（CI 与本地共用）。

用法（仓库根运行）：

    python scripts/make_update_manifest.py generate \
      --version 2.10.0 \
      --base-url https://dhxy-ascript.oss-cn-beijing.aliyuncs.com/zhuomuniao/stable \
      --app-zip dist_update/zhuomuniao-app-2.10.0.zip \
      --pylibs-zip dist_update/zhuomuniao-pylibs-1a2b3c4d.zip \
      --notes-file release_notes.md \
      --release-url https://github.com/OPC-UNI/dhxy_ascript/releases/tag/v2.10.0 \
      --out dist_update/update.json

    python scripts/make_update_manifest.py validate dist_update/update.json \
      --app-zip dist_update/zhuomuniao-app-2.10.0.zip \
      --pylibs-zip dist_update/zhuomuniao-pylibs-1a2b3c4d.zip

清单完全由 CI 生成，不手工维护；`validate` 在上传前再自检一次。
退出码 0 = 通过，非 0 = 失败。
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import sys
import zipfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import update_config  # noqa: E402
from src.updater import (UpdateError, parse_version,  # noqa: E402
                         validate_manifest)


def _sha256(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _file_info(path, url):
    path = pathlib.Path(path)
    if not path.is_file():
        raise SystemExit("文件不存在：%s" % path)
    return {
        "sha256": _sha256(path),
        "size": path.stat().st_size,
        "url": url,
    }


def _check_zip_layout(path, expected):
    """校验 zip 顶层条目与客户端白名单一致（防打包目录结构写错）。"""
    path = pathlib.Path(path)
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
    tops = {n.replace("\\", "/").split("/")[0] for n in names}
    if tops != set(expected):
        raise SystemExit(
            "更新包顶层目录不符合预期：%s，期望 %s（%s）"
            % (sorted(tops), sorted(expected), path))


def _read_notes(args):
    if args.notes_file:
        path = pathlib.Path(args.notes_file)
        if not path.is_file():
            raise SystemExit("更新日志文件不存在：%s" % path)
        return path.read_text(encoding="utf-8").strip()
    return (args.notes or "").strip()


def cmd_generate(args):
    version = args.version.strip().lstrip("v")
    if parse_version(version) is None:
        raise SystemExit("版本号格式不正确：%r" % args.version)
    base = args.base_url.rstrip("/")
    app_url = "%s/app/zhuomuniao-app-%s.zip" % (base, version)
    pylibs_sha = _sha256(args.pylibs_zip)
    pylibs_url = "%s/pylibs/zhuomuniao-pylibs-%s.zip" % (
        base, pylibs_sha[:8])
    _check_zip_layout(args.app_zip, ("zhuomuniao.exe", "res"))
    _check_zip_layout(args.pylibs_zip, ("pylibs",))
    app_info = _file_info(args.app_zip, app_url)
    pylibs_info = _file_info(args.pylibs_zip, pylibs_url)
    data = {
        "schema": 1,
        "app": update_config.APP_NAME,
        "channel": update_config.CHANNEL,
        "version": version,
        "min_supported_version": args.min_supported,
        "published_at": args.published_at,
        "release_url": args.release_url or "",
        "notes": _read_notes(args),
        "parts": [
            {
                "name": "app",
                "url": app_url,
                "sha256": app_info["sha256"],
                "size": app_info["size"],
                "targets": ["zhuomuniao.exe", "res"],
            },
            {
                "name": "pylibs",
                "url": pylibs_url,
                "sha256": pylibs_info["sha256"],
                "size": pylibs_info["size"],
                "targets": ["pylibs"],
            },
        ],
    }
    manifest = validate_manifest(data)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("update.json 已生成：%s" % out)
    print("  版本：%s（渠道 %s）" % (manifest["version"], manifest["channel"]))
    print("  app：%.1f MB  sha256=%s"
          % (app_info["size"] / 1024 / 1024, app_info["sha256"][:12]))
    print("  pylibs：%.1f MB  sha256=%s"
          % (pylibs_info["size"] / 1024 / 1024,
             pylibs_info["sha256"][:12]))
    return 0


def _verify_zip(part, zip_path):
    info = _file_info(zip_path, part["url"])
    if info["sha256"] != part["sha256"]:
        raise SystemExit("sha256 不一致：%s" % zip_path)
    if info["size"] != part["size"]:
        raise SystemExit("大小不一致：%s" % zip_path)
    print("  OK %s：%s" % (part["name"], zip_path))


def cmd_validate(args):
    path = pathlib.Path(args.manifest)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = validate_manifest(data)
    except (OSError, ValueError, UpdateError) as e:
        raise SystemExit("清单校验失败：%s" % e)
    print("清单校验通过：v%s（渠道 %s，%d 个分片，共 %.1f MB）"
          % (manifest["version"], manifest["channel"],
             len(manifest["parts"]),
             manifest["total_size"] / 1024 / 1024))
    print("  更新日志 %d 字" % len(manifest["notes"]))
    if args.app_zip:
        app_part = next(p for p in manifest["parts"] if p["name"] == "app")
        _verify_zip(app_part, args.app_zip)
    if args.pylibs_zip:
        pylibs_part = next(
            p for p in manifest["parts"] if p["name"] == "pylibs")
        _verify_zip(pylibs_part, args.pylibs_zip)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成/校验 update.json")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="生成清单")
    gen.add_argument("--version", required=True)
    gen.add_argument("--base-url", required=True,
                     help="例如 https://host/zhuomuniao/stable")
    gen.add_argument("--app-zip", required=True)
    gen.add_argument("--pylibs-zip", required=True)
    gen.add_argument("--notes-file", default="")
    gen.add_argument("--notes", default="")
    gen.add_argument("--release-url", default="")
    gen.add_argument("--min-supported", default="0.0.0")
    gen.add_argument(
        "--published-at",
        default=datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"))
    gen.add_argument("--out", required=True)
    gen.set_defaults(func=cmd_generate)

    val = sub.add_parser("validate", help="校验清单与 zip")
    val.add_argument("manifest")
    val.add_argument("--app-zip", default="")
    val.add_argument("--pylibs-zip", default="")
    val.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UpdateError as e:
        raise SystemExit("失败：%s" % e)


if __name__ == "__main__":
    sys.exit(main())
