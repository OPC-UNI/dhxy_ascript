"""名望任务自动化（山河名望）——第一阶段：魔族领取入口 + 面板领取。

流程（用户 2026-11-10 需求，先实现领取入口，后续流程再补充）：
1. 种族判定（按角色**每天**缓存一次：同一天内跨轮次/跨启动复用，
   不重复打开属性面板；角色可转种族，跨天自动重判）：点击窗口坐标
   (1230,95) 打开人物属性面板（用户标定）→
   匹配竖排标题牌 renwushuxing.png → OCR 标题牌右侧 120px 内的种族
   名称区：出现「男魔角色/女魔角色」等魔族角色列表文案 → 魔族；
   出现「女仙角色」等仙族角色列表文案 → 仙族。其他种族（人/鬼/龙/
   神巫）暂不识别（后续 TODO）。识别后关闭面板（右键标题牌 → 再点
   按钮 → Esc 三级兜底）。
2. 魔族领取点：大唐边境 (477,180) 附近，NPC 逆天魔。
   - 已在大唐边境 → 打开小地图点击 (985,384)
   - 不在 → 世界地图点击「大唐边境」（shijie_datangbianjing.png）
     打开该地图小地图 → 点击 (985,384) → 等待寻路完成
   - 到达判定：坐标进入 (477,180) 容差内；或坐标稳定且已看到逆天魔
3. 到达后点击 NPC（mingwang_nitianmo.png）→ 对话框【不使用对话框
   模板门控，直接区域 OCR】点击绿色选项「开启山河名望志」→ 等待并点击
   山河名望入口（mingwang_shanhemingwang.png）打开任务领取面板。
4. 面板领取（2026-11-10 实机标定）：任务栏锚点
   mingwang_shanhemingwang_maodian.png（面板顶部图标）→ 锚点左下角
   推导 6 张任务卡（3 列 × 2 行、190×154、列距 201/行距 161）→ 逐卡
   OCR 级别（高级/中级/初级）与标题 → 按优先级 高级 > 中级 > 初级
   + 卡片顺序领取已实现任务（蛟魔复仇/沙海迷踪/蛟魔探秘）：在命中卡内
   匹配接取按钮 mingwang_shanhemingwang_jiequ.png 点击领取，确认信号=
   接取按钮消失或任务进入追踪面板。
5. 领取后（用户 2026-11-10 规则）：接取后出现对话框——对话框模板
   门控命中则点屏幕中间关闭；任务追踪出现「山河名望」→ 复用
   xuanshu.follow_task 跟踪到任务完成（每轮点击追踪绿字推进）。
   任务执行对话按 DIALOG_OPTION_MAP 处理（对话框文案 → 绿色选项，
   可配置多个候选选项按顺序尝试），如「黑水河深，不如蛟魔之心。」→
   「逆天魔大将派我前来」/「<山河名望>」。
   沙海迷踪：入口对话「少侠准备好清理地图了吗？」→「我已经准备好了」
   后，armed 窗口内识别「开始」按钮 mingwang_chenjiuditu_kaishi 点击
   进入战斗，战斗结束即任务完成（无需其它操作）。
   蛟魔探秘：入口对话「老头子我最喜欢的，就是在这里钓鱼了~」→
   「请将鱼竿借我一用！」后，armed 窗口内识别窗口标题
   mingwang_diaochushuizhongguaiwu 并复用共享闭环
   pochufengyin.play_pochufengyin（点击冲刺连点模式：单次点击推进小，
   须高频率连点才能到达目标带；填充条停手会自动回退，进带后持续
   补点维持到窗口结束），标题消失即小游戏结束。
6. 主循环（同玄枢轶闻）：完成一轮后回去领取下一轮，直到次数上限/
   每日次数已满/任务栏无当前已实现任务；启动时追踪已有任务则跳过
   领取直接续跑。返程由领取寻路（世界地图/小地图）兜底。
"""
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard

from .app_utils import log, human_click, norm_text
from . import app_utils  # 静默模式判定（Esc 兜底按键通道）
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板（对话框区域常量）
from . import pochufengyin as _pochu_minigame  # 破除封印/钓鱼共用小游戏（闭环实现）
from .battle import is_in_battle, is_in_battle_frame  # 战斗判定（单帧复用）
from . import debug_overlay  # 调试信息悬浮层（任务阶段更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）
from .dialog_common import (_click_green_option_once,  # 绿色选项点击（共享实现）
                            _dialog_text_present,      # 对话框文案模糊匹配
                            _fuzzy_contains,           # 任务标题模糊匹配
                            clear_blocking_dialog,     # 对话框门控 + 点屏幕中间关闭
                            dialog_frame_score,        # 对话框模板门控分数
                            DIALOG_FRAME_THRESHOLD,
                            close_scene_panels)       # 场景面板清理（地图/物品栏）
from .xuanshu import follow_task  # 共享任务跟踪引擎（玄枢轶闻实现，五环/修罗/客栈同用）

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
# 种族关键词（文案内出现即计一票）：魔族角色列表含「男魔角色/女魔角色/
# 逆天魔/混天魔/绝影魔」等；仙族列表含「女仙角色/精灵仙/霓裳仙」等。
# 条带内同时出现两族字样（OCR 噪声/界面混排）视为无法判定。
# 2026-11-10 实机：条带 OCR 实际读到的是角色自身信息（如「逆天魔魔」+
# 等级/气血），角色名即图3/图4 列表中的一项。仙族角色名有不含「仙」的
# （云中君/武尊神/玄剑娥等），补仙族时须改为角色名单匹配（图17/18）。
RACE_KEYWORDS = {"魔族": "魔", "仙族": "仙"}
# 种族判定缓存（角色维度持久化，**每天一次**）：同一天内跨轮次/跨启动
# 复用、不再重复打开人物属性面板（2026-11-10 用户反馈：完成一轮后返回
# 领取前又重判种族）；角色可能转种族，跨天自动重判（用户 2026-11-10
# 规则）。诊断页重判成功会覆盖当天缓存，可作手动刷新。
RACE_CACHE_KEY = "mingwang_race"
# 种族 → 领取点（当前仅实现魔族；仙族为天宫(190,121) NPC 武尊神，待补充）
CLAIM_LOCATIONS = {"魔族": "大唐边境(477,180) 逆天魔"}

# ---- 魔族领取点（大唐边境） ----
CLAIM_MAP = "大唐边境"
CLAIM_TARGET_POS = (477, 180)          # 领取点附近坐标（用户标定）
CLAIM_POS_TOLERANCE = 10               # 到达容差（寻路落点附近即可）
CLAIM_MAP_CLICK_POS = (985, 384)       # 大唐边境小地图上的领取点点击位置
CLAIM_WORLD_ICON = "shijie_datangbianjing.png"  # 世界地图「大唐边境」标签（19.png）
CLAIM_NPC_ICON = "mingwang_nitianmo.png"        # NPC 逆天魔（用户素材 20.png）
CLAIM_NPC_THRESHOLD = 0.75             # NPC 匹配阈值（NPC 待机动画抖动，留余量）
CLAIM_NPC_BAND = (200, 120, 1180, 750)  # NPC 搜索条带（世界区域，排除 HUD/追踪面板）
CLAIM_NPC_FULL_INTERVAL = 3.0          # 条带未命中时的全帧兜底最小间隔（秒）
CLAIM_DIALOG_OPTION = "开启山河名望志"    # 领取对话框绿色选项（2026-11-10 实机日志 OCR 原文）
CLAIM_SHANHE_ICON = "mingwang_shanhemingwang.png"  # 山河名望入口（21.png，43x154）
CLAIM_SHANHE_THRESHOLD = 0.8           # 入口竖牌匹配阈值（静态 UI）
ARRIVE_TIMEOUT = 90                    # 等待寻路完成的超时（秒）
ARRIVE_STABLE_POLLS = 2                # 坐标连续不变的轮询次数（NPC 兜底到达）
# 领取入口对话链参数
NPC_CLICK_RETRY_INTERVAL = 4.0         # 点击 NPC 后未出现对话/入口时的重试间隔（秒）
OPTION_CLICK_RETRY_INTERVAL = 3.0      # 同一选项点击后的最短重试间隔（防连点）
CLAIM_ENTRY_TIMEOUT = 45               # 整条领取入口链（NPC→选项→入口）超时（秒）

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
# 期望领取优先级（高→低）：后续实现各任务执行流程后按该顺序选卡
LEVEL_PRIORITY = ("高级", "中级", "初级")
LEVEL_OCR_ALIASES = str.maketrans({"級": "级"})  # OCR 常把「级」读成繁体「級」
# 已实现识别/领取+执行流程的任务（标题/级别）。任务栏领取时按级别
# 优先级（高级>中级>初级）再按卡片顺序选第一个可用的已实现任务。
CLAIM_TASKS = (
    {"title": "蛟魔复仇", "level": "高级"},
    {"title": "沙海迷踪", "level": "高级"},
    {"title": "蛟魔探秘", "level": "高级"},
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
    "老头子我最喜欢的，就是在这里钓鱼了~":"请将鱼竿借我一用！"
}
FOLLOW_TIMEOUT = 300        # 跟踪任务总超时（秒；战斗等待时间不计入，同 xuanshu）

# ---- 沙海迷踪（高级任务）执行入口 ----
# 入口对话选项「少侠准备好清理地图了吗？」→「我已经准备好了」（在
# DIALOG_OPTION_MAP 中）；点击选项后识别「开始」按钮并点击，无需其它
# 操作，等待进入战斗，战斗结束即任务完成（用户 2026-11-10 规则）。
SHAHAI_DIALOG_TEXT = "少侠准备好清理地图了吗？"
SHAHAI_START_ICON = "mingwang_chenjiuditu_kaishi.png"  # 「开始」按钮（用户素材 24.png，115x25）
SHAHAI_START_THRESHOLD = 0.8   # 开始按钮匹配阈值（静态 UI，实测待校准）
SHAHAI_ARM_TIMEOUT = 20.0      # 点击入口选项后检测「开始」按钮的时限（秒）
_shahai_armed_at = 0.0         # 入口选项点击时间；>0 且未超时=检测开启

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


def _bgr_frame(img):
    """PIL（RGB）帧转 BGR（模板匹配用；OCR 路径继续用 PIL 帧）。"""
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


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
        from . import silent_click  # 延迟导入（避免模块级循环）
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

    魔族/仙族任一关键词出现即计一票；取票多者。两边同票（含 0 票）
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
        for race, kw in RACE_KEYWORDS.items():
            if kw in text:
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


def _at_claim_point(pos):
    """是否已在大唐边境领取点 (477,180) 附近（地名精确匹配）。"""
    if pos is None:
        return False
    name, x, y = pos
    return (name.strip() == CLAIM_MAP
            and abs(x - CLAIM_TARGET_POS[0]) <= CLAIM_POS_TOLERANCE
            and abs(y - CLAIM_TARGET_POS[1]) <= CLAIM_POS_TOLERANCE)


def _match_npc(bgr, full_check_at):
    """单帧查找逆天魔：条带优先，全帧兜底限频。

    返回 (点 或 None, 最佳分数, 更新后的全帧兜底时间戳)。世界区域
    条带未命中时按 CLAIM_NPC_FULL_INTERVAL 限频做全帧兜底（布局漂移
    保险，非目标场景不每轮付全帧成本）。
    """
    score, pt = game_map.best_match_np(bgr, CLAIM_NPC_ICON,
                                       region=CLAIM_NPC_BAND)
    if score >= CLAIM_NPC_THRESHOLD:
        return pt, score, full_check_at
    now = time.time()
    if now - full_check_at < CLAIM_NPC_FULL_INTERVAL:
        return None, score, full_check_at
    score2, pt2 = game_map.best_match_np(bgr, CLAIM_NPC_ICON)
    if score2 >= CLAIM_NPC_THRESHOLD:
        return pt2, score2, now
    return None, max(score, score2), now


def wait_arrive(win, timeout=ARRIVE_TIMEOUT):
    """等待寻路完成（大唐边境 477,180 附近）。

    到达判定：坐标进入容差；或坐标连续稳定且已看到逆天魔（寻路落点
    略有偏差但 NPC 可见时的兜底）。位置 20 秒无变化且未见 NPC 提前
    失败。每轮询只截一帧，战斗判定/坐标识别/NPC 匹配同帧复用。
    返回 (是否完成, 描述)。
    """
    deadline = time.time() + timeout
    last = None
    unchanged = 0
    polls = 0
    last_score = 0.0
    full_check_at = 0.0
    while time.time() < deadline:
        polls += 1
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(2.0)
            continue
        bgr = _bgr_frame(img)
        if is_in_battle_frame(bgr):
            # 战斗中：坐标显示不可用，跳过本轮识别（不计入稳定性）
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win, img=img)
        if _at_claim_point(pos):
            return True, (f"已到达领取点附近 "
                          f"{CLAIM_MAP}[{CLAIM_TARGET_POS[0]},{CLAIM_TARGET_POS[1]}]")
        npc_pt, last_score, full_check_at = _match_npc(bgr, full_check_at)
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if npc_pt is not None and unchanged >= ARRIVE_STABLE_POLLS:
            return True, "位置稳定且已看到逆天魔，视为到达领取点"
        if unchanged >= 10:
            return False, "位置 20 秒无变化且未出现逆天魔，寻路无响应"
        if polls % 5 == 0:
            log(f"[名望] 寻路等待中... 当前位置: {pos or '识别失败'}"
                f"，逆天魔最佳匹配 {last_score:.3f}", "INFO")
        time.sleep(2.0)
    return False, f"等待寻路完成超时（{timeout}s）"


def navigate_to_claim_point(win):
    """寻路到魔族领取点（大唐边境 477,180 附近）。

    已在大唐边境 → 打开小地图点击 (985,384)；不在 → 世界地图点击
    「大唐边境」打开该地图小地图再点击。到达后返回成功。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    # 场景前置：清理残留地图/物品栏（在人物属性面板关闭之后调用）
    close_scene_panels(win, "名望")

    pos = game_map.read_map_position(win)
    if pos is None:
        loc = "识别失败"
        log("[名望] 位置识别失败，按不在领取点处理", "WARN")
    else:
        loc = f"{pos[0]}[{pos[1]},{pos[2]}]"

    if _at_claim_point(pos):
        log(f"[名望] 已在领取点附近（{loc}），跳过寻路", "SYS")
        return True, f"已在领取点附近（{loc}）"

    if pos is not None and pos[0].strip() == CLAIM_MAP:
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
        log(f"[名望] 在{CLAIM_MAP}（{loc}），已打开小地图", "SYS")
    else:
        ok, info = game_map.open_mini_map_city(win, CLAIM_WORLD_ICON,
                                               CLAIM_MAP)
        if not ok:
            return False, info
        log(f"[名望] 不在{CLAIM_MAP}（{loc}），经世界地图前往", "SYS")

    human_click(win, CLAIM_MAP_CLICK_POS[0], CLAIM_MAP_CLICK_POS[1])
    log(f"[名望] 已点击小地图领取点 {CLAIM_MAP_CLICK_POS}，自动寻路中", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    # 清理仍打开的小地图/世界地图/物品栏（同玄枢轶闻领取流程）
    close_scene_panels(win, "名望")

    ok, info = wait_arrive(win)
    if ok:
        log(f"[名望] {info}", "SYS")
    return ok, info


def _click_claim_option(win, items, img_np):
    """单帧点击领取对话框绿色选项（不使用对话框模板门控）。

    用户 2026-11-10 明确：该对话框不使用对话框门控——本链直接对
    对话区做区域 OCR + 绿字判定，不经过 dialog_ocr_skip/模板门。
    """
    return _click_green_option_once(win, items, img_np,
                                    CLAIM_DIALOG_OPTION, prefix="名望")


def _claim_and_open_panel(win):
    """领取入口链：点击 NPC → 绿色选项 → 点击山河名望入口。

    每轮询只截一帧（战斗判定/入口匹配/对话区 OCR/NPC 匹配同帧）；
    对话框区域直接 OCR，不使用对话框模板门控（用户要求）。
    返回 (是否成功, 描述)。
    """
    deadline = time.time() + CLAIM_ENTRY_TIMEOUT
    npc_click_at = 0.0
    option_click_at = 0.0
    last_score = 0.0
    full_check_at = 0.0
    npc_logged_at = 0.0
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

        # ③ 点击 NPC（对话未出现/点击未注册时按间隔重试）。选项已点过
        #    后不再点 NPC：对话框可能仍在推进/结算，此时点 NPC 区域会
        #    误关对话（选项未选中）——等入口出现或按间隔重点选项即可
        if option_click_at == 0.0 \
                and now - npc_click_at >= NPC_CLICK_RETRY_INTERVAL:
            pt, score, full_check_at = _match_npc(bgr, full_check_at)
            last_score = score
            if pt is not None:
                # 场景锚点：位置须为大唐边境（坐标读不出时不阻断——
                # NPC 模板本身即场景锚点，位置 OCR 偶发失败常见）
                pos = game_map.read_map_position(win, img=img)
                if pos is not None and pos[0].strip() != CLAIM_MAP:
                    log(f"[名望] 场景异常：当前 {pos[0]}，不在{CLAIM_MAP}，"
                        f"不点击 NPC", "WARN")
                    time.sleep(1.0)
                    continue
                human_click(win, pt[0], pt[1])
                npc_click_at = now
                log(f"[名望] 已点击逆天魔 @({pt[0]},{pt[1]})"
                    f"（匹配 {score:.3f}）", "SYS")
            elif now - npc_logged_at >= 10.0:
                npc_logged_at = now
                log(f"[名望] 画面中未找到逆天魔（最佳匹配 {last_score:.3f}，"
                    f"阈值 {CLAIM_NPC_THRESHOLD}），继续等待", "INFO")
        time.sleep(0.4)
    return False, (f"领取入口链超时（{CLAIM_ENTRY_TIMEOUT}s，逆天魔最佳匹配 "
                   f"{last_score:.3f}）")


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

    返回 (是否确认, 描述)。两个信号任一出现即成功；超时返回未确认
    （调用方按已点击处理，避免重复点击接取）。
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
        pt = game_map.match_frame_np(bgr, PANEL_JIEQU_ICON,
                                     threshold=PANEL_JIEQU_THRESHOLD,
                                     region=region, fallback=False)
        if pt is None:
            return True, "接取按钮已消失"
        now = time.time()
        if now >= next_track:
            next_track = now + 1.5
            # 先以追踪面板标题条模板在场为门：山河名望面板左缘竖牌
            # 文字同为「山河名望」，面板未关且追踪行未渲染时会误判
            if task_tracker.track_content_region(np.asarray(img)) is not None:
                items, img_np = task_tracker.ocr_track(img)
                if task_tracker.has_task_in(items, img_np, title):
                    return True, "任务已进入追踪面板"
        time.sleep(0.5)
    return False, "接取按钮仍在且追踪面板未出现任务"


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

    已实现：高级「蛟魔复仇」「沙海迷踪」（CLAIM_TASKS）。流程：扫描
    6 卡（级别+标题）→ 按 高级>中级>初级 + 卡片顺序选定目标（未找到
    时 1s 后复扫一次，防 OCR 瞬态）→ 在该卡内匹配接取按钮并点击 →
    确认（按钮消失/任务进追踪面板）。
    返回 (状态, 描述)；状态：claimed 已点击领取 / not_found 无已实现
    任务 / fail 面板或接取按钮未找到。
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
    card = target["rect"]
    bgr = _bgr_frame(img)
    region = (card[0] - 4, card[1] - 4, card[2] + 4, card[3] + 4)
    score, pt = game_map.best_match_np(bgr, PANEL_JIEQU_ICON, region=region)
    if score < PANEL_JIEQU_THRESHOLD:
        return "fail", (f"「{title}」卡片内未找到接取按钮"
                        f"（最佳匹配 {score:.3f}，阈值 {PANEL_JIEQU_THRESHOLD}）")
    human_click(win, pt[0], pt[1])
    log(f"[名望] 已点击{level}任务「{title}」的接取按钮 @({pt[0]},{pt[1]})"
        f"（匹配 {score:.3f}）", "SYS")
    ok, vinfo = _verify_claim(win, title, card)
    if ok:
        return "claimed", f"已领取{level}任务「{title}」（{vinfo}）"
    return "claimed", (f"已点击{level}任务「{title}」的接取按钮，未检测到"
                       f"确认信号（{vinfo}）——按已领取处理，不重复点击")


def _dismiss_claim_dialog(win, timeout=CLAIM_DIALOG_TIMEOUT):
    """领取后对话框：对话框模板门控命中 → 点屏幕中间关闭（可多屏）。

    用户 2026-11-10 规则：接取后出现的对话框用对话框门控判定、点屏幕
    中间取消显示。复用 dialog_common.clear_blocking_dialog（模板命中才
    点击，点后复检模板消失，最多 3 屏）。
    返回 (是否检测并处理, 描述)。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.5)
            continue
        if dialog_frame_score(img) >= DIALOG_FRAME_THRESHOLD:
            had, _clicked = clear_blocking_dialog(win, "名望")
            if had:
                return True, "领取对话框已通过对话框门控点屏幕中间关闭"
        time.sleep(0.5)
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


def _maybe_click_shahai_start(win):
    """armed 窗口内检测沙海迷踪「开始」按钮，出现即点击（进入战斗）。

    用户 2026-11-10 规则：入口对话选「我已经准备好了」后识别
    mingwang_chenjiuditu_kaishi 并点击，无需其它操作，等待进入战斗，
    战斗结束即任务完成。窗口超时自动关闭（战斗由 follow_task 等待）。
    返回是否已点击。
    """
    global _shahai_armed_at
    if _shahai_armed_at <= 0:
        return False
    if time.time() - _shahai_armed_at > SHAHAI_ARM_TIMEOUT:
        _shahai_armed_at = 0.0
        log("[名望] 沙海迷踪「开始」按钮检测窗口超时（未出现），"
            "由任务追踪变化兜底", "WARN")
        return False
    img = win.capture(force_activate=False)
    if img is None:
        return False
    score, pt = game_map.best_match_np(_bgr_frame(img), SHAHAI_START_ICON)
    if score < SHAHAI_START_THRESHOLD:
        return False
    human_click(win, pt[0], pt[1])
    _shahai_armed_at = 0.0
    log(f"[名望] 沙海迷踪：已点击「开始」@({pt[0]},{pt[1]})"
        f"（匹配 {score:.3f}），等待进入战斗", "SYS")
    return True


def _arm_mingwang_diaoyu_check(opt):
    """钓鱼入口选项被点击 → 开启小游戏窗口检测（armed 门控）。"""
    global _mingwang_diaoyu_armed_at
    if opt == MINGWANG_DIAOYU_ARM_OPTION:
        _mingwang_diaoyu_armed_at = time.time()
        log(f"[名望] 已点击蛟魔探秘钓鱼入口选项，开启小游戏窗口检测"
            f"（{MINGWANG_DIAOYU_ARM_TIMEOUT}s 内有效）", "SYS")


def _mingwang_diaoyu_window_check(win):
    """检测并执行蛟魔探秘钓鱼小游戏（armed 门控 + 限频），返回是否已处理。

    与破除封印一致：入口选项被点击后（_arm_mingwang_diaoyu_check）才在
    POCHU_REGION 内匹配窗口标题 mingwang_diaochushuizhongguaiwu.png；
    窗口出现即阻塞执行共享闭环 pochufengyin.play_pochufengyin（**点击
    模式 + 冲刺连点 mash=True**：开始后按钮为「点击」，该游戏单击仅推进
    ~0.1 刻度，按三档节奏点不到目标带），标题消失即结束。检测限频
    MINGWANG_DIAOYU_CHECK_INTERVAL；未 armed/
    限频未到/未命中返回 False（热路径零成本）。extras 回调不携带整帧，
    armed 窗口内按限频自行截一帧（1s 一次）。
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
    score, wpt = game_map.best_match_np(
        _bgr_frame(img), MINGWANG_DIAOYU_TITLE_ICON,
        region=_pochu_minigame.POCHU_REGION)
    if wpt is None or score < _pochu_minigame.POCHU_THRESHOLD:
        return False
    log(f"[名望] 检测到蛟魔探秘钓鱼窗口 @({wpt[0]},{wpt[1]})"
        f"（匹配度 {score:.2f}）", "SYS")
    ok, info = _pochu_minigame.play_pochufengyin(
        win, MINGWANG_DIAOYU_TITLE_ICON, prefix="名望", label="蛟魔探秘钓鱼",
        mash=True)
    if ok:
        _mingwang_diaoyu_armed_at = 0.0
    log(f"[名望] {info}", "SYS" if ok else "WARN")
    return True


def _handle_dialog_options(win, items, img_np):
    """名望任务对话表（extra_dialog_handler）：文案命中 → 点对应绿字。

    类似 xuanshu 内层轮询的 DIALOG_OPTION_MAP 处理，交由共享 follow_task
    的 extras 通道调用（优先级高于引擎内置表与剧情对话兜底）。
    沙海迷踪入口选项点击成功后 arm「开始」按钮检测窗口；蛟魔探秘钓鱼
    入口选项点击成功后 arm 小游戏窗口检测（与玄枢钓鱼同款闭环）。
    表值支持候选选项元组：按顺序逐个尝试，点到哪个算哪个。
    返回是否已处理。
    """
    if _maybe_click_shahai_start(win):
        return True
    if _mingwang_diaoyu_window_check(win):
        return True
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
                _arm_mingwang_diaoyu_check(opt)
                return True
    return False


def _follow_task(win, timeout=FOLLOW_TIMEOUT):
    """跟踪「山河名望」任务直到完成（复用 xuanshu.follow_task 引擎）。

    extra_dialog_handler 处理本模块任务对话表；skip_texts={} 禁用玄枢
    轶闻的跳过表；chuyijuebao_check=False 关闭锄遗聚宝模板检测（玄枢
    专属）；story_known_texts 传本模块对话表——否则本任务的带选项对话
    不在已知表内，选项未渲染时会被剧情兜底误点屏幕中间关闭。
    返回 (是否完成, 描述)。
    """
    return follow_task(win, TASK_TITLE, prefix="名望", timeout=timeout,
                       extra_dialog_handler=_handle_dialog_options,
                       skip_texts={},
                       chuyijuebao_check=False,
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
    （不重走领取链重复接取）。返回 (是否完成, 描述)。
    """
    dismissed, dinfo = _dismiss_claim_dialog(win)
    log(f"[名望] {dinfo}", "SYS" if dismissed else "INFO")
    ok, info = _resume_tracking(win)
    return ok, f"{dinfo}；{info}"


def _open_claim_panel(win):
    """前往领取点并打开山河名望任务面板（第一阶段入口链）。

    面板已打开时直接返回（重试/诊断时不重复走 NPC 对话链）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮（等待战斗结束）"
    img = win.capture(force_activate=False)
    if img is not None and _find_panel_anchor(_bgr_frame(img)) is not None:
        return True, "山河名望面板已打开"
    debug_overlay.set_phase("前往领取点")
    ok, info = navigate_to_claim_point(win)
    if not ok:
        return False, info
    debug_overlay.set_phase("领取任务")
    return _claim_and_open_panel(win)


def run_cycle(win):
    """执行一轮名望：已有任务则续跑跟踪至完成；否则领取后跟踪至完成。

    种族判定在 run_loop 中需要领取时才执行（种族不随轮次变化）。
    返回 (状态, 描述)；状态：done 任务完成 / not_found 无目标任务 /
    fail 本轮失败。
    """
    # 已有追踪任务：跳过领取直接跟踪到完成（对齐玄枢轶闻 run_cycle）
    if _task_in_tracker(win):
        log("[名望] 追踪面板已有「山河名望」任务，跳过领取直接续跑跟踪", "SYS")
        ok, info = _resume_tracking(win)
        return ("done", info) if ok else ("fail", info)
    ok, info = _open_claim_panel(win)
    if not ok:
        return "fail", info
    debug_overlay.set_phase("领取名望任务")
    status, info = claim_panel_task(win)
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
    无额外延时）——种族判定 → 前往大唐边境领取点 → 点击逆天魔 →
    点绿色选项「开启山河名望志」→ 点击山河名望入口打开任务领取面板。

    不领取面板内任务（面板领取见 verify_claim_task_flow）；仙族/其他
    种族在判定后直接返回待补充。返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本次验证"
    race, info = detect_race(win)
    if race is None:
        return False, info
    _save_cached_race(win, race)  # 诊断结果同步写入角色种族缓存
    log(f"[名望] 验证种族判定: {info}", "SYS")
    if race not in CLAIM_LOCATIONS:
        return False, f"{race}领取流程待补充（当前仅实现魔族）"
    return _open_claim_panel(win)


def verify_claim_task_flow(win):
    """诊断页-测试领取名望任务：面板已开则直接识别领取；未开则先走
    第一阶段链路（种族判定 → 领取点 → 打开面板）再识别领取。

    领取后继续：关闭领取对话框（对话框门控 + 点屏幕中间）→ 校验任务
    追踪「山河名望」→ follow_task 跟踪至任务完成（含任务对话表）。
    领取目标：高级任务「蛟魔复仇」。
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
        if race not in CLAIM_LOCATIONS:
            return False, f"{race}领取流程待补充（当前仅实现魔族）"
        ok, info = _open_claim_panel(win)
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
    2. 任务栏不存在当前已实现的任务（如「蛟魔复仇」）
    3. 种族为仙族/其他（当前仅实现魔族）

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
            if race not in CLAIM_LOCATIONS:
                msg = (f"{race}领取流程待补充（当前仅实现魔族："
                       f"{CLAIM_LOCATIONS['魔族']}；仙族后续补充）")
                log(f"[名望] {msg}", "WARN")
                return msg
        status, info = run_cycle(win)
        if status == "done":
            done += 1
            app_utils.task_progress_add(win, TASK_MODE, done)
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            debug_overlay.record_round()  # 统计页：完成轮次计数
            log(f"[名望] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次名望任务，达到次数上限 {max_rounds}"
        elif status == "not_found":
            # 卡片列表本轮内不会变化（每日刷新），重试无意义
            log(f"[名望] {info}", "WARN")
            return f"名望领取失败：{info}"
        else:
            log(f"[名望] {info}", "WARN")
        time.sleep(cycle_interval)
