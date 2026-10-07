"""客栈任务自动化。

流程（与玄枢轶闻基本一致）：
0. 任务追踪中已有「客栈」任务（续跑）→ 直接跟踪（复用 xuanshu.follow_task）
1. 没有任务时，前往洛阳客栈领取任务：
   1.1 已在洛阳客栈 → 查找掌柜 zhanggui.png；未找到 → Alt+6 后重试一次，
       仍没有 → 终止任务
   1.2 不在洛阳客栈 → 世界地图 → 点击洛阳城 → 小地图点击 (884,600)
       - 出现「你要到洛阳客栈」对话框 → 点绿色选项「快送我去」
       - 未出现 → 等待寻路结束
   1.3 寻路结束后回到 1.1 逻辑（最多 NAV_MAX_ATTEMPTS 轮，防止死循环）
2. 点击掌柜对话 → 逐屏处理对话链直到任务进入追踪面板：
   「接取客栈任务」绿字 → 瑶池迎仙大会对话（点「广寒宫我熟悉的很，
   包在我身上！」）；跟踪期间出现该对话同样处理。
   每日次数已满时出现玄枢轶闻同款文案，结束任务循环
3. 跟踪任务直到完成 → 继续领取下一轮；达到次数上限时结束

跟踪与玄枢轶闻一致：点击任务追踪绿字下划线文案推进，战斗等待战斗线程处理
（follow_task 战斗中自动跳过识别）。特殊任务步骤「前往洛阳城大理寺墙外附近」
已实现手动寻路（世界地图 → 洛阳城 → 小地图 (620,317) → 到达 220,195 附近
→ 检查阿旺 awang.png）；提交物品/无跟踪两类特殊任务后续单独处理。
"""
import re
import time

import cv2
import numpy as np
from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R

from .app_utils import log, human_click
from . import bag  # 物品栏开关
from . import map as game_map  # 地图操作（世界地图/小地图/位置识别）
from . import task_tracker  # 任务追踪面板操作
from .battle import is_in_battle  # 战斗状态判定（统一在 battle.py 中实现）
from . import debug_overlay  # 调试信息悬浮层（任务进度更新）
from .control_window import load_task_counts  # 任务次数上限（通用设置）
from .xuanshu import (follow_task, _click_green_option_once,
                      _dialog_text_present, _fuzzy_contains,
                      DAILY_LIMIT_TEXT, DAILY_LIMIT_INFO)  # 复用玄枢轶闻的跟踪/对话框逻辑

# 任务追踪面板中的任务标题
TASK_MODE = "客栈"       # 控制面板任务模式名（任务次数配置键）
TASK_TITLE = "客栈任务"  # 追踪面板标题匹配串（如 月宫采珍【客栈任务】）
# 注意：不能只匹配「客栈」——地图位置文案 洛阳客栈[18,12] 也含「客栈」，
# 会被 track_task 误作任务锚点（实测第 12 轮跟踪失败根因）

# ---- 前往洛阳客栈找掌柜 ----
KEZHAN_MAP = "洛阳客栈"               # 位置识别地名（精确匹配）
LUOYANG_CITY = "洛阳城"               # 世界地图城市名
ZHANGGUI_ICON = "zhanggui.png"       # 掌柜模板（实测素材）
INN_MAP_CLICK_POS = (884, 600)       # 洛阳小地图上客栈位置的点击点（窗口相对坐标）
FAST_DIALOG_TEXT = "你要到洛阳客栈"    # 点击后可能出现的快送对话框文案
FAST_DIALOG_OPTION = "快送我去"       # 快送对话框绿色选项
ARRIVE_TIMEOUT = 120                  # 等待寻路完成的超时（秒）
NAV_MAX_ATTEMPTS = 3                 # 前往客栈的最多尝试轮数（防死循环）
ALT6_RETRY_WAIT = 1.5                # Alt+6 后等待画面刷新的时间（秒）
# 领取任务对话框中的绿色选项（点击掌柜后出现）
DIALOG_OPTION_LINGQU = "接取客栈任务"
DIALOG_TIMEOUT = 10                  # 等待对话框出现的超时（秒）
# 任务对话处理（对话框文案 → 点击的绿色选项，同 xuanshu.DIALOG_OPTION_MAP）：
# 对话框出现相应文案时，点击其绿色选项推进。领取对话链与跟踪期间均按此处理，
# 后续新增对话直接在此追加条目。
DIALOG_OPTION_MAP = {
    "瑶池近日要办迎仙大会，宴上那道琉璃映月羹": "广寒宫我熟悉的很，包在我身上！",
    "少侠有什么事吗": "客栈任务",
    "有空我请你喝瑶池鲜酿": "那我就坐等瑶池鲜酿了",
    "被镇于修罗古城中的妖邪近日隐隐躁动": "修罗出世非同小可，我这便前去加固封印",
    "火云戈壁的浮屠塔近来邪气外泄": "护法镇魔是我的强项，我这便前往。",
    "就凭你": "收拾你",
    "此间异象比老夫预想之中复杂万倍，实难在短期内探明全貌": "日后若有需要找我便是",
    "城东王老汉的小孙子阿旺前日出门玩耍，至今未归。有人说曾在大理寺墙外": "孩童走失非同小可，我这就去大理寺附近仔细寻找。",
    "我不回去！回去又要听爹娘摔碗砸锅": "阿旺朝比武场的方向跑去了，快去追上他吧",
    "地府为震慑怨念深重、不愿往生的凶魂，特在四方设下四象镇魂印": "阴阳有序，此事义不容辞。",
    "少侠代我去看看吧":"我这就动身",
    "还请少侠帮忙":"看我的",
    "我想看着她健健康康": "放心吧，你母亲定会早日康复的",
    "少侠可否帮忙寻一寻": "我这就去",
    "我也想念爷爷，我现在就回家！": "快回去吧，爷爷看到你一定会很高兴的！",
    "少侠不妨去那边": "没问题",
    "当初我决心要修炼成精的那一日，我本以为是短暂的分别":"哎",
    "阴阳通道重归稳定，多谢少侠出手相助。":"小事一桩，不足挂齿，告辞。"
}
# 需要点击屏幕中间跳过的对话框文案（出现则连点屏幕中间推进对话，
# 同 xuanshu.DIALOG_SKIP_TEXTS）；实测遇到无选项的对话在此追加
DIALOG_SKIP_TEXTS = ["少侠来得正及时！这月华霜蕊灵气充盈，正是宴上所需",
                     "附近的妖邪已被我清理干净了。",
                     "莫要过来",
                     "你怎么也跟过来",
                     "有个身份不明的孩童逢人便问花花的下落",
                     "你又来找我干嘛",
                     "我本是溪边一颗石子，花花自幼便叼着我在草地上打滚。那段被它捧在爪间",
                     "四象归位，怨气已平，去告知"]
# 任务追踪多绿字点击顺序（四象镇魂：按 青龙→白虎→朱雀→玄武 依次加固封印）；
# 关键词未命中时自动退回点第一个绿字，不影响其他任务
GREEN_CLICK_ORDER = ["青龙", "白虎", "朱雀", "玄武"]
# ---- 拍毒虫小游戏（入口对话框 → 开始 → 拍虫） ----
BUG_START_TEXT = "请在游戏开始后拍打毒虫，不要误伤蜻蜓和七星虫哦"  # 入口对话框文案
BUG_START_ICON = "bug_start.png"       # 开始按钮模板（38.png 实测素材）
DUCHONG_ICONS = ["duchong1.png", "duchong2.png", "duchong3.png"]  # 三种毒虫模板
BUG_CHECK_INTERVAL = 0.14              # 毒虫检索间隔（秒，用户指定 140ms）
BUG_TIMEOUT = 60                       # 小游戏总超时（秒）
BUG_IDLE_END = 5.0                     # 连续无虫出现达到该时长视为游戏结束（秒）
DIALOG_SKIP_CLICKS = 4               # 跳过对话的点击次数
SCREEN_CENTER_POS = (689, 415)       # 屏幕中间（游戏窗口中心，点击推进对话）
CLAIM_CHAIN_TIMEOUT = 30             # 整条领取对话链（含接取与任务对话）的总超时（秒）
# ---- 特殊任务步骤：前往洛阳城大理寺墙外（追踪文案出现时手动寻路） ----
DALI_TRACK_TEXT = "前往洛阳城大理寺墙外附近"   # 任务追踪文案（出现即需手动寻路）
DALI_MAP_CLICK_POS = (620, 317)               # 洛阳小地图上大理寺墙外的点击点（用户实测）
DALI_TARGET_POS = (220, 195)                  # 到达目标（洛阳城坐标，±5 容差）
DALI_ARRIVE_TIMEOUT = 120                     # 寻路等待超时（秒）
DALI_REDO_INTERVAL = 15                       # 寻路完成后最短重做间隔（任务文案未更新时防重复点击）
AWANG_ICON = "awang.png"                      # 阿旺 NPC 模板（图片后续提供）
# NPC 名字 OCR 兜底查找的排除区（任务追踪面板；面板内文案如「找阿旺。」不可点）
TRACK_PANEL_REGION = (0, 200, 260, 600)
# 任务追踪面板顶部小区域（标题行；客栈任务恒为面板首个任务，拍虫时快速检查用）
TRACK_TITLE_REGION = (0, 200, 260, 330)
_awang_missing_logged = False                 # 模板缺失仅记录一次，避免刷日志
_dali_last_done = 0.0                         # 上次大理寺寻路完成时间（防重复触发）
# ---- 特殊任务步骤：阿旺朝比武场方向跑（追踪文案出现时寻路到比武场） ----
BIWU_TRACK_TEXT = "阿旺朝洛阳城比武场方向跑"     # 任务追踪文案（出现即需寻路）
BIWU_MAP_CLICK_POS = (655, 620)               # 洛阳小地图上比武场的点击点（用户实测）
BIWU_TARGET_POS = (244, 12)                   # 到达目标（洛阳城坐标，±5 容差）
BIWU_ARRIVE_TIMEOUT = 120                     # 寻路等待超时（秒）
BIWU_REDO_INTERVAL = 15                       # 寻路完成后最短重做间隔
_biwu_last_done = 0.0                         # 上次比武场寻路完成时间（防重复触发）


def _norm_text(s):
    """去标点/空格/特殊字符（保留中文与字母数字），匹配前统一规范化。

    OCR 对标点误读常见（「，」读成「；」、句号变逗号等），去标点后
    再做精确/模糊匹配可大幅降低漏配。
    """
    return re.sub(r"[^\w]", "", s)


def _norm_dialog_present(items, dtxt):
    """规范化（去标点等）后的对话框文案模糊匹配（见 _norm_text）。"""
    norm_items = [(_norm_text(t), r, s) for t, r, s in items]
    return _dialog_text_present(norm_items, _norm_text(dtxt))


def _at_inn(win):
    """判断是否已在洛阳客栈（位置地名精确匹配）。"""
    pos = game_map.read_position_with_recovery(win)
    return pos is not None and pos[0].strip() == KEZHAN_MAP


def _find_zhanggui(win):
    """查找掌柜模板；未找到时按 Alt+6 后重试一次。返回 (x, y) 或 None。

    模板实测匹配 0.806，NPC 待机动画会小幅波动，阈值取 0.75 留余量
    （实测 0.7 以上无其他误匹配）。
    """
    pt = game_map.find_template(win, ZHANGGUI_ICON, threshold=0.75)
    if pt is not None:
        return pt
    log("[客栈] 未找到掌柜，按 Alt+6 后重试", "WARN")
    Keyboard.key_hot("alt", "6")
    time.sleep(ALT6_RETRY_WAIT)  # 等待画面刷新
    pt = game_map.find_template(win, ZHANGGUI_ICON, threshold=0.75)
    if pt is not None:
        log("[客栈] Alt+6 后已找到掌柜", "SYS")
    return pt


def _close_scene_panels(win):
    """关闭场景中可能打开的界面：小地图、世界地图、物品栏。"""
    game_map.close_maps(win)
    if bag.is_bag_open(win):
        ok, info = bag.close_bag(win)
        log(f"[客栈] 物品栏{info}", "SYS" if ok else "WARN")


def _click_fast_travel_option(win, items, img_np):
    """检测快送对话框并点击绿色选项「快送我去」；点击成功返回 True。"""
    if not _norm_dialog_present(items, FAST_DIALOG_TEXT):
        return False
    return _click_green_option_once(win, items, img_np, FAST_DIALOG_OPTION)


def _go_to_inn(win):
    """前往洛阳客栈：世界地图 → 点击洛阳城 → 小地图点击 (884,600)。

    点击后两种走向：
    - 出现「你要到洛阳客栈」对话框 → 点击「快送我去」快送到达
    - 未出现 → 等待寻路结束（到达洛阳客栈；位置稳定即视为寻路结束）
    返回 (是否成功, 描述)。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"

    _close_scene_panels(win)
    # 世界地图 → 点击洛阳城，打开洛阳小地图
    ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON,
                                           LUOYANG_CITY)
    if not ok:
        return False, info
    human_click(win, INN_MAP_CLICK_POS[0], INN_MAP_CLICK_POS[1])
    log("[客栈] 已点击洛阳小地图 (884,600)，前往洛阳客栈", "SYS")
    time.sleep(1.0)  # 等待寻路/对话框响应
    # 先查快送对话框再清理地图：清理的右键回退点可能落在对话框上将其点掉
    img = win.capture()
    if img is not None:
        # 快送对话框为居中对话框，区域 OCR（全帧 930ms → 区域 ~100ms）
        items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
        if _click_fast_travel_option(win, items, img_np):
            log("[客栈] 已点击「快送我去」，等待传送", "SYS")
            time.sleep(2.0)  # 等待传送完成
            return True, "已点击快送选项，传送洛阳客栈"
    _close_scene_panels(win)

    # 等待：快送对话框 / 到达洛阳客栈 / 寻路结束（位置稳定）
    deadline = time.time() + ARRIVE_TIMEOUT
    last = None
    unchanged = 0
    polls = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        img = win.capture()
        if img is not None:
            items, img_np = game_map.ocr_regions(img, (task_tracker.DIALOG_REGION,))
            if _click_fast_travel_option(win, items, img_np):
                log("[客栈] 已点击「快送我去」，等待传送", "SYS")
                time.sleep(2.0)  # 等待传送完成
                return True, "已点击快送选项，传送洛阳客栈"
        # 位置识别（寻路等待期间不做 Alt+C 恢复：Alt+C 会打断自动寻路，
        # 失败跳过本轮即可），同时判定是否已到客栈
        pos = game_map.read_map_position(win)
        if pos is not None and pos[0].strip() == KEZHAN_MAP:
            return True, "已到达洛阳客栈"
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 5:
            # 寻路已结束但未到客栈（点击无响应或目标位置变化），交给外层重试
            return False, "寻路结束但未到达洛阳客栈"
        polls += 1
        if polls % 5 == 0:
            log(f"[客栈] 前往客栈中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"前往洛阳客栈超时（{ARRIVE_TIMEOUT}s）"


def _ensure_zhanggui(win):
    """确保在洛阳客栈并找到掌柜（用户流程 1.1~1.3）。

    返回 (掌柜坐标, 描述)；未找到返回 (None, 描述)。
    """
    for attempt in range(1, NAV_MAX_ATTEMPTS + 1):
        if _at_inn(win):
            pt = _find_zhanggui(win)
            if pt is not None:
                return pt, f"已在洛阳客栈并找到掌柜（第 {attempt} 轮）"
            # 在客栈但找不到掌柜：按用户要求终止任务
            return None, "在洛阳客栈但未找到掌柜（Alt+6 后仍无），终止任务"
        log(f"[客栈] 不在洛阳客栈（第 {attempt}/{NAV_MAX_ATTEMPTS} 轮），开始寻路", "SYS")
        ok, info = _go_to_inn(win)
        log(f"[客栈] {info}", "SYS" if ok else "WARN")
        if ok:
            # 快送/到达后回到 1.1 逻辑（下一轮循环检查）
            continue
        if "战斗" in info:
            return None, info
        time.sleep(2.0)  # 轮次间隔，避免空转
    return None, f"多次尝试后仍未到达洛阳客栈（{NAV_MAX_ATTEMPTS} 次），终止任务"


def _has_task(win):
    """判断任务追踪中是否存在客栈任务（标题行匹配，避免内容行误判）。

    匹配串取「客栈任务」（标题形如 月宫采珍【客栈任务】）：
    单「客栈」会命中地图位置文案 洛阳客栈[18,12]（实测第 12 轮误判根因）。
    改用 task_tracker.has_task：只 OCR 追踪面板区域（约 0.9s，全窗 OCR
    约 2.2s），标题恒在面板左区且非绿字，判定语义与 get_task 一致；
    领取对话链轮询与每轮任务判定均随之提速。
    """
    return task_tracker.has_task(win, TASK_TITLE)


def _click_screen_center(win, times=DIALOG_SKIP_CLICKS):
    """点击屏幕中间若干次（推进 NPC 对话）。"""
    cx, cy = SCREEN_CENTER_POS
    for _ in range(times):
        human_click(win, cx, cy)
        time.sleep(0.5)  # 对话推进间隔
    log(f"[客栈] 已点击屏幕中间 {times} 次（推进对话）", "SYS")


def _near_luoyang(pos, target):
    """判断是否在洛阳城目标坐标附近（±5 容差）。"""
    if pos is None:
        return False
    return (pos[0].strip() == LUOYANG_CITY
            and abs(pos[1] - target[0]) <= 5
            and abs(pos[2] - target[1]) <= 5)


def _find_awang(win):
    """查找阿旺模板；awang.png 未提供时仅记录一次（不刷日志）。"""
    global _awang_missing_logged
    try:
        return game_map.find_template(win, AWANG_ICON, threshold=0.75)
    except Exception as e:
        if not _awang_missing_logged:
            log(f"[客栈] 阿旺模板不可用（{e}），改用 OCR 名字兜底", "WARN")
            _awang_missing_logged = True
        return None


def _find_awang_by_ocr(win):
    """全屏 OCR 查找 NPC 名字「阿旺」，返回点击坐标或 None。

    只匹配短行（≤4 字）含「阿旺」，排除任务追踪面板区域（0,200-260,600），
    避免误点追踪文案（如「找阿旺。」）。NPC 名字标签本身即可点击。
    """
    img = win.capture()
    if img is None:
        return None
    items, _ = game_map.ocr_image(img)
    _, pt, pr, pb = TRACK_PANEL_REGION
    for txt, rect, score in items:
        if score < 0.55 or len(txt) > 4 or "阿旺" not in txt:
            continue
        if rect[0] < pr and pt <= rect[1] <= pb:
            continue  # 任务追踪面板内的文案，跳过
        cx = (rect[0] + rect[2]) // 2
        cy = (rect[1] + rect[3]) // 2
        log(f"[客栈] OCR 识别到 NPC 名字「{txt}」@({cx},{cy})", "SYS")
        return cx, cy
    return None


def _check_awang(win):
    """检查阿旺 NPC：先模板 awang.png，未命中时全屏 OCR 名字「阿旺」兜底；
    找到则点击推进任务。返回 (是否成功, 描述)。
    """
    pt = _find_awang(win)
    if pt is None:
        pt = _find_awang_by_ocr(win)
    if pt is None:
        return False, "未找到阿旺（模板与 OCR 名字均未命中）"
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 已点击阿旺 @({pt[0]},{pt[1]})", "SYS")
    return True, "已点击阿旺"


def _walk_to_luoyang_pos(win, click_pos, target_pos, timeout, desc):
    """洛阳城寻路通用流程：已在目标附近则跳过；已在洛阳城直接开小地图，
    否则世界地图 → 洛阳城 → 小地图；点击 click_pos 后等待到达目标附近。
    返回 (是否成功, 描述)。

    等待期间只用普通位置读取：Alt+C 恢复会打断自动寻路
    （实测按 Alt+C 后角色停在原地不再移动），识别失败跳过本轮即可。
    """
    if is_in_battle(win):
        return False, "战斗中，跳过本轮寻路"
    pos = game_map.read_position_with_recovery(win)
    if _near_luoyang(pos, target_pos):
        return True, f"已在{desc}附近"
    _close_scene_panels(win)
    if pos is not None and pos[0].strip() == LUOYANG_CITY:
        # 已在洛阳城：无需打开世界地图，直接打开小地图
        ok, info = game_map.open_mini_map(win)
    else:
        ok, info = game_map.open_mini_map_city(win, game_map.WORLD_MAP_LUOYANG_ICON,
                                               LUOYANG_CITY)
    if not ok:
        return False, info
    human_click(win, click_pos[0], click_pos[1])
    log(f"[客栈] 已点击洛阳小地图 {click_pos}，前往{desc}", "SYS")
    time.sleep(1.0)  # 等待自动寻路启动
    _close_scene_panels(win)
    # 等待到达目标附近
    deadline = time.time() + timeout
    polls = 0
    last = None
    unchanged = 0
    while time.time() < deadline:
        if is_in_battle(win):
            time.sleep(2.0)
            continue
        pos = game_map.read_map_position(win)
        if _near_luoyang(pos, target_pos):
            log(f"[客栈] 已到达{desc}附近", "SYS")
            return True, f"已到达{desc}附近"
        # 位置长时间不变（寻路被打断/无响应）→ 提前退出交给外层重试，
        # 不等满超时（识别失败 None 不计入）
        if pos is not None and pos == last:
            unchanged += 1
        else:
            unchanged = 0
        last = pos
        if unchanged >= 10:
            return False, f"寻路到{desc}无响应（位置 {pos} 长时间不变）"
        polls += 1
        if polls % 5 == 0:
            log(f"[客栈] 前往{desc}中... 当前位置: {pos or '识别失败'}", "INFO")
        time.sleep(2.0)
    return False, f"寻路到{desc}超时（未到达洛阳城 {target_pos[0]},{target_pos[1]} 附近）"


def _walk_to_dali(win):
    """前往洛阳城大理寺墙外并检查阿旺。返回 (是否成功, 描述)。"""
    ok, info = _walk_to_luoyang_pos(win, DALI_MAP_CLICK_POS, DALI_TARGET_POS,
                                    DALI_ARRIVE_TIMEOUT, "大理寺墙外")
    if not ok:
        return False, info
    return _check_awang(win)


def _walk_to_biwu(win):
    """前往洛阳城比武场（阿旺朝比武场方向跑），到达后搜索阿旺并点击。
    返回 (是否成功, 描述)。
    """
    ok, info = _walk_to_luoyang_pos(win, BIWU_MAP_CLICK_POS, BIWU_TARGET_POS,
                                    BIWU_ARRIVE_TIMEOUT, "比武场")
    if not ok:
        return False, info
    return _check_awang(win)


def _task_needs_dali_walk(task):
    """判断任务追踪文案是否要求前往大理寺墙外（模糊匹配，跨行兼容）。"""
    return (task is not None
            and _norm_dialog_present(task["lines"], DALI_TRACK_TEXT))


def _task_needs_biwu_walk(task):
    """判断任务追踪文案是否出现「阿旺朝洛阳城比武场方向跑」。"""
    return (task is not None
            and _norm_dialog_present(task["lines"], BIWU_TRACK_TEXT))


def _order_green_state(items, img_np):
    """四象任务绿字状态：返回 (仍绿关键词列表, 词簇中心列表, 区域原点)。

    对含顺序关键词的 OCR 行取并集区域做绿字掩码（只保留绿像素）；
    绿字列连通簇按阅读顺序排列，与「文本中存在的顺序关键词」尾部对齐
    （封印按序完成、完成的关键词变红，仍绿的必为关键词序列的后缀），
    从而在文字不变、仅颜色变化时也能精确判定每个关键词的绿字状态。
    词级判定不受 OCR 行合并/拆分抖动影响（关键词从各行拼接规范化
    文本中查找）。无顺序关键词的任务返回空。

    只取任务追踪面板内的行：对话框文案（如判官解释四象顺序）也可能含
    关键词，会把对话框的绿字选项误当成封印关键词（实测点到了 (711,391)
    的对话框选项）。
    """
    pl, pt_, pr, pb = TRACK_PANEL_REGION
    kw_lines = [(txt, rect) for txt, rect, _ in items
                if any(kw in _norm_text(txt) for kw in GREEN_CLICK_ORDER)
                and rect[0] < pr and pt_ <= rect[1] <= pb]
    if not kw_lines:
        return [], [], (0, 0)
    l = min(rect[0] for _, rect in kw_lines)
    t = min(rect[1] for _, rect in kw_lines)
    r = max(rect[2] for _, rect in kw_lines)
    b = max(rect[3] for _, rect in kw_lines)
    region = img_np[t:b, l:r].astype(int)
    g = region[:, :, 1]
    rr = region[:, :, 0]
    bb = region[:, :, 2]
    mask = (g > 90) & (g > rr + 30) & (g > bb + 30)
    # 文本中存在的顺序关键词（各行拼接后规范化查找，抗跨行拆分）
    joined = _norm_text("".join(txt for txt, _ in kw_lines))
    kw_seq = [kw for kw in GREEN_CLICK_ORDER if kw in joined]
    if not mask.any() or not kw_seq:
        return [], [], (l, t)
    # 绿字 2D 连通域（字符级，每字一块），过滤噪点与「完成变红后
    # 残留的绿色下划线」（下划线高约 1-5px，文字高约 14px，h>=6 区分）
    ncomp, _, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    chars = []
    for i in range(1, ncomp):
        x, y, w, h, area = stats[i]
        if w >= 8 and h >= 6 and area >= 20:
            chars.append([x, y, x + w, y + h])
    # 同行相邻字符合并为词（y 差<=6、字符间距<=6px；实测字宽约 13px、
    # 词内字距约 2px、词间距约 17px），词按阅读顺序（先 y 后 x）
    words = []
    for c in sorted(chars, key=lambda c: (c[1], c[0])):
        if words and abs(c[1] - words[-1][1]) <= 6 and c[0] - words[-1][2] <= 6:
            words[-1][2] = max(words[-1][2], c[2])
            words[-1][3] = max(words[-1][3], c[3])
        else:
            words.append(c[:])
    comps = [((w[0] + w[2]) // 2, (w[1] + w[3]) // 2) for w in words]
    # 仍绿关键词 = 关键词序列尾部 n 个（按序完成 → 绿的必为后缀）
    n = min(len(comps), len(kw_seq))
    if n == 0:
        return [], [], (l, t)
    green_kws = kw_seq[-n:]
    return green_kws, comps[-n:], (l, t)


def _order_progress_key(items, img_np):
    """四象任务进度键：仍为绿字的顺序关键词列表（如 白虎,朱雀,玄武）。

    封印完成关键词变红（文字不变），绿字集合缩小即任务推进，
    follow_task 据此点击下一个绿字。词级判定不受 OCR 抖动影响。
    """
    green_kws, _, _ = _order_green_state(items, img_np)
    return ",".join(green_kws)


def _pick_order_green(items, img_np):
    """精确选中最早的绿字顺序关键词并返回窗口坐标点击点。

    行级选择无法区分同一行内的多个绿字（如 白虎 已红而同行的 朱雀
    仍绿），绿字词簇按阅读顺序与关键词后缀一一对应，取最早仍绿的
    关键词词簇中心点击。未命中返回 None（回退 track_task 行级选择）。
    """
    green_kws, centers, (l, t) = _order_green_state(items, img_np)
    if not green_kws or not centers:
        return None
    cx, cy = centers[0]
    return l + cx, t + cy


def _find_duchong(win, threshold=0.75):
    """一次截图同时检索三种毒虫模板，返回首个命中的 (x, y) 窗口坐标。

    返回模板匹配中心（与 FindImages 约定一致）。每轮只截图一次、
    三个模板连续匹配，接近 140ms 的检索节奏。
    """
    img = win.capture()
    if img is None:
        return None
    scene = np.asarray(img)[:, :, :3]
    for icon in DUCHONG_ICONS:
        tmpl = cv2.imread(R.img(icon))
        if tmpl is None:
            log(f"[客栈] 毒虫模板不可用: {icon}", "WARN")
            continue
        res = cv2.matchTemplate(scene, tmpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxl = cv2.minMaxLoc(res)
        if maxv >= threshold:
            h, w = tmpl.shape[:2]
            return maxl[0] + w // 2, maxl[1] + h // 2
    return None


def _has_task_fast(win):
    """快速检查任务追踪中是否仍有客栈任务（仅 OCR 追踪面板顶部一小块，
    客栈任务恒为面板首个任务；避免全窗 OCR 拖慢拍虫节奏）。
    截图失败时返回 True（不判定结束）。"""
    img = win.capture()
    if img is None:
        return True
    items, _ = game_map.ocr_region(img, TRACK_TITLE_REGION)
    return task_tracker.find_task(task_tracker.group_tasks(items),
                                  TASK_TITLE) is not None


def _play_duchong(win):
    """拍毒虫小游戏：点击开始按钮 → 循环检索三种毒虫并点击。

    开始后每 BUG_CHECK_INTERVAL（140ms）检索一次毒虫模板（三种类型，
    每次只会出现一只），命中即点击。结束条件（满足其一）：
    1. 任务追踪中客栈任务消失（任务完成，每 2 秒快速检查一次）
    2. 连续 BUG_IDLE_END 秒无虫出现
    3. BUG_TIMEOUT 总超时兜底
    返回 (是否成功, 描述)。
    """
    pt = game_map.find_template(win, BUG_START_ICON, threshold=0.75)
    if pt is not None:
        human_click(win, pt[0], pt[1])
        log("[客栈] 已点击毒虫小游戏开始", "SYS")
        time.sleep(0.5)  # 等待游戏开始动画
    deadline = time.time() + BUG_TIMEOUT
    clicked = 0
    last_hit = time.time()
    last_task_check = 0.0
    while time.time() < deadline:
        pt = _find_duchong(win)
        if pt is not None:
            human_click(win, pt[0], pt[1])
            clicked += 1
            last_hit = time.time()
            log(f"[客栈] 拍中第 {clicked} 只毒虫 @({pt[0]},{pt[1]})", "SYS")
        elif time.time() - last_hit > BUG_IDLE_END:
            return True, f"拍毒虫结束（共拍 {clicked} 只）"
        # 客栈任务从追踪中消失即任务完成，结束小游戏。仅在无虫间歇检查
        # （游戏仅约 20 秒，OCR 检查约 1.1s，拍虫正忙时不做检查）
        now = time.time()
        if now - last_hit > 2.0 and now - last_task_check > 4.0:
            last_task_check = now
            if not _has_task_fast(win):
                return True, f"拍毒虫结束（共拍 {clicked} 只，客栈任务已消失）"
        time.sleep(BUG_CHECK_INTERVAL)
    return True, f"拍毒虫超时结束（共拍 {clicked} 只）"


def _handle_follow_extras(win, items, img_np):
    """follow 期间的附加处理。

    拍毒虫小游戏入口优先（对话框出现则完整处理）；大理寺/比武场寻路
    （阻塞执行；完成后各自 REDO_INTERVAL 内不重复触发）；防抖期内与
    无关时照常处理任务对话选项/跳过（实测：防抖期内对话处理被短路
    导致「莫要过来」跳过从未触发）。
    返回是否已处理。
    """
    global _dali_last_done, _biwu_last_done
    task = task_tracker.find_task(task_tracker.group_tasks(items), TASK_TITLE)
    if not is_in_battle(win):
        # 拍毒虫小游戏暂不处理（毒虫模板未匹配成功，先人工操作）：
        # 模板就绪后恢复此段即可自动开始并拍虫
        if False and _norm_dialog_present(items, BUG_START_TEXT):
            ok, info = _play_duchong(win)
            log(f"[客栈] {info}", "SYS" if ok else "WARN")
            return True
        if _task_needs_dali_walk(task) \
                and time.time() - _dali_last_done >= DALI_REDO_INTERVAL:
            # 寻路阻塞执行，本轮 items 已过期，不再做对话处理
            ok, info = _walk_to_dali(win)
            _dali_last_done = time.time()
            log(f"[客栈] 大理寺寻路: {info}", "SYS" if ok else "WARN")
            return True
        if _task_needs_biwu_walk(task) \
                and time.time() - _biwu_last_done >= BIWU_REDO_INTERVAL:
            ok, info = _walk_to_biwu(win)
            _biwu_last_done = time.time()
            log(f"[客栈] 比武场寻路: {info}", "SYS" if ok else "WARN")
            return True
    return _handle_dialog_options(win, items, img_np)


def _click_green_option_fuzzy(win, items, img_np, text):
    """点击指定文案的绿色选项（模糊匹配，抗 OCR 个别字误读）；成功返回 True。

    优先精确子串；否则相似度 ≥0.8 兜底（实测区域裁剪 OCR 会把
    「阴阳有序，此事义不容辞。」读成「…此事文不容辞，」，精确匹配漏掉）。
    """
    nopt = _norm_text(text)
    for txt, rect, score in items:
        if score < 0.55 or rect[0] < task_tracker.PANEL_MAX_X:
            continue  # 限定对话框区域，避免误点追踪面板的绿字链接
        ntxt = _norm_text(txt)
        if nopt in ntxt or _fuzzy_contains(ntxt, nopt, threshold=0.8):
            if task_tracker.is_green_text(img_np, rect):
                cx, cy = task_tracker.green_text_center(img_np, rect,
                                                        prefer_underline=False)
                human_click(win, cx, cy)
                log(f"[客栈] 已点击对话框绿色选项「{txt}」@({cx},{cy})", "SYS")
                return True
    return False


def _click_green_option_fallback(win, items, img_np, qtxt):
    """选项 OCR 不可识别（如单字「哎」）时兜底：定位问题行，点击其下方
    80px 内的第一个亮绿文字块（选项链接为亮绿，世界暗绿噪点被过滤）。
    返回是否已点击。
    """
    qrect = None
    for txt, rect, _ in items:
        if _fuzzy_contains(_norm_text(txt), _norm_text(qtxt), threshold=0.85):
            qrect = rect
            break
    if qrect is None:
        return False
    x0 = qrect[0] - 20
    y0 = qrect[3]
    x1 = qrect[2] + 300
    region = img_np[y0:y0 + 80, x0:x1].astype(int)
    if region.size == 0:
        return False
    g = region[:, :, 1]
    r = region[:, :, 0]
    b = region[:, :, 2]
    mask = (g > 120) & (g > r + 30) & (g > b + 30)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8),
                                                      connectivity=8)
    cands = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w >= 6 and h >= 6 and area >= 15:
            cands.append((y, x, w, h))
    if not cands:
        return False
    cands.sort()  # 阅读序，取第一个
    y, x, w, h = cands[0]
    cx = x0 + x + w // 2
    cy = y0 + y + h // 2
    human_click(win, cx, cy)
    log(f"[客栈] 选项「{qtxt}」OCR 不可识别，按问题行下方绿字块点击 @({cx},{cy})", "SYS")
    return True


def _handle_dialog_options(win, items, img_np):
    """处理任务对话：DIALOG_OPTION_MAP 绿字选项优先，其次按
    DIALOG_SKIP_TEXTS 连点屏幕中间跳过（同 xuanshu 的处理顺序）。

    领取对话链与跟踪期间（follow_task 的附加对话处理回调）均使用，
    返回是否已处理。
    """
    for dtxt, opt in DIALOG_OPTION_MAP.items():
        if not _norm_dialog_present(items, dtxt):
            continue
        if _click_green_option_fuzzy(win, items, img_np, opt):
            log(f"[客栈] 检测到任务对话，已点击绿色选项「{opt}」", "SYS")
            return True
        # 选项 OCR 不可识别（如单字「哎」）：按问题行下方绿字块点击
        if _click_green_option_fallback(win, items, img_np, dtxt):
            log("[客栈] 检测到任务对话（选项 OCR 不可识别，绿字块兜底点击）", "SYS")
            return True
    for dtxt in DIALOG_SKIP_TEXTS:
        if _norm_dialog_present(items, dtxt):
            _click_screen_center(win)
            return True
    return False


def _claim_task(win, pt):
    """点击掌柜并领取客栈任务。返回 (是否成功, 描述)。

    pt 为模板匹配中心（FindImages 返回匹配中心，实测与 NPC 人物本体一致），
    直接点击即可。

    对话链（逐屏推进，直到任务进入追踪面板）：
    掌柜 → 「接取客栈任务」绿字 → （瑶池迎仙大会对话 → 点「广寒宫我熟悉的
    很，包在我身上！」）→ 任务出现在追踪面板。
    每日次数已满时出现玄枢轶闻同款文案，提前结束。
    """
    human_click(win, pt[0], pt[1])
    log(f"[客栈] 已点击掌柜 @({pt[0]},{pt[1]})", "SYS")
    deadline = time.time() + CLAIM_CHAIN_TIMEOUT
    while time.time() < deadline:
        if _has_task(win):
            return True, "已领取客栈任务"
        img = win.capture()
        if img is None:
            time.sleep(0.5)
            continue
        items, img_np = game_map.ocr_image(img)
        if _norm_dialog_present(items, DAILY_LIMIT_TEXT):
            return False, DAILY_LIMIT_INFO
        # 接取选项优先；未出现时处理任务对话（两屏顺序不定，逐屏推进）
        handled = _click_green_option_once(win, items, img_np, DIALOG_OPTION_LINGQU)
        if not handled:
            _handle_dialog_options(win, items, img_np)
        time.sleep(1.0)
    return False, f"点击掌柜后未领取到客栈任务（对话链超时 {CLAIM_CHAIN_TIMEOUT}s）"


def run_cycle(win):
    """执行一轮客栈任务检查：有任务则跟踪到完成，无任务则领取。

    任务判定三重保险（实测：比武场步骤后任务未完成却误判无任务、
    带任务跑回客栈领取失败）：
    1. 无任务判定隔 3 秒复核一次（OCR 噪声/面板刷新会瞬时漏检）
    2. 特殊寻路失败不中断本轮（交给 follow 与兜底处理器重试）
    3. 到达客栈后领前复核：任务其实还在则直接跟踪，不点掌柜

    Args:
        win: 游戏主窗口 Window 对象

    返回 (状态, 描述)；状态：done 完成一次任务 / daily_limit 每日次数已满 / fail 本轮失败
    """
    if is_in_battle(win):
        return "fail", "战斗中，跳过本轮（等待战斗结束）"

    # 无任务判定先复核一次（2s：任务判定已改区域 OCR，仍保留复核防瞬时漏检；
    # 若出现「有任务却误判没有」需调回 3s）
    if not _has_task(win):
        time.sleep(2.0)

    if _has_task(win):
        # 特殊任务步骤：追踪要求前往大理寺墙外/比武场 → 先手动寻路
        # （绿字点击的自动寻路不适用；跟踪期间 _handle_follow_extras 同样兜底）
        global _dali_last_done, _biwu_last_done
        task = task_tracker.get_task(win, TASK_TITLE)
        if _task_needs_dali_walk(task) \
                and time.time() - _dali_last_done >= DALI_REDO_INTERVAL:
            ok, info = _walk_to_dali(win)
            _dali_last_done = time.time()
            log(f"[客栈] 大理寺寻路: {info}", "SYS" if ok else "WARN")
        if _task_needs_biwu_walk(task) \
                and time.time() - _biwu_last_done >= BIWU_REDO_INTERVAL:
            ok, info = _walk_to_biwu(win)
            _biwu_last_done = time.time()
            log(f"[客栈] 比武场寻路: {info}", "SYS" if ok else "WARN")
        ok, info = follow_task(win, TASK_TITLE, prefix="客栈",
                               extra_dialog_handler=_handle_follow_extras,
                               green_order=GREEN_CLICK_ORDER,
                               progress_key=_order_progress_key,
                               green_picker=_pick_order_green)
        return ("done", info) if ok else ("fail", info)

    # 没有任务 → 前往洛阳客栈找掌柜并领取任务
    pt, info = _ensure_zhanggui(win)
    if pt is None:
        return "fail", info
    # 到达客栈后领前复核：任务其实还在（此前误判）→ 直接跟踪，不点掌柜
    if _has_task(win):
        log("[客栈] 领前复核发现任务仍在追踪中，改为直接跟踪", "WARN")
        ok, info = follow_task(win, TASK_TITLE, prefix="客栈",
                               extra_dialog_handler=_handle_follow_extras,
                               green_order=GREEN_CLICK_ORDER,
                               progress_key=_order_progress_key,
                               green_picker=_pick_order_green)
        return ("done", info) if ok else ("fail", info)
    ok, info = _claim_task(win, pt)
    if not ok:
        return ("daily_limit", info) if info == DAILY_LIMIT_INFO else ("fail", info)

    # 领取成功：紧接着跟踪任务，不等轮次间隔
    ok, info = follow_task(win, TASK_TITLE, prefix="客栈",
                           extra_dialog_handler=_handle_follow_extras,
                           green_order=GREEN_CLICK_ORDER,
                           progress_key=_order_progress_key,
                           green_picker=_pick_order_green)
    return ("done", info) if ok else ("fail", info)


def run_loop(win, cycle_interval=3.0):
    """客栈主循环：跟踪完成的任务 → 回去继续领取 → 往复执行。

    结束条件（满足其一即返回结束原因，任务随之停止）：
    1. 完成任务次数达到设置的次数上限（通用设置-任务次数）
    2. 领取任务时出现「请明天再来」文案（每日次数已满，同玄枢轶闻）
    3. 在洛阳客栈找不到掌柜（Alt+6 后仍无）→ 终止任务
    返回结束原因描述。
    """
    max_rounds = load_task_counts().get(TASK_MODE, 0)
    log(f"[客栈] 任务次数上限: {max_rounds if max_rounds > 0 else '不限'}", "SYS")
    done = 0
    round_no = 0
    while True:
        round_no += 1
        log(f"[客栈] ==== 第 {round_no} 轮 ====", "SYS")
        status, info = run_cycle(win)
        if status == "done":
            done += 1
            total = max_rounds if max_rounds > 0 else "∞"
            debug_overlay.set_progress(f"{done}/{total}")
            log(f"[客栈] {info}（{done}/{total}）", "SYS")
            if 0 < max_rounds <= done:
                return f"已完成 {done} 次客栈任务，达到次数上限 {max_rounds}"
        elif status == "daily_limit":
            log(f"[客栈] {info}（已完成 {done} 次）", "SYS")
            return f"每日任务次数已满（已完成 {done} 次）"
        else:
            log(f"[客栈] {info}", "WARN")
            # 找不到掌柜等终止性失败直接结束任务（由 run_cycle 返回的描述判定）
            if "终止任务" in info:
                return info
        time.sleep(cycle_interval)  # 轮次间隔，避免空转
