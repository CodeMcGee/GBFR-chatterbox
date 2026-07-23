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


def decode_label(label, grunt_hint=True):
    """Turn an engine label into a readable line-type hint, e.g.
    PL2900_vo_SP_burst_reaction_B_PL0300 -> 'skill or co-op link: burst reaction b'.
    grunt_hint appends the offensive/defensive wordless-clip disambiguation;
    build_ctx wants the bare hint (the tail verifiably pushes worded lines
    into grunts)."""
    stripped = re.sub(r"^PL\d+_vo_", "", label or "")
    parts = stripped.split("_")
    if not parts:
        return ""
    cat = CAT.get(parts[0], parts[0].lower())
    rest = re.sub(r"pl\d{4}", "", " ".join(parts[1:]), flags=re.I)
    rest = " ".join(rest.split()).lower()
    base = f"{cat}: {rest}".strip(": ").strip()
    if not grunt_hint:
        return base
    if parts[0] in ("ATK", "SP"):
        base += (". If wordless, this is an OFFENSIVE attacking effort — an "
                 "aggressive battle-cry or exertion (e.g. Hyah!, Rrah!, Tah!), "
                 "not a pained sound")
    elif parts[0] == "DMG":
        base += (". If wordless, this is a DEFENSIVE reaction to taking a hit — "
                 "a pained grunt or gasp (e.g. Ugh!, Gah!, Nngh!), not an attack cry")
    return base


def ally_name(label):
    """The character a partner-directed line addresses, by name - labels end
    in an engine tag like _PL0300 or _NP0300, sometimes with a take number
    after it (_PL0600_3); nothing past this function should ever see one.
    None when the line addresses nobody."""
    tag = re.search(r"_((PL|NP)\d{4})(?:_\d+)?$", label or "")
    if not tag:
        return None
    return NPC.get(tag.group(1)) or NAMES.get(tag.group(1).lower())


def build_ctx(pl, label):
    """Per-line context: speaker, addressee (when the label names one), line
    type. The 'If wordless' grunt tail is dropped - it verifiably pushes worded
    lines into grunts."""
    speaker = NAMES.get(pl, pl)
    ctx = f"This line is spoken by {speaker}."
    ally = ally_name(label)
    if speaker == "Fediel":
        ctx += race_hint(ally)
    elif ally:
        ctx += f" It is directed at their ally {ally}."
    line_type = decode_label(label, grunt_hint=False)
    if line_type:
        ctx += f" Line type: {line_type}."
    return ctx


def race_hint(ally):
    """Fediel is a primal beast who names allies by race and gender, never by
    name. Nudge the model toward the ally's race and gender - just enough to
    pick "lass" over "bash"."""
    profile = RACES.get(ally) if ally else None
    if not profile or profile.get("race") == "Other":
        return ""
    return f" The ally is a {profile['gender']} {profile['race']}."
