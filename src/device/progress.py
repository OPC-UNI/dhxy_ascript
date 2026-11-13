"""任务进度汇总（当天完成次数 / 上限 / 百分比）。

依赖 device.game 读取当前角色键，并从 core.settings 读取次数配置；
从 core.settings 拆出，保持 core 不依赖 device。
"""
import time

from ascript.windows.system.key_value import KeyValue

from ..core import app_utils
from ..core import user_data
from ..core.settings import (_TASK_PROGRESS_KEY, load_selected_modes,
                             load_task_counts)
from . import game

def _legacy_progress_char_key():
    """旧版进度键兜底（未迁移/单测）：当前窗口的角色键。"""
    try:
        win = game.find_main_window()
        return app_utils.char_key(win) if win is not None else None
    except Exception:
        return None

def _fresh_progress_modes():
    """直读当前角色 state.json 的当天进度（跨进程可见）。

    任务子进程写 data/<角色>/state.json（读-改-写原子替换），主进程
    每次直读文件。旧版未迁移时回退 config.json 的角色键；迁移后不再
    回退，避免角色间串数据。
    返回 {mode: 当天已完成次数}，数据缺失/损坏按空处理。
    """
    try:
        entry = user_data.get_state(_TASK_PROGRESS_KEY, None, None)
    except Exception:
        entry = None
    if entry is None and not user_data.is_migrated():
        try:
            KeyValue._initialize()
            data = KeyValue._load_data().get(_TASK_PROGRESS_KEY, {}) or {}
            entry = data.get(_legacy_progress_char_key() or "", {})
        except Exception:
            entry = None
    if isinstance(entry, dict) and entry.get("date") == time.strftime("%Y-%m-%d"):
        modes = entry.get("modes", {}) or {}
        return {m: int(v) for m, v in modes.items() if isinstance(v, (int, float))}
    return {}

def task_progress_summary():
    """总进度汇总：选中任务当天完成次数合计 / 次数上限合计（次数 0 =
    不限，不计入分母——无法度量；已完成超出上限按上限计，防修改上限
    后百分比破百）。
    返回 {"percent", "done", "total", "per_task": {mode: {"done", "count", "percent"}}}。
    """
    counts = load_task_counts()
    done_map = _fresh_progress_modes()
    total = 0
    done_total = 0
    per_task = {}
    for mode in load_selected_modes():
        count = counts.get(mode, 0) or 0
        done = done_map.get(mode, 0) or 0
        if count > 0:
            total += count
            done_total += min(done, count)
            per_task[mode] = {"done": min(done, count), "count": count,
                              "percent": min(100, round(done * 100.0 / count))}
        else:
            per_task[mode] = {"done": done, "count": 0, "percent": 0}
    percent = round(done_total * 100.0 / total) if total else 0
    return {"percent": min(100, percent), "done": done_total, "total": total,
            "per_task": per_task}
