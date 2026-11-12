#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PSX Pocket contributors
"""Side-by-side check of the save-drive-state step (scripts/prepare_save_drive_state.py).

The cd_top before the step (reference) and after it (candidate) run in lockstep under the random
command stimulus of sim/reference/tb_cd_lockstep.vhd (the bench is derived from it here: both
save_cd_state outputs mapped, the reference's setLocActive brought out on a debug port). On every
clk1x edge after the first reset:
  - every cd_top output but save_cd_state equal (the lockstep bench's own check);
  - candidate save_cd_state = reference save_cd_state with bit 56 OR setLocActive (reference);
  - the cycles where the step changes bit 56 are counted ("held"; the random stimulus leaves a Setloc
    pending only on some seeds: 2 does, 1 and 3 do not).
Planted faults (prepare_save_drive_state.FAULTS) run as candidates on --fault-seed (2) and must fail;
the fault seed's own candidate run must have held > 0.
The drive-level effect (save while a Setloc is pending) is scripts/run_cd_save_read.py case 4.

Usage: run_save_drive_state.py --project PROJECT [--seeds 1 2 3] [--cycles N] [--build-only | --run-only]
"""
import argparse
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import prepare_save_drive_state as step
from psx_disc_host import multitrack_metadata_words
from run_cd_lockstep import TRACKS, sim_text
from tool_paths import ROOT, find_tool

TB = 'tb_cd_drive_state_tap'


def _once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f'derive: expected 1 match of {old.strip()[:70]!r}, found {text.count(old)}')
    return text.replace(old, new)


def derive_tb(text):
    """tb_cd_lockstep.vhd -> the tap lockstep bench."""
    text = text.replace('tb_cd_lockstep', TB)
    text = _once(text, '      cd_hps_lba : std_logic_vector(31 downto 0);\n   end record;',
                 '      cd_hps_lba : std_logic_vector(31 downto 0);\n'
                 '      cd_state : std_logic_vector(63 downto 0);\n      setloc : std_logic;\n   end record;')
    text = _once(text, "            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle);\n         a <= o;",
                 "            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle,\n"
                 "            save_cd_state => o.cd_state, dbg_setloc => o.setloc);\n         a <= o;")
    text = _once(text, "            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle);\n         b <= o;",
                 "            SS_wren => '0', SS_rden => '0', SS_DataRead => open, SS_idle => o.SS_idle,\n"
                 "            save_cd_state => o.cd_state);\n         o.setloc <= '0';\n         b <= o;")
    text = _once(text, '   signal n_irq, n_sectors, n_cmds : integer := 0;',
                 '   signal n_irq, n_sectors, n_cmds : integer := 0;\n'
                 '   signal n_held, n_setloc : integer := 0;   -- cycles the step changes bit 56; cycles with a Setloc pending\n'
                 '   function strip(x : tout) return tout is\n'
                 '      variable y : tout := x;\n'
                 '   begin\n'
                 "      y.cd_state := (others => '0'); y.setloc := '0'; return y;\n"
                 '   end function;')
    text = _once(text, '         if started and a /= b then',
                 '         if started then\n'
                 "            if a.setloc = '1' then n_setloc <= n_setloc + 1; end if;\n"
                 "            if a.setloc = '1' and a.cd_state(56) = '0' then n_held <= n_held + 1; end if;\n"
                 "            if b.cd_state /= (a.cd_state(63 downto 57) & (a.cd_state(56) or a.setloc) & a.cd_state(55 downto 0)) then\n"
                 '               report "LOCKSTEP MISMATCH tap cycle " & integer\'image(cycles) & " " & to_hstring(a.cd_state) & "/"\n'
                 '                  & to_hstring(b.cd_state) & " setloc " & std_logic\'image(a.setloc) severity failure;\n'
                 '            end if;\n'
                 '         end if;\n'
                 '         if started and strip(a) /= strip(b) then')
    text = _once(text, '" sectors " & integer\'image(n_sectors) severity note;',
                 '" sectors " & integer\'image(n_sectors)\n'
                 '         & " held " & integer\'image(n_held) & " setloc " & integer\'image(n_setloc) severity note;')
    return text


def ref_text(text):
    """The reference cd_top with setLocActive on a debug output (nothing else changes)."""
    text = _once(text, '      save_cd_state        : out std_logic_vector(63 downto 0);',
                 "      save_cd_state        : out std_logic_vector(63 downto 0);\n      dbg_setloc           : out std_logic := '0';")
    return _once(text, '   save_copy_busy <= ', '   dbg_setloc <= setLocActive;\n   save_copy_busy <= ')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', type=Path)
    ap.add_argument('--work', type=Path, default=ROOT / 'build/sim/save-drive-state')
    ap.add_argument('--seeds', type=int, nargs='+', default=[2, 4, 5])
    ap.add_argument('--fault-seed', type=int, default=2, help='a seed whose stimulus leaves a Setloc pending (1 and 3 never do)')
    ap.add_argument('--cycles', type=int, default=6000000)
    ap.add_argument('--sim-lib', type=Path)
    ap.add_argument('--build-only', action='store_true')
    ap.add_argument('--run-only', action='store_true')
    ap.add_argument('--jobs', type=int, default=3)
    args = ap.parse_args()
    ghdl = find_tool('ghdl')
    w = args.work.resolve(); w.mkdir(parents=True, exist_ok=True)
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    base = ['-Paltlib', '-Pmemlib']
    labels = ['cand'] + [f'f-{f}' for f in step.FAULTS]

    def run(cmd, timeout=900):
        r = subprocess.run(cmd, cwd=w, capture_output=True, text=True, timeout=timeout)
        if r.returncode: raise SystemExit(f'failed: {" ".join(map(str, cmd))}\n{r.stdout[-3000:]}{r.stderr[-3000:]}')
        return r.stdout + r.stderr

    if not args.run_only:
        q = find_tool('quartus_sh')
        lib = args.sim_lib or (Path(q).parents[1] / 'eda/sim_lib' if q else None)
        if not lib or not (lib / 'altera_mf.vhd').is_file(): raise SystemExit('give --sim-lib DIR')
        qsf = (args.project / 'apf_top.qsf').read_text()

        def assigned(name):
            hits = sorted({line.split('"')[1] for line in qsf.splitlines() if f'/{name}"' in line})
            if not hits: raise SystemExit(f'{name} not assigned')
            return hits[0]
        src = w / 'src'; src.mkdir(exist_ok=True)

        def local(path):
            q = src / Path(path).name
            q.write_bytes(Path(path).read_bytes())
            return str(q)
        deps = [local(assigned(n)) for n in ('dpram.vhd', 'pocket_dpram_sdp.vhd', 'export.vhd', 'cd_xa_zigzag.vhd', 'cd_xa.vhd')]
        fifo = [local(assigned(n)) for n in ('SyncFifo.vhd', 'SyncFifoFallThrough.vhd')]
        alt = [local(lib / 'altera_mf_components.vhd'), local(lib / 'altera_mf.vhd')]
        old = Path(assigned('cd_top.vhd')).read_text()
        new = step.cd_text(old)
        if step.restore(new) != old: raise SystemExit('restore is not exact')
        texts = {'ref': ref_text(old), 'cand': new}
        texts.update({f'f-{f}': step.fault_text(new, f) for f in step.FAULTS})
        for k, t in texts.items(): (src / f'{k}_sim.vhd').write_text(sim_text(t))
        (src / f'{TB}.vhd').write_text(derive_tb((ROOT / 'sim/reference/tb_cd_lockstep.vhd').read_text()))
        (w / 'metadata.hex').write_text(''.join(f'{x:08x}\n' for x in multitrack_metadata_words(TRACKS, region=2)))
        if not (w / 'altlib/altera_mf-obj08.cf').exists():
            (w / 'altlib').mkdir(exist_ok=True)
            run([ghdl, '-a', *opts, '--work=altera_mf', '--workdir=altlib', *alt])
        for name in ['memlib', 'ref'] + [f'work-{x}' for x in labels]:
            d = w / name
            if d.exists():
                for f in d.iterdir(): f.unlink()
            d.mkdir(exist_ok=True)
        run([ghdl, '-a', *opts, '--work=mem', '--workdir=memlib', '-Paltlib', *fifo, deps[0], deps[1]])
        run([ghdl, '-a', *opts, '--work=ref', '--workdir=ref', *base, *deps, str(src / 'ref_sim.vhd')])
        for x in labels:
            run([ghdl, '-a', *opts, f'--workdir=work-{x}', '-Pref', *base, *deps, str(src / f'{x}_sim.vhd'), str(src / f'{TB}.vhd')])
            run([ghdl, '-e', *opts, f'--workdir=work-{x}', '-Pref', *base, TB])
        print(f'built {w}: {", ".join(labels)}')
    if args.build_only: return

    def sim(label, seed):
        r = subprocess.run([ghdl, '-r', *opts, f'--workdir=work-{label}', '-Pref', *base, TB, f'-gSEED={seed}',
                            f'-gRUN_CYCLES={args.cycles}', f'-gSECTORS={sum(TRACKS)}', '--ieee-asserts=disable'],
                           cwd=w, capture_output=True, text=True, timeout=14400)
        m = re.search(r'LOCKSTEP (PASS.*|MISMATCH.*)', r.stdout + r.stderr)
        return (m[1] if m else 'NO RESULT ' + (r.stdout + r.stderr)[-400:]).strip()
    jobs = [('cand', s) for s in args.seeds] + [(x, args.fault_seed) for x in labels[1:]]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(lambda j: sim(*j), jobs))
    ok, rows = True, []
    for (label, seed), r in zip(jobs, results):
        fault = label != 'cand'
        held = re.search(r' held (\d+)', r)
        # a candidate run must pass; it counts as exercising the step only with held > 0 (reported)
        good = r.startswith('MISMATCH') if fault else r.startswith('PASS')
        ok &= bool(good)
        rows.append({'run': label, 'seed': seed, 'result': r, 'expected': 'MISMATCH' if fault else 'PASS, held > 0', 'ok': bool(good)})
        print(f'{label} seed {seed}: {r} -> {"ok" if good else "WRONG"}', flush=True)
    exercised = [s for (label, s), r in zip(jobs, results) if label == 'cand' and re.search(r' held [1-9]', r)]
    if args.fault_seed not in exercised: ok = False; print(f'fault seed {args.fault_seed} does not leave a Setloc pending')
    (w / 'report.json').write_text(json.dumps({'ok': ok, 'exercised_seeds': exercised, 'runs': rows}, indent=1) + '\n')
    print('PASS save-drive-state tap lockstep' if ok else 'FAIL save-drive-state tap lockstep')
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__':
    main()
