#!/usr/bin/env python3
"""Ensemble transcription pass: Qwen3-ASR with per-character hotwords over the
omni-baked atlas. Merge rule (precision-first, see EXPERIMENTS.md):

- ASR empty (barks/grunts/name-shouts) -> keep atlas text.
- ASR agrees modulo case/punct        -> keep atlas text (preserves register).
- Disagreement: atlas confidence <= GATE -> take ASR (atlas was likely wrong);
  else keep atlas, emit to the review queue.

Hotwords: character's glossary skills/SBA + ally/world names + any
truth.json-verified catchphrases for this character. E5 showed this biasing
only surfaces a phrase when the audio supports it.

Usage: ensemble.py [--pl pl2200] [--out build/atlas-ensemble] [--gate -0.3]
"""
import argparse
import json
import pathlib
import re
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from asr_compare import asr
from retranscribe import Audio
import serve


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def hotwords(pl, short=False):
    """Full list for worded lines; short=True drops ally names — a long
    context suppresses short barks (E5/E7), the lean list rescues them."""
    g = json.loads((HERE / "glossary.json").read_text())
    name = serve.NAMES.get(pl, pl)
    c = g["characters"].get(name, {})
    words = list(c.get("skills", [])) + ([c["sba"]] if "sba" in c else [])
    if not short:
        words += sorted(g["characters"])        # ally names, for call lines
    truth = json.loads((HERE / "truth.json").read_text())
    words += [t["text"] for t in truth["verified"].values() if t["pl"] == pl]
    seen, out = set(), []
    for w in words:
        if norm(w) not in seen:
            seen.add(norm(w)); out.append(w.rstrip("!.?"))
    return ". ".join(out) + "."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8211/v1")
    ap.add_argument("--model", default="qwen3-asr")
    ap.add_argument("--pl", default="pl2200")
    ap.add_argument("--out", default="build/atlas-ensemble")
    ap.add_argument("--gate", type=float, default=-0.3)
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    doc = json.loads((ROOT / "data/per-character" / f"{a.pl}.json").read_text())
    ctx = hotwords(a.pl)
    all_phrases = [p for p in ctx.split(". ") if p]
    # bark rescue vocabulary: shout-shaped phrases only, scanned in 4-phrase
    # windows - ASR reads a longer list (or conversational phrases) as
    # document context and mutes short barks entirely (E7)
    shouts = [p.rstrip(".") for p in all_phrases if len(p.split()) <= 4
              and p.rstrip(".") not in serve.NAMES.values()]
    print(f"hotword ctx ({len(ctx)} chars); rescue shouts: {shouts}", flush=True)

    def echo(text):
        return sum(1 for p in all_phrases if norm(p) in norm(text)) >= 3

    def rescue(wav):
        for i in range(0, len(shouts), 4):
            got, conf = asr(a.base, a.model, wav, ". ".join(shouts[i:i+4]) + ".")
            if got and got.isascii() and not echo(got):
                return got, conf
        return "", None

    review, took_asr, kept = [], 0, 0
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        for wid, r in doc["lines"].items():
            if not r.get("bank"):
                continue
            try:
                audio.wav(r["bank"], wid, wav)
                got, conf = asr(a.base, a.model, wav, ctx)
                if got and (not got.isascii() or echo(got)):
                    got = ""                    # CJK from hums / ctx echoed back
                if not got and (r.get("confidence") or 0) <= a.gate:
                    got, conf = rescue(wav)
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            at = r.get("transcript") or ""
            if not got or norm(got) == norm(at):
                kept += 1
                continue
            if r.get("source_model") == "human":
                kept += 1                       # never overwrite human truth
                continue
            if (r.get("confidence") or 0) <= a.gate:
                review.append((wid, r.get("label",""), at, r.get("confidence"), got, conf, "TOOK ASR"))
                r["transcript"], r["confidence"], r["source_model"] = got, conf, "ensemble-asr"
                took_asr += 1
            else:
                review.append((wid, r.get("label",""), at, r.get("confidence"), got, conf, "kept atlas"))

    out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{a.pl}.json").write_text(json.dumps(doc, indent=1))
    print(f"\n{a.pl}: kept {kept}, ASR replaced {took_asr}, review queue {len(review)-took_asr}")
    for wid, lab, at, ac, got, gc, act in review:
        print(f"  [{act}] {wid} [{lab}] atlas={at!r} ({ac}) asr={got!r} ({gc})")


if __name__ == "__main__":
    main()
