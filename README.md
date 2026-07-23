# GBFR Chatterbox

A dataset of every English battle voice line in *Granblue Fantasy: Relink*.
The data is the project; the bundled Windows app is a toy for browsing and
listening to it against your own install - with muting and swapping thrown in
as a fun experiment.

> **This edits your game files. Use it at your own risk.** Every file is backed
> up before it is changed, and Steam's "Verify integrity of game files" restores
> everything. No warranty.

> **Unofficial fan project.** Not affiliated with Cygames or Bandai Namco. No
> game audio is redistributed. May not be maintained; fork freely.

> The game's archives are read by [`siero.py`](chatterbox/siero.py), written
> from scratch for this project. No third-party archive tool is used or bundled.

## Download the tool

**[Latest release](../../releases/latest)** (Windows). Nothing to install, no
internet needed after download. The green **Code** button is the source without
the bundled Python - it will not run.

## The dataset

**[`data/gbfr-voice-atlas.csv`](data/gbfr-voice-atlas.csv)** - 28,373 lines,
29 characters. Source of truth in [`data/per-character/`](data/per-character/);
see [`data/README.md`](data/README.md).

Columns: `character`, `pl_id`, `bank`, `wem_id`, `label`, `category`,
`ui_source`, `group`/`variant`, `transcript`, `transcript_source`,
`confidence`, `duration_s`, `audio_source`, `prefetch_s`, `sample_rate`,
`channels`, `silent`, and the Japanese twin: `jp_wem_id`, `jp_text` (what the
JP track says), `jp_literal` (its direct English translation), `jp_confidence`.

About 14,500 lines are stored split: ~0.4s in the `.bnk`, the rest streamed
from a `.pck` in the archives. Bank-only tools transcribe the fragment; these
lines were reassembled first, so durations and transcripts cover the whole line.

Battle lines have no subtitle track in the game files, so there is no ground
truth in the game data. **Most transcripts are machine generated and some are
wrong** - treat those as a search index. Weak spots: proper nouns, short
shouted lines, grunts. `transcript_source` says who produced each line:
`human` rows are ear-verified (a growing set, currently 160+) and protected
from rebakes; the rest name the model. `confidence` is the model's own score
(near 0 = confident, more negative = likelier wrong); filter on it if
accuracy matters, but treat it as a triage signal, never proof. Listen before
believing anything that reads oddly. How the transcripts are produced — and
the measurements behind every pipeline rule — is documented in
[TRANSCRIBING.md](TRANSCRIBING.md) and [EXPERIMENTS.md](EXPERIMENTS.md).

## The tool

A viewer for the dataset: browse a character's lines, listen to each against
your installed game files, and - the experiment part - mute or swap them.
Nothing is uploaded or downloaded.

**Review mode** (checkbox in the header) flattens every category into one
list sorted least-trustworthy-first, for correcting transcripts by ear: play
a line, then either tick the red box and type what it really says, or tick
the green box to mark it verified so it never resurfaces. Verified lines
sink to the bottom; your marks live in `%APPDATA%\chatterbox\flags.json`.

### Running it (Windows)

1. Close the game.
2. Right-click the downloaded zip -> Properties -> tick **Unblock** -> OK.
3. Extract the whole zip; do not run from inside it.
4. Double-click `run.bat`. Your browser opens <http://127.0.0.1:8777/>
   (address is also printed in the console; that window is the program).
5. Mute/Swap lines, press **Apply**, close the console, start the game.

**"Windows protected your PC"**: More info -> Run anyway. Antivirus flagging
`tools\vgmstream-cli.exe` is a false positive.

**Game not found**: it creates `game-path.txt` next to `run.bat`; paste your
game folder into it (Steam -> Manage -> Browse local files) and run again.

## Undo

- In the tool: **Undo everything** / **Undo this character**.
- Steam: Properties -> Installed Files -> **Verify integrity of game files**.
- First Apply saves the original beside the bank as `.chatterbox-backup`.
  Choices persist in `%APPDATA%\chatterbox\profile.json`; after a game patch,
  double-click `reapply.bat` to put them back.

## Worth knowing

- Muting works on every line, including streamed ones.
- A swapped-in line longer than the original gets cut off; prefer similar length.
- Lip-sync follows the swap. English battle voices only.
- Playing online with modified files is your own call.

## Layout

| Path | |
|---|---|
| `run.bat`, `reapply.bat` | Windows launchers |
| `serve.py` | Entry point |
| `chatterbox/` | The package: game formats + app domains, one module each |
| `data/` | The published dataset |
| `transcribe/` | Transcription pipeline ([TRANSCRIBING.md](TRANSCRIBING.md)) |
| `dev/` | Scratch, model-server scripts, release build |
| `tests/` | Test suite |

## For developers

A clone has no bundled binaries. Python 3.11+, [uv](https://docs.astral.sh/uv/),
and [vgmstream](https://vgmstream.org) at `tools/vgmstream-cli` for previews.

```
python serve.py      # falls back to data/per-character automatically
uv run pytest        # test suite (--cov for the 70% coverage gate)
```

## Contributing

- **Transcript corrections:** use review mode - tick wrong lines and type
  the corrected words, tick the green box on lines you have confirmed - then
  open an issue with the contents of `%APPDATA%\chatterbox\flags.json`, or
  plain `"wem_id": "correct words"` pairs. Accepted fixes land in
  `transcribe/corrections.json`, the ground-truth corpus, and the next
  dataset build.
- **PRs welcome** for code, prompting, or transcription fixes.

## Licence

MIT, see [LICENSE](LICENSE). The release bundles Windows Python and
[vgmstream](https://vgmstream.org) under their own terms
([THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md)). All game-format readers
are implemented here.
