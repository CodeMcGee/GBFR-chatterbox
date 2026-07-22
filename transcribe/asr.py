"""Qwen3-ASR client. Its protocol differs from omni: the system message is a
plain context/bias string (vocabulary, not prose - prose suppresses short
barks, see EXPERIMENTS E5/E7), the user turn is audio only, and output is
"language <lang><asr_text><transcript>". No few-shot, no instructions."""
import json
import re

from transcribe import NAMES, PKG, avg_logprob, norm, post_json
from transcribe.omni import audio_part


def asr(base, model, wav_path, ctx):
    """Transcribe one clip. Returns (text, avg_logprob_confidence)."""
    payload = {
        "model": model, "temperature": 0, "max_tokens": 128,
        "messages": [
            {"role": "system", "content": ctx},
            {"role": "user", "content": [audio_part(wav_path)]},
        ],
        "logprobs": True,
    }
    choice = post_json(base, "/chat/completions", payload)["choices"][0]
    text = choice["message"]["content"]
    tail = re.search(r"<asr_text>(.*)", text, re.S)
    text = (tail.group(1) if tail else text).strip()
    return text, avg_logprob(choice)


def hotwords(pl, short=False):
    """Bias vocabulary for a character: glossary skills/SBA + truth-verified
    catchphrases, plus ally names unless short=True. A long context (or any
    conversational phrase in it) suppresses short barks - the lean list is for
    bark rescue."""
    glossary = json.loads((PKG / "glossary.json").read_text())
    name = NAMES.get(pl, pl)
    entry = glossary["characters"].get(name, {})
    words = list(entry.get("skills", [])) + ([entry["sba"]] if "sba" in entry else [])
    if not short:
        words += sorted(glossary["characters"])        # ally names, for call lines
    truth = json.loads((PKG / "truth.json").read_text())
    words += [line["text"] for line in truth["verified"].values() if line["pl"] == pl]
    seen, kept = set(), []
    for word in words:
        if norm(word) not in seen:
            seen.add(norm(word))
            kept.append(word.rstrip("!.?"))
    return ". ".join(kept) + "."
