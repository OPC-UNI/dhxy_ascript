# -*- coding: utf-8 -*-
"""天下无贼（300环）小游戏：两种类型共用入口，进战斗/竞技场后区分。

- 竞技场型（play）：敲打江湖大盗/强盗/强盗僵尸，避开小可爱/老人；
- 战斗型（run_firecracker_battle）：系统赠送 10 个大型爆竹，右键背包
  爆竹后左键点怪，十字爆炸清怪；召唤兽每回合防御。详见文件后半部分
  「战斗型：大型爆竹」区块（2026-11-27 用户规则 + 实机）。

2026-11-27 实机标定（可乐可口 36096466），竞技场型流程与判据：
- 入口：任务追踪「天下无贼[江湖历练任务]」→ 点击追踪绿字到乌鸡国王后 →
  对话框选项「毛贼！休想逃！(130分以上即可通关)」（同屏另有「找你有其他
  事情」「天下无贼，只是一个梦。」）→ 点击后规则面板约 9~10s（
  「拿起大锤，敲打江湖大盗，强盗和强盗僵尸吧，可不要打到小孩，老人哦。」
  + 五种怪图例 江湖大盗 +6 / 强盗 +4 / 强盗僵尸 +5 / 小可爱 -3 / 老人 -5
  +「还有 N 秒 开始」）→ 浮空岛竞技场 48s 倒计时。
- 竞技场：怪物从场地边缘走进来、停留数秒后离开；点击命中即时加分并
  消失。**玩家点得越快，出怪越快**（2026-11-27 用户实机规则）——检测到
  正分怪立即点击，轮询/分类全部走快路径（OPENCV 帧 + cv2.absdiff +
  bincount 直方图，单轮 ~40ms，实测 0.14s 轮询一局 180 分）。
- 结束：达标 → 系统频道「剿灭强盗任务结束，你得到N分」+ 直接回世界
  （**没有对话框**，任务推进下一环）；未达标 → 「(面面相觑)坏了，尴尬了…」
  多屏无选项剧情对话（点屏推进），任务追踪不变，需重走追踪链接再次挑战
  （重试由任务侧驱动）。结束判定必须双信号：计分条消失（主）+ 对话框
  模板（次）——2026-11-27 22:35 实机教训：达标只走系统频道文案，模板
  判据等不到对话框，白等到 65s 超时（任务其实已推进下一环）。
- 背景基准：竞技场是固定浮空岛，怪物是唯一运动元素。顶部黑色计分条
  「分数/倒计时」的黄色字像素（区域 560,285-720,335，面板期 160 vs
  竞技场 392~540）首次出现即抓一帧作背景（计分条出现约 0.5s 后第一只
  怪才入场，0.1s 轮询能抓到干净帧）。
- 怪物识别：帧 vs 背景差分圈候选（面积/边长过滤），候选内取「去背景后
  的 sprite 像素」（diff>50）算 8^3 RGB 颜色直方图，与 res/img/
  txwz_ref_*.png 参考色样做直方图交集分类，同类取最高分。
  模板匹配不可用（2026-11-27 实测：带背景模板跨位置同姿态仅 0.30~0.63
  分；掩码 SQDIFF 各类型都 ~0.8 无区分度）；颜色直方图留一交叉验证
  24/24（28 张参考色样自检全部命中本类，最低同类分 0.76、次高类 <=0.71）。
- 幽灵候选抑制（2026-11-27 用户实机反馈：怪物出现后消失、点击才落到原
  位置）：背景抓取晚于怪物入场时，会把某只怪烘进背景，它离开后该位置
  持续出现「幽灵」候选（sprite 像素就是烘进去的那只怪，分类分数不低）。
  幽灵像素全场逐帧完全不变，真怪有呼吸/走路动画每 0.2~0.4s 必变
  （实机标定）；同一位置候选连续 GHOST_STATIC_SECONDS 秒像素全等即判
  幽灵，本轮不再点它。

参考色样由实机帧的去背景 sprite 像素生成（RGBA，alpha=diff>50 掩码），
运行时按 alpha>0 像素重算直方图，与对局中的取样口径一致。
import 期零副作用；竞技场由任务侧 wait_handler 调用 play()，战斗型由
任务侧在入口选项出现时把 run_firecracker_battle 注入 battle 战斗计划，
战斗线程进场后阻塞调用。
"""
import os
import re
import time

import cv2
import numpy as np
from PIL import Image
from ascript.windows.system import R
from ascript.windows.window.Window import WindowCaptureFormat

from ..core import app_utils
from ..core.app_utils import human_click, log
from ..device import bag as bag_lib
from ..device import battle as battle_lib
from ..device import items as item_catalog
from ..device import map as game_map
from ..device import task_tracker
from ..device.dialog_common import DIALOG_FRAME_THRESHOLD, dialog_frame_score

# ---- 入口/过程文案（OCR 判据）----
ENTRY_OPTION_TEXT = "毛贼！休想逃！"     # 入口绿字选项（含「(130分以上即可通关)」后缀）
INTRO_TEXT = "拿起大锤"                  # 规则面板首句（实机 OCR 稳定 1.00）
# ---- 竞技场开始检测：顶部计分条黄色字 ----
# 黄色「分数：/倒计时：」实测 RGB (241,240,2)~(255,255,0)；区域
# (560,285)-(720,335) 内黄色像素数：规则面板期 160（面板金边）、竞技场
# 392~540，取 300 分离。
SCORE_REGION = (560, 285, 720, 335)
SCORE_YELLOW_MIN = 300
# ---- 竞技场与怪物候选（2026-11-27 实机录制标定）----
ARENA_REGION = (430, 240, 940, 540)   # 浮空岛场地（怪物全部落点 + 边角余量）
BG_DIFF_THRESHOLD = 40                # 背景差分阈值（场景静态，实测噪声 <=35）
BLOB_MIN_AREA = 400                   # 候选最小面积（过滤草叶/粒子）
BLOB_MIN_W, BLOB_MAX_W = 12, 150      # 候选边长范围（怪物实测 24~80 宽）
BLOB_MIN_H, BLOB_MAX_H = 20, 150      # （同高实测 43~105）
SPRITE_PIXEL_DIFF = 50                # 直方图取样像素的 diff 阈值（同参考色样）
# ---- 颜色直方图分类（与参考色样同口径）----
HIST_BINS = 8                         # 8 档/通道（>>5），bincount 快路径
MATCH_MIN_SCORE = 0.45                # 最高类得分下限（实机最低命中 ~0.55）
MATCH_MIN_MARGIN = 0.05               # 与次高类最小差（防 Q/Z 邻近类误判）
# 可敲打的怪（正分）；小可爱/老人不点
POSITIVE_TYPES = ("qiangdao", "zombie", "dadao")
TYPE_NAMES = {"qiangdao": "强盗", "zombie": "强盗僵尸", "dadao": "江湖大盗",
              "kid": "小可爱", "old": "老人"}
# 参考色样清单（实机去背景 sprite 裁剪，RGBA；2026-11-27 录制生成）。
# 显式列名：res 模板审计测试要求每个模板文件被源码引用，也防误删素材。
# 生成口径见模块注释；同类多张覆盖朝向/走路姿态，分类时取同类最高分。
REF_FILES = (
    "txwz_ref_qiangdao_1.png", "txwz_ref_qiangdao_2.png",
    "txwz_ref_qiangdao_3.png", "txwz_ref_qiangdao_4.png",
    "txwz_ref_qiangdao_5.png", "txwz_ref_qiangdao_6.png",
    "txwz_ref_qiangdao_7.png", "txwz_ref_qiangdao_8.png",
    "txwz_ref_qiangdao_9.png", "txwz_ref_qiangdao_10.png",
    "txwz_ref_qiangdao_11.png",
    "txwz_ref_zombie_1.png", "txwz_ref_zombie_2.png",
    "txwz_ref_zombie_3.png", "txwz_ref_zombie_4.png",
    "txwz_ref_dadao_1.png", "txwz_ref_dadao_2.png", "txwz_ref_dadao_3.png",
    "txwz_ref_kid_1.png", "txwz_ref_kid_2.png", "txwz_ref_kid_3.png",
    "txwz_ref_kid_4.png", "txwz_ref_kid_5.png",
    "txwz_ref_old_1.png", "txwz_ref_old_2.png", "txwz_ref_old_3.png",
    "txwz_ref_old_4.png", "txwz_ref_old_5.png",
)
# ---- 节奏/超时 ----
POLL_INTERVAL = 0.05                  # 对局轮询目标间隔（秒；点得越快出怪越快）
ARENA_START_TIMEOUT = 25.0            # 规则面板 → 竞技场开始等待（倒计时 9~10s）
ROUND_TIMEOUT = 65.0                  # 对局兜底（正常 48s 倒计时）
END_CONFIRM = 2                       # 结束判定：连续命中次数（每个信号独立计数）
END_CHECK_EVERY = 3                   # 对话框模板每 N 轮询查一次（~18ms/次）
# 计分条消失判定（每轮查，~0.2ms）：对局中该区域只有分数/倒计时数字
# 本身在变（局部小占比）；结束后整条消失（露出天空）或被结算对话框
# 盖住，区域大面积与背景不同。达标结束时游戏只在系统频道出一条文案，
# **不弹对话框**——2026-11-27 22:35 实机：任务已推进下一环，模板判据
# 等不到对话框，白等到 65s 超时。阈值取 0.5（数字变化的占比远小于
# 0.5，条消失/被盖远大于 0.5），实际分离度待结束帧实机复核。
END_BAR_GONE_RATIO = 0.5
# 开场保护：计分条出现后约 0.5s 才来第一只怪、整局 48s；前 5s 内
# 计分条自身还可能在淡入/位置微调，禁用「条消失」信号防误判。
END_BAR_MIN_ELAPSED = 5.0
END_SCORE_WAIT = 0.6                  # 结束后等对话框文字渲染再读分（秒）
CLICK_COOLDOWN = 0.8                  # 同位置重复点击冷却（秒；命中的怪
                                      # 0.3~0.5s 内消失，冷却只防同一只被连敲，
                                      # 压短让新刷出的怪尽快可点——点得越快出怪越快）
CLICK_RADIUS = 26                     # 同位置判定半径（px）
MAX_CLICKS_PER_POLL = 3               # 单轮询最多点击次数（多怪同屏）
# ---- 幽灵候选抑制（背景基准不干净时，怪物离开的原位置会持续出候选）----
GHOST_STATIC_SECONDS = 1.2            # 同位置像素全等超过该时长即判幽灵
GHOST_PIXEL_EPS = 1.0                 # 「像素全等」的平均差阈值（真怪动画必变）

_REF_CACHE = None


def _quantize(pixels):
    """RGB/BGR 像素样本 → 8^3 归一化直方图（bincount 快路径，~0.4ms）。

    通道顺序任意但须与参考色样一致（统一 BGR：OpenCV 帧原样取样，
    参考色样加载时 RGB→BGR 翻转）。
    """
    if pixels is None or len(pixels) < 20:
        return None
    p = pixels.astype(np.uint8)
    idx = ((p[:, 0].astype(np.int32) >> 5) << 6) \
        | ((p[:, 1].astype(np.int32) >> 5) << 3) \
        | (p[:, 2].astype(np.int32) >> 5)
    hist = np.bincount(idx, minlength=HIST_BINS ** 3).astype(np.float32)
    total = float(hist.sum())
    return hist / total if total else None


def _load_refs():
    """加载参考色样（缓存）：{类型: [直方图, ...]}；缺失返回空字典。

    参考色样为 RGBA PNG（alpha=去背景 sprite 像素掩码），按 alpha>0 的
    像素计算直方图（RGB→BGR 翻转后与 OpenCV 帧同序）。
    """
    global _REF_CACHE
    if _REF_CACHE is not None:
        return _REF_CACHE
    refs = {}
    try:
        img_dir = str(R.res("img"))
    except Exception:
        img_dir = ""
    for name in REF_FILES:
        parts = name.split("_")
        if len(parts) < 4:
            continue
        label = parts[2]
        try:
            with Image.open(os.path.join(img_dir, name)) as im:
                arr = np.asarray(im.convert("RGBA"))
        except Exception:
            continue
        pixels = arr[arr[:, :, 3] > 0][:, :3][:, ::-1]  # RGB→BGR
        hist = _quantize(pixels)
        if hist is not None:
            refs.setdefault(label, []).append(hist)
    if not refs:
        log("[天下无贼] 参考色样缺失（res/img/txwz_ref_*.png），无法识别怪物",
            "WARN")
    _REF_CACHE = refs
    return refs


def arena_started(frame_bgr):
    """单帧判定竞技场已开始（顶部黄色计分条出现）。frame 为 BGR numpy 帧。"""
    if frame_bgr is None:
        return False
    l, t, r, b = SCORE_REGION
    if frame_bgr.shape[0] < b or frame_bgr.shape[1] < r:
        return False
    sub = frame_bgr[t:b, l:r].astype(np.int32)
    blue, green, red = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    yellow = (red > 180) & (green > 140) & (blue < 90) \
        & (red - blue > 110) & (green - blue > 80)
    return int(yellow.sum()) >= SCORE_YELLOW_MIN


def score_bar_gone(frame_bgr, bg_bgr):
    """计分条消失/被遮挡判定：SCORE_REGION 与背景基准的差异像素占比。

    对局中该区域是常驻计分条（分数/倒计时数字变化只占小部分像素）；
    本局结束后计分条整体消失（露出天空）或被结算对话框盖住，区域
    大面积与背景不同。这是「达标结束」在无对话框时唯一可靠的结束信号
    （2026-11-27 22:35 实机：任务已推进，模板判据白等到 65s 超时）。
    """
    l, t, r, b = SCORE_REGION
    if frame_bgr is None or frame_bgr.shape[0] < b \
            or frame_bgr.shape[1] < r:
        return False
    diff = cv2.absdiff(frame_bgr[t:b, l:r], bg_bgr[t:b, l:r]).max(axis=2)
    return float((diff > BG_DIFF_THRESHOLD).mean()) >= END_BAR_GONE_RATIO


def _blobs(frame_bgr, bg_bgr):
    """当前帧 vs 背景的怪物候选框（全帧坐标），返回 [(x, y, w, h), ...]。

    cv2.absdiff（uint8 SIMD ~4ms/510x300）+ 7x7 闭运算合并躯干/四肢碎块；
    面积与边长过滤掉草叶、粒子和小游戏 UI 碎片。
    """
    l, t, r, b = ARENA_REGION
    diff = cv2.absdiff(frame_bgr[t:b, l:r], bg_bgr[t:b, l:r]).max(axis=2)
    mask = (diff > BG_DIFF_THRESHOLD).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for k in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[k][:5])
        if area < BLOB_MIN_AREA:
            continue
        if not (BLOB_MIN_W <= w <= BLOB_MAX_W and BLOB_MIN_H <= h <= BLOB_MAX_H):
            continue
        out.append((l + x, t + y, w, h))
    return out


def classify(frame_bgr, bg_bgr, blob, refs):
    """候选框分类：返回 (类型, 最高分, 次高分)；无法取样返回 (None,0,0)。

    取样 = 候选框内 diff>50 的 sprite 像素（去背景后的真实配色）；
    分数 = 与各参考直方图的交集，取每类最高分排序。
    """
    x, y, w, h = blob
    sub = frame_bgr[y:y + h, x:x + w]
    ref = bg_bgr[y:y + h, x:x + w]
    mask = cv2.absdiff(sub, ref).max(axis=2) > SPRITE_PIXEL_DIFF
    hist = _quantize(sub[mask])
    if hist is None:
        return None, 0.0, 0.0
    scores = []
    for label, hist_list in refs.items():
        best = 0.0
        for ref_hist in hist_list:
            inter = float(np.minimum(hist, ref_hist).sum())
            if inter > best:
                best = inter
        scores.append((best, label))
    if not scores:
        return None, 0.0, 0.0
    scores.sort(reverse=True)
    top_score, top_label = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    return top_label, top_score, second


def _note_candidate(ghost_state, ghosts, key, crop, now):
    """幽灵抑制状态更新：返回 True = 本候选判为背景幽灵（调用方跳过）。

    同一位置键的候选像素与上次快照近似全等 → 视为「未变化」：超过
    GHOST_STATIC_SECONDS 仍全等即判幽灵（背景里烘进了怪物的位置）；
    像素有变化（真怪呼吸/走路动画，实测 0.2~0.4s 必变）则重置计时并
    更新快照。ghost_state[key] = [上次变化时刻, 上次像素快照]。
    """
    snapshot = ghost_state.get(key)
    if snapshot is not None and snapshot[1].shape == crop.shape \
            and float(cv2.absdiff(crop, snapshot[1]).mean()) < GHOST_PIXEL_EPS:
        if now - snapshot[0] > GHOST_STATIC_SECONDS:
            ghosts.add(key)
            return True
        return False
    ghost_state[key] = [now, crop.copy()]
    return False


def _capture(win):
    """对局截帧：BGR ndarray（OpenCV 格式），force_activate=False 不抢焦点。"""
    return win.capture(force_activate=False,
                       format=WindowCaptureFormat.OPENCV)


def _read_round_score(win):
    """结束后读对话框里的得分文案（「你得到N分」），失败返回空串。"""
    try:
        time.sleep(END_SCORE_WAIT)
        img = win.capture(force_activate=False)
        if img is None:
            return ""
        items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        joined = "".join(t for t, _r, s in (items or []) if s >= 0.55)
        m = re.search(r"得到\s*(\d+)\s*分", joined)
        return f"得分 {m.group(1)}" if m else ""
    except Exception:
        return ""


def play(win, prefix="300环"):
    """天下无贼完整对局（阻塞）：等竞技场开始 → 敲正分怪到对话框出现。

    返回 (是否正常进入并打完一局, 描述)。未进入竞技场（规则面板超时、
    对话框在场）返回 False 交回任务侧；对局结束（无论分数）返回 True，
    由任务侧按任务追踪是否推进决定重试。
    """
    tag = f"[{prefix}] " if prefix else ""
    refs = _load_refs()
    if not refs:
        return False, "参考色样缺失（res/img/txwz_ref_*.png）"
    # ---- 等竞技场开始（规则面板倒计时期间不抓背景）----
    deadline = time.time() + ARENA_START_TIMEOUT
    bg = None
    while time.time() < deadline:
        app_utils.raise_if_aborted()
        frame = _capture(win)
        if frame is None:
            time.sleep(0.1)
            continue
        if dialog_frame_score(frame) >= DIALOG_FRAME_THRESHOLD:
            return False, "等待竞技场时对话框在场（本局未开始）"
        if arena_started(frame):
            bg = frame.copy()
            break
        time.sleep(0.1)
    if bg is None:
        return False, f"{ARENA_START_TIMEOUT:.0f}s 内竞技场未开始"
    log(f"{tag}天下无贼：竞技场开始，背景基准已抓取", "SYS")
    # ---- 对局循环：捕获→差分→分类→点击，单轮 ~40ms ----
    clicks = 0
    last_clicks = []          # (时间, x, y)：同位置冷却
    type_hits = {}
    end_hits = 0
    bar_end_hits = 0
    poll_no = 0
    started = time.time()
    ended = False
    # 幽灵抑制：位置键 → [上次像素变化时刻, 上次候选像素快照]
    ghost_state = {}
    ghosts = set()
    while time.time() - started < ROUND_TIMEOUT:
        app_utils.raise_if_aborted()
        now = time.time()
        frame = _capture(win)
        if frame is None:
            time.sleep(POLL_INTERVAL)
            continue
        poll_no += 1
        # 计分条消失（每轮查，~0.2ms；主信号，达标结束无对话框）
        if now - started >= END_BAR_MIN_ELAPSED:
            if score_bar_gone(frame, bg):
                bar_end_hits += 1
                if bar_end_hits >= END_CONFIRM:
                    ended = True
                    break
            else:
                bar_end_hits = 0
        # 对话框模板限频检查（~18ms/次）：未达标结束弹出失败对话
        if poll_no % END_CHECK_EVERY == 0:
            if dialog_frame_score(frame) >= DIALOG_FRAME_THRESHOLD:
                end_hits += 1
                if end_hits >= END_CONFIRM:
                    ended = True
                    break
            else:
                end_hits = 0
        clicked_this = 0
        for blob in _blobs(frame, bg):
            x, y, w, h = blob
            key = (x // 24, y // 24)
            if key in ghosts:
                continue
            # 幽灵抑制：同位置候选连续像素全等（真怪动画 0.2~0.4s 必变）
            crop = frame[y:y + h, x:x + w]
            if _note_candidate(ghost_state, ghosts, key, crop, now):
                log(f"{tag}天下无贼：位置 {key} 候选长时间静止，判背景幽灵"
                    f"（跳过）", "INFO")
                continue
            label, score, second = classify(frame, bg, blob, refs)
            if label not in POSITIVE_TYPES:
                continue
            if score < MATCH_MIN_SCORE or score - second < MATCH_MIN_MARGIN:
                continue
            cx, cy = x + w // 2, y + h // 2
            if any(now - t0 < CLICK_COOLDOWN
                   and abs(cx - px) < CLICK_RADIUS
                   and abs(cy - py) < CLICK_RADIUS
                   for t0, px, py in last_clicks):
                continue
            human_click(win, cx, cy)
            clicks += 1
            clicked_this += 1
            last_clicks.append((now, cx, cy))
            type_hits[label] = type_hits.get(label, 0) + 1
            if clicked_this >= MAX_CLICKS_PER_POLL:
                break
        last_clicks = [c for c in last_clicks if now - c[0] < CLICK_COOLDOWN + 2.0]
        slept = POLL_INTERVAL - (time.time() - now)
        if slept > 0:
            time.sleep(slept)
    duration = time.time() - started
    if not ended:
        log(f"{tag}天下无贼：对局超时（{duration:.0f}s，点击 {clicks} 次）", "WARN")
        return True, f"对局超时结束（点击 {clicks} 次）"
    score_text = _read_round_score(win)
    detail = "、".join(f"{TYPE_NAMES.get(k, k)}{v}" for k, v in type_hits.items())
    return True, (f"对局结束（{duration:.0f}s，点击 {clicks} 次"
                  f"{'：' + detail if detail else ''}"
                  f"{'，' + score_text if score_text else ''}）")


# ============================================================================
# 战斗型：大型爆竹（天下无贼第二种类型，2026-11-27 用户规则 + 实机）
# ============================================================================
# 与竞技场型共用追踪文案/入口选项，进战斗后才能区分：战斗开场「请等待」
# + 中心日志 10 条「你获得了大型爆竹。」→ 第 N 回合 + 右侧竖栏。
#
# 操作规则（用户 2026-11-27 确认）：
# - 一回合两个阶段：人物操作 → 召唤兽操作。右侧竖栏 8 个按钮（法术/
#   道具/防御/保护/召唤/召还/捕捉/逃跑）= 人物阶段；只剩 4 个（法术/
#   道具/防御/保护）= 召唤兽阶段。顶部红色倒计时出现 = 本回合可操作窗口，
#   操作完成即消失。
# - 进战斗先取消自动战斗（用户 2026-11-27）：上一场常规战斗挂上的自动
#   会跨场持续，自动提交人物行动后爆竹无法生效。
# - 人物：点「道具」开背包 → 右键「大型爆竹」→ 左键点怪身上（躯干；
#   点脚/地面无效）。召唤兽：点「防御」。
# - 背包跨场景记住上次使用的储物格（给与/任务储物格等流程会切换），
#   开战道具栏未必停在爆竹所在格——当前格未找到时逐格切换重查。
# - 每回合只能用 1 个爆竹（人物动作），系统开战赠送 10 个。
#
# 爆炸与怪物（用户 2026-11-27）：
# - 两排 × 5 列固定格子。十字范围 = 目标 + 同排左右 + 另一排同列，
#   共 4 格（角落/边按存在格截断）。
# - 一步倒/两步倒/三步倒 = 需要 1/2/3 次命中；本回合死亡、下回合不可见，
#   外观不变（名字就是 HP）。
# - 三种站形：全 1；前排全 1 / 后排 1 2 1 2 1；前排 1 2 1 2 1 /
#   后排 1 1 3 1 1。名字标签 OCR 得到 HP 网格后按下述求解器给出的
#   最少回合序列逐回合投掷。

BATTLE_DAOJU_ICON = "battle_daoju.png"            # 「道具」按钮（右侧竖栏）
BATTLE_DEFEND_ICON = "battle_fangyu_vertical.png"  # 「防御」按钮（右侧竖栏）
FIRECRACKER_ICON = item_catalog.icon("daxingbaozhu")
BAG_BOX_ICON = bag_lib.BAG_BOX_ICON               # 背包面板标志（同一模板）
# 右侧竖栏搜索条带（窗口 1378x831；竖栏随布局上下漂移，实测 y 331-541）
BATTLE_BAR_REGION = (1250, 300, 1378, 580)
BAG_ANCHOR_T = 0.8
# bag_box 模板中心 → 大型爆竹图标中心（2026-11-27 实机：锚点 (56,436)、
# 图标 (219,766)）。背包面板可拖动，面板内相对位置恒定；该格是开战
# 赠送的爆竹堆（数量 10→9…），同排另有玩家自备爆竹，不能按图标乱点。
FIRECRACKER_CELL_OFFSET = (163, 330)
FIRECRACKER_T = 0.65          # 图标淡入（灰暗态）实测 0.746，正常 0.92+
FIRECRACKER_ROI = 22          # 图标格校验半径（排除相邻同类物品）
BAG_OPEN_TIMEOUT = 3.0
FIRECRACKER_RENDER_WAIT = 0.55  # 开包后图标淡入渲染等待（实测 0.6s 内偏灰）
FIRECRACKER_CLICK_WAIT = 0.45   # 右键后等背包收起/进入选怪
# 两排 × 5 列怪物精灵中心（2026-11-27 实机模板匹配，脚底/包围盒中心）。
# 行顺序任意（求解器按实际 HP 网格计算，不依赖前后排命名）；
# 自上而下为每排的列序。十字用「同排 ±1 列 + 另一排同列」。
GRID_ROWS = (
    ((627, 250), (567, 360), (507, 470), (447, 580), (387, 690)),
    ((487, 210), (427, 320), (367, 430), (307, 540), (247, 650)),
)
# 精灵中心 → 点怪位置：实测点脚/包围盒中心无效，躯干（-25px）有效
GRID_CLICK_DY = -25
# 每个格子名字标签的 OCR 区域（相对精灵中心；实测 10/10 命中 1.00）
LABEL_OCR_OFFSETS = (-18, 20, 58, 52)
# 三种站形（用户 2026-11-27；前排/后排顺序仅作参考，实际按 OCR 网格）
FORMATION_PATTERNS = (
    ((1, 1, 1, 1, 1), (1, 1, 1, 1, 1)),
    ((1, 1, 1, 1, 1), (1, 2, 1, 2, 1)),
    ((1, 2, 1, 2, 1), (1, 1, 3, 1, 1)),
)
# 顶部红色大数字倒计时（操作窗口指示；区域实测数字 x 620-760 y 155-225）
TURN_COUNTDOWN_REGION = (520, 140, 800, 240)
TURN_RED_MIN = 400
TURN_WAIT_TIMEOUT = 90.0      # 等人物操作栏（含开场「请等待」+ 10 条获得日志）
BEAST_WAIT_TIMEOUT = 8.0      # 人物操作后等召唤兽栏
BEAST_CHAR_GRACE = 3.0        # 人物栏持续超过该时长 → 爆竹未生效
AUTO_CANCEL_RECHECK = 0.6     # 等人物栏期间取消自动战斗的检查节奏（秒；本帧
                              # 复用判定，自动面板晚出现/重开都能及时取消）
BATTLE_TOTAL_TIMEOUT = 600.0  # 整场兜底（正常 3~5 回合）
MAX_USES = 10                 # 开战赠送 10 个爆竹


def label_type(text):
    """名字标签 → HP（一步倒=1、两步倒=2、三步倒=3）；无法解析返回 None。

    OCR 偶发把「两」读成「二」，或首字漏检只留「步倒」：含「三」→3、
    含「两/二」→2、否则含「步/倒」→1（站形里其余格必然是一步倒）。
    """
    import re as _re
    t = _re.sub(r"[^一-鿿]", "", text or "")
    if not t or "倒" not in t:
        return None
    if "三" in t:
        return 3
    if "两" in t or "二" in t:
        return 2
    if "步" in t:
        return 1
    return None


def monster_grid_from_ocr(img):
    """OCR 10 个固定格子的名字标签 → {(row, col): 1/2/3/None}。

    标签在精灵脚下（相对精灵中心 +10,+34），区域 OCR 单格 ~25ms。
    未知格返回 None，由 _snap_to_formations 用三种站形补齐。
    """
    grid = {}
    for r, row in enumerate(GRID_ROWS):
        for c, (x, y) in enumerate(row):
            l, t, rgt, b = (x + LABEL_OCR_OFFSETS[0], y + LABEL_OCR_OFFSETS[1],
                            x + LABEL_OCR_OFFSETS[2], y + LABEL_OCR_OFFSETS[3])
            items, _ = game_map.ocr_region(img, (l, t, rgt, b))
            typ = None
            for txt, _rect, score in items:
                if score >= 0.5:
                    typ = label_type(txt)
                    if typ is not None:
                        break
            grid[(r, c)] = typ
    return grid


def _snap_to_formations(grid):
    """把 OCR 网格按三种已知站形补齐（未知格填 1/2/3）。

    行/列翻转都尝试（玩家描述的「前排/后排」方向未知，列序也按屏幕
    上→下；三种站形对列翻转对称，实际不影响求解）。返回 (hp, 命中数)；
    无任何站形匹配（极端 OCR 误读）时未知格按 1、命中数 0。
    """
    best = None
    for pat in FORMATION_PATTERNS:
        for swap_rows in (False, True):
            for flip_cols in (False, True):
                values = {}
                ok = True
                for r in range(2):
                    pr = 1 - r if swap_rows else r
                    for c in range(5):
                        pc = 4 - c if flip_cols else c
                        values[(r, c)] = pat[pr][pc]
                        known = grid.get((r, c))
                        if known is not None and known != values[(r, c)]:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                known_n = sum(1 for v in grid.values() if v is not None)
                # 同命中数优先原方向（不翻转），保证确定性
                key = (known_n, not swap_rows, not flip_cols)
                if best is None or key > best[0]:
                    best = (key, values)
    if best is None:
        return ({k: (1 if v is None else v) for k, v in grid.items()}, 0)
    return best[1], best[0][0]


def cross_cells(cell):
    """十字爆炸覆盖格：目标 + 同排左右 + 另一排同列（越界自动截断）。"""
    r, c = cell
    out = {(r, c)}
    if c > 0:
        out.add((r, c - 1))
    if c < 4:
        out.add((r, c + 1))
    out.add((1 - r, c))
    return out


def plan_firecracker_placements(hp, max_rounds=MAX_USES):
    """按当前 HP 网格求最少回合的爆竹投掷序列（目标格列表）；无解 None。

    每回合只能在存活格投掷（中心格本回合必须在场），十字内所有格同时
    -1 HP。10 格、深度 ≤10 的 DFS + memo，命中排序剪枝；实测三种站形
    秒级以内（F1 3 回合、F2 4 回合、F3 5 回合）。
    """
    from functools import lru_cache

    cells = tuple(sorted(hp))
    index = {cell: i for i, cell in enumerate(cells)}
    cross_idx = {i: tuple(index[x] for x in cross_cells(cell))
                 for cell, i in index.items()}

    @lru_cache(maxsize=None)
    def dfs(state, left):
        if all(v <= 0 for v in state):
            return ()
        if left <= 0:
            return None
        centers = [i for i in range(len(cells)) if state[i] > 0]
        centers.sort(key=lambda i: -sum(1 for j in cross_idx[i] if state[j] > 0))
        best = None
        for i in centers:
            ns = list(state)
            for j in cross_idx[i]:
                ns[j] -= 1
            tail = dfs(tuple(ns), left - 1)
            if tail is not None:
                seq = (cells[i],) + tail
                if best is None or len(seq) < len(best):
                    best = seq
        return best

    return dfs(tuple(hp[c] for c in cells), max_rounds)


def greedy_center(hp):
    """无完整解时的单步贪心：命中存活怪最多、累计 HP 最高、尽量居中。"""
    live = [c for c, v in hp.items() if v > 0]
    if not live:
        return None

    def score(cell):
        hit = [x for x in cross_cells(cell) if hp.get(x, 0) > 0]
        return (len(hit), sum(hp[x] for x in hit),
                -abs(cell[1] - 2), -cell[0], -cell[1])

    return max(live, key=score)


def grid_click_point(cell):
    """格子 → 点怪屏幕坐标（躯干；精灵中心上移 GRID_CLICK_DY）。"""
    x, y = GRID_ROWS[cell[0]][cell[1]]
    return x, y + GRID_CLICK_DY


def _bar_phase(bgr):
    """右侧竖栏阶段：'char'（8 按钮=人物）/ 'beast'（4 按钮=召唤兽）/ None。

    与通用战斗共用 battle.bar_phase（8 按钮优先判，用户 2026-11-27 规则）；
    保留本函数作为小游戏内部调用点（单测打桩用）。
    """
    return battle_lib.bar_phase(bgr)


def turn_red_pixels(bgr):
    """顶部红色倒计时像素数（>TURN_RED_MIN = 本回合可操作窗口）。"""
    l, t, r, b = TURN_COUNTDOWN_REGION
    sub = bgr[t:b, l:r].astype(np.int32)
    blue, green, red = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    return int(((red > 150) & (green < 90) & (blue < 90)).sum())


def _bag_anchor(bgr):
    """背包面板标志 bag_box 的中心坐标；未打开返回 None。"""
    return game_map.match_frame_np(bgr, BAG_BOX_ICON, threshold=BAG_ANCHOR_T,
                                   fallback=False)


def _find_firecracker_on_frame(bgr):
    """当前帧（当前储物格）定位大型爆竹，返回图标中心或 None。

    主路径：bag_box 锚点 + 固定格偏移（面板可拖动、内部布局恒定），
    小 ROI 模板校验（允许灰暗淡入态 0.65）；相邻格有玩家自备爆竹，
    兜底全背包搜索只在 ≥0.85 高置信命中时采用，避免点到别的爆竹堆。
    """
    anchor = _bag_anchor(bgr)
    if anchor is None:
        return None
    pt = (anchor[0] + FIRECRACKER_CELL_OFFSET[0],
          anchor[1] + FIRECRACKER_CELL_OFFSET[1])
    r = FIRECRACKER_ROI
    region = (max(0, pt[0] - r), max(0, pt[1] - r),
              pt[0] + r, pt[1] + r)
    score, _hit = game_map.best_match_np(bgr, FIRECRACKER_ICON, region=region)
    if score >= FIRECRACKER_T:
        return pt
    # 兜底：整块背包面板内高置信搜索（防物品排序/格位变化）
    region = (max(0, anchor[0] - 40), max(0, anchor[1] - 40),
              anchor[0] + 420, anchor[1] + 420)
    score, hit = game_map.best_match_np(bgr, FIRECRACKER_ICON, region=region)
    if score >= 0.85 and hit != (0, 0):
        return hit
    return None


def _slot_switch_base(bgr):
    """储物格切换基准按钮（box_one_close）中心；背包面板不在场返回 None。

    背包与给与面板共用同一储物格列（bag.SLOT_OFFSETS 校准，35px 步进），
    与 bag.find_box_one_close 同一模板/阈值。bag_box 先判面板在场（防止
    面板已关时对战场盲点），再在同一帧上定位按钮（省一次截图）。
    """
    if _bag_anchor(bgr) is None:
        return None
    return game_map.match_frame_np(bgr, bag_lib.BOX_ONE_CLOSE_ICON,
                                   threshold=0.8, fallback=False)


def _find_firecracker(win):
    """在已打开的背包中定位开战赠送的大型爆竹，返回图标中心或 None。

    当前储物格未找到时逐格切换（box_one_close + bag.SLOT_OFFSETS）重查：
    背包跨场景记住上次使用的储物格（2026-11-27 21:56 实机：同日 18:43
    同型战斗固定格命中，21:56 未找到——期间给与/任务储物格流程切过格，
    开战道具栏停在无爆竹的格上）。每次点击前用新帧复检背包面板仍在前台
    并刷新按钮位置，面板已关闭立即中止，绝不向战场盲点。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    bgr = game_map.bgr_frame(img)
    pt = _find_firecracker_on_frame(bgr)
    if pt is not None:
        return pt
    base = _slot_switch_base(bgr)
    if base is None:
        log("[天下无贼] 战斗型：未定位到储物格切换按钮（box_one_close），"
            "无法切格查找爆竹", "INFO")
        return None
    log("[天下无贼] 战斗型：当前储物格未找到大型爆竹，逐格切换查找", "INFO")
    for i, offset in enumerate(bag_lib.SLOT_OFFSETS):
        if i > 0:
            # 每次点击前复检面板仍在前台：切格/右键期间背包可能被关闭，
            # 此时点击会落到战场上
            img = win.capture(force_activate=False)
            if img is None:
                return None
            bgr = game_map.bgr_frame(img)
            base = _slot_switch_base(bgr)
            if base is None:
                log("[天下无贼] 战斗型：切格途中背包面板丢失，中止查找",
                    "WARN")
                return None
        human_click(win, base[0], base[1] + offset)
        # 轮询切换后的当前格直到图标渲染出来（切页后图标可能淡入偏灰，
        # 同开包 FIRECRACKER_RENDER_WAIT；超时即本格无爆竹，换下一格）
        deadline = time.time() + FIRECRACKER_RENDER_WAIT
        while True:
            time.sleep(0.12)  # 等待储物格界面切换（同 bag.find_item_with_slots）
            img = win.capture(force_activate=False)
            if img is None:
                return None
            pt = _find_firecracker_on_frame(game_map.bgr_frame(img))
            if pt is not None:
                if offset:
                    log(f"[天下无贼] 战斗型：切换储物格 {i + 1} 后找到大型爆竹",
                        "SYS")
                return pt
            if time.time() >= deadline:
                break
    return None


def _open_daoju_bag(win):
    """人物阶段点右侧「道具」开背包；返回 (是否成功, bag_box 锚点)。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False, None
    bgr = game_map.bgr_frame(img)
    pt = game_map.match_frame_np(bgr, BATTLE_DAOJU_ICON, threshold=0.9,
                                 region=BATTLE_BAR_REGION, fallback=False)
    if pt is None:
        return False, None
    human_click(win, pt[0], pt[1])
    deadline = time.time() + BAG_OPEN_TIMEOUT
    while time.time() < deadline:
        time.sleep(0.25)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        anchor = _bag_anchor(game_map.bgr_frame(img))
        if anchor is not None:
            return True, anchor
    return False, None


def _close_bag_safe(win):
    try:
        bag_lib.close_bag(win)
    except Exception as e:
        log(f"[天下无贼] 关闭背包异常: {e}", "WARN")


def use_firecracker(win, cell):
    """人物阶段：开道具栏 → 右键大型爆竹 → 左键点怪躯干。

    整段持背包锁（与摄妖香等其它背包流程互斥）。返回是否已出手（右键
    成功且背包已收起）；点怪是否命中由下一回合的存活情况体现。
    """
    with app_utils._BAG_LOCK:
        ok, _anchor = _open_daoju_bag(win)
        if not ok:
            log("[天下无贼] 战斗型：道具栏未打开，本回合不投掷", "WARN")
            return False
        time.sleep(FIRECRACKER_RENDER_WAIT)  # 图标淡入渲染
        pt = _find_firecracker(win)
        if pt is None:
            log("[天下无贼] 战斗型：背包中未找到大型爆竹", "WARN")
            _close_bag_safe(win)
            return False
        human_click(win, pt[0], pt[1], button="secondary")
        time.sleep(FIRECRACKER_CLICK_WAIT)
        img = win.capture(force_activate=False)
        if img is not None and _bag_anchor(game_map.bgr_frame(img)) is not None:
            log("[天下无贼] 战斗型：右键后背包仍打开（可能不在操作窗口），"
                "本回合不点怪", "WARN")
            _close_bag_safe(win)
            return False
        x, y = grid_click_point(cell)
        human_click(win, x, y)
        log(f"[天下无贼] 战斗型：右键大型爆竹 @({pt[0]},{pt[1]}) → "
            f"点怪 {cell} @({x},{y})", "SYS")
        return True


def defend_beast(win):
    """召唤兽阶段点「防御」。返回是否点击。"""
    img = win.capture(force_activate=False)
    if img is None:
        return False
    pt = game_map.match_frame_np(game_map.bgr_frame(img), BATTLE_DEFEND_ICON,
                                 threshold=0.9, region=BATTLE_BAR_REGION,
                                 fallback=False)
    if pt is None:
        log("[天下无贼] 战斗型：召唤兽栏未识别到「防御」", "WARN")
        return False
    human_click(win, pt[0], pt[1])
    log(f"[天下无贼] 战斗型：召唤兽防御 @({pt[0]},{pt[1]})", "SYS")
    return True


def _wait_phase(win, want, timeout, allow_end=True):
    """等待右侧竖栏进入指定阶段。

    返回 want / 'ended' / 'timeout'；want='beast' 时若人物栏持续
    BEAST_CHAR_GRACE 秒仍在（爆竹未生效），提前返回 'char'。
    allow_end=False 用于开场「请等待」：此时战斗模板可能尚未出现，
    不能把「不在战斗」当成结束。
    want='char' 期间每 AUTO_CANCEL_RECHECK 秒顺手取消自动战斗（用户
    2026-11-27 规则）：上一场常规战斗挂上的自动会跨场持续，抢在投爆竹
    前提交人物行动；取消后当前回合手动条随即出现，等待逻辑天然对齐。
    自动面板晚于人物栏出现时也会在后续轮询被取消，不依赖开场时机。
    """
    deadline = time.time() + timeout
    char_since = 0.0
    char_bar_since = 0.0
    last_auto_check = 0.0
    while time.time() < deadline:
        if allow_end and battle_lib.battle_ended(win):
            return "ended"
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.12)
            continue
        bgr = game_map.bgr_frame(img)
        if want == "char" and time.time() - last_auto_check >= AUTO_CANCEL_RECHECK:
            last_auto_check = time.time()
            battle_lib.cancel_auto_frame(win, bgr)  # 自动中才点击，自带日志
        phase = _bar_phase(bgr)
        if phase == want:
            if want == "char":
                # 用户 2026-11-27 规则：顶部红色倒计时出现 = 本回合可操作
                # 窗口。倒计时渲染可能比按钮栏晚一拍，按钮栏稳定 1.5s
                # 仍未出数字时按按钮栏放行（防区域漏检饿死整场）。
                if turn_red_pixels(bgr) >= TURN_RED_MIN:
                    return "char"
                now = time.time()
                if not char_bar_since:
                    char_bar_since = now
                elif now - char_bar_since >= 1.5:
                    return "char"
            else:
                return want
        else:
            char_bar_since = 0.0
        if want == "beast":
            now = time.time()
            if phase == "char":
                if not char_since:
                    char_since = now
                elif now - char_since >= BEAST_CHAR_GRACE:
                    return "char"
            else:
                char_since = 0.0
        time.sleep(0.12)
    return "timeout"


def refresh_monster_grid(win, hp):
    """用最新名字标签校正 HP 模型：复活/重刷格按标签重置，识别更正值。

    OCR 失败的格不动模型（可能只是渲染遮挡）；死亡后下一回合标签消失，
    模型里该格 HP 已≤0，求解器不会再选它。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return hp
    raw = monster_grid_from_ocr(img)
    for cell, typ in raw.items():
        if typ is None:
            continue
        if hp.get(cell, 0) <= 0:
            hp[cell] = typ
        elif typ > hp[cell]:
            hp[cell] = typ
    return hp


def run_firecracker_battle(win):
    """战斗型天下无贼主流程（战斗线程阻塞调用，由 battle 计划注入）。

    轮询人物操作栏（8 按钮）→ 投爆竹 → 等召唤兽栏（4 按钮）→ 防御，
    直到战斗结束/超时。返回是否正常结束流程（不代表战斗胜负）。
    进战斗先取消自动战斗（用户 2026-11-27 规则）：开场操作条已出现时
    立即取消；未出现时由 _wait_phase 在等人物栏期间继续取消。
    2026-11-27 21:56 实机：第 1 场常规战斗 21:54:59 已点自动，第 2 场
    天下无贼自动接管，爆竹右键始终不生效、空转到 10 个上限。
    """
    log("[天下无贼] 战斗型：开始（人物投爆竹 → 召唤兽防御）", "SYS")
    started = time.time()
    hp = None
    uses = 0
    seen_char = False
    battle_lib.cancel_auto(win)  # 尽早取消（自动中才点击）
    while time.time() - started < BATTLE_TOTAL_TIMEOUT:
        phase = _wait_phase(win, "char", TURN_WAIT_TIMEOUT,
                            allow_end=seen_char)
        if phase == "ended":
            log("[天下无贼] 战斗型：战斗已结束", "SYS")
            return True
        if phase != "char":
            log(f"[天下无贼] 战斗型：等待人物操作栏失败（{phase}）", "WARN")
            return seen_char
        seen_char = True
        if hp is None:
            img = win.capture(force_activate=False)
            raw = {}
            if img is not None:
                raw = monster_grid_from_ocr(img)
            hp, matched = _snap_to_formations(raw)
            seq = plan_firecracker_placements(hp)
            log(f"[天下无贼] 战斗型：名字标签 {raw}", "SYS")
            log(f"[天下无贼] 战斗型：HP 网格 {hp}（标签命中 {matched}），"
                f"计划 {len(seq) if seq else '?'} 回合：{seq}", "SYS")
        else:
            hp = refresh_monster_grid(win, hp)
        seq = plan_firecracker_placements(hp)
        center = seq[0] if seq else greedy_center(hp)
        if center is None:
            log("[天下无贼] 战斗型：模型内已无存活怪，等待战斗结束", "SYS")
            time.sleep(1.0)
            continue
        uses += 1
        if uses > MAX_USES:
            log(f"[天下无贼] 战斗型：已投掷 {MAX_USES} 个仍未结束，停止", "WARN")
            return False
        ok = use_firecracker(win, center)
        if ok:
            for cell in cross_cells(center):
                if cell in hp:
                    hp[cell] -= 1
            log(f"[天下无贼] 战斗型：第 {uses} 个爆竹目标 {center}，"
                f"模型剩余 HP {sum(max(0, v) for v in hp.values())}", "SYS")
        phase = _wait_phase(win, "beast", BEAST_WAIT_TIMEOUT)
        if phase == "beast":
            defend_beast(win)
        elif phase == "char":
            log("[天下无贼] 战斗型：人物操作栏仍在（爆竹未生效），"
                "本回合跳过召唤兽防御", "WARN")
        elif phase == "ended":
            log("[天下无贼] 战斗型：战斗已结束", "SYS")
            break
        time.sleep(0.3)
    log("[天下无贼] 战斗型：流程结束", "SYS")
    return True
