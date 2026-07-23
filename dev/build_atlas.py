#!/usr/bin/env python3
"""Merge per-character JSONs into the published Atlas (CSV + JSON).

Usage: build_atlas.py [atlas_dir] [out_prefix]
"""
import csv
import json
import pathlib
import re
import sys

NAMES = json.loads((pathlib.Path(__file__).resolve().parent.parent
                    / "chatterbox" / "characters.json").read_text())

LABEL_RE = re.compile(r'^PL\d{4}_vo_([A-Z]+)_(.*)$')
CATEGORIES = {
    'ATK': 'Attack', 'SP': 'Skill/Link', 'DMG': 'Damage taken', 'NAV': 'Battle callout',
    'DUO': 'Duo/Pair', 'CMM': 'Emote/Command', 'ETC': 'Misc', 'MOV': 'Movement',
}
FIELDS = ['character', 'pl_id', 'bank', 'wem_id', 'label', 'category', 'ui_source',
          'group', 'variant', 'transcript', 'transcript_source', 'confidence',
          'duration_s', 'audio_source', 'prefetch_s', 'sample_rate', 'channels', 'silent',
          'jp_wem_id', 'jp_text', 'jp_literal', 'jp_confidence']


def ui_source(cat, variant):
    """Which in-game menu triggers this line.

    Both wheels live under the CMM category, but they split by label shape:
    CMM_emo_* are the physical emote wheel (greet, sit, pushup, rock-paper-scissors),
    everything else under CMM is the quick-chat wheel (thanks, please_heal, careful).
    Note the internal names differ from the player-facing ones -- CMM_emo_hello is
    the "greet" emote, not a "hello" one.
    """
    if cat != 'CMM':
        return ''
    return 'Emote wheel' if variant.startswith('emo_') else 'Chat wheel'


def rows(atlas_dir):
    for f in sorted(pathlib.Path(atlas_dir).glob('pl*.json')):
        pl = f.stem
        for wem_id, r in json.loads(f.read_text())['lines'].items():
            label = r.get('label') or ''
            m = LABEL_RE.match(label)
            cat, variant = (m.group(1), m.group(2)) if m else ('', '')
            # group = variant with trailing indices and partner suffix stripped
            grp = re.sub(r'(_\d+)+$', '', re.sub(r'_(PL\d{4}).*$', r'_\1', variant))
            yield {
                'character': NAMES.get(pl, pl),
                'pl_id': pl,
                'bank': r.get('bank') or '',
                'wem_id': wem_id,
                'label': label,
                'category': CATEGORIES.get(cat, cat),
                'ui_source': ui_source(cat, variant),
                'group': grp,
                'variant': variant,
                'transcript': r.get('transcript') or '',
                # recogniser's own average log probability; near 0 is confident,
                # strongly negative is usually wrong. Blank where nothing was said.
                # who produced the transcript: 'human' rows are ear-verified
                # and never overwritten by rebakes; the rest name the model
                'transcript_source': r.get('source_model', '') if r.get('transcript') else '',
                'confidence': r.get('confidence') if r.get('transcript') else '',
                'duration_s': r.get('duration_s'),
                # where the full audio lives
                'audio_source': r.get('source', 'bank'),
                # how much the .bnk holds when streamed (what other tools see)
                'prefetch_s': r.get('prefetch_s'),
                'sample_rate': r.get('sample_rate'),
                'channels': r.get('channels'),
                'silent': 'yes' if r.get('peak') == 0.0 else '',
                # the Japanese twin: what's said on the JP track and its
                # direct English translation (a separate localization)
                'jp_wem_id': r.get('jp', {}).get('wem_id', ''),
                'jp_text': r.get('jp', {}).get('text', ''),
                'jp_literal': r.get('jp', {}).get('literal', ''),
                'jp_confidence': r.get('jp', {}).get('confidence', ''),
            }


def main():
    atlas_dir = sys.argv[1] if len(sys.argv) > 1 else 'build/atlas-full'
    prefix = sys.argv[2] if len(sys.argv) > 2 else 'build/gbfr-voice-atlas'
    all_rows = list(rows(atlas_dir))
    with open(f'{prefix}.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(all_rows)
    pathlib.Path(f'{prefix}.json').write_text(json.dumps(all_rows, indent=1))
    chars = len({r['pl_id'] for r in all_rows})
    spoken = sum(1 for r in all_rows if r['transcript'])
    streamed = sum(1 for r in all_rows if r['audio_source'] == 'stream')
    wheels = sum(1 for r in all_rows if r['ui_source'])
    print(f"{len(all_rows)} lines / {chars} characters / {spoken} transcribed")
    print(f"{streamed} recovered from streamed .pck (invisible to bank-only tools)")
    print(f"{wheels} lines tagged to an in-game wheel (emote / chat)")
    print(f"-> {prefix}.csv  {prefix}.json")


if __name__ == '__main__':
    main()
