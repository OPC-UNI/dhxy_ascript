"""星魂归位（山河名望·仙族）四象禁地小游戏闭环（2026-11-17 实机全流程）。

流程（用户 2026-11-17 实机操作确认）：
1. 领取后追踪「看看天庭的星官X遇到什么麻烦了。」→ 点追踪绿字「星官X」
   自动寻路到 NPC → 对话选「是武尊神遣我过来帮忙的。」（任务侧
   DIALOG_OPTION_MAP）→ 后续台词点屏推进 → **自动传送**到四象禁地。
2. 四象禁地追踪文案：「在四象禁地（160，50）附近找到星魂，点击星魂
   移动，对应带到箕之星宿石、房之星宿石、心之星宿石、角之星宿石、
   氐之星宿石、尾之星宿石处。」——六个星宿石为绿字链接（同一行内
   常有两条，须按关键词定位下划线）。
3. **先关入场对话**（2026-11-17 用户补充）：刚传送进四象禁地时屏幕
   中间仍有入场剧情对话（「少侠不愧是武尊神看重之人…」），未关闭前
   点星魂标签会被对话框吞掉——场景内先跑共享 StoryDialogDismisser
   点屏推进（模板门 + 绿字选项保护 + 站定判定）；推进器被守卫拒绝
   时不点世界，等下一轮询。
4. 找场景中「X之星魂」石柱（位置每次随机；回中心不一定全部可见）：
   点标签自动寻路到星魂旁 → 点石柱 → 对话「此乃X之星魂。」选
   「催动法力，让星魂跟随自己移动。」→ 星魂跟随人物。
5. 点追踪面板「X之星宿石」链接 → 自动寻路到对应星宿石 → 到达弹
   「感觉差不多到对应的星位了，是否让星魂归位？」→ 选「星魂归位！」
   → 屏幕浮字「已成功让X之星魂归位了！」。
6. 点追踪「四象禁地」链接回到星魂附近，重复 3-5（六个顺序无要求；
   星魂数量固定 6，用户 2026-11-17 确认）。
7. 六个全部归位后任务自动完成：**最后一个归位后游戏自动把人物传出
   四象禁地**（2026-11-22 用户确认），随后发奖励、追踪面板消失
   （完成判定交给 follow 引擎，本模块不做完成判定）。传出瞬间追踪
   文案可能尚未刷新：计数满 6（_done >= XH_SOUL_TOTAL）或地图名已
   不是四象禁地时，`_return_phase` / `_search_phase` 回中心与游走
   一律不点（_in_scene_pos 门控，位置读不出按离场处理）。

实测要点（2026-11-17 用户实机逐条操作标定）：
- 星魂=灰色石柱带青色光环，名字标签在石柱下方；点标签只会自动寻路，
  不会开对话——必须走位结束后再点**石柱本体**（标签框顶上方约 68px，
  实测 62~76px 均可命中）。
- 远距离（屏幕 >~200px）直接点石柱无效（不寻路不响应），所以先点标签。
- 走位结束后标签常被人物名字/特效遮挡（OCR 读不到）：回退点
  XH_SELF_PILLAR_POINT——相机把人物固定在画面中央，石柱在人物正上方
  约 65~75px（箕/氐两次实测点 (690,315)/(700,300) 命中）。
- 追踪行「应带到箕之星宿石、房之星宿」一行两个链接（还会折行），
  必须按关键词 + 下划线定位（task_tracker.underline_point），点行中心
  会落到两个链接之间。
- 场景标签 OCR 只认「X之星魂」四字整行（对话「此乃X之星魂。」带
  前缀，不得误认成场景标签）。两个星魂几乎重叠时标签叠绘成一行
  （2026-11-22 实机「箕房星魂魂」0.63），由 _merged_soul_elements
  拆分，点哪个石柱以对话文案为准。
- 人物/队友名字与星魂标签同排时也会被 OCR 合并成一行（2026-11-24
  实机「孤独つ魅 角之星魂星魂」0.945：人物正站在石柱前，队友名与
  两个星魂标签叠在同一文本行）——整行匹配失败，模块误判「视野内
  无星魂」反复回中心/游走约 60s。由 _embedded_soul_labels 在行内
  定位完整「X之星魂」子串，并按字符位置比例换算子矩形（实测子矩形
  577~644，点到的就是标签本体与石柱；「角之星魂」黄色色块 577~637，
  右缘含相邻残片数 px）。
- 追踪文案在四象禁地阶段是**静态**的（归位不改变文字/颜色），follow
  引擎签名不变化、不会自行点链接；所有导航动作由本模块驱动。
  green_picker 在场景阶段返回「四象禁地」链接点（引擎万一要补点
  链接时只回中心，绝不点星宿石）。

与调用方（mingwang 任务侧）的接口：`handle(win, items, img_np,
frame_fn=None, prefix="[名望] ")`——extras 链每轮询调用一次；
`frame_fn` 提供共享帧（(win, items) → (RGB np, BGR np)，复用同轮询
截帧，同 mowangchongju）；`green_picker(items, img_np)`；`reset()`。
import 期零副作用；不 import tasks/*。
"""
import time

import cv2
import numpy as np

from ..core.app_utils import human_click, log, norm_text
from ..device import map as game_map
from ..device import task_tracker
from ..device.dialog_common import (DIALOG_FRAME_THRESHOLD,
                                    StoryDialogDismisser,
                                    _click_green_option_once,
                                    dialog_frame_score)

# 触发词：追踪面板任务名 + 四象禁地执行阶段特征词（NPC 阶段追踪
# 文案「看看天庭的星官X遇到什么麻烦了。」不含「星宿石」，本模块零操作，
# 由 follow 引擎绿字点击 + 任务对话表走完入口）
XH_TASK_KEYWORD = "星魂归位"
XH_SCENE_KEYWORD = "星宿石"
# 星魂/星宿石元素与 OCR 形近字别名（实机：氐→氏、尾→尼、箕→页）
XH_ELEMENTS = (
    ("箕", ("箕", "其", "页")),
    ("房", ("房",)),
    ("心", ("心",)),
    ("角", ("角",)),
    ("氐", ("氐", "氏")),
    ("尾", ("尾", "尼")),
)
XH_SOUL_TOTAL = 6                    # 星魂总数（用户 2026-11-17 确认固定 6）
# 场景标签 OCR 条带（世界区，避开左侧追踪面板与标题栏）
XH_SCAN_REGION = (410, 120, 1378, 790)
# 左缘窄带补扫（2026-11-19 实机截图）：星魂标签贴世界区左缘时（实测
# 「箕之星魂」OCR rect 左缘 x=389、追踪面板右缘 ≈307），主条带左界
# 410 把整行裁成「2星魂」→ 四字整行匹配失败：画面里明明有星魂，模块
# 连续 3 轮「视野内无星魂」游走约 60s 才把它转进主条带。直接把主条带
# 左界放宽到 330 更糟——区域宽 >736 触发降采样，同一标签在原尺寸裁剪
# 下 0.991 而宽条带里直接漏检。故保持主条带不变，主条带没有任何星魂
# 时补扫左带（宽 330px <736，按原尺寸检测）；正常帧主条带即命中，
# 补扫零开销。
XH_SCAN_LEFT_REGION = (330, 120, 660, 790)
XH_SCAN_INTERVAL = 2.0               # 场景 OCR 最小间隔（秒，全区域 ~0.4s）
XH_SCAN_MIN_SCORE = 0.55             # 标签行置信度下限（同各任务模块）
XH_PILLAR_OFFSET = 68                # 石柱点击点 = 标签框顶上方 68px（实测 62~76）
XH_SELF_PILLAR_POINT = (690, 305)    # 标签被遮挡时的石柱兜底点（相机居中，实测命中）
XH_WALK_TIMEOUT = 8.0                # 点标签后等走位结束的上限（秒）
XH_WALK_STABLE_POLLS = 2             # 位置连续稳定轮询数判定走位结束
XH_PILLAR_RETRY_INTERVAL = 2.5       # 点石柱后等对话的宽限（秒），超时重试
XH_PILLAR_RETRY_MAX = 3              # 同一星魂点石柱最多尝试次数
XH_LINK_CLICK_INTERVAL = 2.0         # 追踪链接重复点击最小间隔（秒）
XH_LINK_CLICK_MAX = 5                # 同一阶段追踪链接最多点击次数
XH_LINK_STILL_POLLS = 2              # 寻路停滞判定：位置连续不变轮询数（可补点）
# 追踪链接低对比度补偿 OCR 的最小间隔（秒）：面板末行压在亮背景上时
# 区域 OCR 整行漏检（2026-11-19 实测「、氏之星宿石、尾之星宿石处」
# 缺失 → 跟随后点不到星宿石、静默卡到 follow 超时 160s）；普通帧链接
# 查找失败才触发增强重识别（track_task.ocr_track_enhanced，约 155ms），
# 限频防卡死窗口内每轮询空烧
XH_LINK_ENH_INTERVAL = 1.5
XH_PHASE_DELAY = 1.0                 # 阶段切换后首次点击延迟（等对话关闭/浮字）
XH_RETURN_WAIT = 4.0                 # 点「四象禁地」后回中心等待（秒）
XH_SEARCH_INTERVAL = 2.0             # 无星魂可见时游走搜索的最小间隔（秒）
# 游走点（窗口坐标）：视野内无星魂时点这些世界位置移动相机扩大视野。
# 取人物四周约 200px（人物约在 (690,380)），全部落在世界区（x>410）。
XH_SEARCH_OFFSETS = ((950, 300), (950, 520), (450, 300), (450, 520))
XH_ABSENT_RESET = 5                  # 任务文案连续缺失轮询数 → 复位
# 场景地图名：最后一个星魂归位后游戏会自动把人物传出四象禁地
# （2026-11-22 用户实机），此时追踪文案往往还没刷新——凡是要点
# 「四象禁地」链接/世界游走的位置，先确认地图名仍是四象禁地
XH_SCENE_MAP = "四象禁地"

# 状态机：0=找星魂/拿星魂；1=带星魂去星宿石（点链接/等到达）；2=归位后回中心
_phase = 0
_target = None            # 当前星魂元素（"尾" 等）
_phase_at = 0.0           # 阶段进入时间（首次点击延迟）
_done = 0                 # 已归位数（日志/诊断）
_walk_at = 0.0            # 已点星魂标签、等走位结束
_walk_pos = None
_walk_stable = 0
_pillar_at = 0.0          # 已点石柱、等「此乃X之星魂」对话
_pillar_tries = 0
_link_at = 0.0            # 追踪链接（星宿石/四象禁地）上次点击
_link_tries = 0
_link_pos = None
_link_still = 0
_enh_link_at = 0.0        # 链接低对比度补偿 OCR 上次尝试（限频）
_search_at = 0.0
_search_idx = 0
_search_pos = None
_search_still = 0
_scan_at = 0.0
_scan_result = []
_bad = set()              # 点不出对话的星魂元素（本轮跳过）
_missing = 0
_left_scene = False       # 已确认离开四象禁地（离场日志只打一次）
# 场景阻挡对话点屏推进器（共享 StoryDialogDismisser：模板门 + 已知对话
# 抑制 + 绿字选项保护 + 站定判定，性能规则 13）；懒创建，reset 时清空
_dismisser = None

# 共享帧缓存（frame_fn 未注入时的兜底；按 items 对象 id 缓存）
_FRAME_UNSET = object()
_frame_key = None
_frame_rgb = None
_frame_bgr = None
_frame_pos = _FRAME_UNSET


def reset():
    """状态复位（新任务领取成功/跨任务防残留）。"""
    global _phase, _target, _phase_at, _done, _walk_at, _walk_pos, \
        _walk_stable, _pillar_at, _pillar_tries, _link_at, _link_tries, \
        _link_pos, _link_still, _enh_link_at, _search_at, _search_idx, \
        _search_pos, _search_still, _scan_at, _scan_result, _bad, _missing, \
        _left_scene, _dismisser, _frame_key, _frame_rgb, _frame_bgr, \
        _frame_pos
    _phase = 0
    _target = None
    _phase_at = 0.0
    _done = 0
    _walk_at = 0.0
    _walk_pos = None
    _walk_stable = 0
    _pillar_at = 0.0
    _pillar_tries = 0
    _link_at = 0.0
    _link_tries = 0
    _link_pos = None
    _link_still = 0
    _enh_link_at = 0.0
    _search_at = 0.0
    _search_idx = 0
    _search_pos = None
    _search_still = 0
    _scan_at = 0.0
    _scan_result = []
    _bad = set()
    _missing = 0
    _left_scene = False
    _dismisser = None
    _frame_key = None
    _frame_rgb = _frame_bgr = None
    _frame_pos = _FRAME_UNSET


def _sig(items):
    """追踪面板文案签名（只取面板左列，排除对话框/浮字；抗 OCR 拆行）。"""
    return "".join(norm_text(t) for t, r, s in items
                   if s >= 0.55 and r[0] < task_tracker.PANEL_MAX_X)


def scene_active(items):
    """是否处于四象禁地执行阶段（任务名 + 星宿石文案同时在场）。"""
    sig = _sig(items)
    return XH_TASK_KEYWORD in sig and XH_SCENE_KEYWORD in sig


def _frame(win, items, frame_fn):
    """共享帧（按 id(items) 缓存），返回 (RGB, BGR)。"""
    global _frame_key, _frame_rgb, _frame_bgr, _frame_pos
    if frame_fn is not None:
        return frame_fn(win, items)
    key = id(items)
    if _frame_key != key:
        img = win.capture(force_activate=False)
        _frame_key = key
        _frame_pos = _FRAME_UNSET
        if img is None:
            _frame_rgb = _frame_bgr = None
        else:
            _frame_rgb = np.asarray(img)[:, :, :3]
            _frame_bgr = cv2.cvtColor(_frame_rgb, cv2.COLOR_RGB2BGR)
    return _frame_rgb, _frame_bgr


def _pos(win, items, frame_fn):
    """当前轮询共享帧上的位置读取（frame_fn 注入时也按 items 缓存）。"""
    global _frame_pos
    if frame_fn is None:
        rgb, _ = _frame(win, items, frame_fn)
    else:
        if _frame_pos is _FRAME_UNSET:
            rgb0, _ = frame_fn(win, items)
            _frame_pos = (None if rgb0 is None
                          else game_map.read_map_position(win, img=rgb0))
        return _frame_pos
    if _frame_pos is not _FRAME_UNSET:
        return _frame_pos
    _frame_pos = (None if rgb is None
                  else game_map.read_map_position(win, img=rgb))
    return _frame_pos


def _in_scene_pos(pos):
    """当前位置是否仍在四象禁地（位置读不出按离场处理，fail-closed）。

    最后一个星魂归位后游戏自动把人物传出四象禁地（2026-11-22 用户
    实机）：传送后追踪面板文案尚未刷新的一两秒里，状态机若照常点
    「四象禁地」链接或点世界游走，会落到新地图上（误点/乱走）。
    读不出地图名时同样不做世界点击——下一轮询会重读，任务完成由
    follow 引擎按追踪面板消失判定。
    """
    return pos is not None and pos[0].strip() == XH_SCENE_MAP


def _soul_labels(win, items, frame_fn, now, force=False):
    """场景 OCR 找视野内「X之星魂」标签，返回 [(元素, rect), ...]。

    标签可独占一行（norm 后恰为「X之星魂」），也可与玩家名/叠绘残片
    同排（2026-11-24 实机，见 _embedded_soul_labels）；对话正文
    「此乃X之星魂。」与归位浮字「已成功让X之星魂归位了！」不得误认
    成场景标签。按 XH_SCAN_INTERVAL 限频（全区域 OCR ~0.4s）；
    force=True 时强制重新识别（走位结束后的石柱定位）。
    """
    global _scan_at, _scan_result
    if not force and now - _scan_at < XH_SCAN_INTERVAL:
        return _scan_result
    _scan_at = now
    rgb, _ = _frame(win, items, frame_fn)
    if rgb is None:
        _scan_result = []
        return _scan_result
    exact = {f"{a}之星魂": el
             for el, aliases in XH_ELEMENTS for a in aliases}
    cands = []
    # 主条带 → 左缘窄带补扫（见 XH_SCAN_LEFT_REGION 注释）：标签贴左缘
    # 时主条带会裁掉「X之」前缀、整行读成「2星魂」而漏检；主条带已命中
    # 任何星魂即不再补扫（窄带只为贴左缘标签，正常帧零额外开销）
    for region in (XH_SCAN_REGION, XH_SCAN_LEFT_REGION):
        try:
            sitems, _ = game_map.ocr_region(rgb, region)
        except Exception:
            sitems = []
        for txt, rect, score in sitems:
            if score < XH_SCAN_MIN_SCORE:
                continue
            n = norm_text(txt)
            el = exact.get(n)
            if el is not None:
                cands.append((el, rect))
                continue
            # 玩家名/叠绘残片与标签同排的合并行（见 _embedded_soul_labels）
            emb = _embedded_soul_labels(txt, rect)
            if emb:
                cands.extend(emb)
                continue
            # 叠绘合并行（两个星魂几乎重叠，见 _merged_soul_elements）
            for mel in _merged_soul_elements(n):
                cands.append((mel, rect))
        if cands:
            break
    _scan_result = cands
    return cands


def _merged_soul_elements(text):
    """两星魂几乎重叠时标签叠绘成一行的解析（2026-11-22 实机）。

    星魂位置随机，两个星魂可能几乎站在同一点：各自的「X之星魂」标签
    叠绘在一起，OCR 读成一行「箕房星魂魂」（矩形为两者并集，实测
    0.63）——四字整行匹配失败，模块误判「视野内无星魂」无限游走。
    这里把该行拆回两个元素，共用并集 rect（点哪个石柱由对话文案
    「此乃X之星魂」定夺，_dialog_soul_element 会覆盖 _target）。

    只认「≥2 个不同元素别名 + 之星魂」且**去掉别名与「之星魂」后
    无任何残余字符**的行：对话正文「此乃X之星魂」（单个别名 + 前缀）
    与世界里的其他人名/怪物名（含别名但留残余字符）一律拒绝。
    """
    els = []
    rest = text
    for el, aliases in XH_ELEMENTS:
        for a in aliases:
            if a in rest:
                els.append(el)
                rest = rest.replace(a, "")
                break
    if len(els) < 2:
        return []
    rest = rest.replace("之", "")
    if "魂" not in rest or set(rest) - set("星魂"):
        return []
    return els


def _sub_rect(rect, text, idx, length):
    """OCR 行矩形内按字符位置换算子串矩形（近似：按字符宽度比例分配）。

    OCR 只给整行并集框。玩家名与星魂标签同排时整行中心会落到队友名字
    上（点名字只会选中队友/空走），必须按子串在行内的字符位置收窄。
    2026-11-24 实拍行「孤独つ魅 角之星魂星魂」框 x 494~677：换算
    「角之星魂」x≈577~644，与实际黄色标签色块 577~637 吻合。
    中日韩字符与玩家名用字宽度接近，字符比例换算是够用的近似。
    """
    if not text or length <= 0 or len(text) <= length:
        return rect
    l, t, r, b = rect
    w = r - l
    x0 = l + int(round(w * idx / float(len(text))))
    x1 = l + int(round(w * (idx + length) / float(len(text))))
    return [x0, t, x1, b]


def _embedded_soul_labels(txt, rect):
    """一行文本内嵌的「X之星魂」标签 → [(元素, 子矩形), ...]。

    2026-11-24 实机：人物/队友站在星魂石柱前时，队友名字与星魂标签被
    OCR 合成一行「孤独つ魅 角之星魂星魂」（0.945）——其中「角之星魂」
    是完整标签，尾部「星魂」是另一个被遮挡标签的残片。整行精确匹配
    失败 → 模块误判「视野内无星魂」反复游走，而星魂就在人物脚下。
    在原始行文本里找完整「X之星魂」子串（含形近字别名），按字符位置
    收窄矩形；同一元素只取首个。

    排除两类别含子串的非场景文案：星魂对话「此乃X之星魂。」与归位
    浮字「已成功让X之星魂归位了！」——按行为标记词（此乃/归位）拒绝。
    """
    n = norm_text(txt)
    if not txt or "此乃" in n or "归位" in n:
        return []
    out = []
    seen = set()
    for el, aliases in XH_ELEMENTS:
        if el in seen:
            continue
        for a in aliases:
            tok = f"{a}之星魂"
            idx = txt.find(tok)
            if idx < 0:
                continue
            seen.add(el)
            out.append((el, _sub_rect(rect, txt, idx, len(tok))))
            break
    return out


def _pillar_point(cands, el):
    """星魂石柱点击点：标签框顶上方 XH_PILLAR_OFFSET；标签被遮挡时回退。"""
    for cel, rect in cands:
        if cel == el:
            return (rect[0] + (rect[2] - rect[0]) // 2,
                    max(0, rect[1] - XH_PILLAR_OFFSET))
    return XH_SELF_PILLAR_POINT


def _link_point(items, img_np, keywords):
    """追踪面板含关键词的绿字链接点击点（下划线吸附），未识别返回 None。"""
    for txt, rect, score in items:
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        for kw in keywords:
            idx = txt.find(kw)
            if idx < 0:
                continue
            n = max(1, len(txt))
            x = rect[0] + (rect[2] - rect[0]) * (idx + len(kw) / 2.0) / n
            pt = task_tracker.underline_point(img_np, rect, x)
            return pt if pt is not None else (int(x), rect[3] - 2)
    return None


def _soul_dialog_present(items):
    """「此乃X之星魂。」对话在场（选项未渲染/低置信度时等待，不点世界）。"""
    return any("此乃" in t and "之星魂" in t and s >= 0.55
               and r[0] >= task_tracker.PANEL_MAX_X
               for t, r, s in items)


def _arrive_dialog_present(items):
    """星宿石到达确认对话在场（「感觉差不多到对应的星位了…」）。"""
    return any(("差不多到对应" in t or "让星魂归位" in t)
               and s >= 0.55 and r[0] >= task_tracker.PANEL_MAX_X
               for t, r, s in items)


def _scene_dismisser():
    """场景阻挡对话点屏推进器（懒创建；reset 清空）。"""
    global _dismisser
    if _dismisser is None:
        # known_texts 空：带绿字选项的对话由 green_option_row_present 保护，
        # 不需要文案白名单；未知无选项台词（入场「少侠不愧是武尊神看重
        # 之人…」等）点屏推进
        _dismisser = StoryDialogDismisser()
    return _dismisser


def _dialog_soul_element(items):
    """从「此乃X之星魂。」对话解析星魂元素（比标签 OCR 更可靠）。

    标签（场景）与对话（正文）两处 OCR 可能不同；对话文本完整，作为
    当前带魂元素的第二证据，冲突时以对话为准。
    """
    exact = {a: el for el, aliases in XH_ELEMENTS for a in aliases}
    for t, r, s in items:
        if s < 0.55 or r[0] < task_tracker.PANEL_MAX_X:
            continue
        n = norm_text(t)
        if "此乃" not in n:
            continue
        for a, el in exact.items():
            if f"{a}之星魂" in n:
                return el
    return None


def _stone_keywords(el):
    """元素对应的追踪星宿石链接关键词（含 OCR 形近字别名）。

    优先 4 字「X之星宿」（命中折行前的部分）；再短 3 字「X之星」兜底
    （OCR 把「X之星宿石」拆成「X之星」+「宿石」两行时仍能定位）。
    """
    aliases = next((a for e, a in XH_ELEMENTS if e == el), (el,))
    kws = []
    for a in aliases:
        kws.append(f"{a}之星宿")
        kws.append(f"{a}之星")
    return tuple(kws)


def green_picker(items, img_np):
    """follow green_picker：场景阶段只允许点「四象禁地」链接（回中心）。

    场景阶段追踪文案静态、星魂归化不改变文字——引擎正常不会点链接；
    万一补点（签名抖动/停滞看门狗），点中心链接只做「回星魂附近」，
    绝不点星宿石链接把人物带离搜索区。非本任务返回 None 走引擎默认。

    全部归位或已确认离开四象禁地时返回 PICK_FAIL：返回 None 会让引擎
    回落行级绿字点击，而面板上唯一的绿字仍是「四象禁地」链接——照样
    会把人物派回已完成的任务点（2026-11-22 用户实机）。
    """
    if not scene_active(items):
        return None
    if _done >= XH_SOUL_TOTAL or _left_scene:
        return task_tracker.PICK_FAIL
    return _link_point(items, img_np, ("四象禁地",))


def _walk_end(now):
    """点标签后的走位结束判定（位置连续稳定 / 超时）。"""
    global _walk_stable, _walk_pos
    return _walk_stable >= XH_WALK_STABLE_POLLS or \
        now - _walk_at >= XH_WALK_TIMEOUT


def _search_phase(win, items, img_np, frame_fn, now, prefix):
    """phase 0：找星魂 → 点标签 → 走位 → 点石柱 → 点「催动法力」。"""
    global _phase, _phase_at, _target, _walk_at, _walk_pos, _walk_stable, \
        _pillar_at, _pillar_tries, _search_at, _search_idx, _search_pos, \
        _search_still, _bad
    # 1) 星魂对话在场：点「催动法力，让星魂跟随自己移动。」
    if _soul_dialog_present(items):
        el = _dialog_soul_element(items)
        if el is not None:
            _target = el  # 对话文案是当前带魂元素的可靠证据（覆盖标签 OCR）
        if _click_green_option_once(win, items, img_np, "催动法力",
                                    prefix=prefix.strip("[] ")):
            name = _target or "?"
            log(f"{prefix}星魂归位：「{name}之星魂」已跟随，"
                f"前往对应星宿石（已完成 {_done}/{XH_SOUL_TOTAL}）", "SYS")
            _phase = 1
            _phase_at = now
            _link_at = 0.0
            _link_tries = 0
            _link_pos = None
            _link_still = 0
            _walk_at = 0.0
            _pillar_at = 0.0
            return True
        # 对话在场但选项未点中（低置信度/渲染中）：等下一轮询，不点世界
        return False
    # 1.5) 场景阻挡对话（2026-11-17 用户实机：刚进四象禁地时屏幕中间
    #      有入场剧情对话（「少侠不愧是武尊神看重之人…」），未关闭前
    #      点星魂标签会被对话框吞掉/误推进对话）：先点屏推进；推进器
    #      因站定/渲染守卫拒绝时也不做世界点击，等下一轮询
    rgb, bgr = _frame(win, items, frame_fn)
    if _scene_dismisser().try_dismiss(win, items, img_np,
                                      prefix=prefix.strip("[] "), img=rgb):
        return True
    if bgr is not None \
            and dialog_frame_score(bgr) >= DIALOG_FRAME_THRESHOLD:
        return False
    # 2) 已点标签：等走位结束后点星魂石柱
    if _walk_at:
        pos = _pos(win, items, frame_fn)
        if pos is None or _walk_pos is None:
            moved = True
        else:
            moved = abs(pos[1] - _walk_pos[1]) > 2 \
                or abs(pos[2] - _walk_pos[2]) > 2
        _walk_stable = 0 if moved else _walk_stable + 1
        _walk_pos = pos
        if not _walk_end(now):
            return False
        cands = _soul_labels(win, items, frame_fn, now, force=True)
        pt = _pillar_point(cands, _target)
        human_click(win, pt[0], pt[1])
        log(f"{prefix}星魂归位：已点击「{_target}之星魂」石柱 "
            f"@({pt[0]},{pt[1]})", "SYS")
        _walk_at = 0.0
        _pillar_at = now
        _pillar_tries += 1
        return True
    # 3) 已点石柱：等对话；超时后重试/放弃该星魂
    if _pillar_at:
        if now - _pillar_at < XH_PILLAR_RETRY_INTERVAL:
            return False
        _pillar_at = 0.0
        if _pillar_tries >= XH_PILLAR_RETRY_MAX:
            if _target is not None:
                _bad.add(_target)
            log(f"{prefix}星魂归位：「{_target}之星魂」石柱连续 "
                f"{_pillar_tries} 次未出现对话，暂时跳过", "WARN")
            _target = None
            _pillar_tries = 0
    # 4) 扫描视野内的星魂标签 → 点标签自动寻路
    cands = [(el, rect) for el, rect in
             _soul_labels(win, items, frame_fn, now) if el not in _bad]
    if cands:
        el, rect = cands[0]
        if el != _target:
            _pillar_tries = 0
        _target = el
        cx = rect[0] + (rect[2] - rect[0]) // 2
        cy = rect[1] + (rect[3] - rect[1]) // 2
        human_click(win, cx, cy)
        _walk_at = now
        _walk_pos = None
        _walk_stable = 0
        log(f"{prefix}星魂归位：已点击「{el}之星魂」前往", "SYS")
        return True
    # 5) 视野内无星魂：回中心 / 游走搜索（位置随机且不一定全可见）
    if now - _search_at < XH_SEARCH_INTERVAL:
        return False
    # 游走点须等上一次走位结束（站定 2 轮）再点，防连环改道原地打转；
    # 回中心（idx 0）不受限——它本身就是「走到中心」
    pos = _pos(win, items, frame_fn)
    if not _in_scene_pos(pos):
        return False  # 已离开四象禁地（最后归位后自动传出）：不做世界点击
    if pos is not None and _search_pos is not None \
            and (abs(pos[1] - _search_pos[1]) > 2
                 or abs(pos[2] - _search_pos[2]) > 2):
        _search_still = 0
    else:
        _search_still += 1
    _search_pos = pos
    if _search_idx > 0 and _search_still < XH_LINK_STILL_POLLS:
        return False
    _search_at = now
    idx = _search_idx
    _search_idx = (_search_idx + 1) % (len(XH_SEARCH_OFFSETS) + 1)
    if idx == 0:
        pt = _link_point(items, img_np, ("四象禁地",))
        if pt is None:
            return False
        desc = "回四象禁地中心"
    else:
        pt = XH_SEARCH_OFFSETS[idx - 1]
        desc = f"游走搜索 {idx}/{len(XH_SEARCH_OFFSETS)}"
    human_click(win, pt[0], pt[1])
    log(f"{prefix}星魂归位：视野内无星魂，{desc} @({pt[0]},{pt[1]})", "INFO")
    return True


def _stone_phase(win, items, img_np, frame_fn, now, prefix):
    """phase 1：点「X之星宿石」链接 → 到达 → 点「星魂归位！」

    链接查找带低对比度补偿兜底（见 XH_LINK_ENH_INTERVAL）：面板末行
    （氐/尾等链接）压在亮背景上时区域 OCR 会整行漏检，普通帧 items
    找不到链接不代表链接不存在——增强 OCR 重识别后再放弃。
    """
    global _phase, _phase_at, _done, _link_at, _link_tries, _link_pos, \
        _link_still, _enh_link_at
    # 到达星宿石：归位对话框在场 → 点「星魂归位！」
    if _arrive_dialog_present(items):
        if _click_green_option_once(win, items, img_np, "星魂归位",
                                    prefix=prefix.strip("[] ")):
            _done += 1
            log(f"{prefix}星魂归位：「{_target}之星魂」已归位"
                f"（{_done}/{XH_SOUL_TOTAL}），回四象禁地继续", "SYS")
            _phase = 2
            _phase_at = now
            _link_at = 0.0
            _link_tries = 0
            return True
        return False  # 对话在场但选项未点中：等下一轮询，不点追踪链接
    if now - _phase_at < XH_PHASE_DELAY:
        return False  # 选项对话关闭动画/浮字出现中，先不点追踪
    if _link_tries >= XH_LINK_CLICK_MAX:
        return False  # 已点满，等 follow 超时由下一轮续跑（状态保留）
    if now - _link_at < XH_LINK_CLICK_INTERVAL:
        return False
    # 寻路中不重复点时：位置仍在变化 → 等；连续不变且无到达对话 → 补点
    pos = _pos(win, items, frame_fn)
    if pos is not None and _link_pos is not None \
            and (abs(pos[1] - _link_pos[1]) > 2
                 or abs(pos[2] - _link_pos[2]) > 2):
        _link_still = 0
    else:
        _link_still += 1
    _link_pos = pos
    if _link_tries > 0 and _link_still < XH_LINK_STILL_POLLS:
        return False
    kws = _stone_keywords(_target or "")
    pt = _link_point(items, img_np, kws)
    if pt is None and now - _enh_link_at >= XH_LINK_ENH_INTERVAL:
        # 低对比度补偿（2026-11-19 实机 160s 卡死）：「、氏之星宿石、
        # 尾之星宿石处」末行压在亮背景下被区域 OCR 整行漏检——跟随后
        # 找不到星宿石链接，静默等到 follow 超时。增强 OCR（3x 对比度）
        # 实测可读回该行（尾之星宿石处 0.99、点击 166,327），命中即点
        _enh_link_at = now
        rgb, _ = _frame(win, items, frame_fn)
        if rgb is not None:
            try:
                enh = task_tracker.ocr_track_enhanced(rgb)
            except Exception:
                enh = None
            if enh:
                pt = _link_point(enh, rgb, kws)
    if pt is None:
        return False
    human_click(win, pt[0], pt[1])
    _link_at = now
    _link_tries += 1
    log(f"{prefix}星魂归位：已点击「{_target}之星宿石」追踪链接 "
        f"@({pt[0]},{pt[1]})", "SYS")
    return True


def _return_phase(win, items, img_np, frame_fn, now, prefix):
    """phase 2：点「四象禁地」链接回星魂附近 → 回 phase 0 继续找。

    两个不点回中心链接的前提（2026-11-22 用户实机）：
    - 六个星魂已全部归位（_done >= 6）：任务自动完成并传出四象禁地，
      无需再回中心；
    - 当前地图已不是四象禁地（传送途中/已传出）：追踪文案尚未刷新时
      点「四象禁地」链接会落到新地图上，等 follow 引擎按面板消失判定。
    """
    global _phase, _phase_at, _link_at, _link_tries, _search_at, _search_idx, \
        _search_pos, _search_still, _left_scene
    if now - _phase_at < XH_PHASE_DELAY:
        return False
    if _link_tries >= 1:
        if now - _link_at < XH_RETURN_WAIT:
            return False
        _phase = 0
        _phase_at = now
        _link_tries = 0
        _search_at = 0.0
        _search_idx = 0
        _search_pos = None
        _search_still = 0
        return False
    if _done >= XH_SOUL_TOTAL:
        return False  # 全部归位：任务自动收尾，不再回中心（等面板消失）
    pos = _pos(win, items, frame_fn)
    if not _in_scene_pos(pos):
        if not _left_scene:
            _left_scene = True
            name = pos[0] if isinstance(pos, tuple) else "位置未知"
            log(f"{prefix}星魂归位：已不在四象禁地（{name}），"
                f"不再点「四象禁地」链接，等待任务完成判定", "SYS")
        return False
    pt = _link_point(items, img_np, ("四象禁地",))
    if pt is None:
        _phase = 0  # 链接未读出（任务可能已完成）：交回找星魂阶段
        _phase_at = now
        return False
    human_click(win, pt[0], pt[1])
    _link_at = now
    _link_tries = 1
    log(f"{prefix}星魂归位：已点击「四象禁地」追踪链接回中心", "SYS")
    return True


def handle(win, items, img_np, frame_fn=None, prefix="[名望] "):
    """星魂归位状态机（follow extras 通道，每轮询一次）。

    frame_fn：(win, items) → (RGB np|None, BGR np|None)，共享帧回调
    （复用同轮询截帧）；None 时本模块自行截帧缓存。
    触发：追踪签名同时含「星魂归位」与「星宿石」（四象禁地执行阶段）。
    NPC 入口阶段（「看看天庭的星官X…」）不命中，由 follow 引擎绿字点击
    + 任务对话表处理；完成判定也交给 follow（追踪面板消失）。
    返回是否已处理。
    """
    global _missing, _frame_pos
    sig = _sig(items)
    if XH_TASK_KEYWORD not in sig:
        # 只在空闲态计数复位：战斗中 extras 走「仅对话区」通道（无追踪面板
        # items），mid-phase 复位会丢「带魂/已归位」状态导致重复拿魂
        # （同五庄封印 _seal_missing 只在 phase 0 计数的教训）
        if _phase == 0 and _target is None:
            _missing += 1
            if _missing >= XH_ABSENT_RESET:
                reset()
        return False
    _missing = 0
    if XH_SCENE_KEYWORD not in sig:
        return False
    if frame_fn is not None:
        _frame_pos = _FRAME_UNSET  # 新轮询：位置按本 items 重读
    now = time.time()
    if _phase == 1:
        return _stone_phase(win, items, img_np, frame_fn, now, prefix)
    if _phase == 2:
        return _return_phase(win, items, img_np, frame_fn, now, prefix)
    return _search_phase(win, items, img_np, frame_fn, now, prefix)
