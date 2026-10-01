# ADR 0002: In-process components behind real API contracts first

**Status:** accepted. The lab runs on a laptop and in CI without Docker, Kubernetes, Kafka or
Temporal. Components are real services with TMF-style HTTP APIs, compensation, retries and events, with
in-memory state. Replacing a stand-in with the real tool is an adapter change behind the interfaces in
`docs/architecture.md`.
**Trade-off:** state is not durable and concurrency is single-process; Temporal-level durability and
Kafka semantics are not demonstrated until those adapters exist.
