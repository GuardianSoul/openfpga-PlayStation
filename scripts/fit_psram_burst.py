#!/usr/bin/env python3
"""Fit both-die burst PSRAM controller/PHY on real Pocket pins.

The held native stream uses virtual pins; this is a component measurement,
not a game bitstream. The memory PLL is shared with SDRAM in topology C.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from build_pocket import source
from prepare_platform_target import PLATFORM
from prepare_psram_burst import prepare as prepare_controller, prepare_clocks
from prepare_resource_targets import apply_compile_profile, digest, write_project
from record_pocket_measurements import verify_sources
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=True)
    top = 'psx_psram_burst_resource'
    specs = [('clk_74a', 'in', 1), ('reset_async', 'in', 1),
        ('valid', 'in', 1), ('write', 'in', 1), ('die', 'in', 1),
        ('addr', 'in', 22), ('wdata', 'in', 32), ('be', 'in', 4), ('last', 'in', 1),
        ('ready', 'out', 1), ('rvalid', 'out', 1), ('rdata', 'out', 32),
        ('idle', 'out', 1), ('initialized', 'out', 1), ('wait_error', 'out', 1)]
    pads = []
    for chip in range(2):
        pads += [(f'cram{chip}_a', 'out', 6), (f'cram{chip}_dq', 'inout', 16),
                 (f'cram{chip}_wait', 'in', 1)]
        pads += [(f'cram{chip}_{n}', 'out', 1) for n in
                 ('clk', 'adv_n', 'cre', 'ce0_n', 'ce1_n', 'oe_n', 'we_n', 'ub_n', 'lb_n')]
    specs += pads
    declarations = []
    for name, direction, width in specs:
        bounds = '[21:16] ' if name.endswith('_a') else f'[{width-1}:0] ' if width > 1 else ''
        declarations.append(f'    {dict(in_="input", out="output", inout="inout")["in_" if direction == "in" else direction]} wire {bounds}{name}')
    body = '''
    wire clk, clk2x, clk_sample, unused_dram_clock, locked;
    psx_burst_mem_clocks memory_clocks (.clk_74a(clk_74a), .reset_async(reset_async),
        .clk_sys(clk), .clk_mem(clk2x), .clk_dram(unused_dram_clock), .clk_sample(clk_sample), .locked(locked));
    wire reset_all=reset_async || !locked;
    (* async_reg="true" *) reg [2:0] reset_local=7;
    always @(posedge clk or posedge reset_all)
        if (reset_all) reset_local<=7; else reset_local<={reset_local[1:0],1'b0};
    wire [5:0] o_a;
    wire [31:0] o_dq, i_dq;
    wire o_dq_oe, o_adv_n, o_oe_n, o_we_n, o_cre, clk_en;
    wire [1:0] o_ce_n, i_wait;
    wire [3:0] o_bm_n;
    psx_psram_burst controller (.clk(clk), .rst(reset_local[2]),
        .z_valid(valid), .z_die(die), .z_ready(ready), .z_we(write),
        .z_addr(addr), .z_wdata(wdata), .z_wmask(be), .z_last(last),
        .z_rvalid(rvalid), .z_rdata(rdata), .z_idle(idle),
        .x_valid(1'b0), .x_we(1'b0), .x_addr(22'b0), .x_wdata(32'b0),
        .x_be(4'b0), .x_ack(), .x_rdata(), .cfg(4'h5), .cfg_wr(1'b0),
        .ready(initialized), .wait_err(wait_error), .o_a(o_a), .o_dq(o_dq),
        .o_dq_oe(o_dq_oe), .o_adv_n(o_adv_n), .o_oe_n(o_oe_n), .o_we_n(o_we_n),
        .o_cre(o_cre), .o_ce_n(o_ce_n), .o_bm_n(o_bm_n), .o_clk_en(clk_en),
        .i_dq(i_dq), .i_wait(i_wait));
    psx_psram_burst_phy phy (.clk(clk), .clk2x(clk2x), .clk_sample(clk_sample), .o_a(o_a),
        .o_dq(o_dq), .o_dq_oe(o_dq_oe), .o_adv_n(o_adv_n), .o_oe_n(o_oe_n),
        .o_we_n(o_we_n), .o_cre(o_cre), .o_ce_n(o_ce_n), .o_bm_n(o_bm_n),
        .clk_en(clk_en), .i_dq(i_dq), .i_wait(i_wait),
'''
    body += ',\n'.join(f'        .{n}({n})' for n, _, _ in pads)+');\n'
    wrapper = directory/(top+'.sv')
    wrapper.write_text('module '+top+'(\n'+',\n'.join(declarations)+'\n);\n'+body+'endmodule\n')
    controller = prepare_controller(directory/'burst-rtl')
    clocks = prepare_clocks(directory/'burst-rtl')
    sources = [source(wrapper), controller, clocks]
    external = [(n, d, 'std_logic' if w == 1 else
                 'std_logic_vector(21 downto 16)' if n.endswith('_a') else
                 f'std_logic_vector({w-1} downto 0)') for n, d, w in specs]
    sdc = ['create_clock -name ref_a -period 13.468 [get_ports clk_74a]',
        'derive_pll_clocks', 'derive_clock_uncertainty',
        'set ps_sys2x {memory_clocks|pll|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}',
        'if {[get_collection_size [get_pins $ps_sys2x]] != 1} {error "Missing PSRAM doubled clock"}',
        '# Fixed-code-4 burst clock: rising 12 ns, falling 20 ns, next rise 28 ns.',
        '# Bounds include a provisional 0.5 ns board allowance; not measured board delays.',
        'foreach c {cram0 cram1} {',
        '    create_generated_clock -name ${c}_clk -source [get_pins $ps_sys2x] -edges {4 6 8} [get_ports ${c}_clk]',
        '    set outs [get_ports "${c}_a[*] ${c}_dq[*] ${c}_adv_n ${c}_cre ${c}_ce0_n ${c}_ce1_n ${c}_oe_n ${c}_we_n ${c}_ub_n ${c}_lb_n"]',
        '    set_output_delay -clock ${c}_clk -max 3.5 $outs',
        '    set_output_delay -clock ${c}_clk -min -2.5 $outs',
        '    set ins [get_ports "${c}_dq[*] ${c}_wait"]',
        '    set_input_delay -clock ${c}_clk -max 9.5 $ins',
        '    set_input_delay -clock ${c}_clk -min 2.0 $ins',
        '    # Pin samples at 13.4/21.4/29.4 ns. Only 29.4 reaches the enabled fabric stage.',
        '    # Old data launched at 12 ns; next data can change after 28 ns + tKOH.',
        '    # Setup End 3 gives 12 -> 29.4; Hold End 1 gives 28 -> 29.4.',
        '    set_multicycle_path -setup -end 3 -from $ins -to [get_registers {*|pin_dq[*] *|pin_wait[*]}]',
        '    set_multicycle_path -hold -end 1 -from $ins -to [get_registers {*|pin_dq[*] *|pin_wait[*]}]',
        '}',
        '# Virtual stream, async reset/PLL unlock, CE enable/disable and board delays require separate closure.']
    write_project(directory, top, external, ('clk_74a',), sources, sdc)
    qsf = directory/(top+'.qsf')
    lines = [line for line in qsf.read_text().splitlines()
             if not ('VIRTUAL_PIN' in line and '-to "cram' in line)]
    for line in (PLATFORM/'pocket.tcl').read_text().splitlines():
        if line.startswith('set_') and re.search(r'-to (?:"?)(?:cram|clk_74a)', line):
            lines.append(line)
    for line in (ROOT/'references/MIRLO64/projects/mirlo.qsf').read_text().splitlines():
        if line.startswith('set_instance_assignment -name FAST_') and ' -to cram' in line:
            lines.append(line)
    qsf.write_text('\n'.join(lines)+'\n')
    target = dict(top=top, directory=str(directory), quartus_sources=sources, fixed_ports={},
        physical_clock_ports=['clk_74a'], components_connected=True,
        project_sha256={s: digest(directory/(top+s)) for s in ('.qsf', '.qpf', '.sdc')},
        included_components=['MIRLO both-die burst controller', 'read-retimed PSRAM PHY',
                             '62.5/125 MHz memory PLL'],
        limitations=['Native stream/reset/status virtual pins; no core/APF/SDRAM/native adapters',
            'Memory PLL must be shared with SDRAM in the release design',
            'Board delays provisional; asynchronous reset/PLL unlock and burst gating need audit',
            'Unconstrained virtual interface and no simultaneous guest-traffic proof'])
    manifest = apply_compile_profile(dict(device='5CEBA4F23C8', targets={'psram_burst': target}), 'area-min-state')
    manifest.update(controller=controller, controller_mhz=62.5, memory_pll_shared=True,
                    board_timing_verified=False, hardware_verified=False)
    verify_sources(directory, sources)
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/resources/psram-burst')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    parent = args.output.resolve()/'runs'; parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    manifest = prepare(directory); print(directory, flush=True)
    if args.prepare_only: return
    tool = find_tool('quartus_sh')
    if not tool: raise SystemExit('Quartus required; prepared project preserved')
    binary = Path(tool).parent
    result = fit_target(binary, manifest['targets']['psram_burst'], manifest['device'], 1800)
    report = dict(tool_version=subprocess.check_output([str(binary/'quartus_sh'), '--version'], text=True),
                  **manifest, result=result)
    (directory/'quartus-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k: result.get(k) for k in ('fit_verified', 'counts', 'tool_error_messages', 'reported_timing')}), flush=True)
    if not result['fit_verified']: raise SystemExit(1)


if __name__ == '__main__': main()
