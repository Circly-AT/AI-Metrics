# 0006 — DB TLS is verified, or explicitly off — never left unconfigured

**Status:** accepted · 2026-10-15 · adopted from AI-Pipeline-V3 (operator ruling
2026-09-12, "we only ever take verified SSL")

## Context

Given no `ssl` argument, pymysql does not connect in cleartext: if the server offers TLS,
it encrypts **and verifies nothing** (`CERT_NONE`, no hostname check). That stops a passive
listener, not an attacker who can intercept the connection and present their own
certificate — they would see the DB password and every row. From inside the process the
connection looks exactly like a verified one. The first version of AIM connected this way.

## Decision

- `DB_TLS` is **required, without a default**: `verified` or `off`. A missing or
  misspelled variable stops startup.
- `verified` needs `DB_SSL_CA`, `DB_SSL_CERT` and `DB_SSL_KEY`, all three, each an existing
  file — checked at startup, not at the first connection. `check_hostname` and
  `verify_mode` are passed explicitly.
- `off` sends `ssl_disabled=True` (real cleartext, for a TLS-less endpoint such as a local
  test DB) and refuses certificate paths, which pymysql would otherwise silently ignore.
- `create_db_engine` requires the TLS arguments and refuses an empty mapping.

Implementation: `src/aim/boundaries/settings.py`, `src/aim/boundaries/db/engine.py`.
Simplified from the pipeline's `DataDbSettings`; same variable names, so one `.env` serves
both.

## Consequences

- AIM cannot start against a DB without either certificates or an explicit `off`.
- `tests/unit/test_settings_tls.py` checks the effect at the driver (the context pymysql
  builds is `CERT_REQUIRED`), and pins pymysql's unverified default — if an upgrade changes
  that, the test says so.
