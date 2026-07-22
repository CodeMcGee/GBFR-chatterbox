"""Forced-choice rescoring: compare candidate transcripts by the likelihood
the omni model assigns them given the audio (vllm prompt_logprobs on a forced
assistant answer). Nothing is generated, so nothing anchors or hallucinates.

Measured in EXPERIMENTS E9: sound mechanism, but the local 30B ear prefers its
own errors on ~2/3 of human-corrected lines at any quantization - use it to
rank candidates, not to overrule a human.
"""
from transcribe import post_json
from transcribe.omni import PROMPT, audio_part


def ntokens(base, model, text):
    """Token count of `text` per the server's /tokenize endpoint."""
    return len(post_json(base.replace("/v1", ""), "/tokenize",
                         {"model": model, "prompt": text})["tokens"])


def score(base, model, wav, ctx, candidate):
    """(sum, avg) logprob of candidate's tokens as the forced answer."""
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": [{"type": "text", "text": f"Context: {ctx}"},
                                             audio_part(wav)]},
                {"role": "assistant", "content": candidate}]
    response = post_json(base, "/chat/completions", {
        "model": model, "messages": messages, "max_tokens": 1, "temperature": 0,
        "prompt_logprobs": 0, "add_generation_prompt": False,
        "continue_final_message": True})
    prompt_logprobs = (response.get("prompt_logprobs")
                       or response["choices"][0].get("prompt_logprobs"))
    token_logprobs = [list(token.values())[0]["logprob"]
                      for token in prompt_logprobs if token]
    candidate_logprobs = token_logprobs[-ntokens(base, model, candidate):]
    return sum(candidate_logprobs), sum(candidate_logprobs) / len(candidate_logprobs)


def pick(base, model, wav, ctx, candidates):
    """Return (candidate, total_logprob, per_token_logprob) tuples sorted
    best-first by per-token likelihood."""
    scored = [(candidate, *score(base, model, wav, ctx, candidate))
              for candidate in candidates]
    per_token = 2                            # index of per_token_logprob above
    return sorted(scored, key=lambda entry: entry[per_token], reverse=True)
