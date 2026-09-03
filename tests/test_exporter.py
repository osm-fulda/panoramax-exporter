"""Unit tests for the Panoramax exporter.

The module reads its config from the environment at import time and registers
its gauges on the default prometheus registry, so tests import it once and
stub out the HTTP layer (`exporter._get`) rather than hitting a real instance.
"""

import math
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
import requests

import exporter


def _page(collections, next_url=None):
    links = [{"rel": "next", "href": next_url}] if next_url else []
    return {"collections": collections, "links": links}


def _collection(uid, name, pics, km, roles=("producer",)):
    providers = [{"id": uid, "name": name, "roles": list(roles)}] if uid else []
    return {
        "stats:items": {"count": pics},
        "geovisio:length_km": km,
        "providers": providers,
    }


# --- version -----------------------------------------------------------------


def test_version_matches_pyproject():
    """A tag ships the version the exporter reports; keep the two in step."""
    data = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    assert exporter.__version__ == data["project"]["version"]


# --- HTTP layer --------------------------------------------------------------


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return {"ok": True}


def test_get_fails_fast_on_4xx(monkeypatch):
    """4xx won't fix itself — one attempt, no retry budget burned."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        return _Resp(403)

    monkeypatch.setattr(exporter._session, "get", fake_get)
    monkeypatch.setattr(exporter.time, "sleep", lambda _s: None)
    with pytest.raises(requests.HTTPError):
        exporter._get("http://api.test/collections")
    assert len(calls) == 1


def test_get_retries_on_5xx_then_succeeds(monkeypatch):
    seq = [_Resp(503), _Resp(503), _Resp(200)]

    def fake_get(url, timeout=None):
        return seq.pop(0)

    monkeypatch.setattr(exporter._session, "get", fake_get)
    monkeypatch.setattr(exporter.time, "sleep", lambda _s: None)
    assert exporter._get("http://api.test/collections") == {"ok": True}
    assert seq == []


def test_get_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(exporter._session, "get", lambda url, timeout=None: _Resp(500))
    monkeypatch.setattr(exporter.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError):
        exporter._get("http://api.test/collections")


# --- collection paging -------------------------------------------------------


def test_iter_collections_follows_next_links(monkeypatch):
    pages = {
        f"{exporter.API}/collections?limit={exporter.PAGE_LIMIT}": _page(
            [_collection("a", "Alice", 1, 1.0)], "http://api.test/page2"
        ),
        "http://api.test/page2": _page([_collection("b", "Bob", 2, 2.0)]),
    }
    monkeypatch.setattr(exporter, "_get", lambda url: pages[url])
    assert len(list(exporter._iter_collections())) == 2


# --- aggregation -------------------------------------------------------------


def test_refresh_api_aggregates_totals_and_per_user(monkeypatch):
    cols = [
        _collection("a", "Alice", 10, 1.5),
        _collection("a", "Alice", 5, 0.5),
        _collection("b", "Bob", 7, 2.0),
    ]
    monkeypatch.setattr(exporter, "_iter_collections", lambda: iter(cols))
    exporter.refresh_api()

    assert exporter.g_seqs._value.get() == 3
    assert exporter.g_pics._value.get() == 22
    assert exporter.g_km._value.get() == pytest.approx(4.0)
    assert exporter.g_contrib._value.get() == 2
    assert exporter.gu_pics.labels("Alice", "a")._value.get() == 15
    assert exporter.gu_seqs.labels("Alice", "a")._value.get() == 2


def test_refresh_api_skips_provider_less_collections(monkeypatch):
    """No provider = no per-user series and no contributor, but totals still count it."""
    cols = [_collection("a", "Alice", 4, 1.0), _collection(None, None, 6, 2.0)]
    monkeypatch.setattr(exporter, "_iter_collections", lambda: iter(cols))
    exporter.refresh_api()

    assert exporter.g_pics._value.get() == 10
    assert exporter.g_contrib._value.get() == 1


def test_refresh_api_prefers_producer_over_other_roles(monkeypatch):
    col = {
        "stats:items": {"count": 3},
        "geovisio:length_km": 1.0,
        "providers": [
            {"id": "host1", "name": "Host", "roles": ["host", "licensor"]},
            {"id": "prod1", "name": "Producer", "roles": ["producer"]},
        ],
    }
    monkeypatch.setattr(exporter, "_iter_collections", lambda: iter([col]))
    exporter.refresh_api()

    assert exporter.gu_pics.labels("Producer", "prod1")._value.get() == 3


def test_refresh_api_clears_stale_user_series(monkeypatch):
    """A user whose sequences disappear must not keep a stale gauge forever."""
    monkeypatch.setattr(exporter, "_iter_collections", lambda: iter([_collection("gone", "Gone", 1, 1.0)]))
    exporter.refresh_api()
    monkeypatch.setattr(exporter, "_iter_collections", lambda: iter([_collection("stay", "Stay", 1, 1.0)]))
    exporter.refresh_api()

    samples = {s.labels["user_id"] for m in exporter.gu_pics.collect() for s in m.samples}
    assert samples == {"stay"}


# --- admin stats -------------------------------------------------------------

_STATS = {
    "accounts": {"total": 42, "new": {"1d": 1, "7d": 3, "30d": 9}},
    "pictures": {
        "total": 1000,
        "by_status": {"ready": 900, "broken": 10, "preparing": 90},
        "new": {"1d": 5, "7d": 20, "30d": 70},
        "seconds_since_last_insert": 123.5,
    },
    "sequences": {
        "total": 50,
        "by_status": {"ready": 48, "deleted": 2},
        "by_visibility": {"public": 40, "hidden": 9, "unset": 1},
    },
    "jobs": {"by_task": {"prepare": 4, "delete": 0}, "oldest_due_job_age_seconds": 61.0},
}


def _enable_admin(monkeypatch):
    monkeypatch.setattr(exporter, "ADMIN", "auto")
    monkeypatch.setattr(exporter, "TOKEN", "secret")


def test_unlabelled_admin_gauges_start_unknown():
    """Never-scraped admin gauges must not read 0 -- that is a real value.

    Without a token, `panoramax_job_queue_oldest_seconds 0` would read as "no
    backlog" rather than "never looked". Checked in a subprocess because the
    assertion is about the module's startup state, which the other tests in
    this file have long since overwritten.
    """
    probe = textwrap.dedent("""
        import math, exporter
        unknown = (exporter.g_accounts, exporter.g_pics_all, exporter.g_seqs_all,
                   exporter.g_pic_fresh, exporter.g_jobq_oldest, exporter.g_reports_open)
        assert all(math.isnan(g._value.get()) for g in unknown), "expected NaN before first scrape"
        # the _up gauges are different: 0 there means "not working", which is true
        assert exporter.admin_up._value.get() == 0
        assert exporter.reports_up._value.get() == 0
        print("ok")
    """)
    root = Path(__file__).parent.parent
    result = subprocess.run([sys.executable, "-c", probe], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_admin_stats_disabled_without_token(monkeypatch):
    """ADMIN_STATS=auto and no token: stay quiet rather than hammering a 401."""
    monkeypatch.setattr(exporter, "ADMIN", "auto")
    monkeypatch.setattr(exporter, "TOKEN", "")
    called = []
    monkeypatch.setattr(exporter, "_get", lambda url: called.append(url))
    exporter.refresh_admin_stats()
    assert called == []


def test_admin_stats_maps_every_section(monkeypatch):
    _enable_admin(monkeypatch)
    seen = []

    def fake_get(url):
        seen.append(url)
        return _STATS

    monkeypatch.setattr(exporter, "_get", fake_get)
    exporter.refresh_admin_stats()

    assert seen == [f"{exporter.API}/admin/stats"]
    assert exporter.g_accounts._value.get() == 42
    assert exporter.g_accounts_new.labels("7d")._value.get() == 3
    assert exporter.g_pics_all._value.get() == 1000
    assert exporter.g_pic_status.labels("broken")._value.get() == 10
    assert exporter.g_pic_new.labels("30d")._value.get() == 70
    assert exporter.g_pic_fresh._value.get() == 123.5
    assert exporter.g_seqs_all._value.get() == 50
    assert exporter.g_seq_status.labels("deleted")._value.get() == 2
    assert exporter.g_seq_vis.labels("unset")._value.get() == 1
    assert exporter.g_jobq.labels("prepare")._value.get() == 4
    assert exporter.g_jobq_oldest._value.get() == 61.0
    assert exporter.admin_up._value.get() == 1


def test_admin_stats_reports_nan_on_null(monkeypatch):
    """null freshness/job age is unknown, not zero and not the previous value."""
    _enable_admin(monkeypatch)
    monkeypatch.setattr(exporter, "_get", lambda url: _STATS)
    exporter.refresh_admin_stats()

    empty = {
        **_STATS,
        "pictures": {**_STATS["pictures"], "seconds_since_last_insert": None},
        "jobs": {**_STATS["jobs"], "oldest_due_job_age_seconds": None},
    }
    monkeypatch.setattr(exporter, "_get", lambda url: empty)
    exporter.refresh_admin_stats()

    assert math.isnan(exporter.g_pic_fresh._value.get())
    assert math.isnan(exporter.g_jobq_oldest._value.get())


def test_admin_stats_clears_vanished_keys(monkeypatch):
    """A status that disappears upstream must not freeze at its last value."""
    _enable_admin(monkeypatch)
    monkeypatch.setattr(exporter, "_get", lambda url: _STATS)
    exporter.refresh_admin_stats()

    fewer = {**_STATS, "pictures": {**_STATS["pictures"], "by_status": {"ready": 950}}}
    monkeypatch.setattr(exporter, "_get", lambda url: fewer)
    exporter.refresh_admin_stats()

    statuses = {s.labels["status"] for s in exporter.g_pic_status.collect()[0].samples}
    assert statuses == {"ready"}


def test_admin_stats_sets_down_on_failure(monkeypatch):
    _enable_admin(monkeypatch)

    def boom(url):
        raise RuntimeError("api down")

    monkeypatch.setattr(exporter, "_get", boom)
    exporter.refresh_admin_stats()
    assert exporter.admin_up._value.get() == 0


# --- reports -----------------------------------------------------------------

_REPORTS = {
    "total": 4,
    "unresolved": 3,
    "by_status": {"open": 2, "waiting": 1, "closed_solved": 1, "closed_ignored": 0},
    "by_status_issue": {
        "open": {"blur_missing": 2},
        "waiting": {"privacy": 1},
        "closed_solved": {"privacy": 1},
    },
}


def test_reports_disabled_without_token(monkeypatch):
    monkeypatch.setattr(exporter, "ADMIN", "auto")
    monkeypatch.setattr(exporter, "TOKEN", "")
    called = []
    monkeypatch.setattr(exporter, "_get", lambda url: called.append(url))
    exporter.refresh_reports()
    assert called == []


def test_reports_counts_by_status_and_issue(monkeypatch):
    _enable_admin(monkeypatch)
    seen = []

    def fake_get(url):
        seen.append(url)
        return _REPORTS

    monkeypatch.setattr(exporter, "_get", fake_get)
    exporter.refresh_reports()

    assert seen == [f"{exporter.API}/admin/reports/stats"]
    assert exporter.g_reports.labels("open", "blur_missing")._value.get() == 2
    assert exporter.g_reports_status.labels("waiting")._value.get() == 1
    # taken from the endpoint, not recomputed locally
    assert exporter.g_reports_open._value.get() == 3
    assert exporter.reports_up._value.get() == 1


def test_reports_sets_down_on_failure(monkeypatch):
    _enable_admin(monkeypatch)

    def boom(url):
        raise RuntimeError("api down")

    monkeypatch.setattr(exporter, "_get", boom)
    exporter.refresh_reports()
    assert exporter.reports_up._value.get() == 0
