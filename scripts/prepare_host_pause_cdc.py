#!/usr/bin/env python3
"""Add stable host pause/PAL levels to an existing four-bit config snapshot.

Private copied project only. No numeric timing-bound or reset change; all six
payload bits use the existing held/ACK protocol. Host pause must be held until the
core acknowledges it; this is not a lossless pulse/command interface.
"""
import argparse
import json
from pathlib import Path
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import digest
from tool_paths import ROOT


PATCHES=[
    ('    reg pause=0;','    reg pause_host=0;\n    wire pause;'),
    ('    reg isPal=0;','    reg isPal_host=0;\n    wire isPal;'),
    ("if(bridge_wr && bridge_addr==32'h11000018) pause<=bridge_wr_data[0:0];",
     "if(bridge_wr && bridge_addr==32'h11000018) pause_host<=bridge_wr_data[0:0];"),
    ("if(bridge_wr && bridge_addr==32'h11000154) isPal<=bridge_wr_data[0:0];",
     "if(bridge_wr && bridge_addr==32'h11000154) isPal_host<=bridge_wr_data[0:0];"),
    ('psx_config_cdc #(.WIDTH(4)) host_config (','psx_config_cdc #(.WIDTH(6)) host_config ('),
    ('.src_data({biosregion_host,region_host})','.src_data({isPal_host,pause_host,biosregion_host,region_host})'),
    ('.dst_data({biosregion,region})','.dst_data({isPal,pause,biosregion,region})')]


def adapt_top(original):
    text=original
    if original.count('.pause(pause)')!=1 or original.count('.isPal(isPal)')!=1:
        raise ValueError('Expected one raw host pause/PAL consumer')
    for before,after in PATCHES:
        if text.count(before)!=1:raise ValueError('Host pause adaptation anchor changed: '+before)
        text=text.replace(before,after,1)
    restored=text
    for before,after in reversed(PATCHES):restored=restored.replace(after,before,1)
    if restored!=original:raise ValueError('Unrestorable host pause source')
    return text


def prepare_project(directory,expected_top_sha256):
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';core=directory/'core_top.sv'
    assigned=assignments(directory,qsf.read_text())
    if [p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='core_top.sv']!=[core] or digest(core)!=expected_top_sha256:
        raise ValueError('Expected identity-checked canonical copied top')
    scope=[p for kind,p in assigned if kind=='SDC_FILE' and p.name=='host.sdc']
    if len(scope)!=1 or scope[0].read_text()!=(ROOT/'scripts/pocket_config_scope.tcl').read_text():
        raise ValueError('Expected original reviewed four-bit host scope')
    text=adapt_top(core.read_text());old_scope=scope[0].read_text();new_scope=old_scope
    for name in ('payload','dst_data'):
        before='config_regs '+name+' 4'
        if new_scope.count(before)!=1:raise ValueError('Unknown host config width guard')
        new_scope=new_scope.replace(before,'config_regs '+name+' 6')
    new_scope='# Explicit six-bit snapshot: PAL + pause + BIOS region + region.\nset config_width 6\n'+new_scope
    if (text.count('.src_current(config_current)')!=1 or
            text.count('wire config_current;')!=1 or
            text.count("32'h12000158: service_read={31'b0,config_current};")!=1 or
            text.count('config_current')!=3):
        raise ValueError('Configuration current must remain diagnostic-only')
    # No new scope/dependency: the already assigned qualified bridge supports
    # WIDTH=6. Its held-payload, reset and clock contracts are unchanged.
    before={str(p):digest(p) for p in (core,scope[0],qsf)}
    if not all(digest(Path(p))==sha for p,sha in before.items()):raise ValueError('Parent changed while checking pause')
    core.write_text(text);scope[0].write_text(new_scope)
    report=dict(original_top_sha256=expected_top_sha256,top_sha256=digest(core),scope_sha256=digest(scope[0]),
        original_scope_sha256=before[str(scope[0])],payload_bits=6,patches=PATCHES,
        latest_levels_may_coalesce=True,source_pause_address='0x11000018',source_PAL_address='0x11000154',
        guest_reset_or_boot_gating_changed=False,numeric_bounds_changed=False,additional_exceptions=False,
        fit_verified=False,timing_verified=False,hardware_verified=False,production_selected=False)
    (directory/'host-pause-cdc.json').write_text(json.dumps(report,indent=2)+'\n');return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path);p.add_argument('--expected-top-sha256',required=True)
    a=p.parse_args();print(json.dumps(prepare_project(a.project,a.expected_top_sha256),indent=2))
