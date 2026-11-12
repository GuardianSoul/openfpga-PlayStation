#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Saved-disc remount, hardware part (a frozen project step).

The save routine re-selects the disc a state was saved on through the path the
"Swap Discs" menu setting already drives (core_top disc_setting -> psx_disc_menu
-> psx_disc_swap -> psx_cd_stream, and psx_cue_mount, which reads the same
register). No disc controller of its own. Works for 2 to 5 discs (Disc 5 has no
slots of its own: the menu mounts disc 1's slot again and the cue firmware
fills it, so the disc is told apart by the menu's selection, not by the slot).
EXP1 in psx_save_exp1:
  0x180B read DISC: [2:0] the disc the menu last selected for mounting - 1 (was
         two bits taken from the slot number). Nothing else in this byte: the
         routine adds it to the state's magic word.
  0x1823 write DISC_SET: [2:0] disc - 1 loaded into the menu setting (clk_74a) as
         if the Pocket had written it; [3] silent: while 1 a swap remounts without
         the lid-open sequence (psx_disc_swap quiet). 0 after reset.
         read [0] settled: the first mount has reached the core, no swap is
         running, the menu's setting pipeline is stable and asks for the mounted
         disc, and the cue firmware is not loading a disc (table grant).
         read [1] wrong disc: the cue firmware could not open the disc the menu
         asks for and kept the one in the drive (psx_cue_mount mount_failed with a
         cue mounted); valid once settled. 0 in builds without the cue reader.
The routine sets the disc, then waits for DISC = the disc it wants and settled,
with a timeout that ends the load with an error before anything is restored.
Without the cue reader a disc the game file does not have is never taken
(psx_disc_menu discs_present): settled stays 0 and the routine times out. With
the cue reader every disc is taken and a missing one shows as the wrong-disc
bit after settled (cue firmware of c2fz5z 409da26f or later sets mount_failed
then); the routine refuses the load.
The routine part is not in this step.
Usage: prepare_save_disc_set.py SOURCE --output OUT
"""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
MARK = 'save-disc-set'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def exp1(t):
    t = once(t, "    input  wire [1:0]  disc,\n",
             "    input  wire [2:0]  disc,\n"
             "    // save-disc-set: DISC_SET (0x1823) toward the menu's disc setting, and the settled level\n"
             "    output reg  [2:0]  disc_set = 3'd0,\n    output reg         disc_set_tgl = 1'b0,\n"
             "    output reg         disc_silent = 1'b0,\n    input  wire        disc_settled,\n    input  wire        disc_wrong,\n")
    t = once(t, "            4'd11:   bus_dataRead = {6'd0, disc};", "            4'd11:   bus_dataRead = {5'd0, disc};")
    t = once(t, "        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} : 8'd0;",
             "        if (reg_area && bus_addr[5]) bus_dataRead = bus_addr[4:0] == 5'd2 ? {7'd0, ss_busy} :\n"
             "                                                    bus_addr[4:0] == 5'd3 ? {6'd0, disc_wrong, disc_settled} : 8'd0;   // save-disc-set")
    t = once(t, "            spu_hold <= 1'b0; sp_st <= CP_IDLE;\n", "            spu_hold <= 1'b0; sp_st <= CP_IDLE; disc_silent <= 1'b0;\n")
    t = once(t, "            if (wr_edge && reg_area && bus_addr[5] && !cp_busy && !ss_busy) begin\n",
             "            if (wr_edge && reg_area && bus_addr[5] && bus_addr[4:0] == 5'd3) begin   // save-disc-set\n"
             "                disc_set <= bus_dataWrite[2:0]; disc_silent <= bus_dataWrite[3]; disc_set_tgl <= !disc_set_tgl;\n"
             "            end\n"
             "            if (wr_edge && reg_area && bus_addr[5] && !cp_busy && !ss_busy) begin\n")
    return t


def core_top(t):
    t = once(t, ".disc(save_disc), .core_held(!reset_n),\n",
             ".disc(save_disc), .core_held(!reset_n),\n"
             "        .disc_set(save_disc_set), .disc_set_tgl(save_disc_set_tgl), .disc_silent(save_disc_silent), .disc_settled(save_disc_settled), .disc_wrong(save_disc_wrong),\n")
    t = once(t, "    wire [1:0] save_disc;   // the disc in the drive - 1, assigned at the end of the module\n",
             "    wire [2:0] save_disc;   // the disc the menu selected for mounting - 1, assigned at the end of the module\n"
             "    wire [2:0] save_disc_set; wire save_disc_set_tgl, save_disc_silent, save_disc_settled, save_sel_stable, save_disc_wrong;   // save-disc-set\n")
    t = once(t, ".disc_pending(cd_disc_pending), .quiet(cd_guest_idle), .change_req(swap_change_req)",
             ".disc_pending(cd_disc_pending), .quiet(cd_guest_idle || save_disc_silent), .change_req(swap_change_req)")
    m = re.findall(r"    always @\(posedge clk_74a\) if \(bridge_wr && bridge_addr==32'h50000044\)\n        (disc_setting<=[^\n]*;)\n", t)
    if len(m) != 1:
        raise ValueError('disc setting register not found once')
    t = re.sub(r"    always @\(posedge clk_74a\) if \(bridge_wr && bridge_addr==32'h50000044\)\n        disc_setting<=[^\n]*;\n", lambda _: (
        "    // save-disc-set: the save routine's DISC_SET takes the place of a menu write (toggle from clk2x;\n"
        "    // save_disc_set is stable long before the synchronised toggle arrives)\n"
        "    (* async_reg=\"true\", preserve, dont_merge *) reg [1:0] save_disc_set_sync=2'b00;\n"
        "    reg save_disc_set_seen=1'b0;\n"
        "    always @(posedge clk_74a) begin\n"
        "        save_disc_set_sync<={save_disc_set_sync[0],save_disc_set_tgl};\n"
        "        save_disc_set_seen<=save_disc_set_sync[1];\n"
        "        if (bridge_wr && bridge_addr==32'h50000044)\n"
        "            " + m[0] + "\n"
        "        else if (save_disc_set_sync[1]!=save_disc_set_seen) disc_setting<=save_disc_set;\n"
        "    end\n"), t)
    t = once(t, ".discs_present(cd_discs_present), .swap_request(disc_menu_request));",
             ".discs_present(cd_discs_present), .swap_request(disc_menu_request),\n"
             "        .selected_disc(save_disc), .selection_stable(save_sel_stable));   // save-disc-set")
    grant = 'cue_table_grant' if re.search(r'\bwire\b[^;\n]*\bcue_table_grant\b', t) else "1'b1"
    wrong = ('cue_mount_failed && cue_mounted' if re.search(r'\bwire\b[^;\n]*\bcue_mount_failed\b', t) and
             re.search(r'\bwire\b[^;\n]*\bcue_mounted\b', t) else "1'b0")
    old = re.findall(r"    assign save_disc = [^\n]*;   // prepare_save_states.py\n", t)
    if len(old) != 1:
        raise ValueError('save_disc assignment not found once')
    t = t.replace(old[0],
                  "    // save-disc-set: settled (clk1x levels; the cue firmware's table grant is a clk_74a level)\n"
                  "    (* async_reg=\"true\", preserve, dont_merge *) reg [1:0] save_cue_grant_sync=2'b00;\n"
                  f"    always @(posedge clk1x) save_cue_grant_sync<={{save_cue_grant_sync[0],{grant}}};\n"
                  "    assign save_disc_settled = cd_disc_settled && !swap_busy && !disc_menu_request && save_sel_stable && save_cue_grant_sync[1];\n"
                  "    (* async_reg=\"true\", preserve, dont_merge *) reg [1:0] save_disc_wrong_sync=2'b00;   // clk_74a levels\n"
                  f"    always @(posedge clk1x) save_disc_wrong_sync<={{save_disc_wrong_sync[0],{wrong}}};\n"
                  "    assign save_disc_wrong = save_disc_wrong_sync[1];\n")
    return t


# Register names as the other EXP1 crossings write them ("*|" stands for the "core_top:ic|" prefix;
# the first version wrote "*|core_top:ic|...", which matches nothing: psx-fit #1149 showed both
# crossings untimed at -6.6 ns). The value is held from before its toggle until the far side has
# seen it, so it gets the same 13 ns window as req_load_74 / fin_ok_2x.
SDC = ('\n# save-disc-set: toggle into clk_74a through a synchroniser; the 3-bit value is held\n'
       'set_false_path -to [get_registers {*|save_disc_set_sync[0]}]\n'
       'set_false_path -to [get_registers {*|save_cue_grant_sync[0]}]\n'
       'set_false_path -to [get_registers {*|save_disc_wrong_sync[0]}]\n'
       'set_max_delay 13 -from [get_registers {*|psx_save_exp1:save_exp1|disc_set[*]}] -to [get_registers {*|disc_setting[*]}]\n'
       'set_min_delay -13 -from [get_registers {*|psx_save_exp1:save_exp1|disc_set[*]}] -to [get_registers {*|disc_setting[*]}]\n')


def compiled(project, name, kind):
    qsf = (project / 'apf_top.qsf').read_text()
    hits = sorted({l.split('"')[1] for l in qsf.splitlines() if kind in l and l.count('"') >= 2 and l.split('"')[1].endswith('/' + name)})
    if len(hits) != 1 or not Path(hits[0]).is_relative_to(project):
        raise ValueError(f'expected one compiled {name} inside the snapshot, found {hits}')
    return Path(hits[0])


def prepare(source, output):
    from run_frozen_pocket_fit import snapshot, hold_guard
    from measure_pocket_seeds import inputs
    source, output = Path(source).resolve(), Path(output).resolve()
    result = snapshot(source, source / 'fit-inputs.json', output, 2)
    files = [(compiled(output, 'psx_save_exp1.sv', 'SYSTEMVERILOG_FILE'), exp1),
             (compiled(output, 'core_top.sv', 'SYSTEMVERILOG_FILE'), core_top),
             (output / 'pocket.sdc', lambda t: t + SDC)]
    for p, f in files:
        text = p.read_text()
        if MARK in text:
            raise ValueError(f'{p.name} already has {MARK}')
        p.write_text(f(text))
    current = inputs(output)
    changed = {a['path'] for a, b in zip(result['quartus_sources'], current) if a != b}
    if len(current) != len(result['quartus_sources']) or changed != {str(p) for p, _ in files}:
        raise ValueError(f'unexpected input changes: {sorted(changed)}')
    hold_guard(output, current)
    result['quartus_sources'] = current
    (output / 'fit-inputs.json').write_text(json.dumps(result, indent=2) + '\n')
    report = dict(source=str(source), changed=sorted(changed), firmware_integrated=False)
    (output / 'save-disc-set.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    a = ap.parse_args()
    print(prepare(a.source, a.output))
