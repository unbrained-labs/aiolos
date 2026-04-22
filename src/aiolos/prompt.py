"""Shared interactive prompts.

Keep this small and stdlib-only. Every mutating subcommand should route its
yes/no gate through `confirm()` so `--yes` / `--json` / piped-input contexts
all behave consistently.
"""
from __future__ import annotations


def confirm(message: str, default: bool = True, assume_yes: bool = False) -> bool:
    """Ask the user to proceed. Returns the user's choice.

    - `assume_yes=True` → return True without prompting (for --yes / --json /
      --noninteractive / --dry-run).
    - EOF on input (empty stdin in CI/tests) → return `default`.
    - Piped input (`echo n | aiolos ...`) → `n` is read and respected.
    """
    if assume_yes:
        return True
    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            resp = input(message + suffix).strip().lower()
        except EOFError:
            return default
        if not resp:
            return default
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False


def choose(prompt: str, options: list[str], default: int = 1) -> int | None:
    """Numeric picker. Returns 1-indexed choice or None if user cancelled.

    Reads stdin regardless of TTY status — piped input (q, 2, etc.) is
    respected. EOF on empty stdin → returns `default`.
    """
    if not options:
        return None
    print(prompt)
    for i, opt in enumerate(options, start=1):
        marker = "→" if i == default else " "
        print(f"  {marker} {i}) {opt}")
    while True:
        try:
            resp = input(f"Choice [1-{len(options)}, Enter={default}, q=cancel]: ").strip().lower()
        except EOFError:
            return default
        if not resp:
            return default
        if resp in ("q", "quit", "cancel"):
            return None
        if resp.isdigit():
            n = int(resp)
            if 1 <= n <= len(options):
                return n
        print(f"  please enter 1-{len(options)} or q")


def multiselect(prompt: str, options: list[str]) -> list[int]:
    """Multi-select picker. Returns 1-indexed list of chosen entries.

    Input syntax:
        1,3,5    → items 1, 3, 5
        1-4      → items 1 through 4
        all      → every item
        (empty)  → no items
        q        → cancel (returns [])
    """
    if not options:
        return []
    print(prompt)
    for i, opt in enumerate(options, start=1):
        print(f"  {i}) {opt}")
    try:
        resp = input("Pick (e.g. 1,3 or 1-4 or 'all', Enter=none): ").strip().lower()
    except EOFError:
        return []
    if not resp or resp in ("q", "quit", "cancel"):
        return []
    if resp == "all":
        return list(range(1, len(options) + 1))
    chosen: set[int] = set()
    for token in resp.replace(" ", "").split(","):
        if "-" in token:
            try:
                lo, hi = (int(x) for x in token.split("-", 1))
            except ValueError:
                continue
            for n in range(min(lo, hi), max(lo, hi) + 1):
                if 1 <= n <= len(options):
                    chosen.add(n)
        elif token.isdigit():
            n = int(token)
            if 1 <= n <= len(options):
                chosen.add(n)
    return sorted(chosen)
