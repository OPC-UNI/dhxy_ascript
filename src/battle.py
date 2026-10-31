"""战斗自动化：按场次调度战斗操作与自动战斗，处理巫医对话框。

战斗状态检测（模板匹配多信号判定，不用 OCR，防止非战斗画面误报）：
- 操作条按钮（法术/防御/自动）：战斗专属 UI，误报率最低（非战斗实测 ≤0.39，
  真实按钮 1.0，阈值 0.85）
- round_banner.png：回合横幅「回合」文字区（第X回合任意回合均匹配，
  非战斗实测 ≤0.45，真实横幅 0.99，阈值 0.8），覆盖操作条隐藏的回合间隙
- battle_first.png 即「第1回合」横幅，与 battle_other.png（「第2回合」横幅）
  双模板比分数判断是否第 1 回合（两横幅仅数字不同，会互相弱匹配）

战斗状态判定统一在本模块实现，其他模块（玄枢轶闻等）复用 is_in_battle。

场次调度（不在战斗 → 进入战斗 → 战斗结束算一场战斗，回合变化不算；
进入与结束均连续两次轮询确认，防战斗开场抖动与回合间隙误判）：
1. 第 1 场战斗：进场若已处于自动战斗先取消自动（双模板比分数判定），
   按配置执行人物/召唤兽操作（第 1 回合），操作完成后立即点击自动
   （一轮就结束的战斗也挂上自动）；战斗结束时仍未挂上的，下一场
   进场补点一次自动
2. 第 6/11/16... 场（每 5 场）：进入战斗后点击一次自动按钮——自动战斗
   跨场持续，例行确认；已处于自动战斗时（按钮为取消自动）跳过不点
3. 其余场次：不操作，交由游戏自动战斗
4. 每场战斗结束后检查血蓝条回满（战斗中不检查）
5. 巫医对话框：文案含 WUYI_DIALOG_TEXTS 中任一（修罗「我是超级巫医…」、
   五倍「召唤兽和坐骑都是上天赐给我们的好朋友…」）时点击绿色选项
   「全部医治和修复」；战后立即检查，非战斗时周期性检查

人物/召唤兽操作（第 1 回合）：
- 攻击：直接点击攻击按钮位置
- 防御：识别 fangyu 模板并点击
- 技能C-R：识别 fashu 模板点击打开技能选择面板 → 点击对应技能
  → 鼠标移动到攻击按钮位置再点击一次
- 无 / 捕捉 / 逃跑：不操作（捕捉与逃跑暂无按钮模板，仅日志提示），
  等待倒计时自动结束本阶段

技能网格规律（用户实测 5 个锚点）：面板 3 列 × 5 行，
「技能C-R」= 第 C 列、第 R 行：
    x = 512 + 150 * (C - 1)   （锚点 512 / 662 / 815）
    y = 300 + 30 * (R - 1)    （锚点 300 / 420）
召唤兽技能坐标已按首尾锚点标定（见 BEAST_SKILL_POS，用户实测）。
"""
import re
import time

import cv2
import numpy as np
from PIL import Image
from ascript.windows.window.Window import WindowCaptureFormat

from .app_utils import log, human_click, human_move, current_task_mode, UserAbort
from . import map as game_map
from . import status_bars  # 血蓝条检测回满（每场战斗结束后调用）
from . import task_tracker  # 对话框区域与绿色选项定位（巫医对话框）
from . import debug_overlay  # 调试信息悬浮层（战斗场次更新）
from . import team  # 队伍身份（抢盒子的队长规则）
from .control_window import (load_battle_actions,
                             load_xiuluo_hezi, load_wubei_hezi,
                             load_auto_famen, load_auto_refill)
from . import tab_group  # 组号模式（一窗口多 Tab）：pass 抑制标志与队员自动 pass

# ---- 战斗 UI 坐标与模板（窗口相对坐标，用户实测） ----
ATTACK_POS = (366, 415)        # 攻击按钮位置；技能选择后也在此位置再点一次
SKILL_GRID_ORIGIN = (512, 300)  # 人物技能网格原点（技能1-1 位置）
SKILL_GRID_STEP = (150, 30)     # 列/行间隔（3 列 × 5 行）
FASHU_ICON = "fashu.png"       # 法术按钮模板（用户提供 15.png）
FANGYU_ICON = "fangyu.png"     # 防御按钮模板（用户提供 16.png）
AUTO_ICON = "auto.png"         # 自动按钮模板（用户提供 17.png）
AUTO_CANCEL_ICON = "auto_cancel.png"    # 取消自动按钮模板（用户提供 18.png）
AUTO_RESUME_ICON = "auto_resume.png"    # 恢复自动按钮模板（用户提供 19.png）
BATTLE_FIRST_ICON = "battle_first.png"  # 「第1回合」横幅模板（第 1 回合判定，见 is_round_1）
BATTLE_OTHER_ICON = "battle_other.png"  # 「第2回合」横幅模板（与第1回合双模板比分数）
ROUND_BANNER_ICON = "round_banner.png"  # 回合横幅「回合」文字区（任意回合均匹配）
ROUND_BANNER_THRESHOLD = 0.8   # 横幅文字匹配阈值（非战斗画面实测最高 0.45，真实横幅 0.99）
BAR_BUTTON_THRESHOLD = 0.85    # 操作条按钮匹配阈值（非战斗画面实测最高 0.39，真实按钮 1.0）

# 召唤兽技能坐标（用户实测标定）：技能1~技能6 在技能面板中的
# 窗口相对坐标——首尾实测 技能1 (588,300)、技能6 (588,410)，中间
# 平均分布（6 点 5 段，步进 22px）
BEAST_SKILL_POS = {f"技能{i + 1}": (588, 300 + 22 * i) for i in range(6)}

# ---- 自动使用法门（开关见通用设置；坐标用户标定） ----
FAMEN_CLICK_POS = (1319, 711)  # 法门点击坐标（窗口相对坐标，用户标定）
_famen_missing_logged = False  # 坐标未标定时仅记录一次（防御性保留，避免刷日志）

# ---- 时序参数（秒） ----
WATCH_POLL_INTERVAL = 0.5   # 战斗监控轮询间隔（单帧判定 + 快速匹配后 0.5s 无压力，
                            # 进场确认 2 次轮询 ≈ 0.5~1s，比原先 1~2s 更快）
BAR_POLL_INTERVAL = 0.5     # 操作条出现/消失轮询间隔
ROUND_BAR_TIMEOUT = 60      # 等待新回合操作条出现（含回合动画时间）
PHASE_SWITCH_DELAY = 2.0    # 点击操作后操作条关闭、召唤兽条出现
BEAST_BAR_TIMEOUT = 10      # 等待召唤兽操作条出现
IDLE_COUNTDOWN_WAIT = 40    # 无操作时等待游戏倒计时结束（游戏约 30 秒）
BAR_GONE_TIMEOUT = 10       # 操作后等待操作条关闭
SKILL_PANEL_DELAY = 1.0     # 点击法术后等待技能选择面板打开
SKILL_CONFIRM_DELAY = 0.5   # 点击技能后等待面板收起
AUTO_CANCEL_SETTLE = 1.5    # 点击取消自动后等待自动按钮出现的超时上限（快恢复即继续）

# ---- 场次调度（进入战斗 = 一场战斗开始，回合变化不算） ----
AUTO_REASSERT_EVERY = 5      # 每 5 场战斗进入战斗后点击一次自动按钮（第 1 场操作完成后点击）
AUTO_ASSERT_TIMEOUT = 35     # 例行确认自动时等待自动按钮出现的超时（覆盖一整回合操作条）
BATTLE_CONFIRM_POLLS = 2     # 战斗进入的连续轮询确认次数（防开场抖动）
BATTLE_END_CONFIRM_POLLS = 4  # 战斗结束的连续轮询确认次数（0.5s×4=2s 窗口，
                              # 与原先 1s×2 相同，防回合间隙误判结束）

# ---- 战斗 UI 搜索条带（快速匹配用，窗口相对坐标，实测 1378x831 布局） ----
BANNER_BAND = (0, 40, 400, 240)  # 回合横幅带（第1/第2回合横幅实测 x34-121 y88-116）
BAR_BAND_HEIGHT = 190            # 操作条按钮带高度（按钮实测 y≈677，取底部 190px）
FULL_CHECK_INTERVAL = 3.0        # 带区全未命中时全帧兜底复查的最小间隔（秒）——
                                 # 非战斗画面带区每轮询都全未命中，全帧兜底若不限频，
                                 # 空闲轮询反而比旧实现更慢；限频后布局漂移 3 秒内
                                 # 仍能被兜底发现

# ---- 巫医对话框（召唤兽医治） ----
# 对话框文案（游戏内原文，任一出现则处理；不同任务场景文案不同，选项相同）
WUYI_DIALOG_TEXTS = (
    "我是超级巫医，可以帮你医治召唤兽",
    "召唤兽和坐骑都是上天赐给我们的好朋友，要好好对待它们，会保佑你的。",
)
WUYI_OPTION_TEXT = "全部医治和修复"                    # 对话框绿色选项（游戏内原文，精确子串匹配）
WUYI_CHECK_INTERVAL = 2.0    # 战斗结束后高发窗口内的巫医对话框检查间隔（秒）。
                             # 实测 5s 节奏弹窗最多等 5s 才被点击，人物寻路走远后
                             # 对话框关闭、点击失效；2s 下最坏延迟 ~2s——该节奏
                             # 仅在高发窗口内保持（性能 2026-10-27：行走中差分门
                             # 因世界移动失效，2s 恒检 OCR 白烧 ~0.3 核）
WUYI_POST_BATTLE_WINDOW = 15.0   # 战斗结束后巫医弹窗高发窗口（召唤兽受伤弹窗在
                                 # 战斗结束时弹出；窗口内保持 2s 节奏）
WUYI_EXPECT_WINDOW = 15.0        # 主流程点击巫医 NPC 后的高频检查窗口（秒）：
                                 # 覆盖流程 8s 等待上限 + 对话框处理余量；窗口内
                                 # 战斗线程用 2s 节奏（10s 空闲节奏会错过 8s 等待，
                                 # 2026-10-29 日志 10:58 兜底触发+重复点击教训）
WUYI_IDLE_CHECK_INTERVAL = 10.0  # 高发窗口/预期窗口之外的安全兜底节奏：仅剩
                                 # 巫医 NPC 对话未被预期窗口覆盖的兜底（人物静止、
                                 # 对话框不随移动关闭，10s 内处理）
# 非队长保持模式（五倍/修罗 _keep_alive 置位）：队伍共享的巫医对话框
# 由队长点 NPC 触发，本进程不设 wuyi_expected_at——10s 空闲节奏会
# 在队长走远关闭对话框后才赶到、点击失效（2026-10-30 实测）；置位后
# 空闲节奏提升到 2s。非队长人物静止，对话框区域差分门有效（行走中
# 差分门失效烧 OCR 的 10s 校准不适用于站立场景），成本可控。
wuyi_fast_idle = False
WUYI_RETRY_COOLDOWN = 15.0   # 点击选项后的冷却期（对话框未关闭时防重复点击）
WUYI_DIFF_THRESHOLD = 2.0    # 对话框区域平均像素差阈值：低于则跳过 OCR（区域无变化）

# ---- 修罗/五倍盒子（福源广泽盒：出现时点「我需要」） ----
XIULUO_HEZI_ICON = "xiuluohezi.png"                 # 盒子面板（用户提供 5.png 素材）
XIULUO_HEZI_WOXUYAO_ICON = "xiuluohezi_woxuyao.png"  # 「我需要」按钮（用户提供 6.png 素材）
XIULUO_HEZI_WOXUYAO_THRESHOLD = 0.92  # 按钮匹配阈值（用户要求 ≥0.92，小按钮防误点）
XIULUO_HEZI_CHECK_INTERVAL = 5.0   # 非战斗时检查节奏（与巫医检查同档）
XIULUO_HEZI_RETRY_COOLDOWN = 15.0  # 点击后的冷却期（盒子未消失防重复点击）

_SKILL_RE = re.compile(r"^技能(\d)-(\d)$")

# 战斗监控线程的确认状态（watch_loop 维护、任务线程读取）。
# is_in_battle 是模板匹配的单帧判定，战斗动画间隙（操作条/回合横幅
# 不显示）会返回 False；watch_loop 的进入/结束各有连续轮询确认窗口，
# 全程未误判（实测 2.5 分钟战斗 19:45:06-19:47:35 无误报），任务侧
# 判定「任务消失」等边界时复用此状态，免重复截图判定
_watch_in_battle = False
# 主流程点击巫医 NPC 的时刻（xiuluo 任务进程设置、战斗线程读取）：
# 此后 WUYI_EXPECT_WINDOW 内战斗线程用 2s 高频检查，8s 等待窗口内
# 必能捡到对话框；只读共享、时间戳自然过期，无需复位
wuyi_expected_at = 0.0


def watch_in_battle():
    """战斗监控线程当前确认的战斗状态（线程间共享，读取几乎零成本）。"""
    return _watch_in_battle

# 暂未支持的操作（无按钮模板/坐标），仅日志提示
_UNSUPPORTED_ACTIONS = ("捕捉", "逃跑")


def _bar_band(img):
    """操作条按钮搜索条带（相对帧尺寸：底部 190px 全宽，按钮实测 y≈677）。"""
    h, w = img.shape[:2]
    return (0, max(0, h - BAR_BAND_HEIGHT), w, h)


def is_in_battle(win):
    """判断是否处于战斗中（多信号判定，防止非战斗画面误报）。

    操作条按钮（法术/防御/自动）是战斗专属 UI，出现即战斗操作阶段，
    误报率最低；回合横幅「回合」文字覆盖操作条隐藏的回合间隙。
    用 BGR 帧 + 条带限定匹配（单帧判定，模板/阈值/短路顺序不变）。
    组号 pass 期间返回 True：屏幕上可能是队员 Tab，队员画面的战斗
    判定不可代表队长——统一按「战斗中等待」处理，让全库所有
    「战斗中跳过」护栏在 pass 期间同样生效（走位/等待循环不会在
    队员屏幕点小地图）；pass 内部判定用 is_in_battle_frame（帧级），
    不受此影响。pass 只会在组号激活时发生，单号恒 False 直通。
    """
    if tab_group.pass_active:
        return True
    img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
    if img is None:
        return False
    return is_in_battle_frame(img)


# 全帧兜底复查的最近时间（限频，见 FULL_CHECK_INTERVAL）
_last_full_check = 0.0


def is_in_battle_frame(img):
    """在已捕获帧上判断是否战斗中（模板、阈值与顺序同 is_in_battle）。

    供轮询循环复用本帧已抓的截图（如 follow_task 内层），避免重复截窗。
    接受 BGR numpy（战斗内部路径）或 PIL 帧（其他模块复用）；
    条带限定匹配；带区全部未命中时全帧兜底复查——限频（非战斗画面
    带区每轮询都全未命中，全帧兜底若每轮询都跑反而比旧实现更慢），
    布局漂移 3 秒内仍能被发现。
    """
    global _last_full_check
    if isinstance(img, Image.Image):
        img = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    band = _bar_band(img)
    found = game_map.match_frame_multi(img, (FASHU_ICON, FANGYU_ICON, AUTO_ICON),
                                       threshold=BAR_BUTTON_THRESHOLD,
                                       regions=(band, band, band))
    for icon in (FASHU_ICON, FANGYU_ICON, AUTO_ICON):
        if found[icon] is not None:
            return True
    if game_map.match_frame_np(img, ROUND_BANNER_ICON,
                               threshold=ROUND_BANNER_THRESHOLD,
                               region=BANNER_BAND, fallback=False) is not None:
        return True
    now = time.time()
    if now - _last_full_check < FULL_CHECK_INTERVAL:
        return False
    _last_full_check = now
    # 布局漂移兜底：带区全部未命中时全帧重跑一遍原顺序（限频）
    found2 = game_map.match_frame_multi(img, (FASHU_ICON, FANGYU_ICON, AUTO_ICON),
                                        threshold=BAR_BUTTON_THRESHOLD)
    for icon in (FASHU_ICON, FANGYU_ICON, AUTO_ICON):
        if found2[icon] is not None:
            return True
    return game_map.match_frame_np(img, ROUND_BANNER_ICON,
                                   threshold=ROUND_BANNER_THRESHOLD) is not None


def is_round_1(win):
    """判断当前是否第 1 回合（第1/第2回合横幅双模板比分数）。

    两个横幅仅回合数字不同，模板互相弱匹配（实测对方横幅 0.893 ≥
    0.8 阈值，单模板判定会误判）；比分数取高者：第1回合模板分数
    达标且更高才算第 1 回合。第 3 回合及以后两模板分数均不高于
    第1模板，不会误判。
    """
    img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
    if img is None:
        return False
    return _is_round_1_frame(img)


def _is_round_1_frame(img):
    """帧内第 1 回合判定（横幅带区比分数，带区双未命中时全帧兜底）。"""
    first_score, _ = game_map.best_match_np(img, BATTLE_FIRST_ICON, region=BANNER_BAND)
    other_score, _ = game_map.best_match_np(img, BATTLE_OTHER_ICON, region=BANNER_BAND)
    if first_score < ROUND_BANNER_THRESHOLD and other_score < ROUND_BANNER_THRESHOLD:
        first_score, _ = game_map.best_match_np(img, BATTLE_FIRST_ICON)
        other_score, _ = game_map.best_match_np(img, BATTLE_OTHER_ICON)
    return first_score >= ROUND_BANNER_THRESHOLD and first_score > other_score


def char_skill_pos(action):
    """解析「技能C-R」为人物技能网格坐标；不合法返回 None。"""
    m = _SKILL_RE.match(action)
    if not m:
        return None
    col, row = int(m.group(1)), int(m.group(2))
    if not (1 <= col <= 3 and 1 <= row <= 5):
        return None
    return (SKILL_GRID_ORIGIN[0] + SKILL_GRID_STEP[0] * (col - 1),
            SKILL_GRID_ORIGIN[1] + SKILL_GRID_STEP[1] * (row - 1))


def _park_mouse(win):
    """将鼠标移回窗口中间，避免悬停在操作条上遮挡召唤兽操作/高亮按钮。"""
    human_move(win.rect.left + win.rect.width // 2,
               win.rect.top + win.rect.height // 2)


def _norm_text(s):
    """去标点/空白（保留中文与字母数字），选项文案精确子串匹配前规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做子串匹配可大幅降低漏配。
    """
    return re.sub(r"[^\w]", "", s)


def _wait_icon(win, icon, timeout, want_found=True):
    """轮询模板出现（want_found=True）或消失（False），战斗结束提前返回 False。

    单帧判定：每轮询只截一帧（BGR），战斗状态与图标匹配共用（原先
    每轮询截 2 次窗且 find_template 带激活抢前台焦点）。
    战斗结束判定连续两次轮询不在战斗（防回合间隙误判，回合变化不算战斗）。
    """
    deadline = time.time() + timeout
    out_polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if img is None:
            time.sleep(BAR_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            out_polls = 0
        else:
            out_polls += 1
            if out_polls >= BATTLE_CONFIRM_POLLS:
                return False
        pt = _find_in_frame(win, img, icon)
        if (pt is not None) == want_found:
            return True
        time.sleep(BAR_POLL_INTERVAL)
    return False


def _battle_ended(win):
    """战斗结束判定：连续两次轮询不在战斗（防回合间隙误判，回合变化不算）。"""
    if is_in_battle(win):
        return False
    time.sleep(BAR_POLL_INTERVAL)
    return not is_in_battle(win)


def _find_in_frame(win, frame, icon):
    """帧内查找图标中心（操作条图标走条带+兜底）；frame 为 None 时自行截帧。"""
    if frame is None:
        frame = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if frame is None:
            return None
    region = _bar_band(frame) if icon in (FASHU_ICON, FANGYU_ICON,
                                          AUTO_ICON, AUTO_CANCEL_ICON) else None
    return game_map.match_frame_np(frame, icon, region=region, fallback=True)


def _click_template(win, icon):
    """查找模板并点击其位置，返回是否成功（单帧匹配，不抢焦点）。"""
    pt = _find_in_frame(win, None, icon)
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    return True


def _cancel_auto(win):
    """进入第 1 回合时若已处于自动战斗，先点击取消自动恢复手动。

    不取消的话手动操作来不及执行就被自动抢先。原实现要求「取消自动」
    「恢复自动」同时存在才取消（上局遗留自动），新客户端下两按钮未必
    同时出现；改为双模板比分数：取消自动分数达标且高于自动按钮分数
    即处于自动中（两按钮同位置样式相近，单模板阈值会误判）。
    """
    img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
    if img is None:
        return
    _cancel_auto_frame(win, img)


def _cancel_auto_frame(win, img):
    """帧内取消自动（见 _cancel_auto）。返回是否点击了取消。

    取消后轮询自动按钮出现代替固定等待（快恢复时立即继续，1.5s 超时
    上限与旧固定等待相同）。
    """
    band = _bar_band(img)
    cancel_score, cancel_pt = game_map.best_match_np(img, AUTO_CANCEL_ICON, region=band)
    auto_score, _ = game_map.best_match_np(img, AUTO_ICON, region=band)
    if cancel_score >= BAR_BUTTON_THRESHOLD and cancel_score > auto_score:
        human_click(win, cancel_pt[0], cancel_pt[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡操作条
        log("[战斗] 检测到自动战斗，已点击取消自动恢复手动", "SYS")
        # 等待操作条恢复手动（自动按钮出现即恢复）
        _wait_icon(win, AUTO_ICON, AUTO_CANCEL_SETTLE, want_found=True)
        return True
    # 分数可疑（取消自动弱匹配但未达判定）才输出，便于排查模板是否需更新；
    # 明确的非自动状态（取消自动 <0.5）不打日志
    if cancel_score >= 0.5:
        log(f"[战斗] 非自动状态（取消自动={cancel_score:.3f} 自动={auto_score:.3f}），无需取消", "INFO")
    return False


def _do_action(win, action, label, frame=None, round_no=None):
    """执行一条战斗操作，返回是否发出了操作指令。

    frame：调用方已捕获的 BGR 帧（第 1 回合人物操作与取消自动/回合
    判定共用一帧，省重复截图）；None 时自行截帧。
    round_no：回合号（操作日志前缀用，替代原先独立的「第 N 回合：
    X 操作」头行，日志合并为一行）。
    未发出指令（无/暂不支持/识别失败）时操作条会停留到倒计时结束，
    调用方需等待该阶段自行结束。
    """
    prefix = f"第 {round_no} 回合 " if round_no else ""
    if action == "无":
        return False
    if action in _UNSUPPORTED_ACTIONS:
        log(f"[战斗] {prefix}{label}：{action}暂不支持（无按钮模板），本阶段不操作", "WARN")
        return False

    # 攻击：直接点击攻击按钮
    if action == "攻击":
        human_click(win, ATTACK_POS[0], ATTACK_POS[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
        log(f"[战斗] {prefix}{label}：攻击", "INFO")
        return True

    # 防御：识别防御按钮点击
    if action == "防御":
        pt = _find_in_frame(win, frame, FANGYU_ICON)
        if pt is None:
            log(f"[战斗] {prefix}{label}：未识别到防御按钮，本阶段不操作", "WARN")
            return False
        human_click(win, pt[0], pt[1])
        _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
        log(f"[战斗] {prefix}{label}：防御", "INFO")
        return True

    # 技能：识别法术按钮 → 打开技能面板 → 点击技能 → 攻击按钮位置再点一次
    if label == "人物":
        pos = char_skill_pos(action)
    else:
        pos = BEAST_SKILL_POS.get(action)
        if pos is None:
            log(f"[战斗] {prefix}{label}：{action} 坐标未配置，本阶段不操作", "WARN")
            return False
    if pos is None:
        log(f"[战斗] {prefix}{label}：无法解析 {action}，本阶段不操作", "WARN")
        return False
    pt = _find_in_frame(win, frame, FASHU_ICON)
    if pt is None:
        log(f"[战斗] {prefix}{label}：未识别到法术按钮，本阶段不操作", "WARN")
        return False
    human_click(win, pt[0], pt[1])
    time.sleep(SKILL_PANEL_DELAY)  # 等待技能选择面板打开
    human_click(win, pos[0], pos[1])
    time.sleep(SKILL_CONFIRM_DELAY)  # 等待面板收起
    human_click(win, ATTACK_POS[0], ATTACK_POS[1])
    _park_mouse(win)  # 鼠标移开，避免遮挡召唤兽操作条
    log(f"[战斗] {prefix}{label}：施放 {action}", "INFO")
    return True


def _click_famen(win, bar_ready=False):
    """自动使用法门：每场战斗第 1 回合点一次法门坐标（开关开启时）。

    bar_ready=False 时先等操作条出现（fashu 可见）；首场战斗操作条
    已确认出现时传 True 免重复等待。坐标 FAMEN_CLICK_POS 未标定
    （None）时跳过并仅记录一次。每场战斗调用一次（首场在
    handle_battle 内、后续场次在 watch_loop 进场时）。
    仅人物动作为法术（技能X-Y）时生效：法门只对法术生效（用户确认），
    攻击/防御/捕捉等动作点了无效。
    """
    if not load_auto_famen():
        return
    if not load_battle_actions()["char"].startswith("技能"):
        return
    global _famen_missing_logged
    if FAMEN_CLICK_POS is None:
        if not _famen_missing_logged:
            _famen_missing_logged = True
            log("[战斗] 法门：开关已开启但坐标未标定（FAMEN_CLICK_POS=None），"
                "本场不点法门", "WARN")
        return
    if not bar_ready and not _wait_icon(win, FASHU_ICON, ROUND_BAR_TIMEOUT,
                                        want_found=True):
        log("[战斗] 法门：未等到第 1 回合操作条，本场不点法门", "WARN")
        return
    human_click(win, FAMEN_CLICK_POS[0], FAMEN_CLICK_POS[1])
    log(f"[战斗] 法门：已点击法门 @ ({FAMEN_CLICK_POS[0]},{FAMEN_CLICK_POS[1]})", "SYS")


def handle_battle(win):
    """处理首场战斗：第 1 回合按配置操作，操作完成后点击自动。

    返回 "auto_ok"（本场已挂上自动）或 "no_auto"（战斗结束时仍未挂上，
    供 watch_loop 在下一场战斗进场时补点一次自动）。
    后续场次由 watch_loop 按场次调度（每 5 场例行点击一次自动，
    其余场次交由游戏自动战斗），不再进入本函数；战斗结束的确认、
    血蓝条检查与巫医对话框处理也统一在 watch_loop 中完成。
    """
    actions = load_battle_actions()  # 操作配置已在 main.py 启动时输出，不重复打日志
    auto_engaged = False
    rounds = 0
    round_1 = True
    round1_frame = None  # 第 1 回合操作条帧（取消自动/回合判定/人物操作共用）
    while not _battle_ended(win):
        # 等待操作条出现（新回合）
        if not _wait_icon(win, FASHU_ICON, ROUND_BAR_TIMEOUT, want_found=True):
            break
        time.sleep(0.5)  # 操作条刚出现，稍作稳定再操作
        rounds += 1
        if rounds == 1:
            # 首次操作条：截一帧完成「取消自动 + 第 1 回合判定」，
            # 该帧同时供人物操作的防御/法术按钮定位复用（省 2 次截图）
            round1_frame = win.capture(force_activate=False,
                                       format=WindowCaptureFormat.OPENCV)
            if round1_frame is None:
                _cancel_auto(win)
                round_1 = is_round_1(win)
            else:
                canceled = _cancel_auto_frame(win, round1_frame)
                round_1 = _is_round_1_frame(round1_frame)
                if canceled:
                    round1_frame = None  # 取消自动后操作条已变化，帧失效
            log(f"[战斗] 当前为第 1 回合" if round_1 else "[战斗] 当前非第 1 回合", "INFO")
        # 自动使用法门：第 1 回合先点一次法门坐标（每场一次），
        # 然后继续原流程的战斗操作（操作条已确认出现，免重复等待）
        if rounds == 1 and round_1:
            _click_famen(win, bar_ready=True)
        # 非第 1 回合：点击自动按钮，剩余回合交由游戏自动战斗
        if not (rounds == 1 and round_1):
            if _click_template(win, AUTO_ICON):
                _park_mouse(win)  # 鼠标移开，避免遮挡操作条
                auto_engaged = True
                log("[战斗] 已点击自动，剩余回合由游戏自动战斗", "SYS")
                break
            log("[战斗] 未识别到自动按钮，本回合按配置手动操作", "WARN")
        # ---- 第 1 回合（或自动不可用）：按配置执行人物/召唤兽操作 ----
        # （操作日志自带回合前缀，不再单独输出「第 N 回合：X 操作」头行）
        acted = _do_action(win, actions["char"], "人物",
                           frame=round1_frame if rounds == 1 else None,
                           round_no=rounds)
        if acted:
            time.sleep(PHASE_SWITCH_DELAY)  # 操作条关闭，召唤兽条出现
        else:
            _wait_icon(win, FASHU_ICON, IDLE_COUNTDOWN_WAIT, want_found=False)
        if _battle_ended(win):
            break
        # ---- 召唤兽操作阶段 ----
        _wait_icon(win, FASHU_ICON, BEAST_BAR_TIMEOUT, want_found=True)
        acted = _do_action(win, actions["beast"], "召唤兽", round_no=rounds)
        if rounds == 1 and round_1 and acted:
            # 操作完成立即点自动（赶在操作条关闭前）：一轮就结束的战斗也要
            # 挂上自动，否则下场战斗进场不在自动状态；点不到时由下一操作条
            # （本场第 2 回合）或 watch_loop 下场进场补点
            time.sleep(SKILL_CONFIRM_DELAY)
            if _click_template(win, AUTO_ICON):
                _park_mouse(win)  # 鼠标移开，避免遮挡操作条
                auto_engaged = True
                log("[战斗] 操作完成，已点击自动", "SYS")
                break
            log("[战斗] 操作条已关闭未点到自动，待下一操作条补点", "INFO")
        _wait_icon(win, FASHU_ICON,
                   BAR_GONE_TIMEOUT if acted else IDLE_COUNTDOWN_WAIT,
                   want_found=False)
    return "auto_ok" if auto_engaged else "no_auto"


def _click_auto_once(win):
    """进入战斗后点击一次自动按钮（每 5 场例行确认，自动战斗跨场持续）。

    已处于自动战斗时操作条显示「取消自动」而非「自动」，自动按钮
    不会出现，轮询超时即跳过、不打扰；自动被关掉（如次数耗尽）时
    点一次重新开启。操作条可能正处回合间隙，最多等待一整回合的
    操作条周期；战斗结束（连续两轮确认）提前返回。单帧判定。
    返回是否点击了自动按钮（组号模式据此触发队员自动 pass）。
    """
    deadline = time.time() + AUTO_ASSERT_TIMEOUT
    out_polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
        if img is None:
            time.sleep(BAR_POLL_INTERVAL)
            continue
        if is_in_battle_frame(img):
            out_polls = 0
        else:
            out_polls += 1
            if out_polls >= BATTLE_CONFIRM_POLLS:
                return False
        pt = game_map.match_frame_np(img, AUTO_ICON, region=_bar_band(img),
                                     fallback=True)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            _park_mouse(win)  # 鼠标移开，避免遮挡操作条
            log("[战斗] 已点击自动按钮（每 5 场例行确认）", "SYS")
            return True
        time.sleep(BAR_POLL_INTERVAL)
    log("[战斗] 未识别到自动按钮（可能已处于自动战斗），本场不点击", "INFO")
    return False


# 巫医对话框选项未识别告警去重标记（对话框消失后复位，防轮询刷屏）
_wuyi_option_missing_warned = False
# 对话框区域上一帧（像素差分预过滤：区域无变化跳过 OCR）
_wuyi_prev_roi = None


def _handle_wuyi_dialog(win):
    """巫医对话框：文案含「我是超级巫医…」时点击绿色选项「全部医治和修复」。

    文案用模糊匹配（抗 OCR 误读）；选项去标点后精确子串匹配（游戏内原文，
    记忆规则：选项匹配不"纠正"用字）；限定对话框区域（x≥PANEL_MAX_X）
    且为绿字行，避免误点追踪面板的绿字链接。返回是否已点击。

    性能：对话框区域像素差分预过滤——区域与上一帧几乎相同（无弹窗/
    动画）时跳过 OCR（实测 OCR 148-311ms）；已知对话框存在但选项
    未识别（告警中）时不跳过，保持重试。
    """
    global _wuyi_option_missing_warned, _wuyi_prev_roi
    img = win.capture(force_activate=False)
    if img is None:
        return False
    l, t, r, b = task_tracker.DIALOG_REGION
    roi = np.asarray(img)[t:b, l:r]
    # 尺寸防护：窗口瞬时切换（最小化/缩放/遮挡裁切）会返回异常尺寸帧，
    # 与上一帧形状不符时直接差分会广播异常，且 _wuyi_prev_roi 永不更新
    # → 每轮询永久抛异常刷屏（实测 (330,412,3) vs (330,580,3)）。形状
    # 不符时跳过差分、以新帧为基线自愈
    if _wuyi_prev_roi is not None and not _wuyi_option_missing_warned \
            and _wuyi_prev_roi.shape == roi.shape:
        diff = float(np.abs(roi.astype(np.int16)
                            - _wuyi_prev_roi.astype(np.int16)).mean())
        if diff < WUYI_DIFF_THRESHOLD:
            _wuyi_prev_roi = roi
            return False
    _wuyi_prev_roi = roi
    items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
    from .xuanshu import _dialog_text_present  # 函数内导入，避免循环依赖
    if not any(_dialog_text_present(items, t) for t in WUYI_DIALOG_TEXTS):
        _wuyi_option_missing_warned = False
        return False
    pt = _find_wuyi_option(items, img_np)
    if pt is None:
        # 选项文字可能很小（实测五倍对话框「全部医治和修复」字高仅 13px，
        # 区域 OCR 原尺寸漏检），对对话框区 2 倍放大重识别一次
        l, t, r, b = task_tracker.DIALOG_REGION
        crop = img.crop((l, t, r, b)).resize(((r - l) * 2, (b - t) * 2),
                                             Image.LANCZOS)
        items2, _ = game_map.ocr_zoom(crop)  # 快引擎：max/1600 检出分辨率同档、CPU 更低
        mapped = [(txt, [l + rect[0] // 2, t + rect[1] // 2,
                         l + rect[2] // 2, t + rect[3] // 2], score)
                  for txt, rect, score in items2]
        pt = _find_wuyi_option(mapped, img_np)
    if pt is None:
        if not _wuyi_option_missing_warned:
            log("[战斗] 巫医对话框出现，但未识别到「全部医治和修复」选项", "WARN")
            _wuyi_option_missing_warned = True
        return False
    human_click(win, pt[0], pt[1])
    log(f"[战斗] 巫医对话框：已点击「全部医治和修复」 @ ({pt[0]},{pt[1]})", "SYS")
    _wuyi_option_missing_warned = False
    return True


def _find_wuyi_option(items, img_np):
    """在 OCR 行中查找「全部医治和修复」绿字选项，返回点击坐标或 None。

    去标点精确子串匹配（游戏内原文）；限定对话框区域（x≥PANEL_MAX_X）
    且为绿字行，避免误点追踪面板的绿字链接。
    """
    needle = _norm_text(WUYI_OPTION_TEXT)
    for txt, rect, score in items:
        if score >= 0.55 and needle in _norm_text(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            return task_tracker.green_text_center(img_np, rect,
                                                  prefer_underline=False)
    return None


def _wuyi_check_interval(now, last_battle_end_at, expected_at):
    """巫医检查节奏：战后高发窗口或主流程 NPC 预期窗口内 2s，否则 10s 兜底。

    预期窗口（wuyi_expected_at 之后 WUYI_EXPECT_WINDOW 秒）：主流程点击
    NPC 后对话框即将出现，10s 空闲节奏的相位不巧时会错过流程 8s 等待
    上限、被迫兜底自点（2026-10-29 日志 10:58 教训）。
    非队长保持模式（wuyi_fast_idle=True）：空闲节奏提升到 2s——队伍
    共享巫医对话框随队长走远关闭，10s 节奏点击失效（2026-10-30 实测）。
    纯函数便于单测。
    """
    if now - last_battle_end_at < WUYI_POST_BATTLE_WINDOW \
            or now - expected_at < WUYI_EXPECT_WINDOW:
        return WUYI_CHECK_INTERVAL
    if wuyi_fast_idle:
        return WUYI_CHECK_INTERVAL
    return WUYI_IDLE_CHECK_INTERVAL


def _handle_hezi(win):
    """修罗/五倍盒子：不在战斗时若出现福源广泽盒，点击「我需要」。

    开关规则：仅修罗/五倍模式执行；抢盒子开关只在**非队长**时生效，
    是队长则**一定抢**（队长规则优先）。身份以最近一次队伍检查结果
    为准（team.known_leader：开队伍检查时 check_team_min 更新；关闭
    检查队伍时任务寻路/领取成功即记队长）。
    性能（见 CLAUDE.md 性能规则）：5 秒节奏单帧检查；两段匹配——先
    盒子后按钮，按钮只有盒子命中才匹配；点击后冷却防重复。
    盒子出现位置暂未标定条带（素材为裁剪图无绝对坐标），全帧匹配
    单模板 ~27ms/次（5 秒节奏占空比 <1%）；后续拿到窗口内位置后
    可收紧为条带匹配。
    """
    mode = current_task_mode()  # 当前任务模式（多任务顺序执行时由主循环切换）
    if mode == "修罗":
        enabled = load_xiuluo_hezi()
    elif mode == "五倍":
        enabled = load_wubei_hezi()
    else:
        return False
    if not (team.known_leader() is True or enabled):
        return False  # 非队长/身份未知且开关关闭：不抢
    img = win.capture(force_activate=False, format=WindowCaptureFormat.OPENCV)
    if img is None:
        return False
    box_pt = game_map.match_frame_np(img, XIULUO_HEZI_ICON, fallback=False)
    if box_pt is None:
        return False
    # 按钮小（82x26），用户要求 0.92 以上才点，防误点
    btn_pt = game_map.match_frame_np(img, XIULUO_HEZI_WOXUYAO_ICON,
                                     threshold=XIULUO_HEZI_WOXUYAO_THRESHOLD,
                                     fallback=False)
    if btn_pt is None:
        log("[战斗] 修罗盒子出现，但未识别到「我需要」按钮", "WARN")
        return False
    human_click(win, btn_pt[0], btn_pt[1])
    log(f"[战斗] 修罗盒子：已点击「我需要」 @ ({btn_pt[0]},{btn_pt[1]})", "SYS")
    return True


def _member_auto_pass_hook(win, wait_auto):
    """组号模式：队长点完自动后遍历队员 Tab 点击自动（仅激活时执行）。

    队员与队长同场战斗，队长的自动触发时机即队员的触发时机
    （首场 wait_auto=5s 等操作条、每 5 场例行 wait_auto=1s 多半已挂
    自动跳过）。任何异常只记日志，不影响战斗监控主循环。
    """
    try:
        if tab_group.active():
            tab_group.member_auto_pass(win, wait_auto)
    except Exception as e:
        log(f"[战斗] 组号队员自动 pass 异常: {e}", "WARN")


def watch_loop(win):
    """战斗监控线程入口：轮询战斗状态，按场次调度战斗操作。

    场次规则（不在战斗 → 进入战斗 → 战斗结束算一场战斗，回合变化不算；
    进入与结束均连续两次轮询确认，防战斗开场抖动与回合间隙误判）：
    - 第 1 场：handle_battle 按配置操作，操作完成后点击自动
    - 第 6/11/16... 场（每 5 场）：进入战斗后点击一次自动按钮
    - 其余场次：不操作，交由游戏自动战斗（自动战斗跨场持续生效）
    每场战斗结束后检查血蓝条回满，并立即检查巫医对话框；
    非战斗时周期性检查巫医对话框（召唤兽受伤弹出的医治对话框）。
    组号模式：队长点完自动后遍历队员 Tab 点自动（_member_auto_pass_hook）；
    任务线程发起 pass 期间（tab_group.pass_active）跳过本轮全部处理——
    屏幕上可能是队员 Tab，误处理会点错账号。
    """
    global _watch_in_battle
    log("[战斗] 战斗监控已启动", "SYS")
    battle_no = 0          # 本场次计数（第 1 场按配置操作）
    in_battle = False      # 已确认的战斗状态
    in_polls = 0           # 连续在战斗的轮询数（进入确认）
    out_polls = 0          # 连续不在战斗的轮询数（结束确认）
    pending_auto = False   # 首场结束仍未挂上自动 → 下一场进场补点一次
    last_wuyi_check = 0.0  # 上次巫医对话框检查时间
    wuyi_click_at = 0.0    # 上次点击巫医选项时间（冷却）
    last_battle_end_at = 0.0  # 上次战斗结束时间（巫医弹窗高发窗口起点）
    last_hezi_check = 0.0  # 上次修罗盒子检查时间
    hezi_click_at = 0.0    # 上次点击「我需要」时间（冷却）
    while True:
        try:
            if tab_group.pass_active:
                # 任务线程的 pass 进行中：跳过本轮处理（屏幕是队员 Tab）
                time.sleep(WATCH_POLL_INTERVAL)
                continue
            if is_in_battle(win):
                out_polls = 0
                if not in_battle:
                    in_polls += 1
                    if in_polls >= BATTLE_CONFIRM_POLLS:  # 连续两轮确认进入
                        in_battle = True
                        _watch_in_battle = True  # 任务侧共享状态
                        battle_no += 1
                        debug_overlay.set_battle(battle_no)  # 调试悬浮层场次
                        log(f"[战斗] 第 {battle_no} 场战斗开始", "SYS")
                        if battle_no == 1:
                            result = handle_battle(win)  # 首场：按配置操作后点击自动
                            pending_auto = (result != "auto_ok")
                            if pending_auto:
                                log("[战斗] 首场结束未挂上自动，下一场进场补点一次", "WARN")
                            else:
                                _member_auto_pass_hook(win, wait_auto=True)
                        else:
                            # 自动使用法门：每场战斗第 1 回合点一次
                            # （首场由 handle_battle 内处理）
                            _click_famen(win)
                            if battle_no % AUTO_REASSERT_EVERY == 1:
                                if _click_auto_once(win):  # 每 5 场：点一次自动
                                    _member_auto_pass_hook(win, wait_auto=False)
                            elif pending_auto:
                                if _click_auto_once(win):  # 首场未挂上自动：本场进场补点
                                    _member_auto_pass_hook(win, wait_auto=True)
                                pending_auto = False
                        # 其余场次：不操作，等待游戏自动战斗结束
            else:
                in_polls = 0
                if in_battle:
                    out_polls += 1
                    # 结束确认保持 2s 窗口（0.5s×4 轮询，与原先 1s×2 相同，
                    # 防回合间隙误判结束——退出判定不放宽）
                    if out_polls >= BATTLE_END_CONFIRM_POLLS:
                        in_battle = False
                        _watch_in_battle = False  # 任务侧共享状态
                        log(f"[战斗] 第 {battle_no} 场战斗结束", "SYS")
                        # 战后补血蓝受通用设置开关门控（默认开）：关闭时
                        # 不检测血蓝条、不自动右键回满
                        if load_auto_refill():
                            status_bars.check_all_bars(win)
                            debug_overlay.set_bars(status_bars.last_summary())  # 悬浮层血蓝摘要
                        # 战后立即检查：先补血蓝（上一步），再巫医对话框/修罗盒子
                        last_wuyi_check = 0.0
                        last_battle_end_at = time.time()  # 巫医弹窗高发窗口起点
                        last_hezi_check = 0.0
                now = time.time()
                # 巫医检查节奏：战斗结束后高发窗口内 2s（5s 教训：弹窗等
                # 久人物走远即关闭），主流程点 NPC 后预期窗口内同样 2s，
                # 窗口外 10s 兜底（弹窗仅在战斗结束/巫医 NPC 对话出现，
                # NPC 场景人物静止无走远关闭问题）
                wuyi_interval = _wuyi_check_interval(now, last_battle_end_at,
                                                     wuyi_expected_at)
                if now - last_wuyi_check >= wuyi_interval:
                    last_wuyi_check = now
                    if now - wuyi_click_at >= WUYI_RETRY_COOLDOWN \
                            and _handle_wuyi_dialog(win):
                        wuyi_click_at = now
                if now - last_hezi_check >= XIULUO_HEZI_CHECK_INTERVAL:
                    last_hezi_check = now
                    if now - hezi_click_at >= XIULUO_HEZI_RETRY_COOLDOWN \
                            and _handle_hezi(win):
                        hezi_click_at = now
        except UserAbort as e:
            log(f"[战斗] {e}，战斗监控退出", "WARN")
            break
        except Exception as e:
            log(f"[战斗] 监控异常: {e}", "WARN")
        time.sleep(WATCH_POLL_INTERVAL)
