#!/usr/bin/env python3
"""Regenerate docs/ui.svg, the README's screenshot of the interactive card.

Drives the real TaggerApp headless through textual's run_test and exports
textual's own SVG, so the image is the actual UI rather than a mock-up. The
library content is fabricated: no real path or title from anyone's library
ends up in the repo.

Run from the repo root after changing the card layout:

    pip install textual && python3 docs/screenshot.py
"""
import argparse
import asyncio
import importlib.util
import os
import queue
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SHOW = "/srv/media/series/Harbour Watch"
EPISODE = f"{SHOW}/Season 1/Harbour Watch - S01E03.mkv"

# 100x16 is the smallest frame the full card, header and key hints fit in
# without the app's fixed card viewport leaving dead rows under "heard".
SIZE = (100, 16)


def load_tool(state_dir):
    os.environ["AUDIO_LANG_TAGGER_STATE_DIR"] = state_dir
    spec = importlib.util.spec_from_file_location(
        "alt", REPO / "audio-lang-tagger.py")
    alt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(alt)
    # USE_COLOR is decided at import against a real stdout; there is none
    # here, and without it every card row exports as flat white.
    alt.USE_COLOR = True
    alt.DISPLAY_ROOT = Path("/srv/media")
    return alt


def fake_work():
    """One card's worth of ffprobe/whisper/arr data, shaped like the real
    thing: a 52-minute eac3 track that transcribed cleanly as English."""
    stream = {"codec_name": "eac3", "channels": 6, "bit_rate": "640000",
              "codec_type": "audio", "tags": {}}
    info = {"format": {"duration": "3138.0", "size": str(2_576_980_378)},
            "streams": [stream]}
    guess = {"iso1": "en", "prob": 0.97, "detections": [("en", 0.97)],
             "chars": 912, "words": 184, "unique": 121, "full": False,
             "snippet": "right then let's see what the tide brought in"}
    work_q = queue.Queue()
    work_q.put(("progress", "scanning  Harbour Watch - S01E04.mkv"))
    for path in (EPISODE, f"{SHOW}/Season 1/Harbour Watch - S01E04.mkv"):
        work_q.put(("file", (Path(path), info, [(1, stream)], {},
                             {1: dict(guess)})))
    return work_q


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(REPO / "docs" / "ui.svg"))
    args = ap.parse_args()

    alt = load_tool(tempfile.mkdtemp(prefix="alt-shot-"))
    # Mid-run counts, so the header shows a partly filled progress bar and a
    # warm scan cache instead of a run that just started.
    stats = {"files_with_und": 96, "tagged": 61, "auto": 28, "skipped": 6,
             "errors": 0, "deferred": 0}
    alt._scan_cache_hits = 37
    tui_args = argparse.Namespace(auto=False, plain=False, list=False,
                                  full=False, prescan=False, bulk=None,
                                  jobs=2, no_arr=False)
    app = alt.build_tui(fake_work(), threading.Event(), tui_args, set(),
                        stats, set(), {SHOW: ("eng", "Sonarr", 2019, ["Drama"])},
                        "whisper-cli", "ggml-base.bin", 340, False)

    async def shoot():
        async with app.run_test(size=SIZE) as pilot:
            app.title = "audio-lang-tagger"
            await pilot.pause()
            app._remember(alt.green("tag eng"), "Harbour Watch - S01E02.mkv")
            await asyncio.sleep(0.5)
            await pilot.pause()
            app.save_screenshot(args.out)

    asyncio.run(shoot())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
