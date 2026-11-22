"""多开舰队行数据：管理列表顺序固定（2026-11-22 v2）。"""
from src.app import fleet_api, window_registry


class _TM:
    def running_targets(self):
        return []


class _Host(fleet_api.FleetApiMixin):
    def __init__(self):
        self._tm = _TM()
        self._fleet_starting = False


def _row(role_id, label, seq, selected=False):
    return {
        "key": role_id, "role_id": role_id, "label": label, "server": "",
        "name": "", "hwnd": 0, "title": label, "live": True,
        "logged_in": True, "enabled": True, "selected": selected,
        "selectable": True, "duplicate": False, "seq": seq,
    }


def test_fleet_rows_stay_in_seq_order(monkeypatch):
    """管理列表不因运行/选中/勾选重排：按 seq（首次出现顺序）稳定输出。"""
    rows = [_row("333", "丙", 2),
            _row("111", "甲", 0),
            _row("222", "乙", 1, selected=True)]
    monkeypatch.setattr(window_registry, "list_rows",
                        lambda: {"rows": rows, "selected": "222",
                                 "selected_hwnd": 0})
    monkeypatch.setattr(fleet_api.debug_overlay, "read_runtime_status",
                        lambda rid=None: None)
    monkeypatch.setattr(fleet_api.progress, "task_progress_summary",
                        lambda rid=None: {"percent": 0, "done": 0,
                                          "total": 0, "per_task": {}})
    monkeypatch.setattr(fleet_api.schemes, "active_name", lambda rid=None: "")
    monkeypatch.setattr(fleet_api.role_lock, "is_held", lambda rid=None: False)

    state = _Host().get_fleet_state()
    assert [r["role_id"] for r in state["rows"]] == ["111", "222", "333"]
    assert state["selected"] == "222"
