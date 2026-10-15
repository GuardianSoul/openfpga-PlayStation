#!/usr/bin/env python3
"""Fit the native VRAM stream, SDRAM controller/PHY and actual Pocket pins.

Native buses use virtual pins. This component excludes the core/APF, PSRAM,
video/audio PLLs and persistent services; it is not a game bitstream.
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
from prepare_pocket_read_pipeline import prepare as retime_phy
from prepare_resource_targets import apply_compile_profile, digest, write_project
from record_pocket_measurements import verify_sources
from run_resource_fit import fit_target
from tool_paths import ROOT, find_tool


WRAPPER='''module psx_vram_stream_resource (
    input wire clk_74a, clk_74b, reset_async,
    input wire rd, we,
    input wire [27:0] addr,
    input wire [7:0] count, be,
    input wire [63:0] din,
    output wire busy, fault, ready,
    output wire [63:0] dout,
    output wire initialized,
    output wire [12:0] dram_a,
    output wire [1:0] dram_ba, dram_dqm,
    inout wire [15:0] dram_dq,
    output wire dram_ras_n, dram_cas_n, dram_we_n, dram_cke, dram_clk
);
    wire [2:0] cpu_clocks;
    wire cpu_locked, mem_locked, mem_clk, mem_clk2, forwarded_clock;
    altera_pll #(.fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(3), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("33.868800 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("67.737600 MHz"), .phase_shift1("0 ps"), .duty_cycle1(50),
        .output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50)) cpu_pll (
        .refclk(clk_74a), .rst(reset_async), .fbclk(1'b0), .fboutclk(),
        .locked(cpu_locked), .outclk(cpu_clocks));
    psx_mem_clocks memory_clocks (.clk_74a(clk_74b), .reset_async(reset_async),
        .clk_sys(mem_clk), .clk_mem(mem_clk2), .clk_dram(forwarded_clock), .locked(mem_locked));
    wire reset_all=reset_async || !cpu_locked || !mem_locked;
    (* async_reg="true" *) reg [2:0] reset_mem=7, reset_mem2=7;
    always @(posedge mem_clk or posedge reset_all)
        if(reset_all) reset_mem<=7; else reset_mem<={reset_mem[1:0],1'b0};
    always @(posedge mem_clk2 or posedge reset_all)
        if(reset_all) reset_mem2<=7; else reset_mem2<={reset_mem2[1:0],1'b0};
    wire valid, write, request_ready, rvalid;
    wire [23:0] address;
    wire [31:0] wdata, rdata;
    wire [3:0] byte_en;
    psx_vram_stream stream (.src_clk(cpu_clocks[1]), .mem_clk(mem_clk), .reset_async(reset_all),
        .mem_initialized(initialized), .rd(rd), .we(we), .addr(addr), .count(count), .be(be), .din(din),
        .busy(busy), .fault(fault), .ready(ready), .dout(dout), .mem_valid(valid), .mem_write(write),
        .mem_ready(request_ready), .mem_addr(address), .mem_wdata(wdata), .mem_byte_en(byte_en),
        .mem_rvalid(rvalid), .mem_rdata(rdata));
    wire [1:0] ras, cas, wen;
    wire [25:0] dfi_address;
    wire [3:0] bank, mask;
    wire cke, wren, rden, phy_valid;
    wire [31:0] data, phy_data;
    sdr_ctrl #(.RL(5), .AHEAD(0)) controller (.clk(mem_clk), .rst(reset_mem[2]), .ready(initialized),
        .req_valid(valid), .req_ready(request_ready), .req_we(write), .req_addr(address),
        .req_wdata(wdata), .req_be(byte_en), .req_tag(3'b0), .la_valid(1'b0), .la_addr(24'b0),
        .rd_valid(rvalid), .rd_data(rdata), .rd_tag(), .rd_next(), .rd_next_tag(),
        .dfi_ras_n(ras), .dfi_cas_n(cas), .dfi_we_n(wen), .dfi_addr(dfi_address), .dfi_ba(bank),
        .dfi_cke(cke), .dfi_wrdata_en(wren), .dfi_wrdata(data), .dfi_wrdata_mask(mask),
        .dfi_rddata_en(rden), .dfi_rddata(phy_data), .dfi_rddata_valid(phy_valid));
    psx_sdr_phy phy (.clk(mem_clk), .rst(reset_mem[2]), .clk2x(mem_clk2), .rst2x(reset_mem2[2]),
        .clk2x_90(forwarded_clock), .ras_n(ras), .cas_n(cas), .we_n(wen), .addr(dfi_address),
        .ba(bank), .cke(cke), .wrdata_en(wren), .wrdata(data), .wrdata_mask(mask),
        .rddata_en(rden), .rddata(phy_data), .rddata_valid(phy_valid), .sdram_a(dram_a),
        .sdram_ba(dram_ba), .sdram_dm(dram_dqm), .sdram_dq(dram_dq), .sdram_ras_n(dram_ras_n),
        .sdram_cas_n(dram_cas_n), .sdram_we_n(dram_we_n), .sdram_cke(dram_cke), .sdram_clock(dram_clk));
endmodule
'''


def prepare(directory):
    top='psx_vram_stream_resource'
    wrapper=directory/(top+'.sv'); wrapper.write_text(WRAPPER)
    phy=retime_phy(directory/'retimed-phy')
    sources=[source(wrapper), source(ROOT/'rtl/pocket/psx_vram_stream.sv'),
        source(ROOT/'references/Laxer3a_PSX/hdlGPU/rtl/dual_clock_fifo.sv'),
        source(ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv'),
        source(ROOT/'rtl/memory/psx_mem_clocks.sv'), phy]
    specs=[('clk_74a','in',1), ('clk_74b','in',1), ('reset_async','in',1),
        ('rd','in',1), ('we','in',1), ('addr','in',28), ('count','in',8), ('be','in',8), ('din','in',64),
        ('busy','out',1), ('fault','out',1), ('ready','out',1), ('dout','out',64), ('initialized','out',1),
        ('dram_a','out',13), ('dram_ba','out',2), ('dram_dqm','out',2), ('dram_dq','inout',16),
        *[(n,'out',1) for n in ('dram_ras_n','dram_cas_n','dram_we_n','dram_cke','dram_clk')]]
    external=[(n,d,'std_logic' if w==1 else f'std_logic_vector({w-1} downto 0)') for n,d,w in specs]
    # Area measurement constraints. Do not suppress whole asynchronous domains:
    # only the actual first sampling stages of Gray/level crossings are cut.
    # Gray bus skew and board I/O closure remain explicit unfinished work.
    sdc=['create_clock -name ref_a -period 13.468 [get_ports clk_74a]',
        'create_clock -name ref_b -period 13.468 [get_ports clk_74b]',
        'derive_pll_clocks', 'derive_clock_uncertainty',
        'set_false_path -to [get_registers {*|wr_addr_gray_rd[*] *|rd_addr_gray_wr[*] *|consumed_meta[*] *|retired_command_meta[*] *|finished_word_meta[*] *|initialized_sync[0] *|backend_fault_sync[0] *|source_online_sync[0]}]',
        '# No full-board timing claim: Gray bus skew/flight bounds, reset and physical I/O delays need closure.']
    write_project(directory, top, external, ('clk_74a','clk_74b'), sources, sdc)
    qsf=directory/(top+'.qsf')
    lines=[line for line in qsf.read_text().splitlines() if not ('VIRTUAL_PIN' in line and '-to "dram_' in line)]
    for line in (PLATFORM/'pocket.tcl').read_text().splitlines():
        if line.startswith('set_') and re.search(r'-to (?:"?)(?:dram_|clk_74)',line): lines.append(line)
    qsf.write_text('\n'.join(lines)+'\n')
    target=dict(top=top, directory=str(directory), quartus_sources=sources, fixed_ports={},
        physical_clock_ports=['clk_74a','clk_74b'], components_connected=True,
        project_sha256={suffix:digest(directory/(top+suffix)) for suffix in ('.qsf','.qpf','.sdc')},
        included_components=['native VRAM stream', 'two Laxer async FIFOs', 'MIRLO sdr_ctrl',
            'read-retimed psx_sdr_phy', 'CPU PLL', '62.5/125/forwarded SDRAM memory PLL'],
        limitations=['Native bus/reset/status virtual pins; no APF/PSX core or PSRAM fabric',
            'Two PLLs here; release must share the memory PLL with PSRAM for four total PLLs',
            'Gray pointer skew/flight, reset and board I/O timing incomplete',
            'Synthetic pin-model supply is not simultaneous game bandwidth or hardware evidence'])
    manifest=apply_compile_profile(dict(device='5CEBA4F23C8', targets={'vram_stream':target}), 'area-min-state')
    manifest.update(read_pipeline_retiming=phy, native_mhz=67.7376, controller_mhz=62.5, sdram_mhz=125,
                    fifo_aw=4, queued_write_words=1024, hardware_verified=False)
    verify_sources(directory, sources)
    (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'build/resources/vram-stream')
    parser.add_argument('--prepare-only', action='store_true')
    args=parser.parse_args()
    parent=args.output.resolve()/'runs'; parent.mkdir(parents=True, exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=parent))
    manifest=prepare(directory); print(directory, flush=True)
    if args.prepare_only: return
    tool=find_tool('quartus_sh')
    if not tool: raise SystemExit('Quartus required; prepared project preserved')
    binary=Path(tool).parent
    result=fit_target(binary, manifest['targets']['vram_stream'], manifest['device'], 1800)
    report=dict(tool_version=subprocess.check_output([str(binary/'quartus_sh'),'--version'], text=True),
                **manifest, result=result)
    (directory/'quartus-report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:result.get(k) for k in ('fit_verified','counts','tool_error_messages','reported_timing')}), flush=True)
    if not result['fit_verified']: raise SystemExit(1)


if __name__=='__main__': main()
