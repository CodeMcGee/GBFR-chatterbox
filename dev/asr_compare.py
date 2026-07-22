#!/usr/bin/env python3
"""Compare Qwen3-ASR against the atlas / omni passes on Seofon clips.

Qwen3-ASR protocol (vllm): system message = plain context/bias text, user
message = audio only, output = "language <lang><asr_text><transcript>".
No few-shot exemplars, no instruction-following - it is a dedicated ASR model.

Usage: asr_compare.py [--base http://127.0.0.1:8211/v1] [--pl pl2200]
                      [--out build/atlas-asr] [--game <path>]
"""
import argparse
import base64
import json
import pathlib
import re
import sys
import tempfile
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from retranscribe import Audio, build_ctx
import serve


def asr(base, model, wav_path, ctx):
    b = base64.b64encode(pathlib.Path(wav_path).read_bytes()).decode()
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 128,
        "messages": [
            {"role": "system", "content": ctx},
            {"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": b, "format": "wav"}}]},
        ],
        "logprobs": True,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        ch = json.loads(r.read())["choices"][0]
    text = ch["message"]["content"]
    m = re.search(r"<asr_text>(.*)", text, re.S)
    text = (m.group(1) if m else text).strip()
    lps = [t["logprob"] for t in (ch.get("logprobs") or {}).get("content") or []]
    return text, (round(sum(lps) / len(lps), 3) if lps else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8211/v1")
    ap.add_argument("--model", default="qwen3-asr")
    ap.add_argument("--pl", default="pl2200")
    ap.add_argument("--out", default="build/atlas-asr")
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    doc = json.loads((ROOT / "data/per-character" / f"{a.pl}.json").read_text())
    out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "t.wav"
        n = 0
        for wid, r in doc["lines"].items():
            if not r.get("bank"):
                continue
            try:
                audio.wav(r["bank"], wid, wav)
                got, conf = asr(a.base, a.model, wav, build_ctx(a.pl, r.get("label", "")))
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            r["transcript"] = got
            r["confidence"] = conf
            r["source_model"] = a.model
            n += 1
    out_file = out_dir / f"{a.pl}.json"
    out_file.write_text(json.dumps(doc, indent=1))
    print(f"{a.pl}: {n} lines -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
