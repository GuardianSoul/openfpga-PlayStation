#!/usr/bin/env python3
"""Inventory local disc images without extracting or uploading game contents."""
import argparse
import json
from pathlib import Path
import sys
from disc_image import ImageError, inspect_disc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', type=Path, default=Path('roms'))
    parser.add_argument('--hash', action='store_true', help='Compute SHA-256 over each complete BIN')
    parser.add_argument('--output', type=Path, default=Path('build/assets/discs.json'))
    args = parser.parse_args()
    cues = sorted(path for path in args.root.rglob('*') if path.suffix.lower() == '.cue')
    if not cues:
        sys.exit(f'No CUE files found under {args.root}')
    discs, errors = [], []
    for cue in cues:
        try:
            disc = inspect_disc(cue, args.hash)
            discs.append(disc)
            print(f"{cue.parent.name}: {disc['boot_file']} | {disc['licence_area']} | {disc['track_sectors']} sectors")
        except (ImageError, OSError, UnicodeError) as error:
            errors.append({'cue': str(cue), 'error': str(error)})
            print(f'{cue}: {error}', file=sys.stderr)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'schema': 1, 'discs': discs, 'errors': errors}, indent=2) + '\n')
    print(f'Local inventory: {args.output}')
    print('Checks cover image/boot metadata; game compatibility and EDC/ECC integrity remain untested.')
    return bool(errors)


if __name__ == '__main__':
    sys.exit(main())
