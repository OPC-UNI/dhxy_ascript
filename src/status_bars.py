"""人物/召唤兽血蓝条状态检测。

颜色检测基于窗口截图（win.capture / PrintWindow），坐标使用窗口相对坐标，
不受窗口位置移动与屏幕遮挡影响。

检测原理（实测确立）：血蓝条的填充色是随填充比例拉伸的渐变，
精确色值在不同状态下必然不同；而填充与空槽的边界有稳定的颜色跳变
（红/蓝 → 灰）。因此沿条中线扫描"填充色系"像素的右边缘即可得到
填充比例，不依赖精确色值。
"""
import numpy as np

from .app_utils import log, human_click
from .control_window import load_thresholds, DEFAULT_THRESHOLD

# 各条区域（窗口相对坐标，用户提供）
CHAR_HP_RECT = [1264, 82, 1365, 96]    # 人物红条（HP）
CHAR_MP_RECT = [1264, 99, 1365, 113]   # 人物蓝条（MP）
BEAST_HP_RECT = [1115, 80, 1186, 92]   # 召唤兽红条（HP）
BEAST_MP_RECT = [1115, 95, 1186, 106]  # 召唤兽蓝条（MP）

BARS = [
    ("人物红条", CHAR_HP_RECT, "red", "char_hp"),
    ("人物蓝条", CHAR_MP_RECT, "blue", "char_mp"),
    ("召唤兽红条", BEAST_HP_RECT, "red", "beast_hp"),
    ("召唤兽蓝条", BEAST_MP_RECT, "blue", "beast_mp"),
]

# 填充色宽松判定参数
_FILL_MIN_BRIGHT = 80   # 主通道最低亮度
_FILL_DOMINANCE = 30    # 主通道须高出其他通道的差值（蓝条对 G 通道放宽到 10）


def _is_fill_pixel(c, color_mode):
    """判断像素是否属于填充色系（红/蓝）。"""
    red, green, blue = int(c[0]), int(c[1]), int(c[2])
    if color_mode == "blue":
        return (blue > _FILL_MIN_BRIGHT and blue > red + _FILL_DOMINANCE
                and blue > green + 10)
    return (red > _FILL_MIN_BRIGHT and red > green + _FILL_DOMINANCE
            and red > blue + _FILL_DOMINANCE)


def _scan_fill_edge(img_np, rect, color_mode):
    """沿条中线扫描填充右边缘（填充色 → 空槽跳变点）。

    连续 3 个非填充像素视为填充结束（抗锯齿容错）。
    返回填充边缘 x 或 None（无填充=空条/不可见）。
    """
    l, t, r, b = rect
    y = (t + b) // 2
    edge = None
    gray_run = 0
    for x in range(l, r):
        if _is_fill_pixel(img_np[y, x], color_mode):
            edge = x
            gray_run = 0
        else:
            gray_run += 1
            if gray_run >= 3 and edge is not None:
                break
    return edge


def _scan_bar(img_np, rect, color_mode, threshold):
    """帧内检测单条血蓝条，返回 (百分比或 None, 是否低于阈值)。

    无填充（空条/未召唤）时百分比为 None。
    """
    edge = _scan_fill_edge(img_np, rect, color_mode)
    if edge is None:
        return None, True
    left, _t, right, _b = rect
    pct = (edge - left) / (right - left)
    return pct, pct < threshold / 100


def check_all_bars(win):
    """检测全部血蓝条：低于阈值时右键对应条回满。

    单帧检测：4 条共用一次截图（原先每条各自截窗 4 次，且默认
    带激活抢前台焦点）；像素扫描逻辑与阈值不变。
    日志合并：正常时一行摘要（原先每场战斗 4 行 INFO），
    仅未检测到填充/低于阈值/回满动作单独输出。
    """
    thresholds = load_thresholds()
    img = win.capture(force_activate=False)
    if img is None:
        log("[血蓝] 窗口截图失败，跳过检测", "WARN")
        return
    img_np = np.asarray(img)
    parts = []
    problems = []  # (label, rect, pct 或 None, threshold)
    for label, rect, mode, key in BARS:
        threshold = thresholds.get(key, DEFAULT_THRESHOLD)
        pct, low = _scan_bar(img_np, rect, mode, threshold)
        if pct is None:
            parts.append(f"{label}空")
            problems.append((label, rect, None, threshold))
            continue
        parts.append(f"{label}{pct * 100:.0f}%")
        if low:
            problems.append((label, rect, pct, threshold))
    log(f"[血蓝] {' '.join(parts)}", "INFO")
    for label, rect, pct, threshold in problems:
        if pct is None:
            log(f"[血蓝] {label}: 未检测到填充（空条或召唤兽未出战）", "WARN")
            continue
        log(f"[血蓝] {label}: {pct * 100:.0f}%（低于{threshold}%）", "WARN")
        # 低于阈值：移动到条中心右键回满
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        human_click(win, cx, cy, button="secondary")
        log(f"[血蓝] {label} 已右键回满（{pct * 100:.0f}% → 待恢复）", "SYS")
