"""python -m transcribe <command> - the transcription pipeline CLI.

Each command owns its own argument parser; this level only dispatches, so
run `python -m transcribe <command> --help` for per-command options.
"""
import argparse

COMMANDS = {
    "bake": ("transcribe.bake", "re-transcribe the atlas through a local omni server"),
    "ensemble": ("transcribe.ensemble", "ASR cross-check + gated merge for one character"),
    "corrections": ("transcribe.corrections", "overlay human-verified fixes onto the atlas"),
    "eval": ("transcribe.evaluate", "score sources against the ground-truth corpus"),
}


def main():
    """Parse the command name and dispatch to that module's main(argv)."""
    ap = argparse.ArgumentParser(
        prog="python -m transcribe",
        description=__doc__.splitlines()[0],
        epilog="commands:\n" + "\n".join(f"  {name:12s}{summary}"
                                  for name, (_module, summary) in COMMANDS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    args, rest = ap.parse_known_args()
    module = __import__(COMMANDS[args.command][0], fromlist=["main"])
    return module.main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
