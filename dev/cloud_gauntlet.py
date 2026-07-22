#!/usr/bin/env python3
"""E11: fully extravagant cloud gauntlet.

All truth-corpus lines x 3 prompt variants x 5 votes against a top audio model
via OpenRouter. Every request's full detail is appended to a results JSONL so
any later comparison re-reads the file instead of re-spending: wid, variant,
vote, raw output, usage, latency, model. Reruns skip (wid,variant,vote) rows
already on disk, so the run is resumable and idempotent.

Variants:
  zero     - neutral transcription system prompt + per-line ctx
  glossary - the local bake's full glossary prompt + per-line ctx
  fewshot  - glossary prompt + per-character exemplar audio pairs (as in the
             local omni harness), minus the target line

Auth: OPENROUTER_API_KEY from the environment only. Never written anywhere.

Usage: OPENROUTER_API_KEY=... cloud_gauntlet.py [--model google/gemini-3.1-pro-preview]
       [--votes 5] [--out build/cloud-gauntlet/results.jsonl] [--workers 6]
Score later: cloud_gauntlet.py --score-only
"""
import argparse
import base64
import collections
import json
import os
import pathlib
import re
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from retranscribe import Audio, build_ctx
from smoke_qwen3omni import PROMPT, _audio
from cloud_check import SYSTEM as NEUTRAL
from test_refine_truth import WRONG_DRAFT
import serve

VARIANTS = ("zero", "glossary", "fewshot")


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def call(key, model, messages, temperature, max_tokens=512, reasoning="low"):
    payload = {"model": model, "temperature": temperature,
               "max_tokens": max_tokens, "messages": messages}
    if reasoning != "default":
        # reasoning models: keep thinking short so answers aren't length-cut
        payload["reasoning"] = {"effort": reasoning}
    body = json.dumps(payload).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                                 body, {"Content-Type": "application/json",
                                        "Authorization": f"Bearer {key}"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.loads(r.read())
    ch = out["choices"][0]
    return {"text": (ch["message"]["content"] or "").strip(),
            "usage": out.get("usage"), "latency_s": round(time.time() - t0, 2),
            "finish": ch.get("finish_reason")}


def messages_for(variant, ctx, wav_b64, exemplars):
    audio_part = {"type": "input_audio", "input_audio": {"data": wav_b64, "format": "wav"}}
    if variant == "zero":
        return [{"role": "system", "content": NEUTRAL},
                {"role": "user", "content": [{"type": "text", "text": f"Context: {ctx}"}, audio_part]}]
    msgs = [{"role": "system", "content": PROMPT}]
    if variant == "fewshot":
        for ex_b64, ex_text in exemplars:
            msgs.append({"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": ex_b64, "format": "wav"}}]})
            msgs.append({"role": "assistant", "content": ex_text})
    msgs.append({"role": "user", "content": [{"type": "text", "text": f"Context: {ctx}"}, audio_part]})
    return msgs


def score(out_path):
    rows = [json.loads(l) for l in out_path.read_text().splitlines() if l.strip()]
    truth = json.loads((HERE / "truth.json").read_text())["verified"]
    by = collections.defaultdict(list)
    for r in rows:
        if "error" not in r:
            by[(r["wid"], r["variant"])].append(r["text"])
    print(f"{len(rows)} rows on disk, {len(by)} (line,variant) cells")
    tok_in = sum((r.get("usage") or {}).get("prompt_tokens", 0) for r in rows)
    tok_out = sum((r.get("usage") or {}).get("completion_tokens", 0) for r in rows)
    print(f"tokens: {tok_in} in, {tok_out} out")
    for variant in VARIANTS:
        exact = major = 0
        n = 0
        hard_exact = hard_n = 0
        for wid, t in truth.items():
            votes = by.get((wid, variant))
            if not votes:
                continue
            n += 1
            tn = norm(t["text"])
            hits = sum(1 for v in votes if norm(v) == tn)
            exact += hits > 0
            counts = collections.Counter(norm(v) for v in votes)
            major += counts.most_common(1)[0][0] == tn
            if wid in WRONG_DRAFT:
                hard_n += 1
                hard_exact += hits > 0
        print(f"{variant:9s}: any-vote exact {exact}/{n}, majority-vote {major}/{n}, "
              f"known-hard any-vote {hard_exact}/{hard_n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-3.1-pro-preview")
    ap.add_argument("--votes", type=int, default=5)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--out", default="build/cloud-gauntlet/results.jsonl")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-tokens", type=int, default=512)
    ap.add_argument("--reasoning", default="low")
    ap.add_argument("--score-only", action="store_true")
    ap.add_argument("--game")
    a = ap.parse_args()

    out_path = ROOT / a.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if a.score_only:
        return score(out_path)

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("set OPENROUTER_API_KEY in the environment")

    done = set()
    if out_path.exists():
        for l in out_path.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                if "error" not in r:
                    done.add((r["wid"], r["variant"], r["vote"], r["model"]))

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    truth = json.loads((HERE / "truth.json").read_text())["verified"]
    ex_map = json.loads((HERE / "exemplars.json").read_text())
    docs, wav64, ex64 = {}, {}, {}

    with tempfile.TemporaryDirectory() as td:
        # pre-encode target + exemplar audio once
        for wid, t in truth.items():
            pl = t["pl"]
            if pl not in docs:
                docs[pl] = json.loads((ROOT / "data/per-character" / f"{pl}.json").read_text())["lines"]
            r = docs[pl].get(wid)
            if not r or not r.get("bank"):
                continue
            w = pathlib.Path(td) / "t.wav"
            audio.wav(r["bank"], wid, w)
            wav64[wid] = base64.b64encode(w.read_bytes()).decode()
        for pl in {t["pl"] for t in truth.values()}:
            pairs = []
            for e in ex_map.get(pl, []):
                er = docs.get(pl, {}).get(e["wem_id"])
                if not er:
                    continue
                w = pathlib.Path(td) / "e.wav"
                try:
                    audio.wav(er["bank"], e["wem_id"], w)
                    pairs.append((e["wem_id"], base64.b64encode(w.read_bytes()).decode(), e["transcript"]))
                except Exception:
                    pass
            ex64[pl] = pairs

    lock = threading.Lock()
    fh = open(out_path, "a")

    def one(wid, variant, vote):
        t = truth[wid]
        ctx = build_ctx(t["pl"], docs[t["pl"]][wid].get("label", ""))
        exemplars = [(b, txt) for ewid, b, txt in ex64.get(t["pl"], []) if ewid != wid]
        msgs = messages_for(variant, ctx, wav64[wid], exemplars)
        temp = 0 if vote == 0 else a.temperature
        row = {"wid": wid, "pl": t["pl"], "character": serve.NAMES.get(t["pl"], t["pl"]),
               "variant": variant, "vote": vote, "model": a.model,
               "temperature": temp, "truth": t["text"],
               "local_wrong": WRONG_DRAFT.get(wid),
               "n_exemplars": len(exemplars) if variant == "fewshot" else 0,
               "ctx": ctx, "ts": round(time.time(), 1)}
        try:
            row.update(call(key, a.model, msgs, temp, a.max_tokens, a.reasoning))
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"
        with lock:
            fh.write(json.dumps(row) + "\n"); fh.flush()
        return row

    jobs = [(wid, v, k) for wid in wav64 for v in VARIANTS for k in range(a.votes)
            if (wid, v, k, a.model) not in done]
    print(f"{len(jobs)} requests to run ({len(done)} already on disk)", flush=True)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(lambda j: one(*j), jobs), 1):
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)
    fh.close()
    score(out_path)


if __name__ == "__main__":
    main()
