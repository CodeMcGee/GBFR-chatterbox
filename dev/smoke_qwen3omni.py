#!/usr/bin/env python3
"""Smoke-test an NVFP4 omni model against Whisper on a handful of clips.

Assumes a vllm OpenAI-compatible server is already up with the omni model
(see dev/serve_qwen3omni.sh). Sends each clip's audio + a transcription prompt,
prints the model's line beside the current Whisper transcript.

    python dev/smoke_qwen3omni.py [--base http://localhost:8000/v1] [--model qwen3-omni]
"""
import argparse, base64, glob, json, pathlib, sys, urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
WAVCACHE = HERE / ".wavcache"
PCK_DIRS = ["pck", "build/pck-all", "samples/pck"]


def full_wav(wem_id, pl, streamed):
    """Return a path to the FULL audio for a line. Streamed lines keep only a
    ~0.4s prefetch head in build/atlas; the rest lives in a .pck, so decode from
    there (cached) or the model hears a fragment and mis-transcribes."""
    bank_wav = ROOT / "build" / "atlas" / pl / f"{wem_id}.wav"
    if not streamed:
        return bank_wav
    WAVCACHE.mkdir(exist_ok=True)
    out = WAVCACHE / f"{wem_id}.wav"
    if out.exists():
        return out
    from chatterbox.pck import Pck
    from chatterbox.banks import decode_wav
    wid = int(wem_id)
    for d in PCK_DIRS:
        for p in glob.glob(str(ROOT / d / f"vo_{pl}*.pck")):
            pk = Pck(p)
            if wid in pk:
                decode_wav(pk.wem(wid), out)
                return out
    return bank_wav        # no pck locally: fall back to the stub, better than nothing


def build_prompt():
    """Compose the transcription prompt, seeding it with the domain glossary so
    shouted skill/SBA names and proper nouns are transcribed, not spelled out
    phonetically. See dev/glossary.json."""
    g = json.loads((HERE / "glossary.json").read_text())
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


CAT = {"ATK": "attack", "SP": "skill or co-op link", "DMG": "damage taken",
       "NAV": "battle callout", "DUO": "duo", "CMM": "emote or chat wheel",
       "MOV": "movement", "ETC": "misc", "PART": "party"}


def decode_label(label):
    """Turn an engine label into a readable line-type hint, e.g.
    PL2900_vo_SP_burst_reaction_B_PL0300 -> 'skill or co-op link: burst reaction b'."""
    import re
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


def _audio(wav_path):
    b = base64.b64encode(pathlib.Path(wav_path).read_bytes()).decode()
    return {"type": "input_audio", "input_audio": {"data": b, "format": "wav"}}


def transcribe(base, model, wav_path, ctx="", exemplars=()):
    # system: instructions + glossary. Then verified (audio -> transcript) pairs
    # for THIS character as few-shot turns, priming the model on their voice.
    messages = [{"role": "system", "content": PROMPT}]
    for ex_wav, ex_text in exemplars:
        messages.append({"role": "user", "content": [_audio(ex_wav)]})
        messages.append({"role": "assistant", "content": ex_text})
    target = [_audio(wav_path)]
    if ctx:
        target.insert(0, {"type": "text", "text": f"Context — this clip's line type is: {ctx}."})
    messages.append({"role": "user", "content": target})
    body = json.dumps({
        "model": model, "temperature": 0, "max_tokens": 128, "messages": messages,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body,
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--clips", default=str(HERE / "smoke_clips.json"),
                    help="JSON list of {wem_id}; other fields are pulled from the atlas")
    ap.add_argument("--atlas-dir", default=str(ROOT / "data/per-character"))
    ap.add_argument("--exemplars", default=str(HERE / "exemplars.json"),
                    help="per-character few-shot audio examples; '' to disable")
    a = ap.parse_args()
    from build_atlas import rows                 # the per-character JSONs (not the published CSV)
    atlas = {r["wem_id"]: r for r in rows(a.atlas_dir)}
    ex_map = json.loads(pathlib.Path(a.exemplars).read_text()) if a.exemplars else {}

    def exemplars_for(pl, skip_id):
        """Resolve this character's verified examples to (full_wav, transcript)
        pairs, skipping the target line itself."""
        out = []
        for e in ex_map.get(pl, []):
            if e["wem_id"] == skip_id:
                continue
            er = atlas.get(e["wem_id"], {})
            out.append((full_wav(e["wem_id"], pl, er.get("audio_source") == "stream"),
                        e["transcript"]))
        return out

    clips = json.loads(pathlib.Path(a.clips).read_text())
    agree = 0
    for c in clips:
        wid = c["wem_id"]
        r = atlas.get(wid, {})
        pl, streamed = r.get("pl_id", c.get("pl_id", "")), r.get("audio_source") == "stream"
        whisper = r.get("transcript", c.get("whisper", ""))
        try:
            wav = full_wav(wid, pl, streamed)
            got = transcribe(a.base, a.model, wav, decode_label(r.get("label", c.get("label", ""))),
                             exemplars_for(pl, wid))
        except Exception as e:
            print(f"[{wid}] ERROR: {e}", file=sys.stderr); continue
        same = got.lower().strip(".!? ") == whisper.lower().strip(".!? ")
        agree += same
        mark = "==" if same else "!="
        print(f"\n{r.get('character', c.get('character','?')):10} conf={r.get('confidence','?'):>6}"
              f"  {'[streamed:full]' if streamed else ''}")
        print(f"  whisper: {whisper!r}")
        print(f"  omni   : {got!r}  {mark}")
    print(f"\n{agree}/{len(clips)} agree with Whisper "
          f"(disagreements are the ones to eyeball).")


if __name__ == "__main__":
    main()
