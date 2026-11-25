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
  画面让角色乱走）；目标像素被地图图标/NPC 名盖住（浮层不出）时，
  在容差半径内找可读邻居再校验点击（见 _probe_hover_within_tolerance）
- 点击地图点 = 开始寻路（同图/跨图均可，跨图由客户端自动过图）；
  点击后 6s 内无位移按点到飞行旗等无效点处理，偏移 14px 重试一次
- 到达判据：HUD 地名 + 坐标 ±ARRIVE_TOLERANCE；位置 12s 不变提前
  判失败（目标不可达时客户端会站在最近可达点）

关于搜索框（坐标输入 + 红环）：客户端会保留上次搜索值，实测退格 /
Ctrl+A / 拖选均无法清空输入框，输入会追加成错误坐标——不作为生产
路径（机制见记忆 map-coord-search-red-ring）。
"""
import base64
import heapq
import json
import math
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from ascript.windows.hardware.keyboard import Keyboard

from ..core import app_utils
from ..core.app_utils import human_click, log
from . import map as game_map
from .battle import is_in_battle_frame, watch_in_battle  # 途中遇战斗冻结到达/进层计时


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
# ---- 标定修正的角色持久化（2026-11-22 用户需求）----
# 会话缓存跨进程重启即丢：内置表按旧窗口几何采集，窗口被拖动/缩放后
# 首个导航要「瞄准→修正→再瞄准」多付 0.8~1.6s（如宝象国 19:00 锚点
# (460,217)→(479,229)，首瞄差 5 格）。修正/采样得到的标定写角色数据
# data/<角色>/map_nav_affines.json（布局 → 地图 → 解），重启后直接命中；
# 每次导航仍保留悬停校验，持久化值过期时会自动修正并刷新。
# 文件格式：{"version":1,"layouts":{"1378x831":{"宝象国":{"sx","sy","ax","ay","at"}}}}
AFFINE_CACHE_VERSION = 1
_PERSISTED_AFFINES = None
_PERSISTED_AFFINES_MTIME = None

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

# ---- 特殊地图（世界地图无标签、进入后无小地图的洞府场景）----
# 用户 2026-11-23 实机规则（莲花洞）：
# - 世界地图上没有该地名，客户端「寻路」面板也报「没有找到目标场景」；
# - 进入后没有小地图（Alt+1/点位置区域打不开场景地图），内部坐标
#   （如 莲花洞 75,21）无法按图悬停点击；
# - 进入方式一：先到父地图入口坐标（平顶山 102,42），走近入口光圈即
#   自动进入（实测 [103,42] 触发场景切换）；
# - 进入方式二：打开父地图场景地图、勾选「普通」筛选后，点该地图黄字
#   地名（莲花洞）下方的入口 NPC 绿字名（金角大王/银角大王），实测从
#   [79,10] 自动寻路约 7s 到 [103,42] 并自动进入。
# 进入后洞内无小地图、不能按图点击；用「右键点地 → 读 HUD 坐标」小步
# 自校准步行到目标坐标（±3 格即可，见 _walk_special_coordinate），不依赖
# 角色名字（人多时名字 OCR 不可靠，用户 2026-11-23 规则）。新增洞府在此追加。
SPECIAL_MAP_ENTRIES = {
    "莲花洞": {
        "parent": "平顶山",
        "entry": (102, 42),
        "npc_anchor": "莲花洞",  # 场景地图上的黄字地名（其下方为入口 NPC 绿字）
        # 洞内入口/出口光圈（进洞落点；走过/走进会退回平顶山）。
        # 2026-11-24 实机：路线从 (90,6) 直穿 (84,9) 附近 → 直接被传送出洞。
        "portal": (84, 9),
    },
    "波月洞": {
        "parent": "平顶山",
        "entry": (80, 154),
        # 场景地图黄字地名（其下方入口 NPC 绿字：黄袍怪，2026-11-24 用户确认）
        "npc_anchor": "波月洞",
        # 洞内进出光圈（用户 2026-11-24 实测；走进会退出山洞）
        "portal": (66, 51),
    },
    "火云洞": {
        "parent": "火云戈壁",
        "entry": (16, 66),
        # 场景地图黄字地名（其下方入口 NPC 绿字：红孩儿，2026-11-24 用户确认）
        "npc_anchor": "火云洞",
        # 洞内进出光圈（用户 2026-11-24 实测；走进会退出山洞）
        "portal": (10, 5),
    },
    "金兜洞": {
        "parent": "火云戈壁",
        "entry": (218, 142),
        # 场景地图黄字地名（其下方入口 NPC 绿字：独角兕，2026-11-24 用户确认）
        "npc_anchor": "金兜洞",
        # 洞内进出光圈（用户 2026-11-24 实测；走进会退出山洞）
        "portal": (13, 5),
    },
}
# 到达父地图入口后等待自动进门的短窗口（光圈触发通常在 1s 内）
SPECIAL_AUTO_ENTER_WAIT = 6.0
# 场景地图点入口 NPC 后等待进入的窗口：可能从远处跨图寻路（兜底路径），
# 放宽到与 ARRIVE_TIMEOUT 同档；战斗期不计入（_wait_special_enter）
SPECIAL_ENTER_TIMEOUT = 180.0
# 点 NPC 后等待起步窗口：地图刚打开时第一次点击 NPC 名可能只填入
# 搜索框（2026-11-23 实机：点击已选中金角大王但人物不动，再点一次
# 立即起步），3s 内位置不动且地图仍开着就再点一次（最多 3 次）；
# 地图已关闭时不再补点（避免点进世界画面取消寻路）
SPECIAL_CLICK_START_WAIT = 3.0
SPECIAL_CLICK_ATTEMPTS = 3

# ---- 特殊地图洞内按坐标步行（无小地图、无角色名可用）----
# 用户 2026-11-23 规则：洞内小步走一律**右键**（左键是选择/交互）；
# 不依赖角色名字（人多时 OCR 读不到名字）；到达目标 ±3 格即可（目的是
# 看到任务模板/执行操作）；行走可打断——不必等走到位再点下一次。
#
# 鼠标右键点击地面 = 走到该屏幕点对应的世界坐标（与相机当前视图相关），
# 模型：world = M * screen + B，M = diag(SX, SY)（2026-11-23 莲花洞实测
# 屏幕 +x → 世界 +x、屏幕 +y → 世界 -y，比例约 0.048 格/px）：
#   world_x = 0.048 * px + Bx
#   world_y = -0.049 * py + By
# 每次点击后读 HUD 到达坐标，用「点击点 → 到达坐标」反解 B（相机在
# 小步走时基本不动；相机边缘跟随造成的小偏差由下一步 B 重解吸收）。
# 初始 B 用探针点击（依次尝试几个常见地面点；点中 NPC 会出对话框，
# 检测到先点屏关闭再换点），因此全程不需要识别角色名字/位置。
SPECIAL_WALK_SX = 0.048          # 格/px（屏幕 x 轴）
SPECIAL_WALK_SY = -0.049         # 格/px（屏幕 y 轴，向下为世界 y 减小）
SPECIAL_WALK_TOLERANCE = 3       # 到达容差（格，用户 2026-11-24 改回 3：
# ±5 离目标太远，看模板/做操作要更近）
SPECIAL_WALK_MAX_STEPS = 60
SPECIAL_WALK_TIMEOUT = 240.0
SPECIAL_WALK_MOVE_WAIT = 4.0     # 单步点击后等起步（探针/停稳判定用）
SPECIAL_WALK_SETTLE = 1.0        # 位移后连续不变判定停稳
SPECIAL_WALK_POS_RETRIES = 3     # 位置 OCR 读失败重试次数
# 相机位移跟踪（2026-11-23 实机校准）：异形洞内相机随人物滚动，单次探针
# 得到的 B 只对点击时刻有效；用相位相关测两次截图之间场景内容的平移，
# 实时更新 B（world = M*screen + B），长距离/跨区点击也能准确落点。
# 区域避开顶部聊天/HUD 与底部快捷栏；实测两对已知相机位移 (-224,-90)、
# (-259,+90) 的帧，相位相关返回值与模板匹配完全一致（误差 <1px），
# 响应度 0.3~0.8；低于 MIN_RESP 视为不可靠、本次不更新。
SPECIAL_WALK_SHIFT_REGION = (200, 200, 1300, 730)
SPECIAL_WALK_SHIFT_MIN_RESP = 0.12
# 特殊地图路网（2026-11-23 用户规则）：**人工描边边界**——用户手动操作
# 人物沿可走区域描边，scripts/trace_special_map.py 记录实走坐标，
# scripts/build_special_map_route.py 把描边存成边界多边形（boundary）
# 与可选捷径折线（trace）。语义：**描边以内都可走，以外不可走**。
# 导航时目标在边界内 → 直接直线走过去（客户端自动绕障）；目标在边界外
# → 落到最近可走边界点，绝不点空洞；无描边时才用自动探图/折线兜底。
SPECIAL_MAP_ROUTES_VERSION = 1
SPECIAL_MAP_ROUTES_FILE = "special_map_routes.json"  # 内置路网（res/）
SPECIAL_ROUTE_MAX_NODE_DIST = 25   # 目标离最近臂节点超过该格数则不按路网走
SPECIAL_ROUTE_WP_TOLERANCE = 3     # 途经节点到达容差（格）
SPECIAL_ROUTE_WP_SKIP_AFTER = 3    # 途经点连续不可达时跳过（继续下一途经点）
SPECIAL_TRACE_BOUNDARY_TOL = 3     # 描边边界容差（格）：边界内=可走
# 传送光圈避让半径（格，用户 2026-11-24 实测修正 5→3）：路线**穿过**
# 光圈会退出山洞（2026-11-24 实机 (90,6)→(70,20) 直穿 (84,9) 附近被
# 传送回平顶山）；端点落在光圈内的路段放行（进洞落点/目标本身就在
# 光圈上）。
SPECIAL_PORTAL_AVOID_R = 3.0
# 点击相对角色屏幕位置的最大步长（px）：只点角色附近（附近必是可走地面），
# 不再按远目标的屏幕点（可能落在空洞/屏幕外被钳到空洞边缘）点击。
SPECIAL_WALK_SCREEN_STEP = 320
# 描边采集时的角色屏幕锚点模型（用户 2026-11-23 提议）：trace_special_map.py
# 记录用户实走轨迹时同时 OCR 角色名字标签，把「世界坐标 → 角色屏幕位置」
# 存进路网 self_anchor；运行期拿当前 HUD 坐标就近查表（K 近邻反距离加权）
# 直接得到角色屏幕位置 → B = W - M*A，不依赖运行期名字 OCR（人多读不到
# 名字）/探针/热启动。用户确认采集与运行条件基本一致，窗口尺寸须匹配。
SPECIAL_ANCHOR_MODEL_MAX_DIST = 12.0   # 锚点样品覆盖半径（格）
SPECIAL_ANCHOR_MODEL_K = 6             # 近邻样品数
SPECIAL_ANCHOR_MODEL_WIN_TOL = 8       # 窗口尺寸容差（px）
# 角色名字标签（显示在角色脚下，小号彩色字）用「裁剪 2x 放大 + ocr_zoom
# 引擎」识别：全帧引擎时好时坏、区域快引擎基本读不到（2026-11-23 实机），
# 2x 裁剪实测 0.07~0.5s、置信度 0.9；允许 1 字误识别（可乐可口→同乐可口）。
SPECIAL_NAME_REGION = (150, 180, 1250, 730)   # 游戏画面中部（排除标题/Tab/底栏）
SPECIAL_NAME_ZOOM_SCALE = 2
SPECIAL_NAME_MIN_SCORE = 0.6
SPECIAL_NAME_FUZZY_DIST = 1
_ROUTE_TABLE = None
_ROUTE_TABLE_MTIME = None
# 上一次洞内导航结束时的视图（帧 + B + 地图）：下次导航用相位相关
# 直接把 B 平移到当前视图，免探针绕路、也避免探针被挡导致 B 失准。
_LAST_SPECIAL_VIEW = {}
# 探针候选点（窗口相对，常见地面区域；点中 NPC/角色不出位移就换下一个）
SPECIAL_WALK_PROBES = ((820, 560), (760, 540), (900, 580),
                       (640, 560), (860, 620))
# 不再限制单步格数：目标/途经点的屏幕点可点就一次点到位（人物连续走），
# 只有屏幕点超出可点区才钳到安全边分段（走走停停的根因是每 8 格断一次）。
# 点击点安全范围（避开顶部 HUD/底部快捷栏与窗口边缘）
SPECIAL_WALK_MIN_X, SPECIAL_WALK_MAX_X = 60, 1320
SPECIAL_WALK_MIN_Y, SPECIAL_WALK_MAX_Y = 140, 760
# 连续步行（用户 2026-11-23）：行走可打断，不必走到点击点停稳再点。
# 逐帧相位相关跟踪相机，按固定间隔沿目标方向重新右击，人物不停顿；
# 首步等停稳测一次映射偏差（aim_bias，地形/锚点定义造成的固定偏移），
# 之后不再等待停稳。停滞判定用「连续 N 轮位置不变」而不是单次点击。
SPECIAL_WALK_CLICK_INTERVAL = 0.5   # 两次右键最小间隔（s）
SPECIAL_WALK_STALL_POLLS = 4        # 连续 N 轮位置不变 → 记一次点击无效
# 首步停稳校准（默认关闭，用户 2026-11-24：每次导航开头都先走一步停一下）。
# 改为**终点闭环**：接近目标时停稳复核，误差超容差按最终误差修正点击偏置
# 再补一步；途中连续走不停顿。
SPECIAL_WALK_AIM_CALIB_FIRST = False
SPECIAL_WALK_ARRIVE_CHECK = 8       # 终点复核预判半径（格；成功仍要 ±3）
# 直线导航（单途经点）长时间无实质进展 → 改走人工描边路网绕行。
# 2026-11-24 实机：起点/目标都在描边内但直线被局部障碍挡住、人物原地
# 小幅抖动（每轮 ±1 格），「连续位置不变」的停滞判定永远不触发，点了
# 30+ 次还在原地；改为 15s 内对最终目标没有 ≥3 格进展就换描边路网。
SPECIAL_WALK_DIRECT_STUCK_TIME = 15.0
SPECIAL_WALK_PROGRESS_MIN = 3       # 视为「有进展」的最小前进格数
# 可见性导航（用户 2026-11-24）：有人物屏幕位置就能算出视窗内可见的
# 世界坐标；每次只点**路线上屏幕内最远**的途经点（中间点直接跳过），
# 不再把点击钳到屏幕边缘——边缘点常是空洞/被 HUD 挡，人物会原地打转。
SPECIAL_WALK_VIEW_MARGIN = 20       # 视窗内缩边距（px，避开 UI/边缘）
# 首步校准的点击距离上限（px）：要等停稳测落点偏差，整段走太久；
# 且相位相关在单次位移接近半幅（区域 1100x530）时会混叠，限制在
# 240px 内保证相机位移可测。
SPECIAL_WALK_CALIB_STEP = 200
# ---- 多分区特殊地图（波月洞等：同一坐标平面分 A/B 两区，洞口传送连接）----
# 用户 2026-11-24 规则：波月洞进入先到 A 区，A 区走到一个坐标附近自动
# 传送到 B 区；A/B 的坐标都画在同一平面上，但两区之间是空洞/墙壁。
# 旧的单一描边边界会把两区当成一块连通区域（A* 在空洞里绕），按坐标
# 可见性永远走不进 B 区。路网改为 zones（每区独立边界）+ links（传送
# 触发点/落点），导航时先走到当前区的触发点，等传送进目标区再继续。
SPECIAL_TRIGGER_TOLERANCE = 2        # 走到传送触发点的到达容差（格）
SPECIAL_TRIGGER_WALK_TIMEOUT = 90.0  # 单次走到触发点的超时（s）
SPECIAL_TELEPORT_WAIT = 8.0          # 到达触发点后等待场景切换（s）
SPECIAL_TELEPORT_STABLE_POLLS = 2    # 传送后位置连续 N 次一致才继续
SPECIAL_TELEPORT_JUMP_MIN = 10.0     # 落点不在任何分区时按跳变判传送（格）
SPECIAL_TELEPORT_NUDGES = 3          # 未触发传送时朝洞口方向补点次数
SPECIAL_TELEPORT_NUDGE_TIMEOUT = 20.0
# 区内其他传送触发点与进出光圈共用 SPECIAL_PORTAL_AVOID_R=3 格避让：
# 太小会误踩回另一区，太大会把触发点附近的合法目标挤出可走区（波月洞
# 落点距回程触发点仅约 4.5 格，避让半径必须小于该距离）。

# ---- 父地图场景地图「普通」筛选与入口 NPC 定位（2026-11-23 实测）----
# 「普通」勾选框不再按搜索条偏移推算：2026-11-24 实机复盘发现旧偏移
# (32,452) 实际落在筛选行下方 22px 的地图内容上——该处亮像素被误判为
# 「已勾选」（客栈跨图导航必现），点击后打开世界地图盖住小地图，后续
# 悬停标定全部失效。改为复用 game_map.minimap_filter_boxes（小地图
# 筛选行模板定位，第 3 个框 =「普通」，见 MINI_MAP_FILTER_PUTONG_INDEX）。
# 地图画布 OCR 区域相对基准点（实测窗口地图区 (266,237)-(1106,647)，
# 基准点 (406,217) → 左 -140、上 +20、右 +700、下 +430）
MINIMAP_CANVAS_DX0 = -140
MINIMAP_CANVAS_DY0 = 20
MINIMAP_CANVAS_DX1 = 700
MINIMAP_CANVAS_DY1 = 430
# 入口 NPC 绿字定位带：黄字地名锚点正下方（实测「莲花洞」rect
# (469,516)-(532,544)，金角/银角大王绿字位于 (491,541)-(538,564)）
NPC_LABEL_BAND_DX0 = -25
NPC_LABEL_BAND_DX1 = 65
NPC_LABEL_BAND_DY0 = -3
NPC_LABEL_BAND_DY1 = 45
NPC_LABEL_HEAD_H = 12      # 第一行文字高度（两行 NPC 名贴得很近，只取顶行）
NPC_LABEL_MIN_GREEN = 30
NPC_LABEL_GREEN_DR = 40    # G-R 下限（亮绿字实测 ~200+）
NPC_LABEL_GREEN_DB = 30    # G-B 下限
NPC_LABEL_GREEN_MIN = 110  # G 下限
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


def special_map_entry(name):
    """特殊地图入口定义（世界地图无标签的洞府场景）；普通地图返回 None。"""
    return SPECIAL_MAP_ENTRIES.get((name or "").strip())


def all_navigate_maps():
    """诊断-地图导航下拉选项：常规地图 + 龙窟/凤巢/大雁塔各层 + 特殊地图。

    排除三类：
    - UNSUPPORTED_NAV_MAPS（海底迷宫/地狱迷宫，用户 2026-11-20 暂缓）；
    - 层级图基名（龙窟/凤巢/大雁塔）——世界地图点它们只会打开层块页
      或无法进入，不能作为导航目标，只能选具体层名（2026-11-20 审查发现）；
    与 WORLD_MAP_LABELS 同序，层名附后，特殊地图（莲花洞等，世界地图
    无标签、进入后无小地图）列在最后（2026-11-23）。
    """
    return ([k for k in game_map.WORLD_MAP_LABELS
             if k not in UNSUPPORTED_NAV_MAPS
             and k not in LAYER_MAP_FLOORS]
            + layer_map_names() + list(SPECIAL_MAP_ENTRIES))


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


def _aim_probe_offsets(sx, sy):
    """瞄准点悬停无浮层时的邻域试探偏移（px，按优先级排序）。

    校验容差是 AIM_TOLERANCE 格，换算成像素半径 = AIM_TOLERANCE·比例；
    只有这个半径内的邻居读回才可能仍在容差内。地图图标/NPC 名多是横向
    小块，先沿 x 轴左右各试整半径与半半径（最容易跳出遮挡），再沿 y 轴，
    最后对角。偏移更大时读回必然超差，偏差修正会把点击点搬回原像素、
    再次无浮层——这正是旧实现 ±14px 恢复的死循环来源（2026-11-23 蟠桃园
    (27,112)：目标像素 (543,429) 被地图内容盖住，±14px 读回偏 7 格，
    修正回原像素，3 次后误回退完整采样 14s）。
    """
    rx = max(1, int(round(AIM_TOLERANCE * abs(float(sx)))))
    ry = max(1, int(round(AIM_TOLERANCE * abs(float(sy)))))
    offs = [(rx, 0), (rx // 2, 0), (-rx, 0), (-rx // 2, 0),
            (0, ry), (0, ry // 2), (0, -ry), (0, -ry // 2),
            (rx, ry), (rx, -ry), (-rx, ry), (-rx, -ry)]
    seen = set()
    out = []
    for dx, dy in offs:
        if (dx, dy) == (0, 0) or (dx, dy) in seen:
            continue
        seen.add((dx, dy))
        out.append((dx, dy))
    return out


def _probe_hover_within_tolerance(win, affine, px, py, tx, ty, emit):
    """目标像素悬停无浮层：在容差半径内试探邻居像素，返回
    (px, py, got) 或 None。

    只接受读回坐标与目标差 ≤AIM_TOLERANCE 格的邻居——它的点击点与
    目标像素差 ≤2 格，仍在悬停校验口径内；读回超差的邻居不点（标定
    可能过期，交给调用方修正或回退采样）。偏移恢复不改标定、不写缓存。
    目标像素落在地图图标/NPC 名上是常见现象（浮层被盖住不出），贴边
    邻居通常可读：2026-11-21 大雁塔五层内置标定点 (864,388) → +4px
    的 (868,388) 正常；2026-11-23 蟠桃园 (27,112) 目标像素 (543,429)
    被盖住，邻近的 (541,430) 实测有浮层（读回 26,112）。
    """
    for dx, dy in _aim_probe_offsets(affine["sx"], affine["sy"]):
        _check_cancel()
        hx, hy = int(px + dx), int(py + dy)
        if not (0 <= hx <= 1377 and 0 <= hy <= 830):
            continue
        got = _hover_coord(win, hx, hy)
        if got is None:
            continue
        if (abs(got[0] - tx) <= AIM_TOLERANCE
                and abs(got[1] - ty) <= AIM_TOLERANCE):
            emit(f"瞄准点悬停无浮层，容差内偏移 ({dx:+d},{dy:+d}) 恢复")
            return hx, hy, got
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
    """清空标定缓存（内置表 + 会话 + 持久化内存镜像；测试用）。

    不删除角色持久化文件（生产重启动时要继续用）。
    """
    global _AFFINE_TABLE, _AFFINE_TABLE_MTIME
    global _PERSISTED_AFFINES, _PERSISTED_AFFINES_MTIME
    _AFFINE_TABLE = None
    _AFFINE_TABLE_MTIME = None
    _SESSION_AFFINES.clear()
    _PERSISTED_AFFINES = None
    _PERSISTED_AFFINES_MTIME = None


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


def _persisted_affines_path():
    """角色持久化标定文件路径（测试可注入临时路径）。"""
    from ..core import user_data
    return user_data.map_nav_affines_path()


def _load_persisted_affines():
    """加载角色持久化标定（按 mtime 热更新）；缺失/损坏返回 {}。"""
    global _PERSISTED_AFFINES, _PERSISTED_AFFINES_MTIME
    from ..core import user_data
    path = None
    mtime = None
    try:
        path = _persisted_affines_path()
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if _PERSISTED_AFFINES is not None and mtime == _PERSISTED_AFFINES_MTIME:
        return _PERSISTED_AFFINES
    data = {}
    if path is not None and mtime is not None:
        try:
            data = user_data.read_map_nav_affines(path=path)
        except Exception:
            data = {}
    _PERSISTED_AFFINES = data
    _PERSISTED_AFFINES_MTIME = mtime
    return data


def _persist_affine(target, affine, w, h):
    """把标定写角色数据（读-改-写，失败只影响跨重启热启动）。

    与上次记录差异 <0.05px 不写盘——悬停修正常在 1 格以内反复触发，
    避免每次导航都产生一次磁盘写。
    """
    global _PERSISTED_AFFINES, _PERSISTED_AFFINES_MTIME
    from ..core import user_data
    try:
        entry = {"sx": round(float(affine["sx"]), 4),
                 "sy": round(float(affine["sy"]), 4),
                 "ax": round(float(affine["ax"]), 2),
                 "ay": round(float(affine["ay"]), 2),
                 "at": round(time.time(), 1)}
        data = dict(_load_persisted_affines())
        layouts = data.get("layouts")
        layouts = dict(layouts) if isinstance(layouts, dict) else {}
        key = f"{int(w)}x{int(h)}"
        per = layouts.get(key)
        per = dict(per) if isinstance(per, dict) else {}
        prev = per.get(target)
        if isinstance(prev, dict):
            try:
                if all(abs(float(prev[k]) - entry[k]) < 0.05
                       for k in ("sx", "sy", "ax", "ay")):
                    return
            except (KeyError, TypeError, ValueError):
                pass
        per[target] = entry
        layouts[key] = per
        data["version"] = AFFINE_CACHE_VERSION
        data["layouts"] = layouts
        path = _persisted_affines_path()
        user_data.write_map_nav_affines(data, path=path)
        _PERSISTED_AFFINES = data
        try:
            _PERSISTED_AFFINES_MTIME = path.stat().st_mtime
        except Exception:
            _PERSISTED_AFFINES_MTIME = None
    except Exception:
        pass  # 持久化失败不影响本次导航（下次再尝试写）


def affine_for(win, target):
    """取目标地图标定：会话缓存 → 角色持久化 → 内置表。

    布局（游戏窗口尺寸）不匹配/无记录返回 None。持久化值只是热启动，
    点击前仍做悬停校验——窗口几何再变时会自动修正并刷新持久化。
    """
    size = [int(win.rect.width), int(win.rect.height)]
    cached = _SESSION_AFFINES.get((target, size[0], size[1]))
    if cached is not None:
        return dict(cached)
    layouts = _load_persisted_affines().get("layouts")
    if isinstance(layouts, dict):
        per = layouts.get(f"{size[0]}x{size[1]}")
        if isinstance(per, dict) and per.get(target):
            aff = _valid_affine(per[target])
            if aff is not None:
                return aff
    table = _load_affine_table()
    if list(table.get("layout") or []) != size:
        return None
    entry = (table.get("maps") or {}).get(target)
    if not entry:
        return None
    return _valid_affine(entry)


def remember_affine(win, target, affine):
    """把采样/修正解记录到会话缓存 + 角色持久化（重启后直接热启动）。"""
    w, h = int(win.rect.width), int(win.rect.height)
    _SESSION_AFFINES[(target, w, h)] = dict(affine)
    _persist_affine(target, affine, w, h)


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
    目标像素悬停无浮层时先在容差半径内试邻居（图标/NPC 名遮挡的常见
    情况，见 _probe_hover_within_tolerance），每条调用只探一轮。
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
    probed = False
    t0 = time.time()
    for _attempt in range(1, AIM_ATTEMPTS + 1):
        _check_cancel()
        if not (0 <= px <= 1377 and 0 <= py <= 830):
            return False, None, "计算出的点击点超出窗口范围"
        got = _hover_coord(win, px, py)
        if got is None:
            probe = None
            if not probed:
                # 图标/NPC 名遮挡目标像素（浮层不出）是常见情况：先在
                # 容差半径内找可读邻居。每条调用只探一轮——全部失败说明
                # 标定过期或点在地图窗口外，再探只会白付悬停耗时。
                probed = True
                probe = _probe_hover_within_tolerance(
                    win, aff, px, py, tx, ty, emit)
            if probe is not None:
                px, py, got = probe
            elif anchor_pt is None:
                # 热启动无采样点可收缩：交由调用方回退完整采样
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

    战斗判定先查战斗线程确认状态（battle.watch_in_battle，内存读零
    成本）：已确认战斗中直接冻结超时计时，跳过本帧截图 + 条带模板
    匹配 + 每 3s 全帧兜底（2026-11-25，与 _wait_arrival 同款）；线程
    未确认/会话中止（线程可能已退出、状态残留）时保持原帧级判定，
    防死等。
    """
    deadline = time.time() + timeout
    last_name = None
    while time.time() < deadline:
        _check_cancel()
        if watch_in_battle() and not app_utils.session_abort_reason():
            # 途中遇战斗：坐标不可读、人物不动——战斗期不计入超时
            deadline += 0.8
            time.sleep(0.8)
            continue
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


# ---- 层块页坐标缓存（2026-11-25 用户确认：层块页布局固定）----
# 首次 OCR 定位成功后缓存该层标题小块模板；后续导航用模板匹配
# （~10ms）当「层块页已渲染」信号并给出点击点，再用小区域 OCR
# （~30-100ms，_rect_text 2x）读回层名做文本确认，总开销 ~0.1s
# vs 整页 OCR ~0.5-0.9s。确认失败/模板未命中（动画、布局变化）回退
# 整页 OCR 并重新学习。键含窗口尺寸（窗口布局变化时重新学习）。
# 2026-11-25 用户需求：模板持久化到 data/map_nav_layer_tiles.json
# （PNG base64，按窗口尺寸分层）——层块页是游戏 UI、与角色无关，
# 全局共享，所有账号重启后首次导航即命中，不必每层再付一次整页 OCR。
# 另支持随版本发布的内置表 res/map_nav_layer_tiles.json（2026-11-25
# 实机抓取龙窟/凤巢 14 层，格式与持久化文件一致）；查找顺序
# 会话缓存 → 运行时持久化（学习/修正优先）→ 内置表 → 整页 OCR。
_LAYER_TILE_CACHE = {}
LAYER_TILE_TPL_HALF_W = 46       # 标题小块半宽（层名 4-6 字，实测 ~60-100px）
LAYER_TILE_TPL_HALF_H = 14       # 半高（单行文字 ~24px）
LAYER_TILE_TPL_THRESHOLD = 0.90  # TM_CCOEFF_NORMED 灰度（静态标题，取严）
# OCR 吞字容错门槛（2026-11-25 实机：凤巢一/二层标题小区域 OCR 稳定
# 读成「凤巢层」，整页 OCR 同样吞「一」）。模板分数接近全等（跨层
# 模板交叉实测最高 0.759，0.90 门限已能拒绝错层）+ 文本含基础名与
# 「层」且不含其它层序号，才允许绕过完整层名确认；分数不够仍回退
# 整页 OCR（吞字时整页 OCR 的网格拟合仍能定位）。
LAYER_TILE_TPL_STRICT_THRESHOLD = 0.97
LAYER_TILE_CACHE_VERSION = 1     # 持久化/内置表文件格式版本
LAYER_TILE_TABLE_FILE = "map_nav_layer_tiles.json"  # 内置表（res/）
# 持久化模板尺寸合法性（坏文件/跨版本残留不得撑爆匹配）
LAYER_TILE_TPL_MIN_W, LAYER_TILE_TPL_MIN_H = 20, 10
LAYER_TILE_TPL_MAX_W, LAYER_TILE_TPL_MAX_H = 200, 60
_PERSISTED_LAYER_TILES = None
_PERSISTED_LAYER_TILES_MTIME = None
_LAYER_TILE_TABLE = None
_LAYER_TILE_TABLE_MTIME = None


def reset_layer_tile_cache():
    """清空层块页坐标缓存（内存 + 持久化/内置表镜像；测试/布局变化用）。

    不删除角色持久化文件与内置表（生产重启动时要继续用）。
    """
    global _PERSISTED_LAYER_TILES, _PERSISTED_LAYER_TILES_MTIME
    global _LAYER_TILE_TABLE, _LAYER_TILE_TABLE_MTIME
    _LAYER_TILE_CACHE.clear()
    _PERSISTED_LAYER_TILES = None
    _PERSISTED_LAYER_TILES_MTIME = None
    _LAYER_TILE_TABLE = None
    _LAYER_TILE_TABLE_MTIME = None


def _layer_tile_cache_key(win, base, layer):
    try:
        return (base, layer, int(win.rect.width), int(win.rect.height))
    except Exception:  # noqa: BLE001 —— 无窗口几何时不缓存（测试桩）
        return None


def _layer_tile_title_rect(pt):
    """标题中心 → 标题小块矩形（模板裁剪与文本确认共用）。"""
    cx, cy = int(pt[0]), int(pt[1])
    return (cx - LAYER_TILE_TPL_HALF_W, cy - LAYER_TILE_TPL_HALF_H,
            cx + LAYER_TILE_TPL_HALF_W, cy + LAYER_TILE_TPL_HALF_H)


def _layer_tile_table_path():
    """内置层块模板表路径（res/map_nav_layer_tiles.json；测试可注入）。"""
    from ascript.windows.system import R
    return Path(R.res(LAYER_TILE_TABLE_FILE))


def _load_layer_tile_table():
    """加载内置层块模板表（按 mtime 热更新）；缺失/损坏返回 {}。"""
    global _LAYER_TILE_TABLE, _LAYER_TILE_TABLE_MTIME
    path = None
    mtime = None
    try:
        path = _layer_tile_table_path()
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if _LAYER_TILE_TABLE is not None and mtime == _LAYER_TILE_TABLE_MTIME:
        return _LAYER_TILE_TABLE
    table = {}
    if path is not None and mtime is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                table = data
        except Exception:
            table = {}
    _LAYER_TILE_TABLE = table
    _LAYER_TILE_TABLE_MTIME = mtime
    return table


def _persisted_layer_tiles_path():
    """持久化层块模板文件路径（data/ 根目录，全角色共享；测试可注入）。"""
    from ..core import user_data
    return user_data.map_nav_layer_tiles_path()


def _load_persisted_layer_tiles():
    """加载持久化层块模板（按 mtime 热更新）；缺失/损坏返回 {}。"""
    global _PERSISTED_LAYER_TILES, _PERSISTED_LAYER_TILES_MTIME
    from ..core import user_data
    path = None
    mtime = None
    try:
        path = _persisted_layer_tiles_path()
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if (_PERSISTED_LAYER_TILES is not None
            and mtime == _PERSISTED_LAYER_TILES_MTIME):
        return _PERSISTED_LAYER_TILES
    data = {}
    if path is not None and mtime is not None:
        try:
            data = user_data.read_map_nav_layer_tiles(path=path)
        except Exception:
            data = {}
    _PERSISTED_LAYER_TILES = data
    _PERSISTED_LAYER_TILES_MTIME = mtime
    return data


def _decode_layer_tile(entry):
    """持久化条目 → 灰度模板；字段缺失/损坏/尺寸异常返回 None。"""
    if not isinstance(entry, dict):
        return None
    try:
        tw, th = int(entry["w"]), int(entry["h"])
        if not (LAYER_TILE_TPL_MIN_W <= tw <= LAYER_TILE_TPL_MAX_W
                and LAYER_TILE_TPL_MIN_H <= th <= LAYER_TILE_TPL_MAX_H):
            return None
        raw = base64.b64decode(str(entry.get("png_b64") or ""))
        if not raw:
            return None
        tpl = cv2.imdecode(np.frombuffer(raw, np.uint8),
                           cv2.IMREAD_GRAYSCALE)
        if tpl is None or tpl.shape[0] != th or tpl.shape[1] != tw:
            return None
        return tpl
    except Exception:
        return None


def _layer_tile_entry(table, layout_key, base, layer):
    """从表（{layouts:{布局:{层名:条目}}}）取层模板条目；无则 None。"""
    layouts = table.get("layouts") if isinstance(table, dict) else None
    if not isinstance(layouts, dict):
        return None
    per = layouts.get(layout_key)
    if not isinstance(per, dict):
        return None
    entry = per.get(layer)
    if not isinstance(entry, dict):
        return None
    if entry.get("base") and entry.get("base") != base:
        return None
    return entry


def _promote_layer_tile(win, base, layer):
    """会话缓存未命中：运行时持久化 → 内置表，取该层模板提升进内存缓存。

    持久化（运行期学习/刷新）优先于内置表（随版本发布的热启动资产），
    两处都未命中才回退整页 OCR。
    """
    key = _layer_tile_cache_key(win, base, layer)
    if key is None:
        return None
    layout = f"{key[2]}x{key[3]}"
    for table in (_load_persisted_layer_tiles(), _load_layer_tile_table()):
        entry = _layer_tile_entry(table, layout, base, layer)
        tpl = _decode_layer_tile(entry)
        if tpl is None:
            continue
        pt = entry.get("pt")
        try:
            pt = (int(pt[0]), int(pt[1]))
        except (TypeError, IndexError, ValueError):
            pt = None
        ent = {"pt": pt, "tpl": tpl}
        _LAYER_TILE_CACHE[key] = ent
        return ent
    return None


def _persist_layer_tile(win, base, layer, pt, tpl):
    """把学到的层块标题模板写共享数据（读-改-写；失败不影响本次导航）。

    模板未变化（同层重复学习）不写盘；多进程各自持 mtime 镜像，
    并发写以最后一次为准——丢失的层下次导航重新学习即可。
    """
    global _PERSISTED_LAYER_TILES, _PERSISTED_LAYER_TILES_MTIME
    from ..core import user_data
    try:
        ok, buf = cv2.imencode(".png", tpl)
        if not ok:
            return
        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        w, h = int(win.rect.width), int(win.rect.height)
        data = dict(_load_persisted_layer_tiles())
        layouts = data.get("layouts")
        layouts = dict(layouts) if isinstance(layouts, dict) else {}
        key = f"{w}x{h}"
        per = layouts.get(key)
        per = dict(per) if isinstance(per, dict) else {}
        prev = per.get(layer)
        if isinstance(prev, dict) and prev.get("png_b64") == b64:
            return  # 模板未变化不写盘
        per[layer] = {"base": base, "pt": [int(pt[0]), int(pt[1])],
                      "w": int(tpl.shape[1]), "h": int(tpl.shape[0]),
                      "png_b64": b64, "at": round(time.time(), 1)}
        layouts[key] = per
        data["version"] = LAYER_TILE_CACHE_VERSION
        data["layouts"] = layouts
        path = _persisted_layer_tiles_path()
        user_data.write_map_nav_layer_tiles(data, path=path)
        _PERSISTED_LAYER_TILES = data
        try:
            _PERSISTED_LAYER_TILES_MTIME = path.stat().st_mtime
        except Exception:
            _PERSISTED_LAYER_TILES_MTIME = None
    except Exception:
        pass  # 持久化失败不影响本次导航（下次学习再尝试写）


def _remember_layer_tile(win, img, base, layer, pt):
    """缓存层块标题中心 + 标题小块灰度模板（首次 OCR 定位成功后调用），
    并写入共享持久化（进程重启后直接命中）。"""
    key = _layer_tile_cache_key(win, base, layer)
    if key is None:
        return
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    x0, y0, x1, y1 = _layer_tile_title_rect(pt)
    x0, y0 = max(0, x0), max(0, y0)
    x1 = min(gray.shape[1], x1)
    y1 = min(gray.shape[0], y1)
    if x1 - x0 < 20 or y1 - y0 < 10:
        return
    tpl = gray[y0:y1, x0:x1].copy()
    _LAYER_TILE_CACHE[key] = {"pt": (int(pt[0]), int(pt[1])), "tpl": tpl}
    _persist_layer_tile(win, base, layer, pt, tpl)


def _layer_text_confirms(text, base, layer, score):
    """层块标题文本确认（防模板误配点错层块）。

    正常：小区域 OCR 读回完整层名（子串匹配）。OCR 吞字（实测「凤巢
    一层」读成「凤巢层」）：文本含基础名与「层」、不含其它层序号，
    且模板分数 ≥0.97 时放行；否则交回整页 OCR（网格拟合兜底）。
    """
    if layer in text:
        return True
    if score < LAYER_TILE_TPL_STRICT_THRESHOLD:
        return False
    if base not in text or "层" not in text:
        return False
    for cn in _CN_NUM[:LAYER_MAP_FLOORS.get(base, 0)]:
        if cn in text and f"{base}{cn}层" != layer:
            return False
    return True


def _match_layer_tile(win, img, base, layer):
    """缓存命中：在全帧模板匹配标题小块（布局固定、全帧 ~10ms），
    再用小区域 OCR 读回层名确认。返回确认后的标题中心或 None。

    会话缓存未命中时先查持久化/内置表（重启后免整页 OCR）。
    """
    key = _layer_tile_cache_key(win, base, layer)
    ent = _LAYER_TILE_CACHE.get(key) if key else None
    if ent is None:
        ent = _promote_layer_tile(win, base, layer)
    if ent is None:
        return None
    gray = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    tpl = ent["tpl"]
    th, tw = tpl.shape[:2]
    if gray.shape[0] < th or gray.shape[1] < tw:
        return None
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < LAYER_TILE_TPL_THRESHOLD:
        return None
    cx = int(max_loc[0] + tw // 2)
    cy = int(max_loc[1] + th // 2)
    # 文本确认：小区域 2x OCR 读回层名（防模板误配点错层块）
    text = _rect_text(img, _layer_tile_title_rect((cx, cy)))
    if not _layer_text_confirms(text, base, layer, max_val):
        return None
    return (cx, cy)


def _wait_layer_tile(win, base, layer_name, timeout):
    """在层块页定位目标层标题，返回标题中心；找不到返回 None。

    优先缓存（模板匹配 + 文本确认，~0.1s）；未命中/确认失败回退整页
    OCR（~0.5-0.9s），成功后更新缓存。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            pt = _match_layer_tile(win, img, base, layer_name)
            if pt is not None:
                return pt
            pt = locate_layer_tile(img, base, layer_name)
            if pt is not None:
                _remember_layer_tile(win, img, base, layer_name, pt)
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
# 的必经点），实机验证点后角色立即起步（(103,77) → (73,54)/2.4s）；
# 宝象国 (75,135)（2026-11-24 五倍用户提供）。
# 点击像素由该地图标定换算（与导航同一套 affine）；无标定/越界跳过预走。
# **任务模式限制**（用户 2026-11-21/11-24 规则）：导航内自动预走按
# 当前任务模式识别——修罗（灵兽村）与五倍（宝象国）任务启用，从灵兽村
# 出发去做五环等其他任务不预走。五倍领取后任务栏确认任务需执行、随后
# 首次手动寻路（开图前）即触发；本轮有自动巫医时由 wubei 置整轮抑制
# （set_prewalk_suppressed，用户规则：巫医完再出发不需要预走）。
# 同图导航不预走；未配置的地图跳过。
PREWALK_POINTS = {"灵兽村": (14, 13), "宝象国": (75, 135)}
PREWALK_TASK_MODES = ("修罗", "五倍")
# 预走路整轮抑制（任务侧按轮次置位/复位）：五倍巫医轮次整轮不预走；
# 只影响预走路，不动任何导航逻辑（每任务独立进程，无跨任务干扰）
_PREWALK_SUPPRESS = False


def set_prewalk_suppressed(on):
    """置位/复位预走路抑制（五倍巫医轮次整轮不预走，用户 2026-11-24）。"""
    global _PREWALK_SUPPRESS
    _PREWALK_SUPPRESS = bool(on)


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

    返回预走耗时秒数（0=未执行/失败/被抑制，视为原地等待，不影响后续导航）。
    """
    if _PREWALK_SUPPRESS:
        return 0.0  # 本轮被任务侧抑制（五倍巫医轮次）
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
        return 0.0  # 非修罗/五倍任务（如五环）不预走
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
    # 快速关小地图：刚由本流程打开（known_open 免双帧复核）、无自动
    # 关闭动画风险，不等 1s 确认，0.15s 步长尽早返回；世界地图流程
    # 需要它先关（窗口重叠）。仍确认关闭——若不确认，后续 close_maps
    # 会把关闭动画中的面板当打开，白等 1s 确认窗口。
    game_map.close_map_if_open(win, game_map.MINI_MAP_OPEN_ICON, "小地图",
                               primary_button="primary",
                               match_region=game_map.MINI_MAP_REGION,
                               confirm_wait=0.0, quiet=True,
                               known_open=True, poll_step=0.15)
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
                  allow_restart=False, enter_name=None):
    """轮询 HUD 位置直到到达目标地图坐标 ±ARRIVE_TOLERANCE。

    位置连续 ARRIVE_STALL_TIMEOUT 秒不变 → 提前判失败（目标点不可达
    时客户端会站在最近可达点不动）。allow_restart=True 时改为 5s 停滞
    就返回 ARRIVE_RESTART_TEXT，由调用方重新开图补一次寻路。
    enter_name：特殊地图名（如莲花洞）——HUD 地名变成它即视为到达
    （特殊地图经父地图入口自动进场，客户端不会停在入口坐标上）。

    verbose=False（生产）：位置日志只在地名变化（过场景）时输出，
    避免行走期每 1s 一条刷屏；诊断卡片传 verbose=True 保留逐次位置。

    战斗判定先查战斗线程确认状态（battle.watch_in_battle，内存读零
    成本）：已确认战斗中直接冻结停滞计时，跳过本帧截图 + 条带模板
    匹配 + 每 3s 全帧兜底（2026-11-25 py-spy 实测行走期该帧判定占
    任务进程 ~9% 采样）；线程未确认/会话中止（线程可能已退出、状态
    残留）时保持原帧级判定，防死等。
    """
    deadline = time.time() + ARRIVE_TIMEOUT
    last = None
    last_name = None
    last_change = time.time()
    while time.time() < deadline:
        _check_cancel()
        if watch_in_battle() and not app_utils.session_abort_reason():
            # 途中遇战斗：坐标不可读、人物不动——冻结停滞计时并把战斗
            # 时间从总超时里扣掉（战斗线程自动打完，之后继续等寻路）
            last_change = time.time()
            deadline += ARRIVE_POLL_INTERVAL
            time.sleep(ARRIVE_POLL_INTERVAL)
            continue
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
        if enter_name is not None and pos is not None \
                and str(pos[0]).strip() == enter_name:
            return True, (f"已进入{enter_name} ({pos[1]},{pos[2]})")
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


# ---- 特殊地图进入（世界地图无标签、洞内无小地图，见 SPECIAL_MAP_ENTRIES）----

def _special_map_name_match(hud, target):
    """HUD 地名与目标特殊地图名是否一致（容忍 1 个 OCR 错字/漏字）。

    特殊地图步行只在同一张图内进行，位置区域 OCR 偶发错字：2026-11-24
    波月洞 B→A 实机把「波月洞」读成「波月同 (10,69)」，被当成场景变化
    直接判失败（其实已传送到 A 区）。编辑距离 ≤1（替换/插/删）按同图。
    """
    hud = str(hud or "").strip()
    target = str(target or "").strip()
    if not hud or not target:
        return False
    if hud == target:
        return True
    if len(target) < 2 or abs(len(hud) - len(target)) > 1:
        return False
    shorter, longer = ((hud, target) if len(hud) <= len(target)
                       else (target, hud))
    i = 0
    while i < len(shorter) and shorter[i] == longer[i]:
        i += 1
    if len(shorter) == len(longer):
        return shorter[i + 1:] == longer[i + 1:]
    return shorter[i:] == longer[i + 1:]


def _wait_special_enter(win, name, timeout, emit):
    """等待 HUD 地名变为特殊地图名（进场判据；战斗期不计入超时）。

    特殊地图洞内没有小地图，无法用坐标到达判定——进入场景本身即
    「到达」。途中随机遇战斗时坐标不可读，战斗时间从超时里扣掉。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancel()
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            deadline += 1.0
            time.sleep(1.0)
            continue
        pos = game_map.read_map_position(None, img=img) if img is not None else None
        if pos is not None and _special_map_name_match(pos[0], name):
            emit(f"已进入{name}（{pos[1]},{pos[2]}）")
            return True
        time.sleep(0.8)
    return False


def _putong_filter_box(img):
    """定位场景地图筛选行「普通」勾选框，返回 (是否勾选, cx, cy) 或 None。"""
    boxes = game_map.minimap_filter_boxes(img)
    if boxes is None or len(boxes) <= game_map.MINI_MAP_FILTER_PUTONG_INDEX:
        return None
    return boxes[game_map.MINI_MAP_FILTER_PUTONG_INDEX]


def _set_minimap_putong_filter(win, want_checked, emit):
    """设置场景地图「普通」筛选勾选状态（模板定位勾选框，按需点击一次）。

    勾选框由 map.minimap_filter_boxes 模板定位（筛选行第 3 个框），
    不再按搜索条偏移推算——2026-11-24 实机复盘：旧偏移 (32,452) 落在
    筛选行下方 22px 的地图内容上（不同场景行 y 实测 657/667），亮像素
    被误判为「已勾选」，点击后打开世界地图盖住小地图，悬停标定全部
    失效（客栈跨图导航必现）。识别不到筛选行时绝不点击（返回 False），
    宁可少做一次辅助筛选也不误点地图/其它 UI。
    入口 NPC 兜底需要勾选（绿字只勾选后显示）；其余坐标导航开图后统一
    取消勾选——勾选时 NPC 绿字（金角大王等）会吃掉点击（用户 2026-11-24
    规则：五倍手动寻路也不得残留勾选，其他任务一律取消）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败（窗口可能已最小化）"
    box = _putong_filter_box(img)
    if box is None:
        # 地图刚打开时筛选行可能尚未渲染：短轮询再试（最多 ~0.9s）
        for _ in range(3):
            time.sleep(0.3)
            img = win.capture(force_activate=False)
            if img is None:
                continue
            box = _putong_filter_box(img)
            if box is not None:
                break
    if box is None:
        return False, "筛选行未识别（地图可能未打开）"
    state, cx, cy = box
    if state == want_checked:
        return True, ("「普通」筛选已勾选" if want_checked
                      else "「普通」筛选已取消")
    human_click(win, cx, cy)
    time.sleep(0.4)
    img = win.capture(force_activate=False)
    box = _putong_filter_box(img) if img is not None else None
    if box is not None and box[0] == want_checked:
        emit(f"{'已勾选' if want_checked else '已取消'}「普通」筛选 @({cx},{cy})")
        return True, ("已勾选「普通」筛选" if want_checked
                      else "已取消「普通」筛选")
    return False, ("点击后「普通」筛选仍未勾选" if want_checked
                   else "点击后「普通」筛选仍未取消")


def _ensure_minimap_putong_filter(win, emit):
    """确保场景地图「普通」筛选已勾选（入口 NPC 绿字只在勾选后显示）。"""
    return _set_minimap_putong_filter(win, True, emit)


def _find_special_entry_npc(win, anchor_name, bar):
    """在父地图场景地图中定位特殊地图入口 NPC，返回点击点或 None。

    用户 2026-11-23 实机：勾选「普通」后，特殊地图黄字地名（莲花洞）
    正下方画着入口 NPC 绿字名（金角大王/银角大王），点任一即自动寻路
    进入。定位：OCR 黄字地名锚点 → 其下方限定带内取亮绿文字像素，
    取顶行（两行 NPC 名贴得很近，金角/银角均可）中点。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    region = (max(bar["x"] + MINIMAP_CANVAS_DX0, 0),
              max(bar["y"] + MINIMAP_CANVAS_DY0, 0),
              min(bar["x"] + MINIMAP_CANVAS_DX1, 1378),
              min(bar["y"] + MINIMAP_CANVAS_DY1, 831))
    items, _ = game_map.ocr_regions(img, [region])
    anchor = None
    for text, rect, score in items:
        t = (text or "").replace(" ", "").translate(
            game_map.WORLD_MAP_OCR_ALIASES)
        if score >= 0.75 and anchor_name in t:
            anchor = rect
            break
    if anchor is None:
        return None
    arr = np.asarray(img)[:, :, :3].astype(np.int16)
    l = max(anchor[0] + NPC_LABEL_BAND_DX0, 0)
    r = min(anchor[2] + NPC_LABEL_BAND_DX1, arr.shape[1])
    top = max(anchor[3] + NPC_LABEL_BAND_DY0, 0)
    bottom = min(anchor[3] + NPC_LABEL_BAND_DY1, arr.shape[0])
    if r <= l or bottom <= top:
        return None
    band = arr[top:bottom, l:r]
    mask = ((band[:, :, 1] > NPC_LABEL_GREEN_MIN)
            & (band[:, :, 1] - band[:, :, 0] > NPC_LABEL_GREEN_DR)
            & (band[:, :, 1] - band[:, :, 2] > NPC_LABEL_GREEN_DB))
    ys, xs = np.nonzero(mask)
    if len(ys) < NPC_LABEL_MIN_GREEN:
        return None
    y_min = int(ys.min())
    head = ys <= y_min + NPC_LABEL_HEAD_H
    xs_head = xs[head]
    cx = l + (int(xs_head.min()) + int(xs_head.max())) // 2
    cy = top + y_min + NPC_LABEL_HEAD_H // 2
    return cx, cy


def _wait_special_start(win, name, before, timeout):
    """点入口 NPC 后是否已起步：位置变化/位置暂不可读即已开始。

    地图已关闭（过图/切场景）也视为已开始——此时绝不能补点（点击会
    落到世界画面，取消自动寻路）。返回 False 仅表示地图仍开着且人物
    原地未动（可安全补点）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_cancel()
        time.sleep(0.5)
        pos = _read_pos(win)
        if pos is None:
            return True  # 过图动画/位置暂不可读 → 视为已起步
        if before is None or pos != before:
            return True
        if _special_map_name_match(pos[0], name):
            return True
        if not (game_map.is_mini_map_open(win)
                or game_map.is_world_map_open(win)):
            return True  # 地图已关闭：已在过图/切场景，不再补点
    return False


def _enter_special_by_npc(win, target, entry, emit):
    """场景地图 NPC 入口兜底：打开父地图 → 勾选普通 → 点入口 NPC。

    点击后客户端自动寻路到入口并过场（实测从 [79,10] 约 7s 进入），
    以 HUD 地名变为 target 为成功。2026-11-23 实机：地图刚打开时第一次
    点 NPC 名可能只把 NPC 填入搜索框、人物不动（再次点击立即起步）——
    起步窗口内未动且地图仍开着就补点，最多 SPECIAL_CLICK_ATTEMPTS 次。
    返回 (ok, info)。
    """
    parent = entry["parent"]
    anchor = entry.get("npc_anchor") or target
    pos = _read_pos(win)
    emit(f"特殊入口兜底：打开{parent}场景地图，点「{anchor}」入口 NPC")
    ok, info = _open_target_map(win, parent, None, pos, emit)
    if not ok:
        game_map.close_maps(win, quiet=True)
        return False, f"打开{parent}地图失败：{info}"
    _check_cancel()
    bar = locate_search_bar(win)
    if bar is None:
        game_map.close_maps(win, quiet=True)
        return False, "未定位到地图窗口搜索条"
    ok_f, info_f = _ensure_minimap_putong_filter(win, emit)
    if not ok_f:
        game_map.close_maps(win, quiet=True)
        return False, info_f
    pt = _find_special_entry_npc(win, anchor, bar)
    if pt is None:
        game_map.close_maps(win, quiet=True)
        return False, f"场景地图未找到「{anchor}」入口 NPC"
    before = _read_pos(win)
    started = False
    for attempt in range(1, SPECIAL_CLICK_ATTEMPTS + 1):
        emit(f"点击「{anchor}」入口 NPC @({pt[0]},{pt[1]})"
             f"（第 {attempt}/{SPECIAL_CLICK_ATTEMPTS} 次），等待自动寻路")
        human_click(win, pt[0], pt[1])
        if _wait_special_start(win, target, before,
                               SPECIAL_CLICK_START_WAIT):
            started = True
            break
        if not (game_map.is_mini_map_open(win)
                or game_map.is_world_map_open(win)):
            started = True  # 地图已关：过图中，不再补点
            break
        emit(f"点击入口 NPC 后人物未起步（地图仍开着），补点一次")
    if not started:
        game_map.close_maps(win, quiet=True)
        return False, f"点击「{anchor}」入口 NPC 后人物未起步"
    entered = _wait_special_enter(win, target, SPECIAL_ENTER_TIMEOUT, emit)
    game_map.close_maps(win, quiet=True)
    if entered:
        return True, f"已通过 NPC 入口进入{target}"
    return False, f"点击「{anchor}」入口 NPC 后未进入{target}"


def _special_model_anchor(win, target, pos):
    """描边采集的角色屏幕锚点模型 → 当前位置的角色屏幕位置；无则 None。

    用户 2026-11-23：人工描边时（scripts/trace_special_map.py）顺带 OCR
    角色名字标签，按「世界坐标 → 角色屏幕位置」记录样品，由建图脚本写入
    路网 ``self_anchor``。运行期给当前 HUD 坐标就近查表（K 近邻反距离
    加权）即可得到角色屏幕位置——采集时与运行时条件一致，故运行期不再
    依赖名字 OCR（人多读不到名字时同样可用）/探针/热启动。
    窗口尺寸与采集不一致、或附近没有样品覆盖时返回 None（退回其余路径）。
    """
    route = special_map_route(target)
    model = (route or {}).get("self_anchor")
    if not isinstance(model, dict):
        return None
    samples = model.get("samples")
    if not isinstance(samples, list) or not samples:
        return None
    size = model.get("window")
    win_w = win_h = None
    if isinstance(size, (list, tuple)) and len(size) == 2:
        rect = getattr(win, "rect", None)
        if rect is None:
            return None
        try:
            if (abs(int(rect.width) - int(size[0]))
                    > SPECIAL_ANCHOR_MODEL_WIN_TOL
                    or abs(int(rect.height) - int(size[1]))
                    > SPECIAL_ANCHOR_MODEL_WIN_TOL):
                return None
            win_w, win_h = int(size[0]), int(size[1])
        except Exception:  # noqa: BLE001 —— 窗口尺寸不可读按不匹配处理
            return None
    wx, wy = pos[1], pos[2]
    cand = []
    for s in samples:
        if not isinstance(s, (list, tuple)) or len(s) < 4:
            continue
        try:
            d = math.hypot(float(s[0]) - wx, float(s[1]) - wy)
            sx, sy = float(s[2]), float(s[3])
        except (TypeError, ValueError):
            continue
        # 采集时相机跟踪跑飞（如传送黑屏期间）会记下窗口外的锚点：
        # 波月洞 B 区实测有 ay>1000 的样品，运行期命中会把点击点换算到
        # 屏幕外，必须丢弃（建图脚本同样过滤，这里兜底旧数据）。
        if win_w is not None and not (0 <= sx <= win_w and 0 <= sy <= win_h):
            continue
        if d <= SPECIAL_ANCHOR_MODEL_MAX_DIST:
            cand.append((d, sx, sy))
    if not cand:
        return None
    cand.sort(key=lambda t: t[0])
    wsum = ax = ay = 0.0
    for d, sx, sy in cand[:SPECIAL_ANCHOR_MODEL_K]:
        wgt = 1.0 / (d + 0.5)
        wsum += wgt
        ax += sx * wgt
        ay += sy * wgt
    return ax / wsum, ay / wsum


# ---- 任务坐标反查屏幕点（2026-11-24 用户规则）----
# 手动寻路到达后，目标名字可能被其他名字叠绘挡住（OCR 读不出来）、任务
# 小牌在地图边缘也不渲染时，按任务栏给的目标坐标反查当前画面屏幕点直接
# 点击。模型沿用洞内导航：world = diag(SX, SY) * screen + B，锚点模型给出
# 「当前世界坐标 → 角色屏幕位置」A → B = W - M*A，于是
#   screen(目标) = A + (目标坐标 - 当前坐标) / (SX, SY)
# 已有 self_anchor 数据的特殊地图（莲花洞/波月洞/火云洞/金兜洞）直接可用。
# 精度：停稳状态实测约 20px（2026-11-24 莲花洞 [38,50] 截图：模型锚点
# (712,411)，名字标签实测 (692,416)）；采集样品按 1s 间隔且含行走中相机
# 跟随滞后，行走状态误差可到 50px+——调用方必须在人物停稳后反查。
# 点击点再上移 SPECIAL_COORD_CLICK_UP 像素落到角色本体：坐标点对应 NPC
# 脚下（名字渲染在角色下方，与名字兜底同标定），名字文字本身点不中。
SPECIAL_COORD_CLICK_UP = 25


def special_coord_screen_point(win, map_name, x, y, emit=None):
    """特殊地图：目标世界坐标 → 当前画面屏幕点；不可用返回 None。

    map_name 传 None 时按当前 HUD 地名自动匹配特殊地图（诊断用；HUD 名
    可能 OCR 错字，按 _special_map_name_match 容错匹配）。
    仅当目标图是特殊地图（有描边 self_anchor 锚点模型）、当前 HUD 就在
    该图、模型覆盖当前坐标时返回 (sx, sy)。窗口尺寸不符/锚点缺失/位置
    读不到一律 None（调用方回退 Alt+A/名字 OCR 等其余兜底）。
    emit 可选，打印反查明细（角色坐标、锚点、屏幕点）。
    """
    if map_name and special_map_entry(map_name) is None:
        return None  # 普通地图没有锚点模型：先判掉，免得白读一次 HUD 坐标
    pos = _special_read_pos(win)
    if pos is None:
        return None
    if map_name:
        if not _special_map_name_match(pos[0], map_name):
            return None
    else:
        for key in SPECIAL_MAP_ENTRIES:
            if _special_map_name_match(pos[0], key):
                map_name = key
                break
        else:
            return None
    anchor = _special_model_anchor(win, map_name, pos)
    if anchor is None:
        return None
    sx = anchor[0] + (x - pos[1]) / SPECIAL_WALK_SX
    sy = (anchor[1] + (y - pos[2]) / SPECIAL_WALK_SY
          - SPECIAL_COORD_CLICK_UP)
    rect = getattr(win, "rect", None)
    if rect is not None:
        try:
            if not (0 <= sx <= int(rect.width)
                    and 0 <= sy <= int(rect.height)):
                return None
        except Exception:  # noqa: BLE001 —— 窗口尺寸不可读按不可用处理
            return None
    if emit:
        emit(f"坐标反查：角色 ({pos[1]},{pos[2]}) 屏幕锚点 "
             f"≈({anchor[0]:.0f},{anchor[1]:.0f}) → 目标 ({x},{y}) "
             f"屏幕点 ≈({sx:.0f},{sy:.0f})（已上移 "
             f"{SPECIAL_COORD_CLICK_UP}px 到角色本体）")
    return int(round(sx)), int(round(sy))


def _name_hit(text, name):
    """OCR 文本是否包含角色名（允许少量字符误识别）。"""
    if not text or not name:
        return False
    if name in text:
        return True
    n = len(name)
    if n < 3:  # 短名字不做模糊：错一个字就可能重名
        return False
    for i in range(0, len(text) - n + 1):
        seg = text[i:i + n]
        diff = sum(1 for a, b in zip(seg, name) if a != b)
        if diff <= SPECIAL_NAME_FUZZY_DIST:
            return True
    return False


def _name_label_anchor(img, name, box=None):
    """2x 放大 OCR 找角色名字标签，返回 (score, ax, ay) 或 None。

    名字标签显示在角色脚下（如「20「可乐可口」」），是小号彩色字：
    2026-11-23 实机实测全帧引擎时好时坏、区域快引擎基本读不到；裁剪后
    2x 放大交给 ocr_zoom 引擎稳定识别（2x 裁剪 0.07~0.5s、0.9 置信度），
    1 字误识别由 _name_hit 容忍。box 为搜索框（窗口坐标，默认游戏画面
    中部），返回坐标已映射回窗口坐标；ax=名字框中心，ay=名字框上沿-3。
    """
    if img is None or not name:
        return None
    l, t, r, b = box or SPECIAL_NAME_REGION
    l, t = int(l), int(t)
    w, h = max(1, int(r) - l), max(1, int(b) - t)
    try:
        crop = img.crop((l, t, l + w, t + h))
        crop = crop.resize((w * SPECIAL_NAME_ZOOM_SCALE,
                            h * SPECIAL_NAME_ZOOM_SCALE), Image.LANCZOS)
        items, _ = game_map.ocr_zoom(crop)
    except Exception:  # noqa: BLE001 —— 裁剪/OCR 失败按未找到处理
        return None
    best = None
    for text, rect, score in items:
        if score < SPECIAL_NAME_MIN_SCORE or not _name_hit(text, name):
            continue
        wl = l + rect[0] // SPECIAL_NAME_ZOOM_SCALE
        wt = t + rect[1] // SPECIAL_NAME_ZOOM_SCALE
        wr = l + rect[2] // SPECIAL_NAME_ZOOM_SCALE
        cand = (float(score), (wl + wr) // 2, wt - 3)
        if best is None or cand[0] > best[0]:
            best = cand
    return best


def _special_self_anchor(win, emit):
    """用角色名字标签估计角色屏幕位置（可选；找不到返回 None）。

    用户 2026-11-23 指出：导航缺了「角色在屏幕中的位置」，一直按远目标的
    屏幕点点击会点到空洞。名字标签可见时直接得到锚点 → B = W - M*A，
    免探针绕路；人多 OCR 不到名字时退回探针/热启动。标签是小号彩色字，
    用画面中部 2x 放大 + ocr_zoom 引擎识别（见 _name_label_anchor）。
    """
    m = re.search(r"「([^」]+)」（ID：", win.title or "")
    if not m:
        return None
    name = m.group(1)
    if not name:
        return None
    img = win.capture(force_activate=False)
    if img is None:
        return None
    best = _name_label_anchor(img, name)
    if best is None:
        return None
    emit(f"名字锚点：角色屏幕位置 ≈ ({best[1]},{best[2]}) "
         f"（置信度 {best[0]:.2f}）")
    return float(best[1]), float(best[2])


def _special_read_pos(win):
    """读 HUD 位置（截图/OCR 偶发失败时重试，最多 SPECIAL_WALK_POS_RETRIES）。"""
    for _ in range(SPECIAL_WALK_POS_RETRIES):
        pos = _read_pos(win)
        if pos is not None:
            return pos
        time.sleep(0.3)
    return None


def _special_settle_pos(win, pos):
    """到达判定后等人物停稳，返回最终 HUD 位置（最多约 2.5s）。

    连续步行时人物可能还在走向最后一个点击点：半路位置满足容差就宣布
    到达，日志坐标会和人物实际站位不一致（2026-11-24 用户实机反馈：
    日志 (34,41) 实际 (37,43)）。停稳后按最终位置复核，出了容差继续走。
    """
    last = pos
    same = 0
    for _ in range(10):
        time.sleep(0.25)
        p = _special_read_pos(win)
        if p is None:
            continue
        if (p[1], p[2]) == (last[1], last[2]):
            same += 1
            if same >= 3:
                break
        else:
            last = p
            same = 0
    return last


def _special_dialog_open(img):
    """洞内步行误点 NPC 时的对话框判定（延迟导入避免模块耦合）。"""
    try:
        from . import dialog_common
        return (dialog_common.dialog_frame_score(img)
                >= dialog_common.DIALOG_FRAME_THRESHOLD)
    except Exception:  # noqa: BLE001 —— 辅助判定失败按无对话框处理
        return False


def _special_close_dialog(win, emit):
    """误点 NPC 出对话框：点屏幕中间推进/关闭（最多 4 次）。返回是否关闭。"""
    for _ in range(4):
        _check_cancel()
        human_click(win, 689, 415)
        time.sleep(0.6)
        img = win.capture(force_activate=False)
        if img is None or not _special_dialog_open(img):
            emit("误点 NPC 对话框已关闭，继续步行")
            return True
    return False


def _special_wait_step(win, before, emit):
    """右键点击后等待位移起步/停稳，返回 (after_pos, moved)。

    洞内可能随机遇战斗（坐标不可读、人物不动）：战斗期间冻结等待。
    """
    deadline = time.time() + SPECIAL_WALK_MOVE_WAIT
    last = before
    last_change = time.time()
    started = False
    while time.time() < deadline + 4.0:
        _check_cancel()
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            deadline += 1.0
            time.sleep(1.0)
            continue
        pos = game_map.read_map_position(None, img=img) if img is not None else None
        if pos is None:
            time.sleep(0.4)
            continue
        if not started:
            if before is None or pos != before:
                started = True
                last = pos
                last_change = time.time()
            elif time.time() > deadline:
                return pos, False
        else:
            if pos != last:
                last = pos
                last_change = time.time()
            elif time.time() - last_change >= SPECIAL_WALK_SETTLE:
                return pos, True
        time.sleep(0.4)
    if not started:
        # 等待期间位置 OCR 偶发读失败：收尾再重试一次，避免把已移动误判为无位移
        pos = _special_read_pos(win)
        if pos is not None and before is not None and pos != before:
            return pos, True
    return last, started


def _special_frame_shift(img0, img1):
    """相位相关测相机位移（场景内容从 img0 到 img1 的平移像素）。

    返回 (dx, dy, resp) 或 None（帧无效/响应度不足）。2026-11-23 校准：
    两对已知相机位移 (-224,-90)、(-259,+90) 的实机帧，相位相关返回值
    与模板匹配完全一致（<1px），响应度 0.3~0.8；异形洞内相机随人物
    滚动，必须每步测位移才能维持屏幕↔世界映射。
    """
    if img0 is None or img1 is None:
        return None
    l, t, r, b = SPECIAL_WALK_SHIFT_REGION
    try:
        a = cv2.cvtColor(np.asarray(img0)[t:b, l:r],
                         cv2.COLOR_RGB2GRAY).astype(np.float32)
        c = cv2.cvtColor(np.asarray(img1)[t:b, l:r],
                         cv2.COLOR_RGB2GRAY).astype(np.float32)
        (dx, dy), resp = cv2.phaseCorrelate(a, c)
    except Exception:  # noqa: BLE001 —— 测位移失败按不可用处理
        return None
    if resp < SPECIAL_WALK_SHIFT_MIN_RESP:
        return None
    # 超出区域半幅（1100x530 → ±550/±265）的位移会混叠成错误值
    if abs(dx) > 500 or abs(dy) > 240:
        return None
    return float(dx), float(dy), float(resp)


def _special_apply_shift(B, shift):
    """相机内容平移 shift → 更新屏幕↔世界平移 B（world = M*screen + B）。

    相机看的方向变了，同一屏幕像素对应的世界坐标随之改变：
    B_new = B - M * shift。
    """
    if B is None or shift is None:
        return B
    return (B[0] - SPECIAL_WALK_SX * shift[0],
            B[1] - SPECIAL_WALK_SY * shift[1])


def _special_probe(win, target, emit, deadline):
    """探针：右键地面点 → 到达坐标 + 相机位移，标定点击时刻的 B。

    取第一个有位移的探针点；B_click 用到达坐标反解（假设走到点击点），
    再用同一次移动的相机位移换算到当前视图。
    """
    for px, py in SPECIAL_WALK_PROBES:
        _check_cancel()
        if time.time() > deadline:
            break
        before = _special_read_pos(win)
        if before is None or not _special_map_name_match(before[0], target):
            continue
        img0 = win.capture(force_activate=False)
        human_click(win, px, py, button="secondary")
        after, moved = _special_wait_step(win, before, emit)
        img1 = win.capture(force_activate=False)
        if moved and after is not None and after != before:
            B_click = (after[1] - SPECIAL_WALK_SX * px,
                       after[2] - SPECIAL_WALK_SY * py)
            shift = _special_frame_shift(img0, img1)
            B = _special_apply_shift(B_click, shift)
            s_txt = "无" if shift is None else (
                f"({shift[0]:.1f},{shift[1]:.1f}) resp={shift[2]:.2f}")
            emit(f"洞内步行探针 ({px},{py})：{before[1:]} → {after[1:]}，"
                 f"相机位移 {s_txt}，标定 B=({B[0]:.2f},{B[1]:.2f})")
            return B
        img = win.capture(force_activate=False)
        if img is not None and _special_dialog_open(img):
            _special_close_dialog(win, emit)
        emit(f"洞内步行探针 ({px},{py}) 无位移，换点")
    return None


def _valid_special_route(route):
    """校验路网条目：分区路网、人工描边轨迹或「中心 + 非空臂」三者其一。"""
    if not isinstance(route, dict):
        return None
    if len(_special_zones(route)) >= 2:
        return route
    trace = route.get("trace")
    if isinstance(trace, list) and len(trace) >= 2:
        return route
    center = route.get("center")
    arms = route.get("arms")
    if not (isinstance(center, (list, tuple)) and len(center) == 2
            and isinstance(arms, dict) and arms):
        return None
    return route


def _special_trace_waypoints(route, cur, x, y):
    """人工描边轨迹 → 途经点：沿描边折线图走，避免穿空洞。

    描边点是用户实际操作走出来的真实可走位置；相邻采样点连边即安全
    路径。当前点/目标各取最近描边点，Dijkstra 走折线（含闭环回边），
    最后从最近描边点直接到目标。
    """
    nodes = []
    for p in route.get("trace") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            nodes.append((int(p[0]), int(p[1])))
    if len(nodes) < 2:
        return None
    n = len(nodes)
    adj = [[] for _ in range(n)]
    for i in range(n - 1):
        a, b = nodes[i], nodes[i + 1]
        w = abs(a[0] - b[0]) + abs(a[1] - b[1])
        adj[i].append((i + 1, w))
        adj[i + 1].append((i, w))
    # 描边首尾仅在同一位置/相邻采样内才补闭环边；不凭距离近就跨接，
    # 避免把用户没实际走过的两端当成通路（可能跨空洞）
    a, b = nodes[0], nodes[-1]
    if abs(a[0] - b[0]) + abs(a[1] - b[1]) <= 2:
        w = abs(a[0] - b[0]) + abs(a[1] - b[1])
        adj[0].append((n - 1, w))
        adj[n - 1].append((0, w))

    def _nearest(px, py):
        best, bd = None, None
        for i, (nx, ny) in enumerate(nodes):
            d = abs(nx - px) + abs(ny - py)
            if bd is None or d < bd:
                best, bd = i, d
        return best, bd

    start, _sd = _nearest(cur[1], cur[2]) if cur else (0, 0)
    goal, _gd = _nearest(x, y)
    # Dijkstra（节点少，朴素 O(n^2) 足够）
    INF = float("inf")
    dist = [INF] * n
    prev = [-1] * n
    used = [False] * n
    dist[start] = 0
    for _ in range(n):
        u = -1
        for i in range(n):
            if not used[i] and (u < 0 or dist[i] < dist[u]):
                u = i
        if u < 0 or dist[u] == INF:
            break
        used[u] = True
        for v, w in adj[u]:
            if dist[u] + w < dist[v]:
                dist[v] = dist[u] + w
                prev[v] = u
    if dist[goal] == INF:
        return None
    seq = []
    u = goal
    while u >= 0:
        seq.append(nodes[u])
        if u == start:
            break
        u = prev[u]
    seq.reverse()
    if (seq and cur
            and abs(seq[0][0] - cur[1]) <= 2
            and abs(seq[0][1] - cur[2]) <= 2):
        seq.pop(0)
    if not seq or seq[-1] != (x, y):
        seq.append((x, y))
    return seq or [(x, y)]


def _load_builtin_routes():
    """内置探图路网（res/special_map_routes.json，按 mtime 热更新）。"""
    global _ROUTE_TABLE, _ROUTE_TABLE_MTIME
    from ascript.windows.system import R
    path = Path(R.res(SPECIAL_MAP_ROUTES_FILE))
    mtime = None
    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = None
    if _ROUTE_TABLE is not None and mtime == _ROUTE_TABLE_MTIME:
        return _ROUTE_TABLE
    table = {}
    if mtime is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("maps"), dict):
                table = data
        except Exception:
            table = {}
    _ROUTE_TABLE = table
    _ROUTE_TABLE_MTIME = mtime
    return table


def special_map_route(name):
    """读取特殊地图探图路网：角色数据优先，其次内置 res 表；无返回 None。"""
    from ..core import user_data
    try:
        data = user_data.read_special_map_routes()
    except Exception:  # noqa: BLE001 —— 路网读取失败按无路网处理
        data = {}
    route = (data.get("maps") or {}).get(name) if isinstance(data, dict) else None
    route = _valid_special_route(route)
    if route is not None:
        return route
    table = _load_builtin_routes()
    return _valid_special_route((table.get("maps") or {}).get(name))


def save_special_map_route(name, route):
    """写特殊地图路网（读-改-写角色数据；失败只影响下次探图复用）。"""
    from ..core import user_data
    try:
        data = user_data.read_special_map_routes()
        maps = data.get("maps") if isinstance(data, dict) else None
        maps = dict(maps) if isinstance(maps, dict) else {}
        maps[name] = route
        data = {"version": SPECIAL_MAP_ROUTES_VERSION, "maps": maps}
        user_data.write_special_map_routes(data)
        return True
    except Exception:  # noqa: BLE001 —— 持久化失败不影响本次导航
        return False


def _special_segment_distance(px, py, ax, ay, bx, by):
    """点到线段最近距离与最近点（世界坐标）。"""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5, (ax, ay)
    t = ((px - ax) * dx + (py - ay) * dy) / float(dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5, (cx, cy)


def _special_boundary_contains(boundary, px, py,
                               tol=SPECIAL_TRACE_BOUNDARY_TOL):
    """描边多边形内部判定（自动闭合；边界 tol 格内也算内部）。

    用户 2026-11-23 规则：描边数据以内都可以走、以外不能走。描边是
    用户实际走出来的边界折线，用它做点包含判定，比沿折线绕一圈快得多。
    """
    poly = [(float(p[0]), float(p[1])) for p in boundary
            if isinstance(p, (list, tuple)) and len(p) == 2]
    n = len(poly)
    if n < 3:
        return False
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        d, _ = _special_segment_distance(px, py, ax, ay, bx, by)
        if d <= tol:
            return True
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            xi = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
            if px < xi:
                inside = not inside
    return inside


def _special_boundary_nearest(boundary, px, py):
    """描边边界上离 (px,py) 最近的点（目标在描边外时落到最近可走点）。"""
    poly = [(float(p[0]), float(p[1])) for p in boundary
            if isinstance(p, (list, tuple)) and len(p) == 2]
    n = len(poly)
    best = None
    if n < 2:
        return None
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        d, pt = _special_segment_distance(px, py, ax, ay, bx, by)
        if best is None or d < best[0]:
            best = (d, pt)
    return best[1] if best else None


def _special_point(p):
    """把 (x, y) / [x, y] 归一化成浮点坐标；非法值返回 None。"""
    try:
        return float(p[0]), float(p[1])
    except (TypeError, ValueError, IndexError):
        return None


def _special_portal_points(points):
    """归一化传送/避让点参数：单点 (x,y) 或点列表 → [(x,y), ...]。

    旧调用方传单个光圈点 (x,y)；多分区路网需要同时避让区内多个传送
    触发点，传 [[x,y], [x,y]]（或元组列表）。两种格式都接受。
    """
    if points is None:
        return []
    if isinstance(points, (list, tuple)):
        one = _special_point(points)
        if one is not None:
            return [one]
        out = []
        for p in points:
            q = _special_point(p)
            if q is not None:
                out.append(q)
        return out
    return []


def _special_segment_inside(boundary, p0, p1, step=1.5, portal=None):
    """线段是否全程在描边内（含边界容差；portal 传送点不可被穿过）。

    端点都在描边内 ≠ 直线可走：十字形地图两臂之间是空洞（边界外），
    直线穿空洞时客户端会走到走廊尽头来回振荡（2026-11-23 实机：
    莲花洞 (34,20)→(30,56) 直线穿过两臂间空洞，连续 30+ 次点击原地
    打转）。此时改走人工描边路网绕行。
    portal：洞内传送光圈/洞口触发点（走进会退出山洞或切到另一分区），
    支持单点或点列表；端点落在传送点避让半径内的路段放行（进洞落点
    就在光圈上），两端都在半径外时路段不得进入避让半径。
    """
    x0, y0 = p0
    x1, y1 = p1
    for pxp, pyp in _special_portal_points(portal):
        rr = SPECIAL_PORTAL_AVOID_R ** 2
        start_in = (x0 - pxp) ** 2 + (y0 - pyp) ** 2 <= rr
        end_in = (x1 - pxp) ** 2 + (y1 - pyp) ** 2 <= rr
        if not (start_in or end_in):
            d, _pt = _special_segment_distance(pxp, pyp, x0, y0, x1, y1)
            if d < SPECIAL_PORTAL_AVOID_R:
                return False
    dist = math.hypot(x1 - x0, y1 - y0)
    n = max(1, int(dist / step))
    for i in range(n + 1):
        t = i / float(n)
        if not _special_boundary_contains(
                boundary, x0 + (x1 - x0) * t, y0 + (y1 - y0) * t):
            return False
    return True


def _special_boundary_waypoints(boundary, cur, dst, portal=None):
    """边界内几何最短路（扫描线栅格 + A*），绕开两臂之间的空洞。

    2026-11-24 实机教训：链式描边路网（_special_trace_waypoints）只能
    沿用户行走的先后顺序走，全图来回描边后跨臂路线会绕出 68 个途经点
    的之字路（两分钟走不完）。改用边界多边形内部栅格 A*：路线按几何
    方向最短，只保证全程在描边内（内部障碍仍由客户端自动绕障，与直线
    导航一致）。返回途经点（末项为 dst）；无路返回 None。
    """
    poly = [(float(p[0]), float(p[1])) for p in boundary
            if isinstance(p, (list, tuple)) and len(p) == 2]
    if len(poly) < 3 or cur is None:
        return None
    minx = int(math.floor(min(p[0] for p in poly)))
    maxx = int(math.ceil(max(p[0] for p in poly)))
    miny = int(math.floor(min(p[1] for p in poly)))
    maxy = int(math.ceil(max(p[1] for p in poly)))
    # 扫描线填充多边形内部（严格内部；起点/终点取最近可走格）
    walkable = set()
    for gy in range(miny, maxy + 1):
        xs = []
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            if (y1 > gy) != (y2 > gy):
                xs.append(x1 + (gy - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for j in range(0, len(xs) - 1, 2):
            for gx in range(int(math.ceil(xs[j])),
                            int(math.floor(xs[j + 1])) + 1):
                walkable.add((gx, gy))
    portals = _special_portal_points(portal)
    if portals:
        # 光圈/洞口格子不可走：路线绕开传送点（走进会退出山洞/切区）
        rr = SPECIAL_PORTAL_AVOID_R ** 2
        walkable = {c for c in walkable
                    if all((c[0] - pxp) ** 2 + (c[1] - pyp) ** 2 > rr
                           for pxp, pyp in portals)}
    if not walkable:
        return None

    def _nearest_cell(px, py):
        best = None
        for cell in walkable:
            d = (cell[0] - px) ** 2 + (cell[1] - py) ** 2
            if best is None or d < best[0]:
                best = (d, cell)
        return best[1] if best else None

    start = _nearest_cell(cur[0], cur[1])
    goal = _nearest_cell(dst[0], dst[1])
    if start is None or goal is None:
        return None

    def _h(c):
        dx = abs(c[0] - goal[0])
        dy = abs(c[1] - goal[1])
        return (dx + dy) + (math.sqrt(2) - 2.0) * min(dx, dy)

    def _clear(n, dx, dy):
        if n not in walkable:
            return False
        if dx and dy:   # 对角不切角
            return ((n[0] - dx, n[1]) in walkable
                    and (n[0], n[1] - dy) in walkable)
        return True

    openh = [(0.0, 0.0, start)]
    came = {start: None}
    gscore = {start: 0.0}
    closed = set()
    found = False
    while openh:
        _f, g, c = heapq.heappop(openh)
        if c in closed:
            continue
        closed.add(c)
        if c == goal:
            found = True
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                       (1, 1), (1, -1), (-1, 1), (-1, -1)):
            n = (c[0] + dx, c[1] + dy)
            if n in closed or not _clear(n, dx, dy):
                continue
            ng = g + (1.41421356 if dx and dy else 1.0)
            if ng < gscore.get(n, 1e18):
                gscore[n] = ng
                came[n] = c
                heapq.heappush(openh, (ng + _h(n), ng, n))
    if not found:
        return None
    path = []
    c = goal
    while c is not None:
        path.append(c)
        c = came.get(c)
    path.reverse()
    if (path and abs(path[0][0] - cur[0]) <= 1
            and abs(path[0][1] - cur[1]) <= 1):
        path.pop(0)
    if not path:
        return None
    # 只保留转向点（直线段合并），终点必留
    wps = []
    for i in range(1, len(path)):
        if i >= 2:
            d = (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
            prev = (path[i - 1][0] - path[i - 2][0],
                    path[i - 1][1] - path[i - 2][1])
            if d != prev:
                wps.append(path[i - 1])
    wps.append(path[-1])
    wps = [w for w in wps
           if abs(w[0] - cur[0]) + abs(w[1] - cur[1]) > 2]
    target = (int(round(dst[0])), int(round(dst[1])))
    if not wps or wps[-1] != target:
        wps.append(target)
    return wps or [target]


def _special_route_waypoints(route, cur, x, y, portal=None):
    """按路网生成途经点：人工描边轨迹优先，其次十字中心/臂节点。

    人工描边边界（boundary/trace）语义：以内皆可走、以外不可走——
    边界内目标直线走过去（客户端绕障）；但直线**全程**要留在边界内，
    穿两臂间空洞时改走描边路网绕行。边界外目标落最近边界点（同样
    检查直线是否穿空洞/传送光圈）。无描边时退「同臂沿节点、跨臂经
    中心」的自动探图路网。portal 为洞内传送光圈（路线不可穿过）。
    返回列表最后一项恒为最终目标（可能是钳制后的点）。
    """
    bnd = route.get("boundary")
    has_boundary = isinstance(bnd, list) and len(bnd) >= 3
    boundary = bnd
    if not has_boundary:
        trace = route.get("trace")
        if isinstance(trace, list) and len(trace) >= 3:
            boundary = trace
    trace = route.get("trace")
    has_trace = isinstance(trace, list) and len(trace) >= 2
    if boundary:
        if _special_boundary_contains(boundary, x, y):
            dst = (x, y)
        else:
            nearest = _special_boundary_nearest(boundary, x, y)
            dst = ((int(round(nearest[0])), int(round(nearest[1])))
                   if nearest is not None else (x, y))
        cur_xy = (cur[1], cur[2]) if cur else None
        if cur_xy is None or _special_segment_inside(
                boundary, cur_xy, dst, portal=portal):
            return [dst]
        # 直线穿空洞/光圈：边界内栅格 A*（几何最短路）；失败再退链式路网
        if has_boundary:
            wps = _special_boundary_waypoints(boundary, cur_xy, dst,
                                              portal=portal)
            if wps:
                return wps
        if has_trace:
            wps = _special_trace_waypoints(route, cur, dst[0], dst[1])
            if wps:
                return wps
        return [dst]
    if has_trace:
        return _special_trace_waypoints(route, cur, x, y)
    center = tuple(int(v) for v in route["center"])
    arms = route["arms"]
    if cur is None:
        return None

    def _nearest(nodes, px, py):
        best = None
        bd = None
        for i, n in enumerate(nodes):
            d = abs(int(n[0]) - px) + abs(int(n[1]) - py)
            if bd is None or d < bd:
                bd, best = d, i
        return best, bd

    best_target = None
    best_cur = None
    for key, nodes in arms.items():
        if not nodes:
            continue
        ti, td = _nearest(nodes, x, y)
        ci, cd = _nearest(nodes, cur[1], cur[2])
        if ti is not None and (best_target is None or td < best_target[0]):
            best_target = (td, key, ti)
        if ci is not None and (best_cur is None or cd < best_cur[0]):
            best_cur = (cd, key, ci)
    if best_target is None or best_target[0] > SPECIAL_ROUTE_MAX_NODE_DIST:
        return None
    wps = []
    if best_cur is not None and best_cur[1] == best_target[1]:
        nodes = arms[best_target[1]]
        idx = best_cur[2]
        step = 1 if best_target[2] >= idx else -1
        while True:
            wps.append(tuple(int(v) for v in nodes[idx]))
            if idx == best_target[2]:
                break
            idx += step
    else:
        cur_nodes = arms[best_cur[1]] if best_cur else None
        cur_idx = best_cur[2] if best_cur else None
        if cur_nodes and cur_idx is not None:
            idx = cur_idx
            while idx >= 0:
                wps.append(tuple(int(v) for v in cur_nodes[idx]))
                idx -= 1
        if center not in wps:
            wps.append(center)
        tgt_nodes = arms[best_target[1]]
        idx = 0
        while idx <= best_target[2]:
            node = tuple(int(v) for v in tgt_nodes[idx])
            if not wps or wps[-1] != node:
                wps.append(node)
            idx += 1
    if wps and abs(wps[0][0] - cur[1]) <= 2 and abs(wps[0][1] - cur[2]) <= 2:
        wps.pop(0)
    if not wps or wps[-1] != (x, y):
        wps.append((x, y))
    return wps or [(x, y)]


def _special_boundary_distance(boundary, px, py):
    """点到描边边界（闭合多边形各边）的最近距离；无边界返回 inf。"""
    poly = [(float(p[0]), float(p[1])) for p in boundary
            if isinstance(p, (list, tuple)) and len(p) == 2]
    n = len(poly)
    if n < 2:
        return float("inf")
    best = float("inf")
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i + 1) % n]
        d, _pt = _special_segment_distance(px, py, ax, ay, bx, by)
        if d < best:
            best = d
    return best


def _special_zones(route):
    """路网分区列表（zones: 每区独立 boundary/trace）；无有效分区返回 []。"""
    out = []
    zones = (route or {}).get("zones") if isinstance(route, dict) else None
    if not isinstance(zones, list):
        return out
    for z in zones:
        if not isinstance(z, dict):
            continue
        b = z.get("boundary")
        if isinstance(b, list) and len(b) >= 3:
            out.append(z)
    return out


def _special_zone_name(zone):
    """分区名（路网 links 用它引用分区）；缺失返回空串。"""
    if not isinstance(zone, dict):
        return ""
    return str(zone.get("name") or "").strip()


def _special_zone_at(zones, px, py, allow_nearest=False):
    """坐标所在分区：严格包含优先；allow_nearest=True 时退到最近边界分区。

    波月洞 A/B 两区在同一坐标平面上但互不相邻（中间是空洞），用边界
    包含判定当前区/目标区即可；目标/落点略在描边外时取最近边界所在区。
    """
    if not zones:
        return None
    hits = [z for z in zones
            if _special_boundary_contains(z["boundary"], px, py)]
    if hits:
        if len(hits) == 1:
            return hits[0]
        return min(hits, key=lambda z: _special_boundary_distance(
            z["boundary"], px, py))
    if allow_nearest:
        return min(zones, key=lambda z: _special_boundary_distance(
            z["boundary"], px, py))
    return None


def _special_zone_links(route):
    """路网传送链接（from_zone → to_zone，from 为触发点/land 为落点）。"""
    names = {_special_zone_name(z) for z in _special_zones(route)}
    out = []
    links = (route or {}).get("links") if isinstance(route, dict) else None
    if not isinstance(links, list):
        return out
    for link in links:
        if not isinstance(link, dict):
            continue
        a = str(link.get("from_zone") or "").strip()
        b = str(link.get("to_zone") or "").strip()
        p = _special_point(link.get("from"))
        if a not in names or b not in names or p is None:
            continue
        land = _special_point(link.get("land") or link.get("to")) or p
        out.append({"from_zone": a, "to_zone": b, "from": p, "land": land,
                    "land_recalibrate": bool(
                        link.get("land_recalibrate"))})
    return out


def _special_zone_path(links, src, dst):
    """分区传送图 BFS：返回从 src 到 dst 的链接列表；不可达返回 None。"""
    if src == dst:
        return []
    adj = {}
    for link in links:
        adj.setdefault(link["from_zone"], []).append(link)
    prev = {src: None}
    queue = [src]
    while queue:
        zone = queue.pop(0)
        for link in adj.get(zone, []):
            nxt = link["to_zone"]
            if nxt in prev:
                continue
            prev[nxt] = (zone, link)
            queue.append(nxt)
    if dst not in prev:
        return None
    hops = []
    cur = dst
    while prev.get(cur) is not None:
        pz, link = prev[cur]
        hops.append(link)
        cur = pz
    hops.reverse()
    return hops


def _remember_special_view(win, target, B, pos, zone_name=None):
    """记住本次导航结束视图（帧+B+分区）：下次同区导航热启动，免探针绕路。

    分区名必须一并记录：传送切区后场景/相机全变，跨区的旧帧+B 不能
    复用于相位相关（会得到错误位移），见 _walk_special_coordinate 的
    热启动条件。
    """
    global _LAST_SPECIAL_VIEW
    try:
        img = win.capture(force_activate=False)
    except Exception:  # noqa: BLE001 —— 记忆失败只影响下次热启动
        img = None
    if img is not None and B is not None:
        _LAST_SPECIAL_VIEW = {"map": target, "zone": zone_name, "B": B,
                              "frame": img, "pos": pos}


def _walk_special_coordinate(win, target, x, y, emit, use_route=True,
                             verbose=False, zone=None, avoid=None,
                             tolerance=None, timeout=None,
                             zones=None, zone_state=None,
                             recalibrate=False):
    """特殊地图洞内按坐标步行：逐帧相机跟踪 + 连续右键步行。

    异形洞穴（莲花洞为斜十字）相机随人物滚动，单次标定的屏幕↔世界
    平移 B 只对当时视图有效；本实现每轮用相位相关测相机位移并更新 B
    （world = M*screen + B），长距离/跨区点击仍准确。不依赖角色名字。
    有该图人工描边边界时：目标在边界内直接直线走过去（客户端自动
    绕障），目标在边界外落到最近可走边界点——绝不点空洞（用户
    2026-11-23）。**连续步行**（用户 2026-11-23）：首步停稳测一次映射
    偏差，之后每 `SPECIAL_WALK_CLICK_INTERVAL` 秒沿目标方向重新右击，
    人物不必走到点击点停稳；到达 ±3 格即成功。返回 (ok, info)。
    verbose=True（诊断卡片/探图）：逐步输出「洞内步行 N」点击明细；
    生产默认不输出（用户 2026-11-24：正式环境每步一条刷屏），只保留
    到达/修正/无位移等关键节点。

    分区路网参数（波月洞 A/B 等，见 _walk_special_zones）：
    zone：限定本次步行只用该分区的 boundary/trace（缺省用整图路网）；
    avoid：区内其它传送触发点，路线不得穿过（与进出光圈同样避让）；
    tolerance/timeout：本次到达容差/超时（走到传送触发点要更近更快）；
    zones/zone_state：传入时监视「位置进入其它分区」，命中即记录到
    zone_state 并提前返回（传送可能发生在走到触发点途中）；
    recalibrate：传送落点/贴地图边缘起步时不信任描边锚点模型（转场期
    采集的样品与稳态相机不一致），先用名字标签 OCR，失败再强制探针。
    """
    tol = SPECIAL_WALK_TOLERANCE if tolerance is None else int(tolerance)
    walk_timeout = SPECIAL_WALK_TIMEOUT if timeout is None else float(timeout)
    t0 = time.time()
    deadline = t0 + walk_timeout
    pos0 = _special_read_pos(win)
    if (pos0 is not None and _special_map_name_match(pos0[0], target)
            and abs(pos0[1] - x) <= tol
            and abs(pos0[2] - y) <= tol):
        emit(f"已在{target} ({pos0[1]},{pos0[2]})，误差 "
             f"({pos0[1] - x:+d},{pos0[2] - y:+d})，无需步行")
        return True, (f"已到达{target} ({pos0[1]},{pos0[2]})，"
                      f"误差 ({pos0[1] - x:+d},{pos0[2] - y:+d})")
    zone_name = _special_zone_name(zone) if isinstance(zone, dict) else ""
    zone_label = f" {zone_name} 区" if zone_name else ""

    def _zone_teleported(p):
        """位置已进入其它分区 → 记录传送并返回 True（仅触发点步行启用）。"""
        if zones is None or zone_state is None or not zone_name or p is None:
            return False
        z_now = _special_zone_at(zones, p[1], p[2])
        if z_now is None:
            return False
        name_now = _special_zone_name(z_now)
        if not name_now or name_now == zone_name:
            return False
        zone_state["teleported"] = True
        zone_state["pos"] = p
        zone_state["zone"] = z_now
        emit(f"已传送到 {name_now} 区 ({p[1]},{p[2]})")
        return True
    emit(f"{target}{zone_label} 洞内按坐标步行 → ({x},{y})（相机位移跟踪，"
         f"容差 ±{tol} 格）")
    _entry = special_map_entry(target)
    portal = _entry.get("portal") if _entry else None
    # 传送光圈 + 区内其它洞口触发点统一为避让点列表（单点写法兼容）
    portal = _special_portal_points(portal) + _special_portal_points(avoid)
    route = special_map_route(target) if use_route else None
    active_route = zone if isinstance(zone, dict) else route
    waypoints = [(x, y)]
    final_target = (x, y)

    def _describe_waypoints(wps, prefix=""):
        """日志：描边内直线 / 描边外落到最近可走点 / 沿途经点走。"""
        nonlocal final_target
        final_target = (int(wps[-1][0]), int(wps[-1][1]))
        if final_target != (x, y):
            emit(f"{prefix}目标 ({x},{y}) 在描边外，按最近可走点 "
                 f"{final_target} 导航")
        elif len(wps) == 1:
            emit(f"{prefix}{target} 目标在描边范围内，直线导航")
        else:
            emit(f"{prefix}使用{target}探图路网：{len(wps)} 个途经点")

    if use_route and pos0 is not None and active_route is not None:
        wps = _special_route_waypoints(active_route, pos0, x, y, portal=portal)
        if wps:
            waypoints = wps
            _describe_waypoints(wps)

    def _refresh_waypoints(tag):
        """探针/重探针会移动人物：按探针后的实际位置重算途经点。"""
        nonlocal waypoints
        if not use_route or active_route is None:
            return
        pos_now = _special_read_pos(win)
        if pos_now is None:
            return
        wps = _special_route_waypoints(active_route, pos_now, x, y,
                                       portal=portal)
        if wps:
            waypoints = wps
            _describe_waypoints(wps, prefix=f"{tag}：")

    B = None
    name_tried = False
    probe_tried = False
    if recalibrate:
        # 传送落点/贴地图边缘起步：描边锚点样品多在场景切换瞬间采集
        # （相机还在转场或贴地图边缘），与稳态位置不一致——波月洞 B→A
        # 实机模型偏差约 (150,155)px，按模型点击会点到墙/屏幕边缘，连点
        # 无位移。名字标签 OCR 便宜且反映当前真实位置；失败再强制探针，
        # 两者都不行才退回锚点模型。
        if pos0 is not None:
            name_tried = True
            anchor = _special_self_anchor(win, emit)
            if anchor is not None:
                B = (pos0[1] - SPECIAL_WALK_SX * anchor[0],
                     pos0[2] - SPECIAL_WALK_SY * anchor[1])
                emit(f"传送后名字锚点重标定 → B=({B[0]:.2f},{B[1]:.2f})")
        if B is None:
            probe_tried = True
            B = _special_probe(win, target, emit, deadline)
            if B is not None:
                emit("传送后探针重标定完成")
            else:
                emit("传送后名字/探针标定失败，退回描边锚点模型")
    if B is None and pos0 is not None:
        # 描边采集的锚点模型优先（免 OCR/探针，人多也不受影响）
        anchor = _special_model_anchor(win, target, pos0)
        if anchor is not None:
            B = (pos0[1] - SPECIAL_WALK_SX * anchor[0],
                 pos0[2] - SPECIAL_WALK_SY * anchor[1])
            emit(f"描边锚点模型：角色屏幕 ≈({anchor[0]:.0f},{anchor[1]:.0f})"
                 f" → B=({B[0]:.2f},{B[1]:.2f})")
        if B is None and not name_tried:
            anchor = _special_self_anchor(win, emit)
            if anchor is not None:
                B = (pos0[1] - SPECIAL_WALK_SX * anchor[0],
                     pos0[2] - SPECIAL_WALK_SY * anchor[1])
                emit(f"名字锚点标定 → B=({B[0]:.2f},{B[1]:.2f})")
    lv = _LAST_SPECIAL_VIEW
    if (B is None and lv.get("map") == target and lv.get("frame") is not None
            and lv.get("B") is not None
            and (not zone_name or lv.get("zone") == zone_name)):
        img_now = win.capture(force_activate=False)
        shift = _special_frame_shift(lv["frame"], img_now)
        if shift is not None and shift[2] >= 0.20:
            B = _special_apply_shift(lv["B"], shift)
            emit(f"沿用上次相机标定（位移 ({shift[0]:.1f},{shift[1]:.1f}) "
                 f"resp={shift[2]:.2f}）→ B=({B[0]:.2f},{B[1]:.2f})")
    if B is None:
        if probe_tried:
            return False, "洞内无法定位（探针点击均无位移）"
        B = _special_probe(win, target, emit, deadline)
        if B is None:
            return False, "洞内无法定位（探针点击均无位移）"
    _refresh_waypoints("标定后")

    # 可见性检查用边界（只点视窗内且直线不穿空洞的路线点）
    route_boundary = None
    if use_route and isinstance(active_route, dict):
        _b = active_route.get("boundary")
        if isinstance(_b, list) and len(_b) >= 3:
            route_boundary = _b

    misses = 0
    read_misses = 0
    wp_fail = 0
    miss_idx = 0
    retry_offsets = ((0, 0), (28, 0), (-28, 0), (0, 24), (0, -24),
                     (20, 20), (-20, -20))
    cur_wp = None
    wp_best_dist = 10 ** 9
    wp_stall = 0
    aim_bias = (0.0, 0.0)   # 点击点世界修正（首步停稳测一次，之后保持）
    aim_done = not SPECIAL_WALK_AIM_CALIB_FIRST
    prev_img = None
    prev_pos = None
    still_polls = 0
    last_click_t = 0.0
    clicks = 0
    direct_best = None
    direct_stuck_t = time.time()
    direct_rescued = False
    endpoint_last = None     # 上次终点复核查过的站位（同一站位不重复停稳）
    last = None
    # 连续步行：每轮都算点击点并右击，不再等走到位停稳。
    for _step in range(SPECIAL_WALK_MAX_STEPS * 4):
        _check_cancel()
        if time.time() > deadline:
            return False, f"步行超时（最后位置 {last}）"
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            deadline += 1.0
            time.sleep(1.0)
            continue
        # 逐帧相机跟踪：连续走时相机一直在滚动，B 必须对应本帧，
        # 否则点击点会按旧视图换算。
        shift_ok = True
        if img is not None and prev_img is not None:
            shift = _special_frame_shift(prev_img, img)
            if shift is not None:
                B = _special_apply_shift(B, shift)
            else:
                shift_ok = False
        if img is not None:
            prev_img = img
        pos = game_map.read_map_position(None, img=img) if img is not None else None
        if pos is None:
            pos = _special_read_pos(win)
        if pos is None:
            read_misses += 1
            if read_misses >= 6:
                return False, f"洞内位置读取失败（最后位置 {last}）"
            continue
        read_misses = 0
        if not _special_map_name_match(pos[0], target):
            return False, (f"洞内步行时场景变化：{pos[0]} "
                           f"({pos[1]},{pos[2]})")
        if _zone_teleported(pos):
            # 触发点途中/到达瞬间就完成传送：本次步行目标已不存在，
            # 交回 _walk_special_zones 从落点继续下一段
            return True, f"已传送到{_special_zone_name(zone_state['zone'])}区"
        if not shift_ok:
            # 相位相关不可用（画面变化大/遮挡）：用当前站位查锚点模型
            # 重标定 B，避免点击点按旧视图换算。
            alt = _special_model_anchor(win, target, pos)
            if alt is not None:
                B = (pos[1] - SPECIAL_WALK_SX * alt[0],
                     pos[2] - SPECIAL_WALK_SY * alt[1])
                emit("  相机位移不可测 → 按描边锚点模型重标定 "
                     f"B=({B[0]:.2f},{B[1]:.2f})")
            else:
                emit("  相机位移不可测（本次不更新 B）")
        # 角色屏幕位置越界 = B 已被错误位移（长距离/传送/混叠）带偏：
        # 用锚点模型按当前站位重标定，避免后续点击全落到屏幕边缘。
        if B is not None:
            ax = (pos[1] - B[0]) / SPECIAL_WALK_SX
            ay = (pos[2] - B[1]) / SPECIAL_WALK_SY
            if not (SPECIAL_WALK_MIN_X - 80 <= ax <= SPECIAL_WALK_MAX_X + 80
                    and SPECIAL_WALK_MIN_Y - 80 <= ay
                    <= SPECIAL_WALK_MAX_Y + 80):
                alt = _special_model_anchor(win, target, pos)
                if alt is not None:
                    B = (pos[1] - SPECIAL_WALK_SX * alt[0],
                         pos[2] - SPECIAL_WALK_SY * alt[1])
                    emit("  角色屏幕位置越界（B 不可靠）→ 按描边锚点模型"
                         f"重标定 B=({B[0]:.2f},{B[1]:.2f})")
        # 位移/停滞判定：连续走时以「连续 N 轮位置不变」为准
        if prev_pos is not None and (pos[1], pos[2]) != prev_pos:
            still_polls = 0
            misses = 0
            miss_idx = 0
            wp_fail = 0
            endpoint_last = None
        else:
            still_polls += 1
        prev_pos = (pos[1], pos[2])
        last = pos
        goal_dist = (abs(final_target[0] - pos[1])
                     + abs(final_target[1] - pos[2]))
        if (direct_best is None
                or goal_dist <= direct_best - SPECIAL_WALK_PROGRESS_MIN):
            direct_best = goal_dist
            direct_stuck_t = time.time()
        fdx = final_target[0] - pos[1]
        fdy = final_target[1] - pos[2]
        arrive_gate = max(tol, SPECIAL_WALK_ARRIVE_CHECK)
        if (abs(fdx) <= arrive_gate and abs(fdy) <= arrive_gate
                and endpoint_last != (pos[1], pos[2])):
            # 接近目标：等停稳用最终落点复核（连续走时半路位置可能还在走，
            # 日志/成功判定都必须用停稳后的真实站位）
            final_pos = _special_settle_pos(win, pos)
            if final_pos is not None:
                if not _special_map_name_match(final_pos[0], target):
                    return False, (f"洞内步行时场景变化：{final_pos[0]} "
                                   f"({final_pos[1]},{final_pos[2]})")
                pos = final_pos
                last = pos
                prev_pos = (pos[1], pos[2])
                prev_img = None
                if _zone_teleported(pos):
                    return True, (f"已传送到"
                                  f"{_special_zone_name(zone_state['zone'])}区")
                fdx = final_target[0] - pos[1]
                fdy = final_target[1] - pos[2]
                # 停稳后的真实进展才重置「直线无进展」计时（避免终点原地
                # 修正把 15s 绕行救援不停顺延）
                goal_dist = (abs(final_target[0] - pos[1])
                             + abs(final_target[1] - pos[2]))
                if (direct_best is None
                        or goal_dist <= direct_best - SPECIAL_WALK_PROGRESS_MIN):
                    direct_best = goal_dist
                    direct_stuck_t = time.time()
            if (abs(fdx) <= tol and abs(fdy) <= tol):
                extra = ("" if final_target == (x, y)
                         else f"（目标在描边外，最近可走点 {final_target}）")
                emit(f"洞内步行到达 {target} ({pos[1]},{pos[2]})，"
                     f"误差 ({fdx:+d},{fdy:+d}){extra}，共 {clicks} 次点击 "
                     f"{time.time() - t0:.1f}s")
                _remember_special_view(win, target, B, pos,
                                       zone_name=zone_name or None)
                return True, (f"已到达{target} ({pos[1]},{pos[2]}){extra}，"
                              f"误差 ({fdx:+d},{fdy:+d})")
            # 同一站位不再重复停稳复核（否则反复「修正→原地→再修正」）：
            # 记下站位，下一轮交给正常点击停滞/换偏移点/探针/绕行逻辑
            endpoint_last = (pos[1], pos[2])
            # 停稳复核超容差：按最终误差修正点击偏置（无首步校准的终点闭环）
            aim_bias = (max(-6.0, min(6.0, aim_bias[0] + fdx)),
                        max(-6.0, min(6.0, aim_bias[1] + fdy)))
            emit(f"停稳后 ({pos[1]},{pos[2]}) 超出容差 ±{tol}，修正偏置"
                 f"({aim_bias[0]:+.1f},{aim_bias[1]:+.1f})，继续走")
        while (len(waypoints) > 1
               and abs(pos[1] - waypoints[0][0])
               <= SPECIAL_ROUTE_WP_TOLERANCE
               and abs(pos[2] - waypoints[0][1])
               <= SPECIAL_ROUTE_WP_TOLERANCE):
            waypoints.pop(0)
        tx_wp, ty_wp = waypoints[0]
        # 直线导航（单途经点）长时间没有实质进展 → 改走描边路网绕行（一次）
        if (use_route and not direct_rescued and len(waypoints) == 1
                and active_route is not None
                and time.time() - direct_stuck_t
                >= SPECIAL_WALK_DIRECT_STUCK_TIME):
            wps = _special_trace_waypoints(
                active_route, pos, final_target[0], final_target[1])
            if not wps and route_boundary is not None:
                # 分区只有边界没有描边折线时，用边界栅格 A* 绕行
                wps = _special_boundary_waypoints(
                    route_boundary, (pos[1], pos[2]), final_target,
                    portal=portal)
            if wps and len(wps) > 1:
                waypoints = wps
                direct_rescued = True
                direct_best = None
                direct_stuck_t = time.time()
                wp_best_dist = 10 ** 9
                wp_stall = 0
                emit(f"直线 {goal_dist} 格 "
                     f"{SPECIAL_WALK_DIRECT_STUCK_TIME:.0f}s 无进展 → "
                     f"改走描边路网绕行（{len(wps)} 个途经点）")
                continue
        # 可见性导航：路线上**屏幕内最远**的途经点（人物屏幕位置 → 视窗
        # 范围内可见的世界坐标）；只点看得见的路线点，不把点击钳到屏幕边缘
        win_l = SPECIAL_WALK_MIN_X + SPECIAL_WALK_VIEW_MARGIN
        win_r = SPECIAL_WALK_MAX_X - SPECIAL_WALK_VIEW_MARGIN
        win_t = SPECIAL_WALK_MIN_Y + SPECIAL_WALK_VIEW_MARGIN
        win_b = SPECIAL_WALK_MAX_Y - SPECIAL_WALK_VIEW_MARGIN
        vis_chosen = False
        for idx in range(len(waypoints) - 1, -1, -1):
            wx, wy = waypoints[idx]
            sx = (wx - B[0]) / SPECIAL_WALK_SX
            sy = (wy - B[1]) / SPECIAL_WALK_SY
            if not (win_l <= sx <= win_r and win_t <= sy <= win_b):
                continue
            if (route_boundary is not None
                    and not _special_segment_inside(
                        route_boundary, (pos[1], pos[2]), (wx, wy),
                        portal=portal)):
                continue
            vis_chosen = True
            tx_wp, ty_wp = wx, wy
            if idx > 0:
                waypoints = waypoints[idx:]
            break
        if cur_wp != (tx_wp, ty_wp):
            cur_wp = (tx_wp, ty_wp)
            wp_best_dist = 10 ** 9
            wp_stall = 0
            # aim_bias 是映射常量，跨途经点不重置
        wp_dist = abs(tx_wp - pos[1]) + abs(ty_wp - pos[2])
        if wp_dist < wp_best_dist:
            wp_best_dist = wp_dist
            wp_stall = 0
        elif still_polls == 0:
            # 在走但不逼近途经点（被墙/平台隔开）→ 跳过该点；
            # 原地不动交给下面的「连续点击无位移」处理（换点/重探针）
            wp_stall += 1
            if wp_stall >= SPECIAL_ROUTE_WP_SKIP_AFTER and len(waypoints) > 1:
                skipped = waypoints.pop(0)
                emit(f"途经点 {skipped} 无进展，跳过")
                wp_stall = 0
                wp_best_dist = 10 ** 9
                continue
        # 连续点击后仍长时间不动：按点击无效处理（换偏移点/重探针）
        if clicks > 0 and still_polls >= SPECIAL_WALK_STALL_POLLS:
            misses += 1
            miss_idx += 1
            wp_fail += 1
            still_polls = 0
            if img is not None and _special_dialog_open(img):
                _special_close_dialog(win, emit)
                misses = 0
                continue
            # 描边路网的途经点可能位于当前站位不可直达的臂/平台边缘：
            # 连续点不动就跳过这个途经点，换路线上下一个点（终目标不跳）
            if wp_fail >= SPECIAL_ROUTE_WP_SKIP_AFTER and len(waypoints) > 1:
                skipped = waypoints.pop(0)
                emit(f"途经点 {skipped} 连续不可达，跳过")
                misses = 0
                miss_idx = 0
                wp_fail = 0
                continue
            emit(f"洞内步行点击无位移（{misses}/4），换点重试")
            if misses >= 4:
                B = _special_probe(win, target, emit, deadline)
                if B is None:
                    return False, "洞内步行连续无位移，无法继续"
                misses = 0
                miss_idx = 0
                _refresh_waypoints("重新探针后")
                prev_img = None
        dx, dy = tx_wp - pos[1], ty_wp - pos[2]
        A_x = (pos[1] - B[0]) / SPECIAL_WALK_SX
        A_y = (pos[2] - B[1]) / SPECIAL_WALK_SY
        if vis_chosen:
            # 点视窗内的路线途经点（世界 → 屏幕，叠加映射偏置）
            wx = tx_wp + aim_bias[0]
            wy = ty_wp + aim_bias[1]
            raw_px = int(round((wx - B[0]) / SPECIAL_WALK_SX))
            raw_py = int(round((wy - B[1]) / SPECIAL_WALK_SY))
            step_world = (wx, wy)
            slen = math.hypot(raw_px - A_x, raw_py - A_y)
        else:
            # 路线点都不可见：沿到途经点方向，把点击裁剪到视窗内
            sdx = dx / SPECIAL_WALK_SX
            sdy = dy / SPECIAL_WALK_SY
            k = 1.0
            if sdx > 0:
                k = min(k, (win_r - A_x) / sdx)
            elif sdx < 0:
                k = min(k, (win_l - A_x) / sdx)
            if sdy > 0:
                k = min(k, (win_b - A_y) / sdy)
            elif sdy < 0:
                k = min(k, (win_t - A_y) / sdy)
            k = max(0.0, min(1.0, k))
            if k <= 0.0:
                k = min(1.0, SPECIAL_WALK_SCREEN_STEP
                        / max(1.0, math.hypot(sdx, sdy)))
            raw_px = int(round(A_x + sdx * k
                               + aim_bias[0] / SPECIAL_WALK_SX))
            raw_py = int(round(A_y + sdy * k
                               + aim_bias[1] / SPECIAL_WALK_SY))
            step_world = (pos[1] + sdx * k * SPECIAL_WALK_SX,
                          pos[2] + sdy * k * SPECIAL_WALK_SY)
            slen = math.hypot(sdx * k, sdy * k)
        if not aim_done:
            # 首步校准：限制点击距离（等停稳测偏差 + 保证相机位移可测）
            cdx = raw_px - A_x
            cdy = raw_py - A_y
            clen = math.hypot(cdx, cdy)
            if clen > SPECIAL_WALK_CALIB_STEP:
                kc = SPECIAL_WALK_CALIB_STEP / clen
                raw_px = int(round(A_x + cdx * kc))
                raw_py = int(round(A_y + cdy * kc))
                step_world = (SPECIAL_WALK_SX * raw_px + B[0],
                              SPECIAL_WALK_SY * raw_py + B[1])
                slen = SPECIAL_WALK_CALIB_STEP
        ox, oy = retry_offsets[min(miss_idx, len(retry_offsets) - 1)]
        px = max(SPECIAL_WALK_MIN_X,
                 min(SPECIAL_WALK_MAX_X, raw_px + ox))
        py = max(SPECIAL_WALK_MIN_Y,
                 min(SPECIAL_WALK_MAX_Y, raw_py + oy))
        screen_clamped = ((px != raw_px + ox) or (py != raw_py + oy))
        seg_target = ((SPECIAL_WALK_SX * px + B[0],
                       SPECIAL_WALK_SY * py + B[1])
                      if screen_clamped else step_world)
        if verbose:
            # 逐步点击明细只在诊断/探图输出（用户 2026-11-24：正式环境
            # 每步一条刷屏不需要）
            bias_txt = ("" if aim_bias == (0.0, 0.0)
                        else f" 修正({aim_bias[0]:+.1f},{aim_bias[1]:+.1f})")
            clamp_txt = " 屏幕边缘" if screen_clamped else ""
            emit(f"洞内步行 {clicks + 1}: {pos[1:]} 差 ({dx:+d},{dy:+d}) "
                 f"角色屏幕≈({A_x:.0f},{A_y:.0f}) 步长 {slen:.0f}px "
                 f"→ 右键 ({px},{py}) 途经点 ({tx_wp},{ty_wp})"
                 f"{bias_txt}{clamp_txt}")
        human_click(win, px, py, button="secondary")
        last_click_t = time.time()
        clicks += 1
        if not aim_done:
            # 首步校准：等这一走停稳，测「点击点 → 实际落点」偏差。
            # 之后不再等待停稳，直接连续走（用户 2026-11-23：行走可打断）。
            after, moved = _special_wait_step(win, pos, emit)
            img1 = win.capture(force_activate=False)
            if img1 is not None:
                shift = _special_frame_shift(img, img1)
                if shift is not None:
                    B = _special_apply_shift(B, shift)
                    emit(f"  首步相机位移 ({shift[0]:.1f},{shift[1]:.1f}) "
                         f"resp={shift[2]:.2f} → B=({B[0]:.2f},{B[1]:.2f})")
                else:
                    post = after if (moved and after is not None) else pos
                    alt = _special_model_anchor(win, target, post)
                    if alt is not None:
                        B = (post[1] - SPECIAL_WALK_SX * alt[0],
                             post[2] - SPECIAL_WALK_SY * alt[1])
                        emit("  首步相机位移不可测 → 按描边锚点模型重标定 "
                             f"B=({B[0]:.2f},{B[1]:.2f})")
                prev_img = img1
            if moved and after is not None and after != pos:
                aim_done = True
                prev_pos = (after[1], after[2])
                still_polls = 0
                misses = 0
                miss_idx = 0
                wp_fail = 0
                if not screen_clamped:
                    err_x = after[1] - seg_target[0]
                    err_y = after[2] - seg_target[1]
                    if abs(err_x) > 1 or abs(err_y) > 1:
                        aim_bias = (
                            max(-6.0, min(6.0, aim_bias[0] - err_x)),
                            max(-6.0, min(6.0, aim_bias[1] - err_y)))
                emit(f"  首步映射校准完成，偏置({aim_bias[0]:+.1f},"
                     f"{aim_bias[1]:+.1f})，之后连续步行")
            else:
                # 首步就点不动：跳过映射校准，后续交给停滞/探针处理
                aim_done = True
                misses += 1
                miss_idx += 1
                wp_fail += 1
                emit(f"洞内步行点击无位移（{misses}/4），换点重试")
                if misses >= 4:
                    B = _special_probe(win, target, emit, deadline)
                    if B is None:
                        return False, "洞内步行连续无位移，无法继续"
                    misses = 0
                    miss_idx = 0
                    _refresh_waypoints("重新探针后")
                    prev_img = None
        else:
            # 连续模式：控制点击频率（用人物持续行走代替停稳再点）
            remain = SPECIAL_WALK_CLICK_INTERVAL - (time.time() - last_click_t)
            if remain > 0:
                time.sleep(remain)
    return False, f"洞内步行步数超过上限（最后位置 {last}）"


def _special_wait_teleport(win, target, from_zone_name, trigger, zones, emit,
                           timeout=SPECIAL_TELEPORT_WAIT):
    """等待洞口传送完成：位置进入其他分区（或大幅跳变）且连续稳定。

    传送有加载黑屏/位置 OCR 短暂失败——读不到继续等；战斗期间冻结。
    位置跳变但落在所有描边外（落点略偏出描边）时，离触发点超过
    SPECIAL_TELEPORT_JUMP_MIN 也按传送处理（取最近边界所在区）。
    返回 (pos, zone)；超时返回 (None, None)。
    """
    deadline = time.time() + timeout
    stable_pos = None
    stable_zone = None
    stable_n = 0
    while time.time() < deadline:
        _check_cancel()
        img = win.capture(force_activate=False)
        if img is not None and is_in_battle_frame(img):
            deadline += 1.0
            time.sleep(1.0)
            continue
        pos = (game_map.read_map_position(None, img=img)
               if img is not None else None)
        if pos is None:
            pos = _special_read_pos(win)
        cand = None
        if pos is not None and _special_map_name_match(pos[0], target):
            zone = _special_zone_at(zones, pos[1], pos[2])
            if zone is not None and _special_zone_name(zone) != from_zone_name:
                cand = zone
            elif zone is None:
                moved = math.hypot(pos[1] - trigger[0],
                                   pos[2] - trigger[1])
                if moved >= SPECIAL_TELEPORT_JUMP_MIN:
                    near = _special_zone_at(zones, pos[1], pos[2],
                                            allow_nearest=True)
                    if (near is not None
                            and _special_zone_name(near) != from_zone_name):
                        cand = near
        if cand is not None:
            if (stable_pos == (pos[1], pos[2])
                    and _special_zone_name(stable_zone)
                    == _special_zone_name(cand)):
                stable_n += 1
            else:
                stable_pos = (pos[1], pos[2])
                stable_zone = cand
                stable_n = 1
            if stable_n >= SPECIAL_TELEPORT_STABLE_POLLS:
                emit(f"传送完成：{from_zone_name} 区 → "
                     f"{_special_zone_name(cand)} 区 ({pos[1]},{pos[2]})")
                return pos, cand
        else:
            stable_pos = None
            stable_zone = None
            stable_n = 0
        time.sleep(0.4)
    return None, None


def _special_trigger_nudge(win, target, trigger, zone, avoid, emit,
                           verbose=False):
    """未触发传送：朝洞口方向越过触发点补点一次。

    补点可能落在描边外，由 _special_route_waypoints 自动钳到最近可走
    边界点（通常就是触发点附近），人物再走一次即可踩进触发区。
    """
    pos = _special_read_pos(win)
    if pos is None:
        return False
    dx = trigger[0] - pos[1]
    dy = trigger[1] - pos[2]
    dist = math.hypot(dx, dy)
    if dist > 1e-6:
        bx = int(round(trigger[0] + dx / dist * 3.0))
        by = int(round(trigger[1] + dy / dist * 3.0))
    else:
        bx, by = int(round(trigger[0])), int(round(trigger[1]))
    emit(f"传送未触发：朝洞口方向补点 ({bx},{by})")
    ok, _info = _walk_special_coordinate(
        win, target, bx, by, emit, verbose=verbose, zone=zone, avoid=avoid,
        tolerance=1, timeout=SPECIAL_TELEPORT_NUDGE_TIMEOUT)
    return ok


def _walk_special_zones(win, target, x, y, emit, verbose=False, _retries=2,
                        _force_probe_first=False):
    """特殊地图分区导航（波月洞 A/B）：跨区先走洞口传送，再在目标区步行。

    分区路网（route["zones"]/["links"]）描述同一坐标平面上的多块互不
    相连区域：先用位置判定当前区/目标区，BFS 求传送链，逐段走到本区
    触发点并等待场景切换（传送也可能发生在步行途中，由 zone_state
    捕获），最后在目标区按该区边界步行到目标。目标区步行期间若被
    洞口传送到别的区（目标靠近回程触发点），按新位置重新规划，最多
    _retries 次。无分区路网时等价于 _walk_special_coordinate。
    从传送落点附近起步的每一段都用名字/探针重标定相机（recalibrate），
    不信任转场期采集的描边锚点样品。返回 (ok, info)。
    """
    route = special_map_route(target)
    zones = _special_zones(route)
    if len(zones) < 2:
        return _walk_special_coordinate(win, target, x, y, emit,
                                        verbose=verbose)
    names = [_special_zone_name(z) for z in zones]
    if "" in names or len(set(names)) != len(names):
        emit(f"{target} 分区路网缺少唯一区名，按整图路网步行")
        return _walk_special_coordinate(win, target, x, y, emit,
                                        verbose=verbose)
    by_name = {_special_zone_name(z): z for z in zones}
    links = _special_zone_links(route)
    pos = _special_read_pos(win)
    if pos is None or not _special_map_name_match(pos[0], target):
        return _walk_special_coordinate(win, target, x, y, emit,
                                        verbose=verbose)
    cur = _special_zone_at(zones, pos[1], pos[2], allow_nearest=True)
    dst = _special_zone_at(zones, x, y, allow_nearest=True)
    if cur is None or dst is None:
        return _walk_special_coordinate(win, target, x, y, emit,
                                        verbose=verbose)
    cur_name = _special_zone_name(cur)
    dst_name = _special_zone_name(dst)
    triggers = {}
    for link in links:
        triggers.setdefault(link["from_zone"], []).append(link["from"])
    need_recal = bool(_force_probe_first)

    def _landing_recal(px, py, r=6.0):
        """起点在传送落点附近时，该落点是否被标记为需重标定相机。

        波月洞 A 落点（B→A）附近没有稳态锚点样品，必须名字/探针重标定；
        B 落点（A→B）样品正常，继续用锚点模型，避免探针把人物带偏绕路。
        """
        best = None
        for link in links:
            d = math.hypot(px - link["land"][0], py - link["land"][1])
            if d <= r and (best is None or d < best[0]):
                best = (d, link)
        return bool(best and best[1].get("land_recalibrate"))

    if _landing_recal(pos[1], pos[2]):
        need_recal = True

    def _walk_destination(zone_obj, recalibrate=False):
        """目标区步行：区内其他触发点避让（目标本身在触发点附近时除外）。"""
        zn = _special_zone_name(zone_obj)
        zavoid = [p for p in triggers.get(zn, [])
                  if math.hypot(p[0] - x, p[1] - y)
                  > SPECIAL_PORTAL_AVOID_R]
        state = {}
        ok2, info2 = _walk_special_coordinate(
            win, target, x, y, emit, verbose=verbose, zone=zone_obj,
            avoid=zavoid, zones=zones, zone_state=state,
            recalibrate=recalibrate)
        return ok2, info2, state

    def _replan(zone_now):
        """目标区步行期间被传送走：清旧视图并按当前区重新规划。"""
        emit(f"{target} 目标区步行期间被传送到 {zone_now} 区，重新规划")
        _LAST_SPECIAL_VIEW.clear()
        if _retries > 0:
            return _walk_special_zones(win, target, x, y, emit,
                                       verbose=verbose,
                                       _retries=_retries - 1,
                                       _force_probe_first=True)
        return False, f"{target} 分区导航反复传送，未能到达目标"

    if cur_name == dst_name:
        ok, info, state = _walk_destination(cur, recalibrate=need_recal)
        if state.get("teleported"):
            return _replan(_special_zone_name(state.get("zone")))
        return ok, info
    path = _special_zone_path(links, cur_name, dst_name)
    if path is None:
        emit(f"{target} 当前 {cur_name} 区，目标在 {dst_name} 区，"
             f"但路网没有可用传送路线")
        return False, (f"{target} 分区之间无传送路线"
                       f"（{cur_name}→{dst_name}）")
    emit(f"{target} 分区路网：当前 {cur_name} 区 ({pos[1]},{pos[2]})，"
         f"目标 {dst_name} 区 ({x},{y})，需传送 {len(path)} 次")
    for hop in path:
        fname = hop["from_zone"]
        tname = hop["to_zone"]
        from_zone = by_name[fname]
        trig = hop["from"]
        # 本区其它传送触发点一律避让；当前要走的触发点是目标，不避让
        avoid = [p for p in triggers.get(fname, []) if p != trig]
        emit(f"前往 {fname} 区传送点 ({trig[0]:.0f},{trig[1]:.0f})"
             f" → {tname} 区")
        state = {}
        ok, info = _walk_special_coordinate(
            win, target, int(round(trig[0])), int(round(trig[1])), emit,
            verbose=verbose, zone=from_zone, avoid=avoid,
            tolerance=SPECIAL_TRIGGER_TOLERANCE,
            timeout=SPECIAL_TRIGGER_WALK_TIMEOUT,
            zones=zones, zone_state=state, recalibrate=need_recal)
        if not state.get("teleported") and not ok:
            return False, f"{target} 前往传送点失败：{info}"
        if state.get("teleported"):
            tp = state.get("pos")
            emit(f"已传送进入 {tname} 区"
                 + (f" ({tp[1]},{tp[2]})" if tp is not None else ""))
        else:
            # 已走到触发点但还未切场景：等自动传送，未触发则补点重试
            tp, tzone = _special_wait_teleport(win, target, fname, trig,
                                               zones, emit)
            for _ in range(SPECIAL_TELEPORT_NUDGES):
                if tp is not None:
                    break
                _special_trigger_nudge(win, target, trig, from_zone, avoid,
                                       emit, verbose=verbose)
                tp, tzone = _special_wait_teleport(win, target, fname, trig,
                                                   zones, emit)
            if tp is None:
                return False, (f"{target} 传送未触发"
                               f"（{fname} 区 {trig} → {tname} 区）")
            emit(f"已传送进入 {_special_zone_name(tzone)} 区 "
                 f"({tp[1]},{tp[2]})")
        # 落点与路网预期不符（踩到别的洞口等）：按实际分区重新规划
        landed = (_special_zone_at(zones, tp[1], tp[2], allow_nearest=True)
                  if tp is not None else None)
        landed_name = _special_zone_name(landed)
        if landed_name and landed_name != tname:
            return _replan(landed_name)
        # 刚从该链接的落点起步：只有落点被标记（附近无稳态锚点样品）
        # 才重标定；A→B 的 B 落点锚点正常，不触发探针绕路。
        need_recal = bool(hop.get("land_recalibrate"))
        # 相机/场景已切换：旧视图热启动不可复用
        _LAST_SPECIAL_VIEW.clear()
    ok, info, state = _walk_destination(by_name[dst_name],
                                        recalibrate=need_recal)
    if state.get("teleported"):
        return _replan(_special_zone_name(state.get("zone")))
    return ok, info


def explore_special_map(win, target, center=None, arm_step=8,
                        max_arm_steps=8, log_fn=None):
    """特殊地图自动探图：从十字中心向四个斜向臂探测并保存路网。

    人工描边（scripts/trace_special_map.py + build_special_map_route.py）
    是首选建图方式；本函数是无描边时的兜底：按已知十字中心向四个
    对角方向逐节点走，走不动的方向即臂端。结果写角色数据
    special_map_routes.json（不覆盖已有 trace）。返回 (ok, info)。
    """
    emit = log_fn or (lambda m: log(f"[地图导航] {m}", "INFO"))
    if center is None:
        route = special_map_route(target)
        center = tuple(route["center"]) if route else (57, 26)
    emit(f"{target} 自动探图：中心 {center}，四臂探测中（可中断）")
    ok, info = _walk_special_coordinate(win, target, center[0], center[1],
                                        emit, use_route=False, verbose=True)
    if not ok:
        return False, f"探图前到达中心失败：{info}"
    arms = {}
    for d in ((1, -1), (-1, 1), (-1, -1), (1, 1)):
        ok_c, info_c = _walk_special_coordinate(
            win, target, center[0], center[1], emit, use_route=False,
            verbose=True)
        if not ok_c:
            emit(f"返回中心失败：{info_c}")
            break
        nodes = [[int(center[0]), int(center[1])]]
        for k in range(1, max_arm_steps + 1):
            tx = center[0] + d[0] * arm_step * k
            ty = center[1] + d[1] * arm_step * k
            ok_a, info_a = _walk_special_coordinate(
                win, target, tx, ty, emit, use_route=False, verbose=True)
            if not ok_a:
                emit(f"臂 {d} 第 {k} 节点 ({tx},{ty}) 不可达：{info_a}")
                break
            pos = _special_read_pos(win)
            if pos is None:
                break
            node = [int(pos[1]), int(pos[2])]
            if nodes[-1] != node:
                nodes.append(node)
            emit(f"臂 {d} 节点 {node}")
        arms[f"{d[0]},{d[1]}"] = nodes
        emit(f"臂 {d} 探测完成：{nodes}")
    route = {"center": [int(center[0]), int(center[1])], "arms": arms}
    saved = save_special_map_route(target, route)
    emit(f"{target} 探图完成：{len(arms)} 臂，"
         f"{'已保存' if saved else '保存失败'}到角色数据")
    return True, (f"{target} 自动探图完成："
                  f"{sum(len(v) for v in arms.values())} 个节点")


def _navigate_special_map(win, target, x, y, entry, emit, verbose=False,
                          on_walk_start=None):
    """特殊地图导航：先进入场景，再在洞内按坐标步行到 (x,y)。

    入口两条路（用户 2026-11-23 明确为两种进入方式）：① 父地图入口
    坐标（平顶山 102,42）走近光圈自动进场；② 未自动进门时打开父地图
    场景地图、勾选「普通」、点入口 NPC（金角大王/银角大王）自动寻路
    进入。进入后洞内无小地图，用 _walk_special_zones 右键小步按 HUD
    坐标走到目标（分区图先经洞口传送，±3 格即可）。
    """
    t_start = time.time()

    pos = _read_pos(win)
    if pos is not None and _special_map_name_match(pos[0], target):
        emit(f"已在{target}（{pos[1]},{pos[2]}），直接步行到坐标")
        return _walk_special_zones(win, target, x, y, emit,
                                   verbose=verbose)
    parent = entry["parent"]
    ex, ey = entry["entry"]
    emit(f"{target} 世界地图无标签：经{parent}入口 ({ex},{ey}) 进入")
    ok, info = _navigate_impl(win, parent, ex, ey, emit, verbose=verbose,
                              allow_restart=True,
                              on_walk_start=on_walk_start,
                              enter_name=target)
    pos = _read_pos(win)
    entered = pos is not None and _special_map_name_match(pos[0], target)
    if ok and not entered:
        # 到达入口坐标但还没自动进门：给光圈一个短窗口（走近即触发），
        # 仍不进再走场景地图 NPC 入口（用户 2026-11-23 兜底）
        entered = _wait_special_enter(win, target, SPECIAL_AUTO_ENTER_WAIT,
                                      emit)
    if not entered:
        emit(f"入口寻路未进入{target}"
             f"（{info if not ok else '到达入口未自动进入'}），改走 NPC 入口")
        ok2, info2 = _enter_special_by_npc(win, target, entry, emit)
        if not ok2:
            return False, (f"{target} 进入失败（入口：{info}；"
                           f"NPC 入口：{info2}）")
        emit(f"{info2}（总耗时 {time.time() - t_start:.1f}s）")
    ok_w, info_w = _walk_special_zones(win, target, x, y, emit,
                                       verbose=verbose)
    if not ok_w:
        return False, f"已进入{target}，但未能走到坐标：{info_w}"
    emit(f"{target} 导航完成（总耗时 {time.time() - t_start:.1f}s）")
    return True, info_w


def navigate_to_map_coordinate(win, target, x, y, log_fn=None, verbose=False,
                               on_walk_start=None, pos_hint=None):
    """导航到 target 地图 (x, y)：打开地图 → 悬停标定 → 校验点击 → 到达。

    target：WORLD_MAP_LABELS 地图名（经 resolve 归一）、层级图全名
    （龙窟一层…凤巢七层）或 SPECIAL_MAP_ENTRIES 特殊地图（莲花洞等）。
    特殊地图世界地图无标签、洞内无小地图：先经父地图入口进入，再在洞内
    按 HUD 坐标右键小步走到 (x,y)（±3 格，见 _navigate_special_map）。
    返回 (ok, info)，过程经 log_fn 输出。
    每个关键阶段 emit 一行累计/分段耗时，最终 info 附完整耗时汇总。
    verbose=True（诊断卡片/探图）逐次输出行走位置与洞内步行逐步点击
    明细；生产默认只在地名变化（过场景）、到达/修正/无位移等关键节点
    输出，避免每 1s 一条刷屏。
    on_walk_start：可选无参回调，位移启动确认后、到达等待前调用一次
    （行走期插队任务，如修罗手动寻路的组号队员血蓝 pass——与 follow
    点任务绿字后的寻路期 pass 对齐）。回调异常只记日志、不影响导航；
    停滞重启会再走一遍流程，回调可能被调用第二次（调用方自行节流）。
    pos_hint：调用方已知的当前位置（与 read_map_position 同形元组），
    仅用于开图路线的地图名判断（预走路/同图/层内）；传入后免一次
    截帧+位置 OCR（~0.4s）。只认 pos[0]，坐标不参与路线决策；位移
    基线仍在点击后另读。仅首趟生效（停滞重启时人物已移动，hint 过期）。
    行走中位置停滞 5s（寻路被点世界/其它操作打断）会自动重新开图补点
    一次；主动 request_cancel() 中断（诊断「中断寻路」）不补——那是用户
    要停：各轮询循环每轮检查（1-3s 生效），中断时关地图/寻路面板并返回
    (False, "已中断寻路")。注意：人物身上的客户端自动寻路不会因此停止，
    仍会走到最后一次点击的目标点。
    """
    emit = log_fn or (lambda m: log(f"[地图导航] {m}", "INFO"))
    _CANCEL.clear()
    try:
        entry = special_map_entry(target)
        if entry is not None:
            # 特殊地图（世界地图无标签、洞内无小地图）：只能经父地图入口
            # 进入，无法按图点击内部坐标（用户 2026-11-23 莲花洞规则）
            ok, info = _navigate_special_map(win, target, x, y, entry, emit,
                                             verbose=verbose,
                                             on_walk_start=on_walk_start)
            return ok, info
        ok, info = _navigate_impl(win, target, x, y, emit, verbose=verbose,
                                  allow_restart=True,
                                  on_walk_start=on_walk_start,
                                  pos_hint=pos_hint)
        if not ok and info == ARRIVE_RESTART_TEXT:
            # 人物原地站了 5s：多半是客户端寻路被打断（点世界/其它操作），
            # 重新走一遍开图 + 点目标点补一次；补过仍不行按失败返回
            emit("位置 5s 未变化（寻路可能被打断）——重新开图补一次")
            # 重启时人物已移动，pos_hint 不再可信：重新读位置
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


def start_navigation(win, target, x, y, log_fn=None, on_map_open=None,
                     trusted=False):
    """坐标化寻路（只开图/标定/点击，不等到达）：任务点位迁移用。

    与 navigate_to_map_coordinate 前半段一致：同图直开小地图/跨图经
    世界地图标签 → 搜索条定位 → 标定换算 → 悬停校验 → 点击地图点。
    点击成功后**地图保持打开**，由调用方按原逻辑决定关闭时机并等待到达
    （保留各任务自己的「附近」判据：按钮/NPC/对话框/位置消失）。
    失败/中断路径清理残留地图与寻路面板。
    on_map_open：目标地图打开后、点击前回调（领取流程读小地图「任务」
    筛选状态等）。回调异常只记日志、不影响寻路。
    trusted：有标定直接点（免悬停校验/采样），固定领取点专用；无标定
    立即失败由调用方回退固定像素点（见 _start_impl）。
    返回 (ok, info, point)。
    """
    emit = log_fn or (lambda m: log(f"[地图导航] {m}", "INFO"))
    _CANCEL.clear()
    try:
        ok, info, ctx = _start_impl(win, target, x, y, emit,
                                    on_map_open=on_map_open, trusted=trusted)
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


def _start_impl(win, target, x, y, emit, on_map_open=None, pos_hint=None,
                trusted=False):
    """开图 + 标定 + 点击前半段（start_navigation / _navigate_impl 共用）。

    世界地图（layer=None）开图后统一取消「普通」筛选（用户 2026-11-24）：
    勾选时金角大王等 NPC 绿字会吃掉点击/误开对话框；特殊地图 NPC 入口
    兜底自行勾选（_enter_special_by_npc），残留状态由下次开图时清除。
    成功时地图保持打开（调用方决定关闭时机），返回 (True, "", ctx)；
    失败已清理残留地图（寻路面板不会自动关闭，必须统一收尾）。
    pos_hint：调用方已知的当前位置（read_map_position 同形元组）——只
    用于路线判断（预走路/同图/层内），传入后免一次截帧+位置 OCR；
    坐标不参与决策（位移基线在点击后另读）。
    trusted：有会话/内置标定就直接点（免悬停校验/采样），固定领取点等
    坐标容差大的目标专用；无标定/越界立即失败，由调用方回退固定像素点
    （见 claim_flow.open_claim_map）。
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
    if pos_hint is not None:
        pos = tuple(pos_hint)
        emit(f"目标 {layer or base} ({x},{y})；起点位置 {pos}（hint）")
    else:
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
    if layer is None:
        # 「普通」筛选统一取消勾选（用户 2026-11-24）：世界地图坐标导航的
        # 点击点都是地形点，勾选时 NPC 绿字（金角大王等）会吃掉点击/误开
        # 对话框；特殊地图 NPC 入口兜底（_enter_special_by_npc）自行勾选、
        # 进入后残留的勾选状态由这里在下次开图时统一取消。层级图
        # （龙窟/凤巢/大雁塔层）页面无该筛选行，不做处理。
        try:
            ok_p, info_p = _set_minimap_putong_filter(win, False, emit)
        except Exception as e:  # noqa: BLE001 —— 辅助检查不得阻断寻路
            ok_p, info_p = False, f"「普通」筛选检查异常: {e}"
        if not ok_p:
            emit(f"「普通」筛选: {info_p}")

    key = layer or base
    affine = affine_for(win, key)
    point = None
    err = ""
    ok = False

    def _cache_corrected(aff):
        """偏差修正后的标定写入会话缓存：同图下次导航一次命中。"""
        remember_affine(win, key, aff)
        emit("标定已按悬停校验修正并缓存（同图后续导航直接命中）")

    if trusted:
        # trusted 直点（2026-11-25 用户确认）：固定领取点这类坐标容差大
        # （差 1-2 格不影响到达判据），跳过悬停标定/校验——悬停浮层在
        # 窗口非前台时可能整体不渲染（2026-11-25 实机 32 次×36s 空转），
        # 直点让重走领取点不再被标定链拖死。无标定/换算越界立即失败，
        # 由调用方回退固定像素点（claim_flow 的旧生产路径），不跑采样。
        if affine is None:
            _cleanup()
            emit(f"耗时: trusted 直点无标定（总计 {time.time() - t_start:.1f}s）")
            return False, f"{key} 无地图标定（trusted 直点不可用）", None
        px = int(round(affine["ax"] + affine["sx"] * x))
        py = int(round(affine["ay"] + affine["sy"] * y))
        if not (0 <= px <= 1377 and 0 <= py <= 830):
            _cleanup()
            emit(f"耗时: trusted 直点换算越界 ({px},{py})"
                 f"（总计 {time.time() - t_start:.1f}s）")
            return False, f"trusted 直点 ({px},{py}) 超出窗口", None
        _check_cancel()
        emit(f"使用内置/会话标定（trusted 直点，免悬停校验）→ ({px},{py})")
        human_click(win, px, py)
        point = (px, py)
        ok = True
    elif affine is not None:
        emit("使用内置/会话标定（悬停校验）")
        ok, point, err = _aim_and_click(win, affine, None, x, y, emit,
                                        on_affine_corrected=_cache_corrected)
        if not ok:
            emit(f"标定未通过校验（{err}），回退完整采样")
            affine = None
    if not trusted and affine is None:
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
                   on_walk_start=None, enter_name=None, pos_hint=None):
    """导航主流程（navigate_to_map_coordinate 的实现，emit 已就绪）。

    enter_name：特殊地图名（莲花洞等）——特殊地图经父地图入口自动进场，
    到达等待以 HUD 地名变为该名为准（见 _wait_arrival）。
    pos_hint：调用方已知的当前位置（只认地名，见 _start_impl）。
    世界地图开图后统一取消「普通」筛选（见 _start_impl；用户 2026-11-24）。
    """
    def _cleanup():
        """统一收尾：先关地图（寻路面板可能被地图窗口遮住），再关面板。"""
        game_map.close_maps(win, quiet=True)
        _close_xunlu_panel_if_open(win)

    ok, info, ctx = _start_impl(win, target, x, y, emit, pos_hint=pos_hint)
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
        cur = _read_pos(win)
        if _arrived(cur, base, layer, x, y) or (
                enter_name is not None and cur is not None
                and str(cur[0]).strip() == enter_name):
            _cleanup()
            emit(f"耗时: 已在目标附近，总计 {time.time() - t_start:.1f}s")
            return True, "已到达目标附近（无需移动）"
        emit("点击后无位移（可能点到飞行旗等无效点），偏移重试")
        human_click(win, min(point[0] + CLICK_OFFSET_RETRY, 1377), point[1])
        _wait_movement(win, before)
    emit(f"位移启动（点击后 {time.time() - t_click:.1f}s）")
    # 位移已启动即关地图（2026-11-25 性能）：到达等待只读 HUD 位置
    # （MAP_POS_REGION，不在地图窗口内），不依赖地图窗口；关图/面板
    # 收尾与行走并行，到达后不再占一段收尾时间（原实现到达后才
    # close_maps，含 1s 关闭确认实测 ~1.5s，每轮白等）。大雁塔入口的
    # 「寻路」面板被目的地地图窗口遮住，须先关地图让面板露出再补关
    # （2026-11-21 实机：面板一直留到行走/战斗中）。
    game_map.close_maps(win, quiet=True)
    if xunlu_entry and _close_xunlu_panel_if_open(win):
        emit("已关闭残留「寻路」面板")
    if on_walk_start is not None:
        # 行走期插队回调（修罗手动寻路 → 组号队员血蓝 pass）：人物已在走，
        # 回调期间客户端寻路继续；异常只记日志，不拖垮导航
        try:
            on_walk_start()
        except Exception as e:  # noqa: BLE001 —— 回调异常不影响导航
            emit(f"行走起步回调异常: {e}")

    ok, info = _wait_arrival(win, base, layer, x, y, emit,
                             verbose=verbose, allow_restart=allow_restart,
                             enter_name=enter_name)
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
