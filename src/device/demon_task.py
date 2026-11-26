"""降魔任务（五倍）识别：从任务详情文本解析任务类型与目标（五倍手动寻路前置）。

降魔任务即五倍任务——同一玩法的不同叫法（用户 2026-11-23 明确），
任务栏分组行显示为「降魔任务」。

输入为任务栏「任务详情」的 OCR 文本（objective_text，段落已由
task_panel.objective_lines 截取），识别只做文本解析，不截图、不点击。

支持的实机文案（用户实机截图，任务栏 OCR 实测）：

1. 击杀类（三藏逢魔 等，地图/目标/坐标随轮次变化）：
   「三藏黑松林迷路……义士速赶至四圣庄，消灭神通魔尊(四圣庄，163，105)，
     为营救三藏而战！」
   → kind=kill，map=四圣庄，coords=[(163,105)]，target=神通魔尊
   另一实测变体：万寿山，37，11 / 天眼魔尊。

2. 暗雷 / 锁妖网类（殿前献艺）：
   「义士且去平顶山或火云戈壁，使用道具栏的锁妖网，与队友合力捕捉
     3只暗雷怪……」
   → kind=reclaim，reclaim=True：当前五倍逻辑无法自动推进，需重新领取
   替换（与 wubei 现有「锁妖网捕捉」替换规则同语义）。

3. 显形镜类（宝象迷情）：
   「前往平顶山的(187,27)，(269,28)几处使用显形镜查探。」
   → kind=xianxing，map=平顶山，coords=[(187,27),(269,28)]（A、B 顺序），
     item=显形镜，item_icon=wubei_xianxingjing.png（用户提供素材，
     位于背包-任务储物格；下一步五倍手动寻路使用）。

4. 黄袍怪多轮类（智斗黄袍）：
   「前往平顶山(302,22)消灭愤怒的黄袍怪。智斗黄袍为限时通关任务，
     一旦切入战斗，请在7分钟内完成尽可能多轮次的挑战（最多5轮）。」
   → kind=huangpao，map=平顶山，coords=[(302,22)]，target=愤怒的黄袍怪，
     max_rounds=5（解析「最多N轮」，缺失默认 5；2026-11-23 日志实测
     一轮连续战斗 5 场）。普通击杀类 max_rounds=1。
   （多场战斗共用同一目标；注意三藏逢魔文案里的「黄袍怪老巢」是叙述，
     不能误判，仅按击杀目标/任务名判定）。

5. 奎星归位（奎木狼，2026-11-25 用户实机截图）：
   「奎木狼(火云戈壁，134，12)终于要出动了，与他一决雌雄吧！」
   → kind=kill，target=奎木狼，map=火云戈壁，coords=[(134,12)]。
   目标名紧邻括号地图坐标、没有「消灭/击败」等击杀动词，按已知任务名
   「奎星归位」判定（用户确认该任务同样是杀一次）。

6. 修罗任务（2026-11-26 用户实机截图，任务栏「常规玩法」分组）：
   「前往凤巢六层(84,55)处消灭修罗。」
   → kind=kill，map=凤巢六层，coords=[(84,55)]，target=修罗，
     max_rounds=1（修罗任务恒为单杀，用户 2026-11-26）。层级图地名
     （龙窟/凤巢/大雁塔 + 层数）整名保留，不被世界地图归一模糊成基础
     图名；任务提示段（玩法说明）由 task_panel 截掉，不参与识别。

未匹配已知文案时 kind=unknown，仍尽量返回地图/坐标，供调用方决策。
OCR 容错：全/半角括号与逗号、缺右括号、显形镜被读成「豆形镜」等（取
「形镜」子串）；地图名经 map.resolve_world_map_name 繁体/形近归一。
"""
import re

from . import map as game_map

KIND_KILL = "kill"            # 击杀类：地图 + 坐标 + 目标名
KIND_XIANXING = "xianxing"    # 显形镜类：地图 + A/B 两坐标 + 显形镜
KIND_HUANGPAO = "huangpao"    # 黄袍怪多轮类（智斗黄袍）：同目标多场战斗
KIND_RECLAIM = "reclaim"      # 暗雷/锁妖网类：领取到即需重新领取替换
KIND_UNKNOWN = "unknown"

# 需重新领取的任务关键词（与五倍现有替换规则同语义）
RECLAIM_KEYWORDS = ("暗雷", "锁妖网")
# 已知任务名兜底（目标文案 OCR 失败时仍能判类型；新变体在此追加）
RECLAIM_TASK_NAMES = ("殿前献艺",)
XIANXING_TASK_NAMES = ("宝象迷情",)
# 黄袍怪多轮任务的稳定标记：击杀目标含「黄袍」或任务名「智斗黄袍」。
# 不能直接按全文含「黄袍怪」判定——三藏逢魔文案「误入黄袍怪老巢」是
# 叙述而非击杀目标。
HUANGPAO_KEYWORD = "黄袍"
HUANGPAO_TASK_NAMES = ("智斗黄袍",)
# 已知击杀任务名兜底（目标文案无「消灭/击败」动词时仍按击杀处理；
# 新增变体在此追加）。奎星归位（奎木狼，2026-11-25 用户实机截图）：
# 任务目的为「奎木狼(火云戈壁，134，12)终于要出动了，与他一决雌雄吧！」
# ——目标名紧邻括号地图坐标，没有击杀动词；「奎星」子串容忍尾部 OCR 误读。
# 修罗任务（2026-11-26 用户实机截图）：任务栏「常规玩法」分组下的单杀
# 任务，每轮 1 场战斗；任务目的形如「前往凤巢六层(84,55)处消灭修罗。」
KILL_TASK_NAMES = ("奎星归位", "奎星", "修罗任务")
_HUANGPAO_KILL_RE = re.compile(
    r"(?:消灭|击败|击杀)[^。！；，]{0,8}黄袍怪")
# 黄袍怪轮次数：任务文案写明「（最多5轮）」，2026-11-23 可乐可口日志
# 实测一轮智斗黄袍连续战斗 5 场（第 13~17 场）后本轮完成；文案 OCR
# 缺失时按 5 兜底。普通击杀类（三藏逢魔等）每轮 1 场战斗。
HUANGPAO_DEFAULT_ROUNDS = 5
_MAX_ROUNDS_RE = re.compile(r"最多\s*(\d+)\s*轮")

# 显形镜道具：名称与背包-任务储物格的图标模板（用户 2026-11-23 素材；
# 原图 47x46，裁去四周深色留白后为 24x44 的图标本体）
XIANXING_ITEM = "显形镜"
XIANXING_ICON = "wubei_xianxingjing.png"
# 用「形镜」而非「显形镜」：追踪面板 OCR 对「显」稳定误读成「豆」
# （实测「豆形镜」，见 wubei.XIANXINGJING_LINK_KEYWORD 同款结论）
XIANXING_KEYWORDS = ("形镜",)

_TARGET_VERBS = ("消灭", "击败", "击杀", "降服", "除掉", "擒获", "捉拿")

# 地图 + 坐标三元组：如「(四圣庄，163，105)」「（万寿山，37，11）」
# 地图与 x 之间的逗号允许缺失：OCR 有时把间隔读成空格，_clean 去空格后
# 地图与坐标直连（2026-11-25 奎星归位实测「火云戈壁，134，12」）。
_MAP_COORD_RE = re.compile(
    r"[（(]\s*(?P<map>[一-龥]{2,8})\s*[，,、]?\s*"
    r"(?P<x>\d{1,3})\s*[，,、.．]\s*(?P<y>\d{1,3})\s*[)）]?")
# 纯坐标对：如「(187,27)」（显形镜 A/B）；括号可缺右半边
_COORD_PAIR_RE = re.compile(
    r"[（(]\s*(?P<x>\d{1,3})\s*[，,、.．]\s*(?P<y>\d{1,3})\s*[)）]?")
# 坐标前的「前往平顶山的(187,27)」：取坐标前的地名候选。
# place 用非贪婪，否则贪婪会把结构助词「的」吃进地名（「平项山的」）
_PLACE_BEFORE_COORD_RE = re.compile(
    r"(?P<place>[一-龥]{2,10}?)(?:的)?\s*[（(]\s*\d{1,3}"
    r"\s*[，,、.．]\s*\d{1,3}")
# 击杀目标：消灭/击败…（可带「3只」数量）；目标名 2~8 个汉字
_TARGET_RE = re.compile(
    r"(?:" + "|".join(_TARGET_VERBS) + r")(?:\d+只)?"
    r"(?P<target>[一-龥]{2,8})")
# 目标名紧邻括号地图坐标（无击杀动词的击杀文案，奎星归位）：
# 「奎木狼(火云戈壁，134，12)…」。名称取括号前整段中文串再剥离前置
# 动词——固定长度字符集会在长叙述里从串中间截出半截名字。
_NAME_BEFORE_MAP_COORD_RE = re.compile(
    r"(?P<name>[一-龥]+)[（(]\s*(?P<map>[一-龥]{2,8})\s*[，,、]?\s*"
    r"(?P<x>\d{1,3})\s*[，,、.．]\s*(?P<y>\d{1,3})\s*[)）]?")

# 地名前的动词（「义士速赶至四圣庄」取地名时剥离）
_PLACE_VERBS = ("速赶至", "前往", "赶到", "去往", "去到", "去", "到", "至")


def _clean(text):
    return re.sub(r"\s+", "", text or "")


_MAP_NAMES_CACHE = None


def _known_map_names():
    """已知地图名（普通地图 + 层级图 + 别名），按长度降序（供后缀匹配）。

    层级图名（凤巢六层/龙窟五层/大雁塔四层…）由 map_nav 维护——函数内
    导入防循环（map_nav → battle → …），只在首次识别时建立缓存。
    """
    global _MAP_NAMES_CACHE
    if _MAP_NAMES_CACHE is None:
        from . import map_nav  # 函数内导入防循环
        names = set(game_map.WORLD_MAP_LABELS) | set(map_nav.layer_map_names())
        names |= set((game_map.WORLD_MAP_NAME_ALIASES or {}).keys())
        _MAP_NAMES_CACHE = sorted((n for n in names if n),
                                  key=len, reverse=True)
    return _MAP_NAMES_CACHE


def _resolve_map(name):
    """地名归一：层级图/普通地图按整名或后缀命中；未收录保留原文。

    修罗任务的层级图地名（如「凤巢六层」）必须整名保留——世界地图归一
    （resolve_world_map_name）会把层名模糊归一成基础图「凤巢」，丢层号
    （2026-11-26 用户实机截图校准）。坐标前的地名候选可能带叙述前缀
    （「请速去大雁塔四层」），按已知地名后缀取最长者；仍未命中再走
    繁体/形近归一。
    """
    if not name:
        return None
    name = _clean(name)
    aliases = game_map.WORLD_MAP_NAME_ALIASES or {}
    names = _known_map_names()
    for known in names:
        if name == known:
            return aliases.get(known, known)
    for known in names:
        if len(name) > len(known) and name.endswith(known):
            return aliases.get(known, known)
    return game_map.resolve_world_map_name(name) or name


def _strip_place_verbs(place):
    for verb in _PLACE_VERBS:
        if place.startswith(verb) and len(place) > len(verb) + 1:
            return place[len(verb):]
    return place


def _map_before_coord(text):
    """坐标前的地名候选（「前往平顶山的(187,27)」→ 平顶山）。"""
    m = _PLACE_BEFORE_COORD_RE.search(text)
    if not m:
        return None
    return _strip_place_verbs(m.group("place"))


def _dedupe_coords(coords):
    out = []
    for c in coords:
        if c not in out:
            out.append(c)
    return out


def _parse_target(text):
    """击杀目标名（如「消灭神通魔尊(」→ 神通魔尊）。"""
    m = _TARGET_RE.search(text)
    return m.group("target") if m else None


def _is_xianxing(text, task_name):
    if any(kw in text for kw in XIANXING_KEYWORDS):
        return True
    return any(n in (task_name or "") for n in XIANXING_TASK_NAMES)


def _is_reclaim(text, task_name):
    for kw in RECLAIM_KEYWORDS:
        if kw in text:
            return kw
    for name in RECLAIM_TASK_NAMES:
        if name in (task_name or ""):
            return name
    return None


def _is_huangpao(text, task_name, target):
    """黄袍怪多轮任务判定（不按全文含「黄袍怪」：三藏逢魔有「黄袍怪老巢」）。"""
    if target and HUANGPAO_KEYWORD in target:
        return True
    if any(name in (task_name or "") for name in HUANGPAO_TASK_NAMES):
        return True
    return bool(_HUANGPAO_KILL_RE.search(text))


def _is_known_kill_task(task_name):
    """任务名是否已知击杀类（目标文案无击杀动词时按任务名兜底判类型）。"""
    return any(name in (task_name or "") for name in KILL_TASK_NAMES)


def _parse_name_before_coord(text):
    """「奎木狼(火云戈壁，134，12)…」式目标名（无击杀动词的击杀文案）。

    名称取括号前的中文串（可能混入叙述），按最后一个击杀动词截断
    （「速去消灭奎木狼」→「奎木狼」）；2~8 字才认定为名字。
    """
    for m in _NAME_BEFORE_MAP_COORD_RE.finditer(text):
        name = m.group("name")
        for verb in _TARGET_VERBS:
            idx = name.rfind(verb)
            if idx >= 0:
                name = name[idx + len(verb):]
        if 2 <= len(name) <= 8:
            return name
    return None


def recognize(task_name, objective_text):
    """识别降魔任务类型与目标，返回结构化 dict（字段见模块 docstring）。

    task_name：任务列表中的子任务名（如「三藏逢魔」；OCR 原生文本）。
    objective_text：任务详情「任务目的」段落拼接文本（无空格）。
    """
    text = _clean(objective_text)
    rec = {
        "task": task_name or "",
        "kind": KIND_UNKNOWN,
        "reclaim": False,
        "reclaim_reason": "",
        "map": None,
        "coords": [],
        "coord_a": None,
        "coord_b": None,
        "target": None,
        "max_rounds": None,
        "item": None,
        "item_icon": None,
        "item_location": None,
        "objective_text": text,
    }
    reclaim_hit = _is_reclaim(text, task_name)
    if reclaim_hit:
        rec.update(
            kind=KIND_RECLAIM,
            reclaim=True,
            reclaim_reason=f"任务文案含「{reclaim_hit}」，当前逻辑无法自动"
                           f"推进，需重新领取替换",
        )
        return rec

    map_tuples = [
        (m.group("map"), int(m.group("x")), int(m.group("y")))
        for m in _MAP_COORD_RE.finditer(text)
    ]
    plain_coords = [
        (int(m.group("x")), int(m.group("y")))
        for m in _COORD_PAIR_RE.finditer(text)
    ]

    if _is_xianxing(text, task_name):
        coords = _dedupe_coords(
            [(x, y) for _mp, x, y in map_tuples] + plain_coords)
        place = map_tuples[0][0] if map_tuples else _map_before_coord(text)
        rec.update(
            kind=KIND_XIANXING,
            map=_resolve_map(place),
            coords=coords,
            coord_a=coords[0] if coords else None,
            coord_b=coords[1] if len(coords) > 1 else None,
            item=XIANXING_ITEM,
            item_icon=XIANXING_ICON,
            item_location="task_box",  # 背包-任务储物格（bag.open_task_box）
        )
        return rec

    kill_task = _is_known_kill_task(task_name)
    target = _parse_target(text)
    if not target and kill_task:
        # 奎星归位等无击杀动词的击杀文案：「目标名(地图，x，y)…」
        target = _parse_name_before_coord(text)
    coords = _dedupe_coords(
        [(x, y) for _mp, x, y in map_tuples] + plain_coords)
    place = map_tuples[0][0] if map_tuples else _map_before_coord(text)
    if map_tuples or plain_coords or place:
        rec["map"] = _resolve_map(place)
        rec["coords"] = coords
        rec["coord_a"] = coords[0] if coords else None
        rec["coord_b"] = coords[1] if len(coords) > 1 else None
    if target:
        rec["target"] = target
        rec["kind"] = KIND_HUANGPAO if _is_huangpao(
            text, task_name, target) else KIND_KILL
    elif kill_task:
        # 任务名已知为击杀类（奎星归位）：目标 OCR 缺失也按击杀处理
        rec["kind"] = KIND_KILL
    elif _is_huangpao(text, task_name, None):
        rec["kind"] = KIND_HUANGPAO
    if rec["kind"] == KIND_HUANGPAO:
        m = _MAX_ROUNDS_RE.search(text)
        rec["max_rounds"] = int(m.group(1)) if m else HUANGPAO_DEFAULT_ROUNDS
    elif rec["kind"] == KIND_KILL:
        rec["max_rounds"] = 1
    return rec


def describe(rec):
    """识别结果单行摘要（任务栏日志用）。"""
    if not rec:
        return "无识别结果"
    kind = rec.get("kind")
    if kind == KIND_RECLAIM:
        return f"类型=需重新领取 | {rec.get('reclaim_reason')}"
    if kind == KIND_XIANXING:
        return (f"类型=显形镜任务 | 地图={rec.get('map') or '?'} "
                f"A={rec.get('coord_a')} B={rec.get('coord_b')} "
                f"道具={rec.get('item')}({rec.get('item_icon')}, "
                f"背包-任务储物格)")
    if kind == KIND_HUANGPAO:
        return (f"类型=黄袍怪多轮任务 | 地图={rec.get('map') or '?'} "
                f"坐标={rec.get('coords')} 目标={rec.get('target') or '?'} "
                f"轮次={rec.get('max_rounds') or HUANGPAO_DEFAULT_ROUNDS}")
    if kind == KIND_KILL:
        return (f"类型=击杀任务 | 地图={rec.get('map') or '?'} "
                f"坐标={rec.get('coords')} 目标={rec.get('target') or '?'} "
                f"轮次={rec.get('max_rounds') or 1}")
    return (f"类型=未识别（未匹配已知降魔任务文案）"
            f" 地图={rec.get('map') or '?'} 坐标={rec.get('coords') or '?'}")
