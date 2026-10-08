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
   1.2 到达后持续监控队伍人数（通用设置-修罗设置，仅队长生效）：
       满足最少人数才领取（本步不检查种族）；不满足 10 秒后重查，
       人留在领取点；队长被转移则转 1.0 非队长模式
   1.3 识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
       → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
       （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框，本轮领取失败
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
import time
from pathlib import Path

import cv2
import numpy as np
from ascript.windows.system import R  # 资源路由（模板路径）
from ascript.windows.hardware.keyboard import Keyboard  # Alt+C 飞行/刷新（领取验证兜底）

from .app_utils import human_click, human_move, log
from . import task_tracker  # 任务追踪面板操作
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import team  # 组队检查（领取前队伍人数检查，仅队长生效）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from . import bag  # 背包操作（打开/关闭背包、任务储物格）
from . import status_bars  # 血蓝检测（自动巫医前置条件）
from .battle import is_in_battle, is_in_battle_frame  # 战斗状态判定（统一在 battle.py 中实现）
from .battle import WUYI_DIALOG_TEXTS  # 巫医对话框文案（自动巫医等待判定）
from .control_window import (load_task_counts, load_xiuluo_settings,
                             load_xiuluo_check_team,
                             load_xiuluo_auto_wuyi,
                             load_xiuluo_wuyi_interval)  # 任务次数上限/修罗设置/自动巫医
from .xuanshu import (follow_task,  # 任务跟踪（绿字点击寻路/战斗跳过/附加对话处理）
                      _dialog_text_present)  # 对话框文案检测（复用玄枢轶闻）

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
CLAIM_NEAR_TOLERANCE = 15                     # 已在领取点附近判定容差（用户步 1.1）
WALK_ARRIVE_TOLERANCE = 5                     # 寻路到达判定容差（用户步 1.2，更严格：
                                              # 走动中识图点击容易点不到）
LINGSHOU_MAP_POS = (627, 396)                 # 灵兽村小地图上领取点位置的点击点（窗口相对坐标，用户实测）
CLAIM_DIALOG_OPTION = "闲来无事，要我帮忙吗"    # 领取对话框绿色选项（游戏内原文，实测用字）
CLAIM_OPTION_TAIL = "要我帮忙吗"               # 选项换行渲染时的尾部匹配串（实测完整文案
                                              # 永不单行命中，两次全靠尾部/兜底）
CLAIM_COUNT_TEXT = "今日已完成次数"            # 领取后对话框中的完成次数文案（辅助记录完成次数）
TEAM_WARNING_TEXT = "修罗的战斗力异常强大，千万要找齐伙伴再一同消灭。"  # 队伍数量小于 3 的提示（实测文案）
SCREEN_CENTER_POS = (689, 415)                # 屏幕中间（游戏窗口中心，同 kezhan/xuanshu，点击关闭/推进对话框）
CLAIM_ARRIVE_TIMEOUT = 90                     # 寻路到领取点的超时
CLAIM_BUTTON_TIMEOUT = 20                     # 到达后等待领取任务按钮出现的超时
CLAIM_DIALOG_TIMEOUT = 15                     # 领取对话框处理超时
CLAIM_DIALOG_APPEAR_TIMEOUT = 4               # 点击领取入口后等待对话框出现的短窗口
                                              # （正常 ~1.5s 即出）：超时未见选项判定
                                              # 坐标不对，走小地图重定位兜底

# ---- 自动巫医（用户流程：领取后每 N 轮一次，召唤兽血蓝低于阈值时医治） ----
WUYI_NPC_ICON = "xiuluo_lingshoucunwuyi.png"  # 巫医 NPC（点击打开医治对话框）
WUYI_SPOT = (115, 72)                         # 巫医 NPC 位置（用户实测）
WUYI_MAP_POS = (610, 442)                     # 小地图上巫医位置的点击点（窗口相对坐标，用户实测）
WUYI_NEAR_TOLERANCE = 11                       # 巫医附近的判定容差（严格：小距离移动，
                                              # 宽松容差会漏判偏移——用户确认）
WUYI_ARRIVE_TIMEOUT = 60                      # 寻路到巫医处的超时
WUYI_HANDLE_TIMEOUT = 15                      # 点击 NPC 后等待战斗线程处理对话框的超时
CLAIM_COUNT_TIMEOUT = 1.2                     # 等待完成次数对话框出现的超时（尽力而为）：
                                              # 实测该对话框从未出现，长窗口每次领取固定空转
CLAIM_VERIFY_WAIT = 5                         # 领取后任务进入追踪的正常轮询时长（秒）：
                                              # 超时未检索到 → 按一次 Alt+C 飞行后仅再检索一次，
                                              # 仍无即判定失败（用户确认：多次重试无效）

# ---- 领取前队伍检查 ----
TEAM_CHECK_RETRY_INTERVAL = 10  # 队伍人数不足时的重试间隔（秒）
TEAM_RECOG_RETRY_INTERVAL = 3   # 成员识别失败（OCR 噪声）时的重试间隔（秒）：
                                # 识别失败≠人数不足，等满 10s 纯属空转

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

# ---- 战斗（战斗操作由 battle.watch_loop 线程负责） ----
BATTLE_END_POLL = 2.0            # 等待战斗结束的轮询间隔
BATTLE_END_TIMEOUT = 600         # 等待战斗结束的超时兜底

# ---- 返程（用户流程：背包任务储物格 → 修罗回城符右键 → 回灵兽村） ----
BOX_TASK_ICON = "box_task.png"                    # 背包任务储物格按钮（未选中态，点击打开）
BOX_TASK_SELECTED_ICON = "box_task_selected.png"  # 任务储物格已打开（选中态，无需再点击）
XIULUO_RETURN_ICON = "xiuluo_huichengfu.png"      # 修罗回城符模板（TODO: 待用户提供）
TASK_BOX_THRESHOLD = 0.8         # 储物格按钮双模板判定阈值（与其他模板一致）
TASK_BOX_TIMEOUT = 10            # 等待任务储物格打开的超时（点击后渲染/切换）
RETURN_TALISMAN_TIMEOUT = 15     # 等待回城符在任务储物格中出现的超时
RETURN_POLL_INTERVAL = 0.5       # 返程轮询间隔（打开动画约 0.4s，实测点击后 1s 内
                                 # 出现选中态；非防抖判定点，无需更长窗口）
VERIFY_HOVER_SECONDS = 2.0       # 验证模式：鼠标悬停在回城符上等待关闭背包的秒数
# 任务储物格按钮相对背包面板标志（bag_box 中心）的偏移与搜索条带：
# 面板可拖动但内部布局固定，偏移恒定（面板左缘/中部两位置实测
# +349/+305 与 +353/+305，取均值）；条带留 55px 余量，带区全未命中
# 才全帧兜底（限频：仅带区未命中时触发）
TASK_BOX_OFFSET = (351, 305)
TASK_BOX_BAND_MARGIN = 55

# 骨架阶段占位标记：流程函数未实现时返回该描述，主循环据此退出避免空转；
# 实现逐个填充后该分支自然不再触发
STUB_INFO = "流程待实现"

# 领取后对话框「今日已完成次数：N」的完成次数解析（OCR 可能把冒号误读，兼容）
_CLAIM_COUNT_RE = re.compile(re.escape(CLAIM_COUNT_TEXT) + r"\s*[:：]?\s*(\d+)")


def _near_wuyi_spot(pos):
    """判断位置是否在巫医 NPC (115,72) 附近（严格容差 ±5）。

    小距离移动场景：附近定义必须严格，宽松容差会漏判偏移（用户确认）。
    """
    if pos is None:
        return False
    return (pos[0].strip() == LINGSHOUCUN
            and abs(pos[1] - WUYI_SPOT[0]) <= WUYI_NEAR_TOLERANCE
            and abs(pos[2] - WUYI_SPOT[1]) <= WUYI_NEAR_TOLERANCE)


def _run_wuyi_chain(win, hover_before_click=False, skip_condition=False):
    """自动巫医主链路（自动流程与验证共用）：召唤兽血/蓝低于阈值时医治。

    1. 巫医条件：召唤兽红条/蓝条低于阈值（通用设置血蓝阈值；空条=
       未出战，无需医治）——不满足直接返回成功（无需处理）；
       skip_condition=True 时跳过门槛（验证模式：血蓝状态仅报告）
    2. 位置检查：是否在灵兽村 (115,72) 附近（严格容差 ±5）
    3. 不在 → 打开小地图点击 (610,442) 寻路 → 关闭地图（顺带背包）
    4. 点击巫医 NPC（xiuluo_lingshoucunwuyi.png）打开医治对话框，
       等待战斗线程点击「全部医治和修复」（battle.py 已有完整逻辑，
       无需重复实现——对话框出现→消失即处理完成）；
       hover_before_click=True 时 NPC 点击前悬停 2s（验证观察用）

    到达/点击的最终真信号是对话框出现：坐标 OCR 不可靠（实测走完
    站在 NPC 旁位置显示缺失，坐标判定卡死到达循环），NPC 模板远距离
    也会命中（使者教训）——点击后对话框未出现判定距离不够，小地图
    再走一次重试（与领取入口同款兜底）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过自动巫医"
    # 1. 巫医条件（只读检查，不触发右键回满）
    low = status_bars.beast_below_threshold(win)
    if not skip_condition and not low:
        return True, "召唤兽血蓝正常，无需巫医"
    log(f"[修罗][巫医] 召唤兽血蓝"
        f"{'低于阈值，执行自动巫医' if low else '正常（验证模式不要求条件满足）'}", "SYS")
    # 2. 位置检查（严格容差：小距离移动宽松容差会漏判偏移；坐标
    #    读不到按不在处理——走去巫医无害）
    pos = game_map.read_map_position(win)
    if pos is None:
        pos = game_map.read_position_with_recovery(win)
    need_walk = not _near_wuyi_spot(pos)
    # 3~5. 走位 → NPC 点击 → 对话框；对话框未出现判定距离不够，
    #      小地图再走一次重试（最多两次点击尝试）
    for attempt in (1, 2):
        if attempt == 2 or need_walk:
            ok, info = game_map.open_mini_map(win)
            if not ok:
                return False, info
            human_click(win, WUYI_MAP_POS[0], WUYI_MAP_POS[1])
            log("[修罗][巫医] 已点击小地图巫医位置，自动寻路中", "SYS")
            time.sleep(1.0)
            game_map.close_maps(win)
        # 等待 NPC 模板可见（到达直接信号，不依赖坐标）
        pt = None
        deadline = time.time() + WUYI_ARRIVE_TIMEOUT
        while time.time() < deadline:
            if is_in_battle(win):
                time.sleep(2.0)
                continue
            pt = game_map.find_template(win, WUYI_NPC_ICON)
            if pt is not None:
                break
            time.sleep(2.0)
        if pt is None:
            if attempt == 1:
                log("[修罗][巫医] 未检测到巫医 NPC，小地图寻路后重试", "WARN")
                need_walk = True
                continue
            return False, f"未找到巫医 NPC（待补充 {WUYI_NPC_ICON} 模板或位置有误）"
        if hover_before_click:
            human_move(win.rect.left + pt[0], win.rect.top + pt[1])
            log(f"[修罗][验证] 已移动鼠标到巫医 NPC ({pt[0]}, {pt[1]})，"
                f"悬停 {VERIFY_HOVER_SECONDS:.0f} 秒...", "SYS")
            time.sleep(VERIFY_HOVER_SECONDS)
        human_click(win, pt[0], pt[1])
        log(f"[修罗][巫医] 已点击巫医 NPC ({pt[0]}, {pt[1]})，等待医治对话框处理", "SYS")
        # 等待对话框出现并处理（出现→消失）
        deadline = time.time() + WUYI_HANDLE_TIMEOUT
        appeared = False
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.5)
                continue
            items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            # 与 battle.py 的巫医判定同款调用（内部模糊匹配，无需预规范化）
            has_dialog = any(_dialog_text_present(items, t) for t in WUYI_DIALOG_TEXTS)
            if has_dialog:
                appeared = True
                time.sleep(1.0)
                continue
            if appeared:
                log("[修罗][巫医] 医治对话框已处理完毕", "SYS")
                return True, "自动巫医完成"
            time.sleep(0.5)
        if appeared:
            return True, "医治对话框已出现（战斗线程将继续处理）"
        # 对话框未出现：距离不够 → 走一次再重试
        log("[修罗][巫医] 点击后未出现对话框（距离不够），小地图重定位后重试", "WARN")
        need_walk = True
    return False, "点击巫医后未出现医治对话框（重定位后仍无）"


def _auto_wuyi(win):
    """自动巫医（用户流程，通用设置开关+间隔门控，见 _run_wuyi_chain）。"""
    return _run_wuyi_chain(win)


def verify_wuyi_flow(win):
    """验证自动巫医流程（高级设置-验证按钮，主进程调用）。

    与 _auto_wuyi 同一链路，区别：条件不满足也继续执行（血蓝状态仅
    报告），NPC 点击前悬停 2s 供人工确认。返回 (是否成功, 描述)。
    """
    return _run_wuyi_chain(win, hover_before_click=True, skip_condition=True)


def _near_claim_spot(pos, tolerance):
    """判断位置是否在灵兽村领取点附近（地名精确匹配，容差抗 OCR 噪声）。"""
    if pos is None:
        return False
    return (pos[0].strip() == LINGSHOUCUN
            and abs(pos[1] - CLAIM_SPOT[0]) <= tolerance
            and abs(pos[2] - CLAIM_SPOT[1]) <= tolerance)


def _find_claim_entry(win):
    """查找领取入口（领取任务按钮或灵兽村使者），返回 (pt, 来源) 或 (None, None)。

    两个入口识别到任一即可点击领取（用户确认：使者 NPC 也可点击）。
    """
    for icon, label in ((LINGQU_ICON, "领取任务按钮"),
                        (LINGSHOUCUN_SHIZHE_ICON, "灵兽村使者")):
        pt = game_map.find_template(win, icon)
        if pt is not None:
            return pt, label
    return None, None


def _wait_claim_entry(win, timeout):
    """等待领取入口出现（按钮或使者），返回 (pt, 来源) 或 (None, None)。

    战斗中跳过等待；每轮询只截一帧，双模板批量匹配共用一帧。
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
               _norm_text(CLAIM_OPTION_TAIL),             # 尾部（换行渲染时实测命中）
               _norm_text("帮忙"))                        # 最后兜底
    for needle in needles:
        for txt, rect, score in items:
            if score >= 0.55 and needle in re.sub(r"[^\w]", "", txt) \
                    and rect[0] >= task_tracker.PANEL_MAX_X \
                    and task_tracker.is_green_text(img_np, rect):
                cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
                return cx, cy, txt, needle == _norm_text("帮忙")
    return None


def _click_claim_option(win, items, img_np):
    """点击领取对话框中的绿色选项「闲来无事，要我帮忙吗」，成功返回 True。"""
    found = _find_claim_option(items, img_np)
    if found is None:
        return False
    cx, cy, txt, fallback = found
    human_click(win, cx, cy)
    suffix = "（兜底匹配）" if fallback else ""
    log(f"[修罗] 已点击绿色选项「{txt}」{suffix}", "SYS")
    return True


def _norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做模糊匹配可大幅降低漏配。
    """
    return re.sub(r"[^\w]", "", s)


def _handle_team_warning(win, items, img_np):
    """对话框出现「修罗的战斗力异常强大…」：队伍数量小于 3，
    点击屏幕中间一次关闭对话框。返回是否已处理。
    """
    norm_items = [(_norm_text(t), r, s) for t, r, s in items]
    if not _dialog_text_present(norm_items, _norm_text(TEAM_WARNING_TEXT)):
        return False
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    log("[修罗] 队伍数量小于 3，已点击屏幕中间关闭提示对话框", "WARN")
    return True


def _read_claim_count(win, timeout=CLAIM_COUNT_TIMEOUT):
    """领取后对话框「今日已完成次数：N」中解析已完成次数；失败返回 None。

    辅助信息尽力而为：限对话框区域 OCR，短窗口（1.2s，实测该对话框
    多轮从未出现，长窗口每次领取固定空转——「已点选项→已领取」间隔的
    构成之一）。全部 OCR 行拼接后正则提取（行拆分/冒号误读均兼容）。
    """
    deadline = time.time() + timeout
    while True:
        img = win.capture(force_activate=False)
        if img is not None:
            items, _ = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            raw = re.sub(r"\s", "", "".join(t for t, _, s in items if s >= 0.55))
            m = _CLAIM_COUNT_RE.search(raw)
            if m:
                return int(m.group(1))
        if time.time() > deadline:
            return None
        time.sleep(0.4)


def _walk_to_claim_spot(win):
    """寻路到灵兽村领取点附近（与队伍人数无关，队长身份下执行）。

    用户流程：已在领取点 110,90 附近（±15）→ 跳过寻路；
    不在灵兽村 → 世界地图点击灵兽村图标 → 小地图点击 (603,392) 寻路；
    在灵兽村但不在领取点附近 → 直接打开小地图点击 (603,392) 寻路。
    等待到达 110,90（±5 严格容差：走动中识图点击容易点不到）。

    到达判定优先用直接信号（领取任务按钮可见 = 已到领取点）——
    位置 OCR 坐标实测不可靠：区域快引擎把 111,90 误读为
    11,90/102,79/18,14，玩家站在领取点却被判定未到达、空等 30s+
    并误走小地图导航。位置读取失败（传送后加载画面遮挡）时先等待
    恢复（最多 8s），不立即误走导航；等待期间不按 Alt+C（该键是
    位置显示开关，加载期误按会关掉显示）。
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    # 直接信号：领取按钮可见 = 已在领取点附近（免位置 OCR，规避坐标
    # 误读）。注意到达判据只认按钮——使者模板远距离也会命中（实测
    # 距离过远点击不出现对话框），按钮只在可点击范围内出现
    if game_map.find_template(win, LINGQU_ICON) is not None:
        return True, "已在领取点附近（领取按钮可见）"
    # 位置读取：失败时等待传送加载恢复（最多 8s，期间不按 Alt+C）
    pos = game_map.read_map_position(win)
    if pos is None:
        deadline = time.time() + 8
        while pos is None and time.time() < deadline:
            time.sleep(2.0)
            pos = game_map.read_map_position(win)
        if pos is None:
            pos = game_map.read_position_with_recovery(win)  # 最后按一次 Alt+C 恢复
    if _near_claim_spot(pos, CLAIM_NEAR_TOLERANCE):
        return True, "已在领取点附近"
    if pos is None or pos[0].strip() != LINGSHOUCUN:
        # 不在灵兽村：世界地图点击灵兽村图标，打开灵兽村小地图
        ok, info = game_map.open_mini_map_city(win, WORLD_MAP_LINGSHOUCUN_ICON,
                                               LINGSHOUCUN)
        if not ok:
            return False, info
    else:
        # 在灵兽村但不在领取点附近：直接打开小地图
        ok, info = game_map.open_mini_map(win)
        if not ok:
            return False, info
    human_click(win, LINGSHOU_MAP_POS[0], LINGSHOU_MAP_POS[1])
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
        if _near_claim_spot(pos, WALK_ARRIVE_TOLERANCE):
            log("[修罗] 已到达领取点附近", "SYS")
            return True, "已到达领取点附近"
        polls += 1
        if polls % 5 == 0:
            log(f"[修罗] 前往领取点中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"寻路到领取点超时（未到达灵兽村 {CLAIM_SPOT[0]},{CLAIM_SPOT[1]} 附近）"


def _click_entry_ensuring_dialog(win, log_tag="[修罗]", hover_before_click=False):
    """点击领取入口（按钮或使者）并确保对话框出现；未出现判定坐标不对：
    打开小地图点击领取点坐标重定位后重试一次（用户确认的兜底路径）。

    hover_before_click：点击前悬停入口（验证模式观察用）。
    返回 (是否成功, 描述, 来源, 坐标)；成功时对话框已打开（选项可见）。
    """
    dialog_seen = False
    appear_timeout = CLAIM_DIALOG_APPEAR_TIMEOUT
    src2 = pt2 = None
    for attempt in (1, 2):
        pt, src = _wait_claim_entry(win, CLAIM_BUTTON_TIMEOUT)
        if pt is None:
            return False, "到达后未出现领取任务按钮/灵兽村使者", None, None
        pt2, src2 = _find_claim_entry(win)
        if pt2 is None:
            return False, "领取入口出现后又消失，未点击", None, None
        if hover_before_click:
            human_move(win.rect.left + pt2[0], win.rect.top + pt2[1])
            log(f"{log_tag} 已移动鼠标到{src2} ({pt2[0]}, {pt2[1]})，"
                f"悬停 {VERIFY_HOVER_SECONDS:.0f} 秒...", "SYS")
            time.sleep(VERIFY_HOVER_SECONDS)
        human_click(win, pt2[0], pt2[1])
        log(f"{log_tag} 已点击{src2}（领取）", "SYS")
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
                return False, "队伍数量小于 3，无法领取（已关闭提示对话框）", None, None
            if _find_claim_option(items, img_np) is not None:
                dialog_seen = True
                break
            time.sleep(0.3)
        if dialog_seen:
            break
        if attempt == 1:
            # 对话框未出现：坐标不对 → 打开小地图点击领取点坐标重定位
            log(f"{log_tag} 点击后未出现对话框（坐标不对），打开小地图重定位", "WARN")
            ok, info = game_map.open_mini_map(win)
            if not ok:
                return False, info, None, None
            human_click(win, LINGSHOU_MAP_POS[0], LINGSHOU_MAP_POS[1])
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
        return False, "点击领取入口后对话框始终未出现（小地图重定位后仍无）", src2, pt2
    return True, f"已点击{src2}", src2, pt2


def _claim_task(win):
    """在领取点附近领取修罗任务（前提：已走到领取点附近）。返回 (是否成功, 描述)。

    识图 liuluo_lingqu.png 点击 → 对话框点绿色选项「闲来无事，要我帮忙吗」
    → 校验任务出现在追踪面板；若出现「修罗的战斗力异常强大…」提示
    （队伍数量小于 3）→ 点击屏幕中间一次关闭对话框，本轮领取失败。
    领取后对话框「今日已完成次数：N」解析已完成次数（辅助完成次数记录）。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮领取"
    # 识图领取入口（按钮或灵兽村使者）并点击；点击后对话框未出现
    # （短窗口内选项不可见）判定坐标不对：打开小地图点击领取点坐标
    # 重定位后重试一次（用户确认的兜底路径）
    ok, info, src2, pt2 = _click_entry_ensuring_dialog(win)
    if not ok:
        return False, info
    # 对话框：点绿色选项「闲来无事，要我帮忙吗」完成领取
    # 每轮询：截图 1 次 + 限对话框区域 OCR（全帧 ~930ms → 区域 ~100-170ms，
    # 绿色选项点击依赖的全图坐标/像素判定由 ocr_regions 全图黑底 img_np
    # 保证）；完整文案与「帮忙」兜底在同一帧先后尝试——OCR 把选项拆成
    # 两行时完整串永远匹配不上（实测主循环空转 15s 后才进兜底点中，
    # 领取按钮→选项点击 14~17s），合并后首帧可读即点中。
    # has_task（追踪区 OCR ~0.9s）限频到每 4 轮——对话框出现前
    # 任务不可能已进追踪（领取后验证循环兜底），每轮都查纯属空转
    deadline = time.time() + CLAIM_DIALOG_TIMEOUT
    claimed = False
    polls = 0
    while time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is None:
            time.sleep(0.3)
            continue
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if _handle_team_warning(win, items, img_np):
            return False, "队伍数量小于 3，无法领取（已关闭提示对话框）"
        if _click_claim_option(win, items, img_np):
            claimed = True
            break
        polls += 1
        if polls % 4 == 0 and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
            claimed = True
            break
        time.sleep(0.3)
    if not claimed:
        return False, "领取对话框处理失败"
    # 1.5 领取后对话框「今日已完成次数：N」：解析已完成次数（辅助完成次数记录）
    count = _read_claim_count(win)
    if count is not None:
        log(f"[修罗] {CLAIM_COUNT_TEXT}：{count}", "SYS")
    # 校验任务出现在追踪面板：正常轮询 CLAIM_VERIFY_WAIT 秒；未出现则
    # 按一次 Alt+C（飞行/刷新界面状态）后仅再检索一次——用户确认多次
    # 重试无效，Alt+C 后仍检索不到即判定领取失败，不再长窗空等。
    # 期间处理队伍不足提示对话框（点击选项后队伍掉到 3 人以下时出现）
    deadline = time.time() + CLAIM_VERIFY_WAIT
    while not task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS) \
            and time.time() < deadline:
        img = win.capture(force_activate=False)
        if img is not None:
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _handle_team_warning(win, items, img_np):
                return False, "队伍数量小于 3，无法领取（已关闭提示对话框）"
        time.sleep(1.0)
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}"
    # 未检索到：按 Alt+C 飞行后再次检索（仅一次，多次重试无效）
    Keyboard.key_hot("alt", "c")
    log("[修罗] 任务追踪未检索到任务，按 Alt+C 飞行后再次检索", "WARN")
    time.sleep(1.0)
    if task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
        log("[修罗] Alt+C 飞行后任务已出现在追踪面板", "SYS")
        tail = f"（{CLAIM_COUNT_TEXT}：{count}）" if count is not None else ""
        return True, f"修罗任务已领取{tail}"
    # Alt+C 后仍无：不再重试，直接失败（下一轮 has_task 检查兜底，
    # 任务若随后渲染出现会以续跑方式被拾起）
    try:
        titles = [t["title"] for t in task_tracker.read_tasks(win)]
        log(f"[修罗] 验证失败，追踪面板当前标题: "
            f"{' / '.join(titles) if titles else '(无标题行)'}", "WARN")
    except Exception:
        pass
    return False, "已点击领取选项，但任务追踪中未检测到任务（Alt+C 飞行后仍无）"


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


def _handle_fight_dialog(win, items, img_np):
    """对话开打：跟踪期间出现对话 → 点击绿色选项「看打！」进入战斗。

    点击成功即标记本轮战斗由「看打！」进入（战后据此决定是否返程）。
    OCR 对标点误读常见，去标点后按子串匹配；限定对话框区域
    （x≥PANEL_MAX_X），避免误点追踪面板的绿字链接。
    返回是否已处理。
    """
    global _fight_via_kanda
    needle = _norm_text(FIGHT_DIALOG_OPTION)
    for txt, rect, score in items:
        if score >= 0.55 and needle in _norm_text(txt) \
                and rect[0] >= task_tracker.PANEL_MAX_X \
                and task_tracker.is_green_text(img_np, rect):
            cx, cy = task_tracker.green_text_center(img_np, rect, prefer_underline=False)
            human_click(win, cx, cy)
            log(f"[修罗] 已点击「看打！」进入战斗", "SYS")
            _fight_via_kanda = True
            return True
    return False


def _follow_to_done(win):
    """寻路 + 对话开打 + 战斗：跟踪任务直到本轮完成。

    复用 xuanshu.follow_task：绿字点击自动寻路、对话开打走附加对话处理
    （点击「看打！」进入战斗）、战斗由战斗监控线程操作且跟踪中自动跳过。
    返回 (是否成功, 描述)。
    """
    ok, info = follow_task(win, TASK_TITLE, prefix="修罗",
                           extra_dialog_handler=_handle_fight_dialog,
                           fallback_keywords=TASK_FALLBACK_TEXTS)
    return ok, info


def _wait_battle_end(win, timeout=BATTLE_END_TIMEOUT):
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
    human_click(win, pt[0], pt[1], button="secondary")
    log(f"[修罗] 已右键使用修罗回城符 ({pt[0]}, {pt[1]})，返回灵兽村", "SYS")
    time.sleep(1.0)  # 等待传送生效
    return True, "已右键使用修罗回城符"


def _close_return_panels(win):
    """返程收尾：关闭世界地图、小地图、背包（尽力而为，失败仅记日志）。

    先截一帧批量检测三项是否全未打开（常见场景：回城符使用后界面已
    自行关闭）：全未打开直接返回，1 次截图替代 4~6 次带截图的逐项检测
    （实测逐项 ~110ms/两项 + 关包检测 ~140ms）；任一打开才走既有关闭流程。
    """
    img = win.capture(force_activate=False)
    if img is not None:
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        found = game_map.match_frame_multi(
            frame, (game_map.WORLD_MAP_OPEN_ICON,
                    game_map.MINI_MAP_OPEN_ICON,
                    bag.BAG_BOX_ICON))
        if not any(found.values()):
            log("[修罗] 世界地图/小地图/背包均未打开，无需关闭", "INFO")
            return
    game_map.close_maps(win)
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
    # 且失败后下一轮领取会误走世界地图寻路兜底）
    deadline = time.time() + 5
    pos = None
    while time.time() < deadline:
        pos = game_map.read_map_position(win)
        if pos is not None:
            break
        time.sleep(1.0)
    if pos is not None:
        log(f"[修罗] 返程后位置: {pos[0]} {pos[1]},{pos[2]}", "SYS")
    else:
        log("[修罗] 返程后位置识别失败（不影响本轮返程）", "INFO")
    return True, "已返回灵兽村"


def verify_return_flow(win):
    """验证修罗回程流程（高级设置-验证按钮，主进程调用）。

    与实际返程 _return_to_npc 完全一致，唯一区别是不右键使用回城符：
    仅将鼠标移动到回城符上悬停 VERIFY_HOVER_SECONDS 秒供人工确认，
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
    # 2. 识别修罗回城符：仅移动鼠标悬停，不点击
    pt, info = _find_return_talisman(win)
    if pt is None:
        _close_return_panels(win)
        return False, info
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    log(f"[修罗][验证] 已移动鼠标到修罗回城符 ({pt[0]}, {pt[1]})，"
        f"悬停 {VERIFY_HOVER_SECONDS:.0f} 秒（不点击）...", "SYS")
    time.sleep(VERIFY_HOVER_SECONDS)
    # 3. 关闭世界地图、小地图、背包
    _close_return_panels(win)
    return True, f"验证通过：回城符位于 ({pt[0]}, {pt[1]})"


def verify_claim_flow(win):
    """验证修罗领取流程（高级设置-验证按钮，主进程调用）。

    与领取流程一致，但在「点击绿色选项完成领取」前停止：走位 → 领取
    按钮识别 → 点击按钮打开对话框（领取发生在点选项之后，开对话框
    不改变任务状态）→ 识别绿色选项（完整/尾部/帮忙）→ 仅悬停
    VERIFY_HOVER_SECONDS 秒不点击 → 点击屏幕中间关闭对话框。
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
    # 2. 领取入口：悬停 2s → 点击打开对话框；点击后对话框未出现则
    #    小地图重定位后重试（与领取流程共用同一兜底助手）
    ok, info, src2, pt2 = _click_entry_ensuring_dialog(
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
                f"悬停 {VERIFY_HOVER_SECONDS:.0f} 秒（不点击）...", "SYS")
            time.sleep(VERIFY_HOVER_SECONDS)
            # 4. 点击屏幕中间关闭对话框（不领取）
            human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
            log("[修罗][验证] 已点击屏幕中间关闭对话框（未领取）", "SYS")
            return True, f"验证通过：{src2} ({pt2[0]}, {pt2[1]})，选项「{txt}」({cx}, {cy})"
        time.sleep(0.3)
    # 对话框未出现或选项未识别：点击屏幕中间清理后失败
    human_click(win, SCREEN_CENTER_POS[0], SCREEN_CENTER_POS[1])
    return False, "对话框选项未识别（未领取，已尝试关闭对话框）"


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
    2. 队长先走到领取点附近（队伍是否满都先走位）
    3. 首次领取前到达后持续监控队伍人数：满足最少人数才领取，
    不满足 10 秒重查（人留在领取点，只开队伍面板检查）
    4. 队伍检查仅首次领取前执行一次（_team_checked）：首次领取成功后
    后续轮次直接走位领取，不再开面板（每轮省 5~7s）；领取失败时
    补查一次队伍状态，队长被转移则转非队长保持模式

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取时出现每日次数已满（TODO: 具体文案待确认）
    返回结束原因描述。

    骨架阶段：流程函数返回 STUB_INFO（未实现）时直接结束任务，避免空转。
    """
    global _fight_via_kanda, _team_checked, _task_gone_last_round
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[修罗] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    # 检查队伍开关（通用设置-修罗设置，默认关闭）：关闭时领取前跳过
    # 全部队伍检查（非队长判定/人数监控），直接走位领取
    check_team = load_xiuluo_check_team()
    # 自动巫医（通用设置开关，默认开、每 5 轮一次）：领取任务后召唤兽
    # 血蓝低于阈值时前往巫医 NPC 医治（对话框处理复用战斗线程逻辑）
    auto_wuyi = load_xiuluo_auto_wuyi()
    wuyi_interval = max(1, load_xiuluo_wuyi_interval())
    log(f"[修罗] 自动巫医: {'开' if auto_wuyi else '关'}"
        + (f"（每 {wuyi_interval} 轮）" if auto_wuyi else ""), "SYS")
    log(f"[修罗] 检查队伍: {'开（领取前检查队伍）' if check_team else '关（跳过队伍检查，直接领取）'}", "SYS")

    done = 0
    round_no = 0
    while True:
        round_no += 1
        log(f"[修罗] ==== 第 {round_no} 轮 ====", "SYS")
        # 1. 领任务：任务追踪中已有修罗任务（续跑）→ 跳过领取，直接跟踪。
        #    上一轮已确认任务完成（follow_task 以任务消失为完成判据）时，
        #    本轮开始必然无任务——has_task（区域 OCR ~0.9s）只在可能
        #    续跑时检查（进程首轮或上轮跟踪失败未确认完成）
        if not _task_gone_last_round and task_tracker.has_task(win, TASK_TITLE, TASK_FALLBACK_TEXTS):
            log("[修罗] 任务追踪中已有修罗任务，直接跟踪", "SYS")
        else:
            if _task_gone_last_round:
                _task_gone_last_round = False
                log("[修罗] 上一轮已确认任务完成，跳过任务检查，直接领取", "INFO")
            # 队伍检查受「检查队伍」开关门控（默认关闭=完全跳过），且仅在
            # 首次领取前执行（_team_checked 置位后跳过）：每轮回程后开面板
            # 检查耗时 5~7s，首次领取成功后不再重复
            if check_team and not _team_checked:
                # 1.0 非队长判定：已在领取点附近时跳过（返程落点就在领取点，
                #    1.2 监控循环马上会检查——先查再走位会让面板连续开合两次，
                #    一次开合实测 5~7s）；不在领取点则先判定身份，非队长不白走寻路
                at_spot = (game_map.find_template(win, LINGQU_ICON) is not None
                           or _near_claim_spot(game_map.read_map_position(win),
                                               CLAIM_NEAR_TOLERANCE))
                if not at_spot:
                    status, info = _check_team_ready(win)
                    if status == "not_leader":
                        _keep_alive(info)
            # 1.1 队长：队伍是否满都先走到领取点附近
            ok, info = _walk_to_claim_spot(win)
            if not ok:
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            if check_team and not _team_checked:
                # 1.2 首次领取：到达后持续监控队伍人数（人留在领取点）：
                #     满足最少人数才领取；队长被转移则转非队长保持模式；
                #     识别失败是 OCR 噪声，短间隔重查（不等满 10 秒）
                while True:
                    status, info = _check_team_ready(win)
                    if status == "not_leader":
                        _keep_alive(info)
                    if status == "ready":
                        log(f"[修罗] {info}", "SYS")
                        break
                    interval = (TEAM_RECOG_RETRY_INTERVAL if "识别失败" in info
                                else TEAM_CHECK_RETRY_INTERVAL)
                    log(f"[修罗] {info}，暂不领取（持续监控队伍人数）", "WARN")
                    time.sleep(interval)
            # 1.3 领取任务
            ok, info = _claim_task(win)
            if not ok:
                if info == STUB_INFO:
                    return f"修罗任务流程尚未实现（{info}），结束任务"
                if check_team and _team_checked:
                    # 已通过队伍检查仍领取失败（如队长被转移、按钮未出现）：
                    # 补一次队伍检查恢复状态感知，非队长转保持模式
                    status, info2 = _check_team_ready(win)
                    if status == "not_leader":
                        _keep_alive(info2)
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
            log(f"[修罗] {info}", "SYS")
            _team_checked = True  # 首次领取成功：后续轮次跳过队伍检查
            _fight_via_kanda = False  # 新一轮开始：重置「看打！」进入战斗标记
            # 自动巫医：每 N 轮领取后检查一次召唤兽血蓝，低于阈值则
            # 前往巫医 NPC 医治（失败仅记日志，不阻断后续寻路）
            if auto_wuyi and round_no % wuyi_interval == 0:
                ok2, info2 = _auto_wuyi(win)
                log(f"[修罗][巫医] {info2}", "SYS" if ok2 else "WARN")
        # 2~4. 寻路 / 对话开打 / 战斗：由任务跟踪统一驱动
        #     （寻路点击绿字自动进行，对话开打走附加对话处理，
        #      战斗由战斗监控线程自动操作、跟踪中自动跳过）
        ok, info = _follow_to_done(win)
        if not ok:
            if info == STUB_INFO:
                return f"修罗任务流程尚未实现（{info}），结束任务"
            log(f"[修罗] {info}", "WARN")
            time.sleep(cycle_interval)
            continue
        _task_gone_last_round = True  # 任务消失已确认：下一轮跳过 has_task 检查
        # 5. 返程：仅「看打！」进入的战斗结束后才返程（背包任务储物格
        #    右键修罗回城符回灵兽村），其他方式进入的战斗不自动返程
        if _fight_via_kanda:
            ok, info = _return_to_npc(win)
            if not ok:
                if info == STUB_INFO:
                    return f"修罗任务流程尚未实现（{info}），结束任务"
                log(f"[修罗] {info}", "WARN")
                time.sleep(cycle_interval)
                continue
        done += 1
        total = max_rounds if max_rounds > 0 else "∞"
        debug_overlay.set_progress(f"{done}/{total}")
        log(f"[修罗] 本轮修罗任务完成（{done}/{total}）", "SYS")
        if 0 < max_rounds <= done:
            return f"已完成 {done} 次修罗任务，达到次数上限 {max_rounds}"
        # 成功路径不加轮次间隔：返程流程结尾已有 5s 位置识别等待，
        # 游戏状态已稳定，下一轮直接读位置领取即可（间隔仅保留在
        # 失败重试路径，防快速失败模式紧循环空转）
