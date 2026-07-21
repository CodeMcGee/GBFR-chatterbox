#!/usr/bin/env python3
"""Expand the atlas to every bank per character, preserving existing transcripts.

The first atlas covered only each character's largest bank. Characters have
several (co-op reactions, emotes, extra callouts); this adds the lines from the
others. Existing lines are kept as-is, including any hand corrections, and only
the new ones are transcribed. Every line gains a `bank` field.

Usage: expand_atlas.py <voice_dir> <pck_dir> <existing_atlas_dir> <out_dir> [--gpu N]
"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from build_full_atlas import BLEED, prompt_for

from chatterbox.banks import MediaBank, decode_wav, label_of, wav_stats, wem_meta
from chatterbox.pck import Pck


def main():
    voice_dir, pck_dir, old_dir, out_dir = map(pathlib.Path, sys.argv[1:5])
    gpu = int(sys.argv[sys.argv.index("--gpu") + 1]) if "--gpu" in sys.argv else 0
    out_dir.mkdir(parents=True, exist_ok=True)

    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cuda", device_index=gpu, compute_type="float16")

    banks_by_pl = {}
    for bp in sorted(voice_dir.glob("vo_pl*_m.bnk"), key=lambda p: p.stat().st_size, reverse=True):
        banks_by_pl.setdefault(bp.name[3:9], []).append(bp)   # largest first

    for pl, bank_paths in sorted(banks_by_pl.items()):
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {pl}", flush=True); continue
        old = {}
        if (old_dir / f"{pl}.json").exists():
            old = json.loads((old_dir / f"{pl}.json").read_text())["lines"]

        lines, new, dropped = {}, 0, 0
        for bank_path in bank_paths:
            bank = MediaBank(bank_path)
            pck_path = pck_dir / bank_path.name.replace("_m.bnk", ".pck")
            pck = Pck(pck_path) if pck_path.exists() else None
            for wid in bank.entries:
                sw = str(wid)
                if sw in lines:            # already taken from a larger bank
                    continue
                if sw in old:              # keep the existing line, just tag its bank
                    r = dict(old[sw]); r["bank"] = bank_path.name
                    lines[sw] = r
                    continue
                bw = bank.wem(wid)
                declared, present, bps, rate, ch = wem_meta(bw)
                streamed = declared > present
                full = pck.wem(wid) if (streamed and pck and wid in pck) else bw
                recovered = streamed and full is not bw
                with tempfile.TemporaryDirectory() as td:
                    wav = pathlib.Path(td) / "x.wav"
                    try:
                        decode_wav(full, wav)
                        peak, dur = wav_stats(wav)
                    except Exception as e:
                        print(f"  SKIP {pl} wem {sw}: {type(e).__name__}", flush=True)
                        dropped += 1
                        continue
                    r = {
                        "wem_id": sw, "label": label_of(full) or label_of(bw),
                        "duration_s": round(dur, 3), "peak": round(peak, 4),
                        "sample_rate": rate, "channels": ch,
                        "source": "stream" if recovered else "bank",
                        "streamed": streamed, "recovered": recovered,
                        "prefetch_s": round(present / bps, 3) if (streamed and bps) else None,
                        "bytes": len(full), "bank": bank_path.name,
                    }
                    if peak == 0.0:
                        r["transcript"], r["confidence"] = "", None
                    else:
                        segs, _ = model.transcribe(
                            str(wav), language="en", beam_size=5,
                            condition_on_previous_text=False,
                            initial_prompt=prompt_for(pl, r["label"] or ""))
                        segs = list(segs)
                        text = " ".join(s.text.strip() for s in segs).strip()
                        r["transcript"] = "" if BLEED.match(text) else text
                        r["confidence"] = (round(sum(s.avg_logprob for s in segs) / len(segs), 3)
                                           if segs and r["transcript"] else None)
                lines[sw] = r
                new += 1

        out_file.write_text(json.dumps(
            {"pl_id": pl, "banks": [b.name for b in bank_paths], "lines": lines}, indent=1))
        print(f"{pl}: {len(lines)} lines ({new} new, {dropped} undecodable) across "
              f"{len(bank_paths)} banks -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
