"""配置方案管理：多套设置一键切换 + 导入导出。

方案 = 用户设置键的完整快照，白名单为 core.settings 的设置 load/save
键。运行时状态（任务进度、摄妖香时间戳、清屏标记、窗口绑定、授权缓存）、
派生缓存（high_perf_cache）与调试相关全局设置（调试信息开关
debug_overlay、「显示与调试」卡片解锁标记 debug_ui_unlocked）不进方案。
界面主题 ui_theme 为全局设置（2026-11-11 用户确认：切账号/切方案不改主题）。

存储（2026-11-11 起按角色隔离）：
    data/<角色ID>/settings.json   # 当前生效设置（含未保存改动）
    data/<角色ID>/schemes.json    # 命名方案快照 + active
切换账号不改写 config.json：每个角色自己的 settings.json 就是生效设置，
任务子进程直接读当前角色设置。新角色首次使用从默认值建「默认」方案
（不继承其他角色的方案）。

快照时机：仅在 切换/保存/另存新方案 三个时点更新——切换前先把当前
设置快照回写当前活动方案，保证界面未保存改动不丢失；「保存」按钮
按输入名写回活动方案（改名即重命名）。应用方案时缺失键以默认值
补齐（快照语义完整），写入走 role_settings.update（当前角色
data/<角色ID>/settings.json）。

本模块不顶层导入 core.settings（control_window 顶层导入本模块），设置键表经
_settings() 惰性获取——与 xiuluo 延迟导入同模式。
"""
import json
import os
import threading
from pathlib import Path

from . import app_utils
from . import user_data
from . import role_settings

DEFAULT_SCHEME_NAME = "默认"
MAX_NAME_LEN = 20
_INVALID_NAME_CHARS = set('\\/:*?"<>|')
# schemes.json 仅主进程读写（任务子进程不接触），锁防导入导出后台线程
# 与桥接调用并发——主线程串行是常态，锁是兜底。
_SCHEMES_LOCK = threading.Lock()


def _settings():
    """惰性获取设置模块（避免顶层循环导入）。"""
    from . import settings as cw
    return cw


def _schemes_path(role=None):
    """当前角色的 schemes.json 路径（data/<角色ID>/schemes.json）。"""
    return user_data.schemes_path(role)


def _load(role=None):
    """读取角色方案文件；缺失/损坏/active 无效返回 None（调用方重建）。

    不持有锁——仅在 _get_data/_update 锁内调用。
    """
    try:
        data = json.loads(_schemes_path(role).read_text(encoding="utf-8"))
        active = data.get("active") if isinstance(data, dict) else None
        if (isinstance(data, dict) and isinstance(active, str)
                and isinstance(data.get(active), dict)):
            return data
    except Exception:
        pass
    return None


def _save_locked(data, role=None):
    """锁内写盘（调用方持 _SCHEMES_LOCK）。"""
    _schemes_path(role).write_text(
        json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


def _default_data():
    """全新角色/文件缺失时的默认方案（默认值，不继承当前 config）。"""
    return {"active": DEFAULT_SCHEME_NAME,
            DEFAULT_SCHEME_NAME: dict(_settings_spec())}


def _fresh_data(role=None):
    """锁内读；文件缺失时按默认值建「默认」方案（不继承其他角色设置）。"""
    data = _load(role)
    if data is None:
        data = _default_data()
        _save_locked(data, role)
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
# 覆盖 core.settings 设置键；默认值与各 load_* 函数首次默认相同：
# 选中五环、次数 TASK_COUNT_DEFAULTS、摄妖香开、阈值 50、
# 战斗 人物技能3-5/召唤兽攻击、修罗 5 人+男人男鬼、盒子关、
# 检查队伍/自动创建队伍/自动归队/喊话（含各任务喊话内容默认文案）/
# 组号开、种族比武同款队伍开关与巫医/修理间隔、五环活力继续开、
# 高性能开、补血蓝开、清屏开、巫医开+间隔 5、修理开+间隔 30、法门关。
# 调试信息开关（debug_overlay）、「显示与调试」解锁标记、界面主题
# （ui_theme）为全局设置，刻意不在表内：方案应用/快照都不碰。
def _settings_spec():
    """返回 {设置键: 默认值}（惰性取 core.settings 常量）。"""
    cw = _settings()
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
        cw._XIULUO_CHECK_TEAM_KEY: True,
        cw._XIULUO_AUTO_CREATE_TEAM_KEY: True,
        cw._XIULUO_AUTO_REJOIN_KEY: True,
        cw._XIULUO_AUTO_WUYI_KEY: True,
        cw._XIULUO_WUYI_INTERVAL_KEY: 5,
        cw._XIULUO_AUTO_REPAIR_KEY: True,
        cw._XIULUO_REPAIR_INTERVAL_KEY: 30,
        cw._XIULUO_SHOUT_SWITCH_KEY: True,
        cw._XIULUO_SHOUT_TEXT_KEY: cw.XIULUO_SHOUT_TEXT_DEFAULT,
        cw._XIULUO_TAB_GROUP_KEY: True,
        cw._WUHUAN_VITALITY_CONTINUE_KEY: True,
        cw._WUBEI_HEZI_KEY: False,
        cw._WUBEI_CHECK_TEAM_KEY: True,
        cw._WUBEI_AUTO_CREATE_TEAM_KEY: True,
        cw._WUBEI_AUTO_REJOIN_KEY: True,
        cw._WUBEI_SHOUT_SWITCH_KEY: True,
        cw._WUBEI_SHOUT_TEXT_KEY: cw.WUBEI_SHOUT_TEXT_DEFAULT,
        cw._WUBEI_AUTO_WUYI_KEY: True,
        cw._WUBEI_WUYI_INTERVAL_KEY: 5,
        cw._WUBEI_AUTO_REPAIR_KEY: True,
        cw._WUBEI_REPAIR_INTERVAL_KEY: 30,
        cw._WUBEI_KUIMULANG_KEY: True,
        cw._WUBEI_TAB_GROUP_KEY: True,
        cw._ZHONGZU_CHECK_TEAM_KEY: True,
        cw._ZHONGZU_AUTO_CREATE_TEAM_KEY: True,
        cw._ZHONGZU_AUTO_REJOIN_KEY: True,
        cw._ZHONGZU_SHOUT_SWITCH_KEY: True,
        cw._ZHONGZU_SHOUT_TEXT_KEY: cw.ZHONGZU_SHOUT_TEXT_DEFAULT,
        cw._ZHONGZU_AUTO_WUYI_KEY: True,
        cw._ZHONGZU_WUYI_INTERVAL_KEY: 5,
        cw._ZHONGZU_AUTO_REPAIR_KEY: True,
        cw._ZHONGZU_REPAIR_INTERVAL_KEY: 30,
        cw._ZHONGZU_TAB_GROUP_KEY: True,
        cw._BATTLE_ONLY_AUTO_REJOIN_KEY: True,
        cw._HIGH_PERF_KEY: True,
        cw._AUTO_REFILL_KEY: True,
        cw._CLEAR_SCREEN_KEY: True,
        cw._SILENT_MODE_KEY: True,
        cw._AUTO_FAMEN_KEY: False,
        # ui_theme 为全局设置（2026-11-11 用户确认），不进方案
    }


def _snapshot_current():
    """当前设置的完整快照（走各 load_* 函数，夹取/默认语义与页面一致）。

    设置键仅主进程 UI 写入（write-through 保存时已刷新 KeyValue 快照），
    任务子进程只写运行时状态键——load_* 读到的是当前生效值。
    """
    cw = _settings()
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
        cw._XIULUO_AUTO_CREATE_TEAM_KEY: cw.load_xiuluo_auto_create_team(),
        cw._XIULUO_AUTO_REJOIN_KEY: cw.load_xiuluo_auto_rejoin(),
        cw._XIULUO_AUTO_WUYI_KEY: cw.load_xiuluo_auto_wuyi(),
        cw._XIULUO_WUYI_INTERVAL_KEY: cw.load_xiuluo_wuyi_interval(),
        cw._XIULUO_AUTO_REPAIR_KEY: cw.load_xiuluo_auto_repair(),
        cw._XIULUO_REPAIR_INTERVAL_KEY: cw.load_xiuluo_repair_interval(),
        cw._XIULUO_SHOUT_SWITCH_KEY: cw.load_xiuluo_shout_switch(),
        cw._XIULUO_SHOUT_TEXT_KEY: cw.load_xiuluo_shout_text(),
        cw._XIULUO_TAB_GROUP_KEY: cw.load_xiuluo_tab_group(),
        cw._WUHUAN_VITALITY_CONTINUE_KEY: cw.load_wuhuan_vitality_continue(),
        cw._WUBEI_HEZI_KEY: cw.load_wubei_hezi(),
        cw._WUBEI_CHECK_TEAM_KEY: cw.load_wubei_check_team(),
        cw._WUBEI_AUTO_CREATE_TEAM_KEY: cw.load_wubei_auto_create_team(),
        cw._WUBEI_AUTO_REJOIN_KEY: cw.load_wubei_auto_rejoin(),
        cw._WUBEI_SHOUT_SWITCH_KEY: cw.load_wubei_shout_switch(),
        cw._WUBEI_SHOUT_TEXT_KEY: cw.load_wubei_shout_text(),
        cw._WUBEI_AUTO_WUYI_KEY: cw.load_wubei_auto_wuyi(),
        cw._WUBEI_WUYI_INTERVAL_KEY: cw.load_wubei_wuyi_interval(),
        cw._WUBEI_AUTO_REPAIR_KEY: cw.load_wubei_auto_repair(),
        cw._WUBEI_REPAIR_INTERVAL_KEY: cw.load_wubei_repair_interval(),
        cw._WUBEI_KUIMULANG_KEY: cw.load_wubei_kuimulang_switch(),
        cw._WUBEI_TAB_GROUP_KEY: cw.load_wubei_tab_group(),
        cw._ZHONGZU_CHECK_TEAM_KEY: cw.load_zhongzu_check_team(),
        cw._ZHONGZU_AUTO_CREATE_TEAM_KEY: cw.load_zhongzu_auto_create_team(),
        cw._ZHONGZU_AUTO_REJOIN_KEY: cw.load_zhongzu_auto_rejoin(),
        cw._ZHONGZU_SHOUT_SWITCH_KEY: cw.load_zhongzu_shout_switch(),
        cw._ZHONGZU_SHOUT_TEXT_KEY: cw.load_zhongzu_shout_text(),
        cw._ZHONGZU_AUTO_WUYI_KEY: cw.load_zhongzu_auto_wuyi(),
        cw._ZHONGZU_WUYI_INTERVAL_KEY: cw.load_zhongzu_wuyi_interval(),
        cw._ZHONGZU_AUTO_REPAIR_KEY: cw.load_zhongzu_auto_repair(),
        cw._ZHONGZU_REPAIR_INTERVAL_KEY: cw.load_zhongzu_repair_interval(),
        cw._ZHONGZU_TAB_GROUP_KEY: cw.load_zhongzu_tab_group(),
        cw._BATTLE_ONLY_AUTO_REJOIN_KEY: cw.load_battle_only_auto_rejoin(),
        cw._HIGH_PERF_KEY: cw.load_high_perf(),
        cw._AUTO_REFILL_KEY: cw.load_auto_refill(),
        cw._CLEAR_SCREEN_KEY: cw.load_clear_screen(),
        cw._SILENT_MODE_KEY: cw.load_silent_mode(),
        cw._AUTO_FAMEN_KEY: cw.load_auto_famen(),
        # ui_theme 全局设置，不进方案快照（2026-11-11）
    }


def _coerce(key, value):
    """按默认值类型规整导入值：类型不符/越界回默认，防脏值进入角色设置
    （load_* 对坏值可能直接 int() 崩溃——导入文件是外部输入，必须兜底）。"""
    cw = _settings()
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
            elif key in (cw._XIULUO_WUYI_INTERVAL_KEY,
                         cw._WUBEI_WUYI_INTERVAL_KEY,
                         cw._ZHONGZU_WUYI_INTERVAL_KEY):
                v = min(cw._WUYI_INTERVAL_MAX, max(cw._WUYI_INTERVAL_MIN, v))
            elif key in (cw._XIULUO_REPAIR_INTERVAL_KEY,
                         cw._WUBEI_REPAIR_INTERVAL_KEY,
                         cw._ZHONGZU_REPAIR_INTERVAL_KEY):
                v = min(cw._REPAIR_INTERVAL_MAX, max(cw._REPAIR_INTERVAL_MIN, v))
            elif key in cw._COUNT_KEYS.values():
                v = max(0, v)
            return v
        if isinstance(default, list):
            if isinstance(value, list) and key == cw._MODES_KEY:
                return cw._sanitize_modes(value)  # 过滤非法项 + 仅战斗互斥
            return list(value) if isinstance(value, list) else default
        if isinstance(default, str):
            return value if isinstance(value, str) else default
    except (TypeError, ValueError):
        return default
    return value


def _apply(settings):
    """把方案设置写入当前角色 settings.json：缺失键以默认值补齐、
    白名单外键丢弃、值经 _coerce 规整（快照语义完整 + 外部文件兜底）。"""
    if not isinstance(settings, dict):
        settings = {}
    spec = _settings_spec()
    mapping = {key: _coerce(key, settings[key]) if key in settings else default
               for key, default in spec.items()}
    role_settings.update(mapping)
    # 静默操作：主进程立即生效（验证/诊断流程；任务子进程启动时按配置置位）
    try:
        app_utils.set_silent_mode(mapping[_settings()._SILENT_MODE_KEY])
    except Exception:
        pass


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

def ensure_role_schemes(role=None):
    """确保当前角色有方案文件（缺失时按默认值建「默认」）。"""
    with _SCHEMES_LOCK:
        data = _fresh_data(role)
    return {"status": "success", "active": data.get("active")}


def bootstrap_current_role():
    """迁移/首次启动：当前角色方案文件以 settings.json 实时设置为准。

    保留旧方案列表，只把活动方案的内容替换为实时设置（迁移时未保存
    的修改不丢）；文件不存在时新建「默认」。
    """
    with _SCHEMES_LOCK:
        data = _load()
        snap = _snapshot_current()
        if data is None:
            data = {"active": DEFAULT_SCHEME_NAME, DEFAULT_SCHEME_NAME: snap}
        else:
            active = data.get("active")
            if isinstance(data.get(active), dict):
                data[active] = snap
            else:
                data = {"active": DEFAULT_SCHEME_NAME,
                        DEFAULT_SCHEME_NAME: snap}
        _save_locked(data)
    return {"status": "success", "active": data.get("active")}


def activate_role(previous=None, current=None):
    """角色切换：只确保当前角色方案文件存在。

    生效设置已按角色存放在 data/<角色>/settings.json，切换角色无需
    再改写 config.json；任务子进程直接读当前角色的设置文件。
    """
    current = current or user_data.current_role_id()
    ensure_role_schemes(current)
    return {"status": "success",
            "switched": bool(previous and previous != current)}


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
    成功后新方案成为活动方案（settings.json 无需改写：设置即当前）。"""
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


# ---- 系统文件对话框（原生 Win32 comdlg32） ----
# 不能用 tkinter：Nuitka 打包版没有 Tcl/Tk 运行库（import tkinter 直接
# 失败），导出/导入点了没反应（2026-11-14 用户反馈，源码版正常）。
# comdlg32 是系统 DLL，无打包依赖；对话框在当前线程跑自己的消息循环，
# 调用方仍在短命线程中执行，不阻塞 webview 主线程。

# Windows 通用对话框标志（winuser.h commdlg.h）
_OFN_OVERWRITEPROMPT = 0x00000002
_OFN_NOCHANGEDIR = 0x00000008
_OFN_PATHMUSTEXIST = 0x00000800
_OFN_FILEMUSTEXIST = 0x00001000
_OFN_EXPLORER = 0x00080000


def _dialog_owner():
    """对话框 owner：当前进程的前台窗口；拿不到返回 0（无 owner 也能弹）。"""
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return 0
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(hwnd) if pid.value == os.getpid() else 0
    except Exception:
        return 0


def _file_dialog(save, title, initial_name="", owner_hwnd=None):
    """原生打开/另存为对话框：返回所选路径，取消返回空串。

    字符串指针字段用 c_void_p + addressof：若声明成 LPCWSTR，ctypes 会
    把带内嵌 NUL 的过滤器串按首个 NUL 截断（过滤器只剩第一段）。
    """
    import ctypes
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", ctypes.c_void_p),
            ("lpstrCustomFilter", ctypes.c_void_p),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", ctypes.c_void_p),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", ctypes.c_void_p),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", ctypes.c_void_p),
            ("lpstrTitle", ctypes.c_void_p),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", ctypes.c_void_p),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", ctypes.c_void_p),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    # 过滤器格式：显示名\0通配符\0...\0\0（create_unicode_buffer 保留内嵌 NUL）
    filter_buf = ctypes.create_unicode_buffer(
        "JSON 文件 (*.json)\0*.json\0所有文件 (*.*)\0*.*\0\0")
    title_buf = ctypes.create_unicode_buffer(title)
    defext_buf = ctypes.create_unicode_buffer("json")
    # 路径缓冲：对话框会写回选择结果；1024 字符兼容长路径
    file_buf = ctypes.create_unicode_buffer(1024)
    if save and initial_name:
        file_buf.value = initial_name

    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = _dialog_owner() if owner_hwnd is None else owner_hwnd
    ofn.lpstrFilter = ctypes.addressof(filter_buf)
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.addressof(file_buf)
    ofn.nMaxFile = len(file_buf)
    ofn.lpstrTitle = ctypes.addressof(title_buf)
    ofn.Flags = _OFN_EXPLORER | _OFN_NOCHANGEDIR | _OFN_PATHMUSTEXIST
    if save:
        ofn.Flags |= _OFN_OVERWRITEPROMPT
        ofn.lpstrDefExt = ctypes.addressof(defext_buf)
    else:
        ofn.Flags |= _OFN_FILEMUSTEXIST

    commdlg32 = ctypes.WinDLL("comdlg32")
    commdlg32.CommDlgExtendedError.restype = wintypes.DWORD
    func = (commdlg32.GetSaveFileNameW if save
            else commdlg32.GetOpenFileNameW)
    func.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(ofn)):
        err = commdlg32.CommDlgExtendedError()
        if err:
            raise OSError(f"文件对话框打开失败（错误码 {err:#x}）")
        return ""  # 用户取消
    return file_buf.value


def pick_save_path(initial_name, owner_hwnd=None):
    """另存为对话框：返回所选路径或空串（取消）。"""
    return _file_dialog(True, "导出配置方案", initial_name, owner_hwnd)


def pick_open_path(owner_hwnd=None):
    """打开对话框：返回所选文件路径或空串（取消）。"""
    return _file_dialog(False, "导入配置方案", owner_hwnd=owner_hwnd)
