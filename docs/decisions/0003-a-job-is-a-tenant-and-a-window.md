# 0003 — A job is a tenant and a window of complete days

**Status:** accepted · 2026-10-15

## Context

A first draft evaluated one forecast run (`aiJobId` in `jobConfiguration`). But the backend
writes no `jobConfiguration`, and the previous system evaluated "the last 30 days of
results against the actuals". Open was whether to hard-code that or make it configurable.

## Decision

- A job evaluates **one tenant (`data-jobs.cid`) over a window of complete days**.
- **Default plus optional overrides in the same config model**: empty `jobConfiguration` =
  the last 30 complete days, ending yesterday. `windowDays` or a fixed `start`/`end` override
  it. Schema: [data-jobs contract](../contracts/data-jobs.md#jobconfiguration).
- **Today is never evaluated** — its sales are still arriving, and including it would make
  every forecast look too high. A fixed `end` of today or later is a config error.
- **Strict parsing** (`extra="forbid"`, strict JSON types): a typo surfaces as
  `error_management` with the reason, instead of silently running on defaults.
- **Days are UTC** for now (provisional; see
  [reads contract](../contracts/forecast-and-sales-reads.md#days)).

## Consequences

- Works with what the backend writes today; customisation needs no code path of its own —
  a new key is a new field with a default.
- Because a window spans many runs, *which* forecast counts for a day has to be decided:
  [ADR 0004](0004-forecast-selection-is-a-named-strategy.md).
- "Today" comes from an injected clock (`Poller(today=…)`), so windows are testable.
