# Changelog

Each tag's section here becomes its GitHub release notes.

## v1.9.2

- A file now finds its title's original language when Radarr or Sonarr store
  the library through a symlink the sweep does not follow, for example
  `/mnt/storage` pointing at `/storage`. Before, no file matched, so `--auto`
  never had the arr's corroboration and left every file to an interactive run.

## v1.9.0

- Requires Python 3.11 or newer (was 3.8).
- Norwegian (`no`) now auto-tags under `--auto` on the same gates as other
  languages. `nn`, `da` and `sv` stay excluded, since whisper confuses them
  with each other and with `no`.
- Install from GitHub releases: `releases/latest/download/audio-lang-tagger.py`
  serves the newest stable release. Betas are published as pre-releases, which
  `latest` skips.
- The README is split into pages under `docs/`, and SECURITY.md is added.

## v1.8.2

- The realtime factor defaults to 20 (was 12), measured end to end on a
  24-core host. `--realtime-factor` re-tunes it for slower or faster hardware.

## v1.8.1

First public release.

- A path that does not exist now stops the run. It used to be skipped, so a
  typo or an unmounted share reported a clean library.
- The `--auto` gates and the coherence floor are covered by the test suite, so
  the thresholds the README documents fail CI if they drift.
