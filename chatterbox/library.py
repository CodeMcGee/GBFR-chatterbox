"""Read side: a character's banks, atlas lines with live state, full-line
previews, and the streamed .pck packages."""
import json
import pathlib
import struct
import tempfile

from chatterbox.banks import SILENCE, MediaBank, atomic_write, decode_wav
from chatterbox.game import APP_DIR, NAMES, battle_banks, check_pl
from chatterbox.patching import backup_path
from chatterbox.pck import Pck
from chatterbox.siero import DataArchive


def pad_wav(wav: bytes, ms: int = 250) -> bytes:
    """Append silence. Browsers drop the tail of very short clips; the padding
    absorbs it."""
    pos, fmt = 12, None
    while pos + 8 <= len(wav):
        tag, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        if tag == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", wav, pos + 8)
        elif tag == b"data" and fmt:
            nbytes = (fmt[2] * ms // 1000) * fmt[4]      # rate * ms * block_align
            out = bytearray(wav[:pos + 8 + size] + b"\0" * nbytes + wav[pos + 8 + size:])
            struct.pack_into("<I", out, pos + 4, size + nbytes)      # data chunk size
            struct.pack_into("<I", out, 4, len(out) - 8)             # RIFF size
            return bytes(out)
        pos += 8 + size + (size & 1)
    return wav


class Library:
    """Banks, pcks and atlas for one game install, cached lazily."""

    def __init__(self, voice_dir, atlas_dir, store):
        self.voice_dir = voice_dir
        self.atlas_dir = pathlib.Path(atlas_dir)
        self.store = store
        self.banks = {}   # pl -> [MediaBank], largest first
        self.pcks = {}    # pck filename -> Pck or None
        self._wanted = None   # pck names present in the archive for our banks

    @property
    def game_index(self):
        return self.voice_dir.parent.parent.parent / "data.i"

    def characters(self):
        out = []
        for f in sorted(self.atlas_dir.glob("pl*.json")):
            pl = f.stem
            if battle_banks(self.voice_dir, pl):
                out.append({"pl": pl, "name": NAMES.get(pl, pl)})
        return out

    def banks_for(self, pl):
        """Every bank for a character, as MediaBanks, cached and largest first."""
        pl = check_pl(pl)
        if pl not in self.banks:
            paths = battle_banks(self.voice_dir, pl)
            if not paths:
                raise ValueError(f"no bank for {pl}")
            self.banks[pl] = [MediaBank(p) for p in paths]
        return self.banks[pl]

    def invalidate(self, pl):
        """Drop a character's cached banks; next read re-parses from disk."""
        self.banks.pop(pl, None)

    def bank_paths(self, pl):
        """A character's bank file paths, largest first, WITHOUT parsing them.
        Reverting must not choke on a corrupt live bank it is about to overwrite."""
        return battle_banks(self.voice_dir, check_pl(pl))

    def bank_of(self, pl, wem):
        """The bank that holds this wem, largest first. Ids can appear in more
        than one bank with identical bytes; the atlas tags each to the largest
        too, so this and the atlas agree."""
        for b in self.banks_for(pl):
            if wem in b.entries:
                return b
        return None

    def lines(self, pl):
        """Every line for a character, across all its banks, with current state."""
        pl = check_pl(pl)
        atlas = json.loads((self.atlas_dir / f"{pl}.json").read_text())["lines"]
        silence = SILENCE.read_bytes()
        flags = self.store.flags()
        banks = self.banks_for(pl)
        # per bank: its pristine entries, and a (offset,len) -> wem reverse map,
        # so muted/swapped state is read from the bytes with no side-car to desync.
        orig, byloc = {}, {}
        for b in banks:
            bk = backup_path(b.path)
            o = MediaBank(bk).entries if bk.exists() else {}
            orig[b.path.name] = o
            byloc[b.path.name] = {v: k for k, v in o.items()}
        out = []
        for wid, r in atlas.items():
            w = int(wid)
            b = self.bank_of(pl, w)
            if not b:
                continue
            o = orig[b.path.name]
            cur, was = b.entries[w], o.get(w)
            muted = cur[1] == len(silence) and b.wem(w) == silence
            swapped_from = None
            if not muted and was is not None and cur != was:
                src = byloc[b.path.name].get(cur)
                swapped_from = str(src) if src is not None else "?"
            pk = self.pck_for(b)
            out.append({
                "wem_id": wid,
                "label": r.get("label") or "",
                "category": (r.get("label") or "_").split("_vo_")[-1].split("_")[0],
                "transcript": r.get("transcript") or "",
                "confidence": r.get("confidence"),
                "flagged": bool((flags.get(wid) or {}).get("wrong")),
                "correction": (flags.get(wid) or {}).get("correct") or "",
                "duration": r.get("duration_s"),
                "muted": muted,
                "swapped_from": swapped_from,
                # Streamed lines keep only a ~0.4s prefetch head in the bank; the
                # rest lives in a .pck. Without that .pck we can only preview the
                # head, so tell the UI rather than looking broken.
                "streamed": bool(r.get("streamed")),
                "preview_full": not r.get("streamed") or bool(pk and w in pk),
                "prefetch_s": r.get("prefetch_s"),
                # which bank (version) the line lives in, so swaps stay intra-bank
                "bank": b.path.name,
            })
        main = banks[0]
        backup = backup_path(main.path)
        return {"lines": out, "bank": main.path.name,
                "bank_path": str(main.path), "backup_path": str(backup),
                "backup_exists": backup.exists()}

    def wanted_pcks(self):
        """The .pck names present in the archive for our banks, resolved once.
        Caching the index lookup avoids re-reading data.i on every status poll."""
        if self._wanted is None:
            self._wanted = set()
            if self.game_index.exists():
                names = [b.name.replace("_m.bnk", ".pck")
                         for c in self.characters()
                         for b in battle_banks(self.voice_dir, c["pl"])]
                with DataArchive(self.game_index) as ar:
                    self._wanted = {n for n in names if "sound/english(us)/" + n in ar}
        return self._wanted

    def pck_status(self):
        """How many of the streamed voice packages are available locally."""
        want = self.wanted_pcks()
        have = sum(1 for n in want if (APP_DIR / "pck" / n).exists())
        return {
            "have": have,
            "total": len(want),
            "extractor": self.game_index.exists(),
            "game_root": str(self.voice_dir.parent.parent.parent),
        }

    def extract_pcks(self):
        """Pull the streamed voice packages out of the user's OWN game archives.
        Nothing is downloaded and nothing leaves the machine."""
        index = self.game_index
        if not index.exists():
            raise ValueError(f"game index not found at {index}")
        dest = APP_DIR / "pck"; dest.mkdir(exist_ok=True)
        want = {n for n in self.wanted_pcks() if not (dest / n).exists()}
        if not want:
            return {"ok": True, "extracted": 0, "note": "already complete"}
        print(f"[extract] pulling {len(want)} voice packages from {index}", flush=True)
        n = 0
        with DataArchive(index) as ar:
            for name in sorted(want):
                # atomic_write so an interrupted extract cannot leave a truncated
                # .pck that later reads as corrupt audio
                atomic_write(dest / name, ar.read("sound/english(us)/" + name))
                n += 1
        self.pcks.clear()
        print(f"[extract] {n} packages -> {dest}", flush=True)
        return {"ok": True, "extracted": n, "dest": str(dest)}

    def pck_for(self, bank):
        """The Pck streaming this bank's audio, if available locally."""
        name = bank.path.name.replace("_m.bnk", ".pck")
        if name not in self.pcks:
            self.pcks[name] = None
            for d in (APP_DIR / "pck", self.voice_dir):
                if (d / name).exists():
                    self.pcks[name] = Pck(d / name)
                    break
        return self.pcks[name]

    def wav(self, pl, wid):
        """Preview the WHOLE line: streamed lines are only a prefetch head in
        the bank, so take those from the .pck when we have it."""
        w = int(wid)
        b = self.bank_of(check_pl(pl), w)
        if not b:
            raise ValueError(f"{wid} is not one of {pl}'s lines")
        data = b.wem(w)
        if b.is_stub(w):
            pk = self.pck_for(b)
            if pk and w in pk:
                data = pk.wem(w)
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td) / "p.wav"
            decode_wav(data, out)
            return pad_wav(out.read_bytes())
