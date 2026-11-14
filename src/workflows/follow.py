"""跨任务任务跟踪引擎（从 xuanshu.follow_task 抽出，2026-11-13）。

任务模块只负责领取/返程与任务专属对话/小游戏；本模块负责：
- 点击任务追踪绿字推进 → 等待内容变化 → 任务追踪消失即完成；
- 战斗帧跳过识别（战斗时间不计入跟踪超时）；
- 追踪面板差分门 + 帧全等复用的轮询热路径；
- 任务消失确认（面板缺席 + 遮挡负条件 + 战斗线程确认门）；
- 内置对话框处理链（任务对话表 / 跳过表 / 已知绿字 / 剧情兜底）。

任务专属的小游戏与「点击选项后的 armed 动作」通过 wait_handler /
option_click_handler 注入，避免引擎反向依赖任务模块。

默认对话表（DEFAULT_DIALOG_OPTION_MAP / DEFAULT_DIALOG_SKIP_TEXTS /
DEFAULT_KNOWN_GREEN_OPTIONS）是从玄枢轶闻迁移的共享默认值：为保持
其他任务现有行为，调用方未显式传表时沿用；后续按任务实机验证后逐
步改为各任务自传（见 docs/conventions.md P1）。
"""
import difflib
import time

import numpy as np

from ..core import app_utils
from ..core import debug_overlay
from ..device import map as game_map
from ..device import tab_group
from ..device import task_tracker
from ..core.app_utils import log
from ..device.battle import is_in_battle, is_in_battle_frame
from ..device.battle import watch_in_battle as battle_watch_in_battle
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD, DIALOG_LOSE_TEXTS,
                             PositionStillness, StoryDialogDismisser,
                             _click_dialog_option_by_index,
                             _click_screen_center, _dialog_text_present,
                             click_green_option_safe, dialog_frame_score,
                             dialog_ocr_skip)

# ---- 共享默认对话表（自玄枢轶闻迁移；保持既有调用方行为）----
# 任务过程中出现的对话框绿色选项（跟踪期间检测到则点击，按顺序）
DEFAULT_KNOWN_GREEN_OPTIONS = ["注入玄蕴试试看"]

# 需要点击屏幕中间跳过的对话框文案 → 点击次数（出现则连点屏幕中间推进
# 对话，同 kezhan.DIALOG_SKIP_TEXTS）；不同文案要推进的屏数不同，次数
# 按文案单独设置，默认先统一 4 次（DIALOG_SKIP_CLICKS），实测后逐条校准
DEFAULT_DIALOG_SKIP_TEXTS = {
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
DEFAULT_DIALOG_OPTION_MAP = {
    "什么江中劫货？休要血口喷人，你有证据吗？": "指明口技之事",
    "哼，这天底下会口技的人多了去了，你怎么就能断定犯事的是我？": "指明绳结之事",
    "那、那又怎么样，这码头上做活的人那么多，你凭什么说一定是我？": "指明伤口一事",
    # 用完整选项文案：短文本「玄枢轶闻」会子串命中本选项并按字符
    # 比例点偏（实测点击 (537,425) 且写入 option_玄枢轶闻 缓存键——
    # 与领取对话框的「玄枢轶闻」选项 (490,427) 共用一键、互相覆写，
    # 领取缓存点击必现点偏 2026-10-25）
    "少侠有什么事吗？": "玄闻辨真（玄枢轶闻）",
    "凤姑娘":"玄枢轶闻",
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
DEFAULT_DIALOG_OPTION_INDEX_MAP = {
    "大唐人最喜欢喝什么": 3,  # 茶（咖啡、果汁之后的第 3 项）
    "太阳的别称是什么": 2,    # 金乌（铜乌/金乌/银乌三选项中的第 2 项；
                              # 实测区域 OCR 漏读「金乌」行致文案点击失败，
                              # 按序号兜底点击——2026-10-15）
}
# 序号点击的坐标参数（相对问题行 rect）：首项纵偏移、行间距、横向偏移
# （实测三选项文字中心：咖啡 +12、果汁 +30、茶 +48，间距 18px）

# ---- 跟踪轮询 / 任务消失确认参数（实测校准资产）----
# 循环跟踪任务参数
FOLLOW_TIMEOUT = 300                # 跟踪任务总超时（秒）
FOLLOW_POLL_INTERVAL = 0.5          # 任务信息变化轮询间隔（秒）；配合帧复用，
                                    # 静止帧轮询几乎零成本，动画帧周期由 OCR 主导
CONFIRM_POLL_INTERVAL = 0.2         # 变化确认轮询间隔（秒）；OCR 本身约 1.2s，
                                    # 确认轮询只需极短间隔留出渲染稳定余量
# 寻路停滞看门狗（2026-11-13 玄枢轶闻实测，仅点徐卿卿这一腿开启）：
# 点「再去找徐卿卿接受他的考验」链接后寻路偶发起步即卡住（人物原地
# 不动、无对话框、任务签名不变），follow 只能空等到 300s 超时——
# 11-08/11-13 同一腿各一次，11-13 超时重试后 23s 即到达，属偶发卡点。
# 位置文字区逐轮像素比对（复用 STORY_MOVE_DIFF_THRESHOLD，~0.1ms/次），
# 静止 ≥6s 才做对话框模板检查（~9ms/次，只在疑似停滞时付）：对话框在
# 场按「站定正常」重新计时；无对话框且静止 ≥15s 时补点一次任务链接。
STALL_RECHECK_TARGET = "徐卿卿"     # 仅该腿开启（用户 2026-11-13：只发现这一腿卡）
STALL_RECHECK_SECONDS = 15.0        # 允许补点的连续静止时长（秒）
STALL_DIALOG_CHECK_SECONDS = 6.0    # 静止超过该时长才查对话框模板（省热路径开销）
STALL_RECHECK_MAX = 3               # 每轮跟踪最多补点次数（防紧循环）
# 战斗结束后的寻路恢复补点（battle_resume_reclick，五环 2026-11-14 用户
# 规则）：点任务链接发起的自动寻路会被战斗打断——正常情况战斗胜利后
# 子目标推进、链接行变化，follow 点新链接重新寻路；异常情况（战败
# 「你输了」/遭遇战打断后寻路未恢复）战斗结束但任务签名不变，follow
# 只认签名变化，会空等到跟踪超时。签名不变而人物站定即判寻路已断：
# 战斗线程确认结束后宽限 BATTLE_RECLICK_GRACE 秒等面板更新（胜利推进
# 实测 ≤2s 检出，留足渲染余量），仍未变化且连续站定
# BATTLE_RECLICK_STILL 秒、本帧无对话动作时补点一次任务链接恢复寻路
# （重新进入战斗）。每次战斗结束最多补点 BATTLE_RECLICK_MAX 次，仍未
# 推进则交回轮询/超时，防紧循环。
BATTLE_RECLICK_GRACE = 8.0          # 战斗结束后等待任务变化的宽限（秒）
BATTLE_RECLICK_STILL = 3.0          # 补点前要求人物连续站定的时长（秒）
BATTLE_RECLICK_MAX = 3              # 每次战斗结束后最多补点次数
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
# 与标定值一致，面板漂移时跟随）。标题条未命中时 _task_gone_region
# 返回 None，_confirm_task_gone 回退本静态条带采集证据——游戏在无
# 追踪任务时自动收起面板（任务追踪消失=任务结束，用户 2026-11-12），
# 并查遮挡负条件防「其它面板盖住追踪面板」误判。
TASK_GONE_REGION = (0, 210, 400, 470)


def _task_gone_region(img_np):
    """任务消失确认条带（动态）：内容区上下扩边。

    相对标定 TASK_GONE_REGION=(0,210,400,470) 与内容区顶（标题条底）
    的偏移：top=内容区顶-18、bottom=内容区顶+242；横向取内容区左右缘
    （标题/链接行均在区内，右侧收窄同时减少 OCR 面积）。标题条模板
    未命中返回 None——追踪面板不在场（游戏无任务自动收起/手动关闭/
    遮挡），调用方回退静态条带并查遮挡负条件。
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



def _task_gone_occluded(img, bgr=None):
    """面板缺席判完成前的遮挡负条件：其它面板在场 → 本帧不判任务消失。

    追踪面板标题条未命中不止「游戏收起面板/用户手动关闭」一种原因——
    背包、世界/小地图、队伍/申请加入面板打开时会盖住面板，单看「面板
    缺席+条带无任务」会把「被盖住」误判成完成。命中任一已知遮挡即推迟
    本轮判定，等对应面板被处理（extras/队伍线程关闭）后复查。只在面板
    缺席、证据已足的判定帧调用（每轮任务完成一次），实测成本：单人模式
    ~0.1s（背包/地图模板各 ~30ms）；组号模式 ~0.55s（另加队伍面板判定
    ~0.44s：标志模板 0.436~0.683 随成员数退化，须 OCR 兜底，2026-11-12
    实测）——只影响完成判定帧，不在轮询热路径。

    标准对话框不在遮挡名单（用户 2026-11-12 确认）：对话框不会盖住任务
    追踪面板（15:03 实测对话框在场时面板仍可读），面板缺席+对话框在场
    即任务完成——把对话框当遮挡会让已完成的任务卡在确认循环里空转。

    模板检查与 task_tracker.panel_occluded 共用；队伍面板只在组号模式
    检查（单人模式跟随期间队伍面板不会残留前台，跳过含 OCR 兜底的
    ~0.44s 检查；组号模式战斗线程切 Tab 做队伍操作时残留面板会盖住
    追踪面板，2026-11-11 实测）。
    """
    return task_tracker.panel_occluded(
        img, bgr=bgr, include_team=tab_group.active())


def _confirm_task_gone(win, title, fallback_texts=(), first_img=None,
                       polls=TASK_GONE_CONFIRM_POLLS, check_occluders=True):
    """确认任务确实从追踪面板消失（连续轮询未见且无战斗遮挡）。

    OCR 偶发漏读（标题误读、面板瞬时遮挡）会误判任务消失——五环实测
    误判「任务消失」导致完成次数虚增、任务提前结束；战斗画面遮挡追踪
    面板时 OCR 必然读不到任务，战斗中不计入确认窗口（延长窗口等战斗
    结束后再观察）。返回 True=确认消失（任务完成）。

    面板缺席（标题条模板未命中）：按任务结束判定——游戏在无追踪任务
    时把面板自动收起（用户 2026-11-12 规则：任务追踪消失=任务结束）。
    修罗/五倍的任务完成由战斗驱动（战斗结束返程），不走此判定；
    战斗线程仍在战斗时的缺席帧不计证据（战斗 UI 会盖住面板）。

    遮挡负条件：面板缺席、证据已足的判定帧先查背包/地图/队伍等大面板
    （见 _task_gone_occluded），在场则推迟本轮判定——否则「被面板盖住」
    会被当成任务完成。标准对话框不在遮挡名单（用户 2026-11-12 确认：
    对话框不会盖住追踪面板；完成瞬间的收尾剧情对话不阻塞完成判定）。
    check_occluders=False 跳过该检查（战斗结束返程快路径：此时不可能
    有背包/地图面板，对话由 _task_gone_dialog_guard 兜底，省 ~0.1s，
    组号模式 ~0.55s）。

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
    panel_seen = False       # 确认窗口内是否采集到消失证据（条带 OCR）
    panel_missing_logged = False
    occluded_logged = False  # 遮挡负条件命中日志（限一次）
    occluded_by_panel = False  # 最近一次判定因遮挡被推迟（窗口耗尽时不得视为完成）
    while time.time() < deadline and time.time() < hard_deadline:
        # 屏幕已不是队长 Tab（用户手动切走 / pass 中途开始）：队员屏本来
        # 就没有任务追踪，面板缺席不构成完成证据——放弃本次确认，交回
        # 外层轮询（follow 的队长屏护栏会暂停识别，切回后重新确认）。
        if not tab_group.on_leader_screen(win):
            log("[任务追踪] 屏幕已不是队长 Tab，本次不判定任务消失", "INFO")
            return False
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
        gone_absent = False  # 本帧证据是否来自面板缺席回退条带（判完成前查遮挡）
        gone_region = _task_gone_region(np.asarray(img))
        if gone_region is None:
            # 战斗线程仍在战斗（动画间隙帧）：战斗 UI 会遮住追踪面板，
            # 面板缺席不能作为消失证据（任务仍在战斗途中）。战斗结束
            # 路径以战斗线程确认结束为前提，此检查对它是空操作
            if battle_watch_in_battle():
                gone_polls = 0
                time.sleep(TASK_GONE_POLL_INTERVAL)
                continue
            # 面板缺席按任务结束判定（用户 2026-11-12 规则：游戏在无
            # 追踪任务时自动收起面板，任务追踪消失=任务结束）。回退
            # 静态条带采集证据（条带读不到任务即确认完成，主循环下轮
            # has_task 会复查兜住非收尾战斗的误判）
            gone_region = TASK_GONE_REGION
            gone_absent = True
            if not panel_missing_logged:
                panel_missing_logged = True
                log("[任务追踪] 追踪面板不在场，按任务消失判定", "INFO")
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
            if gone_absent and check_occluders and _task_gone_occluded(img):
                # 面板缺席但其它大面板在场（背包/地图/队伍/对话框盖住
                # 追踪面板）：标题条同样未命中，任务其实可能还在——
                # 放弃本轮证据，等面板被处理后再判（本帧识别成本高
                # 约 0.55s，只在证据已足的判定帧做，不在轮询热路径）
                gone_polls = 0
                occluded_by_panel = True
                if not occluded_logged:
                    occluded_logged = True
                    log("[任务追踪] 追踪面板不在场但其它面板在场"
                        "（被遮挡），不判定任务消失", "INFO")
                time.sleep(TASK_GONE_POLL_INTERVAL)
                continue
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
    if not panel_seen or occluded_by_panel:
        # 整窗未采集到消失证据（面板一直缺席且人物移动/被遮挡），或
        # 最近一次判定被遮挡推迟：按未完成处理，交回外层正常轮询
        # （本函数会被周期性重入）
        log("[任务追踪] 任务消失确认：追踪面板未在场或其它面板在场，"
            "本次不判定任务消失", "INFO")
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
    if not tab_group.on_leader_screen(win):
        return False  # 屏幕已不是队长 Tab（pass/用户手动切走）：不处理对话框
    if tpl_gate and dialog_ocr_skip(img, win) \
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
    if not tab_group.on_leader_screen(win):
        return False  # 屏幕已不是队长 Tab（pass/用户手动切走）：不处理对话框
    try:
        img = win.capture(force_activate=False)
        if img is None or is_in_battle_frame(img):
            return False
        if tpl_gate and dialog_ocr_skip(img, win) \
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
    from ..device import bag  # 函数内导入防循环：bag→battle→… 环
    try:
        ran, info = bag.use_sheyaoxiang_if_due(win)
        if ran:
            log(f"[摄妖香] 寻路期续用: {info}", "SYS")
    except Exception as e:
        log(f"[摄妖香] 寻路期续用异常: {e}", "WARN")


def _handle_dialogs(win, items, img_np, img, title="", prefix="",
                    story_gate=None, green_order=None, green_picker=None,
                    fallback_keywords=None, require_coord=False,
                    skip_texts=None, dialog_option_map=None,
                    dialog_option_index_map=None, known_green_options=None,
                    option_click_handler=None):
    """follow 轮询的内置对话框处理链，返回是否已处理。

    顺序：特定对话框指定绿字 → 点屏幕中间跳过 → 已知绿字选项 → 无选项
    剧情对话点屏兜底。任务面板在场时的正常轮询调用（面板缺席即任务
    结束，走消失确认，不做对话框处理——对话框不挡追踪面板，用户
    2026-11-12 确认）。

    dialog_option_map / dialog_option_index_map / known_green_options：
    任务对话表；缺省用共享默认表（调用方可经 follow_task 显式覆盖）。
    option_click_handler：选项点击成功后的任务专属回调 (opt)（如玄枢
    钓鱼入口点击后开启窗口检测）。
    """
    option_map = DEFAULT_DIALOG_OPTION_MAP if dialog_option_map is None \
        else dialog_option_map
    option_index_map = DEFAULT_DIALOG_OPTION_INDEX_MAP \
        if dialog_option_index_map is None else dialog_option_index_map
    known_options = DEFAULT_KNOWN_GREEN_OPTIONS \
        if known_green_options is None else known_green_options
    handled = False
    for dtxt, opt in option_map.items():
        if _dialog_text_present(items, dtxt):
            if click_green_option_safe(win, items, img_np, opt, prefix=prefix):
                handled = True
                if option_click_handler is not None:
                    option_click_handler(opt)  # 任务专属 armed 动作
                break
            # 选项未被 OCR 识别（如单字选项）：按序号点击兜底
            idx = option_index_map.get(dtxt)
            if idx and _click_dialog_option_by_index(win, items, img_np,
                                                     dtxt, idx, prefix):
                handled = True
                if option_click_handler is not None:
                    option_click_handler(opt)  # 同上（序号兜底路径）
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
        for dtxt, clicks in (DEFAULT_DIALOG_SKIP_TEXTS.items()
                             if skip_texts is None
                             else skip_texts.items()):
            if _dialog_text_present(items, dtxt):
                # 命中即按文案对应次数连点推进（多屏对话无需枚举
                # 后续文案，漏推进由下一轮询 OCR 自愈）；命中文案
                # 打日志便于排查
                log(f"[{prefix}] 跳过对话命中文案「{dtxt}」，连点 {clicks} 次", "SYS")
                _click_screen_center(win, times=clicks, prefix=prefix)
                handled = True
                break
    if not handled:
        for opt in known_options:
            if click_green_option_safe(win, items, img_np, opt, prefix=prefix):
                debug_overlay.set_phase("任务对话")  # 看板阶段
                handled = True
                break
    # 无选项剧情对话兜底：点屏幕中间推进/关闭（不处理会常驻屏幕，
    # 对话门每轮命中、对话区 OCR 每 0.5s 空烧，2026-11-10 实测）
    # img 为本轮已截帧（复用 OCR 结果时为 None，寻路判定内部补截）：
    # 寻路中点屏会取消自动寻路，人物停下不走（2026-11-11 用户实机）
    if not handled and story_gate is not None:
        story_gate.try_dismiss(win, items, img_np, prefix=prefix, img=img)
    return handled

def follow_task(win, title, timeout=FOLLOW_TIMEOUT, prefix="",
                extra_dialog_handler=None, green_order=None, progress_key=None,
                green_picker=None, fallback_keywords=None, first_items=None,
                signature_key=None, ignore_empty_key=False, skip_texts=None,
                pre_click_handler=None, require_coord=False,
                post_track_handler=None, signature_ratio=0.8,
                task_gone_polls=None,
                dialog_tpl_gate=True, dialog_extra_gate=None,
                battle_extras_tpl_gate=False,
                story_dialog_dismiss=True, story_known_texts=None,
                stall_recheck=False, battle_resume_reclick=False,
                dialog_option_map=None, dialog_option_index_map=None,
                known_green_options=None, wait_handler=None,
                option_click_handler=None):
    """循环跟踪任务直到完成（prefix 供其他任务复用，如五环）。

    每轮：点击任务当前第一个绿色文案 → 等待任务信息变化（内容更新）
    → 进入下一轮；任务从追踪面板消失即完成。
    等待期间处理对话框（dialog_option_map 指定绿字、skip_texts
    点屏幕中间跳过（None 用默认 DEFAULT_DIALOG_SKIP_TEXTS，{} 禁用——
    五环等自有对话处理的调用方禁用，防误触；文案→点击次数字典，
    不同文案按屏数配置不同连点次数）、known_green_options 已知绿字、
    战斗失败「你输了」点击任务绿字推进）。任务专属小游戏由调用方通过
    wait_handler 注入，引擎本身不内置。
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
    面板缺席判定（2026-11-12 用户规则，见 _confirm_task_gone）：游戏
    在无追踪任务时会把面板自动收起（只剩左缘小箭头）——追踪面板消失
    即任务结束。修罗/五倍的任务完成由战斗驱动（目标战斗结束后返程），
    不走面板缺席判定；「看打！」进入战斗后战斗结束 → 返程领任务。
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
    wait_handler：任务专属小游戏/附加等待处理回调
    (win, items, img_np, img) → 是否已处理。在 extras 处理之后、任务面板
    查找之前调用；命中则本帧跳过识别、下一轮询继续（如玄枢的钓鱼/机关/
    锄遗聚宝）。引擎本身不感知任何任务专属小游戏。
    option_click_handler：对话框选项点击成功后的回调 (opt)，用于任务专属
    的 armed 动作（如点击「先钓一下再说！」后开启钓鱼窗口检测）。
    dialog_option_map / dialog_option_index_map / known_green_options：
    任务对话表；缺省用共享默认表（保持既有任务行为）。
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
    story_known_texts：剧情对话兜底的「已知对话表」（默认共享
    DEFAULT_DIALOG_OPTION_MAP）。复用本引擎的任务（名望）传自己的选项表
    ——否则本任务的带选项对话不在已知表内、选项未渲染时会被误判为
    无选项剧情对话点屏关闭（2026-11-10）。
    stall_recheck：寻路停滞看门狗（默认关，其他任务零影响）。点追踪
    链接后寻路偶发起步即卡住（人物原地不动、无对话框、签名不变），
    follow 只能空等到 300s 超时（2026-11-08/11-13 玄枢轶闻同一腿实测）。
    开启后位置文字区逐轮像素比对：非战斗、静止 ≥STALL_RECHECK_SECONDS
    且无对话框时补点一次任务追踪链接（每轮至多 STALL_RECHECK_MAX 次，
    补点后重新计时）。仅当本轮签名含 STALL_RECHECK_TARGET 时生效
    （用户 2026-11-13：只发现「再去找徐卿卿接受他的考验」这一腿会卡）。
    battle_resume_reclick：战斗结束后的寻路恢复补点（默认关，五环
    2026-11-14 用户规则）。点任务链接发起的自动寻路被战斗打断后，正常
    情况胜利推进使签名变化、follow 点新链接；异常情况（战败/遭遇战打断
    后未恢复）签名不变，follow 会空等到超时。开启后战斗线程确认结束起
    宽限 BATTLE_RECLICK_GRACE 秒等面板更新，仍未变化且人物连续站定
    BATTLE_RECLICK_STILL 秒、本帧无对话动作时补点一次任务链接恢复寻路
    （重新进入战斗），每次战斗结束至多 BATTLE_RECLICK_MAX 次。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    # 无选项剧情对话点屏推进兜底（2026-11-10，见 dialog_common.
    # StoryDialogDismisser）。已知对话表用任务选项表：其对话的绿字选项
    # 未渲染时只等待，不当剧情对话点屏（防误关/误推进）
    option_map = DEFAULT_DIALOG_OPTION_MAP if dialog_option_map is None \
        else dialog_option_map
    option_index_map = DEFAULT_DIALOG_OPTION_INDEX_MAP \
        if dialog_option_index_map is None else dialog_option_index_map
    known_options = DEFAULT_KNOWN_GREEN_OPTIONS \
        if known_green_options is None else known_green_options
    # 已知对话表默认用任务选项表：其对话的绿字选项未渲染时只等待，
    # 不当剧情对话点屏（防误关/误推进）
    known_texts = option_map if story_known_texts is None \
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
            # 战斗刚结束的首个非战斗轮：面板缺席按任务消失处理（用户
            # 2026-11-11 确认）——否则要空等 6s 后不判定（战斗结束
            # 返程慢 10s）
            if _confirm_task_gone(win, title, fallback_keywords,
                first_img=img, polls=_gone_polls()):
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
            # 任务可能恰在本次点击尝试前完成：面板过渡帧标题/内容仍在但
            # 绿字链接已消失，track_task 找不到可点击绿字返回失败。变化
            # 确认落在完成瞬间时必然走到这里（2026-11-13 客栈第 20 次
            # 完成记漏：进度键确认前对话框/extras 持续复位 changed_once，
            # 确认后才补这次多余点击，点不到绿字 → 整轮 fail 少记 1 次）。
            # 先做任务消失确认：确认消失即按完成返回（用户规则：任务追踪
            # 消失=任务结束），任务仍在才保持原失败。
            if _confirm_task_gone(win, title, fallback_keywords,
                                  polls=_gone_polls()):
                if _task_gone_dialog_guard(win, extra_dialog_handler,
                    tpl_gate=dialog_tpl_gate,
                    extra_gate=dialog_extra_gate):
                    continue  # 弹窗仍在：先处理，下一轮再确认
                if not task_seen:
                    return False, info
                return True, f"任务已从任务追踪中消失（本轮跟踪 {rounds} 次点击）"
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

        # 等待任务信息变化或任务消失；期间处理对话框选项
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
        # 寻路停滞看门狗状态（每轮跟踪重置）：仅 stall_recheck 且本轮签名
        # 含 STALL_RECHECK_TARGET（徐卿卿腿）时创建，其他轮次零开销
        stall_on = stall_recheck and STALL_RECHECK_TARGET in sig
        stall_watch = PositionStillness() if stall_on else None
        stall_reclicks = 0
        # 战斗结束补点恢复状态（battle_resume_reclick，见常量说明）：
        # battle_reclick_at 为战斗线程确认结束时刻（None=不在战后窗口），
        # battle_reclicks 为本次战斗结束以来的补点次数；站定计时用
        # PositionStillness（位置文字区像素比对，零 OCR）
        battle_reclick_at = None
        battle_reclicks = 0
        reclick_watch = PositionStillness() if battle_resume_reclick else None

        while time.time() < deadline:
            poll_interval = CONFIRM_POLL_INTERVAL if changed_once else FOLLOW_POLL_INTERVAL
            time.sleep(poll_interval)
            # 组号 pass 进行中：屏幕是队员 Tab，跳过本轮识别（同外层护栏）
            if tab_group.pass_active:
                if stall_watch is not None:
                    stall_watch.reset()  # 屏幕是队员 Tab，位置区不可比
                continue
            # 用户手动切到队员 Tab（2026-11-13 用户实机）：队员屏没有本
            # 任务的任务追踪，继续识别会把「面板不在场」判成任务消失；
            # 队员的对话框也会被当队长对话框处理。暂停识别与点击，切回
            # 队长 Tab 后继续（暂停期逐轮顺延 deadline，不计入跟踪超时）。
            if not tab_group.on_leader_screen(win):
                deadline += poll_interval
                tab_group.note_off_leader(f"[{prefix}] ")
                if stall_watch is not None:
                    stall_watch.reset()
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
                if stall_watch is not None:
                    stall_watch.reset()  # 战斗中位置不可比，战后重新计时
                # 新一轮战斗：战后补点窗口作废，战斗结束处重新计时
                battle_reclick_at = None
                battle_reclicks = 0
                if reclick_watch is not None:
                    reclick_watch.reset()
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
                    if battle_extras_tpl_gate and dialog_ocr_skip(img, win) \
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
                if reclick_watch is not None:
                    # 战斗线程确认结束：战后补点宽限期起点；战斗帧位置
                    # 不可比，站定计时从本帧重建
                    battle_reclick_at = time.time()
                    reclick_watch.reset()
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
                # 确认：战斗结束后任务追踪会消失，随即应打开背包返程）；
                # 跳过遮挡检查（返程快路径，对话由 _task_gone_dialog_guard
                # 兜底，省 ~0.55s）
                if _confirm_task_gone(win, title, fallback_keywords,
                    first_img=img, polls=_gone_polls(), check_occluders=False):
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
                if dialog_tpl_gate and dialog_ocr_skip(img, win) \
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
                if stall_watch is not None:
                    stall_watch.reset()  # 本帧有处理动作：静止计时重新开始
                changed_once = False
                continue
            # 任务专属小游戏/附加等待处理（由调用方注入，见 wait_handler
            # 说明）：命中即视为本帧已处理，下一轮询继续
            if wait_handler is not None \
                    and wait_handler(win, items, img_np, img):
                if stall_watch is not None:
                    stall_watch.reset()  # 小游戏处理中：静止属正常
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
            dlg_handled = _handle_dialogs(
                win, items, img_np, img, title=title, prefix=prefix,
                story_gate=story_gate, green_order=green_order,
                green_picker=green_picker,
                fallback_keywords=fallback_keywords,
                require_coord=require_coord, skip_texts=skip_texts,
                dialog_option_map=option_map,
                dialog_option_index_map=option_index_map,
                known_green_options=known_options,
                option_click_handler=option_click_handler)
            # 战斗结束补点恢复（battle_resume_reclick，见常量说明）：战斗
            # 打断点击链接发起的自动寻路后，签名不变而人物站定即判寻路
            # 已断——宽限期（等胜利推进的面板更新）后补点任务链接恢复
            # 寻路（重新进入战斗）。本帧有对话动作（如战败「你输了」已由
            # 内置链点链接/推进对话）时只重置站定计时，不做补点
            if reclick_watch is not None:
                if dlg_handled:
                    reclick_watch.reset()
                else:
                    still_r = reclick_watch.seconds(img)
                    if battle_reclick_at is not None \
                            and battle_reclicks < BATTLE_RECLICK_MAX \
                            and time.time() - battle_reclick_at \
                            >= BATTLE_RECLICK_GRACE \
                            and still_r >= BATTLE_RECLICK_STILL:
                        battle_reclick_at = time.time()
                        battle_reclicks += 1
                        ok_r, info_r = task_tracker.track_task(
                            win, title, green_order=green_order,
                            green_picker=green_picker,
                            fallback_keywords=fallback_keywords,
                            pre_items=(items, img_np),
                            require_coord=require_coord)
                        reclick_watch.reset()
                        if ok_r:
                            app_utils.mark_track_walk()
                            log(f"[{prefix}] 战斗结束后任务未变化"
                                f"（站定 {still_r:.0f}s），已补点任务链接恢复寻路"
                                f"（第 {battle_reclicks}/{BATTLE_RECLICK_MAX} 次）",
                                "SYS")
                        else:
                            log(f"[{prefix}] 战斗结束后任务未变化"
                                f"（站定 {still_r:.0f}s），补点任务链接失败: "
                                f"{info_r}（第 {battle_reclicks}/"
                                f"{BATTLE_RECLICK_MAX} 次）", "WARN")
                        if stall_watch is not None:
                            stall_watch.reset()
                        continue
            # 寻路停滞看门狗（见 STALL_RECHECK_*）：位置区逐轮像素比对
            # （~0.1ms）；静止 ≥STALL_DIALOG_CHECK_SECONDS 才查对话框模板
            # （~9ms/次，只在疑似停滞时付），对话框在场按「站定正常」重新
            # 计时；无对话框且静止 ≥STALL_RECHECK_SECONDS 补点一次任务
            # 追踪链接恢复寻路（2026-11-13 徐卿卿腿空等 300s 实测）
            if stall_watch is not None:
                if dlg_handled:
                    stall_watch.reset()  # 本帧有对话动作：静止计时重新开始
                else:
                    still = stall_watch.seconds(img)
                    if still >= STALL_DIALOG_CHECK_SECONDS:
                        if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
                            stall_watch.reset()  # 对话框在场：站定属正常
                        elif still >= STALL_RECHECK_SECONDS \
                                and stall_reclicks < STALL_RECHECK_MAX:
                            ok_s, info_s = task_tracker.track_task(
                                win, title, green_order=green_order,
                                green_picker=green_picker,
                                fallback_keywords=fallback_keywords,
                                require_coord=require_coord)
                            stall_reclicks += 1
                            stall_watch.reset()
                            if ok_s:
                                app_utils.mark_track_walk()
                                log(f"[{prefix}] 寻路停滞 {still:.0f}s"
                                    f"（无对话框/战斗），已补点任务链接恢复寻路",
                                    "WARN")
                            else:
                                log(f"[{prefix}] 寻路停滞 {still:.0f}s，"
                                    f"补点任务链接失败: {info_s}", "WARN")
        else:
            return False, f"第 {rounds} 轮跟踪后任务信息无变化（超时）"
    return False, f"跟踪任务超时（{timeout}s）"
