# AGENTS.md

Instructions for any AI coding assistant (Claude Code, Codex, Cursor, Copilot,
Cody) working in this repository. Humans, read this too — it's the short
version of the design rules.

## What this project is

**aiolos** (installs as `aiolos`). A CLI that configures `.claude/`
directories for Claude Code projects. Stack detection, preset-driven install,
MCP wiring with env-var placeholders, settings.json hardening, CLI-wrapper
scaffolder. Plumbing only.

## The one rule that matters

**This tool ships no skill or agent content.** Do not add files under
`skills/` or `agents/`. If you're tempted to write a SKILL.md that contains
expertise on a topic (Solidity, pentesting, etc.), stop — we route users to
real authors' work via `trust.toml` + `fetch`. The tool is plumbing; the
content belongs to Anthropic's built-ins and the community authors the user
explicitly trusts.

Every time I violated this rule during development, the result was worse
than silence. See the commit history if you want the scars.

## Orientation

- **Python source**: `src/aiolos/` — the installable package.
- **Presets**: `presets/*.toml` — declarative recipes (detect rules +
  built-in agent names + optional fetch/MCP lists). No code.
- **The `/setup` skill**: `skill/setup/SKILL.md` — the only SKILL.md that
  lives in this repo; it delegates to the CLI.
- **Site**: `site/` — single-page landing at
  [unbrained-labs.github.io/aiolos](https://unbrained-labs.github.io/aiolos/).
  Static HTML + CSS + one JS file. No framework, no build step.
- **Tests**: `tests/` — pytest, 60+ cases. Green before you commit.

## Running things

```bash
pip install -e .
PYTHONPATH=src python -m pytest tests -q        # full suite
PYTHONPATH=src python -m aiolos.cli ...    # run the CLI locally
cd site && python -m http.server 8765            # serve the site
```

## Code conventions

- Python 3.11+. Type annotations on every public function. `from __future__
  import annotations` in new modules.
- `pathlib.Path` everywhere, never `os.path`.
- stdlib only. No new runtime dependencies without a very good reason —
  `tomllib`, `shutil`, `subprocess`, `json`, `pathlib` cover the tool.
- One short comment for the *why* — avoid the "what." Well-named
  functions are the "what."
- Test every new subcommand end-to-end via
  `subprocess.run([sys.executable, "-m", "aiolos.cli", ...])`.
- Presets are data. If you need logic in a preset, the wrong design won.

## Principles, in priority order

1. **No fabricated expertise.** If you don't have a primary source for a
   security claim, a framework convention, or a best practice, don't
   write it as authoritative. Point at the canonical source.
2. **Plumbing, not content.** New features should move data around
   (detect, fetch, write, merge) — not opine on what the data should be.
3. **Idempotent and reversible.** Every write has a managed marker + a
   sidecar lock so re-runs merge cleanly and `--clear` undoes cleanly.
4. **No secrets in git.** `.mcp.json` gets `${VAR}` placeholders;
   `.env.claude` is always in `.gitignore`; `settings.json` harden rules
   cover the usual footguns.
5. **Preserve user edits.** Anything outside a managed block is theirs.
   Refuse to overwrite JSON we can't parse; tell the user to fix it.
6. **Read the bug tracker before adding safety features.** Claude Code's
   permission and hook systems have known gaps (deny-rules partially
   ignored, some matchers cosmetic). Don't promise enforcement we don't
   have; document the actual state.

## Things reviewers will push back on

- A new skill or agent markdown file checked into this repo. Delete it.
- Dependencies added to `pyproject.toml` without justification.
- Any claim of specialist knowledge (web3 audit, pentest, medical, legal,
  ML infra) written in markdown. Reference the expert; don't impersonate.
- A subcommand that isn't idempotent.
- Writing to `~/.claude/` or `.claude/` outside a documented managed
  block.
- A test that doesn't run in under a second or that depends on network.

## Commit style

Present tense, imperative. One concept per commit. Explain *why* in the
body when the diff alone doesn't. Trailer lines for co-authorship.

## If you get stuck

The three files that explain the architecture fastest:
`src/aiolos/cli.py`, `src/aiolos/installer.py`,
`src/aiolos/detect.py`. Read them top to bottom before proposing a
change to the preset loader or the harden writer.
