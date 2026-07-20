#!/usr/bin/env python3
"""Siero: fetches any file out of the game's archives, by name.

Named for the Knickknack Shack owner, who has everything and produces it
on request. Reads the data.i index and the data.N archives beside it.

The game keeps its assets in a dozen data.N blobs. data.i is a FlatBuffers
table listing, for every file, which chunk holds it, where in that chunk it
starts, and how long it is. Files are found by hash, never by name: the index
stores a sorted array of XXHash64 values over the lowercased path.

Chunks are stored raw when compression did not pay (already-compressed audio
usually) and LZ4 block compressed otherwise.

    python -m chatterbox.siero <data.i> exists <path> [path...]
    python -m chatterbox.siero <data.i> extract <outdir> <path> [path...]

Paths use forward slashes and are matched case-insensitively, e.g.
    sound/english(us)/vo_pl2700_02_00_00.pck
"""
import pathlib, struct, sys

# ---------------------------------------------------------------- XXHash64

P1 = 0x9E3779B185EBCA87
P2 = 0xC2B2AE3D27D4EB4F
P3 = 0x165667B19E3779F9
P4 = 0x85EBCA77C2B2AE63
P5 = 0x27D4EB2F165667C5
M = 0xFFFFFFFFFFFFFFFF


def _rol(x, r):
    return ((x << r) | (x >> (64 - r))) & M


def _round(acc, val):
    return (_rol((acc + val * P2) & M, 31) * P1) & M


def _merge(acc, val):
    return ((acc ^ _round(0, val)) * P1 + P4) & M


def xxh64(data: bytes, seed: int = 0) -> int:
    """Standard XXHash64. The index hashes lowercased forward-slashed paths."""
    n = len(data)
    if n >= 32:
        v1, v2 = (seed + P1 + P2) & M, (seed + P2) & M
        v3, v4 = seed, (seed - P1) & M
        pos = 0
        while pos <= n - 32:
            a, b, c, d = struct.unpack_from("<QQQQ", data, pos)
            v1, v2, v3, v4 = _round(v1, a), _round(v2, b), _round(v3, c), _round(v4, d)
            pos += 32
        h = (_rol(v1, 1) + _rol(v2, 7) + _rol(v3, 12) + _rol(v4, 18)) & M
        for v in (v1, v2, v3, v4):
            h = _merge(h, v)
    else:
        h = (seed + P5) & M
        pos = 0
    h = (h + n) & M
    while pos <= n - 8:
        h = (_rol(h ^ _round(0, struct.unpack_from("<Q", data, pos)[0]), 27) * P1 + P4) & M
        pos += 8
    if pos <= n - 4:
        h = (_rol(h ^ ((struct.unpack_from("<I", data, pos)[0] * P1) & M), 23) * P2 + P3) & M
        pos += 4
    while pos < n:
        h = (_rol(h ^ ((data[pos] * P5) & M), 11) * P1) & M
        pos += 1
    h ^= h >> 33
    h = (h * P2) & M
    h ^= h >> 29
    h = (h * P3) & M
    h ^= h >> 32
    return h


# ---------------------------------------------------------------- LZ4 block

def lz4_decompress(src: bytes, out_len: int) -> bytearray:
    """LZ4 block format. No frame header: the index already knows both sizes."""
    dst = bytearray(out_len)
    s, d, n = 0, 0, len(src)
    while s < n:
        token = src[s]; s += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[s]; s += 1
                lit += b
                if b != 255:
                    break
        if lit:
            # Bounds-check before the slice: assigning a short source to a
            # bytearray slice RESIZES it, so a truncated block would quietly
            # shrink the output and still satisfy a naive length check.
            if s + lit > n:
                raise ValueError("lz4: literal run extends past the end of the block")
            if d + lit > out_len:
                raise ValueError("lz4: literals overflow the declared output size")
            dst[d:d + lit] = src[s:s + lit]
            s += lit; d += lit
        if s >= n:
            break
        off = src[s] | (src[s + 1] << 8); s += 2
        if off == 0:
            raise ValueError("lz4: zero match offset")
        mlen = token & 0xF
        if mlen == 15:
            while True:
                b = src[s]; s += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        p = d - off
        if p < 0:
            raise ValueError("lz4: match before start of block")
        if d + mlen > out_len:
            raise ValueError("lz4: match overflows the declared output size")
        if off >= mlen:                      # non-overlapping, copy in one go
            dst[d:d + mlen] = dst[p:p + mlen]
            d += mlen
        else:                                # overlapping run, byte at a time
            for _ in range(mlen):
                dst[d] = dst[p]
                d += 1; p += 1
    # len(dst), not d: the counter can disagree with the buffer if a slice
    # assignment ever resized it, and the buffer is what the caller gets.
    if d != out_len or len(dst) != out_len:
        raise ValueError(f"lz4: produced {len(dst)} bytes, expected {out_len}")
    return dst


# ---------------------------------------------------------------- FlatBuffers

class _Table:
    """Just enough FlatBuffers to read one known root table."""

    def __init__(self, buf, pos):
        self.buf, self.pos = buf, pos
        self.vtable = pos - struct.unpack_from("<i", buf, pos)[0]
        self.vtable_size = struct.unpack_from("<H", buf, self.vtable)[0]

    def _field(self, fid):
        off = 4 + fid * 2
        if off >= self.vtable_size:
            return 0
        return struct.unpack_from("<H", self.buf, self.vtable + off)[0]

    def scalar(self, fid, fmt, default=0):
        o = self._field(fid)
        return struct.unpack_from(fmt, self.buf, self.pos + o)[0] if o else default

    def vector(self, fid):
        """(start_offset, count) for a vector field, or (0, 0) if absent."""
        o = self._field(fid)
        if not o:
            return 0, 0
        p = self.pos + o
        start = p + struct.unpack_from("<I", self.buf, p)[0]
        return start + 4, struct.unpack_from("<I", self.buf, start)[0]

    def string(self, fid):
        o = self._field(fid)
        if not o:
            return ""
        p = self.pos + o
        start = p + struct.unpack_from("<I", self.buf, p)[0]
        ln = struct.unpack_from("<I", self.buf, start)[0]
        return self.buf[start + 4:start + 4 + ln].decode("ascii", "replace")


# field ids from the index schema, in declaration order
F_CODENAME, F_NUM_ARCHIVES, F_SEED = 0, 1, 2
F_ARCHIVE_HASHES, F_INDEXERS, F_CHUNKS, F_EXTERNAL_HASHES = 3, 4, 5, 6

INDEXER = struct.Struct("<iII")          # chunk index, file size, offset into chunk
CHUNK = struct.Struct("<QIII4x")         # file offset, size, uncompressed size, align
# byte 20 is a flag and 21 is padding; the archive number sits at 22
CHUNK_FILE_NO = 22
CHUNK_SIZE = 24


def hash_path(path: str) -> int:
    return xxh64(path.replace("\\", "/").lower().encode("ascii"))


class DataArchive:
    def __init__(self, index_path):
        self.path = pathlib.Path(index_path)
        self.dir = self.path.parent
        buf = self.path.read_bytes()
        root = struct.unpack_from("<I", buf, 0)[0]
        t = _Table(buf, root)
        self.num_archives = t.scalar(F_NUM_ARCHIVES, "<H")
        self.buf = buf
        self._hashes, self._nhashes = t.vector(F_ARCHIVE_HASHES)
        self._indexers, n_idx = t.vector(F_INDEXERS)
        self._chunks, self._nchunks = t.vector(F_CHUNKS)
        if self._nhashes != n_idx:
            raise ValueError(f"{self.path}: {self._nhashes} hashes but {n_idx} indexers")
        self._streams = {}

    def __len__(self):
        return self._nhashes

    def _find(self, h):
        """Index of a hash in the sorted array, or -1. The array is the only
        lookup the format offers: there are no names stored anywhere."""
        lo, hi = 0, self._nhashes - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            v = struct.unpack_from("<Q", self.buf, self._hashes + mid * 8)[0]
            if v == h:
                return mid
            if v < h:
                lo = mid + 1
            else:
                hi = mid - 1
        return -1

    def __contains__(self, path):
        return self._find(hash_path(path)) >= 0

    def read(self, path) -> bytes:
        """The file's bytes, decompressing its chunk if needed."""
        i = self._find(hash_path(path))
        if i < 0:
            raise FileNotFoundError(f"{path} is not in {self.path.name}")
        chunk_i, size, offset = INDEXER.unpack_from(self.buf, self._indexers + i * 12)
        if chunk_i == -1:
            return b""                       # recorded but empty
        if not 0 <= chunk_i < self._nchunks:
            raise ValueError(f"{path}: chunk {chunk_i} out of range")

        c = self._chunks + chunk_i * CHUNK_SIZE
        file_off, csize, usize, _ = CHUNK.unpack_from(self.buf, c)
        archive_no = self.buf[c + CHUNK_FILE_NO]
        if archive_no > self.num_archives:
            raise ValueError(f"{path}: archive {archive_no} above {self.num_archives}")

        fh = self._streams.get(archive_no)
        if fh is None:
            p = self.dir / f"data.{archive_no}"
            if not p.exists():
                raise FileNotFoundError(f"{p} is missing; the game install is incomplete")
            fh = self._streams[archive_no] = open(p, "rb")
        fh.seek(file_off)
        raw = fh.read(csize)
        if len(raw) != csize:
            raise ValueError(f"{path}: short read from data.{archive_no}")

        data = raw if csize == usize else lz4_decompress(raw, usize)
        if offset + size > len(data):
            raise ValueError(f"{path}: runs past the end of its chunk")
        return bytes(data[offset:offset + size])

    def extract(self, path, outdir) -> pathlib.Path:
        out = pathlib.Path(outdir) / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.read(path))
        return out

    def close(self):
        for fh in self._streams.values():
            fh.close()
        self._streams.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def selfcheck():
    """XXHash64 against the published vectors, and the LZ4 case that breaks
    hand-written decoders: a match that overlaps what it is still writing."""
    assert xxh64(b"") == 0xEF46DB3751D8E999, hex(xxh64(b""))
    assert xxh64(b"a") == 0xD24EC4F1A98C6E5B, hex(xxh64(b"a"))
    assert xxh64(b"x" * 100) == xxh64(b"x" * 100)
    # token 0x1F: 1 literal, match length field 15 (so a trailing extension
    # byte), offset 1 -> a 19-byte overlapping run grown from one literal.
    assert lz4_decompress(bytes([0x1F, 0x61, 0x01, 0x00, 0x00]), 20) == b"a" * 20
    assert lz4_decompress(bytes([0x30, 0x61, 0x62, 0x63]), 3) == b"abc"
    print("siero self-check passed: xxhash64 vectors, lz4 literals and overlapping match")


def main():
    if len(sys.argv) == 1:
        return selfcheck()
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    with DataArchive(sys.argv[1]) as ar:
        cmd = sys.argv[2]
        if cmd == "exists":
            for p in sys.argv[3:]:
                print(f"{'HIT ' if p in ar else 'miss'} {p}")
        elif cmd == "extract":
            for p in sys.argv[4:]:
                try:
                    out = ar.extract(p, sys.argv[3])
                    print(f"OK   {p} -> {out} ({out.stat().st_size} bytes)")
                except Exception as e:
                    print(f"FAIL {p}: {e}")
        else:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
