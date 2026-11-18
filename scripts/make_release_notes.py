#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成按类型分类的更新日志（Release 正文与面板更新说明共用）。

从「上一 tag..当前 tag」的提交按 Conventional Commits 前缀分类：

- `feat:`  → 🆕 新增功能
- `fix:`   → 🐛 问题修复
- 其余（refactor / perf / docs / chore / test / ci / build 及无前缀提交）
  → 🔧 变更与优化

面向用户的日志会**丢弃纯内部提交**（docs / test / ci / build / style /
chore / release，含 `chore: 发布 vX.Y.Z` 版本号 bump）。分类后剥掉
`type(scope):` 前缀，只留面向用户的描述。若存在 `changelog/<tag>.md`，
以人工更新日志为准（不再分类）。

用法（仓库根运行）：

    python scripts/make_release_notes.py --tag v2.13.4 \
      --range v2.13.3..HEAD \
      --compare-url https://github.com/OWNER/REPO/compare/v2.13.3...v2.13.4

输出两份文件：
- update_notes.md  → update.json 的 notes（面板更新提示，纯净无链接）
- release_notes.md → GitHub Release 正文（末尾附完整对比链接）
"""
import argparse
import pathlib
import re
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 分类与展示顺序（用户要求：新增功能 → 变更功能 → 修复 bug）
CATEGORIES = (
    ("feat", "🆕 新增功能"),
    ("other", "🔧 变更与优化"),
    ("fix", "🐛 问题修复"),
)

_CONVENTIONAL = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^)]*)\))?!?[：:]\s*(?P<desc>.+)$")
# 纯内部提交：对用户不可见，不进更新日志（chore 已含版本号 bump 提交）
_SKIP_TYPES = {"docs", "doc", "test", "tests", "ci", "build", "style",
               "chore", "release"}


def categorize_subjects(subjects):
    """把提交标题分类为 {feat/fix/other: [描述, ...]}（保持入参顺序）。

    - 带 `feat:` / `fix:` 前缀的进对应分类，其余进 other；
    - 剥离 `type(scope):` 前缀，仅保留描述；
    - 纯内部提交（docs / test / ci / build / style / chore / release）
      丢弃；同一分类内重复描述去重。
    """
    groups = {key: [] for key, _ in CATEGORIES}
    for raw in subjects:
        subject = (raw or "").strip()
        if not subject:
            continue
        match = _CONVENTIONAL.match(subject)
        if match:
            ptype = match.group("type").lower()
            desc = match.group("desc").strip()
            if ptype in _SKIP_TYPES:
                continue
            key = ptype if ptype in ("feat", "fix") else "other"
        else:
            desc = subject
            key = "other"
        if desc and desc not in groups[key]:
            groups[key].append(desc)
    return groups


def build_notes(groups):
    """分类结果 → Markdown 更新日志（空分类整节省略）。"""
    blocks = []
    for key, title in CATEGORIES:
        items = groups.get(key) or []
        if not items:
            continue
        blocks.append("### " + title + "\n"
                      + "\n".join("- " + item for item in items))
    return "\n\n".join(blocks)


def _git_subjects(rev_range):
    """读取 git log 提交标题列表；失败抛 CalledProcessError。"""
    if not rev_range:
        return []
    proc = subprocess.run(
        ["git", "log", "--pretty=format:%s", rev_range],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=True)
    return [line for line in proc.stdout.split("\n") if line.strip()]


def render_notes(tag, subjects):
    """提交标题列表 → 分类 Markdown（供测试与调用方复用）。"""
    notes = build_notes(categorize_subjects(subjects))
    if not notes:
        return "（本次发版无新的提交记录）"
    return notes


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="按类型分类生成更新日志（update_notes.md / release_notes.md）")
    parser.add_argument("--tag", required=True,
                        help="当前 tag（如 v2.13.4）")
    parser.add_argument("--range", default="",
                        help="git 提交范围（如 v2.13.3..HEAD）；留空表示无提交")
    parser.add_argument("--compare-url", default="",
                        help="Release 正文末尾的完整对比链接；留空则不附")
    parser.add_argument("--curated", default="",
                        help="人工更新日志文件（存在则以它为准，不再分类）")
    parser.add_argument("--update-out", default="update_notes.md",
                        help="面板更新说明输出路径")
    parser.add_argument("--release-out", default="release_notes.md",
                        help="Release 正文输出路径")
    args = parser.parse_args(argv)

    curated_path = pathlib.Path(args.curated) if args.curated else None
    if curated_path is not None and curated_path.is_file():
        notes = curated_path.read_text(encoding="utf-8").strip()
        print(f"使用人工更新日志: {curated_path}")
    else:
        if curated_path is not None:
            print(f"::warning title=更新日志不存在::{curated_path} 不存在，"
                  "回退按提交分类生成")
        subjects = _git_subjects(args.range)
        notes = render_notes(args.tag, subjects)
        print(f"按提交分类生成更新日志：范围 {args.range or '(空)'}，"
              f"提交 {len(subjects)} 条")

    pathlib.Path(args.update_out).write_text(notes + "\n", encoding="utf-8")
    release = notes
    if args.compare_url:
        release += "\n\n完整对比: " + args.compare_url
    pathlib.Path(args.release_out).write_text(release + "\n", encoding="utf-8")

    print("--- 生成的更新日志 ---")
    print(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
