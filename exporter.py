#!/usr/bin/env python3
"""
Prometheus exporter for a Panoramax (GeoVisio) instance.

Two sources, both HTTP:

- Public STAC API (/api/collections): sequences, pictures, length, per-user
  breakdown and contributor count. No credentials needed.
- Admin API (/api/admin/stats and /api/admin/reports/stats): accounts, pipeline
  health, content breakdowns and moderation backlog. Needs an admin/reviewer
  bearer token, and Panoramax >= 2.15.1.

A background thread refreshes the numbers every REFRESH_INTERVAL seconds and
writes them into Gauges; Prometheus scrapes the cached values cheaply.
"""

import logging
import math
import os
import threading
import time

import requests
from prometheus_client import Gauge, start_http_server

__version__ = "0.2.0"

# --- config (env) -----------------------------------------------------------
API = os.environ.get("PANORAMAX_API", "http://localhost:5000/api").rstrip("/")
TOKEN = os.environ.get("PANORAMAX_TOKEN", "").strip()  # admin bearer, optional
PORT = int(os.environ.get("LISTEN_PORT", "9155"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "300"))  # seconds
PER_USER = os.environ.get("PER_USER", "true").lower() in ("1", "true", "yes")
# the admin endpoints need a token; on by default when one is set
ADMIN = os.environ.get("ADMIN_STATS", "auto").lower()
PAGE_LIMIT = int(os.environ.get("PAGE_LIMIT", "1000"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "60"))
HTTP_RETRIES = int(os.environ.get("HTTP_RETRIES", "5"))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("panoramax-exporter")

# --- metrics ----------------------------------------------------------------
up = Gauge("panoramax_up", "1 if last API scrape succeeded, else 0")
scrape_dur = Gauge("panoramax_scrape_duration_seconds", "Duration of last full refresh")
scrape_ts = Gauge("panoramax_scrape_timestamp_seconds", "Unix time of last successful refresh")

# public STAC catalogue
g_seqs = Gauge("panoramax_sequences_total", "Total public sequences (collections)")
g_pics = Gauge("panoramax_pictures_total", "Total public pictures")
g_km = Gauge("panoramax_length_km_total", "Total public sequence length in km")
g_contrib = Gauge("panoramax_contributors_total", "Distinct users owning >=1 public sequence")

gu_seqs = Gauge("panoramax_user_sequences_total", "Public sequences per user", ["user", "user_id"])
gu_pics = Gauge("panoramax_user_pictures_total", "Public pictures per user", ["user", "user_id"])
gu_km = Gauge("panoramax_user_length_km_total", "Public length km per user", ["user", "user_id"])

# admin stats (optional, needs token)
admin_up = Gauge("panoramax_admin_stats_up", "1 if last admin stats query succeeded, else 0 (only when enabled)")
g_accounts = Gauge("panoramax_accounts_total", "Total registered accounts")
g_accounts_new = Gauge("panoramax_accounts_new_total", "Accounts created within window", ["window"])
# instance-wide inventory: unlike panoramax_pictures_total above, these include
# non-public data, so the two legitimately disagree
g_pics_all = Gauge("panoramax_pictures_all_total", "Total pictures, including non-public")
g_seqs_all = Gauge("panoramax_sequences_all_total", "Total sequences, including non-public")
g_pic_status = Gauge("panoramax_pictures_by_status", "Pictures grouped by processing status", ["status"])
g_pic_new = Gauge("panoramax_pictures_new_total", "Pictures inserted within window", ["window"])
g_pic_fresh = Gauge("panoramax_last_picture_inserted_seconds", "Seconds since most recent picture insert")
g_seq_status = Gauge("panoramax_sequences_by_status", "Sequences grouped by status", ["status"])
g_seq_vis = Gauge("panoramax_sequences_by_visibility", "Sequences grouped by visibility", ["visibility"])
g_jobq = Gauge("panoramax_job_queue_depth", "Pending jobs by task", ["task"])
g_jobq_oldest = Gauge("panoramax_job_queue_oldest_seconds", "Age of oldest job currently due")

# moderation reports (optional, needs token)
reports_up = Gauge("panoramax_reports_up", "1 if last reports query succeeded, else 0 (only when enabled)")
g_reports = Gauge("panoramax_reports_total", "Reports grouped by status and issue type", ["status", "issue"])
g_reports_status = Gauge("panoramax_reports_by_status_total", "Reports grouped by status", ["status"])
g_reports_open = Gauge("panoramax_reports_open_total", "Reports in an unresolved state (open/open_autofix/waiting)")

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
        except requests.HTTPError as e:
            # 4xx (auth/not-found/bad-request) won't fix themselves; fail fast
            # instead of burning the retry budget and stalling the refresh loop
            if e.response is not None and 400 <= e.response.status_code < 500:
                raise
            last = e
            log.warning("GET failed (%d/%d): %s", attempt, HTTP_RETRIES, e)
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("GET failed (%d/%d): %s", attempt, HTTP_RETRIES, e)
        if attempt < HTTP_RETRIES:  # no point sleeping after the last try
            time.sleep(min(2**attempt, 15))
    raise RuntimeError(f"GET {url} failed after {HTTP_RETRIES} tries: {last}")


def _set_mapping(gauge, mapping):
    """Replace a labelled gauge's series from a {label: value} dict.

    Cleared first so keys that disappeared upstream stop being exported instead
    of freezing at their last value.
    """
    gauge.clear()
    for key, value in (mapping or {}).items():
        gauge.labels(str(key)).set(value)


def _iter_collections():
    """Yield every collection dict, following STAC `next` links."""
    url = f"{API}/collections?limit={PAGE_LIMIT}"
    while url:
        data = _get(url)
        yield from data.get("collections", [])
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
        # attribute to the "producer" provider; STAC may also list licensor/host
        provs = c.get("providers") or []
        owner = next((p for p in provs if "producer" in (p.get("roles") or [])), None)
        if owner is None and provs:
            owner = provs[0]
        uid = owner.get("id") if owner else None
        # skip provider-less collections so they don't inflate the contributor
        # count / emit an "unknown" series; totals above still include them
        if uid:
            row = per_user.setdefault(uid, [owner.get("name", "unknown"), 0, 0, 0.0])
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


def _admin_enabled():
    return ADMIN in ("1", "true", "yes", "on") or (ADMIN == "auto" and bool(TOKEN))


def refresh_admin_stats():
    """GET /api/admin/stats -- accounts, pipeline health, content breakdowns.

    Replaces what earlier versions read straight out of Postgres; the endpoint
    exists from Panoramax 2.15.1 on.
    """
    if not _admin_enabled():
        return
    if not TOKEN:
        log.warning("admin stats enabled but no PANORAMAX_TOKEN set; the endpoint requires auth")
        admin_up.set(0)
        return
    try:
        data = _get(f"{API}/admin/stats")

        accounts = data.get("accounts") or {}
        g_accounts.set(accounts.get("total", 0))
        _set_mapping(g_accounts_new, accounts.get("new"))

        pictures = data.get("pictures") or {}
        g_pics_all.set(pictures.get("total", 0))
        _set_mapping(g_pic_status, pictures.get("by_status"))
        _set_mapping(g_pic_new, pictures.get("new"))
        # null means the instance holds no picture at all. An unlabelled gauge
        # always has exactly one sample, so the series cannot be withdrawn --
        # NaN says "unknown" instead of inventing an age or freezing the last.
        fresh = pictures.get("seconds_since_last_insert")
        g_pic_fresh.set(math.nan if fresh is None else fresh)

        sequences = data.get("sequences") or {}
        g_seqs_all.set(sequences.get("total", 0))
        _set_mapping(g_seq_status, sequences.get("by_status"))
        _set_mapping(g_seq_vis, sequences.get("by_visibility"))

        jobs = data.get("jobs") or {}
        _set_mapping(g_jobq, jobs.get("by_task"))
        # null = no job currently due; NaN for the same reason as above
        oldest = jobs.get("oldest_due_job_age_seconds")
        g_jobq_oldest.set(math.nan if oldest is None else oldest)

        admin_up.set(1)
        log.info(
            "admin stats ok: accounts=%d pictures=%d sequences=%d",
            accounts.get("total", 0),
            pictures.get("total", 0),
            sequences.get("total", 0),
        )
    except Exception as e:  # noqa: BLE001
        admin_up.set(0)
        log.error("admin stats refresh failed: %s", e)


def refresh_reports():
    """GET /api/admin/reports/stats -- aggregate moderation counts.

    Counts only: the endpoint never returns a report body, reporter email or
    reporter identity.
    """
    if not _admin_enabled():
        return
    if not TOKEN:
        reports_up.set(0)
        return
    try:
        data = _get(f"{API}/admin/reports/stats")

        _set_mapping(g_reports_status, data.get("by_status"))
        g_reports_open.set(data.get("unresolved", 0))

        g_reports.clear()
        for status, issues in (data.get("by_status_issue") or {}).items():
            for issue, n in (issues or {}).items():
                g_reports.labels(str(status), str(issue)).set(n)

        reports_up.set(1)
        log.info("reports ok: total=%d unresolved=%d", data.get("total", 0), data.get("unresolved", 0))
    except Exception as e:  # noqa: BLE001
        reports_up.set(0)
        log.error("reports refresh failed: %s", e)


def loop():
    while True:
        try:
            refresh_api()
            up.set(1)
        except Exception as e:  # noqa: BLE001
            up.set(0)
            log.error("api refresh failed: %s", e)
        refresh_admin_stats()
        refresh_reports()
        time.sleep(REFRESH_INTERVAL)


def main():
    log.info(
        "starting panoramax exporter %s: api=%s admin=%s port=%d interval=%ds",
        __version__,
        API,
        "on" if _admin_enabled() else "off",
        PORT,
        REFRESH_INTERVAL,
    )
    start_http_server(PORT)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    t.join()


if __name__ == "__main__":
    main()
