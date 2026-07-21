"""Write side: pristine backups, and rebuilding banks from them with the
saved profile replayed on top."""
import pathlib, threading

from chatterbox.banks import SILENCE, MediaBank, atomic_write, replay
from chatterbox.game import backup_path, battle_banks, check_pl, pl_of


def unused_name(path):
    """path, or path-2, path-3... Retiring a backup must never clobber an older one."""
    cand, n = path, 1
    while cand.exists():
        n += 1
        cand = path.with_name(f"{path.name}-{n}")
    return cand


class Patcher:
    """Applies mutes/swaps to banks and guards the pristine originals."""

    def __init__(self, library):
        self.library = library
        self.store = library.store
        self.locks = {}          # pl -> Lock; one writer per character
        self.lock_guard = threading.Lock()

    def lock(self, pl):
        pl = check_pl(pl)    # validate before it becomes a permanent dict key
        with self.lock_guard:
            return self.locks.setdefault(pl, threading.Lock())

    def backup_is_valid(self, bank_path):
        """A backup only counts if it exists, parses, and matches what we recorded."""
        p = backup_path(bank_path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        try:
            sha = MediaBank(p).sha256()                           # must also parse
        except Exception:
            return False
        known = self.store.manifest_sha(pathlib.Path(bank_path).name, "original")
        return known is None or known == sha

    def game_was_patched(self, name, backup, live):
        """True only when the live bank is a new game file (a patch or reinstall).

        Compares shas rather than replaying intent onto the backup: replay
        cannot reproduce chained swaps, so it misread our own edits as a patch
        and retired the only original. When unsure, keep the backup: a wrong
        keep costs nothing, a wrong retire costs the original."""
        try:
            live_sha = live.sha256()
            if live_sha == self.store.manifest_sha(name, "applied"):
                return False                       # exactly what we last wrote
            if live_sha == MediaBank(backup).sha256():
                return False                       # the current file IS the original
        except Exception as e:
            print(f"[backup] cannot account for the current bank ({e}); keeping the backup")
            return False
        return self.store.manifest_sha(name, "applied") is not None

    def ensure_original(self, bank):
        """Guarantee a pristine backup exists for this bank and matches the game.

        The backup is the immutable original: revert copies it straight back, and
        every apply rebuilds from it. Returns True if it was just created."""
        name = bank.path.name
        backup = backup_path(bank.path)
        if not self.backup_is_valid(bank.path):
            known = self.store.manifest_sha(name, "original")
            cur = bank.sha256()
            if known is not None and known != cur:
                raise ValueError(
                    f"{name} has been changed since Chatterbox first saw it, and its "
                    f"backup is missing, so the original cannot be recovered from here.\n"
                    f"Fix it in Steam FIRST: right-click Granblue Fantasy: Relink -> "
                    f"Properties -> Installed Files -> Verify integrity of game files.\n"
                    f"Only once that has finished, run:  serve.py --forget {pl_of(name)}\n"
                    f"Doing it the other way round would record your edited bank as the "
                    f"original.")
            atomic_write(backup, bank.data)
            self.store.remember_original(name, cur)
            print(f"[backup] original saved -> {backup}")
            return True
        # The backup is sound. Did a game update replace the live file? Then the
        # old original is a previous version; retire it (kept, never deleted) and
        # adopt the new file as the original to rebuild onto.
        if self.game_was_patched(name, backup, bank):
            retired = unused_name(backup.with_suffix(".backup-previous-version"))
            backup.replace(retired)
            atomic_write(backup, bank.data)
            self.store.remember_original(name, bank.sha256())
            print(f"[backup] {name} changed; new original recorded, "
                  f"old kept as {retired.name}")
        return False

    def _rebuild(self, bank, entry):
        """Write one bank as its pristine original with this profile entry
        replayed onto it. Rebuilding from pristine every time means edits never
        accumulate, and un-mute is just the entry's absence. Returns whether the
        original was just created."""
        created = self.ensure_original(bank)
        fresh = MediaBank(backup_path(bank.path))
        replay(fresh, entry)
        try:
            fresh.write(bank.path)
        except PermissionError:
            # By far the most common cause, and "[WinError 5] Access is denied"
            # tells a player nothing.
            raise ValueError(
                "Could not write to the game files. Is Granblue Fantasy: Relink still "
                "running? Close the game completely, check the taskbar and the system "
                "tray, then press Apply again.")
        self.store.remember_applied(bank.path.name, fresh.sha256())
        self.library.invalidate(pl_of(bank.path.name))        # cache stale after the write
        return created

    def apply(self, pl, mutes, swaps, unmutes=()):
        """mutes/unmutes: [wem_id]; swaps: {target: source}. Backs up once, then writes."""
        with self.lock(pl):
            return self._apply(pl, mutes, swaps, unmutes)

    def _apply(self, pl, mutes, swaps, unmutes=()):
        # Distribute the request to the bank that owns each line, fold it into that
        # bank's saved profile entry, and rebuild only the banks touched. The
        # profile keys by bank filename, since a character has several banks.
        prof = self.store.profile()
        banks = {b.path.name: b for b in self.library.banks_for(pl)}
        touched = set()

        def owners(wem):
            return [b for b in banks.values() if int(wem) in b.entries]

        def entry(name):
            touched.add(name)
            return prof.setdefault(name, {"mutes": [], "swaps": {}})

        # A mute/unmute hits EVERY physical copy of the id: ids can appear in more
        # than one of a character's banks, and a mute must silence the line
        # wherever it plays. A swap targets the one bank the user picked the line
        # from (largest-first, matching how the atlas tags it).
        for w in mutes:
            for b in owners(w):
                e = entry(b.path.name)
                if str(w) not in e["mutes"]:
                    e["mutes"] = sorted(e["mutes"] + [str(w)])
        for w in unmutes:
            for b in owners(w):
                e = entry(b.path.name)
                e["mutes"] = [x for x in e["mutes"] if x != str(w)]
                e["swaps"].pop(str(w), None)
        for tgt, src in swaps.items():
            b = self.library.bank_of(pl, int(tgt))
            if b:
                entry(b.path.name)["swaps"][str(tgt)] = str(src)

        for name in list(touched):                    # empty entry = pristine bank
            if not prof.get(name, {}).get("mutes") and not prof.get(name, {}).get("swaps"):
                prof.pop(name, None)
        # Record the full intent BEFORE writing any bank. If a later bank's write
        # fails, the profile is then AHEAD of disk (a reapply completes it), never
        # behind it (which would silently drop an already-written edit).
        self.store.save_profile(prof)
        created = False
        for name in touched:
            created = self._rebuild(banks[name], prof.get(name) or {"mutes": [], "swaps": {}}) or created
        main = self.library.banks_for(pl)[0]
        print(f"[apply] {len(mutes) + len(swaps)} change(s) across {len(touched)} bank(s) for {pl}")
        return {"ok": True, "applied": len(mutes) + len(swaps),
                "backup_path": str(backup_path(main.path)), "backup_created": created,
                "patched_path": str(main.path), "profile_path": str(self.store.profile_file)}

    def mute_character(self, pl):
        """Silence every line for one character, across all banks, replacing edits."""
        with self.lock(pl):
            return self._mute_character(pl)

    def _mute_character(self, pl):
        silence = len(SILENCE.read_bytes())
        prof = self.store.profile()
        banks = self.library.banks_for(pl)
        total = 0
        for b in banks:
            # skip any slot too small for the silent clip; in practice none are,
            # but a bulk mute must never fail wholesale on one odd stub
            ids = [str(w) for w, (_o, ln) in b.entries.items() if ln >= silence]
            prof[b.path.name] = {"mutes": ids, "swaps": {}}
            total += len(ids)
        self.store.save_profile(prof)                  # record intent before writing
        for b in banks:
            self._rebuild(b, prof[b.path.name])
        print(f"[mute] {total} line(s) silenced for {pl}")
        return {"ok": True, "pl": pl, "muted": total}

    def mute_all(self):
        """Silence every line for every character."""
        done, failed = {}, {}
        for c in self.library.characters():
            pl = c["pl"]
            try:
                done[pl] = self.mute_character(pl)["muted"]   # locks per character
            except Exception as e:      # one bad character must not abort the rest
                failed[pl] = str(e)
        return {"ok": not failed, "muted": done, "failed": failed}

    def reapply(self):
        """Re-apply saved intent to the current banks after a game patch or
        reinstall. ensure_original detects a new game version and refreshes the
        original before rebuilding."""
        prof = self.store.profile()
        done, failed = {}, {}
        for name in list(prof):
            path = self.library.voice_dir / name
            if not path.exists():
                continue
            pl = pl_of(name)
            try:
                with self.lock(pl):
                    self.library.invalidate(pl)         # re-read: may be a new game version
                    # construct just this bank, so a corrupt sibling of the same
                    # character cannot poison a healthy one via banks_for
                    self._rebuild(MediaBank(path), prof[name])
                done[name] = len(prof[name]["mutes"]) + len(prof[name]["swaps"])
            except Exception as e:          # one bad bank must not abort the rest
                failed[name] = str(e)
                print(f"[reapply] {name} failed: {e}")
        return {"ok": not failed, "reapplied": done, "failed": failed,
                "profile_path": str(self.store.profile_file)}

    def forget(self, pl):
        """Drop the recorded originals for a character, so verified-clean banks
        can be re-adopted without hand-editing originals.json."""
        pl = check_pl(pl)
        m = self.store.manifest()
        banks = battle_banks(self.library.voice_dir, pl)
        gone = sum(m.pop(b.name, None) is not None for b in banks)
        if not gone:
            print(f"[forget] nothing recorded for {pl}")
            return
        # Clear the manifest FIRST. If a backup rename then fails, the manifest no
        # longer claims an original for that bank, so ensure_original will not tell
        # the user to Steam-verify for a half-finished forget.
        self.store.save_manifest(m)
        for b in banks:
            backup = backup_path(b)
            if backup.exists():
                backup.replace(unused_name(backup.with_suffix(".backup-previous-version")))
        print(f"[forget] {pl} reset ({gone} bank(s)); the next Apply records fresh originals")

    def revert_all(self):
        """Undo every character."""
        done, failed = [], {}
        for c in self.library.characters():
            pl = c["pl"]
            if not any(backup_path(b).exists() for b in battle_banks(self.library.voice_dir, pl)):
                continue
            try:
                r = self.revert(pl)
                done.append(pl)
                if not r["ok"]:         # some banks reverted, some did not
                    failed[pl] = r["failed"]
            except Exception as e:      # one bad character must not abort the rest
                failed[pl] = str(e)
        return {"ok": not failed, "reverted": done, "failed": failed}

    def revert(self, pl):
        with self.lock(pl):
            return self._revert(pl)

    def _revert(self, pl):
        """Restore every one of a character's banks from its backup. Each bank is
        independent: one bad backup must not abort the others, and progress is
        persisted so a later reapply cannot un-revert what already succeeded."""
        restored, failed = [], {}
        prof = self.store.profile()
        for path in self.library.bank_paths(pl):   # paths only; a corrupt live bank is fine
            backup = backup_path(path)
            if not backup.exists():
                continue
            try:
                if not self.backup_is_valid(path):
                    raise ValueError(
                        f"the backup at {backup} is empty, damaged, or from a different "
                        f"version of the game. Verify the game files through Steam instead.")
                # Drop the profile entry and persist it BEFORE overwriting the live
                # file. A crash in this window then leaves the bank un-reverted but
                # unrecorded, so a later reapply skips it (safe) rather than
                # re-muting a bank the user just restored.
                prof.pop(path.name, None)
                self.store.save_profile(prof)
                atomic_write(path, backup.read_bytes())
                restored.append(path.name)
            except Exception as e:
                failed[path.name] = str(e)
        self.library.invalidate(pl)
        if not restored:
            raise ValueError(next(iter(failed.values())) if failed
                             else f"no backup found for {pl}")
        print(f"[revert] {pl}: restored {len(restored)} bank(s)"
              + (f", {len(failed)} failed" if failed else ""))
        return {"ok": not failed, "restored_from": restored, "restored_to": pl,
                "count": len(restored), "failed": failed}
