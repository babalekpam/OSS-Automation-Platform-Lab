# Runbooks

## Order failed
1. `GET /tmf-api/serviceOrdering/v4/serviceOrder/{id}`: read `orderItem[].error`.
2. `GET /events?topic=workflow.step&key={id}` for the step trail; or `oss_lab.ai.workflow_opt.root_cause`.
3. `GET /reconciliation/report`: a correct compensation leaves it clean. Any finding means a
   compensation step failed (look for `compensation_failed` events).

## Sync drift
1. `GET /reconciliation/report`; group findings by `type`.
2. `attribute_drift` / `missing_object`: re-push intent with a `modify` order (no-op characteristic change).
3. `orphaned_config`: find who changed the device out-of-band before removing it.
4. `orphaned_reservation` / `stale_reference`: inventory defect, fix in inventory first.

## Activation failure
`device_error` is permanent (fix device/config, resubmit). `transient_error` is retried 3x; if it
exhausts, check controller reachability and resubmit with the same order id only after confirming
the earlier attempt compensated.

## Event backlog / platform health
`GET /metrics` for order, retry, failure and active-alarm counts.
