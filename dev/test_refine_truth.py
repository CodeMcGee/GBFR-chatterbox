#!/usr/bin/env python3
"""E8 eval: run the refine prompt against every ground-truth line.

For each truth.json verified line, feed the refine context with the
historically WRONG draft (where recorded) and score whether omni recovers the
human-verified text, echoes the wrong draft, or invents something else.
Lines with no recorded wrong draft get their correct text as draft instead -
that tests the opposite failure (breaking a correct line).

Usage: test_refine_truth.py [--base URL] [--no-drafts]  (--no-drafts: plain ctx, no draft shown)
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
from retranscribe import Audio, build_ctx
from refine import refine_ctx
from smoke_qwen3omni import transcribe
import serve

# What the atlas said BEFORE the human fixed it (from corrections history).
WRONG_DRAFT = {
    "441369863": "Hanguard!",
    "355245523": "Lash me whiskers!",
    "780614151": "Well, pincer meets timbers!",
    "1019504325": "Fire rose!",
    "35162464": "Rolan, fix it!",
    "495980171": "Master Primark!",
    "845626067": "Mercy unto these skies!",
    "825022410": "You screwed up, thing!",
    "36524872": "Lohenwolf!",
    "879962080": "Lohenwolf!",
    "745122813": "You're a gunner.",
    "779504685": "Wreck em!",
}


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--no-drafts", action="store_true")
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    truth = json.loads((HERE / "truth.json").read_text())["verified"]
    ex_map = json.loads((HERE / "exemplars.json").read_text())
    docs = {}

    rec = ech = brk = inv = 0
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        for wid, t in truth.items():
            pl = t["pl"]
            if pl not in docs:
                docs[pl] = json.loads((ROOT / "data/per-character" / f"{pl}.json").read_text())["lines"]
            r = docs[pl].get(wid)
            if not r or not r.get("bank"):
                print(f"  {wid}: no audio, skipped"); continue
            exemplars = []
            for e in ex_map.get(pl, []):
                er = docs[pl].get(e["wem_id"])
                if not er or e["wem_id"] == wid:
                    continue
                ew = pathlib.Path(td) / f"ex_{pl}_{e['wem_id']}.wav"
                if not ew.exists():
                    try:
                        audio.wav(er["bank"], e["wem_id"], ew)
                    except Exception:
                        continue
                exemplars.append((str(ew), e["transcript"]))
            draft = WRONG_DRAFT.get(wid, t["text"])
            wrong_draft = wid in WRONG_DRAFT
            try:
                audio.wav(r["bank"], wid, wav)
                ctx = (build_ctx(pl, r.get("label", "")) if a.no_drafts
                       else refine_ctx(pl, r.get("label", ""), {"pass1": draft}))
                got, conf = transcribe(a.base, a.model, wav, ctx, exemplars, with_conf=True)
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            if norm(got) == norm(t["text"]):
                verdict = "RECOVERED" if wrong_draft else "kept-correct"
                rec += wrong_draft; brk += 0
            elif norm(got) == norm(draft) and wrong_draft:
                verdict = "ECHOED-WRONG"; ech += 1
            elif wrong_draft:
                verdict = "other-wrong"; inv += 1
            else:
                verdict = "BROKE-CORRECT"; brk += 1
            print(f"  [{verdict}] {wid} ({serve.NAMES.get(pl,pl)}) draft={draft!r} -> {got!r} ({conf}) truth={t['text']!r}", flush=True)

    n_wrong = sum(1 for w in truth if w in WRONG_DRAFT)
    n_ok = sum(1 for w in truth if w not in WRONG_DRAFT)
    print(f"\nwrong-draft lines: {rec}/{n_wrong} recovered, {ech} echoed, {inv} other")
    print(f"correct-draft lines: {n_ok - brk}/{n_ok} kept, {brk} broken")


if __name__ == "__main__":
    main()
