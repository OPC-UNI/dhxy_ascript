---
name: release-tag
description: 提交+推送（可选发版打 tag）。带 vX.Y.Z 时走发版流程（check/bump/test → 提交、推送、打 tag）；只说「提交并push/提交推送」时走仅提交推送流程（precommit/test → 提交、推送，不 bump、不打 tag）。scripts/release.py 封装 check/bump/precommit/test 机械步骤。
---

# 提交推送 / 发版打 Tag

啄木鸟 dhxy2 项目的标准提交流程，**先判定模式**：

- **发版模式**：用户请求形如「提交并push tag v2.3.0」「打tag发版」或带 `vX.Y.Z` → bump 版本号 + 提交 + 推送 + 打 tag（GitHub Actions 收到 tag 自动构建发布）。
- **仅提交推送模式**：用户请求形如「提交并push」「提交推送」且**不带版本号/tag 字样** → 不 bump、不打 tag，只提交当前改动并推送。

不确定属于哪种模式时先询问；用户明确说「不打 tag」时一律按仅提交推送模式。

辅助脚本：`.claude/skills/release-tag/scripts/release.py`（在仓库根运行，Windows Git Bash 用 `python` 调用）。它封装确定性、易错的机械步骤；退出码非 0 即失败，必须停下处理再继续。

## 发版模式步骤

1. **解析版本号**：从用户请求中提取 `vX.Y.Z`（小写 v 前缀 + 三段数字）；用户没给版本号时先询问。
2. **发版前核对**：`python .claude/skills/release-tag/scripts/release.py check X.Y.Z`。脚本校验：版本格式 → tag 序列递增（> 已有最大 tag）→ tag 未占用 → src/__init__.py 与 pyproject.toml 两处版本同步 → 目标版本不低于当前；随后列出 `git add -A` 将提交的全部改动。**未跟踪文件逐个确认**：命中疑似运行时数据名单（task_stats.json、debug_overlay_state.json、config.json 等）时，先决定是否进 .gitignore 再提交（v2.9.2 教训：task_stats.json 是跨会话用户数据，入库前被拦下）。
3. **bump 版本号**：`python .claude/skills/release-tag/scripts/release.py bump X.Y.Z`——字节级正则原子更新两处版本号（不动换行符与其余内容；第二处写入失败自动回滚第一处）。禁止手改版本号。
4. **跑全量测试**：`python .claude/skills/release-tag/scripts/release.py test`（= `python -m pytest -q`）——必须全绿才发版，失败停下修复。
5. **写提交信息**（仓库惯例，中文）：
   - 首行 `feat:` / `fix:` + 一句功能摘要
   - 正文分条列出关键变更（按 git diff 实际内容梳理，用户视角的实测结论/迁移项）
   - 结尾固定附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`
6. **提交并推送**：`git add -A && git commit -m "…"` → `git push`
7. **打 tag**：`git tag vX.Y.Z && git push origin vX.Y.Z`（GitHub Actions 收到 tag 自动构建发布）
8. **汇报**：提交 hash、tag 名、推送结果、测试通过数。

## 仅提交推送模式步骤

1. **提交前核对**：`python .claude/skills/release-tag/scripts/release.py precommit`——列出 `git add -A` 将提交的全部改动；**未跟踪文件逐个确认**，命中疑似运行时数据名单时先决定是否进 .gitignore（同发版模式第 2 步）。
2. **跑全量测试**：`python .claude/skills/release-tag/scripts/release.py test`——必须全绿才提交，失败停下修复。
3. **写提交信息**（仓库惯例，中文）：
   - 首行 `feat:` / `fix:` + 一句功能摘要
   - 正文分条列出关键变更（按 git diff 实际内容梳理）
   - 结尾固定附 `Co-Authored-By: Claude Code <noreply@anthropic.com>`
4. **提交并推送**：`git add -A && git commit -m "…"` → `git push`
5. **汇报**：提交 hash、推送结果、测试通过数；明确说明本次未 bump 版本、未打 tag（后续要发版再走发版模式）。

## 要点

- tag 名与用户请求严格一致（不要改大小写或补零）；脚本的版本参数可带可不带 v 前缀，仅校验数字。
- **仅提交推送模式不修改版本号、不打 tag**；用户需要发版时会单独给出版本号/tag 请求。
- 版本号只存在于 `src/__init__.py` 与 `pyproject.toml` 两处，README 等其他文件不含版本号；两处不一致时脚本直接拒绝 bump（先手动对齐）。
- 提交信息用 heredoc 传多行中文（`git commit -m "$(cat <<'EOF' … EOF)"`），避免引号转义问题。
- 本机 git 会提示 LF→CRLF 转换 warning，属正常现象，忽略。
- 历史发版示例：v2.2.0（静默操作开关）、v2.3.0（键盘/文本静默通道）。
