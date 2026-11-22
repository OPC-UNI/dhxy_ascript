"""多窗口注册表：枚举游戏主窗口 + 按角色勾选/选中持久化 + 选中目标注入。

多窗口（2026-11-22，见 docs/multi-window.md）：
- 行 = 当前检测到的游戏主窗口（含未登录窗口）。窗口关闭即从窗口管理
  移除，重开自动重新出现；勾选状态按角色持久化，重开恢复。
- 勾选/选中状态按角色 ID 持久化在 config.json 的 `multi_window_state`；
  主进程唯一写者（worker 不碰）。
- 选中行 = 主进程当前窗口：apply_selection 把角色注入 user_data，并把
  目标窗口（句柄优先、角色兜底）注入 game——find_main_window /
  设置页 / 成就 / 组号入口全部跟随选中行。
- 选中窗口关闭时选中回落到其他活窗口（已勾选优先、Tab 固定顺序）；
  全部关闭则清空选中，重开任一窗口后自动选中。

旧单窗口绑定迁移：首次启动把 bound_window_id 的角色迁移为「勾选 + 选中」；
无旧绑定则选第一个已登录窗口（此后新窗口默认不勾选）。
"""
import functools
import re
import threading

from ..core import app_utils, user_data
from ..device import binding, game

STATE_KEY = "multi_window_state"

_STATE = None
_LOCK = threading.RLock()


def _clean(raw):
    """规整持久化状态（坏值兜底，外部文件输入不可信）。"""
    raw = raw if isinstance(raw, dict) else {}
    roles = raw.get("roles")
    roles = roles if isinstance(roles, dict) else {}
    clean_roles = {}
    for rid, meta in roles.items():
        rid = str(rid or "")
        if not re.fullmatch(r"\d+", rid) or not isinstance(meta, dict):
            continue
        clean_roles[rid] = {
            "enabled": bool(meta.get("enabled")),
            "label": str(meta.get("label") or rid),
            "title": str(meta.get("title") or ""),
        }
    selected = raw.get("selected")
    selected = str(selected) if selected and re.fullmatch(r"\d+", str(selected)) else None
    try:
        selected_hwnd = int(raw.get("selected_hwnd") or 0)
    except (TypeError, ValueError):
        selected_hwnd = 0
    # 窗口 Tab 固定顺序（2026-11-22 v2）：角色首次出现时追加；
    # 独立于 roles（勾选/选中不改变 Tab 位置）。
    order = raw.get("order")
    clean_order = []
    seen = set()
    for rid in (order if isinstance(order, list) else []):
        rid = str(rid or "")
        if re.fullmatch(r"\d+", rid) and rid not in seen:
            clean_order.append(rid)
            seen.add(rid)
    return {"initialized": bool(raw.get("initialized")),
            "selected": selected,
            "selected_hwnd": selected_hwnd,
            "order": clean_order,
            "roles": clean_roles}


def _load():
    return _clean(app_utils.kv_read_fresh(STATE_KEY, None))


def _state():
    global _STATE
    with _LOCK:
        if _STATE is None:
            _STATE = _load()
        return _STATE


def _save(state):
    global _STATE
    with _LOCK:
        _STATE = state
        app_utils.kv_save_merged(STATE_KEY, state)


def reset_cache():
    """测试用：丢弃进程内缓存，下次读取回落到 config。"""
    global _STATE
    with _LOCK:
        _STATE = None


def _locked(fn):
    """状态读写统一持 _LOCK：1s 舰队轮询与用户勾选/选中并发。"""
    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return _wrapped


# ==================== 窗口枚举辅助 ====================

def _win_by_hwnd(hwnd):
    hwnd = int(hwnd or 0)
    if not hwnd:
        return None
    for w in game.list_main_windows():
        if int(getattr(w, "hwnd", 0) or 0) == hwnd:
            return w
    return None


def _win_for_role(role_id):
    rid = str(role_id or "")
    if not rid:
        return None
    for w in game.list_main_windows():
        if game.window_role_id(w) == rid:
            return w
    return None


def _info_for_role(role_id, infos):
    rid = str(role_id or "")
    for info in infos:
        if info.get("role_id") == rid:
            return info
    return None


def _row_label(info, meta=None):
    label = binding.short_label_from_title(info.get("title") or "")
    if label:
        return label
    if meta and meta.get("label"):
        return meta["label"]
    return str(info.get("role_id") or info.get("title") or "")


def _apply_role_silent(rid):
    """主进程静默标志跟随选中角色（验证/诊断/多窗口前置检查共用）。"""
    try:
        from ..core.settings import load_silent_mode
        app_utils.set_silent_mode(load_silent_mode(rid))
    except Exception:
        pass


def _pick_live_info(state, infos):
    """从活窗口里挑一个作为当前窗口（已勾选优先，再按 Tab 固定顺序）。"""
    candidates = [i for i in infos
                  if i.get("role_id") and i.get("logged_in", True)]
    if not candidates:
        return None
    roles = state.get("roles") or {}
    order = state.get("order") or []

    def _key(info):
        rid = str(info["role_id"])
        enabled = bool((roles.get(rid) or {}).get("enabled"))
        try:
            idx = order.index(rid)
        except ValueError:
            idx = len(order)
        return (0 if enabled else 1, idx)

    return min(candidates, key=_key)


# ==================== 初始化 / 选中 ====================

@_locked
def ensure_initialized():
    """启动初始化：迁移旧绑定、补齐选中项、注入主进程目标。"""
    state = _state()
    infos = game.list_main_window_infos()
    if not state["initialized"]:
        roles = state["roles"]
        rid = ""
        title = ""
        label = ""
        raw = game.bound_window_id()  # 此时尚无进程目标，读全局旧绑定
        if isinstance(raw, dict):
            rid = user_data.role_id_from_text(raw.get("id")) or ""
            title = str(raw.get("title") or "")
            label = binding.short_label_from_title(title) or ""
        elif raw:
            rid = user_data.role_id_from_text(raw) or ""
            label = str(raw)
        info = _info_for_role(rid, infos) if rid else None
        if info is not None:
            title = info["title"]
            label = _row_label(info)
        if not rid:
            info = next((i for i in infos if i.get("logged_in")), None)
            if info is not None:
                rid = info["role_id"]
                title = info["title"]
                label = _row_label(info)
        if rid:
            entry = roles.setdefault(rid, {})
            entry["enabled"] = True
            entry.setdefault("label", label or rid)
            entry.setdefault("title", title or "")
            state["selected"] = rid
            state["selected_hwnd"] = int(info["hwnd"]) if info is not None else 0
        state["initialized"] = True
        _save(state)
    # 选中窗口已关闭（上次退出后未重开）时不保留选中：sync_live 回落到
    # 其他活窗口或清空，并在窗口重新出现后自动选中。句柄窗口直接复用
    # （组号 pass 标题显示队员时按角色找不到窗口）。
    state = sync_live(infos)
    rid = state.get("selected")
    if rid and rid not in state["roles"]:
        # 选中项落在未登记角色上（自动选中/手动改号）：补一条以便勾选可读
        state["roles"][rid] = {"enabled": False, "label": rid, "title": ""}
        _save(state)
    win = _win_by_hwnd(state.get("selected_hwnd") or 0)
    apply_selection(rid, win)
    try:
        label = (state["roles"].get(rid) or {}).get("label") if rid else ""
        app_utils.log(
            "[多开] 窗口注册表就绪：选中 "
            f"{label or rid or '无'}；已勾选 {len(enabled_roles())} 个窗口", "SYS")
    except Exception:
        pass
    return state


def apply_selection(role_id, win=None):
    """把选中角色/窗口注入主进程：角色缓存 + find_main_window 目标。"""
    rid = str(role_id) if role_id else None
    user_data.set_selected_role(rid)
    if not rid:
        game.clear_process_target()
        return None
    if win is None:
        win = _win_for_role(rid)
    if win is not None:
        game.set_process_target(rid,
                                hwnd=int(getattr(win, "hwnd", 0) or 0),
                                title=getattr(win, "title", "") or "",
                                strict=False)
        return win
    # 兜底：句柄窗口未找到时退回仅角色目标（正常路径 sync_live 已回落/
    # 清空选中，不会停在已关闭窗口上）
    game.set_process_target(rid, hwnd=0, title="", strict=False)
    return None


@_locked
def select(role_id, hwnd=None):
    """用户选中某行（role_id + 可选句柄，区分同角色重复窗口）。"""
    rid = str(role_id or "")
    if not re.fullmatch(r"\d+", rid):
        return {"status": "error", "msg": "该窗口未登录，不能选中"}
    state = _state()
    infos = game.list_main_window_infos()
    info = None
    if hwnd:
        info = next((i for i in infos
                     if int(i.get("hwnd") or 0) == int(hwnd)), None)
    if info is None:
        info = _info_for_role(rid, infos)
    if info is None:
        # 窗口关闭即从窗口管理移除：不存在/已关闭的行不能选中
        return {"status": "error", "msg": "该窗口已关闭，无法选择"}
    state["selected"] = rid
    state["selected_hwnd"] = int(info["hwnd"])
    meta = state["roles"].setdefault(rid, {})
    meta["label"] = _row_label(info, meta)
    meta["title"] = info["title"]
    _save(state)
    win = _win_by_hwnd(state["selected_hwnd"]) if state["selected_hwnd"] else None
    if win is None:
        win = _win_for_role(rid)
    apply_selection(rid, win)
    return {"status": "success", "selected": rid,
            "selected_hwnd": state["selected_hwnd"]}


@_locked
def set_enabled(role_id, enabled):
    """勾选/取消勾选「参与多开」（按角色持久化）。"""
    rid = str(role_id or "")
    if not re.fullmatch(r"\d+", rid):
        return {"status": "error", "msg": "该窗口未登录，不能加入多开"}
    state = _state()
    meta = state["roles"].setdefault(rid, {})
    meta["enabled"] = bool(enabled)
    info = _info_for_role(rid, game.list_main_window_infos())
    if info is not None:
        meta["label"] = _row_label(info, meta)
        meta["title"] = info["title"]
    _save(state)
    return {"status": "success", "role": rid, "enabled": bool(enabled)}


# ==================== 实时同步 / 行数据 ====================

def _role_in_window(win, role_id):
    """该窗口的已登录 Tab 里是否有此角色（区分组号 pass 与同窗口换登）。"""
    try:
        return any(str(t.get("id")) == str(role_id)
                   for t in game.list_tab_windows(win))
    except Exception:
        return False


@_locked
def sync_live(infos=None):
    """把选中目标对齐到活窗口（句柄优先、同角色重开兜底），刷新标签。

    窗口关闭即从窗口管理移除（2026-11-22 用户确认）：选中窗口消失时
    不再保留灰行，而是回落到其他活窗口（已勾选优先、Tab 固定顺序）；
    全部窗口关闭则清空选中（面板显示未选择窗口），重开任一窗口后自动
    选中。组号 pass（标题变成队员 Tab、句柄仍在）期间选中角色不变。

    唯一跟随标题账号的情形：同一句柄已换登，且选中角色不在该窗口的
    已登录 Tab 里——跟随当前账号，避免面板按旧角色读写、实际却操作
    新账号（旧版自动改绑语义）。返回 state。
    """
    state = _state()
    infos = infos if infos is not None else game.list_main_window_infos()
    changed = False
    rid = state.get("selected")
    selected_hwnd = int(state.get("selected_hwnd") or 0)
    info = None
    if rid and selected_hwnd:
        info = next((i for i in infos
                     if int(i.get("hwnd") or 0) == selected_hwnd), None)
    if (rid and info is not None and info.get("role_id")
            and str(info["role_id"]) != str(rid)):
        win = _win_by_hwnd(info["hwnd"])
        if win is None or not _role_in_window(win, rid):
            # 同一窗口换登账号：选中角色跟随标题账号
            new_rid = str(info["role_id"])
            meta = state["roles"].setdefault(new_rid, {
                "enabled": True, "label": _row_label(info),
                "title": info["title"]})
            meta["enabled"] = True
            meta["label"] = _row_label(info, meta)
            meta["title"] = info["title"]
            state["selected"] = new_rid
            state["selected_hwnd"] = int(info["hwnd"])
            rid = new_rid
            changed = True
            user_data.set_selected_role(new_rid)
            _apply_role_silent(new_rid)
    if rid and info is None:
        info = _info_for_role(rid, infos)
        if info is not None:
            new_hwnd = int(info["hwnd"])
            if new_hwnd != selected_hwnd:
                state["selected_hwnd"] = new_hwnd
                changed = True
        else:
            # 选中窗口已关闭：移除该窗口，选中不留在已关闭窗口上
            rid = None
            state["selected"] = None
            state["selected_hwnd"] = 0
            changed = True
            user_data.set_selected_role(None)
    if rid is None:
        # 无选中（游戏刚启动 / 窗口全部关闭后重开）：自动选中活窗口
        info = _pick_live_info(state, infos)
        if info is not None:
            rid = str(info["role_id"])
            state["selected"] = rid
            state["selected_hwnd"] = int(info["hwnd"])
            changed = True
            user_data.set_selected_role(rid)
            _apply_role_silent(rid)
    # 已登记角色的标签/完整标题跟随活窗口（重开时标签保持可读）
    for info2 in infos:
        rid2 = info2.get("role_id")
        meta = state["roles"].get(rid2) if rid2 else None
        if meta is None:
            continue
        label = _row_label(info2, meta)
        title = info2["title"]
        if label != meta.get("label") or title != meta.get("title"):
            meta["label"] = label
            meta["title"] = title
            changed = True
    if changed:
        _save(state)
    if info is not None:
        game.set_process_target(rid, hwnd=int(info["hwnd"]),
                                title=info["title"], strict=False)
    else:
        game.set_process_target(rid, hwnd=0, title="", strict=False)
    return state


@_locked
def list_rows(infos=None):
    """多窗口行数据（只含当前活窗口；关闭的窗口不出行）。"""
    state = sync_live(infos)
    infos = infos if infos is not None else game.list_main_window_infos()
    roles = state["roles"]
    selected = state.get("selected")
    selected_hwnd = int(state.get("selected_hwnd") or 0)
    # 窗口 Tab 的固定顺序（2026-11-22 v2）：角色首次出现时追加到 order，
    # 之后运行/选中/勾选都不重排（Tab 位置跳动会点错）。order 每轮按
    # 「活窗口 ∪ 已登记角色」裁剪，不会无限增长；新窗口追加到末尾。
    known = []
    seen_ids = set()
    for info in infos:
        rid = str(info.get("role_id") or "")
        if rid and rid not in seen_ids:
            known.append(rid)
            seen_ids.add(rid)
    for rid in roles:
        if rid not in seen_ids:
            known.append(rid)
            seen_ids.add(rid)
    old_order = state.get("order") or []
    order = [rid for rid in old_order if rid in seen_ids]
    order_set = set(order)
    for rid in known:
        if rid not in order_set:
            order.append(rid)
            order_set.add(rid)
    if order != old_order:
        state["order"] = order
        _save(state)
    role_order = {rid: i for i, rid in enumerate(order)}
    rows = []
    seen_selected = False
    role_seen = set()

    for info_idx, info in enumerate(infos):
        rid = info.get("role_id")
        if not rid:
            rows.append({"key": f"hwnd:{info['hwnd']}", "role_id": None,
                         "label": info.get("title") or "未登录窗口",
                         "server": info.get("server") or "",
                         "name": info.get("name") or "",
                         "hwnd": info["hwnd"], "title": info.get("title") or "",
                         "live": True, "logged_in": False,
                         "enabled": False, "selected": False,
                         "selectable": False, "duplicate": False,
                         "seq": 200000 + info_idx})
            continue
        meta = roles.get(rid, {})
        duplicate = rid in role_seen
        role_seen.add(rid)
        is_selected = (rid == selected and not seen_selected
                       and (not selected_hwnd
                            or int(info["hwnd"]) == selected_hwnd))
        if is_selected:
            seen_selected = True
        rows.append({"key": f"{rid}:{info['hwnd']}", "role_id": rid,
                     "label": _row_label(info, meta),
                     "server": info.get("server") or "",
                     "name": info.get("name") or "",
                     "hwnd": info["hwnd"], "title": info.get("title") or "",
                     "live": True,
                     # 标题含 ID ≠ 已登录：被顶号时画面弹出顶号对话框，
                     # list_main_window_infos 已按未登录返回（2026-11-22）
                     "logged_in": bool(info.get("logged_in", True)),
                     "enabled": bool(meta.get("enabled")),
                     "selected": is_selected,
                     "selectable": True, "duplicate": duplicate,
                     "seq": role_order.get(rid, 100000 + info_idx)})

    live_roles = {i.get("role_id") for i in infos if i.get("role_id")}
    # 组号 pass：选中角色的窗口还在，但标题显示的是另一个登录 Tab 的角色。
    # 不能把它当「已关闭」移除——保留一行选中行，否则面板会显示未选择
    # 窗口，而设置/方案仍跟随该角色。真正关闭的窗口不再出行。
    if (selected and not seen_selected and selected not in live_roles
            and selected_hwnd):
        win_info = next((i for i in infos
                         if int(i.get("hwnd") or 0) == selected_hwnd), None)
        win = _win_by_hwnd(selected_hwnd) if win_info is not None else None
        if win is not None and _role_in_window(win, selected):
            meta = roles.get(selected) or {}
            rows.append({"key": f"{selected}:{selected_hwnd}",
                         "role_id": selected,
                         "label": meta.get("label") or selected,
                         "server": "", "name": "",
                         "hwnd": selected_hwnd,
                         "title": win_info.get("title") or "",
                         "live": True, "logged_in": True,
                         "enabled": bool(meta.get("enabled")),
                         "selected": True, "selectable": True,
                         "duplicate": False,
                         "seq": role_order.get(selected, 100000)})

    rows.sort(key=lambda r: (
        0 if r["selected"] else 1,
        0 if r["enabled"] else 1,
        0 if (r["live"] and r["logged_in"]) else 1,
        0 if r["selectable"] else 1,
        str(r["role_id"] or r["key"]),
    ))
    return {"selected": selected, "selected_hwnd": selected_hwnd,
            "rows": rows}


# ==================== 查询辅助 ====================

@_locked
def selected_role():
    return _state().get("selected")


@_locked
def selected_hwnd():
    return int(_state().get("selected_hwnd") or 0)


@_locked
def selected_label():
    """选中角色短文案（活窗口标题 → 持久化标签 → 角色 ID）。"""
    state = sync_live()
    rid = state.get("selected")
    if not rid:
        return ""
    info = _info_for_role(rid, game.list_main_window_infos())
    if info is not None:
        return _row_label(info, state["roles"].get(rid))
    meta = state["roles"].get(rid) or {}
    return meta.get("label") or rid


@_locked
def enabled_roles():
    """已勾选参与多开的角色 ID 列表（稳定顺序）。"""
    state = _state()
    return sorted(rid for rid, meta in state["roles"].items()
                  if meta.get("enabled"))


@_locked
def resolve_target(role_id=None, hwnd=None):
    """解析启动目标（角色 + 活窗口句柄/标题 + 实时登录态）。

    登录态 fresh 复检（标题 + 顶号对话框画面判定）：调用方为启动守卫，
    不复用看板 5s 缓存，避免在缓存窗口内拉起已被顶号的窗口。
    窗口不存在返回 None。
    """
    state = _state()
    rid = str(role_id or state.get("selected") or "")
    if not re.fullmatch(r"\d+", rid):
        return None
    win = _win_by_hwnd(hwnd) if hwnd else None
    if win is None:
        win = _win_for_role(rid)
    if win is None:
        return None
    return {"role_id": rid,
            "hwnd": int(getattr(win, "hwnd", 0) or 0),
            "title": getattr(win, "title", "") or "",
            "logged_in": game.window_logged_in(win, fresh=True)}
