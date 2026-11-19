#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release-tag 发版辅助脚本（.claude/skills/release-tag/scripts/release.py）

用法（在仓库根运行，Windows 用 python 调用）：
    python .claude/skills/release-tag/scripts/release.py check <X.Y.Z>
    python .claude/skills/release-tag/scripts/release.py bump  <X.Y.Z>
    python .claude/skills/release-tag/scripts/release.py precommit
    python .claude/skills/release-tag/scripts/release.py tagcheck <X.Y.Z>
    python .claude/skills/release-tag/scripts/release.py test
    python .claude/skills/release-tag/scripts/release.py diffsum [--width 200] [--max-lines N]

封装发版/提交流程中确定性、易错的机械步骤；判断性步骤（提交信息撰写、
git commit/push/tag）仍由执行者按 SKILL.md 手动完成：

  check  发版前核对：版本格式 → tag 序列递增且未占用 → 双文件版本同步
         → 目标版本不低于当前（等于=已 bump 完成，跳过 bump）→ 列出
         git add -A 将提交的全部改动；未跟踪文件命中疑似运行时数据
         名单时警告（先决定是否进 .gitignore 再提交）
  bump   原子更新 src/__init__.py 与 pyproject.toml 两处版本号：
         校验同 check 且要求目标版本严格高于当前；字节级正则替换
         （不动换行符与其余内容）；第二处写入失败时回滚第一处
  precommit  仅提交推送（不打 tag、不 bump）的提交前核对：列出 git add -A
         将提交的全部改动 + 未跟踪运行时数据警告（同 check 的清单部分，
         不做版本/tag 校验）
  tagcheck   仅打 tag（不 bump、不提交）的前核对：版本格式 → tag 序列
         递增且未占用 → 双文件版本号互相同步（不要求等于目标；CI 构建
         时会把 tag 版本号烙进产物，滞后仅提示）→ 工作区干净 → 本地
         HEAD 已推送（无上游时仅提示）
  test   全量测试 python -m pytest -q，退出码透传（未全绿不得发版/提交）
  diffsum  提交信息撰写用的压缩改动摘要：文件 stat + git diff HEAD -U0
         （去掉上下文与 index/mode 杂项头），每条改动行截断至 --width
         字符（默认 200；docs 里几千字符的表格单行不再整行刷屏），
         --max-lines N 可按文件再限行数；未跟踪文件只列路径/大小不读
         内容。需要完整内容时按文件单独 git diff HEAD -- <path>
         （2026-11-19 教训：无上下文会话里读全量 diff 撰写提交信息，
         超长单行表格占掉大半 token）

版本参数可带可不带 v 前缀（tag 名仍以用户请求原文为准，脚本只校验数字）。
仓库根按当前工作目录经 git rev-parse 解析（脚本路径所在仓库无关紧要）。
退出码 0 = 通过，非 0 = 校验/命令失败。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    REPO = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=".", text=True, encoding="utf-8", errors="replace").strip())
except Exception:  # git 不可用时按脚本位置推导仓库根
    REPO = Path(__file__).resolve().parents[4]

INIT_PY = REPO / "src" / "__init__.py"
PYPROJECT = REPO / "pyproject.toml"
_INIT_RE = re.compile(rb'^__version__\s*=\s*"([^"]+)"', re.M)
_PYPROJ_RE = re.compile(rb'^version\s*=\s*"([^"]+)"', re.M)

# 疑似运行时数据（git add -A 前逐个确认是否该进 .gitignore；
# v2.9.2 教训：task_stats.json 是跨会话用户数据，入库前被拦下）
RUNTIME_SUSPECT = {
    "task_stats.json", "debug_overlay_state.json",
    "debug_overlay_helper_exit.txt", "config.json", "path_cache.json",
    "schemes.json", "first_frame.png", "tmp_err_img.png", "tmp_team_err.png",
}
RUNTIME_SUSPECT_PARTS = (".npz", "capture_diag_", "qi_debug", "logs/")

VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _fail(msg):
    print(f"失败：{msg}")
    sys.exit(1)


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=REPO,
                                   text=True, encoding="utf-8",
                                   errors="replace")


def _parse(raw):
    """vX.Y.Z / X.Y.Z → (x, y, z)；格式不对直接失败退出。"""
    m = VERSION_RE.fullmatch(raw.strip().lstrip("v"))
    if not m:
        _fail(f"版本号格式不正确：{raw!r}（须为 vX.Y.Z 或 X.Y.Z 三段数字）")
    return tuple(int(x) for x in m.groups())


def _latest_tag():
    tags = [t for t in _git("tag").splitlines()
            if VERSION_RE.fullmatch(t.lstrip("v"))]
    return max(tags, key=lambda t: _parse(t)) if tags else None


def _extract(path, regex, label):
    if not path.exists():
        _fail(f"未找到 {path.relative_to(REPO)}（{label} 定义缺失）")
    m = regex.search(path.read_bytes())
    if not m:
        _fail(f"{path.relative_to(REPO)} 中未找到 {label} 定义")
    return m.group(1).decode()


def _read_versions():
    init = _extract(INIT_PY, _INIT_RE, "__version__")
    proj = _extract(PYPROJECT, _PYPROJ_RE, "version")
    if init != proj:
        _fail(f"两处版本号不一致：__init__.py={init}，pyproject.toml={proj}，"
              f"请先手动对齐")
    return _parse(init)


def _validate(raw, mode):
    """公共校验：格式 → tag 序列 → tag 未占用 → 双文件同步 → 目标版本高低。

    mode="check"：目标 >= 当前文件版本（等于=已 bump 完成，提示）；
    mode="bump" ：目标必须严格高于当前文件版本。返回 (版本元组, 当前版本元组)。
    """
    ver = _parse(raw)
    latest = _latest_tag()
    if latest is not None and _parse(latest) >= ver:
        _fail(f"版本未递增：已有最大 tag 为 {latest}，目标 {raw} 不大于它")
    tags = set(_git("tag").splitlines())
    vstr = ".".join(map(str, ver))
    for cand in (f"v{vstr}", vstr):
        if cand in tags:
            _fail(f"tag {cand} 已存在")
    cur = _read_versions()
    if mode == "bump" and ver <= cur:
        _fail(f"目标版本 {vstr} 未高于当前文件版本 {'.'.join(map(str, cur))}"
              f"{'（版本未变化）' if ver == cur else ''}")
    if mode == "check" and ver < cur:
        _fail(f"目标版本 {vstr} 低于当前文件版本 {'.'.join(map(str, cur))}")
    return ver, cur


def _suspect(rel_path):
    name = rel_path.replace("\\", "/").split("/")[-1]
    if name in RUNTIME_SUSPECT:
        return True
    return any(part in rel_path for part in RUNTIME_SUSPECT_PARTS)


def _list_changes():
    """列出 git add -A 将提交的全部改动 + 未跟踪运行时数据警告。"""
    lines = [l for l in _git("status", "--porcelain").splitlines() if l.strip()]
    if not lines:
        print("通过：工作区干净，无待提交改动")
        return
    print(f"通过：工作区有 {len(lines)} 项改动（git add -A 将全部提交）：")
    for l in lines:
        print(f"  {l}")
    untracked = [l[3:].strip() for l in lines if l.startswith("??")]
    if untracked:
        print(f"注意：未跟踪文件 {len(untracked)} 个将被一并提交，"
              f"逐个确认是否该进 .gitignore：")
        for f in untracked:
            warn = "  ← 疑似运行时数据，先确认 gitignore" if _suspect(f) else ""
            print(f"  ?? {f}{warn}")


def do_check(args):
    ver, cur = _validate(args.version, "check")
    vstr = ".".join(map(str, ver))
    latest = _latest_tag()
    print(f"通过：版本序列校验（目标 v{vstr} > 已有最大 tag "
          f"{latest or '（无）'}，tag 未占用）")
    if ver == cur:
        print(f"注意：两处版本号已是 {vstr}（bump 已完成或无需 bump）")
    else:
        print(f"通过：双文件版本同步（当前 {'.'.join(map(str, cur))}）")
    _list_changes()
    if ver == cur:
        print("通过：发版前核对完成（版本号已是目标，跳过 bump："
              "测试 → 提交推送 → 打 tag）")
    else:
        print("通过：发版前核对完成（下一步 bump）")


def do_precommit(_args):
    """仅提交推送（不打 tag、不 bump）的提交前核对。"""
    _list_changes()
    print("通过：提交前核对完成（仅提交推送：不 bump 版本、不打 tag）")


def do_tagcheck(args):
    """仅打 tag（不 bump、不提交）的前核对：当前 HEAD 是否可直接打 tag。"""
    ver = _parse(args.version)
    vstr = ".".join(map(str, ver))
    latest = _latest_tag()
    if latest is not None and _parse(latest) >= ver:
        _fail(f"版本未递增：已有最大 tag 为 {latest}，目标 {args.version} 不大于它")
    tags = set(_git("tag").splitlines())
    for cand in (f"v{vstr}", vstr):
        if cand in tags:
            _fail(f"tag {cand} 已存在")
    cur = _read_versions()  # 只要求两处互相同步，不要求等于目标
    cur_str = ".".join(map(str, cur))
    if cur != ver:
        print(f"注意：两处版本号当前为 {cur_str}，与目标 v{vstr} 不一致"
              f"（不要求 bump：CI 构建时会把 tag 版本号烙进构建产物，"
              f"源码版本下次发版再对齐）")
    changed = [l for l in _git("status", "--porcelain").splitlines() if l.strip()]
    if changed:
        _fail(f"工作区有 {len(changed)} 项未提交改动：tag 必须指向已提交"
              f"状态，先提交或还原后再打 tag")
    try:
        ahead = int(_git("rev-list", "--count", "@{u}..HEAD").strip() or "0")
    except subprocess.CalledProcessError:
        pushed = "HEAD 推送状态未校验（无上游跟踪分支）"
        print("注意：当前分支无上游跟踪分支，跳过「HEAD 已推送」校验，"
              "打 tag 前请确认已 git push")
    else:
        if ahead:
            _fail(f"本地 HEAD 领先上游 {ahead} 个提交（未推送）："
                  f"先 git push 再打 tag")
        pushed = "HEAD 已推送"
    suffix = "" if cur == ver else f"（≠ 目标 v{vstr}，仅提示）"
    print(f"通过：版本序列校验（目标 v{vstr} > 已有最大 tag "
          f"{latest or '（无）'}，tag 未占用）")
    print(f"通过：两处版本号互相同步为 {cur_str}{suffix}")
    print(f"通过：工作区干净、{pushed}")
    print(f"通过：打 tag 前核对完成（下一步 git tag {args.version} && "
          f"git push origin {args.version}）")


def do_bump(args):
    ver, cur = _validate(args.version, "bump")
    vstr = ".".join(map(str, ver))
    originals = {}
    try:
        for path, regex, label in ((INIT_PY, _INIT_RE, "__version__"),
                                   (PYPROJECT, _PYPROJ_RE, "version")):
            data = path.read_bytes()
            updated, n = regex.subn(f'{label} = "{vstr}"'.encode(), data,
                                    count=1)
            if n != 1:  # 双保险（_validate 已读过两处定义）
                _fail(f"{path.relative_to(REPO)} 中 {label} 定义替换失败")
            path.write_bytes(updated)
            originals[path] = data
    except SystemExit:
        raise
    except Exception:
        for path, data in originals.items():  # 回滚已写入的文件
            path.write_bytes(data)
        raise
    print(f"通过：版本号 {'.'.join(map(str, cur))} → {vstr}"
          f"（src/__init__.py、pyproject.toml 两处已同步更新）")


def do_test(_args):
    rc = subprocess.run([sys.executable, "-m", "pytest", "-q"],
                        cwd=REPO).returncode
    if rc != 0:
        print("失败：pytest 未全绿（必须全绿才能发版）")
    sys.exit(rc)


# ---- diffsum：提交信息撰写用的压缩改动摘要 ----

# diff 头阶段省略的杂项行（index 哈希 / 文件模式 / rename 等对提交
# 信息无贡献；---/+++ 与 diff --git 行重复）
_DIFF_HEAD_NOISE = (
    "index ", "old mode ", "new mode ", "deleted file mode ",
    "new file mode ", "similarity index ", "dissimilarity index ",
    "rename from ", "rename to ", "copy from ", "copy to ",
    "--- ", "+++ ", "\\ No newline",
)


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _truncate(line, width):
    """改动行截断：保留行首标记（+/-），超出部分折算为字符数提示。"""
    if width <= 0 or len(line) <= width + 1:
        return line
    extra = len(line) - (width + 1)
    return f"{line[:width + 1]} …[+{extra}字符]"


def _diffsum_untracked():
    """未跟踪条目只列路径/大小，不读内容（内容按需单独查看）。"""
    out = []
    for l in _git("status", "--porcelain").splitlines():
        if not l.startswith("??"):
            continue
        rel = l[3:].strip().strip('"').rstrip("/")
        p = REPO / rel
        try:
            if p.is_dir():
                files = [f for f in p.rglob("*") if f.is_file()]
                size = sum(f.stat().st_size for f in files)
                out.append(f"  ?? {rel}/ （目录，{len(files)} 个文件，"
                           f"{_human_size(size)}）")
            elif p.is_file():
                out.append(f"  ?? {rel} （{_human_size(p.stat().st_size)}）")
            else:
                out.append(f"  ?? {rel}")
        except OSError:
            out.append(f"  ?? {rel}")
    return out


def do_diffsum(args):
    """压缩改动摘要：文件 stat + 截断的改动行（无上下文、去头杂项）。

    供撰写提交信息使用；完整内容按文件用 git diff HEAD -- <path> 细读。
    """
    width, max_lines = args.width, args.max_lines
    stat = _git("diff", "HEAD", "--stat").strip()
    diff = _git("diff", "HEAD", "-U0").splitlines()
    untracked = _diffsum_untracked()
    if not stat and not diff and not untracked:
        print("通过：工作区干净，无待提交改动")
        return
    wdesc = "不截断" if width <= 0 else f"截断至 {width} 字符"
    print(f"说明：提交信息撰写用压缩摘要（改动行{wdesc}、无上下文、"
          "省略 index/mode 头）；需要完整内容用 "
          "git diff HEAD -- <path> 按文件细读")
    print("== 文件摘要（git diff HEAD --stat）==")
    print(stat if stat else "  （无已跟踪文件改动）")
    print("== 改动行摘要（git diff HEAD -U0）==")
    if not diff:
        print("  （无已跟踪文件改动）")
    in_head = False   # 是否处于 diff 头阶段（hunk 之前）
    shown = 0         # 当前文件已显示改动行数
    hidden = 0        # 当前文件因 --max-lines 隐藏的改动行数

    def _flush_hidden():
        nonlocal hidden
        if hidden:
            print(f"…（该文件另有 {hidden} 行变更未显示，"
                  f"用 git diff HEAD -- <path> 查看）")
            hidden = 0

    for line in diff:
        if line.startswith("diff --git "):
            _flush_hidden()
            in_head, shown = True, 0
            print(line)
            continue
        if in_head:
            if line.startswith("@@"):
                in_head = False
                print(line)
                continue
            if line.startswith("Binary files "):
                print(line)
                continue
            if line.startswith(_DIFF_HEAD_NOISE):
                continue
            print(line)
            continue
        if line.startswith("@@"):
            print(line)
            continue
        if line.startswith(("-", "+")):
            if max_lines and shown >= max_lines:
                hidden += 1
                continue
            shown += 1
            print(_truncate(line, width))
            continue
        if line.startswith("\\ No newline"):
            continue
        print(line)
    _flush_hidden()
    if untracked:
        print("== 未跟踪文件（git status，未读内容）==")
        for l in untracked:
            print(l)
    print("通过：改动摘要输出完成")


def main(argv=None):
    parser = argparse.ArgumentParser(description="dhxy2 发版辅助脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("check", do_check, "发版前核对（校验+改动清单）"),
                            ("bump", do_bump, "原子更新两处版本号"),
                            ("precommit", do_precommit,
                             "仅提交推送的提交前核对（改动清单）"),
                            ("tagcheck", do_tagcheck,
                             "仅打 tag 前核对（不 bump、不提交）"),
                            ("test", do_test, "全量测试 pytest -q"),
                            ("diffsum", do_diffsum,
                             "压缩改动摘要（撰写提交信息用）")):
        p = sub.add_parser(name, help=help_)
        if name in ("check", "bump", "tagcheck"):
            p.add_argument("version", help="目标版本号（vX.Y.Z 或 X.Y.Z）")
        elif name == "diffsum":
            p.add_argument("--width", type=int, default=200,
                           help="每条改动行截断字符数（0 = 不截断，默认 200）")
            p.add_argument("--max-lines", type=int, default=0,
                           help="每文件最多显示的改动行数（0 = 不限，默认 0）")
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
