"""Configuration and constants for claude-setup."""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_LIBRARY = Path.home() / ".claude-library"

SKILLS_DIR = "skills"
AGENTS_DIR = "agents"
PRESETS_DIR = "presets"

PROJECT_SKILLS_PATH = ".claude/skills"
PROJECT_AGENTS_PATH = ".claude/agents"
PROJECT_CLAUDE_DIR = ".claude"

SKILLS_CLI = "npx"
SKILLS_CLI_PACKAGE = "skills"

PRESET_EXTS = (".toml", ".txt")


def get_library() -> Path:
    env = os.environ.get("CLAUDE_LIBRARY")
    return Path(env).expanduser() if env else DEFAULT_LIBRARY


def ensure_library(library: Path) -> None:
    for d in (library / SKILLS_DIR, library / AGENTS_DIR, library / PRESETS_DIR):
        d.mkdir(parents=True, exist_ok=True)
