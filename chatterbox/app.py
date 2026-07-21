"""The application: one game install + one profile, wired together."""
import re

from chatterbox.banks import MediaBank
from chatterbox.game import battle_banks
from chatterbox.library import Library
from chatterbox.patching import Patcher, backup_path
from chatterbox.store import Store


class App:
    """Facade over Store, Library and Patcher. Keeps the surface serve.py
    always had; the domain logic lives in the composed parts."""

    def __init__(self, voice_dir, atlas_dir, profile=None):
        self.store = Store(profile)
        self.library = Library(voice_dir, atlas_dir, self.store)
        self.patcher = Patcher(self.library)
        self._migrate()

    # ponytail: one delegation line beats thirty def wrappers
    def __getattr__(self, name):
        if name in ("patcher", "library", "store"):    # not set yet during __init__
            raise AttributeError(name)
        for part in (self.patcher, self.library, self.store):
            if hasattr(part, name):
                return getattr(part, name)
        raise AttributeError(name)

    @staticmethod
    def backup_path(bank_path):
        return backup_path(bank_path)

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
        prof = self.store.profile()
        if remap(prof):
            self.store.save_profile(prof)
            print("[migrate] moved legacy profile entries onto their main bank")
        m = self.store.manifest()
        if remap(m):
            self.store.save_manifest(m)
