# Roadmap

Living doc. Things are on here either because they filled a real gap research
turned up, or because using the tool produced a specific ask. Anything not on
here either shipped, is dead, or doesn't have a source.

## Shipped in v0.1

- `init` — detect → activate matching built-in agents → fetch trusted skills.
- `mcp` — write `.mcp.json` with `${VAR}` placeholders + `.env.claude.example` + `.gitignore`.
- `harden` — `permissions.deny` baseline in `settings.json` + optional hooks (env-write block, prod pause, tool-use log, stop-ding).
- `tools` — scan PATH for authenticated CLIs + scaffold wrapper skills.
- `new-skill`, `new-agent`, `lint`, `doctor`, `audit`, `trust.toml`.
- `wizard` — 5 gated steps (init → browse → harden → mcp → tools), each shown-then-confirmed.
- `browse` — interactive picker over trusted sources (anthropics/skills, obra, vercel-labs, skills.sh, custom repos) → fetch → install.
- Consent model — every writer prints a plan and prompts `[Y/n]` before touching disk. `--yes` / `--json` / `--dry-run` skip.
- Monorepo-additive preset detection.
- `/setup` Claude Code skill that delegates to the CLI.
- Static site at [unbrained-labs.github.io/aiolos](https://unbrained-labs.github.io/aiolos/).

## Next

### Self-improving — `stats` subcommand · S

Parse `.claude/tool-fires.jsonl` (written by the `log_tool_use` hook) and show
per-skill invocation counts + descriptions that never fire. Answers the
widely-cited "is my SKILL.md even being triggered?" question. Mechanical,
small, useful.

### Sharing — `extract --as-preset` · S

Take a repo's current `.claude/` + `.mcp.json` + `settings.json` managed
blocks and emit a preset TOML. Turns a team's hand-configured repo into a
shareable recipe in one command. Combined with a future plugin emitter,
this is the viral loop.

### Validation — `verify` · M

Parse the effective merged settings (user + project + local) and warn where
a configured deny rule is known to not enforce (e.g. Bash pipelines on older
Claude Code versions — see anthropics/claude-code#27040 and #18846). Ships a
hook-based backstop snippet. Extension of `doctor`, not a new surface.

### Sound-hooks templates · S

Today `harden` supports one "ding on stop" hook. Add a curated set of Stop /
PreToolUse / PostToolUse sound templates — completion chime, error buzz,
production-command warning, long-task stinger. Opt-in, listed in
`aiolos harden` questionnaire.

### Plugin emit — `publish` · M

Anthropic's plugin system is the native distribution channel. Emit a
`.claude-plugin/plugin.json` manifest from any `.claude/` so the result is
publishable to plugin marketplaces directly. Unblocks teams that want to
distribute their aiolos-generated config beyond their own repo.

### Shared-org presets — remote `extends` · M

Support `extends = ["github:acme/claude-presets"]` to inherit from a remote
preset repo, with a lockfile for reproducibility. Direct answer to the
"teams want one canonical Claude config" ask.

## Maybe

- **Cursor / Codex bridge** — `rulesync` already solves cross-tool sync
  well. Revisit only if users ask.
- **Skill invocation telemetry dashboard** — requires a local TUI; probably
  too much for `stats` alone. Wait until `stats` feedback says otherwise.
- **Company-wide trust.toml distribution** — pair with org presets.

## Not doing

- Shipping skill or agent content. See `AGENTS.md`.
- Duplicating Claude Code's built-in agents.
- Reinventing `rulesync` / `npx skills`.
- Any form of telemetry that leaves the user's machine.

## Open questions (for review)

1. **Scope of `verify`.** Is it worth parsing the effective merged settings
   ourselves, or should we shell out to `claude --dry-run` (if that becomes
   a thing)? The first is simpler now; the second is more correct long-term.
2. **Preset format evolution.** Keeping presets TOML is nice but the
   `[[mcp_custom]]` pattern shows they're drifting toward a richer schema.
   Worth defining a `presetSchemaVersion` field now, or YAGNI?
3. **Naming collision.** Package is `aiolos`, brand is `aiolos`.
   The split works but it's one more thing to explain. If Anthropic ever
   renames Claude Code, `aiolos` becomes a liability — do we rename
   the package one day?
4. **How opinionated should `harden --defaults` be?** Current defaults
   block destructive commands + cloud-delete APIs. Cautious users want
   package-publish blocked too; prolific users want none of it. Is a
   "caution level" flag (`--level=lax|safe|paranoid`) worth it?
