"""沙海迷踪「记住混沌」小游戏的纯检测/解析逻辑与常量。

触发/armed、阻塞主循环、点击与状态重置仍在 mingwang 任务侧
（`_arm_shahai_start` / `maybe_play` / `play_shahai_miyong`）。
本模块只做文本解析、黑点检测、跨轮询跟踪与烟雾差分校验，
import 期零副作用。
"""
import re
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..core import debug_overlay
from ..device import map as game_map
from ..core.app_utils import human_click, log
from ..device.map import bgr_frame as _bgr_frame


def _press_esc(win):
    """Esc 关闭面板兜底（与 mingwang 同名工具同实现；纯设备操作）。"""
    if app_utils.silent_mode_on():
        from ..device import silent_click  # 延迟导入（避免模块级循环）
        return silent_click.send_silent_hotkey(win, (), "esc") is not None
    if app_utils.keyboard_ok(win):
        Keyboard.key("esc")
        return True
    return False

SHAHAI_GAME_REGION = (363, 262, 1031, 630)    # 地图卷轴区（黑点检测，用户标定）

SHAHAI_DOT_MAX_CH = 60        # 黑点判定：BGR 最大通道 < 60（墨团实测 17~29，山体 ≥55）
SHAHAI_DOT_MIN_AREA = 60      # 连通域面积下限（窗口尺度黑点 ~130-330；固定墨点 ≤40）
SHAHAI_DOT_MAX_AREA = 3000    # 面积上限（防把整块暗色 UI 当黑点）
SHAHAI_DOT_MIN_SIDE = 6       # 包围盒短边下限（像素）
SHAHAI_DOT_MAX_SIDE = 80      # 包围盒长边上限（像素）
SHAHAI_DOT_MIN_FILL = 0.25    # 连通域填充率下限（面积/包围盒）
SHAHAI_DOT_EDGE_MARGIN = 4    # 距区域边缘 <该值 的连通域忽略（弹窗装饰边框）
SHAHAI_DOT_MERGE_PX = 12      # 同一墨团被拆成多块时的合并距离（像素）
SHAHAI_DOT_TRACK_PX = 18      # 跨轮询同一黑点的匹配距离（像素）
SHAHAI_DOT_CONFIRM_HITS = 2   # 记忆阶段同一坐标至少命中轮数（防单帧噪声）
SHAHAI_MAX_DOTS = 4           # 单轮最多记录黑点数（用户实测 2~3 个）

SHAHAI_SMOKE_BOX = 26             # 显形检测半边长（像素）
SHAHAI_SMOKE_DIFF = 8.0           # 显形检测灰度平均绝对差阈值（静态弹窗噪声 ~0-2）
SHAHAI_TOP_REGION = (400, 256, 1031, 300)     # 顶部「第N次地图 剩余X秒」
SHAHAI_MID_REGION = (400, 289, 1031, 336)     # 中部横条（消散/并非混沌所在/结算）
SHAHAI_BOTTOM_REGION = (400, 584, 1031, 646)  # 底部阶段提示（记忆/点击）
SHAHAI_GAME_ARM_TIMEOUT = 30.0    # 点击「开始」后检测小游戏弹窗的时限（秒）
SHAHAI_GAME_CHECK_INTERVAL = 0.5  # armed 弹窗检测限频（秒）
SHAHAI_GAME_TIMEOUT = 150.0       # 小游戏总超时（秒，防卡死）
SHAHAI_POLL_INTERVAL = 0.25       # 主循环节拍（秒）
SHAHAI_TOP_OCR_INTERVAL = 0.8     # 顶部轮次/倒计时 OCR 间隔（秒）
SHAHAI_BOTTOM_OCR_INTERVAL = 0.5  # 底部阶段提示 OCR 间隔（秒；差分门命中只比像素）
SHAHAI_BOTTOM_OCR_FORCE = 2.0     # 底部 OCR 差分门强制重检间隔（秒）
SHAHAI_MID_OCR_INTERVAL = 1.0     # 中部横条 OCR 间隔（秒；结算判定用）
SHAHAI_MID_OCR_FORCE = 3.0        # 中部 OCR 差分门强制重检间隔（秒）
SHAHAI_POPUP_MISS_POLLS = 3       # 顶部文字连续缺失轮数 → 弹窗已关闭
SHAHAI_MEMORY_MIN_SEC = 0.6       # 记忆阶段最短观察时间（防切换瞬间沿用上轮点击提示）
SHAHAI_NEW_ROUND_QUIET = 1.0      # 上轮净化后静默期（残留显形烟雾不记为新黑点）
SHAHAI_NEW_ROUND_GRACE = 2.5      # 上轮净化后点击提示未消退的新轮等待（OCR 卡住兜底）
SHAHAI_DOTS_GONE_POLLS = 3        # 记忆阶段黑点连续消失轮数 → 点击阶段（OCR 不清兜底）
SHAHAI_ZERO_POLLS = 2             # 剩余秒数为 0 连续轮数 → 结束（每轮重置即清零计数）
SHAHAI_CLICK_GAP = 0.25           # 不同坐标之间点击间隔（用户要求 200~300ms）
SHAHAI_PAIR_GAP = 0.20            # 同一坐标两下点击间隔（用户 2026-11-11 机制）
SHAHAI_PURIFY_WAIT = 0.30         # 第二下后等横条反馈/烟雾消散
SHAHAI_RETRY_WAIT = 0.20          # 补点前等待（用户要求 200ms）
SHAHAI_RETRY_CONFIRM = 0.15       # 补点前复检烟雾残留间隔（防消散动画残影误补点）
SHAHAI_END_KEYWORDS = ("通关", "失败", "结算")   # 结算文字（顶部/中部横条）
SHAHAI_OCR_SCORE = 0.55           # 区域 OCR 置信度下限（项目统一）

_SHAHAI_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                     "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_SHAHAI_ROUND_RE = re.compile(r"第\s*([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*次")
_SHAHAI_REMAIN_RE = re.compile(r"剩\s*余?\s*([0-9]{1,3})\s*秒")
_SHAHAI_WS_RE = re.compile(r"\s+")
_SHAHAI_OPEN_KERNEL = np.ones((3, 3), np.uint8)  # 形态学开运算核（去细线噪声）

def _shahai_norm(text):
    """去空白（OCR 偶发在汉字/数字间插空格，如「显 形」「3 秒」）。"""
    return _SHAHAI_WS_RE.sub("", text) if text else text

def _shahai_cn_number(text):
    """中文/阿拉伯数字串 → int（轮次 1~10）；无法解析返回 None。"""
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _SHAHAI_CN_DIGITS.get(left, 1) if left else 1
        ones = _SHAHAI_CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return _SHAHAI_CN_DIGITS.get(text)

def _shahai_parse_top(text):
    """顶部文字 → (轮次, 剩余秒数)；未识别项为 None。

    实测文案「第 2 次地图 剩余 8 秒」（2.png）/「第 1 次地图 剩余 6 秒」
    （3.png）。轮次支持阿拉伯/中文数字；倒计时只认阿拉伯数字。
    """
    if not text:
        return None, None
    round_no = remain = None
    m = _SHAHAI_ROUND_RE.search(text)
    if m:
        round_no = _shahai_cn_number(m.group(1))
    m = _SHAHAI_REMAIN_RE.search(text)
    if m:
        remain = int(m.group(1))
    return round_no, remain

def _shahai_top_present(text):
    """顶部横条在场判定（弹窗关闭检测）：含次地图/剩余秒文案。"""
    if not text:
        return False
    text = _shahai_norm(text)
    return "地图" in text or "剩余" in text

def _shahai_stage_hint(text):
    """底部提示 → "memory"（记忆阶段）/ "click"（点击净化阶段）/ None。

    用户 2026-11-11 规则：OCR 不清时以是否包含「显形」二字区分——点击
    阶段文案「…点击令混沌显形，显形后再次点击将其净化！」含「显形」；
    记忆阶段「请记住混沌出现的位置，3秒后混沌将侵入地图之中」不含。
    """
    if not text:
        return None
    text = _shahai_norm(text)
    if "显形" in text:
        return "click"
    if any(k in text for k in ("请记住", "记住", "3秒", "三秒",
                               "侵入", "侵蚀")):
        return "memory"
    return None

def _shahai_find_dots(bgr, region=None):
    """地图卷轴区深黑混沌墨团检测，返回 [(x, y, area), ...] 窗口相对坐标。

    实测标定（2.png 有黑点 / 3.png 无黑点）：墨团 BGR≈(17,25,29)
    （灰度 25~29、最大通道 < 60），固定山体深色噪点最大通道 ≥ 55、
    且连通域面积（缩放图尺度）≤ 40px。判定链：最大通道阈值 → 3x3
    开运算去细线 → 连通域面积/包围盒/填充率过滤 → 边缘余量排除弹窗
    装饰边框 → 近邻合并（同一墨团被淡边切成多块）。优先保证无黑点
    帧（3.png）零检出，其次不漏检。
    """
    l, t, r, b = region or SHAHAI_GAME_REGION
    h_img, w_img = bgr.shape[:2]
    l, t = max(0, l), max(0, t)
    r, b = min(w_img, r), min(h_img, b)
    if r - l < 2 * SHAHAI_DOT_EDGE_MARGIN \
            or b - t < 2 * SHAHAI_DOT_EDGE_MARGIN:
        return []
    sub = bgr[t:b, l:r]
    mask = (sub.max(axis=2) < SHAHAI_DOT_MAX_CH).astype(np.uint8)
    if not mask.any():
        return []
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _SHAHAI_OPEN_KERNEL)
    n, _, stats, cents = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    rh, rw = sub.shape[:2]
    cand = []
    edge = SHAHAI_DOT_EDGE_MARGIN
    for i in range(1, n):
        x, y = int(stats[i, 0]), int(stats[i, 1])
        bw, bh = int(stats[i, 2]), int(stats[i, 3])
        area = int(stats[i, 4])
        if area < SHAHAI_DOT_MIN_AREA or area > SHAHAI_DOT_MAX_AREA:
            continue
        if bw < SHAHAI_DOT_MIN_SIDE or bh < SHAHAI_DOT_MIN_SIDE \
                or bw > SHAHAI_DOT_MAX_SIDE or bh > SHAHAI_DOT_MAX_SIDE:
            continue
        if not 0.5 <= bw / float(bh) <= 2.0:
            continue
        if area / float(bw * bh) < SHAHAI_DOT_MIN_FILL:
            continue
        if x <= edge or y <= edge or x + bw >= rw - edge \
                or y + bh >= rh - edge:
            continue  # 弹窗装饰边框/贴边噪点
        cand.append([float(cents[i][0]), float(cents[i][1]), float(area)])
    # 同一墨团可能被淡边/高光切成多块：近邻按面积加权合并
    merged = []
    for cx, cy, area in sorted(cand, key=lambda c: -c[2]):
        for mg in merged:
            if (cx - mg[0]) ** 2 + (cy - mg[1]) ** 2 \
                    <= SHAHAI_DOT_MERGE_PX ** 2:
                total = mg[2] + area
                mg[0] = (mg[0] * mg[2] + cx * area) / total
                mg[1] = (mg[1] * mg[2] + cy * area) / total
                mg[2] = total
                break
        else:
            merged.append([cx, cy, area])
    merged.sort(key=lambda c: -c[2])
    return [(int(round(cx)) + l, int(round(cy)) + t, int(area))
            for cx, cy, area in merged]

def _shahai_track_dots(entries, dets, now):
    """把本帧黑点并入跨轮询记录（近邻匹配累加命中次数）。

    记忆阶段约 3 秒（≥8 轮询），同一黑点应反复命中；命中次数
    （hits）用于过滤单帧噪声（SHAHAI_DOT_CONFIRM_HITS）。
    """
    for x, y, area in dets:
        best = None
        best_d2 = SHAHAI_DOT_TRACK_PX ** 2
        for e in entries:
            d2 = (e["x"] - x) ** 2 + (e["y"] - y) ** 2
            if d2 <= best_d2:
                best_d2 = d2
                best = e
        if best is None:
            entries.append({"x": x, "y": y, "area": area,
                            "hits": 1, "last": now})
        else:
            best["x"], best["y"] = x, y
            best["area"] = max(best["area"], area)
            best["hits"] += 1
            best["last"] = now

def _shahai_pick_dots(entries):
    """从跨轮询记录中取确认黑点坐标（按命中次数/面积降序，限 4 个）。"""
    confirmed = [e for e in entries
                 if e["hits"] >= SHAHAI_DOT_CONFIRM_HITS]
    if not confirmed:
        confirmed = list(entries)  # 记忆阶段极短/仅一帧命中：按单帧兜底
    confirmed.sort(key=lambda e: (-e["hits"], -e["area"]))
    return [(e["x"], e["y"]) for e in confirmed[:SHAHAI_MAX_DOTS]]

def _shahai_smoke_score(img0, img1, x, y):
    """显形烟雾检测：点击前后黑点邻域灰度平均绝对差；帧不可用返回 None。

    弹窗背景静态（黑点已淡出的点击阶段），点击后局部出现灰黑色烟雾
    → 邻域像素变化显著（阈值 SHAHAI_SMOKE_DIFF）；无变化 = 点击未
    被注册/未显形，调用方等待 200ms 后同坐标补点。
    """
    if img0 is None or img1 is None:
        return None
    a = np.asarray(img0)
    b = np.asarray(img1)
    if a.shape != b.shape:
        return None
    h, w = a.shape[:2]
    half = SHAHAI_SMOKE_BOX
    l, r = max(0, x - half), min(w, x + half)
    t, bb = max(0, y - half), min(h, y + half)
    if r - l < 4 or bb - t < 4:
        return None
    ga = cv2.cvtColor(a[t:bb, l:r, :3], cv2.COLOR_RGB2GRAY).astype(np.int16)
    gb = cv2.cvtColor(b[t:bb, l:r, :3], cv2.COLOR_RGB2GRAY).astype(np.int16)
    return float(np.abs(ga - gb).mean())

def _shahai_ts(t):
    """时间戳日志格式（时:分:秒.毫秒）。"""
    return time.strftime("%H:%M:%S", time.localtime(t)) \
        + f".{int((t % 1.0) * 1000):03d}"

def _shahai_smoke_left(before, after, x, y):
    """两下点击后邻域烟雾是否仍在：True=残留 / False=已消散 / None=帧不可用。"""
    score = _shahai_smoke_score(before, after, x, y)
    return None if score is None else score >= SHAHAI_SMOKE_DIFF


def _shahai_ocr(img, region):
    """区域 OCR 文本（拼接全部高置信度行）；无文字返回 None。"""
    items, _ = game_map.ocr_regions(img, (region,))
    text = "".join(t for t, _r, s in items if s >= SHAHAI_OCR_SCORE)
    return text or None

def _shahai_text_gated(img, region, cache, force=False):
    """区域 OCR 差分门：区域像素无变化（且非强制）直接复用缓存文本。

    性能规则 5：OCR 前置像素差分预过滤。底部提示/中部横条为静态
    渲染，只有文字切换时像素才变化；缓存命中省一次区域 OCR（约
    100~170ms）。强制重检兜底渲染抖动（SHAHAI_*_OCR_FORCE）。
    """
    crop = np.asarray(img.crop(region))
    if not force and cache.get("crop") is not None \
            and np.array_equal(cache["crop"], crop):
        return cache.get("text")
    cache["crop"] = crop.copy()
    cache["ocr_at"] = time.time()
    cache["text"] = _shahai_ocr(img, region)
    return cache["text"]

def _shahai_click_dot(win, x, y):
    """单个黑点「点两下」：显形 → 0.2s → 净化，返回记录 dict。

    用户 2026-11-11 机制（明确修正）：第一下点击该坐标出现混沌（灰黑
    烟雾团），**间隔 0.2s** 第二下点击即消失（净化）。两下之间不做
    截图/识别，保证间隔不被拖长。第二下后等 SHAHAI_PURIFY_WAIT 再截
    一帧：OCR 中部横条确认「此处混沌已消散！」/「并非混沌所在」；
    横条无反馈且邻域烟雾仍在（两下中有一下被吞）时，按用户规则补点
    一次（200ms 后同坐标）再确认。正常路径严格两下。
    """
    rec = {"x": x, "y": y, "t1": 0.0, "t2": 0.0, "retry": 0.0,
           "smoke_left": None, "message": None, "ok": None}
    time.sleep(SHAHAI_CLICK_GAP)
    before = win.capture(force_activate=False)
    human_click(win, x, y)
    rec["t1"] = time.time()
    time.sleep(SHAHAI_PAIR_GAP)
    human_click(win, x, y)
    rec["t2"] = time.time()
    time.sleep(SHAHAI_PURIFY_WAIT)
    img = win.capture(force_activate=False)
    rec["smoke_left"] = _shahai_smoke_left(before, img, x, y)
    if img is not None:
        rec["message"] = _shahai_ocr(img, SHAHAI_MID_REGION)
    norm = _shahai_norm(rec["message"])
    if norm:
        if "消散" in norm:
            rec["ok"] = True
        elif "并非" in norm:
            rec["ok"] = False
    if rec["ok"] is None and rec["smoke_left"]:
        # 横条无反馈且烟雾仍在外显：两下中有一下被吞（显形未完成），
        # 复检一次确认不是消散动画残影，再补点一次净化
        time.sleep(SHAHAI_RETRY_CONFIRM)
        check = win.capture(force_activate=False)
        if _shahai_smoke_left(before, check, x, y):
            time.sleep(SHAHAI_RETRY_WAIT)
            human_click(win, x, y)
            rec["retry"] = time.time()
            time.sleep(SHAHAI_PURIFY_WAIT)
            img = win.capture(force_activate=False)
            rec["smoke_left"] = _shahai_smoke_left(before, img, x, y)
            if img is not None:
                rec["message"] = _shahai_ocr(img, SHAHAI_MID_REGION)
            norm = _shahai_norm(rec["message"])
            if norm:
                if "消散" in norm:
                    rec["ok"] = True
                elif "并非" in norm:
                    rec["ok"] = False
    return rec

def _shahai_purify_dots(win, dots, prefix="名望"):
    """逐个净化本轮记录的黑点，返回每位置记录列表（日志含两次点击时间）。"""
    recs = []
    for idx, (x, y) in enumerate(dots, 1):
        log(f"[{prefix}] 沙海迷踪：净化第 {idx}/{len(dots)} 个黑点 "
            f"@({x},{y})", "INFO")
        rec = _shahai_click_dot(win, x, y)
        recs.append(rec)
        smoke = "已消散" if rec["smoke_left"] is False else \
            ("烟雾仍在" if rec["smoke_left"] else "烟雾未知")
        result = "消散" if rec["ok"] else \
            ("点错（并非混沌所在）" if rec["ok"] is False else "未识别")
        retry = f"，补点 {_shahai_ts(rec['retry'])}" if rec["retry"] else ""
        log(f"[{prefix}] 沙海迷踪：@({x},{y}) 显形点击 {_shahai_ts(rec['t1'])}"
            f" / 净化点击 {_shahai_ts(rec['t2'])}{retry}，{smoke}，"
            f"横条=「{rec['message'] or '未识别'}」（{result}）", "SYS")
    return recs

def play_shahai_miyong(win, first_img=None, prefix="名望"):
    """沙海迷踪「记住混沌」小游戏闭环（阻塞到弹窗关闭/结算/超时）。

    流程（用户 2026-11-11 完整描述 + 2.png/3.png 实测）：
    1. 等待地图卷轴区出现深黑墨团（记忆阶段；底部提示含「请记住」
       且不含「显形」）。跨轮询记录坐标（命中 ≥2 次确认，防单帧噪声）。
    2. 底部提示切换为「…点击令混沌显形…」（或黑点连续消失 2 轮）→
       进入点击净化阶段；底部提示 OCR 不清时以是否含「显形」区分。
    3. 对每个记录坐标点两下（第一下显形 → 间隔 0.2s 第二下净化；
       用户 2026-11-11 机制），横条 OCR 确认「此处混沌已消散！」；
       两下被吞（烟雾仍在且横条无反馈）则 200ms 后同坐标补点一次。
    4. 一轮完成不退出，等待下一批墨团（顶部「第 N 次地图」递增），
       黑点数量/位置与上轮不符时按当前帧视觉结果重新记录。
    5. 顶部「剩余 X 秒」倒计时耗尽、弹窗关闭（顶部文字连续缺失）或
       顶部/中部出现结算文字（通关/失败/结算）→ 结束。
    最终日志输出每轮黑点坐标、每位置两次点击时间、轮次/剩余倒计时
    与净化是否成功（用户要求）。

    返回 (是否正常结束, 描述)。
    """
    t_start = time.time()
    deadline = t_start + SHAHAI_GAME_TIMEOUT
    log(f"[{prefix}] 沙海迷踪小游戏开始 {time.strftime('%H:%M:%S')}，"
        f"黑点检测区 {SHAHAI_GAME_REGION}", "SYS")
    debug_overlay.set_phase("沙海迷踪")
    results = []              # 已完成轮次记录（用户要求最终输出）
    entries = []              # 当前记忆阶段跨轮询黑点记录
    phase = "wait"            # wait=等黑点 / memory=记忆阶段
    cur = None                # 当前轮记录
    round_no = None
    remain = None
    stage = None              # 底部提示阶段（memory/click/None）
    memory_started = 0.0      # 本轮记忆阶段起点（最短观察时间用）
    round_done_at = 0.0       # 上轮净化完成时间（新轮等待，防显形烟雾误检）
    top_miss = 0
    dots_gone = 0
    zero_polls = 0
    last_top_ocr = 0.0
    last_bottom_ocr = 0.0
    last_mid_ocr = 0.0
    bottom_cache = {"crop": None, "text": None, "ocr_at": 0.0}
    mid_cache = {"crop": None, "text": None, "ocr_at": 0.0}
    img = first_img
    reason = "超时"
    while time.time() < deadline:
        tick = time.time()
        if img is None:
            img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.1)
            continue
        bgr = _bgr_frame(img)
        now = time.time()

        # ---- 顶部：轮次 / 剩余倒计时 / 结算文字（限频 OCR）----
        if now - last_top_ocr >= SHAHAI_TOP_OCR_INTERVAL:
            last_top_ocr = now
            top = _shahai_ocr(img, SHAHAI_TOP_REGION)
            rnd, sec = _shahai_parse_top(top)
            if _shahai_top_present(top):
                top_miss = 0
                if rnd is not None:
                    if round_no is not None and rnd != round_no:
                        log(f"[{prefix}] 沙海迷踪：进入第 {rnd} 次地图"
                            f"（上一轮第 {round_no} 次）", "SYS")
                    round_no = rnd
                if sec is not None:
                    remain = sec
                    zero_polls = 0 if sec > 0 else zero_polls + 1
            else:
                top_miss += 1
            if top and any(k in _shahai_norm(top)
                           for k in SHAHAI_END_KEYWORDS):
                reason = f"结算文字「{top}」"
                break
            if zero_polls >= SHAHAI_ZERO_POLLS and phase == "wait":
                reason = f"倒计时耗尽（第 {round_no} 次地图）"
                break
        if top_miss >= SHAHAI_POPUP_MISS_POLLS:
            reason = "弹窗已关闭"
            break

        # ---- 中部横条（消散/并非/结算；差分门 + 限频）----
        if now - last_mid_ocr >= SHAHAI_MID_OCR_INTERVAL:
            last_mid_ocr = now
            force = now - mid_cache.get("ocr_at", 0.0) \
                >= SHAHAI_MID_OCR_FORCE
            mid = _shahai_text_gated(img, SHAHAI_MID_REGION,
                                     mid_cache, force=force)
            if mid and any(k in _shahai_norm(mid)
                           for k in SHAHAI_END_KEYWORDS):
                reason = f"结算文字「{mid}」"
                break

        # ---- 底部阶段提示（差分门 + 限频）----
        if now - last_bottom_ocr >= SHAHAI_BOTTOM_OCR_INTERVAL:
            last_bottom_ocr = now
            force = now - bottom_cache.get("ocr_at", 0.0) \
                >= SHAHAI_BOTTOM_OCR_FORCE
            new_stage = _shahai_stage_hint(
                _shahai_text_gated(img, SHAHAI_BOTTOM_REGION,
                                   bottom_cache, force=force))
            if new_stage is not None:
                stage = new_stage

        # ---- 黑点检测（每轮询一帧，同帧复用）----
        dets = _shahai_find_dots(bgr)

        if phase == "wait":
            # 上轮净化后的显形烟雾可能仍暗于阈值：净化完成后至少静默
            # 1s；底部仍是点击提示时不开始新记忆记录（2.5s 宽限结束
            # 仍允许，防提示 OCR 卡住导致新轮永远不记录）
            since_done = now - round_done_at
            allow_new = since_done >= SHAHAI_NEW_ROUND_QUIET and (
                stage != "click" or since_done >= SHAHAI_NEW_ROUND_GRACE)
            if dets and allow_new:
                entries = []
                _shahai_track_dots(entries, dets, now)
                cur = {"round": round_no, "remain": remain,
                       "dots": _shahai_pick_dots(entries),
                       "seen_at": now, "clicks": []}
                phase = "memory"
                memory_started = now
                dots_gone = 0
                log(f"[{prefix}] 沙海迷踪：记忆阶段检测到混沌黑点 "
                    f"{len(dets)} 个（第 {round_no} 次地图，剩余 "
                    f"{remain}s），坐标 {cur['dots']}，观察中", "SYS")
        elif phase == "memory":
            if dets:
                _shahai_track_dots(entries, dets, now)
                new_dots = _shahai_pick_dots(entries)
                if new_dots and new_dots != cur["dots"]:
                    # 黑点数量/位置与记录不符：以当前帧视觉结果为准
                    cur["dots"] = new_dots
                dots_gone = 0
            else:
                dots_gone += 1
            # 记忆阶段最短观察时间：本轮黑点出现瞬间可能仍沿用上轮
            # 「点击」提示（渲染过渡帧），至少观察 0.6s 再接受阶段切换
            stage_click = stage == "click" \
                and now - memory_started >= SHAHAI_MEMORY_MIN_SEC
            switched = stage_click or dots_gone >= SHAHAI_DOTS_GONE_POLLS
            if switched:
                cur["dots"] = _shahai_pick_dots(entries)
                cur["remain"] = remain
                if not cur["dots"]:
                    # 记忆阶段极短/检测失败未记录到坐标：回等待
                    log(f"[{prefix}] 沙海迷踪：记忆阶段切换时未记录到"
                        f"黑点坐标，等待下一批", "WARN")
                    phase = "wait"
                    cur = None
                else:
                    why = "底部提示含显形" if stage == "click" \
                        else "黑点消失兜底"
                    log(f"[{prefix}] 沙海迷踪：阶段切换（{why}），"
                        f"进入点击净化，坐标 {cur['dots']}", "SYS")
                    cur["clicks"] = _shahai_purify_dots(
                        win, cur["dots"], prefix)
                    results.append(cur)
                    ok_n = sum(1 for c in cur["clicks"] if c.get("ok"))
                    log(f"[{prefix}] 沙海迷踪：第 {cur['round']} 次地图"
                        f"本轮净化完成，确认消散 {ok_n}/"
                        f"{len(cur['clicks'])} 个位置，等待下一批黑点",
                        "SYS")
                    phase = "wait"
                    cur = None
                    entries = []
                    dots_gone = 0
                    round_done_at = time.time()
                    # 保留 stage（通常仍为 click）：新轮等待期间靠它
                    # 拦住残留烟雾误检，下一轮记忆提示 OCR 会覆盖
                    img = None  # 点击后画面已变：下轮重新截帧
                    continue

        img = None
        rem = SHAHAI_POLL_INTERVAL - (time.time() - tick)
        if rem > 0:
            time.sleep(rem)

    # 非正常关闭（超时/结算）且弹窗仍在：Esc 兜底关闭，防模态弹窗
    # 残留挡住后续任务跟踪（先复核弹窗在场，防 Esc 落到游戏世界开菜单）
    if reason != "弹窗已关闭":
        check = win.capture(force_activate=False)
        if check is not None and _shahai_game_present(check):
            log(f"[{prefix}] 沙海迷踪：{reason}，弹窗仍在，Esc 兜底关闭",
                "WARN")
            _press_esc(win)

    # ---- 最终输出（用户要求：每轮坐标/两次点击时间/轮次与倒计时/净化结果）----
    for r in results:
        clicks_desc = "；".join(
            f"({c['x']},{c['y']}) 显形 {_shahai_ts(c['t1'])} / 净化 "
            f"{_shahai_ts(c['t2'])}"
            f"{'（消散）' if c.get('ok') else ''}"
            f"{'（并非混沌所在）' if c.get('ok') is False else ''}"
            f"{'（未识别）' if c.get('ok') is None else ''}"
            for c in r.get("clicks") or [])
        log(f"[{prefix}] 沙海迷踪 第 {r.get('round')} 次地图"
            f"（剩余 {r.get('remain')}s）：黑点 "
            f"{[tuple(d) for d in r.get('dots') or []]}；"
            f"{clicks_desc or '(无点击记录)'}", "SYS")
    total = sum(len(r.get("clicks") or []) for r in results)
    ok_n = sum(1 for r in results for c in r.get("clicks") or []
               if c.get("ok"))
    cost = time.time() - t_start
    log(f"[{prefix}] 沙海迷踪结束（{reason}）：共 {len(results)} 轮 / "
        f"{total} 个位置，确认消散 {ok_n} 个，用时 {cost:.1f}s", "SYS")
    return True, (f"沙海迷踪小游戏结束（{reason}，{len(results)} 轮，"
                  f"确认消散 {ok_n}/{total}）")

def _shahai_game_present(img):
    """小游戏弹窗在场判定：顶部轮次/倒计时可解析，或底部阶段提示命中。

    顶部要求能解析出「第 N 次」或「剩余 X 秒」（不能只凭「地图/秒」
    字样——游戏世界/追踪面板的普通文案可能含这些字，误触发会进入
    小游戏循环空跑）；底部记忆/点击提示文案特征强，可独立判定。
    """
    text = _shahai_ocr(img, SHAHAI_TOP_REGION)
    rnd, sec = _shahai_parse_top(text)
    if rnd is not None or sec is not None:
        return True
    text = _shahai_ocr(img, SHAHAI_BOTTOM_REGION)
    return _shahai_stage_hint(text) is not None
