"""mingwang.py：名望任务第一阶段（种族判定 + 魔族领取入口）。

验证点：
- 模板校准：四个用户素材（renwushuxing / shijie_datangbianjing /
  mingwang_nitianmo / mingwang_shanhemingwang）贴入合成帧可命中，
  纯噪声帧低于阈值
- 种族判定：魔族/仙族角色列表文案 → 对应种族；同票/无关键词不判定
- 种族条带：标题牌匹配框右侧 120px、上下扩边
- 领取点坐标判定容差
- detect_race：面板已在时不再点按钮，OCR 区域为标题牌右侧条带，
  识别后关闭面板
"""
import time

import cv2
import numpy as np
from PIL import Image
from ascript.windows.system import R

from src import map as game_map
from src import mingwang

# 模板 → 匹配阈值（与 mingwang 常量一致；shijie 标签走 open_mini_map_city
# 默认 0.8）
TEMPLATES = (
    (mingwang.RENWUSHUXING_ICON, mingwang.ATTR_TEMPLATE_THRESHOLD),
    (mingwang.CLAIM_WORLD_ICON, 0.8),
    (mingwang.CLAIM_NPC_ICON, mingwang.CLAIM_NPC_THRESHOLD),
    (mingwang.CLAIM_SHANHE_ICON, mingwang.CLAIM_SHANHE_THRESHOLD),
)

MOZU_ITEMS = [
    ("男魔角色", [540, 10, 610, 28], 0.95),
    ("夺命妖", [540, 34, 580, 50], 0.92),
    ("逆天魔", [540, 100, 580, 116], 0.93),
    ("女魔角色", [540, 150, 610, 168], 0.94),
    ("狐美人", [540, 190, 580, 206], 0.9),
]

XIANZU_ITEMS = [
    ("智圣仙", [540, 10, 580, 26], 0.94),
    ("女仙角色", [540, 90, 610, 108], 0.95),
    ("玄剑娥", [540, 120, 580, 136], 0.9),
    ("霓裳仙", [540, 180, 580, 196], 0.92),
]


class _FakeWin:
    """capture 按序返回帧（PIL）；帧用尽后返回最后一帧。"""

    def __init__(self, frames):
        self.frames = list(frames)
        self.calls = 0

    def capture(self, force_activate=False):
        self.calls += 1
        idx = min(self.calls - 1, len(self.frames) - 1)
        return self.frames[idx]


def _noise_bgr(seed=20261110):
    r = np.random.default_rng(seed)
    return r.integers(0, 256, (831, 1378, 3), dtype=np.uint8)


def _paste_bgr(icon, x=600, y=300, seed=20261110):
    tpl = cv2.imread(R.img(icon), cv2.IMREAD_COLOR)
    frame = _noise_bgr(seed)
    th, tw = tpl.shape[:2]
    frame[y:y + th, x:x + tw] = tpl
    return frame


def _pil(bgr):
    return Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


def test_templates_self_match():
    for icon, threshold in TEMPLATES:
        frame = _paste_bgr(icon)
        score, pt = game_map.best_match_np(frame, icon)
        assert score >= threshold, (icon, score, threshold)
        assert game_map.match_frame_np(frame, icon, threshold=threshold,
                                       fallback=False) is not None, icon


def test_templates_no_false_positive():
    frame = _noise_bgr(seed=99)
    for icon, threshold in TEMPLATES:
        score, _pt = game_map.best_match_np(frame, icon)
        assert score < threshold, (icon, score, threshold)


def test_race_from_items_mozu():
    race, counts, lines = mingwang._race_from_items(MOZU_ITEMS)
    assert race == "魔族"
    assert counts["魔族"] == 3  # 男魔角色 / 逆天魔 / 女魔角色
    assert counts["仙族"] == 0
    assert len(lines) == len(MOZU_ITEMS)


def test_race_from_items_xianzu():
    race, counts, _lines = mingwang._race_from_items(XIANZU_ITEMS)
    assert race == "仙族"
    assert counts["仙族"] >= 3
    assert counts["魔族"] == 0


def test_race_from_items_unknown_and_tie():
    race, counts, lines = mingwang._race_from_items(
        [("人物属性", [0, 0, 10, 10], 0.9)])
    assert race is None and counts["魔族"] == 0 and counts["仙族"] == 0
    assert lines == ["人物属性"]

    race, _counts, _lines = mingwang._race_from_items(
        [("魔族", [0, 0, 10, 10], 0.9), ("仙族", [0, 20, 10, 30], 0.9)])
    assert race is None  # 两族同票：不判定


def test_race_strip_rect():
    rect = (500, 150, 532, 328)  # 32x178 标题牌
    strip = mingwang._race_strip_rect(rect, (1378, 831))
    assert strip == (532, 130, 652, 348)
    # 边界夹取
    strip = mingwang._race_strip_rect((1300, 5, 1365, 180), (1378, 831))
    assert strip == (1365, 0, 1378, 200)


def test_at_claim_point():
    assert mingwang._at_claim_point(("大唐边境", 477, 180))
    assert mingwang._at_claim_point(("大唐边境", 470, 189))
    assert not mingwang._at_claim_point(("大唐边境", 460, 180))
    assert not mingwang._at_claim_point(("长安", 477, 180))
    assert not mingwang._at_claim_point(None)


def test_find_attr_rect_skips_button_area():
    """标题牌与打开按钮同图形时，优先取面板内候选而非按钮区域。"""
    tpl = cv2.imread(R.img(mingwang.RENWUSHUXING_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261112)
    # 按钮区域候选（中心对准 ATTR_PANEL_BTN_POS）与面板内候选
    frame[0:th, 1214:1214 + tw] = tpl
    frame[300:300 + th, 520:520 + tw] = tpl
    rect = mingwang._find_attr_rect(frame)
    assert rect == (520, 300, 520 + tw, 300 + th)


def test_match_npc_band_and_full_fallback():
    frame = _paste_bgr(mingwang.CLAIM_NPC_ICON, x=450, y=300)
    pt, score, _full = mingwang._match_npc(frame, 0.0)
    assert pt is not None and score >= mingwang.CLAIM_NPC_THRESHOLD
    # 无 NPC 的噪声帧：条带 + 全帧都没有命中
    pt, score, _full = mingwang._match_npc(_noise_bgr(seed=7), 0.0)
    assert pt is None and score < mingwang.CLAIM_NPC_THRESHOLD


def test_detect_race_uses_title_right_strip_and_closes(monkeypatch):
    plaque = cv2.imread(R.img(mingwang.RENWUSHUXING_ICON), cv2.IMREAD_COLOR)
    frame = _noise_bgr(seed=20261110)
    ph, pw = plaque.shape[:2]
    frame[150:150 + ph, 500:500 + pw] = plaque
    win = _FakeWin([_pil(frame)])

    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    ocr_regions = []

    def fake_ocr_region(img, region):
        ocr_regions.append(region)
        return MOZU_ITEMS, np.asarray(img)

    monkeypatch.setattr(mingwang.game_map, "ocr_region", fake_ocr_region)
    closed = []
    monkeypatch.setattr(
        mingwang, "_close_attr_panel",
        lambda w, r=None: closed.append(r) or (True, "面板已关闭"))

    race, info = mingwang.detect_race(win)
    assert race == "魔族"
    assert "魔族" in info
    # 面板已在：不点打开按钮（clicks 为空）
    assert clicks == []
    # OCR 区域 = 标题牌右侧 120px 条带（上下扩 20px）
    assert ocr_regions == [(532, 130, 652, 348)]
    # 识别后尝试关闭面板（传入匹配框）
    assert closed and closed[0] == (500, 150, 532, 328)


def test_detect_race_unknown_returns_detail(monkeypatch):
    plaque = cv2.imread(R.img(mingwang.RENWUSHUXING_ICON), cv2.IMREAD_COLOR)
    frame = _noise_bgr(seed=20261111)
    ph, pw = plaque.shape[:2]
    frame[150:150 + ph, 500:500 + pw] = plaque
    win = _FakeWin([_pil(frame)])

    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary": None)
    monkeypatch.setattr(
        mingwang.game_map, "ocr_region",
        lambda img, region: ([("人物属性", [540, 160, 610, 178], 0.95)],
                             np.asarray(img)))
    # 2x 放大兜底：不打真实 OCR 引擎，返回空结果
    monkeypatch.setattr(
        mingwang.game_map, "ocr_image",
        lambda img: ([], np.asarray(img)))
    monkeypatch.setattr(mingwang, "_close_attr_panel",
                        lambda w, r=None: (True, "面板已关闭"))

    race, info = mingwang.detect_race(win)
    assert race is None
    assert "种族未识别" in info and "人物属性" in info


def test_verify_claim_flow(monkeypatch):
    """诊断验证：魔族走领取入口，仙族判定后直接提示待补充。"""
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("魔族", "种族 魔族"))
    called = []

    def fake_open_panel(w):
        called.append(True)
        return True, "已打开山河名望任务领取面板"

    monkeypatch.setattr(mingwang, "_open_claim_panel", fake_open_panel)
    monkeypatch.setattr(mingwang, "_save_cached_race", lambda w, r: None)
    ok, info = mingwang.verify_claim_flow(object())
    assert ok and called and "领取面板" in info

    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("仙族", "种族 仙族"))
    called.clear()
    ok, info = mingwang.verify_claim_flow(object())
    assert not ok and "待补充" in info and not called


def test_run_cycle_resumes_existing_task(monkeypatch):
    """启动/重试时已有追踪任务：直接续跑跟踪到完成，不进入领取链路。"""
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: True)
    monkeypatch.setattr(mingwang, "_resume_tracking",
                        lambda w: (True, "任务已从任务追踪中消失"))
    monkeypatch.setattr(mingwang, "_open_claim_panel",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("已有任务不应走领取")))
    status, info = mingwang.run_cycle(object())
    assert status == "done" and "消失" in info


def test_run_loop_startup_resume(monkeypatch):
    """run_loop 启动时已有任务：跳过种族判定与领取直接续跑并计次结束。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 1})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang.app_utils, "task_progress_add",
                        lambda w, m, d: None)
    monkeypatch.setattr(mingwang.debug_overlay, "set_progress", lambda t: None)
    monkeypatch.setattr(mingwang.debug_overlay, "record_round", lambda: None)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: None)
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: True)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w: ("done", "任务已从任务追踪中消失"))
    monkeypatch.setattr(mingwang, "_detect_race_with_retries",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("续跑不应判种族")))
    info = mingwang.run_loop(object())
    assert "达到次数上限" in info


def test_race_cache_roundtrip(monkeypatch):
    """种族缓存：当天有效、跨天失效（角色可转种族）、脏值当无缓存。"""
    import time
    store = {}
    monkeypatch.setattr(mingwang.app_utils, "char_state_set",
                        lambda key, win, value: store.__setitem__(key, value))
    monkeypatch.setattr(mingwang.app_utils, "char_state_get",
                        lambda key, win, default=None: store.get(key, default))
    win = object()
    assert mingwang._load_cached_race(win) is None
    mingwang._save_cached_race(win, "魔族")
    assert store[mingwang.RACE_CACHE_KEY]["race"] == "魔族"
    assert mingwang._load_cached_race(win) == "魔族"
    # 跨天缓存失效 → 重判（角色可能已转种族）
    store[mingwang.RACE_CACHE_KEY] = {"date": "2000-01-01", "race": "魔族"}
    assert mingwang._load_cached_race(win) is None
    # 旧格式/未知种族按无缓存处理
    store[mingwang.RACE_CACHE_KEY] = "魔族"
    assert mingwang._load_cached_race(win) is None
    store[mingwang.RACE_CACHE_KEY] = {"date": time.strftime("%Y-%m-%d"),
                                      "race": "人类"}
    assert mingwang._load_cached_race(win) is None


def test_run_loop_uses_cached_race(monkeypatch):
    """已缓存种族时领取不再重开人物属性面板。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 1})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang.app_utils, "task_progress_add",
                        lambda w, m, d: None)
    monkeypatch.setattr(mingwang.debug_overlay, "set_progress", lambda t: None)
    monkeypatch.setattr(mingwang.debug_overlay, "record_round", lambda: None)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: "魔族")
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w: ("done", "任务已从任务追踪中消失"))
    monkeypatch.setattr(mingwang, "_detect_race_with_retries",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("有缓存不应重判种族")))
    info = mingwang.run_loop(object())
    assert "达到次数上限" in info


def test_start_track_waits_and_follows(monkeypatch):
    """追踪面板出现后交给 follow_task 跟踪至完成。"""
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w, img=None: True)
    monkeypatch.setattr(mingwang, "_follow_task",
                        lambda w: (True, "任务已从任务追踪中消失"))
    ok, info = mingwang._start_track(object())
    assert ok and "消失" in info


def test_handle_dialog_options(monkeypatch):
    """对话表：命中「黑水河深，不如蛟魔之心。」→ 点击「逆天魔大将派我前来」。"""
    clicked = []
    monkeypatch.setattr(
        mingwang, "_click_green_option_once",
        lambda w, items, img_np, text, prefix="":
        clicked.append(text) or True)
    items = [("黑水河深，不如蛟魔之心。", [500, 300, 700, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is True
    assert clicked == ["逆天魔大将派我前来"]


def test_handle_dialog_options_no_match(monkeypatch):
    monkeypatch.setattr(
        mingwang, "_click_green_option_once",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应点击")))
    items = [("无关对话文案", [500, 300, 700, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is False


def test_follow_task_uses_mingwang_dialog_map(monkeypatch):
    """复用 follow_task 时传入本模块对话表与关闭玄枢专属检测。"""
    calls = {}

    def fake(win, title, **kw):
        calls.update({"title": title, **kw})
        return True, "任务已从任务追踪中消失"

    monkeypatch.setattr(mingwang, "follow_task", fake)
    ok, _info = mingwang._follow_task(object())
    assert ok
    assert calls["title"] == mingwang.TASK_TITLE
    assert calls["prefix"] == "名望"
    assert calls["extra_dialog_handler"] is mingwang._handle_dialog_options
    assert calls["skip_texts"] == {}
    assert calls["chuyijuebao_check"] is False
    assert calls["story_known_texts"] is mingwang.DIALOG_OPTION_MAP


def test_run_loop_unsupported_race_stops(monkeypatch):
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 5})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: None)
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("仙族", "种族 仙族"))
    monkeypatch.setattr(mingwang, "_save_cached_race", lambda w, r: None)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("仙族不应进入领取流程")))
    info = mingwang.run_loop(object())
    assert "仙族领取流程待补充" in info


# ---- 山河名望任务栏（2026-11-10 实机截图标定的 6 卡 OCR 结果） ----
LIVE_ANCHOR = (392, 252, 425, 287)  # maodian 匹配框（中心 408,269，33x35）

LIVE_CARD_ITEMS = {
    (0, 0): [("高級", [399, 298, 422, 338], 0.80),
             ("蛟魔复仇", [458, 307, 537, 333], 1.0),
             ("火云戈壁的风沙里，徒留", [404, 347, 575, 369], 1.0),
             ("永世难解的仇怨。", [404, 363, 524, 386], 1.0),
             ("接取", [462, 409, 514, 437], 1.0)],
    (0, 1): [("初级", [599, 299, 623, 337], 1.0),
             ("朱雀火羽", [662, 308, 735, 332], 1.0),
             ("神鸟自天火中降生，仅是", [605, 347, 776, 369], 1.0),
             ("一片羽毛便可焚天灭地。", [606, 363, 770, 385], 1.0),
             ("接取", [663, 409, 715, 437], 1.0)],
    (0, 2): [("初级", [802, 299, 824, 338], 0.98),
             ("溯梦清灵", [862, 308, 937, 332], 0.98),
             ("你寻寻觅觅的从前，只在", [807, 347, 977, 369], 1.0),
             ("睡梦中清晰，醒来时便", [806, 364, 977, 386], 1.0),
             ("已散如云烟。", [807, 380, 896, 404], 1.0),
             ("接取", [864, 410, 915, 435], 1.0)],
    (1, 0): [("高級", [399, 459, 422, 499], 0.77),
             ("沙海迷踪", [460, 470, 535, 493], 1.0),
             ("陈旧卷轴上的浊息，掩盖", [404, 508, 574, 530], 1.0),
             ("看沧海桑田间失落的族群", [405, 525, 574, 547], 0.96),
             ("接取", [463, 571, 513, 596], 1.0)],
    (1, 1): [("心", [641, 459, 641, 498], 0.64),
             ("初级", [597, 460, 624, 498], 0.99),
             ("瑶池论道", [660, 470, 737, 491], 1.0),
             ("辨玄论道，其中关节不在", [605, 508, 776, 530], 1.0),
             ("辩，而在于道。", [605, 525, 710, 547], 1.0),
             ("接取", [663, 570, 714, 598], 1.0)],
    (1, 2): [("任务栏未解锁", [844, 515, 937, 535], 1.0),
             ("完成本族高级进阶任务", [816, 541, 968, 560], 1.0)],
}


def _live_rects():
    return {(r, c): rect for r, c, rect in mingwang._card_rects(LIVE_ANCHOR)}


def test_card_grid_geometry():
    rects = _live_rects()
    assert rects[(0, 0)] == (392, 290, 582, 444)
    assert rects[(0, 2)] == (794, 290, 984, 444)
    assert rects[(1, 0)] == (392, 451, 582, 605)
    assert rects[(1, 2)] == (794, 451, 984, 605)


def test_card_level_and_title_from_live_ocr():
    rects = _live_rects()
    assert mingwang._card_level(LIVE_CARD_ITEMS[(0, 0)], rects[(0, 0)]) == "高级"
    assert mingwang._card_title(LIVE_CARD_ITEMS[(0, 0)], rects[(0, 0)]) == "蛟魔复仇"
    assert mingwang._card_level(LIVE_CARD_ITEMS[(0, 1)], rects[(0, 1)]) == "初级"
    assert mingwang._card_title(LIVE_CARD_ITEMS[(0, 1)], rects[(0, 1)]) == "朱雀火羽"
    assert mingwang._card_level(LIVE_CARD_ITEMS[(1, 0)], rects[(1, 0)]) == "高级"
    assert mingwang._card_title(LIVE_CARD_ITEMS[(1, 0)], rects[(1, 0)]) == "沙海迷踪"
    # 零宽 OCR 碎片「心」不得并入标题
    assert mingwang._card_title(LIVE_CARD_ITEMS[(1, 1)], rects[(1, 1)]) == "瑶池论道"
    # 解锁卡描述「完成本族高级进阶任务」含「高级」：级别必须判为未知
    assert mingwang._card_level(LIVE_CARD_ITEMS[(1, 2)], rects[(1, 2)]) is None
    assert mingwang._card_title(LIVE_CARD_ITEMS[(1, 2)], rects[(1, 2)]) == ""


def _live_frame(with_button=True, skip_buttons=()):
    """实机布局合成帧：贴入锚点与 5 个接取按钮。"""
    frame = _noise_bgr(seed=20261113)
    tpl = cv2.imread(R.img(mingwang.PANEL_MAODIAN_ICON), cv2.IMREAD_COLOR)
    l, t = LIVE_ANCHOR[0], LIVE_ANCHOR[1]
    frame[t:t + tpl.shape[0], l:l + tpl.shape[1]] = tpl
    btn = cv2.imread(R.img(mingwang.PANEL_JIEQU_ICON), cv2.IMREAD_COLOR)
    bh, bw = btn.shape[:2]
    for cx, cy in ((487, 423), (688, 423), (889, 423), (487, 584), (688, 584)):
        if not with_button and (cx, cy) == (487, 423):
            continue
        if (cx, cy) in skip_buttons:
            continue
        frame[cy - bh // 2:cy - bh // 2 + bh,
              cx - bw // 2:cx - bw // 2 + bw] = btn
    return frame


def _patch_live_cards(monkeypatch):
    rects = _live_rects()

    def fake_ocr(img, region):
        for key, rect in rects.items():
            if tuple(region) == tuple(rect):
                return LIVE_CARD_ITEMS[key], np.asarray(img)
        return [], np.asarray(img)

    monkeypatch.setattr(mingwang.game_map, "ocr_region", fake_ocr)
    monkeypatch.setattr(mingwang.task_tracker, "ocr_track",
                        lambda img: ([], np.asarray(img)))


def test_claim_panel_task_claims_jiaomo(monkeypatch):
    """实机布局合成帧：识别高级「蛟魔复仇」并点击其接取按钮。"""
    _patch_live_cards(monkeypatch)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    # 首次扫描帧有接取按钮；确认帧按钮已消失（领取成功信号）
    win = _FakeWin([_pil(_live_frame(True)), _pil(_live_frame(False))])
    status, info = mingwang.claim_panel_task(win)
    assert status == "claimed"
    assert "蛟魔复仇" in info and "接取按钮已消失" in info
    assert clicks == [(487, 423, "primary")]


def test_dismiss_claim_dialog_uses_frame_gate(monkeypatch):
    """领取后对话框：模板门控命中 → 调 clear_blocking_dialog 点屏关闭。"""
    win = _FakeWin([_pil(_noise_bgr(seed=1))])
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.99)
    called = []
    monkeypatch.setattr(mingwang, "clear_blocking_dialog",
                        lambda w, p="": called.append(p) or (True, True))
    ok, info = mingwang._dismiss_claim_dialog(win, timeout=1.0)
    assert ok and called == ["名望"] and "关闭" in info


def test_dismiss_claim_dialog_no_dialog(monkeypatch):
    win = _FakeWin([_pil(_noise_bgr(seed=2))])
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.1)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._dismiss_claim_dialog(win, timeout=0.0)
    assert not ok and "未检测到" in info


def test_task_in_tracker_requires_tracker_header(monkeypatch):
    """追踪面板标题条未命中时不得把山河名望面板竖牌当任务（假阳性防护）。"""
    win = _FakeWin([_pil(_noise_bgr(seed=3))])
    monkeypatch.setattr(mingwang.task_tracker, "track_content_region",
                        lambda img_np: None)
    monkeypatch.setattr(mingwang.task_tracker, "has_task_in",
                        lambda *a, **k: True)
    assert mingwang._task_in_tracker(win) is False
    monkeypatch.setattr(mingwang.task_tracker, "track_content_region",
                        lambda img_np: (0, 227, 300, 600))
    monkeypatch.setattr(mingwang.task_tracker, "ocr_track",
                        lambda img: ([], np.asarray(img)))
    assert mingwang._task_in_tracker(win) is True


def test_post_claim_flow_order(monkeypatch):
    order = []
    monkeypatch.setattr(mingwang, "_dismiss_claim_dialog",
                        lambda w: order.append("dialog") or (True, "对话框已关闭"))
    monkeypatch.setattr(mingwang, "_close_shanhe_panel",
                        lambda w: order.append("close") or (True, "面板未打开"))
    monkeypatch.setattr(mingwang, "_start_track",
                        lambda w: order.append("track") or (True, "任务已从任务追踪中消失"))
    ok, info = mingwang._post_claim_flow(object())
    assert ok and order == ["dialog", "close", "track"]
    assert "对话框已关闭" in info and "消失" in info


def test_close_shanhe_panel_not_open(monkeypatch):
    win = _FakeWin([_pil(_noise_bgr(seed=4))])
    monkeypatch.setattr(mingwang.game_map, "best_match_np",
                        lambda frame, icon: (0.1, (0, 0)))
    ok, info = mingwang._close_shanhe_panel(win)
    assert ok and "未打开" in info


def test_close_shanhe_panel_right_click(monkeypatch):
    """面板仍在 → 右键标题牌关闭，消失即成功。"""
    seq = [(0.99, (350, 379)), (0.1, (0, 0))]
    monkeypatch.setattr(mingwang.game_map, "best_match_np",
                        lambda frame, icon: seq.pop(0) if seq else (0.1, (0, 0)))
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    win = _FakeWin([_pil(_noise_bgr(seed=5))])
    ok, info = mingwang._close_shanhe_panel(win)
    assert ok and clicks == [(350, 379, "secondary")] and "已关闭" in info


def _patch_cards_with(monkeypatch, overrides):
    """按实机布局返回卡片 OCR；overrides: {(row,col): items} 覆盖指定卡。"""
    rects = _live_rects()

    def fake_ocr(img, region):
        for key, rect in rects.items():
            if tuple(region) == tuple(rect):
                return overrides.get(key, LIVE_CARD_ITEMS[key]), np.asarray(img)
        return [], np.asarray(img)

    monkeypatch.setattr(mingwang.game_map, "ocr_region", fake_ocr)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)


def test_claim_panel_task_not_found(monkeypatch):
    """任务栏无已实现任务（两个高级卡都换成其它标题）→ not_found，不点击。"""
    _patch_cards_with(monkeypatch, {
        (0, 0): [("高級", [399, 298, 422, 338], 0.9),
                 ("黑水蛟王", [458, 307, 537, 333], 1.0),
                 ("接取", [462, 409, 514, 437], 1.0)],
        (1, 0): [("高級", [399, 459, 422, 499], 0.9),
                 ("黑水精怪", [460, 470, 535, 493], 1.0),
                 ("接取", [463, 571, 513, 596], 1.0)],
    })
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        (_ for _ in ()).throw(AssertionError("不应点击")))
    win = _FakeWin([_pil(_live_frame(True))])
    status, info = mingwang.claim_panel_task(win)
    assert status == "not_found" and "蛟魔复仇" in info and "沙海迷踪" in info


def test_claim_panel_task_claims_shahai(monkeypatch):
    """蛟魔复仇不可用时领取另一已实现任务「沙海迷踪」。"""
    _patch_cards_with(monkeypatch, {
        (0, 0): [("高級", [399, 298, 422, 338], 0.9),
                 ("黑水蛟王", [458, 307, 537, 333], 1.0),
                 ("接取", [462, 409, 514, 437], 1.0)],
    })
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    win = _FakeWin([
        _pil(_live_frame(True)),
        _pil(_live_frame(True, skip_buttons=((487, 584),))),
    ])
    status, info = mingwang.claim_panel_task(win)
    assert status == "claimed" and "沙海迷踪" in info
    # 第二行第一列卡片（沙海迷踪）的接取按钮中心
    assert clicks == [(487, 584, "primary")]


# ---- 沙海迷踪（高级任务）：入口对话 → 「开始」按钮 → 进入战斗 ----
def test_shahai_option_arms_start_check(monkeypatch):
    """入口选项点击成功后 arm「开始」按钮检测窗口。"""
    monkeypatch.setattr(mingwang, "_shahai_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "_click_green_option_once",
                        lambda *a, **k: True)
    items = [(mingwang.SHAHAI_DIALOG_TEXT, [500, 300, 700, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is True
    assert mingwang._shahai_armed_at > 0


def test_shahai_start_clicked_when_armed(monkeypatch):
    """armed 窗口内出现「开始」按钮 → 点击并解除 armed。"""
    monkeypatch.setattr(mingwang, "_shahai_armed_at", time.time())
    tpl = cv2.imread(R.img(mingwang.SHAHAI_START_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261114)
    frame[300:300 + th, 600:600 + tw] = tpl
    win = _FakeWin([_pil(frame)])
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    ok = mingwang._maybe_click_shahai_start(win)
    assert ok and clicks == [(600 + tw // 2, 300 + th // 2, "primary")]
    assert mingwang._shahai_armed_at == 0.0


def test_shahai_start_arm_timeout(monkeypatch):
    """armed 窗口超时 → 不再检测/点击，窗口关闭。"""
    monkeypatch.setattr(mingwang, "_shahai_armed_at", time.time() - 999)
    ok = mingwang._maybe_click_shahai_start(object())
    assert not ok and mingwang._shahai_armed_at == 0.0


def test_shahai_start_not_clicked_when_not_armed(monkeypatch):
    monkeypatch.setattr(mingwang, "_shahai_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未 arm 不应点击")))
    assert mingwang._maybe_click_shahai_start(object()) is False


def test_handle_dialog_options_second_candidate(monkeypatch):
    """候选选项：第一个不存在时尝试第二个（如 <山河名望>）。"""
    monkeypatch.setattr(mingwang, "_shahai_armed_at", 0.0)
    calls = []
    monkeypatch.setattr(
        mingwang, "_click_green_option_once",
        lambda w, items, img_np, text, prefix="":
        calls.append(text) or (text == "<山河名望>"))
    items = [("黑水河深，不如蛟魔之心。", [500, 300, 700, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is True
    assert calls == ["逆天魔大将派我前来", "<山河名望>"]


def test_dialog_option_map_config():
    """配置校验：文案非空；值必须是字符串或非空字符串元组。"""
    for dtxt, opts in mingwang.DIALOG_OPTION_MAP.items():
        assert isinstance(dtxt, str) and dtxt.strip()
        vals = (opts,) if isinstance(opts, str) else opts
        assert isinstance(vals, tuple) and vals
        assert all(isinstance(o, str) and o.strip() for o in vals)


# ---- 蛟魔探秘：钓鱼小游戏（与玄枢钓鱼共用 pochufengyin 闭环） ----
def test_pick_card_priority_over_row_order():
    """级别优先级高于卡片顺序：中级卡在前仍先选高级已实现任务。"""
    cards = [
        {"row": 0, "col": 0, "rect": (0, 0, 190, 154),
         "level": "中级", "title": "蛟魔探秘"},
        {"row": 1, "col": 0, "rect": (0, 161, 190, 315),
         "level": "高级", "title": "蛟魔复仇"},
    ]
    _card, task = mingwang._pick_card(cards)
    assert task["title"] == "蛟魔复仇"


def test_pick_card_level_none_matches_any_level(monkeypatch):
    """任务定义级别为 None 时按卡片实际级别匹配（不限级别）。"""
    monkeypatch.setattr(mingwang, "CLAIM_TASKS",
                        ({"title": "蛟魔探秘", "level": None},))
    cards = [{"row": 0, "col": 0, "rect": (0, 0, 190, 154),
              "level": "中级", "title": "蛟魔探秘"}]
    _card, task = mingwang._pick_card(cards)
    assert task["title"] == "蛟魔探秘"


def test_mingwang_diaoyu_option_arms_check(monkeypatch):
    """点击「请将鱼竿借我一用！」后 arm 小游戏窗口检测。"""
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "_shahai_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "_click_green_option_once",
                        lambda *a, **k: True)
    items = [("老头子我最喜欢的，就是在这里钓鱼了~",
              [500, 300, 800, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is True
    assert mingwang._mingwang_diaoyu_armed_at > 0


def test_mingwang_diaoyu_window_runs_minigame(monkeypatch):
    """armed 窗口内命中标题 → 调共享点击闭环（同破除封印）。"""
    import time as _t
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", _t.time())
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_last_check", 0.0)
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    tpl = cv2.imread(R.img(mingwang.MINGWANG_DIAOYU_TITLE_ICON),
                     cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261115)
    frame[500:500 + th, 400:400 + tw] = tpl  # POCHU_REGION 内
    win = _FakeWin([_pil(frame)])
    calls = {}

    def fake_play(w, title_icon, **kw):
        calls.update({"title": title_icon, **kw})
        return True, "钓鱼小游戏完成"

    monkeypatch.setattr(mingwang._pochu_minigame, "play_pochufengyin", fake_play)
    handled = mingwang._mingwang_diaoyu_window_check(win)
    assert handled is True
    assert calls["title"] == mingwang.MINGWANG_DIAOYU_TITLE_ICON
    assert calls["prefix"] == "名望"
    # 点击模式 + 冲刺连点（单击仅推进 ~0.1 刻度，三档节奏到不了目标带）
    assert calls.get("hold", False) is False
    assert calls.get("mash") is True
    assert "action_icon" not in calls
    assert mingwang._mingwang_diaoyu_armed_at == 0.0


def test_mingwang_diaoyu_not_armed_noop(monkeypatch):
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未 arm 不应操作")))
    assert mingwang._mingwang_diaoyu_window_check(object()) is False
