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
