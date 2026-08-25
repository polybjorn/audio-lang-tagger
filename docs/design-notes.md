# Design notes

Why the tool asks before it tags, why the auto gates are shaped the way they
are, and what was measured to settle each one. None of this is needed to run
the tool - the [README](../README.md) is the whole operating manual. This is
for anyone deciding whether to trust `--auto`, or changing a threshold and
wanting to know what it was protecting against.

The constants themselves live at the top of `audio-lang-tagger.py`, and the
module docstring is the authority for their current values. Numbers here are
the evidence behind them.

## Detection is wrong in a specific way

Whisper only ever detects language from 30 seconds of audio, and its language
head fires confidently on music. That is not random error, it is a systematic
one, and it is what makes blind tagging unusable:

- A sung-through 1956 short "transcribes" a 219-word `La la la` loop and the
  detector reports it at p=0.86. High confidence, no language content.
- A 1940 sparse-dialogue cartoon opens on a musical title card. It detects at
  p=0.42 while transcribing 99 clean words later in the same track. Low
  confidence, real language content.

A single confidence number cannot separate those two, because they fail in
opposite directions. So the tool stops treating detection as one question.

## Two questions, two sources of evidence

*Are there words here* is answered from the transcript: character count, word
count, distinct-word ratio. *Which language* is answered by the detector. A
detection with no transcript behind it is never offered as the Enter default,
however confident it is.

Music hallucinations are degenerate in a way real speech is not - repetition
loops, or one stretched word - and they stay degenerate however long they run.
That is why the coherence floor grades unique words as a *ratio* on short
transcripts and switches to a distinct-word count once a transcript is long
enough for the ratio to sag.

Distinct-word count alone was measured as a simpler alternative and rejected.
The sparse-dialogue short has 50 distinct words; a sung-through short that must
not pass has 52. No threshold separates them, so the ratio has to stay.

## Asking the detector twice

On a track short enough to transcribe whole, the detector's reading comes from
the first 30 seconds, which on a cartoon is the title score. That put the gate
at odds with its own evidence: of 8 known-dialogue animated shorts that
transcribed cleanly end to end, only 4 cleared 0.90 on their opening.

So after a whole-track transcription the detector is asked a second time about
the 30-second window that actually held the most words, and the gate grades the
better of the two readings. All 8 shorts then cleared 0.90, while the 1956
sung-through case (43 distinct words inside 425) stayed at 0.81 and is rejected
by the coherence floor as well.

The card uses the same pair of readings once more, and this part never touches
the auto gates: a degenerate transcript whose better reading still clears 0.90
is presented as that language rather than `zxx`, because music does not reach
that bar. It is sparse real speech buried in repetition padding, and the human
decides.

## Why only Sonarr and Radarr corroborate

Corroboration hinges on one fact: the title's *original language*. Whisper
hears "this is Italian"; the arr knows "this film was made in Italian".
Unattended tagging requires those two to have been arrived at independently.

TMDB holds that fact and the arrs pass it through their API. Jellyfin scrapes
the same databases but does not store original language, and the Kodi NFO
standard has no field for it either. Neither can stand in. A corroboration
source that supplied only year and genres would look like a safety check
without being one, which is worse than having none.

This is also why a failed arr lookup degrades to "no corroboration" rather than
to an error: the consequence is simply that fewer tracks pass the gates and
more reach the human.

## What the other gates are protecting against

- **Year >= 1940** is a proxy for sparse dialogue, not for age as such. It uses
  the episode's own air year where Sonarr knows it rather than the series year,
  because an anthology's start year blocks its whole run and discriminates
  nothing.
- **Genre not music or concert** removes the content whose failure mode is the
  one above.
- **A single non-commentary audio track** avoids the case where the detection
  is right about *a* track and wrong about *this* one.
- **Outside the `no`/`nn`/`da`/`sv` cluster** is an admission: whisper confuses
  those four often enough that agreement between it and the arr is not
  independent evidence.
- **`zxx` is checked more suspiciously than any language code**, in `--bulk`
  and on the card alike, because it claims absence. Twenty distinct words
  anywhere in the track is enough to hand the file back to the human, where
  claiming one language over another asks for a full coherent transcript first.

## Measured numbers

All on a 6-core Alder Lake CPU with the `base` model, which is what every
threshold was calibrated against. A larger model reads differently and the
gates should be re-checked before it is trusted with `--auto`.

- Transcription runs 10.5-15x realtime end to end, including the ffmpeg
  extract. `--realtime-factor` defaults to 12, the middle of that range, and it
  decides which tracks are short enough to scan whole and what the `f` key
  estimates. Nothing else reads it.
- The scan cache turns a 34s cold scan into 1s warm, and the first card
  arrives in 0.5s instead of 34s. Cache keys are path plus mtime plus track, so
  a retagged or replaced file can never be served a stale entry.
- `--jobs 2` was the best of 1, 2, 3 and 6, and it is only 1.28x one job.
  Whisper already parallelises internally and the work is memory-bandwidth
  bound, so raising it further buys nothing.
