#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save device step "save-checksum" (RTL; docs/save-states-finish/checksum-card.md).

Nothing checked a Memory's data words: a word the card slot dropped during the Pocket's upload (a
bridge strobe while the slot's one request register is taken), or a word damaged anywhere between
the save buffer and the SD card, loaded as it was. This step gives the routine two things:

  cs        32-bit register in psx_save_exp1 (clk2x). Every copy-engine word on the aux port, in
            either direction (page -> buffer: the page word; buffer -> page: aux_dout), is added
            to cs rotated left by one bit: cs <= {cs[30:0], cs[31]} + word. So a changed, missing
            or swapped word changes the sum. SPU RAM copies (no aux word) do not count.
            Timing (RC #1312): the word is taken into cs_w on every clk2x and added one clk2x after
            its done (cs_v), so aux_dout (psram clock domain, single-cycle crossing) drives one
            register bit each, never the adder; aux_done drives cs_v only.
            Cleared: by a write of any value to 0x1830-0x1833 (after the sum: the clear wins).
            Read:    bytes 0x1830-0x1833 (little-endian, one lw), bit 31 flipped while a LOAD
                     request is open (want_load) and the card slot's overflow flag is set.
  overflow  psx_memcard_slot's sticky `overflow` (clk_74a; core_top left it unconnected), set (this
            step, audit-059 #8) only by a dropped save-window write, i.e. a lost upload word: into a
            two-flop synchroniser here. Cleared through the slot's own overflow_clear input, a
            one-clk2x pulse this device now gives at every FINISH write (0x1803 without the retry
            bit). So at a load the flag covers the Pocket's upload since the previous request
            ended, and the routine needs no extra instruction for it: a set flag makes the
            checksum compare fail.

The routine (prepare_save_checksum_routine.py) clears cs before the compressed stream's page copies
on both sides, stores cs after them on a save (page 0 word 199), and compares on a load before
anything of the game changes.

core_top: memcard_overflow -> the device's overflow input; the device's overflow_clear -> the
slot's overflow_clear input (was tied low). One SDC line: false path into the synchroniser's first
stage. The slot's own clear path (clr_tgl_2x, s74_clr, clr_seen_74) and overflow are already in its
text and constraints; they were pruned while nothing read the flag.

Cost expected: 32 + 2 + 1 registers in the device; a 32-bit adder with a 2:1 operand select
(about 16 ALMs); one more 4:1 byte source in the read mux; the slot's overflow logic comes back
(about 5 registers). No M10K.

Check: scripts/run_save_checksum.py (device bench, Icarus).
Usage: prepare_save_checksum.py DEVICE.sv --text-only OUT          (one psx_save_exp1.sv)
       prepare_save_checksum.py SOURCE --output OUTPUT [--seed N]  (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-checksum'
CS_ADDR = 0x1830


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


PORT_OLD = "    input  wire [63:0] cd_state\n);\n"
PORT_NEW = f'''\
    input  wire [63:0] cd_state,
    // {TAG}: psx_memcard_slot's sticky overflow (clk_74a: a bridge strobe was dropped) and its
    // clear, a one-clk2x pulse at every FINISH write
    input  wire        overflow,
    output reg         overflow_clear = 1'b0
);
'''
DECL_OLD = "    // register reads: one 16-way byte select over {CD state, GPU ranges}\n"
DECL = f'''\
    // {TAG}: rotated sum of the copy engine's buffer words, read at 0x1830-0x1833; bit 31 reads
    // flipped while a load request is open and the card slot has dropped a bridge word
    reg [31:0] cs = 32'd0;
    // the word goes through a register first (cs_w, taken every clk2x, no enable) and is added one clk2x
    // later (cs_v): aux_dout comes from the psram's clock and drives one flop here, never the adder
    reg [31:0] cs_w = 32'd0;
    reg cs_v = 1'b0;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED_IF_ASYNCHRONOUS" *)
    reg [1:0] s2x_ovf = 2'b00;
    wire [31:0] cs_rd = {{cs[31] ^ (want_load && s2x_ovf[1]), cs[30:0]}};
    wire [7:0] cs_q = cs_rd[8*bus_addr[1:0] +: 8];
'''
DECL_NEW = DECL + DECL_OLD
READ_OLD = "                                                    bus_addr[3] ? disc_id_q :   // save-disc-id\n"
READ = f"                                                    bus_addr[4] ? cs_q :   // {TAG}: 0x1830-0x1833\n"
READ_NEW = READ_OLD + READ
SYNC_OLD = "        s2x_core <= {s2x_core[1:0], core_reset};\n"
SYNC = f'''\
        s2x_ovf <= {{s2x_ovf[0], overflow}};   // {TAG}
        cs_w <= cp_dir ? aux_dout : cp_q;
        cs_v <= own_req && aux_done && !reset_2x && !(wr_edge && reg_area && bus_addr[5] && bus_addr[4] && !bus_addr[3] && !bus_addr[2]);
        overflow_clear <= wr_edge && reg_area && !bus_addr[5] && !bus_addr[4] && bus_addr[3:0] == 4'd3 && !bus_dataWrite[2];
'''
SYNC_NEW = SYNC_OLD + SYNC
SUM_OLD = "            if (up_go) begin if (aux_done) up_go <= 1'b0; end\n"
SUM = f'''\
            // {TAG}: every copy word on the aux port, either direction, one clk2x after its done; a write
            // to 0x1830-0x1833 clears (after the sum: the clear wins, also over a word done on its clock)
            if (cs_v) cs <= {{cs[30:0], cs[31]}} + cs_w;
            if (wr_edge && reg_area && bus_addr[5] && bus_addr[4] && !bus_addr[3] && !bus_addr[2]) cs <= 32'd0;
'''
SUM_NEW = SUM + SUM_OLD

PAIRS = ((PORT_OLD, PORT_NEW), (DECL_OLD, DECL_NEW), (READ_OLD, READ_NEW), (SYNC_OLD, SYNC_NEW), (SUM_OLD, SUM_NEW))

# psx_memcard_slot.sv (audit-059 #8): the flag means "an upload word was lost", nothing else. A dropped
# card-window strobe (card traffic) or a dropped save-window read (a save's read-out; its damage is in
# the Memory and the next load's checksum refuses it) no longer sets it, so it cannot refuse a good
# Memory once.
SLOT_OLD = "            if ((wr_strobe || rd_strobe) && pending) overflow <= 1'b1;\n"
SLOT_NEW = f"            if (wr_strobe && in_save && pending) overflow <= 1'b1;   // {TAG}: a dropped upload word only\n"
SLOT_PAIRS = ((SLOT_OLD, SLOT_NEW),)

# core_top.sv
TOP_WIRE_OLD = "    wire memcard_overflow;   // clk_74a, sticky: a card/save bridge word was dropped\n"
TOP_WIRE_NEW = TOP_WIRE_OLD + f"    wire save_ovf_clear;     // {TAG}: psx_save_exp1's pulse at every FINISH clears the slot's overflow\n"
TOP_CLR_OLD = ".overflow_clear(1'b0)"
TOP_CLR_NEW = ".overflow_clear(save_ovf_clear)"
TOP_DEV_OLD = "        .cd_state(save_cd_state));\n"
TOP_DEV_NEW = f"        .cd_state(save_cd_state),\n        .overflow(memcard_overflow), .overflow_clear(save_ovf_clear));   // {TAG}\n"
TOP_PAIRS = ((TOP_WIRE_OLD, TOP_WIRE_NEW), (TOP_CLR_OLD, TOP_CLR_NEW), (TOP_DEV_OLD, TOP_DEV_NEW))

SDC = f'''
# {TAG}: psx_memcard_slot's sticky overflow (clk_74a) into the save device's two-flop synchroniser
set_false_path -to [get_registers {{*|psx_save_exp1:save_exp1|s2x_ovf[0]}}]
'''

# Planted faults: (old, new) on the transformed device text. Each must be rejected by the bench.
FAULTS = {
    # plain sum, no rotation: two swapped words give the same value
    'no_rotate': [("cs <= {cs[30:0], cs[31]} + cs_w;", "cs <= cs + cs_w;")],
    # a load's words are not summed (the page word is taken in both directions)
    'load_word': [("cs_w <= cp_dir ? aux_dout : cp_q;", "cs_w <= cp_q;")],
    # the slot's card accesses on the aux port count too
    'card_words': [("cs_v <= own_req && aux_done", "cs_v <= aux_done")],
    # a word done on the clock of a clear is still added after it
    'clear_keeps_word': [("cs_v <= own_req && aux_done && !reset_2x && !(wr_edge && reg_area && bus_addr[5] && bus_addr[4] && !bus_addr[3] && !bus_addr[2]);",
                          "cs_v <= own_req && aux_done && !reset_2x;")],
    # the word taken one clk2x late (the next word's value, or the page word after cp_word moved on)
    'late_word': [("cs <= {cs[30:0], cs[31]} + cs_w;", "cs <= {cs[30:0], cs[31]} + (cp_dir ? aux_dout : cp_q);")],
    # the clear is lost when it comes with a copy word's done
    'sum_wins': [(SUM, "            if (wr_edge && reg_area && bus_addr[5] && bus_addr[4] && !bus_addr[3] && !bus_addr[2]) cs <= 32'd0;\n"
                       "            if (cs_v) cs <= {cs[30:0], cs[31]} + cs_w;\n")],
    # the overflow flag is not folded in
    'no_overflow': [("cs[31] ^ (want_load && s2x_ovf[1])", "cs[31]")],
    # the flag is folded in on a save too (a save after a dropped read-out word stores a wrong value)
    'overflow_on_save': [("(want_load && s2x_ovf[1])", "s2x_ovf[1]")],
    # no clear pulse at FINISH
    'no_clear_pulse': [("&& bus_addr[3:0] == 4'd3 && !bus_dataWrite[2];", "&& bus_addr[3:0] == 4'd3 && 1'b0;")],
    # the clear pulse also at a retry (FINISH 4): an upload's drop would be forgotten before the load runs
    'clear_on_retry': [("&& bus_addr[3:0] == 4'd3 && !bus_dataWrite[2];", "&& bus_addr[3:0] == 4'd3;")],
    # byte order of the read
    'byte_order': [("cs_rd[8*bus_addr[1:0] +: 8]", "cs_rd[8*(2'd3 - bus_addr[1:0]) +: 8]")],
    # the clear decode one bit short: 0x1838 (the disc identity's alias) clears too
    'clear_decode': [("bus_addr[5] && bus_addr[4] && !bus_addr[3] && !bus_addr[2]) cs <= 32'd0;",
                      "bus_addr[5] && bus_addr[4] && !bus_addr[2]) cs <= 32'd0;")],
}


def device_text(text):
    """psx_save_exp1.sv (a project's pinned copy, after save-readout-hold) with cs and the overflow port."""
    if TAG in text or 's2x_ovf' in text:
        raise ValueError(f'{TAG}: already applied')
    if 'rd_seen' not in text or 'disc_id_q' not in text:
        raise ValueError(f'{TAG}: run after prepare_save_disc_id and prepare_save_readout_hold')
    for old, new in PAIRS:
        text = _once(text, old, new)
    return text


def restore(text):
    """The device text before the step (the inverse of device_text, exact)."""
    for old, new in reversed(PAIRS):
        text = _once(text, new, old)
    return text


def slot_text(text):
    if TAG in text and SLOT_NEW in text:
        raise ValueError(f'{TAG}: the slot already has the step')
    for old, new in SLOT_PAIRS:
        text = _once(text, old, new)
    return text


def slot_restore(text):
    for old, new in reversed(SLOT_PAIRS):
        text = _once(text, new, old)
    return text


# Planted faults on the slot with the step (scripts/run_save_slot_overflow.py must reject each)
SLOT_FAULTS = {
    'unscoped': [(SLOT_NEW, SLOT_OLD)],                                                          # as before
    'reads_too': [("if (wr_strobe && in_save && pending)", "if ((wr_strobe || rd_strobe) && in_save && pending)")],
    'card_too': [("if (wr_strobe && in_save && pending)", "if (wr_strobe && pending)")],
    'never': [("if (wr_strobe && in_save && pending)", "if (wr_strobe && in_save && pending && 1'b0)")],
}


def top_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: core_top already has the step')
    for old, new in TOP_PAIRS:
        text = _once(text, old, new)
    return text


def sdc_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: pocket.sdc already has the lines')
    if 'psx_save_exp1:save_exp1|s2x_req[0]' not in text:
        raise ValueError(f'{TAG}: pocket.sdc has no psx_save_exp1 synchroniser lines')
    return text + SDC


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None, transforms=None):
    """Snapshot SOURCE into OUTPUT, patch the device, core_top.sv and pocket.sdc there.
    transforms: {file name: function} (default this step's three). A name with a '/' matches the end
    of the path (for a file name that occurs twice in the manifest)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    def _named(path, name):
        return str(path).endswith('/' + name) if '/' in name else Path(path).name == name
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    transforms = transforms or {'psx_save_exp1.sv': device_text, 'psx_memcard_slot.sv': slot_text, 'core_top.sv': top_text,
                                 'pocket.sdc': sdc_text}
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    pinned = {}
    for name in transforms:
        rows = [r for r in manifest['quartus_sources'] if _named(r['path'], name)]
        if len(rows) != 1:
            raise SystemExit(f'{TAG}: {source} does not compile one {name}')
        pinned[name] = rows[0]
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    changed = {}
    for name, transform in transforms.items():
        rows = [Path(r['path']) for r in before if _named(r['path'], name)]
        if len(rows) != 1:
            raise SystemExit(f'{TAG}: snapshot does not compile one {name}')
        path = rows[0]
        # the snapshot relocates project paths in core_top.sv (ROM_HEX) and pocket.sdc; the device and slot have none
        if Path(name).name not in ('core_top.sv', 'pocket.sdc') and digest(path) != pinned[name]['sha256']:
            raise SystemExit(f'{TAG}: snapshot changed {name}')
        changed[str(path)] = digest(path)
        path.write_text(transform(path.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected {", ".join(transforms)} to change, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    for name, row in pinned.items():
        if digest(Path(row['path'])) != row['sha256']:
            raise SystemExit(f'{TAG}: the source project changed ({name})')
    return seed, {p: (old, digest(Path(p))) for p, old in changed.items()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--seed', type=int)
    parser.add_argument('--text-only', type=Path, metavar='OUT')
    args = parser.parse_args()
    try:
        if args.text_only:
            if args.text_only.resolve() == args.source.resolve():
                raise SystemExit(f'{TAG}: refusing to overwrite the input')
            args.text_only.write_text(device_text(args.source.read_text()))
            print(f'{TAG}: wrote {args.text_only} (sha256 {sha(args.text_only.read_text())})')
        else:
            if not args.output:
                parser.error('SOURCE --output OUTPUT required')
            seed, changed = prepare(args.source, args.output, args.seed)
            print(f'{TAG}: snapshot {args.output.resolve()} (seed {seed})')
            for path, (old, new) in changed.items():
                print(f'{TAG}: patched {path} (sha256 {old[:16]} -> {new[:16]})')
            print(f'{TAG}: cs (32) + cs_w (32) + cs_v (1) + overflow synchroniser (2) + clear pulse (1) registers; read 0x1830-0x1833; 1 SDC line')
    except ValueError as error:
        raise SystemExit(str(error))
