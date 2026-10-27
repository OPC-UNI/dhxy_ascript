# 一次性计时脚本（诊断用，非项目代码）：实测 截图/OCR/模板匹配 单次成本
# 只读操作：force_activate=False，不点击不按键。与运行中的任务进程互不干扰（各自独立引擎）。
import sys, time, os
from pathlib import Path
sys.path.insert(0, r"C:\Users\Wang\Desktop\ascript\dhxy2")
# R 类按 sys.argv[0] 锚定根目录：脚本不在工程根，先显式指定（须在导入 src.* 之前）
from ascript.windows.system import R as _R
_R._BASE_DIR = Path(r"C:\Users\Wang\Desktop\ascript\dhxy2")
from ascript.windows.window.Window import WindowCaptureFormat
from src import game, map as game_map
from src import battle, task_tracker

win = game.find_main_window()
if win is None:
    print("未找到游戏主窗口"); sys.exit(1)
print(f"窗口: {win.title}  {win.rect}")

# 1. 截图成本
for fmt, label, n in ((None, "PIL(默认)", 5), (WindowCaptureFormat.OPENCV, "OPENCV", 5)):
    t0 = time.perf_counter(); c0 = time.process_time()
    for _ in range(n):
        img = win.capture(force_activate=False, format=fmt)
    wall = (time.perf_counter() - t0) / n; cpu = (time.process_time() - c0) / n
    print(f"capture[{label}]: wall {wall*1000:.0f}ms  cpu {cpu*1000:.0f}ms  (n={n}, ok={img is not None})")

# 2. 双区域 OCR（follow 轮询主成本）：区域与 xuanshu.FOLLOW_OCR_REGIONS 一致
img_pil = win.capture(force_activate=False)
t0 = time.perf_counter(); c0 = time.process_time()
items, _ = game_map.ocr_regions(img_pil, task_tracker.OCR_REGIONS)
wall1 = time.perf_counter() - t0; cpu1 = time.process_time() - c0
print(f"ocr_regions[双区域 TRACK+DIALOG]: wall {wall1*1000:.0f}ms  cpu {cpu1*1000:.0f}ms  items={len(items)}")

# 3. 单区域 OCR（确认轮询路径，每 0.2s 一次）
t0 = time.perf_counter(); c0 = time.process_time()
items2, _ = game_map.ocr_regions(img_pil, (task_tracker.TRACK_REGION,))
wall2 = time.perf_counter() - t0; cpu2 = time.process_time() - c0
print(f"ocr_regions[单区域 TRACK]:       wall {wall2*1000:.0f}ms  cpu {cpu2*1000:.0f}ms  items={len(items2)}")

# 4. 战斗判定模板匹配（battle-watch 每 0.5s 一次）
img_bgr = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
band = battle._bar_band(img_bgr)
banner = battle.BANNER_BAND
t0 = time.perf_counter(); c0 = time.process_time()
for _ in range(3):
    battle.is_in_battle_frame(img_bgr)
wall3 = (time.perf_counter() - t0) / 3; cpu3 = (time.process_time() - c0) / 3
print(f"is_in_battle_frame[3按钮条带+横幅条带]: wall {wall3*1000:.0f}ms  cpu {cpu3*1000:.0f}ms")

# 5. 模拟 follow 非战斗完整轮询（截一帧 + 双区域 OCR + 任务分组）
t0 = time.perf_counter(); c0 = time.process_time()
img_pil = win.capture(force_activate=False)
items, _ = game_map.ocr_regions(img_pil, task_tracker.OCR_REGIONS)
task = task_tracker.find_task(task_tracker.group_tasks(items), "除魔任务")
wall5 = time.perf_counter() - t0; cpu5 = time.process_time() - c0
print(f"follow 完整轮询[截帧+双区OCR+分组]: wall {wall5*1000:.0f}ms  cpu {cpu5*1000:.0f}ms  task={'命中' if task else '未命中'}")

# 6. 战斗期任务线程轮询（is_in_battle 截一帧+匹配）
t0 = time.perf_counter(); c0 = time.process_time()
for _ in range(3):
    battle.is_in_battle(win)
wall6 = (time.perf_counter() - t0) / 3; cpu6 = (time.process_time() - c0) / 3
print(f"is_in_battle[截帧+匹配]:           wall {wall6*1000:.0f}ms  cpu {cpu6*1000:.0f}ms")
