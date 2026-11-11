"""玄枢轶闻任务自动化。

流程：
1. 任务追踪中已有 [玄枢轶闻] 任务 → 循环跟踪任务直到完成
   （点击绿色带下划线文案，等待任务信息变化后继续点击；
   期间处理对话框绿色选项与锄遗聚宝小游戏；钓鱼小游戏——对话
   「不知道这里有没有那条锦鲤鱼」→ 选项「先钓一下再说！」→ 窗口标题
   xuanshu_diaoyu.png 出现，闭环处理与客栈破除封印完全一致（共享
   pochufengyin 模块；仅标题模板与推进方式不同——钓鱼开始后按钮为
   「长按」，须按住不放才能推进，破除封印为「点击」连点））
2. 没有任务时，寻路到任务位置（洛阳城 93,125）并领取任务：
   - 位置识别失败一律当不在目标坐标（不按 Alt+C 重试），直接寻路
   - 已在任务位置 5px 内 → 跳过寻路
   - 在洛阳城其他位置 → 打开小地图点击任务点寻路
   - 不在洛阳城 → 世界地图 → 洛阳城 → 小地图 → 点击任务点寻路
   - 寻路完成判定：到达目标坐标附近；或坐标连续两次不变且出现领取按钮
     （距目标过近点击任务点无响应时的兜底）
   - 寻路前/后清理地图与物品栏，到达后点击领取任务按钮
   - 对话框 1 点击绿色选项「玄枢轶闻」，对话框 2 点击绿色选项「领取玄枢轶闻」
   - 校验任务追踪中出现玄枢轶闻任务
3. 任务完成后（追踪中消失）继续返回领取下一轮（run_loop 主循环）；
   达到设置的次数上限或领取时出现「请明天再来」文案（每日次数已满）时结束
"""
import difflib
import os
import re
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard  # Alt+C 降落（封印机关破解）
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import log, human_click, precise_click, rapid_click, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import pochufengyin as _pochu_minigame  # 破除封印/钓鱼共用小游戏（闭环实现）
from . import task_tracker  # 任务追踪面板操作
from . import clear_screen  # 启动清屏：活动侧栏收起（锄遗聚宝开始前调用）
from .battle import (is_in_battle, is_in_battle_frame,
                     watch_in_battle as battle_watch_in_battle)  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from . import tab_group  # 组号模式：pass 抑制标志（follow 轮询护栏）
from .control_window import load_task_counts  # 任务次数上限（通用设置）

# 任务追踪面板中的任务标题
TASK_TITLE = "玄枢轶闻"

# ---- 寻路与领取任务（洛阳城 93,125） ----
LUOYANG_CITY = "洛阳城"
TASK_MAP_POS = (93, 125)            # 任务位置（洛阳城坐标；None 表示目标非坐标）
MAP_POS_TOLERANCE = 5               # 坐标判定容差（±5 像素）
MAP_CLICK_POS = (399, 435)          # 小地图上任务位置对应的点击点（窗口相对坐标）
TASK_LINGQU_ICON = "task_lingqu.png"           # 领取任务按钮模板
ARRIVE_TIMEOUT = 90                 # 等待寻路完成的超时（秒）
# 领取任务对话框中的绿色选项（两步）
DIALOG_OPTION_XUANSHU = "玄枢轶闻"
DIALOG_OPTION_LINGQU = "领取玄枢轶闻"
DIALOG_TIMEOUT = 10                 # 等待对话框出现的超时（秒）
# 任务过程中出现的对话框绿色选项（跟踪期间检测到则点击，按顺序）
DIALOG_OPTIONS = ["注入玄蕴试试看"]
# ---- 监天盘子任务（追踪文案「使用监天盘寻找石妖踪迹」出现即触发）----
JIANTIANPAN_TRACK_TEXT = "使用监天盘寻找石妖踪迹"  # 任务追踪触发文案
JIANTIANPAN_WORLD_ICON = "shijie_huoyungebi.png"  # 世界地图「火云戈壁」标签模板
JIANTIANPAN_MAP_CLICK_POS = (759, 435)            # 火云戈壁小地图点击点（寻路到目标附近）
JIANTIANPAN_TARGET_POS = (180, 86)                # 目标坐标（火云戈壁）
JIANTIANPAN_ARRIVE_TOLERANCE = 4                  # 到达容差（严格：仙药只在目标附近出现）
JIANTIANPAN_ARRIVE_TIMEOUT = 90                   # 寻路等待超时（秒）
JIANTIANPAN_ICON = "xuanshu_jiantianpan.png"      # 监天盘模板
JIANTIANPAN_THRESHOLD = 0.65                      # 监天盘模板匹配阈值（实测渲染分数偏低，
                                                  # 0.8 漏检——用户 2026-10-17 指定）
JIANTIANPAN_CLICK_TIMEOUT = 10                    # 到达后等待监天盘出现的超时（秒）
TANYAO_ICON = "xuanshu_tanyao.png"                # 探妖按钮模板（使用监天盘后出现，
                                                  # 用户素材 21.png，实测匹配 1.0）
TANYAO_TIMEOUT = 10                               # 点击监天盘后等待探妖出现的超时（秒）
XIANYAO_ICON = "xuanshu_xianyao.png"              # 仙药模板（点击探妖后出现）
XIANYAO_TIMEOUT = 20                              # 等待仙药出现的超时（秒）
JIANTIANPAN_REDO_INTERVAL = 20                    # 流程失败后最短重做间隔（秒，防紧循环）
_jiantianpan_last_done = 0.0                      # 上次流程执行时间戳
# 战斗失败/平局对话框（共享常量，见 dialog_common）
# 每日次数已满文案与跳过/选项几何常量同样在 dialog_common
from .dialog_common import (DAILY_LIMIT_TEXT, DAILY_LIMIT_INFO,
                            DIALOG_LOSE_TEXTS, DIALOG_SKIP_CLICKS,
                            SCREEN_CENTER_POS,
                            dialog_ocr_skip, clear_blocking_dialog,
                            StoryDialogDismisser)
# 每日任务次数已满的对话文案（出现即结束任务循环）
# 需要点击屏幕中间跳过的对话框文案 → 点击次数（出现则连点屏幕中间推进
# 对话，同 kezhan.DIALOG_SKIP_TEXTS）；不同文案要推进的屏数不同，次数
# 按文案单独设置，默认先统一 4 次（DIALOG_SKIP_CLICKS），实测后逐条校准
DIALOG_SKIP_TEXTS = {
    "你是做嘛的？莫来惹老子！": 2,
    "多帮助新人，神仙都会感谢你的！": 4,
    "受害者所闻那水鬼之声出自善口技者之音，你幼时曾学过口技，又时常与人": 1,
    "第二起案子中，被打捞的尸体手脚被渔人结牢牢捆住，那渔人结并非常人所": 1,
    "少侠运气不错": 4,
    "翠花说的难道是这块石头？但是.": 4,
    "刚刚是你再说话？": 4,
    "准备好接受地理方面的考验了么": 4,
    "少侠准备好接受考验了么": 4,
    "少侠准备好接受常识类学问的考验了么": 4,
    "少侠可是来替小乔试装的？": 4,
    "看少侠的表情，旺小乔不满意？": 4,
    "少侠的脸色有点难看": 4,
    # "留香阁的凤姑娘探听到了一些有关玄石的消息": 4,  # 领取后 NPC 追问（无选项，须屏幕中间推进）
    "看来要破解的便是此地的封印了。":1,
    "换一处再试试": 5,
}
# 特定对话框出现时点击的绿色选项（对话框文案 → 绿字选项）
# 三题（口技/绳结/伤口）选项行序随机（实测同屏乱序），只能按文本
# 定位、严禁加进 DIALOG_OPTION_INDEX_MAP 按序号点击；选项 OCR 常
# 漏字误读（「指明伤口一事」→「指明伤口一」），依赖
# _click_green_option_once 的行模糊匹配兜底
DIALOG_OPTION_MAP = {
    "什么江中劫货？休要血口喷人，你有证据吗？": "指明口技之事",
    "哼，这天底下会口技的人多了去了，你怎么就能断定犯事的是我？": "指明绳结之事",
    "那、那又怎么样，这码头上做活的人那么多，你凭什么说一定是我？": "指明伤口一事",
    # 用完整选项文案：短文本「玄枢轶闻」会子串命中本选项并按字符
    # 比例点偏（实测点击 (537,425) 且写入 option_玄枢轶闻 缓存键——
    # 与领取对话框的「玄枢轶闻」选项 (490,427) 共用一键、互相覆写，
    # 领取缓存点击必现点偏 2026-10-25）
    "少侠有什么事吗？": "玄闻辨真（玄枢轶闻）",
    "太阳的别称是什么":"金乌",
    "浑天仪":"观测星星的位置",
    "大唐的都城":"长安",
    "大唐的丝绸":"丝绸之路",
    "大唐人最喜欢喝什么":"茶",
    "大唐人见面行礼":"拱手",
    "有人要偷我们的宝贝，拿下他！":"什么情况",
    "我倒数三个数后大家一起出拳":"出石头",
    "看打！":"看我怎么收拾你",
    "不知道这里有没有那条锦鲤鱼": "先钓一下再说！",
    "这就要少侠自己尝试了":"试试",
    "少侠习惯就好":"试试",
    "少侠想换什么款式":"试试",
    "略略略略略":"都别吵啦",
    "要么要钱，要么要命，你自己选吧":"看打",
    "少侠救我":"看我的",
    "嘿，又来一个上当的，今日又能提前完成修行了！":"竟是一只石妖，看打！",
    "杜必赢使劲晃动杯中的三颗骰子":"轻轻摇晃"
}
# 选项无法被 OCR 识别（如单字选项「茶」）时，按序号点击：
# 选项为问题文案下方的纵向绿色文字列表，第 idx 项（对话框文案 → 序号）。
# 点击位置由 dialog_common 按问题行下方条带的绿色像素行定位（不再用
# 固定偏移——「太阳的别称」选项行 x=439 起、问题行左缘 +1 点落在
# x=438 点空，2026-11-07 实测连点 47 次不推进）
DIALOG_OPTION_INDEX_MAP = {
    "大唐人最喜欢喝什么": 3,  # 茶（咖啡、果汁之后的第 3 项）
    "太阳的别称是什么": 2,    # 金乌（铜乌/金乌/银乌三选项中的第 2 项；
                              # 实测区域 OCR 漏读「金乌」行致文案点击失败，
                              # 按序号兜底点击——2026-10-15）
}
# 序号点击的坐标参数（相对问题行 rect）：首项纵偏移、行间距、横向偏移
# （实测三选项文字中心：咖啡 +12、果汁 +30、茶 +48，间距 18px）
# ---- 封印机关破解（对话框「看来要破解的便是此地的封印了。」出现后） ----
JIGUAN_DIALOG_TEXT = "看来要破解的便是此地的封印了。"
JIGUAN_CLICK_ORDER = ("xuanshu_jiguanhuang.png",   # 点击顺序：黄→蓝→红（用户 2026-10-15 指定）
                      "xuanshu_jiguanlan.png",
                      "xuanshu_jiguanhong.png")
JIGUAN_CLICK_WAIT = 5        # 每次点击机关后等待（秒），随后检查任务追踪
JIGUAN_FIND_TIMEOUT = 8     # 单个机关模板查找超时（秒，找不到跳过继续下一个；
                            # 8s 覆盖角色短暂遮挡——遮挡帧低于阈值移，
                            # 不点击，等遮挡移开后高分局命中；也吸收 Alt+C 降落动画）
JIGUAN_THRESHOLD = 0.6       # 机关模板匹配阈值（2026-10-19 测试按钮实测：黄 0.94、
                             # 红 0.88~1.0、蓝 0.62~0.68，角色遮挡时蓝掉到 0.54
                             # 且最佳位置漂移为误匹配。取 0.6：蓝机关正常分可通过，
                             # 遮挡帧 0.54 不点击——遮挡是瞬时的，靠加长查找窗口
                             # 等其移开；失败时打全帧最佳匹配度日志供再校准）

# 锄遗聚宝小游戏（点击「注入玄蕴试试看」后出现）
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
# ---- 钓鱼小游戏（玄枢轶闻专属：对话框「不知道这里有没有那条锦鲤鱼」→
# 选项「先钓一下再说！」点击后出现）----
# 与客栈破除封印同一款进度条对准小游戏：闭环实现、开始按钮、目标高亮带、
# 进度条测量区域完全共用（pochufengyin 共享模块），仅窗口标题模板与
# 推进方式不同（用户 2026-11-10 提供）：钓鱼开始后按钮变为「长按」
# （xuanshu_diaoyu_changan.png），须按住不放才能推进进度；破除封印为
# 「点击」连点推进。
DIAOYU_ENTRY_OPTION = "先钓一下再说！"          # 入口对话框选项（点击后小游戏窗口出现）
DIAOYU_TITLE_ICON = "xuanshu_diaoyu.png"        # 窗口标题「钓鱼」（消失=任务完成）
DIAOYU_HOLD_ICON = "xuanshu_diaoyu_changan.png"  # 「长按」按钮（开始后出现；按住推进进度）
DIAOYU_ARM_TIMEOUT = _pochu_minigame.POCHU_ARM_TIMEOUT      # 入口选项点击后检测窗口时限（秒）
DIAOYU_CHECK_INTERVAL = _pochu_minigame.POCHU_CHECK_INTERVAL  # 窗口检测限频（秒）
_diaoyu_armed_at = 0.0        # 钓鱼入口选项点击时间；>0 且未超时=检测开启
_diaoyu_last_check = 0.0      # 上次窗口检测时间（限频）
# 循环跟踪任务参数
FOLLOW_TIMEOUT = 300                # 跟踪任务总超时（秒）
FOLLOW_POLL_INTERVAL = 0.5          # 任务信息变化轮询间隔（秒）；配合帧复用，
                                    # 静止帧轮询几乎零成本，动画帧周期由 OCR 主导
CONFIRM_POLL_INTERVAL = 0.2         # 变化确认轮询间隔（秒）；OCR 本身约 1.2s，
                                    # 确认轮询只需极短间隔留出渲染稳定余量
# 任务消失确认（过滤 OCR 偶发漏读/战斗遮挡导致的误判完成——五环实测
# 误判「任务消失」导致完成次数虚增、任务提前结束）：任务从追踪面板
# 消失后连续若干轮询未见、且期间无战斗遮挡面板，才判定任务完成。
# 战斗中不计入确认窗口（战斗画面遮挡追踪面板，OCR 必然读不到任务）。
TASK_GONE_CONFIRM_TIMEOUT = 6.0    # 确认窗口总时长（秒）
TASK_GONE_CONFIRM_POLLS = 2        # 连续未见的轮询次数（面板区域 OCR 约 0.9s）
TASK_GONE_POLL_INTERVAL = 0.5      # 确认轮询间隔（秒）；两轮证据采样点相距
                                   # ~1.4s（0.5 间隔 + 0.9 OCR），面板渲染/遮挡
                                   # 的误判余量仍足（返程交接提速，2026-10-24）
TASK_GONE_GATE_POLL_INTERVAL = 0.2 # 证据已足时等战斗线程确认门的短轮询间隔（秒）：
                                   # 不再 OCR，门开即返回（返程交接省 ~1.7s/轮）
# 跟踪轮询的 OCR 区域见 task_tracker（TRACK_REGION/DIALOG_REGION 静态兜底，
# 运行时由 task_tracker.ocr_follow 按标题条模板动态定位）
# 任务消失确认用面板条带（返程链路热路径，2026-11-06 实测标定）：
# 面板全区 (0,200,400,600) OCR 约 0.9s/轮 ×2 轮是返程延迟大头。
# 实测面板布局：标题行 y230-254、内容链接行 y250-291（多轮日志点击
# y258-291 一致）；确认只需标题行（find_task）与各任务的兜底链接行
# （五倍消灭黄袍怪/消灭魔尊/形镜/锁妖网/奎木狼、修罗消灭修罗、五环
# 子目标替身/武痴等，均为前几行内容行）——条带取 (0,210,400,470)，
# 标题+约 9 行内容行，最深实测链接 291 下留约 180px 余量，OCR 降至
# ~0.6s。兜底行落到条带外的偶发漏判由主循环 has_task 全区域复查兜住
# （判错任务消失 → 复查发现仍在 → 跳过返程继续跟踪，~2s 自愈）。
# 静态值为标定基准；运行时由 _task_gone_region 按内容区位置动态推导
# （内容区顶=标题条底≈227：条带 top=内容区顶-18≈210、bottom=+242≈470，
# 与标定值一致，面板漂移时跟随）。标题条未命中（面板被关闭/折叠）
# 时 _task_gone_region 返回 None，该帧不计任务消失证据，不回退
# 静态条带——面板不在场时条带 OCR 必然为空，回退会把「面板关闭」
# 误判成「任务消失」（2026-11-11 用户实机）。
TASK_GONE_REGION = (0, 210, 400, 470)


def _task_gone_region(img_np):
    """任务消失确认条带（动态）：内容区上下扩边。

    相对标定 TASK_GONE_REGION=(0,210,400,470) 与内容区顶（标题条底）
    的偏移：top=内容区顶-18、bottom=内容区顶+242；横向取内容区左右缘
    （标题/链接行均在区内，右侧收窄同时减少 OCR 面积）。标题条模板
    未命中返回 None——此时追踪面板不在场（被手动关闭/折叠或遮挡），
    面板内任务列表整体不可见，_confirm_task_gone 不得据此判定任务
    消失（2026-11-11 用户实机教训）。
    """
    region = task_tracker.track_content_region(img_np)
    if region is None:
        return None
    l, t, r, _b = region
    return (l, max(0, t - 18), r, min(img_np.shape[0], t + 242))


# 面板差分门见 task_tracker.TrackGate（共享组件，领取校验循环同用；
# 门区域/兜底时限为实测校准资产，见 task_tracker.TRACK_GATE_*）
# 战斗中 extras 对话框 OCR 限频（秒）：战斗动画帧帧不同、差分门失效，
# 限频替代门控（同 WUYI_CHECK_INTERVAL 模式）。战斗中任务对话
# （如五环提交装备）仍被处理，仅至多延迟该间隔。
BATTLE_EXTRAS_INTERVAL = 2.0


def _at_target(pos):
    """判断位置是否在任务目标附近（目标非坐标时恒 False）。"""
    if TASK_MAP_POS is None:
        return False
    return game_map.is_at_position(pos, LUOYANG_CITY,
                                   TASK_MAP_POS[0], TASK_MAP_POS[1],
                                   MAP_POS_TOLERANCE)


def wait_arrive(win, timeout=ARRIVE_TIMEOUT):
    """等待寻路完成。

    目标为坐标（TASK_MAP_POS 非空）：到达目标 5px 内即完成；
    通用兜底（含目标非坐标）：坐标连续两次不变且出现领取按钮即完成
    （距目标过近时点击任务点游戏可能不响应寻路，靠此兜底推进领取）；
    位置 20 秒无变化且无领取按钮时提前失败（避免长时间无响应）。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    last = None
    unchanged = 0
    polls = 0
    while time.time() < deadline:
        polls += 1
        if is_in_battle(win):
            # 战斗中：坐标显示不可用，跳过本轮识别（不计入稳定性）
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if TASK_MAP_POS is not None:
            if game_map.is_at_position(pos, LUOYANG_CITY,
                                       TASK_MAP_POS[0], TASK_MAP_POS[1],
                                       MAP_POS_TOLERANCE):
                return True, f"已到达目标附近 {LUOYANG_CITY}[{TASK_MAP_POS[0]},{TASK_MAP_POS[1]}]"
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 2 and game_map.find_template(win, TASK_LINGQU_ICON) is not None:
            return True, "位置稳定且出现领取任务按钮"
        if unchanged >= 10:
            return False, "位置 20 秒无变化且未出现领取按钮，寻路无响应"
        if polls % 5 == 0:
            log(f"[玄枢轶闻] 寻路等待中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"等待寻路完成超时（{timeout}s）"


def _jiantianpan_active(items):
    """追踪面板是否出现监天盘子任务文案（跨行拼接后模糊匹配）。"""
    panel_txt = "".join(t for t, r, _ in items if r[0] < task_tracker.PANEL_MAX_X)
    return _fuzzy_contains(norm_text(panel_txt), JIANTIANPAN_TRACK_TEXT)


def _at_huoyun_target(pos):
    """是否已在火云戈壁目标附近（严格容差；地名抗 OCR 误读）。"""
    if pos is None:
        return False
    x, y = JIANTIANPAN_TARGET_POS
    if not (abs(pos[1] - x) <= JIANTIANPAN_ARRIVE_TOLERANCE
            and abs(pos[2] - y) <= JIANTIANPAN_ARRIVE_TOLERANCE):
        return False
    name = pos[0].replace("雲", "云").strip()
    if name == "火云戈壁":
        return True
    return len(name) >= 3 and difflib.SequenceMatcher(
        None, name, "火云戈壁").ratio() >= 0.7


def _wait_jiantianpan_arrive(win, timeout=JIANTIANPAN_ARRIVE_TIMEOUT):
    """等待走到火云戈壁目标附近（严格容差 + 连续两次同坐标确认停稳）。

    点击小地图后人物可能路过目标附近但不停下，必须停稳才算到达
    （仙药只在目标附近出现，附近要求严格）。
    """
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if _at_huoyun_target(pos) and pos == last:
            return True, f"已到达火云戈壁目标附近 {pos[0]}[{pos[1]},{pos[2]}]（严格容差）"
        last = pos
        time.sleep(2.0)
    return False, "前往火云戈壁目标附近超时（严格容差未达成）"


def _click_template_wait(win, icon, label, timeout, threshold=0.8):
    """轮询等待模板出现并点击。返回 (是否成功, 描述)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        pt = game_map.find_template(win, icon, threshold=threshold)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log(f"[玄枢轶闻] 已点击「{label}」@({pt[0]},{pt[1]})", "SYS")
            return True, f"已点击「{label}」"
        time.sleep(0.5)
    return False, f"未找到「{label}」模板（{timeout}s 超时）"


def _handle_jiantianpan(win, items, img_np):
    """监天盘子任务（follow_task 附加处理回调）。

    追踪出现「使用监天盘寻找石妖踪迹」时执行：
    世界地图点火云戈壁 → 小地图点 (744,441) 寻路 → 严格等待到达
    火云戈壁 183,81 附近 → 点击监天盘 → 点击探妖（用户 2026-10-09
    提供模板）→ 等待仙药出现并点击。
    已在目标附近时跳过寻路直接点监天盘（失败重试幂等）。
    流程失败后 JIANTIANPAN_REDO_INTERVAL 内不重试（防紧循环）。
    返回是否已处理（执行过流程即 True，即使失败）。
    """
    global _jiantianpan_last_done
    if not _jiantianpan_active(items):
        return False
    if time.time() - _jiantianpan_last_done < JIANTIANPAN_REDO_INTERVAL:
        return False
    _jiantianpan_last_done = time.time()
    log("[玄枢轶闻] 检测到「使用监天盘寻找石妖踪迹」，执行监天盘流程", "SYS")
    if not _at_huoyun_target(game_map.read_map_position(win)):
        ok, info = game_map.open_mini_map_city(win, JIANTIANPAN_WORLD_ICON, "火云戈壁")
        if not ok:
            log(f"[玄枢轶闻] 监天盘流程寻路失败: {info}", "WARN")
            game_map.close_maps(win)
            return True
        human_click(win, JIANTIANPAN_MAP_CLICK_POS[0], JIANTIANPAN_MAP_CLICK_POS[1])
        log("[玄枢轶闻] 已点击火云戈壁小地图寻路点，自动寻路中", "SYS")
        time.sleep(1.0)  # 等待自动寻路启动
        game_map.close_maps(win)  # 关闭残留地图，避免遮挡监天盘
        ok, info = _wait_jiantianpan_arrive(win)
        if not ok:
            log(f"[玄枢轶闻] {info}", "WARN")
            return True
        log(f"[玄枢轶闻] {info}", "SYS")
    ok, info = _click_template_wait(win, JIANTIANPAN_ICON, "监天盘",
                                    JIANTIANPAN_CLICK_TIMEOUT,
                                    threshold=JIANTIANPAN_THRESHOLD)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
        return True
    # 使用监天盘后点「探妖」（同一右下角按钮区，用户 2026-10-09 确认顺序）
    ok, info = _click_template_wait(win, TANYAO_ICON, "探妖", TANYAO_TIMEOUT)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
        return True
    ok, info = _click_template_wait(win, XIANYAO_ICON, "仙药", XIANYAO_TIMEOUT)
    if not ok:
        log(f"[玄枢轶闻] {info}", "WARN")
    else:
        log(f"[玄枢轶闻] {info}", "SYS")
    return True


from .dialog_common import (_click_green_option_once as _dc_click_green_option_once,
                            _click_dialog_option_by_index as _dc_click_dialog_option_by_index,
                            _click_screen_center as _dc_click_screen_center,
                            _fuzzy_contains, _dialog_text_present,
                            close_scene_panels)


# 领取链路绝不点击的选项行：「放弃本次玄枢轶闻任务」与「领取玄枢轶闻」
# 「玄枢轶闻」互为子串——搜「领取玄枢轶闻」时 0.8 模糊匹配会命中
# 「玄枢轶闻」（ratio 恰 0.8）和含「玄枢轶闻」的放弃行；搜「玄枢轶闻」
# 时子串直配也会命中放弃行（2026-11-10 日志实测 3 次误点放弃选项）。
# 放弃选项被打中可能丢掉已领取任务（哪怕还要二次确认），从候选行
# 直接剔除、任何路径都不点；代价仅是目标为放弃选项时本轮不推进。
_DIALOG_OPTION_EXCLUDE = ("放弃",)


def _click_green_option_once(win, items, img_np, text, prefix="玄枢轶闻"):
    """前缀包装（共享实现在 dialog_common；默认玄枢轶闻前缀）。

    过滤危险兄弟选项（见 _DIALOG_OPTION_EXCLUDE）：放弃类选项与
    领取类选项互为子串，模糊/子串匹配会误点，一律不参与候选。
    """
    items = [it for it in items
             if not any(x in it[0] for x in _DIALOG_OPTION_EXCLUDE)]
    return _dc_click_green_option_once(win, items, img_np, text, prefix)


def _click_dialog_option_by_index(win, items, img_np, dtxt, idx):
    """玄枢轶闻前缀包装（共享实现在 dialog_common）。"""
    return _dc_click_dialog_option_by_index(win, items, img_np, dtxt, idx, "玄枢轶闻")


def _option_present(items, text):
    """选项行是否可见（以 text 开头的行，限定对话框区域）。"""
    return any(t.startswith(text) and s >= 0.55
               and r[0] >= task_tracker.PANEL_MAX_X
               for t, r, s in items)


def _wait_option_visible(win, text, abort_text, timeout=4.0):
    """等对话框渲染出选项行（以 text 开头）；期间出现 abort_text 返回 False。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if abort_text and _dialog_text_present(items, abort_text):
            return False
        if _option_present(items, text):
            return True
        time.sleep(0.5)
    return False


def _option_gone(win, text, timeout=3.0):
    """点击后选项行是否消失（对话框已推进）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if not _option_present(items, text):
            return True
        time.sleep(0.5)
    return False


def _click_dialog_green_option(win, text, timeout=DIALOG_TIMEOUT, abort_text=None):
    """等待对话框出现，点击其中指定文案的绿色选项。

    等待期间若出现 abort_text 文案（如每日次数已满）则提前中止。
    高性能：选项坐标已缓存 → 先等选项渲染出现（点前确认）→ 按缓存
    坐标点击 → 点后确认选项消失（推进）；任一步不满足则清缓存回退
    OCR 识别路径（缓存中心被合并行毒化的实测教训；点后单端校验会把
    「点击已生效、对话框快速推进」误判为未生效）。返回 (是否成功, 描述)。
    """
    if app_utils.high_perf_on():
        pos = app_utils.fixed_pos_get(f"option_{text}", win)
        if pos is not None:
            if _wait_option_visible(win, text, abort_text):
                time.sleep(0.3)  # 渲染稳定
                human_click(win, pos[0], pos[1])
                log(f"[玄枢轶闻][高性能] 已按缓存坐标点击「{text}」 ({pos[0]}, {pos[1]})", "SYS")
                if _option_gone(win, text):
                    return True, f"已按缓存坐标点击对话框绿色选项「{text}」"
                app_utils.fixed_pos_clear(f"option_{text}")
                log(f"[玄枢轶闻][高性能] 缓存坐标点击「{text}」未生效，清缓存回退识别", "WARN")
            else:
                app_utils.fixed_pos_clear(f"option_{text}")
                log(f"[玄枢轶闻][高性能] 点击前选项「{text}」未出现"
                    f"（对话框未开或状态已变），清缓存回退识别", "WARN")
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if abort_text and _dialog_text_present(items, abort_text):
            return False, f"检测到「{abort_text}」"
        if _click_green_option_once(win, items, img_np, text):
            return True, f"已点击对话框绿色选项「{text}」"
        time.sleep(0.5)
    return False, f"未找到对话框绿色选项「{text}」"


def navigate_to_task(win):
    """寻路到玄枢轶闻任务位置（洛阳城 93,125）并领取任务。

    1. 场景前置：关闭已打开的小地图/世界地图/物品栏
    2. 识别当前位置（失败一律当不在目标坐标，不重试）
    3. 不在目标附近时寻路：在洛阳城 → 小地图点击任务点；不在 → 世界地图 → 洛阳城 → 小地图
    4. 等待寻路完成（到达目标附近；目标非坐标时位置稳定且出现领取按钮）
    5. 点击领取任务按钮 → 对话框 1 点「玄枢轶闻」→ 对话框 2 点「领取玄枢轶闻」
    6. 校验任务追踪中出现玄枢轶闻任务

    返回 (是否成功, 描述, 校验命中时的 (items, img_np) 或 None)——成功时
    附带领取校验最后轮询的 OCR 结果，供 follow_task 复用省一次截图+OCR。
    """
    # 战斗中：坐标与界面不可用，跳过本轮（由主循环稍后重试）
    if is_in_battle(win):
        return False, "当前战斗中，跳过本轮寻路", None

    # 场景前置：清理残留地图（小地图在上层，先关）与物品栏
    close_scene_panels(win, "玄枢轶闻")

    # 识别失败一律当不在目标坐标处理：不按 Alt+C 重试（重试无收益
    # 且拖慢轮次），直接走世界地图路径，寻路完成后位置自然恢复
    pos = game_map.read_map_position(win)
    if pos is None:
        name = loc = "识别失败"
        log("[玄枢轶闻] 位置识别失败，按不在目标坐标处理", "WARN")
    else:
        name, x, y = pos
        loc = f"{name}[{x},{y}]"

    at_target = _at_target(pos)
    if at_target:
        log(f"[玄枢轶闻] 已在任务位置附近（{loc}），跳过寻路", "SYS")
    elif name == LUOYANG_CITY:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info, None
        log(f"[玄枢轶闻] 在洛阳城（{loc}），已打开小地图", "SYS")
    else:
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON, "洛阳城")
        if not ok:
            return False, info, None
        log(f"[玄枢轶闻] 不在洛阳城（{loc}），经世界地图前往洛阳城", "SYS")

    if not at_target:
        human_click(win, MAP_CLICK_POS[0], MAP_CLICK_POS[1])
        log(f"[玄枢轶闻] 已点击小地图任务点，自动寻路中", "SYS")
        time.sleep(1.0)  # 等待自动寻路启动
        # 清理：关闭仍打开的小地图（点击任务点后仍打开）、世界地图（通常已自动关闭）与物品栏
        close_scene_panels(win, "玄枢轶闻")

    # 等待寻路完成
    ok, info = wait_arrive(win)
    if not ok:
        return False, info, None
    log(f"[玄枢轶闻] {info}", "SYS")

    # 领取任务（按钮随站位渲染位置变化，必须识图定位，不缓存）
    pt = game_map.find_template(win, TASK_LINGQU_ICON)
    if pt is None:
        # 对话框遮挡兜底：到达时若有对话框（NPC 欢迎语/奇遇弹窗）会
        # 挡住领取按钮，点屏幕中间关闭后重试（2026-11-07 用户反馈）
        had, _ = clear_blocking_dialog(win, "玄枢轶闻")
        if had:
            pt = game_map.find_template(win, TASK_LINGQU_ICON)
            if pt is not None:
                log("[玄枢轶闻] 关闭遮挡对话框后已找到领取任务按钮", "SYS")
    if pt is None:
        return False, "已到达但未找到领取任务按钮", None
    human_click(win, pt[0], pt[1])
    log("[玄枢轶闻] 已点击领取任务", "SYS")

    # 对话框 1：点击绿色选项「玄枢轶闻」（每日次数已满时出现「请明天再来」文案）
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_XUANSHU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO, None) if DAILY_LIMIT_TEXT in info \
            else (False, info, None)
    # 对话框 2：点击绿色选项「领取玄枢轶闻」
    ok, info = _click_dialog_green_option(win, DIALOG_OPTION_LINGQU,
                                          abort_text=DAILY_LIMIT_TEXT)
    if not ok:
        return (False, DAILY_LIMIT_INFO, None) if DAILY_LIMIT_TEXT in info \
            else (False, info, None)

    # 校验任务追踪中出现玄枢轶闻任务；期间检测每日次数已满文案
    # （满 5 次提示出现在点击「领取玄枢轶闻」之后）。
    # 命中时把 OCR 结果带回，供 follow_task 复用，省一次截图+OCR
    deadline = time.time() + DIALOG_TIMEOUT
    track_gate = task_tracker.TrackGate(min_interval=0.0)  # 面板差分门（领取校验短窗口
        # 关键检测，不限频——同修罗 _claim_task，防「未出现」结论被
        # 复用 2s 拖慢领取（2026-10-28）
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            items = track_gate.ocr(img)
            items2, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
            img_np = np.zeros_like(np.asarray(img))
            track_gate.paste_track(img, img_np)
            if _dialog_text_present(items2, DAILY_LIMIT_TEXT):
                return False, DAILY_LIMIT_INFO, None
            if task_tracker.has_task_in(items, img_np, TASK_TITLE):
                # 任务刚出现时领取对话框还在切换/面板刚渲染，布局可能未稳定
                # （实测首帧坐标点击落空、未开始寻路）：等待约 1.5s 后重新
                # 截帧 OCR，把稳定帧带回 follow_task 保证首次点击落点准确
                time.sleep(1.5)
                img2 = win.capture(force_activate=False)
                if img2 is not None:
                    items3, img_np3 = task_tracker.ocr_track(img2)
                    if task_tracker.has_task_in(items3, img_np3, TASK_TITLE):
                        return True, "已领取任务", (items3, img_np3)
                return True, "已领取任务", (items, img_np)  # 复检失败按首帧返回
        time.sleep(0.2)
    return False, "已点击领取玄枢轶闻，但任务追踪中未检测到任务", None


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


def _current_task_sig(win, title):
    """当前任务签名（仅 OCR 追踪面板区域）；任务不存在返回 ""。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = task_tracker.ocr_track(img)
    task = task_tracker.find_task(
        task_tracker.group_tasks(items,
                                 task_tracker.panel_row_xmax(np.asarray(img))),
        title)
    if task is None:
        return ""
    return task_tracker.task_signature(task)


def _task_done_or_changed(win, title, sig0):
    """任务追踪是否消失或签名变化（相对 sig0；sig0 None 表示读不到，
    此时不判变化）。"""
    sig = _current_task_sig(win, title)
    if sig is None:
        return False
    if sig == "":
        return True  # 任务从追踪中消失
    return sig0 is not None and sig0 != "" and not _signatures_similar(sig, sig0)


def _play_jiguan_puzzle(win, title):
    """封印机关破解（用户 2026-10-15 流程）：

    提示对话框点击一次关闭 → Alt+C 降落 → 按 黄→蓝→红 顺序识别并
    点击机关模板；每次点击后等待 5s，任务追踪消失或签名变化即停止
    （不再点剩余机关）。返回 (是否执行了流程, 描述)。
    """
    # 1. 点击一次关闭提示对话框（屏幕中间；该对话框无选项）
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[玄枢轶闻] 已点击关闭封印提示对话框", "SYS")
    time.sleep(0.5)
    # 2. Alt+C 降落（飞行状态下落地，机关才可见可点）；
    #    静默操作：走消息投递（无需前台，2026-11-03 实测有效）
    if app_utils.silent_mode_on():
        from . import silent_click  # 延迟导入：避免模块级循环
        silent_click.send_silent_hotkey(win, ("alt",), "c")
        log("[玄枢轶闻] 已静默 Alt+C 降落", "SYS")
        time.sleep(1.0)
    elif app_utils.keyboard_ok(win):
        Keyboard.key_hot("alt", "c")
        log("[玄枢轶闻] 已按 Alt+C 降落", "SYS")
        time.sleep(1.0)
    else:
        log("[玄枢轶闻] 游戏不在前台：跳过 Alt+C 降落", "WARN")
    # 3. 按序点击机关（每次点击后 5s 检查任务追踪）
    sig0 = _current_task_sig(win, title)
    clicked = 0
    for icon in JIGUAN_CLICK_ORDER:
        deadline = time.time() + JIGUAN_FIND_TIMEOUT
        pt = None
        while time.time() < deadline:
            pt = game_map.find_template(win, icon, threshold=JIGUAN_THRESHOLD)
            if pt is not None:
                break
            time.sleep(0.5)
        if pt is None:
            # 诊断：记录全帧最佳匹配度与位置，区分「分数差一点」与
            # 「模板/场景不符」（后者分数会远低于阈值，须换模板）
            conf, (bx, by) = (0.0, (0, 0))
            img = win.capture(force_activate=False)
            if img is not None:
                conf, (bx, by) = game_map.best_match_in(img, icon)
            log(f"[玄枢轶闻] 未找到机关 {icon}，跳过"
                f"（全帧最佳匹配度 {conf:.2f} @({bx},{by})）", "WARN")
            continue
        human_click(win, pt[0], pt[1])
        clicked += 1
        log(f"[玄枢轶闻] 已点击机关 {icon} @({pt[0]},{pt[1]})，等待 {JIGUAN_CLICK_WAIT}s", "SYS")
        time.sleep(JIGUAN_CLICK_WAIT)
        if _task_done_or_changed(win, title, sig0):
            return True, f"封印破解完成（点击 {clicked} 个机关后任务追踪变化）"
    return clicked > 0, f"封印破解流程结束（点击 {clicked} 个机关，任务追踪未变化）"


def verify_jiguan_templates(win):
    """高级设置-测试机关模板按钮：对当前画面逐一测试三个机关模板，
    打印全帧最佳匹配度与位置日志（只读不点击，供阈值/模板校准）。
    返回 (是否成功, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        log("[玄枢轶闻] 测试机关模板: 截图失败", "WARN")
        return False, "截图失败"
    for icon in JIGUAN_CLICK_ORDER:
        conf, (x, y) = game_map.best_match_in(img, icon)
        hit = conf >= JIGUAN_THRESHOLD
        log(f"[玄枢轶闻] 测试机关模板 {icon}: 全帧最佳匹配度 {conf:.2f}"
            f" @({x},{y}){'（≥阈值，任务会点击）' if hit else '（<阈值，任务不会点击）'}",
            "SYS" if hit else "WARN")
    return True, "三个模板已测试，结果见日志"


# ---- 封印按序开启任务（花果山「先触南，再寻东，后破西」，2026-10-19 实测） ----
# 三个可跟踪绿字（机关·东/南/西）嵌在描述行内、文案颜色恒不变，须按
# 南→东→西 点击；每点完一处面板出现「第N处封印已开启」——进度信号。
# green_order 不适用（关键词恒绿无法序进），用 progress_key（已开启
# 计数）+ green_picker（按计数选关键词、链接下划线实线段定位点击——
# OCR 文本比例映射会因行拆分/误读点错，实测 2026-10-25）实现；
# 回调在非本任务阶段自门控不介入（progress_key 返回 None、picker 返回
# None 回退 track_task 行级选择）。
JIGUAN_LINK_ORDER = ("南", "东", "西")   # 点击顺序（面板提示原文：先触南，再寻东，后破西）
# 链接行绿字簇序（按 x 左→右）→ 关键词：面板固定渲染「机关·东、
# 机关·南、机关·西」（实测簇 x 24-88 / 106-170 / 188-220，2026-10-25）
JIGUAN_LINK_CLUSTER = {"东": 0, "南": 1, "西": 2}
# 进度信号用词兼容「已开启」/「已解除」两种变体（实测 2026-10-25 截图
# OCR 为「已开启」，用户确认亦有「已解除」文案的轮次变体）
JIGUAN_PROGRESS_RE = re.compile(r"第\s*([一二三四五1-5])\s*处\s*封印\s*已\s*(?:开启|解除)")
_JIGUAN_ORD = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}


def _jiguan_link_count(items):
    """面板「第N处封印已开启」的最大序数（无则 None）。"""
    best = None
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        for m in JIGUAN_PROGRESS_RE.finditer(txt):
            n = _JIGUAN_ORD.get(m.group(1))
            if n is None and m.group(1).isdigit():
                n = int(m.group(1))
            if n is not None and (best is None or n > best):
                best = n
    return best


def _jiguan_link_stage(items, img_np):
    """是否处于封印按序开启阶段：面板存在含「机关」的绿字行
    （描述行绿字占比实测 8~9%，提示行无绿字）。"""
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if "机关" in txt and task_tracker.is_green_text(img_np, rect):
            return True
    return False


def _jiguan_link_progress(items, img_np):
    """封印按序开启进度键：已开启计数；阶段内未开为 0（与点击前
    基线一致，点后 1/2/3 变化即推进），阶段外 None（交由默认
    文本签名驱动）。"""
    n = _jiguan_link_count(items)
    if n is not None:
        return str(n)
    return "0" if _jiguan_link_stage(items, img_np) else None


def _jiguan_link_picker(items, img_np):
    """按已开启计数选下一个关键词（南→东→西）并定位点击。

    布局随进度变化（每点开一处任务描述即改写，2026-10-26 用户确认）：
    - 0 处已开（描述「根据桃小淘所言，前往花果…」）：链接行折两行——
      上行「…机关·东、机关」+ 下行「·南、机关·西三处封印。」，南的标签
      「机关·南」被折行拆成两段（上行行尾「机关」+ 下行行首「·南」），
      共 4 段实线下划线（实测 y287：东 113-172、南A 188-217；y304：
      南B 23-55、西 73-137）。此时只能点南：取最底行最左段（南B）。
    - 1/2 处已开（描述「第一/二处封印已开启，再去开启…」）：链接行仍
      折两行但主行含 3 段——上行「机关·东、机关·南、机关」+ 下行「·西
      …」尾段（实测 y288：东 24-88、南 106-170、西A 188-220；y305：
      西B 24-56 单段尾行）。点法：找 3 段主行按 x 序映射（东/南/西），
      不能按最底行取（会取到 1 段的西B 尾行）。

    扫描取含「机关」行的纵向邻域（-10/+30px：下行底 307 vs 上行底
    289，差 18px，OCR 漏读下行时也能扫到其下划线；碎片行可能不含
    机关关键词却承载链接）。点击取下划线上方 2px（行底可点区规则）。
    阶段内链接无法解析时返回 PICK_FAIL（track_task 拒绝行级兜底点击
    并重试——行级兜底按最长实线段点会点错机关：2026-10-26 实测点南
    落到西、顺序破坏任务卡死）。非封印阶段返回 None（回退行级选择，
    如领取后的寻路链接）。计数用尽（≥3）返回 None。
    """
    n = _jiguan_link_count(items)
    if n is None:
        if not _jiguan_link_stage(items, img_np):
            return None  # 非封印阶段不介入（防其他任务绿字行含南/东/西误选）
        n = 0
    if n >= len(JIGUAN_LINK_ORDER):
        return None
    y_top = y_bot = None
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if "机关" in txt:
            y_top = rect[1] if y_top is None else min(y_top, rect[1])
            y_bot = rect[3] if y_bot is None else max(y_bot, rect[3])
    if y_top is None:
        return None
    y_top = max(0, y_top - 10)
    y_bot = min(img_np.shape[0], y_bot + 30)
    region = img_np[y_top:y_bot, 0:task_tracker.PANEL_MAX_X].astype(int)
    mask = task_tracker.green_mask(region, task_tracker.GREEN_MIN_G)
    # 收集所有含实线段（≥20px 连续亮绿列）的行（自底向上）：下划线可跨
    # 1~2 行，字形笔画连排实测 ≤13px 不构成实线段
    run_rows = []
    for rr in range(mask.shape[0] - 1, -1, -1):
        cols = np.where(mask[rr])[0]
        if cols.size < 20:
            continue
        segs = []
        start = prev = cols[0]
        for c in cols[1:]:
            if c != prev + 1:
                segs.append((int(start), int(prev)))
                start = c
            prev = c
        segs.append((int(start), int(prev)))
        solid = sorted(s for s in segs if s[1] - s[0] + 1 >= 20)
        if solid:
            run_rows.append((y_top + rr, solid))
    if not run_rows:
        return task_tracker.PICK_FAIL  # 阶段内实线段全无：拒绝行级兜底
    # 主链接行优先：含 3 段实线段的行（1/2 处已开布局：主行「机关·东、
    # 机关·南、机关」+ 下行「·西…」尾段——实测 y288 东 24-88 / 南
    # 106-170 / 西A 188-220，y305 西B 24-56 单段尾行）。不能按最底行
    # 取（会取到 1 段的西B 尾行），须在全部行中找 3 段主行
    for y0, runs0 in run_rows:
        if len(runs0) == 3:
            # 按 x 序映射（东/南/西）
            ci = JIGUAN_LINK_CLUSTER[JIGUAN_LINK_ORDER[n]]
            cx = (runs0[ci][0] + runs0[ci][1]) // 2
            return cx, y0 - 2  # 下划线上方 2px
    if n == 0 and len(run_rows) == 2 and len(run_rows[0][1]) == 2 \
            and len(run_rows[1][1]) == 2:
        # 折行布局（初始态）：上下两行各 2 段，南的可点部分是下行
        # 最左段（「·南」）。要求两行俱全——上行孤存（下行被遮挡/
        # 渲染中）时其左段是东，不能点
        y0, runs0 = run_rows[0]
        cx = (runs0[0][0] + runs0[0][1]) // 2
        return cx, y0 - 2
    return task_tracker.PICK_FAIL  # 下划线形态异常：拒绝行级兜底


def _play_chuyijuebao(win, timeout=CHUYIJUEBAO_TIMEOUT):
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
        log(f"[玄枢轶闻] {info_act}", "SYS" if ok_act else "WARN")
        pt = game_map.find_template(win, CHUYIJUEBAO_START_ICON)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            log("[玄枢轶闻] 已点击锄遗聚宝开始", "SYS")
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
                    log(f"[玄枢轶闻] 调试: 检测到 {len(clusters)} 个杂气目标"
                        f"（超面积团块 {oversized} 个），已标注保存 {path}", "SYS")
                    log(f"[玄枢轶闻] 调试: 团块 "
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
                    log(f"[玄枢轶闻] 点击无效拉黑位置 ({pos0[0]},{pos0[1]})（静态红物防护）", "WARN")
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
            log(f"[玄枢轶闻] 锄遗聚宝超时诊断: 最后一帧 {len(clusters)} 个目标，"
                f"已保存 {path}", "WARN")
    except Exception:
        pass
    return False, (f"锄遗聚宝超时（{timeout}s，主图未消失；共消除 {clicked} 个，"
                   f"拆分点击 {split_clicked}，超面积团块 {oversized_frames} 帧，"
                   f"单帧最多 {max_clusters_seen} 个）")


def _arm_diaoyu_check(opt):
    """钓鱼入口选项被点击 → 开启小游戏窗口检测（armed 门控）。

    只有「先钓一下再说！」被实际点击后才检测 xuanshu_diaoyu.png 标题，
    其他任务阶段不跑这个模板匹配（无开销、无误配）。重复点击入口选项
    会刷新时限；窗口处理完成或超时后检测自动关闭。
    """
    global _diaoyu_armed_at
    if opt == DIAOYU_ENTRY_OPTION:
        _diaoyu_armed_at = time.time()
        log(f"[玄枢轶闻] 已点击钓鱼入口选项，开启小游戏窗口检测"
            f"（{DIAOYU_ARM_TIMEOUT}s 内有效）", "SYS")


def _diaoyu_window_check(win, img, prefix="玄枢轶闻"):
    """检测并执行钓鱼小游戏（armed 门控 + 限频），返回是否已处理。

    入口选项「先钓一下再说！」被点击后（_arm_diaoyu_check）才在
    POCHU_REGION 内匹配窗口标题 xuanshu_diaoyu.png（搜索条带/阈值与
    客栈破除封印完全一致）；窗口出现即阻塞执行完整闭环（共享实现
    pochufengyin.play_pochufengyin，长按模式：开始后按钮为「长按」，
    按住才推进），标题消失（结束）或战斗打断后返回。检测限频
    DIAOYU_CHECK_INTERVAL（条带匹配 ~10ms）；未 armed/限频未到/
    未命中均返回 False，热路径零成本。
    """
    global _diaoyu_last_check, _diaoyu_armed_at
    if _diaoyu_armed_at <= 0 \
            or time.time() - _diaoyu_armed_at > DIAOYU_ARM_TIMEOUT:
        return False
    now = time.time()
    if now - _diaoyu_last_check < DIAOYU_CHECK_INTERVAL:
        return False
    _diaoyu_last_check = now
    bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
    score, wpt = game_map.best_match_np(bgr, DIAOYU_TITLE_ICON,
                                        region=_pochu_minigame.POCHU_REGION)
    if wpt is None or score < _pochu_minigame.POCHU_THRESHOLD:
        return False
    log(f"[{prefix}] 检测到钓鱼窗口 @({wpt[0]},{wpt[1]})"
        f"（匹配度 {score:.2f}）", "SYS")
    ok, info = _pochu_minigame.play_pochufengyin(
        win, DIAOYU_TITLE_ICON, prefix=prefix, label="钓鱼",
        hold=True, action_icon=DIAOYU_HOLD_ICON)
    if ok:
        # 小游戏完成 → 关闭检测（处理失败保持 armed，窗口仍在时
        # 下轮询重试）
        _diaoyu_armed_at = 0.0
    log(f"[{prefix}] {info}", "SYS" if ok else "WARN")
    return True


def _signatures_similar(a, b, ratio=0.8):
    """任务签名相似判定：OCR 抖动的细微差异视为同一任务状态。

    任务未真实变化时 OCR 可能产生 1~2 字符的抖动（如标点、单字误读），
    相似度达到 ratio 即视为相同，避免据此重复点击绿字。
    """
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio




def _frames_equal(a, b):
    """两帧像素完全一致（全分辨率逐像素比较，约 2-4ms）。

    仅全等才复用上一帧 OCR 结论；降采样比较会把微小变化抹平，
    导致缓慢浮现的对话框延迟检出，故不用。
    """
    if a is None or b is None or a.size != b.size:
        return False
    return bool((np.asarray(a) == np.asarray(b)).all())



def _confirm_task_gone(win, title, fallback_texts=(), first_img=None,
                       polls=TASK_GONE_CONFIRM_POLLS, require_panel=True):
    """确认任务确实从追踪面板消失（连续轮询未见且无战斗遮挡）。

    OCR 偶发漏读（标题误读、面板瞬时遮挡）会误判任务消失——五环实测
    误判「任务消失」导致完成次数虚增、任务提前结束；战斗画面遮挡追踪
    面板时 OCR 必然读不到任务，战斗中不计入确认窗口（延长窗口等战斗
    结束后再观察）。返回 True=确认消失（任务完成）。

    require_panel：追踪面板（renwuzhuizong.png 标题条）不在场时是否
    拒绝判定完成。True（默认，非战斗轮询）：面板被用户手动关闭/折叠
    时任务列表整体不可见，读不到任务不能当作任务消失（2026-11-11
    用户实机寻路中关面板被误判完成）；False（战斗结束后的确认路径）：
    用户 2026-11-11 确认「战斗结束后任务追踪会消失，此时应打开背包
    返程」——面板缺席按消失处理，回退静态条带按原逻辑采集证据。

    first_img：调用方本轮已截取的帧（PIL）——战斗结束转换帧上的首个
    确认轮询跳过截图直接 OCR（返程链路上省一次截窗 ~0.3s）。

    fallback_texts：标题漏检防误判兜底——修罗实测标题行整个被区域
    OCR 漏检（彩色徽标前缀），领取后首次检查即误判「任务消失」，
    3s 假完成一轮、下一轮卡等 20s 领取按钮（任务激活时按钮不出现）。
    内容链接行「前往…消灭修罗」检测稳定，面板左区内出现任一兜底
    串即判定任务仍在（立刻中止确认）。

    战斗结束后的延迟优化（实测战斗画面已清 → 返程启动等 3~4s）：
    战斗线程的 2s 结束确认（防回合间隙误判）与任务消失确认原本串行，
    合计 4~6s。现在战斗画面已清但战斗线程确认未完成时先行收集证据
    （面板可读就 OCR；任务还在则立即中止，不影响正确性），最终判定
    仍以战斗线程确认为门——回合间隙的画面空档期间计数会被「战斗画面
    再出现」分支清零，不会累积成误判。
    """
    deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
    # 延长总上限：战斗/截窗失败不断延长窗口的硬顶（约为基本窗口 4 倍）。
    # 超过上限仍未决 → 返回 False 让调用方继续跟踪（由外层战斗检查
    # 等战斗结束后重新确认），避免长时间遮挡下误判完成
    hard_deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT * 4
    gone_polls = 0
    panel_seen = False       # 确认窗口内是否见过追踪面板（标题条模板命中）
    panel_missing_logged = False
    while time.time() < deadline and time.time() < hard_deadline:
        img = first_img if first_img is not None else win.capture(force_activate=False)
        first_img = None
        if img is None:
            # 截窗失败不计入确认窗口（与战斗帧同待遇，防瞬时失败误判）
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(TASK_GONE_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            # 战斗画面又出现（回合间隙结束/战斗未真结束）：此前计数作废，
            # 延长确认窗口。单帧匹配在战斗动画间隙会漏判（实测 2.5 分钟
            # 战斗中被误判任务消失完成），因此最终判定还须战斗线程确认
            gone_polls = 0
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            time.sleep(2.0)
            continue
        gone_region = _task_gone_region(np.asarray(img))
        if gone_region is None:
            if require_panel:
                # 任务追踪面板不在场（renwuzhuizong.png 标题条模板未命中）
                # 且不是战斗结束路径：面板被手动关闭/折叠时任务列表整体
                # 不可见，OCR 读不到任务 ≠ 任务消失，不能作为完成证据
                # （2026-11-11 用户实机：寻路中手动关闭追踪面板，2s 内
                # 被误判任务完成、返程重新领取必失败）。本帧不计证据、
                # 也不延长窗口：窗口耗尽返回 False 交回正常轮询（战斗
                # 中延长窗口由上面的战斗帧分支负责）——正常轮询会处理
                # 被遮挡期间弹出的对话框（如驿站付费框）
                if not panel_missing_logged:
                    panel_missing_logged = True
                    log("[任务追踪] 追踪面板标题条未命中（被遮挡或已关闭），"
                        "本帧不计任务消失证据", "INFO")
                time.sleep(TASK_GONE_POLL_INTERVAL)
                continue
            # 战斗结束路径（require_panel=False）：用户确认战斗结束后
            # 任务追踪会消失、随即应返程——面板缺席按消失处理，回退
            # 静态条带按原逻辑采集证据（静态条带读不到任务即确认完成，
            # 主循环下轮 has_task 会复查兜住非收尾战斗的误判）
            gone_region = TASK_GONE_REGION
            if not panel_missing_logged:
                panel_missing_logged = True
                log("[任务追踪] 战斗结束后追踪面板不在场，按任务消失判定"
                    "（战斗结束路径）", "INFO")
        panel_seen = True
        items, img_np = game_map.ocr_regions(img, (gone_region,))
        if task_tracker.find_task(
                task_tracker.group_tasks(
                    items, task_tracker.panel_row_xmax(np.asarray(img))),
                title) is not None:
            return False  # 任务重新出现：此前为漏读，继续跟踪
        if fallback_texts:
            # 标题行被区域 OCR 漏检时 find_task 找不到任务：用内容链接行
            # 兜底串判定任务仍在（修罗「前往…消灭修罗」检测稳定，
            # 面板左区+只在任务存在时出现），立刻中止确认防假完成
            for txt, rect, score in items:
                if score >= 0.55 and rect[0] < task_tracker.PANEL_MAX_X \
                        and any(ft in txt for ft in fallback_texts):
                    return False
        gone_polls += 1
        if battle_watch_in_battle():
            # 画面已清但战斗线程的 2s 结束确认未完成：证据已收集
            # （任务还在则上面已 return False），不最终判定。
            # 证据已足（≥2 连续轮询未见）时不再 OCR 重复验证，短间隔
            # 轮询门状态、门开即返回——原逻辑门开后还要再睡 1.0s +
            # OCR 0.9s 才出结论，实测每轮返程交接白白多等 ~1.7s
            # （2026-10-24 战斗结束→开背包恒为 2~3s）。门等待 ≤2s，
            # 最后一次 OCR 时效性与原逻辑（门后一轮 ~1.9s）无实质差异
            deadline = time.time() + TASK_GONE_CONFIRM_TIMEOUT
            if gone_polls >= polls:
                while time.time() < deadline and battle_watch_in_battle():
                    time.sleep(TASK_GONE_GATE_POLL_INTERVAL)
                if not battle_watch_in_battle():
                    return True  # 门已开且证据已足：直接判定任务消失
                continue
            time.sleep(TASK_GONE_POLL_INTERVAL)
            continue
        if gone_polls >= polls:
            return True
        time.sleep(TASK_GONE_POLL_INTERVAL)
    if time.time() >= hard_deadline:
        return False  # 延长耗尽仍无法确认：继续跟踪，稍后重新确认
    if battle_watch_in_battle():
        # 战斗尚未结束：窗口耗尽也不得判定任务消失（2026-11-06 实测：
        # 回合间隙操作条+横幅同时漏匹配 → follow 假转换进入确认 →
        # 证据 1 轮后等门，门在战斗中永不开，6s 窗口耗尽走「视为完成」
        # 兜底在战斗中误判任务消失 → 返程前置战斗检查被迫等 35s、
        # has_task 在战斗中误判无任务——任务完成判定必须以战斗线程
        # 确认为门，战斗未结束一律返回 False 继续跟踪，由外层战斗
        # 检查等战斗结束后重新确认）
        log("[任务追踪] 任务消失确认：窗口耗尽但战斗未结束，不判定消失", "WARN")
        return False
    if not panel_seen:
        # 整窗未见追踪面板：无从判断任务是否存在，按未完成处理
        # （面板长时间遮挡/关闭时本函数会被周期性重入，INFO 免刷屏；
        # 用户关闭面板的持续状态由主循环的 WARN 提示一次）
        log("[任务追踪] 任务消失确认：追踪面板未在场，本次不判定任务消失", "INFO")
        return False
    return True  # 确认窗口内始终未见任务：视为完成


def _confirm_guard_dialog(win, img, extra_dialog_handler, tpl_gate=True,
                          extra_gate=None):
    """确认轮询跳出前的对话框复核（负面条件检查）。

    确认轮询只 OCR 追踪面板（快速路径），但跳出后要点击任务绿字——
    确认窗口期可能弹出对话（如五环到达 NPC 的开场白，实测 2026-10-13
    跳出点击「去找长安」绿字时对话正打开）。补 OCR 对话框区域交给
    extras 处理：命中（对话被处理）返回 True，调用方回退正常轮询；
    无 extras 或未命中返回 False 放行。
    本守卫只作辅助检查：extras 抛出的业务异常（MissingEquipment/
    DailyComplete 等）一律吞掉放行——正常轮询路径仍会按设计处理，
    守卫里提前触发会把「给与面板关闭动画残留」误判成任务终止。
    """
    if extra_dialog_handler is None:
        return False
    if tpl_gate and dialog_ocr_skip(img) \
            and not (extra_gate is not None and extra_gate(img)):
        return False  # 高性能+标准模板未命中且无补充门：无对话框，跳过 OCR 与 extras
    try:
        items_d, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        return bool(extra_dialog_handler(win, items_d, np.asarray(img)))
    except Exception:
        return False


def _task_gone_dialog_guard(win, extra_dialog_handler, tpl_gate=True,
                            extra_gate=None):
    """任务消失确认后的对话框复核（任务消失时奇遇弹窗可能仍在）。

    任务从追踪面板消失后，奇遇弹窗可能还开着（如客栈开箱奇遇的结局
    幕，任务完成与弹窗出现同帧附近，2026-10-31 实测弹窗未处理遗留
    到下一轮、遮挡掌柜模板致任务终止）——确认结论前重新截一帧 OCR
    对话框区域交给 extras：命中（弹窗仍在且被处理）返回 True，调用方
    推迟任务消失结论、回正常轮询下一轮再确认；无 extras/战斗帧/
    未命中返回 False 放行。extras 业务异常吞掉放行（与
    _confirm_guard_dialog 同约定）。
    """
    if extra_dialog_handler is None:
        return False
    try:
        img = win.capture(force_activate=False)
        if img is None or is_in_battle_frame(img):
            return False
        if tpl_gate and dialog_ocr_skip(img) \
                and not (extra_gate is not None and extra_gate(img)):
            return False  # 高性能+标准模板未命中且无补充门：无对话框，
                          # 跳过 OCR 与 extras（返程链路省截窗后的
                          # OCR ~0.4~2s，2026-11-06 开打 2x 放大重识别
                          # 白耗教训）
        items_d, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        return bool(extra_dialog_handler(win, items_d, np.asarray(img)))
    except Exception:
        return False


def _maybe_sheyaoxiang(win):
    """点击任务追踪后（寻路期）的摄妖香续用（到期才执行）。

    任务领取/巫医等对话弹框阶段背包交互被弹框挡住（储物格切换
    点击无效，用户 2026-11-04），寻路期无弹框、窗口空闲，是安全
    使用窗口——替代原任意时机的定时续用线程。每轮寻路都过此门，
    仅使用成功/异常时记日志（跳过态不刷屏）。
    """
    from . import bag  # 函数内导入防循环：bag→battle→…→xuanshu 环
    try:
        ran, info = bag.use_sheyaoxiang_if_due(win)
        if ran:
            log(f"[摄妖香] 寻路期续用: {info}", "SYS")
    except Exception as e:
        log(f"[摄妖香] 寻路期续用异常: {e}", "WARN")


def follow_task(win, title=TASK_TITLE, timeout=FOLLOW_TIMEOUT, prefix="玄枢轶闻",
                extra_dialog_handler=None, green_order=None, progress_key=None,
                green_picker=None, fallback_keywords=None, first_items=None,
                signature_key=None, ignore_empty_key=False, skip_texts=None,
                pre_click_handler=None, require_coord=False,
                post_track_handler=None, signature_ratio=0.8,
                task_gone_polls=None, dialog_tpl_gate=True,
                dialog_extra_gate=None,
                chuyijuebao_check=True, battle_extras_tpl_gate=False,
                story_dialog_dismiss=True, story_known_texts=None):
    """循环跟踪任务直到完成（prefix 供其他任务复用，如五环）。

    每轮：点击任务当前第一个绿色文案 → 等待任务信息变化（内容更新）
    → 进入下一轮；任务从追踪面板消失即完成。
    等待期间处理对话框（DIALOG_OPTION_MAP 指定绿字、skip_texts
    点屏幕中间跳过（None 用默认 DIALOG_SKIP_TEXTS，{} 禁用——
    五环等自有对话处理的调用方禁用，防误触；文案→点击次数字典，
    不同文案按屏数配置不同连点次数）、DIALOG_OPTIONS 已知绿字、
    战斗失败「你输了」点击任务绿字推进）与锄遗聚宝小游戏。
    战斗中跳过识别（任务追踪与坐标不可用），等待战斗结束。

    extra_dialog_handler：附加对话框处理回调 (win, items, img_np) → 是否已处理，
    供其他任务注入专属对话流程（如五环的提交装备）；回调中抛出的异常向上传播，
    供调用方做任务级终止。
    pre_click_handler：点击任务绿字前的检查回调 (win, items, img_np)，可抛异常
    终止跟踪（如五倍锁妖网任务在寻路开始前拦截，人物不起步直接替换领取）。
    post_track_handler：点击任务绿字成功后的回调 (win)——点完即进入寻路期，
    供组号模式检查队员血蓝（回调自行节流与消化异常，不阻塞跟踪推进）。
    signature_ratio：签名变化判定的相似阈值（默认 0.8）。同图链接变化
    （如五环「去找长安城东李冰冰的替身」→「去找长安城东食婴鬼手下的
    手下」，地图同为长安城东）归一签名相似度实测 0.846，默认阈值将其
    过滤为 OCR 噪声而不点击、任务卡住须人工点链接恢复（2026-11-01
    两轮实测各卡 2.5/7.4 分钟）；五环传 0.9 可检出（单字误读相似度
    ≥0.923 仍被过滤，两字以上误读由确认轮询收敛、误点击只是重复点
    同一链接无害）。
    green_order：多个可点击绿字须按序点击时的顺序关键词列表
    （如 青龙→白虎→朱雀→玄武），传给 track_task；None 时点第一个绿字。
    progress_key：进度判定回调 (items, img_np) → str。任务推进可能只改变
    绿字颜色而文字不变（如四象封印完成关键词变红），文字签名检测不到；
    该回调计算进度键，键变化即视为任务推进（点下一个绿字）。
    None 时仅按文字签名判定。基线在点击前捕获（点击后可能立刻完成，
    首个轮询才建基线会漏检）。
    green_picker：精确点击回调 (items, img_np) → (x, y) 或 None，
    透传给 track_task（同一行内多个绿字须按序点击时使用）。
    fallback_keywords：锚点内容采集失败时的兜底关键词列表（如五环子目标
    「替身/武痴/盗号贼/食婴鬼」），透传给 track_task 在追踪面板区域内
    直接按关键词匹配绿字行点击。
    first_items：调用方刚 OCR 的 (items, img_np)（如领取校验刚确认任务
    出现），首轮跳过截图+OCR，省约 1.2s。
    signature_key：任务文本签名回调 (items, img_np) → str，替代默认的
    标题+首行签名参与相似度比较（五环链接行被 OCR 拆成链接/计数两个
    碎片行且输出顺序抖动，默认签名反复翻转导致重复点击；五环改用
    行序无关的排序拼接签名）。回调返回空串表示「标题在但链接行内容
    不可读」：本轮不点击、不作变化信号，等待下一轮复读（五环过渡帧，
    2026-10-31）。
    ignore_empty_key：进度键为空时不作为变化信号（五环到达 NPC 时
    链接变暗、亮绿关键词集合为空再回弹，属过渡帧抖动；客栈的「」
    是合法终态，默认不启用）。
    require_coord：透传 track_task——点击目标行须含地图坐标，坐标行
    未检出时重试而非行级兜底点击（五环子目标行/剩余时间行不可点，
    2026-10-31）。
    task_gone_polls：任务消失确认的连续未见轮询数（传 _confirm_task_gone）。
    默认 None = TASK_GONE_CONFIRM_POLLS(2)。可传可调用对象，确认时实时
    求值——五倍非黄袍怪轮次传 1（该轮次战斗均为任务最终战斗，任务消失
    即完成；误判由主循环 has_task 复查兜住自愈），黄袍怪多杀轮次保持 2
    （两场战斗之间面板瞬时为空，1 轮会误判完成触发 4s 复查窗口）。
    dialog_tpl_gate：高性能模式下用对话框模板门控对话区 OCR（未命中
    模板即跳过，实测匹配 ~9ms vs OCR 470~930ms）。标准对话框
    （巫医/修罗/客栈/五倍领取与开打等）均命中；无标准边框的例外
    面板（客栈奇遇、给予面板、队伍面板、申请加入面板）依赖 OCR 的
    调用方须传 False 或提供 dialog_extra_gate（如客栈用奇遇横幅模板
    补门）——给予/队伍面板本身走模板检测不受影响。
    普通模式（高性能关）门控不生效，行为与旧版一致。
    dialog_extra_gate：补充对话框在场判定回调 (img) → bool。标准模板
    未命中时再调用，命中则仍做对话区 OCR（两者任一在场即放行，如客栈
    奇遇无标准边框但有自己的标题横幅模板）。
    chuyijuebao_check：等待期间是否检测锄遗聚宝小游戏（玄枢轶闻专属，
    每个轮询一次全帧模板匹配 ~30ms）。不出现该小游戏的任务（修罗等）
    传 False 省去轮询开销；默认 True 保持玄枢轶闻原行为。
    钓鱼小游戏（玄枢轶闻专属）无需开关：入口选项「先钓一下再说！」被
    点击后才 armed 检测窗口标题，其他任务阶段零开销。
    battle_extras_tpl_gate：战斗中 extras 通道（每 BATTLE_EXTRAS_INTERVAL
    一次对话框区域 OCR）加模板预检——战斗 UI 无标准对话框边框时跳过
    OCR（战斗帧模板分实测 0.52~0.63 < 0.79，2026-11-07），但保留
    空 items 心跳调用：依赖战斗期调用维持状态的处理器（五倍黄袍怪
    _hpg_was_in_battle）语义不变，空 items 也免去战斗 UI 文本误触发；
    默认 False 保持原行为。真对话框在场时模板命中、OCR 照跑，行为
    不变。仅当调用方确认其战斗中对话为标准边框时传 True（战斗中
    可能出现无边框面板对话的调用方勿开）。
    story_dialog_dismiss：内层轮询兜底——无绿字选项且不在任何已知
    对话表里的标准对话框（NPC 剧情台词）点屏幕中间推进/关闭。不处理
    时它常驻屏幕，对话门每轮命中、对话区每 0.5s 全量 OCR 空烧
    （2026-11-10 实测，见 dialog_common.StoryDialogDismisser）。
    自行实现了等价兜底（含任务专属抑制窗口，如五环给与面板预期）
    的调用方传 False。
    story_known_texts：剧情对话兜底的「已知对话表」（默认玄枢轶闻
    DIALOG_OPTION_MAP）。复用本引擎的任务（名望）传自己的选项表——
    否则本任务的带选项对话不在已知表内、选项未渲染时会被误判为
    无选项剧情对话点屏关闭（2026-11-10）。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    # 无选项剧情对话点屏推进兜底（2026-11-10，见 dialog_common.
    # StoryDialogDismisser）。已知对话表用 DIALOG_OPTION_MAP：其对话的
    # 绿字选项未渲染时只等待，不当剧情对话点屏（防误关/误推进）
    known_texts = DIALOG_OPTION_MAP if story_known_texts is None \
        else story_known_texts
    story_gate = StoryDialogDismisser(known_texts=known_texts) \
        if story_dialog_dismiss else None
    battle_since = None  # 战斗中等待起点：战斗时间不计入跟踪超时（2026-11-06
                         # 实测黄袍怪多杀单轮任务内含 4~5 场战斗约 4 分钟，吃满
                         # 300s 超时后 follow 死于战斗中途，主循环丢失任务完成
                         # 信号——战后不走返程反而误入领取流程走回宝象国；
                         # 战斗是任务推进的外部等待，不应消耗「任务卡死」预算）
    rounds = 0
    task_seen = False  # 本轮跟踪中任务是否曾被识别到：完成须满足
                       # 「无→有→无」——从未出现即确认消失是假完成
                       # （实测客栈 3s 一轮 0 次点击假完成，任务与对话框
                       # 都没执行；此时领取大概率失败，应报错回主循环重试）
    last_clicked_sig = None
    progressed = False
    last_items = None
    empty_sig_streak = 0  # 连续空签名次数（外循环退避节奏用）

    def _sig_of(items, img_np, task):
        """当前帧的任务文本签名（signature_key 优先，默认标题+首行）。"""
        if signature_key is not None:
            return signature_key(items, img_np)
        return task_tracker.task_signature(task)

    def _gone_polls():
        """任务消失确认轮数（可调用对象实时求值，见 task_gone_polls 说明）。"""
        if callable(task_gone_polls):
            return task_gone_polls()
        return TASK_GONE_CONFIRM_POLLS if task_gone_polls is None \
            else task_gone_polls
    while time.time() < deadline:
        # 组号 pass 进行中（战斗线程在切队员 Tab）：屏幕不是队长，
        # 跳过本轮识别防误点队员账号
        if tab_group.pass_active:
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 组号护栏：pass 切回失败残留时确保回到队长 Tab 再点寻路
        # （点任务追踪/对话框绝不能落在队员 Tab 上）
        if tab_group.active() and not tab_group.ensure_leader_tab(win):
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 战斗中：任务追踪不可用（可能误判任务消失），等待战斗结束。
        # 战斗等待时间不计入超时（deadline 顺延，见 battle_since 说明）。
        # 战斗线程确认状态优先（廉价读）：单帧模板判定在战斗动画间隙
        # 会漏匹配，漏掉的帧会走进任务消失确认空烧 6s（2026-11-11 实测）
        if battle_watch_in_battle() or is_in_battle(win):
            if battle_since is None:
                battle_since = time.time()
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        just_battle_ended = battle_since is not None
        if battle_since is not None:
            deadline += time.time() - battle_since
            battle_since = None
        # 复用最近一次 OCR 结果（内层轮询确认推进时或领取校验命中时），
        # 省一次截图+OCR；该帧同样透传给 track_task 与进度键基线，
        # 点击路径上一共省掉约 3.5s 的重复 OCR
        img = None  # 本轮新截帧（复用 OCR 结果时无帧：不得把旧帧传给
                    # 任务消失确认，防陈旧帧误导确认结论）
        if last_items is not None:
            items, img_np = last_items
            last_items = None
        elif first_items is not None:
            items, img_np = first_items
            first_items = None
        else:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(FOLLOW_POLL_INTERVAL)
                continue
            items, img_np = task_tracker.ocr_follow(img)
        task = task_tracker.find_task(
            task_tracker.group_tasks(items, task_tracker.panel_row_xmax(img_np)),
            title)
        if task is None:
            # 战斗刚结束的首个非战斗轮：与内层战斗结束路径同语义——面板
            # 缺席按任务消失处理（用户 2026-11-11 确认），不走严格面板
            # 门控空等 6s 后不判定（旧行为：战斗结束返程慢 10s）
            if _confirm_task_gone(win, title, fallback_keywords,
                first_img=img, polls=_gone_polls(),
                require_panel=not just_battle_ended):
                if _task_gone_dialog_guard(win, extra_dialog_handler,
                    tpl_gate=dialog_tpl_gate,
                    extra_gate=dialog_extra_gate):
                    continue  # 弹窗仍在：先处理，下一轮再确认任务消失
                if not task_seen:
                    # 诊断：转储追踪面板 OCR 行——宽松匹配（领取校验
                    # has_task）命中而分组匹配（find_task）漏任务时，
                    # 标题行形态即根因（五环实测死循环 2026-10-15）
                    try:
                        img2 = win.capture(force_activate=False)
                        if img2 is not None:
                            items2, _ = task_tracker.ocr_track(img2)
                            dump = " | ".join(f"「{t[0]}」" for t in items2
                                              if t[2] >= 0.55)
                            log(f"[{prefix}] 追踪面板 OCR: {dump or '(无)'}", "WARN")
                    except Exception:
                        pass
                    return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
            continue  # 瞬时漏读：任务仍在，继续跟踪
        task_seen = True
        sig = _sig_of(items, img_np, task)
        if not sig:
            # 标题在但链接行未检出（signature_key 返回空串，如五环链接行
            # 被区域 OCR 漏读/拆碎的过渡帧）：不点击也不作变化信号；对话
            # 处理照常（等待期间可能弹出任务对话，不处理会卡到超时）。
            # 连续空签名逐步退避至 1.5s：外循环每轮全区域 OCR（约 1.2s），
            # 不退避会以 2Hz 空烧 CPU
            empty_sig_streak += 1
            if extra_dialog_handler is not None:
                extra_dialog_handler(win, items, img_np)
            time.sleep(min(1.5, FOLLOW_POLL_INTERVAL * empty_sig_streak))
            continue
        empty_sig_streak = 0
        if last_clicked_sig is not None and _signatures_similar(sig, last_clicked_sig,
                                                                ratio=signature_ratio) \
                and not progressed:
            # 任务信息未真实变化（OCR 噪声抖动）→ 不重复点击，继续等待
            # progressed=True 时（进度键已变化）允许继续点击下一个绿字。
            # 阻塞期仍须处理任务对话（如五环提交装备）：对话框出现时
            # 任务签名不变，不处理会一直卡到跟踪超时
            if extra_dialog_handler is not None:
                extra_dialog_handler(win, items, img_np)
            time.sleep(FOLLOW_POLL_INTERVAL)
            continue
        # 点击前检查（可选回调，可抛异常终止跟踪）：在点任务绿字之前
        # 调用——需要替换的任务（如锁妖网）在寻路开始前拦截，人物
        # 不起步、原地进入替换领取（原流程先点链接开始寻路，内层轮询
        # 才检测到，白走一段再折返）
        if pre_click_handler is not None:
            pre_click_handler(win, items, img_np)
        # 进度键基线：直接用当前帧（点击前捕获；点击后目标可能立刻完成
        # 变色，首个轮询才建基线会漏检该变化导致卡住）
        pre_key = progress_key(items, img_np) if progress_key is not None else None
        ok, info = task_tracker.track_task(win, title, green_order=green_order,
                                           green_picker=green_picker,
                                           fallback_keywords=fallback_keywords,
                                           pre_items=(items, img_np),
                                           require_coord=require_coord)
        if not ok:
            return False, info
        last_clicked_sig = sig
        rounds += 1
        progressed = False
        debug_overlay.set_phase("寻路中")  # 看板阶段（点击任务绿字后自动寻路）
        log(f"[{prefix}] 第 {rounds} 轮跟踪完成，等待任务信息变化", "SYS")
        # 寻路期安全窗口：先续用摄妖香（到期才执行）再跑回调——组号
        # 模式回调 = 队员血蓝 pass，顺序「点击寻路 → 摄妖香 → 切 Tab
        # 血蓝」（用户 2026-11-04；弹框阶段背包交互无效）
        app_utils.mark_track_walk()  # 寻路信号：空闲期摄妖香兜底的时钟
        _maybe_sheyaoxiang(win)
        if post_track_handler is not None:
            try:
                post_track_handler(win)
            except Exception as e:
                log(f"[{prefix}] post_track_handler 异常: {e}", "WARN")

        # 等待任务信息变化或任务消失；期间处理对话框选项与锄遗聚宝小游戏
        changed_once = False
        changed_kind = None    # 首次变化来自哪个信号："text"/"key"——
                               # 确认轮询须验证同一信号（旧逻辑文本/进度键
                               # 各自单次抖动可组合骗过确认，导致重复点击）
        changed_sig = None     # 首次变化时的文本签名：确认轮询的新签名
                               # 须与其相似（过滤 A→B→A 的 OCR 抖动）
        changed_key = None     # 首次变化时的进度键：确认轮询的键须与其
                               # 相等（过滤单轮询键抖动）
        baseline_key = pre_key
        first_poll = pre_key is None
        last_poll_img = None
        last_poll_items = None
        track_gate = task_tracker.TrackGate()  # 面板差分门（共享组件，见 task_tracker）
        last_battle_extras_at = 0.0 # 战斗中 extras 上次对话框 OCR 时间（限频）
        was_battle = False      # 本轮询前处于战斗（战斗结束转换帧直接做任务消失确认）；
                                # 战斗帧与战斗线程确认状态都会置位，动画间隙不消费

        while time.time() < deadline:
            time.sleep(CONFIRM_POLL_INTERVAL if changed_once else FOLLOW_POLL_INTERVAL)
            # 组号 pass 进行中：屏幕是队员 Tab，跳过本轮识别（同外层护栏）
            if tab_group.pass_active:
                continue
            img = win.capture(force_activate=False)
            if img is None:
                continue
            # 战斗帧判定前置（性能 2026-10-27）：战斗 UI 遮挡追踪面板，
            # 面板差分门与帧复用均失效（战斗动画帧帧不同），先判战斗、
            # 命中即跳过面板/对话框全量 OCR——原先 OCR 在战斗判定之前，
            # 战斗期每轮空烧双区域 OCR（实测 1.4-2.2 CPU 秒/次）。
            # 战斗线程确认状态一并采信（2026-11-11）：操作条/回合横幅
            # 单帧漏匹配的动画间隙帧必须仍按战斗处理——旧实现把间隙帧
            # 当战后首帧，消费 was_battle 并进任务消失确认空烧 6s，真
            # 战斗结束反而退回严格路径再烧 6s（13:49/13:52 实测返程慢
            # 10~30s）
            if is_in_battle_frame(img) or battle_watch_in_battle():
                was_battle = True
                # 战斗等待时间不计入超时（与外层 is_in_battle 门同一预算；
                # 首个非战斗帧处一次性顺延 deadline）
                if battle_since is None:
                    battle_since = time.time()
                # 确认轮询期战斗帧：保持原 2s 退避（确认轮询间隔仅 0.2s，
                # 不退避会以 5Hz 截窗+匹配空烧）
                if changed_once:
                    time.sleep(2.0)
                    continue
                # 战斗中也可能出现任务对话（如五环提交装备）：保留对话框
                # 区域 OCR 供 extras 处理；限频替代差分门（动画帧门控失效）。
                # battle_extras_tpl_gate=True 的调用方（修罗/五倍）：战斗 UI
                # 无标准对话框边框，模板未命中即跳过对话框区 OCR（实测
                # ~1.2s/2s 白烧）；但保留空 items 心跳调用——五倍黄袍怪
                # 状态机靠战斗期调用维护 _hpg_was_in_battle（多杀重链的
                # 战斗结束沿），空 items 也天然免去战斗 UI 文本误触发
                # （锁妖网关键词/开打 2x 兜底）。真对话框在场时模板命中、
                # OCR 照跑，行为不变
                now = time.time()
                if extra_dialog_handler is not None \
                        and now - last_battle_extras_at >= BATTLE_EXTRAS_INTERVAL:
                    last_battle_extras_at = now
                    if battle_extras_tpl_gate and dialog_ocr_skip(img) \
                            and not (dialog_extra_gate is not None
                                     and dialog_extra_gate(img)):
                        extra_dialog_handler(win, [],
                                             np.zeros_like(np.asarray(img)))
                        continue
                    items_d, img_np_d = game_map.ocr_region(
                        img, task_tracker.DIALOG_REGION)
                    extra_dialog_handler(win, items_d, img_np_d)
                continue
            if battle_since is not None:
                # 首个非战斗帧：战斗等待时长一次性顺延 deadline（战斗不消耗
                # 「任务卡死」超时预算，见 battle_since 说明）
                deadline += time.time() - battle_since
                battle_since = None
            if changed_once:
                # 确认轮询：只复查追踪面板区域的任务签名/进度键（区域更小
                # OCR 更快），跳过对话框处理，最快确认变化。战斗帧已在
                # 循环头部拦截。验证首次变化时的同一信号且新值稳定：文本
                # 变化须新签名与首次变化时相似；进度键变化须与首次变化时
                # 相等。单一信号的单次 OCR 抖动被过滤，两个信号各抖一次
                # 也无法组合骗过
                items = track_gate.ocr(img)
                img_np = np.zeros_like(np.asarray(img))
                track_gate.paste_track(img, img_np)
                task2 = task_tracker.find_task(
                    task_tracker.group_tasks(
                        items, task_tracker.panel_row_xmax(img_np)),
                    title)
                if task2 is None:
                    if _confirm_task_gone(win, title, fallback_keywords,
                        first_img=img, polls=_gone_polls()):
                        if _task_gone_dialog_guard(win, extra_dialog_handler,
                            tpl_gate=dialog_tpl_gate,
                            extra_gate=dialog_extra_gate):
                            changed_once = False
                            continue
                        if not task_seen:
                            return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                        return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
                    changed_once = False  # 面板瞬时不可读：回退正常轮询
                    continue
                sig2 = _sig_of(items, img_np, task2)
                if not sig2:
                    changed_once = False  # 链接行未检出：不作变化信号，回正常轮询
                    continue
                text_changed = not _signatures_similar(sig2, sig, ratio=signature_ratio)
                key = progress_key(items, img_np) if progress_key is not None else None
                # baseline_key 为 None（阶段外基线）时不得参与比较：None 是
                # 「不归我管」信号而非可比较值，与阶段内值（"0"/"1"）比较
                # 会把阶段刚出现误判为任务推进（实测「None」→「0」误确认，
                # 2026-10-26）
                key_changed = key is not None and baseline_key is not None \
                    and not first_poll and key != baseline_key
                if ignore_empty_key and (not key or not baseline_key):
                    key_changed = False  # 空键为过渡帧，不作信号
                if changed_kind == "text":
                    if text_changed and _signatures_similar(sig2, changed_sig,
                                                           ratio=signature_ratio):
                        # 跳出前复核对话框：确认轮询只 OCR 追踪面板（快速
                        # 路径），而确认窗口期可能弹出对话（如五环到达 NPC
                        # 的开场白，实测 2026-10-13）——补 OCR 对话框区域
                        # 交给 extras 处理，命中则回退正常轮询，避免对着
                        # 打开中的对话框点任务绿字
                        if _confirm_guard_dialog(win, img, extra_dialog_handler,
                            tpl_gate=dialog_tpl_gate,
                            extra_gate=dialog_extra_gate):
                            changed_once = False
                            continue
                        log(f"[{prefix}] 变化确认: 任务签名「{sig}」→「{sig2}」", "INFO")
                        progressed = True
                        last_items = (items, img_np)
                        break
                    changed_once = False
                    continue
                if key_changed and key == changed_key:
                    if _confirm_guard_dialog(win, img, extra_dialog_handler,
                        tpl_gate=dialog_tpl_gate,
                        extra_gate=dialog_extra_gate):
                        changed_once = False
                        continue
                    log(f"[{prefix}] 变化确认: 进度键「{baseline_key}」→「{key}」", "INFO")
                    progressed = True
                    last_items = (items, img_np)
                    break
                changed_once = False  # 变化未持续（OCR 抖动），回退正常轮询
                continue
            # 战斗结束转换帧：跳过全区域 OCR 直接做任务消失确认——战斗
            # 帧已由循环头部拦截（战斗线程确认状态一并采信，动画间隙
            # 不会误走到这里），走到此处即「战斗线程确认结束后的首个
            # 非战斗帧」；证据在战斗线程结束确认完成后立即收集，返程
            # 交接省时（实测 2026-10-24 战斗结束→开背包恒 2s，根因为
            # 全区域 OCR 拖晚确认循环进入，门开时证据才 1 轮、还需补
            # 1 轮 ~1.9s）
            if was_battle:
                was_battle = False
                last_poll_items = None
                last_poll_img = None
                # 战斗结束路径：面板消失即视为任务消失（用户 2026-11-11
                # 确认：战斗结束后任务追踪会消失，随即应打开背包返程），
                # 不要求标题条模板在场
                if _confirm_task_gone(win, title, fallback_keywords,
                    first_img=img, polls=_gone_polls(), require_panel=False):
                    if _task_gone_dialog_guard(win, extra_dialog_handler,
                        tpl_gate=dialog_tpl_gate,
                        extra_gate=dialog_extra_gate):
                        changed_once = False
                        continue
                    if not task_seen:
                        return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                    return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
                changed_once = False  # 任务仍在（遭遇战结束）或未决：回正常轮询
                continue
            # 帧全等复用：与上一轮询帧完全相同时复用其 OCR 结果（RapidOCR
            # 同输入输出确定，逐字等价；静止帧省一次双区域 OCR ~1.2s）
            if last_poll_items is not None and _frames_equal(img, last_poll_img):
                items, img_np = last_poll_items
            else:
                # 面板差分门（见 task_tracker.TrackGate）：移动场景整帧
                # 比对永不命中，面板核心静止即复用面板 OCR 结论；对话框
                # 区域逐轮 OCR（对话随时可能出现，移动世界像素持续变化、
                # 差分门不适用）
                track_items = track_gate.ocr(img)
                if dialog_tpl_gate and dialog_ocr_skip(img) \
                        and not (dialog_extra_gate is not None
                                 and dialog_extra_gate(img)):
                    # 高性能+模板未命中：跳过对话区 OCR（无对话框空烧
                    # 实测 470~930ms/轮询；模板匹配仅 ~9ms）。对话框
                    # 出现时模板必命中，开打/选项处理不受影响
                    dialog_items = []
                    img_np = np.zeros_like(np.asarray(img))
                else:
                    dialog_items, img_np = game_map.ocr_region(
                        img, task_tracker.DIALOG_REGION)
                track_gate.paste_track(img, img_np)
                items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
                last_poll_items = (items, img_np)
                last_poll_img = img
            # 附加对话框处理优先（战斗中的任务对话已由循环头部战斗帧
            # 分支限频处理，此处处理非战斗帧的对话）
            extras_handled = False
            if extra_dialog_handler is not None:
                extras_handled = extra_dialog_handler(win, items, img_np)
            if extras_handled:
                # extras 已处理（关闭对话框/点选项/点链接）：本帧是处理
                # 前的陈旧帧，签名变化与对话框判定均不可信，推迟到下一
                # 轮询（实测 2026-10-13 五环：开场白对话与面板变化同帧，
                # 若继续用陈旧帧跳出点击任务绿字会误点打开中的对话框）
                changed_once = False
                continue
            # 钓鱼小游戏（玄枢轶闻专属）：入口选项「先钓一下再说！」点击
            # 后（armed 门控）检测窗口标题 xuanshu_diaoyu.png；窗口出现
            # 即完整执行闭环（与客栈破除封印同一实现；钓鱼为长按模式，
            # 开始后按钮为「长按」，按住才推进）
            if _diaoyu_window_check(win, img, prefix):
                continue
            # 封印机关破解：提示对话框出现则完整处理（点击关闭 → 降落 →
            # 按序点机关，任务追踪变化即停）
            if _dialog_text_present(items, JIGUAN_DIALOG_TEXT):
                ok2, info2 = _play_jiguan_puzzle(win, title)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            # 锄遗聚宝优先：出现则完整处理（复用本帧模板匹配，不重复截窗）
            if chuyijuebao_check \
                    and game_map.find_template_in(img, CHUYIJUEBAO_ICON) is not None:
                ok2, info2 = _play_chuyijuebao(win)
                log(f"[{prefix}] {info2}", "SYS" if ok2 else "WARN")
                continue
            task2 = task_tracker.find_task(
                task_tracker.group_tasks(items,
                                         task_tracker.panel_row_xmax(img_np)),
                title)
            if task2 is None:
                if _confirm_task_gone(win, title, fallback_keywords,
                    first_img=img, polls=_gone_polls()):
                    if _task_gone_dialog_guard(win, extra_dialog_handler,
                        tpl_gate=dialog_tpl_gate,
                        extra_gate=dialog_extra_gate):
                        changed_once = False
                        continue
                    if not task_seen:
                        return False, f"任务从未出现在追踪面板（跟踪 {rounds} 次点击，领取可能失败）"
                    return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
                changed_once = False  # 瞬时漏读：任务仍在，继续等待
                continue
            task_seen = True
            sig2 = _sig_of(items, img_np, task2)
            if not sig2:
                changed_once = False  # 链接行未检出：不作变化信号（过渡帧）
                continue
            text_changed = not _signatures_similar(sig2, sig, ratio=signature_ratio)
            key = None
            key_changed = False
            if progress_key is not None:
                # 进度键：点击后首轮建立基线，之后与基线比较（文字不变
                # 仅颜色变化的任务推进，如封印完成绿字变红）
                key = progress_key(items, img_np)
                if first_poll:
                    baseline_key = key
                    first_poll = False
                else:
                    # 同确认轮询：None（阶段外/识别未决）不作为可比较键值，
                    # 防「None→0」与「0→None」误判推进（2026-10-26 实测）
                    key_changed = key is not None and baseline_key is not None \
                        and key != baseline_key
                    if ignore_empty_key and (not key or not baseline_key):
                        key_changed = False  # 空键为过渡帧，不作信号
            if text_changed or key_changed:
                # 连续两次轮询确认同一信号确实变化（单次可能是 OCR 抖动，
                # 避免任务无变化时重复点击任务绿字）
                if changed_once:
                    progressed = True
                    last_items = (items, img_np)
                    break
                changed_once = True
                # 进度键为精确匹配，双信号同时变化时优先信任
                changed_kind = "key" if key_changed else "text"
                changed_sig = sig2
                changed_key = key
                continue
            changed_once = False
            # 对话框处理：特定对话框指定绿字 → 点屏幕中间跳过 → 已知绿字选项
            # 文案匹配为模糊匹配（逐行与全文拼接，抗 OCR 个别字误读与换行拆分）。
            # extras 已处理过本帧（如点过开打选项/追踪链接）时跳过——
            # 防止回调点击后的陈旧帧继续命中内置表造成同帧双点（审计契约问题 1）
            if extras_handled:
                continue
            handled = False
            for dtxt, opt in DIALOG_OPTION_MAP.items():
                if _dialog_text_present(items, dtxt):
                    if _click_green_option_once(win, items, img_np, opt):
                        handled = True
                        _arm_diaoyu_check(opt)  # 钓鱼入口点击 → 开启窗口检测
                        break
                    # 选项未被 OCR 识别（如单字选项）：按序号点击兜底
                    idx = DIALOG_OPTION_INDEX_MAP.get(dtxt)
                    if idx and _click_dialog_option_by_index(win, items, img_np,
                                                             dtxt, idx):
                        handled = True
                        _arm_diaoyu_check(opt)  # 同上（序号兜底路径）
                        break
            if not handled and any(_dialog_text_present(items, t)
                                   for t in DIALOG_LOSE_TEXTS):
                # 战斗失败/平局对话框：继续点击任务追踪的绿色文案推进任务
                # （复用本轮 OCR 结果，省一次截图+OCR）
                ok3, info3 = task_tracker.track_task(win, title, green_order=green_order,
                                                     green_picker=green_picker,
                                                     fallback_keywords=fallback_keywords,
                                                     pre_items=(items, img_np),
                                                     require_coord=require_coord)
                if ok3:
                    log(f"[{prefix}] 检测到「你输了/平局」对话，继续点击任务绿字推进", "SYS")
                    handled = True
                else:
                    log(f"[{prefix}] 「你输了/平局」对话中任务绿字点击失败: {info3}", "WARN")
            if not handled:
                for dtxt, clicks in (DIALOG_SKIP_TEXTS.items()
                                     if skip_texts is None
                                     else skip_texts.items()):
                    if _dialog_text_present(items, dtxt):
                        # 命中即按文案对应次数连点推进（多屏对话无需枚举
                        # 后续文案，漏推进由下一轮询 OCR 自愈）；命中文案
                        # 打日志便于排查
                        log(f"[{prefix}] 跳过对话命中文案「{dtxt}」，连点 {clicks} 次", "SYS")
                        _dc_click_screen_center(win, times=clicks, prefix=prefix)
                        handled = True
                        break
            if not handled:
                for opt in DIALOG_OPTIONS:
                    if _click_green_option_once(win, items, img_np, opt, prefix=prefix):
                        debug_overlay.set_phase("任务对话")  # 看板阶段
                        handled = True
                        break
            # 无选项剧情对话兜底：点屏幕中间推进/关闭（不处理会常驻屏幕，
            # 对话门每轮命中、对话区 OCR 每 0.5s 空烧，2026-11-10 实测）
            # img 为本轮已截帧（复用 OCR 结果时为 None，寻路判定内部补截）：
            # 寻路中点屏会取消自动寻路，人物停下不走（2026-11-11 用户实机）
            if not handled and story_gate is not None:
                story_gate.try_dismiss(win, items, img_np, prefix=prefix, img=img)
        else:
            return False, f"第 {rounds} 轮跟踪后任务信息无变化（超时）"
    return False, f"跟踪任务超时（{timeout}s）"


def run_cycle(win):
    """执行一轮玄枢轶闻检查：有任务则跟踪到完成，无任务则领取。

    领取成功后立即开始跟踪（不等轮次间隔，缩短领取后首次点击延迟）。

    Args:
        win: 游戏主窗口 Window 对象

    返回 (状态, 描述)；状态：done 完成一次任务 / daily_limit 每日次数已满 / fail 本轮失败
    """
    if is_in_battle(win):
        return "fail", "战斗中，跳过本轮（等待战斗结束）"

    if task_tracker.has_task(win, TASK_TITLE):
        ok, info = follow_task(win, extra_dialog_handler=_handle_jiantianpan,
                               progress_key=_jiguan_link_progress,
                               green_picker=_jiguan_link_picker)
        return ("done", info) if ok else ("fail", info)

    # 没有任务 → 寻路到任务位置并领取任务
    debug_overlay.set_phase("前往领取点")  # 看板阶段
    ok, info, pre_items = navigate_to_task(win)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔；复用领取校验的 OCR 结果
    ok, info = follow_task(win, first_items=pre_items,
                           extra_dialog_handler=_handle_jiantianpan,
                           progress_key=_jiguan_link_progress,
                           green_picker=_jiguan_link_picker)
    return ("done", info) if ok else ("fail", info)


def run_loop(win, cycle_interval=3.0):
    """玄枢轶闻主循环：跟踪完成的任务 → 回去继续领取 → 往复执行。

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取任务时出现「少侠已经完成5次任务，请明天再来吧」（每日次数已满）

    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(TASK_TITLE, 0)
    log(f"[玄枢轶闻] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    done = app_utils.task_progress_done(win, TASK_TITLE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[玄枢轶闻] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[玄枢轶闻] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次玄枢轶闻任务，达到次数上限 {max_rounds}"
    round_no = 0
    while True:
        round_no += 1
        log(f"[玄枢轶闻] ==== 第 {round_no} 轮 ====", "SYS")
        status, info = run_cycle(win)
        if status == "done":
            done += 1
            app_utils.task_progress_add(win, TASK_TITLE, done)
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            debug_overlay.record_round()  # 统计页：完成轮次计数
            log(f"[玄枢轶闻] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[玄枢轶闻] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[玄枢轶闻] {info}", "WARN")
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
