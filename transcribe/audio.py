"""Decode full line audio from the game install: resident lines from the
.bnk, streamed lines from a .pck (extracted locally or pulled from data.i)."""
import pathlib

from chatterbox.banks import MediaBank, atomic_write, decode_wav
from chatterbox.pck import Pck
from chatterbox.siero import DataArchive

from transcribe import ROOT

PCK_DIRS = ["pck", "build/pck-all"]


class Audio:
    """Decode full line audio from the game, caching banks and pcks."""

    def __init__(self, voice_dir):
        """voice_dir: the game's english voice bank directory (from find_game)."""
        self.voice_dir = pathlib.Path(voice_dir)
        self.banks, self.pcks = {}, {}
        self.index = self.voice_dir.parent.parent.parent / "data.i"
        self.archive = DataArchive(self.index) if self.index.exists() else None

    def bank(self, name):
        """Open (and cache) a .bnk by filename."""
        if name not in self.banks:
            self.banks[name] = MediaBank(self.voice_dir / name)
        return self.banks[name]

    def pck(self, bank_name):
        """The .pck holding a bank's streamed audio: local extract if present,
        else pulled once from data.i. None if neither exists."""
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
                    atomic_write(tmp, self.archive.read(key))   # interrupt-safe
                    self.pcks[pname] = Pck(tmp)
        return self.pcks[pname]

    def wav(self, bank_name, wem_id, out):
        """Decode one line's FULL audio to `out` - resident bytes from the bank,
        or the streamed original when the bank only holds a prefetch stub."""
        b = self.bank(bank_name); wid = int(wem_id)
        data = b.wem(wid)
        if b.is_stub(wid):
            pk = self.pck(bank_name)
            if pk and wid in pk:
                data = pk.wem(wid)
        decode_wav(data, out)
