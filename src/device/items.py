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


ITEMS = {
    # ---- 普通背包 ----
    "mihoutao": ItemDef(
        "mihoutao", "猕猴桃", "item_mihoutao.png", BAG,
        "用户 2026-11-26 提供（46x38）"),
    "sheyaoxiang": ItemDef(
        "sheyaoxiang", "摄妖香", "sheyaoxiang.png", BAG,
        "自动使用摄妖香（bag.use_sheyaoxiang）"),
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
    "zhuweiling": ItemDef(
        "zhuweiling", "助威令", "zhongzu_zhuweiling.png", BAG,
        "种族比武返程长安"),
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


def name(key):
    """物品游戏内名称。"""
    return item(key).name


def storage(key):
    """物品所在储物位置（BAG/TASK_BOX/ANY）。"""
    return item(key).storage


def find_by_name(text):
    """在文本中查找已登记物品名称，返回 ItemDef 或 None。

    用于任务文案「快去找到…并将其交给…」的物品名解析：按登记名称做
    子串匹配（名称尽量取完整词，如「猕猴桃」「天书残卷」），多个命中
    时取名称最长的（更具体）。
    """
    hits = [entry for entry in ITEMS.values()
            if entry.name and entry.name in (text or "")]
    if not hits:
        return None
    return max(hits, key=lambda e: len(e.name))
