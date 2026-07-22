# Atlas data

- `per-character/plXXXX.json` — source of truth. One file per character; the
  app and the transcription pipeline read and write these.
- `gbfr-voice-atlas.csv` — the same data flattened to one row per line, for
  browsing and spreadsheets. Derived; rebuild with
  `python dev/build_atlas.py data/per-character data/gbfr-voice-atlas`.

Edit per-character files (or better, add the fix to
`transcribe/corrections.json` and run `python -m transcribe corrections`),
never the CSV.
