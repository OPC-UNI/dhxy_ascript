"""配置方案管理：多套设置一键切换 + 导入导出。

方案 = 用户设置键的完整快照，白名单为 control_window 全部 load/save
设置键（30 键）。运行时状态（任务进度、摄妖香时间戳、清屏标记、
窗口绑定、授权缓存）与派生缓存（high_perf_cache）不进方案，全局共享。

存储：与 config.json 同目录的 schemes.json——
    {"active": "默认", "默认": {设置键: 值}, ...}
方案与 active 全局一份（多实例共享 config.json 的现状下不做按实例
隔离，与现有「谁后写谁生效」的共享设置语义一致，用户确认接受）。

快照时机：仅在 切换/保存/另存新方案 三个时点更新——切换前先把当前
设置快照回写当前活动方案，保证界面未保存改动不丢失；「保存」按钮
按输入名写回活动方案（改名即重命名）。应用方案时缺失键以默认值
补齐（快照语义完整），写入走 app_utils.kv_save_many_merged（重读→改→写
一次，防跨进程旧快照整文件覆写）。

本模块不顶层导入 control_window（后者顶层导入本模块），设置键表经
_control_window() 惰性获取——与 xiuluo 延迟导入同模式。
"""
import json
import threading
from pathlib import Path

from ascript.windows.system.key_value import KeyValue

from . import app_utils

SCHEMES_FILENAME = "schemes.json"
DEFAULT_SCHEME_NAME = "默认"
MAX_NAME_LEN = 20
_INVALID_NAME_CHARS = set('\\/:*?"<>|')
# schemes.json 仅主进程读写（任务子进程不接触），锁防导入导出后台线程
# 与桥接调用并发——主线程串行是常态，锁是兜底。
_SCHEMES_LOCK = threading.Lock()


def _control_window():
    """惰性获取 control_window 模块（避免顶层循环导入）。"""
    from . import control_window as cw
    return cw


def _schemes_path():
    """schemes.json 路径：与 config.json 同目录（跟随 KeyValue 路径策略）。"""
    KeyValue._initialize()
    return KeyValue._file_path.parent / SCHEMES_FILENAME


def _load():
    """读取 schemes.json；文件缺失/损坏/active 无效返回 None（调用方重建）。

    不持有锁——仅在 _get_data/_update 锁内调用。
    """
    try:
        data = json.loads(_schemes_path().read_text(encoding="utf-8"))
        active = data.get("active") if isinstance(data, dict) else None
        if (isinstance(data, dict) and isinstance(active, str)
                and isinstance(data.get(active), dict)):
            return data
    except Exception:
        pass
    return None


def _save_locked(data):
    """锁内写盘（调用方持 _SCHEMES_LOCK）。"""
    _schemes_path().write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _fresh_data():
    """锁内读；文件缺失/损坏时按当前设置重建（「默认」方案 + 迁移）。"""
    data = _load()
    if data is None:
        data = {"active": DEFAULT_SCHEME_NAME,
                DEFAULT_SCHEME_NAME: _snapshot_current()}
        _save_locked(data)
    return data


def _update(mutator):
    """锁内读-改-写；文件缺失时先重建。返回 mutator 结果。"""
    with _SCHEMES_LOCK:
        data = _fresh_data()
        result = mutator(data)
        _save_locked(data)
        return result


def _get_data():
    """锁内只读获取（缺失时重建并落盘）。"""
    with _SCHEMES_LOCK:
        return _fresh_data()


# ---- 设置键表与默认值 ----
# 覆盖 control_window 全部设置键；默认值与各 load_* 函数首次默认相同：
# 选中五环、次数 TASK_COUNT_DEFAULTS、摄妖香开、阈值 50、
# 战斗 人物技能3-5/召唤兽攻击、修罗 5 人+男人男鬼、盒子/检查队伍/喊话
# 关、高性能开、补血蓝开、清屏开、巫医开+间隔 5、法门关、悬浮关、主题浅色。
def _settings_spec():
    """返回 {设置键: 默认值}（惰性取 control_window 常量）。"""
    cw = _control_window()
    return {
        cw._MODES_KEY: list(cw._DEFAULT_MODES),
        **{key: cw.TASK_COUNT_DEFAULTS[mode] for mode, key in cw._COUNT_KEYS.items()},
        cw._USE_SHEYAOXIANG_KEY: True,
        **{key: cw.DEFAULT_THRESHOLD for key in cw._THRESHOLD_KEYS.values()},
        cw._BATTLE_KEYS["char"]: cw._DEFAULT_BATTLE["char"],
        cw._BATTLE_KEYS["beast"]: cw._DEFAULT_BATTLE["beast"],
        cw._XIULUO_MIN_KEY: cw.XIULUO_MIN_DEFAULT,
        cw._XIULUO_RACES_KEY: ",".join(cw.XIULUO_RACES_DEFAULT),
        cw._XIULUO_HEZI_KEY: False,
        cw._XIULUO_CHECK_TEAM_KEY: False,
        cw._XIULUO_AUTO_WUYI_KEY: True,
        cw._XIULUO_WUYI_INTERVAL_KEY: 5,
        cw._XIULUO_SHOUT_SWITCH_KEY: False,
        cw._XIULUO_TAB_GROUP_KEY: False,
        cw._WUBEI_HEZI_KEY: False,
        cw._WUBEI_CHECK_TEAM_KEY: False,
        cw._WUBEI_SHOUT_SWITCH_KEY: False,
        cw._WUBEI_AUTO_WUYI_KEY: True,
        cw._WUBEI_WUYI_INTERVAL_KEY: 5,
        cw._WUBEI_KUIMULANG_KEY: True,
        cw._WUBEI_TAB_GROUP_KEY: False,
        cw._HIGH_PERF_KEY: True,
        cw._AUTO_REFILL_KEY: True,
        cw._CLEAR_SCREEN_KEY: True,
        cw._AUTO_FAMEN_KEY: False,
        cw._DEBUG_OVERLAY_KEY: False,
        cw._UI_THEME_KEY: "light",
    }


def _snapshot_current():
    """当前设置的完整快照（走各 load_* 函数，夹取/默认语义与页面一致）。

    设置键仅主进程 UI 写入（write-through 保存时已刷新 KeyValue 快照），
    任务子进程只写运行时状态键——load_* 读到的是当前生效值。
    """
    cw = _control_window()
    counts = cw.load_task_counts()
    thresholds = cw.load_thresholds()
    battle = cw.load_battle_actions()
    xl = cw.load_xiuluo_settings()
    return {
        cw._MODES_KEY: cw.load_selected_modes(),
        **{key: counts[mode] for mode, key in cw._COUNT_KEYS.items()},
        cw._USE_SHEYAOXIANG_KEY: cw.load_use_sheyaoxiang(),
        **{key: thresholds[name] for name, key in cw._THRESHOLD_KEYS.items()},
        cw._BATTLE_KEYS["char"]: battle["char"],
        cw._BATTLE_KEYS["beast"]: battle["beast"],
        cw._XIULUO_MIN_KEY: xl["min_members"],
        cw._XIULUO_RACES_KEY: ",".join(xl["races"]),
        cw._XIULUO_HEZI_KEY: cw.load_xiuluo_hezi(),
        cw._XIULUO_CHECK_TEAM_KEY: cw.load_xiuluo_check_team(),
        cw._XIULUO_AUTO_WUYI_KEY: cw.load_xiuluo_auto_wuyi(),
        cw._XIULUO_WUYI_INTERVAL_KEY: cw.load_xiuluo_wuyi_interval(),
        cw._XIULUO_SHOUT_SWITCH_KEY: cw.load_xiuluo_shout_switch(),
        cw._XIULUO_TAB_GROUP_KEY: cw.load_xiuluo_tab_group(),
        cw._WUBEI_HEZI_KEY: cw.load_wubei_hezi(),
        cw._WUBEI_CHECK_TEAM_KEY: cw.load_wubei_check_team(),
        cw._WUBEI_SHOUT_SWITCH_KEY: cw.load_wubei_shout_switch(),
        cw._WUBEI_AUTO_WUYI_KEY: cw.load_wubei_auto_wuyi(),
        cw._WUBEI_WUYI_INTERVAL_KEY: cw.load_wubei_wuyi_interval(),
        cw._WUBEI_KUIMULANG_KEY: cw.load_wubei_kuimulang_switch(),
        cw._WUBEI_TAB_GROUP_KEY: cw.load_wubei_tab_group(),
        cw._HIGH_PERF_KEY: cw.load_high_perf(),
        cw._AUTO_REFILL_KEY: cw.load_auto_refill(),
        cw._CLEAR_SCREEN_KEY: cw.load_clear_screen(),
        cw._AUTO_FAMEN_KEY: cw.load_auto_famen(),
        cw._DEBUG_OVERLAY_KEY: cw.load_debug_overlay(),
        cw._UI_THEME_KEY: cw.load_ui_theme(),
    }


def _coerce(key, value):
    """按默认值类型规整导入值：类型不符/越界回默认，防脏值进入 config.json
    （load_* 对坏值可能直接 int() 崩溃——导入文件是外部输入，必须兜底）。"""
    cw = _control_window()
    spec = _settings_spec()
    default = spec[key]
    try:
        if isinstance(default, bool):
            return bool(value) if isinstance(value, (bool, int)) else default
        if isinstance(default, int):
            v = int(value)
            if key in cw._THRESHOLD_KEYS.values():
                v = min(95, max(5, v))
            elif key == cw._XIULUO_MIN_KEY:
                v = min(cw.XIULUO_MIN_RANGE[1], max(cw.XIULUO_MIN_RANGE[0], v))
            elif key in (cw._XIULUO_WUYI_INTERVAL_KEY, cw._WUBEI_WUYI_INTERVAL_KEY):
                v = min(cw._WUYI_INTERVAL_MAX, max(cw._WUYI_INTERVAL_MIN, v))
            elif key in cw._COUNT_KEYS.values():
                v = max(0, v)
            return v
        if isinstance(default, list):
            if isinstance(value, list) and key == cw._MODES_KEY:
                return [m for m in value if m in cw.TASK_MODES]
            return list(value) if isinstance(value, list) else default
        if isinstance(default, str):
            return value if isinstance(value, str) else default
    except (TypeError, ValueError):
        return default
    return value


def _apply(settings):
    """把方案设置写入 config.json：缺失键以默认值补齐、白名单外键丢弃、
    值经 _coerce 规整（快照语义完整 + 外部文件兜底）。"""
    if not isinstance(settings, dict):
        settings = {}
    spec = _settings_spec()
    mapping = {key: _coerce(key, settings[key]) if key in settings else default
               for key, default in spec.items()}
    app_utils.kv_save_many_merged(mapping)


def _valid_name(name):
    """校验方案名：返回 (规整名, None) 或 (None, 错误文案)。"""
    name = str(name or "").strip()
    if not name:
        return None, "方案名不能为空"
    if len(name) > MAX_NAME_LEN:
        return None, f"方案名最长 {MAX_NAME_LEN} 字"
    if any(ch in _INVALID_NAME_CHARS for ch in name):
        return None, "方案名不能含 \\ / : * ? \" < > |"
    return name, None


# ---- 对外操作 ----

def migrate_if_needed():
    """首次启用方案：现有 config.json 设置落为「默认」方案并设为活动。

    全新安装（无 config.json）时快照即全默认值。主进程启动时调用一次；
    schemes.json 已存在则空操作（_update 读取时同样有重建兜底）。
    """
    _update(lambda data: {"status": "success"})


def list_schemes():
    """方案列表与当前活动方案（页面下拉/管理区渲染）。"""
    data = _get_data()
    return {"active": data["active"],
            "names": [k for k in data if k != "active" and isinstance(data[k], dict)]}


def active_name():
    """当前活动方案名。"""
    return _get_data().get("active")


def switch_scheme(name):
    """切换到目标方案。

    先把当前设置快照回写当前活动方案（未保存改动不丢失），再整体应用
    目标方案。返回 {"status", "active"}。
    """
    name = str(name or "").strip()

    def _do(data):
        if name not in data or not isinstance(data[name], dict):
            return {"status": "error", "msg": "方案不存在"}
        active = data.get("active")
        if active and isinstance(data.get(active), dict) and active != name:
            data[active] = _snapshot_current()
        data["active"] = name
        return {"status": "success", "active": name, "settings": data[name]}

    result = _update(_do)
    if result.get("status") == "success":
        _apply(result["settings"])
        result.pop("settings", None)  # 仅内部应用用，不回传页面
    return result


def resave_scheme(name):
    """保存：当前设置快照写回活动方案；输入名与活动名不同即同时重命名。

    名字不变 = 把当前设置烤进活动方案（「默认」同样允许——切换离开时
    本就会快照回写，显式保存与之一致）；「默认」不可改名。
    """
    name, err = _valid_name(name)
    if err:
        return {"status": "error", "msg": err}
    snapshot = _snapshot_current()

    def _do(data):
        cur = data.get("active")
        if cur == DEFAULT_SCHEME_NAME and name != cur:
            return {"status": "error", "msg": "「默认」方案不可重命名"}
        if name != cur:
            if name in data:
                return {"status": "error", "msg": "方案名已存在"}
            data[name] = data.pop(cur)
        data[name] = snapshot
        data["active"] = name
        return {"status": "success", "active": name, "renamed": name != cur}

    return _update(_do)


def save_scheme_copy(name):
    """另存新方案：当前设置另存为新方案（名字已占用时自动加序号后缀），
    成功后新方案成为活动方案（config.json 无需改写：设置即当前）。"""
    name, err = _valid_name(name)
    if err:
        return {"status": "error", "msg": err}
    snapshot = _snapshot_current()

    def _do(data):
        # 锁内自算后缀（unique_name 会重入 _SCHEMES_LOCK，不能调用）
        final = name
        i = 2
        while final in data:
            final = f"{name}({i})"
            if len(final) > MAX_NAME_LEN:
                final = final[:MAX_NAME_LEN]
            i += 1
        data[final] = snapshot
        data["active"] = final
        return {"status": "success", "active": final, "name": final}

    return _update(_do)


def delete_scheme(name):
    """删除方案（「默认」不可删除）；删除活动方案时应用「默认」回落。"""
    def _do(data):
        if name not in data or not isinstance(data[name], dict):
            return {"status": "error", "msg": "方案不存在"}
        if name == DEFAULT_SCHEME_NAME:
            return {"status": "error", "msg": "「默认」方案不可删除"}
        del data[name]
        applied = False
        if data.get("active") == name:
            data["active"] = DEFAULT_SCHEME_NAME
            applied = True
        return {"status": "success", "active": data["active"],
                "applied": applied,
                "settings": data[DEFAULT_SCHEME_NAME] if applied else None}

    result = _update(_do)
    if result.get("status") == "success" and result.get("applied"):
        _apply(result["settings"])
        result.pop("settings", None)  # 仅内部应用用，不回传页面
    return result


def export_scheme_to(path, name):
    """导出单方案为自描述 JSON 文件：{"name": ..., "settings": ...}。

    settings 含默认补齐（导出文件自包含，缺失键不依赖导入方版本）。
    """
    data = _get_data()
    if name not in data or not isinstance(data[name], dict):
        raise ValueError("方案不存在")
    spec = _settings_spec()
    settings = {**spec,
                **{k: v for k, v in data[name].items() if k in spec}}
    Path(path).write_text(
        json.dumps({"name": name, "settings": settings},
                   indent=4, ensure_ascii=False),
        encoding="utf-8")


def read_import_file(path):
    """解析导入文件：返回 (方案名, 已规整设置)；非法文件抛 ValueError。

    兼容两种形态：{"name", "settings"} 自描述文件（本应用导出）与裸设置
    对象（名字退文件 stem）。值经 _coerce 规整后再入库。
    """
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"文件读取失败: {e}")
    if not isinstance(payload, dict):
        raise ValueError("不是有效的方案文件（顶层须为 JSON 对象）")
    settings = payload.get("settings")
    if isinstance(settings, dict):
        name = str(payload.get("name") or "").strip()
        if not name:
            name = Path(path).stem
    else:
        settings = payload
        name = Path(path).stem
    name, err = _valid_name(name)
    if err:
        raise ValueError(f"方案名无效（{err}），请修改文件名或文件内 name 字段")
    spec = _settings_spec()
    settings = {k: _coerce(k, v) for k, v in settings.items() if k in spec}
    if not settings:
        raise ValueError("文件中没有可识别的设置键")
    return name, settings


def name_exists(name):
    """方案名是否已占用（导入冲突检测）。"""
    return name in _get_data()


def add_scheme(name, settings):
    """导入落地：新方案入列（不激活）。调用方须已处理同名冲突。"""
    def _do(data):
        if name in data:
            return {"status": "error", "msg": "方案已存在"}
        data[name] = settings
        return {"status": "success", "active": data["active"]}

    return _update(_do)


def replace_scheme(name, settings):
    """导入覆盖同名方案（「默认」锚点不可覆盖，只能另存为）。"""
    def _do(data):
        if name == DEFAULT_SCHEME_NAME:
            return {"status": "error", "msg": "「默认」方案不可覆盖，请用另存为"}
        if name not in data or not isinstance(data[name], dict):
            return {"status": "error", "msg": "方案不存在"}
        data[name] = settings
        return {"status": "success", "active": data["active"]}

    return _update(_do)


def unique_name(base):
    """冲突另存为名：base、base(2)、base(3)... 首个未占用。"""
    data = _get_data()
    base = str(base or "").strip() or DEFAULT_SCHEME_NAME
    if base not in data:
        return base
    i = 2
    while True:
        candidate = f"{base}({i})"
        if len(candidate) > MAX_NAME_LEN:
            candidate = candidate[:MAX_NAME_LEN]
        if candidate not in data:
            return candidate
        i += 1


# ---- 系统文件对话框（pywebview 6.2.1 无对话框 API，tkinter 兜底） ----
# 由调用方在短命线程中执行（每个 Tk root 只服务一次对话框，用完销毁）；
# 对话框自带消息循环，不阻塞 webview 主线程。

def pick_save_path(initial_name):
    """另存为对话框：返回所选路径或空串（取消）。"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        return filedialog.asksaveasfilename(
            title="导出配置方案", initialfile=initial_name,
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
    finally:
        root.destroy()


def pick_open_path():
    """打开对话框：返回所选文件路径或空串（取消）。"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        return filedialog.askopenfilename(
            title="导入配置方案",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
    finally:
        root.destroy()
