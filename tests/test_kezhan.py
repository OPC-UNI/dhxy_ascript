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

from src.tasks import kezhan
from src.device import task_tracker
from src.minigames import duchong, pochufengyin, yinlv


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


def test_cleanup_leftovers_clears_residual_qiyu(monkeypatch):
    """任务结束自清理钩子：次数达到上限后残留的奇遇弹窗由本钩子清掉。

    2026-11-28 用户实报：10 次客栈后接修罗，残留奇遇弹窗遮挡灵兽村
    领取按钮 → 领取失败。奇遇无标准边框，通用对话框清理检测不到。
    """
    calls = []
    monkeypatch.setattr(kezhan, "_clear_qiyu_popup",
                        lambda win: calls.append(win) or True)
    win = object()
    assert kezhan.cleanup_leftovers(win) is True
    assert calls == [win]


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
    assert pochufengyin.pochu_tick(435) == 0.0
    assert pochufengyin.pochu_tick(992) == 10.0
    assert abs(pochufengyin.pochu_tick(713.5) - 5.0) < 1e-9
    assert abs(pochufengyin.pochu_tick(492) - 1.023) < 0.01   # 7.png 实测填充边缘
    assert abs(pochufengyin.pochu_tick(908) - 8.49) < 0.01    # 7.png 实测目标带中心


def test_pochu_fill_edge_finds_right_edge():
    """合成帧：条带内画金填充段（x433~600），右边缘应≈600；
    目标带括号（白金色 H36 S61）不干扰连通段。"""
    import cv2
    import numpy as np
    frame = np.zeros((828, 1376, 3), np.uint8)          # 暗背景
    l, t, r, b = pochufengyin.POCHU_BAR_REGION
    y0, y1 = pochufengyin.POCHU_BAR_Y
    # 填充：BGR 金色（HSV H≈21 S≈133 V≈176）→ BGR (84,131,176)
    frame[y0:y1, 433:601] = (84, 131, 176)
    # 目标带括号：白金色 (164,208,215)（H36 S61 V215），位置 x879~881/934~936
    frame[y0 - 2:y1 + 2, 879:882] = (164, 208, 215)
    frame[y0 - 2:y1 + 2, 934:937] = (164, 208, 215)
    assert pochufengyin.pochu_fill_edge(frame) == 600
    # 无填充 → None
    assert pochufengyin.pochu_fill_edge(np.zeros_like(frame)) is None
    # 填充与括号间隔断开（缺口>POCHU_FILL_GAP）：连通段仍止于填充右缘
    frame2 = np.zeros((828, 1376, 3), np.uint8)
    frame2[y0:y1, 433:600] = (84, 131, 176)
    frame2[y0 - 2:y1 + 2, 700:703] = (164, 208, 215)
    assert pochufengyin.pochu_fill_edge(frame2) == 599


def test_play_pochufengyin_delegates_shared_module(monkeypatch):
    """破除封印闭环调用共享模块，标题模板/日志前缀与名称由客栈传入。

    必须开冲刺连点（mash=True）：同款「点击」窗口单击推进仅 ~0.1 刻度，
    三档拟人节奏到不了目标带（2026-11-12 实机日志 13 击 P=1.38 失败）。
    """
    seen = {}

    def _play(win, icon, prefix="客栈", label="破除封印", mash=False,
              fail_detect=False):
        seen.update(icon=icon, prefix=prefix, label=label, mash=mash,
                    fail_detect=fail_detect)
        return True, "破除封印结束"

    monkeypatch.setattr(kezhan._pochu_minigame, "play_pochufengyin", _play)
    assert kezhan._play_pochufengyin(object()) == (True, "破除封印结束")
    assert seen == {"icon": "kezhan_pochufengyin.png",
                    "prefix": "客栈", "label": "破除封印", "mash": True,
                    "fail_detect": True}


# ---- 破除封印失败重试（2026-11-20 用户规则）----
def test_pochu_fail_retry_schedule_and_reset():
    """判负 → 计数并安排重走；成功/新一轮复位。"""
    kezhan._reset_pochu_fail_state()
    kezhan._schedule_pochu_retry()
    assert kezhan._pochu_fail_streak == 1
    assert kezhan._pochu_retry_at > 0.0
    kezhan._reset_pochu_fail_state()
    assert kezhan._pochu_fail_streak == 0
    assert kezhan._pochu_retry_at == 0.0 and kezhan._pochu_retry_watch_at == 0.0


def test_pochu_fail_retry_streak_cap():
    """连续失败超上限：停止自动重试（不安排重走）。"""
    kezhan._reset_pochu_fail_state()
    for _ in range(kezhan.POCHU_FAIL_RETRY_MAX + 1):
        kezhan._schedule_pochu_retry()
    assert kezhan._pochu_fail_streak == kezhan.POCHU_FAIL_RETRY_MAX + 1
    assert kezhan._pochu_retry_at == 0.0
    kezhan._reset_pochu_fail_state()


def test_pochu_retry_closes_fail_panel_before_tracking(monkeypatch):
    """看门狗：失败面板必须先点 X 关闭（不关面板重走无效，2026-11-20
    用户规则）；失败对话在场先等；都退场后重点任务链接一次。"""
    import numpy as np

    class _Win:
        def capture(self, force_activate=False):
            return np.zeros((831, 1378, 3), dtype=np.uint8)

    win = _Win()
    calls = {"track": 0, "close": 0}
    state = {"panel": True, "fail": True, "dialog": False}

    monkeypatch.setattr(kezhan, "_pochu_panel_present",
                        lambda bgr: state["panel"])
    monkeypatch.setattr(kezhan, "_pochu_fail_panel_present",
                        lambda bgr: state["panel"] and state["fail"])
    monkeypatch.setattr(kezhan, "dialog_frame_score",
                        lambda img: 0.99 if state["dialog"] else 0.10)

    def _close(w):
        calls["close"] += 1
        return True, "失败面板已关闭"

    monkeypatch.setattr(kezhan, "_close_pochu_fail_panel", _close)

    def _track(w, title, **kw):
        calls["track"] += 1
        return True, "已点击绿字"

    monkeypatch.setattr(kezhan.task_tracker, "track_task", _track)

    kezhan._reset_pochu_fail_state()
    kezhan._pochu_armed_at = 0.0
    kezhan._pochu_retry_at = 1.0  # 已到点（time.time() 恒远大于 1）
    # 1) 失败面板在场：先关面板，不点任务追踪（面板会拦住点击）
    kezhan._pochu_retry_watch_at = 0.0
    assert kezhan._maybe_retry_pochu_fail(win) is True
    assert calls == {"track": 0, "close": 1}
    assert kezhan._pochu_retry_at > 0.0  # 面板关闭后仍待重走
    # 2) 面板已关、失败对话仍在：等剧情兜底点屏，不叠操作
    state["panel"] = False
    state["dialog"] = True
    kezhan._pochu_retry_watch_at = 0.0
    assert kezhan._maybe_retry_pochu_fail(win) is False
    assert calls == {"track": 0, "close": 1}
    # 3) 面板/对话都退场：重点任务链接一次并清除待重试
    state["dialog"] = False
    kezhan._pochu_retry_watch_at = 0.0
    assert kezhan._maybe_retry_pochu_fail(win) is True
    assert calls == {"track": 1, "close": 1}
    assert kezhan._pochu_retry_at == 0.0
    kezhan._reset_pochu_fail_state()


def test_close_pochu_fail_panel_refuses_normal_game(monkeypatch):
    """面板在场但非失败结果态：绝不点 X（正常游戏中不得误关）。"""
    import numpy as np
    clicks = []

    class _Win:
        def capture(self, force_activate=False):
            return np.zeros((831, 1378, 3), dtype=np.uint8)

    monkeypatch.setattr(kezhan, "_pochu_panel_present", lambda bgr: True)
    monkeypatch.setattr(kezhan, "_pochu_fail_panel_present", lambda bgr: False)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda w, x, y: clicks.append((x, y)))
    closed, info = kezhan._close_pochu_fail_panel(_Win())
    assert closed is False and "非失败结果态" in info
    assert clicks == []


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


# ---- 任务消失判定：面板缺席处理已统一到 workflows.follow._confirm_task_gone
#      （2026-11-12 用户规则：缺席+站定=完成、缺席+寻路中=任务仍在），
#      客栈不再传 per-task 参数 ----

# ---- 阿旺走近点按屏幕左右侧取侧（2026-11-11 用户规则） ----
class _AwangWin:
    """窗口宽 1376：中心 688，中间区 508~868（±180）。"""

    class rect:
        width = 1376
        height = 828

    def capture(self, **k):
        return None  # 默认无帧：_check_awang 的晚到对话框兜底跳过


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
    """阿旺在屏幕中间区域且点击生效：只点一次交互点，不走近。"""
    clicks, arrived = [], []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_find_awang", lambda w: (700, 400))
    monkeypatch.setattr(kezhan, "_awang_click_took_effect", lambda w: True)
    monkeypatch.setattr(kezhan, "_wait_awang_arrival",
                        lambda w: arrived.append(1))
    ok, info = kezhan._check_awang(_AwangWin())
    assert ok is True and info == "已点击阿旺"
    assert clicks == [(700, 400)]
    assert arrived == []


def test_check_awang_center_click_no_effect_walks_closer(monkeypatch):
    """屏幕中间区直接点无响应（2026-11-27 比武场）：改点朝中间一侧 90px
    走近，到位后重新定位再点交互点。"""
    clicks, arrived = [], []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_find_awang", lambda w: (700, 400))
    monkeypatch.setattr(kezhan, "_awang_click_took_effect", lambda w: False)
    monkeypatch.setattr(kezhan, "_wait_awang_arrival",
                        lambda w: arrived.append(1))
    ok, info = kezhan._check_awang(_AwangWin())
    assert ok is True and info == "已点击阿旺"
    # 700 在中心右侧 → 走近点 = 左侧 90px
    assert clicks == [(700, 400), (610, 400), (700, 400)]
    assert arrived == [1]


class _AwangVerifyWin:
    """按序返回预置帧的窗口（帧耗尽后返回 None）。"""

    class rect:
        width = 1376
        height = 828

    def __init__(self, frames):
        self._frames = list(frames)

    def capture(self, **k):
        return self._frames.pop(0) if self._frames else None


def test_awang_click_effect_dialog_hit(monkeypatch):
    """对话框弹出即生效，不再读位置。"""
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda img: 0.99)
    reads = []
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: reads.append(1) or ("洛阳城", 1, 1))
    assert kezhan._awang_click_took_effect(_AwangVerifyWin([object()])) is True
    assert reads == []


def test_awang_click_effect_walk_or_unknown(monkeypatch):
    """位置变化（人物起步）或两次都读不到位置（不确定）均按生效处理。"""
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda img: 0.1)
    for p1, p2 in [(("洛阳城", 1, 1), ("洛阳城", 1, 9)), (None, None)]:
        reads = iter([p1, p2])
        monkeypatch.setattr(kezhan.game_map, "read_map_position",
                            lambda w, img=None: next(reads))
        assert kezhan._awang_click_took_effect(
            _AwangVerifyWin([object(), object()])) is True


def test_awang_click_effect_no_move_returns_false(monkeypatch):
    """无对话且位置两次不变 → 判定点击未生效（允许改走近）。"""
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "dialog_frame_score", lambda img: 0.1)
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: ("洛阳城", 1, 1))
    assert kezhan._awang_click_took_effect(
        _AwangVerifyWin([object(), object()])) is False


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


# ---- 阿旺名字世界区 OCR（2026-11-19 参考星魂归位场景标签条带扫法） ----
class _AwangFrameWin:
    def capture(self, **k):
        import numpy as np
        return np.zeros((831, 1378, 3), np.uint8)


def _patch_awang_ocr(monkeypatch, items):
    seen = []

    def _ocr(img, bands):
        seen.append(bands)
        return items, None

    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_track_panel_region",
                        lambda img: (0, 200, 260, 600))
    monkeypatch.setattr(kezhan.game_map, "ocr_regions", _ocr)
    return seen


def test_find_awang_by_ocr_uses_bands_and_returns_name(monkeypatch):
    """名字 OCR 走两段 736 条带（区域快引擎原尺寸），短行命中返回中心。

    旧实现整帧全精度 OCR（约 2.2s、整屏杂背景参与检测）；星魂归位同款
    条带扫法对小字世界标签更稳（「阿旺。」规范化后 2 字仍命中）。
    """
    seen = _patch_awang_ocr(monkeypatch, [
        ("阿旺。", [600, 300, 660, 326], 0.97)])
    assert kezhan._find_awang_by_ocr(_AwangFrameWin()) == (630, 313)
    assert seen == [kezhan.AWANG_NAME_BANDS]


def test_find_awang_by_ocr_excludes_tracker_panel(monkeypatch):
    """追踪面板矩形内的同名文案跳过（「找阿旺。」不可点）。"""
    _patch_awang_ocr(monkeypatch, [
        ("找阿旺。", [20, 300, 100, 326], 0.99)])
    assert kezhan._find_awang_by_ocr(_AwangFrameWin()) is None


def test_find_awang_by_ocr_records_lines_for_diagnosis(monkeypatch):
    """未命中时记录世界区 OCR 行（失败日志标定数据，同沙都修罗）。"""
    _patch_awang_ocr(monkeypatch, [
        ("生日我最大", [640, 440, 730, 470], 0.99),
        ("低置信行", [10, 10, 50, 30], 0.40),
    ])
    kezhan._awang_ocr_lines = []
    assert kezhan._find_awang_by_ocr(_AwangFrameWin()) is None
    assert kezhan._awang_ocr_lines == ["生日我最大"]


# ---- 金色名字标签兜底（2026-11-24 比武场实测：金色小字 1x 时好时坏） ----
def _gold_label_frame(x=976, y=621, w=37, h=13, color=(244, 243, 122)):
    """构造含一个金色名字标签（实测阿旺标签尺寸/颜色）的 1378x831 假帧。"""
    import numpy as np
    from PIL import Image
    img = np.zeros((831, 1378, 3), np.uint8)
    img[y:y + h, x:x + w] = color
    return Image.fromarray(img)


def test_find_awang_by_gold_zooms_candidate_and_maps_back(monkeypatch):
    """金色文本块裁剪 2x 走 ocr_zoom，命中「阿旺」返回映射回全帧的中心。"""
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_track_panel_region",
                        lambda img: (0, 200, 260, 600))
    seen = []

    def _zoom(crop):
        w, h = crop.size
        seen.append((w, h))
        return [("阿旺", [0, 0, w, h], 1.0)], None

    monkeypatch.setattr(kezhan.game_map, "ocr_zoom", _zoom)
    pt = kezhan._find_awang_by_gold(_gold_label_frame())
    assert pt is not None
    # 标签中心 (994.5, 627.5)；候选块膨胀+余量后中心一致（±8px）
    assert abs(pt[0] - 994) <= 8 and abs(pt[1] - 627) <= 8
    # OCR 用的是 2x 放大裁剪
    assert seen and seen[0][0] >= 2 * kezhan.AWANG_GOLD_MIN_W


def test_find_awang_by_gold_skips_tracker_panel(monkeypatch):
    """任务追踪面板矩形内的金色块跳过（面板文案如「阿旺朝……」不可点）。"""
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_track_panel_region",
                        lambda img: (0, 200, 260, 600))
    monkeypatch.setattr(kezhan.game_map, "ocr_zoom",
                        lambda crop: ([("阿旺", [0, 0, 10, 10], 1.0)], None))
    assert kezhan._find_awang_by_gold(
        _gold_label_frame(x=100, y=300)) is None


def test_find_awang_by_ocr_band_hit_skips_gold_fallback(monkeypatch):
    """条带 OCR 命中即返回，不再走金色兜底。"""
    _patch_awang_ocr(monkeypatch, [
        ("阿旺。", [600, 300, 660, 326], 0.97)])
    calls = []
    monkeypatch.setattr(kezhan, "_find_awang_by_gold",
                        lambda img: calls.append(1))
    assert kezhan._find_awang_by_ocr(_AwangFrameWin()) == (630, 313)
    assert calls == []


def test_find_awang_by_ocr_falls_back_to_gold_label(monkeypatch):
    """条带 OCR 未命中时走金色名字标签兜底（2026-11-24 比武场漏检教训）。"""
    _patch_awang_ocr(monkeypatch, [
        ("任务追踪", [20, 210, 100, 230], 1.0)])
    seen = []
    monkeypatch.setattr(kezhan, "_find_awang_by_gold",
                        lambda img: seen.append(img) or (993, 626))
    assert kezhan._find_awang_by_ocr(_AwangFrameWin()) == (993, 626)
    assert len(seen) == 1  # 复用同一帧，不再二次截图


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


def test_walk_to_luoyang_pos_coord_nav(monkeypatch, coord_nav_enabled):
    """坐标化开关开启：客栈走位改走 map_nav 坐标点击（单落点取目标坐标）。"""
    from src.device import map_nav

    class _NavWin:
        def capture(self, force_activate=False):
            return None

    started = {}
    near_calls = {"n": 0}

    def _near(pos, targets):
        near_calls["n"] += 1
        return near_calls["n"] > 1  # 首次不在附近；寻路后判到达

    def _start(w, map_name, x, y, log_fn=None, on_map_open=None):
        started["args"] = (map_name, x, y)
        return True, "已点击", (623, 317)

    monkeypatch.setattr(kezhan, "is_in_battle", lambda w: False)
    monkeypatch.setattr(kezhan.game_map, "read_position_with_recovery",
                        lambda w: ("长安", 1, 2))
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: ("洛阳城", 223, 195))
    monkeypatch.setattr(kezhan, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_near_luoyang", _near)
    monkeypatch.setattr(map_nav, "start_navigation", _start)
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    ok, _info = kezhan._walk_to_luoyang_pos(
        _NavWin(), (623, 317), (223, 195), 10, "大理寺墙外")
    assert ok is True
    assert started["args"] == (kezhan.LUOYANG_CITY, 223, 195)


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
             for i, icon in enumerate(yinlv.YINLV_BELLS)}
    monkeypatch.setattr(yinlv, "YINLV_LOCATE_TIMEOUT", locate_timeout)
    monkeypatch.setattr(yinlv, "YINLV_GLOW_POLL", 0.0)
    monkeypatch.setattr(yinlv, "YINLV_GLOW_COOLDOWN", 0.0)
    monkeypatch.setattr(yinlv.time, "sleep", lambda s: None)
    monkeypatch.setattr(yinlv, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(yinlv, "_yinlv_locate_bells", lambda bgr: bells)
    monkeypatch.setattr(yinlv, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(yinlv, "log", lambda *a, **k: None)
    state = {"checks": 0, "glow": list(glows)}

    def fake_glow(bgr, rois):
        return state["glow"].pop(0) if state["glow"] else None

    monkeypatch.setattr(yinlv, "_yinlv_glow_index", fake_glow)

    def fake_match(bgr, icon, threshold=0.8, region=None, fallback=True):
        if icon == yinlv.YINLV_START_ICON:
            return (668, 552)
        if icon == kezhan.YINLV_WINDOW_ICON:
            state["checks"] += 1
            return (677, 262) if state["checks"] <= window_hits else None
        return None

    monkeypatch.setattr(yinlv.game_map, "match_frame_np", fake_match)
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
    assert status == yinlv.YINLV_RETRY
    assert clicks == []


def test_yinlv_glow_index_picks_golden_bell():
    """金色光晕落在哪个钟 ROI 就是哪个钟高亮；无高亮返回 None。"""
    import cv2
    import numpy as np
    bells = {icon: (100 + i * 50, 150)
             for i, icon in enumerate(yinlv.YINLV_BELLS)}
    rois = yinlv._yinlv_bell_rois(bells)
    hsv = np.zeros((300, 400, 3), np.uint8)
    cv2.circle(hsv, (250, 150), 30, (22, 200, 220), -1)  # 3 号钟金色光晕
    assert yinlv._yinlv_glow_index(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR),
                                    rois) == 3
    empty = cv2.cvtColor(np.zeros((300, 400, 3), np.uint8),
                         cv2.COLOR_HSV2BGR)
    assert yinlv._yinlv_glow_index(empty, rois) is None


def test_yinlv_window_band_from_banner_pt(monkeypatch):
    """检查条带按检测点+模板尺寸+余量推导；无检测点退回全帧常量。"""
    monkeypatch.setattr(kezhan.game_map, "template_size",
                        lambda icon: (302, 28))
    assert yinlv._yinlv_window_band((677, 262)) == (
        677 - 151 - yinlv.YINLV_BAND_MARGIN,
        262 - 14 - yinlv.YINLV_BAND_MARGIN,
        677 + 151 + yinlv.YINLV_BAND_MARGIN,
        262 + 14 + yinlv.YINLV_BAND_MARGIN)
    assert yinlv._yinlv_window_band(None) is kezhan.YINLV_WINDOW_REGION


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
        return {duchong.BUG_RESTART_ICON: (0.90, (680, 553)),
                duchong.BUG_START_ICON: (0.85, (681, 553))}

    monkeypatch.setattr(kezhan.game_map, "best_match_multi_np", fake_match)
    assert kezhan._bug_button_pt(object()) == (680, 553)
    assert seen["icons"] == (duchong.BUG_RESTART_ICON, duchong.BUG_START_ICON)
    assert seen["region"] == duchong.BUG_BUTTON_REGION


def test_bug_candidates_diff_finds_moving_blob():
    import numpy as np
    l, t, _, _ = duchong.BUG_REGION
    prev = np.zeros((831, 1378, 3), np.uint8)
    cur = prev.copy()
    cur[t + 100:t + 150, l + 200:l + 260] = 255
    boxes = duchong._bug_candidates(prev, cur)
    assert boxes == [(l + 200, t + 100, 60, 50)]


def test_match_duchong_returns_best_with_full_frame_offset(monkeypatch):
    import numpy as np

    def fake_match(crop, icons):
        assert tuple(icons) == duchong.DUCHONG_ICONS
        return {duchong.DUCHONG_ICONS[0]: (0.66, (10, 12)),
                duchong.DUCHONG_ICONS[1]: (0.90, (30, 20))}

    monkeypatch.setattr(kezhan.game_map, "best_match_multi_np", fake_match)
    l, t, _, _ = duchong.BUG_REGION
    score, pt, icon = duchong._match_duchong(
        np.zeros((831, 1378, 3), np.uint8), [(l + 100, t + 100, 40, 40)])
    assert score == 0.90
    assert icon == duchong.DUCHONG_ICONS[1]
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
    monkeypatch.setattr(duchong, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(duchong, "log", lambda *a, **k: None)
    monkeypatch.setattr(duchong.time, "sleep", lambda s: None)
    monkeypatch.setattr(duchong, "_bug_candidates",
                        lambda prev, cur: [(400, 300, 40, 40)])
    monkeypatch.setattr(duchong, "_match_duchong",
                        lambda img, boxes: (0.9, (500, 400), "tpl"))
    state = {"n": -1}

    def fake_capture(win):
        state["n"] += 1
        return np.zeros((10, 10, 3), np.uint8)

    monkeypatch.setattr(duchong, "_bug_capture", fake_capture)
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: False)
    # 前 4 帧无面板（可拍虫），第 5/6 帧连续命中面板按钮
    monkeypatch.setattr(duchong, "_bug_button_pt",
                        lambda img: (680, 553) if state["n"] >= 4 else None)
    status, clicks_n = kezhan._swat_round(object(), time.time() + 2.0)
    assert status == "panel"
    assert clicks_n == 1          # 全局点击冷却 0.8s 内只点一次
    assert clicks == [(500, 400)]


def test_swat_round_idle_returns(monkeypatch):
    import numpy as np
    monkeypatch.setattr(duchong, "_bug_capture",
                        lambda win: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(duchong, "_bug_button_pt", lambda img: None)
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: False)
    monkeypatch.setattr(duchong, "_bug_candidates", lambda prev, cur: [])
    monkeypatch.setattr(duchong, "BUG_IDLE_END", 0.0)
    monkeypatch.setattr(duchong.time, "sleep", lambda s: None)
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
    monkeypatch.setattr(duchong, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(duchong, "log", lambda *a, **k: None)
    monkeypatch.setattr(duchong.time, "sleep", lambda s: None)
    monkeypatch.setattr(duchong, "_bug_button_pt", lambda img: None)
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: False)
    monkeypatch.setattr(duchong, "_bug_candidates",
                        lambda prev, cur: [(400, 300, 40, 40)])
    monkeypatch.setattr(duchong, "_match_duchong",
                        lambda img, boxes: (0.5, (500, 400), "tpl"))
    monkeypatch.setattr(duchong, "BUG_IDLE_END", 0.0)
    status, clicks_n = kezhan._swat_round(W(), time.time() + 1.0)
    assert status == "idle" and clicks_n == 0
    assert clicks == []


def _patch_duchong_play(monkeypatch, states, swat_results, clicks,
                        button_pt=(680, 553)):
    """给 _play_duchong 装桩：固定面板按钮、可编排任务签名与单局结果。"""
    import numpy as np
    monkeypatch.setattr(duchong, "_duchong_finished", None)
    monkeypatch.setattr(duchong, "_duchong_next_try", 0.0)
    monkeypatch.setattr(duchong.time, "sleep", lambda s: None)
    monkeypatch.setattr(duchong, "log", lambda *a, **k: None)
    monkeypatch.setattr(duchong, "_bug_capture",
                        lambda win: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(duchong, "_bug_button_pt", lambda img: button_pt)
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: False)
    monkeypatch.setattr(
        duchong, "_swat_round",
        lambda win, deadline, ignore_panel_until=0.0: swat_results.pop(0))
    monkeypatch.setattr(duchong, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    state_iter = iter(states)
    monkeypatch.setattr(duchong, "_bug_task_state",
                        lambda win, task_title=None: next(state_iter, states[-1]))


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
    monkeypatch.setattr(duchong, "_duchong_finished",
                        ("sigA", time.time()))
    before = time.time()
    ok, info = kezhan._play_duchong(object())
    assert ok and "不再重进" in info
    assert clicks == []
    # 退避必须写回 minigame 模块的共享状态（任务侧 can_enter 读同一份）
    assert duchong._duchong_next_try >= \
        before + duchong.DUCHONG_RETRY_GAP - 1.0


def test_play_duchong_expired_finished_state_cleared(monkeypatch):
    """完成态 TTL 过期后必须清空，不能长期驻留在模块全局。"""
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"], [], clicks, button_pt=None)
    monkeypatch.setattr(duchong, "BUG_FIRST_PANEL_WAIT", 0.0)
    monkeypatch.setattr(
        duchong, "_duchong_finished",
        ("sigA", time.time() - duchong.DUCHONG_FINISHED_TTL - 1))
    ok, info = kezhan._play_duchong(object())
    assert ok is False and "未检测到开始按钮" in info
    assert duchong._duchong_finished is None


def test_play_duchong_without_panel_returns_false(monkeypatch):
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"], [], clicks, button_pt=None)
    monkeypatch.setattr(duchong, "BUG_FIRST_PANEL_WAIT", 0.0)
    ok, info = kezhan._play_duchong(object())
    assert ok is False and "未检测到开始按钮" in info
    assert clicks == []


def test_swat_round_dialog_ends_round(monkeypatch):
    """任务对话出现 = 小游戏完成：连续 2 次命中即返回 "dialog"，不再等面板。

    2026-11-17 用户实测：最后一局达标后游戏直接弹任务对话、面板不重现，
    旧逻辑白等 8s 面板超时才结束（对话选项延迟 12s 才被点击）。
    """
    import numpy as np
    clicks = []
    monkeypatch.setattr(duchong, "human_click",
                        lambda win, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(duchong, "log", lambda *a, **k: None)
    monkeypatch.setattr(duchong.time, "sleep", lambda s: None)
    monkeypatch.setattr(duchong, "_bug_capture",
                        lambda win: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(duchong, "_bug_button_pt", lambda img: None)
    monkeypatch.setattr(duchong, "_bug_candidates", lambda prev, cur: [])
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: True)
    monkeypatch.setattr(duchong, "BUG_DIALOG_CHECK_INTERVAL", 0.0)
    status, clicks_n = kezhan._swat_round(object(), time.time() + 5.0)
    assert status == "dialog" and clicks_n == 0
    assert clicks == []


def test_play_duchong_dialog_ends_immediately(monkeypatch):
    """面板等待期任务对话出现 → 立即按完成返回（不等 BUG_PANEL_WAIT）。"""
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"] * 10, [("panel", 3)], clicks)
    # 首轮面板按钮只在第一次轮询出现（点「开始」进入小游戏），其后消失
    seq = {"n": 0}

    def _btn(img):
        seq["n"] += 1
        return (680, 553) if seq["n"] == 1 else None

    monkeypatch.setattr(duchong, "_bug_button_pt", _btn)
    monkeypatch.setattr(duchong, "BUG_PANEL_WAIT", 30.0)  # 若等面板必超时
    monkeypatch.setattr(duchong, "BUG_DIALOG_CHECK_INTERVAL", 0.0)
    monkeypatch.setattr(duchong, "_bug_dialog_present", lambda img: True)
    ok, info = kezhan._play_duchong(object())
    assert ok and "任务对话出现" in info and "1 局" in info


def test_play_duchong_swat_dialog_status(monkeypatch):
    """_swat_round 返回 dialog → play 立即按完成返回（不再重开/等面板）。"""
    clicks = []
    _patch_duchong_play(monkeypatch, ["sigA"] * 10, [("dialog", 4)], clicks)
    ok, info = kezhan._play_duchong(object())
    assert ok and "任务对话出现" in info
    assert clicks == [(680, 553)]  # 只点了开始，不再点重新开始


def test_extras_enters_duchong_on_panel_button(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(duchong, "_duchong_next_try", 0.0)
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
    monkeypatch.setattr(duchong, "_duchong_next_try", 0.0)
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
    assert duchong._duchong_next_try >= \
        before + duchong.DUCHONG_RETRY_GAP - 1.0


def test_extras_duchong_skip_during_retry_gap(monkeypatch):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((10, 10, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: (680, 553))
    monkeypatch.setattr(duchong, "_duchong_next_try", time.time() + 60)
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


# ---- 除异觉宝小游戏（玄石探秘分支，2026-11-16 实机复现：点入口选项
# 「注入玄蕴试试看」后面板干等，从未点「开始」）----
def _patch_extras_for_chuyijuebao(monkeypatch, score, play_result, calls):
    import numpy as np
    monkeypatch.setattr(kezhan, "_kj_frame",
                        lambda win, items: np.zeros((831, 1378, 3), np.uint8))
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda bgr: False)
    monkeypatch.setattr(kezhan, "_bug_button_pt", lambda bgr: None)
    monkeypatch.setattr(kezhan, "_yinlv_armed_at", 0.0)
    monkeypatch.setattr(kezhan, "_pochu_armed_at", 0.0)
    monkeypatch.setattr(kezhan, "_chuyijuebao_last_check", 0.0)
    monkeypatch.setattr(kezhan.task_tracker, "find_task",
                        lambda tasks, title: None)
    monkeypatch.setattr(kezhan.task_tracker, "group_tasks",
                        lambda items, xmax=None: [])
    monkeypatch.setattr(kezhan.task_tracker, "panel_row_xmax",
                        lambda img: None)
    monkeypatch.setattr(kezhan.game_map, "best_match_np",
                        lambda bgr, icon, region=None: (score, (712, 720)))
    monkeypatch.setattr(kezhan, "_play_chuyijuebao",
                        lambda win: calls.append(1) or play_result)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)


def test_extras_enters_chuyijuebao_on_panel_title(monkeypatch):
    """面板标题命中 → 阻塞执行小游戏并返回已处理（修复面板干等）。"""
    calls = []
    _patch_extras_for_chuyijuebao(monkeypatch, 0.95, (True, "结束"), calls)
    assert kezhan._handle_follow_extras(object(), [], None) is True
    assert calls == [1]
    assert kezhan._chuyijuebao_last_check > 0.0


def test_extras_skips_chuyijuebao_below_threshold(monkeypatch):
    """模板分低于阈值（无面板）→ 不进入小游戏，继续后续对话处理。"""
    calls = []
    _patch_extras_for_chuyijuebao(monkeypatch, 0.5, (True, "结束"), calls)
    monkeypatch.setattr(kezhan, "_handle_give_panel", lambda win, bgr: False)
    monkeypatch.setattr(kezhan, "_handle_dialog_options",
                        lambda *a, **k: False)
    assert kezhan._handle_follow_extras(object(), [], None) is False
    assert calls == []


def test_extras_chuyijuebao_rate_limited(monkeypatch):
    """限频窗口内不重复检测（1s 内第二帧不匹配模板、不重进小游戏）。"""
    calls = []
    _patch_extras_for_chuyijuebao(monkeypatch, 0.95, (True, "结束"), calls)
    monkeypatch.setattr(kezhan, "_chuyijuebao_last_check", time.time())
    monkeypatch.setattr(kezhan, "_handle_give_panel", lambda win, bgr: False)
    monkeypatch.setattr(kezhan, "_handle_dialog_options",
                        lambda *a, **k: False)
    assert kezhan._handle_follow_extras(object(), [], None) is False
    assert calls == []


def test_try_flag_dialog_only_for_inn_destination(monkeypatch):
    """飞行旗对话框仅当目的地含洛阳客栈时点确认选项。"""
    clicked = []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(
        kezhan, "_click_green_option_fuzzy",
        lambda w, items, img_np, text: clicked.append(text) or text == "使用")

    items = [("你是否使用飞行旗前往洛阳客栈？", [500, 300, 900, 340], 0.95),
             ("使用", [600, 380, 660, 400], 0.95)]
    assert kezhan._try_flag_dialog(object(), items, None) is True
    assert clicked[:2] == ["快送", "使用"]  # 按候选顺序尝试

    clicked.clear()
    items2 = [("你是否使用飞行旗前往长安城？", [500, 300, 900, 340], 0.95)]
    assert kezhan._try_flag_dialog(object(), items2, None) is False
    assert clicked == []


def test_go_to_inn_waits_for_inn_landing(monkeypatch, coord_nav_enabled):
    """点击快送/飞行旗后按「洛阳客栈 24,7」确认到达，不提前返回成功。"""
    from src.device import map_nav

    class _Win:
        def capture(self, force_activate=False):
            return object()

    monkeypatch.setattr(map_nav, "start_navigation",
                        lambda *a, **k: (True, "已点击", (884, 600)))
    monkeypatch.setattr(kezhan, "is_in_battle", lambda w: False)
    monkeypatch.setattr(kezhan, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(kezhan, "_clear_qiyu_popup", lambda w: False)
    monkeypatch.setattr(kezhan, "close_scene_panels", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "_try_fast_travel",
                        lambda w, img: True)  # 首次即命中快送/飞行旗
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: (kezhan.KEZHAN_MAP,
                                             *kezhan.INN_SPOT))
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(map_nav, "cancel_requested", lambda: False)
    ok, info = kezhan._go_to_inn(_Win())
    assert ok is True and "24,7" in info


def test_try_fast_travel_handles_waypoint_menu(monkeypatch):
    """玩家路标/飞行旗菜单（你要到洛阳客栈(24,7)去吗？/快送我去）可处理。"""
    items = [("你要到洛阳客栈(24,7)去吗？", [450, 370, 930, 395], 0.99),
             ("快送我去", [430, 400, 520, 425], 0.99),
             ("重做路标（将路标修改为当前站立的坐标）",
              [430, 430, 900, 455], 0.99)]
    monkeypatch.setattr(kezhan.game_map, "ocr_regions",
                        lambda img, regions: (list(items), None))
    clicked = []
    monkeypatch.setattr(
        kezhan, "_click_green_option_once",
        lambda w, it, img_np, text, prefix="": clicked.append(text) or True)
    assert kezhan._try_fast_travel(object(), object()) is True
    assert clicked == [kezhan.FAST_DIALOG_OPTION]




# ---- 掌柜被人物遮挡的走开拉开（2026-11-28 用户反馈）----
def _walk_win():
    class _WalkWin:
        def capture(self, force_activate=False):
            return object()
    return _WalkWin()


def test_find_zhanggui_walks_aside_when_blocked(monkeypatch):
    """初始/奇遇/对话框/兜底点击均未命中 → 点远处空地走开重扫命中。"""
    clicks = []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan, "_zhanggui_scan", lambda w: None)
    monkeypatch.setattr(kezhan, "_clear_qiyu_popup", lambda w: False)
    monkeypatch.setattr(kezhan, "clear_blocking_dialog",
                        lambda w, prefix="": (False, False))
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: (kezhan.KEZHAN_MAP, 18, 12))
    monkeypatch.setattr(kezhan, "_wait_zhanggui_walk",
                        lambda w, start: (413, 539))
    assert kezhan._find_zhanggui(_walk_win()) == (413, 539)
    # 兜底点 + 第一个走开步点
    assert clicks == [kezhan.ZHANGGUI_FALLBACK_CLICK_POS,
                      kezhan.ZHANGGUI_WALK_STEPS[0]]


def test_walk_aside_tries_next_step_after_settle_without_hit(monkeypatch):
    """第一步走完仍未命中（None）→ 换第二个步点重扫命中。"""
    clicks = []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: (kezhan.KEZHAN_MAP, 18, 12))
    results = iter([None, (413, 539)])
    monkeypatch.setattr(kezhan, "_wait_zhanggui_walk",
                        lambda w, start: next(results))
    assert kezhan._walk_aside_reveal_zhanggui(_walk_win()) == (413, 539)
    assert clicks == list(kezhan.ZHANGGUI_WALK_STEPS)


def test_walk_aside_skips_when_not_at_inn(monkeypatch):
    """场景锚点不在客栈：不点步点（防步点落到出口/世界），直接返回 None。"""
    clicks = []
    monkeypatch.setattr(kezhan, "log", lambda *a, **k: None)
    monkeypatch.setattr(kezhan, "human_click",
                        lambda w, x, y, **k: clicks.append((x, y)))
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: ("洛阳城", 381, 18))
    assert kezhan._walk_aside_reveal_zhanggui(_walk_win()) is None
    assert clicks == []


def _walk_clock(monkeypatch):
    clock = iter(range(100))
    monkeypatch.setattr(kezhan.time, "time", lambda: float(next(clock)))
    monkeypatch.setattr(kezhan.time, "sleep", lambda s: None)


def test_wait_zhanggui_walk_stops_after_settle(monkeypatch):
    """走位站定（位置先变化再连续相同）仍未命中 → 返回 None 换方向。"""
    _walk_clock(monkeypatch)
    monkeypatch.setattr(kezhan, "_zhanggui_on_frame", lambda img: None)
    positions = iter([(kezhan.KEZHAN_MAP, 20, 12),
                      (kezhan.KEZHAN_MAP, 22, 11),
                      (kezhan.KEZHAN_MAP, 22, 11),
                      (kezhan.KEZHAN_MAP, 22, 11)])
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: next(positions))
    start = (kezhan.KEZHAN_MAP, 18, 12)
    assert kezhan._wait_zhanggui_walk(_walk_win(), start) is None


def test_wait_zhanggui_walk_returns_match_mid_walk(monkeypatch):
    """走位途中某一帧即命中 → 立即返回该坐标（不必等站定）。"""
    _walk_clock(monkeypatch)
    frames = iter([None])
    monkeypatch.setattr(
        kezhan, "_zhanggui_on_frame",
        lambda img: next(frames, (413, 539)))
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: (kezhan.KEZHAN_MAP, 22, 11))
    start = (kezhan.KEZHAN_MAP, 18, 12)
    assert kezhan._wait_zhanggui_walk(_walk_win(), start) == (413, 539)


def test_at_inn_never_presses_alt_c(monkeypatch):
    """洛阳客栈是室内场景不能飞行（2026-11-28 用户规则）：_at_inn 只用
    普通位置读取，不得触发 Alt+C 恢复。"""
    monkeypatch.setattr(kezhan.game_map, "read_map_position",
                        lambda w, img=None: (kezhan.KEZHAN_MAP, 24, 7))
    recovered = []
    monkeypatch.setattr(kezhan.game_map, "read_position_with_recovery",
                        lambda w: recovered.append(w))
    assert kezhan._at_inn(object()) is True
    assert recovered == []
