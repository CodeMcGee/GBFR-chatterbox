"""GBFR-chatterbox, standard library only.

Format readers/editors:

    banks    .bnk sound banks (BKHD/DIDX/DATA), and the editing operations
    pck      .pck packages, the AKPK container the streamed audio lives in
    siero    data.i and the data.N archives beside it

Application domains:

    game     locate the install; bank filenames and character ids
    store    profile / originals-manifest / review-flags side-cars
    library  read side: banks, atlas lines, previews, streamed .pck
    patching write side: backups, apply/revert/reapply
    app      App facade wiring the above together
    web      localhost server and UI

Import from the module you need; the package itself pulls in nothing, so
`python -m chatterbox.siero` runs cleanly.
"""
