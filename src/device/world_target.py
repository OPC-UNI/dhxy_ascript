"""世界区 NPC 名字 OCR 定位（手动寻路到达后找不到任务标记时的兜底）。

手动寻路到达目标坐标后先找 NPC 头顶的「任务」小牌模板；目标点位于地图
边缘时游戏不渲染该小牌（用户 2026-11-24 确认是任务设计），Alt+A 隐藏
界面后仍找不到时，改为 OCR 世界区的 NPC 名字并点击名字上方：
- 五倍：任务栏已知完整目标名（例：智慧魔尊 / 白龙马 / 愤怒的黄袍怪 /
  奎星，经 demon_task 识别结果传入）；
- 修罗：领取对话框只给坐标，没有完整名字，但目标名必为「xx修罗」。

OCR 条带与用法对齐名望沙都修罗 / 客栈阿旺的既有实测：区域快引擎按 736
宽分段（宽 ≤736 的裁剪按原尺寸检测，小字名字原尺寸检出；1200+ 宽整条
会被压到 736 导致小字漏检），两段横向重叠 76px 防名字正好压在分段
边界被裁掉（星魂归位左缘裁字教训，2026-11-19）。游戏 NPC 名字渲染在
角色下方，名字框中心正上方 40px 即角色本体（用户标定，名望/客栈共用）。

任务追踪面板里的同名文案必须排除：面板可见时按标题条模板动态矩形排除
（track_content_region）；标题条未命中时**不做静态左列兜底**——本兜底
通常在 Alt+A 隐藏界面后调用，面板本就不在，静态 (0,200,400,600) 会误杀
窗口左缘的 NPC（名望沙都修罗 2026-11-19 教训）。

匹配分两轮：先精确包含（行内含目标名 / 行以「修罗」结尾），精确全未命中
才启用模糊（4 字以上名字允许 1 字 OCR 误读：2026-11-24 截图实测世界名
「智慧魔尊」被 OCR 读成「岩慧魔尊」，0.85 阈值会漏——精确未命中场景
没有更可靠的线索，模糊兜底优于直接判失败）。行内命中名字子串时按字符
位置收窄点击点（玩家名与 NPC 名同排被 OCR 合并成一行时，整行中心会点
到玩家；同星魂归位 2026-11-24 教训）。
"""
import numpy as np

from ..core.app_utils import norm_text
from . import map as game_map
from . import task_tracker
from .dialog_common import _fuzzy_contains

# 世界区 OCR 条带（y 100~760 排除顶部 HUD 与底部快捷栏；736 宽分段 +
# 76px 横向重叠，同客栈阿旺 AWANG_NAME_BANDS 实测）
NAME_BANDS = ((0, 100, 736, 760), (660, 100, 1378, 760))
NAME_MIN_SCORE = 0.55        # OCR 行置信度下限（同各任务模块）
NAME_CLICK_OFFSET = 40       # 点击点 = 名字框（子串框）中心正上方像素
NAME_DEDUPE_PX = 24          # 同一 NPC 多行命中去重间距（像素）
# 精确未命中时的模糊兜底阈值：4 字名 1 字误读 = 0.75（截图实测）；3 字名
# 1 字误读 = 0.67 不达阈值，短名不做模糊（误点相似名怪风险高于收益）
NAME_FUZZY_MIN = 0.75
NAME_FUZZY_MIN_LEN = 4
# 修罗名字长度上限（「xx修罗」=4 字；玩家名/NPC 名合并行留余量；超过
# 上限的行多半是面板文案/整句，不点）
NAME_SUFFIX_MAX_LEN = 16
NAME_SUFFIX_TAKE = 4         # 后缀命中取行尾字数（「xx修罗」=修饰 2 + 后缀 2）


def _rects_intersect(a, b):
    """两矩形 [l,t,r,b] 是否相交（碰边算相交，同名望 _rects_intersect）。"""
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _sub_rect(rect, text, idx, length):
    """OCR 行矩形内按字符位置换算子串矩形（近似；同星魂归位 _sub_rect）。

    OCR 只给整行并集框。玩家名与 NPC 名同排时整行中心会落到玩家身上
    （点名字只会选中玩家/空走），按子串在行内的字符位置收窄。
    """
    if not text or length <= 0 or len(text) <= length:
        return tuple(rect)
    l, t, r, b = rect
    w = r - l
    x0 = l + int(round(w * idx / float(len(text))))
    x1 = l + int(round(w * (idx + length) / float(len(text))))
    return (x0, t, x1, b)


def _panel_rect(img):
    """任务追踪面板内容区（动态标题条矩形）；未命中返回 None（不排除）。"""
    return task_tracker.track_content_region(np.asarray(img))


def _name_lines(img):
    """世界区 OCR 得到名字候选行 [(txt, rect, score)]（排除追踪面板同名文案）。"""
    items, _ = game_map.ocr_regions(img, NAME_BANDS)
    panel = _panel_rect(img)
    lines = []
    for txt, rect, score in items or []:
        if score < NAME_MIN_SCORE or not txt:
            continue
        if panel is not None and _rects_intersect(rect, panel):
            continue
        lines.append((txt, tuple(rect), float(score)))
    return lines


def scan_name_lines(img):
    """诊断用：世界区 OCR 原始名字行 [(txt, rect, score)]（只读）。

    与 find_named_points / find_suffix_points 共用条带、置信度与追踪
    面板排除逻辑，供诊断按钮打印当前画面能识别到的名字，人工核对
    「模板缺失时名字 OCR 是否可用」。
    """
    return _name_lines(img)


def _point(rect):
    """名字（子串）框 → 点击点：中心正上方 NAME_CLICK_OFFSET（角色本体）。"""
    x = (rect[0] + rect[2]) // 2
    y = int(rect[1]) - NAME_CLICK_OFFSET
    return (int(x), max(0, y))


def _dedupe(points):
    """同一点附近的重复命中（两段条带重叠区名字被 OCR 两次）去重。"""
    out = []
    for txt, pt in points:
        if all((pt[0] - q[0]) ** 2 + (pt[1] - q[1]) ** 2
               >= NAME_DEDUPE_PX ** 2 for _t, q in out):
            out.append((txt, pt))
    return out


def _exact_named(txt, rect, wanted):
    """精确包含匹配：返回 (子矩形, 命中名) 或 None。"""
    norm = norm_text(txt)
    if not norm:
        return None
    for name in wanted:
        needle = norm_text(name)
        if not needle or needle not in norm:
            continue
        idx = txt.find(name)
        sub = _sub_rect(rect, txt, idx, len(name)) if idx >= 0 else rect
        return sub, name
    return None


def _fuzzy_named(txt, rect, wanted):
    """模糊匹配（仅 4 字以上名字，允许 1 字 OCR 误读）：(rect, 命中名)。"""
    norm = norm_text(txt)
    if not norm:
        return None
    for name in wanted:
        needle = norm_text(name)
        if len(needle) < NAME_FUZZY_MIN_LEN:
            continue
        if _fuzzy_contains(norm, needle, NAME_FUZZY_MIN):
            return rect, name
    return None


def find_named_points(img, names, lines=None):
    """按完整目标名找世界区名字，返回名字上方点击点 [(txt, (x, y))]。

    names 为可接受的名字列表（变体由调用方展开，如黄袍怪三种写法）。
    先精确包含；全部未命中才用模糊兜底（见模块说明）。按点击点从上到
    下、从左到右排序。lines 可传入 scan_name_lines 的已扫结果复用
    （诊断一次截图只 OCR 一遍），缺省现场 OCR。
    """
    wanted = [n for n in (names or []) if n]
    if not wanted:
        return []
    lines = lines if lines is not None else _name_lines(img)
    out = []
    for txt, rect, _score in lines:
        hit = _exact_named(txt, rect, wanted)
        if hit is not None:
            out.append((txt, _point(hit[0])))
    if not out:
        for txt, rect, _score in lines:
            hit = _fuzzy_named(txt, rect, wanted)
            if hit is not None:
                out.append((txt, _point(hit[0])))
    out.sort(key=lambda p: (p[1][1], p[1][0]))
    return _dedupe(out)


def find_suffix_points(img, suffixes, lines=None):
    """按名字后缀找世界区名字（修罗「xx修罗」），返回名字上方点击点。

    行必须以后缀结尾（规范化后，别名如 修罗/修羅）。点击点取行尾
    NAME_SUFFIX_TAKE 字换算的子串框上方——玩家名与 NPC 名合并成一行时
    点击点仍落在 NPC 一侧（2026-11-24 星魂归位合并行教训）。
    lines 可传入 scan_name_lines 的已扫结果复用（诊断），缺省现场 OCR。
    """
    sufs = [norm_text(s) for s in (suffixes or []) if s]
    if not sufs:
        return []
    max_suf = max(len(s) for s in sufs)
    out = []
    for txt, rect, _score in (lines if lines is not None else _name_lines(img)):
        norm = norm_text(txt)
        if len(norm) < max_suf or len(norm) > NAME_SUFFIX_MAX_LEN:
            continue
        if not any(norm.endswith(s) for s in sufs):
            continue
        take = min(len(txt), NAME_SUFFIX_TAKE)
        sub = _sub_rect(rect, txt, len(txt) - take, take)
        out.append((txt, _point(sub)))
    out.sort(key=lambda p: (p[1][1], p[1][0]))
    return _dedupe(out)
