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
   ___  _     _   _  _   ___  ___      ___  ___  ___  _  _  ___
  / __|| |   /_\ | || | |   \| __|    / __|| __|/ __|| || || _ \
 | (__ | |__ / _ \| __ | | |) | _|    \__ \| _| \__ \| __ || __/
  \___||____/_/ \_\_||_| |___/|___|   |___/|___||___/|_||_||_|

              maxxxx shipping.  one preset to rule the repo.
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
    """Entry point for `aiolos wizard`.

    Returns a small summary dict for tests. Side-effects: prints banner,
    optionally plays sound, and runs the init + harden + tools closing note.
    """
    from .cli import build_parser  # local import to avoid cycles

    print(BANNER)
    wants_sound = False
    if not noninteractive:
        wants_sound = _ask(
            "Sound? (synthesizes a short techno kick pattern via sox if available)", False
        )

    if wants_sound:
        play_intro("hardstyle")

    # Run init (one-shot) with human output, then harden defaults, then tools.
    summary: dict = {"sound": wants_sound, "steps": []}

    parser = build_parser()
    # init
    init_args = parser.parse_args(["init", "--project", str(project)])
    from .cli import cmd_init, cmd_harden, cmd_tools
    print("\n— step 1/3  aiolos init —\n")
    cmd_init(init_args)
    summary["steps"].append("init")

    # harden (defaults, no questions — wizard is about speed; user can re-run harden interactively later)
    print("\n— step 2/3  aiolos harden --defaults —\n")
    harden_args = parser.parse_args(["harden", "--project", str(project), "--defaults"])
    cmd_harden(harden_args)
    summary["steps"].append("harden")

    # tools (show what's wrappable)
    print("\n— step 3/3  aiolos tools —\n")
    tools_args = parser.parse_args(["tools", "--project", str(project)])
    cmd_tools(tools_args)
    summary["steps"].append("tools")

    print("\nDone. Run `aiolos harden` (no --defaults) later to pick hooks.")
    return summary
