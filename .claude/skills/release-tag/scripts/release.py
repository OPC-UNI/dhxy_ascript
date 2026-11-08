#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""release-tag 发版辅助脚本（.claude/skills/release-tag/scripts/release.py）

用法（在仓库根运行，Windows 用 python 调用）：
    python .claude/skills/release-tag/scripts/release.py check <X.Y.Z>
    python .claude/skills/release-tag/scripts/release.py bump  <X.Y.Z>
    python .claude/skills/release-tag/scripts/release.py test

封装发版流程中确定性、易错的机械步骤；判断性步骤（提交信息撰写、
git commit/push/tag）仍由执行者按 SKILL.md 手动完成：

  check  发版前核对：版本格式 → tag 序列递增且未占用 → 双文件版本同步
         → 目标版本不低于当前（等于=已 bump 完成，提示即可）→ 列出
         git add -A 将提交的全部改动；未跟踪文件命中疑似运行时数据
         名单时警告（先决定是否进 .gitignore 再提交）
  bump   原子更新 src/__init__.py 与 pyproject.toml 两处版本号：
         校验同 check 且要求目标版本严格高于当前；字节级正则替换
         （不动换行符与其余内容）；第二处写入失败时回滚第一处
  test   全量测试 python -m pytest -q，退出码透传（未全绿不得发版）

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
              f"请先手动对齐再发版")
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
    lines = [l for l in _git("status", "--porcelain").splitlines() if l.strip()]
    if not lines:
        print("通过：工作区干净，无待提交改动")
    else:
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
    print("通过：发版前核对完成（下一步 bump）")


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


def main(argv=None):
    parser = argparse.ArgumentParser(description="dhxy2 发版辅助脚本")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn, help_ in (("check", do_check, "发版前核对（校验+改动清单）"),
                            ("bump", do_bump, "原子更新两处版本号"),
                            ("test", do_test, "全量测试 pytest -q")):
        p = sub.add_parser(name, help=help_)
        if name != "test":
            p.add_argument("version", help="目标版本号（vX.Y.Z 或 X.Y.Z）")
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
