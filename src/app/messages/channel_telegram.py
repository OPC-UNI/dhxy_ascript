"""Telegram Bot API 通道：getUpdates 长轮询 + 文本/图片发送 + 一键验证。

只用标准库 urllib（与 updater.py 一致），不新增依赖。所有日志/状态文案
经 TelegramError.public() 脱敏，绝不输出含 token 的 URL。
"""
import json
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from io import BytesIO

from .channel_base import (ChannelAdapter, InboundCommand, SendResult,
                           VerifyReport, VerifyStep, split_text)
from .registry import TELEGRAM_SPEC

API_BASE = "https://api.telegram.org"
USER_AGENT = "Zhuomuniao-MessageService/1.0"
MAX_TEXT = 4000
CAPTION_MAX = 1000
PHOTO_MAX_SIDE = 1920
PHOTO_MAX_BYTES = 4_500_000
STARTUP_SKEW_TOLERANCE = 5.0
NETWORK_BACKOFF = (1, 2, 4, 8, 16, 30, 60)
CONFLICT_BACKOFF = (10, 20, 40, 60)
STATE_TEXT = {
    "idle": "未启动",
    "connecting": "连接中",
    "polling": "轮询中",
    "not_configured": "配置不完整",
    "auth_error": "Token 无效",
    "conflict": "被其它实例占用",
    "webhook_conflict": "Webhook 冲突",
    "rate_limited": "被限流",
    "network_error": "网络异常",
    "error": "异常",
    "stopped": "已停止",
}
_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b")


class TelegramError(Exception):
    def __init__(self, code, description, retry_after=0.0, network=False):
        super().__init__(str(description))
        self.code = int(code or 0)
        self.description = str(description or "")
        self.retry_after = float(retry_after or 0.0)
        self.network = bool(network)

    def public(self):
        """脱敏文案（不含 token；截断防日志膨胀）。"""
        return _TOKEN_RE.sub("<token>", self.description)[:300] or "未知错误"


def _parse_json(raw):
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _encode(params, files=None):
    """表单编码或 multipart 编码；返回 (bytes, content_type)。"""
    if not files:
        return (urllib.parse.urlencode(params).encode("utf-8"),
                "application/x-www-form-urlencoded")
    boundary = "----Zhuomuniao" + uuid.uuid4().hex
    body = bytearray()
    for key, value in (params or {}).items():
        body.extend(
            (f"--{boundary}\r\nContent-Disposition: form-data; "
             f"name=\"{key}\"\r\n\r\n{value}\r\n").encode("utf-8"))
    for key, (filename, data) in files.items():
        body.extend(
            (f"--{boundary}\r\nContent-Disposition: form-data; "
             f"name=\"{key}\"; filename=\"{filename}\"\r\n"
             f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8"))
        body.extend(data)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def classify_poll_error(error):
    """轮询错误 → {state, kind, delay, stop}（kind: network/conflict/fixed/stop）。"""
    if error.network or error.code == 0:
        return {"state": "network_error", "kind": "network", "delay": 0.0,
                "stop": False}
    if error.code in (401, 403, 404):
        return {"state": "auth_error", "kind": "stop", "delay": 0.0,
                "stop": True}
    if error.code == 409:
        return {"state": "conflict", "kind": "conflict", "delay": 0.0,
                "stop": False}
    if error.code == 429:
        return {"state": "rate_limited", "kind": "fixed",
                "delay": min(max(error.retry_after, 1.0), 60.0), "stop": False}
    desc = error.description.lower()
    if error.code == 400 and "webhook" in desc and "getupdates" in desc:
        return {"state": "webhook_conflict", "kind": "stop", "delay": 0.0,
                "stop": True}
    if error.code == 400 and ("unauthorized" in desc or "invalid token" in desc):
        return {"state": "auth_error", "kind": "stop", "delay": 0.0,
                "stop": True}
    return {"state": "error", "kind": "fixed", "delay": 60.0, "stop": False}


def _jitter(seconds):
    return max(0.5, float(seconds) * random.uniform(0.8, 1.2))


class TelegramAdapter(ChannelAdapter):
    spec = TELEGRAM_SPEC

    def __init__(self, config, log=None):
        super().__init__(config, log)
        self._token = ""
        self._chat_ids = []
        self._proxy = ""
        self._poll_timeout = 20
        self._photo_original = False
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._offset = 0
        self._started_at = 0.0
        self._state = "idle"
        self._last_error = ""
        self._last_error_ts = 0.0
        self._last_poll_ok = 0.0
        self._username = ""
        self._bot_id = None
        self.configure(config)

    def configure(self, config):
        super().configure(config)
        cfg = self._config
        self._token = str(cfg.get("token") or "").strip()
        chat_ids = cfg.get("chat_ids") or []
        if isinstance(chat_ids, str):
            chat_ids = re.split(r"[\r\n,，]+", chat_ids)
        self._chat_ids = [str(x).strip() for x in chat_ids if str(x).strip()]
        self._proxy = str(cfg.get("proxy") or "").strip()
        try:
            self._poll_timeout = max(5, min(50, int(cfg.get("poll_timeout") or 20)))
        except (TypeError, ValueError):
            self._poll_timeout = 20
        self._photo_original = bool(cfg.get("photo_original"))

    # ---- 生命周期 ----

    def start(self, on_command, on_health):
        if not self._token or not self._chat_ids:
            self._set_state("not_configured", "缺少 Bot Token 或 chat_id")
            self._notify_health(on_health)
            return False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, args=(on_command, on_health),
            daemon=True, name="message-telegram")
        self._thread.start()
        return True

    def stop(self, timeout=2.0):
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, float(timeout)))
        self._set_state("stopped")

    # ---- 轮询 ----

    def _poll_loop(self, on_command, on_health):
        self._set_state("connecting")
        try:
            me = self._api("getMe", timeout=15)
            with self._lock:
                self._username = str(me.get("username") or "")
                self._bot_id = me.get("id")
        except TelegramError as e:
            if e.code in (401, 403, 404):
                self._set_state("auth_error", e.public())
                self._notify_health(on_health)
                self._log(f"Telegram Token 无效：{e.public()}", "WARN")
                return
            self._set_state("network_error", e.public())
            self._notify_health(on_health)
        if not self._prime_offset(on_health):
            return
        network_idx = 0
        while not self._stop.is_set():
            try:
                updates = self._api("getUpdates", self._poll_params(),
                                    timeout=self._poll_timeout + 15)
                network_idx = 0
                with self._lock:
                    self._last_poll_ok = time.time()
                self._set_state("polling")
                self._process_updates(updates, on_command)
                self._notify_health(on_health)
            except TelegramError as e:
                action = classify_poll_error(e)
                self._set_state(action["state"], e.public())
                self._notify_health(on_health)
                if action["stop"]:
                    self._log(f"Telegram 轮询停止：{e.public()}", "WARN")
                    return
                if action["kind"] == "network":
                    delay = _jitter(NETWORK_BACKOFF[
                        min(network_idx, len(NETWORK_BACKOFF) - 1)])
                    network_idx += 1
                elif action["kind"] == "conflict":
                    delay = _jitter(CONFLICT_BACKOFF[
                        min(network_idx, len(CONFLICT_BACKOFF) - 1)])
                    network_idx += 1
                else:
                    delay = action["delay"]
                if self._stop.wait(delay):
                    return
            except Exception as e:
                self._set_state("error", f"{type(e).__name__}: {e}")
                self._notify_health(on_health)
                if self._stop.wait(30):
                    return

    def _prime_offset(self, on_health=None):
        """启动时取最后一条 update_id，避免把停机期间旧指令当新指令。"""
        self._started_at = time.time()
        try:
            updates = self._api("getUpdates",
                                {"offset": -1, "limit": 1, "timeout": 0},
                                timeout=15)
        except TelegramError as e:
            if e.code in (401, 403, 404):
                self._set_state("auth_error", e.public())
                self._notify_health(on_health)
                return False
            # 网络失败：靠启动时间过滤兜底（处理时丢弃旧消息）
            self._offset = 0
            return True
        with self._lock:
            if updates:
                try:
                    self._offset = int(updates[-1].get("update_id", -1)) + 1
                except (TypeError, ValueError):
                    self._offset = 0
            else:
                self._offset = 0
        return True

    def _poll_params(self):
        with self._lock:
            offset = self._offset
        return {"offset": offset, "timeout": self._poll_timeout, "limit": 50,
                "allowed_updates": json.dumps(["message"])}

    def _process_updates(self, updates, on_command):
        for update in updates or []:
            if self._stop.is_set():
                return
            if not isinstance(update, dict):
                continue
            try:
                uid = int(update.get("update_id") or 0)
            except (TypeError, ValueError):
                continue
            with self._lock:
                if uid < self._offset:
                    continue
                self._offset = max(self._offset, uid + 1)
                started_at = self._started_at
            message = update.get("message") or {}
            text = message.get("text") or ""
            if not text:
                continue
            sender = message.get("from") or {}
            if sender.get("is_bot"):
                continue
            try:
                msg_date = float(message.get("date") or 0)
            except (TypeError, ValueError):
                msg_date = 0
            if msg_date and started_at and \
                    msg_date < started_at - STARTUP_SKEW_TOLERANCE:
                continue
            chat = message.get("chat") or {}
            chat_id = str(chat.get("id") or "")
            if not chat_id:
                continue
            command = InboundCommand(
                channel="telegram", chat_id=chat_id,
                user_id=str(sender.get("id") or ""),
                username=str(sender.get("username") or ""),
                text=str(text), message_id=message.get("message_id"),
                update_id=uid, received_at=time.time())
            try:
                on_command(command)
            except Exception as e:
                self._log(f"处理 Telegram 指令异常：{type(e).__name__}: {e}",
                          "WARN")

    # ---- 发送 ----

    def send_text(self, chat_id, text):
        parts = split_text(text, MAX_TEXT)
        if not parts:
            return SendResult(True)
        last_id = None
        for index, part in enumerate(parts):
            body = part if len(parts) == 1 else \
                f"{part}\n[{index + 1}/{len(parts)}]"
            result = self._send_with_retry(
                "sendMessage",
                {"chat_id": chat_id, "text": body,
                 "disable_web_page_preview": True})
            if not result.ok:
                return result
            last_id = result.message_id
        return SendResult(True, message_id=last_id)

    def send_photo(self, chat_id, image, caption="", filename="capture.png"):
        caption = str(caption or "")[:CAPTION_MAX]
        if self._photo_original:
            return self._send_with_retry(
                "sendDocument",
                {"chat_id": chat_id, "caption": caption},
                files={"document": (filename, image)}, timeout=30)
        try:
            data = self._compress_photo(image)
        except Exception:
            data = None
        if data:
            result = self._send_with_retry(
                "sendPhoto", {"chat_id": chat_id, "caption": caption},
                files={"photo": ("capture.jpg", data)}, timeout=30)
            if result.ok or result.code != 400:
                return result
        return self._send_with_retry(
            "sendDocument", {"chat_id": chat_id, "caption": caption},
            files={"document": (filename, image)}, timeout=30)

    def _compress_photo(self, image):
        from PIL import Image
        img = Image.open(BytesIO(image))
        img.thumbnail((PHOTO_MAX_SIDE, PHOTO_MAX_SIDE))
        rgb = img.convert("RGB")
        data = b""
        for quality in (85, 70, 50):
            buf = BytesIO()
            rgb.save(buf, format="JPEG", quality=quality)
            data = buf.getvalue()
            if len(data) <= PHOTO_MAX_BYTES:
                break
        return data

    def _send_with_retry(self, method, params, files=None, timeout=15):
        for attempt in (0, 1):
            try:
                result = self._api(method, params, files=files,
                                   timeout=timeout)
                message_id = (result or {}).get("message_id") \
                    if isinstance(result, dict) else None
                return SendResult(True, message_id=message_id)
            except TelegramError as e:
                if e.code == 429 and e.retry_after and attempt == 0:
                    time.sleep(min(max(e.retry_after, 1.0), 10.0))
                    continue
                return SendResult(False, e.public(), e.code, e.retry_after)
        return SendResult(False, "发送失败")

    # ---- 验证 ----

    def verify(self, step_cb=None):
        step_cb = step_cb or (lambda step: None)
        started = time.time()
        labels = {step["key"]: step["label"]
                  for step in self.spec.verify_steps}
        steps = []
        emitted = set()

        def emit(key, ok, detail=""):
            step = VerifyStep(key, labels.get(key, key), ok, detail)
            steps.append(step)
            emitted.add(key)
            try:
                step_cb(step)
            except Exception:
                pass

        def finish(ok):
            for key in ("config", "get_me", "send_message"):
                if key not in emitted:
                    emit(key, False, "未执行")
            return VerifyReport(channel="telegram", ok=ok, steps=steps,
                                started_at=started, finished_at=time.time())

        token = self._token
        if not token or not self._chat_ids:
            emit("config", False, "缺少 Bot Token 或允许的 chat_id")
            return finish(False)
        emit("config", True,
             f"Token 尾号 {token[-4:]}，chat_id {len(self._chat_ids)} 个")
        try:
            me = self._api("getMe", timeout=15)
        except TelegramError as e:
            emit("get_me", False, e.public())
            return finish(False)
        emit("get_me", True,
             f"@{me.get('username', '')} (id={me.get('id')})")
        try:
            result = self._api(
                "sendMessage",
                {"chat_id": self._chat_ids[0],
                 "text": "啄木鸟消息通道验证：%s（由「验证通道」按钮发送）"
                         % time.strftime("%Y-%m-%d %H:%M:%S"),
                 "disable_web_page_preview": True},
                timeout=15)
        except TelegramError as e:
            emit("send_message", False, e.public())
            return finish(False)
        emit("send_message", True,
             f"message_id={(result or {}).get('message_id')}")
        return finish(True)

    # ---- HTTP ----

    def _api(self, method, params=None, files=None, timeout=15):
        url = f"{API_BASE}/bot{self._token}/{method}"
        payload, content_type = _encode(params or {}, files)
        request = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"User-Agent": USER_AGENT,
                     "Content-Type": content_type})
        opener = urllib.request.build_opener(*self._proxy_handlers())
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
            except Exception:
                raw = b""
            data = _parse_json(raw)
            raise TelegramError(
                e.code, data.get("description") or f"HTTP {e.code}",
                (data.get("parameters") or {}).get("retry_after") or 0)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise TelegramError(
                0, f"network: {type(e).__name__}: {reason}", network=True)
        data = _parse_json(raw)
        if not data.get("ok"):
            raise TelegramError(
                data.get("error_code") or 0,
                data.get("description") or "API error",
                (data.get("parameters") or {}).get("retry_after") or 0)
        return data.get("result")

    def _proxy_handlers(self):
        if not self._proxy:
            return ()
        return (urllib.request.ProxyHandler(
            {"http": self._proxy, "https": self._proxy}),)

    # ---- 状态 ----

    def _set_state(self, state, error=None):
        with self._lock:
            self._state = state
            if error:
                self._last_error = str(error)[:300]
                self._last_error_ts = time.time()

    def _notify_health(self, on_health):
        if on_health is None:
            return
        try:
            on_health(self.state())
        except Exception:
            pass

    def state(self):
        with self._lock:
            state = self._state
            return {"state": state,
                    "state_text": STATE_TEXT.get(state, state),
                    "last_poll_ok": self._last_poll_ok,
                    "last_error": self._last_error,
                    "last_error_ts": self._last_error_ts,
                    "username": self._username,
                    "bot_id": self._bot_id,
                    "poll_timeout": self._poll_timeout}
