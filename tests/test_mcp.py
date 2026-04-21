"""Tests for the MCP subcommand + catalog + custom server inlining."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiolos import mcp


def test_catalog_has_canonical_servers_only() -> None:
    slugs = {s.slug for s in mcp.CATALOG}
    # These ship in modelcontextprotocol/servers and are safe to reference.
    assert {"filesystem", "git", "github", "postgres", "sqlite"} <= slugs
    # No vendor-specific slugs baked in (we don't fabricate metadata).
    assert "sentry" not in slugs
    assert "stripe" not in slugs
    assert "pencil" not in slugs


def test_suggest_postgres_from_pj_dep(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "package.json").write_text('{"dependencies": {"pg": "^8"}}')
    suggested = [s.slug for s in mcp.suggest(project)]
    assert "postgres" in suggested


def test_suggest_postgres_from_pyproject_dep(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname="x"\ndependencies = ["psycopg[binary]>=3"]\n'
    )
    suggested = [s.slug for s in mcp.suggest(project)]
    assert "postgres" in suggested


def test_write_mcp_config_creates_placeholders(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    summary = mcp.write_mcp_config(project, ["postgres", "github"])

    data = json.loads((project / ".mcp.json").read_text())
    assert "mcpServers" in data
    assert data["mcpServers"]["postgres"]["env"]["DATABASE_URL"] == "${DATABASE_URL}"
    assert data["mcpServers"]["github"]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == (
        "${GITHUB_PERSONAL_ACCESS_TOKEN}"
    )
    assert data["mcpServers"]["postgres"]["comment"] == mcp.MANAGED_MARKER

    env_example = (project / ".env.claude.example").read_text()
    assert "DATABASE_URL=" in env_example
    assert "GITHUB_PERSONAL_ACCESS_TOKEN=" in env_example

    gi = (project / ".gitignore").read_text()
    assert ".env.claude" in gi

    assert set(summary["installed"]) == {"postgres", "github"}
    assert summary["wrote_gitignore"] is True


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    mcp.write_mcp_config(project, ["postgres"])
    mcp.write_mcp_config(project, ["postgres"])
    env = (project / ".env.claude.example").read_text()
    # no duplicated key
    assert env.count("DATABASE_URL=") == 1


def test_preserves_user_authored_entries(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "my-custom": {"command": "my-server", "args": []},
            # Override: user keeps their own postgres entry, no managed marker.
            "postgres": {"command": "my-pg", "args": [], "comment": "hand-written"},
        }
    }))
    mcp.write_mcp_config(project, ["postgres"])
    data = json.loads((project / ".mcp.json").read_text())
    # User's my-custom preserved.
    assert data["mcpServers"]["my-custom"]["command"] == "my-server"
    # User's postgres was NOT overwritten by catalog.
    assert data["mcpServers"]["postgres"]["command"] == "my-pg"


def test_removal_via_subsequent_run(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    mcp.write_mcp_config(project, ["postgres", "github"])
    mcp.write_mcp_config(project, ["postgres"])
    data = json.loads((project / ".mcp.json").read_text())
    assert "postgres" in data["mcpServers"]
    assert "github" not in data["mcpServers"]


def test_custom_server_from_preset(tmp_path: Path) -> None:
    """User-defined servers (e.g. Pencil) embed inline; we don't need to know them."""
    project = tmp_path / "proj"
    project.mkdir()
    pencil = mcp.server_from_dict({
        "slug": "pencil",
        "description": "Pencil — free Figma alternative",
        "command": "npx",
        "args": ["-y", "@pencil/mcp"],
        "env": ["PENCIL_API_KEY"],
    })
    mcp.write_mcp_config(project, [], custom_servers=[pencil])
    data = json.loads((project / ".mcp.json").read_text())
    assert data["mcpServers"]["pencil"]["command"] == "npx"
    assert data["mcpServers"]["pencil"]["env"]["PENCIL_API_KEY"] == "${PENCIL_API_KEY}"


def test_unknown_slug_errors_clearly(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(ValueError, match="mcp_custom"):
        mcp.write_mcp_config(project, ["nonexistent-vendor-thing"])


def test_server_from_dict_requires_fields() -> None:
    with pytest.raises(ValueError, match="missing required field"):
        mcp.server_from_dict({"slug": "x"})  # no command


def test_preset_mcp_loads_through_library(tmp_path: Path, library: Path) -> None:
    """Preset with [[mcp_custom]] blocks round-trips through load_preset."""
    (library / "presets" / "design.toml").write_text("""
extends = ["base"]
mcp = ["filesystem"]

[[mcp_custom]]
slug = "pencil"
description = "Pencil — Figma alternative"
command = "npx"
args = ["-y", "@pencil/mcp"]
env = ["PENCIL_API_KEY"]
""")
    from aiolos.library import load_preset
    resolved = load_preset("design", library)
    assert "filesystem" in resolved["mcp"]
    assert len(resolved["mcp_custom"]) == 1
    assert resolved["mcp_custom"][0]["slug"] == "pencil"
