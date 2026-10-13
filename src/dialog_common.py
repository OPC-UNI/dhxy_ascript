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
# 按序号点击选项的几何参数（选项无法 OCR 识别时使用）
OPTION_FIRST_OFFSET = 10
OPTION_ROW_STEP = 18
OPTION_X_OFFSET = 1


def green_option_center(img_np, rect, opt_len, line_len):
    """选项点击中心：全行绿字中心；OCR 行合并了相邻文字时（line_len
    明显长于选项长度）按字符比例取行左部选项自身中心。

    实测「玄枢轶闻我想领取通用玄宝」合并成一行，全行绿字中心 520
    偏离选项自身中心 470 点不中，且毒化高性能缓存（客栈/五环/修罗/
    玄枢轶闻共用的统一修正）。opt_len/line_len 建议传规范化后长度。
    纵向合并（两个选项行堆叠成高框，如「接取客栈任务了解任务详情」，
    实测偶现点错第二项）：高框时纵向同样按比例取上部选项自身中心，
    全行绿字纵向中心会落到第二项上。
    """
    cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
    if line_len > opt_len + 2:
        frac = min(opt_len / line_len, 1.0)
        cx = rect[0] + int((rect[2] - rect[0]) * frac / 2)
        if rect[3] - rect[1] > 30:
            cy = rect[1] + int((rect[3] - rect[1]) * frac / 2)
    return cx, cy


def _click_green_option_once(win, items, img_np, text, prefix=""):
    """单帧检测并点击指定文案的绿色选项；点击成功返回 True。

    限定对话框区域（x≥PANEL_MAX_X）：任务追踪面板的绿字链接可能包含
    相同文案（如链接「去找小胖丫帮旺小乔试试服装」含「试试」），
    面板行 y 序在前会被先遍历到，不限区域会误点面板链接。
    prefix：日志前缀（如「客栈」「玄枢轶闻」）。
    """
    tag = f"[{prefix}] " if prefix else ""
    for txt, rect, score in items:
        if text in txt and score >= 0.55 and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = green_option_center(img_np, rect, len(text), len(txt))
            human_click(win, cx, cy)
            # 高性能：记录选项固定位置（按文案做键，各任务模块共用；
            # 位置漂移时由本路径自然刷新）
            app_utils.fixed_pos_set(f"option_{text}", win, cx, cy)
            log(f"{tag}已点击对话框绿色选项「{txt}」@({cx},{cy})", "SYS")
            move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
            return True
    return False


def _click_dialog_option_by_index(win, items, dtxt, idx, prefix=""):
    """按序号点击问题下方的选项（选项无法 OCR 识别时使用）。

    定位问题文案所在行，选项在其下方纵向排列：
    第 idx 项的点击位置 = 问题行下方 10 + 18*(idx-1) 像素、问题行左侧 +1 像素
    （实测问答题三选项文字中心 咖啡 +12/果汁 +30/茶 +48，取略偏上避免点到行间）。
    """
    tag = f"[{prefix}] " if prefix else ""
    qrect = None
    for txt, rect, _ in items:
        if _fuzzy_contains(txt, dtxt):
            qrect = rect
            break
    if qrect is None:
        return False
    cx = qrect[0] + OPTION_X_OFFSET
    cy = qrect[3] + OPTION_FIRST_OFFSET + OPTION_ROW_STEP * (idx - 1)
    human_click(win, cx, cy)
    log(f"{tag}已按序号点击第 {idx} 项选项 @({cx},{cy})", "SYS")
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _click_screen_center(win, times=DIALOG_SKIP_CLICKS, prefix=""):
    """点击屏幕中间若干次（推进 NPC 对话）。

    固定次数点击——多屏对话无需枚举后续文案，命中首屏文案即连点推进
    （漏推进由下一轮询 OCR 自愈；逐次校验文案会卡住多屏对话，实测废弃）。
    """
    tag = f"[{prefix}] " if prefix else ""
    cx, cy = SCREEN_CENTER_POS
    for _ in range(times):
        human_click(win, cx, cy)
        time.sleep(0.3)  # 对话推进间隔（漏推进由下一轮询 OCR 自愈）
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    log(f"{tag}已点击屏幕中间 {times} 次（推进对话）", "SYS")


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


def close_scene_panels(win, prefix=""):
    """关闭场景中可能打开的界面：小地图、世界地图、物品栏。

    prefix：日志前缀（如「客栈」「玄枢轶闻」）。
    """
    tag = f"[{prefix}] " if prefix else ""
    game_map.close_maps(win)
    if bag.is_bag_open(win):
        ok, info = bag.close_bag(win)
        log(f"{tag}物品栏{info}", "SYS" if ok else "WARN")
