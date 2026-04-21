"""Scaffold new skills and agents with Anthropic-aligned best practices.

Every generated SKILL.md gets:
    * `<!-- prettier-ignore -->` header (description must stay single-line)
    * An imperative "ALWAYS invoke this skill when …" description
    * A scoped `allowed-tools` placeholder (commented guidance)
    * No `disable-model-invocation` — it silently breaks manual `/name` use
    * Progressive disclosure hooks (references/, scripts/, assets/)
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import SKILLS_DIR, AGENTS_DIR, get_library, ensure_library

SKILL_NAME_RE = re.compile(r"^[a-z0-9-]+$")
NAMESPACED_SKILL_RE = re.compile(r"^[a-z0-9-]+(?:/[a-z0-9-]+)*$")


SKILL_TEMPLATE = """<!-- prettier-ignore -->
---
name: {leaf}
description: {description}
allowed-tools: {allowed_tools}
---

# /{leaf} — {title}

## When to use

{when_to_use}

## Steps

1. <first step — concrete, verifiable>
2. <second step>
3. <third step>

## Rules

- <hard constraint 1>
- <hard constraint 2>

## References

See `references/` for background material (load on demand).
"""


CLI_WRAPPER_TEMPLATE = """<!-- prettier-ignore -->
---
name: {leaf}
description: {description}
allowed-tools: Bash({cli}:*) Read
---

# /{leaf} — {cli} wrapper

Assumes `{cli}` is installed and authenticated. If it isn't, stop and tell
the user how to install / log in.

## Preflight

```bash
command -v {cli} >/dev/null || echo "NOT_INSTALLED — install: <doc link>"
{cli} --version
```

## Cheat sheet

| Intent | Command |
|---|---|
| <intent 1> | `{cli} <verb>` |
| <intent 2> | `{cli} <verb>` |
| <intent 3> | `{cli} <verb>` |

## Typical flow

1. <what you read first to ground in the repo state>
2. <the action>
3. <how you verify it worked>

## Rules

- Do not run destructive {cli} subcommands without explicit user confirmation.
- Prefer read-only subcommands to inspect state before mutating anything.
- If `{cli}` prompts for interactive input, tell the user — this skill does not
  fake answers.
"""


AGENT_TEMPLATE = """---
name: {name}
description: {description}
model: {model}
---

You are a {role} for this project.

## Responsibilities

- <bullet 1>
- <bullet 2>
- <bullet 3>

## Rules

- <hard constraint 1>
- <hard constraint 2>
"""


def _slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"[^a-z0-9/-]", "", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-/")


def validate_skill_name(name: str) -> None:
    if not NAMESPACED_SKILL_RE.match(name):
        raise ValueError(
            f"Invalid skill name '{name}'. Use lowercase letters, digits, hyphens, "
            "and '/' for namespacing (e.g. git/commit)."
        )


def validate_agent_name(name: str) -> None:
    if not SKILL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid agent name '{name}'. Use lowercase letters, digits, hyphens only."
        )


def validate_description(description: str) -> list[str]:
    """Return a list of human-readable issues; empty = ok."""
    problems: list[str] = []
    if "\n" in description:
        problems.append("description must be a single line (prettier will silently break it).")
    if len(description) > 1024:
        problems.append(f"description is {len(description)} chars; keep under ~1024.")
    lowered = description.lower()
    triggers = ("use when", "always invoke", "must be used", "proactively")
    if not any(t in lowered for t in triggers):
        problems.append(
            "description should contain an imperative trigger "
            '("Use when…", "ALWAYS invoke when…", "MUST BE USED when…", "Proactively …").'
        )
    return problems


def scaffold_skill(
    name: str,
    description: str,
    allowed_tools: str = "Read",
    library: Path | None = None,
    overwrite: bool = False,
    wraps: str | None = None,
) -> Path:
    """Write a new SKILL.md (and folder scaffold) into the library.

    `wraps` — optional CLI command name; when set, we use a wrapper-specific
    template that pre-scopes `allowed-tools` to `Bash(<cli>:*)` and seeds a
    preflight / cheat-sheet layout tuned for wrapping an existing CLI.
    """
    validate_skill_name(name)
    problems = validate_description(description)
    if problems:
        raise ValueError("description problems:\n  - " + "\n  - ".join(problems))

    lib = library or get_library()
    ensure_library(lib)
    skill_dir = lib / SKILLS_DIR / name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists() and not overwrite:
        raise FileExistsError(f"{skill_file} already exists; pass overwrite=True.")

    leaf = name.rsplit("/", 1)[-1]
    title = leaf.replace("-", " ").title()

    if wraps:
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9._-]*$", wraps):
            raise ValueError(f"Invalid CLI name {wraps!r}")
        content = CLI_WRAPPER_TEMPLATE.format(
            leaf=leaf,
            description=description,
            cli=wraps,
        )
    else:
        content = SKILL_TEMPLATE.format(
            leaf=leaf,
            description=description,
            allowed_tools=allowed_tools,
            title=title,
            when_to_use="Describe the trigger conditions in one short paragraph.",
        )

    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "references").mkdir(exist_ok=True)
    (skill_dir / "scripts").mkdir(exist_ok=True)
    skill_file.write_text(content)

    # Ship a .prettierignore pointer so the description never gets wrapped.
    prettier = lib / SKILLS_DIR / ".prettierignore"
    marker = "SKILL.md"
    if not prettier.exists() or marker not in prettier.read_text():
        prettier.write_text("# keep SKILL.md frontmatter on one line\n**/SKILL.md\n")

    return skill_file


def scaffold_agent(
    name: str,
    description: str,
    role: str = "specialist",
    model: str = "sonnet",
    library: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write a new agent markdown file into the library."""
    validate_agent_name(name)
    if model not in {"sonnet", "opus", "haiku", "inherit"}:
        raise ValueError(f"model must be one of sonnet/opus/haiku/inherit, got {model!r}")

    lib = library or get_library()
    ensure_library(lib)
    agent_dir = lib / AGENTS_DIR / name
    agent_file = agent_dir / f"{name}.md"
    if agent_file.exists() and not overwrite:
        raise FileExistsError(f"{agent_file} already exists; pass overwrite=True.")

    content = AGENT_TEMPLATE.format(
        name=name, description=description, model=model, role=role
    )
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_file.write_text(content)
    return agent_file
