"""修罗任务自动化。

流程（与五环基本一致）：
0. 任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪
1. 领任务（已实现）：灵兽村领取点
   1.0 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
   1.1 队长先走到领取点附近（队伍是否满都先走位）：
       已在领取点 110,90 附近（±15）→ 跳过寻路；
       不在灵兽村 → 世界地图点击灵兽村图标 → 小地图点击 (603,392) 寻路；
       在灵兽村但不在领取点附近 → 直接打开小地图点击 (603,392) 寻路
       寻路等待到达 110,90（±5，更严格：走动中识图点击容易点不到）
   1.2 到达后进入队伍监控循环（通用设置-修罗设置，仅队长生效）：
       查人数（最少人数按设置）→ 缺人从申请加入面板允许申请人
       （每允许一人立即重查；无申请人 10 秒后重查）→ 人满才领取；
       队长被转移则转 1.0 非队长模式
   1.3 识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
       → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
       （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框；检查队伍开启
       时重入 1.2 监控补人循环（人满下轮重试领取），关闭时本轮领取失败
   1.4 领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）
2. 寻路：点击任务追踪绿字自动寻路（复用 xuanshu.follow_task）
3. 对话开打：寻路到达后出现对话面板 → 点击绿色选项「看打！」进入战斗
   （点击成功标记本轮战斗由「看打！」进入）
4. 战斗：战斗由 battle.watch_loop 线程自动操作；follow_task 战斗中自动跳过识别
5. 返程：战斗结束后用修罗回城符回灵兽村（仅「看打！」进入的战斗才返程，
   其他方式进入的战斗不自动返程）：打开背包 → 打开任务储物格
   （box_task/box_task_selected 双模板比分数判定）→ 右键修罗回城符
   → 关闭世界地图/小地图/背包
6. 循环往复；达到次数上限时结束任务进程

追踪面板标题实测（2026-10-08）：真实标题为「修罗任务[常规玩法]」，但
「修罗」前缀是彩色徽标渲染，OCR 时有时无（实测读成「任务【常规玩法］」），
匹配串用两种渲染的公共子串「常规玩法」；面板中「狂风修罗」等无方括号
干扰行不含该子串，不会抢锚点。
"""
import re
import random
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image  # 对话框区域 2x 放大重识别（看打选项 OCR 漏检兜底）
from ascript.windows.system import R  # 资源路由（模板路径）
from ascript.windows.hardware.keyboard import Keyboard  # Alt+C 飞行/刷新（领取验证兜底）

from .app_utils import human_click, human_move, precise_click, log, move_away, norm_text
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from . import task_tracker  # 任务追踪面板操作
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import team  # 组队检查（领取前队伍人数检查，仅队长生效）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from . import bag  # 背包操作（打开/关闭背包、任务储物格）
from . import status_bars  # 血蓝检测（自动巫医前置条件）
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from .battle import WUYI_DIALOG_TEXTS  # 巫医对话框文案（自动巫医等待判定）
from .battle import _find_wuyi_option  # 「全部医治和修复」选项定位（验证模式自行点击）
from . import battle  # 巫医预期时间戳（点 NPC 后通知战斗线程切入 2s 高频检查）
from .control_window import (load_task_counts, load_xiuluo_settings,
                             load_xiuluo_shout_switch,
                             load_xiuluo_check_team,
                             load_xiuluo_auto_wuyi,
                             load_xiuluo_wuyi_interval)  # 任务次数上限/修罗设置/自动巫医
from .xuanshu import follow_task  # 任务跟踪（绿字点击寻路/战斗跳过/附加对话处理）
from .dialog_common import (_dialog_text_present, _norm_dialog_present,
                            _click_green_option_once,
                            SCREEN_CENTER_POS,
                            green_option_center)  # 对话框共享组件

# 任务模式与追踪面板标题（2026-10-08 日志+截图实测确认）
TASK_MODE = "修罗"           # 控制面板任务模式名（任务次数配置键）
TASK_TITLE = "常规玩法"       # 追踪面板标题匹配串：标题「修罗任务[常规玩法]」的
                              # 「修罗」前缀是彩色徽标，OCR 时有时无（实测读成
                              # 「任务【常规玩法］」→ has_task("修罗任务") 匹配
                              # 失败，领取验证误报 19s/9s 失败）——用两种渲染的
                              # 公共子串。「狂风修罗」等无方括号干扰行不含它，
                              # 锚点选取安全（不再抢锚点）
TASK_FALLBACK_TEXTS = ("消灭修罗",)  # has_task 兜底匹配串：标题整行被区域 OCR
                              # 漏检时（实测标题行完全缺失），用内容链接行
                              # 「前往…消灭修罗」判定任务存在——链接行检测稳定
                              # （0.82~0.88）且仅在任务存在时出现

# ---- 领取任务（灵兽村，用户实测） ----
LINGSHOUCUN = "灵兽村"                        # 位置识别地名（精确匹配）
WORLD_MAP_LINGSHOUCUN_ICON = "shijie_lingshoucun.png"  # 世界地图灵兽村图标（14.png 实测素材）
LINGQU_ICON = "xiuluo_lingqu.png"             # 修罗领取任务按钮（15.png 实测素材）
LINGSHOUCUN_SHIZHE_ICON = "xiuluo_lingshoucunshizhe.png"  # 灵兽村使者 NPC（识别到也可点击领取）
CLAIM_SPOT = (111, 90)                        # 领取点地图坐标（用户实测 111,90 附近）
SHIZHE_SPOT_BOX = (94, 78, 131, 112)         # 灵兽村使者领取点坐标框（用户 2026-10-21）：
                                             # 使者站位 (101,82)，框 x∈[94,131]、y∈[78,112]。
                                             # 按钮模板可见必可领取；仅识别到使者时须在
                                             # 框内才能领取（使者远距离也可见）。严格上下界
                                             # 判定——超出 +30px 可能落在其他可领取坐标
                                             # 范围（仍能领取），宽容差会把其他点误判为本点
CLAIM_NEAR_TOLERANCE = 15                     # 已在领取点附近判定容差（用户步 1.1）
CLAIM_NEAR_BTN_GRACE = 3.0                    # 位置近但按钮不可见时的渲染短窗口（秒）：
                                              # 窗口内按钮出现即免寻路，仍无走小地图重走
                                              # （实测返程落点 101,83 距领取点 12.2 单位，
                                              # 按钮不出现，旧逻辑入口空等 20s 需人工走位）
WALK_ARRIVE_TOLERANCE = 5                     # 寻路到达判定容差（用户步 1.2，更严格：
                                              # 走动中识图点击容易点不到）
LINGSHOU_MAP_POS = (627, 396)                 # 灵兽村小地图上领取点位置的点击点（窗口相对坐标，用户实测）
CLAIM_DIALOG_OPTION = "闲来无事，要我帮忙吗"    # 领取对话框绿色选项（游戏内原文，实测用字）
CLAIM_OPTION_TAIL = "要我帮忙吗"               # 选项换行渲染时的尾部匹配串（实测完整文案
                                              # 永不单行命中，两次全靠尾部/兜底）
CLAIM_COUNT_TEXT = "今日已完成次数"            # 领取后对话框中的完成次数文案（辅助记录完成次数）
TEAM_WARNING_TEXT = "修罗的战斗力异常强大，千万要找齐伙伴再一同消灭。"  # 队伍数量小于 3 的提示（实测文案）
TEAM_CONFIRM_TEXT = "修罗的战斗力异常强大，你当前队伍的人数不足5人，确定要领取任务吗？"  # 领取确认弹窗（实测文案）
TEAM_CONFIRM_ACCEPT = "确定"           # 确认弹窗选项：接受领取
TEAM_CONFIRM_DECLINE = "我再想想"       # 确认弹窗选项：拒绝（等待补人）
CLAIM_ARRIVE_TIMEOUT = 90                     # 寻路到领取点的超时
CLAIM_BUTTON_TIMEOUT = 20                     # 到达后等待领取任务按钮出现的超时
ENTRY_FIRST_WAIT = 4.5                         # 入口首轮等待：位置近判定可能误判（超出
                                               # 可点击范围入口不会出现），首轮只等 4.5s
                                               # （3 次 1.5s 轮询覆盖模板相位），未出现即
                                               # 小地图重走领取点再等一轮（五倍同款，
                                               # 实测 2026-10-12 修罗第 70 轮空等 20s
                                               # 需人工走位才恢复）
CLAIM_DIALOG_TIMEOUT = 15                     # 领取对话框处理超时
CLAIM_DIALOG_APPEAR_TIMEOUT = 4               # 点击领取入口后等待对话框出现的短窗口
                                              # （正常 ~1.5s 即出）：超时未见选项判定
                                              # 坐标不对，走小地图重定位兜底
CLAIM_DIALOG_FIXED_WAIT = (0.8, 1.2)          # 高性能缓存点击路径：入口点击后的固定渲染
                                              # 等待区间（秒，均匀随机取——用户 2026-10-26
                                              # 指定 0.8~1.2 随机，验证后反馈正确率）。
                                              # 固定等待较轮询信号更合理——轮询信号偶现
                                              # 异常致缓存点击点偏
CLAIM_VERIFY_POLL_INTERVAL = 0.8              # 校验轮询间隔（秒）。用户 2026-10-26 指定
                                              # 0.8：OCR 占空比降到 ~50%，CPU 占用极低；
                                              # 检测粒度由任务块 2x 放大重识别补偿（漏检
                                              # 时补一次高精度识别，不靠高频轮询）
TASK_BLOCK_REGION = (0, 225, 400, 310)        # 任务块裁剪区域（面板顶部：标题行 y~230-255
                                              # + 内容链接行 y~260-295，留余量）；2x 放大
                                              # 重识别用（修罗标题徽标漏检 + 插入滑动动画
                                              # 期间 det 失败——放大帧检出能力更强）
CLAIM_FOLLOW_RECOVER_WINDOW = 30              # 领取后首次跟踪失败的补偿窗口（秒）：
                                              # 首次点击及其重试实测 9s、慢 OCR 下 ~20s；
                                              # 窗口外的同类失败视为跟踪中期，不补偿

# ---- 自动巫医（用户流程：领取后每 N 轮一次，无条件医治——血蓝正常
#      也照常巫医，2026-10-09 用户确认；对话框处理复用战斗线程逻辑） ----
WUYI_NPC_ICON = "xiuluo_lingshoucunwuyi.png"  # 巫医 NPC（点击打开医治对话框）
WUYI_SPOT = (115, 72)                         # 巫医 NPC 位置（用户实测）
WUYI_MAP_POS = (610, 442)                     # 小地图上巫医位置的点击点（窗口相对坐标，用户实测）
WUYI_NEAR_TOLERANCE = 11                       # 巫医附近的判定容差（严格：小距离移动，
                                              # 宽松容差会漏判偏移——用户确认）
WUYI_ARRIVE_TIMEOUT = 8                       # 走到巫医坐标附近的超时（领取点距巫医很近，
                                              # 实测走位 2~3s；位置 ±5/NPC 模板可见/位置由
                                              # 可读变缺失均算到达——单次尝试，用户 2026-10-09
                                              # 确认：走到附近超时 24s 不合理，压缩上限）
WUYI_NPC_TIMEOUT = 3                          # 到达后识别巫医 NPC 模板的窗口：3s 内未识别到
                                              # 即本次取消，下次轮到再试（用户 2026-10-09 确认）
WUYI_HANDLE_TIMEOUT = 8                       # 点击 NPC 后等待战斗线程处理对话框的超时
                                              # （用户 2026-10-28 指定：15s 太长，最长 8s；
                                              # 超时后自行点击选项兜底，见 _run_wuyi_chain）
CLAIM_VERIFY_WAIT = 5                         # 领取后任务进入追踪的正常轮询时长（秒）：
                                              # 超时未检索到 → 按一次 Alt+C 飞行后仅再检索一次，
                                              # 仍无即判定失败（用户确认：多次重试无效）

# ---- 领取前队伍检查 ----
TEAM_CHECK_RETRY_INTERVAL = 10  # 缺人且无申请人可补时的重查间隔（秒）
TEAM_RECOG_RETRY_INTERVAL = 3   # 成员识别失败（OCR 噪声）时的重试间隔（秒）：
                                # 识别失败≠人数不足，等满 10s 纯属空转
SHOUT_MIN_INTERVAL = 30         # 缺人自动喊话最短间隔（秒，防刷屏）
_shout_text = ""                # 本轮喊话内容（按必须种族拼装；空=不喊话）
_shout_last_at = 0.0            # 上次喊话时间（跨监控调用保持间隔）

# ---- 对话开打（寻路到达后出现对话面板） ----
FIGHT_DIALOG_OPTION = "看打！"  # 对话面板绿色选项（点击进入战斗，游戏内原文）
_fight_via_kanda = False        # 本轮战斗是否由「看打！」进入（决定战后是否返程）
_team_checked = False           # 队伍检查已通过（首次领取成功后置位）：
                                # 后续轮次跳过检查——用户确认每轮回程后开面板
                                # 检查耗时 5~7s，只需第一次领取前检查一次
_task_gone_last_round = False   # 上一轮任务消失已确认（follow_task 完成判据）：
                                # 下一轮开始必然无任务，跳过 has_task 检查
                                # （区域 OCR ~0.9s）；仅进程首轮/上轮跟踪失败
                                # 未确认时才检查（续跑场景）
_returned_last_round = False    # 上一轮返程成功（回城符已使用、落点必在
                                # 灵兽村）：下一轮领取时位置识别失败改为
                                # 轮询等待加载完成，不误走世界地图寻路
                                # （实测 12:30 传送加载超 8s 白走地图 ~11s）；
                                # 进入领取流程时消耗复位，仅对紧邻轮次生效

# ---- 高性能模式（通用设置开关，共享设施见 app_utils：领取选项/看打
#      选项固定位置缓存点击 + 效果校验 + 识别回退；背包按钮缓存已下沉
#      到 bag.open_bag，各任务模块共用） ----
_fight_cached_used = False       # 当前这次看打对话框出现已试过缓存点击

# ---- 战斗（战斗操作由 battle.watch_loop 线程负责） ----
BATTLE_END_POLL = 2.0            # 等待战斗结束的轮询间隔

# ---- 返程（用户流程：背包任务储物格 → 修罗回城符右键 → 回灵兽村） ----
BOX_TASK_ICON = "box_task.png"                    # 背包任务储物格按钮（未选中态，点击打开）
BOX_TASK_SELECTED_ICON = "box_task_selected.png"  # 任务储物格已打开（选中态，无需再点击）
XIULUO_RETURN_ICON = "xiuluo_huichengfu.png"      # 修罗回城符模板（用户实测素材）
TASK_BOX_THRESHOLD = 0.8         # 储物格按钮双模板判定阈值（与其他模板一致）
TASK_BOX_TIMEOUT = 10            # 等待任务储物格打开的超时（点击后渲染/切换）
RETURN_TALISMAN_TIMEOUT = 15     # 等待回城符在任务储物格中出现的超时
RETURN_POLL_INTERVAL = 0.5       # 返程轮询间隔（打开动画约 0.4s，实测点击后 1s 内
                                 # 出现选中态；非防抖判定点，无需更长窗口）
# 任务储物格按钮相对背包面板标志（bag_box 中心）的偏移与搜索条带：
# 面板可拖动但内部布局固定，偏移恒定（面板左缘/中部两位置实测
# +349/+305 与 +353/+305，取均值）；条带留 55px 余量，带区全未命中
# 才全帧兜底（限频：仅带区未命中时触发）
TASK_BOX_OFFSET = (351, 305)
TASK_BOX_BAND_MARGIN = 55

# 领取后对话框「今日已完成次数：N」的完成次数解析（OCR 可能把冒号误读，兼容）
_CLAIM_COUNT_RE = re.compile(re.escape(CLAIM_COUNT_TEXT) + r"\s*[:：]?\s*(\d+)")


def _near_wuyi_spot(pos):
    """判断位置是否在巫医 NPC (115,72) 附近（严格容差 ±5）。

    小距离移动场景：附近定义必须严格，宽松容差会漏判偏移（用户确认）。
    """
    return game_map.is_at_position(pos, LINGSHOUCUN,
                                  WUYI_SPOT[0], WUYI_SPOT[1],
                                  WUYI_NEAR_TOLERANCE)


def _run_wuyi_chain(win, hover_before_click=False, report_condition=False,
                    click_option_self=False):
    """自动巫医主链路（自动流程与验证共用）：前往巫医 NPC 医治。

    1. 巫医条件（2026-10-09 用户确认）：自动巫医按间隔设置无条件
       执行，血蓝正常也照常巫医——不再以血蓝为门槛；
       report_condition=True（验证模式）时检查召唤兽红条/蓝条并仅
       报告（不影响执行，只读检查不触发右键回满）
    2. 位置检查：是否在灵兽村 (115,72) 附近（严格容差 ±5）
    3. 不在 → 打开小地图点击 (610,442) 寻路 → 等待走到坐标附近
       （位置 ±5 / NPC 模板可见 / 位置由可读变缺失均算到达，超时 8s
       即取消）→ 到达后 3s 窗口识别 NPC 模板（未识别到即本次取消，
       下次轮到再试——用户 2026-10-09 确认）
    4. 点击巫医 NPC（xiuluo_lingshoucunwuyi.png）打开医治对话框。
       真实流程：等待战斗线程点击「全部医治和修复」（battle.py 已有
       完整逻辑，无需重复实现——对话框出现→消失即处理完成）；
       点击后设置 battle.wuyi_expected_at 通知战斗线程切入 2s 高频
       检查（10s 空闲节奏会错过 8s 等待上限，被迫兜底自点）；
       click_option_self=True（验证模式）：主进程没有战斗线程，验证
       自行点击医治选项（复用 battle._find_wuyi_option，与战斗线程
       行为等效——不宜把常驻 battle 线程拉进主进程，避免与后续任务
       的战斗线程抢点击）；
       hover_before_click=True 时 NPC 点击前先移动鼠标定位（无延时，验证时序与真实流程一致）

    到达/点击的最终真信号是对话框出现：坐标 OCR 不可靠（实测走完
    站在 NPC 旁位置显示缺失，坐标判定卡死到达循环），NPC 模板远距离
    也会命中（使者教训）。单次尝试（用户 2026-10-09 确认）：找不到
    NPC/对话框未出现即本次取消，下次轮到再试——失败不阻塞修罗主流程。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过自动巫医"
    # 1. 血蓝报告（仅验证模式）：只读检查，不影响执行
    if report_condition:
        low = status_bars.beast_below_threshold(win)
        log(f"[修罗][巫医] 召唤兽血蓝{'低于阈值' if low else '正常'}（仅报告，不影响执行）", "SYS")
    # 2. 位置检查（严格容差：小距离移动宽松容差会漏判偏移；坐标
    #    读不到按不在处理——走去巫医无害）
    pos = game_map.read_map_position(win)
    if pos is None:
        pos = game_map.read_position_with_recovery(win)
    need_walk = not _near_wuyi_spot(pos)
    # 3. 走位（需要时）：小地图点击巫医位置，等待走到坐标附近
    #    （位置 ±5 或 NPC 模板可见均算到达——坐标 OCR 不可靠时
    #    模板兜底，原实现教训）
    if need_walk:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
        human_click(win, WUYI_MAP_POS[0], WUYI_MAP_POS[1])
        log("[修罗][巫医] 已点击小地图巫医位置，自动寻路中", "SYS")
        time.sleep(1.0)
        game_map.close_maps(win)
        deadline = time.time() + WUYI_ARRIVE_TIMEOUT
        arrived = False
        pos_seen = False
        while time.time() < deadline:
            if is_in_battle(win):
                time.sleep(2.0)
                continue
            if game_map.find_template(win, WUYI_NPC_ICON) is not None:
                arrived = True
                break
            pos2 = game_map.read_map_position(win)
            if _near_wuyi_spot(pos2):
                arrived = True
                break
            if pos2 is not None:
                pos_seen = True  # 位置可读说明仍在路上
            elif pos_seen:
                # 位置由可读变为缺失：实测走到巫医旁位置显示会消失
                # （站在 NPC 旁坐标判定卡死到达循环的根因），视为已到达，
                # 转入 3s 识别窗口——识别不到快速失败
                arrived = True
                break
            time.sleep(2.0)
        if not arrived:
            return False, "走到巫医坐标附近超时（本次取消）"
    # 4. 到达后 3s 窗口识别 NPC 模板（0.5s 轮询覆盖待机动画波动相位）；
    #    未识别到即本次取消（用户 2026-10-09 确认：3s 含识别，最长 3s）
    pt = None
    deadline = time.time() + WUYI_NPC_TIMEOUT
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        pt = game_map.find_template(win, WUYI_NPC_ICON)
        if pt is not None:
            break
        time.sleep(0.5)
    if pt is None:
        return False, f"到达后 {WUYI_NPC_TIMEOUT}s 内未识别到巫医 NPC（本次取消）"
    if hover_before_click:
        human_move(win.rect.left + pt[0], win.rect.top + pt[1])
        log(f"[修罗][验证] 已移动鼠标到巫医 NPC ({pt[0]}, {pt[1]})，"
            f"已定位到（验证时序与真实流程一致）", "SYS")
    human_click(win, pt[0], pt[1])
    # 通知战斗线程：巫医对话框即将出现，切入 2s 高频检查（10s 空闲节奏
    # 相位不巧时会错过 8s 等待上限，被迫兜底自点，2026-10-29 日志教训）
    battle.wuyi_expected_at = time.time()
    log(f"[修罗][巫医] 已点击巫医 NPC ({pt[0]}, {pt[1]})，等待医治对话框处理", "SYS")
    # 等待对话框出现并处理（出现→消失）
    deadline = time.time() + WUYI_HANDLE_TIMEOUT
    appeared = False
    option_clicked = False
    last_items = None   # 对话框打开期间的最近 OCR（超时自行点击兜底用）
    last_img_np = None
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        # 与 battle.py 的巫医判定同款调用（内部模糊匹配，无需预规范化）
        has_dialog = any(_dialog_text_present(items, t) for t in WUYI_DIALOG_TEXTS)
        if has_dialog:
            appeared = True
            last_items, last_img_np = items, img_np
            if click_option_self and not option_clicked:
                # 验证模式：主进程无战斗线程，自行点击医治选项
                # （与真实环境的战斗线程行为等效）
                pt_opt = _find_wuyi_option(items, img_np)
                if pt_opt is not None:
                    human_click(win, pt_opt[0], pt_opt[1])
                    log(f"[修罗][验证] 已点击「全部医治和修复」"
                        f"@ ({pt_opt[0]}, {pt_opt[1]})", "SYS")
                    option_clicked = True
            time.sleep(1.0)
            continue
        if appeared:
            log("[修罗][巫医] 医治对话框已处理完毕", "SYS")
            return True, "自动巫医完成"
        time.sleep(0.5)
    if appeared:
        # 超时仍未关闭：战斗线程未及时点击（弹窗动画吞掉过早点击等）。
        # 自行点击一次兜底，保证「巫医完 → 再追踪」的顺序
        # （用户 2026-10-28：超时上限 8s + 兜底自行点击）
        if not option_clicked and last_items is not None:
            pt_opt = _find_wuyi_option(last_items, last_img_np)
            if pt_opt is not None:
                human_click(win, pt_opt[0], pt_opt[1])
                log(f"[修罗][巫医] 等待超时，自行点击「全部医治和修复」"
                    f"@ ({pt_opt[0]}, {pt_opt[1]})", "WARN")
                time.sleep(1.0)  # 等待点击生效
                return True, "医治对话框超时未关闭，已自行点击选项"
        return True, "医治对话框已出现（战斗线程将继续处理）"
    # 对话框未出现（距离不够）：本次取消，下次轮到再试
    return False, "点击巫医后未出现医治对话框（本次取消）"


def _auto_wuyi(win):
    """自动巫医（用户流程，通用设置开关+间隔门控）：每 N 轮领取后
    无条件前往巫医医治（血蓝正常也照常，见 _run_wuyi_chain）。"""
    return _run_wuyi_chain(win)


def verify_wuyi_flow(win):
    """验证自动巫医流程（高级设置-验证按钮，主进程调用）。

    与 _auto_wuyi 同一链路，区别：血蓝状态检查并仅报告（不影响执行），
    NPC 点击前先移动鼠标定位（无延时），并自行点击医治选项
    （主进程没有战斗线程——真实环境中该点击由任务进程的战斗线程
    完成，二者行为等效）。返回 (是否成功, 描述)。
    """
    return _run_wuyi_chain(win, hover_before_click=True, report_condition=True,
                           click_option_self=True)


def _near_claim_spot(pos, tolerance):
    """判断位置是否在灵兽村领取点附近（地名精确匹配，容差抗 OCR 噪声）。"""
    return game_map.is_at_position(pos, LINGSHOUCUN,
                                  CLAIM_SPOT[0], CLAIM_SPOT[1], tolerance)


def _near_shizhe_spot(pos):
    """是否在灵兽村使者领取点坐标框内（严格 x/y 上下界，非容差圆）。"""
    if pos is None or pos[0] != LINGSHOUCUN:
        return False
    xmin, ymin, xmax, ymax = SHIZHE_SPOT_BOX
    return xmin <= pos[1] <= xmax and ymin <= pos[2] <= ymax


def _wait_claim_entry(win, timeout):
    """等待领取入口出现（按钮或使者），返回 (pt, 来源) 或 (None, None)。

    战斗中跳过等待；每轮询只截一帧，双模板批量匹配共用一帧。
    走动中镜头随人物移动，点击须瞬时落点（调用方用 precise_click
    move_duration=0 取消拟人化移动，实测 2026-10-21 拟人化移动期间
    点击位置已过期偶现无效）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.5)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        found = game_map.match_frame_multi(frame, (LINGQU_ICON, LINGSHOUCUN_SHIZHE_ICON))
        for icon, label in ((LINGQU_ICON, "领取任务按钮"),
                            (LINGSHOUCUN_SHIZHE_ICON, "灵兽村使者")):
            if found[icon] is not None:
                return found[icon], label
        time.sleep(1.5)
    return None, None


def _find_claim_option(items, img_np):
    """在 OCR 结果中查找领取对话框的绿色选项，返回 (cx, cy, txt, 是否兜底) 或 None。

    选项实测换行渲染：OCR 无单行包含完整文案，完整文案匹配从未命中、
    两次实测全靠「帮忙」兜底。按优先级依次尝试：完整文案 → 尾部
    「要我帮忙吗」→ 含「帮忙」。OCR 对中文标点误读常见（如「，」读成
    「；」），匹配前统一去标点。限定对话框区域（x≥PANEL_MAX_X），
    避免误点追踪面板的绿字链接。点击与验证（悬停）共用本函数。
    """
    needles = (re.sub(r"[^\w]", "", CLAIM_DIALOG_OPTION),  # 完整文案（单行渲染时）
               norm_text(CLAIM_OPTION_TAIL),             # 尾部（换行渲染时实测命中）
               norm_text("帮忙"))                        # 最后兜底
    for needle in needles:
        for txt, rect, score in items:
            if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                # 合并行按比例取选项自身中心（见 dialog_common.green_option_center）
                ntxt = re.sub(r"[^\w]", "", txt)
                cx, cy = green_option_center(img_np, rect, len(needle), len(ntxt))
                return cx, cy, txt, needle == norm_text("帮忙")
    return None


def _task_in_tracker_zoom(img):
    """任务块裁剪 2x 放大重识别（面板 OCR 漏检兜底，看打选项同款）。

    修罗标题行带彩色徽标被区域 OCR 确定性漏检、内容链接行在面板
    插入滑动动画期间 det 偶发失败——裁剪任务块（标题+链接行区域）
    2x 放大后全帧引擎重识别，检出能力更强。返回是否检出任务。
    """
    try:
        l, t, r, b = TASK_BLOCK_REGION
        crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                             Image.LANCZOS)
        items2, _ = game_map.ocr_image(crop)
        for txt, _, score in items2:
            if score < 0.55:
                continue
            if TASK_TITLE in txt or any(ft in txt for ft in TASK_FALLBACK_TEXTS):
                return True
    except Exception:
        pass
    return False


def _team_dialog_zoom(win, img):
    """队伍确认弹窗的对话框区域 2x 放大兜底识别（弹窗文案 OCR 偶发
    漏检——实测 2026-10-26 人数不足弹窗 3 轮未检出、空转 ~9s）。

    检出后按设置点「确定」/「我再想想」（看打选项同款放大重识别
    点击路径）。返回 "declined" / "accepted" / None（非本弹窗）。
    """
    try:
        l, t, r, b = task_tracker.DIALOG_REGION
        crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                             Image.LANCZOS)
        items2, _ = game_map.ocr_image(crop)
        joined = "".join(tx for tx, _, s in items2 if s >= 0.55)
        if not _fuzzy_contains(joined, norm_text(TEAM_CONFIRM_TEXT),
                               threshold=0.8):
            return None
        min_members = load_xiuluo_settings()["min_members"]
        needle = norm_text(TEAM_CONFIRM_DECLINE if min_members >= 5
                           else TEAM_CONFIRM_ACCEPT)
        for txt, rect, score in items2:
            if score < 0.55 or needle not in norm_text(txt):
                continue
            rect2 = [l + rect[0] // 2, t + rect[1] // 2,
                     l + rect[2] // 2, t + rect[3] // 2]
            if rect2[0] < task_tracker.PANEL_MAX_X:
                continue
            frame = np.asarray(img)[:, :, :3]
            if not task_tracker.is_green_text(frame, rect2):
                continue
            cx, cy = task_tracker.green_text_center(frame, rect2,
                                                    prefer_underline=False)
            human_click(win, cx, cy)
            if min_members >= 5:
                log("[修罗] 队伍不足 5 人，按设置（最少 5 人）点「我再想想」"
                    "（2x 补识），等待补人", "WARN")
                return "declined"
            log(f"[修罗] 队伍不足 5 人，按设置（最少 {min_members} 人）"
                f"点「确定」接受领取（2x 补识）", "SYS")
            return "accepted"
    except Exception:
        pass
    return None


def _click_claim_option(win, items, img_np):
    """点击领取对话框中的绿色选项「闲来无事，要我帮忙吗」，成功返回 True。

    识别点击成功即记录选项坐标（高性能模式缓存用；位置漂移时由
    本路径自然刷新）。
    """
    found = _find_claim_option(items, img_np)
    if found is None:
        return False
    cx, cy, txt, fallback = found
    human_click(win, cx, cy)
    suffix = "（兜底匹配）" if fallback else ""
    log(f"[修罗] 已点击绿色选项「{txt}」{suffix}", "SYS")
    app_utils.fixed_pos_set("claim_option", win, cx, cy)
    move_away(win)  # 鼠标停留选项上会使其悬停变色，干扰后续绿字判定
    return True


def _handle_team_warning(win, items, img_np):
    """对话框出现「修罗的战斗力异常强大…」：队伍数量小于 3，
    点击屏幕中间一次关闭对话框。返回是否已处理。
    """
    if not _norm_dialog_present(items, TEAM_WARNING_TEXT):
        return False
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[修罗] 队伍数量小于 3，已点击屏幕中间关闭提示对话框", "WARN")
    return True


def _handle_team_confirm(win, items, img_np):
    """领取确认弹窗（队伍不足 5 人时游戏询问是否仍要领取）。

    按修罗设置的最少人数决定（用户 2026-10-15 指定）：
    <5 人配置（3/4）→ 点「确定」接受领取；
    =5 → 点「我再想想」拒绝（等待补满 5 人再领）。
    返回 "accepted" / "declined" / None（非此弹窗）。
    """
    if not _norm_dialog_present(items, TEAM_CONFIRM_TEXT):
        return None
    min_members = load_xiuluo_settings()["min_members"]
    if min_members >= 5:
        _click_green_option_once(win, items, img_np, TEAM_CONFIRM_DECLINE,
                                 prefix="修罗")
        log("[修罗] 队伍不足 5 人，按设置（最少 5 人）点「我再想想」，等待补人", "WARN")
        return "declined"
    _click_green_option_once(win, items, img_np, TEAM_CONFIRM_ACCEPT,
                             prefix="修罗")
    log(f"[修罗] 队伍不足 5 人，按设置（最少 {min_members} 人）点「确定」接受领取", "SYS")
    return "accepted"


def _open_claim_map(win, pos=None):
    """地图感知导航到灵兽村领取点（世界地图/小地图自动选择）。

    位置读为灵兽村 → 直接打开小地图；其他地图（如五倍返程落点的
    宝象国）或位置不可读 → 世界地图点击灵兽村图标。随后点击小地图
    领取点坐标寻路。返回 (是否成功, 描述)。

    领取按钮模板与五倍宝象国领取按钮同图（跨模板匹配 0.947），不在
    灵兽村时直接开小地图点坐标会在别的图乱走（2026-10-23 五倍反向
    实测：灵兽村小地图点宝象国坐标，人物跨图乱走）。

    pos：调用方刚读到的位置（同帧复用，免重截+重 OCR——_walk_to_claim_spot
    决策帧已读过）；None 时自行截帧读取。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    if is_in_battle_frame(img):
        return False, "战斗中，跳过地图导航"
    if pos is None:
        pos = game_map.read_map_position(win, img=img)
    if pos is not None and pos[0].strip() == LINGSHOUCUN:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    else:
        if pos is not None:
            log(f"[修罗] 不在灵兽村（{pos[0].strip()}[{pos[1]},{pos[2]}]），"
                f"经世界地图前往灵兽村", "SYS")
        ok, info = game_map.open_mini_map_city(win, WORLD_MAP_LINGSHOUCUN_ICON,
                                               LINGSHOUCUN)
        if not ok:
            return False, info
    human_click(win, LINGSHOU_MAP_POS[0], LINGSHOU_MAP_POS[1])
    return True, "已点击灵兽村小地图领取点位置"


def _walk_to_claim_spot(win, just_returned=False):
    """寻路到灵兽村领取点附近（与队伍人数无关，队长身份下执行）。

    用户流程：已在领取点 110,90 附近（±15）→ 跳过寻路；
    不在灵兽村 → 世界地图点击灵兽村图标 → 小地图点击 (603,392) 寻路；
    在灵兽村但不在领取点附近 → 直接打开小地图点击 (603,392) 寻路。
    等待到达 110,90（±5 严格容差：走动中识图点击容易点不到）。
    另有使者领取点坐标框（用户 2026-10-21）：框内且使者模板可见
    也算到达，直接点使者领取（按钮模板可见必可领取，仅使者可见
    时须在框内——使者远距离也可见，点远处使者不出对话框）。

    到达判定优先用直接信号（领取任务按钮可见 = 已到领取点）——
    位置 OCR 坐标实测不可靠：区域快引擎把 111,90 误读为
    11,90/102,79/18,14，玩家站在领取点却被判定未到达、空等 30s+
    并误走小地图导航。按钮信号仅 just_returned=True（上一轮返程
    成功，落点必在灵兽村）时无条件信任；否则须地图名门控——按钮
    模板与五倍宝象国领取按钮同图（跨模板匹配 0.947），跨图误命中
    会把五倍领取按钮当成修罗入口（实测 2026-10-23）。位置读取失败
    也按不在附近处理，直接走地图寻路（用户 2026-10-09 确认）：
    加载/渲染等待由 open_mini_map_city 的轮询吸收，原 8s 等待 +
    Alt+C 恢复实测整段空转（且 Alt+C 是位置显示开关，加载期误按
    会关掉显示——此处不再按）。
    just_returned=True（上一轮返程成功，落点必在灵兽村）时例外：
    位置识别失败极可能是传送加载未完成（实测 12:30 加载超 8s，
    按钮也未渲染），改为轮询等待按钮/位置出现（RETURN_LOAD_TIMEOUT
    上限），超时才走世界地图寻路兜底——不再白走地图。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    btn_visible = game_map.match_frame_np(frame, LINGQU_ICON) is not None
    if just_returned and btn_visible:
        # 返程落点必在灵兽村（回城符落点地图固定）：按钮直达信号无条件
        # 信任，免位置 OCR（每轮领取热路径）。到达判据只认按钮——使者
        # 模板远距离也会命中（实测距离过远点击不出现对话框），按钮
        # 只在可点击范围内出现
        return True, "已在领取点附近（领取按钮可见）"
    # 位置读取（同帧复用）。非返程轮次（任务切换/中途失败后位置未知）
    # 按钮信号须地图名门控：领取按钮模板与五倍宝象国领取按钮同图
    # （跨模板匹配 0.947），跨图误命中会把五倍领取按钮当成修罗入口
    # （2026-10-23 五倍反向实测）。坐标误读场景（111,90→11,90/102,79/
    # 18,14）地图名仍对，按钮信号继续兜底
    pos = game_map.read_map_position(win, img=img)
    if btn_visible and (pos is None or pos[0].strip() == LINGSHOUCUN):
        return True, "已在领取点附近（领取按钮可见）"
    # 坐标未识别时不使用使者模板兜底——使者远距离也可见，远处命中会
    # 误判已在领取点、点击无效（用户 2026-10-11 确认）：到达判定只认
    # 领取按钮（仅在可点击范围内出现）
    near_old = _near_claim_spot(pos, CLAIM_NEAR_TOLERANCE)
    near_shizhe = _near_shizhe_spot(pos)
    if near_old or near_shizhe:
        # 位置近但入口不可见：可能是位置误读，或超出可点击范围
        # （实测返程落点 101,83 距领取点 12.2 单位处按钮不出现——
        # 旧逻辑直接判「已在附近」跳过寻路，入口等待 20s 空转、
        # 2026-10-12 第 70 轮手动走位才恢复）。给入口 3s 渲染短窗口
        # （返程加载后可能延迟出现），窗口内出现即免寻路，仍无则走
        # 小地图重走兜底（五倍同款）。按钮可见必可领取；仅使者可见
        # 时须在坐标框内才可领取（用户 2026-10-21）。
        deadline = time.time() + CLAIM_NEAR_BTN_GRACE
        while time.time() < deadline:
            if game_map.find_template(win, LINGQU_ICON) is not None:
                return True, "已在领取点附近（领取按钮可见）"
            if near_shizhe \
                    and game_map.find_template(win, LINGSHOUCUN_SHIZHE_ICON) is not None:
                return True, "已在领取点附近（使者可见，坐标框内可领取）"
            time.sleep(0.5)
        log("[修罗] 位置近但领取入口未出现，改走小地图重走领取点", "WARN")
    if pos is None and just_returned:
        # 刚返程：位置识别失败大概率是传送加载未完成（回城符落点
        # 必在灵兽村，不会在别处）。只识别一次（同帧做按钮匹配 +
        # 位置 OCR，不轮询）：加载未完成则落入下方世界地图寻路
        # 兜底——该路径实测加载期可用（12:30），寻路走完加载也
        # 完成，慢加载的等待由兜底路径自然吸收。返程后原地不动
        # 不会进战斗，无需战斗判定（用户 2026-10-11 确认）
        img = win.capture(force_activate=False)
        if img is not None:
            if game_map.find_template_in(img, LINGQU_ICON) is not None:
                return True, "已在领取点附近（领取按钮可见，等待传送加载完成）"
            pos2 = game_map.read_map_position(win, img=img)
            if _near_claim_spot(pos2, CLAIM_NEAR_TOLERANCE) \
                    or _near_shizhe_spot(pos2):
                return True, "已在领取点附近（等待传送加载完成）"
    ok, info = _open_claim_map(win, pos)  # 决策帧已读位置，同帧复用
    if not ok:
        return False, info
    log("[修罗] 已点击灵兽村小地图领取点位置，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    game_map.close_maps(win)
    # 等待到达：按钮可见优先（直接信号），坐标 ±5 作为次级判据
    # （坐标误读时按钮判据兜底，实测站在领取点空等 30s+ 的根因）
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        if game_map.find_template(win, LINGQU_ICON) is not None:
            log("[修罗] 已到达领取点附近（领取按钮可见）", "SYS")
            return True, "已到达领取点附近"
        pos = game_map.read_map_position(win)
        if _near_shizhe_spot(pos) \
                and game_map.find_template(win, LINGSHOUCUN_SHIZHE_ICON) is not None:
            # 使者领取点：仅使者可见时须坐标框内才算到达（使者远距离
            # 也可见，不能单独作到达判据——用户 2026-10-21）
            log("[修罗] 已到达领取点附近（使者可见，坐标框内可领取）", "SYS")
            return True, "已到达领取点附近"
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            log("[修罗] 已到达领取点附近", "SYS")
            return True, "已到达领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[修罗] 前往领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"寻路到领取点超时（未到达灵兽村 {CLAIM_SPOT[0]},{CLAIM_SPOT[1]} 附近）"


def _relocate_to_claim_spot(win):
    """小地图点击领取点坐标重新寻路（领取校验兜底，用户 2026-10-09）。

    领取已点但任务追踪未出现时调用：重走一次领取点坐标可能触发任务
    渲染。与 _walk_to_claim_spot 的区别：不判断是否已在领取点附近
    （通常在领取点原地重走，几秒即回），但同样走地图感知导航
    （_open_claim_map：不在灵兽村时先经世界地图）。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过重走"
    ok, info = _open_claim_map(win)
    if not ok:
        return False, info
    log("[修罗] 已点击灵兽村小地图领取点位置，重新寻路（领取校验兜底）", "SYS")
    time.sleep(1.0)
    game_map.close_maps(win)
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        if game_map.find_template(win, LINGQU_ICON) is not None:
            return True, "已重走到领取点附近（领取按钮可见）"
        pos = game_map.read_map_position(win)
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            return True, "已重走到领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[修罗] 重走领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, "重走领取点超时"


def _click_entry_ensuring_dialog(win, log_tag="[修罗]", hover_before_click=False):
    """点击领取入口（按钮或使者）并确保对话框出现；未出现判定坐标不对：
    打开小地图点击领取点坐标重定位后重试一次（用户确认的兜底路径）。

    hover_before_click：点击前先移动鼠标定位入口（无延时，验证时序与真实流程一致）。
    返回 (是否成功, 描述, 来源, 坐标, 对话框帧)；成功时对话框已打开
    （选项可见），对话框帧 (items, img_np) 供领取流程同帧点选项——
    免重截+重 OCR（每轮省 ~0.5s）。
    """
    dialog_seen = False
    appear_timeout = CLAIM_DIALOG_APPEAR_TIMEOUT
    src2 = pt2 = None
    seen_frame = None
    for attempt in (1, 2):
        # 入口坐标直接用等待循环最近一帧的匹配结果点击：等待刚命中后
        # 再补一次识别（旧双检）会因标记动画/镜头未稳出现「出现后又
        # 消失」假失败（实测到达秒级报错未点击）；点不中由对话框出现
        # 判定兜底（小地图重定位重试）
        wait_timeout = ENTRY_FIRST_WAIT if attempt == 1 else CLAIM_BUTTON_TIMEOUT
        pt2, src2 = _wait_claim_entry(win, wait_timeout)
        if pt2 is None:
            if attempt == 1:
                # 入口未出现：小地图重走领取点（原地或短途，几秒即回）
                # 后重试一次，不再空等整轮（2026-10-12 实测第 70 轮
                # 空等 20s 失败、需人工走位才恢复——五倍同款自愈）
                log(f"{log_tag} 等待入口 {wait_timeout}s 未出现，小地图重走领取点后重试", "WARN")
                ok, info = _relocate_to_claim_spot(win)
                if not ok:
                    log(f"{log_tag} {info}", "WARN")
                    return False, "到达后未出现领取任务按钮/灵兽村使者", None, None, None
                continue
            return False, "到达后未出现领取任务按钮/灵兽村使者（重走领取点后仍无）", None, None, None
        if hover_before_click:
            human_move(win.rect.left + pt2[0], win.rect.top + pt2[1])
            log(f"{log_tag} 已移动鼠标到{src2} ({pt2[0]}, {pt2[1]})，"
                f"已定位到（验证时序与真实流程一致）", "SYS")
        # 瞬时点击（取消拟人化移动）：走动中镜头随人物移动，拟人化
        # 移动期间点击位置已过期（实测 2026-10-21 偶现点击无效）
        precise_click(win, pt2[0], pt2[1], move_duration=0)
        log(f"{log_tag} 已点击{src2}（领取）", "SYS")
        # 高性能：领取选项坐标已缓存 → 固定等待渲染后由 _claim_task
        # 直接按缓存坐标点选项（点不中由「任务进追踪」校验循环识别
        # 补点兜底，无 seen 帧返回）
        if app_utils.high_perf_on() \
                and app_utils.fixed_pos_get("claim_option", win) is not None:
            time.sleep(random.uniform(*CLAIM_DIALOG_FIXED_WAIT))
            dialog_seen = True
            break
        # 等待对话框出现：轮询选项（正常 ~1.5s 即出）；首次尝试短窗口，
        # 重定位后的第二次尝试用完整窗口
        deadline = time.time() + appear_timeout
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.3)
                continue
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _handle_team_warning(win, items, img_np):
                return False, "队伍数量小于 3，无法领取（已关闭提示对话框）", None, None, None
            if _find_claim_option(items, img_np) is not None:
                dialog_seen = True
                seen_frame = (items, img_np)
                break
            time.sleep(0.3)
        if dialog_seen:
            break
        if attempt == 1:
            # 对话框未出现：坐标不对 → 地图感知重定位（在灵兽村直接小地图；
            # 不在则先经世界地图——按钮模板跨图误命中时人可能在别的图）
            log(f"{log_tag} 点击后未出现对话框（坐标不对），打开小地图重定位", "WARN")
            ok, info = _open_claim_map(win)
            if not ok:
                return False, info, None, None, None
            log(f"{log_tag} 已点击灵兽村小地图领取点位置，重新寻路中", "SYS")
            time.sleep(1.0)
            game_map.close_maps(win)
            # 等待重定位到达（只认领取按钮——按钮才代表进入可点击范围，
            # 使者模板远距离也会命中，不能作到达判据）
            deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
            while time.time() < deadline:
                if is_in_battle(win):
                    time.sleep(2.0)
                    continue
                if game_map.find_template(win, LINGQU_ICON) is not None:
                    break
                time.sleep(2.0)
            appear_timeout = CLAIM_DIALOG_TIMEOUT
    if not dialog_seen:
        return False, "点击领取入口后对话框始终未出现（小地图重定位后仍无）", src2, pt2, None
    return True, f"已点击{src2}", src2, pt2, seen_frame


def _claim_task(win, allow_fill=False):
    """在领取点附近领取修罗任务（前提：已走到领取点附近）。

    识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
    → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
    （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框，本轮领取失败。
    领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）。

    返回 (是否成功, 描述, 校验帧)：校验帧为任务进追踪的确认帧
    （items, img_np），供 follow_task 首轮复用免重复截图+OCR
    （省 ~1.2s，点击任务追踪绿字立即进行）；Alt+C 兜底路径无帧返回 None。

    领取时序优化（实测 09:05 一轮：入口点击→选项点击 4s、选项点击→
    已领取判定 4s、判定→追踪点击 2s）：
    1. 选项点击直接复用「对话框已出现」的同一帧（免重截+重 OCR）
    2. 完成次数解析并入校验循环的同一批双区域 OCR 帧（原独立 1.2s
       窗口从未命中，每轮空转）
    3. 校验循环内确认任务出现即返回，不再重复一次 has_task 全量检查
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取", None
    # 识图领取入口（按钮或灵兽村使者）并点击；点击后对话框未出现
    # （短窗口内选项不可见）判定坐标不对：打开小地图点击领取点坐标
    # 重定位后重试一次（用户确认的兜底路径）
    ok, info, _src2, _pt2, seen = _click_entry_ensuring_dialog(win)
    if not ok:
        return False, info, None
    # 对话框：点绿色选项「闲来无事，要我帮忙吗」完成领取——直接用
    # 「对话框已出现」的同一帧点击（帧上已确认选项可见，点同一帧
    # 与 0.3s 后重截重 OCR 再点等价）；未点中（罕见：选项渲染中）
    # 再走短窗轮询兜底。完整文案与「帮忙」兜底在同一帧先后尝试——
    # OCR 把选项拆成两行时完整串永远匹配不上（实测主循环空转 15s
    # 后才进兜底点中），合并后首帧可读即点中。has_task（追踪区 OCR
    # ~0.9s）限频到每 4 轮——对话框出现前任务不可能已进追踪
    # 高性能：入口点击后未 OCR（seen 为 None）→ 按缓存坐标直点，
    # 生效与否由「任务进追踪」校验循环判定并识别补点兜底
    used_cached = False
    if seen is None:
        used_cached = app_utils.high_perf_click(win, "claim_option",
                                                "领取选项", prefix="[修罗] ")
    if not used_cached:
        claimed = False
        if seen is not None:
            items, img_np = seen
            claimed = _click_claim_option(win, items, img_np)
        polls = 0
        deadline = time.time() + CLAIM_DIALOG_TIMEOUT
        while not claimed and time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.3)
                continue
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            # 领取确认弹窗（不足 5 人询问是否仍要领取）：按设置决定
            confirm = _handle_team_confirm(win, items, img_np)
            if confirm == "declined":
                if allow_fill:
                    log("[修罗] 领取被拒：队伍人数不足，进入监控补人循环", "WARN")
                    _monitor_team_and_fill(win)  # 人满才返回；非队长不返回（保持模式）
                    return False, "队伍已补齐（监控循环判定），下轮重试领取", None
                return False, "队伍人数不足，已点「我再想想」（检查队伍开关关闭，不补人）", None
            # accepted：已点「确定」接受领取，任务将进追踪（has_task 兜底置 claimed）
            if _handle_team_warning(win, items, img_np):
                if allow_fill:
                    log("[修罗] 领取被拒：队伍人数不足，进入监控补人循环", "WARN")
                    _monitor_team_and_fill(win)  # 人满才返回；非队长不返回（保持模式）
                    return False, "队伍已补齐（监控循环判定），下轮重试领取", None
                return False, "队伍数量小于 3，无法领取（已关闭提示对话框）", None
            claimed = _click_claim_option(win, items, img_np)
            polls += 1
            if not claimed and polls % 4 == 0 \
                    and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
                claimed = True
            time.sleep(0.3)
        if not claimed:
            return False, "领取对话框处理失败", None
    # 校验任务出现在追踪面板（正常轮询 CLAIM_VERIFY_WAIT 秒）与
    # 「今日已完成次数：N」解析共用同一批双区域 OCR 帧：任务出现即
    # 确认（不再重复一次 has_task 全量检查）；期间处理队伍不足提示
    # 对话框（点击选项后队伍掉到 3 人以下时出现）
    deadline = time.time() + CLAIM_VERIFY_WAIT
    count = None
    found_frame = None
    polls = 0
    reclick_poll = 0  # 识别补点发生的轮询序（补点后队伍对话框需再覆盖一轮）
    option_seen = False  # 校验期是否曾见领取选项（区分未渲染与点击已生效）
    track_gate = task_tracker.TrackGate(min_interval=0.0)  # 面板差分门（领取校验为
        # 短窗口关键检测，不限频：任务渲染后须下一轮即检出，限频会把
        # 「未出现」结论复用 2s、被 2x 重识别兜底捡走（实测每次领取
        # 多 3-4s，2026-10-28）
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        polls += 1
        # 面板单区域 OCR（0.9s）为主节奏：任务进追踪是确认主信号。
        # 对话框区域检查（队伍确认/警告、完成次数、缓存点击识别补点）
        # 仅在必要轮询执行：前 2 轮（队伍对话框出现在选项点击后
        # ~0.5-1s）+ used_cached 待补点期间 + 补点后一轮（补点点击
        # 同样可能弹队伍对话框）+ 完成次数未解析（限前 4 轮）——
        # 其余轮询只 OCR 面板区（1.2s→0.9s），配合 sleep 0.2 节奏
        # 约 1.1s/轮。found_frame 复用面板单区域帧即可：follow 首轮
        # 绿字点击只用面板区，对话框区域缺席不产生误动作（extras
        # 无对话框行即不介入）
        need_dialog = polls <= 2 or used_cached or (count is None and polls <= 4) \
            or 0 < reclick_poll < polls <= reclick_poll + 1
        # 面板差分门：任务进追踪必然改变面板核心像素（门必开）；
        # 对话框区域（队伍确认/警告、完成次数、缓存补点）逐轮新鲜 OCR
        track_items = track_gate.ocr(img)
        if need_dialog:
            dialog_items, img_np = game_map.ocr_region(
                img, task_tracker.DIALOG_REGION)
            track_gate.paste_track(img, img_np)
            items = sorted(track_items + dialog_items, key=lambda x: x[1][1])
        else:
            items = track_items
            img_np = np.zeros_like(np.asarray(img))
            track_gate.paste_track(img, img_np)
        if task_tracker.has_task_in(items, img_np, TASK_TITLE, TASK_FALLBACK_TEXTS):
            found_frame = (items, img_np)
            break
        if not need_dialog:
            time.sleep(CLAIM_VERIFY_POLL_INTERVAL)
            continue
        # 完成次数对话框（限对话框区域行）同帧解析，零额外截图
        if count is None:
            raw = re.sub(r"\s", "", "".join(
                t for t, r, s in items
                if s >= 0.55 and r[0] >= task_tracker.PANEL_MAX_X))
            m = _CLAIM_COUNT_RE.search(raw)
            if m:
                count = int(m.group(1))
                log(f"[修罗] {CLAIM_COUNT_TEXT}：{count}", "SYS")
        # 领取确认弹窗（不足 5 人询问是否仍要领取）：按设置决定。
        # 弹窗文案 OCR 偶发漏检（实测 2026-10-26 3 轮未检出空转
        # ~9s）：第 2 轮一次性对话框区域 2x 放大补识别——弹窗在
        # 点击后 ~1s 出现、第 2 轮截帧必然已含弹窗；补识仅此一次，
        # 不增加常规路径的持续 OCR 负担
        confirm = _handle_team_confirm(win, items, img_np)
        if confirm is None and polls == 2 \
                and any(s >= 0.55 and r[0] >= task_tracker.PANEL_MAX_X
                        for _, r, s in items):
            # 对话框区域确有文字才放大补识：正常满队领取时对话框已
            # 关闭、区域为空则跳过（零额外 OCR，不影响常规流程）；
            # 人数不足弹窗挂在对话框区，此门控必放行
            confirm = _team_dialog_zoom(win, img)
        if confirm == "declined":
            if allow_fill:
                log("[修罗] 领取被拒：队伍人数不足，进入监控补人循环", "WARN")
                _monitor_team_and_fill(win)  # 人满才返回；非队长不返回（保持模式）
                return False, "队伍已补齐（监控循环判定），下轮重试领取", None
            return False, "队伍人数不足，已点「我再想想」（检查队伍开关关闭，不补人）", None
        # accepted：已点「确定」接受领取，校验循环继续等任务进追踪
        if _handle_team_warning(win, items, img_np):
            if allow_fill:
                log("[修罗] 领取被拒：队伍人数不足，进入监控补人循环", "WARN")
                _monitor_team_and_fill(win)  # 人满才返回；非队长不返回（保持模式）
                return False, "队伍已补齐（监控循环判定），下轮重试领取", None
            return False, "队伍数量小于 3，无法领取（已关闭提示对话框）", None
        # 高性能缓存点击兜底：任务未进追踪且选项仍可见 → 同帧识别补点
        # （识别点击成功后缓存坐标自动刷新，位置漂移自愈）；只补一次。
        # 「曾见选项后消失」判为缓存点击已生效、释放对话框区检查——
        # 选项从未出现则保持等待（慢渲染时补点须等到选项渲染出来）
        if used_cached:
            if _find_claim_option(items, img_np) is not None:
                option_seen = True
                _click_claim_option(win, items, img_np)
                reclick_poll = polls
                used_cached = False
            elif option_seen:
                used_cached = False
        # 面板 OCR 漏检：任务块裁剪 2x 放大重识别（首轮截帧必然早于
        # 渲染完成——任务实测 ~1s 后才出现——跳过免白耗）。置于弹窗
        # 处理之后：弹窗阻塞期任务不可能进面板，放大 OCR 白耗会拖慢
        # 弹窗响应
        if polls >= 2 and _task_in_tracker_zoom(img):
            log("[修罗] 任务块 2x 重识别检出任务", "SYS")
            # 重截一帧补全双区域 OCR：found_frame 需完整帧供 follow
            # 首轮复用（面板绿字点击与对话框处理依赖）
            img2 = win.capture(force_activate=False)
            if img2 is not None:
                items, img_np = game_map.ocr_regions(img2, task_tracker.OCR_REGIONS)
                found_frame = (items, img_np)
                break
        time.sleep(CLAIM_VERIFY_POLL_INTERVAL)
    if found_frame is not None:
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}", found_frame
    # 未检索到：按 Alt+C 飞行后再次检索（仅一次，多次重试无效）
    Keyboard.key_hot("alt", "c")
    log("[修罗] 任务追踪未检索到任务，按 Alt+C 飞行后再次检索", "WARN")
    time.sleep(1.0)
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        log("[修罗] Alt+C 飞行后任务已出现在追踪面板", "SYS")
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}", None
    # Alt+C 后仍无：小地图重走领取点坐标后再次检索（用户 2026-10-09
    # 确认：重走可能触发任务渲染，不直接判失败）
    ok2, info2 = _relocate_to_claim_spot(win)
    if not ok2:
        log(f"[修罗] {info2}", "WARN")
    elif task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        log("[修罗] 重走领取点后任务已出现在追踪面板", "SYS")
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}", None
    # 重走后仍无：不再重试，直接失败（下一轮 has_task 检查兜底，
    # 任务若随后渲染出现会以续跑方式被拾起）
    try:
        titles = [t["title"] for t in task_tracker.read_tasks(win)]
        log(f"[修罗] 验证失败，追踪面板当前标题: "
            f"{' / '.join(titles) if titles else '(无标题行)'}", "WARN")
    except Exception:
        pass
    return False, "已点击领取选项，但任务追踪中未检测到任务（Alt+C 飞行后仍无）", None


def _check_team_ready(win):
    """领取前队伍检查（修罗设置仅在队长时生效，一次面板开关）。

    队长时按通用设置-修罗设置的最少人数检查（本步不检查种族）。
    检查结果顺带更新调试悬浮层「队伍」行（身份 + 成员信息）。
    返回 (状态, 描述)；状态见 team.check_xiuluo_team：
    ready 可领取 / wait 人数不足 / not_leader 非队长或队长无法判定。
    """
    settings = load_xiuluo_settings()
    status, info, members = team.check_team_min(win,
                                                min_members=settings["min_members"])
    team.update_overlay_team(status, info, members)
    return status, info


def _check_team_with_retry(win, tries=3):
    """队伍检查带瞬态失败重试（与五倍同款，审计 B5）：截窗失败/面板
    打不开可能是瞬时问题，一次失败即转非队长保持模式会把队长永久
    挂起。瞬态类失败短间隔重试，连续 N 次才按 not_leader 处理；
    明确的非队长判定（面板打开且无解散按钮）与未组队（菜单中无
    「队伍信息」）不重试——未组队是确定性状态，重试纯空转。"""
    last = ("not_leader", "队伍检查未执行")
    for i in range(tries):
        status, info = _check_team_ready(win)
        last = (status, info)
        if status != "not_leader":
            return status, info
        # 未组队是确定性状态（没组队就没有队长）——注意其提示带
        # 「队长无法判定」前缀，必须先于瞬态判定短路返回
        if "未组队" in info:
            return status, info
        if "队长无法判定" not in info and "截图失败" not in info:
            return status, info  # 明确非队长（面板打开且无解散按钮）
        if i < tries - 1:
            log(f"[修罗] {info}，瞬态失败重试（{i + 1}/{tries - 1}）", "WARN")
            time.sleep(2.0)
    return last


_fight_fb_last_at = 0.0  # 兜底 2x OCR 冷却（防无选项对话框每轮询白耗）


def _click_fight_option_fallback(win, items, img_np):
    """「看打！」选项偶发未被区域 OCR 检出时的兜底（实测 90 轮 2 次：
    区域快引擎漏检选项行，全帧引擎可读——选项行与故事行紧贴时 det
    偶发吞掉）。对对话框区域 2x 放大重识别一次（巫医同款：选项字小
    时放大后 det 可检出）；只有真正识别到绿色「看打」文字才点击——
    不猜像素（像素兜底实测误点场景绿块 3 次，2026-10-12 废弃）。
    对话框区域有故事行（≥120px 宽非绿文本）才尝试；5s 冷却防无选项
    对话框（如队伍警告）每轮询白耗 2x OCR。返回是否已点击。
    """
    global _fight_fb_last_at
    has_story = any(s >= 0.55 and r[0] >= task_tracker.PANEL_MAX_X
                    and r[2] - r[0] >= 120
                    and not task_tracker.is_green_text(img_np, r)
                    for t, r, s in items)
    if not has_story:
        return False
    now = time.time()
    if now - _fight_fb_last_at < 5.0:
        return False
    _fight_fb_last_at = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    l, t, r, b = task_tracker.DIALOG_REGION
    crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                         Image.LANCZOS)
    items2, _ = game_map.ocr_zoom(crop)  # 快引擎：max/1600 检出分辨率同档、CPU 更低
    needle = norm_text(FIGHT_DIALOG_OPTION)
    for txt, rect, score in items2:
        if score < 0.55 or needle not in norm_text(txt):
            continue
        # 映射回窗口坐标；限定对话框区域 + 绿字判定
        rect2 = [l + rect[0] // 2, t + rect[1] // 2,
                 l + rect[2] // 2, t + rect[3] // 2]
        if rect2[0] < task_tracker.PANEL_MAX_X:
            continue
        frame = np.asarray(img)[:, :, :3]
        if not task_tracker.is_green_text(frame, rect2):
            continue
        cx, cy = task_tracker.green_text_center(frame, rect2,
                                                prefer_underline=False)
        human_click(win, cx, cy)
        log(f"[修罗] 「看打！」选项未识别（OCR 漏检），2x 放大重识别点击 @({cx},{cy})", "WARN")
        app_utils.fixed_pos_set("fight_option", win, cx, cy)
        move_away(win)
        return True
    return False


def _handle_fight_dialog(win, items, img_np):
    """对话开打：跟踪期间出现对话 → 点击绿色选项「看打！」进入战斗。

    点击成功即标记本轮战斗由「看打！」进入（战后据此决定是否返程）。
    OCR 对标点误读常见，去标点后按子串匹配；限定对话框区域
    （x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    高性能：检测到看打对话后按缓存坐标直点（首次由识别路径点击成功
    后记录）；同一对话框出现只试一次缓存，未生效（对话框仍在）下次
    轮询回退识别路径并刷新缓存——对话框位置可能随修罗名换行漂移，
    缓存点不中由本路径自愈。
    返回是否已处理。
    """
    global _fight_via_kanda, _fight_cached_used
    needle = norm_text(FIGHT_DIALOG_OPTION)
    present = any(score >= 0.55 and needle in norm_text(txt)
                  and rect[0] >= task_tracker.PANEL_MAX_X
                  and task_tracker.is_green_text(img_np, rect)
                  for txt, rect, score in items)
    if not present:
        _fight_cached_used = False  # 对话框消失：重置，下次出现再用缓存
        # 选项行偶发被区域 OCR 漏检（实测 90 轮 2 次，凤巢七层无名
        # 小妖「想要阻止我…」对话框）：有故事行时按像素兜底点绿字块
        if _click_fight_option_fallback(win, items, img_np):
            _fight_via_kanda = True
            return True
        return False
    if not _fight_cached_used and app_utils.high_perf_click(
            win, "fight_option", "「看打！」", prefix="[修罗] "):
        _fight_cached_used = True
        _fight_via_kanda = True
        return True
    for txt, rect, score in items:
        if score >= 0.55 and needle in norm_text(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[修罗] 已点击「看打！」进入战斗", "SYS")
            app_utils.fixed_pos_set("fight_option", win, cx, cy)
            _fight_via_kanda = True
            return True
    return False


def _follow_to_done(win, first_items=None):
    """寻路 + 对话开打 + 战斗：跟踪任务直到本轮完成。

    复用 xuanshu.follow_task：绿字点击自动寻路、对话开打走附加对话处理
    （点击「看打！」进入战斗）、战斗由战斗监控线程操作且跟踪中自动跳过。
    first_items：领取校验刚确认任务出现的 (items, img_np) 帧，首轮
    跳过截图+OCR 立即点击追踪绿字（省 ~1.2s）。
    跟踪失败为「任务内容中未找到绿色文案」（标题可见内容未渲染，
    实测 2026-10-21 领取完成后偶现）时执行一次补偿：走到领取点后
    重新跟踪——领取点面板识别可靠。最小影响原则：仅领取后首次跟踪
    （first_items 存在）且失败发生在跟踪开始 30s 内（首次点击及其
    重试的窗口）才补偿；跟踪中期的同类失败按原失败返回，交给
    主循环下轮重试（has_task 续跑，不重新领取）。
    返回 (是否成功, 描述)。
    """
    t0 = time.time()
    ok, info = follow_task(win, TASK_TITLE, prefix="修罗",
                           extra_dialog_handler=_handle_fight_dialog,
                           fallback_keywords=TASK_FALLBACK_TEXTS,
                           first_items=first_items)
    if not ok and first_items is not None \
            and "任务内容中未找到绿色文案" in info \
            and time.time() - t0 < CLAIM_FOLLOW_RECOVER_WINDOW:
        log(f"[修罗] {info}，走到领取点补偿重试跟踪（一次）", "WARN")
        ok2, info2 = _walk_to_claim_spot(win)
        if not ok2:
            return False, f"{info}；走到领取点失败: {info2}"
        ok, info = follow_task(win, TASK_TITLE, prefix="修罗",
                               extra_dialog_handler=_handle_fight_dialog,
                               fallback_keywords=TASK_FALLBACK_TEXTS)
    return ok, info


def _wait_battle_end(win, timeout=60):  # 等待战斗结束的超时（调用点均显式传 60）
    """战斗：等待当前战斗结束（战斗操作由 battle.watch_loop 线程自动完成）。

    轮询 is_in_battle 直到战斗画面消失；超时返回失败防止卡死。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + timeout
    while is_in_battle(win):
        if time.time() > deadline:
            return False, f"等待战斗结束超时（{timeout}s）"
        time.sleep(BATTLE_END_POLL)
    return True, "战斗已结束"


def _open_task_box(win):
    """打开背包并确保任务储物格已打开。返回 (是否成功, 描述)。

    用户流程：打开背包 → 检查 box_task_selected（选中态）：存在说明
    任务储物格已在背包中打开，无需点击；否则点击 box_task 打开。
    两模板是同一按钮的两种状态、互相弱匹配，双模板比分数判定：
    各自分数达标且更高者胜（防止未选中按钮被选中态模板误判成已打开，
    以及已打开时误点未选中态反而把储物格关上）。
    每轮询只截一帧，双模板批量检测共用一帧；以 bag_box 为锚点的
    搜索条带限定匹配范围（带区全未命中才全帧兜底）。
    """
    if not bag.is_bag_open(win):
        # 高性能背包按钮缓存已下沉到 bag.open_bag（各任务模块共用）
        ok, info = bag.open_bag(win)
        if not ok:
            return False, f"打开背包失败: {info}"
    else:
        log("[修罗] 背包已打开，跳过开包", "INFO")
    # 以 bag_box（面板标志）为锚点计算任务储物格按钮的搜索条带
    # （面板可拖动，内部元素相对偏移恒定；未找到锚点则全帧匹配）
    region = None
    anchor = bag.find_bag_box(win)
    if anchor is not None:
        bx, by = anchor
        region = (bx + TASK_BOX_OFFSET[0] - TASK_BOX_BAND_MARGIN,
                  by + TASK_BOX_OFFSET[1] - TASK_BOX_BAND_MARGIN,
                  bx + TASK_BOX_OFFSET[0] + TASK_BOX_BAND_MARGIN,
                  by + TASK_BOX_OFFSET[1] + TASK_BOX_BAND_MARGIN)
    deadline = time.time() + TASK_BOX_TIMEOUT
    clicked = False
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        sel_score, _ = game_map.best_match_np(frame, BOX_TASK_SELECTED_ICON,
                                              region=region)
        task_score, task_pt = game_map.best_match_np(frame, BOX_TASK_ICON,
                                                     region=region)
        if sel_score < TASK_BOX_THRESHOLD and task_score < TASK_BOX_THRESHOLD \
                and region is not None:
            # 带区全未命中：全帧兜底（限频——仅带区未命中时触发）
            sel_score, _ = game_map.best_match_np(frame, BOX_TASK_SELECTED_ICON)
            task_score, task_pt = game_map.best_match_np(frame, BOX_TASK_ICON)
        if sel_score >= TASK_BOX_THRESHOLD and sel_score > task_score:
            return True, "任务储物格已在背包中打开"
        if task_score >= TASK_BOX_THRESHOLD:
            if not clicked:
                human_click(win, task_pt[0], task_pt[1])
                log("[修罗] 已点击任务储物格按钮，等待打开...", "SYS")
                clicked = True
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        time.sleep(RETURN_POLL_INTERVAL)
    if clicked:
        return False, "已点击任务储物格按钮，但未检测到选中态（打开失败）"
    return False, "背包中未找到任务储物格按钮"


def _find_return_talisman(win):
    """在已打开的任务储物格中查找修罗回城符，返回 (pt 或 None, 描述)。

    只识别不点击（右键使用由 _use_return_talisman 执行；验证流程
    复用本函数仅移动鼠标悬停）。模板尚未提供时直接失败并说明
    （不进入等待循环）。
    """
    try:
        has_tpl = Path(R.img(XIULUO_RETURN_ICON)).exists()
    except Exception:
        # R.img 对不存在的模板会打印 ⚠️ 警告，GBK 控制台下 print 抛
        # UnicodeEncodeError；吞掉按模板缺失处理
        has_tpl = False
    if not has_tpl:
        return None, f"修罗回城符模板未提供（待补充 {XIULUO_RETURN_ICON}）"
    deadline = time.time() + RETURN_TALISMAN_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(RETURN_POLL_INTERVAL)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, XIULUO_RETURN_ICON)
        if pt is not None:
            return pt, "已在任务储物格中找到修罗回城符"
        time.sleep(RETURN_POLL_INTERVAL)
    return None, "任务储物格中未找到修罗回城符"


def _use_return_talisman(win):
    """在已打开的任务储物格中识别修罗回城符并右键使用，返回灵兽村。

    返回 (是否成功, 描述)。未找到时下一轮领取流程会走世界地图
    寻路回灵兽村兜底。
    """
    pt, info = _find_return_talisman(win)
    if pt is None:
        return False, info
    # 保险：右键前确认背包仍打开（持背包锁下不可达，防未来新流程遗漏锁）
    if not bag.is_bag_open(win):
        return False, "右键回城符前背包已被关闭，未使用（下轮重试）"
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"[修罗] 已右键使用修罗回城符 ({pt[0]}, {pt[1]})，返回灵兽村", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, "已右键使用修罗回城符"


def _close_return_panels(win):
    """返程收尾：关闭世界地图、小地图、背包（尽力而为，失败仅记日志）。

    先截一帧批量检测三项是否全未打开（常见场景：回城符使用后界面已
    自行关闭）：全未打开直接返回，1 次截图替代 4~6 次带截图的逐项检测
    （实测逐项 ~110ms/两项 + 关包检测 ~140ms）。
    批量帧进一步分流：返程场景地图几乎恒未打开（实测每轮小地图/世界
    地图双帧确认均「未打开」空转 ~1s）——地图未打开时直接跳过
    close_maps 的双帧确认，只走关包；确实打开才走既有关闭流程
    （返程流程不使用地图，批量帧判未打开后不会瞬态再开）。
    截图失败时走完整关闭流程（各函数自带截图重试），保持原行为。
    """
    img = win.capture(force_activate=False)
    if img is None:
        game_map.close_maps(win)
        ok, info = bag.close_bag(win)
        if ok:
            log(f"[修罗] {info}", "SYS")
        else:
            log(f"[修罗] 背包关闭失败: {info}", "WARN")
        return
    frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    found = game_map.match_frame_multi(
        frame, (game_map.WORLD_MAP_OPEN_ICON,
                game_map.MINI_MAP_OPEN_ICON,
                bag.BAG_BOX_ICON))
    maps_open = found[game_map.WORLD_MAP_OPEN_ICON] is not None \
        or found[game_map.MINI_MAP_OPEN_ICON] is not None
    bag_open = found[bag.BAG_BOX_ICON] is not None
    if not maps_open and not bag_open:
        log("[修罗] 世界地图/小地图/背包均未打开，无需关闭", "INFO")
        return
    if maps_open:
        game_map.close_maps(win)
    if bag_open:
        ok, info = bag.close_bag(win)
        if ok:
            log(f"[修罗] {info}", "SYS")
        else:
            log(f"[修罗] 背包关闭失败: {info}", "WARN")


def _return_to_npc(win):
    """返程：任务完成后用修罗回城符回灵兽村，准备下一轮领取。返回 (是否成功, 描述)。

    用户流程：
    1. 战斗结束后打开背包：检查 box_task_selected（选中态），存在说明
       任务储物格已在背包中打开；否则点击 box_task 打开
    2. 识别修罗回城符 → 右键使用，返回到灵兽村
    3. 关闭世界地图、小地图、背包
    """
    # 前置：确保战斗已结束（意外遭遇战会遮挡背包按钮）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 1~3. 背包整段流程持背包锁：与其他背包流程（摄妖香等）互斥，
    # 防止交错破坏状态（实测：摄妖香关包后右键回城符点在游戏世界）
    with app_utils._BAG_LOCK:
        # 1. 打开背包并确保任务储物格已打开
        ok, info = _open_task_box(win)
        if not ok:
            return False, info
        # 2. 识别修罗回城符并右键使用（返回灵兽村）
        ok, info = _use_return_talisman(win)
        if not ok:
            # 回城符未使用：也关闭背包与地图保持界面干净；下一轮领取
            # 会走世界地图寻路回灵兽村兜底
            _close_return_panels(win)
            return False, info
        # 3. 关闭世界地图、小地图、背包（清理界面）
        _close_return_panels(win)
    # 传送后位置识别（短时等待加载完成，仅供日志与下一轮领取判断；
    # 5s 上限为实测校准值——实测加载超 3s 时 3s 时限识别失败，
    # 且失败后下一轮领取会误走世界地图寻路兜底）。0.5s 轮询：
    # 加载完成即识别，较 1.0s 最多省 0.5s 空等
    deadline = time.time() + 5
    pos = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if pos is not None:
            break
        time.sleep(0.5)
    if pos is not None:
        log(f"[修罗] 返程后位置: {pos[0]} {pos[1]},{pos[2]}", "SYS")
    else:
        log("[修罗] 返程后位置识别失败（不影响本轮返程）", "INFO")
    return True, "已返回灵兽村"


def verify_return_flow(win):
    """验证修罗回程流程（高级设置-验证按钮，主进程调用）。

    与实际返程 _return_to_npc 完全一致，唯一区别是不右键使用回城符：
    仅将鼠标移动到回城符上（不点击，无延时——时序与真实返程一致），
    然后关闭世界地图/小地图/背包。返回 (是否成功, 描述)。
    """
    # 前置：确保战斗已结束（与实际返程一致）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 1. 打开背包并确保任务储物格已打开（与实际返程一致）
    ok, info = _open_task_box(win)
    if not ok:
        return False, info
    # 2. 识别修罗回城符：仅移动鼠标定位，不点击
    pt, info = _find_return_talisman(win)
    if pt is None:
        _close_return_panels(win)
        return False, info
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    log(f"[修罗][验证] 已移动鼠标到修罗回城符 ({pt[0]}, {pt[1]})，"
        f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
    # 3. 关闭世界地图、小地图、背包
    _close_return_panels(win)
    return True, f"验证通过：回城符位于 ({pt[0]}, {pt[1]})"


def verify_claim_flow(win):
    """验证修罗领取流程（高级设置-验证按钮，主进程调用）。

    与领取流程一致，但在「点击绿色选项完成领取」前停止：走位 → 领取
    按钮识别 → 点击按钮打开对话框（领取发生在点选项之后，开对话框
    不改变任务状态）→ 识别绿色选项（完整/尾部/帮忙）→ 仅移动鼠标定位（不点击）
    仅定位不点击 → 点击屏幕中间关闭对话框。
    返回 (是否成功, 描述)。
    """
    # 前置：确保战斗已结束（与实际领取一致）
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    # 任务已在追踪时游戏不给重领（领取按钮不出现，实测 20s 空等）→ 明确失败
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        return False, "任务追踪中已有修罗任务，无法验证领取（请先完成当前任务）"
    # 1. 走位到领取点（与实际领取一致）
    ok, info = _walk_to_claim_spot(win)
    if not ok:
        return False, info
    # 2. 领取入口：鼠标定位 → 点击打开对话框；点击后对话框未出现则
    #    小地图重定位后重试（与领取流程共用同一兜底助手）
    ok, info, src2, pt2, _frame = _click_entry_ensuring_dialog(
        win, log_tag="[修罗][验证]", hover_before_click=True)
    if not ok:
        return False, info
    # 3. 识别绿色选项：仅移动鼠标悬停，不点击（点击即领取）
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if _handle_team_warning(win, items, img_np):
            return False, "队伍数量小于 3，无法领取（已关闭提示对话框）"
        found = _find_claim_option(items, img_np)
        if found is not None:
            cx, cy, txt, _ = found
            human_move(win.rect.left + cx, win.rect.top + cy)
            log(f"[修罗][验证] 已移动鼠标到绿色选项「{txt}」({cx}, {cy})，"
                f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
            # 4. 点击屏幕中间关闭对话框（不领取）
            human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
            log("[修罗][验证] 已点击屏幕中间关闭对话框（未领取）", "SYS")
            return True, f"验证通过：{src2} ({pt2[0]}, {pt2[1]})，选项「{txt}」({cx}, {cy})"
        time.sleep(0.3)
    # 对话框未出现或选项未识别：点击屏幕中间清理后失败
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    return False, "对话框选项未识别（未领取，已尝试关闭对话框）"


def _build_shout_text():
    """缺人喊话内容（用户 2026-10-14 指定格式）：

    有必须种族 → 「百次修罗来人，效率队，来男人」（多种族顿号连接）；
    无必须种族 → 「百次修罗来人，效率队，随意来人」。
    注意：不得带连续「+」号——游戏聊天拦截连续加号（实测消息不出
    现在公屏，喊话测试按钮二分定位确认）。
    """
    races = load_xiuluo_settings()["races"]
    if races:
        return f"百次修罗来人，效率队，来{'、'.join(races)}"
    return "百次修罗来人，效率队，随意来人"


def _shout_recruit(win):
    """缺人自动喊话：Enter 打开聊天输入 → 输入内容（中文自动走剪贴板
    粘贴，Keyboard.input 内置）→ Enter 发送到当前默认频道。

    与五倍 _shout_recruit 逐字节一致（验证阶段 2026-10-14：修罗
    日志有喊话但游戏无消息，代码 diff 无区别，差异在游戏侧状态）。
    """
    global _shout_last_at
    # 先激活游戏窗口：键盘事件只进前台窗口（与五倍同款修复——
    # 偶现喊话静默丢失的头号嫌疑，喊话测试按钮因带激活从不失败）
    win.activate()
    time.sleep(0.5)
    # 游戏聊天输入常驻（无需 Enter 打开）：直接输入 + Enter 发送
    Keyboard.input(_shout_text)
    time.sleep(0.8)  # 等待剪贴板粘贴完成（中文走 Ctrl+V 粘贴，需落地时间）
    Keyboard.key("enter")  # 发送
    time.sleep(0.5)
    _shout_last_at = time.time()
    log(f"[修罗] 已自动喊话: {_shout_text}", "SYS")


def _monitor_team_and_fill(win):
    """队长缺人监控循环（领取前与「战斗力异常强大」被拒后共用）。

    每轮先看申请人证据（申请面板已开，或组队按钮为 zudui_item2
    申请待处理态——有人申请时按钮变样且该态即确定性队长）：有则先
    补人（允许一名申请人后立即重查），无则查人数（最少人数按修罗
    设置）——缺人按间隔重查、人满返回（调用方随即领取）；非队长
    转保持模式（不返回）。

    先补后查的原因：按钮处于申请态时菜单打不开（点击弹的是申请面板），
    查人数走菜单路径必失败；补完申请人后按钮恢复普通态，查人数才可用。

    间隔：识别失败（OCR 噪声）3s 短重查，缺人无申请人 10s 重查。
    """
    while True:
        img = win.capture(force_activate=False)
        if img is not None and team.has_pending_apply(img):
            ok_fill, fill_info = team.fill_team(
                win, target=load_xiuluo_settings()["min_members"], max_allow=1)
            log(f"[修罗] 补人: {fill_info}", "SYS" if ok_fill else "INFO")
            if ok_fill:
                continue  # 已补一人，立即重查
        status, info = _check_team_with_retry(win)
        if status == "not_leader":
            if team.is_definitive_not_leader(info):
                _keep_alive(info)  # 无限循环，用户手动停止
            # 瞬态失败（遮挡/加载/按钮未命中等）：不退出监控，按间隔
            # 持续重查——瞬时问题不应把队长永久降级
            interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                        else TEAM_CHECK_RETRY_INTERVAL)
            log(f"[修罗] {info}（瞬态失败，{interval}s 后重查）", "WARN")
            time.sleep(interval)
            continue
        if status == "ready":
            return
        interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                    else TEAM_CHECK_RETRY_INTERVAL)
        # 缺人自动喊话（间隔限频；有申请人正在补时走上面的补人分支，
        # 不喊话）
        if _shout_text and time.time() - _shout_last_at >= SHOUT_MIN_INTERVAL:
            try:
                _shout_recruit(win)
            except Exception as e:
                log(f"[修罗] 喊话失败: {e}", "WARN")
        log(f"[修罗] {info}，暂无申请人可补，{interval}s 后重查", "WARN")
        time.sleep(interval)


def _keep_alive(info):
    """非队长：核心流程不执行，保持任务运行（战斗线程自动操作）。

    用户手动停止时任务进程被回收，无需自行退出。
    """
    log(f"[修罗] {info}，跳过核心流程，仅保持战斗线程运行", "SYS")
    while True:
        time.sleep(1)


def run_loop(win, cycle_interval=1.0):
    """修罗主循环：领任务 → 寻路 → 对话开打 → 战斗 → 返程 → 循环领取。

    领取逻辑（用户确认）：
    1. 非队长 → 核心流程不执行，任务保持运行仅战斗线程自动操作
    2. 不在领取点时先预检队伍（身份+人数，一次面板开合）：人数满足
       → 走位后直接领取（不重复开面板）；缺人 → 走位后等 10s 进入
       监控循环正常轮询；瞬态失败原地重查不走位（非队长不白走寻路）
    3. 首次领取前到达后持续监控队伍人数：满足最少人数才领取，
    不满足 10 秒重查（人留在领取点，只开队伍面板检查）
    4. 队伍检查仅首次领取前执行一次（_team_checked）：首次领取成功后
    后续轮次直接走位领取，不再开面板（每轮省 5~7s）；领取失败时
    补查一次队伍状态——非队长转保持模式，人数不足恢复领取前
    监控（等人数满足再领取），检查通过则下轮直接重试领取

    结束条件：完成任务次数达到设置的次数上限（通用设置-任务次数）
    即返回结束原因、任务随之停止（无其他结束条件）。
    返回结束原因描述。
    """
    global _fight_via_kanda, _team_checked, _task_gone_last_round, _returned_last_round, \
        _shout_text
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[修罗] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    # 高性能模式开关由 main.task_start 按通用设置置位（共享设施）
    # 检查队伍开关（通用设置-修罗设置，默认关闭）：关闭时领取前跳过
    # 全部队伍检查（非队长判定/人数监控），直接走位领取
    check_team = load_xiuluo_check_team()
    # 自动巫医（通用设置开关，默认开、每 5 轮一次）：每 N 轮领取后
    # 无条件前往巫医 NPC 医治，血蓝正常也照常（对话框处理复用战斗线程逻辑）
    auto_wuyi = load_xiuluo_auto_wuyi()
    wuyi_interval = max(1, load_xiuluo_wuyi_interval())
    log(f"[修罗] 自动巫医: {'开' if auto_wuyi else '关'}"
        + (f"（每 {wuyi_interval} 轮）" if auto_wuyi else ""), "SYS")
    log(f"[修罗] 检查队伍: {'开（领取前检查队伍）' if check_team else '关（跳过队伍检查，直接领取）'}", "SYS")
    _shout_text = _build_shout_text() if load_xiuluo_shout_switch() else ""
    if _shout_text:
        log(f"[修罗] 缺人自动喊话: 开（{_shout_text}，最短间隔 {SHOUT_MIN_INTERVAL}s）", "SYS")

    done = app_utils.task_progress_done(win, TASK_MODE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[修罗] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[修罗] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
    round_no = 0
    while True:
        round_no += 1
        log(f"[修罗] ==== 第 {round_no} 轮 ====", "SYS")
        # 1. 领任务：任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪。
        #    上一轮已确认任务完成（follow_task 以任务消失为完成判据）时，
        #    本轮开始必然无任务——has_task（区域 OCR ~0.9s）只在可能
        #    续跑时检查（进程首轮或上轮跟踪失败未确认完成）
        claim_items = None  # 领取校验帧（续跑路径无，跟踪首轮自行截图+OCR）
        if not _task_gone_last_round and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
            log("[修罗] 任务追踪中已有修罗任务，直接跟踪", "SYS")
            # 已有任务必为队长（用户 2026-10-25 确认）：置位队长记录与
            # 队伍检查标记，下一轮领取免「队长判定 + 监控补人」双检查、
            # 直接走领取；领取被拒人数不足时仍由 _claim_task 的监控补人
            # 流程兜底
            team.record_leader_status(True)
            _team_checked = True
        else:
            if _task_gone_last_round:
                _task_gone_last_round = False
                log("[修罗] 上一轮已确认任务完成，跳过任务检查，直接领取", "INFO")
            # 刚返程标记：进入领取流程即消耗复位（仅紧邻轮次生效；
            # 位置识别失败时等待传送加载完成，见 _walk_to_claim_spot）
            just_returned = _returned_last_round
            _returned_last_round = False
            # 队伍检查受「检查队伍」开关门控（默认关闭=完全跳过），且仅在
            # 首次领取前执行（_team_checked 置位后跳过）：每轮回程后开面板
            # 检查耗时 5~7s，首次领取成功后不再重复
            pre_status = None  # 1.0 预检结果（ready/wait）：走位后按结果
                               # 分支，避免监控循环重复开面板检查
            if check_team and not _team_checked:
                # 1.0 非队长判定：已在领取点附近时跳过（返程落点就在领取点，
                #    1.2 监控循环马上会检查——先查再走位会让面板连续开合两次，
                #    一次开合实测 5~7s）；不在领取点则先判定身份，非队长不白走寻路
                img0 = win.capture(force_activate=False)
                pos0 = game_map.read_map_position(win, img=img0) \
                    if img0 is not None else None
                frame0 = cv2.cvtColor(np.asarray(img0), cv2.COLOR_RGB2BGR) \
                    if img0 is not None else None
                # 按钮直达信号只在地图名读不出或为灵兽村时可信
                # （按钮模板与五倍领取按钮跨图误命中，见 _walk_to_claim_spot）
                at_spot = _near_claim_spot(pos0, CLAIM_NEAR_TOLERANCE) \
                    or (frame0 is not None
                        and (pos0 is None or pos0[0].strip() == LINGSHOUCUN)
                        and game_map.match_frame_np(frame0, LINGQU_ICON) is not None)
                if not at_spot:
                    # 瞬态失败（遮挡/按钮未命中/截图失败）原地按间隔重查、
                    # 不走位——瞬态下身份未定，走位会让非队长白走寻路
                    # （用户 2026-10-27）；真实队长瞬态恢复后原地通过检查
                    # 再走位，无进度损失。确定性非队长由 _keep_alive 处理。
                    while True:
                        status, info = _check_team_with_retry(win)
                        if status == "not_leader" \
                                and team.is_definitive_not_leader(info):
                            _keep_alive(info)
                        if status != "not_leader":
                            pre_status = status
                            break
                        interval = (TEAM_RECOG_RETRY_INTERVAL
                                    if "识别失败" in info
                                    else TEAM_CHECK_RETRY_INTERVAL)
                        log(f"[修罗] {info}（瞬态失败，{interval}s 后原地重查）", "WARN")
                        time.sleep(interval)
            # 1.1 队长：队伍是否满都先走到领取点附近
            ok, info = _walk_to_claim_spot(win, just_returned=just_returned)
            if not ok:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            if not check_team:
                # 关闭队伍检查：不开面板判定身份，任务自行寻路即视为
                # 队长（抢盒子的队长规则依赖此记录，见 battle._handle_hezi）
                team.record_leader_status(True)
            if check_team and not _team_checked:
                # 1.2 首次领取：监控循环（查人数 → 缺人补一名申请人 →
                #     立即重查；人满才继续领取；非队长转保持模式不返回）。
                #     1.0 已确认人数满足（ready）→ 直接领取不再开面板；
                #     1.0 已知缺人（wait）→ 走位后等 10s 再进监控正常轮询
                #     （面板刚开合过，立即重查是冗余的连续开合）；
                #     1.0 未检查（已在领取点）→ 照常立即监控
                if pre_status == "ready":
                    log("[修罗] 走位前队伍检查已通过（人数满足），直接领取", "SYS")
                else:
                    if pre_status == "wait":
                        time.sleep(TEAM_CHECK_RETRY_INTERVAL)
                    log("[修罗] 首次领取前进入队伍监控循环（缺人自动补）", "SYS")
                    _monitor_team_and_fill(win)
                    log("[修罗] 队伍人数满足要求，开始领取", "SYS")
            # 1.3 领取任务（校验帧随返回值带出，跟踪首轮复用免重复 OCR）；
            #     队伍不足被拒（战斗力异常强大提示）在 _claim_task 内已
            #     按开关重入监控补人循环，其余失败直接下轮重试
            ok, info, claim_items = _claim_task(win, allow_fill=check_team)
            if not ok:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[修罗] {info}", "SYS")
            team.record_leader_status(True)  # 领取成功必为队长（关闭检查队伍时抢盒子队长规则靠此记录）
            _team_checked = True  # 首次领取成功：后续轮次跳过队伍检查
            _fight_via_kanda = False  # 新一轮开始：重置「看打！」进入战斗标记
            # 自动巫医：每 N 轮领取后无条件前往巫医 NPC 医治，
            # 血蓝正常也照常（失败仅记日志，不阻断后续寻路）
            if auto_wuyi and round_no % wuyi_interval == 0:
                ok2, info2 = _auto_wuyi(win)
                log(f"[修罗][巫医] {info2}", "SYS" if ok2 else "WARN")
        # 2~4. 寻路 / 对话开打 / 战斗：由任务跟踪统一驱动
        #     （寻路点击绿字自动进行，对话开打走附加对话处理，
        #      战斗由战斗监控线程自动操作、跟踪中自动跳过）；
        #     领取校验帧传入后首轮跳过截图+OCR 立即点击绿字
        ok, info = _follow_to_done(win, first_items=claim_items)
        if not ok:
            log(f"[修罗] {info}", "WARN")
            time.sleep(cycle_interval)
            continue
        _task_gone_last_round = True  # 任务消失已确认：下一轮跳过 has_task 检查
        # 5. 返程：仅「看打！」进入的战斗结束后才返程（背包任务储物格
        #    右键修罗回城符回灵兽村），其他方式进入的战斗不自动返程
        if _fight_via_kanda:
            ok, info = _return_to_npc(win)
            if not ok:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            _returned_last_round = True  # 下一轮领取：位置失败等加载（见 _walk_to_claim_spot）
        done += 1
        app_utils.task_progress_add(win, TASK_MODE, done)
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        log(f"[修罗] 本轮修罗任务完成（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
        # 成功路径不加轮次间隔：返程流程结尾已有 5s 位置识别等待，
        # 游戏状态已稳定，下一轮直接读位置领取即可（间隔仅保留在
        # 失败重试路径，防快速失败模式紧循环空转）
