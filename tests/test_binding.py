"""窗口绑定短文案测试（从 core.settings 拆出的 device.binding）。"""
from types import SimpleNamespace

from src.device import binding


def _win(title):
    return SimpleNamespace(title=title)


def test_short_label_parses_server_and_char():
    title = "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）"
    assert binding._short_label_from_title(title) == "一生所爱长相思"


def test_short_label_without_id_returns_none():
    assert binding._short_label_from_title("大话西游2经典版 - 一生所爱") is None
    assert binding._short_label_from_title("") is None
    assert binding._short_label_from_title(None) is None


def test_short_label_pure_id_returns_none():
    """旧版纯 ID 绑定没有角色名：页面按「ID：xxx」展示。"""
    assert binding._short_label_from_title("（ID：12345）") is None


def test_short_label_unexpected_format_falls_back_to_title():
    """标题格式意外但含 ID：回退完整标题，超长截断。"""
    assert binding._short_label_from_title("某某（ID：1）") == "某某（ID：1）"
    long_title = "某" * 30 + "（ID：1）"
    assert binding._short_label_from_title(long_title) == "某" * 19 + "…"


def test_bound_short_label_prefers_live_title(monkeypatch):
    fake = SimpleNamespace(
        find_main_window=lambda: _win(
            "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）"),
        bound_window_id=lambda: {"title": "不应使用的绑定记录"},
    )
    monkeypatch.setattr(binding, "game", fake)
    assert binding.bound_short_label() == "一生所爱长相思"


def test_bound_short_label_falls_back_to_binding(monkeypatch):
    """窗口在登录界面（标题无 ID）时回退绑定记录。"""
    fake = SimpleNamespace(
        find_main_window=lambda: _win("大话西游2经典版 - 登录"),
        bound_window_id=lambda: {
            "title": "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）"},
    )
    monkeypatch.setattr(binding, "game", fake)
    assert binding.bound_short_label() == "一生所爱长相思"

    fake.bound_window_id = lambda: (
        "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）")
    assert binding.bound_short_label() == "一生所爱长相思"


def test_bound_short_label_no_window_no_binding(monkeypatch):
    fake = SimpleNamespace(find_main_window=lambda: None,
                           bound_window_id=lambda: None)
    monkeypatch.setattr(binding, "game", fake)
    assert binding.bound_short_label() is None


def test_bound_label_from_binding_dict_and_string(monkeypatch):
    fake = SimpleNamespace(bound_window_id=lambda: {
        "title": "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）"})
    monkeypatch.setattr(binding, "game", fake)
    assert binding.bound_label_from_binding() == "一生所爱长相思"

    fake.bound_window_id = lambda: (
        "大话西游2经典版 - 一生所爱 - 长相思（ID：12345）")
    assert binding.bound_label_from_binding() == "一生所爱长相思"

    fake.bound_window_id = lambda: None
    assert binding.bound_label_from_binding() is None
