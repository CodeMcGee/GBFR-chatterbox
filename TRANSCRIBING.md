# How the transcripts are made

The dataset side of the project lives in the `transcribe/` package. It turns
game audio into the per-character JSONs under `data/per-character/`, which
`dev/build_atlas.py` publishes as `data/gbfr-voice-atlas.csv`. Everything here
was arrived at by measurement — the experiments and their numbers are in
[EXPERIMENTS.md](EXPERIMENTS.md).

## Pipeline

```
game install (.bnk / .pck / data.i)
  └─ transcribe.audio.Audio       full-line wav decode (reassembles streamed lines)
      └─ python -m transcribe bake        qwen3-omni: glossary prompt + per-character
      │                                   audio exemplars + per-line context
      └─ python -m transcribe ensemble    qwen3-ASR cross-check, confidence-gated
      │                                   merge, disagreement review queue
      └─ python -m transcribe corrections human-verified overlay, reapplied after every pass
      └─ python -m transcribe eval        ground-truth gate: ships only if green
              └─ dev/build_atlas.py       publish the CSV
```

Both model servers are local vllm containers; launch with
`dev/serve_qwen3omni_awq.sh` (omni) and `dev/serve_qwen3asr.sh` (ASR).

## The rules the pipeline encodes

Each rule traces to a measured failure (E-numbers refer to EXPERIMENTS.md):

- **Per-line context is facts only** — speaker, addressee, line type. Any
  literal string in the prompt gets pasted into low-information clips (E2, E3).
- **Human lines are armored.** Anything a person verified is in
  `transcribe/corrections.json`, applied after every pass, and marked
  `source_model: "human"` so no automated pass touches it again. Two verified
  fixes were silently regressed by rebakes before this existed.
- **Confidence prioritizes, never clears.** Very negative avg-logprob reliably
  flags wrongness; near-zero does not mean right — half the human-caught
  errors scored better than -0.15. Sort review queues by it.
- **Models propose, humans decide.** Every model we measured — local 30B omni
  at two quantizations, dedicated ASR, cloud pro tier — mishears the hard tail
  of short shouted barks and in-world epithets (E9, E10, E11). The review flow
  in the app (flag a line, type the correction) is the oracle; the pipeline's
  job is to surface good candidates cheaply.

## The ground-truth corpus

`transcribe/truth.json` holds every human-verified line (and any known-wrong
readings without a fix yet). It only grows. `python -m transcribe eval` scores
any atlas-shaped directory against it and exits non-zero on regression — run
it before publishing anything. Every new correction should land in three
places at once: `corrections.json`, the atlas JSON, and `truth.json`
(`python -m transcribe corrections` handles the first two).

## Adding a correction

1. Flag the line in the app and type the correct words, or note the wem_id.
2. Add it to `transcribe/corrections.json`.
3. `python -m transcribe corrections && python -m transcribe eval`
4. Rebuild the CSV with `dev/build_atlas.py` when batching up a release.
