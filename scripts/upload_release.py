#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把更新资产上传到阿里云 OSS（GitHub Actions 发布步骤调用）。

上传顺序：app zip → pylibs zip（内容寻址，已存在则跳过）→
最后 update.json。清单最后写入，保证「看到清单时资产已就绪」。

manual 完整包**不上传 OSS**：仅首个带自动更新的版本（v2.10.0）需要
一份手工安装包（线上已留存 manual/zhuomuniao-windows-latest.zip，
新用户装它后走面板升级）；后续版本用户面板内升级即可，省存储成本。
版本命名 zip 仍由 GitHub Release 归档（workflow 附产物）。

用法：

    set OSS_ACCESS_KEY_ID=...
    set OSS_ACCESS_KEY_SECRET=...
    python scripts/upload_release.py \
      --endpoint oss-cn-beijing.aliyuncs.com \
      --bucket dhxy-ascript-new \
      --prefix zhuomuniao/stable \
      --manifest dist_update/update.json \
      --app-zip dist_update/zhuomuniao-app-2.10.0.zip \
      --pylibs-zip dist_update/zhuomuniao-pylibs-1a2b3c4d.zip

只修正线上清单（如更新日志文案错误）时用 --manifest-only，不动任何 zip：

    python scripts/upload_release.py \
      --endpoint ... --bucket ... --prefix ... \
      --manifest dist_update/update.json \
      --manifest-only

依赖：`pip install oss2`（仅 CI/发布机需要，不进运行依赖）。
"""
import argparse
import json
import os
import pathlib
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _bucket(args):
    try:
        import oss2
    except ImportError:
        raise SystemExit("缺少 oss2：请先 pip install oss2")
    key_id = args.access_key_id or os.environ.get("OSS_ACCESS_KEY_ID", "")
    key_secret = (args.access_key_secret
                  or os.environ.get("OSS_ACCESS_KEY_SECRET", ""))
    if not key_id or not key_secret:
        raise SystemExit("缺少 OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET")
    endpoint = args.endpoint
    if not endpoint.startswith(("http://", "https://")):
        endpoint = "https://" + endpoint
    auth = oss2.Auth(key_id, key_secret)
    return oss2, oss2.Bucket(auth, endpoint, args.bucket)


def _put(bucket, key, path, content_type, cache_control, dry_run):
    path = pathlib.Path(path)
    if not path.is_file():
        raise SystemExit("文件不存在：%s" % path)
    headers = {
        "Content-Type": content_type,
        "Cache-Control": cache_control,
    }
    print("上传 %s -> %s（%.1f MB）"
          % (path.name, key, path.stat().st_size / 1024 / 1024))
    if dry_run:
        return
    # PutObject 单次上限 5GB，发布包远小于此，无需分片上传。
    bucket.put_object_from_file(key, str(path), headers=headers)


def main(argv=None):
    parser = argparse.ArgumentParser(description="上传更新资产到阿里云 OSS")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="zhuomuniao/stable")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--app-zip", default="")
    parser.add_argument("--pylibs-zip", default="")
    parser.add_argument("--access-key-id", default="")
    parser.add_argument("--access-key-secret", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest-only", action="store_true",
                        help="只上传 update.json（修正清单文案用，zip 不动）")
    args = parser.parse_args(argv)

    if not args.manifest_only:
        for name in ("app_zip", "pylibs_zip"):
            if not getattr(args, name):
                raise SystemExit("缺少 --%s（或使用 --manifest-only）"
                                 % name.replace("_", "-"))

    manifest_path = pathlib.Path(args.manifest)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = manifest["version"]
    parts = {part["name"]: part for part in manifest.get("parts", [])}
    if "app" not in parts or "pylibs" not in parts:
        raise SystemExit("清单缺少 app / pylibs 分片")

    prefix = args.prefix.strip("/")
    app_key = "%s/app/zhuomuniao-app-%s.zip" % (prefix, version)
    pylibs_key = "%s/pylibs/zhuomuniao-pylibs-%s.zip" % (
        prefix, parts["pylibs"]["sha256"][:8])
    manifest_key = "%s/update.json" % prefix

    if args.dry_run:
        bucket = None  # 本地演练不需要 oss2 / 密钥
    else:
        _, bucket = _bucket(args)
    immutable = "public, max-age=31536000, immutable"
    no_cache = "no-cache, max-age=60"

    endpoint_host = args.endpoint
    for scheme in ("https://", "http://"):
        if endpoint_host.startswith(scheme):
            endpoint_host = endpoint_host[len(scheme):]
    endpoint_host = endpoint_host.strip("/")
    base = "https://%s.%s/%s" % (args.bucket, endpoint_host, prefix)

    if args.manifest_only:
        _put(bucket, manifest_key, manifest_path,
             "application/json", no_cache, args.dry_run)
        print("清单已更新：%s/update.json（v%s）" % (base, version))
        return 0

    _put(bucket, app_key, args.app_zip,
         "application/zip", immutable, args.dry_run)

    if args.dry_run:
        exists = False
    else:
        exists = bucket.object_exists(pylibs_key)
    if exists:
        print("pylibs 已存在，跳过上传：%s" % pylibs_key)
    else:
        _put(bucket, pylibs_key, args.pylibs_zip,
             "application/zip", immutable, args.dry_run)

    # 清单最后上传：此时所有 zip 已就绪。
    _put(bucket, manifest_key, manifest_path,
         "application/json", no_cache, args.dry_run)

    print("发布完成：%s/update.json（v%s）" % (base, version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
