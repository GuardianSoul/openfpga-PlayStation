#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Main-RAM DMA read words one CPU clock earlier after a row conflict or closed row.

psx_sdram_ctrl publishes each DMA word twice: x_dma_wr / x_dma_data on the
before_base clk3x edge, then dma_wr / dma_data on clk_base. When a DMA read
has to open its row first (refresh or another client closed it), the extra
ACTIVE pushes the words across that second boundary. This step (Codex's
DMA_MISS_RETURN, docs/native-dma-return.md on codex/native-dma-return-058,
without that branch's M10K DMA ring) sets `dma_miss_return` when a ch1 DMA
read to a kept-open main-RAM bank is started on a row that is not open, and
only when no old read word, DMA word or strobe is in flight; overlapping
streams keep their mode. While set, dma_wr / dma_data are the before_base
copies and the clk_base copy of dma_wr is suppressed.

Peip DMA rows on the native rig (docs/timing-cpu-mem.md). Guest-visible on
purpose: no equivalence claim.
Usage: prepare_dma_miss_return.py PROJECT
(post-prepare step; psx_sdram_ctrl.sv moves to PROJECT/dma-miss-return/)
"""
import argparse
import re
from pathlib import Path

TAG = 'dma-miss-return'
EDITS = (
    ('dma_wr port', '    output reg          dma_wr = 0,\n', '    output wire         dma_wr,\n'),
    ('dma_data port', '    output reg  [31:0]  dma_data = 0,\n', '    output wire [31:0]  dma_data,\n'),
    ('copy', 'dma_reqprocessed <= x_dma_reqprocessed; dma_wr <= x_dma_wr; dma_data <= x_dma_data;',
     f'dma_reqprocessed <= x_dma_reqprocessed; dma_wr_q <= x_dma_wr && !dma_miss_return; dma_data_q <= x_dma_data; // {TAG}'),
    ('registers', '    always @(posedge clk_base) begin\n',
     f'    // {TAG}: before_base publication for a DMA read that opened its row.\n'
     '    reg dma_miss_return = 0;\n'
     '    reg dma_wr_q = 0;\n'
     '    reg [31:0] dma_data_q = 0;\n'
     '    assign dma_wr = dma_miss_return ? x_dma_wr : dma_wr_q;\n'
     '    assign dma_data = dma_miss_return ? x_dma_data : dma_data_q;\n'
     '    always @(posedge clk_base) begin\n'),
    ('take', '                    took1 = 1; pend1 <= 0;\n',
     '                    took1 = 1; pend1 <= 0;\n'
     f'                    // {TAG}: change mode only with no old read/DMA word in flight.\n'
     '                    if (!(|rd_v) && !q_v && dma_n == 0 && !x_dma_wr && !dma_wr_q)\n'
     '                        dma_miss_return <= ch1_dma && ch1_rnw && !ch1_cache && keep_open(ch1_a) && sel_st != ST_HIT;\n'),
    ('init', '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0;',
     '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0; dma_miss_return <= 0;'),
)


def controller_text(text):
    if TAG in text:
        raise ValueError('controller already carries ' + TAG)
    if 'LATE_START = CAS_LATENCY == 3' not in text:
        raise ValueError('needs the CL3 late-start controller')
    for what, old, new in EDITS:
        n = text.count(old)
        if what == 'registers':
            # the first clk_base block is the clk1x-facing copy
            if n < 1:
                raise ValueError(f'{what}: not found')
            text = text.replace(old, new, 1)
            continue
        if n != 1:
            raise ValueError(f'{what}: expected exactly one match, found {n}')
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
