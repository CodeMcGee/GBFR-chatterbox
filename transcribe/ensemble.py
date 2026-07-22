"""Ensemble pass: Qwen3-ASR with per-character hotwords over the omni-baked
atlas. Precision-first merge rule (measured in EXPERIMENTS E7):

- ASR empty (barks/grunts/name-shouts) -> keep atlas text.
- ASR agrees modulo case/punct        -> keep atlas text (preserves register).
- Disagreement: atlas confidence <= gate -> take ASR (atlas was likely wrong);
  else keep atlas and emit to the review queue.
- Human-verified lines are never touched.

Guards: non-ASCII output rejected (hums come back as CJK), context echo
rejected (>=3 hotword phrases in the output means the model read the list
back). Bark rescue via lean-context retries exists but is brittle (E7) -
treat the review queue, not the rescue, as the product.
"""
import argparse
import json
import pathlib
import tempfile

from transcribe import ATLAS_DIR, NAMES, ROOT, find_game, norm, resolve_pl
from transcribe.asr import asr, hotwords
from transcribe.audio import Audio
from transcribe.context import build_ctx


def main(argv=None):
    """CLI: run the gated ASR merge for one character, print the review queue."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8211/v1")
    ap.add_argument("--model", default="qwen3-asr")
    ap.add_argument("--character", "--pl", dest="character", default="Seofon",
                    help="character name (or pl id)")
    ap.add_argument("--out", default="build/atlas-ensemble")
    ap.add_argument("--gate", type=float, default=-0.3)
    ap.add_argument("--game")
    args = ap.parse_args(argv)

    audio = Audio(find_game(args.game))
    pl = resolve_pl(args.character)
    doc = json.loads((ATLAS_DIR / f"{pl}.json").read_text())
    ctx = hotwords(pl)
    normed_phrases = [norm(phrase) for phrase in ctx.split(". ") if phrase]

    def echo(text):
        """True when the model read the hotword list back instead of listening."""
        normed_text = norm(text)
        return sum(1 for phrase in normed_phrases if phrase in normed_text) >= 3

    review, replaced, kept = [], 0, 0
    with tempfile.TemporaryDirectory() as workdir:
        wav = pathlib.Path(workdir) / "target.wav"
        for wem_id, line in doc["lines"].items():
            if not line.get("bank"):
                continue
            try:
                audio.wav(line["bank"], wem_id, wav)
                heard, heard_conf = asr(args.base, args.model, wav, ctx)
                if heard and (not heard.isascii() or echo(heard)):
                    heard = ""
            except Exception as err:
                print(f"  {wem_id}: {type(err).__name__}: {err}", flush=True)
                continue
            atlas_text = line.get("transcript") or ""
            if not heard or norm(heard) == norm(atlas_text) or line.get("source_model") == "human":
                kept += 1
                continue
            action = "TOOK ASR" if (line.get("confidence") or 0) <= args.gate else "kept atlas"
            review.append((wem_id, line.get("label", ""), atlas_text,
                           line.get("confidence"), heard, heard_conf, action))
            if action == "TOOK ASR":
                line["transcript"], line["confidence"], line["source_model"] = \
                    heard, heard_conf, "ensemble-asr"
                replaced += 1

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{pl}.json").write_text(json.dumps(doc, indent=1))
    print(f"\n{NAMES.get(pl, pl)}: kept {kept}, ASR replaced {replaced}, "
          f"review queue {len(review) - replaced}")
    for wem_id, label, atlas_text, atlas_conf, heard, heard_conf, action in review:
        print(f"  [{action}] {wem_id} [{label}] atlas={atlas_text!r} ({atlas_conf}) "
              f"asr={heard!r} ({heard_conf})")


if __name__ == "__main__":
    main()
