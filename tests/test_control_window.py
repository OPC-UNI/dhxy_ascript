"""任务模式多选保存/读取的「仅战斗」互斥逻辑测试。

验证点：
- 混合列表（仅战斗 + 其它任务）→ 剔除仅战斗、保留普通任务
  （页面禁用互斥下正常不出现，兜底旧数据/方案导入）
- 仅战斗单独选中 → 保留
- 非法模式过滤不受影响
"""
import inspect
import re

from src import control_window
from src.control_window import (BATTLE_ONLY_MODE, TASK_MODES, _sanitize_modes,
                                _COUNT_KEYS, TASK_COUNT_DEFAULTS)


def test_all_exposed_methods_exist():
    """self.expose(self.X) 的每个 X 必须是类方法。

    __init__ 在窗口构造时立即执行 expose，缺失即启动崩溃（2026-11-12
    自动修理开关只加了模块级函数、漏类包装方法，启动 AttributeError）。
    """
    src = inspect.getsource(control_window.ControlWindow.__init__)
    names = set(re.findall(r"self\.expose\(self\.(\w+)\)", src))
    assert names  # 解析自检：正则失效时不得静默通过
    missing = sorted(n for n in names
                     if not hasattr(control_window.ControlWindow, n))
    assert missing == []


def test_sanitize_keeps_battle_only_when_alone():
    assert _sanitize_modes(["仅战斗"]) == [BATTLE_ONLY_MODE]


def test_sanitize_drops_battle_only_when_mixed():
    assert _sanitize_modes(["五环", "仅战斗"]) == ["五环"]
    assert _sanitize_modes(["仅战斗", "修罗", "五倍"]) == ["修罗", "五倍"]
    # 顺序按选择顺序保留（面板拖动排序，不再重排为 TASK_MODES 固定顺序）
    assert _sanitize_modes(["五倍", "仅战斗", "五环"]) == ["五倍", "五环"]


def test_sanitize_preserves_selection_order():
    """执行顺序 = 选择顺序：清洗不得重排（面板拖动排序依赖）。"""
    assert _sanitize_modes(["五倍", "五环", "客栈"]) == ["五倍", "五环", "客栈"]
    assert _sanitize_modes(["玄枢轶闻", "修罗"]) == ["玄枢轶闻", "修罗"]
    # 全量反转（含仅战斗混合 → 剔除仅战斗）后其余仍保持反转顺序
    rev = [m for m in TASK_MODES[::-1] if m != BATTLE_ONLY_MODE]
    assert _sanitize_modes(TASK_MODES[::-1]) == rev


def test_sanitize_filters_invalid_modes():
    assert _sanitize_modes(["五环", "不存在", "仅战斗"]) == ["五环"]
    assert _sanitize_modes(["五环", "不存在", "五倍"]) == ["五环", "五倍"]
    assert _sanitize_modes(["不存在"]) == []
    assert _sanitize_modes([]) == []


def test_sanitize_single_mode_each_valid():
    for mode in TASK_MODES:
        assert _sanitize_modes([mode]) == [mode]


def test_settings_spec_covers_all_task_modes():
    """方案快照按 _COUNT_KEYS 展开时不得缺键——「仅战斗」加入
    TASK_MODES 后 TASK_COUNT_DEFAULTS 漏键导致切换方案 KeyError
    （2026-11-04 面板报错）的回归测试。"""
    from src import schemes
    spec = schemes._settings_spec()
    for mode, key in _COUNT_KEYS.items():
        assert key in spec
        assert spec[key] == TASK_COUNT_DEFAULTS[mode]


def _patch_kv_empty(monkeypatch):
    """模拟 KeyValue 未写入状态：get 一律返回默认值（测试不得依赖
    真实 config.json——用户在面板改过开关后断言「首次默认」会误报）。"""
    from ascript.windows.system.key_value import KeyValue
    monkeypatch.setattr(KeyValue, "get", lambda key, default=None: default)


def test_settings_spec_rejoin_and_check_team_defaults(monkeypatch):
    """自动归队与检查队伍键在方案快照默认值表中存在且默认开启
    （load_* 首次默认与 _settings_spec 必须一致，否则方案对比/
    导入导出把默认值判成脏值）。"""
    from src import schemes
    from src.control_window import (_WUBEI_AUTO_REJOIN_KEY, _XIULUO_AUTO_REJOIN_KEY,
                                    _BATTLE_ONLY_AUTO_REJOIN_KEY,
                                    _WUBEI_CHECK_TEAM_KEY, _XIULUO_CHECK_TEAM_KEY,
                                    load_wubei_auto_rejoin, load_xiuluo_auto_rejoin,
                                    load_battle_only_auto_rejoin,
                                    load_wubei_check_team, load_xiuluo_check_team)
    spec = schemes._settings_spec()
    assert spec[_WUBEI_AUTO_REJOIN_KEY] is True
    assert spec[_XIULUO_AUTO_REJOIN_KEY] is True
    assert spec[_BATTLE_ONLY_AUTO_REJOIN_KEY] is True
    assert spec[_WUBEI_CHECK_TEAM_KEY] is True
    assert spec[_XIULUO_CHECK_TEAM_KEY] is True
    # load_* 首次默认同开（KeyValue 未写入时）
    _patch_kv_empty(monkeypatch)
    for key, load in ((_WUBEI_AUTO_REJOIN_KEY, load_wubei_auto_rejoin),
                      (_XIULUO_AUTO_REJOIN_KEY, load_xiuluo_auto_rejoin),
                      (_BATTLE_ONLY_AUTO_REJOIN_KEY, load_battle_only_auto_rejoin)):
        assert load() == spec[key]


def test_settings_spec_auto_create_team_defaults(monkeypatch):
    """自动创建队伍键在方案快照默认值表中存在且默认开启（修罗/五倍
    各一开关；load_* 首次默认与 _settings_spec 必须一致，2026-11-12
    用户改为默认开）。"""
    from src import schemes
    from src.control_window import (_WUBEI_AUTO_CREATE_TEAM_KEY,
                                    _XIULUO_AUTO_CREATE_TEAM_KEY,
                                    load_wubei_auto_create_team,
                                    load_xiuluo_auto_create_team)
    spec = schemes._settings_spec()
    assert spec[_WUBEI_AUTO_CREATE_TEAM_KEY] is True
    assert spec[_XIULUO_AUTO_CREATE_TEAM_KEY] is True
    _patch_kv_empty(monkeypatch)
    for key, load in ((_WUBEI_AUTO_CREATE_TEAM_KEY, load_wubei_auto_create_team),
                      (_XIULUO_AUTO_CREATE_TEAM_KEY, load_xiuluo_auto_create_team)):
        assert load() == spec[key]


def test_settings_spec_shout_and_tab_group_defaults(monkeypatch):
    """缺人自动喊话 / 组号专用默认开启（修罗/五倍/种族比武；load 与 spec 一致）。

    2026-11-12 用户确认：喊话 load 默认开但 spec 曾为关闭（新角色实际
    关、老角色缺键实际开），本次统一为开；种族比武喊话同日加入；
    组号专用默认开（已登录 Tab <2 的单号不激活，行为与关闭一致）。
    """
    from src import schemes
    from src.control_window import (
        _XIULUO_SHOUT_SWITCH_KEY, _WUBEI_SHOUT_SWITCH_KEY,
        _ZHONGZU_SHOUT_SWITCH_KEY,
        _XIULUO_TAB_GROUP_KEY, _WUBEI_TAB_GROUP_KEY,
        load_xiuluo_shout_switch, load_wubei_shout_switch,
        load_zhongzu_shout_switch,
        load_xiuluo_tab_group, load_wubei_tab_group)
    spec = schemes._settings_spec()
    for key in (_XIULUO_SHOUT_SWITCH_KEY, _WUBEI_SHOUT_SWITCH_KEY,
                _ZHONGZU_SHOUT_SWITCH_KEY,
                _XIULUO_TAB_GROUP_KEY, _WUBEI_TAB_GROUP_KEY):
        assert spec[key] is True
    _patch_kv_empty(monkeypatch)
    for key, load in ((_XIULUO_SHOUT_SWITCH_KEY, load_xiuluo_shout_switch),
                      (_WUBEI_SHOUT_SWITCH_KEY, load_wubei_shout_switch),
                      (_ZHONGZU_SHOUT_SWITCH_KEY, load_zhongzu_shout_switch),
                      (_XIULUO_TAB_GROUP_KEY, load_xiuluo_tab_group),
                      (_WUBEI_TAB_GROUP_KEY, load_wubei_tab_group)):
        assert load() == spec[key]


def test_shout_text_settings_defaults_and_save():
    """喊话内容设置（2026-11-12 用户要求可修改保存）：三任务默认内容与
    方案快照一致；保存去首尾空白、空内容恢复默认、超长截断。"""
    from src import schemes
    from src.control_window import (
        _XIULUO_SHOUT_TEXT_KEY, _WUBEI_SHOUT_TEXT_KEY, _ZHONGZU_SHOUT_TEXT_KEY,
        XIULUO_SHOUT_TEXT_DEFAULT, WUBEI_SHOUT_TEXT_DEFAULT,
        ZHONGZU_SHOUT_TEXT_DEFAULT, SHOUT_TEXT_MAX_LEN,
        load_xiuluo_shout_text, load_wubei_shout_text, load_zhongzu_shout_text,
        save_xiuluo_shout_text, save_wubei_shout_text, save_zhongzu_shout_text)
    spec = schemes._settings_spec()
    assert spec[_XIULUO_SHOUT_TEXT_KEY] == XIULUO_SHOUT_TEXT_DEFAULT
    assert spec[_WUBEI_SHOUT_TEXT_KEY] == WUBEI_SHOUT_TEXT_DEFAULT
    assert spec[_ZHONGZU_SHOUT_TEXT_KEY] == ZHONGZU_SHOUT_TEXT_DEFAULT
    # 未写入时 load 返回默认
    assert load_xiuluo_shout_text() == XIULUO_SHOUT_TEXT_DEFAULT
    assert load_wubei_shout_text() == WUBEI_SHOUT_TEXT_DEFAULT
    assert load_zhongzu_shout_text() == ZHONGZU_SHOUT_TEXT_DEFAULT
    # 保存生效：去首尾空白
    assert save_xiuluo_shout_text("  百次修罗速度队  ")["text"] == "百次修罗速度队"
    assert load_xiuluo_shout_text() == "百次修罗速度队"
    save_wubei_shout_text("五倍来人群")
    assert load_wubei_shout_text() == "五倍来人群"
    save_zhongzu_shout_text("比武来人群")
    assert load_zhongzu_shout_text() == "比武来人群"
    # 清空保存恢复默认，并回传生效内容（UI 输入框同步显示）
    assert save_wubei_shout_text("   ") == {
        "status": "success", "text": WUBEI_SHOUT_TEXT_DEFAULT}
    assert load_wubei_shout_text() == WUBEI_SHOUT_TEXT_DEFAULT
    # 超长内容截断到上限
    long_text = "喊" * (SHOUT_TEXT_MAX_LEN + 10)
    assert save_zhongzu_shout_text(long_text)["text"] == "喊" * SHOUT_TEXT_MAX_LEN
    assert load_zhongzu_shout_text() == "喊" * SHOUT_TEXT_MAX_LEN


# ---- 组号成员战斗动作持久化（2026-11-07） ----


def _patch_kv_store(monkeypatch, data=None):
    """KeyValue 内存存储打桩：get 读 store、kv_save_merged 写 store
    （save_group_actions 的合并写路径）。"""
    from ascript.windows.system.key_value import KeyValue
    from src import control_window
    store = dict(data or {})
    monkeypatch.setattr(KeyValue, "get",
                        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(control_window.app_utils, "kv_save_merged",
                        lambda key, value: store.__setitem__(key, value))
    return store


def _patch_role_store(monkeypatch, data=None):
    """角色 state.json 存储打桩：组号动作/快照按角色隔离（2026-11-11）。"""
    from src import control_window
    from src import user_data
    store = dict(data or {})
    monkeypatch.setattr(control_window.app_utils, "role_state_get",
                        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(control_window.app_utils, "role_state_set",
                        lambda key, value: store.__setitem__(key, value) or True)
    monkeypatch.setattr(user_data, "is_migrated", lambda: True)
    return store


def test_save_group_actions_merges_without_pruning(monkeypatch):
    """保存合并写入：已有账号条目保留（队员换队长/队伍变动后旧设置
    不丢，2026-11-07 需求——CDE 第一天设置、第二天直接读出）。"""
    from src import control_window
    store = _patch_role_store(monkeypatch, {
        "group_member_actions": {
            "oldA": {"char": "防御", "beast": "无"}}})
    r = control_window.save_group_actions({
        "idB": {"char": "攻击", "beast": "防御"}})
    assert r == {"status": "success"}
    saved = store["group_member_actions"]
    assert saved["oldA"] == {"char": "防御", "beast": "无"}
    assert saved["idB"] == {"char": "攻击", "beast": "防御"}
    # 同账号再存覆盖旧值
    r = control_window.save_group_actions({
        "idB": {"char": "技能2-3", "beast": "攻击"}})
    assert r == {"status": "success"}
    assert store["group_member_actions"]["idB"] == {"char": "技能2-3",
                                                    "beast": "攻击"}


def test_save_group_actions_rejects_invalid(monkeypatch):
    """任一值非法拒绝整单（UI 下拉只出合法值，此为双保险）。"""
    from src import control_window
    store = _patch_role_store(monkeypatch)
    r = control_window.save_group_actions({
        "idB": {"char": "乱写", "beast": "攻击"}})
    assert r["status"] == "error"
    assert "group_member_actions" not in store
    r = control_window.save_group_actions({"idB": "不是字典"})
    assert r["status"] == "error"
    r = control_window.save_group_actions(["不是字典"])
    assert r["status"] == "error"


def test_load_group_actions_filters_bad_entries(monkeypatch):
    """读取过滤非法条目（旧数据/手工改坏文件自愈），账号 ID 归一为
    字符串。"""
    from src import control_window
    _patch_role_store(monkeypatch, {
        "group_member_actions": {
            12345: {"char": "攻击", "beast": "防御"},
            "bad": {"char": "乱写", "beast": "攻击"},
            "bad2": {"char": "攻击"},
            "notdict": "x",
        }})
    result = control_window.load_group_actions()
    assert result == {"12345": {"char": "攻击", "beast": "防御"}}


# ---- 队伍检查快照：组号页打开自动展示（2026-11-11 用户需求） ----


def test_save_group_last_scan_keeps_only_in_team(monkeypatch):
    """快照只存队长与确认在队伍中的队员（不在队伍账号即使设置过动作
    也不进快照）；面板没读到（panel_read=False）不覆盖上次快照。"""
    from src import control_window
    store = _patch_role_store(monkeypatch)
    data = {"panel_read": True,
            "leader": {"id": "idL", "name": "队长甲"},
            "members": [
                {"id": "idM1", "name": "队员乙", "in_team": True},
                {"id": "idM2", "name": "路人丙", "in_team": False}]}
    ts = control_window.save_group_last_scan(data)
    assert ts and store["group_last_scan"]["leader"]["id"] == "idL"
    snap = store["group_last_scan"]
    assert snap["members"] == [{"id": "idM1", "name": "队员乙"}]
    assert snap["ts"] == ts
    # panel_read=False：不覆盖
    assert control_window.save_group_last_scan(
        {"panel_read": False, "leader": {"id": "x"}, "members": []}) is None
    assert store["group_last_scan"]["members"] == [{"id": "idM1", "name": "队员乙"}]


def test_build_group_last_scan_payload_merges_actions(monkeypatch):
    """组号页打开自动展示载荷：只含快照内（在队伍）的队员，战斗动作
    按账号 ID 实时读 group_member_actions。"""
    from src import control_window
    snap = {"ts": 1762800000.0,
            "leader": {"id": "idL", "name": "队长甲"},
            "members": [{"id": "idM1", "name": "队员乙"}]}
    _patch_role_store(monkeypatch, {
        "group_member_actions": {
            "idM1": {"char": "攻击", "beast": "防御"}}})
    monkeypatch.setattr(control_window, "load_group_last_scan", lambda: snap)
    r = control_window.build_group_last_scan_payload()
    assert r["ok"] and r["data"]["leader"]["id"] == "idL"
    assert r["data"]["leader"]["char"] is None
    assert r["data"]["members"] == [{"id": "idM1", "name": "队员乙",
                                     "in_team": True,
                                     "char": "攻击", "beast": "防御"}]
    assert r["data"]["snapshot_ts"] == snap["ts"]
    assert "上次检查" in r["info"]
    # 无快照：ok=False 引导手动检查
    monkeypatch.setattr(control_window, "load_group_last_scan", lambda: None)
    r2 = control_window.build_group_last_scan_payload()
    assert r2["status"] == "success" and r2["ok"] is False
    assert r2["data"] is None


# ---- 调试相关全局设置不进配置方案（2026-11-10 用户要求） ----


def test_settings_spec_excludes_debug_overlay():
    """调试信息开关放 config.json、不随方案走：切换方案不得改变
    「诊断」导航与悬浮层显隐，方案白名单不得包含该键。"""
    from src import schemes
    from src.control_window import _DEBUG_OVERLAY_KEY
    assert _DEBUG_OVERLAY_KEY not in schemes._settings_spec()


def test_snapshot_excludes_debug_globals(monkeypatch):
    """方案快照（切换/保存时写回）同样不得携带调试全局键，
    否则旧方案里残留的 debug_overlay 会随快照再次混入。"""
    from src import schemes
    from src.control_window import _DEBUG_OVERLAY_KEY, _DEBUG_UI_KEY
    _patch_kv_empty(monkeypatch)
    snap = schemes._snapshot_current()
    assert _DEBUG_OVERLAY_KEY not in snap
    assert _DEBUG_UI_KEY not in snap


def test_theme_is_global_not_in_scheme(monkeypatch):
    """界面主题为全局设置（2026-11-11 用户确认）：不进方案白名单/
    快照，切账号/切方案不改主题。"""
    from src import schemes
    from src.control_window import _UI_THEME_KEY
    assert _UI_THEME_KEY not in schemes._settings_spec()
    _patch_kv_empty(monkeypatch)
    snap = schemes._snapshot_current()
    assert _UI_THEME_KEY not in snap


def test_debug_ui_unlock_save_load(monkeypatch):
    """「显示与调试」卡片解锁标记：默认关闭、保存后读回开启
    （全局键，写入 config.json）。"""
    from src import control_window
    store = _patch_kv_store(monkeypatch)
    assert control_window.load_debug_ui_unlocked() is False
    r = control_window.save_debug_ui_unlocked(True)
    assert r == {"status": "success"}
    assert store[control_window._DEBUG_UI_KEY] is True
    assert control_window.load_debug_ui_unlocked() is True
