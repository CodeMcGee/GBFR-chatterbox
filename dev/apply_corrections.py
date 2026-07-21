#!/usr/bin/env python3
"""Overlay human-verified transcripts onto the per-character atlas.

corrections.json ({wem_id: text}) holds reviewer fixes the model gets wrong.
Run after every re-transcription, before build_atlas.py.

Usage: apply_corrections.py [atlas_dir]   (default data/per-character)
"""
import glob, json, pathlib, sys

HERE = pathlib.Path(__file__).resolve().parent


def main():
    atlas_dir = sys.argv[1] if len(sys.argv) > 1 else "data/per-character"
    fixes = json.loads((HERE / "corrections.json").read_text())
    left = dict(fixes)
    for f in sorted(glob.glob(f"{atlas_dir}/pl*.json")):
        p = pathlib.Path(f)
        doc = json.loads(p.read_text())
        hit = False
        for wid, text in fixes.items():
            r = doc["lines"].get(wid)
            if r and wid in left:
                if r["transcript"] != text:
                    print(f"{p.stem} {wid}: {r['transcript']!r} -> {text!r}")
                    r["transcript"] = text
                    r["source_model"] = "human"
                    hit = True
                left.pop(wid)
        if hit:
            p.write_text(json.dumps(doc, indent=1))
    for wid in left:
        print(f"UNMATCHED {wid} (not in any atlas file)")
    print(f"{len(fixes) - len(left)}/{len(fixes)} corrections present")


if __name__ == "__main__":
    main()
