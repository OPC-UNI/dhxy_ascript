"""自动修理装备（repair.py）单元测试：计数口径、自然日重置、弹窗映射。"""
import pytest

from src.core import app_utils
from src.device import repair
from src.core import user_data


class _FakeWin:
    """仅提供标题（char_key 取标题中的账号 ID）。"""

    def __init__(self, title="[大话西游2_经典版] xxx（ID：888888）"):
        self.title = title


@pytest.fixture
def win():
    return _FakeWin()


@pytest.fixture(autouse=True)
def _reset_mode():
    yield
    app_utils.set_current_task_mode("")


def test_record_and_due_isolated_by_mode(win):
    app_utils.set_current_task_mode("修罗")
    for _ in range(3):
        repair.record_battle(win)
    assert repair.battles_today(win, "修罗") == 3
    assert repair.battles_today(win, "五倍") == 0
    assert repair.is_due(win, "修罗", 3) is True
    assert repair.is_due(win, "修罗", 4) is False
    app_utils.set_current_task_mode("五倍")
    repair.record_battle(win)
    assert repair.battles_today(win, "五倍") == 1
    assert repair.battles_today(win, "修罗") == 3  # 各自独立


def test_record_ignores_other_task_modes(win):
    app_utils.set_current_task_mode("客栈")
    repair.record_battle(win)
    entry = user_data.get_state(repair._REPAIR_COUNT_KEY,
                                user_data.role_id_from_char_key("（ID：888888）"))
    assert not entry or not entry.get("modes")


def test_daily_reset(win):
    role = user_data.role_id_from_char_key("（ID：888888）")
    user_data.set_state(repair._REPAIR_COUNT_KEY,
                        {"date": "2000-01-01", "modes": {"修罗": 99}}, role)
    assert repair.battles_today(win, "修罗") == 0
    assert repair.is_due(win, "修罗", 30) is False
    # 新的一天从 0 开始累计
    app_utils.set_current_task_mode("修罗")
    repair.record_battle(win)
    assert repair.battles_today(win, "修罗") == 1


def test_mark_attempted_resets_counter(win):
    app_utils.set_current_task_mode("修罗")
    for _ in range(5):
        repair.record_battle(win)
    repair.mark_attempted(win, "修罗")
    assert repair.battles_today(win, "修罗") == 0


def test_count_isolated_by_role():
    w1 = _FakeWin("xxx（ID：1001）")
    w2 = _FakeWin("xxx（ID：1002）")
    app_utils.set_current_task_mode("修罗")
    repair.record_battle(w1)
    repair.record_battle(w1)
    assert repair.battles_today(w1, "修罗") == 2
    assert repair.battles_today(w2, "修罗") == 0


def test_match_dialog_two_variants(monkeypatch):
    monkeypatch.setattr(repair, "REPAIR_DIALOGS",
                        (("有装备需要修理", "修理全部装备"),
                         ("无装备需要修理", "我知道了")))
    items = [("你的装备有装备需要修理，是否花费银两", [500, 300, 900, 340], 0.96)]
    assert repair.match_dialog(items) == ("有装备需要修理", "修理全部装备")
    items = [("当前无装备需要修理，请回吧", [500, 300, 900, 340], 0.96)]
    assert repair.match_dialog(items) == ("无装备需要修理", "我知道了")
    assert repair.match_dialog([("客官慢走", [500, 300, 900, 340], 0.96)]) is None


def test_default_dialog_map_has_equipment():
    """真实配置：有装备弹窗长文案（分片段匹配）→「确认修理」。"""
    items = [("小隐隐于野，中隐隐于市，大隐隐于朝。阁下想修理装备吗？",
              [500, 300, 900, 340], 0.96)]
    spec = repair.match_dialog(items)
    assert spec is not None
    assert spec[1] == "确认修理"


def test_default_dialog_map_no_equipment():
    """真实配置：无装备弹窗→「算了，还是看看」。"""
    items = [("你身上的装备完好无损，无需修理", [500, 300, 900, 340], 0.96)]
    spec = repair.match_dialog(items)
    assert spec is not None
    assert spec[1] == "算了，还是看看"


def test_default_materials_configured():
    """用户提供的模板/坐标/文案齐全时 _configured() 为真。

    模板文件在 res/img/luoyang_xiuli.png（仓库内），防资料被误删；
    坐标/文案为 2026-11-12 用户实测值。
    """
    assert repair._configured() is True


def test_unconfigured_chain_fails_without_clicking(win, monkeypatch):
    monkeypatch.setattr(repair, "REPAIR_MAP_POS", None)
    monkeypatch.setattr(repair, "REPAIR_SPOT", None)
    monkeypatch.setattr(repair, "REPAIR_DIALOGS", ())
    ok, info = repair._run_repair_chain(win)
    assert ok is False
    assert "未配置" in info


def test_configured_requires_all_materials(monkeypatch):
    monkeypatch.setattr(repair, "REPAIR_NPC_ICON", "x.png")
    monkeypatch.setattr(repair, "REPAIR_MAP_POS", (1, 2))
    monkeypatch.setattr(repair, "REPAIR_SPOT", (3, 4))
    monkeypatch.setattr(repair, "REPAIR_DIALOGS", (("文案", "选项"),))
    monkeypatch.setattr(repair.game_map, "template_size", lambda icon: (10, 10))
    assert repair._configured() is True
    monkeypatch.setattr(repair, "REPAIR_DIALOGS", (("文案", ""),))
    assert repair._configured() is False
    # 模板文件缺失同样按未配置处理（不空找模板/不走位）
    monkeypatch.setattr(repair, "REPAIR_DIALOGS", (("文案", "选项"),))
    monkeypatch.setattr(repair.game_map, "template_size", lambda icon: None)
    assert repair._configured() is False
