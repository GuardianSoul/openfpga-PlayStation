#!/usr/bin/env python3
"""Check a fitted Pocket project against the release timing policy.

Runs scripts/release_timing.tcl on a private copy of the project and gets the
worst slack of every corner, check (setup, hold, recovery, removal) and
clock pair. The policy (docs/public-release.md, "Release timing"):

* Paths that start and end on the same clock must meet timing at every
  corner. Nothing can waive them.
* A negative slack between two different clocks passes only when an entry
  in support/release_timing_waivers.json covers it: the clock pair, the
  check, a floor the slack may not go below, and the reason it is safe.

  python3 scripts/release_timing.py <fitted project> [--propose]

--propose prints waiver entries for every uncovered crossing, to review and
give a reason before they go into the waiver file. The result is written to
build/release/timing.json; package_release.py reads it.
"""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from tool_paths import ROOT, find_tool

WAIVERS = ROOT/'support/release_timing_waivers.json'
# The CPU clocks, by period (ns); waivers may name these instead of the PLL path.
ALIASES = {'clk1x': 29.526, 'clk2x': 14.763, 'clk3x': 9.842}
LINE = re.compile(r'^RELEASE_TIMING (\S+) (setup|hold|recovery|removal) (\d+) (-?[\d.]+) (\S+) -> (\S+)\s*$')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def names(clock, periods):
    result = {clock}
    for alias, period in ALIASES.items():
        if abs(periods.get(clock, 0) - period) < 0.01: result.add(alias)
    return result


def covering(row, waivers, periods):
    froms, tos = names(row['from'], periods), names(row['to'], periods)
    for waiver in waivers:
        if waiver['check'] == row['check'] and any(fnmatch.fnmatchcase(n, waiver['from']) for n in froms) \
                and any(fnmatch.fnmatchcase(n, waiver['to']) for n in tos):
            return waiver
    return None


def evaluate(rows, periods, waivers):
    for waiver in waivers:
        if not waiver.get('reason') or waiver['reason'].startswith('TODO'): raise ValueError(f'waiver without a reason: {waiver}')
    failures, waived = [], []
    for row in rows:
        if row['slack'] >= 0: continue
        if row['from'] == row['to']:
            failures.append(dict(row, why='same-clock path')); continue
        waiver = covering(row, waivers, periods)
        if not waiver: failures.append(dict(row, why='crossing without a waiver'))
        elif row['slack'] < waiver['floor_ns']: failures.append(dict(row, why=f'below waiver floor {waiver["floor_ns"]}'))
        else: waived.append(dict(row, reason=waiver['reason']))
    return failures, waived


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('project', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'build/release/timing.json')
    parser.add_argument('--propose', action='store_true')
    parser.add_argument('--log', type=Path, help='parse an existing quartus_sta log instead of running it')
    args = parser.parse_args()
    project = args.project.resolve()
    if args.log: text = args.log.read_text()
    else:
        sta = find_tool('quartus_sh')
        if not sta: sys.exit('Quartus not found')
        work = ROOT/'build/release/timing-audit'
        if work.exists(): shutil.rmtree(work)
        shutil.copytree(project, work)       # project_open may rewrite the QSF
        run = subprocess.run([str(Path(sta).parent/'quartus_sta'), '-t', str(ROOT/'scripts/release_timing.tcl')],
                             cwd=work, capture_output=True, text=True, timeout=3600)
        text = run.stdout + run.stderr
        (work/'release-timing.log').write_text(text)
        if run.returncode: sys.exit(f'quartus_sta failed, see {work}/release-timing.log')
    # A failed read_sdc leaves every crossing unconstrained; the slacks are then meaningless.
    broken = re.findall(r'^.*(?:\(332008\)|Read_sdc failed|Error \(332\d+\)).*$', text, re.M)
    if broken: sys.exit('constraints did not load, timing table not trusted:\n  ' + '\n  '.join(broken[:10]))
    periods = {m[2]: float(m[1]) for m in re.finditer(r'^RELEASE_CLOCK (-?[\d.]+) (\S+)', text, re.M)}
    corners = re.search(r'^RELEASE_CORNERS (\d+)', text, re.M)
    rows = [dict(corner=m[1], check=m[2], paths=int(m[3]), slack=float(m[4]), **{'from': m[5], 'to': m[6]})
            for m in map(LINE.match, text.splitlines()) if m]
    if not corners or int(corners[1]) < 3 or not rows: sys.exit('timing table incomplete')
    waivers = json.loads(WAIVERS.read_text())['waivers']
    failures, waived = evaluate(rows, periods, waivers)
    if args.propose:
        seen = set()
        for row in failures:
            key = (row['from'], row['to'], row['check'])
            if row['why'] != 'crossing without a waiver' or key in seen: continue
            seen.add(key)
            worst = min(r['slack'] for r in failures if (r['from'], r['to'], r['check']) == key)
            print(json.dumps({'from': row['from'], 'to': row['to'], 'check': row['check'],
                              'floor_ns': round(worst - 0.05, 2), 'reason': 'TODO'}))
    report = dict(project=str(project), qsf_sha256=digest(project/'apf_top.qsf'), corners=int(corners[1]),
                  clocks=periods, passed=not failures, failures=failures, waived=waived,
                  waivers_sha256=digest(WAIVERS))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    for row in failures: print(f'FAIL {row["corner"]} {row["check"]} {row["slack"]:+.3f} {row["from"]} -> {row["to"]}: {row["why"]}')
    for row in waived: print(f'waived {row["corner"]} {row["check"]} {row["slack"]:+.3f} {row["from"]} -> {row["to"]}')
    print(('release timing passed' if not failures else 'release timing FAILED') + f': {args.output}')
    if failures: sys.exit(1)


if __name__ == '__main__': main()
