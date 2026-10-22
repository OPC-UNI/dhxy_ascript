"""地图相关操作：打开/关闭世界地图与小地图、判断与识别当前地图位置。

功能：
- read_map_position：OCR 地图位置区域，识别当前地图 (地名, x, y)
- read_position_with_recovery：识别当前位置，失败按 Alt+C 恢复后重试
- is_in_map：判断当前是否在指定地图（地名模糊匹配）
- is_at_position：判断位置是否在指定地图坐标附近（±tolerance）
- open_world_map / close_world_map：世界地图开关
- open_mini_map：打开当前地图的小地图（点击地图位置区域）
- open_mini_map_city：打开世界地图并点击指定城市，打开该城市的小地图
- close_mini_map / close_maps：关闭小地图/世界地图（小地图左键模板位置、
  世界地图右键模板位置；世界地图带 1s 确认等待，复检仍打开才点击——
  防关闭动画瞬态误检右键取消寻路）
- find_template：模板查找（返回窗口相对坐标）

坐标约定：全部使用窗口相对坐标，由调用方通过 human_click 转屏幕坐标。
"""
import re
import time
from pathlib import Path

import cv2
import numpy as np
from rapidocr import RapidOCR  # 新版引擎（rapidocr 3.x，PP-OCRv5/v6 模型）
from rapidocr.utils.typings import OCRVersion

from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages

from .app_utils import human_click, log
from . import app_utils  # 高性能模式共享设施（固定位置缓存点击）

# 地图位置文字区域（窗口相对坐标，左上角）
MAP_POS_REGION = (45, 60, 222, 90)
# 世界地图按钮模板（实测匹配 0.969）
WORLD_MAP_ICON = "item_map_shijie.png"
# 世界地图上的城市图标（任务模块共用：点击城市打开该城市的小地图）
WORLD_MAP_LUOYANG_ICON = "shijie_luoyang.png"  # 洛阳城（实测匹配 0.991）
WORLD_MAP_CHANGAN_ICON = "shijie_changan.png"         # 长安城（五环任务实测）
# 世界地图打开标志模板（实测匹配 0.95，右键其位置可关闭）
# 小地图面板匹配区域（右上区）：面板可拖动但 58 帧实测均在
# (900-1100, 150-300)；锄遗聚宝小游戏等面板含相似图标，会误匹配到
# 屏幕中部（实测 (559,395)/(606,613) 连点不关），区域外匹配忽略
MINI_MAP_REGION = (800, 0, 1378, 400)
WORLD_MAP_OPEN_ICON = "map_shijie_open.png"
# 小地图打开标志模板（实测匹配 0.95；左键其位置可关闭）
MINI_MAP_OPEN_ICON = "map_xiaoditu_open.png"
# 地图画布回退点：右键地图画布可关闭地图（实测有效）
MAP_CANVAS_POS = (700, 380)

# 地图位置文案格式：地名［坐标x,坐标y］（前后常混入小图标误识别的噪声字符）
_MAP_POS_RE = re.compile(r"([一-鿿]{2,})\s*[\[［]\s*(\d+)\s*[,，]\s*(\d+)\s*[\]］]")

_engine = None
_region_engine = None

# OCR 引擎版本设置（用户 2026-10-11）：rapidocr 3.x params 以点路径
# 覆盖配置。Det/Rec 默认已是最新 PP-OCRv6 small（新包默认值），
# Cls 按设置传 PP-OCRv5。日志级别压到 error：战斗期对话框区域无文字时
# 引擎打「text detection result is empty」WARNING，属设计内噪声；
# RapidOCR 初始化会用配置值重置自身日志级别（rapidocr/main.py __init__），
# 必须经 params 传入而非外部 setLevel（会被覆盖）。
# 模型目录指向 res/models（打包随包分发，2026-10-12）：文件已存在则
# 跳过联网下载（打包环境曾缺 rapidocr 依赖致启动崩溃），目录缺失时
# rapidocr 会自动创建并从 modelscope 下载兜底。
_OCR_MODEL_DIR = R.res("models")
_OCR_PARAMS = {"Cls.ocr_version": OCRVersion.PPOCRV5,
               "Global.model_root_dir": _OCR_MODEL_DIR,
               "Global.log_level": "error",
               # 推理线程数限制（2026-10-22 实测校准）：onnxruntime 默认
               # intra=-1 用满全部 16 线程，双区域 OCR 一次烧 15.7 CPU 秒
               # 且 wall 反而更慢（1473ms，线程争抢）；intra=2/inter=1
               # 实测 wall 436ms（与最优相当）而 CPU 仅 0.92s/次（-17x）
               "EngineConfig.onnxruntime.intra_op_num_threads": 2,
               "EngineConfig.onnxruntime.inter_op_num_threads": 1}
# 区域快配置引擎参数（对应旧版 det_limit_type='max'/det_limit_side_len=736/
# use_angle_cls=False）：区域按原尺寸检测（默认 'min' 会把小区域放大到
# 736 最小边再检测，实测双区域 1344ms→944ms）；游戏 UI 全是水平文本，
# 关闭角度分类。Det/Rec 模型路径保持默认（null = 自动下载的官方模型）。
# 推理线程数限制同全帧引擎（轮询热路径：follow 每 0.5s 一次双区域 OCR，
# 默认全核并行致任务进程常驻 5~6 核 CPU，见 docs/performance.md）
_REGION_OCR_PARAMS = {
    "Cls.ocr_version": OCRVersion.PPOCRV5,
    "Det.limit_type": "max",
    "Det.limit_side_len": 736,
    "Global.model_root_dir": _OCR_MODEL_DIR,
    "Global.use_cls": False,
    "Global.log_level": "error",
    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
}


def _engine_items(res):
    """新版引擎输出（RapidOCROutput）转为 [(txt, rect, score), ...]。

    boxes 为 (N,4,2) 数组，rect 取 [left, top, right, bottom]。
    """
    items = []
    if res is not None and res.txts is not None:
        for box, txt, score in zip(res.boxes, res.txts, res.scores):
            bl, bt = float(box[0][0]), float(box[0][1])
            br, bb = float(box[2][0]), float(box[2][1])
            items.append((str(txt), [int(bl), int(bt), int(br), int(bb)],
                          float(score)))
    return items


def ocr_regions(img, regions):
    """OCR 识别多个区域并合并结果，返回 (items, img_np)。

    regions 为空时识别全图。多区域场景（如任务追踪 + 对话框）比单一
    外接矩形省去区域之间的无关像素，进一步提速；rect 与 img_np 保持
    全图坐标（区域外置黑），下游绿色文字判定与点击坐标无需改动。

    items：按纵坐标排序的 [(txt, rect, score)] 列表；
    img_np：与全图等大的 numpy 数组（区域外为黑），供调用方做像素级判断。
    """
    global _engine, _region_engine
    full_np = np.asarray(img)
    if not regions:
        if _engine is None:
            _engine = RapidOCR(params=_OCR_PARAMS)
        img_np = full_np
        items = _engine_items(_engine(img_np))
        items.sort(key=lambda x: x[1][1])
        return items, img_np
    # 区域 OCR 用快配置引擎：limit_type='max'/736 下区域按原尺寸检测
    # （默认 'min'/736 会把小区域放大到 736 最小边再检测，实测双区域
    # 1344ms→944ms、面板区 -29%、对话框区 -52%）。快配置下部分行置信度
    # 降低（如问答选项「金乌」0.66→0.58），下游 score 门槛已统一降至 0.55。
    # use_cls=False：游戏 UI 全是水平文本，角度分类只判 0/180 度翻转，
    # 关掉实测双区域 277→138ms（-50%），识别框数不变
    if _region_engine is None:
        _region_engine = RapidOCR(params=_REGION_OCR_PARAMS)
    img_np = np.zeros_like(full_np)
    items = []
    for l, t, r, b in regions:
        crop = full_np[t:b, l:r]
        img_np[t:b, l:r] = crop
        for txt, rect, score in _engine_items(_region_engine(crop)):
            items.append((txt,
                          [l + rect[0], t + rect[1], l + rect[2], t + rect[3]],
                          score))
    items.sort(key=lambda x: x[1][1])
    return items, img_np


def ocr_region(img, region=None):
    """OCR 识别单个区域（region 为 None 时全图），见 ocr_regions。"""
    return ocr_regions(img, [] if region is None else [region])


def ocr_image(img):
    """OCR 识别全图，返回 (items, img_np)（见 ocr_regions，regions 为空）。"""
    return ocr_regions(img, [])


def warmup_ocr():
    """预热 OCR 引擎（首次调用需加载 det/cls/rec 模型，一次性约 1s；
    新版引擎首次会下载官方模型，网络耗时另计——建议首次运行前联网）。"""
    global _engine, _region_engine
    if _engine is None:
        _engine = RapidOCR(params=_OCR_PARAMS)
        _engine(np.zeros((32, 32, 3), dtype=np.uint8))
    if _region_engine is None:
        _region_engine = RapidOCR(params=_REGION_OCR_PARAMS)
        _region_engine(np.zeros((32, 32, 3), dtype=np.uint8))


def _clean_map_pos(text):
    """清洗地图位置文案，提取 地名[坐标] 主内容。

    匹配到「地名[数字,数字]」格式时直接返回干净结果；
    否则剔除纯标点/符号噪声后返回，仍无有效内容返回空串。
    """
    m = _MAP_POS_RE.search(text)
    if m:
        return f"{m.group(1)}[{m.group(2)},{m.group(3)}]"
    cleaned = re.sub(r"[^一-鿿0-9A-Za-z\[\]［］,，\s]", "", text)
    return cleaned.strip(" ,，[]［］")


def read_map_position(win, img=None):
    """OCR 地图位置区域（MAP_POS_REGION），返回 (地名, x, y)；失败返回 None。

    地名统一繁转简（世界地图用繁体，位置文案两种都可能出现）。
    用区域快配置引擎直接检测：原实现裁剪后走全精度引擎，det 'min' 会把
    30px 高的区域放大到 736 最小边再检测（177x30 → 4336x736，极慢），
    快引擎按原尺寸检测，位置文字约 18-20px 可稳定识别。
    img：调用方已截取的帧（PIL），传入则跳过截图（轮询复用帧）。
    """
    if img is None:
        img = win.capture(force_activate=False)
    if img is None:
        return None
    items, _ = ocr_region(img, MAP_POS_REGION)
    raw = " ".join(t[0] for t in items).strip()
    m = _MAP_POS_RE.search(_clean_map_pos(raw))
    if m:
        name = m.group(1).replace("陽", "阳")
        return name, int(m.group(2)), int(m.group(3))
    return None


def verify_map_position(win):
    """高级设置-获取当前坐标按钮：读取当前地图位置并打日志
    （只读，不按键不点击，失败重读一次）。返回 (是否成功, 描述)。"""
    pos = read_map_position(win)
    if pos is None:
        time.sleep(0.5)
        pos = read_map_position(win)
    if pos is None:
        log("[地图] 获取当前坐标: 位置识别失败", "WARN")
        return False, "位置识别失败（地图名与坐标未读取到）"
    log(f"[地图] 获取当前坐标: {pos[0]} {pos[1]},{pos[2]}", "SYS")
    return True, f"当前位置: {pos[0]} {pos[1]},{pos[2]}"


def read_position_with_recovery(win):
    """识别当前位置；失败时按 Alt+C 恢复位置显示后重试一次（常见于人物未落地）。"""
    pos = read_map_position(win)
    if pos is None:
        log("[地图] 位置识别失败，按 Alt+C 后重试", "WARN")
        Keyboard.key_hot("alt", "c")
        time.sleep(1.0)  # 等待位置显示恢复
        pos = read_map_position(win)
    return pos


def is_in_map(win, name):
    """判断当前是否在指定地图（地名精确匹配）。

    精确匹配避免误判相邻地图（如「长安」不能匹配「长安城东」）。
    """
    pos = read_position_with_recovery(win)
    return pos is not None and pos[0].strip() == name


def is_at_position(pos, name, x, y, tolerance=5):
    """判断位置是否在指定地图坐标附近（±tolerance 像素）。"""
    if pos is None:
        return False
    pname, px, py = pos
    return (pname == name
            and abs(px - x) <= tolerance
            and abs(py - y) <= tolerance)


def find_template(win, icon, threshold=0.8):
    """在游戏窗口内查找模板，返回首个匹配的 (x, y) 窗口相对坐标或 None。

    先截一帧（force_activate=False）再在帧上匹配：FindImages 的
    im_source=Window 路径内部调用 win.capture() 且默认 force_activate=True，
    每次匹配都激活游戏窗口抢前台焦点（耗时且干扰用户其他窗口），
    违反性能规则「轮询截图一律 force_activate=False」。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    return find_template_in(img, icon, threshold)


def find_template_in(img, icon, threshold=0.8):
    """在已捕获图像内查找模板，返回首个匹配的 (x, y) 图像相对坐标或 None。

    与 find_template 同模板同阈值，但不重新截窗（FindImages 对 Window
    源每次调用内部重新 win.capture，一帧多模板场景如战斗判定会重复截
    4 次窗；此函数复用调用方已抓帧，行为等价）。
    """
    results = FindImages.find_all_template(R.img(icon), im_source=img, threshold=threshold)
    return (results[0].x, results[0].y) if results else None


def best_match_in(img, icon):
    """在已捕获图像内查找模板的全局最高匹配，返回 (confidence, (x, y))。

    与 find_template_in 同源同匹配算法，maxcnt=1 快速模式取全局最高分。
    供双模板比分数场景使用（如队伍面板解散/离开按钮、第1/第2回合横幅
    ——相似模板互相弱匹配，单模板阈值判定会误判）。
    无匹配时返回 (0.0, (0, 0))。
    """
    results = FindImages.find_all_template(R.img(icon), im_source=img,
                                           threshold=0.0, max_res=1)
    if not results:
        return 0.0, (0, 0)
    r = results[0]
    return float(r.confidence), (int(r.center[0]), int(r.center[1]))


# ---- 快速模板接口（战斗等热路径专用；旧接口全部保留，兼容现有调用方） ----
# 同一帧上多模板匹配的重复开销：每次 FindImages 调用都对 PIL 帧做
# np.array+BGR 转换（实测 ~7ms）并重新解码模板（~0.3ms）。本接口：
# 1) 模板灰度缓存（mtime 失效）2) 帧一次灰度转换 3) 可选 ROI 限定
# （TM_CCOEFF_NORMED 分数在裁剪区内与全图严格一致，无需重标定阈值）。
_TEMPLATE_CACHE = {}  # icon -> (mtime_ns, 灰度模板数组)


def _load_template(icon):
    """加载模板灰度数组（磁盘解码一次后缓存，按 mtime 失效）。失败返回 None。"""
    path = Path(R.img(icon))
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _TEMPLATE_CACHE.get(icon)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    tpl = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        return None
    _TEMPLATE_CACHE[icon] = (mtime, tpl)
    return tpl


def match_frame_np(img_np, icon, threshold=0.8, region=None, fallback=True):
    """在 BGR numpy 帧上做模板匹配，返回首个 (x, y) 中心或 None。

    与 find_template_in 同算法同阈值语义（TM_CCOEFF_NORMED 灰度匹配）。
    region=(l,t,r,b) 限定搜索区域；区域内未命中且 fallback=True 时
    全帧兜底（布局漂移保险，慢路径只在未命中时触发）。
    """
    tpl = _load_template(icon)
    if tpl is None:
        return None
    search = img_np
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        search = img_np[max(0, t):min(img_np.shape[0], b),
                        max(0, l):min(img_np.shape[1], r)]
        ox, oy = max(0, l), max(0, t)
    res = cv2.matchTemplate(cv2.cvtColor(search, cv2.COLOR_BGR2GRAY), tpl,
                            cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val < threshold:
        if fallback and region is not None:
            return match_frame_np(img_np, icon, threshold=threshold,
                                  region=None, fallback=False)
        return None
    h, w = tpl.shape[:2]
    return ox + max_loc[0] + w // 2, oy + max_loc[1] + h // 2


def best_match_np(img_np, icon, region=None):
    """在 BGR numpy 帧上取模板全局最高分，返回 (confidence, (x, y))。

    与 best_match_in 同语义（双模板比分数场景用）；无匹配返回 (0.0, (0, 0))。
    """
    tpl = _load_template(icon)
    if tpl is None:
        return 0.0, (0, 0)
    search = img_np
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        search = img_np[max(0, t):min(img_np.shape[0], b),
                        max(0, l):min(img_np.shape[1], r)]
        ox, oy = max(0, l), max(0, t)
    res = cv2.matchTemplate(cv2.cvtColor(search, cv2.COLOR_BGR2GRAY), tpl,
                            cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    h, w = tpl.shape[:2]
    return float(max_val), (ox + max_loc[0] + w // 2, oy + max_loc[1] + h // 2)


def match_frame_all(img_np, icon, threshold=0.8, region=None):
    """在 BGR numpy 帧上取模板全部匹配中心，返回 [(score, x, y), ...]
    按分数降序。多实例场景用（如白龙马任务按钮同屏多处相似图案，
    逐一尝试点击）。同一实例邻域（模板尺寸范围）内取最高分点，
    抑制近重复坐标。无匹配返回空列表。
    """
    tpl = _load_template(icon)
    if tpl is None:
        return []
    search = img_np
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        search = img_np[max(0, t):min(img_np.shape[0], b),
                        max(0, l):min(img_np.shape[1], r)]
        ox, oy = max(0, l), max(0, t)
    res = cv2.matchTemplate(cv2.cvtColor(search, cv2.COLOR_BGR2GRAY), tpl,
                            cv2.TM_CCOEFF_NORMED)
    h, w = tpl.shape[:2]
    ys, xs = np.where(res >= threshold)
    if len(xs) == 0:
        return []
    pts = sorted(((float(res[y, x]), x, y) for x, y in zip(xs, ys)),
                 reverse=True)
    out = []
    for score, x, y in pts:
        cx, cy = ox + x + w // 2, oy + y + h // 2
        if any(abs(cx - px) < w // 2 and abs(cy - py) < h // 2
               for _, px, py in out):
            continue  # 同一实例的近重复命中
        out.append((score, cx, cy))
    return out


def match_frame_multi(img_np, icons, threshold=0.8, regions=None):
    """同一 BGR 帧上批量匹配多模板（帧只做一次灰度转换）。

    icons: 模板名列表；regions: 与 icons 一一对应的 region 或 None。
    返回 {icon: (x, y) 或 None}。未命中不回退全帧（调用方自行兜底）。
    """
    gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
    result = {}
    for i, icon in enumerate(icons):
        tpl = _load_template(icon)
        if tpl is None:
            result[icon] = None
            continue
        search = gray
        ox = oy = 0
        region = regions[i] if regions else None
        if region is not None:
            l, t, r, b = region
            search = gray[max(0, t):min(gray.shape[0], b),
                          max(0, l):min(gray.shape[1], r)]
            ox, oy = max(0, l), max(0, t)
        res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        if max_val < threshold:
            result[icon] = None
            continue
        h, w = tpl.shape[:2]
        result[icon] = (ox + max_loc[0] + w // 2, oy + max_loc[1] + h // 2)
    return result


def find_templates(win, icon, threshold=0.8):
    """在游戏窗口内查找模板的全部匹配，返回 [(x, y), ...] 窗口相对坐标列表。

    截一帧（force_activate=False）后在帧上匹配，不激活游戏窗口
    （原因同 find_template）。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return []
    return [(r.x, r.y)
            for r in FindImages.find_all_template(R.img(icon), im_source=img, threshold=threshold)]


def is_world_map_open(win):
    """判断世界地图是否打开。"""
    return find_template(win, WORLD_MAP_OPEN_ICON) is not None


def is_mini_map_open(win):
    """判断小地图是否打开。"""
    return find_template(win, MINI_MAP_OPEN_ICON) is not None


def open_world_map(win):
    """点击世界地图按钮打开世界地图。返回 (是否成功, 描述)。

    高性能：按钮坐标缓存后直点；未打开回退识别点击（并刷新缓存）。
    """
    cached = app_utils.high_perf_click(win, "world_map_btn",
                                       "世界地图按钮", prefix="[地图] ")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.3)
        if is_world_map_open(win):
            return True, "世界地图已打开（高性能缓存）" if cached else "世界地图已打开"
    if cached:
        log("[地图][高性能] 缓存点击未打开世界地图，回退识别", "WARN")
    pt = find_template(win, WORLD_MAP_ICON)
    if pt is None:
        return False, "未找到世界地图按钮"
    app_utils.fixed_pos_set("world_map_btn", win, pt[0], pt[1])
    log(f"[地图] 打开世界地图: 点击按钮 @ ({pt[0]},{pt[1]})", "INFO")
    human_click(win, pt[0], pt[1])
    # 轮询等待打开（实测约 0.5s 即开；替代固定 1.5s 等待，打开即返回）
    deadline = time.time() + 2.0
    while time.time() < deadline:
        time.sleep(0.3)
        if is_world_map_open(win):
            return True, "世界地图已打开"
    return False, "点击后世界地图未打开"


def open_mini_map(win):
    """点击地图位置区域打开当前地图的小地图。返回 (是否成功, 描述)。"""
    cx = (MAP_POS_REGION[0] + MAP_POS_REGION[2]) // 2
    cy = (MAP_POS_REGION[1] + MAP_POS_REGION[3]) // 2
    log(f"[地图] 打开小地图: 点击位置区域 @ ({cx},{cy})", "INFO")
    human_click(win, cx, cy)
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.5)
    if not is_mini_map_open(win):
        return False, "点击后小地图未打开"
    return True, "小地图已打开"


def open_mini_map_city(win, city_icon, city_label):
    """打开世界地图并点击指定城市，打开该城市的小地图。

    世界地图点击城市后自动关闭（实测）。返回 (是否成功, 描述)。
    高性能：城市图标坐标（世界地图布局固定）缓存后直点；未打开
    回退识别点击（并刷新缓存）。
    """
    ok, info = open_world_map(win)
    if not ok:
        return False, info
    key = f"city_{city_icon}"
    cached = app_utils.high_perf_click(win, key, f"{city_label}图标", prefix="[地图] ")
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.3)
    if is_mini_map_open(win):
        return True, f"{city_label}小地图已打开"
    if cached:
        log(f"[地图][高性能] 缓存点击未打开{city_label}小地图，回退识别", "WARN")
    pt = find_template(win, city_icon)
    if pt is None:
        return False, f"世界地图中未找到{city_label}"
    app_utils.fixed_pos_set(key, win, pt[0], pt[1])
    log(f"[地图] 打开{city_label}小地图: 点击城市图标 @ ({pt[0]},{pt[1]})", "INFO")
    human_click(win, pt[0], pt[1])
    # 轮询等待小地图打开（替代固定 1.5s 等待，打开即返回）
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.3)
    if not is_mini_map_open(win):
        return False, f"点击{city_label}后小地图未打开"
    return True, f"{city_label}小地图已打开"


def close_map_if_open(win, icon, label, fallbacks=(), primary_button="secondary",
                      close_retries=3, confirm_wait=0.0, match_region=None,
                      quiet=False):
    """检测地图（模板）是否打开，打开则点击模板位置关闭。

    双帧确认后才点击（防瞬时误检）；confirm_wait > 0 时再等复检一次
    （世界地图用：点击城市/小地图目标后世界地图有时自动关闭、有时
    仍打开——动画期的瞬态匹配会在复检时消失，避免误点击落到游戏
    世界取消寻路；复检仍在才点击）。
    match_region：匹配位置约束 (l,t,r,b)。小地图面板可拖动但实测
    限于右上区；锄遗聚宝小游戏等面板含相似图标会误匹配到屏幕中部
    （实测 (559,395)/(606,613) 连点不关），区域外匹配直接忽略。
    主点击（小地图左键、世界地图右键）后轮询复检，未关闭则重试主
    点击（关闭动画期间不误判），多次主点击仍未关闭才走画布兜底。
    注意：画布兜底右键会点到游戏世界、取消寻路（实测阻塞寻路），
    调用方按需传入（世界地图全屏面板右键落在面板上安全；小地图
    不传兜底——没关不影响移动，下轮流程自愈）。
    quiet：周期性兜底检查用——「未打开不点击」这类无操作路径不再
    打日志（锄遗聚宝 5s 兜底检查实测每轮刷 2 条 INFO），实际执行
    关闭动作的日志保留。
    返回 (是否成功, 描述)。全部点击与复检结果打日志（排查多点一次）。
    """
    def _find():
        if match_region is None:
            return find_template(win, icon)
        img = win.capture(force_activate=False)
        if img is None:
            return None
        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        return match_frame_np(frame, icon, region=match_region, fallback=False)

    pt = _find()
    if pt is None or _find() is None:
        if not quiet:
            log(f"[地图] 关闭{label}: 双帧确认未打开，不点击", "INFO")
        return False, f"{label}未打开，无需关闭"
    if confirm_wait > 0:
        time.sleep(confirm_wait)
        pt2 = _find()
        if pt2 is None:
            if not quiet:
                log(f"[地图] 关闭{label}: 确认等待后已自动关闭（动画瞬态），不点击", "INFO")
            return True, f"{label}已自动关闭，无需点击"
        pt = pt2
    btn = "左" if primary_button == "primary" else "右"
    for attempt in range(close_retries):
        log(f"[地图] 关闭{label}: 第 {attempt + 1}/{close_retries} 次"
            f"{btn}键点击模板位置 @ ({pt[0]},{pt[1]})", "INFO")
        human_click(win, pt[0], pt[1], button=primary_button)
        deadline = time.time() + 1.0  # 轮询复检替代固定等待（关闭动画约 1s）
        while time.time() < deadline:
            time.sleep(0.3)
            if _find() is None:
                log(f"[地图] 关闭{label}: 复检已关闭（第 {attempt + 1} 次点击生效）", "INFO")
                return True, f"{label}已在模板位置{btn}键关闭"
        pt = _find()  # 仍在：位置可能漂移，重取后重试主点击
        if pt is None:
            log(f"[地图] 关闭{label}: 复检时已关闭", "INFO")
            return True, f"{label}已关闭"
        log(f"[地图] 关闭{label}: 点击后仍检测到打开（新位置 ({pt[0]},{pt[1]})），重试", "WARN")
    for x, y in list(fallbacks):
        log(f"[地图] 关闭{label}: 主点击重试 {close_retries} 次未关闭，"
            f"右键画布兜底 @ ({x},{y})", "WARN")
        human_click(win, x, y, button="secondary")
        deadline = time.time() + 1.0
        while time.time() < deadline:
            time.sleep(0.3)
            if _find() is None:
                log(f"[地图] 关闭{label}: 画布兜底已关闭", "INFO")
                return True, f"{label}已右键关闭（画布回退点({x},{y})）"
    return False, f"{label}关闭失败（已重试 {close_retries} 次主点击）"


def close_world_map(win, quiet=False):
    """关闭世界地图（若打开）：模板位置右键，画布兜底。返回 (是否成功, 描述)。

    带 1s 确认等待：点击城市/小地图目标后世界地图有时自动关闭、有时
    仍打开（用户实测）——等待后复检仍在才点击，动画期的瞬态匹配
    （误检点击会落到游戏世界取消寻路）在复检时消失、不点击。
    """
    return close_map_if_open(win, WORLD_MAP_OPEN_ICON, "世界地图",
                             fallbacks=(MAP_CANVAS_POS,), primary_button="secondary",
                             confirm_wait=1.0, quiet=quiet)


def close_mini_map(win, quiet=False):
    """关闭小地图（若打开）：模板位置左键，无画布兜底（右键会点到
    游戏世界取消寻路）。匹配限定右上区（MINI_MAP_REGION）——小地图
    面板可拖动但限于右上，锄遗聚宝小游戏等面板的相似图标误匹配在
    屏幕中部时直接忽略。返回 (是否成功, 描述)。"""
    return close_map_if_open(win, MINI_MAP_OPEN_ICON, "小地图",
                             primary_button="primary",
                             match_region=MINI_MAP_REGION, quiet=quiet)


def close_maps(win, quiet=False):
    """关闭打开的地图（小地图在上层，先关）。世界地图关闭带 1s 确认
    等待（见 close_world_map），只在复检仍打开时点击。"""
    close_mini_map(win, quiet=quiet)
    close_world_map(win, quiet=quiet)
