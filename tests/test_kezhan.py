"""kezhan.py：奇遇对话框第三幕（单选项「我知道了」）识别与点击，
三才锁阴阵任务（循法固阵：引灯→镇煞→定魂 按序点击）的进度计数与链接定位。

奇遇 2026-10-31 实测：开箱奇遇第三幕「掌柜却倏然现身…封口费」无匹配键，
弹窗遗留遮挡掌柜模板致任务终止。OCR 行取自当天用户截图实测数据。
三才 2026-11-02 用户截图实测：初始文案「根据钟馗所示，依据点灯、镇鬼、
固魂之序加固镇煞阵法、引灯阵法、定魂阵法三处三才锁阴阵阵法。」，三个
追踪点嵌描述行内（镇煞阵法 y271-284/x145-203、引灯阵法 y288-301/x26-82、
定魂阵法 y288-301/x99-158，下划线各自 y287/y304），文案阅读序≠点击序。
"""
import time

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
    assert kezhan._locate_panel_link(items, f, "引灯阵法") == (53, 302)
    assert kezhan._locate_panel_link(items, f, "镇煞阵法") == (173, 285)
    assert kezhan._locate_panel_link(items, f, "定魂阵法") == (128, 302)


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


# ---- 智戏成空任务（凤巢：风吟印→炎阳印→水镜印 依次开启三处封印）----
# 初始布局取自用户 2026-11-12 截图实测（截图 1.5x 放大，此处为 1378x831
# 窗口坐标）：关键词行「水镜印、风吟印、炎阳印三处」(23,285)-(219,305)，
# 三个链接下划线 y=304 x24-68 / 84-128 / 144-188——阅读序（水镜→风吟→
# 炎阳）≠ 点击序（风吟→炎阳→水镜，任务原文「按风火水三相轮回之序开启」）。
ZHIXI_INITIAL_ITEMS = [
    ("任务追踪", (84, 205, 167, 225), 1.0),
    ("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
    ("根据客栈掌柜所示，前往凤巢", (23, 251, 219, 271), 1.0),
    ("按风火水三相轮回之序开启", (23, 269, 220, 288), 1.0),
    ("水镜印、风吟印、炎阳印三处", (23, 285, 219, 305), 0.99),
    ("封印。", (21, 302, 67, 323), 1.0),
]
ZHIXI_UNDERLINES = ((304, 24, 68), (304, 84, 128), (304, 144, 188))
# 阶段 1/2 的文案排版未实机标定，按「剩余链接重新成行」建模：定位按
# 关键词 + 最近下划线实线段，与具体折行布局无关（同一函数亦服务三才）
ZHIXI_STAGE1_ITEMS = [
    ("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
    ("第一处封印已开启，再去开启", (23, 251, 219, 271), 1.0),
    ("炎阳印、水镜印两处封印。", (23, 269, 219, 289), 1.0),
]
ZHIXI_STAGE2_ITEMS = [
    ("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
    ("第二处封印已开启，再去开启", (23, 251, 219, 271), 1.0),
    ("水镜印一处封印。", (23, 269, 219, 289), 1.0),
]
ZHIXI_DONE_ITEMS = [
    ("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
    ("封印已经全部开启", (23, 251, 219, 271), 1.0),
    ("回去告知掌柜", (23, 269, 160, 289), 1.0),
]


def test_zhixi_stage_and_count_initial():
    items = list(ZHIXI_INITIAL_ITEMS)
    assert kezhan._zhixi_stage(items) is True
    assert kezhan._zhixi_count(items) is None  # 未开启 → 进度键 封印0
    assert kezhan._zhixi_progress_key(items, None) == "封印0"


def test_zhixi_locate_three_links():
    """三个关键词按词定位到各自下划线（阅读序≠点击序，按词不按序）。"""
    f = _frame(underlines=ZHIXI_UNDERLINES)
    items = list(ZHIXI_INITIAL_ITEMS)
    assert kezhan._locate_panel_link(items, f, "水镜印") == (46, 302)
    assert kezhan._locate_panel_link(items, f, "风吟印") == (106, 302)
    assert kezhan._locate_panel_link(items, f, "炎阳印") == (166, 302)


def test_zhixi_picker_initial_clicks_fengyin():
    """0 处已开启 → 点风吟印（阅读序第二，点击序第一）。"""
    f = _frame(underlines=ZHIXI_UNDERLINES)
    assert kezhan._zhixi_picker(list(ZHIXI_INITIAL_ITEMS), f) == (106, 302)
    # 调度器同点（智戏成空优先于三才/四象逻辑）
    assert kezhan._pick_task_green(list(ZHIXI_INITIAL_ITEMS), f) == (106, 302)


def test_zhixi_count_progress_states():
    """「第N处封印已开启」计数与全开文案：1/2/3 与数字、空格变体。"""
    for n, txt in ((1, "第一处封印已开启"), (2, "第 2 处 封印 已 开启")):
        items = [("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
                 (txt, (23, 251, 219, 271), 1.0),
                 ("炎阳印、水镜印两处封印。", (23, 269, 219, 289), 1.0)]
        assert kezhan._zhixi_count(items) == n
        assert kezhan._zhixi_progress_key(items, None) == f"封印{n}"
    items = list(ZHIXI_DONE_ITEMS)
    assert kezhan._zhixi_count(items) == 3
    assert kezhan._zhixi_progress_key(items, None) == "封印3"
    # 全开文案变体：OCR 把「已」读成「己」也必须判为 3（短键「全部开启」）
    items = [("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
             ("封印己经全部开启", (23, 251, 219, 271), 1.0)]
    assert kezhan._zhixi_count(items) == 3


def test_zhixi_picker_second_stage_targets_yanyang():
    """1 处已开启 → 点炎阳印（点击序第二）。"""
    f = _frame(underlines=((288, 24, 68), (288, 84, 128)))
    pt = kezhan._zhixi_picker(list(ZHIXI_STAGE1_ITEMS), f)
    assert pt == (46, 286)


def test_zhixi_picker_third_stage_targets_shuijing():
    """2 处已开启 → 点水镜印（点击序第三）。"""
    f = _frame(underlines=((288, 24, 68),))
    assert kezhan._zhixi_picker(list(ZHIXI_STAGE2_ITEMS), f) == (46, 286)


def test_zhixi_picker_handles_relayout_and_reorder():
    """追踪文案变化导致链接换行、剩余链接阅读序调换：按关键词逐帧定位。

    用户 2026-11-12 提醒：每点一处追踪文案即改写，三个链接位置随之变化。
    定位不缓存坐标（每帧按关键词在行内位置比例 + 最近下划线实线段重定位），
    换行/换位/阅读序变化均不影响。
    """
    items = [("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
             ("第一处封印已开启，再去开启", (23, 251, 219, 271), 1.0),
             # 剩余链接换行且阅读序调换（炎阳印在后）
             ("水镜印、炎阳印两处封印。", (23, 269, 219, 289), 1.0)]
    f = _frame(underlines=((288, 24, 68), (288, 84, 128)))
    assert kezhan._zhixi_picker(items, f) == (106, 286)  # 第二段下划线=炎阳印


def test_zhixi_picker_handles_keyword_split_across_lines():
    """关键词被折行拆开（「…开启炎阳」/「印、水镜印…」）→ 跨行兜底点击。

    主路径单行 find 两行都落空；按「行尾后缀+下一行行首前缀」匹配到
    后缀所在行行底上方 2px（追踪文案变化会改变折行位置，逐帧重算）。
    """
    items = [("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
             ("第一处封印已开启，按风火", (23, 251, 219, 271), 1.0),
             ("水三相轮回之序开启炎阳", (23, 269, 220, 288), 1.0),
             ("印、水镜印两处封印。", (21, 285, 160, 305), 1.0)]
    assert kezhan._zhixi_picker(items, _frame()) == (202, 286)


def test_sancai_picker_handles_keyword_split_across_lines():
    """跨行兜底为三才/智戏成空共用：镇煞阵法拆到两行也能点。"""
    items = [("循法固阵[客栈任务]", (16, 229, 159, 252), 1.0),
             ("第一处阵法已加固完成", (22, 249, 198, 275), 1.0),
             ("引灯阵法、镇煞", (21, 265, 140, 290), 1.0),
             ("阵法、定魂阵法三处三才", (21, 283, 200, 305), 1.0)]
    assert kezhan._sancai_picker(items, _frame()) == (120, 288)


def test_zhixi_picker_done_falls_back_to_line_level():
    """三处全部开启 → 返回 None 交回行级选择（复命链接）。"""
    assert kezhan._zhixi_count(list(ZHIXI_DONE_ITEMS)) == 3
    assert kezhan._zhixi_picker(list(ZHIXI_DONE_ITEMS), None) is None


def test_zhixi_picker_stage_miss_returns_pick_fail():
    """阶段内目标无法定位（下划线/字形均无）→ PICK_FAIL 拒绝行级兜底。"""
    assert kezhan._zhixi_picker(list(ZHIXI_INITIAL_ITEMS),
                                _frame()) == task_tracker.PICK_FAIL


def test_zhixi_stage_excludes_dialog_rows():
    """居中对话框行含「炎阳印」（领取对话「先破风吟阵，再解炎阳印…」）
    不算本任务阶段——否则领取对话期间会以 PICK_FAIL 阻塞正常对话。"""
    items = [("智戏成空[客栈任务]", (21, 232, 161, 251), 1.0),
             ("先破风吟阵，再解炎阳印，终启水镜界。", (462, 388, 927, 417), 1.0)]
    assert kezhan._zhixi_stage(items) is False
    assert kezhan._zhixi_progress_key(items, None) is None
    assert kezhan._zhixi_picker(items, None) is None


def test_zhixi_and_sancai_mutually_exclusive():
    """智戏成空关键词不触发三才逻辑，反之亦然。"""
    assert kezhan._sancai_stage(list(ZHIXI_INITIAL_ITEMS)) is False
    assert kezhan._sancai_picker(list(ZHIXI_INITIAL_ITEMS), None) is None
    assert kezhan._zhixi_stage(list(SANCAI_INITIAL_ITEMS)) is False
    assert kezhan._zhixi_picker(list(SANCAI_INITIAL_ITEMS), None) is None


def test_order_progress_key_zhixi_priority():
    """_order_progress_key：智戏成空阶段返回「封印N」键。"""
    assert kezhan._order_progress_key(list(ZHIXI_INITIAL_ITEMS), None) == "封印0"
    assert kezhan._order_progress_key(list(ZHIXI_STAGE1_ITEMS), None) == "封印1"
    assert kezhan._order_progress_key(list(ZHIXI_DONE_ITEMS), None) == "封印3"


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
    """破除封印闭环调用共享模块，标题模板/日志前缀与名称由客栈传入。

    必须开冲刺连点（mash=True）：同款「点击」窗口单击推进仅 ~0.1 刻度，
    三档拟人节奏到不了目标带（2026-11-12 实机日志 13 击 P=1.38 失败）。
    """
    seen = {}

    def _play(win, icon, prefix="客栈", label="破除封印", mash=False):
        seen.update(icon=icon, prefix=prefix, label=label, mash=mash)
        return True, "破除封印结束"

    monkeypatch.setattr(kezhan._pochu_minigame, "play_pochufengyin", _play)
    assert kezhan._play_pochufengyin(object()) == (True, "破除封印结束")
    assert seen == {"icon": "kezhan_pochufengyin.png",
                    "prefix": "客栈", "label": "破除封印", "mash": True}


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


# ---- 任务消失判定：面板缺席处理已统一到 xuanshu._confirm_task_gone
#      （2026-11-12 用户规则：缺席+站定=完成、缺席+寻路中=任务仍在），
#      客栈不再传 per-task 参数 ----

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


# ---- 阿旺兜底到达目标：185/176 两个落点任一到达即可（2026-11-11 用户） ----
def test_near_luoyang_accepts_either_fallback_landing():
    """兜底目标为多落点：落在 (185,207)/(176,207) 任一点 ±5 内即算到达。"""
    tgt = kezhan.AWANG_FALLBACK_TARGET_POS
    assert kezhan._near_luoyang(("洛阳城", 185, 207), tgt) is True
    assert kezhan._near_luoyang(("洛阳城", 176, 207), tgt) is True
    assert kezhan._near_luoyang(("洛阳城", 180, 210), tgt) is True
    assert kezhan._near_luoyang(("洛阳城", 165, 207), tgt) is False
    assert kezhan._near_luoyang(("长安", 185, 207), tgt) is False
    assert kezhan._near_luoyang(None, tgt) is False


def test_near_luoyang_single_target_still_works():
    """单点目标（大理寺墙外等）行为不变。"""
    assert kezhan._near_luoyang(("洛阳城", 223, 195),
                                kezhan.DALI_TARGET_POS) is True
    assert kezhan._near_luoyang(("洛阳城", 223, 205),
                                kezhan.DALI_TARGET_POS) is False


# ---- 音律小游戏：动态追亮点（2026-11-13 用户规则 + 实机验证） ----
class _YinlvWin:
    """最小窗口桩：capture 返回固定形状 np 帧（_play_yinlv 只做转换）。"""

    def __init__(self):
        import numpy as np
        self.frame = np.zeros((20, 20, 3), np.uint8)

    def capture(self, force_activate=False):
        return self.frame


def _patch_yinlv(monkeypatch, clicks, glows, window_hits,
                 locate_timeout=5.0):
    """给 _play_yinlv 装桩：开始按钮+编钟必中；窗口横幅前 window_hits
    次检查命中、之后消失（连续 YINLV_END_MISSES 次未命中才停）；
    glows 为逐轮询的高亮钟下标（耗尽后恒无高亮）。sleep/轮询/防抖
    置零免真等待。"""
    bells = {icon: (100 + i, 200)
             for i, icon in enumerate(kezhan.YINLV_BELLS)}
    monkeypatch.setattr(kezhan, "YINLV_LOCATE_TIMEOUT", locate_timeout)
    monkeypatch.setattr(kezhan, "YINLV_GLOW_POLL", 0.0)
    monkeypatch.setattr(kezhan, "YINLV_GLOW_COOLDOWN", 0.0)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_yinlv_locate_bells", lambda bgr: bells)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    state = {"checks": 0, "glow": list(glows)}

    def fake_glow(bgr, rois):
        return state["glow"].pop(0) if state["glow"] else None

    monkeypatch.setattr(kezhan, "_yinlv_glow_index", fake_glow)

    def fake_match(bgr, icon, threshold=0.8, region=None, fallback=True):
        if icon == kezhan.YINLV_START_ICON:
            return (668, 552)
        if icon == kezhan.YINLV_WINDOW_ICON:
            state["checks"] += 1
            return (677, 262) if state["checks"] <= window_hits else None
        return None

    monkeypatch.setattr(kezhan.game_map, "match_frame_np", fake_match)
    return state


def test_play_yinlv_follows_glow_and_done_when_banner_gone(monkeypatch):
    """点亮哪个敲哪个；横幅消失即停点，并按完成处理（2026-11-13 用户
    规则：不再按敲击数判失败重新点击）。"""
    clicks = []
    _patch_yinlv(monkeypatch, clicks=clicks, glows=[0, 1, 2],
                 window_hits=2)
    status, info = kezhan._play_yinlv(_YinlvWin(), banner_pt=None)
    assert status == kezhan.YINLV_DONE
    assert "窗口横幅消失" in info
    # 开始按钮 1 次 + 高亮钟 2 次；第 3 轮起横幅已消失 → 停点
    assert clicks[0] == (668, 552)
    assert len(clicks) == 3


def test_play_yinlv_follows_glow_and_succeeds(monkeypatch):
    """横幅在场期间逐个跟随高亮钟点击；横幅消失 → DONE。"""
    clicks = []
    _patch_yinlv(monkeypatch, clicks=clicks, glows=[4, 0, 3, 2, 1],
                 window_hits=5)
    status, info = kezhan._play_yinlv(_YinlvWin(), banner_pt=None)
    assert status == kezhan.YINLV_DONE
    assert len(clicks) == 6  # 开始 1 + 编钟 5
    # 点击落在对应编钟模板中心（编钟 i 中心 (100+i, 200)）
    assert clicks[1:] == [(104, 200), (100, 200), (103, 200),
                          (102, 200), (101, 200)]


def test_play_yinlv_retry_when_locate_timeout(monkeypatch):
    """开始按钮/编钟定位超时（窗口仍在）：返回 retry 交给调用方保持
    armed 重试。"""
    clicks = []
    _patch_yinlv(monkeypatch, clicks=clicks, glows=[],
                 window_hits=0, locate_timeout=0.0)
    status, info = kezhan._play_yinlv(_YinlvWin(), banner_pt=None)
    assert status == kezhan.YINLV_RETRY
    assert clicks == []


def test_yinlv_glow_index_picks_golden_bell():
    """金色光晕落在哪个钟 ROI 就是哪个钟高亮；无高亮返回 None。"""
    import cv2
    import numpy as np
    bells = {icon: (100 + i * 50, 150)
             for i, icon in enumerate(kezhan.YINLV_BELLS)}
    rois = kezhan._yinlv_bell_rois(bells)
    hsv = np.zeros((300, 400, 3), np.uint8)
    cv2.circle(hsv, (250, 150), 30, (22, 200, 220), -1)  # 3 号钟金色光晕
    assert kezhan._yinlv_glow_index(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR),
                                    rois) == 3
    empty = cv2.cvtColor(np.zeros((300, 400, 3), np.uint8),
                         cv2.COLOR_HSV2BGR)
    assert kezhan._yinlv_glow_index(empty, rois) is None


def test_yinlv_window_band_from_banner_pt(monkeypatch):
    """检查条带按检测点+模板尺寸+余量推导；无检测点退回全帧常量。"""
    monkeypatch.setattr(kezhan.game_map, "template_size",
                        lambda icon: (302, 28))
    assert kezhan._yinlv_window_band((677, 262)) == (
        677 - 151 - kezhan.YINLV_BAND_MARGIN,
        262 - 14 - kezhan.YINLV_BAND_MARGIN,
        677 + 151 + kezhan.YINLV_BAND_MARGIN,
        262 + 14 + kezhan.YINLV_BAND_MARGIN)
    assert kezhan._yinlv_window_band(None) is kezhan.YINLV_WINDOW_REGION


def _patch_extras_for_yinlv(monkeypatch, play_result, tracks):
    """给 _handle_follow_extras 装桩：armed 命中 → _play_yinlv 返回
    play_result；记录重新寻路的 track_task 调用。"""
    monkeypatch.setattr(kezhan.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    monkeypatch.setattr(kezhan, "_kj_frame", lambda win, items: object())
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: None)
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan.game_map, "best_match_np",
                        lambda bgr, icon, region=None: (1.0, (677, 262)))
    monkeypatch.setattr(kezhan, "_play_yinlv",
                        lambda win, banner_pt=None: play_result)
    monkeypatch.setattr(kezhan.task_tracker, "track_task",
                        lambda win, title, **k: tracks.append(k) or (True, "ok"))
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_yinlv_armed_at", time.time())
    monkeypatch.setattr(kezhan, "_yinlv_last_check", 0.0)


def test_yinlv_done_disarms_and_skips_repath(monkeypatch):
    """正常结束 → 关闭 armed 检测，不重新点击寻路。"""
    tracks = []
    _patch_extras_for_yinlv(
        monkeypatch, (kezhan.YINLV_DONE, "音律小游戏结束"), tracks)
    assert kezhan._handle_follow_extras(object(), [], None) is True
    assert tracks == []
    assert kezhan._yinlv_armed_at == 0.0


# ---- 拍毒虫小游戏（药圃除害，2026-11-12 实机录制标定） ----

def test_bug_button_pt_prefers_higher_score(monkeypatch):
    seen = {}

    def fake_match(img, icons, region=None):
        seen["icons"] = tuple(icons)
        seen["region"] = region
        return {kezhan.BUG_RESTART_ICON: (0.90, (680, 553)),
                kezhan.BUG_START_ICON: (0.85, (681, 553))}

    monkeypatch.setattr(kezhan.game_map, "best_match_multi_np", fake_match)
    assert kezhan._bug_button_pt(object()) == (680, 553)
    assert seen["icons"] == (kezhan.BUG_RESTART_ICON, kezhan.BUG_START_ICON)
    assert seen["region"] == kezhan.BUG_BUTTON_REGION


def test_bug_button_pt_below_threshold_returns_none(monkeypatch):
    monkeypatch.setattr(
        kezhan.game_map, "best_match_multi_np",
        lambda img, icons, region=None: {
            kezhan.BUG_RESTART_ICON: (0.60, (680, 553)),
            kezhan.BUG_START_ICON: (0.55, (681, 553))})
    assert kezhan._bug_button_pt(object()) is None


def test_bug_candidates_diff_finds_moving_blob():
    import numpy as np
    l, t, _, _ = kezhan.BUG_REGION
    prev = np.zeros((831, 1378, 3), np.uint8)
    cur = prev.copy()
    cur[t + 100:t + 150, l + 200:l + 260] = 255
    boxes = kezhan._bug_candidates(prev, cur)
    assert boxes == [(l + 200, t + 100, 60, 50)]


def test_bug_candidates_ignores_static_scene():
    import numpy as np
    frame = np.zeros((831, 1378, 3), np.uint8)
    assert kezhan._bug_candidates(frame, frame.copy()) == []


def test_match_duchong_returns_best_with_full_frame_offset(monkeypatch):
    import numpy as np

    def fake_match(crop, icons):
        assert tuple(icons) == kezhan.DUCHONG_ICONS
        return {kezhan.DUCHONG_ICONS[0]: (0.66, (10, 12)),
                kezhan.DUCHONG_ICONS[1]: (0.90, (30, 20))}

    monkeypatch.setattr(kezhan.game_map, "best_match_multi_np", fake_match)
    l, t, _, _ = kezhan.BUG_REGION
    score, pt, icon = kezhan._match_duchong(
        np.zeros((831, 1378, 3), np.uint8), [(l + 100, t + 100, 40, 40)])
    assert score == 0.90
    assert icon == kezhan.DUCHONG_ICONS[1]
    # 候选框外扩 BUG_CANDIDATE_PAD 后的裁剪原点再叠加模板中心
    assert pt == (l + 70 + 30, t + 70 + 20)


def test_bug_task_advanced_states():
    assert kezhan._bug_task_advanced(None, "sig") is False  # 读不到不判
    assert kezhan._bug_task_advanced("", "sig") is True     # 任务已消失
    base = "客栈任务|和阿翠一起前往药圃"
    assert kezhan._bug_task_advanced(base + "。", base) is False  # 单字抖动
    assert kezhan._bug_task_advanced("客栈任务|回到女儿村找阿翠", base) is True
    assert kezhan._bug_task_advanced(base, None) is False   # 无基线不判


def test_bug_task_state_capture_failure():
    class W:
        def capture(self, **kwargs):
            return None
    assert kezhan._bug_task_state(W()) is None


def test_swat_round_clicks_once_and_reports_panel(monkeypatch):
    import numpy as np
    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "_bug_candidates",
                        lambda prev, cur: [(400, 300, 40, 40)])
    monkeypatch.setattr(kezhan, "_match_duchong",
                        lambda img, boxes: (0.9, (500, 400), "tpl"))
    state = {"n": -1}

    def fake_capture(win):
        state["n"] += 1
        return np.zeros((10, 10, 3), np.uint8)

    monkeypatch.setattr(kezhan, "_bug_capture", fake_capture)
    # 前 4 帧无面板（可拍虫），第 5/6 帧连续命中面板按钮
    monkeypatch.setattr(kezhan, "_bug_button_pt",
                        lambda img: (680, 553) if state["n"] >= 4 else None)
    status, clicks_n = kezhan._swat_round(object(), time.time() + 2.0)
    assert status == "panel"
    assert clicks_n == 1          # 全局点击冷却 0.8s 内只点一次
    assert clicks == [(500, 400)]


def test_swat_round_idle_returns(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_bug_capture",
                        lambda win: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda img: None)
    monkeypatch.setattr(kezhan, "_bug_candidates", lambda prev, cur: [])
    monkeypatch.setattr(kezhan, "BUG_IDLE_END", 0.0)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    status, clicks = kezhan._swat_round(object(), time.time() + 1.0)
    assert status == "idle" and clicks == 0


def test_swat_round_ignores_below_threshold_match(monkeypatch):
    """候选有动但模板分不足（蜻蜓/特效/花丛误配）→ 不点击。"""
    import numpy as np

    class W:
        def __init__(self):
            self.n = 0

        def capture(self, **kwargs):
            self.n += 1
            return np.zeros((10, 10, 3), np.uint8)

    clicks = []
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda img: None)
    monkeypatch.setattr(kezhan, "_bug_candidates",
                        lambda prev, cur: [(400, 300, 40, 40)])
    monkeypatch.setattr(kezhan, "_match_duchong",
                        lambda img, boxes: (0.5, (500, 400), "tpl"))
    monkeypatch.setattr(kezhan, "BUG_IDLE_END", 0.0)
    status, clicks_n = kezhan._swat_round(W(), time.time() + 1.0)
    assert status == "idle" and clicks_n == 0
    assert clicks == []


def _patch_duchong_play(monkeypatch, states, swat_results, clicks,
                        button_pt=(680, 553)):
    """给 _play_duchong 装桩：固定面板按钮、可编排任务签名与单局结果。"""
    import numpy as np
    monkeypatch.setattr(kezhan, "_duchong_finished", None)
    monkeypatch.setattr(kezhan, "_duchong_next_try", 0.0)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_bug_capture",
                        lambda win: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda img: button_pt)
    monkeypatch.setattr(
        kezhan, "_swat_round",
        lambda win, deadline, ignore_panel_until=0.0: swat_results.pop(0))
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    state_iter = iter(states)
    monkeypatch.setattr(kezhan, "_bug_task_state",
                        lambda win: next(state_iter, states[-1]))


def test_play_duchong_stops_when_task_advanced(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA", "sigB"], [("panel", 2)], clicks)
    ok, info = kezhan._play_duchong(object())
    assert ok and "任务已推进" in info
    assert clicks == [(680, 553)]  # 只点了一次「开始」，任务推进后不再重开


def test_play_duchong_restarts_until_task_changes(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA", "sigA", "sigB"],
                        [("panel", 1), ("panel", 2)], clicks)
    ok, info = kezhan._play_duchong(object())
    assert ok and "任务已推进" in info
    assert clicks == [(680, 553), (680, 553)]  # 开始 + 重新开始
    assert "2 局" in info


def test_play_duchong_quick_panel_guard(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"] * 10,
                        [("panel", 0), ("panel", 0), ("panel", 0)], clicks)
    ok, info = kezhan._play_duchong(object())
    assert ok is False and "面板未消失" in info
    assert len(clicks) == 3  # 连续 3 次点击未进入小游戏即放弃


def test_play_duchong_skips_when_already_finished(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"], [], clicks)
    monkeypatch.setattr(kezhan, "_duchong_finished",
                        ("sigA", time.time()))
    ok, info = kezhan._play_duchong(object())
    assert ok and "不再重进" in info
    assert clicks == []


def test_play_duchong_without_panel_returns_false(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"], [], clicks, button_pt=None)
    monkeypatch.setattr(kezhan, "BUG_FIRST_PANEL_WAIT", 0.0)
    ok, info = kezhan._play_duchong(object())
    assert ok is False and "未检测到开始按钮" in info
    assert clicks == []


def test_extras_enters_duchong_on_panel_button(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(kezhan, "_duchong_next_try", 0.0)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(kezhan, "_play_duchong",
                        lambda win: calls.append(1) or (True, "拍毒虫完成"))
    monkeypatch.setattr(kezhan.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    assert kezhan._handle_follow_extras(object(), [], None) is True
    assert calls == [1]


def test_extras_duchong_failure_sets_retry_gap(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(kezhan, "_duchong_next_try", 0.0)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_play_duchong", lambda win: (False, "fail"))
    monkeypatch.setattr(kezhan.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    before = time.time()
    kezhan._handle_follow_extras(object(), [], None)
    assert kezhan._duchong_next_try >= before + kezhan.DUCHONG_RETRY_GAP - 1.0


def test_extras_duchong_skip_during_retry_gap(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(kezhan, "_duchong_next_try", time.time() + 60)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(kezhan, "_play_duchong",
                        lambda win: calls.append(1) or (True, "ok"))
    monkeypatch.setattr(kezhan.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    # 退避期内继续走后续 extras 链：给与面板/对话处理装桩
    monkeypatch.setattr(kezhan, "_handle_give_panel", lambda win, bgr: False)
    monkeypatch.setattr(kezhan, "_handle_dialog_options",
                        lambda *a, **k: False)
    kezhan._handle_follow_extras(object(), [], None)
    assert calls == []
