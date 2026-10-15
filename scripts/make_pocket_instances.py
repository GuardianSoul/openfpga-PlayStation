#!/usr/bin/env python3
"""Write APF instance JSONs for multi-track PlayStation discs on the Pocket SD card.

For builds packaged with --multi-track: each .cue under
Assets/<platform>/common gets Assets/<platform>/GuardianSoul.<core>/<game>.json,
with the cue in slot 100 and its track files in slots 101, 102, ... (the PC
Engine CD core's convention). The core plays the files back to back: track 1
from physical LBA 150, every later file starting with its 150-sector pregap
(redump's INDEX 00 00:00:00 / INDEX 01 00:02:00). Cues that do not match that
layout (one merged .bin, or pregaps outside the files) are reported and
skipped.

  python3 scripts/make_pocket_instances.py /path/to/sd-card --core PSXEarlyTracks
"""
import argparse
import json
from pathlib import Path
import re


def parse_cue(path):
    """[(filename, [(track, type, {index: frames})])] in cue order."""
    files = []
    for line in path.read_text(errors='replace').splitlines():
        words = line.strip()
        if m := re.match(r'FILE\s+"(.+)"\s+\S+', words) or re.match(r'FILE\s+(\S+)\s+\S+', words):
            files.append((m.group(1), []))
        elif m := re.match(r'TRACK\s+(\d+)\s+(\S+)', words):
            if not files: raise ValueError('TRACK before FILE')
            files[-1][1].append((int(m.group(1)), m.group(2).upper(), {}))
        elif m := re.match(r'INDEX\s+(\d+)\s+(\d+):(\d+):(\d+)', words):
            if not files or not files[-1][1]: raise ValueError('INDEX before TRACK')
            mm, ss, ff = int(m.group(2)), int(m.group(3)), int(m.group(4))
            files[-1][1][-1][2][int(m.group(1))] = (mm*60+ss)*75+ff
    return files


def check(files, folder):
    """Reason the core cannot play this layout, or None."""
    if not files: return 'no FILE entries'
    for n, (name, tracks) in enumerate(files):
        if len(tracks) != 1: return f'{name}: {len(tracks)} tracks in one file (merged image)'
        number, kind, index = tracks[0]
        if not (folder/name).is_file(): return f'{name}: file missing'
        if (folder/name).stat().st_size % 2352: return f'{name}: not whole 2352-byte sectors'
        if n == 0:
            if kind == 'AUDIO' or index.get(1) != 0: return 'track 1 must be data with INDEX 01 at 00:00:00'
        elif kind != 'AUDIO' or index.get(0, 0) != 0 or index.get(1) != 150:
            return f'track {number}: expected AUDIO with INDEX 00 00:00:00 and INDEX 01 00:02:00'
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('card', type=Path, help='SD card root')
    parser.add_argument('--core', default='PSXEarlyTracks', help='core name after GuardianSoul.')
    parser.add_argument('--platform', default='playstation')
    parser.add_argument('--max-tracks', type=int, help="track slots the core declares (default: read from the core's data.json on the card, else 8)")
    args = parser.parse_args()
    if args.max_tracks is None:
        import json
        data = args.card/'Cores'/f'GuardianSoul.{args.core}'/'data.json'
        try:
            slots = json.loads(data.read_text())['data']['data_slots']
            args.max_tracks = sum(1 for s in slots if isinstance(s.get('id'), int) and 101 <= s['id'] <= 199)
        except (OSError, KeyError, ValueError): args.max_tracks = 8
    common = args.card/'Assets'/args.platform/'common'
    out = args.card/'Assets'/args.platform/f'GuardianSoul.{args.core}'
    written = skipped = 0
    for cue in sorted(common.rglob('*.cue')):
        folder = cue.parent
        try: files = parse_cue(cue)
        except ValueError as error: files, reason = None, str(error)
        else: reason = check(files, folder)
        if reason is None and len(files) > args.max_tracks: reason = f'{len(files)} track files (core takes {args.max_tracks})'
        if reason:
            print(f'skip {cue.relative_to(common)}: {reason}'); skipped += 1; continue
        data_path = folder.relative_to(common).as_posix()
        slots = [{'id': 100, 'filename': cue.name}] + [{'id': 101+k, 'filename': name} for k, (name, _) in enumerate(files)]
        instance = {'instance': {'magic': 'APF_VER_1', 'data_path': data_path + '/' if data_path != '.' else '',
                                 'data_slots': slots}}
        out.mkdir(parents=True, exist_ok=True)
        (out/(cue.stem + '.json')).write_text(json.dumps(instance, indent=2) + '\n')
        print(f'wrote {cue.stem}.json ({len(files)} track{"s" if len(files) > 1 else ""})'); written += 1
    print(f'{written} instance files in {out}, {skipped} skipped')


if __name__ == '__main__':
    main()
