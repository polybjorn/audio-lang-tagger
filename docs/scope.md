# Scope

What the tool covers, why it asks rather than trusting a confidence threshold,
and how it sits next to other tools that touch the same tracks.

## Formats

Matroska only: the write is `mkvpropedit --set language=`, an in-place MKV
track-header edit. MP4/MOV keep their language in the `mdhd` box and would need
a second backend, which does not exist yet. Bare audio files are out of scope on
purpose - an mp3 has no per-track language header to be wrong about, only a tag
field, so the problem this tool solves does not arise.

## Why it asks

A personal tool, published because the tools that already do this tag
unattended on a confidence threshold, and the two errors are not the same size.
Being asked costs a second; a wrong language header is silent, persists, and is
acted on later by whatever reads it. A threshold would still be defensible if
confidence ranked the safe cases above the risky ones, and the
[design notes](design-notes.md#detection-is-wrong-in-a-specific-way) measure
that it does not: p=0.86 on a 219-word `La la la` loop, p=0.42 on a track
holding 99 clean words. They fail in opposite directions, so no cutoff orders
them correctly, and a larger model shifts both without fixing the ordering.

## Other tools

[ULDAS][uldas] covers more ground (subtitles, remuxing, a web UI, GPU) and is
the more capable tool if you want a library handled without being asked. Its
`dry_run` prints the same decisions without writing them, but there is no
approve step between that and a full unattended run. This one takes the other
side of that trade and assumes you would rather confirm. Do not point both at
one library: two tools writing track headers, one ledger between them.

If you also run a track stripper, this complements it rather than competing:
[radarr-striptracks][striptracks] and its like have to keep every `und` track,
because removing one blind risks a file with no audio left. Identify them first
and that pass can decide.

[uldas]: https://github.com/netplexflix/MKV-Undefined-Audio-Language-Detector
[striptracks]: https://github.com/TheCaptain989/radarr-striptracks
