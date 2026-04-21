"""Diagnose the library: missing skill/agent references, broken presets, etc."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AGENTS_DIR, SKILLS_DIR, get_library
from .library import list_presets, load_preset


@dataclass
class Finding:
    severity: str  # "error" | "warn" | "info"
    message: str

    def __str__(self) -> str:
        icon = {"error": "✗", "warn": "!", "info": "·"}[self.severity]
        return f"  {icon} [{self.severity}] {self.message}"


def _skill_exists(library: Path, name: str) -> bool:
    return (library / SKILLS_DIR / name / "SKILL.md").exists()


def _agent_exists(library: Path, name: str) -> bool:
    agent_dir = library / AGENTS_DIR / name
    return agent_dir.is_dir() and any(agent_dir.glob("*.md"))


def run_doctor(library: Path | None = None) -> list[Finding]:
    lib = library or get_library()
    findings: list[Finding] = []

    if not lib.exists():
        findings.append(Finding("error", f"library {lib} does not exist. Run bootstrap.sh."))
        return findings

    for sub in (SKILLS_DIR, AGENTS_DIR, "presets"):
        p = lib / sub
        if not p.exists():
            findings.append(Finding("error", f"missing {sub}/ in {lib}"))

    presets = list_presets(lib)
    for name in presets:
        try:
            resolved = load_preset(name, lib)
        except Exception as e:
            findings.append(Finding("error", f"preset {name!r} failed to load: {e}"))
            continue

        for skill in resolved["skills"]:
            if not _skill_exists(lib, skill):
                findings.append(
                    Finding(
                        "error",
                        f"preset {name!r} references missing skill {skill!r} "
                        f"(expected at {lib / SKILLS_DIR / skill / 'SKILL.md'})",
                    )
                )
        for agent in resolved["agents"]:
            if not _agent_exists(lib, agent):
                findings.append(
                    Finding(
                        "error",
                        f"preset {name!r} references missing agent {agent!r} "
                        f"(expected at {lib / AGENTS_DIR / agent}/{agent}.md)",
                    )
                )

    if not presets:
        findings.append(Finding("warn", "no presets found in library"))

    return findings
