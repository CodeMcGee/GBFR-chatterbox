#!/usr/bin/env python3
"""E10: blind cloud second-opinion on the known-hard lines.

Sends each truth-corpus line that a local model historically got wrong to a
big-eared model via a LAN litellm gateway, with the same neutral
per-line context the local bake uses — no drafts, no catchphrases, nothing to
parrot. Scores against truth.json.

Auth: reads LITELLM_KEY from the environment. Never stored.

Usage: LITELLM_KEY=... cloud_check.py [--base $LITELLM_BASE]
                                      [--model chat-fast] [--all-verified]
"""
import argparse
import base64
import json
import os
import pathlib
import re
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from retranscribe import Audio, build_ctx
from test_refine_truth import WRONG_DRAFT
import serve

SYSTEM = (
    "You transcribe short English combat voice lines from the game Granblue "
    "Fantasy: Relink. Lines are shouted, often under two seconds. Transcribe "
    "the spoken English exactly. If the clip is a wordless grunt or battle "
    "cry, spell it phonetically (e.g. Hah!, Hyah!, Nngh!). Output ONLY the "
    "transcription - no quotes, no notes.")


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def ask(base, key, model, wav, ctx):
    b = base64.b64encode(pathlib.Path(wav).read_bytes()).decode()
    body = json.dumps({"model": model, "temperature": 0, "max_tokens": 64, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": f"Context: {ctx}"},
            {"type": "input_audio", "input_audio": {"data": b, "format": "wav"}}]},
    ]}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body,
                                 {"Content-Type": "application/json",
                                  "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("LITELLM_BASE", "http://127.0.0.1:4000/v1"))
    ap.add_argument("--model", default="chat-fast")
    ap.add_argument("--all-verified", action="store_true",
                    help="run every corpus line, not just the known-hard ones")
    ap.add_argument("--game")
    a = ap.parse_args()
    key = os.environ.get("LITELLM_KEY")
    if not key:
        sys.exit("set LITELLM_KEY in the environment")

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    truth = json.loads((HERE / "truth.json").read_text())["verified"]
    targets = {w: t for w, t in truth.items()
               if a.all_verified or w in WRONG_DRAFT}
    docs, right = {}, 0

    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        for wid, t in targets.items():
            pl = t["pl"]
            if pl not in docs:
                docs[pl] = json.loads((ROOT / "data/per-character" / f"{pl}.json").read_text())["lines"]
            r = docs[pl].get(wid)
            if not r or not r.get("bank"):
                continue
            audio.wav(r["bank"], wid, wav)
            try:
                got = ask(a.base, key, a.model, wav, build_ctx(pl, r.get("label", "")))
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            ok = norm(got) == norm(t["text"])
            right += ok
            local_wrong = WRONG_DRAFT.get(wid, "")
            print(f"  [{'MATCH' if ok else 'diff '}] {wid} ({serve.NAMES.get(pl, pl)}): "
                  f"got {got!r}  truth {t['text']!r}"
                  + (f"  (local said {local_wrong!r})" if local_wrong else ""), flush=True)
    print(f"\n{a.model}: {right}/{len(targets)} exact (punct-insensitive)")


if __name__ == "__main__":
    main()
