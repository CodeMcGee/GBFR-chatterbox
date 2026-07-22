"""Transcription pipeline: bake the voice atlas from game audio, merge model
opinions, apply human corrections, and score everything against the
ground-truth corpus. The dataset side of the project; the chatterbox package
is the app side.

Entry point: python -m transcribe <bake|ensemble|corrections|eval|rescore>
Method and measured results: EXPERIMENTS.md at the repo root.
"""
import json
import pathlib
import re
import urllib.request

PKG = pathlib.Path(__file__).resolve().parent
ROOT = PKG.parent
ATLAS_DIR = ROOT / "data" / "per-character"

from chatterbox.game import NAMES, find_game  # noqa: E402,F401

# Humans remember names, not engine ids: every CLI boundary accepts either.
_BY_NAME = {v.lower(): k for k, v in NAMES.items()}


def resolve_pl(name_or_pl):
    """'Seofon' or 'pl2200' -> 'pl2200'. Raises on unknown."""
    wanted = name_or_pl.strip().lower()
    pl = _BY_NAME.get(wanted, wanted)
    if pl not in NAMES:
        raise SystemExit(f"unknown character {name_or_pl!r} (try one of: "
                         + ", ".join(sorted(NAMES.values())) + ")")
    return pl


def norm(text):
    """Case/punctuation-insensitive form: the one definition of "these two
    transcripts are the same" shared by the ensemble merge, dedup, and the
    truth-corpus scoring."""
    return re.sub(r"[^a-z0-9 ]", "", (text or "").lower()).strip()


def post_json(base, path, payload, timeout=120):
    """POST json to a model server, return the parsed response."""
    request = urllib.request.Request(f"{base}{path}", json.dumps(payload).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def avg_logprob(choice):
    """Average token logprob of a chat completion choice - the pipeline's
    confidence score (near 0 = confident, more negative = likelier wrong).
    None when the server returned no logprobs."""
    logprobs = [token["logprob"]
                for token in (choice.get("logprobs") or {}).get("content") or []]
    return round(sum(logprobs) / len(logprobs), 3) if logprobs else None
