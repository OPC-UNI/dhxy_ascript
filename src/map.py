"""地图相关操作：打开/关闭世界地图与小地图、判断与识别当前地图位置。

功能：
- read_map_position：OCR 地图位置区域，识别当前地图 (地名, x, y)
- read_position_with_recovery：识别当前位置，失败按 Alt+C 恢复后重试
- is_in_map：判断当前是否在指定地图（地名模糊匹配）
- is_at_position：判断位置是否在指定地图坐标附近（±tolerance）
- open_world_map / close_world_map：世界地图开关
- open_mini_map：打开当前地图的小地图（点击地图位置区域）
- open_mini_map_city：打开世界地图并点击指定城市，打开该城市的小地图
- close_mini_map / close_maps：小地图/全部地图关闭（小地图左键、世界地图右键模板位置，画布兜底）
- find_template：模板查找（返回窗口相对坐标）

坐标约定：全部使用窗口相对坐标，由调用方通过 human_click 转屏幕坐标。
"""
import re
import time

import numpy as np
from rapidocr_onnxruntime import RapidOCR

from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages

from .app_utils import human_click, log

# 地图位置文字区域（窗口相对坐标，左上角）
MAP_POS_REGION = (45, 60, 222, 90)
# 世界地图按钮模板（实测匹配 0.969）
WORLD_MAP_ICON = "item_map_shijie.png"
# 世界地图上的城市图标（任务模块共用：点击城市打开该城市的小地图）
WORLD_MAP_LUOYANG_ICON = "shijie_luoyang.png"  # 洛阳城（实测匹配 0.991）
WORLD_MAP_CHANGAN_ICON = "shijie_changan.png"         # 长安城（五环任务实测）
# 世界地图打开标志模板（实测匹配 0.95，右键其位置可关闭）
WORLD_MAP_OPEN_ICON = "map_shijie_open.png"
# 小地图打开标志模板（实测匹配 0.95；左键其位置可关闭）
MINI_MAP_OPEN_ICON = "map_xiaoditu_open.png"
# 地图画布回退点：右键地图画布可关闭地图（实测有效）
MAP_CANVAS_POS = (700, 380)

# 地图位置文案格式：地名［坐标x,坐标y］（前后常混入小图标误识别的噪声字符）
_MAP_POS_RE = re.compile(r"([一-鿿]{2,})\s*[\[［]\s*(\d+)\s*[,，]\s*(\d+)\s*[\]］]")

_engine = None


def ocr_regions(img, regions):
    """OCR 识别多个区域并合并结果，返回 (items, img_np)。

    regions 为空时识别全图。多区域场景（如任务追踪 + 对话框）比单一
    外接矩形省去区域之间的无关像素，进一步提速；rect 与 img_np 保持
    全图坐标（区域外置黑），下游绿色文字判定与点击坐标无需改动。

    items：按纵坐标排序的 [(txt, rect, score)] 列表；
    img_np：与全图等大的 numpy 数组（区域外为黑），供调用方做像素级判断。
    """
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    full_np = np.asarray(img)
    if not regions:
        img_np = full_np
        result, _ = _engine(img_np)
        items = []
        if result:
            for box, txt, score in result:
                bl, bt = float(box[0][0]), float(box[0][1])
                br, bb = float(box[2][0]), float(box[2][1])
                items.append((str(txt), [int(bl), int(bt), int(br), int(bb)],
                              float(score)))
        items.sort(key=lambda x: x[1][1])
        return items, img_np
    img_np = np.zeros_like(full_np)
    items = []
    for l, t, r, b in regions:
        crop = full_np[t:b, l:r]
        img_np[t:b, l:r] = crop
        result, _ = _engine(crop)
        if result:
            for box, txt, score in result:
                bl, bt = float(box[0][0]), float(box[0][1])
                br, bb = float(box[2][0]), float(box[2][1])
                items.append((str(txt),
                              [l + int(bl), t + int(bt), l + int(br), t + int(bb)],
                              float(score)))
    items.sort(key=lambda x: x[1][1])
    return items, img_np


def ocr_region(img, region=None):
    """OCR 识别单个区域（region 为 None 时全图），见 ocr_regions。"""
    return ocr_regions(img, [] if region is None else [region])


def ocr_image(img):
    """OCR 识别全图，返回 (items, img_np)（见 ocr_regions，regions 为空）。"""
    return ocr_regions(img, [])


def _clean_map_pos(text):
    """清洗地图位置文案，提取 地名[坐标] 主内容。

    匹配到「地名[数字,数字]」格式时直接返回干净结果；
    否则剔除纯标点/符号噪声后返回，仍无有效内容返回空串。
    """
    m = _MAP_POS_RE.search(text)
    if m:
        return f"{m.group(1)}[{m.group(2)},{m.group(3)}]"
    cleaned = re.sub(r"[^一-鿿0-9A-Za-z\[\]［］,，\s]", "", text)
    return cleaned.strip(" ,，[]［］")


def read_map_position(win):
    """OCR 地图位置区域（MAP_POS_REGION），返回 (地名, x, y)；失败返回 None。

    地名统一繁转简（世界地图用繁体，位置文案两种都可能出现）。
    """
    img = win.capture()
    if img is None:
        return None
    region_img = img.crop(MAP_POS_REGION)
    items, _ = ocr_image(region_img)
    raw = " ".join(t[0] for t in items).strip()
    m = _MAP_POS_RE.search(_clean_map_pos(raw))
    if m:
        name = m.group(1).replace("陽", "阳")
        return name, int(m.group(2)), int(m.group(3))
    return None


def read_position_with_recovery(win):
    """识别当前位置；失败时按 Alt+C 恢复位置显示后重试一次（常见于人物未落地）。"""
    pos = read_map_position(win)
    if pos is None:
        log("[地图] 位置识别失败，按 Alt+C 后重试", "WARN")
        Keyboard.key_hot("alt", "c")
        time.sleep(1.0)  # 等待位置显示恢复
        pos = read_map_position(win)
    return pos


def is_in_map(win, name):
    """判断当前是否在指定地图（地名精确匹配）。

    精确匹配避免误判相邻地图（如「长安」不能匹配「长安城东」）。
    """
    pos = read_position_with_recovery(win)
    return pos is not None and pos[0].strip() == name


def is_at_position(pos, name, x, y, tolerance=5):
    """判断位置是否在指定地图坐标附近（±tolerance 像素）。"""
    if pos is None:
        return False
    pname, px, py = pos
    return (pname == name
            and abs(px - x) <= tolerance
            and abs(py - y) <= tolerance)


def find_template(win, icon, threshold=0.8):
    """在游戏窗口内查找模板，返回首个匹配的 (x, y) 窗口相对坐标或 None。"""
    results = FindImages.find_all_template(R.img(icon), im_source=win, threshold=threshold)
    return (results[0].x, results[0].y) if results else None


def find_templates(win, icon, threshold=0.8):
    """在游戏窗口内查找模板的全部匹配，返回 [(x, y), ...] 窗口相对坐标列表。"""
    return [(r.x, r.y)
            for r in FindImages.find_all_template(R.img(icon), im_source=win, threshold=threshold)]


def is_world_map_open(win):
    """判断世界地图是否打开。"""
    return find_template(win, WORLD_MAP_OPEN_ICON) is not None


def is_mini_map_open(win):
    """判断小地图是否打开。"""
    return find_template(win, MINI_MAP_OPEN_ICON) is not None


def open_world_map(win):
    """点击世界地图按钮打开世界地图。返回 (是否成功, 描述)。"""
    pt = find_template(win, WORLD_MAP_ICON)
    if pt is None:
        return False, "未找到世界地图按钮"
    human_click(win, pt[0], pt[1])
    time.sleep(1.5)  # 等待世界地图打开
    if not is_world_map_open(win):
        return False, "点击后世界地图未打开"
    return True, "世界地图已打开"


def open_mini_map(win):
    """点击地图位置区域打开当前地图的小地图。返回 (是否成功, 描述)。"""
    cx = (MAP_POS_REGION[0] + MAP_POS_REGION[2]) // 2
    cy = (MAP_POS_REGION[1] + MAP_POS_REGION[3]) // 2
    human_click(win, cx, cy)
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.5)
    if not is_mini_map_open(win):
        return False, "点击后小地图未打开"
    return True, "小地图已打开"


def open_mini_map_city(win, city_icon, city_label):
    """打开世界地图并点击指定城市，打开该城市的小地图。

    世界地图点击城市后自动关闭（实测）。返回 (是否成功, 描述)。
    """
    ok, info = open_world_map(win)
    if not ok:
        return False, info
    pt = find_template(win, city_icon)
    if pt is None:
        return False, f"世界地图中未找到{city_label}"
    human_click(win, pt[0], pt[1])
    time.sleep(1.5)  # 等待城市小地图打开
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.5)
    if not is_mini_map_open(win):
        return False, f"点击{city_label}后小地图未打开"
    return True, f"{city_label}小地图已打开"


def close_map_if_open(win, icon, label, fallbacks=(), primary_button="secondary"):
    """检测地图（模板）是否打开，打开则点击模板位置关闭。

    先以 primary_button 点击模板位置（小地图左键、世界地图右键）；
    若仍打开，依次右键回退点（画布区域）。
    返回 (是否成功, 描述)。
    """
    pt = find_template(win, icon)
    if pt is None:
        return False, f"{label}未打开，无需关闭"
    human_click(win, pt[0], pt[1], button=primary_button)
    time.sleep(1.0)  # 等待关闭动画
    if find_template(win, icon) is None:
        btn = "左" if primary_button == "primary" else "右"
        return True, f"{label}已在模板位置{btn}键关闭"
    for x, y in list(fallbacks):
        human_click(win, x, y, button="secondary")
        time.sleep(1.0)  # 等待关闭动画
        if find_template(win, icon) is None:
            return True, f"{label}已右键关闭（画布回退点({x},{y})）"
    return False, f"{label}关闭失败"


def close_world_map(win):
    """关闭世界地图（若打开）：模板位置右键，画布兜底。返回 (是否成功, 描述)。"""
    return close_map_if_open(win, WORLD_MAP_OPEN_ICON, "世界地图",
                             fallbacks=(MAP_CANVAS_POS,), primary_button="secondary")


def close_mini_map(win):
    """关闭小地图（若打开）：模板位置左键，画布右键兜底。返回 (是否成功, 描述)。"""
    return close_map_if_open(win, MINI_MAP_OPEN_ICON, "小地图",
                             fallbacks=(MAP_CANVAS_POS,), primary_button="primary")


def close_maps(win):
    """关闭所有打开的地图（小地图在上层，先关）。"""
    close_mini_map(win)
    close_world_map(win)
