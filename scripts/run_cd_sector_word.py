#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lockstep of prepare_cd_sector_word.py against its input, with traffic for all three word paths.

run_cd_lockstep.py's disc has no Mode 2 sectors, so it never stores a data
sector or feeds XA. This run uses its bench (sim/reference/tb_cd_lockstep.vhd)
with text changes only in a work copy: track 1 sectors get a sync, a BCD header
and Mode 2 subheaders (every third real-time audio), and the read-then-pause
stimulus runs with XA enabled half the time. A directed start reads sectors with
XA on, then with XA off and their data read out. The candidate reports how often
each strobe fired, so a pass shows each path carried words, and asserts under
every strobe that the word is the buffer output of the clock before. Every
cd_top output is compared on every clk1x edge. Each mutation drops the merged
load in one state and must be caught.
Usage: run_cd_sector_word.py --project PROJECT [--seeds 1 2 3] [--cycles N] [--no-mutations]
"""
import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from prepare_cd_sector_word import MARK, word_text
from psx_disc_host import multitrack_metadata_words
from run_cd_lockstep import TRACKS, sim_text
from tool_paths import ROOT, find_tool

COUNT = """   process (clk1x)   -- check: path counts
      variable n_data, n_xa, n_cdda, n : integer := 0;
      variable prev : std_logic_vector(31 downto 0) := (others => '0');
   begin
      if (rising_edge(clk1x)) then
         assert not (sectorBuffers_wrenA = '1' or XA_write = '1' or CDDA_write = '1') or
                    (sectorBuffers_DataA = prev and XA_data = prev and CDDA_data = prev)
            report "CDWORD INVARIANT word differs from the buffer output" severity failure;
         prev := sectorBuffer_DataB;
         if (sectorBuffers_wrenA = '1') then n_data := n_data + 1; end if;
         if (XA_write = '1') then n_xa := n_xa + 1; end if;
         if (CDDA_write = '1') then n_cdda := n_cdda + 1; end if;
         n := n + 1;
         if (n mod 500000 = 0) then
            report "CDWORD data " & integer'image(n_data) & " xa " & integer'image(n_xa) & " cdda " & integer'image(n_cdda) severity note;
         end if;
      end if;
   end process;

"""


def mutate(cand, tag):
    """Drop the merged load in one state."""
    old = {'data': '                  sectorBuffers_DataA <= sectorBuffer_DataB;\n',
           'xa': f'                  sectorBuffers_DataA <= sectorBuffer_DataB;   -- {MARK}: XA_data\n',
           'cdda': f'                  sectorBuffers_DataA <= sectorBuffer_DataB;   -- {MARK}: CDDA_data\n'}[tag]
    if cand.count(old) != 1: raise SystemExit(f'mutation anchor {tag}: {cand.count(old)}')
    return cand.replace(old, '')


def bench_text(t):
    def once(old, new):
        nonlocal t
        if t.count(old) != 1: raise SystemExit(f'bench anchor: {old[:50]}')
        t = t.replace(old, new)
    once("                  else cd_hps_data <= std_logic_vector(to_unsigned((lba * 101 + i * 29) mod 65536, 16)); end if;\n",
         """                  elsif lba < 150 + 40 and i <= 11 then
                     case i is
                        when 0 => cd_hps_data <= x"FF00";
                        when 1 | 2 | 3 | 4 => cd_hps_data <= x"FFFF";
                        when 5 => cd_hps_data <= x"00FF";
                        when 6 => cd_hps_data <= std_logic_vector(to_unsigned(((((lba / 75) mod 60) / 10) * 16 + (lba / 75) mod 60 mod 10) * 256 + ((lba / 4500) / 10) * 16 + (lba / 4500) mod 10, 16));
                        when 7 => cd_hps_data <= std_logic_vector(to_unsigned(2 * 256 + ((lba mod 75) / 10) * 16 + (lba mod 75) mod 10, 16));
                        when 8 | 10 => cd_hps_data <= x"0101";
                        when others => if lba mod 3 = 0 then cd_hps_data <= x"0044"; else cd_hps_data <= x"0008"; end if;
                     end case;
                  else cd_hps_data <= std_logic_vector(to_unsigned((lba * 101 + i * 29) mod 65536, 16)); end if;
""")
    once("""            if n = 0 then sec := 150 + pick(r, 30); else sec := 150 + 40 + 150 + pick(r, 30); end if;
            wr(0, 0);
""", """            if n = 0 then sec := 150 + pick(r, 30); else sec := 150 + 40 + 150 + pick(r, 30); end if;
            wr(0, 0);
            uniform(s1, s2, r); wr(2, 16#01# + 16#40# * pick(r, 2)); wr(1, 16#0E#); n_cmds <= n_cmds + 1;
            tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
""")
    once("""      wr(0, 1); wr(2, 31); wr(0, 1); wr(3, 31); wr(0, 0);
      while cycles < RUN_CYCLES loop
""", """      wr(0, 1); wr(2, 31); wr(0, 1); wr(3, 31); wr(0, 0);
      wr(0, 2); wr(2, 128); wr(3, 128); wr(0, 3); wr(1, 128); wr(2, 128); wr(3, 32); wr(0, 0);
      wr(1, 16#0C#); tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
      for pass in 0 to 3 loop
         if pass mod 2 = 0 then wr(2, 16#40#); else wr(2, 16#00#); end if;
         wr(1, 16#0E#); tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
         sec := 154 + pass * 6;
         wr(2, ((sec / 4500) / 10) * 16 + (sec / 4500) mod 10);
         wr(2, (((sec / 75) mod 60) / 10) * 16 + ((sec / 75) mod 60) mod 10);
         wr(2, ((sec mod 75) / 10) * 16 + (sec mod 75) mod 10);
         wr(1, 16#02#); tick(40000); wr(0, 1); wr(3, 31); wr(0, 0);
         wr(1, 16#06#); n_cmds <= n_cmds + 1;
         for i in 0 to 24 loop
            tick(30000);
            wr(0, 1); rd(3); rd(1); wr(3, 31); wr(0, 0); wr(3, 16#80#);
            for j in 0 to 99 loop rd(2); end loop;
         end loop;
         wr(1, 16#09#); tick(60000); wr(0, 1); wr(3, 31); wr(0, 0);
      end loop;
      while cycles < RUN_CYCLES loop
""")
    return t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path, required=True)
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/cd-sector-word')
    ap.add_argument('--seeds', type=int, nargs='+', default=[1, 2, 3])
    ap.add_argument('--cycles', type=int, default=6000000)
    ap.add_argument('--no-mutations', action='store_true')
    args = ap.parse_args()
    ghdl = find_tool('ghdl')
    lib = Path(find_tool('quartus_sh')).parents[1] / 'eda/sim_lib'
    w = args.work.resolve(); w.mkdir(parents=True, exist_ok=True)
    qsf = (args.project / 'apf_top.qsf').read_text()

    def assigned(name):
        hits = sorted({line.split('"')[1] for line in qsf.splitlines() if f'/{name}"' in line})
        if len(hits) != 1: raise SystemExit(f'{name}: {hits}')
        return hits[0]
    deps = [assigned(n) for n in ('dpram.vhd', 'pocket_dpram_sdp.vhd', 'export.vhd', 'cd_xa_zigzag.vhd', 'cd_xa.vhd')]
    fifo = [assigned(n) for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
    opts = ['--std=08', '-frelaxed', '-fsynopsys']

    def run(cmd):
        r = subprocess.run(cmd, cwd=w, capture_output=True, text=True, timeout=900)
        if r.returncode: raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-3000:]}{r.stderr[-3000:]}')

    if not (w / 'altlib/altera_mf-obj08.cf').exists():
        (w / 'altlib').mkdir(exist_ok=True)
        run([ghdl, '-a', *opts, '--work=altera_mf', '--workdir=altlib', lib / 'altera_mf_components.vhd', lib / 'altera_mf.vhd'])
    for name in ('memlib', 'ref'):
        d = w / name; d.mkdir(exist_ok=True)
        for f in d.iterdir(): f.unlink()
    run([ghdl, '-a', *opts, '--work=mem', '--workdir=memlib', '-Paltlib', *fifo, deps[0], deps[1]])
    ref = Path(assigned('cd_top.vhd')).read_text()
    (w / 'ref_sim.vhd').write_text(sim_text(ref))
    base = ['-Paltlib', '-Pmemlib']
    run([ghdl, '-a', *opts, '--work=ref', '--workdir=ref', *base, *deps, 'ref_sim.vhd'])
    (w / 'metadata.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words(TRACKS, region=2)))
    (w / 'tb.vhd').write_text(bench_text((ROOT / 'sim/reference/tb_cd_lockstep.vhd').read_text()))

    def build(label, text):
        end = text.rindex('end architecture')
        (w / f'{label}_sim.vhd').write_text(sim_text(text[:end] + COUNT + text[end:]))
        d = w / f'work-{label}'; d.mkdir(exist_ok=True)
        for f in d.iterdir(): f.unlink()
        run([ghdl, '-a', *opts, f'--workdir={d.name}', '-Pref', *base, *deps, f'{label}_sim.vhd', 'tb.vhd'])
        run([ghdl, '-e', *opts, f'--workdir={d.name}', '-Pref', *base, 'tb_cd_lockstep'])
        return d.name

    def sim(job):
        d, seed = job
        out = subprocess.run([ghdl, '-r', *opts, f'--workdir={d}', '-Pref', *base, 'tb_cd_lockstep', f'-gSEED={seed}',
                              f'-gRUN_CYCLES={args.cycles}', f'-gSECTORS={sum(TRACKS)}', '-gPAUSE_READ=1'],
                             cwd=w, capture_output=True, text=True, timeout=7200)
        text = out.stdout + out.stderr
        m = re.search(r'LOCKSTEP (PASS.*|MISMATCH.*)|(CDWORD INVARIANT.*)', text)
        c = re.findall(r'CDWORD (data \d+ xa \d+ cdda \d+)', text)
        return ((m[1] or m[2]) if m else 'NO RESULT ' + text[-400:]).strip()[:200], (c[-1] if c else 'no counts')

    cand = word_text(ref)
    jobs = [(build('cand', cand), s) for s in args.seeds]
    if not args.no_mutations:
        jobs += [(build(f'mut-{tag}', mutate(cand, tag)), s) for tag in ('data', 'xa', 'cdda') for s in args.seeds]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(sim, jobs))
    ok = True
    caught = {}
    for (d, s), (r, c) in zip(jobs, results):
        print(f'{d} seed {s}: {r} | {c}', flush=True)
        if d.startswith('work-mut'): caught[d] = caught.get(d, False) or r.startswith(('MISMATCH', 'CDWORD INVARIANT'))
        else: ok &= r.startswith('PASS')
    for d, hit in caught.items():
        print(f'{d}: {"caught" if hit else "NOT CAUGHT"}'); ok &= hit
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
