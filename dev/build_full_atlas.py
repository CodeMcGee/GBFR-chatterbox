#!/usr/bin/env python3
"""Build the complete Atlas: bank (resident) + pck (streamed) audio, fully transcribed.

Lines the game streams are only a ~0.4s prefetch head inside the .bnk; the whole
line lives in a .pck inside the packed archives. This merges both so every line
is measured and transcribed at full length.

Usage: build_full_atlas.py <bank_dir> <pck_dir> <old_atlas_dir> <out_dir> [--gpu N]
"""
import json, pathlib, re, struct, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from chatterbox.banks import MediaBank, decode_wav, label_of, wav_stats, wem_meta
from chatterbox.pck import Pck

NAMES = json.loads((pathlib.Path(__file__).resolve().parent.parent
                    / "chatterbox" / "characters.json").read_text())


def prompt_for(pl, label):
    """A short, line-specific hint for the recogniser.

    Without it, invented names come out wrong - "Rackam" became "Rack'em".
    Keep it short and specific: a long list of every character name measurably
    dilutes the effect and makes results worse.
    """
    speaker = NAMES.get(pl, "")
    bits = [f"Granblue Fantasy Relink. {speaker} speaking"] if speaker else ["Granblue Fantasy Relink"]
    m = re.search(r"_(PL\d{4})", label or "")
    if m:
        other = NAMES.get(m.group(1).lower())
        if other and other != speaker:
            bits.append(f"to {other}")
    return " ".join(bits) + "."


# Grunts, dodges and effort noises have no words in them, so the recogniser
# falls back on echoing its own initial_prompt: "Eustace speaking." Those lines
# are non-verbal, so the transcript belongs empty.
BLEED = re.compile(
    r"^(?:(?:Granblue )?Fantasy Relink\.\s*)?(?:\w+|Fantasy Relink) speaking(?: to \w+)?\.$")


def main():
    bank_dir, pck_dir, old_dir, out_dir = map(pathlib.Path, sys.argv[1:5])
    gpu = int(sys.argv[sys.argv.index("--gpu") + 1]) if "--gpu" in sys.argv else 0
    out_dir.mkdir(parents=True, exist_ok=True)

    from faster_whisper import WhisperModel
    model = WhisperModel("large-v3", device="cuda", device_index=gpu, compute_type="float16")

    for bank_path in sorted(bank_dir.glob("*_m.bnk")):
        pl = bank_path.name.split("_")[1]
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {pl}", flush=True); continue

        bank = MediaBank(bank_path)
        pck_path = pck_dir / bank_path.name.replace("_m.bnk", ".pck")
        pck = Pck(pck_path) if pck_path.exists() else None
        old = {}
        if (old_dir / f"{pl}.json").exists():
            old = json.loads((old_dir / f"{pl}.json").read_text())["lines"]

        with tempfile.TemporaryDirectory() as td:
            tdp = pathlib.Path(td)

            def prep(wid):
                bw = bank.wem(wid)
                declared, present, bps, rate, ch = wem_meta(bw)
                streamed = declared > present
                full = pck.wem(wid) if (streamed and pck and wid in pck) else bw
                recovered = streamed and full is not bw
                wav = tdp / f"{wid}.wav"
                try:
                    decode_wav(full, wav)
                    peak, dur = wav_stats(wav)
                except Exception:
                    return None
                return {
                    "wem_id": str(wid), "label": label_of(full) or label_of(bw),
                    "duration_s": round(dur, 3), "peak": round(peak, 4),
                    "sample_rate": rate, "channels": ch,
                    "source": "stream" if recovered else "bank",
                    "streamed": streamed, "recovered": recovered,
                    "prefetch_s": round(present / bps, 3) if (streamed and bps) else None,
                    "bytes": len(full),
                }

            with ThreadPoolExecutor(8) as ex:
                rows = [r for r in ex.map(prep, bank.entries) if r]

            todo = 0
            for r in rows:
                if r["peak"] == 0.0:
                    r["transcript"] = ""
                    continue
                segs, _ = model.transcribe(
                    str(tdp / f"{r['wem_id']}.wav"), language="en", beam_size=5,
                    condition_on_previous_text=False,
                    initial_prompt=prompt_for(pl, r.get("label") or ""))
                segs = list(segs)
                text = " ".join(s.text.strip() for s in segs).strip()
                r["transcript"] = "" if BLEED.match(text) else text
                # the recogniser's own confidence, so low-quality lines can be
                # filtered. No transcript means nothing was said, so a score
                # there would be dangling.
                r["confidence"] = (round(sum(s.avg_logprob for s in segs) / len(segs), 3)
                                   if segs and r["transcript"] else None)
                todo += 1

        rec = sum(1 for r in rows if r["recovered"])
        out_file.write_text(json.dumps({
            "pl_id": pl, "bank": bank_path.name,
            "pck": pck_path.name if pck else None,
            "lines": {r["wem_id"]: r for r in rows},
        }, indent=1))
        print(f"{pl}: {len(rows)} lines, {rec} recovered from stream, "
              f"{todo} transcribed -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
