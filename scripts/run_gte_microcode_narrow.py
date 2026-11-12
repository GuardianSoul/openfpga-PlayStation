#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Check scripts/prepare_gte_microcode_narrow.py: the narrowed GTE microcode ROM equals the original.

1. Verilator elaborates the original PocketGTEMicroCode (the QSF-selected
   gte-adapter GTEMicroCode.sv, includes from the pinned PS-FPGA gte/rtl) and
   dumps all 256 entries (--x-assign 0 --x-initial 0: unassigned bits 0, as
   Quartus builds them). They must equal rtl/gte/gte_microcode_table.hex
   (--write-table writes it) and, with --mif, a fitted Quartus MIF (X as 0).
2. Lockstep: the original and the narrowed module (same source through
   narrow_text) get the same random PC, clock enable and other inputs every
   clock; every output is compared after every edge.
3. Planted faults that must be caught: a duplicate bit wired to the wrong
   narrow column, the constant bit inverted, the narrow register ignoring the
   clock enable.
"""
import argparse
import random
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_gte_microcode_narrow as narrow
from tool_paths import ROOT, find_tool

CYCLES = 20000


def verilator():
    bundled = ROOT / 'build/tools/oss-cad-suite/bin/verilator'
    v = str(bundled) if bundled.is_file() else find_tool('verilator')
    if not v:
        raise SystemExit('Verilator is required')
    return v


def run(directory, top, sources, include):
    directory.mkdir(parents=True, exist_ok=True)
    subprocess.run([verilator(), '--binary', '--timing', '-Wno-fatal', '-Wno-lint', '-Wno-style', '--x-assign', '0',
                    '--x-initial', '0', '-I' + str(include), '--top-module', top, '-Mdir', str(directory / 'obj'),
                    *map(str, sources)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return subprocess.run([str(directory / 'obj' / ('V' + top))], check=True, capture_output=True, text=True).stdout


DUMP_TB = '''`include "GTEDefine.hv"
module tb_dump;
  reg clk = 0; reg [7:0] pc = 0;
  gteComputeCtrl c; gteWriteBack w; wire l;
  PocketGTEMicroCode dut(.i_clk(clk), .i_ce(1'b1), .isNewInstr(1'b0), .Instruction(6'd0), .i_PC(pc),
                         .o_writeBack(w), .o_ctrl(c), .o_lastInstr(l));
  integer i;
  initial begin
    for (i = 0; i < 256; i = i + 1) begin
      pc = i[7:0]; #1 clk = 1; #1 clk = 0;
      $display("E %0d %022x", i, {c, w, l});
    end
    $finish;
  end
endmodule
'''


def lockstep_tb(seed):
    return f'''`include "GTEDefine.hv"
module tb_lockstep;
  reg clk = 0; reg [7:0] pc = 0; reg ce = 0; reg nw = 0; reg [5:0] ins = 0;
  gteComputeCtrl c1, c2; gteWriteBack w1, w2; wire l1, l2;
  PocketGTEMicroCode a(.i_clk(clk), .i_ce(ce), .isNewInstr(nw), .Instruction(ins), .i_PC(pc),
                       .o_writeBack(w1), .o_ctrl(c1), .o_lastInstr(l1));
  PocketGTEMicroCodeNarrow b(.i_clk(clk), .i_ce(ce), .isNewInstr(nw), .Instruction(ins), .i_PC(pc),
                             .o_writeBack(w2), .o_ctrl(c2), .o_lastInstr(l2));
  integer i, bad = 0, s = {seed};
  initial begin
    for (i = 0; i < {CYCLES}; i = i + 1) begin
      pc = $random(s); ce = ($random(s) & 3) != 0; nw = $random(s); ins = $random(s);
      #1 clk = 1; #1 clk = 0;
      if ({{c1, w1, l1}} !== {{c2, w2, l2}}) begin
        if (bad < 5) $display("MISMATCH cycle %0d pc %0d ce %0d: %022x vs %022x", i, pc, ce, {{c1, w1, l1}}, {{c2, w2, l2}});
        bad = bad + 1;
      end
    end
    $display("RESULT %0d mismatches in {CYCLES} cycles", bad);
    $finish;
  end
endmodule
'''


def dump(gte_sv, include, out):
    tb = out / 'tb_dump.sv'
    out.mkdir(parents=True, exist_ok=True)
    tb.write_text(DUMP_TB)
    text = run(out / 'dump', 'tb_dump', [gte_sv, tb], include)
    words = {int(m[1]): int(m[2], 16) for m in re.finditer(r'^E (\d+) ([0-9a-f]+)$', text, re.M)}
    if sorted(words) != list(range(256)):
        raise SystemExit('dump incomplete')
    return [words[i] for i in range(256)]


def mif_words(path):
    body = Path(path).read_text().split('CONTENT BEGIN')[1].split('END;')[0]
    words = {}
    for line in body.splitlines():
        m = re.match(r'\s*(\d+)\s*:\s*([01X]+);', line)
        if m:
            words[int(m[1])] = int(m[2].replace('X', '0'), 2)
    return [words[i] for i in range(256)]


def lockstep(gte_sv, include, out, words, fault=None, seed=1):
    columns, sources = narrow.plan(words)
    if fault == 'wrong-column':
        b = next(b for b, (k, v) in enumerate(sources) if k == 'rom' and columns[v] != b)   # a duplicate bit
        sources[b] = ('rom', (sources[b][1] + 1) % len(columns))
    elif fault == 'const-flip':
        b = next(b for b, (k, v) in enumerate(sources) if k == 'const')
        sources[b] = ('const', 1 - sources[b][1])
    out.mkdir(parents=True, exist_ok=True)
    hex_path = out / 'narrow.hex'
    hex_path.write_text(''.join(f'{w:0{(len(columns) + 3) // 4}x}\n' for w in narrow.narrow_words(words, columns)))
    text = narrow.narrow_text(Path(gte_sv).read_text(), hex_path, sources, len(columns), module='PocketGTEMicroCodeNarrow')
    if fault == 'ce-ignored':
        text = text.replace('always @(posedge i_clk) if (i_ce) begin\n\t\tnarrowQ', 'always @(posedge i_clk) begin\n\t\tnarrowQ', 1)
    narrow_sv = out / 'narrow.sv'
    narrow_sv.write_text(text)
    tb = out / 'tb_lockstep.sv'
    tb.write_text(lockstep_tb(seed))
    result = run(out / 'lockstep', 'tb_lockstep', [gte_sv, narrow_sv, tb], include)
    m = re.search(r'RESULT (\d+) mismatches', result)
    return int(m[1]) if m else None, result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('gte_sv', type=Path, help="the QSF-selected gte-adapter GTEMicroCode.sv")
    parser.add_argument('--include', type=Path, default=ROOT / 'references/PS-FPGA/rtl/gte/rtl')
    parser.add_argument('--mif', type=Path, help='a fitted Quartus microcode MIF to compare with')
    parser.add_argument('--write-table', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'build/sim/gte-microcode-narrow')
    args = parser.parse_args()
    if narrow.sha256(args.include / 'MicroCode.inl') != narrow.INL_SHA256:
        raise SystemExit('MicroCode.inl differs from the pinned one')
    failures = []
    words = dump(args.gte_sv, args.include, args.output)
    if args.write_table:
        narrow.TABLE.parent.mkdir(parents=True, exist_ok=True)
        narrow.TABLE.write_text(''.join(f'{w:022x}\n' for w in words))
        print(f'wrote {narrow.TABLE.relative_to(ROOT)}')
    if words != narrow.read_table():
        failures.append('elaborated table differs from rtl/gte/gte_microcode_table.hex')
    if args.mif and words != mif_words(args.mif):
        failures.append('elaborated table differs from the Quartus MIF')
    columns, sources = narrow.plan(words)
    print(f'table: 256 x 87, {len(columns)} stored columns, {sum(k == "const" for k, _ in sources)} constant bit(s)'
          + (', equals the Quartus MIF' if args.mif and 'MIF' not in ' '.join(failures) else ''))
    for seed in (1, 2, 3):
        bad, text = lockstep(args.gte_sv, args.include, args.output / f'seed{seed}', words, seed=seed)
        print(f'lockstep seed {seed}: {bad} mismatches in {CYCLES} cycles')
        if bad != 0:
            failures.append(f'lockstep seed {seed}: {bad}\n{text}')
    for fault in ('wrong-column', 'const-flip', 'ce-ignored'):
        bad, _ = lockstep(args.gte_sv, args.include, args.output / fault, words, fault=fault)
        print(f'fault {fault}: {"caught" if bad else "NOT caught"} ({bad} mismatches)')
        if not bad:
            failures.append(f'fault {fault} not caught')
    if failures:
        print('FAIL gte microcode narrow:\n  ' + '\n  '.join(failures))
        sys.exit(1)
    print('PASS gte microcode narrow')


if __name__ == '__main__':
    main()
