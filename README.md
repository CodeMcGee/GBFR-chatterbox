# GBFR Chatterbox

A dataset of every English battle voice line in *Granblue Fantasy: Relink*, and
a small Windows tool for muting or swapping them.

> **This edits your game files. Use it at your own risk.** It backs up every
> file before changing it and tries hard to be safe, but no warranty is given.
> If it corrupts your install, breaks your game, sets your PC on fire, or kicks
> your dog, I can't be held responsible. You can always restore everything by
> verifying the game files through Steam.

> **On GBFRDataTools.** Earlier versions of this project used
> [Nenkai](https://github.com/Nenkai)'s
> [GBFRDataTools](https://github.com/Nenkai/GBFRDataTools) to read the game's
> archives. Its author raised concerns about his work being used in AI-related
> projects and was weighing licence changes. The transcripts here come from a
> speech recognition model, so the archive reader was reimplemented from scratch
> as [`siero.py`](siero.py). No code of his is used or redistributed here, and
> the release bundles no third-party archive tool. The format research was his.

> **Unofficial fan project.** Not affiliated with, endorsed by, or associated
> with Cygames or Bandai Namco. All game names, characters and content belong to
> their respective owners. No game audio is redistributed.

> **This is an experiment and may not be maintained.** It works, it is finished
> enough to be useful, and it is published in case it is useful to someone else.
> Do not count on updates, fixes, or support. Fork it freely.

---

## Download the tool

**[Download the latest release](../../releases/latest)** for Windows. Nothing to
install, and no account or internet connection needed once it is downloaded.

Do **not** use the green **Code** button at the top of this page. That gives you
the source code without the bundled Python runtime, and it will not run. Setup
instructions are in [Running it](#running-it-windows) below.

If you only want the data and not the tool, the dataset is right below and needs
no download at all.

---

## The dataset

**[`data/gbfr-voice-atlas.csv`](data/gbfr-voice-atlas.csv)** - 28,373 voice lines
across 29 playable characters. Per-character JSON in
[`data/per-character/`](data/per-character/).

| Column | |
|---|---|
| `character`, `pl_id` | Who says it |
| `wem_id` | The audio's id inside the sound bank |
| `label` | The developer's own filename for the line |
| `category` | Attack, Damage taken, Skill/Link, Battle callout, Emote, etc. |
| `ui_source` | For emote/chat lines, which in-game wheel triggers it |
| `group`, `variant` | The line's family, e.g. `reload` and `reload_02` |
| `transcript` | What is said (machine generated, see below) |
| `confidence` | The recogniser's own score for that transcript, blank if nothing is said |
| `duration_s` | Length of the complete line |
| `audio_source` | `bank` if resident, `stream` if recovered from the archives |
| `prefetch_s` | For streamed lines, how much sits in the bank |
| `sample_rate`, `channels`, `silent` | Audio properties |

### Why this was not just sitting in the sound banks

The game stores long or less-urgent lines in two pieces: a roughly 0.4 second
opening inside the `.bnk` sound bank, and the rest inside a `.pck` package in
the packed `data.i` archives. Latency-critical combat audio (attacks, damage,
dodges) is resident in full; emotes, battle callouts and party banter are not.

**About 14,500 of these 28,373 lines are split that way.** Any tool that reads
sound banks alone sees only the opening fragment, with no indication anything is
missing, and transcribes it as a complete line. That is where entries like
`"How's it ha-"` come from.

Those lines were reassembled from the `.pck` streams before transcription, so
the durations and transcripts here describe the whole line. If you want to do
the same, `siero.py` reads the `data.i` archive index and `pck.py` reads the
packages it yields.

### Why the text is not simply lifted from the game

It is not in there. Battle voice lines have no subtitle track.

All 176 English text files were extracted from the game's archives and
searched. They contain story cutscene dialogue (keyed `SNT_*`, matching the
cutscene audio), UI labels, skill names, tutorials and item descriptions.
There is no battle-voice text table. Of 300 sampled multi-word battle lines,
the 25 that matched game text verbatim all traced back to *story cutscenes*
that happen to use the same common phrase, and the distinctive ones - "Need
ammo", "Lock and load", "How's it hanging" - appear nowhere at all.

That is consistent with how the game works: combat barks are fired by the
audio engine and never displayed, so they carry lipsync markers but no text.

It also means there is no ground truth to check these transcripts against.

### Transcript accuracy - read this before trusting a line

**These transcripts are machine generated and a meaningful number of them are
wrong.** Treat them as a search index, not a script.

Real examples from this dataset:

| Actually said | Transcribed as |
|---|---|
| "Ha, boxed you in!" | "Ha! Bucks do win!" |
| "How's it hanging?" | "How's it ha-" *(fixed, was a truncation bug)* |
| Eugen's attack name | "The Antican Canona!" |

The failure modes, roughly in order of how often they bite:

- **Invented proper nouns.** Character names, attack names, places. These are
  words the recogniser has never heard.
- **Short shouted lines.** Most of this dataset is under two seconds, often
  yelled over combat, sometimes heavily processed. Speech recognition is
  trained on ordinary speech and degrades badly here.
- **Grunts and effort noises** become plausible-looking words.
- **Near-silent lines** sometimes produce invented text.

Most lines carry a `confidence` value, the recogniser's own average log
probability. Values near 0 are confident; strongly negative ones are usually
wrong. Filter on it if accuracy matters more than coverage to you.

Around 970 lines have an empty transcript and no confidence. Those are grunts,
dodges and effort noises with no words in them, not lines that went
unprocessed.

Ordinary sentences are largely reliable. Listen to anything that reads oddly,
or looks like a name, before believing it.

Corrections are welcome, though see the maintenance note above.

---

## The tool

Browse every line for a character, listen to it, and mute it or replace it with
another line from the same character. It reads **your** installed game files.
Nothing is uploaded or downloaded, and no account or connection is needed.

### Running it (Windows)

Download the release first, from the [link at the top](#download-the-tool). The
release includes a bundled Python, the audio decoder, and the dataset, so
nothing needs installing.

1. **Close Granblue Fantasy: Relink.** It must not be running.
2. **Right-click the downloaded zip, choose Properties, tick "Unblock", press
   OK.** Windows blocks files downloaded from the internet, and the block
   spreads to everything you extract. Do this before extracting.
3. **Extract the whole zip to a folder.** Your Desktop is fine. Do not run it
   from inside the zip.
4. **Double-click `run.bat`.**
5. A console window opens, and your browser should open to
   <http://127.0.0.1:8777/>. If it does not, type that address into your browser
   yourself. It is also printed in the console window.
6. Pick a character and a category, press play to listen.
7. Press **Mute** or **Swap** on lines you want changed, then **Apply**.
8. Close the console window when finished, and start the game.

> Leave the console window open while using it. That window *is* the program.

After extracting you should see:

```
run.bat        <- double-click this
python\        <- bundled Python, do not delete
atlas\         <- voice line data
tools\         <- audio decoder
chatterbox\    <- the program itself
serve.py
```

If `python\` or `atlas\` are missing, either the extraction did not finish, or
you downloaded the source instead of the release.

### "Windows protected your PC"

Windows shows this for any program it has not seen before. Click **More info**,
then **Run anyway**.

Your antivirus may also quarantine `tools\vgmstream-cli.exe`. That is a false
positive: vgmstream is open source, bundled unmodified, and credited at the
bottom of this page. If previews will not play, check your antivirus quarantine
first.

Nothing here connects to the internet. It serves a page to your own machine at
127.0.0.1 and reads your own game files.

### If it cannot find your game

It will create a file called `game-path.txt` next to `run.bat` and tell you so.
Open that file in Notepad, paste in your game folder, save it, and run
`run.bat` again.

To find the folder: in Steam, right-click **Granblue Fantasy: Relink**, choose
**Manage**, then **Browse local files**. Copy the path from the address bar.

---

## If something goes wrong

**Steam can undo everything.** Right-click **Granblue Fantasy: Relink** ->
**Properties** -> **Installed Files** -> **Verify integrity of game files**.
That restores every original voice file. Nothing else in your game is affected,
and your save is not touched.

Inside Chatterbox itself, **Undo everything** restores every character you have
changed, and **Undo this character** restores only the one on screen.

## Backups

The first time you press **Apply** for a character, the original file is copied
beside it ending in `.chatterbox-backup`. The undo buttons restore from it.

Your choices are also saved to `%APPDATA%\chatterbox\profile.json`, deliberately
outside the game folder so a game update or reinstall cannot remove them. If a
game patch wipes your changes, double-click **`reapply.bat`** to put them back.

---

## Things worth knowing

- Muting works on every line, including the streamed ones.
- **Swapping:** a replacement longer than the original gets cut off, because the
  game stops the voice when the action that triggered it ends. Prefer a line of
  similar length or shorter.
- Lip-sync follows the swapped line automatically.
- English battle voices only. Japanese, town chatter and story scenes are not
  covered.
- Playing online with modified files is your own call. Only sound is changed.

---

## Layout

| Path | |
|---|---|
| `run.bat`, `reapply.bat` | Windows launchers; double-click these |
| `serve.py` | Local server; finds the game via Steam's own library config |
| `chatterbox/` | The library, one module per game format |
| `chatterbox/banks.py` | `.bnk` sound banks (BKHD/DIDX/DATA), and the edits |
| `chatterbox/pck.py` | `.pck` packages, the streamed half of the audio |
| `chatterbox/siero.py` | `data.i` and the game archives; XXHash64, LZ4, FlatBuffers |
| `chatterbox/ui.html` | The interface |
| `chatterbox/silence.wem` | 361-byte silent clip injected when muting |
| `data/` | The published dataset |
| `tests/` | Test suite; not needed to run the tool |
| `dev/` | Dataset and release build scripts |

## For developers

A clone has the code and the dataset but none of the bundled binaries, so
`run.bat` will not work. You need Python 3.11+, and
[vgmstream](https://vgmstream.org) placed at `tools/vgmstream-cli` (or
`tools/vgmstream-cli.exe`) for audio previews. Then:

```
python serve.py
```

It falls back to `data/per-character` for the dataset automatically.

```
python tests/test_smoke.py   # boots the server, exercises every endpoint
python tests/test_banks.py   # the bank editor, the only thing that writes
python tests/test_backup.py  # guards the backup lifecycle against data loss
python tests/test_siero.py   # the archive reader: hash, LZ4, index layout
```

## Licence

MIT, see [LICENSE](LICENSE). The packaged release bundles a Windows Python and
[vgmstream](https://vgmstream.org), which decodes the audio, both under their
own terms and documented in
[THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md). Everything that reads the
game's own formats - sound banks, `.pck` packages, and the `data.i` archive
index - is implemented in this repository.
