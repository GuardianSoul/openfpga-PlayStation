#!/usr/bin/env python3
"""SPU per-sample time against the 768-clk1x budget (sim/reference/tb_spu_budget.vhd).

Builds a project's spu.vhd (one with the audio probe's dbg_stash/dbg_busy ports,
e.g. rc057t14's audio-probe/spu.vhd) and runs it with all 24 voices on, reverb and
CD audio on, SPU RAM answering each request after LAT (+ random 0..JIT) clk1x.
Prints the longest busy run, the stash maximum and the dropped ticks per setting.
Usage: run_spu_budget.py PROJECT [--samples N] [--lat 3,8,13,...] [--jit N] [--pitch HEX]

Without more arguments SPU RAM is the direct path (no cache model). The cache
model and the traffic of docs/spu-voice-sets.md:
  --voice-sets 0|1|2   the slim cache with hash sets, voice sets, voice sets and the owner table
  --tag32              the model's line tag as it was until 2026-11-11 (32 bytes; the old tables)
  --random [SEED]      pseudo-random voice start addresses
  --writes SPEC        transfer writes: N (one upload of N halfwords), N/P (N every P samples), dma (sustained)
  --write-lat N        clk1x a write waits at ch3 (default: as a read)
  --write-voice V      the transfer starts at voice V's start block (--write-addr HEX: at that address register)
  --owners empty|full|long   the owner table at the start (--voice-sets 2)
  --post-writes        the cache's POST_WRITE (--voice-sets 2): a write is answered at once, ch3 finishes it meanwhile
  --reverb-base HEX    reverb work area register (CFF8: from 0x67FC0, partly in SDRAM)
  --stat-from N        first sample of the window figures and of the transfer (default 60)
  --extra '-gX=1 ...'  any other generic of the bench, passed last
  --fault NAME         run a copy of the bench with a planted fault in the owner table (must show as stale reads;
                       it needs writes into a block a voice holds: --writes 1/1 --write-voice V --stat-from 1)
After each BUDGET line a second line gives the window figures: hits, clk1x per
processed sample (mean/max), stash maximum, dropped ticks, lines cleared by
writes, stale reads. A stale read is a read answered from a line whose word was
written after the line took it: the run then exits 1, except with --fault,
where it exits 1 when the fault is NOT seen.
e.g. the doc's "1 per tick" row: PROJECT --samples 400 --lat 7,13 --random --voice-sets 2 --writes 1/1 --write-lat 5
"""
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

from run_reference_checks import spu_simulation_source
from tool_paths import ROOT, find_tool

UPSTREAM = ROOT / 'references/PSX_MiSTer'
BENCH = ROOT/'sim/reference/tb_spu_budget.vhd'
# Planted faults: (text in the bench, replacement). Each anchor must be found exactly once. Set 31's clear has no
# fault here: the bench has no traffic that reads a block through set 31, writes it and reads it again with no other
# set-31 read between (make test-spu-ch3-cache plants that fault on the cache itself).
FAULTS = {
    'no-clear': ('                                    pres(s) := "0000";   -- owners: a listed set loses its line\n', ''),
    'no-record': ("                                owners(to_integer(unsigned(adr(18 downto 10))))(set mod 16) := '1';   -- owners: a fill lists its set\n",
                  '                                null;\n'),
}


def generics(args):
    g = []
    if args.voice_sets is not None: g += ['-gCACHE=1', f'-gVIDX={args.voice_sets}']
    if args.tag32: g.append('-gTAG32=1')
    if args.random is not None: g += ['-gRANDOM=1', f'-gSEED={args.random}']
    if args.writes:
        if args.writes == 'dma': words, period = 100000, 100000
        else:
            words, _, period = args.writes.partition('/')
            words, period = int(words), int(period) if period else 100000
        g += [f'-gWR_WORDS={words}', f'-gWR_PERIOD={period}']
    if args.write_lat is not None: g.append(f'-gWLAT={args.write_lat}')
    if args.write_voice is not None: g.append(f'-gWR_VOICE={args.write_voice}')
    if args.write_addr is not None: g.append(f'-gWR_ADDR={int(args.write_addr, 16)}')
    if args.owners != 'empty': g.append(f"-gOWN_START={dict(full=1, long=2)[args.owners]}")
    if args.post_writes: g.append('-gPOST_WRITE=1')
    if args.reverb_base is not None: g.append(f'-gRVB_BASE={int(args.reverb_base, 16)}')
    if args.stat_from is not None: g.append(f'-gWR_START={args.stat_from}')
    return g + args.extra.split()


def summary(line):
    r = dict(re.findall(r'(\w+)=(-?\w+)', line))
    if 'win_runs' not in r: return None, 0
    n = lambda k: int(r[k])
    hits = f"{100 * n('win_hits') / n('win_reads'):.1f} % hits, " if n('win_reads') and n('CACHE') else ''
    mean = f"{n('win_busy') / n('win_runs'):.0f}" if n('win_runs') else '-'
    drop = f", first after sample {r['first_drop']}" if n('drops') else ''
    post = (f", {r['post_n']} posted writes, the next request waited {n('post_wait') / n('post_n'):.2f} clk1x (mean) / {r['post_wait_max']} (max)"
            if n('post_n') else '')
    return (f"  from sample {r['win_from']}: {hits}{mean}/{r['win_max']} clk1x per processed sample (mean/max), stash {r['max_stash']}, "
            f"dropped {r['win_drops']} (whole run {r['drops']}{drop}), {n('win_wreqs') / max(1, n('win_runs')):.2f} writes per sample, "
            f"lines cleared {r['cleared']}, stale reads {r['stale']}" + post), n('stale')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('project', type=Path)
    ap.add_argument('--samples', type=int, default=4000)
    ap.add_argument('--lat', default='3,8,13,20,30')
    ap.add_argument('--jit', type=int, default=0)
    ap.add_argument('--pitch', default='3FFF')
    ap.add_argument('--spu', type=Path, help='this spu.vhd instead of the project\'s')
    ap.add_argument('--extra', default='', help='more generics, space separated (e.g. -gCACHE=1 -gVIDX=1)')
    ap.add_argument('--voice-sets', type=int, choices=(0, 1, 2))
    ap.add_argument('--tag32', action='store_true')
    ap.add_argument('--random', type=int, nargs='?', const=1, metavar='SEED')
    ap.add_argument('--writes', metavar='N|N/P|dma')
    ap.add_argument('--write-lat', type=int)
    ap.add_argument('--write-voice', type=int)
    ap.add_argument('--write-addr', metavar='HEX')
    ap.add_argument('--owners', choices=('empty', 'full', 'long'), default='empty')
    ap.add_argument('--post-writes', action='store_true')
    ap.add_argument('--reverb-base', metavar='HEX')
    ap.add_argument('--stat-from', type=int)
    ap.add_argument('--fault', choices=sorted(FAULTS))
    ap.add_argument('--work', default='spu-budget', help='build directory under build/sim (one per concurrent run)')
    args = ap.parse_args()
    project = args.project.resolve(); q = (project/'apf_top.qsf').read_text()
    pick = lambda n: sorted(set(re.findall(rf'"([^"]*/{re.escape(n)})"', q)))[0]
    spu, sdp = str(args.spu.resolve()) if args.spu else pick('spu.vhd'), pick('pocket_dpram_sdp.vhd')
    if 'dbg_stash' not in Path(spu).read_text(): raise SystemExit('the project spu.vhd has no audio-probe ports')
    ghdl = find_tool('ghdl'); lib = Path(find_tool('quartus_sh')).parents[1]/'eda/sim_lib'
    d = ROOT/'build/sim'/(args.work + (f'-fault-{args.fault}' if args.fault else '')); d.mkdir(parents=True, exist_ok=True)
    bench = BENCH
    if args.fault:
        text = BENCH.read_text(); old, new = FAULTS[args.fault]
        if text.count(old) != 1: raise SystemExit(f'fault {args.fault}: anchor found {text.count(old)} times in {BENCH.name}')
        bench = d/'tb_spu_budget_fault.vhd'; bench.write_text(text.replace(old, new))
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    mem = UPSTREAM/'sim/system/src/mem'
    sources = [lib/'altera_mf_components.vhd', lib/'altera_mf.vhd', mem/'dpram.vhd', mem/'RamMLAB.vhd', mem/'SyncFifo.vhd',
               UPSTREAM/'rtl/SyncFifoFallThrough.vhd', UPSTREAM/'rtl/SyncFifoFallThroughMLAB.vhd', sdp,
               UPSTREAM/'rtl/spu_gauss.vhd', UPSTREAM/'rtl/spu_ram.vhd', spu, bench]
    stamp = hashlib.sha256()
    for f in sources: stamp.update(str(f).encode() + b'\0' + Path(f).read_bytes())
    stamp = stamp.hexdigest()
    # Analyse only when a source changed, so runs in one build directory can follow or overlap each other.
    if not ((d/'build.stamp').is_file() and (d/'build.stamp').read_text() == stamp):
        for f in [*d.glob('*.cf'), d/'build.stamp']: f.unlink(missing_ok=True)
        def call(*a):
            r = subprocess.run([ghdl, *a], cwd=d, capture_output=True, text=True, timeout=900)
            if r.returncode: raise SystemExit(f"ghdl {a[0]} failed:\n{(r.stdout + r.stderr)[-3000:]}")
        call('-a', *opts, '--work=altera_mf', str(lib/'altera_mf_components.vhd'), str(lib/'altera_mf.vhd'))
        for library in ('mem', 'work'):
            call('-a', *opts, f'--work={library}', str(mem/'dpram.vhd'), str(mem/'RamMLAB.vhd'))
        call('-a', *opts, '--work=mem', str(mem/'SyncFifo.vhd'), str(UPSTREAM/'rtl/SyncFifoFallThrough.vhd'),
             str(UPSTREAM/'rtl/SyncFifoFallThroughMLAB.vhd'), sdp)
        call('-a', *opts, str(UPSTREAM/'rtl/spu_gauss.vhd'), spu_simulation_source(UPSTREAM, d), spu, str(bench))
        call('-e', *opts, 'tb_spu_budget')
        (d/'build.stamp').write_text(stamp)
    stale_runs = missing = 0
    for lat in map(int, args.lat.split(',')):
        r = subprocess.run([ghdl, '-r', *opts, 'tb_spu_budget', f'-gOPERATIONS={args.samples}', f'-gLAT={lat}',
                            f'-gJIT={args.jit}', f'-gPITCH={int(args.pitch, 16)}', *generics(args), '--ieee-asserts=disable'],
                           cwd=d, capture_output=True, text=True, timeout=14400)
        m = re.search(r'BUDGET .*', r.stdout + r.stderr)
        print(m.group(0) if m else f'LAT={lat}: no result\n' + (r.stdout + r.stderr)[-1500:], flush=True)
        if not m: missing += 1; continue
        text, stale = summary(m.group(0))
        if text: print(text, flush=True)
        stale_runs += stale > 0
    runs = len(args.lat.split(','))
    if missing: sys.exit(f'{missing} of {runs} runs gave no result')
    if args.fault:
        if stale_runs: print(f'fault {args.fault}: seen as stale reads in {stale_runs} of {runs} runs')
        else: sys.exit(f'fault {args.fault}: NOT seen (no stale read in {runs} runs; it needs writes into a block a voice holds, e.g. --writes 1/1 --write-voice 7 --stat-from 1)')
    elif stale_runs: sys.exit(f'stale reads in {stale_runs} of {runs} runs: the cache model answered from a line written since it was filled')


if __name__ == '__main__':
    main()
