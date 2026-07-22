"""Re-transcribe the atlas through a local qwen3-omni server.

Decodes every line's FULL audio from the game install and transcribes it with
the omni harness (glossary + per-character verified exemplars + per-line ctx).
Writes one JSON per character, skipping any already done, so it is resumable.
Run corrections.apply and then dev/build_atlas.py afterwards.
"""
import argparse
import json
import pathlib
import tempfile

from transcribe import ATLAS_DIR, NAMES, PKG, ROOT, find_game, resolve_pl
from transcribe.audio import Audio
from transcribe.context import build_ctx
from transcribe.omni import transcribe


def bake_character(pl, doc, audio, base, model, ex_map, out_file):
    """Transcribe every non-human line of one character's doc and write it to
    out_file. Returns the number of lines transcribed."""
    lines = doc["lines"]
    with tempfile.TemporaryDirectory() as td:
        # per-character exemplars: decode each once, reuse for every line
        exemplars = []
        for e in ex_map.get(pl, []):
            er = lines.get(e["wem_id"])
            if not er:
                continue
            ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
            try:
                audio.wav(er["bank"], e["wem_id"], ew)
                exemplars.append((str(ew), e["transcript"], e["wem_id"]))
            except Exception:
                pass

        new = 0
        wav = pathlib.Path(td) / "t.wav"
        for wid, r in lines.items():
            if not r.get("bank") or r.get("source_model") == "human":
                continue                        # never rebake a human-verified line
            try:
                audio.wav(r["bank"], wid, wav)
                ex = [(w, t) for w, t, ewid in exemplars if ewid != wid]
                got, conf = transcribe(base, model, wav,
                                       build_ctx(pl, r.get("label", "")), ex,
                                       with_conf=True)
            except Exception as e:
                print(f"  {pl} {wid}: {type(e).__name__}: {e}", flush=True)
                continue
            r["transcript"] = got
            r["confidence"] = conf
            r["source_model"] = "qwen3-omni"
            new += 1
    out_file.write_text(json.dumps(doc, indent=1))
    return new


def main(argv=None):
    """CLI: bake all (or --only named) characters into --out."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--game")
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--atlas-dir", default=str(ATLAS_DIR))
    ap.add_argument("--out", default="build/atlas-omni")
    ap.add_argument("--only", default="",
                    help="comma-separated character names or pl ids")
    ap.add_argument("--no-exemplars", action="store_true")
    a = ap.parse_args(argv)

    audio = Audio(find_game(a.game))
    out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)
    ex_map = {} if a.no_exemplars else json.loads((PKG / "exemplars.json").read_text())

    atlas_dir = pathlib.Path(a.atlas_dir)
    pls = sorted(p.stem for p in atlas_dir.glob("pl*.json"))
    if a.only:
        want = {resolve_pl(x) for x in a.only.split(",")}
        pls = [p for p in pls if p in want]

    for pl in pls:
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {NAMES.get(pl, pl)} (done)", flush=True); continue
        doc = json.loads((atlas_dir / f"{pl}.json").read_text())
        n = bake_character(pl, doc, audio, a.base, a.model, ex_map, out_file)
        print(f"{NAMES.get(pl, pl)}: {n} lines -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
