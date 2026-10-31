"""kezhan.py：奇遇对话框第三幕（单选项「我知道了」）识别与点击。

2026-10-31 实测：开箱奇遇第三幕「掌柜却倏然现身…封口费」无匹配键，
弹窗遗留遮挡掌柜模板致任务终止。OCR 行取自当天用户截图实测数据。
"""
from src import kezhan


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
