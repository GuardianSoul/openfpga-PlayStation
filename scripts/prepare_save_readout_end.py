#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Step "save-readout-end" (RTL; docs/save-states-finish/drive-state.md section 9): the end of a Memory's
read-out is known exactly, and the cue reader sends the Pocket no command while a save holds the drive.

test41 (hardware): a disc read sent while the Pocket reads a Memory out is never answered, also after
the read-out; the read-out has pauses of more than 1.5 s, so the quiet-window rule ended the hold early.
Owner's decision (2026-11-12): end the hold on the read-out's end, and send no target command during the read-out.

  psx_save_exp1  rd_last: a bridge read of the save window in the Memory's last page (up_addr[19:9] + 1
                 = SIZE, the pages the Pocket copies; 0x1818/9) since the last SPU_HOLD write; read as
                 bit 2 of 0x180C (set wins over the clear, as rd_seen).
                 cue_busy: psx_cue_mount's APF command in flight (cmd_busy, clk_74a), two flops into clk2x;
                 read as bit 3 of 0x180C.
  psx_cue_mount  save_hold: psx_save_exp1's cd_freeze (set by the routine at the save's quiet point,
                 cleared at its exit, after the read-out), two flops into clk_74a; read by the firmware
                 at 0x08 bit 16 (R_CHEAT: zero there with or without cheats before).
                 cmd_busy_o: cmd_busy out.
  core_top       two wires. pocket.sdc: false paths to the two first flops; rd_last bounded from the
                 slot's held request words as rd_seen is (13 ns max / -13 ns min).
Routine step prepare_save_readout_end_routine.py (hold until rd_last, caps kept; FINISH waits for cue_busy
low) and the firmware (serve() sends nothing while save_hold) go with it.

Expected cost: 5 registers, about 15-20 ALUTs. No RAM.
Check: scripts/run_save_readout_end.py (save device and cue mount, before/after side by side, faults).
Usage: prepare_save_readout_end.py SOURCE --output OUTPUT [--seed N]
       prepare_save_readout_end.py --exp1 FILE | --cue-mount FILE  --text-only OUT
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-readout-end'


def _once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} ({what}): expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


EX = (  # psx_save_exp1.sv (after save-readout-hold)
    ("    output reg         cd_freeze = 1'b0,\n",
     "    output reg         cd_freeze = 1'b0,\n"
     "    input  wire        cue_busy,          // save-readout-end: the cue reader's APF command in flight (clk_74a)\n"),
    ("    reg rd_seen = 1'b0;\n",
     "    reg rd_seen = 1'b0;\n"
     "    // save-readout-end: a read of the Memory's last page since the last SPU_HOLD write (0x180C bit 2);\n"
     "    // the cue reader's command in flight, synchronised (bit 3)\n"
     "    reg rd_last = 1'b0;\n"
     "    (* altera_attribute = \"-name SYNCHRONIZER_IDENTIFICATION FORCED\" *) reg [1:0] cue_busy_s = 2'b00;\n"
     "    always @(posedge clk2x) cue_busy_s <= {cue_busy_s[0], cue_busy};\n"),
    ("            4'd12:   bus_dataRead = {6'd0, rd_seen, spu_quiet};   // save-readout-hold\n",
     "            4'd12:   bus_dataRead = {4'd0, cue_busy_s[1], rd_last, rd_seen, spu_quiet};   // save-readout-hold, save-readout-end\n"),
    ("mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; end",
     "mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; rd_last <= 1'b0; end"),
    ("            if (up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;   // save-readout-hold (after the bus writes: set wins)\n",
     "            if (up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;   // save-readout-hold (after the bus writes: set wins)\n"
     "            if (up_go && !up_we && up_addr[20] && {1'b0, up_addr[19:9]} + 12'd1 == size_pages) rd_last <= 1'b1;   // save-readout-end\n"),
)
CM = (  # psx_cue_mount.sv
    ("    input wire [2:0] d_error,\n",
     "    input wire [2:0] d_error,\n"
     "    input wire save_hold,                        // save-readout-end: a save holds the drive (no APF command now)\n"
     "    output wire cmd_busy_o,                      // save-readout-end: an APF command in flight\n"),
    ("    wire [31:0] cheat_q={ch_tgl!=ch_ack_s[1],vbl_s[1],cheats_on,13'd0,ch_q};",
     "    // save-readout-end: save_hold into clk_74a; read at 0x08 (R_CHEAT) bit 16, with or without cheats\n"
     "    (* altera_attribute = \"-name SYNCHRONIZER_IDENTIFICATION FORCED\" *) reg [1:0] save_hold_s=2'b00;\n"
     "    always @(posedge clk) save_hold_s<={save_hold_s[0],save_hold};\n"
     "    assign cmd_busy_o=cmd_busy;\n"
     "    wire [31:0] cheat_q={ch_tgl!=ch_ack_s[1],vbl_s[1],cheats_on,12'd0,save_hold_s[1],ch_q};"),
    ("    wire [31:0] io_q=CHEATS!=0 && adr[3] ? cheat_q :",
     "    wire [31:0] io_q=CHEATS==0 && adr[3] ? {15'd0,save_hold_s[1],16'd0} : CHEATS!=0 && adr[3] ? cheat_q :"),
)
CT = (  # core_top.sv
    ("    wire save_trap, save_mode, save_cd_freeze,",
     "    wire cue_cmd_busy;   // save-readout-end\n    wire save_trap, save_mode, save_cd_freeze,"),
    (".cd_freeze(save_cd_freeze),", ".cd_freeze(save_cd_freeze), .cue_busy(cue_cmd_busy),"),
    (".d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err),",
     ".d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err), .save_hold(save_cd_freeze), .cmd_busy_o(cue_cmd_busy),"),
)
SDC = f'''
# {TAG}: two-flop synchronisers (psx_cue_mount cmd_busy -> psx_save_exp1, psx_save_exp1 cd_freeze -> psx_cue_mount)
set_false_path -to [get_registers {{*|psx_save_exp1:save_exp1|cue_busy_s[0] *|psx_cue_mount:cue_mount|save_hold_s[0]}}]
# {TAG}: psx_memcard_slot's held request words into rd_last, sampled only while the request is on the aux
# port (the same bounds as rd_seen's, save-readout-hold)
set_max_delay 13 -from [get_registers {{*|psx_memcard_slot:memcard_slot|req_we_74 *|psx_memcard_slot:memcard_slot|req_addr_74[*]}}] -to [get_registers {{*|psx_save_exp1:save_exp1|rd_last}}]
set_min_delay -13 -from [get_registers {{*|psx_memcard_slot:memcard_slot|req_we_74 *|psx_memcard_slot:memcard_slot|req_addr_74[*]}}] -to [get_registers {{*|psx_save_exp1:save_exp1|rd_last}}]
'''

FAULTS = {
    # the bit for any read, not the last page (the hold ends at the first read)
    'ex-any-page': ('ex', "&& {1'b0, up_addr[19:9]} + 12'd1 == size_pages) rd_last", ") rd_last"),
    # one page early (the last page is still to be read when the hold ends)
    'ex-page-early': ('ex', "{1'b0, up_addr[19:9]} + 12'd1 == size_pages", "{1'b0, up_addr[19:9]} + 12'd2 == size_pages"),
    # the bit never cleared (a later save's hold ends at once)
    'ex-never-cleared': ('ex', "rd_seen <= 1'b0; rd_last <= 1'b0; end", "rd_seen <= 1'b0; end"),
    # the card slot's writes (a load's upload) count
    'ex-writes-count': ('ex', "if (up_go && !up_we && up_addr[20] && {1'b0", "if (up_go && up_addr[20] && {1'b0"),
    # cue busy not shown (FINISH does not wait for a command in flight)
    'ex-busy-zero': ('ex', "{4'd0, cue_busy_s[1], rd_last,", "{4'd0, 1'b0, rd_last,"),
    # the cue reader never sees the hold
    'cm-hold-zero': ('cm', "? {15'd0,save_hold_s[1],16'd0} :", "? 32'd0 :"),
}


def ex_text(t):
    if TAG in t: raise ValueError(f'{TAG}: psx_save_exp1 already has the step')
    if 'rd_seen' not in t: raise ValueError(f'{TAG}: run after prepare_save_readout_hold')
    for o, n in EX: t = _once(t, o, n, 'psx_save_exp1')
    return t


def cm_text(t):
    if TAG in t: raise ValueError(f'{TAG}: psx_cue_mount already has the step')
    for o, n in CM: t = _once(t, o, n, 'psx_cue_mount')
    return t


def ct_text(t):
    if TAG in t: raise ValueError(f'{TAG}: core_top already has the step')
    for o, n in CT: t = _once(t, o, n, 'core_top')
    return t


def sdc_text(t):
    if TAG in t: raise ValueError(f'{TAG}: pocket.sdc already has the lines')
    return t + SDC


def restore(t, pairs):
    for o, n in pairs: t = _once(t, n, o, 'restore')
    return t


def fault_text(t, fault):
    _, old, new = FAULTS[fault]
    return _once(t, old, new, fault)


def prepare(source, output, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    names = {'psx_save_exp1.sv': ex_text, 'psx_cue_mount.sv': cm_text, 'core_top.sv': ct_text, 'pocket.sdc': sdc_text}
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, fn in names.items():
        rows = [Path(r['path']) for r in before if Path(r['path']).name == name]
        if len(rows) != 1: raise SystemExit(f'{TAG}: {source} does not compile one {name}')
        changed[str(rows[0])] = digest(rows[0])
        rows[0].write_text(fn(rows[0].read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected {", ".join(names)} to change, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return seed, {p: (old, digest(Path(p))) for p, old in changed.items()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path, nargs='?')
    ap.add_argument('--output', type=Path)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--exp1', type=Path)
    ap.add_argument('--cue-mount', type=Path)
    ap.add_argument('--text-only', type=Path, metavar='OUT')
    args = ap.parse_args()
    try:
        if args.text_only:
            src, fn = (args.exp1, ex_text) if args.exp1 else (args.cue_mount, cm_text)
            args.text_only.write_text(fn(src.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {hashlib.sha256(args.text_only.read_bytes()).hexdigest()})')
        else:
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for p, (o, n) in changed.items(): print(f'{TAG}: patched {p} ({o[:16]} -> {n[:16]})')
    except ValueError as e:
        raise SystemExit(str(e))
