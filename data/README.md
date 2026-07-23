# Atlas data

- `per-character/plXXXX.json` — source of truth. One file per character; the
  app and the transcription pipeline read and write these.
- `gbfr-voice-atlas.csv` — the same data flattened to one row per line, for
  browsing and spreadsheets. Derived; rebuild with
  `python dev/build_atlas.py data/per-character data/gbfr-voice-atlas`.

## Per-character format

```
{
 "pl_id": "pl2200",
 "character": "Seofon",
 "banks": [...],
 "lines": {
  "<english wem id>": {
   "label": "PL2200_vo_...",        // language-independent join key
   "transcript": "...",             // English release line
   "confidence": -0.17,             // recognizer avg logprob; near 0 = confident
   "source_model": "human",         // who produced the transcript; human = ear-verified
   "corrected": "consensus",        // optional provenance note
   "bank": "...", "duration_s": ..., "peak": ..., ...   // audio provenance
   "jp": {                          // the Japanese twin, when one exists
    "wem_id": "<japanese wem id>",
    "text": "一気にいこう！",          // what the JP track actually says
    "literal": "Let's go all at once!",  // direct English translation of it
    "confidence": -0.2,
    "duration_s": 0.907,
    "alt_takes": [{...}]            // rare: extra JP recordings of the same
                                    // script line, same fields minus duration
   }
  }
 }
}
```

Lines are keyed by English wem id; the JP wem ids live inside `jp` because
wem ids differ per language while the script line is one entity. The EN and
JP scripts are separate localizations — `jp.literal` preserves what the
Japanese actually says.

Edit per-character files (or better, add the fix to
`transcribe/corrections.json` and run `python -m transcribe corrections`),
never the CSV.
