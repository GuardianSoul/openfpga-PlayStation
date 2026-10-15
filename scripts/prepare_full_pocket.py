#!/usr/bin/env python3
"""Physical full-feature PSX integration for budgeting, not a release bitstream.

Save/load/rewind and every original controller mode remain reachable through
bridge registers. The host transport is a diagnostic interface; APF save/card
file persistence and the 44.1->48 kHz resampler still require integration.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from build_pocket import prepare_probe, source
from prepare_resource_targets import prepare, wrapper, SYSTEM_FIXED, COMPILE_PROFILES, apply_compile_profile, apply_reset_only, digest
from tool_paths import ROOT, find_tool

VARIANTS = ('gte-adapter', 'pocket-controls', 'cd-arithmetic', 'no-turbo-cache',
    'gpu-line-share', 'gpu-divider-pool', 'gpu-timing-prune', 'spu-context-ram',
    'gpu-coordinates', 'gpu-borrowed-inputs')
# Reset-only area variants that are not part of the 1,910-LAB candidate.
EXTRA_VARIANTS = ('dma-channel-ram', 'spu-reverb-table', 'pocket-dualshock', 'cd-ffvii', 'gte-control-ram', 'gte-matrix-ram', 'gpu-poly-share', 'gpu-vertex-ram', 'gpu-tag-generation', 'gte-multicycle', 'spu-reverb-resample', 'mdec-idct-pipe', 'spu-guest-m10k', 'gpu-mul-narrow', 'gpu-half-pipe', 'gpu-poly-onehot', 'gte-add-table', 'gte-vector-ram', 'dma-chcr-ram', 'gpu-mul-retime', 'gpu-no-watchdog', 'cd-xa-history-clamp', 'mdec-mednafen')

# Same registered scaler protocol as Claude's recompiled early hardware build.
# Kept as one source fragment so the protocol fixture executes the generated RTL.
APF_VIDEO_ADAPTER = '''    // APF scaler protocol: one-clock HS/VS pulses, RGB zero outside DE
    // (nonzero RGB on an HS with DE low selects scaler slots).
    reg apf_hs_d=0, apf_vs_d=0, apf_hs=0, apf_vs=0, apf_de=0, apf_skip=0;
    reg [23:0] apf_rgb=0;
    always @(posedge video_rgb_clock) begin
        apf_hs_d<=hsync; apf_vs_d<=vsync;
        apf_hs<=hsync && !apf_hs_d; apf_vs<=vsync && !apf_vs_d;
        apf_de<=!hblank && !psx_vblank;
        apf_skip<=!hblank && !psx_vblank && !video_ce;
        apf_rgb<=(!hblank && !psx_vblank) ? {video_r,video_g,video_b} : 24'h0;
    end
    assign video_skip=apf_skip, video_rgb=apf_rgb;
    assign video_de=apf_de, video_hs=apf_hs, video_vs=apf_vs;
'''


def prepare_system(directory, variants, reset_only, profile, save_adapters=False):
    system = prepare(directory, ['psx_top'])
    # Restore host save controls before applying any variant. The resource
    # wrapper's historical SYSTEM_FIXED defaults otherwise prune save paths.
    target = system['targets']['psx_top']
    if not reset_only:
        fixed = {k:v for k,v in target['fixed_ports'].items() if k not in (
            'save_state','load_state','rewind_on','rewind_active','state_loaded','validSStates','savestate_number')}
        top, external, text = wrapper('psx_top', fixed)
        path = Path(target['directory']) / (top+'.vhd')
        path.write_text(text)
        target.update(fixed_ports=fixed, external_ports=[n for n,_,_ in external])
        for item in target['quartus_sources']:
            if item['path'] == str(path): item['sha256'] = digest(path)
    if 'pocket-controls' in variants:
        from prepare_pocket_controls import apply_pocket_controls
        system = apply_pocket_controls(system)
    if 'pocket-dualshock' in variants:
        if 'pocket-controls' in variants: raise ValueError('Select one Pocket controls variant')
        from prepare_pocket_dualshock import apply_pocket_dualshock
        system = apply_pocket_dualshock(system)
    if reset_only: system = apply_reset_only(system)
    transforms = (
        ('gte-adapter','prepare_gte_adapter','apply_gte_adapter'),
        ('cd-arithmetic','prepare_cd_arithmetic','apply_cd_arithmetic'),
        ('no-turbo-cache','prepare_no_turbo_cache','apply_no_turbo_cache'),
        ('gpu-line-share','prepare_gpu_line_share','apply_gpu_line_share'),
        ('gpu-divider-pool','prepare_gpu_divider_pool','apply_gpu_divider_pool'),
        ('gpu-timing-prune','prepare_gpu_timing','apply_gpu_timing_prune'),
        ('spu-context-ram','prepare_spu_context_ram','apply_spu_context_ram'),
        ('gpu-coordinates','prepare_gpu_coordinates','apply_gpu_coordinates'),
        ('gpu-borrowed-inputs','prepare_gpu_borrowed','apply_gpu_borrowed'),
        ('dma-channel-ram','prepare_dma_channel_ram','apply_dma_channel_ram'),
        ('dma-chcr-ram','prepare_dma_chcr_ram','apply_dma_chcr_ram'),
        ('spu-reverb-table','prepare_spu_reverb_table','apply_spu_reverb_table'),
        ('spu-reverb-resample','prepare_spu_reverb_resample','apply_spu_reverb_resample'),
        ('spu-guest-m10k','prepare_spu_guest_m10k','apply_spu_guest_m10k'),
        ('cd-ffvii','prepare_cd_ffvii','apply_cd_ffvii'),
        ('cd-xa-history-clamp','prepare_cd_xa_history_clamp','apply_cd_xa_history_clamp'),
        ('gte-control-ram','prepare_gte_control_ram','apply_gte_control_ram'),
        ('gte-matrix-ram','prepare_gte_matrix_ram','apply_gte_matrix_ram'),
        ('gte-add-table','prepare_gte_add_table','apply_gte_add_table'),
        ('gte-vector-ram','prepare_gte_vector_ram','apply_gte_vector_ram'),
        ('gpu-poly-share','prepare_gpu_poly_share','apply_gpu_poly_share'),
        ('gpu-vertex-ram','prepare_gpu_vertex_ram','apply_gpu_vertex_ram'),
        ('gpu-mul-narrow','prepare_gpu_mul_narrow','apply_gpu_mul_narrow'),
        ('gpu-mul-retime','prepare_gpu_mul_retime','apply_gpu_mul_retime'),
        ('gpu-half-pipe','prepare_gpu_half_pipe','apply_gpu_half_pipe'),
        ('gpu-poly-onehot','prepare_gpu_poly_onehot','apply_gpu_poly_onehot'),
        ('gpu-no-watchdog','prepare_gpu_no_watchdog','apply_gpu_no_watchdog'),
        ('gpu-tag-generation','prepare_gpu_tag_generation','apply_gpu_tag_generation'),
        ('gte-multicycle','prepare_gte_multicycle','apply_gte_multicycle'),
        ('mdec-mednafen','prepare_mdec_mednafen','apply_mdec_mednafen'),
        ('mdec-idct-pipe','prepare_mdec_idct_pipe','apply_mdec_idct_pipe'))
    import importlib
    for flag, module, function in transforms:
        if flag in variants:
            if save_adapters and flag=='gte-adapter':
                module,function='prepare_gte_save_adapter','apply_gte_save_adapter'
            if save_adapters and flag=='spu-context-ram':
                module,function='prepare_spu_save_context','apply_spu_save_context'
            system = getattr(importlib.import_module(module), function)(system)
    return apply_compile_profile(system, profile)


def prepare_full(directory, variants=(), reset_only=False, profile='area-min-state', save_adapters=False):
    directory = Path(directory).resolve()
    manifest=prepare_probe(directory)
    system=prepare_system(directory/'psx', variants, reset_only, profile, save_adapters)
    target=system['targets']['psx_top']
    record_fixed=target.get('pocket_controls',{}).get('fixed_record_fields')
    top,ports,_=wrapper('psx_top',target['fixed_ports'],record_fixed)
    declarations=[]; connections=[]; assignments=[]; regs=[]; readbacks=[]
    driven={'clk1x','clk2x','clk3x','clkvid','reset','ram_dataRead32','ram_done','cache_wr','cache_data','cache_addr',
        'dma_wr','dma_reqprocessed','dma_data','ram_dmafifo_read','spuram_dataRead','spuram_done',
        'ddr3_BUSY','ddr3_DOUT','ddr3_DOUT_READY'}
    for i,(name,direction,kind) in enumerate(ports):
        if name=='vblank':
            # The APF dock's vblank input and PSX's vblank output are distinct.
            connections.append('.vblank(psx_vblank)'); declarations.append('    wire psx_vblank;'); continue
        m=re.search(r'\((\d+) downto (\d+)\)',kind)
        bound=re.search(r'integer\s+range\s+(\d+)\s+to\s+(\d+)',kind)
        width=int(m[1])-int(m[2])+1 if m else int(bound[2]).bit_length() if bound else 1
        vrange=f'[{width-1}:0] ' if width>1 else ''
        declarations.append(f'    {"reg" if direction=="in" and name not in driven else "wire"} {vrange}{name}'+('=0' if direction=='in' and name not in driven else '')+';')
        connections.append(f'.{name}({name})')
        if direction=='in' and name not in driven:
            # All original input modes, memory-card transactions, CD streaming,
            # save-state slots and disc changes are writable in this budget top.
            assignments.append(f"        if(bridge_wr && bridge_addr==32'h{0x11000000+i*4:08x}) {name}<=bridge_wr_data[{width-1}:0];")
            regs.append({'name':name,'width':width,'address':0x11000000+i*4})
        else:
            readbacks.append(f"        32'h{0x12000000+i*4:08x}: diagnostic_read={name};")
    p=directory/'core_top.sv'; body=p.read_text()
    body=body.replace('wire reset_n, mem_initialized;','wire reset_n, mem_initialized;\n'+ '\n'.join(declarations))
    # Replace the probe's memory controller with the upstream CPU/DMA/SPU
    # SDRAM interface and a separate, actual paired PSRAM VRAM controller.
    start=body.index('    wire mem_protocol_error;');end=body.index('    wire video_locked;',start)
    body=body[:start]+'''    wire mem_protocol_error=0;
    wire cpu_locked;
    wire ps_clk, ps_clk2;
    altera_pll #(.fractional_vco_multiplier("true"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(3), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("33.868800 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("67.737600 MHz"), .phase_shift1("0 ps"), .duty_cycle1(50),
        .output_clock_frequency2("101.606400 MHz"), .phase_shift2("0 ps"), .duty_cycle2(50)) cpu_pll (
        .refclk(clk_74a), .rst(!power_reset_n), .fbclk(1'b0), .fboutclk(), .locked(cpu_locked),
        .outclk({clk3x,clk2x,clk1x}));
    altera_pll #(.fractional_vco_multiplier("false"), .reference_clock_frequency("74.25 MHz"),
        .operation_mode("normal"), .number_of_clocks(2), .pll_type("General"), .pll_subtype("General"),
        .output_clock_frequency0("61.875000 MHz"), .phase_shift0("0 ps"), .duty_cycle0(50),
        .output_clock_frequency1("123.750000 MHz"), .phase_shift1("0 ps"), .duty_cycle1(50)) psram_pll (
        .refclk(clk_74b),.rst(!power_reset_n),.fbclk(1'b0),.fboutclk(),.locked(),.outclk({ps_clk2,ps_clk}));
    assign clkvid=video_rgb_clock;
    assign mem_clk=clk1x, mem_reset=!cpu_locked;
    assign mem_initialized=cpu_locked;
    assign reset=!reset_n || !passed || !cpu_locked;
    wire loader_request, loader_rnw, loader_done;
    wire [26:0] loader_address;
    wire [31:0] loader_data, loader_read;
    psx_probe_sdram_adapter loader_memory (
        .clk(clk1x), .reset(!cpu_locked), .req_valid(c_valid), .req_ready(c_ready),
        .req_write(c_write), .req_addr(c_addr), .req_data(c_data),
        .rsp_valid(c_rsp), .rsp_ready(c_rsp_ready), .rsp_data(c_read), .rsp_error(c_error),
        .request(loader_request), .rnw(loader_rnw), .address(loader_address), .data(loader_data),
        .done(loader_done), .read_data(loader_read));
    sdram main_memory (
        .init(!cpu_locked), .clk(clk3x), .clk_base(clk1x), .SDRAM_EN(1'b1),
        .SDRAM_DQ(dram_dq), .SDRAM_A(dram_a), .SDRAM_DQML(dram_dqm[0]), .SDRAM_DQMH(dram_dqm[1]),
        .SDRAM_BA(dram_ba), .SDRAM_nCS(), .SDRAM_nWE(dram_we_n), .SDRAM_nRAS(dram_ras_n),
        .SDRAM_nCAS(dram_cas_n), .SDRAM_CKE(dram_cke), .SDRAM_CLK(dram_clk),
        .refreshForce(ram_refresh), .ram_idle(),
        .ch1_addr({2'b0,ram_Adr}), .ch1_dout(), .ch1_dout32(ram_dataRead32), .ch1_din(16'b0),
        .ch1_req(ram_ena && ram_rnw), .ch1_rnw(1'b1), .ch1_dma(ram_dma), .ch1_cntDMA(ram_cntDMA),
        .ch1_cache(ram_cache), .ch1_ready(ram_done_read), .cache_wr(cache_wr), .cache_data(cache_data),
        .cache_addr(cache_addr), .dma_wr(dma_wr), .dma_reqprocessed(dma_reqprocessed), .dma_data(dma_data),
        .ch2_addr(27'h0400000+{8'b0,spuram_Adr}), .ch2_dout(spuram_dataRead), .ch2_din(spuram_dataWrite),
        .ch2_req(spuram_ena), .ch2_rnw(spuram_rnw), .ch2_be(spuram_be), .ch2_ready(spuram_done),
        .ch3_addr(!passed ? loader_address : {2'b0,ram_Adr}), .ch3_dout(loader_read),
        .ch3_din(!passed ? loader_data : ram_dataWrite),
        .ch3_req(loader_request || ram_ena && !ram_rnw), .ch3_rnw(loader_request ? loader_rnw : 1'b0),
        .ch3_be(loader_request ? 4'hf : ram_be), .ch3_ready(loader_done),
        .dmafifo_adr({4'b0,ram_dmafifo_adr}), .dmafifo_data(ram_dmafifo_data),
        .dmafifo_empty(ram_dmafifo_empty), .dmafifo_read(ram_dmafifo_read));
    wire ram_done_read;
    assign ram_done=ram_done_read || loader_done;
    wire v_valid,v_ready,v_write,v_rsp,v_rsp_ready,v_error;
    wire [24:0] v_addr; wire [3:0] v_count;
    wire [127:0] v_data,v_read; wire [15:0] v_be;
    wire vram_fault;
    psx_vram_adapter vram_commands (.clk(clk2x),.reset(!cpu_locked),
        .rd(ddr3_RD),.we(ddr3_WE),.addr(ddr3_ADDR),.count(ddr3_BURSTCNT),.be(ddr3_BE),.din(ddr3_DIN),
        .busy(ddr3_BUSY),.ready(ddr3_DOUT_READY),.dout(ddr3_DOUT),
        .req_valid(v_valid),.req_ready(v_ready),.req_write(v_write),.req_addr(v_addr),.req_count(v_count),
        .req_data(v_data),.req_be(v_be),.rsp_valid(v_rsp),.rsp_ready(v_rsp_ready),
        .rsp_data(v_read),.rsp_error(v_error),.fault(vram_fault));
    wire p_valid,p_ready,p_write,p_rsp,p_rsp_ready,p_error;
    wire [24:0] p_addr; wire [3:0] p_count;
    wire [127:0] p_data,p_read; wire [15:0] p_be;
    psx_mem_cdc vram_cdc (.src_clk(clk2x),.dst_clk(ps_clk),.reset_async(!cpu_locked),
        .s_valid(v_valid),.s_ready(v_ready),.s_write(v_write),.s_addr(v_addr),.s_halfwords(v_count),
        .s_wdata(v_data),.s_byte_en(v_be),.s_rsp_valid(v_rsp),.s_rsp_ready(v_rsp_ready),
        .s_rsp_rdata(v_read),.s_rsp_error(v_error),.d_valid(p_valid),.d_ready(p_ready),.d_write(p_write),
        .d_addr(p_addr),.d_halfwords(p_count),.d_wdata(p_data),.d_byte_en(p_be),
        .d_rsp_valid(p_rsp),.d_rsp_ready(p_rsp_ready),.d_rsp_rdata(p_read),.d_rsp_error(p_error));
    wire z_valid,z_ready,z_we,z_last,z_rvalid,z_idle;
    wire [21:0] z_addr; wire [31:0] z_data,z_read; wire [3:0] z_mask;
    psx_psram_endpoint vram_memory (.clk(ps_clk),.reset(!cpu_locked),
        .req_valid(p_valid),.req_ready(p_ready),.req_write(p_write),.req_addr(p_addr),.req_data(p_data),.req_be(p_be),
        .rsp_valid(p_rsp),.rsp_ready(p_rsp_ready),.rsp_data(p_read),.rsp_error(p_error),
        .z_valid(z_valid),.z_ready(z_ready),.z_we(z_we),.z_last(z_last),.z_rvalid(z_rvalid),.z_idle(z_idle),
        .z_addr(z_addr),.z_data(z_data),.z_read(z_read),.z_mask(z_mask));
    wire [5:0] ps_a; wire [31:0] ps_dq,ps_in; wire ps_oe,ps_adv,ps_oe_n,ps_we_n,ps_cre,ps_clk_en;
    wire [1:0] ps_ce,ps_wait; wire [3:0] ps_bm;
    psram vram_controller (.clk(ps_clk),.rst(!cpu_locked),
        .z_valid(z_valid),.z_ready(z_ready),.z_we(z_we),.z_last(z_last),.z_addr(z_addr),
        .z_wdata(z_data),.z_wmask(z_mask),.z_rvalid(z_rvalid),.z_rdata(z_read),.z_idle(z_idle),
        .x_valid(1'b0),.x_we(1'b0),.x_addr(22'b0),.x_wdata(32'b0),.x_be(4'b0),.x_ack(),.x_rdata(),
        .cfg(4'h5),.cfg_wr(1'b0),.ready(),.wait_err(),.o_a(ps_a),.o_dq(ps_dq),.o_dq_oe(ps_oe),
        .o_adv_n(ps_adv),.o_oe_n(ps_oe_n),.o_we_n(ps_we_n),.o_cre(ps_cre),.o_ce_n(ps_ce),
        .o_bm_n(ps_bm),.o_clk_en(ps_clk_en),.i_dq(ps_in),.i_wait(ps_wait));
    psram_phy vram_pads (.clk(ps_clk),.clk2x(ps_clk2),.o_a(ps_a),.o_dq(ps_dq),.o_dq_oe(ps_oe),
        .o_adv_n(ps_adv),.o_oe_n(ps_oe_n),.o_we_n(ps_we_n),.o_cre(ps_cre),.o_ce_n(ps_ce),
        .o_bm_n(ps_bm),.clk_en(ps_clk_en),.i_dq(ps_in),.i_wait(ps_wait),
);
''' +body[end:]
    # psram_phy is the second module, so enumerate its physical ports explicitly.
    psram_names=[f'cram{i}_{n}' for i in range(2) for n in ('a','dq','wait','clk','adv_n','cre','ce0_n','ce1_n','oe_n','we_n','ub_n','lb_n')]
    body=body.replace('.i_dq(ps_in),.i_wait(ps_wait),\n);', '.i_dq(ps_in),.i_wait(ps_wait),\n'+',\n'.join(f'.{n}({n})' for n in psram_names)+');')
    body=re.sub(r'    assign cram[01]_a=.*?;\n','',body,flags=re.S)
    a=body.index('    psx_test_pattern picture');b=body.index('    wire audio_sclk;',a)
    body=body[:a]+APF_VIDEO_ADAPTER+body[b:]
    if body.count('assign video_skip=0;')!=1:
        raise ValueError('Probe video-skip assignment changed')
    body=body.replace('assign video_skip=0;','')
    body=body.replace('25.200000 MHz','53.693166 MHz').replace('9921 ps','4656 ps')
    body=body.replace('.audio_l(16\'b0), .audio_r(16\'b0)', '.audio_l(sound_out_left), .audio_r(sound_out_right)')
    end=body.index('endmodule')
    body=body[:end]+'    always @(posedge clk_74a) begin\n'+'\n'.join(assignments)+'\n    end\n'+f'    {top} psx ('+',\n'.join(connections)+');\n'+body[end:]
    # Inputs/outputs are addressable for hardware diagnostics, so synthesis
    # cannot remove the original devices as unused black-box budget entries.
    body=body.replace('    always @* begin\n        bridge_rd_data=0;',
        '    reg [31:0] diagnostic_read;\n    always @* begin\n        diagnostic_read=0;\n        case(bridge_addr)\n'+'\n'.join(readbacks)+'\n        endcase\n    end\n    always @* begin\n        bridge_rd_data=diagnostic_read;')
    p.write_text(body)
    original=ROOT/'references/PSX_MiSTer/rtl/sdram.sv'
    sdram=directory/'sdram.sv'; txt=original.read_text()
    txt=txt.replace("CAS_LATENCY         = 3'd2", "CAS_LATENCY         = 3'd3")
    txt=txt.replace("14'd12100", "15'd22000").replace('reg [13:0] refresh_count','reg [14:0] refresh_count')
    txt=txt.replace("startup_refresh_max = 14'b11111111111111", "startup_refresh_max = 15'b111111111111111")
    # Keep original timing for this resource measurement. A separate verified
    # mobile-SDRAM timing adaptation is required before hardware use.
    sdram.write_text(txt)
    sources=manifest['sources']+target['quartus_sources']+[source(sdram),source(ROOT/'references/MIRLO64/rtl/soc/psram.sv')]
    sources += [source(ROOT/'rtl/pocket'/n) for n in ('psx_vram_adapter.sv','psx_psram_endpoint.sv','psx_probe_sdram_adapter.sv')]
    for s in sources:s['sha256']=digest(Path(s['path']))
    qsf=directory/'apf_top.qsf'; lines=qsf.read_text().splitlines()
    lines=[line for line in lines if not re.search(r'-name (?:SYSTEMVERILOG_FILE|VERILOG_FILE|VHDL_FILE)',line)]
    lines.append('set_global_assignment -name VHDL_INPUT_VERSION VHDL_2008')
    # Physical capacity is fixed. Rebalance excess inferred multipliers into
    # logic rather than silently measuring an impossible 83-DSP netlist.
    lines.append('set_global_assignment -name MAX_BALANCING_DSP_BLOCKS 66')
    lines += [f'set_global_assignment -name {s.get("assignment","VHDL_FILE")} "{s["path"]}" -library {s["library"]}' for s in sources]
    lines += [f'set_global_assignment -name {k} "{v}"' for k,v in target['compile_settings'].items()]
    # Generated GTE source includes resolve relative to the pinned source dir.
    for line in (Path(target['directory'])/(top+'.qsf')).read_text().splitlines():
        if '-name SEARCH_PATH ' in line: lines.append(line)
    qsf.write_text('\n'.join(lines)+'\n')
    manifest.update(kind='physical-psx-budget', sources=sources, budget_lab=1663,
        variant_flags={flag:flag in variants for flag in VARIANTS},
        extra_variant_flags={flag:flag in variants for flag in EXTRA_VARIANTS}, reset_only=reset_only,
        save_adapter_extensions=save_adapters,
        compile_profile=profile, compile_settings=system['compile_settings'],
        system_manifest=system, save_state_paths_enabled=not reset_only,
        full_features_restored=([] if reset_only else ['save/load/rewind datapaths'])+
        ([] if 'pocket-controls' in variants else ['four original controller records including analog/DualShock'])+[
        'CD/XA','SPU/reverb','MDEC/FMV','two memory cards','runtime disc availability/lid/metadata'],
        diagnostic_input_registers=regs,
        video_adapter={'registered':True, 'sync_pulse_clocks':1,
            'RGB_black_outside_DE':True, 'skip_only_inside_DE':True},
        release_ready=False, limitations=['Diagnostic host registers, APF file persistence not completed',
        'VRAM adapter serialized 64-bit transfers; sustainable bandwidth must be measured',
        'Save-state high-address mapping into PSRAM must be resolved; never a deployable configuration',
        'SDRAM timing adaptation not verified; full board timing constraints outstanding',
        'Audio currently sample-held into 48 kHz I2S; required resampler not yet included'])
    if 'pocket-controls' in variants:
        manifest['limitations'].append('Measurement uses one digital pad; analog/DualShock must return to the release budget')
    manifest.pop('memory_clock_mhz',None); manifest.pop('client_clock_mhz',None)
    manifest['physical_memory_clocks_mhz']={'native_sdram':101.6064,'cpu_client':33.8688,
        'paired_psram_controller':61.875,'paired_psram_phy':123.75}
    if system.get('sdc_extra'):
        sdc=directory/'pocket.sdc'; sdc.write_text(sdc.read_text()+'\n'.join(system['sdc_extra'])+'\n')
    manifest['project_hashes']={n:digest(directory/n) for n in ('apf_top.qsf','apf_top.qpf','pocket.sdc','apf/build_id.mif')}
    (directory/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--output',type=Path,default=ROOT/'build/pocket/full')
    p.add_argument('--profile', choices=COMPILE_PROFILES, default='area-min-state')
    p.add_argument('--reset-only', action='store_true')
    p.add_argument('--candidate', action='store_true', help='Select every variant of the 1,910-LAB measurement')
    p.add_argument('--save-adapters', action='store_true', help='Quiescent GTE save transfers and sequencer-scheduled SPU context readback; use in BOTH members of a matched pair')
    for flag in VARIANTS+EXTRA_VARIANTS: p.add_argument('--'+flag, action='store_true')
    p.add_argument('--prepare-only',action='store_true');args=p.parse_args()
    variants=[flag for flag in VARIANTS if args.candidate or getattr(args,flag.replace('-','_'))]
    variants+=[flag for flag in EXTRA_VARIANTS if getattr(args,flag.replace('-','_'))]
    runs=args.output/'runs';runs.mkdir(parents=True,exist_ok=True)
    directory=Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'),dir=runs))
    manifest=prepare_full(directory, variants, args.reset_only, args.profile, args.save_adapters);print(directory,flush=True)
    if args.prepare_only:return
    binary=Path(find_tool('quartus_sh')).parent
    report={'tool_version':subprocess.check_output([str(binary/'quartus_sh'),'--version'],text=True).strip(),
        'manifest':manifest,'steps':[],'fit_verified':False}
    with (directory/'quartus.log').open('w') as log:
        for name in ('quartus_map','quartus_fit','quartus_sta'):
            result=subprocess.run([str(binary/name),'apf_top'],cwd=directory,stdout=log,stderr=subprocess.STDOUT)
            report['steps'].append({'tool':name,'exit_code':result.returncode})
            report['source_identity_verified']=all(digest(Path(s['path']))==s['sha256'] for s in manifest['sources'])
            report['project_identity_verified']=all(digest(directory/n)==sha for n,sha in manifest['project_hashes'].items())
            summary=directory/'output_files/apf_top.fit.summary'
            if summary.is_file():
                report['fit_summary']={'path':str(summary),'sha256':digest(summary),'text':summary.read_text()}
                report['fit_verified']=any(s['tool']=='quartus_fit' and s['exit_code']==0 for s in report['steps']) and report['source_identity_verified'] and report['project_identity_verified']
            (directory/'quartus-report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(name,result.returncode,flush=True)
            if result.returncode:raise SystemExit(result.returncode)


if __name__=='__main__':main()
