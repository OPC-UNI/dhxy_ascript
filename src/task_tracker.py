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

from .app_utils import log, human_click, norm_text
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
# 地图坐标模式（链接行专属）：链接行恒含「(x, y)」（如「去找大唐边境
# (358, 21)惯骗[」），描述行与计数碎片行不含；绿字行选择时坐标行优先，
# 避免点击目标在描述行/计数碎片行间漂移。兼容实测 OCR 变体：全角括号
# 「（140,39)」、右括号丢失「(178,111」、句点分隔「(178. 111)」
MAP_COORD_RE = re.compile(r"[\(（]\s*\d+\s*[.,，]\s*\d+\s*[\)）]?")
# 精确定位回调的「拒绝行级兜底」信号：调用方阶段内无法解析链接位置时
# 返回（如玄枢轶闻封印按序开启——行级兜底按最长实线段点击会点错机关、
# 顺序破坏任务卡死，2026-10-26 实测点南落到西）。track_task 收到后
# 等待重试并最终失败，不做行级选择点击。坐标为负、非任何真实屏幕坐标。
PICK_FAIL = (-1, -1)
# 跟踪轮询与点击的 OCR 区域（窗口相对坐标）：任务追踪面板 + 居中对话框区。
# 区域外（顶部 HUD、右侧图标列、底部栏）无需识别；全窗 OCR 实测 2.2s，
# 两个区域合计约 1.2s，轮询与点击提速近一半。
# 面板区右边界实测取 400（面板内容在 x<260，260~400 为空白背景）：
# 宽 260 的裁剪会确定性误读标题（实测「玄枢轶闻」读成「玄枢轶铁闻」
# 导致任务被误判消失），宽 300 及以上读对，留 400 余量。
TRACK_REGION = (0, 200, 400, 600)      # 任务追踪面板
DIALOG_REGION = (400, 270, 980, 600)   # 居中对话框区
OCR_REGIONS = (TRACK_REGION, DIALOG_REGION)
# 任务面板读取（read_tasks/get_task）的 OCR 区域：右缘放宽到 430——
# 与 PANEL_MAX_X 同宽会裁切标题框边缘导致标题误读（实测），
# group_tasks 仍按 x<PANEL_MAX_X 过滤，区域外行不参与
TRACK_OCR_REGION = (0, 200, 430, 600)
# 追踪面板差分门（性能 2026-10-27 引入；2026-10-28 增加节奏上限）：
# 2026-10-27 实测门区域 11/11 帧行走中严格稳定（不透明面板）；2026-10-28
# 实测面板渲染状态可变——半透明/浮字渲染下门区域背景随世界移动
# （逐行实测 83% 像素每轮变化、面板内容 bbox 仅 y204-272），严格相等
# 判定被击穿、面板 OCR 每轮空烧。故增加 TRACK_GATE_MIN_INTERVAL 节奏
# 上限：距上次 OCR 不足 2s 时一律复用结论（世界像素变化不再击穿门），
# 任务变化检测时延 ≤2s，精度由确认轮询（0.2s，同复用语义）保证。
# 只复用 items——像素级判定（绿字/进度键）的 img_np 一律由调用方贴
# 当前帧裁剪（TrackGate.paste_track），颜色判定不随复用而陈旧。
TRACK_GATE_REGION = (0, 200, 200, 400)
TRACK_GATE_MIN_INTERVAL = 2.0   # 面板 OCR 最小间隔（节奏上限，防世界像素击穿门）
TRACK_GATE_MAX_SKIP = 5.0       # 门持续命中时的强制全量 OCR 兜底（面板下部/门
                                # 区域外的变化不漏检，限频模式同 battle.FULL_CHECK_INTERVAL）


class TrackGate:
    """追踪面板差分门（follow 轮询与各任务领取校验循环共用）。

    一个任务阶段一个实例（如 follow_task 每轮新建）。战斗帧不要喂给
    ocr()——战斗 UI 覆盖面板会污染门缓存（调用方须先做战斗判定）。
    """

    def __init__(self):
        self.crop = None        # 上轮门区域像素
        self.items = None       # 上轮面板 OCR 结论
        self.ocr_at = 0.0       # 上轮面板 OCR 时间（兜底限频）

    def ocr(self, img):
        """面板 OCR 三态门：节奏上限 → 像素相等 → 全量 OCR。

        1. 距上次 OCR 不足 TRACK_GATE_MIN_INTERVAL：一律复用（世界像素
           变化/面板动画不击穿门，任务变化检测时延 ≤ 该间隔）
        2. 门区域像素与上次 OCR 帧相等且未超 MAX_SKIP：复用（OCR 确定性，
           逐字等价）
        3. 其余：全量面板 OCR（含 MAX_SKIP 兜底强制刷新）
        """
        gl, gt, gr, gb = TRACK_GATE_REGION
        crop = np.asarray(img)[gt:gb, gl:gr]
        now = time.time()
        if self.crop is not None and now - self.ocr_at < TRACK_GATE_MIN_INTERVAL:
            return self.items
        if (self.crop is not None and (crop == self.crop).all()
                and now - self.ocr_at < TRACK_GATE_MAX_SKIP):
            return self.items
        items, _ = game_map.ocr_region(img, TRACK_REGION)
        self.crop, self.items, self.ocr_at = crop, items, now
        return items

    def paste_track(self, img, img_np):
        """把当前帧面板区域裁剪贴入掩码帧（像素判定用，不复用陈旧像素）。"""
        l, t, r, b = TRACK_REGION
        img_np[t:b, l:r] = np.asarray(img)[t:b, l:r]


def green_mask(region, min_g):
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
    return float(green_mask(region, min_g).mean())


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


def _col_runs(cols):
    """列号序列拆为连续段，返回 [(start, end), ...]（升序）。"""
    runs = []
    if not len(cols):
        return runs
    start = prev = cols[0]
    for c in cols[1:]:
        if c != prev + 1:
            runs.append((int(start), int(prev)))
            start = c
        prev = c
    runs.append((int(start), int(prev)))
    return runs


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
        # OCR 框底常贴文字底，下划线落在框底下方 0~6px（五环折行链接
        # 实测续行下划线在框底 4px 外、旧 ±3px 窗口漏检——2026-10-14）。
        # 窗口上探到 -10：OCR 框底可能比文字底低出数 px（下划线落在
        # 框内框底上方，实测链接行框底 291、下划线 287 被旧 -3 窗口
        # 漏检——2026-10-23 玄枢轶闻）。实线连排阈值保证上方邻行文字
        # 顶部不会误判。规则：框底 -10~+8px 内找含 ≥20px 连续亮绿列段
        # 的行（文字行笔画连排实测 ≤13px，下划线实测 ≥30px），自上而下
        # 取首个命中行，点击列段中心上方 2px。
        y0 = max(0, rect[3] - 10)
        y1 = min(img_np.shape[0], rect[3] + 9)
        x0 = max(0, rect[0] - 2)
        x1 = min(img_np.shape[1], rect[2] + 4)
        mask = green_mask(img_np[y0:y1, x0:x1], GREEN_BRIGHT_MIN)
        for y_row in range(mask.shape[0]):
            cols = np.where(mask[y_row])[0]
            if cols.size < 20 or _longest_run_len(cols) < 20:
                continue
            cx = int(x0 + _longest_run_center(cols))
            cy = int(y0 + y_row - 2)  # 下划线上方 2px，容 ±2px 点击抖动
            return cx, cy
    # 亮绿/绿色像素回退：链接是行尾部分（描述+链接同行，如「去找大唐
    # 边境(193,144)惯骗」仅「惯骗」可点击）——下划线未检测到时取最右侧
    # 文字块中心（≥8px 段优先），落在行尾链接上而非描述行中间
    # （五环折行链接 2026-10-14 实测点中描述文字 (101,253)）。
    # 纵向取绿色行块底部上方 2px（与下划线点击规则同款）：链接可点区
    # 在文字行下半部，行块纵向中心/顶部点击会落空——实测玄枢轶闻领取后
    # 首点击 (121,273) 点在链接顶端无反应，全部成功点击均贴近行底
    # （2026-10-23）。对话框选项（prefer_underline=False）保持纵向中心。
    for min_g in (GREEN_BRIGHT_MIN, GREEN_MIN_G):
        mask = green_mask(region, min_g)
        cols = np.where(mask.any(axis=0))[0]
        rows = np.where(mask.any(axis=1))[0]
        if cols.size:
            runs = _col_runs(cols)
            tail = [r for r in runs if r[1] - r[0] + 1 >= 8] or runs
            cx = int(rect[0] + (tail[-1][0] + tail[-1][1]) // 2)
            if prefer_underline:
                cy = max(rect[1], int(rect[1] + rows.max() - 2))
            else:
                cy = int(rect[1] + (rows.min() + rows.max()) // 2)
            return cx, cy
    return (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2


def has_underline(img_np, rect):
    """文字框底边 -10~+8px 内是否存在实线亮绿下划线（≥20px 连续列段）。

    下划线是任务追踪链接的专属标记（可能只覆盖行尾的可点击部分）：
    描述行与链接行同为绿字时用它区分（客栈答题任务描述行「洛阳城正
    举办幸运五十二答题」与链接行「大会，去找答题大赛主办人」均绿，
    按首个绿行点击点错——实测 2026-10-13）。检测规则与
    green_text_center 的下划线定位一致（窗口一致、均自上而下扫描）：
    文字行笔画连排实测 ≤13px、下划线实测 ≥30px，20px 阈值可区分；
    下划线可落在框底 0~6px 外（折行续行实测 4px 外——2026-10-14），
    故窗口下探到 +8；窗口上探到 -10（OCR 框底可能比文字底低出数 px，
    下划线在框内被旧 -3 窗口漏检——2026-10-23 玄枢轶闻实测框底 291、
    下划线 287）；邻行文字顶部（行距仅 ~18px）会落进检测窗口但
    连排长度不达标，不构成误判。
    """
    y0 = max(0, rect[3] - 10)
    y1 = min(img_np.shape[0], rect[3] + 9)
    x0 = max(0, rect[0] - 2)
    x1 = min(img_np.shape[1], rect[2] + 4)
    mask = green_mask(img_np[y0:y1, x0:x1], GREEN_BRIGHT_MIN)
    for y_row in range(mask.shape[0]):
        cols = np.where(mask[y_row])[0]
        if cols.size >= 20 and _longest_run_len(cols) >= 20:
            return True
    return False


def _is_title_line(txt):
    """标题行判定：短行且含方括号，且括号段内不含数字。

    任务标题如 洛川乱渊[水啸神踪]；内容行的回合计数如 [1/5]、
    [1/（OCR 截断）括号段内含数字，须视为内容行。
    标题左括号可能被 OCR 丢失（如 五环[五环] 读成 五环五环]），
    短行只含右括号且无数字时也按标题处理。
    追踪链接行（如 去找大唐境内(223, 21)武痴[1/5]）括号段内含数字；
    OCR 截断只剩未闭合左括号时（…武痴[）也不按标题处理，否则
    内容采集在链接行处中断（实测五环内容 0 行即由此引起）。
    标题行也会被 OCR 截断闭括号（实测「寻童归家[客栈任务」读不到
    闭括号——find_task 漏任务而 has_task 命中，客栈每轮「任务从未
    出现」假失败的根因）：标题截断与链接截断的区分信号是省略号
    （游戏对长链接行做 … 截断，标题短行无省略号）与地图坐标。
    """
    # 计数碎片行（如「1/51]」，链接行计数段被 OCR 拆出且左括号丢失）：
    # 判为内容行，否则误判为标题行会切断任务块内容采集（五环边界）
    if re.match(r"^\d+/\d+\]?$", txt.strip()):
        return False
    if len(txt) > 20:
        return False
    if not re.search(r"[\[［【\]］】]", txt):
        return False
    # 括号段内含数字（如 [1/5]）→ 内容行
    if re.search(r"[\[［【][^\]］】]*\d", txt):
        return False
    # 左括号无右括号闭合：带省略号（游戏对长链接的截断，如「…武痴[」）
    # 或含地图坐标的 → 链接截断，判内容行；否则（短行无坐标，如
    # 「寻童归家[客栈任务」）→ 标题闭括号被 OCR 截断，判标题行
    if re.search(r"[\[［【][^\]］】]*$", txt) \
            and (txt.strip().startswith("…") or MAP_COORD_RE.search(txt)):
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
        # 内容行按视觉自上而下排序：OCR 输出顺序不稳定（同高度碎片行
        # 会随机互换，实测五环链接行「…惯骗[」与计数行「4/5]」交替
        # 翻转，任务签名随之抖动导致重复点击）；纵向坐标排序后签名与
        # 点击目标均稳定
        t["lines"].sort(key=lambda ln: ln[1][1])
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
    items, _ = game_map.ocr_regions(img, (TRACK_OCR_REGION,))
    return group_tasks(items)


def task_count(win):
    """任务追踪中的任务数量。"""
    return len(read_tasks(win))


def has_task_in(items, img_np, title, fallback_texts=()):
    """在 OCR 结果中判断追踪面板是否存在指定任务（判定规则见 has_task）。

    fallback_texts：标题 OCR 不可靠时的兜底匹配串（修罗实测：标题
    「修罗任务[常规玩法]」的彩色徽标前缀导致区域 OCR 整行漏检，
    has_task 误报失败——用内容链接行「前往…消灭修罗」兜底，该行
    区域 OCR 检测稳定且仅在任务存在时出现；允许绿色（链接行本身
    是绿色，标题判定才排除绿色）。
    """
    for txt, rect, score in items:
        if title in txt and score >= 0.55 and rect[0] < PANEL_MAX_X:
            if not is_green_text(img_np, rect):
                return True
    for ft in fallback_texts:
        for txt, rect, score in items:
            if ft in txt and score >= 0.55 and rect[0] < PANEL_MAX_X:
                return True
    return False


def has_task(win, title, fallback_texts=()):
    """判断任务追踪中是否存在指定任务。

    判定为追踪面板标题：位于窗口左区（x<PANEL_MAX_X）且非绿色链接文案，
    避免把居中对话框里的绿色选项误判为任务。
    fallback_texts：标题 OCR 漏检时的兜底匹配串（见 has_task_in）。
    只 OCR 追踪面板区域（全窗 2.2s → 区域约 0.9s）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    items, img_np = game_map.ocr_regions(img, (TRACK_REGION,))
    return has_task_in(items, img_np, title, fallback_texts)


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
    items, _ = game_map.ocr_regions(img, (TRACK_OCR_REGION,))
    return find_task(group_tasks(items), title)


def task_signature(task):
    """任务信息签名（标题 + 首行内容），用于检测任务信息是否变化。

    只取首行内容：任务目标文案恒在首行，尾部行（剩余时间倒计时、
    偶发合并的下一任务行等 OCR 边界噪声）不参与比较，避免任务
    未变化时因尾部行抖动导致重复点击。
    比较前剔除标点与空白（如「…玄石」与「…玄石。」视为相同）。
    """
    first = task["lines"][0][0] if task["lines"] else ""
    return norm_text(f"{task['title']}|{first}")


def _keyword_fallback(items, img_np, keywords, arect):
    """在追踪面板区域内按关键词兜底查找绿字行（锚点内容采集失败时）。

    搜索范围：面板左区（x<PANEL_MAX_X，排除居中对话框）、锚点行下方
    （容 OCR 纵向抖动；arect 为 None 时不限纵向——标题行被区域 OCR
    整行漏检、锚点定位失败时用）；不过滤标题行判定（子目标行可能被
    误判为标题行）。返回 (txt, rect) 或 None。
    """
    cands = []
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= PANEL_MAX_X:
            continue
        if arect is not None and rect[1] < arect[3] - 30:  # 锚点行下方（容抖动）
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

        # 任务锚点行：优先取标题格式行（含方括号短行，_is_title_line），
        # 无标题格式行再回退任意包含标题文案的行（兼容无方括号标题）。
        # 修罗实测：「狂风修罗」（无方括号的干扰行）抢先成为锚点后，
        # 内容采集在真实标题「修罗任务[常规玩法]」处中断，内容恒 0 行
        # → 4 次渲染重试空转 ~8s 后放弃整轮。优先取追踪面板左区内的行，
        # 避免居中对话框里的文案抢先
        anchor_idx = None
        for i, (txt, rect, score) in enumerate(items):
            if _is_title_line(txt) and title in txt and score >= 0.55 \
                    and rect[0] < PANEL_MAX_X:
                anchor_idx = i
                break
        if anchor_idx is None:
            for i, (txt, rect, score) in enumerate(items):
                if title in txt and score >= 0.55 and rect[0] < PANEL_MAX_X:
                    anchor_idx = i
                    break
        if anchor_idx is None:
            for i, (txt, rect, score) in enumerate(items):
                if _is_title_line(txt) and title in txt and score >= 0.55:
                    anchor_idx = i
                    break
        if anchor_idx is None:
            for i, (txt, rect, score) in enumerate(items):
                if title in txt and score >= 0.55:
                    anchor_idx = i
                    break
        if anchor_idx is None:
            # 标题行被区域 OCR 漏检（修罗实测：彩色徽标前缀致整行缺失）
            # 时，用关键词兜底直接点面板左区的绿字链接行（内容行即
            # 跟踪目标，无锚点纵向约束）——否则跟踪失败整轮重试
            if fallback_keywords:
                fb = _keyword_fallback(items, img_np, fallback_keywords, None)
                if fb is not None:
                    txt, rect = fb
                    cx, cy = green_text_center(img_np, rect)
                    human_click(win, cx, cy)
                    log(f"[任务追踪] 已点击「{title}」绿色文案（标题漏检兜底）: "
                        f"{txt} @ ({cx},{cy})", "SYS")
                    return True, f"已跟踪「{title}」（标题漏检兜底: {txt}）"
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
        # 内容行按视觉自上而下排序（OCR 行序不稳定，五环链接行与计数行
        # 实测随机互换，点击目标随之摆动）；排序后首个绿字恒为链接行
        content.sort(key=lambda it: it[1][1])
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
                if pt == PICK_FAIL:
                    # 调用方判定当前帧无法安全定位（如玄枢轶闻封印阶段
                    # 链接下划线不齐）：等待重试而不是行级兜底点击——
                    # 兜底点错会把任务卡死，比晚几秒点更糟
                    if attempt < retries:
                        log(f"[任务追踪] 「{title}」精确定位暂不可用"
                            f"（第 {attempt + 1} 次），等待后重试", "INFO")
                        time.sleep(retry_interval)
                        continue
                    return False, f"「{title}」精确定位不可用，已拒绝行级兜底点击"
                if pt is not None:
                    human_click(win, pt[0], pt[1])
                    log(f"[任务追踪] 已点击「{title}」绿色文案（精确定位）@ ({pt[0]},{pt[1]})", "SYS")
                    return True, f"已跟踪「{title}」（精确定位 @({pt[0]},{pt[1]})）"
            # 优先选择含亮绿（链接文字/下划线）的行：
            # 暗绿可能是整行描述文字染色，其亮绿部分才是真正的链接
            pool = [it for it in green if bright_green_ratio(img_np, it[1]) > 0] or green
            # 坐标行优先（仅无 green_order 的任务）：链接行恒含地图坐标
            # (x, y)（如「去找大唐边境(358, 21)惯骗[」），描述行与计数
            # 碎片行不含——五环实测点击目标在描述行 (54,285) 与链接
            # 下划线 (197,268) 间漂移。按顺序点击的任务（客栈四象封印）
            # 保持关键词选择，不受坐标约束
            coord_pool = None
            if green_order is None:
                # 下划线优先：下划线是链接专属标记，带下划线的行才是点击
                # 目标——描述行同为绿字时按坐标行/首个绿行点击会点错
                # （客栈答题描述行 2026-10-13、五环折行链接 2026-10-14
                # 实测）。折行链接（「…(166,222)食婴」+「鬼手下的手下」）
                # 坐标行的 OCR 框可能裁掉行尾链接、下划线落框外检测不到，
                # 而续行下划线完整——先按下划线选行，再在其中按坐标收紧
                underline_pool = [it for it in pool
                                  if has_underline(img_np, it[1])]
                if underline_pool:
                    pool = underline_pool
                coord_pool = [it for it in pool if MAP_COORD_RE.search(it[0])]
                if coord_pool:
                    pool = coord_pool
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
                    # 相邻判定取 other.top <= tgt.bottom + 6：换行链接的
                    # OCR 框可能相互重叠（实测描述行框底 276、链接行框顶
                    # 267，重叠 9px），abs() 会把重叠行误判为不相邻、
                    # 链接行不参与选择——2026-10-23 玄枢轶闻点击失败根因
                    if other is not tgt and other[1][1] <= tgt[1][3] + 6 \
                            and abs(other[1][0] - tgt[1][0]) < 80:
                        cluster.append(other)
                if len(cluster) > 1:
                    # 坐标行优先时只与坐标行聚簇：描述行/计数碎片行的
                    # 亮绿更多时会抢走点击目标（点击漂移根因）
                    if coord_pool:
                        cluster = [it for it in cluster if MAP_COORD_RE.search(it[0])]
                    if len(cluster) > 1:
                        # 跨行绿字簇内：下划线行优先（链接专属标记，折行
                        # 链接的下划线常只落在续行上），再比亮绿占比
                        tgt = max(cluster, key=lambda it: (
                            has_underline(img_np, it[1]),
                            bright_green_ratio(img_np, it[1])
                            or green_ratio(img_np, it[1])))
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

        # 无绿字重试：内容为空（刚领取尚未渲染）或过渡帧（实测任务变化
        # 瞬间面板重渲染，内容行置信度骤降且绿字判定失败——玄枢轶闻
        # 变化确认跳出复用确认帧，0.8%/0.0% 置信度致整轮白失败）。
        # 过渡帧重试限 2 次：真无绿字状态快速失败，由外层轮次重试兜底
        if attempt < retries and not content:
            log(f"[任务追踪] 「{title}」内容尚未渲染（第 {attempt + 1} 次），等待后重试", "INFO")
            time.sleep(retry_interval)
            continue
        if content and attempt < 2:
            log(f"[任务追踪] 「{title}」内容可见但无绿字（第 {attempt + 1} 次），"
                f"可能为过渡帧，等待后重试", "INFO")
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
