"""python -m transcribe <command>

  bake         re-transcribe the atlas through a local omni server
  ensemble     ASR cross-check + gated merge over one character
  corrections  overlay human-verified fixes onto the atlas
  eval         score sources against the ground-truth corpus
"""
import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    argv = sys.argv[2:]
    if cmd == "bake":
        from transcribe.bake import main as run
        return run(argv)
    if cmd == "ensemble":
        from transcribe.ensemble import main as run
        return run(argv)
    if cmd == "corrections":
        from transcribe.corrections import main as run
        return run(argv)
    if cmd == "eval":
        from transcribe.evaluate import main as run
        return run(argv)
    print(__doc__.strip())
    return 0 if cmd in ("", "-h", "--help") else 1


if __name__ == "__main__":
    raise SystemExit(main())
