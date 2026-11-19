"""The bundled Grafana dashboard must match the metrics the exporter emits.

Metric names are a public API (see RELEASING.md). A metric renamed or removed
here would otherwise leave a dashboard panel blank without any error, and a new
metric would never show up on it.
"""

import json
import re
from pathlib import Path

import pytest
from prometheus_client import Gauge

import exporter

DASHBOARD = json.loads((Path(__file__).parent.parent / "dashboards" / "panoramax.json").read_text())

# Rows are layout; a collapsed row nests its panels under its own `panels`.
PANELS = [c for p in DASHBOARD["panels"] for c in (p.get("panels", []) if p["type"] == "row" else [p])]
TARGETS = [(p["title"], t) for p in PANELS for t in p.get("targets", [])]
METRIC = re.compile(r"\bpanoramax_\w+")

EXPORTED = {g._name for g in vars(exporter).values() if isinstance(g, Gauge)}
QUERIED = {m for _, t in TARGETS for m in METRIC.findall(t["expr"])}


def test_exported_metrics_found():
    assert "panoramax_up" in EXPORTED and len(EXPORTED) >= 20, EXPORTED


@pytest.mark.parametrize("metric", sorted(EXPORTED))
def test_every_metric_has_a_panel(metric):
    assert metric in QUERIED, f"{metric} is exported but no dashboard panel queries it"


@pytest.mark.parametrize("metric", sorted(QUERIED))
def test_every_queried_metric_is_exported(metric):
    assert metric in EXPORTED, f"the dashboard queries {metric}, which the exporter does not emit"


@pytest.mark.parametrize("panel", PANELS, ids=lambda p: p["title"])
def test_every_panel_explains_itself(panel):
    assert panel.get("description", "").strip(), f'panel "{panel["title"]}" has no description'


@pytest.mark.parametrize("title,target", TARGETS, ids=[f"{p}/{t['refId']}" for p, t in TARGETS])
def test_queries_are_scoped_to_the_selected_instance(title, target):
    """Every selector filters on both variables, so two instances never mix."""
    expr = target["expr"]
    for m in re.finditer(r"\b(?:panoramax_\w+|up)\{([^}]*)\}", expr):
        assert 'job="$job"' in m.group(1) and 'instance="$instance"' in m.group(1), f"{title}: {expr}"
    assert not re.search(r"\b(?:panoramax_\w+|up)\b(?!\{)", expr.replace("on()", "")), f"{title}: {expr}"


def test_no_hardcoded_datasource():
    """Importable anywhere: every data-source reference goes through the variable."""

    def refs(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "datasource" and isinstance(value, dict):
                    yield value
                else:
                    yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)

    found = list(refs(DASHBOARD))
    assert found
    for ref in found:
        assert ref.get("uid") == "${datasource}", ref
