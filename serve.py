#!/usr/bin/env python3
"""GBFR-chatterbox entry point. run.bat calls this; the code lives in the
chatterbox package. Old imports (serve.App, serve.find_game, serve.NAMES)
keep working.

Usage: serve.py [--game <path>] [--port 8777] [--atlas <dir>]
"""
from chatterbox.app import App                                        # noqa: F401
from chatterbox.banks import SILENCE, MediaBank, atomic_write         # noqa: F401
from chatterbox.game import (APP_DIR, HERE, NAMES, battle_banks,      # noqa: F401
                             check_pl, find_game, pl_of)
from chatterbox.web import TOKEN, UI, main, make_handler              # noqa: F401

if __name__ == "__main__":
    main()
