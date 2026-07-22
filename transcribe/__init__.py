"""Transcription pipeline: bake the voice atlas from game audio, merge model
opinions, apply human corrections, and score everything against the
ground-truth corpus. The dataset side of the project; the chatterbox package
is the app side.

Entry point: python -m transcribe <bake|ensemble|corrections|eval|rescore>
Method and measured results: EXPERIMENTS.md at the repo root.
"""
import pathlib

PKG = pathlib.Path(__file__).resolve().parent
ROOT = PKG.parent
ATLAS_DIR = ROOT / "data" / "per-character"

from chatterbox.game import NAMES, find_game  # noqa: E402,F401

# Humans remember names, not engine ids: every CLI boundary accepts either.
_BY_NAME = {v.lower(): k for k, v in NAMES.items()}


def resolve_pl(s):
    """'Seofon' or 'pl2200' -> 'pl2200'. Raises on unknown."""
    s = s.strip()
    pl = _BY_NAME.get(s.lower(), s.lower())
    if pl not in NAMES:
        raise SystemExit(f"unknown character {s!r} (try one of: "
                         + ", ".join(sorted(NAMES.values())) + ")")
    return pl
