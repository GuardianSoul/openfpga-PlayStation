#!/usr/bin/env python3
"""Cycle-lockstep the local VRAM return enables against the original path."""
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_resource_targets import digest
from prepare_vram_response_enable import prepare_components, replace_once
from tool_paths import ROOT, find_tool


def main():
    compiler, runtime = find_tool('iverilog'), find_tool('vvp')
    if not compiler or not runtime:
        raise ValueError('Icarus Verilog required')
    parent = ROOT / 'build/sim/vram-response-enable'
    parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    original_cdc = ROOT / 'rtl/memory/psx_mem_cdc.sv'
    original_adapter = ROOT / 'rtl/pocket/psx_vram_adapter.sv'
    bench = ROOT / 'sim/pocket/tb_vram_response_enable.sv'
    protected = {str(p): digest(p) for p in (
        original_cdc, original_adapter, bench, Path(__file__),
        ROOT / 'scripts/prepare_vram_response_enable.py')}
    results = []

    def run(name, cdc, components, clocks):
        case = work / name
        case.mkdir()
        binary = case / 'check.vvp'
        command = [compiler, '-g2012', '-s', 'tb_vram_response_enable', '-o', str(binary)]
        command += [f'-Ptb_vram_response_enable.{key}={value}' for key, value in
                    zip(('SRC_HALF', 'DST_HALF', 'DST_PHASE'), clocks)]
        command += [str(cdc), str(original_adapter), *[s['path'] for s in components], str(bench)]
        with (case / 'build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=60)
        result = subprocess.run([runtime, str(binary)], capture_output=True, text=True, timeout=120)
        output = result.stdout + result.stderr
        (case / 'run.log').write_text(output)
        print(name + ': ' + output, end='', flush=True)
        return {'name': name, 'clocks_fs': clocks, 'exit_code': result.returncode,
                'log_sha256': digest(case / 'run.log'), 'stdout': output,
                'passed': result.returncode == 0 and 'PASS VRAM response enable' in output}

    components = prepare_components(work / 'candidate')
    for i, clocks in enumerate(((7381275, 8000000, 3000000),
                                (5000000, 11000000, 1000000),
                                (13000000, 4500000, 3000000))):
        results.append(run(f'raw-reset-{i}', original_cdc, components, clocks))
        if not results[-1]['passed']:
            raise SystemExit('Lockstep failed; preserve ' + str(work))
    # Physical prepare_probe uses local synchronized deassertion. Verify that
    # actual source form as well; don't rewrite the shared module or builder.
    local_cdc = work / 'local-reset-original.sv'
    text = original_cdc.read_text()
    for domain in ('src', 'dst'):
        text = replace_once(text,
            f'    always @(posedge {domain}_clk or posedge reset_async) begin\n        if (reset_async) begin',
            f'    always @(posedge {domain}_clk or posedge {domain}_reset) begin\n        if ({domain}_reset) begin')
    local_cdc.write_text(text)
    local_components = prepare_components(work / 'local-candidate', local_cdc)
    results.append(run('local-reset', local_cdc, local_components, (7381275, 8080808, 5700000)))
    if not results[-1]['passed']:
        raise SystemExit('Local reset lockstep failed; preserve ' + str(work))
    mutations = []
    for name, which, before, after in (
        ('late-enable', 0, 'response_sync[1] == request_toggle;',
         'response_sync[1] == request_toggle && src_state == RESPONSE;'),
        ('wrong-data', 1, 'dout[lane*8 +: 8]<=rsp_data[lane*8 +: 8];',
         "dout[lane*8 +: 8]<=rsp_data[lane*8 +: 8]^8'h01;")):
        copied = [dict(s) for s in components]
        path = work / (name + '.sv')
        path.write_text(replace_once(Path(copied[which]['path']).read_text(), before, after))
        copied[which]['path'] = str(path)
        result = run(name, original_cdc, copied, (7381275, 8000000, 3000000))
        result['rejected'] = result['exit_code'] != 0 and 'VRAM source cycle lockstep' in result['stdout']
        mutations.append(result)
    for path, sha in protected.items():
        if digest(Path(path)) != sha:
            raise ValueError('VRAM response input changed: ' + path)
    report = {'passed': all(r['passed'] for r in results) and all(m['rejected'] for m in mutations),
              'components': components, 'protected_sha256': protected,
              'runs': results, 'mutations': mutations, 'added_client_cycles': 0,
              'timing_verified': False, 'hardware_verified': False}
    (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(work)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
