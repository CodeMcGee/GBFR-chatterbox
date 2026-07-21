#!/usr/bin/env python3
"""Build a Japanese-audio -> English-subtitle table.

At runtime a Wwise hook sees which Japanese wem is playing. This table maps that
JP wem id to the English transcript to show and how long to show it. The join key
is the label, which is identical across languages (the wem ids are not). JP line
durations come straight from the bank headers - no audio decoding needed.

Re-run after promoting a new atlas so the English text stays in sync.

Usage: build_subtitles.py [--game <path>] [--atlas-dir data/per-character]
                          [--out build/subtitles-jp.csv]
"""
import argparse, csv, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import serve
from build_atlas import rows          # the per-character JSONs, with derived columns
from chatterbox.banks import MediaBank, wem_meta, label_of

FIELDS = ["jp_wem_id", "label", "pl_id", "character", "category",
          "partner", "english", "jp_duration_s", "en_wem_id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--atlas-dir", default="data/per-character")
    ap.add_argument("--out", default="build/subtitles-jp.csv")
    a = ap.parse_args()

    # English side, keyed by the language-independent label (source of truth: the JSONs)
    en = {r["label"]: r for r in rows(ROOT / a.atlas_dir) if r["label"]}

    # Japanese banks live beside the English ones under the same install
    voice = pathlib.Path(serve.find_game(a.game))
    jp_dir = voice.parent / "Japanese"
    if not jp_dir.is_dir():
        sys.exit(f"No Japanese voice dir at {jp_dir}")

    records, seen, unmatched = [], set(), 0
    for bank in sorted(jp_dir.glob("vo_pl*_m.bnk"), key=lambda p: p.stat().st_size, reverse=True):
        mb = MediaBank(bank)
        for wid in mb.entries:
            if wid in seen:                 # a wem can sit in more than one bank; first wins
                continue
            seen.add(wid)
            data = mb.wem(wid)
            label = label_of(data)
            e = en.get(label)
            if not e:                       # JP line with no English match (or unlabelled)
                unmatched += 1
                continue
            declared, present, bps, rate, ch = wem_meta(data)
            dur = round(declared / bps, 3) if bps else None   # full JP length from the header
            records.append({
                "jp_wem_id": wid, "label": label,
                "pl_id": e["pl_id"], "character": e["character"], "category": e["category"],
                "partner": e.get("group", "").partition("_PL")[2],   # "" when no partner
                "english": e["transcript"], "jp_duration_s": dur, "en_wem_id": e["wem_id"],
            })

    records.sort(key=lambda r: (r["pl_id"], r["label"]))
    out = ROOT / a.out; out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS); w.writeheader(); w.writerows(records)
    spoken = sum(1 for r in records if r["english"])
    print(f"{len(records)} JP lines mapped to English ({spoken} with text), "
          f"{unmatched} JP lines had no English match")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
