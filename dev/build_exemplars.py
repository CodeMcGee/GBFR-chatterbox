#!/usr/bin/env python3
"""Build per-character few-shot exemplars from consensus-verified lines.

For each character, pick a couple of lines we KNOW are transcribed right - ones
whose transcript matches the cross-character majority for a shared callout slot
(see consensus.py). Those become audio->transcript examples that prime an omni
model on that character's voice before it transcribes an unknown line.

Usage: build_exemplars.py [atlas.csv] [--per 2] [--out dev/exemplars.json]
"""
import argparse, csv, json, pathlib, re
from collections import Counter, defaultdict


def norm(t):        # compare transcripts ignoring case and punctuation (as in consensus.py)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (t or "").lower())).strip()


def slot(label):    # character-independent slot key: drop PLxxxx_vo_ prefix and _PLxxxx partner
    return re.sub(r"_PL\d{4}", "", re.sub(r"^PL\d+_vo_", "", label or ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?", default="data/gbfr-voice-atlas.csv")
    ap.add_argument("--per", type=int, default=2, help="exemplars per character")
    ap.add_argument("--min-chars", type=int, default=8)
    ap.add_argument("--agree", type=float, default=0.6)
    ap.add_argument("--exclude", default="^CMM", help="slots to skip (emotes deviate)")
    ap.add_argument("--out", default="dev/exemplars.json")
    a = ap.parse_args()
    skip = re.compile(a.exclude)

    rows = list(csv.DictReader(open(a.csv)))
    groups = defaultdict(list)
    for r in rows:
        if (r["transcript"] or "").strip():
            groups[slot(r["label"])].append(r)

    # the verified spelling for each broadly-shared, high-agreement slot
    canon = {}
    for key, items in groups.items():
        if skip.search(key):
            continue
        if len({r["pl_id"] for r in items}) < a.min_chars:
            continue
        clusters = Counter(norm(r["transcript"]) for r in items)
        dom, domn = clusters.most_common(1)[0]
        if domn / len(items) < a.agree:
            continue
        surf = Counter(r["transcript"] for r in items if norm(r["transcript"]) == dom)
        canon[key] = (dom, surf.most_common(1)[0][0])

    # per character: lines that already match their slot's verified majority
    verified = defaultdict(list)
    for r in rows:
        c = canon.get(slot(r["label"]))
        if c and norm(r["transcript"]) == c[0]:
            verified[r["pl_id"]].append({
                "wem_id": r["wem_id"], "transcript": c[1], "slot": slot(r["label"]),
                "streamed": r["audio_source"] == "stream",
                "dur": float(r["duration_s"] or 9)})

    # prefer non-streamed (no pck decode) then shortest, and keep slots distinct
    out = {}
    for pl, cands in verified.items():
        cands.sort(key=lambda x: (x["streamed"], x["dur"]))
        picked, seen = [], set()
        for c in cands:
            if c["slot"] in seen:
                continue
            picked.append({"wem_id": c["wem_id"], "transcript": c["transcript"]})
            seen.add(c["slot"])
            if len(picked) == a.per:
                break
        out[pl] = picked

    dest = pathlib.Path(a.out)
    dest.write_text(json.dumps(out, indent=1))
    thin = [pl for pl, v in out.items() if len(v) < a.per]
    print(f"exemplars for {len(out)} characters -> {dest}")
    print(f"fewer than {a.per}: {thin or 'none'}")


if __name__ == "__main__":
    main()
