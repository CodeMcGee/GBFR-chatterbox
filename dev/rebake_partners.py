#!/usr/bin/env python3
"""Re-bake the JP lines whose bake context carried a raw PL code as addressee.

The first full bake ran against a subtitles-jp.csv whose partner column held
digits ("2700") instead of names; every line with a partner got a useless
呼びかけ相手 hint. This re-runs exactly those lines against the corrected CSV
and updates the existing data/per-character-jp/plXXXX.json files in place.

Usage: rebake_partners.py [--base URL] [--workers 6]
"""
import argparse
import csv
import json
import pathlib
import re
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from chatterbox.banks import MediaBank
from jp_bake import EN_SYS, JP_SYS, ask, line_ctx, moves_of
from jp_probe import JpAudio
from transcribe import NAMES, find_game
from transcribe.omni import audio_part


def main():
    """Re-run jp_real/en_literal for every partner-context line, in place."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--label-re", default="", help="only re-bake labels matching this regex")
    ap.add_argument("--game")
    a = ap.parse_args()

    rows = [r for r in csv.DictReader(open(ROOT / "build" / "subtitles-jp.csv"))
            if r["partner"] and (not a.label_re or re.search(a.label_re, r["label"]))]
    by_pl = {}
    for row in rows:
        by_pl.setdefault(row["pl_id"], []).append(row)

    voice = pathlib.Path(find_game(a.game))
    jp_dir = voice.parent / "Japanese"
    audio = JpAudio(jp_dir)
    bank_of = {}
    for bank_path in sorted(jp_dir.glob("vo_pl*_m.bnk")):
        try:
            for wid in MediaBank(bank_path).entries:
                bank_of[str(wid)] = bank_path.name
        except Exception:
            pass

    for pl, plrows in sorted(by_pl.items()):
        out_file = ROOT / "data" / "per-character-jp" / f"{pl}.json"
        doc = json.loads(out_file.read_text())

        with tempfile.TemporaryDirectory() as workdir:
            todo = []
            for row in plrows:
                jp_wem = row["jp_wem_id"]
                bank = bank_of.get(jp_wem)
                if not bank or jp_wem not in doc["lines"]:
                    continue
                wav = pathlib.Path(workdir) / f"{jp_wem}.wav"
                try:
                    audio.wav(bank, jp_wem, wav)
                except Exception as err:
                    print(f"  {pl} {jp_wem}: {type(err).__name__}: {err}", flush=True)
                    continue
                todo.append((row, wav))

            def rebake(row, wav):
                """Both tracks for one clip with the corrected addressee context."""
                try:
                    part = audio_part(wav)
                    ctx = line_ctx(row)
                    jp_text, jp_conf = ask(a.base, a.model, JP_SYS,
                                           [{"type": "text", "text": ctx}, part], True)
                    moves = moves_of(row["character"])
                    en_ctx = ctx + (f" Move names in English: {moves}." if moves else "")
                    en_text, _ = ask(a.base, a.model, EN_SYS,
                                     [{"type": "text", "text": en_ctx}, part])
                    return jp_text, jp_conf, en_text
                finally:
                    wav.unlink(missing_ok=True)

            done = 0
            with ThreadPoolExecutor(max_workers=a.workers) as pool:
                futures = {pool.submit(rebake, row, wav): row for row, wav in todo}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        jp_text, jp_conf, en_text = future.result()
                    except Exception as err:
                        print(f"  {pl} {row['jp_wem_id']}: {type(err).__name__}: {err}",
                              flush=True)
                        continue
                    doc["lines"][row["jp_wem_id"]].update(
                        jp_real=jp_text, jp_confidence=jp_conf, en_literal=en_text)
                    done += 1
        out_file.write_text(json.dumps(doc, ensure_ascii=False, indent=1))
        print(f"{NAMES.get(pl, pl)}: {done} partner lines re-baked", flush=True)


if __name__ == "__main__":
    main()
