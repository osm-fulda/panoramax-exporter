"""Unit tests for the Panoramax exporter.

The module reads its config from the environment at import time and registers
its gauges on the default prometheus registry, so tests import it once and
stub out the HTTP layer (`exporter._get`) rather than hitting a real instance.
"""

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


# --- reports -----------------------------------------------------------------


def test_refresh_reports_disabled_without_token(monkeypatch):
    """REPORTS=auto and no token: stay silent rather than hammering a 401."""
    monkeypatch.setattr(exporter, "REPORTS", "auto")
    monkeypatch.setattr(exporter, "TOKEN", "")
    called = []
    monkeypatch.setattr(exporter, "_iter_reports", lambda: called.append(1) or iter([]))
    exporter.refresh_reports()
    assert called == []


def test_refresh_reports_counts_by_status_and_issue(monkeypatch):
    reports = [
        {"status": "open", "issue": "blur_missing"},
        {"status": "open", "issue": "blur_missing"},
        {"status": "waiting", "issue": "privacy"},
        {"status": "closed_solved", "issue": "privacy"},
    ]
    monkeypatch.setattr(exporter, "REPORTS", "true")
    monkeypatch.setattr(exporter, "TOKEN", "secret")
    monkeypatch.setattr(exporter, "_iter_reports", lambda: iter(reports))
    exporter.refresh_reports()

    assert exporter.g_reports.labels("open", "blur_missing")._value.get() == 2
    assert exporter.g_reports_status.labels("waiting")._value.get() == 1
    assert exporter.g_reports_open._value.get() == 3  # open + waiting
    assert exporter.reports_up._value.get() == 1


def test_refresh_reports_sets_down_on_failure(monkeypatch):
    def boom():
        raise RuntimeError("api down")

    monkeypatch.setattr(exporter, "REPORTS", "true")
    monkeypatch.setattr(exporter, "TOKEN", "secret")
    monkeypatch.setattr(exporter, "_iter_reports", boom)
    exporter.refresh_reports()

    assert exporter.reports_up._value.get() == 0


# --- db ----------------------------------------------------------------------


def test_db_disabled_without_config(monkeypatch):
    monkeypatch.setattr(exporter, "DB_URL", "")
    monkeypatch.delenv("PGHOST", raising=False)
    assert exporter._db_enabled() is False
    exporter.refresh_db()  # must be a no-op, not an import error


def test_db_enabled_via_pghost(monkeypatch):
    monkeypatch.setattr(exporter, "DB_URL", "")
    monkeypatch.setenv("PGHOST", "db.internal")
    assert exporter._db_enabled() is True
