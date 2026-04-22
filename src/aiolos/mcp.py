"""MCP server configuration — the plumbing layer.

What this does:
    * Detects which MCP servers fit a repo's stack.
    * Writes a committed `.mcp.json` that references env vars (never literal
      secrets).
    * Writes a committed `.env.claude.example` listing the env keys the
      `.mcp.json` references.
    * Adds `.env.claude` (the real dotenv, untracked) to `.gitignore`.
    * Keeps the managed state in a sidecar `.claude/aiolos.mcp.lock.json`
      so re-runs merge cleanly and user-authored servers are preserved.

What this does NOT do:
    * Ship fabricated MCP-server metadata. The catalog below only lists
      servers from the canonical modelcontextprotocol/servers repo — i.e.
      servers whose existence and package shape are published. Vendor-
      specific servers (Sentry, Stripe, Linear, etc.) are not pre-listed;
      users add them to their own presets after confirming the package.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

LOCK_FILENAME = "aiolos.mcp.lock.json"
MANAGED_MARKER = "managed-by:aiolos"
GITIGNORE_TARGETS = [".env.claude"]


@dataclass(frozen=True)
class McpServer:
    """One MCP server entry. Only the canonical modelcontextprotocol/servers
    entries live here. Everything else, the user adds to their own trust.toml
    + preset; we don't make up metadata."""
    slug: str
    description: str
    command: str
    args: list[str]
    env: list[str] = field(default_factory=list)
    detect_files: tuple[str, ...] = ()
    detect_pj_deps: tuple[str, ...] = ()
    detect_py_deps: tuple[str, ...] = ()
    source: str = "modelcontextprotocol/servers"  # the GitHub slug of the authoring org
    notes: str = ""


# Conservative catalog. Package names match the upstream monorepo at
# https://github.com/modelcontextprotocol/servers. Env-var names are
# placeholders the user fills in via `.env.claude`.
CATALOG: list[McpServer] = [
    McpServer(
        slug="filesystem",
        description="Read/write files under an allowlisted directory.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "${FILESYSTEM_ROOT}"],
        env=["FILESYSTEM_ROOT"],
        notes="FILESYSTEM_ROOT is the absolute path the server is allowed to touch.",
    ),
    McpServer(
        slug="git",
        description="git operations scoped to the repository root.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-git", "--repository", "${GIT_REPO_PATH}"],
        env=["GIT_REPO_PATH"],
    ),
    McpServer(
        slug="github",
        description="GitHub API — PRs, issues, search. Requires a PAT.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        detect_files=(".github/",),
        notes="Scope the token to least-privileged access.",
    ),
    McpServer(
        slug="postgres",
        description="Read-only Postgres query + schema introspection.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres", "${DATABASE_URL}"],
        env=["DATABASE_URL"],
        detect_pj_deps=("pg", "postgres", "@neondatabase/serverless"),
        detect_py_deps=("psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "sqlalchemy"),
    ),
    McpServer(
        slug="sqlite",
        description="SQLite query/introspection.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sqlite", "--db-path", "${SQLITE_PATH}"],
        env=["SQLITE_PATH"],
        detect_pj_deps=("better-sqlite3", "sqlite3"),
    ),
    McpServer(
        slug="fetch",
        description="Fetch arbitrary URLs. Useful but high-risk — scope carefully.",
        command="uvx",
        args=["mcp-server-fetch"],
    ),
    McpServer(
        slug="memory",
        description="In-memory knowledge graph that persists across the session.",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
    ),
    McpServer(
        slug="time",
        description="Timezone-aware time + conversion.",
        command="uvx",
        args=["mcp-server-time"],
    ),
]

BY_SLUG = {s.slug: s for s in CATALOG}


# ── Detection ────────────────────────────────────────────────────────────────

def _package_json_deps(project: Path) -> set[str]:
    pj = project / "package.json"
    if not pj.exists():
        return set()
    try:
        data = json.loads(pj.read_text())
    except Exception:
        return set()
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        deps.update((data.get(key) or {}).keys())
    return deps


def _pyproject_deps(project: Path) -> set[str]:
    text_path = project / "pyproject.toml"
    if not text_path.exists():
        return set()
    try:
        import tomllib
        data = tomllib.loads(text_path.read_text())
    except Exception:
        return set()
    deps: set[str] = set()
    for spec in (data.get("project", {}) or {}).get("dependencies", []) or []:
        deps.add(spec.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].split("<", 1)[0].strip().lower())
    return {d for d in deps if d}


def suggest(project: Path) -> list[McpServer]:
    """Return MCP servers whose detect rules match this repo."""
    pj_deps = {d.lower() for d in _package_json_deps(project)}
    py_deps = {d.lower() for d in _pyproject_deps(project)}
    out: list[McpServer] = []
    for server in CATALOG:
        if any((project / m).exists() for m in server.detect_files):
            out.append(server)
            continue
        if any(d.lower() in pj_deps for d in server.detect_pj_deps):
            out.append(server)
            continue
        if any(d.lower() in py_deps for d in server.detect_py_deps):
            out.append(server)
    return out


# ── Writer ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        raise RuntimeError(f"{path} exists but is not valid JSON; refusing to overwrite.")


def _render_server(server: McpServer) -> dict:
    entry: dict = {
        "command": server.command,
        "args": list(server.args),
    }
    if server.env:
        # env values are ${VAR_NAME} placeholders; Claude Code expands them
        # at launch time from the process env (users source .env.claude first).
        entry["env"] = {key: f"${{{key}}}" for key in server.env}
    entry["comment"] = MANAGED_MARKER
    return entry


def _merge_env_example(existing: str, keys: list[tuple[str, str]]) -> str:
    """Append missing keys to the existing .env.claude.example, preserving
    anything the user already wrote."""
    lines = existing.splitlines()
    have = {
        line.split("=", 1)[0].strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#") and "=" in line
    }
    additions: list[str] = []
    for key, comment in keys:
        if key in have:
            continue
        if comment:
            additions.append(f"# {comment}")
        additions.append(f"{key}=")
    if not additions:
        return existing if existing.endswith("\n") else existing + "\n"
    header = existing.rstrip() + "\n\n" if existing.strip() else ""
    return header + "\n".join(additions) + "\n"


def _append_gitignore(project: Path, targets: list[str]) -> bool:
    gi = project / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    missing = [t for t in targets if t not in existing.splitlines()]
    if not missing:
        return False
    block = "\n# added by aiolos — local Claude Code env\n" + "\n".join(missing) + "\n"
    gi.write_text(existing.rstrip() + "\n" + block if existing.strip() else block.lstrip())
    return True


def server_from_dict(data: dict) -> McpServer:
    """Build an McpServer from a user-defined preset entry.

    Presets can embed custom servers inline — useful for vendor-specific
    or private servers not in our catalog:

        [[mcp_custom]]
        slug = "pencil"
        description = "Pencil.dev — open-source Figma alternative"
        command = "npx"
        args = ["-y", "@pencil-dev/mcp"]
        env = ["PENCIL_API_KEY"]

    We don't vet these — the user is stating they trust this source.
    """
    required = {"slug", "command"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"mcp_custom entry missing required field(s): {sorted(missing)}")
    return McpServer(
        slug=data["slug"],
        description=data.get("description", ""),
        command=data["command"],
        args=list(data.get("args") or []),
        env=list(data.get("env") or []),
        detect_files=tuple(data.get("detect_files") or ()),
        detect_pj_deps=tuple(data.get("detect_pj_deps") or ()),
        detect_py_deps=tuple(data.get("detect_py_deps") or ()),
        source=data.get("source", "user-defined"),
        notes=data.get("notes", ""),
    )


def write_mcp_config(
    project_path: Path,
    server_slugs: list[str],
    custom_servers: list[McpServer] | None = None,
    dry_run: bool = False,
) -> dict:
    """Install the listed MCP servers into the repo.

    - `server_slugs`: names from the built-in CATALOG.
    - `custom_servers`: user-defined servers (from a preset's `mcp_custom`).
    - Merges into existing `.mcp.json` (user-authored entries preserved; any
      entry carrying the managed marker is rewritten).
    - Writes/updates `.env.claude.example` with the env vars those servers need.
    - Appends `.env.claude` to `.gitignore` if not already present.
    - Stores what we installed in a sidecar lock so the next run can diff.
    """
    custom_servers = list(custom_servers or [])
    unknown = [s for s in server_slugs if s not in BY_SLUG]
    if unknown:
        raise ValueError(
            f"Unknown MCP servers: {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(BY_SLUG))}. "
            "Add vendor servers as [[mcp_custom]] entries in a preset."
        )

    all_servers: list[McpServer] = [BY_SLUG[s] for s in server_slugs] + custom_servers
    # Dedup on slug — user's custom entry wins over catalog.
    by_slug: dict[str, McpServer] = {}
    for s in all_servers:
        by_slug[s.slug] = s
    effective_slugs = list(by_slug.keys())

    mcp_path = project_path / ".mcp.json"
    env_example = project_path / ".env.claude.example"
    lock_path = project_path / ".claude" / LOCK_FILENAME

    if dry_run:
        return {
            "path": str(mcp_path),
            "would_install": server_slugs,
            "env_keys": sorted({k for s in server_slugs for k in BY_SLUG[s].env}),
            "dry_run": True,
        }

    existing_mcp = _load_json(mcp_path) if mcp_path.exists() else {}
    mcp_servers = existing_mcp.get("mcpServers") or {}

    # Preserve any user-authored entry (no managed marker) under the same slug.
    for slug in effective_slugs:
        existing_entry = mcp_servers.get(slug)
        if existing_entry and existing_entry.get("comment") != MANAGED_MARKER:
            continue  # user overrode this slug; leave theirs alone
        mcp_servers[slug] = _render_server(by_slug[slug])

    # Drop any previously-managed entries that were removed from the list.
    prior: dict = {}
    if lock_path.exists():
        try:
            prior = json.loads(lock_path.read_text())
        except json.JSONDecodeError:
            prior = {}
    for slug in prior.get("installed", []):
        if slug in effective_slugs:
            continue
        entry = mcp_servers.get(slug)
        if entry and entry.get("comment") == MANAGED_MARKER:
            del mcp_servers[slug]

    existing_mcp["mcpServers"] = mcp_servers
    mcp_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_path.write_text(json.dumps(existing_mcp, indent=2) + "\n")

    # .env.claude.example — documented keys only
    env_keys: list[tuple[str, str]] = []
    for slug in effective_slugs:
        s = by_slug[slug]
        for key in s.env:
            env_keys.append((key, f"{slug}: {s.description}"))
    if env_keys:
        existing_env = env_example.read_text() if env_example.exists() else ""
        env_example.write_text(_merge_env_example(existing_env, env_keys))

    wrote_gitignore = _append_gitignore(project_path, GITIGNORE_TARGETS) if env_keys else False

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps({"version": 1, "installed": sorted(effective_slugs)}, indent=2) + "\n")

    return {
        "path": str(mcp_path),
        "env_example": str(env_example) if env_keys else None,
        "installed": sorted(effective_slugs),
        "env_keys": sorted({k[0] for k in env_keys}) if env_keys else [],
        "wrote_gitignore": wrote_gitignore,
    }
