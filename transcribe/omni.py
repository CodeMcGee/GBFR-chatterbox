"""Qwen3-Omni client: glossary-seeded prompt, per-character audio exemplars as
few-shot turns, avg-logprob confidence. Serve the model with
dev/serve_qwen3omni_awq.sh."""
import base64
import json
import pathlib

from transcribe import PKG, avg_logprob, post_json


def audio_part(wav_path):
    """A chat-completions input_audio content part for a wav file."""
    audio_b64 = base64.b64encode(pathlib.Path(wav_path).read_bytes()).decode()
    return {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}}


def build_prompt():
    """Compose the transcription prompt, seeding it with the domain glossary so
    shouted skill/SBA names and proper nouns are transcribed, not spelled out
    phonetically. See transcribe/glossary.json."""
    glossary = json.loads((PKG / "glossary.json").read_text())
    chars = sorted(glossary["characters"])
    moves = sorted({move for entry in glossary["characters"].values()
                    for move in entry["skills"] + ([entry["sba"]] if "sba" in entry else [])})
    return (
        "You are transcribing short English combat voice lines from the game "
        "Granblue Fantasy: Relink. Lines are often shouted in combat and under "
        "two seconds.\n\n"
        "Rules:\n"
        "- Transcribe the spoken English exactly. When you hear a name below, use "
        "its exact spelling.\n"
        "- If the clip is a wordless grunt, yell, or effort noise, spell it "
        "phonetically the way it SOUNDS - matching the exact vowel and consonants "
        "(e.g. Hah!, Tch!, Hmph!, Ugh!, Gah!, Nngh!, Hyah!, Zah!, Huh?!). Every "
        "grunt sounds different; do not reuse the same spelling. Never describe "
        "the sound in words, and never use asterisks.\n"
        "- Output ONLY the transcription: no quotes, no speaker label, no notes.\n\n"
        "Characters: " + ", ".join(chars) + "\n"
        "Skills & Skybound Arts: " + ", ".join(moves) + "\n"
        "World terms: " + ", ".join(glossary["world_terms"]))


PROMPT = build_prompt()


def transcribe(base, model, wav_path, ctx="", exemplars=(), system=None, temperature=0,
               with_conf=False):
    """Transcribe one clip. exemplars are (wav_path, transcript) few-shot
    pairs - each wav given as a path or a pre-built audio content part.
    Returns text, or (text, avg_logprob_confidence) with with_conf."""
    # system: instructions + glossary. Then verified (audio -> transcript) pairs
    # for THIS character as few-shot turns, priming the model on their voice.
    messages = [{"role": "system", "content": system or PROMPT}]
    for ex_audio, ex_text in exemplars:
        # a pre-built content part (dict) or a wav path; callers in hot loops
        # pre-build so exemplar audio is base64-encoded once, not per line
        part = ex_audio if isinstance(ex_audio, dict) else audio_part(ex_audio)
        messages.append({"role": "user", "content": [part]})
        messages.append({"role": "assistant", "content": ex_text})
    target = [audio_part(wav_path)]
    if ctx:
        target.insert(0, {"type": "text", "text": f"Context: {ctx}"})
    messages.append({"role": "user", "content": target})
    payload = {"model": model, "temperature": temperature, "max_tokens": 128,
               "messages": messages, "logprobs": with_conf}
    choice = post_json(base, "/chat/completions", payload)["choices"][0]
    text = choice["message"]["content"].strip()
    if not with_conf:
        return text
    return text, avg_logprob(choice)
