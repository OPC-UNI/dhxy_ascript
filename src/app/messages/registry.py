"""消息通道注册表：字段 spec、默认配置、命令/事件文档、适配器工厂。

新增通道 = 本文件加一条 spec/默认配置 + 一个 adapter 文件；UI 由字段 spec
动态渲染，路由/服务层不需要改动。
"""
import re

from ...core.settings import MESSAGE_EVENT_KEYS
from .channel_base import ChannelSpec, FieldSpec


TELEGRAM_FIELDS = (
    FieldSpec("enabled", "启用", kind="switch", default=False),
    FieldSpec("token", "Bot Token", kind="password", secret=True,
              required=True, placeholder="123456:ABC...",
              hint="已保存的 Token 只显示尾 4 位；重新填写可覆盖；留空表示清空（建议先关闭通道启用）"),
    FieldSpec("chat_ids", "允许的 chat_id", kind="textarea", required=True,
              hint="每行一个数字 ID（群为负数）；给 bot 发消息后用 @userinfobot 查询"),
    FieldSpec("proxy", "代理地址", kind="text",
              placeholder="http://127.0.0.1:7890",
              hint="仅支持 http/https；国内访问 Telegram 需要代理"),
    FieldSpec("poll_timeout", "长轮询秒数", kind="number", default=20,
              min=5, max=50),
    FieldSpec("photo_original", "发送原图", kind="switch", default=False,
              hint="开启后以 PNG 文件发送（更清晰、更慢）"),
)

TELEGRAM_SPEC = ChannelSpec(
    name="telegram", title="Telegram", fields=TELEGRAM_FIELDS,
    help="需要能访问 api.telegram.org；国内网络请配置 HTTP 代理。指令回复与截图会经 Telegram 服务器转发。",
    verify_steps=(
        {"key": "config", "label": "检查配置"},
        {"key": "get_me", "label": "getMe 校验 Token"},
        {"key": "send_message", "label": "发送测试消息"},
    ),
    health_fields=(
        {"key": "last_poll_ok", "label": "最近轮询成功", "kind": "ago"},
        {"key": "username", "label": "Bot", "kind": "text", "prefix": "@"},
        {"key": "last_error", "label": "最近错误", "kind": "error"},
    ),
)

CHANNEL_SPECS = {"telegram": TELEGRAM_SPEC}

DEFAULT_CHANNEL_CONFIG = {
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_ids": [],
        "proxy": "",
        "poll_timeout": 20,
        "photo_original": False,
    },
}

EVENT_LABELS = {
    "task_start": "任务启动",
    "task_stop": "任务停止",
    "task_end": "任务自然结束",
    "task_crash": "任务异常退出",
    "error": "ERROR 告警",
    "license_block": "授权拦截",
}
assert tuple(EVENT_LABELS) == tuple(MESSAGE_EVENT_KEYS)

COMMANDS = (
    {"cmd": "/help", "aliases": ["帮助", "指令", "菜单"],
     "desc": "显示本帮助"},
    {"cmd": "/status", "aliases": ["状态", "运行状态"],
     "desc": "任务运行状态"},
    {"cmd": "/task", "aliases": ["进度", "任务", "今日进度"],
     "desc": "今日任务进度"},
    {"cmd": "/capture", "aliases": ["截图", "截屏", "拍照"],
     "desc": "截取游戏窗口并发回"},
    {"cmd": "/log [n]", "aliases": ["日志"],
     "desc": "最近 n 行日志，默认 20，最多 100"},
    {"cmd": "/schemes", "aliases": ["方案", "方案列表", "获取方案", "获取方案列表"],
     "desc": "配置方案列表"},
    {"cmd": "/switch <名称>", "aliases": ["切换方案", "切换"],
     "desc": "切换配置方案（任务运行中不可切换）"},
    {"cmd": "/start", "aliases": ["开始", "启动", "开始任务", "启动任务"],
     "desc": "启动任务（与面板按钮同链路，含启动前授权校验）"},
    {"cmd": "/stop", "aliases": ["停止", "停止任务"],
     "desc": "停止任务"},
    {"cmd": "/exitgame", "aliases": ["退出游戏", "退出", "下线", "关闭游戏"],
     "desc": "停止任务、解散队伍并退出游戏（需二次确认）"},
)

HELP_TEXT = "\n".join(
    ["啄木鸟消息服务", ""]
    + [f"{item['cmd']}  {item['desc']}" for item in COMMANDS]
    + ["", "也可以直接发送中文关键字，例如「状态」「进度」「方案」「开始」「停止」。",
       "查询与启停/切方案直接生效；/exitgame 需回复 confirm 二次确认。"])

SECRET_FIELD_KEYS = frozenset({"token"})


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def payload():
    """UI 动态渲染所需的全部元数据。"""
    return {
        "channels": [spec.to_dict()
                     for spec in CHANNEL_SPECS.values()],
        "events": [{"key": key, "label": label}
                   for key, label in EVENT_LABELS.items()],
        "commands": [dict(item) for item in COMMANDS],
        "help": HELP_TEXT,
    }


def effective_config(raw=None):
    """补通道默认值；未知通道/未知字段原样保留。"""
    cfg = dict(raw) if isinstance(raw, dict) else {}
    channels = cfg.get("channels")
    channels = channels if isinstance(channels, dict) else {}
    merged = {}
    for name, defaults in DEFAULT_CHANNEL_CONFIG.items():
        current = channels.get(name)
        current = current if isinstance(current, dict) else {}
        item = dict(defaults)
        item.update(current)
        chat_ids = item.get("chat_ids")
        if isinstance(chat_ids, str):
            item["chat_ids"] = [x.strip() for x in
                                re.split(r"[\r\n,，]+", chat_ids) if x.strip()]
        elif not isinstance(chat_ids, (list, tuple)):
            item["chat_ids"] = []
        else:
            item["chat_ids"] = [str(x) for x in chat_ids]
        try:
            item["poll_timeout"] = int(item.get("poll_timeout") or 20)
        except (TypeError, ValueError):
            item["poll_timeout"] = 20
        item["enabled"] = _truthy(item.get("enabled"))
        if "photo_original" in item:
            item["photo_original"] = _truthy(item.get("photo_original"))
        merged[name] = item
    for name, current in channels.items():
        if name not in merged:
            merged[name] = current
    cfg["channels"] = merged
    cfg.setdefault("enabled", False)
    cfg.setdefault("notify_online", False)
    events = cfg.get("events")
    cfg["events"] = dict(events) if isinstance(events, dict) else {}
    return cfg


def enabled_channel_names(cfg):
    cfg = effective_config(cfg)
    return [name for name, item in cfg["channels"].items()
            if isinstance(item, dict) and item.get("enabled")]


def channel_config(cfg, name):
    return effective_config(cfg)["channels"].get(name) or {}


def config_warnings(cfg):
    """保存后给 UI 的配置提示（不阻止保存）。"""
    cfg = effective_config(cfg)
    warnings = []
    names = enabled_channel_names(cfg)
    if cfg.get("enabled") and not names:
        warnings.append("服务已启用，但没有启用任何通道")
    for name in names:
        item = cfg["channels"].get(name) or {}
        if name == "telegram":
            if not str(item.get("token") or "").strip():
                warnings.append("Telegram 未填写 Bot Token")
            if not item.get("chat_ids"):
                warnings.append("Telegram 未填写允许的 chat_id")
    return warnings


def build_adapter(name, config, log=None):
    """按通道名构建适配器（未知/未实现通道报错，不静默启动）。"""
    if name == "telegram":
        from .channel_telegram import TelegramAdapter
        return TelegramAdapter(config, log=log)
    raise ValueError(f"未实现的通道：{name}")
