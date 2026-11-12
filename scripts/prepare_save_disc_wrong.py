#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Step "save-disc-wrong" (RTL, core_top.sv only; audit-059 #15, drive-state.md section 10).

The save routine's disc_mount reads "wrong disc" (DISC_SET bit 1) as psx_cue_mount's mount_failed AND
cue_mounted. When a Swap Discs change fails to mount and the remount of the current disc fails too
(two APF errors in a row), the firmware leaves mount_failed set and cue_mounted clear: "wrong disc" read
0, the routine took the drive for mounted, and loaded. With this step "wrong disc" is mount_failed
alone: a load is refused whenever the last disc the cue reader tried did not mount (a disc the game
lacks, as before, or no cue disc at all). A game left to APF (game JSON) never sets mount_failed.

  save_disc_wrong_sync <= {save_disc_wrong_sync[0], cue_mount_failed};   (was: cue_mount_failed && cue_mounted)

One AND input less; the synchroniser now samples a single register.

audit-059 #17 (same step): DISC (0x180B, the disc a Memory records and a load compares) was the Swap
Discs setting (psx_disc_menu selected_disc) also when the cue reader had launched another disc. The cue
firmware now reports the disc in the drive (cur, from "(Disc n)" or the playlist entry; 0 unknown) at the
end of every boot, pick and menu change, by a W_CLR write with bit 4 (bits 3:0 = cur; no other W_CLR
write sets bit 4):
  psx_cue_mount  cur_disc (4 bits) latched from that write; output cur_disc_o.
  core_top       cue_mounted and cur_disc_o through two clk1x flops; save_disc = the cue disc
                 (cur - 1, disc 1 for 0) while a cue is mounted, else the menu's selection as before.
  pocket.sdc     false paths to the first flops.
A failed switch to a disc the game lacks now leaves DISC at the current disc, so the routine's
disc_mount waits its poll limit and refuses (before: refused at once on the wrong-disc bit).
Check: scripts/run_save_disc_wrong.py (both parts side by side, faults).
Usage: prepare_save_disc_wrong.py SOURCE --output OUTPUT [--seed N]
"""
import argparse
import json
import re
import sys
from pathlib import Path

TAG = 'save-disc-wrong'
OLD = "    always @(posedge clk1x) save_disc_wrong_sync<={save_disc_wrong_sync[0],cue_mount_failed && cue_mounted};\n"
NEW = ("    // save-disc-wrong: the last disc the cue reader tried did not mount (cue_mounted or not; audit-059 #15)\n"
       "    always @(posedge clk1x) save_disc_wrong_sync<={save_disc_wrong_sync[0],cue_mount_failed};\n")
CM = (  # psx_cue_mount.sv: the disc in the drive from the firmware
    ("    output reg mount_failed=0,                    // the last cue did not mount\n",
     "    output reg mount_failed=0,                    // the last cue did not mount\n"
     "    output reg [3:0] cur_disc_o=0,                // save-disc-wrong: the disc in the drive (firmware cur; 0 unknown)\n"),
    ("                    if(db_dat[7] && !in_menu) menu_seen<=0;\n",
     "                    if(db_dat[7] && !in_menu) menu_seen<=0;\n"
     "                    if(db_dat[4]) cur_disc_o<=db_dat[3:0];   // save-disc-wrong (audit-059 #17)\n"),
)
CT2 = (  # core_top.sv: DISC from the cue disc while one is mounted
    ("        .selected_disc(save_disc), .selection_stable(save_sel_stable));   // save-disc-set\n",
     "        .selected_disc(save_menu_disc), .selection_stable(save_sel_stable));   // save-disc-set, save-disc-wrong\n"
     "    // save-disc-wrong (audit-059 #17): DISC is the cue reader's disc in the drive while a cue is mounted\n"
     "    wire [2:0] save_menu_disc; wire [3:0] cue_cur_disc;\n"
     "    (* async_reg=\"true\", preserve, dont_merge *) reg [4:0] save_cue_disc_sync0=5'd0, save_cue_disc_sync1=5'd0;\n"
     "    always @(posedge clk1x) begin save_cue_disc_sync0<={cue_mounted,cue_cur_disc}; save_cue_disc_sync1<=save_cue_disc_sync0; end\n"
     "    assign save_disc = !save_cue_disc_sync1[4] ? save_menu_disc : save_cue_disc_sync1[3:0]==4'd0 ? 3'd0 : save_cue_disc_sync1[2:0]-3'd1;\n"),
    (".cue_mounted(cue_mounted), .mount_failed(cue_mount_failed));",
     ".cue_mounted(cue_mounted), .mount_failed(cue_mount_failed), .cur_disc_o(cue_cur_disc));"),
)
SDC = '''
# save-disc-wrong: the cue reader's disc in the drive into clk1x (two flops)
set_false_path -to [get_registers {*|save_cue_disc_sync0[*]}]
'''
FAULTS = {
    # the step undone in effect
    'and-mounted': (NEW.splitlines()[1] + '\n', OLD),
    # wrong disc from cue_mounted (a mounted disc reads "wrong")
    'mounted-only': ("save_disc_wrong_sync[0],cue_mount_failed};", "save_disc_wrong_sync[0],cue_mounted};"),
    # the synchroniser stage skipped (a single flop from clk_74a)
    'one-flop': ("save_disc_wrong_sync<={save_disc_wrong_sync[0],cue_mount_failed};", "save_disc_wrong_sync<={cue_mount_failed,cue_mount_failed};"),
    # #17: the menu's disc kept (the cue disc never used)
    'menu-disc': ("assign save_disc = !save_cue_disc_sync1[4] ?", "assign save_disc = 1'b1 ?"),
    # #17: the firmware's number used as is (disc n read as n, not n - 1)
    'off-by-one': ("save_cue_disc_sync1[2:0]-3'd1;", "save_cue_disc_sync1[2:0];"),
    # #17: the latch on any W_CLR write (bits 3:0 of a CLR_MENU write)
    'any-write': ("if(db_dat[4]) cur_disc_o<=db_dat[3:0];", "cur_disc_o<=db_dat[3:0];"),
}


def _once(t, a, b):
    if t.count(a) != 1: raise ValueError(f'{TAG}: anchor not found once: {a.strip()[:70]}')
    return t.replace(a, b)


def ct_text(t):
    if TAG in t: raise ValueError(f'{TAG}: core_top already has the step')
    t = _once(t, OLD, NEW)
    for a, b in CT2: t = _once(t, a, b)
    return t


def cm_text(t):
    if TAG in t: raise ValueError(f'{TAG}: psx_cue_mount already has the step')
    for a, b in CM: t = _once(t, a, b)
    return t


def sdc_text(t):
    if TAG in t: raise ValueError(f'{TAG}: pocket.sdc already has the lines')
    return t + SDC


def restore(t):
    if 'cur_disc_o<=' in t:
        for a, b in CM: t = _once(t, b, a)
        return t
    for a, b in CT2: t = _once(t, b, a)
    return _once(t, NEW, OLD)


def fault_text(t, name):
    old, new = FAULTS[name]
    return _once(t, old, new)


def prepare(source, output, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, fn in (('core_top.sv', ct_text), ('psx_cue_mount.sv', cm_text), ('pocket.sdc', sdc_text)):
        rows = [Path(r['path']) for r in before if Path(r['path']).name == name]
        if len(rows) != 1: raise SystemExit(f'{TAG}: not one {name}')
        changed[str(rows[0])] = digest(rows[0]); rows[0].write_text(fn(rows[0].read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected core_top.sv, psx_cue_mount.sv, pocket.sdc to change, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    return seed, {p: (o, digest(Path(p))) for p, o in changed.items()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int)
    args = ap.parse_args()
    try:
        seed, changed = prepare(args.source, args.output, args.seed)
        print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
        for p, (o, n) in changed.items(): print(f'{TAG}: patched {p} ({o[:16]} -> {n[:16]})')
    except ValueError as e:
        raise SystemExit(str(e))
