#!/usr/bin/env python3
"""Direct-pin APF protocol checks, not an external electrical timing signoff.

The pinned adapter uses procedural tristate inout variables, which Icarus
requires lowering to explicit output drivers on wire ports. That mechanical
simulation copy preserves both reads of the resolved pins and every clock,
state, synchronizer, assignment and Z value. No repository input is edited.
Omitted positional synchronizer outputs become explicit unconnected ports.
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

from tool_paths import ROOT, find_tool

BRIDGE = ROOT/'references/MIRLO64/platform/pocket/io_bridge_peripheral.v'
COMMON = ROOT/'references/MIRLO64/platform/pocket/common.v'
BENCH = ROOT/'sim/pocket/tb_apf_bridge_pin.sv'
BRIDGE_SHA256 = '35cc7d0464b2d0130955155eab442aa462c9e80c6982d101dbc3318c6aabb98d'
COMMON_SHA256 = '310156368dc697b4caa44239bf5a7b49c6eacb5e78db7411f8471c75676ba26e'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def simulation_copy(text):
    if hashlib.sha256(text.encode()).hexdigest() != BRIDGE_SHA256:
        raise ValueError('Pinned APF bridge needs a new simulation compatibility review')
    original = text
    declarations = []
    for name, count in (('phy_spiclk', 5), ('phy_spimosi', 4), ('phy_spimiso', 4)):
        old = 'inout   reg             '+name
        if text.count(old) != 1:
            raise ValueError('APF tristate port changed: '+name)
        text = text.replace(old, 'inout   wire            '+name)
        text, changed = re.subn(r'\b'+name+r'(\s*<=)', name+'_driver'+r'\1', text)
        if changed != count:
            raise ValueError('APF procedural driver changed: '+name)
        declarations.append(f'reg {name}_driver;\nassign {name} = {name}_driver;\n')
    position = text.index(');\n', text.index('module io_bridge_peripheral'))+3
    inserted = '\n'+''.join(declarations)
    text = text[:position]+inserted+text[position:]
    unused_ports = [
        ('synch_3 s00(reset_n, reset_n_s, clk);', 'synch_3 s00(reset_n, reset_n_s, clk, , );'),
        ('synch_3 s01(endian_little, endian_little_s, clk);', 'synch_3 s01(endian_little, endian_little_s, clk, , );'),
        ('synch_3 s03(rx_byte_done, rx_byte_done_s, clk, rx_byte_done_r);',
         'synch_3 s03(rx_byte_done, rx_byte_done_s, clk, rx_byte_done_r, );'),
    ]
    for old, new in unused_ports:
        if text.count(old) != 1: raise ValueError('APF unused synchronizer ports changed')
        text = text.replace(old, new)
    # Reversibility guards every unmodified clock/state/read expression too.
    restored = text.replace(inserted, '', 1)
    for old, new in unused_ports:
        restored = restored.replace(new, old)
    for name in ('phy_spiclk', 'phy_spimosi', 'phy_spimiso'):
        restored = restored.replace('inout   wire            '+name, 'inout   reg             '+name)
        restored = restored.replace(name+'_driver', name)
    if restored != original:
        raise ValueError('Non-mechanical APF simulation rewrite')
    return text


def verdict(output, returncode, expected_error=None):
    if expected_error:
        return returncode != 0 and expected_error in output and 'PASS APF_BRIDGE' not in output
    matches = re.findall(r'^PASS APF_BRIDGE (.+)$', output, re.M)
    if returncode or len(matches) != 1 or re.search(r'FATAL|ERROR|FAIL|BRIDGE_TIMEOUT', output):
        return False
    fields = dict(re.findall(r'(\w+)=(\d+)', matches[0]))
    return all(int(fields.get(k, -1)) == v for k, v in
               dict(writes=16, reads=16, aborts=2, rx_edges=800, tx_pairs=256).items())


def profiles():
    cases = [dict(name=f'phase-{i}', parameters=dict(PHASE_PS=1+i*841)) for i in range(16)]
    for name, params in (
        ('input-clock-late', dict(INPUT_CLOCK_PS=5000)),
        ('input-data-late', dict(INPUT_DATA_PS=5000)),
        ('select-late', dict(SELECT_PS=5000)),
        ('return-clock-late', dict(RETURN_CLOCK_PS=5000)),
        ('return-data-late', dict(RETURN_DATA_PS=5000)),
        ('mixed-skews', dict(INPUT_CLOCK_PS=3300, INPUT_DATA_PS=700, SELECT_PS=4100,
                             RETURN_CLOCK_PS=800, RETURN_DATA_PS=4200)),
        ('stopped-host-clock', dict(PAUSE_PS=100000)),
        ('incommensurate-host-a', dict(HOST_HALF_PS=17011, PHASE_PS=3311)),
        ('incommensurate-host-b', dict(HOST_HALF_PS=20011, PHASE_PS=7019, RESPONSE_CYCLES=20)),
    ):
        cases.append(dict(name=name, parameters=params))
    return cases


def fault_profiles():
    return [
        dict(name='late-input-data', parameters=dict(INPUT_DATA_PS=18000, CHECK_WINDOWS=0),
             expected_error='BRIDGE_READ_REQUEST'),
        dict(name='late-return-data', parameters=dict(RETURN_DATA_PS=18000, CHECK_WINDOWS=0),
             expected_error='BRIDGE_READ got='),
        dict(name='short-input-setup', parameters=dict(SETUP_PS=14000),
             expected_error='BRIDGE_INPUT_WINDOW setup'),
        dict(name='host-turnaround-contention', parameters=dict(CONTENTION=1),
             expected_error='BRIDGE_CONTENTION'),
        dict(name='wrong-transmit-pair', parameters={}, expected_error='BRIDGE_READ got=',
             mutation=['phy_spimosi_driver <= spis_word[31];', 'phy_spimosi_driver <= spis_word[30];']),
        dict(name='wrong-read-endian', parameters={}, expected_error='BRIDGE_READ got=',
             mutation=['spis_word_tx <= pmp_rd_data_e;', 'spis_word_tx <= pmp_rd_data;']),
    ]


def run(output, only=None):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a fresh evidence directory')
    if not output.is_relative_to(ROOT/'build'):
        raise ValueError('APF evidence must stay under ignored build/')
    if subprocess.run(['git', 'check-ignore', '--quiet', str(output/'report.json')], cwd=ROOT).returncode:
        raise ValueError('APF evidence directory must be ignored')
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    if not iverilog or not vvp:
        raise ValueError('Icarus and vvp are required')
    selected = profiles()+fault_profiles()
    if only:
        if len(only) != len(set(only)):
            raise ValueError('Unknown or repeated profile')
        selected = [p for p in selected if p['name'] in only]
        if len(selected) != len(set(only)):
            raise ValueError('Unknown or repeated profile')
    protected = {str(p):digest(p) for p in (BRIDGE, COMMON, BENCH, Path(__file__))}
    if protected[str(COMMON)] != COMMON_SHA256:
        raise ValueError('Pinned APF synchronizer needs a new compatibility review')
    compatible = simulation_copy(BRIDGE.read_text())
    output.mkdir(parents=True)
    snapshot = output/'sources'
    snapshot.mkdir()
    for source in (BRIDGE, COMMON, BENCH):
        shutil.copyfile(source, snapshot/source.name)
        if digest(snapshot/source.name) != protected[str(source)]:
            raise ValueError('APF source changed while snapshotting')
    report = dict(scope='Pinned APF adapter direct-pin protocol and assumed setup/hold windows',
        source_snapshot_before_simulation=True, protected_sha256=protected, runs=[], passed=False,
        snapshot_sha256={str(p):digest(p) for p in snapshot.iterdir()},
        complete_suite=only is None, selected_profiles=[c['name'] for c in selected],
        hardware_verified=False, board_timing_qualified=False, pin_constraints_added=False,
        production_modified=False, physical_budget_assumptions_qualified=False,
        limits=['No metastability, FPGA routed I/O delay, pad loading or vendor electrical model.',
                '1ns setup/hold and independently selected flight delays are bench assumptions.',
                'Host serial profiles are exercised inputs, not a newly asserted APF host specification.',
                'No one-wire PAD model or PSRAM qualification; no timing exception introduced.'])
    for case in selected:
        folder = output/case['name']
        folder.mkdir()
        text = compatible
        if case.get('mutation'):
            old, new = case['mutation']
            if text.count(old) != 1:
                raise ValueError('Planted APF fault location changed')
            text = text.replace(old, new)
        bridge = folder/'io_bridge_sim.v'
        bridge.write_text(text)
        binary = folder/'tb'
        command = [iverilog, '-g2012', '-s', 'tb_apf_bridge_pin', '-o', str(binary)]
        command += [f'-Ptb_apf_bridge_pin.{k}={v}' for k,v in case['parameters'].items()]
        command += [str(bridge), str(snapshot/COMMON.name), str(snapshot/BENCH.name)]
        log = folder/'compile.log'
        with log.open('w') as f:
            subprocess.run(command, stdout=f, stderr=subprocess.STDOUT, timeout=30, check=True)
        executed = subprocess.run([vvp, str(binary)], capture_output=True, text=True, timeout=30)
        text = executed.stdout+executed.stderr
        (folder/'run.log').write_text(text)
        passed = verdict(text, executed.returncode, case.get('expected_error'))
        row = dict(case, passed=passed, returncode=executed.returncode, compile_command=command,
                   run_command=[vvp, str(binary)], source_sha256=digest(bridge),
                   executable_sha256=digest(binary), compile_log_sha256=digest(log),
                   log=str(folder/'run.log'), log_sha256=digest(folder/'run.log'))
        match = re.search(r'^PASS APF_BRIDGE (.+)$', text, re.M)
        if match: row['coverage'] = {k:int(v) for k,v in re.findall(r'(\w+)=(\d+)', match[1])}
        report['runs'].append(row)
        print(case['name'], 'PASS' if passed else 'FAIL', text.strip().splitlines()[-2:], flush=True)
        if not all(digest(Path(p))==sha for p,sha in protected.items()):
            raise ValueError('APF source input changed while running')
        report['passed'] = all(row['passed'] for row in report['runs'])
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        if not passed:
            raise ValueError('APF bridge profile failed: '+case['name'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--only', nargs='+')
    args = parser.parse_args()
    parent = ROOT/'build/sim/apf-bridge-pin/runs'
    parent.mkdir(parents=True, exist_ok=True)
    if args.output:
        output = args.output
    else:
        output = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
        output.rmdir()
    result = run(output, args.only)
    print(output)
    if not result['passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()
