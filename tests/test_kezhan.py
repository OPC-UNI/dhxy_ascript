"""kezhan.py：奇遇对话框第三幕（单选项「我知道了」）识别与点击，
三才锁阴阵任务（循法固阵：引灯→镇煞→定魂 按序点击）的进度计数与链接定位。

奇遇 2026-10-31 实测：开箱奇遇第三幕「掌柜却倏然现身…封口费」无匹配键，
弹窗遗留遮挡掌柜模板致任务终止。OCR 行取自当天用户截图实测数据。
三才 2026-11-02 用户截图实测：初始文案「根据钟馗所示，依据点灯、镇鬼、
固魂之序加固镇煞阵法、引灯阵法、定魂阵法三处三才锁阴阵阵法。」，三个
追踪点嵌描述行内（镇煞阵法 y271-284/x145-203、引灯阵法 y288-301/x26-82、
定魂阵法 y288-301/x99-158，下划线各自 y287/y304），文案阅读序≠点击序。
"""
from src import kezhan
from src import task_tracker


# 截图实测 OCR 行（txt, rect, score）；对话框区域行（x≥400）
QIYU_3RD_ITEMS = [
    ("客栈奇遇", (619, 287, 768, 351), 1.0),
    ("概率获得奖励加成！", (629, 352, 786, 377), 1.0),
    ("箱中宝物莹莹发光，你伸手欲取，掌柜却倏然现身，一把收走，回头",
     (462, 388, 927, 417), 1.0),
    ("塞给你些银子当作封口费。", (464, 410, 646, 434), 1.0),
    ("（恭喜少侠获得额外收益）", (469, 432, 675, 470), 0.94),
    ("我知道了", (653, 505, 723, 531), 1.0),
]


def test_handle_qiyu_dialog_clicks_third_screen_option(monkeypatch):
    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "move_away", lambda win: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    win = object()
    assert kezhan._handle_qiyu_dialog(win, QIYU_3RD_ITEMS, None) is True
    # 点「我知道了」文本矩形中心
    assert clicks == [((653 + 723) // 2, (505 + 531) // 2)]


def test_handle_qiyu_dialog_no_key_match(monkeypatch):
    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "move_away", lambda win: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    items = [
        ("陌生奇遇文案没有任何匹配键", (462, 388, 927, 417), 1.0),
        ("某个选项", (653, 505, 723, 531), 1.0),
    ]
    assert kezhan._handle_qiyu_dialog(object(), items, None) is False
    assert clicks == []


# 2026-11-09 用户截图实测 OCR 行（区域 400,270,980,600）：
# 横幅 (619,287,770,350)、副标题 (630,353,782,375) 缩进但非选项、
# 叙述行左对齐 x≈466-472（末行底缘 464）、选项「我知道了……」(653,507)
QIYU_REAL_ITEMS = [
    ("客栈奇遇", (619, 287, 770, 350), 1.00),
    ("概率获得奖励加成！", (630, 353, 782, 375), 1.00),
    ("你推开银票，厉声质问。围观人群被你正气所慑，纷纷支持。大使为",
     (467, 392, 921, 411), 0.99),
    ("息事宁人，被迫退钱并给了你一笔额外补偿。", (466, 411, 764, 432), 1.00),
    ("(恭喜少侠获得额外收益)", (472, 437, 672, 464), 0.95),
    ("我知道了……", (653, 507, 748, 529), 0.93),
]


def test_handle_qiyu_dialog_template_anchor_clicks_first_option(monkeypatch):
    """未收录文案的新奇遇：模板锚点通用定位点第一个缩进选项（不点副标题）。"""
    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "move_away", lambda win: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    assert kezhan._handle_qiyu_dialog(
        object(), QIYU_REAL_ITEMS, None,
        banner_pt=(697, 332), banner_h=29) is True
    assert clicks == [((653 + 748) // 2, (507 + 529) // 2)]


def test_handle_qiyu_dialog_template_anchor_picks_first_of_multi(monkeypatch):
    """多选项：模板锚点路径按阅读序取第一个选项。"""
    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "move_away", lambda win: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    items = [
        ("客栈奇遇", (619, 287, 770, 350), 1.00),
        ("师傅传你一套口诀，问你如何运功", (462, 388, 900, 417), 1.00),
        ("立即运功，镇压凶气", (641, 470, 761, 494), 1.00),
        ("不为所动，催劲强压", (641, 494, 761, 518), 1.00),
    ]
    assert kezhan._handle_qiyu_dialog(
        object(), items, None, banner_pt=(697, 332), banner_h=29) is True
    assert clicks == [((641 + 761) // 2, (470 + 494) // 2)]


def test_clear_qiyu_popup_clicks_until_gone(monkeypatch):
    """残留弹窗：模板命中→点选项，下一帧模板消失→返回已处理。"""
    calls = []
    scores = [(0.9, (697, 332), 29), (0.2, None, 0)]
    monkeypatch.setattr(
        kezhan, "_qiyu_match",
        lambda f: scores.pop(0) if scores else (0.2, None, 0))
    monkeypatch.setattr(kezhan.game_map, "ocr_regions",
                        lambda img, regions: ([], None))
    monkeypatch.setattr(kezhan, "_handle_qiyu_dialog",
                        lambda *a, **k: calls.append(1) or True)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)

    class W:
        def capture(self, **k):
            return object()

    assert kezhan._clear_qiyu_popup(W()) is True
    assert len(calls) == 1


def test_clear_qiyu_popup_no_popup(monkeypatch):
    """无奇遇弹窗：不点击、返回未处理。"""
    called = []
    monkeypatch.setattr(kezhan, "_qiyu_match", lambda f: (0.1, None, 0))
    monkeypatch.setattr(kezhan, "_handle_qiyu_dialog",
                        lambda *a, **k: called.append(1) or True)

    class W:
        def capture(self, **k):
            return object()

    assert kezhan._clear_qiyu_popup(W()) is False
    assert called == []


# ---- 对话框模板门控（2026-11-09）----
def test_qiyu_frame_band_covers_real_banner_rect():
    """奇遇横幅实测位置（2026-10-31 截图 OCR「客栈奇遇」@619,287-768,351）
    必须落在搜索条带内，否则真机永远不命中。"""
    l, t, r, b = kezhan.QIYU_FRAME_BAND
    assert l <= 619 and t <= 287 and 768 <= r and 351 <= b


def test_qiyu_present_detects_banner_and_rejects_noise(monkeypatch):
    import cv2
    import numpy as np
    from pathlib import Path
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    tpl = cv2.imdecode(np.fromfile(
        str(Path(__file__).resolve().parent.parent / "res/img"
            / kezhan.QIYU_FRAME_ICON), dtype=np.uint8), cv2.IMREAD_COLOR)
    rng = np.random.default_rng(12345)
    frame = rng.integers(0, 256, (831, 1378, 3), dtype=np.uint8)
    assert kezhan._qiyu_present(frame) is False       # 噪声帧不误命中
    f = frame.copy()
    h, w = tpl.shape[:2]
    f[287:287 + h, 619:619 + w] = tpl                  # 贴实测横幅位置
    assert kezhan._qiyu_present(f) is True


def test_handle_dialog_options_gated_off(monkeypatch):
    """标准/奇遇模板都未命中：两表与奇遇逻辑都不执行（防盲点）。"""
    called = []
    monkeypatch.setattr(kezhan, "_qiyu_match", lambda f: (0.1, None, 0))
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda f: 0.1)
    monkeypatch.setattr(kezhan, "_handle_qiyu_dialog",
                        lambda *a: called.append("qiyu") or True)
    monkeypatch.setattr(kezhan, "_handle_quiz_options",
                        lambda *a: called.append("quiz") or True)
    monkeypatch.setattr(kezhan, "_click_green_option_fuzzy",
                        lambda *a: called.append("opt") or True)
    monkeypatch.setattr(kezhan, "_click_screen_center",
                        lambda *a, **k: called.append("skip"))
    assert kezhan._handle_dialog_options(object(), [], None,
                                         frame=object()) is False
    assert called == []


def test_handle_dialog_options_qiyu_gate(monkeypatch):
    """奇遇模板命中：进奇遇逻辑（标准模板未命中也不跑两表）。"""
    monkeypatch.setattr(kezhan, "_qiyu_match", lambda f: (0.9, (697, 332), 29))
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda f: 0.1)
    monkeypatch.setattr(kezhan, "_handle_qiyu_dialog",
                        lambda *a, **k: True)
    assert kezhan._handle_dialog_options(object(), [], None,
                                         frame=object()) is True


def test_handle_dialog_options_standard_gate(monkeypatch):
    """标准对话框模板命中：奇遇不触发，两表正常判定。"""
    end_norm = kezhan.norm_text(kezhan.KEZHAN_END_TEXT)
    monkeypatch.setattr(kezhan, "_qiyu_match", lambda f: (0.1, None, 0))
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda f: 0.9)
    monkeypatch.setattr(kezhan, "_handle_qiyu_dialog",
                        lambda *a, **k: True)
    monkeypatch.setattr(kezhan, "_handle_quiz_options", lambda *a: False)
    monkeypatch.setattr(kezhan, "_dialog_text_present",
                        lambda items, dtxt: dtxt != end_norm)
    monkeypatch.setattr(kezhan, "_click_green_option_fuzzy", lambda *a: True)
    monkeypatch.setattr(kezhan, "_arm_yinlv_check", lambda opt: None)
    assert kezhan._handle_dialog_options(object(), [], None,
                                         frame=object()) is True


# ---- 三才锁阴阵任务（循法固阵）----
# 初始布局 OCR 行与下划线位置取自用户 2026-11-02 截图实测
SANCAI_INITIAL_ITEMS = [
    ("任务追踪", (83, 205, 167, 225), 1.0),
    ("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
    ("根据钟馗所示，依据点灯、镇", (22, 250, 224, 272), 1.0),
    ("鬼、固魂之序加固镇煞阵法、", (21, 265, 216, 290), 1.0),
    ("引灯阵法、定魂阵法三处三才", (21, 284, 221, 306), 1.0),
    ("锁阴阵阵法。", (22, 302, 109, 322), 1.0),
]


def _frame(underlines=(), glyphs=()):
    """白色 RGB 帧（1378x831）。underlines: (y, x0, x1) 亮绿实线段；
    glyphs: (y0, y1, x0, x1) 亮绿字形块。"""
    import numpy as np
    f = np.full((831, 1378, 3), 255, np.uint8)
    for y, x0, x1 in underlines:
        f[y, x0:x1 + 1] = (0, 208, 17)
    for y0, y1, x0, x1 in glyphs:
        f[y0:y1, x0:x1] = (0, 208, 17)
    return f


# 初始布局下划线（实测：镇煞 y287 x144-203；引灯 y304 x24-83；定魂 y304 x99-158）
SANCAI_UNDERLINES = ((287, 144, 203), (304, 24, 83), (304, 99, 158))


def test_sancai_stage_and_count_initial():
    items = list(SANCAI_INITIAL_ITEMS)
    assert kezhan._sancai_stage(items) is True
    assert kezhan._sancai_count(items) is None  # 未加固 → 进度键 0
    assert kezhan._sancai_progress_key(items, None) == "0"


def test_sancai_picker_initial_clicks_yindeng():
    """初始状态（0 处加固）→ 点 引灯阵法（下划线 x24-83 中心上方 2px）。"""
    pt = kezhan._sancai_picker(list(SANCAI_INITIAL_ITEMS),
                               _frame(underlines=SANCAI_UNDERLINES))
    assert pt == (53, 302)
    # 调度器同点（三才阶段优先于四象逻辑）
    assert kezhan._pick_task_green(list(SANCAI_INITIAL_ITEMS),
                                   _frame(underlines=SANCAI_UNDERLINES)) == (53, 302)


def test_sancai_locate_three_keywords():
    """三个关键词分别定位到各自下划线（文案阅读序≠点击序，按词定位）。"""
    f = _frame(underlines=SANCAI_UNDERLINES)
    items = list(SANCAI_INITIAL_ITEMS)
    assert kezhan._sancai_locate_link(items, f, "引灯阵法") == (53, 302)
    assert kezhan._sancai_locate_link(items, f, "镇煞阵法") == (173, 285)
    assert kezhan._sancai_locate_link(items, f, "定魂阵法") == (128, 302)


def test_sancai_count_progress_states():
    """「第N处阵法已加固完成」计数：1/2/3 与变体（数字、空格）均识别。"""
    for n, txt in ((1, "第一处阵法已加固完成"), (2, "第二处阵法已加固完成"),
                   (3, "第 3 处 阵法 已 加固 完成")):
        items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
                 (txt, (22, 249, 198, 275), 1.0),
                 ("、".join(kezhan.SANCAI_LINK_ORDER[n:]) + "三处三才",
                  (21, 265, 216, 290), 1.0)]
        assert kezhan._sancai_count(items) == n
        assert kezhan._sancai_progress_key(items, None) == str(n)


def test_sancai_picker_second_stage_targets_zhensha():
    """1 处已加固 → 点 镇煞阵法（剩余 镇煞、定魂 同行，取前者下划线）。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第一处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("镇煞阵法、定魂阵法三处三才", (21, 265, 216, 290), 1.0)]
    f = _frame(underlines=((287, 24, 83), (287, 99, 158)))  # 镇煞/定魂 同行下划线
    pt = kezhan._sancai_picker(items, f)
    assert pt == (53, 285)


def test_sancai_picker_third_stage_targets_dinghun():
    """2 处已加固 → 点 定魂阵法。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第二处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("定魂阵法三处三才", (21, 265, 140, 290), 1.0)]
    f = _frame(underlines=((287, 24, 83),))
    assert kezhan._sancai_picker(items, f) == (53, 285)


def test_sancai_picker_done_falls_back_to_line_level():
    """3 处均已加固（计数用尽）→ 返回 None 交回行级选择（如复命链接）。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第三处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("回去告知钟馗", (22, 265, 140, 290), 1.0)]
    assert kezhan._sancai_picker(items, None) is None


def test_sancai_picker_stage_miss_returns_pick_fail():
    """阶段内目标无法定位（下划线/字形均无）→ PICK_FAIL 拒绝行级兜底。"""
    pt = kezhan._sancai_picker(list(SANCAI_INITIAL_ITEMS), _frame())
    assert pt == task_tracker.PICK_FAIL


def test_sancai_picker_other_task_returns_none():
    """非三才任务（四象）→ 不介入，回退四象顺序逻辑（无绿字时 None）。"""
    items = [("月宫采珍[客栈任务]", (16, 229, 159, 252), 1.0),
             ("青龙", (22, 249, 60, 275), 1.0),
             ("白虎", (22, 265, 60, 291), 1.0),
             ("朱雀", (22, 281, 60, 307), 1.0),
             ("玄武", (22, 297, 60, 323), 1.0)]
    assert kezhan._sancai_stage(items) is False
    assert kezhan._sancai_progress_key(items, None) is None
    assert kezhan._sancai_picker(items, None) is None


def test_sancai_glyph_fallback_click():
    """下划线缺失的渲染变体 → 回退绿色字形簇，点击行底上方 2px。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第二处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("定魂阵法三处三才", (21, 265, 140, 290), 1.0)]
    # 字形块：4 个字各 13px 宽、间距 3px（连排 ≤13px 不构成下划线实线段）
    f = _frame(glyphs=((271, 284, 24, 37), (271, 284, 40, 53),
                       (271, 284, 56, 69), (271, 284, 72, 85)))
    pt = kezhan._sancai_picker(items, f)
    # 簇相对区域 x3-64 中心 33（窗口坐标 21+33），y 283-2（行底可点区）
    assert pt == (54, 282)


def test_order_progress_key_sancai_priority():
    """_order_progress_key：三才阶段返回计数键，非三才回退四象/文本签名。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第一处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("镇煞阵法、定魂阵法三处三才", (21, 265, 216, 290), 1.0)]
    assert kezhan._order_progress_key(items, None) == "1"


# ---- 破除封印小游戏闭环：刻度映射与填充边缘测量（7.png 标定 2026-11-09）----
def test_pochu_tick_mapping():
    """窗口 x → 1~10 刻度：435→0、992→10，中点 713.5→5。"""
    assert kezhan._pochu_tick(435) == 0.0
    assert kezhan._pochu_tick(992) == 10.0
    assert abs(kezhan._pochu_tick(713.5) - 5.0) < 1e-9
    assert abs(kezhan._pochu_tick(492) - 1.023) < 0.01   # 7.png 实测填充边缘
    assert abs(kezhan._pochu_tick(908) - 8.49) < 0.01    # 7.png 实测目标带中心


def test_pochu_fill_edge_finds_right_edge():
    """合成帧：条带内画金填充段（x433~600），右边缘应≈600；
    目标带括号（白金色 H36 S61）不干扰连通段。"""
    import cv2
    import numpy as np
    frame = np.zeros((828, 1376, 3), np.uint8)          # 暗背景
    l, t, r, b = kezhan.POCHU_BAR_REGION
    y0, y1 = kezhan.POCHU_BAR_Y
    # 填充：BGR 金色（HSV H≈21 S≈133 V≈176）→ BGR (84,131,176)
    frame[y0:y1, 433:601] = (84, 131, 176)
    # 目标带括号：白金色 (164,208,215)（H36 S61 V215），位置 x879~881/934~936
    frame[y0 - 2:y1 + 2, 879:882] = (164, 208, 215)
    frame[y0 - 2:y1 + 2, 934:937] = (164, 208, 215)
    assert kezhan._pochu_fill_edge(frame) == 600
    # 无填充 → None
    assert kezhan._pochu_fill_edge(np.zeros_like(frame)) is None
    # 填充与括号间隔断开（缺口>POCHU_FILL_GAP）：连通段仍止于填充右缘
    frame2 = np.zeros((828, 1376, 3), np.uint8)
    frame2[y0:y1, 433:600] = (84, 131, 176)
    frame2[y0 - 2:y1 + 2, 700:703] = (164, 208, 215)
    assert kezhan._pochu_fill_edge(frame2) == 599


def test_play_pochufengyin_delegates_shared_module(monkeypatch):
    """破除封印闭环调用共享模块，标题模板/日志前缀与名称由客栈传入。"""
    seen = {}

    def _play(win, icon, prefix="客栈", label="破除封印"):
        seen.update(icon=icon, prefix=prefix, label=label)
        return True, "破除封印结束"

    monkeypatch.setattr(kezhan._pochu_minigame, "play_pochufengyin", _play)
    assert kezhan._play_pochufengyin(object()) == (True, "破除封印结束")
    assert seen == {"icon": "kezhan_pochufengyin.png",
                    "prefix": "客栈", "label": "破除封印"}


# ---- 诊断页找阿旺验证（2026-11-10）----
def test_verify_find_awang_battle_short_circuit(monkeypatch):
    """战斗中：直接失败返回，不触发寻路。"""
    called = []
    monkeypatch.setattr(kezhan, "is_in_battle", lambda w: True)
    monkeypatch.setattr(kezhan, "_walk_to_dali",
                        lambda w: called.append(1) or (True, "x"))

    class W:
        pass

    ok, info = kezhan.verify_find_awang(W())
    assert ok is False and "战斗中" in info and called == []


def test_verify_find_awang_delegates_to_walk_to_dali(monkeypatch):
    """非战斗：走真实 _walk_to_dali 链路，结果原样透传。"""
    monkeypatch.setattr(kezhan, "is_in_battle", lambda w: False)
    monkeypatch.setattr(kezhan, "_walk_to_dali",
                        lambda w: (True, "已点击阿旺"))

    class W:
        pass

    ok, info = kezhan.verify_find_awang(W())
    assert ok is True and info == "已点击阿旺"


# ---- 任务消失确认：客栈允许面板缺席（2026-11-11 16:29/16:33 实测） ----
def test_follow_kezhan_disables_panel_requirement(monkeypatch):
    """客栈 follow_task 必须传 task_gone_require_panel=False。

    游戏在无追踪任务时会把「任务追踪」面板自动收起（只剩左缘小箭头）：
    客栈任务在交付对话点完的瞬间面板即消失，若仍要求面板在场，任务
    消失确认永远不判定、空转到跟踪超时（300s）才去领取（实测卡 2~3
    分钟）。客栈任务在即面板必在，面板没了即完成。
    """
    seen = {}

    def _fake_follow(win, title, **kwargs):
        seen["title"] = title
        seen.update(kwargs)
        return True, "任务已从任务追踪中消失"

    monkeypatch.setattr(kezhan, "follow_task", _fake_follow)
    status, info = kezhan._follow_kezhan(object())
    assert status == "done"
    assert seen["task_gone_require_panel"] is False
    assert seen["prefix"] == "客栈"


# ---- 阿旺走近点按屏幕左右侧取侧（2026-11-11 用户规则） ----
class _AwangWin:
    """窗口宽 1376：中心 688，中间区 508~868（±180）。"""

    class rect:
        width = 1376
        height = 828


def test_awang_approach_left_side_walks_to_right():
    """阿旺在屏幕左侧 → 走到其右侧 90px。"""
    assert kezhan._awang_approach_pos(200, 400, _AwangWin()) == (290, 400)


def test_awang_approach_right_side_walks_to_left():
    """阿旺在屏幕右侧 → 走到其左侧 90px。"""
    assert kezhan._awang_approach_pos(1200, 400, _AwangWin()) == (1110, 400)


def test_awang_approach_center_region_no_walk():
    """阿旺在屏幕中间 ±180px 内（含边界）→ 不走近，返回 None。"""
    win = _AwangWin()
    assert kezhan._awang_approach_pos(688, 400, win) is None
    assert kezhan._awang_approach_pos(508, 400, win) is None
    assert kezhan._awang_approach_pos(868, 400, win) is None
    # 边界外一像素即按对应侧走近
    assert kezhan._awang_approach_pos(507, 400, win) == (597, 400)
    assert kezhan._awang_approach_pos(869, 400, win) == (779, 400)


def test_check_awang_center_clicks_interact_point_directly(monkeypatch):
    """阿旺在屏幕中间区域：直接点交互点，不再走近等待。"""
    clicks, arrived = [], []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_find_awang", lambda w: (700, 400))
    monkeypatch.setattr(kezhan, "_wait_awang_arrival",
                        lambda w: arrived.append(1))
    ok, info = kezhan._check_awang(_AwangWin())
    assert ok is True and info == "已点击阿旺"
    assert clicks == [(700, 400)]
    assert arrived == []


def test_check_awang_left_side_walks_then_clicks(monkeypatch):
    """阿旺在屏幕左侧：先点其右侧 90px 走近，到位后重新定位再点交互点。"""
    clicks, arrived = [], []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_find_awang", lambda w: (200, 400))
    monkeypatch.setattr(kezhan, "_wait_awang_arrival",
                        lambda w: arrived.append(1))
    ok, info = kezhan._check_awang(_AwangWin())
    assert ok is True and info == "已点击阿旺"
    assert clicks == [(290, 400), (200, 400)]
    assert arrived == [1]


def test_check_awang_ocr_name_approach_uses_name_y(monkeypatch):
    """OCR 名字兜底：交互点=名字上方 40px，走近点=名字行 y 的右/左侧 90px。"""
    clicks, arrived = [], []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_find_awang", lambda w: None)
    monkeypatch.setattr(kezhan, "_find_awang_by_ocr", lambda w: (1200, 300))
    monkeypatch.setattr(kezhan, "_wait_awang_arrival",
                        lambda w: arrived.append(1))
    ok, _info = kezhan._check_awang(_AwangWin())
    assert ok is True
    # 名字在屏幕右侧 → 走近点在其左侧 90px，y 取名字行中心
    assert clicks == [(1110, 300), (1200, 260)]
    assert arrived == [1]


# ---- 阿旺模板组：三模板统一阈值 0.72（2026-11-11 用户提供素材） ----
class _AwangCaptureWin:
    def capture(self, **k):
        import numpy as np
        return np.zeros((8, 8, 4), np.uint8)  # RGBA 帧，_find_awang 截前 3 通道


def _awang_templates_present(monkeypatch, missing=()):
    monkeypatch.setattr(kezhan.game_map, "template_size",
                        lambda icon: None if icon in missing else (24, 36))


def _awang_scores(monkeypatch, scores):
    monkeypatch.setattr(kezhan.game_map, "best_match_np",
                        lambda frame, icon: scores[icon])


def test_find_awang_three_templates_highest_score_wins(monkeypatch):
    """三模板同帧匹配：达标模板中取最高分，未达标模板不参与。"""
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    _awang_templates_present(monkeypatch)
    _awang_scores(monkeypatch, {"awang.png": (0.60, (100, 100)),
                                "awang2.png": (0.74, (200, 200)),
                                "awang3.png": (0.80, (300, 300))})
    assert kezhan._find_awang(_AwangCaptureWin()) == (300, 300)


def test_find_awang_threshold_boundary_hits(monkeypatch):
    """任一模板恰好 0.72 即命中（统一阈值含边界）。"""
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    _awang_templates_present(monkeypatch)
    _awang_scores(monkeypatch, {"awang.png": (0.72, (120, 130)),
                                "awang2.png": (0.30, (0, 0)),
                                "awang3.png": (0.20, (0, 0))})
    assert kezhan._find_awang(_AwangCaptureWin()) == (120, 130)


def test_find_awang_all_below_threshold_returns_none(monkeypatch):
    """三模板分数均低于 0.72 → 未命中（交给 OCR 名字兜底）。"""
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    _awang_templates_present(monkeypatch)
    _awang_scores(monkeypatch, {"awang.png": (0.71, (10, 10)),
                                "awang2.png": (0.71, (20, 20)),
                                "awang3.png": (0.71, (30, 30))})
    assert kezhan._find_awang(_AwangCaptureWin()) is None


def test_find_awang_missing_template_skipped_warn_once(monkeypatch):
    """缺失模板跳过、其余照常匹配；缺失告警只记录一次。"""
    logs = []
    monkeypatch.setattr(kezhan, "log",
                        lambda msg, *a, **k: logs.append(msg))
    monkeypatch.setattr(kezhan, "_awang_missing_logged", False)
    _awang_templates_present(monkeypatch, missing=("awang3.png",))
    _awang_scores(monkeypatch, {"awang.png": (0.50, (0, 0)),
                                "awang2.png": (0.75, (250, 250))})
    assert kezhan._find_awang(_AwangCaptureWin()) == (250, 250)
    assert len(logs) == 1 and "awang3.png" in logs[0]
    # 标志已置位：再次调用不重复告警
    assert kezhan._find_awang(_AwangCaptureWin()) == (250, 250)
    assert len(logs) == 1


def test_find_awang_all_templates_missing_returns_none(monkeypatch):
    """三模板全部缺失：直接返回 None（不截帧），OCR 名字兜底仍在。"""
    logs, captured = [], []
    monkeypatch.setattr(kezhan, "log",
                        lambda msg, *a, **k: logs.append(msg))
    monkeypatch.setattr(kezhan, "_awang_missing_logged", False)
    monkeypatch.setattr(kezhan.game_map, "template_size", lambda icon: None)

    class W:
        def capture(self, **k):
            captured.append(1)
            return None

    assert kezhan._find_awang(W()) is None
    assert captured == []
    assert len(logs) == 1

