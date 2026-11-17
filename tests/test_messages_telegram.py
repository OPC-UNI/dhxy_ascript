"""Telegram 适配器：请求构造/错误分类/重放防护/验证/发送（全部无网络）。"""
import time
from io import BytesIO

from src.app.messages.channel_telegram import (TelegramAdapter, TelegramError,
                                               _encode, classify_poll_error)


def _adapter(**overrides):
    config = {"token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ",
              "chat_ids": ["42"], "proxy": "", "poll_timeout": 20,
              "photo_original": False}
    config.update(overrides)
    return TelegramAdapter(config)


def test_encode_form_and_multipart():
    data, content_type = _encode({"a": "1", "b": "2"})
    assert data == b"a=1&b=2"
    assert content_type == "application/x-www-form-urlencoded"
    data, content_type = _encode({"chat_id": "42"},
                                 {"photo": ("x.jpg", b"PNGDATA")})
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="chat_id"' in data
    assert b'filename="x.jpg"' in data
    assert b"PNGDATA" in data


def test_error_public_redacts_token():
    error = TelegramError(
        401, "Unauthorized 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    text = error.public()
    assert "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in text
    assert "<token>" in text


def test_classify_poll_error_table():
    assert classify_poll_error(TelegramError(401, "Unauthorized"))["stop"]
    assert classify_poll_error(TelegramError(409, "Conflict"))["state"] == \
        "conflict"
    rate = classify_poll_error(TelegramError(429, "Too Many", retry_after=7))
    assert rate["delay"] == 7
    webhook = classify_poll_error(TelegramError(
        400, "Conflict: can't use getUpdates method while webhook is active"))
    assert webhook["stop"] is True
    network = classify_poll_error(TelegramError(0, "network", network=True))
    assert network["kind"] == "network"
    other = classify_poll_error(TelegramError(400, "bad request"))
    assert other["state"] == "error" and other["delay"] == 60


def test_prime_offset_uses_last_update():
    adapter = _adapter()
    calls = []

    def fake_api(method, params=None, files=None, timeout=15):
        calls.append((method, params))
        return [{"update_id": 41}]

    adapter._api = fake_api
    assert adapter._prime_offset() is True
    assert adapter._offset == 42
    assert calls[0][0] == "getUpdates"
    assert calls[0][1]["offset"] == -1
    assert calls[0][1]["timeout"] == 0


def test_process_updates_filters_and_dispatches():
    adapter = _adapter()
    adapter._started_at = time.time()
    now = int(time.time())
    commands = []
    updates = [
        {"update_id": 1, "message": {"text": "/status", "date": now,
                                     "chat": {"id": 42},
                                     "from": {"id": 7, "is_bot": False}}},
        {"update_id": 2, "message": {"text": "/task", "date": now - 1000,
                                     "chat": {"id": 42},
                                     "from": {"id": 7}}},
        {"update_id": 3, "message": {"text": "/help", "date": now,
                                     "chat": {"id": 42},
                                     "from": {"id": 7, "is_bot": True}}},
        {"update_id": 4, "message": {"date": now, "chat": {"id": 42}}},
    ]
    adapter._process_updates(updates, commands.append)
    assert len(commands) == 1
    assert commands[0].chat_id == "42"
    assert commands[0].text == "/status"
    assert adapter._offset == 5


def test_verify_order_and_no_get_updates():
    adapter = _adapter()
    calls = []

    def fake_api(method, params=None, files=None, timeout=15):
        calls.append(method)
        if method == "getMe":
            return {"username": "my_bot", "id": 1}
        if method == "sendMessage":
            return {"message_id": 7}
        raise AssertionError(method)

    adapter._api = fake_api
    steps = []
    report = adapter.verify(step_cb=steps.append)
    assert report.ok is True
    assert [step.key for step in report.steps] == \
        ["config", "get_me", "send_message"]
    assert len(steps) == 3
    assert calls == ["getMe", "sendMessage"]
    assert "getUpdates" not in calls


def test_verify_config_failure_has_all_steps():
    adapter = _adapter(token="", chat_ids=[])
    report = adapter.verify()
    assert report.ok is False
    assert [step.key for step in report.steps] == \
        ["config", "get_me", "send_message"]
    assert report.steps[1].detail == "未执行"


def test_send_text_splits_long_text():
    adapter = _adapter()
    calls = []

    def fake_api(method, params=None, files=None, timeout=15):
        calls.append((method, params))
        return {"message_id": 1}

    adapter._api = fake_api
    result = adapter.send_text("42", "a" * 4500)
    assert result.ok is True
    assert len(calls) == 2
    assert all(call[0] == "sendMessage" for call in calls)


def _png_bytes():
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (20, 10), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_send_photo_compresses_to_jpeg():
    adapter = _adapter()
    calls = []

    def fake_api(method, params=None, files=None, timeout=15):
        calls.append((method, files))
        return {"message_id": 1}

    adapter._api = fake_api
    result = adapter.send_photo("42", _png_bytes(), "cap")
    assert result.ok is True
    method, files = calls[0]
    assert method == "sendPhoto"
    assert files["photo"][1][:2] == b"\xff\xd8"


def test_send_photo_original_uses_document():
    adapter = _adapter(photo_original=True)
    calls = []

    def fake_api(method, params=None, files=None, timeout=15):
        calls.append((method, files))
        return {"message_id": 1}

    adapter._api = fake_api
    adapter.send_photo("42", _png_bytes(), "cap")
    assert calls[0][0] == "sendDocument"
    assert "document" in calls[0][1]


def test_proxy_handler_and_state():
    adapter = _adapter(proxy="http://127.0.0.1:7890")
    assert len(adapter._proxy_handlers()) == 1
    adapter._set_state("polling")
    state = adapter.state()
    assert state["state"] == "polling"
    assert state["state_text"] == "轮询中"
    assert state["poll_timeout"] == 20
