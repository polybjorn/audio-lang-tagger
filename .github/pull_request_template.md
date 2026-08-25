## What this changes

<!-- What the change does functionally, and why if it is not obvious from the diff. -->

## Test plan

<!--
How you checked it. `python3 -m unittest discover -s tests` covers the parser and
the formatters; anything touching detection, tagging or undo needs a real run
against MKV files, so say what you ran it on and paste the relevant ledger rows.
-->

- [ ] `python3 -m unittest discover -s tests` passes
- [ ] Ran against real media, if the change can touch track headers
