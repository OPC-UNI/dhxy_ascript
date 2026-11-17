"""消息通道基础类型与适配器接口。

通道适配器只负责协议收发（文本/图片、收指令、验证）；命令解析、白名单、
限频与格式化全部在 router.py，配置读写在 core/settings.py。
"""
from dataclasses import dataclass
from typing import Any, Optional

TEXT_LIMIT = 4000  # Telegram 单条上限 4096，留余量


@dataclass(frozen=True)
class FieldSpec:
    """通道配置字段（registry 声明，UI 按 kind 动态渲染）。"""
    key: str
    label: str
    kind: str = "text"          # text|password|textarea|switch|number
    placeholder: str = ""
    hint: str = ""
    default: Any = ""
    secret: bool = False
    required: bool = False
    min: Optional[int] = None
    max: Optional[int] = None
    compact: bool = False       # 短字段：与相邻 compact 字段合并成一行

    def to_dict(self):
        return {"key": self.key, "label": self.label, "kind": self.kind,
                "placeholder": self.placeholder, "hint": self.hint,
                "default": self.default, "secret": self.secret,
                "required": self.required, "min": self.min, "max": self.max,
                "compact": self.compact}


@dataclass(frozen=True)
class ChannelSpec:
    name: str
    title: str
    fields: tuple
    help: str = ""
    verify_steps: tuple = ()
    # 通道健康指标（UI 在通道卡片内渲染）：轮询通道用 last_poll_ok，
    # 订阅/长连接通道可用 last_event_ts 等；未声明则不显示。
    health_fields: tuple = ()

    def to_dict(self):
        return {"name": self.name, "title": self.title, "help": self.help,
                "fields": [f.to_dict() for f in self.fields],
                "verify_steps": [dict(step) for step in self.verify_steps],
                "health_fields": [dict(field) for field in self.health_fields]}


@dataclass
class InboundCommand:
    channel: str
    chat_id: str
    user_id: str = ""
    username: str = ""
    text: str = ""
    message_id: Optional[int] = None
    update_id: Optional[int] = None
    received_at: float = 0.0


@dataclass
class SendResult:
    ok: bool
    error: str = ""
    code: int = 0
    retry_after: float = 0.0
    message_id: Optional[int] = None


@dataclass
class VerifyStep:
    key: str
    label: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyReport:
    channel: str
    ok: bool
    steps: list
    started_at: float
    finished_at: float

    def to_dict(self):
        return {"channel": self.channel, "ok": self.ok,
                "steps": [{"key": s.key, "label": s.label, "ok": s.ok,
                           "detail": s.detail} for s in self.steps],
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "duration": round(self.finished_at - self.started_at, 2)}


class ChannelAdapter:
    """通道适配器基类（协议收发；不解析命令、不读配置）。"""
    spec: Optional[ChannelSpec] = None

    def __init__(self, config, log=None):
        self._config = dict(config or {})
        self._log = log or (lambda msg, level="INFO": None)

    def configure(self, config):
        self._config = dict(config or {})

    def start(self, on_command, on_health):
        raise NotImplementedError

    def stop(self, timeout=2.0):
        raise NotImplementedError

    def send_text(self, chat_id, text):
        raise NotImplementedError

    def send_photo(self, chat_id, image, caption="", filename="capture.png"):
        raise NotImplementedError

    def verify(self, step_cb):
        raise NotImplementedError

    def state(self):
        raise NotImplementedError


def split_text(text, limit=TEXT_LIMIT):
    """按换行/常见标点切段，保证每段不超过 limit；无合适断点则硬切。"""
    text = str(text or "")
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts, rest = [], text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
            for mark in ("。", "；", "！", "？", ". ", "; "):
                pos = rest.rfind(mark, 0, limit)
                if pos >= limit // 2:
                    cut = min(limit, pos + len(mark))
                    break
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return parts
