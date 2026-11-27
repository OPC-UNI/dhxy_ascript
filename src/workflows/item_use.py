"""任务储物格物品使用：开背包 → 任务储物格 → 右键物品 → 关背包。

300环挖宝类（用户 2026-11-26）：到达藏宝处后，任务道具「挖宝锄头」在
背包-任务储物格，需右键使用触发挖掘。名望的唤风符/灵力镐等也是同一
流程（各任务自己的实现见 mingwang._use_task_item）；本模块提供通用
版本供任务调用，避免复制粘贴。

放在 workflows（跨任务业务流）而不是任务模块：能力下沉，任务只负责
「何时用」。
"""
import time

from ..core import app_utils
from ..core.app_utils import human_click, log
from ..device import bag

ITEM_USE_AFTER_CLICK = 1.0   # 右键物品后等待生效（传送/战斗/挖掘动画）
ITEM_USE_FIND_THRESHOLD = 0.8


def use_task_box_item(win, icon, label, prefix="", threshold=None):
    """在背包-任务储物格右键使用物品。返回 (是否成功, 描述)。

    整段持背包锁（app_utils._BAG_LOCK），与摄妖香等其它背包流程互斥。
    物品未找到/任务储物格打不开时关包后返回失败，调用方可安全重试。
    """
    if threshold is None:
        threshold = ITEM_USE_FIND_THRESHOLD
    tag = f"[{prefix}] " if prefix else "[物品] "
    with app_utils._BAG_LOCK:
        ok, info = bag.open_task_box(win, prefix=prefix)
        if not ok:
            return False, f"打开任务储物格失败：{info}"
        pts = bag.find_item_in_bag(win, icon, threshold=threshold)
        if not pts:
            bag.close_bag(win)  # best effort 收尾，不阻断失败返回
            return False, f"任务储物格中未找到{label}（{icon}）"
        item = pts[0]
        human_click(win, item.x, item.y, button="secondary")
        log(f"{tag}已右键使用{label} @({item.x},{item.y})"
            f"（匹配度 {getattr(item, 'confidence', 0):.2f}）", "SYS")
        time.sleep(ITEM_USE_AFTER_CLICK)
        bag.close_bag(win)
        return True, f"已使用{label}"
