#!/usr/bin/env python3
"""Make docs/games.tsv, the title and serial list behind the game list page.

Reads libretro-database's redump-based "Sony - PlayStation.dat" (CC BY-SA
4.0) and writes one "serial<TAB>name" line per disc, sorted by name. The
page's report search and grouping (docs/compat.js) read it.

  python3 scripts/make_compat_games.py "Sony - PlayStation.dat" release/docs/games.tsv
"""
import argparse
from pathlib import Path
import re

GAME = re.compile(r'^game \(\n\tname "((?:[^"\\]|\\.)*)"\n((?:\t.*\n)*?)\)', re.M)
SERIAL = re.compile(r'\b(S[CL][A-Z]{2})-(\d{5})\b')
HEADER = ('# PlayStation disc serials and redump names, from libretro-database\n'
          '# (https://github.com/libretro/libretro-database, CC BY-SA 4.0,\n'
          '# https://creativecommons.org/licenses/by-sa/4.0/). Made by\n'
          '# scripts/make_compat_games.py; this file is CC BY-SA 4.0 too.\n')


def discs(text):
    seen = set()
    for match in GAME.finditer(text):
        name = match.group(1).replace('\\"', '"')
        serial = re.search(r'^\tserial "([^"]*)"', match.group(2), re.M)
        for prefix, number in SERIAL.findall(serial.group(1) if serial else ''):
            key = (f'{prefix}-{number}', name)
            if key not in seen:
                seen.add(key)
                yield key


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('dat', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rows = sorted(discs(args.dat.read_text(encoding='utf-8')), key=lambda r: (r[1].lower(), r[0]))
    if len(rows) < 1000: raise SystemExit(f'only {len(rows)} discs in {args.dat}; is it the PlayStation dat?')
    args.output.write_text(HEADER + ''.join(f'{s}\t{n}\n' for s, n in rows), encoding='utf-8')
    print(f'{len(rows)} discs in {args.output}')


if __name__ == '__main__':
    main()
