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
# 链接亮绿判定下限：链接文字/下划线为亮绿（五环实测 G≈186、玄枢轶闻 G=255），
# 而任务描述文字可能是暗绿（五环实测 G≈130），须区分
GREEN_BRIGHT_MIN = 150
GREEN_DOMINANCE = 30
# 区域中绿色像素占比下限（OCR 合并长描述行时绿字占比可低至 ~2.6%，实测取 1%）
GREEN_MIN_RATIO = 0.01
# 追踪面板左区边界（x），用于区分面板标题与居中对话框的绿色选项
PANEL_MAX_X = 400
# 跟踪轮询与点击的 OCR 区域（窗口相对坐标）：任务追踪面板 + 居中对话框区。
# 区域外（顶部 HUD、右侧图标列、底部栏）无需识别；全窗 OCR 实测 2.2s，
# 两个区域合计约 1.2s，轮询与点击提速近一半。
# 面板区右边界实测取 400（面板内容在 x<260，260~400 为空白背景）：
# 宽 260 的裁剪会确定性误读标题（实测「玄枢轶闻」读成「玄枢轶铁闻」
# 导致任务被误判消失），宽 300 及以上读对，留 400 余量。
TRACK_REGION = (0, 200, 400, 600)      # 任务追踪面板
DIALOG_REGION = (400, 270, 980, 600)   # 居中对话框区
OCR_REGIONS = (TRACK_REGION, DIALOG_REGION)


def _green_mask(region, min_g):
    """绿色像素掩码（G 通道亮度与色相判定）。"""
    g = region[:, :, 1].astype(int)
    r = region[:, :, 0].astype(int)
    b = region[:, :, 2].astype(int)
    return (g > min_g) & (g > r + GREEN_DOMINANCE) & (g > b + GREEN_DOMINANCE)


def green_ratio(img_np, rect, min_g=GREEN_MIN_G):
    """文字区域内绿色像素占比。"""
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]]
    if region.size == 0:
        return 0.0
    return float(_green_mask(region, min_g).mean())


def bright_green_ratio(img_np, rect):
    """文字区域内亮绿像素（链接文字/下划线）占比。"""
    return green_ratio(img_np, rect, min_g=GREEN_BRIGHT_MIN)


def is_green_text(img_np, rect):
    """判断文字区域内是否存在绿色像素（覆盖文字与下划线）。"""
    return green_ratio(img_np, rect) > GREEN_MIN_RATIO


def _longest_run_center(cols):
    """最长连续列段的中点（下划线为实线，比文字噪点列段显著更长）。"""
    best_start = best_len = 0
    start = None
    prev = 0
    for c in cols:
        if start is None or c != prev + 1:
            start = c
        prev = c
        if c - start + 1 > best_len:
            best_len = c - start + 1
            best_start = start
    return best_start + best_len // 2


def _longest_run_len(cols):
    """最长连续列段长度（下划线实线判定；文字行笔画为散碎列）。"""
    best = run = 0
    prev = None
    for c in cols:
        run = run + 1 if prev is not None and c == prev + 1 else 1
        prev = c
        if run > best:
            best = run
    return best


def green_text_center(img_np, rect, prefer_underline=True):
    """定位文字区域内绿色像素的点击中心。

    OCR 可能把描述文字与绿色链接合并成一行（绿字只占行的一部分，
    甚至整行描述都与链接同色），此时按整行绿色像素中心点击会点错。

    prefer_underline=True（任务追踪链接）：优先点击链接下划线——在 OCR 框
    底边 ±3px 内找实线亮绿行（≥20px 连续列段），点击该行上方 2px
    （下划线是链接专属，不点到文字下方；下划线可能只覆盖行尾的可点击
    部分，如「去找大唐边境(193,144)惯骗」仅「惯骗」可点击）。
    prefer_underline=False（对话框选项）：不检测下划线——按亮绿/绿色像素
    的纵向中心与最长连续列段定位，落在选项文字本体上（OCR 框的余白或
    邻行文字不干扰）。
    依次退回亮绿像素、任意绿色像素，再退回区域中心。
    """
    region = img_np[rect[1]:rect[3], rect[0]:rect[2]]
    if region.size == 0:
        return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    if prefer_underline:
        # 下划线检测：下划线是文字下方的一行实线亮绿。下划线可能只覆盖
        # 行尾的可点击部分——如「去找大唐边境(193,144)惯骗」仅「惯骗」
        # 带下划线、其余绿字不可点击，必须点在下划线段上。
        # OCR 框常把下划线排除在框外（框底即文字底，下划线位于框底 ±3px 内），
        # 换行碎片行（如 [3/5]）的文字紧邻其下但笔画散碎。
        # 规则：框底 ±3px 内找含 ≥20px 连续亮绿列段的行（文字行笔画连排
        # 实测 ≤13px，下划线实测 ≥30px），取最低行，点击列段中心上方 2px。
        y0 = max(0, rect[3] - 3)
        y1 = min(img_np.shape[0], rect[3] + 4)
        x0 = max(0, rect[0] - 2)
        x1 = min(img_np.shape[1], rect[2] + 4)
        mask = _green_mask(img_np[y0:y1, x0:x1], GREEN_BRIGHT_MIN)
        for y_row in range(mask.shape[0] - 1, -1, -1):
            cols = np.where(mask[y_row])[0]
            if cols.size < 20 or _longest_run_len(cols) < 20:
                continue
            cx = int(x0 + _longest_run_center(cols))
            cy = int(y0 + y_row - 2)  # 下划线上方 2px，容 ±2px 点击抖动
            return cx, cy
    # 亮绿/绿色像素：最长连续列段横向中心、绿色像素纵向中心（落在文字本体上）
    for min_g in (GREEN_BRIGHT_MIN, GREEN_MIN_G):
        mask = _green_mask(region, min_g)
        cols = np.where(mask.any(axis=0))[0]
        rows = np.where(mask.any(axis=1))[0]
        if cols.size:
            cx = int(rect[0] + _longest_run_center(cols))
            cy = int(rect[1] + (rows.min() + rows.max()) // 2)
            return cx, cy
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def _is_title_line(txt):
    """标题行判定：短行且含方括号，且括号段内不含数字。

    任务标题如 洛川乱渊[水啸神踪]；内容行的回合计数如 [1/5]、
    [1/（OCR 截断）括号段内含数字，须视为内容行。
    标题左括号可能被 OCR 丢失（如 五环[五环] 读成 五环五环]），
    短行只含右括号且无数字时也按标题处理。
    追踪链接行（如 去找大唐境内(223, 21)武痴[1/5]）括号段内含数字；
    OCR 截断只剩未闭合左括号时（…武痴[）也不按标题处理，否则
    内容采集在链接行处中断（实测五环内容 0 行即由此引起）。
    """
    if len(txt) > 20:
        return False
    if not re.search(r"[\[［【\]］】]", txt):
        return False
    # 括号段内含数字（如 [1/5]）→ 内容行
    if re.search(r"[\[［【][^\]］】]*\d", txt):
        return False
    # 左括号无右括号闭合（OCR 截断，如「…武痴[」）→ 内容行
    if re.search(r"[\[［【][^\]］】]*$", txt):
        return False
    return True


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
    img = win.capture(force_activate=False)
    if img is None:
        return []
    items, _ = game_map.ocr_image(img)
    return group_tasks(items)


def task_count(win):
    """任务追踪中的任务数量。"""
    return len(read_tasks(win))


def has_task_in(items, img_np, title):
    """在 OCR 结果中判断追踪面板是否存在指定任务（判定规则见 has_task）。"""
    for txt, rect, score in items:
        if title in txt and score >= 0.55 and rect[0] < PANEL_MAX_X:
            if not is_green_text(img_np, rect):
                return True
    return False


def has_task(win, title):
    """判断任务追踪中是否存在指定任务。

    判定为追踪面板标题：位于窗口左区（x<PANEL_MAX_X）且非绿色链接文案，
    避免把居中对话框里的绿色选项误判为任务。
    只 OCR 追踪面板区域（全窗 2.2s → 区域约 0.9s）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, img_np = game_map.ocr_regions(img, (TRACK_REGION,))
    return has_task_in(items, img_np, title)


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
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = game_map.ocr_image(img)
    return find_task(group_tasks(items), title)


def task_signature(task):
    """任务信息签名（标题 + 首行内容），用于检测任务信息是否变化。

    只取首行内容：任务目标文案恒在首行，尾部行（剩余时间倒计时、
    偶发合并的下一任务行等 OCR 边界噪声）不参与比较，避免任务
    未变化时因尾部行抖动导致重复点击。
    比较前剔除标点与空白（如「…玄石」与「…玄石。」视为相同）。
    """
    first = task["lines"][0][0] if task["lines"] else ""
    return re.sub(r"[^\w]", "", f"{task['title']}|{first}")


def _keyword_fallback(items, img_np, keywords, arect):
    """在追踪面板区域内按关键词兜底查找绿字行（锚点内容采集失败时）。

    搜索范围：面板左区（x<PANEL_MAX_X，排除居中对话框）、锚点行下方
    （容 OCR 纵向抖动），不过滤标题行判定（子目标行可能被误判为标题行）。
    返回 (txt, rect) 或 None。
    """
    cands = []
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= PANEL_MAX_X:
            continue
        if rect[1] < arect[3] - 30:  # 锚点行下方（容抖动）
            continue
        if not any(kw in txt for kw in keywords):
            continue
        if not is_green_text(img_np, rect):
            continue
        cands.append((txt, rect))
    if not cands:
        return None
    # 优先含亮绿（链接文字/下划线）的行，取最靠上者
    bright = [c for c in cands if bright_green_ratio(img_np, c[1]) > 0] or cands
    return min(bright, key=lambda c: c[1][1])


def track_task(win, title, text=None, green_index=0, retries=4, retry_interval=1.5,
               green_order=None, green_picker=None, fallback_keywords=None,
               pre_items=None):
    """跟踪任务：点击任务内容的绿色带下划线文案。

    默认点击第一个绿色文案（green_index=0）；指定 text 时先在内容
    （含跨换行拼接）中查找该文案再点击所在行。
    green_order：顺序关键词列表（如 青龙→白虎→朱雀→玄武）。任务同时出现
    多个可点击绿字且须按序点击时使用：选中最早出现在列表中的关键词所在
    绿字行（跳过跨行合并逻辑，关键词行即目标）；关键词均未命中时退回
    第一个绿字。
    green_picker：精确点击回调 (items, img_np) → (x, y) 或 None。行级
    选择无法区分同一行内的多个绿字时使用（如绿字掩码 OCR 定位关键词）；
    返回坐标则直接点击，返回 None 时回退行级选择。
    fallback_keywords：内容采集兜底关键词列表（如五环子目标
    「替身/武痴/盗号贼/食婴鬼」）。锚点下方内容行采集为空或其中无绿字时，
    直接在追踪面板区域内按关键词匹配绿字行点击，规避内容采集的几何
    过滤与标题行误判。
    pre_items：调用方刚完成 OCR 的 (items, img_np)（如 follow_task 复用
    内层轮询的最近一帧），首次尝试跳过截图+OCR，省约 1.2s。
    任务刚领取时内容行可能尚未渲染（标题已出现、内容为空），
    内容为空时自动重试等待渲染。
    返回 (是否成功, 描述)。
    """
    last_img_np = None
    last_anchor_txt = ""
    last_content = []
    last_items = []
    last_arect = None
    for attempt in range(retries + 1):
        if attempt == 0 and pre_items is not None:
            items, img_np = pre_items
        else:
            img = win.capture(force_activate=False)
            if img is None:
                return False, "窗口截图失败"
            items, img_np = game_map.ocr_regions(img, OCR_REGIONS)

        # 任务锚点行：任意包含标题文案的行（兼容无方括号的标题）。
        # 优先取追踪面板左区内的行，避免居中对话框里的「五环」文案抢先
        anchor_idx = None
        for i, (txt, rect, score) in enumerate(items):
            if title in txt and score >= 0.55 and rect[0] < PANEL_MAX_X:
                anchor_idx = i
                break
        if anchor_idx is None:
            for i, (txt, rect, score) in enumerate(items):
                if title in txt and score >= 0.55:
                    anchor_idx = i
                    break
        if anchor_idx is None:
            return False, f"任务追踪中未找到「{title}」"
        arect = items[anchor_idx][1]
        last_img_np = img_np
        last_anchor_txt = items[anchor_idx][0]
        last_items = items
        last_arect = arect

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
            # 精确点击回调优先：调用方可用绿字掩码 OCR 等方式精确定位并返回
            # 点击坐标（如同一行内多个绿字须按序点击，行级选择无法区分）
            if green_picker is not None:
                pt = green_picker(items, img_np)
                if pt is not None:
                    human_click(win, pt[0], pt[1])
                    log(f"[任务追踪] 已点击「{title}」绿色文案（精确定位）@ ({pt[0]},{pt[1]})", "SYS")
                    return True, f"已跟踪「{title}」（精确定位 @({pt[0]},{pt[1]})）"
            # 优先选择含亮绿（链接文字/下划线）的行：
            # 暗绿可能是整行描述文字染色，其亮绿部分才是真正的链接
            pool = [it for it in green if bright_green_ratio(img_np, it[1]) > 0] or green
            tgt = pool[min(green_index, len(pool) - 1)]
            wrap_note = ""
            if green_order:
                # 按顺序关键词选择：最早命中的关键词所在绿字行（多绿字须按序点击）
                chosen = None
                for kw in green_order:
                    for it in pool:
                        if kw in it[0]:
                            chosen = it
                            break
                    if chosen is not None:
                        break
                if chosen is not None:
                    tgt = chosen
            else:
                # 跨行绿字：相邻绿行构成一个链接（如「那块」+「奇怪的石头」），
                # 选择亮绿像素最多的行点击（链接主体所在行，避开描述行的绿字噪声）
                cluster = [tgt]
                for other in green:
                    if other is not tgt and abs(other[1][1] - tgt[1][3]) <= 6 \
                            and abs(other[1][0] - tgt[1][0]) < 80:
                        cluster.append(other)
                if len(cluster) > 1:
                    tgt = max(cluster, key=lambda it: bright_green_ratio(img_np, it[1])
                              or green_ratio(img_np, it[1]))
                    wrap_note = " + ".join(f"「{t[0]}」" for t in cluster if t is not tgt)
                    wrap_note += "(跨行绿字)"
            cx, cy = green_text_center(img_np, tgt[1])
            human_click(win, cx, cy)
            log(f"[任务追踪] 已点击「{title}」绿色文案: {tgt[0]}"
                + (f" + {wrap_note}" if wrap_note else "")
                + f" @ ({cx},{cy})", "SYS")
            return True, f"已跟踪「{title}」（绿色文案: {tgt[0]}{' + ' + wrap_note if wrap_note else ''}）"

        # 兜底：锚点内容行采集为空或其中无绿字时，直接在追踪面板区域内
        # 按关键词匹配绿字行点击（子目标行可能被判定为标题行、与锚点
        # 几何关系不符或超出纵向范围，导致内容采集失败）
        if not green and fallback_keywords:
            fb = _keyword_fallback(items, img_np, fallback_keywords, arect)
            if fb is not None:
                txt, rect = fb
                cx, cy = green_text_center(img_np, rect)
                human_click(win, cx, cy)
                log(f"[任务追踪] 已点击「{title}」绿色文案（关键词兜底）: {txt} @ ({cx},{cy})", "SYS")
                return True, f"已跟踪「{title}」（关键词兜底: {txt}）"

        # 无绿字且内容为空：刚领取尚未渲染 → 重试
        if attempt < retries and not content:
            log(f"[任务追踪] 「{title}」内容尚未渲染（第 {attempt + 1} 次），等待后重试", "INFO")
            time.sleep(retry_interval)
            continue
        break

    # 失败诊断：输出识别到的任务信息与各行绿色像素占比，便于排查绿字漏检；
    # 另输出锚点下方面板区域内的其他 OCR 行（内容采集未收纳的行，
    # 如被误判为标题行的子目标行），便于排查关键词兜底为何未命中
    detail = " / ".join(f"「{t[0]}」{green_ratio(last_img_np, t[1]):.1%}"
                        for t in last_content) or "(无内容行)"
    if last_arect is not None:
        content_rects = [t[1] for t in last_content]
        near = [(t[0], t[1]) for t in last_items
                if t[1][0] < PANEL_MAX_X
                and t[1][1] >= last_arect[3] - 40
                and t[1][1] <= last_arect[3] + 200
                and t[1] not in content_rects]
        if near:
            detail += " / 锚点下方面板行: " + " | ".join(
                f"「{t[0]}」y={t[1][1]} 绿={green_ratio(last_img_np, t[1]):.1%}"
                for t in near)
    log(f"[任务追踪] 识别到「{title}」任务: 标题「{last_anchor_txt}」，"
        f"内容 {len(last_content)} 行: {detail}", "WARN")
    return False, f"「{title}」任务内容中未找到绿色文案"
