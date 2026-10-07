"""背包相关操作：打开背包、物品检测与点击。"""
import time

from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages
from .app_utils import log, human_click

# 背包按钮模板（res/img/bag.png，34x34，实测匹配度 0.999）
BAG_ICON = "bag.png"
# 背包面板标志模板（res/img/bag_box.png，34x170，实测匹配度 1.0）。
# 背包面板可拖动：模板必须只含面板自身稳定内容（左缘边框+内部上段），
# 不得混入屏幕背景——旧版 34x265 下段含面板外背景，面板从屏幕左缘
# 拖到中部后匹配分 0.946→0.698，is_bag_open 误判导致开包流程失败。
# 新模板已交叉验证：左缘/中部两位置 + 储物格开/关两状态均 ≥0.9。
BAG_BOX_ICON = "bag_box.png"
# 摄妖香物品图标模板（res/img/sheyaoxiang.png，49x48，实测匹配度 1.0）
SHEYAOXIANG_ICON = "sheyaoxiang.png"
# 背包储物格切换按钮模板（res/img/box_one_close.png，26x27）
BOX_ONE_CLOSE_ICON = "box_one_close.png"
# 储物格切换的纵向偏移：以 box_one_close 为基准向下 35px 递增，
# 共 4 个储物格（再往下第 5 个位置已经是任务栏）
SLOT_OFFSETS = [0, 35, 70, 105]


def _match_template(win, icon, threshold=0.8):
    """截一帧（不激活窗口）并在帧上匹配模板，返回首个匹配结果或 None。

    FindImages 的 im_source=Window 路径内部调用 win.capture() 且默认
    force_activate=True——每次匹配都先激活游戏窗口抢前台焦点（激活是
    耗时操作，且会干扰用户当前正在使用的窗口）。统一改为先截帧再在
    帧上匹配，与性能规则「轮询截图一律 force_activate=False」一致。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    results = FindImages.find_all_template(R.img(icon), im_source=img, threshold=threshold)
    return results[0] if results else None


def _match_all_template(win, icon, threshold=0.8):
    """截一帧（不激活窗口）并匹配模板的全部结果，返回列表（同 _match_template 约束）。"""
    img = win.capture(force_activate=False)
    if img is None:
        return []
    return FindImages.find_all_template(R.img(icon), im_source=img, threshold=threshold)


def find_and_click_bag(win):
    """在游戏窗口中找到背包按钮并点击打开。

    返回 (是否成功, 描述)。
    """
    r = _match_template(win, BAG_ICON)
    if r is None:
        return False, "未找到背包按钮"
    human_click(win, r.x, r.y)
    return True, f"匹配度 {r.confidence:.2f}"


def _find_bag_box(win):
    """查找背包面板标志（bag_box），返回首个匹配结果或 None。"""
    return _match_template(win, BAG_BOX_ICON)


def find_bag_box(win):
    """查找背包面板标志（bag_box）的中心坐标，未找到返回 None。

    供调用方以面板标志为锚点定位面板内元素（面板可拖动，内部元素
    相对偏移恒定，见修罗回程的搜索条带）。
    """
    r = _find_bag_box(win)
    return (r.x, r.y) if r else None


# 开/关背包的轮询参数：点击后轮询面板标志出现/消失（替代固定 1.5s
# 等待——面板动画约 0.4s 完成，成功路径平均省 ~1s；1.5s 为超时上限）
_BAG_TOGGLE_POLL = 0.4
_BAG_TOGGLE_TIMEOUT = 1.5


def is_bag_open(win):
    """检测背包是否已打开（是否找到 bag_box 模板）。"""
    return _find_bag_box(win) is not None


def find_item_in_bag(win, icon_name):
    """在游戏窗口中查找指定物品图标（背包打开状态下）。

    返回匹配结果列表，未找到返回空列表。
    """
    return _match_all_template(win, icon_name)


def find_box_one_close(win):
    """查找背包储物格切换按钮 box_one_close，返回首个匹配或 None。"""
    return _match_template(win, BOX_ONE_CLOSE_ICON)


def find_item_with_slots(win, icon_name, label):
    """在背包中查找指定物品，未找到时切换储物格再找。

    流程：
    1. 直接检测（命中即返回）
    2. 未命中：低阈值诊断匹配度并记日志（区分"没检测到"与"匹配度不足"），
       等待 0.5s 复检一次——背包刚打开时物品图标可能尚未渲染完
       （实测摄妖香在储物格 4 上首检漏检，遍历一圈回来才 0.84 命中）
    3. 仍未命中：点击 box_one_close（储物格切换），以 35px 为步长向下
       点击，最多 4 个储物格；每次操作前等待 100ms
    返回 (MatchResult 或 None, 过程描述)。
    """
    items = find_item_in_bag(win, icon_name)
    if items:
        return items[0], "直接检测到"

    # 诊断：低阈值再查，看当前储物格上是否只是匹配度不足（日志可见，便于排查）
    near = _match_all_template(win, icon_name, threshold=0.6)
    best = max((r.confidence for r in near), default=0.0)
    log(f"[背包] {label}直接检测未命中（低阈值最优匹配度 {best:.2f}），"
        f"等待 0.5s 复检", "INFO")
    time.sleep(0.5)
    items = find_item_in_bag(win, icon_name)
    if items:
        return items[0], "复检检测到"

    base = find_box_one_close(win)
    if base is None:
        return None, "未找到 box_one_close 储物格按钮"

    for i, offset in enumerate(SLOT_OFFSETS):
        time.sleep(0.1)  # 每次操作前等待（识别物品的耗时不计入等待）
        y = base.y + offset
        log(f"[背包] {label}未检测到，点击储物格 {i + 1}: ({base.x}, {y})"
            f"（box_one_close 下方 {offset}px）", "INFO")
        human_click(win, base.x, y)
        time.sleep(0.1)  # 等待储物格界面切换
        items = find_item_in_bag(win, icon_name)
        if items:
            return items[0], f"切换储物格 {i + 1} 后检测到"
    return None, f"切换 {len(SLOT_OFFSETS)} 个储物格后仍未检测到{label}"


def find_sheyaoxiang_with_slots(win):
    """在背包中查找摄妖香（含储物格切换搜索），返回 (MatchResult 或 None, 描述)。"""
    return find_item_with_slots(win, SHEYAOXIANG_ICON, "摄妖香")


def open_bag(win):
    """点击背包并验证打开。

    点击后轮询 bag_box 模板出现：出现=打开成功；1.5s 内未出现=重试一次
    （仅一次）。注意背包按钮是开关切换：若此前已是打开状态，第一次
    点击会关闭，检测失败后重试点击即可恢复打开，流程自愈。
    返回 (是否成功, 描述)。
    """
    for attempt in (1, 2):
        r = _match_template(win, BAG_ICON)
        if r is None:
            return False, "未找到背包按钮"
        human_click(win, r.x, r.y)
        deadline = time.time() + _BAG_TOGGLE_TIMEOUT
        while time.time() < deadline:
            if is_bag_open(win):
                return True, f"第{attempt}次点击成功（匹配度 {r.confidence:.2f}）"
            time.sleep(_BAG_TOGGLE_POLL)
        log(f"[背包] 第{attempt}次点击后未检测到背包面板，重试一次...", "WARN")
    return False, "两次尝试后仍未检测到背包面板"


def close_bag(win):
    """右键点击背包面板关闭背包。

    识别到 bag_box 后记录坐标，关闭时移动到该位置右键点击（游戏面板
    支持右键关闭）。点击后轮询 bag_box 消失：消失=关闭成功；1.5s 内
    仍存在=重试一次。返回 (是否成功, 描述)。
    """
    for attempt in (1, 2):
        box = _find_bag_box(win)
        if box is None:
            if not is_bag_open(win):
                return True, "背包未打开"
            return False, "未找到背包面板"
        human_click(win, box.x, box.y, button="secondary")
        deadline = time.time() + _BAG_TOGGLE_TIMEOUT
        while time.time() < deadline:
            if not is_bag_open(win):
                return True, f"第{attempt}次右键成功关闭（匹配度 {box.confidence:.2f}）"
            time.sleep(_BAG_TOGGLE_POLL)
        log(f"[背包] 第{attempt}次右键后背包仍打开，重试一次...", "WARN")
    return False, "两次尝试后背包仍未关闭"


def use_sheyaoxiang(win):
    """完整执行一次使用摄妖香流程（面向对象视角：背包使用摄妖香）。

    打开背包 → 查找摄妖香（未找到时切换储物格）→ 右键使用 → 关闭背包。
    注意：已右键使用但关闭背包失败时仍视为成功——重试会造成重复使用。
    返回 (是否成功, 描述)。
    """
    # 1. 打开背包
    ok, info = open_bag(win)
    if not ok:
        return False, f"打开背包失败: {info}"
    log(f"[背包] 已打开（{info}）", "SYS")

    # 2. 查找摄妖香（含储物格切换搜索）
    item, desc = find_sheyaoxiang_with_slots(win)
    if item is None:
        log(f"[摄妖香] {desc}", "WARN")
        ok2, info2 = close_bag(win)  # 未找到也关闭背包，保持界面干净
        if not ok2:
            log(f"[背包] {info2}", "WARN")
        return False, desc

    # 3. 右键使用
    log(f"[摄妖香] 背包中存在（{desc}，匹配度 {item.confidence:.2f}）", "SYS")
    human_click(win, item.x, item.y, button="secondary")
    log(f"[摄妖香] 已右键点击 ({item.x}, {item.y})", "SYS")
    time.sleep(0.2)  # 物品使用后短暂等待

    # 4. 关闭背包
    ok2, info2 = close_bag(win)
    if ok2:
        log(f"[背包] 已关闭（{info2}）", "SYS")
        return True, "使用完成"
    log(f"[背包] 关闭失败: {info2}", "WARN")
    return True, "已使用，但关闭背包失败（不再重试，避免重复使用）"
