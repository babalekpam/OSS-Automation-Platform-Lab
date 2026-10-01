# Architecture

The lab is a set of components with standard API contracts, composed by `oss_lab.core.stack.Stack`.
Each component sits behind a narrow interface so the in-memory implementation can be replaced by the
real tool (NetBox, Kafka, Temporal, a NETCONF/gNMI controller) without changing callers.

| Component | Module | Standard | Stand-in for |
|---|---|---|---|
| Service catalog | `core/catalog.py` | TMF633 | Catalog service |
| Service orchestrator | `core/orchestrator.py` | TMF641 | Temporal workflows |
| Inventory | `core/inventory.py` | TMF638 / TMF639 | NetBox + TMF adapter |
| Activation | `core/activation.py` | TMF640 | Config renderer + southbound push |
| Controller + devices | `core/controller.py` | NETCONF/gNMI semantics | OpenDaylight / SR Linux |
| Assurance | `core/assurance.py` | TMF642 | Alarm/telemetry correlation |
| Event bus | `core/events.py` | Topic model | Kafka |
| Reconciliation | `core/reconciliation.py` | - | Data-integrity jobs |
| HTTP edge | `core/api.py` | OpenAPI | FastAPI microservices |

## Order flow (add)

`create_service → reserve_resources → activate → enroll_assurance → set_state(active)`

Every step is a `Step(do, undo)`. Steps are retried on `TransientError` (up to 3 attempts). On any other
failure the completed steps are compensated in reverse order, so a failed order leaves no inventory,
reservation, device or assurance residue. This is asserted by `rollback_on_device_failure` and the
reconciliation report. Orders are idempotent by order id.

## Lifecycle

Service states: `designed → reserved → active ⇄ inactive → terminated`. `terminated` is final.
Order actions: `add`, `modify` (immutable characteristics rejected), `suspend`, `resume`, `delete`
(`suspend`/`resume` are lab extensions to TMF641's action set).

## Events

Topic `workflow.step` (`started | retry | completed | failed | compensated`), `order.state`,
`service.state`. Keyed by order id / service id for correlation (`ai/workflow_opt.py`).

## Three-way reconciliation

Intended state (inventory + catalog template) is compared with the controller's view and the actual
device config. Findings: `missing_object`, `attribute_drift`, `controller_drift`, `orphaned_config`,
`stale_reference`, `reservation_mismatch`, `orphaned_reservation`, `assurance_gap`.

## Lab controls

`/lab/*` endpoints (reset, fault injection, drift injection, state export/import) exist only so the
validation suite can drive any target. They must not be exposed outside a lab.
