#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save device step "save-core-reset" (RTL: goes into the next fit, not a routine change).

psx_save_exp1 is reset by the PLL lock only (core_top: reset_2x = !cpu_locked). A core
reset while the save routine runs ("Restart Game" during a save or load) leaves what the
routine had set: SAVE_MODE (RAM address bits 22:21 pass), CD_FREEZE (the CD controller's
clock enable off), SPU_HOLD, the set tap, the retry timer, a page copy, and the open
request (the Pocket's busy never ends; every later save or load is answered with an error).
Bench: scripts/run_save_core_reset.py, sim/pocket/tb_save_core_reset.sv.

The step adds a core_reset input (the core's reset, a clk1x register asserted
asynchronously; two synchroniser flops on clk2x). On its RISING EDGE:
  - an open request (or one arriving on that clock) is answered with an error;
  - trap, want_save / want_load, SAVE_MODE, CD_FREEZE, SPU_HOLD, the set tap, the silent
    disc swap and the retry timer are cleared;
  - the copy engine stops: a buffer word already on the aux port ends first (the PSRAM
    controller is not reset by a core reset), the SPU RAM / SPU port handshakes are dropped
    (the SPU is reset with the core).
Only the edge acts: a load that arrives while the Pocket holds the core in reset (a wake)
comes after the edge and runs when the reset ends, as before. Not changed: the staging
page, PAGE_LO / PAGE_HI, the size register, the card slot's aux accesses.
Known consequence: a second reset edge while such a held load waits drops that load (it
was already answered OK to the Pocket).
core_top: the instance gets .core_reset(reset).
Usage: prepare_save_core_reset.py DEVICE.sv CORE_TOP.sv --output OUT     (the two files into OUT)
       prepare_save_core_reset.py SOURCE --output OUTPUT [--seed N]      (frozen project snapshot)
The frozen form snapshots SOURCE (a project with fit-inputs.json, after prepare_save_sp_far_edge)
into OUTPUT, patches the snapshot's psx_save_exp1.sv and core_top.sv, refuses any other input
change and writes fit-inputs.json again.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-core-reset'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'save-core-reset anchor: expected one match, found {text.count(old)}: {old[:90]!r}')
    return text.replace(old, new)


PORT_OLD = '    input  wire        reset_2x,\n'
PORT_NEW = PORT_OLD + '''\
    // save-core-reset: the core's reset (clk1x register, asserted asynchronously). Its rising edge
    // ends what the routine left: an open request is answered with an error; SAVE_MODE, CD_FREEZE,
    // SPU_HOLD, the set tap, the retry timer and the copy engine go back to idle. A request that
    // arrives while the reset is held (a wake's load) comes after the edge and stays.
    input  wire        core_reset,
'''

SS_OLD = '        if (reset_2x) begin ss_seen1 <= ss_tgl; ss_ack1 <= ss_tgl; end\n'
SS_NEW = '        if (reset_2x || core_reset) begin ss_seen1 <= ss_tgl; ss_ack1 <= ss_tgl; end   // save-core-reset\n'

SP_OLD = '''\
        if (spuram_done && sp_pend) begin sp_q1 <= spuram_rdata; sp_done1 <= !sp_done1; sp_pend <= 1'b0; end
'''
SP_NEW = SP_OLD + '''\
        if (core_reset) begin sp_seen1 <= sp_tgl; sp_pend <= 1'b0; spuram_req <= 1'b0; end   // save-core-reset
'''

ALWAYS_OLD = '''\
    always @(posedge clk2x) begin
        s2x_req <= {s2x_req[0], req_tgl_74};
'''
ALWAYS_NEW = '''\
    // save-core-reset
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [2:0] s2x_core = 3'b000;
    wire core_edge = s2x_core[1] && !s2x_core[2];
    reg cp_stop = 1'b0;            // the copy ends with the buffer word on the aux port
    always @(posedge clk2x) begin
        s2x_core <= {s2x_core[1:0], core_reset};
        s2x_req <= {s2x_req[0], req_tgl_74};
'''

TAIL_OLD = '''\
                    else cp_word <= cp_word + 1'b1;
                end
                default: ;
            endcase
        end
    end
endmodule
'''
EDGE = '''\
            // save-core-reset (last, so it wins over the bus writes of the same clock)
            if (core_edge) begin
                if (want_save || want_load || s2x_req[1] != req_seen_2x) begin
                    fin_ok_2x <= 1'b0; fin_tgl_2x <= !fin_tgl_2x;       // the open request: an error
                end
                req_seen_2x <= s2x_req[1];
                trap <= 1'b0; want_save <= 1'b0; want_load <= 1'b0; set_sel <= 3'd0; retry_wait <= 16'd0;
                save_mode <= 1'b0; cd_freeze <= 1'b0; disc_silent <= 1'b0;
                spu_hold <= 1'b0;
                sp_st <= CP_IDLE; ss_ack2 <= ss_tgl; ss_ack3 <= ss_tgl;
                if (cp_st == CP_AUX && ((own_req && !aux_done) || own_start)) cp_stop <= 1'b1;
                else begin cp_st <= CP_IDLE; cp_busy <= 1'b0; end
            end
            if (cp_stop && own_req && aux_done) begin cp_stop <= 1'b0; cp_st <= CP_IDLE; cp_busy <= 1'b0; end
'''
TAIL_NEW = '''\
                    else cp_word <= cp_word + 1'b1;
                end
                default: ;
            endcase
''' + EDGE + '''\
        end
    end
endmodule
'''
RESET_OLD = "            spu_hold <= 1'b0; sp_st <= CP_IDLE; disc_silent <= 1'b0;\n"
RESET_NEW = "            spu_hold <= 1'b0; sp_st <= CP_IDLE; disc_silent <= 1'b0; cp_stop <= 1'b0;\n"

TOP_OLD = '        .clk2x(clk2x), .reset_2x(mc_reset2x[1]),\n'
TOP_NEW = '        .clk2x(clk2x), .reset_2x(mc_reset2x[1]), .core_reset(reset),   // save-core-reset\n'

# Deliberate faults for the bench (never a fitted input)
FAULTS = {
    'spu-hold-kept': ("                spu_hold <= 1'b0;\n", ''),
    'request-not-answered': ("                    fin_ok_2x <= 1'b0; fin_tgl_2x <= !fin_tgl_2x;       // the open request: an error\n",
                             "                    fin_ok_2x <= 1'b0;                                   // FAULT: no answer\n"),
    'level-not-edge': ('    wire core_edge = s2x_core[1] && !s2x_core[2];\n', '    wire core_edge = s2x_core[1];   // FAULT: the level\n'),
}


def device_text(text):
    """psx_save_exp1.sv (with the SPU port and DISC_SET, as in build/ss/d2 and build/ss/e) -> with core_reset."""
    if 'save-core-reset' in text:
        raise ValueError('save-core-reset is applied already')
    for old, new in ((PORT_OLD, PORT_NEW), (SS_OLD, SS_NEW), (SP_OLD, SP_NEW), (ALWAYS_OLD, ALWAYS_NEW),
                     (RESET_OLD, RESET_NEW), (TAIL_OLD, TAIL_NEW)):
        text = once(text, old, new)
    return text


def core_top_text(text):
    if '.core_reset(reset)' in text:
        raise ValueError('save-core-reset is applied already')
    return once(text, TOP_OLD, TOP_NEW)


def fault_text(text, name):
    return once(text, *FAULTS[name])


def prepare(source, output, seed=None):
    """Frozen project step: snapshot SOURCE into OUTPUT and patch the device and core_top there."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    names = {'psx_save_exp1.sv': device_text, 'core_top.sv': core_top_text}
    rows = {n: [r for r in manifest['quartus_sources'] if Path(r['path']).name == n] for n in names}
    if any(len(r) != 1 for r in rows.values()):
        raise SystemExit(f'{TAG}: {source} does not compile one psx_save_exp1.sv and one core_top.sv')
    if 'sp_qf' not in Path(rows['psx_save_exp1.sv'][0]['path']).read_text():
        raise SystemExit(f'{TAG}: run after prepare_save_sp_far_edge')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, transform in names.items():
        path = next(Path(r['path']) for r in before if Path(r['path']).name == name)
        # the snapshot relocates the project paths written in core_top.sv; the device has none
        if name == 'psx_save_exp1.sv' and digest(path) != rows[name][0]['sha256']:
            raise SystemExit(f'{TAG}: snapshot changed {name}')
        changed[str(path)] = digest(path)
        path.write_text(transform(path.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if a.keys() != b.keys() or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected only psx_save_exp1.sv and core_top.sv to change')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if any(digest(Path(r[0]['path'])) != r[0]['sha256'] for r in rows.values()):
        raise SystemExit(f'{TAG}: the source project changed')
    return seed, {p: (old, digest(Path(p))) for p, old in changed.items()}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('inputs', type=Path, nargs='+', metavar='INPUT', help='DEVICE.sv CORE_TOP.sv, or one frozen project')
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seed', type=int)
    a = ap.parse_args()
    try:
        if len(a.inputs) == 1:
            seed, changed = prepare(a.inputs[0], a.output, a.seed)
            print(f'{TAG}: snapshot {a.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new})')
            print(f'{TAG}: core_reset input, 3 synchroniser flops on clk2x, cp_stop; fit-inputs.json rewritten')
        elif len(a.inputs) == 2:
            a.output.mkdir(parents=True, exist_ok=True)
            for source, transform in zip(a.inputs, (device_text, core_top_text)):
                out = a.output / source.name
                out.write_text(transform(source.read_text()))
                print(f'{source} {hashlib.sha256(source.read_bytes()).hexdigest()[:16]} -> {out} {hashlib.sha256(out.read_bytes()).hexdigest()[:16]}')
        else:
            ap.error('DEVICE.sv CORE_TOP.sv, or one frozen project')
    except ValueError as error:
        raise SystemExit(str(error))
