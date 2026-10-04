"""玄枢轶闻任务自动化。

流程：
1. OCR 全窗口，检查任务追踪中是否存在 [玄枢轶闻]，没有则走第 4 步
2. 找到后读取其下方的具体任务内容
3. 在任务内容中找到绿色带下划线的文案，鼠标移动过去
4. 没有任务时，OCR 地图位置区域并输出文案内容
"""
import re

import numpy as np
from rapidocr_onnxruntime import RapidOCR

from .app_utils import log, human_move

# 任务追踪面板中的任务标题
TASK_TITLE = "玄枢轶闻"
# 地图位置文字区域（窗口相对坐标，左上角小地图下方）
MAP_POS_REGION = (45, 60, 222, 90)
# 绿色文案判定参数：G 通道显著高于 R/B 即视为绿色文字
GREEN_MIN_G = 90        # G 通道最低亮度
GREEN_DOMINANCE = 30    # G 须高出 R/B 的差值
GREEN_MIN_RATIO = 0.03  # 区域中绿色像素占比下限

_engine = None

# 地图位置文案格式：地名［坐标x,坐标y］（前后常混入小图标误识别的噪声字符）
_MAP_POS_RE = re.compile(r"([一-鿿]{2,})\s*[\[［]\s*(\d+)\s*[,，]\s*(\d+)\s*[\]］]")


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


def _ocr_engine():
    """懒加载 RapidOCR 引擎（首次初始化较慢）。"""
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def _ocr(img):
    """OCR 识别图像，返回按纵坐标排序的 [(txt, rect, score)] 列表。"""
    result, _ = _ocr_engine()(np.asarray(img))
    items = []
    if result:
        for box, txt, score in result:
            l, t = float(box[0][0]), float(box[0][1])
            r, b = float(box[2][0]), float(box[2][1])
            items.append((str(txt), [int(l), int(t), int(r), int(b)], float(score)))
    items.sort(key=lambda x: x[1][1])
    return items


def _find_task_title_and_content(items):
    """在 OCR 结果中找到 [玄枢轶闻] 标题及其下方的任务内容行。

    返回 (标题项, 内容项列表)；未找到标题时返回 (None, [])。
    """
    title_item = None
    for txt, rect, score in items:
        if TASK_TITLE in txt and score >= 0.6:
            title_item = (txt, rect, score)
            break
    if title_item is None:
        return None, []

    # 内容行：标题下方、与标题横向有交叠的文本（任务描述通常缩进几像素）
    _, tr, _ = title_item
    content = []
    for txt, rect, score in items:
        if rect[1] <= tr[3]:
            continue
        if rect[0] < tr[2] + 40 and rect[2] > tr[0] - 40:
            content.append((txt, rect, score))
    return title_item, content


def _is_green_text(img_np, rect):
    """判断文字区域内是否存在绿色像素（覆盖文字与下划线）。"""
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]]
    if region.size == 0:
        return False
    g = region[:, :, 1].astype(int)
    r = region[:, :, 0].astype(int)
    b = region[:, :, 2].astype(int)
    green_mask = (g > GREEN_MIN_G) & (g > r + GREEN_DOMINANCE) & (g > b + GREEN_DOMINANCE)
    return float(green_mask.mean()) > GREEN_MIN_RATIO


def run_cycle(win):
    """执行一轮玄枢轶闻检查（由任务主循环周期性调用）。

    Args:
        win: 游戏主窗口 Window 对象
    """
    img = win.capture()
    if img is None:
        return

    items = _ocr(img)
    title, content = _find_task_title_and_content(items)

    if title is None:
        # 第 4 步：没有任务 → OCR 地图位置区域，输出清洗后的文案内容
        region_img = img.crop(MAP_POS_REGION)
        region_items = _ocr(region_img)
        raw = " ".join(t[0] for t in region_items).strip()
        text = _clean_map_pos(raw)
        log(f"[玄枢轶闻] 未检测到任务，当前地图位置: {text or '识别为空'}", "INFO")
        return

    log(f"[玄枢轶闻] 已找到任务: {title[0]}", "SYS")

    # 第 2/3 步：在任务内容中找到绿色带下划线的文案，鼠标移动过去
    img_np = np.asarray(img)
    for txt, rect, score in content:
        if _is_green_text(img_np, rect):
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            log(f"[玄枢轶闻] 绿色文案: {txt} @ ({cx}, {cy})", "SYS")
            human_move(win.rect.left + cx, win.rect.top + cy)
            return
    log("[玄枢轶闻] 任务内容中未找到绿色文案", "WARN")
