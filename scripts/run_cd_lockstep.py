#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Clock-level lockstep of a candidate cd_top.vhd against a reference copy.

Both copies get the same simulation-only preparation (identical text
substitutions, so a candidate must keep these constants spelled the same):
 - long drive/work delays (>= 250,000 clocks: lid opening, motor start, pause
   rotation, seek limits), the new-disc window and the READSPEED constants
   are divided by 16 so a
   run reaches reads and seeks in millions instead of hundreds of millions of
   clocks;
 - the save-state readback of the signed positionInIndex uses to_signed
   (GHDL range-checks to_unsigned; synthesis truncates the same bits; the
   reset-only build does not use ss_out).
GHDL (native VHDL, Quartus altera_mf simulation library) runs
sim/reference/tb_cd_lockstep.vhd for each seed: every cd_top output is compared
on every clk1x edge. A planted one-clock command-timing fault must be caught.
Usage: run_cd_lockstep.py --reference REF.vhd --candidate CAND.vhd --project PROJECT
       [--seeds 1 2 3] [--cycles N] [--no-fault]
"""
import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psx_disc_host import multitrack_metadata_words
from tool_paths import ROOT, find_tool

# One data track and one audio track (Play, CDDA position reports).
TRACKS = [40, 200]
SPEED_EXEMPT = {'134217727', '16777215', '33554431', '524287', '524288'}


def sim_text(text):
    text = text.replace('std_logic_vector(to_unsigned(positionInIndex, 20))',
                        'std_logic_vector(to_signed(positionInIndex, 20))')
    # the 2 s "new disc" window (2**26 clocks), also divided by 16
    text = text.replace("newCDCounter <= (others => '1');", "newCDCounter <= to_unsigned(4194303, 26);")
    text = re.sub(r'(constant READSPEED\dX\s*: integer := 44100 \* 16#300# / )(\d+);',
                  lambda m: f'{m[1]}({m[2]} * 16);', text)
    out = []
    for line in text.split('\n'):
        if 'range' not in line and 'x"' not in line and '16#' not in line:
            line = re.sub(r'(?<![\w#"])(\d{6,})(?![\w#"])',
                          lambda m: m[1] if m[1] in SPEED_EXEMPT or int(m[1]) < 250000 else str(int(m[1]) // 16), line)
        out.append(line)
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reference', type=Path, required=True)
    ap.add_argument('--candidate', type=Path, required=True)
    ap.add_argument('--project', type=Path, required=True, help='frozen project with the other CD sources')
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/cd-lockstep')
    ap.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3])
    ap.add_argument('--cycles', type=int, default=6000000)
    ap.add_argument('--no-fault', action='store_true')
    ap.add_argument('--extra-fault', nargs=2, metavar=('OLD', 'NEW'), help='also plant OLD->NEW in the candidate; must be caught')
    args = ap.parse_args()
    ghdl = find_tool('ghdl')
    lib = Path(find_tool('quartus_sh')).parents[1] / 'eda/sim_lib'
    w = args.work.resolve()
    w.mkdir(parents=True, exist_ok=True)
    qsf = (args.project / 'apf_top.qsf').read_text()

    def assigned(name):
        hits = sorted({line.split('"')[1] for line in qsf.splitlines() if f'/{name}"' in line})
        if len(hits) < 1:
            raise SystemExit(f'{name} not assigned in {args.project}')
        return hits[0]
    deps = [assigned(n) for n in ('dpram.vhd', 'pocket_dpram_sdp.vhd', 'export.vhd', 'cd_xa_zigzag.vhd', 'cd_xa.vhd')]
    fifo = [assigned(n) for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    opts = ['--std=08', '-frelaxed', '-fsynopsys']

    def run(cmd, cwd=w, timeout=600):
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-3000:]}{r.stderr[-3000:]}')
        return r.stdout + r.stderr

    if not (w / 'altlib/altera_mf-obj08.cf').exists():
        (w / 'altlib').mkdir(exist_ok=True)
        run([ghdl, '-a', *opts, '--work=altera_mf', '--workdir=altlib', lib / 'altera_mf_components.vhd', lib / 'altera_mf.vhd'])
    for name in ('memlib', 'ref'):
        d = w / name
        if d.exists():
            for f in d.iterdir(): f.unlink()
        d.mkdir(exist_ok=True)
    run([ghdl, '-a', *opts, '--work=mem', '--workdir=memlib', '-Paltlib', *fifo, deps[0], deps[1]])
    (w / 'ref_sim.vhd').write_text(sim_text(args.reference.read_text()))
    (w / 'cand_sim.vhd').write_text(sim_text(args.candidate.read_text()))
    base = ['-Paltlib', '-Pmemlib']
    run([ghdl, '-a', *opts, '--work=ref', '--workdir=ref', *base, *deps, 'ref_sim.vhd'])
    (w / 'metadata.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words(TRACKS, region=2)))
    tb = ROOT / 'sim/reference/tb_cd_lockstep.vhd'

    def build(label, source):
        d = w / f'work-{label}'
        if d.exists():
            for f in d.iterdir(): f.unlink()
        d.mkdir(exist_ok=True)
        run([ghdl, '-a', *opts, f'--workdir={d.name}', '-Pref', *base, *deps, source, tb])
        run([ghdl, '-e', *opts, f'--workdir={d.name}', '-Pref', *base, 'tb_cd_lockstep'])
        return d.name

    def sim(dirname, seed):
        out = subprocess.run([ghdl, '-r', *opts, f'--workdir={dirname}', '-Pref', *base, 'tb_cd_lockstep',
                              f'-gSEED={seed}', f'-gRUN_CYCLES={args.cycles}', f'-gSECTORS={sum(TRACKS)}', '--ieee-asserts=disable'],
                             cwd=w, capture_output=True, text=True, timeout=7200)
        text = out.stdout + out.stderr
        m = re.search(r'LOCKSTEP (PASS.*|MISMATCH.*)', text)
        return (m[1] if m else 'NO RESULT ' + text[-500:]).strip()

    cand = build('cand', 'cand_sim.vhd')
    jobs = [(cand, s) for s in args.seeds]
    if not args.no_fault:
        fault = (w / 'cand_sim.vhd').read_text()
        old = 'cmd_delay  <= 25000 - 2;'
        if fault.count(old) != 1:
            raise SystemExit('fault anchor missing in candidate')
        (w / 'fault_sim.vhd').write_text(fault.replace(old, 'cmd_delay  <= 25000 - 3;'))
        jobs.append((build('fault', 'fault_sim.vhd'), args.seeds[0]))
    if args.extra_fault:
        text = (w / 'cand_sim.vhd').read_text()
        if text.count(args.extra_fault[0]) != 1:
            raise SystemExit('extra fault anchor not unique in candidate')
        (w / 'fault2_sim.vhd').write_text(text.replace(*args.extra_fault))
        jobs.append((build('fault2', 'fault2_sim.vhd'), args.seeds[0]))
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        results = list(pool.map(lambda j: sim(*j), jobs))
    ok = True
    for (d, s), r in zip(jobs, results):
        is_fault = 'fault' in d
        good = r.startswith('MISMATCH') if is_fault else r.startswith('PASS')
        if not is_fault and good:
            m = re.search(r'irqs (\d+) sectors (\d+)', r)
            good = bool(m) and int(m[1]) > 20 and int(m[2]) > 10
        ok &= good
        print(f'{"fault" if is_fault else "seed " + str(s)}: {r[:300]} -> {"ok" if good else "FAIL"}', flush=True)
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
