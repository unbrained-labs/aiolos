"""The grand opening sequence.

`aiolos wizard` runs init + harden + tools in one flow, with an optional
techno-ish kick pattern at the start. No audio files are bundled; we synthesise
kicks via `sox` when it's installed, and fall back to the system sounds that
ship with macOS or to the plain terminal bell on Linux. Fully silent if the
user says no (which is the default).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BANNER = r"""
    █████╗ ██╗ ██████╗ ██╗      ██████╗ ███████╗
   ██╔══██╗██║██╔═══██╗██║     ██╔═══██╗██╔════╝
   ███████║██║██║   ██║██║     ██║   ██║███████╗
   ██╔══██║██║██║   ██║██║     ██║   ██║╚════██║
   ██║  ██║██║╚██████╔╝███████╗╚██████╔╝███████║
   ╚═╝  ╚═╝╚═╝ ╚═════╝ ╚══════╝ ╚═════╝ ╚══════╝

      plumbing for Claude Code — you stay in control.
"""


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _sox_kick(freq: float = 55.0, dur: float = 0.08) -> None:
    """One kick drum via sox — sine wave with a fast fade."""
    subprocess.run(
        ["sox", "-n", "-d", "synth", str(dur), "sine", str(freq),
         "fade", "0", str(dur), "0.02"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _play_system(sound: str) -> None:
    if _have("afplay"):
        subprocess.run(
            ["afplay", f"/System/Library/Sounds/{sound}.aiff"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    elif _have("paplay"):
        subprocess.run(
            ["paplay", "/usr/share/sounds/freedesktop/stereo/complete.oga"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def _beep() -> None:
    sys.stdout.write("\a")
    sys.stdout.flush()


def play_intro(pattern: str = "hardstyle") -> None:
    """Play a short, obnoxious intro. Returns quickly — total <1s."""
    if os.environ.get("CLAUDE_SETUP_NO_SOUND"):
        return

    if _have("sox"):
        # A tiny 4-on-the-floor kick pattern at ~150 BPM (400ms per kick).
        # Modulated freq for that pseudo-hardstyle "wub".
        if pattern == "hardstyle":
            for freq in (55, 55, 52, 58, 55, 55, 52, 60):
                _sox_kick(freq=float(freq), dur=0.07)
                time.sleep(0.04)
        else:
            for freq in (60, 80, 60, 80):
                _sox_kick(freq=float(freq), dur=0.05)
                time.sleep(0.05)
        return

    # Fallback: rapid system-sound stabs.
    if _have("afplay"):
        for s in ("Tink", "Tink", "Bottle", "Tink", "Pop", "Bottle"):
            _play_system(s)
            time.sleep(0.02)
        return

    # Last resort: terminal bells.
    for _ in range(4):
        _beep()
        time.sleep(0.08)


def _ask(prompt: str, default: bool) -> bool:
    suffix = " [Y/n] " if default else " [y/N] "
    try:
        resp = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not resp:
        return default
    return resp in ("y", "yes")


def run_wizard(project: Path, noninteractive: bool = False) -> dict:
    """Interactive grand-tour. Each step is gated by its own y/N prompt, so
    users can pick what they actually want. `noninteractive=True` auto-accepts
    every step (for tests / CI).

    Returns a small summary dict for tests.
    """
    from .cli import build_parser  # local import to avoid cycles

    print(BANNER)
    print("  aiolos sets up Claude Code for this repo in a few small, reversible steps.")
    print("  Every step shows you exactly what it's about to write before doing it.")
    print()

    wants_sound = False
    if not noninteractive:
        wants_sound = _ask(
            "Sound? (short synthesised kick via sox if installed)", False
        )

    if wants_sound:
        play_intro("hardstyle")

    parser = build_parser()
    summary: dict = {"sound": wants_sound, "steps": []}

    def _step(num: int, total: int, label: str, explainer: str) -> bool:
        """Print a step header and ask whether to run it. Returns True = run."""
        print(f"\n— step {num}/{total}  {label} —")
        print(f"  {explainer}")
        if noninteractive:
            return True
        return _ask("  Run this step?", True)

    from .cli import cmd_init, cmd_harden, cmd_tools, cmd_browse, cmd_mcp

    # Step 1 — init (preset detect + install). cmd_init runs its own plan+confirm,
    # but we still gate the step-start so users can skip init entirely.
    if _step(1, 5, "aiolos init",
             "detect your stack → enable built-in agents → install preset skills"):
        # wizard passes --yes so cmd_init's own prompt doesn't double-ask
        init_args = parser.parse_args(
            ["init", "--project", str(project)] + (["--yes"] if noninteractive else [])
        )
        cmd_init(init_args)
        summary["steps"].append("init")

    # Step 2 — browse community skills (new).
    if _step(2, 5, "aiolos browse",
             "pick community skills from anthropics/skills, skills.sh, etc. (optional)"):
        if noninteractive:
            # Can't browse without a TTY; skip in CI.
            print("  (skipped in noninteractive mode)")
        else:
            try:
                browse_args = parser.parse_args(["browse", "--project", str(project)])
                cmd_browse(browse_args)
                summary["steps"].append("browse")
            except SystemExit:
                pass  # browse exits 1 if skills CLI is missing — don't fail the wizard

    # Step 3 — harden (deny rules + hooks).
    if _step(3, 5, "aiolos harden",
             "deny reads of ~/.ssh/~/.aws/.env, block destructive commands, optional hooks"):
        # In interactive mode we run the questionnaire so users pick their hooks.
        # In noninteractive mode we use defaults (what tests rely on).
        argv = ["harden", "--project", str(project)]
        if noninteractive:
            argv += ["--defaults", "--yes"]
        harden_args = parser.parse_args(argv)
        cmd_harden(harden_args)
        summary["steps"].append("harden")

    # Step 4 — mcp (optional).
    if _step(4, 5, "aiolos mcp",
             "configure MCP servers for this repo — .mcp.json with ${VAR} placeholders"):
        if noninteractive:
            print("  (skipped in noninteractive mode — no --preset/--server given)")
        else:
            mcp_args = parser.parse_args(["mcp", "--project", str(project)])
            cmd_mcp(mcp_args)
            summary["steps"].append("mcp")

    # Step 5 — tools scan (read-only, safe).
    if _step(5, 5, "aiolos tools",
             "read-only: show repo-relevant CLIs Claude can't drive yet"):
        tools_args = parser.parse_args(["tools", "--project", str(project)])
        cmd_tools(tools_args)
        summary["steps"].append("tools")

    print("\nDone. Re-run any step individually (see `aiolos --help`) whenever you want.")
    return summary
