#!/usr/bin/env python3
"""Cycle-compare the reset-seq-lean sequencer against the pinned reset sequence.

The run_release_init.py bench and profiles, with prepare_reset_seq_lean.py
applied on top of the release-init source: every control output on every
2x tick, the DDR address and payload whenever a request is up, the SS and
SPU payloads with their strobes. Three planted faults in the lean source
must fail: a VRAM clear address, a header address step and a header read
address left at a dropped value.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from prepare_release_init import release_init_source
from prepare_reset_seq_lean import lean_text
from prepare_resource_targets import UPSTREAM, digest
from run_release_init import bench_text
from tool_paths import ROOT, find_tool

PROFILES = [('full-offchip', ['-gROUNDS=2', '-gSEED=2']),
            ('fast-offchip', ["-gFASTSIM='1'", '-gROUNDS=2', '-gSEED=1']),
            ('full-internal', ["-gSPUSDRAM='0'", '-gROUNDS=1', '-gSEED=4']),
            ('retry', ["-gFASTSIM='1'", '-gPAUSE_DELAY=2500', '-gSEED=3'])]
FAULTS = [
    ('bad-vram-address', ['-gSEED=2'],   # FASTSIM ends before the VRAM clear
     ('ddr3_ADDR    <= "0000000" & std_logic_vector(RAMAddrNext);', 'ddr3_ADDR    <= "0000000" & std_logic_vector(RAMAddrNext + 2);'),
     'Reset DDR request mismatch'),
    ('bad-header-step', ["-gFASTSIM='1'", '-gSEED=1'],
     ('std_logic_vector(unsigned(ddr3_ADDR) + to_unsigned(SAVESTATESIZE, 26))', 'std_logic_vector(unsigned(ddr3_ADDR) + to_unsigned(SAVESTATESIZE / 2, 26))'),
     'Reset DDR request mismatch'),
    # the first header read's address left out like the dropped assignments: visible, so caught
    ('bad-header-start', ["-gFASTSIM='1'", '-gSEED=1'],
     ('                     ddr3_ADDR      <= std_logic_vector(to_unsigned(Softmap_SaveState_ADDR, 26));\n', ''),
     'Reset DDR request mismatch'),
]


def main():
    parent = ROOT/'build/sim/reset-seq-lean'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='checks-', dir=parent))
    lean = work/'savestates_lean.vhd'
    lean.write_text(lean_text(Path(release_init_source(work/'rtl')['path']).read_text()))
    bench = work/'tb.vhd'; bench.write_text(bench_text()); reference = UPSTREAM/'rtl/savestates.vhd'
    ghdl = find_tool('ghdl'); runs = []

    def run(name, generics, mutation=None, marker=None):
        directory = work/name; directory.mkdir(); selected = lean
        if mutation:
            before, after = mutation; text = lean.read_text()
            if text.count(before) != 1: raise ValueError(f'{name}: fault anchor changed')
            selected = directory/'savestates.vhd'; selected.write_text(text.replace(before, after))
        with (directory/'build.log').open('w') as log:
            for command in ([ghdl, '-a', '--std=08', str(reference)], [ghdl, '-a', '--std=08', '--work=optimized', str(selected)],
                            [ghdl, '-a', '--std=08', str(bench)], [ghdl, '-e', '--std=08', 'tb_mister_reset_only']):
                subprocess.run(command, cwd=directory, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        r = subprocess.run([ghdl, '-r', '--std=08', 'tb_mister_reset_only', *generics, '--assert-level=error',
                            '--ieee-asserts=disable-at-0'], cwd=directory, capture_output=True, text=True, timeout=3600)
        output = r.stdout + r.stderr; (directory/'run.log').write_text(output)
        if marker: passed = r.returncode != 0 and marker in output
        else: passed = r.returncode == 0 and output.count('PASS reset-only') == 1 and 'assertion failure' not in output
        runs.append(dict(name=name, generics=generics, passed=passed, exit_code=r.returncode, source_sha256=digest(selected)))
        print(name, 'PASS' if passed else 'FAIL', output.strip().splitlines()[-1:] if output.strip() else '', flush=True)
        return passed

    ok = all([run(name, args) for name, args in PROFILES])
    caught = [run(name, args, mutation, marker) for name, args, mutation, marker in FAULTS]
    report = dict(passed=ok and all(caught), runs=runs, lean_sha256=digest(lean))
    (work/'report.json').write_text(json.dumps(report, indent=2) + '\n'); print(work/'report.json')
    if not report['passed']: raise SystemExit('reset-seq-lean check failed')
    print('PASS reset-seq-lean: equal to the pinned reset sequence; 3 planted faults caught')


if __name__ == '__main__':
    main()
