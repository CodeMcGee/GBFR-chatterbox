#!/usr/bin/env python3
"""Guard the backup lifecycle: the user's pristine bank must survive everything.

A swaps-only profile once made --reapply mistake our own edit for a game patch,
enshrine the edited bank as "the original", and on the next run overwrite the
retired copy. That destroyed the only untouched bank on disk.

    python3 dev/test_backup.py
"""
import hashlib, json, pathlib, shutil, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import serve
from test_banks import bank as make_bank, wem   # synthetic bank builders

sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
main_bank = lambda app, pl: app.banks_for(pl)[0]
backup_of = lambda app, pl: app.backup_path(app.banks_for(pl)[0].path)


def setup(td, bank):
    voice = td / "game/data/sound/English(US)"
    voice.mkdir(parents=True)
    shutil.copy(bank, voice / bank.name)
    app = serve.App(serve.find_game(str(td / "game")), HERE / "data/per-character",
                    td / "profile.json")
    return app, voice / bank.name


def main():
    banks = sorted(HERE.glob("samples/**/*_m.bnk"))
    if not banks:
        sys.exit("need a sample bank under samples/ to test")
    bank = banks[0]
    pl = bank.name.split("_")[1]

    # 1. a swaps-only profile must not cost the user their original
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        pristine = sha(live)
        ids = list(main_bank(app, pl).entries)[:3]
        app.apply(pl, [], {str(ids[0]): str(ids[1])})
        backup = backup_of(app, pl)
        assert sha(backup) == pristine, "backup should hold the untouched bank"

        app.reapply()
        assert sha(backup) == pristine, "reapply must not enshrine our own swap"
        app.apply(pl, [], {str(ids[0]): str(ids[2])})
        app.reapply()
        assert sha(backup) == pristine, "changing a swap must not retire the backup"
        print("  ok  swaps-only reapply leaves the backup pristine")

    # 1b. a chained re-swap (A->B, C->A, A->D) once fooled the patch check into
    # retiring the pristine backup, because replaying the flat swap dict could
    # not reproduce the live bytes. The sha of what we wrote must settle it.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        pristine = sha(live)
        a, b_, c, d = (str(w) for w in list(main_bank(app, pl).entries)[:4])
        app.apply(pl, [], {a: b_})
        app.apply(pl, [], {c: a})
        app.apply(pl, [], {a: d})
        backup = backup_of(app, pl)
        app.reapply()
        assert sha(backup) == pristine, "a chained re-swap must not retire the backup"
        assert not list(backup.parent.glob(backup.stem + "*previous-version*")), \
            "nothing external changed, so no backup should have been retired"
        print("  ok  chained re-swaps do not retire the pristine backup")

    # 2. a real game patch must retire the stale backup and record the new original
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        pristine = sha(live)
        ids = list(main_bank(app, pl).entries)[:2]
        app.apply(pl, [str(ids[0])], {})
        backup = backup_of(app, pl)

        # Steam replaces the file: same structure, different bytes, none of them ours
        b = serve.MediaBank(live)
        b.data[b.data_off + 1:b.data_off + 5] = b"\xde\xad\xbe\xef"
        b.write(live)
        patched = sha(live)
        app.banks.pop(pl, None)
        app.reapply()

        retired = backup.with_suffix(".backup-previous-version")
        assert retired.exists(), "a real patch should retire the old backup"
        assert sha(retired) == pristine, "retired copy must be the old original"
        assert sha(backup) != pristine, "backup should now track the new game version"
        assert app.manifest_sha(main_bank(app, pl).path.name, "original") == patched \
            or sha(backup) == patched, "the new original should be recorded"
        print("  ok  genuine game patch retires the backup and records the new original")

    # 3. retiring twice must not clobber the first retired copy
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        first = sha(live)
        ids = list(main_bank(app, pl).entries)[:2]
        app.apply(pl, [str(ids[0])], {})
        backup = backup_of(app, pl)
        for filler in (b"\x01\x02\x03\x04", b"\x05\x06\x07\x08"):
            b = serve.MediaBank(live)
            b.data[b.data_off + 1:b.data_off + 5] = filler
            b.write(live)
            app.banks.pop(pl, None)
            app.reapply()
        retired = list(backup.parent.glob(backup.stem + "*previous-version*"))
        assert len(retired) == 2, f"expected two retired copies, found {len(retired)}"
        assert first in {sha(p) for p in retired}, "the first original must still exist"
        print("  ok  a second retirement does not clobber the first")

    # 4. every apply rebuilds from the pristine original, so edits never
    # accumulate on the live bank. Mute a line, then swap that same line in a
    # later apply, then un-mute it: the result must be the line's real audio,
    # not the silence-plus-tail hybrid the old in-place edits produced.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        ids = list(main_bank(app, pl).entries)[:2]
        a, b_ = str(ids[0]), str(ids[1])
        original_a = serve.MediaBank(bank).wem(ids[0])
        app.apply(pl, [a], {})                    # mute A
        app.apply(pl, [], {a: b_})                # later: swap A -> B
        app.apply(pl, [], {}, unmutes=[a])        # then un-mute A
        got = serve.MediaBank(live).wem(ids[0])
        assert got == original_a, "un-muting a since-swapped line must give real audio"
        assert sha(backup_of(app, pl)) == sha(bank), "the original stays pristine throughout"
        print("  ok  every apply rebuilds from pristine, no leftover edit state")

    # 5. muting a whole character silences every line and overrides prior edits,
    # while leaving the original pristine so it can still be undone.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        sil = serve.SILENCE.read_bytes()
        ids = list(main_bank(app, pl).entries)[:2]
        app.apply(pl, [], {str(ids[0]): str(ids[1])})     # a swap first
        r = app.mute_character(pl)
        mb = serve.MediaBank(live)
        assert all(mb.wem(w) == sil for w in mb.entries), "mute_character must silence every line"
        assert r["muted"] == len(mb.entries), "reported count must match the lines silenced"
        assert sha(backup_of(app, pl)) == sha(bank), "muting all must leave the original pristine"
        print("  ok  mute-character silences everything and keeps the original pristine")

    # 6. a character with several banks: mute and undo must cover all of them,
    # and an apply must route each edit to the bank that owns the line. This is
    # the co-op reaction bank case that mute-all used to miss.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        # a second, smaller bank for the same character, sitting beside the first
        second = live.parent / ("vo_" + pl + "_02_00_00_m.bnk")
        second.write_bytes(make_bank([(90001, wem(4000)), (90002, wem(4000))]))
        assert len(app.banks_for(pl)) == 2, "both banks should be discovered"

        r = app.mute_character(pl)
        sil = serve.SILENCE.read_bytes()
        for b in (serve.MediaBank(live), serve.MediaBank(second)):
            assert all(b.wem(w) == sil for w in b.entries), "every bank must be silenced"
        assert r["muted"] == len(serve.MediaBank(live).entries) + 2, "count spans both banks"
        assert app.backup_path(second).exists(), "second bank must be backed up too"
        print("  ok  mute-character covers every bank of a character")

        app.banks.pop(pl, None)
        # a single mute of a line that lives only in the second bank
        app.apply(pl, ["90001"], {})
        prof = json.loads((td / "profile.json").read_text())
        assert second.name in prof and "90001" in prof[second.name]["mutes"], \
            "the edit must be recorded against the bank that owns the line"
        assert serve.MediaBank(second).wem(90001) == sil, "the second bank's line is muted"

        app.revert(pl)
        assert serve.MediaBank(live).sha256() == serve.MediaBank(bank).sha256(), "main restored"
        assert serve.MediaBank(second).entries[90001][1] != len(sil), "second bank restored"
        print("  ok  apply routes per bank and revert restores every bank")

    # 7. a partial revert (one bank's backup unusable) must still revert the
    # others and persist that, so a later reapply cannot un-revert what worked.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        second = live.parent / ("vo_" + pl + "_02_00_00_m.bnk")
        second.write_bytes(make_bank([(90001, wem(4000))]))
        mainid = str(list(serve.MediaBank(live).entries)[0])
        app.apply(pl, [mainid, "90001"], {})            # mute a line in each bank
        app.backup_path(second).write_bytes(b"not a bank")   # break the second backup
        r = app.revert(pl)
        assert serve.MediaBank(live).sha256() == serve.MediaBank(bank).sha256(), "main reverted"
        assert not r["ok"] and second.name in r["failed"], "the bad bank is reported failed"
        app.banks.pop(pl, None)
        app.reapply()
        assert serve.MediaBank(live).sha256() == serve.MediaBank(bank).sha256(), \
            "a reverted bank must not be re-edited by a later reapply"
        print("  ok  partial revert persists; reapply does not undo it")

    # 8. an old single-bank profile (keyed by plNNNN) must migrate to the main
    # bank on load, so an upgrading user does not silently lose their mutes.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        voice = td / "game/data/sound/English(US)"; voice.mkdir(parents=True)
        shutil.copy(bank, voice / bank.name)
        prof = td / "p.json"
        mid = str(list(serve.MediaBank(bank).entries)[0])
        prof.write_text(json.dumps({pl: {"mutes": [mid], "swaps": {}}}))   # legacy shape
        app = serve.App(serve.find_game(str(td / "game")), HERE / "data/per-character", prof)
        migrated = json.loads(prof.read_text())
        assert pl not in migrated and bank.name in migrated, "legacy key remapped to the bank"
        assert migrated[bank.name]["mutes"] == [mid], "the mute survived migration"
        print("  ok  legacy plNNNN profile migrates onto the main bank")

    # 9. a wem id can appear in more than one of a character's banks (identical
    # bytes); muting it must silence every physical copy, not just the largest.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        voice = td / "game/data/sound/English(US)"; voice.mkdir(parents=True)
        shared = wem(4000)
        (voice / "vo_pl0000_m.bnk").write_bytes(make_bank([(555, shared), (111, wem(4000))]))
        (voice / "vo_pl0000_02_00_00_m.bnk").write_bytes(make_bank([(555, shared), (222, wem(4000))]))
        app = serve.App(serve.find_game(str(td / "game")), HERE / "data/per-character", td / "p.json")
        sil = serve.SILENCE.read_bytes()
        app.apply("pl0000", ["555"], {})
        assert serve.MediaBank(voice / "vo_pl0000_m.bnk").wem(555) == sil, "main copy muted"
        assert serve.MediaBank(voice / "vo_pl0000_02_00_00_m.bnk").wem(555) == sil, "secondary copy muted"
        print("  ok  muting a shared id silences it in every bank")

    # 10. a write that fails partway through a multi-bank apply must leave the
    # profile AHEAD of disk (edit recorded), never behind it, or a later apply
    # would silently drop the already-written edit.
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        voice = td / "game/data/sound/English(US)"; voice.mkdir(parents=True)
        (voice / "vo_pl0000_m.bnk").write_bytes(make_bank([(111, wem(4000)), (112, wem(4000))]))
        (voice / "vo_pl0000_02_00_00_m.bnk").write_bytes(make_bank([(221, wem(4000))]))
        app = serve.App(serve.find_game(str(td / "game")), HERE / "data/per-character", td / "p.json")
        sil = serve.SILENCE.read_bytes()
        real = serve.MediaBank.write
        serve.MediaBank.write = lambda self, p: (_ for _ in ()).throw(PermissionError()) \
            if "02_00_00" in str(p) else real(self, p)
        try:
            app.apply("pl0000", ["111", "221"], {})   # 221's bank write fails
        except Exception:
            pass
        serve.MediaBank.write = real
        prof = json.loads((td / "p.json").read_text())
        assert "111" in prof.get("vo_pl0000_m.bnk", {}).get("mutes", []), "written edit must be recorded"
        app.banks.pop("pl0000", None)
        app.apply("pl0000", ["112"], {})              # unrelated later edit
        assert serve.MediaBank(voice / "vo_pl0000_m.bnk").wem(111) == sil, \
            "a later apply must not drop the earlier recorded edit"
        print("  ok  a mid-apply failure records progress; later apply keeps it")

    # 11. forget clears the manifest before retiring backups, so an interrupted
    # forget cannot leave a bank with no backup but a still-recorded original
    # (which would falsely demand a Steam verify).
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        app, live = setup(td, bank)
        second = live.parent / ("vo_" + pl + "_02_00_00_m.bnk")
        second.write_bytes(make_bank([(90001, wem(4000))]))
        app.apply(pl, [str(list(serve.MediaBank(live).entries)[0]), "90001"], {})
        app.forget(pl)
        for b in (live, second):
            assert not app.backup_path(b).exists(), "forget retires the backup"
            assert app.manifest_sha(b.name, "original") is None, "and clears its manifest entry"
        print("  ok  forget clears the manifest with the backups, no false lockout")

    print("\n11 checks passed")


if __name__ == "__main__":
    main()
