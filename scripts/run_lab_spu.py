#!/usr/bin/env python3
"""Lockstep check of prepare_lab_spu.py against RC-g's own save-states/spu.vhd.

Reference: the snapshot's spu.vhd. Candidate: prepare_lab_spu.spu_text of it (renamed spu_g). Both are
compiled with the snapshot's own pocket_dpram_sdp and the pinned upstream RAM / FIFO / gauss / spu_ram
sources. Bench: run_spu_reverb_operands' directed reverb lockstep (reverb ring at the top of SPU RAM,
reverb, CD audio and all voices on, every output compared each clk1x). Here the reverb offsets are
0..3 instead of 1..3, so mSAME / mDIFF = 0 (rv-addr's 18-bit wrap) occurs. Then the plain random
lockstep (tb_spu_guest_m10k). Planted faults (prepare_lab_spu.FAULTS) must fail.
Usage: run_lab_spu.py --src DIR [--parts rv-addr,rv-cse] [--seeds 1,2,3] [--no-faults]
  DIR holds the snapshot's spu.vhd, pocket_dpram_sdp.vhd, dpram.vhd, RamMLAB.vhd, SyncFifo*.vhd,
  spu_gauss.vhd and spu_ram.vhd (copied there by the caller); altera_mf from Quartus's sim_lib.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_lab_spu as lab  # noqa: E402
import run_spu_reverb_operands as rv  # noqa: E402
from tool_paths import ROOT, find_tool  # noqa: E402

OLD_OFFSET = 'data := std_logic_vector(to_unsigned(1 + rnd(3), 16));'
NEW_OFFSET = 'data := std_logic_vector(to_unsigned(rnd(4), 16));   -- lab-spu: offset 0 too'


def run(directory, src, candidate_text, seeds, operations, directed):
    ghdl = find_tool('ghdl')
    directory.mkdir(parents=True, exist_ok=True)
    log = (directory / 'build.log').open('w')
    opts = ['--std=08', '-frelaxed', '-fsynopsys']
    altlib = directory.parent / 'altlib'
    if not (altlib / 'altera_mf-obj08.cf').exists():
        altlib.mkdir(parents=True, exist_ok=True)
        lib = Path(find_tool('quartus_sh')).parents[1] / 'eda/sim_lib'
        subprocess.run([ghdl, '-a', *opts, '--work=altera_mf', f'--workdir={altlib}', str(lib / 'altera_mf_components.vhd'),
                        str(lib / 'altera_mf.vhd')], check=True, timeout=1800, stdout=log, stderr=subprocess.STDOUT)

    def call(cmd, *args):
        subprocess.run([ghdl, cmd, *opts, f'-P{altlib}', *args], cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=1800)
    for name in ('dpram.vhd', 'RamMLAB.vhd', 'SyncFifo.vhd', 'SyncFifoFallThrough.vhd', 'SyncFifoFallThroughMLAB.vhd',
                 'pocket_dpram_sdp.vhd'):
        call('-a', '--work=mem', str(src / name))
    for name in ('dpram.vhd', 'RamMLAB.vhd', 'spu_gauss.vhd', 'spu_ram.vhd'):   # spu_ram uses work.dpram_dif
        call('-a', str(src / name))
    # the save-state SPU instantiates psx_spu_gaussian_sum (hard DSP, SystemVerilog): bind the portable
    # VHDL model, as run_base_trim_audio.py does, or both sides run with an unbound interpolation sum
    from run_spu_gaussian_sum import MODEL
    (directory / 'psx_spu_gaussian_sum_model.vhd').write_text(MODEL)
    call('-a', str(directory / 'psx_spu_gaussian_sum_model.vhd'))
    reference = (src / 'spu.vhd').read_text()
    (directory / 'spu_ref.vhd').write_text(reference)
    call('-a', str(directory / 'spu_ref.vhd'))
    (directory / 'spu_g.vhd').write_text(re.sub(r'\bspu\b', 'spu_g', candidate_text(reference)))
    call('-a', str(directory / 'spu_g.vhd'))
    if directed:
        bench, top = rv.reverb_bench(directory), 'tb_spu_reverb_lockstep'
    else:
        text = (ROOT / 'sim/reference/tb_spu_guest_m10k.vhd').read_text()
        anchor = "        reset<='0';\n        for i in 1 to 20 loop tick; end loop;\n"
        if text.count(anchor) != 1:
            raise SystemExit('tb_spu_guest_m10k start changed')
        bench, top = directory / 'tb_spu_guest_m10k.vhd', 'tb_spu_guest_m10k'
        bench.write_text(text.replace(anchor, "        reset<='0';\n        for i in 1 to 200 loop tick; end loop;   -- lab-spu: after the voice-RAM clear\n"))
    call('-a', str(bench))
    call('-e', top)
    results = []
    for seed in seeds:
        r = subprocess.run([ghdl, '-r', *opts, f'-P{altlib}', top, f'-gSEED={seed}', f'-gOPERATIONS={operations}',
                            '--assert-level=error', '--ieee-asserts=disable'], cwd=directory, capture_output=True, text=True, timeout=7200)
        out = r.stdout + r.stderr
        (directory / f'seed{seed}.log').write_text(out)
        results.append((r.returncode == 0 and 'PASS SPU guest block RAM lockstep' in out,
                        [l for l in out.splitlines() if 'PASS' in l or 'mismatch' in l.lower() or 'error' in l.lower()][:1]))
    return results


ADDR_TB = """library ieee; use ieee.std_logic_1164.all; use ieee.numeric_std.all;
entity tb_lab_spu_addr is end;
architecture t of tb_lab_spu_addr is
   type tstate is (IDLE, REVERB_READ1, REVERB_WRITE1);
   signal state : tstate := IDLE;
   signal reverb_count : integer range 0 to 7 := 0;
   signal reverb_reqAddr : unsigned(15 downto 0) := (others => '0');
   signal reverbCurrentAddress : unsigned(17 downto 0) := (others => '0');
   signal reverb_reqAddr2 : unsigned(17 downto 0);
   signal reverb_dec : std_logic;
   signal reverb_addA, reverb_decMask : unsigned(18 downto 0);
   signal old_sum, reverbAddAddr : unsigned(18 downto 0);
begin
   -- original text (prepare_lab_spu.ADDR old), reverbAddAddr renamed old_sum
OLDTEXT
   -- inserted text (prepare_lab_spu.ADDR new)
NEWTEXT
   process
      type tcur is array (0 to 13) of natural;
      constant cur : tcur := (0, 1, 2, 3, 4, 131071, 131072, 262140, 262141, 262142, 262143, 77777, 200003, 65536);
      variable bad : natural := 0;
   begin
      for s in 0 to 2 loop
         for c in 0 to 7 loop
            for k in cur'range loop
               for a in 0 to 65535 loop
                  state <= tstate'val(s); reverb_count <= c; reverb_reqAddr <= to_unsigned(a, 16);
                  reverbCurrentAddress <= to_unsigned(cur(k), 18);
                  wait for 1 ns;
                  if old_sum /= reverbAddAddr then bad := bad + 1; end if;
               end loop;
            end loop;
         end loop;
      end loop;
      assert bad = 0 report "rv-addr mismatches: " & integer'image(bad) severity failure;
      report "PASS rv-addr exhaustive 3 x 8 x 14 x 65536";
      wait;
   end process;
end;
"""


def addr_check(directory, new_text):
    """Exhaustive check of the rv-addr expression: every reqAddr, state / count, 14 current addresses."""
    ghdl = find_tool('ghdl')
    directory.mkdir(parents=True, exist_ok=True)
    old, new = lab.ADDR[0][0], new_text
    old = old.replace('reverbAddAddr   <=', 'old_sum   <=')
    (directory / 'tb.vhd').write_text(ADDR_TB.replace('OLDTEXT', old).replace('NEWTEXT', new))
    for cmd in (['-a', '--std=08', 'tb.vhd'], ['-e', '--std=08', 'tb_lab_spu_addr']):
        subprocess.run([ghdl, *cmd], cwd=directory, check=True, capture_output=True, timeout=600)
    r = subprocess.run([ghdl, '-r', '--std=08', 'tb_lab_spu_addr'], cwd=directory, capture_output=True, text=True, timeout=3600)
    out = r.stdout + r.stderr
    return r.returncode == 0 and 'PASS rv-addr' in out, ([l for l in out.splitlines() if 'PASS' in l or 'mismatch' in l] or [out[-200:]])[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--src', type=Path, required=True)
    parser.add_argument('--parts', default=','.join(lab.PARTS))
    parser.add_argument('--seeds', default='1,2,3')
    parser.add_argument('--no-faults', action='store_true')
    parser.add_argument('--faults', help='comma list: only these planted faults (default: every fault of the parts)')
    args = parser.parse_args()
    parts = [p for p in args.parts.split(',') if p]
    seeds = [int(s) for s in args.seeds.split(',')]
    if rv.SETUP.count(OLD_OFFSET) != 1:
        raise SystemExit('reverb bench setup changed')
    rv.SETUP = rv.SETUP.replace(OLD_OFFSET, NEW_OFFSET)
    # SS_reset (tied to reset) clears 192 voice-RAM rows; a register write during that clear wraps it and
    # re-clears records after the first sample ("Record initialization overlaps active voice", seeds 1/4).
    # Wait 200 clk1x after reset before the first write (PSX: SPU and DMA timing's diagnosis).
    start = '        for i in 1 to 20 loop tick; end loop;\n'
    if not rv.SETUP.startswith(start):
        raise SystemExit('reverb bench start changed')
    rv.SETUP = '        for i in 1 to 200 loop tick; end loop;   -- lab-spu: after the voice-RAM clear\n' + rv.SETUP[len(start):]
    # the directed setup holds SPUCNT at 0xC085 (CD audio and CD reverb on): toggle bits 0 / 2 now and
    # then, so that the reverb input's "no CD" branch runs too (rv-cse's cd fold)
    rv.REVERB_SAFE += ('            if rnd(32) = 0 then address := 16#1AA#; data := x"C085";   -- lab-spu: SPUCNT CD bits\n'
                       "               if rnd(2) = 0 then data(0) := '0'; end if; if rnd(2) = 0 then data(2) := '0'; end if; end if;\n")
    directory = ROOT / 'build/sim/lab-spu-rcg'
    src = args.src.resolve()
    failures = []
    if 'rv-addr' in parts:
        ok, line = addr_check(directory / 'addr', lab.ADDR[0][1])
        print('rv-addr exhaustive:', 'PASS' if ok else 'FAIL', line, flush=True)
        if not ok:
            failures.append('rv-addr exhaustive')
        if not args.no_faults:
            for name in ('no-wrap', 'dec-all-reads'):
                _, fo, fn = lab.FAULTS[name]
                caught = not addr_check(directory / ('addr-' + name), lab.ADDR[0][1].replace(fo, fn))[0]
                print(f'fault {name} (exhaustive):', 'caught' if caught else 'NOT DETECTED', flush=True)
                if not caught:
                    failures.append(name)
    if not args.no_faults:
        for name, (part, _, _) in lab.FAULTS.items():
            if part not in parts or part == 'rv-addr':   # rv-addr faults: judged by the exhaustive check above
                continue                                 # (the lockstep outputs do not expose the reverb address)
            if args.faults and name not in args.faults.split(','):
                continue
            # gs-half breaks the bus readback of the static / guest copies: the random bench reads them back
            directed = name not in ('gs-half', 'gs-half2')
            caught = not run(directory / ('fault-' + name), src, lambda t, n=name: lab.fault_text(lab.spu_text(t, parts), n),
                             seeds[:1], rv.OPERATIONS if directed else 6000, directed)[0][0]
            print(f'fault {name}:', 'caught' if caught else 'NOT DETECTED', flush=True)
            if not caught:
                failures.append(name)
    for passed, tail in run(directory / 'reverb', src, lambda t: lab.spu_text(t, parts), seeds, rv.OPERATIONS, True):
        print('reverb lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed:
            failures.append('reverb lockstep')
    for passed, tail in run(directory / 'random', src, lambda t: lab.spu_text(t, parts), seeds[:2], 6000, False):
        print('random lockstep:', 'PASS' if passed else 'FAIL', *tail, flush=True)
        if not passed:
            failures.append('random lockstep')
    print('FAIL lab-spu: ' + ', '.join(failures) if failures else f'PASS lab-spu {",".join(parts)}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
