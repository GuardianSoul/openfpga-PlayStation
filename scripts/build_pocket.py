#!/usr/bin/env python3
"""Build physical APF Pocket projects. Never uses virtual pins or private ROMs."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from prepare_platform_target import CORE, PLATFORM, declarations, SAVESTATE_FIXED
from prepare_resource_targets import digest
from tool_paths import ROOT, find_tool


def source(path):
    return {'path':str(path.resolve()), 'sha256':digest(path), 'library':'work',
            'assignment':{'.v':'VERILOG_FILE','.sv':'SYSTEMVERILOG_FILE','.vhd':'VHDL_FILE'}[path.suffix]}


def compiler_version_append_only(original, compiled):
    """Quartus adds a version label; reject every other QSF edit."""
    return re.fullmatch(re.escape(original)+
        r'\nset_global_assignment -name LAST_QUARTUS_VERSION "[^"\n]+"\n?',compiled) is not None


def shell(directory):
    # Retain the pinned physical/logical port declaration verbatim.
    header=(CORE/'core_top.sv').read_text().split('\n);',1)[0]+'\n);\n'
    # No JTAG UART is instantiated. Omit its four passthroughs, which would
    # otherwise become unassigned user I/O rather than dedicated JTAG pins.
    header=re.sub(r'^.*altera_reserved_.*\n','',header,flags=re.M)
    header=re.sub(r',\s*\);', '\n);',header)
    body='''
    assign port_ir_tx=0, port_ir_rx_disable=1;
    assign cart_tran_bank1=8'bz, cart_tran_bank2=8'bz, cart_tran_bank3=8'bz;
    assign cart_tran_bank1_dir=0, cart_tran_bank2_dir=0, cart_tran_bank3_dir=0;
    assign cart_tran_bank0=4'hf, cart_tran_bank0_dir=1;
    assign cart_tran_pin30=1'bz, cart_tran_pin30_dir=0, cart_pin30_pwroff_reset=0;
    assign cart_tran_pin31=1'bz, cart_tran_pin31_dir=0;
    assign port_tran_si=1'bz, port_tran_so=1'bz, port_tran_sck=1'bz, port_tran_sd=1'bz;
    assign port_tran_si_dir=0, port_tran_so_dir=0, port_tran_sck_dir=0, port_tran_sd_dir=0;
    assign sram_a=0, sram_dq=16'bz, sram_oe_n=1, sram_we_n=1, sram_ub_n=1, sram_lb_n=1;
    assign dbg_tx=1'bz, user1=1'bz, aux_sda=1'bz, aux_scl=1'bz, vpll_feed=1'bz;
    assign bridge_endian_little=0;
    reg [7:0] power_count=0;
    reg power_reset_n=0;
    always @(posedge clk_74a) begin
        if (!power_reset_n) power_count<=power_count+1'b1;
        if (&power_count) power_reset_n<=1;
    end
    wire reset_n, mem_initialized;
    wire [31:0] cmd_bridge_rd_data;
    wire dataslot_allcomplete;
    (* async_reg="true" *) reg [1:0] mem_ready74=0;
    always @(posedge clk_74a) mem_ready74<={mem_ready74[0],mem_initialized};
'''
    fixed={**SAVESTATE_FIXED, 'clk':'clk_74a','reset_n':'reset_n',
        'bridge_addr':'bridge_addr','bridge_rd':'bridge_rd','bridge_wr':'bridge_wr',
        'bridge_wr_data':'bridge_wr_data','bridge_rd_data':'cmd_bridge_rd_data',
        'bridge_endian_little':'bridge_endian_little',
        'status_boot_done':'power_reset_n','status_setup_done':'power_reset_n','status_running':'reset_n',
        'dataslot_requestread_ack':"1'b1",'dataslot_requestread_ok':"1'b1",
        'dataslot_requestwrite_ack':'mem_ready74[1]','dataslot_requestwrite_ok':"1'b1",
        'dataslot_allcomplete':'dataslot_allcomplete'}
    associations=[]
    for name,direction,width in declarations(CORE/'core_bridge_cmd.v'):
        if name in fixed: value=fixed[name]
        elif direction=='input': value="'0"
        else: value=''
        associations.append(f'        .{name}({value})')
    body+='    core_bridge_cmd commands (\n'+',\n'.join(associations)+'\n    );\n'
    body+='''
    wire passed, failed, checking;
    wire [17:0] loaded_words, checked_words;
    wire [31:0] write_crc, read_crc;
    always @* begin
        bridge_rd_data=0;
        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;
        else case (bridge_addr)
            32'h10000000: bridge_rd_data={28'b0,checking,failed,passed,mem_ready74[1]};
            32'h10000004: bridge_rd_data={14'b0,loaded_words};
            32'h10000008: bridge_rd_data={14'b0,checked_words};
            32'h1000000c: bridge_rd_data=write_crc ^ 32'hffffffff;
            32'h10000010: bridge_rd_data=read_crc ^ 32'hffffffff;
        endcase
    end
    wire load_valid, load_ready, load_write, load_rsp, load_rsp_ready, load_error;
    wire [24:0] load_addr;
    wire [127:0] load_data, load_read;
    wire [15:0] load_be;
    psx_bios_probe bios_probe (
        .clk(clk_74a), .reset(!power_reset_n), .bridge_wr(bridge_wr),
        .bridge_addr(bridge_addr), .bridge_data(bridge_wr_data), .allcomplete(dataslot_allcomplete),
        .req_valid(load_valid), .req_ready(load_ready), .req_write(load_write), .req_addr(load_addr),
        .req_data(load_data), .req_be(load_be), .rsp_valid(load_rsp), .rsp_ready(load_rsp_ready),
        .rsp_data(load_read), .rsp_error(load_error), .loaded_words(loaded_words),
        .checked_words(checked_words), .write_crc(write_crc), .read_crc(read_crc),
        .passed(passed), .failed(failed), .checking(checking));
    wire mem_clk, mem_reset;
    wire c_valid, c_ready, c_write, c_rsp, c_rsp_ready, c_error;
    wire [24:0] c_addr;
    wire [3:0] c_count;
    wire [127:0] c_data, c_read;
    wire [15:0] c_be;
    psx_mem_cdc loader_cdc (
        .src_clk(clk_74a), .dst_clk(mem_clk), .reset_async(!power_reset_n || mem_reset),
        .s_valid(load_valid), .s_ready(load_ready), .s_write(load_write), .s_addr(load_addr),
        .s_halfwords(4'd2), .s_wdata(load_data), .s_byte_en(load_be),
        .s_rsp_valid(load_rsp), .s_rsp_ready(load_rsp_ready), .s_rsp_rdata(load_read), .s_rsp_error(load_error),
        .d_valid(c_valid), .d_ready(c_ready), .d_write(c_write), .d_addr(c_addr), .d_halfwords(c_count),
        .d_wdata(c_data), .d_byte_en(c_be), .d_rsp_valid(c_rsp), .d_rsp_ready(c_rsp_ready),
        .d_rsp_rdata(c_read), .d_rsp_error(c_error));
    wire mem_protocol_error;
    psx_mem_pocket #(.PORTS(1),.T_REFI(240)) memory (
        .clk_74a(clk_74a), .reset_async(!power_reset_n), .client_clk(mem_clk), .client_reset(mem_reset),
        .initialized(mem_initialized), .protocol_error(mem_protocol_error),
        .p_valid(c_valid), .p_ready(c_ready), .p_write(c_write), .p_addr(c_addr), .p_halfwords(c_count),
        .p_wdata(c_data), .p_byte_en(c_be), .p_rsp_valid(c_rsp), .p_rsp_ready(c_rsp_ready),
        .p_rsp_rdata(c_read), .p_rsp_error(c_error),
        .dram_a(dram_a), .dram_ba(dram_ba), .dram_dqm(dram_dqm), .dram_dq(dram_dq),
        .dram_ras_n(dram_ras_n), .dram_cas_n(dram_cas_n), .dram_we_n(dram_we_n),
        .dram_cke(dram_cke), .dram_clk(dram_clk));
    wire video_locked;
    altera_pll #(.fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(2), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("25.200000 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("25.200000 MHz"), .phase_shift1("9921 ps"), .duty_cycle1(50)) video_pll (
        .refclk(clk_74a), .rst(!power_reset_n), .fbclk(1'b0), .fboutclk(), .locked(video_locked),
        .outclk({video_rgb_clock_90,video_rgb_clock}));
    assign video_skip=0;
    psx_test_pattern picture (.clk(video_rgb_clock), .reset(!video_locked),
        .passed(passed), .failed(failed || mem_protocol_error), .checking(checking),
        .progress(checking ? checked_words : loaded_words),
        .rgb(video_rgb), .de(video_de), .hs(video_hs), .vs(video_vs));
    wire audio_sclk;
    mf_audio_pll audio_pll (.refclk(clk_74b),.rst(!power_reset_n),
        .outclk_0(audio_mclk),.outclk_1(audio_sclk),.locked());
    sound_i2s #(.SIGNED_INPUT(1)) sound (.audio_sclk(audio_sclk),
        .audio_l(16'b0), .audio_r(16'b0), .audio_lrck(audio_lrck), .audio_dac(audio_dac));
'''
    for i in range(2):
        body+=f'''    assign cram{i}_a=0, cram{i}_dq=16'bz, cram{i}_clk=0,
        cram{i}_adv_n=1, cram{i}_cre=0, cram{i}_ce0_n=1, cram{i}_ce1_n=1,
        cram{i}_oe_n=1, cram{i}_we_n=1, cram{i}_ub_n=1, cram{i}_lb_n=1;
'''
    path=directory/'core_top.sv'; path.write_text(header+body+'endmodule\n`default_nettype wire\n')
    return path


def prepare_probe(directory, sdram_phy='mirlo'):
    if sdram_phy not in ('mirlo','falling'):
        raise ValueError('Select the hardware MIRLO baseline or the explicit falling-edge experiment')
    directory.mkdir(parents=True,exist_ok=True)
    generated=shell(directory)
    apf=directory/'apf_top.v'
    text=(PLATFORM/'apf_top.v').read_text()
    text=re.sub(r'^.*altera_reserved_.*\n','',text,flags=re.M)
    text=re.sub(r',\s*\);', '\n);',text)
    apf.write_text(text)
    paths=[apf]+[PLATFORM/n for n in ('common.v','io_bridge_peripheral.v',
        'io_pad_controller.v','mf_ddio_bidir_12.v','mf_datatable.v')]
    paths += [CORE/n for n in ('core_bridge_cmd.v','sound_i2s.sv','mf_audio_pll.v','mf_audio_pll/mf_audio_pll_0002.v')]
    paths += [ROOT/'rtl/memory'/n for n in ('psx_mem_clocks.sv','psx_mem_pocket.sv','psx_mem_arb.sv',
        'psx_mem_bridge.sv','psx_sdram.sv','psx_sdram_pins.sv','psx_sdr_phy.sv','psx_mem_cdc.sv')]
    paths += [ROOT/'references/MIRLO64/rtl/soc/sdr_ctrl.sv',ROOT/'rtl/pocket/psx_bios_probe.sv',
              ROOT/'rtl/pocket/psx_test_pattern.sv',generated]
    sources=[source(p) for p in paths]
    # Select the exact PHY/clock combination that passed the Pocket CRC sweep.
    # Keep the failed slow/falling-edge experiment explicit for reproduction.
    original=ROOT/'rtl/memory/psx_mem_clocks.sv'
    slow=directory/'psx_mem_clocks.sv'
    clock_text=original.read_text()
    if sdram_phy=='falling':
        clock_text=clock_text.replace('62.500000 MHz','31.250000 MHz')\
            .replace('125.000000 MHz','62.500000 MHz').replace('4000 ps','8000 ps')
    slow.write_text(clock_text)
    sources=[source(slow) if Path(s['path'])==original else s for s in sources]
    phy_record=None
    if sdram_phy=='mirlo':
        from prepare_mirlo_sdram_phy import prepare as prepare_phy
        phy_record=prepare_phy(directory/'mirlo-sdram')
        sources=[phy_record if Path(s['path'])==ROOT/'rtl/memory/psx_sdr_phy.sv' else s for s in sources]
    # Asynchronous assertion, local synchronized deassertion for the CDC's
    # state/data banks. The reset synchronizer chains themselves retain the
    # common raw reset. This avoids a genuine recovery violation when memory
    # initialization releases reset between clk_74a edges.
    cdc_original=ROOT/'rtl/memory/psx_mem_cdc.sv'
    cdc=directory/'psx_mem_cdc.sv'; text=cdc_original.read_text()
    for domain in ('src','dst'):
        marker=f'    always @(posedge {domain}_clk or posedge reset_async) begin\n        if (reset_async) begin'
        if text.count(marker)!=1:raise ValueError('CDC reset contract changed')
        text=text.replace(marker,f'    always @(posedge {domain}_clk or posedge {domain}_reset) begin\n        if ({domain}_reset) begin')
    cdc.write_text(text)
    sources=[source(cdc) if Path(s['path'])==cdc_original else s for s in sources]
    init=directory/'apf/build_id.mif';init.parent.mkdir(exist_ok=True)
    init.write_text('WIDTH=32;\nDEPTH=256;\nADDRESS_RADIX=HEX;\nDATA_RADIX=HEX;\nCONTENT BEGIN\n[00..FF] : 00000000;\n00 : 50535850;\nEND;\n')
    qsf=[]
    # Board assignments only: no inherited N64 build hooks or source graph.
    for line in (PLATFORM/'pocket.tcl').read_text().splitlines():
        if line.startswith('set_') and not any(x in line for x in ('SCRIPT_FILE','QIP_FILE')):
            qsf.append(line)
    qsf+=['set_global_assignment -name TOP_LEVEL_ENTITY apf_top',
        'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files',
        'set_global_assignment -name NUM_PARALLEL_PROCESSORS 4',
        'set_global_assignment -name SEED 1',
        'set_global_assignment -name SDC_FILE pocket.sdc',
        'set_global_assignment -name MIF_FILE apf/build_id.mif']
    qsf += [f'set_global_assignment -name {s["assignment"]} "{s["path"]}" -library {s["library"]}' for s in sources]
    (directory/'apf_top.qsf').write_text('\n'.join(qsf)+'\n')
    (directory/'apf_top.qpf').write_text('PROJECT_REVISION = "apf_top"\n')
    (directory/'pocket.sdc').write_text(f'source "{PLATFORM / "apf_constraints.sdc"}"\nderive_clock_uncertainty\n'+
        '# Handshake data is held through synchronized request/response toggles.\n'+
        'set_false_path -to [get_registers {*|request_sync[0] *|response_sync[0] *|mem_ready74[0] *|status_meta[*]}]\n'+
        'set_false_path -from [get_registers {*|power_reset_n}]\n'+
        '# Only the raw asynchronous assertion into reset synchronizers is exempt.\n'+
        '# Their local shift stages and all state/data recovery paths remain timed.\n'+
        'set_false_path -from [get_registers {*|sys_reset[2]}] -to [get_registers {*|src_reset_pipe[*] *|dst_reset_pipe[*]}]\n'+
        'set_max_delay 10 -from [get_registers {*|request_payload[*]}] -to [get_registers {*|d_addr[*] *|d_write_q *|d_halfwords[*] *|d_wdata[*] *|d_byte_en[*]}]\n'+
        'set_max_delay 10 -from [get_registers {*|response_payload[*]}] -to [get_registers {*|s_rsp_rdata[*] *|s_rsp_error}]\n'+
        'set_min_delay 0 -from [get_registers {*|request_payload[*]}] -to [get_registers {*|d_addr[*] *|d_write_q *|d_halfwords[*] *|d_wdata[*] *|d_byte_en[*]}]\n'+
        'set_min_delay 0 -from [get_registers {*|response_payload[*]}] -to [get_registers {*|s_rsp_rdata[*] *|s_rsp_error}]\n'+
        'create_generated_clock -name pocket_sdram -source [get_pins {ic|memory|memory_clocks|pll|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}] -divide_by 1 [get_ports dram_clk]\n'+
        'set_output_delay -clock pocket_sdram -max 1.5 [get_ports {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]\n'+
        'set_output_delay -clock pocket_sdram -min -0.7 [get_ports {dram_a[*] dram_ba[*] dram_dqm[*] dram_dq[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]\n'+
        'set_input_delay -clock pocket_sdram -max 6.0 [get_ports {dram_dq[*]}]\n'+
        'set_input_delay -clock pocket_sdram -min 2.0 [get_ports {dram_dq[*]}]\n'+
        '# External memory timings are audited separately; STA exit is not hardware proof.\n')
    manifest={'kind':'bios-probe','top':'apf_top','device':'5CEBA4F23C8','virtual_pins':False,
        'sources':sources,'included':['complete APF shell','SDRAM controller/PHY/PLL','BIOS loader/readback CRC',
        '640x480 video/DDR output','audio PLL/I2S silence'], 'private_assets_embedded':False,
        'timing_complete':False, 'hardware_verified':False,
        'memory_clock_mhz':125 if sdram_phy=='mirlo' else 62.5,
        'client_clock_mhz':62.5 if sdram_phy=='mirlo' else 31.25,
        'refresh_interval_us':3.84 if sdram_phy=='mirlo' else 7.68,
        'sdram_phy_variant':sdram_phy,'sdram_phy_source':phy_record,
        'hardware_selection_note':('MIRLO baseline CRC passed on one Pocket; new placement/corners remain unverified'
            if sdram_phy=='mirlo' else 'Slow/falling-edge probe failed Pocket BIOS CRC; diagnostic reproduction only'),
        'memory_clock_source_sha256':digest(original),
        'cdc_reset_source_sha256':digest(cdc_original),
        'constraint_sources':{str(PLATFORM/'apf_constraints.sdc'):digest(PLATFORM/'apf_constraints.sdc')},
        'project_hashes':{n:digest(directory/n) for n in ('apf_top.qsf','apf_top.qpf','pocket.sdc','apf/build_id.mif')}}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--output',type=Path,default=ROOT/'build/pocket')
    parser.add_argument('--bin',type=Path)
    args=parser.parse_args()
    runs=args.output/'runs'; runs.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=runs))
    manifest=prepare_probe(directory)
    print(directory,flush=True)
    if args.prepare_only:return
    binary=args.bin or Path(find_tool('quartus_sh')).parent
    original_qsf=(directory/'apf_top.qsf').read_text()
    (directory/'apf_top.qsf.precompile').write_text(original_qsf)
    steps=[]
    with (directory/'quartus.log').open('w') as log:
        for name in ('quartus_map','quartus_fit','quartus_sta','quartus_asm'):
            result=subprocess.run([str(binary/name),'apf_top'],cwd=directory,stdout=log,stderr=subprocess.STDOUT)
            steps.append({'tool':name,'exit_code':result.returncode})
            print(name,result.returncode,flush=True)
            if result.returncode:break
    changed_sources=[s['path'] for s in manifest['sources'] if digest(Path(s['path']))!=s['sha256']]
    raw_changed_projects=[name for name,sha in manifest['project_hashes'].items() if digest(directory/name)!=sha]
    version_only='apf_top.qsf' in raw_changed_projects and compiler_version_append_only(
        original_qsf,(directory/'apf_top.qsf').read_text())
    changed_projects=[name for name in raw_changed_projects if name!='apf_top.qsf' or not version_only]
    changed_constraints=[name for name,sha in manifest['constraint_sources'].items() if digest(Path(name))!=sha]
    report={'steps':steps,'source_identity_verified':not changed_sources,
            'project_identity_verified':not changed_projects and not changed_constraints,
            'changed_sources':changed_sources,'changed_projects':changed_projects,
            'changed_constraint_sources':changed_constraints,'project_hashes_identical':not raw_changed_projects,
            'compiler_version_metadata_appended':version_only,
            'compiled_project_hashes':{name:digest(directory/name) for name in manifest['project_hashes']},
            'timing_verified':False,'hardware_verified':False}
    (directory/'compile-report.json').write_text(json.dumps(report,indent=2)+'\n')
    if steps[-1]['exit_code']:raise SystemExit(steps[-1]['exit_code'])
    if changed_sources or changed_projects or changed_constraints:
        raise SystemExit('Compiled input identity changed; see compile-report.json')
    print('Bitstream:',directory/'output_files/apf_top.rbf',flush=True)


if __name__=='__main__':main()
