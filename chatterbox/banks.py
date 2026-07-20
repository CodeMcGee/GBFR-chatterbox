#!/usr/bin/env python3
"""GBFR-chatterbox core tool: inspect, extract, map, and mute voice lines
in Wwise media banks (BKHD+DIDX+DATA .bnk files).

Commands:
  list <bank>                      wem inventory with embedded labels
  extract <bank> <outdir> [ids..]  extract wems (all if no ids)
  map <bank> <out.json>            label+duration map (see dev/build_full_atlas.py
                                   for the transcribed dataset build)
  mute <bank> <out.bnk> <ids..>    alias ids to a silent wem (DIDX patch)
  verify <bank>                    structural chunk-walk check
"""
import argparse, hashlib, json, os, pathlib, struct, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

# Files that ship inside the package (silence.wem, ui.html, characters.json).
PKG_DIR = pathlib.Path(__file__).resolve().parent
# The app root, holding tools/ and atlas/. Under a frozen build that is the temp
# extraction dir, so bundled files must resolve through it, never through __file__.
BUNDLE_DIR = pathlib.Path(getattr(sys, "_MEIPASS", PKG_DIR.parent))


def tool(name):
    """A bundled helper binary, .exe-suffixed on Windows."""
    return BUNDLE_DIR / "tools" / (name + ".exe" if sys.platform == "win32" else name)


VGMSTREAM = tool("vgmstream-cli")


def atomic_write(path, data: bytes):
    """Write via a temp file in the same directory, then rename.

    Writing straight over a game file means an interrupted write (full disk,
    closed console, power loss) leaves a truncated bank with no original left.
    os.replace is atomic on NTFS and ext4.
    """
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".chatterbox-tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if tmp.stat().st_size != len(data):
            raise OSError(f"short write: {tmp.stat().st_size} of {len(data)} bytes")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def iter_chunks(buf, pos, end, pad=True):
    """Yield (tag, payload_offset, size) over a RIFF-style chunk run."""
    while pos + 8 <= end:
        tag = bytes(buf[pos:pos + 4])
        size = struct.unpack_from("<I", buf, pos + 4)[0]
        yield tag, pos + 8, size
        pos += 8 + size + ((size & 1) if pad else 0)


def read_chunks(data: bytes) -> dict:
    """Top-level bnk chunks (unpadded). Strict: no trailing bytes allowed."""
    chunks, pos = {}, 0
    for tag, off, size in iter_chunks(data, 0, len(data), pad=False):
        chunks[tag.decode("ascii", "replace")] = (off, size)
        pos = off + size
    if pos != len(data):
        raise ValueError(f"trailing bytes: chunk walk ended at {pos} of {len(data)}")
    return chunks


def wem_meta(w: bytes):
    """(declared_data_bytes, present_bytes, bytes_per_sec, sample_rate, channels).

    declared > present means the bank holds only a prefetch head and the rest of
    the line streams from a .pck inside the game archives.
    """
    bps = rate = ch = 0
    for tag, off, size in iter_chunks(w, 12, len(w)):
        if tag == b"fmt ":
            f = struct.unpack_from("<HHIIHH", w, off)
            ch, rate, bps = f[1], f[2], f[3]
        elif tag == b"data":
            return size, len(w) - off, bps, rate, ch
    return 0, 0, bps, rate, ch


def label_of(w: bytes):
    """The original developer filename, from the RIFF LIST-adtl labl chunk."""
    for tag, off, size in iter_chunks(w, 12, len(w)):
        if tag == b"LIST" and w[off:off + 4] == b"adtl":
            for stag, soff, ssize in iter_chunks(w, off + 4, off + size):
                if stag == b"labl":
                    return w[soff + 4:soff + ssize].split(b"\0")[0].decode("ascii", "replace")
    return None


class MediaBank:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.data = bytearray(self.path.read_bytes())
        self.chunks = read_chunks(self.data)
        if "DIDX" not in self.chunks or "DATA" not in self.chunks:
            raise ValueError(f"{path}: not a media bank (no DIDX/DATA) - use the _m.bnk")
        self.didx_off, didx_size = self.chunks["DIDX"]
        self.data_off = self.chunks["DATA"][0]
        didx = bytes(self.data[self.didx_off:self.didx_off + didx_size])
        self.entries = {wid: (off, ln) for wid, off, ln in struct.iter_unpack("<III", didx)}

    def wem(self, wid) -> bytes:
        off, ln = self.entries[wid]
        return bytes(self.data[self.data_off + off:self.data_off + off + ln])

    def label(self, wid):
        """Original dev filename from the RIFF LIST-adtl labl chunk, if present."""
        w = self.wem(wid)
        for tag, off, size in iter_chunks(w, 12, len(w)):
            if tag == b"LIST" and w[off:off + 4] == b"adtl":
                for stag, soff, ssize in iter_chunks(w, off + 4, off + size):
                    if stag == b"labl":
                        return w[soff + 4:soff + ssize].split(b"\x00")[0].decode("ascii", "replace")
        return None

    def inject(self, target_id, wem: bytes):
        """Overwrite a line's bytes in place and shrink its DIDX length.

        The slot belongs to this entry alone, so nothing else shifts - no rebuild.
        Unlike aliasing, this works in banks that contain no silent wem of their own,
        and the injected file is self-consistent (declares exactly what it holds).
        """
        off, ln = self.entries[target_id]
        if len(wem) > ln:
            raise ValueError(f"wem {target_id}: slot is {ln}B, need {len(wem)}B")
        # A swap points two entries at the same bytes. Overwriting them here would
        # silently corrupt the other line, which the user never touched.
        sharers = [w for w, (o, _) in self.entries.items() if o == off and w != target_id]
        if sharers:
            raise ValueError(
                f"wem {target_id} shares its audio with {sharers} (from an earlier swap); "
                f"revert this character before muting it")
        start = self.data_off + off
        self.data[start:start + len(wem)] = wem
        idx = list(self.entries).index(target_id)
        struct.pack_into("<I", self.data, self.didx_off + idx * 12 + 8, len(wem))
        self.entries[target_id] = (off, len(wem))

    def alias(self, target_id, source_id):
        """Point target's DIDX entry at source's bytes. 8-byte in-place patch."""
        src = self.entries[source_id]
        idx = list(self.entries).index(target_id)
        struct.pack_into("<II", self.data, self.didx_off + idx * 12 + 4, *src)
        self.entries[target_id] = src

    def is_stub(self, wid):
        """True if the bank holds only a prefetch head - the rest streams from a .pck."""
        declared, present, _, _, _ = wem_meta(self.wem(wid))
        return declared > present

    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()

    def write(self, path):
        # ponytail: raw dump; becomes DIDX/DATA re-serialization when cross-bank copy lands
        atomic_write(path, self.data)


def decode_wav(wem_bytes: bytes, out_wav: pathlib.Path):
    if not VGMSTREAM.exists():
        # In a release this file ships in the zip, so if it has gone missing the
        # overwhelmingly likely cause is an antivirus quarantine, not a bad download.
        raise ValueError(
            f"The audio decoder is missing, so previews cannot play.\n"
            f"Expected it at: {VGMSTREAM}\n"
            f"If you are using the packaged release, check your antivirus quarantine: "
            f"vgmstream is open source and sometimes flagged by mistake.\n"
            f"If you are running from source, download it from https://vgmstream.org")
    tmp = out_wav.with_suffix(".wem")
    tmp.write_bytes(wem_bytes)
    try:
        subprocess.run([VGMSTREAM, "-o", out_wav, tmp], check=True, capture_output=True)
    finally:
        tmp.unlink(missing_ok=True)


def wav_stats(path):
    """(peak, duration_s) from a wav without materializing sample lists."""
    d = pathlib.Path(path).read_bytes()
    fmt = None
    for tag, off, size in iter_chunks(d, 12, len(d)):
        if tag == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", d, off)
        elif tag == b"data":
            if not fmt:
                raise ValueError(f"{path}: data chunk before fmt")
            _, _, sr, _, block_align, bits = fmt
            if not block_align or not sr:
                raise ValueError(f"{path}: bad wav header (align={block_align} rate={sr})")
            dur = size // block_align / sr
            if bits == 32:  # vgmstream emits float32
                pk = max(map(abs, struct.unpack_from(f"<{size // 4}f", d, off)))
            else:
                pk = max(map(abs, struct.unpack_from(f"<{size // 2}h", d, off))) / 32768
            return pk, dur
    raise ValueError(f"{path}: no data chunk")


def cmd_list(args):
    b = MediaBank(args.bank)
    for wid, (_, ln) in b.entries.items():
        print(f"{wid:>12}  {ln:>9}  {b.label(wid) or '-'}")
    print(f"total: {len(b.entries)}")


def cmd_extract(args):
    b = MediaBank(args.bank)
    outdir = pathlib.Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    ids = [int(i) for i in args.ids] or list(b.entries)
    for wid in ids:
        (outdir / f"{wid}.wem").write_bytes(b.wem(wid))
    print(f"extracted {len(ids)} wems to {outdir}")


def cmd_map(args):
    b = MediaBank(args.bank)
    wavdir = pathlib.Path(args.out).with_suffix(""); wavdir.mkdir(parents=True, exist_ok=True)

    def one(wid):
        wav = wavdir / f"{wid}.wav"
        decode_wav(b.wem(wid), wav)
        pk, dur = wav_stats(wav)
        return wid, {"label": b.label(wid), "duration_s": round(dur, 3),
                     "peak": round(pk, 4), "transcript": None}

    with ThreadPoolExecutor(8) as ex:  # subprocess decode releases the GIL
        rows = dict(ex.map(one, b.entries))
    pathlib.Path(args.out).write_text(json.dumps(
        {"bank": str(args.bank), "sha256": hashlib.sha256(b.data).hexdigest(),
         "lines": rows}, indent=1))
    print(f"mapped {len(rows)} wems -> {args.out}")


# Muting injects this silent clip over a line's own bytes. It carries no lipsync
# marker, so a muted line moves no mouth. Injection beats the game's audio
# streaming (verified in-game): the engine plays the bank bytes even for lines
# whose full audio streams from the archives, so every line can be muted.
SILENCE = PKG_DIR / "silence.wem"


def replay(bank, entry):
    """Apply a saved profile entry to a bank. The one place edits are spelled out.

    The safety invariant the backup logic rests on: replaying a character's
    entry onto their pristine backup must reproduce the live bank exactly. If
    a new edit type is added it goes here, or reapply() stops recognising our
    own work and retires the user's original.
    """
    silence = SILENCE.read_bytes()
    for w in entry.get("mutes", []):
        bank.inject(int(w), silence)
    for tgt, src in entry.get("swaps", {}).items():
        bank.alias(int(tgt), int(src))


def cmd_mute(args):
    b = MediaBank(args.bank)
    silence = SILENCE.read_bytes()
    for wid in args.ids:
        b.inject(int(wid), silence)
    b.write(args.out)
    print(f"muted {len(args.ids)} lines (injected {len(silence)}B silence) -> {args.out}")


def cmd_verify(args):
    b = MediaBank(args.bank)
    dsize = b.chunks["DATA"][1]
    bad = [w for w, (o, ln) in b.entries.items()
           if o + ln > dsize or b.wem(w)[:4] != b"RIFF"]
    seen = {}
    overlap = []
    for w, (o, ln) in b.entries.items():
        if o in seen and seen[o] != ln:
            overlap.append((w, seen[o], ln))
        seen[o] = ln
    if overlap:
        print(f"  WARNING: {len(overlap)} entries share a slot with a different length "
              f"(a swap followed by an edit can corrupt these): {overlap[:3]}")
    print(f"chunks {list(b.chunks)}, {len(b.entries)} wems, "
          f"{'FAIL: ' + str(bad) if bad else 'all entries in bounds and RIFF-headed'}")
    return 1 if bad else 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("bank"); s.set_defaults(f=cmd_list)
    s = sub.add_parser("extract"); s.add_argument("bank"); s.add_argument("outdir")
    s.add_argument("ids", nargs="*"); s.set_defaults(f=cmd_extract)
    s = sub.add_parser("map"); s.add_argument("bank"); s.add_argument("out")
    s.add_argument("--compute", default="float16"); s.set_defaults(f=cmd_map)
    s = sub.add_parser("mute"); s.add_argument("bank"); s.add_argument("out")
    s.add_argument("ids", nargs="+"); s.set_defaults(f=cmd_mute)
    s = sub.add_parser("verify"); s.add_argument("bank"); s.set_defaults(f=cmd_verify)
    args = p.parse_args()
    try:
        sys.exit(args.f(args) or 0)
    except (ValueError, KeyError, struct.error, OSError) as e:
        sys.exit(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
