# Building Chatterbox

You do not need any of this to *use* Chatterbox. Download the release and
double-click `run.bat`. This file is for building it yourself, or for checking
that a release contains what it claims to.

## What a release contains

| Part | Source | Size |
|---|---|---|
| `serve.py`, `chatterbox/`, `*.bat` | this repo | small |
| `atlas\` | this repo, `data/per-character/` | 12 MB |
| `python\` | python.org Windows embeddable package | 22 MB |
| `tools\vgmstream-cli.exe` and its DLLs | vgmstream.org release | 11 MB |

Nothing is built from source. The two bundled pieces are downloaded unmodified
from upstream, and everything that reads the game's own formats is Python in
this repository.

## The game's formats

All three readers are implemented here, with no third-party archive tooling:

| File | Reads |
|---|---|
| `chatterbox/banks.py` | `.bnk` sound banks: BKHD, DIDX and DATA chunks |
| `chatterbox/pck.py` | `.pck` packages: the AKPK container the streamed audio lives in |
| `chatterbox/siero.py` | `data.i` and the `data.N` archives beside it |

`chatterbox/siero.py` needs the most explanation. `data.i` is a FlatBuffers
table holding, for each file, which chunk contains it, where in that chunk it
begins, and how long it is. Files are addressed only by hash, never by name:
the index stores a sorted array of XXHash64 values over the lowercased,
forward-slashed path. The reader needs three things, all in that one file and
all pure Python:

- **XXHash64**, to turn a path into the key the index is sorted on
- **A minimal FlatBuffers reader**, enough for one known root table
- **LZ4 block decompression**, for chunks the game stored compressed

Voice `.pck` chunks turn out to be stored uncompressed, since the audio is
already compressed, so extracting all 29 voice packages never touches the LZ4
path and takes well under a second.

Run it directly to check an install:

```sh
python -m chatterbox.siero "<game>/data.i" exists "sound/english(us)/vo_pl2700_02_00_00.pck"
python -m chatterbox.siero "<game>/data.i" extract /tmp/out "sound/english(us)/vo_pl2700_02_00_00.pck"
```

Running `python -m chatterbox.siero` with no arguments runs its self-checks: XXHash64
against the published reference vectors, and an LZ4 round trip over an
overlapping match, which is where hand-written decoders usually break.

## The bundled pieces

**Python.** The `python\` folder is the unmodified Windows embeddable package
from <https://www.python.org/downloads/windows/>, 3.12.8, amd64. Nothing is
installed into it. Chatterbox uses only the standard library.

**vgmstream.** `tools\vgmstream-cli.exe` and the codec DLLs beside it are the
unmodified Windows release from <https://vgmstream.org>. It decodes Wwise
Vorbis so the browser can play previews. It is needed for playback only, never
for editing: muting and swapping work without it.

Both are gitignored, so a fresh clone has neither. Download them and drop them
in place before building a release.

## Assembling the release zip

```sh
python dev/build_release.py
```

Collects the source, the dataset from `data/per-character/` into `atlas/`, and
the Windows halves of `tools/` and `python/`, then writes a versioned zip and
its SHA256. Linux binaries in `tools/` are filtered out.

## Running the tests

```sh
uv run pytest                # the whole suite
uv run pytest --cov          # same, with the 70% coverage gate
```

Each file also runs standalone (`python tests/test_banks.py`):
test_smoke boots the server and exercises every endpoint; test_banks covers
the bank editor, the only thing that writes; test_backup guards the backup
lifecycle; test_siero covers the archive reader (hash, LZ4, index layout).

test_smoke, test_backup and part of test_banks need a sample `_m.bnk` under `samples/`, which is not in the repo
because it is game data. Copy one out of your own install:

```
<game>/data/sound/English(US)/vo_pl0000_m.bnk
```

## Licences

Chatterbox is MIT. The two bundled pieces keep their own, reproduced in
[THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md): vgmstream is ISC-style
permissive, Python is under the PSF licence. No game assets are redistributed.
