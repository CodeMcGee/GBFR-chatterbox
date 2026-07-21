#!/usr/bin/env python3
"""Re-transcribe Fediel (pl2900) with the partner's race+gender injected.

Fediel is a primal beast and calls allies by race and gender ("Erune man!",
"Human lass!"), not by name. Her partner-directed lines encode the ally in the
label (_PL####), so we look up that ally's race+gender (dev/races.json) and feed
it to the model as context. Rewrites build/atlas-omni/pl2900.json in place.
"""
import json, pathlib, re, sys, tempfile
sys.path.insert(0, "dev"); sys.path.insert(0, ".")
import serve
from retranscribe import Audio
from smoke_qwen3omni import transcribe, decode_label

BASE, MODEL, PL = "http://127.0.0.1:8210/v1", "qwen3-omni", "pl2900"
GENDER = {"male": "man", "female": "woman"}


def main():
    races = json.loads((pathlib.Path("dev/races.json")).read_text())
    au = Audio(pathlib.Path(serve.find_game()))
    f = pathlib.Path("build/atlas-omni/pl2900.json")
    doc = json.loads(f.read_text()); lines = doc["lines"]
    ex_map = json.loads(pathlib.Path("dev/exemplars.json").read_text())

    n = 0
    with tempfile.TemporaryDirectory() as td:
        exemplars = []
        for e in ex_map.get(PL, []):
            er = lines.get(e["wem_id"])
            if not er:
                continue
            ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
            try:
                au.wav(er["bank"], e["wem_id"], ew)
                exemplars.append((str(ew), e["transcript"]))
            except Exception:
                pass
        for wid, r in lines.items():
            if not r.get("bank"):
                continue
            ctx = decode_label(r.get("label", ""))
            m = re.search(r"_PL(\d{4})$", r.get("label", ""))          # partner-directed line
            if m:
                pr = races.get("pl" + m.group(1))
                if pr and pr.get("race") not in (None, "Other"):
                    g = GENDER.get(pr.get("gender"), "")
                    ctx += (f". Fediel is a primal beast who names allies by race "
                            f"and gender; the ally referred to here is a {pr['race']} {g}".rstrip())
            wav = pathlib.Path(td) / "t.wav"
            try:
                au.wav(r["bank"], wid, wav)
                ex = [e for e in exemplars if not e[0].endswith(f"ex_{wid}.wav")]
                r["transcript"] = transcribe(BASE, MODEL, wav, ctx, ex)
                n += 1
            except Exception as e:
                print(f"  {wid}: {type(e).__name__}: {e}", flush=True)
    f.write_text(json.dumps(doc, indent=1))
    print(f"Fediel: {n} lines re-transcribed with partner race+gender", flush=True)


if __name__ == "__main__":
    main()
