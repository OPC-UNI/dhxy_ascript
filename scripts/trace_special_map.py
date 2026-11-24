# -*- coding: utf-8 -*-
"""人工描边记录器：按固定频率读 HUD 坐标，记录玩家自己操作走出的路线。

用途（用户 2026-11-23 提议）：特殊地图（莲花洞等）异形、不能按图点击，
由人手动操作人物沿可走路线/地图描边，脚本只读坐标不点鼠标，记录真实
可走轨迹，事后据此建立该图的路网/坐标系，识别比自动探图更可靠。

同时（--anchor-interval > 0，默认开启）记录「HUD 世界坐标 → 角色屏幕
位置」锚点样品：先用 2x 放大 OCR 在游戏画面中部找到一次角色名字标签
标定相机 B，之后每帧用相位相关测相机位移并更新 B（与导航同一算法），
锚点 = (HUD 坐标 - B)/M；每 ``--reanchor-interval`` 秒在预测位置附近
小框重标定一次校正漂移（小框连续失败则整片重扫）。建图脚本把它
写进路网 ``self_anchor``，导航时用 HUD 坐标就近查表即可得到角色屏幕位置
——运行期不再依赖名字 OCR（人多读不到名字）/探针，点击始终落在角色附近。

运行（仓库根目录，项目 venv）::

    python scripts/trace_special_map.py --map 莲花洞 --interval 0.4

- 不抢前台、不移动鼠标/键盘（截图 force_activate=False，仅 OCR HUD/名字）。
- 每记录到一个新坐标追加一行 JSON 到 --out（默认
  logs/special_map_trace.jsonl），中途停止也不丢数据。
- 锚点样品行带 ``"kind": "anchor"`` 与 ``ax/ay/src/w/h``，位置行无 kind。
- 停止：Ctrl+C、达到 --duration 秒，或创建停止文件
  logs/special_map_trace.stop。
- 结束后同目录输出 summary（start/end/点数/包围盒/总里程/锚点样品数）。

注意：本脚本只读取，操作人物完全由用户完成。
"""
import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

# 控制台 GBK：中文输出统一 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _repo_root() -> Path:
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "device" / "map.py").exists():
            return p
    raise SystemExit("未找到仓库根（向上找不到 src/device/map.py）")


REPO = _repo_root()
sys.path.insert(0, str(REPO))

from ascript.windows.system import R  # noqa: E402

# R 默认按脚本目录解析 res/，必须改回仓库根（否则 OCR 模型/模板找不到）
R._BASE_DIR = REPO

from src.device import game  # noqa: E402
from src.core import app_utils  # noqa: E402
from src.device import map as game_map  # noqa: E402
from src.device import map_nav  # noqa: E402


def _role_name(win):
    """窗口标题「角色名」（ID：...）→ 完整角色名（OCR 模糊匹配用）。"""
    m = re.search(r"「([^」]+)」（ID：", getattr(win, "title", "") or "")
    return m.group(1) if m else None


# 重标定用的预测框：锚点在名字框中心/上沿附近，追踪漂移与标签框差异
# （含不含等级前缀）都在几十 px 内；偏离预测太远视为别人同名标签。
REANCHOR_BOX_X = 160
REANCHOR_BOX_TOP = 50
REANCHOR_BOX_BOTTOM = 70
REANCHOR_MAX_SHIFT = 200      # OCR 锚点偏离预测超过该值丢弃（px）
REANCHOR_FULL_AFTER = 3       # 连续小框失败 → 全画面重扫


def _predicted_anchor(x, y, B):
    return ((x - B[0]) / map_nav.SPECIAL_WALK_SX,
            (y - B[1]) / map_nav.SPECIAL_WALK_SY)


def _reanchor_box(ax, ay):
    l, t, r, b = map_nav.SPECIAL_NAME_REGION
    return (max(l, int(ax) - REANCHOR_BOX_X),
            max(t, int(ay) - REANCHOR_BOX_TOP),
            min(r, int(ax) + REANCHOR_BOX_X),
            min(b, int(ay) + REANCHOR_BOX_BOTTOM))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="人工描边记录游戏 HUD 坐标")
    ap.add_argument("--map", default="莲花洞", help="目标地图名（HUD 地名）")
    ap.add_argument("--interval", type=float, default=0.4,
                    help="采样间隔秒（默认 0.4）")
    ap.add_argument("--anchor-interval", type=float, default=1.0,
                    help="角色屏幕锚点采样间隔秒（0=关闭；默认 1.0）")
    ap.add_argument("--reanchor-interval", type=float, default=8.0,
                    help="名字标签 OCR 重标定间隔秒（默认 8；"
                         "0=只在开始时标定一次）")
    ap.add_argument("--duration", type=float, default=900.0,
                    help="最长记录秒数（默认 900，10 分钟）")
    ap.add_argument("--out", default=None,
                    help="输出 JSONL（默认 logs/special_map_trace.jsonl）")
    ap.add_argument("--stop-file", default=None,
                    help="停止标记文件（默认 logs/special_map_trace.stop）")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out = Path(args.out) if args.out else REPO / "logs" / "special_map_trace.jsonl"
    stop_file = (Path(args.stop_file) if args.stop_file
                 else REPO / "logs" / "special_map_trace.stop")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if stop_file.exists():
            stop_file.unlink()
    except Exception:
        pass
    win = game.find_main_window()
    if win is None:
        print("NO_WINDOW：找不到游戏主窗口（未启动/未登录？）")
        return 2
    app_utils.set_silent_mode(True)
    print(f"窗口: {win.title}")
    print(f"目标地图: {args.map}；间隔 {args.interval}s；最长 {args.duration}s")
    role_name = _role_name(win)
    if args.anchor_interval > 0 and role_name:
        re_txt = ("只在开始时标定一次" if args.reanchor_interval <= 0
                  else f"每 {args.reanchor_interval:.0f}s 名字标签重标定")
        print(f"角色屏幕锚点：每 {args.anchor_interval}s 记录，{re_txt}"
              f"（名字标签「{role_name}」）")
    elif args.anchor_interval > 0:
        print("角色屏幕锚点：窗口标题解析不到角色名，关闭锚点记录")
    else:
        print("角色屏幕锚点：关闭（--anchor-interval 0）")
    print(f"输出: {out}")
    print(f"停止: Ctrl+C / 到时长 / 创建文件 {stop_file}")
    print("请现在开始操作人物描边（脚本只读坐标，不移动鼠标）。")

    samples = []
    anchor_count = 0
    last = None
    last_anchor = 0.0
    last_reanchor = 0.0
    anchor_fail = 0
    B = None        # 相机标定：world = M*screen + B
    prev_img = None
    t0 = time.time()
    last_heartbeat = t0
    with out.open("w", encoding="utf-8") as f:
        while time.time() - t0 < args.duration:
            if stop_file.exists():
                print("检测到停止文件，结束记录。")
                break
            now = time.time()
            img = win.capture(force_activate=False)
            pos = game_map.read_map_position(None, img=img) if img is not None else None
            if pos is None:
                pos = game_map.read_map_position(win)
            if pos is not None:
                name, x, y = str(pos[0]).strip(), int(pos[1]), int(pos[2])
            else:
                name = None
            if name == args.map:
                # 相机位移跟踪（与导航同一算法）：逐帧更新 B。
                # 相位相关有 +0.5px/次的系统偏差，靠定期名字标签 OCR
                # 重标定把漂移限制在一个窗口内（默认 8s 约 8px）。
                if img is not None:
                    if B is not None and prev_img is not None:
                        shift = map_nav._special_frame_shift(prev_img, img)
                        if shift is not None:
                            B = map_nav._special_apply_shift(B, shift)
                        else:
                            B = None   # 位移不可测：下次名字标签重标定
                    prev_img = img
                if last is None or (x, y) != last:
                    rec = {"t": round(now - t0, 2), "map": name, "x": x, "y": y}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    samples.append(rec)
                    last = (x, y)
                    print(f"  [{rec['t']:7.2f}s] {name} ({x},{y})")
                # 标定/重标定：角色名字标签 2x 放大 OCR（map_nav._name_label_anchor）
                if B is None:
                    gap = 10.0          # 尚未标定/断帧：10s 后重试
                elif args.reanchor_interval > 0:
                    gap = args.reanchor_interval
                else:
                    gap = None          # 只开始标定一次
                if (img is not None and role_name and args.anchor_interval > 0
                        and gap is not None and now - last_reanchor >= gap):
                    last_reanchor = now
                    if B is None:
                        # 首次标定：游戏画面中部整片 2x 扫描
                        anc = map_nav._name_label_anchor(img, role_name)
                        anchor_fail = 0
                    else:
                        # 已跟踪：只在预测位置附近小框 2x OCR（快）
                        pax, pay = _predicted_anchor(x, y, B)
                        anc = map_nav._name_label_anchor(
                            img, role_name, _reanchor_box(pax, pay))
                        if (anc is not None
                                and math.hypot(anc[1] - pax, anc[2] - pay)
                                > REANCHOR_MAX_SHIFT):
                            anc = None   # 偏离预测太远：多半是别人同名标签
                        if anc is None:
                            anchor_fail += 1
                            if anchor_fail >= REANCHOR_FULL_AFTER:
                                anchor_fail = 0
                                # 小框连续失败：漂移出框/标定错了 → 全画面重扫
                                anc = map_nav._name_label_anchor(img, role_name)
                    if anc is not None:
                        B = (x - map_nav.SPECIAL_WALK_SX * anc[1],
                             y - map_nav.SPECIAL_WALK_SY * anc[2])
                        print(f"  [{now - t0:7.2f}s] 锚点标定 "
                              f"({anc[1]},{anc[2]}) conf={anc[0]:.2f} "
                              f"B=({B[0]:.2f},{B[1]:.2f})")
                # 锚点样品：角色屏幕位置 = (HUD 坐标 - B)/M
                if (img is not None and B is not None
                        and args.anchor_interval > 0
                        and now - last_anchor >= args.anchor_interval):
                    last_anchor = now
                    ax = int(round((x - B[0]) / map_nav.SPECIAL_WALK_SX))
                    ay = int(round((y - B[1]) / map_nav.SPECIAL_WALK_SY))
                    rec = {"kind": "anchor",
                           "t": round(now - t0, 2), "map": name,
                           "x": x, "y": y, "ax": ax, "ay": ay,
                           "src": "track",
                           "w": img.size[0], "h": img.size[1]}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    anchor_count += 1
            else:
                # 不在目标图/位置读不到：相机状态未知，回来时重新标定
                B = None
                prev_img = None
            if now - last_heartbeat >= 10.0:
                last_heartbeat = now
                print(f"  ... 记录中 {len(samples)} 点、锚点 {anchor_count} 个，"
                      f"已 {now - t0:.0f}s（最长 {args.duration:.0f}s）")
            time.sleep(max(0.05, args.interval))
    if samples:
        xs = [r["x"] for r in samples]
        ys = [r["y"] for r in samples]
        dist = sum(abs(samples[i]["x"] - samples[i - 1]["x"])
                   + abs(samples[i]["y"] - samples[i - 1]["y"])
                   for i in range(1, len(samples)))
        summary = {
            "map": args.map,
            "points": len(samples),
            "anchor_points": anchor_count,
            "start": samples[0],
            "end": samples[-1],
            "bbox": [min(xs), min(ys), max(xs), max(ys)],
            "manhattan_distance": dist,
            "file": str(out),
        }
        summary_path = out.with_suffix(".summary.json")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"完成：{len(samples)} 点、锚点 {anchor_count} 个，"
              f"包围盒 {summary['bbox']}，里程 {dist}，汇总 {summary_path}")
    else:
        print("未记录到坐标（检查地图名/窗口/人物是否在目标地图）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
