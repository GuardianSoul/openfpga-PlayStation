#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit a Native SPU cache against a matched uncached wrapper/controller.

Both generated projects are frozen before compilation. Required cases cover
coherence and idle/active/common reset; one-clock API stress is separate.
The candidate gets a failing result if a required case fails. No bitstream,
BIOS/disc, production source change or electrical qualification is involved.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from measure_pocket_seeds import inputs
from prepare_resource_targets import digest
from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool

CASES = ('coherence', 'reset-idle', 'reset-active-read', 'reset-active-write',
         'common-reset-active-read', 'one-clock-api-stress')


def freeze(project, directory):
    directory.mkdir()
    result = []
    for i, row in enumerate(inputs(project)):
        source = Path(row['path'])
        target = directory/str(i)/source.name
        target.parent.mkdir()
        shutil.copyfile(source, target)
        if digest(target) != row['sha256']:
            raise ValueError('Source changed while freezing: '+str(source))
        result.append(dict(row, captured=str(target)))
    return result


def selected(rows, name):
    matches = [r for r in rows if Path(r['path']).name == name]
    if len(matches) != 1:
        raise ValueError('Expected one assigned '+name)
    return Path(matches[0]['captured'])


def run(directory, rows, support, cached):
    directory.mkdir()
    wrapper = selected(rows, 'psx_native_memory.sv')
    text = wrapper.read_text()
    if '.CAS_LATENCY(3)' not in text or '.SPU_FIRST(1)' not in text:
        raise ValueError('This fixture requires the reviewed Native CAS3/SPU_FIRST profile')
    if ('psx_spu_ch3_cache spu_cache' in text) != cached:
        raise ValueError('Cache/control wrapper profile differs')
    bench = (support/'bench.sv').read_text()
    cache_source = selected(rows, 'psx_spu_ch3_cache.sv').read_text() if cached else ''
    if not cached or not re.search(r'\breg\b[^;]*\bstate\b', cache_source):
        # Only the display probes change (absent in the control and in caches
        # without state/word/cancel). All requests/data/checks are identical.
        bench, count = re.subn(r'dut\.spu_cache\.(?:state|word|cancel)', "3'd0", bench)
        if count != 8:
            raise ValueError('Native cache debug probe shape differs')
    test = directory/'bench.sv'
    test.write_text(bench)
    sources = [wrapper, selected(rows, 'psx_sdram_ctrl.sv'), test,
               support/'sdram_chip_model.sv', support/'altddio.sv']
    if cached:
        sources.append(selected(rows, 'psx_spu_ch3_cache.sv'))
    protected = {str(p): digest(p) for p in sources}
    command = [find_tool('verilator'), '--binary', '--timing', '-j', '2',
               '-Wno-fatal', '--top-module', 'tb', '--Mdir', str(directory/'obj'),
               *map(str, sources)]
    with (directory/'build.log').open('w') as log:
        subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=240)
    binary = directory/'obj/Vtb'
    runs = []
    for i, name in enumerate(CASES):
        cmd = [str(binary), f'+case={i}']
        process = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=60)
        output = process.stdout+process.stderr
        log = directory/(name+'.log')
        log.write_text(output)
        passed = process.returncode == 0 and f'PASS NATIVE_CACHE_PORTS case={i} ' in output
        runs.append(dict(case=i, name=name, required=i<5, command=cmd,
                         passed=passed, exit_code=process.returncode,
                         log=dict(path=str(log), sha256=digest(log))))
        print(directory.name, name, 'PASS' if passed else 'FAIL', flush=True)
    identity = all(digest(Path(p)) == sha for p, sha in protected.items())
    return dict(required_cases_passed=identity and all(r['passed'] for r in runs if r['required']),
                source_identity_verified=identity, compiled_inputs=protected, command=command,
                binary=dict(path=str(binary), sha256=digest(binary)), runs=runs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True, help='Generated Native cache project')
    parser.add_argument('--control-project', type=Path, required=True, help='Matched uncached Native project')
    parser.add_argument('--output', type=Path, help='Fresh ignored build directory')
    args = parser.parse_args()
    if args.output:
        work = args.output.resolve()
        if work.exists() or not work.is_relative_to(ROOT/'build'):
            parser.error('Output must be fresh and below build/')
        work.mkdir(parents=True)
    else:
        parent = ROOT/'build/sim/native-spu-cache-ports'
        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    print(work, flush=True)
    cached = freeze(args.project.resolve(), work/'cached-inputs')
    control = freeze(args.control_project.resolve(), work/'control-inputs')
    if digest(selected(cached, 'psx_sdram_ctrl.sv')) != digest(selected(control, 'psx_sdram_ctrl.sv')):
        raise ValueError('Matched wrappers must use identical controller bytes')
    support = work/'support'
    support.mkdir()
    for old, name in [(ROOT/'sim/pocket/tb_native_spu_cache_ports.sv', 'bench.sv'),
                      (ROOT/'sim/memory/sdram_chip_model.sv', 'sdram_chip_model.sv')]:
        shutil.copyfile(old, support/name)
    (support/'altddio.sv').write_text(ALTDDIO)
    protected = {str(p): digest(p) for p in support.iterdir()}
    result = dict(cached=run(work/'cached', cached, support, True),
                  control=run(work/'control', control, support, False))
    identity = all(digest(Path(row['captured'])) == row['sha256'] for row in cached+control)
    identity &= all(digest(Path(p)) == sha for p, sha in protected.items())
    report = dict(passed=identity and all(v['required_cases_passed'] for v in result.values()),
                  source_identity_verified=identity, results=result,
                  sources=dict(cached=cached, control=control), support=protected,
                  scripted_inputs={str(p):digest(p) for p in [Path(__file__),ROOT/'scripts/run_sdram_ctrl.py']},
                  one_clock_case='API stress below boot_gate minimum; excluded from required-case verdict',
                  actual_SPU_engine_connected=False, actual_BIOS_or_game=False,
                  compiled_RBF_leaf_identity_proven=False,
                  hardware_verified=False, electrical_four_state_qualification=False)
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print('PASS NATIVE CACHE PORTS' if report['passed'] else 'FAIL NATIVE CACHE PORTS', flush=True)
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
