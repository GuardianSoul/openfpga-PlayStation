#!/usr/bin/env python3
"""Physical APF BIOS/readback probe plus both-die PSRAM burst self-test."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

from build_pocket import prepare_probe, source
from fit_psram_burst import prepare as prepare_pin_constraints
from prepare_pocket_read_pipeline import prepare as prepare_sdram_phy
from prepare_psram_burst import prepare as prepare_controller, prepare_clocks
from prepare_resource_targets import COMPILE_PROFILES, digest
from tool_paths import ROOT, find_tool


def prepare(directory):
    # Preserve the historical failed SDRAM experiment for the diagnostic sweep.
    # Ordinary BIOS probes now default to the measured MIRLO hardware baseline.
    manifest=prepare_probe(directory,sdram_phy='falling')
    controller=prepare_controller(directory/'burst-rtl')
    clocks=prepare_clocks(directory/'burst-rtl')
    # Preserve the original slow BIOS/SDRAM probe while PSRAM runs at the
    # proposed release bus rate. All six outputs come from ONE physical PLL.
    clocks_path=Path(clocks['path']);text=clocks_path.read_text()
    for before,after in {
        'clk_dram, clk_sample, locked':'clk_dram, clk_sample, clk_psram_sys, clk_psram_mem, locked',
        '.number_of_clocks(4)':'.number_of_clocks(6)',
        '.output_clock_frequency0("62.500000 MHz")':'.output_clock_frequency0("31.250000 MHz")',
        '.output_clock_frequency1("125.000000 MHz")':'.output_clock_frequency1("62.500000 MHz")',
        '.output_clock_frequency2("125.000000 MHz"), .phase_shift2("4000 ps")':
            '.output_clock_frequency2("62.500000 MHz"), .phase_shift2("8000 ps")',
        '.pll_type("General"), .pll_subtype("General")':
            '.output_clock_frequency4("62.500000 MHz"), .phase_shift4("0 ps"), .duty_cycle4(50),\n        .output_clock_frequency5("125.000000 MHz"), .phase_shift5("0 ps"), .duty_cycle5(50),\n        .pll_type("General"), .pll_subtype("General")',
        '.outclk({clk_sample, clk_dram, clk_mem, clk_sys})':
            '.outclk({clk_psram_mem, clk_psram_sys, clk_sample, clk_dram, clk_mem, clk_sys})',
    }.items():
        if text.count(before)!=1:raise ValueError('Burst-probe PLL contract changed: '+before)
        text=text.replace(before,after)
    clocks_path.write_text(text);clocks.update(sha256=digest(clocks_path),
        transform='same PLL: slow 31.25/62.5/shifted SDRAM plus 62.5/125/shifted PSRAM clocks')
    phy=prepare_sdram_phy(directory/'retimed-sdram')
    original=ROOT/'rtl/memory/psx_mem_pocket.sv'
    text=original.read_text()
    replacements={
        'output wire client_clk, client_reset, initialized, protocol_error,':
            'output wire client_clk, client_reset, initialized, protocol_error,\n    output wire burst_clk1x, burst_clk2x, burst_sample, burst_locked,',
        'psx_mem_clocks memory_clocks (':'psx_burst_mem_clocks memory_clocks (',
        '.clk_sys(client_clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .locked(locked)':
            '.clk_sys(client_clk), .clk_mem(clk_mem), .clk_dram(clk_dram), .clk_sample(burst_sample), .clk_psram_sys(burst_clk1x), .clk_psram_mem(burst_clk2x), .locked(locked)',
        'wire req_valid, req_ready, req_write, rsp_valid, rsp_ready, rsp_error;':
            'assign burst_locked=locked;\n    wire req_valid, req_ready, req_write, rsp_valid, rsp_ready, rsp_error;',
    }
    for before,after in replacements.items():
        if text.count(before)!=1: raise ValueError('Pocket memory contract changed: '+before)
        text=text.replace(before,after)
    memory=directory/'psx_mem_pocket.sv';memory.write_text('// Generated shared-clock bring-up wrapper.\n'+text)
    replacements={original:source(memory),ROOT/'rtl/memory/psx_sdr_phy.sv':phy}
    sources=[]
    for s in manifest['sources']:
        path=Path(s['path'])
        if path.name=='psx_mem_clocks.sv': sources.append(clocks)
        else: sources.append(replacements.get(path,s))
    sources += [controller,source(ROOT/'rtl/pocket/psx_psram_probe.sv')]
    core=directory/'core_top.sv';text=core.read_text()
    for name in ('passed','failed','checking'):
        text=re.sub(r'\b'+name+r'\b', 'bios_'+name, text)
        # Module port names retain their actual public names.
        text=text.replace('.bios_'+name+'(','.'+name+'(')
    marker='.client_clk(mem_clk), .client_reset(mem_reset),'
    if text.count(marker)!=1: raise ValueError('Probe memory instance changed')
    text=text.replace(marker,marker+'\n        .burst_clk1x(burst_clk1x), .burst_clk2x(burst_clk2x), .burst_sample(burst_sample), .burst_locked(burst_locked),')
    for chip in range(2):
        marker=f'''    assign cram{chip}_a=0, cram{chip}_dq=16'bz, cram{chip}_clk=0,
        cram{chip}_adv_n=1, cram{chip}_cre=0, cram{chip}_ce0_n=1, cram{chip}_ce1_n=1,
        cram{chip}_oe_n=1, cram{chip}_we_n=1, cram{chip}_ub_n=1, cram{chip}_lb_n=1;
'''
        if text.count(marker)!=1: raise ValueError('Unused PSRAM pins changed')
        text=text.replace(marker,'')
    # Keep the BIOS status registers and add separate PSRAM failure diagnostics.
    marker="32'h10000010: bridge_rd_data=read_crc ^ 32'hffffffff;"
    if text.count(marker)!=1: raise ValueError('BIOS debug contract changed')
    text=text.replace(marker,marker+'''
            32'h10000014: bridge_rd_data={29'b0,ps_flags_sync};
            32'h10000018: bridge_rd_data={14'b0,ps_checked_sync};
            32'h1000001c: bridge_rd_data={9'b0,ps_failed_addr_sync};
            32'h10000020: bridge_rd_data=ps_failed_expected_sync;
            32'h10000024: bridge_rd_data=ps_failed_actual_sync;''')
    marker='psx_test_pattern picture ('
    # Picture receives combined stable flags; BIOS producer remains independent.
    head,tail=text.split(marker,1)
    tail=tail.replace('.passed(bios_passed), .failed(bios_failed || mem_protocol_error), .checking(bios_checking)',
                      '.passed(all_passed), .failed(any_failed || mem_protocol_error), .checking(any_checking)')
    text=head+marker+tail
    body='''
    wire burst_clk1x, burst_clk2x, burst_sample, burst_locked;
    wire ps_initialized, ps_wait_error, ps_valid, ps_write, ps_die, ps_last, ps_ready, ps_rvalid, ps_idle;
    wire [21:0] ps_addr;
    wire [31:0] ps_wdata, ps_rdata;
    wire [3:0] ps_be;
    wire ps_passed, ps_failed, ps_checking;
    wire [17:0] ps_checked;
    wire [22:0] ps_failed_addr;
    wire [31:0] ps_failed_expected, ps_failed_actual;
    (* async_reg="true" *) reg [2:0] ps_reset=7;
    wire ps_reset_all=!power_reset_n || !burst_locked;
    always @(posedge burst_clk1x or posedge ps_reset_all)
        if(ps_reset_all) ps_reset<=7; else ps_reset<={ps_reset[1:0],1'b0};
    psx_psram_probe psram_probe (.clk(burst_clk1x), .reset(ps_reset[2]),
        .initialized(ps_initialized), .wait_error(ps_wait_error), .valid(ps_valid), .write(ps_write),
        .die(ps_die), .last(ps_last), .address(ps_addr), .wdata(ps_wdata), .byte_en(ps_be),
        .ready(ps_ready), .rvalid(ps_rvalid), .idle(ps_idle), .rdata(ps_rdata),
        .passed(ps_passed), .failed(ps_failed), .checking(ps_checking), .checked_words(ps_checked),
        .failed_address(ps_failed_addr), .failed_expected(ps_failed_expected), .failed_actual(ps_failed_actual));
    // Result data is stable after the flag; three flag stages follow two data
    // stages, allowing debug reads of the final record after the result color.
    (* async_reg="true" *) reg [2:0] ps_flags_meta=0, ps_flags_stage=0, ps_flags_sync=0;
    reg [17:0] ps_checked_meta=0, ps_checked_sync=0;
    reg [22:0] ps_failed_addr_meta=0, ps_failed_addr_sync=0;
    reg [31:0] ps_failed_expected_meta=0, ps_failed_expected_sync=0;
    reg [31:0] ps_failed_actual_meta=0, ps_failed_actual_sync=0;
    always @(posedge clk_74a) begin
        ps_flags_meta<={ps_failed,ps_passed,ps_checking};
        ps_flags_stage<=ps_flags_meta; ps_flags_sync<=ps_flags_stage;
        ps_checked_meta<=ps_checked; ps_checked_sync<=ps_checked_meta;
        ps_failed_addr_meta<=ps_failed_addr; ps_failed_addr_sync<=ps_failed_addr_meta;
        ps_failed_expected_meta<=ps_failed_expected; ps_failed_expected_sync<=ps_failed_expected_meta;
        ps_failed_actual_meta<=ps_failed_actual; ps_failed_actual_sync<=ps_failed_actual_meta;
    end
    wire all_passed=bios_passed && ps_flags_sync[1];
    wire any_failed=bios_failed || ps_flags_sync[2];
    wire any_checking=bios_checking || ps_flags_sync[0];
    wire [5:0] ps_o_a;
    wire [31:0] ps_o_dq, ps_i_dq;
    wire ps_o_dq_oe, ps_o_adv_n, ps_o_oe_n, ps_o_we_n, ps_o_cre, ps_clk_en;
    wire [1:0] ps_o_ce_n, ps_i_wait;
    wire [3:0] ps_o_bm_n;
    psx_psram_burst psram_ctrl (.clk(burst_clk1x), .rst(ps_reset[2]),
        .z_valid(ps_valid), .z_die(ps_die), .z_ready(ps_ready), .z_we(ps_write),
        .z_addr(ps_addr), .z_wdata(ps_wdata), .z_wmask(ps_be), .z_last(ps_last),
        .z_rvalid(ps_rvalid), .z_rdata(ps_rdata), .z_idle(ps_idle),
        .x_valid(1'b0), .x_we(1'b0), .x_addr(22'b0), .x_wdata(32'b0), .x_be(4'b0),
        .x_ack(), .x_rdata(), .cfg(4'h5), .cfg_wr(1'b0), .ready(ps_initialized), .wait_err(ps_wait_error),
        .o_a(ps_o_a), .o_dq(ps_o_dq), .o_dq_oe(ps_o_dq_oe), .o_adv_n(ps_o_adv_n),
        .o_oe_n(ps_o_oe_n), .o_we_n(ps_o_we_n), .o_cre(ps_o_cre), .o_ce_n(ps_o_ce_n),
        .o_bm_n(ps_o_bm_n), .o_clk_en(ps_clk_en), .i_dq(ps_i_dq), .i_wait(ps_i_wait));
    psx_psram_burst_phy psram_phy (.clk(burst_clk1x), .clk2x(burst_clk2x), .clk_sample(burst_sample),
        .o_a(ps_o_a), .o_dq(ps_o_dq), .o_dq_oe(ps_o_dq_oe), .o_adv_n(ps_o_adv_n),
        .o_oe_n(ps_o_oe_n), .o_we_n(ps_o_we_n), .o_cre(ps_o_cre), .o_ce_n(ps_o_ce_n),
        .o_bm_n(ps_o_bm_n), .clk_en(ps_clk_en), .i_dq(ps_i_dq), .i_wait(ps_i_wait),
'''
    pads=[]
    for chip in range(2):
        pads += [f'cram{chip}_{n}' for n in ('a','dq','wait','clk','adv_n','cre','ce0_n','ce1_n','oe_n','we_n','ub_n','lb_n')]
    body+=',\n'.join(f'        .{n}({n})' for n in pads)+');\n'
    core.write_text(text.replace('endmodule\n',body+'endmodule\n',1))
    # Recompute the core digest after adding the real controller/test path.
    sources=[source(core) if Path(s['path'])==core else s for s in sources]
    qsf=directory/'apf_top.qsf'
    lines=[line for line in qsf.read_text().splitlines()
           if not re.match(r'set_global_assignment -name (SYSTEMVERILOG_FILE|VERILOG_FILE|VHDL_FILE) ',line)]
    lines += [f'set_global_assignment -name {s["assignment"]} "{s["path"]}" -library {s["library"]}' for s in sources]
    for line in (ROOT/'references/MIRLO64/projects/mirlo.qsf').read_text().splitlines():
        if line.startswith('set_instance_assignment -name FAST_') and ' -to cram' in line: lines.append(line)
    lines += [f'set_global_assignment -name {k} "{v}"' for k,v in COMPILE_PROFILES['area-min-state'].items()]
    qsf.write_text('\n'.join(lines)+'\n')
    constraints=prepare_pin_constraints(directory/'component-constraint-source')
    constraint_file=Path(constraints['targets']['psram_burst']['directory'])/'psx_psram_burst_resource.sdc'
    extra=constraint_file.read_text().split('set ps_sys2x',1)[1]
    extra='set ps_sys2x'+extra.replace('memory_clocks|pll|','ic|memory|memory_clocks|pll|')
    extra=extra.replace('general[1].gpll~PLL_OUTPUT_COUNTER|divclk','general[5].gpll~PLL_OUTPUT_COUNTER|divclk')
    sdc=directory/'pocket.sdc'
    sdc.write_text(sdc.read_text()+extra+'''
set_false_path -to [get_registers {*|ps_flags_meta[*]}]
# Final result words are held before synchronized flags, never live control.
set_max_delay 10 -to [get_registers {*|ps_checked_meta[*] *|ps_failed_addr_meta[*] *|ps_failed_expected_meta[*] *|ps_failed_actual_meta[*]}]
set_min_delay 0 -to [get_registers {*|ps_checked_meta[*] *|ps_failed_addr_meta[*] *|ps_failed_expected_meta[*] *|ps_failed_actual_meta[*]}]
''')
    manifest.update(kind='bios-psram-burst-probe',sources=sources,
        included=manifest['included']+['both-die fixed-latency PSRAM controller/PHY','autonomous masked/address/readback test'],
        memory_clock_mhz=62.5,client_clock_mhz=31.25,psram_clock_mhz=62.5,
        psram_sample_shift_ns=1.4,compile_profile='area-min-state',memory_pll_shared=True,
        shared_memory_wrapper=dict(original=str(original),original_sha256=digest(original),sha256=digest(memory)),
        controller=controller,memory_clocks=clocks,sdram_retiming=phy,
        project_hashes={n:digest(directory/n) for n in ('apf_top.qsf','apf_top.qpf','pocket.sdc','apf/build_id.mif')})
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build/pocket/burst-probe')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    runs=args.output.resolve()/'runs';runs.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=runs))
    manifest=prepare(directory);print(directory,flush=True)
    if args.prepare_only:return
    located=find_tool('quartus_sh')
    if not located:raise SystemExit('Quartus required; prepared project preserved')
    binary=Path(located).parent
    with (directory/'quartus.log').open('w') as log:
        for tool in ('quartus_map','quartus_fit','quartus_sta','quartus_asm'):
            result=subprocess.run([str(binary/tool),'apf_top'],cwd=directory,stdout=log,stderr=subprocess.STDOUT,timeout=1800)
            print(tool,result.returncode,flush=True)
            if result.returncode:raise SystemExit(result.returncode)
    for s in manifest['sources']:
        if digest(Path(s['path']))!=s['sha256']:raise ValueError('Compiled source changed: '+s['path'])
    for name,sha in manifest['project_hashes'].items():
        if digest(directory/name)!=sha:raise ValueError('Compiled project changed: '+name)
    print('Bitstream:',directory/'output_files/apf_top.rbf',flush=True)


if __name__=='__main__':main()
