"""Score a transcription source against the human-verified corpus (truth.json).

A source is any dir of per-character JSONs (data/per-character, build/atlas-*).
Reports accuracy on verified lines, hits on known-wrong text, and whether
confidence separates right from wrong. Confidence is a one-way signal: very
negative reliably flags wrongness, but near-zero does NOT clear a line (half
the human-caught errors scored better than -0.15) - sort by it, never trust it.
"""
import json
import pathlib
import re
import sys

from transcribe import ATLAS_DIR, NAMES, PKG


def norm(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def truth():
    return json.loads((PKG / "truth.json").read_text())


def lines_of(src):
    out = {}
    for p in pathlib.Path(src).glob("pl*.json"):
        for wid, r in json.loads(p.read_text())["lines"].items():
            out[wid] = r
    return out


def score(src, corpus=None):
    corpus = corpus or truth()
    lines = lines_of(src)
    right, wrong, missing = [], [], []
    for wid, t in corpus["verified"].items():
        r = lines.get(wid)
        if not r:
            missing.append(wid); continue
        (right if norm(r.get("transcript")) == norm(t["text"]) else wrong).append((wid, r))
    kw_hits = [wid for wid, t in corpus["known_wrong"].items()
               if wid in lines and norm(lines[wid].get("transcript")) == norm(t["not"])]

    n = len(right) + len(wrong)
    print(f"{src}: verified {len(right)}/{n} correct"
          + (f", {len(missing)} not in source" if missing else "")
          + f"; known-wrong text reproduced on {len(kw_hits)}/{len(corpus['known_wrong'])}"
          + (f" ({', '.join(kw_hits)})" if kw_hits else ""))
    for wid, r in wrong:
        print(f"  WRONG {wid} ({NAMES.get(corpus['verified'][wid].get('pl'), '?')}): {r.get('transcript')!r} (conf {r.get('confidence')}) "
              f"!= {corpus['verified'][wid]['text']!r}")
    cr = [r.get("confidence") for _, r in right if r.get("confidence") is not None]
    cw = [r.get("confidence") for _, r in wrong if r.get("confidence") is not None]
    if cr and cw:
        mr, mw = sum(cr) / len(cr), sum(cw) / len(cw)
        print(f"  conf: correct avg {mr:.3f}, wrong avg {mw:.3f} -> "
              + ("separates" if mw < mr else "DOES NOT separate"))
    return len(wrong) + len(kw_hits)


def main(argv=None):
    srcs = (argv if argv is not None else sys.argv[1:]) or [str(ATLAS_DIR)]
    return 1 if sum(score(s) for s in srcs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
