"""窗口绑定跟随实际窗口测试（2026-11-13）。

A 退出、打开 B 后：find_main_window 动态回退到 B（脚本实际就操作它），
ensure_binding_follows_live_window 负责把绑定记录改绑到 B，角色数据
（按角色隔离）随新窗口切换；未登录窗口/无窗口不改绑。
"""
from src.device import game


class _Win:
    def __init__(self, title, hwnd=0):
        self.title = title
        self.hwnd = hwnd


def _patch(monkeypatch, wins, bound):
    saved = []
    monkeypatch.setattr(game, "list_main_windows", lambda: wins)
    monkeypatch.setattr(game, "bound_window_id", lambda: bound)
    monkeypatch.setattr(game, "save_bound_window",
                        lambda win: saved.append(win))
    return saved


def test_rebind_when_bound_window_closed(monkeypatch):
    """绑定窗口 A 不存在、回退窗口 B 已登录：改绑到 B。"""
    b = _Win("服务器 - B（ID：222）", 2)
    saved = _patch(monkeypatch, [b],
                   {"id": "（ID：111）", "hwnd": 1, "title": "服务器 - A"})
    win, rebound = game.ensure_binding_follows_live_window()
    assert rebound is True
    assert win is b
    assert saved == [b]


def test_no_rebind_when_bound_window_alive(monkeypatch):
    """绑定窗口仍在（组号 pass 切 Tab 标题变化也属此类）：不改绑。"""
    a = _Win("服务器 - 队员（ID：214369642）", 1)
    saved = _patch(monkeypatch, [a],
                   {"id": "（ID：111）", "hwnd": 1, "title": "服务器 - 队长"})
    win, rebound = game.ensure_binding_follows_live_window()
    assert rebound is False
    assert win is a
    assert saved == []


def test_no_rebind_for_unlogged_live_window(monkeypatch):
    """回退窗口未登录（标题无账号 ID）：不写完整标题作绑定（多开会误匹配）。"""
    b = _Win("大话西游2经典版", 2)
    saved = _patch(monkeypatch, [b],
                   {"id": "（ID：111）", "hwnd": 1, "title": "服务器 - A"})
    win, rebound = game.ensure_binding_follows_live_window()
    assert rebound is False
    assert win is b
    assert saved == []


def test_no_rebind_without_any_window(monkeypatch):
    """游戏窗口全部关闭（客户端重启间隙）：保持绑定记录。"""
    saved = _patch(monkeypatch, [],
                   {"id": "（ID：111）", "hwnd": 1, "title": "服务器 - A"})
    assert game.ensure_binding_follows_live_window() == (None, False)
    assert saved == []


def test_no_auto_bind_without_binding_record(monkeypatch):
    """从未绑定过：交给启动/任务启动绑定流程，运行期轮询不写入。"""
    b = _Win("服务器 - B（ID：222）", 2)
    saved = _patch(monkeypatch, [b], None)
    win, rebound = game.ensure_binding_follows_live_window()
    assert rebound is False
    assert win is b
    assert saved == []
