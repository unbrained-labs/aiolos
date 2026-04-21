# Contributing to aiolos

Thanks for looking. A few things before you open a PR.

## Read AGENTS.md first

[AGENTS.md](./AGENTS.md) is the source of truth for design rules, what belongs
in this repo, and what reviewers will push back on. Read it; it's short.

The single rule that matters: **this tool ships no skill or agent content.**
If you're tempted to author a SKILL.md about a domain, don't. Route users to
the real author via `trust.toml` + `fetch` instead.

## Local setup

```bash
pip install -e .
PYTHONPATH=src python -m pytest tests -q          # ~60 tests, must be green
PYTHONPATH=src python -m claude_setup.cli --help  # try the CLI locally
cd site && python -m http.server 8765             # preview the site
```

Python 3.11+ only. stdlib-first — adding a runtime dependency needs a real
argument.

## Branching & commits

- Branch off `main`: `git checkout -b <kind>/<short-slug>`
  (`kind` ∈ `feat` · `fix` · `docs` · `chore` · `ci`).
- One concept per commit, imperative mood, present tense.
- Explain the *why* in the commit body when the diff alone doesn't make it
  obvious. PR description is not a substitute for a clear history.

## PR expectations

- Tests for every new subcommand — prefer end-to-end via
  `subprocess.run([sys.executable, "-m", "claude_setup.cli", ...])`.
- Docs updated: README for user-facing changes, AGENTS.md if you change a
  principle, CONTRIBUTING.md if you change the workflow.
- Dry-run mode if your change writes files.
- Idempotent. Re-running your command should not duplicate or corrupt.
- Managed block + sidecar lock if you write JSON the user might also edit.

## Reviewing

If you're reviewing, push back on:

- Any claim of specialist expertise written as markdown inside this repo.
- Any non-idempotent writer.
- Any command that can destroy data without `--dry-run` + an explicit flag.
- Any new top-level key stashed in `settings.json` (use sidecars — see
  `.claude/claude-setup.lock.json` pattern).
- Fabricated MCP-server metadata. Only `modelcontextprotocol/servers`
  canonical entries are pre-catalogued; everything else is user-defined
  via `[[mcp_custom]]` in a preset.

## Issues

Good issues include: the exact command you ran, the version of
claude-setup and Claude Code, the OS, and the expected vs actual output.
"It doesn't work" gets closed.
