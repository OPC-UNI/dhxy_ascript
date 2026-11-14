"""wubei.py：黄袍怪多杀任务战斗结束后的追踪链接重点点击。

点击位置回归 2026-11-03：旧实现点 OCR 文本矩形中心——整行
「前往平顶山消灭黄袍怪」矩形中心 (99,261) 落在可点击链接「平顶山」
右缘外（同帧对比 follow 路径 green_text_center 点 (76,268)），偏 23px
点到非链接文字偶现点击无反应。修正后点下划线中心（链接专属标记）。
"""
import numpy as np
import pytest

from src.tasks import wubei


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
    monkeypatch.setattr(wubei, "find_wuyi_option",
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

    monkeypatch.setattr(wubei, "find_wuyi_option", _boom)
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
    """非黄袍怪任务（面板无「黄袍」关键词）：整机跳过——不武装重点
    链接、不点链接、不触发 pass，也不打「黄袍怪超时」误导 WARN
    （2026-11-14 实机：莲花洞魔尊战败后误报）。"""
    clock, passes = _hpg_env(monkeypatch)
    logs = []
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append((level, msg)))
    wubei._hpg_was_in_battle = True
    win = object()
    items = [("前往平顶山消灭魔尊", [14, 249, 185, 269], 0.99)]
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_MIN + 0.1)
    assert wubei._handle_huangpaoguai(win, items, HPG_FRAME) is False
    assert wubei._hpg_need_click is False      # 未武装重点链接
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_TIMEOUT + 0.1)
    assert wubei._handle_huangpaoguai(win, items, HPG_FRAME) is False
    assert passes == []
    assert not [m for _lv, m in logs if "黄袍" in m]  # 不再误报


def test_huangpaoguai_seen_then_gone_times_out_with_warn(monkeypatch):
    """黄袍怪多杀任务：关键词识别后链接消失（面板持续无链接）→ 15s
    超时放弃并告警（原有行为保留，只对真正的黄袍怪任务生效）。"""
    clock, passes = _hpg_env(monkeypatch)
    logs = []
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append((level, msg)))
    wubei._hpg_was_in_battle = True
    win = object()
    # 战斗结束后首帧链接可见：识别为黄袍怪任务并武装（RECLICK_MIN 内不点）
    assert wubei._handle_huangpaoguai(win, HPG_ITEMS, HPG_FRAME) is False
    assert wubei._hpg_task_seen is True
    assert wubei._hpg_need_click is True
    # 之后链接持续缺失 → 超时告警并复位
    items_gone = [("前往平顶山消灭魔尊", [14, 249, 185, 269], 0.99)]
    clock.sleep(wubei.HUANGPAOGUAI_RECLICK_TIMEOUT + 0.1)
    assert wubei._handle_huangpaoguai(win, items_gone, HPG_FRAME) is False
    assert [m for _lv, m in logs if "黄袍怪：重点链接超时" in m]
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
    monkeypatch.setattr(wubei, "_xj_anchor", ("宝象国", 90, 85))
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
    monkeypatch.setattr(wubei, "_xj_anchor", ("平顶山", 212, 131))
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


# ---- 显形镜：到达锚点/地图门/翻侧上限（2026-11-11 半路用镜修复） ----

def test_xj_walking_never_counts_as_arrived(monkeypatch):
    """步行每轮询移动 2-3 单位：旧实现比相邻两帧（每轴 ≤3）会连判
    「稳定」→ 半路用镜；锚点累计判定必须在位移后清零重计。"""
    used = []
    positions = iter([("火云戈壁", 150, 70), ("火云戈壁", 153, 71),
                      ("火云戈壁", 156, 72), ("火云戈壁", 159, 73)])
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos", lambda win, items: next(positions))
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_click_xianxingjing_use",
                        lambda win, items, img_np: used.append(1) or True)
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    monkeypatch.setattr(wubei, "_xj_stable", 0)
    monkeypatch.setattr(wubei, "_xj_anchor", None)
    monkeypatch.setattr(wubei, "_xj_last_pos", None)
    monkeypatch.setattr(wubei, "_xj_use_count", 0)
    monkeypatch.setattr(wubei, "_xj_need_move", False)
    monkeypatch.setattr(wubei, "_xj_move_stuck", 0)
    monkeypatch.setattr(wubei, "_xj_walk_side", None)
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    monkeypatch.setattr(wubei, "_xj_veto_click_at", wubei.time.time())
    items = [("前往火云戈壁、火云戈壁处使", (40, 260, 200, 280), 0.99),
             ("显形镜查探。", (40, 276, 140, 292), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    for _ in range(4):
        assert wubei._handle_xianxingjing_track(object(), items, img) is False
    assert used == []
    assert wubei._xj_stable <= 1


def test_xj_map_matches_ocr_tolerance():
    """地图门：位置地名与链接地名各有 1 字 OCR 误读仍算同图；
    标题「宝象迷情」不得让领取城「宝象国」蒙混过关。"""
    items = [("宝象迷情降魔任务", (10, 240, 180, 258), 0.99),
             ("前往火云洞、火云洞处使用", (40, 260, 200, 280), 0.99)]
    assert wubei._xj_map_matches(("火云戈壁", 162, 71), items)
    assert wubei._xj_map_matches(("火云洞", 70, 27), items)
    assert not wubei._xj_map_matches(("宝象国", 77, 130), items)
    assert not wubei._xj_map_matches(("平顶山", 100, 30), items)
    assert not wubei._xj_map_matches(None, items)
    # 链接行拆行：第二处地名可能落在无「前往」的「处使用」碎片行
    split = [("前往火云洞、", (40, 258, 120, 274), 0.99),
             ("火云戈壁处使用", (40, 276, 200, 292), 0.99)]
    assert wubei._xj_map_matches(("火云戈壁", 47, 72), split)


def test_xj_map_gate_vetoes_use_and_reclicks_link(monkeypatch):
    """站定在领取城（任务地点是别的地图）不得用镜：限频重点寻路链接
    （2026-11-11 13:17 宝象国 77,130 实测）。"""
    used, walks, logs = [], [], []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos", lambda win, items: ("宝象国", 77, 130))
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(wubei, "_click_xianxingjing_use",
                        lambda win, items, img_np: used.append(1) or True)
    monkeypatch.setattr(wubei, "_click_xianxingjing_walk",
                        lambda win, items, img_np, side="b":
                        walks.append(side) or True)
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    monkeypatch.setattr(wubei, "_xj_stable", 3)
    monkeypatch.setattr(wubei, "_xj_anchor", ("宝象国", 77, 130))
    monkeypatch.setattr(wubei, "_xj_last_pos", ("宝象国", 77, 130))
    monkeypatch.setattr(wubei, "_xj_use_count", 0)
    monkeypatch.setattr(wubei, "_xj_need_move", False)
    monkeypatch.setattr(wubei, "_xj_move_stuck", 0)
    monkeypatch.setattr(wubei, "_xj_walk_side", None)
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    monkeypatch.setattr(wubei, "_xj_veto_click_at", 0.0)
    items = [("前往火云洞、火云洞处使用", (40, 260, 200, 280), 0.99),
             ("显形镜查探。", (40, 276, 140, 292), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing_track(object(), items, img) is True
    assert used == []
    assert walks == ["a"]
    assert any("不在任务地点" in m for m in logs)
    assert wubei._xj_need_move is True  # 重点链接后须先见移动才允许用镜


def test_xj_keyword_miss_reset_keeps_quota(monkeypatch):
    """follow 轮次重启时关键词瞬时消失：安全复位保留 A/B 配额与侧别，
    不再退回「第 1/2 处」重复用 A 侧（2026-11-11 13:21:26）。"""
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_xj_phase", 1)
    monkeypatch.setattr(wubei, "_xj_use_count", 1)
    monkeypatch.setattr(wubei, "_xj_walk_side", "b")
    monkeypatch.setattr(wubei, "_xj_warned", True)
    monkeypatch.setattr(wubei, "_xj_quota_exhausted_at", 123.0)
    monkeypatch.setattr(wubei, "_xj_missing", 1)
    items = [("宝象迷情降魔任务", (10, 240, 180, 258), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing_track(object(), items, img) is False
    assert wubei._xj_use_count == 1
    assert wubei._xj_walk_side == "b"
    assert wubei._xj_warned is True         # 配额卡死计时不被重启
    assert wubei._xj_quota_exhausted_at == 123.0
    assert wubei._xj_phase == 0             # 瞬时状态照常复位
    assert wubei._xj_missing == 0


def test_xj_flip_retry_capped(monkeypatch):
    """寻路未启动翻侧 3 次后按失败替换，不再 A/B 无限乒乓
    （2026-11-11 13:09 连续 11 次/50s 实测）。"""
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_pos",
                        lambda win, items: ("火云洞", 73, 31))
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_click_xianxingjing_walk",
                        lambda win, items, img_np, side="b": True)
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    monkeypatch.setattr(wubei, "_xj_stable", 1)
    monkeypatch.setattr(wubei, "_xj_anchor", ("火云洞", 73, 31))
    monkeypatch.setattr(wubei, "_xj_use_count", 1)
    monkeypatch.setattr(wubei, "_xj_need_move", True)
    monkeypatch.setattr(wubei, "_xj_walk_from", ("火云洞", 73, 31))
    monkeypatch.setattr(wubei, "_xj_move_stuck",
                        wubei.XIANXINGJING_MOVE_STUCK_MAX - 1)
    monkeypatch.setattr(wubei, "_xj_flip_count", wubei.XIANXINGJING_FLIP_MAX)
    monkeypatch.setattr(wubei, "_xj_walk_side", "a")
    monkeypatch.setattr(wubei, "_xj_missing", 0)
    monkeypatch.setattr(wubei, "_xj_need_replace", False)
    items = [("前往火云洞、火云洞处使用", (40, 260, 200, 280), 0.99),
             ("显形镜查探。", (40, 276, 140, 292), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    with pytest.raises(wubei.ReplaceTaskError):
        wubei._handle_xianxingjing_track(object(), items, img)
    assert wubei._xj_need_replace is True


def test_xj_walk_click_never_clicks_use_line(monkeypatch):
    """链接行缺失时「用显形镜查探」行不得被当寻路链接点（点它=用镜）。"""
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.task_tracker, "is_green_text",
                        lambda img, rect: True)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    use_only = [("用显形镜查探。", [40, 276, 140, 292], 0.99)]
    assert wubei._click_xianxingjing_walk(object(), use_only, img,
                                          side="a") is False
    assert clicks == []
    both = [("前往火云洞、火云洞处使用", [40, 258, 200, 274], 0.99),
            ("用显形镜查探。", [40, 276, 140, 292], 0.99)]
    assert wubei._click_xianxingjing_walk(object(), both, img, side="a") is True
    assert clicks == [(120, 266)]  # 点的是前往行中心，不是用镜行


# ---- 显形镜任务按钮：模板未现但对话框仍在场 → 继续点屏推进（2026-11-11） ----

def _patch_xj_btn_env(monkeypatch, score, pts):
    """装配 _handle_xianxingjing 找模板窗口环境。"""
    clicks, logs = [], []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_dialog_present", lambda items: False)
    monkeypatch.setattr(wubei, "_xj_fight_dialog_open", lambda items: False)
    monkeypatch.setattr(wubei, "_xj_frame",
                        lambda win, items: (None, np.zeros((10, 10, 3),
                                                           dtype=np.uint8)))
    monkeypatch.setattr(wubei.game_map, "best_match_np",
                        lambda bgr, icon: (score, (0, 0)))
    monkeypatch.setattr(wubei.game_map, "match_frame_all",
                        lambda bgr, icon, threshold=0.8: pts)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img_np: 0.99)
    monkeypatch.setattr(wubei, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append("center"))
    monkeypatch.setattr(wubei, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(wubei, "_xj_btn_clicked_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_btn_missing", 0)
    monkeypatch.setattr(wubei, "_xj_dismissed_at", wubei.time.time() - 1.0)
    monkeypatch.setattr(wubei, "_xj_dialog_clicked_at",
                        wubei.time.time() - wubei.XIANXINGJING_RESIDUAL_CLICK
                        - 0.1)
    monkeypatch.setattr(wubei, "_xj_tpl_best", (0.0, (0, 0)))
    return clicks, logs


def test_xj_template_missing_dialog_present_reclicks(monkeypatch):
    """模板未现但标准对话框仍在场：继续点屏推进（白龙马文本页两次点屏
    有一次被开场动画吞掉时，按钮被剩余文字遮挡，2026-11-11 14:11 实测）。"""
    clicks, logs = _patch_xj_btn_env(monkeypatch, score=0.62, pts=[])
    items = [("前往火云洞、火云洞处使用", (40, 258, 200, 274), 0.99),
             ("显形镜查探。", (40, 276, 140, 292), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    # 返回 True（本帧已处理）：同帧剧情兜底不再点一次，防双点打到世界
    assert wubei._handle_xianxingjing(object(), items, img) is True
    assert clicks == ["center"]
    assert any("继续点屏幕中间推进" in m for m in logs)
    assert wubei._xj_tpl_best[0] == 0.62  # 失败诊断用最高分


def _patch_xj_dialog_env(monkeypatch, last_center_click_ago):
    clicks, logs = [], []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "_xj_dialog_present", lambda items: True)
    monkeypatch.setattr(wubei, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append("center"))
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(wubei, "dc_last_center_click_at",
                        lambda: wubei.time.time() - last_center_click_ago)
    monkeypatch.setattr(wubei, "_xj_dialog_clicked_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_dismissed_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_tpl_best", (0.9, (1, 1)))
    monkeypatch.setattr(wubei, "_xj_quota_exhausted_at", 0.0)
    monkeypatch.setattr(wubei, "_xj_warned", False)
    return clicks, logs


def test_xj_dialog_click_skipped_when_story_gate_just_clicked(monkeypatch):
    """剧情兜底刚点过屏幕中间（白龙马文案尚未 OCR 出的过渡帧）：显形镜
    处理器不得补点——对话框一次即关，第二次点击落到世界让人物走动
    （2026-11-11 14:11 用户确认，当日 5/26 次模板未命中）。"""
    clicks, logs = _patch_xj_dialog_env(monkeypatch, last_center_click_ago=0.3)
    items = [("一阵光芒闪过，白龙马出现在眼前", (400, 300, 900, 340), 0.9)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing(object(), items, img) is True
    assert clicks == []
    assert any("不重复点击" in m for m in logs)
    assert wubei._xj_dismissed_at > 0  # 模板窗口照常开启


def test_xj_dialog_click_after_cooldown_clicks_once(monkeypatch):
    """无他人点击时显形镜处理器照常点一次关闭对话框。"""
    clicks, _logs = _patch_xj_dialog_env(monkeypatch,
                                         last_center_click_ago=
                                         wubei.XIANXINGJING_XJ_CLICK_GUARD + 1)
    items = [("一阵光芒闪过，白龙马出现在眼前", (400, 300, 900, 340), 0.9)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing(object(), items, img) is True
    assert clicks == ["center"]
    assert wubei._xj_dismissed_at > 0


def test_xj_template_hit_clicks_button_not_reclick(monkeypatch):
    """模板达标时点击按钮、不点屏推进（回归：先取最高分再取候选）。"""
    clicks, _logs = _patch_xj_btn_env(monkeypatch, score=0.92,
                                      pts=[(0.92, 100, 200)])
    items = [("前往火云洞、火云洞处使用", (40, 258, 200, 274), 0.99),
             ("显形镜查探。", (40, 276, 140, 292), 0.99)]
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert wubei._handle_xianxingjing(object(), items, img) is True
    assert clicks == [(100, 200)]
    assert wubei._xj_btn_clicked_at > 0
    assert wubei._xj_dismissed_at == 0.0


# ---- 显形镜：无本买卖对话框（用镜前后均点「偶然路过」关闭，2026-11-12） ----

def _patch_xj_wuben_env(monkeypatch):
    """装配无本买卖对话框环境（共享选项点击路径的依赖一并替换）。"""
    from src.device import dialog_common
    clicks, logs = [], []
    monkeypatch.setattr(wubei, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(wubei, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(dialog_common, "log",
                        lambda msg, level="INFO": logs.append(msg))
    monkeypatch.setattr(dialog_common, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(dialog_common, "move_away", lambda win: None)
    monkeypatch.setattr(dialog_common.app_utils, "fixed_pos_set",
                        lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_xj_block_clicked_at", 0.0)
    return clicks, logs


def _wuben_items():
    return [("无本的买卖最好做", (400, 260, 900, 290), 0.95),
            ("偶然路过", (430, 300, 560, 330), 0.95)]


def test_xj_wuben_dialog_clicks_option(monkeypatch):
    """用镜前（phase 0）出现无本买卖对话框：点「偶然路过」关闭并接管
    本帧；冷却内不连点（对话框关闭动画期）。"""
    clicks, logs = _patch_xj_wuben_env(monkeypatch)
    monkeypatch.setattr(wubei, "_xj_phase", 0)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[300:330, 430:560] = (0, 255, 0)  # 选项绿字
    assert wubei._handle_xianxingjing(object(), _wuben_items(), img) is True
    assert len(clicks) == 1
    assert wubei._xj_block_clicked_at > 0
    assert any("偶然路过" in m for m in logs)
    assert wubei._handle_xianxingjing(object(), _wuben_items(), img) is True
    assert len(clicks) == 1  # 防连点


def test_xj_wuben_after_use_freezes_grace(monkeypatch):
    """用镜后（phase 1 宽限窗口）出现：同样接管并冻结宽限计时——
    对话框处理期间不得按宽限过期误转另一处。"""
    clicks, _logs = _patch_xj_wuben_env(monkeypatch)
    monkeypatch.setattr(wubei, "_xj_phase", 1)
    monkeypatch.setattr(wubei, "_xj_grace_start", 0.0)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    img[300:330, 430:560] = (0, 255, 0)
    assert wubei._handle_xianxingjing(object(), _wuben_items(), img) is True
    assert len(clicks) == 1
    assert wubei._xj_grace_start > 0


def test_xj_wuben_option_unread_no_blind_click(monkeypatch):
    """选项未识别到：仍接管本帧（防剧情兜底点屏），但不盲点。"""
    clicks, _logs = _patch_xj_wuben_env(monkeypatch)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    items = [("无本的买卖最好做", (400, 260, 900, 290), 0.95)]
    assert wubei._handle_xj_wuben(object(), items, img) is True
    assert clicks == []


def test_xj_wuben_absent_not_handled():
    """其他对话（白龙马长句）不得被无本买卖处理器接管。"""
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    items = [("一阵光芒闪过，白龙马出现在眼前", (400, 300, 900, 340), 0.9)]
    assert wubei._handle_xj_wuben(object(), items, img) is False


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


# ---- 战斗失败重新领取（用户 2026-11-14：五倍不取消任务，直接重领） ----

def test_on_battle_end_dead_in_lunhuisi(monkeypatch):
    """战斗结束位置为轮回司：判定失败（死亡优先于任务消失确认）。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("轮回司", 38, 20))
    monkeypatch.setattr(wubei, "_battle_failed_reason", None)
    monkeypatch.setattr(wubei, "_fight_via_kanda", True)
    monkeypatch.setattr(wubei, "_hpg_task_seen", False)
    assert wubei._on_battle_end(_FakeWin(), _FakeWin().capture()) is True
    assert wubei._battle_failed_reason == "dead"


def test_on_battle_end_task_present_after_dialog_battle(monkeypatch):
    """对话开打后战斗结束任务仍在（复核仍在）：判定失败。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: (wubei.BAOXIANGGUO, 100, 120))
    monkeypatch.setattr(wubei, "_battle_failed_reason", None)
    monkeypatch.setattr(wubei, "_fight_via_kanda", True)
    monkeypatch.setattr(wubei, "_hpg_task_seen", False)
    monkeypatch.setattr(
        wubei.task_tracker, "task_absent_in_frame",
        lambda img, title, fb=(): (
            False, [("前往平顶山消灭魔尊", (14, 249, 185, 269), 0.99)]))
    assert wubei._on_battle_end(_FakeWin(), _FakeWin().capture()) is True
    assert wubei._battle_failed_reason == "task"
    assert clock.t >= 0.5  # 战斗结束首帧后复核一次


def test_on_battle_end_task_lingering_row_not_failure(monkeypatch):
    """完成瞬间任务行仍在退出动画：复核已消失 → 不判失败。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: (wubei.BAOXIANGGUO, 100, 120))
    monkeypatch.setattr(wubei, "_battle_failed_reason", None)
    monkeypatch.setattr(wubei, "_fight_via_kanda", True)
    monkeypatch.setattr(wubei, "_hpg_task_seen", False)
    seq = [(False, [("前往平顶山消灭魔尊", (14, 249, 185, 269), 0.99)]),
           (True, [])]
    monkeypatch.setattr(wubei.task_tracker, "task_absent_in_frame",
                        lambda img, title, fb=(): seq.pop(0))
    assert wubei._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert wubei._battle_failed_reason is None


def test_on_battle_end_huangpao_multi_kill_not_failure(monkeypatch):
    """黄袍怪多杀两场之间任务仍在属正常，不看任务存在条件。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: (wubei.BAOXIANGGUO, 100, 120))
    monkeypatch.setattr(wubei, "_battle_failed_reason", None)
    monkeypatch.setattr(wubei, "_fight_via_kanda", True)
    monkeypatch.setattr(wubei, "_hpg_task_seen", True)
    monkeypatch.setattr(
        wubei.task_tracker, "task_absent_in_frame",
        lambda *a, **k: (
            False, [("前往平顶山消灭黄袍怪", (14, 249, 185, 269), 0.99)]))
    assert wubei._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert wubei._battle_failed_reason is None


def test_on_battle_end_random_battle_not_failure(monkeypatch):
    """随机遭遇战结束任务仍在属正常（非对话开打门控）。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: (wubei.BAOXIANGGUO, 100, 120))
    monkeypatch.setattr(wubei, "_battle_failed_reason", None)
    monkeypatch.setattr(wubei, "_fight_via_kanda", False)
    monkeypatch.setattr(wubei, "_hpg_task_seen", False)
    monkeypatch.setattr(
        wubei.task_tracker, "task_absent_in_frame",
        lambda *a, **k: (
            False, [("前往平顶山消灭魔尊", (14, 249, 185, 269), 0.99)]))
    assert wubei._on_battle_end(_FakeWin(), _FakeWin().capture()) is False
    assert wubei._battle_failed_reason is None


def test_recover_battle_failure_reclaim_directly(monkeypatch):
    """队长已在宝象国：置位重领标记并强制队伍检查；不调用取消任务。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "time", _FakeClock())
    monkeypatch.setattr(wubei, "_check_team_with_retry",
                        lambda w, **k: ("ready", "队伍满足"))
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: (wubei.BAOXIANGGUO, 94, 84))
    cancelled = []
    monkeypatch.setattr(wubei, "_cancel_task",
                        lambda w: cancelled.append(1), raising=False)
    monkeypatch.setattr(wubei, "_battle_failed_reclaim", False)
    monkeypatch.setattr(wubei, "_battle_recovery_pending", True)
    monkeypatch.setattr(wubei, "_team_checked", True)
    status, info = wubei._recover_battle_failure(_FakeWin(), True)
    assert status == "reclaim" and "重新领取" in info
    assert wubei._battle_failed_reclaim is True
    assert wubei._team_checked is False
    assert wubei._battle_recovery_pending is False  # 恢复完成：清除重试标记
    assert cancelled == []  # 五倍不取消任务


def test_recover_battle_failure_non_leader_keep_alive(monkeypatch):
    """非队长：不重新领取，转保持模式并自动归队。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(
        wubei, "_check_team_with_retry",
        lambda w, **k: ("not_leader", "非队长（队伍面板无解散按钮）"))
    kept = []
    monkeypatch.setattr(wubei, "_keep_alive",
                        lambda w, info: kept.append(info))
    status, info = wubei._recover_battle_failure(_FakeWin(), True)
    assert status == "failed" and "保持模式" in info
    assert kept and "非队长" in kept[0]


def test_recover_battle_failure_lunhuisi_uses_talisman(monkeypatch):
    """落轮回司：世界地图不可用，走五倍回城符返程。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "time", _FakeClock())
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("轮回司", 38, 20))
    calls = []
    monkeypatch.setattr(
        wubei, "_return_to_npc",
        lambda w: calls.append("talisman") or (True, "已返回宝象国"))
    monkeypatch.setattr(
        wubei, "_walk_to_claim_spot",
        lambda w, **k: (_ for _ in ()).throw(
            AssertionError("轮回司不应走世界地图")))
    monkeypatch.setattr(wubei, "_team_checked", True)
    monkeypatch.setattr(wubei, "_battle_recovery_pending", True)
    status, _info = wubei._recover_battle_failure(_FakeWin(), False)
    assert status == "reclaim" and calls == ["talisman"]
    assert wubei._battle_recovery_pending is False


def test_recover_battle_failure_return_failed(monkeypatch):
    """回宝象国多次失败：报告失败（主循环下轮重试）。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("长安", 100, 100))
    monkeypatch.setattr(wubei, "_walk_to_claim_spot",
                        lambda w, **k: (False, "寻路超时"))
    monkeypatch.setattr(wubei, "_return_to_npc",
                        lambda w: (False, "背包打开失败"))
    monkeypatch.setattr(wubei, "_battle_recovery_pending", False)
    status, info = wubei._recover_battle_failure(_FakeWin(), False)
    assert status == "failed" and "回宝象国失败" in info
    assert clock.t >= 3 * 3.0
    # 恢复未完成：下一轮先重试恢复，不得回走任务跟踪
    assert wubei._battle_recovery_pending is True


def test_recover_battle_failure_transient_identity_sets_pending(monkeypatch):
    """身份瞬态无法判定：标记待恢复重试，不贸然重新领取。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_check_team_with_retry",
                        lambda w, **k: ("not_leader", "队长无法判定（面板按钮未命中）"))
    monkeypatch.setattr(wubei, "_battle_recovery_pending", False)
    status, info = wubei._recover_battle_failure(_FakeWin(), True)
    assert status == "failed" and "身份无法判定" in info
    assert wubei._battle_recovery_pending is True


def test_follow_to_done_wires_battle_end_handler(monkeypatch):
    """跟踪起点复位开打标记与失败原因，并注入战斗结束回调。"""
    captured = {}

    def fake_follow(win, first_items=None, **kwargs):
        captured.update(kwargs)
        return True, "完成"

    monkeypatch.setattr(wubei.claim_flow, "follow_to_done", fake_follow)
    monkeypatch.setattr(wubei, "_fight_via_kanda", True)
    monkeypatch.setattr(wubei, "_battle_failed_reason", "dead")
    ok, _info = wubei._follow_to_done(_FakeWin())
    assert ok is True
    assert captured["battle_end_handler"] is wubei._on_battle_end
    assert wubei._fight_via_kanda is False
    assert wubei._battle_failed_reason is None

