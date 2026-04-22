<!-- prettier-ignore -->
---
name: setup
description: ALWAYS invoke this skill when the user opens an unconfigured project and says "set up Claude", "configure skills", or runs /setup. Detects the stack, enables matching Claude Code built-in agents, fetches real skills from trusted sources on the user's trust.toml allowlist — and does nothing more. Does NOT ship skill content.
disable-model-invocation: false
allowed-tools: Bash(aiolos:*) Bash(command -v:*) Bash(cat:*) Bash(ls:*) Bash(find:*) Read Write
---

# /setup — Project configurator

This skill delegates to the `aiolos` CLI.

aiolos does not ship skill or agent content. It provides:

1. **Stack detection** — which Claude Code built-in agents fit this repo.
2. **Fetch routing** — pulls real skills from trusted authors (trust.toml
   allowlist) into the user's `~/.claude-library`, then into `.claude/`.
3. **Harden** — writes a `permissions.deny` baseline + optional hooks.
4. **Tool wrapping** — scans the user's authenticated CLIs and scaffolds
   wrappers the user can author themselves.

Never fabricate skill content inside this skill.

---

## Step 1 — Preflight

```bash
command -v aiolos >/dev/null || echo "NOT_INSTALLED"
```

If `NOT_INSTALLED`, tell the user to run `uv tool install aiolos`
(or `pip install --user aiolos`). Stop.

---

## Step 2 — One-shot init

```bash
aiolos init --project . --json
```

The JSON describes:
- `selected` — matched preset(s).
- `fallback_used` — true if no preset matched and `base` was used.
- `ambiguous` — true if a weak match tied.
- `installed.agents_builtin` — Claude Code built-in agents activated.
- `installed.skills_installed` — skills installed into `.claude/`.
- `installed.fetched` / `fetch_errors` — skills pulled from trusted sources.

If `ambiguous` is true, ask the user which preset to pick. Otherwise
report the summary and stop.

---

## Step 3 — Summarise

```
── Setup complete ────────────────────────────────────

Preset(s)     : <selected>
Built-ins     : <agents_builtin>   (ship with Claude Code)
Skills        : <skills_installed> (in ~/.claude-library)
Fetched       : <fetched>          (from trusted sources)

Next:
  • /skills                 verify what's active
  • aiolos harden     baseline deny rules + hooks
  • aiolos tools      wrap your authenticated CLIs
```

---

## Hard rules

- Never invent skill content. If the user wants a new skill, use
  `aiolos new-skill` to scaffold — it enforces the SKILL.md
  conventions and prompts for a real imperative description.
- Never claim auditor / specialist expertise. Refer the user to the
  canonical source (Trail of Bits, Spearbit, the protocol's own team).
- Fetch only from trust.toml allowlist. If the user wants a source
  added, have them edit trust.toml, not bypass the check.
- Don't duplicate Claude Code built-ins. Built-in agents are referenced
  by name in presets and activate on their own descriptions.
