"""Readers and editors for the game's own formats.

Three modules, one per format, none depending on anything outside the standard
library:

    banks    .bnk sound banks (BKHD/DIDX/DATA), and the editing operations
    pck      .pck packages, the AKPK container the streamed audio lives in
    siero    data.i and the data.N archives beside it

Only banks writes. The other two read. Import from the module you need, e.g.
`from chatterbox.banks import MediaBank`; the package itself pulls in nothing,
so `python -m chatterbox.siero` runs cleanly.
"""
