"""wubei.py：黄袍怪多杀任务战斗结束后的追踪链接重点点击。

点击位置回归 2026-11-03：旧实现点 OCR 文本矩形中心——整行
「前往平顶山消灭黄袍怪」矩形中心 (99,261) 落在可点击链接「平顶山」
右缘外（同帧对比 follow 路径 green_text_center 点 (76,268)），偏 23px
点到非链接文字偶现点击无反应。修正后点下划线中心（链接专属标记）。
"""
import numpy as np
import pytest

from src.device import demon_task
from src.tasks import wubei


@pytest.fixture(autouse=True)
def _reset_claim_walk_state(monkeypatch):
    """小地图走位/任务筛选状态跨用例隔离。"""
    monkeypatch.setattr(wubei, "_arrive_by_walk", False)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", None)
    monkeypatch.setattr(wubei, "_claim_map_kept_open", False)
    monkeypatch.setattr(wubei, "_claim_arrive_dialog_frame", None)
    # 默认追踪面板不在场：_FakeWin 的 60x60 帧小于标题条模板，真实
    # panel_visible 会触发 cv2 尺寸断言且随 3s 全帧限频在用例间抖动。
    # 需要面板在场的用例自行 patch 为 True（后者覆盖本夹具）。
    monkeypatch.setattr(wubei.task_tracker, "panel_visible",
                        lambda **k: False)


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


def test_claimed_task_replace_kind_suoyaowang(monkeypatch):
    """新领取任务为锁妖网：修理前先替换（用户 2026-11-23 顺序修正）。"""
    monkeypatch.setattr(wubei, "_track_state", lambda w: (True, True, False))
    assert wubei._claimed_task_replace_kind(_FakeWin()) == "锁妖网"


def test_claimed_task_replace_kind_kuimulang_switch(monkeypatch):
    """奎木狼仅在「领取奎木狼任务」开关关闭时需要替换。"""
    monkeypatch.setattr(wubei, "_track_state", lambda w: (True, False, True))
    monkeypatch.setattr(wubei, "_kuimulang_on", False)
    assert wubei._claimed_task_replace_kind(_FakeWin()) == "奎木狼"
    monkeypatch.setattr(wubei, "_kuimulang_on", True)
    assert wubei._claimed_task_replace_kind(_FakeWin()) is None


def test_claimed_task_replace_kind_normal_and_unknown(monkeypatch):
    monkeypatch.setattr(wubei, "_track_state", lambda w: (True, False, False))
    assert wubei._claimed_task_replace_kind(_FakeWin()) is None
    # 面板过渡/关闭读不到任务：不阻塞修理，跟随期仍会兜底替换
    monkeypatch.setattr(wubei, "_track_state", lambda w: (False, False, False))
    assert wubei._claimed_task_replace_kind(_FakeWin()) is None


def test_claim_task_skips_when_task_reappeared(monkeypatch):
    """误判消失走了领取、任务又出现（面板在场）：跳过领取，交主循环续跑跟踪。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "panel_visible",
                        lambda **k: True)
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


def test_claim_task_claims_when_tracker_panel_absent(monkeypatch):
    """追踪面板不在场：静态区域 OCR 残留任务文案不得拦截领取。

    2026-11-23 实机：锁妖网任务领取后追踪标题条未匹配，_claim_task
    用静态区域 OCR 判「任务仍在」→ 连续 18 轮「跳过领取（续跑跟踪）」
    40s 空转。面板缺席应与 _track_state 同语义走领取/替换。
    """
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "panel_visible",
                        lambda **k: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track",
                        lambda img: ([("使用任务栏的锁妖网捕捉", [10, 240, 200, 260], 0.99)], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: "sig")
    called = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: called.append(1) or (False, "入口失败", None, None, None))
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert ok is False and info == "入口失败" and frame is None
    assert called == [1]  # 面板缺席：进入领取入口流程，不再空转跳过


def test_claim_task_manual_nav_accepts_without_tracker(monkeypatch):
    """兼容无快捷寻路：无追踪 + 选项消失 + 收尾对话框在场 → 领取成功。"""
    _claim_task_same_task_env(monkeypatch, option_still=False)
    # 无任务追踪场景：面板标题条门控失败（pre_sig=None）、面板内无任务
    monkeypatch.setattr(wubei.task_tracker, "panel_visible",
                        lambda **k: False)
    monkeypatch.setattr(wubei.task_tracker, "has_task_in",
                        lambda *a, **k: False)
    ok, info, frame = wubei._claim_task(_FakeWin(), manual_nav=True)
    assert ok is True and info == "五倍任务已领取" and frame is not None


def _manual_rec(kind, map_name="平顶山", a=(187, 27), b=(269, 28),
                max_rounds=None):
    if max_rounds is None:
        max_rounds = 1 if kind == "kill" else (5 if kind == "huangpao"
                                               else None)
    return {"task": "测试任务", "kind": kind, "map": map_name,
            "coords": [c for c in (a, b) if c],
            "coord_a": a, "coord_b": b,
            "max_rounds": max_rounds,
            "reclaim": kind == "reclaim",
            "reclaim_reason": "测试需重领" if kind == "reclaim" else "",
            "target": "目标", "item": None, "item_icon": None}


def test_manual_nav_round_kill(monkeypatch):
    """击杀：寻路 → 模板开打 → 1 场战斗结束直接返程，不再读任务栏。

    用户 2026-11-24：单次战斗结束后任务即完成，读两轮任务栏确认白白
    多耗 ~8s（日志 13:33:07 战斗完成 → 13:33:16 才返程）。
    """
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    nav, fights = [], []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))
    monkeypatch.setattr(wubei, "_manual_template_fight",
                        lambda w, **k: fights.append(1)
                        or (True, "结束", (100, 200)))

    def no_final_read(w):
        raise AssertionError("单场击杀完成后不应再读任务栏确认")

    monkeypatch.setattr(wubei, "_manual_read_task_after_battle",
                        no_final_read)
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": _manual_rec("kill", "万寿山",
                                                           (37, 11), None)})
    assert ok is True and "直接返程" in info
    # 单杀：寻路一次、战斗一次
    assert nav == [("万寿山", (37, 11))] and fights == [1]


def test_manual_nav_round_kuixing_guiwei_single_kill(monkeypatch):
    """奎星归位（奎木狼）：任务目的无击杀动词，按任务名识别为单场击杀。

    用户 2026-11-25 实机截图/确认：任务目的「奎木狼(火云戈壁，134，12)
    终于要出动了，与他一决雌雄吧！」；同样是杀一次。
    """
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    monkeypatch.setattr(wubei, "_kuimulang_on", True)
    rec = demon_task.recognize(
        "奎星归位",
        "奎木狼(火云戈壁，134，12)终于要出动了，与他一决雌雄吧！")
    nav, fights, names = [], [], []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))

    def fake_fight(w, **k):
        fights.append(1)
        names.append(k.get("target_names"))
        return True, "结束", (100, 200)

    monkeypatch.setattr(wubei, "_manual_template_fight", fake_fight)
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": rec})
    assert ok is True and "直接返程" in info
    assert nav == [("火云戈壁", (134, 12))]
    assert fights == [1]  # 杀一次
    assert names == [["奎木狼"]]  # 世界区名字兜底使用任务栏已知目标名


def test_manual_nav_round_kuimulang_switch_off_reclaims(monkeypatch):
    """领取奎木狼开关关闭：手动链路同样不寻路，置重领标志下一轮替换。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    monkeypatch.setattr(wubei, "_kuimulang_on", False)
    rec = demon_task.recognize(
        "奎星归位",
        "奎木狼(火云戈壁，134，12)终于要出动了，与他一决雌雄吧！")
    wubei._manual_reclaim_pending = False
    nav = []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": rec})
    assert ok is False and "奎木狼" in info and nav == []
    assert wubei._manual_reclaim_pending is True
    wubei._manual_reclaim_pending = False  # 复位，防跨测试污染


def test_manual_nav_round_huangpao_multi(monkeypatch):
    """黄袍 5 杀：首杀寻路，之后复用命中模板直接再杀，不重新寻路。

    两场战斗之间（结算界面恢复后的间隙）须补调组号队员血蓝 pass
    （用户 2026-11-24：多杀期间队员得不到回血回蓝检查）。
    """
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    monkeypatch.setattr(wubei, "time", _FakeClock())
    rec = _manual_rec("huangpao", "平顶山", (302, 22), None,
                      max_rounds=5)
    nav, pref, bars = [], [], []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))
    monkeypatch.setattr(wubei, "_maybe_tab_bars_pass",
                        lambda w: bars.append(1))

    def fake_fight(w, preferred_pt=None, wait=None, **k):
        pref.append(preferred_pt)
        return True, "结束", (321, 222)

    monkeypatch.setattr(wubei, "_manual_template_fight", fake_fight)

    def no_final_read(w):
        raise AssertionError("黄袍 5 杀打满后不应再读任务栏确认")

    monkeypatch.setattr(wubei, "_manual_read_task_after_battle",
                        no_final_read)
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": rec})
    assert ok is True and "5 次战斗" in info and "直接返程" in info
    assert nav == [("平顶山", (302, 22))]  # 只在首杀寻路
    # 首杀全量搜索；之后复用首杀命中的模板点
    assert pref == [None, (321, 222), (321, 222), (321, 222), (321, 222)]
    # 第 2~5 场开打前各补调一次（共 4 次）；首杀由寻路 on_walk_start 触发
    assert bars == [1, 1, 1, 1]


def test_manual_nav_round_huangpao_early_gone(monkeypatch):
    """黄袍预计 5 杀但任务提前消失：复用点未触发时先读任务栏，消失即完成。"""
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    rec = _manual_rec("huangpao", "平顶山", (302, 22), None,
                      max_rounds=5)
    nav, calls = [], []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))

    def fake_fight(w, preferred_pt=None, wait=None, **k):
        calls.append(preferred_pt)
        if len(calls) == 1:
            return True, "结束", (321, 222)
        return False, "模板未出现", None

    monkeypatch.setattr(wubei, "_manual_template_fight", fake_fight)
    monkeypatch.setattr(wubei, "_manual_read_task_after_battle",
                        lambda w: ("gone", None))
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": rec})
    assert ok is True and "提前完成" in info
    assert nav == [("平顶山", (302, 22))]  # 只在首杀寻路
    assert calls == [None, (321, 222)]  # 第 2 杀复用点失败后判定任务已消失


def test_manual_task_read_once_statuses(monkeypatch):
    """任务栏读取三态：present / gone（明确无分组）/ unknown（读取失败）。"""
    rec = {"recognition": _manual_rec("kill")}
    seq = iter([(rec, "ok"),
                (None, "展开分组失败: 任务列表中未找到「降魔」分组"),
                (None, "未定位到任务栏面板")])
    monkeypatch.setattr(wubei.task_panel, "read_first_group_task",
                        lambda w: next(seq))
    assert wubei._manual_task_read_once(_FakeWin()) == ("present", rec)
    assert wubei._manual_task_read_once(_FakeWin()) == ("gone", None)
    assert wubei._manual_task_read_once(_FakeWin()) == ("unknown", None)


def test_manual_read_after_battle_unknown_not_completed(monkeypatch):
    """任务栏读取失败不得按任务消失处理（防误计数）。"""
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei.task_panel, "read_first_group_task",
                        lambda w: (None, "未定位到任务栏面板"))
    assert wubei._manual_read_task_after_battle(_FakeWin()) == ("unknown", None)
    monkeypatch.setattr(wubei.task_panel, "read_first_group_task",
                        lambda w: (None,
                                   "展开分组失败: 任务列表中未找到「降魔」分组"))
    assert wubei._manual_read_task_after_battle(_FakeWin()) == ("gone", None)


def _xj_manual_dialog_env(monkeypatch, items):
    """装配 _manual_template_fight 显形镜腿的对话框场景（虚拟时钟）。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: (list(items), None))
    clicks = []
    monkeypatch.setattr(wubei, "_click_screen_center",
                        lambda win, times=1, prefix="": clicks.append(times))
    monkeypatch.setattr(wubei, "dc_last_center_click_at", lambda: 0.0)
    return clock, clicks


def test_manual_template_fight_story_dialog_skips_immediately(monkeypatch):
    """用镜后剧情对话框（非白龙马）两帧确认即判本处无怪。

    用户 2026-11-24 13:39 金兜洞 A 处：用镜后出现「有高僧自东土大唐
    而来…」剧情对话框，旧逻辑点屏推进后空等模板窗才换 B 处；
    现改为点屏关闭后立即返回失败，由调用方换下一处。
    """
    items = [("有高僧自东土大唐而来，礼乐雍容", [430, 520, 900, 560], 0.95)]
    clock, clicks = _xj_manual_dialog_env(monkeypatch, items)
    t0 = clock.t
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), expect_bailongma=True)
    assert ok is False and "未命中白龙马" in info and hit is None
    assert clicks == [1]          # 关闭剧情对话框一次（防吞寻路点击）
    assert clock.t - t0 < 3.0     # 两帧即判，不空等模板窗


def test_manual_template_fight_bailongma_dialog_not_misjudged(monkeypatch):
    """白龙马对话框命中文案：不按剧情页判失败，照常点屏找模板。"""
    items = [("一阵光芒闪过，白龙马出现在眼前，竟开口说起话来。",
              [430, 520, 950, 560], 0.95)]
    clock, clicks = _xj_manual_dialog_env(monkeypatch, items)
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), expect_bailongma=True, wait=1.0)
    assert ok is False and "未命中白龙马" not in info
    assert clicks                     # 白龙马对话框照常点屏关闭遮挡
    assert "未出现白龙马任务模板" in info


def _manual_alt_ocr_env(monkeypatch, points):
    """装配 _manual_template_fight 的「模板→Alt+A→名字 OCR」兜底场景。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    state = {"battle": False}

    def _is_battle(img):
        return state["battle"]

    monkeypatch.setattr(wubei, "is_in_battle_frame", _is_battle)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.0)
    monkeypatch.setattr(wubei.game_map, "match_frame_all",
                        lambda *a, **k: [])
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: ([], None))
    monkeypatch.setattr(wubei, "_manual_close_block_dialog",
                        lambda *a, **k: False)
    monkeypatch.setattr(wubei, "_xj_fight_dialog_open", lambda items: False)
    hotkeys = []
    monkeypatch.setattr(wubei.app_utils, "press_alt_hotkey",
                        lambda win, key, label, prefix="": (
                            hotkeys.append(key) or (True, "alt")))
    clicked = []
    point_values = set(points.values())

    def _click(win, x, y, **k):
        clicked.append((x, y))
        if (x, y) in point_values:
            state["battle"] = True  # 点中名字后进入战斗

    monkeypatch.setattr(wubei, "human_click", _click)
    monkeypatch.setattr(wubei, "move_away", lambda win: None)
    monkeypatch.setattr(wubei, "_manual_wait_battle_end",
                        lambda win: (True, "战斗已结束（手动寻路回合）"))
    named = []

    def _named(img, names):
        named.append(list(names))
        return [(t, pt) for t, pt in points.items()]

    monkeypatch.setattr(wubei.world_target, "find_named_points", _named)
    return state, hotkeys, clicked, named


def test_manual_template_fight_alt_ocr_name_fallback(monkeypatch):
    """模板短窗未现 → Alt+A 复查仍无 → OCR 世界区目标名 → 点名字上方开打。

    用户 2026-11-24：目标点在地图边缘时游戏不渲染任务小牌；五倍任务栏
    已知完整目标名，按名字找 NPC（点击名字上方 40px）兜底。
    """
    points = {"智慧魔尊": (544, 401)}
    state, hotkeys, clicked, named = _manual_alt_ocr_env(monkeypatch, points)
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), target_names=("智慧魔尊",))
    assert ok is True and info.startswith("战斗已结束")
    assert hit == (544, 401)
    assert hotkeys == ["a"]                 # 模板窗口后才按 Alt+A
    assert clicked == [(544, 401)]          # 点击 OCR 名字上方点击点
    assert named == [["智慧魔尊"]]


def test_manual_template_fight_alt_restored_on_failure(monkeypatch):
    """Alt+A + 名字 OCR 均未命中：点屏幕中间释放 Alt+A 后判失败。"""
    state, hotkeys, clicked, named = _manual_alt_ocr_env(monkeypatch, points={})
    centers = []
    monkeypatch.setattr(wubei, "_click_screen_center",
                        lambda win, times=1, prefix="": centers.append(times))
    monkeypatch.setattr(wubei, "dc_last_center_click_at", lambda: 0.0)
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), target_names=("智慧魔尊",))
    assert ok is False and hit is None
    assert "未出现白龙马任务模板" in info and "名字 OCR" in info
    assert hotkeys == ["a"]
    assert centers == [1]                   # 释放 Alt+A
    assert clicked == []


def test_manual_template_fight_coord_screen_fallback(monkeypatch):
    """模板未现 → 特殊地图先按任务坐标反查屏幕点，命中不按 Alt+A。

    用户 2026-11-24：目标名字被其他名字叠绘挡住、OCR 读不出时，按任务栏
    坐标反查屏幕点直接点（必须读 HUD 坐标，放在 Alt+A 之前）。
    """
    state, hotkeys, clicked, named = _manual_alt_ocr_env(monkeypatch, points={})
    seen = {}

    def _coord(win, name, x, y, emit=None):
        seen["args"] = (name, x, y)
        return (754, 427)

    monkeypatch.setattr(wubei.map_nav, "special_coord_screen_point", _coord)

    def _click(win, x, y, **k):
        clicked.append((x, y))
        state["battle"] = True  # 点中坐标反查点后进入战斗

    monkeypatch.setattr(wubei, "human_click", _click)
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), target_names=("智慧魔尊",),
        target_map="莲花洞", target_coord=(40, 48))
    assert ok is True and hit == (754, 427)
    assert seen["args"] == ("莲花洞", 40, 48)
    assert hotkeys == []      # 反查命中，不按 Alt+A
    assert named == []        # 未走到名字 OCR


def test_manual_template_fight_coord_unavailable_continues_alt(monkeypatch):
    """坐标反查不可用（普通地图/无锚点模型）→ 继续 Alt+A + 名字兜底。"""
    state, hotkeys, clicked, named = _manual_alt_ocr_env(monkeypatch, points={})
    monkeypatch.setattr(wubei.map_nav, "special_coord_screen_point",
                        lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_click_screen_center",
                        lambda win, times=1, prefix="": None)
    monkeypatch.setattr(wubei, "dc_last_center_click_at", lambda: 0.0)
    ok, info, hit = wubei._manual_template_fight(
        _FakeWin(), target_names=("智慧魔尊",),
        target_map="平顶山", target_coord=(163, 105))
    assert ok is False and hit is None
    assert hotkeys == ["a"]   # 反查不可用仍走 Alt+A
    assert named == [["智慧魔尊"]]  # 名字 OCR 兜底执行


def test_manual_target_names_huangpao_variants():
    """黄袍怪三种写法共用短名兜底；普通击杀用完整目标名。"""
    assert wubei._manual_target_names(
        {"kind": "huangpao", "target": "愤怒的黄袍怪"}) == \
        ["愤怒的黄袍怪", "黄袍怪"]
    assert wubei._manual_target_names(
        {"kind": "huangpao", "target": "黄袍怪"}) == ["黄袍怪"]
    assert wubei._manual_target_names(
        {"kind": "huangpao", "target": None}) == ["黄袍怪"]
    assert wubei._manual_target_names(
        {"kind": "kill", "target": "智慧魔尊"}) == ["智慧魔尊"]
    assert wubei._manual_target_names(
        {"kind": "kill", "target": None}) == []
    assert wubei._manual_target_names(None) == []


def test_manual_nav_round_xianxing_ab(monkeypatch):
    """显形镜：A 未完成 → B；B 战斗结束直接返程（不再读任务栏确认）。

    用户 2026-11-24：显形镜任一处战斗结束即任务完成，与单场击杀同规。
    """
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    rec = _manual_rec("xianxing")
    nav, fights, items = [], [], []
    bm_flags = []  # 用镜腿必须要求白龙马文案判定
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append((m, c)) or (True, "ok"))
    monkeypatch.setattr(wubei, "_manual_use_xianxingjing",
                        lambda w: items.append(1) or (True, "已使用"))
    results = iter([(False, "A 未触发", None),
                    (True, "B 战斗结束", (200, 300))])

    def fake_fight(w, **k):
        bm_flags.append(k.get("expect_bailongma"))
        fights.append(1)
        return next(results)

    monkeypatch.setattr(wubei, "_manual_template_fight", fake_fight)
    after = iter([("present", {"recognition": rec})])

    def read_after(w):
        try:
            return next(after)
        except StopIteration:
            raise AssertionError("显形镜战斗完成后不应再读任务栏确认")

    monkeypatch.setattr(wubei, "_manual_read_task_after_battle", read_after)
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": rec})
    assert ok is True and "直接返程" in info
    assert nav == [("平顶山", (187, 27)), ("平顶山", (269, 28))]
    assert len(items) == 2 and len(fights) == 2
    assert bm_flags == [True, True]  # 两条腿都走白龙马文案判定


def test_manual_nav_round_reclaim_fails_fast(monkeypatch):
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_manual_close_task_panel", lambda w: True)
    nav = []
    monkeypatch.setattr(wubei, "_manual_navigate",
                        lambda w, m, c: nav.append(1) or (True, "ok"))
    wubei._manual_reclaim_pending = False
    ok, info = wubei._manual_nav_round(
        _FakeWin(), pre_result={"recognition": _manual_rec("reclaim")})
    assert ok is False and "重新领取" in info and nav == []
    # 强制手动寻路时任务一直在追踪：置重领标志，下一轮走领取/替换分支，
    # 防每轮手动回合空转（暗雷/锁妖网无法手动推进）
    assert wubei._manual_reclaim_pending is True
    wubei._manual_reclaim_pending = False  # 复位，防跨测试污染


def test_claim_task_replace_allows_existing_task(monkeypatch):
    """替换任务场景（allow_existing=True）：面板有任务仍走领取流程。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
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


def test_claim_task_auto_dialog_clicks_claim_option(monkeypatch):
    """小地图走位到达+任务勾选：自动弹出的领取对话框直接点领取选项
    （人数不足由领取流程自带的确认/补人逻辑处理，用户 2026-11-16）。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    seen = ([], None)
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda w, needles, **k: seen)
    entry = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k) or (False, "x", None, None, None))
    options = []
    monkeypatch.setattr(wubei, "_click_claim_option",
                        lambda w, items, img_np: options.append(1) or False)
    monkeypatch.setattr(wubei, "CLAIM_DIALOG_TIMEOUT", 0)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert not ok and "领取对话框处理失败" in info and frame is None
    assert entry == []  # 已自动弹出对话框：不点领取入口
    assert options == [1]  # 直接点自动对话框的领取选项


def test_claim_task_auto_dialog_insufficient_team_fills(monkeypatch):
    """自动对话框点领取选项后人数不足 → 走自动补人逻辑（用户 2026-11-16）。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    seen = ([], None)
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda w, needles, **k: seen)
    entry = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k) or (False, "x", None, None, None))
    monkeypatch.setattr(wubei, "_click_claim_option",
                        lambda w, items, img_np: False)
    monkeypatch.setattr(wubei, "_dialog_text_present",
                        lambda items, txt: txt == wubei.FULL_TEAM_DIALOG_TEXT)
    monkeypatch.setattr(wubei.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    filled = []
    monkeypatch.setattr(wubei, "_monitor_team_and_fill",
                        lambda w, **k: filled.append(k))
    monkeypatch.setattr(wubei, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    # 跳过首次领取前队伍监控的路径：未组队时监控首次检查内自动创建
    ok, info, frame = wubei._claim_task(
        _FakeWin(), allow_fill=True, create_team_if_missing=True)
    assert not ok and "队伍已补齐" in info and frame is None
    assert entry == []  # 自动对话框已弹出：不点入口
    # 人数不足进入监控补人循环，并透传自动创建队伍开关
    assert filled == [{"create_if_missing": True}]


def test_claim_task_no_auto_dialog_check_when_standing(monkeypatch):
    """站着/返程到达：不检查自动对话框，正常走领取入口流程（原流程不变）。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(wubei, "_arrive_by_walk", False)
    auto = []
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: auto.append(1) or ([], None))
    entry = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k)
        or (False, "入口失败", None, None, None))
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert not ok and info == "入口失败" and frame is None
    assert auto == []  # 非走位到达：不查自动对话框
    assert entry and entry[0]["allow_auto_dialog"] is False


def test_walk_to_claim_spot_keeps_map_when_auto_dialog(monkeypatch):
    """走位后领取对话框已自动弹出（模态）：保留地图进领取流程
    （选项点掉后再补关），用户 2026-11-16。"""
    win = _FakeWin()
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("宝象国", 60, 60))

    def fake_open(w, pos=None):
        wubei._claim_task_filter_on = True  # 开小地图时读到的筛选状态
        return True, "已点击"

    def fake_close(w):
        wubei._claim_map_kept_open = True
        return True

    monkeypatch.setattr(wubei, "_open_claim_map", fake_open)
    monkeypatch.setattr(wubei, "_close_claim_map_before_arrival", fake_close)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    ok, info = wubei._walk_to_claim_spot(win)
    assert ok is True and "自动弹出" in info
    assert wubei._claim_map_kept_open is True


def test_close_claim_map_before_arrival_keeps_map_on_dialog(monkeypatch):
    """单次关地图失败且领取对话框已自动弹出：保留地图，不再做 3 次重试。"""
    win = _FakeWin()
    monkeypatch.setattr(wubei.game_map, "match_frame_np",
                        lambda *a, **k: (1030, 217))
    monkeypatch.setattr(wubei.game_map, "is_mini_map_open", lambda w: True)
    monkeypatch.setattr(wubei, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: ([], None))
    closed = []
    monkeypatch.setattr(wubei.game_map, "close_maps",
                        lambda w, quiet=False: closed.append(1))
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    assert wubei._close_claim_map_before_arrival(win) is True
    assert wubei._claim_map_kept_open is True
    assert closed == []  # 模态对话框拦着：不白跑 3 次重试


def test_close_claim_map_before_arrival_retries_when_no_dialog(monkeypatch):
    """关地图失败但无自动对话框（非对话框原因）：回退标准 close_maps 重试。"""
    win = _FakeWin()
    monkeypatch.setattr(wubei.game_map, "match_frame_np",
                        lambda *a, **k: (1030, 217))
    monkeypatch.setattr(wubei.game_map, "is_mini_map_open", lambda w: True)
    monkeypatch.setattr(wubei, "human_click", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(wubei.game_map, "close_maps",
                        lambda w, quiet=False: closed.append(1))
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    assert wubei._close_claim_map_before_arrival(win) is False
    assert wubei._claim_map_kept_open is False
    assert closed == [1]


# ---- 入口等待：自动对话框轮询 + 任务勾选走位驻足规则
# （2026-11-16 实机 18:32：黄字「降魔侍卫」走位终点 ≈86,86 不在落点
#  盒内，坐标门控不通过；自动对话框已在场仍空等入口窗口 8s）

def _entry_wait_env(monkeypatch, positions, dialog_score=0.0):
    """装配 _wait_claim_entry：领取按钮每轮命中、位置按序列返回、无战斗。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: dialog_score)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(
        wubei.game_map, "match_frame_multi",
        lambda frame, icons: {wubei.LINGQU_ICON: (818, 358),
                              wubei.XIANGMOSHIWEI_ICON: None})
    state = {"i": 0}

    def fake_read(win, img=None):
        i = min(state["i"], len(positions) - 1)
        state["i"] += 1
        return positions[i]

    monkeypatch.setattr(wubei.game_map, "read_map_position", fake_read)
    return state


def test_wait_claim_entry_relaxes_walk_gate_after_stop_on_task_click(monkeypatch):
    """任务筛选勾选：小地图黄字走位终点 ≈86,86（不在落点盒内），驻足
    （连续两轮坐标相同）后按站着规则放行点领取按钮（规则 4）。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    _entry_wait_env(monkeypatch, [
        (wubei.BAOXIANGGUO, 112, 95),   # 走位中：门控不通过
        (wubei.BAOXIANGGUO, 86, 86),    # 刚落点：环比一次不算驻足
        (wubei.BAOXIANGGUO, 86, 86),    # 驻足：放行
    ])
    pt, src, pos = wubei._wait_claim_entry(_FakeWin(), 10, arrive_by_walk=True)
    assert pt == (818, 358) and src == "领取任务按钮"
    assert pos == (wubei.BAOXIANGGUO, 86, 86)


def test_wait_claim_entry_relaxes_walk_gate_when_filter_off(monkeypatch):
    """任务筛选未勾选：驻足后同样放行（2026-11-25 修正）。

    2026-11-21 起领取点统一取消勾选筛选，旧条件（勾选/未知才放行）会
    永久禁用驻足规则；寻路终点不一定是落点盒 (94,84)±5（点击点压 NPC
    名会走到侍卫旁，市场人流挡路会中途停在 86,82）——2026-11-25 20:40
    验证领取卡在 (86,82)：入口模板命中但规则 1 永不命中。
    """
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    _entry_wait_env(monkeypatch, [
        (wubei.BAOXIANGGUO, 86, 82),   # 落脚：环比一次不算驻足
        (wubei.BAOXIANGGUO, 86, 82),   # 驻足：按站着规则放行
    ])
    pt, src, pos = wubei._wait_claim_entry(
        _FakeWin(), 10, arrive_by_walk=True)
    assert pt == (818, 358) and src == "领取任务按钮"
    assert pos == (wubei.BAOXIANGGUO, 86, 82)


def test_wait_claim_entry_no_early_click_while_walking(monkeypatch):
    """走位中位置每轮都变：即使坐标在领取点盒内也不提前点击
    （驻足规则只在停下后生效，保留 2026-10-29 防误点设计）。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    # 均在领取点 (86,86)±15 内、落点盒 (94,84)±5 外，且每轮坐标都变
    _entry_wait_env(monkeypatch, [
        (wubei.BAOXIANGGUO, 86, 88),
        (wubei.BAOXIANGGUO, 90, 90),
        (wubei.BAOXIANGGUO, 82, 90),
        (wubei.BAOXIANGGUO, 84, 88),
    ])
    assert wubei._wait_claim_entry(_FakeWin(), 5, arrive_by_walk=True) \
        == (None, None, None)


def test_wait_claim_entry_detects_auto_dialog_during_wait(monkeypatch):
    """入口等待期间自动弹出的领取对话框：轮询内命中即返回哨兵，
    调用方补查该帧直接点领取选项，不再空等整个入口窗口。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    _entry_wait_env(monkeypatch, [(wubei.BAOXIANGGUO, 112, 95)],
                    dialog_score=1.0)
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: ([], None))
    monkeypatch.setattr(wubei, "_find_claim_option",
                        lambda items, img_np: (534, 399, "除魔卫国，义不容辞", False))
    assert wubei._wait_claim_entry(
        _FakeWin(), 10, arrive_by_walk=True, allow_auto_dialog=True) \
        == (None, "自动对话框", None)


def test_wait_claim_entry_skips_dialog_check_when_filter_off(monkeypatch):
    """任务筛选未勾选：入口等待不查自动对话框（原流程零变化）。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    _entry_wait_env(monkeypatch, [(wubei.BAOXIANGGUO, 112, 95)],
                    dialog_score=1.0)
    calls = []
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: calls.append(1) or ([], None))
    wubei._wait_claim_entry(_FakeWin(), 3, arrive_by_walk=True,
                            allow_auto_dialog=True)
    assert calls == []


def test_claim_task_reuses_arrival_dialog_frame(monkeypatch):
    """到达检查已确认的对话框帧直接复用点选项（免重截+重 OCR）；
    对话框门控复核不在场时不复用陈旧帧，回退短窗轮询。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    monkeypatch.setattr(wubei, "CLAIM_DIALOG_TIMEOUT", 0)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_click_claim_option",
                        lambda w, items, img_np: False)

    # 1) 对话框门控达标（仍在场）→ 复用到达帧，不重查对话框
    stash = (["到达帧选项"], None)
    monkeypatch.setattr(wubei, "_claim_arrive_dialog_frame", stash)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.99)
    called = []
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: called.append(1) or ([], None))
    used = []
    monkeypatch.setattr(wubei, "_click_claim_option",
                        lambda w, items, img_np:
                        used.append((items, img_np)) or False)
    wubei._claim_task(_FakeWin())
    assert called == []  # 未重查对话框（复用到达检查帧）
    assert used == [(stash[0], stash[1])]  # 用到达检查帧点选项
    assert wubei._claim_arrive_dialog_frame is None  # 用后即弃

    # 2) 对话框门控不达标（已不在场）→ 不复用陈旧帧，回退短窗轮询
    monkeypatch.setattr(wubei, "_claim_arrive_dialog_frame", stash)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.3)
    called.clear()
    used.clear()
    wubei._claim_task(_FakeWin())
    assert called == [1]  # 回退重查
    assert used and used[0][0] == []  # 用重查帧（stub 返回空 items）


def test_claim_task_skips_auto_dialog_when_filter_off(monkeypatch):
    """小地图「任务」筛选未勾选：即使走位到达也不查自动对话框。"""
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: None)
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    auto = []
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: auto.append(1) or ([], None))
    entry = []
    monkeypatch.setattr(
        wubei, "_click_entry_ensuring_dialog",
        lambda w, **k: entry.append(k)
        or (False, "入口失败", None, None, None))
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert not ok and info == "入口失败" and frame is None
    assert auto == []
    assert entry and entry[0]["allow_auto_dialog"] is False


def test_auto_dialog_claim_mode_requires_walk_and_filter(monkeypatch):
    """自动对话框模式 = 小地图走位到达 + 任务筛选勾选/未知（None 也算）。

    该模式下首次领取前的队伍监控循环让位给领取流程（run_loop）：直接
    点自动弹出的对话框选项，人数不足走拒绝→监控补人（用户 2026-11-16
    实机 18:42：先监控会让对话框在场无人处理 10s+）。"""
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    assert wubei._auto_dialog_claim_mode() is True
    monkeypatch.setattr(wubei, "_claim_task_filter_on", None)
    assert wubei._auto_dialog_claim_mode() is True
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    assert wubei._auto_dialog_claim_mode() is False
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    monkeypatch.setattr(wubei, "_arrive_by_walk", False)  # 站着/返程到达
    assert wubei._auto_dialog_claim_mode() is False


def test_arrival_claim_dialog_up_requires_mode_and_dialog(monkeypatch):
    """到达后检查自动对话框：只有「黄字走位模式 + 对话框真的在场」才跳过
    队伍监控；没点中黄字（对话框没弹）→ False，走正常队伍监控。"""
    calls = []
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda w, needles, **k: calls.append(k.get("timeout"))
                        or ([], None))
    # 非黄字走位模式：不检查对话框
    monkeypatch.setattr(wubei, "_arrive_by_walk", False)
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    assert wubei._arrival_claim_dialog_up(_FakeWin()) is False
    assert calls == []
    # 黄字走位模式 + 对话框已弹出
    monkeypatch.setattr(wubei, "_arrive_by_walk", True)
    assert wubei._arrival_claim_dialog_up(_FakeWin()) is True
    # 黄字走位模式 + 无对话框（点在地面/没点中黄字）
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda w, needles, **k: None)
    assert wubei._arrival_claim_dialog_up(_FakeWin()) is False
    # 筛选未勾选：不检查
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    monkeypatch.setattr(wubei.claim_flow, "wait_auto_claim_dialog",
                        lambda *a, **k: ([], None))
    assert wubei._arrival_claim_dialog_up(_FakeWin()) is False


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



# ---- 暂离超时自动请离设置注入（与修罗同款，2026-11-15） ----

def test_team_flow_injects_kick_away_settings(monkeypatch):
    """五倍队伍检查传入请离开关与超时：不注入时 TeamFlow 走默认
    （恒请离、固定 5 分钟），五倍设置面板将无法生效。"""
    from src.workflows import team_flow
    calls = {}

    def _check(win, **kwargs):
        calls.update(kwargs)
        return "ready", "队伍已满", []

    monkeypatch.setattr(team_flow.team, "check_team_min", _check)
    monkeypatch.setattr(team_flow.team, "update_overlay_team", lambda *a: None)
    wubei._team_flow.check_team_ready(object())
    assert calls["kick_overdue"] is True     # 首次默认开启
    assert calls["kick_timeout"] == 300      # 首次默认 5 分钟


# ---- claim_flow 批次 2 包装接线与到达判据（2026-11-18）----

def test_claim_flow_open_map_wiring(monkeypatch):
    """包装必须把宝象国参数与筛选状态回调原样传给共享实现。"""
    captured = {}
    win = _FakeWin()

    def fake(win, pos=None, **kwargs):
        captured["win"], captured["pos"] = win, pos
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(wubei.claim_flow, "open_claim_map", fake)
    assert wubei._open_claim_map(win, ("宝象国", 1, 2)) == (True, "ok")
    assert captured["win"] is win
    assert captured["pos"] == ("宝象国", 1, 2)
    assert captured["map_name"] == wubei.BAOXIANGGUO
    assert captured["map_pos"] == wubei.BAOXIANG_MAP_POS
    assert captured["prefix"] == "[五倍]"
    assert captured["on_filter_state"] is wubei._set_claim_task_filter
    assert captured["map_coord"] == wubei.BAOXIANG_CLAIM_COORD


def test_claim_flow_set_filter_state_writes_global(monkeypatch):
    monkeypatch.setattr(wubei, "_claim_task_filter_on", True)
    wubei._set_claim_task_filter(None)
    assert wubei._claim_task_filter_on is None


def test_claim_flow_relocate_wiring(monkeypatch):
    captured = {}
    win = _FakeWin()

    def fake(win, **kwargs):
        captured["win"] = win
        captured.update(kwargs)
        return True, "ok"

    monkeypatch.setattr(wubei.claim_flow, "relocate_to_claim_spot", fake)
    assert wubei._relocate_to_claim_spot(win) == (True, "ok")
    assert captured["win"] is win
    assert captured["map_name"] == wubei.BAOXIANGGUO
    assert captured["open_map_fn"] is wubei._open_claim_map
    assert captured["close_map_fn"] is wubei._close_claim_map_before_arrival
    assert captured["arrival_fn"] is wubei._relocate_arrival
    assert captured["timeout"] == wubei.CLAIM_ARRIVE_TIMEOUT


def test_relocate_arrival_button_requires_map_gate(monkeypatch):
    """长安命中领取按钮模板（跨图同图）：不得判到达。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    monkeypatch.setattr(wubei.game_map, "match_frame_np",
                        lambda frame, icon, **k: (300, 300)
                        if icon == wubei.LINGQU_ICON else None)
    monkeypatch.setattr(wubei, "_near_walk_target_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "_near_xiangmoshiwei_spot", lambda pos: False)
    img = np.zeros((831, 1378, 3), np.uint8)
    assert wubei._relocate_arrival(
        None, img, img, ("长安", 367, 27)) is None


def test_relocate_arrival_button_in_map(monkeypatch):
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    monkeypatch.setattr(wubei.game_map, "match_frame_np",
                        lambda frame, icon, **k: (300, 300)
                        if icon == wubei.LINGQU_ICON else None)
    monkeypatch.setattr(wubei, "_near_walk_target_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "_near_xiangmoshiwei_spot", lambda pos: False)
    img = np.zeros((831, 1378, 3), np.uint8)
    desc = wubei._relocate_arrival(None, img, img, ("宝象国", 60, 60))
    assert desc and "领取按钮可见" in desc


def test_relocate_arrival_filter_false_skips_dialog_ocr(monkeypatch):
    """小地图「任务」筛选未勾选（False）：不做自动对话框 OCR。"""
    ocr = []
    monkeypatch.setattr(wubei, "_claim_task_filter_on", False)
    monkeypatch.setattr(wubei.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_near_walk_target_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "_near_xiangmoshiwei_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: ocr.append(1) or ([], None))
    img = np.zeros((831, 1378, 3), np.uint8)
    assert wubei._relocate_arrival(
        None, img, img, ("宝象国", 60, 60)) is None
    assert ocr == []


def test_relocate_arrival_filter_none_still_checks_dialog(monkeypatch):
    """筛选状态未知（None）仍做门控检查（仅 False 才跳过）。"""
    monkeypatch.setattr(wubei, "_claim_task_filter_on", None)
    monkeypatch.setattr(wubei.game_map, "match_frame_np", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_near_walk_target_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "_near_xiangmoshiwei_spot", lambda pos: False)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: 0.99)
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: ([], None))
    monkeypatch.setattr(wubei, "_find_claim_option",
                        lambda items, img_np: (1, 2, "选项", False))
    img = np.zeros((831, 1378, 3), np.uint8)
    desc = wubei._relocate_arrival(None, img, img, ("宝象国", 60, 60))
    assert desc and "自动弹出" in desc


# ---- 领取前后对话框清障与同文案任务受理（2026-11-22 实机 18:38 用户实报）----

def _claim_guard_env(monkeypatch, dialog_scores, claim_option=None,
                     green_rows=False):
    """装配 _claim_start_dialog_guard：对话框模板按序列返回、记录点击。"""
    seq = list(dialog_scores)

    def fake_score(img):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(wubei, "dialog_frame_score", fake_score)
    region_items = ([("未知选项", (500, 400, 900, 430), 0.9)]
                    if green_rows else [])
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: (list(region_items),
                                             np.zeros((831, 1378, 3), np.uint8)))
    monkeypatch.setattr(wubei, "_find_claim_option",
                        lambda items, img_np: claim_option)
    monkeypatch.setattr(wubei, "green_option_row_present",
                        lambda img_np, items: green_rows)
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda w: None)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("宝象国", 90, 84))
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    return clicks


def test_claim_start_guard_closes_leftover_closeout_dialog(monkeypatch):
    """领取前检测到无选项滞留对话框：点屏关闭并复检；关闭后继续入口流程。"""
    clicks = _claim_guard_env(monkeypatch, [0.99, 0.99, 0.2])
    img = np.zeros((831, 1378, 3), np.uint8)
    seen, had = wubei._claim_start_dialog_guard(_FakeWin(), img)
    assert seen is None and had is True
    assert len(clicks) == 2  # 首次点击未关（复检仍在场）→ 再点一次关闭


def test_claim_start_guard_reuses_leftover_claim_dialog(monkeypatch):
    """领取前检测到遗留领取对话框（含领取选项）：复用该帧直接点选项，不点屏。"""
    option = (534, 399, "除魔卫国，义不容辞", False, True)
    clicks = _claim_guard_env(monkeypatch, [0.99], claim_option=option)
    img = np.zeros((831, 1378, 3), np.uint8)
    seen, had = wubei._claim_start_dialog_guard(_FakeWin(), img)
    assert seen is not None and had is True
    assert clicks == []  # 不点屏幕中间，交调用方点领取选项


def test_claim_start_guard_does_not_click_unknown_green_options(monkeypatch):
    """无领取选项但含未知绿字选项的对话框：不点屏（防误选未知选项）。"""
    clicks = _claim_guard_env(monkeypatch, [0.99], green_rows=True)
    img = np.zeros((831, 1378, 3), np.uint8)
    seen, had = wubei._claim_start_dialog_guard(_FakeWin(), img)
    assert seen is None and had is True
    assert clicks == []


def test_dismiss_dialog_click_until_gone_retries_swallowed_click(monkeypatch):
    """点屏取消点击被过渡帧吞掉：复检仍在场则再点，消失即停。"""
    scores = [0.99, 0.2]
    monkeypatch.setattr(wubei, "dialog_frame_score",
                        lambda img: scores.pop(0))
    clicks = []
    monkeypatch.setattr(wubei, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(wubei, "move_away", lambda w: None)
    monkeypatch.setattr(wubei.time, "sleep", lambda s: None)
    assert wubei._dismiss_dialog_click_until_gone(_FakeWin()) == (2, True)
    assert len(clicks) == 2


def _claim_task_same_task_env(monkeypatch, *, option_still, dialog_score=0.99,
                              pos=("宝象国", 94, 84)):
    """装配 _claim_task 替换领取校验循环：入口成功、选项已点、签名不变。"""
    clock = _FakeClock()
    monkeypatch.setattr(wubei, "time", clock)
    monkeypatch.setattr(wubei, "is_in_battle", lambda w: False)
    monkeypatch.setattr(wubei, "is_in_battle_frame", lambda img: False)
    # 同文案替换领取场景：追踪面板在场（pre_sig 非空才走同文案受理分支）
    monkeypatch.setattr(wubei.task_tracker, "panel_visible",
                        lambda **k: True)
    monkeypatch.setattr(wubei.task_tracker, "ocr_track", lambda img: ([], None))
    monkeypatch.setattr(wubei.task_tracker, "task_signature_in",
                        lambda items, title: "sig")
    monkeypatch.setattr(wubei, "_claim_start_dialog_guard",
                        lambda w, img: (None, False))
    monkeypatch.setattr(wubei, "_arrive_by_walk", False)
    monkeypatch.setattr(wubei, "CLAIM_VERIFY_WAIT_STALE", 2.0)
    monkeypatch.setattr(wubei, "CLAIM_DIALOG_TIMEOUT", 0)
    monkeypatch.setattr(wubei, "log", lambda *a, **k: None)
    monkeypatch.setattr(wubei, "_click_entry_ensuring_dialog",
                        lambda w, **k: (True, "x", "src", (1, 1), ([], None)))
    monkeypatch.setattr(wubei, "_click_claim_option",
                        lambda w, items, img_np: True)
    monkeypatch.setattr(wubei, "_claim_verify_wait",
                        lambda *a, **k: clock.sleep(1.0))

    class _Gate:
        def __init__(self, min_interval=0.0):
            pass

        def ocr(self, img, skip_when_absent=False):
            return []

        def paste_track(self, img, img_np):
            pass

    monkeypatch.setattr(wubei.task_tracker, "TrackGate", _Gate)
    monkeypatch.setattr(wubei.game_map, "ocr_region",
                        lambda img, region: ([], np.zeros((831, 1378, 3), np.uint8)))
    monkeypatch.setattr(wubei.task_tracker, "has_new_task_in",
                        lambda *a, **k: False)
    monkeypatch.setattr(wubei.task_tracker, "has_task_in",
                        lambda *a, **k: True)
    monkeypatch.setattr(wubei, "_find_claim_option",
                        lambda items, img_np:
                        (534, 399, "除魔卫国，义不容辞", False, True)
                        if option_still else None)
    monkeypatch.setattr(wubei, "dialog_frame_score", lambda img: dialog_score)
    monkeypatch.setattr(wubei, "_last_replace_at", 0.0)
    monkeypatch.setattr(wubei, "_replace_fail_streak", 0)
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: pos)
    monkeypatch.setattr(wubei.task_tracker, "read_tasks", lambda w: [])
    relocated = []
    monkeypatch.setattr(wubei, "_relocate_to_claim_spot",
                        lambda w: relocated.append(1) or (False, "x"))
    return relocated


def test_claim_task_same_task_confirmed_by_closeout_dialog(monkeypatch):
    """替换领取：选项已消失 + 收尾对话框在场 → 同文案任务按领取已受理。

    锁妖网等任务文案固定，重领后签名不变——旧校验空耗 20s 后误判失败
    （2026-11-22 实机连续 3 轮终止五倍）。直接信号为「领取选项消失 +
    收尾对话框在场」。"""
    _claim_task_same_task_env(monkeypatch, option_still=False)
    ok, info, frame = wubei._claim_task(_FakeWin(), allow_existing=True)
    assert ok is True and info == "五倍任务已领取"
    assert wubei._claim_same_task_ack is True
    assert wubei._claim_confirm_dlg_score == 0.99  # 同帧取消收尾对话框
    assert frame is not None


def test_claim_task_same_task_not_confirmed_while_option_still(monkeypatch):
    """领取选项仍在（点击未生效）：不得用收尾对话框信号误确认。"""
    relocated = _claim_task_same_task_env(
        monkeypatch, option_still=True, pos=("宝象国", 60, 60))
    ok, info, frame = wubei._claim_task(_FakeWin(), allow_existing=True)
    assert ok is False and frame is None
    assert wubei._claim_same_task_ack is False
    assert relocated == [1]  # 真正失败仍走重走兜底


def test_claim_task_skips_relocate_when_already_at_claim_coord(monkeypatch):
    """校验失败但人已在领取点坐标（目标 ±5）：跳过重走领取点（开图/寻路白耗）。"""
    relocated = _claim_task_same_task_env(
        monkeypatch, option_still=False, dialog_score=0.3)
    ok, info, frame = wubei._claim_task(_FakeWin())
    assert ok is False and frame is None
    assert relocated == []  # 已在 94,84（领取点坐标）：不再开小地图重走


def test_claim_start_guard_no_click_when_not_at_claim_spot(monkeypatch):
    """位置可读且不在领取点附近：人物可能仍在寻路，不点屏（防取消寻路）。"""
    clicks = _claim_guard_env(monkeypatch, [0.99])
    monkeypatch.setattr(wubei.game_map, "read_map_position",
                        lambda w, img=None: ("宝象国", 60, 60))
    img = np.zeros((831, 1378, 3), np.uint8)
    seen, had = wubei._claim_start_dialog_guard(_FakeWin(), img)
    assert seen is None and had is True
    assert clicks == []
