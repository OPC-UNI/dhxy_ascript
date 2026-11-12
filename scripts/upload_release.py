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

清理 prefix/app 与 prefix/pylibs 下的旧包（只保留本次清单引用的对象 +
--prune-keep 个最近旧版，默认 1 = 上一版兜底），不上传任何东西：

    python scripts/upload_release.py \
      --endpoint ... --bucket ... --prefix ... \
      --manifest dist_update/update.json \
      --prune-only

清理要求 RAM 子账号对该前缀有 ListObjects / DeleteObject（见
docs/auto-update-design.md §4.2）；无权限时只打印警告并跳过，不影响发布。

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


def _prune_objects(oss2_mod, bucket, dir_prefix, keep_keys, keep_extra,
                   dry_run):
    """删除目录前缀下未被当前清单引用的旧对象。

    keep_keys 为本次 update.json 引用的 key（绝不删）；另按 LastModified
    保留最近的 keep_extra 个旧对象（默认 1 = 上一版），覆盖「客户端已拉到
    旧清单、下载尚未完成」的短暂竞态；0 表示只留本次引用的对象。

    只清理 prefix/app/ 与 prefix/pylibs/：update.json 与 manual/ 完整包
    不经此处。缺 ListObjects 权限时打印警告跳过，清理失败不应阻断发布。
    """
    keep = set(keep_keys)
    try:
        objects = list(oss2_mod.ObjectIterator(bucket, prefix=dir_prefix))
    except oss2_mod.exceptions.OssError as e:
        print("警告：列取 %s 失败，跳过清理（%s）。"
              "如需清理，给 RAM 子账号补该前缀的 ListObjects/DeleteObject。"
              % (dir_prefix, e))
        return
    others = [obj for obj in objects if obj.key not in keep]
    others.sort(key=lambda obj: obj.last_modified, reverse=True)
    survivors = others[:max(0, keep_extra)]
    doomed = others[max(0, keep_extra):]
    freed = sum(obj.size for obj in doomed) / 1024 / 1024
    print("清理 %s：共 %d 个对象 → 保留引用 %d 个 + 最近 %d 个，删除 %d 个"
          "（%.1f MB）"
          % (dir_prefix, len(objects), len(objects) - len(others),
             len(survivors), len(doomed), freed))
    for obj in survivors:
        print("  保留上一版：%s" % obj.key)
    if not doomed:
        return
    for obj in doomed[:10]:
        print("  删除：%s" % obj.key)
    if len(doomed) > 10:
        print("  ...其余 %d 个略" % (len(doomed) - 10))
    if dry_run:
        print("  [dry-run] 未实际删除")
        return
    # BatchDeleteObjects 单次上限 1000 个 key。
    for start in range(0, len(doomed), 1000):
        chunk = [obj.key for obj in doomed[start:start + 1000]]
        bucket.batch_delete_objects(chunk)


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
    parser.add_argument("--prune-only", action="store_true",
                        help="只清理 app/ 与 pylibs/ 下的旧包，不上传")
    parser.add_argument("--prune-keep", type=int, default=1,
                        help="清理时每个目录额外保留的最近旧对象数"
                             "（默认 1 = 上一版兜底；0 = 只留本次引用的）")
    args = parser.parse_args(argv)

    if not args.manifest_only and not args.prune_only:
        for name in ("app_zip", "pylibs_zip"):
            if not getattr(args, name):
                raise SystemExit(
                    "缺少 --%s（或使用 --manifest-only / --prune-only）"
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

    if args.prune_only:
        # 清理必须列取对象，dry-run 也需要凭证（只打印不删除）。
        oss2_mod, bucket = _bucket(args)
        for dir_prefix, keep_keys in (
                ("%s/app/" % prefix, [app_key]),
                ("%s/pylibs/" % prefix, [pylibs_key])):
            _prune_objects(oss2_mod, bucket, dir_prefix, keep_keys,
                           args.prune_keep, args.dry_run)
        print("清理完成：%s/app 与 %s/pylibs 仅保留本次清单引用的对象"
              " + 最近 %d 个旧对象" % (prefix, prefix, max(0, args.prune_keep)))
        return 0

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
