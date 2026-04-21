#!/usr/bin/env bash
# bootstrap.sh — scaffold a project's .claude/ directory before Claude Code opens
#
# Run this once in a repo that has no .claude/ yet.
# It installs the /setup skill globally so Claude Code can take over from there.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/<you>/aiolos/main/bootstrap.sh | bash
#   or locally:
#   ./bootstrap.sh [--library PATH] [--copy]

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────

LIBRARY="${CLAUDE_LIBRARY:-$HOME/.claude-library}"
GLOBAL_SKILLS_DIR="$HOME/.claude/skills"
SETUP_SKILL_DIR="$GLOBAL_SKILLS_DIR/setup"
SKILL_SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skill/setup"
USE_SYMLINK=true

# ── Arg parsing ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --library) LIBRARY="$2"; shift 2 ;;
    --copy)    USE_SYMLINK=false; shift ;;
    --help|-h)
      echo "Usage: bootstrap.sh [--library PATH] [--copy]"
      echo ""
      echo "Options:"
      echo "  --library PATH   Path to your skill library (default: ~/.claude-library)"
      echo "  --copy           Copy files instead of symlinking"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

info()    { echo "  [·] $*"; }
success() { echo "  [✓] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "── $* ──"; }

require() {
  if ! command -v "$1" &>/dev/null; then
    warn "$1 is not installed or not in PATH"
    return 1
  fi
}

# ── Preflight ─────────────────────────────────────────────────────────────────

header "Preflight"

require node  && success "node $(node --version)" || warn "node not found — skills.sh fetch will not work"
require npx   && success "npx found"              || true
require git   && success "git found"              || warn "git not found"

if ! command -v aiolos &>/dev/null; then
  warn "aiolos CLI not found"
  warn "Install it with:   uv tool install .    (from this repo)"
  warn "Or:                pip install --user ."
  warn "Continuing anyway to set up library structure..."
fi

if ! command -v claude &>/dev/null; then
  warn "claude CLI not found — install Claude Code first: https://code.claude.com"
  warn "Continuing anyway to set up library structure..."
fi

# ── Library structure ─────────────────────────────────────────────────────────

header "Library"

for dir in "$LIBRARY/skills" "$LIBRARY/agents" "$LIBRARY/presets"; do
  if [[ ! -d "$dir" ]]; then
    mkdir -p "$dir"
    success "Created $dir"
  else
    info "Exists: $dir"
  fi
done

# ── Install /setup skill globally ─────────────────────────────────────────────

header "Global /setup skill"

mkdir -p "$GLOBAL_SKILLS_DIR"

if [[ ! -d "$SKILL_SOURCE" ]]; then
  warn "Skill source not found at $SKILL_SOURCE"
  warn "Are you running this from the aiolos repo root?"
  exit 1
fi

if [[ -e "$SETUP_SKILL_DIR" ]]; then
  warn "/setup skill already exists at $SETUP_SKILL_DIR"
  read -r -p "       Overwrite? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    info "Skipping /setup skill install"
  else
    rm -rf "$SETUP_SKILL_DIR"
    install_setup=true
  fi
else
  install_setup=true
fi

if [[ "${install_setup:-false}" == "true" ]]; then
  if [[ "$USE_SYMLINK" == "true" ]]; then
    ln -sfn "$(realpath "$SKILL_SOURCE")" "$SETUP_SKILL_DIR"
    success "Symlinked /setup skill → $SETUP_SKILL_DIR"
  else
    cp -r "$SKILL_SOURCE" "$SETUP_SKILL_DIR"
    success "Copied /setup skill → $SETUP_SKILL_DIR"
  fi
fi

# ── Seed presets ──────────────────────────────────────────────────────────────
#
# aiolos does not ship skill or agent content. We seed presets only —
# each preset points at Claude Code's built-in agents by name and optionally
# lists community skills to fetch from trusted sources (see trust.toml).
# Populate your own library with `aiolos fetch <owner/repo> --skill <name>`.

header "Seeding presets"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRESET_SRC="$REPO_ROOT/presets"

if [[ -d "$PRESET_SRC" ]]; then
  shopt -s nullglob
  for preset in "$PRESET_SRC"/*.toml "$PRESET_SRC"/*.txt; do
    name=$(basename "$preset")
    dest="$LIBRARY/presets/$name"
    if [[ ! -f "$dest" ]]; then
      cp "$preset" "$dest"
      success "Added preset: $name"
    else
      info "Preset exists: $name (skipping)"
    fi
  done
  shopt -u nullglob
else
  info "No presets directory found, skipping"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "────────────────────────────────────────────────"
echo ""
echo "  Bootstrap complete."
echo ""
echo "  Library   : $LIBRARY"
echo "  Skills    : $GLOBAL_SKILLS_DIR"
echo ""
echo "  Next steps:"
echo "    1. Add your own skills to:  $LIBRARY/skills/<name>/SKILL.md"
echo "    2. Add your own agents to:  $LIBRARY/agents/<name>/<name>.md"
echo "    3. Edit presets at:         $LIBRARY/presets/<name>.txt"
echo "    4. Open any repo in Claude Code and run:  /setup"
echo ""
echo "  To fetch skills from skills.sh into your library:"
echo "    npx skills add vercel-labs/agent-skills --list"
echo "    npx skills add vercel-labs/agent-skills --skill <name> --dir $LIBRARY/skills -y"
echo ""
