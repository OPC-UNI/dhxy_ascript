"""对话框处理共享组件：跨任务模式（玄枢轶闻/五环/客栈/修罗）共用的
对话框检测、选项点击与文案常量。

从 xuanshu.py 抽出以便各任务模块解耦（kezhan 等不再依赖 xuanshu 的
流程级代码）；日志带 prefix 参数（各模块传自己的任务名）。

注意：不要在本模块 import xuanshu 或任何任务模块——battle.py 等
会在运行时反向导入本模块的函数（懒导入），循环依赖只允许在运行时。
"""
import time
from collections import Counter
from functools import lru_cache
import difflib

from .app_utils import log, human_click, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（选项坐标缓存记录）
from . import task_tracker  # 面板左区边界与绿色文字判定
from . import bag  # 物品栏开关（close_scene_panels 用）
from . import map as game_map  # 地图开关（close_scene_panels 用）


# ---- 共享对话框常量 ----
# 每日次数已满文案（玄枢轶闻/客栈同款）
DAILY_LIMIT_TEXT = "少侠已经完成5次任务，请明天再来吧"
DAILY_LIMIT_INFO = "今日任务次数已满（请明天再来）"
# 战斗失败/平局对话框文案（出现任一则重新点击任务追踪绿字推进）
DIALOG_LOSE_TEXTS = ["你输了", "平局"]
# 跳过对话的点击次数与屏幕中间位置
DIALOG_SKIP_CLICKS = 4               # 跳过对话的点击次数上限
SCREEN_CENTER_POS = (689, 415)       # 屏幕中间（游戏窗口中心，点击推进对话）
# 序号点击同文案防抖间隔（秒）：点击落空（对话框未推进）时下一轮询
# 会再次命中同一问题文案，防抖避免每秒一次的连点刷屏（玄枢轶闻
# 「太阳的别称」2026-11-07 实测连点 47 次不推进）；点击生效时问题
# 文案随之消失/变化，防抖不影响正常推进，同题重问（下一轮任务）
# 间隔远超该窗口不受影响
INDEX_CLICK_DEBOUNCE = 3.0
_INDEX_LAST_CLICK = {}    # (prefix, dtxt, idx) -> 上次点击时间戳
_INDEX_LAST_WARN = {}     # (prefix, dtxt, idx) -> 上次未检出告警时间戳（限频用）


def green_option_center(img_np, rect, opt_len, line_len, target_center=None):
    """选项点击中心：全行绿字中心；OCR 行合并了相邻文字时（line_len
    明显长于选项长度）按字符比例取选项自身中心。

    实测「玄枢轶闻我想领取通用玄宝」合并成一行，全行绿字中心 520
    偏离选项自身中心 470 点不中，且毒化高性能缓存（客栈/五环/修罗/
    玄枢轶闻共用的统一修正）。opt_len/line_len 建议传规范化后长度。

    target_center：目标选项在合并行文本中的中心偏移（字符数）；None
    按目标为首个选项处理（与旧行为一致）。目标不是首个选项时（如
    「了解任务详情接取客栈任务」中取后者），旧实现仍取行左段会点到
    相邻选项——按 target_center 在行内的比例取段才是目标本身。
    纵向合并（两选项堆叠成高框）：<30 字选项绝不换行（用户规则
    2026-10-27），高框必为多选项堆叠，目标行 = 其在合并文本中的序位；
    ≥30 字长选项可能折行，维持取上部选项中心的旧行为。
    """
    cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
    if line_len > opt_len + 2:
        center = opt_len / 2 if target_center is None else target_center
        pos_frac = min(center / line_len, 1.0)
        cx = rect[0] + int((rect[2] - rect[0]) * pos_frac)
        if rect[3] - rect[1] > 30:
            if opt_len < 30:
                cy = rect[1] + int((rect[3] - rect[1]) * pos_frac)
            else:
                cy = rect[1] + int((rect[3] - rect[1])
                                   * min(opt_len / line_len, 1.0) / 2)
    return cx, cy


def _option_target_center(txt, text):
    """目标选项在 OCR 行文本中的中心偏移（字符数），供 green_option_center
    按比例取段：精确命中用子串位置；模糊命中（漏字/误读）用最长公共
    块定位。"""
    idx = txt.find(text)
    if idx >= 0:
        return idx + len(text) / 2
    m = difflib.SequenceMatcher(None, txt, text).find_longest_match(
        0, len(txt), 0, len(text))
    return m.a + m.size / 2


def _click_green_option_once(win, items, img_np, text, prefix=""):
    """单帧检测并点击指定文案的绿色选项；点击成功返回 True。

    限定对话框区域（x≥PANEL_MAX_X）：任务追踪面板的绿字链接可能包含
    相同文案（如链接「去找小胖丫帮旺小乔试试服装」含「试试」），
    面板行 y 序在前会被先遍历到，不限区域会误点面板链接。
    prefix：日志前缀（如「客栈」「玄枢轶闻」）。
    """
    tag = f"[{prefix}] " if prefix else ""
    exact = []
    fuzzy = []
    for txt, rect, score in items:
        if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X \
                or not task_tracker.is_green_text(img_np, rect):
            continue
        if text in txt:
            exact.append((txt, rect, _option_target_center(txt, text)))
        elif _fuzzy_contains(txt, text, threshold=0.8):
            # 行模糊匹配兜底：选项 OCR 常漏字/误读（实测「指明伤口一事」
            # 读成「指明伤口一」、「指明绳结之事」读成「指明沉船」，精确
            # 匹配全漏；同屏选项行序随机，只能按文本定位）。阈值 0.8
            # 容忍 6 字选项漏 1~2 字，与相邻选项互串比 ≤0.5 不会误配
            fuzzy.append((txt, rect, _option_target_center(txt, text)))
    tgt = (exact or fuzzy)
    if not tgt:
        # 长选项被区域 OCR 拆成同行多框的兜底（实测 2026-10-27「玄闻辨真
        # （玄枢轶闻）」拆成「玄闻辨真」+「(玄枢轶闻)」两框，单框精确/
        # 模糊均不命中，对话框永久不推进）：同行绿框（y 中心差 ≤10px、
        # 相邻水平间隙 ≤30px）按 x 序拼接后匹配；命中按字符比例定位
        # 选项中心（复用 green_option_center 的合并行定位逻辑）。
        # <30 字选项不换行（用户规则），同 y 行拼接不会跨选项误拼。
        rows_by_y = {}
        for txt, rect, score in items:
            if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X \
                    or not task_tracker.is_green_text(img_np, rect):
                continue
            cy = (rect[1] + rect[3]) / 2
            key = next((k for k in rows_by_y if abs(k - cy) <= 10), None)
            if key is None:
                key = cy
                rows_by_y[key] = []
            rows_by_y[key].append((txt, rect))
        for rows in rows_by_y.values():
            if len(rows) < 2:
                continue
            rows.sort(key=lambda r: r[1][0])
            merged = []
            for txt, rect in rows:
                if merged and rect[0] - merged[-1][1][2] > 30:
                    break  # 间隙过大：后续框属另一选项，不再拼接
                merged.append((txt, rect))
            if len(merged) < 2:
                continue
            joined = "".join(t for t, _ in merged)
            if text not in joined and not _fuzzy_contains(joined, text, threshold=0.8):
                continue
            l = min(r[0] for _, r in merged)
            t = min(r[1] for _, r in merged)
            r = max(r[2] for _, r in merged)
            b = max(r[3] for _, r in merged)
            rect = [l, t, r, b]
            center = _option_target_center(joined, text)
            cx, cy = green_option_center(img_np, rect, len(text), len(joined), center)
            human_click(win, cx, cy)
            app_utils.fixed_pos_set(f"option_{text}", win, cx, cy)
            log(f"{tag}已点击对话框绿色选项「{joined}」@({cx},{cy})（同行拆框拼接）", "SYS")
            move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
            return True
        return False
    txt, rect, center = tgt[0]
    cx, cy = green_option_center(img_np, rect, len(text), len(txt), center)
    human_click(win, cx, cy)
    # 高性能：记录选项固定位置（按文案做键，各任务模块共用；
    # 位置漂移时由本路径自然刷新）。键为选项文案，互为子串的
    # 不同选项会共用一键互相覆写（实测「玄枢轶闻」与「玄闻辨真
    # （玄枢轶闻）」2026-10-25 领取必现点偏）——新增选项文案须
    # 避免与其他选项构成子串关系，或用完整文案
    app_utils.fixed_pos_set(f"option_{text}", win, cx, cy)
    log(f"{tag}已点击对话框绿色选项「{txt}」@({cx},{cy})", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _green_option_rows(img_np, qrect, y_max_offset=100, max_row_height=22):
    """定位问题行下方的绿色选项行，返回每行 (cx, cy) 列表（像素级）。

    扫描条带取问题行 x 范围（±10px）与问题行下方 y_max_offset：
    选项为绿色文字+下划线，逐行聚合绿色像素列，间隔 ≤3px 的连续
    行合并为一行；过滤过矮（噪点/下划线，<8px）与过高（相邻选项
    行合并，>max_row_height）的行、绿色像素过少（<10 个）的行。
    行 x 中心取该行绿色像素列的中位数（抗条带边缘杂散绿点）。
    条带异常或行数不足时由调用方不点击，等下一轮询重试。
    """
    import numpy as np
    if img_np is None:
        return None
    l = max(0, qrect[0] - 10)
    r = min(img_np.shape[1], qrect[2] + 10)
    t = qrect[3]
    b = min(img_np.shape[0], t + y_max_offset)
    if r <= l or b <= t:
        return None
    region = img_np[t:b, l:r].astype(int)
    mask = task_tracker.green_mask(region, task_tracker.GREEN_BRIGHT_MIN)
    runs = []  # (y0, y1, 行内绿色像素列序号列表)
    for yy in range(mask.shape[0]):
        cols = np.where(mask[yy])[0]
        if cols.size == 0:
            continue
        if runs and yy - runs[-1][1] <= 3:
            runs[-1] = (runs[-1][0], yy, runs[-1][2] + cols.tolist())
        else:
            runs.append((yy, yy, cols.tolist()))
    rows = []
    for y0, y1, xs in runs:
        h = y1 - y0 + 1
        if h < 8 or h > max_row_height or len(xs) < 10:
            continue
        rows.append((l + int(np.median(xs)), t + (y0 + y1) // 2))
    return rows


def _click_dialog_option_by_index(win, items, img_np, dtxt, idx, prefix=""):
    """按序号点击问题下方的绿色选项行（选项无法 OCR 识别时使用）。

    定位问题文案所在行，在问题行下方条带内按绿色像素定位选项行，
    点击第 idx 行的绿色像素中心。旧实现按问题行左缘固定偏移点击：
    选项文字与问题行左缘的缩进不一致时会点空——「太阳的别称」
    选项行 x=439 起、固定偏移点落在 x=438，2026-11-07 实测连点
    47 次不推进（11-02 同日复现 6 连点，10-31 单点侥幸命中）。
    检测不到足够的选项行时不点击（避免盲点）。
    同文案点击防抖 INDEX_CLICK_DEBOUNCE 秒：点击生效问题文案随即
    变化，防抖只拦落空后的连点；OCR 路径（文案点击）不受防抖影响，
    落空等待期内 OCR 仍有机会读到选项推进对话。
    """
    tag = f"[{prefix}] " if prefix else ""
    qrect = None
    for txt, rect, _ in items:
        if _fuzzy_contains(txt, dtxt):
            qrect = rect
            break
    if qrect is None:
        return False
    key = (prefix, dtxt, idx)
    now = time.time()
    if now - _INDEX_LAST_CLICK.get(key, 0.0) < INDEX_CLICK_DEBOUNCE:
        return False  # 上一击未推进（问题文案仍在）：不连点，等 OCR 路径
    rows = _green_option_rows(img_np, qrect)
    if not rows or len(rows) < idx:
        if now - _INDEX_LAST_WARN.get(key, 0.0) >= 10.0:
            _INDEX_LAST_WARN[key] = now
            log(f"{tag}问题「{dtxt}」下方仅检测到 {len(rows or [])} 行绿色选项"
                f"（需第 {idx} 行），不点击", "WARN")
        return False
    cx, cy = rows[idx - 1]
    _INDEX_LAST_CLICK[key] = now
    human_click(win, cx, cy)
    log(f"{tag}已按序号点击第 {idx} 项选项 @({cx},{cy})", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


_last_center_click_at = 0.0  # 最近一次点屏幕中间的时间（跨组件防双点）


def last_center_click_at():
    """最近一次「点屏幕中间」的时间戳。

    供其他组件（如五倍显形镜处理器）判断剧情兜底是否刚点过屏：同一次
    对话由两个组件各自点一次会把第二次点击打到世界画面（2026-11-11
    14:11 白龙马对话框一次即关，第二次点击让人物走动、任务按钮模板
    窗口空耗——当日 5/26 次模板未命中）。
    """
    return _last_center_click_at


def _click_screen_center(win, times=DIALOG_SKIP_CLICKS, prefix=""):
    """点击屏幕中间若干次（推进 NPC 对话）。

    固定次数点击——多屏对话无需枚举后续文案，命中首屏文案即连点推进
    （漏推进由下一轮询 OCR 自愈；逐次校验文案会卡住多屏对话，实测废弃）。
    连点 >1 次时首次点击前确认标准对话框在场，之后每次点击前复检模板，
    对话框已消失即停——对话页数少于连点次数时，多余点击落到游戏世界会
    取消自动寻路、人物停下不走（2026-11-11 用户实机，同 StoryDialogDismisser
    的寻路保护）。非标准对话框（模板不在场）保持旧的固定连点行为，
    单次点击不做复检（无多余点击，零开销）。
    """
    global _last_center_click_at
    tag = f"[{prefix}] " if prefix else ""
    cx, cy = SCREEN_CENTER_POS
    tracked = False
    _last_center_click_at = time.time()
    if times > 1:
        img = win.capture(force_activate=False)
        tracked = img is not None \
            and dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD
    clicked = 0
    for _ in range(times):
        human_click(win, cx, cy)
        clicked += 1
        time.sleep(0.3)  # 对话推进间隔（漏推进由下一轮询 OCR 自愈）
        if tracked and clicked < times:
            img = win.capture(force_activate=False)
            if img is None or dialog_frame_score(img) < DIALOG_FRAME_THRESHOLD:
                break  # 对话已关闭：剩余点击会落到世界画面
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    log(f"{tag}已点击屏幕中间 {clicked} 次（推进对话）", "SYS")


@lru_cache(maxsize=None)
def _char_counts(s):
    """字符串字符多重集（needle 静态、同一行跨轮询重复，缓存供上界预过滤）。"""
    return Counter(s)


def _fuzzy_contains(text, needle, threshold=0.85):
    """模糊子串匹配：text 中是否存在与 needle 相似度达标的窗口。

    对话框文案的 OCR 结果可能有个别字误读，精确匹配会漏掉；
    以长度 ±3 的滑动窗口做相似度比对（抗误读与多/少字）。
    预过滤均为 difflib 保证的上界（多重集重合度 / real_quick_ratio /
    quick_ratio），低于阈值才跳过，不改命中语义（实测 15 万随机对
    与旧实现 0 不一致）。
    """
    n = len(needle)
    if n == 0:
        return True
    # 多重集上界：窗口 ratio = 2*M/(size+n) ≤ 2*O/(min_size+n)，
    # M 为匹配字符数、O 为整行与 needle 的字符重合度；上界低于阈值整行跳过
    min_size = max(1, n - 3) if n <= len(text) else len(text)
    overlap = sum((_char_counts(text) & _char_counts(needle)).values())
    if 2 * overlap / (n + min_size) < threshold:
        return False
    if n > len(text):
        sm = difflib.SequenceMatcher(None, text, needle)
        return sm.real_quick_ratio() >= threshold and sm.ratio() >= threshold
    for size in (n - 3, n - 2, n - 1, n, n + 1, n + 2, n + 3):
        if size <= 0 or size > len(text):
            continue
        for i in range(0, len(text) - size + 1, max(1, size // 2)):
            window = text[i:i + size]
            sm = difflib.SequenceMatcher(None, window, needle)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            if sm.ratio() >= threshold:
                return True
    return False


def _dialog_text_present(items, dtxt):
    """对话框文案是否出现：逐行与全文拼接（无空格）做模糊匹配。"""
    joined = "".join(t[0] for t in items)
    return any(_fuzzy_contains(line, dtxt)
               for line in [t[0] for t in items] + [joined])


def _norm_dialog_present(items, dtxt):
    """规范化（去标点）后的对话框文案模糊匹配（见 norm_text）。"""
    norm_items = [(norm_text(t), r, s) for t, r, s in items]
    return _dialog_text_present(norm_items, norm_text(dtxt))


def close_scene_panels(win, prefix="", quiet=False):
    """关闭场景中可能打开的界面：小地图、世界地图、物品栏。

    prefix：日志前缀（如「客栈」「玄枢轶闻」）。
    quiet：周期性兜底检查用——地图「未打开不点击」路径不再打日志。
    """
    tag = f"[{prefix}] " if prefix else ""
    game_map.close_maps(win, quiet=quiet)
    if bag.is_bag_open(win):
        ok, info = bag.close_bag(win)
        log(f"{tag}物品栏{info}", "SYS" if ok else "WARN")


# ---- 对话框模板检测（性能方案，2026-11-06 验证中） ----
# 用对话框特征块模板判断对话框是否在场：未命中即跳过对话区 OCR
# （follow 内层轮询每轮对话区 OCR ~0.4s，无对话场景空烧）。模板由
# 用户截取（res/img/dialog_frame.png，83x56）；预期覆盖巫医/修罗/
# 客栈/五倍领取与开打等标准对话面板；不覆盖：客栈奇遇、客栈/五环
# 给予面板、队伍面板、申请加入面板（无标准对话框边框，仍走原
# OCR 路径）。诊断页「测试对话框识别」按钮可对照有/无对话框场景
# 的分数与耗时。阈值校准：巫医 NPC 对话框实测 0.795（2026-11-07
# 用户真图，原 0.8 门静默挡掉战斗线程巫医检查→任务侧 12s 超时兜底
# 自点，当天 2/2 巫医回合超时）→ 降到 0.79；无对话框场景 ≤0.59
# 余量仍充足（用户自行真机验证）。
DIALOG_FRAME_ICON = "dialog_frame.png"
DIALOG_FRAME_BAND = (350, 200, 1030, 650)  # 居中对话框搜索条带（DIALOG_REGION 外扩）
DIALOG_FRAME_THRESHOLD = 0.79              # 命中阈值（2026-11-07 巫医真图 0.795 校准）

# ---- 剧情对话点屏推进的寻路保护（2026-11-11 用户实机） ----
# 剧情对话不挡路时人物照常自动寻路，此时点屏幕中间会取消寻路（人物
# 停下不再走）。用左上角地图位置文字的**像素比对**判断人物是否移动：
# 站定时该区域像素逐帧全等，行走/换图时坐标数字变化（45 对真实帧实测：
# 站立帧差 ≤0.26、行走 ≥0.50，走 1 格约 2~6），单次比对 ~0.1ms、零
# OCR——不用每轮 OCR 坐标。阈值取 0.3（误判移动只是推迟 1s，误判站定
# 会打断寻路，故向"移动"倾斜）。
STORY_MOVE_STATIC_POLLS = 2     # 连续像素不变轮询数（站定确认，≈1s）
STORY_MOVE_DIFF_THRESHOLD = 0.3 # 位置区平均像素差阈值
STORY_MOVE_BACKOFF = 1.0        # 检测到移动后的复查间隔（秒）
STORY_MOVE_TRACK_GRACE = 2.0    # 追踪寻路点击后的静默期（秒）
STORY_MOVE_LOG_INTERVAL = 5.0   # 寻路抑制日志限频（秒）


def _position_crop(img):
    """地图位置文字区域裁剪（寻路判定的像素信号）。失败返回 None。"""
    import numpy as np
    if img is None:
        return None
    arr = np.asarray(img)
    l, t, r, b = game_map.MAP_POS_REGION
    if arr.ndim < 2 or arr.shape[0] < b or arr.shape[1] < r:
        return None
    return arr[t:b, l:r].astype(np.int16)


def dialog_frame_score(frame):
    """对话框模板全局最高分（接受 PIL 或 BGR 帧，条带内匹配）。"""
    import cv2
    import numpy as np
    if not isinstance(frame, np.ndarray):
        frame = cv2.cvtColor(np.asarray(frame), cv2.COLOR_RGB2BGR)
    score, _ = game_map.best_match_np(frame, DIALOG_FRAME_ICON,
                                      region=DIALOG_FRAME_BAND)
    return score


_dlg_hit_state = False    # 上一轮询模板命中状态（上升沿日志用）
_dlg_hit_log_at = 0.0    # 上次命中日志时间（多调用方交错时的去重下限）


def dialog_ocr_skip(frame):
    """对话区 OCR 跳过判定：高性能模式下模板未命中即跳过。

    方案门控（用户 2026-11-06）：高性能模式才识别模板——普通模式
    行为与旧版完全一致（模板匹配零开销）；高性能下模板未命中
    （无标准对话框）即跳过对话区 OCR（实测匹配 ~9ms vs OCR
    470~930ms）。标准对话框（领取/看打/客栈等）模板分数实测
    ≥0.98，无对话框场景 ≤0.59；巫医 NPC 对话框实测 0.795
    （2026-11-07 真图，0.8 门曾挡掉战斗线程巫医检查），阈值校准
    为 0.79。

    模板命中（上升沿）打一次 INFO 日志便于观察方案生效；多调用方
    （follow/战斗线程/守卫）交错时的重复命中由 2s 下限去重。
    """
    global _dlg_hit_state, _dlg_hit_log_at
    if not app_utils.high_perf_on():
        return False
    score = dialog_frame_score(frame)
    hit = score >= DIALOG_FRAME_THRESHOLD
    now = time.time()
    if hit and not _dlg_hit_state and now - _dlg_hit_log_at >= 2.0:
        _dlg_hit_log_at = now
        log(f"[对话框] 模板命中（分数 {score:.3f}），检测到对话框，对话区 OCR 放行", "INFO")
    _dlg_hit_state = hit
    return not hit


def clear_blocking_dialog(win, prefix="", max_clicks=3):
    """对话框遮挡清理：模板命中（对话框在场）时点屏幕中间推进关闭。

    目标模板（掌柜/领取按钮等）找不到时不一定是站位问题——对话框
    会挡住画面（客栈奇遇弹窗、NPC 欢迎语等，2026-11-07 用户反馈）。
    点一次后复查模板，仍在则继续点（多屏对话），至多 max_clicks 次。
    返回 (是否检测到对话框, 是否已点击)——检测不到对话框时不动画面
    （世界点击可能误开新对话）。
    """
    tag = f"[{prefix}] " if prefix else ""
    img = win.capture(force_activate=False)
    if img is None or dialog_frame_score(img) < DIALOG_FRAME_THRESHOLD:
        return False, False
    log(f"{tag}检测到对话框（模板命中），点击屏幕中间关闭", "WARN")
    for _ in range(max_clicks):
        human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
        time.sleep(1.0)
        img = win.capture(force_activate=False)
        if img is not None and dialog_frame_score(img) < DIALOG_FRAME_THRESHOLD:
            break
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True, True


class StoryDialogDismisser:
    """无绿字选项的标准对话框（NPC 剧情台词）点屏幕中间推进/关闭。

    背景（2026-11-10 实测）：剧情台词框是标准对话框（模板分 0.9886），
    无绿字选项、不在任何跳过/选项表里 → 无人处理、常驻屏幕，follow
    轮询对话区模板门每轮命中、每 0.5s 全量区域 OCR（~100-170ms/次，
    占任务线程 20-35% wall）；与修罗 NPC 收尾对话同因（2026-11-09
    修）。点屏推进后对话关闭、模板门未命中，OCR 归零。

    守卫（顺序，任一不满足即不动画面）：
    ① allow_dismiss 回调（可选）整体抑制——如「给与面板预期窗口内
       不推进 NPC 对话」的任务专属规则；
    ② 对话区有可读文本（items 来自对话区 OCR；渲染过渡帧无文字不点）；
    ③ 已知对话文案（known_texts）命中即跳过——有待点选项/待推进文案
       的对话交调用方处理，选项未渲染时只等待，不当剧情对话点屏；
    ④ 对话框模板在场（img_np 为区域 OCR 掩码帧，特征块在 DIALOG_REGION
       内时掩码帧与整帧同分，实测 0.9886）——战斗 UI/背包/商店等无标准
       边框（商店面板实测 0.653 < 0.79）不误点世界；
    ⑤ 对话区任何行都无绿字（带选项的对话不点）；
    ⑥ 连续 2 轮询命中（单帧可能是渲染过渡），点击间隔 ≥1s（多屏对话
       逐页推进，防连点）；
    ⑦ 人物已站定（地图位置文字区域像素连续 STORY_MOVE_STATIC_POLLS
       次不变）——寻路中点屏幕中间会取消自动寻路、人物停下不再走
       （2026-11-11 用户实机教训）。刚点过任务追踪的
       STORY_MOVE_TRACK_GRACE 秒内一律按寻路中处理（坐标显示有刷新
       间隔，起步瞬间读不到变化）。
    一个任务阶段一个实例（状态随实例重置，不跨任务泄漏）。

    img：调用方已截帧（PIL，可选）——寻路判定复用同一帧省一次截窗；
    None 时内部截帧（force_activate=False，不抢焦点）。
    """

    def __init__(self, known_texts=(), allow_dismiss=None):
        self.known_texts = tuple(known_texts)
        self.allow_dismiss = allow_dismiss
        self.streak = 0
        self.last_click_at = 0.0
        self._last_crop = None      # 上次位置区像素（寻路判定基线）
        self._static_polls = 0      # 连续像素不变轮询数
        self._move_retry_at = 0.0   # 移动中复查退避截止时间
        self._move_log_at = 0.0     # 寻路抑制日志限频时间戳

    def _reset_walk(self):
        """重置寻路判定状态（对话关闭/守卫失败时调用）。"""
        self._last_crop = None
        self._static_polls = 0
        self._move_retry_at = 0.0

    def _walking(self, win, img=None):
        """人物是否仍在寻路（位置区像素仍在变化）。True = 禁止点屏。

        剧情对话不挡路时人物照常自动寻路，此时点屏幕中间会取消寻路
        （2026-11-11 用户实机：人物停下不再走）——寻路中一律不点，
        等人物站定（连续 STORY_MOVE_STATIC_POLLS 次像素不变）再推进。
        位置区像素比对零 OCR（~0.1ms）；检测到移动后退避
        STORY_MOVE_BACKOFF 秒复查，长距离寻路不逐轮询比对。
        无帧可查（截窗失败/调用方无窗口）按未寻路放行：保持旧行为，
        防无坐标场景的剧情对话永不推进（模板/绿字守卫仍在）。
        """
        now = time.time()
        if app_utils.track_walk_idle_seconds() < STORY_MOVE_TRACK_GRACE:
            return True  # 刚点过追踪寻路：位置文字可能尚未刷新
        if now < self._move_retry_at:
            return True  # 移动中退避：不重复截帧/比对
        if img is None and win is not None:
            img = win.capture(force_activate=False)
        crop = _position_crop(img)
        if crop is None:
            self._reset_walk()
            return False
        if self._last_crop is not None and self._last_crop.shape == crop.shape:
            import numpy as np
            if float(np.abs(crop - self._last_crop).mean()) \
                    >= STORY_MOVE_DIFF_THRESHOLD:
                self._last_crop = crop
                self._static_polls = 0
                self._move_retry_at = now + STORY_MOVE_BACKOFF
                return True
        self._last_crop = crop
        self._static_polls += 1
        return self._static_polls < STORY_MOVE_STATIC_POLLS

    def try_dismiss(self, win, items, img_np, prefix="", img=None):
        """满足全部守卫时点屏幕中间一次，返回是否已点击。"""
        if self.allow_dismiss is not None and not self.allow_dismiss():
            self.streak = 0
            return False
        dl, dt = task_tracker.DIALOG_REGION[0], task_tracker.DIALOG_REGION[1]
        region_items = [it for it in items
                        if it[2] >= 0.55 and it[1][0] >= dl and it[1][1] >= dt]
        if not region_items:
            self.streak = 0
            self._reset_walk()
            return False
        if any(_dialog_text_present(region_items, t)
               for t in self.known_texts):
            self.streak = 0
            self._reset_walk()
            return False
        if dialog_frame_score(img_np) < DIALOG_FRAME_THRESHOLD:
            self.streak = 0
            self._reset_walk()
            return False
        if any(task_tracker.is_green_text(img_np, it[1]) for it in region_items):
            self.streak = 0  # 有绿字选项：不是无选项剧情对话
            self._reset_walk()
            return False
        self.streak += 1
        if self.streak < 2:
            return False
        now = time.time()
        if now - self.last_click_at < 1.0:
            return False
        if self._walking(win, img):
            if now - self._move_log_at >= STORY_MOVE_LOG_INTERVAL:
                self._move_log_at = now
                log(f"[{prefix}] 人物寻路中，暂不点屏推进剧情对话（防打断寻路）",
                    "INFO")
            return False
        self.last_click_at = now
        self.streak = 0
        txt = region_items[0][0][:20]
        log(f"[{prefix}] 检测到无选项剧情对话「{txt}」，点击屏幕中间推进", "SYS")
        _click_screen_center(win, times=1, prefix=prefix)
        return True


def verify_dialog_detect(win):
    """诊断-测试对话框识别：单帧模板判定对话框在场 + 对话区 OCR 计时。

    只读不点击（任务运行中也可用）；有/无对话框场景各点一次，
    对照日志中的模板分数/耗时与 OCR 耗时。返回 (是否成功, 描述)。
    """
    import cv2
    import numpy as np
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    t0 = time.time()
    score, pt = game_map.best_match_np(frame, DIALOG_FRAME_ICON,
                                       region=DIALOG_FRAME_BAND)
    t_match = time.time() - t0
    hit = score >= DIALOG_FRAME_THRESHOLD
    # 客栈奇遇横幅模板（标准对话框之外的补充门，供真机标定；导入/匹配
    # 失败不影响本诊断）
    qiyu_score = None
    t_qiyu = 0.0
    try:
        from . import kezhan  # 函数内导入避免 dialog_common↔kezhan 循环
        t0 = time.time()
        qiyu_score = kezhan._qiyu_frame_score(frame)
        t_qiyu = time.time() - t0
    except Exception:
        pass
    t0 = time.time()
    items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
    t_ocr = time.time() - t0
    lines = [t for t, _, s in items if s >= 0.55][:5]
    log(f"[诊断] 对话框模板: 分数 {score:.3f}（阈值 {DIALOG_FRAME_THRESHOLD}）"
        f"{' 命中' if hit else ' 未命中'} @ ({pt[0]},{pt[1]})，"
        f"匹配耗时 {t_match * 1000:.0f}ms", "SYS")
    if qiyu_score is not None:
        qiyu_hit = qiyu_score >= kezhan.QIYU_FRAME_THRESHOLD
        log(f"[诊断] 客栈奇遇模板: 分数 {qiyu_score:.3f}"
            f"（阈值 {kezhan.QIYU_FRAME_THRESHOLD}）"
            f"{' 命中' if qiyu_hit else ' 未命中'}，"
            f"匹配耗时 {t_qiyu * 1000:.0f}ms", "SYS")
    log(f"[诊断] 对话区 OCR: 耗时 {t_ocr * 1000:.0f}ms，"
        f"文案 {lines or '(无)'}", "SYS")
    desc = (f"模板分数 {score:.3f}（{'命中' if hit else '未命中'}），"
            f"匹配 {t_match * 1000:.0f}ms / OCR {t_ocr * 1000:.0f}ms")
    if qiyu_score is not None:
        desc += f"；奇遇模板 {qiyu_score:.3f}"
    return True, desc
