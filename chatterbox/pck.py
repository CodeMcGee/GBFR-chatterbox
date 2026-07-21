#!/usr/bin/env python3
"""Wwise AKPK (.pck) reader - the streamed half of GBFR voice audio.

The game keeps long/non-latency-critical lines as a small prefetch head in the
.bnk plus the full stream in a .pck inside the packed archives. This reads the
.pck so those lines can be recovered whole.

Usage: python -m chatterbox.pck <file.pck> [outdir]     # list, or extract all wems
"""
import pathlib
import struct
import sys


def read_lut(d, pos, declared=None):
    count = struct.unpack_from("<I", d, pos)[0]
    end = pos + 4 + count * 20
    if end > len(d) or (declared is not None and count * 20 + 4 > declared + 4):
        raise ValueError(f"lookup table overruns the file ({count} entries)")
    pos += 4
    out = {}
    for _ in range(count):
        fid, block, size, start, lang = struct.unpack_from("<IIIII", d, pos)
        pos += 20
        off = start * block
        if off + size > len(d):
            raise ValueError(f"entry {fid} points past the end of the package")
        out[fid] = (off, size, lang)
    return out, pos


class Pck:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.data = self.path.read_bytes()
        if self.data[:4] != b"AKPK":
            raise ValueError(f"{path}: not an AKPK package")
        hdr_size, ver, langsz, bnksz, stmsz = struct.unpack_from("<IIIII", self.data, 4)
        if ver != 1:
            # the LUT entry layout is version-specific; guessing would silently
            # produce plausible garbage rather than an error
            raise ValueError(f"{path}: unsupported AKPK version {ver}")
        pos = 12 + 16 + langsz          # magic+hdrsize+version, 4 LUT sizes, language map
        self.banks, pos = read_lut(self.data, pos, bnksz)
        self.streams, pos = read_lut(self.data, pos, stmsz)

    def wem(self, wem_id) -> bytes:
        off, size, _ = self.streams[wem_id]
        return self.data[off:off + size]

    def __contains__(self, wem_id):
        return wem_id in self.streams

    def __len__(self):
        return len(self.streams)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    p = Pck(sys.argv[1])
    if len(sys.argv) == 2:
        for fid, (off, size, _lang) in sorted(p.streams.items()):
            print(f"{fid:>12}  {size:>9}  @{off}")
        print(f"total: {len(p)} streamed wems ({len(p.banks)} banks)")
        return
    out = pathlib.Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
    for fid in p.streams:
        (out / f"{fid}.wem").write_bytes(p.wem(fid))
    print(f"extracted {len(p)} wems -> {out}")


if __name__ == "__main__":
    main()
