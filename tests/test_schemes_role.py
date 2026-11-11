"""配置方案按角色隔离（data/<角色ID>/schemes.json）测试（2026-11-11）。

覆盖：
- 新角色方案文件按默认值创建（不继承其他角色设置）
- 迁移后的当前角色：方案列表保留，活动方案内容替换为当前实时设置
- 换角色：旧角色快照回写 → 新角色活动方案应用到 config
- 同角色/首次运行：不覆盖 config 里的实时设置
"""
import json

import pytest

from src import schemes, user_data


@pytest.fixture
def scheme_env(tmp_path, monkeypatch):
    monkeypatch.setattr(user_data, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(user_data, "_probe_role_id", lambda: "1001")
    monkeypatch.setattr(user_data, "_ROLE_CACHE", {"id": None, "ts": 0.0})
    yield tmp_path


def _path(root, role):
    return root / "data" / role / "schemes.json"


def test_ensure_role_schemes_creates_defaults(scheme_env):
    schemes.ensure_role_schemes()
    data = json.loads(_path(scheme_env, "1001").read_text(encoding="utf-8"))
    assert data["active"] == schemes.DEFAULT_SCHEME_NAME
    assert data[schemes.DEFAULT_SCHEME_NAME] == schemes._settings_spec()


def test_bootstrap_keeps_scheme_list_and_snapshots_current(scheme_env, monkeypatch):
    path = _path(scheme_env, "1001")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "active": "另一套", "默认": {"a": 1}, "另一套": {"b": 2}},
        ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(schemes, "_snapshot_current", lambda: {"live": True})
    schemes.bootstrap_current_role()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["active"] == "另一套"
    assert data["另一套"] == {"live": True}
    assert data["默认"] == {"a": 1}  # 其他方案不丢


def test_activate_role_switches_without_touching_other_role(scheme_env):
    """角色切换只确保当前角色方案文件；生效设置在各角色 settings.json。"""
    a = _path(scheme_env, "1001")
    a.parent.mkdir(parents=True)
    a.write_text(json.dumps({"active": "A", "A": {"x": 1}}), encoding="utf-8")

    schemes.activate_role("1001", "2002")

    # 旧角色方案不被改写
    assert json.loads(a.read_text(encoding="utf-8"))["A"] == {"x": 1}
    # 新角色按默认值建方案
    b_data = json.loads(_path(scheme_env, "2002").read_text(encoding="utf-8"))
    assert b_data["active"] == schemes.DEFAULT_SCHEME_NAME
    assert b_data[schemes.DEFAULT_SCHEME_NAME] == schemes._settings_spec()


def test_activate_role_new_role_gets_defaults(scheme_env):
    schemes.activate_role("1001", "2002")
    data = json.loads(_path(scheme_env, "2002").read_text(encoding="utf-8"))
    assert data["active"] == schemes.DEFAULT_SCHEME_NAME
    assert data[schemes.DEFAULT_SCHEME_NAME] == schemes._settings_spec()


def test_apply_writes_role_settings(scheme_env):
    """方案应用写当前角色 settings.json（不再写 config.json）。"""
    from src import role_settings
    schemes._apply({"task_modes": ["修罗"], "use_sheyaoxiang": False})
    assert role_settings.get("task_modes") == ["修罗"]
    assert role_settings.get("use_sheyaoxiang") is False
    # 未提供的键补默认值
    assert role_settings.get("task_count_修罗") == 90


def test_activate_same_role_does_not_apply(scheme_env, monkeypatch):
    applied = []
    monkeypatch.setattr(schemes, "_apply", lambda s: applied.append(s))
    schemes.activate_role("1001", "1001")
    assert applied == []
    assert _path(scheme_env, "1001").exists()
