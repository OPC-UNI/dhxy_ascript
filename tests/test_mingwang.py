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
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from ascript.windows.system import R

from src.device import map as game_map
from src.tasks import mingwang
from src.minigames import moqushendan as moqu
from src.minigames import shahai


@pytest.fixture(autouse=True)
def _reset_shahai_state(monkeypatch):
    """沙海迷踪全局状态跨用例隔离（入口/小游戏 armed 时间戳）。"""
    for name in ("_shahai_armed_at", "_shahai_game_armed_at",
                 "_shahai_game_last_check"):
        monkeypatch.setattr(mingwang, name, 0.0)


@pytest.fixture(autouse=True)
def _reset_tgqy_state(monkeypatch):
    """天宫奇遇全局状态跨用例隔离（状态机/armed 时间戳）。"""
    for name, value in (
            ("_tgqy_phase", 0), ("_tgqy_stable", 0),
            ("_tgqy_last_pos", None), ("_tgqy_need_move", False),
            ("_tgqy_walk_from", None), ("_tgqy_move_stuck", 0),
            ("_tgqy_use_at", 0.0), ("_tgqy_use_retries", 0),
            ("_tgqy_missing", 0), ("_tgqy_bag_close_at", 0.0),
            ("_tgqy_link_at", 0.0), ("_tgqy_game_armed_at", 0.0),
            ("_tgqy_game_last_check", 0.0), ("_tgqy_game_retry_at", 0.0)):
        monkeypatch.setattr(mingwang, name, value)

# 模板 → 匹配阈值（与 mingwang 常量一致；世界地图标签统一由
# map.WORLD_MAP_LABELS 提供，阈值同 WORLD_MAP_LABEL_THRESHOLD）
TEMPLATES = (
    (mingwang.RENWUSHUXING_ICON, mingwang.ATTR_TEMPLATE_THRESHOLD),
    (game_map.world_map_label_icon(mingwang.CLAIM_POINTS["魔族"]["map"]), 0.8),
    (game_map.world_map_label_icon(mingwang.CLAIM_POINTS["仙族"]["map"]), 0.8),
    (mingwang.CLAIM_SHANHE_ICON, mingwang.CLAIM_SHANHE_THRESHOLD),
    (mingwang.SEAL_ITEM_ICON, mingwang.SEAL_ITEM_THRESHOLD),
    (mingwang.PICK_ITEM_ICON, mingwang.PICK_ITEM_THRESHOLD),
    (mingwang.CHUANYIN_ITEM_ICON, mingwang.CHUANYIN_ITEM_THRESHOLD),
    (mingwang.PICK_MINING_TITLE_ICON, mingwang._pochu_minigame.POCHU_THRESHOLD),
    (mingwang._pochu_minigame.POCHU_FAIL_ICON,
     mingwang._pochu_minigame.POCHU_FAIL_THRESHOLD),
    (mingwang.DIAOYU_FAIL_CLOSE_ICON, mingwang.DIAOYU_FAIL_CLOSE_THRESHOLD),
    (game_map.world_map_label_icon(mingwang.SHADU_MAP), 0.8),  # 世界地图标签
    (mingwang.SHADU_NPC_ICON, mingwang.SHADU_NPC_THRESHOLD),
    (mingwang.SHAHAI_START_ICON, mingwang.SHAHAI_START_THRESHOLD),
    (mingwang.QINGXI_START_ICON, mingwang.QINGXI_START_THRESHOLD),
    (mingwang.TGQY_ITEM_ICON, mingwang.TGQY_ITEM_THRESHOLD),
    (mingwang.REFRESH_ICON, mingwang.REFRESH_THRESHOLD),
    (moqu.MOQUSHENDAN_TITLE_ICON, moqu.MOQUSHENDAN_TITLE_THRESHOLD),
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
    """种族领取点配置完整（小地图快捷领取：地图/黄字点/筛选点），
    模板文件存在可解码。"""
    assert set(mingwang.CLAIM_POINTS) == {"魔族", "仙族"}
    for race, claim in mingwang.CLAIM_POINTS.items():
        assert claim["map"] and claim["npc_name"], race
        assert len(claim["map_filter_pos"]) == 2
        assert len(claim["map_label_pos"]) == 2
        assert game_map.template_size(
            game_map.world_map_label_icon(claim["map"])) is not None, race
    assert mingwang.CLAIM_POINTS["仙族"]["map"] == "天宫"
    # 旧「坐标寻路 + NPC 模板」路线已移除（用户 2026-11-16 规则）
    for race in ("魔族", "仙族"):
        for key in ("target", "mini_click", "npc_icon", "npc_icon2",
                    "npc_threshold", "npc_full_fallback"):
            assert key not in mingwang.CLAIM_POINTS[race], (race, key)


def test_race_strip_rect():
    rect = (500, 150, 532, 328)  # 32x178 标题牌
    strip = mingwang._race_strip_rect(rect, (1378, 831))
    assert strip == (532, 130, 652, 348)
    # 边界夹取
    strip = mingwang._race_strip_rect((1300, 5, 1365, 180), (1378, 831))
    assert strip == (1365, 0, 1378, 200)


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
    """复用 follow_task 时传入本模块对话表/跳过表（引擎无玄枢专属检测）。"""
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
    assert calls["story_known_texts"] is mingwang.DIALOG_OPTION_MAP
    # 面板缺席判定统一在 workflows.follow._confirm_task_gone（2026-11-12 全局规则），
    # 名望不再传 per-task 参数
    assert "task_gone_require_panel" not in calls


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


def test_daily_full_dialog_present_fuzzy():
    """「今日还请多加歇息，明日再来吧」识别（抗 OCR 单字误读）。"""
    assert mingwang._daily_full_dialog_present(
        [("今日还请多加歇息，明日再来吧", [500, 380, 900, 410], 0.99)])
    assert mingwang._daily_full_dialog_present(
        [("今日还请多加唔息，明日再来吧", [500, 380, 900, 410], 0.99)])
    assert not mingwang._daily_full_dialog_present(
        [("少侠准备好清理地图了吗？", [500, 380, 900, 410], 0.99)])


def test_claim_panel_task_daily_full(monkeypatch):
    """接取后出现「今日还请多加歇息，明日再来吧」→ daily_done 并关面板。

    2026-11-15 用户规则：今日任务全部完成时接取按钮仍在。该提示与
    玄枢轶闻「少侠已经完成5次任务」同为**非标准对话框**——对话模板
    门控不命中（本用例把 dialog_frame_score 桩成 0.1 专门锁住这一点），
    必须直接区域 OCR 检测；且提示判定须先于「接取按钮消失」判定。
    """
    rects = _live_rects()

    def fake_ocr(img, region):
        if tuple(region) == tuple(mingwang.task_tracker.DIALOG_REGION):
            return [("今日还请多加歇息，明日再来吧",
                     [500, 380, 900, 410], 0.99)], np.asarray(img)
        for key, rect in rects.items():
            if tuple(region) == tuple(rect):
                return LIVE_CARD_ITEMS[key], np.asarray(img)
        return [], np.asarray(img)

    monkeypatch.setattr(mingwang.game_map, "ocr_region", fake_ocr)
    # 非标准对话框：模板门不命中也要能检测
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.1)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    closed = []
    monkeypatch.setattr(mingwang, "clear_blocking_dialog",
                        lambda w, p="": closed.append("dialog") or (True, True))
    monkeypatch.setattr(mingwang, "_close_shanhe_panel",
                        lambda w: closed.append("panel")
                        or (True, "山河名望面板已关闭"))
    win = _FakeWin([_pil(_live_frame(True))])
    status, info = mingwang.claim_panel_task(win)
    assert status == "daily_done"
    assert "今日任务已全部完成" in info
    # 接取只点一次；先关提示对话框、再关面板
    assert clicks == [(487, 423, "primary")]
    assert closed == ["dialog", "panel"]


def test_run_cycle_daily_full(monkeypatch):
    """领取链路返回 daily_done → run_cycle 直接透传，不进入跟踪。"""
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "_open_claim_panel",
                        lambda w, race: (True, "已打开山河名望任务领取面板"))
    monkeypatch.setattr(mingwang, "claim_panel_task",
                        lambda w: ("daily_done", "今日任务已全部完成"))
    monkeypatch.setattr(mingwang, "_post_claim_flow",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("daily_done 不应进入跟踪")))
    status, info = mingwang.run_cycle(object(), race="魔族")
    assert status == "daily_done" and "全部完成" in info


def test_run_loop_stops_on_daily_full(monkeypatch):
    """今日任务全部完成 → 主循环结束（不计次、不重试领取）。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 5})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang.app_utils, "task_progress_add",
                        lambda w, m, d: (_ for _ in ()).throw(
                            AssertionError("daily_done 不应计数")))
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: "魔族")
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w, race=None: ("daily_done", "今日任务已全部完成"))
    info = mingwang.run_loop(object())
    assert "今日任务已全部完成" in info and "达到次数上限" not in info


def test_run_loop_not_found_closes_panel(monkeypatch):
    """任务栏无已实现任务 → 主循环结束前关闭山河名望面板（用户 2026-11-17
    规则：残留面板遮挡后续任务的追踪面板/对话框区）。

    该路径**不得**清标准对话框：领取面板在场时点屏幕中间 (689,415)
    恰落在中间列「接取」按钮 (688,423) 附近，对话框模板一旦对面板误
    命中就会误领未实现任务——只右键标题牌关闭。
    """
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 5})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang, "_load_cached_race", lambda w: "魔族")
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(mingwang, "run_cycle",
                        lambda w, race=None: ("not_found",
                                              "任务栏未找到已实现任务"))
    monkeypatch.setattr(mingwang, "clear_blocking_dialog",
                        lambda w, p="": (_ for _ in ()).throw(
                            AssertionError("无已实现任务路径不得清对话框")))
    closes = []
    monkeypatch.setattr(mingwang, "_close_shanhe_panel",
                        lambda w: closes.append("panel")
                        or (True, "山河名望面板已关闭"))
    info = mingwang.run_loop(object())
    assert "未找到已实现任务" in info and "山河名望面板已关闭" in info
    assert closes == ["panel"]


def test_close_shanhe_panel_safely_retries(monkeypatch):
    """面板关闭失败 → 最多重试 3 次；daily_done 路径先清标准对话框。"""
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    seq = []

    def _clear(w, p=""):
        seq.append("dialog")
        return True, True

    def _close(w):
        seq.append("panel")
        return False, "山河名望面板关闭失败（右键标题牌未生效）"

    monkeypatch.setattr(mingwang, "clear_blocking_dialog", _clear)
    monkeypatch.setattr(mingwang, "_close_shanhe_panel", _close)
    desc = mingwang._close_shanhe_panel_safely(object(), "测试")
    assert "关闭失败" in desc
    assert seq.count("panel") == 3
    assert seq.count("dialog") == 4   # 首次 + 每次重试前各清一次


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


def test_shahai_start_click_arms_game_check(monkeypatch):
    """「开始」点击成功后 arm 小游戏弹窗检测窗口。"""
    monkeypatch.setattr(mingwang, "_shahai_armed_at", time.time())
    tpl = cv2.imread(R.img(mingwang.SHAHAI_START_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261120)
    frame[300:300 + th, 600:600 + tw] = tpl
    win = _FakeWin([_pil(frame)])
    monkeypatch.setattr(mingwang, "human_click", lambda *a, **k: None)
    assert mingwang._maybe_click_shahai_start(win)
    assert mingwang._shahai_game_armed_at > 0


# ---- 沙海迷踪「记住混沌」小游戏：黑点检测 / 阶段识别 / 点两下净化 ----
@pytest.fixture
def shahai_fixtures():
    """用户实测标定截图目录（2.png 有 3 个黑点 / 3.png 无黑点）。"""
    return Path(__file__).resolve().parent / "fixtures"


def test_shahai_parse_top_and_stage_hint():
    """顶部轮次/倒计时解析 + 底部提示阶段判定（用户 2026-11-11 文案）。"""
    assert shahai._shahai_parse_top("第 2 次地图 剩余 8 秒") == (2, 8)
    assert shahai._shahai_parse_top("第1次地图剩余6秒") == (1, 6)
    assert shahai._shahai_parse_top("第三次地图 剩余 10 秒") == (3, 10)
    assert shahai._shahai_parse_top("") == (None, None)
    memory = "请记住混沌出现的位置，3秒后混沌将侵蚀入地图之中"
    click = "请记住混沌出现的位置，点击令混沌显形，显形后再次点击将其净化！"
    assert shahai._shahai_stage_hint(memory) == "memory"
    assert shahai._shahai_stage_hint(click) == "click"
    assert shahai._shahai_stage_hint("无法识别的乱码") is None


def test_shahai_dot_detection_calibration_images(shahai_fixtures):
    """实测标定图（贴入 1378x831 窗口的标定区域）：
    有黑点帧检出 3 个墨团，无黑点帧零检出（固定山体噪点被过滤）。"""
    l, t, r, b = shahai.SHAHAI_GAME_REGION

    def paste(name):
        img = cv2.imread(str(shahai_fixtures / name))
        frame = np.full((831, 1378, 3), 120, np.uint8)
        frame[t:b, l:r] = cv2.resize(img, (r - l, b - t))
        return frame

    dots = shahai._shahai_find_dots(paste("shahai_dots.png"))
    assert len(dots) == 3, dots
    got = sorted((x, y) for x, y, _a in dots)
    # 2.png 墨团中心 (320,249)/(512,104)/(127,252) 按区域缩放映射
    expected = sorted([(703, 536), (907, 376), (498, 539)])
    for (x, y), (ex, ey) in zip(got, expected):
        assert abs(x - ex) <= 5 and abs(y - ey) <= 5, (got, expected)
    assert shahai._shahai_find_dots(paste("shahai_clean.png")) == []


def test_shahai_dot_detection_ignores_dark_green_and_specks():
    """深绿山体（最大通道 ≥60）与小面积墨点不误检。"""
    frame = np.full((400, 700, 3), 190, np.uint8)
    cv2.circle(frame, (200, 200), 14, (30, 70, 50), -1)   # 深绿山体噪点
    cv2.circle(frame, (400, 200), 3, (10, 10, 10), -1)    # 过小墨点
    cv2.circle(frame, (300, 250), 11, (15, 22, 28), -1)   # 真黑点
    dots = shahai._shahai_find_dots(frame, region=(50, 50, 650, 350))
    assert len(dots) == 1, dots
    assert abs(dots[0][0] - 300) <= 2 and abs(dots[0][1] - 250) <= 2


def test_shahai_dot_tracking_confirm_hits():
    """跨轮询同坐标命中累加；单帧命中按兜底保留。"""
    entries = []
    shahai._shahai_track_dots(entries, [(100, 100, 200)], 1.0)
    assert shahai._shahai_pick_dots(entries) == [(100, 100)]
    shahai._shahai_track_dots(entries, [(102, 101, 180)], 1.3)
    assert entries[0]["hits"] == 2
    assert shahai._shahai_pick_dots(entries) == [(102, 101)]


def _box_frame(color=(200, 200, 200)):
    return np.full((831, 1378, 3), color, np.uint8)


def test_shahai_click_dot_two_clicks(monkeypatch):
    """正常路径：第一下显形 → 间隔 0.2s 第二下净化，横条确认消散。

    用户 2026-11-11 机制：两下之间严格 0.2s，之间不做截图/识别。
    """
    assert shahai.SHAHAI_PAIR_GAP == 0.20
    base = _box_frame()
    win = _FakeWin([_pil(base), _pil(base)])  # 两下后烟雾已消散
    clicks = []
    sleeps = []
    monkeypatch.setattr(shahai, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    monkeypatch.setattr(shahai.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(shahai, "_shahai_ocr",
                        lambda img, region: "此处混沌已消散！")
    rec = shahai._shahai_click_dot(win, 500, 400)
    assert clicks == [(500, 400), (500, 400)]
    assert sleeps.count(shahai.SHAHAI_PAIR_GAP) == 1
    assert rec["smoke_left"] is False and rec["ok"] is True
    assert rec["t2"] > rec["t1"] > 0


def test_shahai_click_dot_retries_when_smoke_left(monkeypatch):
    """两下中有一下被吞（烟雾仍在且横条无反馈）→ 200ms 后补点一次净化。"""
    base = _box_frame()
    smoked = base.copy()
    cv2.circle(smoked, (500, 400), 18, (70, 70, 70), -1)
    # before=base → 两下后=somked → 复检=somked → 补点后=base
    win = _FakeWin([_pil(base), _pil(smoked), _pil(smoked), _pil(base)])
    clicks = []
    ocr_calls = {"n": 0}

    def fake_ocr(img, region):
        ocr_calls["n"] += 1
        return "此处混沌已消散！" if ocr_calls["n"] >= 2 else None

    monkeypatch.setattr(shahai, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    monkeypatch.setattr(shahai.time, "sleep", lambda s: None)
    monkeypatch.setattr(shahai, "_shahai_ocr", fake_ocr)
    rec = shahai._shahai_click_dot(win, 500, 400)
    assert clicks == [(500, 400)] * 3
    assert rec["retry"] > 0 and rec["ok"] is True
    assert rec["smoke_left"] is False


def test_shahai_click_dot_wrong_position(monkeypatch):
    """点错坐标（无烟雾、横条「并非混沌所在」）：只点两下、不补点。"""
    base = _box_frame()
    win = _FakeWin([_pil(base), _pil(base)])
    clicks = []
    monkeypatch.setattr(shahai, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    monkeypatch.setattr(shahai.time, "sleep", lambda s: None)
    monkeypatch.setattr(shahai, "_shahai_ocr",
                        lambda img, region: "此处并非混沌所在，请少侠仔细回忆！")
    rec = shahai._shahai_click_dot(win, 500, 400)
    assert clicks == [(500, 400), (500, 400)]
    assert rec["ok"] is False and rec["smoke_left"] is False


def test_maybe_play_shahai_detects_and_plays(monkeypatch):
    """armed 窗口内弹窗在场 → 复用本帧阻塞执行小游戏并解除 armed。"""
    monkeypatch.setattr(mingwang, "_shahai_game_armed_at", time.time())
    monkeypatch.setattr(mingwang, "_shahai_game_present", lambda img: True)
    called = {}

    def fake_play(win, first_img=None, prefix="名望"):
        called["img"] = first_img
        return True, "小游戏完成"

    monkeypatch.setattr(mingwang, "play_shahai_miyong", fake_play)
    win = _FakeWin([_pil(_noise_bgr())])
    assert mingwang._maybe_play_shahai(win) is True
    assert called["img"] is win.frames[0]
    assert mingwang._shahai_game_armed_at == 0.0


def test_play_shahai_records_dots_and_purifies(monkeypatch):
    """主循环：识别记忆阶段 3 个黑点 → 阶段切换 → 逐个净化 → 弹窗关闭结束。"""
    monkeypatch.setattr(shahai, "SHAHAI_TOP_OCR_INTERVAL", 0.0)
    monkeypatch.setattr(shahai, "SHAHAI_BOTTOM_OCR_INTERVAL", 0.0)
    monkeypatch.setattr(shahai, "SHAHAI_MID_OCR_INTERVAL", 0.0)
    monkeypatch.setattr(shahai, "SHAHAI_POLL_INTERVAL", 0.0)
    monkeypatch.setattr(shahai, "SHAHAI_MEMORY_MIN_SEC", 0.0)
    monkeypatch.setattr(shahai, "SHAHAI_POPUP_MISS_POLLS", 2)
    monkeypatch.setattr(shahai.time, "sleep", lambda s: None)
    dots = [(400, 400), (500, 450), (600, 500)]
    state = {"frames": 0, "top_calls": 0, "purified": None}

    def fake_ocr(img, region):
        if region == shahai.SHAHAI_TOP_REGION:
            state["top_calls"] += 1
            if state["top_calls"] <= 2:
                return "第 1 次地图 剩余 8 秒"
            return None
        return None

    monkeypatch.setattr(shahai, "_shahai_ocr", fake_ocr)
    monkeypatch.setattr(
        shahai, "_shahai_text_gated",
        lambda img, region, cache, force=False:
        "点击令混沌显形，显形后再次点击将其净化！")

    def fake_find(bgr, region=None):
        state["frames"] += 1
        if state["frames"] <= 2:
            return [(x, y, 200) for x, y in dots]
        return []

    monkeypatch.setattr(shahai, "_shahai_find_dots", fake_find)

    def fake_purify(win, ds, prefix="名望"):
        state["purified"] = list(ds)
        return [{"x": x, "y": y, "t1": 1.0, "t2": 2.0, "retry": 0.0,
                 "smoke_left": False, "message": "此处混沌已消散！",
                 "ok": True}
                for x, y in ds]

    monkeypatch.setattr(shahai, "_shahai_purify_dots", fake_purify)
    win = _FakeWin([_pil(_noise_bgr())])
    ok, info = mingwang.play_shahai_miyong(win)
    assert ok and "1 轮" in info
    assert state["purified"] == dots


# ---- 净化水晶丛林：入口对话选项 → armed 检测「开始」按钮 → 战斗 ----
def test_qingxi_option_arms_start_check(monkeypatch):
    """点击「马上净化水晶丛林上的混沌。」后 arm「开始」按钮检测窗口。"""
    monkeypatch.setattr(mingwang, "_qingxi_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "_click_green_option_once",
                        lambda *a, **k: True)
    items = [(mingwang.QINGXI_DIALOG_TEXT, [500, 300, 800, 330], 0.95)]
    assert mingwang._handle_dialog_options(object(), items, None) is True
    assert mingwang._qingxi_armed_at > 0


def test_qingxi_start_clicked_when_armed(monkeypatch):
    """armed 窗口内出现「开始」按钮 → 点击并解除 armed。"""
    monkeypatch.setattr(mingwang, "_qingxi_armed_at", time.time())
    tpl = cv2.imread(R.img(mingwang.QINGXI_START_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261119)
    frame[300:300 + th, 600:600 + tw] = tpl
    win = _FakeWin([_pil(frame)])
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    ok = mingwang._maybe_click_qingxi_start(win)
    assert ok and clicks == [(600 + tw // 2, 300 + th // 2, "primary")]
    assert mingwang._qingxi_armed_at == 0.0


def test_qingxi_start_arm_timeout(monkeypatch):
    """armed 窗口超时 → 不再检测/点击，窗口关闭。"""
    monkeypatch.setattr(mingwang, "_qingxi_armed_at", time.time() - 999)
    ok = mingwang._maybe_click_qingxi_start(object())
    assert not ok and mingwang._qingxi_armed_at == 0.0


def test_qingxi_start_not_clicked_when_not_armed(monkeypatch):
    monkeypatch.setattr(mingwang, "_qingxi_armed_at", 0.0)
    monkeypatch.setattr(mingwang, "human_click",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("未 arm 不应点击")))
    assert mingwang._maybe_click_qingxi_start(object()) is False


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


def test_pick_card_shadu_xiuluo_high_level():
    """「沙都修罗」为高级已实现任务（2026-11-10 用户规则：通用跟踪即可）。"""
    cards = [{"row": 0, "col": 0, "rect": (0, 0, 190, 154),
              "level": "高级", "title": "沙都修罗"}]
    _card, task = mingwang._pick_card(cards)
    assert task["title"] == "沙都修罗" and task["level"] == "高级"
    low = [{"row": 0, "col": 0, "rect": (0, 0, 190, 154),
            "level": "中级", "title": "沙都修罗"}]
    _card, task = mingwang._pick_card(low)
    assert task is None


def test_claim_panel_task_claims_shadu(monkeypatch):
    """沙都修罗与其它高级卡同优先：按卡片顺序领取（通用跟踪任务）。"""
    _patch_cards_with(monkeypatch, {
        (0, 0): [("高級", [399, 298, 422, 338], 0.9),
                 ("沙都修罗", [458, 307, 537, 333], 1.0),
                 ("接取", [462, 409, 514, 437], 1.0)],
    })
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    win = _FakeWin([
        _pil(_live_frame(True)),
        _pil(_live_frame(True, skip_buttons=((487, 423),))),
    ])
    status, info = mingwang.claim_panel_task(win)
    assert status == "claimed" and "沙都修罗" in info
    assert clicks == [(487, 423, "primary")]


# ---- 沙都修罗（高级）：追踪文案触发 → 蟠桃园五路点找 NPC → 点名字上方 ----
def test_shadu_trigger_phrases():
    """触发词：完整文案/短词精确命中；短词 1 字 OCR 误读仍触发。"""
    assert mingwang._shadu_trigger(
        "山河名望日常沙都修罗根据三张蟠桃园残像所示位置")
    assert mingwang._shadu_trigger("蟠桃园残像")
    assert mingwang._shadu_trigger("根据三张蟠桃园残相所示位置")
    assert not mingwang._shadu_trigger("日常五庄封印山河名望")


def test_shadu_npc_point_skips_tracking_panel():
    """追踪面板同名文案（与面板矩形相交）排除；点击点=文案中心上方 40px。"""
    items = [("沙都修罗", [120, 300, 200, 320], 1.0),   # 追踪面板同名文案
             ("沙都修罗", [600, 300, 660, 320], 1.0)]
    assert mingwang._shadu_npc_point(items) == (630, 260)
    assert mingwang._shadu_npc_point(items[:1]) is None
    # 低置信度行不参与
    assert mingwang._shadu_npc_point(
        [("沙都修罗", [600, 300, 660, 320], 0.4)]) is None


def test_shadu_npc_point_left_edge_world_label():
    """2026-11-19 回归：NPC 名在窗口左缘（x<400）也必须识别。

    实机文案框 (331,622,400,646)、动态面板矩形 (7,227,307,557)：
    旧实现按 x<400 整列排除 → 路点 3 站在 NPC 旁认不出；改为按面板
    矩形相交排除后，世界区左缘文案正常命中，点击点=中心正上方 40px。
    """
    panel = (7, 227, 307, 557)
    items = [("·[日常]沙都修罗[山河名望]", [14, 231, 205, 253], 1.0),
             ("，找到入侵蟠桃园的沙都修罗", [27, 268, 220, 288], 1.0),
             ("沙都修罗", [331, 622, 400, 646], 1.0)]
    assert mingwang._shadu_npc_point(items, panel) == (365, 582)
    # 面板矩形缺失时静态 TRACK_REGION 兜底（面板行仍被排除）
    assert mingwang._shadu_npc_point(items) == (365, 582)


def test_shadu_scan_npc_prefers_template(monkeypatch):
    """先查 NPC 本体模板：命中直接返回模板中心，不再 OCR 文案。"""
    tpl = cv2.imread(R.img(mingwang.SHADU_NPC_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261116)
    frame[300:300 + th, 600:600 + tw] = tpl
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang.game_map, "ocr_regions",
                        lambda img, regions: (_ for _ in ()).throw(
                            AssertionError("模板命中不应 OCR")))
    pt, info = mingwang._shadu_scan_npc(_FakeWin([_pil(frame)]))
    assert pt == (600 + tw // 2, 300 + th // 2)
    assert "模板" in info


def test_shadu_scan_npc_falls_back_to_ocr(monkeypatch):
    """模板未命中 → 两段 OCR 文案并返回文案上方 40px。"""
    frame = _noise_bgr(seed=20261117)
    seen = []
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_shadu_panel_rect",
                        lambda img: (7, 227, 307, 557))
    monkeypatch.setattr(
        mingwang.game_map, "ocr_regions",
        lambda img, regions: (seen.append(regions)
                              or ([("沙都修罗", [600, 300, 660, 320], 0.99)],
                                  None)))
    pt, info = mingwang._shadu_scan_npc(_FakeWin([_pil(frame)]))
    assert pt == (630, 260)
    assert "文案" in info
    # 世界区两段 OCR（原生尺寸检出，见 SHADU_NPC_BANDS 注释）
    assert seen and seen[0] == mingwang.SHADU_NPC_BANDS


def _patch_shadu_search(monkeypatch, calls, pos=("蟠桃园", 300, 300)):
    monkeypatch.setattr(mingwang, "close_scene_panels",
                        lambda w, p="", quiet=False: None)
    monkeypatch.setattr(mingwang.game_map, "read_map_position",
                        lambda w, img=None: pos)
    monkeypatch.setattr(mingwang.game_map, "is_mini_map_open", lambda w: False)
    monkeypatch.setattr(mingwang.game_map, "open_mini_map",
                        lambda w: (True, "小地图已打开"))
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary": calls.append((x, y)))
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_shadu_wait_arrive",
                        lambda w, t: (True, "已到达"))


def test_shadu_search_clicks_npc_at_first_waypoint(monkeypatch):
    """已在蟠桃园：点第一个搜索点，到达后扫描到 NPC 即点击。"""
    calls = []
    _patch_shadu_search(monkeypatch, calls)
    monkeypatch.setattr(mingwang, "_shadu_scan_npc",
                        lambda w: ((640, 280), "找到「沙都修罗」"))
    ok, info = mingwang._shadu_search_npc(object())
    assert ok and "第 1 个搜索点" in info
    assert calls == [(739, 588), (640, 280)]


def test_shadu_search_falls_back_through_waypoints(monkeypatch):
    """前四个点未找到 → 依次走到第五个点（2026-11-12 新增）找到并点击。"""
    calls = []
    _patch_shadu_search(monkeypatch, calls)
    scans = iter([None, None, None, None, (600, 200)])

    def fake_scan(w):
        pt = next(scans)
        return pt, ("找到" if pt else "未找到")
    monkeypatch.setattr(mingwang, "_shadu_scan_npc", fake_scan)
    ok, info = mingwang._shadu_search_npc(object())
    assert ok and "第 5 个搜索点" in info
    assert calls == [(739, 588), (623, 476), (541, 428), (552, 310),
                     (710, 393), (600, 200)]


def test_shadu_search_enters_map_via_world_map_label(monkeypatch):
    """不在蟠桃园 → 世界地图点「蟠桃园」标签打开小地图（只传地图名）。"""
    calls = []
    entered = []
    _patch_shadu_search(monkeypatch, calls, pos=("天宫", 185, 122))
    monkeypatch.setattr(mingwang.game_map, "is_mini_map_open", lambda w: True)
    monkeypatch.setattr(
        mingwang.game_map, "open_mini_map_by_name",
        lambda w, map_name: entered.append(map_name) or (True, "ok"))
    monkeypatch.setattr(mingwang, "_shadu_scan_npc",
                        lambda w: ((600, 200), "找到"))
    ok, info = mingwang._shadu_search_npc(object())
    assert ok
    assert entered == [mingwang.SHADU_MAP]


def test_shadu_search_coord_nav_uses_waypoint_coords(
        monkeypatch, coord_nav_enabled):
    """坐标化开关开启：沙都路点改走 map_nav 坐标点击（6 个路点全走一遍）。"""
    from src.device import map_nav
    calls = []

    def _start(w, map_name, x, y, log_fn=None, on_map_open=None):
        calls.append((map_name, x, y))
        return True, "已点击", (0, 0)

    monkeypatch.setattr(map_nav, "start_navigation", _start)
    monkeypatch.setattr(mingwang, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(mingwang.game_map, "read_map_position",
                        lambda w: (mingwang.SHADU_MAP, 1, 1))
    monkeypatch.setattr(mingwang, "_shadu_wait_arrive",
                        lambda w, t: (True, "已到达"))
    monkeypatch.setattr(mingwang, "_shadu_scan_npc",
                        lambda w, timeout=None: (None, "未找到"))
    monkeypatch.setattr(mingwang, "log", lambda *a, **k: None)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._shadu_search_npc(object())
    assert ok is False and "未找到" in info
    assert calls == [(mingwang.SHADU_MAP, t[0], t[1])
                     for _, t in mingwang.SHADU_WAYPOINTS]


def test_shadu_search_skips_nav_when_already_at_waypoint(
        monkeypatch, coord_nav_enabled):
    """已在某搜索点容差内：该点跳过开图寻路，其余路点照常导航。"""
    from src.device import map_nav
    calls = []
    monkeypatch.setattr(
        map_nav, "start_navigation",
        lambda *a, **k: calls.append(1) or (True, "已点击", None))
    monkeypatch.setattr(mingwang, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(
        mingwang.game_map, "read_map_position",
        lambda w: (mingwang.SHADU_MAP, *mingwang.SHADU_WAYPOINTS[0][1]))
    monkeypatch.setattr(mingwang, "_shadu_wait_arrive",
                        lambda w, t: (True, "已到达"))
    monkeypatch.setattr(mingwang, "_shadu_scan_npc",
                        lambda w, timeout=None: (None, "未找到"))
    monkeypatch.setattr(mingwang, "log", lambda *a, **k: None)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._shadu_search_npc(object())
    assert ok is False and "未找到" in info
    # 第 1 个路点已在附近跳过，第 2-6 个路点各导航一次
    assert len(calls) == len(mingwang.SHADU_WAYPOINTS) - 1


def _shadu_win():
    return _FakeWin([_pil(_noise_bgr(seed=20261115))])


def test_shadu_handler_click_and_cooldown(monkeypatch):
    """触发词命中 → 执行搜索并点击；冷却期内不重复执行。"""
    monkeypatch.setattr(mingwang, "_shadu_next_try_at", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_click_until", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_absent", 0)
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.1)
    runs = []
    monkeypatch.setattr(mingwang, "_shadu_search_npc",
                        lambda w: runs.append(1) or (True, "已点击沙都修罗上方"))
    items = [("根据三张蟠桃园残像所示位置", [100, 300, 300, 320], 0.95)]
    win = _shadu_win()
    assert mingwang._handle_shadu_xiuluo(win, items, None) is True
    assert runs == [1]
    assert mingwang._handle_shadu_xiuluo(win, items, None) is False
    assert runs == [1]  # 点击冷却，不重复搜索


def test_shadu_handler_dialog_blocks_search(monkeypatch):
    """对话框在场 → 暂停搜索（先由对话表/剧情兜底推进）。"""
    monkeypatch.setattr(mingwang, "_shadu_next_try_at", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_click_until", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_absent", 0)
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(mingwang, "_shadu_search_npc",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("对话框在场不应搜索")))
    items = [("蟠桃园残像", [100, 300, 300, 320], 0.95)]
    assert mingwang._handle_shadu_xiuluo(_shadu_win(), items, None) is False


def test_shadu_handler_failure_limited_retry(monkeypatch):
    """全部路点失败 → 按 SHADU_RETRY_AFTER 限频，不每轮询重跑。"""
    monkeypatch.setattr(mingwang, "_shadu_next_try_at", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_click_until", 0.0)
    monkeypatch.setattr(mingwang, "_shadu_absent", 0)
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda img: 0.1)
    runs = []
    monkeypatch.setattr(mingwang, "_shadu_search_npc",
                        lambda w: runs.append(1) or (False, "全部路点均未找到"))
    items = [("蟠桃园残像", [100, 300, 300, 320], 0.95)]
    win = _shadu_win()
    assert mingwang._handle_shadu_xiuluo(win, items, None) is False
    assert mingwang._handle_shadu_xiuluo(win, items, None) is False
    assert runs == [1]


def test_shadu_handler_resets_when_keyword_gone(monkeypatch):
    """触发词连续缺失 → 状态复位（点击冷却清除）。"""
    monkeypatch.setattr(mingwang, "_shadu_click_until", time.time() + 999)
    monkeypatch.setattr(mingwang, "_shadu_absent", 0)
    items = [("日常五庄封印山河名望", [100, 300, 300, 320], 0.95)]
    for _ in range(mingwang.SHADU_ABSENT_RESET):
        assert mingwang._handle_shadu_xiuluo(object(), items, None) is False
    assert mingwang._shadu_click_until == 0.0


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


# ---- 蛟魔探秘钓鱼失败重试（关面板 X → 点任务追踪重走，用户 2026-11-17）----
def _diaoyu_fail_frame(seed=20261117, x_visible=True):
    """合成失败结果面板帧：标题/「游戏失败」/X 模板按真机坐标贴入。"""
    frame = _noise_bgr(seed)
    icons = [(mingwang.MINGWANG_DIAOYU_TITLE_ICON, (592, 459)),
             (mingwang._pochu_minigame.POCHU_FAIL_ICON, (646, 576))]
    if x_visible:
        icons.append((mingwang.DIAOYU_FAIL_CLOSE_ICON, (991, 469)))
    for icon, (x, y) in icons:
        tpl = cv2.imread(R.img(icon), cv2.IMREAD_COLOR)
        th, tw = tpl.shape[:2]
        frame[y:y + th, x:x + tw] = tpl
    return frame


def _diaoyu_items():
    return [("·[日常]蛟魔探秘[山河名望]", [14, 230, 205, 253], 0.98),
            ("请前往火云湖边，将捣乱者从", [21, 249, 221, 274], 0.99)]


def test_diaoyu_fail_watchdog_closes_panel_and_retries(monkeypatch):
    """失败面板看门狗：X 模板命中 → 点击关闭 → 确认标题消失 → 点任务
    追踪绿字重走（用户规则：失败先点 X 关闭面板，再点任务追踪）。"""
    monkeypatch.setattr(mingwang, "_diaoyu_fail_watch_at", 0.0)
    monkeypatch.setattr(mingwang, "_diaoyu_fail_streak", 0)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    track_calls = []

    def _track(w, title, **kw):
        track_calls.append(title)
        return True, "已跟踪「山河名望」"

    monkeypatch.setattr(mingwang.task_tracker, "track_task", _track)
    # 第 1 帧看门狗检测；第 2 帧关闭链路复查并点 X；第 3 帧面板消失
    win = _FakeWin([_pil(_diaoyu_fail_frame(x_visible=True)),
                    _pil(_diaoyu_fail_frame(x_visible=True)),
                    _pil(_noise_bgr(seed=7))])
    assert mingwang._maybe_retry_diaoyu_fail(win, _diaoyu_items()) is True
    assert clicks and clicks[0][:2] == (999, 479)   # X 模板中心
    assert track_calls == [mingwang.TASK_TITLE]
    assert mingwang._diaoyu_fail_streak == 1


def test_diaoyu_fail_watchdog_retries_close_when_panel_stays(monkeypatch):
    """X 点击后面板仍在：限时内重试点击（失败面板不关闭重走无从谈起）。"""
    monkeypatch.setattr(mingwang, "_diaoyu_fail_watch_at", 0.0)
    monkeypatch.setattr(mingwang, "_diaoyu_fail_streak", 0)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    monkeypatch.setattr(mingwang.task_tracker, "track_task",
                        lambda w, title, **kw: (True, "已跟踪"))
    # 前 3 帧面板仍在（X 点击未生效），之后消失
    frames = [_pil(_diaoyu_fail_frame()) for _ in range(4)]
    frames.append(_pil(_noise_bgr(seed=8)))
    win = _FakeWin(frames)
    assert mingwang._maybe_retry_diaoyu_fail(win, _diaoyu_items()) is True
    assert len(clicks) >= 3


def test_diaoyu_fail_watchdog_skips_other_tasks(monkeypatch):
    """追踪面板不是蛟魔探秘：不截帧零开销（看门狗 items 门控）。"""
    monkeypatch.setattr(mingwang, "_diaoyu_fail_watch_at", 0.0)
    monkeypatch.setattr(mingwang, "_diaoyu_fail_streak", 0)
    win = _FakeWin([_pil(_diaoyu_fail_frame())])
    items = [("·[日常]五庄封印[山河名望]", [14, 230, 205, 253], 0.98)]
    assert mingwang._maybe_retry_diaoyu_fail(win, items) is False
    assert win.calls == 0


def test_diaoyu_fail_watchdog_stops_after_max(monkeypatch):
    """连续失败达到上限 → 停止自动重试（防控制失准时无限空转）。"""
    monkeypatch.setattr(mingwang, "_diaoyu_fail_watch_at", 0.0)
    monkeypatch.setattr(mingwang, "_diaoyu_fail_streak",
                        mingwang.DIAOYU_FAIL_RETRY_MAX)
    win = _FakeWin([_pil(_diaoyu_fail_frame())])
    assert mingwang._maybe_retry_diaoyu_fail(win, _diaoyu_items()) is False
    assert win.calls == 0


def test_mingwang_diaoyu_window_skips_fail_panel(monkeypatch):
    """失败结果面板在场：不再次进入小游戏闭环（交给看门狗关闭+重走）。"""
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", time.time())
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_last_check", 0.0)
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang._pochu_minigame, "play_pochufengyin",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("失败面板不应进入闭环")))
    win = _FakeWin([_pil(_diaoyu_fail_frame())])
    assert mingwang._mingwang_diaoyu_window_check(win) is False


def test_mingwang_diaoyu_window_failure_keeps_armed(monkeypatch):
    """小游戏返回失败：开启 fail_detect、不 disarm（面板由看门狗重走）。"""
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", time.time())
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_last_check", 0.0)
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    frame = _noise_bgr(seed=20261118)   # 仅标题，无失败红字
    tpl = cv2.imread(R.img(mingwang.MINGWANG_DIAOYU_TITLE_ICON),
                     cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame[500:500 + th, 400:400 + tw] = tpl
    calls = {}

    def _play(w, icon, **kw):
        calls.update(kw)
        return False, "蛟魔探秘钓鱼游戏失败（P=1.99，点击 18 次）"

    monkeypatch.setattr(mingwang._pochu_minigame, "play_pochufengyin", _play)
    win = _FakeWin([_pil(frame)])
    assert mingwang._mingwang_diaoyu_window_check(win) is True
    assert calls.get("fail_detect") is True
    assert calls.get("mash") is True
    assert mingwang._mingwang_diaoyu_armed_at > 0   # 未 disarm，等看门狗接管


def test_diaoyu_fail_success_resets_streak(monkeypatch):
    """小游戏成功 → 连续失败计数清零（新一轮失败重新计数）。"""
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_armed_at", time.time())
    monkeypatch.setattr(mingwang, "_mingwang_diaoyu_last_check", 0.0)
    monkeypatch.setattr(mingwang, "_diaoyu_fail_streak", 3)
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    frame = _noise_bgr(seed=20261119)
    tpl = cv2.imread(R.img(mingwang.MINGWANG_DIAOYU_TITLE_ICON),
                     cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame[500:500 + th, 400:400 + tw] = tpl
    monkeypatch.setattr(mingwang._pochu_minigame, "play_pochufengyin",
                        lambda *a, **k: (True, "蛟魔探秘钓鱼结束（命中区间）"))
    win = _FakeWin([_pil(frame)])
    assert mingwang._mingwang_diaoyu_window_check(win) is True
    assert mingwang._diaoyu_fail_streak == 0
    assert mingwang._mingwang_diaoyu_armed_at == 0.0


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


def _patch_seal_sm(monkeypatch, pos=("五庄观", 100, 100)):
    """五庄封印状态机替换：位置可变、用符/点链接记录、对话门关闭。"""
    state = {"pos": pos, "use": 0, "click": 0}
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: state["pos"])
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, None))
    monkeypatch.setattr(mingwang, "_seal_dialog_blocking",
                        lambda w, items, img: (False, False))

    def _use(win):
        state["use"] += 1
        return True, "已使用唤风符"

    def _click(win, items, img, link_idx=None):
        state["click"] += 1
        return True

    monkeypatch.setattr(mingwang, "_use_huanfengfu", _use)
    monkeypatch.setattr(mingwang, "_click_seal_link", _click)
    return state


def test_seal_wait_arrival_uses_item_after_stable(monkeypatch):
    """封印链接出现 → 寻路位移 → 位置连续稳定 3 轮 → 用符（同显形镜）。"""
    state = _patch_seal_sm(monkeypatch)
    mingwang._reset_seal_state()
    frame = _seal_frame_red_green(fire_red=False)  # 两个链接均绿
    win = object()
    # 链接首次出现：武装位移门，原地不动不用符
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is False
    assert state["use"] == 0 and mingwang._seal_need_move is True
    state["pos"] = ("五庄观", 130, 120)   # 寻路启动：位移
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is False
    assert mingwang._seal_need_move is False
    for _ in range(2):
        assert mingwang._handle_wuzhuang_seal(
            win, SEAL_ITEMS_LIVE, frame) is False
    assert state["use"] == 0
    assert mingwang._handle_wuzhuang_seal(win, SEAL_ITEMS_LIVE, frame) is True
    assert state["use"] == 1 and mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def test_seal_no_use_when_stationary_at_start(monkeypatch):
    """封印链接出现时人物静止在清风对话点（同五庄观）→ 不得原地用符。

    2026-11-20 实机：链接出现 7 秒内就在对话点右键唤风符，白耗一张，
    15s 后重试才在真封印点生效。无位移 → 点当前封印链接启动寻路。
    """
    state = _patch_seal_sm(monkeypatch, pos=("五庄观", 160, 50))
    mingwang._reset_seal_state()
    frame = _seal_frame_red_green(fire_red=False)
    for _ in range(6):
        assert mingwang._handle_wuzhuang_seal(
            object(), SEAL_ITEMS_LIVE, frame) is False
    assert state["use"] == 0
    assert state["click"] == 1            # 4 轮询无位移 → 点当前封印链接
    assert mingwang._seal_need_move is True
    mingwang._reset_seal_state()


def test_seal_forced_use_after_stuck_cycles(monkeypatch):
    """断点续跑人物已站在封印点：多次重点仍无位移 → 放行用符。"""
    state = _patch_seal_sm(monkeypatch, pos=("五庄观", 100, 100))
    mingwang._reset_seal_state()
    frame = _seal_frame_red_green(fire_red=False)
    for _ in range(20):                   # 3 个重点周期（12 轮询）后放行
        mingwang._handle_wuzhuang_seal(object(), SEAL_ITEMS_LIVE, frame)
    assert mingwang._seal_need_move is False
    assert state["use"] >= 1 and mingwang._seal_phase == 1
    mingwang._reset_seal_state()


def test_seal_no_use_before_links_appear(monkeypatch):
    """「看看五庄观的清风」阶段（追踪无封印链接）：站定也不得用符。"""
    state = _patch_seal_sm(monkeypatch, pos=("五庄观", 100, 100))
    mingwang._reset_seal_state()
    items = [("[日常]五庄封印[山河名望]", [18, 230, 205, 253], 0.97),
             ("看看五庄观的清风遇到什么麻", [22, 251, 222, 271], 1.0)]
    for _ in range(20):
        assert mingwang._handle_wuzhuang_seal(object(), items, None) is False
    assert state["use"] == 0
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


def test_seal_parse_links_dynamic_elements():
    """封印点从追踪文案动态解析（五行任选两个，2026-11-17 实机）。

    实机已见「金藏+土藏」「水藏+火藏」「火藏+木藏」；OCR 形近字归一
    （全→金、未/本→木、士→土、灭→火）；追踪拆行不影响（签名拼接无换行）。
    """
    assert mingwang._seal_parse_links(
        "山河名望在五庄观找到金藏之位和土藏") == ["金", "土"]
    assert mingwang._seal_parse_links(
        "找到水藏之位和火藏之位的风暴封印处") == ["水", "火"]
    assert mingwang._seal_parse_links("找到火藏之位和木藏") == ["火", "木"]
    assert mingwang._seal_parse_links("找到全藏之位和士藏") == ["金", "土"]
    assert mingwang._seal_parse_links("找到火藏之位和未藏") == ["火", "木"]
    assert mingwang._seal_parse_links("没有链接的文案") == []


def test_seal_link_keywords_and_color_follow_parsed_elements():
    """解析出的元素用于定位/判色（「水藏」是真实元素，不再当木的别名）。"""
    items = [
        ("[日常]五庄封印[山河名望]", [18, 230, 205, 253], 0.97),
        ("在五庄观找到火藏之位和水藏", [22, 251, 222, 271], 1.0),
        ("之位的风暴封印处，使用唤风", [23, 267, 222, 288], 1.0),
    ]
    frame = _seal_frame_red_green(fire_red=True, wood_red=False)
    elems = mingwang._seal_parse_links(mingwang._seal_sig(items))
    assert elems == ["火", "水"]
    kws = mingwang._seal_link_keywords(1, elems)
    assert "水藏" in kws
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
    monkeypatch.setattr(mingwang, "_find_task_item",
                        lambda w, icon, label, threshold=0.8, timeout=10.0:
                        ((600, 300), f"已找到{label}"))
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
    # 封印点为动态解析（五行任选两个，2026-11-17 用户确认）
    assert mingwang.SEAL_LINK_NAME_SUFFIX == "藏之位"
    assert mingwang._seal_parse_links("找到金藏之位和土藏") == ["金", "土"]
    assert game_map.template_size(mingwang.SEAL_ITEM_ICON) is not None
    assert mingwang.SEAL_ARRIVE_POLLS >= 2



# ---- 到达后无选项对话自动推进 / 用符后残留背包补关（2026-11-10 实机）----
def test_seal_dialog_advances_when_no_option(monkeypatch):
    """到达后无绿字选项对话：连续 4 轮询（~2s）后点屏推进一次。

    返回 (在场, 无选项)：无选项=True 让状态机独占推进（引擎剧情兜底
    跳过）——两套点屏同帧命中会连点两次（2026-11-17 实机教训）。
    """
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
        assert mingwang._seal_dialog_blocking(
            object(), items, frame) == (True, True)
    assert clicked == []  # 未达 4 轮询：留足选项渲染时间
    assert mingwang._seal_dialog_blocking(object(), items, frame) == (True, True)
    assert clicked == [1]
    mingwang._reset_seal_state()


def test_seal_dialog_with_green_option_not_advanced(monkeypatch):
    """对话区有绿字选项：不点屏幕中间，返回「非独占」（交给选项表）。"""
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
        assert mingwang._seal_dialog_blocking(
            object(), items, frame) == (True, False)
    mingwang._reset_seal_state()


def test_seal_dialog_not_advanced_while_walking(monkeypatch):
    """寻路中不点屏推进：点屏幕中间会取消自动寻路，人物停在半路。

    2026-11-17 实机：封印状态机在引擎判定「人物寻路中」时仍点屏，
    寻路被打断；点屏推进须确认人物站定（MoveWatch）。
    """
    mingwang._reset_seal_state()
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (frame, frame))
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda bgr: 0.99)
    monkeypatch.setattr(mingwang._seal_walk_guard, "walking",
                        lambda *a, **k: True)
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("寻路中不应点屏")))
    items = [("这该怎么办呢，好担心", [500, 300, 800, 330], 0.95)]
    for _ in range(6):
        assert mingwang._seal_dialog_blocking(
            object(), items, frame) == (True, True)
    mingwang._reset_seal_state()


def test_seal_dialog_advances_after_walk_false_positive(monkeypatch):
    """五庄观位置标签条背后动画致像素误判「寻路中」：坐标 OCR 复核放行。

    2026-11-20 实机回归：裸 MoveWatch 让战后剧情对话空等 56 秒
    （15:30:52~15:31:48 连续「人物寻路中」）；WalkGuard 在持续
    移动 ≥STORY_MOVE_VERIFY_AFTER 后读坐标，两次一致即按站定点屏。
    """
    import time as _time
    from src.device import dialog_common as dc
    mingwang._reset_seal_state()
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (frame, frame))
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda bgr: 0.99)
    guard = mingwang._seal_walk_guard
    monkeypatch.setattr(guard._move, "walking", lambda *a, **k: True)
    monkeypatch.setattr(mingwang.game_map, "read_map_position",
                        lambda win, img=None: ("五庄观", 7, 31))
    guard._moving_since = _time.time() - dc.STORY_MOVE_VERIFY_AFTER - 1
    guard._verify_at = 0.0
    guard._verify_pos = ("五庄观", 7, 31)
    clicked = []
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda w, times=1, prefix="": clicked.append(times))
    items = [("跑得真快，但是一瞬间，那人的人皮怎么好像掉了",
              [500, 300, 940, 330], 0.98)]
    for _ in range(3):
        assert mingwang._seal_dialog_blocking(
            object(), items, frame) == (True, True)
    assert clicked == []                 # 未达 4 轮询：留足选项渲染时间
    assert mingwang._seal_dialog_blocking(
        object(), items, frame) == (True, True)
    assert clicked == [1]                # 坐标复核一致 → 站定放行点屏
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


# ---- 领取入口链（自动对话框模式）：等绿色选项，绝不发生世界点击 ----
def test_claim_entry_auto_dialog_only_no_world_click(monkeypatch):
    """等待自动对话框期间只等绿色选项：不点 NPC/不点屏幕中间（世界点击
    会取消游戏自动寻路），超时直接返回失败。"""
    win = _FakeWin([_pil(_noise_bgr(seed=20261118))])
    monkeypatch.setattr(mingwang, "MAP_CLAIM_ARRIVE_TIMEOUT", 0.05)
    monkeypatch.setattr(mingwang, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(mingwang.game_map, "ocr_region",
                        lambda img, region=None: ([], np.asarray(img)))
    monkeypatch.setattr(mingwang, "_click_claim_option", lambda *a, **k: False)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda *a, **k: clicks.append(a))
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda *a, **k: clicks.append("center"))
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._claim_and_open_panel(
        win, mingwang.CLAIM_POINTS["仙族"], timeout=0.05)
    assert not ok and "超时" in info and clicks == []


# ---- 灵力镐：追踪「前往大唐边境，使用灵力镐」→ 点蕴灵石头 → 到达 → 用镐（2026-11-10）----
def _pick_frame():
    """合成帧：实机追踪文案两行 + 两处链接下划线（灵力镐 / 蕴灵石头）。

    2026-11-10 实机截图：行1「前往大唐边境，使用 灵力镐」（灵力镐 为
    物品说明链接）、行2「，用合适的力道开凿 蕴灵石头」（蕴灵石头 为
    导航链接）、行3「(412,45)。」。两者都是亮绿带下划线——必须只点
    蕴灵石头（点灵力镐会开物品说明，用户反馈点错）。
    """
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    frame[246:264, 10:200] = (30, 200, 30)      # 行1 绿字
    frame[266:268, 150:200] = (0, 255, 0)       # 灵力镐下划线（错误目标）
    frame[268:286, 10:230] = (30, 200, 30)      # 行2 绿字
    frame[288:290, 140:210] = (0, 255, 0)       # 蕴灵石头下划线（正确目标）
    return frame


PICK_ITEMS = [
    ("前往大唐边境，使用", [10, 246, 145, 264], 0.95),
    ("灵力镐", [150, 246, 200, 264], 0.92),
    ("，用合适的力道开凿", [10, 268, 135, 286], 0.95),
    ("蕴灵石头", [140, 268, 210, 286], 0.93),
    ("(412,45)。", [10, 290, 90, 306], 0.90),
]


def test_lingligao_trigger_keywords():
    assert mingwang._pick_trigger("山河名望 前往大唐边境，使用灵力镐")
    assert mingwang._pick_trigger("前往大唐边境，使用灵力稿")  # OCR 形近
    assert not mingwang._pick_trigger("火藏之位 木藏之位")
    assert not mingwang._pick_trigger("")


def test_lingligao_link_point_targets_stone_not_pickaxe():
    """只点「蕴灵石头」下划线，绝不点「灵力镐」下划线（物品说明）。"""
    pt = mingwang._pick_link_point(PICK_ITEMS, _pick_frame())
    assert pt is not None
    x, y = pt
    assert 140 <= x <= 210 and y == 286          # 蕴灵石头下划线上方 2px
    assert not (150 <= x <= 200 and y == 264)    # 不是灵力镐下划线


def test_lingligao_link_point_stone_missing_no_fallback():
    """蕴灵石头未读出 → 返回 None（不退而点灵力镐/前往文案）。"""
    items = [("前往大唐边境，使用", [10, 246, 145, 264], 0.95),
             ("灵力镐", [150, 246, 200, 264], 0.92)]
    assert mingwang._pick_link_point(items, _pick_frame()) is None


def test_lingligao_link_point_short_keyword():
    """OCR 截断为「蕴灵」短词时仍能定位（形近/截断兜底）。"""
    items = [("用合适的力道开凿 蕴灵", [10, 268, 210, 286], 0.92)]
    pt = mingwang._pick_link_point(items, _pick_frame())
    assert pt is not None and 140 <= pt[0] <= 210


def test_lingligao_link_point_excludes_dialog():
    """居中对话框同名文案（x≥panel_row_xmax）不参与链接定位。"""
    frame = _pick_frame()
    frame[268:290, 420:560] = (0, 255, 0)
    items = [("蕴灵石头", [420, 268, 560, 286], 0.95)]
    assert mingwang._pick_link_point(items, frame) is None


def test_mingwang_green_picker_falls_through():
    """非封印/非灵力镐任务：green_picker 返回 None 交给引擎行级选择。"""
    frame = _pick_frame()
    items = [("去找大唐边境(12,34)惯骗", [10, 246, 200, 264], 0.9)]
    assert mingwang._mingwang_green_picker(items, frame) is None


def test_lingligao_green_picker_refuses_fallback():
    """触发词在但蕴灵石头未读出 → 返回 PICK_FAIL，禁止引擎兜底点灵力镐。"""
    frame = _pick_frame()
    assert mingwang._pick_green_picker(PICK_ITEMS, frame) is not None
    items = [("前往大唐边境，使用", [10, 246, 145, 264], 0.95),
             ("灵力镐", [150, 246, 200, 264], 0.92)]
    assert mingwang._pick_green_picker(items, frame) \
        == mingwang.task_tracker.PICK_FAIL
    # 非灵力镐任务不受影响：回退引擎行级选择
    other = [("去找大唐边境(12,34)惯骗", [10, 246, 200, 264], 0.9)]
    assert mingwang._pick_green_picker(other, frame) is None


def _patch_pick_sm(monkeypatch):
    """灵力镐状态机替换：位置可变、用镐/点链接记录、背包/对话框关闭。"""
    state = {"pos": ("大唐边境", 477, 180), "use": 0, "click": 0}
    monkeypatch.setattr(mingwang, "_seal_sig",
                        lambda items: "山河名望 前往大唐边境，使用灵力镐")
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda win, items: state["pos"])
    monkeypatch.setattr(mingwang, "_seal_dialog_blocking",
                        lambda win, items, img: (False, False))
    monkeypatch.setattr(mingwang, "_bag_box_on_frame",
                        lambda win, items: False)

    def _use(win):
        state["use"] += 1
        return True, "已使用灵力镐"

    def _click(win, items, img):
        state["click"] += 1
        mingwang._pick_link_at = 0.0
        return True

    monkeypatch.setattr(mingwang, "_use_lingligao", _use)
    monkeypatch.setattr(mingwang, "_click_pick_link", _click)
    return state


def test_lingligao_use_after_arrival(monkeypatch):
    """有位移（寻路启动）+ 位置稳定 + 目标地图 → 到达后用镐一次。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 500, 220)          # 位移：寻路启动
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 640, 360)
    for _ in range(3):
        mingwang._handle_lingligao(object(), [], frame)
    assert state["use"] == 1 and mingwang._pick_phase == 1


def test_lingligao_no_use_when_stationary_at_start(monkeypatch):
    """静止在领取点（同图大唐边境）→ 先用镐会打空：不得用镐，转而重点链接。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    for _ in range(6):
        mingwang._handle_lingligao(object(), [], frame)
    assert state["use"] == 0
    assert state["click"] == 1          # 4 轮询无位移 → 重点链接
    assert mingwang._pick_need_move is True


def test_lingligao_forced_use_after_stuck_cycles(monkeypatch):
    """连续多次重点仍无位移（已在目的地）→ 放行用镐。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    for _ in range(20):                  # 3 个重点周期（12 轮询）后放行
        mingwang._handle_lingligao(object(), [], frame)
    assert mingwang._pick_need_move is False
    assert state["use"] == 1


def test_lingligao_reclick_when_wrong_map(monkeypatch):
    """原地静止但不在目标地图 → 到间隔后重点链接重新寻路。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    state["pos"] = ("长安", 300, 300)
    for _ in range(4):
        mingwang._handle_lingligao(object(), [], frame)
    assert state["use"] == 0
    mingwang._pick_link_at = time.time() - mingwang.PICK_LINK_RETRY_AFTER - 1
    mingwang._handle_lingligao(object(), [], frame)
    assert state["click"] == 1 and state["use"] == 0


def test_lingligao_reset_when_trigger_gone(monkeypatch):
    """触发词消失（任务推进完成）→ 连续缺失后状态复位。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 500, 220)
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 760, 410)
    for _ in range(3):
        mingwang._handle_lingligao(object(), [], frame)
    assert mingwang._pick_phase == 1
    monkeypatch.setattr(mingwang, "_seal_sig",
                        lambda items: "山河名望 前往大唐边境")
    for _ in range(mingwang.PICK_ABSENT_RESET):
        mingwang._handle_lingligao(object(), [], frame)
    assert mingwang._pick_phase == 0 and mingwang._pick_need_move is True


# ---- 用镐后「开采石头」小游戏（kaicaishitou 模板 → 破除封印同款点击闭环）----
def test_pick_mining_template_calibration():
    """标题模板贴入合成帧（POCHU_REGION 内）可命中；噪声帧低于阈值。"""
    frame = _paste_bgr(mingwang.PICK_MINING_TITLE_ICON, x=600, y=480)
    score, pt = game_map.best_match_np(frame, mingwang.PICK_MINING_TITLE_ICON,
                                       region=mingwang._pochu_minigame.POCHU_REGION)
    assert score >= mingwang._pochu_minigame.POCHU_THRESHOLD
    noise = _noise_bgr(seed=77)
    s2, p2 = game_map.best_match_np(noise, mingwang.PICK_MINING_TITLE_ICON)
    assert s2 < mingwang._pochu_minigame.POCHU_THRESHOLD


def _patch_mining_check(monkeypatch, title_score):
    mingwang._reset_pick_state()
    win = _FakeWin([_pil(_noise_bgr(seed=55))])
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang.game_map, "best_match_np",
                        lambda frame, icon, region=None, **k:
                        (title_score, (600, 500)) if icon
                        == mingwang.PICK_MINING_TITLE_ICON
                        else (0.0, None))
    calls = []

    def _play(win_, icon, prefix="名望", label="", **kw):
        calls.append((icon, label, kw))
        return True, "开采石头结束"

    monkeypatch.setattr(mingwang._pochu_minigame, "play_pochufengyin", _play)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    return win, calls


def test_pick_mining_window_plays_hold_mode(monkeypatch):
    """armed + 标题命中 → 长按运输+点按维持（hold/hold_catch + 长按按钮）。"""
    win, calls = _patch_mining_check(monkeypatch, 0.95)
    mingwang._arm_pick_mining_check()
    handled = mingwang._pick_mining_window_check(win)
    assert handled is True
    assert len(calls) == 1
    icon, label, kw = calls[0]
    assert icon == mingwang.PICK_MINING_TITLE_ICON and label == "开采石头"
    assert kw.get("hold") is True          # 开采石头是长按不是点击
    assert kw.get("hold_catch") is True    # 长按推过带上缘后点按维持
    assert kw.get("action_icon") == mingwang.PICK_MINING_HOLD_ICON
    assert mingwang._pick_mining_armed_at == 0.0   # 成功后解除 armed


def test_pick_mining_window_not_armed(monkeypatch):
    """未 armed（未用镐）→ 检测零成本直接返回 False。"""
    win, calls = _patch_mining_check(monkeypatch, 0.95)
    assert mingwang._pick_mining_window_check(win) is False
    assert calls == []


def test_pick_mining_window_title_absent(monkeypatch):
    """armed 但标题未命中 → 不执行小游戏。"""
    win, calls = _patch_mining_check(monkeypatch, 0.2)
    mingwang._arm_pick_mining_check()
    assert mingwang._pick_mining_window_check(win) is False
    assert calls == []
    assert mingwang._pick_mining_armed_at > 0     # 保持 armed 继续检测


def test_pick_mining_armed_on_use(monkeypatch):
    """到达用镐成功 → 自动 armed（进入小游戏窗口检测窗口）。"""
    state = _patch_pick_sm(monkeypatch)
    mingwang._reset_pick_state()
    frame = _pick_frame()
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 500, 220)
    mingwang._handle_lingligao(object(), [], frame)
    state["pos"] = ("大唐边境", 640, 360)
    for _ in range(3):
        mingwang._handle_lingligao(object(), [], frame)
    assert state["use"] == 1
    assert mingwang._pick_mining_armed_at > 0


def test_use_chuanyinshi_clicks_task_item(monkeypatch):
    """任务储物格（bag.open_task_box 通用能力）命中传音石 → 右键使用。"""
    win = _FakeWin([_pil(_noise_bgr(seed=13))])
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda frame: 0.1)
    called = []
    monkeypatch.setattr(mingwang.bag, "open_task_box",
                        lambda w, prefix="": called.append(prefix)
                        or (True, "任务储物格已打开"))
    seen = {}

    def _find(w, icon, label, threshold=0.8, timeout=10.0):
        seen.update(icon=icon, label=label, threshold=threshold,
                    timeout=timeout)
        return (610, 310), f"已找到{label}"

    monkeypatch.setattr(mingwang, "_find_task_item", _find)
    monkeypatch.setattr(mingwang.bag, "is_bag_open", lambda w: True)
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    monkeypatch.setattr(mingwang, "_close_bag_quiet", lambda w: None)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    ok, info = mingwang._use_chuanyinshi(win)
    assert ok and "传音石" in info
    assert called == ["名望"]
    assert seen == {"icon": mingwang.CHUANYIN_ITEM_ICON,
                    "label": mingwang.CHUANYIN_ITEM_LABEL,
                    "threshold": mingwang.CHUANYIN_ITEM_THRESHOLD,
                    "timeout": mingwang.CHUANYIN_ITEM_FIND_TIMEOUT}
    assert clicks == [(610, 310, "secondary")]


# ---- 蛟魔复仇（暗雷战斗）：到达文案地图后降落 ----
JIAMO_SIG = ("日常蛟魔复仇山河名望仙族兵士逃窜而去请在女儿村仔细搜查"
             "可不能让让他就这么走了暗雷战斗")


def test_jiamo_target_map_parse():
    """「请在<地图>仔细搜查」解析目标地图（含 OCR 形近变体/后缀剥离）。"""
    assert mingwang._jiamo_target_map(JIAMO_SIG) == "女儿村"
    assert mingwang._jiamo_target_map("山河名望请在长寿村搜查暗雷战斗") == "长寿村"
    assert mingwang._jiamo_target_map("山河名望请在女儿村仟细搜查暗雷战斗") == "女儿村"
    assert mingwang._jiamo_target_map("山河名望请在女儿村一带仔细搜查暗雷战斗") == "女儿村"
    assert mingwang._jiamo_target_map("山河名望请在仔细搜查暗雷战斗") is None
    assert mingwang._jiamo_target_map("山河名望暗雷战斗") is None


def test_jiamo_trigger_from_tracker_items():
    """实机追踪面板 OCR 行（含拆行）→ _seal_sig 触发词 + 目标地图解析。"""
    items = [
        ("[日常]蛟魔复仇[山河名望]", [11, 230, 205, 253], 0.99),
        ("仙族兵士逃窜而去，请在女儿村仔细搜查，可不能让让他就这么走了。",
         [11, 256, 300, 280], 0.98),
        ("（暗雷战斗）", [11, 282, 120, 300], 0.97),
    ]
    sig = mingwang._seal_sig(items)
    assert mingwang.JIAMO_ANLEI_KEYWORD in sig
    assert mingwang._jiamo_target_map(sig) == "女儿村"


def test_jiamo_map_match():
    """地名比对：精确 + 等长 ≥3 字容 1 字误读；不做包含匹配。"""
    assert mingwang._jiamo_map_match("女儿村", "女儿村")
    assert mingwang._jiamo_map_match("女儿材", "女儿村")
    assert not mingwang._jiamo_map_match("长寿村", "女儿村")
    assert not mingwang._jiamo_map_match("长安城", "长安城东")  # 前缀地图不误判
    assert not mingwang._jiamo_map_match("天宫", "地府")        # 2 字不容错
    assert not mingwang._jiamo_map_match(None, "女儿村")
    assert not mingwang._jiamo_map_match("女儿村", None)


def _patch_jiamo(monkeypatch, sig, pos, land_ok=True):
    """蛟魔复仇状态机替换：追踪文案、位置读取、降落/关面板/走位记录。"""
    state = {"land": 0, "close": 0, "sig": sig, "pos": pos, "ok": land_ok,
             "pos_reads": 0, "walk": 0}
    monkeypatch.setattr(mingwang, "_seal_sig", lambda items: state["sig"])

    def _pos(win, items):
        state["pos_reads"] += 1
        return state["pos"]

    monkeypatch.setattr(mingwang, "_seal_pos", _pos)

    def _land(win):
        state["land"] += 1
        return state["ok"], ("已降落（无需降落）" if state["ok"]
                             else "战斗中不可使用飞行驭器")

    def _close(win):
        state["close"] += 1
        return True, "飞行驭器面板已关闭"

    def _walk(win):
        state["walk"] += 1
        return "已点击世界地面模拟行走，等待暗雷遇敌"

    monkeypatch.setattr(mingwang.zuoqi, "ensure_landed", _land)
    monkeypatch.setattr(mingwang.zuoqi, "close_feixingyuqi_panel", _close)
    monkeypatch.setattr(mingwang, "_jiamo_walk_click", _walk)
    return state


def _jiamo_poll(state, pos=None):
    """绕过位置 OCR 限频跑一轮（可顺带更新桩位置）。"""
    if pos is not None:
        state["pos"] = pos
    mingwang._jiamo_pos_at = 0.0
    return mingwang._handle_jiamo_anlei(object(), [], None)


def _jiamo_seed_stable(pos):
    """预置「位移已见 + 稳定 2 轮」：再一轮同位置即达 JIAMO_ARRIVE_POLLS。"""
    mingwang._jiamo_move_seen = True
    mingwang._jiamo_walk_from = pos
    mingwang._jiamo_stable = mingwang.JIAMO_ARRIVE_POLLS - 1
    mingwang._jiamo_last_pos = pos


def test_jiamo_waits_move_before_landing(monkeypatch):
    """先寻路后降落：在目标地图但未见位移 → 不降落；见位移并稳定后才降落。

    回归 2026-11-15 用户实机问题：点击寻路前已在目标地图，旧逻辑立即
    降落，随后寻路起飞白降落一次。
    """
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 88, 40))
    mingwang._reset_jiamo_state()
    # 已在目标地图但未见位移：不降落（等寻路启动）
    assert _jiamo_poll(state) is False
    assert state["land"] == 0 and mingwang._jiamo_phase == 0
    assert _jiamo_poll(state) is False
    assert state["land"] == 0
    # 位移出现（寻路启动）→ 位置稳定 3 轮后降落
    assert _jiamo_poll(state, ("女儿村", 95, 45)) is False
    assert mingwang._jiamo_move_seen is True
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state) is False
    assert state["land"] == 0
    assert _jiamo_poll(state) is True
    assert state["land"] == 1 and state["close"] == 1
    assert mingwang._jiamo_phase == 1


def test_jiamo_no_move_timeout_lands(monkeypatch):
    """始终未见位移（目的地就在脚下）→ JIAMO_MOVE_WAIT 超时放行后到达并降落。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 7, 7))
    mingwang._reset_jiamo_state()
    assert _jiamo_poll(state) is False
    assert state["land"] == 0
    mingwang._jiamo_walk_at = time.time() - mingwang.JIAMO_MOVE_WAIT - 1
    assert _jiamo_poll(state) is False          # 放行后开始累计稳定
    assert mingwang._jiamo_move_seen is True
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state) is True
    assert state["land"] == 1 and mingwang._jiamo_phase == 1


def test_jiamo_waits_until_target_map(monkeypatch):
    """寻路途中（其它地图）不降落；到达目标地图并稳定后 → 降落一次并关面板。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("洛阳城", 144, 41))
    mingwang._reset_jiamo_state()
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state, ("洛阳城", 150, 45)) is False
    assert state["land"] == 0 and mingwang._jiamo_phase == 0
    # 到达目标地图：仍须连续稳定 3 轮
    assert _jiamo_poll(state, ("女儿村", 88, 40)) is False
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state) is False
    assert _jiamo_poll(state) is True
    assert state["land"] == 1 and state["close"] == 1
    assert mingwang._jiamo_phase == 1
    # 已降落：后续轮询零动作（位置不可读/战斗心跳也不重复降落）
    state["pos"] = None
    assert _jiamo_poll(state) is False
    assert state["land"] == 1


def test_jiamo_pos_check_throttled(monkeypatch):
    """位置地名 OCR 按间隔限频（轮询热路径不每轮读位置）。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("洛阳城", 1, 1))
    mingwang._reset_jiamo_state()
    assert mingwang._handle_jiamo_anlei(object(), [], None) is False
    assert state["pos_reads"] == 1
    assert mingwang._handle_jiamo_anlei(object(), [], None) is False
    assert state["pos_reads"] == 1                 # 间隔内不再读位置
    mingwang._jiamo_pos_at = time.time() - mingwang.JIAMO_POS_CHECK_INTERVAL
    assert mingwang._handle_jiamo_anlei(object(), [], None) is False
    assert state["pos_reads"] == 2


def test_jiamo_land_failure_retries(monkeypatch):
    """降落失败（战斗中/面板瞬态）→ 保持 phase 0，按间隔重试。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 1, 1),
                         land_ok=False)
    mingwang._reset_jiamo_state()
    _jiamo_seed_stable(("女儿村", 1, 1))
    assert _jiamo_poll(state) is False
    assert state["land"] == 1 and state["close"] == 1  # 成败都尝试关面板
    assert mingwang._jiamo_phase == 0
    assert _jiamo_poll(state) is False
    assert state["land"] == 1                      # 重试间隔内不再开面板
    mingwang._jiamo_land_at = time.time() - mingwang.JIAMO_LAND_RETRY_AFTER
    state["ok"] = True
    _jiamo_seed_stable(("女儿村", 1, 1))
    assert _jiamo_poll(state) is True
    assert state["land"] == 2 and mingwang._jiamo_phase == 1


def test_jiamo_new_target_map_relands(monkeypatch):
    """多段搜查换图：目标地图变化 → 重新等待寻路/到达并再次降落。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 1, 1))
    mingwang._reset_jiamo_state()
    _jiamo_seed_stable(("女儿村", 1, 1))
    assert _jiamo_poll(state) is True
    assert mingwang._jiamo_phase == 1 and state["land"] == 1
    state["sig"] = "山河名望请在长寿村仔细搜查暗雷战斗"
    state["pos"] = ("长寿村", 5, 6)
    assert _jiamo_poll(state) is False             # 目标变化：重新等待寻路
    assert mingwang._jiamo_phase == 0
    assert mingwang._jiamo_target == "长寿村"
    _jiamo_seed_stable(("长寿村", 5, 6))
    mingwang._jiamo_land_at = time.time() - mingwang.JIAMO_LAND_RETRY_AFTER
    assert _jiamo_poll(state) is True
    assert state["land"] == 2 and mingwang._jiamo_phase == 1


def test_jiamo_walk_click_after_stable_no_battle(monkeypatch):
    """到达稳定后仍不开战（任务文案仍在）→ 点击世界地面模拟行走催战
    （静默 Alt+D 不会让人物走动，2026-11-17 用户实测修正；限频重按）。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 1, 1))
    mingwang._reset_jiamo_state()
    _jiamo_seed_stable(("女儿村", 1, 1))
    assert _jiamo_poll(state) is True              # 降落 → phase 1
    assert state["walk"] == 0
    # 稳定但未到走位等待时限：不点
    assert _jiamo_poll(state) is False
    assert state["walk"] == 0 and mingwang._jiamo_stable_at > 0
    # 等待时限到：点地面走位
    mingwang._jiamo_stable_at = time.time() - mingwang.JIAMO_WALK_WAIT - 1
    assert _jiamo_poll(state) is True
    assert state["walk"] == 1
    # 重按间隔内不重复
    assert _jiamo_poll(state) is False
    assert state["walk"] == 1
    # 间隔到：再次点
    mingwang._jiamo_walk_click_at = (time.time()
                                     - mingwang.JIAMO_WALK_INTERVAL - 1)
    assert _jiamo_poll(state) is True
    assert state["walk"] == 2
    # 位置变化（走动中）→ 稳定清零、不点
    assert _jiamo_poll(state, ("女儿村", 40, 40)) is False
    assert mingwang._jiamo_stable_at == 0.0
    mingwang._jiamo_walk_click_at = 0.0
    assert _jiamo_poll(state) is False
    assert state["walk"] == 2


def test_jiamo_walk_click_human_click_rotates(monkeypatch):
    """走位点击：human_click 世界点轮换（避开 UI，四点循环）。"""
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    mingwang._jiamo_walk_idx = 0
    for _ in range(len(mingwang.JIAMO_WALK_POINTS) + 1):
        desc = mingwang._jiamo_walk_click(object())
        assert "点击世界地面" in desc and "等待暗雷遇敌" in desc
    pts = [p for p in clicks]
    assert pts[0] == mingwang.JIAMO_WALK_POINTS[0]
    assert pts[:4] == list(mingwang.JIAMO_WALK_POINTS)
    assert pts[4] == mingwang.JIAMO_WALK_POINTS[0]   # 循环回第一个点
    # 走位点都在世界区域（避开左上追踪面板/底部战斗面板/技能栏）
    for x, y in mingwang.JIAMO_WALK_POINTS:
        assert 420 <= x <= 950 and 230 <= y <= 680


def test_jiamo_reset_when_trigger_gone(monkeypatch):
    """触发词连续缺失 → 状态复位；空 OCR 帧（战斗心跳）不计缺失。"""
    state = _patch_jiamo(monkeypatch, JIAMO_SIG, ("女儿村", 1, 1))
    mingwang._reset_jiamo_state()
    _jiamo_seed_stable(("女儿村", 1, 1))
    assert _jiamo_poll(state) is True
    # 战斗中 extras 空 items 心跳不计数：已降落状态保持
    state["sig"] = ""
    for _ in range(mingwang.JIAMO_ABSENT_RESET * 2):
        assert mingwang._handle_jiamo_anlei(object(), [], None) is False
    assert mingwang._jiamo_phase == 1
    # 追踪面板可读且触发词消失 → 连续 N 轮后复位
    state["sig"] = "山河名望仙族兵士已被击败"
    for _ in range(mingwang.JIAMO_ABSENT_RESET):
        mingwang._handle_jiamo_anlei(object(), [], None)
    assert mingwang._jiamo_phase == 0 and mingwang._jiamo_target is None


def test_jiamo_arrival_fallback_when_unparsed(monkeypatch):
    """锚词 OCR 变形致解析失败 → 「请在」后窗口含当前地名即到达。"""
    sig = "山河名望仙族兵士逃窜而去请在女儿村走一遭暗雷战斗"
    state = _patch_jiamo(monkeypatch, sig, ("女儿村", 9, 9))
    mingwang._reset_jiamo_state()
    assert mingwang._jiamo_target_map(sig) is None
    _jiamo_seed_stable(("女儿村", 9, 9))
    assert _jiamo_poll(state) is True
    assert mingwang._jiamo_target == "女儿村"
    assert state["land"] == 1 and mingwang._jiamo_phase == 1


def test_handle_dialog_options_calls_jiamo(monkeypatch):
    """extras 链接入：蛟魔复仇处理器命中即短路返回（前置处理器全部放行）。"""
    for name in ("_maybe_play_shahai", "_handle_wuzhuang_seal",
                 "_pick_mining_window_check", "_handle_lingligao",
                 "_handle_chuanyinshi"):
        monkeypatch.setattr(mingwang, name, lambda *a, **k: False)
    monkeypatch.setattr(mingwang, "_handle_jiamo_anlei",
                        lambda w, items, img: True)
    assert mingwang._handle_dialog_options(object(), [], None) is True


# ---- 领取链不做起飞/降落（2026-11-12 用户规则：领取链飞行逻辑已去掉）----
# ---- 小地图快捷领取（2026-11-16 用户规则：名望唯一领取路线）----
def test_claim_points_have_map_claim_positions():
    """两族都有小地图快捷领取**绝对坐标**（窗口位置固定，用户 2026-11-16
    规则：不做窗口锚点相对换算）。"""
    for race in ("魔族", "仙族"):
        claim = mingwang.CLAIM_POINTS[race]
        assert isinstance(claim["map_filter_pos"], tuple)
        assert isinstance(claim["map_label_pos"], tuple)
    # 2026-11-16 实机日志：魔族勾选框 (501,665)、黄色逆天魔 (989,380)；
    # 仙族黄色武尊神 (821,368)（勾选框按天宫锚点换算 (599,668)）
    assert mingwang.CLAIM_POINTS["魔族"]["map_filter_pos"] == (501, 665)
    assert mingwang.CLAIM_POINTS["魔族"]["map_label_pos"] == (989, 380)
    assert mingwang.CLAIM_POINTS["仙族"]["map_label_pos"] == (821, 368)


def test_map_filter_checked_white_pixels():
    """勾选框近白像素判定：勾选 13 vs 未勾选 0（实测，阈值 4）。"""
    img = Image.fromarray(np.zeros((40, 40, 3), dtype=np.uint8))
    assert not game_map.minimap_task_filter_checked(img, 20, 20)
    a = np.asarray(img).copy()
    a[17:23, 17:23] = 255  # 模拟白色对勾
    assert game_map.minimap_task_filter_checked(Image.fromarray(a), 20, 20)


def test_ensure_minimap_task_filter_clicks_when_unchecked(monkeypatch):
    """任务筛选未勾选 → 点击绝对坐标勾选框，复检勾选。"""
    win = _FakeWin([_pil(_noise_bgr(seed=1)), _pil(_noise_bgr(seed=2))])
    states = iter([False, True])
    monkeypatch.setattr(game_map, "minimap_task_filter_checked",
                        lambda img, cx, cy: next(states))
    clicks = []
    monkeypatch.setattr(game_map, "human_click",
                        lambda w, x, y, button="primary": clicks.append((x, y)))
    monkeypatch.setattr(game_map.time, "sleep", lambda s: None)
    ok, _info = game_map.ensure_minimap_task_filter(win, (501, 665))
    assert ok and clicks == [(501, 665)]


def test_ensure_minimap_task_filter_skips_when_checked(monkeypatch):
    """任务筛选已勾选 → 不点击（勾选状态零 OCR 判定）。"""
    win = _FakeWin([_pil(_noise_bgr(seed=1))])
    monkeypatch.setattr(game_map, "minimap_task_filter_checked",
                        lambda *a: True)
    clicks = []
    monkeypatch.setattr(game_map, "human_click",
                        lambda w, x, y, button="primary": clicks.append((x, y)))
    ok, _info = game_map.ensure_minimap_task_filter(win, (501, 665))
    assert ok and clicks == []


def test_minimap_quick_navigate_opens_scene_map_directly(monkeypatch):
    """已在目标地图 → 直接开小地图（不绕世界地图）→ 勾筛选 → 点黄字
    固定点（绝对坐标，只点一次）。"""
    order = []
    monkeypatch.setattr(game_map, "read_map_position",
                        lambda w: ("大唐边境", 475, 180))
    monkeypatch.setattr(game_map, "open_mini_map",
                        lambda w: order.append("mini") or (True, "小地图已打开"))
    monkeypatch.setattr(game_map, "open_mini_map_by_name",
                        lambda w, map_name:
                        order.append("city") or (True, "ok"))
    monkeypatch.setattr(game_map, "ensure_minimap_task_filter",
                        lambda w, pos:
                        order.append(("filter", pos)) or (True, "已勾选"))
    monkeypatch.setattr(game_map, "human_click",
                        lambda w, x, y, button="primary":
                        order.append(("point", (x, y))))
    ok, info = game_map.minimap_quick_navigate(
        object(), "大唐边境", (501, 665), (989, 380))
    assert ok and "已打开大唐边境小地图" in info
    assert order == ["mini", ("filter", (501, 665)),
                     ("point", (989, 380))]


def test_minimap_quick_navigate_uses_world_map_when_elsewhere(monkeypatch):
    """不在目标地图 → 经世界地图点城市标签打开小地图。"""
    order = []
    monkeypatch.setattr(game_map, "read_map_position",
                        lambda w: ("魔王寨", 62, 82))
    monkeypatch.setattr(game_map, "open_mini_map",
                        lambda w: order.append("mini") or (True, "ok"))
    monkeypatch.setattr(game_map, "open_mini_map_by_name",
                        lambda w, map_name:
                        order.append(("city", map_name)) or (True, "ok"))
    monkeypatch.setattr(game_map, "ensure_minimap_task_filter",
                        lambda w, pos: (True, "已勾选"))
    monkeypatch.setattr(game_map, "human_click",
                        lambda w, x, y, button="primary": None)
    ok, _info = game_map.minimap_quick_navigate(
        object(), "大唐边境", (501, 665), (989, 380))
    assert ok
    assert order == [("city", "大唐边境")]


def test_minimap_quick_navigate_stops_when_filter_fails(monkeypatch):
    """任务筛选勾选失败 → 不点击固定点，直接返回失败。"""
    clicked = []
    monkeypatch.setattr(game_map, "read_map_position", lambda w: None)
    monkeypatch.setattr(game_map, "open_mini_map_by_name",
                        lambda w, map_name: (True, "ok"))
    monkeypatch.setattr(game_map, "ensure_minimap_task_filter",
                        lambda w, pos: (False, "筛选点击后仍未勾选"))
    monkeypatch.setattr(game_map, "human_click",
                        lambda w, x, y, button="primary":
                        clicked.append((x, y)))
    ok, info = game_map.minimap_quick_navigate(
        object(), "大唐边境", (501, 665), (989, 380))
    assert not ok and "仍未勾选" in info and clicked == []


def test_navigate_to_claim_by_map_label_success(monkeypatch):
    """新路线：调用下沉到 map 的快捷导航（传入种族绝对坐标）。"""
    claim = mingwang.CLAIM_POINTS["魔族"]
    win = _FakeWin([_pil(_noise_bgr(seed=6))])
    calls = []
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "close_scene_panels",
                        lambda w, p="", quiet=False:
                        calls.append(("close", quiet)))
    monkeypatch.setattr(mingwang.game_map, "minimap_quick_navigate",
                        lambda w, map_name, fpos, lpos:
                        calls.append(("quick", map_name, fpos, lpos))
                        or (True, "已点击小地图固定点"))
    ok, info = mingwang.navigate_to_claim_by_map_label(win, claim)
    assert ok and "已点击" in info
    assert ("quick", claim["map"],
            claim["map_filter_pos"], claim["map_label_pos"]) in calls


def test_navigate_to_claim_by_map_label_failure_closes_map(monkeypatch):
    """标签点击失败 → 关地图并返回失败（调用方回退旧路线）。"""
    claim = mingwang.CLAIM_POINTS["魔族"]
    win = _FakeWin([_pil(_noise_bgr(seed=7))])
    calls = []
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "close_scene_panels",
                        lambda w, p="", quiet=False:
                        calls.append(("close", quiet)))
    monkeypatch.setattr(mingwang.game_map, "minimap_quick_navigate",
                        lambda *a, **k: (False, "未找到黄色标签"))
    ok, info = mingwang.navigate_to_claim_by_map_label(win, claim)
    assert not ok and "未找到黄色标签" in info
    assert ("close", True) in calls  # 失败清理地图（quiet）


def test_verify_map_claim_flow_only_uses_quick_route(monkeypatch):
    """诊断-魔族/仙族领取：仅走小地图快捷领取（不做种族判定/旧路线）。"""
    seen = []
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "_find_panel_anchor", lambda bgr: None)
    monkeypatch.setattr(mingwang, "detect_race",
                        lambda w: seen.append("race") or ("魔族", "x"))
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: seen.append(("map", c["map"]))
                        or (True, "已点击固定点"))

    def fake_claim(w, c, timeout=None):
        seen.append(("claim", c["map"], timeout))
        return True, "已打开山河名望任务领取面板"

    monkeypatch.setattr(mingwang, "_claim_and_open_panel", fake_claim)
    win = _FakeWin([_pil(_noise_bgr(seed=31))])
    for race in ("魔族", "仙族"):
        seen.clear()
        ok, info = mingwang.verify_map_claim_flow(win, race)
        assert ok and "面板" in info
        claim_map = mingwang.CLAIM_POINTS[race]["map"]
        assert seen == [("map", claim_map),
                        ("claim", claim_map,
                         mingwang.MAP_CLAIM_ARRIVE_TIMEOUT)]


def test_verify_map_claim_flow_unsupported_race(monkeypatch):
    """诊断-未配置种族（人族/鬼族）→ 明确提示未配置，不发任何点击。"""
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    called = []
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: called.append(c) or (True, "x"))
    win = _FakeWin([_pil(_noise_bgr(seed=32))])
    for race in ("人族", "鬼族"):
        ok, info = mingwang.verify_map_claim_flow(win, race)
        assert not ok and "未配置" in info
    assert called == []


def test_verify_map_claim_flow_panel_already_open(monkeypatch):
    """面板已打开 → 直接成功，不再打开地图/点黄字。"""
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "_find_panel_anchor", lambda bgr: (1, 2, 3, 4))
    called = []
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: called.append(1) or (True, "x"))
    win = _FakeWin([_pil(_noise_bgr(seed=33))])
    ok, info = mingwang.verify_map_claim_flow(win, "魔族")
    assert ok and "已打开" in info and called == []


def test_verify_map_claim_flow_quick_route_failure(monkeypatch):
    """小地图快捷领取失败 → 诊断返回失败（不落回旧路线）。"""
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "_find_panel_anchor", lambda bgr: None)
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: (False, "小地图窗口锚点未找到"))
    claimed = []
    monkeypatch.setattr(mingwang, "_claim_and_open_panel",
                        lambda *a, **k: claimed.append(1) or (True, "x"))
    win = _FakeWin([_pil(_noise_bgr(seed=34))])
    ok, info = mingwang.verify_map_claim_flow(win, "仙族")
    assert not ok and "锚点" in info and claimed == []


def test_open_claim_panel_uses_map_route(monkeypatch):
    """名望唯一领取路线：小地图快捷领取，入口链超时覆盖跨图寻路。"""
    order = []
    seen = {}
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "_find_panel_anchor", lambda bgr: None)
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: order.append("map") or (True, "已点击黄字"))

    def fake_claim(w, c, timeout=None):
        seen["timeout"] = timeout
        order.append("claim")
        return True, "已打开面板"

    monkeypatch.setattr(mingwang, "_claim_and_open_panel", fake_claim)
    monkeypatch.setattr(mingwang.debug_overlay, "set_phase", lambda p: None)
    win = _FakeWin([_pil(_noise_bgr(seed=22))])
    ok, info = mingwang._open_claim_panel(win, "魔族")
    assert ok and "面板" in info
    assert order == ["map", "claim"]
    assert seen["timeout"] == mingwang.MAP_CLAIM_ARRIVE_TIMEOUT


def test_open_claim_panel_no_fallback_when_map_route_fails(monkeypatch):
    """小地图快捷领取失败 → 直接返回失败（旧路线已移除，无回退）。"""
    order = []
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    monkeypatch.setattr(mingwang, "_find_panel_anchor", lambda bgr: None)
    monkeypatch.setattr(mingwang, "navigate_to_claim_by_map_label",
                        lambda w, c: order.append("map")
                        or (False, "未找到黄色标签"))
    monkeypatch.setattr(mingwang, "_claim_and_open_panel",
                        lambda *a, **k: order.append("claim") or (True, "x"))
    monkeypatch.setattr(mingwang.debug_overlay, "set_phase", lambda p: None)
    win = _FakeWin([_pil(_noise_bgr(seed=23))])
    ok, info = mingwang._open_claim_panel(win, "魔族")
    assert not ok and "黄色标签" in info
    assert order == ["map"]  # 不进入入口链


# ---- 天宫奇遇（仙族·天宫）：导航链接 / 1px 到达 / 偷丹葫芦 / 魔取仙丹 ----
def test_tgqy_at_target_one_px_tolerance():
    """到达判定：天宫(13,99) ±1px（用户指定），跨地图不算到达。"""
    assert mingwang._tgqy_at_target(("天宫", 13, 99))
    assert mingwang._tgqy_at_target(("天宫 ", 12, 98))
    assert mingwang._tgqy_at_target(("天宫", 14, 100))
    assert not mingwang._tgqy_at_target(("天宫", 15, 99))
    assert not mingwang._tgqy_at_target(("天宫", 13, 97))
    assert not mingwang._tgqy_at_target(("长安城", 13, 99))
    assert not mingwang._tgqy_at_target(None)


def test_tgqy_trigger_keywords():
    assert mingwang._tgqy_trigger("前往老君丹房门口，使用偷丹葫芦。")
    assert mingwang._tgqy_trigger("老君丹房")   # 短词兜底 OCR 截断
    assert not mingwang._tgqy_trigger("五庄封印火藏之位")


def _tgqy_link_frame():
    """追踪行「前往老君丹房门口，使用偷丹」+ 两段亮绿下划线。

    行框 22..221 共 13 字：老君丹房门口=第 2..7 字，偷丹=第 11..12 字。
    返回 (RGB 帧, items, 两段下划线起止 x)。
    """
    img = np.zeros((320, 400, 3), np.uint8)

    def x(char_idx):
        return int(22 + (221 - 22) * char_idx / 13)

    img[268:270, x(2):x(8)] = (0, 255, 0)    # 老君丹房门口（可导航）
    img[268:270, x(11):x(13)] = (0, 255, 0)  # 偷丹（物品说明，不可点）
    items = [("前往老君丹房门口，使用偷丹", [22, 251, 221, 272], 0.96)]
    return img, items, x(2), x(8), x(11)


def test_tgqy_link_point_prefers_nav_link(monkeypatch):
    """同行两个绿字：定位「老君丹房门口」，不点「偷丹葫芦」。"""
    monkeypatch.setattr(mingwang.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    img, items, l_nav, r_nav, l_item = _tgqy_link_frame()
    pt = mingwang._tgqy_link_point(items, img)
    assert pt is not None
    assert l_nav <= pt[0] <= r_nav
    assert pt[0] < l_item


def test_mingwang_green_picker_includes_tgqy(monkeypatch):
    """组合 green_picker：五庄/灵力镐未命中时回退到天宫奇遇导航链接。"""
    monkeypatch.setattr(mingwang.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    img, items, l_nav, r_nav, _l_item = _tgqy_link_frame()
    pt = mingwang._mingwang_green_picker(items, img)
    assert pt is not None and l_nav <= pt[0] <= r_nav


def test_handle_tiangong_qiyu_uses_item_after_stable_target(monkeypatch):
    """已在目标点：连续 3 次稳定 → 任务储物格使用偷丹葫芦 → phase 1。"""
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("天宫", 13, 99))
    monkeypatch.setattr(mingwang, "_seal_dialog_blocking",
                        lambda *a: (False, False))
    used = []
    monkeypatch.setattr(mingwang, "_use_toudanhulu",
                        lambda w: used.append(1) or (True, "已使用偷丹葫芦"))
    armed = []
    monkeypatch.setattr(mingwang, "_arm_tgqy_game_check",
                        lambda: armed.append(1))
    items = [("前往老君丹房门口，使用偷丹", [22, 251, 221, 272], 0.96),
             ("葫芦", [23, 271, 59, 285], 0.99)]
    assert mingwang._handle_tiangong_qiyu(object(), items, None) is False
    assert mingwang._handle_tiangong_qiyu(object(), items, None) is False
    assert mingwang._handle_tiangong_qiyu(object(), items, None) is True
    assert used == [1] and armed == [1]
    assert mingwang._tgqy_phase == 1
    assert mingwang._tgqy_use_at > 0


def test_handle_tiangong_qiyu_clicks_link_when_not_at_target(monkeypatch):
    """不在目标点：点「老君丹房门口」导航链接开始寻路（不点物品链接）。"""
    monkeypatch.setattr(mingwang.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("天宫", 190, 122))
    img, items, l_nav, r_nav, _ = _tgqy_link_frame()
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    assert mingwang._handle_tiangong_qiyu(object(), items, img) is True
    assert len(clicks) == 1 and l_nav <= clicks[0][0] <= r_nav
    assert mingwang._tgqy_need_move is True


def test_maybe_play_tgqy_detects_and_plays(monkeypatch):
    """armed 窗口命中魔取仙丹标题 → 阻塞游玩；成功 → disarmed + phase 2。"""
    monkeypatch.setattr(mingwang, "_tgqy_game_armed_at", time.time())
    monkeypatch.setattr(mingwang, "is_in_battle", lambda w: False)
    tpl = cv2.imread(R.img(moqu.MOQUSHENDAN_TITLE_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=20261114)
    frame[310:310 + th, 700:700 + tw] = tpl
    win = _FakeWin([_pil(frame)])
    played = []
    monkeypatch.setattr(moqu, "play_moqushendan",
                        lambda w, prefix="名望":
                        played.append(prefix) or (True, "通过"))
    assert mingwang._maybe_play_tgqy(win) is True
    assert played == ["名望"]
    assert mingwang._tgqy_game_armed_at == 0.0
    assert mingwang._tgqy_phase == 2


def test_maybe_play_tgqy_not_armed_noop(monkeypatch):
    """未 arm：零操作，不截图。"""
    monkeypatch.setattr(mingwang, "is_in_battle",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("未 arm 不应检测")))
    assert mingwang._maybe_play_tgqy(object()) is False


# ---- 魔取仙丹小游戏：金色亮起判定 / 演示顺序录制 / 按钮三态 ----
def _moqu_rgb_frame(lit_names=()):
    """合成 RGB 帧：指定葫芦 ROI 填金色（亮起），其余暗金（未亮）。"""
    frame = np.zeros((640, 800, 3), np.uint8)
    for g in moqu.MOQUSHENDAN_GOURDS:
        l, t, r, b = g["roi"]
        frame[t:b, l:r] = ((240, 210, 120) if g["name"] in lit_names
                           else (120, 110, 90))
    return frame


def test_moqu_gourd_lit_map_gold_threshold():
    assert not any(moqu.gourd_lit_map(_moqu_rgb_frame()).values())
    lit = moqu.gourd_lit_map(_moqu_rgb_frame(("右",)))
    assert lit == {"上": False, "右": True, "左": False, "左下": False}


def test_moqu_record_demo_first_lit_order(monkeypatch):
    """按各葫芦首次亮起记录顺序，全部熄灭即演示结束。"""
    monkeypatch.setattr(moqu.time, "sleep", lambda s: None)
    order = ("上", "右", "左", "左下")
    frames, lit = [], set()
    for name in order:
        lit.add(name)
        frames.append(Image.fromarray(_moqu_rgb_frame(lit)))
    frames.append(Image.fromarray(_moqu_rgb_frame()))  # 演示结束：全灭
    win = _FakeWin(frames)
    seq, info = moqu._record_demo(win)
    assert seq == list(order), info


def test_moqu_record_demo_timeout(monkeypatch):
    """演示始终未结束（一直亮）→ 返回 None。"""
    monkeypatch.setattr(moqu.time, "sleep", lambda s: None)
    monkeypatch.setattr(moqu, "MOQUSHENDAN_DEMO_TIMEOUT", 0.0)
    win = _FakeWin([Image.fromarray(_moqu_rgb_frame(("上",)))])
    seq, info = moqu._record_demo(win)
    assert seq is None and "超时" in info


@pytest.mark.parametrize("text,label", (
    ("观看", "观看"),
    ("观看中倒计时：99秒", "观看中"),   # 「观看中」优先于子串「观看」
    ("确认倒计时：94秒", "确认"),
    ("倒计时：99秒", ""),
))
def test_moqu_button_label_three_states(monkeypatch, text, label):
    monkeypatch.setattr(moqu.game_map, "ocr_region",
                        lambda img, region:
                        ([("按钮", [0, 0, 1, 1], 0.9),
                          (text, [0, 0, 2, 2], 0.99)], None))
    got, _texts = moqu._button_label(object())
    assert got == label


def test_moqu_template_present_and_absent():
    tpl = cv2.imread(R.img(moqu.MOQUSHENDAN_TITLE_ICON), cv2.IMREAD_COLOR)
    th, tw = tpl.shape[:2]
    frame = _noise_bgr(seed=7)
    frame[310:310 + th, 700:700 + tw] = tpl
    assert moqu.moqushendan_present(frame)
    assert not moqu.moqushendan_present(_noise_bgr(seed=8))


def test_play_moqu_click_order(monkeypatch):
    """一局通过：观看 → 确认 → 按演示顺序点 4 葫芦 → 确认提交。"""
    monkeypatch.setattr(moqu.time, "sleep", lambda s: None)
    img = Image.fromarray(_moqu_rgb_frame(
        ("上", "右", "左", "左下")))
    win = _FakeWin([img])
    seq = ["上", "右", "左", "左下"]
    monkeypatch.setattr(moqu, "_wait_button_label",
                        lambda w, timeout=moqu.MOQUSHENDAN_BTN_TIMEOUT:
                        ("观看", img, ""))
    monkeypatch.setattr(moqu, "_record_demo",
                        lambda w, prefix="": (seq, "演示顺序 上→右→左→左下"))
    monkeypatch.setattr(moqu, "_window_closed", lambda w: (True, img))
    clicks = []
    monkeypatch.setattr(moqu, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y)))
    ok, info = moqu.play_moqushendan(win, prefix="名望")
    assert ok and "演示顺序" in info
    expected = [moqu.MOQUSHENDAN_BTN_POS,          # 观看
                moqu.MOQUSHENDAN_BTN_POS]          # 确认进入输入阶段
    expected += [moqu._gourd(n)["click"] for n in seq]
    expected.append(moqu.MOQUSHENDAN_BTN_POS)      # 提交
    assert clicks == expected


def test_seal_phase1_advances_dialog_before_retry(monkeypatch):
    """用符后链接未变红但对话框常驻：先推进对话再考虑重试用符。

    2026-11-17 实机：战后任务台词常驻 + story 兜底误判寻路中，
    重试用符被「对话框在场」拒绝、链接永不变红，空转 3 分钟到超时。
    """
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_use_at = time.time() - 30
    frame = np.zeros((831, 1378, 3), dtype=np.uint8)
    monkeypatch.setattr(mingwang, "_seal_frame",
                        lambda w, items: (None, frame))
    monkeypatch.setattr(mingwang, "dialog_frame_score", lambda bgr: 0.99)
    monkeypatch.setattr(mingwang, "_seal_pos",
                        lambda w, items: ("五庄观", 160, 50))
    monkeypatch.setattr(mingwang, "_seal_link_color",
                        lambda items, img, kw: "green")
    monkeypatch.setattr(mingwang, "_use_huanfengfu",
                        lambda w: (_ for _ in ()).throw(
                            AssertionError("对话在场不应重试用符")))
    clicked = []
    monkeypatch.setattr(mingwang, "_click_screen_center",
                        lambda w, times=1, prefix="": clicked.append(times))
    items = [("跑得真快，但是一瞬间，那人的人皮怎么好像掉了",
              [500, 300, 940, 330], 0.98)]
    for _ in range(3):
        assert mingwang._seal_check_outcome(
            object(), items, frame, time.time()) is True
    assert clicked == []
    assert mingwang._seal_check_outcome(
        object(), items, frame, time.time()) is True
    assert clicked == [1]
    mingwang._reset_seal_state()


def _seal_dynamic_env(monkeypatch, colors, pos=("五庄观", 100, 100)):
    """动态元素状态机测试环境：颜色按关键词桩、点击记录。"""
    items = [
        ("[日常]五庄封印[山河名望]", [14, 232, 204, 252], 0.97),
        ("在五庄观找到金藏之位和土藏", [22, 251, 222, 271], 1.0),
        ("之位的风暴封印处，使用唤风", [23, 267, 222, 288], 1.0),
    ]
    monkeypatch.setattr(mingwang, "_seal_frame", lambda w, it: (None, None))
    monkeypatch.setattr(mingwang, "_seal_pos", lambda w, it: pos)
    monkeypatch.setattr(mingwang, "_seal_link_color",
                        lambda it, img, kws: colors.get(kws[0]) if kws else None)
    clicked = []
    monkeypatch.setattr(mingwang, "_click_seal_link",
                        lambda w, it, img, link_idx=None:
                        clicked.append(link_idx) or True)
    return items, clicked


def test_seal_dynamic_elements_advance_to_other(monkeypatch):
    """实机回归（2026-11-17）：追踪为「金藏+土藏」，金藏变红 → 改点土藏。

    旧版写死火藏/木藏：颜色判定全部落空 → 日志一直报火藏 → 空转到超时。
    """
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_use_at = time.time() - 999
    items, clicked = _seal_dynamic_env(
        monkeypatch, {"金藏": "red", "全藏": "red",
                      "土藏": "green", "士藏": "green"})
    assert mingwang._handle_wuzhuang_seal(object(), items, None) is True
    assert clicked == [1]
    assert mingwang._seal_elems == ["金", "土"]
    assert mingwang._seal_link_idx == 1
    mingwang._reset_seal_state()


def test_seal_left_map_with_green_link_advances(monkeypatch):
    """坐标离开五庄观但当前链接仍绿：按「本点完成」改点另一个（不是都完成）。"""
    mingwang._reset_seal_state()
    mingwang._seal_phase = 1
    mingwang._seal_link_idx = 0
    mingwang._seal_use_at = time.time() - 999
    items, clicked = _seal_dynamic_env(
        monkeypatch, {"金藏": "green", "全藏": "green",
                      "土藏": "green", "士藏": "green"},
        pos=("长安", 10, 20))
    assert mingwang._handle_wuzhuang_seal(object(), items, None) is True
    assert clicked == [1]
    assert mingwang._seal_phase == 0
    mingwang._reset_seal_state()


# ---- 任务栏刷新（用户 2026-11-21 规则：无已实现任务时刷新卡片）----
def _cards_with_titles(titles):
    """按实机卡片矩形构造指定标题的卡片列表（titles: {(row,col): 标题}）。"""
    rects = _live_rects()
    return [{"row": r, "col": c, "rect": rects[(r, c)],
             "level": "高级", "title": title}
            for (r, c), title in titles.items()]


def _patch_refresh_state(monkeypatch):
    """角色状态内存桩（隔离 user_data 文件）。"""
    store = {}
    monkeypatch.setattr(
        mingwang.app_utils, "char_state_get",
        lambda key, win, default=None: store.get(key, default))
    monkeypatch.setattr(
        mingwang.app_utils, "char_state_set",
        lambda key, win, value: store.__setitem__(key, value))
    return store


def test_parse_refresh_used():
    """顶部计数「任务已刷新：X/3」解析（整行/拆段/低置信度/无匹配）。"""
    assert mingwang._parse_refresh_used(
        [("今日已完成：3/5 任务已刷新：0/3",
          [393, 261, 661, 282], 0.999)]) == 0
    assert mingwang._parse_refresh_used(
        [("今日已完成：3/5", [393, 261, 520, 282], 1.0),
         ("任务已刷新：2/3", [530, 261, 661, 282], 1.0)]) == 2
    assert mingwang._parse_refresh_used(
        [("任务已刷新：1／3", [530, 261, 661, 282], 0.8)]) == 1
    assert mingwang._parse_refresh_used([]) is None
    assert mingwang._parse_refresh_used(
        [("任务已刷新：X/3", [530, 261, 661, 282], 0.4)]) is None


def test_refresh_header_strip():
    """计数条带：锚点左缘向右 310px、锚点上下区域内。"""
    assert mingwang._refresh_header_strip((392, 252, 425, 287),
                                          (1378, 831)) == (392, 256, 702, 287)
    assert mingwang._refresh_header_strip((1300, 500, 1330, 540),
                                          (1378, 831)) == (1300, 504, 1378, 540)


def test_card_refresh_point_live_offset():
    """实机偏移：按钮中心 = 卡片右缘 -17 / 顶缘 +24；无按钮卡不命中。"""
    tpl = cv2.imread(R.img(mingwang.REFRESH_ICON), cv2.IMREAD_COLOR)
    frame = _noise_bgr(seed=20261122)
    frame[300:300 + tpl.shape[0], 551:551 + tpl.shape[1]] = tpl
    pt, score = mingwang._card_refresh_point(frame, (392, 290, 582, 444))
    assert pt == (565, 314) and score >= mingwang.REFRESH_THRESHOLD
    assert mingwang._card_refresh_point(
        _noise_bgr(seed=20261123), (794, 451, 984, 605))[0] is None


def test_refresh_state_roundtrip_and_cross_day(monkeypatch):
    """当天状态往返；跨天自动重置（每角色每天 3 次）。"""
    store = _patch_refresh_state(monkeypatch)
    win = object()
    today = time.strftime("%Y-%m-%d")
    assert mingwang._load_refresh_state(win) == {
        "date": today, "used": 0, "no_support": False}
    assert mingwang._refresh_skip_reason(win) is None
    mingwang._save_refresh_state(win, used=3, no_support=True)
    assert mingwang._load_refresh_state(win)["used"] == 3
    assert "今日不再领取" in mingwang._refresh_skip_reason(win)
    # 未满 3 次 / 有已实现任务：不触发跳过门槛
    store[mingwang.REFRESH_STATE_KEY] = {
        "date": today, "used": 2, "no_support": True}
    assert mingwang._refresh_skip_reason(win) is None
    store[mingwang.REFRESH_STATE_KEY] = {
        "date": today, "used": 3, "no_support": False}
    assert mingwang._refresh_skip_reason(win) is None
    # 跨天重置
    store[mingwang.REFRESH_STATE_KEY] = {
        "date": "2000-01-01", "used": 3, "no_support": True}
    assert mingwang._load_refresh_state(win)["used"] == 0
    assert mingwang._refresh_skip_reason(win) is None


def test_refresh_skip_gate_in_run_loop(monkeypatch):
    """刷新用尽且无已实现任务 → 任务启动直接跳过，不判种族/不领取。"""
    monkeypatch.setattr(mingwang, "load_task_counts", lambda: {"名望": 5})
    monkeypatch.setattr(mingwang.app_utils, "task_progress_done",
                        lambda w, m: 0)
    monkeypatch.setattr(mingwang, "_task_in_tracker", lambda w: False)
    monkeypatch.setattr(
        mingwang, "_refresh_skip_reason",
        lambda w: "今日任务已刷新 3/3 次且无可领取的已实现任务，"
                  "今日不再领取名望任务")
    monkeypatch.setattr(
        mingwang, "_detect_race_with_retries",
        lambda w: (_ for _ in ()).throw(AssertionError("跳过时不应判种族")))
    monkeypatch.setattr(
        mingwang, "run_cycle",
        lambda w, race=None: (_ for _ in ()).throw(
            AssertionError("跳过时不应领取")))
    info = mingwang.run_loop(object())
    assert "今日不再领取" in info


def test_refresh_until_supported_clicks_and_finds(monkeypatch):
    """无已实现任务 → 点第一张可刷新卡 → 刷新后识别出现已实现任务。"""
    store = _patch_refresh_state(monkeypatch)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 0)
    monkeypatch.setattr(mingwang, "_card_refresh_point",
                        lambda bgr, rect: ((565, 314), 1.0))
    monkeypatch.setattr(
        mingwang, "_wait_refresh_applied",
        lambda w, a, card, used: (True, used + 1,
                                  f"刷新计数 {used}→{used + 1}/3"))
    before = _cards_with_titles({(0, 0): "安抚鹦鹉", (0, 1): "鬼祟仙族"})
    after = _cards_with_titles({(0, 0): "蛟魔复仇", (0, 1): "鬼祟仙族"})
    monkeypatch.setattr(
        mingwang, "_scan_panel_cards",
        lambda w, timeout=None: (after, LIVE_ANCHOR, _pil(_live_frame(True))))
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    cards, _anchor, _img, info = mingwang._refresh_until_supported(
        object(), LIVE_ANCHOR, before, _pil(_live_frame(True)))
    assert cards is not None and "蛟魔复仇" in info
    assert clicks == [(565, 314, "primary")]
    state = store[mingwang.REFRESH_STATE_KEY]
    assert state["used"] == 1 and state["no_support"] is False


def test_refresh_until_supported_retries_on_next_card(monkeypatch):
    """第一次刷新仍无已实现任务 → 换下一张卡刷新（最多 3 次）。"""
    store = _patch_refresh_state(monkeypatch)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 0)
    monkeypatch.setattr(mingwang, "_card_refresh_point",
                        lambda bgr, rect: ((565, 314), 1.0))
    monkeypatch.setattr(
        mingwang, "_wait_refresh_applied",
        lambda w, a, card, used: (True, used + 1, "刷新生效"))
    before = _cards_with_titles({(0, 0): "安抚鹦鹉", (0, 1): "鬼祟仙族"})
    mid = _cards_with_titles({(0, 0): "奇异蜘蛛", (0, 1): "鬼祟仙族"})
    after = _cards_with_titles({(0, 0): "奇异蜘蛛", (0, 1): "沙海迷踪"})
    scans = [mid, mid, after]  # 第一次刷新后首扫+复扫均无，第二次刷新后命中
    monkeypatch.setattr(mingwang, "_scan_panel_cards",
                        lambda w, timeout=None: (scans.pop(0), LIVE_ANCHOR,
                                                 _pil(_live_frame(True))))
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    cards, _anchor, _img, info = mingwang._refresh_until_supported(
        object(), LIVE_ANCHOR, before, _pil(_live_frame(True)))
    assert cards is not None and "沙海迷踪" in info
    assert len(clicks) == 2
    assert store[mingwang.REFRESH_STATE_KEY]["used"] == 2


def test_refresh_until_supported_exhausted_marks_no_support(monkeypatch):
    """最后一次刷新后仍无已实现任务 → 记录 no_support（下次启动跳过）。"""
    store = _patch_refresh_state(monkeypatch)
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 2)
    monkeypatch.setattr(mingwang, "_card_refresh_point",
                        lambda bgr, rect: ((565, 314), 1.0))
    monkeypatch.setattr(mingwang, "_wait_refresh_applied",
                        lambda w, a, card, used: (True, 3, "刷新计数 2→3/3"))
    before = _cards_with_titles({(0, 0): "安抚鹦鹉", (0, 1): "鬼祟仙族"})
    monkeypatch.setattr(
        mingwang, "_scan_panel_cards",
        lambda w, timeout=None: (before, LIVE_ANCHOR, _pil(_live_frame(True))))
    monkeypatch.setattr(mingwang, "human_click", lambda *a, **k: None)
    cards, _anchor, _img, info = mingwang._refresh_until_supported(
        object(), LIVE_ANCHOR, before, _pil(_live_frame(True)))
    assert cards is None and "刷新次数已用尽" in info
    state = store[mingwang.REFRESH_STATE_KEY]
    assert state["used"] == 3 and state["no_support"] is True


def test_refresh_until_supported_already_exhausted(monkeypatch):
    """进入刷新链时计数已 3/3 → 不点击，直接记录 no_support。"""
    store = _patch_refresh_state(monkeypatch)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 3)
    monkeypatch.setattr(
        mingwang, "human_click",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("次数已用尽不应点击刷新")))
    before = _cards_with_titles({(0, 0): "安抚鹦鹉"})
    cards, _anchor, _img, info = mingwang._refresh_until_supported(
        object(), LIVE_ANCHOR, before, _pil(_live_frame(True)))
    assert cards is None and "3/3" in info
    assert store[mingwang.REFRESH_STATE_KEY]["no_support"] is True


def test_refresh_until_supported_no_icon_no_click(monkeypatch):
    """找不到刷新按钮（面板变体/未渲染）：不点击、不误记 no_support。"""
    store = _patch_refresh_state(monkeypatch)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 0)
    monkeypatch.setattr(mingwang, "_card_refresh_point",
                        lambda bgr, rect: (None, 0.3))
    monkeypatch.setattr(
        mingwang, "human_click",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("无刷新按钮不应点击")))
    before = _cards_with_titles({(0, 0): "安抚鹦鹉"})
    cards, _anchor, _img, info = mingwang._refresh_until_supported(
        object(), LIVE_ANCHOR, before, _pil(_live_frame(True)))
    assert cards is None and "未找到可点击的刷新按钮" in info
    assert (mingwang.REFRESH_STATE_KEY not in store
            or not store[mingwang.REFRESH_STATE_KEY]["no_support"])


def test_claim_panel_task_claims_after_refresh(monkeypatch):
    """刷新链返回已实现任务 → 正常点击该卡接取按钮领取。"""
    _patch_cards_with(monkeypatch, {
        (0, 0): [("高級", [399, 298, 422, 338], 0.9),
                 ("黑水蛟王", [458, 307, 537, 333], 1.0),
                 ("接取", [462, 409, 514, 437], 1.0)],
        (1, 0): [("高級", [399, 459, 422, 499], 0.9),
                 ("黑水精怪", [460, 470, 535, 493], 1.0),
                 ("接取", [463, 571, 513, 596], 1.0)],
    })
    supported = _cards_with_titles({(0, 0): "蛟魔复仇"})
    monkeypatch.setattr(
        mingwang, "_refresh_until_supported",
        lambda w, anchor, cards, img:
        (supported, LIVE_ANCHOR, _pil(_live_frame(True)),
         "刷新后出现已实现任务「蛟魔复仇」"))
    clicks = []
    monkeypatch.setattr(mingwang, "human_click",
                        lambda w, x, y, button="primary":
                        clicks.append((x, y, button)))
    win = _FakeWin([
        _pil(_live_frame(True)),
        _pil(_live_frame(True, skip_buttons=((487, 423),))),
    ])
    status, info = mingwang.claim_panel_task(win)
    assert status == "claimed" and "蛟魔复仇" in info
    assert clicks == [(487, 423, "primary")]


def test_claim_panel_task_refresh_not_found_includes_info(monkeypatch):
    """刷新链未找到任务 → not_found 描述带刷新结论（供日志定位）。"""
    _patch_cards_with(monkeypatch, {
        (0, 0): [("高級", [399, 298, 422, 338], 0.9),
                 ("黑水蛟王", [458, 307, 537, 333], 1.0),
                 ("接取", [462, 409, 514, 437], 1.0)],
        (1, 0): [("高級", [399, 459, 422, 499], 0.9),
                 ("黑水精怪", [460, 470, 535, 493], 1.0),
                 ("接取", [463, 571, 513, 596], 1.0)],
    })
    monkeypatch.setattr(
        mingwang, "_refresh_until_supported",
        lambda w, anchor, cards, img:
        (None, None, None, "今日刷新次数已用尽（3/3），今日不再领取名望任务"))
    monkeypatch.setattr(
        mingwang, "human_click",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应点击")))
    win = _FakeWin([_pil(_live_frame(True))])
    status, info = mingwang.claim_panel_task(win)
    assert status == "not_found" and "刷新次数已用尽" in info


def test_wait_refresh_applied_by_count(monkeypatch):
    """刷新生效主信号：顶部计数增加（权威）。"""
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: 1)
    win = _FakeWin([_pil(_noise_bgr(seed=20261124))])
    card = {"row": 0, "col": 0, "rect": (392, 290, 582, 444),
            "level": "高级", "title": "安抚鹦鹉"}
    applied, used, desc = mingwang._wait_refresh_applied(
        win, LIVE_ANCHOR, card, 0)
    assert applied and used == 1 and "0→1" in desc


def test_wait_refresh_applied_by_title_change(monkeypatch):
    """计数 OCR 失败时，卡片标题变化作为生效兜底信号。"""
    monkeypatch.setattr(mingwang.time, "sleep", lambda s: None)
    monkeypatch.setattr(mingwang, "_read_refresh_used", lambda img, a: None)
    monkeypatch.setattr(
        mingwang.game_map, "ocr_region",
        lambda img, rect: ([("蛟魔复仇", [458, 307, 537, 333], 1.0)], None))
    win = _FakeWin([_pil(_noise_bgr(seed=20261125))])
    card = {"row": 0, "col": 0, "rect": (392, 290, 582, 444),
            "level": "高级", "title": "安抚鹦鹉"}
    applied, used, desc = mingwang._wait_refresh_applied(
        win, LIVE_ANCHOR, card, 0)
    assert applied and used == 1 and "蛟魔复仇" in desc


def test_wait_refresh_applied_unconfirmed(monkeypatch):
    """超时无变化 → 未确认（调用方不得重复点击，防白耗次数）。"""
    monkeypatch.setattr(mingwang, "REFRESH_APPLY_TIMEOUT", 0.0)
    applied, used, desc = mingwang._wait_refresh_applied(
        object(), LIVE_ANCHOR, {"row": 0, "col": 0,
                                "rect": (392, 290, 582, 444),
                                "level": "高级", "title": "安抚鹦鹉"}, 1)
    assert not applied and used == 1 and "未确认" in desc
