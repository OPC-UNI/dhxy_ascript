"""模块依赖方向检查：把 docs/conventions.md 的规则变成可执行测试。

当前阶段（P2 完成）强制：
1. 任务模块之间禁止 import（CLAUDE.md「任务模块隔离」规则）。
2. 通用/设备/流程/小游戏模块禁止 import 任务模块。
3. 非 app 层禁止 import app 层（设置读取走 core/settings）。
4. 低层禁止 import 高层（core ← device ← workflows/minigames ← tasks），
   `UPWARD_ALLOWLIST` 精确登记 core→device 的历史延迟导入。
5. `src.*` 导入目标必须真实存在，旧平铺路径残留（含延迟导入）直接失败；
   两个白名单都做 stale 检查，只减不增。

新增例外必须同时：写进白名单 + 注释原因 + 标注清理阶段；不允许
「先 import 再补规范」。
"""
import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"

# 已知债务：P1 完成后为空（保留机制，供未来临时例外登记；
# 白名单只许删、不许加）
TASK_EDGE_ALLOWLIST = {}
# 包 __init__ 里对外暴露的属性（不是子模块）：from 包 import 属性 不算
# 旧模块路径，例如 src.app.control_window 的 `from src import __version__`。
PACKAGE_SYMBOLS = {"src": {"__version__"}}


def _collect_modules():
    """模块名（src.x.y）→ 文件路径。"""
    mods = {}
    for path in SRC_ROOT.rglob("*.py"):
        rel = path.relative_to(SRC_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(["src"] + parts) if parts else "src"
        mods[name] = path
    return mods


def _is_package(module, modules):
    return any(m.startswith(module + ".") for m in modules)


def _resolve_from(importer, node, modules):
    """解析 from-import 的目标模块（含 from 包里导入子模块的边）。

    `from ..device import game` 同时记录 `src.device` 与 `src.device.game`：
    前者是包级依赖，后者让「core 新增任意 device 子模块依赖」无法借包级
    白名单蒙混过关。只返回真实存在的模块。
    """
    # 当前文件所属包：__init__.py 即包自身，普通模块取父级
    if _is_package(importer, modules) and modules[importer].name == "__init__.py":
        pkg = importer.split(".")
    else:
        pkg = importer.split(".")[:-1]

    if node.level == 0:
        if not node.module:
            return []
        targets = [node.module] if node.module in modules else []
        targets.extend(node.module + "." + a.name for a in node.names
                       if node.module + "." + a.name in modules)
        return targets

    base = pkg[:len(pkg) - (node.level - 1)]
    if node.module:
        target = ".".join(base + [node.module])
        targets = [target] if target in modules else []
        targets.extend(target + "." + a.name for a in node.names
                       if target + "." + a.name in modules)
        return targets
    return [t for a in node.names
            if (t := ".".join(base + [a.name])) in modules]


def _collect_edges():
    """所有内部 import 边（含函数内延迟导入；ast.walk 全覆盖）。"""
    modules = _collect_modules()
    edges = set()
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for target in _resolve_from(name, node, modules):
                    if target != name:
                        edges.add((name, target))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules and alias.name != name:
                        edges.add((name, alias.name))
    return edges


def _collect_unresolved():
    """指向不存在 src.* 模块的 import（迁移后旧路径残留）。

    只看 from/import 的模块路径，不把 from 后导入的符号当模块，避免
    `from x import symbol` 误报。
    """
    modules = _collect_modules()
    bad = set()
    for name, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("src.") \
                            and alias.name not in modules:
                        bad.add((name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module == "src":
                        for a in node.names:
                            if a.name in PACKAGE_SYMBOLS.get("src", set()):
                                continue
                            target = "src." + a.name
                            if target not in modules:
                                bad.add((name, target))
                    elif node.module and node.module.startswith("src.") \
                            and node.module not in modules:
                        bad.add((name, node.module))
                else:
                    pkg = (name.split(".") if _is_package(name, modules)
                           and modules[name].name == "__init__.py"
                           else name.split(".")[:-1])
                    base = pkg[:len(pkg) - (node.level - 1)]
                    if node.module:
                        target = ".".join(base + [node.module])
                        if target.startswith("src.") \
                                and target not in modules:
                            bad.add((name, target))
                    else:
                        pkg_name = ".".join(base)
                        for a in node.names:
                            if a.name in PACKAGE_SYMBOLS.get(pkg_name, set()):
                                continue
                            target = ".".join(base + [a.name])
                            if target.startswith("src.") \
                                    and target not in modules:
                                bad.add((name, target))
    return bad


def test_no_unresolved_src_imports():
    """旧平铺路径残留：导入目标必须是真实模块。

    否则函数内延迟导入写成旧路径时规则测试会静默丢弃，只有运行到该
    函数才 ImportError。
    """
    bad = _collect_unresolved()
    assert not bad, "存在指向不存在模块的 src.* 导入：\n" + _fmt(bad)


def _layer(module):
    """模块所属层：core/device/workflows/minigames/tasks/app。

    目录即分层（P2 迁移后成立）；未归入任何已知子包的文件视为共享模块。
    """
    parts = module.split(".")
    if len(parts) == 1:  # src/__init__.py
        return "app"
    top = parts[1]
    if top in {"core", "device", "workflows", "minigames", "tasks", "app"}:
        return top
    return "shared"


def _fmt(edges):
    return "\n".join("  %s -> %s" % (a, b) for a, b in sorted(edges))


def test_checker_sees_known_edges():
    """自检：解析器确实能看见内部 import（防止规则空转）。"""
    edges = _collect_edges()
    assert ("src.tasks.kezhan", "src.workflows.follow") in edges  # P1 新共享引擎
    assert ("src.device.battle", "src.device.team") in edges       # 顶层 from . import
    assert ("src.device.battle", "src.device.repair") in edges     # 函数内延迟导入
    # P0/P1 修复的反向依赖不得再出现（含函数内延迟导入）
    assert ("src.device.battle", "src.tasks.xuanshu") not in edges
    assert ("src.device.dialog_common", "src.tasks.kezhan") not in edges
    assert ("src.tasks.kezhan", "src.tasks.xuanshu") not in edges


def test_no_task_to_task_imports():
    """任务模块之间禁止 import（白名单仅 P1 前的 follow 债务）。"""
    violations = {
        (a, b) for a, b in _collect_edges()
        if _layer(a) == "tasks" and _layer(b) == "tasks"
        and (a, b) not in TASK_EDGE_ALLOWLIST
    }
    assert not violations, (
        "任务模块之间禁止互相 import（共享能力请下沉到 workflows/ 或 device/）：\n"
        + _fmt(violations))


def test_no_shared_to_task_imports():
    """通用/设备/流程/小游戏模块禁止 import 任务模块。"""
    violations = {
        (a, b) for a, b in _collect_edges()
        if _layer(a) in {"shared", "core", "device", "workflows", "minigames"}
        and _layer(b) == "tasks"
    }
    assert not violations, (
        "通用模块不得依赖任务模块（任务专属逻辑应留在任务模块，"
        "通用诊断/能力改用回调注入或下沉到共享层）：\n" + _fmt(violations))


def test_no_non_app_to_app_imports():
    """非 app 层禁止 import app 层（设置读取走 core/settings）。"""
    violations = {
        (a, b) for a, b in _collect_edges()
        if _layer(a) != "app" and _layer(b) == "app"
    }
    assert not violations, (
        "非 app 模块不得依赖 control_window 等 app 层（设置读取请用 "
        "core/settings）：\n" + _fmt(violations))



# 分层方向的已知债务：core 的底层工具对 device 有函数内延迟导入
# （app_utils→silent_click/game/team、user_data→game，历史环）；
# 白名单精确到子模块，只减不增：新增 core→device 依赖直接测试失败。
UPWARD_ALLOWLIST = {
    ("src.core.app_utils", "src.device"): "静默点击/当前任务模式/队伍判断（历史环）",
    ("src.core.app_utils", "src.device.silent_click"): "静默点击（历史环）",
    ("src.core.app_utils", "src.device.game"): "当前任务模式（历史环）",
    ("src.core.app_utils", "src.device.team"): "队伍判断（历史环）",
    ("src.core.user_data", "src.device"): "当前角色窗口解析（历史环）",
    ("src.core.user_data", "src.device.game"): "当前角色窗口解析（历史环）",
}
LAYER_ORDER = {"core": 0, "device": 1, "workflows": 2, "minigames": 2,
               "tasks": 3, "app": 9}


def test_no_upward_layer_imports():
    """禁止低层 import 高层（core ← device ← workflows/minigames ← tasks）。"""
    violations = set()
    for a, b in _collect_edges():
        la, lb = _layer(a), _layer(b)
        if la not in LAYER_ORDER or lb not in LAYER_ORDER:
            continue
        if la == "app" or lb == "app":
            continue
        if LAYER_ORDER[la] < LAYER_ORDER[lb] and (a, b) not in UPWARD_ALLOWLIST:
            violations.add((a, b))
    assert not violations, (
        "低层不得 import 高层（共享能力请下沉到更低的层）：\n"
        + _fmt(violations))


def test_allowlist_has_no_stale_entries():
    """白名单只许删：边已消失说明债务修完，必须同步清理白名单。"""
    edges = _collect_edges()
    stale = (set(TASK_EDGE_ALLOWLIST) | set(UPWARD_ALLOWLIST)) - edges
    assert not stale, (
        "以下白名单条目已不再是实际依赖（已完成迁移），请从对应白名单"
        "删除：\n" + _fmt(stale))
