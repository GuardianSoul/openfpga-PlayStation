#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Save with a disc read in flight, load as a wake, the read completes: the project's cd_top in
sim/reference/tb_cd_save_read.vhd (GHDL, real delay constants); the save routine's cd_quiet and the
load routine's load_start / early-load / cd_resume played by the bench
(docs/save-states-finish/drive-state.md).

A case is CASE:ROUTINE[:key=value...] with keys of the bench's generics (DELAY, STALL, LSTALL, TARGET,
MODE, SSTAT, SMODE, SLBA, SFLAGS, NOSAVE, FAULT, RESTORE), e.g. 0:0:DELAY=2000.
Usage: run_cd_save_read.py --project PROJECT [--cd-top FILE] [--case C ...] [--jobs 3] [--run-only]
Prints one JSON line per case and writes report.json into --work.
"""
import argparse
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psx_disc_host import multitrack_metadata_words
from tool_paths import ROOT, find_tool

CASES = {0: 'SeekL complete, then ReadN: no sector yet', 1: 'Setloc + ReadN: no sector yet',
         2: 'reading, three sectors in', 3: 'reading, the next sector request not served',
         4: 'Setloc acked, no read yet (game sends ReadN after the load)',
         5: 'SeekL complete, ReadN, first sector request not served',
         6: 'reading, Pause complete (game sends ReadN after the load)',
         7: 'Setloc acked, the game sends ReadN at the save\'s 5th retry',
         8: 'reading, save, drive frozen with the Pocket silent (FREEZE clocks), the game goes on (no load)', 9: 'state given (hardware state replay)'}
KEYS = ('GAP', 'TOUT', 'RECOV', 'FREEZE', 'DELAY', 'STALL', 'LSTALL', 'TARGET', 'MODE', 'SSTAT', 'SMODE', 'SLBA', 'SFLAGS', 'NOSAVE', 'FAULT', 'RESTORE')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path)
    ap.add_argument('--cd-top', type=Path, help='cd_top.vhd to test instead of the project\'s')
    ap.add_argument('--case', action='append', default=[])
    ap.add_argument('--jobs', type=int, default=3)
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/cd-save-read')
    ap.add_argument('--sim-lib', type=Path, help='Quartus eda/sim_lib (altera_mf.vhd) when quartus_sh is not on the path')
    ap.add_argument('--run-only', action='store_true', help='the work directory is built (a mirror on the other host): run the cases')
    args = ap.parse_args()
    ghdl = find_tool('ghdl')
    w = args.work.resolve(); w.mkdir(parents=True, exist_ok=True)
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    base = ['--workdir=work', '-Paltlib', '-Pmemlib']
    if not args.run_only: build(args, ghdl, w, opts, base)
    if args.case: cases(args, ghdl, w, opts, base)


def sim_cd_top(text):
    """cd_top as the early-load bench simulates it (shorter motor counter, no audio output)."""
    for old, new in (("newCDCounter <= (others => '1');", "newCDCounter <= to_unsigned(4194303, 26);"),
                     ('std_logic_vector(to_unsigned(positionInIndex, 20))', 'std_logic_vector(to_signed(positionInIndex, 20))'),
                     ('goutput : if 1 = 1 generate', 'goutput : if 1 = 0 generate'),
                     ('goutputcdda : if 1 = 1 generate', 'goutputcdda : if 1 = 0 generate')):
        if old not in text: raise SystemExit(f'cd_top: no "{old}"')
        text = text.replace(old, new)
    return text


def build(args, ghdl, w, opts, base):
    q = find_tool('quartus_sh')
    lib = args.sim_lib or (Path(q).parents[1] / 'eda/sim_lib' if q else None)
    if not lib or not (lib / 'altera_mf.vhd').is_file(): raise SystemExit('no Quartus sim_lib: give --sim-lib DIR (altera_mf.vhd)')
    qsf = (args.project / 'apf_top.qsf').read_text()

    def assigned(name):
        hits = sorted({line.split('"')[1] for line in qsf.splitlines() if f'/{name}"' in line})
        if not hits: raise SystemExit(f'{name} not assigned in {args.project}')
        return hits[0]
    cd_top = args.cd_top or Path(assigned('cd_top.vhd'))
    deps = [assigned(n) for n in ('dpram.vhd', 'pocket_dpram_sdp.vhd', 'export.vhd', 'cd_xa_zigzag.vhd', 'cd_xa.vhd')]
    fifo = [assigned(n) for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]

    def run(cmd, timeout=900):
        r = subprocess.run(cmd, cwd=w, capture_output=True, text=True, timeout=timeout)
        if r.returncode: raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-3000:]}{r.stderr[-3000:]}')
        return r.stdout + r.stderr
    (w / 'metadata.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words([40000, 200], region=0)))
    src = w / 'src'; src.mkdir(exist_ok=True)

    def local(path):
        q = src / Path(path).name
        q.write_bytes(Path(path).read_bytes())
        return str(q)
    xa = src / 'cd_xa_sim.vhd'
    xa.write_text(re.sub(r'-- synthesis translate_off.*?-- synthesis translate_on', '', Path(deps[4]).read_text(), flags=re.S))
    deps = [local(x) for x in deps[:4]] + [str(xa)]
    fifo = [local(x) for x in fifo]
    (src / 'cd_top_sim.vhd').write_text(sim_cd_top(Path(cd_top).read_text()))
    tb = local(ROOT / 'sim/reference/tb_cd_save_read.vhd')
    alt = [local(lib / 'altera_mf_components.vhd'), local(lib / 'altera_mf.vhd')]
    if not (w / 'altlib/altera_mf-obj08.cf').exists():
        (w / 'altlib').mkdir(exist_ok=True)
        run([ghdl, '-a', *opts, '--work=altera_mf', '--workdir=altlib', *alt])
    (w / 'memlib').mkdir(exist_ok=True)
    run([ghdl, '-a', *opts, '--work=mem', '--workdir=memlib', '-Paltlib', *fifo, deps[0], deps[1]])
    d = w / 'work'; d.mkdir(exist_ok=True)
    for f in d.iterdir(): f.unlink()
    run([ghdl, '-a', *opts, *base, *deps, str(src / 'cd_top_sim.vhd'), tb])
    run([ghdl, '-e', *opts, *base, 'tb_cd_save_read'])


def parse(case):
    f = case.split(':')
    g = {'CASE_N': int(f[0]), 'ROUTINE': int(f[1])}
    for kv in f[2:]:
        k, v = kv.split('=')
        if k not in KEYS: raise SystemExit(f'unknown key {k}')
        g[k] = int(v, 0)
    return g


def cases(args, ghdl, w, opts, base):
    def one(case):
        g = parse(case)
        t = time.time()
        r = subprocess.run([ghdl, '-r', *opts, *base, 'tb_cd_save_read', *[f'-g{k}={v}' for k, v in g.items()], '--ieee-asserts=disable'],
                           cwd=w, capture_output=True, text=True, timeout=14400)
        m = re.search(r'SAVEREAD (\w+) (.*)', r.stdout + r.stderr)
        res = {'case': case, 'name': CASES.get(g['CASE_N'], '?'), 'seconds': round(time.time() - t, 1)}
        if m:
            res['verdict'] = m[1]
            res.update({k: int(v) for k, v in re.findall(r'(\w+)=(-?\d+)', m[2])})
        else:
            res['verdict'] = 'NORESULT'; res['tail'] = (r.stdout + r.stderr)[-400:]
        print(json.dumps(res), flush=True)
        return res
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(one, args.case))
    name = 'report.json'
    old = json.loads((w / name).read_text()) if (w / name).exists() else []
    keep = [r for r in old if r['case'] not in {x['case'] for x in results}]
    (w / name).write_text(json.dumps(keep + results, indent=1) + '\n')


if __name__ == '__main__':
    main()
