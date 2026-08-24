#!/usr/bin/env python3
"""
Interactive audio language tagger
=================================
Finds MKV audio tracks with a missing/und language flag, guesses the language
by running a short sample through whisper.cpp, and asks for confirmation
before tagging losslessly in place with mkvpropedit (header edit, no remux).

Tagged tracks fix the "Unknown" audio labels media servers show, and let a
library-cleanup pass strip unwanted tracks more confidently. A wrong tag here
is display damage only, never data loss (the edit is reversible from the
ledger, and nothing deletes audio) - but every tag is still confirmed
interactively.

One-time setup: install whisper.cpp and fetch a model.
  pacman -S whisper-cpp          # Arch
  apt install whisper.cpp        # Debian/Ubuntu (or build from source)
  brew install whisper-cpp       # macOS
  mkdir -p ~/.local/share/whisper
  curl -L -o ~/.local/share/whisper/ggml-base.bin \\
    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
Also needs ffmpeg/ffprobe and mkvtoolnix (mkvpropedit, mkvmerge, mkvextract).

Usage (interactive modes need a tty; over ssh use `ssh -t HOST ...`):
  audio-lang-tagger.py                  # work the saved candidate queue
  audio-lang-tagger.py --plain          # line-mode prompts, no full-screen UI
  audio-lang-tagger.py --auto           # auto-tag gate-passing tracks, prompt rest
  audio-lang-tagger.py --prescan        # unattended: scan+cache, tag gate-passers, never prompt
  audio-lang-tagger.py --jobs N         # concurrent scans (default 2)
  audio-lang-tagger.py --full           # re-sweep every configured media dir
  audio-lang-tagger.py PATH [PATH...]   # limit to given files/dirs
  audio-lang-tagger.py --list           # report untagged tracks, no prompts
  audio-lang-tagger.py --bulk CODE PATH # one code over a judged range

Configuration comes from CLI flags, then AUDIO_LANG_TAGGER_* environment
variables, then ~/.config/audio-lang-tagger.conf, then
/etc/audio-lang-tagger.conf (plain KEY=value), then built-in defaults - so a
user file wins over the one config management writes. --show-config prints
what resolved.
Only --media-dir has no default: a run given explicit PATHs needs nothing set,
while --full and the queue run need to know what the library is.

--auto tags without prompting only when ALL gates pass: every sample position
detects the same language at >=0.90 (or, on a track short enough to be scanned
whole, that single gap-free pass reaches >=0.90 on either the opening 30s or
the densest 30s of speech - see below), the
transcript is coherent (>=40 chars, >=8 words, and either >=50% unique words or
- once a transcript is long enough for that ratio to sag - >=100 distinct words
at >=20% unique; music hallucinations are degenerate repetition or one
stretched word, and stay degenerate however long they run), Sonarr/Radarr's original
language agrees, year >= 1940 (the episode's own air year where Sonarr knows
it, not the series year - an anthology's start year blocks everything and
discriminates nothing), genre is not music/concert, the file has a
single non-commentary audio track, and the language is outside the
no/nn/da/sv cluster whisper confuses. Every applied tag - auto and
manual - is appended to lang_tagger_tags.tsv in the state dir (old value
always und), so any batch or misclick is reviewable and reversible. "No speech found" never auto-tags anything.

Whisper only ever detects language from 30 seconds of audio, and on a
whole-track pass those are the FIRST 30 - which on a cartoon is the musical
title card. That put the gate at odds with the evidence: 8 known-dialogue
animated shorts transcribed cleanly end to end, but only 4 cleared 0.90 on their
opening (a 1940 sparse-dialogue short: 99 coherent words, p=0.42). So after a whole-track
transcription the detector is asked a second time about the 30s window that
actually held the most words, and the gate grades the better of the two
readings. All 8 then cleared 0.90, while a degenerate music case (a 1956
sung-through short: 43 distinct words inside 425) stayed at 0.81 and is rejected by the
coherence floor too. Constants and the full calibration sit at
SPEECH_WINDOW_SECONDS. Distinct-word count was measured as the alternative and
rejected: the sparse-dialogue short has 50 distinct words, below sung shorts
that must not pass (52 in one 1956 case), so no threshold separates them.

The card (never the auto gates) uses the same two readings once more: a
degenerate transcript whose better reading still clears 0.90 is presented as
that language instead of zxx, because music never reaches that bar - it is
sparse real speech drowned in repetition padding, and the human decides.

--bulk CODE PATH... sets one language across a whole range after a single
confirmation, for the calls no scan can make: the ~200 pre-1940 animated shorts
and a dialogue-free cartoon series, where zxx-vs-eng is a human
judgement about sparse dialogue. It never scans, PATHs are mandatory, and a
cached scan that contradicts the code holds its file back for the interactive
pass. zxx is checked far more suspiciously than a language code is, because it
claims absence: 20 distinct words anywhere in the track is enough to hand the
file to the human, where claiming one language over another asks for a full
coherent transcript first.
Each file lands in the ledger as its own und->code row with mode 'bulk'.

The default run reads a candidate queue an external library scanner saved (so
it starts prompting in seconds instead of re-probing the library) and
re-verifies each file before prompting. Files imported after that scan only
show up under --full or an explicit PATH. The queue is a small JSON file -
{"date": "YYYY-MM-DD", "files": N, "tracks": N, "paths": [...]} - and its
location is --queue-file; without one, the first run falls back to a full
sweep and later runs read the queue this tool prunes on the way out.

Long sweeps: every whisper result is cached by path+mtime+track in
.cleanup/lang_tagger_scans.json, so a scan is paid for once and an interrupted
run resumes for free (mtime moves whenever a file is retagged or replaced, so a
stale entry can never be served). The intended shape for a big queue is
--prescan in the background first - unattended, needs no tty, tags whatever the
--auto gates allow and caches everything else - then an interactive pass that
does no scanning at all. Measured on one file: 34s cold, 1s warm, and the first
card in 0.5s instead of 34s. Cached files are served ahead of unscanned ones,
so a partially prescanned queue still opens with instant cards.

Scans run --jobs at a time (default 2, at cpu_count/jobs whisper threads each).
Benchmarked on a 6-core box that is only 1.28x one job, because whisper
already parallelises internally, so raising it further buys nothing. Cards
therefore arrive as scans finish rather than in queue order, which is why the
counter is a running tally.

Two runs at once are survivable but not recommended. Writes to the scan cache
and the saved queue are read-modify-write under an flock, so they merge instead
of clobbering, and a second instance is detected and announced (a second
--prescan is refused outright). What still isn't safe: both runs compete for the
same cores, and both can reach the same file and tag it twice. Prefer finishing
the prescan first.

Scan strategy: a track short enough to transcribe whole inside
SCAN_BUDGET_SECONDS of whisper time (~12 min of audio) is scanned whole on the
first pass, so its card is conclusive with no escalation. Longer tracks sample
four 30s windows and stop at the first one containing actual words. The split
is by runtime rather than by movie/episode because what breaks sampling is
sparse dialogue, and that correlates with short runtime (silent-era shorts);
feature-length material has dense enough dialogue that any window hits it.

The interactive run is a full-screen UI (textual) when the library is
installed, with the same evidence card plus a progress header, a live scan
status line and a strip of recent decisions; --plain keeps the classic
scrolling prompts, and missing textual falls back to them with a notice.
Keys are the same in both, except typing a replacement code starts with 'c'
in the full-screen UI.

Prompt keys:
  Enter   accept the guess
  <code>  type a 3-letter ISO 639-2 code to use instead ('c' first in the
          full-screen UI)
  d       deep scan: sample up to 7 more windows across the track, stopping
          at the first coherent transcript, then re-show the prompt. For
          sparse-dialogue content where the standard windows hit music or
          silence. Windows are disjoint from the first pass's, cost is bounded
          by 7x30s of audio and usually less since it exits early, and the
          redisplayed card reports how many windows it actually reached.
          Evidence only - never tags by itself.
  f       full scan: transcribe the ENTIRE track - the only scan with no
          sampling gap, so finding nothing is conclusive for this transfer.
          No early exit, so the duration/WHISPER_REALTIME cost is always paid
          in full (the prompt shows the estimate); asks first on files over
          30 min. Locks the language to the current guess, so it confirms
          words rather than re-detecting which language they are.
  s       skip this file for this run only
  n       never ask about this track again (recorded in skip-list)
  u       undo the last decision and re-show its card (full-screen UI only,
          single level): a tag is written back to und with a corrective
          ledger row, a never-ask is removed from the skip list
  q       quit

Both keys are hidden on a track that was already scanned whole, where 'f'
would redo identical work. Where they do apply, the card's runtime row and the
'f' estimate show what each would cost.

The workload counter starts from the saved queue's total, minus anything the
tag log and skip list show as already done, and drops its "~" once a
background recount of the queue lands (~2.5 min for 1500 files, overlapping
the first cards). The recount matters because the tag log only sees this
tool's own work: a 1500-file queue measured 1377 still outstanding, and only 7
of that difference came from here. Finished files are pruned from the saved
queue on exit. A run given an explicit PATH counts against that scope instead:
the weekly total describes a different run, and using it showed an 18-file
season as [1/~1500].

Single-letter keys are case-insensitive - each action has its own letter,
so capitalization can't change what a keypress does.

A detected language with zero transcript evidence is never the Enter
default (that is the music-hallucination case): accepting it requires
typing the code. Once a deep scan has sampled the whole spread without
finding words, absence becomes evidence and Enter suggests zxx instead.
"""

import argparse
import atexit
import concurrent.futures as cf
import contextlib
import fcntl
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

__version__ = "1.7.1"

# ── Runtime configuration ───────────────────────────────────────────────────
# Every path below is resolved by configure() before any work starts, from
# flags, then AUDIO_LANG_TAGGER_* env vars, then the config files, then these
# defaults. They stay module-level names so the rest of the file reads them
# plainly rather than threading a config object through every function.

CONFIG_FILES = ["/etc/audio-lang-tagger.conf",
                "~/.config/audio-lang-tagger.conf"]
ENV_PREFIX = "AUDIO_LANG_TAGGER_"

# Library roots swept by --full and by the fallback when no queue exists. No
# default: this is the one thing that cannot be guessed, and a run given
# explicit PATHs never needs it.
MEDIA_DIRS = []
# Everything this tool writes lives here: the skip list, the tag ledger, the
# scan cache, the two lock files and (by default) the candidate queue.
STATE_DIR = ""
SKIP_FILE = ""
# Last known untagged totals plus the candidate paths. Written by whatever
# scanner feeds this tool, and pruned here as files are finished.
UNTAGGED_STATE_FILE = ""
TAG_LOG_FILE = ""
# Whisper results keyed by path+mtime+track (see SCAN_CACHE_FILE notes below).
SCAN_CACHE_FILE = ""
STATE_LOCK_FILE = ""
INSTANCE_LOCK_FILE = ""
# Strips this prefix off filenames on screen. Derived from MEDIA_DIRS, so a
# library at /srv/media/{movies,series} shows "movies/Title/file.mkv".
DISPLAY_ROOT = None

WHISPER_BINS = ["whisper-cli", "whisper-cpp", "whisper"]
WHISPER_BIN_OVERRIDE = None
# Searched in order. The first is where this tool's own setup text puts a
# model; the rest are where the common packages leave one.
MODEL_PATHS = [
    os.path.expanduser(
        os.environ.get("XDG_DATA_HOME", "~/.local/share")
        + "/whisper/ggml-base.bin"),
    "/var/lib/whisper/ggml-base.bin",
    "/usr/share/whisper.cpp/ggml-base.bin",
    "/usr/local/share/whisper.cpp/ggml-base.bin",
]

# whisper pads every clip to a fixed 30s encoder window, so a 20s sample cost
# exactly what a 30s one does (measured: 5s/10s/30s clips all ~2.3-2.6s).
SAMPLE_SECONDS = 30
# Fractions of duration, sampled in order; interactively it stops at the first
# window with a coherent transcript. Four windows cost about what the old two
# did, because each window is now one whisper invocation instead of two.
SAMPLE_POSITIONS = [0.20, 0.40, 0.60, 0.80]
MIN_PROB = 0.55  # below this the guess is shown as low-confidence

# --auto gates, calibrated 2026-07-27 against known-silent (1922-27 animated
# shorts) vs known-dialogue files: music-only samples never exceeded p=0.86
# and only ever "transcribed" degenerate repetition loops, while dialogue
# cleared p>=0.92 with coherent text. Sparse dialogue CAN sample as silence
# (a 1929 part-talkie: 0 words in 2x20s), so absence of speech never
# auto-tags anything - auto-accept only fires on unanimous positive
# evidence, and everything else falls through to the prompt.
AUTO_PROB = 0.90           # every sampled position must reach this
AUTO_MIN_CHARS = 40        # transcript alpha chars for "coherent"
AUTO_MIN_WORDS = 8         # a stretched "Shhhh..." is 100+ chars, 1 word
AUTO_MIN_UNIQUE = 0.5      # unique-word ratio (kills repetition loops)
# Second arm of the coherence test, for long transcripts. The unique-word ratio
# falls as a transcript grows - the same handful of function words keeps
# recurring - so a flat 0.5 floor rejects the most obviously linguistic tracks
# in the library. Measured 2026-07-30 across 19 shorts: the known-dialogue ones
# run 0.28 (a 1959 featurette, 2045 words) to 0.58, and a 1953 dialogue short
# sits at 0.49 with 426 words of clean dialogue already detected at p=0.93.
# Distinct-word count is what separates those from a repetition loop, which is
# degenerate however long it runs: the worst real case (the 1956 sung-through
# short) is 43 distinct words inside 425 at ratio 0.10, and the known "La la la" loops
# hold 3 to 12. Both floors have to be cleared, so this only ever opens for a
# transcript carrying a lot of varied language.
AUTO_LONG_MIN_DISTINCT = 100
AUTO_LONG_MIN_UNIQUE = 0.20
AUTO_MIN_YEAR = 1940       # pre-sound-era / sparse-dialogue zone prompts
AUTO_EXCLUDE_GENRES = {"music", "concert"}
AUTO_EXCLUDE_ISO1 = {"no", "nn", "da", "sv"}  # whisper flips within cluster
# --bulk zxx safety net. zxx claims a track holds NO linguistic content, so the
# bar for doubting that claim is deliberately far below the bar for auto-tagging
# anything: the risk is one-sided (a wrong zxx is a real error, an extra prompt
# costs nothing), which is the opposite of what the --auto gates are tuned for.
# Whisper hallucinates freely over music, but always as repetition - the known
# loops carry 3 to 12 distinct words inside hundreds - so distinct vocabulary is
# what separates "heard something" from "looped a noise". At 20, a 1933 short (37
# distinct) and the 1956 sung-through short (43) go to the human while the loops and the
# silent tracks pass through. Roughly 40% of the pre-1940 shorts land above it.
BULK_ZXX_MIN_DISTINCT = 20
# Measured on a 6-core Alder Lake box (base model, CPU backend): transcription
# alone runs 16-21x realtime, but end-to-end with the ffmpeg extract it is
# 10.5-15x (music-heavy tracks sit at the low end, since failed decodes retry
# through the temperature fallback). 12 is the conservative middle and drives
# both the whole-track budget and the 'f' estimate. Hardware-calibrated, so
# --realtime-factor re-tunes it for a slower or faster host without touching
# any of the gates.
WHISPER_REALTIME = 12
# A track whose whole-track pass fits this much wall-clock is scanned whole
# instead of sampled - conclusive first card, no 'd' press. Runs in the
# prefetch thread, so only the first card of a run waits on it.
SCAN_BUDGET_SECONDS = 60
FULL_SCAN_MAX_SECONDS = SCAN_BUDGET_SECONDS * WHISPER_REALTIME  # 12 min
# Whisper reads language from the FIRST 30s of whatever audio it is handed and
# nothing else, so a cartoon that opens on music detects at 0.4-0.6 however
# unambiguous the rest of the track is. A whole-track transcript knows where
# the words actually are, so the detector gets a second reading on the densest
# 30s of speech and the gate takes whichever window read higher.
#
# Measured 2026-07-30 on 8 known-dialogue vs 7 known-music/song shorts. All 8
# dialogue shorts cleared 0.90 on the speech window where only 4 did on the
# opening (the 1940 sparse-dialogue short: 0.42 -> 0.91, a 1953 featurette:
# 0.37 -> 0.99), while the one degenerate case (the 1956 sung-through short,
# 43 distinct words in 425) stayed at 0.81 and is rejected by the coherence floor as well. The
# window can also read *lower* than the opening (a 1933 short: 0.97 ->
# 0.88), which is why this is max() of the two readings rather than a
# replacement: it can only ever admit, never withdraw a tag that passes today.
#
# NOTE: the window has to be cut into its own file. whisper's --offset-t moves
# the transcript but NOT the detection, which is always read from the start of
# the loaded audio (measured: an offset of 61000 ms returned the whole-track
# reading of 0.4215, while the same 30s cut out with ffmpeg returned 0.9144).
SPEECH_WINDOW_SECONDS = 30
# The extra detect-only pass (--detect-language exits before transcribing)
# measured 1.4s next to a 25-45s whole-track scan, and only runs when the track
# holds enough words to aim at - there is nothing to point the detector at on a
# silent one.
DETECT_ONLY_TIMEOUT = 180

# The scan cache (SCAN_CACHE_FILE) keys whisper results by path+mtime+track. A
# whole-track scan is 25-45s, so sweeping a 436-file queue is ~3.3h; without
# it, an interrupt, a 'q', or a later second pass repeats all of that.
# STATE_LOCK_FILE serialises read-modify-write on the two shared JSON files
# (scan cache and saved queue), so a prescan and an interactive pass can run
# together without either one's blind overwrite dropping the other's work, and
# INSTANCE_LOCK_FILE is held for the whole process lifetime purely to detect a
# second instance. All three are resolved into STATE_DIR by configure().
# Entries between disk writes. Each scan is 25-45s, so this bounds what a hard
# kill can lose to a couple of minutes; the write itself is trivial next to
# whisper. Normal exits, quits and unhandled exceptions all flush via atexit.
SCAN_CACHE_FLUSH_EVERY = 5
# Long enough to read the warning and hit Ctrl-C, short enough not to nag on a
# deliberate second pass.
INSTANCE_WARN_PAUSE = 3

# Concurrency, benchmarked on a 6-core box over four ~430s tracks:
#   1 job x 4 threads  104 files/h   (whisper's own default)
#   2 jobs x 3 threads 134 files/h   <- best
#   3 jobs x 2 threads 128 files/h
#   6 jobs x 1 thread  102 files/h
# Only 1.28x, because whisper already parallelises internally and the work is
# memory-bandwidth bound rather than core-starved. Not worth oversubscribing.
SCAN_JOBS = 2
WHISPER_THREADS = 3

# whisper.cpp reports ISO 639-1; mkvpropedit wants ISO 639-2 (bibliographic,
# bibliographic forms specifically: fre/ger/dut/chi, not fra/deu/nld/zho).
ISO1_TO_ISO2B = {
    "ar": "ara", "bg": "bul", "bn": "ben", "ca": "cat", "cs": "cze",
    "cy": "wel", "da": "dan", "de": "ger", "el": "gre", "en": "eng",
    "es": "spa", "et": "est", "eu": "baq", "fa": "per", "fi": "fin",
    "fr": "fre", "gl": "glg", "he": "heb", "hi": "hin", "hr": "hrv",
    "hu": "hun", "id": "ind", "is": "ice", "it": "ita", "ja": "jpn",
    "ko": "kor", "la": "lat", "lt": "lit", "lv": "lav", "mk": "mac",
    "ms": "may", "nl": "dut", "nn": "nno", "no": "nor", "pl": "pol",
    "pt": "por", "ro": "rum", "ru": "rus", "sk": "slo", "sl": "slv",
    "sr": "srp", "sv": "swe", "sw": "swa", "ta": "tam", "te": "tel",
    "th": "tha", "tl": "tgl", "tr": "tur", "uk": "ukr", "ur": "urd",
    "vi": "vie", "zh": "chi",
}
# mul = multiple languages (no single one dominates), zxx = no linguistic
# content (music-only track)
VALID_ISO2 = set(ISO1_TO_ISO2B.values()) | {"mul", "zxx"}

DETECT_RE = re.compile(r"auto-detected language:\s*([a-z]+)\s*\(p\s*=\s*([0-9.]+)")
# Timestamped segment lines on stdout: [00:01:01.000 --> 00:01:03.000]  text
SEGMENT_RE = re.compile(
    r"\[(\d\d):(\d\d):(\d\d\.\d+)\s*-->\s*(\d\d):(\d\d):(\d\d\.\d+)\]\s*(.*)")
# Bracketed annotations ([Music], (applause), *sighs*) are whisper describing
# the audio rather than transcribing it, so they are not words.
ANNOTATION_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")
WORD_RE = re.compile(r"[A-Za-zÀ-ÿ']{2,}")

# Sonarr/Radarr originalLanguage names -> ISO 639-2/B
ARR_LANG_NAME_TO_CODE = {
    "arabic": "ara", "bulgarian": "bul", "chinese": "chi", "czech": "cze",
    "danish": "dan", "dutch": "dut", "english": "eng", "estonian": "est",
    "finnish": "fin", "french": "fre", "german": "ger", "greek": "gre",
    "hebrew": "heb", "hindi": "hin", "hungarian": "hun", "icelandic": "ice",
    "indonesian": "ind", "italian": "ita", "japanese": "jpn", "korean": "kor",
    "latvian": "lav", "lithuanian": "lit", "malay": "may", "norwegian": "nor",
    "persian": "per", "polish": "pol", "portuguese": "por", "romanian": "rum",
    "russian": "rus", "slovak": "slo", "slovenian": "slv", "spanish": "spa",
    "swedish": "swe", "tamil": "tam", "telugu": "tel", "thai": "tha",
    "turkish": "tur", "ukrainian": "ukr", "vietnamese": "vie",
}

# Sonarr/Radarr are optional corroboration, and only the --auto gates need
# them. Reading config.xml is the zero-setup path when this runs on the same
# host as the arrs; SONARR_URL + SONARR_API_KEY (and the RADARR_ pair) reach a
# remote one, and --no-arr turns the whole lookup off.
SONARR_CONFIG = "/var/lib/sonarr/config.xml"
RADARR_CONFIG = "/var/lib/radarr/config.xml"
SONARR_URL = None
SONARR_API_KEY = None
RADARR_URL = None
RADARR_API_KEY = None
USE_ARR = True

# ── Terminal colors (plain when piped or NO_COLOR is set) ───────────────────

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)


def bold(t):
    return c("1", t)


def dim(t):
    return c("2", t)


def green(t):
    return c("32", t)


def yellow(t):
    return c("33", t)


def red(t):
    return c("31", t)


def cyan(t):
    return c("36", t)


def num(n):
    """Counts are the payload of every status line, so they get one consistent
    colour. Without it the eye has to read each sentence to find its number."""
    return bold(cyan(n))


def plural(n, word, suffix="s"):
    return f"{n} {word}{'' if n == 1 else suffix}"


def term_width(default=100):
    return shutil.get_terminal_size((default, 24)).columns


def ellipsize(text, reserve=0):
    """Clip to the terminal so a status line can't wrap mid-filename. The card
    already puts long names on their own line; this holds the header to the
    same standard.

    Counts visible characters only - measuring the raw string made every
    colour code eat into the budget and clipped lines that fit fine."""
    width = max(40, term_width() - reserve)
    if len(ANSI_RE.sub("", text)) <= width:
        return text
    out, shown, i = [], 0, 0
    while i < len(text) and shown < width - 1:
        m = ANSI_RE.match(text, i)
        if m:  # copy escapes through without spending budget on them
            out.append(m.group())
            i = m.end()
            continue
        out.append(text[i])
        shown += 1
        i += 1
    return "".join(out) + "…" + ("\033[0m" if USE_COLOR else "")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LABEL_COL = 9  # visible width of the label column in card rows
# Wide enough for the widest value the card produces ("no speech detected",
# 18) plus a two-space gap, so the detail column starts at the same offset on
# every row. 14 was tried for a tighter gap on typical rows, but the overflow
# rows breaking the grid read worse than the extra eye-travel.
VALUE_COL = 20  # visible width of the value column


def vpad(text, width):
    """Pad to a visible width, ignoring ANSI color codes."""
    return text + " " * max(0, width - len(ANSI_RE.sub("", text)))


def row(label, value, extra=""):
    """Card row: dim label, short value, optional detail column.

    The value column is padded whether or not this row carries detail, so the
    detail column lines up down the whole card instead of appearing to float
    only on the rows that happen to have it, and detail sits behind a dim
    rule so the grid reads as columns. A value wider than the column would
    push its rule out of line, so it gets a two-space gap instead of
    touching it."""
    val = vpad(value, VALUE_COL)
    if extra and len(ANSI_RE.sub("", value)) > VALUE_COL - 2:
        val += "  "
    sep = dim("│ ") if extra else ""
    print(f"  {dim(f'{label:<{LABEL_COL}}')}{val}{sep}{extra}".rstrip())


def wide_row(label, text):
    """Card row whose text spans both columns - transcripts and notes are far
    too long for the value column and would shove their own detail out of
    alignment."""
    print(f"  {dim(f'{label:<{LABEL_COL}}')}{text}".rstrip())


def fmt_duration(seconds):
    if not seconds:
        return "?"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}h{mins:02d}m" if hours else f"{mins}m{secs:02d}s"


def fmt_size(nbytes):
    if not nbytes:
        return "?"
    gib = nbytes / 1024 ** 3
    return f"{gib:.1f} GB" if gib >= 1 else f"{nbytes / 1024 ** 2:.0f} MB"


_arr_series = {}          # series path -> (base_url, api_key, series_id)
_episode_years = {}       # series path -> {episode file path: air year}
_episode_years_lock = threading.Lock()


def arr_endpoint(url, api_key, config):
    """(base_url, api_key) for one arr, or (None, None) when unreachable.

    An explicit URL + key wins, so the tool can consult an arr on another host.
    Otherwise the local config.xml supplies both, which needs no setup at all
    when this runs beside the arrs."""
    if url and api_key:
        return url.rstrip("/"), api_key
    try:
        text = Path(config).read_text()
    except OSError:
        return None, None
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
    key = api_key or (m.group(1) if m else None)
    m = re.search(r"<Port>(\d+)</Port>", text)
    port = m.group(1) if m else None
    if not key or not port:
        return None, None
    return f"http://localhost:{port}", key


def arr_probe(base_url, api_key):
    """One-line connection verdict for --show-config: the arr's name and
    version on success, otherwise what is wrong and which setting to check.
    Nothing else in the tool reports a misconfigured arr - lookups degrade
    silently by design - so this is where a bad URL or key becomes visible."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-w", r"\n%{http_code}", "--max-time", "10",
             "-H", f"X-Api-Key: {api_key}",
             f"{base_url}/api/v3/system/status"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "FAILED: no response (check the URL and that the arr is up)"
    body, _, code = result.stdout.rpartition("\n")
    if result.returncode != 0 or not code.isdigit() or code == "000":
        return "FAILED: no response (check the URL and that the arr is up)"
    if code == "401":
        return "FAILED: 401 unauthorized (check the API key)"
    if code != "200":
        return f"FAILED: HTTP {code} (check the URL - is this the arr's base URL?)"
    try:
        status = json.loads(body)
        return f"ok ({status.get('appName', 'arr')} {status.get('version', '?')})"
    except json.JSONDecodeError:
        return "FAILED: HTTP 200 but not an arr API (check the URL)"


def build_expected_map():
    """{title_path: (iso2_code, 'Radarr'|'Sonarr', year, genres)} from the
    arr APIs.

    Corroboration: a title's original language says nothing about
    dubs/commentary tracks, so on its own it never auto-applies - but it is
    one of the required --auto gates, and year/genres feed the gates that
    keep pre-1940 and music content interactive.
    """
    expected = {}
    if not USE_ARR:
        return expected
    for url, api_key, config, key, source in [
        (RADARR_URL, RADARR_API_KEY, RADARR_CONFIG, "movie", "Radarr"),
        (SONARR_URL, SONARR_API_KEY, SONARR_CONFIG, "series", "Sonarr"),
    ]:
        base_url, api_key = arr_endpoint(url, api_key, config)
        if not base_url:
            continue
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"X-Api-Key: {api_key}",
                 f"{base_url}/api/v3/{key}"],
                capture_output=True, text=True, timeout=30,
            )
            items = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            path = item.get("path", "")
            name = item.get("originalLanguage", {}).get("name", "").lower()
            code = ARR_LANG_NAME_TO_CODE.get(name)
            year = item.get("year") or None
            genres = {g.lower() for g in item.get("genres", [])
                      if isinstance(g, str)}
            if path and code:
                expected[path] = (code, source, year, genres)
            # Remember how to reach this series' episodes, so the year gate can
            # later ask for the episode's own air year (see episode_year).
            if path and source == "Sonarr" and item.get("id"):
                _arr_series[path] = (base_url, api_key, item["id"])
    return expected


def episode_year(filepath, series_path):
    """Air year of the episode owning `filepath`, or None when unknown.

    The series year is the wrong signal for an anthology. Sonarr reports 1921
    for one shorts anthology, so a >=1940 gate blocks a 1955 talkie for the
    same reason as a 1928 silent - the gate stops discriminating rather than
    being cautious, and for that series it blocked all 499 episodes.

    Measured before adopting it: airDateUtc is present on 888/888 episodes, 486
    of 489 queued files match episodeFile.path exactly, and the air year agrees
    with the season-number-as-year convention on all 465 checkable episodes
    with zero disagreements. It also recovers the specials, whose season 0
    carries real air years (1940-1948) that no season-number heuristic could.

    One API call per series, cached for the run. Unknown stays None and the
    caller keeps the series year, so the fallback remains conservative.
    """
    conn = _arr_series.get(series_path)
    if conn is None:
        return None
    with _episode_years_lock:
        table = _episode_years.get(series_path)
    if table is None:
        base_url, api_key, series_id = conn
        table = {}
        try:
            result = subprocess.run(
                ["curl", "-s", "-H", f"X-Api-Key: {api_key}",
                 f"{base_url}/api/v3/episode"
                 f"?seriesId={series_id}&includeEpisodeFile=true"],
                capture_output=True, text=True, timeout=60,
            )
            episodes = json.loads(result.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            episodes = []
        if isinstance(episodes, list):
            for ep in episodes:
                ep_path = (ep.get("episodeFile") or {}).get("path")
                aired = ep.get("airDateUtc") or ""
                if ep_path and len(aired) >= 4 and aired[:4].isdigit():
                    table[ep_path] = int(aired[:4])
        with _episode_years_lock:
            _episode_years[series_path] = table
    return table.get(str(filepath))


def expected_for(filepath, expected_map):
    """(iso2_code, source, year, genres) for a file.

    `year` is the episode's own air year where the arr knows it, falling back to
    the title year otherwise - so the shape stays a 4-tuple and auto_gate needs
    no knowledge of where the year came from. The blocked-reason line prints the
    year it actually used, which is how you can tell the two apart.
    """
    fpath = str(filepath)
    for path, val in expected_map.items():
        if fpath == path or fpath.startswith(path.rstrip("/") + "/"):
            aired = episode_year(filepath, path)
            if aired:
                code, source, _title_year, genres = val
                return (code, source, aired, genres)
            return val
    return None


def find_whisper():
    """Locate the whisper.cpp binary and a model file. Exits with setup help.

    --whisper-bin / --model pin either one; otherwise the binary is looked up
    on PATH under its three upstream names and the model in MODEL_PATHS."""
    binary = None
    if WHISPER_BIN_OVERRIDE:
        binary = shutil.which(os.path.expanduser(WHISPER_BIN_OVERRIDE))
    else:
        binary = next((shutil.which(b) for b in WHISPER_BINS
                       if shutil.which(b)), None)
    model = next((m for m in MODEL_PATHS if os.path.isfile(m)), None)
    if not binary or not model:
        missing = ("binary" if not binary else "model file")
        print(f"whisper.cpp not set up: no {missing} found.")
        if not binary:
            print(f"  looked for {', '.join(WHISPER_BINS)} on PATH")
            print("  install it: pacman -S whisper-cpp / apt install "
                  "whisper.cpp / brew install whisper-cpp")
        if not model:
            print(f"  looked in {', '.join(MODEL_PATHS)}")
            print(f"  mkdir -p {os.path.dirname(MODEL_PATHS[0])}")
            print(f"  curl -L -o {MODEL_PATHS[0]} \\")
            print("    https://huggingface.co/ggerganov/whisper.cpp/"
                  "resolve/main/ggml-base.bin")
            print("  or point --model at one you already have")
        sys.exit(1)
    return binary, model


# Which package supplies each required binary, for the preflight message.
TOOL_PACKAGES = {
    "ffprobe": ("ffmpeg", "pacman -S ffmpeg / apt install ffmpeg / "
                          "brew install ffmpeg"),
    "ffmpeg": ("ffmpeg", "pacman -S ffmpeg / apt install ffmpeg / "
                         "brew install ffmpeg"),
    "mkvpropedit": ("mkvtoolnix", "pacman -S mkvtoolnix-cli / apt install "
                                  "mkvtoolnix / brew install mkvtoolnix"),
    "mkvmerge": ("mkvtoolnix", "pacman -S mkvtoolnix-cli / apt install "
                               "mkvtoolnix / brew install mkvtoolnix"),
    "mkvextract": ("mkvtoolnix", "pacman -S mkvtoolnix-cli / apt install "
                                 "mkvtoolnix / brew install mkvtoolnix"),
}


def require_binaries(names):
    """Exit with install help when any required binary is missing, so nobody
    reaches their first Enter-to-tag only to hit a FileNotFoundError. Same
    contract as find_whisper, which handles the whisper binary and model."""
    missing = [n for n in names if not shutil.which(n)]
    if not missing:
        return
    print(f"missing tools: {', '.join(missing)}")
    for pkg, install in {TOOL_PACKAGES[n][0]: TOOL_PACKAGES[n][1]
                         for n in missing}.items():
        print(f"  {pkg}: {install}")
    sys.exit(1)


def probe(filepath):
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", str(filepath)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def untagged_audio_tracks(info):
    """Return [(audio_position_1based, stream_dict)] for und/missing-language
    tracks. Fast ffprobe pre-filter only: old muxes carry legacy per-track
    SimpleTag LANGUAGE entries that ffprobe merges over the header language,
    so candidates must be confirmed against header_audio_langs()."""
    tracks = []
    pos = 0
    for s in info.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        pos += 1
        lang = s.get("tags", {}).get("language", "")
        if lang in ("", "und", "undetermined"):
            tracks.append((pos, s))
    return tracks


def header_audio_langs(filepath):
    """{audio_position_1based: header_language} via mkvmerge -J, or None on
    probe failure. Reads the track-header element mkvpropedit edits and
    players use, unlike ffprobe's tag dict (see untagged_audio_tracks)."""
    cmd = ["mkvmerge", "-J", str(filepath)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                                stdin=subprocess.DEVNULL, env=_clean_env())
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    langs = {}
    pos = 0
    for t in data.get("tracks", []):
        if t.get("type") != "audio":
            continue
        pos += 1
        langs[pos] = t.get("properties", {}).get("language") or ""
    return langs


_scan_children = set()
_scan_children_lock = threading.Lock()
_scan_children_killed = False


def run_scan_child(cmd, timeout, text=False):
    """subprocess.run for the extract/whisper children, with the Popen
    registered so kill_scan_children() can end an in-flight scan the moment
    the run quits - exit otherwise sits through up to a whole whisper pass
    while the pool unwinds, minutes after the screen is gone.

    stdin is always DEVNULL: scan children overlap the interactive prompt,
    and ffmpeg handed a tty as stdin flips it into no-echo single-key mode to
    watch for its own 'q' - which eats whatever the human is typing."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=text,
                            stdin=subprocess.DEVNULL)
    with _scan_children_lock:
        if _scan_children_killed:
            proc.kill()
        _scan_children.add(proc)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise
    finally:
        with _scan_children_lock:
            _scan_children.discard(proc)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def kill_scan_children():
    """Terminate in-flight scan children and refuse new ones. The stop checks
    at the cache-put sites discard their half-made results."""
    global _scan_children_killed
    with _scan_children_lock:
        _scan_children_killed = True
        procs = list(_scan_children)
    for proc in procs:
        try:
            proc.terminate()
        except OSError:
            pass


def scan_children_killed():
    return _scan_children_killed


def extract_sample(filepath, audio_pos, position_frac, duration, wav_path):
    """Extract a mono 16 kHz sample of one audio track. Returns True on success."""
    start = max(0, duration * position_frac - SAMPLE_SECONDS / 2)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start:.1f}", "-i", str(filepath),
        "-map", f"0:a:{audio_pos - 1}", "-t", str(SAMPLE_SECONDS),
        "-ac", "1", "-ar", "16000", wav_path,
    ]
    try:
        result = run_scan_child(cmd, 120)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and os.path.getsize(wav_path) > 0


def extract_window(wav_path, start, dest_path,
                   window=SPEECH_WINDOW_SECONDS):
    """Cut one window out of an already-extracted wav. Copying the stream
    avoids a second decode of the source mkv, so this is effectively free next
    to the whisper pass that follows it."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.1f}",
           "-t", str(window), "-i", wav_path, dest_path]
    try:
        result = run_scan_child(cmd, 120)
    except subprocess.TimeoutExpired:
        return False
    return (result.returncode == 0 and os.path.exists(dest_path)
            and os.path.getsize(dest_path) > 0)


def extract_full(filepath, audio_pos, wav_path, timeout=900):
    """Extract a whole audio track as mono 16 kHz. Returns True on success."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(filepath),
           "-map", f"0:a:{audio_pos - 1}", "-ac", "1", "-ar", "16000", wav_path]
    try:
        result = run_scan_child(cmd, timeout)
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and os.path.getsize(wav_path) > 0


def whisper_pass(whisper_bin, model, wav_path, lang=None, timeout=300,
                 threads=None):
    """One whisper.cpp invocation yielding both the language detection and the
    transcript. Returns (detection, chars, unique_ratio, snippet, words,
    segments), where detection is (iso1, prob) or None and segments is a list
    of (start_seconds, text) for locating the speech later.

    `-l auto` makes whisper report "auto-detected language: xx (p = ...)" on
    stderr during the transcribe pass, so the separate --detect-language run
    this used to need is redundant: one process per window instead of two, and
    process startup is roughly half the cost of a 30s window.

    `-np` is deliberately not passed - it suppresses that detection line
    entirely (measured), while stdout carries the same segments either way, so
    dropping it costs nothing. Two other flags were measured and rejected on a
    music-heavy window: `-sns` turns background singing into a 219-word
    "La la la" repetition loop at 3x the runtime, and `-mc 0` changed no
    outcome while adding transcribed musical notation that feeds that same
    degenerate-repetition path.
    """
    cmd = [whisper_bin, "-m", model, "-f", wav_path, "-l", lang or "auto"]
    if threads:
        cmd += ["-t", str(threads)]
    try:
        result = run_scan_child(cmd, timeout, text=True)
    except subprocess.TimeoutExpired:
        return None, 0, 0.0, "", 0, []
    m = DETECT_RE.search(result.stderr + result.stdout)
    if m:
        det = (m.group(1), float(m.group(2)))
    else:
        det = (lang, 0.0) if lang else None
    return (det,) + transcript_stats(result.stdout) + (segments(result.stdout),)


def segments(text):
    """[(start_seconds, text)] from whisper's timestamped output lines."""
    out = []
    for line in text.splitlines():
        m = SEGMENT_RE.match(line.strip())
        if m:
            start = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                     + float(m.group(3)))
            out.append((start, m.group(7)))
    return out


def detect_language(whisper_bin, model, wav_path, threads=None):
    """(iso1, prob) from a detect-only pass (-dl exits before transcribing),
    or None. Reads the first SPEECH_WINDOW_SECONDS of the file it is given."""
    cmd = [whisper_bin, "-m", model, "-f", wav_path, "-l", "auto", "-dl"]
    if threads:
        cmd += ["-t", str(threads)]
    try:
        result = run_scan_child(cmd, DETECT_ONLY_TIMEOUT, text=True)
    except subprocess.TimeoutExpired:
        return None
    m = DETECT_RE.search(result.stderr + result.stdout)
    return (m.group(1), float(m.group(2))) if m else None


def densest_speech_start(segs, window=SPEECH_WINDOW_SECONDS):
    """Start offset (seconds) of the `window`-long span holding the most
    transcribed words, and that word count. Candidate starts are the segment
    starts themselves, so the window always opens on speech rather than on
    whatever silence precedes it."""
    best_start, best_words = 0.0, -1
    counts = [(start, len(words_in(text))) for start, text in segs]
    for start, _ in counts:
        n = sum(w for s, w in counts if start <= s < start + window)
        if n > best_words:
            best_start, best_words = start, n
    return best_start, max(best_words, 0)


def distinct_words(uniq, words):
    """Distinct word count, recovered from the ratio the scan recorded. Exact
    by construction (the ratio is distinct/total), so cached scans can be
    graded on it without re-transcribing anything."""
    return round(uniq * words)


def repetitive(uniq, words):
    """Whether whisper's "words" are degenerate repetition rather than
    language - a stretched sound, or a loop of the same few words.

    Two ways to be real: a high unique-word ratio, or - for a transcript long
    enough that the ratio is bound to sag - a large distinct vocabulary at a
    ratio still far above any loop (see AUTO_LONG_MIN_DISTINCT).

    The card and the gate both ask this one question, so they cannot end up
    disagreeing about whether a transcript contains words. Length is
    deliberately not part of it: a sparse transcript is short, not degenerate,
    and the two are different findings for the human."""
    if uniq >= AUTO_MIN_UNIQUE:
        return False
    return not (distinct_words(uniq, words) >= AUTO_LONG_MIN_DISTINCT
                and uniq >= AUTO_LONG_MIN_UNIQUE)


def coherent(chars, uniq, words):
    """Whether a transcript is real speech rather than a music hallucination:
    enough of it, and not degenerate."""
    return (chars >= AUTO_MIN_CHARS and words >= AUTO_MIN_WORDS
            and not repetitive(uniq, words))


def words_in(text):
    """Claimed words only: annotations and the timestamp prefixes (themselves
    bracketed) drop out, so music hallucinations show up as either nothing at
    all or a degenerate repetition loop."""
    return WORD_RE.findall(ANNOTATION_RE.sub(" ", text))


def transcript_stats(text):
    """(alpha_chars, unique_word_ratio, snippet, word_count) from whisper's
    segment output. Bracketed annotations ([Music], (applause)) and the
    timestamp prefixes are stripped first so only claimed words count - music
    hallucinations then show up as either zero chars or a degenerate
    repetition loop (low unique-word ratio)."""
    words = words_in(text)
    if not words:
        return 0, 0.0, "", 0
    chars = sum(len(w) for w in words)
    uniq = len({w.lower() for w in words}) / len(words)
    snippet = " ".join(w[:24] + ("\u2026" if len(w) > 24 else "")
                       for w in words[:12])
    return chars, uniq, snippet, len(words)


def scan_whole_track(whisper_bin, model, filepath, audio_pos, duration,
                     threads=None):
    """Transcribe an entire track in one pass. Returns an evidence dict marked
    full/deep_exhausted (absence of speech here is conclusive), or None.

    Where the pass found a coherent transcript, the detector is read a second
    time on the densest 30s of speech (win_prob/win_iso1/win_start). The first
    reading only ever saw the opening 30s, which on a cartoon is the score."""
    with tempfile.TemporaryDirectory(prefix="langtag-") as tmp:
        wav = os.path.join(tmp, "whole.wav")
        if not extract_full(filepath, audio_pos, wav):
            return None
        det, chars, uniq, snippet, nwords, segs = whisper_pass(
            whisper_bin, model, wav, timeout=max(900, int(duration)),
            threads=threads)
        if det is None:
            return None
        result = {"iso1": det[0], "prob": det[1], "detections": [det],
                  "chars": chars, "unique": uniq, "snippet": snippet,
                  "words": nwords, "deep_exhausted": True, "full": True}
        # Gated on there being speech to aim at, deliberately not on
        # coherent(): the probe is evidence-gathering, and which transcripts
        # count as coherent is a separate judgement that has moved before. A
        # cached scan then carries its window reading either way, so tuning
        # that judgement never costs a re-scan.
        if segs and nwords >= AUTO_MIN_WORDS:
            result.update(speech_window_probe(whisper_bin, model, wav, segs,
                                              tmp, threads=threads))
    return result


def speech_window_probe(whisper_bin, model, wav, segs, tmpdir, threads=None):
    """Re-read the detector on the densest 30s of speech, as
    {win_prob, win_iso1, win_start, win_words}, or {} when there is nothing to
    add. The window is cut to its own file because whisper's offset flag does
    not move language detection (see SPEECH_WINDOW_SECONDS)."""
    start, nwords = densest_speech_start(segs)
    if not nwords:
        return {}
    win = os.path.join(tmpdir, "window.wav")
    if not extract_window(wav, start, win):
        return {}
    det = detect_language(whisper_bin, model, win, threads=threads)
    if det is None:
        return {}
    return {"win_prob": det[1], "win_iso1": det[0], "win_start": start,
            "win_words": nwords}


def analyze_track(whisper_bin, model, filepath, audio_pos, duration, auto,
                  threads=None):
    """Return an evidence dict {iso1, prob, detections, chars, unique,
    snippet, words}, or None when nothing could be analyzed.

    Tracks short enough to fit SCAN_BUDGET_SECONDS of whisper time are
    transcribed whole rather than sampled, which makes the first card
    conclusive instead of a starting point for 'd' presses. Sparse-dialogue
    content concentrates all its speech into one short burst - Mickeys
    Choo-Choo (1929) has 18s of dialogue in 410s, 4% of the runtime - so no
    affordable number of windows reliably lands on it. Worse, whisper's decode
    is knife-edge sensitive to how much music precedes speech *inside* a
    window: a window starting 6s earlier over that same burst returned only
    "(singing in foreign language)". A whole-track pass costs ~20s on a file
    that short (base model runs 16-21x realtime) and gets a decode attempt on
    every region, so it is both cheaper than an escalated deep scan and
    conclusive.

    Longer tracks have dense enough dialogue that any window hits it, so they
    keep sampling. In auto mode every position is sampled so the gates can
    require unanimity; interactively it stops at the first window with actual
    words."""
    if duration and duration <= FULL_SCAN_MAX_SECONDS:
        whole = scan_whole_track(whisper_bin, model, filepath, audio_pos,
                                 duration, threads=threads)
        if whole:
            return whole
        # extraction or detection failed - fall through and sample instead
    best = None
    detections = []
    chars, uniq, snippet, nwords = 0, 0.0, "", 0
    with tempfile.TemporaryDirectory(prefix="langtag-") as tmp:
        for i, frac in enumerate(SAMPLE_POSITIONS):
            wav = os.path.join(tmp, f"sample{i}.wav")
            if not extract_sample(filepath, audio_pos, frac, duration, wav):
                continue
            det, t_chars, t_uniq, t_snippet, t_words, _segs = whisper_pass(
                whisper_bin, model, wav, threads=threads)
            if det is None:
                continue
            detections.append(det)
            if (t_words, t_chars) > (nwords, chars):
                chars, uniq, snippet, nwords = t_chars, t_uniq, t_snippet, t_words
            if best is None or det[1] > best[1]:
                best = det
            # Stop on evidence, not on detector confidence. The old MIN_PROB
            # test quit after a confident *music* detection, which is exactly
            # when another window is warranted.
            if not auto and coherent(t_chars, t_uniq, t_words):
                break
    if best is None:
        return None
    return {"iso1": best[0], "prob": best[1], "detections": detections,
            "chars": chars, "unique": uniq, "snippet": snippet,
            "words": nwords}


# Deliberately disjoint from SAMPLE_POSITIONS: re-sampling a window the first
# pass already transcribed buys nothing. Ordered widest-spread first.
DEEP_POSITIONS = [0.10, 0.30, 0.50, 0.70, 0.90, 0.25, 0.65]


def deep_scan(whisper_bin, model, filepath, audio_pos, duration, analysis):
    """Prompt-key escalation ('d'): sample additional windows across the
    track, stopping at the first coherent transcript. For sparse-dialogue
    content (a mostly-quiet cartoon) where the standard windows land in
    music/silence. Evidence for the human - the result feeds the
    redisplayed prompt, never an auto-tag. A window that yields a coherent
    transcript decides the language claim outright: a quiet window's
    confident-looking garbage detection must not outvote actual words."""
    base = dict(analysis) if analysis else {
        "iso1": None, "prob": 0.0, "detections": [],
        "chars": 0, "unique": 0.0, "snippet": "", "words": 0}
    base.setdefault("words", 0)
    base["detections"] = list(base["detections"])
    base["deep_total"] = len(DEEP_POSITIONS)
    # a re-run must not inherit the previous scan's verdict: an exhausting
    # second pass would otherwise still show the first pass's stop point
    for stale in ("deep_stop", "deep_windows", "deep_exhausted"):
        base.pop(stale, None)
    sampled = 0
    with tempfile.TemporaryDirectory(prefix="langtag-") as tmp:
        for i, frac in enumerate(DEEP_POSITIONS):
            print(dim(f"   deep scan  window {i + 1}/{len(DEEP_POSITIONS)}"
                      f" ({frac:.0%})..." + " " * 10), end="\r", flush=True)
            wav = os.path.join(tmp, f"deep{i}.wav")
            if not extract_sample(filepath, audio_pos, frac, duration, wav):
                continue
            got, chars, uniq, snippet, nwords, _segs = whisper_pass(
                whisper_bin, model, wav)
            if got is None:
                continue
            sampled += 1
            base["deep_windows"] = sampled
            base["detections"].append(got)
            if (nwords, chars) > (base.get("words", 0), base["chars"]):
                base["chars"], base["unique"], base["snippet"] = chars, uniq, snippet
                base["words"] = nwords
            if coherent(chars, uniq, nwords):
                base["iso1"], base["prob"] = got
                base["deep_stop"] = frac
                print(dim(f"   deep scan  words found at {frac:.0%}")
                      + " " * 20)
                break
            if base["iso1"] is None or got[1] > base["prob"]:
                base["iso1"], base["prob"] = got
        else:
            # every deep window sampled without hitting a coherent transcript:
            # absence of speech is now evidence, not a sampling gap
            base["deep_exhausted"] = True
            print(dim(f"   deep scan  no words in any of "
                      f"{len(DEEP_POSITIONS)} extra windows") + " " * 15)
    return base if base["iso1"] else None


FULL_SCAN_WARN_SECONDS = 30 * 60  # confirm before full-scanning longer files


def full_scan_estimate(duration):
    """Rough wall-clock minutes for a whole-track transcription. Unlike a deep
    scan there is no early exit, so this cost is always paid in full."""
    return max(1, round(duration / WHISPER_REALTIME / 60))


def full_scan(whisper_bin, model, filepath, audio_pos, duration, analysis):
    """Prompt-key escalation ('f'): transcribe the ENTIRE track. The only
    scan with no sampling gap - absence of speech here is conclusive for
    this transfer, not just for the sampled windows. Roughly duration divided
    by WHISPER_REALTIME. Evidence only, never tags by itself.

    Tracks under FULL_SCAN_MAX_SECONDS are already scanned this way on the
    first pass, so pressing 'f' there just re-runs the same work."""
    iso1 = analysis["iso1"] if analysis else "en"
    base = dict(analysis) if analysis else {
        "iso1": None, "prob": 0.0, "detections": [],
        "chars": 0, "unique": 0.0, "snippet": "", "words": 0}
    base.setdefault("words", 0)
    est = full_scan_estimate(duration)
    print(dim(f"   full scan  transcribing the whole track (~{est} min)..."),
          flush=True)
    with tempfile.TemporaryDirectory(prefix="langtag-") as tmp:
        wav = os.path.join(tmp, "full.wav")
        if not extract_full(filepath, audio_pos, wav):
            print(red("   full scan  audio extraction failed"))
            return analysis
        _det, chars, uniq, snippet, nwords, _segs = whisper_pass(
            whisper_bin, model, wav, lang=iso1,
            timeout=max(900, int(duration)))
    if (nwords, chars) > (base.get("words", 0), base["chars"]):
        base["chars"], base["unique"], base["snippet"] = chars, uniq, snippet
        base["words"] = nwords
    base["deep_exhausted"] = True
    base["full"] = True
    if nwords == 0:
        print(dim("   full scan  no words anywhere in the track"))
    else:
        print(dim(f"   full scan  {nwords} word(s) found"))
    if base["iso1"] is None:
        base["iso1"], base["prob"] = iso1, 0.0
    return base


def best_prob(analysis):
    """The strongest calibrated reading for this scan: the opening 30s, or the
    densest 30s of speech when the second probe agrees on the language.

    A window that names a *different* language is a disagreement, not an
    improvement, so it never contributes - the whole point of the probe is to
    ask the detector about the same claim with better audio. An analysis
    predating the probe (an older cache entry, or a sampled scan) has no window
    reading and simply grades on the opening, exactly as before."""
    prob = analysis.get("prob", 0.0)
    if analysis.get("win_iso1") and analysis["win_iso1"] == analysis["iso1"]:
        prob = max(prob, analysis.get("win_prob", 0.0))
    return prob


def auto_gate(analysis, exp, tracks, header_langs, stream):
    """Return None when every --auto gate passes, else a short reason the
    track must be prompted instead."""
    if analysis is None or not ISO1_TO_ISO2B.get(analysis["iso1"]):
        return "no detection"
    if exp is None:
        return "no arr metadata"
    exp_code, _src, year, genres = exp
    if analysis["iso1"] in AUTO_EXCLUDE_ISO1:
        return "ambiguous language cluster"
    dets = analysis["detections"]
    if analysis.get("full"):
        # A whole-track pass has no sampling gap. Cross-window unanimity is
        # what the multi-sample test approximates, so a single detection over
        # the entire track is stronger evidence than two agreeing windows, not
        # weaker - without this branch --auto could never tag a short file.
        # The reading graded here is the better of the opening and the densest
        # speech window, because the opening alone is what a cartoon's musical
        # title sequence scores (see SPEECH_WINDOW_SECONDS).
        if not dets or best_prob(analysis) < AUTO_PROB:
            return "low confidence"
    elif (len(dets) < 2 or any(p < AUTO_PROB for _, p in dets)
            or len({lang for lang, _ in dets}) != 1):
        return "samples not unanimous"
    if not coherent(analysis["chars"], analysis["unique"],
                    analysis.get("words", 0)):
        return "no coherent transcript"
    if ISO1_TO_ISO2B[analysis["iso1"]] != exp_code:
        return "differs from arr language"
    if year is None or year < AUTO_MIN_YEAR:
        return f"year {year or 'unknown'} < {AUTO_MIN_YEAR}"
    if genres & AUTO_EXCLUDE_GENRES:
        return "music/concert genre"
    if len(tracks) != 1 or (header_langs and len(header_langs) != 1):
        return "multiple audio tracks"
    if "commentary" in stream.get("tags", {}).get("title", "").lower():
        return "commentary track"
    return None


def record_tag(filepath, audio_pos, code, mode, analysis=None, old="und"):
    """Append every applied tag (auto and manual) to the audit log so any
    mistake is enumerable and reversible (mkvpropedit back to und using
    columns 2-4). An undo appends its own corrective row (old=the code it
    reverts) rather than editing history.

    The probability column is the reading the decision was actually made on -
    best_prob, the same figure the card shows - not the opening 30s. Logging
    the opening made an auto row read like the gate had admitted a track at
    p=0.42 (the sparse-dialogue calibration short) when it admitted it on a 0.91 speech window."""
    from datetime import datetime
    line = "\t".join([
        datetime.now().isoformat(timespec="seconds"), str(filepath),
        f"a{audio_pos}", f"{old}->{code}", mode,
        f"{best_prob(analysis):.2f}" if analysis else "-",
        str(analysis["chars"]) if analysis else "-",
    ])
    with open(TAG_LOG_FILE, "a") as f:
        f.write(line + "\n")


def _clean_env():
    """Environment with a valid locale. ssh from macOS forwards LC_CTYPE=UTF-8,
    which is not a valid locale on Linux and crashes mkvtoolnix's C++ runtime."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LC_") and k != "LANG"}
    env["LC_ALL"] = "C.UTF-8"
    return env


def _audio_track_props(filepath, audio_pos):
    """mkvmerge's properties dict for the Nth audio track, or None on failure."""
    try:
        result = subprocess.run(["mkvmerge", "-J", str(filepath)],
                                capture_output=True, text=True, timeout=60,
                                env=_clean_env())
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None
    pos = 0
    for t in data.get("tracks", []):
        if t.get("type") != "audio":
            continue
        pos += 1
        if pos == audio_pos:
            return t.get("properties", {})
    return None


def clear_shadowing_language_tag(filepath, audio_pos, code):
    """Drop a legacy per-track SimpleTag LANGUAGE that now contradicts the
    header language, rewriting the track's other tags unchanged.

    mkvpropedit --set language= writes only the track header, so a file
    carrying an old Matroska SimpleTag LANGUAGE ends up internally
    contradictory. ffprobe surfaces that SimpleTag over the header (the key
    arrives uppercase, so a lowercase "language" lookup reads empty), and
    anything probing with ffmpeg then reports the audio as undetermined
    despite a correct header - Jellyfin showed two episodes of one series as Unknown
    audio that way.

    The whole tag set cannot simply be deleted: measured across this library,
    10 of 61 tracks carrying tag_language also carry mkvmerge statistics tags
    (bps, duration, number_of_frames), which players use to report bitrate
    without scanning. So only the LANGUAGE entry is removed.

    Returns (ok, note); note is None when there was nothing to do. A failure to
    clear is reported but not fatal - the header edit it follows is the real
    tagging operation and has already been verified.
    """
    props = _audio_track_props(filepath, audio_pos)
    if props is None:
        return True, None
    tag_lang = props.get("tag_language")
    if tag_lang is None or tag_lang == code:
        return True, None
    uid = str(props.get("uid", ""))

    try:
        got = subprocess.run(["mkvextract", "tags", str(filepath)],
                             capture_output=True, text=True, timeout=120,
                             env=_clean_env())
    except (subprocess.TimeoutExpired, OSError):
        return True, f"left a stale LANGUAGE={tag_lang} tag (mkvextract failed)"
    if got.returncode != 0:
        return True, f"left a stale LANGUAGE={tag_lang} tag (mkvextract failed)"
    try:
        # mkvextract emits a BOM, which ElementTree will not accept in a str
        root = ET.fromstring(got.stdout.lstrip("﻿"))
    except ET.ParseError:
        return True, f"left a stale LANGUAGE={tag_lang} tag (unparsable XML)"

    keep = []
    for tag in root.findall("Tag"):
        targets = tag.find("Targets")
        tuid = targets.find("TrackUID") if targets is not None else None
        if tuid is None or (tuid.text or "").strip() != uid:
            continue  # global tags and other tracks are out of this selector
        for simple in list(tag.findall("Simple")):
            name = simple.find("Name")
            if name is not None and (name.text or "").strip().upper() == "LANGUAGE":
                tag.remove(simple)
        if tag.findall("Simple"):
            keep.append(tag)

    with tempfile.TemporaryDirectory(prefix="langtag-") as tmp:
        # An empty filename after the selector deletes that track's tags; a
        # path replaces them with its contents.
        arg = f"track:a{audio_pos}:"
        if keep:
            xml_path = os.path.join(tmp, "tags.xml")
            doc = ET.Element("Tags")
            for tag in keep:
                doc.append(tag)
            ET.ElementTree(doc).write(xml_path, encoding="utf-8",
                                      xml_declaration=True)
            arg += xml_path
        try:
            res = subprocess.run(["mkvpropedit", str(filepath), "--tags", arg],
                                 capture_output=True, text=True, timeout=120,
                                 env=_clean_env())
        except (subprocess.TimeoutExpired, OSError):
            return True, f"left a stale LANGUAGE={tag_lang} tag (mkvpropedit failed)"
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()[:80]
        return True, f"left a stale LANGUAGE={tag_lang} tag: {detail}"

    after = _audio_track_props(filepath, audio_pos)
    if after is None:
        return True, "cleared the stale LANGUAGE tag (unverified)"
    if after.get("language") != code:
        # Rewriting tags must never disturb the header we just set.
        return False, (f"clearing the LANGUAGE tag disturbed the header: "
                       f"reads '{after.get('language')}'")
    still = after.get("tag_language")
    if still is not None and still != code:
        return True, f"stale LANGUAGE tag survives, still reads '{still}'"
    note = f"cleared a stale LANGUAGE={tag_lang} tag"
    if keep:
        note += f", kept {len(keep)} other tag block(s)"
    return True, note


def apply_tag(filepath, audio_pos, code):
    """Set the language on the Nth audio track via mkvpropedit (in-place header
    edit), then clear any legacy SimpleTag that would contradict it.
    Returns (ok, message)."""
    cmd = ["mkvpropedit", str(filepath),
           "--edit", f"track:a{audio_pos}", "--set", f"language={code}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                            env=_clean_env())
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()[:200]
    # Verify from the artifact: re-read the track header and confirm the tag
    # landed. Header truth via mkvmerge, not ffprobe (legacy SimpleTag
    # LANGUAGE entries shadow the header value in ffprobe's tag dict).
    langs = header_audio_langs(filepath)
    if langs is None:
        return False, "verification failed: could not re-probe file"
    got = langs.get(audio_pos, "")
    if got != code:
        return False, f"verification failed: track reads '{got}' after edit"
    ok, note = clear_shadowing_language_tag(filepath, audio_pos, code)
    if not ok:
        return False, note
    return True, note or "tagged"


def load_skips():
    skips = set()
    if os.path.exists(SKIP_FILE):
        with open(SKIP_FILE) as f:
            for line in f:
                line = line.rstrip("\n")
                if line:
                    skips.add(line)
    return skips


def record_skip(key):
    with open(SKIP_FILE, "a") as f:
        f.write(key + "\n")


def remove_skip(key):
    """Take one never-ask entry back out ('u' undo). Read-modify-write under
    the state lock so a concurrent append is not lost."""
    with state_file_lock():
        if not os.path.exists(SKIP_FILE):
            return
        with open(SKIP_FILE) as f:
            lines = [l for l in f.read().splitlines() if l and l != key]
        tmp = SKIP_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write("".join(l + "\n" for l in lines))
        os.replace(tmp, SKIP_FILE)


_scan_cache = {}
_scan_cache_lock = threading.Lock()
_scan_cache_dirty = 0
_scan_cache_hits = 0
_instance_lock_fh = None


@contextlib.contextmanager
def state_file_lock():
    """Exclusive advisory lock across processes, for the read-modify-write of a
    shared JSON file. Yields True when the lock was actually taken; a
    filesystem without flock support yields False and the caller proceeds
    unlocked rather than refusing to save."""
    fh = None
    locked = False
    try:
        fh = open(STATE_LOCK_FILE, "a+")
        fcntl.flock(fh, fcntl.LOCK_EX)
        locked = True
    except OSError:
        pass
    try:
        yield locked
    finally:
        if fh is not None:
            if locked:
                try:
                    fcntl.flock(fh, fcntl.LOCK_UN)
                except OSError:
                    pass
            fh.close()


def _flush_and_exit_on_signal(signum, _frame):
    """Flush the scan cache on SIGTERM/SIGHUP.

    atexit does not run for a signal-terminated process, so a `pkill` (or a
    systemd stop, or the terminal closing) would otherwise discard everything
    scanned since the last periodic flush. Raising SystemExit here returns to
    the normal shutdown path, so atexit still fires for whatever accumulates
    while the pool unwinds - and killing the scan children first means the
    pool unwinds in moments, not after a full whisper pass.
    """
    kill_scan_children()
    save_scan_cache()
    sys.exit(128 + signum)


def install_signal_flush(unattended=False):
    """SIGINT is only taken over for an unattended run. There, Python's default
    KeyboardInterrupt does not reliably stop the pool - a prescan kept
    dispatching new whisper jobs for minutes after a `pkill -INT`, because the
    interrupt lands in whichever thread the interpreter picks and the worker
    loop carries on. Interactively, Ctrl-C at the prompt already means a clean
    'q' (which prunes the queue and flushes on the way out), so replacing it
    with an immediate exit there would lose that."""
    sigs = [signal.SIGTERM, signal.SIGHUP]
    if unattended:
        sigs.append(signal.SIGINT)
    for sig in sigs:
        try:
            signal.signal(sig, _flush_and_exit_on_signal)
        except (OSError, ValueError):
            pass  # not the main thread, or the platform lacks it


def claim_instance_lock():
    """Take a lifetime lock so a second instance can be detected. Returns the
    other holder's pid as a string when one is already running, else None
    (which also covers "locking unavailable" - never block on that)."""
    global _instance_lock_fh
    try:
        fh = open(INSTANCE_LOCK_FILE, "a+")
    except OSError:
        return None
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            fh.seek(0)
            pid = fh.read().strip()
        except OSError:
            pid = ""
        fh.close()
        return pid or "unknown"
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    _instance_lock_fh = fh  # deliberately kept open: closing frees the lock
    return None


def scan_cache_key(filepath, audio_pos):
    """Path + mtime + track. mtime moves whenever the file is retagged or
    replaced, so a stale entry can never be served."""
    try:
        mtime = int(os.path.getmtime(filepath))
    except OSError:
        mtime = 0
    return f"{filepath}\t{mtime}\ta{audio_pos}"


def load_scan_cache():
    """Read whisper results saved by earlier runs. Returns the entry count."""
    global _scan_cache
    try:
        with open(SCAN_CACHE_FILE) as f:
            data = json.load(f)
        _scan_cache = data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        _scan_cache = {}
    return len(_scan_cache)


def save_scan_cache():
    """Merge with whatever is on disk, then write through a temp file in the
    same directory so an interrupt mid-write cannot truncate the cache.

    Read-modify-write under a cross-process lock, not a blind dump: each
    process holds a snapshot taken at its own start, so dumping it would delete
    every entry another process added since. Entries are immutable for a given
    (path, mtime, track), so a key present on both sides is the same scan and
    the merge order doesn't matter. The merged result is folded back into memory
    so this process also gains the other's entries as lookup hits.
    """
    global _scan_cache, _scan_cache_dirty
    with _scan_cache_lock:
        mine = dict(_scan_cache)
        _scan_cache_dirty = 0
    with state_file_lock():
        merged = {}
        try:
            with open(SCAN_CACHE_FILE) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                merged = loaded
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        merged.update(mine)
        tmp = SCAN_CACHE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(merged, f)
            os.replace(tmp, SCAN_CACHE_FILE)
        except OSError:
            return
    with _scan_cache_lock:
        _scan_cache = merged


def scan_cache_get(filepath, audio_pos, need_auto):
    """A cached analysis, or None.

    A whole-track scan is gap-free and identical whichever mode produced it, so
    it always serves. A sampled one is not: an interactive run stops at the
    first window holding words, so it carries fewer detections than --auto's
    unanimity gate needs. Those only satisfy a later auto run if an auto run
    made them.
    """
    global _scan_cache_hits
    with _scan_cache_lock:
        entry = _scan_cache.get(scan_cache_key(filepath, audio_pos))
        if entry is None:
            return None
        if entry.get("full") or entry.get("_auto") or not need_auto:
            _scan_cache_hits += 1
            return dict(entry)
    return None


def scan_cache_put(filepath, audio_pos, analysis, was_auto):
    """Record an analysis. Deep and full scans land here too, so the evidence a
    'd' or 'f' press paid for is not lost when the run ends."""
    global _scan_cache_dirty
    if analysis is None:
        return
    entry = dict(analysis)
    entry["_auto"] = bool(was_auto)
    with _scan_cache_lock:
        _scan_cache[scan_cache_key(filepath, audio_pos)] = entry
        _scan_cache_dirty += 1
        due = _scan_cache_dirty >= SCAN_CACHE_FLUSH_EVERY
    if due:
        save_scan_cache()


def scan_cache_stats():
    with _scan_cache_lock:
        return len(_scan_cache), _scan_cache_hits


def scan_cache_servable_mtimes(need_auto):
    """path -> mtimes holding at least one entry scan_cache_get would serve
    this run, for the cached-first reorder in main(). Same servability rule as
    scan_cache_get: sampled interactive scans do not satisfy an auto run."""
    servable = {}
    with _scan_cache_lock:
        for key, entry in _scan_cache.items():
            if not (entry.get("full") or entry.get("_auto") or not need_auto):
                continue
            path, mtime, _track = key.rsplit("\t", 2)
            servable.setdefault(path, set()).add(int(mtime))
    return servable


def load_resolved_paths():
    """Files already tagged or permanently skipped by this tool.

    Read back from the append-per-action logs rather than kept in memory, so
    the workload estimate stays true across a killed run: record_tag appends
    before the next prompt is drawn. Only ever used to discount the estimate -
    the scan worker re-verifies every file from its header regardless, so a
    stale entry here costs nothing but a slightly-off denominator.

    Path granularity, not track: a measured queue's own totals (1503 untagged
    tracks across 1500 files) make multi-track files a rounding error.
    """
    resolved = set()
    for path, field in ((TAG_LOG_FILE, 1), (SKIP_FILE, 0)):
        try:
            with open(path) as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) > field and parts[field]:
                        resolved.add(parts[field])
        except OSError:
            continue
    return resolved


def prune_worklist(touched, skips, applied):
    """Drop finished files from the saved queue so the next run's counter
    starts from a true number rather than the last external scan's total.

    Each touched file is re-read from its track headers, so a file whose second
    audio track is still untagged stays queued. The external scan still
    overwrites this file wholesale and remains the authoritative recount, so
    `date` keeps pointing at that scan and the "imports newer than that scan"
    caveat stays true; `pruned` records this cheaper mid-week correction.

    The header probing happens before the lock is taken and the file is re-read
    inside it, so two processes pruning at once compose their removals instead
    of one reinstating what the other dropped.
    """
    from datetime import datetime
    if not touched:
        return
    done = set()
    for p in touched:
        langs = header_audio_langs(p)
        if langs is None:
            continue
        remaining = [pos for pos, lang in langs.items()
                     if lang in ("", "und")]
        # Nothing left untagged, or everything left is on the never-ask list:
        # either way this file will not prompt again.
        if all(f"{p}\ta{pos}" in skips for pos in remaining):
            done.add(str(p))
    if not done:
        return
    with state_file_lock():
        try:
            with open(UNTAGGED_STATE_FILE) as f:
                st = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        paths = st.get("paths")
        if not isinstance(paths, list):
            return
        st["paths"] = [p for p in paths if p not in done]
        st["files"] = len(st["paths"])
        st["tracks"] = max(len(st["paths"]),
                           int(st.get("tracks", 0)) - applied)
        st["pruned"] = datetime.now().strftime("%Y-%m-%d")
        tmp = UNTAGGED_STATE_FILE + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, UNTAGGED_STATE_FILE)
        except OSError:
            pass


def find_mkv_files(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_file() and p.suffix.lower() == ".mkv":
            files.append(p)
        elif p.is_dir():
            files.extend(p.rglob("*.mkv"))
    return sorted(files)


def load_worklist():
    """(files, state_dict) from the saved candidate queue.

    files is None when no queue is available (state missing, pre-paths
    format, or empty) - callers fall back to a full sweep. Deleted files
    are dropped; renamed/re-imported ones are missed until the next external
    scan (that's what --full is for).
    """
    try:
        with open(UNTAGGED_STATE_FILE) as f:
            st = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None
    paths = st.get("paths")
    if not isinstance(paths, list) or not paths:
        return None, st
    files = sorted(Path(p) for p in paths)
    return [p for p in files if p.is_file()], st


def probe_candidates(filepath, skips):
    """(info, tracks, header_langs) for a file that still has untagged audio,
    or None to pass over it. Confirms ffprobe's und candidates against the
    track headers and drops never-ask entries."""
    info = probe(filepath)
    if info is None:
        return None
    candidates = untagged_audio_tracks(info)
    header_langs = header_audio_langs(filepath) if candidates else None
    if header_langs is not None:
        candidates = [(pos, s) for pos, s in candidates
                      if header_langs.get(pos, "") in ("", "und")]
    tracks = [(pos, s) for pos, s in candidates
              if f"{filepath}\ta{pos}" not in skips]
    if not tracks:
        return None
    return info, tracks, header_langs


def print_card(pos, s, guess, exp, info, header_langs, duration,
               filesize, auto_reason):
    """One track's evidence card, printed to stdout. Shared by the line UI
    (which prints straight to the terminal) and the TUI (which captures the
    output and renders it in its card panel). Returns (code, whole): the
    Enter-default code - None when accepting needs a typed code - and
    whether the track was already transcribed whole."""
    code = None
    degenerate = False
    det_val = det_ext = ""
    if guess:
        iso1 = guess["iso1"]
        prob = best_prob(guess)
        code = ISO1_TO_ISO2B.get(iso1)
        pct = f"{prob:.0%}"
        w_n = guess.get("words", 0)
        degenerate = guess["chars"] > 0 and (
            w_n < 3 or repetitive(guess["unique"], w_n))
        n_win = max(len(guess["detections"]), 1)
        scope = ("the whole track" if guess.get("full")
                 else f"{n_win} window{'s' if n_win != 1 else ''}")
        # The detector reading gets its own row: it is secondary
        # evidence, because whisper only ever detects from 30s of
        # audio. Leaving it inside the verdict's detail made a
        # 54% reading look like the confidence of a verdict backed
        # by 14 cleanly transcribed words.
        det_val = f"{code or iso1} {pct}"
        # Which 30s produced that number: the opening by default,
        # or the densest speech when the second probe beat it.
        win_used = (guess.get("win_prob") is not None
                    and prob > guess.get("prob", 0.0))
        if win_used:
            at = int(guess.get("win_start", 0))
            det_from = (f"densest speech at {at // 60}:{at % 60:02d}"
                        f", {guess.get('win_words', 0)} words")
        else:
            det_from = "opening 30s"
        rescued = False
        if code and degenerate and prob >= AUTO_PROB:
            # A repetitive transcript normally reads as hallucinated
            # music, but the detector clearing the auto bar on its
            # best 30s reading does not happen on music (measured
            # ceiling 0.86, calibration in the module docstring):
            # this is sparse real speech drowned in loop padding.
            # Suggest the language, qualified, instead of zxx. Card
            # evidence only - coherent() still fails, so the auto
            # gates are untouched.
            rescued = True
            w_val = yellow(bold(code))
            w_ext = "sparse speech found, most of the transcript is repetition"
            det_ext = dim(det_from)
        elif code and (guess["chars"] == 0 or degenerate):
            # Zero transcript OR a degenerate one (stretched
            # sound, repetition loop) is the hallucination case -
            # never the Enter default. After an exhausted deep
            # scan, absence across the track earns a zxx
            # suggestion instead, matching the scan verdict.
            # The detector row is dropped rather than annotated: a
            # percentage with no words behind it is whisper's
            # language head firing on music, and printing it only
            # invites second-guessing the verdict right above it.
            det_val = ""
            if guess.get("full") or guess.get("deep_exhausted"):
                code = "zxx"
                w_val = green(bold("zxx"))
                # "coherent" only when there was a transcript to judge: a
                # degenerate one is qualified, a truly empty one is not.
                w_ext = (f"no coherent speech in {scope}" if degenerate
                         else f"no speech in {scope}")
            else:
                code = None
                w_val = yellow("no speech")
                w_ext = f"{scope} sampled, none held words"
        elif code and guess.get("full") and coherent(
                guess["chars"], guess["unique"], w_n):
            # Gap-free pass with a real transcript: the words are
            # the evidence, so don't grade it on prob.
            w_val = green(bold(code))
            w_ext = f"{w_n} words, whole track scanned"
            det_ext = dim(det_from if win_used else
                          "opening 30s, so it reads low on a "
                          "music opening")
        elif code:
            colored = green if prob >= 0.85 else (
                yellow if prob >= MIN_PROB else red)
            w_val = colored(bold(code))
            w_ext = (f"{w_n} words in {scope}" if w_n
                     else f"{scope} scanned")
            if prob < MIN_PROB:
                det_ext = red("low confidence")
            elif win_used:
                det_ext = dim(det_from)
        else:
            w_val = yellow(f"'{iso1}'")
            w_ext = "unmapped code, type one manually"
    else:
        w_val = red("no speech detected")
        w_ext = "type a code or skip"

    codec = s.get("codec_name", "?")
    ch = s.get("channels", "?")
    title = s.get("tags", {}).get("title", "")
    try:
        kbps = int(s.get("bit_rate", 0)) // 1000
    except (TypeError, ValueError):
        kbps = 0
    track_ext = f"{codec}   {ch}ch"
    if kbps:
        track_ext += f"   {kbps} kb/s"
    if title:
        track_ext += f'   "{title}"'

    deeped = bool(guess and (guess.get("deep_exhausted")
                             or len(guess["detections"]) > 3))
    print()
    # Codec, channels, bitrate, runtime and size all answer "what
    # is this file", so they share one row and leave the evidence
    # rows (whisper/detector/arr) adjacent instead of split apart.
    # Runtime and size stay on the card rather than the header
    # because the header isn't reprinted on the post-scan
    # redisplay, and they are the cost signal for d/f.
    facts = (f"{track_ext}   {fmt_duration(duration)}"
             f"   {fmt_size(filesize)}")
    row("track", f"a{pos}", ellipsize(facts, reserve=33))
    row("whisper", w_val, w_ext)
    if det_val:
        row("detector", dim(det_val), det_ext)
    if (guess and guess.get("deep_stop") is not None
            and not guess.get("full")):
        # An early-exiting deep scan leaves the later windows
        # unsampled; say how much of the spread actually got looked
        # at, since the whisper row only reports window counts when
        # nothing was found.
        row("deep", dim(f"{guess['deep_windows']}/"
                        f"{guess['deep_total']} windows"),
            dim(f"stopped at {guess['deep_stop']:.0%}"))
    if auto_reason and not deeped:
        row("auto", dim("prompting"), dim(auto_reason))
    if exp:
        exp_code, exp_src = exp[0], exp[1]
        if code and code == exp_code:
            row(exp_src.lower(), bold(exp_code), green("agree"))
        elif code and code != "zxx":
            row(exp_src.lower(), bold(exp_code),
                yellow("differs (dub/commentary?)"))
        else:
            # zxx is a no-linguistic-content claim, not a dub -
            # the series language simply doesn't apply
            row(exp_src.lower(), bold(exp_code))
        # Heads-up when the file contains no track in the expected
        # language at all: likely a wrong-audio file (dub only).
        # Once tagged, an external scan can flag it for replacement.
        if code and code != exp_code and code != "zxx":
            file_langs = (set(header_langs.values()) if header_langs
                          else {
                s2.get("tags", {}).get("language", "")
                for s2 in info.get("streams", [])
                if s2.get("codec_type") == "audio"
            })
            if exp_code not in file_langs:
                wide_row("note", yellow(
                    f"no {exp_code} audio anywhere in this file - "
                    "original may be missing"))
    if guess and guess["chars"]:
        # Last, and spanning both columns: a transcript is far
        # longer than the value column and is read as prose anyway.
        heard = f'"{guess["snippet"]}..."'
        if degenerate:
            heard += " " + yellow("mostly repetition" if rescued else
                                  "repetition or stretched sound,"
                                  " not speech")
        wide_row("heard", heard)
    print()
    return code, bool(guess and guess.get("full"))


def scan_worker(files, skips, whisper_bin, model, listing, auto, out_q,
                stop_event, jobs=1, threads=None):
    """Producer: probe files and (interactive mode) run whisper ahead of the
    prompt, so the next card is ready while the current one is answered.

    Whisper is CPU-bound and one scan at a time leaves most of the box idle, so
    `jobs` scans run concurrently with `threads` whisper threads each. Cards
    then arrive as scans finish rather than strictly in queue order, which is
    why the on-screen counter is a running tally rather than an index.

    Emits ("progress", str), ("file", payload) and a final ("done", None) on
    out_q, whose bounded size supplies the backpressure.
    """
    scanned = 0
    announced = False

    def analyze(filepath, info, tracks, header_langs):
        duration = float(info.get("format", {}).get("duration", 0) or 0)
        guesses = {}
        for pos, _ in tracks:
            if stop_event.is_set():
                return None
            cached = scan_cache_get(filepath, pos, auto)
            if cached is not None:
                guesses[pos] = cached
                continue
            result = analyze_track(whisper_bin, model, filepath, pos,
                                   duration, auto, threads=threads)
            if stop_event.is_set() or scan_children_killed():
                # A quit mid-scan kills the whisper child; its truncated
                # "no speech" result must never reach the cache.
                return None
            scan_cache_put(filepath, pos, result, auto)
            guesses[pos] = result
        return (filepath, info, tracks, header_langs, guesses)

    def drain(futures, wait_all=False):
        """Emit finished scans; without wait_all, only those already done."""
        if not futures:
            return futures
        mode = cf.ALL_COMPLETED if wait_all else cf.FIRST_COMPLETED
        done, still = cf.wait(futures, return_when=mode)
        for fut in done:
            try:
                res = fut.result()
            except Exception as exc:
                # Never swallow this: a scan crashing silently drops the file
                # from the run and looks identical to "nothing to tag".
                out_q.put(("progress",
                           red(f"  scan failed: {type(exc).__name__}: {exc}")))
                continue
            if res:
                out_q.put(("file", res))
        return still

    pending = set()
    with cf.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        for filepath in files:
            if stop_event.is_set():
                break
            scanned += 1
            if scanned % 200 == 0:
                out_q.put(("progress",
                           dim(f"  ... scanned {scanned}/{len(files)}")))
            found = probe_candidates(filepath, skips)
            if found is None:
                continue
            info, tracks, header_langs = found
            if listing:
                out_q.put(("file", (filepath, info, tracks, header_langs, {})))
                continue
            if not announced:
                # until the first card lands, narrate the whisper work so a
                # fresh run never looks stuck. Concurrency is stated once, by
                # the Working block; repeating it here said the same fact
                # twice, two lines apart.
                # "starting with" read as a promise about the first card, but
                # scans finish out of order, so the first card is routinely a
                # different file. This names what it actually is.
                out_q.put(("progress",
                           ellipsize(f"             {dim('first scan')} "
                                     f"{filepath.name}", reserve=1)))
                announced = True
            pending.add(pool.submit(analyze, filepath, info, tracks,
                                    header_langs))
            # Cap work in flight: unbounded submission would probe the whole
            # queue up front and hold every result in memory.
            while len(pending) >= jobs * 2:
                pending = drain(pending)
        drain(pending, wait_all=True)
    out_q.put(("done", None))


def count_worker(files, skips, out_q, stop_event):
    """Second producer thread: ffprobe the whole queue to get the true
    remaining count, then report it once as ("total", (files, tracks)).

    Separate from scan_worker because that thread interleaves probing with
    25-45s whisper scans and would not reach the end of a 1500-file queue for
    hours. Probing alone is ~0.04s/file (plus ~0.06s of mkvmerge on files that
    still hold candidates), so the exact number lands a couple of minutes in,
    while the first cards are being answered - and the counter can drop its
    "~" once it does.
    """
    n_files = n_tracks = 0
    for filepath in files:
        if stop_event.is_set():
            return
        info = probe(filepath)
        if info is None:
            continue
        candidates = untagged_audio_tracks(info)
        if not candidates:
            continue
        header_langs = header_audio_langs(filepath)
        if header_langs is not None:
            candidates = [(pos, s) for pos, s in candidates
                          if header_langs.get(pos, "") in ("", "und")]
        live = [pos for pos, _ in candidates
                if f"{filepath}\ta{pos}" not in skips]
        if live:
            n_files += 1
            n_tracks += len(live)
    out_q.put(("total", (n_files, n_tracks)))


def track_summary(stream):
    codec = stream.get("codec_name", "?")
    ch = stream.get("channels", "?")
    title = stream.get("tags", {}).get("title", "")
    parts = [f"{codec} {ch}ch"]
    if title:
        parts.append(f'"{title}"')
    return ", ".join(parts)


def bulk_conflict(filepath, audio_pos, code):
    """Why a cached scan argues against bulk-tagging this track `code`, or None.

    The cache is the only evidence a bulk run has (it never scans), so this
    only ever speaks up about files something already looked at.

    zxx is held to a much lower bar than the language codes, because it is a
    claim of absence: any real vocabulary at all (BULK_ZXX_MIN_DISTINCT) is
    enough doubt to send the file to the human. Claiming one language over
    another instead needs the scan to have found actual speech, so that arm
    still asks for a coherent transcript."""
    entry = scan_cache_get(filepath, audio_pos, False)
    if not entry:
        return None
    uniq, words = entry.get("unique", 0.0), entry.get("words", 0)
    if code == "zxx":
        distinct = distinct_words(uniq, words)
        # Either a real vocabulary, or a short transcript that reads cleanly.
        # Distinct count alone cannot tell 15 genuine words from a loop that
        # happens to carry 15, so the second arm asks whether it repeats.
        if (distinct >= BULK_ZXX_MIN_DISTINCT
                or (words >= AUTO_MIN_WORDS and not repetitive(uniq, words))):
            return f"{words} words transcribed, {distinct} distinct"
        return None
    if not coherent(entry.get("chars", 0), uniq, words):
        return None
    detected = ISO1_TO_ISO2B.get(entry.get("iso1"))
    if detected and detected != code:
        return f"scanned as {detected}"
    return None


def bulk_tag(paths, code, skips):
    """Apply one language to every untagged track under `paths` after a single
    confirmation, logging each file to the audit ledger exactly as an
    individual tag would.

    This exists for the populations no scan can decide: ~200 pre-1940 animated
    shorts and a dialogue-free cartoon series, where zxx-vs-eng is a
    human judgement about sparse dialogue rather than something whisper can
    settle. The alternative is 200 identical keypresses. A previous ad-hoc
    script did this and wrote nothing to the ledger, which cost a session to
    reconstruct afterwards - hence one und->code row per track here, with the
    mode column reading 'bulk' so a batch is greppable and reversible as one.

    Paths are mandatory: a bulk run is only ever meant for a range a human has
    just looked at, never the whole library."""
    files = find_mkv_files(paths)
    if not files:
        print(red("No MKV files found under the given path(s)."))
        return 1
    print(dim(f"Checking {len(files)} file(s) for untagged audio..."))
    planned, conflicts, unscanned = [], [], 0
    for filepath in files:
        got = probe_candidates(filepath, skips)
        if got is None:
            continue
        _info, tracks, _header_langs = got
        for pos, _stream in tracks:
            reason = bulk_conflict(filepath, pos, code)
            if reason:
                conflicts.append((filepath, pos, reason))
            else:
                if not scan_cache_get(filepath, pos, False):
                    unscanned += 1
                planned.append((filepath, pos))
    if conflicts:
        print()
        print(yellow(f"Holding back {len(conflicts)} track(s) whose cached scan "
                     f"disagrees with '{code}':"))
        for filepath, pos, reason in conflicts[:10]:
            print(dim(f"  {os.path.basename(str(filepath))[:64]}  ({reason})"))
        if len(conflicts) > 10:
            print(dim(f"  ... and {len(conflicts) - 10} more"))
        print(dim("  Those need the interactive pass. Everything else "
                  "continues below."))
    if not planned:
        print(green("Nothing left to tag under that path."))
        return 0
    print()
    print(bold(f"About to set language={code} on {len(planned)} audio "
               f"track(s):"))
    for filepath, pos in planned[:10]:
        print(dim(f"  a{pos}  {os.path.basename(str(filepath))[:70]}"))
    if len(planned) > 10:
        print(dim(f"  ... and {len(planned) - 10} more"))
    print()
    if unscanned:
        # The held-back check reads the scan cache and nothing else, so a file
        # nobody has scanned passes it by default rather than by evidence.
        print(yellow(f"  {unscanned} of those have never been scanned, so "
                     "nothing has looked at them."))
    print(dim(f"  Every one is logged to {TAG_LOG_FILE} as und->{code}, so "
              "the batch can be reviewed or undone as a unit."))
    # Typing the count rather than "yes" means the number on screen has to
    # have been read - a bulk run's only real check is that a human looked.
    try:
        answer = input(f"  Type {len(planned)} to apply, anything else to "
                       f"abort > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer != str(len(planned)):
        print(yellow("Aborted, nothing was changed."))
        return 1
    applied = failed = 0
    for filepath, pos in planned:
        ok, note = apply_tag(filepath, pos, code)
        if ok:
            record_tag(filepath, pos, code, "bulk")
            applied += 1
            print(green(f"  tagged  a{pos}  "
                        f"{os.path.basename(str(filepath))[:64]}"))
        else:
            failed += 1
            print(red(f"  FAILED  a{pos}  "
                      f"{os.path.basename(str(filepath))[:64]}: {note}"))
    print()
    print(bold(f"Applied {applied} tag(s)") + (red(f", {failed} failed")
                                               if failed else ""))
    return 1 if failed else 0


def read_config_files():
    """KEY=value pairs from the config files, later files winning.

    Deliberately not an ini or a TOML parse: the whole file is a handful of
    paths, and a flat format is one an Ansible template or a shell `source` can
    write and read without a second opinion about sections."""
    values = {}
    for path in CONFIG_FILES:
        try:
            with open(os.path.expanduser(path)) as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip().upper()] = val.strip().strip('"').strip("'")
    return values


def setting(name, flag_value, conf):
    """One knob, resolved flag > env > config file. The config file accepts
    the key with or without the AUDIO_LANG_TAGGER_ prefix, because a file
    dedicated to this tool has no need to repeat its name on every line."""
    if flag_value is not None:
        return flag_value
    return (os.environ.get(ENV_PREFIX + name)
            or conf.get(ENV_PREFIX + name) or conf.get(name))


def default_state_dir():
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return os.path.expanduser(os.path.join(base, "audio-lang-tagger"))


def display_root(media_dirs, paths):
    """Prefix stripped off filenames on screen, or None to show them whole.

    The common parent of the media dirs, so a library configured as
    {root}/movies + {root}/series prints "movies/Title/file.mkv". A run with no
    media dirs configured falls back to the common parent of its own PATHs,
    which is the same idea applied to a one-off scope."""
    for candidate in (media_dirs, [str(p) for p in paths or []]):
        if not candidate:
            continue
        try:
            root = os.path.commonpath([os.path.abspath(p) for p in candidate])
        except ValueError:  # different drives, or a mix of relative/absolute
            continue
        if os.path.isfile(root):
            root = os.path.dirname(root)
        if root and root != os.sep:
            return root
    return None


def configure(args):
    """Resolve every path and host-calibrated number before any work starts."""
    global MEDIA_DIRS, STATE_DIR, SKIP_FILE, UNTAGGED_STATE_FILE, TAG_LOG_FILE
    global SCAN_CACHE_FILE, STATE_LOCK_FILE, INSTANCE_LOCK_FILE, DISPLAY_ROOT
    global MODEL_PATHS, WHISPER_BIN_OVERRIDE, WHISPER_REALTIME
    global FULL_SCAN_MAX_SECONDS, SONARR_CONFIG, RADARR_CONFIG
    global SONARR_URL, SONARR_API_KEY, RADARR_URL, RADARR_API_KEY, USE_ARR
    conf = read_config_files()

    dirs = args.media_dir
    if not dirs:
        raw = setting("MEDIA_DIRS", None, conf)
        dirs = [d for d in raw.split(os.pathsep) if d] if raw else []
    MEDIA_DIRS = [os.path.abspath(os.path.expanduser(d)) for d in dirs]

    state = setting("STATE_DIR", args.state_dir, conf)
    explicit_state = bool(state)
    STATE_DIR = (os.path.abspath(os.path.expanduser(state)) if state
                 else default_state_dir())
    SKIP_FILE = os.path.join(STATE_DIR, "lang_tagger_skips.txt")
    TAG_LOG_FILE = os.path.join(STATE_DIR, "lang_tagger_tags.tsv")
    SCAN_CACHE_FILE = os.path.join(STATE_DIR, "lang_tagger_scans.json")
    STATE_LOCK_FILE = os.path.join(STATE_DIR, "lang_tagger.state.lock")
    INSTANCE_LOCK_FILE = os.path.join(STATE_DIR, "lang_tagger.instance.lock")
    queue = setting("QUEUE_FILE", args.queue_file, conf)
    UNTAGGED_STATE_FILE = (os.path.abspath(os.path.expanduser(queue)) if queue
                           else os.path.join(STATE_DIR, "untagged_audio.json"))

    DISPLAY_ROOT = display_root(MEDIA_DIRS, args.paths)

    model = setting("MODEL", args.model, conf)
    if model:
        MODEL_PATHS = [os.path.expanduser(model)]
    WHISPER_BIN_OVERRIDE = setting("WHISPER_BIN", args.whisper_bin, conf)

    realtime = setting("REALTIME", args.realtime_factor, conf)
    if realtime:
        try:
            WHISPER_REALTIME = max(1.0, float(realtime))
        except ValueError:
            print(f"ERROR: realtime factor '{realtime}' is not a number")
            sys.exit(1)
    # Both derive from it, so they cannot be left at the module-level value.
    FULL_SCAN_MAX_SECONDS = SCAN_BUDGET_SECONDS * WHISPER_REALTIME

    SONARR_CONFIG = setting("SONARR_CONFIG", args.sonarr_config,
                            conf) or SONARR_CONFIG
    RADARR_CONFIG = setting("RADARR_CONFIG", args.radarr_config,
                            conf) or RADARR_CONFIG
    SONARR_URL = os.environ.get("SONARR_URL") or conf.get("SONARR_URL")
    SONARR_API_KEY = (os.environ.get("SONARR_API_KEY")
                      or conf.get("SONARR_API_KEY"))
    RADARR_URL = os.environ.get("RADARR_URL") or conf.get("RADARR_URL")
    RADARR_API_KEY = (os.environ.get("RADARR_API_KEY")
                      or conf.get("RADARR_API_KEY"))
    USE_ARR = not args.no_arr

    # An explicitly configured state dir is usually on the media mount, where a
    # missing directory means the mount is gone rather than a first run - so
    # creating it silently would scatter state across an empty mountpoint. The
    # default one under XDG_STATE_HOME has no such meaning and is just made.
    if not os.path.isdir(STATE_DIR):
        if explicit_state:
            print(f"ERROR: state dir {STATE_DIR} not found - storage not "
                  "mounted?")
            sys.exit(1)
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
        except OSError as exc:
            print(f"ERROR: cannot create state dir {STATE_DIR}: {exc}")
            sys.exit(1)


def require_media_dirs():
    """MEDIA_DIRS, or exit explaining how to set them. A sweep is the one mode
    that cannot infer its own scope."""
    if not MEDIA_DIRS:
        print(red("No media dirs configured, so there is nothing to sweep."))
        print("  --media-dir /path/to/movies --media-dir /path/to/series")
        print(f"  or {ENV_PREFIX}MEDIA_DIRS=/path/to/movies"
              f"{os.pathsep}/path/to/series")
        print(f"  or MEDIA_DIRS=... in {CONFIG_FILES[1]}")
        print("  (or just pass the files/dirs you want as arguments)")
        sys.exit(1)
    return MEDIA_DIRS


def show_config():
    print(f"media dirs     {os.pathsep.join(MEDIA_DIRS) or '(none set)'}")
    print(f"state dir      {STATE_DIR}")
    print(f"queue file     {UNTAGGED_STATE_FILE}")
    print(f"tag ledger     {TAG_LOG_FILE}")
    print(f"scan cache     {SCAN_CACHE_FILE}")
    print(f"skip list      {SKIP_FILE}")
    print(f"display root   {DISPLAY_ROOT or '(none, full paths shown)'}")
    binary = WHISPER_BIN_OVERRIDE or next(
        (shutil.which(b) for b in WHISPER_BINS if shutil.which(b)), None)
    print(f"whisper bin    {binary or 'not found on PATH'}")
    model = next((m for m in MODEL_PATHS if os.path.isfile(m)), None)
    print(f"model          {model or 'not found in ' + ', '.join(MODEL_PATHS)}")
    print(f"realtime       {WHISPER_REALTIME}x "
          f"(whole-track budget {FULL_SCAN_MAX_SECONDS / 60:.0f} min)")
    if USE_ARR:
        for label, url, key, config in (
                ("sonarr", SONARR_URL, SONARR_API_KEY, SONARR_CONFIG),
                ("radarr", RADARR_URL, RADARR_API_KEY, RADARR_CONFIG)):
            base, api_key = arr_endpoint(url, key, config)
            if not base:
                print(f"{label}         unavailable ({config} unreadable and "
                      f"no {label.upper()}_URL + {label.upper()}_API_KEY set)")
                continue
            # A resolved endpoint says nothing about whether the URL and key
            # actually work, so probe: this is the tool's connection test.
            print(f"{label}         {base}  ->  {arr_probe(base, api_key)}")
    else:
        print("sonarr/radarr  disabled (--no-arr)")


def build_tui(work_q, stop_event, args, skips, stats, touched, expected_map,
              whisper_bin, model, est_files, est_exact):
    """Full-screen interactive pass (textual). Consumes the same scan queue
    as the line UI and routes every decision through the same
    apply/record/skip helpers, so the ledger, cache, stats and queue pruning
    are identical whichever UI ran. Returns the App instance - run_tui runs
    it for real; tests drive it headless through textual's run_test."""
    import io
    from rich.text import Text
    from textual.app import App
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import Input, Static

    def from_ansi(txt):
        return Text.from_ansi(txt.rstrip("\n"))

    class _Relay(io.TextIOBase):
        """stdout shim for deep/full scans: their progress prints become
        status-line updates instead of writes underneath the alt screen."""

        def __init__(self, cb):
            self.cb = cb
            self.buf = ""

        def write(self, text):
            self.buf += text
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if line.strip():
                    self.cb(line.strip())
            return len(text)

    class TaggerApp(App):
        BINDINGS = [
            Binding("enter", "accept", "tag", show=False),
            Binding("c", "code", "code", show=False),
            Binding("d", "deep", "deep scan", show=False),
            Binding("f", "full", "full scan", show=False),
            Binding("s", "skip", "skip file", show=False),
            Binding("n", "never", "never ask", show=False),
            Binding("u", "undo", "undo", show=False),
            Binding("q", "quit_run", "quit", show=False),
            Binding("ctrl+c", "quit_run", "quit", show=False, priority=True),
            Binding("escape", "cancel", "cancel", show=False, priority=True),
        ]
        CSS = """
        /* One compact block centered on the screen, not islands pinned to
           its edges: header, card and key hints travel together, so the
           hints sit right under the card instead of at the bottom bezel.
           The card viewport is FIXED at the tallest realistic card (title
           2 + blank + 6 fact rows + wrapped note/heard + padding) so the
           block never changes height, and the screen does not jump,
           between cards. Anything taller scrolls inside it. _fit_body()
           caps it below that on small terminals: a CSS fraction resolves
           against the whole screen, which let a tall card push the hints
           off the bottom. */
        Screen { align-vertical: middle; }
        #header { height: 3; padding: 0 1; background: $panel; }
        #bodywrap { height: 16; }
        #body { padding: 1 1; height: auto; }
        #status { height: 1; padding: 0 1; }
        #recent { height: 1; padding: 0 1; }
        #entry { display: none; }
        #hints { height: 1; padding: 0 1; background: $panel; }
        """

        def __init__(self):
            super().__init__()
            self.pending = []
            self.cur = None    # the file whose tracks are being judged
            self.track = None  # (pos, stream, auto_reason)
            self.code = None
            self.whole = False
            self.duration = 0.0
            self.busy = None   # "deep"/"full" while a rescan thread runs
            self.confirm_full = False
            self.pending_code = None
            self.feeding = True
            self.est = est_files
            self.exact = est_exact
            self.note = ""
            self.recent = []
            self.undo_state = None  # last decision, single-level
            self.resume = None      # (cur, track) preempted by an undo

        def compose(self):
            yield Static(id="header")
            yield VerticalScroll(Static(id="body"), id="bodywrap")
            yield Static(id="status")
            yield Static(id="recent")
            yield Input(placeholder="3-letter code, esc cancels", id="entry")
            yield Static(id="hints")

        def on_mount(self):
            self._fit_body()
            self._show_waiting()
            self._refresh_header()
            self._refresh_hints()
            threading.Thread(target=self._pump, daemon=True).start()

        def on_resize(self, event):
            self._fit_body()

        def _fit_body(self):
            """Cap the card viewport so the rows below it always stay on
            screen: header 3, status 1, recent 1, hints 1, plus the code
            entry while it is open."""
            fixed = 6
            entry = self.query_one("#entry", Input)
            if entry.display:
                fixed += max(entry.outer_size.height, 3)
            self.query_one("#bodywrap").styles.max_height = max(
                3, self.screen.size.height - fixed)

        # ---- scan-queue plumbing ------------------------------------------

        def _pump(self):
            while True:
                kind, payload = work_q.get()
                try:
                    self.call_from_thread(self._on_msg, kind, payload)
                except Exception:
                    return
                if kind == "done":
                    return

        def _on_msg(self, kind, payload):
            if kind == "done":
                self.feeding = False
                if self.cur is None and not self.pending:
                    self.exit()
                    return
            elif kind == "progress":
                plain = ANSI_RE.sub("", payload).strip()
                if "scan failed" in plain:
                    self._status(red(plain))
                else:
                    self.note = plain
            elif kind == "total":
                n_files, n_tracks = payload
                self.est = max(n_files, stats["files_with_und"])
                self.exact = True
            else:
                self.pending.append(payload)
                if self.cur is None:
                    self._advance_file()
            self._refresh_header()

        def _advance_file(self):
            while self.pending:
                filepath, info, tracks, header_langs, guesses = \
                    self.pending.pop(0)
                stats["files_with_und"] += 1
                try:
                    shown = (filepath.relative_to(DISPLAY_ROOT)
                             if DISPLAY_ROOT else filepath)
                except ValueError:
                    shown = filepath
                self.cur = {"filepath": filepath, "info": info,
                            "tracks": tracks, "header_langs": header_langs,
                            "guesses": guesses, "shown": shown, "idx": 0,
                            "exp": expected_for(filepath, expected_map)}
                if self._advance_track():
                    return
            self.cur = None
            self.track = None
            if not self.feeding:
                self.exit()
                return
            self._show_waiting()
            self._refresh_header()

        def _advance_track(self):
            """Show the file's next un-judged track; True when one is on
            screen, False when the file is exhausted (auto may eat them all)."""
            cur = self.cur
            while cur["idx"] < len(cur["tracks"]):
                pos, s = cur["tracks"][cur["idx"]]
                cur["idx"] += 1
                guess = cur["guesses"].get(pos)
                auto_reason = None
                if args.auto:
                    auto_reason = auto_gate(guess, cur["exp"], cur["tracks"],
                                            cur["header_langs"], s)
                    if auto_reason is None:
                        auto_code = ISO1_TO_ISO2B[guess["iso1"]]
                        ok, _msg = apply_tag(cur["filepath"], pos, auto_code)
                        if ok:
                            record_tag(cur["filepath"], pos, auto_code,
                                       "auto", guess)
                            touched.add(cur["filepath"])
                            stats["auto"] += 1
                            self._remember(green("auto " + auto_code),
                                           cur["shown"].name)
                        else:
                            stats["errors"] += 1
                            self._remember(red("auto failed"),
                                           cur["shown"].name)
                        continue
                self.track = (pos, s, auto_reason)
                self._render_card()
                return True
            return False

        def _next_track(self):
            if self.resume is not None:
                # An undo pushed the then-current card aside; the re-answer
                # returns to it instead of pulling from the queue.
                cur, track = self.resume
                self.resume = None
                if track is not None:
                    self.cur, self.track = cur, track
                    self._render_card()
                    return
                self.cur = None
                self.track = None
                self._advance_file()
                return
            if not self._advance_track():
                self._advance_file()

        # ---- rendering ----------------------------------------------------

        def _render_card(self):
            cur = self.cur
            pos, s, auto_reason = self.track
            self.duration = float(
                cur["info"].get("format", {}).get("duration", 0) or 0)
            try:
                filesize = int(cur["info"].get("format", {}).get("size", 0)
                               or 0)
            except (TypeError, ValueError):
                filesize = 0
            if not filesize:
                try:
                    filesize = cur["filepath"].stat().st_size
                except OSError:
                    filesize = 0
            guess = cur["guesses"].get(pos)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.code, self.whole = print_card(
                    pos, s, guess, cur["exp"], cur["info"],
                    cur["header_langs"], self.duration, filesize, auto_reason)
            counter = self._counter()
            parent = str(cur["shown"].parent)
            if parent and parent != ".":
                title = (f"{dim(counter)} {dim(parent + '/')}\n"
                         f"{' ' * (len(counter) + 1)}"
                         f"{bold(cyan(cur['shown'].name))}")
            else:
                title = f"{dim(counter)} {bold(cyan(cur['shown'].name))}"
            self.query_one("#body", Static).update(
                from_ansi(title + "\n" + buf.getvalue()))
            self.confirm_full = False
            self.pending_code = None
            self._status("")
            self._refresh_header()
            self._refresh_hints()

        def _counter(self):
            seen = stats["files_with_und"]
            if self.est:
                return f"[{seen}{'/' if self.exact else '/~'}{self.est}]"
            return f"[{seen}]"

        def _show_waiting(self):
            self.query_one("#body", Static).update(
                from_ansi(dim("waiting for the next scan to finish...")))
            self._refresh_hints()

        def _refresh_header(self):
            seen = stats["files_with_und"]
            counts = (f"tagged {green(str(stats['tagged']))}"
                      + (f"  auto {green(str(stats['auto']))}"
                         if args.auto else "")
                      + f"  never {stats['skipped']}")
            if self.est:
                width = 24
                fill = min(width, int(width * seen / max(self.est, 1)))
                bar = green("━" * fill) + dim("╌" * (width - fill))
                line1 = f"{bar}  {bold(self._counter())}   {counts}"
            else:
                line1 = f"{bold(str(seen))} files seen   {counts}"
            state = ("queue drained" if not self.feeding
                     else (self.note or "scanning"))
            _entries, hits = scan_cache_stats()
            # The scan note carries a filename, so it owns a row: sharing one
            # with the buffer figures ellipsized all three into noise.
            line2 = dim(ellipsize(state, reserve=4))
            line3 = dim(f"{plural(len(self.pending), 'card')} buffered   "
                        f"cache hits {hits}")
            self.query_one("#header", Static).update(
                from_ansi(line1 + "\n" + line2 + "\n" + line3))

        def _refresh_hints(self):
            if self.busy:
                hints = dim(f"{self.busy} scan running - other keys wait "
                            "until it lands (q still quits)")
            elif self.track is None:
                hints = dim("u=undo    q=quit" if self.undo_state
                            else "q=quit")
            else:
                enter_hint = (f"enter{dim('=tag ' + self.code)}" if self.code
                              else dim("enter=n/a"))
                keys = ["c=code"]
                if not self.whole:
                    keys += ["d=deep scan",
                             f"f=full scan ~{full_scan_estimate(self.duration)}m"]
                keys += ["s=skip file", "n=never ask"]
                if self.undo_state:
                    keys.append("u=undo")
                keys.append("q=quit")
                hints = f"{enter_hint}    {dim('    '.join(keys))}"
            self.query_one("#hints", Static).update(from_ansi(hints))

        def _refresh_recent(self):
            # Only the latest decision: it is the one 'u' would take back,
            # and older entries next to it just diluted that meaning.
            if self.recent:
                verdict, name = self.recent[-1]
                shown = f"{dim('last')}   {verdict} {name}"
            else:
                shown = ""
            self.query_one("#recent", Static).update(from_ansi(shown))

        def _remember(self, verdict, name):
            self.recent.append((verdict, name))
            self._refresh_recent()

        def _status(self, text):
            self.query_one("#status", Static).update(from_ansi(text))

        # ---- actions ------------------------------------------------------

        def action_accept(self):
            if self.busy or self.track is None:
                return
            if not self.code:
                self._status(red("no guess to accept")
                             + dim(" - press c and type a 3-letter code"))
                return
            self._apply(self.code)

        def _apply(self, chosen):
            cur = self.cur
            pos, _s, _reason = self.track
            guess = cur["guesses"].get(pos)
            ok, msg = apply_tag(cur["filepath"], pos, chosen)
            if ok:
                record_tag(cur["filepath"], pos, chosen, "manual", guess)
                touched.add(cur["filepath"])
                stats["tagged"] += 1
                self._snap_undo("tag", chosen)
                self._remember(green(chosen), cur["shown"].name)
            else:
                stats["errors"] += 1
                self._remember(red("failed"), cur["shown"].name)
                self._status(red(f"tag failed: {msg}"))
            self._next_track()

        def action_skip(self):
            if self.busy or self.track is None:
                return
            self._snap_undo("skip")
            self._remember(yellow("skip"), self.cur["shown"].name)
            self._advance_file()

        def action_never(self):
            if self.busy or self.track is None:
                return
            cur = self.cur
            pos, _s, _reason = self.track
            record_skip(f"{cur['filepath']}\ta{pos}")
            skips.add(f"{cur['filepath']}\ta{pos}")
            touched.add(cur["filepath"])
            stats["skipped"] += 1
            self._snap_undo("never")
            self._remember(yellow("never"), cur["shown"].name)
            self._next_track()

        def _snap_undo(self, kind, code=None):
            """Remember the decision just made, before advancing. Single
            level: each decision replaces the previous snapshot."""
            self.undo_state = {"kind": kind, "cur": self.cur,
                               "track": self.track, "code": code,
                               "recent_len": len(self.recent)}

        def action_undo(self):
            if self.busy:
                return
            u = self.undo_state
            if u is None:
                self._status(dim("nothing to undo"))
                return
            cur = u["cur"]
            pos, _s, _reason = u["track"]
            if u["kind"] == "tag":
                ok, msg = apply_tag(cur["filepath"], pos, "und")
                if not ok:
                    self._status(red(f"undo failed: {msg}"))
                    return
                record_tag(cur["filepath"], pos, "und", "undo",
                           cur["guesses"].get(pos), old=u["code"])
                stats["tagged"] -= 1
                # both edits moved the mtime, so re-key the cached scan:
                # an undo followed by a quit must not cost a re-scan
                guess = cur["guesses"].get(pos)
                if guess:
                    scan_cache_put(cur["filepath"], pos, guess, args.auto)
            elif u["kind"] == "never":
                key = f"{cur['filepath']}\ta{pos}"
                remove_skip(key)
                skips.discard(key)
                stats["skipped"] -= 1
            self.undo_state = None
            # drop the undone decision from the recent strip by position -
            # auto tags may have appended behind it in the meantime
            idx = u["recent_len"]
            if idx < len(self.recent):
                del self.recent[idx]
                self._refresh_recent()
            self.resume = (self.cur, self.track)
            self.cur, self.track = cur, u["track"]
            self._render_card()

        def action_code(self):
            if self.busy or self.track is None:
                return
            entry = self.query_one("#entry", Input)
            entry.value = ""
            entry.display = True
            entry.focus()
            self._fit_body()

        def on_input_submitted(self, event):
            chosen = event.value.strip().lower()
            if not (len(chosen) == 3 and chosen.isalpha()):
                self._status(red(f"'{chosen}' is not a 3-letter code"))
                return
            if chosen not in VALID_ISO2 and self.pending_code != chosen:
                self.pending_code = chosen
                self._status(yellow(f"'{chosen}' not in the known-code list"
                                    " - submit again to use it anyway"))
                return
            self.action_cancel()
            self._apply(chosen)

        def action_cancel(self):
            entry = self.query_one("#entry", Input)
            entry.display = False
            self.pending_code = None
            self.set_focus(None)
            self._status("")
            self._fit_body()

        def action_deep(self):
            self._rescan("deep")

        def action_full(self):
            if (self.duration > FULL_SCAN_WARN_SECONDS
                    and not self.confirm_full and not self.busy):
                self.confirm_full = True
                est = full_scan_estimate(self.duration)
                self._status(yellow(f"long file - a full scan takes roughly "
                                    f"{est} min. press f again to confirm"))
                return
            self._rescan("full")

        def _rescan(self, kind):
            if self.busy or self.track is None:
                return
            if self.whole:
                self._status(dim("whole track already scanned - nothing "
                                 "deeper to try"))
                return
            self.busy = kind
            self.confirm_full = False
            self._status(cyan(f"{kind} scan starting..."))
            self._refresh_hints()
            cur = self.cur
            pos, _s, _reason = self.track
            guess = cur["guesses"].get(pos)
            fn = deep_scan if kind == "deep" else full_scan

            def relay(line):
                try:
                    self.call_from_thread(self._status, dim(line))
                except Exception:
                    pass

            def bg():
                try:
                    with contextlib.redirect_stdout(_Relay(relay)):
                        new = fn(whisper_bin, model, cur["filepath"], pos,
                                 self.duration, guess) or guess
                    if scan_children_killed():
                        return
                    scan_cache_put(cur["filepath"], pos, new, args.auto)
                except Exception as exc:
                    err = f"{kind} scan failed: {type(exc).__name__}: {exc}"
                    try:
                        self.call_from_thread(self._rescan_failed, err)
                    except Exception:
                        pass
                    return
                try:
                    self.call_from_thread(self._rescan_done, pos, new)
                except Exception:
                    pass

            threading.Thread(target=bg, daemon=True).start()

        def _rescan_done(self, pos, new):
            self.busy = None
            if self.cur is not None:
                self.cur["guesses"][pos] = new
            if self.track is not None and self.track[0] == pos:
                self._render_card()

        def _rescan_failed(self, err):
            self.busy = None
            self._status(red(err))
            self._refresh_hints()

        def action_quit_run(self):
            stop_event.set()
            kill_scan_children()
            self.exit()

    return TaggerApp()


def run_tui(*tui_args):
    build_tui(*tui_args).run()


def main():
    parser = argparse.ArgumentParser(description="Interactive MKV audio language tagger")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("paths", nargs="*", default=None,
                        help="files/dirs to scan (default: the saved "
                             "candidate queue)")
    parser.add_argument("--list", action="store_true",
                        help="only report untagged tracks, no prompts or edits")
    parser.add_argument("--full", action="store_true",
                        help="re-sweep every configured media dir instead of "
                             "working the saved queue")
    parser.add_argument("--auto", action="store_true",
                        help="tag without prompting when every gate passes "
                             "(unanimous high-confidence samples, coherent "
                             "transcript, arr agreement, 1940+, non-music, "
                             "single non-commentary track); everything else "
                             "still prompts. Every tag is appended to the "
                             "ledger in the state dir.")
    parser.add_argument("--prescan", action="store_true",
                        help="unattended: scan the queue, fill the scan cache, "
                             "auto-tag gate-passers and leave everything else "
                             "for a later interactive pass. Needs no tty, so "
                             "run it in the background; the interactive pass "
                             "afterwards is then instant on cache hits.")
    parser.add_argument("--bulk", metavar="CODE",
                        help="set this ISO 639-2 code on every untagged track "
                             "under the given PATHs after one confirmation, "
                             "for ranges a human has judged as a whole (the "
                             "pre-1940 zxx-vs-eng call). Never scans; a cached "
                             "scan that disagrees holds its file back. Logged "
                             "per file like any other tag.")
    parser.add_argument("--jobs", type=int, default=SCAN_JOBS,
                        help=f"concurrent whisper scans (default {SCAN_JOBS}; "
                             "benchmarked best on a 6-core box, only ~1.28x "
                             "over one job since whisper parallelises "
                             "internally)")
    parser.add_argument("--plain", action="store_true",
                        help="line-mode prompts instead of the full-screen "
                             "interface (also the automatic fallback when "
                             "textual is not installed)")

    cfg = parser.add_argument_group(
        "configuration",
        "Each also reads AUDIO_LANG_TAGGER_<NAME> from the environment, then "
        "~/.config/audio-lang-tagger.conf, then /etc/audio-lang-tagger.conf "
        "(KEY=value).")
    cfg.add_argument("--media-dir", action="append", metavar="DIR",
                     help="library root to sweep, repeatable. Needed by --full "
                          "and by a queue run; explicit PATHs need nothing. "
                          f"Env/config: MEDIA_DIRS, '{os.pathsep}'-separated")
    cfg.add_argument("--state-dir", metavar="DIR",
                     help="where the skip list, tag ledger, scan cache and "
                          "locks live (default: "
                          "$XDG_STATE_HOME/audio-lang-tagger). Must already "
                          "exist when given, so a missing mount is an error "
                          "rather than a fresh empty state")
    cfg.add_argument("--queue-file", metavar="PATH",
                     help="candidate queue JSON written by an external library "
                          "scanner (default: untagged_audio.json in the state "
                          "dir)")
    cfg.add_argument("--model", metavar="PATH",
                     help="whisper.cpp model file (default: first of "
                          + ", ".join(MODEL_PATHS) + " that exists)")
    cfg.add_argument("--whisper-bin", metavar="PATH",
                     help="whisper.cpp binary (default: "
                          + "/".join(WHISPER_BINS) + " on PATH)")
    cfg.add_argument("--realtime-factor", metavar="N",
                     help=f"how many times realtime this host transcribes "
                          f"(default {WHISPER_REALTIME}). Sets the whole-track "
                          "scan budget and the 'f' estimate; re-measure on "
                          "different hardware")
    cfg.add_argument("--sonarr-config", metavar="PATH",
                     help=f"Sonarr config.xml (default {SONARR_CONFIG}). Or "
                          "set SONARR_URL + SONARR_API_KEY to reach a remote "
                          "one")
    cfg.add_argument("--radarr-config", metavar="PATH",
                     help=f"Radarr config.xml (default {RADARR_CONFIG}). Or "
                          "set RADARR_URL + RADARR_API_KEY")
    cfg.add_argument("--no-arr", action="store_true",
                     help="skip the Sonarr/Radarr lookup entirely. --auto then "
                          "tags nothing, since arr agreement is a gate")
    cfg.add_argument("--show-config", action="store_true",
                     help="print the resolved configuration and exit")

    args = parser.parse_args()
    configure(args)
    if args.show_config:
        show_config()
        return
    if args.full and not args.paths:
        # Checked here rather than at the sweep, which happens after the
        # whisper lookup: an unconfigured scope should not be reported as a
        # whisper installation problem.
        require_media_dirs()
    jobs = max(1, args.jobs)
    # Split the box between jobs rather than letting each take whisper's
    # default 4 threads, which would oversubscribe and slow every scan down.
    threads = max(1, (os.cpu_count() or 2) // jobs)
    if args.prescan:
        args.auto = True  # prescan tags only what the --auto gates allow

    if args.bulk:
        args.bulk = args.bulk.strip().lower()
        if args.bulk not in VALID_ISO2:
            print(f"ERROR: '{args.bulk}' is not a known ISO 639-2/B code "
                  "(zxx = no linguistic content, mul = multiple)")
            sys.exit(1)
        if not args.paths:
            print("ERROR: --bulk needs explicit PATHs - it is for a range you "
                  "have just judged, not the whole library")
            sys.exit(1)
        if args.auto or args.prescan or args.list or args.full:
            print("ERROR: --bulk applies one code by hand, so it does not "
                  "combine with --auto/--prescan/--list/--full")
            sys.exit(1)
        if not sys.stdin.isatty():
            print("--bulk needs a tty to confirm "
                  "(over ssh: ssh -t HOST audio-lang-tagger.py --bulk ...)")
            sys.exit(1)

    # Everything downstream assumes these exist; better one message now than
    # a traceback at the first tag. --list only probes, --bulk never scans.
    if args.list:
        require_binaries(["ffprobe"])
    elif args.bulk:
        require_binaries(["ffprobe", "mkvpropedit", "mkvmerge", "mkvextract"])
    else:
        require_binaries(["ffprobe", "ffmpeg", "mkvpropedit", "mkvmerge",
                          "mkvextract"])

    whisper_bin = model = None
    if not args.list and not args.bulk:  # a bulk run never scans
        whisper_bin, model = find_whisper()
        if not args.prescan and not sys.stdin.isatty():
            print("Interactive mode needs a tty "
                  "(over ssh: ssh -t HOST audio-lang-tagger.py)")
            print("For an unattended sweep that prompts for nothing: --prescan")
            sys.exit(1)

    if not args.list:
        other = claim_instance_lock()
        if other:
            print(yellow(bold(f"Another tagger instance is running "
                              f"(pid {other}).")))
            if args.prescan:
                print(red("  Refusing to start a second unattended sweep. Stop "
                          "the other first:"))
                print(red("    pkill -INT -f 'lang-tagger[.]py'"))
                sys.exit(1)
            print(dim("  Shared state merges under a lock, so neither run "
                      "loses work. What isn't safe: both can reach the same "
                      "file and tag it twice."))
            # Telling someone to interrupt and then continuing in the same
            # breath makes the advice unusable - by the time it is read the
            # first scans are already dispatched. Hold long enough to act on.
            print(yellow(f"  Starting in {INSTANCE_WARN_PAUSE}s - Ctrl-C now "
                         f"if that wasn't intended."))
            time.sleep(INSTANCE_WARN_PAUSE)

    skips = load_skips()
    if not args.list:
        cached = load_scan_cache()
        # Covers every exit path - a clean finish, a 'q', a Ctrl-C mid-scan, or
        # an unhandled exception - so a long sweep never loses its work.
        # atexit misses signal deaths, hence the handler alongside it.
        atexit.register(save_scan_cache)
        install_signal_flush(unattended=args.prescan)
        if cached:
            print()
            print(f"Scan cache   {num(cached)} saved whisper results")

    if args.bulk:
        # After the cache load, so a scan that contradicts the code can hold
        # its file back.
        sys.exit(bulk_tag(args.paths, args.bulk, skips))

    queue_state = None
    if args.paths:
        files = find_mkv_files(args.paths)
    elif args.full:
        files = find_mkv_files(require_media_dirs())
    else:
        files, queue_state = load_worklist()
        if files is None:
            print(yellow("No saved candidate queue yet - falling back to a "
                         "full sweep."))
            files = find_mkv_files(require_media_dirs())
            queue_state = None
    if queue_state is not None and not files:
        print()
        print(green("Nothing to tag."))
        return

    # Scope and workload are one statement, not two: printed apart they
    # repeated the same totals on consecutive lines, and the counter below
    # then repeated them a third time.
    est_files = None
    est_exact = False
    origin = ""
    if args.paths or queue_state is not None:
        # An explicit PATH is its own workload, so the saved queue's
        # library-wide total describes a different run entirely: it put an
        # 18-file season behind a [1/~1500] counter. len(files) is still an
        # upper bound (only files with und tracks produce a card), hence the
        # "~" until the background recount lands and tightens it.
        est_files = len(files)
        origin = (f"saved queue {queue_state.get('date', '?')}"
                  if queue_state is not None
                  else ", ".join(Path(p).name for p in args.paths))
    else:
        origin = ", ".join(os.path.basename(d) for d in MEDIA_DIRS)
        try:
            with open(UNTAGGED_STATE_FILE) as f:
                st = json.load(f)
            est_files = int(st.get("files", 0)) or None
            origin = f"full scan {st.get('date', '?')}"
        except (OSError, ValueError, json.JSONDecodeError):
            est_files = None

    already = 0
    # Not len(files): under --full that counts every MKV, while est_files is
    # the queue's untagged subset, so the two are different quantities.
    total_before = est_files
    if est_files and not args.list:
        # Discount work already done since that scan. Free, and it makes the
        # denominator honest from the first card rather than after the recount.
        already = len({p for p in files if str(p) in load_resolved_paths()})
        if already:
            est_files = max(1, est_files - already)

    if est_files:
        done = dim(f" ({total_before} minus {already} done)") if already else ""
        print(ellipsize(f"Scope        {num(est_files)} files, "
                        f"{bold(origin)}{done}", reserve=1))
    else:
        print(dim("Scope        unknown until a full sweep has counted the "
                  "library once"))

    expected_map = {}
    if not args.list:
        expected_map = build_expected_map()
        if expected_map:
            # The role only earns its words under --auto, where this data
            # decides what gets tagged without asking.
            role = (dim(", feeds the language/year/genre gates") if args.auto
                    else "")
            print(f"Arr metadata {num(len(expected_map))} titles{role}")
        else:
            print(yellow("Arr metadata unavailable - no expected-language "
                         "comparison."))
        # Interactive is the default, so announcing it says nothing. The other
        # two change what happens without asking and stay loud.
        if args.prescan:
            print(f"Mode         {green(bold('prescan'))}, unattended: "
                  f"gate-passers are tagged, the rest is cached for a later "
                  f"interactive pass. {dim('Nothing prompts.')}")
        elif args.auto:
            print(f"Mode         {green(bold('auto'))}, gate-passers tag "
                  f"silently as green 'auto' lines   {dim(TAG_LOG_FILE)}")
        if args.auto and not expected_map:
            print(yellow("             ...but without arr data no track can "
                         "pass the gates, so nothing will be auto-tagged."))

    print()

    stats = {"files_with_und": 0, "tagged": 0, "auto": 0, "skipped": 0,
             "errors": 0, "deferred": 0}
    touched = set()  # files finished this run, for the exit prune
    t0 = time.monotonic()
    quit_requested = False

    if not args.list:
        # Cached files first, queue order preserved within each half: the
        # first cards are then instant however stale the last prescan is,
        # while whisper chews the unscanned tail in the background. One
        # stat() per file buys the mtime check that makes "warm" mean
        # "actually servable", not "scanned once, maybe replaced since".
        servable = scan_cache_servable_mtimes(args.auto)

        def cache_ready(f):
            try:
                return int(os.path.getmtime(f)) in servable.get(str(f), ())
            except OSError:
                return False

        warm_files, cold_files = [], []
        for f in files:
            (warm_files if cache_ready(f) else cold_files).append(f)
        files = warm_files + cold_files
        # "N of M already scanned" only restated the cache and scope lines
        # above; one word about the wait is all the line owes.
        if not cold_files:
            first = "first card immediately, whole scope already scanned"
        elif warm_files:
            first = (f"first card immediately, {num(len(warm_files))} "
                     f"cached files first")
        else:
            first = "first card in ~15-60s"
        print(f"{cyan(bold('Working'))}      {first}"
              f"{dim(f', {jobs} scans at {threads} threads')}")
    work_q = queue.Queue(maxsize=3)
    stop_event = threading.Event()
    threading.Thread(
        target=scan_worker,
        args=(files, skips, whisper_bin, model, args.list, args.auto,
              work_q, stop_event, jobs, threads),
        daemon=True,
    ).start()
    if not args.list and est_files:
        threading.Thread(
            target=count_worker,
            args=(files, skips, work_q, stop_event),
            daemon=True,
        ).start()

    use_tui = not (args.plain or args.list or args.prescan)
    if use_tui:
        try:
            import textual  # noqa: F401
        except ImportError:
            use_tui = False
            print(yellow("textual is not installed - line-mode prompts "
                         "(pacman -S python-textual for the full-screen UI)"))
    if use_tui:
        run_tui(work_q, stop_event, args, skips, stats, touched, expected_map,
                whisper_bin, model, est_files, est_exact)
        quit_requested = True  # the TUI consumed the queue; skip the line loop

    while not quit_requested:
        kind, payload = work_q.get()
        if kind == "done":
            break
        if kind == "progress":
            print(payload)
            continue
        if kind == "total":
            n_files, n_tracks = payload
            est_files = max(n_files, stats["files_with_und"])
            est_exact = True
            print(dim(f"  recount done: {n_tracks} untagged track(s) in "
                      f"{n_files} file(s) still to go"))
            continue
        filepath, info, tracks, header_langs, guesses = payload

        stats["files_with_und"] += 1
        try:
            shown_path = (filepath.relative_to(DISPLAY_ROOT) if DISPLAY_ROOT
                          else filepath)
        except ValueError:
            shown_path = filepath
        if est_files:
            # No "~" once the background recount has landed: the number is
            # then exactly what is left, not last Sunday's total.
            sep = "/" if est_exact else "/~"
            counter = f"[{stats['files_with_und']}{sep}{est_files}]"
        else:
            counter = f"[{stats['files_with_und']}]"
        print(dim("\u2500" * 62))
        # Directory and filename on separate lines: these names run past any
        # terminal width and wrapping them mid-word buried the episode title.
        parent = str(shown_path.parent)
        if parent and parent != ".":
            print(f"{dim(counter)} {dim(parent + '/')}")
            print(f"{' ' * (len(counter) + 1)}{bold(cyan(shown_path.name))}")
        else:
            print(f"{dim(counter)} {bold(cyan(shown_path.name))}")

        if args.list:
            for pos, s in tracks:
                print(f"  track a{pos}: {track_summary(s)} " + dim("- language missing"))
            print()
            continue

        exp = expected_for(filepath, expected_map)

        skip_file = False
        for pos, s in tracks:
            if quit_requested:
                break
            guess = guesses.get(pos)
            code = None
            auto_reason = None
            if args.auto:
                auto_reason = auto_gate(guess, exp, tracks, header_langs, s)
                if auto_reason is None:
                    auto_code = ISO1_TO_ISO2B[guess["iso1"]]
                    ok, msg = apply_tag(filepath, pos, auto_code)
                    if ok:
                        record_tag(filepath, pos, auto_code, "auto", guess)
                        touched.add(filepath)
                        dets = "/".join(f"{p:.0%}" for _, p in guess["detections"])
                        print("  " + vpad(green("auto"), 9)
                              + vpad(bold(auto_code), VALUE_COL)
                              + dim(f"{guess['words']} words   {dets}"))
                        if msg != "tagged":
                            print(dim(f"     {msg}"))
                        stats["auto"] += 1
                    else:
                        print(red(f"   ✗ auto-tag failed: {msg}"))
                        stats["errors"] += 1
                    print()
                    continue
            if args.prescan:
                # Unattended: the scan is cached, so leave the judgement to a
                # later interactive pass instead of prompting nobody.
                pending = ISO1_TO_ISO2B.get(guess["iso1"]) if guess else None
                row("defer", dim(pending or "-"), dim(auto_reason or "no guess"))
                stats["deferred"] += 1
                print()
                continue
            duration = float(info.get("format", {}).get("duration", 0) or 0)
            try:
                filesize = int(info.get("format", {}).get("size", 0) or 0)
            except (TypeError, ValueError):
                filesize = 0
            if not filesize:
                try:
                    filesize = filepath.stat().st_size
                except OSError:
                    filesize = 0
            while True:  # redisplayed after a deep scan
                guess = guesses.get(pos)
                code, whole = print_card(pos, s, guess, exp, info,
                                         header_langs, duration, filesize,
                                         auto_reason)
                enter_hint = f"Enter{dim('=tag ' + code)}" if code else dim("Enter=n/a")
                keys = ["xxx=code"]
                if not whole:
                    # On a track already transcribed whole, 'f' would redo
                    # identical work and 'd' would sample a subset of what was
                    # scanned. Both still function if typed; they just stop
                    # being advertised as the next thing to try.
                    keys += ["d=deep scan",
                             f"f=full scan ~{full_scan_estimate(duration)}m"]
                keys += ["s=skip", "n=never ask", "q=quit"]
                keys_hint = dim("    ".join(keys))
                prompt = f"  {enter_hint}    {keys_hint} > "
                if whole:
                    print(dim("  whole track already scanned, so there is no"
                              " deeper scan to try"))
                deepen = False
                while True:
                    try:
                        answer = input(prompt).strip()
                    except (EOFError, KeyboardInterrupt):
                        print()
                        answer = "q"
                    low = answer.lower()
                    if low == "q":
                        quit_requested = True
                        break
                    if low == "s":
                        skip_file = True
                        break
                    if low == "d":
                        guesses[pos] = deep_scan(
                            whisper_bin, model, filepath, pos, duration,
                            guess) or guess
                        scan_cache_put(filepath, pos, guesses[pos], args.auto)
                        deepen = True
                        break
                    if low == "f":
                        if duration > FULL_SCAN_WARN_SECONDS:
                            est = full_scan_estimate(duration)
                            sure = input(f"   long file - full scan takes "
                                         f"roughly {est} min. Continue? [y/N] ")
                            if sure.strip().lower() != "y":
                                continue
                        guesses[pos] = full_scan(
                            whisper_bin, model, filepath, pos, duration,
                            guess) or guess
                        scan_cache_put(filepath, pos, guesses[pos], args.auto)
                        deepen = True
                        break
                    if low == "n":
                        record_skip(f"{filepath}\ta{pos}")
                        skips.add(f"{filepath}\ta{pos}")
                        touched.add(filepath)
                        print(dim("   skipped - won't ask again"))
                        stats["skipped"] += 1
                        break
                    chosen = code if answer == "" else low
                    if not chosen:
                        print(red("   no guess to accept") + dim(" - type a 3-letter code, d, f, s, n or q"))
                        continue
                    if not (len(chosen) == 3 and chosen.isalpha()):
                        print(red(f"   '{chosen}' is not a 3-letter code"))
                        continue
                    if chosen not in VALID_ISO2:
                        confirm = input(f"   '{chosen}' not in the known-code list - use anyway? [y/N] ")
                        if confirm.strip().lower() != "y":
                            continue
                    ok, msg = apply_tag(filepath, pos, chosen)
                    if ok:
                        record_tag(filepath, pos, chosen, "manual", guess)
                        touched.add(filepath)
                        print(green(f"   ✓ tagged {chosen}"))
                        if msg != "tagged":
                            print(dim(f"     {msg}"))
                        stats["tagged"] += 1
                    else:
                        print(red(f"   ✗ {msg}"))
                        stats["errors"] += 1
                    break
                if not deepen:
                    break
            if skip_file:
                break
        print()

    stop_event.set()
    kill_scan_children()
    if not args.list:
        save_scan_cache()
        prune_worklist(touched, skips, stats["tagged"] + stats["auto"])
    mins, secs = divmod(int(time.monotonic() - t0), 60)
    summary = (f"Done in {mins}m{secs:02d}s."
               f" Files with untagged audio: {bold(stats['files_with_und'])}"
               f"  tagged: {green(stats['tagged'])}"
               f"  auto: {green(stats['auto'])}"
               f"  skipped: {stats['skipped']}"
               f"  errors: {red(stats['errors']) if stats['errors'] else 0}")
    if stats["deferred"]:
        summary += f"  deferred: {stats['deferred']}"
    print(summary)
    if not args.list:
        entries, hits = scan_cache_stats()
        print(dim(f"Scan cache: {entries} "
                  f"{'entry' if entries == 1 else 'entries'}, "
                  f"{plural(hits, 'hit')} this run - a hit is 25-45s of "
                  f"whisper not spent."))
        if stats["deferred"]:
            print(dim("Those deferred files are cached now, so the interactive "
                      "pass over them costs no scanning."))


if __name__ == "__main__":
    main()
