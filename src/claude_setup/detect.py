"""
Scan a project for stack signals and match them against preset detect rules.

A preset's `[detect]` block (TOML) can declare:

    [detect]
    files        = ["package.json", "sites/apps.txt"]        # any-of
    any_files    = ["**/*.tf"]                               # glob, any-of
    all_files    = ["Dockerfile", "docker-compose.yml"]      # glob, all-of
    package_json_has = ["next", "react"]                     # dependency names (any-of)
    pyproject_has    = ["django", "fastapi"]                 # project.dependencies (any-of)
    contains         = [{ file = "apps.txt", text = "erpnext" }]

Matching is best-effort and conservative: missing files count as non-matches,
never as errors.
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Optional

from .config import get_library
from .library import list_presets, load_preset


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return None


def _glob_any(project: Path, patterns: list[str]) -> list[str]:
    hits: list[str] = []
    for pattern in patterns:
        for match in project.glob(pattern):
            if match.is_file():
                hits.append(str(match.relative_to(project)))
                break  # one hit per pattern is enough
    return hits


def _package_json_deps(project: Path) -> set[str]:
    pj = project / "package.json"
    text = _read_text(pj)
    if not text:
        return set()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps.update((data.get(key) or {}).keys())
    return deps


def _pyproject_deps(project: Path) -> set[str]:
    text = _read_text(project / "pyproject.toml")
    if not text:
        return set()
    try:
        data = tomllib.loads(text)
    except Exception:
        return set()
    deps: set[str] = set()
    # PEP 621
    for spec in (data.get("project", {}) or {}).get("dependencies", []) or []:
        deps.add(_strip_dep(spec))
    opt = (data.get("project", {}) or {}).get("optional-dependencies", {}) or {}
    for group in opt.values():
        for spec in group:
            deps.add(_strip_dep(spec))
    # Poetry
    poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
    deps.update(k for k in (poetry.get("dependencies") or {}).keys() if k != "python")
    deps.update((poetry.get("dev-dependencies") or {}).keys())
    return deps


def _strip_dep(spec: str) -> str:
    """'fastapi>=0.100,<1' → 'fastapi'"""
    for sep in ("[", "=", ">", "<", "!", "~", ";", " "):
        idx = spec.find(sep)
        if idx != -1:
            spec = spec[:idx]
    return spec.strip().lower()


def score_rules(project: Path, rules: dict) -> tuple[int, list[str]]:
    """
    Returns (score, reasons). Score is a simple count of matched rule clauses.
    A preset with score >= 1 is considered a match.
    """
    if not rules:
        return 0, []
    reasons: list[str] = []
    score = 0

    files = rules.get("files") or []
    for f in files:
        if (project / f).exists():
            reasons.append(f"file {f!r} present")
            score += 1
            break

    any_files = rules.get("any_files") or []
    hits = _glob_any(project, any_files)
    if hits:
        reasons.append(f"glob match: {hits[0]}")
        score += 1

    all_files = rules.get("all_files") or []
    if all_files:
        all_hit = all(_glob_any(project, [p]) for p in all_files)
        if all_hit:
            reasons.append(f"all of: {', '.join(all_files)}")
            score += 1

    want = [d.lower() for d in (rules.get("package_json_has") or [])]
    if want:
        deps = {d.lower() for d in _package_json_deps(project)}
        overlap = sorted(set(want) & deps)
        if overlap:
            reasons.append(f"package.json has {overlap[0]}")
            score += 1

    want_py = [d.lower() for d in (rules.get("pyproject_has") or [])]
    if want_py:
        deps = {d.lower() for d in _pyproject_deps(project)}
        overlap = sorted(set(want_py) & deps)
        if overlap:
            reasons.append(f"pyproject has {overlap[0]}")
            score += 1

    for check in rules.get("contains") or []:
        f = check.get("file")
        needle = check.get("text")
        if not f or not needle:
            continue
        text = _read_text(project / f)
        if text and needle in text:
            reasons.append(f"{f!r} contains {needle!r}")
            score += 1

    return score, reasons


def detect_presets(
    project: Path,
    library: Optional[Path] = None,
) -> list[dict]:
    """Return matching presets sorted by score desc."""
    lib = library or get_library()
    matches: list[dict] = []
    for name in list_presets(lib):
        try:
            preset = load_preset(name, lib)
        except Exception as e:
            matches.append({"preset": name, "score": 0, "reasons": [f"load error: {e}"], "error": True})
            continue
        score, reasons = score_rules(project, preset.get("detect") or {})
        if score > 0:
            matches.append({
                "preset": name,
                "score": score,
                "reasons": reasons,
                "skills": preset["skills"],
                "agents": preset["agents"],
                "chain": preset["chain"],
            })
    matches.sort(key=lambda m: m["score"], reverse=True)
    return matches


def pick_presets(matches: list[dict], additive: bool = True) -> list[str]:
    """Choose which preset(s) to install from a list of matches.

    Monorepos commonly trigger multiple presets — e.g. a Python backend and a
    Next.js frontend in the same repo. When `additive` is true (default) we
    install every preset that matched (score > 0) because each one's
    [detect] block is supposed to be conservative enough that a match means
    "this stack lives here." Duplicates are resolved by the installer.

    When `additive` is false, pick the single top-scorer.
    """
    if not matches:
        return []
    real = [m for m in matches if not m.get("error")]
    if not real:
        return []
    if not additive:
        return [real[0]["preset"]]
    return [m["preset"] for m in real if m["score"] > 0]
