"""Audit SKILL.md + its supporting files for high-risk patterns.

Not a replacement for reading skills before installing — a rough filter to
catch obvious red flags quickly. We surface *signals*, not verdicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import SKILLS_DIR, get_library

Severity = Literal["critical", "high", "medium", "low"]


@dataclass
class AuditFinding:
    severity: Severity
    file: Path
    line: int
    pattern: str
    snippet: str
    rationale: str

    def __str__(self) -> str:
        icon = {"critical": "✗", "high": "!", "medium": "·", "low": "·"}[self.severity]
        loc = f"{self.file}:{self.line}"
        return f"  {icon} [{self.severity}] {loc}  {self.pattern} — {self.rationale}"


# (severity, regex, label, rationale)
PATTERNS: list[tuple[Severity, re.Pattern, str, str]] = [
    ("critical", re.compile(r"Bash\(\s*\*\s*\)"), "Bash(*)",
     "unscoped bash — grants the skill any shell command"),
    ("critical", re.compile(r"\b(eval|exec)\s+\$\("), "eval/exec subshell",
     "dynamic command execution — near-impossible to review"),
    ("critical", re.compile(r"\bcurl[^\n]*\|\s*(bash|sh)\b"), "curl | bash",
     "piping a remote script into a shell — classic supply-chain risk"),
    ("critical", re.compile(r"\bbase64\s+-[dD]\b[^\n]*\|\s*(bash|sh)\b"), "base64 | bash",
     "obfuscated shell execution"),
    ("high", re.compile(r"~/\.ssh|\$HOME/\.ssh"), "reads ~/.ssh",
     "SSH keys should never be needed by a skill"),
    ("high", re.compile(r"~/\.aws|\$HOME/\.aws"), "reads ~/.aws",
     "cloud credentials should come via aws-cli auth, not a skill reading the file"),
    ("high", re.compile(r"~/\.config/gcloud|gcloud/.*credentials"),
     "reads gcloud credentials", "same rationale as ~/.aws"),
    ("high", re.compile(r"~/\.netrc|\$HOME/\.netrc"), "reads ~/.netrc",
     "contains plaintext remote credentials"),
    ("high", re.compile(r"security\s+find-generic-password|security\s+add-generic-password"),
     "macOS keychain access",
     "keychain operations should be explicit, not buried in a skill"),
    ("high", re.compile(r"\bsecret-tool\b|\bgnome-keyring\b"), "Linux secret store access",
     "same rationale as keychain"),
    ("medium", re.compile(r"\bcurl\s+[^\n]*--data-binary\s+@"), "curl --data-binary @file",
     "potential data exfiltration — uploads a file body to a URL"),
    ("medium", re.compile(r"\brm\s+-rf\s+[/~]"), "rm -rf on root/home",
     "destructive path pattern"),
    ("medium", re.compile(r"\.env(?:\.[a-z]+)?\b"), ".env reference",
     "skill touches dotenv files; confirm it doesn't leak them"),
    ("low", re.compile(r"disable-model-invocation:\s*true", re.IGNORECASE),
     "disable-model-invocation", "breaks manual /name; only set if intentional"),
]


def audit_file(path: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    if not path.exists() or not path.is_file():
        return findings
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception:
        return findings

    for idx, line in enumerate(lines, start=1):
        for sev, pat, label, rationale in PATTERNS:
            if pat.search(line):
                findings.append(
                    AuditFinding(
                        severity=sev,
                        file=path,
                        line=idx,
                        pattern=label,
                        snippet=line.strip()[:120],
                        rationale=rationale,
                    )
                )
    return findings


def audit_skill(skill_dir: Path) -> list[AuditFinding]:
    """Walk a single skill directory and audit every text file under it."""
    findings: list[AuditFinding] = []
    if skill_dir.is_file():
        return audit_file(skill_dir)
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".md", ".sh", ".py", ".js", ".ts", ".toml", ".yaml", ".yml", ""}:
            findings.extend(audit_file(path))
    return findings


def audit_library(library: Path | None = None) -> list[AuditFinding]:
    lib = library or get_library()
    findings: list[AuditFinding] = []
    for skill_md in (lib / SKILLS_DIR).rglob("SKILL.md"):
        findings.extend(audit_skill(skill_md.parent))
    return findings


# ── Trust allowlist ──────────────────────────────────────────────────────────

DEFAULT_TRUSTED_AUTHORS: tuple[str, ...] = (
    "anthropics",
    "vercel-labs",
    "obra",
    "trail-of-bits",
    "microsoft",
)


def _trust_file(library: Path) -> Path:
    return library / "trust.toml"


def load_trust(library: Path | None = None) -> set[str]:
    """Return the set of trusted author/org slugs."""
    lib = library or get_library()
    path = _trust_file(lib)
    if not path.exists():
        return set(DEFAULT_TRUSTED_AUTHORS)
    try:
        import tomllib
        data = tomllib.loads(path.read_text())
    except Exception:
        return set(DEFAULT_TRUSTED_AUTHORS)
    authors = data.get("authors") or list(DEFAULT_TRUSTED_AUTHORS)
    return {a.lower().strip() for a in authors if a}


def ensure_trust_file(library: Path) -> Path:
    """Write a default trust.toml if one doesn't exist."""
    path = _trust_file(library)
    if path.exists():
        return path
    lines = [
        "# Trusted skill authors (GitHub org or user slugs).",
        "# aiolos fetch warns when installing from an author not on this list.",
        "authors = [",
    ]
    for a in DEFAULT_TRUSTED_AUTHORS:
        lines.append(f'  "{a}",')
    lines.append("]")
    path.write_text("\n".join(lines) + "\n")
    return path


def source_author(source: str) -> str:
    """Extract the author slug from a skills.sh source spec like `owner/repo`."""
    if "://" in source:
        # e.g. https://github.com/owner/repo
        return source.rstrip("/").split("/")[-2].lower()
    if "/" in source:
        return source.split("/", 1)[0].lower()
    return source.lower()


def is_trusted(source: str, library: Path | None = None) -> bool:
    return source_author(source) in load_trust(library)
