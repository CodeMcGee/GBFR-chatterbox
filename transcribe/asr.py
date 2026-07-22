"""Qwen3-ASR client. Its protocol differs from omni: the system message is a
plain context/bias string (vocabulary, not prose - prose suppresses short
barks, see EXPERIMENTS E5/E7), the user turn is audio only, and output is
"language <lang><asr_text><transcript>". No few-shot, no instructions."""
import base64
import json
import pathlib
import re
import urllib.request

from transcribe import NAMES, PKG


def asr(base, model, wav_path, ctx):
    """Transcribe one clip. Returns (text, avg_logprob_confidence)."""
    audio_b64 = base64.b64encode(pathlib.Path(wav_path).read_bytes()).decode()
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 128,
        "messages": [
            {"role": "system", "content": ctx},
            {"role": "user", "content": [
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}}]},
        ],
        "logprobs": True,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        choice = json.loads(resp.read())["choices"][0]
    text = choice["message"]["content"]
    tail = re.search(r"<asr_text>(.*)", text, re.S)
    text = (tail.group(1) if tail else text).strip()
    logprobs = [tok["logprob"]
                for tok in (choice.get("logprobs") or {}).get("content") or []]
    return text, (round(sum(logprobs) / len(logprobs), 3) if logprobs else None)


def _norm(s):
    """Case/punctuation-insensitive form for dedup."""
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


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
        if _norm(word) not in seen:
            seen.add(_norm(word)); kept.append(word.rstrip("!.?"))
    return ". ".join(kept) + "."
