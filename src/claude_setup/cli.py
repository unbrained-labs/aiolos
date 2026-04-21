"""claude-setup CLI — install Claude Code skills and agents from your library."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import shutil as _shutil

from .audit import (
    audit_library,
    audit_skill as audit_skill_dir,
    ensure_trust_file,
    is_trusted,
    source_author,
)
from .config import ensure_library, get_library
from .detect import detect_presets, pick_presets
from .doctor import run_doctor
from .harden import (
    AVAILABLE_HOOKS,
    Policy,
    defaults as harden_defaults,
    run_questionnaire,
    write_settings,
)
from .installer import install_to_project, remove_from_project
from .library import (
    fetch_from_skills_sh,
    list_agents,
    list_presets,
    list_skills,
    load_preset,
)
from .lint import lint_library, lint_skill
from .scaffolder import scaffold_agent, scaffold_skill
from . import mcp as mcp_mod
from . import tools as cli_tools


# ── init (one-shot) ───────────────────────────────────────────────────────────

def _auto_seed_presets(lib: Path) -> bool:
    """If the library has no presets, copy the shipped presets from the repo.
    We do NOT seed skills or agents — claude-setup doesn't ship that content.
    """
    if list_presets(lib):
        return False
    candidates = [Path.cwd(), Path(__file__).resolve().parents[2]]
    for root in candidates:
        presets_src = root / "presets"
        if not presets_src.is_dir():
            continue
        (lib / "presets").mkdir(parents=True, exist_ok=True)
        for p in list(presets_src.glob("*.toml")) + list(presets_src.glob("*.txt")):
            dest = lib / "presets" / p.name
            if not dest.exists():
                _shutil.copy(p, dest)
        return True
    return False


def _print_header(title: str) -> None:
    line = "─" * max(8, 40 - len(title))
    print(f"\n── {title} {line}")


def cmd_init(args: argparse.Namespace) -> None:
    """Detect → install matching preset(s) → report.

    Monorepos: if multiple presets tie for top score, we install them all
    (orthogonal stacks). Install is deduped across presets.
    """
    lib = get_library()
    ensure_library(lib)

    seeded = False
    if not list_presets(lib):
        seeded = _auto_seed_presets(lib)

    project = Path(args.project).resolve()

    use_symlinks: bool | None
    if args.symlink:
        use_symlinks = True
    elif args.copy:
        use_symlinks = False
    else:
        use_symlinks = None

    matches = detect_presets(project, lib)
    fallback_used = False
    ambiguous = False

    selected_list: list[str]
    if matches:
        top_score = matches[0]["score"]
        top_presets = pick_presets(matches, additive=True)
        # "ambiguous" only if we have no confidence — i.e. top score is 1 and
        # more than one preset tied, signalling a weak detection. Stronger
        # matches are treated as a legitimate multi-stack repo.
        if top_score == 1 and len(top_presets) > 1:
            ambiguous = True
        selected_list = top_presets
    elif "base" in list_presets(lib):
        selected_list = ["base"]
        fallback_used = True
    else:
        selected_list = []

    report: dict = {
        "project": str(project),
        "library": str(lib),
        "seeded_library": seeded,
        "matches": matches,
        "selected": selected_list,
        "fallback_used": fallback_used,
        "ambiguous": ambiguous,
        "installed": None,
    }

    # We install unless ambiguous-and-not-forced.
    if selected_list and not (ambiguous and not args.force):
        summary = install_to_project(
            project_path=project,
            presets=selected_list,
            use_symlinks=use_symlinks,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            library=lib,
            verbose=False,  # we render our own tighter summary below
        )
        report["installed"] = summary

    if args.json:
        print(json.dumps(report, indent=2, default=str))
        return

    # ── Human-readable output ────────────────────────────────────────────
    print(f"◇ Project  {project}")
    print(f"◇ Library  {lib}")
    if seeded:
        print("◇ Seeded library from the repo starter set.")

    if not selected_list:
        print("\n✗ No presets matched and no 'base' preset is available.")
        print("  Next step: `./bootstrap.sh` to seed the library, or add a preset TOML to")
        print("  ~/.claude-library/presets/ with a [detect] block for this stack.")
        sys.exit(1)

    if matches:
        _print_header("Detected")
        for m in matches:
            marker = "✓" if m["preset"] in selected_list else "·"
            print(f"  {marker} {m['preset']}  (score {m['score']})")
            for r in m["reasons"]:
                print(f"      · {r}")

    if ambiguous and not args.force:
        print()
        print(f"Top matches tied at score 1: {', '.join(selected_list)}")
        print("Too little signal to pick — re-run with `--force` or")
        print(f"  claude-setup install --preset {selected_list[0]}")
        sys.exit(0)

    if fallback_used:
        print("\n· No preset matched — installed `base` as a starter.")

    if report["installed"]:
        s = report["installed"]
        _print_header("Active for this project")
        if s.get("agents_builtin"):
            print(f"  built-in agents  {', '.join(s['agents_builtin'])}")
            print("                   (ship with Claude Code; no install needed)")
        if s["skills_installed"]:
            print(f"  skills           {', '.join(s['skills_installed'])}")
        if s.get("fetched"):
            print(f"  fetched          {', '.join(s['fetched'])}")
        if s.get("fetch_errors"):
            print()
            print("  fetch errors:")
            for err in s["fetch_errors"]:
                print(f"    ✗ {err}")
        if s["skills_installed"] or s["agents_installed"]:
            print(f"  mode             {s.get('mode', '?')}"
                  + ("  (git-safe copy)" if s.get("git_repo") and s.get("mode") == "copy" else ""))
        if s.get("wrote_gitignore"):
            print("  note             wrote .claude/.gitignore for personal symlinks")

        # Closing hint: installed CLIs with no wrapper
        try:
            statuses = cli_tools.scan(project, lib)
        except Exception:
            statuses = []
        wrappable = [st for st in statuses if st.installed and not st.wrapped_by_library]
        if wrappable:
            _print_header("Authenticated CLIs with no wrapper")
            for st in wrappable[:5]:
                print(f"  • {st.tool.command:<10} {st.tool.blurb}")
            print("  Run `claude-setup tools --scaffold-all` to wrap them.")

        _print_header("Next")
        print("  · open this repo in Claude Code and run /skills to verify")
        print("  · `claude-setup harden` to add deny-rule baseline in settings.json")


# ── install ───────────────────────────────────────────────────────────────────

def cmd_install(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    project = Path(args.project).resolve()

    if not args.skill and not args.agent and not args.preset:
        print("Nothing to install. Specify --skill, --agent, or --preset.")
        sys.exit(1)

    use_symlinks: bool | None
    if args.symlink:
        use_symlinks = True
    elif args.copy:
        use_symlinks = False
    else:
        use_symlinks = None

    print(f"Library : {lib}")
    print(f"Project : {project}")
    if args.dry_run:
        print("Mode    : dry-run (no changes will be made)")
    print()

    summary = install_to_project(
        project_path=project,
        skills=args.skill or [],
        agents=args.agent or [],
        presets=args.preset or [],
        use_symlinks=use_symlinks,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        library=lib,
        verbose=True,
    )

    print()
    si = summary["skills_installed"]
    ss = summary["skills_skipped"]
    ai = summary["agents_installed"]
    as_ = summary["agents_skipped"]
    mode = summary.get("mode", "?")
    in_git = summary.get("git_repo", False)

    if si:
        print(f"Skills installed  : {', '.join(si)}")
    if ss:
        print(f"Skills skipped    : {', '.join(ss)}  (use --overwrite to replace)")
    if ai:
        print(f"Agents installed  : {', '.join(ai)}")
    if as_:
        print(f"Agents skipped    : {', '.join(as_)}  (use --overwrite to replace)")

    total = len(si) + len(ai)
    if total == 0 and not args.dry_run:
        print("Nothing was installed.")
    elif not args.dry_run:
        verb = "symlinked" if mode == "symlink" else "copied"
        print(f"\nDone — {total} item(s) {verb} into {project / '.claude'}")
        if mode == "symlink" and in_git:
            print("      Wrote .claude/.gitignore so personal symlinks stay out of the repo.")
        if mode == "copy" and in_git:
            print("      Copied (not symlinked) because this is a git repo — commit safely.")


# ── remove ────────────────────────────────────────────────────────────────────

def cmd_remove(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()

    if not args.skill and not args.agent:
        print("Nothing to remove. Specify --skill or --agent.")
        sys.exit(1)

    print(f"Project : {project}")
    if args.dry_run:
        print("Mode    : dry-run")
    print()

    summary = remove_from_project(
        project_path=project,
        skills=args.skill or [],
        agents=args.agent or [],
        dry_run=args.dry_run,
        verbose=True,
    )

    if summary["not_found"]:
        print(f"\nNot found: {', '.join(summary['not_found'])}")
    if summary["removed"] and not args.dry_run:
        print(f"\nRemoved: {', '.join(summary['removed'])}")


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    what = args.what or "all"

    if what in ("all", "skills"):
        skills = list_skills(lib)
        print(f"Skills ({len(skills)}):")
        for s in skills:
            print(f"  {s}")
        if not skills:
            print(f"  (none — add skills to {lib}/skills/)")
        print()

    if what in ("all", "agents"):
        agents = list_agents(lib)
        print(f"Agents ({len(agents)}):")
        for a in agents:
            print(f"  {a}")
        if not agents:
            print(f"  (none — add agents to {lib}/agents/)")
        print()

    if what in ("all", "presets"):
        presets = list_presets(lib)
        print(f"Presets ({len(presets)}):")
        for p in presets:
            try:
                resolved = load_preset(p, lib)
                parts = []
                if resolved["skills"]:
                    parts.append(f"{len(resolved['skills'])} skill(s)")
                if resolved["agents"]:
                    parts.append(f"{len(resolved['agents'])} agent(s)")
                suffix = f"  [{', '.join(parts)}]" if parts else ""
                if len(resolved["chain"]) > 1:
                    suffix += f"  (extends: {' → '.join(resolved['chain'])})"
                print(f"  {p}{suffix}")
            except Exception as e:
                print(f"  {p}  [error: {e}]")
        if not presets:
            print(f"  (none — add presets to {lib}/presets/)")


# ── detect ────────────────────────────────────────────────────────────────────

def cmd_detect(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    project = Path(args.project).resolve()
    matches = detect_presets(project, lib)

    if args.json:
        print(json.dumps({"project": str(project), "matches": matches}, indent=2))
        return

    print(f"Project : {project}")
    print(f"Library : {lib}\n")
    if not matches:
        print("No presets matched this project.")
        return

    print(f"Matched {len(matches)} preset(s):\n")
    for m in matches:
        if m.get("error"):
            print(f"  ✗ {m['preset']} — {m['reasons'][0]}")
            continue
        print(f"  ✓ {m['preset']}  (score {m['score']})")
        for r in m["reasons"]:
            print(f"      · {r}")
        print(f"      skills: {', '.join(m['skills']) or '(none)'}")
        print(f"      agents: {', '.join(m['agents']) or '(none)'}")
        print()

    if args.install:
        top = matches[0]["preset"]
        print(f"Installing top match: {top}\n")
        install_to_project(
            project_path=project,
            presets=[top],
            use_symlinks=None,
            dry_run=args.dry_run,
            overwrite=False,
            library=lib,
            verbose=True,
        )


# ── fetch ─────────────────────────────────────────────────────────────────────

def cmd_fetch(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    ensure_trust_file(lib)

    print(f"Source  : {args.source}")
    print(f"Library : {lib}\n")

    if not is_trusted(args.source, lib) and not args.yes_unknown_author:
        author = source_author(args.source)
        print(
            f"Author {author!r} is not on the trust allowlist at {lib}/trust.toml. "
            "Skills can run arbitrary shell commands — review SKILL.md before installing."
        )
        print("Pass --yes-unknown-author to proceed, or add the author to trust.toml.")
        sys.exit(2)

    if args.list:
        fetch_from_skills_sh(source=args.source, skill_names=[], library=lib, verbose=True)
        return

    if not args.skill:
        print("Specify --skill NAME to fetch, or --list to browse.")
        sys.exit(1)

    installed = fetch_from_skills_sh(
        source=args.source,
        skill_names=args.skill,
        library=lib,
        verbose=True,
    )
    if installed:
        print(f"\nFetched {len(installed)} skill(s): {', '.join(installed)}")
        # Audit each freshly-fetched skill so the user sees any red flags immediately.
        for skill in installed:
            skill_dir = lib / "skills" / skill
            findings = audit_skill_dir(skill_dir)
            crit = [f for f in findings if f.severity in {"critical", "high"}]
            if crit:
                print(f"\n⚠  Audit findings for {skill}:")
                for f in crit:
                    print(f)
    else:
        print("\nNothing was fetched.")


# ── audit ─────────────────────────────────────────────────────────────────────

def cmd_audit(args: argparse.Namespace) -> None:
    target = Path(args.path) if args.path else None
    if target:
        findings = audit_skill_dir(target)
    else:
        findings = audit_library()

    for f in findings:
        print(f)

    crit = sum(1 for f in findings if f.severity == "critical")
    high = sum(1 for f in findings if f.severity == "high")
    med = sum(1 for f in findings if f.severity == "medium")
    low = sum(1 for f in findings if f.severity == "low")
    print(f"\n{crit} critical, {high} high, {med} medium, {low} low.")
    sys.exit(1 if crit or high else 0)


# ── harden ────────────────────────────────────────────────────────────────────

def cmd_harden(args: argparse.Namespace) -> None:
    project = Path(args.project).resolve()
    if args.defaults:
        policy = harden_defaults()
    else:
        policy = run_questionnaire()

    summary = write_settings(project, policy)
    print()
    print(f"Wrote {summary['path']}")
    print(
        f"  {summary['deny_rules']} deny rules, "
        f"{summary['hooks']} hook(s) "
        f"({'updated' if summary['existed'] else 'new'})"
    )
    print("Review with:  cat .claude/settings.json | jq '.permissions.deny'")


# ── tools ─────────────────────────────────────────────────────────────────────

def cmd_tools(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    project = Path(args.project).resolve()

    if args.json:
        print(json.dumps(cli_tools.scan_as_dict(project, lib), indent=2, default=str))
        return

    statuses = cli_tools.scan(project, lib)
    print(f"Project : {project}")
    print(f"Library : {lib}\n")
    print(cli_tools.format_scan(statuses))

    if args.scaffold_all:
        made: list[str] = []
        for s in statuses:
            if s.installed and not s.wrapped_by_library:
                desc = (
                    f"ALWAYS invoke this skill when the user mentions {s.tool.command} "
                    f"or tasks this CLI handles ({s.tool.blurb.rstrip('.')}). "
                    f"Do NOT shell out to {s.tool.command} without this skill."
                )
                try:
                    scaffold_skill(
                        name=s.tool.skill_name,
                        description=desc,
                        library=lib,
                        wraps=s.tool.command,
                    )
                    made.append(s.tool.skill_name)
                except (ValueError, FileExistsError):
                    pass
        if made:
            print(f"\nScaffolded {len(made)} wrapper skill(s):")
            for m in made:
                print(f"  ✓ {m}")
            print("Edit them, then install into a project with `claude-setup install`.")


# ── new-skill / new-agent ─────────────────────────────────────────────────────

def cmd_new_skill(args: argparse.Namespace) -> None:
    name = args.name
    description = args.description
    wraps = args.wraps
    allowed_tools = args.allowed_tools

    # If the user ran `claude-setup new-skill` bare, walk them through it.
    # Three short prompts, nothing more.
    if not description:
        if not name:
            try:
                name = input("Skill name (e.g. git/commit): ").strip()
            except EOFError:
                print("Error: name is required.", file=sys.stderr)
                sys.exit(1)
        print(
            "\nDescription tip: start with 'ALWAYS invoke this skill when …' or "
            "'Use when …'.\nOne line. Front-load the triggers — it's how Claude picks it."
        )
        try:
            description = input("Description: ").strip()
        except EOFError:
            print("Error: description is required.", file=sys.stderr)
            sys.exit(1)
        try:
            wraps_in = input("Wraps a CLI? (empty to skip, e.g. gh / flyctl): ").strip()
            wraps = wraps_in or None
        except EOFError:
            wraps = None

    if not name or not description:
        print("Error: name and description are required.", file=sys.stderr)
        sys.exit(1)

    try:
        path = scaffold_skill(
            name=name,
            description=description,
            allowed_tools=allowed_tools,
            overwrite=args.overwrite,
            wraps=wraps,
        )
    except (ValueError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        if "description problems" in str(e):
            print("\nTip: descriptions need a trigger like 'Use when …' / 'ALWAYS invoke when …'", file=sys.stderr)
        sys.exit(1)

    print(f"\n✓ Wrote {path}")
    print(f"  Next:  claude-setup lint {path}")


def cmd_new_agent(args: argparse.Namespace) -> None:
    try:
        path = scaffold_agent(
            name=args.name,
            description=args.description,
            role=args.role,
            model=args.model,
            overwrite=args.overwrite,
        )
    except (ValueError, FileExistsError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {path}")


# ── lint ──────────────────────────────────────────────────────────────────────

def cmd_lint(args: argparse.Namespace) -> None:
    if args.path:
        issues = lint_skill(Path(args.path))
    else:
        lib = get_library()
        ensure_library(lib)
        issues = lint_library(lib)

    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity == "warn"]
    infos = [i for i in issues if i.severity == "info"]

    for i in issues:
        print(i)

    print()
    print(f"{len(errors)} error(s), {len(warns)} warning(s), {len(infos)} info.")
    sys.exit(1 if errors else 0)


# ── doctor ────────────────────────────────────────────────────────────────────

def cmd_doctor(args: argparse.Namespace) -> None:
    findings = run_doctor()
    errors = [f for f in findings if f.severity == "error"]
    for f in findings:
        print(f)

    # Also surface critical audit findings so a skill with Bash(*) or curl | sh
    # shows up here, not only when the user remembers to run `audit`.
    audit_findings = audit_library()
    crit = [f for f in audit_findings if f.severity in {"critical", "high"}]
    if crit:
        print()
        print("Audit flags:")
        for f in crit:
            print(f)

    if not findings and not crit:
        print("  ✓ library looks healthy")
    total_errors = len(errors) + sum(1 for f in crit if f.severity == "critical")
    print(
        f"\n{len(errors)} structural error(s), "
        f"{len(crit)} audit flag(s), "
        f"{len(findings) - len(errors)} warning(s)/info."
    )
    sys.exit(1 if total_errors else 0)


# ── parser ────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-setup",
        description="Install Claude Code skills and agents from your personal library.",
    )
    sub = parser.add_subparsers(dest="command")

    # init (one-shot)
    p = sub.add_parser("init", help="One-shot: detect → install top preset (or base fallback)")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    link = p.add_mutually_exclusive_group()
    link.add_argument("--copy", action="store_true")
    link.add_argument("--symlink", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="Install top match even when ambiguous")

    # install
    p = sub.add_parser("install", help="Install skills/agents into a project")
    p.add_argument("--skill", "-s", metavar="SKILL", action="append")
    p.add_argument("--agent", "-a", metavar="AGENT", action="append")
    p.add_argument("--preset", "-p", metavar="PRESET", action="append")
    p.add_argument("--project", default=".", metavar="PATH")
    link = p.add_mutually_exclusive_group()
    link.add_argument("--copy", action="store_true")
    link.add_argument("--symlink", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # remove
    p = sub.add_parser("remove", help="Remove installed skills/agents")
    p.add_argument("--skill", "-s", metavar="SKILL", action="append")
    p.add_argument("--agent", "-a", metavar="AGENT", action="append")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--dry-run", action="store_true")

    # list
    p = sub.add_parser("list", aliases=["ls"], help="List skills/agents/presets in library")
    p.add_argument("what", nargs="?", choices=["skills", "agents", "presets", "all"], default="all")

    # detect
    p = sub.add_parser("detect", help="Scan project and match presets")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.add_argument("--install", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # fetch
    p = sub.add_parser("fetch", help="Pull skills from skills.sh / GitHub into library")
    p.add_argument("source", metavar="SOURCE")
    p.add_argument("--skill", "-s", metavar="SKILL", action="append")
    p.add_argument("--list", action="store_true")
    p.add_argument("--yes-unknown-author", action="store_true",
                   help="Proceed even if the source author isn't on trust.toml")

    # new-skill
    p = sub.add_parser("new-skill", help="Scaffold a new SKILL.md in the library (interactive if bare)")
    p.add_argument("name", nargs="?", help="e.g. git/commit — lowercase with optional namespace")
    p.add_argument("--description", "-d",
                   help='Single-line imperative description. Must include a trigger like "Use when…".')
    p.add_argument("--allowed-tools", default="Read",
                   help="Space-separated tool list, e.g. \"Bash(git *) Read\"")
    p.add_argument("--wraps", metavar="CLI",
                   help="Generate a CLI-wrapper skill for this binary (e.g. gh, flyctl).")
    p.add_argument("--overwrite", action="store_true")

    # new-agent
    p = sub.add_parser("new-agent", help="Scaffold a new subagent in the library")
    p.add_argument("name")
    p.add_argument("--description", "-d", required=True)
    p.add_argument("--role", default="specialist")
    p.add_argument("--model", default="sonnet", choices=["sonnet", "opus", "haiku", "inherit"])
    p.add_argument("--overwrite", action="store_true")

    # lint
    p = sub.add_parser("lint", help="Validate SKILL.md files (one file, or the whole library)")
    p.add_argument("path", nargs="?", help="Path to a SKILL.md (omit to lint whole library)")

    # doctor
    p = sub.add_parser("doctor", help="Diagnose library health — missing refs, broken presets")

    # audit
    p = sub.add_parser("audit", help="Scan a skill (or the whole library) for high-risk patterns")
    p.add_argument("path", nargs="?", help="Path to a skill directory or file (omit for library)")

    # harden
    p = sub.add_parser("harden", help="Write .claude/settings.json with deny rules + hooks")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--defaults", action="store_true",
                   help="Use sensible defaults (no questions)")

    # tools
    p = sub.add_parser("tools", help="Scan PATH for productivity CLIs and suggest wrappers")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.add_argument("--scaffold-all", action="store_true",
                   help="Scaffold wrapper skills for every installed-but-unwrapped CLI")

    # wizard
    p = sub.add_parser("wizard", help="One-shot grand opening: init + harden + tools (with optional techno)")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--noninteractive", action="store_true",
                   help="Skip the sound prompt (used by CI and tests)")

    # mcp
    p = sub.add_parser("mcp", help="Write .mcp.json with ${VAR}-placeholder secrets")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--server", action="append", metavar="SLUG",
                   help="MCP server slug from the catalog (repeatable)")
    p.add_argument("--preset", action="append", metavar="NAME",
                   help="Install MCP servers declared in a preset (repeatable)")
    p.add_argument("--clear", action="store_true",
                   help="Remove claude-setup-managed entries from .mcp.json")
    p.add_argument("--dry-run", action="store_true")

    # fetch already exists earlier; extend its flags
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
    dispatch = {
        "init": cmd_init,
        "install": cmd_install,
        "remove": cmd_remove,
        "list": cmd_list,
        "ls": cmd_list,
        "detect": cmd_detect,
        "fetch": cmd_fetch,
        "new-skill": cmd_new_skill,
        "new-agent": cmd_new_agent,
        "lint": cmd_lint,
        "doctor": cmd_doctor,
        "audit": cmd_audit,
        "harden": cmd_harden,
        "tools": cmd_tools,
        "wizard": cmd_wizard,
        "mcp": cmd_mcp,
    }
    dispatch[args.command](args)


def cmd_wizard(args: argparse.Namespace) -> None:
    from .wizard import run_wizard
    run_wizard(Path(args.project).resolve(), noninteractive=args.noninteractive)


# ── mcp ───────────────────────────────────────────────────────────────────────

def cmd_mcp(args: argparse.Namespace) -> None:
    lib = get_library()
    ensure_library(lib)
    project = Path(args.project).resolve()

    # Collect slugs + custom defs: from preset(s), plus explicit --server flags.
    slugs: list[str] = list(args.server or [])
    custom: list[mcp_mod.McpServer] = []

    for preset in (args.preset or []):
        try:
            resolved = load_preset(preset, lib)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        slugs.extend(resolved.get("mcp") or [])
        for entry in resolved.get("mcp_custom") or []:
            try:
                custom.append(mcp_mod.server_from_dict(entry))
            except ValueError as e:
                print(f"Error in preset {preset!r}: {e}", file=sys.stderr)
                sys.exit(1)

    # No targets? Default to detection-driven suggestions.
    if not slugs and not custom and not args.clear:
        suggestions = mcp_mod.suggest(project)
        if not suggestions:
            print("No MCP servers suggested for this project.")
            print("Options:")
            print("  · pick one:  claude-setup mcp --server postgres --server github")
            print("  · from preset:  claude-setup mcp --preset nextjs")
            print(f"  · list catalog:  {sorted(mcp_mod.BY_SLUG)}")
            return
        print("Suggested based on this repo:\n")
        for s in suggestions:
            env_hint = f"  (env: {', '.join(s.env)})" if s.env else ""
            print(f"  • {s.slug:<12} {s.description}{env_hint}")
        print()
        print("Install with:  claude-setup mcp " + " ".join(f"--server {s.slug}" for s in suggestions))
        return

    if args.clear:
        # Remove all managed entries by installing an empty list.
        summary = mcp_mod.write_mcp_config(project, [], custom_servers=[], dry_run=args.dry_run)
        print(f"Cleared managed MCP entries from {summary['path']}")
        return

    slugs = list(dict.fromkeys(slugs))
    try:
        summary = mcp_mod.write_mcp_config(
            project, slugs, custom_servers=custom, dry_run=args.dry_run,
        )
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print(f"[dry-run] Would install: {', '.join(summary['would_install'])}")
        if summary["env_keys"]:
            print(f"[dry-run] Would record env keys: {', '.join(summary['env_keys'])}")
        return

    print(f"✓ Wrote {summary['path']}")
    print(f"  installed  {', '.join(summary['installed'])}")
    if summary.get("env_example"):
        print(f"  env keys   {', '.join(summary['env_keys'])}")
        print(f"  example    {summary['env_example']}")
        print("  next       copy to .env.claude and fill in real values")
    if summary.get("wrote_gitignore"):
        print("  note       added .env.claude to .gitignore")


if __name__ == "__main__":
    main()
