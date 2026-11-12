#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save device step "save-readout-hold" (RTL: goes into the next fit; docs/save-states-finish/readout-hold.md).

After a save's FINISH the Pocket reads the Memory out of the save buffer through bridge reads of
0x30000000-0x303FFFFF and serves no disc data meanwhile. The routine is to keep the game stopped
until that read-out is over (prepare_save_readout_hold_routine.py); the Pocket gives no "done", so
the routine has to see the reads. This step gives it the one bit it lacks:

  rd_seen   one register in psx_save_exp1 (clk2x). Every bridge read of the save window is one aux
            read of psx_memcard_slot with address bit 20 set, and the slot's aux requests pass through
            this device (up_req / up_we / up_addr, already behind the slot's own synchroniser).
            Set:     while such a request is on the aux port (up_go && !up_we && up_addr[20]).
            Cleared: by any write to SPU_HOLD (0x180C; the existing decode. The routine rewrites the
                     value it already holds). Set wins over a clear on the same clock.
            Read:    bit 1 of the SPU_HOLD read (0x180C); bit 0 stays spu_quiet (every reader masks it).
  No counter and no new synchroniser: the routine does the timing (clear, wait a window, read).
  Card slot accesses (address bit 20 clear), the Pocket's writes of the window (a load's upload) and
  the copy engine's own accesses do not set it. core_top is not changed.

  SDC (appended to pocket.sdc): the slot's held request words req_we_74 and req_addr_74[20] reach a
  second clk2x register. They are loaded with the request toggle and held until the answer has
  crossed back; up_go rises two clk2x or more after the toggle. Same bounds as the slot's own lines.

Cost expected: 1 register, about 2 LUTs (set term; one more input on read bit 1). No M10K.

Check: scripts/run_save_readout_hold.py (device bench, Icarus).
Usage: prepare_save_readout_hold.py DEVICE.sv --text-only OUT          (one psx_save_exp1.sv)
       prepare_save_readout_hold.py SOURCE --output OUTPUT [--seed N]  (frozen project snapshot)
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

TAG = 'save-readout-hold'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'{TAG}: expected 1 match, found {text.count(old)}: {old.strip()[:80]}')
    return text.replace(old, new)


DECL_OLD = "    reg up_go = 1'b0;              // a card access is on the aux port\n"
DECL_NEW = DECL_OLD + f'''\
    // {TAG}: a bridge read of the save window (the slot's aux read, address bit 20) was on the
    // aux port since the last SPU_HOLD write; read as bit 1 of 0x180C
    reg rd_seen = 1'b0;
'''
READ_OLD = "            4'd12:   bus_dataRead = {7'd0, spu_quiet};\n"
READ_NEW = "            4'd12:   bus_dataRead = {6'd0, rd_seen, spu_quiet};   // save-readout-hold\n"
WRITE_OLD = "                4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; end   // save-mdec-tap\n"
WRITE_NEW = "                4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; end   // save-mdec-tap, save-readout-hold\n"
SET_OLD = "            if (up_go) begin if (aux_done) up_go <= 1'b0; end\n"
SET = "            if (up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;   // save-readout-hold (after the bus writes: set wins)\n"
SET_NEW = SET + SET_OLD

SDC = f'''
# {TAG}: psx_memcard_slot's held request words into the save device's rd_seen, sampled
# only while the request is on the aux port (as the slot's own lines above)
set_max_delay 13 -from [get_registers {{*|psx_memcard_slot:memcard_slot|req_we_74 *|psx_memcard_slot:memcard_slot|req_addr_74[20]}}] -to [get_registers {{*|psx_save_exp1:save_exp1|rd_seen}}]
set_min_delay -13 -from [get_registers {{*|psx_memcard_slot:memcard_slot|req_we_74 *|psx_memcard_slot:memcard_slot|req_addr_74[20]}}] -to [get_registers {{*|psx_save_exp1:save_exp1|rd_seen}}]
'''

PAIRS = ((DECL_OLD, DECL_NEW), (READ_OLD, READ_NEW), (WRITE_OLD, WRITE_NEW), (SET_OLD, SET_NEW))

# Planted faults: (old, new) on the transformed device text.
FAULTS = {
    # a read never sets the bit again once the routine has cleared it (the "quiet timer never retriggers" case:
    # the routine's window always reads quiet)
    'never_set': [(SET, "            if (1'b0 && up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;\n")],
    # the window decode one address bit off: card slot word addresses with bit 19 count, the window's lower half does not
    'window_bit': [('!up_we && up_addr[20]) rd_seen', '!up_we && up_addr[19]) rd_seen')],
    # the bit is not cleared: seen for good after the first save's read-out
    'never_cleared': [("mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; end", 'mdec_tap <= bus_dataWrite[1]; end')],
    # the Pocket's writes of the window (a load's upload) count as reads
    'writes_count': [('up_go && !up_we && up_addr[20]) rd_seen', 'up_go && up_addr[20]) rd_seen')],
    # the clear wins over a read on the same clock
    'clear_wins': [(SET, ''), (WRITE_NEW, "                4'd12: begin spu_hold <= bus_dataWrite[0]; mdec_tap <= bus_dataWrite[1]; rd_seen <= 1'b0; end\n"),
                   ("            if (wr_edge && reg_area && !bus_addr[5] && !bus_addr[4]) case (bus_addr[3:0])\n",
                    "            if (up_go && !up_we && up_addr[20]) rd_seen <= 1'b1;\n"
                    "            if (wr_edge && reg_area && !bus_addr[5] && !bus_addr[4]) case (bus_addr[3:0])\n")],
    # the bit shows in the read's bit 0 (spu_quiet) too
    'in_quiet_bit': [('{6\'d0, rd_seen, spu_quiet}', "{6'd0, rd_seen, spu_quiet | rd_seen}")],
}


def device_text(text):
    """psx_save_exp1.sv (a project's pinned copy, after save-mdec-tap) with rd_seen."""
    if TAG in text or 'rd_seen' in text:
        raise ValueError(f'{TAG}: already applied')
    if 'mdec_tap' not in text:
        raise ValueError(f'{TAG}: run after prepare_save_mdec_tap')
    for old, new in PAIRS:
        text = _once(text, old, new)
    if len(re.findall(r'\brd_seen\b', text)) != 4:
        raise ValueError(f'{TAG}: unexpected use count')
    return text


def restore(text):
    """The device text before the step (the inverse of device_text, exact)."""
    for old, new in PAIRS:
        text = _once(text, new, old)
    return text


def sdc_text(text):
    if TAG in text:
        raise ValueError(f'{TAG}: pocket.sdc already has the lines')
    if 'psx_memcard_slot:memcard_slot|req_addr_74[*]' not in text:
        raise ValueError(f"{TAG}: pocket.sdc has no psx_memcard_slot request lines")
    return text + SDC


def fault_text(text, fault):
    for old, new in FAULTS[fault]:
        text = _once(text, old, new)
    return text


def prepare(source, output, seed=None):
    """Snapshot SOURCE into OUTPUT, patch the device and pocket.sdc there."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_pocket_seeds import inputs
    from run_frozen_pocket_fit import digest, hold_guard, load_source, snapshot
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest = load_source(source, source / 'fit-inputs.json')
    rows = [r for r in manifest['quartus_sources'] if Path(r['path']).name == 'psx_save_exp1.sv']
    if len(rows) != 1:
        raise SystemExit(f'{TAG}: {source} does not compile one psx_save_exp1.sv')
    if seed is None:
        seed = int(re.search(r'(?m)^set_global_assignment -name SEED (\d+)$', (source / 'apf_top.qsf').read_text())[1])
    manifest = snapshot(source, source / 'fit-inputs.json', output, seed)
    before = inputs(output)
    device = next(Path(r['path']) for r in before if Path(r['path']).name == 'psx_save_exp1.sv')
    if digest(device) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: snapshot changed psx_save_exp1.sv')
    sdc = output / 'pocket.sdc'
    if str(sdc) not in {r['path'] for r in before}:
        raise SystemExit(f'{TAG}: {sdc} is not a fit input')
    changed = {}
    for path, transform in ((device, device_text), (sdc, sdc_text)):
        changed[str(path)] = digest(path)
        path.write_text(transform(path.read_text()))
    after = inputs(output)
    a, b = ({r['path']: r for r in rs} for rs in (after, before))
    if set(a) != set(b) or {p for p in b if a[p] != b[p]} != set(changed):
        raise SystemExit(f'{TAG}: expected psx_save_exp1.sv and pocket.sdc to change, nothing else')
    hold_guard(output, after)
    manifest['quartus_sources'] = after
    (output / 'fit-inputs.json').write_text(json.dumps(manifest, indent=2) + '\n')
    load_source(output, output / 'fit-inputs.json')
    if digest(Path(rows[0]['path'])) != rows[0]['sha256']:
        raise SystemExit(f'{TAG}: the source project changed')
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
            print(f'{TAG}: 1 register (rd_seen), read as bit 1 of 0x180C; 2 SDC lines; core_top unchanged')
    except ValueError as error:
        raise SystemExit(str(error))
