# 0001 — Layered structure modelled on AI-Pipeline-V3, simplified

**Status:** accepted · 2026-10-15

## Context

AI-Pipeline-V3 is layered `apps → coordination → execution → boundaries | engine → kernel`,
held by import-linter, with RabbitMQ workers, MLflow, split leases and a large meta-test
suite. AIM does one thing per job: read two tables, compute a handful of numbers, write
them. It should feel familiar to someone who knows the pipeline without carrying its
weight.

## Decision

Four layers, enforced by import-linter in `pyproject.toml`:

```
aim.apps        → entry point `aim serve`, wiring
aim.service     → the management loop (poller) and the per-job runner
aim.boundaries  | aim.metrics   → DB adapters + settings | pure metric functions
aim.kernel      → domain types and the Protocols (ports) the service talks through
```

- `service`, `metrics` and `kernel` must not import `sqlalchemy` or `pymysql` (second
  contract), so the poller and the metrics are unit-tested against in-memory fakes.
- One process, one job at a time, no message broker. Scaling = more instances; the claim
  ([ADR 0002](0002-jobs-from-data-jobs-with-conditional-claim.md)) keeps them apart.
- Not taken over: MLflow tracking, RabbitMQ, splits/leases/fencing, golden fixtures, the
  meta-test guards.

## Consequences

- A new data source or sink is a new class in `boundaries` behind an existing Protocol;
  the service does not change.
- A job that ever outgrows one process (very large tenants) needs a rethink — the pipeline's
  split/worker model is the reference then.
