#!/usr/bin/env python3
"""Add guarded SDRAM pin timing to a private Native clean-controller project.

No RTL/clock/phase change. Board flight is an explicit unqualified assumption.
CAS2 below its specified period rejects in TimeQuest unless analysis-only is
selected. Default adds no exceptions. The explicit reviewed CAS3 implementation
recipe uses the previous-beat setup relationship, without relaxing hold.
"""
import argparse
import json
import math
from pathlib import Path
import re
import shutil

from prepare_pocket_config_cdc import assignments, scoped_hold_text
from prepare_resource_targets import digest
from tool_paths import ROOT

# Per-READ edge/tag contract reviewed with the datasheet-timed model. Routed
# prototypes qualify only their exact compiled sources/projects. A controller
# revision needs a new capture review; every new project still needs a fresh
# full fit and pin/edge audit, even when port/mode syntax remains compatible.
REVIEWED_CAS3_CAPTURE_SHA256 = {
    '17b708d26aebcaebb4acfaa138bc550f5bfe5e7a14ebcb4e3e7f6f739627decb',
    'f8825c97d3ad7038992e84842232a53117be13747775effc30daeaf9d7f67465',
    # e467767: registered request/head/startup inputs; PHY, read tags, shift
    # and READ-command bodies unchanged. Timed data and late-DQ control checked.
    'd368cde48a2996f13a030a892c02bff747b5d10afd1cb2346b8a510f8fb60928',
    # 9341a90, DEFAULT SPU_FIRST=0 only: pin cells, capture state/results,
    # MRS and READ/tag bodies byte-identical to d368 above; strict CL3 model
    # and late-DQ control checked. wrapper_contract still rejects explicit
    # SPU_FIRST overrides. This is not a routed fit or priority-path signoff.
    'e96f00a8a26ab2c47b77ba6ed8a066f7653e1215e83872b2aa1d417a83753adf',
}


def fitted_input_delays(report, expected_taps):
    """Require all 16 requested D1 settings in the actual Fitter report.

    This proves the physical settings were used, not that timing passed.
    Actual setup/hold, capture edges and other clock domains need their audits.
    """
    if type(expected_taps) is not int or not 0<=expected_taps<=31:
        raise ValueError('Expected Cyclone V D1 setting 0..31')
    sections=report.split('; Delay Chain Summary ')
    if len(sections)!=2:
        raise ValueError('Expected one Fitter Delay Chain Summary')
    table=sections[1].split('; Pad To Core Delay Chain Fanout',1)[0]
    rows={}
    for line in table.splitlines():
        columns=[c.strip() for c in line.split(';')]
        if len(columns)<5 or not re.fullmatch(r'dram_dq\[\d+\]',columns[1]):continue
        name=columns[1]
        if name in rows or columns[2]!='Bidir':
            raise ValueError('Duplicate or non-bidirectional SDRAM delay row: '+name)
        value=columns[3].strip('()')
        if not value.isdigit() or int(value)!=expected_taps:
            raise ValueError('Fitted SDRAM D1 differs from request: '+name+' = '+columns[3])
        rows[name]=int(value)
    if set(rows)!={f'dram_dq[{i}]' for i in range(16)}:
        raise ValueError('Missing or unexpected SDRAM D1 pin coverage')
    return rows


def controller_contract(text, cas_override=None):
    """Resolve the known literal or 14e500e parameterized MRS contract.

    The source default is not the compiled instance's CAS when the wrapper
    overrides it. Unknown expressions/parameters fail before any file write.
    """
    modes = re.findall(r"cmd\s*<=\s*C_MRS;\s*a\s*<=\s*([^;]+);", text)
    if len(modes)!=1:
        raise ValueError('Expected one known BL2/CL2-or-3/single-write mode register')
    literal=re.fullmatch(r"13'h0?2([23])1",modes[0].strip())
    defaults=re.findall(r'parameter\s+integer\s+CAS_LATENCY\s*=\s*([23])\b',text)
    if literal and not re.search(r'\bCAS_LATENCY\b',text):
        if cas_override is not None:
            raise ValueError('Literal controller has no CAS parameter to override')
        cas=int(literal[1])
    elif re.fullmatch(r"CAS_LATENCY\s*==\s*3\s*\?\s*13'h0231\s*:\s*13'h0221",modes[0].strip()) and len(defaults)==1:
        cas=int(defaults[0]) if cas_override is None else cas_override
        if cas not in (2,3):raise ValueError('Unsupported active CAS parameter')
    else:
        raise ValueError('Unknown SDRAM mode-register expression/default')
    clock = r"\.datain_h\(1'b0\),\s*\.datain_l\(1'b1\),\s*\.outclock\(clk\),\s*\.dataout\(SDRAM_CLK\)"
    if len(re.findall(clock,text))!=1 or text.count('always @(posedge clk) dq_in <= SDRAM_DQ;')!=1:
        raise ValueError('Unknown forwarded-clock polarity or SDRAM input-capture contract')
    return cas


def wrapper_contract(text):
    instances=re.findall(r'psx_sdram_ctrl\s*#\(([^\n]+)\)\s+controller\s*\(',text)
    if len(instances)!=1 or text.count('.clk(clk3x), .clk_base(clk1x)')!=1:
        raise ValueError('Unknown single-chip Native clock/instance wiring')
    parameters=re.fullmatch(r'\s*\.SINGLE_CHIP\(1\)(?:,\s*\.CAS_LATENCY\(([23])\))?\s*',instances[0])
    if not parameters:raise ValueError('Unknown Native SDRAM parameter override')
    return int(parameters[1]) if parameters[1] else None


def project_contract(directory, expected_controller_sha256):
    directory=Path(directory).resolve()
    assigned=assignments(directory,(directory/'apf_top.qsf').read_text())
    controllers=[p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='psx_sdram_ctrl.sv']
    wrappers=[p for kind,p in assigned if kind=='SYSTEMVERILOG_FILE' and p.name=='psx_native_memory.sv']
    if len(controllers)!=1 or digest(controllers[0])!=expected_controller_sha256 or len(wrappers)!=1:
        raise ValueError('Expected one identity-checked clean Native SDRAM controller and wrapper')
    override=wrapper_contract(wrappers[0].read_text())
    cas=controller_contract(controllers[0].read_text(),override)
    return controllers[0],wrappers[0],cas,override


def prepare_project(directory, expected_controller_sha256, *, board_min_ns=0.0,
                    board_max_ns=0.5, analysis_only=False, implementation=False,
                    input_delay_taps=None):
    if not all(math.isfinite(x) for x in (board_min_ns,board_max_ns)) or not 0<=board_min_ns<=board_max_ns<=2:
        raise ValueError('Require finite 0 <= board minimum <= maximum <= 2 ns per leg')
    if input_delay_taps is not None and (
            not implementation or type(input_delay_taps) is not int or not 0<=input_delay_taps<=31):
        raise ValueError('D1 input taps require the reviewed I/O implementation and an integer 0..31')
    directory=Path(directory).resolve();qsf=directory/'apf_top.qsf';sdc=directory/'pocket.sdc'
    q=qsf.read_text();saved=sdc.read_text()
    if input_delay_taps is not None and re.search(r'^.*\bD1_DELAY\b.*\bdram_dq\b',q,re.M):
        raise ValueError('Conflicting SDRAM D1 input-delay assignment')
    # Check the parent; retain reviewed scoped exceptions byte-for-byte.
    _,removed=scoped_hold_text(saved)
    if removed:raise ValueError('Remove inherited whole-clock cuts before adding Native pin timing')
    controller,wrapper,cas,override=project_contract(directory,expected_controller_sha256)
    if implementation and (analysis_only or cas!=3 or expected_controller_sha256 not in REVIEWED_CAS3_CAPTURE_SHA256):
        raise ValueError('I/O implementation requires strict CAS3 with a reviewed capture-contract source')
    work=directory/'native-sdram-io'
    if work.exists() or 'pocket_native_sdram_io' in q:
        raise ValueError('Native SDRAM pin model already prepared')
    before={str(p):digest(p) for p in [qsf,sdc,controller,wrapper]}
    work.mkdir();scope=work/'scope.tcl'
    shutil.copyfile(ROOT/'scripts/pocket_native_sdram_io.tcl',scope)
    config=work/'pocket_native_sdram_io.sdc'
    config.write_text(f'''# Explicit model assumptions; not measured board delays.
set native_sdram_cas {cas}
set native_sdram_board_min {board_min_ns:.6f}
set native_sdram_board_max {board_max_ns:.6f}
set native_sdram_analysis_only {int(analysis_only)}
source "{scope}"
''')
    if not all(digest(Path(p))==sha for p,sha in before.items()):
        raise ValueError('Copied Native project changed during preparation')
    q+=f'\nset_global_assignment -name SDC_FILE "{config}"\n'
    added=[scope,config];physical=[]
    if implementation:
        capture=work/'previous-beat.sdc'
        capture.write_text('''# Reviewed e+CAS+1 capture consumes the prior launched beat.
if {$native_sdram_cas!=3 || [get_collection_size $native_sdram_caps]!=16 || abs($native_sdram_period-9.841)>0.002} {error {Reviewed CAS3 capture requires Native 101.6MHz clock/geometry}}
set_multicycle_path -setup 2 -from $native_sdram_inputs -to $native_sdram_caps
# No hold relaxation: next-beat hold is a real fit/audit gate.
''')
        added.append(capture)
        physical=[
            'set_global_assignment -name AUTO_DELAY_CHAINS ON',
            'set_instance_assignment -name ALLOW_SYNCH_CTRL_USAGE OFF -to "core_top:ic|psx_native_memory:native_memory|psx_sdram_ctrl:controller|dq_oe"',
            'set_instance_assignment -name FAST_OUTPUT_ENABLE_REGISTER ON -to "dram_dq[*]"',
        ]
        if input_delay_taps is not None:
            # D1 is buffer -> packed input register, not the D3 core path.
            # Tap count is a physical setting, not a nanosecond guarantee.
            # Keep next-beat hold timed and re-fit/audit every composition.
            physical += [f'set_instance_assignment -name D1_DELAY {input_delay_taps} -to "dram_dq[{bit}]"'
                         for bit in range(16)]
        q+=f'\nset_global_assignment -name SDC_FILE "{capture}"\n'+'\n'.join(physical)+'\n'
    qsf.write_text(q)
    report=dict(cas_latency=cas,cas_wrapper_override=override,
                controller_default_cas=controller_contract(controller.read_text()),
                controller=dict(path=str(controller),sha256=expected_controller_sha256),
                wrapper=dict(path=str(wrapper),sha256=digest(wrapper)),
                per_leg_board_flight_ns=[board_min_ns,board_max_ns],analysis_only=analysis_only,
                assumptions_verified=False,hardware_verified=False,timing_verified=False,fit_verified=False,
                input_tAC_max_ns=6.0 if cas==2 else 5.5,input_tOH_min_ns=2.5,
                minimum_clock_period_ns=12.0 if cas==2 else 6.0,
                output_setup_ns=2.0,output_hold_ns=1.0,
                clock_or_RTL_changed=False,multicycle_added=implementation,false_paths_added=False,
                implementation_recipe=implementation,read_setup_multicycle=2 if implementation else 1,
                read_hold_relaxation=False,physical_assignments=physical,
                input_delay_taps=input_delay_taps,
                before=before,added=[dict(path=str(p),sha256=digest(p)) for p in added],
                limitations=['Board flight/load assumptions require qualification',
                             'Default one-edge input checks do not prove the consumed beat identity',
                             'Analysis-only rate override is diagnostic, never part-spec signoff',
                             'Only SDRAM covered; PSRAM/APF interfaces remain separate gates'])
    (work/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('project',type=Path)
    p.add_argument('--expected-controller-sha256',required=True)
    p.add_argument('--board-min-ns',type=float,default=0)
    p.add_argument('--board-max-ns',type=float,default=.5)
    p.add_argument('--analysis-only',action='store_true')
    p.add_argument('--implement-cas3-io',action='store_true',help='Apply the reviewed CAS3 read-edge and targeted I/O packing/delay recipe; requires a fresh full fit and edge/pin audits')
    p.add_argument('--input-delay-taps',type=int,help='Opt-in Cyclone V D1 setting (0..31) on all 16 SDRAM DQ pins; requires --implement-cas3-io and a fresh physical fit. No timing relaxation.')
    a=p.parse_args();print(json.dumps(prepare_project(a.project,a.expected_controller_sha256,
        board_min_ns=a.board_min_ns,board_max_ns=a.board_max_ns,analysis_only=a.analysis_only,
        implementation=a.implement_cas3_io,input_delay_taps=a.input_delay_taps),indent=2))
