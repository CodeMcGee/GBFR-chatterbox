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
    """The corrections map: {wem_id: human-verified transcript}."""
    return json.loads((PKG / "corrections.json").read_text())


def apply(atlas_dir=None):
    """Overlay every correction onto the per-character JSONs in atlas_dir,
    marking touched lines source_model=\"human\". Returns the number of
    corrections that matched no file (0 = all present)."""
    atlas_dir = pathlib.Path(atlas_dir) if atlas_dir else ATLAS_DIR
    fixes = load()
    left = dict(fixes)
    for path in sorted(atlas_dir.glob("pl*.json")):
        doc = json.loads(path.read_text())
        changed = False
        for wem_id, text in fixes.items():
            line = doc["lines"].get(wem_id)
            if line and wem_id in left:
                if line["transcript"] != text:
                    print(f"{path.stem} {wem_id}: {line['transcript']!r} -> {text!r}")
                    line["transcript"] = text
                    line["source_model"] = "human"
                    changed = True
                elif line.get("source_model") != "human":
                    line["source_model"] = "human"
                    changed = True
                left.pop(wem_id)
        if changed:
            path.write_text(json.dumps(doc, indent=1))
    for wem_id in left:
        print(f"UNMATCHED {wem_id} (not in any atlas file)")
    print(f"{len(fixes) - len(left)}/{len(fixes)} corrections present")
    return len(left)


def main(argv=None):
    """CLI: apply corrections; exit non-zero if any correction went unmatched."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("atlas_dir", nargs="?", default=None)
    a = ap.parse_args(argv)
    return 1 if apply(a.atlas_dir) else 0


if __name__ == "__main__":
    raise SystemExit(main())
