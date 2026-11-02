"""xuanshu.py：任务签名相似判定（follow 轮询防抖核心）与
任务消失弹窗守卫（2026-10-31 客栈奇遇弹窗遗留教训）。"""
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
