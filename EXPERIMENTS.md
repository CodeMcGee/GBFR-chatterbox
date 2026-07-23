# Transcription experiment log

Living log of the precision-transcription work: what was run, what it showed,
what's next. Goal: combine models/contexts so every line gets the most precise
transcription available. Companion plan: [dev/PERSONAS.md](dev/PERSONAS.md).

All runs: Seofon (pl2200, 911 lines), atlas baseline `data/per-character/pl2200.json`.

## Platform

All experiments ran on one workstation:

| | |
|---|---|
| CPU | AMD Ryzen Threadripper 9960X (24c/48t) |
| RAM | 128 GB |
| GPUs | 2× NVIDIA GeForce RTX 5090, 32 GB each (consumer Blackwell, SM 12.0) |
| Driver | 610.43.02 |
| OS | Gentoo Linux, kernel 6.18 |
| Serving | Docker 28.4, vllm OpenAI-compatible images (v0.16 `qwen3_5-cu130` line for the bakes, `v0.19.0-cu130` for the NVFP4 work) |
| Client | Python 3.13, stdlib urllib only |
| Cloud access | LiteLLM gateway on a separate LAN server: metered, monitored access to public models via OpenRouter (the Gemini/Claude runs in E10-E12) |

One GPU is dedicated to a permanent model for continuous inference; the other
hosts the experimental models. The serve scripts pin gpu-memory-utilization
(0.72 for the AWQ omni, ≤0.74 for NVFP4) rather than assume a clean card. The
5090s' FP4 tensor cores are not yet exploited by vllm's kernels for this MoE —
measured in E9/benchmarks, AWQ-int4 via Marlin outruns NVFP4 by ~40% on decode.

## Setups

| name | model | server | harness |
|---|---|---|---|
| omni | Qwen3-Omni-30B-A3B-Instruct **AWQ-4bit** | vllm `qwen3_5-cu130-audio` image, port 8210, GPU0 | `transcribe/bake.py` + `omni.py` (system prompt + glossary + per-char audio exemplars + per-line ctx) |
| asr | Qwen3-ASR-1.7B | vllm `qwen3_5-cu130` image, port 8211, GPU0 | `transcribe/asr.py` (context string in system turn, audio-only user turn, `<asr_text>` parsing) |

Server notes: vllm `nightly` image crashes on omni (vision-encoder regression:
`Qwen2_5_VisionAttention.forward() missing 'sequence_lengths'`); `qwen3_5-cu130`
lacks audio deps — `qwen3_5-cu130-audio` is that image with librosa/soundfile
committed in.

## Experiments

### E1 — persona A/B, one clip (2026-07-22)

`test_persona.py` on the motivating clip 441369863 (`Hanguard!`, conf -0.669),
persona blurb the only variable.

| ctx | result |
|---|---|
| baseline | `Hunh!` (-1.004) |
| + persona quoting "En garde!" | **`En garde!` (-0.112)** |

**Pass** — persona flips the target clip. Triggered E2.

### E2 — full pass, persona with quoted catchphrase → `build/atlas-omni-persona/`

238/911 transcripts changed. Target fixed, but **8 confidently-correct lines
were overwritten with the quoted catchphrase** ("En garde!" appeared in 10 lines
vs 2 legit), including ally calls whose labels name the ally
(`Rackam!`/`Vane!`/`Io!` → `En garde!`). Register rewrites too ("Yep! That
was..." → "Indeed, ..."). Avg conf worsened (-0.119 → -0.169).

**Fail** — quoting a phrase in the persona injects it into low-information clips.

### E3 — full pass, neutral persona (no hints) → `build/atlas-omni-persona2/`

Blurb rewritten to accurate bio only (flamboyant, seemingly confident but
humble, chivalrous, nicknamed "Star Sword Sovereign" — nickname verified via
official Relink account). 166/911 changed.

- All 8 E2 poisonings reverted. Zero "En garde" injections.
- **Target regressed**: `Hanguard!` stayed `Hanguard!` (-0.794).
- **New injection**: 7 `ATK_god_*` barks became `Star Sword Sovereign!`
  (-0.41..-0.54) — the quoted *nickname* is now the vector.
- One degenerate loop: 299326151 (`Seofon: Seia!`) → `Seofon: ` ×32 at
  conf -0.084. greedy decode (temp 0, no repetition penalty, max_tokens 128)
  + AWQ-4bit quant noise on a near-content-free clip; conf is useless there.

**Conclusion for omni personas: the persona only fixes a bark when it contains
the exact phrase, and containing the exact phrase is what poisons other clips.**
A neutral persona is roughly harmless and roughly useless. Any quoted literal
string in the prompt is an injection vector.

### E4 — full pass, Qwen3-ASR-1.7B, prose ctx → `build/atlas-asr/`

Same per-line prose ctx (speaker + line type) as system message.

- Worded lines (704): 498 exact-agree with atlas, 151 differ, 55 empty.
  Differences are mostly register laundering (`ya`→`you`, `movin'`→`moving`,
  `!`→`.`), a few genuine wins (leading interjections), a few confident
  errors: `Time for a break.`→`Time for a brick.` at conf **-0.001**,
  `sky realm`→`Skyrim`, `Rolan`→`Roland`. Its confidence does not flag its
  own errors.
- Barks/grunts (207): **90% empty** — declines non-speech, including the
  en-garde target.

### E5 — ASR context probes on the target + 11 injection-prone clips

| context | target 441369863 |
|---|---|
| prose ctx (speaker/line-type) | *empty* — prose suppresses short barks |
| prose + vocabulary appended | *empty* |
| hotwords only: `En garde. Cien Mil Espadas. Star Sword Sovereign.` | **`En garde.` (-0.04)** |
| empty | *empty* |

Same hotword ctx on the 11 clips omni poisoned: **zero injections**. Worded
lines stay correct; name-shouts and grunt-adjacent clips return empty rather
than wrong; the E3 loop clip returns empty. ASR biasing only surfaces a listed
phrase when the audio supports it — the safe version of what E1/E2 tried to do
with prompt-priming.

### E7 — ensemble merge, Seofon (`transcribe/ensemble.py` → `build/atlas-ensemble/`)

ASR-with-hotwords over the atlas, precision-first merge: keep atlas on ASR
empty/agreement; on disagreement ASR wins only when atlas conf ≤ -0.3, else
review queue; human lines untouchable. Three runs:

- Merge layer works: 750 kept, 11 low-conf replacements, 150-line review
  queue, zero corpus regressions, gate blocked every ASR fabrication
  ("Skyrim", "brick"-class). Two guards proved necessary: reject non-ASCII
  output (a hum came back as CJK) and reject context echo (one line came back
  as the hotword list itself).
- **Bark rescue is brittle beyond engineering**: the En-garde clip returns
  "En garde." or "" depending on phrase *order and count* in the context —
  a 4-phrase window works, the same phrases in a different order don't, a
  2-phrase subset doesn't. 1.7B ASR biasing can't be relied on for barks;
  `Hanguard!` survives all three runs.
- Queue gem: `Fen-drak!` / ASR "Fiendrock." is almost certainly **Feendrache**
  (Vane & Lancelot's kingdom) — glossary gap.

### E8 — iterative refinement on omni (`dev/refine.py`, `dev/test_refine_truth.py`)

Second omni pass over low-conf lines with first-pass drafts in the context
("drafts may be wrong - trust the audio"). Gauntlet: all 30 ground-truth
lines, wrong-draft where history records one (12), correct-draft otherwise (18).

| condition | wrong drafts recovered | correct kept |
|---|---|---|
| drafts in context | **0/12 — all 12 echoed verbatim** | 18/18 |
| no drafts (control) | 4/12 | 11/18 |

Drafts anchor completely: the model copies them instead of listening, and
echoing collapses confidence to ~0 — so refinement also *destroys the error
signal* (a wrong "Fire rose!" re-emerges at -0.03, indistinguishable from
verified). **In-context iterative refinement is disproven on this model.**
The control confirms re-listening without drafts is just the familiar first
pass: it re-breaks 7/18 lines humans had to fix.

### E9 — forced-choice rescoring + quant comparison (`transcribe/rescore.py`)

Score candidate transcripts by likelihood given the audio (vllm
`prompt_logprobs` on a forced assistant answer; nothing generated → nothing
anchors). Gauntlet: truth vs historical error on the 12 corrected lines.

| build | truth preferred (avg logprob) |
|---|---|
| AWQ-4bit | 4/12 |
| NVFP4 | 4/12 |

**Quant exonerated.** Both builds prefer the error on ~2/3 of human-corrected
lines ("Hanguard!", "Lash me whiskers!", "Mercy unto these skies!") and both
win the same easy ones (Rackam, goner, Briar Rose). The 30B omni's audio
front-end genuinely mishears these clips; no prompt/temperature/precision
permutation fixes an ear. Rescoring itself is a sound mechanism — deterministic,
anchor-free — it just needs a better-eared model to grade with.

NVFP4 boot recipe (the checkpoint is broken out-of-the-box): graft
`tokenizer_config.json` (has `extra_special_tokens`) + `added_tokens.json`,
`merges.txt`, `special_tokens_map.json`, `video_preprocessor_config.json` from
the AWQ snapshot; append prefix-agnostic regexes to `quantization_config.ignore`
(vllm names modules `language_model.*`, the checkpoint ignores `thinker.*`);
run on `vllm/vllm-openai:v0.19-nvfp4-audio` (local commit = v0.19.0-cu130 +
librosa/soundfile/av); util ≤0.74 beside the GPU0 residents or the audio
encoder OOMs at request time. Config backups: `config.json.orig`,
`tokenizer_config.json.orig` in the HF snapshot dir.

### E10 — blind cloud second opinion (`dev/cloud_check.py`)

The 12 known-hard clips through Gemini 2.5 Flash (LAN litellm `chat-fast` →
OpenRouter), blind: neutral context, no drafts, no catchphrases.

**2/12 exact** (`Rackam!`, `pincer me timbers`) **+1 phonetic**: it heard the
En-garde bark as "On guard!" — the right sounds, unlike every local attempt.
The rest are its own hallucinations ("Let me whisk her", "Master, look!",
"Rosetta, rose!"), several worse than the local model's. No blind model of any
size gets "Briar Rose", "Mister Fix It", or "Master Primarch" — those took
audio + lore + a human, which is exactly what the corpus records.

**Conclusion across E1-E10: these clips sit beyond every measured model ear,
local or cloud.** Models are proposal generators; the corpus + human ear is
the only oracle. The pipeline should optimize for surfacing good proposals
cheaply (disagreement review queues, confidence sorting), not for automating
the final call.

### E12 — text-only lore repair

Can a model spot garbled proper nouns from the transcript text alone, no
audio? Cases: 11 known garbles (Eternals squat-count, Alley-oop, Feendrache,
Briar Rose...) + 4 correct-line controls.

| model, prompt | recovered | false positives |
|---|---|---|
| local omni 30B, bare | 0/11 | 0/4 |
| local Qwen3.6-35B (GPU1 chat model), ctx + glossary | 2/11 | 1/4 |
| Claude Haiku 4.5, ctx + glossary | 3/11 | 1/4 (un-punned "pincer me timbers") |
| Claude Fable 5 (blind subagent), ctx + glossary | 5/11 | **0/4** |
| Gemini 3.1 Pro, bare | 6/11 | 1/4 ("Miasma unto these skies") |
| Gemini 3.1 Pro + per-line ctx + glossary | 6/11 | **0/4** |

The local 30B has no usable lore memory (invented "Unbound", "Rose Garden").
Gemini catches spelling-level garbles (Primarch, Rackam, goner, Feendrache,
Alley-oop, the Star-Sword sign-off) and the per-line context eliminated its
one false positive. Fable was the only model to recover the Eternals
squat-count (Uno, Tweyen, Threo, Feower, Fif, Seox) and made zero false
corrections, though it saw all 15 cases in one prompt where the others were
queried per-case. Audio-only errors ("Happening!" for "Up and at em.")
stay unreachable by design. Complementary to the ear passes: a text sweep is
~0.1c/line with no audio upload — viable as a cheap atlas-wide *flagger*
whose non-OK outputs feed the review queue, never auto-applied.

### E13 — JP audio probe against the verified English (`dev/jp_probe.py`)

For all 163 truth-corpus lines with a Japanese twin (label join): omni
translates the JP audio directly to English, and separately transcribes the
Japanese then text-translates it. The verified English anchors plausibility
for a maintainer who cannot read Japanese.

- **42% of lines (68/163) carry genuinely different meaning in the JP
  script** (judged semantically against the verified English) - the case for
  the `*_literal` tracks in one number. Highlights: "En garde!" is どうだ
  ("How's that?"), "You're a goner!" is 無駄無駄 ("Useless, useless!"),
  Yodarha's pirate line is a completely different sentence, and Fediel calls
  a Human ally "Number 9's daughter" in JP where EN says "Human lass!".
- Part of the divergence is JP-side mishearing, same weakness as the EN ear:
  proper nouns without a glossary (Löwenbein heard as "level up", Seofon's JP
  name mangled). A katakana name/skill glossary is a prerequisite for the
  jp_real bake.
- **Direct one-step translation beats transcribe-then-translate**: identical
  when both work, but two-step fails on katakana wordplay the direct path
  handles (カニゲット came out "Ganigetto"; direct said "Got the crab!").
- 162/163 JP transcripts came back as actual Japanese text - the omni JP ear
  works; quality gating it is the open problem.

## Findings so far

1. Omni prompt-priming (persona/glossary quotes) is unsafe: literal strings
   leak into low-information clips. Both bakes confirm it.
2. ASR context biasing is safe for the same job, but ASR abstains on
   grunts/barks/name-shouts (~25% of clips) and launders colloquial register.
3. ASR ≠ self-policing: rare confidently-wrong words mean disagreement with
   omni, not ASR confidence, is the error signal.
4. Prose in the ASR context suppresses output; its context must be a bare
   vocabulary/phrase list.
5. Omni decode needs a repetition guard (penalty or lower max_tokens) —
   E3's loop was greedy degeneration, likely amplified by 4-bit quant.

## Ground-truth corpus (E6, ongoing)

`transcribe/truth.json` + `transcribe/evaluate.py`: every human-verified line becomes a
permanent accuracy + confidence-calibration test. Sources merged: the applied
corrections, the En-garde clip, the tool's flag store
(`~/.config/chatterbox/flags.json`), and corrections recovered from old
session transcripts (two had never been applied: 780614151 "pincer me
timbers", 355245523 "Bless me whiskers" — the latter had been user-confirmed
and then *regressed* by a later omni pass, which is exactly why
`apply_corrections.py` now carries all 22 and every verified line is marked
`source_model: "human"`). Baseline:

| source | verified | conf separates? |
|---|---|---|
| atlas (`data/per-character`) | 22/23 (En-garde is the miss) | yes (-0.13 right vs -0.67 wrong) |

History-dig outcome (2026-07-22): every past correction was recovered — the
tool flags store from the Windows test box (8 ally-name fixes) is fully in corrections.json, "blessing
unto these skies" was matched to Fediel 845626067, both flagged Vane clips
(879962080 SBA, 825022410 "You screwed up, Vane.") are locked in, and the
NP0300 slot is already "Rolan!" across all characters. Still open, tracked in
truth.json: 36524872 (Vane charge_name_3, known-wrong "Lohenwolf!" — correct
text needs an ear) and 35162464 ("Rolan, fix it!", conf -0.49, only oddball
left in the NP0300 slot).

Grow it with every new piece of feedback (tool flags, chat corrections);
`python -m transcribe eval <dirs...>` scores any bake before it ships.

## Next

- [ ] Ensemble rule to draft and test: per line, prefer ASR-with-hotwords when
  it returns text; keep omni/atlas text when ASR is empty (barks, grunts,
  name-shouts); route omni-vs-ASR worded disagreements to a review queue
  (consensus.py pattern). Decide register policy (keep atlas colloquial
  spellings when ASR only flattens style).
- [ ] Build per-character hotword lists (glossary skills/SBA + verified
  catchphrases). Validate each addition like E5: target fixed AND no injection.
- [ ] Repetition guard for omni requests; retest 299326151. Optionally compare
  NVFP4 vs AWQ on the loop clip to size the quant effect.
- [ ] Decide fate of neutral personas (E3): no measured benefit; drop unless a
  character shows one.
- [ ] Scale the winning recipe to remaining 28 characters; then Stage 3 rebake
  (PERSONAS.md) if adopted.
