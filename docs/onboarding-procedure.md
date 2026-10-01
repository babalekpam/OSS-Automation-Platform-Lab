# Onboarding procedure

Packages live under `onboarding/` as YAML. `make onboard` runs the gate for all of them; CI runs it on
every PR. A package is *ready* only if every step passes; the first failure skips the rest.

| Kind | Steps |
|---|---|
| `device-type` | schema_validation → inventory_model → controller_adapter_test → activation_test → assurance_enrollment → reconciliation_gate |
| `service-model` | schema_validation → template_render → lifecycle_tests (add, suspend, resume, modify, delete, reconciliation clean after each) → assurance_mapping |

Included: `device-types/srlinux` and service models `mgmt-interface`, `uplink-interface`.

## Add a service model
1. Copy a file in `onboarding/service-models/`, edit `spec` (a `ServiceSpecification`) and `acceptance`.
2. Keep `device` and `interface` characteristics: the orchestrator reserves `<device>:<interface>`.
3. `make onboard` until READY; open a PR.

Packages run in a scratch stack, so the gate never changes shared state. YANG import and vendor
templates beyond the JSON template model are not implemented yet.
