"""窗口绑定文案（服务器+角色名短文案）。

依赖 device.game 读取窗口/绑定记录；从 core.settings 拆出，保持 core
不依赖 device。
"""
import re

from . import game

def _short_label_from_title(title):
    """从完整窗口标题提取「服务器+角色名」短文案；无 ID 返回 None。"""
    if "ID：" not in (title or ""):
        return None
    parts = [p.strip() for p in title.split(" - ")]
    if len(parts) >= 3:
        char = re.sub(r"（ID：\d+）\s*$", "", parts[-1]).strip()
        label = f"{parts[-2]}{char}"
        if label:
            return label
    if re.fullmatch(r"（ID：\d+）", title.strip()):
        return None  # 旧版纯 ID 绑定没有角色名，页面按「ID：xxx」展示
    # 标题格式意外时退回完整标题，超长截断（窗口标题栏空间有限）
    return title if len(title) <= 20 else title[:19] + "…"

def bound_short_label():
    """窗口绑定角色短文案（控制窗口标题/托盘菜单共用）：服务器+角色名，
    如「一生所爱「长相思」」。

    优先取当前窗口标题（实时）；窗口在登录界面（标题无 ID）或未启动
    时，回退绑定记录里的标题解析（角色信息不因登录状态丢失）。无绑定/
    无角色信息返回 None。文案刻意保持简短，不拼完整标题。
    """
    win = game.find_main_window()
    title = (getattr(win, "title", "") or "") if win is not None else ""
    if "ID：" not in title:
        raw = game.bound_window_id()
        title = raw.get("title") if isinstance(raw, dict) else raw
    return _short_label_from_title(title or "")

def bound_label_from_binding():
    """只看绑定记录的角色短文案（成就页轮询用，不做窗口枚举）。"""
    raw = game.bound_window_id()
    title = raw.get("title") if isinstance(raw, dict) else raw
    return _short_label_from_title(title or "")
