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


# ---- 进服装店前降落检查（2026-11-07：飞行中无法点击进店，到达布店
#      门口先 ensure_landed 再点入口；检查失败不阻断，超时兜底） ----

def _buy_env(monkeypatch, land_ok=True):
    """搭 _buy_equipments 桩环境（地图/寻路/店内交互全 mock），
    返回 (事件列表, 点击列表)。商店面板在点「我想买点东西」前不渲染
    （find_template 返回 None），模拟真实时序——面板未开时进店点击必须
    照常发生（_enter_cloth_shop 的「面板已开直接成功」短路不得提前触发）。"""
    events = []
    clicks = []
    state = {"shop_open": False}
    monkeypatch.setattr(wuhuan.game_map, "is_in_map", lambda win, name: True)
    monkeypatch.setattr(wuhuan.game_map, "is_mini_map_open", lambda win: True)
    monkeypatch.setattr(wuhuan, "close_scene_panels", lambda win, prefix: None)
    monkeypatch.setattr(wuhuan, "_wait_near",
                        lambda win, target, timeout, desc, interval:
                        events.append("arrived") or (True, "已到达"))
    monkeypatch.setattr(wuhuan.zuoqi, "ensure_landed",
                        lambda win: events.append("landed")
                        or (land_ok, "已降落" if land_ok else "面板内未定位到降落按钮"))
    monkeypatch.setattr(wuhuan.zuoqi, "close_feixingyuqi_panel",
                        lambda win: events.append("closed")
                        or (True, "飞行驭器面板已关闭"))
    monkeypatch.setattr(wuhuan, "human_click",
                        lambda win, x, y: clicks.append((x, y)))

    def _click_option(win, option, timeout=4):
        state["shop_open"] = True  # 点击选项后商店面板开始渲染
        return True, "已点击"

    monkeypatch.setattr(wuhuan, "_click_dialog_green_option", _click_option)
    finds = {wuhuan.FUZHUANGDIAN_XIANGLIAN_ICON: (100, 100),
             wuhuan.GOUMAI_ICON: (200, 200)}
    monkeypatch.setattr(wuhuan.game_map, "find_template",
                        lambda win, icon, threshold=None:
                        finds.get(icon) if state["shop_open"] else None)
    monkeypatch.setattr(wuhuan, "precise_click", lambda *a, **k: None)
    monkeypatch.setattr(wuhuan, "log", lambda msg, level="INFO": None)
    monkeypatch.setattr(wuhuan.time, "sleep", lambda s: None)
    return events, clicks


def test_buy_equipments_lands_before_enter(monkeypatch):
    """到达布店门口后：先降落检查（ensure_landed → 关面板），
    再点进店入口——顺序不可颠倒（飞行中进店点击无效）。"""
    events, clicks = _buy_env(monkeypatch, land_ok=True)
    ok, desc = wuhuan._buy_equipments(None)
    assert ok
    assert events == ["arrived", "landed", "closed"]
    assert wuhuan.CLOTH_SHOP_ENTER_POS in clicks


def test_buy_equipments_enter_proceeds_when_land_check_fails(monkeypatch):
    """降落检查失败不阻断：照常关面板并尝试进店（真飞行中进店失败由
    SHOP_ENTER_TIMEOUT 兜底后整轮重试，瞬态检查失败不影响正常进店）。"""
    events, clicks = _buy_env(monkeypatch, land_ok=False)
    ok, desc = wuhuan._buy_equipments(None)
    assert ok
    assert events == ["arrived", "landed", "closed"]
    assert wuhuan.CLOTH_SHOP_ENTER_POS in clicks


def test_buy_equipments_waits_shop_panel_render(monkeypatch):
    """点「我想买点东西」后面板渲染有延迟：轮询等项链图标出现才判定，
    不得单次匹配即判「商店中未找到服装店项链」（2026-11-10 时序竞态
    实测：点后 0.3s 单次匹配未命中使整个准备阶段失败，随后同一状态
    项链模板匹配 0.9999）。"""
    events, clicks = _buy_env(monkeypatch, land_ok=True)
    base_find = wuhuan.game_map.find_template
    calls = {"n": 0}

    def _slow_render(win, icon, threshold=None):
        if icon == wuhuan.FUZHUANGDIAN_XIANGLIAN_ICON:
            calls["n"] += 1
            if calls["n"] <= 3:
                return None  # 前几次轮询面板尚未渲染完成
        return base_find(win, icon, threshold)

    monkeypatch.setattr(wuhuan.game_map, "find_template", _slow_render)
    ok, desc = wuhuan._buy_equipments(None)
    assert ok
    assert calls["n"] > 3  # 确实轮询等待了面板渲染
    assert wuhuan.SHOP_OWNER_POS in clicks


# ---- 无选项剧情对话点屏推进（2026-11-10：NPC 剧情台词常驻屏幕命中
#      对话框模板门（实测 0.9886），对话区每 0.5s 全量 OCR 成为 CPU
#      大头，与修罗 NPC 收尾对话 2026-11-09 同款） ----

_STORY_ITEMS = _make([
    ("我被人骗了", 435, 395, 510, 416),
    ("有人骗走了我的20万两银子", 556, 395, 878, 415),
    ("哥哥以后你一定要小心这种人啊", 436, 412, 644, 433),
])
_BLANK_FRAME = np.zeros((831, 1378, 3), np.uint8)


def _story_env(monkeypatch, score=0.99, green=False):
    """搭 _dismiss_story_dialog 桩环境，返回点屏次数列表。

    共享实现 StoryDialogDismisser 在 dialog_common 中取模板分与点屏，
    桩打在 dialog_common 上；实例状态（streak/last_click/寻路判定）逐
    用例重置。寻路时间戳桩返回从未寻路；无帧可查（win=None）按未寻路
    放行，与旧行为一致。
    """
    from src import dialog_common
    clicks = []
    monkeypatch.setattr(dialog_common, "dialog_frame_score", lambda img: score)
    monkeypatch.setattr(wuhuan.task_tracker, "is_green_text",
                        lambda img, rect: green)
    monkeypatch.setattr(dialog_common, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(dialog_common, "log", lambda msg, level="INFO": None)
    monkeypatch.setattr(dialog_common.app_utils, "track_walk_idle_seconds",
                        lambda: 1e9)
    dismisser = wuhuan._story_dismisser
    dismisser.streak = 0
    dismisser.last_click_at = 0.0
    dismisser._reset_walk()
    wuhuan._give_expected_until = 0.0
    return clicks


def test_story_dialog_dismiss_after_two_polls(monkeypatch):
    """连续 2 轮询命中才点屏（单帧可能是渲染过渡；无帧可查时不做
    寻路判定，与旧行为一致）。"""
    clicks = _story_env(monkeypatch)
    assert wuhuan._dismiss_story_dialog(None, _STORY_ITEMS, _BLANK_FRAME) is False
    assert wuhuan._dismiss_story_dialog(None, _STORY_ITEMS, _BLANK_FRAME) is True
    assert clicks == [1]


def test_story_dialog_no_click_without_dialog_frame(monkeypatch):
    """无标准对话框边框（如商店面板实测 0.653 < 0.79）不点屏——
    世界点击会误触走位。"""
    clicks = _story_env(monkeypatch, score=0.65)
    for _ in range(3):
        assert wuhuan._dismiss_story_dialog(
            None, _STORY_ITEMS, _BLANK_FRAME) is False
    assert clicks == []


def test_story_dialog_no_click_with_green_option(monkeypatch):
    """对话区有绿字选项（提交装备/巫医等）不是剧情对话，不点屏。"""
    clicks = _story_env(monkeypatch, green=True)
    for _ in range(3):
        assert wuhuan._dismiss_story_dialog(
            None, _STORY_ITEMS, _BLANK_FRAME) is False
    assert clicks == []


def test_story_dialog_no_click_during_give_window(monkeypatch):
    """给与面板预期窗口内不点屏（选项已点、等给与面板时推进 NPC
    对话有打断给与流程的风险）。"""
    import time as _time
    clicks = _story_env(monkeypatch)
    wuhuan._give_expected_until = _time.time() + 10
    for _ in range(3):
        assert wuhuan._dismiss_story_dialog(
            None, _STORY_ITEMS, _BLANK_FRAME) is False
    assert clicks == []


def test_story_dialog_panel_items_ignored(monkeypatch):
    """追踪面板文字（x < DIALOG_REGION 左界）不参与判定：面板有绿字
    链接，注入后仍应正常点屏。"""
    clicks = _story_env(monkeypatch)
    items = _make([("五环[五环]", 14, 233, 100, 253),
                   ("去找大唐边境(193,144)惯骗[3/5]", 20, 249, 260, 276)]) \
        + _STORY_ITEMS
    assert wuhuan._dismiss_story_dialog(None, items, _BLANK_FRAME) is False
    assert wuhuan._dismiss_story_dialog(None, items, _BLANK_FRAME) is True
    assert clicks == [1]


def test_submit_dialog_falls_back_to_story_dismiss(monkeypatch):
    """提交对话文案未命中且无绿字 → 走剧情对话推进（此前直接 return
    False，剧情台词永远无人处理）。"""
    _story_env(monkeypatch)
    monkeypatch.setattr(wuhuan.game_map, "find_template",
                        lambda win, icon, threshold=None: None)
    assert wuhuan._handle_submit_dialog(None, _STORY_ITEMS, _BLANK_FRAME) is False
    assert wuhuan._handle_submit_dialog(None, _STORY_ITEMS, _BLANK_FRAME) is True


def test_submit_dialog_option_not_rendered_no_story_dismiss(monkeypatch):
    """提交对话文案命中但绿字选项尚未渲染：等下一轮，不得误判为剧情
    对话点屏关闭（会打断提交装备流程）。"""
    clicks = _story_env(monkeypatch)  # green=False → 选项点击必失败
    monkeypatch.setattr(wuhuan.game_map, "find_template",
                        lambda win, icon, threshold=None: None)
    items = _make([("我在这里练级，打了好久都打不过夜叉和猪怪。",
                    435, 395, 878, 433)]) + _STORY_ITEMS
    for _ in range(3):
        assert wuhuan._handle_submit_dialog(None, items, _BLANK_FRAME) is False
    assert clicks == []


# ---- 任务消失判定：面板缺席处理已统一到 xuanshu._confirm_task_gone
#      （2026-11-12 用户规则），五环不再传 per-task 参数 ----
def test_follow_wuhuan_uses_shared_confirm(monkeypatch):
    """五环 follow_task 走共享引擎（prefix/标题正确，无 per-task 面板参数）。"""
    seen = {}

    def _fake_follow(win, title, **kwargs):
        seen["title"] = title
        seen.update(kwargs)
        return True, "任务已从任务追踪中消失"

    monkeypatch.setattr(wuhuan, "follow_task", _fake_follow)
    ok, _info = wuhuan._follow_wuhuan(object())
    assert ok is True
    assert seen["title"] == wuhuan.WUHUAN_TITLE
    assert seen["prefix"] == "五环"
    assert "task_gone_require_panel" not in seen
