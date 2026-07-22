#!/usr/bin/env python3
"""E8: iterative refinement — second omni pass over LOW-CONFIDENCE lines only,
feeding the first-pass candidates and asking the model to adjudicate by ear.

The confidence gate is the safety mechanism (E2/E3): rich context (persona,
candidate transcripts, catchphrases) is proven to fix hard barks AND proven to
poison confident lines — so confident lines never enter this pass.

Usage: refine.py [--pl pl2200] [--gate -0.3] [--limit N] [--out build/atlas-refined]
       refine.py --wem 441369863 --pl pl2200      # single-clip inspection
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from retranscribe import Audio, build_ctx, PERSONA
from smoke_qwen3omni import transcribe
import serve


def refine_ctx(pl, label, drafts):
    """First-pass context plus draft transcripts to adjudicate. Drafts are
    labeled as unreliable so the model listens instead of echoing."""
    ctx = build_ctx(pl, label)
    ds = "; ".join(f"{k}: {v!r}" for k, v in drafts.items() if v)
    if ds:
        ctx += (f" Draft transcriptions from earlier passes (each may be wrong"
                f" - trust the audio over the drafts): {ds}.")
    return ctx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--pl", default="pl2200")
    ap.add_argument("--gate", type=float, default=-0.3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--wem", default="")
    ap.add_argument("--asr-dir", default="build/atlas-asr",
                    help="optional second-opinion source for drafts")
    ap.add_argument("--out", default="build/atlas-refined")
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    doc = json.loads((ROOT / "data/per-character" / f"{a.pl}.json").read_text())
    asr_p = ROOT / a.asr_dir / f"{a.pl}.json"
    asr = json.loads(asr_p.read_text())["lines"] if asr_p.exists() else {}
    ex_map = json.loads((HERE / "exemplars.json").read_text())

    lines = doc["lines"]
    targets = [a.wem] if a.wem else [
        w for w, r in lines.items()
        if r.get("bank") and r.get("source_model") != "human"
        and (r.get("confidence") or 0) <= a.gate]
    if a.limit:
        targets = targets[:a.limit]
    print(f"{a.pl}: refining {len(targets)} low-conf lines (gate {a.gate})", flush=True)

    with tempfile.TemporaryDirectory() as td:
        exemplars = []
        for e in ex_map.get(a.pl, []):
            er = lines.get(e["wem_id"])
            if not er:
                continue
            ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
            try:
                audio.wav(er["bank"], e["wem_id"], ew)
                exemplars.append((str(ew), e["transcript"]))
            except Exception:
                pass

        wav = pathlib.Path(td) / "t.wav"
        changed = 0
        for wid in targets:
            r = lines[wid]
            drafts = {"pass1": r.get("transcript")}
            if wid in asr and (asr[wid].get("transcript") or "").strip():
                drafts["asr"] = asr[wid]["transcript"]
            try:
                audio.wav(r["bank"], wid, wav)
                ex = [e for e in exemplars if not e[0].endswith(f"ex_{wid}.wav")]
                ctx = refine_ctx(a.pl, r.get("label", ""), drafts)
                got, conf = transcribe(a.base, a.model, wav, ctx, ex, with_conf=True)
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            mark = "==" if got == r.get("transcript") else "->"
            print(f"  {wid} [{r.get('label','')}] {r.get('transcript')!r} ({r.get('confidence')}) "
                  f"{mark} {got!r} ({conf})", flush=True)
            if got != r.get("transcript"):
                changed += 1
                r["transcript"], r["confidence"], r["source_model"] = got, conf, "omni-refined"

    if not a.wem:
        out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{a.pl}.json").write_text(json.dumps(doc, indent=1))
        print(f"{a.pl}: {changed}/{len(targets)} refined -> {out_dir}/{a.pl}.json", flush=True)


if __name__ == "__main__":
    main()
