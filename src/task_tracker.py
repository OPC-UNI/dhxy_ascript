"""任务追踪面板相关操作。

任务追踪面板位于窗口左上区域，包含多个任务：标题行与其下的内容描述行。
描述可能换行成多行；绿色带下划线的文案是可点击的跟踪链接。

功能：
- read_tasks：读取任务列表（标题 + 内容行，内容拼接为 text 供跨换行查找）
- task_count / has_task / find_task / get_task：任务数量、存在性、查找与当前信息
- track_task：跟踪任务（点击任务内容的绿色文案；默认第一个，可指定序号或文案）
- task_signature：任务信息签名（检测任务信息是否变化）
- is_green_text：绿色文案判定（供对话框等场景复用）
"""
import re
import time

import numpy as np

from .app_utils import log, human_click
from . import map as game_map  # OCR 能力复用

# 绿色文案判定参数：G 通道显著高于 R/B 即视为绿色文字
GREEN_MIN_G = 90
GREEN_DOMINANCE = 30
# 区域中绿色像素占比下限（OCR 合并长描述行时绿字占比可低至 ~2.6%，实测取 1%）
GREEN_MIN_RATIO = 0.01
# 追踪面板左区边界（x），用于区分面板标题与居中对话框的绿色选项
PANEL_MAX_X = 400


def green_ratio(img_np, rect):
    """文字区域内绿色像素占比。"""
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]]
    if region.size == 0:
        return 0.0
    g = region[:, :, 1].astype(int)
    r = region[:, :, 0].astype(int)
    b = region[:, :, 2].astype(int)
    mask = (g > GREEN_MIN_G) & (g > r + GREEN_DOMINANCE) & (g > b + GREEN_DOMINANCE)
    return float(mask.mean())


def is_green_text(img_np, rect):
    """判断文字区域内是否存在绿色像素（覆盖文字与下划线）。"""
    return green_ratio(img_np, rect) > GREEN_MIN_RATIO


def green_text_center(img_np, rect):
    """定位文字区域内绿色像素的点击中心。

    OCR 可能把描述文字与绿色链接合并成一行（绿字只占行的一部分），
    此时应按绿色像素列的中心点击而不是整行中心。
    无绿色像素时退回区域中心。
    """
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]]
    if region.size == 0:
        return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    g = region[:, :, 1].astype(int)
    r = region[:, :, 0].astype(int)
    b = region[:, :, 2].astype(int)
    mask = (g > GREEN_MIN_G) & (g > r + GREEN_DOMINANCE) & (g > b + GREEN_DOMINANCE)
    cols = np.where(mask.any(axis=0))[0]
    if cols.size == 0:
        return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    cx = rect[0] + (cols.min() + cols.max()) // 2
    cy = (rect[1] + rect[3]) // 2
    return cx, cy


def _is_title_line(txt):
    """标题行判定：含方括号的短行（任务标题如 洛川乱渊[水啸神踪]）。"""
    return len(txt) <= 20 and ("[" in txt or "【" in txt or "［" in txt)


def group_tasks(items):
    """把 OCR 行按标题行分组为任务列表。"""
    tasks = []
    cur = None
    for txt, rect, score in items:
        if _is_title_line(txt):
            if cur is not None:
                tasks.append(cur)
            cur = {"title": txt, "lines": [], "text": ""}
        elif cur is not None and rect[0] < PANEL_MAX_X:
            cur["lines"].append((txt, rect, score))
    if cur is not None:
        tasks.append(cur)
    for t in tasks:
        # 中文换行拼接不加空格，跨换行文案才能直接匹配
        t["text"] = "".join(ln[0] for ln in t["lines"])
    return tasks


def read_tasks(win):
    """读取任务追踪面板中的任务列表。

    每个任务为 {'title': 标题文案, 'lines': [(txt, rect, score), ...],
    'text': 内容行拼接全文（描述换行时用此字段查找）}。
    """
    img = win.capture()
    if img is None:
        return []
    return group_tasks(game_map.ocr_image(img))


def task_count(win):
    """任务追踪中的任务数量。"""
    return len(read_tasks(win))


def has_task(win, title):
    """判断任务追踪中是否存在指定任务。

    判定为追踪面板标题：位于窗口左区（x<PANEL_MAX_X）且非绿色链接文案，
    避免把居中对话框里的绿色选项误判为任务。
    """
    img = win.capture()
    if img is None:
        return False
    items = game_map.ocr_image(img)
    img_np = np.asarray(img)
    for txt, rect, score in items:
        if title in txt and score >= 0.6 and rect[0] < PANEL_MAX_X:
            if not is_green_text(img_np, rect):
                return True
    return False


def find_task(tasks, title):
    """在任务列表中查找指定任务（标题优先，其次拼接后的内容）。"""
    for t in tasks:
        if title in t["title"]:
            return t
    for t in tasks:
        if title in t["text"]:
            return t
    return None


def get_task(win, title):
    """读取任务追踪中指定任务的当前信息；不存在返回 None。

    返回 {'title', 'lines', 'text'} 或 None。
    """
    img = win.capture()
    if img is None:
        return None
    return find_task(group_tasks(game_map.ocr_image(img)), title)


def task_signature(task):
    """任务信息签名（标题 + 拼接内容），用于检测任务信息是否变化。

    比较前剔除标点与空白，避免 OCR 对句读的有无造成假变化
    （如「…玄石」与「…玄石。」被误判为信息变化导致重复点击）。
    """
    return re.sub(r"[^\w]", "", f"{task['title']}|{task['text']}")


def track_task(win, title, text=None, green_index=0, retries=4, retry_interval=1.5):
    """跟踪任务：点击任务内容的绿色带下划线文案。

    默认点击第一个绿色文案（green_index=0）；指定 text 时先在内容
    （含跨换行拼接）中查找该文案再点击所在行。
    任务刚领取时内容行可能尚未渲染（标题已出现、内容为空），
    内容为空时自动重试等待渲染。
    返回 (是否成功, 描述)。
    """
    last_img_np = None
    last_anchor_txt = ""
    last_content = []
    for attempt in range(retries + 1):
        img = win.capture()
        if img is None:
            return False, "窗口截图失败"
        items = game_map.ocr_image(img)
        img_np = np.asarray(img)

        # 任务锚点行：任意包含标题文案的行（兼容无方括号的标题）
        anchor_idx = None
        for i, (txt, rect, score) in enumerate(items):
            if title in txt and score >= 0.6:
                anchor_idx = i
                break
        if anchor_idx is None:
            return False, f"任务追踪中未找到「{title}」"
        arect = items[anchor_idx][1]
        last_img_np = img_np
        last_anchor_txt = items[anchor_idx][0]

        # 内容行：锚点下方、横向交叠、至下一个标题行或纵向超过 150px 为止（描述换行）
        content = []
        for txt, rect, score in items[anchor_idx + 1:]:
            if rect[1] - arect[3] > 150:
                break
            if _is_title_line(txt):
                break
            if rect[0] < arect[2] + 40 and rect[2] > arect[0] - 40:
                content.append((txt, rect, score))
        last_content = content

        if text is not None:
            # 指定文案：单行命中优先；跨换行时在拼接全文（无空格）确认存在后点击第一个绿色文案
            hit = [it for it in content if text in it[0]]
            if hit:
                content = hit
            elif text not in "".join(t[0] for t in content):
                if attempt < retries and not content:
                    log(f"[任务追踪] 「{title}」内容尚未渲染（第 {attempt + 1} 次），等待后重试", "INFO")
                    time.sleep(retry_interval)
                    continue
                return False, f"「{title}」内容中未找到文案「{text}」"
            else:
                log(f"[任务追踪] 文案「{text}」跨换行，点击第一个绿色文案", "WARN")

        green = [it for it in content if is_green_text(img_np, it[1])]
        if green:
            tgt = green[min(green_index, len(green) - 1)]
            # 跨行绿字：相邻绿行构成一个链接（如「那块」+「奇怪的石头」），
            # 选择绿色像素最多的行点击（链接主体所在行，避开描述行的绿字噪声）
            cluster = [tgt]
            for other in green:
                if other is not tgt and abs(other[1][1] - tgt[1][3]) <= 6 \
                        and abs(other[1][0] - tgt[1][0]) < 80:
                    cluster.append(other)
            if len(cluster) > 1:
                tgt = max(cluster, key=lambda it: green_ratio(img_np, it[1]))
                wrap_note = " + ".join(f"「{t[0]}」" for t in cluster if t is not tgt)
                wrap_note += "(跨行绿字)"
            else:
                wrap_note = ""
            cx, cy = green_text_center(img_np, tgt[1])
            human_click(win, cx, cy)
            log(f"[任务追踪] 已点击「{title}」绿色文案: {tgt[0]}"
                + (f" + {wrap_note}" if wrap_note else "")
                + f" @ ({cx},{cy})", "SYS")
            return True, f"已跟踪「{title}」（绿色文案: {tgt[0]}{' + ' + wrap_note if wrap_note else ''}）"

        # 无绿字且内容为空：刚领取尚未渲染 → 重试
        if attempt < retries and not content:
            log(f"[任务追踪] 「{title}」内容尚未渲染（第 {attempt + 1} 次），等待后重试", "INFO")
            time.sleep(retry_interval)
            continue
        break

    # 失败诊断：输出识别到的任务信息与各行绿色像素占比，便于排查绿字漏检
    detail = " / ".join(f"「{t[0]}」{green_ratio(last_img_np, t[1]):.1%}"
                        for t in last_content) or "(无内容行)"
    log(f"[任务追踪] 识别到「{title}」任务: 标题「{last_anchor_txt}」，"
        f"内容 {len(last_content)} 行: {detail}", "WARN")
    return False, f"「{title}」任务内容中未找到绿色文案"
