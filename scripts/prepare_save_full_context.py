#!/usr/bin/env python3
"""Prepare an unselected full-audio PXS4 candidate from the closed test22a base.

Own staged firmware and SPU scan transport; no external save implementation.
This does not establish fit, timing, complete machine coverage or hardware QA.
"""
import argparse
import hashlib
import json
from pathlib import Path

BASE = {
    'save-exp1-pinned/psx_save_exp1.sv': '8ab86f0742110bb06ec10000d7ad0bc8739abee98a780a2ffd7f380b83ddb074',
    'save-states/psx_top.vhd': 'c5be965cc48b01a1e412f57cabd4f96d736937d80120bdbc8a397447851c1e1b',
    'save-states/spu.vhd': '9978e4118bacbb5a5f072440ced98908361b07997c30e8d751f2c3bf0416c966',
    'cpu-div-shared/cpu.vhd': 'e3ffe9423b0ca01e054f29bacdbbd135bd523cd2f76cf222e6c08c0ba14c4704',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Full save context anchor changed: ' + before[:100])
    return text.replace(before, after)


def exp1_text(text):
    # Ordinary APF lifecycle, including busy-request ACK and deferred held load.
    text = once(text, "        if (savestate_start && !start_d) begin\n            savestate_start_ack <= 1'b1;\n            savestate_start_busy <= 1'b0; savestate_start_ok <= 1'b0; savestate_start_err <= 1'b1;\n            req_load_74 <= 1'b0; req_tgl_74 <= !req_tgl_74; // EARLY_HOST_SAVE\n            // Host error is independent of CPU FINISH; diagnostic only.\n        end else if (savestate_load && !load_d) begin\n            savestate_load_ack <= 1'b1;\n            savestate_load_busy <= 1'b0; savestate_load_ok <= 1'b0; savestate_load_err <= 1'b1;\n        end\n", "        if (savestate_start && !start_d) begin\n            savestate_start_ack <= 1'b1;   // (a second save while one runs: only acked)\n            if (!busy_74) begin\n                savestate_start_busy <= 1'b1; savestate_start_ok <= 1'b0; savestate_start_err <= 1'b0;\n                req_load_74 <= 1'b0; req_tgl_74 <= !req_tgl_74;\n            end else if (!savestate_start_busy) begin\n                savestate_start_ok <= 1'b0; savestate_start_err <= 1'b1;\n            end\n        end else if (savestate_load && !load_d) begin\n            savestate_load_ack <= 1'b1;   // (a second load while one runs: only acked)\n            if (!busy_74) begin\n                savestate_load_busy <= !core_held; held_load_74 <= core_held;\n                savestate_load_ok <= core_held; savestate_load_err <= 1'b0;\n                req_load_74 <= 1'b1; req_tgl_74 <= !req_tgl_74;\n            end else if (!savestate_load_busy && !held_load_74) begin\n                savestate_load_ok <= 1'b0; savestate_load_err <= 1'b1;\n            end\n        end\n")
    old = "    assign spu_ss_adr = 9'd0;\n    assign spu_ss_wdata = 32'd0;\n    always @(posedge clk1x) begin spu_ss_rden <= 1'b0; spu_ss_wren <= 1'b0; end\n"
    scan = '''    reg ss_tgl = 0, ss_wr = 0, ss_seen1 = 0, ss_ack1 = 0, ss_ack2 = 0, ss_ack3 = 0;
    wire ss_busy = ss_tgl != ss_ack3;
    wire ss_store = ss_ack2 != ss_ack3 && !ss_wr;
    reg [8:0] ss_adr_1x = 0;
    assign spu_ss_adr = ss_adr_1x;
    assign spu_ss_wdata = cp_q;
    always @(posedge clk1x) begin
        ss_adr_1x <= cp_word;
        spu_ss_rden <= 0; spu_ss_wren <= 0;
        if (reset_2x) begin ss_seen1 <= ss_tgl; ss_ack1 <= ss_tgl; end
        else begin
            if (ss_tgl != ss_seen1) begin
                ss_seen1 <= ss_tgl;
                if (ss_wr) spu_ss_wren <= 1; else spu_ss_rden <= 1;
            end
            ss_ack1 <= ss_seen1;
        end
    end
'''
    text = once(text, old, scan)
    # Bit 5 was previously ignored. The new extension must never alias the
    # existing CD_PUT, SAVE_MODE, CD_FREEZE, SIZE or flag writes.
    text = text.replace('wr_edge && reg_area &&', 'wr_edge && reg_area && !bus_addr[5] &&')
    text = once(text, '        if (reg_area && bus_addr[4]) bus_dataRead = tap_q;',
        '''        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} : 8'd0;
        else if (reg_area && bus_addr[4]) bus_dataRead = tap_q;''')
    text = once(text, '    assign cp_we = buf_store || sp_store;',
                '    assign cp_we = buf_store || sp_store || ss_store;')
    text = once(text, '    always @(*) cp_wd = cp_st == CP_AUX ? aux_dout : {sp_q1, sp_lo};',
                '    always @(*) cp_wd = cp_st == CP_AUX ? aux_dout : sp_st != CP_IDLE ? {sp_q1, sp_lo} : spu_ss_rdata;')
    text = once(text, '        wr_d <= bus_write;',
                '        ss_ack2 <= ss_ack1; ss_ack3 <= ss_ack2;\n        wr_d <= bus_write;')
    text = once(text, "            spu_hold <= 1'b0; sp_st <= CP_IDLE;", "            spu_hold <= 1'b0; sp_st <= CP_IDLE;\n            ss_ack2 <= ss_tgl; ss_ack3 <= ss_tgl;")
    text = once(text, '            // SPU RAM mode of the copy engine', '''            if (wr_edge && reg_area && bus_addr[5] && !cp_busy && !ss_busy) begin
                case (bus_addr[4:0])
                    5'd0: cp_word[7:0] <= bus_dataWrite;
                    5'd1: cp_word[8] <= bus_dataWrite[0];
                    5'd2: if (bus_dataWrite[1:0] != 0) begin ss_wr <= bus_dataWrite[1]; ss_tgl <= !ss_tgl; end
                    default: ;
                endcase
            end

            // SPU RAM mode of the copy engine''')
    # Declare the return toggle before its APF consumer (portable SV elaboration).
    declaration = "    reg req_seen_2x = 1'b0, fin_tgl_2x = 1'b0, fin_ok_2x = 1'b0;\n"
    text = once(text, declaration, '')
    text = once(text, "    reg start_d = 1'b0, load_d = 1'b0;\n",
                declaration + "    reg start_d = 1'b0, load_d = 1'b0;\n")
    return text


def top_text(text):
    for name, rhs in [('adr', 'save_spu_adr'), ('wdata', 'save_spu_wdata'),
                      ('wren', 'save_spu_wren'), ('rden', 'save_spu_rden')]:
        value = "(others => '0')" if name in ('adr', 'wdata') else "'0'"
        # Preserve alignment in the selected source.
        import re
        pattern = rf'(save_spu_ss_{name}\s*<= ){re.escape(value)};'
        text, count = re.subn(pattern, rf'\g<1>{rhs};', text)
        if count != 1:
            raise ValueError('SPU top scan tie changed: ' + name)
    text = once(text, "   save_spu_rdata     <= (others => '0');", '   save_spu_rdata     <= SS_DataRead_SOUND;')
    text = once(text, '   -- (the reset-only sequencer never strobes the SPU\'s register port; the\n   -- save routine restores the SPU by guest writes only, so it is off)',
                '   -- The native PXS5 handler scans expanded SPU context while held quiet.')
    return text


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    from prepare_save_debug_context import cpu_text
    from build_staged_save_fw import image_parts, byte_hex, rom1, rom2, rom40_hex, SRC
    from prepare_spu_sample_save import transform as expanded_spu
    for path, expected in BASE.items():
        if sha(source / path) != expected:
            raise ValueError('Requires exact closed full-audio test22a input: ' + path)
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    changes = {}
    for path, transform in [('cpu-div-shared/cpu.vhd', cpu_text),
                            ('save-exp1-pinned/psx_save_exp1.sv', exp1_text),
                            ('save-states/psx_top.vhd', top_text)]:
        target = output / path
        target.write_text(transform(target.read_text()))
        changes[path] = sha(target)
    spu = output / 'save-states/spu.vhd'
    text = once(spu.read_text(), '   RamVoiceRecord_addrB(0)  <= to_unsigned(index, 5);',
                "   RamVoiceRecord_addrB(0)  <= SS_Adr(6 downto 2) when (save_spu_quiet = '1') else to_unsigned(index, 5);")
    spu.write_text(expanded_spu(text))
    changes[str(spu.relative_to(output))] = sha(spu)
    image, stage2 = image_parts()
    first, second = rom1(image), rom2(image)
    rom = output / 'save-states/psx_save_rom40.hex'
    rom.write_text(rom40_hex(first, second))
    changes[str(rom.relative_to(output))] = sha(rom)
    current = inputs(output)
    previous = result['quartus_sources']
    changed = {a['path'] for a, b in zip(previous, current) if a != b}
    wanted = {str(output / path) for path in changes}
    if len(previous) != len(current) or changed != wanted:
        raise ValueError('Full-context preparation changed other selected inputs')
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    report = dict(source=str(source), selected=False, changes=changes,
                  firmware_source_sha256=sha(SRC), stage2_bytes=len(stage2),
                  full_audio_preserved=True, hardware_verified=False,
                  all_machine_state_covered=False)
    (output / 'save-full-context.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source.resolve(), args.output.resolve()), indent=2))
