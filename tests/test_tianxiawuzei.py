# -*- coding: utf-8 -*-
"""天下无贼（300环）小游戏：检测/分类/幽灵抑制与失败重试驱动。

覆盖核心路径：黄色计分条开场门、候选过滤、直方图分类、幽灵候选抑制、
任务侧「对局结束但未推进 → 重走追踪链接」的状态机。
"""
import numpy as np

from src.minigames import tianxiawuzei as txwz
from src.tasks import sanbaihuan


def _frame():
    return np.zeros((831, 1378, 3), np.uint8)


def test_arena_started_yellow_gate():
    frame = _frame()
    assert not txwz.arena_started(frame)
    l, t, r, b = txwz.SCORE_REGION
    frame[t:b, l:r] = (0, 255, 255)  # BGR 黄色（R=G=255）
    assert txwz.arena_started(frame)
    # 规则面板期只露出少量金边（实测 160 像素）不判开场
    panel = _frame()
    panel[t:t + 4, l:l + 40] = (0, 255, 255)  # 160 像素
    assert not txwz.arena_started(panel)


def test_score_bar_gone_ratio():
    bg = _frame()
    l, t, r, b = txwz.SCORE_REGION
    bg[t:b, l:r] = 60                      # 深色计分条
    assert not txwz.score_bar_gone(bg, bg)  # 稳定对局：区域与背景全等
    assert not txwz.score_bar_gone(None, bg)
    digits = bg.copy()
    digits[t + 10:t + 40, l + 20:l + 90] = 255  # 分数/倒计时数字变化（局部）
    assert not txwz.score_bar_gone(digits, bg)
    gone = bg.copy()
    gone[t:b, l:r] = 220                   # 条消失（露出天空）/被对话框盖住
    assert txwz.score_bar_gone(gone, bg)


def test_play_ends_when_score_bar_gone(monkeypatch):
    """达标结束无对话框：计分条消失即判结束，不白等到 65s 超时。

    2026-11-27 22:35 实机：任务已推进下一环，仅靠对话框模板的结束
    判据等不到对话框，白等到超时。
    """
    bg = _frame()
    l, t, r, b = txwz.SCORE_REGION
    bg[t:b, l:r] = (0, 255, 255)           # 计分条黄色字（BGR）
    gone = bg.copy()
    gone[t:b, l:r] = 220                   # 条消失
    seq = [bg] + [bg] * 3 + [gone] * 4
    state = {"i": 0}

    class W:
        def capture(self, **kw):
            i = min(state["i"], len(seq) - 1)
            state["i"] += 1
            return seq[i].copy()

    monkeypatch.setattr(txwz, "_load_refs",
                        lambda: {"qiangdao": [np.full(512, 1 / 512, np.float32)]})
    monkeypatch.setattr(txwz, "dialog_frame_score", lambda frame: 0.0)
    monkeypatch.setattr(txwz, "_read_round_score", lambda win: "")
    monkeypatch.setattr(txwz, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(txwz, "END_BAR_MIN_ELAPSED", 0.0)
    monkeypatch.setattr(txwz, "POLL_INTERVAL", 0.001)
    ok, info = txwz.play(W(), prefix="test")
    assert ok and "对局结束" in info
    assert state["i"] <= len(seq)       # 计分条消失 2 轮即停，未走满超时


def test_blobs_area_and_size_filters():
    import cv2
    bg = _frame()
    frame = bg.copy()
    frame[400:480, 500:560] = (255, 0, 0)  # 60x80 色块（面积 4800）
    blobs = txwz._blobs(frame, bg)
    assert len(blobs) == 1
    x, y, w, h = blobs[0]
    assert abs(x - 500) <= 2 and abs(y - 400) <= 2
    assert abs(w - 60) <= 4 and abs(h - 80) <= 4
    # 小噪点（面积 < 400）被过滤
    small = bg.copy()
    small[400:404, 500:504] = 255
    assert txwz._blobs(small, bg) == []


def test_quantize_and_classify_pick_matching_type():
    blue = np.array([[255, 0, 0]] * 100, np.uint8)   # BGR 纯蓝
    red = np.array([[0, 0, 255]] * 100, np.uint8)    # BGR 纯红
    hist_blue = txwz._quantize(blue)
    hist_red = txwz._quantize(red)
    assert hist_blue is not None and abs(hist_blue.sum() - 1.0) < 1e-4
    assert txwz._quantize(np.zeros((5, 3), np.uint8)) is None
    refs = {"blue": [hist_blue], "red": [hist_red]}
    bg = _frame()
    frame = bg.copy()
    frame[400:480, 500:560] = (255, 0, 0)  # 蓝色 sprite
    label, score, second = txwz.classify(frame, bg, (500, 400, 60, 80), refs)
    assert label == "blue"
    assert score > 0.99 and second < 0.01


def test_note_candidate_ghost_suppression():
    crop = np.zeros((40, 30, 3), np.uint8)
    crop[10:30, 10:20] = (200, 150, 100)
    key = (3, 4)
    state, ghosts = {}, set()
    assert not txwz._note_candidate(state, ghosts, key, crop, 0.0)
    assert not txwz._note_candidate(state, ghosts, key, crop, 1.0)
    assert txwz._note_candidate(state, ghosts, key, crop, 1.3)  # 静止超时→幽灵
    assert key in ghosts
    # 像素变化（真怪动画，整块变化）重置静止计时
    state2, ghosts2 = {}, set()
    assert not txwz._note_candidate(state2, ghosts2, key, crop, 0.0)
    moved = crop.copy()
    moved[0:30, 0:20] = (100, 100, 100)
    assert not txwz._note_candidate(state2, ghosts2, key, moved, 1.0)
    assert not txwz._note_candidate(state2, ghosts2, key, moved, 2.0)
    assert txwz._note_candidate(state2, ghosts2, key, moved, 2.6)  # 复位后超时


def _items(panel_text="", dialog_text=""):
    items = []
    if panel_text:
        items.append((panel_text, [20, 240, 300, 260], 0.99))
    if dialog_text:
        items.append((dialog_text, [450, 380, 900, 400], 0.99))
    return items


def test_txwz_intro_starts_minigame(monkeypatch):
    calls = []
    monkeypatch.setattr(sanbaihuan.txwz, "play",
                        lambda win, prefix="": calls.append(prefix) or (True, "ok"))
    sanbaihuan._TXWZ_STATE.update(ended_at=0.0, retry_at=0.0, retries=0)
    items = _items("天下无贼[江湖历练任务]",
                   "拿起大锤，敲打江湖大盗，强盗和强盗僵尸吧")
    assert sanbaihuan._txwz_tick(None, items, None, object())
    assert calls and sanbaihuan._TXWZ_STATE["ended_at"] > 0


def test_txwz_retry_state_machine(monkeypatch):
    import time
    retried = []
    monkeypatch.setattr(sanbaihuan.task_tracker, "track_task",
                        lambda win, title, **kw: retried.append(title) or (True, "ok"))
    monkeypatch.setattr(sanbaihuan, "dialog_frame_score", lambda img: 0.2)
    monkeypatch.setattr(sanbaihuan.txwz, "play",
                        lambda win, prefix="": (True, "ok"))
    ring = "天下无贼[江湖历练任务]"
    p = sanbaihuan._TXWZ_STATE
    # 1) 从未对局：不重试
    p.update(ended_at=0.0, retry_at=0.0, retries=0, battle_seen=False)
    assert not sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert retried == []
    # 2) 对局刚结束（间隔不足）：不重试
    p.update(ended_at=time.time(), retry_at=0.0, retries=0)
    assert not sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert retried == []
    # 3) 对话框仍在（失败对话未关）：不重试
    monkeypatch.setattr(sanbaihuan, "dialog_frame_score", lambda img: 0.99)
    p.update(ended_at=time.time() - 10, retry_at=0.0, retries=0)
    assert not sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert retried == []
    # 4) 对话已关且间隔足够：重走追踪链接
    monkeypatch.setattr(sanbaihuan, "dialog_frame_score", lambda img: 0.2)
    p.update(ended_at=time.time() - 10, retry_at=0.0, retries=0)
    assert sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert retried == [sanbaihuan.TASK_TITLE]
    assert p["retries"] == 1 and p["ended_at"] == 0.0
    # 5) 任务已推进（环变化）：状态复位且不重试
    retried.clear()
    p.update(ended_at=time.time() - 10, retry_at=0.0, retries=2)
    assert not sanbaihuan._txwz_tick(
        None, _items("见多识广[江湖历练任务]"), None, object())
    assert retried == []
    assert p["retries"] == 0 and p["ended_at"] == 0.0


def test_label_type_and_formation_snap():
    assert txwz.label_type("一步倒") == 1
    assert txwz.label_type("两步倒") == 2
    assert txwz.label_type("三步倒") == 3
    assert txwz.label_type("二步倒") == 2        # OCR 把「两」读成「二」
    assert txwz.label_type("步倒") == 1          # 首字漏检
    assert txwz.label_type("小可爱") is None
    # 缺 2/3 标签时按三种站形补齐（后排 1 2 1 2 1）
    grid = {(r, c): None for r in range(2) for c in range(5)}
    grid[(1, 1)] = 2
    hp, matched = txwz._snap_to_formations(grid)
    assert matched >= 1
    assert hp[(1, 1)] == 2 and hp[(1, 3)] == 2
    assert hp[(0, 1)] == 1


def _apply(hp, cell):
    for c in txwz.cross_cells(cell):
        hp[c] -= 1


def test_plan_firecracker_min_rounds_and_coverage():
    formations = (
        ({(0, c): 1 for c in range(5)} | {(1, c): 1 for c in range(5)}, 3),
        ({(0, c): 1 for c in range(5)}
         | {(1, c): v for c, v in enumerate((1, 2, 1, 2, 1))}, 4),
        ({(0, c): v for c, v in enumerate((1, 2, 1, 2, 1))}
         | {(1, c): v for c, v in enumerate((1, 1, 3, 1, 1))}, 5),
    )
    for hp, min_rounds in formations:
        seq = txwz.plan_firecracker_placements(hp)
        assert seq is not None and len(seq) == min_rounds
        # 逐回合模拟：中心格必须在场，十字覆盖内全部 -1，最终全部 ≤0
        state = dict(hp)
        for cell in seq:
            assert state[cell] > 0
            _apply(state, cell)
        assert all(v <= 0 for v in state.values())
    # 角落十字只有存在的格（不越界、不报错）
    assert txwz.cross_cells((0, 0)) == {(0, 0), (0, 1), (1, 0)}


# ---- 战斗型：取消自动 + 爆竹储物格切换（2026-11-27 用户规则） ----

class _FakeWin:
    """capture 按序返回预置帧（PIL RGB），其余能力由测试打桩。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self._i = 0

    def capture(self, **kwargs):
        f = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        from PIL import Image
        return Image.fromarray(f[:, :, ::-1])  # BGR → PIL RGB


def _rand_frame(seed):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (831, 1378, 3), dtype=np.uint8)


def _paste_center(frame_bgr, icon, center):
    """把模板中心贴到指定坐标（真实模板匹配，不桩匹配函数）。"""
    from PIL import Image
    from ascript.windows.system import R
    tpl = np.asarray(Image.open(R.img(icon)).convert("RGB"))[:, :, ::-1]
    h, w = tpl.shape[:2]
    x, y = center[0] - w // 2, center[1] - h // 2
    frame_bgr[y:y + h, x:x + w] = tpl


def test_wait_phase_cancels_auto_during_char_wait(monkeypatch):
    """等人物栏期间检测自动战斗并取消（不依赖开场时机）。"""
    from src.device import battle as battle_lib
    frame = _rand_frame(20261129)
    calls = []
    monkeypatch.setattr(battle_lib, "battle_ended", lambda win: False)
    monkeypatch.setattr(battle_lib, "cancel_auto_frame",
                        lambda win, img: calls.append(img) or True)
    monkeypatch.setattr(txwz, "_bar_phase", lambda bgr: "char")
    monkeypatch.setattr(txwz, "turn_red_pixels", lambda bgr: 999)
    assert txwz._wait_phase(_FakeWin([frame]), "char", timeout=1.0) == "char"
    assert len(calls) == 1  # 首帧即取消（自动中才点击的判定在 battle 侧）
    # 召唤兽栏等待不取消自动（避免多余点击）
    calls.clear()
    monkeypatch.setattr(txwz, "_bar_phase", lambda bgr: "beast")
    assert txwz._wait_phase(_FakeWin([frame]), "beast", timeout=1.0) == "beast"
    assert calls == []


def _bag_frames(with_item_slot=None, count=4, bag_on=True):
    """预置 count 个「背包面板」帧；指定 slot 的帧里有大型爆竹。"""
    from src.device import bag as bag_lib
    anchor, base = (56, 436), (387, 476)
    frames = []
    for i in range(count):
        f = _rand_frame(2026112700 + i)
        if bag_on or i == 0:
            _paste_center(f, bag_lib.BAG_BOX_ICON, anchor)
            _paste_center(f, bag_lib.BOX_ONE_CLOSE_ICON, base)
        if i == with_item_slot:
            _paste_center(f, txwz.FIRECRACKER_ICON,
                          (anchor[0] + txwz.FIRECRACKER_CELL_OFFSET[0],
                           anchor[1] + txwz.FIRECRACKER_CELL_OFFSET[1]))
        frames.append(f)
    return frames, base


def test_find_firecracker_switches_storage_slots(monkeypatch):
    """当前格无爆竹 → 逐格切换（box_one_close + 35px 步进）直到命中。"""
    frames, base = _bag_frames(with_item_slot=2)
    win = _FakeWin(frames)
    monkeypatch.setattr(txwz, "FIRECRACKER_RENDER_WAIT", 0.0)  # 省渲染等待
    clicks = []

    def fake_click(_win, x, y, button="left"):
        clicks.append((x, y))
        win._i = (y - base[1]) // 35  # 切到对应储物格的帧

    monkeypatch.setattr(txwz, "human_click", fake_click)
    expect = (56 + txwz.FIRECRACKER_CELL_OFFSET[0],
              436 + txwz.FIRECRACKER_CELL_OFFSET[1])
    assert txwz._find_firecracker(win) == expect
    assert clicks == [(base[0], base[1]),
                      (base[0], base[1] + 35),
                      (base[0], base[1] + 70)]


def test_find_firecracker_direct_hit_no_switch(monkeypatch):
    """当前格命中即返回，不点任何储物格按钮。"""
    frames, _base = _bag_frames(with_item_slot=0)
    clicks = []
    monkeypatch.setattr(txwz, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    assert txwz._find_firecracker(_FakeWin(frames)) is not None
    assert clicks == []


def test_find_firecracker_aborts_when_bag_closed(monkeypatch):
    """切格途中背包被关掉：立即中止，绝不向战场盲点。"""
    frames, base = _bag_frames(with_item_slot=None)
    frames[1] = frames[2] = frames[3] = _rand_frame(99)  # 首格点击后背包关闭
    monkeypatch.setattr(txwz, "FIRECRACKER_RENDER_WAIT", 0.0)
    clicks = []
    monkeypatch.setattr(txwz, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    assert txwz._find_firecracker(_FakeWin(frames)) is None
    assert clicks == [(base[0], base[1])]  # 只点了第 1 格，未继续盲点


def test_txwz_battle_plan_injected_and_battle_end_retry(monkeypatch):
    import time
    from src.device import battle as battle_lib
    ring = "天下无贼[江湖历练任务]"
    p = sanbaihuan._TXWZ_STATE
    p.update(ended_at=0.0, retry_at=0.0, retries=0, battle_seen=False)
    battle_lib.clear_battle_plan()
    # 入口选项出现 → 注入爆竹计划（handler 指向 minigame 处理器）
    monkeypatch.setattr(battle_lib, "watch_in_battle", lambda: False)
    items = _items(ring, "毛贼！休想逃！(130分以上即可通关)")
    assert not sanbaihuan._txwz_tick(None, items, None, object())
    plan = battle_lib.battle_plan()
    assert plan and plan["kind"] == "firecracker"
    assert plan["handler"].__name__ == "run_firecracker_battle"
    # 战斗中：让位给战斗线程，不重试
    monkeypatch.setattr(battle_lib, "watch_in_battle", lambda: True)
    assert not sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert p["battle_seen"] is True
    # 战斗结束且任务未推进 → 标记 ended_at，供后续重试链路
    monkeypatch.setattr(battle_lib, "watch_in_battle", lambda: False)
    assert not sanbaihuan._txwz_tick(None, _items(ring), None, object())
    assert p["ended_at"] > 0 and p["battle_seen"] is False
    # 环变化（任务推进）→ 清除爆竹计划
    assert not sanbaihuan._txwz_tick(
        None, _items("见多识广[江湖历练任务]"), None, object())
    assert battle_lib.battle_plan() is None
    battle_lib.clear_battle_plan()
