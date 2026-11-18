#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把新版本发布通知发到 Telegram 群（GitHub Actions 发布步骤调用）。

由 build-exe.yml 的 notify job 在 build（含 GitHub Release 发布）成功后
执行；更新日志直接取 Release 正文（release_notes.md），与面板更新日志、
GitHub Release 页面保持一致。

用法（仓库根运行）：

    set TELEGRAM_BOT_TOKEN=123456:ABC...
    set TELEGRAM_CHAT_ID=-1001234567890
    python scripts/notify_telegram.py \
      --version v2.13.2 \
      --notes-file release_notes.md

通知只引导用户在面板「系统」页点「检查更新」升级，不附 GitHub Release
或下载链接（用户要求，避免绕过面板更新链路）。

退出码：
  0 = 发送成功，或未配置 Bot Token / chat_id（打印 GitHub 警告并跳过）；
  非 0 = 发送失败（网络、Token、chat_id 或群权限问题）。
"""
import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Telegram 单条消息上限 4096 个 UTF-16 字符，留一点余量
TELEGRAM_MAX_LEN = 4096
MESSAGE_MAX_LEN = 4000


def _tg_len(text: str) -> int:
    """Telegram 的字符计数口径（UTF-16 码元；emoji 计 2）。"""
    return len(text.encode("utf-16-le")) // 2


def clean_notes(markdown: str) -> str:
    """把 Release 正文的 Markdown 轻量清成纯文本。

    只做对通知可读性有意义的替换，不解析 HTML 实体，避免 Telegram
    因格式错误拒收（400 can't parse entities）：
    - 去掉标题标记（# / ## / ###）；
    - 去掉粗体标记 **；
    - [文字](链接) → 文字（链接）；
    - 行内代码 `x` → x；
    - 连续空行折叠为一段。
    """
    if not markdown:
        return ""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for line in text.split("\n"):
        # 流水线会在 Release 正文末尾附「完整对比: <GitHub 链接>」；
        # 群通知不引导用户去 GitHub，统一走面板检查更新，故整行移除。
        if re.match(r"^完整对比[:：]", line.strip()):
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+", "", line)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1（\2）", line)
        line = re.sub(r"`([^`\n]+)`", r"\1", line)
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def build_message(version: str, notes: str,
                  max_len: int = MESSAGE_MAX_LEN) -> str:
    """拼通知正文；超长时截断更新日志。

    不附 GitHub Release / 下载链接：统一引导用户在面板「系统」页
    点「检查更新」升级，避免绕过面板更新链路。
    """
    header = f"🪶 啄木鸟 {version} 已发布\n\n"
    footer = "\n\n🔁 升级请打开面板「系统」页点「检查更新」（客户端每天自动检查一次）。"
    body = clean_notes(notes) or "（本次发版未提供更新日志）"
    full = header + body + footer
    if _tg_len(full) <= max_len:
        return full

    suffix = "\n…（完整更新日志见面板更新提示）"
    minimal = header + suffix + footer
    if _tg_len(minimal) > max_len:
        raise ValueError(
            f"max_len={max_len} 连标题与 Release 链接都放不下")
    low, high = 0, len(body)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = header + body[:mid].rstrip() + suffix + footer
        if _tg_len(candidate) <= max_len:
            low = mid
        else:
            high = mid - 1
    return header + body[:low].rstrip() + suffix + footer


def send_message(token: str, chat_id: str, text: str,
                 timeout: int = 30) -> dict:
    """调用 Telegram Bot API；失败抛 RuntimeError（不打印 Token）。"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(
            f"Telegram API 返回 HTTP {exc.code}：{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 Telegram API：{exc.reason}") from exc
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API 返回失败：{data}")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="发送新版本发布通知到 Telegram 群")
    parser.add_argument("--version", required=True, help="版本号，如 v2.13.2")
    parser.add_argument("--notes-file", required=True,
                        help="Release 正文（Markdown）文件路径")
    parser.add_argument("--max-len", type=int, default=MESSAGE_MAX_LEN,
                        help=f"消息长度上限（Telegram 4096，默认 {MESSAGE_MAX_LEN}）")
    args = parser.parse_args(argv)

    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("::warning title=Telegram 通知跳过::未配置 "
              "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 仓库 Secrets")
        return 0

    notes_path = pathlib.Path(args.notes_file)
    if notes_path.exists():
        notes = notes_path.read_text(encoding="utf-8")
    else:
        print(f"::warning title=更新日志缺失::{notes_path} 不存在，通知中将使用占位文案")
        notes = ""
    if notes.strip().lower() == "null":  # gh api --jq 对空正文输出 null
        notes = ""

    message = build_message(args.version, notes, args.max_len)
    result = send_message(token, chat_id, message)
    message_id = (result.get("result") or {}).get("message_id")
    print(f"Telegram 通知已发送：{args.version}"
          f"（{_tg_len(message)} 字符，message_id={message_id}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
