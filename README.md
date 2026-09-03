# panoramax-exporter

Prometheus exporter for a [Panoramax](https://panoramax.fr/) (GeoVisio) instance.

[![CI](https://github.com/osm-fulda/panoramax-exporter/actions/workflows/ci.yml/badge.svg)](https://github.com/osm-fulda/panoramax-exporter/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/osm-fulda/panoramax-exporter)](https://github.com/osm-fulda/panoramax-exporter/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Panoramax instances expose plenty of data but no metrics endpoint. This exporter
turns three sources into Prometheus gauges so you can graph growth, alert on a
stuck processing pipeline, and see moderation backlog:

- **Public STAC API** (`/api/collections`) — no credentials. Sequences, pictures,
  total length, per-user breakdown, contributor count.
- **Reports API** (`/api/reports`) — needs an admin/reviewer bearer token.
  Report counts by status and issue type.
- **Postgres** (optional) — registered accounts, job-queue depth and age,
  pictures by status, growth windows. Things the public API cannot tell you.

A background thread refreshes every `REFRESH_INTERVAL` seconds; Prometheus
scrapes cached gauges, so a scrape never triggers I/O against your instance.

## Quick start

Docker, against any public instance — no credentials needed:

```bash
docker run --rm -p 9155:9155 \
  -e PANORAMAX_API=https://panoramax.openstreetmap.fr/api \
  ghcr.io/osm-fulda/panoramax-exporter:latest

curl -s localhost:9155/metrics | grep panoramax_
```

From a checkout:

```bash
python3 -m venv .venv && . .venv/bin/activate
make deps
PANORAMAX_API=https://panoramax.openstreetmap.fr/api make run
```

`docker compose up --build` does the same via `docker-compose.yml`, where you can
uncomment `PANORAMAX_TOKEN` and `DB_URL` to enable the other two sources.

Scrape config:

```yaml
scrape_configs:
  - job_name: panoramax
    scrape_interval: 60s
    static_configs:
      - targets: ['panoramax-exporter:9155']
```

## Configuration

All configuration is environment variables.

| Variable | Default | Notes |
|---|---|---|
| `PANORAMAX_API` | `http://localhost:5000/api` | Base API URL of the instance |
| `LISTEN_PORT` | `9155` | |
| `REFRESH_INTERVAL` | `300` | Seconds between refreshes |
| `PER_USER` | `true` | Emit per-user label series |
| `PANORAMAX_TOKEN` | – | Admin/reviewer bearer token; enables reports |
| `REPORTS` | `auto` | `auto` = on when a token is set; or `true`/`false` |
| `DB_URL` | – | libpq DSN; enables the database metrics |
| `PGHOST` / `PGPORT` / `PGUSER` / `PGPASSWORD` / `PGDATABASE` | – | Used when `DB_URL` is unset |
| `NEW_ACCOUNT_WINDOWS` | `1,7,30` | Day windows for the "new" counters |
| `PAGE_LIMIT` | `1000` | API page size |
| `HTTP_TIMEOUT` / `HTTP_RETRIES` | `60` / `5` | Per-request timeout, retry count |
| `LOG_LEVEL` | `INFO` | |

Give the database user **read-only** access. It only ever runs `SELECT`.

## Metrics

| Metric | Labels | Source |
|---|---|---|
| `panoramax_up` | | api |
| `panoramax_scrape_duration_seconds` / `_timestamp_seconds` | | api |
| `panoramax_sequences_total` | | api |
| `panoramax_pictures_total` | | api |
| `panoramax_length_km_total` | | api |
| `panoramax_contributors_total` | | api |
| `panoramax_user_sequences_total` | `user`, `user_id` | api |
| `panoramax_user_pictures_total` | `user`, `user_id` | api |
| `panoramax_user_length_km_total` | `user`, `user_id` | api |
| `panoramax_reports_up` | | reports |
| `panoramax_reports_total` | `status`, `issue` | reports |
| `panoramax_reports_by_status_total` | `status` | reports |
| `panoramax_reports_open_total` | | reports |
| `panoramax_db_up` | | db |
| `panoramax_accounts_total` | | db |
| `panoramax_accounts_new_total` | `window` | db |
| `panoramax_job_queue_depth` | `task` | db |
| `panoramax_job_queue_oldest_seconds` | | db |
| `panoramax_pictures_by_status` | `status` | db |
| `panoramax_sequences_by_visibility` | `visibility` | db |
| `panoramax_pictures_new_total` | `window` | db |
| `panoramax_last_picture_inserted_seconds` | | db |

`status` values for reports: `open`, `open_autofix`, `waiting`, `closed_solved`,
`closed_ignored`. `issue` values: `blur_missing`, `blur_excess`, `inappropriate`,
`privacy`, `picture_low_quality`, `mislocated`, `copyright`, `other`.

Metrics from a disabled source are simply absent. Report gauges stay empty until
the instance has at least one report.

### Useful alerts

- `panoramax_job_queue_oldest_seconds` high → workers stuck or falling behind.
- `panoramax_job_queue_depth{task="prepare"}` climbing → ingest backlog.
- `panoramax_pictures_by_status{status="broken"}` rising → processing failures.
- `panoramax_last_picture_inserted_seconds` large → nothing being uploaded.
- `panoramax_db_up == 0` / `panoramax_reports_up == 0` → credentials or
  connectivity broke, and those metrics are now stale.

## Limits

- API numbers cover **public** sequences only. A token does not change what
  `/api/collections` lists; use the database source for fully accurate counts.
- Each DB metric group is guarded independently — on a Panoramax version whose
  schema lacks a table or column, that group is skipped with a warning instead
  of failing the whole refresh. The `accounts` age windows need a `created_at`
  column; without it the total still works.
- The `/metrics` endpoint is unauthenticated. Keep it internal.

## Deployment

Kubernetes manifests for the OSM Fulda cluster live in
[osm-fulda/gitops](https://codeberg.org/osm-fulda/gitops) under
`apps/panoramax-exporter/` and can serve as a starting point: a Deployment with
a read-only database user, a Service carrying `prometheus.io/*` scrape
annotations, and a ServiceMonitor for prometheus-operator setups.

Images are published to `ghcr.io/osm-fulda/panoramax-exporter` for `linux/amd64`
and `linux/arm64`, signed with cosign keyless and shipped with SLSA provenance —
see [RELEASING.md](RELEASING.md) for the verification commands. Pin a version
tag; `latest` moves.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome — especially
reports from Panoramax versions whose schema differs from the one this was
written against.

## License

MIT — see [LICENSE](LICENSE).
