"""wubei.py：黄袍怪多杀任务战斗结束后的追踪链接重点点击。

点击位置回归 2026-11-03：旧实现点 OCR 文本矩形中心——整行
「前往平顶山消灭黄袍怪」矩形中心 (99,261) 落在可点击链接「平顶山」
右缘外（同帧对比 follow 路径 green_text_center 点 (76,268)），偏 23px
点到非链接文字偶现点击无反应。修正后点下划线中心（链接专属标记）。
"""
import numpy as np

from src import wubei


def test_click_track_link_clicks_underline_not_rect_center(monkeypatch):
    """整行矩形中心在链接「平顶山」右缘外，须点下划线中心。

    像素形态按 2026-11-03 实测对比重建：行文字散碎笔画 y251-264
    （「平顶山」为亮绿链接 x48-99，其余白字不可点），下划线仅覆盖
    链接部分（x48-99, y271, 51px 实线）。
    """
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[251:264, 14:48:4] = (200, 200, 200)   # 「前往」白字（散碎笔画）
    img[251:264, 48:99:4] = (0, 255, 0)       # 「平顶山」亮绿链接文字
    img[251:264, 99:185:4] = (200, 200, 200)  # 「消灭黄袍怪」白字
    img[271, 48:99] = (0, 255, 0)             # 下划线只覆盖链接部分
    items = [
        ("前往平顶山消灭魔尊", [14, 228, 185, 248], 0.99),   # 无关键词：跳过
        ("黄袍怪(2/3)", [14, 230, 90, 246], 0.90),            # 有词但白字：跳过
        ("前往平顶山消灭黄袍怪", [14, 249, 185, 269], 0.99),  # 链接行
    ]
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    assert wubei._click_track_link(object(), items, img, "黄袍") is True
    # 下划线 51px 实线（x48-98）中心 x73、上方 2px；
    # 矩形中心 (99,259) 在链接右缘外，旧实现会点偏
    assert clicks == [(73, 269)]


def test_click_track_link_no_match_no_click(monkeypatch):
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    items = [("前往平顶山消灭魔尊", [14, 249, 185, 269], 0.99)]
    assert wubei._click_track_link(object(), items, img, "黄袍") is False
    assert clicks == []


# ---- 巫医链：轮询点击时间戳立即返回 / 超时兜底（2026-11-04 对齐修罗） ----

import pytest
from PIL import Image

DIALOG_ITEMS = [("我是超级巫医，可以帮你医治召唤兽",
                 (400, 300, 900, 600), 0.96)]
NPC_PT = (661, 187)
OPTION_PT = (534, 399)


class _FakeClock:
    """可控时钟：sleep 推进虚拟时间，time 返回虚拟时间。"""

    def __init__(self):
        self.t = 1000.0  # 真实时间戳语义（>0：区分 last_wuyi_option_at=0）

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


class _FakeWin:
    def capture(self, force_activate=False):
        return Image.new("RGB", (60, 60))


@pytest.fixture
def wuyi_chain(monkeypatch):
    """装配 wubei._run_wuyi_chain 外部依赖：NPC 立即可见、超时缩短为 1s。"""
    clock = _FakeClock()
    clicks = []
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "is_in_battle", lambda win: False)
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda win: (wubei.BAOXIANGGUO, *wubei.WUYI_SPOT))
    monkeypatch.setattr(wubei.game_map, "find_template",
                        lambda win, icon, *a, **k: NPC_PT)
    monkeypatch.setattr(wubei.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), None))
    monkeypatch.setattr(wubei, "_find_wuyi_option",
                        lambda items, img_np: OPTION_PT)
    monkeypatch.setattr(wubei, "WUYI_HANDLE_TIMEOUT", 1.0)  # 单轮即超时
    monkeypatch.setattr(wubei.battle, "last_wuyi_option_at", 0)
    monkeypatch.setattr(wubei.battle, "last_wuyi_option_pos", None)
    monkeypatch.setattr(wubei.battle, "_wuyi_pos_by_key", {})  # 分键缓存隔离
    return {"clock": clock, "clicks": clicks}


def test_wuyi_chain_click_detected_returns_immediately(wuyi_chain):
    """战斗线程点击后（时间戳晚于点 NPC）→ 随机 1~1.2s 即返回，
    不等对话框关闭（点击即寻路）。"""
    wubei.battle.last_wuyi_option_at = 1000.5  # 点 NPC 之后战斗线程点击
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "自动巫医完成" in info
    assert wuyi_chain["clicks"] == [NPC_PT]
    t = wuyi_chain["clock"].t
    assert 1001.0 <= t <= 1001.2


def test_wuyi_chain_timeout_no_click_self_clicks(wuyi_chain):
    """超时时战斗线程未点击且对话框在场 → 兜底自行点击一次。"""
    wubei.battle.last_wuyi_option_at = 0
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, OPTION_PT]


def test_wuyi_chain_timeout_self_click_uses_cached_pos(wuyi_chain, monkeypatch):
    """兜底自点优先用绿字校验过的本弹窗缓存位置，不重新识别。"""
    wubei.battle.last_wuyi_option_at = 0
    # 本弹窗（命中文案）已缓存点击位置——经 record 走分键缓存
    wubei.battle.record_wuyi_option_pos((650, 420), DIALOG_ITEMS[0][0])
    wubei.battle.last_wuyi_option_at = 0  # 清点击信号（仅测分键缓存直点）
    img_np = np.zeros((831, 1378, 3), dtype=np.uint8)
    img_np[410:430, 610:690] = (0, 186, 0)  # 缓存点周围亮绿
    monkeypatch.setattr(wubei.game_map, "ocr_regions",
                        lambda img, regions: (list(DIALOG_ITEMS), img_np))

    def _boom(*a, **k):
        raise AssertionError("有缓存不应再识别")

    monkeypatch.setattr(wubei, "_find_wuyi_option", _boom)
    ok, info = wubei._run_wuyi_chain(_FakeWin())
    assert ok is True
    assert "已自行点击选项" in info
    assert wuyi_chain["clicks"] == [NPC_PT, (650, 420)]


# ---- 黄袍怪多杀：重点链接后队员回血回蓝 pass（组号专用，2026-11-06） ----

HPG_ITEMS = [("前往平顶山消灭黄袍怪", [14, 249, 185, 269], 0.99)]
HPG_FRAME = np.zeros((831, 1378, 3), dtype=np.uint8)


def _hpg_env(monkeypatch):
    """装配 _handle_huangpaoguai 外部依赖：虚拟时钟、链接点击恒成功、
    pass 调用记录、战斗共享标志恒 False（战斗结束沿由 _hpg_was_in_battle
    直置模拟）。"""
    clock = _FakeClock()
    passes = []
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_click_track_link", lambda *a, **k: True)
    monkeypatch.setattr(wubei, "_maybe_tab_bars_pass", lambda win: passes.append(1))
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    wubei._reset_huangpaoguai_state()
    return clock, passes


def test_huangpaoguai_reclick_first_try_triggers_bars_pass_once(monkeypatch):
    """战斗结束沿重点链接：首次点击成功即触发队员回血回蓝 pass，
    重试点击（前次被结算吞掉）不再重复触发——每次战斗结束只补一次。"""
    clock, passes = _hpg_env(monkeypatch)
    wubei._hpg_was_in_battle = True  # 上一轮询在战斗：本轮为结束沿
    win = object()
    # 结束沿首轮询：重新武装待点标记（RECLICK_MIN 内不点）
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is False
    assert passes == []
    # 首点成功 → pass 触发
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_MIN + 0.1)
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is True
    assert passes == [1]
    # 冷却后重试点（前次被结算吞掉）：不重复触发 pass
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_COOLDOWN + 0.1)
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is True
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_COOLDOWN + 0.1)
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is True
    assert passes == [1]
    # 尝试次数用尽：复位待点标记，状态干净
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_COOLDOWN + 0.1)
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is False
    assert wubei._hpg_need_click is False


def test_huangpaoguai_no_keyword_no_pass(monkeypatch):
    """非黄袍怪任务（面板无关键词）：重点链接逻辑不启动，pass 不触发。"""
    clock, passes = _hpg_env(monkeypatch)
    wubei._hpg_was_in_battle = True
    win = object()
    items = [("前往平顶山消灭魔尊", [14, 249, 185, 269], 0.99)]
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_MIN + 0.1)
    assert wubei._handle_huangpaoguai(win, items, HPG_FRAME) is False
    # 待点击期间关键词持续缺失：超时（一次都没点成）放弃并复位，
    # 不点链接不 pass
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_TIMEOUT + 0.1)
    assert wubei._handle_huangpaoguai(win, items, HPG_FRAME) is False
    assert passes == []
    assert wubei._hpg_need_click is False


# ---- 显形镜：配额耗尽后「进度流程进行中」冻结替换计时（2026-11-10） ----

def test_xj_quota_frozen_while_dialog_progress(monkeypatch):
    """白龙马对话框已出现/按钮流程进行中时，配额分支不得重新告警并
    重启 6s 替换计时。

    回归 2026-11-10 22:19：对话框出现后处理器复位了告警/计时，但
    跟踪处理器 phase 0 只认 use_count，下一轮询立即重抛「A、B 均未
    触发」并重新武装 6s 计时——按钮/开打流程超过 6s 会在任务推进中
    误替换。
    """
    logs = []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos", lambda win, items: ("宝象国", 90, 85))
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    # 状态机：已到达、用镜 2 次、白龙马对话框刚关闭（找任务按钮窗口内）
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    monkeypatch.setattr(wubei, "_xj_stable", 3)
    monkeypatch.setattr(wubei, "_xj_last_pos", ("宝象国", 90, 85))
    monkeypatch.setattr(wubei, "_xj_use_count", 2)
    monkeypatch.setattr(wubei, "_xj_need_move", False)
    monkeypatch.setattr(wubei, "_xj_move_stuck", 0)
    monkeypatch.setattr(wubei, "_xj_warned", False)
    monkeypatch.setattr(wubei, "_xj_quota_exhausted_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_dismissed_at", 1000.0)  # >0：按钮流程进行中
    monkeypatch.setattr(wubei, "_xj_btn_clicked_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    items = [("前往火云戈壁处使用显形镜查探", (40, 260, 200, 280), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing_track(object(), items, img) is False
    assert wubei._xj_warned is False
    assert wubei._xj_quota_exhausted_at == 0.0
    assert not any("A、B 两处" in m for m in logs)


def test_xj_dialog_present_short_keyword():
    """长句 OCR 截断时短关键词兜底命中白龙马对话框（2026-11-10 两轮漏判）。"""
    truncated = [("一阵光芒闪过，白龙马出现", (400, 300, 900, 340), 0.9)]
    assert wubei._xj_dialog_present(truncated) is True
    unrelated = [("阴都大帝：尔等何人", (400, 300, 900, 340), 0.9)]
    assert wubei._xj_dialog_present(unrelated) is False


def test_xj_quota_full_skips_walk_back(monkeypatch):
    """第 2 次用镜失败后配额已满：不再点寻路链接白走一趟，直接进入
    卡死替换计时（2026-11-10 22:38 实测回 A 白走 20s 还拖后替换）。"""
    logs = []
    walks = []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos", lambda win, items: ("平顶山", 212, 131))
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(wubei, "_click_xianxingjing_walk",
                        lambda win, items, img_np, side="b":
                        walks.append(side) or True)
    monkeypatch.setattr(wubei, "_xj_phase", 1)
    monkeypatch.setattr(wubei, "_xj_use_count", 2)
    monkeypatch.setattr(wubei, "_xj_grace_start",
                        wubei.time.time() - wubei.XIANXINGJING_GRACE - 0.1)
    monkeypatch.setattr(wubei, "_xj_walk_side", "b")
    monkeypatch.setattr(wubei, "_xj_warned", False)
    monkeypatch.setattr(wubei, "_xj_quota_exhausted_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_dismissed_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_btn_clicked_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    monkeypatch.setattr(wubei, "_xj_diag_at", wubei.time.time())
    items = [("前往平顶山处使用显形镜查探", (40, 260, 200, 280), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing_track(object(), items, img) is False
    assert walks == []  # 不再点链接走位
    assert wubei._xj_warned is True
    assert wubei._xj_quota_exhausted_at > 0
    assert any("A、B 两处" in m for m in logs)


def test_xj_use_log_includes_side(monkeypatch):
    """用镜日志带侧别（A/B）：日志看不出第几处在哪侧排查困难（2026-11-10）。"""
    logs = []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos", lambda win, items: ("平顶山", 212, 131))
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(wubei, "_click_xianxingjing_use",
                        lambda win, items, img_np: True)
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    monkeypatch.setattr(wubei, "_xj_stable", 3)
    monkeypatch.setattr(wubei, "_xj_last_pos", ("平顶山", 212, 131))
    monkeypatch.setattr(wubei, "_xj_use_count", 0)
    monkeypatch.setattr(wubei, "_xj_need_move", False)
    monkeypatch.setattr(wubei, "_xj_move_stuck", 0)
    monkeypatch.setattr(wubei, "_xj_walk_side", None)
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    items = [("前往平顶山处使用显形镜查探", (40, 260, 200, 280), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing_track(object(), items, img) is True
    assert any("用镜" in m and "A 侧" in m for m in logs)


# ---- 领取点到达判据地图名门控（2026-11-11 用户规则：五倍必须宝象国） ----
def test_in_claim_map_requires_baoxiangguo():
    assert wubei._in_claim_map(("宝象国", 86, 86))
    assert not wubei._in_claim_map(("长安", 367, 27))
    assert not wubei._in_claim_map(("灵兽村", 111, 90))
    assert not wubei._in_claim_map(None)  # 位置读不出不采信按钮


def test_walk_to_claim_spot_button_in_changan_not_arrival(monkeypatch):
    """长安命中「任务」按钮模板（同图跨模板）：不得判到达。"""
    win = _FakeWin()
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.game_map, "match_frame_np",
                        lambda frame, icon, **k: (300, 300)
                        if icon == wubei.LINGQU_ICON else None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("长安", 367, 27))
    monkeypatch.setattr(wubei, "_open_claim_map",
                        lambda w, pos=None: (False, "地图导航中止"))
    ok, info = wubei._walk_to_claim_spot(win)
    assert ok is False and info == "地图导航中止"


def test_track_state_panel_closed(monkeypatch):
    """面板不在场：按无任务返回（不阻塞领取），且不得 OCR 静态区域误采信。"""
    monkeypatch.setattr(wubei.task_tracker, "panel_visible", lambda **k: False)

    def _boom(img):
        raise AssertionError("面板不在场不应 OCR 追踪区域")

    monkeypatch.setattr(wubei.task_tracker, "ocr_track_read", _boom)
    assert wubei._track_state(_FakeWin()) == (False, False, False)


def test_track_state_panel_visible_no_task(monkeypatch):
    monkeypatch.setattr(wubei.task_tracker, "panel_visible", lambda **k: True)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track_read",
                        lambda img: ([], None))
    assert wubei._track_state(_FakeWin()) == (False, False, False)


def test_claim_task_skips_when_task_reappeared(monkeypatch):
    """误判消失走了领取、任务又出现：跳过领取，交主循环续跑跟踪。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: "sig")
    called = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: called.append(1) or (False, "x", None, None, None))
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert ok is False and "续跑跟踪" in info and frame is None
    assert called == []  # 不进入领取入口流程


def test_claim_task_replace_allows_existing_task(monkeypatch):
    """替换任务场景（allow_existing=True）：面板有任务仍走领取流程。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: "sig")
    called = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: called.append(1) or (False, "入口失败", None, None, None))
    ok, info, frame = wubei._claim_task(_FakeWin(), allow_existing=True)
    assert ok is False and info == "入口失败"
    assert called == [1]  # 已进入领取入口流程
