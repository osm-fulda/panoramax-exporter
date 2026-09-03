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

Two sources, both HTTP; the second is optional:

- **Public STAC** (`/api/collections`) — totals + per-user breakdown.
- **Admin** (`/api/admin/stats`, `/api/admin/reports/stats`, need
  `PANORAMAX_TOKEN`) — accounts, job queue, content health, moderation counts.
  Available from Panoramax 2.15.1; before that this data was read over a direct
  Postgres connection, which is why the metric names still look database-ish.

## Conventions

- Config is read from the environment **at import time** into module-level
  constants. Tests therefore `monkeypatch.setattr(exporter, "TOKEN", …)` rather
  than setting env vars after import.
- Labelled gauges go through `_set_mapping()`, which clears before writing so
  series for statuses or users that disappeared upstream do not linger.
  `.clear()` only works on labelled metrics — calling it on a plain Gauge
  raises, so "no value" for those is `math.nan`.
- The two admin fetches are separately guarded: one failing must not take the
  other's metrics down with it.
- `_get()` fails fast on 4xx and retries 5xx with backoff. Do not "fix" that
  into a blanket retry.
- Metric names are a public API — renaming one is a breaking change (see
  RELEASING.md).
