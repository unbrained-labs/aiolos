"""Lint SKILL.md files against Anthropic-aligned conventions.

Checks (severity):
    ERROR   — filename must be `SKILL.md` (exact case)
    ERROR   — frontmatter parseable
    ERROR   — name field matches directory or is valid slug
    ERROR   — description present, single-line, < 1024 chars
    WARN    — description should contain an imperative trigger ("Use when …")
    WARN    — body > 500 lines (Anthropic's soft cap)
    WARN    — `disable-model-invocation: true` (breaks manual /name invocation)
    WARN    — `allowed-tools` mentions `Bash(*)` (too broad)
    INFO    — no `<!-- prettier-ignore -->` above frontmatter
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .scaffolder import validate_description

Severity = Literal["error", "warn", "info"]


@dataclass
class Issue:
    severity: Severity
    message: str
    file: Path

    def __str__(self) -> str:
        icon = {"error": "✗", "warn": "!", "info": "·"}[self.severity]
        return f"  {icon} [{self.severity}] {self.file}: {self.message}"


def _parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    """Tiny YAML-ish parser for skill frontmatter.

    Accepts only the shapes we actually emit: single-line key: value pairs
    between `---` fences. Good enough for linting.
    """
    lines = text.splitlines()
    # Skip HTML comments above the fence.
    i = 0
    while i < len(lines) and (lines[i].strip().startswith("<!--") or not lines[i].strip()):
        i += 1
    if i >= len(lines) or lines[i].strip() != "---":
        return None, ""

    fm: dict[str, str] = {}
    j = i + 1
    while j < len(lines):
        if lines[j].strip() == "---":
            body = "\n".join(lines[j + 1 :])
            return fm, body
        line = lines[j]
        if ":" in line:
            key, _, value = line.partition(":")
            fm[key.strip()] = value.strip()
        j += 1
    return None, ""


def lint_skill(path: Path) -> list[Issue]:
    issues: list[Issue] = []

    if path.name != "SKILL.md":
        issues.append(Issue("error", f"filename must be SKILL.md (got {path.name!r})", path))
        return issues

    if not path.exists():
        issues.append(Issue("error", "file does not exist", path))
        return issues

    text = path.read_text()

    # Check for the prettier-ignore marker somewhere in the top 3 lines.
    head = "\n".join(text.splitlines()[:3]).lower()
    if "prettier-ignore" not in head:
        issues.append(
            Issue(
                "info",
                "add `<!-- prettier-ignore -->` above frontmatter to prevent "
                "multi-line `description:` wrapping (silently disables the skill).",
                path,
            )
        )

    fm, body = _parse_frontmatter(text)
    if fm is None:
        issues.append(Issue("error", "no frontmatter block found", path))
        return issues

    # name field
    if "name" not in fm:
        issues.append(Issue("error", "frontmatter is missing `name:`", path))
    else:
        expected = path.parent.name
        if fm["name"].lower() != expected.lower():
            issues.append(
                Issue("warn", f"name={fm['name']!r} differs from directory {expected!r}", path)
            )

    # description
    desc = fm.get("description", "")
    if not desc:
        issues.append(Issue("error", "frontmatter is missing `description:`", path))
    else:
        # Cheap detection of YAML multi-line scalars that would survive our parser.
        if desc in ("|", ">"):
            issues.append(Issue("error", "description uses a multi-line YAML scalar; inline it.", path))
        else:
            for problem in validate_description(desc):
                sev: Severity = "error" if "single line" in problem or "under" in problem else "warn"
                issues.append(Issue(sev, problem, path))

    # disable-model-invocation informational note. Valid for user skills
    # (means Claude can't auto-invoke; the user has to /name it). Documented
    # issues reported it breaking manual invocation in plugin-skill contexts
    # (anthropics/claude-code#22345) — so flag as info, not warn.
    dmi = fm.get("disable-model-invocation", "").lower()
    if dmi == "true":
        issues.append(
            Issue(
                "info",
                "`disable-model-invocation: true` disables model auto-invocation; "
                "the user must type /name. Known to misbehave in plugin contexts; "
                "prefer leaving it off unless auto-invocation is a real problem.",
                path,
            )
        )

    # allowed-tools breadth
    tools = fm.get("allowed-tools", "")
    if "Bash(*)" in tools or "Bash(*, *)" in tools:
        issues.append(
            Issue("warn", "`allowed-tools: Bash(*)` is too broad; scope per command.", path)
        )

    # body length
    body_lines = len(body.splitlines())
    if body_lines > 500:
        issues.append(
            Issue(
                "warn",
                f"body is {body_lines} lines (soft cap 500); move reference material to references/",
                path,
            )
        )

    return issues


def lint_library(library: Path) -> list[Issue]:
    issues: list[Issue] = []
    for skill_md in (library / "skills").rglob("SKILL.md"):
        issues.extend(lint_skill(skill_md))
    return issues
