"""wuhuan.py：五环文本签名键（_wuhuan_text_key）归一稳定性与链接行
精确定位（_wuhuan_link_picker）。"""
from difflib import SequenceMatcher

import numpy as np

from src.device import task_tracker
from src.tasks import wuhuan


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


def _frame_with_link_fragments_no_underline():
    """链接行渲染过渡帧：碎片亮绿、行尾下划线尚未渲染（2026-11-19
    浮字劫持实测帧型——下划线检出时 green_text_center 直接点下划线，
    不会回退到「最右绿块」，浮字也就抢不走点击）。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[255:268, 25:145:4] = (0, 255, 0)   # 替身行文字（亮绿笔画）
    img[287, 24:115] = (0, 255, 0)         # 替身行下划线
    img[255:268, 21:120:4] = (0, 255, 0)   # 「去找大雁塔二」碎片（亮绿）
    img[255:268, 182:222:4] = (0, 255, 0)  # 「情缘」碎片（亮绿）
    return img


def test_link_picker_ignores_change_float_row():
    """任务变化绿色浮字提示（同帧、x≈200 起、与链接行纵向重叠）不得
    参与同行合并或成为点击目标。

    2026-11-19 五环实测：进度键变化确认的同一帧弹浮字，旧行采集只按
    panel_row_xmax(≥400) 过滤行首，浮字行并入链接行合并框；链接行
    过渡帧下划线未检出 → green_text_center 回退取最右绿块点到浮字
    (258,262)，浮字不可点、人物原地 93s 不动（等进度键抖动重新点链接
    才恢复）。修复后左列外浮字行被排除并从像素帧抹黑。
    """
    img = _frame_with_link_fragments_no_underline()
    img[252:269, 202:330] = (0, 255, 0)    # 浮字绿色文字块（连续实心）
    items = _make([
        ("五环[五环]", 14, 233, 100, 253),
        ("去找大雁塔二", 21, 251, 120, 271),
        ("情缘", 182, 252, 222, 272),
        ("24载的替身[1/5]", 19, 262, 139, 293),
        # 浮字（同文案，落在追踪 OCR 区域「区域左缘+300」内）
        ("去找大雁塔二层(13，47)情缘", 202, 250, 330, 274),
    ])
    pt = wuhuan._wuhuan_link_picker(items, img)
    # 修复前 ≈(265,262)（浮字中心）；修复后落在面板链接行碎片上
    assert pt[0] < 202, f"点到浮字区: {pt}"
    assert 250 <= pt[1] <= 275, f"点击行不在链接行: {pt}"


def test_link_picker_region_column_excludes_float_anchor(monkeypatch):
    """标题条模板定位到面板区域时：左列界 = 区域左缘 -10 ~ +ANCHOR_MAX_X。

    浮字小标题（标题形态如「【五环】」、x≥左列界）不得劫持锚点；
    行采集同样限左列，浮字行不参与合并。
    """
    monkeypatch.setattr(wuhuan.task_tracker, "track_content_region",
                        lambda img_np, min_width=None: (7, 227, 307, 557))
    img = _frame_with_link_fragments()  # 带行尾下划线（正常帧）
    items = _make([
        # 浮字小标题排在最前且纵坐标更高——旧锚点循环会先命中它
        ("【五环】", 210, 236, 290, 254),
        ("五环[五环]", 14, 233, 100, 253),
        ("去找大雁塔二", 21, 251, 120, 271),
        ("情缘", 182, 252, 222, 272),
        ("24载的替身[1/5]", 19, 262, 139, 293),
        ("剩余时间：00:59:41", 22, 283, 162, 304),
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
    monkeypatch.setattr(
        wuhuan.game_map, "uncheck_minimap_task_filter",
        lambda win: (True, "「任务」筛选未勾选"))
    monkeypatch.setattr(wuhuan, "close_scene_panels",
                        lambda win, prefix: events.append("panels"))
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

    def _click_option(win, option, timeout=4, prefix=""):
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
    """到达布店门口后：先清理面板，再做降落检查（ensure_landed → 关面板），
    再点进店入口——顺序不可颠倒（飞行中进店点击无效）。"""
    events, clicks = _buy_env(monkeypatch, land_ok=True)
    ok, desc = wuhuan._buy_equipments(None)
    assert ok
    assert events == ["arrived", "panels", "landed", "closed"]
    assert wuhuan.CLOTH_SHOP_ENTER_POS in clicks


def test_buy_equipments_enter_proceeds_when_land_check_fails(monkeypatch):
    """降落检查失败不阻断：照常关面板并尝试进店（真飞行中进店失败由
    SHOP_ENTER_TIMEOUT 兜底后整轮重试，瞬态检查失败不影响正常进店）。"""
    events, clicks = _buy_env(monkeypatch, land_ok=False)
    ok, desc = wuhuan._buy_equipments(None)
    assert ok
    assert events == ["arrived", "panels", "landed", "closed"]
    assert wuhuan.CLOTH_SHOP_ENTER_POS in clicks


def test_yunyou_walk_closes_panels_only_after_arrival(monkeypatch):
    """点小地图寻路后不得立即关闭小地图：那次点击会终止自动寻路。

    2026-11-20 用户实机（wuhuan 必现）：跨地图到长安 → 点小地图云游
    大师位置 → 关闭小地图左键点击 @(1098,217) 落到世界，人物停下不再
    走。清理必须等寻路结束（到达站定）之后再做。
    """
    events = []
    clicks = []
    monkeypatch.setattr(wuhuan.game_map, "is_in_map",
                        lambda win, name: True)
    monkeypatch.setattr(wuhuan.game_map, "is_mini_map_open",
                        lambda win: True)
    monkeypatch.setattr(
        wuhuan.game_map, "uncheck_minimap_task_filter",
        lambda win: (True, "「任务」筛选未勾选"))
    monkeypatch.setattr(wuhuan, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(wuhuan, "close_scene_panels",
                        lambda win, prefix: events.append("panels"))

    def _wait_near(win, target, timeout, desc, interval):
        events.append("arrived")
        return True, "已到达"
    monkeypatch.setattr(wuhuan, "_wait_near", _wait_near)
    monkeypatch.setattr(wuhuan, "_wait_template",
                        lambda win, icon, timeout: True)
    monkeypatch.setattr(wuhuan.game_map, "find_template",
                        lambda win, icon, threshold=None: (300, 300))
    monkeypatch.setattr(wuhuan, "log", lambda msg, level="INFO": None)

    ok, _info = wuhuan._walk_to_yunyou_claim_btn(None)
    assert ok
    # 寻路点击之后到到达之前不得有任何额外点击/清理（多余点击会终止寻路）
    assert clicks[0] == wuhuan.YUNYOU_MAP_POS
    assert clicks[1] == (300, 300)  # 到达后点领取按钮
    assert events == ["arrived", "panels"]  # 先到达，再清理面板


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
    from src.device import dialog_common
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
    """对话区有整行绿字选项（提交装备/巫医等）不是剧情对话，不点屏。

    2026-11-15 起选项判定区分「整行绿字」与「行内绿字高亮」：选项行
    为纯绿字（正文像素≈0），须用真实像素构造（见
    dialog_common.green_option_row_present）。
    """
    from src.device import task_tracker
    clicks = _story_env(monkeypatch, green=task_tracker.is_green_text)
    frame = _BLANK_FRAME.copy()
    l, t, r, b = _STORY_ITEMS[0][1]
    w = r - l
    for i in range(600):  # 纯绿字选项行（无白色正文）
        frame[t + i // w, l + i % w] = (60, 220, 60)
    for _ in range(3):
        assert wuhuan._dismiss_story_dialog(
            None, _STORY_ITEMS, frame) is False
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


# ---- 任务消失判定：面板缺席处理已统一到 workflows.follow._confirm_task_gone
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
    # 战斗结束补点恢复开启（用户 2026-11-14：战败/遭遇战打断寻路后
    # 签名不变，也须补点任务链接重新寻路重新进入战斗）
    assert seen["battle_resume_reclick"] is True
    # 寻路停滞看门狗全轮次启用（2026-11-19 点击落到任务变化浮字上、
    # 人物原地 93s 不动的实测教训）
    assert seen["stall_recheck"] is True
    assert seen["stall_recheck_target"] is None


def test_enter_cloth_shop_coord_nav(monkeypatch, coord_nav_enabled):
    """坐标化开关开启：进布店走位改走 map_nav 坐标点击（长安 130,128）。"""
    from src.device import map_nav
    started = []

    def _start(w, name, x, y, log_fn=None, on_map_open=None):
        started.append((name, x, y))
        return True, "已点击", (475, 443)

    monkeypatch.setattr(map_nav, "start_navigation", _start)
    monkeypatch.setattr(wuhuan.game_map, "read_map_position",
                        lambda w: ("长安", 1, 1))  # 不在布店门口
    monkeypatch.setattr(wuhuan, "_wait_near",
                        lambda *a, **k: (False, "自定义到达失败"))
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    ok, info = wuhuan._enter_cloth_shop(object(), tag="[五环]")
    assert ok is False and "自定义到达失败" in info
    assert started == [(wuhuan.CHANGAN_CITY,
                        *wuhuan.CLOTH_SHOP_TARGET_POS)]


def test_enter_cloth_shop_skips_nav_when_already_near(
        monkeypatch, coord_nav_enabled):
    """已在布店门口（±5）时不再开图/标定点击（省 ~2s，避免重复寻路）。"""
    from src.device import map_nav
    started = []
    monkeypatch.setattr(
        map_nav, "start_navigation",
        lambda *a, **k: started.append(1) or (True, "", None))
    monkeypatch.setattr(
        wuhuan.game_map, "read_map_position",
        lambda w: ("长安", *wuhuan.CLOTH_SHOP_TARGET_POS))
    monkeypatch.setattr(wuhuan.time, "sleep", lambda s: None)  # 站定复读等待
    monkeypatch.setattr(wuhuan, "_wait_near",
                        lambda *a, **k: (False, "自定义到达失败"))
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    ok, info = wuhuan._enter_cloth_shop(object(), tag="[五环]")
    assert ok is False and "自定义到达失败" in info
    assert started == []  # 未走坐标导航


# ---- 领取冷却（昨天与今天的领取时间间隔提示，2026-11-23 用户需求） ----
# 实测提示对话框：「你距离下次领取5环任务还差5分钟，等等再来吧！」
# （用户截图 2026-11-23，出现在点「我可以帮助这些人」之后；点活力值
# 「不差那点小钱…」后同样可能弹出）。
_COOLDOWN_ITEMS = _make([
    ("你距离下次领取5环任务还差5分钟，等等再来吧！", 435, 390, 930, 430),
])


def test_cooldown_parse_minutes():
    assert wuhuan._parse_cooldown_seconds(_COOLDOWN_ITEMS) == 300


def test_cooldown_parse_hours_and_minutes():
    items = _make([
        ("你距离下次领取5环任务还差1小时20分钟，等等再来吧！",
         435, 390, 930, 430),
    ])
    assert wuhuan._parse_cooldown_seconds(items) == 4800


def test_cooldown_parse_split_lines():
    """对话框被 OCR 拆成两行（「…还差」+「30分钟…」）仍能解析。"""
    items = _make([
        ("你距离下次领取5环任务还差", 435, 380, 700, 405),
        ("30分钟，等等再来吧！", 700, 380, 930, 405),
    ])
    assert wuhuan._parse_cooldown_seconds(items) == 1800


def test_cooldown_parse_seconds_unit():
    items = _make([
        ("你距离下次领取5环任务还差30秒，等等再来吧！", 435, 390, 930, 430),
    ])
    assert wuhuan._parse_cooldown_seconds(items) == 30


def test_cooldown_parse_unparseable_returns_none():
    items = _make([
        ("你距离下次领取5环任务还差一会儿，等等再来吧！", 435, 390, 930, 430),
    ])
    assert wuhuan._parse_cooldown_seconds(items) is None


def test_cooldown_dialog_present_main_needle():
    assert wuhuan._cooldown_dialog_present(_COOLDOWN_ITEMS) is True


def test_cooldown_dialog_present_marker_fallback():
    """主判定文案被大面积误读时，组合标记（还差/领取/再来）兜底命中。"""
    from src.device.dialog_common import _dialog_text_present
    items = _make([
        ("你和上次领取隔得还差一会儿，等等再来吧", 435, 390, 930, 430),
    ])
    assert _dialog_text_present(items, wuhuan.COOLDOWN_DIALOG_TEXT) is False
    assert wuhuan._cooldown_dialog_present(items) is True


def test_cooldown_dialog_absent_on_normal_dialog():
    items = _make([("我在这里练级，打了好久都打不过夜叉和猪怪。",
                    435, 395, 878, 433)])
    assert wuhuan._cooldown_dialog_present(items) is False
    assert wuhuan._parse_cooldown_seconds(items) is None


def test_consume_cooldown_dialog_persists_and_closes(monkeypatch):
    noted, clicks, logs = [], [], []

    def _note(win, seconds):
        noted.append(seconds)
        return wuhuan.time.time() + seconds

    monkeypatch.setattr(wuhuan, "_note_cooldown", _note)
    monkeypatch.setattr(wuhuan, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(wuhuan, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    assert wuhuan._consume_cooldown_dialog(None, _COOLDOWN_ITEMS) == 300
    assert noted == [300]
    assert clicks == [1]  # 关闭提示对话框，防遮挡后续队列任务
    assert any("还差 5分0秒" in msg for msg, _ in logs)


def test_consume_cooldown_dialog_unparseable_uses_default(monkeypatch):
    noted = []
    monkeypatch.setattr(wuhuan, "_note_cooldown",
                        lambda win, seconds: noted.append(seconds) or 0.0)
    monkeypatch.setattr(wuhuan, "_click_screen_center",
                        lambda win, times=1, prefix="": None)
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    items = _make([
        ("你距离下次领取5环任务还差一会儿，等等再来吧！", 435, 390, 930, 430),
    ])
    assert wuhuan._consume_cooldown_dialog(None, items) \
        == wuhuan.COOLDOWN_DEFAULT_SECONDS
    assert noted == [wuhuan.COOLDOWN_DEFAULT_SECONDS]


def test_consume_cooldown_dialog_absent_no_side_effects(monkeypatch):
    called = []
    monkeypatch.setattr(wuhuan, "_note_cooldown",
                        lambda *a, **k: called.append(1) or 0.0)
    monkeypatch.setattr(wuhuan, "_click_screen_center",
                        lambda *a, **k: called.append(2))
    assert wuhuan._consume_cooldown_dialog(
        None, _make([("普通的对话", 435, 390, 930, 430)])) is None
    assert called == []


def test_consume_cooldown_dialog_diagnostic_no_close(monkeypatch):
    """诊断链路 close=False：记录冷却但不点屏（不改动界面状态）。"""
    noted, clicks = [], []
    monkeypatch.setattr(wuhuan, "_note_cooldown",
                        lambda win, seconds: noted.append(seconds) or 0.0)
    monkeypatch.setattr(wuhuan, "_click_screen_center",
                        lambda *a, **k: clicks.append(1))
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    assert wuhuan._consume_cooldown_dialog(None, _COOLDOWN_ITEMS,
                                           close=False) == 300
    assert noted == [300] and clicks == []


def test_claim_blocker_cooldown_info(monkeypatch):
    monkeypatch.setattr(wuhuan, "_consume_cooldown_dialog",
                        lambda win, items, close=True: 300)
    ended, info, continued = wuhuan._handle_claim_blockers(None, [], None)
    assert ended is True
    assert info == wuhuan.COOLDOWN_INFO
    assert continued is False


def test_claim_blocker_cooldown_handled_skips_recheck(monkeypatch):
    called = []
    monkeypatch.setattr(wuhuan, "_consume_cooldown_dialog",
                        lambda win, items, close=True: called.append(1) or 300)
    ended, info, continued = wuhuan._handle_claim_blockers(
        None, [], None, cooldown_handled=True)
    assert (ended, info, continued) == (False, None, False)
    assert called == []


def test_cooldown_persist_roundtrip(monkeypatch):
    """截止时间持久化（state.json 按角色）：含安全余量、可读取、可清除。"""
    win = object()
    wuhuan._clear_cooldown(win)
    wuhuan._note_cooldown(win, 300)
    remaining = wuhuan.cooldown_remaining(win)
    assert 300 < remaining <= 300 + wuhuan.COOLDOWN_SAFETY_MARGIN + 2
    wuhuan._clear_cooldown(win)
    assert wuhuan.cooldown_remaining(win) == 0


def test_cooldown_renotify_keeps_earlier_deadline(monkeypatch):
    """同一段冷却被重复识别（遗留对话框）不把截止时间越推越迟。"""
    win = object()
    wuhuan._clear_cooldown(win)
    wuhuan._note_cooldown(win, 300)
    first = wuhuan._cooldown_until(win)
    wuhuan._note_cooldown(win, 300)
    assert wuhuan._cooldown_until(win) == first
    wuhuan._clear_cooldown(win)


def test_wait_cooldown_returns_after_expiry(monkeypatch):
    seq = iter([5.0, 0.0])
    monkeypatch.setattr(wuhuan, "cooldown_remaining", lambda win: next(seq))
    monkeypatch.setattr(wuhuan.time, "sleep", lambda s: None)
    monkeypatch.setattr(wuhuan.app_utils, "raise_if_aborted", lambda: None)
    monkeypatch.setattr(wuhuan.debug_overlay, "set_phase", lambda text: None)
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    ok, info = wuhuan._wait_cooldown(None)
    assert ok is True and "冷却已结束" in info


class _FakeClaimWin:
    """领取流程最小窗口桩（capture 返回非空帧即可）。"""

    def capture(self, force_activate=False):
        return object()


def test_claim_task_cooldown_returns_without_waiting(monkeypatch):
    """非最后一项任务时（wait_cooldown=False）检测到冷却提示立即返回
    COOLDOWN_INFO，不寻路不等待——由 main 调度先执行其他任务。"""
    walked = []
    monkeypatch.setattr(wuhuan, "is_in_battle", lambda win: False)
    monkeypatch.setattr(wuhuan.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(wuhuan, "_consume_cooldown_dialog",
                        lambda win, items, close=True: 300)
    monkeypatch.setattr(wuhuan, "_walk_near_yunyou",
                        lambda *a, **k: walked.append(1) or (True, "ok"))
    monkeypatch.setattr(wuhuan, "_wait_cooldown",
                        lambda win: walked.append(2) or (True, "ok"))
    ok, info = wuhuan._claim_task(_FakeClaimWin(), wait_cooldown=False)
    assert (ok, info) == (False, wuhuan.COOLDOWN_INFO)
    assert walked == []


def test_claim_task_cooldown_waits_when_last_task(monkeypatch):
    """五环为最后一项任务时（wait_cooldown=True）：先走到云游大师并等待
    冷却结束，再走常规领取链路。"""
    walked = []
    monkeypatch.setattr(wuhuan, "is_in_battle", lambda win: False)
    monkeypatch.setattr(wuhuan.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(wuhuan, "_consume_cooldown_dialog",
                        lambda win, items, close=True: 300)
    monkeypatch.setattr(wuhuan, "_handle_claim_blockers",
                        lambda *a, **k: (False, None, False))
    monkeypatch.setattr(wuhuan, "cooldown_remaining", lambda win: 10.0)
    monkeypatch.setattr(wuhuan, "_walk_near_yunyou",
                        lambda *a, **k: walked.append("near") or (True, "ok"))
    monkeypatch.setattr(wuhuan, "_wait_cooldown",
                        lambda win: walked.append("wait") or (True, "ok"))
    monkeypatch.setattr(wuhuan, "_walk_to_yunyou_claim_btn",
                        lambda *a, **k: walked.append("claim")
                        or (False, "到达后未出现领取任务按钮"))
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    ok, info = wuhuan._claim_task(_FakeClaimWin(), wait_cooldown=True)
    assert (ok, info) == (False, "到达后未出现领取任务按钮")
    assert walked == ["near", "wait", "claim"]


def test_run_loop_skips_when_cooldown_beyond_wait_cap(monkeypatch):
    """冷却剩余超过单次等待上限：本轮跳过（不准备不领取），返回哨兵
    COOLDOWN_SKIP_INFO（main 不会据此重新排队）。"""
    prepared = []
    monkeypatch.setattr(wuhuan, "load_task_counts", lambda: {"五环": 2})
    monkeypatch.setattr(wuhuan.app_utils, "task_progress_done",
                        lambda win, mode: 0)
    monkeypatch.setattr(wuhuan.task_tracker, "has_task",
                        lambda win, title: False)
    monkeypatch.setattr(wuhuan, "cooldown_remaining",
                        lambda win: wuhuan.COOLDOWN_MAX_WAIT + 60)
    monkeypatch.setattr(wuhuan, "_prepare",
                        lambda win: prepared.append(1) or (True, "ok"))
    monkeypatch.setattr(wuhuan, "log", lambda *a, **k: None)
    assert wuhuan.run_loop(object()) == wuhuan.COOLDOWN_SKIP_INFO
    assert prepared == []
