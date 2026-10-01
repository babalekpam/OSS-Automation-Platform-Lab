"""Drafts pytest cases from an OpenAPI spec. Deterministic by default; an optional
``llm`` callable can refine the draft. Output is a *draft for human review* and is
never executed against a platform automatically."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

HEADER = '''"""AI-assisted draft generated from the OpenAPI spec. REVIEW BEFORE MERGE."""
'''


def _name(path: str, method: str) -> str:
    slug = "".join(c if c.isalnum() else "_" for c in path).strip("_")
    return f"test_{method}_{slug}"


def generate_pytest(openapi: dict[str, Any], llm: Callable[[str], str] | None = None) -> str:
    blocks = [HEADER]
    for path, item in sorted(openapi.get("paths", {}).items()):
        if "{" in path or path.startswith(("/lab", "/openapi", "/docs", "/redoc")):
            continue  # parameterised and lab-control paths need fixtures a human must supply
        op = item.get("get")
        if not op:
            continue
        schema = (
            op.get("responses", {})
            .get("200", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema")
        )
        body = [
            f"def {_name(path, 'get')}(client):",
            f"    r = client.get({path!r})",
            "    assert r.status_code == 200",
        ]
        if schema is not None:
            body.append("    data = r.json()")
            if schema.get("type") == "array":
                body.append("    assert isinstance(data, list)")
            elif schema.get("type") == "object":
                body.append("    assert isinstance(data, dict)")
        blocks.append("\n".join(body) + "\n")
    draft = "\n\n".join(blocks)
    return llm(draft) if llm else draft
