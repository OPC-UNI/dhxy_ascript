#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性 zip 打包（CI 更新包用）。

`Compress-Archive` 会把文件时间戳写进 zip，依赖内容没变时每次构建的
pylibs 哈希仍不同，内容寻址就失效了（每次都要重下/重传 ~100MB）。
本脚本固定条目顺序、时间戳、权限与 create_system，保证同样的目录内容
生成字节级相同的 zip。

用法：
    python scripts/make_zip.py --source pylibs_pkg --output dist_update/zhuomuniao-pylibs.zip
"""
import argparse
import pathlib
import sys
import zipfile

# ZIP 允许的最早时间（1980-01-01），避免读取文件 mtime
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def _iter_files(source):
    source = pathlib.Path(source)
    for path in sorted(source.rglob("*")):
        if path.is_file():
            yield path.relative_to(source).as_posix(), path


def make_zip(source, output, compresslevel=9):
    source = pathlib.Path(source)
    output = pathlib.Path(output)
    if not source.is_dir():
        raise SystemExit("源目录不存在：%s" % source)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=compresslevel) as zf:
        for rel, path in _iter_files(source):
            info = zipfile.ZipInfo(rel, _FIXED_DATE)
            info.create_system = 0          # MS-DOS，避免平台差异
            info.compress_type = zipfile.ZIP_DEFLATED
            info.internal_attr = 0
            info.external_attr = 0o600 << 16  # 普通文件
            info.flag_bits = 0
            zf.writestr(info, path.read_bytes())
            count += 1
    print("已打包 %d 个文件：%s（%.1f MB）"
          % (count, output, output.stat().st_size / 1024 / 1024))
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description="确定性 zip 打包")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--compresslevel", type=int, default=9)
    args = parser.parse_args(argv)
    make_zip(args.source, args.output, args.compresslevel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
