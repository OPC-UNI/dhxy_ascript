# -*- coding: utf-8 -*-
"""把人工描边轨迹画成地图 PNG（坐标网格 + 轨迹折线 + 起终点标记）。

输入二选一：
- --trace  logs/xxx.jsonl   （scripts/trace_special_map.py 的输出）
- --route-json             （special_map_routes.json，取 maps[--map].trace）

输出默认 logs/special_map_<map>.png。只读取，不操作游戏窗口。
"""
import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw, ImageFont


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "device" / "map.py").exists():
            return p
    raise SystemExit("未找到仓库根（向上找不到 src/device/map.py）")


REPO = _repo_root()


def _load_points(args):
    pts = []
    if args.route_json:
        data = json.loads(Path(args.route_json).read_text(encoding="utf-8"))
        route = (data.get("maps") or {}).get(args.map) or {}
        raw = route.get("trace") or []
        for p in raw:
            if isinstance(p, (list, tuple)) and len(p) == 2:
                pt = [int(p[0]), int(p[1])]
                if not pts or pts[-1] != pt:
                    pts.append(pt)
        return pts, args.route_json
    path = Path(args.trace)
    if not path.is_absolute():
        path = REPO / path
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("map") and rec["map"] != args.map:
            continue
        pt = [int(rec["x"]), int(rec["y"])]
        if not pts or pts[-1] != pt:
            pts.append(pt)
    return pts, str(path)


def _font(size):
    """尽量找中文字体；找不到退回默认字体。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except Exception:
                continue
    return ImageFont.load_default()


def _nice_step(span):
    """网格间距：约 10~16 格一格，取整到 1/2/5/10。"""
    for step in (5, 10, 20, 25, 50):
        if span / step <= 16:
            return step
    return 50


def main(argv=None):
    ap = argparse.ArgumentParser(description="描边轨迹 → 地图 PNG")
    ap.add_argument("--map", required=True)
    ap.add_argument("--trace", default="logs/special_map_trace.jsonl")
    ap.add_argument("--route-json", default=None,
                    help="从 special_map_routes.json 取 trace（优先于 --trace）")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    pts, src = _load_points(args)
    if len(pts) < 2:
        print(f"描边点不足（{len(pts)}）：{src}")
        return 2
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    spanx = max(1, maxx - minx)
    spany = max(1, maxy - miny)
    margin = 70
    max_w, max_h = 1200, 820
    scale = min((max_w - 2 * margin) / spanx, (max_h - 2 * margin) / spany)
    scale = max(scale, 2.0)
    w = int(spanx * scale + 2 * margin)
    h = int(spany * scale + 2 * margin)
    img = Image.new("RGB", (w, h), (250, 250, 248))
    d = ImageDraw.Draw(img)
    font = _font(15)
    title_font = _font(22)

    def px(x):
        return margin + (x - minx) * scale

    def py(y):
        # 世界 y 向上；图像 y 向下
        return h - margin - (y - miny) * scale

    step = _nice_step(max(spanx, spany))
    gx = (minx // step) * step
    while gx <= maxx + step:
        X = px(gx)
        d.line([(X, margin), (X, h - margin)], fill=(226, 226, 222), width=1)
        d.text((X - 8, h - margin + 6), str(gx), fill=(90, 90, 90), font=font)
        gx += step
    gy = (miny // step) * step
    while gy <= maxy + step:
        Y = py(gy)
        d.line([(margin, Y), (w - margin, Y)], fill=(226, 226, 222), width=1)
        d.text((6, Y - 8), str(gy), fill=(90, 90, 90), font=font)
        gy += step

    # 轨迹折线
    line_pts = [(px(x), py(y)) for x, y in pts]
    if len(line_pts) >= 2:
        d.line(line_pts, fill=(40, 110, 220), width=3, joint="curve")
    for x, y in line_pts:
        d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(30, 80, 180))

    # 起点/终点
    sx, sy = line_pts[0]
    ex, ey = line_pts[-1]
    d.ellipse([sx - 7, sy - 7, sx + 7, sy + 7], fill=(40, 170, 80),
              outline=(20, 90, 40), width=2)
    d.ellipse([ex - 7, ey - 7, ex + 7, ey + 7], fill=(220, 70, 60),
              outline=(120, 30, 30), width=2)
    d.text((sx + 10, sy - 10), "起点", fill=(20, 110, 50), font=font)
    d.text((ex + 10, ey - 10), "终点", fill=(150, 40, 30), font=font)

    d.text((margin, 18),
           f"{args.map} 描边地图   点数 {len(pts)}   "
           f"包围盒 [{minx},{miny}]-[{maxx},{maxy}]",
           fill=(30, 30, 30), font=title_font)

    out = Path(args.out) if args.out else REPO / "logs" / f"special_map_{args.map}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print(f"地图已生成: {out}  size={img.size}  点={len(pts)}  "
          f"bbox=[{minx},{miny}]-[{maxx},{maxy}]  scale={scale:.2f}px/格")
    return 0


if __name__ == "__main__":
    sys.exit(main())
