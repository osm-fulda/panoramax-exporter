# panoramax-exporter

Prometheus exporter for a Panoramax (GeoVisio) instance.

Two data sources:

- **Public STAC API** (`/api/collections`) — no credentials. Sequences,
  pictures, total length, per-user breakdown, contributor count.
- **Reports API** (`/api/reports`) — needs an admin/reviewer bearer token.
  Counts of picture/sequence reports by status and issue type.
- **Postgres `accounts` table** (optional) — true registered-account count and
  recent-signup counts. The public API cannot give this.

A background thread refreshes every `REFRESH_INTERVAL` seconds; Prometheus
scrapes cached gauges cheaply.

## Metrics

| metric | labels | source |
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
| `panoramax_reports_up` | | reports (token) |
| `panoramax_reports_total` | `status`, `issue` | reports (token) |
| `panoramax_reports_by_status_total` | `status` | reports (token) |
| `panoramax_reports_open_total` | | reports (token) |
| `panoramax_db_up` | | db |
| `panoramax_accounts_total` | | db |
| `panoramax_accounts_new_total` | `window` (1d/7d/30d) | db |
| `panoramax_job_queue_depth` | `task` | db |
| `panoramax_job_queue_oldest_seconds` | | db |
| `panoramax_pictures_by_status` | `status` | db |
| `panoramax_sequences_by_visibility` | `visibility` | db |
| `panoramax_pictures_new_total` | `window` (1d/7d/30d) | db |
| `panoramax_last_picture_inserted_seconds` | | db |

`report status`: open, open_autofix, waiting, closed_solved, closed_ignored.
`report issue`: blur_missing, blur_excess, inappropriate, privacy,
picture_low_quality, mislocated, copyright, other.

Report/account metrics only appear once their source is enabled. Report gauges
also stay empty until at least one report exists.

Useful alert signals (all DB):
- `panoramax_job_queue_oldest_seconds` high → workers stuck/behind.
- `panoramax_job_queue_depth{task="prepare"}` climbing → ingest backlog.
- `panoramax_pictures_by_status{status="broken"}` rising → processing failures.
- `time() - panoramax_last_picture_inserted_seconds` — actually
  `panoramax_last_picture_inserted_seconds` large → no new uploads (instance quiet/dead).
- `panoramax_sequences_by_visibility` — public vs hidden split (the global
  `/api/collections` stats only count public data).

## Config (env)

| var | default | notes |
|---|---|---|
| `PANORAMAX_API` | `https://panorama.osm-fulda.de/api` | |
| `LISTEN_PORT` | `9155` | |
| `REFRESH_INTERVAL` | `300` | seconds |
| `PER_USER` | `true` | per-user label series |
| `PANORAMAX_TOKEN` | – | admin bearer; enables reports + hidden seqs |
| `REPORTS` | `auto` | `auto` = on when token set; or `true`/`false` |
| `DB_URL` | – | libpq DSN; enables account metrics |
| `PGHOST`/`PGUSER`/`PGPASSWORD`/`PGDATABASE`/`PGPORT` | – | alt to `DB_URL` (used if `DB_URL` unset) |
| `NEW_ACCOUNT_WINDOWS` | `1,7,30` | day windows for new-accounts metric |

The account-age windows need a `created_at` column on `accounts`; if absent
those series are skipped (total count still works).

## Run locally (Docker)

```bash
docker compose up --build          # API-only, no creds
curl -s localhost:9155/metrics | grep panoramax_
```

Add reports + accounts by uncommenting `PANORAMAX_TOKEN` / `DB_URL` in
`docker-compose.yml`.

## GitOps (Codeberg → Flux, cluster k8s01)

The `k8s/` dir holds kustomize manifests matching the repo layout
(`namespace: panoramax`, Zalando Postgres secret, annotation-based scrape).

1. **Build + push image** to the Codeberg container registry, pin a tag:
   ```bash
   docker build -t codeberg.org/osm-fulda/panoramax-exporter:0.1.0 .
   docker push codeberg.org/osm-fulda/panoramax-exporter:0.1.0
   ```
   Update the `image:` in `k8s/deployment.yaml`.

2. **Copy `k8s/` into the gitops repo** — e.g. `apps/panoramax-exporter/`, then
   register it in `clusters/k8s01/apps.yaml` (Flux Kustomization), or add the
   files to `apps/panoramax/` and list them in that `kustomization.yaml`.

3. **Verify the in-cluster API service name.** `deployment.yaml` points
   `PANORAMAX_API` at `http://panoramax.panoramax.svc.cluster.local/api` —
   confirm against the chart's Service (`kubectl -n panoramax get svc`) or just
   use the public URL `https://panorama.osm-fulda.de/api`.

4. **Reports token** (optional):
   ```bash
   kubectl -n panoramax create secret generic panoramax-exporter \
     --from-literal=token=<ADMIN_BEARER>
   ```
   DB creds are already wired from the Zalando secret
   `panoramax.panoramax-postgres.credentials.postgresql.acid.zalan.do`.

5. **Scrape.** No prometheus-operator is present in the repo, so the Service
   carries `prometheus.io/*` annotations. If you later add prometheus-operator,
   enable `k8s/servicemonitor.yaml` in the kustomization.

## Notes / limits

- API stats are **public sequences only**. A token including hidden/private
  seqs still won't change the global `/api/collections` list; use the DB for
  fully accurate picture/sequence counts if needed.
- The reports endpoint defaults to open/waiting only — the exporter passes a
  CQL2 filter covering all five statuses to count closed ones too.
