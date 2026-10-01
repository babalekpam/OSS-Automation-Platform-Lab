# Validation plan: N vs N+1 qualification

`make validate` (or `make validate-remote N_URL=... N1_URL=...`) runs one suite against both releases
and writes `validation/reports/readiness-<n>-vs-<n1>.{md,json}`. Exit code 1 means NO-GO.

| Area | Implementation | Blocks release when |
|---|---|---|
| Lifecycle scenarios | `validation/scenarios.py` (8 scenarios: lifecycle, idempotency, conflict, rollback, retry, drift, assurance, observability) | any scenario fails on N+1 (or on the N baseline, which invalidates the comparison) |
| API contract | `validation/contract.py` diffs the two `/openapi.json` documents | removed operation/property, type change, new required parameter or request property |
| Data migration | `runner.migration_check`: export N state, import to N+1, reconcile, then keep operating | any check fails |
| Performance | add/modify/delete on 12 interfaces: orders/s, request p50/p95/p99 | p95/p99 > max(2x, +25 ms) or throughput < 50% of N |
| Mixed-version interop | `runner.interop_matrix`: every orchestrator x activation release combination runs the full suite; the N+1 correlation-id capability must be used only when both sides support it | any combination fails or capability negotiation is wrong |
| Observability | step events and `/metrics` still emitted and parseable | scenario `observability_signals` fails |
| Behavioral diff | scenario status N vs N+1 | reported; failing N+1 blocks |

Additive contract changes (new optional response fields, new operations) are reported but do not block.

## Entry / exit criteria
- Entry: unit tests, lint, and contract tests green on the candidate build.
- Exit: decision `GO`, no open blockers, readiness report attached to the release.

## Known limits
- Both releases are currently the same codebase differing by an additive field, so the lab proves the
  *harness* (it returns NO-GO on an injected regression; see `tests/test_validation.py`), not a real upgrade.
- Mixed-version interop covers two components (orchestrator, activation) with one real capability
  negotiation; it runs in-process only and is skipped for remote targets.
- Performance numbers from the in-process target reflect Python overhead, not a production deployment.

## API fuzzing
`tests/test_contract_fuzz.py` runs Schemathesis against every documented operation (no 5xx,
documented statuses and content types, schema-conformant responses, valid input accepted, invalid
rejected). It found and fixed: undocumented error responses, lenient type coercion in catalog specs,
alarm severity and fault mode typed as free strings, and 500s from malformed `/lab` input.
`/lab/*` is excluded from the public OpenAPI contract.
