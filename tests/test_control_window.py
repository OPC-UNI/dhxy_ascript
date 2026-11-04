"""任务模式多选保存/读取的「仅战斗」互斥逻辑测试。

验证点：
- 混合列表（仅战斗 + 其它任务）→ 剔除仅战斗、保留普通任务
  （页面禁用互斥下正常不出现，兜底旧数据/方案导入）
- 仅战斗单独选中 → 保留
- 非法模式过滤不受影响
"""
from src.control_window import BATTLE_ONLY_MODE, TASK_MODES, _sanitize_modes


def test_sanitize_keeps_battle_only_when_alone():
    assert _sanitize_modes(["仅战斗"]) == [BATTLE_ONLY_MODE]


def test_sanitize_drops_battle_only_when_mixed():
    assert _sanitize_modes(["五环", "仅战斗"]) == ["五环"]
    assert _sanitize_modes(["仅战斗", "修罗", "五倍"]) == ["修罗", "五倍"]
    # 顺序按 TASK_MODES 固定顺序返回
    assert _sanitize_modes(["五倍", "仅战斗", "五环"]) == ["五环", "五倍"]


def test_sanitize_filters_invalid_modes():
    assert _sanitize_modes(["五环", "不存在", "仅战斗"]) == ["五环"]
    assert _sanitize_modes(["不存在"]) == []
    assert _sanitize_modes([]) == []


def test_sanitize_single_mode_each_valid():
    for mode in TASK_MODES:
        assert _sanitize_modes([mode]) == [mode]
