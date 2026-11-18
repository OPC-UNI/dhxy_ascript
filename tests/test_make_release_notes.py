# -*- coding: utf-8 -*-
"""发布日志分类脚本测试：提交分类、空分类省略、curated 覆盖与产物落盘。"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "make_release_notes", ROOT / "scripts" / "make_release_notes.py")
assert _SPEC and _SPEC.loader
make_release_notes = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(make_release_notes)


def test_categorize_by_conventional_prefix():
    groups = make_release_notes.categorize_subjects([
        "fix: 修复对话框门控",
        "feat: 新增星魂归位任务",
        "refactor: 下沉领取链",
        "随手提交无前缀",
    ])
    assert groups["feat"] == ["新增星魂归位任务"]
    assert groups["fix"] == ["修复对话框门控"]
    assert groups["other"] == ["下沉领取链", "随手提交无前缀"]


def test_categorize_skips_internal_commits():
    groups = make_release_notes.categorize_subjects([
        "chore: 发布 v2.13.4",
        "docs: README 改用频道",
        "test: 补回归用例",
        "ci: 增加门禁",
        "build: 打包调整",
        "style: 格式化",
        "release: v2.13.4",
        "fix: 真正的问题修复",
    ])
    assert groups["feat"] == []
    assert groups["other"] == []
    assert groups["fix"] == ["真正的问题修复"]


def test_categorize_strips_scope_and_accepts_fullwidth_colon():
    groups = make_release_notes.categorize_subjects([
        "feat(mingwang): 新增第六路点",
        "fix(team)：修复归队误判",
        "perf: 轮询单帧化",
    ])
    assert groups["feat"] == ["新增第六路点"]
    assert groups["fix"] == ["修复归队误判"]
    assert groups["other"] == ["轮询单帧化"]


def test_categorize_dedupes_within_group():
    groups = make_release_notes.categorize_subjects(
        ["fix: 同一问题", "fix: 同一问题"])
    assert groups["fix"] == ["同一问题"]


def test_build_notes_order_and_empty_sections():
    notes = make_release_notes.build_notes({
        "feat": ["新功能 A"],
        "other": [],
        "fix": ["修复 B"],
    })
    assert notes.index("🆕 新增功能") < notes.index("🐛 问题修复")
    assert "变更与优化" not in notes  # 空分类整节省略
    assert "- 新功能 A" in notes and "- 修复 B" in notes


def test_render_notes_fallback_when_no_subjects():
    notes = make_release_notes.render_notes("v9.9.9", [])
    assert "无新的提交记录" in notes


def test_main_writes_update_and_release_files(tmp_path, monkeypatch):
    monkeypatch.setattr(make_release_notes, "_git_subjects",
                        lambda rev: ["feat: 新功能", "fix: 修复问题"])
    update = tmp_path / "update_notes.md"
    release = tmp_path / "release_notes.md"
    rc = make_release_notes.main([
        "--tag", "v9.9.9", "--range", "v9.9.8..HEAD",
        "--compare-url", "https://example.com/compare",
        "--update-out", str(update), "--release-out", str(release),
    ])
    assert rc == 0
    update_text = update.read_text(encoding="utf-8")
    release_text = release.read_text(encoding="utf-8")
    assert "🆕 新增功能" in update_text and "🐛 问题修复" in update_text
    assert "完整对比" not in update_text          # 面板日志无外链
    assert "完整对比: https://example.com/compare" in release_text


def test_main_uses_curated_file_without_classification(tmp_path):
    curated = tmp_path / "v9.9.9.md"
    curated.write_text("## 手工整理\n- 只写用户关心的内容\n",
                       encoding="utf-8")
    update = tmp_path / "update_notes.md"
    release = tmp_path / "release_notes.md"
    rc = make_release_notes.main([
        "--tag", "v9.9.9", "--curated", str(curated),
        "--update-out", str(update), "--release-out", str(release),
    ])
    assert rc == 0
    text = update.read_text(encoding="utf-8")
    assert "手工整理" in text
    assert "🆕 新增功能" not in text
