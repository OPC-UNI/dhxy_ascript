"""按角色隔离的数据目录（data/<角色ID>/）。

布局（layout 2，2026-11-11 起）::

    data/_meta.json                     # 迁移标记 / 角色清单 / 上次活动角色
    data/<角色ID>/settings.json         # 当前生效设置（任务选择/次数/阈值/开关）
    data/<角色ID>/state.json            # 任务进度、摄妖香、清屏、名望种族、组号
    data/<角色ID>/schemes.json          # 该角色的配置方案
    data/<角色ID>/task_stats.json       # 该角色的成就统计
    data/<角色ID>/high_perf_cache.json  # 该角色的高性能坐标缓存

角色 ID 取窗口标题「（ID：数字）」；取不到退窗口绑定记录；再退
_default。角色在启动时确定，切换账号需要重启（绑定在启动阶段完成）。

旧版数据迁移：根目录 config.json / schemes.json / task_stats.json 原样
保留（回滚安全），按角色把字段复制到 data/ 下——只复制不移动，
_meta.json 记录 layout=2，重复调用只补缺失文件。旧成就无法按角色
精确拆分，迁移时归入当前绑定角色；config.json 里的方案设置迁入
当前角色 settings.json，之后两阶段清理掉旧字段（config 只保留全局）。
"""
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

LAYOUT_VERSION = 2
DATA_DIR_NAME = "data"
META_NAME = "_meta.json"
DEFAULT_ROLE = "_default"
STATE_NAME = "state.json"
SETTINGS_NAME = "settings.json"
SCHEMES_NAME = "schemes.json"
STATS_NAME = "task_stats.json"
HIGH_PERF_NAME = "high_perf_cache.json"

# 旧版 config.json 里按角色键控的运行时状态（键 = 「（ID：xxx）」）
ROLE_STATE_KEYS = ("task_progress", "sheyaoxiang_last_use",
                   "clear_screen_state", "mingwang_race")
# 组号相关状态：跟随当前队长角色（2026-11-11 用户确认按角色隔离）
GROUP_STATE_KEYS = ("group_member_actions", "group_last_scan", "group_detected")
HIGH_PERF_KEY = "high_perf_cache"
# 迁移后 config.json 里冻结的旧字段（第二次启动备份后清理）
LEGACY_CONFIG_KEYS = ROLE_STATE_KEYS + GROUP_STATE_KEYS + (HIGH_PERF_KEY,)
LEGACY_CONFIG_NAME = "_legacy_config.json"  # data/_legacy_config.json 备份
LEGACY_DIR_NAME = "_legacy"  # 根目录旧 schemes.json/task_stats.json 归档目录
# 旧版方案设置里的历史单键（已迁移到 task_modes / high_perf，清理时一并移除）
ROLE_SETTING_EXTRA_KEYS = ("task_mode", "xiuluo_high_perf")

_ROLE_ID_RE = re.compile(r"ID：(\d+)")
_LOCK = threading.RLock()
_ROLE_CACHE = {"id": None, "ts": 0.0}
_ROLE_TTL = 30.0  # 角色解析缓存（状态读写每几秒一次，窗口枚举不必每次做）


# ==================== 路径与角色解析 ====================

def config_dir():
    """与 KeyValue 相同的配置目录（cwd，不可写时退 ~/.app_data）。"""
    from ascript.windows.system.key_value import KeyValue
    KeyValue._initialize()
    return Path(KeyValue._file_path).parent


def data_root():
    return config_dir() / DATA_DIR_NAME


def role_id_from_text(text):
    """从标题/绑定 ID/角色键中提取数字账号 ID；无 ID 返回 None。"""
    m = _ROLE_ID_RE.search(str(text or ""))
    return m.group(1) if m else None


def role_id_from_char_key(ckey):
    """角色键（「（ID：xxx）」或完整标题）→ 角色目录名。"""
    return role_id_from_text(ckey) or DEFAULT_ROLE


def _probe_role_id():
    """实时解析角色 ID：当前游戏窗口标题 → 绑定记录。异常返回 None。"""
    try:
        from . import game
        win = game.find_main_window()
        rid = role_id_from_text(getattr(win, "title", "") if win is not None else "")
        if rid:
            return rid
        raw = game.bound_window_id()
        if isinstance(raw, dict):
            raw = raw.get("id")
        return role_id_from_text(raw)
    except Exception:
        return None


def current_role_id(refresh=False):
    """当前角色 ID（30s 缓存；refresh=True 强制重新解析）。"""
    now = time.time()
    if not refresh and _ROLE_CACHE["id"] and now - _ROLE_CACHE["ts"] < _ROLE_TTL:
        return _ROLE_CACHE["id"]
    rid = _probe_role_id() or DEFAULT_ROLE
    _ROLE_CACHE["id"] = rid
    _ROLE_CACHE["ts"] = now
    return rid


def role_dir(role=None, ensure=True):
    """角色数据目录（非法角色名统一退 _default）。"""
    rid = role or current_role_id()
    if not re.fullmatch(r"\d+", rid or ""):
        rid = DEFAULT_ROLE
    path = data_root() / rid
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(role=None):
    return role_dir(role) / STATE_NAME


def settings_path(role=None):
    return role_dir(role) / SETTINGS_NAME


def schemes_path(role=None):
    return role_dir(role) / SCHEMES_NAME


def stats_path(role=None):
    return role_dir(role) / STATS_NAME


def high_perf_path(role=None):
    return role_dir(role) / HIGH_PERF_NAME


# ==================== JSON 读写 ====================

def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _atomic_write(path, data):
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


# ==================== 角色状态（state.json） ====================

def read_state(role=None):
    data = _read_json(state_path(role), {})
    return data if isinstance(data, dict) else {}


def update_state(mutator, role=None):
    """读-改-写 state.json（线程锁 + 原子替换）。

    mutator 收到当前 dict，返回新 dict（或原地修改后返回）。跨进程
    并发写入仍采用「写前重读」策略，与 config.json 的合并写同级别。
    """
    with _LOCK:
        data = read_state(role)
        result = mutator(data)
        if isinstance(result, dict):
            data = result
        _atomic_write(state_path(role), data)
        return data


def get_state(key, role=None, default=None):
    return read_state(role).get(key, default)


def set_state(key, value, role=None):
    def _mut(data):
        data[key] = value
        return data
    return update_state(_mut, role)


# ==================== 角色生效设置（settings.json） ====================

def read_settings(role=None):
    """读取角色生效设置（缺失/损坏返回空 dict）。"""
    data = _read_json(settings_path(role), {})
    return data if isinstance(data, dict) else {}


def write_settings(data, role=None):
    with _LOCK:
        _atomic_write(settings_path(role), data if isinstance(data, dict) else {})


# ==================== 高性能缓存 ====================

def read_high_perf(role=None):
    data = _read_json(high_perf_path(role), {})
    return data if isinstance(data, dict) else {}


def write_high_perf(data, role=None):
    with _LOCK:
        _atomic_write(high_perf_path(role), data if isinstance(data, dict) else {})


# ==================== 迁移元数据 ====================

def read_meta():
    data = _read_json(data_root() / META_NAME, {})
    return data if isinstance(data, dict) else {}


def is_migrated():
    return int(read_meta().get("layout") or 0) >= LAYOUT_VERSION


def last_active_role():
    return read_meta().get("last_active_role")


def set_last_active_role(role):
    with _LOCK:
        meta = read_meta()
        meta["last_active_role"] = role
        _atomic_write(data_root() / META_NAME, meta)


def prune_legacy_config(skip_settings=False):
    """两阶段清理 config.json 旧字段（迁移当次不动，下一次启动执行）。

    清理两类键：旧版角色维度运行时键（LEGACY_CONFIG_KEYS）与已迁入
    data/<角色>/settings.json 的方案设置键；完整备份到
    data/_legacy_config.json 再删除。skip_settings=True 用于“刚把
    设置从 config 迁入角色文件”的那次启动，留一轮回滚窗口。
    """
    with _LOCK:
        meta = read_meta()
        if meta.get("legacy_config_pruned") and meta.get("settings_pruned"):
            return {"pruned": False, "already": True}
        if not is_migrated():
            return {"pruned": False, "reason": "not_migrated"}
        cfg = _read_json(config_dir() / "config.json", {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        keys = []
        if not meta.get("legacy_config_pruned"):
            keys += [k for k in LEGACY_CONFIG_KEYS if k in cfg]
        if not skip_settings and not meta.get("settings_pruned"):
            keys += [k for k in _setting_keys() if k in cfg and k not in keys]
        if not keys:
            if not skip_settings:
                meta["settings_pruned"] = True
            meta["legacy_config_pruned"] = True
            _atomic_write(data_root() / META_NAME, meta)
            return {"pruned": False, "already": True}

        backup_path = data_root() / LEGACY_CONFIG_NAME
        existing = _read_json(backup_path, {}) or {}
        backup_keys = existing.get("keys")
        backup_keys = dict(backup_keys) if isinstance(backup_keys, dict) else {}
        backup_keys.update({k: cfg[k] for k in keys})
        backup = {"pruned_at": time.time(), "source": "config.json",
                  "keys": backup_keys}
        _atomic_write(backup_path, backup)

        # 延迟导入：app_utils 顶层依赖本模块（避免循环导入）
        from . import app_utils
        removed = app_utils.kv_remove_many_merged(keys)
        meta = read_meta()
        if not skip_settings:
            meta["settings_pruned"] = True
            meta["settings_pruned_at"] = time.time()
        meta["legacy_config_pruned"] = True
        meta["legacy_config_backup"] = f"{DATA_DIR_NAME}/{LEGACY_CONFIG_NAME}"
        prev = meta.get("legacy_config_keys")
        prev = prev if isinstance(prev, list) else []
        meta["legacy_config_keys"] = sorted(set(prev) | set(keys))
        _atomic_write(data_root() / META_NAME, meta)
        return {"pruned": True, "removed": removed, "keys": keys}


def archive_legacy_files():
    """把根目录旧版 schemes.json / task_stats.json 移到 data/_legacy/。

    只在角色副本已就绪时移动（方案文件存在；成就有数据或已完成
    stats_imported）。根目录不再保留这两份旧文件；回滚旧版本时需
    手动从 data/_legacy/ 移回根目录。重复运行时根目录已无文件则跳过；
    若旧版本重新生成了根文件，会再次归档覆盖。
    """
    with _LOCK:
        if not is_migrated():
            return {"archived": False, "reason": "not_migrated"}
        current = current_role_id()
        meta = read_meta()
        moved = []
        legacy_dir = data_root() / LEGACY_DIR_NAME

        root_schemes = config_dir() / SCHEMES_NAME
        role_schemes = _read_json(schemes_path(current), None)
        if root_schemes.exists() and isinstance(role_schemes, dict):
            legacy_dir.mkdir(parents=True, exist_ok=True)
            os.replace(root_schemes, legacy_dir / SCHEMES_NAME)
            moved.append(SCHEMES_NAME)

        root_stats = config_dir() / STATS_NAME
        role_stats = _read_json(stats_path(current), None)
        stats_safe = bool(meta.get("stats_imported")) or _stats_has_data(role_stats)
        if root_stats.exists() and stats_safe:
            legacy_dir.mkdir(parents=True, exist_ok=True)
            os.replace(root_stats, legacy_dir / STATS_NAME)
            moved.append(STATS_NAME)

        if moved:
            meta = read_meta()
            meta["legacy_files_archived"] = True
            meta["legacy_files_archive"] = f"{DATA_DIR_NAME}/{LEGACY_DIR_NAME}"
            prev = meta.get("legacy_files_moved")
            prev = prev if isinstance(prev, list) else []
            meta["legacy_files_moved"] = sorted(set(prev) | set(moved))
            _atomic_write(data_root() / META_NAME, meta)
        return {"archived": bool(moved), "moved": moved}


# ==================== 旧数据迁移 ====================

def _best_default_entry(key, entries):
    """非 ID 键（窗口标题/空键）里挑一条作为 _default 的当前值。

    完整原始映射另外保存在 state["legacy"]，不丢数据。
    """
    values = list(entries.values())
    if not values:
        return None
    if key == "sheyaoxiang_last_use":
        return max(values, key=lambda v: v if isinstance(v, (int, float)) else 0)
    if key == "task_progress":
        def _score(v):
            if not isinstance(v, dict):
                return ("", 0)
            modes = v.get("modes")
            if not isinstance(modes, dict):
                modes = {}
            return (str(v.get("date") or ""),
                    sum(x for x in modes.values() if isinstance(x, (int, float))))
        return max(values, key=_score)
    if key == "mingwang_race":
        return max(values, key=lambda v: v.get("date", "")
                   if isinstance(v, dict) else "")
    return values[-1]


def _collect_roles(cfg, current):
    """旧配置里出现过的角色 + 当前角色 + 绑定角色。"""
    roles = {current}
    for key in ROLE_STATE_KEYS:
        mapping = cfg.get(key)
        if not isinstance(mapping, dict):
            continue
        for ckey in mapping:
            rid = role_id_from_text(ckey)
            roles.add(rid or DEFAULT_ROLE)
    bound = cfg.get("bound_window_id")
    if isinstance(bound, dict):
        rid = role_id_from_text(bound.get("id"))
        if rid:
            roles.add(rid)
    return roles


def _split_legacy_state(cfg, current):
    """把旧版角色键控状态拆成 {角色: state dict}。

    非 ID 键进 _default；组号相关状态整体归当前角色（2026-11-11
    用户确认组号成员动作按队长角色隔离）。
    """
    states = {}
    default_entries = {}
    for key in ROLE_STATE_KEYS:
        mapping = cfg.get(key)
        if not isinstance(mapping, dict):
            continue
        for ckey, value in mapping.items():
            rid = role_id_from_text(ckey)
            if rid:
                states.setdefault(rid, {})[key] = value
            else:
                default_entries.setdefault(key, {})[ckey] = value
    if default_entries:
        entry = states.setdefault(DEFAULT_ROLE, {})
        for key, values in default_entries.items():
            entry[key] = _best_default_entry(key, values)
        entry["legacy"] = default_entries
    for key in GROUP_STATE_KEYS:
        if key in cfg:
            states.setdefault(current, {})[key] = cfg[key]
    return states


def _setting_keys():
    """角色设置键：方案白名单 + 两个历史单键（惰性导入 schemes）。"""
    from . import schemes
    keys = list(schemes._settings_spec().keys())
    keys.extend(k for k in ROLE_SETTING_EXTRA_KEYS if k not in keys)
    return keys


def _active_scheme_settings(role, spec):
    """角色方案文件里活动方案的设置（只保留方案键）。"""
    data = _read_json(schemes_path(role), None)
    if not isinstance(data, dict):
        return None
    active = data.get("active")
    settings = data.get(active)
    if not isinstance(settings, dict):
        return None
    return {**spec, **{k: v for k, v in settings.items() if k in spec}}


def _ensure_role_settings(meta, current):
    """保证当前角色有 settings.json（config 旧设置一次性迁入）。

    config.json 的生效设置属于上一次活动角色（meta.last_active_role）；
    首次迁移时把它们写进该角色 settings.json，再给当前角色建设置
    （新角色用自己方案的活动快照或默认值）。返回 (meta, current_created)。
    """
    from . import schemes
    spec = dict(schemes._settings_spec())
    keys = _setting_keys()
    owner = meta.get("last_active_role") or meta.get("migration_role") or current
    changed = False
    if not meta.get("settings_migrated"):
        owner_path = settings_path(owner)
        if not owner_path.exists():
            cfg = _read_json(config_dir() / "config.json", {}) or {}
            if not isinstance(cfg, dict):
                cfg = {}
            live = {k: cfg[k] for k in keys if k in cfg}
            if live:
                write_settings({**spec, **live}, owner)
            else:
                write_settings(_active_scheme_settings(owner, spec) or spec, owner)
        meta["settings_migrated"] = True
        meta["settings_owner"] = owner
        meta["settings_migrated_at"] = time.time()
        changed = True
    current_created = False
    if not settings_path(current).exists():
        write_settings(_active_scheme_settings(current, spec) or spec, current)
        current_created = True
    elif changed and current == owner:
        current_created = True  # 本次刚从 config 迁入当前角色
    if changed or current_created:
        _atomic_write(data_root() / META_NAME, meta)
    return meta, current_created


def _stats_has_data(data):
    """成就文件是否有累计数据（total 任一指标 > 0）。"""
    if not isinstance(data, dict):
        return False
    total = data.get("total")
    if not isinstance(total, dict):
        return False
    for key in ("rounds", "battles", "seconds"):
        try:
            if float(total.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


def _ensure_legacy_stats(meta, current):
    """补导旧版成就（迁移时复制失败/目的文件为空时的一次性修复）。

    只在迁移时的目标角色（meta.stats_role）上执行；成功导入后写
    stats_imported 标记，之后用户清空成就不再重导。返回 (meta, imported)。
    """
    if meta.get("stats_imported"):
        return meta, False
    if meta.get("stats_role") != current:
        return meta, False
    root_stats = _read_json(config_dir() / STATS_NAME, None)
    if not isinstance(root_stats, dict) or not _stats_has_data(root_stats):
        meta["stats_imported"] = True
        _atomic_write(data_root() / META_NAME, meta)
        return meta, False
    dest = stats_path(current)
    existing = _read_json(dest, None)
    if _stats_has_data(existing):
        # 目的角色已有新数据：不覆盖（旧文件仍在根目录，可人工合并）
        meta["stats_imported"] = True
        _atomic_write(data_root() / META_NAME, meta)
        return meta, False
    _atomic_write(dest, root_stats)
    meta["stats_imported"] = True
    meta["stats_imported_at"] = time.time()
    _atomic_write(data_root() / META_NAME, meta)
    return meta, True


def _maybe_adopt_default(meta, current):
    """迁移时没有绑定角色（数据落在 _default），首次绑定真实角色后认领。"""
    if current == DEFAULT_ROLE or meta.get("adopted_by"):
        return meta
    if meta.get("migration_role") != DEFAULT_ROLE:
        return meta
    src = data_root() / DEFAULT_ROLE
    if not src.exists():
        return meta
    dst = data_root() / current
    dst.mkdir(parents=True, exist_ok=True)
    for name in (SCHEMES_NAME, STATS_NAME, HIGH_PERF_NAME):
        if (src / name).exists() and not (dst / name).exists():
            shutil.copyfile(src / name, dst / name)
    src_state = read_state(DEFAULT_ROLE)
    dst_state = read_state(current)
    changed = False
    for key, value in src_state.items():
        if key == "legacy":
            continue
        if key not in dst_state:
            dst_state[key] = value
            changed = True
    if changed:
        _atomic_write(dst / STATE_NAME, dst_state)
    meta["adopted_by"] = current
    _atomic_write(data_root() / META_NAME, meta)
    return meta


def migrate_if_needed():
    """旧版本升级迁移（幂等，只复制不移动根目录旧文件）。

    返回 {"migrated", "role", "last_active_role", "meta"}；migrated=True
    表示刚完成迁移，调用方应把当前 config.json 设置快照进当前角色方案。
    """
    with _LOCK:
        current = current_role_id(refresh=True)
        meta = read_meta()
        if int(meta.get("layout") or 0) >= LAYOUT_VERSION:
            meta = _maybe_adopt_default(meta, current)
            meta, settings_created = _ensure_role_settings(meta, current)
            meta, stats_imported = _ensure_legacy_stats(meta, current)
            return {"migrated": False, "role": current,
                    "last_active_role": meta.get("last_active_role"),
                    "settings_created": settings_created,
                    "stats_imported": stats_imported,
                    "meta": meta}

        root = data_root()
        cfg = _read_json(config_dir() / "config.json", {}) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        legacy_schemes = _read_json(config_dir() / SCHEMES_NAME, None)
        legacy_stats = _read_json(config_dir() / STATS_NAME, None)
        roles = _collect_roles(cfg, current)
        states = _split_legacy_state(cfg, current)
        roles.update(states.keys())

        root.mkdir(parents=True, exist_ok=True)
        for rid in roles:
            directory = root / rid
            directory.mkdir(parents=True, exist_ok=True)
            entry = states.get(rid)
            if entry and not (directory / STATE_NAME).exists():
                _atomic_write(directory / STATE_NAME, entry)

        current_dir = root / current
        current_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        stats_imported = False
        # 方案 / 成就 / 高性能缓存只复制给迁移时的当前角色（2026-11-11
        # 用户确认：其他角色从默认方案/空成就/重新学习开始）。
        if isinstance(legacy_schemes, dict) and not (current_dir / SCHEMES_NAME).exists():
            _atomic_write(current_dir / SCHEMES_NAME, legacy_schemes)
            copied.append("schemes")
        if (isinstance(legacy_stats, dict) and _stats_has_data(legacy_stats)
                and not (current_dir / STATS_NAME).exists()):
            _atomic_write(current_dir / STATS_NAME, legacy_stats)
            copied.append("stats")
            stats_imported = True
        high = cfg.get(HIGH_PERF_KEY)
        if isinstance(high, dict) and high and not (current_dir / HIGH_PERF_NAME).exists():
            _atomic_write(current_dir / HIGH_PERF_NAME, high)
            copied.append("high_perf")

        meta = {
            "layout": LAYOUT_VERSION,
            "migrated_at": time.time(),
            "migration_role": current,
            "known_roles": sorted(roles),
            "stats_role": current,
            "copied": copied,
            "stats_imported": stats_imported,
            "last_active_role": None,
        }
        _atomic_write(root / META_NAME, meta)
        meta, settings_created = _ensure_role_settings(meta, current)
        return {"migrated": True, "role": current,
                "last_active_role": None, "stats_imported": stats_imported,
                "settings_created": settings_created, "meta": meta}
