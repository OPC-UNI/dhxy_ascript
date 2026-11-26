"""任务模式多选保存/读取的「仅战斗」互斥逻辑测试。

验证点：
- 混合列表（仅战斗 + 其它任务）→ 剔除仅战斗、保留普通任务
  （页面禁用互斥下正常不出现，兜底旧数据/方案导入）
- 仅战斗单独选中 → 保留
- 非法模式过滤不受影响
"""
import inspect
import re

from src.app import control_window, message_api
from src.core import settings
from src.core.settings import (BATTLE_ONLY_MODE, TASK_MODES, _sanitize_modes,
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
    （2026-11-04 面板报错）的回归测试。

    默认次数用独立字面量锁定：若直接与实现同源的 TASK_COUNT_DEFAULTS
    比较，测试恒真，抓不到表漏键/串键。
    """
    from src.core import schemes
    expected = {
        "五环": 2, "客栈": 20, "修罗": 90, "五倍": 100,
        "种族比武": 10, "玄枢轶闻": 5, "名望": 5, "仅战斗": 0,
    }
    assert set(TASK_COUNT_DEFAULTS) == set(TASK_MODES)
    spec = schemes._settings_spec()
    for mode in TASK_MODES:
        key = _COUNT_KEYS[mode]
        assert key in spec
        assert spec[key] == expected[mode]


def _patch_kv_empty(monkeypatch):
    """模拟 KeyValue 未写入状态：get 一律返回默认值（测试不得依赖
    真实 config.json——用户在面板改过开关后断言「首次默认」会误报）。"""
    from ascript.windows.system.key_value import KeyValue
    monkeypatch.setattr(KeyValue, "get", lambda key, default=None: default)


def test_settings_spec_rejoin_and_check_team_defaults(monkeypatch):
    """自动归队与检查队伍键在方案快照默认值表中存在且默认开启
    （load_* 首次默认与 _settings_spec 必须一致，否则方案对比/
    导入导出把默认值判成脏值）。"""
    from src.core import schemes
    from src.core.settings import (
        _WUBEI_AUTO_REJOIN_KEY, _XIULUO_AUTO_REJOIN_KEY, _BATTLE_ONLY_AUTO_REJOIN_KEY,
        _WUBEI_CHECK_TEAM_KEY, _XIULUO_CHECK_TEAM_KEY, load_wubei_auto_rejoin,
        load_xiuluo_auto_rejoin, load_battle_only_auto_rejoin, load_wubei_check_team,
        load_xiuluo_check_team,
    )
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


def test_battle_only_hezi_default_off_and_roundtrip():
    """仅战斗自动抢盒子（2026-11-15 用户需求）：方案快照注册键、首次
    默认关闭（与 load_* 一致），保存后可读回。"""
    from src.core import schemes
    from src.core.settings import (
        _BATTLE_ONLY_HEZI_KEY, load_battle_only_hezi, save_battle_only_hezi,
    )
    spec = schemes._settings_spec()
    assert spec[_BATTLE_ONLY_HEZI_KEY] is False
    assert load_battle_only_hezi() is False
    assert save_battle_only_hezi(True)["status"] == "success"
    assert load_battle_only_hezi() is True
    assert save_battle_only_hezi(False)["status"] == "success"
    assert load_battle_only_hezi() is False


def test_wubei_manual_nav_default_and_roundtrip():
    """五倍兼容无快捷寻路：方案快照注册键、首次默认开启、保存可读回。"""
    from src.core import schemes
    from src.core.settings import (
        _WUBEI_MANUAL_NAV_KEY, load_wubei_manual_nav, save_wubei_manual_nav,
    )
    spec = schemes._settings_spec()
    assert spec[_WUBEI_MANUAL_NAV_KEY] is True
    assert load_wubei_manual_nav() is True
    assert save_wubei_manual_nav(False)["status"] == "success"
    assert load_wubei_manual_nav() is False
    assert save_wubei_manual_nav(True)["status"] == "success"
    assert load_wubei_manual_nav() is True


def test_wubei_force_manual_default_off_and_roundtrip():
    """五倍强制手动寻路（测试用，与修罗同款）：方案快照注册键、
    首次默认关闭、保存可读回。"""
    from src.core import schemes
    from src.core.settings import (
        _WUBEI_FORCE_MANUAL_KEY, load_wubei_force_manual,
        save_wubei_force_manual,
    )
    spec = schemes._settings_spec()
    assert spec[_WUBEI_FORCE_MANUAL_KEY] is False
    assert load_wubei_force_manual() is False
    assert save_wubei_force_manual(True)["status"] == "success"
    assert load_wubei_force_manual() is True
    assert save_wubei_force_manual(False)["status"] == "success"
    assert load_wubei_force_manual() is False


def test_settings_spec_kick_away_defaults(monkeypatch):
    """暂离超时自动请离开关/分钟（修罗/五倍各一套）：方案快照默认开启、
    默认 5 分钟；load_* 首次默认与 spec 一致；保存越界拒绝。"""
    from src.core import schemes
    from src.core.settings import (
        _XIULUO_AUTO_KICK_AWAY_KEY, _XIULUO_KICK_AWAY_MINUTES_KEY,
        _WUBEI_AUTO_KICK_AWAY_KEY, _WUBEI_KICK_AWAY_MINUTES_KEY,
        load_xiuluo_auto_kick_away, load_xiuluo_kick_away_minutes,
        save_xiuluo_auto_kick_away, save_xiuluo_kick_away_minutes,
        load_wubei_auto_kick_away, load_wubei_kick_away_minutes,
        save_wubei_auto_kick_away, save_wubei_kick_away_minutes,
    )
    spec = schemes._settings_spec()
    for switch_key, minutes_key in (
            (_XIULUO_AUTO_KICK_AWAY_KEY, _XIULUO_KICK_AWAY_MINUTES_KEY),
            (_WUBEI_AUTO_KICK_AWAY_KEY, _WUBEI_KICK_AWAY_MINUTES_KEY)):
        assert spec[switch_key] is True
        assert spec[minutes_key] == 5
    _patch_kv_empty(monkeypatch)
    for load_switch, load_minutes, save_switch, save_minutes in (
            (load_xiuluo_auto_kick_away, load_xiuluo_kick_away_minutes,
             save_xiuluo_auto_kick_away, save_xiuluo_kick_away_minutes),
            (load_wubei_auto_kick_away, load_wubei_kick_away_minutes,
             save_wubei_auto_kick_away, save_wubei_kick_away_minutes)):
        assert load_switch() is True
        assert load_minutes() == 5
        assert save_minutes(0)["status"] == "error"
        assert save_minutes(61)["status"] == "error"
        assert save_minutes(8) == {"status": "success", "interval": 8}
        assert load_minutes() == 8
        assert save_switch(False) == {"status": "success"}
        assert load_switch() is False


def test_settings_spec_auto_create_team_defaults(monkeypatch):
    """自动创建队伍键在方案快照默认值表中存在且默认开启（修罗/五倍
    各一开关；load_* 首次默认与 _settings_spec 必须一致，2026-11-12
    用户改为默认开）。"""
    from src.core import schemes
    from src.core.settings import (
        _WUBEI_AUTO_CREATE_TEAM_KEY, _XIULUO_AUTO_CREATE_TEAM_KEY,
        load_wubei_auto_create_team, load_xiuluo_auto_create_team,
    )
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
    from src.core import schemes
    from src.core.settings import (
        _XIULUO_SHOUT_SWITCH_KEY, _WUBEI_SHOUT_SWITCH_KEY, _ZHONGZU_SHOUT_SWITCH_KEY,
        _XIULUO_TAB_GROUP_KEY, _WUBEI_TAB_GROUP_KEY, load_xiuluo_shout_switch,
        load_wubei_shout_switch, load_zhongzu_shout_switch, load_xiuluo_tab_group,
        load_wubei_tab_group,
    )
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
    from src.core import schemes
    from src.core.settings import (
        _XIULUO_SHOUT_TEXT_KEY, _WUBEI_SHOUT_TEXT_KEY, _ZHONGZU_SHOUT_TEXT_KEY,
        XIULUO_SHOUT_TEXT_DEFAULT, WUBEI_SHOUT_TEXT_DEFAULT,
        ZHONGZU_SHOUT_TEXT_DEFAULT, SHOUT_TEXT_MAX_LEN, load_xiuluo_shout_text,
        load_wubei_shout_text, load_zhongzu_shout_text, save_xiuluo_shout_text,
        save_wubei_shout_text, save_zhongzu_shout_text,
    )
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
    from src.app import control_window
    store = dict(data or {})
    monkeypatch.setattr(KeyValue, "get",
                        lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(control_window.app_utils, "kv_save_merged",
                        lambda key, value: store.__setitem__(key, value))
    return store


def _patch_role_store(monkeypatch, data=None):
    """角色 state.json 存储打桩：组号动作/快照按角色隔离（2026-11-11）。"""
    from src.app import control_window
    from src.core import user_data
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
    from src.app import control_window
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
    from src.app import control_window
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
    from src.app import control_window
    _patch_role_store(monkeypatch, {
        "group_member_actions": {
            12345: {"char": "攻击", "beast": "防御"},
            "bad": {"char": "乱写", "beast": "攻击"},
            "bad2": {"char": "攻击"},
            "notdict": "x",
        }})
    result = settings.load_group_actions()
    assert result == {"12345": {"char": "攻击", "beast": "防御"}}


# ---- 队伍检查快照：组号页打开自动展示（2026-11-11 用户需求） ----


def test_save_group_last_scan_keeps_only_in_team(monkeypatch):
    """快照只存队长与确认在队伍中的队员（不在队伍账号即使设置过动作
    也不进快照）；面板没读到（panel_read=False）不覆盖上次快照。"""
    from src.app import control_window
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
    from src.app import control_window
    snap = {"ts": 1762800000.0,
            "leader": {"id": "idL", "name": "队长甲"},
            "members": [{"id": "idM1", "name": "队员乙"}]}
    _patch_role_store(monkeypatch, {
        "group_member_actions": {
            "idM1": {"char": "攻击", "beast": "防御"}}})
    from src.core import settings as core_settings
    monkeypatch.setattr(core_settings, "load_group_last_scan", lambda: snap)
    r = control_window.build_group_last_scan_payload()
    assert r["ok"] and r["data"]["leader"]["id"] == "idL"
    assert r["data"]["leader"]["char"] is None
    assert r["data"]["members"] == [{"id": "idM1", "name": "队员乙",
                                     "in_team": True,
                                     "char": "攻击", "beast": "防御"}]
    assert r["data"]["snapshot_ts"] == snap["ts"]
    assert "上次检查" in r["info"]
    # 无快照：ok=False 引导手动检查
    monkeypatch.setattr(core_settings, "load_group_last_scan", lambda: None)
    r2 = control_window.build_group_last_scan_payload()
    assert r2["status"] == "success" and r2["ok"] is False
    assert r2["data"] is None


# ---- 调试相关全局设置不进配置方案（2026-11-10 用户要求） ----


def test_settings_spec_excludes_debug_overlay():
    """调试信息开关放 config.json、不随方案走：切换方案不得改变
    「诊断」导航与悬浮层显隐，方案白名单不得包含该键。"""
    from src.core import schemes
    from src.core.settings import (
        _DEBUG_OVERLAY_KEY,
    )
    assert _DEBUG_OVERLAY_KEY not in schemes._settings_spec()


def test_snapshot_excludes_debug_globals(monkeypatch):
    """方案快照（切换/保存时写回）同样不得携带调试全局键，
    否则旧方案里残留的 debug_overlay 会随快照再次混入。"""
    from src.core import schemes
    from src.core.settings import (
        _DEBUG_OVERLAY_KEY, _DEBUG_UI_KEY,
    )
    _patch_kv_empty(monkeypatch)
    snap = schemes._snapshot_current()
    assert _DEBUG_OVERLAY_KEY not in snap
    assert _DEBUG_UI_KEY not in snap


def test_theme_is_global_not_in_scheme(monkeypatch):
    """界面主题为全局设置（2026-11-11 用户确认）：不进方案白名单/
    快照，切账号/切方案不改主题。"""
    from src.core import schemes
    from src.core.settings import (
        _UI_THEME_KEY,
    )
    assert _UI_THEME_KEY not in schemes._settings_spec()
    _patch_kv_empty(monkeypatch)
    snap = schemes._snapshot_current()
    assert _UI_THEME_KEY not in snap


def test_debug_ui_unlock_save_load(monkeypatch):
    """「显示与调试」卡片解锁标记：默认关闭、保存后读回开启
    （全局键，写入 config.json）。"""
    from src.app import control_window
    store = _patch_kv_store(monkeypatch)
    assert control_window.load_debug_ui_unlocked() is False
    r = settings.save_debug_ui_unlocked(True)
    assert r == {"status": "success"}
    assert store[settings._DEBUG_UI_KEY] is True
    assert control_window.load_debug_ui_unlocked() is True


# ---- 任务启动 Tab 对齐绑定（2026-11-13 组号 pass 切 Tab 角色漂移修复） ----

class _TitleWin:
    def __init__(self, title):
        self.title = title


def _patch_binding_env(monkeypatch, title, bound):
    from src.core import app_utils as au
    from src.device import game
    from src.core import user_data
    saved, refreshed = [], []
    monkeypatch.setattr(game, "find_main_window", lambda: _TitleWin(title))
    monkeypatch.setattr(game, "bound_window_id", lambda: bound)
    monkeypatch.setattr(game, "save_bound_window", lambda win: saved.append(win))
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: refreshed.append(refresh))
    monkeypatch.setattr(au, "log", lambda *args, **kwargs: None)
    return saved, refreshed


def test_start_tab_binding_follows_visible_tab(monkeypatch):
    """用户手动切到另一个 Tab 后开任务：绑定跟随启动所在 Tab（组号队长
    候选 = 可见 Tab），并立即刷新角色缓存。"""
    saved, refreshed = _patch_binding_env(
        monkeypatch, "服务器 - 队员（ID：214369642）",
        {"id": "（ID：286958560）", "hwnd": 1})
    control_window.ControlWindow._sync_start_tab_binding(None)
    assert len(saved) == 1
    assert refreshed == [True]


def test_start_tab_binding_noop_when_same_role_or_unlogged(monkeypatch):
    saved, refreshed = _patch_binding_env(
        monkeypatch, "服务器 - 队员（ID：214369642）",
        {"id": "（ID：214369642）", "hwnd": 1})
    control_window.ControlWindow._sync_start_tab_binding(None)
    assert saved == [] and refreshed == []
    # 未登录（标题无 ID）：不改绑定
    saved, refreshed = _patch_binding_env(monkeypatch, "大话西游2经典版", None)
    control_window.ControlWindow._sync_start_tab_binding(None)
    assert saved == [] and refreshed == []


# ---- 运行期换号：绑定窗口关闭后跟随实际窗口（2026-11-13） ----

class _LogStub:
    def __init__(self):
        self.lines = []

    def add_logs(self, lines):
        self.lines.extend(lines)


def test_sync_role_binding_refreshes_role_after_rebind(monkeypatch):
    """绑定自动改绑成功后立即刷新角色缓存（不等 30s），并写面板/文件日志。"""
    from src.core import app_utils as au
    from src.device import game
    from src.core import user_data
    win = _TitleWin("服务器 - B（ID：222）")
    logs = []
    monkeypatch.setattr(game, "ensure_binding_follows_live_window",
                        lambda: (win, True))
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: logs.append(("role", refresh)))
    monkeypatch.setattr(au, "log", lambda *args, **kwargs: None)
    stub = _LogStub()
    assert control_window.ControlWindow._sync_role_binding(stub) is True
    assert ("role", True) in logs
    assert any("自动改绑" in line for line in stub.lines)


def test_sync_role_binding_noop_without_rebind(monkeypatch):
    """绑定窗口仍在：不刷新角色缓存、不打日志。"""
    from src.core import app_utils as au
    from src.device import game
    from src.core import user_data
    calls = []
    monkeypatch.setattr(game, "ensure_binding_follows_live_window",
                        lambda: (_TitleWin("服务器 - A（ID：111）"), False))
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: calls.append(refresh))
    monkeypatch.setattr(au, "log", lambda *args, **kwargs: None)
    stub = _LogStub()
    assert control_window.ControlWindow._sync_role_binding(stub) is False
    assert calls == [] and stub.lines == []


def test_get_role_info_returns_id_and_label(monkeypatch):
    """页面轮询契约：get_role_info 返回 {id,label}（与 get_init_data.role
    基线比对，变化时页面全量重同步方案/设置）。角色强制重算（refresh=
    True），游戏关闭/启动的未绑定切换在轮询周期内生效（2026-11-19）。"""
    from src.core import user_data
    from src.app import window_registry

    class _Stub:
        def _sync_role_binding(self):
            return False

    # 多窗口：选中角色会持久化到 config（跨会话保留），未绑定断言需显式
    # 清空选中；否则本机跑过面板后测试会读到上次选中的角色。
    monkeypatch.setattr(window_registry, "selected_role", lambda: None)
    monkeypatch.setattr(window_registry, "selected_label", lambda: "")

    calls = []
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: calls.append(refresh) or "222")
    monkeypatch.setattr(control_window, "bound_short_label",
                        lambda: "一生所爱「长相思」")
    assert control_window.ControlWindow.get_role_info(_Stub()) == {
        "id": "222", "label": "一生所爱「长相思」"}
    assert calls == [True]

    # 游戏窗口全部关闭：id=_default、label 用可读的「未绑定」
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: user_data.DEFAULT_ROLE)
    monkeypatch.setattr(control_window, "bound_short_label", lambda: None)
    assert control_window.ControlWindow.get_role_info(_Stub()) == {
        "id": user_data.DEFAULT_ROLE, "label": "未绑定"}


def test_get_task_stats_label_empty_when_unbound(monkeypatch):
    """游戏窗口全部关闭（角色 = _default）：成就页不带旧绑定角色名
    （2026-11-19 用户反馈：未打开游戏时不得展示上次绑定角色的数据）。"""
    from src.core import debug_overlay, user_data
    from src.app import window_registry

    # 多窗口：选中角色持久化，清空后才能断言绑定文案路径。
    monkeypatch.setattr(window_registry, "selected_label", lambda: "")
    monkeypatch.setattr(debug_overlay, "read_task_stats",
                        lambda: {"total": {}, "daily": {}})
    monkeypatch.setattr(control_window, "bound_label_from_binding",
                        lambda: "一生所爱长相思")
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: user_data.DEFAULT_ROLE)
    data = control_window.ControlWindow.get_task_stats(object())
    assert data["role"] == {"id": user_data.DEFAULT_ROLE, "label": ""}

    # 绑定角色仍带短文案（原行为不变）
    monkeypatch.setattr(user_data, "current_role_id",
                        lambda refresh=False: "222")
    data = control_window.ControlWindow.get_task_stats(object())
    assert data["role"] == {"id": "222", "label": "一生所爱长相思"}


def test_env_info_unbound_when_no_game_window(monkeypatch):
    """游戏窗口全部关闭：系统页「绑定窗口」按未绑定展示，不回显上次
    绑定的窗口/角色（2026-11-19 用户反馈）。"""
    from src.device import game
    from src.app import window_registry

    class _Stub:
        def _sync_role_binding(self):
            return False

        def refresh_window_title(self):
            pass

    # 多窗口：选中角色持久化；未绑定断言需清空选中角色。
    monkeypatch.setattr(window_registry, "selected_role", lambda: None)
    monkeypatch.setattr(window_registry, "selected_label", lambda: "")

    monkeypatch.setattr(game, "find_main_window", lambda: None)
    monkeypatch.setattr(game, "find_bound_window", lambda: None)
    monkeypatch.setattr(game, "bound_window_id",
                        lambda: {"id": "（ID：111）", "hwnd": 1,
                                 "title": "服务器 - A（ID：111）"})
    info = control_window.ControlWindow.get_env_info(_Stub())
    assert info["bound"] == "未绑定（游戏未启动）"
    assert info["bound_full"] == "未绑定（游戏未启动）"
    assert info["bound_short"] == ""


# ---- 消息服务控制指令 host（/schemes /switch /start /stop 转调） ----

class _FakeWebview:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


class _FakeTaskManager:
    def is_running(self, role=None):
        return False


class _FakeControlWindow:
    def __init__(self):
        self.calls = []
        self.webview_instance = None
        self._tm = _FakeTaskManager()
        self.switch_result = {"status": "success", "active": "方案A"}

    def is_running(self, role=None):
        return False

    def is_fleet_starting(self):
        return False

    def get_fleet_state(self):
        self.calls.append("get_fleet_state")
        return {"rows": []}

    def get_schemes(self):
        self.calls.append("get_schemes")
        return {"active": "默认", "names": ["默认"]}

    def switch_scheme(self, name):
        self.calls.append(("switch_scheme", name))
        return dict(self.switch_result)

    def start_task(self):
        self.calls.append("start_task")
        return {"status": "success", "running": True}

    def stop_window(self, role_id, hwnd=None):
        self.calls.append(("stop_window", role_id))
        return {"status": "success", "running": False}

    def start_all(self):
        self.calls.append("start_all")
        return {"status": "success", "running": True, "starting": 1}

    def stop_all(self):
        self.calls.append("stop_all")
        return {"status": "success", "running": False}

    def select_window(self, role_id, hwnd=None):
        self.calls.append(("select_window", role_id, hwnd))
        return {"status": "success", "selected": role_id,
                "selected_hwnd": hwnd or 0}

    def _message_disband(self):
        self.calls.append("message_disband")
        return {"ok": True, "message": "已解散队伍"}

    def _message_shout(self, text=""):
        self.calls.append(("message_shout", text))
        return {"ok": True, "message": "已喊话"}


def test_message_host_delegates_control_commands(monkeypatch):
    """消息指令必须转调控制窗口入口（启动含授权校验，不绕过面板链路）。"""
    from src.app import window_registry

    monkeypatch.setattr(window_registry, "selected_role", lambda: "111")
    host = control_window._ControlMessageHost(_FakeControlWindow())
    assert host.list_schemes()["active"] == "默认"
    assert host.switch_scheme("方案A")["status"] == "success"
    assert host.start_task()["running"] is True
    assert host.stop_task()["running"] is False
    assert host.start_all()["running"] is True
    assert host.stop_all()["running"] is False
    assert host.is_any_running() is False
    assert host.fleet_state()["rows"] == []
    assert host.disband_team()["ok"] is True
    assert host.shout("来人")["ok"] is True
    assert host._cw.calls == ["get_schemes", ("switch_scheme", "方案A"),
                              "start_task", ("stop_window", "111"),
                              "start_all", "stop_all", "get_fleet_state",
                              "message_disband", ("message_shout", "来人")]


def test_message_host_select_window_refreshes_panel():
    """远程切窗口成功后推面板全量跟随（Tab 高亮/任务页/设置等）。"""
    cw = _FakeControlWindow()
    cw.webview_instance = _FakeWebview()
    host = control_window._ControlMessageHost(cw)
    assert host.select_window("111", 1)["status"] == "success"
    assert any("onRemoteWindowSelected" in script
               for script in cw.webview_instance.scripts)


def test_message_host_switch_refreshes_panel():
    """远程切方案成功 → 推 syncInitData 全量刷新；失败不推。"""
    cw = _FakeControlWindow()
    cw.webview_instance = _FakeWebview()
    host = control_window._ControlMessageHost(cw)
    host.switch_scheme("方案A")
    assert any("syncInitData" in script for script in cw.webview_instance.scripts)
    cw.webview_instance.scripts.clear()
    cw.switch_result = {"status": "error", "msg": "任务运行中，请先停止任务再操作"}
    host.switch_scheme("方案A")
    assert cw.webview_instance.scripts == []


# ---- 消息侧解散队伍 / 喊话（桩，不碰真实窗口） ----

class _FakeTm:
    def __init__(self, running=False):
        self._running = running

    def is_running(self, role=None):
        return self._running


class _MessageCwStub:
    """只带 _message_shout/_message_disband 所需属性的桩。"""

    def __init__(self, running=False):
        import threading
        self._game_op_lock = threading.Lock()
        self._tm = _FakeTm(running)
        self.events = []

    def _message_log(self, msg, level="INFO"):
        self.events.append((msg, level))


def test_message_shout_without_window_returns_error(monkeypatch):
    monkeypatch.setattr(control_window.game, "find_main_window", lambda: None)
    result = control_window.ControlWindow._message_shout(
        _MessageCwStub(), "来人")
    assert result["ok"] is False
    assert "未找到游戏窗口" in result["message"]


def test_message_shout_running_skips_tab_guard_and_notes(monkeypatch):
    """任务运行中也允许喊话：不切 Tab（防打断组号 pass），回复注明按当前 Tab。"""
    from src.device import silent_click, tab_group
    monkeypatch.setattr(control_window.game, "find_main_window", lambda: object())
    monkeypatch.setattr(message_api, "_window_minimized", lambda win: False)
    guard_calls = []
    monkeypatch.setattr(
        tab_group, "ensure_bound_tab",
        lambda win: (guard_calls.append(1), (True, "已在绑定角色 Tab"))[1])
    monkeypatch.setattr(control_window.app_utils, "silent_mode_on",
                        lambda: True)
    sent = []
    monkeypatch.setattr(
        silent_click, "send_silent_text",
        lambda win, text, end_enter=False:
            (sent.append(text), {"hwnd": 1})[1])
    result = control_window.ControlWindow._message_shout(
        _MessageCwStub(running=True), "来人")
    assert result["ok"] is True
    assert sent == ["来人"]
    assert guard_calls == []  # 运行中不切 Tab
    assert "按当前 Tab" in result["message"]


def test_message_shout_stopped_uses_tab_guard(monkeypatch):
    """任务未运行：先切回绑定角色 Tab，正常回复不带运行中提示。"""
    from src.device import silent_click, tab_group
    monkeypatch.setattr(control_window.game, "find_main_window", lambda: object())
    monkeypatch.setattr(message_api, "_window_minimized", lambda win: False)
    guard_calls = []
    monkeypatch.setattr(
        tab_group, "ensure_bound_tab",
        lambda win: (guard_calls.append(1), (True, "已在绑定角色 Tab"))[1])
    monkeypatch.setattr(control_window.app_utils, "silent_mode_on",
                        lambda: True)
    monkeypatch.setattr(silent_click, "send_silent_text",
                        lambda win, text, end_enter=False: {"hwnd": 1})
    result = control_window.ControlWindow._message_shout(
        _MessageCwStub(running=False), "来人")
    assert result["ok"] is True
    assert guard_calls == [1]
    assert "按当前 Tab" not in result["message"]


def test_message_shout_refused_when_minimized(monkeypatch):
    monkeypatch.setattr(control_window.game, "find_main_window",
                        lambda: object())
    monkeypatch.setattr(message_api, "_window_minimized", lambda win: True)
    result = control_window.ControlWindow._message_shout(
        _MessageCwStub(), "来人")
    assert result["ok"] is False
    assert "最小化" in result["message"]


def test_message_disband_refused_when_task_running():
    result = control_window.ControlWindow._message_disband(
        _MessageCwStub(running=True))
    assert result["ok"] is False
    assert "任务运行中" in result["message"]


def test_default_shout_text_follows_selected_modes(monkeypatch):
    stub = _MessageCwStub()
    monkeypatch.setattr(message_api, "load_selected_modes",
                        lambda: ["五环", "五倍"])
    monkeypatch.setattr(message_api, "load_wubei_shout_text",
                        lambda: "百次五倍来人，效率队")
    assert control_window.ControlWindow._default_shout_text(stub) == \
        "百次五倍来人，效率队"
    monkeypatch.setattr(message_api, "load_selected_modes", lambda: ["五环"])
    assert control_window.ControlWindow._default_shout_text(stub) == ""


def test_message_disband_runs_guard_then_team_helper(monkeypatch):
    """解散队伍：先绑定 Tab 护栏，再走队长解散/队员离开；结果透传。"""
    from src.device import tab_group, team
    monkeypatch.setattr(control_window.game, "find_main_window", lambda: object())
    monkeypatch.setattr(message_api, "_window_minimized", lambda win: False)
    calls = []
    monkeypatch.setattr(
        tab_group, "ensure_bound_tab",
        lambda win: (calls.append("guard"), (True, "已在绑定角色 Tab"))[1])
    monkeypatch.setattr(
        team, "leave_or_disband_team",
        lambda win: (calls.append("team"), (False, "面板未打开"))[1])
    result = control_window.ControlWindow._message_disband(_MessageCwStub())
    assert calls == ["guard", "team"]
    assert result["ok"] is False
    assert result["message"] == "面板未打开"


def test_message_shout_sends_truncated_silent_text(monkeypatch):
    """喊话：静默通道投递并追加回车，文本按喊话设置上限截断；换行折叠。"""
    from src.device import silent_click, tab_group
    from src.core import settings
    monkeypatch.setattr(control_window.game, "find_main_window", lambda: object())
    monkeypatch.setattr(message_api, "_window_minimized", lambda win: False)
    monkeypatch.setattr(tab_group, "ensure_bound_tab",
                        lambda win: (True, "已在绑定角色 Tab"))
    monkeypatch.setattr(control_window.app_utils, "silent_mode_on",
                        lambda: True)
    sent = []
    monkeypatch.setattr(
        silent_click, "send_silent_text",
        lambda win, text, end_enter=False:
            (sent.append((text, end_enter)), {"hwnd": 1})[1])
    long_text = "来" * 100
    stub = _MessageCwStub()
    result = control_window.ControlWindow._message_shout(stub, long_text)
    assert result["ok"] is True
    assert sent == [(("来" * settings.SHOUT_TEXT_MAX_LEN), True)]
    control_window.ControlWindow._message_shout(stub, "  来人\n第二行 ")
    assert sent[-1] == ("来人 第二行", True)  # 换行折叠成空格，防提前发送
    assert stub.events  # 已写面板/文件日志


def test_diagnostics_methods_live_in_mixin():
    """P3 拆分：全部诊断验证方法由 DiagnosticsMixin 提供，callPython 名不变。

    遍历 expose 列表中的所有 verify_*（消息服务 verify_message_channel
    除外），防止漏搬、搬回或形成两份实现；`test_all_exposed_methods_exist`
    继续兜底名称可解析。
    """
    from src.app import diagnostics
    from src.app.control_window import ControlWindow

    assert issubclass(ControlWindow, diagnostics.DiagnosticsMixin)
    src = inspect.getsource(ControlWindow.__init__)
    exposed = set(re.findall(r"self\.expose\(self\.(\w+)\)", src))
    diag = {n for n in exposed if n.startswith("verify_")} \
        - {"verify_message_channel"}
    assert len(diag) >= 40  # 解析自检：正则失效不得静默通过
    wrong = sorted(n for n in diag
                   if getattr(ControlWindow, n).__module__
                   != "src.app.diagnostics")
    assert wrong == []
    # 消息服务验证不属于诊断页，归消息 API mixin
    assert ControlWindow.verify_message_channel.__module__ \
        == "src.app.message_api"
    # control_window 仍导出共用前置检查（队伍检查方法在用）
    assert control_window._game_window_clickable \
        is diagnostics._game_window_clickable


def test_moved_verify_methods_no_window_paths(monkeypatch):
    """搬迁后的 verify 方法可调用：无窗口路径返回错误字典（不抛异常）。

    覆盖 mixin 对 `_SILENT_KEY_HOTKEYS`、`_tm`、`_verifying` 与 game 的
    依赖；方法若引用未导入的全局名，这里会直接 NameError。
    """
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self.webview_instance = None
            self.logs = []

        def add_logs(self, lines):
            self.logs.extend(lines)

    monkeypatch.setattr(diagnostics.game, "find_main_window", lambda: None)
    host = _Host()
    team_result = host.verify_team_check()
    assert team_result["status"] == "error"
    assert "未检测到游戏窗口" in team_result["msg"]
    assert host.verify_silent_key("bogus")["status"] == "error"
    assert host.verify_silent_click("bogus")["status"] == "error"


def test_verify_edge_target_guards(monkeypatch):
    """边缘目标查找：无窗口/验证中给出错误，不抛异常。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._verifying = False

    host = _Host()
    monkeypatch.setattr(diagnostics.game, "find_main_window", lambda: None)
    r = host.verify_edge_target()
    assert r["status"] == "error" and "未检测到游戏窗口" in r["msg"]
    host._verifying = True
    r = host.verify_edge_target()
    assert r["status"] == "error" and "验证进行中" in r["msg"]


def test_run_edge_target_verify_logs_ocr_when_template_missing(monkeypatch):
    """边缘目标查找：模板未命中 → 打印 OCR 名字行/点击点与修罗候选（只读）。"""
    import types

    from src.app import diagnostics
    from src.device import battle
    from src.device import map as game_map
    from src.device import world_target

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._verifying = True
            self.webview_instance = None
            self.logs = []

        def add_logs(self, lines):
            self.logs.extend(lines)

    host = _Host()
    monkeypatch.setattr(diagnostics.app_utils, "log", lambda *a, **k: None)
    monkeypatch.setattr(game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(battle, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(game_map, "match_frame_all", lambda *a, **k: [])
    monkeypatch.setattr(game_map, "best_match_np",
                        lambda *a, **k: (0.5, (10, 20)))
    lines = [("智慧魔尊", (510, 441, 578, 464), 0.995)]
    monkeypatch.setattr(world_target, "scan_name_lines", lambda img: lines)
    seen = {}

    def _suffix(img, suffixes, lines=None):
        seen["suffixes"] = suffixes
        seen["lines"] = lines
        return [("狂风修罗", (300, 260))]

    monkeypatch.setattr(world_target, "find_suffix_points", _suffix)
    win = types.SimpleNamespace(
        capture=lambda force_activate=False: "frame")
    host._run_edge_target_verify(win)
    text = "\n".join(host.logs)
    assert "五倍任务小牌" in text and "修罗任务小牌" in text
    assert "0 处" in text                     # 模板未命中
    assert "智慧魔尊" in text                 # 名字汇总行
    assert "点击点 (544,401)" not in text      # 不逐行打印明细（正式日志从简）
    assert "修罗「xx修罗」后缀候选 1 个" in text
    assert seen["lines"] is lines             # 同一次扫描复用，不重复 OCR
    assert seen["suffixes"] == ("修罗", "修羅")
    assert host._verifying is False


def test_run_edge_target_verify_template_hit_skips_ocr(monkeypatch):
    """边缘目标查找：任一任务小牌模板命中即结束，不再 OCR 名字。"""
    import types

    from src.app import diagnostics
    from src.device import battle
    from src.device import map as game_map
    from src.device import world_target

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._verifying = True
            self.webview_instance = None
            self.logs = []

        def add_logs(self, lines):
            self.logs.extend(lines)

    host = _Host()
    monkeypatch.setattr(diagnostics.app_utils, "log", lambda *a, **k: None)
    monkeypatch.setattr(game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(battle, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(game_map, "match_frame_all",
                        lambda *a, **k: [(0.95, 300, 260)])
    monkeypatch.setattr(game_map, "best_match_np",
                        lambda *a, **k: (0.95, (300, 260)))
    called = []
    monkeypatch.setattr(world_target, "scan_name_lines",
                        lambda img: called.append(1) or [])
    win = types.SimpleNamespace(
        capture=lambda force_activate=False: "frame")
    host._run_edge_target_verify(win)
    assert called == []
    assert "模板命中" in "\n".join(host.logs)
    assert host._verifying is False


def test_verify_edge_target_coord_inputs(monkeypatch):
    """边缘目标查找坐标输入校验：非数字/只填一边直接报错。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._verifying = False

    host = _Host()
    r = host.verify_edge_target("abc", "def")
    assert r["status"] == "error" and "数字" in r["msg"]
    r = host.verify_edge_target(40, "")
    assert r["status"] == "error" and "同时填写" in r["msg"]


def _edge_coord_env(monkeypatch, point):
    """装配边缘目标查找的坐标反查场景（模板命中，跳过 OCR）。"""
    import types

    from src.app import diagnostics
    from src.device import battle
    from src.device import map as game_map
    from src.device import map_nav

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._verifying = True
            self.webview_instance = None
            self.logs = []

        def add_logs(self, lines):
            self.logs.extend(lines)

    host = _Host()
    monkeypatch.setattr(diagnostics.app_utils, "log", lambda *a, **k: None)
    monkeypatch.setattr(game_map, "bgr_frame", lambda img: img)
    monkeypatch.setattr(battle, "is_in_battle_frame", lambda img: False)
    monkeypatch.setattr(game_map, "match_frame_all",
                        lambda *a, **k: [(0.95, 300, 260)])
    monkeypatch.setattr(game_map, "best_match_np",
                        lambda *a, **k: (0.95, (300, 260)))
    seen = {}

    def _coord(win, name, x, y, emit=None):
        seen["args"] = (name, x, y)
        if point is not None and emit:
            emit("坐标反查：角色 (38,50) 屏幕锚点 ≈(712,411) → "
                 "目标 (40,48) 屏幕点 ≈(754,427)")
        return point

    monkeypatch.setattr(map_nav, "special_coord_screen_point", _coord)
    win = types.SimpleNamespace(
        capture=lambda force_activate=False: "frame")
    return host, win, seen


def test_run_edge_target_verify_logs_coord_reverse(monkeypatch):
    """填目标坐标：额外打印特殊地图坐标反查屏幕点（只打印不点击）。"""
    host, win, seen = _edge_coord_env(monkeypatch, (754, 427))
    host._run_edge_target_verify(win, 40, 48)
    text = "\n".join(host.logs)
    assert "坐标反查屏幕点：(754, 427)" in text
    assert seen["args"] == (None, 40, 48)  # map_name=None → 按 HUD 地名匹配
    assert host._verifying is False


def test_run_edge_target_verify_coord_unavailable(monkeypatch):
    """坐标反查不可用：打印原因，模板结论照常输出。"""
    host, win, seen = _edge_coord_env(monkeypatch, None)
    host._run_edge_target_verify(win, 40, 48)
    text = "\n".join(host.logs)
    assert "坐标反查不可用" in text
    assert "模板命中" in text  # 模板结论不受影响


def test_site_nav_points_maps_known():
    """坐标化测试点位的地图必须都在导航地图表里（按钮点了不能报错）。"""
    from src.app import diagnostics
    from src.device import map_nav

    known = set(map_nav.all_navigate_maps())
    missing = {spec[1] for spec in diagnostics.SITE_NAV_POINTS.values()} - known
    assert missing == set()


def test_verify_site_navigate_guards(monkeypatch):
    """点位导航：未知点位/无窗口给出错误，不抛异常。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self._map_nav_running = False

    host = _Host()
    r = host.verify_site_navigate("bogus")
    assert r["status"] == "error" and "未知点位" in r["msg"]
    monkeypatch.setattr(diagnostics.game, "find_main_window", lambda: None)
    r = host.verify_site_navigate("repair")
    assert r["status"] == "error"
    assert "未检测到游戏窗口" in r["msg"]


def test_verify_site_navigate_starts_thread(monkeypatch):
    """点位导航线程参数：预置点位表 → (win, 地图, x, y, 点位 key)。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self._map_nav_running = False

    host = _Host()
    fake_win = object()
    monkeypatch.setattr(diagnostics.game, "find_main_window",
                        lambda: fake_win)
    monkeypatch.setattr(diagnostics, "_game_window_clickable",
                        lambda win: True)
    started = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started["target"] = target
            started["args"] = args

        def start(self):
            started["started"] = True

    monkeypatch.setattr(diagnostics.threading, "Thread", _FakeThread)
    r = host.verify_site_navigate("repair")
    assert r["status"] == "success"
    assert started["started"] is True
    assert started["args"] == (fake_win, "洛阳城", 321, 110, "repair")
    assert host._map_nav_running is True and host._verifying is True


def test_task_running_here_uses_selected_window(monkeypatch):
    """诊断按窗口放行：只查选中窗口角色，不再要求全部窗口停止。"""
    from src.app import diagnostics, window_registry
    from src.core import user_data

    class _TM:
        def __init__(self, running_role):
            self.running_role = running_role
            self.calls = []

        def is_running(self, role=None):
            self.calls.append(role)
            return role == self.running_role

    monkeypatch.setattr(window_registry, "selected_role", lambda: "123")
    tm = _TM("123")
    assert diagnostics._task_running_here(tm) is True
    assert tm.calls == ["123"]

    # 另一个窗口在跑、本窗口不在跑：不应阻塞（false 即放行）
    tm2 = _TM("999")
    assert diagnostics._task_running_here(tm2) is False
    assert tm2.calls == ["123"]

    # 未选中走当前角色
    monkeypatch.setattr(window_registry, "selected_role", lambda: None)
    monkeypatch.setattr(user_data, "current_role_id", lambda: "456")
    tm3 = _TM("456")
    assert diagnostics._task_running_here(tm3) is True
    assert tm3.calls == ["456"]

    # 都解析不到：回退全量判定（role=None，任一窗口在跑即拒绝）
    monkeypatch.setattr(user_data, "current_role_id", lambda: None)
    tm4 = _TM(None)
    assert diagnostics._task_running_here(tm4) is True
    assert tm4.calls == [None]


def test_demon_nav_kinds_cover_task_types():
    """四类降魔任务各一个按钮：kind 集合与 demon_task 常量一致。"""
    from src.app import diagnostics
    from src.device import demon_task

    expected = {demon_task.KIND_KILL, demon_task.KIND_HUANGPAO,
                demon_task.KIND_XIANXING, demon_task.KIND_RECLAIM}
    assert {spec[1] for spec in diagnostics.DEMON_NAV_KINDS.values()} == expected


def test_verify_demon_navigate_guards(monkeypatch):
    """降魔模拟：未知类型/无窗口给出错误，不抛异常。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self._map_nav_running = False

    host = _Host()
    r = host.verify_demon_navigate("bogus")
    assert r["status"] == "error" and "未知任务类型" in r["msg"]
    monkeypatch.setattr(diagnostics.game, "find_main_window", lambda: None)
    r = host.verify_demon_navigate("kill")
    assert r["status"] == "error"
    assert "未检测到游戏窗口" in r["msg"]


def test_verify_demon_navigate_starts_thread(monkeypatch):
    """降魔模拟线程参数：(win, kind)，启动后置忙标志。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self._map_nav_running = False

    host = _Host()
    fake_win = object()
    monkeypatch.setattr(diagnostics.game, "find_main_window",
                        lambda: fake_win)
    monkeypatch.setattr(diagnostics, "_game_window_clickable",
                        lambda win: True)
    started = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started["target"] = target
            started["args"] = args
            started["name"] = name

        def start(self):
            started["started"] = True

    monkeypatch.setattr(diagnostics.threading, "Thread", _FakeThread)
    r = host.verify_demon_navigate("huangpao")
    assert r["status"] == "success"
    assert started["started"] is True
    assert started["args"] == (fake_win, "huangpao")
    assert host._map_nav_running is True and host._verifying is True


def _demon_host(diagnostics):
    import types

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = True
            self._map_nav_running = True
            self.webview_instance = None
            self.logs = []
            self.notified = []

        def add_logs(self, lines):
            self.logs.extend(lines)

        def _notify_demon_nav_result(self, kind, ok, info):
            self.notified.append((kind, ok, info))

    return _Host()


def _mock_demon_thread(monkeypatch, rec, nav_results=None):
    """打桩：任务栏读取/关闭 + 导航；返回导航调用记录。"""
    from src.app import diagnostics
    from src.device import map_nav, task_panel

    monkeypatch.setattr(task_panel, "read_first_group_task",
                        lambda win: ({"recognition": rec}, "ok"))
    monkeypatch.setattr(task_panel, "close_task_panel",
                        lambda win: (True, "已关闭"))
    monkeypatch.setattr(map_nav, "all_navigate_maps",
                        lambda: [rec.get("map")] if rec.get("map") else [])
    calls = []
    results = list(nav_results or [])

    def fake_nav(win, name, x, y, log_fn=None, verbose=True):
        calls.append((name, x, y))
        return results.pop(0) if results else (True, "arrived")

    monkeypatch.setattr(map_nav, "navigate_to_map_coordinate", fake_nav)
    monkeypatch.setattr(diagnostics.app_utils, "log",
                        lambda *a, **k: None)
    host = _demon_host(diagnostics)
    return host, calls


def _kill_rec():
    return {"kind": "kill", "task": "三藏逢魔", "map": "万寿山",
            "coords": [(37, 11)], "coord_a": (37, 11), "coord_b": None,
            "target": "天眼魔尊"}


def test_run_demon_navigate_kill(monkeypatch):
    """击杀：识别 → 关闭任务栏 → 按地图/坐标寻路一次，到达即成功。"""
    host, calls = _mock_demon_thread(monkeypatch, _kill_rec())
    host._run_demon_navigate(object(), "kill")
    assert calls == [("万寿山", 37, 11)]
    assert host.notified and host.notified[0][0] == "kill"
    assert host.notified[0][1] is True
    assert "寻路完成" in host.notified[0][2]
    assert host._verifying is False and host._map_nav_running is False


def test_run_demon_navigate_type_mismatch(monkeypatch):
    """按钮类型与当前任务不符：不寻路，提示当前识别结果。"""
    rec = {"kind": "xianxing", "task": "宝象迷情", "map": "平顶山",
           "coords": [(187, 27), (269, 28)],
           "coord_a": (187, 27), "coord_b": (269, 28)}
    host, calls = _mock_demon_thread(monkeypatch, rec)
    host._run_demon_navigate(object(), "kill")
    assert calls == []
    kind, ok, info = host.notified[0]
    assert kind == "kill" and ok is False and "不符" in info


def test_run_demon_navigate_reclaim(monkeypatch):
    """暗雷：只识别并提示需重新领取，不寻路。"""
    rec = {"kind": "reclaim", "task": "殿前献艺", "reclaim": True,
           "reclaim_reason": "任务文案含「暗雷」，需重新领取替换"}
    host, calls = _mock_demon_thread(monkeypatch, rec)
    host._run_demon_navigate(object(), "reclaim")
    assert calls == []
    assert host.notified[0][1] is True
    assert "重新领取" in host.notified[0][2]


def test_run_demon_navigate_xianxing_ab(monkeypatch):
    """显形镜：依次寻路 A、B，两段都到达才算完成。"""
    rec = {"kind": "xianxing", "task": "宝象迷情", "map": "平顶山",
           "coords": [(187, 27), (269, 28)],
           "coord_a": (187, 27), "coord_b": (269, 28)}
    host, calls = _mock_demon_thread(monkeypatch, rec)
    host._run_demon_navigate(object(), "xianxing")
    assert calls == [("平顶山", 187, 27), ("平顶山", 269, 28)]
    assert host.notified[0][1] is True
    assert "A" in host.notified[0][2] and "B" in host.notified[0][2]


def test_run_demon_navigate_xianxing_b_fail(monkeypatch):
    rec = {"kind": "xianxing", "task": "宝象迷情", "map": "平顶山",
           "coords": [(187, 27), (269, 28)],
           "coord_a": (187, 27), "coord_b": (269, 28)}
    host, calls = _mock_demon_thread(
        monkeypatch, rec,
        nav_results=[(True, "到达A"), (False, "超时")])
    host._run_demon_navigate(object(), "xianxing")
    assert len(calls) == 2
    assert host.notified[0][1] is False
    assert "B" in host.notified[0][2] and "失败" in host.notified[0][2]


def test_run_demon_navigate_missing_coord(monkeypatch):
    rec = {"kind": "kill", "task": "三藏逢魔", "map": "万寿山",
           "coords": [], "coord_a": None, "coord_b": None}
    host, calls = _mock_demon_thread(monkeypatch, rec)
    host._run_demon_navigate(object(), "kill")
    assert calls == []
    assert host.notified[0][1] is False
    assert "缺少地图/坐标" in host.notified[0][2]


# ---- 任务栏查询修罗任务（只识别不寻路，用户 2026-11-26） ----

def test_verify_xiuluo_task_query_guards(monkeypatch):
    """查询修罗任务：无游戏窗口给出错误，不抛异常。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False

    host = _Host()
    monkeypatch.setattr(diagnostics.game, "find_main_window", lambda: None)
    r = host.verify_xiuluo_task_query()
    assert r["status"] == "error" and "未检测到游戏窗口" in r["msg"]


def test_verify_xiuluo_task_query_starts_thread(monkeypatch):
    """查询修罗任务线程参数：(win,)，启动后置忙标志。"""
    import types

    from src.app import diagnostics

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False

    host = _Host()
    fake_win = object()
    monkeypatch.setattr(diagnostics.game, "find_main_window",
                        lambda: fake_win)
    monkeypatch.setattr(diagnostics, "_game_window_clickable",
                        lambda win: True)
    started = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started.update(target=target, args=args, name=name)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(diagnostics.threading, "Thread", _FakeThread)
    r = host.verify_xiuluo_task_query()
    assert r["status"] == "success"
    assert started["started"] is True and started["args"] == (fake_win,)
    assert host._verifying is True


def test_run_xiuluo_task_query_reads_named_child(monkeypatch):
    """查询修罗任务：按「常规玩法/修罗任务」读取、识别单杀、关闭任务栏。"""
    from src.app import diagnostics
    from src.device import task_panel

    calls = {}

    def fake_read(win, keyword=None, prefix=task_panel.TAG, task_keyword=None):
        calls["keywords"] = (keyword, task_keyword)
        rec = {"kind": "kill", "task": "修罗任务", "map": "凤巢六层",
               "coords": [(84, 55)], "coord_a": (84, 55), "coord_b": None,
               "target": "修罗", "max_rounds": 1}
        return {"task": "修罗任务", "recognition": rec}, "ok"

    monkeypatch.setattr(task_panel, "read_first_group_task", fake_read)
    closed = []
    monkeypatch.setattr(task_panel, "close_task_panel",
                        lambda win: closed.append(1) or (True, "已关闭"))
    monkeypatch.setattr(diagnostics.app_utils, "log", lambda *a, **k: None)
    host = _demon_host(diagnostics)
    host._verifying = True
    host._notify_xiuluo_task_query_result = (
        lambda ok, info: host.notified.append((ok, info)))
    host._run_xiuluo_task_query(object())
    assert calls["keywords"] == (task_panel.TASK_PANEL_XIULUO_GROUP,
                                 task_panel.TASK_PANEL_XIULUO_TASK)
    assert closed == [1]
    ok, info = host.notified[0]
    assert ok is True
    assert "凤巢六层" in info and "修罗" in info and "单杀" in info
    assert host._verifying is False


def test_cancel_map_navigate_only_when_running(monkeypatch):
    """中断按钮：无导航时拒绝，导航中转发 map_nav.request_cancel。"""
    import types

    from src.app import diagnostics
    from src.device import map_nav

    class _Host(diagnostics.DiagnosticsMixin):
        def __init__(self):
            self._tm = types.SimpleNamespace(is_running=lambda role=None: False)
            self._verifying = False
            self._map_nav_running = False

    host = _Host()
    assert host.cancel_map_navigate()["status"] == "ignored"
    calls = []
    monkeypatch.setattr(map_nav, "request_cancel", lambda: calls.append(1))
    host._map_nav_running = True
    r = host.cancel_map_navigate()
    assert r["status"] == "success" and calls == [1]


def test_mixin_methods_resolve_without_shadowing():
    """P3 拆分：四个 mixin 的每个方法在 ControlWindow 上原样解析（无遮蔽）。

    callPython 名不变；任一同名方法被搬回 ControlWindow 类体或出现在
    另一个 mixin，都会因 `is not` 失败（防止形成两份实现后只改一份）。
    """
    from src.app import diagnostics, message_api, scheme_api, settings_api

    for cls in (diagnostics.DiagnosticsMixin, settings_api.SettingsApiMixin,
                message_api.MessageApiMixin, scheme_api.SchemeApiMixin):
        assert issubclass(control_window.ControlWindow, cls), cls.__name__
        shadowed = sorted(
            name for name, member in vars(cls).items()
            if inspect.isfunction(member)
            and getattr(control_window.ControlWindow, name) is not member)
        assert shadowed == [], f"{cls.__name__} 方法被遮蔽: {shadowed}"
    assert message_api._window_minimized.__module__ == "src.app.message_api"


def test_scheme_import_confirm_without_pending():
    """无待确认导入：返回错误并释放对话框忙标记（搬迁后烟测）。"""
    from src.app import scheme_api

    class _Host(scheme_api.SchemeApiMixin):
        _pending_import = None
        _scheme_dialog_busy = True

    host = _Host()
    result = host.scheme_import_confirm("overwrite")
    assert result["status"] == "error"
    assert "无待确认的导入" in result["msg"]
    assert host._scheme_dialog_busy is False  # 忙标记已释放


def test_exit_game_continues_when_team_handling_fails(monkeypatch):
    """队伍处理失败（未组队时组队菜单打不开）不得阻止退出游戏。

    2026-11-22 用户实测：无队伍时 /exitgame confirm 停在
    「队伍处理失败：组队菜单未打开」，客户端没有退出；队伍处理应是
    尽力而为，失败记录并继续关闭客户端。
    """
    import threading

    from src.device import exit_game as exit_game_mod

    class _TM:
        def is_running(self, role=None):
            return False

    class _Host(message_api.MessageApiMixin):
        def __init__(self):
            self._game_op_lock = threading.RLock()
            self._tm = _TM()
            self.logs = []

        def _message_log(self, text, level="INFO"):
            self.logs.append((level, text))

    monkeypatch.setattr(message_api, "_selected_role", lambda: "111")
    monkeypatch.setattr(message_api.game, "find_main_window", lambda: object())
    monkeypatch.setattr(exit_game_mod, "leave_teams_for_exit",
                        lambda win, progress=None: (False, "组队菜单未打开"))
    monkeypatch.setattr(exit_game_mod, "exit_client",
                        lambda win, progress=None: (True, "游戏客户端已关闭"))
    result = _Host()._message_exit_game()
    assert result["ok"] is True
    assert "游戏客户端已关闭" in result["message"]
    assert "队伍处理跳过：组队菜单未打开" in result["message"]


def test_exit_game_reports_exit_failure(monkeypatch):
    """队伍处理后客户端关闭失败：仍按失败返回关闭原因。"""
    import threading

    from src.device import exit_game as exit_game_mod

    class _TM:
        def is_running(self, role=None):
            return False

    class _Host(message_api.MessageApiMixin):
        def __init__(self):
            self._game_op_lock = threading.RLock()
            self._tm = _TM()

        def _message_log(self, text, level="INFO"):
            pass

    monkeypatch.setattr(message_api, "_selected_role", lambda: "111")
    monkeypatch.setattr(message_api.game, "find_main_window", lambda: object())
    monkeypatch.setattr(exit_game_mod, "leave_teams_for_exit",
                        lambda win, progress=None: (True, "未组队，无需操作"))
    monkeypatch.setattr(exit_game_mod, "exit_client",
                        lambda win, progress=None: (False, "系统菜单未打开"))
    result = _Host()._message_exit_game()
    assert result["ok"] is False
    assert result["message"] == "系统菜单未打开"
