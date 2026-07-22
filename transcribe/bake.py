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
from concurrent.futures import ThreadPoolExecutor, as_completed

from transcribe import ATLAS_DIR, NAMES, PKG, ROOT, find_game, resolve_pl
from transcribe.audio import Audio
from transcribe.context import build_ctx
from transcribe.omni import audio_part, transcribe


def bake_character(pl, doc, audio, base, model, ex_map, out_file, workers=6):
    """Transcribe every non-human line of one character's doc and write it to
    out_file. Returns the number of lines transcribed.

    Audio decoding is serial (the bank/pck readers share file handles); the
    model requests fan out to `workers` threads so the server can batch them -
    one request at a time leaves the GPU mostly idle."""
    lines = doc["lines"]
    with tempfile.TemporaryDirectory() as workdir:
        # per-character exemplars: decode each once, reuse for every line
        exemplars = []
        for example in ex_map.get(pl, []):
            example_id = example["wem_id"]
            atlas_line = lines.get(example_id)
            if not atlas_line:
                continue
            example_wav = pathlib.Path(workdir) / f"ex_{example_id}.wav"
            try:
                audio.wav(atlas_line["bank"], example_id, example_wav)
                # encode once here, not once per line in transcribe()
                exemplars.append((audio_part(example_wav), example["transcript"], example_id))
            except Exception:
                pass

        todo = []
        for wem_id, line in lines.items():
            if not line.get("bank") or line.get("source_model") == "human":
                continue                        # never rebake a human-verified line
            wav = pathlib.Path(workdir) / f"{wem_id}.wav"
            try:
                audio.wav(line["bank"], wem_id, wav)
            except Exception as err:
                print(f"  {pl} {wem_id}: {type(err).__name__}: {err}", flush=True)
                continue
            todo.append((wem_id, line, wav))

        def transcribe_line(wem_id, wav):
            """One model request; deletes its wav so the temp dir stays small."""
            fewshot = [(part, text) for part, text, ex_id in exemplars
                       if ex_id != wem_id]      # never show the target its own answer
            try:
                return transcribe(base, model, wav,
                                  build_ctx(pl, lines[wem_id].get("label", "")), fewshot,
                                  with_conf=True)
            finally:
                wav.unlink(missing_ok=True)

        transcribed = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(transcribe_line, wem_id, wav): (wem_id, line)
                       for wem_id, line, wav in todo}
            for future in as_completed(futures):
                wem_id, line = futures[future]
                try:
                    got, conf = future.result()
                except Exception as err:
                    print(f"  {pl} {wem_id}: {type(err).__name__}: {err}", flush=True)
                    continue
                line["transcript"] = got
                line["confidence"] = conf
                line["source_model"] = "qwen3-omni"
                transcribed += 1
    out_file.write_text(json.dumps(doc, indent=1))
    return transcribed


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
    ap.add_argument("--workers", type=int, default=6,
                    help="concurrent model requests; the server batches them")
    args = ap.parse_args(argv)

    audio = Audio(find_game(args.game))
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    ex_map = {} if args.no_exemplars else json.loads((PKG / "exemplars.json").read_text())

    atlas_dir = pathlib.Path(args.atlas_dir)
    pls = sorted(p.stem for p in atlas_dir.glob("pl*.json"))
    if args.only:
        want = {resolve_pl(x) for x in args.only.split(",")}
        pls = [p for p in pls if p in want]

    for pl in pls:
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {NAMES.get(pl, pl)} (done)", flush=True)
            continue
        doc = json.loads((atlas_dir / f"{pl}.json").read_text())
        n = bake_character(pl, doc, audio, args.base, args.model, ex_map, out_file,
                           workers=args.workers)
        print(f"{NAMES.get(pl, pl)}: {n} lines -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
