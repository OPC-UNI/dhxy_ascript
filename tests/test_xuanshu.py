"""xuanshu.py：任务签名相似判定（follow 轮询防抖核心）、
任务消失弹窗守卫（2026-10-31 客栈奇遇弹窗遗留教训）与
钓鱼小游戏接入（2026-11-10，复用客栈破除封印闭环）。"""
import time
from pathlib import Path

from PIL import Image

from src import xuanshu


def test_signatures_similar_identical():
    assert xuanshu._signatures_similar("常规玩法|前往龙窟五层消灭修罗",
                                       "常规玩法|前往龙窟五层消灭修罗")


def test_signatures_similar_ocr_jitter():
    """1~2 字符 OCR 抖动（标点/单字误读）视为同一状态。"""
    a = "常规玩法|前往龙窟五层消灭修罗"
    b = "常规玩法|前往龙窟五层消灭修罹"  # 单字误读
    assert xuanshu._signatures_similar(a, b)


def test_signatures_similar_different():
    """目标文案整体不同（仅共享标题前缀）→ 视为任务状态变化。"""
    a = "常规玩法|前往龙窟五层消灭修罗"
    b = "常规玩法|任务已完成返回领取奖励"
    assert not xuanshu._signatures_similar(a, b)


class _GuardWin:
    def __init__(self, img=Image.new("RGB", (60, 60))):
        self.img = img

    def capture(self, force_activate=False):
        return self.img


def test_task_gone_dialog_guard_no_handler():
    assert xuanshu._task_gone_dialog_guard(_GuardWin(), None) is False


def test_task_gone_dialog_guard_battle_frame_skips(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: True)
    ocr_calls = []
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda *a, **k: ocr_calls.append(1) or ([], None))
    assert xuanshu._task_gone_dialog_guard(_GuardWin(), lambda *a: True) \
        is False
    assert ocr_calls == []


def test_task_gone_dialog_guard_handled(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([("我知道了", (650, 500, 720, 530), 1.0)], None))
    assert xuanshu._task_gone_dialog_guard(
        _GuardWin(), lambda win, items, img_np: True) is True


def test_task_gone_dialog_guard_not_handled(monkeypatch):
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([], None))
    assert xuanshu._task_gone_dialog_guard(
        _GuardWin(), lambda win, items, img_np: False) is False


def test_task_gone_dialog_guard_extras_raise_swallowed(monkeypatch):
    # 业务异常吞掉放行（与 _confirm_guard_dialog 同约定）
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu.game_map, "ocr_region",
                        lambda img, region: ([("x", (400, 300, 500, 320), 1.0)], None))

    def _raise(*a, **k):
        raise RuntimeError("business")

    assert xuanshu._task_gone_dialog_guard(_GuardWin(), _raise) is False


# ---- 红色杂气团块检测（打包版用户反馈「杂气多时点不完」的修复：超面积团块拆分） ----
def _qi_region(w=900, h=585):
    """QI_REGION 尺寸的黑色 RGB 帧（BGR 全 0）。"""
    import numpy as np
    return np.zeros((h, w, 3), np.uint8)


def _red_dot(f, cx, cy, r):
    """在区域帧上画红色实心圆（RGB 帧 → R=220,G=30,B=30）。"""
    import cv2
    cv2.circle(f, (int(cx), int(cy)), int(r), (220, 30, 30), -1)


def test_find_red_qi_merged_clump_split():
    """3 个杂气重叠连通成超面积团块 → 拆分成多个点击目标（打包版用户
    反馈「杂气多时点不完」的根因场景：合并团块被面积上限整体过滤）。"""
    f = _qi_region()
    for cx, cy in ((300, 200), (330, 215), (360, 200)):  # 相邻 34px，r18 必连通
        _red_dot(f, cx, cy, 18)
    clusters, oversized = xuanshu._find_red_qi_clusters(f)
    assert oversized == 1  # 三个圆连成一个超面积团块
    assert len(clusters) >= 3
    assert all(c[7] for c in clusters)  # 全部为拆分目标
    # 各峰落在三个圆心附近（±6px，返回窗口相对坐标，先回换算区域坐标）
    centers = sorted((c[0] - xuanshu.QI_REGION[0], c[1] - xuanshu.QI_REGION[1])
                     for c in clusters)
    assert centers[0][0] < 310 and centers[0][0] > 290
    assert centers[-1][0] > 350 and centers[-1][0] < 370


def test_find_red_qi_single_clusters_unchanged():
    """分散的单个杂气（面积区间内）→ 保持原质心单目标行为。"""
    f = _qi_region()
    _red_dot(f, 200, 150, 15)
    _red_dot(f, 500, 300, 15)
    clusters, oversized = xuanshu._find_red_qi_clusters(f)
    assert oversized == 0
    assert len(clusters) == 2
    assert all(not c[7] for c in clusters)


def test_find_red_qi_oversized_single_peak_clicked():
    """单个超大杂气（面积>2500 但≤10000）→ 单峰仍点击（大号单杂气）。"""
    f = _qi_region()
    _red_dot(f, 400, 300, 32)  # 面积 π*32²≈3217 > 2500
    clusters, oversized = xuanshu._find_red_qi_clusters(f)
    assert oversized == 1
    assert len(clusters) == 1
    assert clusters[0][7]
    assert abs(clusters[0][0] - xuanshu.QI_REGION[0] - 400) <= 3
    assert abs(clusters[0][1] - xuanshu.QI_REGION[1] - 300) <= 3


def test_find_red_qi_giant_blob_skipped():
    """超大面积单峰（>QI_SPLIT_MAX_AREA）→ 视为大块静态红物不点击。"""
    f = _qi_region()
    _red_dot(f, 400, 300, 70)  # 面积 π*70²≈15394 > 10000
    clusters, oversized = xuanshu._find_red_qi_clusters(f)
    assert oversized == 1
    assert clusters == []


def test_find_red_qi_noise_filtered():
    """面积低于 QI_AREA_MIN 的小红点 → 过滤（红色文字等噪点）。"""
    f = _qi_region()
    _red_dot(f, 100, 100, 5)  # 面积 ≈78 < 250
    clusters, oversized = xuanshu._find_red_qi_clusters(f)
    assert clusters == [] and oversized == 0


def test_observe_click_effects_static_object_confirmed_then_blacklisted():
    """静态红物：点击后窗口红像素不变 → 确认无效；同位置第二次点击拉黑。"""
    f = _qi_region()
    _red_dot(f, 400, 300, 15)
    pending = {(xuanshu.QI_REGION[0] + 400, xuanshu.QI_REGION[1] + 300):
               (700, 0.0, 1, False)}  # 点击时窗口红像素数 700
    xuanshu._observe_click_effects(pending, f, now=xuanshu.QI_NOEFFECT_DELAY + 0.1)
    assert pending[(xuanshu.QI_REGION[0] + 400, xuanshu.QI_REGION[1] + 300)][3] is True


def test_observe_click_effects_effective_click_cleared():
    """有效点击：杂气被消除后窗口红像素明显减少 → 观察记录清除。"""
    f = _qi_region()  # 全黑：点击后杂气已消失
    pos = (xuanshu.QI_REGION[0] + 400, xuanshu.QI_REGION[1] + 300)
    pending = {pos: (700, 0.0, 1, False)}
    xuanshu._observe_click_effects(pending, f, now=xuanshu.QI_NOEFFECT_DELAY + 0.1)
    assert pos not in pending


# ---- 锄遗聚宝批量极速连点（杂气走到石头即消失：吞吐优先） ----
def _fake_find_template_always_present():
    """start 检查（第 1 次调用）返回 None（无开始按钮），主图检查恒在。"""
    n = {"n": 0}
    def _find(*a, **k):
        n["n"] += 1
        return None if n["n"] == 1 else (1, 1)
    return _find


def test_play_chuyijuebao_batch_clicks_all_targets(monkeypatch):
    """同帧 3 个独立杂气 → 批量极速连点一轮点完全部（rapid_click 3 次）。"""
    import numpy as np
    clicks = []
    f = _qi_region()
    for cx, cy in ((300, 200), (500, 200), (700, 200)):
        _red_dot(f, cx, cy, 15)
    monkeypatch.setattr(xuanshu, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(xuanshu.clear_screen, "ensure_activity_closed",
                        lambda win: (True, "测试：跳过活动侧栏收起"))
    monkeypatch.setattr(xuanshu.game_map, "find_template",
                        _fake_find_template_always_present())
    monkeypatch.setattr(xuanshu, "_capture_qi_region", lambda win: f)
    monkeypatch.setattr(xuanshu, "rapid_click",
                        lambda win, x, y, press=0.02: clicks.append((x, y, press)))
    monkeypatch.setattr(xuanshu, "precise_click",
                        lambda *a, **k: clicks.append("precise"))
    ok, info = xuanshu._play_chuyijuebao(object(), timeout=2.0)
    assert not ok  # 主图恒在 → 超时返回
    rapid = [c for c in clicks if isinstance(c, tuple)]
    assert len(rapid) == 3  # 一轮批量点完 3 个目标，无 precise 路径
    assert "precise" not in clicks


def test_play_chuyijuebao_single_target_uses_precise(monkeypatch):
    """同帧仅 1 个杂气 → 保持单目标 precise_click 路径（逐击重截窗）。"""
    clicks = []
    f = _qi_region()
    _red_dot(f, 400, 300, 15)
    monkeypatch.setattr(xuanshu, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(xuanshu.clear_screen, "ensure_activity_closed",
                        lambda win: (True, "测试：跳过活动侧栏收起"))
    monkeypatch.setattr(xuanshu.game_map, "find_template",
                        _fake_find_template_always_present())
    monkeypatch.setattr(xuanshu, "_capture_qi_region", lambda win: f)
    monkeypatch.setattr(xuanshu, "precise_click",
                        lambda *a, **k: clicks.append("precise"))
    monkeypatch.setattr(xuanshu, "rapid_click",
                        lambda *a, **k: clicks.append("rapid"))
    ok, info = xuanshu._play_chuyijuebao(object(), timeout=1.5)
    assert not ok
    assert clicks == ["precise"]  # 仅 1 击（后续轮次被 2s 防抖拦住）


# ---- 钓鱼小游戏（2026-11-10：与客栈破除封印同款窗口，仅标题模板不同）----
def test_diaoyu_template_exists():
    """标题/长按按钮模板必须真实存在于 res/img（禁止引用不存在的模板）。"""
    base = Path(__file__).resolve().parent.parent / "res/img"
    assert (base / xuanshu.DIAOYU_TITLE_ICON).is_file()
    assert (base / xuanshu.DIAOYU_HOLD_ICON).is_file()


def test_diaoyu_arm_only_entry_option():
    """只有入口选项「先钓一下再说！」被点击才开启窗口检测。"""
    xuanshu._diaoyu_armed_at = 0.0
    xuanshu._arm_diaoyu_check("别的选项")
    assert xuanshu._diaoyu_armed_at == 0.0
    xuanshu._arm_diaoyu_check(xuanshu.DIAOYU_ENTRY_OPTION)
    assert xuanshu._diaoyu_armed_at > 0.0


def test_diaoyu_window_check_unarmed_no_match(monkeypatch):
    """未 armed：不跑模板匹配（热路径零成本）。"""
    calls = []
    monkeypatch.setattr(xuanshu.game_map, "best_match_np",
                        lambda *a, **k: calls.append(1) or (0.0, None))
    xuanshu._diaoyu_armed_at = 0.0
    img = Image.new("RGB", (1378, 831))
    assert xuanshu._diaoyu_window_check(object(), img) is False
    assert calls == []


def test_diaoyu_window_check_detects_and_plays(monkeypatch):
    """armed 且标题命中 → 以钓鱼标题/标签调用共享闭环，成功后关闭检测。"""
    import numpy as np
    xuanshu._diaoyu_armed_at = time.time()
    xuanshu._diaoyu_last_check = 0.0
    seen = {}

    monkeypatch.setattr(xuanshu.game_map, "best_match_np",
                        lambda bgr, icon, region=None: (0.99, (600, 500)))

    def _play(win, icon, prefix="客栈", label="破除封印",
              hold=False, action_icon=None, hold_catch=False):
        seen.update(icon=icon, prefix=prefix, label=label, hold=hold,
                    action_icon=action_icon, hold_catch=hold_catch,
                    region=xuanshu._pochu_minigame.POCHU_REGION)
        return True, "钓鱼结束"

    monkeypatch.setattr(xuanshu._pochu_minigame, "play_pochufengyin", _play)
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert xuanshu._diaoyu_window_check(object(), img, prefix="玄枢轶闻") is True
    assert seen["icon"] == "xuanshu_diaoyu.png"
    assert seen["prefix"] == "玄枢轶闻"
    assert seen["label"] == "钓鱼"
    # 长按模式 + 长按按钮模板（钓鱼与破除封印的唯一推进方式差异）
    assert seen["hold"] is True
    assert seen["action_icon"] == "xuanshu_diaoyu_changan.png"
    # 长按运输 + 短段长按维持：进带不锁定，长按推过带上缘→松手回落→
    # 回 C 后反复短按保持在 C 附近（2026-11-12 用户技巧）
    assert seen["hold_catch"] is True
    # 区域与客栈破除封印完全一致（用户要求）
    assert seen["region"] == (314, 458, 1058, 645)
    assert xuanshu._diaoyu_armed_at == 0.0  # 成功后关闭检测


def test_diaoyu_window_check_keeps_armed_on_failure(monkeypatch):
    """闭环失败（如战斗打断）保持 armed，窗口仍在时下轮询重试。"""
    import numpy as np
    xuanshu._diaoyu_armed_at = time.time()
    xuanshu._diaoyu_last_check = 0.0
    monkeypatch.setattr(xuanshu.game_map, "best_match_np",
                        lambda bgr, icon, region=None: (0.99, (600, 500)))
    monkeypatch.setattr(xuanshu._pochu_minigame, "play_pochufengyin",
                        lambda *a, **k: (False, "战斗开始，钓鱼中断"))
    img = np.zeros((831, 1378, 3), dtype=np.uint8)
    assert xuanshu._diaoyu_window_check(object(), img) is True
    assert xuanshu._diaoyu_armed_at > 0.0


# ---- 领取链路危险选项过滤（2026-11-10 玄枢轶闻实测） ----

def test_click_green_option_filters_abandon(monkeypatch):
    """放弃类选项永不参与候选。

    「放弃本次玄枢轶闻任务」与「领取玄枢轶闻」「玄枢轶闻」互为子串：
    搜「领取玄枢轶闻」时 0.8 模糊匹配命中「玄枢轶闻」与放弃行，搜
    「玄枢轶闻」时子串直配命中放弃行（2026-11-10 日志实测 3 次误点，
    放弃会丢掉已领取任务）。包装层先剔除放弃行再交给共享实现。
    """
    seen = {}
    monkeypatch.setattr(
        xuanshu, "_dc_click_green_option_once",
        lambda win, items, img_np, text, prefix: seen.update(
            items=items, text=text, prefix=prefix) or True)
    items = [("放弃本次玄枢轶闻任务", [500, 450, 640, 470], 0.99),
             ("玄枢轶闻", [450, 420, 530, 440], 0.99)]
    assert xuanshu._click_green_option_once(None, items, None, "领取玄枢轶闻")
    assert [t for t, _, _ in seen["items"]] == ["玄枢轶闻"]
    assert seen["text"] == "领取玄枢轶闻"
    assert seen["prefix"] == "玄枢轶闻"


# ---- 任务消失确认：面板缺席即任务结束（用户 2026-11-12 规则） ----
class _GoneClock:
    """可控时钟：sleep 推进虚拟时间，time 返回虚拟时间。"""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    def sleep(self, dt):
        self.t += dt


def _patch_gone_env(monkeypatch, region, occluded=False):
    """装配 _confirm_task_gone 依赖。

    region=None 模拟标题条未命中；occluded 为遮挡负条件桩结果。
    """
    clock = _GoneClock()
    ocr_calls = []
    logs = []

    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(xuanshu, "_task_gone_region", lambda img_np: region)
    monkeypatch.setattr(xuanshu, "_task_gone_occluded",
                        lambda img, bgr=None: occluded)
    monkeypatch.setattr(xuanshu.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    monkeypatch.setattr(xuanshu.game_map, "ocr_regions",
                        lambda img, regions: ocr_calls.append(1) or ([], None))
    monkeypatch.setattr(xuanshu, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))
    return ocr_calls, logs


def test_confirm_task_gone_panel_missing_is_done(monkeypatch):
    """面板缺席即判任务结束（用户 2026-11-12 规则）。

    「任务追踪消失=任务结束」：游戏在无追踪任务时把面板自动收起（只剩
    左缘小箭头）——玄枢/五环/名望/客栈均如此；修罗/五倍的任务完成由
    战斗驱动（战斗结束路径判定），不走这里。
    """
    ocr_calls, logs = _patch_gone_env(monkeypatch, region=None)
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法") is True
    assert ocr_calls  # 回退静态条带采集证据
    assert any("追踪面板不在场，按任务消失判定" in m for m, _ in logs)


def test_confirm_task_gone_panel_missing_occluded_not_done(monkeypatch):
    """面板缺席但其它大面板在场（背包/地图/队伍/对话框）→ 不判完成。

    这些面板打开时会盖住追踪面板，标题条同样未命中——不排除遮挡会把
    「被盖住」误判成任务完成。证据帧照常 OCR，遮挡只在判定帧检查。
    """
    ocr_calls, logs = _patch_gone_env(monkeypatch, region=None, occluded=True)
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法") is False
    assert ocr_calls  # 证据帧照常 OCR
    assert any("被遮挡" in m for m, _ in logs)


def test_confirm_task_gone_absent_during_battle_gap_not_done(monkeypatch):
    """战斗线程仍在战斗（动画间隙帧）+面板缺席 → 不采集证据、不判完成。

    战斗 UI 会遮住追踪面板，面板缺席不能当作消失证据（任务仍在战斗
    途中）。
    """
    ocr_calls, logs = _patch_gone_env(monkeypatch, region=None)
    monkeypatch.setattr(xuanshu, "battle_watch_in_battle", lambda: True)
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法") is False
    assert ocr_calls == []
    assert any("窗口耗尽但战斗未结束" in m for m, _ in logs)


def test_confirm_task_gone_panel_visible_no_task_is_done(monkeypatch):
    """面板在场且连续轮询无任务 → 正常判定任务消失（完成）。"""
    ocr_calls, _logs = _patch_gone_env(monkeypatch, region=(0, 210, 400, 470))
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法") is True
    assert len(ocr_calls) >= xuanshu.TASK_GONE_CONFIRM_POLLS


def test_confirm_task_gone_post_battle_panel_missing_is_done(monkeypatch):
    """战斗结束路径：面板缺席即判任务消失、跳过遮挡检查（返程快路径）。

    用户 2026-11-11 确认：战斗结束后任务追踪会消失、随即打开背包返程
    ——返程快路径不查遮挡（对话由 _task_gone_dialog_guard 兜底），
    省 ~0.55s。
    """
    ocr_calls, logs = _patch_gone_env(monkeypatch, region=None, occluded=True)
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法",
                                      check_occluders=False) is True
    assert ocr_calls  # 回退静态条带采集证据（原逻辑）
    assert any("追踪面板不在场，按任务消失判定" in m for m, _ in logs)


# ---- follow 战斗结束转换：动画间隙不得消费 was_battle（2026-11-11） ----

def test_follow_battle_gap_keeps_battle_end_fastpath(monkeypatch):
    """战斗线程确认在战斗中的动画间隙帧（操作条/回合横幅单帧漏匹配）
    必须仍按战斗处理：不消费 was_battle、不进任务消失确认；线程确认
    结束后的首个非战斗帧走 check_occluders=False 快路径。

    回归 2026-11-11 13:49/13:52：间隙帧消费 was_battle 后，真战斗结束
    退回严格路径空等 6s 并「不判定消失」，返程慢 10s（黄袍怪那次连烧
    3 个 6s 窗口）。
    """
    clock = _GoneClock()
    battle_states = [False,              # 外层循环战斗门
                     True, True, False]  # 内层：间隙 ×2（线程仍在战斗）→ 结束
    frames = [True, False, False, False]  # 战斗帧 → 间隙帧 ×2 → 结束帧
    confirms, confirm_at = [], []

    class _Win:
        def capture(self, force_activate=False):
            return object()

    task = {"title": "常规玩法", "lines": [("前往龙窟五层消灭修罗", None)],
            "text": "常规玩法"}
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu, "log", lambda *a, **k: None)
    monkeypatch.setattr(xuanshu, "battle_watch_in_battle",
                        lambda: battle_states.pop(0) if battle_states else False)
    monkeypatch.setattr(xuanshu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xuanshu, "is_in_battle_frame",
                        lambda img: frames.pop(0) if frames else False)

    def _confirm(win, title, fb=(), first_img=None, polls=2,
                 check_occluders=True):
        confirms.append(check_occluders)
        confirm_at.append(clock.t)
        return True

    monkeypatch.setattr(xuanshu, "_confirm_task_gone", _confirm)
    monkeypatch.setattr(xuanshu, "_task_gone_dialog_guard",
                        lambda *a, **k: False)
    monkeypatch.setattr(xuanshu, "_maybe_sheyaoxiang", lambda win: None)
    monkeypatch.setattr(xuanshu.app_utils, "mark_track_walk", lambda: None)
    monkeypatch.setattr(xuanshu.debug_overlay, "set_phase", lambda *a: None)
    monkeypatch.setattr(xuanshu.tab_group, "pass_active", False)
    monkeypatch.setattr(xuanshu.tab_group, "active", lambda: False)
    monkeypatch.setattr(xuanshu.task_tracker, "ocr_follow",
                        lambda img: ([("常规玩法", [10, 240, 180, 258], 0.99)],
                                     None))
    monkeypatch.setattr(xuanshu.task_tracker, "group_tasks",
                        lambda items, xmax=0: [])
    monkeypatch.setattr(xuanshu.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    monkeypatch.setattr(xuanshu.task_tracker, "find_task",
                        lambda tasks, title: task if not confirms else None)
    monkeypatch.setattr(xuanshu.task_tracker, "task_signature",
                        lambda t: "常规玩法|前往龙窟五层消灭修罗")
    monkeypatch.setattr(xuanshu.task_tracker, "track_task",
                        lambda win, title, **k: (True, "已点击"))

    ok, _info = xuanshu.follow_task(_Win(), "常规玩法", timeout=30)
    assert ok is True
    # 间隙帧（线程确认仍在战斗）不得触发确认、不得消费 was_battle：
    # 确认只发生在战斗线程确认结束后的第 4 拍（t=2.0），且走快路径
    # （check_occluders=False）
    assert confirms == [False]
    assert confirm_at == [2.0]


# ---- 任务消失确认：面板缺席即任务结束（用户 2026-11-12 规则） ----
def _patch_follow_minimal(monkeypatch, confirms):
    """装配 follow_task 最小环境：任务从未出现在面板，首个确认即返回。"""
    class _Win:
        def capture(self, force_activate=False):
            return object()

    def _confirm(win, title, fb=(), first_img=None, polls=2, **kwargs):
        confirms.append(kwargs)
        return True

    monkeypatch.setattr(xuanshu, "log", lambda *a, **k: None)
    monkeypatch.setattr(xuanshu, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(xuanshu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu, "tab_group", type(
        "T", (), {"pass_active": False, "active": staticmethod(lambda: False)}))
    monkeypatch.setattr(xuanshu.task_tracker, "ocr_follow",
                        lambda img: ([], None))
    monkeypatch.setattr(xuanshu.task_tracker, "group_tasks",
                        lambda items, xmax=0: [])
    monkeypatch.setattr(xuanshu.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    monkeypatch.setattr(xuanshu.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(xuanshu, "_confirm_task_gone", _confirm)
    monkeypatch.setattr(xuanshu, "_task_gone_dialog_guard",
                        lambda *a, **k: False)
    return _Win()


def test_follow_task_confirm_no_per_task_flags(monkeypatch):
    """面板缺席判定不带任何 per-task 参数（用户 2026-11-12 全局规则）。"""
    confirms = []
    win = _patch_follow_minimal(monkeypatch, confirms)
    ok, _info = xuanshu.follow_task(win, "常规玩法", timeout=30)
    assert ok is False  # 任务从未出现：假完成防护生效，回主循环重试
    assert "require_standing" not in confirms[0]
    assert "task_gone_require_standing" not in confirms[0]


# ---- follow 点击尝试失败但任务已完成：按完成返回（2026-11-13） ----
def _patch_follow_track_fail(monkeypatch, gone_ok):
    """装配 follow_task：任务在面板 → track_task 失败（完成过渡帧标题/
    内容仍在、绿字链接已消失，点不到绿字）→ _confirm_task_gone 结论由
    gone_ok 决定。"""
    class _Win:
        def capture(self, force_activate=False):
            return object()

    task = {"title": "常规玩法", "lines": [("前往大理寺墙外附近寻", None)],
            "text": "常规玩法"}
    calls = {"confirm": 0}
    monkeypatch.setattr(xuanshu, "log", lambda *a, **k: None)
    monkeypatch.setattr(xuanshu, "battle_watch_in_battle", lambda: False)
    monkeypatch.setattr(xuanshu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xuanshu, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(xuanshu, "tab_group", type(
        "T", (), {"pass_active": False, "active": staticmethod(lambda: False)}))
    monkeypatch.setattr(xuanshu.task_tracker, "ocr_follow",
                        lambda img: ([("常规玩法[每日]", [10, 240, 180, 258],
                                       0.99)], None))
    monkeypatch.setattr(xuanshu.task_tracker, "group_tasks",
                        lambda items, xmax=0: items)
    monkeypatch.setattr(xuanshu.task_tracker, "panel_row_xmax",
                        lambda img_np: 400)
    monkeypatch.setattr(xuanshu.task_tracker, "find_task",
                        lambda tasks, title: task)
    monkeypatch.setattr(xuanshu.task_tracker, "task_signature",
                        lambda t: "常规玩法|前往大理寺墙外附近寻")
    monkeypatch.setattr(xuanshu.task_tracker, "track_task",
                        lambda win, title, **k:
                        (False, "任务追踪中未找到「常规玩法」"))

    def _confirm(*a, **k):
        calls["confirm"] += 1
        return gone_ok

    monkeypatch.setattr(xuanshu, "_confirm_task_gone", _confirm)
    monkeypatch.setattr(xuanshu, "_task_gone_dialog_guard",
                        lambda *a, **k: False)
    return _Win(), calls


def test_follow_task_track_fail_after_completion_is_done(monkeypatch):
    """track_task 点不到绿字但任务消失确认命中 → 按完成返回。

    回归 2026-11-13 客栈第 20 次完成记漏：进度变化确认落在完成瞬间
    （确认前对话框/extras 持续复位 changed_once），确认后补的那次
    track_task 点不到绿字返回失败，整轮 fail 使完成次数少记 1。
    """
    win, calls = _patch_follow_track_fail(monkeypatch, gone_ok=True)
    ok, info = xuanshu.follow_task(win, "常规玩法", timeout=30)
    assert ok is True
    assert "任务已从任务追踪中消失" in info
    assert calls["confirm"] == 1


def test_follow_task_track_fail_task_present_is_fail(monkeypatch):
    """track_task 失败且任务消失确认未通过（任务仍在/未决）：保持失败。"""
    win, calls = _patch_follow_track_fail(monkeypatch, gone_ok=False)
    ok, info = xuanshu.follow_task(win, "常规玩法", timeout=30)
    assert ok is False
    assert info == "任务追踪中未找到「常规玩法」"
    assert calls["confirm"] == 1


# ---- 玄枢轶闻：全部任务共用一条面板缺席规则（用户 2026-11-12） ----
def test_run_cycle_uses_shared_confirm_rule(monkeypatch):
    """玄枢轶闻两条 follow 路径都不传 per-task 面板参数。

    面板缺席判定统一在 _confirm_task_gone（任务追踪消失=任务结束）：
    续跑跟踪与领取后跟踪两个调用点行为一致。
    """
    seen = []
    monkeypatch.setattr(xuanshu, "is_in_battle", lambda win: False)
    monkeypatch.setattr(xuanshu.debug_overlay, "set_phase", lambda *a: None)

    def _fake_follow(win, title=xuanshu.TASK_TITLE, **kwargs):
        seen.append(kwargs)
        return True, "任务已从任务追踪中消失"

    monkeypatch.setattr(xuanshu, "follow_task", _fake_follow)

    # 路径 1：任务已在追踪面板，直接续跑跟踪
    monkeypatch.setattr(xuanshu.task_tracker, "has_task",
                        lambda win, title: True)
    status, _info = xuanshu.run_cycle(object())
    assert status == "done"
    assert "task_gone_require_panel" not in seen[0]

    # 路径 2：无任务 → 领取后紧接跟踪
    monkeypatch.setattr(xuanshu.task_tracker, "has_task",
                        lambda win, title: False)
    monkeypatch.setattr(xuanshu, "navigate_to_task",
                        lambda win: (True, "已领取玄枢轶闻任务", ([], None)))
    status, _info = xuanshu.run_cycle(object())
    assert status == "done"
    assert "task_gone_require_panel" not in seen[1]


# ---- 面板缺席时的遮挡负条件（用户 2026-11-12 规则配套） ----
def test_task_gone_occluded_dialog_not_occluder(monkeypatch):
    """标准对话框不算遮挡（用户 2026-11-12 确认：对话框不挡追踪面板）。

    对话框在场时面板照样可读，面板缺席即任务结束——把对话框当遮挡会
    让已完成任务卡在消失确认循环里空转（15:03 实测空转到 300s 超时）。
    遮挡名单只剩背包/世界地图/小地图/组号队伍面板。
    """
    calls = []
    monkeypatch.setattr(xuanshu.game_map, "match_frame_np",
                        lambda img, icon, **k: calls.append(icon) or None)
    monkeypatch.setattr(xuanshu.tab_group, "active", lambda: False)
    assert xuanshu._task_gone_occluded(Image.new("RGB", (1378, 831))) is False
    assert len(calls) == 3  # 仅查背包/世界地图/小地图三个模板


def test_task_gone_occluded_no_occluder(monkeypatch):
    """无遮挡面板 → False（继续按面板缺席判完成）。"""
    monkeypatch.setattr(xuanshu.game_map, "match_frame_np",
                        lambda img, icon, **k: None)
    monkeypatch.setattr(xuanshu.tab_group, "active", lambda: False)
    frame = Image.new("RGB", (1378, 831))
    assert xuanshu._task_gone_occluded(frame) is False


def test_task_gone_occluded_bag_panel(monkeypatch):
    """背包面板标志模板命中 → 判遮挡。"""
    monkeypatch.setattr(xuanshu.game_map, "match_frame_np",
                        lambda img, icon, **k: (1, 1) if "bag_box" in icon
                        else None)
    assert xuanshu._task_gone_occluded(Image.new("RGB", (1378, 831))) is True


def test_task_gone_occluded_team_only_in_tab_group_mode(monkeypatch):
    """队伍面板检查只在组号模式跑（用户 2026-11-12：单人省 ~0.36s）。

    单人模式队伍面板不会在跟随期间残留前台，跳过含 OCR 兜底的队伍
    检查（遮挡检查 ~0.55s → ~0.19s）；组号模式保留（战斗线程做队伍
    操作时残留面板会盖住追踪面板）。
    """
    import src.team as team_mod
    calls = []
    monkeypatch.setattr(xuanshu.game_map, "match_frame_np",
                        lambda img, icon, **k: None)
    monkeypatch.setattr(team_mod, "panel_visible_in",
                        lambda img: calls.append(1) or True)
    frame = Image.new("RGB", (1378, 831))

    # 单人模式：不调用队伍面板检查
    monkeypatch.setattr(xuanshu.tab_group, "active", lambda: False)
    assert xuanshu._task_gone_occluded(frame) is False
    assert calls == []

    # 组号模式：调用队伍面板检查（命中即遮挡）
    monkeypatch.setattr(xuanshu.tab_group, "active", lambda: True)
    assert xuanshu._task_gone_occluded(frame) is True
    assert calls == [1]


def test_handle_dialogs_option_click(monkeypatch):
    """共享对话框处理链：DIALOG_OPTION_MAP 命中 → 点绿字选项即已处理。"""
    dtxt, opt = next(iter(xuanshu.DIALOG_OPTION_MAP.items()))
    clicks = []
    monkeypatch.setattr(xuanshu, "_click_green_option_once",
                        lambda win, items, img_np, o, prefix="": (
                            clicks.append(o) or True))
    items = [(dtxt, (500, 300, 600, 320), 0.99)]
    assert xuanshu._handle_dialogs(object(), items, None, None) is True
    assert clicks == [opt]


def test_follow_task_dialog_present_does_not_block_gone(monkeypatch):
    """面板缺席 + 标准对话框在场 → 直接判任务完成，不等对话框推进。

    用户 2026-11-12 确认对话框不挡追踪面板：修复前遮挡负条件把对话框
    当遮挡，任务完成后要等收尾剧情对话点完（多页时白等 10s+）甚至
    空转到 300s 超时。现在对话框不参与完成判定。
    """
    events = []
    confirms = []
    win = _patch_follow_minimal(monkeypatch, confirms)

    def _confirm(win, title, fb=(), first_img=None, polls=2, **kwargs):
        events.append("confirm")
        confirms.append(kwargs)
        return True

    monkeypatch.setattr(xuanshu, "_confirm_task_gone", _confirm)
    monkeypatch.setattr(xuanshu, "_handle_dialogs",
                        lambda *a, **k: events.append("dialog") or False)

    ok, _info = xuanshu.follow_task(win, "常规玩法", timeout=30)
    assert ok is False  # 任务从未出现：假完成防护（原语义）
    assert events == ["confirm"]  # 不因对话框推迟完成判定



def test_confirm_task_gone_off_leader_paused(monkeypatch):
    """屏幕不是队长 Tab（用户手动切到队员 Tab / pass 中途开始）：队员屏
    本来就没有任务追踪，面板缺席不构成完成证据——放弃本次确认，交回
    外层轮询（follow 的队长屏护栏会暂停，切回队长后重新确认）。"""
    ocr_calls, logs = _patch_gone_env(monkeypatch, region=None)
    monkeypatch.setattr(xuanshu.tab_group, "on_leader_screen",
                        lambda win: False)
    assert xuanshu._confirm_task_gone(_GuardWin(), "常规玩法") is False
    assert not ocr_calls  # 不采集证据（不 OCR 队员屏）
    assert any("屏幕已不是队长 Tab" in m for m, _ in logs)


def test_task_gone_dialog_guard_off_leader_skips(monkeypatch):
    """任务消失后的对话框复核：屏幕不是队长 Tab 时连截图都不做
    （护栏在截图/OCR 之前——队员屏的对话框不是本任务的）。"""
    called = []
    win = _GuardWin()
    monkeypatch.setattr(win, "capture",
                        lambda force_activate=False: called.append("capture"))
    monkeypatch.setattr(xuanshu.tab_group, "on_leader_screen",
                        lambda win: False)
    ok = xuanshu._task_gone_dialog_guard(
        win, lambda w, items, img: called.append("handler") or True)
    assert ok is False and called == []


# ---- 领取链路的「世界点击」防护（2026-11-13 用户实机） ----
# 现象：玄枢轶闻回到领取点附近后又走到 [117,136]、本轮领取失败，下一轮
# 走回领取点才领到。原因是寻路未停稳时点领取按钮/缓存选项，点击落到游戏
# 世界（既重新指定目的地又把对话框关掉）。两道门：站定确认 + 缓存选项
# 点击前同帧对话框模板复检。

def _pos_frame(offset):
    """位置文字区带白色方块的地图帧（offset 变化=人物在走）。"""
    import numpy as np
    arr = np.zeros((831, 1378, 3), dtype="uint8")
    l, t = 45, 60
    arr[t:t + 20, l + (offset % 150):l + (offset % 150) + 10] = 255
    return Image.fromarray(arr)


class _MoveWin:
    """按序返回帧的假窗口（帧序列代表位置区像素变化）。"""

    def __init__(self, frames):
        self.frames = frames
        self.i = 0

    def capture(self, force_activate=False):
        img = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return img


def test_wait_option_visible_requires_dialog_frame(monkeypatch):
    """无对话框时世界里的同文案文字（NPC 头顶浮标/任务浮字）不算选项：
    对话框边框模板未命中 → 不判「选项已出现」（否则缓存坐标点到世界）。"""
    clock = _GoneClock()
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu, "dialog_frame_score", lambda frame: 0.5)
    ocr_calls = []
    monkeypatch.setattr(
        xuanshu.game_map, "ocr_regions",
        lambda img, regions: ocr_calls.append(1)
        or ([("玄枢轶闻", (490, 427, 600, 447), 0.99)], None))
    assert xuanshu._wait_option_visible(_GuardWin(), "玄枢轶闻", None) is False
    assert not ocr_calls  # 模板未命中连 OCR 都不做


def test_wait_option_visible_with_dialog_frame(monkeypatch):
    """对话框模板在场 + 选项行可见 → 判定选项已渲染。"""
    clock = _GoneClock()
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu, "dialog_frame_score", lambda frame: 0.98)
    monkeypatch.setattr(
        xuanshu.game_map, "ocr_regions",
        lambda img, regions: ([("玄枢轶闻", (490, 427, 600, 447), 0.99)], None))
    assert xuanshu._wait_option_visible(_GuardWin(), "玄枢轶闻", None) is True


def test_cached_option_click_blocked_when_dialog_closed(monkeypatch):
    """渲染等待（0.3s）内对话框关闭：点击前复检不通过 → 不点缓存坐标
    （同一点击会落在游戏世界把人物带走），清缓存回退识别路径。"""
    clock = _GoneClock()
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu.app_utils, "high_perf_on", lambda: True)
    monkeypatch.setattr(xuanshu.app_utils, "fixed_pos_get",
                        lambda key, win: (490, 427))
    cleared = []
    monkeypatch.setattr(xuanshu.app_utils, "fixed_pos_clear",
                        lambda key: cleared.append(key))
    monkeypatch.setattr(xuanshu, "_wait_option_visible", lambda *a, **k: True)
    monkeypatch.setattr(xuanshu, "_dialog_option_visible_now",
                        lambda win, text: False)
    clicks = []
    monkeypatch.setattr(xuanshu, "human_click",
                        lambda *a, **k: clicks.append(a))
    monkeypatch.setattr(xuanshu.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    logs = []
    monkeypatch.setattr(xuanshu, "log",
                        lambda msg, level="INFO": logs.append((msg, level)))

    ok, _info = xuanshu._click_dialog_green_option(_GuardWin(), "玄枢轶闻")
    assert ok is False
    assert clicks == []                      # 没有点到世界
    assert cleared == ["option_玄枢轶闻"]     # 缓存作废，下轮回退识别
    assert any("复检" in m for m, _ in logs)


def test_wait_claim_still_true_when_static(monkeypatch):
    """位置区像素连续不变 → 站定，放行领取按钮点击。"""
    clock = _GoneClock()
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu.app_utils, "track_walk_idle_seconds",
                        lambda: 100.0)
    ok, info = xuanshu._wait_claim_still(_MoveWin([_pos_frame(0)]))
    assert ok is True and info == "人物已站定"


def test_wait_claim_still_false_while_walking(monkeypatch):
    """位置区像素持续变化（寻路未停）→ 超时返回 False，本轮不点领取。"""
    clock = _GoneClock()
    monkeypatch.setattr(xuanshu, "time", clock)
    monkeypatch.setattr(xuanshu.app_utils, "track_walk_idle_seconds",
                        lambda: 100.0)
    frames = [_pos_frame(i * 20) for i in range(60)]
    ok, info = xuanshu._wait_claim_still(_MoveWin(frames))
    assert ok is False and "站定" in info
