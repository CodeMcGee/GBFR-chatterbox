"""Overlay human-verified transcripts onto the per-character atlas.

corrections.json ({wem_id: text}) holds reviewer fixes the models get wrong.
Run after every re-transcription, before build_atlas. Corrected lines are
marked source_model="human" so no automated pass may overwrite them - two
verified fixes regressed in past rebakes before this armor existed.
"""
import json
import pathlib

from transcribe import ATLAS_DIR, PKG


def load():
    return json.loads((PKG / "corrections.json").read_text())


def apply(atlas_dir=None):
    atlas_dir = pathlib.Path(atlas_dir) if atlas_dir else ATLAS_DIR
    fixes = load()
    left = dict(fixes)
    for p in sorted(atlas_dir.glob("pl*.json")):
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
                elif r.get("source_model") != "human":
                    r["source_model"] = "human"
                    hit = True
                left.pop(wid)
        if hit:
            p.write_text(json.dumps(doc, indent=1))
    for wid in left:
        print(f"UNMATCHED {wid} (not in any atlas file)")
    print(f"{len(fixes) - len(left)}/{len(fixes)} corrections present")
    return len(left)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atlas_dir", nargs="?", default=None)
    a = ap.parse_args(argv)
    return 1 if apply(a.atlas_dir) else 0


if __name__ == "__main__":
    raise SystemExit(main())
