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

Multi-disc games on cores packaged with --disc-menu (they declare slots
201+): cues named "<game> (Disc n)..." are combined into one "<game>.json",
disc n's track k in slot 100*n+k; the core's Disc setting switches discs.
Per-disc files this script wrote before are removed.

LibCrypt (cores packaged with --libcrypt, which declare slot 99): a PAL disc's
redump .sbi file next to its cue (same name, or the only .sbi in the folder)
goes into slot 99, and the core reads its protected-sector list from it.

One-file cores (packaged with --one-file-disc, which declare a .trk slot 102):
each disc is one .bin with all its tracks in slot 100*n+1 and a 1,600-byte
track table (.trk, the core's 400 track-info words) in slot 100*n+2, so
discs with any number of tracks (up to 99) play. A cue with one merged .bin
is used as it is, with the table written next to it; per-track files are
copied once into "<cue name> (one file).bin" (the originals stay). Tracks may
have any pregap stored in the image; a PREGAP outside it, or a data track
after track 1, is not supported. With --convert, .chd files are extracted as
one merged .bin instead of split. A disc's .sbi goes into the table (its
LibCrypt mask in word 3) instead of a slot.

Multi-disc games (names with "(Disc N)") share one memory card: the Pocket
names a save after the game file, so each disc would otherwise get its own
card and a save made before a disc change would not show on the next disc.
Each disc's instance names slot 16 "<game without (Disc N)>.sav". The Pocket
looks for an instance-named save under Saves/<platform>/common/<data_path>, so
these instances use an empty data_path and give the disc files with their
folder. The shared card is Saves/<platform>/common/<game>.sav; when it does
not exist yet it starts as a copy of the core's newest per-disc card.

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
LIBCRYPT_SLOT = 99
CARD_SLOT = 16
HELP_ENTRY = '! How to add games (see README).json'   # package_release.py's placeholder
DISC = re.compile(r' \(Disc \d+\)')
DISC_N = re.compile(r'\s*\(Disc (\d+)\)', re.I)
# Other tags in parentheses (region, release, languages) can differ between
# a game's discs (e.g. FFIX: "(Disc 1) (v1.1)" .. "(USA) (Disc 4)
# (Rev 1)"), so grouping uses the title without them.
RELEASE = re.compile(r'\s*\([^()]*\)')


def shared_card_name(stem):
    """'Game (USA) (Disc 2)' -> 'Game (USA).sav'; None for single-disc games."""
    return DISC.sub('', stem, count=1) + '.sav' if DISC.search(stem) else None


def seed_shared_card(card, platform, core, stems, name):
    """Start Saves/<platform>/common/<name> from the newest per-disc card. Never overwrites."""
    shared = card/'Saves'/platform/'common'/name
    if shared.exists(): return None
    own = [card/'Saves'/platform/f'GuardianSoul.{core}'/(s + '.sav') for s in stems]
    own = [f for f in own if f.is_file()]
    if not own: return None
    newest = max(own, key=lambda f: f.stat().st_mtime)
    shared.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(newest, shared)
    return newest


def visible(folder, pattern):
    """Matching files under folder, sorted, without macOS '._' metadata files."""
    return sorted(p for p in folder.rglob(pattern) if not p.name.startswith('._'))


def find_sbi(cue):
    """The disc's .sbi: <cue name>.sbi, else the only .sbi in its folder."""
    same = cue.with_suffix('.sbi')
    if same.is_file(): return same
    found = sorted(cue.parent.glob('*.sbi'))
    return found[0] if len(found) == 1 else None


def parse_cue(path):
    """[(filename, [(track, type, {index: frames})])] in cue order."""
    files = []
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
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


def one_file_plan(files, folder):
    """(sources, tracks) for a one-file disc, or a reason string.

    sources: [(path, sectors)] concatenated in order; tracks: [(start,
    index01)] in sectors from the start of the image (start is INDEX 00, or
    INDEX 01 without one)."""
    if not files: return 'no FILE entries'
    sources, tracks, offset = [], [], 0
    for name, entries in files:
        path = folder/name
        if not path.is_file(): return f'{name}: file missing'
        size = path.stat().st_size
        if size % SECTOR or not size: return f'{name}: not whole 2352-byte sectors'
        for number, kind, index in entries:
            if not raw_track(kind): return f'track {number}: {kind} is not 2352-byte sectors'
            if 1 not in index: return f'track {number}: no INDEX 01'
            if index.get('pregap'): return f'track {number}: PREGAP outside the image'
            if tracks and kind != 'AUDIO': return f'track {number}: data after track 1'
            tracks.append((offset + index.get(0, index[1]), offset + index[1]))
        sources.append((path, size//SECTOR)); offset += size//SECTOR
    if not tracks or files[0][1][0][1] == 'AUDIO' or tracks[0] != (0, 0): return 'track 1 must be data with INDEX 01 at 00:00:00'
    if len(tracks) > 99: return f'{len(tracks)} tracks (a CD has at most 99)'
    if any(not a[1] < b[0] or not a[0] <= a[1] for a, b in zip(tracks, tracks[1:])) or not tracks[-1][1] < offset:
        return 'indexes out of order or past the image'
    if offset + PREGAP > 524288: return 'longer than the core\'s 19-bit LBAs'
    return sources, tracks


def bcd(value): return (value//10)*16 + value%10


# The 16 LibCrypt sector addresses (absolute BCD MM:SS:FF) behind mask bits
# 15..0, as Main_MiSTer support/psx/psx.cpp libCryptMask and psx_cd_stream.
LIBCRYPT_MSF = (0x030805, 0x030956, 0x031310, 0x031429, 0x031524, 0x031849, 0x032056, 0x032155,
                0x032317, 0x032412, 0x032503, 0x032828, 0x033219, 0x033356, 0x033451, 0x033542)


def libcrypt_mask(sbi):
    """16-bit mask from a redump .sbi: 4-byte header, 14-byte entries."""
    data, mask = sbi.read_bytes(), 0
    for offset in range(4, len(data) - 2, 14):
        msf = int.from_bytes(data[offset:offset+3], 'big')
        if msf in LIBCRYPT_MSF: mask |= 1 << (15 - LIBCRYPT_MSF.index(msf))
    return mask


def track_table(tracks, sectors, region=2, mask=0):
    """The core's 400 track-info words (scripts/psx_disc_host.py layout)."""
    end = sectors + PREGAP
    minutes, seconds = divmod(end//75, 60)
    words = [0]*400
    words[:4] = [(bcd(len(tracks)) << 8) | len(tracks), end, (bcd(minutes) << 8) | bcd(seconds), (region << 16) | mask]
    for k, (start, index01) in enumerate(tracks):
        minutes, seconds = divmod((index01 + PREGAP)//75, 60)
        last = (tracks[k+1][0] if k+1 < len(tracks) else sectors) + PREGAP - 1
        words[4*k+4:4*k+8] = [0 if k == 0 else start + PREGAP, last, ((k != 0) << 16) | (bcd(minutes) << 8) | bcd(seconds), 0]
    return words


def write_one_file(cue, files, sbi=None):
    """Write the one-file image (when the cue has several files) and its
    table; returns (bin path, table path, track count) or a reason string."""
    plan = one_file_plan(files, cue.parent)
    if isinstance(plan, str): return plan
    sources, tracks = plan
    total = sum(n for _, n in sources)
    if len(sources) == 1: image = sources[0][0]
    else:
        image = cue.parent/f'{cue.stem} (one file).bin'
        if not (image.is_file() and image.stat().st_size == total*SECTOR):
            partial = image.with_name(image.name + '.part')
            with open(partial, 'wb') as out:
                for path, _ in sources:
                    with open(path, 'rb') as src: shutil.copyfileobj(src, out, 1 << 22)
            os.replace(partial, image)
    table = image.with_suffix('.trk')
    data = b''.join(w.to_bytes(4, 'little') for w in track_table(tracks, total, mask=libcrypt_mask(sbi) if sbi else 0))
    if not (table.is_file() and table.read_bytes() == data): table.write_bytes(data)
    return image, table, len(tracks)


def extract_chd(chd, dest, chdman, split=True):
    """chdman extractcd into a scratch folder beside dest, then split_cue
    (or, without split, move the merged cue and image into dest)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chd-', dir=dest.parent) as scratch:
        cue = Path(scratch)/(chd.stem + '.cue')
        result = subprocess.run([chdman, 'extractcd', '-i', str(chd), '-o', str(cue),
                                 '-ob', str(cue.with_suffix('.bin')), '-f'], capture_output=True, text=True)
        if result.returncode or not cue.is_file():
            message = (result.stderr or result.stdout).strip().splitlines()
            raise ValueError('chdman failed' + (': ' + message[-1] if message else ''))
        if not split:
            dest.mkdir(parents=True, exist_ok=True)
            os.replace(cue.with_suffix('.bin'), dest/(chd.stem + '.bin'))
            os.replace(cue, dest/(chd.stem + '.cue'))
            return dest/(chd.stem + '.cue')
        return split_cue(cue, dest, move=True)


def convert(common, chdman, one_file=False):
    """Extract .chd files and split merged cues under common (one-file
    cores: extract only, merged cues stay as they are). Returns counts."""
    done = failed = 0
    for image in visible(common, '*.chd') + ([] if one_file else visible(common, '*.cue')):
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
            out = extract_chd(image, dest, chdman, not one_file) if image.suffix == '.chd' else split_cue(image, dest)
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
    parser.add_argument('--libcrypt', action=argparse.BooleanOptionalAction, default=None,
                        help="list .sbi files in slot 99 (default: when the core's data.json declares slot 99)")
    parser.add_argument('--shared-cards', action=argparse.BooleanOptionalAction, default=None,
                        help="one memory card per multi-disc game (default: when the core's data.json declares slot 16)")
    parser.add_argument('--one-file', action=argparse.BooleanOptionalAction, default=None,
                        help="one .bin plus a .trk table per disc (default: when the core's data.json declares a .trk slot 102)")
    parser.add_argument('--max-tracks', type=int, help="track slots the core declares (default: read from the core's data.json on the card, else 8)")
    args = parser.parse_args()
    data = args.card/'Cores'/f'GuardianSoul.{args.core}'/'data.json'
    try: declared = json.loads(data.read_text())['data']['data_slots']
    except (OSError, KeyError, ValueError): declared = None
    if args.one_file is None:
        args.one_file = declared is not None and any(s.get('id') == 102 and 'trk' in s.get('extensions', []) for s in declared)
    if args.max_tracks is None:
        args.max_tracks = 8 if declared is None else sum(1 for s in declared if isinstance(s.get('id'), int) and 101 <= s['id'] <= 199)
    if args.shared_cards is None:
        args.shared_cards = declared is None or any(s.get('id') == CARD_SLOT for s in declared)
    if args.libcrypt is None:
        args.libcrypt = declared is not None and any(s.get('id') == LIBCRYPT_SLOT for s in declared)
    common = args.card/'Assets'/args.platform/'common'
    out = args.card/'Assets'/args.platform/f'GuardianSoul.{args.core}'
    if args.convert:
        done, failed = convert(common, args.chdman, args.one_file)
        print(f'{done} images converted, {failed} failed')
    disc_tracks = {n: sum(1 for s in (declared or []) if isinstance(s.get('id'), int) and 100*n+1 <= s['id'] <= 100*n+99)
                   for n in range(2, 10)}
    discs_declared = 1 + sum(1 for n in range(2, 10) if disc_tracks[n])
    written = skipped = 0
    discs = {}
    games = {}   # --disc-menu cores: game name -> {disc number: (cue, files)}
    for cue in visible(common, '*.cue'):
        folder = cue.parent
        try: files = parse_cue(cue)
        except ValueError as error: files, reason = None, str(error)
        else: reason = None if args.one_file else check(files, folder)
        if args.one_file and folder.name == cue.stem and (folder.parent/cue.name).is_file(): continue  # split copy of a merged cue
        if reason is None and args.one_file:
            made = write_one_file(cue, files, find_sbi(cue))
            if isinstance(made, str): reason = made
            else: files = [(made[0].relative_to(folder).as_posix(), None), (made[1].relative_to(folder).as_posix(), None)]; tracks_n = made[2]
        if reason is None and not args.one_file and len(files) > args.max_tracks: reason = f'{len(files)} track files (core takes {args.max_tracks})'
        if reason and (folder/cue.stem/(cue.stem + '.cue')).is_file(): continue  # converted copy is listed
        if reason:
            print(f'skip {cue.relative_to(common)}: {reason}'); skipped += 1; continue
        disc = DISC_N.search(cue.stem)
        if disc and discs_declared > 1:
            name = DISC_N.sub('', cue.stem)
            game = RELEASE.sub('', name).strip()
            # Two releases of the same disc: keep the one whose release tags
            # match disc 1's (decided below), else the first.
            games.setdefault(game, {}).setdefault(int(disc.group(1)), []).append((cue, files, RELEASE.findall(name)))
            continue
        data_path = folder.relative_to(common).as_posix()
        slots = [{'id': 100, 'filename': cue.name}] + [{'id': 101+k, 'filename': name} for k, (name, _) in enumerate(files)]
        sbi = find_sbi(cue) if args.libcrypt or args.one_file else None
        if sbi and not args.one_file: slots.append({'id': LIBCRYPT_SLOT, 'filename': sbi.name})
        prefix = data_path + '/' if data_path != '.' else ''
        shared = shared_card_name(cue.stem) if args.shared_cards else None
        if shared:
            for s in slots: s['filename'] = prefix + s['filename']
            slots.append({'id': CARD_SLOT, 'filename': shared})
            discs.setdefault(shared, []).append(cue.stem)
            prefix = ''
        instance = {'instance': {'magic': 'APF_VER_1', 'data_path': prefix, 'data_slots': slots}}
        out.mkdir(parents=True, exist_ok=True)
        (out/(cue.stem + '.json')).write_text(json.dumps(instance, indent=2) + '\n')
        count = tracks_n if args.one_file else len(files)
        print(f'wrote {cue.stem}.json ({count} track{"s" if count > 1 else ""}{", one file" if args.one_file else ""}{", LibCrypt " + sbi.name if sbi else ""})'); written += 1
    for game, found in sorted(games.items()):
        first_tags = found.get(1, [(None, None, [])])[0][2]
        parts = {n: next(((c, f) for c, f, t in cands if t == first_tags), cands[0][:2]) for n, cands in found.items()}
        numbers = sorted(parts)
        if numbers != list(range(1, len(numbers)+1)) or len(numbers) > discs_declared:
            print(f'skip {game}: discs {numbers} (core takes discs 1-{discs_declared})'); skipped += 1; continue
        too_long = [] if args.one_file else [n for n in numbers[1:] if len(parts[n][1]) > disc_tracks[n]]
        if too_long:
            print(f'skip {game}: disc {too_long[0]} has more track files than the core declares'); skipped += 1; continue
        root = Path(os.path.commonpath([str(parts[n][0].parent) for n in numbers]))
        rel = lambda cue, name: (cue.parent/name).relative_to(root).as_posix()
        cue1 = parts[1][0]
        slots = [{'id': 100, 'filename': rel(cue1, cue1.name)}]
        for n in numbers:
            cue, files = parts[n]
            slots += [{'id': 100*n+1+k, 'filename': rel(cue, name)} for k, (name, _) in enumerate(files)]
        sbi = find_sbi(cue1) if args.libcrypt else None
        if sbi: slots.append({'id': LIBCRYPT_SLOT, 'filename': rel(cue1, sbi.name)})
        data_path = root.relative_to(common).as_posix()
        prefix = data_path + '/' if data_path != '.' else ''
        shared = shared_card_name(cue1.stem) if args.shared_cards else None
        if shared:   # the same shared card as the per-disc files would use
            for s in slots: s['filename'] = prefix + s['filename']
            slots.append({'id': CARD_SLOT, 'filename': shared})
            discs.setdefault(shared, []).extend(parts[n][0].stem for n in numbers)
            prefix = ''
        instance = {'instance': {'magic': 'APF_VER_1', 'data_path': prefix, 'data_slots': slots}}
        out.mkdir(parents=True, exist_ok=True)
        # Named after disc 1 without its disc tag, as before tags were ignored
        # ("Final Fantasy VII (USA).json").
        name = DISC_N.sub('', cue1.stem).strip()
        (out/(name + '.json')).write_text(json.dumps(instance, indent=2) + '\n')
        for n in numbers:   # per-disc files from earlier runs
            (out/(parts[n][0].stem + '.json')).unlink(missing_ok=True)
        print(f'wrote {name}.json ({len(numbers)} discs)'); written += 1
    if written:   # the release's "how to add games" placeholder
        (out/HELP_ENTRY).unlink(missing_ok=True)
    for name, stems in sorted(discs.items()):
        seeded = seed_shared_card(args.card, args.platform, args.core, stems, name)
        if seeded: print(f'shared card {name} starts from {seeded.name}')
    print(f'{written} instance files in {out}, {skipped} skipped'
          + (f', {len(discs)} multi-disc games share a card' if discs else ''))


if __name__ == '__main__':
    main()
