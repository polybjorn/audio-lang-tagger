# audio-lang-tagger

Finds MKV audio tracks whose language flag is missing or `und`, guesses the
language by running a short sample through [whisper.cpp][], and asks for
confirmation before writing the tag with `mkvpropedit`.

Nothing in that path cares what the video is. Any Matroska file with an
unlabelled audio track qualifies: a lecture recording, a conference talk, a
home video, digitised archive footage, a film. A media library is just the
thing that produces thousands of them at once, which is what the saved queue,
the sweep and the unattended pass are shaped around.

The edit is a track-header change, not a remux: nothing is re-encoded, no data
is copied, and a 40 GB file is tagged in well under a second. Players stop
labelling those tracks "Unknown", and any later pass that strips unwanted audio
can work on the assumption that everything left is identified.

[whisper.cpp]: https://github.com/ggml-org/whisper.cpp

![The interactive card: a progress header, the track's codec, runtime and size, whisper's verdict with the words behind it, the detector reading, Sonarr's agreement, a line of what was heard, and the single-key choices along the bottom](docs/ui.svg)

## Quick start

Needs Python 3.8+ (nothing to `pip install`) and three common tools:

```bash
brew install whisper-cpp ffmpeg mkvtoolnix           # macOS
sudo pacman -S whisper-cpp ffmpeg mkvtoolnix-cli     # Arch
sudo apt install whisper.cpp ffmpeg mkvtoolnix       # Debian 13+, Ubuntu 26.04+
sudo dnf install whisper-cpp ffmpeg mkvtoolnix       # Fedora
# no whisper.cpp package (Ubuntu 24.04 and older)? snap install whisper-cpp,
# or build it: https://github.com/ggml-org/whisper.cpp
```

Fetch the language model once (~150 MB):

```bash
mkdir -p ~/.local/share/whisper
curl -L -o ~/.local/share/whisper/ggml-base.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
```

Get the script - it is one file - and point it at some video:

```bash
curl -O https://raw.githubusercontent.com/polybjorn/audio-lang-tagger/main/audio-lang-tagger.py
chmod +x audio-lang-tagger.py
./audio-lang-tagger.py --list /path/to/your/video
```

`--list` changes nothing, it just reports how many untagged tracks you have.
When you are ready to tag, run the same command without it and confirm each
track as it comes up:

```bash
./audio-lang-tagger.py /path/to/your/video
```

If anything is missing the tool says exactly what and how to install it, and
`--show-config` prints every resolved path and connection. For your PATH:
`install -m 755 audio-lang-tagger.py ~/.local/bin/`. Everything below is detail
you can come back to later.

## Usage

```bash
audio-lang-tagger.py PATH [PATH...]   # work through the given files/dirs
audio-lang-tagger.py                  # work the saved candidate queue
audio-lang-tagger.py --list           # report untagged tracks, change nothing
audio-lang-tagger.py --full           # re-sweep every configured root
audio-lang-tagger.py --auto           # tag gate-passing tracks, prompt for the rest
audio-lang-tagger.py --prescan        # unattended: scan + cache, never prompt
audio-lang-tagger.py --bulk zxx PATH  # one code over a range you have judged
audio-lang-tagger.py --plain          # line-mode prompts, no full-screen UI
audio-lang-tagger.py --ledger         # review the last 20 applied tags
audio-lang-tagger.py --undo 3         # revert the last 3 tags to und
audio-lang-tagger.py --show-config    # print resolved paths and exit
audio-lang-tagger.py --version        # print the version and exit
```

Interactive modes need a tty. Over ssh that means `ssh -t HOST
audio-lang-tagger.py`.

The interactive run is the full-screen app in the screenshot above (via
[textual][], optional - `pip install textual`; without it the tool falls back
to plain line-mode prompts). Keys are single presses, no Enter:

| Key | Does |
|---|---|
| `Enter` | tag with the suggested code |
| `c` | type a different code (`esc` cancels) |
| `d` / `f` | deep or full scan, when the samples were not enough |
| `s` | skip this file for now |
| `n` | never ask about this track again |
| `u` | undo the last decision, one level (full-screen UI only) |
| `q` | quit cleanly, flushing the scan cache and pruning the queue |

`u` re-shows the card it took back: a tag is written back to `und` with a
corrective ledger row, a never-ask comes out of the skip list.

[textual]: https://textual.textualize.io/

### What a sweep leaves out

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

## Corroboration: Sonarr/Radarr

Optional interactively, **required for `--auto`**: unattended tagging needs two
independent answers to agree - whisper hears "this is Italian", and something
that already knows what the recording is says "this was made in Italian". A
track with no metadata match is never auto-tagged.

Sonarr and Radarr are the source that is implemented. Without one the tool
still works fully interactively, minus the agree/disagree line on the card.

- **Same host, native install:** automatic. The tool reads the arr's own
  `config.xml` (default `/var/lib/sonarr/`, `/var/lib/radarr/`) for the port
  and API key. Nothing to configure.
- **Anything else - Docker, another host, non-standard paths:** set
  `SONARR_URL` + `SONARR_API_KEY` (and the `RADARR_` pair). The key is in the
  arr's UI under Settings > General > Security; the URL is whatever you
  publish, e.g. `http://localhost:8989`.

`--show-config` probes each configured arr and prints `ok (Sonarr 4.0.19.2979)`
or what is wrong. Check it once before relying on `--auto`: at runtime a failed
lookup degrades silently to "no corroboration", which just means fewer tracks
pass the gates and more reach the card.

Any source that can state a recording's intended language would serve the same
role, but the obvious candidates do not carry the fact: Jellyfin and the Kodi
NFO standard do not store original language, and TMDB does but cannot be asked
about a path ([design notes][notes]).

## How it decides

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/pipeline-dark.svg">
  <img src="docs/pipeline.svg" alt="Pipeline: untagged tracks are scanned by whisper.cpp, checked against the auto gates, and tagged by mkvpropedit; gate failures go to an interactive confirmation card; every tag lands in the ledger">
</picture>

Language detection off a 30-second sample is wrong often enough that blind
tagging is not usable, and wrong in two opposite ways: whisper's language head
fires confidently on music, and reads low on a track that opens with a title
score ([design notes][notes] carry the measurements).

So the tool separates the two questions. *Are there words here* is answered from
the transcript (character count, word count, distinct-word ratio), *which
language* from the detector - and a detection with no transcript behind it is
never offered as the default. The rest is about making a confirmation cheap:

- Tracks short enough to transcribe whole (~12 min of audio) are, so the first
  card is conclusive rather than a starting point. The detector is then asked a
  second time about the densest 30 seconds of *speech*, because its first
  reading only ever saw the opening. Longer tracks sample four windows and stop
  at the first one holding words.
- `d` (deep scan) samples 7 more windows for sparse-dialogue content; `f` (full
  scan) transcribes the whole track, the only pass where finding nothing is
  conclusive.
- Every whisper result is cached by path + mtime + track, so an interrupted
  sweep resumes for free. Measured on one file: 34s cold, 1s warm.
- Every applied tag is appended to a TSV ledger, so a bad batch or a misclick
  is reviewable with `--ledger` and reversible with `--undo`.

Every threshold was calibrated against the `base` model on a 6-core CPU. A
larger model, or material unlike what it was tuned on, will read differently -
watch `--auto` on your own content before trusting it.

### Unattended tagging

`--auto` tags without asking only when every gate passes. The constants that
tune the seven are at the top of the script, which is where to go if your
material needs different thresholds:

1. **Unanimous, confident detections** - every window at p >= 0.90, or a
   whole-track pass reaching it on the opening or the densest speech window
   (`AUTO_PROB`)
2. **Coherent transcript** - enough characters, words and distinct vocabulary
   to rule out music hallucination (`AUTO_MIN_CHARS`, `AUTO_MIN_WORDS`,
   `AUTO_MIN_UNIQUE`; long transcripts `AUTO_LONG_MIN_DISTINCT`,
   `AUTO_LONG_MIN_UNIQUE`)
3. **Corroboration** - the arr's original language matches; no metadata match
   means no auto-tagging at all
4. **Year** - 1940 or later, using the episode's own air year where Sonarr
   knows it (`AUTO_MIN_YEAR`; older content is sparse-dialogue territory)
5. **Genre** - not music or concert (`AUTO_EXCLUDE_GENRES`)
6. **One audio track** - a single non-commentary track in the file
7. **Unambiguous language** - outside the `no`/`nn`/`da`/`sv` cluster whisper
   confuses (`AUTO_EXCLUDE_ISO1`)

"No speech found" never auto-tags anything.

The intended shape for a large queue is `--prescan` in the background first
(unattended, tags gate-passers, caches everything else), then an interactive
pass that does no scanning at all. Cached files are served first either way, so
a partially prescanned queue still opens with instant cards.

### Bulk tagging

`--bulk CODE PATH...` sets one language across a range after a single
confirmation, for the calls no scan can make - a run of pre-sound-era shorts
where `zxx` (no linguistic content) versus `eng` is a human judgement about
sparse dialogue. It never scans, and a cached scan contradicting the code holds
its file back for the interactive pass. `zxx` is checked far more suspiciously
than a language code, because it claims absence.

## Reference

The [design notes][notes] cover the reasoning and the calibration behind all of
the above. What follows is lookup detail.

[notes]: docs/design-notes.md

<details>
<summary><b>Configuration</b> - flags, environment, config file</summary>

Resolved in order: CLI flag, then `AUDIO_LANG_TAGGER_<NAME>` in the environment,
then `~/.config/audio-lang-tagger.conf`, then `/etc/audio-lang-tagger.conf`
(`KEY=value`, `#` comments), then the built-in default. A user file therefore
wins over the one config management writes.

| Flag | Config key | Default |
|---|---|---|
| `--media-dir DIR` (repeatable) | `MEDIA_DIRS` (`:`-separated) | none - only `--full` and queue runs need it |
| `--state-dir DIR` | `STATE_DIR` | `$XDG_STATE_HOME/audio-lang-tagger` |
| `--queue-file PATH` | `QUEUE_FILE` | `untagged_audio.json` in the state dir |
| `--model PATH` | `MODEL` | first existing of `~/.local/share/whisper/ggml-base.bin`, `/var/lib/whisper/ggml-base.bin`, `/usr/share/whisper.cpp/ggml-base.bin`, `/usr/local/share/whisper.cpp/ggml-base.bin` |
| `--whisper-bin PATH` | `WHISPER_BIN` | `whisper-cli` / `whisper-cpp` / `whisper` on PATH |
| `--realtime-factor N` | `REALTIME` | `12` |
| `--sonarr-config PATH` | `SONARR_CONFIG` | `/var/lib/sonarr/config.xml` |
| `--radarr-config PATH` | `RADARR_CONFIG` | `/var/lib/radarr/config.xml` |
| `--ignore PATTERN` (repeatable) | `IGNORE` (`:`-separated) | trailers, samples, extras folders |
| `--no-ignore` | - | filter enabled |
| `--no-arr` | - | arr lookup enabled |
| `--jobs N` | - | `2` |

`--realtime-factor` is how many times realtime this machine transcribes,
end-to-end including the ffmpeg extract. It sets which tracks are short enough
to scan whole and what `f` estimates, nothing else; the default of 12 is
measured, not guessed ([design notes][notes]). Example config:
[`audio-lang-tagger.conf.example`](audio-lang-tagger.conf.example).

</details>

<details>
<summary><b>State files</b> - the ledger, and undoing a tag</summary>

Everything lives in the state dir; none of it is precious except the ledger:

| File | What it is |
|---|---|
| `lang_tagger_tags.tsv` | append-only ledger of every applied tag |
| `lang_tagger_scans.json` | whisper results by path + mtime + track |
| `lang_tagger_skips.txt` | tracks answered with `n` |
| `untagged_audio.json` | the candidate queue |
| `lang_tagger.{state,instance}.lock` | concurrency guards |

A row is `timestamp, path, track, und->code, mode, probability, chars`, where
mode is `auto`, `manual`, `bulk` or `undo`.

`--ledger [N]` prints the tail of it, which is how you check what `--auto` did
while you were not watching. `--undo N` reverts the last N tags still in force,
after one confirmation. An undo appends its own corrective row rather than editing history, so a track
reverted and later re-tagged counts once, at its latest value. A track whose
header no longer says what the ledger recorded is left alone and reported:
something other than this tool changed it. For a single file by hand, the row
gives `mkvpropedit` everything it needs:

```bash
mkvpropedit "FILE" --edit track:a1 --set language=und
```

</details>

<details>
<summary><b>Queue format</b> - feeding the tool from an external scanner</summary>

`--queue-file` points at JSON an external scanner writes. Only `paths` is
required:

```json
{"date": "2026-08-09", "files": 1500, "tracks": 1503, "paths": ["/srv/media/..."]}
```

`date` labels the run on screen, `files`/`tracks`
seed the workload counter before the background recount lands. Without a queue,
the first run falls back to a full sweep and writes one on the way out.
Finished files are pruned on exit, so the counter starts from a true number.

The natural writer is a health pass that already probes every stream: the queue
costs it nothing extra, and reading `lang_tagger_skips.txt` back lets it leave
out the tracks answered with `n`. Both tools must point at the same state dir
for that - the one setting they have to agree on.

</details>

<details>
<summary><b>Concurrency</b> - jobs, threads, parallel runs</summary>

`--jobs` scans run at once, at `cpu_count / jobs` whisper threads each. On a
6-core box 2 jobs beat 1/3/6 and was still only 1.28x one job: whisper already
parallelises internally and the work is memory-bandwidth bound, so raising it
buys nothing.

Two runs at once are survivable but not recommended: writes to the shared JSON
files are read-modify-write under an `flock` so they merge rather than clobber,
and a second instance is announced (a second `--prescan` is refused). What is
not safe is both runs reaching the same file.

</details>

<details>
<summary><b>Tests</b></summary>

Stdlib only, no framework. From a clone of the repo:

```
python3 -m unittest discover -s tests
```

Smoke coverage: the script imports and builds its parser, the formatters
behave, the extras filter skips by convention, `--undo` reads the ledger
correctly, and every `--auto` gate is pinned at the value documented above. CI
runs the same suite on every push.

</details>

## Scope

Matroska only: the write is `mkvpropedit --set language=`, an in-place MKV
track-header edit. MP4/MOV keep their language in the `mdhd` box and would need
a second backend, which does not exist yet. Bare audio files are out of scope on
purpose - an mp3 has no per-track language header to be wrong about, only a tag
field, so the problem this tool solves does not arise.

A personal tool, published because the tools that already do this tag
unattended on a confidence threshold, and the [design notes][notes] measure why
a threshold cannot carry that weight. [ULDAS][uldas] covers more ground
(subtitles, remuxing, a web UI, GPU) and is the better fit for hands-off batch
tagging. This one assumes you would rather confirm. Do not point both at one
library: two tools writing track headers, one ledger between them.

If you also run a track stripper, this complements it rather than competing:
[radarr-striptracks][striptracks] and its like have to keep every `und` track,
because removing one blind risks a file with no audio left. Identify them first
and that pass can decide.

Bug reports with a `--version` and a ledger row are welcome.

[uldas]: https://github.com/netplexflix/MKV-Undefined-Audio-Language-Detector
[striptracks]: https://github.com/TheCaptain989/radarr-striptracks
