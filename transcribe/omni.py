"""Qwen3-Omni client: glossary-seeded prompt, per-character audio exemplars as
few-shot turns, avg-logprob confidence. Serve the model with
dev/serve_qwen3omni_awq.sh."""
import base64
import json
import pathlib
import urllib.request

from transcribe import PKG


def audio_part(wav_path):
    b = base64.b64encode(pathlib.Path(wav_path).read_bytes()).decode()
    return {"type": "input_audio", "input_audio": {"data": b, "format": "wav"}}


def build_prompt():
    """Compose the transcription prompt, seeding it with the domain glossary so
    shouted skill/SBA names and proper nouns are transcribed, not spelled out
    phonetically. See transcribe/glossary.json."""
    g = json.loads((PKG / "glossary.json").read_text())
    chars = sorted(g["characters"])
    moves = sorted({m for c in g["characters"].values()
                    for m in c["skills"] + ([c["sba"]] if "sba" in c else [])})
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
        "World terms: " + ", ".join(g["world_terms"]))


PROMPT = build_prompt()


def transcribe(base, model, wav_path, ctx="", exemplars=(), system=None, temperature=0,
               with_conf=False):
    # system: instructions + glossary. Then verified (audio -> transcript) pairs
    # for THIS character as few-shot turns, priming the model on their voice.
    messages = [{"role": "system", "content": system or PROMPT}]
    for ex_wav, ex_text in exemplars:
        messages.append({"role": "user", "content": [audio_part(ex_wav)]})
        messages.append({"role": "assistant", "content": ex_text})
    target = [audio_part(wav_path)]
    if ctx:
        target.insert(0, {"type": "text", "text": f"Context: {ctx}"})
    messages.append({"role": "user", "content": target})
    body = json.dumps({
        "model": model, "temperature": temperature, "max_tokens": 128, "messages": messages,
        "logprobs": with_conf,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        ch = json.loads(r.read())["choices"][0]
    text = ch["message"]["content"].strip()
    if not with_conf:
        return text
    # avg token logprob, same idea as Whisper's avg_logprob but on qwen's own
    # scale: correct lines sit near 0, garbage near -0.2 and below.
    lps = [t["logprob"] for t in (ch.get("logprobs") or {}).get("content") or []]
    return text, (round(sum(lps) / len(lps), 3) if lps else None)
