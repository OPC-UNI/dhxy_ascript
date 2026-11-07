"""组队功能操作（库模块，供任务模块调用）。

识别方式（用户提供，2026-10-06，模板见 res/img）：
1. 是否已组队：点击组队按钮 zudui_item.png → 菜单中出现「队伍信息」
   duiwuxinxi.png 即已组队，否则未组队
2. 是否队长：队伍面板打开后双模板比分数——「解散队伍」jiesanduiwu.png
   分数达标且高于「离开队伍」likaiduiwu.png 才算队长（两按钮样式相近，
   单模板会把离开按钮误判为解散按钮，实测误匹配 0.807~0.834 ≥ 0.8 阈值）
3. 退出队伍：队长面板点「解散队伍」jiesanduiwu.png；队员面板点
   「离开队伍」likaiduiwu.png
4. 队伍面板已打开判定：duiwumianban.png（0.6）为主判据；标志匹配随成员数
   退化（5 人队实测 0.44~0.68 抖动）不达标时，以面板专属文案
   （队标/暂时离队/招募队友）OCR 兜底。不用关闭按钮判定——多个面板
   共用同款关闭按钮，不具区分性
5. 队伍成员分析：以 duiwumianban 标志模板为锚点划分 5 个固定成员区域
   （124x265，第 1 区域=队长，区域间间隔 20px），逐区域独立解析
   （区域=一个角色，不跨区域拼信息）。空位判定优先用
   duiwumianban_wurenquyu 无人区域模板（未提供时以区域 OCR 无
   种族/等级文案兜底）；首空位之后不再解析，人数=首空位序号。
   第 1 区域种族文字选中态为金色、OCR 读不到时用字形库形状匹配兜底
6. 关闭队伍面板：面板内关闭按钮 zudui_close.png，鼠标移过去左键点击
   （ESC 实测关不掉队伍面板，勿依赖）
7. 关闭组队菜单：菜单打开时再点一次 zudui_item.png（开关式）即消失

实机自检记录（2026-10-06）：组队判定/面板打开/种族识别通过；
首次队长检查因上一轮菜单未关、再点组队按钮把菜单关闭而失败，
故所有入口统一走「菜单已开则复用」的 _ensure_menu_open 流程。

待接入（需使用方提供识别方式）：
- handle_join_requests：申请列表界面与同意/拒绝按钮定位
- apply_to_team：世界场景中「申请队伍」招募玩家的识别
"""
import difflib
import os
import pathlib
import re
import time

import cv2
import numpy as np
from PIL import Image
from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages

from .app_utils import log, human_click, human_move, norm_text
from . import map as game_map  # 找图与 OCR 能力复用
from . import debug_overlay  # 调试信息悬浮层（队伍信息行更新）
from . import task_tracker  # 对话框区域 OCR（允许失败提示检测）
from .dialog_common import _dialog_text_present

# ---- 模板（res/img，用户截图提供） ----
ZUDUI_ITEM_ICON = "zudui_item.png"        # 组队按钮（点击弹出组队菜单）
ZUDUI_ITEM2_ICON = "zudui_item2.png"      # 组队按钮-申请待处理态（有人申请加入时按钮
                                           # 变样；出现即确定性队长，点击直接弹出
                                           # 申请加入面板而非菜单——用户 2026-10-14 实测）
DUIWU_XINXI_ICON = "duiwuxinxi.png"       # 菜单「队伍信息」（已组队时菜单中出现）
JIESAN_DUIWU_ICON = "jiesanduiwu.png"     # 队伍面板「解散队伍」按钮（仅队长可见）
DUIWU_MIANBAN_ICON = "duiwumianban.png"   # 队伍面板标志（面板已打开判定）
ZUDUI_CLOSE_ICON = "zudui_close.png"      # 队伍面板关闭按钮（鼠标移上去左键点击）
ZUDUI_SHENQINGJIARU_ICON = "zudui_shenqingjiaru.png"  # 申请加入面板标志（队伍人数不足时
ZUDUI_YUNXU_ICON = "zudui_yunxu.png"      # 申请加入面板「允许」按钮（用户提供模板，实测 1.00 匹配）
                                          # 点组队按钮可能打开该面板；检测到直接点关闭，不处理）
LIKAI_DUIWU_ICON = "likaiduiwu.png"       # 队伍面板「离开队伍」按钮（队员可见）

# ---- 队伍面板内种族 OCR ----
# 面板成员种族文案区域（窗口相对）；种族文案范围见 RACE_WORDS，
# 每个成员对应一条种族文案，故成员数 = 识别到的种族数（最多 5，最少 1）
RACE_REGION = (300, 260, 1066, 620)
RACE_WORDS = ("男人", "女人", "男鬼", "女鬼", "男仙", "女仙", "男魔", "女魔", "男巫", "女巫",
               "男龙", "女龙")

# 组队菜单区域与文案（OCR 判定菜单已打开：未组队菜单为
# 组队操作/创建队伍/附近队伍/组队平台，已组队菜单含队伍信息）
TEAM_MENU_REGION = (950, 640, 1150, 830)
TEAM_MENU_TEXTS = ("组队操作", "创建队伍", "附近队伍", "组队平台", "队伍信息")

# ---- 队伍面板成员区域（2026-11-05 重写：固定 5 区域，标志模板锚定） ----
# 面板内固定 5 个成员区域，每个区域 = 一个角色（第 1 区域为队长），
# 空位自后向前连续（2 人队伍第 3 区域起为空）。几何（用户提供并实测
# 校准）：以 duiwumianban 标志模板匹配框为锚——第 1 区域左缘 = 模板
# 右缘 +15px、顶缘 = 模板顶缘 +15px；区域宽高 124x265，间隔约 20px
MEMBER_REGION_COUNT = 5
MEMBER_REGION_W = 124
MEMBER_REGION_H = 265
MEMBER_REGION_GAP = 20
MEMBER_REGION_OFFSET = 15
# 种族竖排文字贴区域右缘并右溢约 12px 进间隔（OCR 框实测），
# 区域 OCR 向右扩此宽度保证完整覆盖
MEMBER_RACE_SPILL = 20
# 空位（无人）区域模板（双模板：空位占位图有两种渲染形态，任一命中
# 即空位）：wurenquyu 用户 2026-11-05 提供（124x263，区域裁剪去右侧
# 溢出 7px，真帧校准空位 0.993 / 成员 0.26~0.39）；wurenquyu2 用户
# 2026-11-05 补充（124x267 同款裁剪）。全部缺失时以区域 OCR 兜底
# （无种族且无等级文案视为空位）。存在性运行时判断，缺文件自动降级
EMPTY_REGION_ICON = "duiwumianban_wurenquyu.png"
EMPTY_REGION_ICON2 = "duiwumianban_wurenquyu2.png"
EMPTY_REGION_ICONS = (EMPTY_REGION_ICON, EMPTY_REGION_ICON2)
EMPTY_REGION_THRESHOLD = 0.8
# 空位匹配裁剪外扩（左, 上, 右, 下）：容用户裁剪 1~2px 偏差与面板渲染
# 漂移（实测 4 人帧：区域内 0.837、正确对齐位在区域左 1px 外 0.993）；
# 右扩 12 覆盖种族溢出带，均不越出 20px 区域间隔
EMPTY_REGION_PAD = (4, 4, 12, 4)
# 第 1 区域种族文字固定位置（相对区域左上角，实测 x 116-136 / y 0-40；
# 面板位置会漂移，窗口绝对坐标不可用）。选中态金色文字 OCR 读不到，
# 用固定裁剪 + 颜色掩码字形库形状匹配识别（外扩 2px 容抖动，
# 金色掩码可排除背景干扰）
RACE_GLYPH_REL = (114, 0, 138, 41)
# 字形匹配阈值（同字体不同颜色，掩码归一化后同种族实测 0.86+，
# 不同种族 ≤0.31，取 0.80 留余量且不会误判）
_RACE_GLYPH_MATCH = 0.80

# 模板匹配与等待参数
_TEMPLATE_THRESHOLD = 0.8
# 组队按钮-申请待处理态单独阈值：item2 实测最佳 0.78，低于 0.8 漏检
# （用户 2026-10-15 指定 0.77；其他模板仍用 _TEMPLATE_THRESHOLD）
ZUDUI_ITEM2_THRESHOLD = 0.77
# 面板标志模板匹配度随队伍成员数变化（单人队实测 ~0.99，两人队降至 0.65，
# 5 人队实测 0.436~0.683 抖动）；判定不依赖关闭按钮——多个面板共用同款
# 关闭按钮（申请加入面板即同款），不具区分性。低于阈值时 OCR 文案兜底
_PANEL_FLAG_THRESHOLD = 0.6
# 面板专属文案（队伍面板内按钮/标签，其他面板与场景不出现；OCR 兜底判据）
_PANEL_TEXTS = ("队标", "暂时离队", "招募队友")
_MENU_WAIT_TRIES = 3       # 菜单弹出等待（组队按钮点击后）
_MENU_WAIT_INTERVAL = 0.5
_PANEL_WAIT_TRIES = 8      # 面板打开等待（队伍信息点击后，面板为服务器数据，稍慢）
_PANEL_WAIT_INTERVAL = 0.5
_OCR_MIN_SCORE = 0.55

# 转生等级文案：未转102 / 1转120 / 2转120 / 3转120 / 飞升120
# （容 OCR 噪声：木转=未转、飞异/飞昇=飞升、数字 12O/1 20 等）
_LEVEL_RE = re.compile(r"(未转|木转|[123]转|飞[升异昇])\s*([0-9OoIlSs]{2,3})")
_LEVEL_DIGIT_MAP = str.maketrans({"O": "0", "o": "0", "S": "5", "s": "5",
                                  "I": "1", "l": "1"})


# ---- 内部流程 ----

def _is_menu_open(img):
    """判定组队菜单是否已打开（队伍信息模板优先，菜单文案 OCR 兜底）。

    申请加入面板打开时不算菜单（其区域内的「附近队伍」等文案会被
    OCR 兜底误判成菜单，先排除）。
    """
    if game_map.find_template_in(img, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        return False
    if game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD) is not None:
        return True
    items, _ = game_map.ocr_region(img, TEAM_MENU_REGION)
    return any(any(mt in txt for mt in TEAM_MENU_TEXTS) for txt, _, _ in items)


def _close_apply_panel(win, retries=3):
    """关闭申请加入面板：点击 zudui_close（与队伍面板同款关闭按钮）。

    面板不处理（暂不处理申请流程），关闭后让外层重试。
    带重试（2026-11-07 日志 12:27 教训：单次点击未命中/按钮模板
    偶发未命中会静默留下面板遮挡后续流程）——每次重试前先查面板
    标志，已不在屏即达成目标；按钮找不到或点击后面板仍在则重试，
    全部失败返回 False 由调用方记日志。
    返回 (是否关闭, 描述)。
    """
    for attempt in range(1, retries + 1):
        if game_map.find_template(win, ZUDUI_SHENQINGJIARU_ICON,
                                  _TEMPLATE_THRESHOLD) is None:
            return True, "申请加入面板已关闭"
        pt = game_map.find_template(win, ZUDUI_CLOSE_ICON, _TEMPLATE_THRESHOLD)
        if pt is None:
            continue
        human_move(win.rect.left + pt[0], win.rect.top + pt[1])
        time.sleep(0.15)  # 悬停让按钮进入可点击状态（与队伍面板关闭同款交互）
        human_click(win, pt[0], pt[1])
        for _ in range(_PANEL_WAIT_TRIES):
            time.sleep(_PANEL_WAIT_INTERVAL)
            if game_map.find_template(win, ZUDUI_SHENQINGJIARU_ICON,
                                      _TEMPLATE_THRESHOLD) is None:
                return True, f"申请加入面板已关闭（第 {attempt} 次点击生效）"
    return False, f"申请加入面板关闭失败（重试 {retries} 次仍在）"


# ---- 补人流程（领取被拒后从申请加入面板允许申请人；五倍/修罗复用） ----
APPLY_FAIL_TEXT = "对方已经加入其他队伍"    # 允许失败提示（屏幕中间，出现即换人）
APPLY_PANEL_WAIT_TRIES = 6                 # 点击组队按钮后等待申请加入面板出现（0.5s 一次）
APPLY_FAIL_WAIT = 2.5                      # 点击允许后等待失败提示的窗口（秒）
APPLY_ROW_OFFSET = (-100, 45, 280, 350)    # 申请人列表区域（相对面板标志左上角；实测
                                            # 固定布局：表头 y+51、列表行 y+67 起、
                                            # 允许/拒绝按钮行 y+356）；顶部 45 覆盖
                                            # 表头行——表头在场是「面板确实打开」的
                                            # 校验信号（2026-11-07 教训见 _apply_rows）
APPLY_YUNXU_THRESHOLD = 0.8                # 允许按钮模板匹配阈值（实测 1.00）


def _apply_panel_flag(img):
    """申请加入面板标志模板位置（打开时返回中心坐标，未打开返回 None）。"""
    return game_map.find_template_in(img, ZUDUI_SHENQINGJIARU_ICON,
                                     _TEMPLATE_THRESHOLD)


def is_definitive_not_leader(info):
    """确定性非队长判定：未组队 / 面板打开且无解散按钮。

    瞬态类失败（菜单打不开、按钮未命中、截图失败、窗口被遮挡等）
    不在此列——瞬时问题不应把队长永久降级为保持模式（实测：手动
    遮挡一次窗口致按钮模板分数 0.51，重试两次后误转保持模式——
    2026-10-14）。调用方对非确定性失败应持续重查而非退出。
    """
    return "未组队" in info or "非队长" in info


def has_pending_apply(img):
    """画面中是否存在申请人证据：申请加入面板已开，或组队按钮为
    申请待处理态（zudui_item2）。供监控循环决定是否先走补人。"""
    if _apply_panel_flag(img) is not None:
        return True
    return game_map.find_template_in(img, ZUDUI_ITEM2_ICON,
                                     ZUDUI_ITEM2_THRESHOLD) is not None


def _apply_rows(img, fx, fy):
    """申请人列表行（面板标志下方固定偏移区域 OCR）。

    每名申请人占一行，行内名字/等级/族群性别为同一 y 的多个 OCR 框
    （实测名字与等级同 y，旧实现按行序取首框会取到等级框——日志
    显示「允许飞升160级」而非人名）。按 y 聚簇（≤12px 同一行），
    簇内优先取非等级行作名字（等级文案可被 _parse_level 识别）。
    返回 [{"yc": 行中心 y, "name": 名字文案, "rect": 名字框}]，
    按 y 排序；排除表头行（名字/等级/族群/性别）。
    fx/fy 为面板标志匹配中心。

    双校验（2026-11-07 日志 12:27 教训）：面板关闭后标志模板仍误
    命中时，行区域读到的场景按钮文字「任务」被当申请人点击，实际
    点中任务领取入口、误开领取对话框卡死流程。① 区域内必须见表头
    （名字/等级/族群/性别任一）——表头在场才证明面板确实打开；
    ② 每行必须带等级（申请人列表每行 = 名字+等级）——无等级的单行
    文字（场景按钮标签等）不是申请人。任一校验不过返回空列表，
    放弃本轮点击，由监控循环重查自愈（漏一批申请人无害，误点场景
    有真实卡死危害）。
    """
    l, t, r, b = (int(fx + APPLY_ROW_OFFSET[0]), int(fy + APPLY_ROW_OFFSET[1]),
                  int(fx + APPLY_ROW_OFFSET[2]), int(fy + APPLY_ROW_OFFSET[3]))
    items, _ = game_map.ocr_region(img, (l, t, r, b))
    has_header = any(h in txt for txt, rect, score in items
                     if score >= 0.55 for h in ("名字", "等级", "族群", "性别"))
    if not has_header:
        return []
    lines = []
    for txt, rect, score in items:
        if score < 0.55:
            continue
        if any(h in txt for h in ("名字", "等级", "族群", "性别")):
            continue  # 表头行
        lines.append((txt, rect))
    lines.sort(key=lambda it: it[1][1])
    clusters = []
    for txt, rect in lines:
        yc = (rect[1] + rect[3]) // 2
        if clusters and abs(yc - clusters[-1]["yc"]) <= 12:
            clusters[-1]["lines"].append((txt, rect))
        else:
            clusters.append({"lines": [(txt, rect)], "yc": yc})
    rows = []
    for c in clusters:
        c["lines"].sort(key=lambda it: it[1][0])
        if not any(_parse_level(t[0]) is not None for t in c["lines"]):
            continue  # 无等级行不是申请人（场景文字误入）
        name_line = next((it for it in c["lines"] if _parse_level(it[0]) is None),
                         None)
        if name_line is None:
            name_line = c["lines"][0]
        # 身份 = 名字+等级的规范化文本拼接（OCR 抖动缓冲：单行读错时
        # 另一行仍可匹配；y 坐标在行位移后失效，不能作身份）
        identity = norm_text(name_line[0])
        for txt, _r in c["lines"]:
            if _parse_level(txt) is not None:
                identity += norm_text(txt)
        rows.append({"yc": c["yc"], "name": name_line[0],
                     "id": identity, "rect": name_line[1]})
    return rows


def _same_applicant(id1, id2):
    """两申请人身份是否同一人（模糊比对，抗 OCR 抖动）。"""
    if not id1 or not id2:
        return False
    return difflib.SequenceMatcher(None, id1, id2).ratio() >= 0.7


def fill_team(win, target=5, max_allow=1):
    """从申请加入面板允许申请人补齐队伍（通用，五倍/修罗复用）。

    前置：领取被拒（人数不足）、角色为队长。流程：
    1. 确保申请加入面板打开（点击组队按钮；人数不足时点开的是申请
       加入面板而非组队菜单——_ensure_menu_open 已有该判定经验）
    2. 循环（最多允许 max_allow 人）：OCR 申请人列表 → 点选第一个
       未失败过的申请人 → 点击「允许」按钮（zudui_yunxu 模板）→
       等待检测「对方已经加入其他队伍」提示（屏幕中间；出现则点掉、
       该申请人记失败换下一个）→ 成功判定 = 无失败提示且该行从
       列表消失（行仍在说明允许未生效，同样换下一个）
    3. 结束关闭申请加入面板（不留面板遮挡后续流程），返回
       (是否允许了至少一人, 描述)；调用方随后重试领取，以游戏
       的「前途凶险」对话框作最终裁判（每允许一人重试一次）
    """
    try:
        return _fill_team_loop(win, target, max_allow)
    finally:
        ok_close, close_info = _close_apply_panel(win)
        if not ok_close:
            # 关闭失败必须留痕（此前静默丢弃：面板残留遮挡领取入口、
            # 日志无迹可查，2026-11-07 教训）
            log(f"[组队] 补人结束关闭申请加入面板失败: {close_info}", "WARN")


def _fill_team_loop(win, target, max_allow):
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    flag = _apply_panel_flag(img)
    if flag is None:
        # 面板未开：点击组队按钮。按钮为申请待处理态（zudui_item2，
        # 有人申请加入）时点击直接弹出申请加入面板——该状态即确定性
        # 队长（用户实测）；普通态点击弹出组队菜单
        pt = game_map.find_template(win, ZUDUI_ITEM2_ICON, ZUDUI_ITEM2_THRESHOLD)
        if pt is not None:
            record_leader_status(True)
            log("[组队] 组队按钮为申请待处理态（zudui_item2），队长确认，点击打开申请面板", "INFO")
        else:
            pt = game_map.find_template(win, ZUDUI_ITEM_ICON, _TEMPLATE_THRESHOLD)
            if pt is None:
                return False, "未找到组队按钮（zudui_item/zudui_item2）"
        human_click(win, pt[0], pt[1])
        flag = None
        for _ in range(APPLY_PANEL_WAIT_TRIES):
            time.sleep(0.5)
            img = win.capture(force_activate=False)
            if img is None:
                continue
            flag = _apply_panel_flag(img)
            if flag is not None:
                break
        if flag is None:
            if img is not None and _is_menu_open(img):
                # 菜单打开 = 已组队但暂无申请人：申请加入面板只在有人
                # 申请时出现，与队伍是否满员无关（实测 1/5 队伍点组队
                # 按钮弹的是普通菜单）——返回后由监控循环等待并重查
                return False, "暂无申请人（申请加入面板未出现）"
            return False, "申请加入面板未出现"
    failed = set()  # 失败过的申请人身份（名字+等级规范化文本）。y 坐标
                    # 在允许成功后列表整体上移时失效（实测误判失败并
                    # 误跳过下一个人），不能作键；身份用模糊比对
    allowed = 0
    while allowed < max_allow:
        # 每轮迭代重截帧并重查面板标志：面板可能在两轮迭代间关闭
        # （申请撤回/前次允许实际生效），标志模板仍会误命中场景
        # （2026-11-07 日志 12:27「任务」被当申请人点击的根因路径）
        # ——标志消失即停止点选，绝不在面板已关闭的画面上点行
        img = win.capture(force_activate=False)
        if img is None:
            break
        flag = _apply_panel_flag(img)
        if flag is None:
            return (allowed > 0,
                    f"申请加入面板已关闭（已允许 {allowed} 人）"
                    if allowed else "申请加入面板已关闭（无申请人处理）")
        rows = _apply_rows(img, flag[0], flag[1])
        tgt = next((r for r in rows
                    if not any(_same_applicant(r["id"], fid) for fid in failed)),
                   None)
        if tgt is None:
            return (allowed > 0,
                    f"已允许 {allowed} 名申请人后无可选申请人"
                    if allowed else "申请列表为空或全部失败")
        rect0 = tgt["rect"]
        human_click(win, (rect0[0] + rect0[2]) // 2, (rect0[1] + rect0[3]) // 2)
        time.sleep(0.3)  # 等待行选中态刷新
        pt = game_map.find_template(win, ZUDUI_YUNXU_ICON,
                                    threshold=APPLY_YUNXU_THRESHOLD)
        if pt is None:
            return (allowed > 0, f"未找到允许按钮（zudui_yunxu），已允许 {allowed} 人")
        human_click(win, pt[0], pt[1])
        # 失败提示检测（屏幕中间对话框区域）
        fail = False
        deadline = time.time() + APPLY_FAIL_WAIT
        while time.time() < deadline:
            img = win.capture(force_activate=False)
            if img is None:
                time.sleep(0.5)
                continue
            items, _ = game_map.ocr_region(img, task_tracker.DIALOG_REGION)
            if _dialog_text_present(items, APPLY_FAIL_TEXT):
                fail = True
                break
            time.sleep(0.5)
        if fail:
            log(f"[组队] 允许「{tgt['name']}」失败（对方已加入其他队伍），换下一个", "WARN")
            failed.add(tgt["id"])
            time.sleep(0.5)  # 失败提示自动消失（不点屏幕中间——用户确认无需点击）
            continue
        # 成功校验：该申请人从列表消失（按身份模糊比对——允许成功后
        # 列表整体上移，y 坐标不能作判据）
        img2 = win.capture(force_activate=False)
        rows2 = _apply_rows(img2, flag[0], flag[1]) if img2 is not None else []
        if any(_same_applicant(r["id"], tgt["id"]) for r in rows2):
            log(f"[组队] 允许「{tgt['name']}」后该申请人仍在，视为未生效换下一个", "WARN")
            failed.add(tgt["id"])
            continue
        allowed += 1
        log(f"[组队] 已允许「{tgt['name']}」加入队伍（本轮第 {allowed} 人）", "SYS")
        # 下一轮迭代顶部重截帧并重查面板标志（img 不复用，防陈旧帧
        # 误导行识别与标志判定）
    return True, f"已允许 {allowed} 名申请人"


def _wait_menu(win):
    """等待组队菜单弹出，返回 (是否打开, 最新截图)。"""
    for _ in range(_MENU_WAIT_TRIES):
        time.sleep(_MENU_WAIT_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None:
            continue
        if _is_menu_open(img):
            return True, img
    return False, None


def _ensure_menu_open(win):
    """确保组队菜单已打开：已打开则复用，未打开才点击组队按钮。

    菜单未关闭时再点组队按钮会把它关掉（实机踩坑），故必须先检查再决定
    是否点击。队伍人数不足时点击组队按钮可能打开申请加入面板：
    面板已开/被点开都只关闭不处理，本轮返回未打开交给外层重试。
    返回 (是否打开, 最新截图)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, None
    # 申请加入面板开着会挡住组队菜单：直接关闭，本轮不做其他处理
    if game_map.find_template_in(img, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        ok, info = _close_apply_panel(win)
        log(f"[组队] {info}", "SYS" if ok else "WARN")
        return False, None
    if _is_menu_open(img):
        return True, img
    pt = game_map.find_template(win, ZUDUI_ITEM_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        # 按钮可能处于申请待处理态（zudui_item2，有人申请加入）：
        # 该状态点击打开的是申请加入面板而非菜单，菜单打不开——直接
        # 返回，由监控循环先走补人（用户 2026-10-14 实测根因；监控
        # 循环入口 check_team_min 开面板前已先查该证据，此处兜底
        # 其余调用方）
        diag = win.capture(force_activate=False)
        if diag is not None and game_map.find_template_in(
                diag, ZUDUI_ITEM2_ICON, ZUDUI_ITEM2_THRESHOLD) is not None:
            log("[组队] 组队按钮为申请待处理态（zudui_item2），菜单无法打开，应先补人", "INFO")
            return False, None
        # 诊断：组队按钮两个形态都未命中——记录最佳匹配分（区分「按钮
        # 被遮挡/换屏」分数极低 与「渲染差异」分数略低于阈值），并存
        # 现场截图（tmp_err_img.png，gitignore 已忽略）供人工排查
        try:
            if diag is not None:
                score, _pos = game_map.best_match_in(diag, ZUDUI_ITEM_ICON)
                score2, _ = game_map.best_match_in(diag, ZUDUI_ITEM2_ICON)
                log(f"[组队] 组队按钮未命中：zudui_item 最佳 {score:.2f}、"
                    f"zudui_item2 最佳 {score2:.2f}（阈值 {_TEMPLATE_THRESHOLD}），"
                    f"现场已存 tmp_err_img.png", "WARN")
                try:
                    diag.save("tmp_err_img.png")
                except Exception:
                    pass
        except Exception as e:
            log(f"[组队] 组队按钮诊断失败: {e}", "WARN")
        return False, None
    human_click(win, pt[0], pt[1])
    opened, img2 = _wait_menu(win)
    if opened:
        return True, img2
    # 菜单未打开：可能点开了申请加入面板（队伍人数不足时出现），
    # 检测到则直接点击关闭，不处理该面板
    if game_map.find_template(win, ZUDUI_SHENQINGJIARU_ICON, _TEMPLATE_THRESHOLD) is not None:
        ok, info = _close_apply_panel(win)
        log(f"[组队] {info}", "SYS" if ok else "WARN")
    return False, None


def _has_duiwuxinxi(img):
    """菜单中是否出现「队伍信息」（模板优先，OCR 兜底）。"""
    if game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD) is not None:
        return True
    items, _ = game_map.ocr_region(img, TEAM_MENU_REGION)
    return any("队伍信息" in txt for txt, _, _ in items)


def _panel_open_in(img):
    """帧内判定队伍面板是否已打开（面板专属信号判定）。

    不依赖关闭按钮——多个面板共用同款关闭按钮（申请加入面板即同款），
    不具区分性。判定只看面板专属信号：
    1. duiwumianban 面板标志（随成员数退化，5 人队实测 0.436~0.683
       抖动，阈值 0.6 时灵时不灵）
    2. 不达标时以面板专属文案（队标/暂时离队/招募队友）OCR 兜底——
       这些文案只在队伍面板内出现，申请加入面板与场景界面均无
    """
    if img is None:
        return False
    if _best_match(img, DUIWU_MIANBAN_ICON)[0] >= _PANEL_FLAG_THRESHOLD:
        return True
    items, _ = game_map.ocr_region(img, RACE_REGION)
    return any(any(t in txt for t in _PANEL_TEXTS) for txt, _, _ in items)


def _is_panel_open(win):
    """队伍面板是否已打开（截帧后按 _panel_open_in 判定）。"""
    return _panel_open_in(win.capture(force_activate=False))


def _wait_panel(win):
    """等待队伍面板打开（duiwumianban 模板）。"""
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if _is_panel_open(win):
            return True
    return False


def _parse_level(txt):
    """解析转生等级文案，返回 (转生段, 等级数字) 或 None。

    转生段为 未转/1转/2转/3转/飞升；容 OCR 噪声（木转→未转、
    飞异→飞升、数字 12O→120）。
    """
    m = _LEVEL_RE.search(txt)
    if not m:
        return None
    stage = m.group(1)
    if stage == "木转":
        stage = "未转"
    elif stage.startswith("飞"):
        stage = "飞升"
    digits = m.group(2).translate(_LEVEL_DIGIT_MAP)
    if not digits.isdigit():
        return None
    return stage, int(digits)


def _best_match(img, icon):
    """帧内模板匹配全局最高分与其中心坐标（maxcnt=1 快速模式）。

    与 find_template_in 同源（FindImages/TM_CCOEFF_NORMED），返回
    (confidence, (x, y))；无匹配时返回 (0.0, (0, 0))（调用方先以
    confidence ≥ 阈值 过滤，无匹配时坐标不会被使用）。
    """
    return game_map.best_match_in(img, icon)


def _classify_leader_frame(img):
    """帧内队伍身份三分类（双模板比分数）。

    解散队伍与离开队伍按钮会互误匹配（实测 0.807~0.834 ≥ 0.8 阈值），
    必须比分数区分两者。返回 (分类, (解散分, 离开分))：
    - leader 队长：解散分数达标且更高
    - member 队员：离开分数达标且更高（真实队员面板离开按钮稳定高分，
      确定性非队长判据）
    - unclear 按钮未命中：两分数均不达标，面板内容不可读（渲染中/
      瞬时遮挡/帧异常）——不能据此判非队长（2026-11-05 实测队长面板
      单帧误判非队长，核心流程被永久挂起）
    """
    jiesan_score, _ = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, _ = _best_match(img, LIKAI_DUIWU_ICON)
    if jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score:
        return "leader", (jiesan_score, likai_score)
    if likai_score >= _TEMPLATE_THRESHOLD and likai_score > jiesan_score:
        return "member", (jiesan_score, likai_score)
    return "unclear", (jiesan_score, likai_score)


def _is_leader_in_frame(img):
    """帧内队长判定（见 _classify_leader_frame，仅取 leader 分类）。"""
    return _classify_leader_frame(img)[0] == "leader"


_LEADER_RECHECK_TRIES = 3      # 按钮未命中复核次数（0.5s 间隔）
_LEADER_RECHECK_INTERVAL = 0.5
# member 分类直接采信的离开分数下限：真实离开按钮实测 ~0.985；低于
# 0.9 可能是队长面板上离开模板的弱误匹配，须复核重判
_MEMBER_CONFIRM_SCORE = 0.9


def _recheck_leader_frames(win, first_scores):
    """队长判定复核：重截重判至多 _LEADER_RECHECK_TRIES 次。

    面板渲染滞后/打开动画帧/瞬时遮挡等单帧抖动会让解散/离开两按钮
    同时失配（unclear），重截等面板稳定后命中；真实队员面板离开按钮
    持续达标，复核即命中 member。复核帧必须面板可确认——帧内无面板
    信号（面板已自关或面板检测误报）立即停，重截无意义。
    返回 (分类, (解散分, 离开分))；全程不明返回 ("unclear", 末帧分数)。
    """
    scores = [first_scores]
    last_img = None
    for _ in range(_LEADER_RECHECK_TRIES):
        time.sleep(_LEADER_RECHECK_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None or not _panel_open_in(img):
            break
        last_img = img
        kind, s = _classify_leader_frame(img)
        scores.append(s)
        if kind != "unclear":
            log(f"[组队] 队长判定复核命中 {kind}"
                f"（解散/离开分: {'、'.join(f'{a:.2f}/{b:.2f}' for a, b in scores)}）",
                "INFO")
            return kind, s
    log(f"[组队] 队长判定按钮未命中"
        f"（解散/离开分: {'、'.join(f'{a:.2f}/{b:.2f}' for a, b in scores)}，"
        f"阈值 {_TEMPLATE_THRESHOLD}），按无法判定处理", "WARN")
    # 全程不明：存现场帧供人工排查（与组队按钮诊断共用 tmp_err_img.png）
    try:
        if last_img is not None:
            last_img.save("tmp_err_img.png")
    except Exception:
        pass
    return "unclear", scores[-1]


def _confirm_leader_kind(win, kind, scores):
    """首帧分类复核：unclear 或弱 member（离开分数 < _MEMBER_CONFIRM_SCORE）
    重截重判（见 _recheck_leader_frames），返回 (分类, 分数)。"""
    if kind == "unclear" or (kind == "member" and scores[1] < _MEMBER_CONFIRM_SCORE):
        return _recheck_leader_frames(win, scores)
    return kind, scores


def _find_banner_rect(img):
    """定位 duiwumianban 标志模板，返回 (分数, (l, t, r, b)) 或 (0.0, None)。

    成员区域以模板匹配框（rect）锚定——FindImages 的 x/y 是中心，
    rect 才是权威边界；面板位置会漂移，绝对坐标不可用。
    """
    results = FindImages.find_all_template(R.img(DUIWU_MIANBAN_ICON),
                                           im_source=img,
                                           threshold=0.0, max_res=1)
    if not results:
        return 0.0, None
    r = results[0]
    return float(r.confidence), (int(r.rect[0]), int(r.rect[1]),
                                 int(r.rect[2]), int(r.rect[3]))


def _member_regions(banner_rect):
    """标志模板锚定的 5 个成员区域矩形 [(l, t, r, b), ...]（第 1 区域=队长）。"""
    bl, bt, br, _bb = banner_rect
    x0 = br + MEMBER_REGION_OFFSET
    y0 = bt + MEMBER_REGION_OFFSET
    pitch = MEMBER_REGION_W + MEMBER_REGION_GAP
    return [(x0 + i * pitch, y0,
             x0 + i * pitch + MEMBER_REGION_W,
             y0 + MEMBER_REGION_H) for i in range(MEMBER_REGION_COUNT)]


def _tpl_path(icon):
    """模板文件路径；未提供时返回不存在的路径（调用方降级）。

    与加载器同根（find_template_in 内部走 R.img 按 R.get_root() 解析；
    打包后锚定解压目录/可执行文件目录，os.getcwd() 会错位）。
    不直接调 R.img——缺文件时 R 会 print 警告，GBK 管道下抛
    UnicodeEncodeError（2026-11-05 实测）；用 R.get_root() 拼接无此问题。
    """
    return pathlib.Path(R.get_root()) / "res" / "img" / icon


def _empty_tpl_path(icon=EMPTY_REGION_ICON):
    """空位模板文件路径（见 _tpl_path）。"""
    return _tpl_path(icon)


_EMPTY_TPL_MISSING_LOGGED = False


def _empty_icons_present():
    """存在的空位模板列表（可能为空：全部缺失走 OCR 兜底）。"""
    return [icon for icon in EMPTY_REGION_ICONS if _empty_tpl_path(icon).exists()]


def _log_missing_tpls():
    """空位模板缺失提示（每进程一次）。"""
    global _EMPTY_TPL_MISSING_LOGGED
    if _EMPTY_TPL_MISSING_LOGGED:
        return
    _EMPTY_TPL_MISSING_LOGGED = True
    missing = [icon for icon in EMPTY_REGION_ICONS if not _empty_tpl_path(icon).exists()]
    if not missing:
        return
    if len(missing) == len(EMPTY_REGION_ICONS):
        log(f"[组队] 空位模板全部未提供，空位判定用区域 OCR 兜底"
            f"（放入 res/img 后自动启用模板判定）", "INFO")
    else:
        log(f"[组队] 空位模板 {len(missing)}/{len(EMPTY_REGION_ICONS)} 个缺失"
            f"（{'、'.join(missing)}），按现有模板判定", "INFO")


def _region_is_empty(img, rect):
    """帧内判定成员区域是否为空位（无人）：双空位模板任一命中即空。

    空位占位图有两种渲染形态（wurenquyu/wurenquyu2），任一匹配达标
    即判空；模板全部缺失时返回 None（调用方用 OCR 内容兜底）。
    """
    icons = _empty_icons_present()
    if not icons:
        _log_missing_tpls()
        return None
    _log_missing_tpls()
    l, t, r, b = rect
    pl, pt, pr, pb = EMPTY_REGION_PAD
    crop = img.crop((l - pl, t - pt, r + pr, b + pb))
    for icon in icons:
        if game_map.find_template_in(crop, icon, EMPTY_REGION_THRESHOLD) is not None:
            return True
    return False


def _parse_member_region(img_np, img, rect, idx, glyphs):
    """解析单个成员区域（区域=一个角色，不跨区域拼信息）。

    返回 (种族词或None, 等级文案或None, 字形库是否新增样本)。
    种族：OCR 竖排文字框（高≥宽，排除横向名字/帮派行误匹配）；
    第 1 区域选中态金色文字 OCR 读不到时用字形库形状匹配兜底。
    等级：OCR 区域内的转生等级文案。
    """
    l, t, r, b = rect
    items, _ = game_map.ocr_region(img, (l, t, r + MEMBER_RACE_SPILL, b))
    race, race_rect = None, None
    lv = None
    for txt, rect2, score in items:
        if score < _OCR_MIN_SCORE:
            continue
        if race is None and (rect2[3] - rect2[1]) >= (rect2[2] - rect2[0]):
            for w in RACE_WORDS:
                if w in txt:
                    race, race_rect = w, rect2
                    break
        if lv is None and _parse_level(txt) is not None:
            lv = _parse_level(txt)
            lv = f"{lv[0]}{lv[1]}"
    changed = False
    if race is not None:
        changed = _harvest_glyphs(img_np, [(race, race_rect)], glyphs)
    elif idx == 0:
        # 第 1 区域金色选中态：固定裁剪 + 金色掩码字形库匹配兜底
        gl_rect = (RACE_GLYPH_REL[0] + l, RACE_GLYPH_REL[1] + t,
                   RACE_GLYPH_REL[2] + l, RACE_GLYPH_REL[3] + t)
        matched, score = _match_glyph(img_np, glyphs, gl_rect)
        if matched is not None and score >= _RACE_GLYPH_MATCH:
            race = matched
    return race, lv, changed


# ---- 种族字形库（第 1 列金色种族文字的识别） ----
# 第 1 列种族文字为金色（队长标记色？），灰度对比度低、OCR 读不到；
# 但 5 列种族文字位置固定（列中心 ≈465/608/753/894/1038）且字体相同。
# 方案：可读列的 OCR 结果自动采集 12 个种族的字形样本入库（随挂机
# 积累），第 1 列按固定坐标裁剪 + 金色掩码提取字形，与库做形状匹配
# 识别（颜色归一化后与颜色无关）。库在配置目录，不入 git。
_GLYPH_SIZE = (23, 46)  # 字形归一化目标尺寸（提取时 NEAREST 缩放）


def _glyph_path():
    """字形库文件路径（配置目录，随挂机自动积累）。"""
    return pathlib.Path(os.getcwd()) / "team_race_glyphs.npz"


def _load_glyphs():
    """加载字形库 {种族: 二值数组}；文件不存在/损坏返回空。"""
    path = _glyph_path()
    if not path.exists():
        return {}
    try:
        data = np.load(path)
        return {str(k): data[k].astype(np.uint8) for k in data.files}
    except Exception:
        return {}


def _save_glyphs(glyphs):
    """保存字形库（写临时文件后原子替换）。"""
    path = _glyph_path()
    tmp = path.with_suffix(".tmp")
    try:
        np.savez_compressed(tmp, **glyphs)
        os.replace(tmp, path)
    except Exception:
        pass


def _extract_glyph(np_img, rect, gold=False):
    """从矩形提取二值字形（归一化到 _GLYPH_SIZE），笔画过少返回 None。

    gold=True：第 1 列金色文字掩码（R/G 高、B 低）；
    gold=False：可读列亮像素掩码（max>150）。
    """
    l, t, r, b = [int(v) for v in rect]
    seg = np_img[t:b, l:r].astype(int)
    if seg.size == 0:
        return None
    if gold:
        mask = ((seg[:, :, 0] > 160) & (seg[:, :, 1] > 140) & (seg[:, :, 2] < 170)
                & (seg[:, :, 0] > seg[:, :, 2] + 40) & (seg[:, :, 1] > seg[:, :, 2] + 30))
    else:
        mask = seg.max(axis=2) > 150
    if mask.sum() < 30:
        return None
    small = Image.fromarray((mask * 255).astype(np.uint8)).resize(
        _GLYPH_SIZE, Image.NEAREST)
    return np.asarray(small) > 0


def _match_glyph(np_img, glyphs, rect):
    """金色种族字形与库匹配，返回 (种族, 分数) 或 (None, 0.0)。

    rect：帧内绝对矩形（第 1 区域按 RACE_GLYPH_REL 相对区域左上角偏移）。
    """
    if not glyphs:
        return None, 0.0
    query = _extract_glyph(np_img, rect, gold=True)
    if query is None:
        return None, 0.0
    qf = query.astype(np.float32)
    best, best_score = None, 0.0
    for race, g in glyphs.items():
        gf = g.astype(np.float32)
        if gf.shape != qf.shape:
            continue
        score = float(cv2.matchTemplate(qf, gf, cv2.TM_CCOEFF_NORMED).max())
        if score > best_score:
            best, best_score = race, score
    return best, best_score


def _harvest_glyphs(img_np, race_items, glyphs):
    """从可读种族列采集字形样本入库（已有种族的样本跳过）。

    img_np：面板帧（RGB numpy）；返回库是否变化（需要落盘）。
    """
    changed = False
    for race, rect in race_items:
        if race in glyphs or race == "?":
            continue
        # OCR 框外扩 2px 再采集，防笔画贴边被裁
        l, t, r, b = rect
        g = _extract_glyph(img_np, (l - 2, t - 2, r + 2, b + 2))
        if g is None:
            continue
        glyphs[race] = g.astype(np.uint8)
        changed = True
    return changed


def _empty_region_score(img, rect):
    """空位双模板在区域内的最佳匹配分（取两模板较高者；全部缺失返回 None）。

    可疑原因日志用（_region_is_empty 只给布尔结果）；与
    _region_is_empty 同裁剪（含 EMPTY_REGION_PAD 外扩）。
    """
    icons = _empty_icons_present()
    if not icons:
        return None
    l, t, r, b = rect
    pl, pt, pr, pb = EMPTY_REGION_PAD
    crop = img.crop((l - pl, t - pt, r + pr, b + pb))
    best = 0.0
    for icon in icons:
        results = FindImages.find_all_template(R.img(icon), im_source=crop,
                                               threshold=0.0, max_res=1)
        if results:
            best = max(best, float(results[0].confidence))
    return best


def _scan_member_regions(img_np, img, regions, glyphs):
    """逐区域扫描成员，返回 (成员列表, 人数, 可疑原因或None, 字形库是否变化)。

    成员列表 = [(种族或'?', 等级文案或None), ...]；人数 = 首空位序号
    （区域自第 1 位起连续填充，首空位之后不再解析）。
    空位判定：优先空位模板；模板缺失时以区域 OCR 无种族且无等级兜底。
    可疑（原因非 None）场景——面板数据渲染中：
    - 空位模板判定某区域有人但 OCR 读不到任何信息
    - 空位之后又出现有内容区域（与连续填充矛盾）
    调用方应重截一帧再扫。
    """
    members = []
    changed = False
    for i, rect in enumerate(regions):
        tpl_empty = _region_is_empty(img, rect)
        if tpl_empty is True:
            # 模板判空：其后区域应全空，抽查下一个区域防数据渲染中
            if i + 1 < MEMBER_REGION_COUNT and not _region_is_empty(img, regions[i + 1]):
                s = _empty_region_score(img, regions[i + 1])
                reason = (f"区域{i + 1}命中空位模板，但区域{i + 2}仍有人"
                          f"（空位分 {s:.2f} < {EMPTY_REGION_THRESHOLD}），数据渲染中")
                return members, i, reason, changed
            return members, i, None, changed
        race, lv, c = _parse_member_region(img_np, img, rect, i, glyphs)
        changed = changed or c
        if race is None and lv is None:
            if tpl_empty is False:
                # 模板确认有人但 OCR 读不到：数据渲染中，重截再扫
                s = _empty_region_score(img, rect)
                reason = (f"区域{i + 1}空位模板未命中（分数 {s:.2f}）"
                          f"且区域 OCR 无种族/等级文案，数据渲染中")
                return members, i, reason, changed
            # 模板缺失（None）：OCR 兜底判空，抽查下一区域防漏
            if i + 1 < MEMBER_REGION_COUNT:
                race2, lv2, c2 = _parse_member_region(img_np, img, regions[i + 1],
                                                      i + 1, glyphs)
                changed = changed or c2
                if race2 is not None or lv2 is not None:
                    reason = f"区域{i + 1}OCR 判空，但区域{i + 2}仍有成员（渲染中）"
                    return members, i, reason, changed
            return members, i, None, changed
        members.append((race or "?", lv))
    return members, MEMBER_REGION_COUNT, None, changed


def _scan_panel_img(img):
    """帧内队伍面板扫描：返回 (成员列表, 人数, 是否成功, 可疑原因或None)。

    单帧完成：标志模板定位 → 5 区域划分 → 逐区域解析（空位模板优先）。
    字形库新增样本在扫描后落盘。
    """
    _score, banner = _find_banner_rect(img)
    if banner is None:
        return [], 0, False, "标志模板未命中（面板不可见或未渲染）"
    glyphs = _load_glyphs()
    img_np = np.asarray(img)
    members, count, reason, changed = _scan_member_regions(
        img_np, img, _member_regions(banner), glyphs)
    if changed:
        _save_glyphs(glyphs)
    if reason:
        return members, count, False, reason
    return members, count, True, None


# 可疑扫描重截间隔：面板数据渲染滞后自愈窗口（16:48:38 实测 1 人队
# 区域 2 空位模板未命中 + OCR 无文案、0.5s 单次重试仍未渲染完——
# 旧 0.5s×1 太短；重试总预算 ~2.5s，仅失败路径消耗）
_SCAN_RETRY_INTERVALS = (0.5, 1.0, 1.0)


def _analyze_panel_frame(win, img):
    """帧内解析队伍面板（含重截兜底），返回 (是否成功, 成员列表, 人数)。

    面板数据渲染滞后（空位模板判定有人但 OCR 空/空位后有内容）时按
    _SCAN_RETRY_INTERVALS 重截再扫；仍失败返回已得部分结果并打
    可疑原因日志、存现场帧 tmp_team_err.png 供人工排查。
    """
    members, count, ok, reason = _scan_panel_img(img)
    for interval in _SCAN_RETRY_INTERVALS:
        if ok:
            break
        time.sleep(interval)
        img2 = win.capture(force_activate=False)
        if img2 is None:
            continue
        members, count, ok, reason = _scan_panel_img(img2)
    if not ok:
        log(f"[组队] 成员扫描可疑: {reason}（重截 {len(_SCAN_RETRY_INTERVALS)} 次"
            f"仍不明确，现场帧已存 tmp_team_err.png）", "WARN")
        try:
            img2 = win.capture(force_activate=False)
            if img2 is not None:
                img2.save("tmp_team_err.png")
        except Exception:
            pass
    return ok, members, count


def _close_menu(win):
    """关闭组队菜单：菜单打开时再点一次组队按钮（开关式）即关闭。

    用户实测：菜单打开状态下点击 zudui_item 会让子菜单消失。
    关闭后校验菜单消失。返回 (是否关闭, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None or not _is_menu_open(img):
        return True, "组队菜单本就未打开"
    pt = game_map.find_template_in(img, ZUDUI_ITEM_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "未定位到组队按钮"
    human_click(win, pt[0], pt[1])
    for _ in range(_MENU_WAIT_TRIES):
        time.sleep(_MENU_WAIT_INTERVAL)
        img2 = win.capture(force_activate=False)
        if img2 is None or not _is_menu_open(img2):
            return True, "组队菜单已关闭"
    return False, "组队菜单关闭失败"


# ---- 面板开关 ----

def is_team_panel_open(win):
    """检查队伍面板是否已打开（duiwumianban 模板出现）。返回 (是否打开, 描述)。"""
    if _is_panel_open(win):
        return True, "队伍面板已打开"
    return False, "队伍面板未打开"


def open_team_panel(win):
    """打开队伍面板：确保组队菜单打开 → 点击菜单「队伍信息」。

    仅已组队时菜单才有「队伍信息」，未组队时无法打开面板。
    面板已打开时直接复用——实机踩坑：面板开着时点「队伍信息」会把它
    切换关闭（按钮开关式），必须先检查面板状态。
    打开后保持面板开启，由调用方 close_team_panel 关闭。
    返回 (是否成功, 描述)。
    """
    if _is_panel_open(win):
        return True, "队伍面板已打开（复用）"
    opened, img = _ensure_menu_open(win)
    if not opened:
        return False, "组队菜单未打开"
    if not _has_duiwuxinxi(img):
        _close_menu(win)
        return False, "未组队（菜单中无「队伍信息」）"
    pt = game_map.find_template_in(img, DUIWU_XINXI_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        _close_menu(win)
        return False, "菜单中未定位到「队伍信息」"
    human_click(win, pt[0], pt[1])
    if not _wait_panel(win):
        # 面板未打开：关闭为打开面板而拉起的组队菜单，避免菜单残留
        # 干扰后续操作（同函数上方两条失败路径同款处理）。
        # 原 _try_close_menu（ESC 兜底）已在静默通道落地时移除
        # （e9eaae7），此处调用点漏改导致 NameError（2026-11-04 五倍
        # 主循环异常日志）
        _close_menu(win)
        return False, "队伍面板未打开"
    return True, "队伍面板已打开"


def close_team_panel(win):
    """关闭队伍面板：鼠标移到面板关闭按钮 zudui_close 上，左键点击。

    点击后校验 duiwumianban 消失。返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        return True, "队伍面板本就未打开"
    pt = game_map.find_template(win, ZUDUI_CLOSE_ICON, _TEMPLATE_THRESHOLD)
    if pt is None:
        return False, "未找到面板关闭按钮（zudui_close）"
    human_move(win.rect.left + pt[0], win.rect.top + pt[1])
    time.sleep(0.15)  # 悬停让按钮进入可点击状态
    human_click(win, pt[0], pt[1])
    for _ in range(_PANEL_WAIT_TRIES):
        time.sleep(_PANEL_WAIT_INTERVAL)
        if not _is_panel_open(win):
            return True, "队伍面板已关闭"
    return False, "队伍面板关闭失败（duiwumianban 仍可见）"


# ---- 组队状态检查 ----

# 最近一次队伍身份检查结果（供战斗线程等读取，如抢盒子的队长规则）
_known_leader = None  # True=队长 / False=非队长 / None=未知（未检查过）


def record_leader_status(is_leader):
    """记录最近一次队伍身份检查结果（见 _known_leader）。"""
    global _known_leader
    _known_leader = is_leader


def known_leader():
    """最近一次队伍身份检查是否队长；未检查过返回 None。"""
    return _known_leader


def is_in_team(win):
    """检查是否已组队：打开组队菜单，出现「队伍信息」即已组队。

    菜单已打开时直接复用（不重复点击组队按钮）。
    返回 (是否在队伍中, 描述)。
    """
    opened, img = _ensure_menu_open(win)
    if not opened:
        return False, "组队菜单未打开（无法判定）"
    in_team = _has_duiwuxinxi(img)
    _close_menu(win)  # 再点一次组队按钮关闭菜单（开关式），未组队同样生效
    if in_team:
        return True, "已在队伍中"
    return False, "未组队（菜单中无「队伍信息」）"


def is_team_leader(win):
    """检查自己是否是队长：队伍面板打开后双模板比分数判定。

    流程：面板未打开则先打开（组队菜单 → 队伍信息），
    帧内比较解散队伍/离开队伍两个模板的匹配分（见 _classify_leader_frame，
    单模板会把离开按钮误判为解散按钮）；按钮未命中（unclear）时重截
    复核——单帧抖动不算确定性非队长（2026-11-05 队长面板单帧误判
    教训）。检查后关闭面板。
    返回 (是否队长, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    kind, scores = _classify_leader_frame(img)
    kind, scores = _confirm_leader_kind(win, kind, scores)
    close_team_panel(win)
    if kind == "leader":
        return True, "是队长（面板可见「解散队伍」）"
    if kind == "member":
        return False, "不是队长（面板无「解散队伍」）"
    return False, "队长无法判定（面板内解散/离开按钮未命中）"


# ---- 队伍种族分析 ----

def analyze_team_members(win):
    """分析队伍成员：固定 5 成员区域逐区解析（区域=一个角色）。

    前置条件：队伍面板已打开（duiwumianban 模板可见）。
    标志模板锚定 5 区域（124x265），每区域独立 OCR 种族/等级
    （种族限 RACE_WORDS 竖排文字；第 1 区域金色选中态用字形库
    形状匹配兜底）；空位模板（未提供时 OCR 内容兜底）截断。
    不再取消第 1 成员选中——点击取消选中多余且会引入额外状态变化。
    返回 (是否成功, [(种族, 等级文案或None), ...])；识别到 0 条视为失败。
    """
    if not _is_panel_open(win):
        return False, "队伍面板未打开"
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    ok, members, _count = _analyze_panel_frame(win, img)
    if not ok:
        return False, "成员区域识别失败（面板数据未渲染）"
    if not members:
        return False, "成员区域内未识别到成员信息"
    return True, members


def analyze_team_races(win):
    """分析队伍内种族构成（见 analyze_team_members，仅取种族列表）。

    前置条件：队伍面板已打开。返回 (是否成功, 种族列表)。
    """
    ok, members = analyze_team_members(win)
    if not ok:
        return False, members
    return True, [m[0] for m in members]


def check_team_conditions(win, min_members=None, required_races=None):
    """检查队伍是否满足任务条件（如修罗任务的种族/人数要求）。

    流程：组队菜单 → 队伍信息 → 队伍面板 → 种族 OCR 统计 → 与条件比对，
    完成后关闭面板。
    min_members: 人数下限（含自己，按成员区域数计），None 不检查
    required_races: 必须包含的种族列表（每个都须在队伍中出现，如
    ["男鬼", "女魔"]），None 不检查
    返回 (是否满足, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    ok, races = analyze_team_races(win)
    close_team_panel(win)
    if not ok:
        return False, f"种族识别失败: {races}"
    problems = []
    if min_members is not None and len(races) < min_members:
        problems.append(f"人数不足（{len(races)}/{min_members}）")
    if required_races:
        missing = [r for r in required_races if r not in races]
        if missing:
            problems.append(f"缺少种族: {'、'.join(missing)}")
    if problems:
        log(f"[组队] 队伍不满足条件: {'；'.join(problems)}"
            f"（当前 {len(races)} 人: {'、'.join(races)}）", "WARN")
        return False, "队伍不满足条件：" + "；".join(problems)
    return True, f"队伍满足条件（{len(races)} 人: {'、'.join(races)}）"


def check_team_min(win, min_members=None, allow_kick=False):
    """领取前队伍检查（通用，修罗/五倍等复用）：一次打开队伍面板完成
    队长判定、人数检查与成员信息采集。

    开面板前先查申请人证据（申请加入面板已开，或组队按钮为 zudui_item2
    申请待处理态）——该状态即确定性队长（只有队长会收到入队申请），
    直接按人数待补（wait）返回交监控循环补人，不算「队长无法判定」
    （2026-11-02 日志教训：zudui_item2 可点击打开申请面板查看队伍信息）。
    面板打不开（未组队等）与非队长均返回 not_leader。
    队长判定为双模板比分数（防解散/离开按钮互误匹配），按钮未命中
    （unclear）时重截复核（见 _confirm_leader_kind）——单帧抖动不算
    确定性非队长，返回「队长无法判定」由调用方按瞬态失败重查
    （2026-11-05 队长面板单帧误判非队长、核心流程永久挂起的教训）；
    人数与成员信息同帧完成（5 固定区域扫描，首空位截断），不再二次
    截窗与取消选中。
    allow_kick=True（仅任务流程传入，诊断检查不踢人）：队长且成员
    扫描稳定时在同一面板打开内执行超时暂离请离（todo2：按名字记账、
    超 5 分钟未归队从后往前连踢，面板不关闭），踢人后重扫成员——
    人数判定用最新值。
    min_members: 人数下限（含自己），None 不检查
    返回 (状态, 描述, 成员列表)；状态：ready 可领取 / wait 队长但人数
    不足 / not_leader 非队长或队长无法判定。
    """
    if not _is_panel_open(win):
        img0 = win.capture(force_activate=False)
        if img0 is not None and has_pending_apply(img0):
            # 申请人证据即确定性队长：菜单打不开不能算「队长无法判定」，
            # 按人数待补返回，由监控循环补人后重查（见 docstring）
            record_leader_status(True)
            return "wait", "队伍人数待补（有申请人待处理，确定性队长）", []
        ok, info = open_team_panel(win)
        if not ok:
            # 未组队是确定性非队长状态，直接用原因作描述（套「队长无法
            # 判定」外壳语义矛盾：没组队就没有队长，无从「判定」）；
            # 面板打不开等瞬态类保留「队长无法判定」表述
            if "未组队" in info:
                return "not_leader", info, []
            return "not_leader", f"队长无法判定（{info}）", []
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return "not_leader", "窗口截图失败", []
    kind, scores = _classify_leader_frame(img)
    kind, scores = _confirm_leader_kind(win, kind, scores)
    if kind != "leader":
        # 非队长/无法判定同样在同一面板打开内采集成员信息（悬浮层展示用）
        ok2, members = analyze_team_members(win)
        close_team_panel(win)
        if not ok2:
            members = []
        if kind == "member":
            record_leader_status(False)
            return "not_leader", "非队长（面板无「解散队伍」）", members
        # unclear：按钮未命中不算确定性非队长（面板渲染滞后/瞬时遮挡），
        # 按「队长无法判定」返回——调用方按瞬态失败间隔重查，不转保持模式
        return "not_leader", "队长无法判定（面板内解散/离开按钮未命中）", members
    record_leader_status(True)
    # 人数与成员信息同帧完成（5 固定区域扫描，首空位截断；面板数据
    # 渲染滞后时内部重截一帧兜底）——不重复开关面板、不取消选中
    ok, members, count = _analyze_panel_frame(win, img)
    if allow_kick and ok:
        # todo2 超时暂离请离：面板保持打开按名字记账连踢（从后往前），
        # 踢人后重扫成员——人数判定用最新值（请离后区域平移）
        kicked, kick_info = _auto_kick_overdue(win)
        if kicked:
            log(f"[组队] {kick_info}", "SYS")
        elif kick_info:
            log(f"[组队] 超时请离未完成: {kick_info}", "WARN")
        if kicked:
            img2 = win.capture(force_activate=False)
            if img2 is None:
                close_team_panel(win)
                return "wait", f"{kick_info}（请离后截图失败）", members
            ok, members, count = _analyze_panel_frame(win, img2)
            if not ok:
                close_team_panel(win)
                return "wait", "成员识别失败（请离后面板数据未渲染）", members
    close_team_panel(win)
    if not ok:
        return "wait", "成员识别失败（面板数据未渲染）", members
    # 日志按成员逐条列「种族 等级」（用户 2026-10-14 要求；面板无名称列）
    member_parts = []
    for r, lv in members:
        if r and r != "?":
            member_parts.append(f"{r} {lv}" if lv else r)
        elif lv:
            member_parts.append(f"? {lv}")
    member_txt = "、".join(member_parts) or "未识别到成员"
    if min_members is not None and count < min_members:
        log(f"[组队] 队伍不满足条件: 人数不足（{count}/{min_members}）"
            f"（当前 {count} 人: {member_txt}）", "WARN")
        return "wait", f"队伍人数不足（{count}/{min_members}，当前: {member_txt}）", members
    return "ready", f"队伍满足人数要求（{count} 人: {member_txt}）", members


def diagnose_team_check(win):
    """诊断页「检查队伍信息」：走真实队伍检查链路（check_team_min），
    结果输出到日志。

    流程：申请人证据检查 → 队伍面板开合 → 队长三分类判定（含复核）
    → 5 固定区域成员扫描（空位模板截断、种族/等级逐区识别）→ 关闭
    面板，全程真实点击。check_team_min 内部 [组队] 日志已逐步骤输出，
    本函数补成员明细与最终结论。返回 (状态, 描述)。
    """
    log("[组队] ===== 诊断：检查队伍信息（真实 check_team_min 链路）=====", "SYS")
    status, info, members = check_team_min(win, min_members=None)
    log(f"[组队] 检查结果: {status} - {info}", "SYS")
    incomplete = "识别失败" in info  # 扫描中断：成员列表是中断前部分结果，非完整明细
    if members:
        detail = "、".join(f"{r} {lv or '等级未识别'}" for r, lv in members)
        label = "扫描中断前已识别" if incomplete else "成员明细"
        log(f"[组队] {label}（{len(members)} 人）: {detail}",
            "WARN" if incomplete else "INFO")
    elif incomplete:
        log("[组队] 扫描未识别到任何成员（面板数据未渲染）", "WARN")
    else:
        log("[组队] 成员明细: 未识别到成员", "INFO")
    return status, info


def update_overlay_team(status, info, members):
    """按队伍检查结果更新调试悬浮层「队伍」行（身份 + 人数/种族/等级）。

    修罗/五倍等任务模块每次 check_team_min 后调用；不为悬浮层单独
    开面板，检查的自然结果顺带展示。等级转生段省略为数字。
    """
    if status == "not_leader":
        leader_txt = ("非队长" if "非队长" in info
                      else "未组队" if "未组队" in info else "未知")
    else:
        leader_txt = "队长"
    parts = [f"队伍: {leader_txt}"]
    if members:
        items = []
        for race, lv in members:
            digits = re.sub(r"[^0-9]", "", lv or "")
            items.append(f"{race}{digits}")
        parts.append(f"{len(members)}人: {' '.join(items)}")
    debug_overlay.set_team(" | ".join(parts))


def check_xiuluo_team(win, min_members=None):
    """修罗领取前队伍检查（见 check_team_min，仅取状态与描述）。

    面板打不开（未组队/申请加入面板等）与非队长均返回 not_leader
    （修罗核心流程仅队长执行）。非队长不检查人数（修罗设置仅在队长生效）。
    min_members: 人数下限（含自己，按成员区域数计），None 不检查
    返回 (状态, 描述)；状态：ready 可领取 / wait 队长但人数不足
    / not_leader 非队长或队长无法判定。
    """
    status, info, _members = check_team_min(win, min_members)
    return status, info


def self_test(win):
    """实机自检：组队判定 → 面板内队长检查 → 成员分析（种族+等级）→ 关闭面板。

    最小界面流程：组队菜单开一次、队伍面板开一次（未组队时仅菜单）。
    所有检查在同一次面板打开内完成，避免重复开关面板。
    供实机验证使用（临时挂在某任务模式分支下），验证通过后移除调用即可。
    """
    log("[组队] ========== 实机自检开始 ==========", "SYS")
    # open_team_panel 未组队时报错（菜单无「队伍信息」），即组队判定结果
    ok, info = open_team_panel(win)
    log(f"[组队] 1.是否组队+打开面板: {ok} - {info}", "SYS")
    if ok:
        img = win.capture(force_activate=False)
        is_leader = _is_leader_in_frame(img) if img is not None else False
        log(f"[组队] 2.是否队长: {is_leader} - {'是队长' if is_leader else '不是队长'}"
            f"（双模板比分数识别）", "SYS")
        ok, members = analyze_team_members(win)
        if ok:
            detail = "、".join(f"{r} {lv or '等级未识别'}" for r, lv in members)
            log(f"[组队] 3.成员分析: True - {len(members)} 人: {detail}", "SYS")
        else:
            log(f"[组队] 3.成员分析: {ok} - {members}", "SYS")
        ok, info = close_team_panel(win)
        log(f"[组队] 4.关闭面板: {ok} - {info}", "SYS")
    log("[组队] ========== 实机自检结束 ==========", "SYS")
    return "实机自检完成"


# ---- 自动归队（todo1：非队长战斗死亡落轮回司 → 队伍面板点「归队」） ----
# 用户 2026-11-06 确认机制：战斗中死亡会被自动送回轮回司，此时仍在
# 队伍（面板显示离队状态），队员面板有「归队」按钮，点击即传送到
# 队长身边恢复跟队。死亡判定复用战斗结束的坐标方案（read_map_position
# 地名精确匹配轮回司），无需额外战斗状态判定——调用方保证非战斗时调用。
LUNHUISI = "轮回司"                       # 死亡复活落点地图名（位置 OCR 地名精确匹配）
ZUDUI_GUIDUI_ICON = "zudui_guidui.png"   # 队伍面板「回归队伍」归队按钮模板（用户
                                         # 2026-11-06 提供，90x19 裁剪，OCR 文案
                                         # 「回归队伍」1.00；缺失时归队降级为告警
                                         # 跳过，绝不误点击）
# 归队按钮匹配阈值（用户要求 0.9+；2026-11-06 实测校准：目标帧贴入
# 原图 1.000 唯一命中、非目标帧（离开队伍/解散队伍按钮，同含「队伍」
# 文案样式相近）最佳分 0.825、纯噪声帧 0.134——0.9 恰好把 1.000 与
# 0.825 分开，不得放宽到通用 0.8（会把离开队伍按钮误判成归队按钮）
GUIDUI_THRESHOLD = 0.9
REJOIN_POS_INTERVAL = 1.0   # 轮回司判定轮询间隔（秒，连续 2 轮确认防 OCR 单次误读）
REJOIN_POS_POLLS = 2        # 轮回司判定连续轮询数
REJOIN_CLICK_TIMEOUT = 8    # 点击归队后等待位置离开轮回司的超时（秒，传送加载）
REJOIN_POLL_INTERVAL = 3.0  # 调用方（非队长保持循环）空闲轮询节奏（秒）
REJOIN_RETRY_INTERVAL = 30  # 归队尝试后（无论成败）的调用方放慢间隔（秒）：
                            # 防频繁开关队伍面板（面板开合 5~7s，且遮挡画面）

_guidui_missing_logged = False  # 归队模板缺失告警（每进程一次）


def is_in_lunhuisi(pos):
    """位置是否在轮回司（死亡复活落点，地名精确匹配）。

    pos：read_map_position 结果 (地名, x, y)；None 按不在处理。
    """
    return pos is not None and pos[0].strip() == LUNHUISI


def _at_lunhuisi(win):
    """连续 REJOIN_POS_POLLS 轮询确认落点在轮回司（防位置 OCR 单次误读）。

    每轮询只截一帧，位置 OCR 同帧复用。第一轮未命中即返回（热路径
    免第二轮空转——不在轮回司是常态）。返回 (是否在轮回司, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None or not is_in_lunhuisi(game_map.read_map_position(win, img=img)):
        return False, "不在轮回司"
    for _ in range(REJOIN_POS_POLLS - 1):
        time.sleep(REJOIN_POS_INTERVAL)
        img = win.capture(force_activate=False)
        if img is None or not is_in_lunhuisi(game_map.read_map_position(win, img=img)):
            return False, "不在轮回司"
    return True, "在轮回司"


def maybe_rejoin(win, prefix="[组队]"):
    """非队长死亡归队检查（todo1）：落点在轮回司即战斗中死亡被送回
    （离队状态），打开队伍面板点「归队」按钮传送回队长身边。

    前置：调用方保证非战斗状态（battle 模块反向依赖 team，战斗判定
    由调用方完成）。轮回司判定复用战斗结束的坐标方案（地名精确匹配，
    每轮询只截一帧、位置 OCR 与模板匹配同帧复用）。

    返回 (是否处理, 描述)：
    - (False, ...)：不在轮回司，本轮无需归队，调用方按正常节奏轮询
    - (True, ...)：已尝试归队（成功/失败/无归队条件，如已不在队伍或
      队长身份无归队按钮），调用方按 REJOIN_RETRY_INTERVAL 放慢重查
    """
    global _guidui_missing_logged
    ok, at = _at_lunhuisi(win)
    if not ok:
        return False, at
    ok, info = open_team_panel(win)
    if not ok:
        # 未组队（被请离/队伍解散）同样打不开面板：无法归队，交由
        # 调用方按间隔重查（可能被重新邀请入队）
        return True, f"归队失败: {info}"
    try:
        img = win.capture(force_activate=False)
        if img is None:
            return True, "归队失败: 队伍面板截图失败"
        kind, scores = _classify_leader_frame(img)
        kind, scores = _confirm_leader_kind(win, kind, scores)
        if kind != "member":
            # 队长面板无归队按钮（队长即归队目标，自身无需归队）；
            # unclear 是瞬态（渲染滞后/遮挡），不误点击，下轮重查
            if kind == "leader":
                return True, "归队跳过: 队长身份（面板无归队按钮）"
            return True, "归队失败: 队伍身份无法判定（面板按钮未命中）"
        if not _tpl_path(ZUDUI_GUIDUI_ICON).exists():
            if not _guidui_missing_logged:
                _guidui_missing_logged = True
                log(f"[组队] 归队按钮模板未提供（待补充 res/img/{ZUDUI_GUIDUI_ICON}），"
                    f"归队降级为告警跳过", "WARN")
            return True, "归队按钮模板未提供（待补充 zudui_guidui.png）"
        pt = game_map.find_template_in(img, ZUDUI_GUIDUI_ICON, GUIDUI_THRESHOLD)
        if pt is None:
            return True, "归队失败: 队伍面板中未找到归队按钮（zudui_guidui）"
        human_click(win, pt[0], pt[1])
        log(f"{prefix} 已点击归队按钮 @ ({pt[0]},{pt[1]})，等待传送回队长", "SYS")
        # 点击后校验：位置离开轮回司即归队成功；传送加载期位置不可读
        # 按加载中继续等待。每轮询只截一帧，位置 OCR 同帧复用
        deadline = time.time() + REJOIN_CLICK_TIMEOUT
        while time.time() < deadline:
            img2 = win.capture(force_activate=False)
            if img2 is None:
                time.sleep(REJOIN_POS_INTERVAL)
                continue
            pos2 = game_map.read_map_position(win, img=img2)
            if pos2 is not None and not is_in_lunhuisi(pos2):
                return True, (f"已归队（当前位置: {pos2[0]}[{pos2[1]},{pos2[2]}]）")
            time.sleep(REJOIN_POS_INTERVAL)
        return True, "归队未确认: 点击归队后位置仍在轮回司（可能冷却/未生效，稍后重试）"
    finally:
        close_team_panel(win)


# ---- 暂离成员检测与请离（todo2：队长侧离队成员识别与请离） ----
# 用户 2026-11-07 确认交互：暂离成员卡在队伍面板对应成员区域内显示
# 「召回」标记（32x15，找到即该队员暂离）；队长点选该成员卡后出现
# 「请离队伍」按钮（88x21），点击即请离。识别链路先落诊断按钮，
# 由用户实测反馈后再接入任务流程（5 分钟未归队自动请离）。
ZHAOHUI_ICON = "duiwumianban_zhaohui.png"   # 队员卡「召回」标记（暂离成员卡出现，
                                            # 区域内匹配，位置随面板渲染不固定）
ZUDUI_QINGLI_ICON = "duiwumian_qingliduiwu.png"  # 「请离队伍」按钮（点选暂离成员后出现）
ZHAOHUI_THRESHOLD = 0.9   # 召回标记阈值（实测校准 2026-11-07：目标 1.000 /
                          # 其他按钮帧 0.271 / 纯噪声 0.216）
QINGLI_THRESHOLD = 0.9    # 请离按钮阈值（实测校准 2026-11-07：目标 1.000 /
                          # 归队+离开+解散按钮帧 0.821 / 纯噪声 0.115——
                          # 0.9 恰好把请离与回归队伍按钮（交叉 0.821/0.773）
                          # 分开，不得放宽到通用 0.8）
AWAY_REGION_PAD = (4, 4, 4, 4)   # 召回标记区域搜索外扩（标记在卡内位置未标定，
                                 # 外扩容 1~2px 裁剪/渲染漂移，不越区域间隔）
QINGLI_WAIT_TRIES = 6       # 点选成员后等待请离按钮出现（0.5s 一次）
KICK_VERIFY_WAIT = 1.5      # 点击请离后等待面板数据刷新的时间（秒）
# 召回扫描的面板标志最低分门：_find_banner_rect 阈值 0.0 会取噪声帧的
# 垃圾最优匹配（实测噪声 0.069~0.071），不门控会把垃圾矩形当面板区域
# 扫出「无暂离成员」假结论；真实面板标志随成员数退化最低 0.436
# （5 人队实测），0.3 门留足余量（2026-11-07 校准）
_BANNER_MIN_SCORE = 0.3


# 名字行与等级行的最大间距（像素）：实测布局（用户 2026-11-07 截图
# 校准）名字行底与等级行顶相邻（间距 ~2px），称谓与等级间距 ~27px——
# 名字行 OCR 漏检时宁可返回 None 也不把称谓误当名字
_MEMBER_NAME_LEVEL_GAP = 12


def _member_name(items):
    """成员区域 OCR 行中取昵称：等级行的上一行（名字紧贴等级上方）。

    卡内实测布局（用户 2026-11-07 截图，从上到下）：召回标记（顶部，
    OCR 常读成「招回」）、种族（右缘竖排）、称谓信息、名字、等级
    （如「飞升159级」）。称谓在名字上方但不取（用户确认不需要）；
    名字与等级行相邻，取等级行上方最近的一行且间距 ≤
    _MEMBER_NAME_LEVEL_GAP——名字行漏检时返回 None（报告端降级只
    显示第 X 名），不把称谓/其他行误当名字。等级行未检出返回 None
    （无锚点不猜）。
    """
    horiz = []
    level_top = None
    for txt, rect, score in items:
        if score < _OCR_MIN_SCORE:
            continue
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < h:
            continue  # 竖排种族行（右缘）
        if "召回" in txt or "招回" in txt:
            continue  # 暂离标记自身文案（顶部，OCR 常读成「招回」）
        if _parse_level(txt) is not None:
            if level_top is None or rect[1] < level_top:
                level_top = rect[1]
            continue  # 等级行：名字行锚点
        name = txt.strip()
        if name:
            horiz.append((name, rect))
    if level_top is None or not horiz:
        return None
    above = [(n, r) for n, r in horiz if r[1] < level_top]
    if not above:
        return None
    name, rect = max(above, key=lambda it: it[1][1])  # y 最大 = 最靠近等级行
    if level_top - rect[3] > _MEMBER_NAME_LEVEL_GAP:
        return None  # 等级上方最近的是称谓（间距 ~27px），不是名字
    return name


def _member_race(items):
    """成员区域 OCR 行中取种族（右缘竖排文字，RACE_WORDS 之一）。

    与 _parse_member_region 同判定（竖排框高≥宽 + 种族词表）。
    返回种族词或 None。与 _member_name 共用同一份区域 OCR 结果
    （_away_member_indices 只对召回命中区域 OCR 一次）。
    """
    for txt, rect, score in items:
        if score < _OCR_MIN_SCORE:
            continue
        if (rect[3] - rect[1]) < (rect[2] - rect[0]):
            continue  # 横向行
        for w in RACE_WORDS:
            if w in txt:
                return w
    return None


def _away_member_indices(img):
    """帧内扫描暂离成员：逐成员区域找「召回」标记，命中区域 OCR 提取
    昵称与种族。

    成员区域以 duiwumianban 标志锚定（面板位置会漂移）；每个区域独立
    裁剪 + AWAY_REGION_PAD 外扩搜索。召回标记出现即该队员暂离
    （用户确认语义；区域 i 对应第 i+1 名成员，第 1 区域为队长）。
    只对标记命中的区域做 OCR（非暂离区域不付 OCR 成本），昵称与种族
    共用同一份 OCR 结果。
    返回 (暂离列表, 原因或None)：暂离列表 = [(区域序号, 昵称或None,
    种族或None), ...]——「名字模糊+种族精确」复合身份是 5 分钟未归队
    跟踪的记账身份（成员区域会随请离/加入整体平移，序号不可作身份）；
    标志未命中返回 (None, 原因)。
    """
    score, banner = _find_banner_rect(img)
    if banner is None or score < _BANNER_MIN_SCORE:
        # _find_banner_rect 阈值 0.0 在噪声帧也会返回垃圾最优匹配
        # （实测 0.07 档），按最低分门排除（真实面板 ≥0.436，见常量注释）
        return None, "标志模板未命中（面板不可见或未渲染）"
    away = []
    for i, rect in enumerate(_member_regions(banner)):
        l, t, r, b = rect
        pl, pt, pr, pb = AWAY_REGION_PAD
        crop = img.crop((l - pl, t - pt, r + pr, b + pb))
        if game_map.find_template_in(crop, ZHAOHUI_ICON, ZHAOHUI_THRESHOLD) is None:
            continue
        # 命中区域 OCR 提取昵称与种族（同帧同区域一次 OCR，含右侧溢出带）
        items, _ = game_map.ocr_region(img, (l, t, r + MEMBER_RACE_SPILL, b))
        away.append((i, _member_name(items), _member_race(items)))
    return away, None


def scan_away_members(win):
    """检查队伍暂离（诊断按钮/队长监控共用）：打开队伍面板扫描召回标记
    并提取昵称与种族。

    流程：确保面板打开 → 帧内 _away_member_indices → 关闭面板。
    返回 (是否成功, 暂离成员列表, 描述)；暂离成员列表 =
    [(序号, 昵称或None, 种族或None), ...]，序号从 1 起（面板第 1 区域
    为队长）。无暂离成员也是成功（识别链路正常）。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, [], info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, [], "窗口截图失败"
    away, reason = _away_member_indices(img)
    close_team_panel(win)
    if away is None:
        return False, [], reason or "召回标记扫描失败"
    if not away:
        return True, [], "无暂离成员"
    parts = [f"第 {i + 1} 名" + (f"「{name}」" if name else "")
             + (f"({race})" if race else "")
             for i, name, race in away]
    return True, away, "暂离成员: " + "、".join(parts)


# ---- 暂离超时自动请离（todo2 任务流程接入：超过 5 分钟未归队请离） ----
# 用户 2026-11-07 定稿逻辑：
# 1. 5 分钟阈值——从「识别到该成员暂离」起算（按「名字模糊+种族精确」
#    复合身份记账；成员区域随请离/加入整体平移，序号不可作身份）
# 2. 队伍检查（领取被拒后每 10s 一次）时若该身份仍在暂离名单且超时
#    即请离；归队/被请离/离队则从账本移除
# 3. 同一次检查中多人超时：从后往前请离（后位成员先请离，前位区域
#    序号不随移除平移，一次面板打开内可连踢，无需关闭面板）
KICK_AWAY_TIMEOUT = 300     # 暂离超时阈值（秒）
_AWAY_NAME_RATIO = 0.7      # 名字模糊匹配阈值（与申请列表 _same_applicant
                            # 同款；「碧桃娘子/碧桃浪子」比 0.75 会命中，
                            # 靠种族精确匹配排除——用户 2026-11-07 指定）

_away_seen = {}  # (种族, 名字) → 首次识别为暂离的时间戳（任务进程内跨检查记账）


def reset_away_tracking():
    """清空暂离计时账本（任务进程重启自然清空；测试用）。"""
    _away_seen.clear()


def _away_identity_match(scan_name, scan_race, key_name, key_race):
    """扫描身份与账本键是否同一成员：名字模糊 + 种族精确。

    种族任一侧缺失（OCR 漏检）时退化为仅名字模糊匹配（保计时连续，
    不因种族漏检重启 5 分钟）；两侧都有且不同即不是同一人
    （「碧桃娘子 女人」与「碧桃浪子 男人」靠此区分）。
    """
    if not scan_name or not key_name:
        return False
    if scan_race is not None and key_race is not None and scan_race != key_race:
        return False
    return difflib.SequenceMatcher(None, scan_name, key_name).ratio() \
        >= _AWAY_NAME_RATIO


def _track_away(away):
    """按「名字模糊+种族精确」复合身份记账暂离成员。

    扫描身份与账本键匹配（_away_identity_match）则沿用原时间戳；
    新身份记首次时间戳；账本中已无匹配者的移除（归队/被请离/离队）。
    无名字成员不记账（后续检查无从匹配）。
    """
    cur = [(name, race) for _i, name, race in away if name]
    for name, race in cur:
        if not any(_away_identity_match(name, race, kr, kn)
                   for kn, kr in _away_seen):
            # 账本键 = (种族, 名字)，匹配参数为 (名字, 种族)——kn/kr 即
            # 种族/名字，传参顺序必须反过来（2026-11-07 修复：传反会
            # 拿种族比名字、永远匹配不上，计时每次重启）
            _away_seen[(race, name)] = time.time()
            race_txt = f"({race})" if race else ""
            log(f"[组队] 首次识别暂离成员「{name}」{race_txt}，开始计时"
                f"（{int(KICK_AWAY_TIMEOUT)}s 未归队自动请离）", "INFO")
    for key in list(_away_seen):
        kn, kr = key
        if not any(_away_identity_match(name, race, kr, kn)
                   for name, race in cur):
            _away_seen.pop(key, None)


def _overdue_away(away):
    """本次扫描中已超时的暂离成员，按区域序号从后往前排序
    （含名字者才可记账判定；第 1 区域队长自身永不请离）。"""
    overdue = []
    for i, name, race in sorted(away, key=lambda it: -it[0]):
        if i <= 0 or not name:
            continue
        seen = next((v for (kn, kr), v in _away_seen.items()
                     if _away_identity_match(name, race, kr, kn)), None)
        if seen is not None and time.time() - seen >= KICK_AWAY_TIMEOUT:
            overdue.append((i, name, race))
    return overdue


def _kick_one(win, idx, name, race, away_before):
    """面板已打开：点选第 idx+1 名成员卡 → 请离队伍按钮 → 请离 → 校验。

    校验（保守原则，宁报失败不报假成功——假成功会把身份移出账本，
    下次扫描重新计时多等 5 分钟）：
    - 有名字：该复合身份（名字模糊+种族精确）从重扫暂离名单消失
      **且** 暂离总数减少才算成功（名字 OCR 单侧漏检/他人新暂离/
      同名不同种族补位都不会误报）
    - 无名字：仅按暂离总数减少判定
    返回 (是否成功, 描述)。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return False, "窗口截图失败"
    score, banner = _find_banner_rect(img)
    if banner is None or score < _BANNER_MIN_SCORE:
        return False, "标志模板未命中（面板不可见或未渲染）"
    rect = _member_regions(banner)[idx]
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    human_click(win, cx, cy)
    name_txt = f"「{name}」" if name else ""
    race_txt = f"({race})" if race else ""
    log(f"[组队] 已点选暂离成员{name_txt}{race_txt}（第 {idx + 1} 名）"
        f"@ ({cx},{cy})，等待请离队伍按钮", "SYS")
    pt = None
    for _ in range(QINGLI_WAIT_TRIES):
        time.sleep(0.5)
        img2 = win.capture(force_activate=False)
        if img2 is None:
            continue
        pt = game_map.find_template_in(img2, ZUDUI_QINGLI_ICON, QINGLI_THRESHOLD)
        if pt is not None:
            break
    if pt is None:
        return False, (f"点选第 {idx + 1} 名成员{name_txt}{race_txt}后未出现"
                       f"请离队伍按钮（duiwumian_qingliduiwu）")
    human_click(win, pt[0], pt[1])
    log(f"[组队] 已点击请离队伍按钮 @ ({pt[0]},{pt[1]})", "SYS")
    time.sleep(KICK_VERIFY_WAIT)
    img3 = win.capture(force_activate=False)
    if img3 is None:
        return False, "点击请离后截图失败（未确认是否生效）"
    away2, _reason2 = _away_member_indices(img3)
    if away2 is None:
        return False, "点击请离后面板不可见（可能已自关），请人工确认是否生效"
    still_away = any(_away_identity_match(name, race, n2, r2)
                     for _i, n2, r2 in away2) if name is not None else None
    count_down = len(away2) < len(away_before)
    if name is not None:
        if not still_away and count_down:
            return True, f"已请离暂离成员「{name}」{race_txt}"
        return False, (f"点击请离后「{name}」{race_txt}仍在暂离名单或人数未减少"
                       f"（未生效或需确认对话框，请人工核对）")
    if count_down:
        return True, f"已请离第 {idx + 1} 名暂离成员"
    return False, (f"已点击请离队伍（第 {idx + 1} 名），暂离标记未减少"
                   f"（未生效或需确认对话框，请人工核对）")


def kick_away_member(win):
    """请离一名暂离成员（诊断按钮用）：面板开合由本函数管理，点选
    第一个暂离成员（跳过第 1 区域队长自己）。任务流程的超时连踢走
    _auto_kick_overdue（面板保持打开）。返回 (是否成功, 描述)。
    """
    try:
        if not _is_panel_open(win):
            ok, info = open_team_panel(win)
            if not ok:
                return False, info
        img = win.capture(force_activate=False)
        if img is None:
            return False, "窗口截图失败"
        away, reason = _away_member_indices(img)
        if away is None:
            return False, reason or "召回标记扫描失败"
        tgt = next(((i, name, race) for i, name, race in away if i > 0), None)
        if tgt is None:
            if away:
                return False, "仅第 1 区域（队长）有召回标记，不能请离自己"
            return False, "无暂离成员可请离"
        return _kick_one(win, tgt[0], tgt[1], tgt[2], away)
    finally:
        close_team_panel(win)


def _auto_kick_overdue(win):
    """面板保持打开：循环请离已超时的暂离成员（任务流程 todo2 接入）。

    每轮重截帧重扫暂离名单（成员移除后面板重渲染；从后往前请离，
    前方成员区域不随移除平移，序号持续有效）→ 按「名字模糊+种族
    精确」复合身份记账更新（归队/离队者移除计时）→ 超时者取序号
    最大（最靠后）先请离 → 成功后移出账本继续下一轮；请离失败即停
    （识别问题不盲目连踢，下轮检查 10s 后自然重试）。
    返回 (已请离数, 描述)；无超时且无异常返回 (0, "")。
    """
    kicked = 0
    parts = []
    while True:
        img = win.capture(force_activate=False)
        if img is None:
            return kicked, ("截图失败" if not kicked
                            else f"已请离 {'、'.join(parts)}；后续截图失败")
        away, reason = _away_member_indices(img)
        if away is None:
            return kicked, (reason or "召回扫描失败") \
                if not kicked else f"已请离 {'、'.join(parts)}；重扫失败: {reason}"
        _track_away(away)
        # todo3 组号处理：暂离队员若是本组号的队员 Tab，切到该 Tab 替其
        # 点「回归队伍」归队（归队成功者离开暂离名单，账本自然移除）；
        # 匹配到的自家队员 Tab 不参与超时请离（归队优先，踢了要重加）
        matched_tabs = set()
        try:
            from . import tab_group  # 函数内导入：tab_group 顶层不依赖 team
            if tab_group.active():
                done, pass_info, matched_tabs = tab_group.member_rejoin_pass(
                    win, [n for _i, n, _r in away if n])
                if pass_info:
                    log(f"[组队] {pass_info}", "SYS" if done else "WARN")
        except Exception as e:
            log(f"[组队] 组号归队 pass 异常: {e}", "WARN")
        overdue = [o for o in _overdue_away(away)
                   if o[1] not in matched_tabs]
        if not overdue:
            break
        idx, name, race = overdue[0]  # 序号最大（最靠后）先请离，前方区域不移位
        ok, info = _kick_one(win, idx, name, race, away)
        if not ok:
            return kicked, (info if not kicked
                            else f"已请离 {'、'.join(parts)}；继续请离失败: {info}")
        kicked += 1
        # 移出账本：按复合身份匹配键（账本键 = (种族, 名字)，不能按
        # 名字裸 pop——名字相同种族不同是不同账目）
        for key in [k for k in _away_seen
                    if _away_identity_match(name, race, k[1], k[0])]:
            _away_seen.pop(key, None)
        parts.append(f"「{name}」")
        log(f"[组队] 暂离成员「{name}」超过 {int(KICK_AWAY_TIMEOUT)}s 未归队，已请离", "SYS")
    if kicked:
        return kicked, f"已请离超时暂离成员: {'、'.join(parts)}"
    return 0, ""


# ---- 待接入识别方式的操作 ----

def leave_team(win):
    """离开队伍（队员身份）：已组队时打开队伍面板，点击「离开队伍」。

    流程：确保队伍面板打开（未组队时报错）→ 帧内双模板比分数，
    离开分数达标且高于解散分数才点击（防两按钮互误匹配）→ 关闭面板。
    面板中无离开按钮返回失败（通常表示是队长，应走 disband_team）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    likai_score, likai_pt = _best_match(img, LIKAI_DUIWU_ICON)
    jiesan_score, _ = _best_match(img, JIESAN_DUIWU_ICON)
    if not (likai_score >= _TEMPLATE_THRESHOLD and likai_score > jiesan_score):
        close_team_panel(win)
        return False, "面板中未找到「离开队伍」按钮（可能是队长）"
    human_click(win, likai_pt[0], likai_pt[1])
    log(f"[组队] 已点击离开队伍按钮 @ ({likai_pt[0]},{likai_pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 已离开队伍，面板可能已自动关闭；未关则补关
    return True, "已离开队伍"


def disband_team(win):
    """解散队伍（队长身份）：已组队时打开队伍面板，点击「解散队伍」。

    流程：确保队伍面板打开（未组队时报错）→ 帧内双模板比分数，
    解散分数达标且高于离开分数才点击（防两按钮互误匹配）→ 关闭面板。
    面板中无解散按钮返回失败（通常表示不是队长）。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    jiesan_score, jiesan_pt = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, _ = _best_match(img, LIKAI_DUIWU_ICON)
    if not (jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score):
        close_team_panel(win)
        return False, "面板中未找到「解散队伍」按钮（可能不是队长）"
    human_click(win, jiesan_pt[0], jiesan_pt[1])
    log(f"[组队] 已点击解散队伍按钮 @ ({jiesan_pt[0]},{jiesan_pt[1]})", "SYS")
    time.sleep(0.5)
    close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
    return True, "已解散队伍"


def leave_or_disband_team(win):
    """按身份退出队伍：未组队不操作；队长解散；队员离开。

    单次面板流程（避免重复开关面板）：打开队伍面板 → 帧内双模板
    比分数（解散/离开按钮会互误匹配，取分数高且达标者）→ 点击。
    未组队时菜单无「队伍信息」，无法打开面板，直接返回成功不操作。
    返回 (是否成功, 描述)。
    """
    if not _is_panel_open(win):
        ok, info = open_team_panel(win)
        if not ok:
            if "未组队" in info:
                return True, f"未组队，无需操作：{info}"
            return False, info
    img = win.capture(force_activate=False)
    if img is None:
        close_team_panel(win)
        return False, "窗口截图失败"
    jiesan_score, jiesan_pt = _best_match(img, JIESAN_DUIWU_ICON)
    likai_score, likai_pt = _best_match(img, LIKAI_DUIWU_ICON)
    if jiesan_score >= _TEMPLATE_THRESHOLD and jiesan_score > likai_score:
        human_click(win, jiesan_pt[0], jiesan_pt[1])
        log(f"[组队] 已点击解散队伍按钮 @ ({jiesan_pt[0]},{jiesan_pt[1]})", "SYS")
        time.sleep(0.5)
        close_team_panel(win)  # 队伍已解散，面板可能已自动关闭；未关则补关
        return True, "已解散队伍"
    if likai_score >= _TEMPLATE_THRESHOLD and likai_score > jiesan_score:
        human_click(win, likai_pt[0], likai_pt[1])
        log(f"[组队] 已点击离开队伍按钮 @ ({likai_pt[0]},{likai_pt[1]})", "SYS")
        time.sleep(0.5)
        close_team_panel(win)
        return True, "已离开队伍"
    close_team_panel(win)
    return False, "队伍面板中既无「解散队伍」也无「离开队伍」按钮"


def handle_join_requests(win, action="allow"):
    """处理入队申请：允许或拒绝。

    action: "allow" 允许 / "reject" 拒绝。
    TODO 待接入：申请列表界面识别与同意/拒绝按钮定位。
    返回 (是否成功, 描述)。
    """
    if action not in ("allow", "reject"):
        return False, f"未知处理方式「{action}」，可选: allow / reject"
    log(f"[组队] 处理入队申请({action})：申请列表界面识别待接入", "WARN")
    return False, "申请列表识别待接入"


def apply_to_team(win):
    """申请入队：点击「申请队伍」状态的玩家。

    机制：鼠标移到申请队伍（招募中）的玩家身上，左键点击即申请入队。
    TODO 待接入：世界场景中招募玩家的识别（名牌/旗帜标记）。
    返回 (是否成功, 描述)。
    """
    log("[组队] 申请入队：招募玩家识别待接入", "WARN")
    return False, "招募玩家识别待接入"
