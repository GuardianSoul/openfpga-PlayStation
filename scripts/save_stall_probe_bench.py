#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""CPU bench support for the EXPERIMENT routine "stall-probe" (scripts/prepare_save_stall_probe.py).

Used by run_save_project_cpu.py --routine-step stall-probe (last, after early-load):
  firmware_text   the probe with PROBE_SHIFT = BENCH_SHIFT (the ROM's 2^17 rounds a unit would be
                  hours of simulation); the code is the same but for that shift amount
  bench_text      sim/reference/tb_staged_save_cpu.vhd with
                    SAVES / SAVE_GAP: after a save's FINISH, once the game runs again, the next
                    save request follows SAVE_GAP cycles later, until SAVES were asked for;
                    a save that stored page 0 alone counts as a probe (saves= counts real saves,
                    so the load of LOAD_DELAY waits for the real one) and must report 2,048 pages;
                    "PROBE save n busy=..." per save: clk1x from the request to FINISH
  wrap_simulate   runs with a save ask for PROBES + 1 saves; the busy times are printed with the
                  rounds each probe ran, and checked: probes 1..PROBES have magic 0, size 2,048,
                  busy times that follow the table (cycles per round from the longest probe,
                  every probe within TOLERANCE cycles of it), and the last save is a real one.
"""
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_save_stall_probe as step

FAULTS = step.FAULTS
fault_text = step.fault_text
BENCH_SHIFT = 5
TOLERANCE = 64          # clk1x: EXP1 access alignment differs a little from request to request
SAVE_GAP = 400          # the bench's game is short: all saves and the load must fit before it ends
rows_seen = []


def firmware_text(text):
    return step.firmware_text(text, shift=BENCH_SHIFT)


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f'stall-probe bench anchor: expected one match, found {text.count(old)}: {old[:80]!r}')
    return text.replace(old, new)


def bench_text(text):
    text = once(text, '      SAVE_AT    : natural  := 0;\n',
                '      SAVE_AT    : natural  := 0;\n'
                '      SAVES      : natural  := 1;     -- stall-probe bench: save requests in all\n'
                '      SAVE_GAP   : natural  := 2000;  -- stall-probe bench: cycles the game runs before the next one\n')
    text = once(text, '      variable size_pages, stored : natural := 0;\n',
                '      variable size_pages, stored : natural := 0;\n'
                '      variable probe_req, save_n, next_save : natural := 0;   -- stall-probe bench\n'
                '      variable again, real_save : boolean := false;\n')
    text = once(text, '                  assert size_pages = stored report "save size "',
                '                  assert size_pages = stored or (stored = 1 and size_pages = 2048) report "save size "')
    text = once(text, "               stored := 0;\n               if want_save = '1' then\n"
                "                  if mux_bus_exp1_dataWrite(0) = '1' then saves_done <= saves_done + 1;\n",
                "               if want_save = '1' then\n"
                "                  save_n := save_n + 1; again := save_n < SAVES; real_save := stored > 1;\n"
                "                  report \"PROBE save \" & integer'image(save_n) & \" busy=\" & integer'image(cycle_count - probe_req) &\n"
                "                     \" stored=\" & integer'image(stored) & \" size=\" & integer'image(size_pages) & \" magic=\" & to_hstring(buf(0)) &\n"
                "                     \" finish=\" & integer'image(to_integer(unsigned(mux_bus_exp1_dataWrite))) severity note;\n"
                "               end if;\n"
                "               stored := 0;\n               if want_save = '1' then\n"
                "                  if mux_bus_exp1_dataWrite(0) = '1' then if real_save then saves_done <= saves_done + 1; end if;\n")
    text = once(text, "            if SAVE_AT > 0 and cycle_count = SAVE_AT then\n"
                "               assert want_save = '0' and want_load = '0' report \"save request while busy\" severity failure;\n",
                "            if again and want_save = '0' and cpu_cpu_done = '1' and\n"
                "               cpu_cpu_export.pc(31 downto 21) = to_unsigned(16#400#, 11) then\n"
                "               next_save := cycle_count + SAVE_GAP; again := false;\n"
                "            end if;\n"
                "            if (SAVE_AT > 0 and cycle_count = SAVE_AT) or (next_save > 0 and cycle_count = next_save) then\n"
                "               probe_req := cycle_count; next_save := 0;\n"
                "               assert want_save = '0' and want_load = '0' report \"save request while busy\" severity failure;\n")
    return text


def probe_rows(log_text):
    return [dict(save=int(m[1]), busy=int(m[2]), stored=int(m[3]), size=int(m[4]), magic=m[5], finish=int(m[6]))
            for m in re.finditer(r'PROBE save (\d+) busy=(\d+) stored=(\d+) size=(\d+) magic=([0-9A-F]+) finish=(\d+)', log_text)]


def problems(rows, shift=BENCH_SHIFT, cold=True):
    """What is wrong with a run's PROBE lines (empty: nothing), and the calibration."""
    found = []
    if len(rows) != step.PROBES + 1:
        return [f'{len(rows)} saves, expected {step.PROBES + 1}'], {}
    probes, real = rows[:step.PROBES], rows[step.PROBES]
    for r in probes:
        if (r['stored'], r['size'], int(r['magic'], 16), r['finish']) != (1, 2048, 0, 1):
            found.append(f"save {r['save']}: not a no-op save with magic 0 and 2,048 pages: {r}")
    if real['stored'] <= 1 or real['size'] != real['stored'] or real['finish'] != 1 or int(real['magic'], 16) == 0:
        found.append(f'save {real["save"]}: not a real save: {real}')
    base = probes[1]['busy']                                   # save 2: table byte 0, stage 2 in place
    n = lambda save: step.rounds(save, shift) - 1
    per_round = (probes[-1]['busy'] - base) / n(step.PROBES)
    for r in probes[1:]:
        r['rounds'] = n(r['save']) + 1
        want = base + n(r['save']) * per_round
        if abs(r['busy'] - want) > TOLERANCE:
            found.append(f"save {r['save']}: busy {r['busy']} clk1x, the table gives {want:.0f}")
    if not 1.5 <= per_round <= 16:
        found.append(f'{per_round:.3f} clk1x a round')
    if cold and probes[0]['busy'] <= base:
        found.append('save 1 (with the unpack) was not longer than save 2')
    return found, dict(fixed_cycles=base, unpack_cycles=probes[0]['busy'] - base, round_cycles=per_round)


def wrap_simulate(simulate):
    def wrapped(directory, generics, tag):
        generics = dict(generics)
        saving = 'SAVE_AT' in generics
        if saving:
            generics['SAVES'] = step.PROBES + 1
            generics['SAVE_GAP'] = SAVE_GAP
        log = Path(directory) / 'checks.log'
        before = log.stat().st_size if log.exists() else 0
        ok, values, image, why = simulate(directory, generics, tag)
        if saving:
            with log.open() as handle:
                handle.seek(before)
                rows = probe_rows(handle.read())
            found, calibration = problems(rows, cold='STAGE2_WARM' not in generics)
            for r in rows:
                print(f'    stall-probe: {r}', flush=True)
            print(f'    stall-probe calibration: {calibration}', flush=True)
            rows_seen.append(dict(tag=tag, rows=rows, calibration=calibration, problems=found))
            if found and ok:
                ok, why = False, 'stall-probe: ' + '; '.join(found)
        return ok, values, image, why
    return wrapped
