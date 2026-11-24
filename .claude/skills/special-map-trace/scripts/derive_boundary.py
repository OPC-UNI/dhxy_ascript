# -*- coding: utf-8 -*-
"""从一遍全图轨迹（JSONL）提取描边边界多边形。

用户 2026-11-24：只走了一遍全图（没有单独跑外轮廓）时，把轨迹栅格化
（相邻采样点连直线、线宽可调、轻微闭运算）→ 取最大外轮廓 → 多边形
简化，得到「描边以内可走」的边界。单独走一圈外轮廓仍然更准；本脚本
只是省一次录制。输出 JSONL（与 trace 同格式，可直接喂
build_special_map_route.py --boundary-trace），并可选画核对图。

用法::

    python .claude/skills/special-map-trace/scripts/derive_boundary.py \\
        --map 波月洞 --trace map_trace/波月洞.jsonl --out map_trace/波月洞_boundary.jsonl
"""
import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import cv2
import numpy as np


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "device" / "map.py").exists():
            return p
    raise SystemExit("未找到仓库根（向上找不到 src/device/map.py）")


REPO = _repo_root()


def _load_points(path, map_name):
    pts = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("kind") == "anchor":
            continue
        if rec.get("map") and rec["map"] != map_name:
            continue
        pt = [int(rec["x"]), int(rec["y"])]
        if not pts or pts[-1] != pt:
            pts.append(pt)
    return pts


def _simplify(poly, min_dist=2.0):
    out = []
    for p in poly:
        if not out or (abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1])
                       >= min_dist):
            out.append(p)
    if len(out) >= 3 and out[0] == out[-1]:
        out.pop()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="全图轨迹 → 描边边界多边形")
    ap.add_argument("--map", required=True)
    ap.add_argument("--trace", default="map_trace/special_map_trace.jsonl")
    ap.add_argument("--out", default=None)
    ap.add_argument("--thickness", type=int, default=3,
                    help="轨迹线宽（格，默认 3；越大边界越外扩）")
    ap.add_argument("--epsilon", type=float, default=1.5,
                    help="多边形简化容差（格，默认 1.5）")
    ap.add_argument("--pad", type=int, default=6, help="画布留边（格）")
    args = ap.parse_args(argv)

    trace = Path(args.trace)
    if not trace.is_absolute():
        trace = REPO / trace
    pts = _load_points(trace, args.map)
    if len(pts) < 3:
        print(f"轨迹点不足（{len(pts)}）：{trace}")
        return 2
    minx = min(p[0] for p in pts) - args.pad
    maxx = max(p[0] for p in pts) + args.pad
    miny = min(p[1] for p in pts) - args.pad
    maxy = max(p[1] for p in pts) + args.pad
    w, h = maxx - minx + 1, maxy - miny + 1
    mask = np.zeros((h, w), np.uint8)
    for i in range(1, len(pts)):
        x1, y1 = pts[i - 1]
        x2, y2 = pts[i]
        # 世界 y 向上、图像 y 向下 → 行坐标翻转
        cv2.line(mask, (x1 - minx, maxy - y1), (x2 - minx, maxy - y2),
                 255, thickness=args.thickness)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        print("未找到外轮廓（轨迹为空？）")
        return 2
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, args.epsilon, True)
    poly = []
    for pt in approx:
        px, py = int(pt[0][0]), int(pt[0][1])
        poly.append([px + minx, maxy - py])
    poly = _simplify(poly)
    if len(poly) < 3:
        print(f"边界多边形点不足（{len(poly)}）")
        return 2

    out = (Path(args.out) if args.out
           else REPO / "map_trace" / f"{args.map}_boundary.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for x, y in poly:
            f.write(json.dumps({"map": args.map, "x": int(x), "y": int(y)},
                               ensure_ascii=False) + "\n")
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    print(f"边界已生成: {out}  点数 {len(poly)}  "
          f"包围盒 [{min(xs)},{min(ys)}]-[{max(xs)},{max(ys)}]")

    # 核对图：轨迹(灰) + 边界(红)
    try:
        from PIL import Image, ImageDraw, ImageFont
        spanx = max(1, max(xs) - min(xs))
        spany = max(1, max(ys) - min(ys))
        margin, max_w, max_h = 70, 1100, 820
        scale = max(2.0, min((max_w - 2 * margin) / spanx,
                             (max_h - 2 * margin) / spany))
        W = int(spanx * scale + 2 * margin)
        H = int(spany * scale + 2 * margin)
        img = Image.new("RGB", (W, H), (250, 250, 248))
        d = ImageDraw.Draw(img)
        font = ImageFont.load_default()
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
        except Exception:
            pass

        def px(x):
            return margin + (x - min(xs)) * scale

        def py(y):
            return H - margin - (y - min(ys)) * scale

        for i in range(1, len(pts)):
            d.line([(px(pts[i - 1][0]), py(pts[i - 1][1])),
                    (px(pts[i][0]), py(pts[i][1]))],
                   fill=(170, 170, 175), width=2)
        for i in range(len(poly)):
            a = poly[i]
            b = poly[(i + 1) % len(poly)]
            d.line([(px(a[0]), py(a[1])), (px(b[0]), py(b[1]))],
                   fill=(210, 60, 60), width=3)
        d.text((margin, 16),
               f"{args.map} 轨迹外轮廓边界  边界点 {len(poly)}  "
               f"轨迹点 {len(pts)}", fill=(30, 30, 30), font=font)
        png = out.with_name(out.stem + "_check.png")
        img.save(png)
        print(f"核对图: {png}")
    except Exception as exc:  # noqa: BLE001 —— 画图失败不影响边界文件
        print(f"核对图生成失败（忽略）: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
