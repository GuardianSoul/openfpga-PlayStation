#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Step "save-trap-timeout" (RTL; docs/save-states-finish/audit-fixes.md, audit-059.md finding 3): a save or
load request whose trap the CPU never takes (a game running or crashed with interrupts off) is answered
with an error instead of leaving the Pocket waiting for ever.

  psx_save_exp1  trap_age counts clk2x while `trap` is up and the core runs (core_paused low); a trap that
                 falls (ACK, FINISH, reset, time-out) starts it at 0 again. At 2^TRAP_AGE_BITS (27: 1.98 s)
                 the trap is dropped and the retry timer starts (65,535 clk2x of a running core) as a
                 grace: an ACK in it (the CPU took the trap just before) cancels the time-out and the
                 routine runs as always; otherwise the request ends with an error, as FINISH 2 does
                 (busy falls, err set, want_* cleared). An ACK during the grace (or in the clock of the
                 time-out) clears the time-out and the retry timer; any other ACK is as before.
                 core_paused: psx_top's `pausing` (clk1x register; the Pocket's menu pause, slow CD):
                 a paused core does not age a trap or run down the grace.
  core_top       .core_paused(isPaused)
No SDC line: core_paused is a clk1x register sampled on clk2x of the same PLL, as the bus signals are.
Check: scripts/run_save_trap_timeout.py (old and new device side by side; directed time-out cases; faults).
Usage: prepare_save_trap_timeout.py SOURCE --output OUTPUT [--seed N]
       prepare_save_trap_timeout.py --exp1 FILE --text-only OUT
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-trap-timeout'


def _once(text, old, new, what):
    if text.count(old) != 1:
        raise ValueError(f'{TAG} ({what}): expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


EX = (  # psx_save_exp1.sv (after save-readout-end)
    ("    parameter [31:0] SAVE_SIZE = 32'h0040_0000\n) (",
     "    parameter [31:0] SAVE_SIZE = 32'h0040_0000,\n"
     "    parameter TRAP_AGE_BITS = 27    // save-trap-timeout: an untaken trap ends after 2^TRAP_AGE_BITS clk2x (1.98 s)\n) ("),
    ("    input  wire        core_held,\n",
     "    input  wire        core_held,\n"
     "    input  wire        core_paused,       // save-trap-timeout: the core stands still (psx_top pausing, clk1x)\n"),
    ("    reg want_save = 1'b0, want_load = 1'b0;\n",
     "    reg want_save = 1'b0, want_load = 1'b0;\n"
     "    // save-trap-timeout: clk2x of a running core with the trap up and not taken; after the time-out the\n"
     "    // trap is down and retry_wait runs as a grace for an ACK of a trap taken just before\n"
     "    reg [TRAP_AGE_BITS:0] trap_age = 0;\n"
     "    reg trap_tout = 1'b0;\n"),
    ("            cp_busy <= 1'b0; cp_st <= CP_IDLE; own_req <= 1'b0; up_go <= 1'b0;\n",
     "            cp_busy <= 1'b0; cp_st <= CP_IDLE; own_req <= 1'b0; up_go <= 1'b0;\n"
     "            trap_age <= 0; trap_tout <= 1'b0;   // save-trap-timeout\n"),
    ("""            if (retry_wait != 16'd0) begin
                retry_wait <= retry_wait - 1'b1;
                if (retry_wait == 16'd1) trap <= 1'b1;
            end
""", """            if (retry_wait != 16'd0 && !(trap_tout && core_paused)) begin
                retry_wait <= retry_wait - 1'b1;
                if (retry_wait == 16'd1) begin
                    if (trap_tout) begin   // save-trap-timeout: no ACK in the grace: the request ends with an error
                        trap_tout <= 1'b0; fin_ok_2x <= 1'b0; fin_tgl_2x <= !fin_tgl_2x; want_save <= 1'b0; want_load <= 1'b0;
                    end else trap <= 1'b1;
                end
            end
            // save-trap-timeout: the trap's age (0 while it is down), held while the core is paused
            trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused} : 0;
            if (trap && trap_age[TRAP_AGE_BITS]) begin trap <= 1'b0; trap_tout <= 1'b1; retry_wait <= 16'hFFFF; end
"""),
    ("                4'd4: trap <= 1'b0;\n",
     "                4'd4: begin trap <= 1'b0;   // save-trap-timeout: an ACK cancels a time-out (also one in this clock)\n"
     "                          if (trap_tout || (trap && trap_age[TRAP_AGE_BITS])) begin trap_tout <= 1'b0; retry_wait <= 16'd0; end\n"
     "                      end\n"),
    ("            if (core_edge) begin\n",
     "            if (core_edge) begin\n"
     "                trap_tout <= 1'b0;   // save-trap-timeout\n"),
)
CT = (  # core_top.sv
    (".core_held(!reset_n),", ".core_held(!reset_n), .core_paused(isPaused),"),
)

FAULTS = {
    # the age runs while the core is paused (a save from the Pocket's menu fails)
    'ex-age-in-pause': ("trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused}", "trap_age + {{TRAP_AGE_BITS{1'b0}}, 1'b1}"),
    # no grace: the error at once (a trap taken just before the time-out runs a routine with no request)
    'ex-no-grace': ("if (trap && trap_age[TRAP_AGE_BITS]) begin trap <= 1'b0; trap_tout <= 1'b1; retry_wait <= 16'hFFFF; end",
                    "if (trap && trap_age[TRAP_AGE_BITS]) begin trap <= 1'b0; fin_ok_2x <= 1'b0; fin_tgl_2x <= !fin_tgl_2x; want_save <= 1'b0; want_load <= 1'b0; end"),
    # an ACK in the grace leaves the timer running (the trap comes back during the routine)
    'ex-ack-keeps-timer': ("begin trap_tout <= 1'b0; retry_wait <= 16'd0; end\n", "begin trap_tout <= 1'b0; end\n"),
    # the age is not cleared when the trap falls (several short waits add up to a time-out)
    'ex-age-kept': ("trap_age <= trap ? trap_age + {{TRAP_AGE_BITS{1'b0}}, !core_paused} : 0;",
                    "trap_age <= trap_age + {{TRAP_AGE_BITS{1'b0}}, trap && !core_paused};"),
    # the grace runs down while paused
    'ex-grace-in-pause': ("if (retry_wait != 16'd0 && !(trap_tout && core_paused)) begin", "if (retry_wait != 16'd0) begin"),
}


def ex_text(t):
    if TAG in t: raise ValueError(f'{TAG}: psx_save_exp1 already has the step')
    if 'rd_last' not in t: raise ValueError(f'{TAG}: run after prepare_save_readout_end')
    for o, n in EX: t = _once(t, o, n, 'psx_save_exp1')
    return t


def ct_text(t):
    if TAG in t or 'core_paused' in t: raise ValueError(f'{TAG}: core_top already has the step')
    for o, n in CT: t = _once(t, o, n, 'core_top')
    return t


def restore(t, pairs):
    for o, n in pairs: t = _once(t, n, o, 'restore')
    return t


def fault_text(t, fault):
    old, new = FAULTS[fault]
    return _once(t, old, new, fault)


def prepare(source, output, seed=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    load_source(source, source / 'fit-inputs.json')
    names = {'psx_save_exp1.sv': ex_text, 'core_top.sv': ct_text}
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
    ap.add_argument('--text-only', type=Path, metavar='OUT')
    args = ap.parse_args()
    try:
        if args.text_only:
            args.text_only.write_text(ex_text(args.exp1.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {hashlib.sha256(args.text_only.read_bytes()).hexdigest()})')
        else:
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for p, (o, n) in changed.items(): print(f'{TAG}: patched {p} ({o[:16]} -> {n[:16]})')
    except ValueError as e:
        raise SystemExit(str(e))
