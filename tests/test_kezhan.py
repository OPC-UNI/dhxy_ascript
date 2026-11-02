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
