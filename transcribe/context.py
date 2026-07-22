"""Per-line context for the transcriber: speaker, addressee, line type.

Every hint the engine label carries, and nothing else. Character personas were
tried and retired - see EXPERIMENTS.md E1-E3: a persona only fixes a bark when
it quotes the exact phrase, and quoting the exact phrase injects it into other
clips. Any literal string in the prompt is an injection vector.
"""
import json
import re

from transcribe import NAMES, PKG

RACES = json.loads((PKG / "races.json").read_text()) if (PKG / "races.json").exists() else {}

# NPC ally ids that appear as label suffixes (verified from their transcripts)
NPC = {"NP0000": "Lyria", "NP0300": "Rolan"}

CAT = {"ATK": "attack", "SP": "skill or co-op link", "DMG": "damage taken",
       "NAV": "battle callout", "DUO": "duo", "CMM": "emote or chat wheel",
       "MOV": "movement", "ETC": "misc", "PART": "party"}

# Grunts need no glossary and no line-type context - the full prompt dilutes the
# phonetic focus and the model collapses to "Hah!". A lean phonetic-only system
# prompt (pass as `system=`) restores Whisper-level variety.
GRUNT_PROMPT = (
    "Transcribe the single short wordless vocalization in this clip as a phonetic "
    "interjection - spell it the way it SOUNDS, matching the exact vowel and "
    "consonants (e.g. Hah!, Tch!, Hmph!, Ugh!, Gah!, Nngh!, Hyah!, Zah!, Huh?!). "
    "Every grunt sounds different; do not reuse the same spelling. Output ONLY the "
    "interjection - never a description, never asterisks, never words.")


def decode_label(label):
    """Turn an engine label into a readable line-type hint, e.g.
    PL2900_vo_SP_burst_reaction_B_PL0300 -> 'skill or co-op link: burst reaction b'."""
    s = re.sub(r"^PL\d+_vo_", "", label or "")
    parts = s.split("_")
    if not parts:
        return ""
    cat = CAT.get(parts[0], parts[0].lower())
    rest = re.sub(r"pl\d{4}", "", " ".join(parts[1:]), flags=re.I)
    rest = " ".join(rest.split()).lower()
    base = f"{cat}: {rest}".strip(": ").strip()
    if parts[0] in ("ATK", "SP"):
        base += (". If wordless, this is an OFFENSIVE attacking effort — an "
                 "aggressive battle-cry or exertion (e.g. Hyah!, Rrah!, Tah!), "
                 "not a pained sound")
    elif parts[0] == "DMG":
        base += (". If wordless, this is a DEFENSIVE reaction to taking a hit — "
                 "a pained grunt or gasp (e.g. Ugh!, Gah!, Nngh!), not an attack cry")
    return base


def build_ctx(pl, label):
    """Per-line context: speaker, addressee (when the label names one), line
    type. The 'If wordless' grunt tail is dropped - it verifiably pushes worded
    lines into grunts."""
    ctx = f"This line is spoken by {NAMES.get(pl, pl)}."
    m = re.search(r"_((PL|NP)\d{4})$", label or "")
    if m:
        name = NPC.get(m.group(1)) or NAMES.get(m.group(1).lower())
        if pl == "pl2900":
            ctx += race_hint(pl, label)  # Fediel names allies by race, never by name
        elif name:
            ctx += f" It is directed at their ally {name}."
    lt = decode_label(label).split(". If wordless")[0]
    if lt:
        ctx += f" Line type: {lt}."
    return ctx


def race_hint(pl, label):
    """Fediel (pl2900) is a primal beast who names allies by race and gender, not
    by name. Her partner-directed lines encode the ally in the label, so nudge the
    model toward the ally's race and gender - just enough to pick "lass" over "bash"."""
    if pl != "pl2900":
        return ""
    m = re.search(r"_PL(\d{4})$", label or "")
    pr = RACES.get("pl" + m.group(1)) if m else None
    if not pr or pr.get("race") == "Other":
        return ""
    return f" The ally is a {pr['gender']} {pr['race']}."
