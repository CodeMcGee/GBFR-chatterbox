#!/usr/bin/env python3
"""Bake the Japanese tracks: jp_real (JP transcription) and en_literal (direct
audio -> English translation) for every JP wem in subtitles-jp.csv.

Recipe per E13 + the glossary A/B: katakana glossary in the system prompt,
per-line context naming speaker and addressee (the label already knows who a
call line addresses - a structural edge over blind listening), direct one-step
translation for the English (two-step measured worse). Writes one JSON per
character under data/per-character-jp/, resumable, concurrent like the EN bake.

Usage: jp_bake.py [--base URL] [--only Seofon,...] [--workers 6] [--limit N]
"""
import argparse
import csv
import json
import pathlib
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from chatterbox.banks import MediaBank
from jp_probe import JpAudio
from transcribe import NAMES, PKG, avg_logprob, find_game, post_json, resolve_pl
from transcribe.context import decode_label
from transcribe.omni import audio_part

GLOSSARY = json.loads((PKG / "glossary-jp.json").read_text())
KATA = "、".join(GLOSSARY["names"].values()) + "、" + "、".join(GLOSSARY["world_terms"])
JP_NAME = GLOSSARY["names"]

JP_SYS = ("あなたはゲーム『グランブルーファンタジー リリンク』の日本語戦闘ボイスを文字起こしします。"
          "短い叫び声が多いです。話された日本語を正確に書き起こしてください。"
          "言葉のない掛け声はカタカナで音の通りに書いてください。"
          "以下の固有名詞が聞こえたら、この表記を使ってください：" + KATA +
          "。出力は書き起こしのみ。")
EN_SYS = ("You hear a short Japanese combat voice line from Granblue Fantasy: Relink. "
          "Translate the spoken Japanese into natural English. Keep proper nouns as "
          "names; spellings: " + ", ".join(f"{jp}={en}" for en, jp in JP_NAME.items()) +
          ". Skill and Skybound Art names keep their English release spellings "
          "(given per line). If the clip is a wordless grunt or battle cry, spell "
          "it phonetically (e.g. Hah!, Hyah!). Never prefix the speaker's name - "
          "output ONLY the spoken line in English.")

EN_GLOSSARY = json.loads((PKG / "glossary.json").read_text())["characters"]


def moves_of(character):
    """This character's move names in the English release, for the translator."""
    entry = EN_GLOSSARY.get(character, {})
    moves = list(entry.get("skills", [])) + ([entry["sba"]] if "sba" in entry else [])
    return ", ".join(moves)


def line_ctx(row):
    """Speaker + addressee + line type, with katakana names for the JP ear."""
    speaker = row["character"]
    ctx = f"話者：{JP_NAME.get(speaker, speaker)}。"
    partner = (row.get("partner") or "").strip()
    if partner:
        ctx += f"呼びかけ相手：{JP_NAME.get(partner, partner)}。"
    line_type = decode_label(row.get("label", ""), grunt_hint=False)
    if line_type:
        ctx += f"場面：{line_type}。"
    return ctx


def ask(base, model, system, content, want_conf=False):
    payload = {"model": model, "temperature": 0, "max_tokens": 128,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": content}],
               "logprobs": want_conf}
    choice = post_json(base, "/chat/completions", payload)["choices"][0]
    text = choice["message"]["content"].strip()
    return (text, avg_logprob(choice)) if want_conf else (text, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--only", default="", help="comma-separated character names")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="lines per character (0 = all)")
    ap.add_argument("--game")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(ROOT / "build" / "subtitles-jp.csv")))
    by_pl = {}
    for row in rows:
        by_pl.setdefault(row["pl_id"], []).append(row)
    if a.only:
        want = {resolve_pl(x) for x in a.only.split(",")}
        by_pl = {pl: v for pl, v in by_pl.items() if pl in want}

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

    out_dir = ROOT / "data" / "per-character-jp"
    out_dir.mkdir(parents=True, exist_ok=True)

    for pl, plrows in sorted(by_pl.items()):
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {NAMES.get(pl, pl)} (done)", flush=True)
            continue
        if a.limit:
            plrows = plrows[:a.limit]

        with tempfile.TemporaryDirectory() as workdir:
            todo = []
            for row in plrows:
                jp_wem = row["jp_wem_id"]
                bank = bank_of.get(jp_wem)
                if not bank:
                    continue
                wav = pathlib.Path(workdir) / f"{jp_wem}.wav"
                try:
                    audio.wav(bank, jp_wem, wav)
                except Exception as err:
                    print(f"  {pl} {jp_wem}: {type(err).__name__}: {err}", flush=True)
                    continue
                todo.append((row, wav))

            def bake_line(row, wav):
                """Both tracks for one clip; wav deleted after."""
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

            lines = {}
            with ThreadPoolExecutor(max_workers=a.workers) as pool:
                futures = {pool.submit(bake_line, row, wav): row for row, wav in todo}
                for future in as_completed(futures):
                    row = futures[future]
                    try:
                        jp_text, jp_conf, en_text = future.result()
                    except Exception as err:
                        print(f"  {pl} {row['jp_wem_id']}: {type(err).__name__}: {err}",
                              flush=True)
                        continue
                    lines[row["jp_wem_id"]] = {
                        "label": row.get("label"), "en_wem_id": row.get("en_wem_id"),
                        "jp_real": jp_text, "jp_confidence": jp_conf,
                        "en_literal": en_text, "english": row.get("english"),
                    }
        out_file.write_text(json.dumps(
            {"pl_id": pl, "character": NAMES.get(pl, pl), "lines": lines},
            ensure_ascii=False, indent=1))
        print(f"{NAMES.get(pl, pl)}: {len(lines)} lines -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
