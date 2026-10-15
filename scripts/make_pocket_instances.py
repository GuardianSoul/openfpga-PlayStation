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

With --convert, the core cannot read CHD itself (its LZMA/zlib/FLAC hunks are
far too large to decode in the FPGA), so each .chd is extracted with MAME's
chdman into a <name>/ folder next to it, and each merged single-.bin cue is
split into one .bin per track in a <name>/ folder. The originals stay.

  python3 scripts/make_pocket_instances.py /path/to/sd-card --core PSXEarlyTracks
  python3 scripts/make_pocket_instances.py /path/to/sd-card --convert
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

SECTOR = 2352
PREGAP = 150


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
        elif m := re.match(r'(INDEX\s+(\d+)|PREGAP)\s+(\d+):(\d+):(\d+)', words):
            if not files or not files[-1][1]: raise ValueError(m.group(1).split()[0] + ' before TRACK')
            mm, ss, ff = int(m.group(3)), int(m.group(4)), int(m.group(5))
            key = int(m.group(2)) if m.group(2) else 'pregap'
            files[-1][1][-1][2][key] = (mm*60+ss)*75+ff
    return files


def raw_track(kind):
    return kind == 'AUDIO' or kind.endswith('/2352') or kind.endswith('/RAW')


def split_cue(cue, dest, move=False):
    """Write cue's single merged image as one file per track in the layout
    check() accepts: track 1 data from INDEX 01 00:00:00, every later track
    starting with its 150-sector pregap (zeros when the cue's PREGAP has no
    data in the image). Returns the new cue, or raises ValueError."""
    files = parse_cue(cue)
    if len(files) != 1: raise ValueError(f'{len(files)} FILE entries (expected one merged image)')
    name, tracks = files[0]
    source = cue.parent/name
    if not source.is_file(): raise ValueError(f'{name}: file missing')
    size = source.stat().st_size
    if size % SECTOR: raise ValueError(f'{name}: not whole 2352-byte sectors')
    sectors = size//SECTOR
    if not tracks or tracks[0][1] == 'AUDIO' or tracks[0][2].get(1) != 0 or tracks[0][2].get('pregap'):
        raise ValueError('track 1 must be data with INDEX 01 at 00:00:00')
    plan = []
    for k, (number, kind, index) in enumerate(tracks):
        if not raw_track(kind): raise ValueError(f'track {number}: {kind} is not 2352-byte sectors')
        if 1 not in index: raise ValueError(f'track {number}: no INDEX 01')
        start = index.get(0, index[1]) if k else 0
        if k:
            stored = index[1] - start
            if stored < 0 or stored + index.get('pregap', 0) != PREGAP:
                raise ValueError(f'track {number}: pregap is not 00:02:00')
            if kind != 'AUDIO': raise ValueError(f'track {number}: data after track 1')
        plan.append([number, kind, start, PREGAP - (index[1] - start) if k else 0])
    for k, entry in enumerate(plan):
        end = plan[k+1][2] if k+1 < len(plan) else sectors
        if not entry[2] < end <= sectors: raise ValueError(f'track {entry[0]}: indexes out of order or past the image')
        entry.append(end)
    dest.mkdir(parents=True, exist_ok=True)
    stem = cue.stem
    lines = []
    for number, kind, start, zeros, end in plan:
        track = f'{stem} (Track {number:02d}).bin' if len(plan) > 1 else f'{stem}.bin'
        if len(plan) == 1 and move:
            os.replace(source, dest/track)
        else:
            with open(source, 'rb') as src, open(dest/track, 'wb') as out:
                out.write(bytes(zeros*SECTOR))
                src.seek(start*SECTOR)
                left = (end-start)*SECTOR
                while left:
                    chunk = src.read(min(left, 1 << 22))
                    if not chunk: raise ValueError(f'{name}: short read')
                    out.write(chunk); left -= len(chunk)
        lines += [f'FILE "{track}" BINARY', f'  TRACK {number:02d} {kind}']
        lines += ['    INDEX 00 00:00:00', '    INDEX 01 00:02:00'] if number != plan[0][0] else ['    INDEX 01 00:00:00']
    out_cue = dest/(stem + '.cue')
    out_cue.write_text('\n'.join(lines) + '\n')
    return out_cue


def extract_chd(chd, dest, chdman):
    """chdman extractcd into a scratch folder beside dest, then split_cue."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chd-', dir=dest.parent) as scratch:
        cue = Path(scratch)/(chd.stem + '.cue')
        result = subprocess.run([chdman, 'extractcd', '-i', str(chd), '-o', str(cue),
                                 '-ob', str(cue.with_suffix('.bin')), '-f'], capture_output=True, text=True)
        if result.returncode or not cue.is_file():
            message = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError('chdman failed' + (': ' + message[-1] if message else ''))
        return split_cue(cue, dest, move=True)


def convert(common, chdman):
    """Extract .chd files and split merged cues under common. Returns counts."""
    done = failed = 0
    for image in sorted(common.rglob('*.chd')) + sorted(common.rglob('*.cue')):
        dest = image.parent/image.stem
        if image.parent.name == image.stem or (dest/(image.stem + '.cue')).is_file(): continue
        if image.suffix == '.cue':
            try: files = parse_cue(image)
            except ValueError: continue
            if len(files) != 1 or len(files[0][1]) < 2: continue
            if image.with_suffix('.chd').is_file(): continue
        elif not chdman:
            print(f'skip {image.relative_to(common)}: chdman not found (install MAME tools, or pass --chdman)'); failed += 1; continue
        try:
            out = extract_chd(image, dest, chdman) if image.suffix == '.chd' else split_cue(image, dest)
        except (ValueError, OSError) as error:
            shutil.rmtree(dest, ignore_errors=True)
            print(f'skip {image.relative_to(common)}: {error}'); failed += 1; continue
        print(f'converted {image.relative_to(common)} -> {out.relative_to(common)}'); done += 1
    return done, failed


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
    parser.add_argument('--convert', action='store_true', help='extract .chd files with chdman and split merged .bin cues into per-track folders first')
    parser.add_argument('--chdman', default=shutil.which('chdman'), help='chdman executable (default: from PATH)')
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
    if args.convert:
        done, failed = convert(common, args.chdman)
        print(f'{done} images converted, {failed} failed')
    written = skipped = 0
    for cue in sorted(common.rglob('*.cue')):
        folder = cue.parent
        try: files = parse_cue(cue)
        except ValueError as error: files, reason = None, str(error)
        else: reason = check(files, folder)
        if reason is None and len(files) > args.max_tracks: reason = f'{len(files)} track files (core takes {args.max_tracks})'
        if reason and (folder/cue.stem/(cue.stem + '.cue')).is_file(): continue  # converted copy is listed
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
