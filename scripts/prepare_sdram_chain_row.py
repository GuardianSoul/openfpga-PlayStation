#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Register the DMA write-chain row compare one clk3x early (clk3x setup into a[10]).

psx_sdram_ctrl's OP_WRITE step 3 decides whether the next DMA FIFO word is
chained into the open row. Its 15-bit compare fifo_adr[24:10] == addr[24:10]
feeds a[10] (auto-precharge) in the same clk3x cycle: release_timing failed
by -0.176 ns on step[1] -> a[10] (0.6.0 batch F, #1371 / sign-off #1381).

This step computes the compare on the step-2 edge into `chain_row_q` and
uses that register at step 3. It is exact, not a guard: at the step-2 edge
`addr` does not change, and `take_fifo` is 0, so the head register after that
edge is `dmafifo_adr` when the head loads on it (!head_v && !dmafifo_empty &&
!dmafifo_read) and `head_adr` otherwise. The compare uses that next head.
Every other term of the chain decision is unchanged. CL3 (LATE_START) only.

Behaviour-preserving: checked by cycle-identical per-access logs against the
controller before it, with planted mutations (docs/timing-cpu-mem.md).
Usage: prepare_sdram_chain_row.py PROJECT
(post-prepare step; psx_sdram_ctrl.sv moves to PROJECT/sdram-chain-row/)
"""
import argparse
import re
from pathlib import Path

TAG = 'sdram-chain-row'
EDITS = (
    ('register', '    reg [5:0]  chain = 0;\n',
     '    reg [5:0]  chain = 0;\n'
     f'    // {TAG}: the step-3 chain row compare, taken on the step-2 edge against the\n'
     '    // head register as it will be after that edge (take_fifo is 0 there).\n'
     '    reg        chain_row_q = 0;\n'
     '    wire [14:0] chain_head_row = (!head_v && !dmafifo_empty && !dmafifo_read) ? dmafifo_adr[24:10] : head_adr[24:10];\n'),
    ('step 2', '                    OP_WRITE: if (step == 2) begin\n'
               '                        cmd <= C_WRITE; a <= column(addr, 9\'d0, 1\'b0); ba <= bank;\n',
     '                    OP_WRITE: if (step == 2) begin\n'
     '                        cmd <= C_WRITE; a <= column(addr, 9\'d0, 1\'b0); ba <= bank;\n'
     f'                        chain_row_q <= chain_head_row == addr[24:10]; // {TAG}\n'),
    ('step 3', '                        if (from_fifo && fifo_v && fifo_adr[24:10] == addr[24:10]\n',
     f'                        if (from_fifo && fifo_v && chain_row_q // {TAG}\n'),
)


def controller_text(text):
    if TAG in text:
        raise ValueError('controller already carries ' + TAG)
    if 'LATE_START = CAS_LATENCY == 3' not in text or 'wire [26:0] fifo_adr  = LATE_START ? head_adr : dmafifo_adr;' not in text:
        raise ValueError('needs the CL3 late-start controller with the DMA FIFO head register')
    for what, old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def apply(project):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    found = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/psx_sdram_ctrl\.sv)"', text)
    if len(found) != 1:
        raise SystemExit(f'{TAG}: expected one psx_sdram_ctrl.sv, found {found}')
    out = project / TAG
    out.mkdir(exist_ok=False)
    new = out / 'psx_sdram_ctrl.sv'
    new.write_text(controller_text(Path(found[0]).read_text()))
    qsf.write_text(text.replace(found[0], str(new)))
    return new


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    print('patched', apply(ap.parse_args().project))
