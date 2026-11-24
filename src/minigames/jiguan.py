"""封印机关破解小游戏的阻塞点击循环。

触发（对话文案 `JIGUAN_DIALOG_TEXT`）仍在 xuanshu 任务侧；本模块提供
play：提示对话框关闭 → 座骑降落（飞行驭器面板「降落」，2026-11-18
用户规则取代 Alt+C）→ OCR 机关标签「封印机关·黄/蓝/红」定位三个机关
→ 按 黄→蓝→红 点标签上方的剑身 → 按解除结果决定：换一处=换下一个
机关；已被破解/已完成任务/剧情式结局=结束；超时无结果=按点击落空
换下一个（绝不按成功处理）。import 期零副作用。封印按序开启（面板
链接按序点击）的进度键与链接定位逻辑见 minigames/fengyin_link.py
（2026-11-17 拆分，代码逐字节未变）。

遮挡处理（2026-11-20 流洲实机 + 用户规则）：人物站在封印中心时可能
正好挡住某个机关标签，区域 OCR 完全读不出（2x 放大也读不出残字），
已有标签全部点过而仍缺机关（尝试数 < 3）时走开一小步再重扫
（JIGUAN_REVEAL_*）。用户确认：走开拉开后从稍远处点剑身能直接触发
破解，人物不必先走到剑前。点对机关的结局除「已被破解」外还有剧情式
结局（玩家「怎么没有反应？」→ 神秘人「玄宝已被人抢先一步取走了」，
任务随即结束），结果为非「换一处」的对话框即按成功处理。

颜色提示与剑身定位（2026-11-24 实机重标定）：任务描述首句就是机关
颜色提示——「玄黄贯气破封断障」→ 黄机关（3 次首击成功）、「赤锋凝华
封禁皆破」→ 红机关（2 次黄/蓝均「换一处」后红成功）。解析提示后直接
点对应颜色，不再盲试。同时按颜色在场景内定位剑身色块（红/金/蓝三把
剑颜色鲜明，31x31 窗口命中像素 400~550，空地 0）：标签被人物挡住时
OCR 只剩「红」这类单字颜色行，残缺标签反推的坐标可偏差 30~40px——
2026-11-24 实机三次点击两个颜色后把蓝剑当未知色重踢一次、红剑从未
踢中，任务卡死。现在色块优先、残缺标签需通过点击点颜色校验，且失败
后走开封印范围让下一轮跟踪重走能重新触发提示对话（否则人物停在原地，
任务每 300s 空转一轮永不恢复）。
"""
import time

import cv2
import numpy as np

from ..core.app_utils import human_click, log
from ..device import map as game_map
from ..device import task_tracker
from ..device import zuoqi
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD, SCREEN_CENTER_POS,
                                    _dialog_text_present,
                                    clear_blocking_dialog,
                                    dialog_frame_score)

# ---- 封印机关破解（对话触发 + 按颜色换机关）----
JIGUAN_DIALOG_TEXT = "看来要破解的便是此地的封印了。"
# 尝试顺序（用户 2026-10-15 指定；颜色取自标签「封印机关·X」）
JIGUAN_CLICK_ORDER = ("黄", "蓝", "红")
# 机关定位改走标签 OCR（2026-11-18 实机重标定）：旧模板
# xuanshu_jiguan{huang,lan,hong}.png 带大面积背景，换一处封印点背景一变，
# 剑身真实位置只有 0.28-0.53 分（狮驼岭两处封印点实测），背景杂岩反而
# 0.6-0.76 分——点击落到错误位置、没有结果对话框，「超时=成功」把未破除
# 的封印当成功，人物原地卡死（用户实机「完全不动了」）。游戏自带的
# 机关标签「封印机关·黄/蓝/红」OCR 0.69-0.99 稳定，剑身就在标签正上方
# JIGUAN_SWORD_DY 处。
JIGUAN_TEMPLATES = ("xuanshu_jiguanhuang.png", "xuanshu_jiguanlan.png",
                    "xuanshu_jiguanhong.png")  # 旧模板（仅诊断按钮参考）
JIGUAN_THRESHOLD = 0.6   # 旧模板诊断阈值（流程不再使用）
JIGUAN_LABEL_KEY = "机关"     # 标签关键词（OCR 行可含玩家名，含「机关」即候选）
JIGUAN_LABEL_KEY_POS = 2      # 「机关」在标签「封印机关·X」六字中的起始字位
JIGUAN_LABEL_CHARS = 6        # 标签字数（封印机关·X）
JIGUAN_SWORD_DY = 65          # 标签中心 → 剑身中心纵向偏移（狮驼岭两处封印点
                              # 实测 65/65；剑身高约 113px，±25px 容差）
JIGUAN_LABEL_MIN_SCORE = 0.6  # 标签行 OCR 置信度下限（被角色遮挡的残标签实测 0.69）
JIGUAN_LABEL_REGION = (450, 280, 980, 680)  # 标签搜索区（角色四周，实测覆盖
                                            # 两处封印点全部三标签；区域 OCR ≈0.3s）
JIGUAN_LABEL_WAIT = 8.0       # 降落/视角就位后等标签出现的上限（秒）
JIGUAN_LABEL_POLL = 0.6       # 标签扫描轮询间隔（秒）
JIGUAN_LABEL_ROUNDS = 2       # 标签轮次上限（一轮三个都落空再重扫一轮）
JIGUAN_SAME_SPOT = 30         # 同位置去重距离（曼哈顿像素，重扫后不重复点）
# 标签颜色繁体归一（世界地图字体下 OCR 常输出繁体：黃/藍/紅，
# 2026-11-18 狮驼岭帧实测「封印机关·黃」）
JIGUAN_COLOR_ALIASES = str.maketrans({"黃": "黄", "藍": "蓝", "紅": "红"})
# 点击机关后的解除结果信号（2026-11-18 实机标定，均在标准对话框/消息栏
# 内、DIALOG_REGION OCR 可达）：
# - 「换一处」：看来此处无法破除封印，换一处再试试吧。（机关不对）
# - 「已被破解」：此处封印已被破解，回去找神秘人询问下一步要做什么。
#   （中间一处封印破除；旧文案「解除封印成功」一并保留）
# - 「已完成玄枢轶闻任务」：最后一处封印破除的消息栏完成提示。
JIGUAN_FAIL_TEXT = "换一处"
JIGUAN_SUCCESS_TEXTS = ("已被破解", "解除封印")
JIGUAN_DONE_TEXT = "已完成玄枢轶闻任务"
# 剧情式结局（2026-11-20 流洲实机）：点对机关后先出「正在破解封印」
# 读条，随后进剧情对话——玩家「怎么没有反应？」→ 神秘人「看来这玄石
# 中的玄宝已被人抢先一步取走了」→ 任务结束。文案不写死（可能随结局
# 变化），只要结果是**非「换一处」的对话框**即按点对处理。
JIGUAN_RESULT_WAIT = 20       # 等待解除结果的上限（秒）。「换一处」约
                              # 5-7s 出现（2026-11-18 实测 5.5s）；剧情
                              # 式结局需读完条再出对话（2026-11-20 实测
                              # ≤20s）。固定时延只作超时上限，任一方命中即返回
JIGUAN_DIALOG_POLL = 0.5      # 等待结果的轮询间隔（秒）
# ---- 标签被人物遮挡时的走开拉开（2026-11-20 用户规则）----
# 人物站在封印中心可能正好挡住某个机关标签（流洲实测：红标签被角色
# 身体盖住，区域 OCR 与 2x 放大均读不出），已有标签全部点过而仍缺
# 机关时按步点走开一小步再重扫。用户确认：走开拉开后从稍远处直接点
# 剑身即可触发破解（无需人物先走到剑前）。
JIGUAN_REVEAL_STEPS = ((150, 40), (-150, 40), (0, 170))  # 步点（屏幕中心偏移）
JIGUAN_REVEAL_BOUNDS = (150, 200, 1230, 700)  # 步点允许范围（避开标题/底栏 UI）
JIGUAN_REVEAL_CLEAR = 130     # 步点与已知机关剑身的最小曼哈顿距离（防点到剑）
JIGUAN_REVEAL_MAX = 2         # 每次 play 最多走开次数（防来回走动）
JIGUAN_REVEAL_SETTLE = 4.0    # 等人物走完的上限（秒）
JIGUAN_REVEAL_POLL = 0.3      # 走位轮询间隔（秒，位置 OCR ≈0.1-0.2s/次）
# ---- 任务文案颜色提示（2026-11-23/24 实机标定）----
# 「玄黄贯气破封断障」→ 黄机关（两个角色合计 3 次首击成功）；
# 「赤锋凝华封禁皆破」→ 红机关（两个角色均是黄/蓝「换一处」后红成功）。
# 描述首句（「依据」之前）即颜色提示：玄黄=黄、赤锋=红；蓝/青/碧按蓝处理
# （青色提示未经实机验证，映射错只多花一次点击，随后按黄→蓝→红兜底）。
JIGUAN_HINT_SPLIT = "依据"       # 描述首句与后续指引的分隔词
JIGUAN_HINT_CLAUSE_LEN = 24      # 只解析首句范围（防后续指引里的撞色字）
JIGUAN_HINT_CHARS = {"黄": "黄", "黃": "黄", "赤": "红", "红": "红",
                     "紅": "红", "蓝": "蓝", "藍": "蓝",
                     "青": "蓝", "碧": "蓝"}
# ---- 剑身颜色定位（2026-11-24 流洲实机标定）----
# 标签可能被人物身体挡住（OCR 只剩「红」），但剑身颜色鲜明、是场景中的
# 大块竖向色块，可直接按颜色定位：红剑 RGB 中位 (113,29,13)、金剑
# (87,59,10)、蓝剑 (111,125,199)，背景岩石灰 (63,63,63)。
# 31x31 窗口命中像素：红 400~550 / 金 319~389 / 蓝 478~508，空地 0。
# 竖向长条 + 宽度过滤排除玩家青色长枪（9px 宽）与名字文字（15px 高）。
JIGUAN_SWORD_REGION = (450, 120, 980, 700)  # 机关场景（含走位后上移的剑）
JIGUAN_SWORD_MIN_AREA = 500     # 剑身色块最小面积（像素）
JIGUAN_SWORD_MIN_W = 16         # 剑身最小宽度（排除武器细杆）
JIGUAN_SWORD_MAX_W = 70         # 剑身最大宽度
JIGUAN_SWORD_MIN_H = 50         # 剑身最小高度（排除名字/标签文字行）
JIGUAN_SWORD_COLOR_MIN = 220    # 点击点颜色校验下限（31x31 窗口命中像素）
JIGUAN_SWORD_COLOR_RADIUS = 15  # 颜色校验窗口半径
JIGUAN_BLOB_PAIR_MAX = 150      # 标签点击点与剑身色块最大配对距离
# ---- 破解失败后走开封印范围（2026-11-24 实机教训）----
# 三点都「换一处」后人物停在封印中心，follow 超时重点任务链接时人已在
# 目的地，封印提示对话不再弹出，任务每 300s 空转一轮永远卡住。
# 失败后走开一段距离，下一轮跟踪重走才能重新触发提示对话。
JIGUAN_LEAVE_STEPS = ((0, -200), (220, 0), (-220, 0), (0, 200))


def _jiguan_result_dialog(win, timeout=JIGUAN_RESULT_WAIT):
    """等待点击机关后的解除结果（直接信号）。

    标准对话框模板先门控（条带匹配 ~9ms/帧，无对话框时不 OCR），命中
    后才对对话区做区域 OCR 判文案。返回：
    - "fail"：出现「换一处再试试」，应换下一个机关；
    - "success"：出现「已被破解」/「解除封印」，最后一处的任务完成
      提示「已完成玄枢轶闻任务」，或剧情式结局——结果是对话框但文案
      不是「换一处」（2026-11-20 实机：玩家「怎么没有反应？」→ 神秘人
      「玄宝已被人抢先一步取走了」，任务随即结束）；
    - None：超时未出现任何结果（点击可能落空，调用方换下一个机关，
      **不得按成功处理**）。
    """
    deadline = time.time() + timeout
    while True:
        img = win.capture(force_activate=False)
        if img is not None \
                and dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
            items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
            if _dialog_text_present(items, JIGUAN_FAIL_TEXT):
                return "fail"
            if any(_dialog_text_present(items, t)
                   for t in JIGUAN_SUCCESS_TEXTS):
                return "success"
            if _dialog_text_present(items, JIGUAN_DONE_TEXT):
                return "success"
            # 结果对话框但不是「换一处」：本机关被接受（剧情式结局）。
            # 触发提示对话（play 开头已点一次关闭）渲染残留时不算结果；
            # 对话区需有可读文案（防渲染过渡帧误判）。
            if not _dialog_text_present(items, JIGUAN_DIALOG_TEXT) \
                    and any(t.strip() for t, _r, _s in items):
                return "success"
        if time.time() >= deadline:
            return None
        time.sleep(JIGUAN_DIALOG_POLL)


def _label_click_pos(box, text):
    """由标签行 OCR 框推算剑身点击坐标，返回 (x, y)。

    标签「封印机关·X」共六字，OCR 行可能与玩家名合并（左侧多字，如
    「入门御童封印机关·黄」）或被角色遮挡只认出中段（如「印机关」）：
    用「机关」在行内的字位反推标签左缘，取标签六字中心，再上移到剑身。
    """
    i = text.find(JIGUAN_LABEL_KEY)
    if i < 0:
        return None
    char_w = (box[2] - box[0]) / max(1, len(text))
    left = box[0] - (JIGUAN_LABEL_KEY_POS - i) * char_w
    cx = left + JIGUAN_LABEL_CHARS / 2 * char_w
    cy = (box[1] + box[3]) / 2
    return int(cx), int(cy - JIGUAN_SWORD_DY)


def _label_color(text):
    """标签颜色（黄/蓝/红）：只在「机关」之后的字里找，防同行玩家名撞字。"""
    i = text.find(JIGUAN_LABEL_KEY)
    if i < 0:
        return None
    tail = text[i + len(JIGUAN_LABEL_KEY):].translate(JIGUAN_COLOR_ALIASES)
    return next((c for c in JIGUAN_CLICK_ORDER if c in tail), None)


def _label_char_width(items):
    """完整标签行的字宽中位数（残缺标签反推标签中心用）。

    取同帧含「机关」且有颜色的行：整行宽 / 字数。合并玩家名的行
    （「直荣印兽封印机关·黄」）字宽与纯标签行一致，实测 16.4 vs 16.7。
    """
    widths = []
    for text, box, score in items:
        if score < JIGUAN_LABEL_MIN_SCORE or JIGUAN_LABEL_KEY not in text:
            continue
        if _label_color(text) is None:
            continue
        widths.append((box[2] - box[0]) / max(1, len(text)))
    if not widths:
        return None
    widths.sort()
    return widths[len(widths) // 2]


def _color_char_row(text):
    """单字颜色行（标签被遮挡后只剩「红」「藍」）→ 归一颜色；否则 None。"""
    t = (text or "").strip().translate(JIGUAN_COLOR_ALIASES)
    if len(t) == 1:
        return t if t in JIGUAN_CLICK_ORDER else None
    if len(t) <= 2:  # OCR 偶带标点
        chars = [ch for ch in t if ch in JIGUAN_CLICK_ORDER]
        if len(chars) == 1:
            return chars[0]
    return None


def scan_labels(win, img=None):
    """扫描机关标签，返回 [(颜色或 None, (x, y))]，颜色未知的排后使用。

    标签被人物身体遮挡时 OCR 可能只剩「红」这类单字颜色行（没有「机关」
    关键字，2026-11-24 流洲实机）：用同帧完整标签的字宽反推标签中心，
    再上移到剑身。残缺「机关」行仍按原公式（字位反推）处理。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return []
    items, _ = game_map.ocr_region(img, JIGUAN_LABEL_REGION)
    char_w = _label_char_width(items)
    out = []
    for text, box, score in items:
        if score < JIGUAN_LABEL_MIN_SCORE:
            continue
        if JIGUAN_LABEL_KEY in text:
            pos = _label_click_pos(box, text)
            if pos is not None:
                out.append((_label_color(text), pos))
            continue
        color = _color_char_row(text)
        if color is not None and char_w is not None:
            # 颜色字是标签第 6 字：颜色字中心 - 2.5 字宽 = 标签中心
            cx = int((box[0] + box[2]) / 2
                     - (JIGUAN_LABEL_CHARS / 2 - 0.5) * char_w)
            cy = int((box[1] + box[3]) / 2)
            out.append((color, (cx, cy - JIGUAN_SWORD_DY)))
    return out


def _color_mask(arr, color):
    """机关剑颜色掩码（RGB 数组，2026-11-24 实机标定）。"""
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    if color == "红":
        return (r >= 80) & ((r - np.maximum(g, b)) >= 45)
    if color == "黄":
        return ((r >= 70) & (g >= 40) & ((r - b) >= 45)
                & ((g - b) >= 20) & ((r - g) <= 80))
    if color == "蓝":
        return (b >= 90) & ((b - np.maximum(r, g)) >= 40)
    return np.zeros(r.shape, dtype=bool)


def _color_count(arr, x, y, color, radius=JIGUAN_SWORD_COLOR_RADIUS):
    """(x,y) 周围窗口内该颜色的命中像素数（越界裁剪）。"""
    x0, x1 = max(0, x - radius), min(arr.shape[1], x + radius + 1)
    y0, y1 = max(0, y - radius), min(arr.shape[0], y + radius + 1)
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(_color_mask(arr[y0:y1, x0:x1], color).sum())


def _point_has_color(img, pos, color):
    """点击点附近是否有该颜色剑身（防标签残缺反推坐标偏差点空）。"""
    arr = np.asarray(img)[:, :, :3]
    x, y = pos
    if not (0 <= x < arr.shape[1] and 0 <= y < arr.shape[0]):
        return False
    return _color_count(arr, x, y, color) >= JIGUAN_SWORD_COLOR_MIN


def find_sword_blobs(img):
    """在机关场景内按颜色定位三把机关剑，返回 {颜色: (剑身中心x, y)}。

    2026-11-24 流洲实机标定（见 JIGUAN_SWORD_* 注释）：色块面积/长宽
    过滤后，红/金/蓝各只剩剑身本身（金剑场景里玩家金色披风 541、
    红剑金色纹饰 836，均小于金剑 1079）。找不到的颜色不出现在结果里。
    """
    arr = np.asarray(img)[:, :, :3]
    left, top, right, bottom = JIGUAN_SWORD_REGION
    roi = arr[top:bottom, left:right]
    out = {}
    kernel = np.ones((5, 5), np.uint8)
    for color in JIGUAN_CLICK_ORDER:
        mask = _color_mask(roi, color)
        if not mask.any():
            continue
        m8 = cv2.morphologyEx(mask.astype(np.uint8) * 255,
                              cv2.MORPH_CLOSE, kernel)
        n, _lab, stats, centroids = cv2.connectedComponentsWithStats(m8, 8)
        best = None
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            if (area < JIGUAN_SWORD_MIN_AREA or w < JIGUAN_SWORD_MIN_W
                    or w > JIGUAN_SWORD_MAX_W or h < JIGUAN_SWORD_MIN_H):
                continue
            if best is None or area > best[0]:
                best = (area, int(centroids[i][0]) + left,
                        int(centroids[i][1]) + top)
        if best is not None:
            out[color] = (best[1], best[2])
    return out


def _nearest_blob(blobs, color, pos):
    """离标签点击点最近的剑身色块 → (颜色, 点)；超配对距离返回 None。"""
    best = None
    for c, bpos in blobs.items():
        if color is not None and c != color:
            continue
        d = abs(bpos[0] - pos[0]) + abs(bpos[1] - pos[1])
        if best is None or d < best[0]:
            best = (d, c, bpos)
    if best is None or best[0] > JIGUAN_BLOB_PAIR_MAX:
        return None
    return best[1], best[2]


def _scan_scene(win):
    """一次截帧 → 标签 + 剑身色块（共用一帧，性能规则）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return [], {}, None
    return scan_labels(win, img=img), find_sword_blobs(img), img


def _wait_scene(win):
    """等机关场景就位（降落/视角切换/关对话框后），任一信号出现即返回。

    返回 (labels, blobs, img)；img 为最近一帧（颜色校验同源）。
    """
    deadline = time.time() + JIGUAN_LABEL_WAIT
    while True:
        labels, blobs, img = _scan_scene(win)
        if labels or blobs or time.time() >= deadline:
            return labels, blobs, img
        time.sleep(JIGUAN_LABEL_POLL)


def _hint_color(text):
    """从任务描述首句解析机关颜色提示（玄黄→黄 / 赤锋→红）。

    2026-11-23/24 实机标定（见 JIGUAN_HINT_* 注释）。解析不到返回 None，
    调用方按 黄→蓝→红 兜底，行为与旧版一致。
    """
    if not text:
        return None
    clause = text.split(JIGUAN_HINT_SPLIT, 1)[0][:JIGUAN_HINT_CLAUSE_LEN]
    for ch in clause:
        color = JIGUAN_HINT_CHARS.get(ch)
        if color is not None:
            return color
    return None


def _ordered_labels(labels):
    """按 黄→蓝→红 排点击顺序，返回 [(颜色或 None, (x, y))]；同色取首个。

    颜色识别不出的位置放最后（仍会尝试）。
    """
    known = {}
    unknown = []
    for color, pos in labels:
        if color is None:
            unknown.append((None, pos))
        elif color not in known:
            known[color] = pos
    return ([(c, known[c]) for c in JIGUAN_CLICK_ORDER if c in known]
            + unknown)


def _ordered_positions(labels):
    """按 黄→蓝→红 排点击顺序；颜色识别不出的位置放最后（仍会尝试）。"""
    return [pos for _c, pos in _ordered_labels(labels)]


def _step_aside(win, labels, prefix):
    """走开一小步拉开人物对机关标签的遮挡（2026-11-20 用户规则）。

    被人物身体挡住的标签 OCR 完全读不出（2x 放大也不行），已有标签
    全部点过而仍缺机关时调用：按 JIGUAN_REVEAL_STEPS 选一个离已知
    机关剑身够远、在画面范围内的空地点一下，等人物走完返回 True，
    调用方重新扫描标签。所有方向都走不动/途中进战斗返回 False。
    """
    from ..device.battle import is_in_battle
    known = [pos for _c, pos in labels]
    start = game_map.read_map_position(win)
    left, top, right, bottom = JIGUAN_REVEAL_BOUNDS
    cx, cy = SCREEN_CENTER_POS
    for dx, dy in JIGUAN_REVEAL_STEPS:
        x, y = cx + dx, cy + dy
        if not (left <= x <= right and top <= y <= bottom):
            continue
        if any(abs(x - px) + abs(y - py) < JIGUAN_REVEAL_CLEAR
               for px, py in known):
            continue  # 步点离机关剑身太近：换个方向
        human_click(win, x, y)
        moved = _wait_settle(win, start)
        if is_in_battle(win):
            log(f"[{prefix}] 走位拉开遮挡时进入战斗，放弃本次破解", "WARN")
            return False
        if moved:
            log(f"[{prefix}] 机关标签被遮挡，已走开拉开视角 @({x},{y})", "SYS")
            return True
        start = game_map.read_map_position(win)
    return False


def _wait_settle(win, start_pos):
    """等走位结束：位置先出现变化、再连续两次相同即站定。

    start_pos 为点击前的地图位置（(地图,x,y) 或 None）。返回是否发生
    过位移——未位移说明点到了不可行走处，调用方换方向重试。
    """
    deadline = time.time() + JIGUAN_REVEAL_SETTLE
    last = start_pos
    moved = start_pos is None
    while time.time() < deadline:
        time.sleep(JIGUAN_REVEAL_POLL)
        pos = game_map.read_map_position(win)
        if pos is None:
            continue
        if start_pos is not None and pos != start_pos:
            moved = True
        if moved and pos == last:
            return True
        last = pos
    return moved


def _land(win, prefix):
    """座骑降落（飞行驭器面板「降落」）：机关落地后才可见可点。

    2026-11-18 用户规则：替换旧的 Alt+C 热键降落。面板平时保持打开，
    降落后主动关闭防遮挡（机关标签在屏幕中部）。战斗外才可用。
    """
    ok, info = zuoqi.ensure_landed(win)
    _closed, cinfo = zuoqi.close_feixingyuqi_panel(win)
    log(f"[{prefix}] 座骑降落：{info}；{cinfo}", "SYS" if ok else "WARN")
    return ok


def _build_candidates(labels, blobs, order, failed_colors, tried, img):
    """候选点击点：剑身颜色色块优先，同色标签次之，颜色未知标签兜底。

    - 同色剑身色块可见 → 点色块中心（剑身中部，不依赖标签 OCR）；
    - 色块缺失但同色标签可见 → 标签上方点击点须通过颜色校验，否则不点
      （2026-11-24 教训：残缺标签反推坐标可偏差 30~40px，点空会让人物
      走开、视角偏离，还可能把已试过的颜色当未知色重踢）；
    - 颜色未知的标签 → 用最近的剑身色块反推颜色，跨视角仍按颜色去重。
    返回 [(颜色或 None, (x, y), 来源说明)]，按 order 排序、同色去重。
    """
    def fresh(pos):
        return all(abs(pos[0] - t[0]) + abs(pos[1] - t[1]) > JIGUAN_SAME_SPOT
                   for t in tried)

    label_pos = {}
    for color, pos in labels:
        if color is not None and color not in label_pos:
            label_pos[color] = pos

    out = []
    seen = set()
    for color in order:
        if color in failed_colors or color in seen:
            continue
        pos = None
        source = ""
        lpos = label_pos.get(color)
        bpos = blobs.get(color)
        if bpos is not None and (lpos is None
                                 or (abs(bpos[0] - lpos[0])
                                     + abs(bpos[1] - lpos[1]))
                                 <= JIGUAN_BLOB_PAIR_MAX):
            pos, source = bpos, "剑身色块"
        elif lpos is not None and img is not None \
                and _point_has_color(img, lpos, color):
            # 色块离标签过远（可能是别人的武器/特效）或缺失：标签点+
            # 颜色校验通过才点
            pos, source = lpos, "标签+颜色校验"
        elif bpos is not None:
            pos, source = bpos, "剑身色块"
        if pos is not None and fresh(pos):
            out.append((color, pos, source))
            seen.add(color)
    for _color, lpos in labels:
        if _color is not None:
            continue
        near = _nearest_blob(blobs, None, lpos)
        if near is None:
            continue
        ncolor, npos = near
        if ncolor in failed_colors or ncolor in seen or not fresh(npos):
            continue
        out.append((ncolor, npos, "标签邻色"))
        seen.add(ncolor)
    return out


def _leave_seal_area(win, prefix):
    """破解失败后走开封印范围（2026-11-24 实机教训）。

    人物停在封印中心时，follow 超时重点任务追踪链接不会产生寻路，封印
    提示对话也不再弹出——任务每 300s 空转一轮永远卡住。走开一段距离后
    下一轮重走才能重新触发提示对话、重启破解。
    """
    start = game_map.read_map_position(win)
    left, top, right, bottom = JIGUAN_REVEAL_BOUNDS
    cx, cy = SCREEN_CENTER_POS
    for dx, dy in JIGUAN_LEAVE_STEPS:
        x, y = cx + dx, cy + dy
        if not (left <= x <= right and top <= y <= bottom):
            continue
        human_click(win, x, y)
        if _wait_settle(win, start):
            log(f"[{prefix}] 破解失败，已走开封印位置 @({x},{y})，"
                f"等待任务追踪重走重新触发封印对话", "WARN")
            return True
        start = game_map.read_map_position(win)
    return False


def play(win, prefix="玄枢轶闻", hint_text=""):
    """封印机关破解（用户 2026-10-15 流程；2026-11-18/20/24 实机重标定）：

    提示对话框点击一次关闭 → 座骑降落 → 定位三个机关（任务描述首句
    的颜色提示优先，其次剑身颜色色块 / 标签 OCR）→ 按 黄→蓝→红（提示
    颜色最先）点击剑身 → 轮询解除结果：
    - 「换一处再试试」：本机关不对，关闭对话框后换下一个；
    - 「已被破解」/「解除封印」/「已完成玄枢轶闻任务」/剧情式结局
      （非「换一处」的结果对话框）：结束；
    - 超时无结果：点击落空，换下一个机关（不按成功处理）。
    hint_text 为任务追踪面板（含描述）的 OCR 文本，用于解析颜色提示。
    每次点击后重新扫描场景（点击落空可能让角色走动、视角变化），同一
    颜色/位置不重复点；目标剑身不可见时走开一小步再重扫
    （2026-11-20/24，JIGUAN_REVEAL_*）；失败后走开封印范围
    （JIGUAN_LEAVE_STEPS）让下一轮重走能重新触发。轮次上限
    JIGUAN_LABEL_ROUNDS。返回 (是否成功, 描述)。
    """
    hint = _hint_color(hint_text)
    # 1. 点击一次关闭提示对话框（屏幕中间；该对话框无选项）
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log(f"[{prefix}] 已点击关闭封印提示对话框", "SYS")
    time.sleep(0.5)
    # 2. 座骑降落（飞行状态下机关不可见不可点）
    _land(win, prefix)
    # 3. 点击顺序：任务文案提示的颜色优先，其余按 黄→蓝→红
    if hint:
        order = [hint] + [c for c in JIGUAN_CLICK_ORDER if c != hint]
        log(f"[{prefix}] 任务描述颜色提示：{hint}机关"
            f"（点击顺序 {'→'.join(order)}）", "SYS")
    else:
        order = list(JIGUAN_CLICK_ORDER)
    clicked = 0
    tried = []
    tried_colors = set()    # 已点过的颜色（含落空，防同一颜色无限重试）
    failed_colors = set()   # 明确提示「换一处」的颜色（之后不再点）
    reveals = 0
    max_attempts = JIGUAN_LABEL_ROUNDS * len(JIGUAN_CLICK_ORDER)
    while clicked < max_attempts:
        # 每次点击前重扫场景（标签 + 剑身色块同帧；视角/人物位置可能变化）
        labels, blobs, img = _wait_scene(win)
        if not labels and not blobs:
            if clicked == 0:
                log(f"[{prefix}] 未找到封印机关（区域 {JIGUAN_LABEL_REGION}），"
                    f"放弃本轮破解", "WARN")
            break
        # 提示颜色未尝试且完全不可见（无剑身色块、标签点颜色校验也不过）：
        # 先走位拉开遮挡，避免跳过提示颜色直接盲试其它颜色
        # （2026-11-24 实机：红剑被人物挡住）
        hint_label = next((pos for color, pos in labels if color == hint), None)
        hint_ready = hint in blobs or (
            hint_label is not None and img is not None
            and _point_has_color(img, hint_label, hint))
        if hint and hint not in tried_colors and hint not in failed_colors \
                and not hint_ready and reveals < JIGUAN_REVEAL_MAX \
                and _step_aside(win, labels, prefix):
            reveals += 1
            continue
        cands = _build_candidates(labels, blobs, order, failed_colors,
                                  tried, img)
        if not cands:
            # 已有标签/剑身都点过仍缺机关：某个机关被人物身体挡住。
            # 走开一小步拉开遮挡后重扫（2026-11-20 用户规则）。
            untried = [c for c in JIGUAN_CLICK_ORDER
                       if c not in tried_colors and c not in failed_colors]
            if untried and reveals < JIGUAN_REVEAL_MAX \
                    and _step_aside(win, labels, prefix):
                reveals += 1
                continue
            break
        color, (x, y), source = cands[0]
        tried.append((x, y))
        if color is not None:
            tried_colors.add(color)
        clicked += 1
        human_click(win, x, y)
        color_tag = f"·{color}" if color else ""
        log(f"[{prefix}] 已点击封印机关{color_tag} @({x},{y})（{source}），"
            f"等待解除结果", "SYS")
        result = _jiguan_result_dialog(win)
        if result == "fail":
            log(f"[{prefix}] 机关 @({x},{y}) 提示「换一处再试试」，改点下一个",
                "SYS")
            if color is not None:
                failed_colors.add(color)  # 明确不对：之后不再点该颜色
            clear_blocking_dialog(win, prefix=prefix, max_clicks=5)
            continue
        if result == "success":
            log(f"[{prefix}] 封印已破除（点击 {clicked} 个机关位置）", "SYS")
            return True, f"封印破解完成（点击 {clicked} 个机关）"
        log(f"[{prefix}] 点击机关 @({x},{y}) 后 {JIGUAN_RESULT_WAIT}s 内无解除"
            f"结果，按点击落空处理，换下一个", "WARN")
    if clicked > 0:
        _leave_seal_area(win, prefix)
    return False, f"封印破解未成功（尝试 {clicked} 个机关位置）"
