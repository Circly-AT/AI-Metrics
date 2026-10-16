# AI-Metrics documentation

Two kinds of pages, kept apart on purpose:

| Kind | Answers | Where |
|---|---|---|
| **Contracts** | What AIM promises to, or relies on from, someone else: the AI-Pipeline-V3 team, the backend, a reader of the results. | [`contracts/`](contracts/) |
| **Decisions** (ADRs) | Why AIM is built the way it is, where we had a choice. | [`decisions/`](decisions/) |

A contract can change only when the other party agrees. A decision is ours to change: write
a new ADR that supersedes the old one.

## Contracts

Every contract page starts with a **Status** line:
**agreed** (with whom, when) · **assumed** (we rely on it, nobody confirmed it) · **proposal**
(we want it, it does not exist yet).

| Contract | With | Status |
|---|---|---|
| [Metric formulas](contracts/metric-formulas.md) | AI-Pipeline-V3 | agreed for the shared metrics |
| [`data-jobs` as job source](contracts/data-jobs.md) | Backend | table agreed; state usage and `jobConfiguration` assumed |
| [Forecast and sales reads](contracts/forecast-and-sales-reads.md) | AI-Pipeline-V3, backend | assumed |
| [`ai-metric-results`](contracts/ai-metric-results.md) | Backend, consumers of the metrics | proposal |

## Decisions

| ADR | Title | Status |
|---|---|---|
| [0001](decisions/0001-layered-structure-modelled-on-pipeline-v3.md) | Layered structure modelled on AI-Pipeline-V3, simplified | accepted |
| [0002](decisions/0002-jobs-from-data-jobs-with-conditional-claim.md) | Jobs come from `data-jobs`, claimed by a conditional UPDATE | accepted |
| [0003](decisions/0003-a-job-is-a-tenant-and-a-window.md) | A job is a tenant and a window of complete days | accepted |
| [0004](decisions/0004-forecast-selection-is-a-named-strategy.md) | The forecast that counts for a day is a named strategy | accepted |
| [0005](decisions/0005-results-in-long-format-replaced-per-job.md) | Results in long format, replaced per data-job | accepted |
| [0006](decisions/0006-db-tls-verified-or-explicitly-off.md) | DB TLS is verified, or explicitly off | accepted |
| [0007](decisions/0007-grant-preflight-and-permanent-db-errors.md) | Grant preflight at start; permanent DB errors stop the process | accepted |

## Keeping this current

- A change that touches a contract updates its page **in the same commit**, and a change to
  an *agreed* contract needs the other party's agreement first.
- A new choice with alternatives gets an ADR: `decisions/NNNN-<slug>.md`, next free number,
  with the sections Status, Context, Decision, Consequences. Accepted ADRs are not
  rewritten; a later ADR supersedes them and both get a link to the other.
