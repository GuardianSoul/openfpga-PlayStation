#!/usr/bin/env python3
"""Hardware test helper: rewrite a redump disc (one .bin per track) the way
ImgBurn writes it: one merged .bin, track 2's pregap left out of the file and
written as PREGAP, later tracks with their gaps in the file (INDEX 00/01).
That is the layout a tester's Rayman and Tomb Raider had on 0.5.3 (cue reader
fix 76800cb). The originals are not touched.

  python3 scripts/make_imgburn_disc.py "<card>/Assets/playstation/common/Rayman (USA)/Rayman (USA).cue" \\
      "<card>/Assets/playstation/common/ImgBurn test - Rayman (USA)"
"""
import argparse
from pathlib import Path
import re
import shutil

SECTOR = 2352


def msf(text):
    m, s, f = map(int, text.split(':'))
    return (m*60 + s)*75 + f


def frames(n):
    return f'{n//4500:02}:{n//75%60:02}:{n%75:02}'


def parse(cue):
    """[(file, [(track, mode, {index: frame within file})])] of a redump cue."""
    files = []
    for line in cue.read_text(errors='replace').splitlines():
        words = line.split()
        if not words: continue
        if words[0] == 'FILE':
            files.append((re.search(r'"(.+)"', line).group(1), []))
        elif words[0] == 'TRACK':
            files[-1][1].append((int(words[1]), words[2], {}))
        elif words[0] == 'INDEX':
            files[-1][1][-1][2][int(words[1])] = msf(words[2])
        elif words[0] == 'PREGAP':
            raise SystemExit('the source cue already has PREGAP lines')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('cue', type=Path)
    parser.add_argument('out', type=Path, help='new folder for the ImgBurn-style copy')
    args = parser.parse_args()
    files = parse(args.cue)
    if len(files) < 2 or any(len(tracks) != 1 for _, tracks in files):
        raise SystemExit('needs a redump cue with one .bin per track and at least two tracks')
    name = 'ImgBurn test - ' + args.cue.stem   # its own game name, never grouped with the original
    args.out.mkdir(parents=True, exist_ok=False)
    bin_path = args.out/f'{name}.bin'
    lines = [f'FILE "{bin_path.name}" BINARY']
    at = 0   # frames written so far
    need = sum((args.cue.parent/f).stat().st_size for f, _ in files)
    if shutil.disk_usage(args.out).free < need + (256 << 20):
        args.out.rmdir(); raise SystemExit(f'not enough free space for {need} bytes plus 256 MB')
    part = bin_path.with_suffix('.bin.part')
    with open(part, 'wb') as merged:
        for k, (file, [(track, mode, index)]) in enumerate(files):
            skip = 0
            lines.append(f'  TRACK {track:02} {mode}')
            if k == 1 and 0 in index:
                # Track 2: the pregap leaves the file and becomes PREGAP.
                skip = index[1] - index[0]
                lines.append(f'    PREGAP {frames(skip)}')
                lines.append(f'    INDEX 01 {frames(at)}')
            else:
                for i in sorted(index):
                    lines.append(f'    INDEX {i:02} {frames(at + index[i])}')
            with open(args.cue.parent/file, 'rb') as source:
                source.seek(skip*SECTOR)
                shutil.copyfileobj(source, merged, 1 << 20)
                at += (source.tell() // SECTOR) - skip
        merged.flush()
    part.rename(bin_path)
    (args.out/f'{name}.cue').write_text('\r\n'.join(lines) + '\r\n')
    print('\n'.join(lines))
    print(f'wrote {bin_path} ({bin_path.stat().st_size} bytes)')


if __name__ == '__main__': main()
