"""Shared fixtures for aiolos tests."""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_skill(lib: Path, namespaced: str) -> None:
    target = lib / "skills" / namespaced
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        "<!-- prettier-ignore -->\n"
        "---\n"
        f"name: {namespaced.rsplit('/', 1)[-1]}\n"
        "description: Use when test fixtures need a skill.\n"
        "allowed-tools: Read\n"
        "---\nbody\n"
    )


def _write_agent(lib: Path, name: str) -> None:
    target = lib / "agents" / name
    target.mkdir(parents=True)
    (target / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: Use when agent is needed.\nmodel: sonnet\n---\n"
    )


@pytest.fixture()
def library(tmp_path: Path) -> Path:
    """A fully-populated library in a path *with spaces* — our real repo name
    ends in a space too, so every code path must handle that."""
    lib = tmp_path / "my library"
    for skill in (
        "git/commit",
        "git/pr",
        "frontend/nextjs",
        "frontend/tailwind",
        "docker/compose",
        "python/pytest",
    ):
        _write_skill(lib, skill)

    for agent in ("code-reviewer", "frontend-developer", "python-pro"):
        _write_agent(lib, agent)

    presets = lib / "presets"
    presets.mkdir(parents=True)

    (presets / "base.toml").write_text(
        'skills = ["git/commit", "git/pr"]\nagents = ["code-reviewer"]\n'
    )
    (presets / "nextjs.toml").write_text(
        'extends = ["base"]\n'
        'skills = ["frontend/nextjs", "frontend/tailwind"]\n'
        'agents = ["frontend-developer"]\n'
        "[detect]\n"
        'files = ["next.config.js"]\n'
        'package_json_has = ["next"]\n'
    )
    (presets / "python.toml").write_text(
        'extends = ["base"]\n'
        'skills = ["python/pytest"]\n'
        'agents = ["python-pro"]\n'
        "[detect]\n"
        'pyproject_has = ["fastapi"]\n'
    )

    # a legacy .txt preset for backwards-compat tests
    (presets / "legacy.txt").write_text(
        "[skills]\ngit/commit\n\n[agents]\ncode-reviewer\n"
    )

    return lib
