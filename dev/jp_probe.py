#!/usr/bin/env python3
"""E13 probe: JP audio for lines whose English is human-verified.

For each truth-corpus line with a Japanese twin (label join), run the JP audio
through omni two ways and compare against the verified English:
  direct   - audio -> English translation in one step
  two-step - audio -> Japanese transcript, then text -> English translation

The verified English is NOT the expected output - JP and EN scripts are
separate localizations - but it anchors "is this translation plausible" for a
maintainer who cannot read Japanese. Results append to build/jp-probe.jsonl.

Usage: jp_probe.py [--base http://127.0.0.1:8210/v1] [--limit N]
"""
import argparse
import csv
import json
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from chatterbox.banks import MediaBank, atomic_write
from chatterbox.pck import Pck
from transcribe import NAMES, PKG, ROOT as TROOT, find_game, post_json
from transcribe.audio import Audio
from transcribe.omni import audio_part

TRANSLATE = ("You hear a short Japanese combat voice line from the game Granblue "
             "Fantasy: Relink. Translate the spoken Japanese into natural English. "
             "Output ONLY the English translation.")
TRANSCRIBE_JP = ("You hear a short Japanese combat voice line from a game. "
                 "Transcribe the spoken Japanese exactly, as Japanese text. "
                 "Output ONLY the Japanese.")


class JpAudio(Audio):
    """Audio that pulls streamed data from the Japanese packages."""

    def pck(self, bank_name):
        pname = bank_name.replace("_m.bnk", ".pck")
        if pname not in self.pcks:
            self.pcks[pname] = None
            key = "sound/japanese/" + pname
            if self.archive and key in self.archive:
                tmp = TROOT / "build" / "pck-jp" / pname
                tmp.parent.mkdir(parents=True, exist_ok=True)
                if not tmp.exists():
                    atomic_write(tmp, self.archive.read(key))
                self.pcks[pname] = Pck(tmp)
        return self.pcks[pname]


def ask(base, model, system, content):
    payload = {"model": model, "temperature": 0, "max_tokens": 128,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": content}]}
    choice = post_json(base, "/chat/completions", payload)["choices"][0]
    return choice["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--game")
    a = ap.parse_args()

    truth = json.loads((PKG / "truth.json").read_text())["verified"]
    jp_of = {}
    for row in csv.DictReader(open(TROOT / "build" / "subtitles-jp.csv")):
        if row.get("en_wem_id"):
            jp_of[row["en_wem_id"]] = row["jp_wem_id"]

    voice = pathlib.Path(find_game(a.game))
    jp_dir = voice.parent / "Japanese"
    audio = JpAudio(jp_dir)

    # index: which JP bank holds each jp wem
    bank_of = {}
    for bank_path in sorted(jp_dir.glob("vo_pl*_m.bnk")):
        try:
            for wid in MediaBank(bank_path).entries:
                bank_of[str(wid)] = bank_path.name
        except Exception:
            pass
    print(f"{len(bank_of)} JP wems indexed across banks", flush=True)

    out = open(TROOT / "build" / "jp-probe.jsonl", "a")
    done = 0
    with tempfile.TemporaryDirectory() as td:
        wav = pathlib.Path(td) / "jp.wav"
        for en_wem, entry in truth.items():
            jp_wem = jp_of.get(en_wem)
            if not jp_wem or jp_wem not in bank_of:
                continue
            try:
                audio.wav(bank_of[jp_wem], jp_wem, wav)
                part = audio_part(wav)
                direct = ask(a.base, a.model, TRANSLATE, [part])
                jp_text = ask(a.base, a.model, TRANSCRIBE_JP, [part])
                two_step = ask(a.base, a.model,
                               "Translate this Japanese game voice line into natural "
                               "English. Output ONLY the English.", jp_text)
            except Exception as e:
                print(f"  {en_wem}: {type(e).__name__}: {e}", flush=True)
                continue
            row = {"en_wem": en_wem, "jp_wem": jp_wem, "character": NAMES.get(entry["pl"], entry["pl"]),
                   "english_truth": entry["text"], "direct": direct,
                   "jp_transcript": jp_text, "two_step": two_step}
            out.write(json.dumps(row, ensure_ascii=False) + "\n"); out.flush()
            print(f"{row['character']:11} EN: {entry['text'][:44]!r:46} "
                  f"direct: {direct[:44]!r:46} 2step: {two_step[:44]!r}", flush=True)
            done += 1
            if a.limit and done >= a.limit:
                break
    print(f"{done} lines probed -> build/jp-probe.jsonl", flush=True)


if __name__ == "__main__":
    main()
