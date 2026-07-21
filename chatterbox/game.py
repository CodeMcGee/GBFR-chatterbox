"""Find the game install and name its parts: Steam discovery, bank files,
character ids."""
import json, pathlib, re, sys

from chatterbox.banks import BUNDLE_DIR, PKG_DIR

HERE = BUNDLE_DIR      # app root: tools/, atlas/, and what we ship beside them
# Under a frozen build HERE is a temp directory wiped on exit, so anything we
# WRITE has to sit beside the exe or the user would re-extract 200MB on every
# launch.
APP_DIR = pathlib.Path(sys.executable).parent if getattr(sys, "frozen", False) else HERE

# pl id -> character name, loaded once.
NAMES = json.loads((PKG_DIR / "characters.json").read_text(encoding="utf-8")) \
    if (PKG_DIR / "characters.json").exists() else {}

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
    """Every _m.bnk for this character, largest first: the main bank plus
    smaller ones for co-op reactions, emotes and callouts."""
    return sorted(voice_dir.glob(f"vo_{pl}*_m.bnk"),
                  key=lambda p: p.stat().st_size, reverse=True)


def pl_of(bank_name):
    """plXXXX from a bank filename like vo_pl1100_02_00_00_m.bnk."""
    return bank_name[3:9]


def check_pl(pl):
    """Character ids build filesystem paths, so only ever accept the real shape."""
    if not re.fullmatch(r"pl\d{4}", str(pl)):
        raise ValueError(f"bad character id: {pl!r}")
    return pl


def backup_path(bank_path):
    """The backup that sits beside a bank file (per bank, not per character)."""
    return pathlib.Path(bank_path).with_suffix(".bnk.chatterbox-backup")
