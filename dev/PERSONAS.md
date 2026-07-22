# Character personas in the transcription prompt

> **Superseded.** Stages 1-3 were run and measured: personas fix a bark only
> when they quote the exact phrase, and quoting the exact phrase poisons other
> clips (EXPERIMENTS.md E1-E3). Personas are retired from the pipeline. Stage 4
> (promotion to a first-class package) happened as `transcribe/`; see
> TRANSCRIBING.md. Kept as the plan of record.

Give the transcriber a one-line description of who is speaking, so it reads
ambiguous audio in that character's register. Motivating failure: Seofon's
en-garde bark (`pl2200` wem `441369863`) transcribes as **"Hanguard!"**
(conf -0.669) — a swordsman's "En garde!" heard by a model with no idea he's a
fencer.

## Where it goes

`dev/retranscribe.py::build_ctx(pl, label)` builds the per-line Context string
(speaker, addressee, line type). A persona is per-*character*, so it prepends
one sentence keyed by `pl` id, from a `PERSONA` map. The line is the only new
input; exemplars, glossary, and everything else stay fixed.

Persona wording is speech-focused (diction/register), not biography — that is
what biases the reading. Seofon:

> Seofon is a flamboyant, supremely confident master swordsman and leader of the
> Eternals, who fights and speaks like a chivalrous duelist, using fencing calls
> such as "En garde!".

## Risk

More context can also *induce* hallucination — the same glossary that should
supply "En garde!" is what turned grunts into "Sword of Lumiel!". So personas
are validated before rollout, never assumed to help.

## Stages

**Stage 1 — prove it on one clip.** `dev/test_persona.py`: A/B the en-garde
clip, baseline ctx vs persona-prepended ctx, exemplars held fixed so the persona
is the only variable. Pass = "Hanguard!" → "En garde!" (or clearly closer) with
no worse confidence. Fail = leave it; personas don't help. *Requires omni up on
a free GPU (GPU0), so the game must be closed.*

**Stage 2 — roll out (only if Stage 1 passes).** Write a short persona for each
of the 29 characters, wire the `PERSONA` map into `build_ctx`, spot-check a
handful of characters' known-hard lines the same A/B way before committing.

Persona drafts come from the model's own lore knowledge, which is unverified
and thin for minor characters — and a wrong persona is exactly the
hallucination-inducing context described above. So before a blurb enters the
`PERSONA` map, verify it against the character's fan-wiki page
(granbluefantasy.wiki.gallery): personality/speech register match, and any
quoted catchphrase actually attested. One fetch per character; fix or drop
blurbs the wiki contradicts.

**Stage 3 — rebake English.** Re-run `dev/retranscribe.py` over all characters
with personas live, then `build_atlas.py` to republish. This refreshes `en_real`
— the anchor the JP subtitle tracks ([[gbfr-subtitle-overlay]],
`MULTILANG-SUBTITLES.md`) join against — so rebuild `build_subtitles.py`
afterward.

## Stage 4 — promote transcription to first-class

The transcription automation graduates from loose `dev/` scripts to a
first-class citizen of the project, alongside the server. Match the existing
layout and conventions:

- A proper package next to `chatterbox/` (e.g. `transcribe/`) — modules, not
  scripts: the retranscribe pipeline, ctx/persona building, exemplar handling,
  consensus/corrections, atlas + subtitle baking.
- Data that drives it (`exemplars.json`, `glossary.json`, `corrections.json`,
  the `PERSONA` map) becomes tracked package data, like
  `chatterbox/characters.json`.
- Entry points follow the `serve.py` pattern (thin root-level runner or
  `python -m`), not `dev/foo.py`.
- Tests in `tests/` per the existing pytest convention.
- `dev/` keeps only true scratch (probe clips, smoke experiments).

Do this after Stage 3 — promote the pipeline that actually shipped the bake,
not the one still being A/B'd.

## Later (separate, deferred)

Japanese-side work — the JP `jp_real` bake and the `*_literal` machine
translations — needs a translation model choice and a way to validate Japanese
output the maintainer cannot read. Discussed when we reach it, not part of this
plan.
