---
name: release-tag
description: 发版提交+推送+打 tag（自动 bump 版本号到 src/__init__.py 与 pyproject.toml）。当用户说「提交并push tag vX.Y.Z」「打tag发版」或要求发版打 tag 时使用。
---

# 发版打 Tag

啄木鸟 dhxy2 项目的标准发版流程。用户请求形如「提交并push tag v2.3.0」。

## 步骤

1. **解析版本号**：从用户请求中提取 `vX.Y.Z`（小写 v 前缀 + 三段数字）；用户没给版本号时先询问。
2. **核对现状**：`git status --short` 看改动文件、`git tag | tail -3` 确认版本序列递增（新 tag 必须大于已有最大 tag）。
3. **bump 版本号**（唯一来源约定见 src/__init__.py docstring，两个文件同步改）：
   - `src/__init__.py`：`__version__ = "X.Y.Z"`
   - `pyproject.toml`：`version = "X.Y.Z"`
4. **跑全量测试**：`python -m pytest -q`——必须全绿才发版，失败停下修复。
5. **写提交信息**（仓库惯例，中文）：
   - 首行 `feat:` / `fix:` + 一句功能摘要
   - 正文分条列出关键变更（按 git diff 实际内容梳理，用户视角的实测结论/迁移项）
   - 结尾固定附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`
6. **提交并推送**：`git add -A && git commit -m "…"` → `git push`
7. **打 tag**：`git tag vX.Y.Z && git push origin vX.Y.Z`（GitHub Actions 收到 tag 自动构建发布）
8. **汇报**：提交 hash、tag 名、推送结果、测试通过数。

## 要点

- tag 名与用户请求严格一致（不要改大小写或补零）。
- 版本号只存在于 `src/__init__.py` 与 `pyproject.toml` 两处，README 等其他文件不含版本号。
- 提交信息用 heredoc 传多行中文（`git commit -m "$(cat <<'EOF' … EOF)"`），避免引号转义问题。
- 本机 git 会提示 LF→CRLF 转换 warning，属正常现象，忽略。
- 历史发版示例：v2.2.0（静默操作开关）、v2.3.0（键盘/文本静默通道）。
