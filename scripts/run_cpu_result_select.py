#!/usr/bin/env python3
"""Cycle-lockstep check of prepare_cpu_result_select.py on the project's CPU.

The run_current_cpu_execute_share.py bench (tb_cpu_cache_contract, paired
observed CPUs, two RAM models, the four memory/CE/reset profiles) with the
QSF-selected cpu.vhd of a prepared project as the control and its result-
select form as the candidate. Program: cpu_result_select_program.py (every
execute-stage result source, then the cached branch/IRQ/COP0 program). Three
planted faults must fail: MFHI reading LO, the logic-op select bit flipped,
and the zero-extended immediate widened.
Usage: run_cpu_result_select.py PROJECT --output build/...
"""
import argparse
from pathlib import Path
import re
import subprocess

import run_current_cpu_regfile as current
from cpu_result_select_program import write_program
from prepare_cpu_result_select import result_select_text
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool

FAULTS = {
    'hi-as-lo': ('=> EXEresultData <= hi;', '=> EXEresultData <= lo;'),
    'logic-select': ('exe_lop := decodeFunct(1 downto 0);', 'exe_lop := decodeFunct(1 downto 0) xor "01";'),
    'immediate-width': ('exe_lb := x"0000" & decodeImmData;', 'exe_lb := decodeImmData & decodeImmData;'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    P, C = args.project.resolve(), args.output.resolve()
    if C.exists() or not C.is_relative_to(ROOT/'build'): raise ValueError('fresh output directory under build/ required')
    selected = assignments(P, (P/'apf_top.qsf').read_text())

    def one(name):
        rows = {p for _, p in selected if p.name == name}; assert len(rows) == 1, name; return rows.pop()
    original = one('cpu.vhd').read_text(); candidate = result_select_text(original)[0]
    C.mkdir(parents=True); failures = []
    for model in ('registered-data', 'registered-address'):
        D = C/model; D.mkdir(); program = write_program(D)
        ram = (UPSTREAM/'sim/system/src/mem/dpram.vhd').read_text(); ram = ram[:ram.index('end rtl;') + len('end rtl;')]
        ram = re.sub(r'\bdpram\b', 'pocket_dpram_sdp', ram, flags=re.I)
        (D/'pocket_dpram_sdp_reverse.vhd').write_text(ram.replace('pocket_dpram_sdp', 'pocket_dpram_sdp_reverse'))
        if model == 'registered-address': ram = ram[:ram.index('architecture rtl of')] + current.ADDRESS_MODEL
        (D/'pocket_dpram_sdp.vhd').write_text(ram); (D/'baseline.vhd').write_text(current.observe(original))
        bench = current.paired_bench(); assert bench.count('EXPECTED_COUNT : positive := 20') == 1
        if 'mem_writeRequest' not in original:
            # the release CPU has no short-write output; the bench only compares it, its memory runs on mem_request/mem_rnw
            for line in ('    signal mem_writeRequest : std_logic;\n', '    signal candidate_mem_writeRequest : std_logic;\n',
                         '        mem_writeRequest=>mem_writeRequest,\n', '        mem_writeRequest=>candidate_mem_writeRequest,\n',
                         '            assert mem_writeRequest=candidate_mem_writeRequest report "CPU short write mismatch" severity failure;\n'):
                if bench.count(line) != 1: raise ValueError('bench short-write line changed: ' + line.strip())
                bench = bench.replace(line, '')
        (D/'bench.vhd').write_text(bench.replace('EXPECTED_COUNT : positive := 20', f'EXPECTED_COUNT : positive := {program["expected_checks"]}'))

        def cmd(argv):
            r = subprocess.run([find_tool('ghdl'), *argv], cwd=D, capture_output=True, text=True, timeout=300)
            with (D/'build.log').open('a') as f: f.write(' '.join(argv) + '\n' + r.stdout + r.stderr)
            if r.returncode: raise RuntimeError(r.stdout + r.stderr)

        def analyze(lib, p): cmd(['-a', '--std=08', '-frelaxed', '--work=' + lib, str(p)])
        analyze('mem', UPSTREAM/'sim/system/src/mem/RamMLAB.vhd')
        for lib in ('work', 'candidate'):
            for p in (UPSTREAM/'sim/system/src/mem/dpram.vhd', D/'pocket_dpram_sdp.vhd', D/'pocket_dpram_sdp_reverse.vhd',
                      one('export.vhd'), one('divider.vhd'), one('datacache.vhd')):
                analyze(lib, p)
        analyze('work', D/'baseline.vhd')
        for mode in ('control', 'select', *FAULTS):
            text = original if mode == 'control' else candidate
            if mode in FAULTS:
                old, new = FAULTS[mode]
                if text.count(old) != 1: raise ValueError('fault anchor changed: ' + mode)
                text = text.replace(old, new)
            path = D/(mode + '.vhd'); path.write_text(current.observe(text)); analyze('candidate', path); analyze('work', D/'bench.vhd')
            cmd(['-e', '--std=08', '-frelaxed', 'tb_cpu_cache_contract'])
            for seed, pause, early, resets in (current.PROFILES if mode in ('control', 'select') else current.PROFILES[:1]):
                argv = ['-r', '--std=08', '-frelaxed', 'tb_cpu_cache_contract', f'-gSEED={seed}', f'-gEXPECTED_COUNT={program["expected_checks"]}',
                        f'-gPAUSES={str(pause).lower()}', f'-gEARLY_DONE={str(early).lower()}', f'-gRESETS={str(resets).lower()}',
                        '--assert-level=error', '--ieee-asserts=disable-at-0']
                r = subprocess.run([find_tool('ghdl'), *argv], cwd=D, capture_output=True, text=True, timeout=1800); out = r.stdout + r.stderr
                (D/f'{mode}-{seed}.log').write_text(out)
                marker = re.search(r'PASS CPU cache[^\n]*', out)
                first = next((s for s in out.splitlines() if 'mismatch' in s.lower() or 'expected' in s.lower() and 'fail' in s.lower()), '')
                passed = r.returncode == 0 and bool(marker) and f'checks={program["expected_checks"]} ' in marker[0]
                ok = passed if mode in ('control', 'select') else not passed
                print(model, mode, seed, 'OK' if ok else 'WRONG', (marker[0] if passed else first)[:150], flush=True)
                if not ok: failures.append(f'{model} {mode} seed {seed}')
    if failures: raise SystemExit('CPU result select check failed: ' + ', '.join(failures))
    print('PASS cpu-result-select: lockstep on 2 RAM models x 4 profiles, 3 planted faults caught')


if __name__ == '__main__':
    main()
