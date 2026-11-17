"""主进程日志行 → 消息事件（旁路，不修改任务子进程）。

输入是 ControlWindow.add_logs 收到的行（任务子进程 stdout 回流 + 主进程
[系统]/[授权] 消息）。分类只做子串匹配，去重/预算在本模块内加锁，调用方
可在 TaskManager 监听线程直接调用。
"""
import collections
import re
import threading
import time

EVENT_DEDUPE = {
    "task_start": 5.0,
    "task_stop": 15.0,   # 「窗口关闭自停」与随后「任务已停止」共键去重
    "task_end": 5.0,
    "task_crash": 10.0,
    "license_block": 60.0,
}
ERROR_DEDUPE_WINDOW = 300.0
ERROR_ESCALATE_AT = 10
ERROR_BUDGET_PER_MIN = 6

_CRASH_RE = re.compile(r"任务进程异常退出（exitcode=(-?\d+)）")
_TS_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\s+\[\w+\]\s*")
_NUM_RE = re.compile(r"\d+")
_SPACE_RE = re.compile(r"\s+")
_LICENSE_MARK = "[授权] 校验未通过："


def _normalize(text):
    """ERROR 去重键：去时间戳/级别、数字归一、压缩空白。"""
    cleaned = _TS_RE.sub("", str(text or ""))
    cleaned = cleaned.replace("[ERROR]", " ")
    cleaned = _NUM_RE.sub("#", cleaned)
    return _SPACE_RE.sub(" ", cleaned).strip()


def _event(key, title, detail="", dedupe_key=None, norm=""):
    return {"key": key, "title": title, "detail": detail,
            "dedupe_key": dedupe_key or key, "norm": norm}


def classify_log_line(line):
    """日志行 → 事件 dict；非事件行返回 None。"""
    text = str(line or "")
    if not text.strip() or "[消息]" in text:
        return None
    if "[系统] 任务已启动" in text:
        return _event("task_start", "任务已启动")
    if "[系统] 任务已结束（自然退出）" in text:
        return _event("task_end", "任务已结束（自然退出）")
    crash = _CRASH_RE.search(text)
    if crash:
        code = crash.group(1)
        return _event("task_crash", "任务进程异常退出",
                      f"exitcode={code}", dedupe_key=f"task_crash:{code}")
    if "[系统] 任务已停止" in text:
        return _event("task_stop", "任务已停止")
    if "[系统] 检测到游戏窗口已关闭，任务自动终止" in text:
        return _event("task_stop", "游戏窗口已关闭，任务自动终止")
    idx = text.find(_LICENSE_MARK)
    if idx >= 0:
        detail = text[idx + len(_LICENSE_MARK):].strip()
        return _event("license_block", "授权校验未通过", detail,
                      dedupe_key="license:" + _normalize(detail))
    if "[ERROR]" in text:
        detail = _TS_RE.sub("", text).replace("[ERROR]", "").strip()
        detail = _SPACE_RE.sub(" ", detail)[:500]
        return _event("error", "ERROR 告警", detail,
                      dedupe_key="error", norm=_normalize(detail))
    return None


class EventDeduper:
    """生命周期事件窗口去重 + ERROR 归一化去重/升级/每分钟预算。"""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._last = {}
        self._errors = {}
        self._error_times = collections.deque()
        self.suppressed = 0

    def reset(self):
        with self._lock:
            self._last.clear()
            self._errors.clear()
            self._error_times.clear()
            self.suppressed = 0

    def accept(self, event):
        """返回应推送的事件（可能被改写为升级文案）；抑制时返回 None。"""
        if not event:
            return None
        now = self._clock()
        if event.get("key") == "error":
            return self._accept_error(event, now)
        key = event.get("dedupe_key") or event.get("key")
        window = EVENT_DEDUPE.get(event.get("key"), 5.0)
        with self._lock:
            last = self._last.get(key, 0.0)
            if now - last < window:
                return None
            self._last[key] = now
            return event

    def _accept_error(self, event, now):
        norm = event.get("norm") or _normalize(event.get("detail") or
                                               event.get("title"))
        with self._lock:
            while self._error_times and now - self._error_times[0] > 60.0:
                self._error_times.popleft()
            if len(self._error_times) >= ERROR_BUDGET_PER_MIN:
                self.suppressed += 1
                return None
            state = self._errors.get(norm)
            if state is None or now - state["first"] > ERROR_DEDUPE_WINDOW:
                self._errors[norm] = {"first": now, "count": 1,
                                      "escalated": False}
                self._error_times.append(now)
                return event
            state["count"] += 1
            if state["count"] >= ERROR_ESCALATE_AT and not state["escalated"]:
                state["escalated"] = True
                self._error_times.append(now)
                upgraded = dict(event)
                upgraded["title"] = ("同一错误已重复 %d 次（后续静默）"
                                     % state["count"])
                return upgraded
            return None


def format_event(event, identity=""):
    """事件推送文本（纯文本，无 Markdown）。"""
    lines = [f"【啄木鸟事件】{event.get('title') or '消息'}",
             "时间：" + time.strftime("%Y-%m-%d %H:%M:%S")]
    if identity:
        lines.append(f"角色：{identity}")
    detail = _SPACE_RE.sub(" ", str(event.get("detail") or "")).strip()
    if detail:
        lines.append("详情：" + detail[:500])
    return "\n".join(lines)
