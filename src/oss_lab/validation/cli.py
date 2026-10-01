"""``oss-lab-validate``: run the N/N+1 qualification and write a readiness report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ..core.api import create_app
from ..core.stack import Stack
from .client import LabClient
from .runner import build_report, to_markdown


def _client(label: str, url: str | None) -> LabClient:
    if url:
        return LabClient(httpx.Client(base_url=url, timeout=30))
    return LabClient(TestClient(create_app(Stack(version=label))))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--from", dest="n", default="n", help="baseline release label")
    p.add_argument("--to", dest="n1", default="n1", help="candidate release label")
    p.add_argument("--n-url", help="base URL of a deployed baseline (default: in-process)")
    p.add_argument("--n1-url", help="base URL of a deployed candidate (default: in-process)")
    p.add_argument("--component", default="all")
    p.add_argument("--out", default="validation/reports")
    args = p.parse_args(argv)

    report = build_report(
        args.n, _client(args.n, args.n_url), args.n1, _client(args.n1, args.n1_url), args.component
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"readiness-{args.n}-vs-{args.n1}"
    (out / f"{stem}.json").write_text(json.dumps(report, indent=2))
    (out / f"{stem}.md").write_text(to_markdown(report))
    print(f"{report['decision']}  ->  {out / (stem + '.md')}")
    for blocker in report["blockers"]:
        print(f"  blocker: {blocker}")
    return 0 if report["decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
