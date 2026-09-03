# AGENTS.md

Prometheus exporter for a Panoramax (GeoVisio) instance. One module,
`exporter.py`, plus tests. No package layout, no framework.

## Commands

    make deps    # install runtime + dev deps (do this in a venv)
    make lint    # ruff check + ruff format --check
    make test    # pytest
    make build   # compileall + docker build
    .venv/bin/python -m pytest tests/test_exporter.py::test_get_fails_fast_on_4xx   # single test

## Architecture

`main()` starts the prometheus HTTP server, then a daemon thread runs `loop()`:
`refresh_api()` → `refresh_reports()` → `refresh_db()` every `REFRESH_INTERVAL`
seconds, writing into module-level `Gauge` objects. Prometheus scrapes the
cached values; a scrape never triggers I/O.

Three independent sources, each optional except the first:

- **API** (`/api/collections`, public) — totals + per-user breakdown.
- **Reports** (`/api/reports`, needs `PANORAMAX_TOKEN`) — moderation counts.
- **Postgres** (`DB_URL` or `PG*` env) — accounts, job queue, content health.

## Conventions

- Config is read from the environment **at import time** into module-level
  constants. Tests therefore `monkeypatch.setattr(exporter, "TOKEN", …)` rather
  than setting env vars after import.
- Every DB metric group is wrapped in `_block()` so a missing table or column
  skips just that metric — Panoramax schemas differ between versions. Keep new
  DB queries inside a `_block`.
- Labelled gauges are `.clear()`ed before each refresh so series for users or
  statuses that disappeared do not linger.
- `_get()` fails fast on 4xx and retries 5xx with backoff. Do not "fix" that
  into a blanket retry.
- Metric names are a public API — renaming one is a breaking change (see
  RELEASING.md).
