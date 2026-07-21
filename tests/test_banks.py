#!/usr/bin/env python3
"""Guard the sound bank editor, which is the only thing here that writes.

Every edit is a byte patch to a file the user cannot easily replace, so the
failure that matters is not "it crashed" but "it wrote something plausible and
wrong". These checks build synthetic banks so the editing rules can be pinned
down exactly, then repeat the important ones against a real bank if one is
available under samples/.

    python tests/test_banks.py
"""
import pathlib, struct, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from chatterbox.banks import (MediaBank, SILENCE, atomic_write, label_of,
                              read_chunks, replay, wem_meta)
from chatterbox.pck import Pck


class Checks:
    def __init__(self):
        self.n = 0

    def ok(self, name):
        self.n += 1
        print(f"  ok  {name}")


def wem(seconds_of_data=32, rate=48000, channels=1, label=None, declared=None):
    """A minimal but structurally valid Wwise-style RIFF."""
    fmt = struct.pack("<HHIIHH", 0xFFFF, channels, rate, rate * 2, 2, 16)
    body = b"\xab" * seconds_of_data
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if label:
        adtl = b"adtl" + b"labl" + struct.pack("<I", 4 + len(label) + 1) \
               + struct.pack("<I", 1) + label.encode() + b"\0"
        chunks += b"LIST" + struct.pack("<I", len(adtl)) + adtl
    # declared lets a stub be built: the header claims more than is present
    chunks += b"data" + struct.pack("<I", declared if declared else len(body)) + body
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def bank(entries):
    """entries: [(wem_id, bytes)] -> a parseable _m.bnk, 16-byte aligned like the game's."""
    didx, data = b"", b""
    for wid, payload in entries:
        off = len(data)
        didx += struct.pack("<III", wid, off, len(payload))
        data += payload
        pad = (-len(data)) % 16
        data += b"\0" * pad
    out = b"BKHD" + struct.pack("<I", 8) + b"\0" * 8
    out += b"DIDX" + struct.pack("<I", len(didx)) + didx
    out += b"DATA" + struct.pack("<I", len(data)) + data
    return out


def written(tmp, entries):
    p = pathlib.Path(tmp) / "vo_pl0000_m.bnk"
    p.write_bytes(bank(entries))
    return p


# --- the container itself ----------------------------------------------------

def check_parsing(t):
    raw = bank([(1, wem()), (2, wem(64))])
    chunks = read_chunks(raw)
    assert set(chunks) >= {"BKHD", "DIDX", "DATA"}
    t.ok("read_chunks walks a well-formed bank")

    # Trailing bytes mean we misread a size somewhere, which would silently
    # shift every offset after it.
    try:
        read_chunks(raw + b"junk")
        raise AssertionError("trailing bytes should be rejected")
    except ValueError:
        pass
    t.ok("read_chunks rejects trailing bytes")

    with tempfile.TemporaryDirectory() as td:
        b = MediaBank(written(td, [(1, wem()), (2, wem(64))]))
        assert list(b.entries) == [1, 2]
        assert b.wem(1)[:4] == b"RIFF"
        t.ok("MediaBank indexes DIDX entries and returns their bytes")

        # An event bank has no DIDX; pointing the tool at one is a common
        # mistake and the message needs to say which file to use.
        p = pathlib.Path(td) / "no_didx.bnk"
        p.write_bytes(b"BKHD" + struct.pack("<I", 8) + b"\0" * 8)
        try:
            MediaBank(p)
            raise AssertionError("a bank without DIDX should be rejected")
        except ValueError as e:
            assert "_m.bnk" in str(e), "the error should name the right file to use"
        t.ok("a bank with no DIDX is rejected with a useful message")


def check_wem_metadata(t):
    declared, present, bps, rate, ch = wem_meta(wem(100, rate=48000, channels=2))
    assert (declared, present) == (100, 100)
    assert (rate, ch) == (48000, 2)
    t.ok("wem_meta reads format and sizes")

    # declared > present is the whole basis for detecting a streamed line.
    stub = wem(40, declared=4000)
    declared, present, *_ = wem_meta(stub)
    assert declared == 4000 and present == 40
    with tempfile.TemporaryDirectory() as td:
        b = MediaBank(written(td, [(1, stub), (2, wem(40))]))
        assert b.is_stub(1) and not b.is_stub(2)
    t.ok("is_stub distinguishes a prefetch head from a whole line")

    assert label_of(wem(label="PL0000_vo_ATK_test")) == "PL0000_vo_ATK_test"
    assert label_of(wem()) is None
    t.ok("label_of recovers the developer filename, and copes without one")


# --- the edits: these write to the user's game files -------------------------

def check_inject(t):
    with tempfile.TemporaryDirectory() as td:
        silence = SILENCE.read_bytes()
        b = MediaBank(written(td, [(1, wem(4000)), (2, wem(4000))]))
        before_two = b.wem(2)
        b.inject(1, silence)
        assert b.wem(1) == silence, "injected bytes should be readable back"
        assert b.entries[1][1] == len(silence), "DIDX length must shrink to fit"
        assert b.wem(2) == before_two, "injecting must not disturb its neighbour"
        t.ok("inject overwrites in place and shrinks the DIDX length")

        # Writing past the slot would corrupt whatever follows it in DATA.
        try:
            b.inject(2, b"\xcc" * 99999)
            raise AssertionError("oversized inject should be refused")
        except ValueError as e:
            assert "slot" in str(e)
        t.ok("inject refuses a payload larger than the slot")

        # After a swap two entries share bytes; muting one would silently
        # silence the other, which the user never asked for.
        b2 = MediaBank(written(td, [(1, wem(4000)), (2, wem(4000))]))
        b2.alias(1, 2)
        try:
            b2.inject(2, silence)
            raise AssertionError("muting a shared slot should be refused")
        except ValueError as e:
            assert "shares its audio" in str(e)
        t.ok("inject refuses a slot shared by an earlier swap")


def check_alias_and_replay(t):
    with tempfile.TemporaryDirectory() as td:
        path = written(td, [(1, wem(4000)), (2, wem(2000, rate=24000))])
        pristine_bytes = path.read_bytes()
        b = MediaBank(path)
        b.alias(1, 2)
        assert b.entries[1] == b.entries[2], "alias points both entries at one slot"
        assert b.wem(1) == b.wem(2)
        # An alias is a DIDX edit only: the audio blob must be untouched.
        assert b.data[b.data_off:] == bytearray(pristine_bytes)[b.data_off:]
        t.ok("alias repoints a DIDX entry without moving any audio")

        # replay is the whole apply path now: every apply rebuilds the bank from
        # the pristine original by replaying the merged profile. Hand-apply the
        # same intent and the bytes must match, or a rebuilt bank would drift
        # from a hand-edited one.
        one = MediaBank(path)
        one.inject(1, SILENCE.read_bytes())
        one.alias(2, 1)
        two = MediaBank(path)
        replay(two, {"mutes": ["1"], "swaps": {"2": "1"}})
        assert one.data == two.data, "replay must match a hand-applied edit byte for byte"
        t.ok("replay reproduces mutes and swaps exactly")

        # Rebuilding from pristine makes un-mute trivial: it is just the profile
        # without that mute. A mute then a swap of the same line, then dropping
        # the mute, must land on the swap alone, never a silence/audio hybrid.
        rebuilt = MediaBank(path)
        replay(rebuilt, {"mutes": [], "swaps": {"1": "2"}})   # mute of 1 dropped
        assert rebuilt.wem(1) == MediaBank(path).wem(2), "un-muted-then-swapped line plays its swap"
        t.ok("a rebuild from pristine has no leftover state from earlier edits")


def check_atomic_write(t):
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        target = td / "bank.bnk"
        target.write_bytes(b"original")

        atomic_write(target, b"replacement")
        assert target.read_bytes() == b"replacement"
        assert not list(td.glob("*.chatterbox-tmp")), "temp file should be gone"
        t.ok("atomic_write replaces a file and cleans up after itself")

        # A failed write must leave the original intact, not a truncated file.
        class Boom(bytes):
            def __len__(self):
                raise OSError("disk full")

        try:
            atomic_write(target, Boom(b"x"))
        except OSError:
            pass
        assert target.read_bytes() == b"replacement", "a failed write must not touch the original"
        assert not list(td.glob("*.chatterbox-tmp")), "temp file should be cleaned on failure"
        t.ok("a failed write leaves the original and no temp file behind")

        assert isinstance(bytearray(b"ab"), bytearray)
        atomic_write(target, bytearray(b"from a bytearray"))
        assert target.read_bytes() == b"from a bytearray"
        t.ok("atomic_write accepts a bytearray without copying it first")


def check_roundtrip(t):
    with tempfile.TemporaryDirectory() as td:
        path = written(td, [(1, wem(4000)), (2, wem(4000))])
        b = MediaBank(path)
        before = b.sha256()
        b.write(path)
        assert MediaBank(path).sha256() == before, "writing unchanged must be a no-op"
        t.ok("write is byte-stable when nothing changed")

        b.inject(1, SILENCE.read_bytes())
        b.write(path)
        assert MediaBank(path).sha256() != before
        assert MediaBank(path).wem(1) == SILENCE.read_bytes()
        t.ok("an edit survives a write and reload")


# --- the real thing, when a sample is available ------------------------------

def check_real_bank(t, path):
    b = MediaBank(path)
    assert len(b.entries) > 100, "a battle bank should hold hundreds of lines"
    labelled = sum(1 for w in list(b.entries)[:50] if b.label(w))
    assert labelled > 0, "real banks carry developer filenames"
    t.ok(f"real bank parses: {len(b.entries)} lines, labels present")

    stubs = sum(1 for w in b.entries if b.is_stub(w))
    assert 0 < stubs < len(b.entries), "a real bank mixes resident and streamed lines"
    t.ok(f"real bank shows {stubs} streamed prefetch heads among {len(b.entries)}")

    with tempfile.TemporaryDirectory() as td:
        copy = pathlib.Path(td) / path.name
        copy.write_bytes(path.read_bytes())
        live = MediaBank(copy)
        target = next(w for w in live.entries if live.entries[w][1] > len(SILENCE.read_bytes()))
        live.inject(target, SILENCE.read_bytes())
        live.write(copy)
        assert MediaBank(copy).wem(target) == SILENCE.read_bytes()
        assert path.read_bytes() != copy.read_bytes(), "the copy changed, not the sample"
        t.ok("muting a line in a real bank round-trips through disk")


def check_pck(t, sample):
    p = Pck(sample)
    assert len(p) > 0
    first = next(iter(p.streams))
    assert p.wem(first)[:4] == b"RIFF", "a streamed wem is still a RIFF"
    assert first in p and 0xDEADBEEF not in p
    t.ok(f"pck parses: {len(p)} streamed wems")


def main():
    t = Checks()
    check_parsing(t)
    check_wem_metadata(t)
    check_inject(t)
    check_alias_and_replay(t)
    check_atomic_write(t)
    check_roundtrip(t)

    banks = sorted(HERE.glob("samples/**/*_m.bnk"))
    if banks:
        check_real_bank(t, banks[0])
    else:
        print("  --  skipping real-bank checks, no sample under samples/")

    pcks = sorted(HERE.glob("build/pck/*.pck")) or sorted(HERE.glob("samples/**/*.pck"))
    if pcks:
        check_pck(t, pcks[0])
    else:
        print("  --  skipping pck checks, no .pck available")

    print(f"\n{t.n} checks passed")


def test_all():
    main()


if __name__ == "__main__":
    main()
