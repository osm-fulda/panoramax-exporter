# panoramax-exporter

Prometheus exporter for a [Panoramax](https://panoramax.fr/) (GeoVisio) instance.

[![CI](https://github.com/osm-fulda/panoramax-exporter/actions/workflows/ci.yml/badge.svg)](https://github.com/osm-fulda/panoramax-exporter/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/osm-fulda/panoramax-exporter)](https://github.com/osm-fulda/panoramax-exporter/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Panoramax instances expose plenty of data but no metrics endpoint. This exporter
turns two HTTP sources into Prometheus gauges so you can graph growth, alert on
a stuck processing pipeline, and see moderation backlog:

- **Public STAC API** (`/api/collections`) — no credentials. Sequences, pictures,
  total length, per-user breakdown, contributor count.
- **Admin API** (`/api/admin/stats` and `/api/admin/reports/stats`) — needs an
  admin/reviewer bearer token. Registered accounts, job-queue depth and age,
  pictures and sequences by status, growth windows, moderation backlog.

> **Requires Panoramax ≥ 2.15.1** for the admin endpoints. The public STAC
> metrics work against any version. Earlier releases of this exporter read the
> same numbers straight out of Postgres; that path is gone — see
> [CHANGELOG](#changelog).

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
uncomment `PANORAMAX_TOKEN` to enable the admin metrics.

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
| `PANORAMAX_TOKEN` | – | Admin/reviewer bearer token; enables the admin metrics |
| `ADMIN_STATS` | `auto` | `auto` = on when a token is set; or `true`/`false` |
| `PAGE_LIMIT` | `1000` | API page size |
| `HTTP_TIMEOUT` / `HTTP_RETRIES` | `60` / `5` | Per-request timeout, retry count |
| `LOG_LEVEL` | `INFO` | |

The token needs the same permission as `GET /api/reports` — Panoramax gates the
admin stats endpoints on `can_check_reports()`. No database credentials are
involved any more.

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
| `panoramax_admin_stats_up` | | admin |
| `panoramax_accounts_total` | | admin |
| `panoramax_accounts_new_total` | `window` | admin |
| `panoramax_pictures_all_total` | | admin |
| `panoramax_sequences_all_total` | | admin |
| `panoramax_pictures_by_status` | `status` | admin |
| `panoramax_pictures_new_total` | `window` | admin |
| `panoramax_last_picture_inserted_seconds` | | admin |
| `panoramax_sequences_by_status` | `status` | admin |
| `panoramax_sequences_by_visibility` | `visibility` | admin |
| `panoramax_job_queue_depth` | `task` | admin |
| `panoramax_job_queue_oldest_seconds` | | admin |
| `panoramax_reports_up` | | admin |
| `panoramax_reports_total` | `status`, `issue` | admin |
| `panoramax_reports_by_status_total` | `status` | admin |
| `panoramax_reports_open_total` | | admin |

`panoramax_pictures_total` counts **public** pictures from the STAC catalogue;
`panoramax_pictures_all_total` counts everything the instance holds. The two are
meant to differ — the gap is your non-public data. Same for sequences.

`panoramax_last_picture_inserted_seconds` and `panoramax_job_queue_oldest_seconds`
are `NaN` when the instance has no picture at all, respectively no job currently
due. A gauge without labels always has exactly one sample, so `NaN` is how "no
value" is expressed rather than withdrawing the series.

`status` values for reports: `open`, `open_autofix`, `waiting`, `closed_solved`,
`closed_ignored`. `issue` values: `blur_missing`, `blur_excess`, `inappropriate`,
`privacy`, `picture_low_quality`, `mislocated`, `copyright`, `other`.

Metrics from a disabled source are simply absent.

### Useful alerts

- `panoramax_job_queue_oldest_seconds` high → workers stuck or falling behind.
- `panoramax_job_queue_depth{task="prepare"}` climbing → ingest backlog.
- `panoramax_pictures_by_status{status="broken"}` rising → processing failures.
- `panoramax_last_picture_inserted_seconds` large → nothing being uploaded.
- `panoramax_admin_stats_up == 0` / `panoramax_reports_up == 0` → the token or
  connectivity broke, and those metrics are now stale.

## Limits

- STAC numbers cover **public** sequences only. A token does not change what
  `/api/collections` lists; the admin totals are the accurate ones.
- The growth windows are fixed at 1d/7d/30d by the API and cannot be configured
  from here.
- The two admin endpoints are fetched independently: if one fails, the other's
  metrics still update and only its own `_up` gauge drops to 0.
- The `/metrics` endpoint is unauthenticated. Keep it internal.

## Changelog

**0.2.0** — Reads the admin API instead of Postgres. `DB_URL`, the `PG*`
variables and `NEW_ACCOUNT_WINDOWS` are gone, `REPORTS` is now `ADMIN_STATS`,
and `panoramax_db_up` became `panoramax_admin_stats_up`. Requires Panoramax
≥ 2.15.1. Adds `panoramax_pictures_all_total`, `panoramax_sequences_all_total`
and `panoramax_sequences_by_status`.

**0.1.0** — First release.

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
