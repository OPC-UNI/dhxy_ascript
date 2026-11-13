"""角色数据目录（data/<角色ID>/）迁移与隔离测试（2026-11-11）。

覆盖：
- 角色 ID 解析（标题 / 角色键 / _default 兜底）
- 旧版 config/schemes/task_stats 迁移：按角色拆 state、方案/成就/
  高性能缓存只复制给迁移时当前角色（用户 2026-11-11 选择 B）
- 根目录旧文件原样保留（回滚安全）、迁移幂等
- 非 ID 键进 _default 并保留 legacy 原样
- 角色间 state/高性能缓存隔离
- 迁移时无绑定 → 首次绑定真实角色后认领 _default 数据
"""
import json

import pytest

from src import user_data

# conftest 的 autouse fixture 会把 _probe_role_id 换成固定值；模块导入时
# 先捕获原函数（角色解析顺序测试需要）。
_ORIGINAL_PROBE = user_data._probe_role_id


@pytest.fixture
def role_env(tmp_path, monkeypatch):
    monkeypatch.setattr(user_data, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(user_data, "_probe_role_id", lambda: "1001")
    monkeypatch.setattr(user_data, "_ROLE_CACHE", {"id": None, "ts": 0.0})
    yield tmp_path


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _legacy(tmp_path):
    cfg = {
        "task_progress": {
            "（ID：1001）": {"date": "2026-11-11", "modes": {"修罗": 5}},
            "（ID：2002）": {"date": "2026-11-10", "modes": {"五倍": 2}},
            "大话西游2经典版": {"date": "2026-11-09", "modes": {"修罗": 1}},
        },
        "sheyaoxiang_last_use": {"（ID：1001）": 111, "（ID：2002）": 222},
        "clear_screen_state": {"（ID：1001）": 10, "（ID：2002）": 20},
        "mingwang_race": {
            "（ID：1001）": {"date": "2026-11-11", "race": "魔族"},
            "": {"date": "2026-11-10", "race": "仙族"},
        },
        "group_member_actions": {"m1": {"char": "攻击", "beast": "防御"}},
        "group_last_scan": {"leader": {"id": "m1"}},
        "group_detected": True,
        "high_perf_cache": {"btn": [800, 600, 1, 2]},
        "bound_window_id": {"id": "（ID：1001）", "hwnd": 1,
                            "title": "大话西游2经典版 - 服务器 - 角色（ID：1001）"},
    }
    _write_json(tmp_path / "config.json", cfg)
    _write_json(tmp_path / "schemes.json",
                {"active": "默认", "默认": {"task_modes": ["修罗"]},
                 "另一套": {"task_modes": ["五倍"]}})
    _write_json(tmp_path / "task_stats.json",
                {"total": {"rounds": 9, "battles": 3, "seconds": 60,
                           "modes": {}}, "daily": {}})
    return cfg


def test_role_id_helpers():
    assert user_data.role_id_from_text("...（ID：123）") == "123"
    assert user_data.role_id_from_text("无 ID") is None
    assert user_data.role_id_from_char_key("（ID：456）") == "456"
    assert user_data.role_id_from_char_key("大话西游2经典版") == user_data.DEFAULT_ROLE


def test_migrate_splits_state_and_copies_current_only(role_env):
    _legacy(role_env)
    before = {name: (role_env / name).read_text(encoding="utf-8")
              for name in ("config.json", "schemes.json", "task_stats.json")}
    result = user_data.migrate_if_needed()
    assert result["migrated"] is True
    assert result["role"] == "1001"

    root = role_env / "data"
    cur = root / "1001"
    assert json.loads((cur / "schemes.json").read_text(encoding="utf-8"))["active"] == "默认"
    assert json.loads((cur / "task_stats.json").read_text(
        encoding="utf-8"))["total"]["rounds"] == 9
    assert json.loads((cur / "high_perf_cache.json").read_text(
        encoding="utf-8")) == {"btn": [800, 600, 1, 2]}

    state = user_data.read_state("1001")
    assert state["task_progress"]["modes"] == {"修罗": 5}
    assert state["sheyaoxiang_last_use"] == 111
    assert state["clear_screen_state"] == 10
    assert state["mingwang_race"]["race"] == "魔族"
    # 组号相关状态归迁移时当前角色
    assert state["group_member_actions"]["m1"]["char"] == "攻击"
    assert state["group_last_scan"]["leader"]["id"] == "m1"
    assert state["group_detected"] is True

    # 其他角色只拿自己的 state，不继承方案/成就/高性能缓存（用户选择 B）
    other = root / "2002"
    assert (other / "state.json").exists()
    assert not (other / "schemes.json").exists()
    assert not (other / "task_stats.json").exists()
    assert not (other / "high_perf_cache.json").exists()
    assert user_data.read_state("2002")["task_progress"]["modes"] == {"五倍": 2}

    # 非 ID 键进 _default，完整原映射保留在 legacy
    default = user_data.read_state(user_data.DEFAULT_ROLE)
    assert default["task_progress"]["modes"] == {"修罗": 1}
    assert default["mingwang_race"]["race"] == "仙族"
    assert "大话西游2经典版" in default["legacy"]["task_progress"]
    assert "" in default["legacy"]["mingwang_race"]

    # 根目录旧文件原样保留（回滚安全）
    for name, content in before.items():
        assert (role_env / name).read_text(encoding="utf-8") == content

    meta = user_data.read_meta()
    assert meta["layout"] == user_data.LAYOUT_VERSION
    assert meta["migration_role"] == "1001"
    assert "2002" in meta["known_roles"]
    assert set(meta["copied"]) == {"schemes", "stats", "high_perf"}
    assert meta["stats_imported"] is True


def test_migrate_idempotent_and_keeps_new_data(role_env):
    _legacy(role_env)
    assert user_data.migrate_if_needed()["migrated"] is True
    user_data.set_state(
        "task_progress", {"date": "2026-11-12", "modes": {"修罗": 99}}, "1001")
    result = user_data.migrate_if_needed()
    assert result["migrated"] is False
    assert user_data.get_state("task_progress", "1001")["modes"] == {"修罗": 99}


def test_fresh_install_creates_meta(role_env):
    result = user_data.migrate_if_needed()
    assert result["migrated"] is True
    assert user_data.read_meta()["layout"] == user_data.LAYOUT_VERSION
    assert (role_env / "data" / "1001").exists()
    # 全新安装没有旧文件可复制
    assert user_data.read_meta()["copied"] == []


def test_state_and_high_perf_isolated_between_roles(role_env):
    user_data.set_state(
        "task_progress", {"date": "d", "modes": {"修罗": 1}}, "1001")
    user_data.set_state(
        "task_progress", {"date": "d", "modes": {"五倍": 2}}, "2002")
    assert user_data.get_state("task_progress", "1001")["modes"] == {"修罗": 1}
    assert user_data.get_state("task_progress", "2002")["modes"] == {"五倍": 2}

    user_data.write_high_perf({"a": [1, 2, 3, 4]}, "1001")
    assert user_data.read_high_perf("1001") == {"a": [1, 2, 3, 4]}
    assert user_data.read_high_perf("2002") == {}


def test_default_adopted_by_first_real_role(role_env, monkeypatch):
    monkeypatch.setattr(user_data, "_probe_role_id",
                        lambda: user_data.DEFAULT_ROLE)
    _legacy(role_env)
    assert user_data.migrate_if_needed()["role"] == user_data.DEFAULT_ROLE
    assert (role_env / "data" / user_data.DEFAULT_ROLE / "task_stats.json").exists()

    # 首次绑定真实角色：认领 _default 的方案/成就/高性能缓存（只补缺）
    monkeypatch.setattr(user_data, "_probe_role_id", lambda: "1001")
    user_data._ROLE_CACHE.update(id=None, ts=0.0)
    result = user_data.migrate_if_needed()
    assert result["migrated"] is False
    assert user_data.read_meta()["adopted_by"] == "1001"
    cur = role_env / "data" / "1001"
    assert (cur / "task_stats.json").exists()
    assert (cur / "schemes.json").exists()
    assert (cur / "high_perf_cache.json").exists()
    # 1001 自己的 state 已在迁移时拆好，认领不覆盖
    assert user_data.read_state("1001")["task_progress"]["modes"] == {"修罗": 5}


def test_prune_legacy_config_backs_up_and_removes(role_env, monkeypatch):
    """两阶段清理：迁移后 config 旧键备份到 data/_legacy_config.json 再删除。"""
    from src import app_utils
    _legacy(role_env)
    user_data.migrate_if_needed()
    cfg = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
    expected = {k for k in user_data.LEGACY_CONFIG_KEYS if k in cfg}
    assert expected

    def fake_remove(keys):
        data = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
        n = 0
        for key in keys:
            if key in data:
                del data[key]
                n += 1
        (role_env / "config.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return n

    monkeypatch.setattr(app_utils, "kv_remove_many_merged", fake_remove)
    result = user_data.prune_legacy_config()
    assert result["pruned"] is True
    after = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
    assert not (set(user_data.LEGACY_CONFIG_KEYS) & set(after))
    backup = json.loads(
        (role_env / "data" / "_legacy_config.json").read_text(encoding="utf-8"))
    assert set(backup["keys"]) == expected
    assert backup["keys"]["high_perf_cache"] == {"btn": [800, 600, 1, 2]}
    meta = user_data.read_meta()
    assert meta["legacy_config_pruned"] is True
    assert meta["legacy_config_backup"] == "data/_legacy_config.json"
    # 幂等：第二次调用不再写
    assert user_data.prune_legacy_config() == {"pruned": False, "already": True}


def test_prune_without_legacy_keys_marks_done(role_env):
    user_data.migrate_if_needed()
    result = user_data.prune_legacy_config()
    assert result == {"pruned": False, "already": True}
    assert user_data.read_meta()["legacy_config_pruned"] is True


def test_legacy_stats_repaired_when_role_stats_empty(role_env):
    """迁移时成就未复制（目的文件为空）：下一次启动一次性补导。"""
    _legacy(role_env)
    user_data.migrate_if_needed()
    role_stats = role_env / "data" / "1001" / "task_stats.json"
    role_stats.write_text(json.dumps({
        "total": {"rounds": 0, "battles": 0, "seconds": 0, "modes": {}},
        "daily": {}}, ensure_ascii=False), encoding="utf-8")
    meta = user_data.read_meta()
    meta["stats_imported"] = False
    (role_env / "data" / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = user_data.migrate_if_needed()
    assert result["migrated"] is False
    assert result["stats_imported"] is True
    data = json.loads(role_stats.read_text(encoding="utf-8"))
    assert data["total"]["rounds"] == 9
    assert user_data.read_meta()["stats_imported"] is True


def test_stats_import_not_repeated_after_clear(role_env):
    """补导标记生效后，用户清空成就不再被旧文件重新导入。"""
    _legacy(role_env)
    user_data.migrate_if_needed()
    role_stats = role_env / "data" / "1001" / "task_stats.json"
    role_stats.write_text(json.dumps({
        "total": {"rounds": 0, "battles": 0, "seconds": 0, "modes": {}},
        "daily": {}}, ensure_ascii=False), encoding="utf-8")
    result = user_data.migrate_if_needed()
    assert result["stats_imported"] is False
    data = json.loads(role_stats.read_text(encoding="utf-8"))
    assert data["total"]["rounds"] == 0


def _patch_remove(monkeypatch, root):
    from src import app_utils

    def fake(keys):
        data = json.loads((root / "config.json").read_text(encoding="utf-8"))
        n = 0
        for key in keys:
            if key in data:
                del data[key]
                n += 1
        (root / "config.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return n
    monkeypatch.setattr(app_utils, "kv_remove_many_merged", fake)


def test_settings_migrated_to_role_and_pruned_two_phase(role_env, monkeypatch):
    """config 生效设置迁入角色 settings.json；下一次启动才从 config 删除。"""
    cfg = {
        "task_modes": ["修罗"],
        "task_count_修罗": 12,
        "use_sheyaoxiang": False,
        "silent_mode": False,
        "high_perf": True,
        "ui_theme": "dark",
        "bound_window_id": {"id": "（ID：1001）"},
        "task_progress": {"（ID：1001）": {"date": "d", "modes": {"修罗": 1}}},
    }
    _write_json(role_env / "config.json", cfg)
    _write_json(role_env / "schemes.json",
                {"active": "默认", "默认": {"task_modes": ["五环"]}})

    result = user_data.migrate_if_needed()
    assert result["settings_created"] is True
    settings = user_data.read_settings("1001")
    assert settings["task_modes"] == ["修罗"]
    assert settings["task_count_修罗"] == 12
    assert settings["use_sheyaoxiang"] is False
    assert settings["silent_mode"] is False
    # 迁移当次 config 仍保留设置键（回滚窗口）
    now = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
    assert now["task_modes"] == ["修罗"]

    _patch_remove(monkeypatch, role_env)
    user_data.prune_legacy_config(skip_settings=True)
    now = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
    assert "task_modes" in now  # 本轮跳过设置清理

    user_data.prune_legacy_config()
    now = json.loads((role_env / "config.json").read_text(encoding="utf-8"))
    assert "task_modes" not in now
    assert "task_count_修罗" not in now
    assert "use_sheyaoxiang" not in now
    assert now["ui_theme"] == "dark"          # 全局保留
    assert now["bound_window_id"]["id"] == "（ID：1001）"
    backup = json.loads(
        (role_env / "data" / "_legacy_config.json").read_text(encoding="utf-8"))
    assert backup["keys"]["task_modes"] == ["修罗"]
    assert backup["keys"]["task_count_修罗"] == 12
    # 角色设置不受 config 清理影响
    assert user_data.read_settings("1001")["task_modes"] == ["修罗"]


def test_archive_legacy_files_moves_to_legacy_dir(role_env):
    _legacy(role_env)
    user_data.migrate_if_needed()
    assert (role_env / "data" / "1001" / "schemes.json").exists()
    assert (role_env / "data" / "1001" / "task_stats.json").exists()

    result = user_data.archive_legacy_files()
    assert result["archived"] is True
    assert set(result["moved"]) == {"schemes.json", "task_stats.json"}
    assert not (role_env / "schemes.json").exists()
    assert not (role_env / "task_stats.json").exists()
    assert (role_env / "data" / "_legacy" / "schemes.json").exists()
    assert (role_env / "data" / "_legacy" / "task_stats.json").exists()
    meta = user_data.read_meta()
    assert meta["legacy_files_archived"] is True
    assert meta["legacy_files_archive"] == "data/_legacy"
    # 根文件已不在：幂等跳过
    assert user_data.archive_legacy_files()["archived"] is False


def test_archive_keeps_stats_when_role_stats_empty(role_env):
    _legacy(role_env)
    user_data.migrate_if_needed()
    role_stats = role_env / "data" / "1001" / "task_stats.json"
    role_stats.write_text(json.dumps({
        "total": {"rounds": 0, "battles": 0, "seconds": 0, "modes": {}},
        "daily": {}}, ensure_ascii=False), encoding="utf-8")
    meta = user_data.read_meta()
    meta["stats_imported"] = False
    (role_env / "data" / "_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    result = user_data.archive_legacy_files()
    assert "schemes.json" in result["moved"]
    assert "task_stats.json" not in result["moved"]
    assert (role_env / "task_stats.json").exists()  # 留给成就补导


# ---- 角色解析顺序：绑定记录优先（2026-11-13 组号 pass 切 Tab 漂移修复） ----

class _TitleWin:
    def __init__(self, title):
        self.title = title


def test_probe_role_prefers_binding_over_live_title(monkeypatch):
    """组号 pass 切到队员 Tab 时窗口标题短暂变成队员账号：绑定窗口仍在，
    角色仍取绑定记录（队长），不随标题漂移；未绑定才退回实时标题。"""
    from src import game
    monkeypatch.setattr(
        game, "bound_window_id",
        lambda: {"id": "（ID：286958560）", "hwnd": 1,
                 "title": "服务器 - 队长（ID：286958560）"})
    monkeypatch.setattr(
        game, "find_bound_window",
        lambda: _TitleWin("服务器 - 队员（ID：214369642）"))
    monkeypatch.setattr(
        game, "find_main_window",
        lambda: _TitleWin("服务器 - 队员（ID：214369642）"))
    assert _ORIGINAL_PROBE() == "286958560"

    # 未绑定：退回实时标题（首次运行/绑定丢失场景）
    monkeypatch.setattr(game, "bound_window_id", lambda: None)
    monkeypatch.setattr(game, "find_bound_window", lambda: None)
    assert _ORIGINAL_PROBE() == "214369642"


def test_probe_role_follows_live_window_when_bound_window_closed(monkeypatch):
    """绑定窗口已关闭（A 退出、打开 B）：绑定记录不再代表实际操作角色，
    跟随实时窗口标题解析（2026-11-13 面板方案/设置停在旧角色修复）。"""
    from src import game
    monkeypatch.setattr(
        game, "bound_window_id",
        lambda: {"id": "（ID：111）", "hwnd": 1,
                 "title": "服务器 - A（ID：111）"})
    monkeypatch.setattr(game, "find_bound_window", lambda: None)  # A 已关闭
    monkeypatch.setattr(
        game, "find_main_window",
        lambda: _TitleWin("服务器 - B（ID：222）"))
    assert _ORIGINAL_PROBE() == "222"


def test_probe_role_keeps_binding_without_any_game_window(monkeypatch):
    """游戏窗口全部关闭（重启客户端间隙）：沿用绑定记录，不落到 _default。"""
    from src import game
    monkeypatch.setattr(
        game, "bound_window_id",
        lambda: {"id": "（ID：111）", "hwnd": 1,
                 "title": "服务器 - A（ID：111）"})
    monkeypatch.setattr(game, "find_bound_window", lambda: None)
    monkeypatch.setattr(game, "find_main_window", lambda: None)
    assert _ORIGINAL_PROBE() == "111"


def test_probe_role_falls_back_to_default_without_binding_and_title(monkeypatch):
    from src import game
    monkeypatch.setattr(game, "bound_window_id", lambda: None)
    monkeypatch.setattr(game, "find_bound_window", lambda: None)
    monkeypatch.setattr(game, "find_main_window", lambda: _TitleWin("大话西游2经典版"))
    assert _ORIGINAL_PROBE() is None  # 由 current_role_id 退 _default
