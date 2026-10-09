"""Run the quickstart of the README exactly as written (Phase 8).

    uv run python -m scripts.check_quickstart            # run it
    uv run python -m scripts.check_quickstart --list     # only print what it would run

The README's "Run it in 5 commands" block is marked ```bash quickstart. CI runs these lines on a
clean runner, so the README cannot drift from what works. Two kinds of line are skipped because the
runner has already done them: ``git clone`` and ``cd`` (it checked the repository out).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BLOCK = re.compile(r"```bash quickstart\n(.*?)```", re.S)
SKIPPED = ("git clone", "cd ")


def commands(text: str) -> list[str]:
    """The commands of the marked block, comments removed, in order. A line like
    ``git clone ... && cd ...`` is checked-out already, so it is dropped as a whole."""
    match = BLOCK.search(text)
    if not match:
        raise SystemExit("README.md has no ```bash quickstart block")
    out = []
    for line in match.group(1).splitlines():
        command = line.split("  #", 1)[0].strip()
        if command and not command.startswith(SKIPPED):
            out.append(command)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)
    todo = commands(args.readme.read_text(encoding="utf-8"))
    if args.list:
        print("\n".join(todo))
        return 0
    started = time.monotonic()
    for command in todo:
        print(f"$ {command}", flush=True)
        step = time.monotonic()
        if subprocess.run(command, shell=True, cwd=ROOT).returncode:
            print(f"FAILED: {command}", file=sys.stderr)
            return 1
        print(f"  ({time.monotonic() - step:.0f} s)", flush=True)
    print(f"the quickstart took {time.monotonic() - started:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
