# -*- coding: utf-8 -*-
"""300环 画符小游戏（灵心巧手）：照描左侧符文 → 点完成。

2026-11-27 实机标定（可乐可口 36096466，用户当场说明 + 闭环验证）：
- 入口：任务追踪「灵心巧手[江湖历练任务]」（内容「蝴蝶妹妹收藏了神秘
  宝贝，过去打听一下吧！」）→ 蝴蝶妹妹对话框选项「（一）切就交给我
  吧！」→ 「制符」小游戏窗口。
- 窗口（位置固定，两次实机一致）：左侧符纸=模板符文，右侧符纸=画纸，
  底部「重画」「完成」；窗口 X 关闭。
- 画法：按住左键沿笔画移动（静默拖拽消息有效，笔刷粗约 12px）。
  **通过判定看笔顺方向，不是描出相同形状就能过**（用户 2026-11-27
  实机纠正）：形状一样但方向反了（如点从右下往左上画）点「完成」
  无反应。方向规则：长笔画粗端起笔、细端出锋收笔；点（短笔画）左上
  尖锋入笔、右下顿笔成肚，**尖端起笔**；两端粗细相当按左上端起笔。
  2026-11-27 实机：方向修正后一次通过，弹出「画的太好了，简直就是
  神来之笔啊。」并推进任务。每笔一次拖拽（按下→移动→抬起），按笔画
  左缘从左到右依次画。
- 完成判定：点「完成」后窗口消失 = 成功（任务推进，收尾由 follow 对话
  链处理）；窗口仍在 = 相似度不足 → 点窗口右上角 X 关闭，返回
  「待重走」交回任务侧重走任务追踪重新开始（重开会随机换一个符文，
  用户 2026-11-27 规则）。同一窗口只画 1 次——实测点「重画」后符文
  不换、第二次落笔与第一次完全相同，重画重试无收益（用户 2026-11-27
  实机）。
- 模板提取（纯图像，无 OCR）：左纸内「中性黑」像素（亮度<80 且通道
  极差<25——排除红色印章/纸边）→ 连通域（每笔一个，面积>=150）→
  Zhang-Suen 细化 → 端点间 BFS 取主干路径；起笔端按上面的笔顺规则
  选（长笔画取笔宽较大端、点取较小端）。已知局限：多笔相交成一整块
  的连笔字形只能描出骨架最长的一支（缺笔必不过）——由关窗重开换
  符文兜底（2026-11-27 实机）。
- 几何：以「重画」按钮模板命中中心为窗口锚点（不透明控件，与背景/
  位置无关），两张符纸与「完成」按钮位置相对锚点固定（2026-11-27
  标定；早期用半透明的「制符」竖牌做锚点，换背景会掉分已弃用）。
- 符文配方库（用户 2026-11-28 方案，实机验证 3 张）：骨架推断对带
  分叉/连笔字形会漏笔（如「细喙分叉」上笔只描主干），准确率低。改为
  先按左侧符纸墨迹掩码在 res/huafu_runes.json 里匹配符文（同符文渲染
  逐像素一致，实测两帧 IoU=1.000），命中即按该符文已验证的固定笔顺/
  路径回放；一张符文一条配方，新增/修改互不影响。未收录的符文仍走
  骨架推断兜底（失败关窗重开换符文）。验证通过的实机画法记录：
  - 0d0fe6e2（2 笔）：上笔从左上细喙起笔走完横条再下斜撇；下笔从底部
    弯钩起笔盘入螺旋。
  - 6d5449c0（3 笔，用户说明「横 竖 捺」）：横(左上→右上) / 竖(竖线
    顶端→底部交点) / 捺(中左端→交点→右下尖)。
  - 35e81725（2 笔）：上撇(左厚端→右上尖) / 下整笔(右尖→峰顶→底尾)。
  - 4c5ffd8c（4 笔，石字形）：横(左上→右上) / 撇(上→左下) / 口外框整笔
    (左上→下→底→上→右上) / 口上横(左→右)。标准口三笔（竖/横折/横）
    实机不过——此风格的口按「U 形外框 + 上横」拆分。
  - 1ac0c63f（3 笔，而字形）：横(左上→右上) / 横折钩(顶点→右上→
    右下→钩尖) / 左侧环形整笔(环缺口外端→绕左/上→中竖→缺口内端)。
    左侧环内的小孔是撇与中竖搭成的闭环，必须按「环形一笔最后画」拆分；
    撇/竖分开画等 20 余种组合实机均不过。
  - 82153d81（4 笔，双钩+双竖）：四个独立组件各一笔，顺序按 x 列
    （左上钩→左竖→右上钩→右竖），均粗端起笔；此字形骨架推断同序
    同向即可通过（2026-11-28 实机）。
  - 笔宽与运笔速度有关（用户 2026-11-28：画得快线细、慢就粗），当前
    配方均在 DRAW_MOVE_INTERVAL 默认速度下验证通过；遇到需要更粗笔道
    的符文再按条目加 per-rune 速度参数。

import 期零副作用；阻塞循环由任务侧 wait_handler 调用 play()。
"""
import json
import time
from collections import deque

import cv2
import numpy as np

from ..core import app_utils
from ..core.app_utils import log

TAG = "[300环画符] "

# ---- 窗口锚点模板与搜索条带（窗口相对坐标）----
# 锚点用「重画」按钮（70x37，不透明、外观全程不变）：最早用「制符」竖牌
# 做锚点，但竖牌是半透明贴图（牌面透出世界画面），换一张地图/换位置后
# 模板像素与背景混合不同会掉分（用户 2026-11-27 指出）。按钮是绘制在
# 羊皮纸上的实心控件，与背景无关——搬到另一背景+另一位置后仍 1.000 命中
# （2026-11-27 实机帧合成验证）。
ANCHOR_ICON = "sanbaihuan_huafu_redraw.png"  # 「重画」按钮 70x37
ANCHOR_BAND = (447, 185, 930, 705)           # 实机窗口 (497,235)-(880,655) 外扩 50px
ANCHOR_THRESHOLD = 0.9                       # 实机命中 0.999~1.000 / 非窗口 <=0.19
FULL_CHECK_INTERVAL = 3.0                    # 带区未命中→全帧兜底的限频（秒，
                                             # 用户可拖动窗口到带区外时靠它兜住）
_last_full_check = 0.0
# ---- 相对锚点（「重画」按钮中心）的固定几何（2026-11-27 实测）----
LEFT_PAPER_REL = (-65, -366, 151, 345)      # 左符纸 (x,y,w,h)，锚点 = (593,624)
RIGHT_PAPER_DX = 180                        # 右纸相对左纸水平偏移
FINISH_REL = (201, 0)                       # 「完成」按钮中心（锚点同一行右侧）
# ---- 关窗重开（用户 2026-11-27 规则：画 1 次不通过 → 点 X 关窗 →
# 重走任务追踪重新开始，符文随机换一个）----
# X = 窗口右上角 28x21 深色圆底关闭按钮。2026-11-27 可乐可口实机标定：
# 锚点 (593,624) 帧上全帧匹配 1.000 命中中心 (860,250)，相对锚点
# (267,-374)；点击后窗口 <1.5s 关闭（关窗后 NPC「这个骗子，竟敢溜！」
# 对话由 follow 对话链推进）。窗口可拖动，X 随锚点移动，按相对位置
# 出搜索带。
CLOSE_ICON = "sanbaihuan_huafu_close.png"
CLOSE_REL = (267, -374)     # X 中心相对「重画」锚点
CLOSE_BAND_PAD = 40         # 期望位置 ±40px 搜索带（模板 28x21，留余量）
CLOSE_THRESHOLD = 0.9       # 静态控件，与锚点同标准（实测 1.000）
CLOSE_WAIT = 1.5            # 点 X → 等窗口消失时限（秒）
# ---- 符文提取 ----
PAPER_INSET_X = 8          # 纸内左右内缩（避开黑色纸边）
PAPER_INSET_Y = 10         # 纸内上下内缩
INK_MAX = 80               # 中性黑亮度上限（笔迹实测 0~60）
INK_CHROMA = 25            # 通道极差上限（红色印章 r-g 远超此值）
MIN_STROKE_AREA = 150      # 有效笔画最小面积
MIN_STROKE_DIM = 6         # 有效笔画最小边长
# 起笔端判定（用户 2026-11-27 实机纠正：通过判定看笔顺方向，形状描得
# 一样但方向反了不过；点从右下往左上画是反的）：
# - 长笔画：笔锋粗端起笔、细端出锋收笔 → 粗端起笔
# - 点（短笔画，骨架长度约 1~2 倍笔宽）：左上尖锋入笔、右下顿笔成肚
#   → 尖锋（细）端起笔
# - 两端粗细相当：按左上端起笔（横左→右、竖上→下）
DOT_LEN_WIDTH_RATIO = 3.0  # 点判据：端点间骨架长度 <= 3×最大笔宽
WIDTH_TIE_DELTA = 0.8      # 两端笔宽差 < 0.8 视为相当（按左上端起笔）
# ---- 节奏/重试 ----
FINISH_WAIT = 1.2          # 点完成 → 等窗口关闭的时限（秒）
FINISH_POLL = 0.12         # 等待轮询间隔（秒）
MAX_ATTEMPTS = 1           # 单次窗口内运笔次数：实测点「重画」后符文不换、
                           # 第二次与第一次完全相同，重画重试无收益——不通过
                           # 即关窗重走追踪换符文（用户 2026-11-27 实机）
DRAW_STEP_PX = 3           # 运笔插值步长（px）
DRAW_MOVE_INTERVAL = 0.012 # 每次插值移动间隔（秒）
# ---- 符文配方库（用户 2026-11-28 方案：成功过的符文存模板 + 画法）----
# 骨架推断对「带细喙分叉」等字形会漏笔，准确率低；改为优先识别左侧符纸
# 上的符文（墨迹掩码 IoU），命中后按已验证过的固定笔顺/路径回放。
# 库文件 res/huafu_runes.json：{id, mask(掩码模板文件名), strokes(相对
# 「重画」锚点的左侧模板空间点列，播放时 +RIGHT_PAPER_DX 移到右纸)}。
# 新建配方流程：实机画到「完成」按钮绿框亮起（点绿框即为通过）→ 记录
# 墨迹掩码与运笔点列入档；一张符文一条，互不影响（用户 2026-11-28）。
RUNE_LIB_FILE = "huafu_runes.json"
RUNE_MATCH_THRESHOLD = 0.85  # 掩码 IoU：同符文逐像素一致（2026-11-28 两帧
                             # 实测 IoU=1.000），不同符文远低于此
_lib_cache = None            # None=未加载；[]=加载失败/空库
_mask_cache = {}


def _bgr(img):
    """PIL/ndarray → BGR ndarray。"""
    if isinstance(img, np.ndarray):
        return img
    return cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)


def _locate(frame_bgr):
    """定位制符窗口：返回「重画」按钮中心 (x, y)，不在场返回 None。

    条带命中优先（~10ms）；带区未命中时限频做全帧兜底（~27ms/次，3s
    一次）——窗口通常固定在画面中部，用户拖动到带区外时也可靠兜底
    找回（性能规则：条带优先、全帧兜底限频）。
    """
    global _last_full_check
    from ..device import map as game_map
    bgr = _bgr(frame_bgr)
    score, pt = game_map.best_match_np(bgr, ANCHOR_ICON, region=ANCHOR_BAND)
    if score >= ANCHOR_THRESHOLD:
        return pt
    now = time.time()
    if now - _last_full_check >= FULL_CHECK_INTERVAL:
        _last_full_check = now
        score, pt = game_map.best_match_np(bgr, ANCHOR_ICON)
        if score >= ANCHOR_THRESHOLD:
            return pt
    return None


def is_ready(frame):
    """单帧判定：画符窗口是否在场（供 wait_handler 每轮询触发）。"""
    try:
        return _locate(frame) is not None
    except Exception:
        return False


def _paper_rect(anchor, right=False):
    """符纸矩形 (l, t, r, b)：锚点 + 相对几何（right=True 取右纸）。"""
    l, t, w, h = LEFT_PAPER_REL
    x = anchor[0] + l + (RIGHT_PAPER_DX if right else 0)
    y = anchor[1] + t
    return (x, y, x + w, y + h)


def _zhang_suen(mask):
    """Zhang-Suen 细化（0/1 uint8），返回骨架 0/1。"""
    im = mask.copy().astype(np.uint8)
    changed = True
    while changed:
        changed = False
        for step in (0, 1):
            p = np.pad(im, 1)
            p2 = p[:-2, 1:-1]
            p3 = p[:-2, 2:]
            p4 = p[1:-1, 2:]
            p5 = p[2:, 2:]
            p6 = p[2:, 1:-1]
            p7 = p[2:, :-2]
            p8 = p[1:-1, :-2]
            p9 = p[:-2, :-2]
            b = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = (p2, p3, p4, p5, p6, p7, p8, p9, p2)
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8)
                    for i in range(8))
            if step == 0:
                m1 = (p2 * p4 * p6) == 0
                m2 = (p4 * p6 * p8) == 0
            else:
                m1 = (p2 * p4 * p8) == 0
                m2 = (p2 * p6 * p8) == 0
            kill = (im == 1) & (b >= 2) & (b <= 6) & (a == 1) & m1 & m2
            if kill.any():
                im[kill] = 0
                changed = True
    return im


def _ink_mask(img, paper):
    """符纸 inset 区域的中性黑墨迹掩码（0/1）及左上角全帧坐标。

    排除红色纸边/印章的判据见 INK_MAX / INK_CHROMA。帧越界返回
    (None, (0, 0))（调用方按不可用处理）。
    """
    arr = np.asarray(img)[:, :, :3].astype(int)
    l, t, r, b = paper
    ix0 = l + PAPER_INSET_X
    iy0 = t + PAPER_INSET_Y
    ix1 = r - PAPER_INSET_X
    iy1 = b - PAPER_INSET_Y
    if arr.shape[0] < iy1 or arr.shape[1] < ix1:
        return None, (0, 0)
    sub = arr[iy0:iy1, ix0:ix1]
    red, green, blue = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    mx = np.maximum(np.maximum(red, green), blue)
    mn = np.minimum(np.minimum(red, green), blue)
    ink = ((mx < INK_MAX) & ((mx - mn) < INK_CHROMA)).astype(np.uint8)
    return ink, (ix0, iy0)


def _load_mask(name):
    """加载配方库掩码模板（0/1），按文件名缓存。缺失返回 None。"""
    if name in _mask_cache:
        return _mask_cache[name]
    mask = None
    try:
        from ascript.windows.system import R
        raw = cv2.imread(str(R.img(name)), cv2.IMREAD_GRAYSCALE)
        if raw is not None:
            mask = (raw > 127).astype(np.uint8)
    except Exception:
        mask = None
    _mask_cache[name] = mask
    return mask


def _library():
    """加载符文配方库（res/huafu_runes.json）；失败返回空表并只警告一次。"""
    global _lib_cache
    if _lib_cache is not None:
        return _lib_cache
    _lib_cache = []
    try:
        from ascript.windows.system import R
        data = json.loads(open(R.res(RUNE_LIB_FILE), encoding="utf-8").read())
        for item in data.get("runes", []):
            strokes = [[(int(x), int(y)) for (x, y) in s]
                       for s in item.get("strokes", []) if len(s) >= 2]
            if strokes and item.get("mask"):
                _lib_cache.append({"id": str(item.get("id", "")),
                                   "mask": item["mask"],
                                   "strokes": strokes})
    except Exception as exc:  # 库缺失/损坏不影响骨架推断兜底
        log(f"{TAG}符文配方库加载失败：{exc}", "WARN")
    return _lib_cache


def _match_rune(mask):
    """掩码与库内符文比对：返回 (最高 IoU, 命中的配方或 None)。"""
    best_score, best = 0.0, None
    for item in _library():
        tpl = _load_mask(item["mask"])
        if tpl is None or tpl.shape != mask.shape:
            continue
        union = int(np.count_nonzero(mask | tpl))
        if not union:
            continue
        score = int(np.count_nonzero(mask & tpl)) / float(union)
        if score > best_score:
            best_score, best = score, item
    if best is not None and best_score >= RUNE_MATCH_THRESHOLD:
        return best_score, best
    return best_score, None


def _recipe_strokes(frame, paper, anchor):
    """匹配配方库：返回 (右纸运笔点列 或 None, 符文 id 或 None, 最高 IoU)。

    配方里存的是相对锚点的左侧模板空间坐标，播放时整体平移到右纸
    （+RIGHT_PAPER_DX），与配方验证时实际落笔位置一致。
    """
    try:
        mask, _origin = _ink_mask(frame, paper)
        if mask is None:
            return None, None, 0.0
        score, rune = _match_rune(mask)
    except Exception as exc:
        log(f"{TAG}符文配方匹配异常：{exc}", "WARN")
        return None, None, 0.0
    if rune is None:
        return None, None, score
    ax, ay = anchor
    strokes = [[(ax + RIGHT_PAPER_DX + x, ay + y) for (x, y) in stroke]
               for stroke in rune["strokes"]]
    return strokes, rune["id"], score


def _stroke_paths(img, paper):
    """提取纸内笔画骨架路径（全帧坐标），每笔一条有序点列。

    返回 [(pts, bbox_x), ...]；pts 已按笔顺方向排好（长笔画粗端起笔，
    点尖端起笔，见 DOT_LEN_WIDTH_RATIO 说明）。
    """
    ink, (ix0, iy0) = _ink_mask(img, paper)
    if ink is None:
        return []
    count, labels, stats, _cent = cv2.connectedComponentsWithStats(ink, 8)
    out = []
    for k in range(1, count):
        x, y, w, h, area = (int(v) for v in stats[k][:5])
        if area < MIN_STROKE_AREA or w < MIN_STROKE_DIM or h < MIN_STROKE_DIM:
            continue
        comp = (labels[y:y + h, x:x + w] == k).astype(np.uint8)
        skel = _zhang_suen(comp)
        ys, xs = np.where(skel == 1)
        pts = set(zip(xs.tolist(), ys.tolist()))
        if not pts:
            continue

        def _nbrs(p):
            cx, cy = p
            return [(cx + dx, cy + dy)
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if not (dx == 0 and dy == 0)
                    and (cx + dx, cy + dy) in pts]

        ends = [p for p in pts if len(_nbrs(p)) == 1]
        # 起笔端按笔顺方向判定（用户 2026-11-27 实机纠正）：长笔画粗端
        # 起笔；点（短笔画）尖端起笔；两端粗细相当按左上端起笔；无端点
        # （闭环）取最左上像素
        dist = cv2.distanceTransform(comp, cv2.DIST_L2, 5)

        def _width_at(p):
            cx, cy = p
            return float(dist[max(0, cy - 2):cy + 3,
                              max(0, cx - 2):cx + 3].max())

        def _path_len(a, b):
            """骨架图两端点间最短步数（8 邻域）；不可达返回 inf。"""
            seen = {a}
            queue = deque([(a, 0)])
            while queue:
                p, d = queue.popleft()
                if p == b:
                    return d
                for q in _nbrs(p):
                    if q not in seen:
                        seen.add(q)
                        queue.append((q, d + 1))
            return float("inf")

        if not ends:
            start = min(pts, key=lambda p: (p[1], p[0]))
        elif len(ends) == 2:
            a, b = ends
            wa, wb = _width_at(a), _width_at(b)
            wmax = max(wa, wb, 1.0)
            if _path_len(a, b) <= DOT_LEN_WIDTH_RATIO * wmax:
                start = a if wa <= wb else b   # 点：尖锋（细）端起笔
            elif abs(wa - wb) < WIDTH_TIE_DELTA:
                start = min(ends, key=lambda p: (p[1], p[0]))  # 左上起笔
            else:
                start = a if wa >= wb else b   # 长笔画：粗端起笔
        else:
            start = max(ends, key=_width_at)
        prev = {start: None}
        queue = deque([start])
        far = start
        while queue:
            p = queue.popleft()
            for q in _nbrs(p):
                if q not in prev:
                    prev[q] = p
                    queue.append(q)
                    far = q
        path = []
        cur = far
        while cur is not None:
            path.append((cur[0] + x + ix0, cur[1] + y + iy0))
            cur = prev[cur]
        out.append((path[::-1], x + ix0))
    out.sort(key=lambda it: it[1])  # 左→右依次画
    return out


def _simplify(points, step=DRAW_STEP_PX):
    """抽稀路径点（相邻点距离 >= step），保留首末点。"""
    if not points:
        return []
    out = [points[0]]
    for (x, y) in points[1:]:
        if abs(x - out[-1][0]) + abs(y - out[-1][1]) >= step:
            out.append((x, y))
    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


def _wait_window_gone(win, timeout):
    """点完成/点 X 后等窗口消失：返回 True=窗口已关。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        frame = win.capture(force_activate=False)
        if frame is None:
            time.sleep(FINISH_POLL)
            continue
        if _locate(frame) is None:
            return True
        time.sleep(FINISH_POLL)
    return False


def _close_window(win):
    """点窗口右上角 X 关闭制符窗口；返回窗口是否已确认关闭。

    单帧完成「锚点定位 → 相对搜索带匹配 X → 点击」，随后用「重画」
    锚点轮询确认窗口消失（不拿 X 模板消失作关闭证据：X 与锚点同属
    窗口，锚点判定沿用进场/完成同一标准）。窗口已不在场按已关闭
    返回 True（调用方据此重走追踪）。
    """
    from ..device import map as game_map
    frame = win.capture(force_activate=False)
    if frame is None:
        return False
    bgr = _bgr(frame)
    anchor = _locate(bgr)
    if anchor is None:
        return True
    cx = anchor[0] + CLOSE_REL[0]
    cy = anchor[1] + CLOSE_REL[1]
    band = (cx - CLOSE_BAND_PAD, cy - CLOSE_BAND_PAD,
            cx + CLOSE_BAND_PAD, cy + CLOSE_BAND_PAD)
    score, pt = game_map.best_match_np(bgr, CLOSE_ICON, region=band)
    if score < CLOSE_THRESHOLD:
        log(f"{TAG}画符：关闭按钮未命中（{score:.3f}），无法关窗重开",
            "WARN")
        return False
    app_utils.human_click(win, pt[0], pt[1])
    return _wait_window_gone(win, CLOSE_WAIT)


def play(win, prefix="300环"):
    """画符小游戏完整流程（阻塞）。返回 (是否完成, 描述, 是否已关窗待重走)。

    取模板 → 逐笔按「粗端→细端」运笔 → 点完成 → 窗口关闭即成功；
    同一窗口只画 1 次（实测「重画」后符文不换、第二次与第一次完全
    相同，重画重试无收益，用户 2026-11-27 实机）——不通过即点 X
    关窗，第三项返回 True 交回任务侧重走任务追踪重新开始（重开会
    随机换一个符文）。
    """
    tag = f"{TAG}" if not prefix else f"[{prefix}] "
    try:
        anchor = _locate(win.capture(force_activate=False))
    except Exception:
        anchor = None
    if anchor is None:
        return False, "画符窗口不在场", False
    # 静默通道在窗口非前台时仍可用；但画符 UI 实测对前台更稳，尽力激活
    try:
        if not win.is_active():
            if win.activate():
                time.sleep(0.2)
    except Exception:
        pass
    attempts = 0
    cap_fails = 0
    while attempts < MAX_ATTEMPTS:
        frame = win.capture(force_activate=False)
        if frame is None:
            # 截图失败不消耗运笔次数：窗口仍在，限次重试（防死循环空转）
            cap_fails += 1
            if cap_fails >= 10:
                return False, "画符窗口截图失败", False
            time.sleep(0.2)
            continue
        anchor = _locate(frame)
        if anchor is None:
            return True, "画符窗口已关闭", False
        attempts += 1
        paper = _paper_rect(anchor)
        # 优先按已验证配方回放（用户 2026-11-28 方案）：同一符文固定画法，
        # 避免骨架推断在带分叉/连笔字形上漏笔；未收录才走骨架推断兜底。
        recipe, rune_id, score = _recipe_strokes(frame, paper, anchor)
        if recipe:
            log(f"{tag}画符：命中符文配方 {rune_id}（IoU {score:.3f}），"
                f"按已验证画法运笔", "SYS")
            strokes = recipe
        else:
            if _library():
                log(f"{tag}画符：符文未收录（库 {len(_library())} 条，"
                    f"最高 IoU {score:.3f}），按骨架推断运笔", "SYS")
            dx = RIGHT_PAPER_DX  # 右纸 = 左纸整体右移（同尺寸、同锚点）
            strokes = [[(x + dx, y) for (x, y) in path]
                       for path, _bx in _stroke_paths(frame, paper)]
        if not strokes:
            log(f"{tag}画符：未提取到符文笔画，关窗重开", "WARN")
        else:
            drawn = 0
            for points in strokes:
                points = _simplify(points)
                if len(points) < 2:
                    continue
                app_utils.drag_path(win, points, step_px=DRAW_STEP_PX,
                                    move_interval=DRAW_MOVE_INTERVAL)
                drawn += 1
                time.sleep(0.04)
            time.sleep(0.25)
            # 点完成前复检窗口在场（窗口若已关闭/消失，点击会落到世界画面
            # 取消寻路/让角色走路，2026-11-11 点屏保护同款教训）
            check = win.capture(force_activate=False)
            if check is None or _locate(check) is None:
                return True, "画符窗口已关闭", False
            finish = (anchor[0] + FINISH_REL[0], anchor[1] + FINISH_REL[1])
            app_utils.human_click(win, finish[0], finish[1])
            if _wait_window_gone(win, FINISH_WAIT):
                return True, f"画符完成（{drawn} 笔）", False
            log(f"{tag}画符：完成按钮未响应（相似度不足），关窗重开 "
                f"（{attempts}/{MAX_ATTEMPTS}）", "WARN")
    # 单次运笔未通过：点 X 关窗，交回任务侧重走任务追踪重新开始
    # （重开会随机换一个符文，用户 2026-11-27 规则）
    if _close_window(win):
        return False, "画符未通过，已关闭制符窗口，等待重走任务追踪换符文", True
    return False, "画符未通过，关闭窗口失败", False
