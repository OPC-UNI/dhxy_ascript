"""多窗口注册表：迁移/勾选/选中/灰行/目标注入（不碰真实 config）。"""
import types

import pytest

from src.app import window_registry


def _win(hwnd, role, server="一生所爱", name="甲"):
    title = (f"大话西游2经典版 $Revision: 1 - {server} - "
             f"{name}（ID：{role}）")
    return types.SimpleNamespace(hwnd=hwnd, title=title)


def _info(hwnd, role, server="一生所爱", name="甲"):
    w = _win(hwnd, role, server, name)
    return {"hwnd": hwnd, "title": w.title, "role_id": str(role),
            "server": server, "name": name, "logged_in": True}


@pytest.fixture
def env(monkeypatch):
    store = {}
    ctx = {"infos": [], "wins": [], "targets": [], "selected": []}
    monkeypatch.setattr(window_registry.app_utils, "kv_read_fresh",
                        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(window_registry.app_utils, "kv_save_merged",
                        lambda key, value: store.__setitem__(key, value))
    monkeypatch.setattr(window_registry.user_data, "set_selected_role",
                        lambda rid: ctx["selected"].append(rid))
    monkeypatch.setattr(window_registry.game, "list_main_window_infos",
                        lambda: list(ctx["infos"]))
    monkeypatch.setattr(window_registry.game, "list_main_windows",
                        lambda: list(ctx["wins"]))
    monkeypatch.setattr(window_registry.game, "set_process_target",
                        lambda role, hwnd=0, title="", strict=False:
                        ctx["targets"].append((role, hwnd, strict)))
    monkeypatch.setattr(window_registry.game, "clear_process_target",
                        lambda: ctx["targets"].append(("clear",)))
    window_registry.reset_cache()
    yield store, ctx
    window_registry.reset_cache()


def test_ensure_initialized_migrates_old_binding(env, monkeypatch):
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    monkeypatch.setattr(
        window_registry.game, "bound_window_id",
        lambda: {"id": "（ID：111）", "hwnd": 1, "title": ctx["wins"][0].title})

    state = window_registry.ensure_initialized()

    assert state["initialized"] is True
    assert state["selected"] == "111"
    assert state["selected_hwnd"] == 1
    assert state["roles"]["111"]["enabled"] is True
    assert "222" not in state["roles"]
    assert ctx["targets"][-1] == ("111", 1, False)
    assert store[window_registry.STATE_KEY]["selected"] == "111"


def test_closed_window_removed_and_selection_falls_back(env, monkeypatch):
    """窗口关闭即从窗口管理移除；选中回落到其他活窗口，重开恢复勾选。"""
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()  # 无旧绑定：选第一个已登录窗口 111
    window_registry.set_enabled("333", True)

    # 111 关闭：不再出行（勾选状态仍按角色持久化），选中回落到 222
    ctx["infos"] = [_info(2, 222, name="乙")]
    ctx["wins"] = [_win(2, 222, name="乙")]
    data = window_registry.list_rows()

    by_role = {r["role_id"]: r for r in data["rows"] if r["role_id"]}
    assert set(by_role) == {"222"}
    assert data["selected"] == "222"
    assert data["selected_hwnd"] == 2
    assert by_role["222"]["selected"] is True
    assert ctx["targets"][-1] == ("222", 2, False)
    saved = store[window_registry.STATE_KEY]
    assert saved["selected"] == "222"
    assert saved["roles"]["111"]["enabled"] is True  # 重开恢复勾选
    assert saved["roles"]["333"]["enabled"] is True
    assert "111" not in by_role and "333" not in by_role

    # 111 重开：重新出现在窗口管理，保留勾选；当前窗口仍是 222
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    data = window_registry.list_rows()
    by_role = {r["role_id"]: r for r in data["rows"] if r["role_id"]}
    assert by_role["111"]["live"] is True
    assert by_role["111"]["enabled"] is True
    assert by_role["111"]["selected"] is False
    assert by_role["222"]["selected"] is True


def test_all_windows_closed_clears_selection_then_reopen_selects(env, monkeypatch):
    """窗口全部关闭：清空选中（面板显示未选择窗口），重开后自动选中。"""
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲")]
    ctx["wins"] = [_win(1, 111, name="甲")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()
    assert window_registry.list_rows()["selected"] == "111"

    ctx["infos"] = []
    ctx["wins"] = []
    data = window_registry.list_rows()
    assert data["rows"] == []
    assert data["selected"] is None
    assert data["selected_hwnd"] == 0
    assert store[window_registry.STATE_KEY]["selected"] is None
    assert ctx["targets"][-1] == (None, 0, False)

    ctx["infos"] = [_info(1, 111, name="甲")]
    ctx["wins"] = [_win(1, 111, name="甲")]
    data = window_registry.list_rows()
    assert data["selected"] == "111"
    assert data["rows"][0]["selected"] is True


def test_group_pass_selected_role_keeps_row(env, monkeypatch):
    """组号 pass：标题显示队员角色，但选中队长的窗口仍活着 → 保留选中行。"""
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲")]
    ctx["wins"] = [_win(1, 111, name="甲")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()
    # pass 切到队员 Tab：标题变成 222，队长 111 仍是该窗口的登录 Tab
    ctx["infos"] = [_info(1, 222, name="乙")]
    monkeypatch.setattr(window_registry, "_role_in_window",
                        lambda win, rid: str(rid) == "111")

    data = window_registry.list_rows()

    by_role = {r["role_id"]: r for r in data["rows"] if r["role_id"]}
    assert data["selected"] == "111"
    assert by_role["111"]["live"] is True       # 窗口没关，不是灰行
    assert by_role["111"]["selected"] is True
    assert by_role["111"]["hwnd"] == 1
    assert by_role["222"]["selected"] is False


def test_select_and_enable_persist(env, monkeypatch):
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()

    result = window_registry.select("222", 2)
    assert result["status"] == "success"
    assert result["selected"] == "222"
    assert ctx["targets"][-1] == ("222", 2, False)

    window_registry.set_enabled("222", True)
    saved = store[window_registry.STATE_KEY]
    assert saved["roles"]["222"]["enabled"] is True
    assert saved["roles"]["222"]["label"]  # 服务器+角色名


def test_unlogged_window_not_selectable(env, monkeypatch):
    _, ctx = env
    ctx["infos"] = [{"hwnd": 9, "title": "大话西游2经典版",
                     "role_id": None, "server": "", "name": "",
                     "logged_in": False}]
    ctx["wins"] = [_win(9, 0, name="")]
    ctx["wins"][0].title = "大话西游2经典版"
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)

    state = window_registry.ensure_initialized()
    assert state["selected"] is None
    data = window_registry.list_rows()
    assert data["rows"][0]["selectable"] is False
    assert data["rows"][0]["enabled"] is False
    assert window_registry.select("", 9)["status"] == "error"


def test_kicked_window_row_is_unlogged(env, monkeypatch):
    """标题带 ID 但被顶号（info.logged_in=False）：行按未登录输出且不自动选中。"""
    _, ctx = env
    ctx["infos"] = [dict(_info(1, 111, name="甲"), logged_in=False)]
    ctx["wins"] = [_win(1, 111, name="甲")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)

    state = window_registry.ensure_initialized()
    assert state["selected"] is None          # 不自动选中未登录窗口
    data = window_registry.list_rows()
    row = next(r for r in data["rows"] if r["role_id"] == "111")
    assert row["live"] is True                # 窗口仍在（重登恢复）
    assert row["logged_in"] is False


def test_resolve_target_fresh_login_state(env, monkeypatch):
    """启动目标解析带 fresh 登录态（不复用看板缓存）。"""
    _, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲")]
    ctx["wins"] = [_win(1, 111, name="甲")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()
    calls = []
    monkeypatch.setattr(window_registry.game, "window_logged_in",
                        lambda win, fresh=False: calls.append(fresh) or False)

    target = window_registry.resolve_target("111", 1)
    assert target["logged_in"] is False
    assert calls == [True]


def test_rows_carry_stable_tab_seq(env, monkeypatch):
    """窗口 Tab 固定顺序：seq 按角色首次出现顺序，选中/勾选不重排。

    2026-11-22 v2：Tab 在启动/停止/选中时跳位会点错，seq 必须在
    list_rows 多次调用间保持一致（角色 dict 插入序）。
    """
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙"),
                    _info(3, 333, name="丙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙"),
                   _win(3, 333, name="丙")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()      # 111 首个进入注册表
    first = {r["role_id"]: r["seq"] for r in window_registry.list_rows()["rows"]
             if r["role_id"]}
    assert first["111"] < first["222"] < first["333"]  # 活窗口出现顺序
    window_registry.set_enabled("222", True)
    window_registry.select("222", 2)
    second = {r["role_id"]: r["seq"] for r in window_registry.list_rows()["rows"]
              if r["role_id"]}
    assert second == first                    # 勾选/选中变化不改 seq
