#!/usr/bin/env python3
"""GBFR-chatterbox local UI. Reads the user's own game install; hosts nothing.

Usage: serve.py [--game <path>] [--port 8777] [--atlas build/atlas]
"""
import argparse, hashlib, http.server, json, os, pathlib, re, secrets, sys, tempfile, threading, urllib.parse, webbrowser

from chatterbox.banks import (BUNDLE_DIR, PKG_DIR, SILENCE, MediaBank,
                              atomic_write, decode_wav, replay)
from chatterbox.pck import Pck
from chatterbox.siero import DataArchive

HERE = BUNDLE_DIR      # app root: tools/, atlas/, and what we ship beside them
# Under a frozen build HERE is a temp directory wiped on exit, so anything we
# WRITE has to sit beside the exe or the user would re-extract 200MB on every
# launch.
APP_DIR = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) else HERE

# Loaded once at startup: pl id -> character name, and the single-page UI.
NAMES = json.loads((PKG_DIR / "characters.json").read_text(encoding="utf-8")) \
    if (PKG_DIR / "characters.json").exists() else {}
UI = (PKG_DIR / "ui.html").read_bytes() if (PKG_DIR / "ui.html").exists() \
    else b"<h1>ui.html is missing from this folder.</h1>"

GAME_DIR = "Granblue Fantasy Relink"
REL_VOICE = "data/sound/English(US)"

# Last-resort guesses if Steam's own config can't be read.
GAME_CANDIDATES = [
    "C:\\Program Files (x86)\\Steam\\steamapps\\common\\" + GAME_DIR,
    "D:\\SteamLibrary\\steamapps\\common\\" + GAME_DIR,
    str(pathlib.Path.home() / ".local/share/Steam/steamapps/common" / GAME_DIR),
    str(pathlib.Path.home() / ".steam/steam/steamapps/common" / GAME_DIR),
]


def steam_roots():
    """Where Steam itself says it is installed."""
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(pathlib.Path(winreg.QueryValueEx(k, val)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
    else:
        for p in (".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/data/Steam"):
            roots.append(pathlib.Path.home() / p)
    return [r for r in roots if r.is_dir()]


def steam_libraries():
    """Every Steam library folder, from libraryfolders.vdf (games often live off-drive)."""
    libs = []
    for root in steam_roots():
        libs.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # entries look like:   "path"    "D:\\SteamLibrary"
        for m in re.finditer(r'"path"\s*"([^"]+)"', text):
            libs.append(pathlib.Path(m.group(1).replace("\\\\", "\\")))
    return libs


def find_game(explicit=None):
    """Locate the game: explicit path, then Steam's own library list, then guesses."""
    if explicit:
        p = pathlib.Path(explicit) / REL_VOICE
        if p.is_dir():
            return p
        sys.exit(f"No voice files under {explicit}\n(expected {explicit}\\{REL_VOICE})")

    # A folder the user typed into a text file, for installs Steam has forgotten.
    # Someone who double-clicks run.bat cannot pass a command-line flag.
    hint = APP_DIR / "game-path.txt"
    if hint.exists():
        for line in hint.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip().strip('"')
            if not line or line.startswith("#"):
                continue
            p = pathlib.Path(line) / REL_VOICE
            if p.is_dir():
                return p
            sys.exit(f"The folder in {hint.name} does not contain the game's voice files.\n"
                     f"  looked in: {pathlib.Path(line) / REL_VOICE}\n"
                     f"Open {hint} in Notepad and correct it.")

    for lib in steam_libraries():
        p = lib / "steamapps" / "common" / GAME_DIR / REL_VOICE
        if p.is_dir():
            return p
    for c in GAME_CANDIDATES:
        p = pathlib.Path(c) / REL_VOICE
        if p.is_dir():
            return p

    try:
        hint.write_text(
            "# Chatterbox could not find Granblue Fantasy: Relink.\n"
            "# In Steam, right-click the game -> Manage -> Browse local files,\n"
            "# then copy the folder from the address bar and paste it below,\n"
            "# replacing this whole file's contents. Save, and run Chatterbox again.\n"
            "#\n"
            "# Example:\n"
            "# D:\\SteamLibrary\\steamapps\\common\\Granblue Fantasy Relink\n",
            encoding="utf-8")
        made = f"\nI made a file called {hint.name} next to run.bat. Open it in Notepad,\n" \
               f"follow the instructions inside, then run Chatterbox again."
    except OSError:
        made = "\nPass the folder yourself, e.g.\n" \
               '  run.bat --game "D:\\SteamLibrary\\steamapps\\common\\Granblue Fantasy Relink"'
    sys.exit("Could not find Granblue Fantasy: Relink." + made)


def battle_banks(voice_dir, pl):
    """Every _m.bnk for this character, largest first. A character has several:
    the main bank plus smaller ones for co-op reactions, emotes and callouts."""
    return sorted(voice_dir.glob(f"vo_{pl}*_m.bnk"),
                  key=lambda p: p.stat().st_size, reverse=True)


def pl_of(bank_name):
    """plXXXX from a bank filename like vo_pl1100_02_00_00_m.bnk."""
    return bank_name[3:9]


def pad_wav(wav: bytes, ms: int = 250) -> bytes:
    """Append silence. Browsers drop the tail of very short clips (fixed-size loss,
    so sub-second barks lose proportionally more); the padding absorbs it."""
    import struct as _s
    pos, fmt = 12, None
    while pos + 8 <= len(wav):
        tag, size = wav[pos:pos + 4], _s.unpack_from("<I", wav, pos + 4)[0]
        if tag == b"fmt ":
            fmt = _s.unpack_from("<HHIIHH", wav, pos + 8)
        elif tag == b"data" and fmt:
            nbytes = (fmt[2] * ms // 1000) * fmt[4]      # rate * ms * block_align
            out = bytearray(wav[:pos + 8 + size] + b"\0" * nbytes + wav[pos + 8 + size:])
            _s.pack_into("<I", out, pos + 4, size + nbytes)          # data chunk size
            _s.pack_into("<I", out, 4, len(out) - 8)                 # RIFF size
            return bytes(out)
        pos += 8 + size + (size & 1)
    return wav


def check_pl(pl):
    """Character ids build filesystem paths, so only ever accept the real shape."""
    if not re.fullmatch(r"pl\d{4}", str(pl)):
        raise ValueError(f"bad character id: {pl!r}")
    return pl


def unused_name(path):
    """path, or path-2, path-3... Retiring a backup must never clobber an older one."""
    cand, n = path, 1
    while cand.exists():
        n += 1
        cand = path.with_name(f"{path.name}-{n}")
    return cand


def profile_path():
    """User-level, NOT in the game dir - must survive reinstall."""
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home())) / "chatterbox"
    else:
        base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME",
                                           pathlib.Path.home() / ".config")) / "chatterbox"
    base.mkdir(parents=True, exist_ok=True)
    return base / "profile.json"


class App:
    def __init__(self, voice_dir, atlas_dir, profile=None):
        self.voice_dir = voice_dir
        self.atlas_dir = pathlib.Path(atlas_dir)
        self.banks = {}   # pl -> [MediaBank], largest first (lazy)
        self.pcks = {}    # pck filename -> Pck or None (lazy)
        self._wanted = None   # pck names present in the archive for our banks
        self.profile_file = pathlib.Path(profile) if profile else profile_path()
        # sha of each bank as we first found it, so we can tell "untouched" from
        # "already edited" and spot a game update. Lives with the profile, not the game.
        self.manifest_file = self.profile_file.with_name("originals.json")
        # Review flags for the transcription pass: {wem_id: {"wrong": true, ...}}.
        # An object per id so a replacement transcript can drop in later.
        self.flags_file = self.profile_file.with_name("flags.json")
        self.locks = {}          # pl -> Lock; one writer per character
        self.lock_guard = threading.Lock()
        self._migrate()

    def _migrate(self):
        """Old profiles/manifests keyed by plNNNN; the model now keys by bank
        filename. Remap any legacy key onto the character's main bank, once, so
        an upgrade does not silently drop a user's saved mutes."""
        def remap(d):
            legacy = [k for k in d if re.fullmatch(r"pl\d{4}", k)]
            for pl in legacy:
                banks = battle_banks(self.voice_dir, pl)
                if banks:
                    name, cur = banks[0].name, d.get(banks[0].name)
                    if isinstance(cur, dict) and "mutes" in cur:   # merge profile entries
                        cur["mutes"] = sorted(set(cur.get("mutes", [])) | set(d[pl].get("mutes", [])))
                        cur.setdefault("swaps", {}).update(d[pl].get("swaps", {}))
                    else:                                          # manifest: newer wins
                        d.setdefault(name, d[pl])
                del d[pl]
            return bool(legacy)
        prof = self.profile()
        if remap(prof):
            self.save_profile(prof)
            print("[migrate] moved legacy profile entries onto their main bank")
        m = self.manifest()
        if remap(m):
            atomic_write(self.manifest_file, json.dumps(m, indent=1).encode())

    def lock(self, pl):
        pl = check_pl(pl)    # validate before it becomes a permanent dict key
        with self.lock_guard:
            return self.locks.setdefault(pl, threading.Lock())

    def _read_json(self, path):
        """A truncated side-car must not brick every apply and revert.

        These files record intent, not game data. If one is unreadable the
        safe move is to carry on with an empty one and let it be rewritten.
        """
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"[warn] {path.name} is unreadable ({e}); starting a fresh one")
            return {}

    def manifest(self):
        return self._read_json(self.manifest_file)

    def manifest_sha(self, key, field):
        """A recorded sha for a bank (keyed by its filename). `field` is
        "original" (the pristine bank we first backed up) or "applied" (as we
        last wrote it). Old manifests stored a bare original string; allow it."""
        v = self.manifest().get(key)
        if isinstance(v, str):
            return v if field == "original" else None
        return (v or {}).get(field)

    def _remember(self, key, field, sha):
        m = self.manifest()
        v = m.get(key)
        entry = v if isinstance(v, dict) else ({"original": v} if isinstance(v, str) else {})
        entry[field] = sha
        m[key] = entry
        atomic_write(self.manifest_file, json.dumps(m, indent=1).encode())

    def remember_original(self, key, sha):
        self._remember(key, "original", sha)

    def remember_applied(self, pl, sha):
        self._remember(pl, "applied", sha)

    def profile(self):
        return self._read_json(self.profile_file)

    def save_profile(self, prof):
        atomic_write(self.profile_file, json.dumps(prof, indent=1).encode())
        print(f"[profile] saved -> {self.profile_file}")

    def flags(self):
        return self._read_json(self.flags_file)

    def set_flag(self, wem_id, wrong):
        """Mark/unmark a line as an incorrect transcription. Preserves any other
        fields already stored for the id (e.g. a future replacement transcript)."""
        f = self.flags()
        wem_id = str(wem_id)
        entry = f.get(wem_id) or {}
        if wrong:
            entry["wrong"] = True
            f[wem_id] = entry
        else:
            entry.pop("wrong", None)
            if entry:
                f[wem_id] = entry
            else:
                f.pop(wem_id, None)
        atomic_write(self.flags_file, json.dumps(f, indent=1).encode())
        return {"ok": True, "wem_id": wem_id, "wrong": bool(wrong)}

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
        flags = self.flags()
        banks = self.banks_for(pl)
        # per bank: its pristine entries, and a (offset,len) -> wem reverse map,
        # so muted/swapped state is read from the bytes with no side-car to desync.
        orig, byloc = {}, {}
        for b in banks:
            bk = self.backup_path(b.path)
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
                "duration": r.get("duration_s"),
                "muted": muted,
                "swapped_from": swapped_from,
                # Streamed lines keep only a ~0.4s prefetch head in the bank; the rest
                # lives in a .pck inside the packed archives. Without that .pck we can
                # only preview the head, so tell the UI rather than looking broken.
                "streamed": bool(r.get("streamed")),
                "preview_full": not r.get("streamed") or bool(pk and w in pk),
                "prefetch_s": r.get("prefetch_s"),
                # which bank (version) the line lives in, so swaps stay intra-bank
                "bank": b.path.name,
            })
        main = banks[0]
        backup = self.backup_path(main.path)
        return {"lines": out, "bank": main.path.name,
                "bank_path": str(main.path), "backup_path": str(backup),
                "backup_exists": backup.exists()}

    def wanted_pcks(self):
        """The .pck names present in the archive for our banks, resolved once.

        A character has several banks; only some stream from a .pck. Caching the
        index lookup avoids re-reading data.i on every status poll.
        """
        if self._wanted is None:
            self._wanted = set()
            index = self.voice_dir.parent.parent.parent / "data.i"
            if index.exists():
                names = [b.name.replace("_m.bnk", ".pck")
                         for c in self.characters()
                         for b in battle_banks(self.voice_dir, c["pl"])]
                with DataArchive(index) as ar:
                    self._wanted = {n for n in names if "sound/english(us)/" + n in ar}
        return self._wanted

    def pck_status(self):
        """How many of the streamed voice packages are available locally."""
        want = self.wanted_pcks()
        have = sum(1 for n in want if (APP_DIR / "pck" / n).exists())
        return {
            "have": have,
            "total": len(want),
            "extractor": (self.voice_dir.parent.parent.parent / "data.i").exists(),
            "game_root": str(self.voice_dir.parent.parent.parent),
        }

    def extract_pcks(self):
        """Pull the streamed voice packages out of the user's OWN game archives.

        Nothing is downloaded and nothing leaves the machine: the game files are
        read, every character's voice .pck (one per bank that streams) is written
        next to the app, and previews then play whole lines.
        """
        index = self.voice_dir.parent.parent.parent / "data.i"
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
        """Preview the WHOLE line: streamed lines are only a prefetch head in the
        bank, so take those from the .pck when we have it."""
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

    def backup_path(self, bank_path):
        """The backup that sits beside a bank file (per bank, not per character)."""
        return pathlib.Path(bank_path).with_suffix(".bnk.chatterbox-backup")

    def backup_is_valid(self, bank_path):
        """A backup only counts if it exists, parses, and matches what we recorded."""
        p = self.backup_path(bank_path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        try:
            sha = hashlib.sha256(MediaBank(p).data).hexdigest()   # must also parse
        except Exception:
            return False
        known = self.manifest_sha(pathlib.Path(bank_path).name, "original")
        return known is None or known == sha

    def game_was_patched(self, name, backup, live):
        """True only when the live bank is a new game file (a patch or reinstall).

        Compares shas rather than replaying intent onto the backup: replay
        cannot reproduce chained swaps, so it misread our own edits as a patch
        and retired the only original. A file matching what we last wrote, or
        the pristine backup, was not changed externally. When unsure, keep the
        backup: a wrong keep costs nothing, a wrong retire costs the original.
        """
        try:
            live_sha = live.sha256()
            if live_sha == self.manifest_sha(name, "applied"):
                return False                       # exactly what we last wrote
            if live_sha == MediaBank(backup).sha256():
                return False                       # the current file IS the original
        except Exception as e:
            print(f"[backup] cannot account for the current bank ({e}); keeping the backup")
            return False
        return self.manifest_sha(name, "applied") is not None

    def apply(self, pl, mutes, swaps, unmutes=()):
        """mutes/unmutes: [wem_id]; swaps: {target: source}. Backs up once, then writes."""
        with self.lock(pl):
            return self._apply(pl, mutes, swaps, unmutes)

    def ensure_original(self, bank):
        """Guarantee a pristine backup exists for this bank and matches the game.

        The backup is the immutable original: revert copies it straight back, and
        every apply rebuilds from it. Returns True if it was just created.
        """
        name = bank.path.name
        backup = self.backup_path(bank.path)
        if not self.backup_is_valid(bank.path):
            known = self.manifest_sha(name, "original")
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
            self.remember_original(name, cur)
            print(f"[backup] original saved -> {backup}")
            return True
        # The backup is sound. Did a game update replace the live file with a new
        # version? If so the old original is a previous version; retire it (kept,
        # never deleted) and adopt the new file as the original to rebuild onto.
        if self.game_was_patched(name, backup, bank):
            retired = unused_name(backup.with_suffix(".backup-previous-version"))
            backup.replace(retired)
            atomic_write(backup, bank.data)
            self.remember_original(name, bank.sha256())
            print(f"[backup] {name} changed; new original recorded, "
                  f"old kept as {retired.name}")
        return False

    def _rebuild(self, bank, entry):
        """Write one bank as its pristine original with this profile entry replayed
        onto it. Rebuilding from pristine every time means edits never accumulate:
        a mute and a later swap of the same line cannot corrupt each other, and
        un-mute is just the entry's absence. Injected silence wins over streaming,
        so every line is mutable. Returns whether the original was just created."""
        created = self.ensure_original(bank)
        fresh = MediaBank(self.backup_path(bank.path))
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
        self.remember_applied(bank.path.name, fresh.sha256())
        self.banks.pop(pl_of(bank.path.name), None)   # cache stale after the write
        return created

    def _apply(self, pl, mutes, swaps, unmutes=()):
        # Distribute the request to the bank that owns each line, fold it into that
        # bank's saved profile entry, and rebuild only the banks touched. The
        # profile keys by bank filename, since a character has several banks.
        prof = self.profile()
        banks = {b.path.name: b for b in self.banks_for(pl)}
        touched = set()

        def owners(wem):
            return [b for b in banks.values() if int(wem) in b.entries]

        def entry(name):
            touched.add(name)
            return prof.setdefault(name, {"mutes": [], "swaps": {}})

        # A mute/unmute hits EVERY physical copy of the id: ids can appear in more
        # than one of a character's banks (byte-identical), and a mute must silence
        # the line wherever it plays. A swap targets the one bank the user picked
        # the line from (largest-first, matching how the atlas tags it).
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
            b = self.bank_of(pl, int(tgt))
            if b:
                entry(b.path.name)["swaps"][str(tgt)] = str(src)

        for name in list(touched):                    # empty entry = pristine bank
            if not prof.get(name, {}).get("mutes") and not prof.get(name, {}).get("swaps"):
                prof.pop(name, None)
        # Record the full intent BEFORE writing any bank. If a later bank's write
        # fails, the profile is then AHEAD of disk (a reapply completes it), never
        # behind it (which would silently drop an already-written edit).
        self.save_profile(prof)
        created = False
        for name in touched:
            created = self._rebuild(banks[name], prof.get(name) or {"mutes": [], "swaps": {}}) or created
        main = self.banks_for(pl)[0]
        print(f"[apply] {len(mutes) + len(swaps)} change(s) across {len(touched)} bank(s) for {pl}")
        return {"ok": True, "applied": len(mutes) + len(swaps),
                "backup_path": str(self.backup_path(main.path)), "backup_created": created,
                "patched_path": str(main.path), "profile_path": str(self.profile_file)}

    def mute_character(self, pl):
        """Silence every line for one character, across all banks, replacing edits."""
        with self.lock(pl):
            return self._mute_character(pl)

    def _mute_character(self, pl):
        silence = len(SILENCE.read_bytes())
        prof = self.profile()
        banks = self.banks_for(pl)
        total = 0
        for b in banks:
            # skip any slot too small for the silent clip; in practice none are,
            # but a bulk mute must never fail wholesale on one odd stub
            ids = [str(w) for w, (_o, ln) in b.entries.items() if ln >= silence]
            prof[b.path.name] = {"mutes": ids, "swaps": {}}
            total += len(ids)
        self.save_profile(prof)                        # record intent before writing
        for b in banks:
            self._rebuild(b, prof[b.path.name])
        print(f"[mute] {total} line(s) silenced for {pl}")
        return {"ok": True, "pl": pl, "muted": total}

    def mute_all(self):
        """Silence every line for every character."""
        done, failed = {}, {}
        for c in self.characters():
            pl = c["pl"]
            try:
                done[pl] = self.mute_character(pl)["muted"]   # locks per character
            except Exception as e:      # one bad character must not abort the rest
                failed[pl] = str(e)
        return {"ok": not failed, "muted": done, "failed": failed}

    def reapply(self):
        """Re-apply saved intent to the current banks after a game patch/reinstall.

        Rebuilds each bank named in the profile; ensure_original there detects a
        new game version and refreshes the original before rebuilding.
        """
        prof = self.profile()
        done, failed = {}, {}
        for name in list(prof):
            path = self.voice_dir / name
            if not path.exists():
                continue
            pl = pl_of(name)
            try:
                with self.lock(pl):
                    self.banks.pop(pl, None)   # re-read: may be a new game version
                    # construct just this bank, so a corrupt sibling of the same
                    # character cannot poison a healthy one via banks_for
                    self._rebuild(MediaBank(path), prof[name])
                done[name] = len(prof[name]["mutes"]) + len(prof[name]["swaps"])
            except Exception as e:          # one bad bank must not abort the rest
                failed[name] = str(e)
                print(f"[reapply] {name} failed: {e}")
        return {"ok": not failed, "reapplied": done, "failed": failed,
                "profile_path": str(self.profile_file)}

    def forget(self, pl):
        """Drop the recorded originals for a character, so verified-clean banks can
        be re-adopted. An operation the app owns, so a user is not told to delete
        originals.json by hand and do it before the Steam verify.
        """
        pl = check_pl(pl)
        m = self.manifest()
        banks = battle_banks(self.voice_dir, pl)
        gone = sum(m.pop(b.name, None) is not None for b in banks)
        if not gone:
            print(f"[forget] nothing recorded for {pl}")
            return
        # Clear the manifest FIRST. If a backup rename then fails, the manifest no
        # longer claims an original for that bank, so ensure_original will not tell
        # the user to Steam-verify for a half-finished forget.
        atomic_write(self.manifest_file, json.dumps(m, indent=1).encode())
        for b in banks:
            backup = self.backup_path(b)
            if backup.exists():
                backup.replace(unused_name(backup.with_suffix(".backup-previous-version")))
        print(f"[forget] {pl} reset ({gone} bank(s)); the next Apply records fresh originals")

    def revert_all(self):
        """Undo every character."""
        done, failed = [], {}
        for c in self.characters():
            pl = c["pl"]
            if not any(self.backup_path(b).exists() for b in battle_banks(self.voice_dir, pl)):
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
        prof = self.profile()
        for path in self.bank_paths(pl):     # paths only; a corrupt live bank is fine
            backup = self.backup_path(path)
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
                self.save_profile(prof)
                atomic_write(path, backup.read_bytes())
                restored.append(path.name)
            except Exception as e:
                failed[path.name] = str(e)
        self.banks.pop(pl, None)
        if not restored:
            raise ValueError(next(iter(failed.values())) if failed
                             else f"no backup found for {pl}")
        print(f"[revert] {pl}: restored {len(restored)} bank(s)"
              + (f", {len(failed)} failed" if failed else ""))
        return {"ok": not failed, "restored_from": restored, "restored_to": pl,
                "count": len(restored), "failed": failed}


# Any web page the user has open can POST to a localhost server. These endpoints
# write into the game install, so writes must prove they came from our own page.
TOKEN = secrets.token_urlsafe(24)


def make_handler(app):
    class H(http.server.BaseHTTPRequestHandler):
        def send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if "html" in ctype:
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            # Reads leak the CSRF token and the game install path, so they need the
            # same Host check as writes. Without it a rebound DNS name can read them.
            if not self.local_host():
                return self.send(403, {"error": "this address is not served here; "
                                               "open http://127.0.0.1:%d/"
                                               % self.server.server_address[1]})
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            try:
                if u.path in ("/", "/index.html"):
                    page = UI.replace(b"__CHATTERBOX_TOKEN__", TOKEN.encode())
                    return self.send(200, page, "text/html; charset=utf-8")
                if u.path == "/api/characters":
                    return self.send(200, app.characters())
                if u.path == "/api/pck-status":
                    return self.send(200, app.pck_status())
                if u.path == "/api/lines":
                    return self.send(200, app.lines(q["pl"][0]))
                if u.path == "/api/wav":
                    return self.send(200, app.wav(q["pl"][0], q["id"][0]), "audio/wav")
                self.send(404, {"error": "not found"})
            except Exception as e:
                self.send(500, {"error": str(e)})

        def local_host(self):
            """Host must name this loopback server, which is what blocks rebinding."""
            port = self.server.server_address[1]
            host = (self.headers.get("Host") or "").strip()
            return host in (f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}")

        def local_request(self):
            """Reject anything not originating from this server's own page."""
            if not self.local_host():
                return False
            port = self.server.server_address[1]
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return False
            sent = self.headers.get("X-Chatterbox-Token") or ""
            return secrets.compare_digest(sent, TOKEN)

        def do_POST(self):
            if not self.local_request():
                return self.send(403, {"error": "request did not come from the Chatterbox page"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > 4 * 1024 * 1024:
                    return self.send(413, {"error": "request too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError) as e:
                return self.send(400, {"error": f"bad request body: {e}"})
            try:
                if self.path == "/api/apply":
                    return self.send(200, app.apply(body["pl"], body.get("mutes", []),
                                                    body.get("swaps", {}),
                                                    body.get("unmutes", [])))
                if self.path == "/api/mute-all":
                    if body.get("all"):
                        return self.send(200, app.mute_all())
                    return self.send(200, app.mute_character(body["pl"]))
                if self.path == "/api/flag":
                    return self.send(200, app.set_flag(body["wem_id"], body.get("wrong", True)))
                if self.path == "/api/extract-pcks":
                    return self.send(200, app.extract_pcks())
                if self.path == "/api/revert":
                    if body.get("all"):
                        return self.send(200, app.revert_all())
                    return self.send(200, app.revert(body["pl"]))
                self.send(404, {"error": "not found"})
            except Exception as e:
                self.send(500, {"error": str(e)})
    return H


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--game")
    p.add_argument("--port", type=int, default=8777)
    default_atlas = HERE / "atlas"
    if not default_atlas.is_dir():          # source checkout: use the published dataset
        default_atlas = HERE / "data" / "per-character"
    p.add_argument("--atlas", default=str(default_atlas))
    p.add_argument("--profile")
    p.add_argument("--reapply", action="store_true",
                   help="re-apply saved profile to the current game files, then exit")
    p.add_argument("--forget", metavar="plXXXX",
                   help="forget the recorded original for one character, after "
                        "restoring it through Steam. Then exit.")
    a = p.parse_args()

    voice = find_game(a.game)
    app = App(voice, a.atlas, a.profile)
    if a.forget:
        app.forget(a.forget)
        return
    if a.reapply:
        r = app.reapply()
        print(f"reapplied: {r['reapplied'] or 'nothing stored'}")
        return
    chars = app.characters()
    if not chars:
        sys.exit(f"No atlas data found in {a.atlas}")

    # Try a few ports: someone who double-clicked run.bat cannot pass --port.
    for port in range(a.port, a.port + 10):
        try:
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
            break
        except OSError:
            continue
    else:
        sys.exit(f"Ports {a.port} to {a.port + 9} are all in use.\n"
                 f"Close any other Chatterbox console windows and try again.")
    if port != a.port:
        print(f"Port {a.port} was busy, using {port} instead.")
    url = f"http://127.0.0.1:{port}/"
    print(f"GBFR-chatterbox - {len(chars)} characters\nGame: {voice}\nOpen: {url}  (Ctrl-C to quit)")
    print("If your browser does not open by itself, type that address into it.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
