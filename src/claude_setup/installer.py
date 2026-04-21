"""Install skills and agents from the library into a project."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from .config import (
    AGENTS_DIR,
    PROJECT_AGENTS_PATH,
    PROJECT_CLAUDE_DIR,
    PROJECT_SKILLS_PATH,
    SKILLS_DIR,
    get_library,
)
from .library import get_preset_items

GITIGNORE_MARKER_START = "# managed-by: claude-setup (personal symlinks)"
GITIGNORE_MARKER_END = "# end claude-setup"


def _is_git_repo(project_path: Path) -> bool:
    """True if project_path is inside a git repo. Also handles submodules and
    worktrees (where `.git` is a file, not a directory)."""
    p = project_path
    for _ in range(10):
        git_path = p / ".git"
        if git_path.is_dir() or git_path.is_file():
            return True
        if p.parent == p:
            break
        p = p.parent
    return False


def _inside(base: Path, child: Path) -> bool:
    """True if `child` resolves to somewhere under `base`."""
    try:
        child.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_remove(dest: Path, project_root: Path) -> None:
    """Refuse to remove anything that resolves outside the project's .claude dir,
    even when dest itself is a symlink pointing outward."""
    claude_dir = (project_root / PROJECT_CLAUDE_DIR).resolve()
    # If dest is a symlink we only ever unlink the link itself, never rmtree its target.
    if dest.is_symlink():
        dest.unlink()
        return
    if not _inside(claude_dir, dest):
        raise RuntimeError(
            f"Refusing to remove {dest} — it resolves outside {claude_dir}. "
            "This protects against accidentally wiping the library or user home."
        )
    shutil.rmtree(dest)


def _try_symlink(src: Path, dest: Path) -> bool:
    """Create a symlink, or fall back to None on platforms without permission.

    Returns True if the link was created, False if the caller should fall back to copy."""
    try:
        dest.symlink_to(src.resolve())
        return True
    except OSError:
        # Common on Windows without Developer Mode / admin; also NFS sometimes.
        return False


def _update_gitignore(project_path: Path, installed_dirs: list[str]) -> None:
    """Write or update the managed gitignore block at .claude/.gitignore.

    Rewrites the block so newly-symlinked paths are added on a re-run; the
    block is always kept alphabetically sorted and de-duplicated."""
    gi = project_path / PROJECT_CLAUDE_DIR / ".gitignore"
    gi.parent.mkdir(parents=True, exist_ok=True)

    existing = gi.read_text() if gi.exists() else ""
    # Strip any existing managed block; we will re-emit it with a merged set.
    tracked: set[str] = set(installed_dirs)
    if GITIGNORE_MARKER_START in existing:
        before, _, tail = existing.partition(GITIGNORE_MARKER_START)
        inside, _, after = tail.partition(GITIGNORE_MARKER_END)
        for line in inside.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                tracked.add(line)
        outside = (before.rstrip() + "\n\n" + after.lstrip()) if after.strip() else before
    else:
        outside = existing

    block = [GITIGNORE_MARKER_START] + sorted(tracked) + [GITIGNORE_MARKER_END]
    new_contents = outside.rstrip() + ("\n\n" if outside.strip() else "") + "\n".join(block) + "\n"
    gi.write_text(new_contents)


def resolve_skills(names: list[str], library: Path) -> list[tuple[str, Path]]:
    resolved: list[tuple[str, Path]] = []
    skills_root = library / SKILLS_DIR
    for name in names:
        candidate = skills_root / name
        if not candidate.exists():
            print(f"  [!] Skill '{name}' not found in library, skipping.")
            continue
        if not (candidate / "SKILL.md").exists():
            print(f"  [!] '{name}' exists but has no SKILL.md, skipping.")
            continue
        resolved.append((name, candidate))
    return resolved


def resolve_agents(names: list[str], library: Path) -> list[tuple[str, Path]]:
    """Resolve agent names that are present in the library. Names not found
    are assumed to be Claude Code built-in agents — the caller records them
    separately in `summary['agents_builtin']`, no warning needed."""
    resolved: list[tuple[str, Path]] = []
    agents_root = library / AGENTS_DIR
    for name in names:
        candidate = agents_root / name
        if candidate.exists():
            resolved.append((name, candidate))
    return resolved


def install_to_project(
    project_path: Path,
    skills: Optional[list[str]] = None,
    agents: Optional[list[str]] = None,
    presets: Optional[list[str]] = None,
    fetch: Optional[list[str]] = None,
    use_symlinks: Optional[bool] = None,
    dry_run: bool = False,
    overwrite: bool = False,
    library: Optional[Path] = None,
    verbose: bool = False,
    auto_fetch: bool = True,
) -> dict:
    lib = library or get_library()
    skills = list(skills or [])
    agents = list(agents or [])
    fetch = list(fetch or [])

    for preset in (presets or []):
        items = get_preset_items(preset, lib)
        skills.extend(items.get("skills", []))
        agents.extend(items.get("agents", []))
        fetch.extend(items.get("fetch", []))

    skills = list(dict.fromkeys(skills))
    agents = list(dict.fromkeys(agents))
    fetch = list(dict.fromkeys(fetch))

    # Fetched skills (format: "owner/repo#skill-name") → pull into library first,
    # then install like any other library skill. This is how claude-setup ships:
    # we don't vendor skill content, we route you to the real author's repo.
    fetched_names: list[str] = []
    fetch_errors: list[str] = []
    if fetch and auto_fetch and not dry_run:
        from .library import fetch_from_skills_sh
        from .audit import is_trusted
        for entry in fetch:
            if "#" not in entry:
                fetch_errors.append(f"malformed fetch entry {entry!r} (expected 'owner/repo#skill')")
                continue
            source, _, skill_name = entry.partition("#")
            if not is_trusted(source, lib):
                fetch_errors.append(
                    f"{entry}: author not on trust.toml allowlist — "
                    "edit ~/.claude-library/trust.toml to add them, then re-run"
                )
                continue
            try:
                installed = fetch_from_skills_sh(
                    source=source, skill_names=[skill_name], library=lib, verbose=verbose,
                )
                if installed:
                    fetched_names.append(skill_name)
                    skills.append(skill_name)  # then install into the project
            except Exception as exc:
                fetch_errors.append(f"{entry}: {exc}")

    in_git = _is_git_repo(project_path)
    if use_symlinks is None:
        use_symlinks = not in_git

    summary: dict = {
        "skills_installed": [],
        "skills_skipped": [],
        "skills_missing": [],
        "agents_installed": [],
        "agents_skipped": [],
        "agents_missing": [],
        "agents_builtin": [],       # names of built-in Claude Code agents referenced by the preset
        "fetched": fetched_names,
        "fetch_errors": fetch_errors,
        "mode": "symlink" if use_symlinks else "copy",
        "git_repo": in_git,
        "wrote_gitignore": False,
    }

    symlink_relpaths: list[str] = []

    def _place(src: Path, dest: Path) -> str:
        """Return the actual mode used for this placement ('symlink' or 'copy')."""
        if use_symlinks and _try_symlink(src, dest):
            return "symlink"
        shutil.copytree(src, dest)
        return "copy"

    # Skills
    if skills:
        resolved = resolve_skills(skills, lib)
        resolved_names = {n for n, _ in resolved}
        summary["skills_missing"] = [n for n in skills if n not in resolved_names]
        dest_root = project_path / PROJECT_SKILLS_PATH
        if not dry_run:
            dest_root.mkdir(parents=True, exist_ok=True)

        for name, src in resolved:
            leaf = Path(name).name
            dest = dest_root / leaf
            if dest.exists() and not overwrite:
                if verbose:
                    print(f"  [skip] skill '{name}' already exists at {dest}")
                summary["skills_skipped"].append(name)
                continue

            if dry_run:
                action = "symlink" if use_symlinks else "copy"
                print(f"  [dry-run] Would {action} skill '{name}' → {dest}")
                summary["skills_installed"].append(name)
                continue

            if dest.exists():
                _safe_remove(dest, project_path)

            actual = _place(src, dest)
            if actual == "symlink":
                symlink_relpaths.append(f"skills/{leaf}")
            summary["skills_installed"].append(name)
            if verbose:
                verb = "Symlinked" if actual == "symlink" else "Copied"
                print(f"  ✓ {verb} skill '{name}' → {dest}")

    # Agents. claude-setup does not ship agent content. A preset's `agents = [...]`
    # list references Claude Code's built-in agents by name — we don't install
    # them (they already ship with Claude Code) but we record them so the
    # summary shows what's active for this stack.
    if agents:
        resolved = resolve_agents(agents, lib)
        resolved_names = {n for n, _ in resolved}
        # Any agent name NOT in the library is assumed to be a built-in reference.
        summary["agents_builtin"] = [n for n in agents if n not in resolved_names]
        dest_root = project_path / PROJECT_AGENTS_PATH
        if not dry_run and resolved:
            dest_root.mkdir(parents=True, exist_ok=True)

        for name, src in resolved:
            dest = dest_root / name
            if dest.exists() and not overwrite:
                if verbose:
                    print(f"  [skip] agent '{name}' already exists at {dest}")
                summary["agents_skipped"].append(name)
                continue

            if dry_run:
                action = "symlink" if use_symlinks else "copy"
                print(f"  [dry-run] Would {action} agent '{name}' → {dest}")
                summary["agents_installed"].append(name)
                continue

            if dest.exists():
                _safe_remove(dest, project_path)

            actual = _place(src, dest)
            if actual == "symlink":
                symlink_relpaths.append(f"agents/{name}")
            summary["agents_installed"].append(name)
            if verbose:
                verb = "Symlinked" if actual == "symlink" else "Copied"
                print(f"  ✓ {verb} agent '{name}' → {dest}")

    # If any symlinks landed in a git repo, manage .claude/.gitignore.
    if in_git and not dry_run and symlink_relpaths:
        _update_gitignore(project_path, symlink_relpaths)
        summary["wrote_gitignore"] = True
        if verbose:
            print("  ✓ Updated .claude/.gitignore for personal symlinks")

    # If the realised mode differed from the asked mode (symlink failed → copy),
    # reflect reality in the summary.
    if use_symlinks and symlink_relpaths == [] and (skills or agents) and not dry_run:
        summary["mode"] = "copy"

    return summary


def remove_from_project(
    project_path: Path,
    skills: Optional[list[str]] = None,
    agents: Optional[list[str]] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict:
    summary: dict = {"removed": [], "not_found": []}

    for name in (skills or []):
        dest = project_path / PROJECT_SKILLS_PATH / Path(name).name
        if not dest.exists():
            summary["not_found"].append(f"skill:{name}")
            continue
        if dry_run:
            print(f"  [dry-run] Would remove skill '{name}' from {dest}")
            summary["removed"].append(f"skill:{name}")
            continue
        _safe_remove(dest, project_path)
        summary["removed"].append(f"skill:{name}")
        if verbose:
            print(f"  ✓ Removed skill '{name}'")

    for name in (agents or []):
        dest = project_path / PROJECT_AGENTS_PATH / name
        if not dest.exists():
            summary["not_found"].append(f"agent:{name}")
            continue
        if dry_run:
            print(f"  [dry-run] Would remove agent '{name}' from {dest}")
            summary["removed"].append(f"agent:{name}")
            continue
        _safe_remove(dest, project_path)
        summary["removed"].append(f"agent:{name}")
        if verbose:
            print(f"  ✓ Removed agent '{name}'")

    return summary
