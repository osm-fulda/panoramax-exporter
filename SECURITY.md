# Security Policy

## Supported versions

The latest released version is supported. Fixes go into a new release rather
than into patches for older tags.

## Reporting a vulnerability

Report privately via GitHub's
[private vulnerability reporting](https://github.com/osm-fulda/panoramax-exporter/security/advisories/new).
Please do not open a public issue for a security problem.

Expect an acknowledgement within a week. If the report is confirmed we agree a
disclosure timeline with you before publishing an advisory.

## Scope notes

The exporter holds two kinds of credential: an optional Panoramax admin bearer
token (`PANORAMAX_TOKEN`) and optional read-only Postgres credentials. Both come
from the environment. The `/metrics` endpoint is unauthenticated by design and
must not be exposed publicly — the per-user series and report counts it exposes
are aggregate, but the endpoint is meant for an in-cluster Prometheus.
