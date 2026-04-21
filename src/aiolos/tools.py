"""CLI-tool awareness — scan PATH for installed productivity CLIs and
recommend / scaffold wrapping skills for them.

Philosophy: a CLI the user has already authenticated is the single biggest
productivity unlock — Claude can drive it directly. We detect installed CLIs,
cross-reference with shipped or library-authored wrapper skills, and tell the
user what to install/wrap.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import SKILLS_DIR, get_library


@dataclass(frozen=True)
class CliTool:
    """One CLI in the curated catalog."""
    command: str                    # the binary name on PATH
    category: str                   # git, cloud, db, etc.
    skill_name: str                 # canonical wrapper skill path
    blurb: str                      # one-line description
    install_hint: str = ""          # how the user installs it
    detects: tuple[str, ...] = ()   # project-file markers that suggest this CLI "fits" the repo


CLI_CATALOG: list[CliTool] = [
    # Git hosting
    CliTool("gh", "git", "gh/ops", "GitHub CLI — PRs, issues, workflows, releases.",
            "brew install gh", detects=(".github/",)),
    CliTool("glab", "git", "glab/ops", "GitLab CLI — MRs, pipelines, releases.",
            "brew install glab", detects=(".gitlab-ci.yml",)),

    # Cloud / edge platforms
    CliTool("flyctl", "cloud", "fly/deploy", "Fly.io — deploy, logs, machines, volumes.",
            "brew install flyctl", detects=("fly.toml",)),
    CliTool("wrangler", "cloud", "cloudflare/wrangler",
            "Cloudflare Workers — deploys, secrets, logs.",
            "npm i -g wrangler", detects=("wrangler.toml", "wrangler.jsonc")),
    CliTool("vercel", "cloud", "vercel/ops", "Vercel — deploys, env, logs.",
            "npm i -g vercel", detects=("vercel.json", ".vercel/")),
    CliTool("netlify", "cloud", "netlify/ops", "Netlify — deploys, env, functions.",
            "npm i -g netlify-cli", detects=("netlify.toml",)),
    CliTool("railway", "cloud", "railway/ops", "Railway — deploy, logs, env.",
            "brew install railway", detects=("railway.toml", "railway.json")),
    CliTool("heroku", "cloud", "heroku/ops", "Heroku — deploy, config, logs.",
            "brew tap heroku/brew && brew install heroku", detects=("Procfile",)),

    # Big cloud
    CliTool("aws", "cloud", "aws/cli", "AWS CLI — S3, IAM, Lambda, ECS.",
            "brew install awscli", detects=("cdk.json", ".aws/")),
    CliTool("gcloud", "cloud", "gcp/cli", "Google Cloud CLI — projects, GKE, functions.",
            "brew install --cask google-cloud-sdk", detects=("app.yaml", ".gcloud/")),
    CliTool("az", "cloud", "azure/cli", "Azure CLI — resource groups, app services, AKS.",
            "brew install azure-cli", detects=("azure-pipelines.yml",)),

    # Orchestration
    CliTool("kubectl", "k8s", "k8s/kubectl", "Kubernetes CLI — pods, deployments, contexts.",
            "brew install kubectl", detects=("k8s/", "kustomization.yaml", "**/Chart.yaml")),
    CliTool("helm", "k8s", "k8s/helm", "Helm — install, upgrade, template charts.",
            "brew install helm", detects=("**/Chart.yaml",)),
    CliTool("terraform", "iac", "terraform/ops", "Terraform — plan, apply, state.",
            "brew install terraform", detects=("**/*.tf",)),

    # Databases / BaaS
    CliTool("neonctl", "db", "neon/db", "Neon Postgres — projects, branches, queries.",
            "npm i -g neonctl"),
    CliTool("supabase", "db", "supabase/ops", "Supabase — db, auth, edge functions.",
            "brew install supabase/tap/supabase", detects=("supabase/config.toml",)),
    CliTool("turso", "db", "turso/db", "Turso (libSQL) — dbs, replicas, auth tokens.",
            "brew install tursodatabase/tap/turso"),
    CliTool("psql", "db", "postgres/psql", "psql — connect + run SQL.", "brew install postgresql"),

    # Containers
    CliTool("docker", "container", "docker/cli", "Docker CLI — build, run, inspect.",
            "brew install --cask docker", detects=("Dockerfile", "docker-compose.yml")),

    # Dev / build
    CliTool("pnpm", "node", "node/pnpm", "pnpm — fast, content-addressed package manager.",
            "npm i -g pnpm", detects=("pnpm-lock.yaml",)),
    CliTool("uv", "python", "python/uv", "uv — Python package + env manager (Astral).",
            "brew install uv", detects=("uv.lock",)),
    CliTool("poetry", "python", "python/poetry", "Poetry — Python dependency management.",
            "pipx install poetry", detects=("poetry.lock",)),

    # Commerce / data
    CliTool("stripe", "commerce", "stripe/cli", "Stripe — events, webhook forwarding, resources.",
            "brew install stripe/stripe-cli/stripe"),

    # Generative / media
    CliTool("fal", "ai", "fal/cli", "fal.ai — generative image/video/audio.",
            "pip install fal-client"),
]


@dataclass
class ToolStatus:
    tool: CliTool
    installed: bool
    path: Optional[str] = None
    wrapped_by_library: bool = False
    wrapped_in_project: bool = False
    repo_suggests: bool = False
    repo_suggestion_reasons: list[str] = field(default_factory=list)

    def priority(self) -> int:
        """Higher = user should do something about it."""
        if self.installed and not self.wrapped_by_library:
            return 3  # big win — wrap what's already authenticated
        if self.repo_suggests and not self.installed:
            return 2  # repo suggests this CLI; user hasn't installed it
        if self.installed and self.wrapped_by_library and not self.wrapped_in_project:
            return 1
        return 0


def _repo_suggests(tool: CliTool, project: Path) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for marker in tool.detects:
        if "*" in marker:
            if any(project.glob(marker)):
                reasons.append(f"glob match {marker}")
                continue
        candidate = project / marker
        if candidate.exists():
            reasons.append(f"{marker!r} present")
    return bool(reasons), reasons


def _wrapper_exists(library: Path, skill_name: str) -> bool:
    return (library / SKILLS_DIR / skill_name / "SKILL.md").exists()


def _project_has_wrapper(project: Path, skill_name: str) -> bool:
    leaf = skill_name.rsplit("/", 1)[-1]
    return (project / ".claude" / "skills" / leaf / "SKILL.md").exists()


def scan(project: Path, library: Optional[Path] = None) -> list[ToolStatus]:
    lib = library or get_library()
    out: list[ToolStatus] = []
    for tool in CLI_CATALOG:
        path = shutil.which(tool.command)
        repo_suggests, reasons = _repo_suggests(tool, project)
        status = ToolStatus(
            tool=tool,
            installed=path is not None,
            path=path,
            wrapped_by_library=_wrapper_exists(lib, tool.skill_name),
            wrapped_in_project=_project_has_wrapper(project, tool.skill_name),
            repo_suggests=repo_suggests,
            repo_suggestion_reasons=reasons,
        )
        out.append(status)
    out.sort(key=lambda s: (-s.priority(), s.tool.category, s.tool.command))
    return out


def scan_as_dict(project: Path, library: Optional[Path] = None) -> list[dict]:
    return [
        {
            "command": s.tool.command,
            "category": s.tool.category,
            "skill": s.tool.skill_name,
            "blurb": s.tool.blurb,
            "installed": s.installed,
            "path": s.path,
            "wrapped_by_library": s.wrapped_by_library,
            "wrapped_in_project": s.wrapped_in_project,
            "repo_suggests": s.repo_suggests,
            "repo_suggestion_reasons": s.repo_suggestion_reasons,
            "priority": s.priority(),
            "install_hint": s.tool.install_hint,
        }
        for s in scan(project, library)
    ]


def format_scan(statuses: list[ToolStatus]) -> str:
    """Human-readable output."""
    lines: list[str] = []
    missing_wrapper = [s for s in statuses if s.installed and not s.wrapped_by_library]
    missing_install = [s for s in statuses if s.repo_suggests and not s.installed]
    available = [s for s in statuses if s.installed and s.wrapped_by_library]

    if missing_wrapper:
        lines.append("Installed but not wrapped (big productivity win):")
        for s in missing_wrapper:
            lines.append(f"  • {s.tool.command:<10} → scaffold  aiolos new-skill --wraps {s.tool.command}")
            lines.append(f"    {s.tool.blurb}")
        lines.append("")

    if missing_install:
        lines.append("Suggested by this project but not installed:")
        for s in missing_install:
            reason = s.repo_suggestion_reasons[0] if s.repo_suggestion_reasons else ""
            lines.append(f"  • {s.tool.command:<10} → {s.tool.install_hint}   ({reason})")
        lines.append("")

    if available:
        lines.append("Wrappers available in the library:")
        for s in available:
            state = "✓ installed in project" if s.wrapped_in_project else "needs install"
            lines.append(f"  • {s.tool.command:<10} → {s.tool.skill_name}   [{state}]")

    if not lines:
        lines.append("No actionable tools found. Everything matched is already wrapped or not installed.")

    return "\n".join(lines)
