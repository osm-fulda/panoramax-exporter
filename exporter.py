#!/usr/bin/env python3
"""
Prometheus exporter for a Panoramax (GeoVisio) instance.

Stats source = public STAC API (/api/collections): sequences, pictures, length,
plus per-user breakdown and contributor count. No credentials needed.

Account metrics = optional direct Postgres query on the `accounts` table
(total registered accounts + recent signups). Enable by setting DB_URL.

A background thread refreshes the numbers every REFRESH_INTERVAL seconds and
writes them into Gauges; Prometheus scrapes the cached values cheaply.
"""

import logging
import os
import threading
import time

import requests
from prometheus_client import Gauge, start_http_server

# --- config (env) -----------------------------------------------------------
API = os.environ.get("PANORAMAX_API", "https://panorama.osm-fulda.de/api").rstrip("/")
TOKEN = os.environ.get("PANORAMAX_TOKEN", "").strip()          # optional bearer
DB_URL = os.environ.get("DB_URL", "").strip()                  # optional libpq DSN
PORT = int(os.environ.get("LISTEN_PORT", "9155"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "300"))  # seconds
PER_USER = os.environ.get("PER_USER", "true").lower() in ("1", "true", "yes")
# reports need an admin/reviewer bearer token; on by default when a token is set
REPORTS = os.environ.get("REPORTS", "auto").lower()
PAGE_LIMIT = int(os.environ.get("PAGE_LIMIT", "1000"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "60"))
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "5"))
# comma-separated day-windows for "new accounts" metric
NEW_WINDOWS = [w.strip() for w in os.environ.get("NEW_ACCOUNT_WINDOWS", "1,7,30").split(",") if w.strip()]

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("panoramax-exporter")

# --- metrics ----------------------------------------------------------------
up = Gauge("panoramax_up", "1 if last API scrape succeeded, else 0")
scrape_dur = Gauge("panoramax_scrape_duration_seconds", "Duration of last full refresh")
scrape_ts = Gauge("panoramax_scrape_timestamp_seconds", "Unix time of last successful refresh")

g_seqs = Gauge("panoramax_sequences_total", "Total public sequences (collections)")
g_pics = Gauge("panoramax_pictures_total", "Total public pictures")
g_km = Gauge("panoramax_length_km_total", "Total public sequence length in km")
g_contrib = Gauge("panoramax_contributors_total", "Distinct users owning >=1 public sequence")

gu_seqs = Gauge("panoramax_user_sequences_total", "Public sequences per user", ["user", "user_id"])
gu_pics = Gauge("panoramax_user_pictures_total", "Public pictures per user", ["user", "user_id"])
gu_km = Gauge("panoramax_user_length_km_total", "Public length km per user", ["user", "user_id"])

# reports (optional, needs token)
ALL_REPORT_STATUSES = ("open", "open_autofix", "waiting", "closed_solved", "closed_ignored")
reports_up = Gauge("panoramax_reports_up", "1 if last reports query succeeded, else 0 (only when enabled)")
g_reports = Gauge("panoramax_reports_total", "Reports grouped by status and issue type", ["status", "issue"])
g_reports_status = Gauge("panoramax_reports_by_status_total", "Reports grouped by status", ["status"])
g_reports_open = Gauge("panoramax_reports_open_total", "Reports in an unresolved state (open/open_autofix/waiting)")

# DB (optional)
db_up = Gauge("panoramax_db_up", "1 if last DB query succeeded, else 0 (only when DB_URL set)")
g_accounts = Gauge("panoramax_accounts_total", "Total registered accounts (DB)")
g_new = Gauge("panoramax_accounts_new_total", "Accounts created within window (DB)", ["window"])
# processing pipeline
g_jobq = Gauge("panoramax_job_queue_depth", "Pending jobs in job_queue by task (DB)", ["task"])
g_jobq_oldest = Gauge("panoramax_job_queue_oldest_seconds", "Age of oldest ready job in job_queue (DB)")
# content health
g_pic_status = Gauge("panoramax_pictures_by_status", "Pictures grouped by status (DB)", ["status"])
g_seq_vis = Gauge("panoramax_sequences_by_visibility", "Sequences grouped by visibility (DB)", ["visibility"])
# growth / liveness
g_pic_new = Gauge("panoramax_pictures_new_total", "Pictures inserted within window (DB)", ["window"])
g_pic_fresh = Gauge("panoramax_last_picture_inserted_seconds", "Seconds since most recent picture insert (DB)")

_session = requests.Session()
if TOKEN:
    _session.headers["Authorization"] = f"Bearer {TOKEN}"


def _get(url):
    last = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            r = _session.get(url, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("GET failed (%d/%d): %s", attempt, HTTP_RETRIES, e)
            time.sleep(min(2 ** attempt, 15))
    raise RuntimeError(f"GET {url} failed after {HTTP_RETRIES} tries: {last}")


def _iter_collections():
    """Yield every collection dict, following STAC `next` links."""
    url = f"{API}/collections?limit={PAGE_LIMIT}"
    while url:
        data = _get(url)
        for c in data.get("collections", []):
            yield c
        url = ""
        for link in data.get("links", []) or []:
            if link.get("rel") == "next":
                url = link.get("href", "")
                break


def refresh_api():
    t0 = time.time()
    seqs = pics = 0
    km = 0.0
    per_user = {}  # user_id -> [name, seqs, pics, km]

    for c in _iter_collections():
        seqs += 1
        pc = ((c.get("stats:items") or {}).get("count")) or 0
        k = c.get("geovisio:length_km") or 0.0
        pics += pc
        km += k
        provs = c.get("providers") or []
        owner = provs[0] if provs else {}
        uid = owner.get("id", "unknown")
        name = owner.get("name", "unknown")
        row = per_user.setdefault(uid, [name, 0, 0, 0.0])
        row[1] += 1
        row[2] += pc
        row[3] += k

    g_seqs.set(seqs)
    g_pics.set(pics)
    g_km.set(round(km, 3))
    g_contrib.set(len(per_user))

    if PER_USER:
        gu_seqs.clear()
        gu_pics.clear()
        gu_km.clear()
        for uid, (name, s, p, k) in per_user.items():
            gu_seqs.labels(name, uid).set(s)
            gu_pics.labels(name, uid).set(p)
            gu_km.labels(name, uid).set(round(k, 3))

    scrape_dur.set(round(time.time() - t0, 3))
    scrape_ts.set(time.time())
    log.info("api ok: seqs=%d pics=%d km=%.2f contributors=%d", seqs, pics, km, len(per_user))


def _iter_reports():
    """Yield every report dict. Default endpoint hides closed ones, so we pass a
    CQL2 filter covering all statuses. Follows `next` links if the API paginates."""
    from urllib.parse import quote

    statuses = ", ".join(f"'{s}'" for s in ALL_REPORT_STATUSES)
    cql = f"status IN ({statuses})"
    url = f"{API}/reports?limit={PAGE_LIMIT}&filter={quote(cql)}"
    while url:
        data = _get(url)
        for rep in data.get("reports", []):
            yield rep
        url = ""
        for link in data.get("links", []) or []:
            if link.get("rel") == "next":
                url = link.get("href", "")
                break


def refresh_reports():
    enabled = REPORTS in ("1", "true", "yes", "on") or (REPORTS == "auto" and bool(TOKEN))
    if not enabled:
        return
    if not TOKEN:
        log.warning("reports enabled but no PANORAMAX_TOKEN set; endpoint requires auth")
        reports_up.set(0)
        return
    try:
        counts = {}       # (status, issue) -> n
        by_status = {s: 0 for s in ALL_REPORT_STATUSES}
        for rep in _iter_reports():
            status = rep.get("status") or "unknown"
            issue = rep.get("issue") or "unknown"
            counts[(status, issue)] = counts.get((status, issue), 0) + 1
            by_status[status] = by_status.get(status, 0) + 1

        g_reports.clear()
        for (status, issue), n in counts.items():
            g_reports.labels(status, issue).set(n)
        g_reports_status.clear()
        for status, n in by_status.items():
            g_reports_status.labels(status).set(n)
        open_ish = by_status.get("open", 0) + by_status.get("open_autofix", 0) + by_status.get("waiting", 0)
        g_reports_open.set(open_ish)

        reports_up.set(1)
        log.info("reports ok: total=%d open-ish=%d", sum(by_status.values()), open_ish)
    except Exception as e:  # noqa: BLE001
        reports_up.set(0)
        log.error("reports refresh failed: %s", e)


def _db_enabled():
    # explicit DSN, or standard libpq env vars (PGHOST/PGUSER/...) from a secret
    return bool(DB_URL or os.environ.get("PGHOST"))


def refresh_db():
    if not _db_enabled():
        return
    import psycopg2  # imported lazily so API-only mode needs no driver

    try:
        # DB_URL wins; otherwise psycopg2 reads PGHOST/PGUSER/PGPASSWORD/PGDATABASE
        conn_args = {"dsn": DB_URL} if DB_URL else {}
        with psycopg2.connect(connect_timeout=10, **conn_args) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM accounts")
                g_accounts.set(cur.fetchone()[0])
                # new-account windows; guarded so a missing created_at just skips
                for w in NEW_WINDOWS:
                    try:
                        cur.execute(
                            "SELECT count(*) FROM accounts "
                            "WHERE created_at > now() - (%s || ' days')::interval",
                            (w,),
                        )
                        g_new.labels(f"{w}d").set(cur.fetchone()[0])
                    except Exception as e:  # noqa: BLE001
                        conn.rollback()
                        log.warning("new-account window %sd skipped: %s", w, e)

                # each block is independently guarded: a missing table/column
                # skips just that metric instead of failing the whole refresh
                def _block(name, fn):
                    try:
                        fn()
                    except Exception as e:  # noqa: BLE001
                        conn.rollback()
                        log.warning("db block %s skipped: %s", name, e)

                def _job_queue():
                    g_jobq.clear()
                    cur.execute("SELECT task, count(*) FROM job_queue GROUP BY task")
                    for task, n in cur.fetchall():
                        g_jobq.labels(str(task)).set(n)
                    # oldest job that is due now (to_do_after_ts null or in the past)
                    cur.execute(
                        "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(ts)), 0) "
                        "FROM job_queue WHERE to_do_after_ts IS NULL OR to_do_after_ts <= now()"
                    )
                    g_jobq_oldest.set(cur.fetchone()[0])
                _block("job_queue", _job_queue)

                def _pic_status():
                    g_pic_status.clear()
                    cur.execute("SELECT status, count(*) FROM pictures GROUP BY status")
                    for status, n in cur.fetchall():
                        g_pic_status.labels(str(status)).set(n)
                _block("pictures_by_status", _pic_status)

                def _seq_vis():
                    g_seq_vis.clear()
                    cur.execute("SELECT COALESCE(visibility::text, 'unknown'), count(*) FROM sequences GROUP BY 1")
                    for vis, n in cur.fetchall():
                        g_seq_vis.labels(str(vis)).set(n)
                _block("sequences_by_visibility", _seq_vis)

                def _pic_growth():
                    for w in NEW_WINDOWS:
                        cur.execute(
                            "SELECT count(*) FROM pictures "
                            "WHERE inserted_at > now() - (%s || ' days')::interval",
                            (w,),
                        )
                        g_pic_new.labels(f"{w}d").set(cur.fetchone()[0])
                    cur.execute("SELECT COALESCE(EXTRACT(EPOCH FROM now() - max(inserted_at)), -1) FROM pictures")
                    g_pic_fresh.set(cur.fetchone()[0])
                _block("pictures_growth", _pic_growth)

        db_up.set(1)
        log.info("db ok: accounts + pipeline/content/growth metrics set")
    except Exception as e:  # noqa: BLE001
        db_up.set(0)
        log.error("db query failed: %s", e)


def loop():
    while True:
        try:
            refresh_api()
            up.set(1)
        except Exception as e:  # noqa: BLE001
            up.set(0)
            log.error("api refresh failed: %s", e)
        refresh_reports()
        refresh_db()
        time.sleep(REFRESH_INTERVAL)


def main():
    log.info("starting panoramax exporter: api=%s db=%s reports=%s port=%d interval=%ds",
             API, "on" if _db_enabled() else "off",
             "on" if (REPORTS in ("1", "true", "yes", "on") or (REPORTS == "auto" and TOKEN)) else "off",
             PORT, REFRESH_INTERVAL)
    start_http_server(PORT)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    t.join()


if __name__ == "__main__":
    main()
