# State and queue

What the tool keeps in its state dir (`--state-dir`, default
`$XDG_STATE_HOME/audio-lang-tagger`), how to review and undo tags, the queue
format an external scanner can write, and how parallel scans and runs behave.

## State files

Everything lives in the state dir; none of it is precious except the ledger:

| File | What it is |
|---|---|
| `lang_tagger_tags.tsv` | append-only ledger of every applied tag |
| `lang_tagger_scans.json` | whisper results by path + mtime + track |
| `lang_tagger_skips.txt` | tracks answered with `n` |
| `untagged_audio.json` | the candidate queue |
| `lang_tagger.{state,instance}.lock` | concurrency guards |

## The ledger and undo

A row is `timestamp, path, track, und->code, mode, probability, chars`, where
mode is `auto`, `manual`, `bulk` or `undo`.

`--ledger [N]` prints the tail of it, which is how you check what `--auto` did
while you were not watching. `--undo N` reverts the last N tags still in force,
after one confirmation. An undo appends its own corrective row rather than
editing history, so a track reverted and later re-tagged counts once, at its
latest value. A track whose header no longer says what the ledger recorded is
left alone and reported: something other than this tool changed it. For a
single file by hand, the row gives `mkvpropedit` everything it needs:

```bash
mkvpropedit "FILE" --edit track:a1 --set language=und
```

## Queue format

`--queue-file` points at JSON an external scanner writes. Only `paths` is
required:

```json
{"date": "2026-08-09", "files": 1500, "tracks": 1503, "paths": ["/srv/media/..."]}
```

`date` labels the run on screen, `files`/`tracks` seed the workload counter
before the background recount lands. Without a queue, the first run falls back
to a full sweep and writes one on the way out. Finished files are pruned on
exit, so the counter starts from a true number.

The natural writer is a health pass that already probes every stream: the queue
costs it nothing extra, and reading `lang_tagger_skips.txt` back lets it leave
out the tracks answered with `n`. Both tools must point at the same state dir
for that - the one setting they have to agree on.

## Concurrency

`--jobs` scans run at once, at `cpu_count / jobs` whisper threads each. On a
6-core box 2 jobs beat 1/3/6 and was still only 1.28x one job: whisper already
parallelises internally and the work is memory-bandwidth bound, so raising it
buys nothing. On a 24-core hybrid CPU 4 jobs beat 2 by 8% in aggregate but
slowed each track, and 1 job at 24 threads ran at a fifth of 2 jobs' rate.

Two runs at once are survivable but not recommended: writes to the shared JSON
files are read-modify-write under an `flock` so they merge rather than clobber,
and a second instance is announced (a second `--prescan` is refused). What is
not safe is both runs reaching the same file.
