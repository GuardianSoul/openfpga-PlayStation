#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cycle-lockstep check of prepare_cpu_tag_split.py on the project's CPU.

The run_cpu_div_shared.py bench (tb_cpu_cache_contract: two observed CPUs
side by side, two RAM models, the four memory/CE/reset profiles) with the
QSF-selected cpu.vhd of a prepared project as the control and its tag-split
form as the candidate. Compared at every enabled clk1x edge: the memory
request and its payload, stall, error and GTE controls, the decode values,
COP0 SR/Cause/EPC, retirement timing, PC, opcode and all 32 registers.

Programs, each on both RAM models and all four profiles:
  cache    cpu_cache_program.py: cached loops, taken and not-taken delay
           slots, JAL/JR, a loaded JALR target, cache-index conflicts, all
           four fill starting words, KUSEG/KSEG0 tag aliases, invalidation
           and refill, IRQ/RFE, SYSCALL, branch-delay exception EPC/BD.
  divider  cpu_divider_program.py: the divide cases, then the same cached
           branch/IRQ/COP0 program at other addresses.
Planted faults (prepare_cpu_tag_split.FAULTS) must fail on the first profile
of both RAM models with the cache program; the one in EQUIVALENT must pass.
Usage: run_cpu_tag_split.py PROJECT --output build/...
"""
import argparse
from pathlib import Path
import re
import subprocess

import cpu_cache_program
import cpu_divider_program
import run_current_cpu_regfile as current
from prepare_cpu_tag_split import EQUIVALENT, FAULTS, fault_text, sha, transform
from prepare_pocket_config_cdc import assignments
from prepare_resource_targets import UPSTREAM
from tool_paths import ROOT, find_tool


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('project', type=Path); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    P, C = args.project.resolve(), args.output.resolve()
    if C.exists() or not C.is_relative_to(ROOT/'build'): raise ValueError('fresh output directory under build/ required')
    selected = assignments(P, (P/'apf_top.qsf').read_text())

    def one(name):
        rows = {p for _, p in selected if p.name == name}; assert len(rows) == 1, name; return rows.pop()
    original = one('cpu.vhd').read_text(); candidate = transform(original)
    print(f'control sha256 {sha(original)[:16]}, candidate sha256 {sha(candidate)[:16]}', flush=True)
    C.mkdir(parents=True); failures = []; runs = caught = 0
    for program_name, writer in (('cache', cpu_cache_program.write_program), ('divider', cpu_divider_program.write_program)):
        for model in ('registered-data', 'registered-address'):
            D = C/program_name/model; D.mkdir(parents=True); program = writer(D)
            ram = (UPSTREAM/'sim/system/src/mem/dpram.vhd').read_text(); ram = ram[:ram.index('end rtl;') + len('end rtl;')]
            ram = re.sub(r'\bdpram\b', 'pocket_dpram_sdp', ram, flags=re.I)
            (D/'pocket_dpram_sdp_reverse.vhd').write_text(ram.replace('pocket_dpram_sdp', 'pocket_dpram_sdp_reverse'))
            if model == 'registered-address': ram = ram[:ram.index('architecture rtl of')] + current.ADDRESS_MODEL
            (D/'pocket_dpram_sdp.vhd').write_text(ram); (D/'baseline.vhd').write_text(current.observe(original))
            bench = current.paired_bench(); assert bench.count('EXPECTED_COUNT : positive := 20') == 1
            if 'mem_writeRequest' not in original:
                # the release CPU has no short-write output; the bench only compares it
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
            first = program_name == 'cache' and model == 'registered-data'
            for mode in ('control', 'split', *(FAULTS if program_name == 'cache' else ())):
                text = original if mode == 'control' else candidate if mode == 'split' else fault_text(candidate, mode)
                path = D/(mode + '.vhd'); path.write_text(current.observe(text)); analyze('candidate', path); analyze('work', D/'bench.vhd')
                cmd(['-e', '--std=08', '-frelaxed', 'tb_cpu_cache_contract'])
                for seed, pause, early, resets in (current.PROFILES if mode in ('control', 'split') else current.PROFILES[:1]):
                    argv = ['-r', '--std=08', '-frelaxed', 'tb_cpu_cache_contract', f'-gSEED={seed}', f'-gEXPECTED_COUNT={program["expected_checks"]}',
                            f'-gPAUSES={str(pause).lower()}', f'-gEARLY_DONE={str(early).lower()}', f'-gRESETS={str(resets).lower()}',
                            '--assert-level=error', '--ieee-asserts=disable-at-0']
                    r = subprocess.run(['nice', '-n', '10', find_tool('ghdl'), *argv], cwd=D, capture_output=True, text=True, timeout=1800)
                    out = r.stdout + r.stderr
                    (D/f'{mode}-{seed}.log').write_text(out)
                    marker = re.search(r'PASS CPU cache[^\n]*', out)
                    why = next((s for s in out.splitlines() if 'mismatch' in s.lower() or 'expected' in s.lower() and 'fail' in s.lower()), '')
                    passed = r.returncode == 0 and bool(marker) and f'checks={program["expected_checks"]} ' in marker[0]
                    ok = passed if mode in ('control', 'split') or mode in EQUIVALENT else not passed
                    runs += mode in ('control', 'split'); caught += mode in FAULTS and mode not in EQUIVALENT and ok
                    note = ' (planted, behaviourally equivalent: must pass)' if mode in EQUIVALENT else ' (planted fault caught)' if mode in FAULTS and ok else ''
                    print(program_name, model, mode, seed, ('OK' if ok else 'WRONG') + note, (marker[0] if passed else why or out.strip()[-150:])[:170], flush=True)
                    if not ok: failures.append(f'{program_name} {model} {mode} seed {seed}')
    if failures: raise SystemExit('CPU tag-split check failed: ' + ', '.join(failures))
    print(f'PASS cpu-tag-split: {runs} control/candidate lockstep runs (2 programs x 2 RAM models x 4 profiles x 2), '
          f'{caught} planted-fault runs caught ({len(FAULTS) - len(EQUIVALENT)} faults x 2 RAM models), '
          f'{len(EQUIVALENT)} equivalent mutation passes')


if __name__ == '__main__':
    main()
