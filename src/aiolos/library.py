"""Manage the local skill/agent library."""
from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Optional

from .config import (
    AGENTS_DIR,
    PRESETS_DIR,
    PRESET_EXTS,
    SKILLS_CLI,
    SKILLS_CLI_PACKAGE,
    SKILLS_DIR,
    ensure_library,
    get_library,
)


def list_skills(library: Optional[Path] = None) -> list[str]:
    """All skill names in the library, including namespaced ones (e.g. git/commit)."""
    lib = library or get_library()
    skills_path = lib / SKILLS_DIR
    if not skills_path.exists():
        return []
    names: list[str] = []
    for skill_md in skills_path.rglob("SKILL.md"):
        rel = skill_md.parent.relative_to(skills_path)
        names.append(str(rel))
    return sorted(names)


def list_agents(library: Optional[Path] = None) -> list[str]:
    lib = library or get_library()
    agents_path = lib / AGENTS_DIR
    if not agents_path.exists():
        return []
    return sorted(
        d.name
        for d in agents_path.iterdir()
        if d.is_dir() and any(d.glob("*.md"))
    )


def list_presets(library: Optional[Path] = None) -> list[str]:
    lib = library or get_library()
    presets_path = lib / PRESETS_DIR
    if not presets_path.exists():
        return []
    seen: set[str] = set()
    out: list[str] = []
    for ext in PRESET_EXTS:
        for f in presets_path.glob(f"*{ext}"):
            if f.stem not in seen:
                seen.add(f.stem)
                out.append(f.stem)
    return sorted(out)


def _find_preset_file(preset_name: str, library: Path) -> Path:
    for ext in PRESET_EXTS:
        candidate = library / PRESETS_DIR / f"{preset_name}{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"Preset '{preset_name}' not found in {library / PRESETS_DIR} "
        f"(looked for {', '.join(preset_name + e for e in PRESET_EXTS)})"
    )


def _parse_txt_preset(path: Path) -> dict:
    """Legacy .txt preset parser."""
    result: dict = {"skills": [], "agents": [], "fetch": [], "extends": [], "detect": {}}
    section = "skills"
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low == "[skills]":
            section = "skills"
        elif low == "[agents]":
            section = "agents"
        elif low == "[fetch]":
            section = "fetch"
        elif low == "[extends]":
            section = "extends"
        else:
            result[section].append(line)
    return result


def _parse_toml_preset(path: Path) -> dict:
    data = tomllib.loads(path.read_text())
    return {
        "skills": list(data.get("skills", [])),
        "agents": list(data.get("agents", [])),
        "fetch": list(data.get("fetch", [])),
        "mcp": list(data.get("mcp", [])),
        "mcp_custom": list(data.get("mcp_custom", [])),
        "extends": list(data.get("extends", [])),
        "detect": dict(data.get("detect", {})),
    }


def load_preset(
    preset_name: str,
    library: Optional[Path] = None,
    _seen: Optional[set[str]] = None,
) -> dict:
    """
    Load a preset with inheritance resolved. Returns a dict:
        { skills: [...], agents: [...], detect: {...}, chain: [names] }
    `chain` records the extends path for debugging / display.
    """
    lib = library or get_library()
    seen = _seen if _seen is not None else set()
    if preset_name in seen:
        raise ValueError(f"Preset cycle detected: {preset_name}")
    seen.add(preset_name)

    path = _find_preset_file(preset_name, lib)
    parsed = _parse_toml_preset(path) if path.suffix == ".toml" else _parse_txt_preset(path)

    skills: list[str] = []
    agents: list[str] = []
    fetch: list[str] = []
    mcp: list[str] = []
    mcp_custom: list[dict] = []
    chain: list[str] = []

    for parent in parsed.get("extends", []):
        parent_resolved = load_preset(parent, lib, seen)
        skills.extend(parent_resolved["skills"])
        agents.extend(parent_resolved["agents"])
        fetch.extend(parent_resolved["fetch"])
        mcp.extend(parent_resolved["mcp"])
        mcp_custom.extend(parent_resolved["mcp_custom"])
        chain.extend(parent_resolved["chain"])

    skills.extend(parsed.get("skills", []))
    agents.extend(parsed.get("agents", []))
    fetch.extend(parsed.get("fetch", []))
    mcp.extend(parsed.get("mcp", []))
    mcp_custom.extend(parsed.get("mcp_custom", []))
    chain.append(preset_name)

    # Dedupe custom servers by slug, child wins.
    seen_slugs: set[str] = set()
    deduped_custom: list[dict] = []
    for entry in reversed(mcp_custom):
        slug = entry.get("slug")
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        deduped_custom.append(entry)
    deduped_custom.reverse()

    return {
        "skills": list(dict.fromkeys(skills)),
        "agents": list(dict.fromkeys(agents)),
        "fetch": list(dict.fromkeys(fetch)),
        "mcp": list(dict.fromkeys(mcp)),
        "mcp_custom": deduped_custom,
        "detect": parsed.get("detect", {}),
        "chain": chain,
    }


def get_preset_items(preset_name: str, library: Optional[Path] = None) -> dict:
    """Back-compat shim used by the installer."""
    resolved = load_preset(preset_name, library)
    return {
        "skills": resolved["skills"],
        "agents": resolved["agents"],
        "fetch": resolved["fetch"],
    }


def fetch_from_skills_sh(
    source: str,
    skill_names: list[str],
    library: Optional[Path] = None,
    verbose: bool = False,
) -> list[str]:
    lib = library or get_library()
    ensure_library(lib)
    dest = lib / SKILLS_DIR

    if shutil.which(SKILLS_CLI) is None:
        raise RuntimeError(
            f"{SKILLS_CLI!r} is not on PATH. Install Node.js to use `aiolos fetch`."
        )

    if not skill_names:
        result = subprocess.run(
            [SKILLS_CLI, "-y", SKILLS_CLI_PACKAGE, "add", source, "--list"],
            capture_output=not verbose,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list skills from {source}:\n{result.stderr}")
        if not verbose and result.stdout:
            print(result.stdout)
        return []

    installed: list[str] = []
    for skill in skill_names:
        cmd = [
            SKILLS_CLI, "-y", SKILLS_CLI_PACKAGE, "add", source,
            "--skill", skill,
            "--dir", str(dest),
            "-y",
        ]
        if verbose:
            print(f"  Fetching '{skill}' from {source} → {dest / skill}")
        result = subprocess.run(cmd, capture_output=not verbose, text=True)
        if result.returncode != 0:
            msg = (result.stderr or "").strip() or "(no stderr)"
            print(f"  [!] Failed to fetch '{skill}': {msg}")
        else:
            installed.append(skill)
            if verbose:
                print(f"  ✓ {skill}")

    return installed
