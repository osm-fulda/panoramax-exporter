# Releasing

Versions follow [SemVer](https://semver.org/), derived from Conventional Commits.
Metric names are part of the public interface: renaming or removing one is a
major bump.

## Cutting a release

1. Ensure CI on `main` is green.
2. Open a PR bumping the version in **both** `pyproject.toml` and
   `exporter.py` (`__version__`) to `X.Y.Z` — `chore(release): vX.Y.Z` — and
   merge it. `tests/test_exporter.py::test_version_matches_pyproject` fails if
   the two drift apart.
3. Tag the merged bump commit:
   `git tag -a vX.Y.Z -m "vX.Y.Z" && git push origin vX.Y.Z`.
4. The `Release` workflow runs the quality gates, builds the multi-arch image,
   pushes `X.Y.Z`, `X.Y` and (for stable tags) `latest` to
   `ghcr.io/osm-fulda/panoramax-exporter`, signs the image with cosign keyless,
   generates an SBOM plus SLSA provenance, and creates a **draft** GitHub
   Release with the artifacts attached.
5. Add curated notes (Highlights / Breaking changes / Fixes) and publish:
   `gh release edit vX.Y.Z --notes-file notes.md --draft=false`.

Prerelease tags (`vX.Y.Z-rc1`) are marked as prereleases and never move `latest`.

Every push to `main` publishes a preview image at
`ghcr.io/osm-fulda/panoramax-exporter:rc` — mutable, overwritten on each build,
not a supported release.

## Verifying signatures

    cosign verify ghcr.io/osm-fulda/panoramax-exporter:X.Y.Z \
      --certificate-identity-regexp 'https://github.com/osm-fulda/panoramax-exporter/\.github/workflows/release\.yml@.*' \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com

    cosign verify-blob checksums.txt \
      --signature checksums.txt.sig --certificate checksums.txt.pem \
      --certificate-identity-regexp 'https://github.com/osm-fulda/panoramax-exporter/.*' \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com

    gh attestation verify oci://ghcr.io/osm-fulda/panoramax-exporter:X.Y.Z \
      --repo osm-fulda/panoramax-exporter

## Consumers

The GitOps deployment lives in
[osm-fulda/gitops](https://codeberg.org/osm-fulda/gitops) under
`apps/panoramax-exporter/`, and pins an exact image tag. An `updatecli` policy
there opens the bump PR once a new tag is published — no manual edit needed.
