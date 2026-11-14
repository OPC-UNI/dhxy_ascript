"""天宫奇遇「魔取仙丹」小游戏（偷丹葫芦解封顺序）。

流程（2026-11-14 实机探针标定）：
- 使用偷丹葫芦后出现「魔取仙丹」窗口（标题模板
  mingwang_moqushendan.png，窗口位置固定）。初始只有「观看」按钮；
  点击后葫芦周围 4 个封印小葫芦按顺序亮起金色光翼（演示），
  约 3~5s 后全部熄灭，按钮变为「C（重播）+ 确认」。
- 点击「确认」进入输入阶段（提示「请少侠按正确顺序触发封印，这样
  方能打开偷丹葫芦」），按演示顺序依次点击 4 个葫芦（点击即亮起，
  累计保持），再点「确认」提交；成功则窗口关闭并弹出「恭喜少侠按
  正确顺序触发封印，成功打开偷丹葫芦！」提示，随后进入战斗。
- 按钮区 OCR 三态：观看 / 观看中 / 确认；倒计时在按钮下方（99s 起）。
  演示中途按钮为「观看中」；演示结束后出现重播按钮（C，按钮左侧
  ~72px）与「确认」。
- 亮起判定：金色光翼像素掩码（R≥205、G≥180、B≤150）。实机逐帧
  标定（demo 帧）：亮起 ROI 金色像素 190~262，未亮 0~72（窗口纸面
  与葫芦本体暗金），阈值 120 双侧留 ≥40 余量。
- 演示顺序两次实机一致（上→右→左→左下），但仍按帧记录、不写死
  （顺序可能随任务实例变化）。

本模块只依赖 core/device（不 import tasks/*），import 期零副作用；
触发 armed/限频由 mingwang 任务侧控制。
"""
import time

import numpy as np

from ..core.app_utils import human_click, log, norm_text
from ..device import map as game_map
from ..device.map import bgr_frame as _bgr_frame

# 窗口标题模板（res/img/mingwang_moqushendan.png，132x47，2026-11-14
# 实机帧裁取；窗口纸面静态区，帧间逐像素一致）。窗口位置固定，
# 标题匹配限定标题条带、不做全帧兜底（战斗画面实测 0.219 << 0.8）。
MOQUSHENDAN_TITLE_ICON = "mingwang_moqushendan.png"
MOQUSHENDAN_TITLE_THRESHOLD = 0.8
MOQUSHENDAN_TITLE_REGION = (650, 280, 990, 380)  # 标题条带（窗口内固定）
# 窗口检测 armed 参数（mingwang 任务侧使用：用道具成功后开启、限频检测）
MOQUSHENDAN_GAME_ARM_TIMEOUT = 20.0
MOQUSHENDAN_CHECK_INTERVAL = 1.0
# 窗口按钮：观看/确认同位置；重播按钮 C 在其左侧（演示结束后出现）
MOQUSHENDAN_BTN_POS = (818, 535)
MOQUSHENDAN_REPLAY_POS = (746, 537)
MOQUSHENDAN_BTN_REGION = (740, 505, 880, 570)   # 按钮+倒计时 OCR 区
# 四个封印小葫芦：名称、金色光翼 ROI、点击点（实机标定；点击点=葫芦
# 本体中心，已验证点击后对应葫芦亮起）
MOQUSHENDAN_GOURDS = (
    {"name": "上", "roi": (548, 328, 588, 388), "click": (574, 364)},
    {"name": "右", "roi": (612, 372, 654, 428), "click": (635, 394)},
    {"name": "左", "roi": (480, 372, 522, 428), "click": (505, 394)},
    {"name": "左下", "roi": (480, 442, 522, 498), "click": (504, 469)},
)
# 金色光翼掩码（见模块 docstring 标定值）；亮起判定阈值
MOQUSHENDAN_GOLD_MIN = 120
_GOLD_R, _GOLD_G, _GOLD_B = 205, 180, 150
MOQUSHENDAN_DEMO_TIMEOUT = 15.0     # 演示录制超时（秒；实测演示 3~5s）
MOQUSHENDAN_POLL = 0.08            # 演示录制截帧间隔（秒）
MOQUSHENDAN_OFF_CONFIRM = 2        # 「全部熄灭」连续轮数（防渲染瞬态误判演示结束）
MOQUSHENDAN_BTN_TIMEOUT = 6.0      # 等待按钮文案可读的时限（秒）
MOQUSHENDAN_DEMO_WAIT = 12.0       # 进入时演示已在进行的等待上限（秒）
MOQUSHENDAN_INPUT_GAP = 0.25       # 按序点击葫芦的间隔（秒；响应窗口 0.3~0.5s）
MOQUSHENDAN_INPUT_SETTLE = 0.35    # 点「确认」进输入阶段后的渲染等待（秒）
MOQUSHENDAN_SUBMIT_WAIT = 0.5      # 提交「确认」后等待结算（秒）
MOQUSHENDAN_SUCCESS_TIMEOUT = 4.0  # 提交后等待窗口关闭的时限（秒）
MOQUSHENDAN_CLOSE_POLLS = 2        # 标题连续未命中轮数 → 窗口已关闭
MOQUSHENDAN_PLAY_ATTEMPTS = 3      # 录制/输入失败重试次数（重新观看演示）
MOQUSHENDAN_OCR_SCORE = 0.55       # 区域 OCR 置信度下限（项目统一）
MOQUSHENDAN_SETTLE = 0.4           # 通用短等待（秒）
# 按钮三态（「观看中」须先于「观看」判定——前者是后者子串）
_ACTION_LABELS = ("观看中", "确认", "观看")


def moqushendan_present(img_bgr):
    """「魔取仙丹」窗口是否在场（标题条带模板匹配，不做全帧兜底）。"""
    score, _pt = game_map.best_match_np(
        img_bgr, MOQUSHENDAN_TITLE_ICON,
        region=MOQUSHENDAN_TITLE_REGION)
    return score >= MOQUSHENDAN_TITLE_THRESHOLD


def _gold_count(rgb, roi):
    """ROI 内金色光翼像素数（RGB uint8 帧）。"""
    l, t, r, b = roi
    reg = rgb[t:b, l:r]
    if reg.size == 0:
        return 0
    return int(((reg[:, :, 0] >= _GOLD_R) & (reg[:, :, 1] >= _GOLD_G)
                & (reg[:, :, 2] <= _GOLD_B)).sum())


def gourd_lit_map(rgb):
    """四个葫芦当前亮起状态 {名称: bool}（RGB uint8 帧）。"""
    return {g["name"]: _gold_count(rgb, g["roi"]) >= MOQUSHENDAN_GOLD_MIN
            for g in MOQUSHENDAN_GOURDS}


def _gourd(name):
    """按名称取葫芦配置（录制顺序回放用）。"""
    for g in MOQUSHENDAN_GOURDS:
        if g["name"] == name:
            return g
    raise KeyError(name)


def _button_label(img):
    """OCR 按钮区，返回 (按钮文案或空串, 全部文案)。

    文案只认「观看中/确认/观看」（按该顺序匹配，防「观看中」被
    「观看」子串抢先）；倒计时行一并返回供日志校准。
    """
    items, _ = game_map.ocr_region(img, MOQUSHENDAN_BTN_REGION)
    texts = [norm_text(t) for t, _r, s in items if s >= MOQUSHENDAN_OCR_SCORE]
    joined = "".join(texts)
    for label in _ACTION_LABELS:
        if label in joined:
            return label, joined
    return "", joined


def _wait_button_label(win, timeout=MOQUSHENDAN_BTN_TIMEOUT):
    """等待按钮文案可读，返回 (文案, 截图帧, 全部文案)。"""
    deadline = time.time() + timeout
    texts = ""
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.2)
            continue
        label, texts = _button_label(img)
        if label:
            return label, img, texts
        time.sleep(0.25)
    return "", None, texts


def _wait_demo_over(win):
    """演示已在进行的等待：返回可操作的按钮文案（观看/确认）或空串。"""
    deadline = time.time() + MOQUSHENDAN_DEMO_WAIT
    while time.time() < deadline:
        label, _img, _texts = _wait_button_label(win, timeout=1.0)
        if label in ("确认", "观看"):
            return label
        time.sleep(0.4)
    return ""


def _record_demo(win, prefix=""):
    """观看并记录演示顺序，返回 (顺序列表或 None, 描述)。

    亮起状态累计保持到演示结束（实机确认：每个葫芦首次亮起后不再熄灭，
    全部亮起后同时熄灭），因此按「首次亮起」的先后记录顺序；收尾条件
    = 四个都亮过且当前全部熄灭。轮询 0.08s << 实机亮起间隔 0.6~1.0s。
    """
    seq, seen = [], set()
    states = {}
    off_polls = 0
    deadline = time.time() + MOQUSHENDAN_DEMO_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(MOQUSHENDAN_POLL)
            continue
        rgb = np.asarray(img)[:, :, :3]
        states = gourd_lit_map(rgb)
        for g in MOQUSHENDAN_GOURDS:
            name = g["name"]
            if states[name] and name not in seen:
                seen.add(name)
                seq.append(name)
                if prefix:
                    log(f"[{prefix}] 魔取仙丹演示：{name}亮起"
                        f"（{len(seq)}/4）", "INFO")
        if len(seq) == len(MOQUSHENDAN_GOURDS) and not any(states.values()):
            off_polls += 1
            if off_polls >= MOQUSHENDAN_OFF_CONFIRM:
                return seq, f"演示顺序 {'→'.join(seq)}"
        else:
            off_polls = 0
        time.sleep(MOQUSHENDAN_POLL)
    return None, (f"演示录制超时（识别 {len(seq)}/4 个，"
                  f"最后状态 {states or '(无)'}）")


def _click_sequence(win, seq, prefix=""):
    """按演示顺序点击四个葫芦（点击间隔 INPUT_GAP）。"""
    for name in seq:
        pt = _gourd(name)["click"]
        human_click(win, pt[0], pt[1])
        if prefix:
            log(f"[{prefix}] 魔取仙丹：点击封印「{name}」@({pt[0]},{pt[1]})",
                "SYS")
        time.sleep(MOQUSHENDAN_INPUT_GAP)


def _window_closed(win):
    """等待窗口关闭（标题模板连续 CLOSE_POLLS 轮未命中）。"""
    deadline = time.time() + MOQUSHENDAN_SUCCESS_TIMEOUT
    misses = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            if moqushendan_present(_bgr_frame(img)):
                misses = 0
            else:
                misses += 1
                if misses >= MOQUSHENDAN_CLOSE_POLLS:
                    return True, img
        time.sleep(0.4)
    return False, None


def play_moqushendan(win, prefix="名望"):
    """执行一局「魔取仙丹」（阻塞至窗口关闭或尝试用尽），返回 (ok, 描述)。

    每轮：读按钮三态 → 观看/重播演示并记录顺序 → 确认进输入阶段 →
    按序点击 → 再确认提交 → 检查窗口关闭（成功标志）。失败（点击未被
    接收/顺序错误导致重置）重新观看，最多 PLAY_ATTEMPTS 次。
    """
    last_info = "未开始"
    for attempt in range(1, MOQUSHENDAN_PLAY_ATTEMPTS + 1):
        label, _img, texts = _wait_button_label(win)
        if label == "观看中":
            # 演示已在跑（窗口刚弹出/上一轮残留）：等结束后重播录制
            label = _wait_demo_over(win)
        if label == "确认":
            human_click(win, MOQUSHENDAN_REPLAY_POS[0],
                        MOQUSHENDAN_REPLAY_POS[1])
            log(f"[{prefix}] 魔取仙丹：演示已结束未录制，点击重播重新观察",
                "INFO")
            time.sleep(MOQUSHENDAN_SETTLE)
        elif label == "观看":
            human_click(win, MOQUSHENDAN_BTN_POS[0],
                        MOQUSHENDAN_BTN_POS[1])
            log(f"[{prefix}] 魔取仙丹：点击「观看」，记录解封顺序", "SYS")
        else:
            last_info = (f"第{attempt}次：按钮文案不可读"
                         f"（OCR {texts or '(空)'}）")
            log(f"[{prefix}] 魔取仙丹：{last_info}", "WARN")
            time.sleep(MOQUSHENDAN_SETTLE)
            continue
        seq, sinfo = _record_demo(win, prefix=prefix)
        if seq is None:
            last_info = f"第{attempt}次：{sinfo}"
            log(f"[{prefix}] 魔取仙丹：{last_info}", "WARN")
            continue
        # 演示结束到按钮切换「确认」有短暂过渡：先等渲染稳定再点确认
        time.sleep(MOQUSHENDAN_SETTLE)
        # 确认 → 输入阶段 → 按序点击 → 再确认提交
        human_click(win, MOQUSHENDAN_BTN_POS[0], MOQUSHENDAN_BTN_POS[1])
        time.sleep(MOQUSHENDAN_INPUT_SETTLE)
        _click_sequence(win, seq, prefix=prefix)
        time.sleep(MOQUSHENDAN_SETTLE)
        img = win.capture(force_activate=False)
        lit = (gourd_lit_map(np.asarray(img)[:, :, :3]) if img is not None
               else {})
        if not lit or not all(lit.values()):
            last_info = (f"第{attempt}次：按序点击后封印未全部亮起"
                         f"（{lit or '截图失败'}）")
            log(f"[{prefix}] 魔取仙丹：{last_info}", "WARN")
            continue
        human_click(win, MOQUSHENDAN_BTN_POS[0], MOQUSHENDAN_BTN_POS[1])
        time.sleep(MOQUSHENDAN_SUBMIT_WAIT)
        closed, _cimg = _window_closed(win)
        if closed:
            return True, (f"魔取仙丹通过（第{attempt}次，演示顺序 "
                          f"{'→'.join(seq)}）")
        last_info = f"第{attempt}次：提交后窗口未关闭（顺序可能错误）"
        log(f"[{prefix}] 魔取仙丹：{last_info}，重新观看演示", "WARN")
    return False, f"魔取仙丹小游戏未通过（{last_info}）"
