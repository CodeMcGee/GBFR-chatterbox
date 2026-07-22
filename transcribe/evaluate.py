"""Score a transcription source against the human-verified corpus (truth.json).

A source is any dir of per-character JSONs (data/per-character, build/atlas-*).
Reports accuracy on verified lines, hits on known-wrong text, and whether
confidence separates right from wrong. Confidence is a one-way signal: very
negative reliably flags wrongness, but near-zero does NOT clear a line (half
the human-caught errors scored better than -0.15) - sort by it, never trust it.
"""
import json
import pathlib
import sys

from transcribe import ATLAS_DIR, NAMES, PKG, norm


def truth():
    """The ground-truth corpus (verified + known-wrong lines)."""
    return json.loads((PKG / "truth.json").read_text())


def lines_of(src):
    """Flatten a dir of per-character JSONs into one {wem_id: line} map."""
    lines = {}
    for path in pathlib.Path(src).glob("pl*.json"):
        for wem_id, line in json.loads(path.read_text())["lines"].items():
            lines[wem_id] = line
    return lines


def score(src, corpus=None):
    """Print a report for one source dir. Returns the failure count:
    wrong verified lines + reproduced known-wrong texts."""
    corpus = corpus or truth()
    lines = lines_of(src)
    right, wrong, missing = [], [], []
    for wem_id, entry in corpus["verified"].items():
        line = lines.get(wem_id)
        if not line:
            missing.append(wem_id); continue
        bucket = right if norm(line.get("transcript")) == norm(entry["text"]) else wrong
        bucket.append((wem_id, line))
    known_wrong_hits = [
        wem_id for wem_id, entry in corpus["known_wrong"].items()
        if wem_id in lines and norm(lines[wem_id].get("transcript")) == norm(entry["not"])]

    n = len(right) + len(wrong)
    print(f"{src}: verified {len(right)}/{n} correct"
          + (f", {len(missing)} not in source" if missing else "")
          + f"; known-wrong text reproduced on {len(known_wrong_hits)}/{len(corpus['known_wrong'])}"
          + (f" ({', '.join(known_wrong_hits)})" if known_wrong_hits else ""))
    for wem_id, line in wrong:
        who = NAMES.get(corpus["verified"][wem_id].get("pl"), "?")
        print(f"  WRONG {wem_id} ({who}): {line.get('transcript')!r} "
              f"(conf {line.get('confidence')}) "
              f"!= {corpus['verified'][wem_id]['text']!r}")
    conf_right = [l.get("confidence") for _, l in right if l.get("confidence") is not None]
    conf_wrong = [l.get("confidence") for _, l in wrong if l.get("confidence") is not None]
    if conf_right and conf_wrong:
        avg_right = sum(conf_right) / len(conf_right)
        avg_wrong = sum(conf_wrong) / len(conf_wrong)
        print(f"  conf: correct avg {avg_right:.3f}, wrong avg {avg_wrong:.3f} -> "
              + ("separates" if avg_wrong < avg_right else "DOES NOT separate"))
    return len(wrong) + len(known_wrong_hits)


def main(argv=None):
    """CLI: score each given source dir (default: the live atlas)."""
    srcs = (argv if argv is not None else sys.argv[1:]) or [str(ATLAS_DIR)]
    return 1 if sum(score(s) for s in srcs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
