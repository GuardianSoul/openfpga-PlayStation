#!/usr/bin/env python3
"""Route diagnostic readback through a coherent RAM-backed status snapshot.

The live CD service and guest-facing outputs retain their direct connections.
This opt-in adaptation requires the existing independent config_reset channel.
No event/command semantics are claimed for periodically sampled diagnostics.
"""
import argparse
import json
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments,scoped_hold_text
from prepare_resource_targets import digest
from tool_paths import ROOT


FIELDS = [('isPaused','12000014','0'),('region_out','120000a0','4:3'),
    ('resetFromCD','120000b8','2'),('cd_hps_req','120000bc','1'),
    ('cd_hps_lba','120000c0','36:5'),('cd_hps_lba_sim','120000c4','68:37')]
INSTANCE = '''    // Diagnostic levels only: the newest complete source snapshot arrives.
    // Live CD requests still use the original disc_stream handshake.
    wire [68:0] status_snapshot;
    psx_status_cdc status_readback (
        .src_clk(clk1x),.dst_clk(clk_74a),.reset_async(config_reset),
        .src_data({cd_hps_lba_sim,cd_hps_lba,region_out,resetFromCD,cd_hps_req,isPaused}),
        .dst_valid(),.dst_data(status_snapshot));
'''


def adapt_top(text):
    if 'psx_status_cdc' in text or 'status_snapshot' in text:
        raise ValueError('Diagnostic snapshot already present')
    if text.count('psx_config_cdc #(.WIDTH(4)) host_config (')!=1 or text.count(
            '(* preserve, dont_merge *) reg config_reset=1;')!=1:
        raise ValueError('Expected the reviewed independent host configuration reset')
    # These addresses are diagnostic only in the reviewed dedicated-service
    # top. Do not coalesce the sole CD command interface of another design.
    live=list(re.finditer(r'^\s*psx_cd_stream\b',text,re.M))
    if len(live)!=1:
        raise ValueError('Expected one dedicated live CD stream')
    start=live[0].start();body=text[start:text.index(');',start)+2]
    if not re.search(r'\bdisc_stream\s*\(',body) or any(len(re.findall(
            r'\.'+n+r'\s*\(\s*'+n+r'\s*\)',body))!=1 for n in ('cd_hps_req','cd_hps_lba')):
        raise ValueError('Live CD stream must retain its direct request/LBA connections')
    original = text
    for name,address,bits in FIELDS:
        before=f"        32'h{address}: service_read={name};"
        if text.count(before)!=1:
            raise ValueError('Unsupported diagnostic readback anchor: '+name)
        text=text.replace(before,f"        32'h{address}: service_read=status_snapshot[{bits}];",1)
    anchor='    reg [31:0] service_read;'
    if text.count(anchor)!=1:
        raise ValueError('Expected one diagnostic service-read mux')
    text=text.replace(anchor,INSTANCE+anchor,1)
    if restore_top(text)!=original:
        raise ValueError('Status adaptation changed non-diagnostic source')
    return text


def restore_top(text):
    if text.count(INSTANCE)!=1:
        raise ValueError('Expected one exact diagnostic snapshot instance')
    text=text.replace(INSTANCE,'',1)
    for name,address,bits in FIELDS:
        before=f"        32'h{address}: service_read=status_snapshot[{bits}];"
        if text.count(before)!=1:
            raise ValueError('Snapshot field binding changed: '+name)
        text=text.replace(before,f"        32'h{address}: service_read={name};",1)
    return text


def prepare_project(directory,expected_top_sha256):
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';core=directory/'core_top.sv'
    q=qsf.read_text();sdc=directory/'pocket.sdc'
    if digest(core)!=expected_top_sha256:
        raise ValueError('Captured top does not match expected identity')
    if [p for k,p in assignments(directory,q) if k=='SYSTEMVERILOG_FILE'
            and p.name=='core_top.sv']!=[core]:
        raise ValueError('Expected canonical copied core_top.sv exactly once')
    _,removed=scoped_hold_text(sdc.read_text())
    if removed:
        raise ValueError('Remove inherited whole-clock cuts before status adaptation')
    if 'psx_status_cdc' in q:
        raise ValueError('Status module already assigned')
    original_top=core.read_text();changed=adapt_top(original_top)
    work=directory/'status-cdc'
    if work.exists():raise ValueError('Status output already exists; select a fresh project')
    before={str(p):digest(p) for p in (qsf,core,sdc)}
    work.mkdir();added=[]
    for original,name,kind in [(ROOT/'rtl/pocket/psx_status_cdc.sv','psx_status_cdc.sv','SYSTEMVERILOG_FILE'),
            (ROOT/'scripts/pocket_status_scope.tcl','status.sdc','SDC_FILE')]:
        p=work/name;sha=digest(original);shutil.copyfile(original,p)
        if digest(p)!=sha:raise ValueError('Status dependency changed while copying')
        q+=f'\nset_global_assignment -name {kind} "{p}"'+(' -library work' if kind!='SDC_FILE' else '')+'\n'
        added.append(dict(path=str(p),sha256=sha,original=str(original)))
    if any(digest(Path(p))!=h for p,h in before.items()):
        raise ValueError('Copied project changed during status preparation')
    core.write_text(changed);qsf.write_text(q)
    report=dict(before=before,added=added,
        top=dict(path=str(core),sha256=digest(core)),preparer_sha256=digest(Path(__file__)),
        field_bindings=[dict(signal=n,address='0x'+a,snapshot_bits=b) for n,a,b in FIELDS],
        source_clock='clk1x',destination_clock='clk_74a',ram_logical_words=2,ram_word_bits=69,
        non_diagnostic_source_identity_verified=restore_top(core.read_text())==original_top,
        latest_levels_may_coalesce=True,guest_or_memory_service_path_changed=False,
        fit_verified=False,timing_verified=False,hardware_verified=False,opt_in=True,
        limitations=['Diagnostic reads see the latest delivered snapshot, not an instantaneous core value',
            'Different APF register transactions need not use the same snapshot',
            'Require mapped M10K inference, toggle/reset geometry and full all-corner fit audits',
            'Other legacy memory-card diagnostic fields and functional/video CDCs remain separate'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project',type=Path);parser.add_argument('--expected-top-sha256',required=True)
    args=parser.parse_args();print(json.dumps(prepare_project(args.project,args.expected_top_sha256),indent=2))
