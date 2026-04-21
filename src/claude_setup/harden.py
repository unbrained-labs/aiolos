"""Write a sane `.claude/settings.json` deny-rule baseline.

The goal: even if a skill has `allowed-tools: Bash(*)` or a clever command
substitution, the settings.json `permissions.deny` list refuses reads of
SSH keys, cloud credentials, keychains, and `.env*` by default.

`settings.json` is the layer Claude Code actually enforces today — frontmatter
`allowed-tools` is cosmetic on current Claude Code (anthropics/claude-code#18837).

Re-runs are idempotent: we merge into a managed block, never stomp user rules.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LOCK_FILENAME = "claude-setup.lock.json"
MANAGED_BLOCK_VERSION = 1

# --- Permission catalogues ---------------------------------------------------

# Always-on: blocks the things you almost certainly don't want the model to read.
# Bash matchers use prefix-space form, not colon: `Bash(cat ~/.ssh/**)` matches
# `cat ~/.ssh/id_rsa` at the start of the command string. Read() denies cover the
# Read tool. Note: blocklisting Bash is defense-in-depth only — cat/less/xxd/
# python -c/awk/sed can all read a file. For true isolation, enable the Claude
# Code sandbox. See README "Harden — caveats".
DEFAULT_DENY = [
    # Read tool denies — these actually enforce for the Read tool path.
    "Read(~/.ssh/**)",
    "Read(~/.aws/**)",
    "Read(~/.config/gcloud/**)",
    "Read(~/.azure/**)",
    "Read(~/.gnupg/**)",
    "Read(~/.netrc)",
    "Read(~/.kube/config)",
    "Read(.env)",
    "Read(.env.*)",

    # Bash denies — correct `Bash(<prefix> <args>)` form. Covers the common
    # reader commands; not exhaustive. If an attacker controls the command
    # string they'll reach for `less`, `python`, `awk`, etc. This is a speed
    # bump, not a fence.
    "Bash(cat ~/.ssh/**)",
    "Bash(cat ~/.aws/**)",
    "Bash(cat ~/.netrc)",
    "Bash(cat ~/.kube/config)",
    "Bash(cat .env)",
    "Bash(cat .env.*)",
    "Bash(less ~/.ssh/**)",
    "Bash(less .env*)",
    "Bash(head ~/.ssh/**)",
    "Bash(tail ~/.ssh/**)",

    # Keychains — command prefix matches.
    "Bash(security find-generic-password*)",
    "Bash(security find-internet-password*)",
    "Bash(security delete-generic-password*)",
    "Bash(security delete-internet-password*)",
    "Bash(secret-tool *)",
]

# Questionnaire-toggled additions.
BLOCK_DESTRUCTIVE = [
    "Bash(rm -rf /*)",
    "Bash(rm -rf ~*)",
    "Bash(rm -rf $HOME*)",
    "Bash(git push --force *)",
    "Bash(git push -f *)",
    "Bash(git reset --hard *)",
    "Bash(git clean -fdx*)",
]

BLOCK_CLOUD_CONTROL_PLANE = [
    "Bash(aws s3 rb *)",
    "Bash(aws iam delete-*)",
    "Bash(gcloud projects delete *)",
    "Bash(flyctl apps destroy *)",
    "Bash(fly apps destroy *)",
    "Bash(kubectl delete namespace *)",
    "Bash(kubectl delete ns *)",
    "Bash(terraform destroy *)",
    "Bash(neonctl projects delete *)",
    "Bash(neonctl databases delete *)",
]

BLOCK_PACKAGE_PUBLISH = [
    "Bash(npm publish *)",
    "Bash(pnpm publish *)",
    "Bash(yarn publish *)",
    "Bash(cargo publish *)",
    "Bash(twine upload *)",
    "Bash(gem push *)",
]

# --- Hook templates ----------------------------------------------------------

HOOK_LOG_TOOL_USE = {
    # The `Skill` matcher on PostToolUse isn't a real event in Claude Code's
    # hook spec (April 2026). The closest workable approximation is logging
    # every UserPromptSubmit + which tools fire in the turn; do that server-
    # side via the post-tool hook with a wildcard matcher and filter for
    # skill invocations in post-processing.
    "event": "PostToolUse",
    "matcher": ".*",
    "command": "jq -nc --arg t \"$(date -Iseconds)\" --arg tool \"$CLAUDE_TOOL_NAME\" '{ts:$t,tool:$tool}' >> .claude/tool-fires.jsonl 2>/dev/null || true",
    "why": "append-only log of every tool invocation; `claude-setup stats` filters for skill fires",
}

HOOK_DENY_ENV_WRITE = {
    "event": "PreToolUse",
    "matcher": "Write|Edit",
    "command": "case \"$CLAUDE_TOOL_INPUT\" in *\".env\"*|*\"secrets.\"*) echo 'refused: edits to env/secrets blocked by harden'; exit 2 ;; esac",
    "why": "hard-block Write/Edit to .env* and secrets.* even if a skill tries",
}

HOOK_CONFIRM_PRODUCTION = {
    "event": "PreToolUse",
    "matcher": "Bash",
    "command": "case \"$CLAUDE_TOOL_INPUT\" in *\"--prod\"*|*\"--production\"*|*\"apply -auto-approve\"*) echo 'production-scoped command — confirm first'; exit 2 ;; esac",
    "why": "pause on production-flagged commands so the user confirms",
}

HOOK_DING_ON_STOP = {
    "event": "Stop",
    "matcher": "",
    "command": (
        "command -v afplay >/dev/null && "
        "afplay /System/Library/Sounds/Glass.aiff >/dev/null 2>&1 || "
        "(command -v paplay >/dev/null && paplay /usr/share/sounds/freedesktop/stereo/complete.oga >/dev/null 2>&1) || "
        "printf '\\a'"
    ),
    "why": "plays a short sound when Claude finishes a turn — works on macOS (afplay), Linux (paplay), falls back to terminal bell",
}

AVAILABLE_HOOKS = {
    "log_tool_use": HOOK_LOG_TOOL_USE,
    "deny_env_write": HOOK_DENY_ENV_WRITE,
    "confirm_prod": HOOK_CONFIRM_PRODUCTION,
    "ding_on_stop": HOOK_DING_ON_STOP,
}


# --- Policy ------------------------------------------------------------------

@dataclass
class Policy:
    """Answers from the questionnaire — everything defaults to the safer option."""
    block_destructive: bool = True
    block_cloud_control_plane: bool = True
    block_package_publish: bool = False  # publishing is a legitimate task in some repos
    extra_deny: list[str] = field(default_factory=list)
    hooks: list[str] = field(default_factory=list)  # keys of AVAILABLE_HOOKS


def defaults() -> Policy:
    return Policy(
        block_destructive=True,
        block_cloud_control_plane=True,
        block_package_publish=False,
        hooks=["deny_env_write"],  # sensible minimum — user can opt into more
    )


def compile_deny_rules(policy: Policy) -> list[str]:
    rules = list(DEFAULT_DENY)
    if policy.block_destructive:
        rules.extend(BLOCK_DESTRUCTIVE)
    if policy.block_cloud_control_plane:
        rules.extend(BLOCK_CLOUD_CONTROL_PLANE)
    if policy.block_package_publish:
        rules.extend(BLOCK_PACKAGE_PUBLISH)
    rules.extend(policy.extra_deny)
    # dedupe, keep order
    return list(dict.fromkeys(rules))


def compile_hooks(policy: Policy) -> list[dict]:
    return [
        {"event": h["event"], "matcher": h["matcher"], "command": h["command"]}
        for key in policy.hooks
        for h in [AVAILABLE_HOOKS[key]]
        if key in AVAILABLE_HOOKS
    ]


# --- settings.json writer ----------------------------------------------------

def _tag_hook(h: dict) -> dict:
    """Attach a private `comment` marker so we can recognise our hooks on
    re-runs without polluting settings.json with a vendor-specific top-level
    key (Anthropic adds top-level keys over time; we don't want to collide)."""
    return {**h, "comment": "managed-by:claude-setup"}


def write_settings(project_path: Path, policy: Policy) -> dict:
    """Write `.claude/settings.json` with managed deny + hook blocks.

    Preserves user-authored rules (anything we didn't install). The marker of
    "what we installed last time" lives in a sidecar `.claude/claude-setup.lock.json`,
    not in settings.json itself.
    """
    claude_dir = project_path / ".claude"
    settings_path = claude_dir / "settings.json"
    lock_path = claude_dir / LOCK_FILENAME
    claude_dir.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            raise RuntimeError(
                f"{settings_path} exists but is not valid JSON; refusing to overwrite."
            )

    prior: dict = {}
    if lock_path.exists():
        try:
            prior = json.loads(lock_path.read_text())
        except json.JSONDecodeError:
            prior = {}

    prior_deny = set(prior.get("deny", []))
    prior_hook_sigs = set(prior.get("hook_sigs", []))
    was_managed = bool(prior)

    # Merge permissions.deny — drop rules we previously installed, keep user's.
    permissions = existing.get("permissions") or {}
    existing_deny = list(permissions.get("deny") or [])
    user_deny = [r for r in existing_deny if r not in prior_deny]
    new_deny = compile_deny_rules(policy)
    permissions["deny"] = list(dict.fromkeys(user_deny + new_deny))
    existing["permissions"] = permissions

    # Merge hooks. Detect managed hooks by the `comment` marker on the object.
    existing_hooks = list(existing.get("hooks") or [])
    user_hooks = [
        h for h in existing_hooks
        if not (isinstance(h, dict) and h.get("comment") == "managed-by:claude-setup")
    ]
    new_hooks = [_tag_hook(h) for h in compile_hooks(policy)]
    existing["hooks"] = user_hooks + new_hooks

    settings_path.write_text(json.dumps(existing, indent=2) + "\n")

    # Sidecar lock records what we installed so the next run can undo it cleanly.
    lock_path.write_text(json.dumps({
        "version": MANAGED_BLOCK_VERSION,
        "deny": new_deny,
        "hook_sigs": [f"{h['event']}:{h['matcher']}" for h in new_hooks],
        "policy": {
            "block_destructive": policy.block_destructive,
            "block_cloud_control_plane": policy.block_cloud_control_plane,
            "block_package_publish": policy.block_package_publish,
            "extra_deny": policy.extra_deny,
            "hooks": policy.hooks,
        },
    }, indent=2) + "\n")

    return {
        "path": str(settings_path),
        "lock_path": str(lock_path),
        "existed": was_managed,
        "deny_rules": len(new_deny),
        "hooks": len(new_hooks),
    }


# --- Questionnaire (interactive) ---------------------------------------------

def _ask(prompt: str, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            resp = input(prompt + suffix).strip().lower()
        except EOFError:
            return default
        if not resp:
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False


def run_questionnaire() -> Policy:
    print("\nWe will write a managed block in .claude/settings.json.")
    print("Secrets (~/.ssh, ~/.aws, ~/.gnupg, keychain, .env*) are always denied.\n")
    block_destructive = _ask(
        "Also block destructive commands (rm -rf /, git push --force, git reset --hard)?",
        True,
    )
    block_cloud = _ask(
        "Block cloud control-plane deletes (aws iam delete, kubectl delete ns, terraform destroy)?",
        True,
    )
    block_publish = _ask(
        "Block package publishing (npm publish, cargo publish, twine upload)?",
        False,
    )
    print("\nHooks (Claude Code executes these on tool events):")
    hooks: list[str] = []
    if _ask("Install hook: hard-block Write/Edit to .env* and secrets.*?", True):
        hooks.append("deny_env_write")
    if _ask("Install hook: log every tool invocation to .claude/tool-fires.jsonl (for stats)?", True):
        hooks.append("log_tool_use")
    if _ask("Install hook: pause on --prod/--production/apply -auto-approve commands?", True):
        hooks.append("confirm_prod")
    if _ask("Install hook: play a short sound when Claude finishes a turn (macOS Glass / paplay / bell)?", False):
        hooks.append("ding_on_stop")

    return Policy(
        block_destructive=block_destructive,
        block_cloud_control_plane=block_cloud,
        block_package_publish=block_publish,
        hooks=hooks,
    )
