"""dialog_common.py 与 app_utils.norm_text：对话文案匹配（原文不"纠正"）。"""
from src import dialog_common
from src.app_utils import norm_text


def _item(txt, x=500, y=400, score=0.95):
    return (txt, [x, y, x + 100, y + 30], score)


def test_norm_text_strips_punct():
    assert norm_text("看打！，「」；：。") == "看打"


def test_dialog_text_present_fuzzy_tolerance():
    """文案检测为模糊匹配：抗 OCR 单字误读（设计行为）；无关文案不命中。

    逐字精确匹配（选项文案不"纠正"用字）是选项定位路径的契约
    （_find_fight_option 类 norm+精确子串），与本函数无关。
    """
    items = [_item("幸苦方丈，多亏了您")]
    assert dialog_common._dialog_text_present(items, "幸苦方丈")
    assert dialog_common._dialog_text_present(items, "辛苦方丈")  # 1 字误读容差
    assert not dialog_common._dialog_text_present(items, "道长请留步")


def test_dialog_text_present_joined_line():
    """跨行拆框拼接：文案被 OCR 拆成两行时，全文拼接可命中。"""
    items = [_item("各位少侠"), _item("请留步听我说")]
    assert dialog_common._dialog_text_present(items, "请留步听我说")


def test_norm_dialog_present_ignores_punct():
    items = [_item("我可要走了；你，留步！")]
    assert dialog_common._norm_dialog_present(items, "我可要走了你留步")
