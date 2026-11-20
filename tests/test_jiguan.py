"""minigames/jiguan.py：封印机关破解的标签定位与解除结果判定。

2026-11-18 实机重标定：旧模板跨封印点背景误匹配（真实剑位 0.28-0.53、
背景杂岩 0.6-0.76），点击落空后「超时=成功」把未破除的封印当成功、
人物原地卡死。改为 OCR 标签「封印机关·黄/蓝/红」定位（剑身 = 标签上方
JIGUAN_SWORD_DY），结果只认三种直接信号：换一处 / 已被破解 / 已完成
玄枢轶闻任务；超时按落空换下一个机关，绝不按成功处理。

2026-11-20 流洲实机补两处：①人物身体可能挡住某个机关标签（OCR 与
2x 放大都读不出），已有标签全部点过而仍缺机关时走开一小步再重扫；
②点对机关的结局可能是剧情式（玩家「怎么没有反应？」→ 神秘人「玄宝
已被人抢先一步取走」），非「换一处」的结果对话框按成功处理。
"""
import numpy as np

from src.minigames import jiguan


class _Win:
    def capture(self, force_activate=False):
        return np.zeros((831, 1378, 3), dtype=np.uint8)


# ---- 标签解析（2026-11-18 实机帧实测值）----

def test_label_click_pos_full_label():
    """整标签「封印机关·黄」(744,627)-(848,650) → 剑身 (796,573)。"""
    x, y = jiguan._label_click_pos([744, 627, 848, 650], "封印机关·黄")
    assert abs(x - 796) <= 3 and abs(y - 573) <= 3


def test_label_click_pos_merged_with_player_name():
    """与玩家名合并的行（(657,446)-(819,470)）→ 剑身 x≈767（实测点击点）。"""
    x, y = jiguan._label_click_pos([657, 446, 819, 470],
                                   "入门御童封印机关·黄")
    assert abs(x - 767) <= 6 and abs(y - 393) <= 3


def test_label_click_pos_occluded_middle():
    """被角色遮挡只认出「印机关」(632,387)-(677,408) → 近似剑身 x≈662。"""
    x, y = jiguan._label_click_pos([632, 387, 677, 408], "印机关")
    assert abs(x - 662) <= 6 and abs(y - 332) <= 3


def test_label_color_only_after_keyword():
    """颜色只在「机关」之后找：同行玩家名里的颜色字不误判。"""
    assert jiguan._label_color("封印机关·黄") == "黄"
    assert jiguan._label_color("封印机关·蓝") == "蓝"
    assert jiguan._label_color("入门御童封印机关·黄") == "黄"
    assert jiguan._label_color("红孩儿封印机关·蓝") == "蓝"
    assert jiguan._label_color("印机关") is None


def test_label_color_traditional_forms():
    """繁体标签颜色归一（世界地图字体下 OCR 输出 黃/藍/紅）。"""
    assert jiguan._label_color("封印机关·黃") == "黄"
    assert jiguan._label_color("有情有问御童封御机关·黃") == "黄"
    assert jiguan._label_color("封印机关·藍") == "蓝"
    assert jiguan._label_color("封印机关·紅") == "红"


def test_ordered_positions_user_order():
    """黄→蓝→红顺序；颜色未识别的标签排最后但仍会尝试。"""
    labels = [("红", (1, 1)), (None, (2, 2)), ("黄", (3, 3)), ("蓝", (4, 4))]
    assert jiguan._ordered_positions(labels) == [(3, 3), (4, 4), (1, 1), (2, 2)]


# ---- 解除结果信号 ----

def _patch_result(monkeypatch, items):
    monkeypatch.setattr(jiguan, "dialog_frame_score", lambda img: 1.0)
    monkeypatch.setattr(jiguan.game_map, "ocr_region",
                        lambda img, region: (items, None))


def test_result_dialog_detects_fail_text(monkeypatch):
    """「看来此处无法破除封印，换一处再试试吧」→ "fail"。"""
    _patch_result(monkeypatch,
                  [("看来此处无法破除封印，换一处再试试吧。", [0, 0, 1, 1], 0.99)])
    assert jiguan._jiguan_result_dialog(_Win()) == "fail"


def test_result_dialog_detects_success_text(monkeypatch):
    """中间一处成功：「此处封印已被破解…」→ "success"（旧文案也认）。"""
    _patch_result(monkeypatch,
                  [("此处封印已被破解，回去找神秘人询问下一步要做什么。",
                    [0, 0, 1, 1], 0.99)])
    assert jiguan._jiguan_result_dialog(_Win()) == "success"
    _patch_result(monkeypatch, [("此处已解除封印", [0, 0, 1, 1], 0.99)])
    assert jiguan._jiguan_result_dialog(_Win()) == "success"


def test_result_dialog_detects_task_done_message(monkeypatch):
    """最后一处：「您已完成玄枢轶闻任务」消息栏提示 → "success"。"""
    _patch_result(monkeypatch,
                  [("您已完成玄枢轶闻任务", [0, 0, 1, 1], 0.99),
                   ("您获得了4点玄枢积分", [0, 30, 1, 31], 0.99)])
    assert jiguan._jiguan_result_dialog(_Win()) == "success"


def test_result_dialog_timeout_returns_none(monkeypatch):
    """无结果对话框 → None（调用方按落空处理，绝不按成功）。"""
    monkeypatch.setattr(jiguan, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    assert jiguan._jiguan_result_dialog(_Win(), timeout=0.01) is None


def test_result_dialog_story_outcome_is_success(monkeypatch):
    """点对机关后的剧情式结局（2026-11-20 实机帧）→ 非「换一处」即成功。"""
    _patch_result(monkeypatch,
                  [("怎么没有反应？", [434, 378, 545, 405], 1.0)])
    assert jiguan._jiguan_result_dialog(_Win()) == "success"


def test_result_dialog_trigger_text_is_not_result(monkeypatch):
    """触发提示对话渲染残留不算结果（不得误判成功）。"""
    _patch_result(monkeypatch,
                  [("看来要破解的便是此地的封印了。", [0, 0, 1, 1], 0.99)])
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    assert jiguan._jiguan_result_dialog(_Win(), timeout=0.01) is None


# ---- play 主流程 ----

def _install(monkeypatch, labels, results):
    """安装替身：labels 供 _wait_labels，results 按点击次数消费。"""
    clicks = []
    dismissed = []
    landed = []
    outcomes = iter(results)
    timeouts = []

    monkeypatch.setattr(jiguan, "log", lambda *a, **k: None)
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    monkeypatch.setattr(jiguan, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(jiguan, "_wait_labels", lambda win: labels)
    monkeypatch.setattr(jiguan, "_jiguan_result_dialog",
                        lambda win, timeout=None: next(outcomes))
    monkeypatch.setattr(jiguan, "clear_blocking_dialog",
                        lambda win, prefix="", max_clicks=3:
                        dismissed.append(prefix) or (True, True))
    monkeypatch.setattr(jiguan.zuoqi, "ensure_landed",
                        lambda win: landed.append("land") or (True, "已降落"))
    monkeypatch.setattr(jiguan.zuoqi, "close_feixingyuqi_panel",
                        lambda win: (True, "面板已关闭"))
    return clicks, dismissed, landed, timeouts


LABELS = [("黄", (760, 400)), ("蓝", (620, 470)), ("红", (800, 560))]


def test_play_lands_with_zuoqi_not_altc(monkeypatch):
    """降落走座骑（飞行驭器面板），不再按 Alt+C。"""
    clicks, _dismissed, landed, _t = _install(monkeypatch, LABELS, [])
    monkeypatch.setattr(jiguan, "_jiguan_result_dialog",
                        lambda win, timeout=None: "success")
    ok, info = jiguan.play(_Win(), prefix="玄枢轶闻")
    assert ok
    assert landed == ["land"]              # 座骑降落被调用
    assert clicks[0] == jiguan.SCREEN_CENTER_POS  # 先关提示对话框


def test_play_fail_then_success(monkeypatch):
    """第一个机关提示换一处 → 关对话框换第二个 → 成功即停。"""
    clicks, dismissed, _landed, _t = _install(
        monkeypatch, LABELS, ["fail", "success"])
    ok, info = jiguan.play(_Win())
    assert ok and "封印破解完成" in info
    assert clicks[1:] == [(760, 400), (620, 470)]  # 黄→蓝，红不再点
    assert len(dismissed) == 1


def test_play_timeout_is_not_success(monkeypatch):
    """点击超时无结果 → 不按成功处理，换下一个；三个都落空 → 失败。"""
    clicks, _dismissed, _landed, _t = _install(
        monkeypatch, LABELS, [None, None, None])
    ok, info = jiguan.play(_Win())
    assert not ok and "尝试 3 个机关位置" in info
    assert clicks[1:] == [(760, 400), (620, 470), (800, 560)]


def test_play_done_message_success(monkeypatch):
    """最后一处封印：任务完成提示即结束。"""
    _clicks, _d, _l, _t = _install(monkeypatch, LABELS, ["success"])
    ok, info = jiguan.play(_Win())
    assert ok and "封印破解完成" in info


def test_play_no_labels_reports_failure(monkeypatch):
    """识别不到标签 → 不点击机关、返回失败（不再假装成功）。"""
    clicks, _dismissed, landed, _t = _install(monkeypatch, [], [])
    ok, info = jiguan.play(_Win())
    assert not ok and "尝试 0 个机关位置" in info
    assert landed == ["land"]
    assert clicks == [jiguan.SCREEN_CENTER_POS]  # 只关了提示对话框


def test_play_retries_new_positions_after_scene_change(monkeypatch):
    """一轮三个位置都落空（超时无结果）后再扫一轮：新位置继续尝试。

    落空不按颜色拉黑（只有明确「换一处」的机关才不再点），场景变化后
    同色机关的新位置仍会点。
    """
    scans = iter([LABELS, LABELS, LABELS, [("黄", (900, 500))]])
    ok, clicks, steps = _play_with_scans(monkeypatch, scans, [None] * 4)
    assert not ok and not steps                  # 落空不走开：只按位置去重
    assert clicks[1:] == [(760, 400), (620, 470), (800, 560), (900, 500)]


def _play_with_scans(monkeypatch, scans, results, step_ok=True):
    """play 替身：scans/results 按次数消费，_step_aside 按 step_ok 返回。

    返回 (是否成功, 点击坐标列表, 走开次数)。results 耗尽即断言失败。
    """
    clicks = []
    steps = []
    outcomes = iter(results)
    monkeypatch.setattr(jiguan, "log", lambda *a, **k: None)
    monkeypatch.setattr(jiguan.time, "sleep", lambda s: None)
    monkeypatch.setattr(jiguan, "human_click",
                        lambda win, x, y: clicks.append((x, y)))
    monkeypatch.setattr(jiguan, "_wait_labels", lambda win: next(scans, []))
    monkeypatch.setattr(jiguan, "_jiguan_result_dialog",
                        lambda win, timeout=None: next(outcomes))
    monkeypatch.setattr(jiguan, "clear_blocking_dialog",
                        lambda win, prefix="", max_clicks=3: (True, True))
    monkeypatch.setattr(jiguan.zuoqi, "ensure_landed",
                        lambda win: (True, "已降落"))
    monkeypatch.setattr(jiguan.zuoqi, "close_feixingyuqi_panel",
                        lambda win: (True, "面板已关闭"))

    def fake_step(win, labels, prefix=""):
        steps.append(list(labels))
        return step_ok

    monkeypatch.setattr(jiguan, "_step_aside", fake_step)
    ok, _info = jiguan.play(_Win())
    return ok, clicks, steps


def test_play_steps_aside_when_label_occluded(monkeypatch):
    """标签被人物挡住只读出黄/蓝（2026-11-20 流洲实机）：两个都换一处后
    走开一步重扫，读出红机关并点中成功。"""
    occluded = [("黄", (760, 400)), ("蓝", (620, 470))]
    revealed = [("黄", (610, 440)), ("蓝", (470, 510)), ("红", (514, 280))]
    scans = iter([occluded, occluded, occluded, revealed])
    ok, clicks, steps = _play_with_scans(
        monkeypatch, scans, ["fail", "fail", "success"])
    assert ok
    assert len(steps) == 1                       # 走开一次
    assert clicks[1:] == [(760, 400), (620, 470), (514, 280)]


def test_play_reveal_failure_gives_up(monkeypatch):
    """走开一步失败（人物走不动）→ 不无限试，按尝试 2 个机关收尾。"""
    occluded = [("黄", (760, 400)), ("蓝", (620, 470))]
    scans = iter([occluded, occluded, occluded])
    ok, clicks, steps = _play_with_scans(
        monkeypatch, scans, ["fail", "fail"], step_ok=False)
    assert not ok
    assert len(steps) == 1
    assert clicks[1:] == [(760, 400), (620, 470)]


def test_play_reveal_bounded_twice(monkeypatch):
    """走开后仍未读出第三机关：最多走开 JIGUAN_REVEAL_MAX 次后放弃。"""
    occluded = [("黄", (760, 400)), ("蓝", (620, 470))]
    scans = iter([occluded] * 6)
    ok, clicks, steps = _play_with_scans(
        monkeypatch, scans, ["fail", "fail"], step_ok=True)
    assert not ok
    assert len(steps) == jiguan.JIGUAN_REVEAL_MAX
    assert clicks[1:] == [(760, 400), (620, 470)]
