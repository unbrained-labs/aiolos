"""aiolos CLI — install Claude Code skills and agents from your library."""
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
from .prompt import choose, confirm, multiselect
from .scaffolder import scaffold_agent, scaffold_skill
from . import mcp as mcp_mod
from . import tools as cli_tools


def _assume_yes(args: argparse.Namespace) -> bool:
    """A few flags all mean 'don't prompt me': --yes, --json, --dry-run,
    --noninteractive. Called everywhere we'd otherwise ask for confirmation."""
    return bool(
        getattr(args, "yes", False)
        or getattr(args, "json", False)
        or getattr(args, "dry_run", False)
        or getattr(args, "noninteractive", False)
    )


# ── init (one-shot) ───────────────────────────────────────────────────────────

def _auto_seed_presets(lib: Path) -> bool:
    """If the library has no presets, copy the shipped presets from the repo.
    We do NOT seed skills or agents — aiolos doesn't ship that content.
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
    """Detect → preview plan → confirm → install.

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

    # ── JSON fast-path (no human output, no prompts) ─────────────────────
    if args.json:
        if selected_list and not (ambiguous and not args.force):
            report["installed"] = install_to_project(
                project_path=project,
                presets=selected_list,
                use_symlinks=use_symlinks,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
                library=lib,
                verbose=False,
            )
        print(json.dumps(report, indent=2, default=str))
        return

    # ── Human flow: detection → plan → confirm → install ─────────────────
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
        _print_header("Detected stack")
        for m in matches:
            marker = "✓" if m["preset"] in selected_list else "·"
            print(f"  {marker} {m['preset']}  (score {m['score']})")
            for r in m["reasons"]:
                print(f"      · {r}")

    if ambiguous and not args.force:
        print()
        print(f"Top matches tied at score 1: {', '.join(selected_list)}")
        print("Too little signal to pick — re-run with `--force` or")
        print(f"  aiolos install --preset {selected_list[0]}")
        sys.exit(0)

    if fallback_used:
        print("\n· No preset matched — falling back to `base` as a starter.")

    # Compute the full plan (builtins + skills + fetches) before writing anything.
    from .library import load_preset
    plan_agents_builtin: list[str] = []
    plan_skills_library: list[str] = []
    plan_fetches: list[str] = []
    for preset in selected_list:
        resolved = load_preset(preset, lib)
        plan_agents_builtin.extend(resolved["agents"])
        plan_skills_library.extend(resolved["skills"])
        plan_fetches.extend(resolved["fetch"])
    plan_agents_builtin = list(dict.fromkeys(plan_agents_builtin))
    plan_skills_library = list(dict.fromkeys(plan_skills_library))
    plan_fetches = list(dict.fromkeys(plan_fetches))

    # Which "library skills" actually exist vs. are just referenced by name
    library_skill_names = set(list_skills(lib))
    will_install = [s for s in plan_skills_library if s in library_skill_names]
    missing_skills = [s for s in plan_skills_library if s not in library_skill_names]

    in_git = (project / ".git").exists()
    mode_label = ("symlink" if use_symlinks else
                  "copy" if use_symlinks is False else
                  ("copy (git repo)" if in_git else "symlink"))

    _print_header("Install plan")
    if plan_agents_builtin:
        print(f"  Claude Code built-in agents:  {', '.join(plan_agents_builtin)}")
        print("    (reference-only — they already ship with Claude Code)")
    if will_install:
        print(f"  library skills → project:     {', '.join(will_install)}")
    if plan_fetches:
        print(f"  community fetches:            {', '.join(plan_fetches)}")
    if missing_skills:
        print(f"  missing from library:         {', '.join(missing_skills)}")
        print("    (referenced by preset but not present — will be skipped)")
    if not (plan_agents_builtin or will_install or plan_fetches):
        print("  (nothing to write — this preset only references built-in agents)")
    print(f"  mode:                         {mode_label}")
    print(f"  writes to:                    {project}/.claude/")

    if not _assume_yes(args):
        if not confirm("\nProceed with install?", default=True):
            print("Cancelled — nothing written.")
            sys.exit(0)

    summary = install_to_project(
        project_path=project,
        presets=selected_list,
        use_symlinks=use_symlinks,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        library=lib,
        verbose=False,
    )
    report["installed"] = summary

    s = summary
    _print_header("Active for this project")
    if s.get("agents_builtin"):
        print(f"  built-in agents  {', '.join(s['agents_builtin'])}")
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

    # Repo-relevant CLI suggestions only — not the whole global toolbox.
    try:
        statuses = cli_tools.scan(project, lib)
    except Exception:
        statuses = []
    relevant_wrap = [st for st in statuses
                     if st.installed and not st.wrapped_by_library and st.repo_suggests]
    if relevant_wrap:
        _print_header("CLIs this repo uses that Claude can't drive yet")
        print("  (a wrapper is a small SKILL.md that tells Claude how to use the CLI)")
        for st in relevant_wrap[:5]:
            reason = st.repo_suggestion_reasons[0] if st.repo_suggestion_reasons else ""
            print(f"  • {st.tool.command:<10} {st.tool.blurb}  ({reason})")
        print("  Run `aiolos tools` to review, then `aiolos tools --scaffold-all` to wrap.")

    _print_header("Next steps (optional)")
    print("  · aiolos browse          — pick community skills from anthropics/skills, skills.sh, …")
    print("  · aiolos harden          — deny secret-file reads, destructive commands, and more")
    print("                             (writes .claude/settings.json + a small lock file we use")
    print("                              to clean up on re-runs)")
    print("  · aiolos mcp             — configure MCP servers for this repo (.mcp.json)")
    print("  · open in Claude Code and run /skills to see what is now active")




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

    # Show what will happen before touching the filesystem.
    _print_header("Install plan")
    if args.preset:
        print(f"  presets:  {', '.join(args.preset)}")
    if args.skill:
        print(f"  skills:   {', '.join(args.skill)}")
    if args.agent:
        print(f"  agents:   {', '.join(args.agent)}")
    print(f"  writes to: {project}/.claude/")

    if not _assume_yes(args):
        if not confirm("\nProceed?", default=True):
            print("Cancelled — nothing written.")
            sys.exit(0)
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

    # Summarise what the policy will do in human terms, then confirm.
    from .harden import compile_deny_rules, compile_hooks, LOCK_FILENAME
    deny_rules = compile_deny_rules(policy)
    hooks = compile_hooks(policy)

    _print_header("Harden plan")
    print(f"  Project: {project}")
    print()
    print("  Categories blocked (deny rules in .claude/settings.json):")
    print("    · read access to ~/.ssh, ~/.aws, ~/.gcloud, keychains, .env*")
    if policy.block_destructive:
        print("    · destructive commands (rm -rf /, git push --force, git reset --hard)")
    if policy.block_cloud_control_plane:
        print("    · cloud control-plane deletes (aws iam delete, kubectl delete ns, terraform destroy)")
    if policy.block_package_publish:
        print("    · package publishing (npm/pnpm/cargo publish, twine upload)")
    if policy.extra_deny:
        print(f"    · {len(policy.extra_deny)} extra rule(s) you configured")
    print(f"    → {len(deny_rules)} deny rules total")
    print()
    if hooks:
        print("  Hooks installed (Claude runs these on tool events):")
        for h in hooks:
            print(f"    · {h['event']} [{h['matcher'] or 'any'}]")
    else:
        print("  Hooks: none")
    print()
    print("  Files written:")
    print(f"    · {project}/.claude/settings.json  — the actual policy")
    print(f"    · {project}/.claude/{LOCK_FILENAME}  — sidecar record of what we installed,")
    print("       so re-runs merge cleanly and never stomp on your hand-written rules")

    if not _assume_yes(args):
        if not confirm("\nWrite these files?", default=True):
            print("Cancelled — nothing written.")
            sys.exit(0)

    summary = write_settings(project, policy)
    print()
    print(f"✓ Wrote {summary['path']}")
    print(f"  {summary['deny_rules']} deny rules, {summary['hooks']} hook(s) "
          f"({'updated' if summary['existed'] else 'new'})")
    print(f"✓ Wrote {summary['lock_path']}  (undo record — don't edit)")
    print()
    print("  Review deny rules:  jq '.permissions.deny' < .claude/settings.json")
    print("  Remove everything:  delete both files and re-run harden")


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
    print(cli_tools.format_scan(statuses, show_all=args.all))

    if args.scaffold_all:
        # Only scaffold for tools that are repo-relevant unless --all is set.
        if args.all:
            pool = [s for s in statuses if s.installed and not s.wrapped_by_library]
        else:
            pool = [s for s in statuses
                    if s.installed and not s.wrapped_by_library and s.repo_suggests]
        if not pool:
            print("\nNothing to scaffold.")
            return
        print(f"\nAbout to scaffold {len(pool)} wrapper skill(s) in your library "
              f"({lib}/skills/):")
        for s in pool:
            print(f"  • {s.tool.skill_name}  (wraps {s.tool.command})")
        if not _assume_yes(args):
            if not confirm("\nCreate these?", default=True):
                print("Cancelled.")
                return
        made: list[str] = []
        for s in pool:
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
            print("Edit them, then install into a project with `aiolos install`.")


# ── new-skill / new-agent ─────────────────────────────────────────────────────

def cmd_new_skill(args: argparse.Namespace) -> None:
    name = args.name
    description = args.description
    wraps = args.wraps
    allowed_tools = args.allowed_tools

    # If the user ran `aiolos new-skill` bare, walk them through it.
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
    print(f"  Next:  aiolos lint {path}")


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
        prog="aiolos",
        description="Install Claude Code skills and agents from your personal library.",
    )
    sub = parser.add_subparsers(dest="command")

    # init (one-shot)
    p = sub.add_parser("init", help="Detect → show plan → confirm → install top preset(s)")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--json", action="store_true", help="Machine-readable output (skips prompts)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt")
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
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt")
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
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt")

    # tools
    p = sub.add_parser("tools", help="Scan PATH for productivity CLIs and suggest wrappers")
    p.add_argument("--project", default=".", metavar="PATH")
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true",
                   help="Show every installed CLI (default: only repo-relevant)")
    p.add_argument("--scaffold-all", action="store_true",
                   help="Scaffold wrapper skills for unwrapped CLIs (repo-relevant unless --all)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt")

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
                   help="Remove aiolos-managed entries from .mcp.json")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--yes", "-y", action="store_true", help="Skip the confirm prompt")

    # browse
    p = sub.add_parser("browse",
                       help="Browse community skills (anthropics/skills, skills.sh, …) and install")
    p.add_argument("--project", default=".", metavar="PATH",
                   help="Where to install selected skills (default: current repo)")
    p.add_argument("--source", metavar="SOURCE",
                   help="Skip the source picker and browse this repo directly (e.g. anthropics/skills)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Skip confirmations (only useful with --source + piped input)")

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
        "browse": cmd_browse,
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
            print("  · pick one:  aiolos mcp --server postgres --server github")
            print("  · from preset:  aiolos mcp --preset nextjs")
            print(f"  · list catalog:  {sorted(mcp_mod.BY_SLUG)}")
            return
        print("Suggested based on this repo:\n")
        for s in suggestions:
            env_hint = f"  (env: {', '.join(s.env)})" if s.env else ""
            print(f"  • {s.slug:<12} {s.description}{env_hint}")
        print()
        print("Install with:  aiolos mcp " + " ".join(f"--server {s.slug}" for s in suggestions))
        return

    if args.clear:
        if not _assume_yes(args):
            if not confirm(f"Remove aiolos-managed MCP entries from {project}/.mcp.json?",
                           default=True):
                print("Cancelled.")
                return
        summary = mcp_mod.write_mcp_config(project, [], custom_servers=[], dry_run=args.dry_run)
        print(f"Cleared managed MCP entries from {summary['path']}")
        return

    slugs = list(dict.fromkeys(slugs))

    # Preview before writing.
    _print_header("MCP install plan")
    if slugs:
        print(f"  catalog servers:  {', '.join(slugs)}")
    if custom:
        print(f"  custom servers:   {', '.join(c.slug for c in custom)}")
    print(f"  writes:  {project}/.mcp.json  (env vars become ${{VAR}} placeholders)")
    print(f"           {project}/.env.claude.example  (template — copy to .env.claude)")

    if not _assume_yes(args):
        if not confirm("\nProceed?", default=True):
            print("Cancelled — nothing written.")
            return

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


# ── browse ────────────────────────────────────────────────────────────────────

# Curated starting points. Every author here is in the default trust list
# (see audit.DEFAULT_TRUSTED_AUTHORS). Users can still type in a custom source.
BROWSE_SOURCES: list[tuple[str, str]] = [
    ("anthropics/skills", "Anthropic's official skill collection"),
    ("obra/skills",       "Jesse Vincent's community-trusted skills"),
    ("vercel-labs/skills", "Vercel Labs skills"),
    ("skills.sh",         "Open community catalogue (skills.sh)"),
]


def cmd_browse(args: argparse.Namespace) -> None:
    """Interactive: pick a trusted source → list skills → pick → fetch → install."""
    lib = get_library()
    ensure_library(lib)
    ensure_trust_file(lib)
    project = Path(args.project).resolve()

    # 1) Source selection
    if args.source:
        source = args.source
    else:
        print("Pick a source to browse:\n")
        labels = [f"{slug:<22} {desc}" for slug, desc in BROWSE_SOURCES]
        labels.append("(type a custom owner/repo — e.g. your-org/skills)")
        pick = choose("Sources:", labels, default=1)
        if pick is None:
            print("Cancelled.")
            return
        if pick == len(labels):
            try:
                source = input("Custom source (owner/repo): ").strip()
            except EOFError:
                print("Cancelled.")
                return
            if not source:
                print("No source given — cancelled.")
                return
        else:
            source = BROWSE_SOURCES[pick - 1][0]

    # 2) Trust check
    if not is_trusted(source, lib):
        author = source_author(source)
        print(f"\n⚠  Author {author!r} is not on your trust allowlist ({lib}/trust.toml).")
        print("   Skills can run arbitrary shell commands — review before installing.")
        if not confirm("Continue anyway?", default=False, assume_yes=args.yes):
            print("Cancelled.")
            return

    # 3) List skills from the source via the existing fetch machinery
    print(f"\nListing skills from {source} …")
    try:
        from .library import fetch_from_skills_sh
        # `--list` mode prints to stdout; we don't currently parse the output.
        # The user sees the list, then we ask them which names to install.
        fetch_from_skills_sh(source=source, skill_names=[], library=lib, verbose=True)
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        print("\nIf `skills.sh` isn't installed, try:  npm install -g skills")
        print("Or configure your library with bootstrap.sh.")
        sys.exit(1)

    # 4) Ask which skills to fetch
    print("\nType skill names to fetch (comma-separated), or Enter to cancel.")
    print("  e.g. git, pytest, docker")
    try:
        raw = input("Skills: ").strip()
    except EOFError:
        raw = ""
    if not raw:
        print("Cancelled — nothing fetched.")
        return
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    if not wanted:
        print("Cancelled.")
        return

    # 5) Fetch into library
    print(f"\nAbout to fetch into your library ({lib}/skills/):")
    for s in wanted:
        print(f"  • {s}  (from {source})")
    if not confirm("\nProceed?", default=True, assume_yes=args.yes):
        print("Cancelled.")
        return

    from .library import fetch_from_skills_sh
    installed = fetch_from_skills_sh(
        source=source, skill_names=wanted, library=lib, verbose=True,
    )
    if not installed:
        print("\nNothing was fetched. Check the skill names and try again.")
        return

    # 6) Offer to install into this project
    print(f"\nFetched into library: {', '.join(installed)}")
    if not confirm(
        f"Install these {len(installed)} skill(s) into {project}/.claude/skills/?",
        default=True, assume_yes=args.yes,
    ):
        print("Done — skills live in your library and you can install them later with:")
        print(f"  aiolos install " + " ".join(f"--skill {s}" for s in installed))
        return

    summary = install_to_project(
        project_path=project, skills=installed, library=lib, verbose=True,
    )
    total = len(summary["skills_installed"])
    print(f"\n✓ Installed {total} skill(s) into {project}/.claude/skills/")


if __name__ == "__main__":
    main()
