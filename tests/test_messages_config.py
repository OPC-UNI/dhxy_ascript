"""消息服务全局配置：默认值/深合并/掩码/校验。"""
import pytest

from src.core import settings


@pytest.fixture
def store(monkeypatch):
    data = {}

    def _read(key, default=None):
        return data.get(key, default)

    def _update(key, fn):
        data[key] = fn(data.get(key))

    monkeypatch.setattr(settings.app_utils, "kv_read_fresh", _read)
    monkeypatch.setattr(settings.app_utils, "kv_update_merged", _update)
    return data


def _telegram(**overrides):
    base = {"enabled": True, "token": "123:ABCdef456",
            "chat_ids": ["42"], "proxy": "", "poll_timeout": 20,
            "photo_original": False}
    base.update(overrides)
    return base


def test_load_defaults(store):
    cfg = settings.load_message_config()
    assert cfg["enabled"] is False
    assert cfg["notify_online"] is False
    assert cfg["channels"] == {}
    assert all(cfg["events"].values())


def test_save_partial_keeps_unknown_keys(store):
    store[settings.MESSAGE_SERVICE_KEY] = {
        "future": {"x": 1},
        "channels": {"wecom": {"token": "keep"}},
    }
    result = settings.save_message_config({"enabled": True})
    assert result["status"] == "success"
    merged = result["config"]
    assert merged["enabled"] is True
    assert merged["future"] == {"x": 1}
    assert merged["channels"]["wecom"] == {"token": "keep"}


def test_mask_roundtrip_keeps_token(store):
    settings.save_message_config({"channels": {"telegram": _telegram()}})
    raw = settings.load_message_config()
    masked = settings.mask_message_config(raw)
    shown = masked["channels"]["telegram"]["token"]
    assert shown.startswith("****")
    assert shown.endswith("f456")
    assert "123:ABCdef456" not in shown

    # UI 回写掩码：不覆盖真实 token
    result = settings.save_message_config(
        {"channels": {"telegram": {"token": shown}}})
    assert result["status"] == "success"
    assert result["config"]["channels"]["telegram"]["token"] == "123:ABCdef456"


def test_new_token_and_clear(store):
    settings.save_message_config({"channels": {"telegram": _telegram()}})
    result = settings.save_message_config(
        {"channels": {"telegram": {"token": "999:ZZZzzz"}}})
    assert result["config"]["channels"]["telegram"]["token"] == "999:ZZZzzz"
    result = settings.save_message_config(
        {"channels": {"telegram": {"token": ""}}})
    assert result["config"]["channels"]["telegram"]["token"] == ""


def test_chat_ids_normalize(store):
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(chat_ids="1\n2,2，-100")}})
    assert result["status"] == "success"
    assert result["config"]["channels"]["telegram"]["chat_ids"] == \
        ["1", "2", "-100"]
    # 非法值回滚
    bad = settings.save_message_config(
        {"channels": {"telegram": _telegram(chat_ids=["abc"])}})
    assert bad["status"] == "error"
    assert settings.load_message_config()["channels"]["telegram"]["chat_ids"] \
        == ["1", "2", "-100"]


def test_proxy_normalize(store):
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(proxy="127.0.0.1:7890")}})
    assert result["config"]["channels"]["telegram"]["proxy"] == \
        "http://127.0.0.1:7890"
    bad = settings.save_message_config(
        {"channels": {"telegram": _telegram(proxy="socks5://127.0.0.1:1080")}})
    assert bad["status"] == "error"


def test_poll_timeout_clamped(store):
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(poll_timeout=1)}})
    assert result["config"]["channels"]["telegram"]["poll_timeout"] == 5
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(poll_timeout=99)}})
    assert result["config"]["channels"]["telegram"]["poll_timeout"] == 50
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(poll_timeout="oops")}})
    assert result["config"]["channels"]["telegram"]["poll_timeout"] == 20


def test_token_whitespace_rejected(store):
    result = settings.save_message_config(
        {"channels": {"telegram": _telegram(token="123:abc def")}})
    assert result["status"] == "error"


def test_events_merge_and_unknown_preserved(store):
    result = settings.save_message_config(
        {"events": {"task_start": False, "future_event": True}})
    events = result["config"]["events"]
    assert events["task_start"] is False
    assert events["future_event"] is True
    assert events["task_end"] is True


def test_merge_message_config_does_not_write(store):
    settings.save_message_config({"channels": {"telegram": _telegram()}})
    before = store[settings.MESSAGE_SERVICE_KEY]
    merged = settings.merge_message_config(
        {"channels": {"telegram": {"token": "999:NEW", "enabled": False}}})
    assert merged["channels"]["telegram"]["token"] == "999:NEW"
    assert store[settings.MESSAGE_SERVICE_KEY] is before


def test_message_service_not_in_schemes():
    from src.core import schemes
    assert settings.MESSAGE_SERVICE_KEY not in schemes._settings_spec()


def test_telegram_spec_declares_health_fields():
    from src.app.messages import registry
    spec = registry.TELEGRAM_SPEC.to_dict()
    keys = [field["key"] for field in spec["health_fields"]]
    assert "last_poll_ok" in keys
    assert "last_error" in keys
    assert spec["health_fields"][0]["label"] == "最近轮询成功"


def test_telegram_fields_are_not_compact():
    """2026-11-17 用户要求：长轮询秒数 / 发送原图各占一行，不再合并。"""
    from src.app.messages import registry
    fields = {field.key: field for field in registry.TELEGRAM_SPEC.fields}
    assert fields["poll_timeout"].compact is False
    assert fields["photo_original"].compact is False
    assert all(field.compact is False
               for field in registry.TELEGRAM_SPEC.fields)
