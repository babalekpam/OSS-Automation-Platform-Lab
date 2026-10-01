"""OpenAPI contract comparison between releases: classifies each change as
breaking or additive."""

from __future__ import annotations

from typing import Any

METHODS = ("get", "post", "put", "patch", "delete")


def _resolve(schema: dict[str, Any] | None, comps: dict[str, Any]) -> dict[str, Any]:
    while schema and "$ref" in schema:
        schema = comps.get(schema["$ref"].rsplit("/", 1)[-1], {})
    return schema or {}


def _flatten(
    schema: dict[str, Any] | None,
    comps: dict[str, Any],
    prefix: str = "",
    seen: tuple[str, ...] = (),
) -> dict[str, tuple[str, bool]]:
    """Map dotted property path -> (type, required)."""
    if schema and "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return {}
        seen = (*seen, name)
    schema = _resolve(schema, comps)
    if "anyOf" in schema:  # Optional[X] -> X
        variants = [v for v in schema["anyOf"] if v.get("type") != "null"]
        return _flatten(variants[0], comps, prefix, seen) if variants else {}
    kind = schema.get("type", "object")
    if kind == "array":
        return _flatten(schema.get("items"), comps, f"{prefix}[]", seen)
    out: dict[str, tuple[str, bool]] = {}
    required = set(schema.get("required", []))
    for prop, sub in schema.get("properties", {}).items():
        path = f"{prefix}.{prop}" if prefix else prop
        sub_r = _resolve(sub, comps)
        sub_kind = sub_r.get("type") or ("anyOf" if "anyOf" in sub_r else "object")
        if "anyOf" in sub_r:
            types = sorted(
                v.get("type", "object") for v in sub_r["anyOf"] if v.get("type") != "null"
            )
            sub_kind = "|".join(types)
        out[path] = (sub_kind, prop in required)
        if sub_kind in ("object", "array") or "$ref" in sub or "anyOf" in sub:
            out.update(_flatten(sub, comps, path, seen))
    return out


def _operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, m): op
        for path, item in spec.get("paths", {}).items()
        for m, op in item.items()
        if m in METHODS
    }


def _response_schema(op: dict[str, Any]) -> dict[str, Any] | None:
    for code, resp in op.get("responses", {}).items():
        if code.startswith("2"):
            return resp.get("content", {}).get("application/json", {}).get("schema")
    return None


def _request_schema(op: dict[str, Any]) -> dict[str, Any] | None:
    return op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def diff_openapi(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []

    def add(kind: str, where: str, detail: str) -> None:
        changes.append({"kind": kind, "where": where, "detail": detail})

    old_ops, new_ops = _operations(old), _operations(new)
    oc, nc = (
        old.get("components", {}).get("schemas", {}),
        new.get("components", {}).get("schemas", {}),
    )
    for (path, method), op in old_ops.items():
        where = f"{method.upper()} {path}"
        if (path, method) not in new_ops:
            add("breaking", where, "operation removed")
            continue
        nop = new_ops[(path, method)]
        old_params = {(p["name"], p["in"]): p for p in op.get("parameters", [])}
        for key, p in {(p["name"], p["in"]): p for p in nop.get("parameters", [])}.items():
            if key not in old_params and p.get("required"):
                add("breaking", where, f"new required {key[1]} parameter '{key[0]}'")
        old_resp = _flatten(_response_schema(op), oc)
        new_resp = _flatten(_response_schema(nop), nc)
        for prop, (typ, _) in old_resp.items():
            if prop not in new_resp:
                add("breaking", where, f"response property '{prop}' removed")
            elif new_resp[prop][0] != typ:
                add(
                    "breaking",
                    where,
                    f"response property '{prop}' type {typ} -> {new_resp[prop][0]}",
                )
        for prop in new_resp.keys() - old_resp.keys():
            add("additive", where, f"response property '{prop}' added")
        old_req = _flatten(_request_schema(op), oc)
        for prop, (_, required) in _flatten(_request_schema(nop), nc).items():
            if required and prop not in old_req:
                add("breaking", where, f"new required request property '{prop}'")
    for path, method in new_ops.keys() - old_ops.keys():
        add("additive", f"{method.upper()} {path}", "operation added")
    return changes
