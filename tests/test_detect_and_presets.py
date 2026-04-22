"""Core detect, preset, and install tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from aiolos.detect import detect_presets
from aiolos.installer import install_to_project
from aiolos.library import list_presets, list_skills, load_preset


def test_list_skills_is_namespaced(library: Path) -> None:
    skills = list_skills(library)
    assert "git/commit" in skills
    assert "frontend/nextjs" in skills
    assert "python/pytest" in skills


def test_preset_inheritance(library: Path) -> None:
    resolved = load_preset("nextjs", library)
    assert resolved["skills"] == ["git/commit", "git/pr", "frontend/nextjs", "frontend/tailwind"]
    assert resolved["agents"] == ["code-reviewer", "frontend-developer"]
    assert resolved["chain"] == ["base", "nextjs"]


def test_legacy_txt_preset_still_parses(library: Path) -> None:
    resolved = load_preset("legacy", library)
    assert resolved["skills"] == ["git/commit"]
    assert resolved["agents"] == ["code-reviewer"]
    assert resolved["chain"] == ["legacy"]


def test_preset_extends_cycle_raises(tmp_path: Path, library: Path) -> None:
    (library / "presets" / "a.toml").write_text('extends = ["b"]\nskills = []\n')
    (library / "presets" / "b.toml").write_text('extends = ["a"]\nskills = []\n')
    with pytest.raises(ValueError, match="cycle"):
        load_preset("a", library)


def test_detect_matches_package_json(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"dependencies": {"next": "14"}}')
    matches = detect_presets(project, library)
    names = [m["preset"] for m in matches]
    assert "nextjs" in names


def test_detect_matches_pyproject(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = ["fastapi>=0.100"]\n'
    )
    matches = detect_presets(project, library)
    names = [m["preset"] for m in matches]
    assert "python" in names


def test_detect_no_match(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    assert detect_presets(project, library) == []


def test_install_copies_in_git_repo(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    summary = install_to_project(
        project_path=project,
        presets=["nextjs"],
        library=library,
    )
    assert summary["mode"] == "copy"
    assert summary["git_repo"] is True
    commit = project / ".claude/skills/commit"
    assert commit.exists() and not commit.is_symlink()


def test_install_handles_git_worktree_file(tmp_path: Path, library: Path) -> None:
    """`.git` as a file (worktrees, submodules) must still be detected."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").write_text("gitdir: /somewhere/else\n")
    summary = install_to_project(
        project_path=project,
        presets=["base"],
        library=library,
    )
    assert summary["git_repo"] is True
    assert summary["mode"] == "copy"


def test_install_symlinks_outside_git(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    summary = install_to_project(
        project_path=project,
        presets=["nextjs"],
        library=library,
    )
    assert summary["mode"] == "symlink"
    assert (project / ".claude/skills/commit").is_symlink()


def test_force_symlink_in_git_writes_gitignore(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    install_to_project(
        project_path=project,
        presets=["nextjs"],
        use_symlinks=True,
        library=library,
    )
    gi = (project / ".claude/.gitignore").read_text()
    assert "skills/commit" in gi
    assert "agents/code-reviewer" in gi
    assert "managed-by: aiolos" in gi


def test_gitignore_block_is_updated_on_rerun(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".git").mkdir()
    install_to_project(
        project_path=project,
        presets=["base"],
        use_symlinks=True,
        library=library,
    )
    install_to_project(
        project_path=project,
        presets=["nextjs"],  # adds more skills/agents
        use_symlinks=True,
        overwrite=False,
        library=library,
    )
    gi = (project / ".claude/.gitignore").read_text()
    # both base and nextjs-exclusive entries should live in a single managed block
    assert gi.count("managed-by: aiolos") == 1
    assert "skills/commit" in gi
    assert "skills/nextjs" in gi
    assert "agents/frontend-developer" in gi


def test_overwrite_reinstalls(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    install_to_project(project_path=project, presets=["base"], library=library)
    # First install — skills exist
    assert (project / ".claude/skills/commit/SKILL.md").exists()
    # Re-run without overwrite → skipped
    s2 = install_to_project(project_path=project, presets=["base"], library=library)
    assert s2["skills_installed"] == []
    assert s2["skills_skipped"] == ["git/commit", "git/pr"]
    # Re-run with overwrite → installed
    s3 = install_to_project(
        project_path=project, presets=["base"], overwrite=True, library=library
    )
    assert "git/commit" in s3["skills_installed"]


def test_missing_skill_in_preset_reported(tmp_path: Path, library: Path) -> None:
    (library / "presets" / "broken.toml").write_text(
        'skills = ["does/not-exist"]\nagents = []\n'
    )
    project = tmp_path / "proj"
    project.mkdir()
    summary = install_to_project(
        project_path=project,
        presets=["broken"],
        library=library,
    )
    assert "does/not-exist" in summary["skills_missing"]
    assert summary["skills_installed"] == []


def test_contains_and_any_files_rules(tmp_path: Path, library: Path) -> None:
    (library / "presets" / "frappe.toml").write_text(
        'skills = []\nagents = []\n'
        "[detect]\n"
        "any_files = ['sites/*.conf']\n"
        'contains = [{ file = "apps.txt", text = "erpnext" }]\n'
    )
    project = tmp_path / "proj"
    (project / "sites").mkdir(parents=True)
    (project / "sites" / "example.conf").touch()
    (project / "apps.txt").write_text("frappe\nerpnext\n")
    matches = [m for m in detect_presets(project, library) if m["preset"] == "frappe"]
    assert matches
    assert matches[0]["score"] == 2


def test_list_presets_includes_legacy(library: Path) -> None:
    presets = set(list_presets(library))
    assert {"base", "nextjs", "python", "legacy"}.issubset(presets)


def test_monorepo_additive_install(tmp_path: Path, library: Path) -> None:
    """A Python + Next.js monorepo should install both presets, deduped."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"dependencies":{"next":"14"}}')
    (project / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies = ["fastapi>=0.100"]\n'
    )
    matches = detect_presets(project, library)
    names = [m["preset"] for m in matches]
    assert "nextjs" in names
    assert "python" in names

    from aiolos.detect import pick_presets
    selected = pick_presets(matches, additive=True)
    assert set(selected) == {"nextjs", "python"}

    summary = install_to_project(
        project_path=project, presets=selected, library=library
    )
    # Base inheritance means commit/pr appear once, not twice
    assert summary["skills_installed"].count("git/commit") == 1
    # Both stacks' specific skills present
    assert "frontend/nextjs" in summary["skills_installed"]
    assert "python/pytest" in summary["skills_installed"]
