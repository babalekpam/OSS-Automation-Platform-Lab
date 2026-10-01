"""``oss-lab-onboard``: run the onboarding gate for one or more packages."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import discover, onboard


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("paths", nargs="*", help="package files or directories (default: ./onboarding)")
    args = p.parse_args(argv)
    files: list[Path] = []
    for raw in args.paths or ["onboarding"]:
        path = Path(raw)
        files += discover(path) if path.is_dir() else [path]
    ok = True
    for f in files:
        result = onboard(f)
        ok &= result["ready"]
        print(f"{'READY ' if result['ready'] else 'FAILED'} {f}")
        for s in result["steps"]:
            print(f"   {s['status']:7} {s['step']}" + (f"  - {s['detail']}" if s["detail"] else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
