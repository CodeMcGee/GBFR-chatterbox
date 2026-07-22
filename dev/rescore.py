#!/usr/bin/env python3
"""E9: forced-choice rescoring — compare candidate transcripts by the
likelihood omni assigns them given the audio (prompt_logprobs on a forced
assistant answer). Nothing is generated, so nothing anchors or hallucinates.

Gauntlet: for every truth line with a recorded historical error, score
truth-vs-error and report which the model prefers. avg = per-token logprob
(length-normalized); sum favors shorter strings.

Usage: rescore.py [--base URL]
"""
import argparse
import base64
import json
import pathlib
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from retranscribe import Audio, build_ctx
from smoke_qwen3omni import PROMPT, _audio
from test_refine_truth import WRONG_DRAFT
import serve


def _post(base, path, payload):
    req = urllib.request.Request(f"{base}{path}", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def ntokens(base, model, text):
    return len(_post(base.replace("/v1", ""), "/tokenize",
                     {"model": model, "prompt": text})["tokens"])


def score(base, model, wav, ctx, candidate):
    """(sum, avg) logprob of candidate's tokens as the forced answer."""
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": [{"type": "text", "text": f"Context: {ctx}"},
                                             _audio(wav)]},
                {"role": "assistant", "content": candidate}]
    out = _post(base, "/chat/completions", {
        "model": model, "messages": messages, "max_tokens": 1, "temperature": 0,
        "prompt_logprobs": 0, "add_generation_prompt": False,
        "continue_final_message": True})
    plp = out.get("prompt_logprobs") or out["choices"][0].get("prompt_logprobs")
    vals = [list(t.values())[0]["logprob"] for t in plp if t]
    n = ntokens(base, model, candidate)
    tail = vals[-n:]
    return sum(tail), sum(tail) / len(tail)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    truth = json.loads((HERE / "truth.json").read_text())["verified"]
    docs, wins_avg, wins_sum, n = {}, 0, 0, 0

    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        for wid, t in truth.items():
            if wid not in WRONG_DRAFT:
                continue
            pl = t["pl"]
            if pl not in docs:
                docs[pl] = json.loads((ROOT / "data/per-character" / f"{pl}.json").read_text())["lines"]
            r = docs[pl].get(wid)
            if not r or not r.get("bank"):
                continue
            audio.wav(r["bank"], wid, wav)
            ctx = build_ctx(pl, r.get("label", ""))
            st, at_ = score(a.base, a.model, wav, ctx, t["text"])
            sw, aw = score(a.base, a.model, wav, ctx, WRONG_DRAFT[wid])
            n += 1
            wa = at_ > aw; ws = st > sw
            wins_avg += wa; wins_sum += ws
            print(f"  {'TRUTH' if wa else 'ERROR'} (avg) {wid} ({serve.NAMES.get(pl, pl)}): "
                  f"{t['text']!r} avg={at_:.3f} sum={st:.2f}  vs  "
                  f"{WRONG_DRAFT[wid]!r} avg={aw:.3f} sum={sw:.2f}", flush=True)
    print(f"\ntruth preferred: {wins_avg}/{n} by avg logprob, {wins_sum}/{n} by sum")


if __name__ == "__main__":
    main()
