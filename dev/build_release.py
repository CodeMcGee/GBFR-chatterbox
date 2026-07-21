#!/usr/bin/env python3
"""Assemble the Windows release zip.

    python dev/build_release.py [--version 0.1.0] [--out build]

Pulls the source from the repo, the dataset from data/per-character into
atlas/, and the Windows halves of tools/ and python/. Writes the zip and its
SHA256. See BUILDING.md for where the binaries come from.
"""
import argparse
import hashlib
import pathlib
import sys
import zipfile

HERE = pathlib.Path(__file__).resolve().parent.parent

SOURCE = ["serve.py", "README.md", "BUILDING.md", "LICENSE",
          "THIRD-PARTY-LICENSES.md"]
# the library ships whole, including the data files it loads
PACKAGE = "chatterbox"
BATCH = ["run.bat", "reapply.bat"]        # rewritten CRLF; Windows is unreliable with LF
# Linux builds sit in tools/ too and must not ship in a Windows zip.
WINDOWS_SUFFIXES = {".exe", ".dll", ".txt", ".pyd", ".zip", ".cat", "._pth"}


def windows_only(d: pathlib.Path):
    return sorted(p for p in d.rglob("*")
                  if p.is_file() and p.suffix.lower() in WINDOWS_SUFFIXES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--out", default="build")
    a = ap.parse_args()

    tools, python, atlas = HERE / "tools", HERE / "python", HERE / "data/per-character"
    for d in (tools, python):
        if not d.is_dir():
            sys.exit(f"{d} is missing. See BUILDING.md for how to populate it.")
    if not (tools / "vgmstream-cli.exe").exists():
        sys.exit("tools/vgmstream-cli.exe is missing. See BUILDING.md.")

    out = HERE / a.out
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / f"chatterbox-v{a.version}.zip"

    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in SOURCE:
            z.write(HERE / name, name); n += 1
        for p in sorted((HERE / PACKAGE).iterdir()):
            if p.is_file() and p.suffix != ".pyc":
                z.write(p, f"{PACKAGE}/{p.name}"); n += 1
        for name in BATCH:
            text = (HERE / name).read_text().replace("\r\n", "\n").replace("\n", "\r\n")
            z.writestr(name, text); n += 1
        for p in sorted(atlas.glob("pl*.json")):
            z.write(p, f"atlas/{p.name}"); n += 1
        for d in (tools, python):
            for p in windows_only(d):
                z.write(p, f"{d.name}/{p.relative_to(d)}"); n += 1

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (zip_path.with_suffix(".zip.sha256")).write_text(f"{digest}  {zip_path.name}\n")
    print(f"{zip_path}  ({zip_path.stat().st_size / 1048576:.1f} MB, {n} files)")
    print(f"sha256  {digest}")


if __name__ == "__main__":
    main()
