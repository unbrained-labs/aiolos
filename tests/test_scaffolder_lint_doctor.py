"""Tests for the authoring toolkit: new-skill, lint, doctor."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from claude_setup.doctor import run_doctor
from claude_setup.lint import lint_skill
from claude_setup.scaffolder import scaffold_agent, scaffold_skill, validate_description


# ── scaffolder ────────────────────────────────────────────────────────────────

def test_scaffold_skill_writes_canonical_layout(library: Path) -> None:
    path = scaffold_skill(
        "testing/smoke",
        description="Use when running smoke tests.",
        allowed_tools="Read",
        library=library,
    )
    assert path.name == "SKILL.md"
    assert path.parent.name == "smoke"
    text = path.read_text()
    assert "<!-- prettier-ignore -->" in text
    assert "name: smoke" in text
    assert (path.parent / "references").is_dir()
    assert (path.parent / "scripts").is_dir()
    # .prettierignore was written so prettier doesn't wrap descriptions
    assert (library / "skills" / ".prettierignore").exists()


def test_scaffold_skill_rejects_invalid_name(library: Path) -> None:
    with pytest.raises(ValueError):
        scaffold_skill("Bad Name!", description="Use when whatever.", library=library)


def test_scaffold_skill_rejects_passive_description(library: Path) -> None:
    with pytest.raises(ValueError, match="description"):
        scaffold_skill("a/b", description="A skill that does stuff.", library=library)


def test_scaffold_skill_rejects_multiline_description(library: Path) -> None:
    with pytest.raises(ValueError, match="single line"):
        scaffold_skill("a/b", description="Use when.\nextra line", library=library)


def test_scaffold_agent(library: Path) -> None:
    path = scaffold_agent(
        name="helpful",
        description="Use PROACTIVELY when help is needed.",
        role="helper",
        library=library,
    )
    assert path.read_text().startswith("---\nname: helpful\n")


def test_validate_description_missing_trigger() -> None:
    problems = validate_description("Handles stuff about things.")
    assert any("trigger" in p.lower() for p in problems)


# ── lint ──────────────────────────────────────────────────────────────────────

def test_lint_clean_skill(library: Path) -> None:
    issues = lint_skill(library / "skills" / "git" / "commit" / "SKILL.md")
    errors = [i for i in issues if i.severity == "error"]
    assert errors == []


def test_lint_flags_lowercase_filename(tmp_path: Path) -> None:
    bad = tmp_path / "skill.md"
    bad.write_text("---\nname: x\ndescription: Use when.\n---\n")
    issues = lint_skill(bad)
    assert any(i.severity == "error" for i in issues)


def test_lint_flags_bash_star(tmp_path: Path) -> None:
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "<!-- prettier-ignore -->\n"
        "---\nname: demo\ndescription: Use when demoing.\n"
        "allowed-tools: Bash(*)\n---\nbody"
    )
    messages = [i.message for i in lint_skill(skill)]
    assert any("Bash(*)" in m for m in messages)


def test_lint_flags_disable_model_invocation_as_info(tmp_path: Path) -> None:
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text(
        "<!-- prettier-ignore -->\n"
        "---\nname: demo\ndescription: Use when.\n"
        "disable-model-invocation: true\n---\nbody"
    )
    issues = lint_skill(skill)
    dmi_issues = [i for i in issues if "disable-model-invocation" in i.message]
    assert dmi_issues
    assert all(i.severity == "info" for i in dmi_issues)


def test_lint_info_when_prettier_ignore_missing(tmp_path: Path) -> None:
    skill = tmp_path / "demo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: demo\ndescription: Use when.\n---\nbody")
    assert any("prettier-ignore" in i.message.lower() for i in lint_skill(skill))


# ── doctor ────────────────────────────────────────────────────────────────────

def test_doctor_healthy_library(library: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_LIBRARY", str(library))
    findings = run_doctor()
    assert all(f.severity != "error" for f in findings)


def test_doctor_flags_missing_skill_reference(
    library: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (library / "presets" / "broken.toml").write_text(
        'skills = ["nope/missing"]\nagents = []\n'
    )
    monkeypatch.setenv("CLAUDE_LIBRARY", str(library))
    findings = run_doctor()
    errs = [f for f in findings if f.severity == "error"]
    assert any("nope/missing" in f.message for f in errs)


# ── CLI smoke (real subprocess — validates argparse wiring) ───────────────────

def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = kwargs.pop("env", None)
    return subprocess.run(
        cmd, capture_output=True, text=True, env=env, check=False, **kwargs
    )


def test_cli_init_one_shot(tmp_path: Path, library: Path) -> None:
    """A detected project should install the matching preset with zero prompts."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"dependencies": {"next": "14"}}')

    import os
    env = os.environ.copy()
    env["CLAUDE_LIBRARY"] = str(library)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = _run(
        [sys.executable, "-m", "claude_setup.cli", "init",
         "--project", str(project), "--json"],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    import json
    payload = json.loads(result.stdout)
    assert payload["selected"] == ["nextjs"]
    assert payload["ambiguous"] is False
    assert "frontend/nextjs" in payload["installed"]["skills_installed"]


def test_cli_init_falls_back_to_base(tmp_path: Path, library: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()

    import os, json
    env = os.environ.copy()
    env["CLAUDE_LIBRARY"] = str(library)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = _run(
        [sys.executable, "-m", "claude_setup.cli", "init",
         "--project", str(project), "--json"],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["selected"] == ["base"]
    assert payload["fallback_used"] is True
