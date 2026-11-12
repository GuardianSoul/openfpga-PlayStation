# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU bench of the routine step readout-end (prepare_save_readout_end_routine.py) for
run_save_project_cpu.py --routine-step readout-hold --routine-step readout-end [--readout MODE].

Built on save_readout_hold_bench (its device model, its Pocket and its rules), plus:
  rd_last    0x180C bit 2: set by the read-out's last read (the Memory's last page) when the mode
             reads it; cleared with rd_seen by an SPU_HOLD write (a read in the same cycle wins).
  gap        READ_GAP cycles without reads from READ_GAP_AT on (the Pocket's pauses within a read-out).
  cue busy   0x180C bit 3: high for CUE_BUSY cycles from the save's CD_FREEZE write (a cue command in
             flight); a save's FINISH while it is high fails the run.
Modes (W = the bench window):
  late     reads 5 W .. 9.3 W after FINISH, the last page last: the exit after the last read, within
           one window and 2,000 cycles
  paused   reads 1 W .. 9 W with a gap of 3 W from 2 W on, the last page last: as late (the quiet-window
           rule would leave in the gap)
  none     no read: the exit at the no-read limit (30 windows)
  endless  reads to the end, never the last page: the exit at the limit (40 windows)
  cue      as late, and the cue reader busy for CUE (300,000) cycles from CD_FREEZE: FINISH after it
"""
import re

import prepare_save_readout_end_routine as step
import save_readout_hold_bench as rh

FAULTS = step.FAULTS
fault_text = step.fault_text
W = rh.W_NOMINAL
CUE = 300000
READS = {
    'late': dict(READ_START=5 * W, READ_LEN=4 * W + W // 3, LAST_PAGE='true'),
    'paused': dict(READ_START=1 * W, READ_LEN=8 * W, READ_GAP_AT=1 * W, READ_GAP=3 * W, LAST_PAGE='true'),
    'none': dict(READ_START=0, READ_LEN=0),
    'endless': dict(READ_START=W // 2, READ_LEN=100 * W),
    'cue': dict(READ_START=5 * W, READ_LEN=4 * W + W // 3, LAST_PAGE='true', CUE_BUSY=CUE),
}
mode = {'reads': 'late'}


def firmware_text(text):
    return step.firmware_text(text)


def _once(t, a, b, n=1):
    if t.count(a) != n: raise ValueError(f'readout-end bench anchor: {t.count(a)} matches (want {n}): {a[:80]!r}')
    return t.replace(a, b)


READ_COND = '(cycle_count - hold_fin - READ_START) mod READ_PACE = 0'


def bench_text(text):
    t = rh.bench_text(text)
    t = _once(t, f'      READ_PACE  : positive := {rh.PACE};\n',
              f'      READ_PACE  : positive := {rh.PACE};\n'
              '      READ_GAP_AT : natural := 0;    -- readout-end: no read for READ_GAP cycles from READ_GAP_AT on\n'
              '      READ_GAP   : natural  := 0;\n'
              '      LAST_PAGE  : boolean  := false;  -- readout-end: the read-out\'s last read is the Memory\'s last page\n'
              '      CUE_BUSY   : natural  := 0;      -- readout-end: the cue reader busy this long from CD_FREEZE\n')
    t = _once(t, "   signal rd_seen_s : std_logic := '0';",
              "   signal rd_seen_s : std_logic := '0';\n"
              "   signal rd_last_s, cue_busy_s : std_logic := '0';   -- readout-end: 0x180C bits 2 and 3\n"
              "   signal cue_until : natural := 0;")
    t = _once(t, '            exp1_q <= "000000" & rd_seen_s & spu_quiet;   -- readout-hold\n',
              '            exp1_q <= "0000" & cue_busy_s & rd_last_s & rd_seen_s & spu_quiet;   -- readout-hold, readout-end\n')
    t = _once(t, READ_COND, READ_COND + ' and (READ_GAP = 0 or cycle_count < hold_fin + READ_START + READ_GAP_AT or\n'
              '            cycle_count >= hold_fin + READ_START + READ_GAP_AT + READ_GAP)', n=2)
    t = _once(t, "            rd_seen_s <= '1';\n",
              "            rd_seen_s <= '1';\n"
              "            if LAST_PAGE and cycle_count + READ_PACE >= hold_fin + READ_START + READ_LEN then rd_last_s <= '1'; end if;   -- readout-end\n")
    t = _once(t, "                  rd_seen_s <= '0';\n", "                  rd_seen_s <= '0'; rd_last_s <= '0';\n")
    t = _once(t, "            if a = 16#180F# then cd_freeze_s <= mux_bus_exp1_dataWrite(0); end if;\n",
              "            if a = 16#180F# then cd_freeze_s <= mux_bus_exp1_dataWrite(0); end if;\n"
              "            if a = 16#180F# and mux_bus_exp1_dataWrite(0) = '1' and CUE_BUSY > 0 then cue_until <= cycle_count + CUE_BUSY; end if;   -- readout-end\n")
    t = _once(t, '               finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite));\n',
              '               finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite));\n'
              "               assert not (want_save = '1' and cue_busy_s = '1') report \"readout-end: a save's FINISH while the cue reader has a command in flight\" severity failure;\n"
              "               if want_save = '1' and CUE_BUSY > 0 then report \"CUE finish \" & integer'image(cycle_count) & \" busy until \" & integer'image(cue_until) severity note; end if;\n")
    t = _once(t, '\nbegin\n', "\nbegin\n   cue_busy_s <= '1' when cycle_count < cue_until else '0';   -- readout-end\n")
    return t


def expect(rows, reads, saved, loaded):
    found = []
    saves = [r for r in rows if r[0] == 1]
    loads = [r for r in rows if r[0] == 2]
    if len(saves) != saved or len(loads) != loaded:
        return [f'readout-end: {len(saves)} save and {len(loads)} load exits logged, expected {saved} and {loaded}']
    plan = READS[reads]
    total, left = rh.step.numbers()[1], rh.step.numbers()[2]
    for _, cycles, windows, w in saves:
        w = w or W
        if plan.get('LAST_PAGE'):
            end = plan['READ_START'] + (plan['READ_LEN'] - 1) // rh.PACE * rh.PACE
            if not end < cycles <= end + w + 2000:
                found.append(f'readout-end: the save left {cycles} cycles after FINISH; the last page was read at {end}, a window is {w}')
        else:
            n = total - left if reads == 'none' else total
            if windows != n or not (n - 1) * w < cycles <= n * w + 2000:
                found.append(f'readout-end: the save left after {windows} windows, {cycles} cycles; expected {n} windows of {w}')
    for _, cycles, windows, w in loads:
        if cycles >= W // 2 or windows:
            found.append(f'readout-end: a load was held {cycles} cycles ({windows} windows) after its FINISH')
    return found


def wrap_simulate(simulate):
    def wrapped(directory, generics, tag):
        generics = dict(generics)
        requests = 'SAVE_AT' in generics or 'LOAD_AT' in generics
        if 'SAVE_AT' in generics:
            generics.update(READS[mode['reads']])
        if 'MAX_CYCLES' in generics and requests:
            generics['MAX_CYCLES'] = int(generics['MAX_CYCLES']) + 45 * W
        log = directory / 'checks.log'
        before = log.stat().st_size if log.exists() else 0
        ok, values, image, why = simulate(directory, generics, tag)
        text = log.read_text()[before:] if log.exists() else ''
        rows = [tuple(map(int, r)) for r in re.findall(r'HOLD kind=(\d) exit after (\d+) cycles windows=(\d+) window_cycles=(\d+)', text)]
        cue = re.findall(r'CUE finish (\d+) busy until (\d+)', text)
        if requests:
            print(f"  readout-end ({mode['reads']}): '{tag}': " + '; '.join(
                f"{'save' if k == 1 else 'load'} left {c} cycles after FINISH, {n} windows of {w}" for k, c, n, w in rows)
                + (f'; FINISH at {cue[0][0]}, cue busy until {cue[0][1]}' if cue else ''), flush=True)
        if ok and requests:
            found = expect(rows, mode['reads'], int(values.get('saves', 0)), int(values.get('loads', 0)))
            if mode['reads'] == 'cue' and not cue:
                found.append('readout-end: the cue busy period was not used')
            if found:
                return False, values, image, found[0]
        return ok, values, image, why
    return wrapped
