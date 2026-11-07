"""任务模式多选保存/读取的「仅战斗」互斥逻辑测试。

验证点：
- 混合列表（仅战斗 + 其它任务）→ 剔除仅战斗、保留普通任务
  （页面禁用互斥下正常不出现，兜底旧数据/方案导入）
- 仅战斗单独选中 → 保留
- 非法模式过滤不受影响
"""
from src.control_window import (BATTLE_ONLY_MODE, TASK_MODES, _sanitize_modes,
                                _COUNT_KEYS, TASK_COUNT_DEFAULTS)


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
    """自动创建队伍键在方案快照默认值表中存在且默认关闭（修罗/五倍
    各一开关；load_* 首次默认与 _settings_spec 必须一致）。"""
    from src import schemes
    from src.control_window import (_WUBEI_AUTO_CREATE_TEAM_KEY,
                                    _XIULUO_AUTO_CREATE_TEAM_KEY,
                                    load_wubei_auto_create_team,
                                    load_xiuluo_auto_create_team)
    spec = schemes._settings_spec()
    assert spec[_WUBEI_AUTO_CREATE_TEAM_KEY] is False
    assert spec[_XIULUO_AUTO_CREATE_TEAM_KEY] is False
    _patch_kv_empty(monkeypatch)
    for key, load in ((_WUBEI_AUTO_CREATE_TEAM_KEY, load_wubei_auto_create_team),
                      (_XIULUO_AUTO_CREATE_TEAM_KEY, load_xiuluo_auto_create_team)):
        assert load() == spec[key]
