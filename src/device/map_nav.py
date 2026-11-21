# -*- coding: utf-8 -*-
"""地图坐标导航：世界地图/层级图 → 悬停标定 → 校验后点击寻路。

2026-11-20 实机验证的机制（诊断-地图导航与后续「无快捷寻路」共用引擎）：

- 打开目标地图（世界地图点标签；层级图先点标签出层块页，再点层块
  进层）→ 地图窗口出现
- 地图窗口支持**悬停读坐标**：静默 WM_MOUSEMOVE 到地图上任意点，
  鼠标旁浮出黄色「x,y」标签（~0.7s 内出现）——像素↔坐标的真值采样
- 用 2~3 个悬停样本解出该窗口的线性变换（实测城市详情图 1.5 px/格、
  层内小图 4.2 px/格，窗口/地图不同比例不同，必须逐窗口现解）
- 目标像素 = 变换计算值，**点击前必须悬停校验**（读回坐标与目标差
  ≤2 格才点）：计算点在窗外时悬停无浮层，据此拒绝点击（防点到世界
  画面让角色乱走）
- 点击地图点 = 开始寻路（同图/跨图均可，跨图由客户端自动过图）；
  点击后 6s 内无位移按点到飞行旗等无效点处理，偏移 14px 重试一次
- 到达判据：HUD 地名 + 坐标 ±ARRIVE_TOLERANCE；位置 12s 不变提前
  判失败（目标不可达时客户端会站在最近可达点）

关于搜索框（坐标输入 + 红环）：客户端会保留上次搜索值，实测退格 /
Ctrl+A / 拖选均无法清空输入框，输入会追加成错误坐标——不作为生产
路径（机制见记忆 map-coord-search-red-ring）。
"""
import json
import re
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image
from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..core.app_utils import human_click, log
from . import map as game_map
from .battle import is_in_battle_frame  # 途中遇战斗冻结到达/进层计时


# ---- 中断（诊断面板「中断寻路」）----
class NavigationCancelled(Exception):
    """导航被用户中断。"""


_CANCEL = threading.Event()


def request_cancel():
    """请求中断正在进行的导航（线程安全，任意线程可调用）。"""
    _CANCEL.set()


def cancel_requested():
    """是否已收到协作式中断请求（诊断「中断寻路」）。

    供任务侧等待循环（如客栈 _go_to_inn）在诊断验证场景下响应中断；
    任务子进程与主进程的中断标记相互独立，生产流程不受影响。
    """
    return _CANCEL.is_set()


def _check_cancel():
    """中断检查点：各轮询循环每轮调用，命中即抛 NavigationCancelled。"""
    if _CANCEL.is_set():
        raise NavigationCancelled()


# ---- 内置标定表（res/map_nav_affines.json）----
# 按「地图名 + 游戏窗口尺寸」记录悬停标定解（比例+原点），避免每次
# 导航重新采样（省 ~4s）。地图窗口可拖动 → 原点可能漂移，因此表值
# 只作热启动，点击前仍做悬停校验（偏差 1~2 次悬停内自动修正），校验
# 不通过回退完整采样。龙窟/凤巢各层暂不内置（TODO，首次使用现场采样
# 并记入会话缓存）。
AFFINE_TABLE_FILE = "map_nav_affines.json"
_AFFINE_TABLE = None
_AFFINE_TABLE_MTIME = None
_SESSION_AFFINES = {}

# ---- 搜索条定位（仅作锚点：确定地图窗口水平位置/条带高度）----
# 搜索条 OCR 区域（窗口相对）：大图详情图搜索条 y≈217-229、层内小图
# y≈277（2026-11-20 实测两种窗口）；730x250，长边 ≤736 保证区域引擎
# 按原分辨率检测。
SEARCH_BAR_REGION = (150, 150, 880, 400)
# 输入框相对「高级搜索」按钮左缘的固定偏移（双窗口实测一致，仅用于
# 换算采样起点，不用于输入）
FIELD_X_OFFSET = -88
# 搜索条定位模板（2026-11-20 实机标定，含按钮边框 80x34）：世界地图
# 城市大图 1.000、层内小图 0.976~0.988，无地图画面（场景）仅 0.301——
# 模板匹配 ~10ms 比区域 OCR（~100-170ms）快一个量级；模板未命中才走
# OCR 锚点兜底。偏移 = 模板中心 → 采样基准点（= 高级搜索左缘-88 与
# 上下缘均值，城市图实测 -121/0，层内小图 -120/0）
SEARCH_BAR_ADV_ICON = "map_search_bar_adv.png"
SEARCH_BAR_ADV_THRESHOLD = 0.90
SEARCH_BAR_ADV_OFFSET = (-121, 0)

# ---- 层级地图（层块页）----
# 龙窟/凤巢各 7 层（2026-11-20 用户确认）；
# 大雁塔 6 层走特殊入口（世界地图「寻路」面板，见 _open_xunlu_destination）。
# TODO（用户 2026-11-20 决定先不处理）：海底迷宫/地狱迷宫也是层级图
# （各 4 层，OCR 标题为繁体「迷宮」）——暂不在下拉中出现。
LAYER_MAP_FLOORS = {"龙窟": 7, "凤巢": 7, "大雁塔": 6}
# 暂不支持的层级/特殊地图（诊断下拉排除）
UNSUPPORTED_NAV_MAPS = {"海底迷宫", "地狱迷宫"}
_CN_NUM = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
LAYER_PAGE_REGION = (280, 200, 1010, 670)
# 层块标题中心 → 层块本体点击下移量（实测标题@(483,245)→本体@(484,300)）
LAYER_TILE_LABEL_DY = 50
# 进层等待：层块点击不保证传送（2026-11-20 实测有时逐层步行进入，
# 六层→三层约 30s+；空闲传送则瞬间），45s 不够，放宽到 120s
LAYER_ARRIVE_TIMEOUT = 120.0
# 层块页 OCR 行内碎片合并（同行 y 中心 ±8px、水平间距 ≤30px 视为同标签）
LAYER_ROW_GROUP_DY = 8
LAYER_TILE_MERGE_GAP = 30
# 层块页网格（3 列；2026-11-20 凤巢层块页实测列距≈193/行距≈139，
# 仅作拟合失败时单锚点外推的默认步长）
LAYER_GRID_COLS = 3
LAYER_GRID_DX_DEFAULT = 193
LAYER_GRID_DY_DEFAULT = 139

# ---- 大雁塔特殊入口：世界地图 →「寻路」面板（2026-11-20 用户规则）----
# 流程：世界地图底部「寻路」→ 面板 [从当前图][到输入框][搜索] → 在到
# 输入框输入目标层 → 搜索 → 结果列表滚到底 → 点黄色「到X层」链接 →
# 打开目的地地图窗口（随后走通用悬停标定/点击/到达）。搜索按钮/到标签
# 用 OCR 锚点；结果列表用静默滚轮滚动（WM_MOUSEWHEEL 实测有效）。
XUNLU_BTN_REGION = (250, 560, 620, 700)
# 寻路按钮兜底区域（按钮位置漂移时放宽搜索）
XUNLU_BTN_WIDE_REGION = (200, 520, 760, 740)
XUNLU_PANEL_TOP_REGION = (480, 330, 880, 390)
# 面板锚点兜底区域（面板位置变化时放宽搜索；不依赖固定坐标）
XUNLU_PANEL_WIDE_REGION = (430, 300, 940, 420)
# 面板「搜索」按钮模板（2026-11-20 实机标定，含边框+边框外 3px，69x39）：
# 面板打开 1.000（4 个不同时刻/不同从字段内容全中），世界地图/场景
# 0.15~0.24——面板在场判定与锚点定位优先用模板（~10ms），面板内布局
# 固定，到/从标签与列表区域按相对模板中心的偏移推算；OCR 锚点保留
# 兜底（按钮悬停高亮等渲染差异时）
XUNLU_SEARCH_ICON = "map_nav_xunlu_search_btn.png"
XUNLU_SEARCH_THRESHOLD = 0.90
# 模板中心 → 到标签/搜索按钮/从标签 rect（cur.png 实测 805..851 等换算）
XUNLU_DAO_OFF = (-179, -11, -154, 9)
XUNLU_SEARCH_OFF = (-24, -11, 22, 12)
XUNLU_FROM_OFF = (-315, -10, -296, 7)
# 结果列表高度（由搜索按钮下缘向下推，宽度由「从/搜索」锚点推出）
XUNLU_LIST_HEIGHT = 185
XUNLU_SCROLL_POINT = (680, 470)
XUNLU_SCROLL_STEPS = 12
# 每次滚动 10 格：目的地一定在列表最下面（用户 2026-11-20 规则），
# 实测 灵兽村→大雁塔四层 路线一次滚 10 格即到底、目的地行完整可见
# （滚 4 格要两次，且中间帧只露出其它层的黄色链接）
XUNLU_WHEEL_NOTCHES = 10
# 目的地链接黄色像素下限（实测链接行数百个黄色像素、绿色步骤行 ~0；
# 黄色 RGB≈(255,255,0)）
XUNLU_LINK_YELLOW_MIN = 60
# 链接文本校验：长路线的每个步骤行都有一个黄色「…传送到 大雁塔X层」
# 链接，同一屏多个同形状黄带，位置/形状判据分不出层——点击前把黄带
# 裁出来 2x 区域 OCR 读回文本（实测 ~30ms/带、1.000；x1 曾丢字）。
BAND_OCR_PAD = 5
BAND_OCR_SCALE = 2
# 地图窗口右侧竖排标题（实测「大雁塔四层」rect (972,258)-(1005,416)、
# 置信度 1.000、h≈4.6w）：点开链接后用标题核对开出来的是不是目的地
# 那一层（横排的相邻层入口标签/竖排诗句按形状与正则排除）
MAP_TITLE_REGION = (850, 180, 1110, 745)
MAP_TITLE_RE = re.compile(r"^大雁塔[一二三四五六七八九十]+层$")

# ---- 悬停采样 ----
# 候选点相对搜索条 X 输入框中心的偏移（覆盖大地图与层内小图两种窗口；
# 窗外的候选悬停无浮层，自动跳过）
HOVER_DX = (60, 240, 420, 600)
HOVER_DY = (110, 280, 430)
HOVER_WAIT_STEPS = (0.7, 0.35)   # 移动后两次截帧等待（浮层 ~0.7s 内出现）
HOVER_MAX_SAMPLES = 4
# 浮层坐标：黄色「x,y」（OCR 文本含逗号分隔数字）
_TOOLTIP_RE = re.compile(r"(\d{1,3})\s*[,，]\s*(\d{1,3})")
# 变换合理性校验（实测 1.5~4.2 px/格；两轴比例应一致——方形格）
SCALE_MIN, SCALE_MAX = 0.8, 8.0
AXIS_RATIO_TOLERANCE = 0.25
# 内点判定（px）：地图窗口边缘的采样点会读到坏坐标（浮层越界/钳位），
# 按残差剔除，不因个别离群样本整体失败
AFFINE_INLIER_PX = 8.0
# 悬停校验误差（格）：超过则按误差修正点击点重试
AIM_TOLERANCE = 2
# 内置标定点的悬停无浮层小偏移恢复（px）：点落到图标/地图边缘/窗口外时
# 浮层不出——先试 4 个方向的小偏移找回地图内容，避免直接回退完整采样
# （2026-11-21 实机：大雁塔五层内置标定点 (864,388) 无浮层，偏移 4px
# 的 (868,388) 正常；完整采样耗时 6.3s）
AIM_NOHOVER_OFFSETS = ((14, 0), (-14, 0), (0, 14), (0, -14))
AIM_ATTEMPTS = 3

# ---- 点击与到达 ----
MOVE_START_TIMEOUT = 6.0     # 点击后位移启动判定（同 mowang 位移校验）
CLICK_OFFSET_RETRY = 14      # 点到飞行旗等无效点的偏移重试（像素）
ARRIVE_TOLERANCE = 10
# 大雁塔特殊入口要经车夫/驿站长路程（实测 洛阳城→大雁塔四层 2 分钟+），
# 超时放宽到 240s；真·不可达由 12s 停滞检测兜底
ARRIVE_TIMEOUT = 240.0
ARRIVE_STALL_TIMEOUT = 12.0  # 位置连续不变判停滞（目标不可达）
ARRIVE_POLL_INTERVAL = 1.0
# 位置停滞 5s 先按「寻路被打断」补一次寻路（2026-11-21 用户需求）：
# 客户端寻路被点世界/其它操作打断时人物原地站住，重新开图再点一次
# 目标点即恢复；补过一次仍停滞才按 ARRIVE_STALL_TIMEOUT 判失败。
# 主动中断（request_cancel/诊断「中断寻路」）不补——那是用户要停。
ARRIVE_RESTART_STALL = 5.0
ARRIVE_RESTART_TEXT = "位置 5s 未变化（寻路可能被打断，补一次寻路）"


def layer_map_names():
    """层级图全名列表（龙窟一层…七层、凤巢一层…七层）。"""
    names = []
    for base, floors in LAYER_MAP_FLOORS.items():
        for i in range(1, floors + 1):
            names.append(f"{base}{_CN_NUM[i - 1]}层")
    return names


def parse_navigate_target(name):
    """导航目标解析：层级图 → (基础图, 层名)；普通图 → (名称, None)。"""
    name = (name or "").strip()
    for base, floors in LAYER_MAP_FLOORS.items():
        for i in range(1, floors + 1):
            layer = f"{base}{_CN_NUM[i - 1]}层"
            if name == layer:
                return base, layer
    return name, None


def all_navigate_maps():
    """诊断-地图导航下拉选项：常规地图 + 龙窟/凤巢/大雁塔各层。

    排除三类：
    - UNSUPPORTED_NAV_MAPS（海底迷宫/地狱迷宫，用户 2026-11-20 暂缓）；
    - 层级图基名（龙窟/凤巢/大雁塔）——世界地图点它们只会打开层块页
      或无法进入，不能作为导航目标，只能选具体层名（2026-11-20 审查发现）；
    与 WORLD_MAP_LABELS 同序，层名附后（共 70 项，与内置标定表一致）。
    """
    return [k for k in game_map.WORLD_MAP_LABELS
            if k not in UNSUPPORTED_NAV_MAPS
            and k not in LAYER_MAP_FLOORS] + layer_map_names()


def names_match(hud_name, target):
    """HUD 地名与目标地图是否同一张图（允许别名，如 长安 ↔ 长安城）。

    仅用于普通地图；层级图用层名精确匹配（resolve 会把「龙窟一层」
    模糊归一成「龙窟」，不能用于层判定）。
    """
    hud = (hud_name or "").strip()
    tgt = (target or "").strip()
    if not hud or not tgt:
        return False
    if hud == tgt:
        return True
    rh = game_map.resolve_world_map_name(hud)
    rt = game_map.resolve_world_map_name(tgt)
    return bool(rh and rt and rh == rt)


def _bar_points(bar_y, adv_left):
    """搜索条锚点 → 采样基准点（纯计算，供测试）。"""
    return {"y": int(bar_y), "x": int(adv_left + FIELD_X_OFFSET)}


def locate_search_bar(win, img=None, allow_ocr=True):
    """定位地图窗口搜索条，返回 {x, y}（采样基准点）；找不到返回 None。

    优先「高级搜索」按钮模板（2026-11-20 实机标定：城市图 1.000、
    层内小图 0.976~0.988，无地图画面 0.301；模板匹配 8-11ms，比区域
    OCR 快约一个量级）；模板未命中退回 OCR 锚点（「查询NPC名字」+
    「高级搜索」两段，X/Y 占位符可能被并进前一段）。
    allow_ocr=False 只做模板匹配——轮询等待窗口出现的热路径用它，
    避免每轮对无窗口画面付 OCR 成本（实测 8ms vs 150ms+）。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return None
    score, pt = game_map.best_match_np(game_map.bgr_frame(img),
                                       SEARCH_BAR_ADV_ICON,
                                       region=SEARCH_BAR_REGION)
    if score >= SEARCH_BAR_ADV_THRESHOLD:
        return {"x": int(pt[0] + SEARCH_BAR_ADV_OFFSET[0]),
                "y": int(pt[1] + SEARCH_BAR_ADV_OFFSET[1])}
    if not allow_ocr:
        return None
    items, _ = game_map.ocr_regions(img, [SEARCH_BAR_REGION])
    anchor_rect = None
    adv_rect = None
    anchor_score = adv_score = 0.0
    for text, rect, score in items:
        t = text or ""
        if "高级搜索" in t:
            if score > adv_score:
                adv_score, adv_rect = score, rect
        elif "查询" in t and "NPC" in t.upper():
            if score > anchor_score:
                anchor_score, anchor_rect = score, rect
    if adv_rect is None or anchor_rect is None:
        return None
    bar_y = (anchor_rect[1] + anchor_rect[3] + adv_rect[1] + adv_rect[3]) // 4
    return _bar_points(bar_y, adv_rect[0])


def parse_tooltip_text(text):
    """解析悬停浮层坐标文本「157,112」→ (157, 112)；不匹配返回 None。"""
    m = _TOOLTIP_RE.search(text or "")
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def _hover_move(win, px, py):
    """悬停到窗口相对点 px,py：静默模式走消息、物理模式移动物理鼠标。"""
    if app_utils.silent_mode_on():
        from . import silent_click  # 延迟导入：避免模块级循环
        return silent_click.send_silent_move(win, int(px), int(py)) is not None
    app_utils.human_move(win.rect.left + int(px), win.rect.top + int(py))
    return True


def _hover_coord(win, px, py):
    """悬停 (px,py) 并读取浮层坐标；浮层未出现返回 None。"""
    if not _hover_move(win, px, py):
        return None
    for wait in HOVER_WAIT_STEPS:
        time.sleep(wait)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        region = (max(int(px) - 30, 0), max(int(py) - 100, 0),
                  min(int(px) + 220, 1378), min(int(py) + 50, 831))
        items, _ = game_map.ocr_regions(img, [region])
        for text, _rect, _score in items:
            got = parse_tooltip_text(text)
            if got is not None:
                return got
    return None


def _collect_hover_samples(win, bar, emit):
    """在窗口内网格候选点采样悬停坐标，返回 [(px,py,gx,gy), ...]。"""
    samples = []
    for dy in HOVER_DY:
        for dx in HOVER_DX:
            _check_cancel()
            px = bar["x"] + dx
            py = bar["y"] + dy
            if px > 1350 or py > 810:
                continue
            got = _hover_coord(win, px, py)
            if got is None:
                continue
            samples.append((px, py, got[0], got[1]))
            emit(f"悬停采样: ({px},{py}) → ({got[0]},{got[1]})")
            if len(samples) >= HOVER_MAX_SAMPLES:
                break
        if len(samples) >= HOVER_MAX_SAMPLES:
            break
    return samples


def solve_affine(samples):
    """由悬停样本解像素↔坐标线性变换；样本不足/不合理返回 None。

    模型：px = ax + sx*gx，py = ay + sy*gy（屏幕 y 向下、游戏 y 向上）。
    按散布从大到小尝试样本对，解两轴（某轴散布不足时按方形格假设用
    另一轴比例补）；按残差统计内点，内点不足 max(2, n-1) 的对丢弃，
    取第一个达标解并用内点均值精修原点。返回 {"sx","sy","ax","ay"}。

    2026-11-20 灵兽村/化生寺实测：地图窗口右侧的采样点会读到坏坐标
    （浮层越界/钳位，如 (250,1)），旧版「任一样本残差大即整体失败」
    会误杀整张图——改为离群点剔除。
    """
    n = len(samples)
    if n < 2:
        return None
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            p1, p2 = samples[i], samples[j]
            spread = abs(p1[2] - p2[2]) + abs(p1[3] - p2[3])
            if spread >= 20:
                pairs.append((spread, p1, p2))
    pairs.sort(key=lambda t: -t[0])
    need = max(2, n - 1)
    for _spread, p1, p2 in pairs:
        dgx, dgy = p2[2] - p1[2], p2[3] - p1[3]
        dpx, dpy = p2[0] - p1[0], p2[1] - p1[1]
        sx = dpx / dgx if abs(dgx) >= 8 else None
        sy = dpy / dgy if abs(dgy) >= 8 else None
        if sx is None and sy is None:
            continue
        if sx is None:
            assert sy is not None  # 上一行已排除双 None
            sx = -sy
        if sy is None:
            sy = -sx
        if not (SCALE_MIN <= abs(sx) <= SCALE_MAX):
            continue
        if abs(abs(sx) - abs(sy)) > AXIS_RATIO_TOLERANCE * abs(sx):
            continue
        if sx <= 0 or sy >= 0:  # 东→右、北→上；方向不对说明样本/窗口异常
            continue
        ax = p1[0] - sx * p1[2]
        ay = p1[1] - sy * p1[3]
        inliers = [(px, py, gx, gy) for px, py, gx, gy in samples
                   if abs(ax + sx * gx - px) <= AFFINE_INLIER_PX
                   and abs(ay + sy * gy - py) <= AFFINE_INLIER_PX]
        if len(inliers) < need:
            continue
        # 内点均值精修原点（比例沿用样本对；内点≥2 保证稳定）
        ax = sum(px - sx * gx for px, _py, gx, _gy in inliers) / len(inliers)
        ay = sum(py - sy * gy for _px, py, _gx, gy in inliers) / len(inliers)
        return {"sx": sx, "sy": sy, "ax": ax, "ay": ay}
    return None


def _affine_table_path():
    """内置标定表路径（res/map_nav_affines.json）。"""
    from ascript.windows.system import R
    return Path(R.res(AFFINE_TABLE_FILE))


def reset_affine_cache():
    """清空标定缓存（内置表 + 会话；测试用）。"""
    global _AFFINE_TABLE, _AFFINE_TABLE_MTIME
    _AFFINE_TABLE = None
    _AFFINE_TABLE_MTIME = None
    _SESSION_AFFINES.clear()


def _load_affine_table():
    """加载内置标定表（按 mtime 热更新）；缺失/损坏返回 {}。

    标定脚本可能在程序运行期间生成/更新表文件——用 mtime 判断是否
    需要重载，避免必须重启控制面板才能用上新标定。
    """
    global _AFFINE_TABLE, _AFFINE_TABLE_MTIME
    path = None
    mtime = None
    try:
        path = _affine_table_path()
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if _AFFINE_TABLE is not None and mtime == _AFFINE_TABLE_MTIME:
        return _AFFINE_TABLE
    table = {}
    if path is not None and mtime is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                table = data
        except Exception:
            table = {}
    _AFFINE_TABLE = table
    _AFFINE_TABLE_MTIME = mtime
    return table


def _valid_affine(entry):
    """校验标定表条目（比例合理、方向正确），无效返回 None。"""
    try:
        aff = {k: float(entry[k]) for k in ("sx", "sy", "ax", "ay")}
    except (KeyError, TypeError, ValueError):
        return None
    if not (SCALE_MIN <= abs(aff["sx"]) <= SCALE_MAX):
        return None
    if aff["sx"] <= 0 or aff["sy"] >= 0:
        return None
    return aff


def affine_for(win, target):
    """取目标地图标定：会话缓存 → 内置表；布局不匹配/无记录返回 None。"""
    size = [int(win.rect.width), int(win.rect.height)]
    cached = _SESSION_AFFINES.get((target, size[0], size[1]))
    if cached is not None:
        return dict(cached)
    table = _load_affine_table()
    if list(table.get("layout") or []) != size:
        return None
    entry = (table.get("maps") or {}).get(target)
    if not entry:
        return None
    return _valid_affine(entry)


def remember_affine(win, target, affine):
    """把采样解记录到会话缓存（下次同图导航直接热启动）。"""
    _SESSION_AFFINES[(target, int(win.rect.width),
                      int(win.rect.height))] = dict(affine)


def _read_pos(win):
    img = win.capture(force_activate=False)
    if img is None:
        return None
    return game_map.read_map_position(None, img=img)


def _aim_and_click(win, affine, anchor_pt, tx, ty, emit,
                   on_affine_corrected=None):
    """计算目标像素 → 悬停校验 → 点击。返回 (ok, 点, 错误描述)。

    anchor_pt：采样锚点；None=内置标定热启动（无采样点可收缩，浮层不
    出现直接判失败，由调用方回退完整采样）。
    校验不到或偏差修正 3 次仍不达标 → 不点击。
    on_affine_corrected：本次发生偏差修正且点击成功时，回传修正后的
    标定 dict。调用方写入会话缓存（remember_affine）后，同图后续导航
    首次悬停即命中——内置表个别地图原点有 ~4 格偏差，不缓存会每次
    导航都多一次「瞄准→修正→再瞄准」的 0.8s 悬停（用户 2026-11-21
    日志：宝象国巫医每次瞄准两次）。
    """
    aff = dict(affine)
    sx, sy = aff["sx"], aff["sy"]
    px = aff["ax"] + sx * tx
    py = aff["ay"] + sy * ty
    corrected = False
    t0 = time.time()
    for _attempt in range(1, AIM_ATTEMPTS + 1):
        _check_cancel()
        if not (0 <= px <= 1377 and 0 <= py <= 830):
            return False, None, "计算出的点击点超出窗口范围"
        got = _hover_coord(win, px, py)
        if got is None:
            if anchor_pt is None:
                # 内置标定热启动：标定点悬停无浮层（点落在图标/地图边缘/
                # 窗口外）——先试小偏移找回地图内容，避免直接回退完整采样
                for dx, dy in AIM_NOHOVER_OFFSETS:
                    got = _hover_coord(win, px + dx, py + dy)
                    if got is not None:
                        px += dx
                        py += dy
                        emit(f"瞄准点悬停无浮层，偏移 ({dx:+d},{dy:+d}) 恢复")
                        break
                if got is None:
                    # 交由调用方回退完整采样
                    return False, None, "瞄准点悬停无浮层（标定可能过期）"
            else:
                # 不在图内容上（窗外/被浮层遮挡）：向已知有效采样点收缩
                emit(f"瞄准点 ({int(px)},{int(py)}) 悬停无浮层，向采样点收缩")
                px = (px + anchor_pt[0]) / 2
                py = (py + anchor_pt[1]) / 2
                continue
        ex, ey = got[0] - tx, got[1] - ty
        emit(f"瞄准 ({int(px)},{int(py)}) 读回 ({got[0]},{got[1]})，"
             f"偏差 ({ex:+d},{ey:+d})，用时 {time.time() - t0:.1f}s")
        if abs(ex) <= AIM_TOLERANCE and abs(ey) <= AIM_TOLERANCE:
            human_click(win, int(px), int(py))
            if corrected and on_affine_corrected is not None:
                on_affine_corrected(dict(aff))
            return True, (int(px), int(py)), ""
        # 偏差修正：读回值比目标大 ex → 该点对应的真实坐标偏大 →
        # 像素点要往小坐标方向移（px -= ex*sx）。符号写反会越修越偏，
        # 3 次用尽报「不收敛」（2026-11-20 审查发现）。
        px -= ex * sx
        py -= ey * sy
        # 把修正吸收进标定（px = ax + sx*tx → ax -= ex*sx）：点击成功后
        # 回传会话缓存，下次同图导航一次命中，不再重复修正
        aff["ax"] -= ex * sx
        aff["ay"] -= ey * sy
        corrected = True
    return False, None, "点击点悬停校验不收敛（读回坐标与目标偏差过大）"


def _wait_movement(win, before, timeout=MOVE_START_TIMEOUT):
    """点击后等待位移启动：位置变化或过图（位置暂不可读）即视为已移动。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancel()
        time.sleep(0.5)
        pos = _read_pos(win)
        if pos is None:
            return True  # 过图动画/位置暂不可读 → 视为已移动
        if before is None or pos != before:
            return True
    return False


def _wait_map_name(win, name, timeout, emit):
    """等待 HUD 地名变为指定层名（层级图进层判定）。

    途中遇战斗（随机遭遇）：战斗期坐标不可读、人物不动，战斗时间不计入
    超时（deadline 逐轮顺延），战斗由战斗线程自动操作（CLAUDE.md 规则 6
    同源：战斗等待不得计入跟踪超时）。
    位置日志降噪（2026-11-21）：只在地名变化（逐层过场景）时输出「位置」
    行——进层是逐层步行，原实现每个位置变化都输出（凤巢四层走两步就
    两行「凤巢四层」），生产日志刷屏。
    """
    deadline = time.time() + timeout
    last_name = None
    while time.time() < deadline:
        _check_cancel()
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            deadline += 0.8  # 战斗期不计入超时
            time.sleep(0.8)
            continue
        pos = game_map.read_map_position(None, img=img) if img is not None else None
        if pos is not None:
            name_now = str(pos[0]).strip()
            if name_now != last_name:
                emit(f"位置: {pos}")
            last_name = name_now
            if name_now == name:
                return True
        time.sleep(0.8)
    return False


def _merge_ocr_lines(items):
    """合并 OCR 行内碎片（如「凤巢」+「层」），返回 [(文本, cx, cy, 行框)]。

    层块标题里的「一/二」常被 OCR 吞掉或拆行（2026-11-20 实测：
    「凤巢一层」→「凤巢」+「层」）；寻路结果列表的长句也会被拆行。
    合并后统一按行匹配，缺字的层块标签再由网格拟合兜底。

    行分组按 y 中心 ±LAYER_ROW_GROUP_DY，再在行内按 x 排序后合并右侧
    相邻碎片（间距必须 0..LAYER_TILE_MERGE_GAP）。2026-11-20 龙窟实测
    教训：同排标签 y 中心有 1px 抖动（245/246），直接按 (cy,x) 排序会
    把「三层」排到「二层」前，负间距合并把两块砖并成一个假标签
    （「龙窟三层」返回两砖中缝 (782,245)，点进缝隙进不了层）。
    """
    frags = []
    for text, rect, score in items:
        # 繁体/形近归一（海底迷宫/地狱迷宫层块标题实测为「迷宮」）
        t = (text or "").replace(" ", "").translate(
            game_map.WORLD_MAP_OCR_ALIASES)
        if not t:
            continue
        frags.append((t, rect[0], rect[1], rect[2], rect[3],
                      (rect[1] + rect[3]) // 2))
    frags.sort(key=lambda f: f[5])
    rows = []
    for frag in frags:
        if rows and abs(frag[5] - rows[-1]["cy"]) <= LAYER_ROW_GROUP_DY:
            rows[-1]["items"].append(frag)
        else:
            rows.append({"cy": frag[5], "items": [frag]})
    lines = []
    for row in rows:
        row["items"].sort(key=lambda f: f[1])
        cur = None
        for t, left, top, right, bottom, _cy in row["items"]:
            if cur is not None \
                    and 0 <= left - cur["right"] <= LAYER_TILE_MERGE_GAP:
                cur["text"] += t
                cur["right"] = max(cur["right"], right)
                cur["top"] = min(cur["top"], top)
                cur["bottom"] = max(cur["bottom"], bottom)
                continue
            if cur is not None:
                lines.append(_merged_line_tuple(cur))
            cur = {"text": t, "left": left, "right": right,
                   "top": top, "bottom": bottom}
        if cur is not None:
            lines.append(_merged_line_tuple(cur))
    return lines


def _merged_line_tuple(cur):
    """合并行 → (文本, cx, cy, (left, top, right, bottom))。"""
    cx = (cur["left"] + cur["right"]) // 2
    cy = (cur["top"] + cur["bottom"]) // 2
    return (cur["text"], cx, cy,
            (cur["left"], cur["top"], cur["right"], cur["bottom"]))


def _fit_axis(points):
    """一维最小二乘：points=[(index, value)] → (base, step)；不足 2 点返回 None。"""
    groups = {}
    for i, v in points:
        groups.setdefault(i, []).append(v)
    if len(groups) < 2:
        return None
    idxs = sorted(groups)
    vals = [sum(groups[i]) / len(groups[i]) for i in idxs]
    n = len(idxs)
    mi = sum(idxs) / n
    mv = sum(vals) / n
    denom = sum((i - mi) ** 2 for i in idxs)
    if denom == 0:
        return None
    step = sum((i - mi) * (v - mv) for i, v in zip(idxs, vals)) / denom
    return mv - step * mi, step


def locate_layer_tile(img, base, layer_name):
    """在层块页帧中定位目标层标题中心；返回 (cx, cy) 或 None。

    优先直接匹配（碎片合并后含完整层名）；其次「基础名+层+序号」宽松
    匹配；最后用其它层标签拟合 3 列网格（列距≈193/行距≈139，2026-11-20
    凤巢层块页实测）推断缺字目标的位置。
    """
    items, _ = game_map.ocr_regions(img, [LAYER_PAGE_REGION])
    labels = _merge_ocr_lines(items)
    if not labels:
        return None
    partial = None
    for text, cx, cy, _rect in labels:
        if text == layer_name:
            return cx, cy
        if partial is None and layer_name in text:
            partial = (cx, cy)
    if partial is not None:
        return partial
    target_idx = None
    for i in range(LAYER_MAP_FLOORS[base]):
        if layer_name == f"{base}{_CN_NUM[i]}层":
            target_idx = i
            break
    if target_idx is None:
        return None
    numeral = _CN_NUM[target_idx]
    for text, cx, cy, _rect in labels:
        if base in text and "层" in text and numeral in text:
            return cx, cy
    # 网格拟合：从可识别的层标签推 3 列布局
    known = []
    for text, cx, cy, _rect in labels:
        if "层" not in text and base not in text:
            continue
        for i, cn in enumerate(_CN_NUM[:LAYER_MAP_FLOORS[base]]):
            if cn in text:
                known.append((i, cx, cy))
                break
    if not known:
        return None
    fit_x = _fit_axis([(i % LAYER_GRID_COLS, cx) for i, cx, _cy in known])
    fit_y = _fit_axis([(i // LAYER_GRID_COLS, cy) for i, _cx, cy in known])
    tgt_col = target_idx % LAYER_GRID_COLS
    tgt_row = target_idx // LAYER_GRID_COLS
    if fit_x is not None:
        px = fit_x[0] + fit_x[1] * tgt_col
    else:
        anchor = known[0]
        px = anchor[1] + (tgt_col - anchor[0] % LAYER_GRID_COLS) \
            * LAYER_GRID_DX_DEFAULT
    if fit_y is not None:
        py = fit_y[0] + fit_y[1] * tgt_row
    else:
        anchor = known[0]
        py = anchor[2] + (tgt_row - anchor[0] // LAYER_GRID_COLS) \
            * LAYER_GRID_DY_DEFAULT
    return int(px), int(py)


def _wait_layer_tile(win, base, layer_name, timeout):
    """在层块页定位目标层标题，返回标题中心；找不到返回 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            pt = locate_layer_tile(img, base, layer_name)
            if pt is not None:
                return pt
        time.sleep(0.4)
    return None


def _type_text(win, text):
    """向游戏输入框打字（静默 WM_CHAR 优先，物理键盘兜底）。

    返回 (是否成功, 通道描述)。大雁塔寻路面板的「到」输入框每次打开
    为空（2026-11-20 实测），不存在追加旧值的问题。
    """
    if app_utils.silent_mode_on():
        from . import silent_click  # 延迟导入：避免模块级循环
        info = silent_click.send_silent_text(win, str(text))
        if info is None:
            return False, "静默文本投递失败（窗口句柄不可用）"
        return True, "静默WM_CHAR"
    if not app_utils.keyboard_ok(win):
        return False, "游戏不在前台，物理键盘不可用"
    win.activate()
    time.sleep(0.5)
    Keyboard.input(str(text))
    time.sleep(0.6)
    return True, "物理键盘"


def _yellow_count(img, rect):
    """帧内 rect 的黄色像素数（R>170 且 G>130 且 B<110；实测链接 255,255,0）。"""
    arr = np.asarray(img)[:, :, :3]
    h, w = arr.shape[:2]
    x0, y0 = max(int(rect[0]), 0), max(int(rect[1]), 0)
    x1, y1 = min(int(rect[2]), w), min(int(rect[3]), h)
    if x1 <= x0 or y1 <= y0:
        return 0
    r = arr[y0:y1, x0:x1, 0].astype(np.int16)
    g = arr[y0:y1, x0:x1, 1].astype(np.int16)
    b = arr[y0:y1, x0:x1, 2].astype(np.int16)
    return int(((r > 170) & (g > 130) & (b < 110)).sum())


def _yellow_text_bands(img, region):
    """在区域里找黄色文字带（黄色文字像素按行聚团），返回 [((cx,cy), rect)]。

    结果列表里唯一的黄色文字就是目的地链接（步骤行是绿/白字），因此
    直接按像素找带最稳：不依赖 OCR 行框（OCR 行框会与相邻行重叠导致
    黄色串行；链接行还可能因换行被拆成「送到大雁塔六层」）。
    """
    arr = np.asarray(img)[:, :, :3]
    h, w = arr.shape[:2]
    x0, y0 = max(int(region[0]), 0), max(int(region[1]), 0)
    x1, y1 = min(int(region[2]), w), min(int(region[3]), h)
    if x1 <= x0 or y1 <= y0:
        return []
    r = arr[y0:y1, x0:x1, 0].astype(np.int16)
    g = arr[y0:y1, x0:x1, 1].astype(np.int16)
    b = arr[y0:y1, x0:x1, 2].astype(np.int16)
    mask = (r > 170) & (g > 130) & (b < 110)
    rows = mask.sum(axis=1)
    bands = []
    in_band = False
    start = 0
    for i, cnt in enumerate(rows):
        if cnt >= 5 and not in_band:
            in_band, start = True, i
        elif cnt < 5 and in_band:
            in_band = False
            bands.append((start, i))
    if in_band:
        bands.append((start, len(rows)))
    out = []
    for a, bnd in bands:
        if not (6 <= bnd - a <= 32):   # 文字带高度（纯下划线/噪声太薄）
            continue
        cols = mask[a:bnd].sum(axis=0)
        xs = np.where(cols > 0)[0]
        if len(xs) == 0:
            continue
        width = int(xs[-1] - xs[0] + 1)
        if width < 40:
            continue
        cx = x0 + (int(xs[0]) + int(xs[-1])) // 2
        cy = y0 + (a + bnd) // 2
        out.append(((cx, cy), (x0 + int(xs[0]), y0 + a,
                               x0 + int(xs[-1]) + 1, y0 + bnd)))
    return out


def _rect_text(img, rect, pad=BAND_OCR_PAD, scale=BAND_OCR_SCALE):
    """裁 rect（外扩 pad、放大 scale 倍）区域 OCR，返回去空格文本。

    黄带/子串校验专用：区域引擎（max/736）对 ~80x20 的链接带实测
    ~30ms、1.000 读全（x1 放大在全帧引擎下会丢字，2x 稳定）。识别
    失败/引擎不可用返回 ""——调用方按「无法校验」保持几何兜底，不能
    因为校验不可用把入口卡死。
    """
    arr = np.asarray(img)
    h, w = arr.shape[:2]
    x0 = max(int(rect[0]) - pad, 0)
    y0 = max(int(rect[1]) - pad, 0)
    x1 = min(int(rect[2]) + pad, int(w))
    y1 = min(int(rect[3]) + pad, int(h))
    if x1 <= x0 or y1 <= y0:
        return ""
    crop = Image.fromarray(np.ascontiguousarray(arr[y0:y1, x0:x1, :3]))
    if scale != 1:
        crop = crop.resize((crop.width * scale, crop.height * scale),
                           Image.LANCZOS)
    try:
        items, _ = game_map.ocr_regions(
            crop, [(0, 0, crop.width, crop.height)])
    except Exception:  # noqa: BLE001 —— 校验失败按未识别处理
        return ""
    return "".join((t or "").replace(" ", "") for t, _r, _s in items)


def _dest_rect_in_line(text, rect, dest):
    """OCR 行内目的地子串的像素矩形（等宽字估算）；不含 dest 返回 None。"""
    t = (text or "").replace(" ", "")
    if not t or dest not in t:
        return None
    idx = t.find(dest)
    per = (rect[2] - rect[0]) / float(len(t))
    return (int(round(rect[0] + idx * per)), int(rect[1]),
            int(round(rect[0] + (idx + len(dest)) * per)), int(rect[3]))


def find_destination_link(img, lines, dest, region=None):
    """在寻路结果里找目的地链接，返回 (x, y) 或 None。

    用户规则（2026-11-20）：可点击文案一定**与目的地文案完全相同**
    （如「大雁塔六层」）且为**黄色**（下划线不要求）。步骤行末尾的
    黄色目的地名与独立链接行同样可点；正文里同名的白字、表头里的
    地名、整行黄色下划线都不是链接。

    长路线的每个步骤行都有一个黄色「…传送到 大雁塔X层」链接——同一
    屏多个同形状黄带，位置/形状判据分不出是哪层，**点击前一律读回
    文本校验**（2026-11-20 23:39 灵兽村→大雁塔四层真机：目的地行还
    没滚进可视区时，规则③把「大雁塔三层」黄带当成目的地，开出三层
    地图走错层）。读不出文本时保持旧的几何兜底。

    ① 含目的地的行（排除表头）→ 目的地子串估算区域黄色达标且读回
       文本含目的地才点（实测黄色 335~353 像素，白字/表头 0；阈值
       XUNLU_LINK_YELLOW_MIN）；
    ② 无黄色命中时退独立链接行文本（以「到/送到」开头、非表头/步骤、
       行宽 ≤220px）——绿色链接与颜色判定的兜底；
    ③ 再退独立链接行的形状特征（贴列表左缘的短黄带，OCR 没认出链接
       行时兜底：高≥12、宽 40..200px、左缘距列表左 ≤60px；2026-11-20
       21:47 灵兽村→大雁塔一层长路线首屏只有整行黄色下划线（宽 318）
       被旧逻辑误点，导致不再滚动），同样要求读回目的地文本。
    """
    for text, _cx, _cy, rect in lines:
        t = (text or "").replace(" ", "")
        if "共有" in t or "路径" in t:
            continue
        sub = _dest_rect_in_line(t, rect, dest)
        if sub is None:
            continue
        if _yellow_count(img, sub) < XUNLU_LINK_YELLOW_MIN:
            continue
        got = _rect_text(img, sub)
        if got and dest not in got:
            # 估算位置落在别的层链接上（OCR 行与相邻行串色/换行拼接）
            continue
        return ((sub[0] + sub[2]) // 2, (sub[1] + sub[3]) // 2)
    lowest = None
    for text, cx, cy, rect in lines:
        t = (text or "").replace(" ", "")
        if dest not in t:
            continue
        if any(k in t for k in ("共有", "路径", "走到", "传送到", "点击")):
            continue
        if not (t.startswith("到") or t.startswith("送到")):
            continue
        if rect[2] - rect[0] > 220:
            continue
        if lowest is None or cy > lowest[1]:
            sub = _dest_rect_in_line(t, rect, dest)
            lowest = (((sub[0] + sub[2]) // 2, (sub[1] + sub[3]) // 2)
                      if sub else (cx, cy))
    if lowest is not None:
        return lowest
    if region is not None:
        region_left = region[0]
        for pt, brect in reversed(_yellow_text_bands(img, region)):
            w = brect[2] - brect[0]
            h = brect[3] - brect[1]
            if h < 12 or not (40 <= w <= 200):
                continue
            if brect[0] - region_left > 60:
                continue
            got = _rect_text(img, brect)
            if got and dest not in got:
                # 别的层的链接（如「大雁塔三层」）：继续向上/滚动再找
                continue
            return pt
    return None


def _dao_rect_from_line(text, rect):
    """从 OCR 行里提取「到」标签矩形；不是「到」标签返回 None。

    OCR 会把「到」和两侧文字粘成一行：实测人在 大雁塔二层 时从字段
    显示层名，整行识别成「大雁塔二层到」（输入目的地图名后又变成
    「大雁塔二层到大雁塔一层」）——2026-11-20 21:08 大雁塔二层→一层
    诊断报「寻路面板未出现」的根因，只认行首「到」会漏。
    粘连时按等宽字推算「到」在行内的像素位置（中文 UI 等宽）。
    """
    t = (text or "").replace(" ", "")
    if not t or any(k in t for k in ("共有", "路径", "走到", "传送到", "点击")):
        return None
    if t == "到":
        return rect
    idx = t.find("到")
    if idx < 0:
        return None
    per = (rect[2] - rect[0]) / float(len(t))
    w = max(int(round(per)), 8)
    x0 = rect[0] + int(round(idx * per))
    return (x0, rect[1], min(x0 + w, rect[2]), rect[3])


def _find_xunlu_anchors(img, allow_ocr=True):
    """寻路面板锚点：到标签、搜索按钮、从标签 rect（缺失为 None）。

    优先「搜索」按钮模板判定面板在场并定位（面板打开 1.000，无面板
    0.15~0.24，~10ms）；面板内布局固定，到/从标签与列表区域按相对
    模板中心的固定偏移推算。模板未命中退回 OCR 锚点（先窄区域，缺失
    再放宽到 XUNLU_PANEL_WIDE_REGION；兼容按钮悬停高亮等渲染差异）。
    allow_ocr=False：只做模板判定（等面板出现的轮询热路径，避免每轮
    对无面板画面付 OCR 成本）。
    """
    if img is None:
        return None, None, None
    score, pt = game_map.best_match_np(game_map.bgr_frame(img),
                                       XUNLU_SEARCH_ICON,
                                       region=XUNLU_PANEL_WIDE_REGION)
    if score >= XUNLU_SEARCH_THRESHOLD:
        cx, cy = int(pt[0]), int(pt[1])

        def _rect(off):
            return (cx + off[0], cy + off[1], cx + off[2], cy + off[3])

        return (_rect(XUNLU_DAO_OFF), _rect(XUNLU_SEARCH_OFF),
                _rect(XUNLU_FROM_OFF))
    if not allow_ocr:
        return None, None, None
    dao = search = from_rect = None
    for region in (XUNLU_PANEL_TOP_REGION, XUNLU_PANEL_WIDE_REGION):
        items, _ = game_map.ocr_regions(img, [region])
        dao = search = from_rect = None
        for text, rect, score in items:
            t = (text or "").replace(" ", "")
            if from_rect is None and t.startswith("从"):
                from_rect = rect
            # 排除地图窗口的「高级搜索」按钮（不是寻路面板的搜索）
            if search is None and "搜索" in t and "高级" not in t:
                search = rect
                continue
            if dao is None:
                # 同一行可能同时是「从…」（从字段文字+到 粘行）
                dao = _dao_rect_from_line(t, rect)
        if dao is not None and search is not None:
            break
    return dao, search, from_rect


def _xunlu_list_region(dao, search, from_rect):
    """由面板锚点推结果列表 OCR 区域（不依赖固定坐标）。"""
    left = (from_rect[0] if from_rect is not None else dao[0] - 40) - 5
    top = search[3] + 12
    right = search[2] + 8
    bottom = top + XUNLU_LIST_HEIGHT
    return (max(left, 0), max(top, 0), min(right, 1378), min(bottom, 831))


def _close_xunlu_panel_if_open(win):
    """关闭残留的「寻路」面板（面板 X = 深色圆钮，实测中心 (866,335)，
    即搜索按钮右缘 +12、上缘 -15——多帧深色像素质心一致；旧公式
    「搜索右缘-4」点偏左 8px 会落在圆钮外，面板关不掉，2026-11-20
    22:13 真机教训）。

    必须同时命中「到」标签与「搜索」按钮才算面板在场——地图窗口的
    「高级搜索」按钮在 OCR 里也含「搜索」，单看它会误点击（可能点到
    地图触发走动）。返回是否点击了关闭。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    dao, search, _from = _find_xunlu_anchors(img)
    if dao is None or search is None:
        return False
    human_click(win, search[2] + 12, search[1] - 15)
    time.sleep(0.4)
    return True


def _read_map_window_title(img):
    """读地图窗口右侧竖排标题（如「大雁塔四层」），未识别返回 ""。

    地图窗口把地图名竖排画在右侧（2026-11-20 实测「大雁塔四层」
    rect (972,258)-(1005,416)、置信度 1.000）。横排的相邻层入口标签
    （「大雁塔三/五层」，宽矩形 h<2w）与两侧竖排诗句（不匹配层名
    正则）均排除。
    """
    items, _ = game_map.ocr_regions(img, [MAP_TITLE_REGION])
    for text, rect, score in items:
        if score < 0.9:
            continue
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w <= 0 or h < 2 * w:
            continue
        t = (text or "").replace(" ", "")
        if MAP_TITLE_RE.match(t):
            return t
    return ""


def _open_xunlu_destination(win, dest, emit):
    """经世界地图「寻路」面板打开 dest（大雁塔X层）的地图窗口。

    2026-11-20 实机验证：世界地图底部「寻路」→ 面板 [从当前图]
    [到输入框][搜索] → 在到输入框输入目标层（面板每次打开字段为空，
    无清空问题）→ 搜索 → 结果列表用静默滚轮滚到底 → 点黄色「到X层」
    链接 → 目的地地图窗口打开（随后走通用悬停标定/点击/到达）。
    返回 (ok, info)；各步骤 emit 带累计耗时（入口阶段定位用）。
    """
    t0 = time.time()
    ok, info = game_map.open_world_map(win)
    if not ok:
        return False, f"打开世界地图失败：{info}"
    _close_xunlu_panel_if_open(win)  # 残留面板先关（实测不会自动关）
    emit(f"世界地图已打开（{time.time() - t0:.1f}s）")
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败（窗口可能已最小化）"
    btn = None
    for region in (XUNLU_BTN_REGION, XUNLU_BTN_WIDE_REGION):
        items, _ = game_map.ocr_regions(img, [region])
        for text, rect, score in items:
            if "寻路" in (text or ""):
                btn = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                break
        if btn is not None:
            break
    if btn is None:
        return False, "世界地图未找到「寻路」按钮"
    emit(f"点击寻路按钮 @({btn[0]},{btn[1]})（{time.time() - t0:.1f}s）")
    human_click(win, btn[0], btn[1])
    # 面板渲染实测 ~1s：轮询代替固定 0.8s 等待，出现即返回。前 2s 只做
    # 模板判定（~10ms/次）；之后带 OCR 兜底（兼容按钮悬停高亮等渲染差异）
    dao = search = from_rect = None
    for i in range(16):
        _check_cancel()
        time.sleep(0.2)
        img = win.capture(force_activate=False)
        if img is not None:
            dao, search, from_rect = _find_xunlu_anchors(
                img, allow_ocr=(i >= 10))
            if dao is not None and search is not None:
                break
    if dao is None or search is None:
        return False, "寻路面板未出现"
    emit(f"寻路面板就绪（{time.time() - t0:.1f}s）")
    list_region = _xunlu_list_region(dao, search, from_rect)
    field_x = dao[2] + 30
    field_y = (dao[1] + dao[3]) // 2
    human_click(win, field_x, field_y)
    time.sleep(0.3)
    ok_t, ch = _type_text(win, dest)
    if not ok_t:
        return False, f"输入目的地失败：{ch}"
    time.sleep(0.2)
    human_click(win, (search[0] + search[2]) // 2,
                (search[1] + search[3]) // 2)
    t_search = time.time()
    link = None
    scrolls = 0
    prev_text = None
    for _ in range(XUNLU_SCROLL_STEPS):
        # 结果渲染实测 <0.5s：不固定先等 1s，短轮询到链接出现即停
        _check_cancel()
        time.sleep(0.3)
        img = win.capture(force_activate=False)
        if img is not None:
            items, _ = game_map.ocr_regions(img, [list_region])
            lines = _merge_ocr_lines(items)
            link = find_destination_link(img, lines, dest, region=list_region)
            if link is not None:
                break
            # 目的地一定在列表最下面（用户 2026-11-20 规则）：滚过一次
            # 后列表文本不再变化 = 已到底，再滚也找不到，提前失败
            text_now = "|".join(t for t, _cx, _cy, _r in lines)
            if scrolls and text_now == prev_text:
                break
            prev_text = text_now
        app_utils.human_scroll(win, XUNLU_SCROLL_POINT[0],
                               XUNLU_SCROLL_POINT[1],
                               -XUNLU_WHEEL_NOTCHES)
        scrolls += 1
    if link is None:
        return False, f"结果列表未找到「到{dest}」链接"
    emit(f"点击目的地链接「到{dest}」@({link[0]},{link[1]})"
         f"（搜索+滚动 {scrolls} 次 {time.time() - t_search:.1f}s，"
         f"累计 {time.time() - t0:.1f}s）")
    human_click(win, link[0], link[1])
    # 地图窗口出现即返回（模板命中 8-11ms，0.2s 轮询；无窗口画面不跑
    # OCR）。前 ~2.4s 只做模板判定，之后带 OCR 兜底（模板缺失/损坏时
    # 这条特殊入口不至于永远失败）
    for i in range(20):
        _check_cancel()
        time.sleep(0.2)
        if locate_search_bar(win, allow_ocr=(i >= 12)) is not None:
            # 用户 2026-11-20：寻路面板不会自动关闭，残留会挡后续操作
            _close_xunlu_panel_if_open(win)
            # 标题核对：开出来不是目的地这一层就立刻失败，不能带着错图
            # 去点坐标（2026-11-20 23:39 四层误点三层 → 在三层地图上
            # 点了 (107,37)，角色走错层）
            img2 = win.capture(force_activate=False)
            title = _read_map_window_title(img2) if img2 is not None else ""
            if title and title != dest:
                game_map.close_maps(win, quiet=True)
                emit(f"点开的链接是「{title}」地图，不是「{dest}」，已关图")
                return False, f"寻路链接点成「{title}」（目标「{dest}」）"
            emit(f"目的地地图已打开（入口总耗时 {time.time() - t0:.1f}s）")
            return True, "目的地地图已打开"
    _close_xunlu_panel_if_open(win)
    return False, "点链接后目的地地图窗口未出现"


# ---- 预走路（2026-11-20 用户建议）----
# 开图/寻路面板阶段角色原地等待（大雁塔入口实测 ~4.4s）。出发地图配置
# 一个「先走起来」的小地图点位：开图前先开小地图点一下，让角色在这段
# 时间里赶路；目的地地图打开后再点最终目标点自然改道。
# 点位用**地图坐标**（2026-11-21 坐标化，原固定像素 (317,616) 的标定
# 反算值）：灵兽村 (14,13) 即冰熊守卫出口（出村去冰窟/北俱芦洲/各大城市
# 的必经点），实机验证点后角色立即起步（(103,77) → (73,54)/2.4s）。
# 点击像素由该地图标定换算（与导航同一套 affine）；无标定/越界跳过预走。
# **仅修罗任务启用**（2026-11-21 用户规则）：从灵兽村出发去做五环等
# 其他任务不需要预走——预走点是为修罗出村赶路设计的优化。
# 同图导航不预走；未配置的地图跳过。
PREWALK_POINTS = {"灵兽村": (14, 13)}
PREWALK_TASK_MODES = ("修罗",)


def _open_mini_map_quick(win, timeout=1.2):
    """打开小地图（预走路专用）：点位置区域 + 短轮询，失败重试一次。

    不用 map.open_mini_map 的 3s 等待——预走路是可选优化，打不开要
    快速跳过，不能反过来拖慢导航。已打开则直接用。
    """
    cx = (game_map.MAP_POS_REGION[0] + game_map.MAP_POS_REGION[2]) // 2
    cy = (game_map.MAP_POS_REGION[1] + game_map.MAP_POS_REGION[3]) // 2
    for _ in range(2):
        _check_cancel()
        if game_map.is_mini_map_open(win):
            return True
        human_click(win, cx, cy)
        deadline = time.time() + timeout
        while time.time() < deadline:
            _check_cancel()
            time.sleep(0.25)
            if game_map.is_mini_map_open(win):
                return True
    return False


def _prewalk_if_configured(win, pos, base, layer, emit):
    """出发地图有预走点且目标不同图时：开小地图点一下让角色先走。

    返回预走耗时秒数（0=未执行/失败，视为原地等待，不影响后续导航）。
    """
    if pos is None:
        return 0.0
    cur = pos[0].strip()
    pt = PREWALK_POINTS.get(cur)
    if pt is None:
        return 0.0
    if layer is not None and cur == layer:
        return 0.0
    if layer is None and names_match(cur, base):
        return 0.0
    mode = app_utils.current_task_mode()
    if mode not in PREWALK_TASK_MODES:
        return 0.0  # 非修罗任务（如五环/五倍）不预走
    # 坐标 → 小地图像素（与主导航同一套标定）：无标定/换算越界跳过预走，
    # 不按旧固定像素硬点（窗口尺寸/布局变化时只会点错位置）
    affine = affine_for(win, cur)
    if affine is None:
        emit(f"预走路跳过：{cur}无标定（不影响导航）")
        return 0.0
    px = affine["ax"] + affine["sx"] * pt[0]
    py = affine["ay"] + affine["sy"] * pt[1]
    if not (0 <= px <= 1377 and 0 <= py <= 830):
        emit(f"预走路跳过：{cur}坐标 {pt} 换算点 "
             f"({px:.0f},{py:.0f}) 超出窗口")
        return 0.0
    t0 = time.time()
    if not _open_mini_map_quick(win):
        emit("预走路跳过：小地图未打开（不影响导航）")
        return 0.0
    human_click(win, int(px), int(py))
    # 快速关小地图：刚由本流程打开、无自动关闭动画风险，不等 1s 确认；
    # 世界地图流程需要它先关（窗口重叠）
    game_map.close_map_if_open(win, game_map.MINI_MAP_OPEN_ICON, "小地图",
                               primary_button="primary",
                               match_region=game_map.MINI_MAP_REGION,
                               confirm_wait=0.0, quiet=True)
    dt = time.time() - t0
    emit(f"预走路：{cur}坐标 {pt} → 小地图点 ({int(px)},{int(py)})"
         f" → 角色先走起来（{dt:.1f}s）")
    return dt


def _arrived(pos, target, layer, x, y):
    """位置是否已到达目标（层级图按层名精确匹配）。"""
    if pos is None:
        return False
    name_ok = (pos[0].strip() == layer) if layer \
        else names_match(pos[0], target)
    return bool(name_ok and abs(pos[1] - x) <= ARRIVE_TOLERANCE
                and abs(pos[2] - y) <= ARRIVE_TOLERANCE)


def _wait_arrival(win, target, layer, x, y, emit, verbose=False,
                  allow_restart=False):
    """轮询 HUD 位置直到到达目标地图坐标 ±ARRIVE_TOLERANCE。

    位置连续 ARRIVE_STALL_TIMEOUT 秒不变 → 提前判失败（目标点不可达
    时客户端会站在最近可达点不动）。allow_restart=True 时改为 5s 停滞
    就返回 ARRIVE_RESTART_TEXT，由调用方重新开图补一次寻路。

    verbose=False（生产）：位置日志只在地名变化（过场景）时输出，
    避免行走期每 1s 一条刷屏；诊断卡片传 verbose=True 保留逐次位置。
    """
    deadline = time.time() + ARRIVE_TIMEOUT
    last = None
    last_name = None
    last_change = time.time()
    while time.time() < deadline:
        _check_cancel()
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            # 途中遇战斗：坐标不可读、人物不动——冻结停滞计时并把战斗
            # 时间从总超时里扣掉（战斗线程自动打完，之后继续等寻路）
            last_change = time.time()
            deadline += ARRIVE_POLL_INTERVAL
            time.sleep(ARRIVE_POLL_INTERVAL)
            continue
        pos = game_map.read_map_position(None, img=img) if img is not None else None
        if pos is not None and pos != last:
            name = str(pos[0]).strip()
            if verbose or name != last_name:
                emit(f"位置: {pos}")
            last_name = name
            last = pos
            last_change = time.time()
        if pos is not None and _arrived(pos, target, layer, x, y):
            return True, (f"到达 {pos[0]} ({pos[1]},{pos[2]})，"
                          f"误差 ({pos[1] - x:+d},{pos[2] - y:+d})")
        if allow_restart and pos is not None \
                and time.time() - last_change > ARRIVE_RESTART_STALL:
            return False, ARRIVE_RESTART_TEXT
        if pos is not None and time.time() - last_change > ARRIVE_STALL_TIMEOUT:
            near = (abs(pos[1] - x) <= ARRIVE_TOLERANCE
                    and abs(pos[2] - y) <= ARRIVE_TOLERANCE)
            hint = ""
            if near:
                # 坐标已到但地名对不上：提示给排障用（如别名缺失）
                hint = (f"（坐标已在 ±{ARRIVE_TOLERANCE} 内，但地名不匹配："
                        f"HUD「{pos[0]}」目标「{layer or target}」）")
            return False, (f"位置 {ARRIVE_STALL_TIMEOUT:.0f}s 未变化"
                           f"（目标点可能不可达），最后位置 {last}{hint}")
        time.sleep(ARRIVE_POLL_INTERVAL)
    return False, f"超时未到达（最后位置 {last}）"


def _open_target_map(win, base, layer, pos, emit, timing=None):
    """打开目标地图窗口：普通图经世界地图标签；层级图点标签→层块进层；
    大雁塔走世界地图「寻路」面板（不能经标签进入，用户 2026-11-20 规则）。

    timing：可选 dict，写入分阶段耗时（当前只有 layer_entry=点层块后
    等待进入目标层的秒数——点层块可能触发逐层步行，实测 19-30s，
    这部分属于「走路」不计入开图）。
    """
    closed = game_map.close_maps(win, quiet=True)
    _check_cancel()
    if closed:
        time.sleep(0.3)  # 只有真的关了地图才等关闭动画（原来恒等 0.3s）
    if layer is not None:
        if pos is not None and pos[0].strip() == layer:
            ok, info = game_map.open_mini_map(win)
            if not ok:
                return False, f"打开小地图失败：{info}"
            return True, "层内地图已打开"
        if base == "大雁塔":
            return _open_xunlu_destination(win, layer, emit)
        t0 = time.time()
        ok, info = game_map.open_world_map(win)
        if not ok:
            return False, f"打开世界地图失败：{info}"
        _check_cancel()
        ok, info = game_map.click_world_map_label(win, base)
        if not ok:
            return False, f"点击「{base}」标签失败：{info}"
        label_pt = _wait_layer_tile(win, base, layer, 3.0)
        _check_cancel()
        if label_pt is None:
            # 层块页未出现：再点一次标签（渲染/动画竞态）
            game_map.click_world_map_label(win, base)
            label_pt = _wait_layer_tile(win, base, layer, 4.0)
        if label_pt is None:
            return False, f"层块页未找到「{layer}」"
        click_y = label_pt[1] + LAYER_TILE_LABEL_DY
        emit(f"点击层块「{layer}」标题@({label_pt[0]},{label_pt[1]})"
             f"→本体@({label_pt[0]},{click_y})"
             f"（世界地图+层块页识别 {time.time() - t0:.1f}s）")
        t_tile = time.time()
        human_click(win, label_pt[0], click_y)
        if not _wait_map_name(win, layer, LAYER_ARRIVE_TIMEOUT, emit):
            return False, f"{LAYER_ARRIVE_TIMEOUT:.0f}s 内未进入「{layer}」"
        entry_dt = time.time() - t_tile
        if timing is not None:
            timing["layer_entry"] = entry_dt
        emit(f"已进入「{layer}」（进层 {entry_dt:.1f}s）")
        game_map.close_maps(win, quiet=True)
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, f"进入「{layer}」后打开小地图失败：{info}"
        return True, "层内地图已打开"
    if not (pos is not None and names_match(pos[0], base)):
        ok, info = game_map.open_mini_map_by_name(win, base)
        if not ok:
            ok, info = game_map.open_mini_map_by_name(win, base)
        if not ok:
            return False, f"打开「{base}」地图失败：{info}"
        _check_cancel()
    else:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, f"打开小地图失败：{info}"
    return True, "地图已打开"


def navigate_to_map_coordinate(win, target, x, y, log_fn=None, verbose=False,
                               on_walk_start=None):
    """导航到 target 地图 (x, y)：打开地图 → 悬停标定 → 校验点击 → 到达。

    target：WORLD_MAP_LABELS 地图名（经 resolve 归一）或层级图全名
    （龙窟一层…凤巢七层）。返回 (ok, info)，过程经 log_fn 输出。
    每个关键阶段 emit 一行累计/分段耗时，最终 info 附完整耗时汇总。
    verbose=True（诊断卡片）逐次输出行走位置；生产默认只在地名变化
    （过场景）时输出，避免每 1s 一条刷屏。
    on_walk_start：可选无参回调，位移启动确认后、到达等待前调用一次
    （行走期插队任务，如修罗手动寻路的组号队员血蓝 pass——与 follow
    点任务绿字后的寻路期 pass 对齐）。回调异常只记日志、不影响导航；
    停滞重启会再走一遍流程，回调可能被调用第二次（调用方自行节流）。
    行走中位置停滞 5s（寻路被点世界/其它操作打断）会自动重新开图补点
    一次；主动 request_cancel() 中断（诊断「中断寻路」）不补——那是用户
    要停：各轮询循环每轮检查（1-3s 生效），中断时关地图/寻路面板并返回
    (False, "已中断寻路")。注意：人物身上的客户端自动寻路不会因此停止，
    仍会走到最后一次点击的目标点。
    """
    emit = log_fn or (lambda m: log(f"[地图导航] {m}", "INFO"))
    _CANCEL.clear()
    try:
        ok, info = _navigate_impl(win, target, x, y, emit, verbose=verbose,
                                  allow_restart=True,
                                  on_walk_start=on_walk_start)
        if not ok and info == ARRIVE_RESTART_TEXT:
            # 人物原地站了 5s：多半是客户端寻路被打断（点世界/其它操作），
            # 重新走一遍开图 + 点目标点补一次；补过仍不行按失败返回
            emit("位置 5s 未变化（寻路可能被打断）——重新开图补一次")
            ok, info = _navigate_impl(win, target, x, y, emit,
                                      verbose=verbose, allow_restart=False,
                                      on_walk_start=on_walk_start)
        return ok, info
    except NavigationCancelled:
        emit("已中断寻路（用户手动中断），关闭地图/寻路面板")
        try:
            # 先关地图再关面板：面板可能被目的地地图窗口遮住
            game_map.close_maps(win, quiet=True)
            _close_xunlu_panel_if_open(win)
        except Exception:  # noqa: BLE001 —— 中断收尾不得再抛
            pass
        return False, "已中断寻路"
    except BaseException:
        # 非预期异常（含 UserAbort 等 BaseException）：清掉可能打开的地图/
        # 寻路面板再原样抛出——残留面板会吃掉后续流程的点击（2026-11-20
        # 审查发现：此前只有取消/预期失败路径收尾）
        try:
            game_map.close_maps(win, quiet=True)
            _close_xunlu_panel_if_open(win)
        except Exception:  # noqa: BLE001 —— 收尾不得掩盖原始异常
            pass
        raise


def site_coord_nav_enabled():
    """任务点位坐标化寻路开关（通用设置，默认开启）。

    关闭时各任务回滚为原固定小地图像素点击逻辑。任务侧分支统一调用
    本函数（测试可 monkeypatch map_nav.site_coord_nav_enabled）。
    """
    from ..core.settings import load_site_coord_nav
    return load_site_coord_nav()


def start_navigation(win, target, x, y, log_fn=None, on_map_open=None):
    """坐标化寻路（只开图/标定/点击，不等到达）：任务点位迁移用。

    与 navigate_to_map_coordinate 前半段一致：同图直开小地图/跨图经
    世界地图标签 → 搜索条定位 → 标定换算 → 悬停校验 → 点击地图点。
    点击成功后**地图保持打开**，由调用方按原逻辑决定关闭时机并等待到达
    （保留各任务自己的「附近」判据：按钮/NPC/对话框/位置消失）。
    失败/中断路径清理残留地图与寻路面板。
    on_map_open：目标地图打开后、点击前回调（领取流程读小地图「任务」
    筛选状态等）。回调异常只记日志、不影响寻路。
    返回 (ok, info, point)。
    """
    emit = log_fn or (lambda m: log(f"[地图导航] {m}", "INFO"))
    _CANCEL.clear()
    try:
        ok, info, ctx = _start_impl(win, target, x, y, emit,
                                    on_map_open=on_map_open)
        if not ok:
            return False, info, None
        return True, f"已点击目标点 {ctx['point']}，开始寻路", ctx["point"]
    except NavigationCancelled:
        emit("已中断寻路（用户手动中断），关闭地图/寻路面板")
        try:
            # 先关地图再关面板：面板可能被目的地地图窗口遮住
            game_map.close_maps(win, quiet=True)
            _close_xunlu_panel_if_open(win)
        except Exception:  # noqa: BLE001 —— 中断收尾不得再抛
            pass
        return False, "已中断寻路", None
    except BaseException:
        # 非预期异常：清掉可能打开的地图/寻路面板再原样抛出
        try:
            game_map.close_maps(win, quiet=True)
            _close_xunlu_panel_if_open(win)
        except Exception:  # noqa: BLE001 —— 收尾不得掩盖原始异常
            pass
        raise


def _start_impl(win, target, x, y, emit, on_map_open=None):
    """开图 + 标定 + 点击前半段（start_navigation / _navigate_impl 共用）。

    成功时地图保持打开（调用方决定关闭时机），返回 (True, "", ctx)；
    失败已清理残留地图（寻路面板不会自动关闭，必须统一收尾）。
    ctx：base/layer/point/t_start/t_map/t_bar/t_click/open_dt/prewalk_dt/
    layer_entry/xunlu_entry（大雁塔跨层走寻路面板：面板被目的地地图窗口
    遮住，需等点击起点、位移启动后先关地图再补关面板）。
    """
    t_start = time.time()

    def _cleanup():
        game_map.close_maps(win, quiet=True)
        _close_xunlu_panel_if_open(win)

    base, layer = parse_navigate_target(target)
    if layer is None:
        resolved = game_map.resolve_world_map_name(base)
        if resolved is None:
            return False, f"地图「{target}」未收录世界地图标签", None
        base = resolved
    pos = _read_pos(win)
    emit(f"目标 {layer or base} ({x},{y})；起点位置 {pos}")
    # 大雁塔跨层走「世界地图→寻路」面板（与 _open_target_map 同一判据）：
    # 面板在目的地地图窗口打开时被遮住、点不到 X，需等地图窗口收起后补关
    # （2026-11-21 实机：面板一直留到行走/战斗中，见 _navigate_impl）。
    xunlu_entry = (layer is not None and base == "大雁塔"
                   and not (pos is not None and pos[0].strip() == layer))
    prewalk_dt = _prewalk_if_configured(win, pos, base, layer, emit)

    phases = {}
    ok, info = _open_target_map(win, base, layer, pos, emit, phases)
    if not ok:
        # 寻路面板不会自动关闭（用户 2026-11-20）：失败路径也清理残留
        _cleanup()
        emit(f"耗时: 打开目标地图失败（总计 {time.time() - t_start:.1f}s）")
        return False, info, None
    _check_cancel()  # 开图期间收到中断：不再继续标定/点击
    # 任务筛选统一取消勾选（用户 2026-11-21）：仅名望快捷领取需要勾选
    # 任务筛选点黄色任务名；修罗/五倍/五环/修理等点位都是普通地形点，
    # 勾选时黄色任务名/任务点可能与点击点重叠，触发自动寻路或误开对话框。
    try:
        ok_f, info_f = game_map.uncheck_minimap_task_filter(win)
    except Exception as e:  # noqa: BLE001 —— 辅助检查不得阻断寻路
        ok_f, info_f = False, f"任务筛选检查异常: {e}"
    if not ok_f:
        emit(f"任务筛选: {info_f}")
    if on_map_open is not None:
        try:
            on_map_open()
        except Exception as e:  # noqa: BLE001 —— 回调异常不影响寻路
            emit(f"开图回调异常: {e}")
    t_map = time.time()
    layer_entry = phases.get("layer_entry", 0.0)
    open_dt = (t_map - t_start) - layer_entry
    extra = []
    if prewalk_dt > 0:
        extra.append(f"含预走路 {prewalk_dt:.1f}s")
    if layer_entry > 0:
        extra.append(f"进层等待 {layer_entry:.1f}s——层块点击触发步行，"
                     f"计入行走")
    suffix = ("、" + "、".join(extra)) if extra else ""
    emit(f"目标地图已打开（开图 {open_dt:.1f}s{suffix}）")

    bar = locate_search_bar(win)
    if bar is None:
        for _ in range(4):
            time.sleep(0.5)
            bar = locate_search_bar(win)
            if bar is not None:
                break
    if bar is None:
        _cleanup()
        emit(f"耗时: 地图窗口未出现（总计 {time.time() - t_start:.1f}s）")
        return False, "地图窗口未出现搜索条（无法定位地图窗口）", None
    t_bar = time.time()
    emit(f"搜索条锚点: ({bar['x']},{bar['y']})"
         f"（定位 {t_bar - t_map:.1f}s）")

    key = layer or base
    affine = affine_for(win, key)
    point = None
    err = ""

    def _cache_corrected(aff):
        """偏差修正后的标定写入会话缓存：同图下次导航一次命中。"""
        remember_affine(win, key, aff)
        emit("标定已按悬停校验修正并缓存（同图后续导航直接命中）")

    if affine is not None:
        emit("使用内置/会话标定（悬停校验）")
        ok, point, err = _aim_and_click(win, affine, None, x, y, emit,
                                        on_affine_corrected=_cache_corrected)
        if not ok:
            emit(f"标定未通过校验（{err}），回退完整采样")
            affine = None
    if affine is None:
        t_sample = time.time()
        samples = _collect_hover_samples(win, bar, emit)
        if len(samples) < 2:
            _cleanup()
            emit(f"耗时: 悬停采样不足（总计 {time.time() - t_start:.1f}s）")
            return False, f"悬停采样不足（{len(samples)} 个有效点，浮层未出现）", None
        affine = solve_affine(samples)
        if affine is None:
            _cleanup()
            emit(f"耗时: 标定求解失败（总计 {time.time() - t_start:.1f}s）")
            return False, "悬停样本无法解出线性变换（两轴比例异常）", None
        remember_affine(win, key, affine)
        emit(f"标定: 比例 {affine['sx']:.3f}/{affine['sy']:.3f} px/格，"
             f"原点 ({affine['ax']:.1f},{affine['ay']:.1f})"
             f"（采样+求解 {time.time() - t_sample:.1f}s）")
        ok, point, err = _aim_and_click(win, affine,
                                        (samples[0][0], samples[0][1]),
                                        x, y, emit,
                                        on_affine_corrected=_cache_corrected)
    if not ok or point is None:
        _cleanup()
        emit(f"耗时: 瞄准点击失败（总计 {time.time() - t_start:.1f}s）")
        return False, err, None
    assert point is not None  # 类型收窄：ok=True 时 point 非 None
    t_click = time.time()
    emit(f"点击地图点 {point} 开始寻路"
         f"（标定+瞄准点击 {t_click - t_bar:.1f}s）")
    return True, "", {
        "base": base, "layer": layer, "point": point,
        "t_start": t_start, "t_map": t_map, "t_bar": t_bar,
        "t_click": t_click, "open_dt": open_dt,
        "prewalk_dt": prewalk_dt, "layer_entry": layer_entry,
        "xunlu_entry": xunlu_entry,
    }


def _navigate_impl(win, target, x, y, emit, verbose=False, allow_restart=False,
                   on_walk_start=None):
    """导航主流程（navigate_to_map_coordinate 的实现，emit 已就绪）。"""
    def _cleanup():
        """统一收尾：先关地图（寻路面板可能被地图窗口遮住），再关面板。"""
        game_map.close_maps(win, quiet=True)
        _close_xunlu_panel_if_open(win)

    ok, info, ctx = _start_impl(win, target, x, y, emit)
    if not ok:
        return False, info
    base = ctx["base"]
    layer = ctx["layer"]
    point = ctx["point"]
    t_start = ctx["t_start"]
    t_map = ctx["t_map"]
    t_bar = ctx["t_bar"]
    t_click = ctx["t_click"]
    open_dt = ctx["open_dt"]
    prewalk_dt = ctx["prewalk_dt"]
    layer_entry = ctx["layer_entry"]
    xunlu_entry = ctx.get("xunlu_entry", False)

    before = _read_pos(win)
    if not _wait_movement(win, before):
        if _arrived(_read_pos(win), base, layer, x, y):
            _cleanup()
            emit(f"耗时: 已在目标附近，总计 {time.time() - t_start:.1f}s")
            return True, "已到达目标附近（无需移动）"
        emit("点击后无位移（可能点到飞行旗等无效点），偏移重试")
        human_click(win, min(point[0] + CLICK_OFFSET_RETRY, 1377), point[1])
        _wait_movement(win, before)
    emit(f"位移启动（点击后 {time.time() - t_click:.1f}s）")
    if xunlu_entry:
        # 大雁塔特殊入口：开图阶段目的地地图窗口压在「寻路」面板上，面板
        # 在场判定看不到内容、X 点不到（2026-11-21 实机：面板一直留到
        # 行走/战斗中才被看见）。起点点击已完成、人物已起步，此时地图
        # 窗口不再需要（客户端可能已自动收起）——先关地图让面板露出，
        # 再补关面板，避免行走与战斗期间一直挡屏。
        game_map.close_maps(win, quiet=True)
        if _close_xunlu_panel_if_open(win):
            emit("已关闭残留「寻路」面板")
    if on_walk_start is not None:
        # 行走期插队回调（修罗手动寻路 → 组号队员血蓝 pass）：人物已在走，
        # 回调期间客户端寻路继续；异常只记日志，不拖垮导航
        try:
            on_walk_start()
        except Exception as e:  # noqa: BLE001 —— 回调异常不影响导航
            emit(f"行走起步回调异常: {e}")

    ok, info = _wait_arrival(win, base, layer, x, y, emit,
                             verbose=verbose, allow_restart=allow_restart)
    t_end = time.time()
    parts = [f"开图 {open_dt:.1f}s"
             + (f"（含预走路 {prewalk_dt:.1f}s）" if prewalk_dt > 0 else "")]
    if layer_entry > 0:
        parts.append(f"进层等待 {layer_entry:.1f}s")
    parts += [f"搜索条定位 {t_bar - t_map:.1f}s",
              f"标定+瞄准点击 {t_click - t_bar:.1f}s",
              f"行走到达 {t_end - t_click:.1f}s",
              f"总计 {t_end - t_start:.1f}s"]
    timing = "、".join(parts)
    emit(f"耗时: {timing}")
    _cleanup()  # 同图行走地图不自动关 + 寻路面板不会自动关闭，统一收尾
    return ok, f"{info}；耗时：{timing}"
