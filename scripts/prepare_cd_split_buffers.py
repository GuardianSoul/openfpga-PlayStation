#!/usr/bin/env python3
"""CD stream sector buffers as two RAMs once they pass 2,048 words.

psx_cd_stream keeps its NBUF sector buffers (588 words of 32 bits each) in
one RAM. Up to 2,048 words that is what it was; with --cd-buffers 4 (2,352
words) Quartus slices the single RAM into 16 M10K (0.5.7-test5: 297 -> 306
blocks for the whole core). Here the first 2,048 words and the rest are two
RAMs (8 + 2 M10K in a standalone fit, 9 with packing; NBUF 3 stays one
RAM, 7 M10K, as NBUF 2). Every write lands at the same word, every read
returns the same word one clk after rd_addr, as before. With NBUF 2 or 3
the generated block is the original single RAM.
Check: make test-cd-stream with --split (scripts/run_cd_stream.py --split
[--side-slot]); its split-select and split-write faults must be caught.
Usage: prepare_cd_split_buffers.py PROJECT (after prepare_pocket_early.py
and the CD side-slot step, on the QSF-selected psx_cd_stream.sv)
"""
from pathlib import Path
import re
import sys

EDITS = [
    ('''    (* ramstyle="M10K" *) reg [31:0] sectors[0:NBUF*WORDS-1];
''', '''    // The sector buffers: one RAM up to 2,048 words. Beyond that (NBUF 4:
    // 2,352 words) Quartus slices a single 32-bit RAM into 16 M10K; a
    // 2,048-word part plus the rest takes 8 + 2. Same contents and timing.
    localparam integer DEPTH=NBUF*WORDS;
    localparam integer LO=DEPTH>2048 ? 2048 : DEPTH;
    localparam integer HI=DEPTH-LO;
'''),
    ('''    always @(posedge host_clk) if(accept_word) sectors[write_index]<=bridge_data;
''', '''    reg [SB+9:0] rd_addr=0;
    wire [31:0] q;   // sector word at rd_addr, one clk after it is set
    generate if(HI==0) begin : one_ram
        (* ramstyle="M10K" *) reg [31:0] sectors[0:DEPTH-1];
        reg [31:0] q_r=0;
        always @(posedge host_clk) if(accept_word) sectors[write_index]<=bridge_data;
        always @(posedge clk) q_r<=sectors[rd_addr];
        assign q=q_r;
    end else begin : split_ram
        localparam integer HB=HI>1 ? $clog2(HI) : 1;
        localparam [SB+9:0] LO_A=LO;
        (* ramstyle="M10K" *) reg [31:0] sectors_lo[0:LO-1];
        (* ramstyle="M10K" *) reg [31:0] sectors_hi[0:HI-1];
        reg [31:0] q_lo=0, q_hi=0;
        reg hi_sel=0;
        wire [SB+9:0] write_hi=write_index-LO_A, read_hi=rd_addr-LO_A;
        always @(posedge host_clk) if(accept_word) begin
            if(write_index<LO_A) sectors_lo[write_index[10:0]]<=bridge_data;
            else sectors_hi[write_hi[HB-1:0]]<=bridge_data;
        end
        always @(posedge clk) begin
            q_lo<=sectors_lo[rd_addr[10:0]];
            q_hi<=sectors_hi[read_hi[HB-1:0]];
            hi_sel<=rd_addr>=LO_A;
        end
        assign q=hi_sel ? q_hi : q_lo;
    end endgenerate
'''),
    ('''    reg [SB+9:0] rd_addr=0;
    reg [31:0] q=0;
''', ''),
    ('''    always @(posedge clk) q<=sectors[rd_addr];
''', ''),
]


def split_text(text):
    for old, new in EDITS:
        if text.count(old) != 1: raise ValueError('cd-split-buffers: psx_cd_stream.sv changed: ' + old.strip()[:60])
        text = text.replace(old, new)
    return text


def prepare_project(project):
    project = Path(project).resolve(); qsf = project/'apf_top.qsf'; q = qsf.read_text()
    hits = re.findall(r'"([^"]*/psx_cd_stream\.sv)"', q)
    if len(hits) != 1: raise ValueError('cd-split-buffers: expected one psx_cd_stream.sv in the project')
    out = project/'cd-split-buffers'/'psx_cd_stream.sv'; out.parent.mkdir(exist_ok=True)
    out.write_text('// Modified by scripts/prepare_cd_split_buffers.py: see its docstring.\n' + split_text(Path(hits[0]).read_text()))
    qsf.write_text(q.replace(f'"{hits[0]}"', f'"{out}"'))
    return out


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__.split('Usage: ')[1])
    print(prepare_project(sys.argv[1]))
