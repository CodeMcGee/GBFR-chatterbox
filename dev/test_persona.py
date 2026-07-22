#!/usr/bin/env python3
"""Stage 1: does a character persona in the prompt fix a mis-transcription?

A/B on ONE clip, holding exemplars fixed so the persona line is the only
variable. Target: Seofon's en-garde bark (pl2200 wem 441369863), currently
"Hanguard!" (conf -0.669). If the persona flips it to "En garde!", we roll the
personas out to every character and rebake; if not, we don't.

Usage: test_persona.py [--base URL] [--wem 441369863] [--pl pl2200]
"""
import argparse
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from transcribe.audio import Audio
from transcribe.context import build_ctx
from transcribe import PKG
from transcribe.omni import transcribe
import serve

# The E1 blurb, kept as the historical record; personas are retired from the
# pipeline (EXPERIMENTS E2/E3) so this experiment defines its own.
PERSONA = {
    "pl2200": "Seofon is a flamboyant, supremely confident master swordsman and "
              "leader of the Eternals, who fights and speaks like a chivalrous "
              'duelist, using fencing calls such as "En garde!".',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--wem", default="441369863")
    ap.add_argument("--pl", default="pl2200")
    ap.add_argument("--game")
    a = ap.parse_args()

    audio = Audio(pathlib.Path(serve.find_game(a.game)))
    doc = json.loads((ROOT / "data/per-character" / f"{a.pl}.json").read_text())
    r = doc["lines"][a.wem]
    label = r.get("label", "")
    ex_map = json.loads((PKG / "exemplars.json").read_text())

    with tempfile.TemporaryDirectory() as td:
        # same exemplars the bake uses, minus the target line itself
        exemplars = []
        for e in ex_map.get(a.pl, []):
            if e["wem_id"] == a.wem:
                continue
            er = doc["lines"].get(e["wem_id"])
            if not er:
                continue
            ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
            audio.wav(er["bank"], e["wem_id"], ew)
            exemplars.append((str(ew), e["transcript"]))

        wav = pathlib.Path(td) / "t.wav"
        audio.wav(r["bank"], a.wem, wav)

        base_ctx = build_ctx(a.pl, label)
        persona_ctx = f"{PERSONA[a.pl]} {base_ctx}"

        print(f"clip {a.wem} ({label})  current atlas: {r.get('transcript')!r}\n")
        for name, ctx in [("baseline", base_ctx), ("persona ", persona_ctx)]:
            got, conf = transcribe(a.base, a.model, wav, ctx, exemplars, with_conf=True)
            print(f"{name}: {got!r}   conf={conf}")
            print(f"          ctx={ctx}\n")


if __name__ == "__main__":
    main()
