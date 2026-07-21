#!/usr/bin/env python3
"""Guard siero.py against the ways a hand-written format reader actually breaks.

Siero only reads, and only from the user's own game install, so there is no
hostile input to defend against here. What there is, is three pieces of
fiddly binary work where being subtly wrong produces plausible-looking
garbage rather than an error:

  - XXHash64, where a wrong value simply fails to find files
  - LZ4 block decoding, where overlapping matches are the classic bug
  - struct offsets in the index, where an off-by-one reads the wrong archive
    (this happened: DataFileNumber sits at byte 22, not 21)

The integration checks are the ones that matter most. Extracting a known file
and comparing its md5 exercises every layer at once, and those hashes came
from the C# implementation this replaced, so they are a real differential.

    python dev/test_siero.py
"""
import hashlib, pathlib, struct, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from chatterbox import siero

GAME = pathlib.Path.home() / ".local/share/Steam/steamapps/common/Granblue Fantasy Relink"
INDEX = GAME / "data.i"

# Both verified byte-identical against the C# tool before it was dropped.
KNOWN = [
    ("sound/english(us)/vo_pl2700_02_00_00.pck", 8922822,
     "86703d6871abe702a29abc690969678d", False),      # chunk stored raw
    ("ba/ba4003/cloth/ba4003_0_0_clp.bxm", 4709,
     "1e92bc9475da5e2a65a3c74aa1c4d9c3", True),       # chunk is LZ4, 10.9MB -> 38.6MB
]


class Checks:
    def __init__(self):
        self.n = 0

    def ok(self, name):
        self.n += 1
        print(f"  ok  {name}")


def lz4_block(*parts):
    """Assemble a raw LZ4 block from (token, literals, offset, ext) tuples."""
    out = bytearray()
    for token, literals, offset, match_ext in parts:
        out.append(token)
        out += literals
        if offset is not None:
            out += struct.pack("<H", offset)
            out += match_ext
    return bytes(out)


# --- the hash: wrong values just fail to find files, silently ----------------

def check_hash(t):
    assert siero.xxh64(b"") == 0xEF46DB3751D8E999
    assert siero.xxh64(b"a") == 0xD24EC4F1A98C6E5B
    t.ok("xxh64 matches the published reference vectors")

    # The algorithm branches at 32 bytes, then 8, then 4, then 1. Every one of
    # those boundaries is a place to drop or double-count a byte.
    seen = {siero.xxh64(b"x" * n) for n in (0, 1, 3, 4, 7, 8, 15, 16, 31, 32, 33, 64)}
    assert len(seen) == 12, "length boundaries collided; the tail loops are wrong"
    t.ok("xxh64 distinct across every length boundary")

    assert siero.xxh64(b"hello", 0) != siero.xxh64(b"hello", 1)
    assert all(0 <= siero.xxh64(b"y" * n) <= 0xFFFFFFFFFFFFFFFF for n in range(0, 40))
    t.ok("xxh64 honours the seed and stays inside 64 bits")

    # Paths are matched case-insensitively with either slash, because the index
    # stores hashes of the lowercased forward-slashed form.
    a = siero.hash_path("Sound/English(US)/Vo_Test.pck")
    assert a == siero.hash_path("sound/english(us)/vo_test.pck")
    assert a == siero.hash_path("Sound\\English(US)\\Vo_Test.pck")
    t.ok("hash_path normalises case and backslashes")


# --- LZ4: overlapping matches are where hand-written decoders go wrong -------

def check_lz4(t):
    assert siero.lz4_decompress(bytes([0x30, 0x61, 0x62, 0x63]), 3) == b"abc"
    assert siero.lz4_decompress(bytes([0x00]), 0) == b""
    t.ok("lz4 literals only")

    # offset 1 means the match reads bytes it is still writing. A slice copy
    # gets this wrong; it has to expand one byte at a time.
    assert siero.lz4_decompress(lz4_block((0x1F, b"a", 1, b"\x00")), 20) == b"a" * 20
    # offset 2 with a longer match: same trap, alternating pattern
    assert siero.lz4_decompress(lz4_block((0x24, b"ab", 2, b"")), 10) == b"ababababab"
    t.ok("lz4 overlapping matches expand instead of copying")

    # A match of exactly 4 (the minimum) with a non-overlapping offset takes
    # the fast path, so both branches need covering.
    assert siero.lz4_decompress(lz4_block((0x40, b"abcd", 4, b"")), 8) == b"abcdabcd"
    t.ok("lz4 non-overlapping match takes the block-copy path")

    # Lengths of 15 or more chain 255 bytes. Getting the terminator wrong here
    # silently truncates, which is worse than crashing.
    long_lit = bytes([0xF0]) + bytes([255, 4]) + b"z" * 274
    assert siero.lz4_decompress(long_lit, 274) == b"z" * 274
    assert siero.lz4_decompress(lz4_block((0x1F, b"q", 1, b"\xfe")), 274) == b"q" * 274
    t.ok("lz4 decodes multi-byte 255 length chains")

    # Silent short output would look like a corrupt game file to the user.
    for bad, out_len, why in [
        (lz4_block((0x1F, b"a", 1, b"\x00")), 21, "size mismatch"),
        (bytes([0x30, 0x61]), 3, "truncated literals"),
        (lz4_block((0x20, b"ab", 0, b"")), 10, "zero offset"),
        (lz4_block((0x04, b"", 4, b"")), 8, "match before start of block"),
    ]:
        try:
            siero.lz4_decompress(bad, out_len)
        except (ValueError, IndexError):
            continue
        raise AssertionError(f"lz4 accepted malformed input: {why}")
    t.ok("lz4 rejects malformed blocks instead of returning short data")


# --- index structs: an off-by-one here reads the wrong archive ---------------

def check_index_layout(t):
    assert siero.INDEXER.size == 12, "FileToChunkIndexer is 3 x 4 bytes"
    assert siero.CHUNK.size == 24, "DataChunk is 24 bytes including tail padding"
    t.ok("index struct sizes match the schema")

    # The regression that bit us: byte 20 is a flag, 21 is padding, and the
    # archive number is at 22. Reading 21 sent every lookup to data.0.
    raw = struct.pack("<QIII", 0x1122334455667788, 111, 222, 16) + bytes([1, 0, 11, 0])
    assert len(raw) == 24
    off, csize, usize, align = siero.CHUNK.unpack_from(raw, 0)
    assert (off, csize, usize, align) == (0x1122334455667788, 111, 222, 16)
    assert raw[siero.CHUNK_FILE_NO] == 11, "DataFileNumber must be read from byte 22"
    t.ok("chunk layout reads the archive number from byte 22")


# --- everything at once, against real game data -----------------------------

def check_against_game(t):
    with siero.DataArchive(INDEX) as ar:
        assert len(ar) > 0 and ar.num_archives > 0
        t.ok(f"index parses: {len(ar):,} files across {ar.num_archives} archives")

        for path, size, md5, compressed in KNOWN:
            assert path in ar, f"{path} should be in the archive"
            data = ar.read(path)
            assert len(data) == size, f"{path}: {len(data)} bytes, expected {size}"
            got = hashlib.md5(data).hexdigest()
            assert got == md5, f"{path}: md5 {got}, expected {md5}"
        t.ok("known files extract byte-identical to the previous implementation")

        # One of those two lives in an LZ4 chunk, so the decompressor is
        # exercised against real data and not only synthetic blocks.
        assert any(c for *_, c in KNOWN), "no compressed sample to exercise LZ4"
        t.ok("a real LZ4-compressed chunk round-trips correctly")

        assert "no/such/file.bin" not in ar
        try:
            ar.read("no/such/file.bin")
            raise AssertionError("missing file should raise")
        except FileNotFoundError:
            pass
        t.ok("a path not in the archive raises FileNotFoundError")

        # Case and separator insensitivity, end to end rather than just hashed.
        p = KNOWN[0][0]
        assert p.upper().replace("/", "\\") in ar
        t.ok("lookup is case and separator insensitive against real data")

        # The reader keeps one handle per archive rather than reopening.
        ar.read(KNOWN[0][0])
        handles = len(ar._streams)
        ar.read(KNOWN[0][0])
        assert len(ar._streams) == handles, "handles should be reused"
        t.ok("archive handles are reused across reads")
    assert not ar._streams, "context manager must close every handle"
    t.ok("context manager closes its handles")


def check_bad_index(t):
    """A partial or verifying Steam install should say so, not crash weirdly."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        for name, blob in [("empty.i", b""), ("junk.i", b"not flatbuffers at all"),
                           ("short.i", struct.pack("<I", 0xFFFFFF))]:
            p = td / name
            p.write_bytes(blob)
            try:
                siero.DataArchive(p)
            except (ValueError, struct.error, IndexError, IsADirectoryError):
                continue
            raise AssertionError(f"{name} should not parse as an index")
    t.ok("a corrupt or empty index fails cleanly")


def main():
    t = Checks()
    check_hash(t)
    check_lz4(t)
    check_index_layout(t)
    check_bad_index(t)

    if INDEX.exists():
        check_against_game(t)
    else:
        print(f"  --  skipping game checks, no index at {INDEX}")

    print(f"\n{t.n} checks passed")


def test_all():
    main()


if __name__ == "__main__":
    main()
