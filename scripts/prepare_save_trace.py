#!/usr/bin/env python3
"""Separate silent-output hardware trace built from the exact test22a closure.

The output converter and PCM observation are removed; unused render arithmetic
may prune. Native SPU RTL and its guest registers/DMA/IRQ wiring are unchanged.
The existing own nonvolatile APF log-slot convention exports on core exit.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

BASE_CPU = 'a65699418f65efc9ec6ab6b1997b8b51bfd2265e5914cf1b5e993e8bc5e06988'
TRACE_CPU_SHA256 = 'edfa7bf661028d9ca2b52bd4a48b6aa4067b9ea406872f16584c8b7eac59ca4c'
TRACE_LOGGER_SHA256 = '3a15144c0cc0fd4036598a211f5783bd96ea3204a990253ff45be1fd560eb41f'
ERROR_ROM_SHA256 = 'f5d18e2f4a9316628b73bca65958ceefb591df0f1c8ae3d406b7fa4799f9df63'
LOG_BYTES = (32 + 768 * 8) * 4
AUDIO_SCOPE_SHA256 = '164a57cdf1e724dcbcd705d5e3f1c5e8be5183e136d7d764a704efcf6d0bb572'
SILENT_RESET_SCOPE = '''# Removed output converter: only the retained silent serializer reset is asynchronous.
set_false_path -from [get_registers {*|audio_reset_async}] -to [get_registers {*|silent_audio_reset[*]}]
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('save trace anchor changed: ' + old.strip()[:90])
    return text.replace(old, new)


def silent_constraints(text):
    start='# Atomic stereo audio stream; no whole-domain cuts\n'
    end='set_false_path -from $audio_reset_origin -to $audio_reset_receivers\n'
    if text.count(start)!=1 or text.count(end)!=1:
        raise ValueError('Expected one selected output-converter constraint block')
    a=text.index(start);b=text.index(end,a)+len(end)
    if hashlib.sha256(text[a:b].encode()).hexdigest()!=AUDIO_SCOPE_SHA256:
        raise ValueError('Output-converter constraint scope changed')
    return text[:a]+SILENT_RESET_SCOPE+text[b:]


PORT = "      save_diag : out std_logic_vector(223 downto 0) := (others => '0');\n"


def cpu_text(text):
    if hashlib.sha256(text.encode()).hexdigest() != BASE_CPU:
        raise ValueError('save trace requires the exact qualified kernel-preserving CPU')
    text = once(text, "      trapRequest           : in  std_logic := '0';\n",
                PORT + "      trapRequest           : in  std_logic := '0';\n")
    # These are existing execute-stage nets. There is no feedback to CPU control.
    hook = '''
   -- save-trace: execute-stage observations; not retirement.
   save_diag(31 downto 0) <= std_logic_vector(pcOld1);
   save_diag(63 downto 32) <= std_logic_vector(opcode1);
   save_diag(68 downto 64) <= std_logic_vector(stall);
   save_diag(73 downto 69) <= std_logic_vector(exception);
   save_diag(74) <= ce;
   save_diag(75) <= reset;
   save_diag(76) <= exceptionTrap;
   save_diag(77) <= trapRequest;
   save_diag(78) <= dmaStallCPU;
   save_diag(79) <= cpuPaused;
   save_diag(80) <= gte_busy;
   save_diag(81) <= executeReadEnable;
   save_diag(82) <= executeMemWriteEnable;
   save_diag(83) <= mem_done;
   save_diag(84) <= mem_fifofull;
   save_diag(85) <= writebackException;
   save_diag(86) <= cop0_CAUSE(31);
   save_diag(87) <= cop0_SR(0);
   save_diag(88) <= cop0_SR(1);
   save_diag(89) <= irqRequest;
   save_diag(90) <= lateReadBypass;
   save_diag(91) <= fetchReady;
   save_diag(92) <= ram_done;
   save_diag(93) <= executeCOP0WriteEnable;
   save_diag(94) <= executeBranchdelaySlot;
   save_diag(95) <= executeBranchTaken;
   save_diag(127 downto 96) <= std_logic_vector(cop0_EPC);
   save_diag(159 downto 128) <= std_logic_vector(cop0_SR);
   save_diag(191 downto 160) <= std_logic_vector(executeReadAddress) when executeReadEnable = '1'
                              else std_logic_vector(executeMemWriteAddr);
   save_diag(223 downto 192) <= std_logic_vector(executeMemWriteData) when executeMemWriteEnable = '1'
                              else mem_dataRead;
'''
    # The architecture's begin is indented in this selected CPU.
    anchors = re.findall(r'(?m)^begin[ \t]*\n', text)
    if len(anchors) != 1:
        raise ValueError('expected one CPU architecture begin')
    anchor = '\n' + anchors[0]
    return once(text, anchor, anchor + hook)


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    if sha(output / 'save-states/psx_save_rom40.hex') != ERROR_ROM_SHA256:
        raise ValueError('Save trace requires the exact error-only ROM')
    before = inputs(output)
    cpu = output / 'cpu-div-shared/cpu.vhd'
    cpu.write_text(cpu_text(cpu.read_text()))
    top = output / 'save-states/psx_top.vhd'
    text = once(top.read_text(), '      save_trap : in std_logic', PORT + '      save_trap : in std_logic')
    text = once(text, '   icpu : entity work.cpu\n   port map\n   (\n',
                '   icpu : entity work.cpu\n   port map\n   (\n      save_diag => save_diag,\n')
    top.write_text(text)
    wrapper = output / 'save-states/psx_mister_psx_top_resource.vhd'
    text = once(wrapper.read_text(), '    save_trap : in std_logic', PORT + '    save_trap : in std_logic')
    text = once(text, '    component_core : entity work.psx_top\n    port map (\n',
                '    component_core : entity work.psx_top\n    port map (\n        save_diag => save_diag,\n')
    wrapper.write_text(text)
    core = output / 'save-states/core_top.sv'
    text = once(core.read_text(), '    psx_mister_psx_top_resource psx (',
                '    wire [223:0] save_diag;\n    psx_mister_psx_top_resource psx (.save_diag(save_diag),')
    start = text.index('    psx_audio_bridge #(')
    end = text.index('    always @(posedge clk_74a) begin\n        if(bridge_wr', start)
    removed = text[start:end]
    if removed.count('audio_stream (') != 1 or removed.count(');') != 1:
        raise ValueError('expected one audio output converter only')
    text = text[:start] + '''    // Diagnostic only: valid silent I2S; unobserved PCM rendering may prune.
    (* async_reg="true", preserve, dont_merge *) reg [2:0] silent_audio_reset=7;
    always @(posedge audio_mclk or posedge audio_reset_async)
        if (audio_reset_async) silent_audio_reset<=7;
        else silent_audio_reset<={silent_audio_reset[1:0],1'b0};
    psx_audio_i2s silent_audio (.audio_clk(audio_mclk),.reset(silent_audio_reset[2]),
        .frame(32'b0),.audio_lrck(audio_lrck),.audio_dac(audio_dac));
    assign audio_fault=1'b0;
''' + text[end:]
    decl = '''
    wire [31:0] save_trace_data;
    reg [31:0] trace_host_events=0;
    always @(posedge clk_74a) trace_host_events <= trace_host_events |
        {22'b0,cd_table_active,ss_load_err,ss_load_ok,ss_load_busy,ss_load_ack,
         ss_start_err,ss_start_ok,ss_start_busy,ss_start_ack,ss_start};
    psx_save_trace save_trace (
        .clk(clk1x),.reset(!cpu_locked),.pc(save_diag[31:0]),.opcode(save_diag[63:32]),
        .flags(save_diag[95:64]),.epc(save_diag[127:96]),
        .bus_addr(save_diag[191:160]),.bus_data(save_diag[223:192]),
        .keys({pocket_pad_buttons,joypad1_KeySquare,joypad1_KeyCross,joypad1_KeyCircle,joypad1_KeyTriangle,
               joypad1_KeyR1,joypad1_KeyL1,joypad1_KeyR2,joypad1_KeyL2,
               joypad1_KeyLeft,joypad1_KeyDown,joypad1_KeyRight,joypad1_KeyUp,
               joypad1_KeyStart,joypad1_KeyR3,joypad1_KeyL3,joypad1_KeySelect}),
        .sticks(pad_sticks_mapped),
        .read_clk(clk_74a),.read_reset(!power_reset_n),.request(ss_start),
        .export_read(bridge_rd && bridge_addr[31:24]==8'h40),
        .read_index(bridge_addr[14:2]),.host_events(trace_host_events),.read_data(save_trace_data));
    reg trace_table_pick=0;
    always @(posedge clk_74a) if (!cd_table_active) trace_table_pick<=!trace_table_pick;
'''
    text = once(text, '    wire save_trap, save_mode,', decl + '\n    wire save_trap, save_mode,')
    anchor = "        if (bridge_addr[31:24]==8'hf8) bridge_rd_data=cmd_bridge_rd_data;"
    text = once(text, anchor, anchor + "\n        if (bridge_addr[31:24]==8'h40) bridge_rd_data=save_trace_data;")
    text = once(text, ".datatable_addr(cd_table_active ? cd_table_addr : 10'd3),",
                ".datatable_addr(cd_table_active ? cd_table_addr : trace_table_pick ? 10'd5 : 10'd3),")
    text = once(text, ".datatable_data(32'd131072),",
                f".datatable_data(trace_table_pick ? 32'd131072 : 32'd{LOG_BYTES}),")
    core.write_text(text)
    logger = output / 'save-states/psx_save_trace.sv'
    logger.write_bytes((Path(__file__).resolve().parents[1] / 'rtl/pocket/psx_save_trace.sv').read_bytes())
    if sha(cpu)!=TRACE_CPU_SHA256 or sha(logger)!=TRACE_LOGGER_SHA256:
        raise ValueError('Trace CPU/logger changed; requalify their fingerprints before fitting')
    qsf = output / 'apf_top.qsf'
    qsf.write_text(qsf.read_text() + f'set_global_assignment -name SYSTEMVERILOG_FILE "{logger}" -library work\n')
    # Control toggles use two-flop synchronizers. Frozen metadata is bundled through a read-side mux:
    # at least two read-clock periods elapse before the read register samples it. The max bound covers the mux path.
    sdc = output / 'pocket.sdc'
    metadata = ['triggered','entered','returned','resumed','frozen','export_stop',
                'trigger_cycle','stop_cycle','pre_count','pre_next','first_count','late_count','late_next',
                'entry_epc','trigger_keys','trigger_sticks']
    patterns = ' '.join('*|save_trace|' + n + ('[*]' if n not in metadata[:6] else '') for n in metadata)
    sdc.write_text(silent_constraints(sdc.read_text()) + '''
# Diagnostic save trace: bounded frozen-header bundle, no guest-path cuts.
set_false_path -to [get_registers {*|save_trace|req_meta *|save_trace|export_meta *|save_trace|frozen_meta}]
''' + 'set_max_delay 20.0 -from [get_registers {' + patterns + '}] -to [get_registers {*|save_trace|header_q[*]}]\n'
        + 'set_min_delay 0.0 -from [get_registers {' + patterns + '}] -to [get_registers {*|save_trace|header_q[*]}]\n'
        + 'set_net_delay -max -from [get_registers {' + patterns + '}] -to [get_registers {*|save_trace|header_q[*]}] 20.0\n')
    after = inputs(output)
    removed=[r['path'] for r in before if r['path'] not in {a['path'] for a in after}]
    if removed != [str(output / 'pocket-audio/coefficients/audio-sinc-mirror.hex18')]:
        raise ValueError('Unexpected non-audio source dependency removed')
    hold_guard(output, after)
    result.update(quartus_sources=after, project_sha256={s: sha(output / ('apf_top'+s)) for s in ('.qsf','.qpf')})
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2)+'\n')
    report = dict(kind='audio-output-disabled-save-trace', source=str(source), log_bytes=LOG_BYTES,
                  cpu_sha256=sha(cpu), logger_sha256=sha(logger), selected=False,
                  native_spu_guest_logic_unchanged=True, pcm_render_may_prune=True,
                  selected_spu_scan_readback_already_zero=True, error_rom_sha256=ERROR_ROM_SHA256,
                  saves_real_states=False, removed=removed,
                  changed=[r['path'] for r in after if next((b['sha256'] for b in before if b['path']==r['path']),None)!=r['sha256']])
    (output / 'save-trace-provenance.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    print(json.dumps(prepare(args.source,args.output),indent=2))
