#!/usr/bin/env python3
"""Request physical minimum routing delay on the XA FIFO write-data cone.

Adds an independent net-delay lower bound; original setup/hold checks remain.
No HDL, clock, FIFO protocol or sample latency changes. Fresh fit required.
"""
import argparse
import json
import math
from pathlib import Path
import shutil
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


def prepare_project(directory,expected_top_sha256,expected_xa_sha256,*,minimum_ns=.5):
    if not math.isfinite(minimum_ns) or not .25<=minimum_ns<=2:raise ValueError('Require .25..2ns minimum XA net delay')
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';q=qsf.read_text();core=directory/'core_top.sv'
    assigned=assignments(directory,q)
    xa=[p for kind,p in assigned if kind=='VHDL_FILE' and p.name=='cd_xa.vhd']
    if len(xa)!=1 or digest(xa[0])!=expected_xa_sha256 or digest(core)!=expected_top_sha256:
        raise ValueError('Expected identity-checked core and assigned XA source')
    if [p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='core_top.sv']!=[core]:raise ValueError('Expected one canonical top')
    work=directory/'xa-hold-route'
    if work.exists() or 'pocket_xa_hold_route' in q:raise ValueError('XA routing request already prepared')
    protected={str(p):digest(p) for p in (qsf,core,xa[0],directory/'pocket.sdc')}
    work.mkdir();scope=work/'scope.tcl';shutil.copyfile(ROOT/'scripts/pocket_xa_hold_scope.tcl',scope)
    config=work/'pocket_xa_hold_route.sdc';config.write_text(f'set pocket_xa_min_net_ns {minimum_ns!r}\nsource "{scope}"\n')
    assert all(digest(Path(p))==h for p,h in protected.items())
    qsf.write_text(q+f'\nset_global_assignment -name SDC_FILE "{config}"\n')
    report=dict(original=protected,minimum_net_ns=minimum_ns,preparer_sha256=digest(Path(__file__)),
        added=[dict(path=str(p),sha256=digest(p)) for p in (scope,config)],
        scope='Existing16clamped data bits to128RAMdata inputs in both XA ADPCM FIFOs',
        rtl_or_clock_changed=False,latency_changed=False,exceptions_added=False,
        setup_or_hold_relaxed=False,fit_verified=False,timing_verified=False,hardware_verified=False,production_selected=False)
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-top-sha256',required=True);p.add_argument('--expected-xa-sha256',required=True)
    p.add_argument('--minimum-ns',type=float,default=.5);a=p.parse_args()
    print(json.dumps(prepare_project(a.project,a.expected_top_sha256,a.expected_xa_sha256,minimum_ns=a.minimum_ns),indent=2))
