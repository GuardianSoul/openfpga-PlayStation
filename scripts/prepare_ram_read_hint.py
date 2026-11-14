#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU main-RAM read hint: close a conflicting SDRAM row while memorymux waits.

memorymux holds every uncached RAM load in WAITFORRAMREAD for the PS1's
wait before it sends the read to psx_sdram_ctrl; on a row conflict the
controller only then issues PRECHARGE, ACTIVE and READ. This step (Codex's
READ_HINT, the lookahead rig's best profile) sends the load's address to the
controller when the CPU presents it. If that row is not open and nothing
else is waiting, the controller precharges the bank right away (never
ACTIVE or READ: the real request still opens the row), and the real read
then returns on the before_base edge like a row-conflict read
(prepare_ram_miss_return.py, required first).

Hint: psx_top data loads (mem_request, mem_rnw, mem_isData) below 8 MiB
while no DMA owns the bus, in ram_Adr format (save_mode keeps bits 22..21).
Path: psx_top -> psx_mister_psx_top_resource -> core_top -> psx_core_memory
-> psx_sdram_ctrl ch1_hint_req / ch1_hint_addr. The controller takes it on
after_base like ch1_req; a real ch1 capture consumes it. All real requests,
refresh and the DMA FIFO keep priority over the hint PRECHARGE, which waits
while refresh is due within 3 clk.

Third form (K'' #1466): psx_top registers the hint on clk1x (one CPU clock
later; the CPU's forwarding path no longer reaches the controller), the PRECHARGE
condition is a register (`hint_go_q`, one clk3x ahead), and the hint PRECHARGE
writes only A10 (the chip ignores the other address bits).

Registered form (K fit #1446/#1447 failed release_timing on the first form):
the row state is computed from the registered hint address one clk3x after
the capture, `ref_timer > 3` is registered a cycle ahead (exact), and no clk1x
request line takes part in the PRECHARGE decision (a request that arrives on
that edge waits for the PRECHARGE, tRP).

Guest-visible on purpose: no equivalence claim; Peip rows decide
(docs/timing-cpu-mem.md).
Usage: prepare_ram_read_hint.py PROJECT  (after prepare_ram_miss_return.py)
"""
import argparse
import re
from pathlib import Path

TAG = 'ram-read-hint'

CTRL = (
    ('ports', '    input  wire [26:0]  ch1_addr,\n',
     '    input  wire [26:0]  ch1_addr,\n'
     f'    input  wire         ch1_hint_req,   // {TAG}\n'
     '    input  wire [26:0]  ch1_hint_addr,\n'),
    ('registers', '    reg [5:0]  chain = 0;\n',
     '    reg [5:0]  chain = 0;\n'
     f'    // {TAG}: a latched CPU load address, never a memory access by itself.\n'
     '    reg hint_v = 0, hint_precharged = 0;\n'
     '    reg [26:0] hint_addr = 0;\n'
     '    reg [1:0] hint_st = 0;\n'
     '    // ref_timer > 3 on the next edge, registered (the reload after 0 is 779)\n'
     '    reg hint_ref_ok = 0;\n'
     '    // the hint PRECHARGE condition, decided one edge ahead (out of the pin cone)\n'
     '    reg hint_go_q = 0;\n'
     '    // a hint lives 6 clk3x (memorymux sends a hinted load within 2 clk1x); a CPU\n'
     '    // request held for several clk1x re-arms it, and a stale one must not precharge\n'
     '    reg [2:0] hint_age = 0;\n'),
    ('init', '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0;',
     '                ready <= 0; power_wait <= 0; power_done <= 0; init_step <= 0; hint_v <= 0; hint_precharged <= 0;'),
    ('idle precharge',
     '                    else begin chosen = OP_WRITE; from_fifo <= 0; ch3_write <= 1; last <= 6; end\n'
     '                end\n'
     '                if (chosen != OP_IDLE) begin op <= chosen; step <= 1; end\n',
     '                    else begin chosen = OP_WRITE; from_fifo <= 0; ch3_write <= 1; last <= 6; end\n'
     f'                end else if (OPEN_ROW != 0 && hint_go_q && // {TAG}\n'
     '                             !pend3_new && dmafifo_empty && !dmafifo_read && hint_ref_ok) begin\n'
     '                    // All real requests win. Only close this bank; the real\n'
     '                    // request still issues ACTIVE/READ.\n'
     '                    cmd <= C_PRE; a[10] <= 1\'b0; ba <= bank_of(hint_addr); ncs <= 0; // one bank\n'
     '                    open_v[hint_addr[10]] <= 0;\n'
     '                    hint_precharged <= 1;\n'
     '                    op <= OP_PRE; step <= 1; last <= 1;\n'
     '                end\n'
     '                if (chosen != OP_IDLE) begin op <= chosen; step <= 1; end\n'),
    ('capture',
     'if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr; miss_read_return <= 0; end // ram-miss-return',
     'if (after_base && ch1_req && !took1) begin pend1 <= 1; ch1_adr_q <= ch1_addr;\n'
     f'                // {TAG}: a hinted read whose row was closed early returns like a row conflict.\n'
     '                miss_read_return <= hint_precharged && hint_addr[26:2] == ch1_addr[26:2] && ch1_rnw && !ch1_dma && !ch1_cache && keep_open(ch1_addr);\n'
     '            end'),
    ('hint capture', '            st3 <= bank_state(ch3_adr_q);\n',
     '            st3 <= bank_state(ch3_adr_q);\n'
     f'            if (OPEN_ROW != 0 && ready && !init_q) begin // {TAG}\n'
     '                if (after_base && ch1_hint_req) begin\n'
     '                    hint_v <= 1; hint_precharged <= 0; hint_addr <= ch1_hint_addr; hint_age <= 0;\n'
     '                end else if (hint_v) begin\n'
     '                    if (hint_age == 3\'d5) hint_v <= 0; else hint_age <= hint_age + 1\'b1;\n'
     '                end\n'
     '                // ch1_hint_addr is a clk1x register (psx_top), like ch1_addr for st1\n'
     '                hint_st <= row_state(after_base && ch1_hint_req ? ch1_hint_addr : hint_addr);\n'
     '                // The real read consumes the hint; a stale one is never reused.\n'
     '                if (after_base && ch1_req) begin hint_v <= 0; hint_precharged <= 0; end\n'
     '            end\n'
     '            hint_ref_ok <= ref_timer > 4 || ref_timer == 0;\n'
     '            hint_go_q <= OPEN_ROW != 0 && ready && !init_q && hint_v && !hint_precharged && hint_st == ST_MISS &&\n'
     '                         hint_addr[26:21] == 0 && !(after_base && (ch1_hint_req || ch1_req));\n'),
)
CORE_MEMORY = (
    ('port', '    input  wire        ram_refresh,\n',
     '    input  wire        ram_refresh,\n'
     f'    input  wire        ram_hint_req,   // {TAG}\n'
     '    input  wire [24:0] ram_hint_adr,\n'),
    ('controller', '.ch1_addr({2\'b00, ram_adr}),',
     f'.ch1_hint_req(ram_hint_req && !cpu_reset), .ch1_hint_addr({{2\'b00, ram_hint_adr}}), // {TAG}\n'
     '        .ch1_addr({2\'b00, ram_adr}),'),
)
CORE_TOP = (
    ('wires', '    wire ram_refresh;\n',
     f'    wire ram_refresh;\n    wire ram_hint_req; wire [24:0] ram_hint_adr; // {TAG}\n'),
    ('memory', '        .ram_refresh(ram_refresh),.ram_ena(ram_ena),',
     '        .ram_refresh(ram_refresh),.ram_hint_req(ram_hint_req),.ram_hint_adr(ram_hint_adr),.ram_ena(ram_ena),'),
    ('psx', '\n.ram_refresh(ram_refresh),\n',
     '\n.ram_refresh(ram_refresh),\n.ram_hint_req(ram_hint_req),.ram_hint_adr(ram_hint_adr),\n'),
)
RESOURCE = (
    ('port', '    ram_refresh : out std_logic;\n',
     '    ram_refresh : out std_logic;\n'
     f'    ram_hint_req : out std_logic; -- {TAG}\n'
     '    ram_hint_adr : out std_logic_vector(24 downto 0);\n'),
    ('map', '        ram_refresh => ram_refresh,\n',
     '        ram_refresh => ram_refresh,\n'
     '        ram_hint_req => ram_hint_req,\n'
     '        ram_hint_adr => ram_hint_adr,\n'),
)
PSX_TOP = (
    ('port', '      ram_refresh           : out std_logic;\n',
     '      ram_refresh           : out std_logic;\n'
     f'      ram_hint_req          : out std_logic; -- {TAG}\n'
     '      ram_hint_adr          : out std_logic_vector(24 downto 0);\n'),
    ('logic', '   ram_refresh   <= reset_intern;\n',
     '   ram_refresh   <= reset_intern;\n'
     f'   -- {TAG}: a CPU data load from main RAM, as the CPU presents it to memorymux,\n'
     '   -- registered on clk1x (the CPU address path does not reach the clk3x controller).\n'
     '   process (clk1x)\n'
     '   begin\n'
     '      if rising_edge(clk1x) then\n'
     "         if (mem_request = '1' and mem_rnw = '1' and mem_isData = '1' and dmaOn = '0' and mem_addressData(28 downto 0) < 16#800000#) then\n"
     "            ram_hint_req <= '1';\n"
     "         else\n"
     "            ram_hint_req <= '0';\n"
     "         end if;\n"
     '         ram_hint_adr <= "00" & (std_logic_vector(mem_addressData(22 downto 21)) and (save_mode & save_mode))\n'
     '                         & std_logic_vector(mem_addressData(20 downto 2)) & "00";\n'
     '      end if;\n'
     '   end process;\n'),
)


def edit(text, edits, what):
    if TAG in text:
        raise ValueError(f'{what} already carries {TAG}')
    for name, old, new in edits:
        if text.count(old) != 1:
            raise ValueError(f'{what} {name}: expected exactly one match, found {text.count(old)}')
        text = text.replace(old, new)
    return text


def controller_text(text):
    if 'ram-miss-return' not in text:
        raise ValueError('apply prepare_ram_miss_return.py first')
    return edit(text, CTRL, 'psx_sdram_ctrl')


def core_memory_text(text):
    return edit(text, CORE_MEMORY, 'psx_core_memory')


def core_top_text(text):
    return edit(text, CORE_TOP, 'core_top')


def resource_text(text):
    return edit(text, RESOURCE, 'psx_mister_psx_top_resource')


def psx_top_text(text):
    return edit(text, PSX_TOP, 'psx_top')


FILES = (('psx_sdram_ctrl.sv', 'SYSTEMVERILOG_FILE', controller_text),
         ('psx_core_memory.sv', 'SYSTEMVERILOG_FILE', core_memory_text),
         ('core_top.sv', 'SYSTEMVERILOG_FILE', core_top_text),
         ('psx_mister_psx_top_resource.vhd', 'VHDL_FILE', resource_text),
         ('psx_top.vhd', 'VHDL_FILE', psx_top_text))


def apply(project):
    project = Path(project).resolve()
    qsf = project / 'apf_top.qsf'
    text = qsf.read_text()
    out = project / TAG
    plan = []
    for name, kind, func in FILES:
        found = re.findall(rf'{kind} "([^"]*/{re.escape(name)})"', text)
        if len(found) != 1:
            raise SystemExit(f'{TAG}: expected one {name}, found {found}')
        plan.append((found[0], func(Path(found[0]).read_text())))
    out.mkdir(exist_ok=False)
    written = []
    for old, new_text in plan:
        new = out / Path(old).name
        new.write_text(new_text)
        text = text.replace(f'"{old}"', f'"{new}"')
        written.append(new)
    qsf.write_text(text)
    return written


if __name__ == '__main__':
    ap = argparse.ArgumentParser(usage=__doc__.split('Usage: ')[1])
    ap.add_argument('project')
    for p in apply(ap.parse_args().project):
        print('patched', p)
