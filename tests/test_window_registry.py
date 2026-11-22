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


def test_rows_include_closed_enabled_role_and_selected_stays(env, monkeypatch):
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()  # 无旧绑定：选第一个已登录窗口 111
    window_registry.set_enabled("333", True)

    # 选中窗口关闭：行保留为灰行，选中角色不变，不回退 222
    ctx["infos"] = [_info(2, 222, name="乙")]
    ctx["wins"] = [_win(2, 222, name="乙")]
    data = window_registry.list_rows()

    by_role = {r["role_id"]: r for r in data["rows"] if r["role_id"]}
    assert data["selected"] == "111"
    assert data["selected_hwnd"] == 0
    assert by_role["111"]["live"] is False
    assert by_role["111"]["selected"] is True
    assert by_role["222"]["live"] is True
    assert by_role["333"]["enabled"] is True
    assert by_role["333"]["live"] is False
    assert ctx["targets"][-1] == ("111", 0, False)


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


def test_rows_carry_stable_tab_seq(env, monkeypatch):
    """窗口 Tab 固定顺序：seq 按角色进入注册表的顺序，选中/运行不重排。

    2026-11-22 v2：Tab 在启动/停止/选中时跳位会点错，seq 必须在
    list_rows 多次调用间保持一致（角色 dict 插入序）。
    """
    store, ctx = env
    ctx["infos"] = [_info(1, 111, name="甲"), _info(2, 222, name="乙")]
    ctx["wins"] = [_win(1, 111, name="甲"), _win(2, 222, name="乙")]
    monkeypatch.setattr(window_registry.game, "bound_window_id", lambda: None)
    window_registry.ensure_initialized()      # 111 首个进入注册表
    window_registry.set_enabled("333", True)  # 333 随后进入（灰行）
    first = {r["role_id"]: r["seq"] for r in window_registry.list_rows()["rows"]
             if r["role_id"]}
    assert first["111"] < first["333"]        # 注册表顺序（222 未入表，排后）
    window_registry.select("222", 2)
    second = {r["role_id"]: r["seq"] for r in window_registry.list_rows()["rows"]
              if r["role_id"]}
    assert second == first                    # 选中变化不改 seq
