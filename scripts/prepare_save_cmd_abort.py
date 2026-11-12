#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Step "save-cmd-abort" (RTL; docs/save-states-finish/drive-state.md section 8): the cue reader can give
up an APF target command the Pocket never answers.

test41 (hardware): a disc read issued while the Pocket reads a Memory out is never completed, even after
the read-out. core_bridge_cmd waits in TARG_ST_WAITRESULT_DSO for "ok" without a limit, psx_cue_mount
waits for done, the firmware's cmd() polls without a limit: one dropped command stops every later disc
read (and the Pocket's own transfers find the target channel busy).

  psx_cue_mount   a firmware write to W_CLR (0x1C) with bit 8 (CLR_ABORT) while a command runs: the
                  command ends with error 7 (cmd_busy off, cmd_err 7) and d_abort pulses for one clock.
  core_bridge_cmd target_dataslot_abort: in TARG_ST_WAITRESULT_DSO the command ends with error 7, done,
                  target_0[31:16] cleared ("cm" withdrawn), back to TARG_ST_IDLE.
  core_top        one wire.
The firmware (cue_mount.c cmd()) writes CLR_ABORT after retry_wait << 6 poll turns (about 60 s); the
stream's read is then asked again (serve(): retry with the file opened again). On a core without this
step the write does nothing (bit 8 of W_CLR is unused) and the firmware waits as before.

All on clk_74a. Expected cost: 1 register (d_abort), about 6-10 ALUTs. No SDC line.
Check: scripts/run_cue_mount.py stream-drop (psx_cue_mount with this step: CUE_MOUNT_SV), and
scripts/run_save_cmd_abort.py (core_bridge_cmd's target path, before/after side by side, faults).
Usage: prepare_save_cmd_abort.py SOURCE --output OUTPUT [--seed N]       (frozen project snapshot)
       prepare_save_cmd_abort.py --cue-mount FILE --text-only OUT          (one psx_cue_mount.sv)
       prepare_save_cmd_abort.py --bridge-cmd FILE --text-only OUT         (one core_bridge_cmd.v)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-cmd-abort'


def _once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} ({what}): expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


CM = (  # psx_cue_mount.sv
    ("    input wire [2:0] d_error,\n",
     "    input wire [2:0] d_error,\n"
     "    output reg d_abort=0,                        // save-cmd-abort: the running APF command is given up (core_bridge_cmd)\n"),
    ("        d_read<=0;d_getfile<=0;d_openfile<=0;\n",
     "        d_read<=0;d_getfile<=0;d_openfile<=0;d_abort<=0;\n"),
    ("                    if(db_dat[7] && !in_menu) menu_seen<=0;\n",
     "                    if(db_dat[7] && !in_menu) menu_seen<=0;\n"
     "                    if(db_dat[8] && cmd_busy) begin d_abort<=1;cmd_busy<=0;cmd_err<=3'd7;end   // save-cmd-abort\n"),
)
BC = (  # core_bridge_cmd.v
    ("output  reg     [2:0]   target_dataslot_err,        // contains result of command execution. zero is OK\n",
     "output  reg     [2:0]   target_dataslot_err,        // contains result of command execution. zero is OK\n"
     "input   wire            target_dataslot_abort,      // save-cmd-abort: give the running command up (error 7)\n"),
    ("            target_dataslot_done <= 1;\n            tstate <= TARG_ST_IDLE;\n        end\n    end\n    TARG_ST_WAITRESULT_RTR: begin\n",
     "            target_dataslot_done <= 1;\n            tstate <= TARG_ST_IDLE;\n        end\n"
     "        if(target_dataslot_abort) begin   // save-cmd-abort: the Pocket never answered\n"
     "            target_dataslot_err <= 3'd7;\n            target_dataslot_done <= 1;\n"
     "            target_0[31:16] <= 16'h0000;\n            tstate <= TARG_ST_IDLE;\n        end\n"
     "    end\n    TARG_ST_WAITRESULT_RTR: begin\n"),
)
CT = (  # core_top.sv
    ("        .target_dataslot_err(cue_apf_target_dataslot_err),\n",
     "        .target_dataslot_err(cue_apf_target_dataslot_err),\n"
     "        .target_dataslot_abort(cue_apf_target_dataslot_abort),   // save-cmd-abort\n"),
    ("    wire [2:0] cue_apf_target_dataslot_err;",
     "    wire cue_apf_target_dataslot_abort;   // save-cmd-abort\n    wire [2:0] cue_apf_target_dataslot_err;"),
    (".d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err),",
     ".d_done(cue_apf_target_dataslot_done), .d_error(cue_apf_target_dataslot_err), .d_abort(cue_apf_target_dataslot_abort),"),
)

# Planted faults on the transformed texts: (file kind, old, new)
FAULTS = {
    # psx_cue_mount: the abort does not end the command (the firmware keeps waiting)
    'cm-busy-kept': ('cm', "d_abort<=1;cmd_busy<=0;cmd_err<=3'd7;", "d_abort<=1;cmd_err<=3'd7;"),
    # psx_cue_mount: the abort ends the command without an error (the stream gets an unfilled buffer)
    'cm-no-error': ('cm', "d_abort<=1;cmd_busy<=0;cmd_err<=3'd7;", "d_abort<=1;cmd_busy<=0;"),
    # core_bridge_cmd: the abort is ignored (the next command waits for ever)
    'bc-ignored': ('bc', "        if(target_dataslot_abort) begin   // save-cmd-abort", "        if(1'b0) begin   // save-cmd-abort"),
    # core_bridge_cmd: "cm" stays up after the abort (the Pocket may still run the dropped command)
    'bc-cm-kept': ('bc', "            target_0[31:16] <= 16'h0000;\n", ""),
}


def cm_text(t):
    if TAG in t: raise ValueError(f'{TAG}: psx_cue_mount already has the step')
    for o, n in CM: t = _once(t, o, n, 'psx_cue_mount')
    return t


def bc_text(t):
    if TAG in t: raise ValueError(f'{TAG}: core_bridge_cmd already has the step')
    for o, n in BC: t = _once(t, o, n, 'core_bridge_cmd')
    return t


def ct_text(t):
    if TAG in t: raise ValueError(f'{TAG}: core_top already has the step')
    for o, n in CT: t = _once(t, o, n, 'core_top')
    return t


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
    names = {'psx_cue_mount.sv': cm_text, 'core_bridge_cmd.v': bc_text, 'core_top.sv': ct_text}
    for name in names:
        if len([r for r in manifest['quartus_sources'] if Path(r['path']).name == name]) != 1:
            raise SystemExit(f'{TAG}: {source} does not compile one {name}')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, fn in names.items():
        path = next(Path(r['path']) for r in before if Path(r['path']).name == name)
        changed[str(path)] = digest(path)
        path.write_text(fn(path.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected psx_cue_mount.sv, core_bridge_cmd.v and core_top.sv to change, nothing else')
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
    ap.add_argument('--cue-mount', type=Path)
    ap.add_argument('--bridge-cmd', type=Path)
    ap.add_argument('--text-only', type=Path, metavar='OUT')
    args = ap.parse_args()
    try:
        if args.text_only:
            src, fn = (args.cue_mount, cm_text) if args.cue_mount else (args.bridge_cmd, bc_text)
            args.text_only.write_text(fn(src.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {hashlib.sha256(args.text_only.read_bytes()).hexdigest()})')
        else:
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for p, (o, n) in changed.items(): print(f'{TAG}: patched {p} ({o[:16]} -> {n[:16]})')
    except ValueError as e:
        raise SystemExit(str(e))
