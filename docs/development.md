# Development

The tests are stdlib only, no framework. From a clone of the repo:

```
python3 -m unittest discover -s tests
```

Smoke coverage: the script imports and builds its parser, the formatters
behave, the extras filter skips by convention, `--undo` reads the ledger
correctly, and every `--auto` gate is pinned at the value
[documented](how-it-decides.md#unattended-tagging). CI runs the same suite on
every push; the GitHub workflow runs it on Python 3.11, 3.12 and 3.13.

## Releases

Pushing a `v*` tag to GitHub runs `.github/workflows/release.yml`, which
creates the GitHub release and attaches `audio-lang-tagger.py`. That release
is the install channel: the README points at `releases/latest/download`, so
nothing on `main` reaches users until it is tagged.

The workflow fails, and publishes nothing, when the tag does not match
`--version`. Bump `__version__` in `audio-lang-tagger.py` first, without the
leading `v`.

A beta is the same signed `vX.Y.Z` / `vX.Y.Z-public` routine with a `-beta.N`
suffix on the version: `__version__ = "1.9.0-beta.1"`, tagged
`v1.9.0-beta.1`. Any tag containing `-` becomes a pre-release, which
`releases/latest` skips, so testers fetch it by tag (README, Testing a beta)
while everyone else stays on the last stable release. Ship the final as
`v1.9.0` once the beta holds.
