# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU bench of the routine step readout-hold (prepare_save_readout_hold_routine.py) for
run_save_project_cpu.py --routine-step readout-hold [--readout late|none|endless].

The routine's window is BENCH_ROUNDS rounds (bench only; the instruction count is the routine's).
The bench's device model gets rd_seen (bit 1 of the 0x180C read, cleared by a 0x180C write, a read
in the same cycle wins) and a Pocket: from READ_START cycles after a save's FINISH it reads the
save window once per READ_PACE cycles (1,313 clk1x = 2,880 clk_74a, the pace measured on hardware)
for READ_LEN cycles. Logged per request: "HOLD save|load exit after N cycles windows=K" when
SPU_HOLD falls. Rules in the bench: while the game is held after FINISH, SPU_HOLD is written only
as 1 (no tap bit), and once the routine has written another device register after FINISH (get_page's
PAGE_LO, PAGE_HI, COMMAND before restore: the wait is over) no further hold window comes; (the bench's own rules) no CD
register is touched while CD_FREEZE is set.
Expectations (expect()), W = the measured window (cycles between two clearing writes):
  late     reads from 5 W to 9.3 W after FINISH: the exit comes after the last read and at most
           2 W + 2,000 cycles after it
  none     no read: the exit after the no-read limit (30 windows), within one window
  endless  reads to the end: the exit after the limit (40 windows), within one window
  a load   exits less than half a window after its FINISH (no hold)
"""
import os
import re

import prepare_save_readout_hold_routine as step

FAULTS = step.FAULTS
fault_text = step.fault_text
PACE = 1313
W_NOMINAL = step.BENCH_ROUNDS * step.ROUND_CYCLES
READS = {
    'late': dict(READ_START=5 * W_NOMINAL, READ_LEN=4 * W_NOMINAL + W_NOMINAL // 3),
    'none': dict(READ_START=0, READ_LEN=0),
    'endless': dict(READ_START=W_NOMINAL // 2, READ_LEN=100 * W_NOMINAL),
}
mode = {'reads': 'late'}


def firmware_text(text):
    return step.bench_window(step.firmware_text(text))


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'readout-hold bench anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def bench_text(text):
    text = once(text, '      SEED       : positive := 1;\n',
                '      SEED       : positive := 1;\n'
                '      READ_START : natural  := 0;     -- readout-hold: the Pocket reads the save window from this many cycles after a save\'s FINISH (0: never)\n'
                '      READ_LEN   : natural  := 0;     -- ... for this many cycles, one read per READ_PACE\n'
                f'      READ_PACE  : positive := {PACE};\n')
    text = once(text, "   signal cd_freeze_s : std_logic := '0';  -- CD_FREEZE (0x180F): the CD model stands still\n",
                "   signal cd_freeze_s : std_logic := '0';  -- CD_FREEZE (0x180F): the CD model stands still\n"
                "   signal rd_seen_s : std_logic := '0';    -- readout-hold: a bridge read of the save window since the last SPU_HOLD write\n"
                "   signal hold_fin, hold_kind, hold_windows, hold_last, hold_w : natural := 0;   -- FINISH cycle (0: none), 1 save 2 load\n"
                "   signal hold_moved : natural := 0;       -- readout-hold: the routine wrote another device register after FINISH (the wait is over)\n")
    text = once(text, '            exp1_q <= "0000000" & spu_quiet;\n', '            exp1_q <= "000000" & rd_seen_s & spu_quiet;   -- readout-hold\n')
    text = once(text, '         a := to_integer(mux_bus_exp1_addr);\n',
                '         a := to_integer(mux_bus_exp1_addr);\n'
                '         -- readout-hold: the Pocket\'s reads of the save window\n'
                "         if at_cpu_edge and hold_kind = 1 and READ_START > 0 and cycle_count >= hold_fin + READ_START and\n"
                "            cycle_count < hold_fin + READ_START + READ_LEN and (cycle_count - hold_fin - READ_START) mod READ_PACE = 0 then\n"
                "            rd_seen_s <= '1';\n"
                '         end if;\n'
                "         if at_cpu_edge and ce = '1' and mux_bus_exp1_write = '1' and hold_kind /= 0 and a >= 16#1800# and a < 16#1840# then\n"
                "            if a /= 16#180C# then hold_moved <= 1; end if;   -- the wait is over: get_page and restore follow (PAGE_LO, PAGE_HI, COMMAND)\n"
                '         end if;\n')
    text = once(text, '               spu_hold <= mux_bus_exp1_dataWrite(0);\n',
                '               spu_hold <= mux_bus_exp1_dataWrite(0);\n'
                '               -- readout-hold: the write clears rd_seen unless the Pocket reads in this cycle\n'
                "               if not (hold_kind = 1 and READ_START > 0 and cycle_count >= hold_fin + READ_START and\n"
                "                       cycle_count < hold_fin + READ_START + READ_LEN and (cycle_count - hold_fin - READ_START) mod READ_PACE = 0) then\n"
                "                  rd_seen_s <= '0';\n"
                '               end if;\n'
                '               if hold_kind /= 0 then\n'
                "                  if mux_bus_exp1_dataWrite(0) = '0' then\n"
                '                     report "HOLD kind=" & integer\'image(hold_kind) & " exit after " & integer\'image(cycle_count - hold_fin) &\n'
                '                        " cycles windows=" & integer\'image(hold_windows) & " window_cycles=" & integer\'image(hold_w) severity note;\n'
                "                     hold_kind <= 0; hold_windows <= 0; hold_last <= 0; hold_moved <= 0;\n"
                '                  else\n'
                '                     assert mux_bus_exp1_dataWrite = x"01" report "SPU_HOLD written as " & to_hstring(mux_bus_exp1_dataWrite) & " while the game is held" severity failure;\n'
                "                     assert hold_moved = 0 report \"a hold window after the routine wrote another device register\" severity failure;\n"
                '                     hold_windows <= hold_windows + 1;\n'
                '                     if hold_last /= 0 then hold_w <= cycle_count - hold_last; end if;\n'
                '                     hold_last <= cycle_count;\n'
                '                  end if;\n'
                '               end if;\n')
    text = once(text, '               finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite));\n',
                '               finish_code <= to_integer(unsigned(mux_bus_exp1_dataWrite));\n'
                "               if mux_bus_exp1_dataWrite(0) = '1' and (want_save = '1' or want_load = '1') then   -- readout-hold\n"
                "                  hold_fin <= cycle_count; hold_moved <= 0;\n"
                "                  if want_save = '1' then hold_kind <= 1; else hold_kind <= 2; end if;\n"
                '               end if;\n')
    return text


def expect(rows, reads, saved, loaded):
    """HOLD rows [(kind, cycles, windows, window_cycles)] of one run -> failures."""
    found = []
    saves = [r for r in rows if r[0] == 1]
    loads = [r for r in rows if r[0] == 2]
    if len(saves) != saved or len(loads) != loaded:
        return [f'readout-hold: {len(saves)} save and {len(loads)} load exits logged, expected {saved} and {loaded}']
    plan = READS[reads]
    for _, cycles, windows, w in saves:
        w = w or W_NOMINAL
        total, noread = (step.numbers()[1], step.numbers()[1] - step.numbers()[2])
        if reads == 'late':
            end = plan['READ_START'] + (plan['READ_LEN'] - 1) // PACE * PACE
            if not end < cycles <= end + 2 * w + 2000:
                found.append(f'readout-hold: the save left {cycles} cycles after FINISH; the last read was at {end}, a window is {w}')
        else:
            n = noread if reads == 'none' else total
            if windows != n or not (n - 1) * w < cycles <= n * w + 2000:
                found.append(f'readout-hold: the save left after {windows} windows, {cycles} cycles; expected {n} windows of {w}')
    for _, cycles, windows, w in loads:
        if cycles >= W_NOMINAL // 2 or windows:
            found.append(f'readout-hold: a load was held {cycles} cycles ({windows} windows) after its FINISH')
    return found


def wrap_simulate(simulate):
    def wrapped(directory, generics, tag):
        generics = dict(generics)
        requests = 'SAVE_AT' in generics or 'LOAD_AT' in generics
        if 'SAVE_AT' in generics:
            generics.update(READS[mode['reads']])
        if 'MAX_CYCLES' in generics and requests:
            generics['MAX_CYCLES'] = int(generics['MAX_CYCLES']) + 45 * W_NOMINAL
        log = directory / 'checks.log'
        before = log.stat().st_size if log.exists() else 0
        ok, values, image, why = simulate(directory, generics, tag)
        text = log.read_text()[before:] if log.exists() else ''
        rows = [tuple(map(int, r)) for r in re.findall(r'HOLD kind=(\d) exit after (\d+) cycles windows=(\d+) window_cycles=(\d+)', text)]
        if requests:
            print(f"  readout-hold ({mode['reads']}): '{tag}': " + '; '.join(
                f"{'save' if k == 1 else 'load'} left {c} cycles after FINISH, {n} windows of {w}" for k, c, n, w in rows), flush=True)
        if ok and requests:
            found = expect(rows, mode['reads'], int(values.get('saves', 0)), int(values.get('loads', 0)))
            if found:
                return False, values, image, found[0]
        return ok, values, image, why
    return wrapped
