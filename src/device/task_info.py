# -*- coding: utf-8 -*-
"""任务追踪内容结构化解析（OCR 行 → TrackTask / GiveOffer）。

用户 2026-11-27 要求：把任务追踪读取到的内容结构化出来，例如
    任务名称：急人所急
    所需物品：[{name: 千年魂石, honor: 40},
               {name: 天书残卷一, honor: 3},
               {name: 天书残卷二, honor: 3}]
给与环据此按「雅誉优先」设置排序候选（settings.load_give_honor_high_first：
高雅誉值优先 = 千年魂石 40 先试；低雅誉值优先 = 天书残卷 3 先试）。

本模块只做纯文本/数据解析（不截图、不 OCR、不点击），任务侧与
workflow 均可复用；天书卷号解析从 tasks/sanbaihuan 下沉到这里
（parse_volumes / volume_of），避免任务模块重复维护正则。
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from . import task_tracker

# ---- 天书残卷卷号（「天书残卷三或十」「天书残卷五、七、八或九」） ----
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_CN_OF = {v: k for k, v in _CN_NUM.items()}
_NUM = r"([一二三四五六七八九十])"
# 单卷号：悬停物品名提示框「天书残卷 第三卷」用（只取首个卷号）
_VOLUME_ONE_RE = re.compile(
    r"天书残卷\s*第?\s*[（(]?\s*" + _NUM + r"\s*[)）]?\s*卷?")
# 需求文案的卷号串：首个卷号 + 任意个后续卷号。
# 2026-11-27 实机「天书残卷五、七、八或九」任意一卷可交，旧正则只认
# 「或」连接的两个卷号，只解析出 {5}（用户报告）。行首绿字「、」也会
# 被 OCR 丢（换行后读出「八或九…」，拼接成「五、七八或九」），分隔符
# 一律可选；卷号最大为十，「五十」这类连写不存在，无误合并风险。
_VOLUME_LIST_RE = re.compile(
    r"天书残卷\s*第?\s*[（(]?\s*[一二三四五六七八九十]\s*[)）]?\s*卷?"
    r"(?:\s*[、,，或和及]?\s*第?\s*[（(]?\s*[一二三四五六七八九十]"
    r"\s*[)）]?\s*卷?)*")
_CN_NUM_CHAR_RE = re.compile(r"[一二三四五六七八九十]")


def parse_volumes(text):
    """文案/物品名 → 天书卷号集合（「天书残卷三或十」→ {3,10}，
    「天书残卷五、七、八或九」→ {5,7,8,9}）。"""
    m = _VOLUME_LIST_RE.search(text or "")
    if not m:
        return set()
    return {_CN_NUM[c] for c in _CN_NUM_CHAR_RE.findall(m.group(0))}


def volume_of(name):
    """物品名 → 卷号（「天书残卷 第三卷」→ 3；无卷号 None）。"""
    m = _VOLUME_ONE_RE.search(name or "")
    return _CN_NUM[m.group(1)] if m else None


# ---- 追踪面板标题（「·急人所急[江湖历练任务]」） ----
_TITLE_RE = re.compile(
    r"[·•・]?\s*([^\[\]【】]+?)\s*[\[【]\s*([^\]】]+?)\s*[\]】]")

# ---- 给与文案（「找到一个X(40点雅誉)或Y(3点雅誉)，并将其交给Z」） ----
# 取「找到」到「并将其交给/并给予/句读」之间的需求段
_SEGMENT_RE = re.compile(
    r"找到\s*(?:一个)?\s*(.+?)"
    r"(?:[，,]\s*(?:并将其交给|并给予)|[，,。；;]|$)")
# 需求段里的「物品名(40点雅誉)」；名称允许含「或」（天书残卷一或二）
_OFFER_RE = re.compile(
    r"([^，,。；;（(]+?)\s*[（(]\s*(?:获得)?\s*(\d+)\s*点雅[誉]?\s*[)）]")


@dataclass(frozen=True)
class GiveOffer:
    """一条可选给与物品：名称 + 雅誉值（天书按卷展开，volumes 标记卷号）。"""

    name: str
    honor: Optional[int] = None
    volumes: Tuple[int, ...] = ()
    raw: str = ""

    def label(self):
        """日志显示名：千年魂石(40雅誉)。"""
        return (f"{self.name}({self.honor}雅誉)"
                if self.honor is not None else self.name)

    def to_dict(self):
        """结构化字典（供 UI/日志/未来接口直接使用）。"""
        data = {"name": self.name, "honor": self.honor}
        if self.volumes:
            data["volumes"] = list(self.volumes)
        return data


@dataclass(frozen=True)
class TrackTask:
    """任务追踪面板的结构化内容。"""

    name: str                                   # 任务名称（急人所急）
    category: str                               # 方括号玩法名（江湖历练任务）
    content: str                                # 内容行拼接
    offers: Tuple[GiveOffer, ...] = ()          # 所需物品（给与类）

    def offers_text(self):
        return "、".join(o.label() for o in self.offers) or "无"

    def to_dict(self):
        """结构化字典：{"name": 任务名, "offers": [{name, honor, volumes}...]}。"""
        return {"name": self.name, "category": self.category,
                "content": self.content,
                "offers": [o.to_dict() for o in self.offers]}


def parse_track_task(items, category="江湖历练任务"):
    """追踪面板 OCR items → TrackTask；无标题行/类别不符返回 None。

    items 为 [(文本, (l,t,r,b), 置信度)]（follow 的 OCR 结果，可含对话区
    行，x ≥ task_tracker.PANEL_MAX_X 的行一律排除）。标题形如
    「·急人所急[江湖历练任务]」，内容取标题行下方的面板行拼接。
    """
    title_i = None
    name = cat = ""
    for i, (txt, rect, score) in enumerate(items or []):
        if score < 0.55 or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        m = _TITLE_RE.search(txt or "")
        if not m:
            continue
        if category and category not in m.group(2):
            continue
        name = m.group(1).strip(" ·•・\t　")
        cat = m.group(2).strip()
        title_i = i
        break
    if title_i is None:
        return None
    trect = items[title_i][1]
    lines = []
    for i, (txt, rect, score) in enumerate(items or []):
        if i == title_i or score < 0.55 \
                or rect[0] >= task_tracker.PANEL_MAX_X:
            continue
        if rect[1] < trect[1]:
            continue  # 标题行上方：面板表头「任务追踪」
        lines.append((rect[1], rect[0], txt))
    lines.sort(key=lambda ln: (ln[0], ln[1]))
    content = "".join(t for _y, _x, t in lines)
    return TrackTask(name=name, category=cat, content=content,
                     offers=tuple(parse_give_offers(content)))


def parse_give_offers(text):
    """给与文案 → 所需物品结构化列表（天书按卷展开，共享同一雅誉值）。

    实机「找到一个千年魂石(40点雅誉)或天书残卷一或二(3点雅誉)」→
    [千年魂石(40), 天书残卷一(3), 天书残卷二(3)]。
    """
    m = _SEGMENT_RE.search(text or "")
    if not m:
        return []
    offers = []
    for om in _OFFER_RE.finditer(m.group(1)):
        raw_name = (om.group(1) or "").strip().lstrip("或").strip(" 　")
        if not raw_name:
            continue
        honor = int(om.group(2))
        vols = parse_volumes(raw_name)
        if "天书残卷" in raw_name and vols:
            for v in sorted(vols):
                offers.append(GiveOffer(
                    name="天书残卷" + _CN_OF[v], honor=honor,
                    volumes=(v,), raw=om.group(0)))
        else:
            offers.append(GiveOffer(name=raw_name, honor=honor,
                                    raw=om.group(0)))
    return offers


def order_give_offers(offers, high_first=True):
    """按雅誉值排序候选：高雅誉优先（40→3）或低雅誉优先（3→40）。

    无雅誉值的条目保持原文顺序排在有雅誉条目之后（stable sort）。
    """
    honored = [o for o in offers if o.honor is not None]
    rest = [o for o in offers if o.honor is None]
    honored.sort(key=lambda o: o.honor, reverse=bool(high_first))
    return honored + rest
