"""发布通知脚本测试：消息拼装、Markdown 清理、超长截断、未配置跳过。

不触碰网络与 Telegram API（send_message 仅由 main 在配置齐全时调用）。
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "notify_telegram", ROOT / "scripts" / "notify_telegram.py")
assert _SPEC and _SPEC.loader
notify_telegram = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(notify_telegram)

def test_build_message_contains_version_and_notes():
    message = notify_telegram.build_message(
        "v2.13.2", "- 修复领取误判\n- 新增客栈小游戏")

    assert "啄木鸟 v2.13.2 已发布" in message
    assert "修复领取误判" in message
    assert "新增客栈小游戏" in message
    assert "检查更新" in message


def test_build_message_has_no_release_link():
    message = notify_telegram.build_message("v1.0.0", "- 修复 A")
    assert "releases/tag" not in message
    assert "github.com" not in message


def test_clean_notes_removes_markdown_markers():
    raw = (
        "# v2.13.2\n"
        "\n"
        "## 变更\n"
        "- **修复** [详情](https://example.com/x)\n"
        "\n"
        "\n"
        "> 提示 `code`\n"
        "\n"
        "完整对比: https://github.com/example/compare/a...b\n"
    )
    cleaned = notify_telegram.clean_notes(raw)

    assert "##" not in cleaned
    assert "**" not in cleaned
    assert "`" not in cleaned
    assert "v2.13.2\n\n变更" in cleaned
    assert "修复 详情（https://example.com/x）" in cleaned
    assert "\n\n\n" not in cleaned
    # 群通知不引导去 GitHub：流水线附加的完整对比行整行移除
    assert "完整对比" not in cleaned
    assert "github.com/example/compare" not in cleaned


def test_build_message_truncates_long_notes():
    notes = "- " + "很长的更新说明" * 2000
    message = notify_telegram.build_message("v9.9.9", notes, max_len=600)

    # Telegram 按 UTF-16 计数，截断后必须不超上限
    assert notify_telegram._tg_len(message) <= 600
    assert "…（完整更新日志见面板更新提示）" in message
    assert "检查更新" in message


def test_build_message_placeholder_when_notes_empty():
    message = notify_telegram.build_message("v1.0.0", "")
    assert "（本次发版未提供更新日志）" in message


def test_main_skips_without_secrets(monkeypatch, tmp_path, capsys):
    notes = tmp_path / "release_notes.md"
    notes.write_text("- 测试更新", encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    rc = notify_telegram.main([
        "--version", "v1.0.0",
        "--notes-file", str(notes),
    ])

    assert rc == 0
    assert "跳过" in capsys.readouterr().out


def test_main_sends_message_with_secrets(monkeypatch, tmp_path):
    notes = tmp_path / "release_notes.md"
    notes.write_text("## 变更\n- 修复 A", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    sent = {}

    def fake_send(token, chat_id, text):
        sent.update(token=token, chat_id=chat_id, text=text)
        return {"ok": True, "result": {"message_id": 42}}

    monkeypatch.setattr(notify_telegram, "send_message", fake_send)
    rc = notify_telegram.main([
        "--version", "v1.0.0",
        "--notes-file", str(notes),
    ])

    assert rc == 0
    assert sent["token"] == "token"
    assert sent["chat_id"] == "-100123"
    assert "修复 A" in sent["text"]
    assert "检查更新" in sent["text"]


def test_send_message_posts_to_bot_api(monkeypatch):
    calls = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true, "result": {}}'

    def fake_urlopen(req, timeout):
        calls["url"] = req.full_url
        calls["payload"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(notify_telegram.urllib.request, "urlopen", fake_urlopen)
    notify_telegram.send_message("TOKEN", "-100123", "hello")

    assert calls["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert calls["payload"]["chat_id"] == "-100123"
    assert calls["payload"]["text"] == "hello"
    assert calls["payload"]["disable_web_page_preview"] is True


def test_send_message_raises_on_api_error(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": false, "description": "chat not found"}'

    monkeypatch.setattr(
        notify_telegram.urllib.request, "urlopen", lambda req, timeout: _Resp())

    with pytest.raises(RuntimeError, match="chat not found"):
        notify_telegram.send_message("TOKEN", "-100123", "hello")
