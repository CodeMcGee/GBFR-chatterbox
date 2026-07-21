#!/usr/bin/env python3
"""Re-transcribe the whole atlas through a local qwen3-omni server.

Decodes every line's FULL audio from the game install (resident lines from the
.bnk, streamed lines from the .pck / data.i) and transcribes it with the omni
harness (glossary + per-character verified exemplars). Writes one JSON per
character, skipping any already done, so it is resumable. Rebuild the published
atlas afterwards with build_atlas.py.

Usage: retranscribe.py [--game <path>] [--base URL] [--out build/atlas-omni]
                       [--only plXXXX,plYYYY] [--no-exemplars]
"""
import argparse, json, pathlib, re, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(HERE))
import serve
from chatterbox.banks import MediaBank, decode_wav, atomic_write
from chatterbox.pck import Pck
from chatterbox.siero import DataArchive
from smoke_qwen3omni import transcribe, decode_label

RACES = json.loads((HERE / "races.json").read_text()) if (HERE / "races.json").exists() else {}

# NPC ally ids that appear as label suffixes (verified from their transcripts)
NPC = {"NP0000": "Lyria", "NP0300": "Rolan"}


def build_ctx(pl, label):
    """Per-line context: every hint the label carries, and nothing else.
    Speaker, addressee (when the label names one), line type. The 'If wordless'
    grunt tail is dropped - it verifiably pushes worded lines into grunts."""
    ctx = f"This line is spoken by {serve.NAMES.get(pl, pl)}."
    m = re.search(r"_((PL|NP)\d{4})$", label or "")
    if m:
        name = NPC.get(m.group(1)) or serve.NAMES.get(m.group(1).lower())
        if pl == "pl2900":
            ctx += race_hint(pl, label)  # Fediel names allies by race, never by name
        elif name:
            ctx += f" It is directed at their ally {name}."
    lt = decode_label(label).split(". If wordless")[0]
    if lt:
        ctx += f" Line type: {lt}."
    return ctx


def race_hint(pl, label):
    """Fediel (pl2900) is a primal beast who names allies by race and gender, not
    by name. Her partner-directed lines encode the ally in the label, so nudge the
    model toward the ally's race and gender - just enough to pick "lass" over "bash"."""
    if pl != "pl2900":
        return ""
    m = re.search(r"_PL(\d{4})$", label or "")
    pr = RACES.get("pl" + m.group(1)) if m else None
    if not pr or pr.get("race") == "Other":
        return ""
    return f" The ally is a {pr['gender']} {pr['race']}."

PCK_DIRS = ["pck", "build/pck-all"]


class Audio:
    """Decode full line audio from the game, caching banks and pcks."""
    def __init__(self, voice_dir):
        self.voice_dir = voice_dir
        self.banks, self.pcks = {}, {}
        self.index = voice_dir.parent.parent.parent / "data.i"
        self.archive = DataArchive(self.index) if self.index.exists() else None

    def bank(self, name):
        if name not in self.banks:
            self.banks[name] = MediaBank(self.voice_dir / name)
        return self.banks[name]

    def pck(self, bank_name):
        pname = bank_name.replace("_m.bnk", ".pck")
        if pname not in self.pcks:
            self.pcks[pname] = None
            for d in PCK_DIRS:
                if (ROOT / d / pname).exists():
                    self.pcks[pname] = Pck(ROOT / d / pname); break
            else:                                   # not extracted locally: pull from data.i
                key = "sound/english(us)/" + pname
                if self.archive and key in self.archive:
                    tmp = ROOT / "build" / "pck-all" / pname
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write(tmp, self.archive.read(key))   # interrupt-safe, as extract_pcks does
                    self.pcks[pname] = Pck(tmp)
        return self.pcks[pname]

    def wav(self, bank_name, wem_id, out):
        b = self.bank(bank_name); wid = int(wem_id)
        data = b.wem(wid)
        if b.is_stub(wid):
            pk = self.pck(bank_name)
            if pk and wid in pk:
                data = pk.wem(wid)
        decode_wav(data, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game")
    ap.add_argument("--base", default="http://127.0.0.1:8210/v1")
    ap.add_argument("--model", default="qwen3-omni")
    ap.add_argument("--atlas-dir", default="data/per-character")
    ap.add_argument("--out", default="build/atlas-omni")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-exemplars", action="store_true")
    a = ap.parse_args()

    voice = pathlib.Path(serve.find_game(a.game))
    audio = Audio(voice)
    out_dir = ROOT / a.out; out_dir.mkdir(parents=True, exist_ok=True)
    ex_map = {} if a.no_exemplars else json.loads((HERE / "exemplars.json").read_text())

    atlas_dir = ROOT / a.atlas_dir
    pls = sorted(p.stem for p in atlas_dir.glob("pl*.json"))
    if a.only:
        pls = [p for p in pls if p in a.only.split(",")]

    for pl in pls:
        out_file = out_dir / f"{pl}.json"
        if out_file.exists():
            print(f"SKIP {pl} (done)", flush=True); continue
        doc = json.loads((atlas_dir / f"{pl}.json").read_text())
        lines = doc["lines"]

        # per-character exemplars: decode each once, reuse for every line
        exemplars = []
        with tempfile.TemporaryDirectory() as td:
            for e in ex_map.get(pl, []):
                er = lines.get(e["wem_id"])
                if not er:
                    continue
                ew = pathlib.Path(td) / f"ex_{e['wem_id']}.wav"
                try:
                    audio.wav(er["bank"], e["wem_id"], ew)
                    exemplars.append((str(ew), e["transcript"]))
                except Exception:
                    pass

            new = 0
            for wid, r in lines.items():
                if not r.get("bank"):
                    continue
                wav = pathlib.Path(td) / "t.wav"
                try:
                    audio.wav(r["bank"], wid, wav)
                    ex = [e for e in exemplars if not e[0].endswith(f"ex_{wid}.wav")]
                    got, conf = transcribe(a.base, a.model, wav,
                                           build_ctx(pl, r.get("label", "")), ex,
                                           with_conf=True)
                except Exception as e:
                    print(f"  {pl} {wid}: {type(e).__name__}: {e}", flush=True)
                    continue
                r["transcript"] = got
                r["confidence"] = conf
                r["source_model"] = "qwen3-omni"
                new += 1
            done = new
        out_file.write_text(json.dumps(doc, indent=1))
        print(f"{pl}: {done} lines -> {out_file}", flush=True)


if __name__ == "__main__":
    main()
