#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Catch SDC constraints whose name pattern matches nothing, before a fit is spent on them.

psx-fit #1143 fitted with two new constraints that Quartus ignored ("*|core_top:ic|..." matches
no register: timing names start at "core_top:ic|"), and only the sign-off showed the crossing
untimed. Two checks:

  pre-queue (no Quartus):  check_sdc_patterns.py PROJECT --baseline OLDER_PROJECT --map-rpt MAP.RPT
      Every pattern in PROJECT's SDC files that OLDER_PROJECT does not have must (1) have an
      instance prefix that matches a hierarchy node of the mapped design (Full Hierarchy Name
      column of any map report of this design family, top level stripped) and (2) end in a
      name that occurs as a word in one of the project's own HDL sources.

  after a fit:             check_sdc_patterns.py --log QUARTUS.LOG --baseline-log OLDER.LOG
      Any "Ignored filter ... could not be matched" pattern the older fit's log does not have
      fails (the baseline graph has about 50 old ones that are known).

Exit 0 when nothing new is unmatched.
"""
import argparse, fnmatch, re, sys
from pathlib import Path

GETTER = re.compile(r'get_(?:registers|keepers|pins|nets|cells)\s+\{([^}]*)\}')


def patterns(project):
    out = {}
    for sdc in sorted(Path(project).rglob('*.sdc')):
        for n, line in enumerate(sdc.read_text(errors='replace').splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            for group in GETTER.findall(line):
                for p in group.split():
                    out.setdefault(p, f'{sdc.name}:{n}')
    return out


def hierarchy(map_rpt):
    nodes = {''}
    for line in Path(map_rpt).read_text(errors='replace').splitlines():
        m = re.search(r';\s*(\|apf_top(?:\|[^ ;]+)*)\s*;', line)
        if m:
            nodes.add(m.group(1)[len('|apf_top'):].lstrip('|'))
    return nodes


def words(project):
    found = set()
    qsf = (Path(project) / 'apf_top.qsf').read_text(errors='replace')
    # the lookahead keeps ".vhd" whole (".v" alone matched first and no VHDL file was ever read)
    for path in re.findall(r'_FILE\s+"?([^"\s]+\.(?:sv|v|vhd|svh|vh))(?=["\s]|$)', qsf):
        p = Path(path) if Path(path).is_absolute() else Path(project) / path
        if p.is_file():
            found.update(re.findall(r'[A-Za-z_][A-Za-z0-9_]*', p.read_text(errors='replace')))
    return found


def prequeue(project, baseline, map_rpt):
    new = {p: where for p, where in patterns(project).items() if p not in patterns(baseline)}
    nodes, names = hierarchy(map_rpt), words(project)
    bad = []
    for p, where in sorted(new.items()):
        prefix, _, leaf = p.rpartition('|')
        prefix_ok = not prefix or any(fnmatch.fnmatchcase(n, prefix) for n in nodes if n)
        ident = re.sub(r'\[[^\]]*\]', '', leaf).replace('*', '')
        ident = re.sub(r'~.*$', '', ident)
        # a VHDL record register is named signal.field: each part must be a name of the HDL
        leaf_ok = all(part in names or any(part in w for w in names if len(part) > 6)
                      for part in ident.split('.') if part)
        print(f"{'ok  ' if prefix_ok and leaf_ok else 'FAIL'} {where:22} {p}"
              + ('' if prefix_ok else '   <- no hierarchy node matches the prefix')
              + ('' if leaf_ok else '   <- name not found in the HDL'))
        if not (prefix_ok and leaf_ok):
            bad.append(p)
    print(f'{len(new)} new patterns, {len(bad)} unmatched')
    return not bad


def ignored(log):
    return set(re.findall(r'Ignored filter at [^:]+: (\S+) could not be matched', Path(log).read_text(errors='replace')))


def postfit(log, baseline_log):
    new = sorted(ignored(log) - ignored(baseline_log))
    for p in new:
        print('FAIL ignored by Quartus:', p)
    print(f'{len(ignored(log))} ignored filters, {len(new)} not in the baseline')
    return not new


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('project', nargs='?', type=Path)
    ap.add_argument('--baseline', type=Path)
    ap.add_argument('--map-rpt', type=Path)
    ap.add_argument('--log', type=Path)
    ap.add_argument('--baseline-log', type=Path)
    a = ap.parse_args()
    ok = True
    if a.project:
        ok &= prequeue(a.project, a.baseline, a.map_rpt)
    if a.log:
        ok &= postfit(a.log, a.baseline_log)
    sys.exit(0 if ok else 1)
