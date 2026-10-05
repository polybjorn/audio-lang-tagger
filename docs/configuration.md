# Configuration

The flag and key table is in the [README](../README.md#configuration). This page
covers how a setting is resolved, the realtime factor, connecting Sonarr and
Radarr, and what a sweep leaves out.

## Resolution order

Resolved in order: CLI flag, then `AUDIO_LANG_TAGGER_<NAME>` in the environment,
then `~/.config/audio-lang-tagger.conf`, then `/etc/audio-lang-tagger.conf`
(`KEY=value`, `#` comments), then the built-in default. A user file therefore
wins over the one config management writes. A config file accepts each key with
or without the `AUDIO_LANG_TAGGER_` prefix.

The exception is the arr connection: `SONARR_URL`, `SONARR_API_KEY`,
`RADARR_URL` and `RADARR_API_KEY` are read under those bare names only, from the
environment first and then the config files. They have no flag.

Example config:
[`audio-lang-tagger.conf.example`](../audio-lang-tagger.conf.example).

## Realtime factor

`--realtime-factor` is how many times realtime this machine transcribes,
end-to-end including the ffmpeg extract. It sets which tracks are short enough
to scan whole and what `f` estimates, nothing else; the default of 20 is
measured on a 24-core CPU, not guessed
([design notes](design-notes.md#measured-numbers)). `--show-config` prints the
whole-track budget it implies.

## Connecting Sonarr and Radarr

The arrs are only needed for `--auto` corroboration
([how it decides](how-it-decides.md#unattended-tagging)); `--no-arr` turns the
lookup off.

- **Same host, native install:** automatic. The tool reads the arr's own
  `config.xml` (default `/var/lib/sonarr/`, `/var/lib/radarr/`, overridden by
  `--sonarr-config` and `--radarr-config`) for the port and API key. Nothing to
  configure.
- **Anything else - Docker, another host, non-standard paths:** set
  `SONARR_URL` + `SONARR_API_KEY` (and the `RADARR_` pair). The key is in the
  arr's UI under Settings > General > Security; the URL is whatever you
  publish, e.g. `http://localhost:8989`.

`--show-config` probes each configured arr and prints `ok (Sonarr 4.0.19.2979)`
or what is wrong. Check it once before relying on `--auto`: at runtime a failed
lookup degrades silently to "no corroboration", which just means fewer tracks
pass the gates and more reach the card.

## What a sweep leaves out

Sweeps and queue runs skip trailers, samples and extras, by the conventions
Plex, Emby and Jellyfin share: a stem suffixed `-trailer` or `-sample`, or any
ancestor folder named `Extras/`, `Featurettes/`, `Interviews/`, `Shorts/` and
the like. It matches those conventions rather than substrings, so "Trailer Park
Boys - S01E01.mkv" survives.

This is a media-library default, and the one place the tool assumes what your
files are. If your material really does live in `interviews/` or `shorts/`,
`--no-ignore` turns the filter off; `--ignore PATTERN` adds plain substrings on
top. What a run left out is counted and reported at startup, and a file named
on the command line is always scanned: asking for a path is asking for that
path.
