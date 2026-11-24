# -*- coding: utf-8 -*-
"""把人工描边轨迹（JSONL）转换成特殊地图路网（trace）。

用户 2026-11-23 规则：异形地图（莲花洞等）用人工描边最可靠——人走过的
点一定是可走点；导航沿描边折线走，不会把空洞坐标当成路点。本脚本把
``scripts/trace_special_map.py`` 的记录转成路网数据：

- 角色数据：``data/<角色>/special_map_routes.json``（优先使用）
- 可选内置表：``res/special_map_routes.json``（--res，随包分发）

用法（仓库根目录，项目 venv）::

    python scripts/build_special_map_route.py --map 莲花洞
    python scripts/build_special_map_route.py --map 莲花洞 --res
    python scripts/build_special_map_route.py --map 某某洞 \\
        --trace logs/某某洞.jsonl --role 36096466 --res

路网格式：{"trace": [[x,y], ...], "trace_source": "..."}；不再需要
十字中心/臂节点（--keep-old 可保留旧的 center/arms 字段作为兜底）。

描边记录里带 "kind":"anchor" 的行是角色屏幕锚点样品（HUD 坐标 + 角色
名字标签屏幕位置，见 scripts/trace_special_map.py）；本脚本把它们清洗
后写成路网 self_anchor：{"window": [w,h], "samples": [[x,y,ax,ay],...]}，
导航时用 HUD 坐标就近查表得到角色屏幕位置，运行期不再依赖名字 OCR/探针。
"""
import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "device" / "map.py").exists():
            return p
    raise SystemExit("未找到仓库根（向上找不到 src/device/map.py）")


REPO = _repo_root()
sys.path.insert(0, str(REPO))


def _load_trace(path, map_name):
    pts = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("kind") == "anchor":   # 锚点样品行不是轨迹点
            continue
        if rec.get("map") and rec["map"] != map_name:
            continue
        pt = [int(rec["x"]), int(rec["y"])]
        if not pts or pts[-1] != pt:
            pts.append(pt)
    return pts


def _load_anchors(path, map_name):
    """读锚点样品行：[[x,y,ax,ay,w,h], ...]（与轨迹同文件）。"""
    anchors = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") != "anchor":
            continue
        if rec.get("map") and rec["map"] != map_name:
            continue
        try:
            anchors.append([int(rec["x"]), int(rec["y"]),
                            int(round(float(rec["ax"]))),
                            int(round(float(rec["ay"]))),
                            int(rec["w"]), int(rec["h"])])
        except (KeyError, TypeError, ValueError):
            continue
    return anchors


def _median(values):
    v = sorted(values)
    n = len(v)
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) / 2.0


def _merge_anchors(anchors, near=6.0, tol=200.0):
    """剔除离群样品后同坐标取中位数（名字 OCR 偶尔命中同名前缀的别人）。

    正常锚点是世界坐标的平滑函数（相机跟随/边缘钳制，边缘最大梯度约
    21px/格），近邻 6 格内合法差异 < 130px，故用 200px 阈值只剔除离谱点；
    先对原始样品做近邻过滤，再合并同坐标，避免单个离群点污染合并结果。
    返回 (samples[[x,y,ax,ay]], window[w,h] 或 None)。
    """
    kept = []
    for i, (x, y, ax, ay, w, h) in enumerate(anchors):
        neigh = [(a[2], a[3]) for j, a in enumerate(anchors)
                 if j != i and a[4] == w and a[5] == h
                 and math.hypot(a[0] - x, a[1] - y) <= near]
        if len(neigh) >= 3:
            if (abs(ax - _median(p[0] for p in neigh)) > tol
                    or abs(ay - _median(p[1] for p in neigh)) > tol):
                continue   # 明显离群（多半是名字误识别）
        kept.append((x, y, ax, ay, w, h))
    groups = {}
    for x, y, ax, ay, w, h in kept:
        groups.setdefault((x, y, w, h), []).append((ax, ay))
    samples = []
    for (x, y, w, h), pts in groups.items():
        samples.append([x, y, int(round(_median(p[0] for p in pts))),
                        int(round(_median(p[1] for p in pts)))])
    if not samples:
        return [], None
    window = Counter((a[4], a[5]) for a in kept).most_common(1)[0][0]
    return samples, [int(window[0]), int(window[1])]


def main(argv=None):
    ap = argparse.ArgumentParser(description="描边轨迹 → 特殊地图路网")
    ap.add_argument("--map", required=True, help="地图名（HUD 地名）")
    ap.add_argument("--trace", default="logs/special_map_trace.jsonl",
                    help="描边 JSONL（默认 logs/special_map_trace.jsonl）")
    ap.add_argument("--role", default=None,
                    help="角色 ID（默认按当前绑定角色解析）")
    ap.add_argument("--res", action="store_true",
                    help="同时写入内置表 res/special_map_routes.json")
    ap.add_argument("--keep-old", action="store_true",
                    help="保留旧 center/arms 字段（作为无描边时的兜底）")
    ap.add_argument("--boundary-trace", default=None,
                    help="边界描边 JSONL（描边外不可走；默认用 --trace 本身做边界）")
    ap.add_argument("--no-boundary", action="store_true",
                    help="不写 boundary 边界（旧模式：仅按描边折线走）")
    ap.add_argument("--no-self-anchor", action="store_true",
                    help="不写 self_anchor 角色屏幕锚点模型")
    args = ap.parse_args(argv)

    trace_path = Path(args.trace)
    if not trace_path.is_absolute():
        trace_path = REPO / trace_path
    pts = _load_trace(trace_path, args.map)
    if len(pts) < 2:
        print(f"轨迹点数不足（{len(pts)}）：{trace_path}")
        return 2
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    print(f"描边：{len(pts)} 点，包围盒 [{min(xs)},{min(ys)}] - "
          f"[{max(xs)},{max(ys)}]")

    from src.core import user_data

    old = {}
    try:
        data = user_data.read_special_map_routes(role=args.role)
        old = ((data.get("maps") or {}).get(args.map) or {})
    except Exception:
        old = {}
    route = dict(old) if args.keep_old else {}
    route["trace"] = pts
    route["trace_source"] = f"manual {trace_path.name}"
    if not args.no_boundary:
        boundary = pts
        if args.boundary_trace:
            bpath = Path(args.boundary_trace)
            if not bpath.is_absolute():
                bpath = REPO / bpath
            boundary = _load_trace(bpath, args.map)
            if len(boundary) < 3:
                print(f"边界描边点不足（{len(boundary)}）：{bpath}")
                return 2
            print(f"边界：{len(boundary)} 点（{bpath.name}）")
        route["boundary"] = boundary
        print(f"边界多边形：{len(boundary)} 点（内部可走，外部不可走）")

    # 角色屏幕锚点模型（HUD 坐标 → 角色屏幕位置；运行期免 OCR/探针）
    anchors = [] if args.no_self_anchor else _load_anchors(trace_path, args.map)
    if args.no_self_anchor:
        print("角色屏幕锚点模型：跳过（--no-self-anchor）")
    else:
        samples, window = _merge_anchors(anchors)
        if anchors:
            print(f"锚点样品：{len(anchors)} 个 → 清洗后 {len(samples)} 个"
                  + (f"，窗口 {window[0]}x{window[1]}" if window else ""))
        else:
            print("锚点样品：无（记录时 --anchor-interval 0 "
                  "或名字 OCR 未命中）")
        if samples:
            route["self_anchor"] = {
                "window": window,
                "samples": samples,
                "source": f"manual {trace_path.name}",
            }

    # 角色数据
    data = user_data.read_special_map_routes(role=args.role)
    maps = data.get("maps") if isinstance(data.get("maps"), dict) else {}
    maps = dict(maps)
    maps[args.map] = route
    user_data.write_special_map_routes({"version": 1, "maps": maps},
                                       role=args.role)
    print(f"角色数据已写入: {user_data.special_map_routes_path(args.role)}")

    if args.res:
        res_path = REPO / "res" / "special_map_routes.json"
        res = {}
        if res_path.exists():
            try:
                res = json.loads(res_path.read_text(encoding="utf-8"))
            except Exception:
                res = {}
        maps = res.get("maps") if isinstance(res.get("maps"), dict) else {}
        maps = dict(maps)
        maps[args.map] = route
        res = {"version": 1, "maps": maps}
        res_path.write_text(
            json.dumps(res, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"内置表已写入: {res_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
