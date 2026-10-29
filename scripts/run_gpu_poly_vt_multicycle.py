#!/usr/bin/env python3
"""Sim check for the gpu_poly vertex -> muldivResult multicycle (prepare_gpu_poly_vt_multicycle.py).

The SDC gives paths from gpu_poly's vertex registers vt(*) to muldivResult1clk /
muldivResult2clk two clk2x cycles (setup 2, hold 1). That is true if no capture
of muldivResult1clk/2clk (states CALCCOLOR2 and CALCTEXTURE2, the only ones that
load them) happens at the clk2x edge right after a vt write (states ISSUE_READ1-3,
SORT_READ1-3, the only ones that write vt).

The candidate is the gpu-recip GPU of run_gpu_recip.py with a sim-only guard in
gpu_poly: mcvAge counts clk2x edges (with ce) since the last vt write, and in a
CALCCOLOR2/CALCTEXTURE2 cycle with mcvAge < MCV_MIN the captured products are
inverted. With MCV_MIN = 1 the guard fires exactly when a capture edge directly
follows a vt write edge, so the multicycle claim holds iff the candidate stays in
cycle lockstep with the unguarded GPU (run_gpu_dy_ram.py bench: every public
output on every clk2x edge, every GPUREAD word, both VRAMs, the oracle programs
plus Gouraud and textured polygon traffic).

Mutations (2026-10-29): MCV_MIN 4, 6, 7, 16, 32 and 48 stay in lockstep, 64
and an always-on guard fail: in this bench every capture comes 49-64 clk2x
edges after the last vt write (CALCBOUNDARY2 waits for its divider,
div1.done), against the two the SDC needs. The kept planted fault is
MCV_MIN 64, which shows the counter resets on vt writes and the guard acts.
"""
import argparse
import concurrent.futures
import json
import time

import run_gpu_dy_ram as dy
import run_gpu_poly_stepadd as stepadd
import run_gpu_recip as recip
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool

VT_WRITE_STATES = ('ISSUE_READ1', 'ISSUE_READ2', 'ISSUE_READ3', 'SORT_READ1', 'SORT_READ2', 'SORT_READ3')
CAPTURE_STATES = ('CALCCOLOR2', 'CALCTEXTURE2')
FAULTS = {'guard_min_64': 64}
DECL = '   signal mulStep             : integer range 0 to 7;\n'


def guard_text(text, minimum=1):
    """gpu_poly text with the sim-only multicycle guard (never a build source)."""
    for name in VT_WRITE_STATES:
        if text.count(f'when {name} =>') != 1: raise ValueError(f'gpu_poly state {name} changed')
    if text.count('vt(0) <= readVertex;') != 2 or text.count('vt(1) <= readVertex;') != 2 or \
            text.count('vt(2) <= readVertex;') != 2:
        raise ValueError('gpu_poly vertex writes changed')
    for reg in ('1', '2'):
        if text.count(f'muldivResult{reg}clk <= muldivResult{reg};') != 10:
            raise ValueError('gpu_poly muldivResult captures changed')
    if text.count(DECL) != 1: raise ValueError('gpu_poly mulStep declaration changed')
    text = text.replace(DECL, DECL +
        f'   signal mcvAge              : integer range 0 to 255 := 255; -- sim-only multicycle guard\n'
        f'   signal mcvBad              : std_logic;\n'
        f'   function mcvGuard(v : signed; bad : std_logic) return signed is\n'
        f'   begin\n      if (bad = \'1\') then return not v; end if;\n      return v;\n   end function;\n')
    head = text.index('architecture')
    begin = text.index('\nbegin\n', head) + len('\nbegin\n')
    writes = ' or '.join(f'state = {s}' for s in VT_WRITE_STATES)
    captures = ' or '.join(f'state = {s}' for s in CAPTURE_STATES)
    guard = (f"   mcvBad <= '1' when (({captures}) and mcvAge < {minimum}) else '0';\n"
             '   process (clk2x)\n   begin\n      if rising_edge(clk2x) then\n         if (ce = \'1\') then\n'
             f'            if ({writes}) then mcvAge <= 0;\n'
             '            elsif (mcvAge < 255) then mcvAge <= mcvAge + 1; end if;\n'
             '         end if;\n      end if;\n   end process;\n\n')
    text = text[:begin] + guard + text[begin:]
    for reg in ('1', '2'):
        text = text.replace(f'muldivResult{reg}clk <= muldivResult{reg};',
                            f'muldivResult{reg}clk <= mcvGuard(muldivResult{reg}, mcvBad);')
    return text


def source_text(name, candidate, fault):
    text, origin = recip.source_text(name, True, None)
    if candidate and name == 'gpu_poly.vhd':
        text = guard_text(text, FAULTS[fault] if fault else 1)
    return text, origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-faults', action='store_true')
    args = parser.parse_args()
    # The guard's anchors must also hold on the release file this SDC is for.
    release = ROOT / 'build/claude-fit/rel057c/gpu-recip/gpu_poly.vhd'
    if release.is_file(): guard_text(release.read_text())
    if ('work', recip.UNIT_NAME) not in dy.SOURCES:
        dy.SOURCES.insert(dy.SOURCES.index(('work', 'gpu.vhd')), ('work', recip.UNIT_NAME))
    dy.source_text = source_text; dy.make_bench = stepadd.make_bench; dy.FAULTS = FAULTS
    ghdl = find_tool('ghdl'); verilator = find_tool('verilator')
    if not ghdl or not verilator: raise SystemExit('GHDL and Verilator required')
    started = time.time()
    directory = ROOT / 'build/sim/gpu-poly-vt-multicycle'; directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'baseline.log').open('w') as log:
        baseline = dy.transcode(directory / 'original', False, None, ghdl, log)
    runs = [(directory, None)] + ([] if args.no_faults else [(directory / ('fault-' + f), f) for f in FAULTS])
    with concurrent.futures.ThreadPoolExecutor(len(runs)) as pool:
        results = list(pool.map(lambda r: dy.run(r[0], baseline, r[1], ghdl, verilator, max(2, 12 // len(runs))), runs))
    for result in results:
        print(f"{result['fault'] or 'candidate'}: {'PASS' if result['passed'] else 'FAIL'} exit={result['exit_code']} "
              f"ticks={result['cycle_ticks']} programs={result['programs']} memory_checks={result['memory_checks']} "
              f"seconds={result['seconds']}")
        if result['coverage']: print('  coverage', ' '.join(f'{k}={v}' for k, v in result['coverage'].items()))
        if result['first_failure']: print('  ' + '\n  '.join(result['first_failure']))
    main_result, faults = results[0], results[1:]
    rejected = {r['fault']: not r['passed'] for r in faults}
    passed = main_result['passed'] and all(rejected.values())
    report = {'passed': passed, 'seconds': round(time.time() - started, 1), 'candidate': main_result,
              'planted_faults_rejected': rejected, 'baseline_verilog_sha256': digest(baseline[1]),
              'claim': 'no CALCCOLOR2/CALCTEXTURE2 capture at the clk2x edge right after a vt write',
              'hardware_verified': False}
    (directory / 'report.json').write_text(json.dumps(report, indent=2, default=str) + '\n')
    for fault, ok in rejected.items(): print(f'Planted fault {"rejected" if ok else "NOT DETECTED"}: {fault}')
    print(f"{'PASS' if passed else 'FAIL'} GPU poly vt multicycle guard ({report['seconds']} s)")
    if not passed: raise SystemExit(1)


if __name__ == '__main__':
    main()
