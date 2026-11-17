"""消息命令路由：解析、白名单、限频、指令格式化与执行。

通道适配器只负责收发；本模块不 import ControlWindow，能力通过 host
对象注入（见 service._ControlMessageHost）。查询类指令（状态/进度/日志/
截图/方案列表）直接格式化回复；控制类指令（启停任务/切换方案）经 host
调用与面板按钮同一入口（授权校验、运行中护栏都在 ControlWindow 侧），
/exitgame 额外走 confirm 二次确认。
"""
import collections
import re
import threading
import time

from .registry import COMMANDS, HELP_TEXT

_CMD_RE = re.compile(
    r"^/([A-Za-z_]+)(?:@[A-Za-z0-9_]+)?(?:\s+(.*))?$")


def _build_aliases():
    aliases = {}
    for item in COMMANDS:
        base = item["cmd"].split()[0].lstrip("/").lower()
        aliases["/" + base] = "/" + base
        aliases[base] = "/" + base
        for alias in item["aliases"]:
            key = alias.split()[0].lstrip("/").lower()
            if key:
                aliases[key] = "/" + base
    return aliases


ALIASES = _build_aliases()


def parse_command(text):
    """返回 (command, arg) / ("unknown", raw) / None（普通聊天）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = _CMD_RE.match(raw)
    if match:
        command = ALIASES.get("/" + match.group(1).lower())
        if command:
            return command, (match.group(2) or "").strip()
        return "unknown", raw
    parts = raw.split(maxsplit=1)
    command = ALIASES.get(parts[0].lower())
    if command:
        return command, (parts[1].strip() if len(parts) > 1 else "")
    return None


def mask_chat_id(chat_id):
    text = str(chat_id or "")
    if len(text) <= 6:
        return text
    return text[:3] + "***" + text[-3:]


class RateLimiter:
    """每 chat 5 burst + 1 条/4s；全局 20 条/分钟；capture 冷却。"""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._chat = {}
        self._global = collections.deque()
        self._notice = {}
        self._capture = {}

    def allow(self, chat_id):
        now = self._clock()
        with self._lock:
            tokens, last = self._chat.get(chat_id, (5.0, now))
            tokens = min(5.0, tokens + max(0.0, now - last) / 4.0)
            if tokens < 1.0:
                self._chat[chat_id] = (tokens, now)
                return False
            self._chat[chat_id] = (tokens - 1.0, now)
            while self._global and now - self._global[0] > 60.0:
                self._global.popleft()
            if len(self._global) >= 20:
                return False
            self._global.append(now)
            return True

    def allow_capture(self, chat_id, cooldown):
        now = self._clock()
        with self._lock:
            last = self._capture.get(chat_id, 0.0)
            if now - last < cooldown:
                return False
            self._capture[chat_id] = now
            return True

    def notice_allowed(self, chat_id, window=30.0):
        now = self._clock()
        with self._lock:
            last = self._notice.get(chat_id, 0.0)
            if now - last < window:
                return False
            self._notice[chat_id] = now
            return True


class MessageRouter:
    EXIT_CONFIRM_TTL = 120.0
    EXIT_CONFIRM_WORDS = ("confirm", "确认", "确认退出", "yes")

    def __init__(self, host, log=None, clock=time.time, record=None):
        self._host = host
        self._log = log or (lambda msg, level="INFO": None)
        self._clock = clock
        self._record = record or (lambda item: None)
        self._limiter = RateLimiter(clock=clock)
        self._pending_exits = {}
        self._exit_lock = threading.Lock()

    def dispatch(self, adapter, incoming, chat_ids, capture_cooldown=3.0):
        """处理一条入站文本；返回记录 dict（供服务写入最近指令）。"""
        now = self._clock()
        text = str(incoming.text or "").strip()
        record = {"ts": now, "channel": incoming.channel,
                  "chat_id": mask_chat_id(incoming.chat_id),
                  "text": text[:200], "ok": False, "error": "",
                  "rejected": False, "command": ""}
        parsed = parse_command(text)
        if parsed is None:
            return record  # 普通聊天静默忽略，不记录
        command, arg = parsed
        record["command"] = command
        allowed = [str(item) for item in (chat_ids or [])]
        if str(incoming.chat_id) not in allowed:
            record["rejected"] = True
            record["error"] = "chat_id 未授权"
            self._record(record)
            self._log("拒绝未授权 chat_id=%s 的指令"
                      % mask_chat_id(incoming.chat_id), "WARN")
            return record
        if command != "/exitgame":
            with self._exit_lock:
                self._pending_exits.pop(str(incoming.chat_id), None)
        if not self._limiter.allow(incoming.chat_id):
            if self._limiter.notice_allowed(incoming.chat_id):
                self._send(adapter, incoming.chat_id,
                           "操作太频繁，请稍后再试。")
            record["error"] = "限频"
            self._record(record)
            return record
        try:
            if command == "/help":
                ok, error = self._send(adapter, incoming.chat_id, HELP_TEXT)
            elif command == "/status":
                ok, error = self._send(adapter, incoming.chat_id,
                                       self._render_status())
            elif command == "/task":
                ok, error = self._send(adapter, incoming.chat_id,
                                       self._render_task())
            elif command == "/log":
                ok, error = self._send(adapter, incoming.chat_id,
                                       self._render_log(arg))
            elif command == "/capture":
                ok, error = self._do_capture(adapter, incoming.chat_id,
                                             capture_cooldown)
            elif command == "/schemes":
                ok, error = self._send(adapter, incoming.chat_id,
                                       self._render_schemes())
            elif command == "/switch":
                ok, error = self._handle_switch(arg, adapter, incoming.chat_id)
            elif command == "/start":
                ok, error = self._handle_start(adapter, incoming.chat_id)
            elif command == "/stop":
                ok, error = self._handle_stop(adapter, incoming.chat_id)
            elif command == "/exitgame":
                ok, error = self._handle_exit_game(arg, incoming, adapter)
            else:
                ok, error = self._send(
                    adapter, incoming.chat_id,
                    "未知指令。发送 /help 查看可用指令。")
            record["ok"] = bool(ok)
            record["error"] = error or ""
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {e}"
            self._log(f"处理指令 {command} 异常：{record['error']}", "WARN")
        self._record(record)
        return record

    def _handle_exit_game(self, arg, incoming, adapter):
        """退出游戏：先回复确认说明，收到 confirm 后在后台链路执行。

        游戏未启动（找不到主窗口）时不进入二次确认，直接回复无需退出。
        """
        chat_id = str(incoming.chat_id)
        now = self._clock()
        tokens = str(arg or "").strip().lower().split()
        word = tokens[0] if tokens else ""
        if word not in self.EXIT_CONFIRM_WORDS and not self._game_window_present():
            return self._send(adapter, chat_id, "游戏未启动，无需退出。")
        if word in self.EXIT_CONFIRM_WORDS:
            with self._exit_lock:
                expires = self._pending_exits.get(chat_id)
                valid = bool(expires and now <= expires)
                self._pending_exits.pop(chat_id, None)
            if not valid:
                return self._send(adapter, chat_id,
                                  "没有待确认的退出操作（或已超过 120 秒）。"
                                  "请先发送 /exitgame。")
            self._send(adapter, chat_id,
                       "开始退出游戏：停止任务 → 解散/离开队伍 → 关闭游戏客户端。")
            if not hasattr(self._host, "exit_game"):
                return self._send(adapter, chat_id,
                                  "当前版本不支持退出游戏。")

            def _progress(text):
                self._send(adapter, chat_id, text)

            try:
                result = self._host.exit_game(progress=_progress) or {}
            except Exception as e:
                self._log(f"退出游戏调用异常：{type(e).__name__}: {e}",
                          "WARN")
                return self._send(
                    adapter, chat_id,
                    f"退出游戏异常：{type(e).__name__}: {e}")
            ok = bool(result.get("ok"))
            message = str(result.get("message") or
                          ("游戏客户端已退出" if ok else "退出游戏失败"))
            return self._send(adapter, chat_id,
                              ("✔ " if ok else "✘ ") + message)
        with self._exit_lock:
            self._pending_exits[chat_id] = now + self.EXIT_CONFIRM_TTL
        return self._send(
            adapter, chat_id,
            "将执行：停止当前任务（若运行中）→ 解散/离开队伍（若在队伍中）"
            "→ 关闭游戏客户端（所有登录 Tab 一起退出）。\n"
            "120 秒内回复 /exitgame confirm 确认；回复其他指令取消。")

    def _game_window_present(self):
        """游戏窗口在场检查：host 未提供该能力时按在场处理（保持旧流程）。"""
        check = getattr(self._host, "game_window_present", None)
        if not callable(check):
            return True
        try:
            return bool(check())
        except Exception:
            return True

    def _identity(self):
        try:
            return self._host.identity() or ""
        except Exception:
            return ""

    def _decorate(self, text):
        """所有回复统一带上当前绑定角色（无绑定时原样返回）。"""
        identity = self._identity()
        return f"角色：{identity}\n{text}" if identity else text

    def _send(self, adapter, chat_id, text):
        try:
            result = adapter.send_text(chat_id, self._decorate(text))
        except Exception as e:
            self._log(f"回复发送异常：{type(e).__name__}: {e}", "WARN")
            return False, str(e)
        if not result.ok:
            self._log(f"回复发送失败：{result.error}", "WARN")
            return False, result.error
        return True, ""

    def _do_capture(self, adapter, chat_id, cooldown):
        cooldown = max(0.0, float(cooldown or 0))
        if not self._limiter.allow_capture(chat_id, cooldown):
            if self._limiter.notice_allowed(chat_id):
                self._send(adapter, chat_id,
                           f"截图冷却中（{cooldown:.0f} 秒），请稍后再试。")
            return False, "截图冷却"
        capture = self._host.capture()
        if not capture or not capture.get("ok"):
            reason = (capture or {}).get("reason", "error")
            detail = str((capture or {}).get("detail") or "")
            messages = {
                "no_window": "未找到游戏窗口（游戏未启动）。",
                "minimized": "游戏窗口已最小化，恢复窗口后再试。",
                "busy": "截图正忙，请稍后再试。",
                "capture_failed": "截图失败（窗口 DC/尺寸异常），请查看运行日志。",
                "error": f"截图异常：{detail}",
            }
            self._send(adapter, chat_id,
                       messages.get(reason, "截图失败。"))
            return False, reason
        width = capture.get("w") or 0
        height = capture.get("h") or 0
        caption = self._decorate(
            f"游戏窗口截图 {width}x{height} · {time.strftime('%H:%M:%S')}")
        if capture.get("black"):
            caption += ("\n⚠ 黑帧：PrintWindow 未画出内容"
                        "（窗口可能最小化/被遮挡/显卡兼容问题）")
        try:
            result = adapter.send_photo(chat_id, capture.get("image") or b"",
                                        caption, "capture.png")
        except Exception as e:
            self._log(f"截图发送异常：{type(e).__name__}: {e}", "WARN")
            return False, str(e)
        if not result.ok:
            self._send(adapter, chat_id, f"截图发送失败：{result.error}")
            return False, result.error
        return True, ""

    def _render_status(self):
        try:
            running = bool(self._host.is_running())
        except Exception:
            running = False
        lines = [f"任务状态：{'运行中' if running else '未运行'}"]
        if not running:
            return "\n".join(lines)
        try:
            status = self._host.runtime_status() or {}
        except Exception:
            status = {}
        if not status:
            lines.append("状态文件暂不可用（任务可能刚启动或状态已陈旧）")
            return "\n".join(lines)
        task = status.get("task") or "-"
        progress = status.get("progress") or ""
        lines.append(f"当前任务：{task}" + (f" {progress}" if progress else ""))
        if status.get("phase"):
            lines.append(f"阶段：{status['phase']}")
        if status.get("battle"):
            lines.append(f"战斗：{status['battle']}")
        if status.get("team"):
            lines.append(f"队伍：{status['team']}")
        if status.get("bars"):
            lines.append(f"血蓝：{status['bars']}")
        return "\n".join(lines)

    def _render_task(self):
        try:
            data = self._host.task_progress() or {}
        except Exception as e:
            return f"进度读取失败：{type(e).__name__}: {e}"
        done = int(data.get("done") or 0)
        total = int(data.get("total") or 0)
        percent = data.get("percent") or 0
        if total:
            lines = [f"今日总进度：{percent}%（{done}/{total}）"]
        else:
            lines = [f"今日已完成：{done} 次（未设置次数上限）"]
        per_task = data.get("per_task") or {}
        if not per_task:
            lines.append("未选择任务。")
        for mode, item in per_task.items():
            count = int(item.get("count") or 0)
            task_done = int(item.get("done") or 0)
            suffix = f"{task_done}/{count}" if count else f"{task_done}（不限）"
            lines.append(f"· {mode}：{suffix}")
        return "\n".join(lines)

    def _render_log(self, arg):
        try:
            count = int(str(arg or "").strip() or 20)
        except (TypeError, ValueError):
            count = 20
        count = max(1, min(100, count))
        try:
            result = self._host.log_tail(count) or {}
        except Exception as e:
            return f"读取日志失败：{type(e).__name__}: {e}"
        if not result.get("ok"):
            return f"读取日志失败：{result.get('reason') or '未知原因'}"
        lines = result.get("lines") or []
        if not lines:
            return "日志暂时为空。"
        return ("最近 %d 行日志：\n%s\n（文件：%s）"
                % (len(lines), "\n".join(lines), result.get("path") or ""))

    # ---- 控制指令：配置方案 / 启停任务 ----

    def _scheme_data(self):
        """{active, names}；host 不支持时返回空（按「无方案」处理）。"""
        reader = getattr(self._host, "list_schemes", None)
        if not callable(reader):
            return {}
        data = reader() or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _fail_text(result, default="未知原因"):
        """控制指令失败文案：BUSY 转人话，其余取 host 返回的 msg。"""
        msg = str((result or {}).get("msg") or "").strip()
        if msg == "BUSY":
            return "任务正在启停中，请稍后再试"
        return msg or default

    def _render_schemes(self):
        try:
            data = self._scheme_data()
        except Exception as e:
            return f"读取配置方案失败：{type(e).__name__}: {e}"
        names = [str(name) for name in data.get("names") or []]
        active = str(data.get("active") or "")
        if not names:
            return "暂无配置方案（可在面板「系统」页新建）。"
        lines = [f"配置方案 {len(names)} 个，当前：{active or '-'}"]
        for name in names:
            lines.append(f"· {name}（当前）" if name == active else f"· {name}")
        lines.append("回复 /switch 方案名 切换（任务运行中不可切换）。")
        return "\n".join(lines)

    def _handle_switch(self, arg, adapter, chat_id):
        """切换配置方案：与面板下拉同一入口（运行中由 ControlWindow 拒绝）。"""
        name = str(arg or "").strip()
        if not name:
            return self._send(adapter, chat_id,
                              "用法：/switch 方案名（发送 /schemes 查看列表）。")
        switcher = getattr(self._host, "switch_scheme", None)
        if not callable(switcher):
            return self._send(adapter, chat_id, "当前版本不支持切换配置方案。")
        try:
            data = self._scheme_data()
        except Exception as e:
            return self._send(adapter, chat_id,
                              f"✘ 读取配置方案失败：{type(e).__name__}: {e}")
        names = [str(item) for item in data.get("names") or []]
        if name not in names:
            available = "、".join(names) if names else "无"
            return self._send(adapter, chat_id,
                              f"✘ 方案不存在：「{name}」\n可用方案：{available}")
        if name == str(data.get("active") or ""):
            return self._send(adapter, chat_id, f"「{name}」已是当前方案。")
        try:
            result = switcher(name) or {}
        except Exception as e:
            self._log(f"切换方案异常：{type(e).__name__}: {e}", "WARN")
            return self._send(adapter, chat_id,
                              f"✘ 切换方案异常：{type(e).__name__}: {e}")
        if result.get("status") == "success":
            return self._send(
                adapter, chat_id,
                f"✔ 已切换方案：「{result.get('active') or name}」")
        return self._send(adapter, chat_id,
                          "✘ 切换失败：" + self._fail_text(result))

    def _handle_start(self, adapter, chat_id):
        """启动任务：走 ControlWindow.start_task（授权校验/窗口绑定对齐同面板）。"""
        starter = getattr(self._host, "start_task", None)
        if not callable(starter):
            return self._send(adapter, chat_id, "当前版本不支持远程启动任务。")
        try:
            if self._host.is_running():
                return self._send(adapter, chat_id, "任务已在运行中。")
        except Exception:
            pass
        # 负面条件：游戏窗口不在场时启动只会拉起一个必然退出的子进程
        # （窗口看护 3 秒即判死），直接拒绝并提示先开游戏（与 /exitgame 对称）
        if not self._game_window_present():
            return self._send(adapter, chat_id,
                              "✘ 启动失败：未检测到游戏窗口（请先启动游戏客户端）。")
        try:
            result = starter() or {}
        except Exception as e:
            self._log(f"启动任务异常：{type(e).__name__}: {e}", "WARN")
            return self._send(adapter, chat_id,
                              f"✘ 启动失败：{type(e).__name__}: {e}")
        if result.get("status") == "success":
            return self._send(adapter, chat_id, "✔ 任务已启动")
        return self._send(adapter, chat_id,
                          "✘ 启动失败：" + self._fail_text(result))

    def _handle_stop(self, adapter, chat_id):
        """停止任务：走 ControlWindow.stop_task（与面板停止按钮同一入口）。"""
        stopper = getattr(self._host, "stop_task", None)
        if not callable(stopper):
            return self._send(adapter, chat_id, "当前版本不支持远程停止任务。")
        try:
            running = bool(self._host.is_running())
        except Exception:
            running = True
        if not running:
            return self._send(adapter, chat_id, "任务当前未运行。")
        try:
            result = stopper() or {}
        except Exception as e:
            self._log(f"停止任务异常：{type(e).__name__}: {e}", "WARN")
            return self._send(adapter, chat_id,
                              f"✘ 停止失败：{type(e).__name__}: {e}")
        if result.get("status") == "success":
            return self._send(adapter, chat_id, "✔ 任务已停止")
        return self._send(adapter, chat_id,
                          "✘ 停止失败：" + self._fail_text(result))
