#!/usr/bin/env python3
"""Add an opt-in physical hold margin to Native's same-clock 2x transfers.

This strengthens derived hold uncertainty without relaxing any path, changing
RTL or changing clocks. It is an implementation request, not proof of closure;
every new project still needs a fresh fit and all-corner/internal/I/O audits.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil

from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments, scoped_hold_text
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare_project(directory, expected_top_sha256, *, margin_ns=0.15):
    if not math.isfinite(margin_ns) or not 0.05 <= margin_ns <= 0.5:
        raise ValueError('Require 0.05..0.5 ns additional hold uncertainty')
    directory = Path(directory).resolve()
    qsf = directory/'apf_top.qsf'
    core = directory/'core_top.sv'
    q = qsf.read_text()
    if digest(core) != expected_top_sha256:
        raise ValueError('Captured top does not match expected identity')
    if [p for k,p in assignments(directory,q) if k=='SYSTEMVERILOG_FILE'
            and p.name=='core_top.sv'] != [core]:
        raise ValueError('Expected the canonical copied core_top.sv exactly once')
    work = directory/'same-2x-hold'
    if work.exists() or 'pocket_2x_hold' in q:
        raise ValueError('Same-clock 2x hold margin already prepared')
    sources = inputs(directory)
    constraints = [Path(s['path']) for s in sources if
        s['assignment'] in ('SDC_FILE','SDC_SOURCE','PROJECT')
        and Path(s['path']).suffix in ('.sdc','.tcl')]
    derives = 0
    for p in constraints:
        text = p.read_text()
        _, removed = scoped_hold_text(text)
        if removed:
            raise ValueError('Remove inherited clock-level cuts before requesting hold margin')
        if re.search(r'^\s*(?:set|remove)_clock_uncertainty\b',text,re.M):
            raise ValueError('Existing uncertainty override needs an explicit combined review')
        derives += len(re.findall(r'^\s*derive_clock_uncertainty\s*(?:#.*)?$',text,re.M))
    if derives < 1:
        raise ValueError('Expected inherited derive_clock_uncertainty')
    source = ROOT/'scripts/pocket_2x_hold_scope.tcl'
    source_sha = digest(source)
    before = {s['path']:s['sha256'] for s in sources}
    work.mkdir()
    scope = work/'scope.tcl'
    shutil.copyfile(source,scope)
    config = work/'pocket_2x_hold.sdc'
    config.write_text(f'set pocket_2x_hold_ns {margin_ns!r}\nsource "{scope}"\n')
    if digest(scope) != source_sha or any(digest(Path(p))!=h for p,h in before.items()):
        raise ValueError('Project or scope changed while preparing hold margin')
    qsf.write_text(q+f'\nset_global_assignment -name SDC_FILE "{config}"\n')
    report = dict(additional_hold_uncertainty_ns=margin_ns, add_to_derived=True,
        inherited_derive_calls=derives,
        same_physical_edge_enabled=True, scope='CPU 2x -> same CPU 2x clock only',
        before=before, added=[dict(path=str(p),sha256=digest(p)) for p in (scope,config)],
        preparer_sha256=digest(Path(__file__)),
        scope_original=dict(path=str(source),sha256=source_sha),
        false_paths_added=False, multicycles_added=False, hold_relaxed=False,
        rtl_or_clock_changed=False, opt_in=True, fit_verified=False,
        timing_verified=False, hardware_verified=False,
        limitations=['An extra constraint does not repair an already compiled database',
            'Require a fresh fit and all-corner setup/hold, pin and CDC audits',
            'Only the reviewed 67.7376MHz CPU 2x clock geometry is supported',
            'Cross-clock paths and the other whole-core gates remain unchanged'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path)
    parser.add_argument('--expected-top-sha256',required=True)
    parser.add_argument('--margin-ns',type=float,default=0.15)
    args = parser.parse_args()
    print(json.dumps(prepare_project(args.project,args.expected_top_sha256,
        margin_ns=args.margin_ns),indent=2))
