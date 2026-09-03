# Contributing

Thanks for helping out. This exporter is deliberately small — a single
`exporter.py`, no framework, no plugin system. Keep it that way where you can.

## Getting started

```bash
python3 -m venv .venv && . .venv/bin/activate
make deps
make test
make lint
```

Run it against any Panoramax instance:

```bash
PANORAMAX_API=https://panoramax.openstreetmap.fr/api make run
curl -s localhost:9155/metrics | grep panoramax_
```

## Workflow

- Work starts from an issue. Open one (bug or enhancement) before the PR, and
  reference it from the PR with `Closes #<issue>`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `chore:` …) — the release notes are derived from them.
- `make lint` and `make test` must pass. New metrics come with a test.
- Metric names are an API: renaming or removing one is a breaking change and
  needs a `!`/`BREAKING CHANGE` commit plus a note in the PR.

## Developer Certificate of Origin

All commits must be signed off (`git commit -s`), certifying the
[DCO](https://developercertificate.org/). The `Signed-off-by` trailer must name a
human identity — the person responsible for the contribution.

## AI-assisted contributions

AI assistance is welcome. Commits produced with an AI agent additionally carry an
`Assisted-by: <Agent>:<model>` trailer (e.g. `Assisted-by: ClaudeCode:claude-opus-5`).
The human signer reviews all AI-generated code and remains responsible for its
correctness and license compliance.
