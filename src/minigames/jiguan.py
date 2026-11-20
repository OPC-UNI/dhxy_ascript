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
"""
import time

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


def scan_labels(win):
    """扫描机关标签，返回 [(颜色或 None, (x, y))]，颜色未知的排后使用。"""
    img = win.capture(force_activate=False)
    if img is None:
        return []
    items, _ = game_map.ocr_region(img, JIGUAN_LABEL_REGION)
    out = []
    for text, box, score in items:
        if score < JIGUAN_LABEL_MIN_SCORE or JIGUAN_LABEL_KEY not in text:
            continue
        pos = _label_click_pos(box, text)
        if pos is not None:
            out.append((_label_color(text), pos))
    return out


def _wait_labels(win):
    """等机关标签出现（降落/关闭面板后视角就位需要时间），出现即返回。"""
    deadline = time.time() + JIGUAN_LABEL_WAIT
    while True:
        labels = scan_labels(win)
        if labels or time.time() >= deadline:
            return labels
        time.sleep(JIGUAN_LABEL_POLL)


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


def play(win, prefix="玄枢轶闻"):
    """封印机关破解（用户 2026-10-15 流程，2026-11-18/11-20 实机重标定）：

    提示对话框点击一次关闭 → 座骑降落 → OCR 机关标签定位三个机关 →
    按 黄→蓝→红 点标签上方的剑身 → 轮询解除结果：
    - 「换一处再试试」：本机关不对，关闭对话框后换下一个；
    - 「已被破解」/「解除封印」/「已完成玄枢轶闻任务」/剧情式结局
      （非「换一处」的结果对话框）：结束；
    - 超时无结果：点击落空，换下一个机关（不按成功处理）。
    每次点击前重新扫描标签（点击落空可能让角色走动、视角变化），同一
    机关（按颜色/位置）不重复点；已有标签全部点过而仍缺机关（标签被
    人物身体挡住，OCR 读不出）时走开一小步再重扫（2026-11-20 用户
    规则，JIGUAN_REVEAL_*）；轮次上限 JIGUAN_LABEL_ROUNDS。
    返回 (是否成功, 描述)。
    """
    # 1. 点击一次关闭提示对话框（屏幕中间；该对话框无选项）
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log(f"[{prefix}] 已点击关闭封印提示对话框", "SYS")
    time.sleep(0.5)
    # 2. 座骑降落（飞行状态下机关不可见不可点）
    _land(win, prefix)
    # 3. 标签定位 → 逐个尝试（每轮点击前重扫，位置随视角更新）
    clicked = 0
    tried = []
    failed_colors = set()   # 明确提示「换一处」的机关颜色（落空的不记，防误杀）
    reveals = 0
    max_attempts = JIGUAN_LABEL_ROUNDS * len(JIGUAN_CLICK_ORDER)
    while clicked < max_attempts:
        labels = _wait_labels(win)
        if not labels:
            log(f"[{prefix}] 未找到封印机关标签（区域 {JIGUAN_LABEL_REGION}），"
                f"放弃本轮破解", "WARN")
            break
        cands = [(color, pos) for color, pos in _ordered_labels(labels)
                 if color not in failed_colors
                 and all(abs(pos[0] - t[0]) + abs(pos[1] - t[1])
                         > JIGUAN_SAME_SPOT for t in tried)]
        if not cands:
            # 已知标签都点过了但机关没凑齐：某个标签被人物身体挡住，
            # 区域 OCR 读不出（2026-11-20 流洲实测，2x 放大也读不出）。
            # 走开一小步拉开遮挡后重扫（用户规则）。
            if len(tried) < len(JIGUAN_CLICK_ORDER) \
                    and reveals < JIGUAN_REVEAL_MAX \
                    and _step_aside(win, labels, prefix):
                reveals += 1
                continue
            break
        color, (x, y) = cands[0]
        tried.append((x, y))
        clicked += 1
        human_click(win, x, y)
        color_tag = f"·{color}" if color else ""
        log(f"[{prefix}] 已点击封印机关{color_tag} @({x},{y})，等待解除结果",
            "SYS")
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
    return False, f"封印破解未成功（尝试 {clicked} 个机关位置）"
