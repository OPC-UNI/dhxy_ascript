# -*- coding: utf-8 -*-
"""游戏窗口探针：经项目自身窗口句柄通道截图 / OCR / 模板匹配 / 点击 / 长按。

复用场景：需要实机观察或操作正在运行的大话西游窗口（摸清新小游戏/UI
流程、验证候选算法）时使用。**禁止 windows-mcp / 桌面级键鼠**（本项目
高危禁用）——本脚本就是替代通道：不移动物理鼠标、不抢前台。

用法（在仓库根目录，用项目 venv 的 python 运行）：

    python .claude/skills/game-window-probe/scripts/probe.py info
    python .claude/skills/game-window-probe/scripts/probe.py capture [out.png]
    python .claude/skills/game-window-probe/scripts/probe.py ocr [l t r b]
    python .claude/skills/game-window-probe/scripts/probe.py match 模板名.png [l t r b]
    python .claude/skills/game-window-probe/scripts/probe.py click X Y
    python .claude/skills/game-window-probe/scripts/probe.py hold X Y 秒数
    python .claude/skills/game-window-probe/scripts/probe.py burst N 间隔秒 [outdir]

坐标一律为**窗口相对坐标**（与 human_click 同空间，即 win.rect 左上角
为原点）；capture 出的 PNG 与截图坐标同空间，可直接在图上量。
帧文件默认写到系统临时目录（不落仓库）。
"""
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

# 控制台 GBK：中文/符号直接 print 会炸，统一改 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _find_repo_root() -> Path:
    """从脚本位置向上找仓库根（含 src/map.py 的目录）。"""
    for p in Path(__file__).resolve().parents:
        if (p / "src" / "map.py").exists():
            return p
    raise SystemExit("未找到仓库根（向上找不到 src/map.py）")


REPO = _find_repo_root()
sys.path.insert(0, str(REPO))

from ascript.windows.system import R  # noqa: E402

# R 的资源根默认按 sys.argv[0] 目录解析（= 本脚本目录）——必须改回仓库根，
# 否则 res/img 模板与 res/models OCR 模型全部找不到
R._BASE_DIR = REPO

from src import game  # noqa: E402
from src import app_utils  # noqa: E402
from src import silent_click  # noqa: E402

app_utils.set_silent_mode(True)  # 静默通道：不动鼠标、不抢前台

DEFAULT_OUT = Path(tempfile.gettempdir()) / "game-window-probe"


def _win():
    win = game.find_main_window()
    if win is None:
        print("NO_WINDOW：找不到游戏主窗口（未启动/未登录？）")
        raise SystemExit(2)
    return win


def _img_to_bgr(img):
    import cv2
    import numpy as np
    return cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)


def _win_state(win):
    """前台/最小化状态（ctypes，只读）。"""
    u = ctypes.windll.user32
    hwnd = int(getattr(win, "hwnd", 0) or 0)
    return {
        "foreground": u.GetForegroundWindow() == hwnd,
        "minimized": bool(u.IsIconic(hwnd)),
        "visible": bool(u.IsWindowVisible(hwnd)),
    }


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "info"
    win = _win()
    if cmd == "info":
        print(f"title: {win.title}")
        print(f"hwnd: {getattr(win, 'hwnd', 0)}  "
              f"rect: ({win.rect.left},{win.rect.top},"
              f"{win.rect.width}x{win.rect.height})")
        print(f"state: {_win_state(win)}")
        return 0
    if cmd == "capture":
        out = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT / "frame.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img = win.capture(force_activate=False)
        if img is None:
            print("CAPTURE_FAIL：截图返回 None")
            return 2
        img.save(out)
        print(f"saved {out} size={img.size}")
        return 0
    if cmd == "ocr":
        from src import map as game_map
        img = win.capture(force_activate=False)
        if img is None:
            print("CAPTURE_FAIL")
            return 2
        regions = None
        if len(argv) >= 6:
            regions = (tuple(int(v) for v in argv[2:6]),)
        items, _ = game_map.ocr_regions(img, regions)
        for t, r, s in sorted(items, key=lambda x: (x[1][1], x[1][0])):
            # 中心坐标便于直接喂 click
            print(f"{s:.3f}\t({r[0]},{r[1]},{r[2]},{r[3]})\t"
                  f"({(r[0] + r[2]) // 2},{(r[1] + r[3]) // 2})\t{t}")
        return 0
    if cmd == "match":
        from src import map as game_map
        icon = argv[2]
        region = tuple(int(v) for v in argv[3:7]) if len(argv) >= 7 else None
        img = win.capture(force_activate=False)
        if img is None:
            print("CAPTURE_FAIL")
            return 2
        score, pt = game_map.best_match_np(_img_to_bgr(img), icon,
                                           region=region)
        print(f"{icon}: score={score:.3f} pt={pt}")
        return 0
    if cmd == "click":
        x, y = int(argv[2]), int(argv[3])
        info = silent_click.send_silent_click(win, x, y)
        print(f"silent_click ({x},{y}) -> {info}")
        return 0 if info else 2
    if cmd == "hold":
        x, y, sec = int(argv[2]), int(argv[3]), float(argv[4])
        hold = app_utils.MouseHold(win, x, y)
        try:
            ok = hold.start()
            print(f"hold start={ok} ({x},{y}) {sec}s")
            time.sleep(sec)
        finally:
            hold.stop()
        print("hold released")
        return 0
    if cmd == "burst":
        n = int(argv[2])
        gap = float(argv[3])
        outdir = Path(argv[4]) if len(argv) > 4 else DEFAULT_OUT / "burst"
        outdir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        for i in range(n):
            target = t0 + i * gap
            now = time.time()
            if now < target:
                time.sleep(target - now)
            img = win.capture(force_activate=False)
            if img is not None:
                img.save(outdir / f"burst_{i:03d}.png")
        print(f"burst {n} frames gap={gap}s -> {outdir}")
        return 0
    print(f"UNKNOWN_CMD {cmd}")
    print(__doc__)
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
