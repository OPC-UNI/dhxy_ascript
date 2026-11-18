"""名望任务自动化（山河名望）——第一阶段：魔族/仙族领取入口 + 面板领取。

流程（用户 2026-11-10 需求，先实现领取入口，后续流程再补充）：
1. 种族判定（按角色**每天**缓存一次：同一天内跨轮次/跨启动复用，
   不重复打开属性面板；角色可转种族，跨天自动重判）：点击窗口坐标
   (1230,95) 打开人物属性面板（用户标定）→
   匹配竖排标题牌 renwushuxing.png → OCR 标题牌右侧 120px 内的种族
   名称区：出现「男魔角色/女魔角色」等魔族角色列表文案 → 魔族；
   出现「女仙角色/精灵仙/霓裳仙」或角色名「武尊神/云中君/玄剑娥」
   （仙族部分角色名不含「仙」）→ 仙族。其他种族（人/鬼/龙/神巫）
   暂不识别（后续 TODO）。识别后关闭面板（右键标题牌 → 再点按钮 →
   Esc 三级兜底）。
2. 领取点（种族配置化，见 CLAIM_POINTS）——**小地图快捷领取是唯一
   路线**（用户 2026-11-16 规则：移除开关与旧路线，名望一律走小地图
   快捷领取）：
   - 魔族：大唐边境 NPC 逆天魔；仙族：天宫 NPC 武尊神。
   - 打开种族地图小地图（**已在目标地图则直接点位置区域开小地图**，
     否则经世界地图 → 点击种族地图标签）→ 勾选底部「任务」筛选 →
     点击地图上**固定位置**的黄色任务 NPC 名——游戏自动寻路/传送并在
     到达后自动打开对话框，直接点绿色选项「开启山河名望志」；不按坐标
     到达判定、不按 Alt+4 清屏、不做 NPC 模板匹配（模板会被玩家/摊位/
     对话框遮挡）。通用能力（开小地图/任务筛选/点击固定点）已下沉到
     `device/map.py` 的 `minimap_quick_navigate`；本模块只保留种族场景
     的**绝对坐标**标定（CLAIM_POINTS 的 map_filter_pos / map_label_pos
     ——小地图窗口位置固定，2026-11-16 用户规则，不做锚点相对换算）。
     黄字只点一次（重复点击会打断自动寻路），等待上限 120s。
3. 点击黄字后游戏自动打开对话框——【不使用对话框模板门控，直接区域
   OCR】点击绿色选项「开启山河名望志」→ 等待并点击山河名望入口
   （mingwang_shanhemingwang.png）打开任务领取面板。
4. 面板领取（2026-11-10 实机标定）：任务栏锚点
   mingwang_shanhemingwang_maodian.png（面板顶部图标）→ 锚点左下角
   推导 6 张任务卡（3 列 × 2 行、190×154、列距 201/行距 161）→ 逐卡
   OCR 级别（高级/中级/初级）与标题 → 按优先级 高级 > 中级 > 初级
   + 卡片顺序领取已实现任务（魔族：蛟魔复仇/沙海迷踪/蛟魔探秘；仙族：
   五庄封印；两族通用：沙都修罗）：在命中卡内匹配接取按钮
   mingwang_shanhemingwang_jiequ.png 点击领取，确认信号=接取按钮消失
   或任务进入追踪面板。
   沙都修罗（高级，2026-11-10 用户流程）：跟踪面板文案包含「根据三张
   蟠桃园残像所示位置」时触发搜索链——不在蟠桃园则经世界地图点
   shijie_pantaoyuan.png 打开蟠桃园小地图 → 依次在小地图点 (739,588)
   →(126,32)、(623,476)→(66,87)、(541,428)→(27,112)、(552,310)
   →(33,171)、(710,393)→(111,130)，每点到达后先查 NPC 本体模板
   mingwang_shaduxiuluo（阈值 0.79，命中直接点模板中心），未命中再
   OCR 世界区域找「沙都修罗」文案（必须排除任务追踪面板同名文案）
   → 找到点击文案上方 40px；全部路点均未找到限频重试。
   五庄封印（仙族，2026-11-10 用户规则 + 2026-11-17 动态元素）：
   追踪面板出现两个「X藏之位」绿字链接（五行任选两个：金/木/水/火/土，
   实机已见 金+土、水+火、火+木），点第一个；到达目的地（位置连续
   稳定，复用五倍显形镜的坐标稳定判定）后打开背包任务储物格（与
   修罗回城符同格）右键使用唤风符（mingwang_huanfengfu）。用符后
   当前链接由亮绿变红（可能立刻、也可能战斗结束后变红）或坐标
   离开五庄观即该点完成，改点另一个未完成的（按颜色选，完成顺序
   不固定）；两个点都完成后只等任务整体从追踪面板消失（follow 判定
   完成；不做战斗判定，战斗由 battle 线程托管）。封印点元素从追踪
   文案动态解析（旧版写死火/木，实机轮到金/土时颜色判定落空、空转
   到超时——2026-11-17 教训）。
   灵力镐（魔族，2026-11-10 用户规则）：任务追踪出现「前往大唐边境，
   使用灵力镐」→ 点击任务追踪绿色文案**蕴灵石头**（不可点「灵力镐」，
   那是物品说明）自动寻路（须先见位移才允许后续步骤，防在领取点
   原地用镐打空）→ 到达目的
   地（位置连续稳定且在目标地图）后打开背包任务储物格（与唤风符同
   格）右键使用灵力镐（mingwang_lingligao）→ 用镐后出现「开采石头」
   小游戏窗口（kaicaishitou 模板）→ 复用破除封印/钓鱼同款闭环
   pochufengyin.play_pochufengyin（**长按运输 + 点按维持**：长按推过
   带上缘 → 松手回落 → 回 C 后点按保持在 [L,R]）；小游戏结束后的
   任务推进由 follow 继续跟踪，触发词消失即状态复位。
   传音石（魔王重聚，2026-11-11 初始规则 + 2026-11-15 实机全流程）：
   任务追踪出现「使用传音石找到魔王和魔王护卫的位置，并在他附近使用
   传音石」→ 任务储物格右键使用传音石 → 寻人引导（读中央提示找图/
   按方向收敛、近身触发魔王护卫战斗）由小游戏模块
   minigames/mowangchongju.py 实现，任务侧只注入道具使用与共享帧；
   战后追踪变为「请将传音石交给魔王三大王」→ 任务追踪引擎通用绿字
   点击寻路 → 对话选项「这是牛魔王遣我送来的传音石。」（DIALOG_OPTION
   _MAP）→ 收尾台词 → 任务完成。
   天宫奇遇（2026-11-14 用户规则）：任务追踪出现「前往老君丹房门口，
   使用偷丹葫芦。」→ 点击追踪绿字**老君丹房门口**自动寻路（「偷丹
   葫芦」是物品说明链接，不得点击）→ 到达天宫 (13,99)（用户指定
   1px 容差）→ 打开背包任务储物格右键使用偷丹葫芦
   （mingwang_toudanhulu）→ 出现「魔取仙丹」小游戏窗口：点「观看」
   记录 4 个封印小葫芦的亮起顺序 → 点「确认」进入输入阶段 → 按序
   点击 4 个葫芦 → 再点「确认」提交解封；成功后进入战斗（战斗由
   follow_task/battle 线程托管，任务追踪消失即完成）。小游戏闭环见
   minigames/moqushendan.py（2026-11-14 实机探针标定并验证通过）。
   蛟魔复仇暗雷战斗（2026-11-11 用户规则）：任务追踪文案含「暗雷战斗」
   （如「仙族兵士逃窜而去，请在女儿村仔细搜查……（暗雷战斗）」）时，
   点击追踪绿字自动寻路到文案中的地图（「请在<地图>仔细搜查」解析）；
   脚本按 1s 限频识别左上角位置地名，到达目标地图后调用飞行驭器降落
   （zuoqi.ensure_landed，降落后关面板防遮挡）→ 等待暗雷遭遇战斗
   （战斗由 follow_task/battle 线程托管，不做特殊处理）。已降落状态
   保持到目标地图变化（多段搜查换图重新降落）或触发词连续缺失复位。
   星魂归位（仙族，2026-11-17 实机全流程）：追踪「看看天庭的星官X遇到
   什么麻烦了。」→ 引擎点「星官X」链接寻路 → 对话选「是武尊神遣我过来
   帮忙的。」（DIALOG_OPTION_MAP）→ 传送四象禁地 → 找场景中「X之星魂」
   石柱（位置随机）点标签寻路、点石柱、选「催动法力，让星魂跟随自己
   移动。」→ 点追踪「X之星宿石」链接寻路 → 选「星魂归位！」→ 点
   「四象禁地」回中心，重复六个（顺序无要求）；六个归位后任务自动完成。
   执行闭环见 minigames/xinghun.py（任务侧只注入共享帧与复位）。
5. 领取后（用户 2026-11-10 规则）：接取后出现对话框——对话框模板
   门控命中则点屏幕中间关闭；任务追踪出现「山河名望」→ 复用
   workflows.follow.follow_task 跟踪到任务完成（每轮点击追踪绿字推进）。
   任务执行对话按 DIALOG_OPTION_MAP 处理（对话框文案 → 绿色选项，
   可配置多个候选选项按顺序尝试），如「黑水河深，不如蛟魔之心。」→
   「逆天魔大将派我前来」/「<山河名望>」；无绿字选项的多屏剧情台词
   按 DIALOG_SKIP_TEXTS 处理（文案 → 连点屏幕中间次数，同玄枢轶闻
   skip_texts；未收录的由剧情兜底 1 次/秒推进）。
   沙海迷踪：入口对话「少侠准备好清理地图了吗？」→「我已经准备好了」
   后，armed 窗口内识别「开始」按钮 mingwang_chenjiuditu_kaishi 点击
   进入「记住混沌」小游戏（2026-11-11 补全）：地图卷轴出现深黑墨团时
   跨轮询记录坐标，底部提示切换为含「显形」的点击阶段后，逐个坐标
   点两下净化（第一下显形 → 间隔 0.2s 第二下净化；两下被吞时补点），
   循环到弹窗关闭/倒计时耗尽/结算文字；详细流程见 play_shahai_miyong。
   净化水晶丛林：入口对话「糟糕，水晶丛林竟真被混沌缠上了，得赶快
   净化这些混沌！」→「马上净化水晶丛林上的混沌。」后，armed 窗口内
   识别「开始」按钮 mingwang_qingxishuijing_kaishi 点击进入战斗，
   战斗结束即任务完成（入口「开始」按钮模子同沙海迷踪）。
   蛟魔探秘：入口对话「老头子我最喜欢的，就是在这里钓鱼了~」→
   「请将鱼竿借我一用！」后，armed 窗口内识别窗口标题
   mingwang_diaochushuizhongguaiwu 并复用共享闭环
   pochufengyin.play_pochufengyin（点击冲刺连点模式：单次点击推进小，
   须高频率连点才能到达目标带；填充条停手会自动回退，进带后持续
   补点维持到窗口结束），标题消失即小游戏结束。**失败重试**（用户
   2026-11-17 规则）：小游戏循环检测到「游戏失败」提前返回失败，失败
   结果面板（标题仍在、不自动关闭）由 _maybe_retry_diaoyu_fail 看门狗
   点右上角 X 关闭 → 点任务追踪绿色链接重走一遍（与 follow 引擎同款
   track_task），入口对话/选项重新触发钓鱼；连续失败
   DIAOYU_FAIL_RETRY_MAX 次后停止自动重试并告警。
6. 主循环（同玄枢轶闻）：完成一轮后回去领取下一轮，直到次数上限/
   每日次数已满/任务栏无当前已实现任务/**今日任务全部完成**（点击
   接取后出现「今日还请多加歇息，明日再来吧」→ 关闭山河名望面板后
   结束，2026-11-15 用户规则）；启动时追踪已有任务则跳过领取直接
   续跑。返程由领取寻路（世界地图/小地图）兜底。
"""
import re
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard

from ..core.app_utils import log, human_click, norm_text
from ..core import app_utils  # 静默模式判定（Esc 兜底按键通道）
from ..device import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from ..device.map import bgr_frame as _bgr_frame  # PIL→BGR（模板匹配用）
from ..device import task_tracker  # 任务追踪面板（对话框区域常量）
from ..device import bag  # 背包操作（五庄封印任务储物格用唤风符）
from ..device import zuoqi  # 飞行驭器（蛟魔复仇到达目标地图后降落）
from ..minigames import moqushendan as _moqu_minigame  # 天宫奇遇「魔取仙丹」顺序解封小游戏
from ..minigames import mowangchongju as _mowang_minigame  # 魔王重聚「传音石寻人」引导（读提示找图/按方向收敛）
from ..minigames import xinghun as _xinghun_minigame  # 星魂归位·四象禁地（找星魂→星宿石→归位闭环）
from ..minigames import pochufengyin as _pochu_minigame  # 破除封印/钓鱼共用小游戏（闭环实现）
from ..minigames import shahai as _shahai_minigame  # 沙海迷踪阻塞循环
from ..minigames.shahai import (SHAHAI_GAME_ARM_TIMEOUT,
                                SHAHAI_GAME_CHECK_INTERVAL,
                                _shahai_game_present)  # 沙海触发门/在场检测
from ..device.battle import is_in_battle, is_in_battle_frame  # 战斗判定（单帧复用）
from ..core import debug_overlay  # 调试信息悬浮层（任务阶段更新）
from ..core.settings import load_task_counts  # 任务次数上限（通用设置）
from ..device.dialog_common import (_click_green_option_once,  # 绿色选项点击（共享实现）
                            _click_screen_center,      # 点击屏幕中间推进对话
                            _dialog_text_present,      # 对话框文案模糊匹配
                            _fuzzy_contains,           # 任务标题模糊匹配
                            clear_blocking_dialog,     # 对话框门控 + 点屏幕中间关闭
                            dialog_frame_score,        # 对话框模板门控分数
                            green_option_row_present,  # 独立绿字选项行判定（行内高亮不算）
                            DIALOG_FRAME_THRESHOLD,
                            close_scene_panels)       # 场景面板清理（地图/物品栏）
from ..workflows.follow import follow_task  # 共享任务跟踪引擎（跨任务零互引）

# 任务模式名（控制面板任务次数配置键 / 当天进度持久化键）
TASK_MODE = "名望"
# 任务追踪标题（后续跟踪流程用；本阶段不读取追踪面板）
TASK_TITLE = "山河名望"

# ---- 种族判定（人物属性面板） ----
ATTR_PANEL_BTN_POS = (1230, 95)        # 打开人物属性面板的按钮（用户标定）
RENWUSHUXING_ICON = "renwushuxing.png"  # 人物属性竖排标题牌（用户素材 16.png，32x178）
ATTR_TEMPLATE_THRESHOLD = 0.8          # 标题牌匹配阈值（静态面板，实测待校准）
ATTR_BTN_EXCLUDE_PX = 80               # 打开按钮区域排除半径（标题牌与按钮同图形的误匹配防护）
ATTR_OPEN_TIMEOUT = 5.0                # 点击按钮后等待面板打开的时限（秒）
ATTR_CLOSE_TIMEOUT = 2.0               # 每种关闭方式后等待面板消失的时限（秒）
RACE_STRIP_WIDTH = 120                 # 标题牌右侧种族名称区宽度（用户规则）
RACE_STRIP_Y_PAD = 20                  # 条带上下扩边（角色列表可能高于标题牌）
RACE_OCR_MIN_SCORE = 0.55              # 区域 OCR 行置信度下限（同各任务模块）
RACE_MAX_ATTEMPTS = 2                  # 种族判定失败（面板/OCR 瞬态）重试次数
# 种族关键词（文案内出现任一词即计一票）：魔族角色列表含「男魔角色/女魔
# 角色/逆天魔/混天魔/绝影魔」等；仙族列表含「女仙角色/精灵仙/霓裳仙」等。
# 条带内同时出现两族字样（OCR 噪声/界面混排）视为无法判定。
# 2026-11-10 实机：条带 OCR 实际读到的是角色自身信息（如「逆天魔魔」+
# 等级/气血），角色名即图3/图4 列表中的一项。仙族部分角色名不含「仙」
# （武尊神/云中君/玄剑娥），**必须按角色名单补关键词**，否则仙族账号
# 读不出种族（2026-11-10 用户新增仙族时确认）。
RACE_KEYWORDS = {
    "魔族": ("魔",),
    "仙族": ("仙", "武尊神", "云中君", "玄剑娥"),
}
# 种族判定缓存（角色维度持久化，**每天一次**）：同一天内跨轮次/跨启动
# 复用、不再重复打开人物属性面板（2026-11-10 用户反馈：完成一轮后返回
# 领取前又重判种族）；角色可能转种族，跨天自动重判（用户 2026-11-10
# 规则）。诊断页重判成功会覆盖当天缓存，可作手动刷新。
RACE_CACHE_KEY = "mingwang_race"

# ---- 领取点（种族 → 地图 / 黄色任务 NPC 名配置） ----
# 名望唯一领取路线（用户 2026-11-16 规则：移除小地图快捷开关，一律走
# 小地图快捷领取；旧「坐标寻路 + NPC 模板点击」路线已删除——模板会被
# 玩家/对话框/摊位遮挡，是用户反馈的明显缺陷）：打开种族地图小地图
# （已在该地图直接开，否则经世界地图 → 点击种族地图标签）→ 勾选底部
# 「任务」筛选 → 点击地图上**固定位置**的黄色任务 NPC 名（魔族 逆天魔 /
# 仙族 武尊神）→ 游戏自动寻路/传送，到达后自动打开 NPC 对话框 → 直接
# 点绿色选项「开启山河名望志」。
# 通用能力（开小地图/任务筛选/点击固定点）下沉到 device/map.py 的
# minimap_quick_navigate，供其他任务复用；本模块只保留种族场景的
# **绝对坐标**标定（CLAIM_POINTS 的 map_filter_pos / map_label_pos——
# 小地图窗口位置固定，即使被拖走下次打开也恢复默认位置，不做窗口锚点
# 相对换算，用户 2026-11-16 规则）。
CLAIM_POINTS = {
    "魔族": {
        "map": "大唐边境",  # 世界地图标签统一由 map.WORLD_MAP_LABELS 维护
        "npc_name": "逆天魔",
        # 小地图「任务」勾选框 / 黄字「逆天魔」点击点（绝对坐标，
        # 2026-11-16 16:38/16:39 实机日志实测值；窗口位置固定）
        "map_filter_pos": (501, 665),
        "map_label_pos": (989, 380),
    },
    "仙族": {
        "map": "天宫",
        "npc_name": "武尊神",
        # 小地图「任务」勾选框 / 黄字「武尊神」点击点（绝对坐标：
        # 勾选框按天宫锚点 (991,217) 标定换算，黄字为 17:06 实机日志
        # 实测值；窗口位置固定）
        "map_filter_pos": (599, 668),
        "map_label_pos": (821, 368),
    },
}
# 种族 → 领取点描述（日志/诊断文案）
CLAIM_LOCATIONS = {
    race: f"{c['map']} {c['npc_name']}"
    for race, c in CLAIM_POINTS.items()
}
CLAIM_DIALOG_OPTION = "开启山河名望志"    # 领取对话框绿色选项（2026-11-10 实机日志 OCR 原文）
CLAIM_SHANHE_ICON = "mingwang_shanhemingwang.png"  # 山河名望入口（21.png，43x154）
CLAIM_SHANHE_THRESHOLD = 0.8           # 入口竖牌匹配阈值（静态 UI）
OPTION_CLICK_RETRY_INTERVAL = 3.0      # 同一选项点击后的最短重试间隔（防连点）
# 点击黄色任务 NPC 名后**只点一次**，随后等游戏自动寻路走过去（用户
# 2026-11-16 指正：不要点邻近点重试——第一次点击已开始寻路，重复点击
# 会重新选点/打断寻路；寻路耗时按 120s 超时）。地图窗口不要求立即关闭。
MAP_CLAIM_ARRIVE_TIMEOUT = 120     # 黄字点击后等待自动寻路到达+对话的时限（秒）

# ---- 山河名望任务栏（2026-11-10 实机标定，大唐边境面板打开时截图标定） ----
# 任务栏锚点 = 面板顶部「接取」图标（今日已完成行左侧，用户素材 22.png）
PANEL_MAODIAN_ICON = "mingwang_shanhemingwang_maodian.png"  # 33x35，实机匹配 1.0
PANEL_MAODIAN_THRESHOLD = 0.8
PANEL_JIEQU_ICON = "mingwang_shanhemingwang_jiequ.png"      # 「接取」按钮（60x20，实机 1.0）
PANEL_JIEQU_THRESHOLD = 0.8
# 卡片网格（用户标定卡片 190x154；实机：接取按钮中心 x=487/688/889、
# y=423/584 → 列间距 201、行间距 161，与 190x154 的间隙吻合）。
# 用户口述「3 行 3 列」，实机为 3 列 × 2 行 = 6 格（按实机校正）。
PANEL_CARD_W = 190
PANEL_CARD_H = 154
PANEL_COL_PITCH = 201
PANEL_ROW_PITCH = 161
PANEL_CARD_TOP_GAP = 3      # 第一行卡片顶 = 锚点底 +3（实机：锚点底 287、卡片顶 290）
PANEL_COLS = 3
PANEL_ROWS = 2
PANEL_OPEN_TIMEOUT = 8.0    # 打开面板/等待锚点出现的时限（秒）
CLAIM_VERIFY_TIMEOUT = 6.0  # 点击接取后等待领取确认信号的时限（秒）
CLAIM_DIALOG_TIMEOUT = 8.0  # 领取后等待对话框出现的时限（秒）
TRACK_TASK_TIMEOUT = 12.0   # 领取后等待追踪面板出现「山河名望」的时限（秒）
# ---- 今日任务全部完成（2026-11-15 用户规则）----
# 点击接取后出现「今日还请多加歇息，明日再来吧」= 今日山河名望任务已
# 全部完成：关闭山河名望面板，结束本次任务循环——不计完成次数、不再
# 重试领取（接取按钮仍在，重试只会反复弹同一句提示）。
# 该提示与玄枢轶闻「少侠已经完成5次任务，请明天再来吧」同为**非标准
# 对话框**提示（无标准边框，对话模板门不命中）：须直接 OCR 对话区
# （同 xuanshu 领取校验的 DAILY_LIMIT_TEXT 检测方式），不得加模板门控
# （2026-11-15 用户指正）。匹配走 _dialog_text_present 模糊匹配（抗
# OCR 单字误读/拆行），锚词按游戏原文维护（识别用有辨识度的整句片段，
# 不要用「明日」这类泛词）。
DAILY_FULL_TEXTS = ("今日还请多加歇息", "明日再来吧")
# 期望领取优先级（高→低）：后续实现各任务执行流程后按该顺序选卡
LEVEL_PRIORITY = ("高级", "中级", "初级")
LEVEL_OCR_ALIASES = str.maketrans({"級": "级"})  # OCR 常把「级」读成繁体「級」
# 已实现识别/领取+执行流程的任务（标题/级别）。任务栏领取时按级别
# 优先级（高级>中级>初级）再按卡片顺序选第一个可用的已实现任务。
# 级别为 None = 不限定级别（按标题匹配任意级别卡片）。
# 仙族「五庄封印」为高级卡片（2026-11-10 用户实机确认）；执行逻辑见
# 五庄封印状态机（追踪双链接 / 坐标稳定到达 / 任务储物格用唤风符）。
# 沙都修罗（高级，2026-11-10 用户流程）：追踪文案含「根据三张蟠桃园
# 残像所示位置」时按五个路点在蟠桃园搜索 NPC 并点击（见下方沙都修罗
# 执行状态机常量）；其余阶段由通用跟踪 + 战斗线程托管。
CLAIM_TASKS = (
    # 魔族
    {"title": "蛟魔复仇", "level": "高级"},
    {"title": "沙海迷踪", "level": "高级"},
    {"title": "蛟魔探秘", "level": "高级"},
    {"title": "天宫奇遇", "level": "高级"},
    {"title": "凿石造像", "level": "中级"},
    {"title": "魔王重聚", "level": "高级"},
    # 仙族
    {"title": "净化水晶", "level": "高级"},
    {"title": "五庄封印", "level": "高级"},
    {"title": "沙都修罗", "level": "高级"},
    # 星魂归位（仙族，2026-11-17 实机全流程）：高级卡片（用户确认）；
    # 执行实现在 minigames/xinghun.py
    {"title": "星魂归位", "level": "高级"},
    # 鬼族
)
# ---- 任务执行对话（类似 xuanshu.DIALOG_OPTION_MAP：对话框文案 → 绿色选项）----
# 逐条按游戏原文维护（用户 2026-11-10 提供）；文案匹配走 _dialog_text_present
# 模糊匹配（逐行 + 全文拼接），选项匹配走 _click_green_option_once。
# 值可以是字符串，也可以是**候选选项元组**：同一句对话可能出现不同选项
# （按顺序逐个尝试，点到哪个算哪个）——如蛟魔复仇的
# 「逆天魔大将派我前来」与「<山河名望>」。
DIALOG_OPTION_MAP = {
    "黑水河深，不如蛟魔之心。": ("逆天魔大将派我前来", "<山河名望>"),
    "血海深仇，永世不忘，不报此仇，誓不为蛟。":"是逆天魔遣我过来帮忙的。",
    "代天巡守，来者何人？":"这里不是你们该来的地方！",
    "少侠准备好清理地图了吗？":"我已经准备好了",
    "嗯？你是何人？":"请问你是，沙蛇一族的吗？",
    "也许世人早已经忘记了我们，这可是一件好事。":"逆天魔派我过来帮忙。",
    "幽幽的缝隙，混杂着混沌气息的黑水从里面缓缓溢出。":"尝试清理",
    "老头子我最喜欢的，就是在这里钓鱼了~":"请将鱼竿借我一用！",
    # 星魂归位（2026-11-17 实机）：星官角木蛟开场选「是武尊神遣我过来帮忙的。」
    # 后传送到四象禁地；到达星宿石的归位确认对话（状态机也点，表作兜底）
    "卦象显示，东方七宿": "是武尊神遣我过来帮忙的。",
    "感觉差不多到对应的星位": "星魂归位！",
    "这该怎么办呢，好担心":"是武尊神遣我过来帮忙的。",
    "大事不妙！蟠桃园内大量的蟠桃树遭":"是武尊神遣我过来帮忙的。",
    "那个又唠叨又烦人的破龙王，天庭将这么重要的东海元石托付给他，居":"是武尊神遣我过来帮忙的。",
    "糟糕，水晶丛林竟真被混沌缠上了，得赶快净化这些混沌！":"马上净化水晶丛林上的混沌。",
    "咱们魔族的风格就是：有肉一块吃，有酒一块喝，至于有怪，还是你自己杀":"<山河名望>",
    "这群手下真是越来越难管了，我让他们凿点石头给俺，这叫凿石头吗？":"是逆天魔遣我过来帮忙的。",
    "总有人和我说火云戈壁里有什么城啥的，俺寻思这也不可能啊，他们一族不":"逆天魔大人派我过来帮忙",
    "有肉咱们一块吃，有酒咱们一块喝。":"牛魔王大人，逆天广大将派我前来见您。",
    # 魔王重聚（2026-11-15 实机）：魔王护卫战后把传音石交给魔王三大王
    "那老牛派你过来":"这是牛魔王遣我送来的传音石。",
    "多么怀念当年俺们七个，还有小白，唉，都是往事了啊，但俺答应过的，俺":"是逆天魔遣我过来帮忙的。",
    "这天上来的消息应该还挺准的吧，俺也没那么指望吃上，可他们也别想吃上":"是逆天魔遣我过来帮忙的。"
}
# ---- 点击屏幕中间推进的对话（同玄枢轶闻/客栈的跳过对话表机制）----
# 键 = 对话框文案（_dialog_text_present 模糊匹配：逐行 + 全文拼接，抗
# OCR 误读/换行拆分；用有辨识度的短句即可），值 = 命中后一次连点屏幕
# 中间的次数（多屏剧情台词无需枚举后续屏文案，漏推进由下一轮询 OCR
# 自愈）。仅用于**无绿字选项**的 NPC 剧情台词：带选项对话必须先命中
# DIALOG_OPTION_MAP（follow 内层顺序：选项表 → 跳过表 → 通用选项 →
# 剧情兜底），本表不会抢点选项对话。未收录的未知剧情对话仍由
# StoryDialogDismisser 兜底（1 次/秒慢速推进），本表用于加速已知多屏
# 对话（2026-11-10 用户要求与玄枢轶闻对齐）。
# 2026-11-10 实机（仙族五庄封印·五庄观 NPC 明月）：风暴封印线索。
DIALOG_SKIP_TEXTS = {
    "我倒是担心风暴的封印": 1,
    # 星魂归位（2026-11-17 实机）：选「是武尊神遣我过来帮忙的。」后的
    # 无选项说明台词，点一次推进后自动传送四象禁地
    "少侠不愧是武尊神看重之人": 1,
    # 魔王重聚（2026-11-15 实机）：魔王护卫战后台词 + 魔王三大王收尾台词
    "本事不错，俺家主人愿意见你一面": 1,
    "哼，不知道老牛吸取了之前失败的教训": 1,
}
FOLLOW_TIMEOUT = 300        # 跟踪任务总超时（秒；战斗等待时间不计入，同 xuanshu）

# ---- 五庄封印（仙族）执行：追踪双链接 → 到达 → 任务储物格用唤风符 ----
# 用户 2026-11-10 流程：追踪面板出现两个「X藏之位」绿字链接（五行任选
# 两个，2026-11-17 用户确认：金/木/水/火/土 都可能，实机已见
# 金藏+土藏、水藏+火藏）→ 点第一个 → 寻路到达 → 打开背包任务储物格
# （与修罗回城符同一格）→ 右键使用唤风符（mingwang_huanfengfu）。用符后
# 当前链接**由亮绿变红**（可能立刻、也可能战斗结束后变红）或坐标离开
# 五庄观 = 该点完成，改点另一个未完成的；两个点都完成后只等任务整体消失
# （follow 判定）。到达判定复用五倍显形镜的「位置连续稳定」逻辑
# （XIANXINGJING_ARRIVE_POLLS=3）。
# **封印点从追踪文案动态解析**（2026-11-17 实机教训：旧版写死火藏/木藏，
# 实机轮到「金藏之位+土藏之位」时颜色判定全落空——状态机认不出已完成，
# 日志还一直报火藏，空转到超时）：目标元素 = 追踪签名里所有「X藏」
# 的规范字，按文本顺序；当前目标 = 第一个未变红的，完成顺序两种都支持。
SEAL_TASK_KEYWORD = "五庄"              # 追踪面板任务名关键词（五庄封印）
SEAL_MAP = "五庄观"                     # 任务地图（离开即视为本点完成）
SEAL_ELEM_ALIASES = {                   # 「X藏」OCR 形近字 → 五行规范字
    "金": "金", "全": "金",
    "木": "木", "未": "木", "本": "木",
    "水": "水",
    "火": "火", "灭": "火",
    "土": "土", "士": "土",
}
SEAL_LINK_NAME_SUFFIX = "藏之位"         # 日志/展示用全名（X + 后缀）
SEAL_ARRIVE_POLLS = 3                   # 位置连续稳定轮询数判定到达（同显形镜）
SEAL_POS_TOLERANCE = 3                  # 坐标稳定容差（像素，同显形镜）
SEAL_MOVE_DELTA = 5                     # 判定寻路已启动的位移阈值（同显形镜）
SEAL_MOVE_STUCK_MAX = 4                 # 点链接后位置连续不变轮询数 → 重点链接
SEAL_USE_GRACE = 3.0                    # 用符后等待链接变红/传送的宽限（秒）
SEAL_USE_RETRY_AFTER = 15.0             # 链接仍未变红时的用符重试间隔（秒）
SEAL_USE_RETRY_MAX = 2                  # 每个封印点最多重试用符次数
# 到达后对话框自动推进（用户 2026-11-10 实测：到达封印点后屏幕中间出现
# 无绿字选项的对话框，story 兜底因「已知对话表」抑制不点、用符等待被
# 阻塞 65s，须人工点屏才继续）：到达/切换链接遇对话框时，若对话区连续
# SEAL_DIALOG_ADVANCE_POLLS 次无绿字选项，主动点屏幕中间推进（≈2s，
# 留足选项渲染时间；有绿字选项时由选项表处理，不点屏）。
SEAL_DIALOG_ADVANCE_POLLS = 4           # 无绿字选项连续轮询数（约 2s）
SEAL_DIALOG_ADVANCE_INTERVAL = 1.0      # 点屏推进最小间隔（秒）
# 用符后残留背包清理：对话框遮挡 bag_box 时 close_bag 会误报「背包未
# 打开」而实际仍开着（用户 2026-11-10 实测）；等对话框消失后按帧检测
# bag_box 再关，限频重试。
SEAL_BAG_CLOSE_INTERVAL = 2.0           # 残留背包检测/关闭最小间隔（秒）
SEAL_ITEM_ICON = "mingwang_huanfengfu.png"  # 唤风符（用户素材 图31，46×47）
SEAL_ITEM_LABEL = "唤风符"
SEAL_ITEM_THRESHOLD = 0.8               # 物品图标匹配阈值（静态图标）
SEAL_ITEM_FIND_TIMEOUT = 10.0           # 任务储物格中查找唤风符的时限（秒）
# 状态机（对齐五倍显形镜）：0=等到达；1=已用符等结果。
# 不做战斗判定——战斗由 follow_task/battle 线程托管；战斗结束后任务
# 若已从追踪面板消失，follow 直接判定完成（用户 2026-11-10 规则）。
_seal_phase = 0
_seal_elems = []            # 封印点元素（规范字，按追踪文案顺序；动态解析）
_seal_link_idx = 0          # 当前目标在 _seal_elems 中的下标
_seal_stable = 0            # 位置连续稳定轮询数
_seal_last_pos = None       # 上次位置
_seal_need_move = False     # 点链接后须位置先变化才允许用符
_seal_walk_from = None      # 点链接时的位置（移动验证起点）
_seal_move_stuck = 0        # 点链接后位置连续不变轮询数
_seal_use_at = 0.0          # 上次用符时间（宽限窗口起点）
_seal_use_retries = 0       # 当前封印点用符重试次数
_seal_missing = 0           # 追踪文案不含五庄的连续轮询数（≥5 复位）
_seal_both_warned = False   # 两处完成/任务消失仅告警一次
_seal_dlg_streak = 0        # 到达后无绿字选项对话框连续轮询数
_seal_dlg_click_at = 0.0    # 上次点屏推进时间（冷却）
_seal_bag_close_at = 0.0    # 上次残留背包检测时间（限频）
# extras 链共享帧缓存（同五倍显形镜的 extras 帧复用做法）：follow 每轮询调一次
# extras，本处理器按 id(items) 共享一帧，位置 OCR/对话框模板检测免重复截窗
_SEAL_POS_UNSET = object()
_seal_frame_key = None
_seal_frame_rgb = None
_seal_frame_bgr = None
_seal_frame_pos = _SEAL_POS_UNSET

# ---- 灵力镐（魔族）：追踪「前往大唐边境，使用灵力镐」→ 点蕴灵石头 → 到达 → 任务储物格用镐 ----
# 用户 2026-11-10 流程：任务追踪出现「前往大唐边境，使用灵力镐」→ 点击
# 任务追踪绿色文案**蕴灵石头**自动寻路 → 到达目的地（位置连续
# 稳定且在目标地图）→ 打开背包任务储物格（与修罗回城符/唤风符同格）→
# 右键使用灵力镐（mingwang_lingligao.png）。到达判定复用五庄封印/五倍
# 显形镜的「位置连续稳定」逻辑；用镐后任务推进（子目标变化/任务完成/
# 进入战斗）由 follow 引擎与 battle 线程继续托管，本状态机触发词消失即
# 复位（连续 PICK_ABSENT_RESET 轮不见触发词）。
# **初始须先见位移**（_pick_need_move=True）：魔族领取点与执行地图同为
# 大唐边境，若领取后站在领取点原地（位置稳定）就直接用镐会打空——链接
# 点击后必须看到位移（寻路已启动）才允许到达/用镐；点链接后长时间无位移
# 连续 PICK_STUCK_CYCLES_USE 个重点周期仍不动，视为已在目的地（链接点击
# 后原地不动），放行用镐。
PICK_TASK_KEYWORDS = ("灵力镐", "灵力稿", "灵力搞")  # 追踪触发词（含 OCR 形近字）
PICK_MAP = "大唐边境"                  # 目标地图（到达判定须在此图）
# 绿色链接定位关键词（按顺序取首个命中）。2026-11-10 实机截图标定：
# 追踪文案为「前往大唐边境，使用 灵力镐 /，用合适的力道开凿 蕴灵石头
# (412,45)。」——两处都是亮绿链接，但可导航的是**蕴灵石头**；「灵力镐」
# 点开的是物品说明（用户反馈点错）。因此只按蕴灵石头定位（短词「蕴灵」
# 作 OCR 截断/形近兜底），其余绿字一律不点，未读到时等待重试。
PICK_LINK_KEYWORDS = ("蕴灵石头", "蕴灵")
PICK_ARRIVE_POLLS = 3                   # 位置连续稳定轮询数判定到达（同封印）
PICK_POS_TOLERANCE = 3                  # 坐标稳定容差（像素）
PICK_MOVE_DELTA = 5                     # 判定寻路已启动的位移阈值
PICK_MOVE_STUCK_MAX = 4                 # 点链接后位置连续不变轮询数 → 重点链接
PICK_STUCK_CYCLES_USE = 3               # 连续重点周期仍无位移 → 视为已在目的地，允许用镐
PICK_LINK_RETRY_AFTER = 20.0            # 原地静止且不在目标地图时的重点链接间隔（秒）
PICK_USE_GRACE = 3.0                    # 用镐后等待任务推进的宽限（秒）
PICK_USE_RETRY_AFTER = 15.0             # 触发词仍在时的用镐重试间隔（秒）
PICK_USE_RETRY_MAX = 2                  # 最多重试用镐次数
PICK_ABSENT_RESET = 5                   # 触发词连续缺失轮询数 → 状态复位
PICK_BAG_CLOSE_INTERVAL = 2.0           # 残留背包检测/关闭最小间隔（秒）
PICK_ITEM_ICON = "mingwang_lingligao.png"  # 灵力镐（用户素材，46×42）
PICK_ITEM_LABEL = "灵力镐"
PICK_ITEM_THRESHOLD = 0.8               # 物品图标匹配阈值（静态图标）
PICK_ITEM_FIND_TIMEOUT = 10.0           # 任务储物格中查找灵力镐的时限（秒）
# ---- 用镐后「开采石头」小游戏（2026-11-10 用户规则）----
# 使用灵力镐后出现窗口标题 kaicaishitou.png（用户素材，164×17）→ 复用
# 破除封印/钓鱼同款进度条小游戏闭环 pochufengyin.play_pochufengyin。
# **长按运输 + 点按维持（hold=True + hold_catch=True，2026-11-10 用户
# 技巧）**：开始后按钮为「长按」，长按推进极快（~2.4 刻度/秒）、目标带
# 仅 ~1 刻度宽且进带不锁定——钓鱼式按住到结束必然冲过目标带、松手又
# 整体回退判失败。改为：长按把填充推过带上缘 → 松手让它回落 → 回到
# C（带中心）后改为点按维持（点击率伺服把填充保持在 [L,R] 内到窗口
# 结束），掉出带下重新长按（见 pochufengyin.POCHU_CATCH_*）。「长按」
# 按钮为小游戏通用美术，复用玄枢钓鱼模板 xuanshu_diaoyu_changan.png。
# 窗口检测同蛟魔探秘钓鱼：armed 门控（用镐成功起 PICK_MINING_ARM_TIMEOUT
# 内有效）+ 限频（每 PICK_MINING_CHECK_INTERVAL 在 POCHU_REGION 内匹配
# 标题），命中即阻塞执行到标题消失；未 armed 零成本。
PICK_MINING_TITLE_ICON = "kaicaishitou.png"
PICK_MINING_HOLD_ICON = "xuanshu_diaoyu_changan.png"  # 「长按」按钮（同引擎通用美术）
PICK_MINING_ARM_TIMEOUT = _pochu_minigame.POCHU_ARM_TIMEOUT
PICK_MINING_CHECK_INTERVAL = _pochu_minigame.POCHU_CHECK_INTERVAL
# 状态机（对齐五庄封印）：0=等到达/用镐；1=已用镐等任务推进。
# 不做战斗判定——战斗由 follow_task/battle 线程托管。
_pick_phase = 0
_pick_stable = 0            # 位置连续稳定轮询数
_pick_last_pos = None       # 上次位置
_pick_need_move = True      # 触发词出现后须位置先变化才允许用镐（领取点防打空）
_pick_walk_from = None      # 移动验证起点位置
_pick_move_stuck = 0        # 位置连续不变轮询数
_pick_stuck_cycles = 0      # 连续重点周期数（≥PICK_STUCK_CYCLES_USE 放行用镐）
_pick_use_at = 0.0          # 上次用镐时间（宽限窗口起点）
_pick_use_retries = 0       # 用镐重试次数
_pick_missing = 0           # 触发词连续缺失轮询数（≥5 复位）
_pick_bag_close_at = 0.0    # 上次残留背包检测时间（限频）
_pick_link_at = 0.0         # 上次点击链接时间（重点间隔）
_pick_mining_armed_at = 0.0  # 用镐成功时间；>0 且未超时=开采石头窗口检测开启
_pick_mining_last_check = 0.0  # 上次开采石头窗口检测时间（限频）

# ---- 传音石（魔王重聚）：任务储物格道具 ----
# 使用传音石后的寻人引导（读中央提示 → 跨图/按方向收敛 → 近身触发
# 「魔王护卫」战斗）全部在 minigames/mowangchongju.py，本模块只提供
# 道具使用能力与战后交付对话配置。实机流程与标定见该小游戏模块。
CHUANYIN_ITEM_ICON = "mingwang_chuanyinshi.png"  # 传音石（用户素材，47×47）
CHUANYIN_ITEM_LABEL = "传音石"
CHUANYIN_ITEM_THRESHOLD = 0.8            # 物品图标匹配阈值（静态图标）
CHUANYIN_ITEM_FIND_TIMEOUT = 10.0        # 任务储物格中查找传音石的时限（秒）

# ---- 天宫奇遇（仙族·天宫）：追踪「前往老君丹房门口」→ 天宫(13,99) →
#      任务储物格用偷丹葫芦 → 「魔取仙丹」顺序解封小游戏 ----
# 用户 2026-11-14 流程：任务追踪内容为「前往老君丹房门口，使用偷丹
# 葫芦。」→ 点击追踪绿字**老君丹房门口**自动寻路（「偷丹葫芦」是物品
# 说明链接，**不得点击**——同灵力镐的蕴灵石头/灵力镐）→ 到达天宫
# (13,99) 附近（用户指定 1px 容差）→ 打开背包任务储物格右键使用偷丹
# 葫芦（mingwang_toudanhulu.png，用户素材 图1）→ 出现「魔取仙丹」
# 小游戏窗口（标题模板 mingwang_moqushendan.png）→ 按演示顺序点击
# 四个封印小葫芦 → 成功后进入战斗（战斗由 follow_task/battle 线程托管，
# 战后任务完成）。小游戏实现见 minigames/moqushendan.py。
TGQY_TASK_KEYWORDS = ("前往老君丹房门口", "老君丹房")  # 追踪触发词（短词兜底 OCR 拆行）
TGQY_LINK_KEYWORDS = ("老君丹房门口", "老君丹房", "丹房")  # 导航绿字（严禁点「偷丹葫芦」物品链接）
TGQY_MAP = "天宫"
TGQY_TARGET = (13, 99)                  # 用户标定到达点
TGQY_POS_TOLERANCE = 1                  # 到达容差（用户指定 1px）
TGQY_ARRIVE_POLLS = 3                   # 位置连续稳定轮询数判定到达
TGQY_MOVE_DELTA = 2                     # 判定寻路已启动的位移阈值（像素）
TGQY_MOVE_STUCK_MAX = 4                 # 点链接后位置连续不变轮询数 → 重点链接
TGQY_LINK_RETRY_AFTER = 20.0            # 寻路未启动时重点链接的最小间隔（秒）
TGQY_USE_GRACE = 3.0                    # 用道具后等待窗口出现的宽限（秒）
TGQY_USE_RETRY_AFTER = 15.0             # 窗口未出现时的重试用道具间隔（秒）
TGQY_USE_RETRY_MAX = 2                  # 最多重试用道具次数
TGQY_GAME_RETRY_AFTER = 15.0            # 小游戏游玩失败后的重试间隔（秒）
TGQY_ABSENT_RESET = 5                   # phase 0 触发词连续缺失轮询数 → 复位
TGQY_BAG_CLOSE_INTERVAL = 2.0           # 残留背包检测/关闭最小间隔（秒）
TGQY_ITEM_ICON = "mingwang_toudanhulu.png"  # 偷丹葫芦（用户素材 图1，49×48）
TGQY_ITEM_LABEL = "偷丹葫芦"
TGQY_ITEM_THRESHOLD = 0.8               # 物品图标匹配阈值（静态图标）
TGQY_ITEM_FIND_TIMEOUT = 10.0           # 任务储物格中查找偷丹葫芦的时限（秒）
# 状态机（对齐灵力镐）：0=等到达/用道具；1=已用道具等小游戏/任务推进；
# 2=小游戏已通过（不再重试用道具，等战斗/任务完成由 follow 判定）。
_tgqy_phase = 0
_tgqy_stable = 0            # 位置连续稳定轮询数
_tgqy_last_pos = None       # 上次位置
_tgqy_need_move = False     # 点链接后须位置先变化才允许到达判定
_tgqy_walk_from = None      # 点链接时的位置（移动验证起点）
_tgqy_move_stuck = 0        # 点链接后位置连续不变轮询数
_tgqy_use_at = 0.0          # 上次用道具时间（宽限窗口起点）
_tgqy_use_retries = 0       # 用道具重试次数
_tgqy_missing = 0           # phase 0 触发词连续缺失轮询数（≥5 复位）
_tgqy_bag_close_at = 0.0    # 上次残留背包检测时间（限频）
_tgqy_link_at = 0.0         # 上次点击追踪链接时间（重点间隔）
_tgqy_game_armed_at = 0.0   # 用道具成功时间；>0 且未超时=魔取仙丹窗口检测开启
_tgqy_game_last_check = 0.0 # 上次窗口检测时间（限频）
_tgqy_game_retry_at = 0.0   # 小游戏游玩失败后的下次允许游玩时间（冷却）

# ---- 蛟魔复仇（暗雷战斗）：追踪「请在<地图>仔细搜查（暗雷战斗）」→
#      寻路 → 到达目标地图 → 降落 → 点地面走位催发暗雷遭遇 ----
# 用户 2026-11-11 规则：追踪面板任务文案括号内含「暗雷战斗」时，点击
# 追踪绿字即自动寻路到文案中的地图（实机原文：「仙族兵士逃窜而去，
# 请在女儿村仔细搜查，可不能让让他就这么走了。（暗雷战斗）」→ 女儿村）；
# 脚本持续识别左上角地图位置地名，到达目标地图后执行降落（飞行驭器
# 已有能力 zuoqi.ensure_landed：面板识别飞行状态，飞行中点「降落」，
# 降落后关面板防遮挡），随后等待暗雷遭遇战斗——战斗由 follow_task/
# battle 线程托管，本状态机不做特殊处理。已降落标志保持到目标地图
# 变化（蛟魔复仇多段搜查换图重新降落）或触发词连续缺失复位；新任务
# 领取时一并复位。
# 2026-11-15 用户规则调整（先寻路后降落）：追踪文案出现时人物可能已站
# 在目标地图，旧逻辑会立即确认降落——但引擎点击追踪绿字开始寻路可能
# 再次起飞，白降落一次。改为先等「位移启动」（寻路已开始），再在目标
# 地图内位置连续稳定才判到达，到达后才降落。未见位移 JIAMO_MOVE_WAIT
# 秒（目的地就在脚下/点击未生效）也按到达放行，避免死等。
# 随机遇敌兜底（2026-11-15 用户规则 + 2026-11-17 修正）：到达稳定后仍
# 长时间未触发战斗且任务仍在（暗雷战斗文案仍可见）时**点击世界地面模拟
# 玩家行走**催发暗雷遭遇战（静默模式下 Alt+D 热键实测不会让人物随机
# 走动——2026-11-17 用户反馈连续多次静默投递零位移零战斗；点击是静默
# 通道已验证生效的世界交互，且走动本身就触发暗雷）。走位点四点轮换防
# 反复撞同一障碍/NPC；点击前已有位置连续稳定断言（点屏/世界点击须先
# 确认站定，防打断自动寻路——性能规则 13）；走动使稳定性自然清零，
# 到达稳定 JIAMO_WALK_WAIT 秒后再点下一个点。战斗开始后由 follow/
# battle 线程接管。
JIAMO_ANLEI_KEYWORD = "暗雷战斗"     # 追踪触发词（任务描述括号内）
# 目标地图解析：「请在<地图>仔细搜查」——地图名 2~8 字非贪婪，锚定在
# 「仔细/搜查」之前（OCR 形近变体一并收录；_seal_sig 已去标点，直接在
# 拼接后的追踪面板文案上匹配）。解析失败时用 _JIAMO_AFTER_RE 的「请在」
# 后 10 字窗口做「当前地名出现在其中」兜底（见 _jiamo_arrived）。
JIAMO_MAP_RE = re.compile(
    r"请在([一-鿿]{2,8}?)(?:仔细|仔佃|仟细|行细|息细|搜查|搜査|搜察)")
_JIAMO_AFTER_RE = re.compile(r"请在([一-鿿]{2,10})")
JIAMO_MAP_SUFFIX_RE = re.compile(r"(?:附近|一带|之内|以内|内|中|的)+$")
# 锚词本身被捕获的退化形态（如「请在仔细搜查」缺地图名）：不作目标
_JIAMO_ANCHOR_WORDS = ("仔细", "仔佃", "仟细", "行细", "息细",
                       "搜查", "搜査", "搜察")
JIAMO_POS_CHECK_INTERVAL = 1.0   # 位置地名 OCR 最小间隔（秒；轮询热路径限频）
JIAMO_LAND_RETRY_AFTER = 5.0     # 降落失败（战斗中/面板未开）后的重试间隔（秒）
JIAMO_ABSENT_RESET = 5           # 触发词连续缺失轮询数 → 状态复位（空 OCR 帧不计）
# 先寻路后降落：位移启动 → 到达稳定 → 降落（2026-11-15 用户规则）
JIAMO_MOVE_DELTA = 5             # 判定寻路已启动的位移阈值（像素，同封印/镐）
JIAMO_MOVE_WAIT = 12.0           # 等待寻路启动时限（秒）——超时未见位移视为已在目的地
JIAMO_ARRIVE_POLLS = 3           # 到达判定：位置连续稳定轮询数（同封印/镐）
JIAMO_POS_TOLERANCE = 3          # 到达判定：坐标稳定容差（像素）
# 随机遇敌兜底（点地面模拟行走）：到达稳定后等战斗的时限与走路间隔
JIAMO_WALK_WAIT = 5.0            # 到达稳定后仍未开战多久点地面走位（秒）
JIAMO_WALK_INTERVAL = 10.0       # 两次走位点击的最小间隔（秒）
# 走位点（窗口坐标，四点轮换）：世界区域中心附近、避开所有 UI 面板
# （左上任务追踪 x<400/y200-355、左下自动战斗 y≥690/x<330、右侧血蓝
# 与技能栏 x>1020、顶部状态栏 y<200、底部技能栏 y>735）——角色在屏幕
# 中心附近，任意一个点都会走出可观位移；轮换防止连续撞同一障碍物或
# 误点到同一 NPC。
JIAMO_WALK_POINTS = ((900, 430), (480, 430), (690, 650), (690, 250))
# 状态机：0=等寻路启动/到达/降落；1=已到达并降落，等战斗（点地面走位
# 兜底；保持到目标地图变化/复位）。
_jiamo_phase = 0
_jiamo_target = None          # 解析出的目标地图名
_jiamo_missing = 0            # 触发词连续缺失轮询数（≥JIAMO_ABSENT_RESET 复位）
_jiamo_pos_at = 0.0           # 上次位置地名检测时间（限频）
_jiamo_land_at = 0.0          # 上次降落尝试时间（重试间隔）
_jiamo_warned = False         # 目标地图解析失败已告警（每状态实例一次）
_jiamo_walk_from = None       # 等待寻路启动的起点位置
_jiamo_move_seen = False      # 已见位移（寻路已启动）或超时放行
_jiamo_walk_at = 0.0          # 首见文案时间（未见位移超时起点）
_jiamo_stable = 0             # 位置连续稳定轮询数（到达/走位共用）
_jiamo_last_pos = None        # 上次位置
_jiamo_stable_at = 0.0        # 稳定起点（走位等待起点；不稳清零）
_jiamo_walk_click_at = 0.0    # 上次走位点击时间（重按间隔）
_jiamo_walk_idx = 0           # 走位点轮换下标

# ---- 沙海迷踪（高级任务）执行入口 ----
# 入口对话选项「少侠准备好清理地图了吗？」→「我已经准备好了」（在
# DIALOG_OPTION_MAP 中）；点击选项后识别「开始」按钮并点击进入小游戏
# （用户 2026-11-10 入口流程；2026-11-11 补全小游戏闭环，见下方）。
SHAHAI_DIALOG_TEXT = "少侠准备好清理地图了吗？"
SHAHAI_START_ICON = "mingwang_chenjiuditu_kaishi.png"  # 「开始」按钮（用户素材 24.png，115x25）
SHAHAI_START_THRESHOLD = 0.8   # 开始按钮匹配阈值（静态 UI，实测待校准）
SHAHAI_ARM_TIMEOUT = 20.0      # 点击入口选项后检测「开始」按钮的时限（秒）
_shahai_armed_at = 0.0         # 入口选项点击时间；>0 且未超时=检测开启
_shahai_game_armed_at = 0.0    # 「开始」点击时间；>0 且未超时=小游戏弹窗检测开启
_shahai_game_last_check = 0.0  # 小游戏弹窗检测上次时间（armed 限频）

# ---- 沙海迷踪「记住混沌」小游戏（2026-11-11 用户完整流程 + 实测截图；
# 同日实机验证通过，两下间隔按用户修正为 0.2s）----
# 流程：点击「开始」进入 → 地图卷轴出现深黑色混沌墨团（记忆阶段，底部
# 提示「请记住混沌出现的位置，3秒后混沌将侵入地图之中」）→ 约 3 秒后墨团
# 淡出、底部提示切换为「点击令混沌显形，显形后再次点击将其净化！」→ 按
# 记录坐标逐个「点两下」（第一下显形，间隔 0.2s 第二下净化；中部横条
# 出现「此处混沌已消散！＝成功」/「此处并非混沌所在＝点错」）→ 不退出，
# 等待下一批墨团（顶部「第 N 次地图」递增）→ 循环。顶部「剩余 X 秒」为
# 每轮倒计时（OCR 超时保护），弹窗关闭或结算文字（通关/失败）即结束。
# 区域为用户标定 363,262-1031,630（窗口相对坐标）；2.png（有黑点，第 2 次
# 地图 剩余 8 秒）/3.png（无黑点，点击阶段）实测：黑点 RGB 最大通道 < 60
# （墨团 BGR≈(17,25,29)），窗口尺度面积约 130~330px；最大通道 <60 的固定
# 山体墨点在标定图最大仅 ~32px（缩放图尺度），叠加面积/形状与边缘余量
# 过滤即可排除（阈值放到 65 会出现山体误检）。
# 文本条带左缘取 400（task_tracker.PANEL_MAX_X）：避开任务追踪面板右缘
# x<400 的文字，防游戏世界/追踪文案误触发弹窗在场判定；弹窗文案实测
# x≥541（2.png/3.png 按区域缩放映射），400~1031 完整覆盖。
# 中文轮次（OCR 偶发把「第 2 次」读成「第二次」）

# ---- 净化水晶丛林（混沌）执行入口 ----
# 入口对话「糟糕，水晶丛林竟真被混沌缠上了，得赶快净化这些混沌！」
# →「马上净化水晶丛林上的混沌。」（在 DIALOG_OPTION_MAP 中）：点击选项
# 后识别「开始」按钮 mingwang_qingxishuijing_kaishi.png（用户 图4，
# 118×24）并点击，无需其它操作，等待进入战斗，战斗结束即任务完成
# （用户 2026-11-10 规则，同沙海迷踪模子）。模板与沙海「开始」按钮为
# 同款（互匹配 0.87~0.91），但 armed 窗口由各自入口对话触发、互斥。
QINGXI_DIALOG_TEXT = "糟糕，水晶丛林竟真被混沌缠上了，得赶快净化这些混沌！"
QINGXI_ARM_OPTION = "马上净化水晶丛林上的混沌。"
QINGXI_START_ICON = "mingwang_qingxishuijing_kaishi.png"
QINGXI_START_THRESHOLD = 0.8   # 开始按钮匹配阈值（静态 UI；非目标探针帧 ≤0.70）
QINGXI_ARM_TIMEOUT = 20.0      # 点击入口选项后检测「开始」按钮的时限（秒）
_qingxi_armed_at = 0.0         # 入口选项点击时间；>0 且未超时=检测开启

# ---- 蛟魔探秘（钓鱼小游戏，与破除封印共用 pochufengyin 闭环）----
# 入口选项「请将鱼竿借我一用！」点击后 armed 检测窗口标题
# mingwang_diaochushuizhongguaiwu.png（用户素材 25.png，277x24）；窗口
# 出现即复用共享闭环 play_pochufengyin（**点击模式**：开始后按钮为
# 「点击」，与客栈破除封印一致；用户 2026-11-10 更正）。该游戏单次
# 点击仅推进 ~0.1 刻度（真机日志 10 击 P≈1.06），须开冲刺连点
# （mash=True）才能到达目标带——三档节奏 ~3 击/秒 在倒计时内只推到
# P≈2（用户 2026-11-10 反馈「点击频率太低」）；且填充条停手会自动
# 回退（到带后停手即掉回 L 以下失败），连点后还有追帧延迟，故冲刺
# 模式停稳后持续补点维持、不按倒计时停手，标题消失即小游戏结束。
MINGWANG_DIAOYU_TITLE_ICON = "mingwang_diaochushuizhongguaiwu.png"
MINGWANG_DIAOYU_ARM_OPTION = "请将鱼竿借我一用！"          # 入口选项（arm 检测）
MINGWANG_DIAOYU_ARM_TIMEOUT = _pochu_minigame.POCHU_ARM_TIMEOUT      # armed 时限（秒）
MINGWANG_DIAOYU_CHECK_INTERVAL = _pochu_minigame.POCHU_CHECK_INTERVAL  # 检测限频（秒）
_mingwang_diaoyu_armed_at = 0.0   # 入口选项点击时间；>0 且未超时=检测开启
_mingwang_diaoyu_last_check = 0.0 # 上次窗口检测时间（限频）

# ---- 蛟魔探秘钓鱼失败重试（2026-11-17 用户规则）----
# 失败结果面板**标题仍在**、进度条下方居中显示红色「游戏失败」，面板
# 不自动关闭（2026-11-17 13:00 真机：脚本按位置猜成「命中区间」返回，
# 失败面板留在屏幕上，任务追踪点击被面板拦截，任务停住到 300s 跟踪
# 超时才自然重试）。失败后恢复链（用户指定）：
# 1. 点面板右上角 X 关闭（模板 pochu_guanbi.png，正样本 1.00/0.99、
#    非面板帧 ≤0.41；关闭后以标题模板消失确认）；
# 2. 点任务追踪绿色链接重走（复用 task_tracker.track_task，与 follow
#    引擎同一实现）→ 游戏自动寻路/原地重开 NPC 对话 → 入口选项重新
#    arm 钓鱼小游戏。
# 小游戏循环 fail_detect=True 检测「游戏失败」提前返回失败（面板仍在），
# 本链路负责关闭；另有 extras 看门狗兜底「标题消失误判结束、面板残留」
# 的路径。同一任务领取期间连续失败 DIAOYU_FAIL_RETRY_MAX 次后停止自动
# 重试并打一条 ERROR（防控制参数失准时无限重试空转）。
DIAOYU_FAIL_WATCH_INTERVAL = 1.0     # 失败面板看门狗检测限频（热路径，秒）
DIAOYU_FAIL_CLOSE_ICON = _pochu_minigame.POCHU_CLOSE_ICON
DIAOYU_FAIL_CLOSE_REGION = _pochu_minigame.POCHU_CLOSE_REGION
DIAOYU_FAIL_CLOSE_THRESHOLD = _pochu_minigame.POCHU_FAIL_THRESHOLD
DIAOYU_FAIL_CLOSE_TIMEOUT = 4.0      # 点 X 后等待面板关闭的时限（秒，含重试点击）
DIAOYU_FAIL_CLOSE_INTERVAL = 0.5     # 关闭确认轮询间隔（秒）
DIAOYU_FAIL_RETRY_MAX = 5            # 连续失败自动重试上限（同一任务领取期间）
_diaoyu_fail_watch_at = 0.0          # 看门狗上次检测时间（限频）
_diaoyu_fail_streak = 0              # 连续失败重试次数（成功/新领取复位）

# ---- 沙都修罗（高级）：追踪文案触发 → 蟠桃园五路点找 NPC → 点名字上方 ----
# 用户 2026-11-10 流程：跟踪面板内容包含「根据三张蟠桃园残像所示位置」时：
# 1. 不在蟠桃园 → 打开世界地图点「蟠桃园」标签 shijie_pantaoyuan.png
#    （用户 图1，72×28）打开蟠桃园小地图；
# 2. 小地图点击 (739,588) → 自动寻路到 蟠桃园[126,32] 附近 →
#    **先查 NPC 本体模板 mingwang_shaduxiuluo（阈值 0.79，命中直接点
#    模板中心）**，未命中再 OCR「沙都修罗」；未找到依次：
#    (623,476)→[66,87]、(541,428)→[27,112]、(552,310)→[33,171]、
#    (710,393)→[111,130]（2026-11-12 用户新增第五路点）；
# 3. OCR 找到后点击「沙都修罗」文案**上方 40px**（游戏 NPC 名字渲染在
#    角色下方，名字上方即角色本体，用户标定）。
# **OCR 必须排除任务追踪面板里的同名文案**（用户明确）：追踪面板在
# x<400（task_tracker.PANEL_MAX_X），世界区条带从 x=400 起。
# 全部路点均未找到 → 限频重试整条搜索链（NPC 可能在移动）；点击成功后
# 冷却一段时间让对话/战斗接管，防同一次寻路重复点击 NPC。
SHADU_KEYWORD = "根据三张蟠桃园残像所示位置"
SHADU_KEYWORD_SHORT = "蟠桃园残像"      # OCR 漏读前缀/尾字时的兜底触发词
SHADU_MAP = "蟠桃园"  # 世界地图标签统一由 map.WORLD_MAP_LABELS 维护
# 模板校准（2026-11-10 实机世界地图帧，logs/_world_map_probe.png）：目标帧
# 0.9999 @(430,239)；天宫/主城帧 ≤0.28——0.8 阈值（map.find_template 默认）
# 干净分开。
SHADU_NPC_TEXT = "沙都修罗"
# NPC 名 OCR 别名（游戏字体下「罗/羅」偶发繁体输出；用户文案为简体原文）
SHADU_NPC_TEXTS = ("沙都修罗", "沙都修羅")
# NPC 本体模板（用户 图2，62×30）：**先模板后 OCR**——扫描时模板命中
# 直接点击匹配中心；未命中才 OCR「沙都修罗」文案并按文案上方 40px 点击
# （用户 2026-11-10 规则，阈值 0.79 用户指定）。非目标帧交叉验证：现有
# 实机探针帧最高 0.52，0.79 阈值有约 0.27 余量；真实 NPC 帧待实机确认。
SHADU_NPC_ICON = "mingwang_shaduxiuluo.png"
SHADU_NPC_THRESHOLD = 0.75
SHADU_NPC_CLICK_OFFSET = 40            # 点击点 = 文案框中心正上方 40px
SHADU_NPC_BAND = (400, 100, 1200, 760)  # 世界区域 OCR 条带（x<400=追踪面板，排除）
SHADU_OCR_MIN_SCORE = 0.55             # OCR 行置信度下限（同各任务模块）
SHADU_WAYPOINTS = (                    # (小地图点击点, 目标地图坐标)
    ((739, 588), (126, 32)),
    ((623, 476), (66, 87)),
    ((541, 428), (27, 112)),
    ((552, 310), (33, 171)),
    ((710, 393), (111, 130)),          # 2026-11-12 用户新增第五路点
    ((762, 430), (138, 111)),          # 2026-11-18 用户新增第六路点
)
SHADU_POS_TOLERANCE = 10               # 到达搜索点容差（附近即可）
SHADU_ARRIVE_TIMEOUT = 30              # 单个搜索点寻路超时（秒）
SHADU_STUCK_POLLS = 8                  # 位置连续不变轮询数 → 寻路停滞（约 12s）
SHADU_NPC_SCAN_TIMEOUT = 5.0           # 到达后等待 NPC 文案出现的时限（秒）
SHADU_NPC_SCAN_INTERVAL = 0.4          # NPC 扫描间隔（秒）
SHADU_RETRY_AFTER = 5.0                # 全部路点失败后的重试间隔（秒）
SHADU_CLICK_COOLDOWN = 20.0            # 点击 NPC 后冷却（对话/战斗接管，防重复点）
SHADU_ABSENT_RESET = 5                 # 触发词连续缺失轮询数 → 状态复位
_shadu_next_try_at = 0.0               # 下次允许执行搜索链的时间（全链失败限频）
_shadu_click_until = 0.0               # 点击 NPC 后的冷却截止时间
_shadu_absent = 0                      # 触发词连续缺失轮询数


def _find_attr_rect(bgr):
    """定位人物属性标题牌，返回匹配框 (l, t, r, b)；未命中返回 None。

    标题牌与右上打开按钮可能是同图形：优先取按钮区域（
    ATTR_PANEL_BTN_POS ± ATTR_BTN_EXCLUDE_PX）之外的候选，避免面板
    未打开时把按钮当标题牌（接着按「右侧 120px」会读到窗口边缘）；
    全部候选都在按钮区域时才退回最高分候选。
    """
    size = game_map.template_size(RENWUSHUXING_ICON)
    if size is None:
        return None
    w, h = size
    matches = game_map.match_frame_all(bgr, RENWUSHUXING_ICON,
                                       threshold=ATTR_TEMPLATE_THRESHOLD)
    if not matches:
        return None
    chosen = None
    for _score, cx, cy in matches:  # 按分数降序
        if (abs(cx - ATTR_PANEL_BTN_POS[0]) <= ATTR_BTN_EXCLUDE_PX
                and abs(cy - ATTR_PANEL_BTN_POS[1]) <= ATTR_BTN_EXCLUDE_PX):
            continue
        chosen = (cx, cy)
        break
    if chosen is None:
        chosen = (matches[0][1], matches[0][2])
    cx, cy = int(chosen[0]), int(chosen[1])
    l, t = cx - w // 2, cy - h // 2
    return l, t, l + w, t + h


def _attr_panel_rect(win):
    """当前帧匹配人物属性标题牌，返回匹配框或 None（同时返回帧/BGR）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return None, None, None
    bgr = _bgr_frame(img)
    return _find_attr_rect(bgr), img, bgr


def _wait_attr_panel(win, timeout=ATTR_OPEN_TIMEOUT):
    """确保人物属性面板打开，返回标题牌匹配框；失败返回 None。

    面板可能已打开（上轮残留）——先查一帧，未命中才点击按钮（避免
    切换式按钮把已打开的面板关掉）。
    """
    rect, _img, _bgr = _attr_panel_rect(win)
    if rect is not None:
        log("[名望] 人物属性面板已打开，跳过按钮点击", "INFO")
        return rect
    human_click(win, ATTR_PANEL_BTN_POS[0], ATTR_PANEL_BTN_POS[1])
    log(f"[名望] 已点击 {ATTR_PANEL_BTN_POS} 打开人物属性面板", "SYS")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.5)
        rect, _img, _bgr = _attr_panel_rect(win)
        if rect is not None:
            return rect
    return None


def _press_esc(win):
    """Esc 关闭面板兜底：静默模式走消息投递，物理模式仅前台可发键。"""
    if app_utils.silent_mode_on():
        from ..device import silent_click  # 延迟导入（避免模块级循环）
        return silent_click.send_silent_hotkey(win, (), "esc") is not None
    if app_utils.keyboard_ok(win):
        Keyboard.key("esc")
        return True
    return False


def _close_attr_panel(win, rect=None):
    """关闭人物属性面板（三级兜底）：右键标题牌 → 再点按钮 → Esc。

    rect 省略时先定位面板；面板不存在视为已关闭。每次动作前重截一帧
    复核（防面板已自动关闭时右键落到游戏世界），每级动作后轮询标题牌
    消失。返回 (是否关闭, 描述)。关闭方式为首次实现的三级兜底
    （用户未指定），真机跑通后按实际生效的一级收敛。
    """
    fresh, _img, _bgr = _attr_panel_rect(win)
    if fresh is not None:
        rect = fresh
    elif rect is not None:
        return True, "人物属性面板已关闭"
    else:
        return True, "人物属性面板未打开"
    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
    log(f"[名望] 关闭人物属性面板：右键标题牌 @({cx},{cy})", "INFO")
    human_click(win, cx, cy, button="secondary")
    for attempt in range(3):
        if attempt == 1:
            log(f"[名望] 面板仍在，再次点击 {ATTR_PANEL_BTN_POS} 关闭", "WARN")
            human_click(win, ATTR_PANEL_BTN_POS[0], ATTR_PANEL_BTN_POS[1])
        elif attempt == 2:
            log("[名望] 面板仍在，Esc 兜底关闭", "WARN")
            if not _press_esc(win):
                break
        deadline = time.time() + ATTR_CLOSE_TIMEOUT
        while time.time() < deadline:
            rect, _img, _bgr = _attr_panel_rect(win)
            if rect is None:
                return True, "人物属性面板已关闭"
            time.sleep(0.4)
    return False, "人物属性面板关闭失败（右键/按钮/Esc 均未生效）"


def _race_strip_rect(rect, img_size):
    """种族名称 OCR 条带：标题牌右侧 120px、上下各扩 RACE_STRIP_Y_PAD。"""
    l, t, r, b = rect
    w, h = img_size
    return (max(0, r), max(0, t - RACE_STRIP_Y_PAD),
            min(w, r + RACE_STRIP_WIDTH), min(h, b + RACE_STRIP_Y_PAD))


def _race_from_items(items, min_score=RACE_OCR_MIN_SCORE):
    """按 OCR 文案判定种族，返回 (种族或 None, 票数字典, 有效文案行)。

    每行文案命中该族任一关键词即计一票（仙族含角色名 武尊神/云中君/
    玄剑娥——这些名字不含「仙」字）；取票多者。两边同票（含 0 票）
    视为无法判定。其他种族不在此表内（后续 TODO）。
    """
    counts = {race: 0 for race in RACE_KEYWORDS}
    lines = []
    for txt, _rect, score in items:
        if score < min_score:
            continue
        text = norm_text(txt)
        if not text:
            continue
        lines.append(text)
        for race, keywords in RACE_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                counts[race] += 1
    best = max(counts, key=counts.get)
    top = counts[best]
    if top <= 0:
        return None, counts, lines
    if sum(1 for c in counts.values() if c == top) > 1:
        return None, counts, lines  # 两族同票：界面混排/噪声，不判定
    return best, counts, lines


def _ocr_race_strip(img, strip):
    """OCR 种族条带，返回 (种族或 None, 票数, 文案行, 描述)。

    先区域快引擎（原尺寸检测）；未判定出种族时 2x 放大走全帧引擎兜底
    （小块 2x 裁剪依赖 'min'/736 的放大检出小字，同修罗任务块规则；
    本路径只取文案、不映射坐标）。两路都未判定时文案取并集（去重）
    供调用方日志校准。识别结果不足不再重试，由调用方决定是否重开面板。
    """
    items, _ = game_map.ocr_region(img, strip)
    race, counts, lines = _race_from_items(items)
    if race is not None:
        return race, counts, lines, "区域 OCR"
    try:
        from PIL import Image
        crop = img.crop(strip)
        big = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        items2, _ = game_map.ocr_image(big)
        race2, counts2, lines2 = _race_from_items(items2)
        if race2 is not None:
            return race2, counts2, lines2, "2x 放大 OCR"
        merged = list(dict.fromkeys(lines + lines2))
        best = counts2 if sum(counts2.values()) > sum(counts.values()) else counts
        return None, best, merged, "区域 OCR + 2x 放大 OCR"
    except Exception as e:  # 放大兜底失败不影响主路径
        return None, counts, lines, f"2x 放大 OCR 失败: {e}"


def detect_race(win):
    """种族判定（打开人物属性面板 → OCR 标题牌右侧条带 → 关闭面板）。

    返回 (种族字符串 或 None, 描述)。判定不出时描述带 OCR 文案（最多
    8 行）供真机校准；面板打开/关闭失败同样返回 None。
    """
    if is_in_battle(win):
        return None, "战斗中，无法打开人物属性面板判定种族"
    # 场景前置：清理残留地图/物品栏（可能遮挡右上按钮或后续寻路）
    close_scene_panels(win, "名望", quiet=True)
    rect = _wait_attr_panel(win)
    if rect is None:
        return None, f"人物属性面板未打开（未找到 {RENWUSHUXING_ICON}）"

    race, counts, lines = None, {r: 0 for r in RACE_KEYWORDS}, []
    try:
        # 面板打开动画/列表渲染可能未完成：最多两轮 OCR（第二轮重截帧）
        for attempt in (1, 2):
            img = win.capture(force_activate=False)
            if img is None:
                continue
            bgr = _bgr_frame(img)
            rect2 = _find_attr_rect(bgr)
            if rect2 is not None:
                rect = rect2
            strip = _race_strip_rect(rect, img.size)
            race, counts, lines, via = _ocr_race_strip(img, strip)
            log(f"[名望] 种族判定第 {attempt} 轮（{via}）条带 {strip}，"
                f"文案 {lines[:8] or '(无)'}", "INFO")
            if race is not None:
                break
            if attempt == 1:
                time.sleep(0.6)  # 等列表渲染完成再重读一帧
    finally:
        closed, cinfo = _close_attr_panel(win, rect)
        if not closed:
            log(f"[名望] {cinfo}", "WARN")
        elif race is not None:
            log(f"[名望] {cinfo}", "INFO")

    if race is None:
        return None, (f"种族未识别（魔族 {counts['魔族']} 票 / "
                      f"仙族 {counts['仙族']} 票，文案 {lines[:8] or '(无)'}）")
    desc = f"种族 {race}（魔族 {counts['魔族']} 票 / 仙族 {counts['仙族']} 票）"
    return race, desc


def navigate_to_claim_by_map_label(win, claim):
    """小地图快捷领取（名望唯一领取路线）：打开种族地图小地图 → 任务
    筛选 → 点黄色任务 NPC 名。

    调用 map.minimap_quick_navigate（通用能力已下沉）——已在目标地图时
    直接开小地图，否则经世界地图点种族地图标签；勾选底部「任务」筛选后
    点击固定位置的黄色任务 NPC 名（魔族 逆天魔 / 仙族 武尊神，**绝对
    坐标**，只点一次），游戏自动寻路/传送并在到达后自动打开对话框；
    后续由 _claim_and_open_panel 等待对话框并点绿色选项「开启山河
    名望志」。旧「坐标寻路 + Alt+4 清屏 + NPC 模板点击」路线已移除
    （用户 2026-11-16 规则：名望一律走小地图快捷领取）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮小地图快捷领取"
    # 场景前置：清理残留地图/物品栏
    close_scene_panels(win, "名望")
    ok, info = game_map.minimap_quick_navigate(
        win, claim["map"], claim["map_filter_pos"], claim["map_label_pos"])
    if not ok:
        close_scene_panels(win, "名望", quiet=True)
        return False, info
    log(f"[名望] 小地图快捷领取：{info}，黄色「{claim['npc_name']}」"
        f"已触发自动寻路（超时 {MAP_CLAIM_ARRIVE_TIMEOUT}s）", "SYS")
    return True, info


def _jiamo_walk_click(win):
    """点击世界地面模拟玩家行走（蛟魔复仇：到达稳定仍未开战的催战兜底）。

    2026-11-17 用户修正：静默模式下 Alt+D 热键不会让人物随机走动
    （连续多次投递零位移零战斗），点击世界地面模拟玩家行走才是有效
    催战方式——走动本身触发暗雷遭遇。走位点按 JIAMO_WALK_POINTS 轮换；
    调用方已通过位置连续稳定断言人物站定（世界点击须先确认站定，防
    打断自动寻路，性能规则 13），点击走 human_click 全局锁/静默通道。
    返回描述文案。
    """
    global _jiamo_walk_idx
    x, y = JIAMO_WALK_POINTS[_jiamo_walk_idx % len(JIAMO_WALK_POINTS)]
    _jiamo_walk_idx += 1
    human_click(win, x, y)
    return f"已点击世界地面 ({x},{y}) 模拟行走，等待暗雷遇敌"


def _click_claim_option(win, items, img_np):
    """单帧点击领取对话框绿色选项（不使用对话框模板门控）。

    用户 2026-11-10 明确：该对话框不使用对话框门控——本链直接对
    对话区做区域 OCR + 绿字判定，不经过 dialog_ocr_skip/模板门。
    """
    return _click_green_option_once(win, items, img_np,
                                    CLAIM_DIALOG_OPTION, prefix="名望")


def _claim_and_open_panel(win, claim, timeout=None):
    """领取入口链：等自动对话框 → 绿色选项 → 开山河名望面板。

    小地图黄字点击后游戏自动寻路并自动打开对话框（名望唯一领取路线）：
    等待期间**只等绿色选项**，不点 NPC、不点屏幕中间（世界点击会取消
    自动寻路）。每轮询只截一帧（战斗判定/入口匹配/对话区 OCR 同帧）；
    对话框区域直接 OCR，不使用对话框模板门控（用户要求）。
    timeout：整条入口链时限（黄字点击后只需等自动对话框）。
    返回 (是否成功, 描述)。
    """
    if timeout is None:
        timeout = MAP_CLAIM_ARRIVE_TIMEOUT  # 运行时读取，测试可 patch 模块常量
    deadline = time.time() + timeout
    option_click_at = 0.0
    npc_name = claim["npc_name"]
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        bgr = _bgr_frame(img)
        if is_in_battle_frame(bgr):
            time.sleep(2.0)
            continue
        now = time.time()

        # ① 山河名望入口出现（选项点击后才会出现）→ 点击打开领取面板。
        #    全帧匹配仅在此阶段（短窗口）执行，不属于常态轮询热路径。
        if option_click_at > 0.0:
            # 面板可能已随选项点击自动打开（2026-11-16 小地图快捷领取
            # 实机：点「开启山河名望志」后领取面板直接弹出，左缘入口
            # 竖牌变成面板标题牌）——已打开则不再点击入口。
            if _find_panel_anchor(bgr) is not None:
                return True, "山河名望任务领取面板已打开"
            score, pt = game_map.best_match_np(bgr, CLAIM_SHANHE_ICON)
            if score >= CLAIM_SHANHE_THRESHOLD:
                human_click(win, pt[0], pt[1])
                log(f"[名望] 已点击山河名望入口 @({pt[0]},{pt[1]})"
                    f"（匹配 {score:.3f}），任务领取面板已打开", "SYS")
                return True, "已打开山河名望任务领取面板"

        # ② 领取对话框绿色选项（不使用对话框门控）
        items, img_np = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if (now - option_click_at >= OPTION_CLICK_RETRY_INTERVAL
                and _click_claim_option(win, items, img_np)):
            option_click_at = now
            log(f"[名望] 已点击「{CLAIM_DIALOG_OPTION}」，等待山河名望入口出现",
                "SYS")
            time.sleep(1.0)  # 等入口渲染（漏渲染时下一轮重试）
            continue
        time.sleep(0.4)
    return False, (f"领取入口链超时（{timeout}s，未等到{npc_name}"
                   f"自动对话框/绿色选项）")


def _find_panel_anchor(bgr):
    """定位山河名望任务栏锚点（面板顶部图标），返回匹配框或 None。"""
    size = game_map.template_size(PANEL_MAODIAN_ICON)
    if size is None:
        return None
    pt = game_map.match_frame_np(bgr, PANEL_MAODIAN_ICON,
                                 threshold=PANEL_MAODIAN_THRESHOLD)
    if pt is None:
        return None
    w, h = size
    l, t = int(pt[0]) - w // 2, int(pt[1]) - h // 2
    return l, t, l + w, t + h


def _card_rects(anchor_rect):
    """按锚点左下角推导 6 个任务卡片矩形（3 列 × 2 行）。

    第一列 left = 锚点 left、第一行 top = 锚点 bottom + PANEL_CARD_TOP_GAP
    （用户规则 + 2026-11-10 实机标定）。返回 [(row, col, (l, t, r, b)), ...]。
    """
    left0, _t, _r, bottom = anchor_rect
    top0 = bottom + PANEL_CARD_TOP_GAP
    cards = []
    for row in range(PANEL_ROWS):
        for col in range(PANEL_COLS):
            l = left0 + col * PANEL_COL_PITCH
            t = top0 + row * PANEL_ROW_PITCH
            cards.append((row, col, (l, t, l + PANEL_CARD_W,
                                     t + PANEL_CARD_H)))
    return cards


def _inside(rect, region):
    """OCR 行矩形是否完全落在区域内。"""
    return (rect[0] >= region[0] and rect[1] >= region[1]
            and rect[2] <= region[2] and rect[3] <= region[3])


def _card_level(items, card_rect):
    """卡片级别：仅认左上角级别标签区（高级/中级/初级，兼容 OCR 读「級」）。

    不能全卡搜索——解锁卡描述「完成本族高级进阶任务」含「高级」，
    会被误判为高级卡（实机卡片 2,2 实测）。
    """
    l, t, _r, _b = card_rect
    tag = (l, t + 2, l + 50, t + 60)
    for txt, rect, score in items:
        if score < 0.55 or not _inside(rect, tag):
            continue
        text = norm_text(txt).translate(LEVEL_OCR_ALIASES)
        for lv in LEVEL_PRIORITY:
            if lv in text:
                return lv
    return None


def _card_title(items, card_rect):
    """卡片标题：级别标签右侧的头部文本行（描述/接取按钮排除）。"""
    l, t, r, _b = card_rect
    head = (l + 40, t + 5, r - 5, t + 58)
    cands = []
    for txt, rect, score in items:
        if score < 0.55 or not _inside(rect, head):
            continue
        if "接取" in txt:
            continue
        if rect[2] - rect[0] < 10:
            continue  # 零宽 OCR 碎片（实机「心」单字碎片 641..641）
        cands.append((rect[1], rect[0], txt))
    cands.sort()
    return "".join(c[2] for c in cands) if cands else ""


def _scan_panel_cards(win, timeout=PANEL_OPEN_TIMEOUT):
    """等待面板锚点并扫描 6 张任务卡片。

    返回 (cards, anchor_rect, img)；超时未找到锚点返回 (None, None, None)。
    cards 每项：{"row", "col", "rect", "level", "title"}。每张卡一次区域
    OCR（6 次约 1s，仅在领取步骤执行，非轮询热路径）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        bgr = _bgr_frame(img)
        anchor = _find_panel_anchor(bgr)
        if anchor is None:
            time.sleep(0.5)
            continue
        cards = []
        for row, col, rect in _card_rects(anchor):
            items, _ = game_map.ocr_region(img, rect)
            cards.append({"row": row, "col": col, "rect": rect,
                          "level": _card_level(items, rect),
                          "title": _card_title(items, rect)})
        return cards, anchor, img
    return None, None, None


def _card_summary(cards):
    """卡片列表日志文案（级别·标题）。"""
    parts = []
    for c in cards:
        if not c["title"] and c["level"] is None:
            continue
        parts.append(f"{c['level'] or '?'}·{c['title'] or '(未识别)'}")
    return "、".join(parts)


def _verify_claim(win, title, card_rect):
    """点击接取后的确认：接取按钮消失 或 任务进入追踪面板。

    返回 (状态, 描述)；状态：
    - "claimed"：接取按钮已消失 / 任务已进入追踪面板；
    - "daily_done"：出现「今日还请多加歇息，明日再来吧」——今日任务
      已全部完成（用户 2026-11-15 规则）；
    - "unconfirmed"：超时未确认（调用方按已点击处理，避免重复点击接取）。

    今日全部完成提示与玄枢轶闻「少侠已经完成5次任务，请明天再来吧」
    同为**非标准对话框提示**（无标准边框，对话模板门不命中）——不能
    用对话框模板门控，须像玄枢领取校验那样每轮直接区域 OCR 对话区
    （2026-11-15 用户指正）。提示**先于**接取按钮判定：命中即认定
    今日全部完成，无需等满确认窗口（面板仍在场时提示叠加在卡片区上，
    「接取按钮消失=已领取」的信号会被干扰）。
    """
    deadline = time.time() + CLAIM_VERIFY_TIMEOUT
    next_track = 0.0
    region = (card_rect[0] - 4, card_rect[1] - 4,
              card_rect[2] + 4, card_rect[3] + 4)
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        bgr = _bgr_frame(img)
        # 今日全部完成提示：非标准对话框，直接区域 OCR（同玄枢
        # DAILY_LIMIT_TEXT 检测，见函数说明）
        items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
        if _daily_full_dialog_present(items):
            return "daily_done", (f"点击「{title}」接取后出现"
                                  f"「{DAILY_FULL_TEXTS[0]}，"
                                  f"{DAILY_FULL_TEXTS[1]}」")
        pt = game_map.match_frame_np(bgr, PANEL_JIEQU_ICON,
                                     threshold=PANEL_JIEQU_THRESHOLD,
                                     region=region, fallback=False)
        if pt is None:
            return "claimed", "接取按钮已消失"
        now = time.time()
        if now >= next_track:
            next_track = now + 1.5
            # 先以追踪面板标题条模板在场为门：山河名望面板左缘竖牌
            # 文字同为「山河名望」，面板未关且追踪行未渲染时会误判
            if task_tracker.track_content_region(np.asarray(img)) is not None:
                items, img_np = task_tracker.ocr_track(img)
                if task_tracker.has_task_in(items, img_np, title):
                    return "claimed", "任务已进入追踪面板"
        time.sleep(0.5)
    return "unconfirmed", "接取按钮仍在且追踪面板未出现任务"


def _daily_full_dialog_present(items):
    """对话框文案是否为「今日还请多加歇息，明日再来吧」（今日全部完成）。

    items：对话框区域 OCR 行；匹配走 _dialog_text_present（逐行 + 全文
    拼接的模糊匹配，抗 OCR 单字误读/拆行）。
    """
    return any(_dialog_text_present(items, txt) for txt in DAILY_FULL_TEXTS)


def _close_shanhe_panel_safely(win, reason, clear_dialog=True):
    """结束前关闭山河名望面板（可选清标准对话框 + 右键标题牌，最多 3 次）。

    两种结束路径共用（用户规则）：今日任务全部完成（2026-11-15）、
    任务栏无已实现任务（2026-11-17）——残留面板会遮挡后续任务的追踪
    面板与对话框区，且任务已结束不会再有领取动作处理它。面板未打开时
    _close_shanhe_panel 直接按成功返回（结束路径的防御性检查）。
    今日全部完成提示是非标准对话框（无标准边框，同玄枢「请明天再来」
    提示）——daily_done 路径先按模板门控清一次标准对话框（若恰有其它
    标准对话在场），再右键面板标题牌关闭面板；关闭失败最多重试 2 次
    （漂浮提示文字可能短暂遮挡/吞点击，_close_shanhe_panel 每次都先
    复核面板仍在才右键，重试幂等）。
    clear_dialog=False：无已实现任务路径**不得**清对话框——领取面板
    在场时点屏幕中间 (689,415) 恰落在中间列「接取」按钮附近
    (688,423)，一旦对话框模板对面板误命中就会误领未实现任务；该路径
    只右键标题牌关闭。reason：日志前缀（结束原因）。返回关闭结果描述。
    """
    if clear_dialog:
        had, _clicked = clear_blocking_dialog(win, "名望")
        if had:
            log(f"[名望] {reason}：已关闭在场的标准对话框", "SYS")
    desc = "山河名望面板未打开"
    for attempt in (1, 2, 3):
        ok, desc = _close_shanhe_panel(win)
        if ok:
            break
        log(f"[名望] {reason}：{desc}，第 {attempt} 次重试关闭面板", "WARN")
        if clear_dialog:
            clear_blocking_dialog(win, "名望")
        time.sleep(0.5)
    return desc


def _pick_card(cards):
    """从卡片列表选定第一个可用的**已实现任务**卡（含其任务定义）。

    优先级：级别（高级>中级>初级）→ 卡片行序 → 列序。级别未知（任务定义
    或卡片 OCR 为 None）时按卡片实际级别参与排序，都不详排最后，不阻断
    标题匹配。标题模糊匹配。返回 (card, task) 或 (None, None)。
    """
    best = None  # (sort_key, card, task)
    for c in cards:
        if not c["title"]:
            continue
        for task in CLAIM_TASKS:
            # 任务定义级别为 None = 不限定级别；两端都有级别时必须一致
            if (task["level"] is not None and c["level"] is not None
                    and c["level"] != task["level"]):
                continue
            if not _fuzzy_contains(norm_text(c["title"]),
                                   norm_text(task["title"]), threshold=0.8):
                continue
            lv = c["level"] or task["level"]
            rank = LEVEL_PRIORITY.index(lv) if lv in LEVEL_PRIORITY \
                else len(LEVEL_PRIORITY)
            key = (rank, c["row"], c["col"])
            if best is None or key < best[0]:
                best = (key, c, task)
            break  # 同一张卡只匹配一个已实现任务
    if best is None:
        return None, None
    return best[1], best[2]


def _task_list_desc():
    """已实现任务列表文案（日志/失败提示用）。"""
    return "、".join(f"{t['level'] or '任意'}「{t['title']}」"
                     for t in CLAIM_TASKS)


def claim_panel_task(win):
    """在山河名望任务栏中识别并领取第一个可用的已实现任务。

    已实现：高级「蛟魔复仇」「沙海迷踪」「蛟魔探秘」「五庄封印」
    「沙都修罗」（CLAIM_TASKS）。流程：扫描
    6 卡（级别+标题）→ 按 高级>中级>初级 + 卡片顺序选定目标（未找到
    时 1s 后复扫一次，防 OCR 瞬态）→ 在该卡内匹配接取按钮并点击 →
    确认（按钮消失/任务进追踪面板）。
    返回 (状态, 描述)；状态：claimed 已点击领取 / daily_done 今日任务
    已全部完成（点击接取后出现「今日还请多加歇息，明日再来吧」，已
    关闭山河名望面板，用户 2026-11-15 规则）/ not_found 无已实现任务 /
    fail 面板或接取按钮未找到。
    """
    cards, _anchor, img = _scan_panel_cards(win)
    if cards is None:
        return "fail", f"未找到山河名望任务栏（锚点 {PANEL_MAODIAN_ICON} 未命中）"
    summary = _card_summary(cards)
    log(f"[名望] 任务栏识别: {summary or '(无)'}", "SYS")

    target, task = _pick_card(cards)
    if target is None:
        # 一次复扫：卡片渲染/OCR 瞬态可能漏读标题，复扫仍无才判不存在
        log(f"[名望] 首次扫描未找到已实现任务（{_task_list_desc()}），"
            f"1s 后复扫", "WARN")
        time.sleep(0.8)
        cards2, _anchor2, img2 = _scan_panel_cards(win, timeout=3.0)
        if cards2 is not None:
            cards, img = cards2, img2
            summary = _card_summary(cards)
            log(f"[名望] 任务栏复扫识别: {summary or '(无)'}", "SYS")
            target, task = _pick_card(cards)
    if target is None:
        return "not_found", (f"任务栏未找到已实现任务"
                             f"（{_task_list_desc()}），"
                             f"当前卡片: {summary or '(无)'}")

    title, level = task["title"], task["level"]
    level_desc = level or "不限级别"
    card = target["rect"]
    bgr = _bgr_frame(img)
    region = (card[0] - 4, card[1] - 4, card[2] + 4, card[3] + 4)
    score, pt = game_map.best_match_np(bgr, PANEL_JIEQU_ICON, region=region)
    if score < PANEL_JIEQU_THRESHOLD:
        return "fail", (f"「{title}」卡片内未找到接取按钮"
                        f"（最佳匹配 {score:.3f}，阈值 {PANEL_JIEQU_THRESHOLD}）")
    human_click(win, pt[0], pt[1])
    log(f"[名望] 已点击{level_desc}任务「{title}」的接取按钮 @({pt[0]},{pt[1]})"
        f"（匹配 {score:.3f}）", "SYS")
    vstate, vinfo = _verify_claim(win, title, card)
    if vstate == "daily_done":
        # 今日任务已全部完成（用户 2026-11-15 规则）：关闭山河名望
        # 面板，结束本次任务——不计数、不再重试领取
        cdesc = _close_shanhe_panel_safely(win, "今日全部完成")
        log(f"[名望] {vinfo}——今日山河名望任务已全部完成，{cdesc}", "SYS")
        return "daily_done", f"{vinfo}——今日任务已全部完成（{cdesc}）"
    if vstate == "claimed":
        return "claimed", f"已领取{level_desc}任务「{title}」（{vinfo}）"
    return "claimed", (f"已点击{level_desc}任务「{title}」的接取按钮，未检测到"
                       f"确认信号（{vinfo}）——按已领取处理，不重复点击")


def _dismiss_claim_dialog(win, timeout=CLAIM_DIALOG_TIMEOUT):
    """领取后对话框：先点已知选项，无选项才点屏幕中间关闭（可多屏）。

    用户 2026-11-10 规则：接取后出现的对话框用对话框门控判定；带
    绿字选项的对话（如五庄封印「这该怎么办呢，好担心」→「是武尊神
    遣我过来帮忙的。」）必须点选项——点屏幕中间不会关闭选项对话。
    无选项台词复用 dialog_common.clear_blocking_dialog（模板命中才点，
    点后复检模板消失，最多 3 屏）。
    返回 (是否检测并处理, 描述)。
    """
    deadline = time.time() + timeout
    handled = False
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
            items, img_np = game_map.ocr_region(img,
                                                task_tracker.DIALOG_REGION)
            if _click_known_dialog_option(win, items, img_np):
                handled = True
                time.sleep(0.5)  # 让下一屏渲染后再判定
                continue
            had, _clicked = clear_blocking_dialog(win, "名望")
            if had:
                return True, "领取对话框已通过对话框门控点屏幕中间关闭"
        elif handled:
            return True, "领取后对话选项已处理，对话框已关闭"
        time.sleep(0.5)
    if handled:
        return True, "领取后对话选项已处理（对话框可能仍有多屏）"
    return False, "未检测到领取对话框（可能已自动关闭）"


def _task_in_tracker(win, img=None):
    """追踪面板是否出现「山河名望」任务。

    先要求追踪面板标题条模板命中（track_content_region 非空）：面板
    未出现时 ocr_track 回退静态区域 (0,200,400,600)，而山河名望面板
    左缘竖牌文字同为「山河名望」，会被误判为任务存在（2026-11-10）。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return False
    if task_tracker.track_content_region(np.asarray(img)) is None:
        return False
    items, img_np = task_tracker.ocr_track(img)
    return task_tracker.has_task_in(items, img_np, TASK_TITLE)


def _arm_shahai_start():
    """arm 沙海迷踪「开始」按钮检测窗口（入口选项点击成功后调用）。"""
    global _shahai_armed_at
    _shahai_armed_at = time.time()


def _click_armed_start(win, armed_at, icon, threshold, timeout, label):
    """armed 窗口内检测「开始」按钮，出现即点击（进入战斗）。

    共用实现（沙海迷踪 / 净化水晶丛林）：窗口超时自动关闭（战斗由
    follow_task 等待）；未 armed / 限频路径零成本。返回
    (是否已点击, 更新后的 armed 时间戳)。
    """
    if armed_at <= 0:
        return False, armed_at
    if time.time() - armed_at > timeout:
        log(f"[名望] {label}「开始」按钮检测窗口超时（未出现），"
            f"由任务追踪变化兜底", "WARN")
        return False, 0.0
    img = win.capture(force_activate=False)
    if img is None:
        return False, armed_at
    score, pt = game_map.best_match_np(_bgr_frame(img), icon)
    if score < threshold:
        return False, armed_at
    human_click(win, pt[0], pt[1])
    log(f"[名望] {label}：已点击「开始」@({pt[0]},{pt[1]})"
        f"（匹配 {score:.3f}），等待后续流程", "SYS")
    return True, 0.0


def _maybe_click_shahai_start(win):
    """armed 窗口内检测沙海迷踪「开始」按钮，出现即点击进入小游戏。

    用户 2026-11-10 规则：入口对话选「我已经准备好了」后识别
    mingwang_chenjiuditu_kaishi 并点击。点击成功后 arm 小游戏弹窗
    检测窗口（_maybe_play_shahai），由后续轮询阻塞执行「记住混沌」
    闭环直到弹窗关闭。窗口超时自动关闭。返回是否已点击。
    """
    global _shahai_armed_at, _shahai_game_armed_at, _shahai_game_last_check
    clicked, _shahai_armed_at = _click_armed_start(
        win, _shahai_armed_at, SHAHAI_START_ICON, SHAHAI_START_THRESHOLD,
        SHAHAI_ARM_TIMEOUT, "沙海迷踪")
    if clicked:
        _shahai_game_armed_at = time.time()
        _shahai_game_last_check = 0.0
    return clicked


# ---- 沙海迷踪小游戏：黑点检测 / 阶段识别 / 点击净化 ----














def play_shahai_miyong(win, first_img=None, prefix="名望"):
    """沙海迷踪闭环（实现在 minigames/shahai.py，任务侧保留薄包装）。"""
    return _shahai_minigame.play_shahai_miyong(
        win, first_img=first_img, prefix=prefix)


def _maybe_play_shahai(win):
    """armed 窗口内检测沙海迷踪小游戏弹窗并阻塞执行闭环，返回是否已处理。

    点击「开始」后 arm（_maybe_click_shahai_start）；检测限频
    SHAHAI_GAME_CHECK_INTERVAL，未 armed/限频未到/未命中零操作。
    命中后捕获的帧直接传给 play_shahai_miyong 复用（不重复截窗）。
    """
    global _shahai_game_armed_at, _shahai_game_last_check
    if _shahai_game_armed_at <= 0:
        return False
    if time.time() - _shahai_game_armed_at > SHAHAI_GAME_ARM_TIMEOUT:
        _shahai_game_armed_at = 0.0
        log("[名望] 沙海迷踪小游戏弹窗未出现，检测窗口超时", "WARN")
        return False
    now = time.time()
    if now - _shahai_game_last_check < SHAHAI_GAME_CHECK_INTERVAL:
        return False
    _shahai_game_last_check = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if not _shahai_game_present(img):
        return False
    _shahai_game_armed_at = 0.0
    ok, info = play_shahai_miyong(win, first_img=img)
    log(f"[名望] {info}", "SYS" if ok else "WARN")
    return True


def _arm_qingxi_start():
    """arm 净化水晶丛林「开始」按钮检测窗口（入口选项点击成功后调用）。"""
    global _qingxi_armed_at
    _qingxi_armed_at = time.time()


def _maybe_click_qingxi_start(win):
    """armed 窗口内检测净化水晶丛林「开始」按钮，出现即点击（进入战斗）。

    用户 2026-11-10 规则：入口对话选「马上净化水晶丛林上的混沌。」后
    识别 mingwang_qingxishuijing_kaishi 并点击，无需其它操作，等待进入
    战斗，战斗结束即任务完成（同沙海迷踪）。窗口超时自动关闭。
    返回是否已点击。
    """
    global _qingxi_armed_at
    clicked, _qingxi_armed_at = _click_armed_start(
        win, _qingxi_armed_at, QINGXI_START_ICON, QINGXI_START_THRESHOLD,
        QINGXI_ARM_TIMEOUT, "净化水晶丛林")
    return clicked


def _arm_mingwang_diaoyu_check(opt):
    """钓鱼入口选项被点击 → 开启小游戏窗口检测（armed 门控）。"""
    global _mingwang_diaoyu_armed_at
    if opt == MINGWANG_DIAOYU_ARM_OPTION:
        _mingwang_diaoyu_armed_at = time.time()
        log(f"[名望] 已点击蛟魔探秘钓鱼入口选项，开启小游戏窗口检测"
            f"（{MINGWANG_DIAOYU_ARM_TIMEOUT}s 内有效）", "SYS")


def _diaoyu_fail_panel_present(bgr):
    """失败结果面板在场：标题 + 「游戏失败」双模板同一帧命中（场景锚点）。

    只用标题判在场不够——正常游戏中的面板也有标题（X 按钮此时不可用于
    关闭）；失败结果面板的区分信号是进度条下方的红字，模板
    pochu_youxishibai.png 真机交叉验证正样本 1.00 / 非面板帧 0.09~0.10。
    """
    if bgr is None:
        return False
    if game_map.match_frame_np(
            bgr, MINGWANG_DIAOYU_TITLE_ICON,
            threshold=_pochu_minigame.POCHU_THRESHOLD,
            region=_pochu_minigame.POCHU_REGION, fallback=False) is None:
        return False
    return game_map.match_frame_np(
        bgr, _pochu_minigame.POCHU_FAIL_ICON,
        threshold=_pochu_minigame.POCHU_FAIL_THRESHOLD,
        region=_pochu_minigame.POCHU_FAIL_REGION, fallback=False) is not None


def _diaoyu_panel_present(bgr):
    """钓鱼面板（任意状态）在场：标题模板命中。"""
    return bgr is not None and game_map.match_frame_np(
        bgr, MINGWANG_DIAOYU_TITLE_ICON,
        threshold=_pochu_minigame.POCHU_THRESHOLD,
        region=_pochu_minigame.POCHU_REGION, fallback=False) is not None


def _close_diaoyu_fail_panel(win):
    """点面板右上角 X 关闭失败结果面板，轮询标题模板确认关闭。

    前置条件：每轮点击前复查「标题 + 游戏失败」双模板（场景锚点 +
    目标唯一性，X 模板 16×20 单独使用不做唯一性假设）；面板已不在场
    （标题消失）直接按成功返回。X 点击无效时在
    DIAOYU_FAIL_CLOSE_TIMEOUT 内重试，超时返回失败由看门狗下轮再试。
    返回 (是否已关闭, 描述)。
    """
    deadline = time.time() + DIAOYU_FAIL_CLOSE_TIMEOUT
    clicks = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(DIAOYU_FAIL_CLOSE_INTERVAL)
            continue
        bgr = _bgr_frame(img)
        if not _diaoyu_panel_present(bgr):
            return True, f"失败面板已关闭（X 点击 {clicks} 次）"
        if not _diaoyu_fail_panel_present(bgr):
            # 面板在场但不是失败结果态（防御：正常游戏中的面板不得点 X）
            return False, "面板在场但非失败结果态，未点击 X"
        score, pt = game_map.best_match_np(
            bgr, DIAOYU_FAIL_CLOSE_ICON, region=DIAOYU_FAIL_CLOSE_REGION)
        if pt is None or score < DIAOYU_FAIL_CLOSE_THRESHOLD:
            log(f"[名望] 蛟魔探秘钓鱼失败：面板 X 未识别"
                f"（分数 {score:.2f}），下轮重试", "WARN")
            time.sleep(DIAOYU_FAIL_CLOSE_INTERVAL)
            continue
        human_click(win, pt[0], pt[1])
        clicks += 1
        log(f"[名望] 蛟魔探秘钓鱼失败：已点击面板 X 关闭 @({pt[0]},{pt[1]})"
            f"（第 {clicks} 次）", "SYS")
        time.sleep(DIAOYU_FAIL_CLOSE_INTERVAL)
    return False, (f"点击 X {clicks} 次后面板仍未关闭"
                   f"（{DIAOYU_FAIL_CLOSE_TIMEOUT:.0f}s 超时）")


def _retry_diaoyu_after_fail(win):
    """失败恢复链：关失败面板 → 点任务追踪绿字重走。

    任务追踪点击复用 task_tracker.track_task（与 follow 引擎同一实现，
    面板锚点/内容行/绿字定位共用）：点击后游戏自动寻路或在原地重新
    打开 NPC 对话（2026-11-17 13:04 实测点后 1s 出对话），入口选项
    「请将鱼竿借我一用！」由 DIALOG_OPTION_MAP 处理并重新 arm 钓鱼。
    返回 (是否已完成重试动作, 描述)。
    """
    closed, cinfo = _close_diaoyu_fail_panel(win)
    if not closed:
        return False, cinfo
    ok, info = task_tracker.track_task(win, TASK_TITLE)
    if ok:
        return True, f"{cinfo}；{info}（已重新点击任务追踪，重走蛟魔探秘）"
    return False, f"{cinfo}；任务追踪绿字点击失败: {info}"


def _maybe_retry_diaoyu_fail(win, items=None):
    """失败面板看门狗（extras 链每轮询调用，限频）：失败 → 关面板重走。

    覆盖两条失败路径：
    1. 小游戏循环 fail_detect 提前返回失败——窗口检测链路本轮已返回
       True，下一轮由本看门狗接管；
    2. 旧路径「标题瞬时消失误判结束」后面板重新出现在屏幕上（2026-11-17
       13:00 实机：循环按位置猜成命中区间返回成功，失败面板残留，任务
       追踪点击被面板拦截，任务卡到 300s 跟踪超时）——看门狗不依赖
       armed/小游戏返回结果，面板在场即恢复。
    items 门控：追踪面板 OCR 行不含「蛟魔探秘」时不截帧（其他任务零
    额外开销；失败面板不遮挡追踪面板，标题行每轮可见）。
    连续失败超过 DIAOYU_FAIL_RETRY_MAX 后停止自动重试并告警一次
    （防小游戏控制失准时无限空转）。返回是否已处理。
    """
    global _diaoyu_fail_watch_at, _diaoyu_fail_streak
    if _diaoyu_fail_streak >= DIAOYU_FAIL_RETRY_MAX:
        return False
    if items is not None and not any(
            "蛟魔探秘" in norm_text(txt)
            or _fuzzy_contains(norm_text(txt), "蛟魔探秘", threshold=0.8)
            for txt, _r, _s in items):
        return False
    now = time.time()
    if now - _diaoyu_fail_watch_at < DIAOYU_FAIL_WATCH_INTERVAL:
        return False
    _diaoyu_fail_watch_at = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if not _diaoyu_fail_panel_present(_bgr_frame(img)):
        return False
    _diaoyu_fail_streak += 1
    log(f"[名望] 蛟魔探秘钓鱼失败：检测到失败结果面板，关闭面板并重走任务"
        f"（连续第 {_diaoyu_fail_streak}/{DIAOYU_FAIL_RETRY_MAX} 次）", "SYS")
    ok, info = _retry_diaoyu_after_fail(win)
    log(f"[名望] 蛟魔探秘钓鱼失败重试: {info}", "SYS" if ok else "WARN")
    if _diaoyu_fail_streak >= DIAOYU_FAIL_RETRY_MAX:
        log(f"[名望] 蛟魔探秘钓鱼连续失败 {_diaoyu_fail_streak} 次，"
            f"停止自动重试（检查小游戏控制参数/站位）", "ERROR")
    return True


def _reset_diaoyu_fail_state():
    """钓鱼失败重试状态复位（新任务领取/小游戏成功时调用）。"""
    global _diaoyu_fail_watch_at, _diaoyu_fail_streak
    _diaoyu_fail_watch_at = 0.0
    _diaoyu_fail_streak = 0


def _mingwang_diaoyu_window_check(win):
    """检测并执行蛟魔探秘钓鱼小游戏（armed 门控 + 限频），返回是否已处理。

    与破除封印一致：入口选项被点击后（_arm_mingwang_diaoyu_check）才在
    POCHU_REGION 内匹配窗口标题 mingwang_diaochushuizhongguaiwu.png；
    窗口出现即阻塞执行共享闭环 pochufengyin.play_pochufengyin（**点击
    模式 + 冲刺连点 mash=True**：开始后按钮为「点击」，该游戏单击仅推进
    ~0.1 刻度，按三档节奏点不到目标带；fail_detect=True 检测「游戏失败」
    提前返回），标题消失即结束。检测限频
    MINGWANG_DIAOYU_CHECK_INTERVAL；未 armed/
    限频未到/未命中返回 False（热路径零成本）。extras 回调不携带整帧，
    armed 窗口内按限频自行截一帧（1s 一次）。
    失败结果面板在场时不再进入闭环（也不 disarm）——关闭面板与重走
    由 _maybe_retry_diaoyu_fail 看门狗负责。
    """
    global _mingwang_diaoyu_armed_at, _mingwang_diaoyu_last_check
    if _mingwang_diaoyu_armed_at <= 0 \
            or time.time() - _mingwang_diaoyu_armed_at \
            > MINGWANG_DIAOYU_ARM_TIMEOUT:
        return False
    if is_in_battle(win):
        return False
    now = time.time()
    if now - _mingwang_diaoyu_last_check < MINGWANG_DIAOYU_CHECK_INTERVAL:
        return False
    _mingwang_diaoyu_last_check = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    bgr = _bgr_frame(img)
    score, wpt = game_map.best_match_np(
        bgr, MINGWANG_DIAOYU_TITLE_ICON,
        region=_pochu_minigame.POCHU_REGION)
    if wpt is None or score < _pochu_minigame.POCHU_THRESHOLD:
        return False
    if _diaoyu_fail_panel_present(bgr):
        # 失败结果面板：交给看门狗关闭+重走，绝不再次进入闭环
        # （结果态没有可玩内容，进入只会空点面板按钮）
        return False
    log(f"[名望] 检测到蛟魔探秘钓鱼窗口 @({wpt[0]},{wpt[1]})"
        f"（匹配度 {score:.2f}）", "SYS")
    ok, info = _pochu_minigame.play_pochufengyin(
        win, MINGWANG_DIAOYU_TITLE_ICON, prefix="名望", label="蛟魔探秘钓鱼",
        mash=True, fail_detect=True)
    if ok:
        _mingwang_diaoyu_armed_at = 0.0
        _reset_diaoyu_fail_state()  # 成功：连续失败计数清零
    log(f"[名望] {info}", "SYS" if ok else "WARN")
    return True


# ---- 沙都修罗执行（追踪文案触发 → 蟠桃园五路点找 NPC → 点名字上方）----
def _reset_shadu_state():
    """沙都修罗搜索状态复位（触发词消失/新任务防残留）。"""
    global _shadu_next_try_at, _shadu_click_until, _shadu_absent
    _shadu_next_try_at = 0.0
    _shadu_click_until = 0.0
    _shadu_absent = 0


def _shadu_trigger(sig):
    """追踪面板文案是否触发沙都修罗搜索（精确 + 短词模糊兜底）。

    短词「蟠桃园残像」模糊阈值 0.8：5 字中 1 字 OCR 误读（如
    「残像」→「残相」）即 4/5=0.8，须能触发；无关追踪文案不含
    「蟠桃园残…」形态，误触发风险低。
    """
    if SHADU_KEYWORD in sig or SHADU_KEYWORD_SHORT in sig:
        return True
    return _fuzzy_contains(sig, SHADU_KEYWORD_SHORT, threshold=0.8)


def _shadu_npc_point(items):
    """从世界区 OCR 行定位「沙都修罗」，返回文案上方点击点或 None。

    排除追踪面板同名文案（x < SHADU_NPC_BAND[0]）——用户 2026-11-10
    明确「注意不是任务追踪中的文案」。点击点取文案框中心正上方
    SHADU_NPC_CLICK_OFFSET 像素（游戏 NPC 名字渲染在角色下方，名字
    上方即角色本体，用户标定）。
    """
    for txt, rect, score in items:
        if score < SHADU_OCR_MIN_SCORE:
            continue
        if rect[0] < SHADU_NPC_BAND[0]:
            continue
        text = norm_text(txt)
        if not any(name in text for name in SHADU_NPC_TEXTS) \
                and not _fuzzy_contains(text, SHADU_NPC_TEXT, threshold=0.85):
            continue
        x = (rect[0] + rect[2]) // 2
        y = rect[1] - SHADU_NPC_CLICK_OFFSET
        if y <= 0:
            continue
        return int(x), int(y)
    return None


def _shadu_wait_arrive(win, target, timeout=SHADU_ARRIVE_TIMEOUT):
    """等待寻路到达搜索点（坐标进入容差）。每轮只截一帧并复用战斗判定。"""
    deadline = time.time() + timeout
    last = None
    unchanged = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(1.0)
            continue
        if is_in_battle_frame(_bgr_frame(img)):
            return False, "战斗中，暂停搜索"
        pos = game_map.read_map_position(win, img=img)
        if pos is not None:
            if (pos[0].strip() == SHADU_MAP
                    and abs(pos[1] - target[0]) <= SHADU_POS_TOLERANCE
                    and abs(pos[2] - target[1]) <= SHADU_POS_TOLERANCE):
                return True, f"已到达{SHADU_MAP}[{target[0]},{target[1]}]附近"
            if pos == last:
                unchanged += 1
            else:
                unchanged = 0
            last = pos
            if unchanged >= SHADU_STUCK_POLLS:
                return False, (f"位置无变化（{pos[0]}[{pos[1]},{pos[2]}]），"
                               f"寻路停滞")
        time.sleep(1.5)
    return False, f"等待到达超时（{timeout}s）"


def _shadu_scan_npc(win, timeout=SHADU_NPC_SCAN_TIMEOUT):
    """到达搜索点后限时扫描「沙都修罗」NPC，返回 (点击点或 None, 描述)。

    用户 2026-11-10 规则：**先查 NPC 本体模板** mingwang_shaduxiuluo
    （阈值 0.79），命中直接点匹配中心；模板未命中才 OCR「沙都修罗」
    文案（排除任务追踪面板同名文案）并点文案上方 40px。每轮询只截一帧，
    模板匹配吃 BGR、OCR 吃 PIL 帧。
    """
    deadline = time.time() + timeout
    lines = []
    score = 0.0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(SHADU_NPC_SCAN_INTERVAL)
            continue
        score, tpt = game_map.best_match_np(_bgr_frame(img), SHADU_NPC_ICON,
                                            region=SHADU_NPC_BAND)
        if score >= SHADU_NPC_THRESHOLD:
            return (int(tpt[0]), int(tpt[1])), \
                f"模板命中「{SHADU_NPC_TEXT}」（匹配 {score:.3f}）"
        items, _ = game_map.ocr_region(img, SHADU_NPC_BAND)
        pt = _shadu_npc_point(items)
        if pt is not None:
            return pt, f"文案找到「{SHADU_NPC_TEXT}」"
        lines = [norm_text(t) for t, _r, s in items
                 if s >= SHADU_OCR_MIN_SCORE]
        time.sleep(SHADU_NPC_SCAN_INTERVAL)
    return None, (f"模板/文案均未找到「{SHADU_NPC_TEXT}」"
                  f"（模板最佳 {score:.3f}，OCR 行 {lines[:8] or '(无)'}）")


def _shadu_search_npc(win):
    """按用户标定的五个路点依次寻找并点击沙都修罗，返回 (是否已点击, 描述)。"""
    close_scene_panels(win, "名望")
    pos = game_map.read_map_position(win)
    if pos is None or pos[0].strip() != SHADU_MAP:
        loc = "识别失败" if pos is None else f"{pos[0]}[{pos[1]},{pos[2]}]"
        ok, info = game_map.open_mini_map_by_name(win, SHADU_MAP)
        if not ok:
            return False, f"前往{SHADU_MAP}失败（当前 {loc}）：{info}"
        log(f"[名望] 沙都修罗：不在{SHADU_MAP}（{loc}），已打开"
            f"{SHADU_MAP}小地图", "SYS")
    for idx, (mini_click, target) in enumerate(SHADU_WAYPOINTS, 1):
        if not game_map.is_mini_map_open(win):
            ok, info = game_map.open_mini_map(win)
            if not ok:
                return False, f"打开小地图失败：{info}"
        human_click(win, mini_click[0], mini_click[1])
        log(f"[名望] 沙都修罗：点击第 {idx}/{len(SHADU_WAYPOINTS)} 个搜索点 "
            f"{mini_click}，前往{SHADU_MAP}[{target[0]},{target[1]}]", "SYS")
        time.sleep(1.0)  # 等自动寻路启动
        close_scene_panels(win, "名望")
        arrived, ainfo = _shadu_wait_arrive(win, target)
        if "战斗中" in ainfo:
            return False, ainfo
        pt, ninfo = _shadu_scan_npc(win)
        if pt is None:
            log(f"[名望] 沙都修罗：第 {idx} 个搜索点{ainfo}，{ninfo}，"
                f"继续下一个", "INFO" if arrived else "WARN")
            continue
        human_click(win, pt[0], pt[1])
        return True, (f"已点击{SHADU_NPC_TEXT}上方 @({pt[0]},{pt[1]})"
                      f"（第 {idx} 个搜索点，{ainfo}，{ninfo}）")
    return False, (f"{len(SHADU_WAYPOINTS)} 个搜索点均未找到"
                   f"「{SHADU_NPC_TEXT}」")


def _handle_shadu_xiuluo(win, items, img_np):
    """沙都修罗搜索状态机（follow extras 通道，每轮询一次）。

    追踪面板文案含「根据三张蟠桃园残像所示位置」（或短词「蟠桃园残像」）
    时执行搜索链：不在蟠桃园先经世界地图进入 → 五个路点依次寻路 +
    世界区域 OCR 找 NPC（排除追踪面板同名文案）→ 找到点名字上方 40px。
    全链失败限频重试；点击成功后冷却让对话/战斗接管，防重复点 NPC；
    对话框在场时暂停搜索（先由对话表/剧情兜底推进，防误点 NPC）。
    触发词连续缺失 SHADU_ABSENT_RESET 轮询复位状态。
    返回是否已处理（点击了 NPC）。
    """
    global _shadu_next_try_at, _shadu_click_until, _shadu_absent
    sig = _seal_sig(items)
    if not _shadu_trigger(sig):
        _shadu_absent += 1
        if _shadu_absent >= SHADU_ABSENT_RESET:
            _reset_shadu_state()
        return False
    _shadu_absent = 0
    now = time.time()
    if now < _shadu_next_try_at or now < _shadu_click_until:
        return False
    # 对话框在场先让对话处理（选项表/剧情兜底推进后再搜索，防对话期间
    # 走到 NPC 处误点）。此检查仅在冷却结束后的搜索尝试时执行（≤1 次/5s），
    # 不在每轮询热路径上。
    img = win.capture(force_activate=False)
    if img is not None and dialog_frame_score(_bgr_frame(img)) \
            >= DIALOG_FRAME_THRESHOLD:
        return False
    ok, info = _shadu_search_npc(win)
    if ok:
        _shadu_click_until = time.time() + SHADU_CLICK_COOLDOWN
        log(f"[名望] 沙都修罗：{info}", "SYS")
        return True
    _shadu_next_try_at = time.time() + SHADU_RETRY_AFTER
    log(f"[名望] 沙都修罗：{info}，{SHADU_RETRY_AFTER:.0f}s 后重试", "WARN")
    return False


# ---- 五庄封印执行（追踪双链接 → 坐标稳定到达 → 任务储物格用唤风符）----
def _reset_seal_state():
    """五庄封印状态机复位（新任务领取成功/跨任务防残留）。"""
    global _seal_phase, _seal_elems, _seal_link_idx, _seal_stable, \
        _seal_last_pos, _seal_need_move, _seal_walk_from, _seal_move_stuck, \
        _seal_use_at, _seal_use_retries, _seal_missing, _seal_both_warned, \
        _seal_dlg_streak, _seal_dlg_click_at, _seal_bag_close_at
    _seal_phase = 0
    _seal_elems = []
    _seal_link_idx = 0
    _seal_stable = 0
    _seal_last_pos = None
    _seal_need_move = False
    _seal_walk_from = None
    _seal_move_stuck = 0
    _seal_use_at = 0.0
    _seal_use_retries = 0
    _seal_missing = 0
    _seal_both_warned = False
    _seal_dlg_streak = 0
    _seal_dlg_click_at = 0.0
    _seal_bag_close_at = 0.0


def _seal_frame(win, items):
    """extras 链共享帧（按 id(items) 缓存），返回 (RGB, BGR) numpy 帧。

    同五倍显形镜的 extras 帧复用：follow 每轮询调一次 extras，本处理器需要整帧
    读取地图坐标/对话框模板（OCR 掩码帧无位置区），按 items 对象 id
    缓存避免同轮询重复截窗（性能规则 1：每轮询只截一帧）。
    """
    global _seal_frame_key, _seal_frame_rgb, _seal_frame_bgr, _seal_frame_pos
    key = id(items)
    if _seal_frame_key != key:
        img = win.capture(force_activate=False)
        _seal_frame_key = key
        _seal_frame_pos = _SEAL_POS_UNSET
        if img is None:
            _seal_frame_rgb = _seal_frame_bgr = None
        else:
            _seal_frame_rgb = np.asarray(img)[:, :, :3]
            _seal_frame_bgr = cv2.cvtColor(_seal_frame_rgb,
                                           cv2.COLOR_RGB2BGR)
    return _seal_frame_rgb, _seal_frame_bgr


def _seal_pos(win, items):
    """当前轮询共享帧上的位置读取（结果按帧缓存，None 也缓存）。"""
    global _seal_frame_pos
    rgb, _ = _seal_frame(win, items)
    if _seal_frame_pos is not _SEAL_POS_UNSET:
        return _seal_frame_pos
    _seal_frame_pos = (None if rgb is None
                       else game_map.read_map_position(win, img=rgb))
    return _seal_frame_pos


def _seal_sig(items):
    """追踪面板文案签名（只取面板左列，排除对话框/浮字；抗 OCR 拆行）。"""
    return "".join(norm_text(t) for t, r, s in items
                   if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X)


def _seal_underline_point(img_np, rect, x):
    """在 OCR 行框底附近找距近似 x 最近的亮绿下划线段，返回点击点。

    通用能力已下沉 task_tracker.underline_point（2026-11-17 星魂归位
    复用同一「一行多个绿字链接按 x 定位」需求）；本函数保留为任务侧
    调用名，行为不变。
    """
    return task_tracker.underline_point(img_np, rect, x)


def _seal_parse_links(sig):
    """从追踪签名解析封印点元素（规范字，按文本顺序）。

    追踪文案如「在五庄观找到金藏之位和土藏之位的风暴封印处」——五行
    任选两个（金木水火土，实机已见 金+土、水+火），链接名「X藏之位」。
    OCR 形近字经 SEAL_ELEM_ALIASES 归一；去重保序。追踪文案拆行不影响
    （签名是各 OCR 行规范化后拼接，无换行）。
    """
    elems = []
    for m in re.finditer(r"[金全木未本水火灭土士]藏", sig):
        el = SEAL_ELEM_ALIASES[m.group(0)[0]]
        if el not in elems:
            elems.append(el)
    return elems


def _seal_link_keywords(link_idx, elems=None):
    """目标元素的可匹配关键词（含 OCR 形近字别名），如 ('金藏',)。"""
    elems = _seal_elems if elems is None else elems
    if not elems:
        return ()
    el = elems[min(link_idx, len(elems) - 1)]
    return tuple(f"{a}藏" for a, canon in SEAL_ELEM_ALIASES.items()
                 if canon == el)


def _seal_link_name(link_idx):
    """目标元素全名（日志/展示用），如「金藏之位」。"""
    if not _seal_elems:
        return "封印点"
    el = _seal_elems[min(link_idx, len(_seal_elems) - 1)]
    return el + SEAL_LINK_NAME_SUFFIX


def _seal_pick_next(items, img_np, exclude=None):
    """除 exclude 外第一个未变红的封印点下标；无可选返回 None。

    完成顺序不固定（游戏可能让玩家先做任一封印点，2026-11-17 实机：
    文案「金藏+土藏」先走到金藏）——按颜色选下一个，而非固定 0→1。
    颜色读不出（None）按未完成处理（宁可多点一次）。
    exclude 用于「坐标离开五庄观视为本点完成」：链接可能仍是绿的，
    必须排除当前点再选另一个，否则会误判「两个都已完成」空转。
    """
    for i in range(len(_seal_elems)):
        if i == exclude:
            continue
        if _seal_link_color(items, img_np, _seal_link_keywords(i)) != "red":
            return i
    return None


def _seal_link_point(items, img_np, keyword):
    """定位追踪面板含 keyword 的绿字链接，返回点击点或 None。

    keyword 可为字符串或关键词元组（OCR 形近字别名，如 木藏/水藏）；
    文本内字符位置按比例映射（中文等宽渲染，误差 <1 字宽），再由
    _seal_underline_point 在下划线带内吸附到实际链接段。关键词可
    ＜链接全名（如「木藏」——「木藏之位」常被 OCR 拆到两行）。
    """
    keywords = (keyword,) if isinstance(keyword, str) else tuple(keyword)
    for txt, rect, score in items:
        if score < 0.55:
            continue
        kw = next((k for k in keywords if k in txt), None)
        if kw is None:
            continue
        n = max(1, len(txt))
        idx = txt.find(kw)
        frac = (idx + len(kw) / 2.0) / n
        x = rect[0] + (rect[2] - rect[0]) * frac
        pt = _seal_underline_point(img_np, rect, x)
        if pt is not None:
            return pt
        return int(x), rect[3] - 2
    return None


def _seal_link_color(items, img_np, keyword):
    """判定追踪面板链接当前颜色：'red'（已完成）/ 'green' / None。

    用户 2026-11-10 实测：封印点用符后链接由亮绿变红（可能立刻、也
    可能战斗结束后变红），红=该点完成，须去点下一个链接；任务整体
    消失才是完成。游戏链接颜色为纯色（亮绿 (0,255,0) / 红 (255,0,0)），
    按文本内字符位置比例定位关键词区域，统计红/绿像素主导色。
    keyword 支持字符串或关键词元组（木藏/水藏 等 OCR 形近字）。
    """
    keywords = (keyword,) if isinstance(keyword, str) else tuple(keyword)
    for txt, rect, score in items:
        if score < 0.55:
            continue
        kw = next((k for k in keywords if k in txt), None)
        if kw is None:
            continue
        n = max(1, len(txt))
        idx = txt.find(kw)
        w = rect[2] - rect[0]
        pad = w / n * 0.5  # 半字宽余量（覆盖字形边缘，不侵入相邻链接）
        x0 = rect[0] + w * idx / n - pad
        x1 = rect[0] + w * (idx + len(kw)) / n + pad
        y0 = max(0, rect[1])
        y1 = min(img_np.shape[0], rect[3] + 8)  # 含下划线带
        l, r = max(0, int(x0)), min(img_np.shape[1], int(x1))
        if r <= l or y1 <= y0:
            continue
        region = img_np[y0:y1, l:r].astype(int)
        if region.size == 0:
            continue
        red = int(((region[:, :, 0] >= 150)
                   & (region[:, :, 0] - region[:, :, 1] >= 40)
                   & (region[:, :, 0] - region[:, :, 2] >= 40)).sum())
        green = int(((region[:, :, 1] >= 150)
                     & (region[:, :, 1] - region[:, :, 0] >= 40)
                     & (region[:, :, 1] - region[:, :, 2] >= 40)).sum())
        if red >= 8 and red > green:
            return "red"
        if green >= 8 and green > red:
            return "green"
        return None
    return None


def _seal_green_picker(items, img_np):
    """track_task green_picker：五庄封印按当前目标链接（动态元素）定位。

    `_seal_elems` 尚未由状态机解析时（引擎先于点绿字）就地按当前追踪
    文案解析，避免退回行级默认点击误点「唤风符」绿字链接。
    当前目标关键词未读出时退回任一封印链接。非五庄任务/无封印链接
    返回 None，保持 track_task 原有行级选择行为。
    """
    elems = _seal_elems or _seal_parse_links(_seal_sig(items))
    if not elems:
        return None
    idx = min(_seal_link_idx, len(elems) - 1)
    pt = _seal_link_point(items, img_np, _seal_link_keywords(idx, elems))
    if pt is not None:
        return pt
    for i in range(len(elems)):
        pt = _seal_link_point(items, img_np, _seal_link_keywords(i, elems))
        if pt is not None:
            return pt
    return None


def _click_seal_link(win, items, img_np, link_idx=None):
    """点击指定（默认当前）封印链接；未识别到返回 False（下轮询重试）。"""
    elems = _seal_elems or _seal_parse_links(_seal_sig(items))
    if not elems:
        return False
    idx = min(_seal_link_idx if link_idx is None else link_idx,
              len(elems) - 1)
    pt = _seal_link_point(items, img_np, _seal_link_keywords(idx, elems))
    if pt is None:
        for i in range(len(elems)):  # 目标未读出：退回任一封印链接
            pt = _seal_link_point(items, img_np, _seal_link_keywords(i, elems))
            if pt is not None:
                break
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    log(f"[名望] 五庄封印：已点击"
        f"「{elems[idx] + SEAL_LINK_NAME_SUFFIX}」追踪链接 "
        f"@({pt[0]},{pt[1]})", "SYS")
    return True


def _find_task_item(win, icon, label, threshold=SEAL_ITEM_THRESHOLD,
                    timeout=SEAL_ITEM_FIND_TIMEOUT):
    """在已打开的任务储物格中查找指定物品，返回 (pt 或 None, 描述)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.4)
            continue
        pt = game_map.match_frame_np(_bgr_frame(img), icon,
                                     threshold=threshold)
        if pt is not None:
            return pt, f"已在任务储物格中找到{label}"
        time.sleep(0.4)
    return None, f"任务储物格中未找到{label}"


def _close_bag_quiet(win):
    """尽力关闭背包（失败仅日志，不阻断流程）。"""
    try:
        ok, info = bag.close_bag(win)
        if ok:
            log(f"[名望] {info}", "INFO")
        else:
            log(f"[名望] 背包关闭失败: {info}", "WARN")
    except Exception as e:  # 关闭异常不影响用符结果判定
        log(f"[名望] 背包关闭异常: {e}", "WARN")


def _use_task_item(win, icon, label, threshold=SEAL_ITEM_THRESHOLD,
                   find_timeout=SEAL_ITEM_FIND_TIMEOUT):
    """打开任务储物格并右键使用指定物品，返回 (是否成功, 描述)。

    用户 2026-11-10 流程：打开背包 → 任务储物格（与修罗回城符同格，
    通用能力在 bag.open_task_box，不引用其他任务模块）→ 识别物品
    图标 → 右键使用。整段持背包锁（与其他背包流程互斥，防点击交错）。
    使用前若标准对话框在场则跳过（对话框遮住背包/右键落到对话上）。
    唤风符（五庄封印）与灵力镐（使用灵力镐）共用本实现。
    """
    img = win.capture(force_activate=False)
    if img is not None and dialog_frame_score(_bgr_frame(img)) \
            >= DIALOG_FRAME_THRESHOLD:
        return False, f"对话框在场，暂不使用{label}"
    with app_utils._BAG_LOCK:
        ok, info = bag.open_task_box(win, prefix="名望")
        if not ok:
            return False, info
        pt, finfo = _find_task_item(win, icon, label, threshold, find_timeout)
        if pt is None:
            _close_bag_quiet(win)
            return False, finfo
        if not bag.is_bag_open(win):
            return False, f"右键{label}前背包已被关闭，未使用"
        human_click(win, pt[0], pt[1], button="secondary")
        log(f"[名望] 已右键使用{label} @({pt[0]},{pt[1]})", "SYS")
        time.sleep(1.0)  # 等待使用生效（传送/战斗/任务完成）
        _close_bag_quiet(win)
    return True, f"已使用{label}"


def _use_huanfengfu(win):
    """五庄封印：任务储物格右键使用唤风符（见 _use_task_item）。"""
    return _use_task_item(win, SEAL_ITEM_ICON, SEAL_ITEM_LABEL)


def _use_lingligao(win):
    """灵力镐：任务储物格右键使用灵力镐（见 _use_task_item）。"""
    return _use_task_item(win, PICK_ITEM_ICON, PICK_ITEM_LABEL,
                          PICK_ITEM_THRESHOLD, PICK_ITEM_FIND_TIMEOUT)


def _use_chuanyinshi(win):
    """传音石：任务储物格右键使用传音石（见 _use_task_item）。"""
    return _use_task_item(win, CHUANYIN_ITEM_ICON, CHUANYIN_ITEM_LABEL,
                          CHUANYIN_ITEM_THRESHOLD,
                          CHUANYIN_ITEM_FIND_TIMEOUT)


def _seal_maybe_advance_dialog(win, items, img_np):
    """到达/切换链接时对话框无绿字选项 → 限次点屏幕中间推进。

    背景（2026-11-10 实机）：到达封印点后屏幕中间出现无选项对话，
    story 兜底因「已知对话表」抑制不点、用符等待被阻塞 65s，须人工
    点屏才继续。有绿字选项时不动（交给选项表）；连续 4 轮询（~2s）
    无绿字才点一次，多屏对话逐页推进（点击后计数清零）。
    返回 (是否无绿字选项剧情对话, 是否本帧点屏推进)。
    """
    global _seal_dlg_streak, _seal_dlg_click_at
    dl, dt = task_tracker.DIALOG_REGION[0], task_tracker.DIALOG_REGION[1]
    region_items = [it for it in items
                    if it[2] >= 0.55 and it[1][0] >= dl and it[1][1] >= dt]
    if not region_items:
        _seal_dlg_streak = 0
        return False, False
    if green_option_row_present(img_np, region_items):
        _seal_dlg_streak = 0  # 有绿字选项：交给选项表处理，不点屏
        return False, False
    _seal_dlg_streak += 1
    now = time.time()
    if _seal_dlg_streak < SEAL_DIALOG_ADVANCE_POLLS \
            or now - _seal_dlg_click_at < SEAL_DIALOG_ADVANCE_INTERVAL:
        return True, False
    _seal_dlg_streak = 0
    _seal_dlg_click_at = now
    log("[名望] 五庄封印：对话无绿字选项，点击屏幕中间推进", "SYS")
    _click_screen_center(win, times=1, prefix="名望")
    return True, True


def _seal_dialog_blocking(win, items, img_np):
    """对话框在场判定（共享帧模板门）；无选项剧情对话由五庄封印独占推进。

    返回 (是否在场, 是否无绿字选项剧情对话)。无选项对话时调用方返回
    True 让引擎跳过剧情兜底——五庄封印的点屏推进与引擎 StoryDialog
    Dismisser 同帧命中同一对话会连点两下，第二下在对话关闭/渐隐帧落到
    世界把人带走，离开封印点后用符失败（2026-11-17 实机：最后一个
    对话被点两次）。有绿字选项的对话返回 (True, False)，交给选项表。
    """
    _rgb, bgr = _seal_frame(win, items)
    if bgr is None or dialog_frame_score(bgr) < DIALOG_FRAME_THRESHOLD:
        return False, False
    no_option, _clicked = _seal_maybe_advance_dialog(win, items, img_np)
    return True, no_option


def _bag_box_on_frame(win, items):
    """共享帧上检测背包是否打开（bag.bag_open_on_frame 双锚点）。

    对话框/任务追踪遮挡 bag_box 时 close_bag 会误报「背包未打开」而
    实际仍开着（用户 2026-11-10 / 2026-11-17 实测）；按帧检测再关是
    唯一可靠方式。bag_box 被追踪面板盖住时由背包右缘储物格按钮兜底
    （bag.bag_open_on_frame，通用能力在 bag.py）。
    """
    _rgb, bgr = _seal_frame(win, items)
    if bgr is None:
        return False
    return bag.bag_open_on_frame(bgr)


def _seal_close_bag_if_open(win, items):
    """用符后残留背包清理：共享帧命中 bag_box 即关闭（限频重试）。

    对话框消失后按帧检测到再关（跟随轮询，限频 2s）。
    """
    global _seal_bag_close_at
    now = time.time()
    if now - _seal_bag_close_at < SEAL_BAG_CLOSE_INTERVAL:
        return
    if not _bag_box_on_frame(win, items):
        return
    _seal_bag_close_at = now
    log("[名望] 五庄封印：检测到残留背包，尝试关闭", "INFO")
    _close_bag_quiet(win)


def _seal_advance_if_done(win, items, img_np):
    """当前链接已变红（上一点完成/启动续跑）→ 自动前进并点下一个链接。

    下一个目标按颜色选（第一个未变红的），不依赖固定顺序；全部变红
    时只等任务整体消失。对话框在场时先让对话处理（点链接会被吞掉）。
    返回是否已处理（点击了下一个链接）。
    """
    global _seal_phase, _seal_link_idx, _seal_stable, _seal_last_pos, \
        _seal_need_move, _seal_walk_from, _seal_move_stuck, _seal_use_retries
    if not _seal_elems:
        return False
    if _seal_link_color(items, img_np,
                        _seal_link_keywords(_seal_link_idx)) != "red":
        return False
    next_idx = _seal_pick_next(items, img_np, exclude=_seal_link_idx)
    if next_idx is None:
        return False  # 两个点都已完成（或颜色未读出）：等任务消失
    pos = _seal_pos(win, items)
    if pos is None:
        return False
    blocking, no_option = _seal_dialog_blocking(win, items, img_np)
    if blocking:
        return no_option
    if not _click_seal_link(win, items, img_np, link_idx=next_idx):
        return False
    prev = _seal_link_name(_seal_link_idx)
    _seal_link_idx = next_idx
    _seal_phase = 0
    _seal_stable = 0
    _seal_last_pos = None
    _seal_need_move = True
    _seal_walk_from = pos
    _seal_move_stuck = 0
    _seal_use_retries = 0
    log(f"[名望] 五庄封印：{prev}已变红（此点已完成），改点"
        f"「{_seal_link_name(next_idx)}」", "SYS")
    return True


def _seal_wait_arrival(win, items, img_np, now):
    """phase 0：等寻路到达（位置连续稳定）→ 用唤风符。

    到达判定同五倍显形镜：连续 SEAL_ARRIVE_POLLS 次坐标差 ≤3 即到达；
    点链接后须位置先变化（寻路启动）才允许用符，位置连续不变达上限
    则重新点当前链接。到达（且在五庄观）后打开任务储物格用符。
    启动续跑/上一点已完成时当前链接已是红色 → 先自动前进到下一个。
    """
    global _seal_stable, _seal_last_pos, _seal_need_move, _seal_walk_from, \
        _seal_move_stuck, _seal_phase, _seal_use_at
    if _seal_advance_if_done(win, items, img_np):
        return True
    pos = _seal_pos(win, items)
    if pos is None:
        return False
    if _seal_last_pos is not None \
            and abs(pos[1] - _seal_last_pos[1]) <= SEAL_POS_TOLERANCE \
            and abs(pos[2] - _seal_last_pos[2]) <= SEAL_POS_TOLERANCE:
        _seal_stable += 1
    else:
        _seal_stable = 1
    _seal_last_pos = pos
    if _seal_need_move:
        if _seal_walk_from is not None and (
                abs(pos[1] - _seal_walk_from[1]) > SEAL_MOVE_DELTA
                or abs(pos[2] - _seal_walk_from[2]) > SEAL_MOVE_DELTA):
            _seal_need_move = False
            _seal_stable = 0
            _seal_move_stuck = 0
        else:
            _seal_move_stuck += 1
            if _seal_move_stuck >= SEAL_MOVE_STUCK_MAX:
                _seal_move_stuck = 0
                if _click_seal_link(win, items, img_np):
                    log("[名望] 五庄封印：寻路未启动，已重新点击当前链接",
                        "WARN")
                    _seal_walk_from = pos
            return False
    if _seal_stable < SEAL_ARRIVE_POLLS:
        return False
    if pos[0].strip() != SEAL_MAP:
        return False  # 跨图寻路中，尚未回到五庄观
    # 对话框在场时不操作背包；无绿字选项的对话由本状态机点屏推进
    blocking, no_option = _seal_dialog_blocking(win, items, img_np)
    if blocking:
        return no_option
    ok, info = _use_huanfengfu(win)
    if not ok:
        _seal_stable = 0
        log(f"[名望] 五庄封印：{info}，稍后重试", "WARN")
        return False
    _seal_phase = 1
    _seal_use_at = time.time()
    log(f"[名望] 五庄封印：已在{_seal_link_name(_seal_link_idx)}附近使用"
        f"唤风符，等待任务完成", "SYS")
    return True


def _seal_check_outcome(win, items, img_np, now):
    """phase 1：用符后等当前封印点完成（链接变红）→ 点下一个链接。

    用户 2026-11-10 规则：用符后当前链接由亮绿变红（可能立刻、也可能
    战斗结束后变红）代表该点完成，必须改点另一个未完成的；
    坐标离开五庄观同样视为本点完成（用符传送/失败），一并切换。
    只在任务整体从追踪面板消失后由 follow 判定完成——本函数在最后
    一个链接变红后仅等待，不做完成判定、不做战斗判定。战斗期间追踪
    面板不可见（seal_present=False），本函数不会运行。
    """
    global _seal_phase, _seal_link_idx, _seal_stable, _seal_last_pos, \
        _seal_need_move, _seal_walk_from, _seal_move_stuck, \
        _seal_use_retries, _seal_both_warned, _seal_use_at
    if now - _seal_use_at < SEAL_USE_GRACE:
        return False
    # 用符后对话可能常驻（战后任务台词等）：无绿字选项的对话必须由本
    # 状态机推进，否则会卡死——重试用符被「对话框在场」拒绝、链接
    # 永不变红、follow 空转到超时（2026-11-17 实机：战后对话 +
    # story 兜底误判寻路中，空转 3 分钟直到超时重开）。有绿字选项的
    # 对话不受影响（返回 False 交选项表）。
    blocking, no_option = _seal_dialog_blocking(win, items, img_np)
    if blocking:
        return no_option
    link = _seal_link_name(_seal_link_idx)
    state = _seal_link_color(items, img_np,
                             _seal_link_keywords(_seal_link_idx))
    pos = _seal_pos(win, items)
    if pos is None:
        return False  # 位置不可读（加载/过渡）：等下一轮询
    reason = ""
    if state == "red":
        reason = "链接已变红"
    elif pos[0].strip() != SEAL_MAP:
        reason = f"坐标离开{SEAL_MAP}（{pos[0]}）"
    if not reason:
        # 本点仍未完成（链接仍绿/未识别且仍在五庄观）：限次重试用符
        if now - _seal_use_at >= SEAL_USE_RETRY_AFTER \
                and _seal_use_retries < SEAL_USE_RETRY_MAX:
            _seal_use_retries += 1
            ok, info = _use_huanfengfu(win)
            log(f"[名望] 五庄封印：{link}未变红，重试用符"
                f"（{info}，{_seal_use_retries}/{SEAL_USE_RETRY_MAX}）",
                "WARN")
            if ok:
                _seal_use_at = now
            return ok
        return False
    next_idx = _seal_pick_next(items, img_np, exclude=_seal_link_idx)
    if next_idx is None:
        if not _seal_both_warned:
            _seal_both_warned = True
            log(f"[名望] 五庄封印：{link}{reason}，两个封印点均已完成，"
                f"等待任务从追踪面板消失", "SYS")
        return False
    # 本点已完成：对话框在场时先让对话处理（点链接会被对话框吞掉；
    # 无绿字选项的对话由状态机点屏推进）
    blocking, no_option = _seal_dialog_blocking(win, items, img_np)
    if blocking:
        return no_option
    if not _click_seal_link(win, items, img_np, link_idx=next_idx):
        return False
    _seal_link_idx = next_idx
    _seal_phase = 0
    _seal_stable = 0
    _seal_last_pos = None
    _seal_need_move = True
    _seal_walk_from = pos
    _seal_move_stuck = 0
    _seal_use_retries = 0
    log(f"[名望] 五庄封印：{link}{reason}，改点"
        f"「{_seal_link_name(_seal_link_idx)}」", "SYS")
    return True


def _handle_wuzhuang_seal(win, items, img_np):
    """五庄封印执行状态机（follow extras 通道，每轮询一次）。

    返回是否已处理（点击链接/使用唤风符）。非五庄任务/等待阶段返回
    False，不影响既有对话处理。状态机同五倍显形镜：0=等到达（坐标
    稳定）→ 用符；1=已用符等结果。不做战斗判定：战斗由 follow_task
    跳过、battle 线程托管；战斗结束任务消失即完成（用户 2026-11-10）。
    """
    global _seal_missing, _seal_phase, _seal_elems, _seal_link_idx
    if _seal_phase == 1:
        # 用符后对话框可能遮挡背包导致关闭失败：残留背包限频补关
        # （放在 tracker 文案判定之前——背包遮挡追踪面板时也要能关）
        _seal_close_bag_if_open(win, items)
    sig = _seal_sig(items)
    seal_present = SEAL_TASK_KEYWORD in sig
    if seal_present:
        _seal_missing = 0
        # 封印点动态解析（2026-11-17：五行任选两个，实机已见 金+土、
        # 水+火；旧版写死火/木导致颜色判定落空）。解析结果变化时按
        # 当前元素在新列表中的位置跟随，不打断进行中的点。
        elems = _seal_parse_links(sig)
        # 只接受「不少于已知数量」的解析结果：OCR 偶发漏一个元素时不
        # 覆盖（否则当前目标会被重置、可能重复点已完成链接）
        if elems and (not _seal_elems or len(elems) >= len(_seal_elems)) \
                and elems != _seal_elems:
            old = _seal_elems[_seal_link_idx] \
                if 0 <= _seal_link_idx < len(_seal_elems) else None
            _seal_elems = elems
            _seal_link_idx = elems.index(old) if old in elems else 0
            log(f"[名望] 五庄封印：封印点 "
                f"{'、'.join(_seal_link_name(i) for i in range(len(elems)))}",
                "SYS")
    elif _seal_phase == 0:
        # 非五庄任务（或任务已完成）：连续多轮无五庄任务才清残留状态
        _seal_missing += 1
        if _seal_missing >= 5:
            _reset_seal_state()
        return False
    if not seal_present:
        # 战斗中/加载过渡期追踪面板不可见：本帧不推进（等任务文本回来）
        return False
    now = time.time()
    if _seal_phase == 1:
        return _seal_check_outcome(win, items, img_np, now)
    return _seal_wait_arrival(win, items, img_np, now)


# ---- 灵力镐执行（追踪「前往大唐边境，使用灵力镐」→ 点链接 → 到达 →
#      用镐 → 「开采石头」小游戏窗口 → 破除封印同款点击模式闭环）----
def _reset_pick_state():
    """灵力镐状态机复位（新任务领取成功/跨任务防残留）。"""
    global _pick_phase, _pick_stable, _pick_last_pos, _pick_need_move, \
        _pick_walk_from, _pick_move_stuck, _pick_stuck_cycles, _pick_use_at, \
        _pick_use_retries, _pick_missing, _pick_bag_close_at, _pick_link_at, \
        _pick_mining_armed_at, _pick_mining_last_check
    _pick_phase = 0
    _pick_stable = 0
    _pick_last_pos = None
    _pick_need_move = True
    _pick_walk_from = None
    _pick_move_stuck = 0
    _pick_stuck_cycles = 0
    _pick_use_at = 0.0
    _pick_use_retries = 0
    _pick_missing = 0
    _pick_bag_close_at = 0.0
    _pick_link_at = 0.0
    _pick_mining_armed_at = 0.0
    _pick_mining_last_check = 0.0


def _pick_trigger(sig):
    """追踪面板文案是否触发灵力镐流程（触发词含 OCR 形近字）。"""
    return any(kw in sig for kw in PICK_TASK_KEYWORDS)


def _pick_link_point(items, img_np):
    """定位「蕴灵石头」绿色链接，返回点击点或 None。

    实机文案见 PICK_LINK_KEYWORDS 注释：只认蕴灵石头（可导航），
    「灵力镐」是物品说明链接，**不得点击**。按 PICK_LINK_KEYWORDS
    顺序用 _seal_link_point 的比例定位 + 下划线吸附；限定追踪面板列
    （panel_row_xmax，动态内容区左缘），排除居中对话框同名文案。
    未读出返回 None，由调用方等待重试（禁止行级兜底）。
    """
    xmax = task_tracker.panel_row_xmax(img_np)
    panel = [it for it in items
             if it[2] >= 0.55 and it[1][0] < xmax]
    for kw in PICK_LINK_KEYWORDS:
        pt = _seal_link_point(panel, img_np, kw)
        if pt is not None:
            return pt
    return None


def _pick_green_picker(items, img_np):
    """track_task green_picker：灵力镐任务只点「蕴灵石头」链接。

    触发词在但链接未读出 → 返回 task_tracker.PICK_FAIL 等待重试：
    引擎行级兜底会选中首条绿字下划线（实机即「灵力镐」，点开物品
    说明——2026-11-10 用户反馈点错），必须拒绝兜底。非灵力镐任务
    返回 None，回退引擎行级选择。
    """
    if not _pick_trigger(_seal_sig(items)):
        return None
    pt = _pick_link_point(items, img_np)
    if pt is None:
        return task_tracker.PICK_FAIL
    return pt


def _mingwang_green_picker(items, img_np):
    """名望追踪链接精确点击：五庄封印 → 蕴灵石头 → 老君丹房门口 → 星魂归位。

    星魂归位（minigames/xinghun.green_picker）：四象禁地阶段引擎若补点
    追踪绿字，只允许点「四象禁地」回中心，绝不点星宿石链接。
    """
    for picker in (_seal_green_picker, _pick_green_picker,
                   _tgqy_green_picker, _xinghun_minigame.green_picker):
        pt = picker(items, img_np)
        if pt is not None:
            return pt
    return None


def _click_pick_link(win, items, img_np):
    """点击「蕴灵石头」导航链接；未识别到返回 False（下轮询重试）。"""
    global _pick_link_at
    pt = _pick_link_point(items, img_np)
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    _pick_link_at = time.time()
    log(f"[名望] 灵力镐：已点击「蕴灵石头」追踪链接 "
        f"@({pt[0]},{pt[1]})", "SYS")
    return True


def _pick_wait_arrival(win, items, img_np, now):
    """phase 0：等位移启动 → 位置稳定到达 → 任务储物格用灵力镐。

    到达判定同五庄封印/五倍显形镜（连续 PICK_ARRIVE_POLLS 次坐标差
    ≤PICK_POS_TOLERANCE 即到达），且须位于目标地图。**初始/每次点
    链接后须先见位移**（寻路已启动）才允许用镐——魔族领取点与执行
    地图同为大唐边境，站在领取点原地用镐会打空；连续
    PICK_STUCK_CYCLES_USE 个重点周期仍无位移视为已在目的地，放行。
    """
    global _pick_stable, _pick_last_pos, _pick_need_move, _pick_walk_from, \
        _pick_move_stuck, _pick_stuck_cycles, _pick_phase, _pick_use_at, \
        _pick_link_at
    pos = _seal_pos(win, items)
    if pos is None:
        return False
    if _pick_last_pos is not None \
            and abs(pos[1] - _pick_last_pos[1]) <= PICK_POS_TOLERANCE \
            and abs(pos[2] - _pick_last_pos[2]) <= PICK_POS_TOLERANCE:
        _pick_stable += 1
    else:
        _pick_stable = 1
    _pick_last_pos = pos
    if _pick_need_move:
        if _pick_walk_from is None:
            _pick_walk_from = pos
        if (abs(pos[1] - _pick_walk_from[1]) > PICK_MOVE_DELTA
                or abs(pos[2] - _pick_walk_from[2]) > PICK_MOVE_DELTA):
            _pick_need_move = False
            _pick_stable = 0
            _pick_move_stuck = 0
            _pick_stuck_cycles = 0
        else:
            _pick_move_stuck += 1
            if _pick_move_stuck >= PICK_MOVE_STUCK_MAX:
                _pick_move_stuck = 0
                _pick_stuck_cycles += 1
                if _pick_stuck_cycles >= PICK_STUCK_CYCLES_USE:
                    # 连续多次重点仍不动：已在目的地（链接点击后原地
                    # 不动），放行到达判定
                    _pick_need_move = False
                    _pick_stable = 0
                    log("[名望] 灵力镐：多次点击链接仍无位移，"
                        "视为已在目的地", "WARN")
                else:
                    if _click_pick_link(win, items, img_np):
                        log("[名望] 灵力镐：寻路未启动，已重新点击链接",
                            "WARN")
                    _pick_walk_from = pos
            return False
    if _pick_stable < PICK_ARRIVE_POLLS:
        return False
    if pos[0].strip() != PICK_MAP:
        # 寻路中/尚未到达目标地图：原地静止超时则重点链接重新寻路
        # （跨图/中途被打断时寻路已停，但链接仍在追踪面板上）
        if now - _pick_link_at >= PICK_LINK_RETRY_AFTER \
                and _click_pick_link(win, items, img_np):
            _pick_walk_from = pos
            _pick_stable = 0
            log(f"[名望] 灵力镐：当前在{pos[0]}（非{PICK_MAP}），"
                f"已重新点击链接寻路", "WARN")
        return False
    blocking, no_option = _seal_dialog_blocking(win, items, img_np)
    if blocking:
        return no_option  # 对话框在场时不操作背包
    ok, info = _use_lingligao(win)
    if not ok:
        _pick_stable = 0
        log(f"[名望] 灵力镐：{info}，稍后重试", "WARN")
        return False
    _pick_phase = 1
    _pick_use_at = time.time()
    _arm_pick_mining_check()
    log(f"[名望] 灵力镐：已在{PICK_MAP}使用灵力镐，等待任务推进", "SYS")
    return True


def _arm_pick_mining_check():
    """用镐成功 → 开启「开采石头」小游戏窗口检测（armed 门控）。"""
    global _pick_mining_armed_at
    _pick_mining_armed_at = time.time()
    log(f"[名望] 已使用灵力镐，开启开采石头窗口检测"
        f"（{PICK_MINING_ARM_TIMEOUT}s 内有效）", "SYS")


def _pick_mining_window_check(win):
    """检测并执行「开采石头」小游戏（armed 门控 + 限频），返回是否已处理。

    用户 2026-11-10 规则：使用灵力镐后检查 kaicaishitou 模板出现，
    操作与破除封印/钓鱼共用闭环（**长按运输 + 点按维持**——长按把
    填充推过目标带上缘，松手回落至带中心 C 后点按保持在 [L,R] 内，
    见 pochufengyin.hold_catch）——窗口出现即阻塞执行
    pochufengyin.play_pochufengyin（hold=True + hold_catch=True +
    「长按」按钮模板），标题消失即结束。检测限频
    PICK_MINING_CHECK_INTERVAL；未 armed/限频未到/未命中返回 False
    （热路径零成本）。**须排在灵力镐状态机之前调用**：窗口在场时
    相位机不得重试用镐（打开背包干扰小游戏）；armed 窗口外的调用零成本。
    """
    global _pick_mining_armed_at, _pick_mining_last_check
    if _pick_mining_armed_at <= 0 \
            or time.time() - _pick_mining_armed_at > PICK_MINING_ARM_TIMEOUT:
        return False
    if is_in_battle(win):
        return False
    now = time.time()
    if now - _pick_mining_last_check < PICK_MINING_CHECK_INTERVAL:
        return False
    _pick_mining_last_check = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    score, wpt = game_map.best_match_np(
        _bgr_frame(img), PICK_MINING_TITLE_ICON,
        region=_pochu_minigame.POCHU_REGION)
    if wpt is None or score < _pochu_minigame.POCHU_THRESHOLD:
        return False
    log(f"[名望] 检测到开采石头窗口 @({wpt[0]},{wpt[1]})"
        f"（匹配度 {score:.2f}）", "SYS")
    ok, info = _pochu_minigame.play_pochufengyin(
        win, PICK_MINING_TITLE_ICON, prefix="名望", label="开采石头",
        hold=True, action_icon=PICK_MINING_HOLD_ICON, hold_catch=True)
    if ok:
        _pick_mining_armed_at = 0.0
    log(f"[名望] {info}", "SYS" if ok else "WARN")
    return True


def _pick_check_outcome(win, items, img_np, now):
    """phase 1：用镐后等任务推进（触发词变化/任务消失/进入战斗）。

    触发词仍在时按 PICK_USE_RETRY_AFTER 间隔限次重试用镐（任务推进可能
    需多次使用）；触发词消失由 _handle_lingligao 连续缺失复位。不做
    完成判定：任务整体消失由 follow 引擎判定，战斗由 battle 线程托管。
    """
    global _pick_use_at, _pick_use_retries
    if now - _pick_use_at < PICK_USE_GRACE:
        return False
    if now - _pick_use_at >= PICK_USE_RETRY_AFTER \
            and _pick_use_retries < PICK_USE_RETRY_MAX:
        _pick_use_retries += 1
        ok, info = _use_lingligao(win)
        log(f"[名望] 灵力镐：任务未推进，重试用镐"
            f"（{info}，{_pick_use_retries}/{PICK_USE_RETRY_MAX}）", "WARN")
        if ok:
            _pick_use_at = now
            _arm_pick_mining_check()
        return ok
    return False


def _handle_lingligao(win, items, img_np):
    """灵力镐执行状态机（follow extras 通道，每轮询一次）。

    触发：追踪面板文案含「灵力镐」（前往大唐边境，使用灵力镐）。
    返回是否已处理（点击链接/使用灵力镐）。非本任务/等待阶段返回
    False，不影响既有对话处理。不做战斗判定：战斗由 follow_task
    跳过、battle 线程托管。
    """
    global _pick_missing, _pick_phase, _pick_bag_close_at
    if _pick_phase == 1:
        # 用镐后残留背包限频补关（对话框可能遮挡 bag_box 导致关闭失败）
        now = time.time()
        if now - _pick_bag_close_at >= PICK_BAG_CLOSE_INTERVAL \
                and _bag_box_on_frame(win, items):
            _pick_bag_close_at = now
            log("[名望] 灵力镐：检测到残留背包，尝试关闭", "INFO")
            _close_bag_quiet(win)
    if not _pick_trigger(_seal_sig(items)):
        _pick_missing += 1
        if _pick_missing >= PICK_ABSENT_RESET:
            _reset_pick_state()
        return False
    _pick_missing = 0
    now = time.time()
    if _pick_phase == 1:
        return _pick_check_outcome(win, items, img_np, now)
    return _pick_wait_arrival(win, items, img_np, now)


# ---- 传音石执行（魔王重聚）：引导小游戏在 minigames/mowangchongju ----
def _handle_chuanyinshi(win, items, img_np):
    """传音石引导（魔王重聚）：委托 minigames/mowangchongju.handle。

    任务侧只注入共享帧（_seal_frame，复用同轮询截帧）与道具使用实现
    （_use_chuanyinshi，任务储物格右键使用 + 对话框门/背包锁）。战后
    交付阶段（「请将传音石交给魔王三大王」）不命中引导触发词，由
    follow 引擎绿字点击 + 本模块 DIALOG_OPTION_MAP 处理。
    """
    return _mowang_minigame.handle(win, items, img_np,
                                   frame_fn=_seal_frame,
                                   use_item=_use_chuanyinshi,
                                   prefix="[名望] ")


# ---- 天宫奇遇执行（追踪「前往老君丹房门口」→ 到达天宫(13,99) →
#      任务储物格用偷丹葫芦 → 「魔取仙丹」小游戏）----
def _reset_tgqy_state():
    """天宫奇遇状态机复位（新任务领取成功/跨任务防残留）。"""
    global _tgqy_phase, _tgqy_stable, _tgqy_last_pos, _tgqy_need_move, \
        _tgqy_walk_from, _tgqy_move_stuck, _tgqy_use_at, _tgqy_use_retries, \
        _tgqy_missing, _tgqy_bag_close_at, _tgqy_link_at, \
        _tgqy_game_armed_at, _tgqy_game_last_check, _tgqy_game_retry_at
    _tgqy_phase = 0
    _tgqy_stable = 0
    _tgqy_last_pos = None
    _tgqy_need_move = False
    _tgqy_walk_from = None
    _tgqy_move_stuck = 0
    _tgqy_use_at = 0.0
    _tgqy_use_retries = 0
    _tgqy_missing = 0
    _tgqy_bag_close_at = 0.0
    _tgqy_link_at = 0.0
    _tgqy_game_armed_at = 0.0
    _tgqy_game_last_check = 0.0
    _tgqy_game_retry_at = 0.0


def _tgqy_trigger(sig):
    """追踪面板文案是否触发天宫奇遇流程（前往老君丹房门口）。"""
    return any(kw in sig for kw in TGQY_TASK_KEYWORDS)


def _tgqy_at_target(pos):
    """是否到达天宫(13,99)（用户指定 1px 容差）。"""
    return (pos is not None and pos[0].strip() == TGQY_MAP
            and abs(pos[1] - TGQY_TARGET[0]) <= TGQY_POS_TOLERANCE
            and abs(pos[2] - TGQY_TARGET[1]) <= TGQY_POS_TOLERANCE)


def _tgqy_link_point(items, img_np):
    """定位「老君丹房门口」导航绿字，返回点击点或 None。

    实机文案：「前往老君丹房门口，使用偷丹葫芦。」——两处都是亮绿
    链接，可导航的是**老君丹房门口**；「偷丹葫芦」点开物品说明
    （同灵力镐的蕴灵石头/灵力镐）。只认导航词，限定追踪面板列
    （panel_row_xmax，排除居中对话框同名文案），未读出返回 None。
    """
    xmax = task_tracker.panel_row_xmax(img_np)
    panel = [it for it in items
             if it[2] >= 0.55 and it[1][0] < xmax]
    for kw in TGQY_LINK_KEYWORDS:
        pt = _seal_link_point(panel, img_np, kw)
        if pt is not None:
            return pt
    return None


def _tgqy_green_picker(items, img_np):
    """track_task green_picker：天宫奇遇只点「老君丹房门口」导航链接。

    触发词在但链接未读出 → PICK_FAIL 等待重试：引擎行级兜底可能点到
    同一行的物品链接「偷丹葫芦」（点开物品说明，2026-11-14 用户明确
    流程），必须拒绝兜底。非天宫奇遇任务返回 None，回退引擎行级选择。
    """
    if not _tgqy_trigger(_seal_sig(items)):
        return None
    pt = _tgqy_link_point(items, img_np)
    if pt is None:
        return task_tracker.PICK_FAIL
    return pt


def _click_tgqy_link(win, items, img_np):
    """点击「老君丹房门口」导航链接；未识别到返回 False（下轮询重试）。"""
    global _tgqy_link_at
    pt = _tgqy_link_point(items, img_np)
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    _tgqy_link_at = time.time()
    log(f"[名望] 天宫奇遇：已点击「老君丹房门口」追踪链接 "
        f"@({pt[0]},{pt[1]})", "SYS")
    return True


def _use_toudanhulu(win):
    """天宫奇遇：任务储物格右键使用偷丹葫芦（见 _use_task_item）。"""
    return _use_task_item(win, TGQY_ITEM_ICON, TGQY_ITEM_LABEL,
                          TGQY_ITEM_THRESHOLD, TGQY_ITEM_FIND_TIMEOUT)


def _arm_tgqy_game_check():
    """用道具成功 → 开启「魔取仙丹」窗口检测（armed 门控）。"""
    global _tgqy_game_armed_at, _tgqy_game_last_check, _tgqy_game_retry_at
    _tgqy_game_armed_at = time.time()
    _tgqy_game_last_check = 0.0
    _tgqy_game_retry_at = 0.0
    log(f"[名望] 已使用偷丹葫芦，开启魔取仙丹窗口检测"
        f"（{_moqu_minigame.MOQUSHENDAN_GAME_ARM_TIMEOUT}s 内有效）", "SYS")


def _maybe_play_tgqy(win):
    """armed 窗口内检测魔取仙丹窗口并阻塞游玩，返回是否已处理。

    使用偷丹葫芦成功后在 armed 窗口内按限频匹配窗口标题
    mingwang_moqushendan.png；窗口出现即阻塞执行
    moqushendan.play_moqushendan（观看演示→记录顺序→按序点击→提交），
    窗口关闭即成功。窗口在场但游玩失败冷却中同样返回 True——阻断
    状态机重试用道具（打开背包会干扰模态小游戏）。未 armed/限频未到/
    未命中返回 False（热路径零成本）。
    """
    global _tgqy_game_armed_at, _tgqy_game_last_check, \
        _tgqy_game_retry_at, _tgqy_phase
    if _tgqy_game_armed_at <= 0:
        return False
    if time.time() - _tgqy_game_armed_at \
            > _moqu_minigame.MOQUSHENDAN_GAME_ARM_TIMEOUT:
        _tgqy_game_armed_at = 0.0
        log("[名望] 天宫奇遇：魔取仙丹窗口未出现，检测窗口超时", "WARN")
        return False
    if is_in_battle(win):
        return False
    now = time.time()
    if now - _tgqy_game_last_check \
            < _moqu_minigame.MOQUSHENDAN_CHECK_INTERVAL:
        return False
    _tgqy_game_last_check = now
    img = win.capture(force_activate=False)
    if img is None:
        return False
    if not _moqu_minigame.moqushendan_present(_bgr_frame(img)):
        return False
    if now < _tgqy_game_retry_at:
        return True  # 窗口在场但游玩冷却中：本帧按已处理跳过状态机
    ok, info = _moqu_minigame.play_moqushendan(win, prefix="名望")
    log(f"[名望] {info}", "SYS" if ok else "WARN")
    if ok:
        _tgqy_game_armed_at = 0.0
        _tgqy_phase = 2  # 已解封：不再重试用道具，等战斗/任务完成
    else:
        _tgqy_game_retry_at = time.time() + TGQY_GAME_RETRY_AFTER
    return True


def _tgqy_wait_arrival(win, items, img_np, now):
    """phase 0：等到达天宫(13,99)（1px）→ 任务储物格用偷丹葫芦。

    到达判定：坐标连续 TGQY_ARRIVE_POLLS 次落在 (13,99)±1 内。点追踪
    链接后须先见位移（寻路已启动）；位置连续不变达上限则按
    TGQY_LINK_RETRY_AFTER 间隔重点链接（寻路偶发未启动）。已在目标点
    （续跑/手动站好）则跳过点击直接走到达判定。
    """
    global _tgqy_stable, _tgqy_last_pos, _tgqy_need_move, _tgqy_walk_from, \
        _tgqy_move_stuck, _tgqy_phase, _tgqy_use_at, _tgqy_link_at
    pos = _seal_pos(win, items)
    if pos is None:
        return False
    if _tgqy_at_target(pos):
        if _tgqy_last_pos is not None and pos == _tgqy_last_pos:
            _tgqy_stable += 1
        else:
            _tgqy_stable = 1
        _tgqy_last_pos = pos
        _tgqy_need_move = False
        if _tgqy_stable < TGQY_ARRIVE_POLLS:
            return False
        blocking, no_option = _seal_dialog_blocking(win, items, img_np)
        if blocking:
            return no_option  # 对话框在场时不操作背包
        ok, info = _use_toudanhulu(win)
        if not ok:
            _tgqy_stable = 0
            log(f"[名望] 天宫奇遇：{info}，稍后重试", "WARN")
            return False
        _tgqy_phase = 1
        _tgqy_use_at = time.time()
        _arm_tgqy_game_check()
        log(f"[名望] 天宫奇遇：已在{TGQY_MAP}使用偷丹葫芦，等待魔取仙丹窗口",
            "SYS")
        return True
    # 不在目标点：点链接寻路；点过后须先见位移，长时间不动则重点
    _tgqy_stable = 0
    _tgqy_last_pos = pos
    if not _tgqy_need_move:
        if _click_tgqy_link(win, items, img_np):
            _tgqy_need_move = True
            _tgqy_walk_from = pos
            _tgqy_move_stuck = 0
            return True
        return False
    if _tgqy_walk_from is not None and (
            abs(pos[1] - _tgqy_walk_from[1]) > TGQY_MOVE_DELTA
            or abs(pos[2] - _tgqy_walk_from[2]) > TGQY_MOVE_DELTA):
        _tgqy_need_move = False
        _tgqy_move_stuck = 0
        return False
    _tgqy_move_stuck += 1
    if _tgqy_move_stuck >= TGQY_MOVE_STUCK_MAX:
        _tgqy_move_stuck = 0
        if now - _tgqy_link_at >= TGQY_LINK_RETRY_AFTER \
                and _click_tgqy_link(win, items, img_np):
            log("[名望] 天宫奇遇：寻路未启动，已重新点击导航链接", "WARN")
            _tgqy_walk_from = pos
    return False


def _tgqy_check_outcome(win, items, img_np, now):
    """phase 1：用道具后等魔取仙丹窗口/任务推进。

    窗口由 _maybe_play_tgqy 检测（窗口在场时本函数不会执行），本函数
    只在触发词仍在且长时间无窗口时按间隔限次重试用道具（用道具被
    对话框/背包遮挡吞掉的情况）。小游戏通过后 phase=2，不再重试。
    """
    global _tgqy_use_at, _tgqy_use_retries
    if now - _tgqy_use_at < TGQY_USE_GRACE:
        return False
    if now - _tgqy_use_at >= TGQY_USE_RETRY_AFTER \
            and _tgqy_use_retries < TGQY_USE_RETRY_MAX:
        blocking, no_option = _seal_dialog_blocking(win, items, img_np)
        if blocking:
            return no_option
        _tgqy_use_retries += 1
        ok, info = _use_toudanhulu(win)
        log(f"[名望] 天宫奇遇：未出现魔取仙丹窗口，重试使用偷丹葫芦"
            f"（{info}，{_tgqy_use_retries}/{TGQY_USE_RETRY_MAX}）", "WARN")
        if ok:
            _tgqy_use_at = now
            _arm_tgqy_game_check()
        return ok
    return False


def _handle_tiangong_qiyu(win, items, img_np):
    """天宫奇遇执行状态机（follow extras 通道，每轮询一次）。

    触发：追踪面板文案含「前往老君丹房门口」（短词兜底「老君丹房」）。
    流程：点「老君丹房门口」导航链接 → 到达天宫(13,99)±1 → 任务储物格
    右键使用偷丹葫芦 → 魔取仙丹小游戏（_maybe_play_tgqy 先于本函数
    调用，窗口在场时本函数不执行）→ 成功后进入战斗。不做战斗判定：
    战斗由 follow_task/battle 线程托管；任务追踪消失即完成。
    返回是否已处理（点击链接/使用道具）。
    """
    global _tgqy_missing, _tgqy_phase, _tgqy_bag_close_at
    if _tgqy_phase in (1, 2):
        # 用道具/小游戏后残留背包限频补关（对话框/模态窗口可能遮挡）
        now = time.time()
        if now - _tgqy_bag_close_at >= TGQY_BAG_CLOSE_INTERVAL \
                and _bag_box_on_frame(win, items):
            _tgqy_bag_close_at = now
            log("[名望] 天宫奇遇：检测到残留背包，尝试关闭", "INFO")
            _close_bag_quiet(win)
    if not _tgqy_trigger(_seal_sig(items)):
        if _tgqy_phase == 0:
            _tgqy_missing += 1
            if _tgqy_missing >= TGQY_ABSENT_RESET:
                _reset_tgqy_state()
        return False
    _tgqy_missing = 0
    now = time.time()
    if _tgqy_phase == 2:
        return False  # 小游戏已通过：等战斗/任务完成（follow 判定）
    if _tgqy_phase == 1:
        return _tgqy_check_outcome(win, items, img_np, now)
    return _tgqy_wait_arrival(win, items, img_np, now)


# ---- 蛟魔复仇（暗雷战斗）：到达文案地图后降落，等待遭遇战斗 ----
def _reset_jiamo_state():
    """蛟魔复仇暗雷战斗状态机复位（新任务领取/触发词连续缺失）。"""
    global _jiamo_phase, _jiamo_target, _jiamo_missing, _jiamo_pos_at, \
        _jiamo_land_at, _jiamo_warned, _jiamo_walk_from, _jiamo_move_seen, \
        _jiamo_walk_at, _jiamo_stable, _jiamo_last_pos, _jiamo_stable_at, \
        _jiamo_walk_click_at, _jiamo_walk_idx
    _jiamo_phase = 0
    _jiamo_target = None
    _jiamo_missing = 0
    _jiamo_pos_at = 0.0
    _jiamo_land_at = 0.0
    _jiamo_warned = False
    _jiamo_walk_from = None
    _jiamo_move_seen = False
    _jiamo_walk_at = 0.0
    _jiamo_stable = 0
    _jiamo_last_pos = None
    _jiamo_stable_at = 0.0
    _jiamo_walk_click_at = 0.0
    _jiamo_walk_idx = 0


def _reset_jiamo_arrival():
    """到达/寻路判定状态清零（目标地图变化时重新等待寻路）。

    降落重试时钟（_jiamo_land_at）不复位——文案 OCR 抖动来回解析成两个
    地图时不会把重试间隔打穿、每轮询都开面板。
    """
    global _jiamo_phase, _jiamo_walk_from, _jiamo_move_seen, \
        _jiamo_walk_at, _jiamo_stable, _jiamo_last_pos, _jiamo_stable_at, \
        _jiamo_walk_click_at
    _jiamo_phase = 0
    _jiamo_walk_from = None
    _jiamo_move_seen = False
    _jiamo_walk_at = 0.0
    _jiamo_stable = 0
    _jiamo_last_pos = None
    _jiamo_stable_at = 0.0
    _jiamo_walk_click_at = 0.0


def _jiamo_target_map(sig):
    """从追踪文案解析目标地图名（「请在<地图>仔细搜查」）；失败返回 None。"""
    m = JIAMO_MAP_RE.search(sig)
    if m is None:
        return None
    name = JIAMO_MAP_SUFFIX_RE.sub("", m.group(1))
    if len(name) < 2 or name in _JIAMO_ANCHOR_WORDS:
        return None  # 缺地图名的退化文案（捕获到锚词本身）
    return name


def _jiamo_map_match(current, target):
    """当前位置地名与目标地图是否一致（等长 ≥3 字容 1 字 OCR 误读）。

    不做包含匹配：长安城/长安城东 这类前缀地图会把途经/邻近地图误判
    为到达（虽只多降落一次，仍以精确判定为准）。地图位置识别是标定
    资产、可信度高，容错只针对任务文案一侧的单字误读（如 女儿村→女儿材）。
    """
    if not current or not target:
        return False
    if current == target:
        return True
    if len(current) == len(target) >= 3:
        return sum(a != b for a, b in zip(current, target)) <= 1
    return False


def _jiamo_arrived(sig, current, target):
    """到达判定：地名与目标地图匹配；目标未解析时「请在」后窗口兜底。

    兜底只认「请在」后 10 字窗口内出现当前地名（如 OCR 把「仔细搜查」
    读花导致正则失败，但地名仍可读），避免把追踪面板其它任务的文案
    误当到达证据。
    """
    if _jiamo_map_match(current, target):
        return True
    if target is not None:
        return False
    m = _JIAMO_AFTER_RE.search(sig)
    return bool(m) and bool(current) and current in m.group(1)


def _jiamo_step_arrival(win, items, now):
    """phase 0：先等寻路启动，再判到达稳定，到达后降落。

    返回是否已处理（执行了降落）。未见位移 JIAMO_MOVE_WAIT 秒放行
    （目的地就在脚下/链接点击未生效），避免死等；到达判定须在地图 +
    位置连续稳定 JIAMO_ARRIVE_POLLS 轮（容差 3px）。
    """
    global _jiamo_walk_from, _jiamo_move_seen, _jiamo_walk_at, \
        _jiamo_stable, _jiamo_last_pos, _jiamo_land_at, _jiamo_phase, \
        _jiamo_target, _jiamo_stable_at
    if not _jiamo_move_seen:
        pos = _seal_pos(win, items)
        if pos is None:
            return False  # 位置不可读（加载/飞行过渡帧）：下轮再查
        if _jiamo_walk_from is None:
            _jiamo_walk_from = pos
            _jiamo_walk_at = now
        moved = (pos[0] != _jiamo_walk_from[0]
                 or abs(pos[1] - _jiamo_walk_from[1]) > JIAMO_MOVE_DELTA
                 or abs(pos[2] - _jiamo_walk_from[2]) > JIAMO_MOVE_DELTA)
        if moved:
            _jiamo_move_seen = True
            log("[名望] 蛟魔复仇：寻路已启动，等待到达目标地图后降落",
                "INFO")
        elif now - _jiamo_walk_at >= JIAMO_MOVE_WAIT:
            _jiamo_move_seen = True
            log("[名望] 蛟魔复仇：未见位移（可能已在目的地），按到达处理",
                "INFO")
        else:
            return False
    pos = _seal_pos(win, items)
    if pos is None:
        return False  # 位置不可读（加载/飞行过渡帧）：下轮再查
    if not _jiamo_arrived(_seal_sig(items), pos[0], _jiamo_target):
        # 仍在其它地图寻路中：稳定计数清零，继续等
        _jiamo_stable = 0
        _jiamo_last_pos = pos
        return False
    last = _jiamo_last_pos
    if (last is not None and pos[0] == last[0]
            and abs(pos[1] - last[1]) <= JIAMO_POS_TOLERANCE
            and abs(pos[2] - last[2]) <= JIAMO_POS_TOLERANCE):
        _jiamo_stable += 1
    else:
        _jiamo_stable = 0
    _jiamo_last_pos = pos
    if _jiamo_stable < JIAMO_ARRIVE_POLLS:
        return False  # 到达但仍在移动：等停下
    if _jiamo_target is None:
        _jiamo_target = pos[0]  # 「请在」窗口兜底命中：以当前地名落定目标
        log(f"[名望] 蛟魔复仇：位置兜底确认目标地图{_jiamo_target}", "INFO")
    if now - _jiamo_land_at < JIAMO_LAND_RETRY_AFTER:
        return False  # 降落失败后的重试间隔未到
    _jiamo_land_at = now
    log(f"[名望] 蛟魔复仇：已到达{_jiamo_target}并稳定，执行降落", "SYS")
    ok, info = zuoqi.ensure_landed(win)
    _closed, cinfo = zuoqi.close_feixingyuqi_panel(win)  # 降落后面板保持打开
    log(f"[名望] 蛟魔复仇：降落{'成功' if ok else '失败'}：{info}；{cinfo}",
        "SYS" if ok else "WARN")
    if not ok:
        return False  # 战斗中/面板瞬态失败：按间隔重试
    _jiamo_phase = 1
    _jiamo_stable_at = 0.0  # 进入等战斗阶段：稳定计时从零开始
    return True


def _jiamo_step_wait_battle(win, items, now):
    """phase 1：已到达并降落，等暗雷遭遇战斗；稳定久未开战 → 点击地面走位。

    战斗由 follow_task/battle 线程托管，本步骤不做战斗操作；仅当任务
    文案仍在（暗雷战斗）、位置稳定且超过 JIAMO_WALK_WAIT 未开战时点
    世界地面模拟玩家行走催战（静默 Alt+D 无效，2026-11-17 用户实测），
    之后每 JIAMO_WALK_INTERVAL 重按。走动的位移会自然清零稳定计数，
    人物停下稳定 JIAMO_WALK_WAIT 秒后再点下一个走位点。
    返回是否已处理（点了世界地面）。
    """
    global _jiamo_stable, _jiamo_last_pos, _jiamo_stable_at, \
        _jiamo_walk_click_at
    pos = _seal_pos(win, items)
    if pos is None:
        return False
    last = _jiamo_last_pos
    if (last is not None and pos[0] == last[0]
            and abs(pos[1] - last[1]) <= JIAMO_POS_TOLERANCE
            and abs(pos[2] - last[2]) <= JIAMO_POS_TOLERANCE):
        _jiamo_stable += 1
    else:
        _jiamo_stable = 0
        _jiamo_stable_at = 0.0   # 位置变化：稳定/走位等待重新计时
    _jiamo_last_pos = pos
    if _jiamo_stable < JIAMO_ARRIVE_POLLS:
        return False  # 仍在移动（自由寻路/被推挤）：不催遇敌
    if _jiamo_stable_at == 0.0:
        _jiamo_stable_at = now
    if now - _jiamo_stable_at < JIAMO_WALK_WAIT:
        return False
    if now - _jiamo_walk_click_at < JIAMO_WALK_INTERVAL:
        return False
    _jiamo_walk_click_at = now
    desc = _jiamo_walk_click(win)
    log(f"[名望] 蛟魔复仇：到达稳定后仍未触发战斗，{desc}", "SYS")
    return True


def _handle_jiamo_anlei(win, items, img_np):
    """蛟魔复仇暗雷战斗状态机（follow extras 通道，每轮询一次）。

    触发：追踪面板文案含「暗雷战斗」——点击追踪绿字由 follow 引擎完成
    （本处理器不点链接，不干涉寻路）。触发后按 JIAMO_POS_CHECK_INTERVAL
    限频识别当前位置：
    1. **先寻路后降落**（2026-11-15 用户规则）：追踪文案出现时人物可能
       已站在目标地图（旧逻辑立即降落），但引擎点链接开始寻路可能再次
       起飞——先等位移启动（未见位移 JIAMO_MOVE_WAIT 超时放行），再在
       目标地图内位置连续稳定（JIAMO_ARRIVE_POLLS）判到达，到达后才
       调用飞行驭器降落（zuoqi.ensure_landed，降落后关面板防遮挡）。
    2. 到达稳定后等暗雷遭遇战斗（battle 线程托管）；**任务仍在但长时间
       未开战时点击世界地面模拟玩家行走**催发（JIAMO_WALK_WAIT 后首次、
       之后每 JIAMO_WALK_INTERVAL 一次——静默 Alt+D 不会让人物走动，
       2026-11-17 用户实测修正）。
    目标地图变化（多段搜查换图）时重新执行寻路/到达/降落判定；
    触发词连续 JIAMO_ABSENT_RESET 轮缺失即复位（空 OCR 帧为战斗心跳/
    加载过渡，不计缺失，防战斗中清掉已降落状态导致战后重复降落）。
    返回是否已处理（执行了降落/点地面走位）。
    """
    global _jiamo_phase, _jiamo_target, _jiamo_missing, _jiamo_pos_at,         _jiamo_land_at, _jiamo_warned
    sig = _seal_sig(items)
    if JIAMO_ANLEI_KEYWORD not in sig:
        if sig:  # 空 OCR 帧不计缺失（战斗中面板不可见，见 docstring）
            _jiamo_missing += 1
            if _jiamo_missing >= JIAMO_ABSENT_RESET:
                _reset_jiamo_state()
        return False
    _jiamo_missing = 0
    target = _jiamo_target_map(sig)
    if target is not None and not _jiamo_map_match(_jiamo_target or "", target):
        first = _jiamo_target is None
        _jiamo_target = target
        if not first:
            # 目标地图变化：重新执行寻路/到达/降落判定（寻路/稳定状态
            # 清零；降落重试时钟不复位——见 _reset_jiamo_arrival）
            _reset_jiamo_arrival()
            log(f"[名望] 蛟魔复仇：目标地图变为{target}，重新等待寻路到达后降落",
                "SYS")
        else:
            log(f"[名望] 蛟魔复仇：目标地图{target}（暗雷战斗），"
                f"寻路到达并稳定后执行降落", "SYS")
    elif target is None and not _jiamo_warned:
        _jiamo_warned = True
        log("[名望] 蛟魔复仇：追踪文案未解析出目标地图（暗雷战斗），"
            "按「请在」后地名出现判定到达", "WARN")
    now = time.time()
    if _jiamo_phase == 1:
        if now - _jiamo_pos_at < JIAMO_POS_CHECK_INTERVAL:
            return False  # 位置 OCR 限频（轮询热路径，规则同 OCR 三原则）
        _jiamo_pos_at = now
        return _jiamo_step_wait_battle(win, items, now)
    if now - _jiamo_pos_at < JIAMO_POS_CHECK_INTERVAL:
        return False  # 位置 OCR 限频（轮询热路径，规则同 OCR 三原则）
    _jiamo_pos_at = now
    return _jiamo_step_arrival(win, items, now)


def _click_known_dialog_option(win, items, img_np):
    """DIALOG_OPTION_MAP 命中 → 点对应绿字（含沙海/净化/钓鱼 arm 副作用）。

    供 follow 的 extras 通道与领取后对话框处理共用（领取后「这该
    怎么办呢，好担心」→「是武尊神遣我过来帮忙的。」须点选项，点屏幕
    中间不会关闭选项对话）。表值支持候选选项元组：按顺序逐个尝试。
    返回是否已处理。
    """
    for dtxt, opts in DIALOG_OPTION_MAP.items():
        if not _dialog_text_present(items, dtxt):
            continue
        if isinstance(opts, str):
            opts = (opts,)
        for opt in opts:
            if _click_green_option_once(win, items, img_np, opt, prefix="名望"):
                log(f"[名望] 对话「{dtxt}」→ 已点击「{opt}」", "SYS")
                if dtxt == SHAHAI_DIALOG_TEXT:
                    _arm_shahai_start()
                if dtxt == QINGXI_DIALOG_TEXT:
                    _arm_qingxi_start()
                _arm_mingwang_diaoyu_check(opt)
                return True
    return False


def _handle_dialog_options(win, items, img_np):
    """名望任务对话表（extra_dialog_handler）：文案命中 → 点对应绿字。

    类似 xuanshu 内层轮询的 DIALOG_OPTION_MAP 处理，交由共享 follow_task
    的 extras 通道调用（优先级高于引擎内置表与剧情对话兜底）。
    沙海迷踪/净化水晶丛林入口选项点击成功后 arm 各自「开始」按钮检测
    窗口；蛟魔探秘钓鱼入口选项点击成功后 arm 小游戏窗口检测（与玄枢
    钓鱼同款闭环），失败结果面板看门狗（_maybe_retry_diaoyu_fail）
    在最前面处理——面板在场时须先关闭+重走，防其他处理器点击被拦截。
    沙海迷踪「开始」点击成功后 arm 小游戏弹窗检测
    （_maybe_play_shahai）：弹窗出现即阻塞执行「记住混沌」闭环到结束。
    表值支持候选选项元组：按顺序逐个尝试，点到哪个算哪个。
    五庄封印执行状态机（追踪双链接/坐标稳定到达/任务储物格用唤风符）、
    灵力镐状态机（追踪「前往大唐边境，使用灵力镐」→ 点链接 → 到达 →
    任务储物格用镐；用镐后出现「开采石头」小游戏窗口 → 钓鱼同款
    长按模式闭环）、传音石引导（魔王重聚：读中央提示跨图/按方向寻路
    收敛 → 魔王护卫战斗，实现见 minigames/mowangchongju）、天宫
    奇遇状态机（追踪「前往老君丹房门口」→ 到达天宫(13,99) → 任务储物格
    用偷丹葫芦 → 「魔取仙丹」顺序解封小游戏）、蛟魔复仇暗雷战斗状态机
    （追踪「请在<地图>仔细搜查（暗雷战斗）」→ 到达目标地图后降落）、
    沙都修罗搜索状态机（追踪文案触发 → 蟠桃园五路点找 NPC）与星魂归位
    四象禁地闭环（minigames/xinghun.handle：找星魂→星宿石→归位，
    追踪含「星宿石」时触发）在最前面处理；其余任务无对应追踪文案时
    零操作。
    开采石头窗口检测排在灵力镐状态机之前：窗口在场时须先阻塞执行
    小游戏，防状态机同时重试用镐（打开背包干扰小游戏）；魔取仙丹
    窗口检测同理排在天宫奇遇状态机之前。沙海迷踪小游戏弹窗为模态
    窗口，检测放在最前（在场时其它处理器零操作）。
    返回是否已处理。
    """
    if _maybe_play_shahai(win):
        return True
    if _maybe_retry_diaoyu_fail(win, items):
        return True
    if _handle_wuzhuang_seal(win, items, img_np):
        return True
    if _pick_mining_window_check(win):
        return True
    if _handle_lingligao(win, items, img_np):
        return True
    if _handle_chuanyinshi(win, items, img_np):
        return True
    if _maybe_play_tgqy(win):
        return True
    if _xinghun_minigame.handle(win, items, img_np,
                                frame_fn=_seal_frame,
                                prefix="[名望] "):
        return True
    if _handle_tiangong_qiyu(win, items, img_np):
        return True
    if _handle_jiamo_anlei(win, items, img_np):
        return True
    if _handle_shadu_xiuluo(win, items, img_np):
        return True
    if _maybe_click_shahai_start(win):
        return True
    if _maybe_click_qingxi_start(win):
        return True
    if _mingwang_diaoyu_window_check(win):
        return True
    return _click_known_dialog_option(win, items, img_np)


def _follow_task(win, timeout=FOLLOW_TIMEOUT):
    """跟踪「山河名望」任务直到完成（复用 workflows.follow 引擎）。

    extra_dialog_handler 处理本模块任务对话表；skip_texts 传本模块
    DIALOG_SKIP_TEXTS（无选项多屏剧情台词命中即连点屏幕中间推进，
    同玄枢轶闻；不再是空表禁用）；引擎不内置任何玄枢专属小游戏检测；
    story_known_texts 传本模块对话表——
    否则本任务的带选项对话不在已知表内，选项未渲染时会被剧情兜底
    误点屏幕中间关闭；green_picker 让五庄封印按当前目标链接
    （按解析出的封印点元素）、灵力镐按「蕴灵石头」链接精确点击，
    其他任务无对应链接时自动退回引擎默认行级选择。
    返回 (是否完成, 描述)。
    """
    global _seal_phase
    if _seal_phase == 1 and time.time() - _seal_use_at > 60.0:
        # 上次跟踪超时后任务仍在：状态机可能卡在等结果，重新开始封印
        # 流程（新任务领取已在 _post_claim_flow 复位；战斗时长不在此
        # 判定——follow 每次调用独立，超时重入时已过战斗）
        log("[名望] 五庄封印：上次跟踪中断后任务仍在，重新开始封印流程",
            "WARN")
        _reset_seal_state()
    return follow_task(win, TASK_TITLE, prefix="名望", timeout=timeout,
                       extra_dialog_handler=_handle_dialog_options,
                       green_picker=_mingwang_green_picker,
                       skip_texts=DIALOG_SKIP_TEXTS,
                       story_known_texts=DIALOG_OPTION_MAP)


def _start_track(win, timeout=TRACK_TASK_TIMEOUT):
    """等待追踪面板出现「山河名望」后启动跟踪（follow_task 至任务完成）。

    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _task_in_tracker(win):
            return _follow_task(win)
        time.sleep(0.5)
    return False, f"任务追踪中未出现「{TASK_TITLE}」"


def _close_shanhe_panel(win):
    """山河名望面板若仍打开则关闭（右键左侧竖排标题牌）。

    领取后面板通常随对话框关闭；未自动关闭时面板覆盖对话区/任务追踪，
    会干扰后续跟踪与对话 OCR。复用「右键面板标题牌关闭」方式（人物
    属性面板同法实测有效）；关闭失败只告警不阻断。
    返回 (是否已关闭, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败"
    score, pt = game_map.best_match_np(_bgr_frame(img), CLAIM_SHANHE_ICON)
    if score < CLAIM_SHANHE_THRESHOLD:
        return True, "山河名望面板未打开"
    log(f"[名望] 山河名望面板仍打开，右键标题牌关闭 @({pt[0]},{pt[1]})",
        "INFO")
    human_click(win, pt[0], pt[1], button="secondary")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.4)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        s2, _pt2 = game_map.best_match_np(_bgr_frame(img), CLAIM_SHANHE_ICON)
        if s2 < CLAIM_SHANHE_THRESHOLD:
            return True, "山河名望面板已关闭"
    return False, "山河名望面板关闭失败（右键标题牌未生效）"


def _resume_tracking(win):
    """已有追踪任务时直接跟踪至完成（续跑，不重新领取）。

    对齐玄枢轶闻 run_cycle 的「追踪已有任务 → 直接跟踪」分支。
    跟进前先确保山河名望面板已关闭（残留面板覆盖任务追踪/对话区）。
    返回 (是否完成, 描述)。
    """
    closed, cinfo = _close_shanhe_panel(win)
    if not closed:
        log(f"[名望] {cinfo}", "WARN")
    debug_overlay.set_phase("跟踪山河名望")
    return _start_track(win)


def _post_claim_flow(win):
    """领取后链路：关闭领取对话框 → 关残留面板 → 跟踪至任务完成。

    任务已点击领取，跟踪失败返回 False 由主循环下一轮走续跑分支处理
    （不重走领取链重复接取）。新任务领取成功即复位五庄封印/灵力镐/
    传音石/天宫奇遇/蛟魔复仇暗雷战斗状态机，防上一轮状态残留。
    返回 (是否完成, 描述)。
    """
    _reset_seal_state()
    _reset_pick_state()
    _mowang_minigame.reset()
    _xinghun_minigame.reset()  # 星魂归位状态（新任务防上一轮残留）
    _reset_tgqy_state()
    _reset_jiamo_state()
    _reset_diaoyu_fail_state()  # 新任务领取：钓鱼失败重试计数清零
    dismissed, dinfo = _dismiss_claim_dialog(win)
    log(f"[名望] {dinfo}", "SYS" if dismissed else "INFO")
    ok, info = _resume_tracking(win)
    return ok, f"{dinfo}；{info}"


def _open_claim_panel(win, race):
    """前往领取点并打开山河名望任务面板（第一阶段入口链）。

    面板已打开时直接返回（重试/诊断时不重复走领取链）。
    名望唯一领取路线（用户 2026-11-16 规则：移除小地图快捷开关，一律
    走小地图快捷领取）：打开种族地图小地图（已在目标地图则直接开小地图，
    否则经世界地图）→ 勾选任务筛选 → 点固定位置黄色任务 NPC 名 → 等
    游戏自动寻路到达并自动打开对话框 → 点绿色选项「开启山河名望志」。
    race：已判定的种族（决定领取点配置）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮（等待战斗结束）"
    claim = CLAIM_POINTS.get(race)
    if claim is None:
        return False, f"{race}领取流程待补充（当前仅实现 {'、'.join(CLAIM_POINTS)}）"
    img = win.capture(force_activate=False)
    if img is not None and _find_panel_anchor(_bgr_frame(img)) is not None:
        return True, "山河名望面板已打开"
    debug_overlay.set_phase("前往领取点")
    ok, info = navigate_to_claim_by_map_label(win, claim)
    if not ok:
        return False, info
    debug_overlay.set_phase("领取任务")
    # 黄字点击后游戏自动寻路/传送，到达后自动打开对话框：只等对话框并
    # 点选项（不做 Alt+4/坐标到达判定/NPC 模板/点屏——世界点击会取消
    # 自动寻路）
    return _claim_and_open_panel(win, claim,
                                 timeout=MAP_CLAIM_ARRIVE_TIMEOUT)


def run_cycle(win, race=None):
    """执行一轮名望：已有任务则续跑跟踪至完成；否则领取后跟踪至完成。

    种族判定在 run_loop 中需要领取时才执行（种族不随轮次变化）。
    race：已判定种族；任务已从追踪面板消失但种族未判定时本轮跳过
    领取（下一轮 run_loop 会重判，不猜测领取点）。
    返回 (状态, 描述)；状态：done 任务完成 / daily_done 今日任务已全部
    完成（提示对话框 + 面板已关闭）/ not_found 无目标任务 / fail 本轮
    失败。
    """
    # 已有追踪任务：跳过领取直接跟踪到完成（对齐玄枢轶闻 run_cycle）
    if _task_in_tracker(win):
        log("[名望] 追踪面板已有「山河名望」任务，跳过领取直接续跑跟踪", "SYS")
        ok, info = _resume_tracking(win)
        return ("done", info) if ok else ("fail", info)
    if race is None:
        return "fail", "种族未判定，跳过本轮领取（下一轮重判）"
    ok, info = _open_claim_panel(win, race)
    if not ok:
        return "fail", info
    debug_overlay.set_phase("领取名望任务")
    status, info = claim_panel_task(win)
    if status == "daily_done":
        # 今日任务已全部完成（用户 2026-11-15 规则）：面板已关闭，
        # 直接结束本次任务（不进入跟踪、不计数）
        return "daily_done", info
    if status != "claimed":
        return status, info
    # 领取已点击成功：跟踪未完成也按 fail 返回（任务已在追踪面板，
    # 下一轮走续跑分支，不会重走领取链重复接取）
    ok, pinfo = _post_claim_flow(win)
    if ok:
        return "done", f"{info}；{pinfo}"
    return "fail", f"{info}；{pinfo}"


def verify_claim_flow(win):
    """诊断页-名望领取验证：走完整第一阶段链路（与任务流程一致，
    无额外延时）——种族判定 → 前往该种族领取点（魔族大唐边境/仙族
    天宫）→ 点击 NPC → 点绿色选项「开启山河名望志」→ 点击山河名望
    入口打开任务领取面板。

    不领取面板内任务（面板领取见 verify_claim_task_flow）；其他种族
    在判定后返回待补充。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本次验证"
    race, info = detect_race(win)
    if race is None:
        return False, info
    _save_cached_race(win, race)  # 诊断结果同步写入角色种族缓存
    log(f"[名望] 验证种族判定: {info}", "SYS")
    if race not in CLAIM_POINTS:
        return False, (f"{race}领取流程待补充"
                       f"（当前仅实现 {'、'.join(CLAIM_POINTS)}）")
    return _open_claim_panel(win, race)


def verify_map_claim_flow(win, race):
    """诊断页-名望-魔族/仙族领取验证：**仅走小地图快捷领取方式**。

    不做种族判定、不走旧「坐标寻路 + NPC 模板」路线（也不受小地图快捷
    领取开关影响，诊断始终用新方式）：打开该种族地图小地图（已在目标
    地图直接开，否则经世界地图）→ 勾选底部「任务」筛选 → 点击固定位置
    黄色任务 NPC 名（魔族 逆天魔 / 仙族 武尊神，只点一次）→ 等待游戏
    自动寻路/传送并在到达后自动打开对话框 → 点绿色选项「开启山河名望
    志」→ 打开山河名望面板后停止（不领取面板内任务）。
    race：已指定的种族（"魔族"/"仙族"）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本次验证"
    claim = CLAIM_POINTS.get(race)
    if claim is None:
        return False, f"{race}领取点未配置（当前仅实现 {'、'.join(CLAIM_POINTS)}）"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败（游戏窗口可能已最小化）"
    if _find_panel_anchor(_bgr_frame(img)) is not None:
        return True, "山河名望面板已打开"
    ok, info = navigate_to_claim_by_map_label(win, claim)
    if not ok:
        return False, info
    log(f"[名望] 验证{race}小地图快捷领取：{info}", "SYS")
    return _claim_and_open_panel(win, claim,
                                 timeout=MAP_CLAIM_ARRIVE_TIMEOUT)


def verify_claim_task_flow(win):
    """诊断页-测试领取名望任务：面板已开则直接识别领取；未开则先走
    第一阶段链路（种族判定 → 领取点 → 打开面板）再识别领取。

    领取后继续：关闭领取对话框（对话框门控 + 点屏幕中间）→ 校验任务
    追踪「山河名望」→ follow_task 跟踪至任务完成（含任务对话表）。
    领取目标：按 CLAIM_TASKS 当前种族可用的已实现任务（高级优先，
    魔族「蛟魔复仇」/仙族「五庄封印」/两族「沙都修罗」等）。
    返回 (是否成功, 描述)；成功=follow_task 跟踪至任务完成。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本次验证"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败（游戏窗口可能已最小化）"
    if _find_panel_anchor(_bgr_frame(img)) is None:
        race, info = detect_race(win)
        if race is None:
            return False, info
        _save_cached_race(win, race)  # 诊断结果同步写入角色种族缓存
        log(f"[名望] 验证种族判定: {info}", "SYS")
        if race not in CLAIM_POINTS:
            return False, (f"{race}领取流程待补充"
                           f"（当前仅实现 {'、'.join(CLAIM_POINTS)}）")
        ok, info = _open_claim_panel(win, race)
        if not ok:
            return False, info
    status, info = claim_panel_task(win)
    if status != "claimed":
        return False, info
    ok, pinfo = _post_claim_flow(win)
    return ok, f"{info}；{pinfo}"


def _load_cached_race(win):
    """读取当天角色种族缓存；无缓存/跨天/脏值返回 None（跨天重判）。"""
    entry = app_utils.char_state_get(RACE_CACHE_KEY, win)
    if not isinstance(entry, dict):
        return None
    if entry.get("date") != time.strftime("%Y-%m-%d"):
        return None
    race = entry.get("race")
    return race if race in RACE_KEYWORDS else None


def _save_cached_race(win, race):
    """写入当天角色种族缓存（合并写；角色可转种族，跨天重判）。"""
    app_utils.char_state_set(
        RACE_CACHE_KEY, win,
        {"date": time.strftime("%Y-%m-%d"), "race": race})


def _detect_race_with_retries(win):
    """种族判定（面板/OCR 瞬态重试）。返回 (种族或 None, 描述)。"""
    info = ""
    for attempt in range(1, RACE_MAX_ATTEMPTS + 1):
        race, info = detect_race(win)
        if race is not None:
            return race, info
        log(f"[名望] 种族判定第 {attempt}/{RACE_MAX_ATTEMPTS} 次失败：{info}",
            "WARN")
        if attempt < RACE_MAX_ATTEMPTS:
            time.sleep(2.0)  # 面板/OCR 瞬态，稍候重判
    return None, info


def run_loop(win, cycle_interval=3.0):
    """名望任务主循环：跟踪完成的任务 → 回去继续领取 → 往复执行。

    结束条件（满足其一即返回结束原因，同玄枢轶闻）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 任务栏不存在当前已实现的任务（如「蛟魔复仇」「五庄封印」「沙都修罗」）
    3. 种族不在已实现领取点内（当前实现魔族/仙族）
    4. 今日任务全部完成：点击接取后出现「今日还请多加歇息，明日再来吧」
       （2026-11-15 用户规则，面板已由领取链路关闭）

    启动时追踪面板已有「山河名望」→ 跳过种族判定与领取，直接续跑跟踪
    （对齐玄枢轶闻 run_cycle 的已有任务分支）。种族判定按角色持久缓存：
    判过一次后跨轮次/跨启动直接复用，不再重复打开人物属性面板（2026-11-10
    用户反馈：完成一轮返回领取前又重判种族）；仅无缓存且需要领取时判一次。

    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    done = app_utils.task_progress_done(win, TASK_MODE)  # 当天已完成（跨重启持久）
    log(f"[名望] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}"
        f"，当天已完成 {done} 次", "SYS")
    if 0 < max_rounds <= done:
        log(f"[名望] 当天进度已达上限 {done}/{max_rounds}，无需再执行", "SYS")
        return f"已完成 {done} 次名望任务，达到次数上限 {max_rounds}"

    race = _load_cached_race(win)  # 当天角色缓存（每天判一次，跨天重判）
    if race is not None:
        log(f"[名望] 使用当天已缓存的种族判定: {race}"
            f"（每日判一次，不重开人物属性面板）", "SYS")
    round_no = 0
    while True:
        round_no += 1
        log(f"[名望] ==== 第 {round_no} 轮 ====", "SYS")
        if not _task_in_tracker(win):
            if race is None:
                debug_overlay.set_phase("种族判定")
                race, info = _detect_race_with_retries(win)
                if race is None:
                    return f"名望领取失败：{info}"
                _save_cached_race(win, race)
                log(f"[名望] {info}", "SYS")
            if race not in CLAIM_POINTS:
                msg = (f"{race}领取流程待补充（当前仅实现："
                       f"{'、'.join(f'{r} {d}' for r, d in CLAIM_LOCATIONS.items())}）")
                log(f"[名望] {msg}", "WARN")
                return msg
        status, info = run_cycle(win, race)
        if status == "done":
            done += 1
            app_utils.task_progress_add(win, TASK_MODE, done)
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            debug_overlay.record_round()  # 统计页：完成轮次计数
            log(f"[名望] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次名望任务，达到次数上限 {max_rounds}"
        elif status == "daily_done":
            # 今日任务全部完成（用户 2026-11-15 规则）：不再重试领取
            log(f"[名望] {info}", "SYS")
            return f"名望今日任务已全部完成：{info}"
        elif status == "not_found":
            # 卡片列表本轮内不会变化（每日刷新），重试无意义；结束前
            # 关闭山河名望面板（用户 2026-11-17 规则：名望结束须关闭
            # 面板——残留面板遮挡后续任务的追踪面板/对话框区，后续
            # 任务模块不会处理它）
            cdesc = _close_shanhe_panel_safely(win, "无已实现任务",
                                               clear_dialog=False)
            log(f"[名望] {info}；{cdesc}", "WARN")
            return f"名望领取失败：{info}（{cdesc}）"
        else:
            log(f"[名望] {info}", "WARN")
        time.sleep(cycle_interval)
