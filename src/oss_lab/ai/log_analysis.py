"""Log template mining (Drain-style) and before/after comparison for deploy validation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable

_MASKS = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<UUID>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<IP>"),
    (re.compile(r"\b(?:ord|svc|job|alm)-\d+\b"), "<ID>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<HEX>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<NUM>"),
]
WILDCARD = "<*>"


def mask(line: str) -> str:
    for pattern, token in _MASKS:
        line = pattern.sub(token, line)
    return line


class TemplateMiner:
    """Groups lines of equal token count whose tokens are >= ``sim`` similar."""

    def __init__(self, sim: float = 0.6) -> None:
        self.sim = sim
        self.clusters: dict[int, list[dict]] = {}

    def _similarity(self, template: list[str], tokens: list[str]) -> float:
        return sum(a == b for a, b in zip(template, tokens, strict=True)) / len(tokens)

    def add(self, line: str) -> str:
        tokens = mask(line).split()
        bucket = self.clusters.setdefault(len(tokens), [])
        best = max(bucket, key=lambda c: self._similarity(c["tokens"], tokens), default=None)
        if best and self._similarity(best["tokens"], tokens) >= self.sim:
            best["tokens"] = [
                a if a == b else WILDCARD for a, b in zip(best["tokens"], tokens, strict=True)
            ]
            best["count"] += 1
        else:
            bucket.append({"tokens": tokens, "count": 1})
            best = bucket[-1]
        return " ".join(best["tokens"])

    def templates(self) -> Counter[str]:
        return Counter(
            {" ".join(c["tokens"]): c["count"] for b in self.clusters.values() for c in b}
        )


def _mine(lines: list[str]) -> Counter[str]:
    miner = TemplateMiner()
    for line in lines:
        miner.add(line)
    # re-label every line with its final template so counts reflect merged wildcards
    final = Counter()
    for line in lines:
        tokens = mask(line).split()
        match = max(
            miner.clusters[len(tokens)],
            key=lambda c: miner._similarity(c["tokens"], tokens),
        )
        final[" ".join(match["tokens"])] += 1
    return final


def compare_windows(
    before: list[str], after: list[str], spike_factor: float = 3.0
) -> dict[str, list]:
    """New templates and spiking templates after a deployment."""
    b, a = _mine(before), _mine(after)
    new = [{"template": t, "count": n} for t, n in a.items() if t not in b]
    spiking = [
        {"template": t, "before": b[t], "after": n}
        for t, n in a.items()
        if t in b and n >= spike_factor * b[t] and n - b[t] >= 3
    ]
    return {
        "new": sorted(new, key=lambda x: -x["count"]),
        "spiking": sorted(spiking, key=lambda x: -x["after"]),
    }


def summarize(findings: dict[str, list], llm: Callable[[str], str] | None = None) -> str:
    """Deterministic summary; if ``llm`` is given it rewrites the facts for humans."""
    facts = [f"NEW x{n['count']}: {n['template']}" for n in findings["new"]]
    facts += [f"SPIKE {s['before']}->{s['after']}: {s['template']}" for s in findings["spiking"]]
    if not facts:
        return "No new or spiking log patterns after the change."
    text = "Log changes after deployment:\n" + "\n".join(f"- {f}" for f in facts)
    return llm(text) if llm else text
