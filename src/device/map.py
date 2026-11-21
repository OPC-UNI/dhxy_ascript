"""地图相关操作：打开/关闭世界地图与小地图、判断与识别当前地图位置。

功能：
- read_map_position：OCR 地图位置区域，识别当前地图 (地名, x, y)
- read_position_with_recovery：识别当前位置，失败按 Alt+C 恢复后重试
- is_in_map：判断当前是否在指定地图（地名模糊匹配）
- is_at_position：判断位置是否在指定地图坐标附近（±tolerance）
- open_world_map / close_world_map：世界地图开关
- open_mini_map：打开当前地图的小地图（高性能 Alt+1/Alt+2 快捷键，
  非高性能点击地图位置区域/世界地图按钮）
- open_mini_map_by_name / click_world_map_label：跨地图打开场景小地图
  （地图名 → WORLD_MAP_LABELS 标签，任务/工作流统一入口，不再传图标）
- close_mini_map / close_maps：关闭小地图/世界地图（小地图左键模板位置、
  世界地图右键模板位置；两者均带 1s 确认等待，复检仍打开才点击——
  防关闭/传送动画瞬态误检点击落到游戏世界取消寻路）
- find_template：模板查找（返回窗口相对坐标）

坐标约定：全部使用窗口相对坐标，由调用方通过 human_click 转屏幕坐标。
"""
import difflib
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from rapidocr import RapidOCR  # 新版引擎（rapidocr 3.x，PP-OCRv5/v6 模型）
from rapidocr.utils.typings import OCRVersion

from ascript.windows.hardware.keyboard import Keyboard
from ascript.windows.system import R
from ascript.windows.screen.find_images import FindImages

from ..core.app_utils import human_click, log, norm_text
from ..core import app_utils  # 高性能模式共享设施（固定位置缓存点击）

# 右下角功能按钮托盘搜索条带（组队/座骑/背包按钮固定位置；
# 2026-11-07 用户标定 900,737-1378,831，含余量）：按钮位置固定，
# 全屏匹配多余（全帧 ~137ms vs 条带 ~20ms，且场景其他位置可能出现
# 近似图案误匹配）；条带内未命中即按钮不在该位置=场景不对，
# 不做全帧兜底
TRAY_BTN_REGION = (900, 737, 1378, 831)

# 地图位置文字区域（窗口相对坐标，左上角）
MAP_POS_REGION = (45, 60, 222, 90)
# 世界地图按钮模板（实测匹配 0.969）
WORLD_MAP_ICON = "item_map_shijie.png"
# ---- 世界地图标签全表（地图名 → 标签模板，唯一来源）----
# 所有「打开世界地图点目标地图」的流程只传地图名，标签模板一律从本表
# 解析（2026-11-18 用户规则：不得在任务模块再定义单图图标常量，
# 如旧 WORLD_MAP_LUOYANG_ICON / WORLD_MAP_CHANGAN_ICON 已删除）。
# 2026-11-15 实机从世界地图截图标定（按 OCR 行框裁剪、留 2px 余量），
# 覆盖世界地图全部 55 个可见地名。全部模板实机自校验：目标世界地图帧
# ≥0.98 唯一命中，非地图帧 ≤0.46（阈值 0.8 有充分余量）。
WORLD_MAP_LABELS = {
    "大唐边境": "shijie_datangbianjing.png",
    "长安城": "shijie_changan.png",
    "洛阳城": "shijie_luoyang.png",
    "宝象国": "shijie_baoxiangguo.png",
    "火云戈壁": "shijie_huoyungebi.png",
    "灵兽村": "shijie_lingshoucun.png",
    "蟠桃园": "shijie_pantaoyuan.png",
    "天宫": "shijie_tiangong.png",
    "广寒宫": "shijie_guanghangong.png",
    "隐林涧": "shijie_yinlinjian.png",
    "修罗古城": "shijie_xiuluogucheng.png",
    "龙窟": "shijie_longku.png",
    "凤巢": "shijie_fengchao.png",
    "瑶池": "shijie_yaochi.png",
    "御马监": "shijie_yumajian.png",
    "女儿村": "shijie_nvercun.png",
    "北俱芦洲": "shijie_beijuluzhou.png",
    "傲来国": "shijie_aolaiguo.png",
    "花果山": "shijie_huaguoshan.png",
    "方寸后山": "shijie_fangcunhoushan.png",
    "流洲": "shijie_liuzhou.png",
    "水帘洞": "shijie_shuiliandong.png",
    "方寸山": "shijie_fangcunshan.png",
    "龙宫": "shijie_longgong.png",
    "蓬莱": "shijie_penlai.png",
    "长寿村": "shijie_changshoucun.png",
    "化生寺": "shijie_huashengsi.png",
    "海底迷宫": "shijie_haidimigong.png",
    "瀛洲": "shijie_yingzhou.png",
    "白骨山": "shijie_baigushan.png",
    "大雁塔": "shijie_dayanta.png",
    "皇宫": "shijie_huanggong.png",
    "长寿村外": "shijie_changshoucunwai.png",
    "平顶山": "shijie_pingdingshan.png",
    "东海渔村": "shijie_donghaiyucun.png",
    "五庄观": "shijie_wuzhuangguan.png",
    "万寿山": "shijie_wanshoushan.png",
    "方壶": "shijie_fanghu.png",
    "珊瑚海岛": "shijie_shanhuhaidao.png",
    "四圣庄": "shijie_sishengzhuang.png",
    "斧头帮": "shijie_futoubang.png",
    "蓬莱海渊": "shijie_penlaihaiyuan.png",
    "长安东": "shijie_changandong.png",
    "女儿国": "shijie_nverguo.png",
    "地下鬼岛": "shijie_dixiaguidao.png",
    "五指山": "shijie_wuzhishan.png",
    "大唐境内": "shijie_datangjingnei.png",
    "普陀山": "shijie_putuoshan.png",
    "狮驼岭": "shijie_shituoling.png",
    "地狱迷宫": "shijie_diyumigong.png",
    "轮回司": "shijie_lunhuisi.png",
    "若木灵境": "shijie_ruomulingjing.png",
    "黑水河": "shijie_heishuihe.png",
    "地府": "shijie_difu.png",
    "兰若寺": "shijie_lanruosi.png",
}
# 世界地图标签匹配阈值（实测目标帧 ≥0.98 唯一命中、非地图帧 ≤0.46）
WORLD_MAP_LABEL_THRESHOLD = 0.8
# 地图名 OCR 繁体/形近归一（世界地图字体下 OCR 常输出繁体：龍宫/長安城/
# 瑤池/蓬菜 等，2026-11-15 实测；提示文案同名，解析前先归一）
WORLD_MAP_OCR_ALIASES = str.maketrans({
    "龍": "龙", "長": "长", "陽": "阳", "宮": "宫", "瑤": "瑶",
    "靈": "灵", "獸": "兽", "鳳": "凤", "東": "东", "華": "华",
    "觀": "观", "萬": "万", "壽": "寿", "寶": "宝", "羅": "罗",
    "漢": "汉", "陰": "阴", "樓": "楼", "蘭": "兰", "獄": "狱",
    "輪": "轮", "迴": "回", "菜": "莱", "園": "园", "頂": "顶",
    "鄉": "乡", "鎮": "镇", "灣": "湾",
})
# 位置地名 → 世界地图标签名的别名（两者不完全一致的场景）：
# 「长安」是任务的**位置识别地名**（五环/种族比武用 CHANGAN 做位置比对），
# 世界地图标签名为「长安城」——不开别名表时只能靠相似度 0.8 与「长安东」
# 打平（快照顺序决定胜负，不可依赖）。
# 「长安城东」：HUD 位置地名（实测 2026-11-20 地图导航在长安东内），
# 世界地图标签名为「长安东」——相似度与「长安城」打平（0.857），
# 不开别名会被 difflib 归到「长安城」，导致到达判定地名不匹配。
WORLD_MAP_NAME_ALIASES = {"长安": "长安城", "长安城东": "长安东"}
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
# 2x 放大帧 OCR 引擎（开打兜底/巫医选项放大共用，2026-10-27）：
# 快引擎 limit 'max'/736 会把 1160px 宽的 2x 对话框裁剪缩小到 736
# （抵消放大、小字重新漏检）；全帧引擎 'min'/736 则把 2x 裁剪再放大
# 1.1x 白烧 det。max/1600 覆盖 2x 裁剪原始尺寸——det 分辨率与旧全帧
# 路径（1293x736）同档、CPU 更低。仅限「放大后最长边 ≤1600 且放大
# 分辨率即检出分辨率」的场景（对话框区 2x 裁剪）；小块 2x 裁剪依赖
# 'min'/736 的额外放大检出小字（如修罗任务块 2x 重识别），继续走全帧
# 引擎，不得替换。
_ZOOM_OCR_PARAMS = dict(_REGION_OCR_PARAMS)
_ZOOM_OCR_PARAMS["Det.limit_side_len"] = 1600
_zoom_engine = None

# 引擎创建锁（双重检查）：区域/2x 引擎在任务启动时预热，正常不会走到
# 懒创建；全帧引擎自 2026-11-20 起不再预热——它只服务 xiuluo 任务块
# 2x/队伍弹窗、mingwang 种族条带三条兜底路径（2026-11-13~20 日志实测
# 全帧 0 次成功触发），首次 ocr_image 时懒创建。任务线程与战斗线程
# 存在同时首次触发的可能，无锁会建出两份引擎、白占内存。
_engine_lock = threading.Lock()
_region_engine_lock = threading.Lock()
_zoom_engine_lock = threading.Lock()


def resolve_world_map_name(name):
    """地图名 → WORLD_MAP_LABELS 键（精确 → 别名 → 繁体归一 → 相似度）。

    地图名来自固定地点表，OCR 误读多为个别字（蓬菜/繁体 龍宫 等）：
    先做繁体/形近归一（WORLD_MAP_OCR_ALIASES）与位置地名别名
    （WORLD_MAP_NAME_ALIASES：长安→长安城、长安城东→长安东），再精确
    匹配，最后取相似度
    最高且 ≥0.6 的候选；短名（2 字）也按全名比对，不做部分包含匹配
    （长安东/长安城 相似度 0.667，取更高者）。未收录返回 None。
    """
    if not name:
        return None
    name = norm_text(name).translate(WORLD_MAP_OCR_ALIASES)
    if not name:
        return None
    name = WORLD_MAP_NAME_ALIASES.get(name, name)
    if name in WORLD_MAP_LABELS:
        return name
    best, best_ratio = None, 0.0
    for key in WORLD_MAP_LABELS:
        ratio = difflib.SequenceMatcher(None, name, key).ratio()
        if ratio > best_ratio:
            best, best_ratio = key, ratio
    return best if best_ratio >= 0.6 else None


def bgr_frame(img):
    """PIL（RGB）帧转 BGR（模板匹配用；OCR 路径继续用 PIL 帧）。"""
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


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
        if _engine is None:  # 全帧引擎：仅兜底路径，首次使用时懒创建
            with _engine_lock:
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
        with _region_engine_lock:
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


def ocr_zoom(img):
    """对 2x 放大帧做全图 OCR（快引擎、不缩小放大效果），返回 (items, img_np)。

    开打兜底/巫医选项放大的 2x 对话框裁剪用（max/1600 检出分辨率与
    旧全帧路径同档、CPU 更低）；调用方自行把 rect 映射回窗口坐标
    （与旧 ocr_image 用法一致）。
    """
    global _zoom_engine
    if _zoom_engine is None:
        with _zoom_engine_lock:
            if _zoom_engine is None:
                _zoom_engine = RapidOCR(params=_ZOOM_OCR_PARAMS)
    full_np = np.asarray(img)
    items = _engine_items(_zoom_engine(full_np))
    items.sort(key=lambda x: x[1][1])
    return items, full_np


def warmup_ocr():
    """预热 OCR 引擎（区域 + 2x 快引擎，一次性约 1s；本地模型已随包
    分发，缺失时新版引擎会联网下载，网络耗时另计）。

    全帧引擎不预热：它只服务 xiuluo 任务块 2x/队伍弹窗、mingwang 种族
    条带三条兜底路径（2026-11-13~20 日志实测全帧 0 次成功触发；名望
    12 轮判定全部由区域引擎完成、22 次直接走当日缓存），首次 ocr_image
    时按 _engine_lock 懒创建（约 0.4-0.8s，每进程一次）。区域引擎是
    所有轮询热路径，2x 引擎在修罗/客栈/五倍有实测使用，二者必须预热。
    """
    global _region_engine, _zoom_engine
    if _region_engine is None:
        with _region_engine_lock:
            if _region_engine is None:
                _region_engine = RapidOCR(params=_REGION_OCR_PARAMS)
                _region_engine(np.zeros((32, 32, 3), dtype=np.uint8))
    if _zoom_engine is None:
        with _zoom_engine_lock:
            if _zoom_engine is None:
                _zoom_engine = RapidOCR(params=_ZOOM_OCR_PARAMS)
                _zoom_engine(np.zeros((32, 32, 3), dtype=np.uint8))


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
    """识别当前位置；失败时按 Alt+C 恢复位置显示后重试一次（常见于人物未落地）。

    静默操作：Alt+C 走消息投递（无需前台，2026-11-03 实测有效）；
    物理路径游戏不在前台时跳过（键盘事件只进前台窗口），返回 None——
    调用方按「位置不可读」处理。
    """
    pos = read_map_position(win)
    if pos is None:
        if app_utils.silent_mode_on():
            from . import silent_click  # 延迟导入：避免模块级循环
            silent_click.send_silent_hotkey(win, ("alt",), "c")
            log("[地图] 位置识别失败，静默 Alt+C 后重试", "WARN")
        elif app_utils.keyboard_ok(win):
            log("[地图] 位置识别失败，按 Alt+C 后重试", "WARN")
            Keyboard.key_hot("alt", "c")
        else:
            log("[地图] 位置识别失败，游戏不在前台：跳过 Alt+C 恢复", "WARN")
            return None
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


def near_and_still(win, name, x, y, tolerance=5, wait=0.8):
    """在指定地图坐标附近且站定（间隔 wait 两次位置读数相同）。

    任务点位「已在附近则跳过寻路」的安全判定：附近但仍在走动返回
    False，调用方照常寻路让角色走到目标点停稳——走动中提前点进店/
    领取按钮/扫描 NPC 的时机不对。位置读不到按未站定处理。
    返回 (是否附近且站定, 最新位置)；未在附近时只读一次位置（~100ms），
    在附近时才多等 wait + 一次读数。
    """
    pos = read_map_position(win)
    if not is_at_position(pos, name, x, y, tolerance):
        return False, pos
    time.sleep(wait)
    pos2 = read_map_position(win)
    return bool(pos2 is not None and pos2 == pos
                and is_at_position(pos2, name, x, y, tolerance)), pos2


def find_template(win, icon, threshold=0.8, region=None):
    """在游戏窗口内查找模板，返回首个匹配的 (x, y) 窗口相对坐标或 None。

    先截一帧（force_activate=False）再在帧上匹配：FindImages 的
    im_source=Window 路径内部调用 win.capture() 且默认 force_activate=True，
    每次匹配都激活游戏窗口抢前台焦点（耗时且干扰用户其他窗口），
    违反性能规则「轮询截图一律 force_activate=False」。
    region=(l,t,r,b) 同 find_template_in 的搜索条带。
    """
    img = win.capture(force_activate=False)
    if img is None:
        return None
    return find_template_in(img, icon, threshold, region=region)


def find_template_in(img, icon, threshold=0.8, region=None):
    """在已捕获图像内查找模板，返回首个匹配的 (x, y) 图像相对坐标或 None。

    与 find_template 同模板同阈值，但不重新截窗（FindImages 对 Window
    源每次调用内部重新 win.capture，一帧多模板场景如战斗判定会重复截
    4 次窗；此函数复用调用方已抓帧，行为等价）。
    region=(l,t,r,b)：限定搜索条带（裁剪后匹配、返回坐标仍为全图坐标），
    条带内未命中返回 None，不做全帧兜底——条带是标定资产，目标不在
    条带内即场景不对（性能规则：全帧 ~137ms vs 条带 ~20ms）。裁剪内
    匹配分数与全图严格一致（TM_CCOEFF_NORMED），无需重标定阈值。
    """
    source = img
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        l, t = max(0, int(l)), max(0, int(t))
        r, b = min(img.size[0], int(r)), min(img.size[1], int(b))
        source = img.crop((l, t, r, b))
        ox, oy = l, t
    results = FindImages.find_all_template(R.img(icon), im_source=source, threshold=threshold)
    if not results:
        return None
    return results[0].x + ox, results[0].y + oy


def find_all_template_in(img, icon, threshold=0.8, region=None):
    """在已捕获图像内查找模板的全部命中，返回 [(x, y), ...]。

    find_template_in 的多目标版本（任务标记等需要逐个处理多个目标的
    场景）；坐标语义与 find_template_in 一致（命中中心），region 语义
    相同（裁剪后匹配、返回坐标仍为全图坐标）。
    """
    source = img
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        l, t = max(0, int(l)), max(0, int(t))
        r, b = min(img.size[0], int(r)), min(img.size[1], int(b))
        source = img.crop((l, t, r, b))
        ox, oy = l, t
    results = FindImages.find_all_template(R.img(icon), im_source=source,
                                           threshold=threshold)
    return [(r.x + ox, r.y + oy) for r in results]


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


def template_size(icon):
    """模板像素尺寸 (宽, 高)；模板缺失/解码失败返回 None。

    供按模板匹配框（rect=中心±半宽高）推导搜索条带/OCR 区域使用
    （如名望种族判定：标题牌右侧 120px 条带）。
    """
    tpl = _load_template(icon)
    if tpl is None:
        return None
    return int(tpl.shape[1]), int(tpl.shape[0])


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


def best_match_multi_np(img_np, icons, region=None):
    """同一 BGR 帧上批量取各模板的全局最高分，返回 {icon: (score, (x, y))}。

    与 best_match_np 同语义（TM_CCOEFF_NORMED 灰度、返回匹配中心），但帧
    只做一次灰度转换、模板走缓存——供多模板同帧比分数/记录最高匹配度的
    热路径使用（如客栈拍毒虫：15 张毒虫模板逐个匹配小候选框）。region 为
    所有模板共用的搜索条带（未命中不回退全帧，条带由调用方标定）；模板
    缺失/大于搜索区按 0 分处理。
    """
    gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
    ox = oy = 0
    if region is not None:
        l, t, r, b = region
        gray = gray[max(0, t):min(gray.shape[0], b),
                    max(0, l):min(gray.shape[1], r)]
        ox, oy = max(0, l), max(0, t)
    result = {}
    for icon in icons:
        tpl = _load_template(icon)
        if tpl is None or tpl.shape[0] > gray.shape[0] \
                or tpl.shape[1] > gray.shape[1]:
            result[icon] = (0.0, (0, 0))
            continue
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        h, w = tpl.shape[:2]
        result[icon] = (float(max_val),
                        (ox + max_loc[0] + w // 2, oy + max_loc[1] + h // 2))
    return result


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
    """判断小地图是否打开。

    匹配限定右上区（MINI_MAP_REGION）：面板可拖动但实测限于右上；
    锄遗聚宝等小游戏面板含相似图标，全帧匹配会误配到屏幕中部
    （实测 (559,395)/(606,613)，见 MINI_MAP_REGION 注释），且开图
    轮询热路径条带 ~20ms vs 全帧 ~137ms。
    """
    return find_template(win, MINI_MAP_OPEN_ICON,
                         region=MINI_MAP_REGION) is not None


# ---- 地图打开失败诊断帧（2026-11-16 用户要求）----
# 打开失败时的现场状态无法从日志还原：2026-11-16 10:18 修理链路——世界
# 地图在场、洛阳城标签回退重识别位置与缓存一致，但点击城市图标两次
# 都没进详情图（间隔 3s），日志只有坐标，无法判断是动画竞态/弹窗遮挡/
# 客户端瞬时无响应。失败时把窗口帧保存到 logs/diag_map_*.png 供事后
# 定位。限频 FAIL_FRAME_DUMP_INTERVAL 秒一张：反复失败的调用方（如
# 2026-11-13 五倍小地图连败约每 8s 一次）不会刷爆磁盘。
FAIL_FRAME_DUMP_INTERVAL = 60.0
_FAIL_DUMP_DIR = app_utils._LOG_DIR  # 工程 logs/（测试可替换为临时目录）
_last_fail_dump_at = 0.0


def _dump_failure_frame(win, tag):
    """把当前窗口帧保存到 logs/ 供失败诊断（限频、best effort）。

    tag：文件名标签（ascii，如 world_open / city_open），便于区分失败点。
    保存失败只记日志，不影响调用方流程。
    """
    global _last_fail_dump_at
    now = time.time()
    if now - _last_fail_dump_at < FAIL_FRAME_DUMP_INTERVAL:
        return
    _last_fail_dump_at = now
    try:
        img = win.capture(force_activate=False)
        if img is None:
            return
        _FAIL_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        path = _FAIL_DUMP_DIR / (
            f"diag_map_{time.strftime('%m%d-%H%M%S')}_{tag}.png")
        img.save(str(path))
        log(f"[地图] 打开失败现场帧已保存: {path}", "WARN")
    except Exception as e:  # 诊断路径失败不影响主流程
        log(f"[地图] 失败现场帧保存失败: {e}", "WARN")


def open_world_map(win):
    """打开世界地图。返回 (是否成功, 描述)。

    高性能（2026-11-18 用户要求）：Alt+2 快捷键打开（位置漂移免疫，
    静默模式可后台投递）；快捷键未发出/未生效回退缓存坐标点击 →
    识别点击。非高性能维持识别模板点击方案。
    已打开时直接返回——按钮/快捷键是切换式，地图开着再触发会把它
    关掉（2026-11-16 小地图快捷领取实机：世界地图残留时
    open_mini_map_by_name 先点按钮反而关闭世界地图，导致城市标签找不到）。
    """
    if is_world_map_open(win):
        return True, "世界地图已打开"
    if app_utils.high_perf_on():
        sent, _ = app_utils.press_alt_hotkey(
            win, "2", "世界地图", prefix="[地图] ")
        if sent:
            # 轮询等待打开（实测约 0.5s；打开即返回）
            deadline = time.time() + 2.0
            while time.time() < deadline:
                time.sleep(0.3)
                if is_world_map_open(win):
                    return True, "世界地图已打开（Alt+2 快捷键）"
            log("[地图][高性能] Alt+2 未打开世界地图，回退点击路径", "WARN")
    cached = app_utils.high_perf_click(win, "world_map_btn",
                                       "世界地图按钮", prefix="[地图] ")
    if cached:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            time.sleep(0.3)
            if is_world_map_open(win):
                return True, "世界地图已打开（高性能缓存）"
        log("[地图][高性能] 缓存点击未打开世界地图，回退识别", "WARN")
    # 未点击（开关关闭/无缓存）不等待：此前无论是否点击都空等 2s，
    # 非高性能模式每次开图白耗 2s（2026-11-18 修正）
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
    _dump_failure_frame(win, "world_open")
    return False, "点击后世界地图未打开"


def open_mini_map(win):
    """打开当前地图的小地图。返回 (是否成功, 描述)。

    高性能（2026-11-18 用户要求）：Alt+1 快捷键打开；已打开直接返回
    （快捷键是切换式，开着再按会关闭）。快捷键未发出/未生效回退点击
    地图位置区域（原方案）。非高性能维持点击位置区域方案。
    """
    if app_utils.high_perf_on():
        if is_mini_map_open(win):
            return True, "小地图已打开"
        sent, _ = app_utils.press_alt_hotkey(
            win, "1", "小地图", prefix="[地图] ")
        if sent:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                time.sleep(0.3)
                if is_mini_map_open(win):
                    return True, "小地图已打开（Alt+1 快捷键）"
            log("[地图][高性能] Alt+1 未打开小地图，回退点击位置区域", "WARN")
    cx = (MAP_POS_REGION[0] + MAP_POS_REGION[2]) // 2
    cy = (MAP_POS_REGION[1] + MAP_POS_REGION[3]) // 2
    log(f"[地图] 打开小地图: 点击位置区域 @ ({cx},{cy})", "INFO")
    human_click(win, cx, cy)
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.5)
    if not is_mini_map_open(win):
        _dump_failure_frame(win, "minimap_open")
        return False, "点击后小地图未打开"
    return True, "小地图已打开"


def world_map_label_icon(map_name):
    """地图名 → 世界地图标签模板（WORLD_MAP_LABELS 唯一来源）。

    名称先经 resolve_world_map_name 归一（别名/繁体/相似度），未收录
    返回 None。任务与工作流只传地图名，不再维护单图图标常量。
    """
    key = resolve_world_map_name(map_name)
    return WORLD_MAP_LABELS[key] if key else None


def click_world_map_label(win, map_name, prefix="[地图] ", cache_key=None):
    """世界地图（需已打开）定位并点击 map_name 标签，返回 (是否成功, 描述)。

    地图标签定位/点击的唯一实现：模板来自 WORLD_MAP_LABELS，全帧最高分
    与 WORLD_MAP_LABEL_THRESHOLD 校验（失败日志带分数便于诊断），首次
    未命中短等 0.3s 复检一次（标签渲染可能滞后世界地图横幅一帧）。
    cache_key：非 None 时把成功定位的坐标写入固定位置缓存（高性能城市
    图标直点复用）。
    """
    key = resolve_world_map_name(map_name)
    icon = WORLD_MAP_LABELS.get(key) if key else None
    if icon is None:
        return False, f"地图「{map_name}」未收录世界地图标签模板"
    score, pt = 0.0, (0, 0)
    for attempt in (1, 2):
        img = win.capture(force_activate=False)
        if img is None:
            return False, "截图失败（窗口可能已最小化）"
        score, pt = best_match_np(bgr_frame(img), icon)
        if score >= WORLD_MAP_LABEL_THRESHOLD:
            break
        if attempt == 1:
            time.sleep(0.3)
    if score < WORLD_MAP_LABEL_THRESHOLD:
        return False, (f"世界地图未找到「{key}」标签"
                       f"（最佳匹配 {score:.3f}）")
    if cache_key is not None:
        app_utils.fixed_pos_set(cache_key, win, pt[0], pt[1])
    human_click(win, pt[0], pt[1])
    log(f"{prefix}世界地图已点「{key}」标签 @({pt[0]},{pt[1]})", "INFO")
    return True, f"已点击「{key}」标签"


def open_mini_map_by_name(win, map_name, prefix="[地图] "):
    """打开 map_name 的场景小地图（跨图时经世界地图点标签）。返回 (是否成功, 描述)。

    统一入口（2026-11-18 用户规则：地图标签图标统一走 WORLD_MAP_LABELS，
    调用方只传地图名；旧 open_mini_map_city(icon, label) 的双参数与
    单图图标常量已删除）。流程：打开世界地图（已打开则复用）→ 点击地图
    标签 → 轮询小地图面板出现。世界地图点击地图后自动关闭（实测）。
    高性能：标签坐标缓存后直点（键沿用 city_<图标>，历史缓存兼容），
    未打开回退识别点击（并刷新缓存）。
    """
    key = resolve_world_map_name(map_name)
    icon = WORLD_MAP_LABELS.get(key) if key else None
    if icon is None:
        return False, f"地图「{map_name}」未收录世界地图标签模板"
    ok, info = open_world_map(win)
    if not ok:
        return False, info
    cache_key = f"city_{icon}"
    cached = app_utils.high_perf_click(win, cache_key, f"{key}标签",
                                       prefix=prefix)
    if cached:
        deadline = time.time() + 3
        while not is_mini_map_open(win) and time.time() < deadline:
            time.sleep(0.3)
        if is_mini_map_open(win):
            return True, f"{key}小地图已打开"
        log(f"{prefix}[高性能] 缓存点击未打开{key}小地图，回退识别", "WARN")
    ok, info = click_world_map_label(win, key, prefix=prefix,
                                     cache_key=cache_key)
    if not ok:
        _dump_failure_frame(win, "city_label_missing")
        return False, info
    # 轮询等待小地图打开（替代固定 1.5s 等待，打开即返回）
    deadline = time.time() + 3
    while not is_mini_map_open(win) and time.time() < deadline:
        time.sleep(0.3)
    if not is_mini_map_open(win):
        # 失败现场落盘（2026-11-16）：世界地图此时大概率仍开着，帧里能
        # 直接看出是弹窗遮挡、动画中、还是点击落在别处
        _dump_failure_frame(win, "city_open")
        return False, f"点击{key}后小地图未打开"
    return True, f"{key}小地图已打开"


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
    屏幕中部时直接忽略。返回 (是否成功, 描述)。

    带 1s 确认等待（2026-11-15 对齐世界地图）：跨地图传送时小地图
    随传送自动关闭，关闭动画期间仍能双帧匹配到渐隐帧——确认等待后
    复检消失即不点击。否则点击落到游戏世界取消寻路、把人物带走
    （玄枢轶闻跨地图领取实测：点了关闭的 5 轮全失败、没点的 2 轮全成功）。
    """
    return close_map_if_open(win, MINI_MAP_OPEN_ICON, "小地图",
                             primary_button="primary",
                             match_region=MINI_MAP_REGION,
                             confirm_wait=1.0, quiet=quiet)


def close_maps(win, quiet=False):
    """关闭打开的地图（小地图在上层，先关）。小地图与世界地图关闭均带
    1s 确认等待（见 close_mini_map / close_world_map），只在复检仍打开
    时点击——关闭动画瞬态匹配不点击（点击会落到游戏世界取消寻路）。

    返回是否有地图处于打开/关闭动画中（调用方据此决定是否等关闭动画；
    两个地图本来就没开时返回 False）。"""
    mini, _ = close_mini_map(win, quiet=quiet)
    world, _ = close_world_map(win, quiet=quiet)
    return bool(mini or world)


# ---- 场景小地图快捷导航（2026-11-16 用户要求下沉，任务共用）----
# 场景小地图窗口右上搜索栏的圆形关闭按钮是窗口的稳定锚点（模板
# map_xiaoditu_open.png，实测 0.97~1.00）；窗口可拖动且不同场景窗口
# 宽度不同（大唐边境/天宫实测），窗口内固定位置（底部「任务」勾选框、
# 黄色任务 NPC 名）一律按锚点 + 偏移换算，不写绝对坐标。
# 典型用法（名望领取入口）：已在该地图直接开小地图（不要再走世界地图），
# 勾选底部「任务」筛选使黄色任务 NPC 名显示，再点固定位置黄字——游戏
# 自动寻路/传送并在到达后自动打开 NPC 对话框。点击点为**绝对坐标**，
# 由调用方按场景标定后传入（见 tasks/mingwang.py CLAIM_POINTS 的
# map_filter_pos / map_label_pos——小地图窗口位置固定，不做锚点相对换算，
# 2026-11-16 用户规则）。
MINI_MAP_TASK_CHECKED_MIN_WHITE = 4  # 勾选框近白像素 ≥4 = 已勾选（实测 13 vs 0）
MINI_MAP_TASK_RADIUS_X = 11         # 勾选框白像素检测半宽（实测框约 24x22）
MINI_MAP_TASK_RADIUS_Y = 10
MINI_MAP_FILTER_WAIT = 0.4          # 点击「任务」筛选后等待渲染（秒）
# 底部筛选勾选框状态模板（2026-11-16 实机标定，24x24）：
# 勾选模板只命中「任务」框（大唐 1.000 / 天宫 0.947），未勾选模板命中
# 其余四个框（0.889~0.930），两类互不命中——跨场景通用，无需按地图
# 标定勾选框偏移。合并命中按 x 排序取第 4 个即「任务」框。
MINI_MAP_TASK_ON_ICON = "map_task_filter_on.png"
MINI_MAP_TASK_OFF_ICON = "map_task_filter_off.png"
MINI_MAP_TASK_ICON_THRESHOLD = 0.85
# 筛选行搜索带（**绝对坐标**，2026-11-16 用户规则：小地图窗口位置固定，
# 即使被拖走下次打开也会恢复默认位置；关闭按钮所在搜索栏宽度随状态变化，
# 锚点不是稳定的内容参照——实机 19:04 锚点从 (1030,217) 漂到 (1011,229)，
# 相对带把勾选框上缘裁掉致状态恒为未知）。带宽为勾选框行（实测 y643-667）
# 上下各留余量：上缘距模板顶 13px、下缘 33px。
MINI_MAP_TASK_BAND = (120, 630, 1120, 700)


def minimap_task_filter_checked(img, cx, cy):
    """小地图底部「任务」勾选框是否已勾选（框内近白像素计数，零 OCR）。

    实测（2026-11-16 大唐边境/天宫实机帧）：勾选态白色对勾像素 13 个、
    未勾选 0 个；阈值 4 留渲染余量。cx/cy 为勾选框中心。
    """
    a = np.asarray(img)[:, :, :3]
    l = max(0, int(cx) - MINI_MAP_TASK_RADIUS_X)
    r = min(a.shape[1], int(cx) + MINI_MAP_TASK_RADIUS_X)
    t = max(0, int(cy) - MINI_MAP_TASK_RADIUS_Y)
    b = min(a.shape[0], int(cy) + MINI_MAP_TASK_RADIUS_Y)
    if r <= l or b <= t:
        return False
    sub = a[t:b, l:r]
    white = ((sub[:, :, 0] >= 200) & (sub[:, :, 1] >= 200)
             & (sub[:, :, 2] >= 200))
    return int(white.sum()) >= MINI_MAP_TASK_CHECKED_MIN_WHITE


def _minimap_task_filter_box(img):
    """定位小地图底部「任务」勾选框，返回 (是否勾选, cx, cy) 或 None。

    底部五个勾选框（全部/传送/普通/任务/商业）是同一控件：勾选模板只
    命中「任务」框，未勾选模板命中其余四个；合并两类命中按 x 排序取
    第 4 个即「任务」框。2026-11-16 实机标定：大唐边境 勾选 1.000 /
    未勾选 0.889~0.924，天宫 勾选 0.947；两类模板互不命中。
    返回模板中心（点击点）；地图未打开或筛选行未识别返回 None。
    """
    bgr = bgr_frame(img)
    # 绝对搜索带（小地图窗口位置固定，见 MINI_MAP_TASK_BAND 注释）：
    # 地图未打开时带内无勾选框行，命中不足 5 个自然返回 None
    hits = []
    for state, icon in ((True, MINI_MAP_TASK_ON_ICON),
                        (False, MINI_MAP_TASK_OFF_ICON)):
        for score, x, y in match_frame_all(
                bgr, icon, threshold=MINI_MAP_TASK_ICON_THRESHOLD,
                region=MINI_MAP_TASK_BAND):
            hits.append((state, float(score), int(x), int(y)))
    if len(hits) < 5:
        return None
    hits.sort(key=lambda h: h[2])
    merged = []
    for h in hits:
        if merged and abs(h[2] - merged[-1][2]) <= 6:
            # 同一勾选框被两类模板同时命中（轻微形变）：取分数高者
            if h[1] > merged[-1][1]:
                merged[-1] = h
        else:
            merged.append(h)
    if len(merged) < 5:
        return None
    state, _score, x, y = merged[3]
    return state, x, y


def minimap_task_filter_state(win, img=None):
    """小地图底部「任务」筛选勾选状态（模板匹配勾选框，跨场景通用）。

    返回 True（已勾选）/ False（未勾选）/ None（地图未打开或筛选行
    未识别）。详见 _minimap_task_filter_box。
    """
    if img is None:
        img = win.capture(force_activate=False)
        if img is None:
            return None
    box = _minimap_task_filter_box(img)
    return None if box is None else box[0]


def uncheck_minimap_task_filter(win, img=None):
    """确保小地图底部「任务」筛选**未勾选**（用户 2026-11-21 规则）。

    失败（截图/识别/点击异常）一律返回 (False, 描述)，绝不向上抛——
    调用方（开图寻路链路）不能因任务筛选检查而中断。
    """
    try:
        return _uncheck_minimap_task_filter(win, img)
    except Exception as e:  # noqa: BLE001 —— 辅助检查不得阻断寻路
        return False, f"任务筛选检查异常: {e}"


def _uncheck_minimap_task_filter(win, img=None):
    """uncheck_minimap_task_filter 的实现（异常由外层兜底）。

    仅名望快捷领取需要勾选任务筛选（点击黄色任务 NPC 名触发自动寻路/
    对话框，minimap_quick_navigate 自行勾选）；其余任务（修罗/五倍/
    五环/修理等）的小地图点击点都是普通地形点——勾选时黄色任务 NPC
    名/任务点可能与点击点重叠，触发自动寻路或误开对话框。开图后统一
    取消勾选；已取消/本就未勾选直接返回。
    返回 (是否已取消/本就未勾选, 描述)；地图未打开/筛选行未识别返回
    (False, 描述)，调用方可忽略（不阻断寻路）。
    """
    if img is None:
        img = win.capture(force_activate=False)
        if img is None:
            return False, "截图失败"
    box = _minimap_task_filter_box(img)
    if box is None:
        # 地图刚打开时筛选行可能尚未渲染：短轮询再试（最多 ~0.9s）
        for _ in range(3):
            time.sleep(0.3)
            img = win.capture(force_activate=False)
            if img is None:
                continue
            box = _minimap_task_filter_box(img)
            if box is not None:
                break
    if box is None:
        return False, "筛选行未识别（地图可能未打开）"
    state, cx, cy = box
    if not state:
        return True, "「任务」筛选未勾选"
    human_click(win, cx, cy)
    log(f"[地图] 小地图「任务」筛选已勾选，取消勾选 @({cx},{cy})", "SYS")
    time.sleep(MINI_MAP_FILTER_WAIT)
    img2 = win.capture(force_activate=False)
    if img2 is None:
        return False, "取消任务筛选后截图失败"
    box2 = _minimap_task_filter_box(img2)
    if box2 is not None and not box2[0]:
        return True, "「任务」筛选已取消勾选"
    return False, "点击后仍未取消勾选"


def ensure_minimap_task_filter(win, filter_pos):
    """确保小地图底部「任务」筛选已勾选。返回 (是否已勾选, 描述)。

    filter_pos=(x,y)：勾选框中心**绝对坐标**（按场景标定后传入；小地图
    窗口位置固定——即使被拖走，下次打开也会恢复默认位置，2026-11-16
    用户规则，不做窗口锚点相对换算）。未勾选则点击一次并复检——未勾选
    时地图不显示黄色任务 NPC 名，后续固定点点击不会触发自动寻路。
    """
    cx, cy = filter_pos
    img = win.capture(force_activate=False)
    if img is None:
        return False, "截图失败（窗口可能已最小化）"
    if minimap_task_filter_checked(img, cx, cy):
        return True, "小地图「任务」筛选已勾选"
    human_click(win, cx, cy)
    log(f"[地图] 小地图「任务」筛选未勾选，已点击勾选框 @({cx},{cy})", "SYS")
    time.sleep(MINI_MAP_FILTER_WAIT)
    img2 = win.capture(force_activate=False)
    if img2 is None:
        return False, "点击任务筛选后截图失败"
    if minimap_task_filter_checked(img2, cx, cy):
        return True, "小地图「任务」筛选已勾选"
    return False, "小地图「任务」筛选点击后仍未勾选"


def minimap_quick_navigate(win, map_name, filter_pos, label_pos):
    """场景小地图快捷导航（任务共用）。返回 (是否成功, 描述)。

    流程：打开目标地图的场景小地图 → 勾选底部「任务」筛选 → 点击黄色
    任务 NPC 名固定点（**绝对坐标，只点一次**——第一次点击已开始寻路，
    重复点击会重新选点/打断寻路，2026-11-16 用户规则）。小地图窗口位置
    固定（即使被拖走，下次打开也会恢复默认位置），点击点直接按绝对
    坐标传入，不做窗口锚点相对换算（关闭按钮所在搜索栏宽度随状态变化，
    锚点不是稳定的内容参照，实机 19:04 锚点从 (1030,217) 漂到 (1011,229)）。
    **已在该地图时直接点位置区域开小地图**，不再绕世界地图 → 城市标签
    （2026-11-16 用户规则）；不在该地图才经世界地图点城市标签打开。
    失败不清理地图（调用方按需 close_maps/close_scene_panels），避免
    这个通用能力产生调用方意外的关闭动作。
    """
    pos = read_map_position(win)
    on_map = pos is not None and pos[0].strip() == map_name
    if on_map:
        ok, info = open_mini_map(win)
    else:
        ok, info = open_mini_map_by_name(win, map_name)
    if not ok:
        return False, f"打开{map_name}小地图失败：{info}"
    ok, finfo = ensure_minimap_task_filter(win, filter_pos)
    if not ok:
        return False, finfo
    human_click(win, label_pos[0], label_pos[1])
    return True, f"已打开{map_name}小地图，已点击小地图固定点 @({label_pos[0]},{label_pos[1]})"
