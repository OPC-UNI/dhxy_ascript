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
       （被拒→补人→重领全链路时序详见 docs/xiuluo-team-fill-flow.md）
   1.4 领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）
2. 寻路：点击任务追踪绿字自动寻路（复用 workflows.follow.follow_task）
3. 对话开打：寻路到达后出现对话面板 → 点击绿色选项「看打！」进入战斗
   （点击成功标记本轮战斗由「看打！」进入）
4. 战斗：战斗由 battle.watch_loop 线程自动操作；follow_task 战斗中自动跳过识别
   战斗失败（用户 2026-11-14）：战斗结束任务仍在追踪、或战死落轮回司
   → 队长回灵兽村（轮回司世界地图不可用，走修罗回城符）→ 点「我想
   取消任务」→ 6s 后重新领取（重新领取前强制队伍检查：暂离/组号队员
   归队/超时请离/补人）；非队长不取消/重领，转保持模式并自动归队
5. 返程：战斗结束后用修罗回城符回灵兽村（仅「看打！」进入的战斗才返程，
   其他方式进入的战斗不自动返程）：返程前 has_task 复查任务确已消失
   （单轮消失确认的兜底）→ 打开背包 → 打开任务储物格
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

import cv2
import numpy as np
from PIL import Image  # 对话框区域 2x 放大重识别（看打选项 OCR 漏检兜底）

from ..core.app_utils import human_click, human_move, precise_click, log, move_away, norm_text
from ..core import app_utils  # 高性能模式共享设施（固定位置缓存点击）
from ..device import task_tracker  # 任务追踪面板操作
from ..device import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from ..device import team  # 组队检查（领取前队伍人数检查，仅队长生效）
from ..core import debug_overlay  # 调试信息悬浮层（任务进度更新）
from ..device import status_bars  # 血蓝检测（自动巫医前置条件）
from ..device.battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from ..device.battle import WUYI_DIALOG_TEXTS  # 巫医对话框文案（自动巫医等待判定）
from ..device.battle import find_wuyi_option  # 「全部医治和修复」选项定位（验证模式自行点击）
from ..device import battle  # 巫医预期时间戳（点 NPC 后通知战斗线程切入 2s 高频检查）
from ..device import repair  # 自动修理装备（洛阳城，共享工具模块）
from ..core.settings import (load_task_counts, load_xiuluo_settings,
                             load_xiuluo_shout_switch,
                             load_xiuluo_shout_text,
                             load_xiuluo_check_team,
                             load_xiuluo_auto_create_team,
                             load_xiuluo_auto_rejoin,
                             load_xiuluo_auto_kick_away,
                             load_xiuluo_kick_away_minutes,
                             load_xiuluo_auto_wuyi,
                             load_xiuluo_wuyi_interval,
                             load_xiuluo_auto_repair,
                             load_xiuluo_repair_interval,
                             load_xiuluo_tab_group)  # 任务次数上限/修罗设置/自动巫医/自动修理/自动归队/暂离请离/组号开关
from ..workflows.team_flow import TeamFlow  # 组队流程编排（检查/建队/补人/喊话/归队）
from ..workflows import return_flow  # 返程流程编排（开箱/回程道具/清屏/归位）
from ..workflows import claim_flow  # 领取共享流程（选项/地图门控/校验探针）
from ..device import tab_group  # 组号模式（一窗口多 Tab）：初始化/队员 pass/抑制标志
from ..device.dialog_common import (_dialog_text_present, _norm_dialog_present,
                            _click_green_option_once, _fuzzy_contains,
                            SCREEN_CENTER_POS,
                            dialog_frame_score,
                            DIALOG_FRAME_THRESHOLD)  # 对话框共享组件

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
CLAIM_OPTION_NEEDLES = (norm_text(CLAIM_DIALOG_OPTION),
                        norm_text(CLAIM_OPTION_TAIL),
                        norm_text("帮忙"))  # 完整→尾部→兜底（供 claim_flow）
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
TASK_BLOCK_REGION = (0, 225, 400, 310)        # 任务块裁剪区域（面板顶部：标题行 y~230-255
                                              # + 内容链接行 y~260-295，留余量）；2x 放大
                                              # 重识别用（修罗标题徽标漏检 + 插入滑动动画
                                              # 期间 det 失败——放大帧检出能力更强）。
                                              # 静态兜底；运行时由标题条模板动态推导
                                              # （见 _task_block_region：内容区顶-2、高 85）
CLAIM_FOLLOW_RECOVER_WINDOW = 30              # 领取后首次跟踪失败的补偿窗口（秒）：
                                              # 首次点击及其重试实测 9s、慢 OCR 下 ~20s；
                                              # 窗口外的同类失败视为跟踪中期，不补偿

# ---- 取消任务（用户 2026-11-14 指定：流程与领取一致——先点领取入口
#      模板打开对话框，取消时改点绿色选项「我想取消任务」） ----
CANCEL_DIALOG_OPTION = "我想取消任务"          # 取消任务对话框绿色选项（用户给出原文）
CANCEL_OPTION_TAIL = "取消任务"                # 选项换行渲染时的尾部匹配串（同领取：
                                              # 完整文案被 OCR 拆行时尾部可命中）
CANCEL_OPTION_NEEDLES = (norm_text(CANCEL_DIALOG_OPTION),
                         norm_text(CANCEL_OPTION_TAIL))  # 完整→尾部。杜绝裸「取消」
                                              # 兜底：取消确认弹窗的「取消」是放弃取消，
                                              # 裸关键词会把确认点反
CANCEL_VERIFY_WAIT = 10                       # 取消后等待任务从追踪面板消失的轮询时长
CANCEL_VERIFY_POLL_INTERVAL = 0.8             # 消失确认轮询间隔（同领取校验 0.8s）
CANCEL_VERIFY_GONE_POLLS = 2                  # 连续「无任务」确认轮数（防面板重绘
                                              # 瞬时漏检；与 follow 任务消失确认同思路）

# ---- 战斗失败恢复（用户 2026-11-14 指定）：作为队长，任务战斗失败 →
#      回灵兽村取消任务 → 6s 后重新领取；人数不足走既有检查暂离/补人流程 ----
CANCEL_RE_CLAIM_DELAY = 6                     # 取消任务后重新领取的等待（秒，用户指定）
_lunhuisi_retry_count = 3                     # 战斗失败后回灵兽村的重试次数（回城符
                                              # 打开任务储物格偶发失败，2026-11-11 日志
                                              # 死亡后首次打开失败、稍后成功）

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
WUYI_HANDLE_TIMEOUT = 12                      # 点击 NPC 后等待战斗线程处理对话框的超时
                                              # （用户 2026-10-28 指定 8s；2026-11-04 实测
                                              # 整链 = 对话框动画 ~2s + 2s 节奏相位空检 ~2s +
                                              # 医治动画 ~2s + 检测 ~1s，相位最差轮次恰好顶满
                                              # 8s 零余量——放宽到 12s 覆盖连续两次相位空检；
                                              # 超时后自行点击选项兜底，见 _run_wuyi_chain）
WUYI_BLOCK_DIALOG_TEXT = "阻止它为非作歹"     # 领取任务后弹出的遮挡对话框文案：面板遮挡巫医
                                              # NPC 模板（2026-10-31 用户反馈），点击屏幕中间
                                              # 取消后模板即恢复可见——仅巫医回合处理（领取后
                                              # 检查见 run_loop，到达窗口兜底见 _find_wuyi_npc）
WUYI_BLOCK_TIMEOUT = 3                       # 领取完成后等待该对话框出现的窗口：窗口内出现即
                                              # 取消；未出现直接去巫医（对话框可能走近巫医时
                                              # 才弹出，由 _find_wuyi_npc 到达窗口兜底）
CLAIM_VERIFY_WAIT = 5                         # 领取后任务进入追踪的正常轮询时长（秒）：
                                              # 超时未检索到 → 小地图重走领取点后仅再检索一次，
                                              # 仍无即判定失败（用户确认：多次重试无效）
CLAIM_VERIFY_WAIT_STALE = 20                  # 领取前有旧任务遗留面板时的轮询时长：
                                              # 旧任务未完成走回重领时面板刷新延迟实测约
                                              # 5-9s（2026-11-04 五倍日志），须等新任务签名
                                              # 变化才确认，正常 5s 窗口不够

# ---- 领取前队伍检查 ----
TEAM_CHECK_RETRY_INTERVAL = 10  # 缺人且无申请人可补时的重查间隔（秒）
TEAM_RECOG_RETRY_INTERVAL = 3   # 成员识别失败（OCR 噪声）时的重试间隔（秒）：
                                # 识别失败≠人数不足，等满 10s 纯属空转
SHOUT_MIN_INTERVAL = 30         # 缺人自动喊话最短间隔（秒，防刷屏）
_shout_text = ""                # 本轮喊话内容（设置内容，{种族}已替换；空=不喊话）
_team_flow = TeamFlow(
    prefix="[修罗]",
    min_members=lambda: load_xiuluo_settings()["min_members"],
    auto_rejoin=load_xiuluo_auto_rejoin, shout_text=lambda: _shout_text,
    check_retry_interval=TEAM_CHECK_RETRY_INTERVAL,
    recog_retry_interval=TEAM_RECOG_RETRY_INTERVAL,
    shout_min_interval=SHOUT_MIN_INTERVAL,
    # 暂离超时自动请离（修罗设置开关 + 分钟数，默认开/5 分钟）：
    # 队伍检查时传入 team.check_team_min；关闭时只停请离，组号暂离
    # 队员归队 pass 仍照常执行
    kick_overdue=load_xiuluo_auto_kick_away,
    kick_timeout=lambda: load_xiuluo_kick_away_minutes() * 60)

# ---- 对话开打（寻路到达后出现对话面板） ----
FIGHT_DIALOG_OPTION = "看打！"  # 对话面板绿色选项（点击进入战斗，游戏内原文）
_fight_via_kanda = False        # 本轮战斗是否由「看打！」进入（决定战后是否返程）
_battle_failed_reason = None    # 战斗失败原因（None=未失败；"task"=战后任务仍在
                                # 追踪；"dead"=战后落轮回司）：follow 的
                                # battle_end_handler 置位、主循环消费后走
                                # 队长取消重领 / 非队长归队恢复（用户 2026-11-14）
_battle_recovery_pending = False  # 战斗失败恢复未完成（回灵兽村/取消任务失败）：
                                # 下一轮开始先重试恢复，不得回到任务跟踪——
                                # 旧任务仍在追踪，直接继续跟踪会反复打同一
                                # 任务（用户 2026-11-14 疑问的缺口）
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

# ---- 返程（任务储物格 → 修罗回城符右键 → 回灵兽村；共享实现在
#      workflows/return_flow.py） ----
XIULUO_RETURN_ICON = "xiuluo_huichengfu.png"      # 修罗回城符模板（用户实测素材）
_return_flow = return_flow.ReturnFlow(
    prefix="[修罗]", item_icon=XIULUO_RETURN_ICON,
    item_label="修罗回城符", target_label="灵兽村",
    success_info="已返回灵兽村",
    open_box=lambda win: _open_task_box(win),
    open_box_retries=1, read_position=True, pos_poll_interval=0.5,
    close_optimized=True, close_failure_detail=True,
    bag_closed_label="回城符")

# 领取后对话框「今日已完成次数：N」的完成次数解析（OCR 可能把冒号误读，兼容）
_CLAIM_COUNT_RE = re.compile(re.escape(CLAIM_COUNT_TEXT) + r"\s*[:：]?\s*(\d+)")


def _near_wuyi_spot(pos):
    """判断位置是否在巫医 NPC (115,72) 附近（严格容差 ±5）。

    小距离移动场景：附近定义必须严格，宽松容差会漏判偏移（用户确认）。
    """
    return game_map.is_at_position(pos, LINGSHOUCUN,
                                  WUYI_SPOT[0], WUYI_SPOT[1],
                                  WUYI_NEAR_TOLERANCE)


def _dismiss_block_dialog_on_items(win, items, tag="[修罗][巫医]"):
    """已 OCR 行上命中「阻止它为非作歹」即点击屏幕中间取消（免重截+重 OCR）。

    领取校验的确认帧已含该对话框（2026-11-10 实测 3/3 轮）：直接复用其
    OCR 结果取消，省一次截图+模板门+对话区 OCR（实测 0.8~1.2s）。命中
    即点击一次并返回 True；未命中返回 False。重复点击防重由调用方控制
    （连续点屏幕中间会误点游戏世界引起走位）。
    """
    if not _dialog_text_present(items, WUYI_BLOCK_DIALOG_TEXT):
        return False
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    move_away(win)
    log(f"{tag} 检测到「阻止它为非作歹」对话框，已点击屏幕中间取消", "SYS")
    return True


def _dismiss_block_dialog_on_frame(win, img, tag="[修罗][巫医]"):
    """单帧检测「阻止它为非作歹」对话框并点击屏幕中间取消。

    巫医回合（遮挡 NPC）与领取后正常回合（收尾对话常驻屏幕）共用。
    命中即点击一次并返回 True；未命中/战斗帧返回 False。重复点击防重
    由调用方控制（连续点屏幕中间会误点游戏世界引起走位）。
    """
    if is_in_battle_frame(img):
        return False
    items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    return _dismiss_block_dialog_on_items(win, items, tag=tag)


def _dismiss_wuyi_block_dialog(win, items=None):
    """领取任务完成后取消「阻止它为非作歹」对话框（期望流程 2026-10-31）。

    领取完成 → 取消对话框 → 去巫医 → 识别模板 → 点击 → 全部医治。
    窗口内出现即取消一次；未出现直接去巫医（对话框可能走近巫医时
    才弹出，由 _find_wuyi_npc 到达窗口兜底）。返回是否已取消。
    items：领取校验确认帧的 OCR 行——已含该对话框（实测 3/3 轮）时
    直接取消（免一次截图+OCR）；未命中回落窗口轮询。
    """
    if items is not None and _dismiss_block_dialog_on_items(win, items):
        return True
    deadline = time.time() + WUYI_BLOCK_TIMEOUT
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if _dismiss_block_dialog_on_frame(win, img):
            return True
        time.sleep(0.5)
    return False


def _dismiss_block_dialog_after_claim(win, items=None, timeout=2.0):
    """领取成功后取消 NPC 收尾对话「…阻止它为非作歹」（正常回合）。

    正常回合原先不取消该对话框：领取确认后它持续挂在屏幕中央（实测
    12s+ 仍在，人物走远才自动关闭），期间 follow 轮询每 0.5s 对它做
    对话区 OCR、战斗线程巫医检查（战后 15s 窗口 2s 节奏）再 OCR 一遍、
    看打兜底每 5s 一次 2x 放大 OCR（故事行门控触发、静默失败）——
    领取完成后约 10s 的 CPU 峰值主要来自这里（2026-11-09 py-spy 实测）。
    巫医回合由 _dismiss_wuyi_block_dialog 处理，本函数只覆盖正常回合。

    items：领取校验确认帧的 OCR 行——实测 3/3 轮已含该对话框
    （2026-11-10），直接复用取消（免一次截图+模板门+对话区 OCR
    0.8~1.2s）；未命中回落窗口轮询。窗口 2s（对话在领取确认后 ~1s 内
    出现），每轮先对话框模板门（~9ms）：模板未命中即无对话，跳过 OCR。
    文案命中才点屏幕中间（防点空误触世界走位）。返回是否已取消。
    """
    # 观测（[领取计时]，2026-11-10 临时埋点）：取消耗时与轮询数，
    # 验证同帧取消效果后连同 _claim_task 内埋点一起删除
    t0 = time.time()
    if items is not None and _dismiss_block_dialog_on_items(win, items,
                                                            tag="[修罗]"):
        log(f"[修罗][领取计时] 阻止对话取消 @+{time.time() - t0:.2f}s"
            f"（确认帧复用）", "INFO")
        return True
    polls = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        polls += 1
        if is_in_battle_frame(img):
            time.sleep(0.5)
            continue
        if dialog_frame_score(img) < DIALOG_FRAME_THRESHOLD:
            time.sleep(0.5)
            continue
        if _dismiss_block_dialog_on_frame(win, img, tag="[修罗]"):
            log(f"[修罗][领取计时] 阻止对话取消 @+{time.time() - t0:.2f}s"
                f"（轮询 {polls}）", "INFO")
            return True
        time.sleep(0.5)
    log(f"[修罗][领取计时] {timeout:.1f}s 窗口内未见阻止对话（轮询 {polls}）",
        "INFO")
    return False


def _find_wuyi_npc(win):
    """到达后识别巫医 NPC 模板（3s 窗口，0.5s 轮询，每轮询只截一帧）。

    未识别到即本次取消（用户 2026-10-09 确认：3s 含识别，最长 3s）。
    遮挡对话框兜底（仅巫医回合，正常回合不涉及）：「阻止它为非作歹」
    对话框可能在走近巫医时才弹出（2026-10-31 用户反馈，领取后检查
    可能扑空），遮挡 NPC 模板时点击屏幕中间取消、窗口顺延 2s——
    识别到点一次（防重复点击误点世界走位）。
    返回 (pt 或 None, 失败描述)。
    """
    pt = None
    deadline = time.time() + WUYI_NPC_TIMEOUT
    dismissed = False
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pt = game_map.match_frame_np(frame, WUYI_NPC_ICON)
        if pt is not None:
            break
        if not dismissed and _dismiss_block_dialog_on_frame(win, img):
            dismissed = True
            deadline += 2.0
        time.sleep(0.5)
    if pt is None:
        return None, f"到达后 {WUYI_NPC_TIMEOUT}s 内未识别到巫医 NPC（本次取消）"
    return pt, ""


def _run_wuyi_chain(win, hover_before_click=False, report_condition=False,
                    click_option_self=False, silent=False):
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
       真实流程：轮询 battle.last_wuyi_option_at 检测战斗线程已点击
       「全部医治和修复」（battle.py 已有完整逻辑——选项位置固定走
       缓存直点），点击后随机 1~1.2s 即返回继续寻路，**不等待对话框
       关闭**（医治动画 ~2s 不阻塞寻路，用户 2026-11-04 确认）；
       点击 NPC 后设置 battle.wuyi_expected_at 通知战斗线程切入 2s
       高频检查（10s 空闲节奏会错过等待上限，被迫兜底自点，
       2026-10-29 日志教训）；
       click_option_self=True（验证模式）：主进程没有战斗线程，验证
       自行点击医治选项（复用 battle.find_wuyi_option，与战斗线程
       行为等效——不宜把常驻 battle 线程拉进主进程，避免与后续任务
       的战斗线程抢点击）；
       hover_before_click=True 时 NPC 点击前先移动鼠标定位（无延时，验证时序与真实流程一致）

    到达/点击的最终真信号是对话框出现：坐标 OCR 不可靠（实测走完
    站在 NPC 旁位置显示缺失，坐标判定卡死到达循环），NPC 模板远距离
    也会命中（使者教训）。单次尝试（用户 2026-10-09 确认）：找不到
    NPC/对话框未出现即本次取消，下次轮到再试——失败不阻塞修罗主流程。
    silent=True（静默巫医验证）：调用方已开 app_utils.silent_clicks()
    ——全部点击走消息通道；位置读取失败跳过 Alt+C 恢复（键盘事件
    只进前台窗口，静默模式游戏可能不在前台）。返回 (是否成功, 描述)。
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
    if pos is None and not silent:
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
    #    未识别到即本次取消（用户 2026-10-09 确认：3s 含识别，最长 3s）。
    #    遮挡对话框处理见 _find_wuyi_npc（仅巫医回合）
    pt, npc_info = _find_wuyi_npc(win)
    if pt is None:
        return False, npc_info
    if hover_before_click:
        human_move(win.rect.left + pt[0], win.rect.top + pt[1])
        log(f"[修罗][验证] 已移动鼠标到巫医 NPC ({pt[0]}, {pt[1]})，"
            f"已定位到（验证时序与真实流程一致）", "SYS")
    human_click(win, pt[0], pt[1])
    # 通知战斗线程：巫医对话框即将出现，切入 2s 高频检查（10s 空闲节奏
    # 相位不巧时会错过 8s 等待上限，被迫兜底自点，2026-10-29 日志教训）
    npc_click_ts = time.time()
    battle.wuyi_expected_at = npc_click_ts
    log(f"[修罗][巫医] 已点击巫医 NPC ({pt[0]}, {pt[1]})，等待医治对话框处理", "SYS")
    deadline = time.time() + WUYI_HANDLE_TIMEOUT
    if click_option_self:
        # 验证模式：主进程无战斗线程——OCR 检测对话框并自行点击，
        # 等待对话框关闭确认整链（与真实环境行为等效）
        appeared = False
        option_clicked = False
        last_items = None   # 对话框打开期间的最近 OCR（超时自行点击兜底用）
        last_img_np = None
        last_text = None    # 命中文案（分键缓存用，见 battle.record_wuyi_option_pos）
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.5)
                continue
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            # 与 battle.py 的巫医判定同款调用（内部模糊匹配，无需预规范化）
            matched_text = next((t for t in WUYI_DIALOG_TEXTS
                                 if _dialog_text_present(items, t)), None)
            if matched_text is not None:
                appeared = True
                last_items, last_img_np, last_text = items, img_np, matched_text
                if not option_clicked:
                    pt_opt = find_wuyi_option(items, img_np)
                    if pt_opt is not None:
                        human_click(win, pt_opt[0], pt_opt[1])
                        log(f"[修罗][验证] 已点击「全部医治和修复」"
                            f"@ ({pt_opt[0]}, {pt_opt[1]})", "SYS")
                        battle.record_wuyi_option_pos(pt_opt, matched_text)  # 组号队员 pass 直点复用
                        move_away(win)  # 悬停会让选项变色，干扰绿字判定
                        option_clicked = True
                time.sleep(1.0)
                continue
            if appeared:
                log("[修罗][巫医] 医治对话框已处理完毕", "SYS")
                return True, "自动巫医完成"
            time.sleep(0.5)
        if appeared and not option_clicked and last_items is not None:
            pt_opt = find_wuyi_option(last_items, last_img_np)
            if pt_opt is not None:
                human_click(win, pt_opt[0], pt_opt[1])
                log(f"[修罗][验证] 等待超时，自行点击「全部医治和修复」"
                    f"@ ({pt_opt[0]}, {pt_opt[1]})", "WARN")
                battle.record_wuyi_option_pos(pt_opt, last_text)  # 组号队员 pass 直点复用
                move_away(win)  # 悬停会让选项变色，干扰绿字判定
                time.sleep(1.0)  # 等待点击生效
                return True, "医治对话框超时未关闭，已自行点击选项"
        return (True, "医治对话框已出现（继续处理）") if appeared \
            else (False, "点击巫医后未出现医治对话框（本次取消）")
    # 真实流程：战斗线程在预期窗口内 2s 高频检查并点击选项。轮询点击
    # 时间戳（内存读，零截图零 OCR）——战斗线程点击后随机 1~1.2s 让
    # 点击生效即返回，不等待对话框关闭（医治动画 ~2s 不阻塞寻路，
    # 用户 2026-11-04 确认「点击全部医治后立即寻路」）
    while time.time() < deadline:
        if battle.last_wuyi_option_at >= npc_click_ts:
            time.sleep(random.uniform(1.0, 1.2))
            return True, "自动巫医完成"
        time.sleep(0.5)
    # 超时：战斗线程未点击——OCR 判定对话框是否出现（最多 2 次，1s 间隔）
    items = None
    matched_text = None
    for _ in range(2):
        img = win.capture(force_activate=False)
        if img is not None:
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            matched_text = next((t for t in WUYI_DIALOG_TEXTS
                                 if _dialog_text_present(items, t)), None)
            if matched_text is not None:
                break
        time.sleep(1.0)
    if matched_text is None:
        # 对话框未出现（距离不够）：本次取消，下次轮到再试
        return False, "点击巫医后未出现医治对话框（本次取消）"
    # 对话框在场但战斗线程没点上：兜底自行点击——优先绿字校验过的
    # 本弹窗分键缓存直点（不同弹窗选项位置不同，见 battle._wuyi_cached_pt）
    pt_opt = battle._wuyi_cached_pt(img_np, matched_text)
    if pt_opt is None and items:
        pt_opt = find_wuyi_option(items, img_np)
    if pt_opt is None:
        return True, "医治对话框已出现（战斗线程将继续处理）"
    human_click(win, pt_opt[0], pt_opt[1])
    log(f"[修罗][巫医] 等待超时，自行点击「全部医治和修复」"
        f"@ ({pt_opt[0]}, {pt_opt[1]})", "WARN")
    battle.record_wuyi_option_pos(pt_opt, matched_text)  # 组号队员 pass 直点复用 + 分键缓存
    move_away(win)  # 悬停会让选项变色，干扰绿字判定
    time.sleep(random.uniform(1.0, 1.2))
    return True, "医治对话框超时未关闭，已自行点击选项"


def _auto_wuyi(win):
    """自动巫医（用户流程，通用设置开关+间隔门控）：每 N 轮领取后
    无条件前往巫医医治（血蓝正常也照常，见 _run_wuyi_chain）。

    组号模式：队长医治成功后遍历队员 Tab 点击其自动弹出的巫医
    对话框（队员不点 NPC、不检查血蓝、不重点任务追踪）。
    """
    ok, info = _run_wuyi_chain(win)
    if ok and tab_group.active():
        tab_group.member_wuyi_pass(win)
    return ok, info


def verify_wuyi_flow(win, silent=False):
    """验证自动巫医流程（高级设置-验证按钮，主进程调用）。

    与 _auto_wuyi 同一链路，区别：血蓝状态检查并仅报告（不影响执行），
    NPC 点击前先移动鼠标定位（无延时），并自行点击医治选项
    （主进程没有战斗线程——真实环境中该点击由任务进程的战斗线程
    完成，二者行为等效）。silent=True（静默修罗巫医按钮）：调用方
    已开 app_utils.silent_clicks()——全程消息点击、不移动鼠标、
    不激活窗口，悬停定位步骤跳过（无物理鼠标可定位）。
    返回 (是否成功, 描述)。
    """
    return _run_wuyi_chain(win, hover_before_click=not silent, report_condition=True,
                           click_option_self=True, silent=silent)


def verify_tab_wuyi_flow(win):
    """验证组号巫医（高级设置-验证按钮，主进程调用，不改变任务状态）。

    与 verify_wuyi_flow 同一队长链路（自行点击医治选项，主进程无
    战斗线程），队长医治完成后追加组号队员 pass：逐个切换队员 Tab
    点击其自动弹出的医治对话框，最后切回队长。前置：窗口内 ≥2 个
    已登录 Tab（开关状态不校验——验证不受开关影响，仅统计 Tab 数）。
    返回 (是否成功, 描述)。
    """
    ok, info = _run_wuyi_chain(win, hover_before_click=True, report_condition=True,
                               click_option_self=True)
    if not ok:
        return False, info
    # 强制枚举 Tab（主进程无任务模式，active() 不可用；验证用
    # ready() + member_wuyi_pass(force=True) 绕过模式校验）
    tab_group.init_task(win, TASK_MODE, True)
    if not tab_group.ready():
        return False, f"{info}；组号模式未激活（需窗口内 ≥2 个已登录 Tab），队员 pass 未执行"
    tab_group.member_wuyi_pass(win, force=True)
    return True, f"{info}；队员巫医 pass 已执行"


def verify_repair_flow(win):
    """验证自动修理流程（诊断-验证修理，主进程调用，不改变任务状态）。"""
    return repair.verify_repair_flow(win, prefix="修罗")


def verify_tab_repair_flow(win):
    """验证组号修理（诊断-验证组号修理，主进程调用，不改变任务状态）。"""
    return repair.verify_tab_repair_flow(win, TASK_MODE, prefix="修罗")


def _near_claim_spot(pos, tolerance):
    """领取点附近判定（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.near_claim_spot(pos, LINGSHOUCUN, CLAIM_SPOT, tolerance)


def _in_claim_map(pos):
    """领取地图门控（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.in_claim_map(pos, LINGSHOUCUN)


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


def _find_claim_option(items, img_np, needles=CLAIM_OPTION_NEEDLES):
    """查找领取/取消绿色选项（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.find_claim_option(items, img_np, needles)


def _task_block_region(img_np):
    """任务块裁剪区域（动态）：内容区顶-2、高 85，横向取内容区左右缘。

    与静态 TASK_BLOCK_REGION=(0,225,400,310) 同标定（内容区顶≈227），
    面板漂移时跟随；标题条模板未命中返回 None（调用方回退静态）。
    实现下沉到 task_tracker.task_block_region（五倍领取探针共用）。
    """
    return task_tracker.task_block_region(img_np)


def _task_in_tracker_zoom(img):
    """任务块裁剪 2x 放大重识别（面板 OCR 漏检兜底，看打选项同款）。

    修罗标题行带彩色徽标被区域 OCR 确定性漏检、内容链接行在面板
    插入滑动动画期间 det 偶发失败——裁剪任务块（标题+链接行区域）
    2x 放大后全帧引擎重识别，检出能力更强。返回是否检出任务。
    """
    try:
        region = _task_block_region(np.asarray(img)) or TASK_BLOCK_REGION
        l, t, r, b = region
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


def _probe_task_block(win):
    """任务块探针（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.probe_task_block(
        win, TASK_TITLE, TASK_FALLBACK_TEXTS,
        lambda img_np: _task_block_region(img_np) or TASK_BLOCK_REGION)


def _claim_verify_wait(win, pre_sig, t_opt, polls):
    """领取校验等待（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.claim_verify_wait(
        win, pre_sig, t_opt, polls, prefix="[修罗]",
        interval=claim_flow.CLAIM_VERIFY_POLL_INTERVAL,
        probe_fn=_probe_task_block)


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
    """点击领取绿色选项（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.click_claim_option(
        win, items, img_np, needles=CLAIM_OPTION_NEEDLES,
        prefix="[修罗]", cache_key="claim_option")


def _click_cancel_option(win, items, img_np):
    """点击取消任务绿色选项「我想取消任务」（同领取选项流程）。

    高性能缓存键独立（cancel_option）：取消选项与领取选项在对话框
    中的位置不同，共键会把领取位置当取消位置点。
    """
    return claim_flow.click_claim_option(
        win, items, img_np, needles=CANCEL_OPTION_NEEDLES,
        prefix="[修罗]", cache_key="cancel_option")


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
    # （2026-10-23 五倍反向实测）；长安等地图的五环「任务」领取横幅
    # 同样命中（2026-11-11 用户实机）。坐标误读场景（111,90→11,90/
    # 102,79/18,14）地图名仍对，按钮信号继续兜底；地图名读不出时不
    # 采信按钮（无法确认在灵兽村）
    pos = game_map.read_map_position(win, img=img)
    if btn_visible and _in_claim_map(pos):
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
    # （坐标误读时按钮判据兜底，实测站在领取点空等 30s+ 的根因）。
    # 按钮判据须地图名门控（_in_claim_map）：按钮模板在其他地图同样
    # 命中，跨图误判到达会点开错误对话框（2026-11-11 用户实机长安
    # 步行途中）。每轮询只截一帧：战斗判定/双模板匹配/位置 OCR 同帧复用
    deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pos = game_map.read_map_position(win, img=img)  # 同帧复用
        if _in_claim_map(pos) \
                and game_map.match_frame_np(frame, LINGQU_ICON) is not None:
            log("[修罗] 已到达领取点附近（领取按钮可见）", "SYS")
            return True, "已到达领取点附近"
        if _near_shizhe_spot(pos) \
                and game_map.match_frame_np(frame, LINGSHOUCUN_SHIZHE_ICON) is not None:
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
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        if is_in_battle_frame(img):
            time.sleep(2.0)
            continue
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        pos = game_map.read_map_position(win, img=img)  # 同帧复用
        # 按钮判据同 _walk_to_claim_spot：须地图名门控（跨图误命中）
        if _in_claim_map(pos) \
                and game_map.match_frame_np(frame, LINGQU_ICON) is not None:
            return True, "已重走到领取点附近（领取按钮可见）"
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            return True, "已重走到领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[修罗] 重走领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, "重走领取点超时"


def _click_entry_ensuring_dialog(win, log_tag="[修罗]", hover_before_click=False,
                                 option_needles=CLAIM_OPTION_NEEDLES,
                                 option_cache_key="claim_option",
                                 action="领取"):
    """点击领取入口（按钮或使者）并确保对话框出现；未出现判定坐标不对：
    打开小地图点击领取点坐标重定位后重试一次（用户确认的兜底路径）。

    hover_before_click：点击前先移动鼠标定位入口（无延时，验证时序与真实流程一致）。
    option_needles / option_cache_key / action：对话框选项匹配串、高性能
    缓存键与动作名（领取默认；取消任务传 CANCEL_OPTION_NEEDLES /
    "cancel_option" / "取消任务"，对话框出现判定按取消选项）。
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
        log(f"{log_tag} 已点击{src2}（{action}）", "SYS")
        # 高性能：选项坐标已缓存 → 固定等待渲染后由调用方
        # 直接按缓存坐标点选项（点不中由后续校验循环识别补点兜底，
        # 无 seen 帧返回）
        if app_utils.high_perf_on() \
                and app_utils.fixed_pos_get(option_cache_key, win) is not None:
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
            if _find_claim_option(items, img_np, option_needles) is not None:
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
            # 使者模板远距离也会命中，不能作到达判据）。按钮判据须
            # 地图名门控（跨图误命中，同 _walk_to_claim_spot）
            deadline = time.time() + CLAIM_ARRIVE_TIMEOUT
            while time.time() < deadline:
                img = win.capture(force_activate=False)
                if img is None:
                    time.sleep(2.0)
                    continue
                if is_in_battle_frame(img):
                    time.sleep(2.0)
                    continue
                pos = game_map.read_map_position(win, img=img)
                if _in_claim_map(pos) and game_map.match_frame_np(
                        cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR),
                        LINGQU_ICON) is not None:
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
    （省 ~1.2s，点击任务追踪绿字立即进行）；小地图重走兜底路径无帧返回 None。

    领取时序优化（实测 09:05 一轮：入口点击→选项点击 4s、选项点击→
    已领取判定 4s、判定→追踪点击 2s）：
    1. 选项点击直接复用「对话框已出现」的同一帧（免重截+重 OCR）
    2. 完成次数解析并入校验循环的同一批双区域 OCR 帧（原独立 1.2s
       窗口从未命中，每轮空转）
    3. 校验循环内确认新任务渲染即返回（领取前有旧任务遗留面板时须
       签名变化才确认），不再重复一次 has_task 全量检查
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取", None
    # 领取前面板签名：旧任务未完成遗留面板时，领取后面板刷新有延迟
    # ——校验须等新任务渲染（签名变化）才确认，防首轮点击追踪点到旧
    # 任务绿字、组号 pass 期间面板刷新被误判为任务进展（2026-11-04
    # 五倍日志实测，修罗同款机制）
    pre_sig = None
    img0 = win.capture(force_activate=False)
    if img0 is not None:
        items0, _ = task_tracker.ocr_track(img0)
        pre_sig = task_tracker.task_signature_in(items0, TASK_TITLE)
    if pre_sig is not None:
        # 追踪面板仍有修罗任务（误判消失走了领取、任务又出现，或旧任务
        # 遗留未清）：游戏不会给重复领取（领取入口不出现），继续走领取
        # 只会空等入口/重走领取点一整轮——直接返回让主循环下一轮
        # _task_state 续跑跟踪（2026-11-11 用户确认场景：任务又出现时
        # 应继续寻路，不要空耗领取流程）
        return False, "任务追踪中仍有修罗任务，跳过领取（续跑跟踪）", None
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
    # 观测（[领取计时]，2026-11-10 临时埋点）：选项点击时刻，用于度量任务
    # 渲染延迟与确认帧是否已含「阻止它为非作歹」；数据收集完成后移除
    t_opt = None
    # 高性能：入口点击后未 OCR（seen 为 None）→ 按缓存坐标直点，
    # 生效与否由「任务进追踪」校验循环判定并识别补点兜底
    used_cached = False
    if seen is None:
        t_opt = time.time()
        used_cached = app_utils.high_perf_click(win, "claim_option",
                                                "领取选项", prefix="[修罗] ")
    if not used_cached:
        claimed = False
        if seen is not None:
            items, img_np = seen
            claimed = _click_claim_option(win, items, img_np)
            if claimed:
                t_opt = time.time()
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
            if claimed:
                t_opt = time.time()  # 观测：识别路径点击生效时刻
            polls += 1
            if not claimed and polls % 4 == 0 \
                    and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
                claimed = True
            time.sleep(0.3)
        if not claimed:
            return False, "领取对话框处理失败", None
    # 校验任务出现在追踪面板（正常轮询 CLAIM_VERIFY_WAIT 秒；旧任务
    # 遗留面板时须签名变化、窗口延长到 CLAIM_VERIFY_WAIT_STALE）与
    # 「今日已完成次数：N」解析共用同一批双区域 OCR 帧：新任务渲染
    # 即确认（不再重复一次 has_task 全量检查）；期间处理队伍不足提示
    # 对话框（点击选项后队伍掉到 3 人以下时出现）
    deadline = time.time() + (CLAIM_VERIFY_WAIT_STALE if pre_sig is not None
                              else CLAIM_VERIFY_WAIT)
    count = None
    found_frame = None
    polls = 0
    reclick_poll = 0  # 识别补点发生的轮询序（补点后队伍对话框需再覆盖一轮）
    option_seen = False  # 校验期是否曾见领取选项（区分未渲染与点击已生效）
    if t_opt is None:
        t_opt = time.time()  # 未点中选项的兜底路径（has_task 判定）：以校验起点计时
    t_confirm = None       # 观测：确认帧截帧时刻（相对 t_opt 度量渲染延迟）
    confirm_polls = 0
    confirm_dialog_ocr = False  # 观测：确认帧是否做了对话区 OCR
    confirm_block = False       # 观测：确认帧是否已读到「阻止它为非作歹」
    block_first_at = None       # 观测：校验期首次读到该对话框的时刻
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
        t_cap = time.time()  # 观测：本帧截取时刻（任务渲染延迟度量）
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
        # 观测：「阻止它为非作歹」首次出现时刻（仅对话区有 OCR 的轮询可查）
        if need_dialog and block_first_at is None \
                and _dialog_text_present(items, WUYI_BLOCK_DIALOG_TEXT):
            block_first_at = time.time()
        if task_tracker.has_new_task_in(items, img_np, TASK_TITLE,
                                        TASK_FALLBACK_TEXTS, pre_sig):
            found_frame = (items, img_np)
            t_confirm = t_cap
            confirm_polls = polls
            confirm_dialog_ocr = need_dialog
            confirm_block = need_dialog and block_first_at is not None
            break
        if not need_dialog:
            _claim_verify_wait(win, pre_sig, t_opt, polls)
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
        # 弹窗响应。
        # 门控（2026-11-09）：面板区已读到任务文案（标题或链接兜底行）
        # 时不放大——能走到这里说明 has_new_task_in 未确认，即旧任务
        # 签名未变化（残留面板）或标题漏检；前者 2x 只会把旧任务再读
        # 一遍、甚至绕过签名变化保护误确认，后者由跟随流程自愈，均无
        # 收益。只有面板 OCR 完全漏检（标题与链接行都没读到）才需要
        # 2x 补读——该路径依赖全帧引擎 'min'/736 放大，单次成本高
        if polls >= 2 \
                and not task_tracker.has_task_in(
                    items, img_np, TASK_TITLE, TASK_FALLBACK_TEXTS) \
                and _task_in_tracker_zoom(img):
            log("[修罗] 任务块 2x 重识别检出任务", "SYS")
            # 重截一帧补全双区域 OCR：found_frame 需完整帧供 follow
            # 首轮复用（面板绿字点击与对话框处理依赖）
            img2 = win.capture(force_activate=False)
            if img2 is not None:
                t_cap2 = time.time()  # 观测：2x 路径确认帧截取时刻
                items, img_np = task_tracker.ocr_follow(img2)
                found_frame = (items, img_np)
                if block_first_at is None \
                        and _dialog_text_present(items, WUYI_BLOCK_DIALOG_TEXT):
                    block_first_at = time.time()
                t_confirm = t_cap2
                confirm_polls = polls
                confirm_dialog_ocr = True  # ocr_follow 为双区域 OCR
                confirm_block = block_first_at is not None
                break
        _claim_verify_wait(win, pre_sig, t_opt, polls)
    if found_frame is not None:
        # 观测（[领取计时]，临时埋点）：确认帧截帧延迟 = 任务渲染延迟上界；
        # 「确认帧含阻止对话」= 同帧取消优化可行；首见时刻用于判断能否在
        # 校验期提前取消。数据收集完成后删除
        if t_confirm is not None:
            block_txt = "未查" if not confirm_dialog_ocr \
                else ("是" if confirm_block else "否")
            first_txt = f"{block_first_at - t_opt:+.2f}s" \
                if block_first_at is not None else "未见解"
            log(f"[修罗][领取计时] 确认帧 @选项点击后 {t_confirm - t_opt:.2f}s"
                f"（轮询 {confirm_polls}，确认总耗时 {time.time() - t_opt:.2f}s，"
                f"确认帧含阻止对话={block_txt}，阻止对话首见={first_txt}）",
                "INFO")
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}", found_frame
    # 未检索到：小地图重走领取点坐标后再次检索（用户 2026-10-09
    # 确认：重走可能触发任务渲染，不直接判失败）。原 Alt+C 飞行重查
    # 已移除——多余逻辑
    ok2, info2 = _relocate_to_claim_spot(win)
    if not ok2:
        log(f"[修罗] {info2}", "WARN")
    elif task_tracker.has_new_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS, pre_sig):
        log("[修罗] 重走领取点后新任务已出现在追踪面板", "SYS")
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
    return False, "已点击领取选项，但任务追踪中未检测到任务（重走领取点后仍无）", None


def _task_absent_in_frame(img):
    """单帧判定追踪面板已无修罗任务（共享实现在 task_tracker）。"""
    return task_tracker.task_absent_in_frame(
        img, TASK_TITLE, TASK_FALLBACK_TEXTS)


def _cancel_task(win):
    """取消当前修罗任务（战斗失败恢复 _recover_battle_failure 调用）。

    用户指定（2026-11-14）：与领取流程一致——先走到灵兽村领取点、
    点击领取入口模板（领取任务按钮/灵兽村使者）打开对话框，区别是
    取消时点绿色选项「我想取消任务」，最后校验任务从追踪面板消失。

    前置：任务追踪中已有修罗任务（无任务时领取入口不出现，无取消
    对象，且防止把其他对话框选项当取消选项点掉）；战斗中跳过。
    返回 (是否成功, 描述)；「无需取消」表示任务已不存在。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过取消任务"
    if not task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        return False, "任务追踪中无修罗任务，无需取消"
    # 1. 走位到领取点（与领取流程一致）
    ok, info = _walk_to_claim_spot(win)
    if not ok:
        return False, info
    # 2. 点击领取入口模板打开对话框：对话框出现判定改用取消选项
    #    （有任务时领取选项不出现，入口通常是灵兽村使者 NPC 模板）
    ok, info, _src, _pt, seen = _click_entry_ensuring_dialog(
        win, option_needles=CANCEL_OPTION_NEEDLES,
        option_cache_key="cancel_option", action="取消任务")
    if not ok:
        return False, info
    # 3. 点「我想取消任务」：优先复用对话框出现帧；高性能缓存路径
    #    （seen=None）按缓存坐标直点；未点中走短窗轮询兜底
    clicked = False
    if seen is None:
        clicked = app_utils.high_perf_click(win, "cancel_option",
                                            "取消任务选项", prefix="[修罗] ")
    if not clicked and seen is not None:
        clicked = _click_cancel_option(win, seen[0], seen[1])
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while not clicked and time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        clicked = _click_cancel_option(win, items, img_np)
        if not clicked:
            time.sleep(0.3)
    if not clicked:
        return False, f"取消对话框未识别到绿色选项「{CANCEL_DIALOG_OPTION}」"
    # 4. 校验任务从追踪面板消失：连续 CANCEL_VERIFY_GONE_POLLS 轮确认
    #    （单轮漏检可能是面板重绘/滑动动画；战斗帧不构成消失证据）。
    #    成功后任务已不存在——调用方下一轮按其常规「无任务」路径处理
    deadline = time.time() + CANCEL_VERIFY_WAIT
    gone_polls = 0
    last_lines = ""
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        if is_in_battle_frame(img):
            gone_polls = 0
            time.sleep(0.5)
            continue
        absent, items = _task_absent_in_frame(img)
        if items:
            last_lines = " / ".join(tx for tx, _, s in items[:6] if s >= 0.55)
        gone_polls = gone_polls + 1 if absent else 0
        if gone_polls >= CANCEL_VERIFY_GONE_POLLS:
            log("[修罗] 已取消修罗任务（追踪面板已无任务）", "SYS")
            return True, "已取消修罗任务"
        time.sleep(CANCEL_VERIFY_POLL_INTERVAL)
    # 失败排查：读一次对话区文案（如出现取消确认弹窗），只记录不点击
    try:
        img = win.capture(force_activate=False)
        if img is not None:
            ditem, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            lines = [tx for tx, _, s in ditem if s >= 0.55]
            if lines:
                log(f"[修罗] 取消后对话区文案: {' / '.join(lines[:8])}", "WARN")
    except Exception:
        pass
    detail = f"（面板残留: {last_lines}）" if last_lines else ""
    return False, f"已点击「{CANCEL_DIALOG_OPTION}」，但任务仍在追踪面板{detail}"


def _check_team_ready(win, create_if_missing=False):
    """领取前队伍检查（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.check_team_ready(win, create_if_missing)


def _check_team_with_retry(win, tries=3, create_if_missing=False):
    """队伍检查带瞬态失败重试（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.check_team_with_retry(win, tries, create_if_missing)


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


def _on_battle_end(win, img=None):
    """战斗结束回调（follow_task 注入）：判定任务战斗是否失败。

    用户 2026-11-14 定义的两种失败（任一）：
    1. 战斗结束后任务仍在追踪面板；
    2. 战斗结束后落在轮回司（死亡复活点）。
    条件 2 不限进入战斗的来源（随机遭遇战死亡同样需要恢复）；条件 1
    仅在确由「看打！」进入战斗（_fight_via_kanda）时判定——随机遭遇
    战结束后任务当然还在，不能当失败。

    调用点先于任务消失确认：死亡落轮回司时追踪面板可能被游戏收起，
    「面板消失=任务结束」会把战败误判成完成（2026-11-11 日志实测）。
    位置在战斗结束首帧可能尚未渲染，短窗补读 2 次（约 1s）。
    返回 True 表示已判定失败（_battle_failed_reason 记录原因），follow
    立即退出跟踪，由主循环走取消任务/归队恢复流程。
    """
    global _battle_failed_reason
    if img is None:
        img = win.capture(force_activate=False)
    pos = game_map.read_map_position(win, img=img) if img is not None else None
    for _ in range(2):
        if pos is not None:
            break
        time.sleep(0.5)
        img2 = win.capture(force_activate=False)
        if img2 is None:
            continue
        img = img2
        pos = game_map.read_map_position(win, img=img)
    if pos is not None and team.is_in_lunhuisi(pos):
        _battle_failed_reason = "dead"
        log(f"[修罗] 战斗结束落点在轮回司 "
            f"（{pos[0]}[{pos[1]},{pos[2]}]），判定战斗失败", "WARN")
        return True
    if not _fight_via_kanda or img is None:
        return False
    absent, items = _task_absent_in_frame(img)
    if items is not None and not absent:
        # 战斗结束首帧任务行可能仍在退出动画中：短窗复核一次，仍在才
        # 判失败（防把即将消失的完成态误判成战败）
        time.sleep(0.5)
        img2 = win.capture(force_activate=False)
        absent2, items2 = _task_absent_in_frame(img2) \
            if img2 is not None else (absent, items)
        if items2 is not None and not absent2:
            _battle_failed_reason = "task"
            log("[修罗] 战斗结束但任务仍在追踪面板，判定战斗失败", "WARN")
            return True
    return False


def _follow_to_done(win, first_items=None):
    """跟踪任务直到完成（共享实现在 workflows/claim_flow.py）。

    task_gone_polls=1：修罗每轮均为单杀任务（任务消失即完成），消失
    确认从默认 2 轮降到 1 轮（对齐五倍非黄袍怪轮次，返程交接省 ~0.5s
    ——多一轮 = 0.5s 轮询间隔 + 条带 OCR）。单轮证据可能被 OCR 偶发
    漏读/面板瞬时遮挡污染，由主循环返程前 has_task 全区域复查兜底
    （_task_still_tracked_before_return），两者不得同时放宽。

    battle_end_handler=_on_battle_end：战斗失败判定（用户 2026-11-14）。
    _fight_via_kanda 在跟踪起点复位，只反映本轮「看打！」进入的战斗，
    防上一轮残留值把随机遭遇战当成任务战斗（误取消任务）。
    """
    global _fight_via_kanda, _battle_failed_reason
    _fight_via_kanda = False
    _battle_failed_reason = None
    return claim_flow.follow_to_done(
        win, first_items, title=TASK_TITLE, prefix="修罗",
        extra_dialog_handler=_handle_fight_dialog,
        fallback_keywords=TASK_FALLBACK_TEXTS,
        post_track_handler=_maybe_tab_bars_pass,
        task_gone_polls=1,
        battle_extras_tpl_gate=True, recovery=_walk_to_claim_spot,
        recovery_window=CLAIM_FOLLOW_RECOVER_WINDOW,
        battle_end_handler=_on_battle_end)


def _task_still_tracked_before_return(win):
    """返程前复查任务追踪是否仍在（兜住 follow 单轮消失确认的误判）。

    follow 本轮只做 1 轮消失确认（见 _follow_to_done）：单轮证据可能
    被 OCR 偶发漏读/面板瞬时遮挡污染，误判完成会提前返程丢任务。
    返程前用全区域 has_task 复查一次（fast_absent：标题条模板未命中
    且无遮挡面板时跳过区域 OCR，面板缺席场景实测 ~0.22s → ~0.12s
    （裸进程）；模板命中时照常 OCR，暖机 ~0.18s，任务进程内 OCR 线程
    争用按 2~3 倍估算），任务仍在 → 跳过返程、恢复跟踪。修罗每轮均为
    单杀任务，无双杀瞬时为空场景，不需要五倍黄袍怪式的 4s 窗口。
    """
    return task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS,
                                 fast_absent=True)


def _recover_battle_failure(win, check_team):
    """任务战斗失败恢复（用户 2026-11-14 指定流程）。

    1. 身份检查（检查队伍开启时）：非队长不取消/重领——转保持模式 +
       自动归队（死亡落轮回司点「归队」回队长身边）。
    2. 回灵兽村：落轮回司时世界地图不可用（2026-11-11 日志实测），
       走修罗回城符返程；其他地图走地图寻路，寻路失败再用回城符。
    3. 取消任务：追踪中仍有任务则点「我想取消任务」；任务已消失
       （战斗胜利后死亡等）按本轮完成处理。
    4. 取消成功 → 等待 CANCEL_RE_CLAIM_DELAY(6s)，下一轮重新领取；
       队伍检查标记复位，重新领取前走检查暂离/组号队员归队/补人流程。
    返回 ("canceled"|"completed"|"failed", 描述)。
    """
    global _team_checked, _task_gone_last_round, _returned_last_round, \
        _battle_recovery_pending
    # 1. 身份：非队长走归队保持模式（队员不能取消/领取队伍任务）
    if check_team:
        status, info = _check_team_with_retry(win)
        if status == "not_leader":
            if team.is_definitive_not_leader(info):
                log(f"[修罗] 战斗失败，非队长：转保持模式并自动归队（{info}）",
                    "WARN")
                _keep_alive(win, info)  # 保持模式无限循环，用户手动停止
                return "failed", f"保持模式异常退出（{info}）"
            _battle_recovery_pending = True
            return "failed", f"战斗失败后队伍身份无法判定（{info}）"
    # 2. 回灵兽村
    img = win.capture(force_activate=False)
    pos = game_map.read_map_position(win, img=img) if img is not None else None
    at_lingshou = pos is not None and pos[0].strip() == LINGSHOUCUN
    last_info = ""
    if not at_lingshou:
        for attempt in range(_lunhuisi_retry_count):
            if team.is_in_lunhuisi(pos):
                # 轮回司世界地图不可用：修罗回城符是自动返程通道
                ok, last_info = _return_to_npc(win)
            else:
                ok, last_info = _walk_to_claim_spot(win, just_returned=False)
                if not ok:
                    log(f"[修罗] 战斗失败后寻路未成功（{last_info}），"
                        f"改用修罗回城符返程", "WARN")
                    ok, last_info = _return_to_npc(win)
            if ok:
                break
            log(f"[修罗] 战斗失败后回灵兽村失败（第 {attempt + 1}/"
                f"{_lunhuisi_retry_count} 次）：{last_info}", "WARN")
            time.sleep(3.0)
            img = win.capture(force_activate=False)
            pos = game_map.read_map_position(win, img=img) \
                if img is not None else None
        else:
            _battle_recovery_pending = True
            return "failed", f"战斗失败后回灵兽村失败: {last_info}"
        _returned_last_round = True
    # 3. 取消任务（任务已消失 = 本轮其实已完成）
    ok, info = _cancel_task(win)
    if ok:
        _battle_recovery_pending = False
        _team_checked = False  # 重新领取前强制队伍检查（暂离/组号归队/补人）
        time.sleep(CANCEL_RE_CLAIM_DELAY)
        _task_gone_last_round = True
        return "canceled", (f"战斗失败：{info}，"
                            f"{CANCEL_RE_CLAIM_DELAY}s 后重新领取")
    if "无需取消" in info:
        _battle_recovery_pending = False
        _task_gone_last_round = True
        return "completed", f"战斗失败但任务已不在追踪（{info}），按本轮完成处理"
    _battle_recovery_pending = True
    return "failed", f"战斗失败后取消任务失败: {info}"


def _maybe_tab_bars_pass(win):
    """组号模式队员血蓝 pass（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.maybe_tab_bars_pass(win)


def _wait_battle_end(win, timeout=60):
    """等待战斗结束（共享实现在 workflows/return_flow.py）。"""
    return return_flow.wait_battle_end(win, timeout)


def _open_task_box(win):
    """打开背包并确保任务储物格已打开（共享实现在 workflows/return_flow.py）。"""
    return return_flow.open_task_box(win, "[修罗]")


def _find_return_talisman(win):
    """查找修罗回城符（共享实现在 workflows/return_flow.py）。"""
    return return_flow.find_return_item(
        win, XIULUO_RETURN_ICON, "修罗回城符")


def _use_return_talisman(win):
    """右键使用修罗回城符（共享实现在 workflows/return_flow.py）。"""
    return return_flow.use_return_item(
        win, XIULUO_RETURN_ICON, "修罗回城符", "灵兽村", "[修罗]",
        bag_closed_label="回城符")


def _close_return_panels(win):
    """返程收尾关闭面板（共享实现在 workflows/return_flow.py）。"""
    return return_flow.close_return_panels(
        win, "[修罗]", optimized=True,
        failure_detail=True)


def _return_to_npc(win):
    """返程流程（共享实现在 workflows/return_flow.py）。"""
    return _return_flow.run(win)


def verify_return_flow(win, silent=False):
    """验证修罗回程流程（高级设置-验证按钮，主进程调用）。

    与实际返程 _return_to_npc 完全一致，唯一区别是不右键使用回城符：
    仅将鼠标移动到回城符上（不点击，无延时——时序与真实返程一致），
    然后关闭世界地图/小地图/背包。
    silent=True（静默修罗回程按钮）：调用方已开 app_utils.silent_clicks()
    ——全程消息点击、不移动鼠标、不激活窗口，「仅移动鼠标定位」步骤
    改为仅日志定位（无物理鼠标可定位）。返回 (是否成功, 描述)。
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
    if silent:
        log(f"[修罗][验证] 已识别修罗回城符 ({pt[0]}, {pt[1]})"
            f"（静默模式，不移动鼠标）", "SYS")
    else:
        human_move(win.rect.left + pt[0], win.rect.top + pt[1])
        log(f"[修罗][验证] 已移动鼠标到修罗回城符 ({pt[0]}, {pt[1]})，"
            f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
    # 3. 关闭世界地图、小地图、背包
    _close_return_panels(win)
    return True, f"验证通过：回城符位于 ({pt[0]}, {pt[1]})"


def verify_claim_flow(win, silent=False):
    """验证修罗领取流程（高级设置-验证按钮，主进程调用）。

    与领取流程一致，但在「点击绿色选项完成领取」前停止：走位 → 领取
    按钮识别 → 点击按钮打开对话框（领取发生在点选项之后，开对话框
    不改变任务状态）→ 识别绿色选项（完整/尾部/帮忙）→ 仅移动鼠标定位（不点击）
    仅定位不点击 → 点击屏幕中间关闭对话框。
    silent=True（静默修罗领取按钮）：调用方已开 app_utils.silent_clicks()
    ——全程消息点击、不移动鼠标、不激活窗口，「仅移动鼠标定位」步骤
    改为仅日志定位（无物理鼠标可定位）。返回 (是否成功, 描述)。
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
        win, log_tag="[修罗][验证]", hover_before_click=not silent)
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
            if silent:
                log(f"[修罗][验证] 已识别绿色选项「{txt}」({cx}, {cy})"
                    f"（静默模式，不移动鼠标）", "SYS")
            else:
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


def verify_cancel_flow(win, silent=False):
    """验证取消任务流程（底层验证函数，暂未接入高级设置按钮）。

    与取消流程一致，但在点「我想取消任务」前停止：走位 → 点击领取
    入口模板打开对话框（点入口不改变任务状态）→ 识别取消绿色选项
    「我想取消任务」→ 仅移动鼠标定位（不点击）→ 点击屏幕中间关闭
    对话框。真正的取消由 _cancel_task 执行。
    前置：任务追踪中已有修罗任务（取消选项仅在有任务时出现）。
    silent=True：调用方已开 app_utils.silent_clicks()（不移动鼠标）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        ok, info = _wait_battle_end(win, timeout=60)
        if not ok:
            return False, info
    if not task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        return False, "任务追踪中无修罗任务，无法验证取消"
    # 1. 走位到领取点（与实际取消一致）
    ok, info = _walk_to_claim_spot(win)
    if not ok:
        return False, info
    # 2. 领取入口：点击打开对话框（对话框出现判定按取消选项）
    ok, info, src2, pt2, frame = _click_entry_ensuring_dialog(
        win, log_tag="[修罗][验证]", hover_before_click=not silent,
        option_needles=CANCEL_OPTION_NEEDLES,
        option_cache_key="cancel_option", action="取消任务")
    if not ok:
        return False, info
    # 3. 识别取消绿色选项：仅移动鼠标悬停，不点击（点击即取消任务）
    found = _find_claim_option(frame[0], frame[1], CANCEL_OPTION_NEEDLES) \
        if frame is not None else None
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    while found is None and time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        found = _find_claim_option(items, img_np, CANCEL_OPTION_NEEDLES)
        if found is None:
            time.sleep(0.3)
    if found is not None:
        cx, cy, txt, _ = found
        if silent:
            log(f"[修罗][验证] 已识别取消选项「{txt}」({cx}, {cy})"
                f"（静默模式，不移动鼠标）", "SYS")
        else:
            human_move(win.rect.left + cx, win.rect.top + cy)
            log(f"[修罗][验证] 已移动鼠标到取消选项「{txt}」({cx}, {cy})，"
                f"已定位到（不点击，验证时序与真实流程一致）", "SYS")
        # 4. 点击屏幕中间关闭对话框（不取消任务）
        human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
        log("[修罗][验证] 已点击屏幕中间关闭对话框（未取消任务）", "SYS")
        return True, f"验证通过：{src2} ({pt2[0]}, {pt2[1]})，选项「{txt}」({cx}, {cy})"
    # 选项未识别：点击屏幕中间清理后失败
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    return False, "取消对话框选项未识别（任务未取消，已尝试关闭对话框）"


def _build_shout_text(template=None):
    """缺人喊话内容：读取修罗设置里的喊话内容（默认
    「百次修罗来人，效率队，来{种族}」），把 {种族} 占位符替换为
    必须种族（顿号连接；无必须种族时替换为「随意来人」）。

    模板例：必须种族 男人+男鬼 → 「百次修罗来人，效率队，来男人、男鬼」；
    无必须种族 → 「百次修罗来人，效率队，来随意来人」。
    注意：不得带连续「+」号——游戏聊天拦截连续加号（实测消息不出
    现在公屏，喊话测试按钮二分定位确认）。
    """
    if template is None:
        template = load_xiuluo_shout_text()
    races = load_xiuluo_settings()["races"]
    filled = "、".join(races) if races else "随意来人"
    # 兼容中文输入法打出的全角大括号「｛种族｝」
    return (template.replace("{种族}", filled)
            .replace("｛种族｝", filled))


def _shout_recruit(win):
    """缺人自动喊话（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.shout_recruit(win)


def _monitor_team_and_fill(win, create_if_missing=False):
    """队长缺人监控循环（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.monitor_team_and_fill(win, create_if_missing)


def _keep_alive(win, info):
    """非队长保持 + 自动归队（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.keep_alive(win, info)


def _auto_create_team_once(win):
    """自动创建队伍一次（共享实现在 workflows/team_flow.py）。"""
    return _team_flow.auto_create_team_once(win)


def _task_state(win):
    """续跑状态判定（共享实现在 workflows/claim_flow.py）。"""
    return claim_flow.task_state(win, TASK_TITLE, TASK_FALLBACK_TEXTS)


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
    global _fight_via_kanda, _battle_failed_reason, _battle_recovery_pending, \
        _team_checked, _task_gone_last_round, _returned_last_round, _shout_text
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[修罗] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    # 高性能模式开关由 main.task_start 按通用设置置位（共享设施）
    # 检查队伍开关（通用设置-修罗设置，默认开启）：关闭时领取前跳过
    # 全部队伍检查（非队长判定/人数监控），直接走位领取
    check_team = load_xiuluo_check_team()
    # 自动巫医（通用设置开关，默认开、每 5 轮一次）：每 N 轮领取后
    # 无条件前往巫医 NPC 医治，血蓝正常也照常（对话框处理复用战斗线程逻辑）
    auto_wuyi = load_xiuluo_auto_wuyi()
    wuyi_interval = max(1, load_xiuluo_wuyi_interval())
    log(f"[修罗] 自动巫医: {'开' if auto_wuyi else '关'}"
        + (f"（每 {wuyi_interval} 轮）" if auto_wuyi else ""), "SYS")
    # 自动修理装备（通用设置开关，默认开、每 30 场战斗一次）：每轮领取后
    # 按今日累计战斗场次判断，满足即前往洛阳城修理（repair.py）
    auto_repair = load_xiuluo_auto_repair()
    repair_interval = max(1, load_xiuluo_repair_interval())
    log(f"[修罗] 自动修理装备: {'开' if auto_repair else '关'}"
        + (f"（每 {repair_interval} 场战斗）" if auto_repair else ""), "SYS")
    log(f"[修罗] 检查队伍: {'开（领取前检查队伍）' if check_team else '关（跳过队伍检查，直接领取）'}", "SYS")
    auto_kick = load_xiuluo_auto_kick_away()
    auto_kick_txt = (f"开（{load_xiuluo_kick_away_minutes()} 分钟未归队请离）"
                     if auto_kick else "关（不自动请离，组号队员归队照常）")
    log(f"[修罗] 暂离超时自动请离: {auto_kick_txt}", "SYS")
    _shout_text = _build_shout_text() if load_xiuluo_shout_switch() else ""
    if _shout_text:
        log(f"[修罗] 缺人自动喊话: 开（{_shout_text}，最短间隔 {SHOUT_MIN_INTERVAL}s）", "SYS")
    # 自动创建队伍（修罗设置开关，默认开启）：任务开始时若无队伍自动
    # 创建（组队按钮 → 菜单「创建队伍」），创建成功后走正常队伍检查与
    # 补人流程；已有队伍时本开关无其他逻辑。战斗中不操作（组队菜单会
    # 遮挡战斗操作），顺延到战斗结束后的首次检查。
    # 合并执行（2026-11-11 组号逻辑优化）：开启「检查队伍」时不再单独
    # 开一次菜单查「是否已组队」——自动创建并入首次队伍面板打开（组号
    # 走 startup_check，单号走首次领取前队伍检查），同一次菜单打开完成
    # 「有无队伍 / 开面板 / 缺队伍则创建」，省重复点击组队按钮；仅
    # 「检查队伍」关闭（没有可合并的检查）时才单独执行。
    auto_create = load_xiuluo_auto_create_team()
    log(f"[修罗] 自动创建队伍: {'开（无队伍时任务开始自动创建）' if auto_create else '关'}", "SYS")
    # 申请待处理态清场（2026-11-07 用户结论，日志 22:03 教训）：启动时
    # 组队按钮为 zudui_item2（有人申请）即确定性队长，但申请态下组队
    # 菜单打不开、自动创建与组号启动检查都必失败；先点按钮开面板 →
    # 清空申请列表 → 关面板，再走正常队伍查询（缺人由监控循环喊话等
    # 新申请）。战斗中按钮不可点：顺延到主循环首个非战斗轮次（一次即消费）
    pending_apply_clear = True
    if not is_in_battle(win):
        pending_apply_clear = False
        hit, apply_clear_info = team.clear_pending_apply(win)
        if hit:
            log(f"[修罗] 启动清场: {apply_clear_info}", "SYS")
    else:
        log("[修罗] 战斗中，申请待处理态清场顺延至战斗结束", "INFO")
    pending_create = auto_create
    create_defer_logged = False
    if pending_create and not check_team:
        # 检查队伍关闭：没有可合并的队伍检查，自动创建单独执行（原链路）
        if not is_in_battle(win):
            pending_create = False
            _auto_create_team_once(win)
        else:
            create_defer_logged = True
            log("[修罗] 战斗中，自动创建队伍顺延至战斗结束", "INFO")

    done = app_utils.task_progress_done(win, TASK_MODE)  # 当天已完成（跨重启持久）
    if done:
        log(f"[修罗] 恢复当天进度: 已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[修罗] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
    # 组号专用（高级设置开关，默认开启）：一窗口多 Tab 多账号时激活，
    # 队长带队、队员按账号配置挂战斗/巫医/补血蓝。激活时要求「检查
    # 队伍」开关必须开启（队员身份靠队伍面板匹配，用户 2026-11-07
    # 指定）；队长判定后检查队员组号战斗动作设置，缺失即停止任务
    # 提示先设置。已登录 Tab <2（单号）不激活，行为与旧版完全一致。
    tab_group.init_task(win, TASK_MODE, load_xiuluo_tab_group())
    group_leader_confirmed = False  # 组号启动检查已确认队长：首次领取前不再
                                    # 为身份判定开面板（2026-11-11）
    group_team_count = None  # 组号启动检查面板昵称人数下限（None=未读到）：
                             # 达到修罗设置人数时首次领取前免队伍检查
                             # （2026-11-11 组号逻辑优化）
    group_min_members = None
    if tab_group.active():
        if not check_team:
            log("[修罗] 组号模式要求开启「检查队伍」开关（修罗设置），停止任务，请开启后重新启动", "ERROR")
            return "组号模式：请先在修罗设置开启「检查队伍」开关后重新启动"
        # 启动检查一次开面板：队长判定 + 成员昵称读取（旧链路开 3 次）；
        # 自动创建队伍并入同一次菜单打开（未组队时直接点创建）
        ok_leader, leader_info, panel_names = tab_group.startup_check(
            win, create_if_missing=pending_create)
        pending_create = False  # 自动创建已在启动检查内消费（成败均不再单独创建）
        if not ok_leader:
            if "未组队" in leader_info:
                # 确定性未组队（启动前解散队伍等）：组号降级关闭（隐藏
                # 组号入口），按普通非队长模式继续运行——任务循环
                # check_team_min → 保持模式（2026-11-07 用户反馈：
                # 未组队不应停止任务）
                tab_group.deactivate_group(reason=leader_info)
                log(f"[修罗] {leader_info}，组号降级为普通非队长模式继续运行", "WARN")
            else:
                log(f"[修罗] {leader_info}，停止任务（组号模式要求队长带队，请在队长 Tab 下启动）", "ERROR")
                return f"组号模式：{leader_info}，请在队长 Tab 下启动任务"
        else:
            missing, member_info = tab_group.startup_member_check(win, panel_names)
            if missing:
                names = "、".join(f"{m['name']}({m['id']})" for m in missing)
                log(f"[修罗] 组号队员 {names} 未设置战斗动作，停止任务"
                    f"（请在「组号」设置面板设置后重新启动）", "ERROR")
                return f"组号模式：请在「组号」设置面板为 {names} 设置战斗动作后重新启动"
            log(f"[修罗] 组号启动检查: {member_info}", "SYS")
            # 启动检查已确认队长：首次领取前的队伍检查用本快照替代，
            # 抢盒子的队长规则依赖此记录（跳过检查时无 check_team_min）
            team.record_leader_status(True)
            group_leader_confirmed = True
            group_team_count = tab_group.startup_team_count(panel_names)
            group_min_members = load_xiuluo_settings()["min_members"]
            if group_team_count is not None:
                log(f"[修罗] 组号启动检查：队伍面板可读成员 {group_team_count} 人"
                    f"（人数下限，满 {group_min_members} 人免首次领取前队伍检查）",
                    "INFO")
            acts = tab_group.leader_battle_actions()
            log(f"[修罗] 组号队长战斗动作: 人物 {acts['char']}，召唤兽 {acts['beast']}"
                + ("（本账号组号设置）" if tab_group.load_group_actions().get(tab_group.leader_id())
                   else "（通用战斗设置）"), "SYS")
    round_no = 0
    while True:
        round_no += 1
        log(f"[修罗] ==== 第 {round_no} 轮 ====", "SYS")
        # 组号护栏：pass 切回失败残留时先回队长 Tab 再走本轮流程
        if tab_group.active() and not tab_group.ensure_leader_tab(win):
            time.sleep(cycle_interval)
            continue
        # 申请待处理态清场顺延（启动时战斗中）：战斗结束后的首个非战斗
        # 轮次执行，一次即消费；先清场后创建——清场后按钮恢复普通态，
        # 创建流程的菜单才能打开
        if pending_apply_clear and not is_in_battle(win):
            pending_apply_clear = False
            hit, apply_clear_info = team.clear_pending_apply(win)
            if hit:
                log(f"[修罗] 启动清场: {apply_clear_info}", "SYS")
        # 自动创建队伍顺延（启动时战斗中、检查队伍关闭）：战斗结束后的
        # 首个非战斗轮次单独执行，一次即消费；开启检查队伍时由首次队伍
        # 检查合并执行（见领取前队伍检查），这里不开菜单
        if pending_create and not check_team and not is_in_battle(win):
            pending_create = False
            _auto_create_team_once(win)
        elif pending_create and not check_team and not create_defer_logged:
            create_defer_logged = True
            log("[修罗] 战斗中，自动创建队伍顺延至战斗结束", "INFO")
        # 0. 上一轮战斗失败恢复未完成（回灵兽村/取消任务失败）：本轮先
        #    重试恢复，不得回到任务跟踪——旧任务仍在追踪，直接继续跟踪
        #    会反复打同一任务（用户 2026-11-14 疑问的缺口）。战斗中不
        #    操作（队伍面板/取消对话框都会干扰战斗），顺延到战斗结束
        if _battle_recovery_pending and not is_in_battle(win):
            debug_overlay.set_phase("战斗失败恢复")  # 看板阶段
            status, rinfo = _recover_battle_failure(win, check_team)
            if status == "canceled":
                _battle_recovery_pending = False
                log(f"[修罗] {rinfo}", "SYS")
                time.sleep(cycle_interval)
                continue
            if status == "completed":
                _battle_recovery_pending = False
                log(f"[修罗] {rinfo}", "SYS")
                # 任务已消失：继续本轮领取流程（_task_gone_last_round 已置位）
            else:
                log(f"[修罗] {rinfo}", "WARN")
                time.sleep(cycle_interval)
                continue
        # 1. 领任务：任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪。
        #    上一轮已确认任务完成（follow_task 以任务消失为完成判据）时，
        #    本轮开始必然无任务——has_task（区域 OCR ~0.9s）只在可能
        #    续跑时检查（进程首轮或上轮跟踪失败未确认完成）
        claim_items = None  # 领取校验帧（续跑路径无，跟踪首轮自行截图+OCR）
        repair_round = False  # 本轮是否执行了自动修理（续跑路径不修理）
        # 续跑检查（一次截帧）：追踪面板是否有修罗任务。面板门控只用于
        # 「任务存在」判定（防静态区域浮字误命中），面板缺席/战斗帧按
        # 无任务放行进入领取分支——用户 2026-11-11：领取被拒→补人→
        # 人数够了必须继续领取，不能被面板缺席阻塞流程
        if _task_gone_last_round:
            task_present = False
        else:
            task_present = _task_state(win)
        if task_present:
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
            # 队伍检查受「检查队伍」开关门控（默认开启；关闭=完全跳过），且仅在
            # 首次领取前执行（_team_checked 置位后跳过）：每轮回程后开面板
            # 检查耗时 5~7s，首次领取成功后不再重复
            pre_status = None  # 1.0 预检结果（ready/wait）：走位后按结果
                               # 分支，避免监控循环重复开面板检查
            if check_team and not _team_checked:
                if group_leader_confirmed:
                    # 组号启动检查刚在同一面板打开内确认过队长与成员
                    # （2026-11-11 优化）：不再为首次领取重开面板。人数
                    # 取面板昵称下限——达标直接领取（ready），不足/未读
                    # 到则走位后由监控循环补人
                    if group_team_count is not None \
                            and group_team_count >= group_min_members:
                        pre_status = "ready"
                        log(f"[修罗] 组号启动检查已确认队伍 {group_team_count}/"
                            f"{group_min_members} 人，跳过首次领取前队伍检查", "SYS")
                    elif group_team_count is not None:
                        log(f"[修罗] 组号启动检查读到 {group_team_count}/"
                            f"{group_min_members} 人，走位后进入监控循环补人", "SYS")
                    else:
                        log("[修罗] 组号启动检查已确认队长（人数未读到），"
                            "走位后进入监控循环检查", "SYS")
                else:
                    # 1.0 非队长判定：已在领取点附近时跳过（返程落点就在
                    #    领取点，1.2 监控循环马上会检查——先查再走位会让面板
                    #    连续开合两次，一次开合实测 5~7s）；不在领取点则先
                    #    判定身份，非队长不白走寻路
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
                        # 自动创建队伍并入本次检查（同一次菜单打开）
                        while True:
                            status, info = _check_team_with_retry(
                                win, create_if_missing=pending_create)
                            pending_create = False  # 自动创建一次即消费
                            if status == "not_leader" \
                                    and team.is_definitive_not_leader(info):
                                _keep_alive(win, info)
                            if status != "not_leader":
                                pre_status = status
                                break
                            interval = (TEAM_RECOG_RETRY_INTERVAL
                                        if "识别失败" in info
                                        else TEAM_CHECK_RETRY_INTERVAL)
                            log(f"[修罗] {info}（瞬态失败，{interval}s 后原地重查）", "WARN")
                            time.sleep(interval)
            elif pending_create and not is_in_battle(win):
                # 首次领取前队伍检查已被「续跑任务」置位跳过
                # （_team_checked）：自动创建无检查可合并，单独兜底一次；
                # 战斗中不操作，顺延到下一轮
                pending_create = False
                _auto_create_team_once(win)
            # 1.1 队长：队伍是否满都先走到领取点附近
            debug_overlay.set_phase("前往领取点")  # 看板阶段
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
                    debug_overlay.set_phase("检查队伍")  # 看板阶段
                    log("[修罗] 首次领取前进入队伍监控循环（缺人自动补）", "SYS")
                    # 预检被跳过（已在领取点/组号人数不足）时，自动
                    # 创建并入监控的首次检查
                    _monitor_team_and_fill(win, create_if_missing=pending_create)
                    pending_create = False
                    log("[修罗] 队伍人数满足要求，开始领取", "SYS")
            # 1.3 领取任务（校验帧随返回值带出，跟踪首轮复用免重复 OCR）；
            #     队伍不足被拒（战斗力异常强大提示）在 _claim_task 内已
            #     按开关重入监控补人循环，其余失败直接下轮重试
            debug_overlay.set_phase("领取任务")  # 看板阶段
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
            # 血蓝正常也照常（失败仅记日志，不阻断后续寻路）。
            # 期望流程（2026-10-31）：领取完成 → 取消「阻止它为非作歹」
            # 对话框（若有）→ 去巫医 → 识别模板 → 点击 → 全部医治；
            # 对话框可能在走近巫医时才弹出，到达窗口另有兜底
            wuyi_round = auto_wuyi and round_no % wuyi_interval == 0
            repair_round = auto_repair and repair.is_due(win, TASK_MODE,
                                                         repair_interval)
            # 领取校验确认帧的 OCR 行（取帧失败路径为 None）：实测已含该
            # 对话框（2026-11-10 3/3 轮），直接复用取消免一次截图+OCR
            claim_dialog_items = claim_items[0] if claim_items else None
            if wuyi_round:
                debug_overlay.set_phase("自动巫医")  # 看板阶段
                _dismiss_wuyi_block_dialog(win, items=claim_dialog_items)
                ok2, info2 = _auto_wuyi(win)
                log(f"[修罗][巫医] {info2}", "SYS" if ok2 else "WARN")
            else:
                # 正常回合同样取消该对话框：否则它挂在屏幕中央 10s+，
                # follow 对话区 OCR + 战斗线程巫医检查 + 看打 2x 兜底
                # 三重空烧（2026-11-09 py-spy 实测领取后 CPU 峰值来源）
                _dismiss_block_dialog_after_claim(win, items=claim_dialog_items)
            # 自动修理装备（用户流程，默认开、每 30 场战斗一次）：今日累计
            # 场次达到间隔 → 前往洛阳城修理（巫医之后：同图巫医先做，修理
            # 跨图后直接恢复寻路）。失败仅记日志，不阻断后续寻路。
            if repair_round:
                debug_overlay.set_phase("自动修理")  # 看板阶段
                ok3, info3 = repair.auto_repair(win, TASK_MODE, prefix="修罗")
                log(f"[修罗][修理] {info3}", "SYS" if ok3 else "WARN")
        # 2~4. 寻路 / 对话开打 / 战斗：由任务跟踪统一驱动
        #     （寻路点击绿字自动进行，对话开打走附加对话处理，
        #      战斗由战斗监控线程自动操作、跟踪中自动跳过）；
        #     领取校验帧传入后首轮跳过截图+OCR 立即点击绿字
        #     （修理行程后旧帧已过期——重新截帧识别）
        ok, info = _follow_to_done(
            win, first_items=None if repair_round else claim_items)
        completed_via_failure = False
        if not ok:
            failed_by_battle = _battle_failed_reason is not None
            if not failed_by_battle:
                # 兜底（用户 2026-11-14 疑问）：非战斗失败返回（如跟踪
                # 300s 超时）时若人在轮回司，说明战败但战斗结束信号未采到
                # /判定漏检——同样按战斗失败恢复，避免下一轮对着同一任务
                # 从轮回司继续跟踪、反复打同一任务
                img = win.capture(force_activate=False)
                pos = game_map.read_map_position(win, img=img) \
                    if img is not None else None
                if team.is_in_lunhuisi(pos):
                    _battle_failed_reason = "dead"
                    log("[修罗] 跟踪失败且人在轮回司，按战斗失败处理", "WARN")
                    failed_by_battle = True
            if failed_by_battle:
                # 任务战斗失败（用户 2026-11-14）：队长回灵兽村取消任务
                # → 取消后 6s 重新领取（人数不足走检查暂离/补人流程）；
                # 非队长转保持模式并自动归队（队员不能取消/领取任务）
                _battle_failed_reason = None
                debug_overlay.set_phase("战斗失败恢复")  # 看板阶段
                status, rinfo = _recover_battle_failure(win, check_team)
                if status == "completed":
                    # 任务已消失（战斗胜利后死亡等）：返程已在恢复流程内
                    # 完成，直接按本轮完成收尾
                    log(f"[修罗] {rinfo}", "SYS")
                    completed_via_failure = True
                elif status == "canceled":
                    # 恢复流程内已等待 CANCEL_RE_CLAIM_DELAY(6s)，这里
                    # 不再追加间隔，下一轮直接领任务
                    log(f"[修罗] {rinfo}", "SYS")
                    continue
                else:
                    log(f"[修罗] {rinfo}", "WARN")
                    time.sleep(cycle_interval)
                    continue
            else:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
        if not completed_via_failure:
            # 5a. 返程前复查：follow 消失确认只做 1 轮（见 _follow_to_done），
            #    全区域 has_task 兜住单轮误判——任务仍在则跳过返程继续跟踪
            #    （下一轮续跑自愈），不得与单轮确认同时放宽
            if _task_still_tracked_before_return(win):
                _task_gone_last_round = False  # 恢复跟踪：下一轮 has_task 续跑
                log("[修罗] 返程前复查任务仍在追踪，跳过返程继续跟踪", "WARN")
                time.sleep(cycle_interval)
                continue
            _task_gone_last_round = True  # 任务消失已确认：下一轮跳过 has_task 检查
            # 5. 返程：仅「看打！」进入的战斗结束后才返程（背包任务储物格
            #    右键修罗回城符回灵兽村），其他方式进入的战斗不自动返程
            if _fight_via_kanda:
                debug_overlay.set_phase("返程")  # 看板阶段
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
        debug_overlay.record_round()  # 统计页：完成轮次计数
        log(f"[修罗] 本轮修罗任务完成（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
        # 成功路径不加轮次间隔：返程流程结尾已有 5s 位置识别等待，
        # 游戏状态已稳定，下一轮直接读位置领取即可（间隔仅保留在
        # 失败重试路径，防快速失败模式紧循环空转）
