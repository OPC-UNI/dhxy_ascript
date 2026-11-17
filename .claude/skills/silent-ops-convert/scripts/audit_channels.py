# -*- coding: utf-8 -*-
"""输入通道审计：物理鼠标/键盘（非静默）vs 窗口消息投递（静默）。

用法（仓库根目录运行）：
    python .claude/skills/silent-ops-convert/scripts/audit_channels.py
    python .claude/skills/silent-ops-convert/scripts/audit_channels.py --root src
    python .claude/skills/silent-ops-convert/scripts/audit_channels.py --strict

输出：
  1) 物理输入调用（Mouse.* / Keyboard.* / *.activate()），逐条给出
     建议的静默对应物与所在函数；
  2) 静默投递调用（send_silent_* / begin_silent_hold / end_silent_hold）；
  3) 门控调用统计（silent_mode_on / keyboard_ok / silent_clicks）。

--strict：判定「疑似漏接静默通道」并退出码 1。规则：
  - src/core/app_utils.py 是统一封装层，其内部的 Mouse/Keyboard 调用豁免；
  - 行内或上一行带 `# silent-ok` 注释的调用豁免（显式说明仅物理）；
  - 物理调用所在函数体内没有 silent_mode_on / keyboard_ok / silent_clicks
    门控 → 视为违规（该操作只有物理通道，静默模式下会抢前台/占用鼠标）。

脚本纯标准库、只读、不发任何输入，供 skill 审计用。
"""
import argparse
import ast
import pathlib
import sys

# .claude/skills/silent-ops-convert/scripts/audit_channels.py → 仓库根
_REPO = pathlib.Path(__file__).resolve().parents[4]
_WRAPPER = "src/core/app_utils.py"

# 物理调用后缀 → (分类, 建议静默对应物)。按顺序匹配第一个命中项。
_PHYSICAL_RULES = (
    ("Mouse.mouse_move", "鼠标移动",
     "human_move（静默模式自动空操作）"),
    ("Mouse.click", "鼠标点击",
     "human_click/precise_click/rapid_click（自动门控）"),
    ("Mouse.mouse_down", "鼠标按下",
     "MouseHold / rapid_click_burst（自动门控）"),
    ("Mouse.mouse_up", "鼠标抬起",
     "MouseHold / rapid_click_burst（自动门控）"),
    ("Keyboard.key_hot", "快捷键",
     'send_silent_hotkey(win, ("alt",), key)；Ctrl+Tab 用 send_silent_tab_click'),
    ("Keyboard.key", "单键",
     "send_silent_hotkey(win, (), key)"),
    ("Keyboard.input", "文本输入",
     "send_silent_text(win, text, end_enter=...)"),
    ("Keyboard.write", "文本输入", "send_silent_text(win, text)"),
    ("Keyboard.type", "文本输入", "send_silent_text(win, text)"),
)

_SILENT_NAMES = (
    "send_silent_click", "send_silent_click_burst", "begin_silent_hold",
    "end_silent_hold", "send_silent_hotkey", "send_silent_tab_click",
    "send_silent_text", "send_silent_char", "send_silent_paste",
    "send_silent_unichar", "set_clipboard_text",
)
_GATE_NAMES = ("silent_mode_on", "keyboard_ok", "silent_clicks",
               "set_silent_mode")


def _dotted(node):
    """Attribute 链 → 点号字符串（如 keyboard.Keyboard.key_hot）。"""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append(_dotted(node.func) + "()")
    else:
        parts.append("?")
    return ".".join(reversed(parts))


def _last_attr(node):
    """Call.func 的末级属性名；非属性/名字调用返回空串。"""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _classify_physical(dotted):
    """物理调用 → (分类, 建议)；非物理输入返回 None。"""
    for suffix, kind, hint in _PHYSICAL_RULES:
        if dotted.endswith(suffix):
            return kind, hint
    if dotted.endswith(".activate") or dotted == "activate":
        return "前台激活", "静默分支禁止 activate；改用 keyboard_ok(win) 判前台"
    return None


def _marked(lines, lineno):
    """该行或上一行带 `# silent-ok` 注释（显式豁免 strict）。"""
    for idx in (lineno - 1, lineno - 2):
        if 0 <= idx < len(lines) and "# silent-ok" in lines[idx]:
            return True
    return False


class _Collector(ast.NodeVisitor):
    """收集物理输入 / 静默投递 / 门控调用，并记录所在函数是否带门控。"""

    def __init__(self, rel, lines):
        self.rel = rel
        self.lines = lines
        self.stack = []      # 函数上下文：[{"label", "gated"}]
        self.classes = []    # 类名栈（仅用于展示）
        self.physical = []
        self.silent = []
        self.gates = []

    # ---- 作用域 ----
    def _label(self, name):
        parts = self.classes + [name]
        return ".".join(parts)

    def visit_ClassDef(self, node):
        self.classes.append(node.name)
        self.generic_visit(node)
        self.classes.pop()

    def visit_FunctionDef(self, node):
        ctx = {"label": self._label(node.name), "gated": False}
        self.stack.append(ctx)
        self.generic_visit(node)
        self.stack.pop()

    def visit_AsyncFunctionDef(self, node):
        self.visit_FunctionDef(node)

    # ---- 调用点 ----
    def visit_Call(self, node):
        dotted = _dotted(node.func)
        name = _last_attr(node.func)
        if name in _GATE_NAMES:
            if self.stack:
                self.stack[-1]["gated"] = True
            self.gates.append((node.lineno, dotted, name))
        if name in _SILENT_NAMES:
            self.silent.append({
                "line": node.lineno,
                "col": node.col_offset,
                "func": self.stack[-1]["label"] if self.stack else "(模块级)",
                "call": dotted,
            })
        hit = _classify_physical(dotted)
        if hit is not None:
            kind, hint = hit
            self.physical.append({
                "line": node.lineno,
                "col": node.col_offset,
                "func": self.stack[-1]["label"] if self.stack else "(模块级)",
                "call": dotted,
                "kind": kind,
                "hint": hint,
                "ctx": self.stack[-1] if self.stack else None,
                "marked": _marked(self.lines, node.lineno),
                "wrapper": self.rel == _WRAPPER,
            })
        self.generic_visit(node)


def _scan(root):
    """遍历 root 下所有 .py，返回 (collectors, skipped)。"""
    collectors, skipped = [], []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_REPO).as_posix() if path.is_relative_to(_REPO) \
            else path.as_posix()
        try:
            src = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(src, filename=str(path))
        except (SyntaxError, UnicodeDecodeError, OSError) as e:
            skipped.append((rel, str(e)))
            continue
        c = _Collector(rel, src.splitlines())
        c.visit(tree)
        collectors.append(c)
    return collectors, skipped


def _print_physical(collectors):
    print("== 物理输入（非静默通道）==")
    total = 0
    for c in collectors:
        for r in c.physical:
            total += 1
            tail = r["call"].split(".")[-2:]
            call = ".".join(tail)
            flags = []
            if r["wrapper"]:
                flags.append("封装层")
            if r["marked"]:
                flags.append("silent-ok")
            gate = "有门控" if (r["ctx"] and r["ctx"]["gated"]) else "无门控"
            print(f"  {c.rel}:{r['line']}  [{r['func']}]  {call}"
                  f"  ({r['kind']}；{gate}{'；' + '、'.join(flags) if flags else ''})")
            print(f"      → {r['hint']}")
    if not total:
        print("  （无）")
    print()
    return total


def _print_silent(collectors):
    print("== 静默投递（send_silent_* / hold）==")
    total = 0
    for c in collectors:
        for r in c.silent:
            total += 1
            print(f"  {c.rel}:{r['line']}  [{r['func']}]  {r['call'].split('.')[-1]}")
    if not total:
        print("  （无）")
    print()
    return total


def _print_gates(collectors):
    counts = {}
    total = 0
    for c in collectors:
        for _line, _dotted, name in c.gates:
            counts[name] = counts.get(name, 0) + 1
            total += 1
    detail = "，".join(f"{k} × {v}" for k, v in sorted(counts.items()))
    print("== 门控调用 ==")
    print(f"  共 {total} 处" + (f"（{detail}）" if detail else ""))
    print()
    return total


def _violations(collectors):
    """strict 违规：物理输入不在封装层、未标记，且所在函数无静默门控。"""
    out = []
    for c in collectors:
        for r in c.physical:
            if r["wrapper"] or r["marked"]:
                continue
            if r["ctx"] is not None and r["ctx"]["gated"]:
                continue
            out.append((c.rel, r))
    return out


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(
        description="审计 src/ 的物理输入与静默投递通道")
    ap.add_argument("--root", default="src",
                    help="扫描目录（相对仓库根或绝对路径，默认 src）")
    ap.add_argument("--strict", action="store_true",
                    help="存在「无静默门控且未豁免」的物理输入时退出码 1")
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root)
    if not root.is_absolute():
        root = (_REPO / root).resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}")
        return 2

    collectors, skipped = _scan(root)
    n_phys = _print_physical(collectors)
    n_silent = _print_silent(collectors)
    _print_gates(collectors)

    print("== 汇总 ==")
    print(f"  扫描文件 {len(collectors)} 个；物理输入 {n_phys} 处；"
          f"静默投递 {n_silent} 处")
    for rel, err in skipped:
        print(f"  跳过 {rel}: {err}")

    bad = _violations(collectors)
    if args.strict:
        print()
        print("== --strict 违规（物理输入无静默门控）==")
        if not bad:
            print("  （无）")
        for rel, r in bad:
            print(f"  {rel}:{r['line']}  [{r['func']}]  "
                  f"{r['call'].split('.')[-1]}（{r['kind']}）")
            print(f"      → {r['hint']}")
        if bad:
            print(f"\n共 {len(bad)} 处待转换/待标记。")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
