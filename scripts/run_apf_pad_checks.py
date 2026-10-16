#!/usr/bin/env python3
"""Pinned APF one-wire protocol, including real-width timeout/reset recovery.

Simulation-only startup seeds fill the three otherwise uninitialized counters.
No counter width, runtime expression, clock, BITLEN or production file changes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from run_apf_bridge_checks import COMMON, COMMON_SHA256, digest
from tool_paths import ROOT, find_tool

PAD = ROOT/'references/MIRLO64/platform/pocket/io_pad_controller.v'
PAD_SHA256 = '7e642096b1d9678cff2e55d6f47c7c7d3b5be8ecba9ae5ee8a1e38490806369a'
BENCH = ROOT/'sim/pocket/tb_apf_pad_pin.sv'


def simulation_copy(original):
    if hashlib.sha256(original.encode()).hexdigest() != PAD_SHA256:
        raise ValueError('Pinned APF PAD needs a new compatibility review')
    old_header = 'module io_pad_controller ('
    header = '''module io_pad_controller #(
parameter integer SIM_RX_SEED = 0,
parameter integer SIM_POLL_SEED = 0,
parameter integer SIM_HEART_SEED = 0
) ('''
    text = original.replace(old_header, header)
    text = text.replace('inout   reg             pad_1wire,', 'inout   wire            pad_1wire,')
    text, count = re.subn(r'\bpad_1wire(\s*<=)', r'pad_1wire_driver\1', text)
    if count != 5: raise ValueError('APF PAD procedural driver changed')
    old_sync = 'synch_3 s00(reset_n, reset_n_s, clk);'
    new_sync = 'synch_3 s00(reset_n, reset_n_s, clk, , );'
    if text.count(old_sync) != 1: raise ValueError('APF PAD synchronizer ports changed')
    text = text.replace(old_sync, new_sync)
    position = text.index(');\n', text.index('module io_pad_controller'))+3
    inserted = '''
reg pad_1wire_driver;
assign pad_1wire = pad_1wire_driver;
// FPGA counter startup is an explicit simulation assumption, not a reset.
initial begin
    rx_timeout = SIM_RX_SEED;
    auto_poll_cnt = SIM_POLL_SEED;
    heartbeat_cnt = SIM_HEART_SEED;
end
'''
    text = text[:position]+inserted+text[position:]
    restored = text.replace(inserted, '', 1).replace(header, old_header).replace(new_sync, old_sync)
    restored = restored.replace('inout   wire            pad_1wire,', 'inout   reg             pad_1wire,')
    restored = restored.replace('pad_1wire_driver', 'pad_1wire')
    if restored != original: raise ValueError('Non-mechanical PAD runtime rewrite')
    return text


def profiles():
    cases = [dict(name=f'phase-{i}', parameters=dict(PHASE_PS=phase))
             for i,phase in enumerate((1, 999, 3367, 6733, 8417, 10001, 12001, 13467))]
    for name, parameters in (
        ('input-flight', dict(INPUT_FALL_PS=10000, INPUT_RISE_PS=17000)),
        ('output-flight', dict(OUTPUT_FALL_PS=1000, OUTPUT_RISE_PS=40000)),
        ('slow-rise', dict(INPUT_RISE_PS=60000)),
        ('pulse-jitter', dict(JITTER_PS=40000)),
        ('peripheral-fast', dict(BIT_PS=780001)),
        ('peripheral-slow', dict(BIT_PS=835007)),
        ('cold-zero-seed', dict(RX_SEED=0, POLL_SEED=0, HEART_SEED=0, HEART_TARGET=3)),
        ('adapter-reset', dict(RESET_SECOND_REPLY=1)),
        ('missing-reply', dict(DROP_SECOND_REPLY=1)),
    ):cases.append(dict(name=name, parameters=parameters))
    return cases


def fault_profiles():
    return [
        dict(name='late-rise', parameters=dict(INPUT_RISE_PS=230000), expected_error='PAD_REPORT'),
        dict(name='too-fast-pulses', parameters=dict(BIT_PS=500000), expected_error='PAD_REPORT'),
        dict(name='turnaround-contention', parameters=dict(TURNAROUND_PS=0), expected_error='PAD_CONTENTION'),
        dict(name='wrong-received-bit', parameters={}, expected_error='PAD_REPORT',
             mutation=['rx_word_shift <= {rx_word_shift[30:0], pad_1wire_s};',
                       'rx_word_shift <= {rx_word_shift[30:0], ~pad_1wire_s};']),
        dict(name='wrong-poll', parameters={}, expected_error='PAD_COMMAND unexpected',
             mutation=["tx_word <= 32'h4A10000C;", "tx_word <= 32'h4A100008;"]),
        dict(name='wrong-heartbeat', parameters={}, expected_error='PAD_COMMAND unexpected',
             mutation=["tx_word <= 32'h4AFE0000;", "tx_word <= 32'h4AFE0001;"]),
    ]


def coverage(output):
    matches = re.findall(r'^PASS APF_PAD (.+)$', output, re.M)
    if len(matches) != 1: return None
    return {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', matches[0])}


def verdict(output, returncode, case):
    if case.get('expected_error'):
        return returncode != 0 and case['expected_error'] in output and 'PASS APF_PAD' not in output
    c = coverage(output)
    if returncode or c is None or re.search(r'FATAL|ERROR|FAIL|PAD_DEADLINE', output):return False
    required = ('polls','reports','hearts','aborts','drops','resets','timeouts','words','decoded_bits','sent_bits')
    if any(k not in c for k in required):return False
    reset = case['parameters'].get('RESET_SECOND_REPLY', 0)
    drop = case['parameters'].get('DROP_SECOND_REPLY', 0)
    return (c['reports']>=2 and c['hearts']>=case['parameters'].get('HEART_TARGET', 1)
            and c['resets']==c['aborts']==reset and c['drops']==c['timeouts']==drop
            and c['words']==12*c['reports'] and c['polls']==c['reports']+reset+drop
            and c['decoded_bits']==32*(c['polls']+c['hearts']) and c['sent_bits']==384*c['reports']+8*reset)


def run(output, only=None):
    output = Path(output).resolve()
    if output.exists():raise ValueError('Use a fresh PAD evidence directory')
    if not output.is_relative_to(ROOT/'build') or subprocess.run(
            ['git','check-ignore','--quiet',str(output/'report.json')],cwd=ROOT).returncode:
        raise ValueError('PAD evidence must stay under ignored build/')
    cases = profiles()+fault_profiles()
    if only:
        selected = [c for c in cases if c['name'] in only]
        if len(only)!=len(set(only)) or len(selected)!=len(only):raise ValueError('Unknown/repeated PAD profile')
        cases = selected
    protected = {str(p):digest(p) for p in (PAD, COMMON, BENCH, Path(__file__), ROOT/'scripts/run_apf_bridge_checks.py')}
    if digest(COMMON)!=COMMON_SHA256:raise ValueError('APF common source changed')
    compatible = simulation_copy(PAD.read_text())
    iverilog,vvp = find_tool('iverilog'),find_tool('vvp')
    if not iverilog or not vvp:raise ValueError('Icarus/vvp required')
    output.mkdir(parents=True)
    snapshot = output/'sources';snapshot.mkdir()
    for p in (PAD,COMMON,BENCH):
        q=snapshot/p.name;shutil.copyfile(p,q)
        if digest(q)!=protected[str(p)]:raise ValueError('PAD input changed while capturing')
    report = dict(scope='APF one-wire public-pin/controller-port protocol', protected_sha256=protected,
        source_snapshot_before_simulation=True,snapshot_sha256={str(p):digest(p) for p in snapshot.iterdir()},
        complete_suite=only is None,runs=[],passed=False,production_modified=False,pin_constraints_added=False,
        hardware_verified=False,board_timing_qualified=False,
        assumptions=['Three counter startup seeds explicit; fast profiles start near wrap, cold profile starts at zero.',
                     'Full runtime counter widths/periods and BITLEN60 retained; no internal force or accelerated timeout.',
                     'Transport rise/flight and pulse jitter are digital assumptions; no analog pull-up/load/metastability model.',
                     'Adapter reset is distinct from PSX/Pocket system warm reset.'])
    for case in cases:
        folder=output/case['name'];folder.mkdir();text=compatible
        if case.get('mutation'):
            old,new=case['mutation']
            if text.count(old)!=1:raise ValueError('PAD planted fault location changed')
            text=text.replace(old,new)
        source=folder/'io_pad_sim.v';source.write_text(text);binary=folder/'tb'
        command=[iverilog,'-g2012','-s','tb_apf_pad_pin','-o',str(binary)]
        command += [f'-Ptb_apf_pad_pin.{k}={v}' for k,v in case['parameters'].items()]
        command += [str(source),str(snapshot/COMMON.name),str(snapshot/BENCH.name)]
        log=folder/'compile.log'
        with log.open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=30)
        p=subprocess.run([vvp,str(binary)],capture_output=True,text=True,timeout=120)
        text=p.stdout+p.stderr;(folder/'run.log').write_text(text)
        passed=verdict(text,p.returncode,case)
        row=dict(case,passed=passed,returncode=p.returncode,coverage=coverage(text),compile_command=command,
                 run_command=[vvp,str(binary)],source_sha256=digest(source),executable_sha256=digest(binary),
                 log=str(folder/'run.log'),log_sha256=digest(folder/'run.log'))
        identity = all(digest(Path(p))==h for p,h in protected.items()) and all(
            digest(Path(p))==h for p,h in report['snapshot_sha256'].items())
        row['source_identity_verified'] = identity
        report['runs'].append(row);report['passed']=identity and all(x['passed'] for x in report['runs'])
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(case['name'],'PASS' if passed else 'FAIL',text.strip().splitlines()[-2:],flush=True)
        if not identity:raise ValueError('PAD inputs changed during run')
        if not passed:raise ValueError('PAD profile failed: '+case['name'])
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path);parser.add_argument('--only',nargs='+');args=parser.parse_args()
    parent=ROOT/'build/sim/apf-pad-pin/runs';parent.mkdir(parents=True,exist_ok=True)
    if args.output:output=args.output
    else:
        output=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=parent));output.rmdir()
    result=run(output,args.only);print(output)
    if not result['passed']:raise SystemExit(1)


if __name__=='__main__':main()
