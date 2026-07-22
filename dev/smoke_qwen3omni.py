#!/usr/bin/env python3
"""Smoke-test the omni server against Whisper-era transcripts on a handful of
clips. The transcription logic lives in the transcribe package; this script
re-exports the old names so older scratch scripts keep working.

    python dev/smoke_qwen3omni.py [--base http://localhost:8000/v1] [--model qwen3-omni]
"""
import argparse
import glob
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
from transcribe import PKG                                    # noqa: E402
from transcribe.context import GRUNT_PROMPT, decode_label     # noqa: E402,F401
from transcribe.omni import PROMPT, audio_part, transcribe    # noqa: E402,F401

_audio = audio_part                      # old name, kept for scratch scripts
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
    from chatterbox.banks import decode_wav
    from chatterbox.pck import Pck
    wid = int(wem_id)
    for d in PCK_DIRS:
        for p in glob.glob(str(ROOT / d / f"vo_{pl}*.pck")):
            pk = Pck(p)
            if wid in pk:
                decode_wav(pk.wem(wid), out)
                return out
    return bank_wav        # no pck locally: fall back to the stub, better than nothing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--clips", default=str(HERE / "smoke_clips.json"),
                    help="JSON list of {wem_id}; other fields are pulled from the atlas")
    ap.add_argument("--atlas-dir", default=str(ROOT / "data/per-character"))
    ap.add_argument("--exemplars", default=str(PKG / "exemplars.json"),
                    help="per-character few-shot audio examples; '' to disable")
    a = ap.parse_args()
    from build_atlas import rows  # the per-character JSONs (not the published CSV)
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
