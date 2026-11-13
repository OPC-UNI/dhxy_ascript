"""锄遗聚宝（红色杂气）小游戏的纯检测逻辑与常量。

触发（点击「注入玄蕴试试看」）与阻塞点击循环 `_play_chuyijuebao`
仍在 xuanshu 任务侧；本模块只做红色团块检测、超面积拆分与点击
无效观察，import 期零副作用。
"""
import os
import time

import cv2
import numpy as np
from ascript.windows.window.Window import WindowCaptureFormat

from ..device import clear_screen
from ..device import map as game_map
from ..core.app_utils import human_click, log, precise_click, rapid_click
from ..device.dialog_common import close_scene_panels

# ---- 锄遗聚宝检测参数（红色杂气）----
CHUYIJUEBAO_ICON = "chuyijuebao.png"              # 主图（消失即结束；实测匹配 0.91）"
CHUYIJUEBAO_START_ICON = "chuyijuebao_start.png"  # 开始按钮（实测匹配 0.92）
CHUYIJUEBAO_CHECK_INTERVAL = 0.2                 # 杂气检测间隔（秒）
CHUYIJUEBAO_TIMEOUT = 60                        # 小游戏总超时（秒）
CHUYIJUEBAO_DEBUG = False                       # 调试开关：True 时不点击鼠标，
                                                # 只标注检测结果截图供人工核对
CHUYIJUEBAO_DEBUG_DIR = "qi_debug"              # 标注截图输出目录（相对程序工作目录）
# 红色杂气检测参数（游戏说明：点击红色杂气将其清除）
QI_RED_MIN = 120        # R 通道最低亮度
QI_RED_DOMINANCE = 30   # R 须高出 G/B 的差值
QI_REGION = (300, 117, 1150, 700)  # 世界区域（排除任务面板、顶部红色说明文字、底部 HUD）
QI_AREA_MIN = 250       # 团块最小面积（过滤红色文字等小噪点）
QI_AREA_MAX = 2500      # 单个杂气最大面积；超出=多个杂气相连成片，走峰值拆分
                        # （打包版用户反馈「杂气多时点不完」：相邻杂气连通成
                        # 超面积团块被整体过滤，成片杂气对点击器不可见）
QI_SPLIT_MIN_R = 8      # 拆分峰值最小内切半径（px）：距离变换峰低于此值视为
                        # 细长噪点不拆分（最小杂气面积 250 → 半径约 9）
QI_SPLIT_MAX_N = 12     # 单个超面积团块最多拆分出的点击目标数（防大块红物刷屏）
QI_SPLIT_SUPPRESS_EXTRA = 2  # 峰值抑制半径在峰半径基础上追加的余量（px）
QI_SPLIT_MAX_AREA = QI_AREA_MAX * 4  # 单峰超大团块仍点击的面积上限（两个大杂气
                        # 高度重叠时双峰互相抑制塌成单峰，但合并面积仍超上限，
                        # 不点则永久漏点；更大面积视为大块静态红物不点击）
# 点击无效拉黑（超面积团块拆分后误点静态红物如角色红衣的防护）：
# 点击位置小窗口内红像素数在观察延迟后基本不变 → 该点击确认无效；
# 同一位置（容 8px）第二次点击后拉黑。半径仅 8px——只封死已确认无效的
# 同一位置，不波及紧凑团块的相邻杂气峰（相邻峰间距 ≥ 峰半径+2）
QI_NOEFFECT_WIN = 15       # 点击位置红像素计数窗口半径（px）
QI_NOEFFECT_DELAY = 1.5    # 点击后观察窗口像素的延迟（秒，等死亡动画结束）
QI_NOEFFECT_KEEP = 0.85    # 窗口红像素数仍在 [×KEEP, ×(2-KEEP)] 区间 → 点击无效
QI_BLACKLIST_RADIUS = 8    # 拉黑排除半径（px）
QI_CLICK_DEBOUNCE = 2.0  # 同一位置重复点击的最短间隔（秒）
# 批量极速连点（杂气走到石头即消失、同屏多杂气须尽快清光——用户实测
# 逐击重截窗 9 目标 ~1.5s 太慢）：同帧目标 ≥ QI_BATCH_CLICK_MIN 时一帧
# 捕获全部目标连续点掉（每击 ~25ms，9 目标 ~0.25s/轮），漂移误差以一轮
# （~0.3s）为界、下一轮重截窗自然校正；目标少时保持单目标逐击重截窗的
# 精确路径（漂移时效优先，历史实测校准）
QI_BATCH_CLICK_MIN = 3     # 触发批量连点的同帧目标数下限
QI_RAPID_PRESS = 0.02      # 批量连点每击按压时长（秒，20ms 保守折中；
                           # 出现点击未注册时调回 0.05）


def _split_cluster_peaks(labels, ci, x, y, w, h):
    """超面积团块的距离变换峰值拆分（坐标相对团块裁剪区）。

    杂气相连成片时按每个杂气的中心取峰：对团块掩码做距离变换，
    贪心取最强峰并用「峰半径+余量」抑制其邻域，直到峰值低于
    QI_SPLIT_MIN_R 或达到 QI_SPLIT_MAX_N。返回 [(px, py, r), ...]。
    """
    sub = (labels[y:y + h, x:x + w] == ci).astype(np.uint8) * 255
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    peaks = []
    work = dist.copy()
    while len(peaks) < QI_SPLIT_MAX_N:
        _, mx, _, loc = cv2.minMaxLoc(work)
        if mx < QI_SPLIT_MIN_R:
            break
        px, py = int(loc[0]), int(loc[1])
        peaks.append((px, py, float(mx)))
        cv2.circle(work, (px, py), int(mx) + QI_SPLIT_SUPPRESS_EXTRA, 0, -1)
    return peaks

def _find_red_qi_clusters(region_np):
    """检测杂气区域（QI_REGION 裁剪，RGB）内的红色杂气团块。

    返回 (clusters, oversized)：clusters 为
    [(cx, cy, x, y, w, h, area, split), ...]，坐标为窗口相对全图坐标；
    split 标记该目标是否由超面积团块拆分而来（area 为峰圆面积估算）。
    oversized 为本帧超面积（相连成片）团块个数（诊断计数）。
    基于游戏说明「点击红色杂气将其清除」：杂气为红色像素团块，
    用连通块分析过滤面积区间，对杂气动画与尺寸变化不敏感。
    cx/cy 为团块中心，x/y/w/h 为团块包围盒，area 为团块掩码面积（像素数）。
    """
    red = region_np[:, :, 0].astype(int)
    green = region_np[:, :, 1].astype(int)
    blue = region_np[:, :, 2].astype(int)
    mask = ((red > QI_RED_MIN) & (red > green + QI_RED_DOMINANCE)
            & (red > blue + QI_RED_DOMINANCE)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    clusters = []
    oversized = 0
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < QI_AREA_MIN:
            continue
        if area <= QI_AREA_MAX:
            clusters.append((QI_REGION[0] + x + w // 2, QI_REGION[1] + y + h // 2,
                             QI_REGION[0] + x, QI_REGION[1] + y, w, h, int(area), False))
            continue
        # 超面积团块=多个杂气相连成片：拆分成多个点击目标；两个大杂气高度
        # 重叠时峰互相抑制只剩单峰，团块面积 ≤ QI_SPLIT_MAX_AREA 时仍点击
        # 该峰（点击消除一个后余下团块自然落回正常面积路径），更大面积视为
        # 大块静态红物不点击
        oversized += 1
        peaks = _split_cluster_peaks(labels, i, x, y, w, h)
        if len(peaks) < 2 and area > QI_SPLIT_MAX_AREA:
            continue
        for px, py, r in peaks:
            cx = QI_REGION[0] + x + px
            cy = QI_REGION[1] + y + py
            half = max(6, int(r) + 2)
            clusters.append((cx, cy, cx - half, cy - half, 2 * half, 2 * half,
                             max(1, int(3.14159 * r * r)), True))
    return clusters, oversized

def _red_count_near(region, wx, wy):
    """窗口相对全图坐标 (wx, wy) 周围 QI_NOEFFECT_WIN 半径内的红像素数。

    region 为 QI_REGION 裁剪帧（RGB）：点击无效判定用——静态红物点击后
    窗口像素数不变，杂气被点击后消失/消减导致像素数明显变化。
    """
    rx, ry = wx - QI_REGION[0], wy - QI_REGION[1]
    sub = region[max(0, ry - QI_NOEFFECT_WIN):ry + QI_NOEFFECT_WIN + 1,
                 max(0, rx - QI_NOEFFECT_WIN):rx + QI_NOEFFECT_WIN + 1]
    if sub.size == 0:
        return 0
    red = sub[:, :, 0].astype(int)
    green = sub[:, :, 1].astype(int)
    blue = sub[:, :, 2].astype(int)
    return int(((red > QI_RED_MIN) & (red > green + QI_RED_DOMINANCE)
                & (red > blue + QI_RED_DOMINANCE)).sum())

def _observe_click_effects(pending, region, now):
    """点击效果观察（就地更新 pending）：点击位置窗口红像素明显变化 →
    点击有效清除记录；基本不变且超过观察延迟 → 标记确认无效（调用方在
    同一位置第二次点击后拉黑）。pending: 位置 -> (点击时红像素数,
    点击时间, 已点击次数, 已确认无效)。
    """
    for pos in list(pending):
        cnt0, t0, clicks, confirmed = pending[pos]
        if now - t0 > 6.0:
            del pending[pos]  # 过久未决（点击后场景变化），放弃观察
            continue
        if now - t0 < QI_NOEFFECT_DELAY:
            continue
        cnt1 = _red_count_near(region, pos[0], pos[1])
        if cnt0 <= 0 or cnt1 < cnt0 * QI_NOEFFECT_KEEP \
                or cnt1 > cnt0 * (2.0 - QI_NOEFFECT_KEEP):
            del pending[pos]
        elif not confirmed:
            pending[pos] = (cnt0, t0, clicks, True)

def _mark_qi_debug(img_np, clusters, path):
    """把检测到的杂气团块画框标注后保存（调试用，img_np 为全窗 RGB 图）。

    拆分目标用黄色框区分（绿色框=正常团块）。截图为 RGB（channel 0=R），
    cv2.imwrite 按 BGR 编码，写盘前转 BGR 保证红色正常显示。
    """
    out = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR).copy()
    for cx, cy, x, y, w, h, _area, split in clusters:
        color = (0, 255, 255) if split else (0, 255, 0)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        cv2.circle(out, (cx, cy), 3, (0, 0, 255), -1)
    cv2.imwrite(path, out)

def _capture_qi_region(win):
    """快速抓取杂气检测区域，返回 RGB numpy 数组（区域裁剪）或 None。

    win.capture 整窗 NUMPY 后裁剪：实测约 34ms，PrintWindow 抗窗口遮挡
    （ImageGrab 区域 BitBlt 约 49ms 且遮挡时拍到遮挡物，已弃用）。
    """
    l, t, r, b = QI_REGION
    full = win.capture(force_activate=False, format=WindowCaptureFormat.NUMPY)
    if full is None:
        return None
    return full[t:b, l:r]

def play(win, timeout=CHUYIJUEBAO_TIMEOUT,
         prefix="玄枢轶闻"):
    """锄遗聚宝小游戏：点击开始（若开始按钮存在），循环点击消除红色杂气，
    主图消失即结束。

    开始后再次进入时无开始按钮（任务已在进行），直接进入消除循环。
    若背包或地图面板打开，先关闭（可能遮挡开始按钮与杂气）；
    点击开始按钮前先收起活动侧栏（展开会遮挡开始按钮，2026-11-04）；
    循环中每 5 秒兜底检查一次。
    点击策略（杂气走到石头即消失，须尽快清光）：
    - 同帧目标 ≥ QI_BATCH_CLICK_MIN 时批量极速连点：一帧捕获全部目标
      连续点掉（rapid_click 每击 ~25ms，不再逐击重截窗），漂移误差以
      一轮（~0.3s）为界、下一轮重截窗校正——9 目标 ~0.25s/轮，逐击
      重截窗 ~1.5s 实测太慢（用户反馈）；
    - 目标少时单目标路径：点击后立即重新截帧定位（漂移时效优先，
      历史实测：批量点击旧位置会点到已过期目标）；
    - 同一位置 2 秒内不重复点击（容 ±20px 漂移，避免误点静态红 UI 刷屏）。
    杂气多时相邻杂气连通成超面积团块：由 _find_red_qi_clusters 拆分成
    多个点击目标逐帧点完（打包版用户反馈「杂气多时点不完」）。
    拆分目标误点静态红物（角色红衣等）的防护：点击无效拉黑——点击位置
    小窗口红像素数不消减即确认无效，同一位置（容 8px）第二次点击后
    拉黑（_observe_click_effects 观察 + 点击时判定），有效点击不拉黑；
    半径仅 8px，不波及紧凑团块的相邻杂气峰。
    结束判定（主图模板匹配需独立截图）每 1 秒摊销一次。
    诊断：结束时返回统计（拆分点击数/超面积团块帧数/单帧最多目标）；
    超时时把最后一帧标注图存到 qi_debug/ 供打包版远程排查。
    CHUYIJUEBAO_DEBUG=True 时：不点击鼠标，仅标注检测结果并保存截图
    （每秒一张到 CHUYIJUEBAO_DEBUG_DIR），供人工核对识别准确性。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    # 场景前置：关闭可能遮挡的背包/小地图/世界地图
    close_scene_panels(win, "玄枢轶闻")

    if not CHUYIJUEBAO_DEBUG:
        # 活动侧栏展开会遮挡小游戏开始按钮：点击前先收起
        # （2026-11-04 校准；未找到图标/已收起为无需操作，失败不阻塞）
        ok_act, info_act = clear_screen.ensure_activity_closed(win)
        log(f"[{prefix}] {info_act}", "SYS" if ok_act else "WARN")
        pt = game_map.find_template(win, CHUYIJUEBAO_START_ICON)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[{prefix}] 已点击锄遗聚宝开始", "SYS")
            time.sleep(0.5)  # 等待开始动画

    clicked = 0
    split_clicked = 0       # 诊断：拆分目标点击数
    oversized_frames = 0    # 诊断：检测到超面积（相连成片）团块的帧数
    max_clusters_seen = 0   # 诊断：单帧最多点击目标数
    last_click_at = {}
    pending = {}            # 点击位置 -> (点击时窗口红像素数, 点击时间, 已点击次数, 已确认无效)
    blacklist = []          # 同一位置两次点击无效后拉黑（防静态红物刷屏）
    last_icon_check = 0.0
    last_panel_check = 0.0
    last_debug_save = 0.0
    while time.time() < deadline:
        now = time.time()
        # 结束判定：主图消失即结束（模板匹配需独立截图，1 秒摊销一次）
        if now - last_icon_check >= 1.0:
            last_icon_check = now
            if game_map.find_template(win, CHUYIJUEBAO_ICON) is None:
                return True, (f"锄遗聚宝结束（共消除 {clicked} 个；拆分点击 {split_clicked}，"
                              f"超面积团块 {oversized_frames} 帧，单帧最多 {max_clusters_seen} 个）")
        # 兜底：关闭可能弹出的背包/地图面板（5 秒一次；quiet——未打开
        # 不点击的路径不打日志，避免小游戏期间每 5 秒刷 2 条 INFO）
        if now - last_panel_check >= 5.0:
            last_panel_check = now
            close_scene_panels(win, "玄枢轶闻", quiet=True)
        if CHUYIJUEBAO_DEBUG:
            # 调试模式：不点击鼠标，标注检测框并保存截图（每秒一张）
            if now - last_debug_save >= 1.0:
                last_debug_save = now
                full = win.capture(force_activate=False,
                                   format=WindowCaptureFormat.NUMPY)
                if full is not None:
                    l, t, r, b = QI_REGION
                    clusters, oversized = _find_red_qi_clusters(full[t:b, l:r])
                    os.makedirs(CHUYIJUEBAO_DEBUG_DIR, exist_ok=True)
                    path = os.path.join(CHUYIJUEBAO_DEBUG_DIR,
                                        f"qi_{time.strftime('%H%M%S')}.png")
                    _mark_qi_debug(full, clusters, path)
                    log(f"[{prefix}] 调试: 检测到 {len(clusters)} 个杂气目标"
                        f"（超面积团块 {oversized} 个），已标注保存 {path}", "SYS")
                    log(f"[{prefix}] 调试: 团块 "
                        f"{[(c[0], c[1], c[6], '拆' if c[7] else '') for c in clusters]}"
                        "（中心, 面积, 拆分）", "INFO")
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        # 快速区域截图：屏幕 BitBlt 只抓杂气区域（比 PrintWindow 全窗
        # 快数倍），杂气移动快，点击时效优先
        region = _capture_qi_region(win)
        if region is None:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        clusters, oversized = _find_red_qi_clusters(region)
        max_clusters_seen = max(max_clusters_seen, len(clusters))
        if not clusters:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue
        oversized_frames += 1 if oversized else 0
        # 点击效果观察：窗口红像素明显变化 → 点击有效清除记录；不变 →
        # 标记确认无效（同一位置第二次点击后拉黑，防静态红物刷屏）
        _observe_click_effects(pending, region, now)
        # 目标筛选：跳过近期点过的位置（容 ±20px 漂移）与拉黑位置
        clickable = []
        for c in clusters:
            if any(now - t < QI_CLICK_DEBOUNCE
                   and abs(c[0] - px) <= 20 and abs(c[1] - py) <= 20
                   for (px, py), t in last_click_at.items()):
                continue
            if any(abs(c[0] - bx) <= QI_BLACKLIST_RADIUS
                   and abs(c[1] - by) <= QI_BLACKLIST_RADIUS
                   for bx, by in blacklist):
                continue
            clickable.append(c)
        if not clickable:
            time.sleep(CHUYIJUEBAO_CHECK_INTERVAL)
            continue

        def _register_click(tgt):
            """点击记账（批量/单点共用）：防抖位置、点击效果观察与拉黑判定。

            本次点击紧邻已确认无效的位置（容 8px）→ 点完即拉黑该位置
            （静态红物最多被点 2 次；有效点击会清除观察记录，不会走到拉黑）。
            """
            last_click_at[(tgt[0], tgt[1])] = now
            if len(last_click_at) > 60:
                keep = {k: t for k, t in last_click_at.items()
                        if now - t < QI_CLICK_DEBOUNCE}
                last_click_at.clear()
                last_click_at.update(keep)
            near_pending = None
            for pos, info in pending.items():
                if abs(tgt[0] - pos[0]) <= QI_BLACKLIST_RADIUS \
                        and abs(tgt[1] - pos[1]) <= QI_BLACKLIST_RADIUS:
                    near_pending = (pos, info)
                    break
            if near_pending is not None:
                pos0, (cnt0, t0, clicks, confirmed) = near_pending
                if confirmed:
                    blacklist.append(pos0)
                    del pending[pos0]
                    log(f"[{prefix}] 点击无效拉黑位置 ({pos0[0]},{pos0[1]})（静态红物防护）", "WARN")
                else:
                    pending[pos0] = (cnt0, t0, clicks + 1, confirmed)
            else:
                pending[(tgt[0], tgt[1])] = (
                    _red_count_near(region, tgt[0], tgt[1]), now, 1, False)

        if len(clickable) >= QI_BATCH_CLICK_MIN:
            # 批量极速连点：一帧捕获全部目标连续点掉（每击 ~25ms，不再逐击
            # 重截窗）——杂气走到石头即消失，吞吐优先（9 目标 ~0.25s/轮，
            # 逐击重截窗 ~1.5s 实测太慢）；漂移误差以一轮为界，下一轮重截
            # 窗校正，漏点/过期位置自动在后续轮次补点
            for tgt in clickable:
                rapid_click(win, tgt[0], tgt[1], press=QI_RAPID_PRESS)
                _register_click(tgt)
                clicked += 1
                split_clicked += 1 if tgt[7] else 0
            continue
        # 单目标路径：快速点击（跳过拟人化弧线移动与落点抖动），点击后
        # 立即重新截帧，保证每次点击都基于最新画面（漂移时效优先）
        tgt = clickable[0]
        precise_click(win, tgt[0], tgt[1], move_duration=0.02)
        _register_click(tgt)
        clicked += 1
        split_clicked += 1 if tgt[7] else 0
    # 超时诊断：保存最后一帧标注图（打包版用户反馈「杂气多时点不完」，
    # 远程无复现条件，靠标注帧定位残留团块形态）
    try:
        full = win.capture(force_activate=False, format=WindowCaptureFormat.NUMPY)
        if full is not None:
            l, t, r, b = QI_REGION
            clusters, _ = _find_red_qi_clusters(full[t:b, l:r])
            os.makedirs(CHUYIJUEBAO_DEBUG_DIR, exist_ok=True)
            path = os.path.join(CHUYIJUEBAO_DEBUG_DIR,
                                f"qi_timeout_{time.strftime('%Y%m%d_%H%M%S')}.png")
            _mark_qi_debug(full, clusters, path)
            log(f"[{prefix}] 锄遗聚宝超时诊断: 最后一帧 {len(clusters)} 个目标，"
                f"已保存 {path}", "WARN")
    except Exception:
        pass
    return False, (f"锄遗聚宝超时（{timeout}s，主图未消失；共消除 {clicked} 个，"
                   f"拆分点击 {split_clicked}，超面积团块 {oversized_frames} 帧，"
                   f"单帧最多 {max_clusters_seen} 个）")
