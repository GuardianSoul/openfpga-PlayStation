#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Memory card 1 and the save-state buffer through the whole aux chain (Icarus).

sim/pocket/tb_card_chain.sv chains the four real modules as the save-state
core_top wires them: psx_memcard_slot (SAVE_BASE 0x30000000) -> psx_save_exp1
(up_* in, page copy engine on the same aux port) -> psx_vram_x3 ->
psx_vram_psram (SAVE_AUX 1, FULL_RATE 1, LATENCY 6, CLK_KHZ 101606, SLOW_DIV 3,
OUT_REG 1) -> two psx_cram_model chips. One run does four cases:

  1 a 128 KiB card image ("MC" header, fixed seed) written through the bridge
    window 0x20000000 while parked; read back through the bridge after unpark
  2 32,768 save-window words (first and last 64 KiB of 0x30000000..0x303FFFFF)
    written while parked, as the Pocket writes a state on a wake
  3 running: page copies in the save device (both directions, page 0, page
    2047, random pages) while the bridge reads and writes card words
  4 running: sequential bridge reads of the save window with card reads and
    writes in between and page copies going on
  6 running: save-window uploads at exactly 88 clk_74a per transaction (and
    88..96): 3,328 consecutive words at the start, the middle and the end of
    the buffer for each VRAM traffic level 0..4 (0 none, 1 random mix with
    gaps, 2 128-word reads back to back, 3 128-word write streams back to
    back, 4 both alternating with no idle clock), which covers 65 KiB of each
    region; then with a card access after about every fourth word and page
    copies running. One LEVEL line per level: dropped transactions, overflow,
    aux latency (slot request to done) max and mean in clk2x
  7 the same with save-window reads (2,048 words per region and level)
  8 16 pages stored by page copies, read out through the bridge, overwritten,
    uploaded again through the bridge under level-4 traffic, fetched by page
    copies and compared
  Cases 3 and 4 run GPU-style read bursts and write streams on the DDR port.
  The slot's sticky overflow output is checked in every case.

Every case ends with a compare of both chips, all modelled words, against a
golden copy that takes every write the bench issues.

Planted faults (--fault NAME, or all of them after the good run): each is a
text mutation of a copy under build/sim/card-chain/ and must make the bench
FAIL. --project DIR takes the four chained modules a frozen project compiles:
DIR/save-exp1-pinned/psx_save_exp1.sv and the slot, psx_vram_x3 and
psx_vram_psram files DIR/apf_top.qsf names (--slot-rtl etc. name them one by
one). A slot with the overflow_clear input gets it tied low, as core_top does.

  python3 scripts/run_card_chain.py --project build/ss/ce
  python3 scripts/run_card_chain.py --no-faults
  python3 scripts/run_card_chain.py --fault c-park-die0
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from tool_paths import ROOT, find_tool

OUT = ROOT / 'build/sim/card-chain'
SLOT = ROOT / 'rtl/pocket/psx_memcard_slot.sv'
SAVE = ROOT / 'rtl/pocket/psx_save_exp1.sv'
X3 = ROOT / 'rtl/memory/psx_vram_x3.sv'
PSRAM = ROOT / 'rtl/memory/psx_vram_psram.sv'
MODEL = ROOT / 'sim/pocket/psx_cram_model.sv'   # compiled as a copy that includes card_chain_windows.svh
BENCH = [ROOT / 'sim/pocket/tb_card_chain.sv',
         ROOT / 'sim/reference/mister_memory_models.sv']   # dpram_dif (the staging page)

# name: (module, [(before, after)], what it is)
FAULTS = {
    'a-slot-window-bit': ('save', [
        ("assign aux_addr = own_req ? {1'b1, page_hi, page_lo, cp_word} : up_addr;",
         "assign aux_addr = own_req ? {1'b1, page_hi, page_lo, cp_word} : (up_addr & 21'h0FFFFF);")],
        'save device drops aux_addr[20] of the slot\'s save-window words'),
    'a-copy-window-bit': ('save', [
        ("assign aux_addr = own_req ? {1'b1, page_hi, page_lo, cp_word} : up_addr;",
         "assign aux_addr = own_req ? {1'b0, page_hi, page_lo, cp_word} : up_addr;")],
        'save device drops aux_addr[20] of the copy engine\'s words'),
    'b-both-granted': ('save', [
        ("wire own_start = cp_st == CP_AUX && !own_req && !up_go && !up_start;",
         "wire own_start = cp_st == CP_AUX && !own_req && !up_go;")],
        'arbiter: a copy word starts although a card request starts in the same clock; the copy word '
        'goes out and its done and data also answer the card request'),
    'b-done-to-card': ('save', [
        ("assign up_done  = up_go && aux_done;", "assign up_done  = aux_done;")],
        'arbiter: every aux_done, also a copy word\'s, answers the card side'),
    # psx_vram_psram before commit 1838edfc: a parked access always goes to card space on die 0
    'c-park-die0': ('psram', [
        ("p_ce_n <= aux_adr[21] ? 2'b01 : 2'b10; p_adv_n <= 1'b0; p_oe <= 1'b1; p_bm_n <= 4'h0;",
         "p_ce_n <= 2'b10; p_adv_n <= 1'b0; p_oe <= 1'b1; p_bm_n <= 4'h0;"),
        ("p_a <= aux_adr[20:15]; p_dq0 <= {aux_adr[14:0], aux_addr[0]}; p_dq1 <= {aux_adr[14:0], aux_addr[0]};\n            end\n        end\n\n        // slot 0",
         "p_a <= 6'd4; p_dq0 <= {1'b0, aux_addr[14:0]}; p_dq1 <= {1'b0, aux_addr[14:0]};\n            end\n        end\n\n        // slot 0")],
        'controller as before 1838edfc: parked aux accesses use die 0 card addressing whatever aux_addr[20] says'),
}

COVERS = [
    'the four real modules chained with core_top\'s port connections, resets (x3 and controller on !cpu_locked, '
    'slot and save device on mc_reset2x[1]) and park = core_held = !reset_n',
    'case 1: all 32,768 card words written through bridge window 0x20000000 while parked, each at die 0 halfword '
    '0x40000 + word on both chips, no other modelled word changed, die 1 never selected; whole card read back '
    'through the bridge with the core running',
    'case 2: 32,768 save-window words (save words 0..0x3FFF and 0xFC000..0xFFFFF: byte addresses whose low 17 bits '
    'cover every card address) written while parked: all on die 1, card range on die 0 unchanged, die 0 never '
    'selected during the writes, card_write never pulsed; 512 parked reads of both windows',
    'case 3: page copies by the save device (staging page <-> buffer page, pages 0, 2047 and random pages of the '
    'first and last 128 KiB) while the bridge reads and writes random card words at 88 clk_74a spacing plus random '
    'gaps; every card read checked, every copy checked word for word, done pulses counted at the slot, the save '
    'device, both sides of psx_vram_x3; card range changes only by the bridge writes',
    'case 4: sequential bridge reads of the save window with card reads/writes in between and page copies going on; '
    'same checks',
    'cases 6 and 7: bridge writes and reads of the save window at exactly 88 clk_74a spacing and at 88..96, at the '
    'start, middle and end of the 4 MiB buffer, for VRAM traffic levels 0..4 up to back-to-back 128-word reads and '
    'write streams with no idle clock, without and with card accesses and page copies; every word checked on die 1 '
    'or as returned, slot overflow and dropped strobes per level, aux latency max and mean per level',
    'case 8: save by page copies, bridge read-out, overwrite, bridge upload under level-4 traffic, load by page copies',
    'the slot\'s overflow output in every case',
    'cases 3 and 4: GPU-style DDR read bursts (4-128 words, checked) and write streams on the controller\'s main port',
    'the PSRAM pin protocol and chip timing as far as psx_cram_model checks them (board profile x3-slow-seed1)',
    'probe, not gating: bridge card reads at 88 clk_74a spacing across 12 park/unpark transitions (wrong answers and '
    'slot overflow are reported, not judged)',
    'measured: slot aux latency, bridge read strobe-to-data, how long aux_dout stays unchanged after the slot takes '
    'a read answer (the copy engine also starts aux reads), park and unpark times',
]
NOT_COVERED = [
    'the guest\'s SIO memory card protocol and the path from the card device to PSRAM (DDR card writes, card_write)',
    'the real Pocket firmware: its transaction spacing (88 clk_74a is the documented minimum), ordering, when it '
    'loads slots relative to reset, and bridge accesses during a park or unpark transition (the bench waits for the '
    'controller to be parked or idle; the transition times are reported)',
    'the gating in core_top around the chain: memcard_load_done (boot_disc_gate), mc_busy into the boot gate, the '
    'bridge read mux for other windows, the APF savestate_start/load handshake and core_held',
    'the CPU-side EXP1 bus timing (memorymux), the save routine itself, the ROM, the SPU RAM and SPU savestate '
    'modes of the copy engine, FINISH/ACK/trap',
    'a format (card without "MC"): the image has the magic, so the slot never formats here',
    'reset (PLL unlock) in the middle of traffic; more than one seed or clk_74a phase unless given',
    'save-buffer pages outside the model\'s windows (the first, middle and last 128 KiB are modelled: pages 0..63, '
    '992..1055, 1984..2047): an access elsewhere stops the chip model; a full 4 MiB state is not transferred',
    'DDR bursts longer than 128 words, DDR reads of die 1 or of the card window, and DDR traffic patterns of real games',
    'a bridge read of a save page while the copy engine writes that same page (the result would depend on timing)',
    'FPGA timing: clk1x/clk2x/clk3x are ideal aligned clocks, clk_74a is asynchronous at 74.25 MHz',
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def mutate(src, edits, name):
    text = src.read_text()
    for before, after in edits:
        if text.count(before) != 1:
            sys.exit(f'{name}: line to mutate found {text.count(before)} times in {src}')
        text = text.replace(before, after)
    out = OUT / f'{name}-{src.name}'
    out.write_text(text)
    return out


def run(name, save, psram, plus, slot=None, x3=None, max_load=28):
    slot = slot or SLOT
    x3 = x3 or X3
    iverilog = find_tool('iverilog')
    vvp = str(Path(iverilog).with_name('vvp'))
    exe = OUT / f'{name}.vvp'
    cmd = [iverilog, '-g2012', '-DPSX_M10K_WRITE_HALF', '-I', str(ROOT / 'sim/pocket'), '-s', 'tb_card_chain', '-o', str(exe)]
    if 'disc_set_tgl' in save.read_text():
        cmd.append('-DSAVE_DISC_SET')
    if 'overflow_clear' in slot.read_text():
        cmd.append('-DSLOT_OVERFLOW_CLEAR')
    model = OUT / 'psx_cram_model.sv'
    text = MODEL.read_text()
    if text.count('`include "psx_vram_psram_windows.svh"') != 1:
        sys.exit('chip model: windows include not found')
    model.write_text(text.replace('`include "psx_vram_psram_windows.svh"', '`include "card_chain_windows.svh"'))
    cmd += [str(slot), str(save), str(x3), str(psram), str(model)] + [str(b) for b in BENCH]
    build = subprocess.run(cmd, capture_output=True, text=True)
    if build.returncode:
        sys.exit(f'{name}: compile failed\n{build.stdout}{build.stderr}')
    while os.getloadavg()[0] >= max_load:   # AGENTS.md: start no simulation on a machine this busy
        time.sleep(20)
    t0 = time.time()
    r = subprocess.run(['nice', '-n', '10', vvp, '-n', str(exe)] + [f'+{k}={v}' for k, v in plus.items()],
                       capture_output=True, text=True, timeout=6 * 3600)
    wall = time.time() - t0
    out = r.stdout + r.stderr
    (OUT / f'{name}.log').write_text(out)
    cases = {}
    levels = [dict(case=int(m.group(1)), result=m.group(2), **json.loads(m.group(3)))
              for m in re.finditer(r'^LEVEL (\d) (PASS|FAIL) (\{.*\})$', out, re.M)]
    for m in re.finditer(r'^CASE (\d) (PASS|FAIL) (\{.*\})$', out, re.M):
        cases[m.group(1)] = dict(result=m.group(2), **json.loads(m.group(3)))
    verdict = re.findall(r'^CARD_CHAIN (PASS|FAIL)$', out, re.M)
    probe = re.search(r'^PROBE park-transitions (\{.*\})$', out, re.M)
    passed = verdict == ['PASS'] and r.returncode == 0 and all(c['result'] == 'PASS' for c in cases.values()) \
        and len(cases) == 7
    errors = [l for l in out.splitlines() if l.startswith('ERROR') or ' FAIL ' in l and not l.startswith('CASE')][:8]
    return dict(name=name, passed=passed, wall_s=round(wall, 1), cases=cases, levels=levels, errors=errors,
                park_transition_probe=json.loads(probe.group(1)) if probe else None,
                log=str((OUT / f'{name}.log').relative_to(ROOT)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path, help='prepared project: its save-exp1-pinned/psx_save_exp1.sv is the save device')
    ap.add_argument('--save-rtl', type=Path, default=SAVE)
    ap.add_argument('--slot-rtl', type=Path, default=SLOT)
    ap.add_argument('--x3-rtl', type=Path, default=X3)
    ap.add_argument('--psram-rtl', type=Path, default=PSRAM)
    ap.add_argument('--max-load', type=float, default=28, help='wait while the 1-minute load is at or above this')
    ap.add_argument('--fault', action='append', choices=sorted(FAULTS), help='run only this planted fault (repeatable)')
    ap.add_argument('--no-faults', action='store_true', help='the good run only')
    ap.add_argument('--no-good', action='store_true', help='skip the good run')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--phase74', type=int, default=0, help='clk_74a start offset, ps')
    ap.add_argument('--copies', type=int, default=40, help='page copies in case 3')
    ap.add_argument('--readout', type=int, default=8192, help='save-window words read in case 4')
    ap.add_argument('--smoke', action='store_true', help='bench debugging: 2,048 card words, 2 x 1,024 save words, 8 copies')
    ap.add_argument('--name', default='report', help='report file name (build/sim/card-chain/NAME.json)')
    a = ap.parse_args()
    os.environ['PATH'] = os.pathsep.join([str(ROOT / 'build/tools/iverilog/bin'), str(ROOT / 'build/tools/oss-cad-suite/bin'),
                                          os.environ['PATH']])
    OUT.mkdir(parents=True, exist_ok=True)
    save_src, slot_src, x3_src, psram_src = a.save_rtl, a.slot_rtl, a.x3_rtl, a.psram_rtl
    if a.project:
        # the files the frozen project compiles (apf_top.qsf); the save device is its pinned copy
        save_src = a.project / 'save-exp1-pinned/psx_save_exp1.sv'
        qsf = (a.project / 'apf_top.qsf').read_text()
        found = {}
        for name in ('psx_memcard_slot.sv', 'psx_vram_x3.sv', 'psx_vram_psram.sv'):
            m = re.findall(r'SYSTEMVERILOG_FILE "([^"]*/' + re.escape(name) + ')"', qsf)
            if len(m) != 1:
                sys.exit(f'{name}: {len(m)} entries in {a.project}/apf_top.qsf')
            found[name] = Path(m[0])
        slot_src, x3_src, psram_src = found['psx_memcard_slot.sv'], found['psx_vram_x3.sv'], found['psx_vram_psram.sv']
    save = OUT / 'psx_save_exp1.sv'
    save.write_text(save_src.read_text())   # every run compiles a copy under build/
    slot, x3, psram = OUT / 'psx_memcard_slot.sv', OUT / 'psx_vram_x3.sv', OUT / 'psx_vram_psram.sv'
    for src, dst in ((slot_src, slot), (x3_src, x3), (psram_src, psram)):
        dst.write_text(src.read_text())
    plus = dict(SEED=a.seed, PHASE74=a.phase74, N3=a.copies, N4=a.readout)
    if a.smoke:
        plus.update(CARD_N=2048, SAVE_N=1024, N3=8, N4=1024, UP_N=256, RB_N=256)
        a.name = 'smoke'
    report = dict(bench='sim/pocket/tb_card_chain.sv', smoke=a.smoke, plusargs=plus, seed=a.seed, phase74_ps=a.phase74, page_copies_case3=a.copies,
                  save_words_read_case4=a.readout,
                  sources={str(p): sha(p) for p in (slot_src, x3_src, psram_src, save_src)},
                  covers=COVERS, not_covered=NOT_COVERED, good=None, faults={})
    ok = True
    if not a.no_good:
        good = run('good', save, psram, plus, slot, x3, a.max_load)
        report['good'] = good
        ok &= good['passed']
        for n, c in sorted(good['cases'].items()):
            print(f"case {n} {c['result']} " + json.dumps({k: v for k, v in c.items() if k != 'result'}))
        for lv in good['levels']:
            print(f"level case {lv['case']} {lv['result']} " + json.dumps({k: v for k, v in lv.items() if k not in ('case', 'result')}))
        for e in good['errors']:
            print('  ' + e)
        if good['park_transition_probe']:
            print('probe (not gating) park transitions ' + json.dumps(good['park_transition_probe']))
        print(f"{'PASS' if good['passed'] else 'FAIL'} card chain, good run, {good['wall_s']} s")
    names = [] if a.no_faults else (a.fault or sorted(FAULTS))
    for name in names:
        module, edits, what = FAULTS[name]
        m_save = mutate(save, edits, name) if module == 'save' else save
        m_psram = mutate(psram, edits, name) if module == 'psram' else psram
        res = run(name, m_save, m_psram, plus, slot, x3, a.max_load)
        failed_cases = [n for n, c in sorted(res['cases'].items()) if c['result'] == 'FAIL']
        caught = not res['passed']
        res.update(what=what, caught=caught, first_failing_case=failed_cases[0] if failed_cases else None)
        report['faults'][name] = res
        ok &= caught
        print(f"fault {name}: {'caught' if caught else 'NOT CAUGHT'}"
              + (f" in case {failed_cases[0]}" if failed_cases else '') + f", {res['wall_s']} s"
              + (f": {res['errors'][0]}" if res['errors'] else ''))
    report['passed'] = bool(ok)
    (OUT / f'{a.name}.json').write_text(json.dumps(report, indent=1) + '\n')
    print(f"{'PASS' if ok else 'FAIL'} card chain"
          + ('' if a.no_good else ', good run') + (f', {len(names)} planted faults' if names else '')
          + f"; report {(OUT / (a.name + '.json')).relative_to(ROOT)}")
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
