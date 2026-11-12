#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lockstep check of the 0.5.6 cd_top base trims (rtl/pocket/base_trims_056).

The reference is the selected rc1i cd_top.vhd; the candidate is that file with
cd_sectorword.patch then cd_copycount.patch applied (they do not overlap; each
must also apply alone). Both get run_cd_lockstep.py's
simulation preparation (sim_text) and run in sim/reference/tb_cd_lockstep.vhd,
which compares every cd_top output on every clk1x edge, in three stimulus modes:
  plain  - the original random command traffic (ReadN/ReadS, Play + CD-DA reports, ...)
  pause  - plus PAUSE_READ (Pause mid-sector while reading or playing)
  xa     - plus PAUSE_READ and XA_TEST (XA ADPCM sectors read with xa_enable and
           unit volumes, reads in track 2's pregap, GetlocP/GetlocL in between)
The reference copy carries "COV" reports (simulation only) that count the paths
the trims touch, so a run says whether it reached them. Planted mutations of
the new logic run in the xa mode and must each be caught (a lockstep mismatch).
Usage: run_base_trim_cd.py --project RC1I_PROJECT [--seeds 1 2 3 4] [--cycles N]
       [--ghdl PATH] [--jobs N] [--modes plain pause xa] [--no-mutations]
       run_base_trim_cd.py --project RC1I_PROJECT --area   (Yosys area screen only; yowasp-yosys)
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from psx_disc_host import multitrack_metadata_words
from run_cd_lockstep import TRACKS, sim_text
from tool_paths import ROOT, find_tool

# BASE_TRIM_PATCHES / BASE_TRIM_CD: another patch set and list (comma-separated)
TRIMS = Path(os.environ.get('BASE_TRIM_PATCHES', ROOT / 'rtl/pocket/base_trims_056'))
PATCHES = os.environ.get('BASE_TRIM_CD', 'cd_sectorword.patch,cd_copycount.patch').split(',')
MODES = {'plain': (0, 0), 'pause': (1, 0), 'xa': (1, 1)}   # PAUSE_READ, XA_TEST

# (name, trim, old, new): planted in the patched candidate, each must be caught.
MUTATIONS = [
    ('sectorword: register loaded only in SPROC_XA', 'cd_sectorword',
     "         sectorBuffers_DataA <= sectorBuffer_DataB;\n\n",
     "         if (sectorProcessState = SPROC_XA) then sectorBuffers_DataA <= sectorBuffer_DataB; end if;\n\n"),
    ('sectorword: register held during SPROC_XA (XA path only)', 'cd_sectorword',
     "         sectorBuffers_DataA <= sectorBuffer_DataB;\n\n",
     "         if (sectorProcessState /= SPROC_XA) then sectorBuffers_DataA <= sectorBuffer_DataB; end if;\n\n"),
    ('sectorword: register held during SPROC_CDDA (CD-DA path only)', 'cd_sectorword',
     "         sectorBuffers_DataA <= sectorBuffer_DataB;\n\n",
     "         if (sectorProcessState /= SPROC_CDDA) then sectorBuffers_DataA <= sectorBuffer_DataB; end if;\n\n"),
    ('copycount: end compare copySize - 1 (one word short)', 'cd_copycount',
     "if (copyReadAddr = copySize) then", "if (copyReadAddr = copySize - 1) then"),
    ('copycount: end compare ignores copySize (always 588)', 'cd_copycount',
     "if (copyReadAddr = copySize) then", "if (copyReadAddr = 588) then"),
    ('standard: one-clock command timing', 'all',
     'cmd_delay  <= 25000 - 2;', 'cmd_delay  <= 25000 - 3;'),
]

# Simulation-only path counters, planted in the reference copy only.
COVERAGE = [
    ("                  XA_write <= '1';\n",
     "                  if (procCount = 0) then report \"COV xa_sector\"; end if;\n"),
    ("                     XA_start            <= '1';\n", "                     report \"COV xa_start\";\n"),
    ("                  CDDA_write <= '1';\n",
     "                  if (procCount = 0) then report \"COV cdda_sector\"; end if;\n"),
    ("                  sectorBuffers_wrenA <= '1';\n                  if (procCount = (procSize - 1)) then\n",
     "                  if (procCount = 0) then report \"COV data_sector\"; end if;\n"),
    ("                  readSubchannelState  <= SSUB_CALCPOS;\n",
     "                  if (subchannelSector - startLBA = PREGAPSIZE) then report \"COV sub_d_eq_150\"; end if;\n"
     "                  if (subchannelSector - startLBA < PREGAPSIZE) then report \"COV sub_pregap\";\n"
     "                  elsif (isAudio = '1') then report \"COV sub_index1_audio\";\n"
     "                  else report \"COV sub_index1_data\"; end if;\n"),
    ("                           copyState  <= COPY_IDLE;\n                        end if;\n",
     "                           report \"COV copy_end_\" & integer'image(copySize);\n"),
    ("                  copyState   <= COPY_IDLE;\n                  FifoData_Wr <= '0';\n",
     "                  report \"COV copy_abort\";\n"),
    ("         if (XA_eof = '1') then\n", "            report \"COV xa_eof\";\n"),
    ("               lastreportCDDA     <= '0' & nextSubdata(9)(7 downto 4);\n",
     "               report \"COV cdda_report\";\n"),
    ("                     when x\"11\" => -- GetLocP\n", "                        report \"COV getlocp\";\n"),
]


def instrument(text):
    for anchor, line in COVERAGE:
        if text.count(anchor) != 1:
            raise SystemExit(f'coverage anchor not unique: {anchor!r}')
        # a report goes after the anchor's first line (inside its branch)
        first, rest = anchor.split('\n', 1)
        text = text.replace(anchor, first + '\n' + line + rest)
    return text


AREA_CELLS = ('MISTRAL_ALUT2', 'MISTRAL_ALUT3', 'MISTRAL_ALUT4', 'MISTRAL_ALUT5', 'MISTRAL_ALUT6',
              'MISTRAL_ALUT_ARITH', 'MISTRAL_FF', 'MISTRAL_M10K')


def area_screen(ghdl, text, sources, w):
    """Yosys synth_intel_alm cell counts of cd_top alone (GHDL --synth to Verilog;
    the FIFOs, RAM wrappers and cd_xa stay black boxes with their exact port
    widths, so every cd_top register that feeds them is kept). An estimate:
    Quartus maps, packs and shares differently."""
    w.mkdir(parents=True, exist_ok=True)
    for f in w.iterdir():
        if f.is_file():
            f.unlink()
    boxes = {'SyncFifoFallThrough': 'mem', 'pocket_dpram_sdp': 'mem', 'dpram': 'mem', 'cd_xa': 'work'}
    comps, modes = '', {}
    for name, lib in boxes.items():
        m = re.search(rf'(?is)\bentity\s+{name}\s+is(.*?)\bend(\s+entity)?(\s+{name})?\s*;', sources[name].read_text())
        comps += f'   component {name} is{m[1]}   end component;\n'
        for pm in re.finditer(r'(?im)^\s*([\w\s,]+?)\s*:\s*(in|out|inout)\b', m[1]):
            for p in pm[1].split(','):
                modes[(name, p.strip().lower())] = pm[2].lower()
        text = re.sub(rf'\bentity {lib}\.{name}\b', name, text)
    text = text.replace('library mem;\n', '', 1)
    text = text.replace('architecture arch of cd_top is\n', 'architecture arch of cd_top is\n' + comps, 1)
    (w / 'cd_top_synth.vhd').write_text(text)
    o = ['--std=08', '-frelaxed', '-fsynopsys', f'--workdir={w}']
    subprocess.run([ghdl, '-a', *o, sources['export'], w / 'cd_top_synth.vhd'], check=True, capture_output=True)
    r = subprocess.run([ghdl, '--synth', *o, '--out=verilog', 'cd_top'], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'ghdl --synth failed:\n{r.stderr[-3000:]}')
    v = r.stdout
    decl = {}
    for m in re.finditer(r'(?m)^\s*\(?(?:wire|reg|input|output)\s*(?:\[(\d+):(\d+)\])?\s*(\\\S+ |\w+)\s*[;,=)]', v):
        decl[m[3].strip()] = int(m[1]) - int(m[2]) + 1 if m[1] else 1

    def width(e):
        e = e.strip()
        if not e:
            return 1
        c = re.fullmatch(r"(\d+)'b[01]+", e)
        if c:
            return int(c[1])
        s = re.fullmatch(r'(\\?\S+?)\s*\[(\d+):(\d+)\]', e)
        if s:
            return int(s[2]) - int(s[3]) + 1
        if e in decl:
            return decl[e]
        raise SystemExit(f'area screen: width of {e!r} unknown')
    stubs = []

    def box(m):
        mod, inst, ports = m[1], m[3], m[4]
        plist = [f'  {"input" if modes[(mod, pm[1].lower())] == "in" else "output"} [{width(pm[2]) - 1}:0] {pm[1]}'
                 for pm in re.finditer(r'\.(\w+)\((.*?)\)(?=,\s*\n|\s*$)', ports, re.S)]
        stubs.append(f'(* blackbox *)\nmodule bb_{inst} (\n' + ',\n'.join(plist) + ');\nendmodule\n')
        return f'  bb_{inst} {inst} ({ports});'
    v = re.sub(r'(?ms)^  (' + '|'.join(boxes) + r') (#\(.*?\)\)\s*)?(\w+) \((.*?)\);', box, v)
    if len(stubs) != 6:
        raise SystemExit(f'area screen: expected 6 black boxes, found {len(stubs)}')
    (w / 'cd_top_bb.v').write_text(v + '\n' + '\n'.join(stubs))
    r = subprocess.run(['yowasp-yosys', '-q', '-p', 'read_verilog cd_top_bb.v; synth_intel_alm -family cyclonev -top cd_top; '
                        'tee -q -o stat.txt stat'], cwd=w, capture_output=True, text=True, timeout=3600)
    if r.returncode:
        raise SystemExit(f'yosys failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
    stat = (w / 'stat.txt').read_text()
    cells = {c: int(m[1]) if (m := re.search(rf'(\d+)\s+{c}\n', stat)) else 0 for c in AREA_CELLS}
    cells['ALUT'] = sum(n for c, n in cells.items() if c.startswith('MISTRAL_ALUT'))
    # word-level inventory before LUT mapping: deterministic, unlike ABC's LUT counts
    subprocess.run(['yowasp-yosys', '-q', '-p', 'read_verilog cd_top_bb.v; hierarchy -top cd_top; proc; opt; wreduce; '
                    'alumacc; opt -full; wreduce; opt_clean; share; opt_clean; tee -q -o coarse.txt stat -width'],
                   cwd=w, capture_output=True, text=True, timeout=3600, check=True)
    bits = Counter()
    for m in re.finditer(r'(\d+)\s+\$(\w+?)_(\d+)\n', (w / 'coarse.txt').read_text()):
        kind = 'ff' if 'dff' in m[2] else 'alu' if m[2] in ('alu', 'macc') else \
            'compare' if m[2] in ('eq', 'ne', 'lt', 'le', 'gt', 'ge') else 'mux' if 'mux' in m[2] else None
        if kind:
            bits[kind] += int(m[1]) * int(m[3])
    cells['words'] = bits
    return cells


def apply_patches(original, names):
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 'cd_top.vhd'
        f.write_text(original)
        for n in names:
            r = subprocess.run(['patch', '-s', '-F0', '-p1', '-d', d, '-i', str(TRIMS / n)],
                               capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f'{n} does not apply after {names[:names.index(n)]}: {r.stdout}{r.stderr}')
        return f.read_text()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path, required=True, help='rc1i project directory (apf_top.qsf)')
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/base-trim-cd')
    ap.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3, 4])
    ap.add_argument('--modes', nargs='+', default=list(MODES), choices=list(MODES))
    ap.add_argument('--cycles', type=int, default=6000000)
    ap.add_argument('--ghdl', default=find_tool('ghdl'))
    ap.add_argument('--jobs', type=int, default=os.cpu_count())
    ap.add_argument('--no-mutations', action='store_true')
    ap.add_argument('--area', action='store_true', help='only the Yosys area screen (rc1i, each patch, all three)')
    args = ap.parse_args()
    if not args.ghdl:
        raise SystemExit('GHDL not found (pass --ghdl)')
    w = args.work.resolve()
    w.mkdir(parents=True, exist_ok=True)
    proj = args.project.resolve()
    qsf = (proj / 'apf_top.qsf').read_text()

    def assigned(name):
        hits = sorted({line.split('"')[1] for line in qsf.splitlines() if f'/{name}"' in line})
        if len(hits) != 1:
            raise SystemExit(f'{name}: expected one assignment in {proj}, got {hits}')
        return proj / hits[0]
    original = assigned('cd_top.vhd').read_text()

    # each patch alone, then all three in order
    for n in PATCHES:
        apply_patches(original, [n])
    cand = apply_patches(original, PATCHES)
    print(f'patches: each applies alone; {" -> ".join(PATCHES)} applies in sequence')

    deps = [assigned(n) for n in ('dpram.vhd', 'pocket_dpram_sdp.vhd', 'export.vhd', 'cd_xa_zigzag.vhd', 'cd_xa.vhd')]
    if args.area:
        sources = {'SyncFifoFallThrough': assigned('SyncFifoFallThrough.vhd'), 'pocket_dpram_sdp': deps[1],
                   'dpram': deps[0], 'cd_xa': deps[4], 'export': deps[2]}
        variants = [('rc1i', original)] + [(n, apply_patches(original, [n])) for n in PATCHES] + [('both, in order', cand)]
        with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            areas = list(pool.map(lambda iv: area_screen(args.ghdl, iv[1][1], sources, w / f'area{iv[0]}'),
                                  enumerate(variants)))
        print('Yosys synth_intel_alm screen of cd_top (estimate; black-box FIFOs/RAM wrappers/cd_xa).')
        print('ALUT totals move by tens between near-identical inputs (ABC); FF/ARITH and the word-level bits do not.')
        print(f'  {"variant":22} {"ALUT":>6} {"ARITH":>6} {"FF":>6} {"M10K":>5}   dALUT dARITH  dFF   '
              'word bits d(alu/compare/mux/ff)')
        a0 = areas[0]
        for (label, _), a in zip(variants, areas):
            wd = '/'.join(f'{a["words"][k] - a0["words"][k]:+d}' for k in ('alu', 'compare', 'mux', 'ff'))
            print(f'  {label:22} {a["ALUT"]:6} {a["MISTRAL_ALUT_ARITH"]:6} {a["MISTRAL_FF"]:6} {a["MISTRAL_M10K"]:5}'
                  f'   {a["ALUT"] - a0["ALUT"]:+5} {a["MISTRAL_ALUT_ARITH"] - a0["MISTRAL_ALUT_ARITH"]:+6}'
                  f' {a["MISTRAL_FF"] - a0["MISTRAL_FF"]:+4}   {wd}')
        return
    fifo = [assigned(n) for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    ghdl = args.ghdl

    def run(cmd, timeout=900):
        r = subprocess.run([str(c) for c in cmd], cwd=w, capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-3000:]}{r.stderr[-3000:]}')

    def fresh(name):
        d = w / name
        d.mkdir(exist_ok=True)
        for f in d.iterdir():
            f.unlink()
        return d

    fresh('altlib')
    quartus = find_tool('quartus_sh')
    if quartus:
        lib = Path(quartus).parents[1] / 'eda/sim_lib'
        alt = [lib / 'altera_mf_components.vhd', lib / 'altera_mf.vhd']
        print('altera_mf: Quartus simulation library')
    else:
        alt = [ROOT / 'sim/reference/altera_mf_altsyncram_model.vhd']
        print('altera_mf: behavioural altsyncram model (Quartus not installed)')
    run([ghdl, '-a', *opts, '--work=altera_mf', '--workdir=altlib', *alt])
    fresh('memlib')
    run([ghdl, '-a', *opts, '--work=mem', '--workdir=memlib', '-Paltlib', *fifo, deps[0], deps[1]])
    fresh('ref')
    (w / 'ref_sim.vhd').write_text(instrument(sim_text(original)))
    base = ['-Paltlib', '-Pmemlib']
    run([ghdl, '-a', *opts, '--work=ref', '--workdir=ref', *base, *deps, 'ref_sim.vhd'])
    (w / 'metadata.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words(TRACKS, region=2)))
    tb = ROOT / 'sim/reference/tb_cd_lockstep_trim.vhd'   # the October bench (XA_TEST)

    def build(label, text):
        (w / f'{label}_sim.vhd').write_text(text)
        d = fresh(f'work-{label}')
        run([ghdl, '-a', *opts, f'--workdir={d.name}', '-Pref', *base, *deps, f'{label}_sim.vhd', tb])
        run([ghdl, '-e', *opts, f'--workdir={d.name}', '-Pref', *base, 'tb_cd_lockstep_trim'])
        return d.name

    def sim(job):
        dirname, seed, mode = job
        pause, xa = MODES[mode]
        out = subprocess.run([ghdl, '-r', *opts, f'--workdir={dirname}', '-Pref', *base, 'tb_cd_lockstep_trim',
                              f'-gSEED={seed}', f'-gRUN_CYCLES={args.cycles}', f'-gSECTORS={sum(TRACKS)}',
                              f'-gPAUSE_READ={pause}', f'-gXA_TEST={xa}', '--ieee-asserts=disable'],
                             cwd=w, capture_output=True, text=True, timeout=14400)
        text = out.stdout + out.stderr
        m = re.search(r'LOCKSTEP (PASS.*|MISMATCH.*)', text)
        cov = Counter(re.findall(r'COV (\w+)', text))
        other = ' | '.join(line for line in text.splitlines() if 'COV ' not in line)[-400:]
        return (m[1] if m else 'NO RESULT ' + other).strip(), cov

    cand_sim = sim_text(cand)
    jobs = [(build('cand', cand_sim), s, m) for m in args.modes for s in args.seeds]
    muts = []
    if not args.no_mutations:
        for i, (name, trim, old, new) in enumerate(MUTATIONS):
            if trim + '.patch' not in PATCHES: continue   # only the selected trims' mutations
            if cand_sim.count(old) != 1:
                raise SystemExit(f'mutation anchor not unique: {name}')
            muts.append((name, (build(f'mut{i}', cand_sim.replace(old, new)), args.seeds[0], 'xa')))
    alljobs = jobs + [j for _, j in muts]
    with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, len(alljobs)))) as pool:
        results = list(pool.map(sim, alljobs))
    ok = True
    total = Counter()
    for (d, s, mode), (r, cov) in zip(jobs, results):
        m = re.search(r'irqs (\d+) sectors (\d+)', r)
        good = r.startswith('PASS') and bool(m) and int(m[1]) > 20 and int(m[2]) >= 10
        ok &= good
        total += cov
        print(f'{mode:5} seed {s}: {r[:200]} -> {"ok" if good else "FAIL"}', flush=True)
        print(f'      coverage: {dict(sorted(cov.items()))}', flush=True)
    print(f'coverage, all candidate runs: {dict(sorted(total.items()))}')
    for (name, _), (r, cov) in zip(muts, results[len(jobs):]):
        caught = r.startswith('MISMATCH')
        ok &= caught
        print(f'mutation [{name}]: {"caught" if caught else "NOT caught"} ({r[:120]})', flush=True)
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
