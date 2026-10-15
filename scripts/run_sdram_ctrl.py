#!/usr/bin/env python3
"""Simulate rtl/memory/psx_sdram_ctrl.sv against a strict SDRAM chip model.

Random PSX-like traffic on every channel (CPU reads, cache fills, DMA reads
and writes, SPU and loader accesses, forced refreshes) is checked against a
golden memory, and the chip model reports every command-timing violation.
When the pocket_sotn submodule is present, its sdram.sv runs the same traffic
side by side and the per-access latencies are compared. The reference is
only simulated here; it is never part of a build.

  python3 scripts/run_sdram_ctrl.py [--ops N] [--seed S]
"""
import argparse
import collections
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tempfile

from tool_paths import ROOT, find_tool

ALTDDIO = """module altddio_out #(parameter extend_oe_disable="", intended_device_family="", invert_output="",
    lpm_hint="", lpm_type="", oe_reg="", power_up_high="", integer width=1) (
    input wire [width-1:0] datain_h, datain_l, input wire outclock, output wire [width-1:0] dataout,
    input wire aclr, aset, oe, outclocken, sclr, sset);
    assign dataout = oe ? (outclock ? datain_h : datain_l) : {width{1'bz}};
endmodule
"""


def reference_copy(work):
    """The reference declares its DQ port as `inout reg`, which Icarus rejects."""
    source = ROOT/'references/pocket_sotn/rtl/sdram.sv'
    if not source.is_file(): return None
    text = source.read_text()
    text, ports = re.subn(r'inout\s+reg\s+\[15:0\]\s+SDRAM_DQ\s*,', 'inout wire [15:0] SDRAM_DQ,', text)
    text, drives = re.subn(r'\bSDRAM_DQ(\s*<=)', r'SDRAM_DQ_r\1', text)
    if ports != 1 or not drives: raise ValueError('Reference sdram.sv DQ port changed')
    text = text.replace(');\n', ");\nreg [15:0] SDRAM_DQ_r = 16'hzzzz;\nassign SDRAM_DQ = SDRAM_DQ_r;\n", 1)
    path = work/'reference_sdram.sv'; path.write_text(text)
    return path


def latencies(log):
    kinds = collections.defaultdict(list); summary = None
    for line in log.read_text().splitlines():
        words = line.split()
        if words[0] == 'summary': summary = dict(zip(words[1::2], words[2::2]))
        else: kinds[words[0]].append(int(words[2]) - int(words[1]))
    return kinds, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--ops', type=int, default=4000)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--no-reference', action='store_true')
    parser.add_argument('--cas', type=int, choices=(2, 3), default=2, help='controller and chip CAS latency (the reference runs only at 2)')
    parser.add_argument('--dclk', type=float, default=2.0, help='ns: chip clock delay from the FPGA clock edge to the chip')
    parser.add_argument('--tret', type=float, default=2.0, help='ns: data return delay from the chip to the FPGA input register')
    parser.add_argument('--tac', type=float, help='ns: chip clock-to-data max (default: datasheet 6.0 at CL2, 5.5 at CL3)')
    parser.add_argument('--toh', type=float, default=2.5, help='ns: chip data hold min')
    parser.add_argument('--compare-cl2', action='store_true', help='with --cas 3: also run CL2 on the same traffic and fail if any access kind idle latency grows by more than one clk1x (mean: 1.5, queueing)')
    parser.add_argument('--expect-fail', action='store_true', help='control: pass only if the controller misreads (delays outside its capture window)')
    args = parser.parse_args()
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    if not iverilog or not vvp: sys.exit('Icarus Verilog not found (scripts/setup_iverilog.sh)')
    parent = ROOT/'build/sim/sdram-ctrl'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    (work/'altddio_out.sv').write_text(ALTDDIO)
    reference = None if args.no_reference or args.cas != 2 else reference_copy(work)
    tac = args.tac if args.tac is not None else (6.0 if args.cas == 2 else 5.5)
    sources = [work/'altddio_out.sv', ROOT/'rtl/memory/psx_sdram_ctrl.sv', ROOT/'sim/memory/sdram_chip_model.sv',
               ROOT/'sim/memory/tb_psx_sdram_ctrl.sv'] + ([reference] if reference else [])
    defines = ['-DWITH_REFERENCE'] if reference else []
    params = [f'-Ptb_psx_sdram_ctrl.OPS={args.ops}', f'-Ptb_psx_sdram_ctrl.SEED={args.seed}',
              f'-Ptb_psx_sdram_ctrl.CAS={args.cas}', f'-Ptb_psx_sdram_ctrl.DCLK={args.dclk}',
              f'-Ptb_psx_sdram_ctrl.TAC={tac}', f'-Ptb_psx_sdram_ctrl.TOH={args.toh}', f'-Ptb_psx_sdram_ctrl.TRET={args.tret}']
    subprocess.run([iverilog, '-g2012', '-o', str(work/'tb.vvp'), '-s', 'tb_psx_sdram_ctrl', *defines, *params,
                    *map(str, sources)], check=True)
    run = subprocess.run([vvp, '-n', str(work/'tb.vvp')], cwd=work, capture_output=True, text=True, timeout=3600)
    (work/'sim.log').write_text(run.stdout + run.stderr)
    ours, ours_summary = latencies(work/'ours.log')
    report = dict(work=str(work), ours=ours_summary, latency={})
    failed = 'ERROR' in run.stdout or not ours_summary or ours_summary.get('errors') != '0'
    for line in run.stdout.splitlines():
        if line.startswith('ERROR') and 'reference' not in line: print(line)
    if reference:
        ref, ref_summary = latencies(work/'reference.log'); report['reference'] = ref_summary
        failed = any(l.startswith('ERROR') and 'reference' not in l for l in run.stdout.splitlines()) or ours_summary.get('errors') != '0'
    for kind in sorted(ours):
        row = dict(ours=dict(min=min(ours[kind]), mean=round(statistics.mean(ours[kind]), 2), max=max(ours[kind])))
        if reference and ref.get(kind):
            row['reference'] = dict(min=min(ref[kind]), mean=round(statistics.mean(ref[kind]), 2), max=max(ref[kind]))
        report['latency'][kind] = row
    if reference:
        for kind, row in report['latency'].items():
            if kind == 'boot' or 'reference' not in row: continue   # power-up wait differs on purpose
            if row['ours']['min'] != row['reference']['min']:
                print(f'{kind}: idle latency {row["ours"]["min"]} clk1x, reference {row["reference"]["min"]}'); failed = True
            if row['ours']['mean'] > row['reference']['mean'] * 1.15 + 0.2:
                print(f'{kind}: mean latency {row["ours"]["mean"]} vs reference {row["reference"]["mean"]}'); failed = True
    (work/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if args.expect_fail:
        if not failed: sys.exit(f'psx_sdram_ctrl control FAILED: no error outside the capture window, see {work}/sim.log')
        print('psx_sdram_ctrl control passed: misreads detected outside the capture window'); return
    if failed: sys.exit(f'psx_sdram_ctrl FAILED, see {work}/sim.log')
    if args.compare_cl2 and args.cas == 3:
        # The same traffic at CL2: CL3 costs one clk3x per read, which can
        # cross one clk1x hand-off edge (29.5 ns) but never more.
        base = subprocess.run([sys.executable, __file__, '--ops', str(args.ops), '--seed', str(args.seed), '--cas', '2',
                               '--dclk', str(args.dclk), '--tret', str(args.tret), '--toh', str(args.toh), '--no-reference'],
                              capture_output=True, text=True, timeout=3600)
        if base.returncode: sys.exit('CL2 comparison run failed:\n' + base.stdout[-2000:])
        cl2 = json.loads((Path(re.search(r'"work": "([^"]+)"', base.stdout).group(1))/'report.json').read_text())['latency']
        growth = {}
        for kind, row in report['latency'].items():
            if kind == 'boot' or kind not in cl2: continue
            dmin = row['ours']['min'] - cl2[kind]['ours']['min']; dmean = row['ours']['mean'] - cl2[kind]['ours']['mean']
            growth[kind] = dict(min=dmin, mean=round(dmean, 2))
            # idle (min) latency may grow by one clk1x; the mean also carries
            # queueing behind other reads that each grew too
            if dmin > 1 or dmean > 1.5: sys.exit(f'{kind}: CL3 grows latency by {dmin} (min) / {dmean:.2f} (mean) clk1x vs CL2')
        print('CL3 vs CL2 latency growth (clk1x):', json.dumps(growth))
    print('psx_sdram_ctrl passed:', ours_summary)


if __name__ == '__main__': main()
