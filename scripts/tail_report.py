#!/usr/bin/env python3
"""Read the tight-tail groups of sign-off results (request key 'tail',
scripts/run_frozen_pocket_fit.py) and compare builds.

  python3 scripts/tail_report.py [label=]<frozen-fit-result.json or its directory> ...

Per build: every group (launch clock, latch clock, check, source register
bank, destination register bank), worst slack first. With two or more builds:
one row per group and one column per build with the worst setup / worst hold
slack. '>lim' means the build has no path of that group below its limit; '?'
means that build's tail is incomplete (a cut or failed report), so absence
proves nothing. A leading '*' marks a group that is below the limit in one
build and absent in another.
"""
import argparse
import json
from pathlib import Path
import re
import sys


def load(argument):
    label, _, path = argument.rpartition('=')
    path = Path(path)
    if path.is_dir(): path = path / 'frozen-fit-result.json'
    result = json.loads(path.read_text())
    if 'tail' not in result or 'tail_info' not in result:
        sys.exit(f'{path}: no tail in this result (the sign-off request had no "tail" key)')
    return dict(label=label or path.parent.name, path=str(path), groups=result['tail'], info=result['tail_info'],
                passed=result.get('release_timing_passed'))


def short(name, full=False):
    """Instance names only: 'core_top:ic|gpu:igpu|xCnt' -> 'ic|igpu|xCnt'."""
    return name if full else '|'.join(part.rpartition(':')[2] if '~' not in part else part for part in name.split('|'))


def corner(name):
    found = re.search(r'(slow|fast)\D*\d+mV\D*(\d+C)', name or '', re.I)
    return (found[1].lower() + found[2]) if found else str(name)


def build_text(build, full=False):
    info, groups = build['info'], sorted(build['groups'], key=lambda g: g['worst_slack_ns'])
    request = info['request']
    lines = [f"== {build['label']}: {len(groups)} groups; setup < {request['setup_below_ns']} ns, hold < "
             f"{request['hold_below_ns']} ns; {'complete' if info['complete'] else 'INCOMPLETE'}; release timing "
             f"{'passed' if build['passed'] else 'FAILED'}; hold-margin SDC removed: {info.get('cpu_hold_sdc_removed')}",
             'check  worst corner   paths clocks        from bank -> to bank']
    for g in groups:
        lines.append(f"{g['check']:5} {g['worst_slack_ns']:6.3f} {corner(g['worst_corner']):8} {g['paths']:5} "
                     f"{g['launch']}->{g['latch']}{' [io]' if g.get('io') else ''}  "
                     f"{short(g['from_bank'], full)} -> {short(g['to_bank'], full)}")
    for r in info['reports']:
        if r['truncated']:
            lines.append(f"TRUNCATED {r['check']} {r['corner']}: complete only below {r['complete_below_ns']} ns")
    lines += ['ERROR ' + e for e in info.get('errors', [])]
    for w in info.get('waived_excluded', []):
        lines.append(f"waived, not listed: {w['check']} {w['launch_clock']} -> {w['latch_clock']}: {w['paths']} rows, "
                     f"worst {w['worst_slack_ns']}")
    return '\n'.join(lines)


def comparison(builds):
    """Rows (marked, key, cells): key = (launch, latch, from bank, to bank); one cell per build
    = {'setup': slack or None, 'hold': slack or None}; marked = below the limit in one build
    and absent in another (for either check). Worst slack over all builds first."""
    table = {}
    for i, build in enumerate(builds):
        for g in build['groups']:
            cells = table.setdefault((g['launch'], g['latch'], g['from_bank'], g['to_bank']),
                                     [dict(setup=None, hold=None) for _ in builds])
            cells[i][g['check']] = g['worst_slack_ns']
    rows = []
    for key, cells in table.items():
        marked = any(any(c[check] is not None for c in cells) and any(c[check] is None for c in cells)
                     for check in ('setup', 'hold'))
        rows.append((marked, key, cells))
    return sorted(rows, key=lambda r: (min(v for c in r[2] for v in c.values() if v is not None), r[1]))


def comparison_text(builds, full=False):
    width = max(15, *(len(b['label']) for b in builds))
    lines = ['== comparison: worst setup / worst hold per build (ns); * = below the limit in one build, absent in another',
             '  ' + ' '.join(f"{b['label']:>{width}}" for b in builds) + '  clocks        from bank -> to bank']
    for marked, (launch, latch, source, sink), cells in comparison(builds):
        shown = []
        for build, cell in zip(builds, cells):
            absent = '>lim' if build['info']['complete'] else '?'
            shown.append('/'.join(f'{cell[c]:.3f}' if cell[c] is not None else absent for c in ('setup', 'hold')))
        lines.append(('* ' if marked else '  ') + ' '.join(f'{s:>{width}}' for s in shown) +
                     f'  {launch}->{latch}  {short(source, full)} -> {short(sink, full)}')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('results', nargs='+', help='[label=]frozen-fit-result.json or the directory holding it')
    parser.add_argument('--full', action='store_true', help='keep entity names in register banks')
    parser.add_argument('--compare-only', action='store_true', help='skip the per-build tables')
    args = parser.parse_args()
    builds = [load(a) for a in args.results]
    if not args.compare_only or len(builds) == 1:
        for build in builds: print(build_text(build, args.full) + '\n')
    if len(builds) > 1: print(comparison_text(builds, args.full))


if __name__ == '__main__': main()
