"""Side-car state: profile (saved intent), originals manifest, review flags.
All under the user's config dir, never in the game install."""
import json
import os
import pathlib
import sys

from chatterbox.banks import atomic_write


def profile_path():
    """User-level, NOT in the game dir - must survive reinstall."""
    if sys.platform == "win32":
        base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home())) / "chatterbox"
    else:
        base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME",
                                           pathlib.Path.home() / ".config")) / "chatterbox"
    base.mkdir(parents=True, exist_ok=True)
    return base / "profile.json"


class Store:
    """The three JSON side-cars, living beside the profile file."""

    def __init__(self, profile_file=None):
        self.profile_file = pathlib.Path(profile_file) if profile_file else profile_path()
        # sha of each bank as we first found it: tells "untouched" from
        # "already edited" and spots a game update.
        self.manifest_file = self.profile_file.with_name("originals.json")
        # Review flags: {wem_id: {"wrong": true, "correct": "..."}} for bad
        # transcripts, {"verified": true} for ear-confirmed ones.
        self.flags_file = self.profile_file.with_name("flags.json")

    def _read_json(self, path):
        """A truncated side-car must not brick every apply and revert. These
        files record intent; if unreadable, carry on empty and let it be
        rewritten."""
        try:
            return json.loads(path.read_text()) if path.exists() else {}
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"[warn] {path.name} is unreadable ({e}); starting a fresh one")
            return {}

    def profile(self):
        return self._read_json(self.profile_file)

    def save_profile(self, prof):
        atomic_write(self.profile_file, json.dumps(prof, indent=1).encode())
        print(f"[profile] saved -> {self.profile_file}")

    def manifest(self):
        return self._read_json(self.manifest_file)

    def save_manifest(self, m):
        atomic_write(self.manifest_file, json.dumps(m, indent=1).encode())

    def manifest_sha(self, key, field):
        """A recorded sha for a bank (keyed by filename). `field` is "original"
        or "applied". Old manifests stored a bare original string; allow it."""
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
        self.save_manifest(m)

    def remember_original(self, key, sha):
        self._remember(key, "original", sha)

    def remember_applied(self, key, sha):
        self._remember(key, "applied", sha)

    def flags(self):
        return self._read_json(self.flags_file)

    def set_flag(self, wem_id, wrong=None, correct=None, verified=None):
        """Mark a line wrong (with the reviewer's corrected words) or
        verified-correct. The two states are mutually exclusive; clearing the
        last one drops the entry."""
        flags = self.flags()
        wem_id = str(wem_id)
        entry = flags.get(wem_id) or {}
        if wrong is not None:
            if wrong:
                entry["wrong"] = True
                entry.pop("verified", None)
                if correct is not None:
                    entry["correct"] = correct.strip()
            else:
                entry.pop("wrong", None)
                entry.pop("correct", None)
        if verified is not None:
            entry = {"verified": True} if verified else {}
        if entry:
            flags[wem_id] = entry
        else:
            flags.pop(wem_id, None)
        atomic_write(self.flags_file, json.dumps(flags, indent=1).encode())
        return {"ok": True, "wem_id": wem_id,
                "wrong": bool(entry.get("wrong")), "verified": bool(entry.get("verified"))}
