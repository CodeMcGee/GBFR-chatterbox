#!/usr/bin/env python3
"""Re-do the wordless grunt lines in place with the current prompt.

A grunt line is one the model rendered non-verbal: an asterisk descriptor
(*sharp exhale*) or the bare "Hah!" default. Re-transcribes just those - no
exemplars, since worded exemplars bias grunts back toward a single filler.
Everything else is left untouched. Resumable.

Usage: regrunt.py [atlas_dir]   (default data/per-character)
"""
import glob, json, pathlib, re, sys, tempfile
sys.path.insert(0, "dev"); sys.path.insert(0, ".")
import serve
from retranscribe import Audio
from smoke_qwen3omni import transcribe, GRUNT_PROMPT

BASE, MODEL = "http://127.0.0.1:8210/v1", "qwen3-omni"


def is_grunt(t):
    t = (t or "").strip()
    return t.startswith("*") or re.sub(r"[^a-z]", "", t.lower()) == "hah"


def main():
    atlas_dir = sys.argv[1] if len(sys.argv) > 1 else "data/per-character"
    au = Audio(pathlib.Path(serve.find_game()))
    total = 0
    for f in sorted(glob.glob(f"{atlas_dir}/pl*.json")):
        pl = pathlib.Path(f).stem
        doc = json.loads(pathlib.Path(f).read_text())
        lines = doc["lines"]
        targets = [w for w, r in lines.items() if is_grunt(r["transcript"])]
        if not targets:
            print(f"{pl}: 0 grunts", flush=True); continue
        with tempfile.TemporaryDirectory() as td:
            n = 0
            for w in targets:
                r = lines[w]
                if not r.get("bank"):
                    continue
                wav = pathlib.Path(td) / "t.wav"
                try:
                    au.wav(r["bank"], w, wav)
                    r["transcript"] = transcribe(BASE, MODEL, wav, system=GRUNT_PROMPT)
                    n += 1
                except Exception as e:
                    print(f"  {pl} {w}: {type(e).__name__}: {e}", flush=True)
        pathlib.Path(f).write_text(json.dumps(doc, indent=1))
        total += n
        print(f"{pl}: {n} grunts redone", flush=True)
    print(f"TOTAL redone: {total}", flush=True)


if __name__ == "__main__":
    main()
