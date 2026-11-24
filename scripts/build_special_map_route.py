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
        --trace map_trace/某某洞.jsonl --role 36096466 --res

路网格式：{"trace": [[x,y], ...], "trace_source": "..."}；不再需要
十字中心/臂节点（--keep-old 可保留旧的 center/arms 字段作为兜底）。
``--zones`` 额外写 ``zones``（每区独立 boundary/trace）与 ``links``
（传送触发点/落点）；若某落点 8 格内不足 3 个非转场期锚点样品，
该链接标 ``land_recalibrate: true``，运行期从落点起步改用名字/探针
重标定相机（波月洞 B→A 的 A 落点实测）。

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


def _load_trace_timed(path, map_name):
    """读带时间戳的轨迹点：[(x, y, t), ...]（用于识别传送转场窗口）。"""
    pts = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("kind") == "anchor":
            continue
        if rec.get("map") and rec["map"] != map_name:
            continue
        pt = (int(rec["x"]), int(rec["y"]), float(rec.get("t") or 0.0))
        if not pts or pts[-1][:2] != pt[:2]:
            pts.append(pt)
    return pts


def _load_anchors(path, map_name):
    """读锚点样品行：[[x,y,ax,ay,w,h,t], ...]（与轨迹同文件）。"""
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
                            int(rec["w"]), int(rec["h"]),
                            float(rec.get("t") or 0.0)])
        except (KeyError, TypeError, ValueError):
            continue
    return anchors


def _median(values):
    v = sorted(values)
    n = len(v)
    if n % 2:
        return v[n // 2]
    return (v[n // 2 - 1] + v[n // 2]) / 2.0


def _median_int(values):
    """整数中位数（偶数个取中间两值均值四舍五入）。"""
    v = sorted(values)
    n = len(v)
    if n == 0:
        return 0
    if n % 2:
        return int(v[n // 2])
    return int(round((v[n // 2 - 1] + v[n // 2]) / 2.0))


def _merge_anchors(anchors, near=6.0, tol=200.0):
    """剔除离群样品后同坐标取中位数（名字 OCR 偶尔命中同名前缀的别人）。

    正常锚点是世界坐标的平滑函数（相机跟随/边缘钳制，边缘最大梯度约
    21px/格），近邻 6 格内合法差异 < 130px，故用 200px 阈值只剔除离谱点；
    先对原始样品做近邻过滤，再合并同坐标，避免单个离群点污染合并结果。
    返回 (samples[[x,y,ax,ay]], window[w,h] 或 None)。
    """
    kept = []
    for i, a in enumerate(anchors):
        x, y, ax, ay, w, h = a[:6]
        if not (0 <= ax <= w and 0 <= ay <= h):
            # 相机跟踪跑飞（传送黑屏/重新标定失败）留下窗口外锚点：
            # 运行期命中会把点击点换算到屏幕外（波月洞 B 区实测），
            # 建图时直接丢弃。
            continue
        neigh = [(b[2], b[3]) for j, b in enumerate(anchors)
                 if j != i and b[4] == w and b[5] == h
                 and math.hypot(b[0] - x, b[1] - y) <= near]
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


# ---- 多分区路网（波月洞等：同一坐标平面上的 A/B 区，洞口传送连接）----
def _split_trace_segments(pts, jump):
    """按相邻点跳变切段：传送瞬间 HUD 坐标跳变远超步行采样间隔。"""
    segs = [[tuple(pts[0])]]
    for p in pts[1:]:
        p = tuple(p)
        prev = segs[-1][-1]
        if math.hypot(p[0] - prev[0], p[1] - prev[1]) >= jump:
            segs.append([p])
        else:
            segs[-1].append(p)
    return segs


def _group_trace_segments(segs, join):
    """空间相邻的轨迹段归为同一分区，返回 [[段索引, ...], ...]。

    传送两端（A 区触发点 → B 区落点）相距很远，而同一分区内被传送
    打断的若干段彼此贴近，按最小点距聚类即可区分分区。
    """
    n = len(segs)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    boxes = []
    for s in segs:
        xs = [p[0] for p in s]
        ys = [p[1] for p in s]
        boxes.append((min(xs), max(xs), min(ys), max(ys)))
    for i in range(n):
        for j in range(i + 1, n):
            bi, bj = boxes[i], boxes[j]
            if (bi[0] - bj[1] > join or bj[0] - bi[1] > join
                    or bi[2] - bj[3] > join or bj[2] - bi[3] > join):
                continue
            best = min(math.hypot(a[0] - b[0], a[1] - b[1])
                       for a in segs[i] for b in segs[j])
            if best < join:
                union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: min(g))


def _derive_zone_boundary(zone_segs, thickness=3, epsilon=1.5, pad=6):
    """分区轨迹 → 外轮廓边界多边形（段与段之间不连线，避免跨传送假边）。"""
    import cv2
    import numpy as np
    pts = [p for s in zone_segs for p in s]
    if len(pts) < 3:
        return []
    minx = min(p[0] for p in pts) - pad
    maxx = max(p[0] for p in pts) + pad
    miny = min(p[1] for p in pts) - pad
    maxy = max(p[1] for p in pts) + pad
    w, h = maxx - minx + 1, maxy - miny + 1
    mask = np.zeros((h, w), np.uint8)
    for s in zone_segs:
        for i in range(1, len(s)):
            x1, y1 = s[i - 1]
            x2, y2 = s[i]
            cv2.line(mask, (x1 - minx, maxy - y1),
                     (x2 - minx, maxy - y2), 255, thickness=thickness)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return []
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    poly = []
    for pt in approx:
        px, py = int(pt[0][0]), int(pt[0][1])
        poly.append([px + minx, maxy - py])
    out = []
    for p in poly:
        if (not out or abs(p[0] - out[-1][0])
                + abs(p[1] - out[-1][1]) >= 2.0):
            out.append(p)
    if len(out) >= 3 and out[0] == out[-1]:
        out.pop()
    return out


def _teleport_suspect_windows(timed, jump, settle=4.0):
    """轨迹跳变 → 锚点不可信时间窗 [跳变前一点, 落点后 settle 秒]。

    传送落地瞬间相机还在转场/贴地图边缘，此时相机跟踪记录的角色屏幕
    锚点与稳态不一致（波月洞 A 落点实测偏差约 150px）。
    """
    windows = []
    for i in range(1, len(timed)):
        if math.hypot(timed[i][0] - timed[i - 1][0],
                      timed[i][1] - timed[i - 1][1]) >= jump:
            windows.append((timed[i - 1][2], timed[i][2] + settle))
    return windows


def _landing_anchor_ok(land, anchors, suspect, radius=8.0, need=3):
    """传送落点附近是否有足够「非转场期」锚点样品可支撑锚点模型。

    落点 radius 格内至少有 need 个不在转场窗口内的有效样品，才认为
    运行期从该落点起步可直接信锚点模型；不足则链接标记
    ``land_recalibrate``，运行期用名字/探针重标定相机。
    """
    good = 0
    for a in anchors:
        if len(a) < 7:
            continue
        x, y, ax, ay, w, h, t = a[:7]
        if not (0 <= ax <= w and 0 <= ay <= h):
            continue
        if math.hypot(x - land[0], y - land[1]) > radius:
            continue
        if any(lo <= t <= hi for lo, hi in suspect):
            continue
        good += 1
    return good >= need


def _derive_zone_links(segs, seg_zone, names):
    """相邻轨迹段跨分区 → 传送链接（触发点=离开段末点，落点=进入段首点）。

    同一对分区的多次观测取中位数（触发点/落点固定，HUD 坐标有 ±1
    舍入）。同一有序分区对只保留一条链接；多洞口地图需要按触发点
    聚类时再扩展。
    """
    obs = {}
    for i in range(len(segs) - 1):
        a, b = seg_zone.get(i), seg_zone.get(i + 1)
        if a is None or b is None or a == b:
            continue
        key = (names[a], names[b])
        obs.setdefault(key, []).append((segs[i][-1], segs[i + 1][0]))
    links = []
    for (za, zb), vals in sorted(obs.items()):
        links.append({
            "from_zone": za,
            "from": [_median_int(v[0][0] for v in vals),
                     _median_int(v[0][1] for v in vals)],
            "to_zone": zb,
            "land": [_median_int(v[1][0] for v in vals),
                     _median_int(v[1][1] for v in vals)],
        })
    return links


def main(argv=None):
    ap = argparse.ArgumentParser(description="描边轨迹 → 特殊地图路网")
    ap.add_argument("--map", required=True, help="地图名（HUD 地名）")
    ap.add_argument("--trace", default="map_trace/special_map_trace.jsonl",
                    help="描边 JSONL（默认 map_trace/special_map_trace.jsonl）")
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
    ap.add_argument("--zones", action="store_true",
                    help="按传送跳变自动拆分多分区路网（波月洞 A/B 等）")
    ap.add_argument("--zone-jump", type=float, default=10.0,
                    help="相邻轨迹点跳变 ≥ 该格数视为传送（默认 10）")
    ap.add_argument("--zone-join", type=float, default=5.0,
                    help="轨迹段最小间距 < 该格数归为同一分区（默认 5）")
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

    # 锚点样品（分区落点可信度也要用；--no-self-anchor 只是不写模型）
    anchors = _load_anchors(trace_path, args.map)

    # 多分区（同一坐标平面上的 A/B 区，洞口传送连接；见 map_nav
    # _walk_special_zones）。zones 每区独立边界，links 记录传送触发点/
    # 落点；整图 boundary 保留作无分区路网的兜底/可视化。
    if args.zones:
        segs = _split_trace_segments(pts, args.zone_jump)
        groups = _group_trace_segments(segs, args.zone_join)
        if len(groups) < 2:
            print("多分区：未检测到传送跳变（单区轨迹），不写 zones")
        else:
            names = {gidx: chr(ord("A") + gidx)
                     for gidx in range(len(groups))}
            zones = []
            kept = {}
            for gidx, ids in enumerate(groups):
                zone_segs = [segs[i] for i in ids]
                boundary = _derive_zone_boundary(zone_segs)
                if len(boundary) < 3:
                    print(f"多分区：分区 {names[gidx]} 边界生成失败，跳过")
                    continue
                kept[gidx] = names[gidx]
                zones.append({
                    "name": names[gidx],
                    "boundary": boundary,
                    "trace": [list(p) for s in zone_segs for p in s],
                })
                print(f"多分区 {names[gidx]}：{len(ids)} 段 / "
                      f"{sum(len(s) for s in zone_segs)} 点，边界 "
                      f"{len(boundary)} 点")
            seg_zone = {}
            for gidx, ids in enumerate(groups):
                if gidx in kept:
                    for i in ids:
                        seg_zone[i] = gidx
            if len(zones) >= 2:
                links = _derive_zone_links(segs, seg_zone, kept)
                timed = _load_trace_timed(trace_path, args.map)
                suspect = _teleport_suspect_windows(timed, args.zone_jump)
                for link in links:
                    if not _landing_anchor_ok(link["land"], anchors,
                                             suspect):
                        # 落点附近没有稳态锚点（样品全是转场期采集的）：
                        # 运行期从该落点起步必须名字/探针重标定相机
                        link["land_recalibrate"] = True
                route["zones"] = zones
                route["links"] = links
                for link in links:
                    extra = ("（落点需重标定）"
                             if link.get("land_recalibrate") else "")
                    print(f"传送 {link['from_zone']} {link['from']} → "
                          f"{link['to_zone']} {link['land']}{extra}")
                print(f"多分区路网：{len(zones)} 区 / {len(links)} 条传送")
            else:
                print("多分区：有效分区不足 2 个，不写 zones")

    # 角色屏幕锚点模型（HUD 坐标 → 角色屏幕位置；运行期免 OCR/探针）
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
