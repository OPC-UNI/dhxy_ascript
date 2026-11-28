# -*- coding: utf-8 -*-
"""四锁四色钥匙开锁小游戏（300环）核心路径单测。

覆盖：
1. Mastermind 反馈语义（含重复颜色）与候选过滤；
2. 信息量最优猜测：能分辨候选 + 全空间抽样真解 ≤10 次内解开；
3. 面板读数：徽章数字前导 0 解析、钥匙颜色分类（实机采样值）；
4. 记录行圆点定位（合成帧）；
5. 历史重建要求行序号从 1 连续；
6. 任务侧状态机：点 X 关窗后重走追踪链接、环切走后复位重开标记。
"""
import time

import numpy as np
from PIL import Image, ImageDraw

from src.minigames import sanbaihuan_jiesuo as js
from src.tasks import sanbaihuan as sb


# ---------------- Mastermind ----------------

def test_feedback_matches_real_game_observations():
    # 2026-11-27 实机：真解 银红金金，三次提交的反馈原文
    secret = (js.SILVER, js.RED, js.GOLD, js.GOLD)
    assert js.feedback((js.GOLD, js.RED, js.SILVER, js.GREEN), secret) == (1, 2)
    assert js.feedback((js.GOLD, js.GOLD, js.RED, js.RED), secret) == (0, 3)
    assert js.feedback((js.GOLD, js.GOLD, js.GOLD, js.SILVER), secret) == (1, 2)
    assert js.feedback(secret, secret) == (4, 0)
    # 重复颜色按两边出现次数取小
    assert js.feedback((0, 0, 0, 0), (0, 0, 1, 2)) == (2, 0)


def test_choose_guess_distinguishes_four_candidates():
    # 实机前两条反馈后的 4 个候选：下一猜必须把它们分到不同反馈分支
    cands = [(1, 1, 0, 2), (1, 1, 3, 0), (2, 1, 0, 0), (3, 1, 0, 0)]
    guess = js.choose_guess(cands)
    buckets = {js.feedback(guess, c) for c in cands}
    assert len(buckets) == len(cands)


def test_solver_wins_sample_secrets_within_limit():
    secrets = [(2, 1, 0, 0), (0, 0, 0, 0), (3, 3, 3, 3),
               (0, 1, 2, 3), (1, 0, 3, 2), (0, 0, 1, 1)]
    for secret in secrets:
        cands = list(js.ALL_CODES)
        for step in range(1, 11):
            guess = js.choose_guess(cands)
            fb = js.feedback(guess, secret)
            if fb == (4, 0):
                break
            cands = js.filter_codes(cands, guess, *fb)
            assert cands, f"候选被过滤为空：{secret}"
        else:
            raise AssertionError(f"10 次内未解开：{secret}")


# ---------------- 面板读数 ----------------

def test_parse_badge_text_strips_leading_dot_zero():
    # 绿点被 OCR 成前导 0 的实测文本
    assert js._parse_badge_text("01") == 1
    assert js._parse_badge_text("00") == 0
    assert js._parse_badge_text("2") == 2
    assert js._parse_badge_text("") is None
    assert js._parse_badge_text("12") is None


def test_classify_key_color_measured_samples():
    # 2026-11-27 实机记录行圆盘采样（见模块注释标定）
    samples = {
        (196, 177, 143): js.GOLD,
        (182, 144, 119): js.RED,
        (155, 154, 151): js.SILVER,
        (103, 142, 121): js.GREEN,
    }
    for rgb, want in samples.items():
        frame = np.zeros((80, 80, 3), dtype=np.float32)
        frame[:, :] = rgb
        assert js.classify_key_color(frame, 40, 40) == want


def test_dot_rows_pairs_green_and_orange():
    frame = Image.new("RGB", (1378, 831), (255, 255, 255))
    draw = ImageDraw.Draw(frame)
    ys = (453, 514)
    for y in ys:
        draw.ellipse((706, y - 5, 716, y + 5), fill=(80, 200, 90))
        draw.ellipse((743, y - 5, 753, y + 5), fill=(250, 180, 60))
    # 只有绿点、没有橙点的行不算记录行
    draw.ellipse((706, 600 - 5, 716, 600 + 5), fill=(80, 200, 90))
    assert js._dot_rows(frame) == list(ys)


def test_decode_history_requires_contiguous_row_numbers(monkeypatch):
    monkeypatch.setattr(js, "_read_badges", lambda frame, cy: (1, 2))
    monkeypatch.setattr(js, "_row_colors",
                        lambda frame, cy: (0, 1, 2, 3))
    monkeypatch.setattr(js, "_read_row_no",
                        lambda frame, cy: {453: 1, 514: 2, 575: 3}.get(cy))
    frame = object()
    assert js._decode_history(frame, [453, 514]) == \
        [((0, 1, 2, 3), 1, 2), ((0, 1, 2, 3), 1, 2)]
    # 记录列表滚动（可见行从 2 开始）：无法完整重建
    monkeypatch.setattr(js, "_read_row_no",
                        lambda frame, cy: {453: 2, 514: 3}.get(cy))
    assert js._decode_history(frame, [453, 514]) is None


# ---------------- 任务侧状态机 ----------------

class _BlankWin:
    def capture(self, force_activate=False):
        return Image.new("RGB", (1378, 831), (0, 0, 0))


def _reset_state():
    sb._JIESUO_STATE.update(pending=False, next_at=0.0, restarts=0,
                            since=0.0, reopened=False, last_panel_at=0.0)


def test_wait_handler_reopen_flow_rewalks_track_link(monkeypatch):
    _reset_state()
    monkeypatch.setattr(sb, "_txwz_tick", lambda *a: False)
    monkeypatch.setattr(sb.jiesuo_mini, "is_ready", lambda bgr: True)
    monkeypatch.setattr(sb.jiesuo_mini, "play",
                        lambda win, prefix="", allow_reopen=True:
                        (sb.jiesuo_mini.STATUS_REOPEN, "次数不足，已关窗"))
    monkeypatch.setattr(sb.app_utils, "mark_track_walk", lambda: None)
    # 静默门（2026-11-28）：本用例只验证「等待期不占帧 + 退避重走」契约，
    # 寻路/对话判定由 test_sanbaihuan 的重走静默门用例单独覆盖
    monkeypatch.setattr(sb._RESTART_WALK, "walking",
                        lambda win=None, img=None: False)
    tracks = []
    monkeypatch.setattr(sb.task_tracker, "track_task",
                        lambda win, title, **kw:
                        tracks.append(title) or (True, "ok"))
    win = _BlankWin()
    # 第一次触发：面板在场 → play 返回 reopen，进入 pending
    assert sb._wait_handler(win, [], None, win.capture()) is True
    assert sb._JIESUO_STATE["pending"] is True
    assert sb._JIESUO_STATE["reopened"] is True
    # 等待期不得占住本帧（要交给 follow 处理对话/寻路），但应立即重走一次
    monkeypatch.setattr(sb.jiesuo_mini, "is_ready", lambda bgr: False)
    assert sb._wait_handler(win, [], None, win.capture()) is False
    assert tracks == [sb.TASK_TITLE]
    # 退避期内不重复点击
    sb._wait_handler(win, [], None, win.capture())
    assert len(tracks) == 1


def test_wait_handler_resets_reopen_flag_when_ring_changes(monkeypatch):
    _reset_state()
    sb._JIESUO_STATE["reopened"] = True
    monkeypatch.setattr(sb, "_txwz_tick", lambda *a: False)
    monkeypatch.setattr(sb.jiesuo_mini, "is_ready", lambda bgr: False)
    items = [("见多识广[江湖历练任务]", (10, 260, 300, 280), 1.0)]
    win = _BlankWin()
    sb._wait_handler(win, items, None, win.capture())
    assert sb._JIESUO_STATE["reopened"] is False


def test_play_rejects_panel_when_button_missing(monkeypatch):
    """面板判据模板未命中：不进入选钥匙流程（防误点世界画面）。"""
    win = _BlankWin()
    monkeypatch.setattr(js, "_capture", lambda w: win.capture())
    status, info = js.play(win)
    assert status == js.STATUS_DONE
    assert "面板不在场" in info
