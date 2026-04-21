# aiolos

> ἀίολος — *keeper of the winds*. In the Odyssey, Aiolos hands Odysseus a
> bag of winds to route his voyage home. This tool routes agents, skills,
> MCP servers, and harden rules into a Claude Code repo.

[![site](https://img.shields.io/badge/site-unbrained--labs.github.io%2Faiolos-141210?style=flat-square)](https://unbrained-labs.github.io/aiolos/)
[![license](https://img.shields.io/badge/license-MIT-141210?style=flat-square)](./LICENSE)
[![python](https://img.shields.io/badge/python-3.11%2B-141210?style=flat-square)](./pyproject.toml)

**Installs as `claude-setup`. Routes projects to the right skills. Does not ship skill content.**

One command, zero prompts, per-project `.claude/` wired up with:

- Claude Code's **built-in agents** that fit the detected stack (nothing to install; they ship with Claude Code).
- **Community skills** fetched from authors you trust (allowlist in `trust.toml`, default: anthropics, vercel-labs, obra, trail-of-bits, microsoft).
- **MCP servers** wired into `.mcp.json` with `${VAR}` placeholders — secrets stay in a gitignored `.env.claude`, never in git.
- Your **authenticated CLIs** (`gh`, `flyctl`, `neonctl`, …) wrapped as skills you author yourself via a scaffolder that enforces best practice.
- A **`permissions.deny` baseline** in `.claude/settings.json` protecting secrets (SSH, AWS, keychain, `.env*`).

The tool ships **zero skill markdown and zero agent markdown**. It's plumbing.

---

## Quick start

```bash
git clone https://github.com/unbrained-labs/aiolos
cd aiolos
uv tool install .          # or: pip install --user .
./bootstrap.sh             # seeds ~/.claude-library/presets/

cd any-project
claude-setup wizard        # one-shot: init + mcp + harden + tools
```

Or step-by-step:

```bash
claude-setup init          # detect stack + activate matching built-in agents
claude-setup mcp           # write .mcp.json with ${VAR} placeholders
claude-setup harden        # permissions.deny baseline in settings.json
claude-setup tools         # scan authenticated CLIs, scaffold wrappers
```

From inside Claude Code: `/setup`.

---

## What a preset is

A preset is a TOML recipe:

```toml
# ~/.claude-library/presets/nextjs.toml
extends = ["base"]

# Built-in Claude Code agents that fit this stack. We don't install them;
# they ship with Claude Code. Listing them here is a pointer.
agents = [
  "nextjs-developer",
  "frontend-developer",
  "typescript-pro",
]

# Skills to fetch from trusted authors. Format: owner/repo#skill-name.
# Only authors on trust.toml will fetch without --yes-unknown-author.
fetch = [
  # "vercel-labs/agent-skills#frontend-design",
  # "anthropics/skills#file-creator",
]

# User-authored skills already in the library (left empty by default).
skills = []

[detect]
files            = ["next.config.js", "next.config.mjs", "next.config.ts"]
package_json_has = ["next"]
```

Detect clauses (any match = score +1; score ≥ 1 = installed):

| clause | checks |
|---|---|
| `files` | any listed path exists |
| `any_files` | any glob matches |
| `all_files` | every glob matches |
| `package_json_has` | any dep in `package.json` |
| `pyproject_has` | any dep in `pyproject.toml` (PEP 621 or Poetry) |
| `contains` | `[{ file = "x", text = "y" }]` |

Monorepos: if more than one preset matches, **all** of them install (deduped).

---

## The authoring scaffolder

If you want to write a skill — your own, or a wrapper around a CLI you have installed — use `new-skill`. It enforces Anthropic's current conventions (single-line imperative description, `<!-- prettier-ignore -->`, progressive disclosure).

```bash
claude-setup new-skill                 # interactive: name, description, wraps?
claude-setup new-skill gh/ops \
  --description "ALWAYS invoke when the user mentions PRs, issues, releases." \
  --wraps gh
claude-setup lint ~/.claude-library/skills/gh/ops/SKILL.md
```

It does not write the skill's body. That's still your job.

---

## `claude-setup tools` — wrap your authenticated CLIs

Scans `PATH` for ~25 productivity CLIs (gh, flyctl, neonctl, wrangler, docker, kubectl, terraform, stripe, fal, aws, gcloud…) and tells you:

1. Installed but no wrapper skill — scaffold one with `--scaffold-all`.
2. Suggested by this repo but not installed — here's how to install it.
3. Wrappers already available in your library.

The scaffolder's `--wraps <cli>` template is a preflight-and-cheat-sheet layout — you fill in the specifics; we don't pretend to know your workflow.

---

## `claude-setup harden` — the deny-rule baseline

Writes a managed block in `.claude/settings.json`. **Defense in depth, not isolation** — for true isolation, enable Claude Code's sandbox.

Always-on denies (Read tool):
- `~/.ssh/`, `~/.aws/`, `~/.config/gcloud/`, `~/.azure/`, `~/.gnupg/`, `~/.netrc`, `~/.kube/config`, `.env`, `.env.*`

Always-on denies (Bash tool — covers the common readers: cat/less/head/tail of those same paths, plus `security` keychain commands and `secret-tool`).

Questionnaire adds (sensible defaults):
- Destructive commands (`rm -rf /`, `git push --force`, `git reset --hard`)
- Cloud control-plane deletes (`aws iam delete`, `terraform destroy`, `flyctl apps destroy`, `neonctl projects delete`)
- Package publishes (optional, off by default)

Hooks:
- `deny_env_write` — hard-block Write/Edit on `.env*` / `secrets.*`
- `confirm_prod` — pause on `--prod` / `--production` / `apply -auto-approve`
- `log_tool_use` — append every tool invocation to `.claude/tool-fires.jsonl`
- `ding_on_stop` — short sound when Claude finishes a turn

Managed state lives in a sidecar `.claude/claude-setup.lock.json`, not in `settings.json` itself — safer against Anthropic's schema evolution.

```bash
claude-setup harden                 # interactive
claude-setup harden --defaults      # non-interactive safe baseline
```

Re-runs merge cleanly; user rules outside our managed block are preserved.

---

## Trust allowlist

`~/.claude-library/trust.toml` lists GitHub org/user slugs whose skills you're willing to install without a warning. Edit it. Claude-setup doesn't pick your trust.

```toml
authors = [
  "anthropics",
  "vercel-labs",
  "obra",
  "trail-of-bits",
  "microsoft",
]
```

`claude-setup fetch <owner/repo>` warns and requires `--yes-unknown-author` for anything off the list.

---

## What this tool does NOT do

- **Does not author skills on your behalf.** When you run `new-skill`, we write the scaffold; you write the body.
- **Does not ship security expertise.** Claude Code has a built-in `security-auditor` agent; use it for first-pass review. For anything shipping to users, commission a real audit from real humans (Trail of Bits, Spearbit, Pashov, Code4rena).
- **Does not duplicate Anthropic's built-in agents.** Presets reference `code-reviewer`, `python-pro`, `frontend-developer`, etc. by name. Those ship with Claude Code.
- **Does not enforce `allowed-tools` itself.** That's Claude Code's job via `settings.json`. `harden` writes the settings; `allowed-tools` in skill frontmatter is advisory on current versions.

---

## Git safety

| Context | Default link mode | Why |
|---|---|---|
| Inside a git repo | **copy** | Committed `.claude/` is portable for teammates |
| Outside a git repo | **symlink** | Library edits propagate immediately |

Override with `--copy` or `--symlink`. Forcing `--symlink` inside a git repo writes/updates a managed block in `.claude/.gitignore`.

---

## CLI reference

```
claude-setup wizard    [--project PATH] [--noninteractive]
claude-setup init      [--project PATH] [--json] [--copy | --symlink]
                       [--overwrite] [--dry-run] [--force]
claude-setup install   [--preset P ...] [--skill S ...] [--agent A ...]
                       [--project PATH] [--copy | --symlink] [--overwrite] [--dry-run]
claude-setup detect    [--project PATH] [--json] [--install] [--dry-run]
claude-setup remove    [--skill S ...] [--agent A ...] [--project PATH]
claude-setup list      [skills|agents|presets|all]
claude-setup fetch     SOURCE [--skill S ...] [--list] [--yes-unknown-author]

claude-setup tools     [--project PATH] [--json] [--scaffold-all]
claude-setup harden    [--project PATH] [--defaults]
claude-setup audit     [PATH]
claude-setup new-skill [NAME] [-d "…"] [--allowed-tools "…"] [--wraps CLI] [--overwrite]
claude-setup new-agent NAME -d "…" [--role ROLE] [--model sonnet|opus|haiku] [--overwrite]
claude-setup lint      [PATH]
claude-setup doctor
```

---

## Environment

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_LIBRARY` | `~/.claude-library` | Override library location |
| `CLAUDE_SETUP_NO_SOUND` | unset | Silence wizard audio |

---

## Development

```bash
pip install -e .
PYTHONPATH=src python -m pytest tests -q
```
