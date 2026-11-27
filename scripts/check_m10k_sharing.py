#!/usr/bin/env python3
"""Fail a fit whose Fitter RAM Summary places two different RAMs in the same M10K block.

Why (area-061, 2026-11-26): when the design needs more M10K than the device has, Quartus packs two RAMs into one
block. In 0.6.1 Rc2/Rc3 (310 needed, 308 available) it put the XA ADPCM zigzag ROM in the same M10K as the GTE
FastDiv ROM (and the sinc phase80 ROM with the MDEC chroma ROM), and XA streamed audio became noisy on the Pocket.
Every clean build had no shared block. This check makes such a fit fail instead of shipping silently.

Usage: check_m10k_sharing.py FIT_RPT
  exit 0: no M10K location holds two RAMs; exit 1: shared blocks listed; exit 2: report unreadable.
  Any sharing fails: the summary's Clock Mode column says single/dual clock per RAM, not which clock, and the XA case
  (zigzag ROM + GTE FastDiv ROM) is two 'Single Clock' ROMs on different clocks.
"""
import sys
from collections import defaultdict


def ram_rows(path):
    lines = open(path, errors='replace').read().splitlines()
    starts = [i for i, l in enumerate(lines) if l.startswith('; Fitter RAM Summary')]
    if not starts:
        raise ValueError('no Fitter RAM Summary')
    header, rows = None, []
    for line in lines[starts[0] + 1:]:
        if not line.startswith(';'):
            if header and rows:
                break
            continue
        cells = [c.strip() for c in line.split(';')[1:-1]]
        if header is None:
            header = cells
            continue
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    if not header or 'Location' not in header:
        raise ValueError('RAM summary has no Location column')
    return rows


def shared_blocks(rows):
    users = defaultdict(list)
    for row in rows:
        for loc in row.get('Location', '').split(','):
            loc = loc.strip()
            if loc.startswith('M10K'):
                users[loc].append(row)
    return {loc: rs for loc, rs in users.items() if len({r['Name'] for r in rs}) > 1}


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        rows = ram_rows(argv[1])
    except (OSError, ValueError) as error:
        print(f'check_m10k_sharing: {error}')
        return 2
    bad = shared_blocks(rows)
    if not bad:
        print(f'check_m10k_sharing: PASS, {len(rows)} RAMs, no M10K block holds two RAMs')
        return 0
    print(f'check_m10k_sharing: FAIL, {len(bad)} M10K block(s) hold two RAMs (M10K over-subscribed, fitter packed them):')
    for loc, rs in sorted(bad.items()):
        print(f'  {loc}:')
        for r in rs:
            print(f"    {r['Name']}  [{r.get('Mode', '?')}, {r.get('Clock Mode', '?')}]")
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
