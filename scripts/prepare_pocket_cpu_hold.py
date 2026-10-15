#!/usr/bin/env python3
"""Request additional physical hold margin on all related Native CPU pairs.

Adapts the existing same-2x mechanism to the complete three-clock family.
No RTL, phase, rate, exceptions or setup requirements change. Requires a fresh
fit; appending this to an existing database cannot repair its placement.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
from measure_pocket_seeds import inputs
from prepare_pocket_config_cdc import assignments,scoped_hold_text
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare_project(directory,expected_top_sha256,*,margin_ns=.10):
    if not math.isfinite(margin_ns) or not .05<=margin_ns<=.5:raise ValueError('Require .05..0.5ns additional hold margin')
    directory=Path(directory).resolve();core=directory/'core_top.sv';qsf=directory/'apf_top.qsf';q=qsf.read_text()
    if digest(core)!=expected_top_sha256 or [p for k,p in assignments(directory,q) if k=='SYSTEMVERILOG_FILE' and p.name=='core_top.sv']!=[core]:
        raise ValueError('Expected the identity-checked canonical copied top')
    work=directory/'cpu-hold-margin'
    if work.exists() or 'pocket_cpu_hold' in q:raise ValueError('CPU hold margin already prepared')
    before=inputs(directory);derives=0
    for row in before:
        p=Path(row['path'])
        if row['assignment'] not in ('SDC_FILE','SDC_SOURCE','PROJECT') or p.suffix not in ('.sdc','.tcl'):continue
        text=p.read_text();_,cuts=scoped_hold_text(text)
        if cuts:raise ValueError('Whole-clock cuts cannot qualify CPU hold margin')
        if re.search(r'^\s*(?:set|remove)_clock_uncertainty\b',text,re.M):raise ValueError('Existing uncertainty override needs a combined review')
        derives+=len(re.findall(r'^\s*derive_clock_uncertainty\s*(?:#.*)?$',text,re.M))
    if not derives:raise ValueError('Expected device-derived clock uncertainty')
    work.mkdir();scope=work/'scope.tcl';shutil.copyfile(ROOT/'scripts/pocket_cpu_hold_scope.tcl',scope)
    config=work/'pocket_cpu_hold.sdc';config.write_text(f'set pocket_cpu_hold_ns {margin_ns!r}\nsource "{scope}"\n')
    assert all(digest(Path(s['path']))==s['sha256'] for s in before)
    qsf.write_text(q+f'\nset_global_assignment -name SDC_FILE "{config}"\n')
    report=dict(additional_hold_ns=margin_ns,add_to_derived=True,same_physical_edge_enabled=True,
        scope='All nine ordered pairs of the three related CPU output clocks',inherited_derive_calls=derives,
        original_inputs=before,added=[dict(path=str(p),sha256=digest(p)) for p in (scope,config)],
        preparer_sha256=digest(Path(__file__)),rtl_or_clock_changed=False,exceptions_added=False,
        hold_relaxed=False,fit_verified=False,timing_verified=False,hardware_verified=False,production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-top-sha256',required=True);p.add_argument('--margin-ns',type=float,default=.10)
    a=p.parse_args();print(json.dumps(prepare_project(a.project,a.expected_top_sha256,margin_ns=a.margin_ns),indent=2))
