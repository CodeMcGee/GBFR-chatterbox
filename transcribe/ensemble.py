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
import re
import tempfile

from transcribe import ATLAS_DIR, ROOT, find_game
from transcribe.asr import asr, hotwords
from transcribe.audio import Audio
from transcribe.context import build_ctx


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8211/v1")
    ap.add_argument("--model", default="qwen3-asr")
    ap.add_argument("--pl", default="pl2200")
    ap.add_argument("--out", default="build/atlas-ensemble")
    ap.add_argument("--gate", type=float, default=-0.3)
    ap.add_argument("--game")
    a = ap.parse_args(argv)

    audio = Audio(find_game(a.game))
    doc = json.loads((ATLAS_DIR / f"{a.pl}.json").read_text())
    ctx = hotwords(a.pl)
    all_phrases = [p for p in ctx.split(". ") if p]

    def echo(text):
        return sum(1 for p in all_phrases if norm(p) in norm(text)) >= 3

    review, took, kept = [], 0, 0
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        for wid, r in doc["lines"].items():
            if not r.get("bank"):
                continue
            try:
                audio.wav(r["bank"], wid, wav)
                got, conf = asr(a.base, a.model, wav, ctx)
                if got and (not got.isascii() or echo(got)):
                    got = ""
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            at = r.get("transcript") or ""
            if not got or norm(got) == norm(at) or r.get("source_model") == "human":
                kept += 1
                continue
            if (r.get("confidence") or 0) <= a.gate:
                review.append((wid, r.get("label", ""), at, r.get("confidence"), got, conf, "TOOK ASR"))
                r["transcript"], r["confidence"], r["source_model"] = got, conf, "ensemble-asr"
                took += 1
            else:
                review.append((wid, r.get("label", ""), at, r.get("confidence"), got, conf, "kept atlas"))

    out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{a.pl}.json").write_text(json.dumps(doc, indent=1))
    print(f"\n{a.pl}: kept {kept}, ASR replaced {took}, review queue {len(review) - took}")
    for wid, lab, at, ac, got, gc, act in review:
        print(f"  [{act}] {wid} [{lab}] atlas={at!r} ({ac}) asr={got!r} ({gc})")


if __name__ == "__main__":
    main()
