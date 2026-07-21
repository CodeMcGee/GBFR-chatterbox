#!/usr/bin/env python3
"""Re-do only the generic-filler grunt lines in build/atlas-omni with the current
prompt (in place). A grunt line is one whose omni transcript is just "Hah!".
Leaves every other transcript untouched. Resumable: re-run picks up remaining fillers.
"""
import glob, json, pathlib, re, sys, tempfile
sys.path.insert(0, "dev"); sys.path.insert(0, ".")
import serve
from retranscribe import Audio
from smoke_qwen3omni import transcribe, decode_label

BASE, MODEL = "http://127.0.0.1:8210/v1", "qwen3-omni"


def norm(t):
    return re.sub(r"[^a-z]", "", (t or "").lower())


def main():
    au = Audio(pathlib.Path(serve.find_game()))
    ex_map = json.loads(pathlib.Path("dev/exemplars.json").read_text())
    total = 0
    for f in sorted(glob.glob("build/atlas-omni/pl*.json")):
        pl = pathlib.Path(f).stem
        doc = json.loads(pathlib.Path(f).read_text())
        lines = doc["lines"]
        targets = [w for w, r in lines.items() if norm(r["transcript"]) == "hah"]
        if not targets:
            print(f"{pl}: 0 fillers", flush=True); continue
        with tempfile.TemporaryDirectory() as td:
            exemplars = []
            for e in ex_map.get(pl, []):
                er = lines.get(e["wem_id"])
                if not er:
                    continue
                ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
                try:
                    au.wav(er["bank"], e["wem_id"], ew)
                    exemplars.append((str(ew), e["transcript"]))
                except Exception:
                    pass
            n = 0
            for w in targets:
                r = lines[w]
                if not r.get("bank"):
                    continue
                wav = pathlib.Path(td) / "t.wav"
                try:
                    au.wav(r["bank"], w, wav)
                    ex = [e for e in exemplars if not e[0].endswith(f"ex_{w}.wav")]
                    r["transcript"] = transcribe(BASE, MODEL, wav, decode_label(r.get("label", "")), ex)
                    n += 1
                except Exception as e:
                    print(f"  {pl} {w}: {type(e).__name__}: {e}", flush=True)
        pathlib.Path(f).write_text(json.dumps(doc, indent=1))
        total += n
        print(f"{pl}: {n} filler lines redone", flush=True)
    print(f"TOTAL redone: {total}", flush=True)


if __name__ == "__main__":
    main()
