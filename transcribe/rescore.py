"""Forced-choice rescoring: compare candidate transcripts by the likelihood
the omni model assigns them given the audio (vllm prompt_logprobs on a forced
assistant answer). Nothing is generated, so nothing anchors or hallucinates.

Measured in EXPERIMENTS E9: sound mechanism, but the local 30B ear prefers its
own errors on ~2/3 of human-corrected lines at any quantization - use it to
rank candidates, not to overrule a human.
"""
import json
import urllib.request

from transcribe.omni import PROMPT, audio_part


def _post(base, path, payload):
    req = urllib.request.Request(f"{base}{path}", json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def ntokens(base, model, text):
    return len(_post(base.replace("/v1", ""), "/tokenize",
                     {"model": model, "prompt": text})["tokens"])


def score(base, model, wav, ctx, candidate):
    """(sum, avg) logprob of candidate's tokens as the forced answer."""
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": [{"type": "text", "text": f"Context: {ctx}"},
                                             audio_part(wav)]},
                {"role": "assistant", "content": candidate}]
    out = _post(base, "/chat/completions", {
        "model": model, "messages": messages, "max_tokens": 1, "temperature": 0,
        "prompt_logprobs": 0, "add_generation_prompt": False,
        "continue_final_message": True})
    plp = out.get("prompt_logprobs") or out["choices"][0].get("prompt_logprobs")
    vals = [list(t.values())[0]["logprob"] for t in plp if t]
    n = ntokens(base, model, candidate)
    tail = vals[-n:]
    return sum(tail), sum(tail) / len(tail)


def pick(base, model, wav, ctx, candidates):
    """Return candidates sorted best-first by per-token likelihood."""
    scored = [(c, *score(base, model, wav, ctx, c)) for c in candidates]
    return sorted(scored, key=lambda t: -t[2])
