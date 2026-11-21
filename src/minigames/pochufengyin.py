"""进度条对准小游戏（破除封印 / 钓鱼 / 开采石头）闭环控制。

同一款小游戏窗口被多个任务复用：客栈「破除封印」（灵兔封印）、名望
「蛟魔探秘钓鱼」与玄枢「钓鱼」共用完全相同的窗口布局、开始按钮、目标
高亮带与进度条区域，仅标题模板与推进方式不同——破除封印/蛟魔探秘为
「点击」（冲刺连点推进，mash=True），钓鱼/开采石头为「长按」（长按
按钮模板如 xuanshu_diaoyu_changan.png）。
长按模式细分两种：**锁定持握**（hold=True，纯按住到窗口结束，当前
无调用方）；**长按运输 + 短段长按维持**（hold=True + hold_catch=True，
长按推过带上缘→松手回落→回 C 后用「短段长按 / 松手」bang-bang 围绕
C 振荡；开采石头 2026-11-10、钓鱼 2026-11-12 起均用此技巧——进带不
锁定，一直按住必然冲过目标带，维持段须反复按-松）。
实现与标定同源（2026-11-09 用户完整描述 + 7.png 实测），调用方只传
标题模板、按钮模板、推进方式与日志前缀/名称。

流程：
弹窗出现 → 识别目标高亮区间 [L,R,C]（target 模板定位，目标带 ≈1 刻度宽）
→ 点「开始」（按钮同位置变为流程按钮）→ 每轮询一帧测量填充右边缘 P
（HSV 金掩码 + 从左连通段）→ 推进（点击模式按 C 三档连点到目标带中心
C 附近才停手——破除封印松手后填充会回落，停在左缘有掉出带下的风险 /
长按模式按住不放 / 冲刺连点模式按在线估算的每击推进量连点并持续维持）。
点击模式 C≤P≤R 停手保持、P>R 过头永久停手；长按模式进入区间后继续按住
（游戏锁定游标，松手会回退脱钩），仅 P>R 时松开让其回落；冲刺模式不按
倒计时停手（填充条自动回退，须维持到窗口结束），按「显示 P + 在途点击
折算」的有效位置补点，悬停在目标带中上段。**带下拉扯补偿**（2026-11-21
蛟魔探秘第一次失败标定）：目标带随机到远带时，带外填充自动回退
（~0.5 刻度/秒）会把每批点击的兑现吃掉，旧「在途信用 + 落地门」逻辑
每秒只放 ~2 击、恰好与回退持平 → P 卡在带下阴跌判失败；现 0.6s 内有点击
但 P 未上涨即判定拉扯，放宽补点目标到 R-0.20、跳过落地门（有效位置
仍限幅），压过回退进带后自动恢复常规维持（见 POCHU_MASH_FIGHT_*）。
按钮变「保持」或倒计时 ≤3s
（冲刺模式除外）完全停手等结算 → 标题消失即任务结束。**冲刺模式的
「保持」不是停手信号**（2026-11-20 实机标定）：第一段 10s 倒计时归零后
游戏进入最后 3 秒「保持」阶段（倒计时重置 3→0、按钮变「保持」、提示
「请停留在N力量的区间保持最后3秒」），游标不在带内即判失败；停手会让
填充条自动回退掉出带下（失败对话「还得再试一次」）。保持阶段须继续
维持点击。目标区间一律是 target 模板白括号（±29px）实测刻度，**不做
偏移**——游戏判定的就是这段括号区间（2026-11-19 破除封印「超出目标
范围」失败标定）。
所有点击/连点批次前用**新截帧**复检窗口标题模板，模板不在即放弃本次
点击（2026-11-20 用户规则：窗口消失后残留点击会落到世界画面、人物开始
走动——实测 [23,61]→[28,68]）。
全部测量在 POCHU_REGION/进度条带内，坐标映射 1~10 刻度（x 435~992，
7.png 实测刻度线间距 55.7px 吻合）。
"""
import os
import re
import time

import cv2
import numpy as np

from ..core.app_utils import (log, human_click, rapid_click_burst, MouseHold,
                         _UNDER_PYTEST)
from ..device import map as game_map
from ..device.battle import is_in_battle_frame

# ---- 窗口按钮与模板（区域范围与客栈破除封印完全一致）----
POCHU_START_ICON = "kezhan_pochufengyin_kaishi.png"  # 「开始」按钮（点击后同位置变为流程按钮）
POCHU_CLICK_ICON = "kezhan_pochufengyin_dianji.png"  # 点击模式「点击」按钮（过程中会变外观，坐标不变仍须点）
POCHU_TARGET_ICON = "kezhan_pochufengyin_target.png"  # 目标高亮区间标记（模板定位 C）
POCHU_PROGRESS_ICON = "kezhan_pochufengyin_progress.png"  # 填充前缘（调试图标注用）
POCHU_REGION = (314, 458, 1058, 645)   # 弹窗模板搜索条带（用户提供，覆盖小游戏窗口区）
POCHU_THRESHOLD = 0.8                  # 模板匹配阈值（标题 0.999/点击 0.998/目标 0.985 实测）
# ---- 进度条测量（7.png 实测标定）----
POCHU_SCALE_X0 = 435                   # 1 刻度窗口 x（进度条左端）
POCHU_SCALE_X1 = 992                   # 10 刻度窗口 x（进度条右端）
POCHU_SCALE_W = POCHU_SCALE_X1 - POCHU_SCALE_X0  # 一满刻度宽（557px，刻度线间距实测 55.7）
POCHU_BAR_REGION = (430, 528, 1000, 562)  # 进度条搜索带（目标带/填充测量用）
POCHU_BAR_Y = (536, 554)               # 进度条本体 y 带（填充行；刻度线在其上方 511~527）
POCHU_FILL_MIN_COL = 6                 # 列内填充像素下限（主体实测 11~13 行/列；
                                       # 旧值 2 会吞掉进度条 2 行装饰线 → 带内读数
                                       # 被桥接到目标带右括号，见上方 HSV 注释）
POCHU_FILL_GAP = 8                     # 连续缺填充列数即视为填充段结束
POCHU_ZONE_HALF = 29                   # 目标带半宽（模板 58px≈1 刻度；实测括号 879~937 吻合）
# 填充金掩码（HSV，2026-11-12 真机帧重标定）：填充体实测 H14 S133 V172
# ——旧下限 H15 把 11 行主体里 7 行排除在外，只剩底部 3~4 行（H17-18）；
# 进度条上还有一条 2 行高的装饰线贯穿整条（含目标带），旧列阈值 2 把它
# 也算填充、8px gap 容差把真实边缘桥接到目标带右括号 → 带内读数恒为
# R-0.01（2026-11-12 钓鱼：填充在 5.8-6.1 徘徊却一直读到 6.97）。
# 现 H 下限放到 12（主体每列 11~13 行）、列阈值提到 6（装饰线只有 2~3
# 行）——全量真机帧复算后读数平滑（0→7.5→6.0 无跳变、带内可直接测）。
# 目标带括号为白金色 H36 S61 V215（H>32 排除）、暗内部 V≤125
# （V 门排除）、刻度白线 S 低（S 门排除）
POCHU_FILL_H = (12, 32)
POCHU_FILL_S = 95
POCHU_FILL_V = 120
# ---- 倒计时与按钮文字（OCR，差分门 + 限频）----
POCHU_CNT_REGION = (600, 545, 770, 598)   # 「施法倒计时：N秒」区域（7.png 实测）
POCHU_BTN_REGION = (915, 565, 1010, 635)  # 按钮文字区域（点击/保持/长按）
POCHU_CNT_STOP = 3                     # 倒计时 ≤3 秒完全停手
POCHU_OCR_REFRESH = 2.0                # 倒计时/按钮 OCR 强制重检间隔（差分门兜底）
POCHU_STALL_CLICKS = 4                 # 点击模式：P 连续不动点击数 → 告警按钮坐标
POCHU_HOLD_STALL = 2.0                 # 长按模式：按住 P 不动达该时长（秒）→ 告警一次
POCHU_HOLD_OVER_MARGIN = 1.0           # 长按模式「冲过右缘」判定余量（刻度）：
                                       # 游标锁定在区间右缘时测量噪声可轻微过 R，
                                       # 余量外（R+1）才松开回落，防误松手
POCHU_STATE_LOG_INTERVAL = 1.0         # 状态日志限频（秒）
# 点击节奏三档（用户 spec）：C<4 近目标易过头（400~500ms，每点等位置更新）；
# 4≤C≤7 中距离（250~300ms）；C>7 远距离时间紧（150~200ms 快速推进）
POCHU_TIER_NEAR = 0.45
POCHU_TIER_MID = 0.275
POCHU_TIER_FAR = 0.175
POCHU_TICK_PACE = 0.15                 # 点击模式测量节拍（每轮询一帧）
POCHU_HOLD_TICK_PACE = 0.08            # 长按模式测量节拍（填充推进快，需更密
                                       # 采样以在进入区间时及时松手防过头）
# ---- 冲刺连点模式（mash=True，蛟魔探秘钓鱼 2026-11-10 真机实测）----
# 同款「点击」窗口的通用打法；客栈破除封印 2026-11-12 实机日志确认
# 同病：13 击/7s 只推到 P≈1.4，10s 倒计时内三档节奏永远到不了目标带。
# 该小游戏两个真机特性（用户 2026-11-10 反馈 + 日志实测）：
# 1) 单击推进小（慢点 10 击 P≈1.06），三档节奏 ~3 击/秒 到不了目标带；
# 2) 填充条会**自动回退**（停手即从带内掉回 L 以下，倒计时 3s 停手
#    必失败），且连点后填充条有追帧延迟（14 连点瞬间仍测到 0.05、
#    约 1s 后才升到 3.23）——按滞后值补点会过量。
# 冲刺模式用 rapid_click_burst 按「每击推进量」在线估算冲进带内，
# 每次冲刺后等填充条停稳（不再上升）才决策，进带后维持泵按有效位置
# （显示 P + 在途点击折算）补充点击，悬停在目标带中心到窗口标题消失
# （不按倒计时停手）。
POCHU_MASH_RATIO0 = 0.25          # 初始每击推进量估计（刻度；偏大保守防过头）
POCHU_MASH_RATIO_SAFETY = 1.15    # 估算保守系数：按每击多推进 15% 计算，宁可少点
POCHU_MASH_RATIO_MIN = 0.01       # 每击推进量有效区间（异常观测丢弃）
POCHU_MASH_RATIO_MAX = 1.0
POCHU_MASH_AIM = 0.45             # 冲刺目标点 = L + 0.45（目标带内、右侧留余量防过头）
POCHU_MASH_DEADBAND = 0.08        # 冲刺死区：P ≥ 目标点-死区 不冲刺（防微抖连点）
POCHU_MASH_RISE_EPS = 0.04        # 冲刺后每轮上升超过该值 = 追帧未停稳，不决策
POCHU_MASH_STABLE_POLLS = 2       # 连续「不再上升」轮数确认停稳（防动画中间帧误判）
# 维持泵（2026-11-19 客栈破除封印失败后重做：原「点击率伺服」按显示值
# 决策，连点后填充条追帧滞后约 1s，泵按 R-0.3 停手时队列里还有点击在
# 途，实测把游标从 6.91 一路推过右缘到 7.50，游戏判「超出目标范围」）。
# 本游戏的目标带就是 target 模板白括号中心 ±29px（2026-11-19 实机帧
# 量测 713~771px ↔ 刻度 [4.99,6.03] 完全一致），**不做任何偏移**——
# 旧 +0.2 偏移让泵悬停在真实右缘附近，结束时 P=7.07 > 真实 R=7.0 却
# 按偏移区间报「命中」（2026-11-17 蛟魔探秘 P=1.99 vs 原始 [0.88,1.88]
# 同病）。维持泵改为按**有效位置**决策：显示 P + 最近 POCHU_MASH_LAG
# 秒内已投递点击的保守推进量，补点量按有效位置到目标点的差值计算，
# 在途点击不会被重复追加——游标稳定悬停在目标带中心附近。
POCHU_MASH_HOLD_ENTER = 0.15     # 进入维持泵：显示 P ≥ L-0.15（带口）才切换；
                                  # 旧 0.60 会让泵在带外就按显示值连点，残留在途
                                  # 点击直接把游标顶过整个目标带（2026-11-19 教训）
POCHU_MASH_PUMP_AIM = 0.60       # 维持目标点 = L+0.60（中上带）：2026-11-20 破除封印
                                  # 实机标定——旧 0.50 泵偏弱，最后 3 秒保持阶段游标
                                  # 衰减到左括号附近（P=L+0.03~0.12）被判失败；强维持
                                  # 原型稳定在 L+0.36~0.81、任务推进
POCHU_MASH_TOP_MARGIN = 0.20     # 硬上限：有效位置 ≥ R-0.20 不再补点
POCHU_MASH_LAG = 1.0             # 冲刺段在途点击窗口（秒）：大批次连点后填充条
                                  # 追帧延迟可达 ~1s（2026-11-10 实测 14 连点 1s
                                  # 后才升到 3.23）——窗口过短会把在途批次当成
                                  # 没点过、重复冲刺顶过右缘
POCHU_MASH_LAG_HOLD = 0.35       # 维持段在途窗口（秒）：维持批次 ≤2 击，填充
                                  # 追帧在 ~0.3s 内结算；2026-11-20 实机保持阶段
                                  # 用 0.35 可及时补偿衰减，游标不再向左滑出带
                                  # （旧 1.0 的未兑现点击台账让泵误以为位置够高、
                                  # 少补点，保持阶段 P 从 6.55 滑到 6.03 判失败）
POCHU_MASH_UNSHOWN_GATE = 0.20    # 落地门阈值（刻度）：在途批次估未兑现量超过
                                  # 该值才等待；每击估算的安全系数 1.15 会
                                  # 留下小尾巴，不能按 >0 卡死（会饿死维持泵）
POCHU_MASH_PUMP_GAP = 0.08       # 两次补点最小间隔（秒；在途窗口已防重复计数，
                                  # 这里只做轻量限频——间隔过大（0.30 实测）
                                  # 会让衰减吃掉补点量、泵悬停点低于目标点）
POCHU_MASH_CNT_GONE_POLLS = 3     # 倒计时连续消失轮数 → 已结算，停手
POCHU_MASH_HOLD_MAX = 2           # 单轮补点上限（贴带口时通常 1 击）
POCHU_MASH_MAX_BURST = 90         # 单次冲刺点击上限（限制测量间隔）
# ---- 带下拉扯补偿（2026-11-21 蛟魔探秘第一次失败标定）----
# 该游戏目标带距离随机（本次 L=5.0 远带），带外鱼在拉扯：填充以
# ~0.5 刻度/秒自动回退。旧控制器按「在途点击信用」决策——信用把回退
# 吃掉的兑现一直算成在途未兑现，n 一路缩小到 1~2 击，落地门再按 1s
# 窗口封顶 ≈2 击/秒，恰好被回退抵消：P 从 4.49 阴跌到 4.56 判失败
# （18 击冲到 4.49 后 12 击只换回 +0.07，倒计时耗尽）。修法：0.6s 内
# 投递过点击但 P 未上涨（涨幅 < 0.05）→ 判定拉扯，把补点目标放宽到
# 硬上限 R-0.20（带下/带口离右缘还有 ≥1 刻度余量，无过冲风险）并
# 跳过落地门——有效位置（含在途信用）仍按原样限幅，每约 1s 信用
# 窗口可再压 ≈4~6 击，压过回退把填充顶进带内后自动恢复常规维持泵。
# 拉扯只改「补点目标/落地门」，带内 2 击/轮上限、R-0.20 硬上限、
# 有效位置台账一律不变（2026-11-19/20 过冲标定不受影响）。
POCHU_MASH_FIGHT_WINDOW = 0.6     # 判定拉扯的观察窗口（秒）
POCHU_MASH_FIGHT_EPS = 0.05       # 窗口内 P 涨幅不足该值 = 点击未兑现
POCHU_MASH_MIN_CLICKS = 5         # 判定「点击未推进」所需最少点击数
POCHU_MASH_PRESS = 0.02           # 冲刺点击按压时长（rapid_click 默认）
POCHU_MASH_PRESS_MAX = 0.05       # 连续未推进时加压上限（防漏注册）
# ---- 结果判定「游戏失败」（2026-11-17 蛟魔探秘真机帧标定）----
# 失败结果面板**标题仍在**：进度条下方居中显示红色「游戏失败」、右侧
# 按钮为「点击」圆钮，面板不会自动关闭。模板自真机失败画面截取
# （pochu_youxishibai.png 85×34，OCR 框 (651,581)-(726,605) 外扩 5px），
# 两条真机帧（13:00 失败画面 / 13:04 复现画面）交叉验证：正样本
# 1.00/1.00，非面板帧（世界/战斗）0.09~0.10——阈值 0.8 余量充足。
# 该提示只在进入结果态后渲染；fail_detect=True 的调用方（蛟魔探秘/
# 客栈破除封印）检测到即停止全部操作提前返回失败，由调用方关闭面板
# 重试；不检测时沿用旧行为（标题消失 + 位置猜测命中）。
POCHU_FAIL_ICON = "pochu_youxishibai.png"
POCHU_FAIL_REGION = (600, 545, 790, 640)  # 「游戏失败」搜索带（面板漂移余量）
POCHU_FAIL_THRESHOLD = 0.8
POCHU_FAIL_CONFIRM = 2                 # 连续命中轮数确认（防单帧误检）
# 面板右上角 X 关闭按钮（模板截自同一真机帧，16×20）：正样本
# 1.00/0.99、非面板帧 ≤0.41——失败重试时由调用方用本模板关闭面板。
POCHU_CLOSE_ICON = "pochu_guanbi.png"
POCHU_CLOSE_REGION = (950, 435, 1060, 525)  # X 搜索带（面板右上角）
POCHU_ARM_TIMEOUT = 120                # 入口选项点击后检测窗口的最长时限（秒）
POCHU_CHECK_INTERVAL = 1.0             # extras 链窗口检测限频（秒，条带匹配 ~10ms）
POCHU_END_WINDOW = 2.0                 # 标题连续消失确认窗口（秒；结束确认防抖，不得缩短）
POCHU_TIMEOUT = 180                    # 小游戏总超时（秒）
# ---- 长按运输 + 短段长按维持模式（hold_catch；开采石头 2026-11-10、
# 钓鱼 2026-11-12）----
# 长按推进极快（2026-11-12 真机帧实测 ~3.6 刻度/秒），目标带仅 ~1 刻度
# 宽且进带不锁定——一直按住必然冲过目标带；松手后填充回落（实测
# ~1.4 刻度/秒）。用户技巧（2026-11-10 开采石头、2026-11-12 钓鱼）：
# **先长按把填充推过目标带上缘，松手让它回落，回到 C 附近后改为
# 短按维持**——维持段用「短段长按 bang-bang」：P ≤ C-LO 按住、
# P ≥ C-HI 松手（松手比目标提前，补偿升速 3.6 刻度/秒 × ~0.1s 的
# 动作滞后 ≈ 0.36 刻度），围绕 C 小幅振荡到窗口结束；掉出带下则重新
# 长按运输（限次），并按掉出方向微调 LO/HI 自校准。
# **维持段不用 rapid_click_burst 短脉冲**：2026-11-12 实机 8 次脉冲后
# 按钮卡在按下状态（UP 丢失），填充从 1.9 一路涨到 10.05 无法挽回；
# MouseHold 的 begin/end（先 MOVEMOVE 延迟 0.05s 再 DOWN、UP 后再
# MOVEMOVE 移出）在长按运输中从未丢过 UP，维持段复用同一通道。
# 倒计时 ≤3s 同样不得停手（填充会回退掉出带下，与冲刺模式同理）。
# 纯长按（锁定持握，hold 不带 hold_catch）与点击/冲刺模式语义不变。
POCHU_CATCH_ABOVE = 0.20        # 长按越过 R 该值后松手（回落起点保证在带上）
POCHU_CATCH_REPRESS_MARGIN = 0.40  # 维持段仍落出 L 下方该值 → 重新长按运输
POCHU_CATCH_MIN_GAP = 0.25      # 松手后重新按住的最小间隔（秒）
POCHU_CATCH_MAX_PRESS = 4       # 最多长按运输次数
POCHU_CATCH_HOLD_LO = 0.20      # 维持：P ≤ C-该值开始按住（初始，自适应微调）
POCHU_CATCH_HOLD_HI = 0.10      # 维持：P ≥ C-该值松手（提前松，补偿动作滞后）
POCHU_CATCH_HOLD_LO_MIN = 0.05  # LO 自适应下限（落出带下 → 减小 LO 提前按）
POCHU_CATCH_HOLD_HI_MAX = 0.40  # HI 自适应上限（冲出带上缘 → 增大 HI 提前松）
POCHU_CATCH_ADAPT_STEP = 0.03   # 每次维持异常（掉出带下/冲出带上缘）的微调步长
# ---- 维持段调试采样（真机标定用；POCHU_DEBUG_DUMP=True 时开启）----
# 2026-11-12 用调试图定位到测量阈值问题（填充体 H14 被旧阈值排除、
# 2 行装饰线被当成填充桥接到目标带右括号）。保留供后续真机标定：
# 上下两栏同 x 对齐（上=原始裁剪、下=金掩码命中像素染绿）+ 测得的
# 填充边缘/目标带刻度画线，不影响控制。
POCHU_DEBUG_DUMP = False
POCHU_DEBUG_DIR = "pochu_debug"
POCHU_DEBUG_INTERVAL = 0.25     # 保存间隔（秒）
POCHU_DEBUG_MAX = 48            # 单次小游戏最多保存张数


def _pochu_debug_dump(bgr, p, px, zone, seq, tag):
    """保存一张进度条带调试图（不影响控制）。上下两栏同 x 对齐：
    上=原始裁剪，下=金掩码命中像素染绿。白线=测得的填充右边缘，
    蓝线=L/R，红线=C，黄框=前缘模板（progress.png）最佳匹配。
    """
    try:
        l, t, r, b = POCHU_BAR_REGION
        x0, x1 = max(0, l - 15), r + 15
        y0, y1 = max(0, t - 25), b + 25
        raw = bgr[y0:y1, x0:x1].copy()
        hsv = cv2.cvtColor(raw, cv2.COLOR_BGR2HSV)
        h_ch, s_ch, v_ch = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        mask = (h_ch >= POCHU_FILL_H[0]) & (h_ch <= POCHU_FILL_H[1]) \
            & (s_ch > POCHU_FILL_S) & (v_ch > POCHU_FILL_V)
        marked = raw.copy()
        marked[mask] = (0, 255, 0)
        vis = np.vstack([raw, marked])
        h = vis.shape[0]
        if px is not None:
            cv2.line(vis, (px - x0, 0), (px - x0, h - 1), (255, 255, 255), 1)
        if zone is not None:
            for vx, color in ((zone[0], (255, 0, 0)), (zone[1], (255, 0, 0)),
                              (zone[2], (0, 0, 255))):
                wx = int(round(POCHU_SCALE_X0 + vx / 10.0 * POCHU_SCALE_W))
                if 0 <= wx - x0 < vis.shape[1]:
                    cv2.line(vis, (wx - x0, 0), (wx - x0, h - 1), color, 1)
        score, ppt = game_map.best_match_np(bgr, POCHU_PROGRESS_ICON,
                                            region=POCHU_BAR_REGION)
        if ppt is not None:
            cv2.rectangle(vis, (ppt[0] - 23 - x0, 0),
                          (ppt[0] + 23 - x0, h - 1), (0, 255, 255), 1)
        cv2.putText(vis, f"P={p:.2f} px={px} prog={score:.2f}",
                    (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (255, 255, 255), 1, cv2.LINE_AA)
        os.makedirs(POCHU_DEBUG_DIR, exist_ok=True)
        path = os.path.join(POCHU_DEBUG_DIR, f"{tag}_{seq:03d}"
                            f"_{time.strftime('%H%M%S')}.png")
        cv2.imwrite(path, vis)
    except Exception:
        pass


def pochu_tick(x):
    """窗口 x → 1~10 刻度（0=进度条左端 435，10=右端 992）。"""
    return (x - POCHU_SCALE_X0) / POCHU_SCALE_W * 10.0


def pochu_fill_edge(bgr):
    """进度条金色填充右边缘窗口 x（HSV 金掩码 + 从左起连通段）。

    填充体/前缘实测 HSV 见 POCHU_FILL_H/S/V；目标带括号（H>32）与暗
    内部（V≤125）被掩码排除，连通段从条左端起算不受其干扰。无填充
    （未开始/掩码漏检）返回 None（调用方按刻度 0 处理）。

    只对进度条带（POCHU_BAR_REGION）做 HSV 转换：这是维持泵每轮都跑
    的热路径，全帧转换白白多算 5~10ms，采样越慢维持越不准。
    """
    l, t, r, b = POCHU_BAR_REGION
    hsv = cv2.cvtColor(bgr[t:b, l:r], cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    fill = (h_ch >= POCHU_FILL_H[0]) & (h_ch <= POCHU_FILL_H[1]) \
        & (s_ch > POCHU_FILL_S) & (v_ch > POCHU_FILL_V)
    colcnt = fill[POCHU_BAR_Y[0] - t:POCHU_BAR_Y[1] - t, :].sum(axis=0)
    have = colcnt >= POCHU_FILL_MIN_COL
    idx = np.flatnonzero(have)
    if idx.size == 0:
        return None
    start = int(idx[0])
    pend = start
    gap = 0
    for i in range(start, have.size):
        if have[i]:
            gap = 0
            pend = i
        else:
            gap += 1
            if gap >= POCHU_FILL_GAP:
                break
    return l + pend


def _pochu_zone(bgr):
    """目标高亮区间：target 模板定位中心 C，L/R=C±POCHU_ZONE_HALF
    （目标带 ≈1 刻度宽，7.png 实测括号 879~937 与模板 ±29 吻合）。
    返回 (L, R, C) 窗口 x 或 (None, None, None)。"""
    c = game_map.match_frame_np(bgr, POCHU_TARGET_ICON,
                                threshold=POCHU_THRESHOLD,
                                region=POCHU_BAR_REGION, fallback=False)
    if c is None:
        return None, None, None
    return c[0] - POCHU_ZONE_HALF, c[0] + POCHU_ZONE_HALF, c[0]


_POCHU_CNT_RE = re.compile(r"(\d{1,2})\s*秒")


def _pochu_ocr_gated(img_pil, region, cache, force=False):
    """区域 OCR 差分门：区域像素无变化（且非强制）直接返回缓存文本
    （性能规则 5：OCR 前置像素差分预过滤）；变化才 OCR。识别失败
    返回 None（未知状态，调用方不动作）。"""
    crop = np.asarray(img_pil.crop(region))
    if not force and cache["crop"] is not None \
            and np.array_equal(cache["crop"], crop):
        return cache["text"]
    cache["crop"] = crop.copy()
    items, _ = game_map.ocr_regions(img_pil, (region,))
    txt = "".join(t for t, _r, s in items if s >= 0.55)
    cache["text"] = txt or None
    return cache["text"]


def _pochu_countdown(img_pil, cache, force=False):
    """倒计时秒数（差分门 OCR「施法倒计时：N秒」）；解析失败返回 None。"""
    txt = _pochu_ocr_gated(img_pil, POCHU_CNT_REGION, cache, force=force)
    if not txt:
        return None
    m = _POCHU_CNT_RE.search(txt)
    return int(m.group(1)) if m else None


def _window_present(win, title_icon):
    """点击前新截帧复检小游戏窗口标题模板（模板不在=窗口已消失，绝不点击）。

    2026-11-20 用户实机规则：破除封印窗口结算消失后，缓存按钮坐标上的
    残留点击会落到世界画面，人物开始走动（实测 [23,61]→[28,68]）。
    标题模板命中即窗口在场；不在即放弃本次点击/连点/长按，下一轮询走
    标题消失确认。单次复检 ~10ms（条带匹配）+ 一次截窗，仅在点击前付。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False
    bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
    return game_map.match_frame_np(bgr, title_icon,
                                   threshold=POCHU_THRESHOLD,
                                   region=POCHU_REGION, fallback=False) is not None


def play_pochufengyin(win, title_icon, prefix="客栈", label="破除封印",
                      hold=False, action_icon=None, mash=False,
                      hold_catch=False, fail_detect=False):
    """进度条对准小游戏闭环控制（阻塞执行到标题消失）。返回 (是否完成, 描述)。

    流程（用户 2026-11-09 完整描述 + 7.png 实测标定）：
    1. 弹窗出现（记录时间）→ 识别目标高亮区间 [L,R,C]
    2. 点「开始」→ 按钮同位置变为流程按钮（点击模式「点击」/长按模式
       「长按」，过程中会变其他外观，坐标不变仍须操作；变「保持」则
       完全停手）
    3. 闭环（每 POCHU_TICK_PACE 一帧一判定，同帧复用性能规则 1）：
       测量填充右边缘 P（HSV 金掩码+连通段）与目标带 [L,R,C]（模板）
       → 推进：点击模式按 C 三档节奏连点到 C 附近停手（松手后填充会
       回落，只推到 L 有掉出带下的风险，用户 2026-11-11）、纯长按模式
       按住不放、hold_catch 模式长按运输→松手回落→短段长按维持。
       点击模式：C≤P≤R 停手保持、P>R 连续 2 轮确认过头后永久停手。
       纯长按模式：进入区间后继续按住（假定游戏锁定游标），仅 P>R
       连续 2 轮时松开让其回落、回到 L 以下自动再按住。
       hold_catch 模式：见下段。点击/纯长按模式按钮变「保持」或
       倒计时 ≤3s 完全停手等结算（长按期间跳过 OCR，以标题消失为准；
       hold_catch/冲刺模式倒计时不停手，须维持到窗口结束）
    4. 标题连续消失 POCHU_END_WINDOW 秒即任务结束（输出最终 P 与是否命中）
    5. 战斗打断立即退出；异常：目标带检测失败沿用缓存区间继续决策、
       长按模式区间左侧按住无进度 POCHU_HOLD_STALL 秒告警并释放重按
       （down 消息漏消费自愈；区间内不动视为游戏锁定，不释放）

    title_icon：窗口标题模板（各任务不同，如 kezhan_pochufengyin.png /
    xuanshu_diaoyu.png）；prefix/label：日志前缀与小游戏名（同款窗口
    不同任务日志可区分）。hold=True 为长按模式（开始后按钮为
    action_icon，按住才推进；纯长按假设游标进带后游戏锁定，须保持
    按住到窗口结束——2026-11-12 钓鱼日志证实进带不锁定，钓鱼已改用
    下方 hold_catch）；hold=False 为点击模式。mash=True 为冲刺连点模式（仅 hold=False
    生效；蛟魔探秘钓鱼与客栈破除封印——同一「点击」窗口）：该游戏单次
    点击推进小、三档拟人节奏到不了目标带（真机实测 7s 内只到 P≈1.4~2），
    且填充条有追帧延迟、停手后会**自动回退**。改为 rapid_click_burst
    按「每击推进量」在线估算连点冲进带内（目标带即 target 模板白括号
    实测刻度，不偏移），每次冲刺后等填充条停稳再决策（防按滞后值
    过量）；进带后切换「维持泵」：按**有效位置**（显示 P + 最近
    POCHU_MASH_LAG 秒内已投递点击的保守推进量）计算补点量，悬停在
    目标带中上段，在途点击不会被重复追加；不按倒计时停手、维持到
    窗口标题消失。**mash 的「保持」按钮不是停手信号**：最后 3 秒
    「保持最后3秒」阶段游标不在带内即判失败，停手会让填充回退出带
    （2026-11-20 客栈实机：13:32 场保持阶段 P 6.55→6.03 判失败、
    失败对话后任务卡住；保持阶段强维持原型 P 4.36~4.81 全程在带 →
    任务推进）。2026-11-19 客栈破除封印「超出目标范围」失败后
    重做——旧实现按显示值决策 + 目标带 +0.2 偏移，追帧滞后把游标
    推过真实右缘且误报「命中区间」。
    hold_catch=True 为「长按运输 + 短段长按维持」模式（须配合
    hold=True；开采石头 2026-11-10、玄枢钓鱼 2026-11-12 用户技巧）：
    该游戏长按推进 ~3.6 刻度/秒（2026-11-12 真机帧实测）、目标带仅
    ~1 刻度宽且进带不锁定，一直按住必然冲过目标带、松手回退（~1.4
    刻度/秒）判失败。改为：长按把填充推过带上缘（P≥R+0.2）→ 松手让
    填充整体回落 → 回到 C（带中心）即切换**短段长按 bang-bang**：
    P≤C-LO 按住、P≥C-HI 松手（HI 提前量补偿升速×动作滞后，使实际峰值
    落在 C 附近），围绕 C 振荡到窗口结束；掉出带下/冲出带上缘按方向
    微调 LO/HI 自校准，掉出带下过多则重新长按运输（限次）。倒计时
    ≤3s 也不停手（停手填充会回退掉出带下）。维持段按住/松手走
    MouseHold（与运输同一通道）——不用 rapid_click_burst 短脉冲
    （2026-11-12 实机 8 次脉冲后 UP 丢失、按钮卡按下，填充跑到 10.05）。
    全程跳过区域 OCR 以保住采样频率（倒计时/按钮文案不参与本模式
    决策）。见 POCHU_CATCH_* 常量。
    fail_detect=True：每轮询（同帧）匹配「游戏失败」结果模板，连续
    POCHU_FAIL_CONFIRM 轮命中即停止全部操作并**提前返回失败**——
    失败结果面板标题仍在、不会自动关闭，旧逻辑要等标题消失（或超时），
    且结束时按位置猜命中会误报成功；蛟魔探秘据失败返回关闭面板并
    重走任务，客栈破除封印据失败停止点击（面板随后自动关闭，任务侧
    等 follow 超时重试）。蛟魔探秘/客栈开启，其他调用方行为不变。
    """
    perf = {"n": 0, "sum": 0.0, "max": 0.0, "last": None}  # 采样间隔统计

    def _pace(tick_start):
        """补齐测量节拍（截图/匹配/点击耗时计入间隔，不累积漂移）。

        顺带统计每轮采样间隔：本小游戏是「采样越快维持越准」的热路径，
        结束描述里输出平均/最大间隔，便于按实机数据调参（2026-11-12
        钓鱼实机暴露过 OCR 把单轮拖到 0.3~0.5s 的问题）。
        """
        nowp = time.time()
        if started and perf["last"] is not None:
            d = nowp - perf["last"]
            perf["n"] += 1
            perf["sum"] += d
            if d > perf["max"]:
                perf["max"] = d
        perf["last"] = nowp
        pace = POCHU_HOLD_TICK_PACE if hold else POCHU_TICK_PACE
        rem = pace - (time.time() - tick_start)
        if rem > 0:
            time.sleep(rem)

    mash = bool(mash) and not hold   # 冲刺连点仅点击模式可用
    hold_catch = bool(hold_catch) and hold  # 长按运输+短段长按维持仅长按模式可用
    act_icon = action_icon or POCHU_CLICK_ICON
    act_name = "长按" if hold else "点击"
    deadline = time.time() + POCHU_TIMEOUT
    started = False
    btn_pt = None                 # 「开始/流程按钮」位置（同位置，命中刷新、未命中缓存续按）
    last_seen = time.time()       # 标题最近命中时间（消失确认窗口起点）
    last_click = 0.0
    last_p = None                 # 点击模式最近一次点击前测量的 P（停顿告警用）
    last_meas_p = None            # 每轮最新测量 P（结束描述/命中判定用）
    stall_clicks = 0
    over_cnt = 0                  # P>R 连续轮数（过头确认防抖）
    terminal = None               # 完全停手原因（按钮保持/倒计时/过头）
    cnt_cache = {"crop": None, "text": None}
    btn_cache = {"crop": None, "text": None}
    last_ocr = 0.0
    last_log = 0.0
    last_zone_warn = 0.0          # 目标带暂未识别（沿用缓存区间）告警限频
    last_cnt = None               # 最近一次读到的倒计时（结束描述用）
    clicks = 0                    # 推进动作次数（点击次数/长按按下次数）
    zone = None                   # 最近一次 (L, R, C) 刻度（缓存，遮挡时沿用）
    holder = None                 # 长按模式当前按住的鼠标（None=未按住）
    hold_p0 = None                # 本次按下时的 P（长按不动告警）
    hold_t0 = 0.0
    hold_stall_logged = False
    mash_ratio = None             # 冲刺模式：在线估算每击推进量（刻度/击）
    mash_ratio_best = None        # 历史最好每击推进量观测（拉扯期观测被污染，按它兜底）
    mash_press = POCHU_MASH_PRESS  # 冲刺点击按压时长（漏注册时加压）
    mash_mark = None              # 冲刺模式：(clicks, p) 稳定锚点
    mash_stall = 0                # 连续「点击未推进」冲刺次数
    mash_waiting = False          # 冲刺后等待填充条追帧停稳（停稳前不决策）
    mash_prev_p = 0.0             # 上轮 P（判定填充条是否仍在上升）
    mash_stable_cnt = 0           # 连续「不再上升」轮数（停稳确认）
    cnt_none_polls = 0            # 倒计时消失连续轮数（结算画面判定，mash 停手用）
    mash_hold = False             # 已进入目标带 → 维持泵模式（禁止长空窗）
    mash_inflight = []            # 在途点击台账 [投递时刻, 击数, 投递时P, 峰值P]（有效位置用）
    mash_fight = False            # 带下拉扯：点击迟迟未兑现（鱼自动回退吃掉推进）
    mash_fight_ref = None         # 拉扯观察窗口起点 [时刻, P, 点击累计]
    last_fight_log = 0.0          # 拉扯告警限频（诊断日志，5s 一条）
    press_cnt = 0                 # 长按运输模式：按住次数（限次防失控）
    last_release = 0.0            # 长按运输模式：上次松手时间（重点间隔）
    catch_pump = False            # 长按运输模式：已切短段长按维持
    catch_lo = POCHU_CATCH_HOLD_LO  # 维持按住阈值（P ≤ C-该值按住），自适应微调
    catch_hi = POCHU_CATCH_HOLD_HI  # 维持松手阈值（P ≥ C-该值松手），自适应微调
    catch_adapt_t = 0.0           # 维持段冲出带上缘的自适应微调冷却（秒）
    last_debug = 0.0              # 调试图保存限频
    debug_seq = 0                 # 调试图序号（单次小游戏）
    fail_cnt = 0                  # 「游戏失败」结果连续命中轮数（fail_detect）
    log(f"[{prefix}] {label}弹窗出现 {time.strftime('%H:%M:%S')}，进入闭环控制", "SYS")

    def _release_hold():
        """释放长按（窗口结束/战斗打断/异常统一出口；未按住无操作）。"""
        nonlocal holder
        if holder is not None:
            holder.stop()
            holder = None

    def _live():
        """点击前新帧复检窗口在场（2026-11-20 用户规则：模板不在就不点）。

        窗口消失后缓存按钮坐标上的点击会落到世界画面让人物走动；复检
        失败时顺带释放长按（防止鼠标卡在按下状态），本帧不再操作。
        """
        if _window_present(win, title_icon):
            return True
        _release_hold()
        return False

    def _mash_ratio_eff():
        """每击推进量估算（含安全系数），拉扯期用历史最好观测兜底。

        正常期沿用在线混合估计（适配 0.05~0.25 刻度/击的真实差异）。
        **拉扯期不能用当前估计决策**：回退会吃掉大部分兑现，观测到的
        净推进塌向 0，按它放大批次会一次性冲过右缘（2026-11-21 仿真
        0.22/轮回退：观测塌到 0.03，11 击从 0.12 顶到 2.87，越过
        R=2.0）。每击推进量是游戏常量，历史最好观测才是可信上界。
        """
        r = mash_ratio or POCHU_MASH_RATIO0
        if mash_fight and mash_ratio_best:
            r = max(r, mash_ratio_best)
        return r * POCHU_MASH_RATIO_SAFETY

    def _mash_pos_eff(now, p):
        """有效位置 = 显示 P + 在途（显示尚未追上）点击的推进量。

        连点后填充条追帧延迟约 1s（2026-11-10 实测 14 连点 1s 后才
        到位；2026-11-19 实测按显示值停泵后队列点击又把游标推高 0.91
        刻度、越过真实右缘 0.5 格导致游戏判「超出目标范围」）。
        每批点击按投递时显示的 P（p0）记账：**该批投递后显示的峰值**
        已涨到的部分视为已兑现，只把尚未兑现的击数折算进有效位置——
        既不重复追加在途点击，也不会因填充条自然回退把已兑现点击
        重新算成在途（那会把泵饿死、掉出带下）。台账条目统一在
        POCHU_MASH_LAG 秒后过期（大批次追帧可达 1s）；**计入有效位置**
        只取阶段窗口内：冲刺段 POCHU_MASH_LAG、维持段
        POCHU_MASH_LAG_HOLD（≤2 击/批追帧 ~0.3s，太长会让泵误以为位置
        够高、少补点，保持阶段向左滑出带）。过期出窗口但仍在台账内的
        条目由 _mash_unshown 用作「落地门」——在途批次未兑现前不补点，
        防按滞后值重复追加把游标顶过右缘（2026-11-19 教训）。
        """
        nonlocal mash_inflight
        # 拉扯期用完整在途窗口记账：此时批次兑现被回退吃掉，0.35s 的
        # 短窗口会让泵误以为位置够低、反复补点（1s 窗口把每秒补点量
        # 限在「目标点-当前」的预算内，压过回退但不会叠推过右缘）
        lag = POCHU_MASH_LAG if (not mash_hold or mash_fight) \
            else POCHU_MASH_LAG_HOLD
        ratio = _mash_ratio_eff()
        fresh = []
        pending = 0.0
        for t, n, p0, p_peak in mash_inflight:
            age = now - t
            if age >= POCHU_MASH_LAG:
                continue
            p_peak = max(p_peak, p)
            fresh.append([t, n, p0, p_peak])
            if age >= lag:
                continue
            unshown = n * ratio - max(0.0, p_peak - p0)
            if unshown > 0:
                pending += unshown
        mash_inflight = fresh
        return p + pending

    def _mash_unshown(now, p):
        """在途未兑现的点击量（POCHU_MASH_LAG 窗口）：>0 时维持泵不补点。

        维持段信用窗口短（0.35s）能快速补偿衰减，但连点批次落地前若
        重复补点，追帧滞后会把游标叠推过右缘；此处用完整窗口做落地门，
        批次兑现（峰值上涨）后自然放行。
        """
        ratio = _mash_ratio_eff()
        total = 0.0
        for t, n, p0, p_peak in mash_inflight:
            if now - t >= POCHU_MASH_LAG:
                continue
            p_peak = max(p_peak, p)
            total += max(0.0, n * ratio - max(0.0, p_peak - p0))
        return total

    def _mash_fight_update(now, p):
        """带下拉扯检测：点击投递后 P 在观察窗口内不涨 = 回退在吃掉推进。

        2026-11-21 蛟魔探秘第一次失败：目标带 L=5.0 远带，18 击冲到
        P=4.49 后，后续 12 击只换回 +0.07（回退 ~0.5 刻度/秒），而控制器
        按在途信用决策、落地门每秒只放 2 击，恰好与回退持平 → P 一路
        阴跌判失败。判定条件：观察窗口内有新点击且 P 涨幅
        < POCHU_MASH_FIGHT_EPS；P 一涨过该值即解除（回退被压过）。
        触发时补点目标放宽到硬上限、跳过落地门（见 POCHU_MASH_FIGHT_*）。
        """
        nonlocal mash_fight, mash_fight_ref
        if mash_fight_ref is None \
                or p > mash_fight_ref[1] + POCHU_MASH_FIGHT_EPS:
            mash_fight_ref = [now, p, clicks]
            mash_fight = False
            return mash_fight
        if clicks > mash_fight_ref[2] \
                and now - mash_fight_ref[0] >= POCHU_MASH_FIGHT_WINDOW:
            mash_fight = True
        return mash_fight

    def _maintain_pump(now, p, bgr, L, R, fight=False):
        """维持泵：按有效位置把填充悬停在目标带中上段（mash 维持段）。

        2026-11-19 重做（原点击率伺服按显示值决策，追帧滞后叠点冲过
        右缘）：补点量 = 有效位置到目标点 L+POCHU_MASH_PUMP_AIM 的
        差值 / 每击推进量，单轮上限 POCHU_MASH_HOLD_MAX；有效位置
        达到目标点或硬上限 R-POCHU_MASH_TOP_MARGIN 一律不补。倒计时
        消失多轮=已结算，停手防结果画面空点。hold_catch 的维持段不用
        本函数（长按窗口走 MouseHold 短段长按 bang-bang，见
        POCHU_CATCH_HOLD_*）。
        fight=True（带下拉扯）：回退会把每批点击的兑现吃掉一部分，落地
        门永远等不到兑现 → 松开落地门、目标点改取硬上限，有效位置仍
        限幅（在途信用不被重复追加），把填充压回带内即自动恢复常规泵。
        """
        nonlocal mash_waiting, clicks, last_click, btn_pt
        mash_waiting = False
        if cnt_none_polls >= POCHU_MASH_CNT_GONE_POLLS:
            return
        aim = L + POCHU_MASH_PUMP_AIM
        top = R - POCHU_MASH_TOP_MARGIN
        if fight:
            aim = max(aim, top)
        pos_eff = _mash_pos_eff(now, p)
        if pos_eff >= aim or pos_eff >= top:
            return
        if not fight and _mash_unshown(now, p) > POCHU_MASH_UNSHOWN_GATE:
            # 上一批点击还没反映到填充条：等落地再补点，防追帧滞后叠推过右缘
            return
        if now - last_click < POCHU_MASH_PUMP_GAP:
            return
        ratio = _mash_ratio_eff()
        n = int((min(aim, top) - pos_eff) / ratio)
        if fight:
            # 拉扯期不强行补「不足一击」的余量：回退会把兑现吃掉，末次
            # 补点的真实落点已贴硬上限，多补一击就会越过右缘
            n = min(n, POCHU_MASH_HOLD_MAX)
        else:
            n = max(1, min(n, POCHU_MASH_HOLD_MAX))
        if n <= 0:
            return
        dpt = game_map.match_frame_np(
            bgr, act_icon, threshold=POCHU_THRESHOLD,
            region=POCHU_REGION, fallback=False)
        if dpt is not None:
            btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
        if btn_pt is not None and _live():
            rapid_click_burst(win, btn_pt[0], btn_pt[1], n,
                              press=mash_press)
            clicks += n
            last_click = now
            mash_inflight.append([now, n, p, p])

    try:
        while time.time() < deadline:
            t0 = time.time()
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.1)
                continue
            bgr = cv2.cvtColor(np.asarray(img)[:, :, :3], cv2.COLOR_RGB2BGR)
            if is_in_battle_frame(bgr):
                return False, f"战斗开始，{label}中断"
            if fail_detect:
                # 失败结果（标题仍在的面板内红色「游戏失败」）：判断须在
                # 所有推进决策之前——结果态下继续点击按钮毫无意义，且
                # 结束时的位置命中猜测会把失败误报成成功（2026-11-17
                # 蛟魔探秘 13:00 实测：P 在带内但游戏判负，任务卡住）。
                if game_map.match_frame_np(
                        bgr, POCHU_FAIL_ICON,
                        threshold=POCHU_FAIL_THRESHOLD,
                        region=POCHU_FAIL_REGION,
                        fallback=False) is not None:
                    fail_cnt += 1
                    if fail_cnt >= POCHU_FAIL_CONFIRM:
                        state = (f"P={last_meas_p:.2f}"
                                 if last_meas_p is not None else "P=未知")
                        if zone is not None:
                            state += f" 区间[{zone[0]:.1f},{zone[1]:.1f}]"
                        if last_cnt is not None:
                            state += f" 倒计时={last_cnt}s"
                        log(f"[{prefix}] {label}：检测到「游戏失败」，停止操作"
                            f"（{state}，{act_name} {clicks} 次）", "WARN")
                        return False, (f"{label}游戏失败（{state}，"
                                       f"{act_name} {clicks} 次）")
                else:
                    fail_cnt = 0
            title = game_map.match_frame_np(bgr, title_icon,
                                            threshold=POCHU_THRESHOLD,
                                            region=POCHU_REGION, fallback=False)
            if title is None:
                # 标题消失 = 小游戏结束。连续消失满 POCHU_END_WINDOW
                # 秒才确认结束（结束确认 2 秒窗口防单帧误判，不得缩短）
                if time.time() - last_seen >= POCHU_END_WINDOW:
                    ok = terminal is not None and "保持" in terminal
                    if zone is not None and last_meas_p is not None:
                        # 普通点击模式保留 ±0.3 容差（按下的量化跳变）；
                        # 长按/冲刺模式是连续测量，按精确区间判定命中——
                        # ±0.3 会把越界（2026-11-19 破除封印 P=7.07 >
                        # R=7.0、2026-11-17 蛟魔探秘 P=1.99 > R=1.88）误报
                        # 成「命中区间」，游戏实际已判失败
                        tol = 0.3 if not (hold or mash) else 0.0
                        ok = ok or zone[0] - tol <= last_meas_p <= zone[1] + tol
                    state = (f"P={last_meas_p:.2f}" if last_meas_p is not None
                             else "P=未知")
                    if zone is not None:
                        state += f" 区间[{zone[0]:.1f},{zone[1]:.1f}]"
                    if last_cnt is not None:
                        state += f" 倒计时={last_cnt}s"
                    perf_txt = ""
                    if perf["n"]:
                        perf_txt = (f"，采样{perf['n']}轮 平均"
                                    f"{perf['sum'] / perf['n'] * 1000:.0f}ms"
                                    f"/最大{perf['max'] * 1000:.0f}ms")
                    return True, (f"{label}结束（{state}，{act_name} {clicks} 次，"
                                  f"{'命中区间' if ok else '未确认命中'}{perf_txt}）")
                _pace(t0)
                continue
            last_seen = time.time()
            now = time.time()
            if not started:
                # 「开始」出现则点击；若按钮已是流程按钮态（手动已开/续接
                # 场景），直接进入闭环不点
                kpt = game_map.match_frame_np(bgr, POCHU_START_ICON,
                                              threshold=POCHU_THRESHOLD,
                                              region=POCHU_REGION, fallback=False)
                dpt = game_map.match_frame_np(bgr, act_icon,
                                              threshold=POCHU_THRESHOLD,
                                              region=POCHU_REGION, fallback=False)
                if kpt is not None:
                    if not _live():
                        _pace(t0)
                        continue
                    human_click(win, kpt[0], kpt[1])
                    btn_pt = kpt
                    started = True
                    log(f"[{prefix}] {label}：已点击「开始」@({kpt[0]},{kpt[1]})", "SYS")
                elif dpt is not None:
                    btn_pt = dpt
                    started = True
                    log(f"[{prefix}] {label}：按钮已是「{act_name}」态 "
                        f"@({dpt[0]},{dpt[1]})，直接进入闭环", "SYS")
                _pace(t0)
                continue
            # ---- 已开始：闭环决策（同帧测量）----
            if hold and (holder is not None or hold_catch):
                # 长按运输模式全程跳过区域 OCR：单次 OCR 100~400ms，会把
                # 采样间隔拉长到 0.3~0.5s，而填充推进/回退 ~1~2.4 刻度/秒
                # （2026-11-12 钓鱼日志：回落捕捉后一轮就跌 0.67 刻度，
                # 维持泵跟不上）。该模式倒计时不再用于停手判定、按钮
                # 「保持」文案由标题消失兜底——OCR 只剩拖慢采样的副作用。
                # 普通长按在按住期间同样跳过（松开后的轮询再读）。
                cnt = btn = None
                last_cnt = None
            else:
                force_ocr = now - last_ocr >= POCHU_OCR_REFRESH
                if force_ocr:
                    last_ocr = now
                cnt = _pochu_countdown(img, cnt_cache, force=force_ocr)
                if cnt is not None:
                    last_cnt = cnt
                    cnt_none_polls = 0
                elif mash and last_cnt is not None:
                    # 倒计时曾读到、随后连续消失 = 小游戏已结算（结果画面），
                    # 冲刺模式据此停手，防在结算画面继续空点
                    cnt_none_polls += 1
                btn = _pochu_ocr_gated(img, POCHU_BTN_REGION, btn_cache,
                                       force=force_ocr)
            lx, rx, cx = _pochu_zone(bgr)
            if cx is None:
                if zone is None:
                    # 从未识别到目标带：0.5s 重试（无区间无法决策）
                    if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                        last_log = now
                        log(f"[{prefix}] {label}：目标带未识别，0.5s 后重试", "WARN")
                    _pace(t0)
                    continue
                # 填充前缘接近/遮住目标括号时模板匹配会掉到阈值下
                # （2026-11-10 实测：长按推进至带前必现）——沿用上次
                # 识别到的区间继续决策，否则永远等不到释放时机
                L, R, C = zone
                if now - last_zone_warn >= POCHU_STATE_LOG_INTERVAL:
                    last_zone_warn = now
                    log(f"[{prefix}] {label}：目标带暂未识别（可能被填充前缘"
                        f"遮挡），沿用上次区间 L={L:.1f} R={R:.1f} C={C:.1f}",
                        "WARN")
            else:
                # 目标带 = target 模板白括号中心 ±POCHU_ZONE_HALF 的实测
                # 刻度，不做偏移：游戏判定的就是这段括号区间（2026-11-19
                # 实机帧量测括号 713~771px ↔ [4.99,6.03] 与模板完全一致；
                # 旧 mash +0.2 偏移让泵悬停在真实右缘附近、结束时越界仍
                # 按偏移区间误报「命中区间」）
                L, R, C = pochu_tick(lx), pochu_tick(rx), pochu_tick(cx)
                if zone is None or abs(zone[2] - C) > 0.15:
                    zone = (L, R, C)
                    log(f"[{prefix}] {label}：目标带 L={L:.1f} R={R:.1f} C={C:.1f}"
                        f"（已{act_name} {clicks} 次）", "SYS")
            px = pochu_fill_edge(bgr)
            p = 0.0 if px is None else pochu_tick(px)
            # 每轮记录最新测量 P：结束描述/命中判定用。点击模式 last_p 只在
            # 点击时更新，0 次点击（填充一开始就在带内）时是 None——不能拿它
            # 做结束判定，否则误报「P=未知/未确认命中」（2026-11-11 客栈实测）
            last_meas_p = p
            if hold_catch and POCHU_DEBUG_DUMP and not _UNDER_PYTEST \
                    and debug_seq < POCHU_DEBUG_MAX \
                    and now - last_debug >= POCHU_DEBUG_INTERVAL:
                last_debug = now
                debug_seq += 1
                _pochu_debug_dump(bgr, p, px, zone, debug_seq, prefix)
            # 完全停手判定（优先；terminal 一旦建立不再恢复）
            if terminal is None:
                if btn and "保持" in btn and not mash:
                    # 冲刺模式的「保持」是最后 3 秒保持阶段的指令（倒计时
                    # 重置 3→0），不是停手信号——停手填充回退出带即判失败
                    # （2026-11-20 实机教训），mash 继续由维持泵保持
                    terminal = f"按钮变为「{btn}」"
                elif not mash and not hold_catch and cnt is not None \
                        and cnt <= POCHU_CNT_STOP:
                    # 冲刺/长按运输模式不禁点：该游戏填充条会持续回退，
                    # 提前停手等于让游标掉出目标带（用户 2026-11-10
                    # 实测教训；钓鱼倒计时 3s 时仍须维持到结束，
                    # 2026-11-12），维持到窗口标题消失为止
                    terminal = f"倒计时 {cnt}s≤{POCHU_CNT_STOP}s"
                elif p > R + (POCHU_HOLD_OVER_MARGIN if hold else 0.0):
                    over_cnt += 1
                    if over_cnt >= 2 and not hold and not mash:
                        # 点击模式：过头永久停手。长按模式不设永久终态
                        # （松开回落、回到 L 以下再次按住）；冲刺模式也不设
                        # 永久终态——维持泵在 P≥R-余量 已自动停泵，追帧
                        # 滞后造成的瞬时越界应等回落，停死会丢失维持
                        terminal = f"过头 P={p:.2f}>R={R:.2f}"
                else:
                    over_cnt = 0
                if terminal is not None:
                    log(f"[{prefix}] {label}：{terminal}，完全停手等待结算", "SYS")
            if hold and hold_catch:
                # 长按运输 + 短段长按维持（开采石头 2026-11-10 / 钓鱼
                # 2026-11-12 用户技巧）：
                # 1) 运输：长按把填充推过目标带上缘（P≥R+POCHU_CATCH_ABOVE）
                # 2) 松手：填充整体回落（该游戏松手即回退）
                # 3) 捕捉：回落到 C（带中心）切换短段长按维持，围绕 C
                # 4) 兜底：维持谷值仍落出带下 → 重新长按运输（限次）
                if catch_pump:
                    # 短段长按 bang-bang：P≤C-LO 按住、P≥C-HI 松手。
                    # 长按升 ~3.6 刻度/秒、松手回落 ~1.4 刻度/秒，松手
                    # 动作滞后 ~0.1s（峰值 ≈ 测得值+0.36）——HI 提前量
                    # 就是补偿它，使实际峰值落在 C 附近；LO 提前量补偿
                    # 按下生效滞后，使实际谷值不跌破 L。掉出带下/冲出带
                    # 上缘时按方向微调 LO/HI 自校准（限幅防反转）。
                    if holder is not None:
                        if p >= C - catch_hi:
                            _release_hold()
                            last_release = now
                            if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                                last_log = now
                                log(f"[{prefix}] {label}：维持松手 P={p:.2f}"
                                    f"（阈值 C-{catch_hi:.2f}）", "SYS")
                        elif not hold_stall_logged and hold_p0 is not None \
                                and now - hold_t0 >= POCHU_HOLD_STALL \
                                and abs(p - hold_p0) < 0.05:
                            # 维持按住但 P 不动：长按通道可能未生效，
                            # 释放后下轮重按自愈
                            hold_stall_logged = True
                            log(f"[{prefix}] {label}：维持按住 "
                                f"{POCHU_HOLD_STALL:.0f}s P 未移动"
                                f"（P={p:.2f}），释放重按", "WARN")
                            _release_hold()
                            last_release = now
                    elif p < L - POCHU_CATCH_REPRESS_MARGIN \
                            and press_cnt < POCHU_CATCH_MAX_PRESS \
                            and now - last_release >= POCHU_CATCH_MIN_GAP:
                        catch_pump = False
                        # 维持失败（谷值跌破带下）→ 下次提前按住
                        # （LO 减小 = 在更高位置按住）
                        catch_lo = max(POCHU_CATCH_HOLD_LO_MIN,
                                       catch_lo - POCHU_CATCH_ADAPT_STEP)
                        log(f"[{prefix}] {label}：P={p:.2f} 掉出带下"
                            f"（L-{POCHU_CATCH_REPRESS_MARGIN:.2f}），"
                            f"重新长按运输（维持按住阈值提前至 "
                            f"C-{catch_lo:.2f}）", "WARN")
                    elif p >= R + POCHU_CATCH_ABOVE:
                        # 维持段冲出带上缘：松手太晚 → 下次更早松手。
                        # 一次过冲会连续若干轮满足条件——限频 1s 只微调
                        # 一次，防一次事件把 HI 累计推大
                        if now - catch_adapt_t >= 1.0:
                            catch_adapt_t = now
                            catch_hi = min(POCHU_CATCH_HOLD_HI_MAX,
                                           catch_hi + POCHU_CATCH_ADAPT_STEP)
                            if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                                last_log = now
                                log(f"[{prefix}] {label}：维持段 P={p:.2f} 冲出"
                                    f"带上缘，松手阈值提前至 C-{catch_hi:.2f}",
                                    "WARN")
                    elif p <= C - catch_lo \
                            and now - last_release >= POCHU_CATCH_MIN_GAP:
                        dpt = game_map.match_frame_np(
                            bgr, act_icon, threshold=POCHU_THRESHOLD,
                            region=POCHU_REGION, fallback=False)
                        if dpt is not None:
                            btn_pt = dpt  # 命中刷新按钮位置（防漂移）
                        # 按下后按钮外观会变，模板不命中时仍按缓存坐标按
                        # （窗口标题须在新帧仍在：消失后按空会拖世界画面）
                        if btn_pt is not None and _live():
                            h = MouseHold(win, btn_pt[0], btn_pt[1])
                            if h.start():
                                holder = h
                                clicks += 1
                                hold_p0 = p
                                hold_t0 = now
                                hold_stall_logged = False
                                if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                                    last_log = now
                                    log(f"[{prefix}] {label}：维持按住"
                                        f"@({btn_pt[0]},{btn_pt[1]}) "
                                        f"P={p:.2f}（阈值 C-{catch_lo:.2f}）",
                                        "SYS")
                elif holder is not None:
                    if p >= R + POCHU_CATCH_ABOVE:
                        _release_hold()
                        last_release = now
                        log(f"[{prefix}] {label}：P={p:.2f} 已越过目标带上缘"
                            f"（R+{POCHU_CATCH_ABOVE:.2f}），松手等回落"
                            f"至 C={C:.2f} 后短段长按维持", "SYS")
                    elif not hold_stall_logged and hold_p0 is not None \
                            and now - hold_t0 >= POCHU_HOLD_STALL \
                            and abs(p - hold_p0) < 0.05 and p < L - 0.2:
                        # 按住无进度（长按通道可能未生效）：释放后重按自愈
                        hold_stall_logged = True
                        log(f"[{prefix}] {label}：已长按 {POCHU_HOLD_STALL:.0f}s"
                            f" P 未移动（P={p:.2f}），释放重按并检查长按通道",
                            "WARN")
                        _release_hold()
                        last_release = now
                elif terminal is None:
                    if p < L - POCHU_CATCH_REPRESS_MARGIN:
                        # 尚未开始/回落途中已低于带下：长按运输
                        if press_cnt < POCHU_CATCH_MAX_PRESS \
                                and now - last_release >= POCHU_CATCH_MIN_GAP:
                            dpt = game_map.match_frame_np(
                                bgr, act_icon, threshold=POCHU_THRESHOLD,
                                region=POCHU_REGION, fallback=False)
                            if dpt is not None:
                                btn_pt = dpt  # 命中刷新按钮位置（防漂移）
                            # 按下后按钮外观会变，模板不命中时仍按缓存坐标按
                            # （窗口标题须在新帧仍在：消失后按空会拖世界画面）
                            if btn_pt is not None and _live():
                                h = MouseHold(win, btn_pt[0], btn_pt[1])
                                if h.start():
                                    holder = h
                                    press_cnt += 1
                                    clicks += 1
                                    hold_p0 = p
                                    hold_t0 = now
                                    hold_stall_logged = False
                                    log(f"[{prefix}] {label}：按住「{act_name}」"
                                        f"@({btn_pt[0]},{btn_pt[1]}) P={p:.2f}，"
                                        f"第 {press_cnt}/{POCHU_CATCH_MAX_PRESS}"
                                        f" 次长按运输", "SYS")
                                elif now - last_log >= POCHU_STATE_LOG_INTERVAL:
                                    last_log = now
                                    log(f"[{prefix}] {label}：长按按下失败"
                                        f"（窗口坐标不可用），下轮重试", "WARN")
                    elif p <= C:
                        # 回落到带中心 C：切入短段长按维持
                        catch_pump = True
                        log(f"[{prefix}] {label}：已回落至 C={C:.2f}"
                            f"（区间[{L:.1f},{R:.1f}]），切换短段长按维持"
                            f"（按住阈值 C-{catch_lo:.2f} / 松手阈值 "
                            f"C-{catch_hi:.2f}）", "SYS")
            elif hold:
                # 纯长按（锁定持握）模式：按住不放，假定游戏会把游标
                # 锁定在目标区间，松手即回退。进入区间后保持按住直到
                # 窗口结束，不做 P≥L 释放。唯一例外：P>R 连续 2 轮
                # （未锁定、冲过右缘）——松开让其回落，回落到 L 以下
                # 会再次按住（终态不永久锁死）。钓鱼 2026-11-12 日志
                # 证实进带不锁定（按住冲过带上缘），已改用 hold_catch。
                if holder is not None and over_cnt >= 2:
                    _release_hold()
                    over_cnt = 0
                    log(f"[{prefix}] {label}：P={p:.2f}>R+余量"
                        f"={R + POCHU_HOLD_OVER_MARGIN:.2f} 冲过目标带，"
                        f"释放长按让其回落（回到 L 以下再按住）", "WARN")
                if terminal is None and p < L and holder is None:
                    dpt = game_map.match_frame_np(bgr, act_icon,
                                                  threshold=POCHU_THRESHOLD,
                                                  region=POCHU_REGION,
                                                  fallback=False)
                    if dpt is not None:
                        btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                    # 按下后按钮外观会变（用户 2026-11-10 确认），模板
                    # 不命中时仍按缓存坐标按下/续按
                    # （窗口标题须在新帧仍在：消失后按空会拖世界画面）
                    if btn_pt is not None and _live():
                        h = MouseHold(win, btn_pt[0], btn_pt[1])
                        if h.start():
                            holder = h
                            clicks += 1
                            hold_p0 = p
                            hold_t0 = now
                            hold_stall_logged = False
                            log(f"[{prefix}] {label}：按住「{act_name}」"
                                f"@({btn_pt[0]},{btn_pt[1]}) P={p:.2f}<L={L:.1f}，"
                                f"持续推进", "SYS")
                        elif now - last_log >= POCHU_STATE_LOG_INTERVAL:
                            last_log = now
                            log(f"[{prefix}] {label}：长按按下失败（窗口坐标"
                                f"不可用），下轮重试", "WARN")
                elif holder is not None and not hold_stall_logged \
                        and hold_p0 is not None \
                        and now - hold_t0 >= POCHU_HOLD_STALL \
                        and abs(p - hold_p0) < 0.05 and p < L - 0.2:
                    # 仍在区间左侧却按住无进度：坐标/静默长按通道可能未
                    # 生效 → 释放后下一轮重按一次（down 消息漏消费自愈）。
                    # p 已进区间时不动：那是游戏把游标锁定在目标带的正常
                    # 现象，不能按「无进度」处理（否则松手回退脱钩）。
                    hold_stall_logged = True
                    log(f"[{prefix}] {label}：已长按 {POCHU_HOLD_STALL:.0f}s P 未移动"
                        f"（P={p:.2f}），释放重按并检查按钮坐标/长按通道", "WARN")
                    _release_hold()
            elif terminal is None and mash:
                # 冲刺连点（蛟魔探秘钓鱼/客栈破除封印）两段式：
                # 1) 接近段（有效位置在带口以下）：按有效位置与在线估算
                #    的每击推进量连点冲刺，每次冲刺后等填充条追帧停稳再
                #    决策（防按滞后值过量；在途点击不会被重复追加）
                # 2) 维持段：显示 P 到带口（L-0.15）切「维持泵」，按有效
                #    位置（显示 P + 在途点击折算）补点，悬停在目标带中心；
                #    一直维持到窗口标题消失（不按倒计时停手）
                # 3) 带下拉扯补偿：点击迟迟不兑现时放宽补点目标/跳过落地门
                #    （2026-11-21 第一次失败标定，见 POCHU_MASH_FIGHT_*）
                was_fight = mash_fight
                fight = _mash_fight_update(now, p)
                if fight and not was_fight \
                        and now - last_fight_log >= 5.0:
                    last_fight_log = now
                    log(f"[{prefix}] {label}：{POCHU_MASH_FIGHT_WINDOW:.1f}s 内"
                        f"点击未上涨（P={p:.2f}，填充被自动回退抵消），"
                        f"放宽补点目标至 R-{POCHU_MASH_TOP_MARGIN:.2f} 压过回退",
                        "WARN")
                if p >= L - POCHU_MASH_HOLD_ENTER and not mash_hold:
                    mash_hold = True
                if mash_hold:
                    _maintain_pump(now, p, bgr, L, R, fight=fight)
                if not mash_hold and mash_waiting:
                    if p > mash_prev_p + POCHU_MASH_RISE_EPS:
                        # 填充条仍在上升（追帧/点击队列处理中）：不决策，
                        # 按滞后值补点会过量冲刺
                        mash_prev_p = p
                        mash_stable_cnt = 0
                    else:
                        mash_stable_cnt += 1
                    if mash_waiting and mash_stable_cnt >= POCHU_MASH_STABLE_POLLS:
                        # 连续多轮不再上升 = 停稳：用停稳值更新每击推进量
                        mash_stable_cnt = 0
                        if mash_mark is not None:
                            dc = clicks - mash_mark[0]
                            dp = p - mash_mark[1]
                            if dc >= 1 and dp > 0.03:
                                obs = dp / dc
                                if POCHU_MASH_RATIO_MIN <= obs \
                                        <= POCHU_MASH_RATIO_MAX:
                                    mash_ratio = obs if mash_ratio is None \
                                        else mash_ratio * 0.4 + obs * 0.6
                                    # 历史最好观测：拉扯期净推进被回退污染，
                                    # 批次估算按它兜底（见 _mash_ratio_eff）
                                    mash_ratio_best = obs \
                                        if mash_ratio_best is None \
                                        else max(mash_ratio_best, obs)
                                mash_stall = 0
                            elif dc >= POCHU_MASH_MIN_CLICKS and dp <= 0.02:
                                mash_stall += 1
                                if mash_stall == 2:
                                    mash_press = min(POCHU_MASH_PRESS_MAX,
                                                     mash_press + 0.015)
                                    log(f"[{prefix}] {label}：连点 {dc} 次"
                                        f" P 未推进（P={p:.2f}），按压时长增至"
                                        f" {mash_press:.3f}s（检查点击漏注册）",
                                        "WARN")
                        mash_mark = (clicks, p)
                        mash_waiting = False
                if not mash_hold and not mash_waiting:
                    goal = L + POCHU_MASH_AIM
                    if fight:
                        # 带下拉扯：在途信用会一直把回退吃掉的兑现算成
                        # 「已推进」，冲刺目标仍按 L+0.45 会永远等不到
                        # 兑现（2026-11-21 实测 12 击换回 +0.07）——目标
                        # 放宽到硬上限，按信用窗口反复压点把填充顶回带内
                        goal = max(goal, R - POCHU_MASH_TOP_MARGIN)
                    n = 0
                    # 冲刺按有效位置决策：上一批点击还在途时不再按显示
                    # 值重复冲一轮（停稳确认只有 2 轮 ≈0.3s，短于追帧
                    # 延迟时会把在途点击重复追加，2026-11-19 教训）
                    pos_eff = _mash_pos_eff(now, p)
                    if pos_eff < goal - POCHU_MASH_DEADBAND:
                        n = int((goal - pos_eff) / _mash_ratio_eff())
                        n = max(1, min(n, POCHU_MASH_MAX_BURST))
                    if n > 0:
                        dpt = game_map.match_frame_np(
                            bgr, act_icon, threshold=POCHU_THRESHOLD,
                            region=POCHU_REGION, fallback=False)
                        if dpt is not None:
                            btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                        if btn_pt is not None and _live():
                            # 整批一次定位/一把锁，逐击按下抬起（静默通道
                            # ~20ms/击；逐击 rapid_click 静默 ~150ms/击，
                            # 倒计时内推不到目标带）
                            mash_mark = (clicks, p)
                            mash_prev_p = p
                            rapid_click_burst(win, btn_pt[0], btn_pt[1], n,
                                              press=mash_press)
                            clicks += n
                            last_click = now
                            mash_inflight.append([now, n, p, p])
                            mash_waiting = True
            elif terminal is None and p < C:
                # 点击决策：推进到目标带中心 C 才停手（节奏按 C 三档）。
                # 不能只推到 L 就停：破除封印松手后填充会回落，停在左缘
                # 有一点回落就掉出带下（用户 2026-11-11：P=0.05 虽 >0 但
                # 有风险），落在 C 附近上下各留 ~0.5 余量
                if C < 4.0:
                    interval = POCHU_TIER_NEAR
                elif C <= 7.0:
                    interval = POCHU_TIER_MID
                else:
                    interval = POCHU_TIER_FAR
                if now - last_click >= interval:
                    dpt = game_map.match_frame_np(bgr, act_icon,
                                                  threshold=POCHU_THRESHOLD,
                                                  region=POCHU_REGION,
                                                  fallback=False)
                    if dpt is not None:
                        btn_pt = dpt  # 命中刷新按钮位置（防窗口漂移）
                    # 按钮在过程中会变成其他外观但坐标不变（用户 2026-11-09
                    # 确认），模板不命中时仍按缓存坐标续点
                    # （窗口标题须在新帧仍在：消失后点空会拖世界画面）
                    if btn_pt is not None and _live():
                        human_click(win, btn_pt[0], btn_pt[1])
                        clicks += 1
                        last_click = now
                        # P 不动告警：点击没生效检查按钮坐标
                        if last_p is not None and abs(p - last_p) < 0.05:
                            stall_clicks += 1
                            if stall_clicks == POCHU_STALL_CLICKS:
                                log(f"[{prefix}] {label}：已点击 {POCHU_STALL_CLICKS} 次"
                                    f" P 未移动（P={p:.2f}），检查按钮坐标/点击是否生效",
                                    "WARN")
                        else:
                            stall_clicks = 0
                        last_p = p
            if now - last_log >= POCHU_STATE_LOG_INTERVAL:
                last_log = now
                if terminal is not None:
                    st = terminal
                elif p < (L if hold or mash else C):
                    # 点击模式推进目标是带中心 C（防松手回落掉出带下），
                    # 长按/冲刺模式仍以 L 为推进边界
                    st = f"{act_name}推进中 P={p:.2f}"
                elif p > R and not hold_catch:
                    # 长按运输的过冲是打法本身（推过带上缘再松手回落），
                    # 不算异常；其余模式越过右缘如实记录——旧日志把
                    # P=7.22 > R=7.2 也写成「保持区间内」，掩盖了越界
                    st = f"{act_name}推进中 P={p:.2f}（越过右缘 R={R:.1f}）"
                else:
                    st = f"保持区间内 P={p:.2f}"
                extra = f" 倒计时={last_cnt}s" if last_cnt is not None else ""
                if mash and mash_fight:
                    extra += " [拉扯补偿]"
                log(f"[{prefix}] {label}：{st} 区间[{L:.1f},{R:.1f}] C={C:.1f}"
                    f" {act_name}{clicks}次{extra}", "INFO")
            _pace(t0)
    finally:
        # 标题消失/战斗打断/异常退出统一抬起按键，防鼠标卡在按下状态
        _release_hold()
    return False, f"{label}超时（{POCHU_TIMEOUT}s，{act_name} {clicks} 次，P={last_meas_p}）"
