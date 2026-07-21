#!/usr/bin/env python3
"""Classify voice lines into consensus groups and propose corrections.

Many lines are shared callouts every character speaks (elemental bursts,
"Ascension!", chat-wheel phrases). For those the majority transcript across
characters is ground truth and the minority are mishearings. Character-specific
lines (attacks, personal skills, directed banter) share a slot *name* but not
the words, so they are NOT consensus-eligible.

This tells them apart empirically: group by the character-independent slot,
then measure agreement inside each group. A group qualifies only if it spans
many characters AND they mostly agree. Then it lists the outliers a consensus
fix would change - it does not apply anything.

Usage: consensus.py [--atlas-dir data/per-character] [--min-chars N] [--agree F] [--apply]
"""
import argparse, json, pathlib, re
from collections import Counter, defaultdict


def norm(t):
    """Compare transcripts ignoring case and punctuation."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (t or "").lower())).strip()


def slot(label):
    """Character-independent slot key: drop the PLxxxx_vo_ prefix and any partner
    _PLxxxx suffix, so the same callout across characters lands in one group."""
    s = re.sub(r"^PL\d+_vo_", "", label or "")
    return re.sub(r"_PL\d{4}", "", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-chars", type=int, default=8,
                    help="a slot must span at least this many characters to vote")
    ap.add_argument("--agree", type=float, default=0.6,
                    help="dominant transcript must be at least this share of the group")
    ap.add_argument("--out", default="build/consensus.json")
    ap.add_argument("--exclude", default="^CMM",
                    help="skip slots matching this regex (default: emotes, which "
                         "deviate per character)")
    ap.add_argument("--atlas-dir", default="data/per-character",
                    help="per-character JSONs to read and (with --apply) rewrite")
    ap.add_argument("--apply", action="store_true",
                    help="write the corrections into the atlas JSONs (no review)")
    a = ap.parse_args()
    skip = re.compile(a.exclude)

    # Read the per-character JSONs directly (not the derived CSV), so the read and
    # write sides reference the same layer - no "rebuild the CSV first" ordering trap.
    NAMES = json.loads(pathlib.Path("chatterbox/characters.json").read_text())
    groups = defaultdict(list)
    for f in sorted(pathlib.Path(a.atlas_dir).glob("pl*.json")):
        pl = f.stem
        for wid, line in json.loads(f.read_text())["lines"].items():
            if (line.get("transcript") or "").strip():   # skip non-verbal / silent
                groups[slot(line.get("label", ""))].append({
                    "wem_id": wid, "pl_id": pl, "character": NAMES.get(pl, pl),
                    "label": line.get("label", ""), "transcript": line["transcript"],
                    "confidence": line.get("confidence")})

    eligible, corrections = [], []
    n_unique = n_divergent = 0
    for key, items in groups.items():
        chars = {r["pl_id"] for r in items}
        clusters = Counter(norm(r["transcript"]) for r in items)
        dom, domn = clusters.most_common(1)[0]
        agree = domn / len(items)
        if len(chars) < a.min_chars:
            n_unique += 1; continue                # too few speakers to be a shared line
        if agree < a.agree:
            n_divergent += 1; continue             # shared slot, different words = per-character
        if skip.search(key):                        # e.g. emotes: shared slot, but per-character words
            continue
        # representative surface form: commonest exact string in the winning cluster
        surf = Counter(r["transcript"] for r in items if norm(r["transcript"]) == dom)
        canonical = surf.most_common(1)[0][0]
        eligible.append({"slot": key, "characters": len(chars),
                         "agreement": round(agree, 2), "canonical": canonical,
                         "members": len(items)})
        for r in items:
            if norm(r["transcript"]) != dom:
                corrections.append({
                    "wem_id": r["wem_id"], "character": r["character"], "pl_id": r["pl_id"],
                    "label": r["label"], "slot": key,
                    "current": r["transcript"], "suggested": canonical,
                    "confidence": r["confidence"]})

    eligible.sort(key=lambda g: -g["members"])
    corrections.sort(key=lambda c: (c["slot"], c["character"]))
    out = pathlib.Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"eligible": eligible, "corrections": corrections}, indent=1))

    if a.apply:
        by_pl = defaultdict(list)
        for c in corrections:
            by_pl[c["pl_id"]].append(c)
        changed = 0
        for pl, cs in by_pl.items():
            p = pathlib.Path(a.atlas_dir) / f"{pl}.json"
            doc = json.loads(p.read_text())
            for c in cs:
                line = doc["lines"].get(c["wem_id"])
                if not line:
                    continue
                line["transcript"] = c["suggested"]
                line["confidence"] = None            # whisper's logprob no longer applies
                line["corrected"] = "consensus"       # traceable, and reversible from the source
                changed += 1
            p.write_text(json.dumps(doc, indent=1))
        print(f"[apply] rewrote {changed} transcript(s) across {len(by_pl)} character file(s)")
        print("       rebuild the published atlas: "
              "python dev/build_atlas.py data/per-character data/gbfr-voice-atlas")

    total = n_unique + n_divergent + len(eligible)
    print(f"slots: {total}  |  consensus-eligible: {len(eligible)}  "
          f"|  divergent (per-character): {n_divergent}  |  too-few-speakers: {n_unique}")
    print(f"lines a consensus fix would correct: {len(corrections)}")
    print(f"-> {out}")
    print("\ntop eligible slots:")
    for g in eligible[:12]:
        print(f"  {g['slot']:32} {g['characters']:>2} chars  agree {g['agreement']:.2f}  "
              f"{g['canonical']!r}")


if __name__ == "__main__":
    main()
