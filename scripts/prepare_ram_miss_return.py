#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Main-RAM read return one CPU clock earlier after a row conflict.

Peip TimingLoadRAM / PipelineRAMread: an uncached CPU load that has to close
another open row returns one clk1x later than a PS1 (docs/timing-cpu-mem.md).
psx_sdram_ctrl copies every clk1x-facing ch1 result twice: x_ch1_dout32 at
the before_base clk3x edge, then ch1_dout32/ch1_ready at clk_base. This step
(Codex's selective return, a5e480f2, in the later one-bank form of the
lookahead rig):

- sets `miss_read_return` when the controller issues PRECHARGE for a pending
  uncached, non-DMA ch1 read to a kept-open main-RAM bank (a real row
  conflict); a new ch1 capture or init clears it;
- while it is set, ch1_ready is the before_base copy (one clk1x earlier) and
  the clk_base copy is suppressed, so the request still sees one pulse;
- one 32-bit publish register replaces x_ch1_dout32 and ch1_dout32: it loads
  ch1_word on the clk3x edge between before_base and after_base (normal reads,
  the old clk_base copy's timing) or on the before_base edge (row-conflict
  reads). 32 fewer registers than before.

Row hits, cache fills, DMA, ch2/ch3, refresh, SDRAM commands and chip timing
are unchanged. Guest-visible on purpose: no equivalence claim; the Peip rows
and the strict-chip golden tests decide.
Usage: prepare_ram_miss_return.py PROJECT
(post-prepare step; psx_sdram_ctrl.sv moves to PROJECT/ram-miss-return/)
"""
import argparse
import re
from pathlib import Path

TAG = 'ram-miss-return'
EDITS = (
    ('ch1 data port', '    output reg  [31:0]  ch1_dout32 = 0,\n', '    output wire [31:0]  ch1_dout32,\n'),
    ('ch1 ready port', '    output reg          ch1_ready = 0,\n', '    output wire         ch1_ready,\n'),
    ('x registers',
     '    reg [31:0] x_ch1_dout32 = 0, x_ch2_dout = 0, x_dma_data = 0;\n'
     '    always @(posedge clk_base) begin\n'
     '        ch1_ready <= x_ch1_ready; ch1_dout32 <= x_ch1_dout32;\n',
     f'    reg [31:0] x_ch2_dout = 0, x_dma_data = 0;\n'
     f'    // {TAG}: set by the PRECHARGE of a ch1 uncached read row conflict.\n'
     f'    reg miss_read_return = 0;\n'
     f'    reg ch1_ready_q = 0;\n'
     f'    reg [31:0] ch1_publish_word = 0;\n'
     f'    // CPU-facing copy of x_ch1_ready: own register, short route into the CPU (K #1446)\n'
     f'    (* preserve *) reg x_ch1_ready_cpu = 0;\n'
     f'    assign ch1_ready = miss_read_return ? x_ch1_ready_cpu : ch1_ready_q;\n'
     f'    assign ch1_dout32 = ch1_publish_word;\n'
     f'    always @(posedge clk_base) begin\n'
     f'        ch1_ready_q <= x_ch1_ready && !miss_read_return;\n'),
    ('publish bank', '    wire ev_ch3       = ch3_delay == 1;\n',
     '    wire ev_ch3       = ch3_delay == 1;\n'
     f'    // {TAG}: one bank holds the public word. A normal read loads it on the\n'
     '    // clk3x edge of the old clk_base copy (ch1_word holds the word by then),\n'
     '    // a row-conflict read on before_base.\n'
     '    always @(posedge clk) if (SDRAM_EN) begin\n'
     '        if (!after_base && !before_base && x_ch1_ready)\n'
     '            ch1_publish_word <= ch1_word;\n'
     '        if (before_base && miss_read_return && (p_ch1 || ev_ch1_ready) && !(OPEN_ROW != 0 ? init_q : init))\n'
     '            ch1_publish_word <= ev_ch1_word ? word : ch1_word;\n'
     '    end\n'),
    ('x data load', '                if (p_ch1 || ev_ch1_ready) x_ch1_dout32 <= ev_ch1_word ? word : ch1_word;\n', ''),
    ('precharge', '                    cmd <= C_PRE; a[10] <= need_ref;\n',
     '                    cmd <= C_PRE; a[10] <= need_ref;\n'
     '                    if (!need_ref && !spu_first && !fifo_v && req1 && ch1_rnw && !ch1_dma && !ch1_cache && keep_open(ch1_a))\n'
     f'                        miss_read_return <= 1; // {TAG}\n'),
    ('cpu copy', '                x_ch1_ready <= p_ch1 || ev_ch1_ready || start_ch1w;\n',
     '                x_ch1_ready <= p_ch1 || ev_ch1_ready || start_ch1w;\n'
     '                x_ch1_ready_cpu <= p_ch1 || ev_ch1_ready || start_ch1w;\n'),
    ('capture', 'if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr; end',
     f'if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr; miss_read_return <= 0; end // {TAG}'),
    ('init', '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0;\n',
     f'                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0; miss_read_return <= 0; // {TAG}\n'),
)


def controller_text(text):
    if TAG in text:
        raise ValueError('controller already carries ' + TAG)
    if 'LATE_START = CAS_LATENCY == 3' not in text:
        raise ValueError('needs the CL3 late-start controller (every ch1 request is captured first)')
    for what, old, new in EDITS:
        if text.count(old) != 1:
            raise ValueError(f'{what}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    if 'x_ch1_dout32' in text:
        raise ValueError('x_ch1_dout32 still used')
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
