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
    (mingwang.CLAIM_POINTS["魔族"]["world_icon"], 0.8),
    (mingwang.CLAIM_POINTS["仙族"]["world_icon"], 0.8),
    (mingwang.CLAIM_POINTS["魔族"]["npc_icon"], mingwang.CLAIM_NPC_THRESHOLD),
    (mingwang.CLAIM_POINTS["仙族"]["npc_icon"], mingwang.CLAIM_NPC_THRESHOLD),
    (mingwang.CLAIM_SHANHE_ICON, mingwang.CLAIM_SHANHE_THRESHOLD),
    (mingwang.SEAL_ITEM_ICON, mingwang.SEAL_ITEM_THRESHOLD),
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


def test_race_from_items_xianzu_role_name_without_xian():
    """仙族角色名不含「仙」（武尊神/云中君/玄剑娥）也必须判为仙族。"""
    for name in ("武尊神", "云中君", "玄剑娥"):
        race, counts, _lines = mingwang._race_from_items(
            [(name, [0, 0, 60, 20], 0.95)])
        assert race == "仙族", name
        assert counts["仙族"] == 1 and counts["魔族"] == 0


def test_claim_points_config():
    """种族领取点配置完整，模板文件存在可解码。"""
    assert set(mingwang.CLAIM_POINTS) == {"魔族", "仙族"}
    for race, claim in mingwang.CLAIM_POINTS.items():
        assert claim["map"] and claim["npc_name"], race
        assert len(claim["target"]) == 2
        assert len(claim["mini_click"]) == 2
        assert game_map.template_size(claim["world_icon"]) is not None, race
        assert game_map.template_size(claim["npc_icon"]) is not None, race
    assert mingwang.CLAIM_POINTS["仙族"]["map"] == "天宫"
    assert mingwang.CLAIM_POINTS["仙族"]["target"] == (185, 122)
    # 小地图点击位置为用户实机微调值（2026-11-10：800→794→821,358）
    assert mingwang.CLAIM_POINTS["仙族"]["mini_click"] == (821, 358)
    # 两族都必须坐标到达领取点（NPC 模板可见不算到达）
    assert "npc_arrival_fallback" not in mingwang.CLAIM_POINTS["仙族"]
    assert "npc_arrival_fallback" not in mingwang.CLAIM_POINTS["魔族"]


def test_race_strip_rect():
    rect = (500, 150, 532, 328)  # 32x178 标题牌
    strip = mingwang._race_strip_rect(rect, (1378, 831))
    assert strip == (532, 130, 652, 348)
    # 边界夹取
    strip = mingwang._race_strip_rect((1300, 5, 1365, 180), (1378, 831))
    assert strip == (1365, 0, 1378, 200)


def test_at_claim_point():
    mozu = mingwang.CLAIM_POINTS["魔族"]
    xian = mingwang.CLAIM_POINTS["仙族"]
    assert mingwang._at_claim_point(("大唐边境", 477, 180), mozu)
    assert mingwang._at_claim_point(("大唐边境", 470, 187), mozu)  # ±7 内
    assert not mingwang._at_claim_point(("大唐边境", 470, 189), mozu)  # y 超 7
    assert not mingwang._at_claim_point(("大唐边境", 469, 187), mozu)  # x 超 7
    assert not mingwang._at_claim_point(("大唐边境", 460, 180), mozu)
    assert not mingwang._at_claim_point(("长安", 477, 180), mozu)
    assert not mingwang._at_claim_point(None, mozu)
    # 仙族：天宫 185,122（±7 容差）
    assert mingwang._at_claim_point(("天宫", 185, 120), xian)  # 2px 差在容差内
    assert mingwang._at_claim_point(("天宫", 180, 129), xian)  # dx5 dy7
    assert not mingwang._at_claim_point(("天宫", 180, 130), xian)  # dy8
    assert not mingwang._at_claim_point(("天宫", 170, 120), xian)
    # 同坐标不同地图不互认
    assert not mingwang._at_claim_point(("大唐边境", 477, 180), xian)
    assert not mingwang._at_claim_point(("天宫", 185, 120), mozu)


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
    for race in ("魔族", "仙族"):
        claim = mingwang.CLAIM_POINTS[race]
        frame = _paste_bgr(claim["npc_icon"], x=450, y=300)
        pt, score, _full = mingwang._match_npc(frame, claim, 0.0)
        assert pt is not None and score >= mingwang.CLAIM_NPC_THRESHOLD, race
        # 无 NPC 的噪声帧：条带 + 全帧都没有命中
        pt, score, _full = mingwang._match_npc(_noise_bgr(seed=7),
                                               claim, 0.0)
        assert pt is None and score < mingwang.CLAIM_NPC_THRESHOLD, race


def test_match_npc_xianzu_no_full_frame_fallback():
    """仙族 NPC 模板与同造型玩家互匹配：条带外（画面右缘）不得兜底命中。"""
    claim = mingwang.CLAIM_POINTS["仙族"]
    assert claim["npc_full_fallback"] is False
    frame = _paste_bgr(claim["npc_icon"], x=1250, y=200)  # 条带右缘 1180 之外
    pt, score, _full = mingwang._match_npc(frame, claim, 0.0)
    assert pt is None and score < mingwang.CLAIM_NPC_THRESHOLD
    # 魔族保留原有全帧兜底（布局漂移保险）
    mozu = mingwang.CLAIM_POINTS["魔族"]
    frame2 = _paste_bgr(mozu["npc_icon"], x=1250, y=200)
    pt, score, full = mingwang._match_npc(frame2, mozu, 0.0)
    assert pt is not None and score >= mingwang.CLAIM_NPC_THRESHOLD
    assert full > 0.0


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
    """诊断验证：魔族/仙族分别走各自领取入口。"""
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    called = []

    def fake_open_panel(w, race):
        called.append(race)
        return True, "已打开山河名望任务领取面板"

    monkeypatch.setattr(mingwang, "_open_claim_panel", fake_open_panel)
    monkeypatch.setattr(mingwang, "_save_cached_race", lambda w, r: None)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("魔族", "种族 魔族"))
    ok, info = mingwang.verify_claim_flow(object())
    assert ok and called == ["魔族"] and "领取面板" in info

    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("仙族", "种族 仙族"))
    called.clear()
    ok, info = mingwang.verify_claim_flow(object())
    assert ok and called == ["仙族"] and "领取面板" in info


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
                        lambda w, race=None: ("done", "任务已从任务追踪中消失"))
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
                        lambda w, race=None: ("done", "任务已从任务追踪中消失"))
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
    """复用 follow_task 时传入本模块对话表/跳过表与关闭玄枢专属检测。"""
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
    # 无选项多屏剧情台词连点表（不再用空表禁用玄枢文案）
    assert calls["skip_texts"] is mingwang.DIALOG_SKIP_TEXTS
    assert calls["chuyijuebao_check"] is False
    assert calls["story_known_texts"] is mingwang.DIALOG_OPTION_MAP


def test_dialog_skip_texts_config():
    """跳过表配置：文案非空、点击次数为正整数；与选项表互不冲突。"""
    assert mingwang.DIALOG_SKIP_TEXTS
    for text, clicks in mingwang.DIALOG_SKIP_TEXTS.items():
        assert isinstance(text, str) and text.strip()
        assert isinstance(clicks, int) and clicks > 0
        assert text not in mingwang.DIALOG_OPTION_MAP


def test_dialog_skip_texts_matches_live_mingyue_dialog():
    """实机 OCR 原样（说话人名 + 两行换行拆分）必须命中跳过表。"""
    items = [
        ("明月", [472, 348, 511, 372], 1.0),
        ("我倒是担心风暴的封印，最近也有消息说有百姓被风所伤，"
         "但镇元子师父布", [435, 381, 940, 402], 1.0),
        ("下的风暴封印明明不会伤及普通人的", [437, 397, 685, 417], 1.0),
    ]
    assert mingwang._dialog_text_present(items, "我倒是担心风暴的封印")


def test_run_loop_unsupported_race_stops(monkeypatch):
    """未实现领取点的种族（人/鬼/龙/神巫）判定后停止，不盲目寻路。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 5})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: None)
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("人类", "种族 人类"))
    monkeypatch.setattr(mingwang, "_save_cached_race", lambda w, r: None)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w, race=None: (_ for _ in ()).throw(
                            AssertionError("未实现种族不应进入领取流程")))
    info = mingwang.run_loop(object())
    assert "人类领取流程待补充" in info


def test_run_loop_xianzu_goes_claim(monkeypatch):
    """仙族已实现：判定后进入领取流程（不再提前退出）。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 1})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang.app_utils, "task_progress_add",
                        lambda w, m, d: None)
    monkeypatch.setattr(mingwang.debug_overlay, "set_progress", lambda t: None)
    monkeypatch.setattr(mingwang.debug_overlay, "record_round", lambda: None)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: None)
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: ("仙族", "种族 仙族"))
    seen = []
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w, race=None:
                        seen.append(race) or ("done", "任务完成"))
    info = mingwang.run_loop(object())
    assert seen == ["仙族"] and "达到次数上限" in info


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
    """领取后对话框：无已知选项 → 调 clear_blocking_dialog 点屏关闭。"""
    win = _FakeWin([_pil(_noise_bgr(seed=1))])
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(mingwang.game_map, "ocr_region",
                        lambda img, region: ([], np.asarray(img)))
    called = []
    monkeypatch.setattr(mingwang, "clear_blocking_dialog",
                        lambda w, p="": called.append(p) or (True, True))
    ok, info = mingwang._dismiss_claim_dialog(win, timeout=1.0)
    assert ok and called == ["名望"] and "关闭" in info


def test_dismiss_claim_dialog_clicks_known_option(monkeypatch):
    """领取后对话框是选项对话（五庄封印）→ 先点选项，不点屏幕中间。"""
    win = _FakeWin([_pil(_noise_bgr(seed=12))])
    scores = [0.99, 0.1]
    monkeypatch.setattr(mingwang, "dialog_frame_score",
                        lambda img: scores.pop(0) if scores else 0.1)
    monkeypatch.setattr(mingwang.game_map, "ocr_region",
                        lambda img, region: ([], np.asarray(img)))
    clicks = []
    monkeypatch.setattr(mingwang, "_click_known_dialog_option",
                        lambda w, items, img_np:
                        clicks.append(True) or len(clicks) == 1)
    monkeypatch.setattr(mingwang, "clear_blocking_dialog",
                        lambda w, p="": (_ for _ in ()).throw(
                            AssertionError("选项对话不应点屏幕中间")))
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._dismiss_claim_dialog(win, timeout=5.0)
    assert ok and clicks == [True] and "选项已处理" in info


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


def test_pick_card_wuzhuang_seal_high_level():
    """仙族「五庄封印」为高级卡片（用户实机确认）；初级卡不匹配。"""
    cards = [{"row": 0, "col": 0, "rect": (0, 0, 190, 154),
              "level": "高级", "title": "五庄封印"}]
    _card, task = mingwang._pick_card(cards)
    assert task["title"] == "五庄封印"
    low = [{"row": 0, "col": 0, "rect": (0, 0, 190, 154),
            "level": "初级", "title": "五庄封印"}]
    _card, task = mingwang._pick_card(low)
    assert task is None


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


# ---- 五庄封印执行：追踪双链接 → 坐标稳定到达 → 任务储物格用唤风符 ----
SEAL_ITEMS_LIVE = [
    ("[日常]五庄封印[山河名望]", [18, 230, 205, 253], 0.97),
    ("在五庄观找到火藏之位和木藏", [22, 251, 222, 271], 1.0),
    ("之位的风暴封印处，使用唤风", [23, 267, 222, 288], 1.0),
    ("符补充封印灵力。", [21, 282, 141, 306], 0.98),
]


def _seal_frame_with_underlines():
    """实机布局：火藏/木藏下划线（亮绿横段）画在对应文字下方。"""
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    for x0, x1, y in ((114, 173, 270), (189, 218, 270), (24, 53, 287)):
        frame[y:y + 2, x0:x1 + 1] = (0, 255, 0)  # RGB 亮绿
    return frame


def test_seal_link_point_prefers_underline():
    frame = _seal_frame_with_underlines()
    fire = mingwang._seal_link_point(SEAL_ITEMS_LIVE, frame, "火藏")
    wood = mingwang._seal_link_point(SEAL_ITEMS_LIVE, frame, "木藏")
    assert fire is not None and abs(fire[0] - 143) <= 10, fire
    assert wood is not None and abs(wood[0] - 203) <= 10, wood
    assert abs(fire[1] - 268) <= 3 and abs(wood[1] - 268) <= 3


def test_seal_green_picker_follows_current_link(monkeypatch):
    frame = _seal_frame_with_underlines()
    monkeypatch.setattr(mingwang, "_seal_link_idx", 0)
    assert abs(mingwang._seal_green_picker(SEAL_ITEMS_LIVE, frame)[0] - 143) <= 10
    monkeypatch.setattr(mingwang, "_seal_link_idx", 1)
    assert abs(mingwang._seal_green_picker(SEAL_ITEMS_LIVE, frame)[0] - 203) <= 10


def test_seal_wait_arrival_uses_item_after_stable(monkeypatch):
    """位置连续稳定 3 轮 → 使用唤风符并进入等结果阶段（同显形镜）。"""
    mingwang._reset_seal_state()
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 100, 100))
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    used = []
    monkeypatch.setattr(mingwang, "_use_huanfengfu",
                        lambda w: used.append(True) or (True, "已使用唤风符"))
    frame = _seal_frame_red_green(fire_red=False)  # 两个链接均绿
    win = object()
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is False
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is False
    assert not used and mingwang._seal_phase == 0
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is True
    assert used == [True] and mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def _seal_frame_red_green(fire_red=True, wood_red=False):
    """火藏/木藏链接着色帧（游戏纯色：亮绿 (0,255,0) / 红 (255,0,0)）。"""
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    frame[248:274, 110:178] = (255, 0, 0) if fire_red else (0, 255, 0)
    frame[248:274, 185:222] = (255, 0, 0) if wood_red else (0, 255, 0)
    return frame


def test_seal_link_color_red_green():
    """实机颜色判读：火藏红 / 木藏绿。"""
    frame = _seal_frame_red_green()
    assert mingwang._seal_link_color(SEAL_ITEMS_LIVE, frame, "火藏") == "red"
    assert mingwang._seal_link_color(SEAL_ITEMS_LIVE, frame, "木藏") == "green"


def test_seal_link_ocr_alias_water_for_wood():
    """OCR 把「木藏」读成「水藏」时仍能定位/判色（2026-11-10 实机）。"""
    items = [
        ("[日常]五庄封印[山河名望]", [18, 230, 205, 253], 0.97),
        ("在五庄观找到火藏之位和水藏", [22, 251, 222, 271], 1.0),
        ("之位的风暴封印处，使用唤风", [23, 267, 222, 288], 1.0),
    ]
    frame = _seal_frame_red_green(fire_red=True, wood_red=False)
    kws = mingwang._seal_link_keywords(1)
    assert "水藏" in kws and "木藏" in kws
    assert mingwang._seal_link_color(items, frame, kws) == "green"
    pt = mingwang._seal_link_point(items, frame, kws)
    assert pt is not None and abs(pt[0] - 203) <= 10


def test_seal_red_link_switches_to_wood(monkeypatch):
    """火藏用符后链接变红 → 点木藏之位重新寻路（状态推进）。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_link_idx = 0
    mingwang._seal_use_at = time.time() - 999
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 100, 100))
    clicked = []
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda w, items, img_np, link_idx=None:
                        clicked.append(link_idx) or True)
    frame = _seal_frame_red_green(fire_red=True)
    assert mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame) is True
    assert clicked == [1]
    assert mingwang._seal_link_idx == 1
    assert mingwang._seal_phase == 0 and mingwang._seal_need_move is True
    mingwang._reset_seal_state()


def test_seal_resume_skips_red_fire_link(monkeypatch):
    """启动续跑/火藏已完成（红）：phase 0 自动前进到木藏并点击。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 0
    mingwang._seal_link_idx = 0
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 100, 100))
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    clicked = []
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda w, items, img_np, link_idx=None:
                        clicked.append(link_idx) or True)
    frame = _seal_frame_red_green(fire_red=True, wood_red=False)
    assert mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame) is True
    assert clicked == [1] and mingwang._seal_link_idx == 1
    assert mingwang._seal_phase == 0 and mingwang._seal_need_move is True
    mingwang._reset_seal_state()


def test_seal_green_link_waits(monkeypatch):
    """火藏仍绿且仍在五庄观 → 等待（不切换、不点链接）。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_link_idx = 0
    # 已过用符宽限但未到重试间隔（验证「绿且同图 → 纯等待」分支）
    mingwang._seal_use_at = time.time() - 5.0
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 100, 100))
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未变红不应点木藏")))
    frame = _seal_frame_red_green(fire_red=False)
    assert mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame) is False
    assert mingwang._seal_link_idx == 0 and mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def test_seal_last_link_red_waits_for_task_done(monkeypatch):
    """木藏也已变红 → 只等任务消失（follow 判定），不再点链接。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_link_idx = 1
    mingwang._seal_use_at = time.time() - 999
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 100, 100))
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("最后一个链接不应再点击")))
    frame = _seal_frame_red_green(fire_red=True, wood_red=True)
    assert mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame) is False
    assert mingwang._seal_both_warned is True
    assert mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def test_seal_position_left_map_also_switches(monkeypatch):
    """坐标离开五庄观（用符传送/失败）同样视为本点完成 → 点木藏。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_link_idx = 0
    mingwang._seal_use_at = time.time() - 999
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("长安", 10, 20))
    clicked = []
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda w, items, img_np, link_idx=None:
                        clicked.append(link_idx) or True)
    frame = _seal_frame_red_green(fire_red=False)
    assert mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame) is True
    assert clicked == [1]
    mingwang._reset_seal_state()


def test_seal_no_battle_logic(monkeypatch):
    """五庄封印不做战斗判定：战斗中若追踪文案缺失，状态机不推进。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    monkeypatch.setattr(mingwang, "is_in_battle_frame",
                        lambda bgr: (_ for _ in ()).throw(
                            AssertionError("不应做战斗判定")))
    monkeypatch.setattr(mingwang, "_seal_frame", lambda w, items: (None, None))
    battle_items = [("", [10, 230, 205, 253], 0.99)]
    assert mingwang._handle_wuzhuang_seal(object(), battle_items, None) is False
    assert mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def test_seal_state_resets_on_non_seal_task(monkeypatch):
    """非五庄任务跟踪文案：连续 5 轮后复位状态，且不做战斗判定。"""
    mingwang._reset_seal_state()
    monkeypatch.setattr(mingwang, "is_in_battle",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("非五庄任务不应判战斗")))
    items = [("蛟魔复仇[山河名望]", [10, 230, 205, 253], 0.99)]
    for _ in range(5):
        assert mingwang._handle_wuzhuang_seal(object(), items, None) is False
    assert mingwang._seal_missing == 0  # 已复位


def test_use_huanfengfu_clicks_item(monkeypatch):
    """任务储物格（bag.open_task_box 通用能力）命中唤风符 → 右键使用。"""
    mingwang._reset_seal_state()
    win = _FakeWin([_pil(_noise_bgr(seed=11))])
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda frame: 0.1)
    called = []
    monkeypatch.setattr(mingwang.bag, "open_task_box",
                        lambda w, prefix="": called.append(prefix)
                        or (True, "任务储物格已打开"))
    monkeypatch.setattr(mingwang, "_find_huanfengfu",
                        lambda w: ((600, 300), "已找到唤风符"))
    monkeypatch.setattr(mingwang.bag, "is_bag_open", lambda w: True)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    monkeypatch.setattr(mingwang, "_close_bag_quiet", lambda w: None)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._use_huanfengfu(win)
    assert ok and "唤风符" in info
    assert called == ["名望"]
    assert clicks == [(600, 300, "secondary")]


def test_seal_config():
    assert mingwang.SEAL_LINKS == ("火藏之位", "木藏之位")
    assert game_map.template_size(mingwang.SEAL_ITEM_ICON) is not None
    assert mingwang.SEAL_ARRIVE_POLLS >= 2



# ---- 到达后无选项对话自动推进 / 用符后残留背包补关（2026-11-10 实机）----
def test_seal_dialog_advances_when_no_option(monkeypatch):
    """到达后无绿字选项对话：连续 4 轮询（~2s）后点屏推进一次。"""
    mingwang._reset_seal_state()
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, frame))
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda bgr: 0.99)
    clicked = []
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda w, times=1, prefix="": clicked.append(times))
    items = [("这该怎么办呢，好担心", [500, 300, 800, 330], 0.95)]
    for _ in range(3):
        assert mingwang._seal_dialog_blocking(object(), items, frame) is True
    assert clicked == []  # 未达 4 轮询：留足选项渲染时间
    assert mingwang._seal_dialog_blocking(object(), items, frame) is True
    assert clicked == [1]
    mingwang._reset_seal_state()


def test_seal_dialog_with_green_option_not_advanced(monkeypatch):
    """对话区有绿字选项：不点屏幕中间（交给选项表处理）。"""
    mingwang._reset_seal_state()
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    frame[300:330, 500:800] = (0, 255, 0)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, frame))
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda bgr: 0.99)
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("有选项不应点屏")))
    items = [("是武尊神遣我过来帮忙的。", [500, 300, 800, 330], 0.95)]
    for _ in range(6):
        assert mingwang._seal_dialog_blocking(object(), items, frame) is True
    mingwang._reset_seal_state()


def test_seal_close_bag_if_open(monkeypatch):
    """用符后残留背包：共享帧命中 bag_box 即关闭，2s 内不重复检测。"""
    mingwang._reset_seal_state()
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, frame))
    monkeypatch.setattr(mingwang.game_map, "match_frame_np",
                        lambda *a, **k: (100, 300))
    closed = []
    monkeypatch.setattr(mingwang, "_close_bag_quiet",
                        lambda w: closed.append(True))
    mingwang._seal_close_bag_if_open(object(), [])
    assert closed == [True]
    mingwang._seal_close_bag_if_open(object(), [])  # 限频窗口内
    assert closed == [True]
    mingwang._reset_seal_state()


# ---- 领取到达：两族都必须坐标到达领取点（NPC 模板可见不算到达）----
def test_wait_arrive_requires_claim_point(monkeypatch):
    """位置稳定且 NPC 可见但坐标不在领取点容差内 → 不算到达。"""
    for race, pos in (("仙族", ("天宫", 166, 136)),
                      ("魔族", ("大唐边境", 460, 180))):
        claim = mingwang.CLAIM_POINTS[race]
        win = _FakeWin([_pil(_noise_bgr(seed=31))])
        monkeypatch.setattr(mingwang, "is_in_battle_frame", lambda bgr: False)
        monkeypatch.setattr(mingwang.game_map, "read_map_position",
                            lambda w, img=None, p=pos: p)
        monkeypatch.setattr(mingwang, "_match_npc",
                            lambda bgr, c, full: ((1000, 500), 0.95, full))
        monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
        ok, _info = mingwang.wait_arrive(win, claim, timeout=0.05)
        assert not ok, race


def test_wait_arrive_at_claim_point(monkeypatch):
    """坐标进入领取点容差 → 直接判定到达。"""
    claim = mingwang.CLAIM_POINTS["仙族"]
    win = _FakeWin([_pil(_noise_bgr(seed=32))])
    monkeypatch.setattr(mingwang, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(mingwang.game_map, "read_map_position",
                        lambda w, img=None: ("天宫", 185, 122))
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang.wait_arrive(win, claim, timeout=1.0)
    assert ok and "已到达领取点附近" in info
