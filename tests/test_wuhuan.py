"""wuhuan.py：五环文本签名键（_wuhuan_text_key）归一稳定性与链接行
精确定位（_wuhuan_link_picker）。"""
from difflib import SequenceMatcher

import numpy as np

from src import task_tracker
from src import wuhuan


def _make(rows):
    """合成 OCR items：[(txt, [x0, y0, x1, y1], score), ...]。"""
    return [(t, [x0, y0, x1, y1], 0.95) for t, x0, y0, x1, y1 in rows]


# 2026-10-31 实测日志中的链接行 OCR 变体（同一视觉行「去找大雁塔二层
# (13，47)情缘」被区域 OCR 以不同方式拆分/合并，签名随之翻转、每 2s
# 重复点击 22 轮）
_A = _make([  # 拆分成「去找大雁塔二」+「情缘」
    ("五环[五环]", 14, 233, 100, 253),
    ("去找大雁塔二", 20, 249, 150, 276),
    ("情缘", 180, 249, 224, 276),
    ("24载的替身[1/5]", 19, 262, 139, 293),
    ("剩余时间：00:59:41", 22, 286, 164, 306),
])
_B = _make([  # 拆分成「47情缘」+「去找大雁塔二」（括号丢失变体）
    ("五环[五环]", 14, 233, 100, 253),
    ("47情缘", 150, 249, 224, 276),
    ("去找大雁塔二", 20, 249, 150, 276),
    ("24载的替身[1/5]", 19, 262, 139, 293),
])
_C = _make([  # 整行合并且坐标读成 1347 混入文案
    ("五环[五环]", 14, 233, 100, 253),
    ("去找大雁塔二层1347情缘", 20, 249, 224, 276),
    ("24载的替身[1/5]", 19, 262, 139, 293),
])
_D = _make([  # 拆分成「去找大雁塔二」+「(13，47)情缘」
    ("五环[五环]", 14, 233, 100, 253),
    ("去找大雁塔二", 20, 249, 150, 276),
    ("(13，47)情缘", 150, 249, 224, 276),
])
_E = _make([  # 场景玩家名随走动混入面板区域（签名污染实测形态）
    ("五环[五环]", 14, 233, 100, 253),
    ("去找大雁塔二", 20, 249, 150, 276),
    ("(13，47)情缘", 150, 249, 224, 276),
    ("日行千里", 30, 340, 140, 360),
    ("老谭酸菜面", 30, 365, 160, 385),
    ("醉饮江天", 30, 390, 160, 410),
])
_F = _make([  # 链接行整个漏检（仅标题+替身行）
    ("五环[五环]", 14, 233, 100, 253),
    ("24载的替身[1/5]", 19, 262, 139, 293),
])
_G = _make([  # 真实推进：换到下一个子目标
    ("五环[五环]", 14, 233, 100, 253),
    ("去找大唐边境(193,144)惯骗[3/5]", 20, 249, 260, 276),
])


def _sig(items):
    return wuhuan._wuhuan_text_key(items, None)


def test_link_fragment_variants_signature_similar():
    """链接行各拆分/合并变体归一后签名相似（≥0.8，不再触发误变化）。"""
    base = _sig(_A)
    assert base
    for items in (_B, _C, _D, _E):
        sig = _sig(items)
        assert sig, f"变体签名不应为空: {sig}"
        r = SequenceMatcher(None, base, sig).ratio()
        assert r >= 0.8, f"变体签名与基线相似度 {r:.2f} < 0.8: {sig!r} vs {base!r}"


def test_real_progress_signature_differs():
    """真实推进（换子目标）签名与基线差异显著（<0.8，变化可检出）。"""
    r = SequenceMatcher(None, _sig(_A), _sig(_G)).ratio()
    assert r < 0.8, f"真实推进未被签名区分: {r:.2f}"


def test_link_line_missing_returns_empty():
    """链接行未检出返回空串（调用方视作内容不可读，不点击不作信号）。"""
    assert _sig(_F) == ""


def test_title_punctuation_normalized():
    """标题标点变体（「·五环[五环]」）归一后签名一致。"""
    items = _make([
        ("·五环[五环]", 14, 233, 100, 253),
        ("去找大雁塔二层(13，47)情缘", 20, 249, 224, 276),
    ])
    sig = _sig(items)
    assert sig.startswith("五环五环|")


# ---- 链接行精确定位（_wuhuan_link_picker）----

def _frame_with_link_fragments():
    """2026-10-31 实测面板形态：链接行拆成「去找大雁塔二」+「情缘」两
    碎片、替身行亮绿带下划线、剩余时间行沾绿（框重叠 3.2%）。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 25:145:4] = (0, 255, 0)   # 替身行文字（亮绿笔画）
    img[287, 24:115] = (0, 255, 0)         # 替身行下划线
    img[255:268, 21:120:4] = (0, 255, 0)   # 「去找大雁塔二」碎片（亮绿）
    img[255:268, 182:222:4] = (0, 255, 0)  # 「情缘」碎片（亮绿）
    img[270, 189:220] = (0, 255, 0)        # 链接行尾下划线（31px 实线）
    return img


def _panel_items():
    return _make([
        ("五环[五环]", 14, 233, 100, 253),
        ("去找大雁塔二", 21, 251, 120, 271),
        ("情缘", 182, 252, 222, 272),
        ("24载的替身[1/5]", 19, 262, 139, 293),
        ("剩余时间：00:59:41", 22, 283, 162, 304),
    ])


def test_link_picker_clicks_merged_line_underline():
    """碎片合并后按链接标记选中合并行，点中行尾下划线（204,268）。

    旧行级选择点中「去找大雁塔二」碎片文字中点 (108,264)——点不中
    链接，人物原地不动（2026-10-31 实测 2 分钟空转）。
    """
    img = _frame_with_link_fragments()
    pt = wuhuan._wuhuan_link_picker(_panel_items(), img)
    assert pt == (204, 268)


def test_link_picker_refuses_when_link_line_missing():
    """链接行整段漏检（只剩替身行/剩余时间行）→ PICK_FAIL，不点任何行。

    旧路径点中替身行下划线 (69,285)（同为亮绿带下划线，颜色无法区分，
    2026-10-31 点错教训）。
    """
    img = _frame_with_link_fragments()
    items = [it for it in _panel_items() if "去找" not in it[0]
             and it[0] != "情缘"]
    assert wuhuan._wuhuan_link_picker(items, img) == task_tracker.PICK_FAIL


def test_link_picker_tail_only_read_clicks_underline():
    """只剩「(13，47)情缘」尾部碎片时（坐标行兜底入候选），同样点中
    下划线。"""
    img = _frame_with_link_fragments()
    items = _make([
        ("五环[五环]", 14, 233, 100, 253),
        ("(13，47)情缘", 150, 249, 224, 276),
        ("24载的替身[1/5]", 19, 262, 139, 293),
    ])
    pt = wuhuan._wuhuan_link_picker(items, img)
    assert pt == (204, 268)
