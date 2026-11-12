#!/usr/bin/env python3
"""Native ch3 SPU cache bench (sim/memory/tb_psx_spu_ch3_cache.sv).

Runs psx_spu_ch3_cache (critical word first, and the slim word-granular
psx_spu_ch3_cache_slim.sv) and the direct ch3 path through psx_sdram_ctrl (CAS3,
SPU_FIRST) and the chip model on the same SPU-like traffic with a busy ch1
client. Passes when every read matches the golden copy, the cache misses no
tick after the cold first one and spends fewer clk1x per tick than the
direct path, and a copy without the posted-write update of a cached line is
caught.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from run_sdram_ctrl import ALTDDIO
from tool_paths import ROOT, find_tool

CONTROLLER = ROOT/'rtl/memory/psx_sdram_ctrl.sv'   # --controller
OPEN_ROW = 0                                       # --open-row
CH3_PATH = None                                    # --core-memory: the directory of the ch3_path.svh in use

RESULT = re.compile(r'RESULT slot (\d+) cache (\d+) load (\d+) pitch (\d+) errors (\d+) late_ticks (\d+) mean (\d+) max (\d+) reads (\d+) hits (\d+)')


def run(work, name, cache_src, cache, ticks, pitch, slot=0, voice_sets=0, layout=0, write_every=1, xfer=0, xmode=0, post=0, may_hang=False, directed=0, gaps=40, load=1, seed=1, ch3_path=None):
    iverilog, vvp = find_tool('iverilog'), find_tool('vvp')
    sources = [work/'altddio_out.sv', CONTROLLER, ROOT/'sim/memory/sdram_chip_model.sv',
               cache_src, ROOT/'sim/memory/tb_psx_spu_ch3_cache.sv']
    out = work/f'{name}.vvp'
    ch3_path = ch3_path or CH3_PATH
    subprocess.run([iverilog, '-g2012', '-o', str(out), '-s', 'tb_psx_spu_ch3_cache', *(['-DCH3_PATH', f'-I{ch3_path}'] if ch3_path else []),
                    f'-Ptb_psx_spu_ch3_cache.CACHE={cache}', f'-Ptb_psx_spu_ch3_cache.TICKS={ticks}',
                    f'-Ptb_psx_spu_ch3_cache.PITCH={pitch}', f'-Ptb_psx_spu_ch3_cache.SLOT={slot}',
                    f'-Ptb_psx_spu_ch3_cache.VOICE_SETS={voice_sets}', f'-Ptb_psx_spu_ch3_cache.LAYOUT={layout}',
                    f'-Ptb_psx_spu_ch3_cache.WRITE_EVERY={write_every}', f'-Ptb_psx_spu_ch3_cache.XFER={xfer}',
                    f'-Ptb_psx_spu_ch3_cache.XMODE={xmode}', f'-Ptb_psx_spu_ch3_cache.POST_WRITE={post}', f'-Ptb_psx_spu_ch3_cache.DIRECTED={directed}',
                    f'-Ptb_psx_spu_ch3_cache.GAPS={gaps}', f'-Ptb_psx_spu_ch3_cache.OPEN_ROW={OPEN_ROW}', f'-Ptb_psx_spu_ch3_cache.LOAD={load}', f'-Ptb_psx_spu_ch3_cache.SEED={seed}',
                    *map(str, sources)], check=True)
    r = subprocess.run([vvp, '-n', str(out)], cwd=work, capture_output=True, text=True, timeout=3600)
    (work/f'{name}.log').write_text(r.stdout + r.stderr)
    m = RESULT.search(r.stdout)
    keys = ('slot', 'cache', 'load', 'pitch', 'errors', 'late_ticks', 'mean', 'max', 'reads', 'hits')
    if not m:
        # a planted fault may stop the traffic: the bench then ends on its timeout without a result
        if may_hang: return dict(dict.fromkeys(keys, 0), errors=1, no_result=1)
        sys.exit(f'{name}: no result (see {work}/{name}.log)')
    return dict(zip(keys, map(int, m.groups())))


# Not a fault in this bench (2026-11-11): psx_sdram_ctrl with CAS 3 registers a ch3 request's address, data, byte
# enables and direction on the clock of ch3_req, so it does not look at the cache's ch3 outputs afterwards.
FOLLOWS_PORT_IS_FAULT = False
NO_ROUND = 100000   # WRITE_EVERY that leaves only tick 0's write round

# VOICE_SETS=2 faults: (name, old text, new text, must be caught by wrong reads)
OWNER_FAULTS = (
    ('no-clear-on-write', '                if (VOICE_SETS==2) valid<=valid & ~own_sets[LINES-1:0];\n', '', True),
    ('fill-not-recorded', "owners[bucket]<=own_wr ? 16'd0 : own_q | own_bit;", "owners[bucket]<=own_wr ? 16'd0 : own_q;", True),
    ('one-owner-per-bucket', "owners[bucket]<=own_wr ? 16'd0 : own_q | own_bit;", "owners[bucket]<=own_wr ? 16'd0 : own_bit;", True),
    ('sets-16-to-30-not-cleared', 'assign own_sets={line_ok, own_q[14:0], own_q};', "assign own_sets={line_ok, 15'd0, own_q};", True),
    ('set-31-not-cleared', 'assign own_sets={line_ok, own_q[14:0], own_q};', "assign own_sets={1'b0, own_q[14:0], own_q};", True),
    ('write-looks-at-its-voice-set', 'isvoice && (VOICE_SETS!=2 || spu_rnw) ?', 'isvoice ?', True),
    ('wrong-set-bit', "own_bit=16'd1<<set_a[3:0];", "own_bit=16'd1<<(set_a[3:0]^4'd1);", True),
    ('entry-of-previous-request', '            own_q<=owners[bucket];', '            if (spu_done) own_q<=owners[bucket];', True),
    # not faults: the reads must still match
    ('bucket-kept-on-write', "owners[bucket]<=own_wr ? 16'd0 : own_q | own_bit;", "owners[bucket]<=own_wr ? own_q : own_q | own_bit;", False),
    ('table-starts-full', "owners[oi]=16'd0;", "owners[oi]=16'hffff;", False),
    ('zero-read-during-write', '            own_q<=owners[bucket];', "            own_q<=(own_fill || own_wr) ? 16'd0 : owners[bucket];", False),
)


def before(work, rev):
    """The slim cache as it was at a Git revision (or a copy in build/sim for a tree without Git)."""
    path = work/f'slim_{rev}.sv'
    r = subprocess.run(['git', '-C', str(ROOT), 'show', f'{rev}:rtl/pocket/psx_spu_ch3_cache_slim.sv'], capture_output=True, text=True)
    if r.returncode == 0: path.write_text(r.stdout)
    else: shutil.copy(ROOT/f'build/sim/slim_{rev}.sv', path)
    return path


# POST_WRITE faults (with VOICE_SETS=2); a fault that stops the traffic counts as caught
POST_FAULTS = (
    ('next-request-not-held', '                if (ch3_ready) eng<=E_IDLE;\n            end\n', '                if (ch3_ready || POST) eng<=E_IDLE;\n            end\n'),
    ('answered-every-clock', "                    spu_q<=0; spu_done<=1; wr_ack<=1;\n", "                    spu_q<=0; spu_done<=1;\n"),
    ('lines-cleared-when-ch3-answers', "        wire own_wr=wr_done && !discard && !reset;\n", "        wire own_wr=wr_done && !discard && !reset;\n        wire late_clr=eng==E_WRITE && ch3_ready;\n"),
)


def post_mutant(work, text, name, old_text, new_text):
    """The slim cache with one posted-write fault planted."""
    if text.count(old_text) != 1: sys.exit(f'posted-write fault anchor {name}')
    mutant = work/f'slim_post_{name}.sv'; mutant.write_text(text.replace(old_text, new_text))
    if name == 'lines-cleared-when-ch3-answers':   # the lines (not the table entry) are cleared late, at the address then on the port
        m = mutant.read_text(); a = '                    if (VOICE_SETS==2) valid<=valid & ~own_sets[LINES-1:0];\n'
        if m.count(a) != 1: sys.exit('posted-write fault anchor (late clear)')
        m = m.replace(a, '').replace('                if (ch3_ready) eng<=E_IDLE;\n            end\n',
                                       '                if (ch3_ready) begin eng<=E_IDLE; valid<=valid & ~own_sets[LINES-1:0]; end\n            end\n')
        mutant.write_text(m)
    return mutant


# Faults for the directed cases only: (name, old text, new text, must be caught by wrong reads)
DIRECTED_FAULTS = (
    # the posted write's lines stay valid
    ('posted-lines-not-cleared', '                    if (VOICE_SETS==2) valid<=valid & ~own_sets[LINES-1:0];\n', '', True),
    # the write on ch3 follows the SPU port while it is in flight (address, data and byte enables not held by the cache)
    ('posted-write-follows-the-port', '                if (ch3_ready) eng<=E_IDLE;\n            end\n',
     "                if (ch3_ready) eng<=E_IDLE;\n                else begin ch3_addr<=base+{8'b0,spu_addr}; ch3_din<={16'b0,spu_data[15:0]}; ch3_be<={2'b00,spu_be[1:0]}; end\n            end\n", FOLLOWS_PORT_IS_FAULT),
)
DIRECTED_CASES = ('read-same', 'read-line', 'read-region', 'write-read', 'read-back')
DIRECTED = re.compile(r'DIRECTED case (\S+) runs (\d+) errors (\d+) inflight (\d+)')
DIRECTED_WRITES = re.compile(r'DIRECTED writes (\d+) landed (\d+) clk1x_min (\d+) clk1x_max (\d+) gaps (\d+)')
DIRECTED_HIST = re.compile(r'DIRECTED write_clk1x (\d+) count (\d+)')


def directed_run(work, name, src, post=1, load=1, seed=1, may_hang=False, ch3_path=None):
    r = run(work, name, src, 1, 1, 1, 0, 2, post=post, directed=1, load=load, seed=seed, may_hang=may_hang, ch3_path=ch3_path)
    log = (work/f'{name}.log').read_text()
    r['cases'] = {m[0]: dict(runs=int(m[1]), errors=int(m[2]), inflight=int(m[3])) for m in DIRECTED.findall(log)}
    m = DIRECTED_WRITES.search(log)
    if m: r.update(zip(('writes', 'landed', 'write_clk1x_min', 'write_clk1x_max', 'gaps'), map(int, m.groups())))
    r['write_clk1x'] = {int(k): int(v) for k, v in DIRECTED_HIST.findall(log)}
    r['no_answer'] = 'ERROR no answer' in log
    return r


# The ch3 read-data register of a project's psx_core_memory.sv (--core-memory): these lines, each exactly once
CH3_REG = "    reg  [31:0] ch3_dout = 32'd0;\n    always @(negedge clk1x) ch3_dout <= ch3_dout_sdram;\n"
CH3_WIRING = ('.ch3_dout(ch3_dout_sdram)', '.ch3_ready(ch3_ready && !cpu_reset), .ch3_dout(ch3_dout));')
# Faults in that register: (name, replacement of its always line, must be caught by wrong reads)
CH3_FAULTS = (
    # the controller loads ch3_dout over two clk1x before its ready reaches the cache (measured 2026-11-11 on the
    # open-row controller: one and two clk1x of extra delay still read right, three do not), so these two are not faults
    ('ch3-register-rising-edge', "    always @(posedge clk1x) ch3_dout <= ch3_dout_sdram;\n", False),
    ('ch3-register-one-clk1x-late', "    reg [31:0] ch3_late = 32'd0;\n    always @(negedge clk1x) begin ch3_late <= ch3_dout_sdram; ch3_dout <= ch3_late; end\n", False),
    ('ch3-register-three-clk1x-late', "    reg [31:0] ch3_l1 = 32'd0, ch3_l2 = 32'd0, ch3_l3 = 32'd0;\n"
     "    always @(negedge clk1x) begin ch3_l1 <= ch3_dout_sdram; ch3_l2 <= ch3_l1; ch3_l3 <= ch3_l2; ch3_dout <= ch3_l3; end\n", True),
)


def ch3_path_of(work, core_memory):
    """ch3_path.svh: the register between the controller's ch3_dout and the cache, as the project's text has it."""
    text = core_memory.read_text()
    for anchor in (CH3_REG,) + CH3_WIRING:
        if text.count(anchor) != 1: sys.exit(f'{core_memory}: the ch3 read-data path is not the one this bench copies ({anchor.strip()[:50]})')
    (work/'ch3').mkdir(); (work/'ch3/ch3_path.svh').write_text(CH3_REG)
    return work/'ch3'


def ch3_faults(work, slim, report, checks):
    """The directed cases with a fault planted in the ch3 read-data register."""
    always = CH3_REG.split('\n', 1)[1]
    for name, new, fault in CH3_FAULTS:
        d = work/f'ch3-{name}'; d.mkdir(); (d/'ch3_path.svh').write_text(CH3_REG.replace(always, new))
        runs = {cfg: directed_run(work, f'slim-directed-{name}-{cfg}', slim, 1, load, 1, may_hang=True, ch3_path=d) for cfg, load in (('no-ch1-load', 0), ('ch1-load', 1))}
        report[f'slim_directed_{name}'] = runs
        wrong = sum(r['errors'] for r in runs.values())
        if fault: checks[f'ch3 read register fault caught by the directed cases: {name}'] = wrong > 0
        else: checks[f'ch3 read register, {name}: directed reads still match'] = wrong == 0


def directed(work, slim, report, checks):
    """POST_WRITE=1: the request after a posted write, at every distance from it (the bench's DIRECTED cases)."""
    configs = (('no-ch1-load', 0, 1), ('ch1-load', 1, 1))   # ch3 writes of 2-4 and of 2-8 clk1x (another ch1 seed gives the same clocks)
    for cfg, load, seed in configs:
        r = directed_run(work, f'slim-directed-{cfg}', slim, 1, load, seed)
        report[f'slim_directed_{cfg}'] = r
        for case in DIRECTED_CASES:
            c = r['cases'].get(case, dict(runs=0, errors=1, inflight=0))
            checks[f'posted writes, directed {case} ({cfg}): every read is the newest data'] = c['runs'] > 0 and c['errors'] == 0
            if case != 'read-back':
                checks[f'posted writes, directed {case} ({cfg}): requests arrive while the write is in flight'] = c['inflight'] > 0
        checks[f'posted writes, directed ({cfg}): the gaps reach past the longest posted write'] = r.get('gaps', 0) >= r.get('write_clk1x_max', 1000) + 8
        checks[f'posted writes, directed ({cfg}): every write reached SDRAM'] = r.get('writes', 1) == r.get('landed', 0)
    r = directed_run(work, 'slim-directed-held', slim, 0, 1, 1)
    report['slim_directed_held'] = r
    checks['held writes, directed cases: every read is the newest data'] = r['errors'] == 0 and len(r['cases']) == len(DIRECTED_CASES)
    text = slim.read_text()
    for name, old_text, new_text, fault in tuple(f + (True,) for f in POST_FAULTS) + DIRECTED_FAULTS:
        mutant = post_mutant(work, text, name, old_text, new_text)
        runs = {cfg: directed_run(work, f'slim-directed-{name}-{cfg}', mutant, 1, load, seed, may_hang=True) for cfg, load, seed in configs}
        report[f'slim_directed_{name}'] = runs
        wrong = sum(r['errors'] for r in runs.values())
        if fault: checks[f'posted write fault caught by the directed cases: {name}'] = wrong > 0
        else: checks[f'posted writes, {name}: directed reads still match (psx_sdram_ctrl holds the request)'] = wrong == 0
    if CH3_PATH: ch3_faults(work, slim, report, checks)


def post_write(work, slim, ticks, report, checks):
    """POST_WRITE=1: a write is answered one clock after it is taken; ch3 finishes it while the SPU goes on."""
    cases = (('a', 1, 1, 16, 0, 0), ('b', 4, 0, NO_ROUND, 1, 1), ('c', 4, 1, 1, 24, 1), ('d', 4, 2, 1, 0, 0), ('e', 4, 0, NO_ROUND, 24, 0))
    good = {}
    for c in cases:
        plain = run(work, f'slim-post0-case-{c[0]}', slim, 1, ticks, c[1], 0, 2, *c[2:])
        good[c[0]] = r = run(work, f'slim-post1-case-{c[0]}', slim, 1, ticks, c[1], 0, 2, *c[2:], post=1)
        report[f'slim_post_write_case_{c[0]}'] = dict(posted=r, held=plain)
        checks[f'posted writes, case {c[0]}: reads match golden'] = r['errors'] == 0
        checks[f'posted writes, case {c[0]}: same hits and no more clk1x than held writes'] = r['hits'] == plain['hits'] and r['mean'] <= plain['mean']
    # this bench has no SPU work between accesses, so the next request waits for most of the posted write:
    # the saving here is a lower bound (the budget bench with spu.vhd measures the real one)
    checks['posted writes, 24 transfer writes per tick: fewer clk1x per tick than held writes'] = (
        good['e']['mean'] < report['slim_post_write_case_e']['held']['mean'])
    # POST_WRITE=0 must be the cache as it was before this option (8a6e9deb)
    old = before(work, '8a6e9deb')
    for c in cases[1:3]:
        r = run(work, f'slim-8a6e9deb-case-{c[0]}', old, 1, ticks, c[1], 0, 2, *c[2:])
        checks[f'held writes, case {c[0]}: same clk1x and hits as the cache before posted writes'] = r == report[f'slim_post_write_case_{c[0]}']['held']
    text = slim.read_text()
    for name, old_text, new_text in POST_FAULTS:
        mutant = post_mutant(work, text, name, old_text, new_text)
        runs = {c[0]: run(work, f'slim-post-{name}-{c[0]}', mutant, 1, ticks, c[1], 0, 2, *c[2:], post=1, may_hang=True) for c in cases}
        report[f'slim_post_{name}'] = runs
        checks[f'posted write fault caught: {name}'] = sum(r['errors'] for r in runs.values()) > 0


def owner_table(work, slim, ticks, report, checks):
    """VOICE_SETS=2: a write clears only the sets its bucket lists."""
    for pitch in (1, 4):
        r = run(work, f'slim-layout1-vs2-p{pitch}', slim, 1, ticks, pitch, 0, 2, 1, 16)
        report[f'slim_layout1_voice_sets2_pitch{pitch}'] = r
        checks[f'owner table, colliding layout, pitch {pitch}: reads match golden, late only on the cold first tick'] = (
            r['errors'] == 0 and r['late_ticks'] <= 1)
    r = run(work, 'slim-layout1-vs2-writes', slim, 1, ticks, 4, 0, 2, 1, 1)
    report['slim_layout1_voice_sets2_writes_every_tick'] = r
    checks['owner table, writes every tick: reads match golden'] = r['errors'] == 0
    r = run(work, 'slim-layout0-vs2', slim, 1, ticks, 4, 0, 2, 0, 16)
    report['slim_layout0_voice_sets2'] = r
    checks['owner table, separate voices, pitch 4: reads match golden, no late tick'] = r['errors'] == 0 and r['late_ticks'] == 0
    # all voices in two of the table's 1 KiB regions: a write there clears many lines that do not hold its block
    r = run(work, 'slim-layout2-vs2-writes', slim, 1, ticks, 4, 0, 2, 2, 1)
    report['slim_layout2_voice_sets2_writes_every_tick'] = r
    checks['owner table, voices 64 bytes apart, writes every tick: reads match golden'] = r['errors'] == 0
    # Transfer writes: 1 or 24 halfwords per tick, away from the voices (XMODE 0) or around voice 23 (XMODE 1)
    for xfer in (1, 24):
        for xmode in (0, 1):
            got = {}
            for vs in (0, 1, 2):
                r = run(work, f'slim-xfer{xfer}-mode{xmode}-vs{vs}', slim, 1, ticks, 4, 0, vs, 0, NO_ROUND, xfer, xmode)
                report[f'slim_xfer{xfer}_mode{xmode}_voice_sets{vs}'] = got[vs] = r
                checks[f'{xfer} transfer writes per tick, mode {xmode}, voice sets {vs}: reads match golden'] = r['errors'] == 0
            checks[f'{xfer} transfer writes per tick, mode {xmode}: owner table keeps over twice the hits of the full clear'] = (
                got[2]['hits'] > 2 * got[1]['hits'])
            checks[f'{xfer} transfer writes per tick, mode {xmode}: owner table costs no more clk1x than the full clear or the hash sets'] = (
                got[2]['mean'] <= got[1]['mean'] and got[2]['mean'] <= got[0]['mean'])
            if xfer == 1:
                checks[f'1 transfer write per tick, mode {xmode}: owner table has no late tick'] = got[2]['late_ticks'] == 0
    # VOICE_SETS 0 and 1 must be the cache as it was before the owner table
    # (533c49b1, the copy in the 0.5.7 and 0.5.8 projects): same clk1x and hits
    older = before(work, '533c49b1')
    for vs in (0, 1):
        old = run(work, f'slim-before-layout1-vs{vs}', older, 1, ticks, 1, 0, vs, 1, 16)
        oldx = run(work, f'slim-before-xfer24-vs{vs}', older, 1, ticks, 4, 0, vs, 0, NO_ROUND, 24, 1)
        report[f'slim_before_owner_table_voice_sets{vs}'] = dict(layout1=old, xfer24=oldx)
        checks[f'voice sets {vs}: same clk1x and hits as the cache before the owner table'] = (
            old == report[f'slim_layout1_voice_sets{vs}_pitch1'] and oldx == report[f'slim_xfer24_mode1_voice_sets{vs}'])
    text = slim.read_text()
    cases = (('a', 1, 1, 16, 0, 0), ('b', 4, 0, NO_ROUND, 1, 1), ('c', 4, 1, 1, 24, 1), ('d', 4, 2, 1, 0, 0))   # name, pitch, layout, write_every, xfer, xmode
    good = {c[0]: run(work, f'slim-vs2-case-{c[0]}', slim, 1, ticks, c[1], 0, 2, *c[2:]) for c in cases}
    for c in cases:
        report[f'slim_voice_sets2_case_{c[0]}'] = good[c[0]]
        checks[f'owner table, fault case {c[0]}: reads match golden'] = good[c[0]]['errors'] == 0
    for name, old, new, fault in OWNER_FAULTS:
        if text.count(old) != 1: sys.exit(f'owner-table fault anchor {name}')
        mutant = work/f'slim_owner_{name}.sv'; mutant.write_text(text.replace(old, new))
        runs = {c[0]: run(work, f'slim-vs2-{name}-{c[0]}', mutant, 1, ticks, c[1], 0, 2, *c[2:]) for c in cases}
        report[f'slim_owner_{name}'] = dict(runs, hits_of_unchanged={k: good[k]['hits'] for k in good})
        wrong = sum(r['errors'] for r in runs.values())
        if fault: checks[f'owner table fault caught: {name}'] = wrong > 0
        else: checks[f'owner table, {name}: reads still match golden'] = wrong == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=120)
    parser.add_argument('--pitch', type=int, default=1, help='samples per tick per voice')
    parser.add_argument('--only', choices=('cwf', 'slim'), help='Run one cache design (direct runs always)')
    parser.add_argument('--post-write-only', action='store_true', help='Run only the POST_WRITE cases and faults')
    parser.add_argument('--directed-only', action='store_true', help='Run only the directed POST_WRITE cases and their faults')
    parser.add_argument('--controller', type=Path, help="Another psx_sdram_ctrl.sv (a project's copy) in place of rtl/memory's")
    parser.add_argument('--core-memory', type=Path, help="A project's psx_core_memory.sv: its ch3 read-data register goes between controller and cache")
    parser.add_argument('--open-row', type=int, default=0, help="The controller's OPEN_ROW (the release psx_core_memory instance: 1)")
    args = parser.parse_args()
    global CONTROLLER, OPEN_ROW, CH3_PATH
    if args.controller: CONTROLLER = args.controller.resolve()
    OPEN_ROW = args.open_row
    if not find_tool('iverilog'): sys.exit('Icarus Verilog not found (scripts/setup_iverilog.sh)')
    parent = ROOT/'build/sim/spu-ch3-cache'; parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='run-', dir=parent))
    (work/'altddio_out.sv').write_text(ALTDDIO)
    # Each cache with a planted fault: no update of a cached line on write.
    variants = {'cwf': (ROOT/'rtl/pocket/psx_spu_ch3_cache.sv', '                if (w_hit) begin\n', "                if (1'b0) begin\n"),
                'slim': (ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv', "eng==E_WRITE && !discard && hit && VOICE_SETS!=2;", "eng==E_WRITE && 1'b0;")}
    if args.core_memory: CH3_PATH = ch3_path_of(work, args.core_memory.resolve())
    report = dict(work=str(work), controller=str(CONTROLLER), open_row=OPEN_ROW, ch3_path=str(args.core_memory) if args.core_memory else None)
    checks = {}
    if args.post_write_only or args.directed_only:
        if args.post_write_only: post_write(work, ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv', args.ticks, report, checks)
        directed(work, ROOT/'rtl/pocket/psx_spu_ch3_cache_slim.sv', report, checks)
        print(json.dumps(report, indent=2))
        for name, ok in checks.items(): print(('PASS ' if ok else 'FAIL ') + name)
        sys.exit(0 if all(checks.values()) else 1)
    for slot in (0, 24):
        direct = run(work, f'direct-slot{slot}', variants['cwf'][0], 0, args.ticks, args.pitch, slot)
        report[f'direct_slot{slot}'] = direct
        checks[f'direct reads match golden (slot {slot})'] = direct['errors'] == 0
        for key, (source, stale, planted) in variants.items():
            if args.only and key != args.only: continue
            cache = run(work, f'{key}-slot{slot}', source, 1, args.ticks, args.pitch, slot)
            report[f'{key}_slot{slot}'] = cache
            checks[f'{key} reads match golden (slot {slot})'] = cache['errors'] == 0
            if slot == 0:   # the fixed voice slot (no --spu-free-slot) is reported only
                checks[f'{key} late only on the cold first tick'] = cache['late_ticks'] <= 1
                text = source.read_text()
                if text.count(stale) != 1: sys.exit(f'{key}: write-update anchor not found')
                mutant = work/f'{source.stem}_no_update.sv'
                mutant.write_text(text.replace(stale, planted))
                stale_run = run(work, f'{key}-no-update', mutant, 1, args.ticks, args.pitch, slot)
                report[f'{key}_no_update'] = stale_run
                checks[f'{key} missing write update is caught'] = stale_run['errors'] > 0
                checks[f'{key} cheaper per tick than direct without the slot'] = cache['mean'] < direct['mean']
                if key == 'slim':   # 4x pitch (the PSX maximum): a new word every tick
                    fast = run(work, 'slim-pitch4', source, 1, args.ticks, 4, slot)
                    report['slim_pitch4'] = fast
                    checks['slim at 4x pitch: no late tick, cheaper than direct'] = (
                        fast['errors'] == 0 and fast['late_ticks'] == 0 and fast['mean'] < direct['mean'])
    if args.only != 'cwf':
        # VOICE_SETS (prepare_spu_voice_sets.py): hash-colliding voice pairs and
        # one block shared by two voices, written now and then.
        slim = variants['slim'][0]
        for vs in (0, 1):
            for pitch in (1, 4):
                r = run(work, f'slim-layout1-vs{vs}-p{pitch}', slim, 1, args.ticks, pitch, 0, vs, 1, 16)
                report[f'slim_layout1_voice_sets{vs}_pitch{pitch}'] = r
                checks[f'slim layout 1 (colliding voices, shared block) reads match golden, voice sets {vs}, pitch {pitch}'] = r['errors'] == 0
                if vs: checks[f'voice sets, colliding layout, pitch {pitch}: late only on the cold first tick'] = r['late_ticks'] <= 1
        r = run(work, 'slim-layout1-vs1-writes', slim, 1, args.ticks, 4, 0, 1, 1, 1)
        report['slim_layout1_voice_sets1_writes_every_tick'] = r
        checks['voice sets, writes every tick (cache cleared each tick): reads match golden'] = r['errors'] == 0
        r = run(work, 'slim-layout0-vs1', slim, 1, args.ticks, 4, 0, 1, 0, 16)
        report['slim_layout0_voice_sets1'] = r
        checks['voice sets, separate voices, pitch 4: reads match golden, no late tick'] = r['errors'] == 0 and r['late_ticks'] == 0
        text = slim.read_text()
        for name, old, new in (('no-clear-on-write', '                if (VOICE_SETS==1) valid<=0;\n', ''),
                               ('isvoice-not-held', '    wire isvoice=spu_req ? spu_isvoice : isvoice_q;', '    wire isvoice=spu_isvoice;')):
            if text.count(old) != 1: sys.exit(f'voice-sets fault anchor {name}')
            mutant = work/f'slim_{name}.sv'; mutant.write_text(text.replace(old, new))
            r = run(work, f'slim-vs-{name}', mutant, 1, args.ticks, 1, 0, 1, 1, 16)
            report[f'slim_voice_sets_{name}'] = r
            good = report['slim_layout1_voice_sets1_pitch1']
            # a lost voice number misfiles every fill (no hits, data still right): caught by the hit count
            checks[f'voice sets fault caught: {name}'] = r['errors'] > 0 or r['hits'] < good['hits'] // 2
        owner_table(work, slim, args.ticks, report, checks)
        post_write(work, slim, args.ticks, report, checks)
        directed(work, slim, report, checks)
    print(json.dumps(report, indent=2))
    for name, ok in checks.items(): print(('PASS ' if ok else 'FAIL ') + name)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == '__main__':
    main()
