# -*- coding: utf-8 -*-
"""物品模板与储物位置统一登记（物品目录）。

项目里的物品图标模板很多，来源分两类：普通背包各储物格（bag）与
背包-任务储物格（task_box）。本模块是物品模板/名称/储物位置的单一
来源：新增物品先在这里登记，任务模块通过 `item(key)` 取用，不再各自
维护散落的 icon 常量。既有常量按迁移进度逐步改为引用本表；新代码
必须走本表（用户 2026-11-26 统一维护要求）。

约定：
- 模板统一放 res/img。新物品图标命名 `item_<key>.png`；历史模板保留
  原文件名，在条目里登记实际文件名，不做物理迁移（迁移收益低于风险）。
- storage：BAG=普通背包储物格；TASK_BOX=背包-任务储物格；
  ANY=两处都可能（调用方按任务状态决定）。查找能力仍由 bag.py 提供
  （find_item_with_slots / open_task_box + find_item_in_bag）。
- name：游戏内名称，用于日志、OCR 匹配与悬停物品名校验。
- category / attr_value：类别型给与（「找到属性13的炼妖石」/「找到
  9-12属性的炼妖石」，任务文案只写类别与属性时按类别批量取候选，任一
  候选可交）。attr_value 是图标角标「+N」的数值——角标相同的同类物品
  模板会互相命中（实机 +8/+9 沧海珠 0.925、蓝田玉 0.98），属性最终以
  悬停提示框的属性行为准（give_flow.pick_and_give_attr）。
  等级类别型给与（「找到1级或2级宝宝装备」，用户 2026-11-27）复用同一
  机制：category=「宝宝装备」、attr_value=装备等级；给与前同样以悬停
  提示框的「等级：N级」行复核（give_flow.pick_and_give_level）。
- threshold：模板专用匹配阈值（0=用调用方默认阈值）。模板未裁剪、带
  左上角数量角标的物品（摄妖香/珍兽丹）数量字每次渲染都可能不同，分数
  会掉到默认阈值以下，按用户要求用同款宽松阈值 0.69；误命中由悬停
  物品名 OCR 复核兜底。按图标文件名取用见 threshold_for_icon（背包
  find_item_in_bag 与给与面板匹配自动采用）。
"""
from dataclasses import dataclass

BAG = "bag"              # 普通背包（各储物格，bag.find_item_with_slots）
TASK_BOX = "task_box"    # 背包-任务储物格（bag.open_task_box + find）
ANY = "any"              # 两处都可能
STORAGES = (BAG, TASK_BOX, ANY)


@dataclass(frozen=True)
class ItemDef:
    """一件游戏物品的登记信息。"""

    key: str
    name: str
    icon: str
    storage: str = BAG
    note: str = ""
    category: str = ""      # 游戏内类别（如「炼妖石」「宝宝装备」），类别型给与用
    attr_value: int = 0     # 类别型给与的数值属性：炼妖石=图标角标「+N」，
                            # 宝宝装备=装备等级；0=未登记
    aliases: tuple = ()     # 任务文案 OCR 变体（如「天外石」= 天外飞石）
    threshold: float = 0.0  # 模板专用匹配阈值（0=调用方默认；数量角标模板放宽）
    craftable: bool = False  # 可在庭院聚灵台用灵气打造（300环给与环缺物时
                             # 先去打造；触发判定见 sanbaihuan）


ITEMS = {
    # ---- 普通背包 ----
    "mihoutao": ItemDef(
        "mihoutao", "猕猴桃", "item_mihoutao.png", BAG,
        "用户 2026-11-26 提供（46x38）"),
    "yimengfu": ItemDef(
        "yimengfu", "忆梦符", "item_yimengfu.png", BAG,
        "属性13（用户 2026-11-27 提供 48x48）",
        craftable=True),
    "sheyaoxiang": ItemDef(
        "sheyaoxiang", "摄妖香", "sheyaoxiang.png", BAG,
        "自动使用摄妖香（bag.use_sheyaoxiang）；模板带数量角标",
        threshold=0.69),
    "xianglian": ItemDef(
        "xianglian", "项链", "fuzhuangdian_xianglian.png", BAG,
        "五环购买/给与（与服装店同一模板）"),
    "jiuzhuanhuanhuntang": ItemDef(
        "jiuzhuanhuanhuntang", "九转还魂汤",
        "kezhan__jiuzhuanhuanhuntang.png", BAG, "客栈给与-苏巧儿"),
    "dinghuntang": ItemDef(
        "dinghuntang", "定魂汤", "bag_dinghuntang.png", BAG,
        "客栈给与-王大力（历史文件名 bag_dinghuntang）"),
    "gaodengshicai": ItemDef(
        "gaodengshicai", "高等食材", "kezhan_gaodengshicai.png", BAG,
        "客栈给与-婉儿姑娘/兜底"),
    "taozhi": ItemDef(
        "taozhi", "桃枝", "kezhan_taozhi.png", BAG, "客栈给与-蟠桃园"),
    "yihelipin": ItemDef(
        "yihelipin", "一盒礼品", "kezhan_yihelipin.png", BAG,
        "客栈给与-掌柜/女儿国"),
    "jiguancailiao": ItemDef(
        "jiguancailiao", "机关材料", "kezhan_jiguancailiao.png", BAG,
        "客栈给与-木匠"),
    "mixin": ItemDef(
        "mixin", "密信", "kezhan_mixin.png", BAG,
        "客栈给与-太上老君/玉面狐狸"),
    "qingtie": ItemDef(
        "qingtie", "请帖", "kezhan_qingtie.png", BAG,
        "客栈给与-李有田/宋有志/夏哈普多"),
    "hualidejinhe": ItemDef(
        "hualidejinhe", "华丽的锦盒", "kezhan_hualidejinhe.png", BAG,
        "客栈给与-玉皇大帝"),
    "xugugao": ItemDef(
        "xugugao", "续骨膏", "kezhan_xugugao.png", BAG, "客栈给与-桃小淘"),
    "tianshu": ItemDef(
        "tianshu", "天书残卷", "sanbaihuan_give_tianshu.png", BAG,
        "300环给与（按卷号选物，图标只定位候选格，卷号靠悬停提示名 OCR）"),
    # 300环双选一给与（2026-11-27 可乐可口实机「千年魂石(40点雅誉)或
    # 天书残卷一或二(3点雅誉)」）：该环打开「请从两种物品任选一种给予」
    # 面板，物品格只列可交物品，点图标选中 → 确定。模板取自面板物品格
    # 原生 47x47（无数量角标；用户提供的背包截图 39x39 带「9」角标，
    # 实测同帧仅 0.59，不能直接用）。
    "qiannianhuns": ItemDef(
        "qiannianhuns", "千年魂石", "item_qiannianhuns.png", BAG,
        "300环双选一给与（2026-11-27 实机采集 47x47）"),
    # 300环普通给与（用户 2026-11-27 提供模板）。天外飞石的任务文案
    # OCR 偶尔漏「飞」读成「天外石」，登记别名让 find_by_name 仍命中，
    # 否则会落进未登记物品的全格悬停兜底（实机读到「无」/选不中给与）。
    "tianwaifeishi": ItemDef(
        "tianwaifeishi", "天外飞石", "item_tianwaifeishi.png", BAG,
        "300环给与（用户 2026-11-27 提供 47x46）",
        aliases=("天外石",)),
    "neidan_jinghua": ItemDef(
        "neidan_jinghua", "内丹精华", "item_neidan_jinghua.png", BAG,
        "300环给与（用户 2026-11-27 提供 42x43；模板去数量角标 28x34）"),
    "qiannianhantie": ItemDef(
        "qiannianhantie", "千年寒铁", "item_qiannianhantie.png", BAG,
        "300环给与（用户 2026-11-27 提供 47x47）"),
    "lingbaotianweiyin": ItemDef(
        "lingbaotianweiyin", "灵宝天威印", "item_lingbaotianweiyin.png", BAG,
        "300环给与（用户 2026-11-27 提供 48x48）",
        aliases=("灵宝天成印",), craftable=True),
    "lingbaozhutianyin": ItemDef(
        "lingbaozhutianyin", "灵宝诸天印", "item_lingbaozhutianyin.png", BAG,
        "300环给与（用户 2026-11-28 提供 47x47）", craftable=True),
    # 300环给与（用户 2026-11-27 提供 47x48）：按用户要求不裁剪，模板
    # 保留左上角数量角标「5」；数量每次可能不同，阈值参考摄妖香 0.69
    # （误命中由悬停物品名 OCR 复核兜底）。
    "zhenshoudan": ItemDef(
        "zhenshoudan", "珍兽丹", "item_zhenshoudan.png", BAG,
        "300环给与（用户 2026-11-27 提供 47x48，含数量角标；阈值 0.69）",
        threshold=0.69),
    # 300环「找到一个三级不可培养配饰（或三级佩饰盒子）」（用户 2026-11-28
    # 提供 47x46 图标，模板去掉左上数量角标裁 33x30，与同款数量无关）。
    # 「三级不可培养配饰」与「三级不可培养盒子/佩饰盒子」指同一件物品，
    # 任务文案的两种叫法走别名；给与面板悬停提示名是「三级佩饰盒子」
    # （2026-11-28 实机提示框 OCR 1.00 复核；用户初述「配饰盒子」实为
    # 「佩饰盒子」——规范名必须是提示名原文，给与命中与悬停关键词都用它，
    # 用错一字悬停读名即为空、报「未找到候选物品图标」）。
    "sanjipeishihezi": ItemDef(
        "sanjipeishihezi", "三级佩饰盒子", "item_sanjipeishihezi.png", BAG,
        "300环给与（用户 2026-11-28 提供 47x46，模板去数量角标 33x30）",
        aliases=("三级不可培养配饰", "三级不可培养佩饰",
                 "三级不可培养盒子", "三级配饰盒子","三级不可培养配子")),
    # 300环「找到属性13的炼妖石」类别型给与（用户 2026-11-27 提供模板）：
    # 炼妖石是类别，背包里任意一件属性13的炼妖石都可交；模板自带「+13」
    # 角标，命中即属性达标（实机悬停核对：辟邪珠 抗鬼火+13.0%、烈焰砂
    # 抗火+13.0%）。
    "pixiezhu": ItemDef(
        "pixiezhu", "辟邪珠", "item_pixiezhu.png", BAG,
        "300环给与-炼妖石（属性13，用户 2026-11-27 提供 48x46）",
        category="炼妖石", attr_value=13),
    "lieyansha": ItemDef(
        "lieyansha", "烈焰砂", "item_lieyansha.png", BAG,
        "300环给与-炼妖石（属性13，用户 2026-11-27 提供 47x47）",
        category="炼妖石", attr_value=13),
    # 300环「找到9-12属性的炼妖石」区间型给与（用户 2026-11-27 提供模板，
    # 实机悬停逐一核对均为炼妖石 +9.0%）。区间 9-12 内任一件都可交；
    # 属性值以图标角标+悬停属性行双重确认（背包里同款 +8 与 +9 图标
    # 模板互相命中，只靠模板/名称会递错属性）。
    "canghaizhu": ItemDef(
        "canghaizhu", "沧海珠", "item_canghaizhu.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 48x47，抗水+9.0%）",
        category="炼妖石", attr_value=9),
    "lantianyu": ItemDef(
        "lantianyu", "蓝田玉", "item_lantianyu.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 48x48，抗雷+9.0%）",
        category="炼妖石", attr_value=9),
    "lingxijiao": ItemDef(
        "lingxijiao", "灵犀角", "item_lingxijiao.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 47x47，抗混乱+9.0%）",
        category="炼妖石", attr_value=9),
    "xiaohanding": ItemDef(
        "xiaohanding", "霄汉鼎", "item_xiaohanding.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 47x46，抗风+9.0%）",
        category="炼妖石", attr_value=9),
    "yimengfu9": ItemDef(
        "yimengfu9", "忆梦符", "item_yimengfu9.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 47x47，抗遗忘+9.0%）",
        category="炼妖石", attr_value=9),
    "yunluozhang": ItemDef(
        "yunluozhang", "云罗帐", "item_yunluozhang.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供 48x48，抗封印+9.0%）",
        category="炼妖石", attr_value=9),
    "wudipao": ItemDef(
        "wudipao", "武帝袍", "item_wudipao.png", BAG,
        "300环给与-炼妖石（属性9，用户 2026-11-27 提供；实机 可乐可口 "
        "背包帧取 48x48 模板，角标+9）",
        category="炼妖石", attr_value=9),
    # 300环「找到1级或2级宝宝装备」等级类别型给与（用户 2026-11-27 提供
    # 庄子/粗布衣/木筝三个模板；实机 23:45 悬停提示框复核三件均
    # 「等级：1级」，任务要求 1 级或 2 级，任一候选可交）。attr_value
    # 存装备等级供 find_by_category_range 区间过滤；给与前由
    # give_flow.pick_and_give_level 读提示框「等级：N级」行复核——同类
    # 不同等级可能共用图标，只凭图标/名称会把不达标等级递出去。
    "zhuangzi": ItemDef(
        "zhuangzi", "庄子", "item_zhuangzi.png", BAG,
        "300环给与-宝宝装备（1级，用户 2026-11-27 提供 46x47）",
        category="宝宝装备", attr_value=1),
    "cubuyi": ItemDef(
        "cubuyi", "粗布衣", "item_cubuyi.png", BAG,
        "300环给与-宝宝装备（1级，用户 2026-11-27 提供 47x47）",
        category="宝宝装备", attr_value=1),
    "muzheng": ItemDef(
        "muzheng", "木筝", "item_muzheng.png", BAG,
        "300环给与-宝宝装备（1级，用户 2026-11-27 提供 48x47）",
        category="宝宝装备", attr_value=1),
    "zhuweiling": ItemDef(
        "zhuweiling", "助威令", "zhongzu_zhuweiling.png", BAG,
        "种族比武返程长安"),
    "xingmengshi": ItemDef(
        "xingmengshi", "星梦石", "item_xingmengshi.png", BAG,
        "用户 2026-11-27 提供（45x43）"),
    # ---- 背包-任务储物格 ----
    "huanfengfu": ItemDef(
        "huanfengfu", "唤风符", "mingwang_huanfengfu.png", TASK_BOX,
        "名望五庄封印"),
    "lingligao": ItemDef(
        "lingligao", "灵力镐", "mingwang_lingligao.png", TASK_BOX,
        "名望蛟魔探秘/使用灵力镐"),
    "chuanyinshi": ItemDef(
        "chuanyinshi", "传音石", "mingwang_chuanyinshi.png", TASK_BOX,
        "名望魔王重聚"),
    "toudanhulu": ItemDef(
        "toudanhulu", "偷丹葫芦", "mingwang_toudanhulu.png", TASK_BOX,
        "名望天宫奇遇"),
    "xianxingjing": ItemDef(
        "xianxingjing", "显形镜", "wubei_xianxingjing.png", TASK_BOX,
        "五倍宝象迷情（手动寻路右键使用）"),
    "wabaochutou": ItemDef(
        "wabaochutou", "挖宝锄头", "item_wabaochutou.png", TASK_BOX,
        "300环挖宝类（到达藏宝处后任务储物格右键使用）"),
    # 天下无贼战斗型（用户口径「大型鞭炮」，游戏内日志「大型爆竹」）：
    # 开战赠送 10 个，放普通背包；道具栏右键后左键点怪（2026-11-27 实机）。
    # 图标模板只裁鞭炮主体（不含左上角数量），数量变化不影响匹配。
    "daxingbaozhu": ItemDef(
        "daxingbaozhu", "大型爆竹", "item_daxingbianpao.png", BAG,
        "天下无贼战斗型（右键使用后左键点怪；2026-11-27 实机）"),
}


def item(key):
    """按 key 取物品登记；未知 key 抛 KeyError（调用方尽早暴露笔误）。"""
    try:
        return ITEMS[key]
    except KeyError:
        raise KeyError(f"未登记的物品 key: {key!r}（请在 device/items.py 登记）")


def icon(key):
    """物品图标模板文件名。"""
    return item(key).icon


# 图标模板文件名 -> 专用匹配阈值（只登记非零项；模板带数量角标/渲染
# 分数偏低的物品）。匹配热路径按图标名查表，不做线性遍历。
_ICON_THRESHOLDS = {e.icon: e.threshold for e in ITEMS.values()
                    if e.threshold > 0}


def threshold_for_icon(icon_name):
    """按图标模板文件名取物品专用匹配阈值（未登记/未设置返回 0.0）。

    供只拿得到模板文件名的匹配链路使用（bag.find_item_in_bag、
    give_flow 给与面板匹配）：返回 >0 时调用方用它替代默认阈值。
    """
    return _ICON_THRESHOLDS.get(icon_name, 0.0)


def name(key):
    """物品游戏内名称。"""
    return item(key).name


def storage(key):
    """物品所在储物位置（BAG/TASK_BOX/ANY）。"""
    return item(key).storage


def find_by_name(text):
    """在文本中查找已登记物品名称/别名，返回 ItemDef 或 None。

    用于任务文案「快去找到…并将其交给…」的物品名解析：按登记名称做
    子串匹配（名称尽量取完整词，如「猕猴桃」「天书残卷」），多个命中
    时取命中的名称/别名最长的（更具体）。aliases 覆盖 OCR 变体
    （如「天外飞石」被识别成「天外石」，2026-11-27 实机）。
    """
    text = text or ""
    hits = []
    for entry in ITEMS.values():
        names = [entry.name] + [a for a in entry.aliases if a]
        matched = [n for n in names if n and n in text]
        if matched:
            hits.append((max(len(n) for n in matched), entry))
    if not hits:
        return None
    return max(hits, key=lambda h: h[0])[1]


def find_by_category(category, attr_value=None):
    """按类别取候选物品（attr_value 非 None 时要求属性值一致）。

    300环类别型给与（用户 2026-11-27 实机「找到属性13的炼妖石」）：
    任务文案只写类别，玩家背包里任意一件属性达标的该类物品都可交；
    调用方按返回顺序逐个用图标模板在给与面板里找，命中任一即可。
    """
    if not category:
        return []
    hits = [entry for entry in ITEMS.values() if entry.category == category]
    if attr_value is not None:
        hits = [e for e in hits if e.attr_value == int(attr_value)]
    return hits


def find_by_category_range(category, low, high):
    """按类别取属性值落在 [low, high] 区间的候选物品。

    300环区间型给与（用户 2026-11-27 实机「找到9-12属性的炼妖石」）：
    区间内任意一件同类物品都可交，候选模板按登记顺序逐个尝试。
    等级类别型给与（「找到1级或2级宝宝装备」）同样走本函数：宝宝装备的
    attr_value 存装备等级，区间即等级范围。
    """
    if not category:
        return []
    low, high = int(low), int(high)
    return [entry for entry in ITEMS.values()
            if entry.category == category and low <= entry.attr_value <= high]
