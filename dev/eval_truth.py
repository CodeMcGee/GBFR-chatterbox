#!/usr/bin/env python3
"""Score a transcription source against the human-verified corpus (truth.json).

A source is any dir of per-character JSONs (data/per-character, build/atlas-*).
Reports accuracy on verified lines, hits on known-wrong text, and whether
confidence separates right from wrong (it should be more negative when wrong).

Usage: eval_truth.py [dir ...]     (default: data/per-character)
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TRUTH = json.loads((HERE / "truth.json").read_text())


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def lines_of(src):
    out = {}
    for p in pathlib.Path(src).glob("pl*.json"):
        for wid, r in json.loads(p.read_text())["lines"].items():
            out[wid] = r
    return out


def score(src):
    lines = lines_of(src)
    right, wrong, missing = [], [], []
    for wid, t in TRUTH["verified"].items():
        r = lines.get(wid)
        if not r:
            missing.append(wid); continue
        (right if norm(r.get("transcript")) == norm(t["text"]) else wrong).append((wid, r))
    kw_hits = [wid for wid, t in TRUTH["known_wrong"].items()
               if wid in lines and norm(lines[wid].get("transcript")) == norm(t["not"])]

    n = len(right) + len(wrong)
    print(f"{src}: verified {len(right)}/{n} correct"
          + (f", {len(missing)} not in source" if missing else "")
          + f"; known-wrong text reproduced on {len(kw_hits)}/{len(TRUTH['known_wrong'])}"
          + (f" ({', '.join(kw_hits)})" if kw_hits else ""))
    for wid, r in wrong:
        print(f"  WRONG {wid}: {r.get('transcript')!r} (conf {r.get('confidence')}) "
              f"!= {TRUTH['verified'][wid]['text']!r}")
    cr = [r.get("confidence") for _, r in right if r.get("confidence") is not None]
    cw = [r.get("confidence") for _, r in wrong if r.get("confidence") is not None]
    if cr and cw:
        mr, mw = sum(cr) / len(cr), sum(cw) / len(cw)
        print(f"  conf: correct avg {mr:.3f}, wrong avg {mw:.3f} -> "
              + ("separates" if mw < mr else "DOES NOT separate"))
    return len(wrong) + len(kw_hits)


if __name__ == "__main__":
    srcs = sys.argv[1:] or [str(ROOT / "data/per-character")]
    sys.exit(1 if sum(score(s) for s in srcs) else 0)
